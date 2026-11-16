"""Normal approved case -> real upload/native parser/ACK -> original175 flow.

Root executes on disposable ga_test only. Test helpers use normal source/task/
case/approval APIs and real worker, never fabricate successful business rows.
"""
import copy
import hashlib
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m, worker, workflow_graph
from generic_agents.db import engine
from generic_agents import layered_case_uses_v210 as uses, layered_cases_v209 as cases
from generic_agents import runtime_document_receipts as delivery
from tests.test_business_data_gateway_pg import scene
from tests.test_layered_cases_pg_v209 import accepted_case_source, create_case, change
from tests.test_authored_workflow_pg_v208 import good
from tests.test_authored_workflow_document_packet_dispatch_pg_v208 import command, task_jobs
from tests.test_runtime_documents_pg_v205 import run

BASE = uses.PREFIX


@pytest.fixture
def case_use_target(accepted_case_source):
    source = accepted_case_source
    _, _, result = create_case(source)
    draft = good(change(source.author_client, result['case'], 'submit'))['case']
    approved = good(change(source.reviewer_client, draft, 'review', decision='approve'))['case']
    # Target is a fresh normal Task. A generic native original may be reused
    # inside its instance; an existing CaseUse's own target may never be reused.
    with Session(engine()) as db:
        entity_ids = copy.deepcopy(db.get(m.Task, source.task_id).data['entity_ids'])
    target = good(source.author_client.post('/api/workspace/instances/' + source.release['instance_id'] + '/tasks', json={
        'request_id': str(uuid4()), 'release_id': source.release['id'],
        'business_id': source.release['available_business_ids'][0], 'entity_ids': entity_ids,
        'title': '新目标任务明确参考已批准案例', 'graph_inputs': {}}), 201)
    target_id = target['id']
    context = good(source.author_client.get(BASE + '/tasks/' + target_id + '/context'))
    assert context['can_create']
    yield SimpleNamespace(**vars(source), source_case=approved, target_task_id=target_id,
        target_approval_path='/api/workspace/tasks/' + target_id + '/approval-v175',
        target_context=context)


def use_selection(f):
    context = good(f.author_client.get(BASE + '/tasks/' + f.target_task_id + '/context'))
    case = f.source_case
    return {'expected_task_version': context['task_version'], 'expected_approval_version': context['approval_version'],
        'case_id': case['id'], 'expected_case_version': case['version'], 'revision_id': case['revision']['id'],
        'revision_hash': case['revision']['revision_hash'], 'source_fingerprint': case['source']['source_fingerprint'],
        'applicable_reason': '仅参考原工程资料核对方式，保留当前原始资料和独立人工确认'}


def create_use(f):
    selection = use_selection(f)
    preview = good(f.author_client.post(BASE + '/tasks/' + f.target_task_id + '/preview', json=selection))
    assert preview['can_create'] and preview['capacity']['selector_count'] > 1
    body = {**selection, 'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'boundary_confirmed': True}
    response = f.author_client.post(BASE + '/tasks/' + f.target_task_id + '/uses', json=body)
    return body, response, good(response, 201)


def ready_use(f, result):
    view = result['use'] if 'use' in result else result
    assert run(f.s, view['document']['version_id']) == 'succeeded'
    delivery.consume_once(f.s.workspace)
    ready = good(f.author_client.get(BASE + '/uses/' + view['id']))
    assert ready['stage']['code'] == 'ready' and ready['document']['ack_verified']
    assert 1 < len(ready['selectors']) <= 20 and ready['permissions']['stage_sources']
    return ready


def stage_reference(f, use):
    # Preserve ordinary native evidence. This is not an automatic Task edit.
    ordinary = good(f.author_client.get('/api/runtime-task-documents/source-sets/' + f.source_set_id))
    selectors = [copy.deepcopy(ordinary['entries'][0]['selector']), *copy.deepcopy(use['selectors'])]
    context = good(f.author_client.get('/api/runtime-task-documents/tasks/' + f.target_task_id + '/context'))
    port = context['input_ports'][0]
    previous = context['source_set_heads'].get(port['key'])
    return good(f.author_client.post('/api/runtime-task-documents/tasks/' + f.target_task_id + '/source-sets', json={
        'request_id': str(uuid4()), 'expected_task_version': context['task_version'],
        'expected_approval_version': context['approval_version'],
        'prior_set_id': previous['source_set_id'] if previous else None,
        'prior_set_hash': previous['source_set_hash'] if previous else None,
        'input_key': port['key'], 'selectors': selectors}), 201)


def consume_use(f, use, *, source_set=None):
    source = source_set or stage_reference(f, use)
    approval = good(f.author_client.get(f.target_approval_path)); value = copy.deepcopy(approval['value'])
    value['graph_inputs'][source['input_key']] = source['ref']
    command(f.author_client, f.target_approval_path, 'revise', value=value)
    command(f.author_client, f.target_approval_path, 'submit')
    submission = good(f.author_client.get(f.target_approval_path))['submission_hash']
    for reviewer in (f.first_client, f.second_client):
        command(reviewer, f.target_approval_path, 'decide', submission_hash=submission, decision='approve')
    command(f.author_client, f.target_approval_path, 'start')
    historical = good(f.author_client.get(cases.PREFIX + '/cases/' + f.source_case['id']))
    assert historical['status'] == 'approved' and historical['version'] == f.source_case['version']
    assert historical['revision']['revision_hash'] == f.source_case['revision']['revision_hash']
    assert historical['revision']['value'] == f.source_case['revision']['value']
    assert historical['source']['source_fingerprint'] == f.source_case['source']['source_fingerprint']
    jobs = task_jobs(f.s, f.target_task_id); assert len(jobs) == 1
    assert worker.process_once(jobs[0][0], kinds=[workflow_graph.KIND], deliver=False, workspace_id=f.s.workspace)
    path = '/api/workspace/tasks/' + f.target_task_id
    for _ in range(16):
        task = good(f.author_client.get(path))
        if not task['graph_pending_humans']: break
        human = task['graph_pending_humans'][0]
        good(f.author_client.post(path + '/graph/complete', json={'request_id': str(uuid4()),
            'activation_id': human['activation_id'], 'version': human['version'],
            'cancel_epoch': task['graph_runtime']['cancel_epoch'],
            'output': {'result': '对照普通原文和完整历史参考完成当前工程核对；不证明现场对象状态'}}))
    basis_view = good(f.reviewer_client.get(f.target_approval_path))
    assert basis_view['permissions']['accept'] and basis_view['acceptance_basis']
    command(f.reviewer_client, f.target_approval_path, 'accept', decision='accept', basis_hash=basis_view['acceptance_basis']['basis_hash'])
    consumed = good(f.reviewer_client.get(BASE + '/uses/' + use['id'] + '/consumption'))
    assert consumed['consumed'] and consumed['accepted'] and consumed['human_results']
    assert not consumed['provided_to_model'] and not consumed['model_analysis_verified']
    return consumed


def source_inventory(f):
    with Session(engine()) as db:
        task = db.get(m.Task, f.task_id); case = db.get(cases.Case, f.source_case['id'])
        memories = [(row.id, row.version, row.status, copy.deepcopy(row.data)) for row in db.scalars(select(m.Memory).where(
            m.Memory.workspace_id == f.s.workspace, m.Memory.task_id == f.task_id).order_by(m.Memory.id))]
        calls = [(row.id, row.status, copy.deepcopy(row.data)) for row in db.scalars(select(m.CloudCall).where(
            m.CloudCall.workspace_id == f.s.workspace).order_by(m.CloudCall.id))]
        return copy.deepcopy((task.version, task.data, case.version, case.data, memories, calls))


def test_real_reference_upload_full_parse_ack_approval_consumption_and_independent_feedback(case_use_target):
    f = case_use_target; original = source_inventory(f)
    with Session(engine()) as db:
        target_before = copy.deepcopy((db.get(m.Task, f.target_task_id).version, db.get(m.Task, f.target_task_id).data))
    body, response, result = create_use(f)
    saved_result = copy.deepcopy(result); identity = result['use_id']
    assert result['use']['stage']['code'] == 'queued'
    raw = f.author_client.get(result['use']['document']['original_url']).content
    for item in f.source_case['revision']['value'].values(): assert item.encode() in raw
    with Session(engine()) as db:
        assert (db.get(m.Task, f.target_task_id).version, db.get(m.Task, f.target_task_id).data) == target_before
    use = ready_use(f, result)
    assert not good(f.author_client.get(BASE + '/uses/' + identity + '/consumption'))['consumed']
    source_set = stage_reference(f, use)
    assert source_set['entries'][0]['selector']['version_id'] == f.native_id
    assert len(source_set['entries']) == len(use['selectors']) + 1
    assert not good(f.author_client.get(BASE + '/uses/' + identity + '/consumption'))['consumed']
    consumed = consume_use(f, use, source_set=source_set)
    feedback_body = {'request_id': str(uuid4()), 'expected_use_version': use['version'],
        'consumption_fingerprint': consumed['consumption_fingerprint'],
        'acceptance_request_id': consumed['acceptance']['request_id'], 'basis_hash': consumed['acceptance']['basis_hash'],
        'evaluation': 'uncertain', 'reason': '保留了参考来源，但单个工程任务不足以证明效果提升',
        'human_activation_ids': [consumed['human_results'][0]['activation_id']]}
    assert f.author_client.post(BASE + '/uses/' + identity + '/feedback', json=feedback_body).status_code == 403
    feedback = good(f.reviewer_client.post(BASE + '/uses/' + identity + '/feedback', json=feedback_body))
    assert feedback['use_version'] == use['version'] + 1 and feedback['use']['feedback']['evaluation'] == 'uncertain'
    assert not good(f.reviewer_client.get(BASE + '/uses/' + identity + '/consumption'))['permissions']['feedback']
    duplicate = {**feedback_body, 'request_id': str(uuid4()), 'expected_use_version': feedback['use_version']}
    assert f.reviewer_client.post(BASE + '/uses/' + identity + '/feedback', json=duplicate).status_code == 403
    recovered = good(f.author_client.get(BASE + '/requests/' + body['request_id'], params={
        'expected_sha256': hashlib.sha256(response.request.content).hexdigest()}))
    assert recovered['result'] == saved_result
    assert good(f.author_client.post(BASE + '/tasks/' + f.target_task_id + '/uses',
        content=response.request.content, headers={'Content-Type': 'application/json'}), 201) == saved_result
    assert source_inventory(f) == original


def test_reference_can_withdraw_without_deleting_native_originals_or_rewriting_target(case_use_target):
    f = case_use_target; _, _, result = create_use(f); use = ready_use(f, result)
    original = source_inventory(f)
    with Session(engine()) as db: before = copy.deepcopy((db.get(m.Task, f.target_task_id).version, db.get(m.Task, f.target_task_id).data))
    raw = f.author_client.get(use['document']['original_url']).content
    withdrawn = good(f.author_client.post(BASE + '/uses/' + use['id'] + '/withdraw', json={
        'request_id': str(uuid4()), 'expected_use_version': use['version'], 'reason': '先保留原件，暂不用于当前新审批'}))
    assert withdrawn['status'] == 'withdrawn' and withdrawn['use']['selectors'] == []
    assert f.author_client.get(use['document']['original_url']).content == raw
    with Session(engine()) as db:
        assert (db.get(m.Task, f.target_task_id).version, db.get(m.Task, f.target_task_id).data) == before
    assert source_inventory(f) == original


def test_context_keeps_history_after_pause_but_rejects_damaged_original_target(case_use_target):
    f = case_use_target; _, _, created = create_use(f)
    current = next(row for row in good(f.author_client.get('/api/instances')) if row['id'] == f.release['instance_id'])
    good(f.author_client.post('/api/instances/' + current['id'] + '/pause', json={'version': current['version']}))
    context = good(f.author_client.get(BASE + '/tasks/' + f.target_task_id + '/context'))
    assert context['can_create'] is False and context['reason']
    assert [row['id'] for row in context['uses']] == [created['use_id']]
    view = good(f.author_client.get(BASE + '/uses/' + created['use_id']))
    assert view['permissions']['withdraw'] and not view['permissions']['stage_sources']
    # Only this negative target is damaged. It must not become a harmless
    # eligibility reason or switch to a newer task/release/source.
    with Session(engine()) as db:
        task = db.get(m.Task, f.target_task_id)
        original_data = copy.deepcopy(task.data)
        task.data = {**task.data, 'creation_hash': 'f' * 64}; db.commit()
    assert f.author_client.get(BASE + '/tasks/' + f.target_task_id + '/context').status_code == 409
    # Restore those exact original bytes for a distinct integrity fault below:
    # the Managed authority now remains valid, but target_pin's immutable
    # saved_execution release check must still fail rather than becoming a
    # harmless "can_create=false" current-eligibility explanation.
    with Session(engine()) as db:
        db.get(m.Task, f.target_task_id).data = original_data
        db.get(m.InstanceRelease, f.release['id']).configuration_hash = 'f' * 64
        db.commit()
    assert f.author_client.get(BASE + '/tasks/' + f.target_task_id + '/context').status_code == 409


def test_start_scope_is_row_bound_and_cannot_authorize_a_draft_transition(case_use_target):
    from fastapi import HTTPException
    from generic_agents import layered_approval_store_v175 as approval
    from generic_agents import layered_case_use_origin_v210 as origin
    from generic_agents.contracts import digest
    f = case_use_target; _, _, created = create_use(f)
    with Session(engine()) as db:
        target = db.get(m.Task, f.target_task_id)
        managed = approval.managed(db, f.s.p, target)
        row = db.get(uses.Use, created['use_id'])
        # Exercise the original scope helper while the target is deliberately
        # still draft: the source is ordinary immutable accepted history, while
        # this target is not a legitimate approved->executing transition.
        with approval.initializing(db, managed, digest(approval.authority(managed))):
            assert origin.checked_use(db, f.s.p, row)['use_id'] == row.id
            with pytest.raises(HTTPException) as caught:
                approval.managed(db, f.s.p, target)
            assert caught.value.status_code == 409
        assert 'layered_approval_initializing_v175' not in db.info
