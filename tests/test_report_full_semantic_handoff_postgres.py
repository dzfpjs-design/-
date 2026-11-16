"""Root-run synthetic PG: lossless shared design, never source/execution approval.

The provider transport alone is synthetic. Manual saves, shared ledger, config
CAS, compilation and cold authenticated reads use normal product APIs. A fresh
Session/TestClient is not a physical restart or real-report quality proof.
"""
import copy
import json
from uuid import UUID, uuid4, uuid5

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, SetupDraft, SetupRevision, Task
from generic_agents.report_configuration_models import ReportConfigurationRecord
from generic_agents.report_evidence_manual_v6_adapter import manual_from_projection_v6
from generic_agents import report_evidence_pipeline as pipeline
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows, fees
from tests.test_report_evidence_pipeline_v5 import call_ids
from tests.test_report_evidence_pipeline_v7_postgres import (
    BASE, NativeHTTP, v7_scene, mapped, reduced, storage_files, call_job_rows,
)


PRIVATE_NOTE = 'PRIVATE_MANUAL_NOTE_MUST_NEVER_BE_SHARED_末字🙂\n'
DESIGN = 'report-full-semantic-design-v1'


def human_value(result):
    """Explicit HUMAN additions, not invented provider/source evidence."""
    value = manual_from_projection_v6(result, note=PRIVATE_NOTE)
    why = '人工新增完整设计，来源和执行规则均仍待核对；不代表原文明示或权限。'
    unknown = {'status': 'unknown', 'citations': [], 'explanation': why}

    def step(name, target):
        return {'name': name, 'purpose': ' 人工填写的待核目的 ', 'agent': '人工确认',
            'role': '人工填写的职责称谓', 'output': ' 人工填写的待核产出🙂 ',
            'next_index': target, 'else_index': None, 'branch_condition': '',
            'citations': [], 'inference_reason': why}

    value['result']['businesses'].append({'operation': 'upsert', 'original_name': '',
        'name': '人工新增有环待核业务', 'trigger': '人工填写的启动条件', 'output': '人工填写的最终产出',
        'steps': [step('人工节点甲', 1), step('人工节点乙', 0), step('人工节点丙', None)],
        'citations': [], 'inference_reason': why,
        'participants': [{'agent': name, 'responsibility': ' 人工填写的待核职责 ',
            'evidence': copy.deepcopy(unknown)} for name in ('人工参与甲', '人工参与乙')],
        'interactions': [{'from_participant_index': 1, 'to_participant_index': 0,
            'purpose': '人工明确填写的待核交接', 'condition': '', 'step_indices': [1, 0],
            'evidence': copy.deepcopy(unknown)}],
        'conditions': [{'kind': 'stop', 'statement': '人工填写的停止条件', 'step_indices': [2],
            'evidence': copy.deepcopy(unknown)}],
        'coordination': [{'kind': 'parallel', 'step_indices': [1, 0], 'start_rule': '人工填写的开始规则',
            'wait_rule': '人工填写的等待规则', 'join_rule': '人工填写的汇合规则',
            'evidence': copy.deepcopy(unknown)}]})
    value['result']['businesses'].append({'operation': 'upsert', 'original_name': '',
        'name': '人工新增不完整待核业务', 'trigger': '', 'output': '', 'steps': [],
        'citations': [], 'inference_reason': why, 'participants': [], 'interactions': [],
        'conditions': [], 'coordination': []})
    value['result']['objects'].append({'name': '人工待核资料对象', 'fields': [{
        'label': '人工填写的识别名称', 'kind': '文本', 'required': True, 'unit': None,
        'citations': [], 'inference_reason': why}], 'citations': [], 'inference_reason': why})
    value['result']['global_claims'].append({'kind': '治理', 'statement': '人工新增仍待核对的独立约束',
        'span_refs': [], 'status': 'unknown', 'explanation': why})
    value['result']['questions'].append(why)
    return value


def test_saved_manual5_shared_whole_design_cas_cold_read_and_blocked_compilation(
        v7_scene, monkeypatch, account):
    fence()
    scene, http = v7_scene, NativeHTTP(monkeypatch)
    _, _, _, middle = mapped(scene, http)
    _, _, _, generated = reduced(scene, http, middle)
    client, identity = scene['client'], generated['id']
    value = human_value(generated['model_result'])
    manual_body = {'revision': generated['revision'], 'request_id': str(uuid4()), 'value': value}
    manual = reply(client.put(BASE + '/' + identity + '/review', json=manual_body))
    private = manual['draft']
    assert manual['current_matches'] is True and private['review_value'] == value
    assert private['model_result'] == generated['model_result']
    assert private['model_result_hash'] == generated['model_result_hash']

    # Use ordinary configuration save to ensure generation's profile precondition.
    config_view = reply(client.get('/api/setup/draft'))
    config = copy.deepcopy(config_view['configuration'])
    config.setdefault('instanceProfile', {}).update(name='合成完整设计实例', domain='合成协议核对')
    config_view = reply(client.put('/api/setup/draft', json={
        'revision': config_view['revision'], 'configuration': config}))
    paid = call_job_rows(), fees(call_ids(private)), storage_files(), len(http.requests)

    def unchanged_paid():
        assert call_job_rows() == paid[0]
        assert (fees(call_ids(private)), storage_files(), len(http.requests)) == paid[1:]

    def command(view=config_view, saved=manual):
        row = saved['draft']
        return {'schema': 'report-full-semantic-handoff-command-v1', 'request_id': str(uuid4()),
            'report_revision': row['revision'], 'review_receipt_hash': saved['receipt']['receipt_hash'],
            'manual_value_hash': digest(row['review_value']), 'model_result_hash': row['model_result_hash'],
            'source_hash': row['preview']['source_hash'],
            'catalog_hash': row['review_value']['result']['catalog_hash'],
            'context_hash': digest(row['preview']['map_preparation']['context']),
            'configuration_revision': view['revision'], 'configuration_hash': digest(view['configuration']),
            'share_scope': 'whole_result_excluding_private_note_v1'}

    body, rows_before = command(), all_rows()
    preview = reply(client.post(BASE + '/' + identity + '/full-configuration-preview', json=body))
    assert preview['schema'] == 'report-full-semantic-configuration-preview-v1'
    assert preview['body'] == body and preview['conflicts'] == []
    assert preview['source_status'] == 'unconfirmed'
    assert preview['can_confirm'] is preview['can_publish'] is False
    design, flows = preview['design'], preview['flows']
    assert design['schema'] == DESIGN and design['result'] == value['result']
    assert design['manual_value_hash'] == digest(value)
    assert PRIVATE_NOTE not in json.dumps(preview, ensure_ascii=False)
    assert all_rows() == rows_before and reply(client.get(BASE + '/' + identity)) == private

    all_ids = []
    for bi, (binding, business) in enumerate(zip(design['business_bindings'], value['result']['businesses'], strict=True)):
        assert binding['candidate_index'] == bi and binding['name'] == business['name']
        for kind, collection in [('step', 'steps'), ('participant', 'participants'),
                ('interaction', 'interactions'), ('condition', 'conditions'), ('coordination', 'coordination')]:
            expected_ids = [str(uuid5(UUID(design['handoff_id']), f'{DESIGN}/{kind}/{bi}/{index}'))
                for index in range(len(business[collection]))]
            assert binding[kind + '_ids'] == expected_ids
            all_ids.extend(expected_ids)
        flow, ids = flows[business['name']], binding['step_ids']
        assert flow['confirmedAt'] is flow['confirmedSignature'] is None
        for index, step in enumerate(business['steps']):
            projected = flow['steps'][index]
            assert projected['id'] == ids[index]
            assert all(projected[key] == step[key] for key in ('name', 'purpose', 'agent', 'role', 'output'))
            assert projected['condition'] == step['branch_condition']
            assert projected['nextId'] == ('end' if step['next_index'] is None else ids[step['next_index']])
            assert projected['elseId'] == ('end' if step['else_index'] is None else ids[step['else_index']])
    assert len(all_ids) == len(set(all_ids))
    assert flows['人工新增不完整待核业务']['steps'] == []

    submit = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    accepted = reply(client.post(BASE + '/' + identity + '/full-configuration-handoff', json=submit))
    assert accepted['schema'] == 'report-full-semantic-handoff-reply-v1' and accepted['current_matches'] is True
    record, receipt = accepted['record'], accepted['receipt']
    assert record['design'] == design and record['materials'] == preview['materials']
    assert record['id'] == design['handoff_id'] == receipt['handoff_id']
    assert record['record_hash'] == receipt['record_hash']
    assert receipt['accepted_revision'] == body['configuration_revision'] + 1
    assert accepted['configuration']['configuration']['fullSemanticDesigns'] == [design]
    assert all(accepted['configuration']['configuration']['flows'][name] == flow for name, flow in flows.items())
    assert PRIVATE_NOTE not in json.dumps(accepted, ensure_ascii=False)
    rows_after = all_rows()
    assert {table for table in rows_before if rows_before[table] != rows_after[table]} == {
        SetupDraft.__tablename__, SetupRevision.__tablename__, Audit.__tablename__, ReportConfigurationRecord.__tablename__}
    assert set(rows_after[ReportConfigurationRecord.__tablename__]) - set(rows_before[ReportConfigurationRecord.__tablename__]) == {record['id']}
    ledger = json.loads(rows_after[ReportConfigurationRecord.__tablename__][record['id']])
    assert ledger['data']['record'] == record and ledger['data']['receipt'] == receipt
    assert ledger['data']['command'] == submit and PRIVATE_NOTE not in json.dumps(ledger, ensure_ascii=False)
    revisions = [json.loads(row) for row in rows_after[SetupRevision.__tablename__].values()]
    assert any(row['version'] == receipt['accepted_revision'] and row['data'] == accepted['configuration']['configuration'] for row in revisions)
    assert reply(client.get(BASE + '/' + identity)) == private
    unchanged_paid()

    shared_path = '/api/setup/full-semantic-handoffs/' + record['id']
    shared = reply(client.get(shared_path))
    assert shared['schema'] == 'report-full-semantic-shared-view-v1' and shared['record'] == record
    assert shared['source_status'] == 'unconfirmed' and shared['execution_supported'] is False
    assert PRIVATE_NOTE not in json.dumps(shared, ensure_ascii=False)
    receipt_path = BASE + '/' + identity + '/full-configuration-receipts/' + body['request_id']
    assert reply(client.get(receipt_path)) == accepted

    # v1 must keep rejecting the new complete family instead of silently dropping fields.
    legacy = {'report_revision': private['revision'], 'review_receipt_hash': manual['receipt']['receipt_hash'],
        'candidate_hash': digest(value['result']), 'source_hash': private['preview']['source_hash'],
        'validation_report_hash': digest(None), 'configuration_revision': accepted['configuration']['revision'],
        'configuration_hash': digest(accepted['configuration']['configuration']), 'business_indexes': [0]}
    snapshot = all_rows()
    rejected = client.post('/api/setup/report-drafts/' + identity + '/configuration-preview', json=legacy)
    assert rejected.status_code == 409 and rejected.json()['detail']['code'] == 'full_semantics_mapping_required'
    assert all_rows() == snapshot

    for mode in ('strip-design', 'strip-marker', 'strip-both', 'reseal-design', 'forged-source',
            'delete-all-flows', 'delete-all-flows-and-businesses', 'delete-business-list'):
        altered = copy.deepcopy(accepted['configuration']['configuration'])
        name = next(iter(flows))
        if mode in ('delete-all-flows', 'delete-all-flows-and-businesses'):
            altered['flows'] = {}
        if mode in ('delete-all-flows-and-businesses', 'delete-business-list'):
            altered['businesses'] = []
        if mode in ('strip-design', 'strip-both'):
            altered.pop('fullSemanticDesigns')
        if mode in ('strip-marker', 'strip-both'):
            for flow in altered['flows'].values():
                flow.pop('fullSemanticSource', None)
        elif mode == 'reseal-design':
            changed = altered['fullSemanticDesigns'][0]
            changed['result']['domain'] += '自重签不能授予共享来源'
            changed['design_hash'] = digest({key: item for key, item in changed.items() if key != 'design_hash'})
            for flow in altered['flows'].values():
                if flow.get('fullSemanticSource'):
                    flow['fullSemanticSource']['design_hash'] = changed['design_hash']
        elif mode == 'forged-source':
            altered['flows'][name]['fullSemanticSource']['handoff_id'] = str(uuid4())
        response = client.put('/api/setup/draft', json={
            'revision': accepted['configuration']['revision'], 'configuration': altered})
        assert response.status_code in (409, 422), (mode, response.text)
        assert all_rows() == snapshot and reply(client.get('/api/setup/draft')) == accepted['configuration']
        unchanged_paid()

    # Both current parent revisions advance legitimately; the original nonce is
    # still resolved before either current CAS and never replaces later input.
    later_value = copy.deepcopy(value); later_value['note'] += '后来私人说明，不共享'
    later = reply(client.put(BASE + '/' + identity + '/review', json={
        'revision': private['revision'], 'request_id': str(uuid4()), 'value': later_value}))
    cfg = copy.deepcopy(accepted['configuration']['configuration'])
    cfg['instanceProfile']['goal'] = cfg['instanceProfile'].get('goal', '') + ' 后来配置要求'
    current_config = reply(client.put('/api/setup/draft', json={
        'revision': accepted['configuration']['revision'], 'configuration': cfg}))
    snapshot = all_rows()
    replay = reply(client.post(BASE + '/' + identity + '/full-configuration-handoff', json=submit))
    assert replay['receipt'] == receipt and replay['record'] == record and replay['current_matches'] is False
    assert replay['configuration'] == current_config and reply(client.get(receipt_path)) == replay
    assert reply(client.get(BASE + '/' + identity)) == later['draft']
    for changed in ({**submit, 'configuration_hash': '0' * 64}, {**submit, 'request_id': str(uuid4())}):
        response = client.post(BASE + '/' + identity + '/full-configuration-handoff', json=changed)
        assert response.status_code == 409
        expected = 'full_semantic_nonce_conflict' if changed['request_id'] == submit['request_id'] else 'configuration_conflict'
        assert response.json()['detail']['code'] == expected
        assert all_rows() == snapshot
    unchanged_paid()

    # Compilation preserves the whole design while the current runtime refuses
    # to execute it. Neither an empty object list nor a shared design is a grant.
    compilation = {'revision': current_config['revision'], 'objects': [], 'role_bindings': {}}
    checked = reply(client.post('/api/instances/check', json=compilation))
    plan = checked['semantic_plan']
    assert set(plan) == {'schema', 'designs', 'business_bindings', 'execution_policy', 'plan_hash'}
    assert plan['schema'] == 'instance-full-semantic-plan-v1' and plan['designs'] == [design]
    assert plan['execution_policy'] == 'blocked_pending_full_semantic_runtime_v1'
    assert plan['plan_hash'] == digest({key: item for key, item in plan.items() if key != 'plan_hash'})
    planned = [business for business in checked['businesses'] if business['name'] in flows]
    assert len(planned) == len(flows) == len(plan['business_bindings'])
    for business in planned:
        assert business['available'] is False and business['execution_mode'] == 'blocked'
        assert any('完整语义设计' in issue for issue in business['issues'])
        assert business['flow'] == current_config['configuration']['flows'][business['name']]
    assert any('完整业务设计' in issue for issue in checked['general_issues'])
    assert PRIVATE_NOTE not in json.dumps(checked, ensure_ascii=False)
    assert all_rows() == snapshot
    release = reply(client.post('/api/instances', json={**compilation,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True,
        'generation_intent': 'new_instance', 'request_id': str(uuid4())}), 201)
    assert release['status'] == 'draft' and release['package_id'] is None
    assert release['available_business_ids'] == []
    assert release['snapshot'] == {key: item for key, item in checked.items() if key != 'configuration_hash'}
    assert release['full_semantic_plan_schema'] == plan['schema']
    assert release['full_semantic_plan_hash'] == plan['plan_hash']
    assert PRIVATE_NOTE not in json.dumps(release, ensure_ascii=False)
    assert all_rows()[Task.__tablename__] == snapshot[Task.__tablename__]
    activation_before = all_rows()
    response = client.post('/api/instances/' + release['instance_id'] + '/activate', json={
        'version': 1, 'release_id': release['id'], 'business_ids': [planned[0]['id']],
        'confirm_human_workflow_scope': True, 'confirm_workflow_scope': True})
    assert response.status_code == 409
    assert all_rows() == activation_before

    # Login is a real auth write (its limiter may advance). Once authenticated,
    # cold draft/shared/receipt GETs must be strictly read-only from that point.
    unchanged_paid()
    with Session(engine()) as db:
        assert pipeline.draft_view(pipeline.owned(db, scene['p'], identity), scene['p']) == later['draft']
    with TestClient(app) as fresh:
        auth = reply(fresh.post('/api/login', json={'username': account[0], 'password': account[1]}))
        fresh.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        cold_rows = all_rows()
        assert reply(fresh.get(BASE + '/' + identity)) == later['draft']
        assert reply(fresh.get(shared_path)) == shared
        assert reply(fresh.get(receipt_path)) == replay
        assert reply(fresh.get('/api/instances/' + release['instance_id'] + '/releases'))[0]['snapshot'] == release['snapshot']
        assert all_rows() == cold_rows
    assert (fees(call_ids(private)), storage_files(), len(http.requests)) == paid[1:]
