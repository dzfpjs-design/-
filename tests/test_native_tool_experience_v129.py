"""Actual native-step HTTP execution and explicit human experience review.

Only the exact registered public model endpoint returns a synthetic response.
CloudCall reservation/settlement, issued releases, Worker, loopback tool HTTP,
Invocation, original goal acceptance and human review remain the real paths.
The synthetic response proves protocol/recovery, never model or business value.
"""
import copy
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents.models import CloudCall, Entity, Invocation, Job, Memory, Plan, Task
from generic_agents.services import as_dict
from generic_agents.worker import process_once
from tests.test_business_tools import INPUT, OUTPUT, service
from tests.test_instance_workspace import configuration, prepared, enable, object_row, task_row
from tests.test_tool_accepted_experience_v129 import (
    _calls, _case_command, _draft_command, _ok, _records, _rejected_source,
)
from tests.test_vertical_slice import manifest, publish


MODEL_ENDPOINT = 'https://api.deepseek.com/chat/completions'


def _provider(monkeypatch, tool_id, field):
    """Intercept one precise synthetic provider URL; forward real tool HTTP."""
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 10)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-v129-not-a-real-key')
    monkeypatch.setattr(settings(), 'deepseek_api_key', SecretStr('synthetic-v129-not-a-real-key'))
    original = httpx.Client.post
    sent = []

    def post(client, target, **kwargs):
        if str(target) != MODEL_ENDPOINT:
            return original(client, target, **kwargs)
        assert kwargs['headers']['Authorization'] == 'Bearer synthetic-v129-not-a-real-key'
        payload = copy.deepcopy(kwargs['json'])
        sent.append(payload)
        content = json.loads(payload['messages'][-1]['content'])
        registered = content['registered_tools']
        assert len(registered) == 1 and registered[0]['capability_id'] == tool_id
        proposal = {'summary': '合成计划仅核对固定档案的只读统计协议，结论留待人工核查', 'steps': [
            {'title': '核对固定档案均值', 'agent': 'data', 'action': 'invoke_capability',
                'capability_id': tool_id, 'arguments': {'field': field}},
            {'title': '整理待人工核查报告', 'agent': 'business', 'action': 'report'}]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(proposal)}}],
            'usage': {'prompt_tokens': 60, 'completion_tokens': 30, 'total_tokens': 90}},
            headers={'x-request-id': 'synthetic-native-v129-' + str(len(sent))})

    monkeypatch.setattr(httpx.Client, 'post', post)
    return sent


def _worker(identity):
    assert process_once(identity, deliver=False)
    with Session(engine()) as db:
        job = db.get(Job, identity)
        assert job.status == 'succeeded', str(job.data.get('error', ''))[:500]


def _job(task_id, kind, plan_id=None):
    with Session(engine()) as db:
        query = select(Job).where(Job.kind == kind, Job.data['task_id'].as_string() == task_id)
        if plan_id is not None: query = query.where(Job.data['plan_id'].as_string() == plan_id)
        rows = list(db.scalars(query))
        assert len(rows) == 1
        return rows[0].id


def _scene(client, monkeypatch, service, *, tool_criteria):
    registration_suffix = uuid4().hex
    calls_before = service['calls']
    with Session(engine()) as db:
        initial_cloud_ids = list(db.scalars(select(CloudCall.id)))
        initial_invocation_ids = list(db.scalars(select(Invocation.id)))
    tool = publish(client, manifest('tool', namespace='native_v129', name='archive_mean_' + registration_suffix, specification={},
        input_schema=copy.deepcopy(INPUT), output_schema=copy.deepcopy(OUTPUT), allowed_agent_roles=['data'],
        execution={'adapter': 'http', 'endpoint': service['http'], 'input_bindings': {'records': 'archive_records'},
            'side_effect': False, 'approval_policy': 'admin'}, data_policy={'external_allowed': True}))
    config = configuration()
    flow = config['flows']['资料核验']
    flow['steps'][0]['planning_tools'] = [{'capability_id': tool['id'], 'allow_external': True}]
    flow['confirmedSignature'] = json.dumps(signature_value(config, flow), ensure_ascii=False)
    release, _, checked = prepared(client, config)
    assert [item['id'] for item in checked['businesses'] if item['available']] == release['available_business_ids']
    enable(client, release)
    obj = object_row(client, release, 1)
    kind = next(item for item in release['snapshot']['object_types'] if item['key'] == obj['entity_type'])
    field = next(item['key'] for item in kind['fields'] if item['kind'] == '整数')
    with Session(engine()) as db: entity = db.get(Entity, obj['id']); attributes = copy.deepcopy(entity.data); version = entity.version
    attributes[field] = 12
    _ok(client.patch('/api/entities/' + obj['id'], json={'expected_version': version,
        'reason': '明确登记本次合成档案数量，后续只读工具必须绑定原档案', 'attributes': attributes}))
    task, creation = task_row(client, release, obj)
    path = '/api/workspace/tasks/' + task['id']; goal_path = path + '/goal'
    criteria = [{'id': 'enough', 'kind': 'evidence_sufficient', 'description': '原档案数量及对象依据充分'},
        {'id': 'human', 'kind': 'human_confirmation', 'description': '原人工流程结束后明确验收'}]
    if tool_criteria:
        criteria += [{'id': 'called', 'kind': 'capability_success', 'capability_id': tool['id'], 'description': '本次只读工具实际成功'},
            {'id': 'value', 'kind': 'tool_value', 'capability_id': tool['id'], 'field': 'value',
                'operator': 'eq', 'expected': 12, 'description': '原档案均值与固定数量一致'}]
    spec = {'purpose': '核对原人工流程、固定档案和明确采用的只读工具结果，不认定现场状态',
        'entity_ids': [obj['id']], 'required_fields': [field], 'criteria': criteria,
        'subgoals': [{'id': 'checked', 'description': '原资料与原人工核查全部满足',
            'criterion_ids': [item['id'] for item in criteria]}],
        'evidence': {'source': 'archive', 'minimum_verification': 'recorded',
            'time': {'mode': 'archive', 'timezone': 'Asia/Shanghai'}},
        'observation_mode': 'existing_only', 'max_steps': 12, 'max_tool_calls': 8, 'stop_on_conflict': True}
    define = {'request_id': str(uuid4()), 'expected_version': task['version'],
        'reason': '在原人工等待步骤明确本任务完成标准及只读调用范围', 'goal_spec': spec}
    _ok(client.post(goal_path, json=define))
    _worker(_job(task['id'], 'check_native_business_goal'))
    model = publish(client, manifest('foundation_model', namespace='native_v129',
        name='native_model_' + registration_suffix, allowed_agent_roles=['business'],
        execution={'provider': 'deepseek', 'model': 'deepseek-chat', 'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 2048},
        data_policy={'cloud_allowed': True}))
    planning_path = path + '/intelligent-plan'
    with monkeypatch.context() as model_transport:
        sent = _provider(model_transport, tool['id'], field)
        preview = _ok(client.post(planning_path + '/preview', json={'model_profile_id': model['id']}))
        start = {'request_id': str(uuid4()), 'expected_version': preview['task_version'],
            'reason': '已逐项核对合成模型发送内容，仅生成候选，不自动执行或验收',
            'model_profile_id': model['id'], 'preview_hash': preview['preview_hash'], 'cloud_authorized': True}
        listing = _ok(client.post(planning_path, json=start)); plan_id = listing['current']['id']
        _worker(_job(task['id'], 'generate_native_step_plan', plan_id))
    listing = _ok(client.get(planning_path))
    assert listing['current']['status'] == 'needs_review' and len(sent) == 1 and service['calls'] == calls_before
    execution_path = planning_path + '/' + plan_id + '/execution'
    execution_preview = _ok(client.post(execution_path + '/preview', json={}))
    approve = {'request_id': str(uuid4()), 'expected_version': execution_preview['task_version'],
        'reason': '已核对完整档案输入和真实合成HTTP地址，明确批准一次只读调用',
        'expected_plan_version': execution_preview['plan_version'],
        'approval_hash': execution_preview['approval_hash'], 'authorize_external_inputs': True}
    execution = _ok(client.post(execution_path + '/approve', json=approve))
    assert execution['execution']['status'] == 'queued'
    return {'task_id': task['id'], 'task_path': path, 'goal_path': goal_path, 'plan_id': plan_id,
        'planning_path': planning_path, 'execution_path': execution_path, 'execution': execution,
        'tool_id': tool['id'], 'entity_id': obj['id'], 'field': field, 'release': release, 'sent': sent,
        'actual_calls_before': calls_before,
        '_initial_cloud_ids': initial_cloud_ids, '_initial_invocation_ids': initial_invocation_ids,
        'commands': [{'path': '/api/workspace/instances/' + release['instance_id'] + '/tasks', 'body': creation},
            {'path': goal_path, 'body': define}, {'path': planning_path, 'body': start},
            {'path': execution_path + '/approve', 'body': approve}]}


def _formal(client, monkeypatch, service, *, settlement='accepted'):
    value = _scene(client, monkeypatch, service, tool_criteria=settlement == 'accepted')
    execution_path, plan_id = value['execution_path'], value['plan_id']
    execution_job = _job(value['task_id'], 'execute_native_step_plan', plan_id)
    if settlement == 'cancelled':
        listing = _ok(client.get(value['planning_path']))
        body = {'request_id': str(uuid4()), 'expected_version': listing['task_version'],
            'expected_plan_version': listing['current']['version'], 'reason': '在工具发送前停止原候选及其执行，保留原计划历史'}
        stop_path = value['planning_path'] + '/' + plan_id + '/stop'
        _ok(client.post(stop_path, json=body)); value['commands'].append({'path': stop_path, 'body': body})
        assert service['calls'] == value['actual_calls_before']
    else:
        _worker(execution_job)
        execution = _ok(client.get(execution_path)); run = execution['execution']
        assert run['status'] == 'needs_review' and execution['can_review'] and service['calls'] == value['actual_calls_before'] + 1
        step = next(item for item in run['steps'] if item['action'] == 'invoke_capability')
        assert step['output']['value'] == 12 and step['output']['samples'] == 1
        assert service['last_inputs']['field'] == value['field']
        assert service['last_inputs']['records'][0]['entity_id'] == value['entity_id']
        assert service['last_inputs']['records'][0]['attributes'][value['field']] == 12
        body = {'request_id': str(uuid4()), 'expected_version': execution['task_version'],
            'expected_execution_version': run['version'], 'result_hash': run['result_hash'], 'decision': settlement,
            'reason': '人工核对真实只读输出及原档案后明确记录采用或拒绝，不自动验收目标'}
        reviewed = _ok(client.post(execution_path + '/review', json=body))
        assert reviewed['execution']['status'] == settlement
        value['commands'].append({'path': execution_path + '/review', 'body': body})
    final_execution = _ok(client.get(execution_path))
    assert final_execution['execution']['status'] == settlement
    task = _ok(client.get(value['task_path']))
    for _ in range(2):
        assert task['status'] == 'workflow_waiting'
        body = {'version': task['version'], 'step_id': task['workflow_runtime']['current_step_id'],
            'note': '已核对本次原资料和只读计划处理结论，明确完成原人工步骤', 'request_id': str(uuid4())}
        human_path = value['task_path'] + '/complete-step'
        task = _ok(client.post(human_path, json=body))
        value['commands'].append({'path': human_path, 'body': body, 'response': task})
    assert task['status'] == 'workflow_completed'
    goal = _ok(client.get(value['goal_path']))
    body = {'request_id': str(uuid4()), 'expected_version': goal['version'], 'reason': '按原目标核查原人工流程和已处置工具计划'}
    _ok(client.post(value['goal_path'] + '/check', json=body))
    with Session(engine()) as db: check_job = db.get(Task, value['task_id']).data['native_goal_check_job_pin']['job_id']
    _worker(check_job)
    goal = _ok(client.get(value['goal_path'])); evaluation = goal['check']['evaluation']
    assert evaluation['ready_for_acceptance'] and evaluation['tool_execution']['all_reviewed']
    assert len(evaluation['tool_execution']['runs']) == 1 and evaluation['tool_execution']['runs'][0]['status'] == settlement
    assert len(evaluation['tool_results']) == int(settlement == 'accepted')
    acceptance = {'request_id': str(uuid4()), 'expected_version': goal['version'],
        'basis_hash': evaluation['basis_hash'], 'reason': '原资料、原人工流程和本次只读计划处置已核对，明确正式验收'}
    accepted = _ok(client.post(value['goal_path'] + '/accept', json=acceptance))
    assert accepted['acceptance']['evaluation']['completed'] and not accepted['acceptance']['learning_auto_published']
    value['commands'] += [{'path': value['goal_path'] + '/check', 'body': body},
        {'path': value['goal_path'] + '/accept', 'body': acceptance, 'response': accepted}]
    with Session(engine()) as db:
        task = db.get(Task, value['task_id']); plan = db.get(Plan, plan_id)
        calls = list(db.scalars(select(CloudCall).where(CloudCall.id.not_in(value['_initial_cloud_ids']))))
        invocations = list(db.scalars(select(Invocation).where(Invocation.id.not_in(value['_initial_invocation_ids']))))
        assert len(calls) == 1 and calls[0].status == 'succeeded' and calls[0].charged_tokens == 90
        assert plan.data['cloud_call_id'] == calls[0].id and len(value['sent']) == 1
        assert len(invocations) == int(settlement != 'cancelled')
        if invocations:
            call = invocations[0]
            assert call.status == 'succeeded' and call.capability_id == value['tool_id']
            assert call.data['approval']['native_step_execution'] is True
            assert call.data['approval']['plan_id'] == plan.id and call.data['job_id'] == execution_job
        value.update(acceptance=copy.deepcopy(task.data['native_goal_acceptance']),
            acceptance_hash=task.data['native_goal_acceptance_hash'],
            outcome=copy.deepcopy(as_dict(db.get(Memory, task.data['native_goal_data_receipt_id']))),
            invocation=copy.deepcopy(as_dict(invocations[0])) if invocations else None,
            cloud_call=copy.deepcopy(as_dict(calls[0])), execution_job_id=execution_job,
            workflow_runtime=copy.deepcopy(task.data['workflow_runtime']),
            execution_ledger=copy.deepcopy(task.data['native_step_execution']))
    value.pop('sent')
    value.pop('_initial_cloud_ids'); value.pop('_initial_invocation_ids')
    value['actual_calls_after'] = service['calls']
    value['actual_calls_delta'] = value['actual_calls_after'] - value['actual_calls_before']
    assert value['actual_calls_delta'] == int(settlement != 'cancelled')
    return value


def _experience(client, proof):
    path = proof['task_path'] + '/experience'; calls = _calls()
    with Session(engine()) as db:
        entities = {row.id: digest(as_dict(row)) for row in db.scalars(select(Entity))}
    view = _ok(client.get(path))
    assert not view['source']['scope_error'] and view['permissions']['draft']['allowed']
    draft_body = _draft_command(view); draft = _ok(client.post(path + '/draft', json=draft_body))
    submit_body = _case_command(draft, '原资料与原只读计划处置依据已逐项核查，明确提交方法审核')
    candidate = _ok(client.post(path + '/submit', json=submit_body))
    review_body = _case_command(candidate, '人工核查方法的原依据和限制后确认可信，不把旧结果晋升为现场事实',
        decision='trust', valid_until=None)
    trusted = _ok(client.post(path + '/review', json=review_body))
    assert trusted['draft']['status'] == 'trusted' and _calls() == calls
    with Session(engine()) as db:
        task = db.get(Task, proof['task_id']); method = db.get(Memory, trusted['draft']['id'])
        assert task.data['native_goal_acceptance'] == proof['acceptance']
        assert task.data['native_goal_acceptance_hash'] == proof['acceptance_hash']
        assert task.data['workflow_runtime'] == proof['workflow_runtime']
        assert task.data['native_step_execution'] == proof['execution_ledger']
        outcome = db.get(Memory, proof['outcome']['id'])
        assert as_dict(outcome) == proof['outcome'] and outcome.status == 'candidate'
        assert method.data['source_proof']['acceptance_hash'] == proof['acceptance_hash']
        assert {row.id: digest(as_dict(row)) for row in db.scalars(select(Entity))} == entities
        memory = copy.deepcopy(as_dict(method))
    commands = [{'path': path + '/' + operation, 'operation': operation, 'body': body, 'response': response}
        for operation, body, response in [('draft', draft_body, draft), ('submit', submit_body, candidate), ('review', review_body, trusted)]]
    readonly = []
    for command in commands:
        body = {'operation': command['operation'], 'original_request': copy.deepcopy(command['body']),
            'expected_version': trusted['version'], 'reason': '只核对原经验请求是否保存，不再次执行或审核', 'action': 'probe'}
        response = _ok(client.post(path + '/requests/recover', json=body))
        assert response['saved'] and not response['abandoned']
        readonly.append({'path': path + '/requests/recover', 'body': body, 'response': response})
    readbacks = [{'path': target, 'response': _ok(client.get(target))}
        for target in (proof['task_path'], proof['goal_path'], path)]
    assert readbacks[1]['response']['acceptance'] == proof['acceptance']
    assert readbacks[2]['response']['draft'] == trusted['draft']
    assert _calls() == calls
    return {'method_id': memory['id'], 'memory': memory, 'commands': commands, 'readonly_requests': readonly,
        'readbacks': readbacks,
        'calls': calls, 'new_jobs': 0, 'new_invocations': 0, 'new_cloud_calls': 0}


def run_native_tool_journey(client, monkeypatch, service, *, settlement='accepted'):
    """Return actual JSON-safe commands/readbacks for a later Root checkpoint."""
    proof = _formal(client, monkeypatch, service, settlement=settlement)
    proof['experience'] = _experience(client, proof)
    assert service['calls'] == proof['actual_calls_after']
    return {'schema': 'native-tool-experience-journey-proof-v129', **proof,
        'physical_state_verified': False, 'automatic_publication': False, 'real_paid_calls': 0}


def test_native_tool_http_and_formal_acceptance_reach_explicit_human_experience(client, monkeypatch, service):
    proof = run_native_tool_journey(client, monkeypatch, service)
    assert proof['experience']['memory']['status'] == 'trusted' and service['calls'] == 1
    assert proof['acceptance']['evaluation']['tool_results'][0]['invocation_id'] == proof['invocation']['id']


@pytest.mark.parametrize('settlement', ['rejected', 'cancelled'])
def test_runs_only_rejected_or_public_planning_stop_preserves_formal_method_basis(client, monkeypatch, service, settlement):
    proof = run_native_tool_journey(client, monkeypatch, service, settlement=settlement)
    evaluation = proof['acceptance']['evaluation']
    assert evaluation['tool_results'] == [] and evaluation['tool_execution']['runs'][0]['status'] == settlement
    assert evaluation['tool_execution']['basis_hash'] != digest([])
    assert proof['experience']['memory']['status'] == 'trusted'
    assert service['calls'] == int(settlement == 'rejected')


def test_damaged_real_native_invocation_cannot_become_a_trusted_experience(client, monkeypatch, service):
    proof = _formal(client, monkeypatch, service); path = proof['task_path'] + '/experience'
    view = _ok(client.get(path)); assert not view['source']['scope_error']
    body = _draft_command(view); call = proof['invocation']; data = copy.deepcopy(call['data'])
    changed_output = copy.deepcopy(data['output']); changed_output['value'] += 1
    with Session(engine()) as db:
        contract = copy.deepcopy(db.get(Plan, proof['plan_id']).data['execution_contract'])
    input_step = next(item for item in contract['steps'] if item['action'] == 'invoke_capability')
    changed_input = copy.deepcopy(input_step['input_snapshot']); changed_input['field'] = 'wrong_field'
    for changes in ({'data': {**data, 'output': changed_output}}, {'data': {**data, 'input_hash': '0' * 64}},
            {'data': {**data, 'inputs': changed_input}}, {'data': {**data, 'approval': {**data['approval'], 'step_index': 99}}}):
        with _records([(Invocation, call['id'], changes)]): _rejected_source(client, path, body)
    assert service['calls'] == 1
    result = _experience(client, proof)
    assert result['memory']['status'] == 'trusted' and service['calls'] == 1
