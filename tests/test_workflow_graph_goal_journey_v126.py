"""Real issued v4 graph -> Worker/Invocation -> original formal goal acceptance.

Only the inherited synthetic document/provider seams create the source fixture.
No release, consent, capability, graph result, lease or goal gate is fabricated
on the successful path. Root alone runs this against its owned disposable PG.
The explicit corruptions below are negative tests, never execution authority.
"""
import copy
import json
import math
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Capability, CloudCall, Entity, Evidence, InstanceRelease, Invocation, Job, Memory, Task
from generic_agents.graph_event_models import GraphEventAdmission
from generic_agents.report_full_semantic_region_models import FullSemanticRegionProof
from generic_agents.worker import process_once
from tests.test_graph_wait_journey_v118 import (
    waiting_release, graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene,
    reply, human, poll,
)
from tests.test_graph_event_pipeline import observe, pending_intents, delivery_job


def _worker(identity):
    assert process_once(identity, deliver=False)
    with Session(engine()) as db:
        row = db.get(Job, identity)
        assert row.status == 'succeeded', str(row.data.get('error', ''))[:500]


def _snapshot():
    with Session(engine()) as db:
        return {
            'tasks': {row.id: digest({'version': row.version, 'status': row.status, 'data': row.data})
                for row in db.scalars(select(Task))},
            'entities': {row.id: digest({'version': row.version, 'data': row.data})
                for row in db.scalars(select(Entity))},
            'memory_statuses': {row.id: row.status for row in db.scalars(select(Memory))},
            'cloud_call_ids': set(db.scalars(select(CloudCall.id))),
        }


def _unchanged(before):
    with Session(engine()) as db:
        for identity, signature in before['tasks'].items():
            row = db.get(Task, identity)
            assert digest({'version': row.version, 'status': row.status, 'data': row.data}) == signature
        for identity, signature in before['entities'].items():
            row = db.get(Entity, identity)
            assert digest({'version': row.version, 'data': row.data}) == signature
        for identity, status in before['memory_statuses'].items():
            assert db.get(Memory, identity).status == status
        assert set(db.scalars(select(CloudCall.id))) == before['cloud_call_ids']


def _options(value):
    release, client = value['release'], value['client']
    business_id = release['available_business_ids'][0]
    business = next(row for row in release['snapshot']['businesses'] if row['id'] == business_id)
    nodes = [node for node in value['adopt_body']['graph']['nodes'] if node['kind'] == 'ability']
    assert len(nodes) == 1
    node = nodes[0]
    path = '/api/workspace/instances/' + release['instance_id'] + '/businesses/' + business_id + '/goal-tool-options'
    metadata = reply(client.get(path, params={'release_id': release['id']}))
    assert metadata['schema'] == 'native-goal-tools-v1'
    assert metadata['release_id'] == release['id'] and metadata['configuration_hash'] == release['configuration_hash']
    tool = next(row for row in metadata['tools'] if row['id'] == node['execution']['capability_id'])
    assert tool['sources'] == ['workflow_step'] and tool['step_names']
    fields = {row['key']: row['kind'] for row in tool['output_fields']}
    assert fields.get('value') == 'number' and fields.get('sample_count') == 'integer'
    # This is the original P0: the compatibility flow is not an execution list.
    assert not any(step.get('execution', {}).get('capability_id') == tool['id'] for step in business['flow']['steps'])
    with Session(engine()) as db:
        actual = db.get(Capability, tool['id'])
        assert actual.status == 'published' and actual.content_hash == node['execution']['capability_hash'] == tool['content_hash']
        assert actual.release_version == tool['version'] == node['execution']['release_version']
    return {'path': path, 'metadata': metadata, 'tool': tool, 'node': node, 'business_id': business_id}


def _create(value, *, mismatch=False):
    client, release, obj = value['client'], value['release'], value['object']
    selected = _options(value)
    kind = next(row for row in release['snapshot']['object_types'] if row['key'] == obj['entity_type'])
    field = next(row['key'] for row in kind['fields'] if row['key'] in obj['attributes'])
    argument = selected['node']['execution']['arguments']
    assert argument['operation'] == 'count'
    numeric_key = next((row['key'] for row in kind['fields'] if row['label'] == argument['field']), None)
    number = obj['attributes'].get(numeric_key)
    expected = int(isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number))
    criteria = [
        {'id': 'enough', 'kind': 'evidence_sufficient', 'description': '原对象档案充分且无冲突'},
        {'id': 'human', 'kind': 'human_confirmation', 'description': '原协同图结束后明确验收'},
        {'id': 'called', 'kind': 'capability_success', 'capability_id': selected['tool']['id'], 'description': '本任务固定图能力真实成功'},
        {'id': 'counted', 'kind': 'tool_value', 'capability_id': selected['tool']['id'], 'field': 'value',
            'operator': 'eq', 'expected': expected + int(mismatch), 'description': '仅核对原对象有效数值条数；零不代表现场不存在'},
        {'id': 'samples', 'kind': 'tool_value', 'capability_id': selected['tool']['id'], 'field': 'sample_count',
            'operator': 'eq', 'expected': expected, 'description': '有效样本数与发起前固定对象一致'},
    ]
    spec = {'purpose': 'V126核对原对象的注册统计回执与真实协同图完成，不认定现场状态',
        'entity_ids': [obj['id']], 'required_fields': [field],
        'criteria': criteria, 'subgoals': [{'id': 'original', 'description': '原任务资料、调用、数值及人工验收全部满足',
            'criterion_ids': [item['id'] for item in criteria]}],
        'evidence': {'source': 'archive', 'minimum_verification': 'recorded',
            'time': {'mode': 'archive', 'timezone': 'Asia/Shanghai'}},
        'observation_mode': 'existing_only', 'max_steps': 12, 'max_tool_calls': 12, 'stop_on_conflict': True}
    body = {'release_id': release['id'], 'business_id': selected['business_id'], 'entity_ids': [obj['id']],
        'title': 'V126图能力正式目标核查', 'request_id': str(uuid4()),
        'graph_inputs': {'watched_entity': obj['id']}, 'goal_spec': spec}
    path = '/api/workspace/instances/' + release['instance_id'] + '/tasks'
    task = reply(client.post(path, json=body), status=201)
    assert reply(client.post(path, json=body), status=201)['id'] == task['id']
    with Session(engine()) as db:
        row = db.get(Task, task['id'])
        assert row.data['native_goal_context']['goal_snapshot']['spec'] == row.data['goal_spec']
        definition = next(item for item in row.data['native_goal_commands'] if item['operation'] == 'define')
        assert definition['request_id'] == 'creation_' + digest(body['request_id'])
        pin = copy.deepcopy(row.data['native_goal_check_job_pin'])
        assert db.get(Job, pin['job_id']).kind == 'check_native_business_goal'
    return {**selected, 'task': reply(client.get('/api/workspace/tasks/' + task['id'])),
        'creation': {'path': path, 'body': body}, 'expected_count': expected, 'definition_job': pin['job_id']}


def _goal(value, task):
    return reply(value['client'].get('/api/workspace/tasks/' + task['id'] + '/goal'))


def _check(value, task):
    client = value['client']; path = '/api/workspace/tasks/' + task['id'] + '/goal/check'
    current = _goal(value, task)
    body = {'request_id': str(uuid4()), 'expected_version': current['version'], 'reason': '按原目标重新核对真实图能力和原资料'}
    reply(client.post(path, json=body))
    with Session(engine()) as db: identity = db.get(Task, task['id']).data['native_goal_check_job_pin']['job_id']
    _worker(identity)
    return _goal(value, task), {'path': path, 'body': body, 'job_id': identity}


def _complete(value, *, mismatch=False):
    client = value['client']; result = _create(value, mismatch=mismatch); task = result['task']
    _worker(result['definition_job'])
    initial = _goal(value, task)
    assert not initial['check']['evaluation']['ready_for_acceptance']
    assert not next(item for item in initial['check']['evaluation']['checks'] if item['id'] == 'called')['passed']
    job = next(iter(task['graph_jobs'].values()))['job_id']; _worker(job)
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    receipt = next(item for item in task['workflow_runtime']['receipts'] if item['execution_mode'] == 'registered_graph_ability')
    assert receipt['capability_id'] == result['tool']['id'] and receipt['output']['value'] == result['expected_count']
    assert receipt['output']['sample_count'] == result['expected_count']
    assert receipt['output']['provenance'] == [{'entity_id': value['object']['id'], 'version': value['object']['version']}]
    early = _goal(value, task)
    assert next(item for item in early['check']['evaluation']['checks'] if item['id'] == 'called')['passed']
    assert not early['check']['evaluation']['ready_for_acceptance']
    denied = client.post('/api/workspace/tasks/' + task['id'] + '/goal/accept', json={
        'request_id': str(uuid4()), 'expected_version': early['version'], 'basis_hash': early['check']['evaluation']['basis_hash'],
        'reason': '真实能力已返回也不能跳过原图人工步骤和来源等待'})
    assert denied.status_code == 409
    task, first = human(client, task, 'a')
    window = next(iter(task['graph_wait_authority']['windows'].values()))
    assert window['status'] == 'open'
    observation, observation_body, processing_job = observe(client, value, temperature=95)
    intent_id, producer_id = pending_intents(observation['id'])[0]
    assert producer_id == value['producer']['id']
    delivery, _ = delivery_job(intent_id); _worker(delivery)
    task = poll(client, task['id'])
    window = next(iter(task['graph_wait_authority']['windows'].values()))
    assert window['status'] == 'consumed'
    task, final = human(client, task, 'c')
    assert task['completed'] and task['status'] == 'workflow_completed'
    goal, check_command = _check(value, task)
    evaluation = goal['check']['evaluation']; checks = {item['id']: item for item in evaluation['checks']}
    assert checks['called']['passed'] and checks['samples']['passed']
    assert checks['counted']['passed'] is (not mismatch)
    assert evaluation['ready_for_acceptance'] is (not mismatch)
    normalized = evaluation['tool_results']
    assert len(normalized) == 1 and normalized[0]['invocation_id'] == receipt['invocation_id']
    assert normalized[0]['candidate_only'] is True and normalized[0]['physical_state_verified'] is False
    return {**result, 'task': task, 'goal': goal, 'receipt': receipt,
        'human_commands': [{'path': '/api/workspace/tasks/' + task['id'] + '/graph/complete', 'body': first},
            {'path': '/api/workspace/tasks/' + task['id'] + '/graph/complete', 'body': final}],
        'check_command': check_command,
        'source': {'path': '/api/observations', 'body': observation_body, 'observation_id': observation['id'],
            'processing_job_id': processing_job, 'intent_id': intent_id, 'producer_id': producer_id,
            'delivery_job_id': delivery, 'admission_id': window['event_id']}}


def _acceptance(value, completed):
    task, goal = completed['task'], completed['goal']
    return {'path': '/api/workspace/tasks/' + task['id'] + '/goal/accept', 'body': {
        'request_id': str(uuid4()), 'expected_version': goal['version'],
        'basis_hash': goal['check']['evaluation']['basis_hash'], 'reason': '仅验收本任务原目标和真实图回执，不晋升现场事实或可信经验'}}


def run_goal_journey(waiting_release, tmp_path):
    """Return actual JSON-safe proof for Root's later private durability checkpoint."""
    value = waiting_release; before = _snapshot(); completed = _complete(value); client = value['client']
    acceptance = _acceptance(value, completed)
    accepted = reply(client.post(acceptance['path'], json=acceptance['body']))
    assert accepted['acceptance']['evaluation']['completed'] and accepted['acceptance']['physical_state_verified'] is False
    assert not accepted['acceptance']['learning_auto_published']
    assert reply(client.post(acceptance['path'], json=acceptance['body']))['acceptance'] == accepted['acceptance']
    task_id = completed['task']['id']; task_path = '/api/workspace/tasks/' + task_id
    recovery = {'path': task_path + '/goal/requests/recover', 'body': {'operation': 'accept',
        'original_request': copy.deepcopy(acceptance['body']), 'expected_version': accepted['version'],
        'reason': '只核对原目标验收请求是否保存', 'action': 'probe'}}
    assert reply(client.post(recovery['path'], json=recovery['body']))['saved']
    export = reply(client.get(task_path + '/goal/export'))
    assert export['goal']['acceptance'] == accepted['acceptance'] and export['physical_state_verified'] is False
    with Session(engine()) as db:
        row = db.get(Task, task_id); call = db.get(Invocation, completed['receipt']['invocation_id'])
        job = db.get(Job, completed['receipt']['request_id']); release = db.get(InstanceRelease, value['release']['id'])
        assert call.status == 'succeeded' and job.status == 'succeeded'
        activation = completed['receipt']['activation_id']; transport = copy.deepcopy(row.data['graph_jobs'][activation])
        result = db.get(Memory, row.data['native_goal_data_receipt_id'])
        graph_result = db.get(Memory, row.data['workflow_runtime']['data_receipt_id'])
        admission = db.get(GraphEventAdmission, completed['source']['admission_id'])
        assert admission.intent_id == completed['source']['intent_id'] and admission.producer_id == value['producer']['id']
        assert result.status == graph_result.status == 'candidate'
        assert result.data['auto_publish'] is False and result.data['entity_state_updated'] is False
        assert release.configuration_hash == value['release']['configuration_hash'] and digest(release.data['snapshot']) == release.configuration_hash
        proof = {'schema': 'workflow-graph-goal-journey-proof-v126', 'task_id': task_id, 'task_path': task_path,
            'goal_path': task_path + '/goal', 'export_path': task_path + '/goal/export',
            'instance_id': release.instance_id, 'release_id': release.id, 'package_id': release.package_id,
            'business_id': row.data['business_id'], 'adoption_path': value['adopt_path'],
            'adoption_request_id': value['adopt_body']['request_id'], 'configuration_hash': release.configuration_hash,
            'metadata': completed['metadata'], 'creation': {**completed['creation'], 'creation_hash': row.data['creation_hash']},
            'goal_spec': copy.deepcopy(row.data['goal_spec']), 'goal_context': copy.deepcopy(row.data['native_goal_context']),
            'goal_context_hash': row.data['native_goal_context_hash'], 'goal_commands': copy.deepcopy(row.data['native_goal_commands']),
            'goal_definition_request_id': next(item['request_id'] for item in row.data['native_goal_commands'] if item['operation'] == 'define'),
            'source': {**completed['source'], 'admission': copy.deepcopy(admission.data)},
            'human_commands': completed['human_commands'], 'check_command': completed['check_command'],
            'acceptance': {**acceptance, 'response': accepted}, 'read_only_recovery': recovery,
            'invocation': {'id': call.id, 'idempotency_key': call.idempotency_key, 'data': copy.deepcopy(call.data)},
            'ability_job': {'id': job.id, 'idempotency_key': job.idempotency_key, 'data': copy.deepcopy(job.data)},
            'transport': transport, 'raw_graph_receipt': completed['receipt'],
            'goal_data_receipt_id': result.id, 'graph_data_receipt_id': graph_result.id,
            'material_snapshots_hash': digest(row.data['material_snapshots']),
            'candidate_only': True, 'physical_state_verified': False, 'automatic_experience_published': False}
        proof['new_cloud_calls'] = 0
    _unchanged(before)
    folder = tmp_path / 'formal-graph-goal-proof'; folder.mkdir(mode=0o700)
    path = folder / 'proof.json'; path.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True), encoding='utf-8'); path.chmod(0o600)
    proof['proof_path'] = str(path)
    return proof


def test_real_graph_goal_success_value_and_saved_acceptance(waiting_release, tmp_path):
    proof = run_goal_journey(waiting_release, tmp_path)
    assert proof['acceptance']['body']['request_id'] == proof['acceptance']['response']['acceptance']['request_id']


def test_successful_graph_with_wrong_frozen_tool_value_cannot_be_accepted(waiting_release):
    completed = _complete(waiting_release, mismatch=True); command = _acceptance(waiting_release, completed)
    assert waiting_release['client'].post(command['path'], json=command['body']).status_code == 409
    with Session(engine()) as db:
        row = db.get(Task, completed['task']['id']); assert not row.data.get('native_goal_acceptance')
        assert not db.scalar(select(Memory.id).where(Memory.data['type'].as_string() == 'native_goal_outcome',
            Memory.data['task_id'].as_string() == row.id))


@pytest.mark.parametrize('damage', ['invocation_output', 'invocation_lease', 'graph_output', 'transport_lease', 'dispatch_actor', 'entity_input', 'instance_context', 'original_evidence'])
def test_formal_acceptance_cold_checks_real_graph_invocation_and_original_scope(waiting_release, damage):
    completed = _complete(waiting_release); command = _acceptance(waiting_release, completed)
    with Session(engine()) as db:
        task = db.get(Task, completed['task']['id']); receipt = completed['receipt']; data = copy.deepcopy(task.data)
        if damage.startswith('invocation_'):
            call = db.get(Invocation, receipt['invocation_id']); changed = copy.deepcopy(call.data)
            if damage == 'invocation_output': changed['output']['value'] = completed['expected_count'] + 100
            else: changed['lease_token'] = str(uuid4())
            call.data = changed
        elif damage == 'original_evidence':
            proof = db.get(FullSemanticRegionProof, waiting_release['adopt_body']['proof_id'])
            assert proof is not None
            original = db.get(Evidence, proof.data['proof']['selection']['anchor']['source_evidence_id'])
            assert original is not None and original.sha256 != '0' * 64
            original.sha256 = '0' * 64
        elif damage == 'entity_input':
            obj = db.get(Entity, waiting_release['object']['id']); attrs = copy.deepcopy(obj.data)
            key = next(iter(attrs)); attrs[key] = '负控：原输入已改变'; obj.data = attrs
        else:
            if damage == 'graph_output': data['graph_runtime']['activations'][receipt['activation_id']]['output']['value'] = completed['expected_count'] + 100
            elif damage == 'transport_lease': data['graph_jobs'][receipt['activation_id']]['dispatch_ownership']['lease_token'] = str(uuid4())
            elif damage == 'dispatch_actor': data['graph_jobs'][receipt['activation_id']]['dispatch_authorization']['actor_id'] = str(uuid4())
            else: data['instance_release_id'] = str(uuid4())
            task.data = data
        db.commit()
    response = waiting_release['client'].post(command['path'], json=command['body'])
    assert response.status_code in {404, 409}, response.text
    with Session(engine()) as db:
        row = db.get(Task, completed['task']['id']); assert not row.data.get('native_goal_acceptance')
        assert not db.scalar(select(Memory.id).where(Memory.data['type'].as_string() == 'native_goal_outcome',
            Memory.data['task_id'].as_string() == row.id))


def test_another_tasks_successful_graph_receipt_cannot_satisfy_the_new_original_goal(waiting_release):
    first = _complete(waiting_release); second = _create(waiting_release)
    with Session(engine()) as db:
        row = db.get(Task, second['task']['id']); data = copy.deepcopy(row.data)
        data['workflow_runtime']['receipts'] = [copy.deepcopy(first['receipt'])]; row.data = data; db.commit()
    from generic_agents.native_goal_tools import workflow_goal_receipts
    from generic_agents.security import Principal
    me = reply(waiting_release['client'].get('/api/me'))
    with Session(engine()) as db:
        row = db.get(Task, second['task']['id'])
        with pytest.raises(HTTPException):
            workflow_goal_receipts(db, Principal(me['user_id'], me['workspace_id'], me['role'], 'actual-graph-goal-negative'), row)
        assert not row.data.get('native_goal_acceptance')
        assert not db.scalar(select(Invocation.id).where(Invocation.idempotency_key.like('graph:' + row.id + ':%')))
