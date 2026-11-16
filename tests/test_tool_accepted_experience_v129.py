"""Issued graph tool acceptance -> explicit human method draft/review.

Root alone runs the actual PostgreSQL/API/Worker fixtures.  No release, tool
result, IAM guard, Invocation, or success receipt is mocked.  Deliberate record
corruption is used only on rejecting paths and restored byte-for-byte.  Existing
real fixtures issue one graph workflow tool; mixed/native-run inventory cases
remain the independent pure contract tests, never invented runtime successes.
"""
import copy
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, Capability, CloudCall, Entity, Invocation, Job, Memory, Task, Workspace
from generic_agents.native_cases import TYPE
from generic_agents.services import as_dict
from tests.test_workflow_graph_goal_journey_v126 import (
    run_goal_journey, waiting_release, graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene,
)


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _calls():
    with Session(engine()) as db:
        return {model.__tablename__: {row.id: digest(as_dict(row)) for row in db.scalars(select(model))}
            for model in (Job, Invocation, CloudCall)}


def _original(proof):
    with Session(engine()) as db:
        task = db.get(Task, proof['task_id'])
        return {'acceptance': copy.deepcopy(task.data['native_goal_acceptance']),
            'acceptance_hash': task.data['native_goal_acceptance_hash'],
            'graph_runtime': copy.deepcopy(task.data['graph_runtime']),
            'workflow_runtime': copy.deepcopy(task.data['workflow_runtime']),
            'goal_outcome': copy.deepcopy(as_dict(db.get(Memory, proof['goal_data_receipt_id']))),
            'graph_outcome': copy.deepcopy(as_dict(db.get(Memory, proof['graph_data_receipt_id']))),
            'entities': {row.id: digest(as_dict(row)) for row in db.scalars(select(Entity))}}


def _command(view, reason, **extra):
    return {'request_id': str(uuid4()), 'expected_version': view['version'], 'reason': reason, **extra}


def _draft_command(view):
    field = view['field_options'][0]['key']
    return _command(view, '依据本任务已正式验收的原工具过程，明确另存待人工审核的方法',
        acceptance_hash=view['source']['acceptance_hash'], title='核对原资料与本次工具过程的对应关系',
        summary='核对本次资料、注册能力及人工验收的对应关系，不复制旧任务数值',
        limits='仅作为后续原资料核查方法；不证明现场状态，也不保证后续模型或业务效果',
        steps=[{'id': 'check_original', 'kind': 'source_identity', 'field': field,
            'instruction': '逐项对照本次原资料和当前任务对象；缺失或不明确时保留未知'}])


def _case_command(view, reason, **extra):
    case = view['draft']
    return _command(view, reason, case_id=case['id'], expected_case_version=case['version'],
        content_hash=case['content_hash'], **extra)


def _source_ready(client, path, proof):
    view = _ok(client.get(path))
    assert view['schema'] == 'native-method-experience-v1' and view['task_id'] == proof['task_id']
    assert view['source']['accepted'] and not view['source']['scope_error']
    assert view['source']['acceptance_hash'] == digest(proof['acceptance']['response']['acceptance'])
    return view


def run_experience_for_accepted(client, proof):
    """JSON-safe issued proof -> human draft/submit/review and cold readbacks.

    No goal/tool/model execution occurs here.  Root can call this after the
    original issued journey and reuse its exact commands in a restart check.
    Original-nonce recovery is read-only and may report the latest view; no old
    mutation response is misrepresented as a fixed current projection.
    """
    path = proof['task_path'] + '/experience'
    original, calls = _original(proof), _calls()
    view = _source_ready(client, path, proof)
    assert view['draft'] is None and view['permissions']['draft']['allowed']
    with Session(engine()) as db:
        assert not list(db.scalars(select(Memory).where(Memory.data['type'].as_string() == TYPE,
            Memory.data['source_task_id'].as_string() == proof['task_id'])))
    commands = []
    draft_body = _draft_command(view)
    draft = _ok(client.post(path + '/draft', json=draft_body))
    commands.append({'operation': 'draft', 'path': path + '/draft', 'body': draft_body, 'response': draft})
    assert draft['draft']['status'] == 'draft' and draft['permissions']['submit']['allowed']
    assert _calls() == calls and _original(proof) == original
    submit_body = _case_command(draft, '原经验草稿与适用限制已人工核对，明确提交审核')
    submitted = _ok(client.post(path + '/submit', json=submit_body))
    commands.append({'operation': 'submit', 'path': path + '/submit', 'body': submit_body, 'response': submitted})
    assert submitted['draft']['status'] == 'candidate' and submitted['permissions']['review']['allowed']
    assert _calls() == calls and _original(proof) == original
    review_body = _case_command(submitted, '人工审核原工具过程、原验收和方法限制后确认此核查方法；不晋升现场事实',
        decision='trust', valid_until=None)
    trusted = _ok(client.post(path + '/review', json=review_body))
    commands.append({'operation': 'review', 'path': path + '/review', 'body': review_body, 'response': trusted})
    assert trusted['draft']['status'] == 'trusted'
    assert _calls() == calls and _original(proof) == original
    me = _ok(client.get('/api/me'))
    with Session(engine()) as db:
        method = db.get(Memory, trusted['draft']['id'])
        assert method.status == 'trusted' and method.data['type'] == TYPE and method.task_id == proof['task_id']
        assert method.data['source_proof']['acceptance_hash'] == original['acceptance_hash']
        assert method.data['source_proof']['task_id'] == proof['task_id']
        assert method.data['source_proof']['outcome_id'] == proof['goal_data_receipt_id']
        assert method.data['source_proof']['workflow_hash'] == digest(original['workflow_runtime']['receipts'])
        assert method.data['source_proof']['goal_snapshot'] == original['acceptance']['goal_snapshot']
        assert method.data['review']['actor_id'] == me['user_id'] and method.data['review']['decision'] == 'trust'
        assert [item['operation'] for item in method.data['history']] == ['submit', 'review']
        for operation in ('draft', 'submit', 'review'):
            audits = list(db.scalars(select(Audit).where(Audit.object_id == proof['task_id'],
                Audit.action == 'native_experience.' + operation, Audit.actor_id == me['user_id'])))
            assert len(audits) == 1
        memory = copy.deepcopy(as_dict(method))
        assert db.get(Memory, proof['goal_data_receipt_id']).status == 'candidate'
        assert db.get(Memory, proof['graph_data_receipt_id']).status == 'candidate'
    readbacks = []
    for current_path in (proof['task_path'], proof['goal_path'], path):
        readbacks.append({'path': current_path, 'response': _ok(client.get(current_path))})
    assert readbacks[1]['response']['acceptance'] == original['acceptance']
    assert readbacks[2]['response']['draft'] == trusted['draft']
    recovered = []
    for command in commands:
        request = {'operation': command['operation'], 'original_request': copy.deepcopy(command['body']),
            'expected_version': trusted['version'], 'reason': '只核对这条原经验操作已保存，不执行工具或再次审核', 'action': 'probe'}
        response = _ok(client.post(path + '/requests/recover', json=request))
        assert response['saved'] and not response['abandoned']
        recovered.append({'path': path + '/requests/recover', 'method': 'POST', 'body': request, 'response': response})
    assert _calls() == calls and _original(proof) == original
    return {'schema': 'tool-accepted-experience-journey-proof-v129', 'task_id': proof['task_id'],
        'experience_path': path, 'method_id': memory['id'], 'commands': commands, 'readbacks': readbacks,
        'readonly_requests': recovered, 'memory': memory, 'original_acceptance': original['acceptance'],
        'original_acceptance_hash': original['acceptance_hash'], 'original_goal_outcome': original['goal_outcome'],
        'original_graph_outcome': original['graph_outcome'], 'calls': calls,
        'new_jobs': 0, 'new_invocations': 0, 'new_cloud_calls': 0, 'automatic_publication': False,
        'physical_state_verified': False}


@contextmanager
def _records(changes):
    """Owned synthetic corruption only; no successful proof is manufactured."""
    saved = []
    with Session(engine()) as db:
        for model, identity, values in changes:
            row = db.get(model, identity)
            saved.append((model, identity, {key: copy.deepcopy(getattr(row, key)) for key in values}))
            for key, value in values.items(): setattr(row, key, copy.deepcopy(value))
        db.commit()
    try: yield
    finally:
        with Session(engine()) as db:
            for model, identity, values in saved:
                row = db.get(model, identity)
                for key, value in values.items(): setattr(row, key, value)
            db.commit()


def _rejected_source(client, path, body):
    calls = _calls()
    with Session(engine()) as db:
        task_id = path.split('/')[-2]
        task_before = digest(as_dict(db.get(Task, task_id)))
        memories = {row.id: digest(as_dict(row)) for row in db.scalars(select(Memory))}
    response = client.get(path)
    assert response.status_code in {200, 409}, response.text
    if response.status_code == 200:
        view = response.json()
        assert view['source']['scope_error'] and not view['permissions']['draft']['allowed']
    denied = client.post(path + '/draft', json={**body, 'request_id': str(uuid4())})
    assert denied.status_code == 409, denied.text
    assert _calls() == calls
    with Session(engine()) as db:
        assert digest(as_dict(db.get(Task, task_id))) == task_before
        assert {row.id: digest(as_dict(row)) for row in db.scalars(select(Memory))} == memories


def test_actual_graph_tool_accepted_experience_requires_explicit_draft_submission_and_human_review(waiting_release, tmp_path):
    proof = run_goal_journey(waiting_release, tmp_path)
    path = proof['task_path'] + '/experience'
    calls = _calls()
    preview = _ok(waiting_release['client'].post(path + '/suggestions/local/preview', json={}))
    assert preview['source_kind'] == 'local_rules' and preview['source_error'] is None and _calls() == calls
    result = run_experience_for_accepted(waiting_release['client'], proof)
    assert result['memory']['status'] == 'trusted' and result['new_invocations'] == result['new_cloud_calls'] == result['new_jobs'] == 0


@pytest.mark.parametrize('damage', ['execution_basis', 'merged_tool_output', 'evaluation_basis'])
def test_resealed_acceptance_and_outcome_cannot_replace_the_original_frozen_tool_basis(waiting_release, tmp_path, damage):
    proof = run_goal_journey(waiting_release, tmp_path); client = waiting_release['client']
    path = proof['task_path'] + '/experience'; view = _source_ready(client, path, proof); body = _draft_command(view)
    with Session(engine()) as db:
        data = copy.deepcopy(db.get(Task, proof['task_id']).data)
        outcome = copy.deepcopy(db.get(Memory, proof['goal_data_receipt_id']).data)
    accepted = copy.deepcopy(data['native_goal_acceptance'])
    if damage == 'execution_basis': accepted['evaluation']['tool_execution']['basis_hash'] = '0' * 64
    elif damage == 'evaluation_basis': accepted['evaluation']['basis_hash'] = '0' * 64
    else:
        normalized = accepted['evaluation']['tool_results']
        assert len(normalized) == 1 and normalized[0]['method'] == 'registered_capability'
        normalized[0]['output']['value'] += 1
    from generic_agents.accepted_tool_basis_v129 import accepted_tool_basis
    from generic_agents.native_goals import basis
    with Session(engine()) as db:
        task = db.get(Task, proof['task_id'])
        try: reconstructed = accepted_tool_basis(task.data['goal_spec'], accepted['evaluation'])
        except ValueError: pass  # A malformed frozen family is itself a closed rejection.
        else:
            if damage == 'evaluation_basis':
                assert basis(task, accepted['evidence'], reconstructed) == accepted['basis_hash']
                assert accepted['evaluation']['basis_hash'] != accepted['basis_hash']
            else: assert basis(task, accepted['evidence'], reconstructed) != accepted['basis_hash']
    data.update(native_goal_acceptance=accepted, native_goal_acceptance_hash=digest(accepted))
    outcome.update(acceptance=copy.deepcopy(accepted), acceptance_hash=digest(accepted))
    assert data['native_goal_acceptance_hash'] == outcome['acceptance_hash'] == digest(outcome['acceptance'])
    with _records([(Task, proof['task_id'], {'data': data}), (Memory, proof['goal_data_receipt_id'], {'data': outcome})]):
        _rejected_source(client, path, {**body, 'acceptance_hash': digest(accepted)})
    assert not _source_ready(client, path, proof)['source']['scope_error']


def test_damaged_actual_graph_invocation_job_or_step_history_cannot_become_a_trusted_method(waiting_release, tmp_path):
    proof = run_goal_journey(waiting_release, tmp_path); client = waiting_release['client']
    path = proof['task_path'] + '/experience'; view = _source_ready(client, path, proof); body = _draft_command(view)
    with Session(engine()) as db:
        # An inert real workspace supplies a valid FK for the rejecting scope
        # case. It has no users, memberships, capabilities or execution grants.
        foreign = Workspace(name='原调用工作区关联负控'); db.add(foreign); db.commit(); foreign_id = foreign.id
        task = copy.deepcopy(db.get(Task, proof['task_id']).data)
        call = copy.deepcopy(db.get(Invocation, proof['invocation']['id']).data)
        job = copy.deepcopy(db.get(Job, proof['ability_job']['id']).data)
    changed_output = copy.deepcopy(call['output']); changed_output['value'] += 1
    receipt_data = copy.deepcopy(task)
    ability = next(item for item in receipt_data['workflow_runtime']['receipts'] if item.get('execution_mode') == 'registered_graph_ability')
    ability['output']['value'] += 1
    graph_data = copy.deepcopy(task)
    graph_data['graph_runtime']['activations'][proof['raw_graph_receipt']['activation_id']]['output']['value'] += 1
    cases = [
        (Invocation, proof['invocation']['id'], {'status': 'failed'}),
        (Invocation, proof['invocation']['id'], {'data': {**call, 'input_hash': '0' * 64}}),
        (Invocation, proof['invocation']['id'], {'data': {**call, 'output': changed_output}}),
        (Invocation, proof['invocation']['id'], {'data': {**call, 'lease_token': str(uuid4())}}),
        (Invocation, proof['invocation']['id'], {'data': {**call, 'job_id': str(uuid4())}}),
        (Invocation, proof['invocation']['id'], {'workspace_id': foreign_id}),
        (Job, proof['ability_job']['id'], {'status': 'failed'}),
        (Job, proof['ability_job']['id'], {'data': {**job, 'input_hash': '0' * 64}}),
        (Job, proof['ability_job']['id'], {'data': {**job, 'task_id': str(uuid4())}}),
        (Task, proof['task_id'], {'data': receipt_data}),
        (Task, proof['task_id'], {'data': graph_data}),
    ]
    for model, identity, changes in cases:
        with _records([(model, identity, changes)]): _rejected_source(client, path, body)
    candidate = _ok(client.post(path + '/draft', json=body))
    submitted = _ok(client.post(path + '/submit', json=_case_command(candidate, '原工具依据完好，人工提交方法审核')))
    review = _case_command(submitted, '不得把真实调用已损坏的方法审核为可信', decision='trust', valid_until=None)
    with _records([(Invocation, proof['invocation']['id'], {'data': {**call, 'output': changed_output}})]):
        before = _calls()
        assert client.post(path + '/review', json=review).status_code == 409
        assert _calls() == before
        with Session(engine()) as db: assert db.get(Memory, submitted['draft']['id']).status == 'candidate'
    trusted = _ok(client.post(path + '/review', json=review))
    assert trusted['draft']['status'] == 'trusted'


def test_current_object_version_tool_publication_and_source_stop_do_not_reexecute_or_resign_accepted_history(waiting_release, tmp_path):
    proof = run_goal_journey(waiting_release, tmp_path); client = waiting_release['client']
    with Session(engine()) as db:
        entity = db.get(Entity, proof['goal_spec']['entity_ids'][0])
        identifier, version, attributes = entity.id, entity.version, copy.deepcopy(entity.data)
    _ok(client.patch('/api/entities/' + identifier, json={'expected_version': version, 'attributes': attributes,
        'reason': '仅核对当前对象形成新的台账版本；原验收及工具输入仍固定在原版本'}))
    producer = waiting_release['producer']
    _ok(client.post('/api/setup/graph-event-producers/revocations', json={
        'schema': 'graph-event-producer-revocation-v2', 'request_id': str(uuid4()), 'producer_id': producer['id'],
        'producer_version': producer['version'], 'contract_hash': producer['contract_hash'],
        'reason': '停止后续结果供应；仅核对已完成任务的原验收和原调用历史'}))
    # This fixture status change exercises history after withdrawal; there is
    # no general public Capability-revoke API and no republish is fabricated.
    capability_id = proof['raw_graph_receipt']['capability_id']
    with _records([(Capability, capability_id, {'status': 'revoked'})]):
        result = run_experience_for_accepted(client, proof)
        assert result['original_acceptance'] == proof['acceptance']['response']['acceptance']
        assert result['memory']['status'] == 'trusted' and result['new_invocations'] == 0
