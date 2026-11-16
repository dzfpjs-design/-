"""Actual typed-float API/PG graph, formal acceptance and human experience.

The inherited source fixture uses its explicit synthetic document/provider IO.
All schema/source adoption, capabilities, roles, release, object, Task, actual
builtin invocation and human review are issued through their real public gates.
This graph preserves the source fork/join/decision and has no event subscription;
v4's exact empty event inventory grants no producer or waiting permission.
This is an API durability journey, not a claim that new local UI numeric input
can already encode negative zero or large integral floats without loss.
"""
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_wait_sources_v118 import POLICY
from generic_agents.models import Capability, CloudCall, Entity, Invocation, Job, Memory, SetupDraft, Task
from tests.test_full_semantic_graph_journey_v114 import (
    graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene,
    configured, graph_command, generate, create_object,
)
from tests.test_instance_workspace import save
from tests.test_report_evidence_pipeline import reply
from tests.test_report_full_semantic_graph_binding_v3 import ref
from tests.test_tool_accepted_experience_v129 import run_experience_for_accepted
from tests.test_typed_payload_journey_v131 import _canonical, _public, _typed_number
from tests.test_vertical_slice import manifest
from tests.test_workflow_graph_goal_journey_v126 import _options, _check, _acceptance, _worker


@pytest.fixture
def typed_graph_seed(v7_scene, monkeypatch):
    """Declare the numeric field before the first manual/proof/schema issuance.

    The original source fixture has only textual object fields. A genuine
    numeric archive result therefore needs a human-declared synthetic field,
    rather than patching an issued schema or inventing object_records later.
    """
    from tests import test_report_full_semantic_source_review_postgres as source_review
    original = source_review.human_value

    def manual_numeric_field(result):
        value = original(result)
        fields = value['result']['objects'][-1]['fields']
        assert not any(field['label'] == '本次核查数值' for field in fields)
        fields.append({'label': '本次核查数值', 'kind': '数值', 'required': False, 'unit': None,
            'citations': [], 'inference_reason': '人工明确新增的合成数值字段，仅核对数值保存；不认定原文声明或现场事实'})
        return value

    monkeypatch.setattr(source_review, 'human_value', manual_numeric_field)
    return graph_seed.__wrapped__(v7_scene, monkeypatch)


def _raw(client, path, body, status=200, *, method='post'):
    result = getattr(client, method)(path, content=_canonical(body),
        headers={'Content-Type': 'application/json'})
    value = reply(result, status=status); _public(value)
    return value


def _min_capability(client, number):
    output = {'type': 'object', 'required': ['value', 'sample_count', 'provenance'],
        'properties': {'value': {'type': ['number', 'null'], 'minimum': number, 'maximum': 1e21},
            'sample_count': {'type': 'integer'}, 'provenance': {'type': 'array'}}}
    body = manifest('tool', name='typed_min_' + uuid4().hex,
        description='按原登记对象计算最小值，核对数值类型持久化，不认定现场事实',
        allowed_agent_roles=['business'],
        input_schema={'type': 'object', 'required': ['records', 'field', 'operation'], 'additionalProperties': False,
            'properties': {'records': {'type': 'array', 'items': {'type': 'object'}}, 'field': {'type': 'string'},
                'operation': {'enum': ['min']}}},
        output_schema=output, execution={'adapter': 'builtin', 'handler': 'aggregate_values', 'input_bindings': {}},
        specification={}, data_policy={'cloud_allowed': False})
    commands = []
    row = _raw(client, '/api/capabilities', body, 201)
    commands.append({'path': '/api/capabilities', 'body': body, 'response': row})
    for action, reason in [('validate', '核对真实最小值能力与原数值合同'), ('publish', '明确发布已核对的只读最小值能力')]:
        path = '/api/capabilities/' + row['id'] + '/' + action
        command = {'expected_version': row['version'], 'reason': reason}
        row = _raw(client, path, command)
        commands.append({'path': path, 'body': command, 'response': row})
    with Session(engine()) as db:
        cold = db.get(Capability, row['id'])
        _typed_number(cold.data['output_schema']['properties']['value']['minimum'], number)
        _typed_number(cold.data['output_schema']['properties']['value']['maximum'], 1e21)
        assert cold.content_hash == cold.validated_hash == digest(cold.data)
    return row, commands


def _issue(seed, number):
    client = seed['client']; view, member, compilation = configured(seed)
    configuration = copy.deepcopy(view['configuration'])
    name = seed['record']['design']['result']['businesses'][0]['name']
    configuration['automation']['policies'][name]['rules']['data'] = {'mode': '自动运行'}
    save(client, configuration, review_automation=True)
    cap, capability_commands = _min_capability(client, number)
    view = reply(client.get('/api/setup/draft')); _public(view)
    declared = [(obj, field) for obj in view['configuration']['instanceObjects']
        for field in obj['fields'] if field['kind'] == '数值']
    assert declared, 'Use typed_graph_seed: the original text-only source cannot authorize a numeric archive field'
    selected_object, selected_field = declared[0]
    object_name, numeric_label = selected_object['name'], selected_field['label']
    command = graph_command(seed, member, view['configuration'], cap)
    graph = command['graph']
    ability = next(node for node in graph['nodes'] if node['kind'] == 'ability')
    ability['execution']['arguments'].update(operation='min', field=numeric_label)
    # A human-declared task input is separately preserved in the run's input
    # snapshot; the builtin still consumes the server's actual object_records.
    graph['input_schema']['properties']['typed_number'] = {'type': 'number', 'minimum': -0.0, 'maximum': 1e21}
    graph['input_schema']['required'].append('typed_number')
    graph['references']['typed_number'] = {'kind': 'number', 'nullable': False,
        'source': 'input', 'field': 'typed_number', 'node_id': None}
    owner = next(item for item in command['owner_bindings'] if item['owner_path'] == '/global_claims/0')
    next(clause for clause in owner['clauses'] if clause['source_field'] == 'statement')['targets'].append(ref('value_ref', 'typed_number'))
    node = next(node for node in graph['nodes'] if node['id'] == 'a')
    node['output_schema']['properties']['reading'] = {'type': 'number', 'minimum': -0.0, 'maximum': 1e21}
    node['output_schema']['required'].append('reading')
    assert graph['subscriptions'] == []
    command.update(schema='report-full-semantic-graph-preview-command-v4',
        event_source_bindings={}, event_admission_policy=copy.deepcopy(POLICY))
    path = '/api/setup/full-semantic-graph-wire/' + seed['record']['id'] + '/v4'
    preview = _raw(client, path + '/preview', command)
    assert preview['canonical'] == _canonical(preview['value'])
    body = {**command, 'schema': 'report-full-semantic-graph-command-v4', 'request_id': str(uuid4()),
        'preview_hash': preview['value']['preview_hash'], 'consent': True, 'semantic_mode': 'workspace_workflow_design'}
    accepted = _raw(client, path + '/adopt', body)
    assert accepted['canonical'] == _canonical(accepted['value'])
    recovery_path = path + '/requests/' + body['request_id']
    recovered = reply(client.get(recovery_path)); _public(recovered)
    assert recovered['value']['found']
    assert recovered['value']['receipt'] == accepted['value']['receipt']
    assert recovered['canonical'] == _canonical(recovered['value'])
    assert _raw(client, path + '/adopt', body)['value']['receipt'] == accepted['value']['receipt']
    current = reply(client.get('/api/setup/draft')); _public(current)
    me = reply(client.get('/api/me'))
    with Session(engine()) as db:
        cold = db.scalar(select(SetupDraft).where(SetupDraft.workspace_id == me['workspace_id']))
        assert cold is not None
        assert _canonical(cold.data) == _canonical(current['configuration'])
    compilation.update(revision=current['revision'], role_bindings={'职责甲': member['id'], '职责乙': member['id']})
    checked = _raw(client, '/api/instances/check', compilation)
    assert checked['general_issues'] == [], checked['general_issues']
    release = generate(client, compilation, checked); _public(release)
    assert release['snapshot']['semantic_runtime_plan']['schema'] == 'instance-full-semantic-runtime-plan-v4'
    instance = next(row for row in reply(client.get('/api/instances')) if row['id'] == release['instance_id'])
    activation = {'version': instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}
    _raw(client, '/api/instances/' + instance['id'] + '/activate', activation)
    kind = next(item for item in release['snapshot']['object_types'] if item['name'] == object_name)
    obj, _ = create_object(client, release, kind)
    numeric_key = next(field['key'] for field in kind['fields']
        if field['label'] == numeric_label and field['kind'] == '数值')
    with Session(engine()) as db:
        entity = db.get(Entity, obj['id']); attributes = copy.deepcopy(entity.data); version = entity.version
    attributes[numeric_key] = number
    object_path = '/api/entities/' + obj['id']
    object_body = {'expected_version': version, 'reason': '明确登记合成原数值，保留浮点类型及负零符号', 'attributes': attributes}
    changed = _raw(client, object_path, object_body, method='patch')
    with Session(engine()) as db:
        entity = db.get(Entity, obj['id']); _typed_number(entity.data[numeric_key], number)
        assert _canonical(entity.data) == _canonical(attributes)
    obj = {**obj, 'version': changed['version'], 'attributes': changed['data']}
    return {'client': client, 'release': release, 'object': obj, 'adopt_body': body,
        'adopt_path': path, 'capability': cap, 'numeric_key': numeric_key, 'numeric_label': numeric_label,
        'commands': capability_commands + [{'path': path + '/preview', 'body': command, 'response': preview},
            {'path': path + '/adopt', 'body': body, 'response': accepted},
            {'path': '/api/instances/check', 'body': compilation, 'response': checked},
            {'path': '/api/instances/' + instance['id'] + '/activate', 'body': activation},
            {'path': object_path, 'method': 'PATCH', 'body': object_body, 'response': changed}],
        'adoption_recovery_path': recovery_path}


def _human(client, current, node_id, number):
    pending = next(item for item in current['graph_pending_humans'] if item['node_id'] == node_id)
    body = {'request_id': str(uuid4()), 'activation_id': pending['activation_id'], 'version': pending['version'],
        'cancel_epoch': current['graph_runtime']['cancel_epoch'],
        'output': {'ok': True, **({'reading': number} if node_id == 'a' else {})}}
    path = '/api/workspace/tasks/' + current['id'] + '/graph/complete'
    result = _raw(client, path, body)
    assert _raw(client, path, body)['graph_runtime']['state_hash'] == result['graph_runtime']['state_hash']
    cold = reply(client.get('/api/workspace/tasks/' + current['id'])); _public(cold)
    if node_id == 'a':
        _typed_number(cold['graph_runtime']['activations'][pending['activation_id']]['output']['reading'], number)
    return cold, {'path': path, 'body': body, 'response': result}


def run_typed_graph_goal_journey(graph_seed, tmp_path, number=-0.0):
    """Consume typed_graph_seed and return genuine JSON-safe restart proof."""
    _typed_number(number, number)
    with Session(engine()) as db: cloud_before = set(db.scalars(select(CloudCall.id)))
    value = _issue(graph_seed, number); client = value['client']; selected = _options(value)
    criteria = [
        {'id': 'enough', 'kind': 'evidence_sufficient', 'description': '原档案数值及对象依据充分'},
        {'id': 'human', 'kind': 'human_confirmation', 'description': '原并行协同结束后明确人工验收'},
        {'id': 'called', 'kind': 'capability_success', 'capability_id': selected['tool']['id'], 'description': '本任务注册最小值能力真实成功'},
        {'id': 'value', 'kind': 'tool_value', 'capability_id': selected['tool']['id'], 'field': 'value',
            'operator': 'eq', 'expected': number, 'description': '最小值对应本任务唯一原对象数值'},
        {'id': 'samples', 'kind': 'tool_value', 'capability_id': selected['tool']['id'], 'field': 'sample_count',
            'operator': 'eq', 'expected': 1, 'description': '恰好采用本任务一条有效数值档案'},
    ]
    spec = {'purpose': '核对真实只读最小值、原浮点输入及原人工协同，不认定现场事实',
        'entity_ids': [value['object']['id']], 'required_fields': [value['numeric_key']], 'criteria': criteria,
        'subgoals': [{'id': 'original', 'description': '原资料、真实调用及人工核对全部满足',
            'criterion_ids': [criterion['id'] for criterion in criteria]}],
        'evidence': {'source': 'archive', 'minimum_verification': 'recorded',
            'time': {'mode': 'archive', 'timezone': 'Asia/Shanghai'}},
        'observation_mode': 'existing_only', 'max_steps': 12, 'max_tool_calls': 12, 'stop_on_conflict': True}
    body = {'release_id': value['release']['id'], 'business_id': selected['business_id'],
        'entity_ids': [value['object']['id']], 'title': 'V131核对原浮点数值的完整API旅程',
        'request_id': str(uuid4()), 'graph_inputs': {'typed_number': number}, 'goal_spec': spec}
    creation_path = '/api/workspace/instances/' + value['release']['instance_id'] + '/tasks'
    current = _raw(client, creation_path, body, 201)
    assert _raw(client, creation_path, body, 201)['id'] == current['id']
    task_path = '/api/workspace/tasks/' + current['id']; goal_path = task_path + '/goal'
    current = reply(client.get(task_path)); _public(current)
    _typed_number(current['graph_runtime']['inputs']['typed_number'], number)
    with Session(engine()) as db:
        task = db.get(Task, current['id'])
        expected = next(item for item in task.data['goal_spec']['criteria'] if item['id'] == 'value')['expected']
        _typed_number(expected, number)
        initial_check = task.data['native_goal_check_job_pin']['job_id']
    _worker(initial_check)
    early = reply(client.get(goal_path)); assert not early['check']['evaluation']['ready_for_acceptance']
    ability_job = next(iter(current['graph_jobs'].values()))['job_id']; _worker(ability_job)
    current = reply(client.get(task_path)); _public(current)
    receipt = next(item for item in current['workflow_runtime']['receipts'] if item['execution_mode'] == 'registered_graph_ability')
    _typed_number(receipt['output']['value'], number)
    assert receipt['output']['operation'] == 'min' and receipt['output']['sample_count'] == 1
    assert receipt['output']['provenance'] == [{'entity_id': value['object']['id'], 'version': value['object']['version']}]
    with Session(engine()) as db:
        invocation = db.get(Invocation, receipt['invocation_id']); job = db.get(Job, ability_job)
        assert invocation.status == job.status == 'succeeded'
        _typed_number(invocation.data['output']['value'], number)
        assert digest(invocation.data['output']) == digest(receipt['output'])
        _typed_number(job.data['inputs']['records'][0]['attributes'][value['numeric_label']], number)
    current, first = _human(client, current, 'a', number)
    current, last = _human(client, current, 'c', number)
    assert current['completed'] and current['status'] == 'workflow_completed'
    goal, check_command = _check(value, current)
    assert goal['check']['evaluation']['ready_for_acceptance']
    results = goal['check']['evaluation']['tool_results']; assert len(results) == 1
    _typed_number(results[0]['output']['value'], number)
    acceptance = _acceptance(value, {'task': current, 'goal': goal})
    accepted = _raw(client, acceptance['path'], acceptance['body'])
    assert accepted['acceptance']['evaluation']['completed']
    assert accepted['acceptance']['physical_state_verified'] is False
    assert _raw(client, acceptance['path'], acceptance['body'])['acceptance'] == accepted['acceptance']
    with Session(engine()) as db:
        task = db.get(Task, current['id'])
        outcome_id = task.data['native_goal_data_receipt_id']
        graph_outcome_id = task.data['workflow_runtime']['data_receipt_id']
        for item in (task.data['native_goal_acceptance'], db.get(Memory, outcome_id).data['acceptance']):
            _typed_number(item['evaluation']['tool_results'][0]['output']['value'], number)
            assert digest(item) == task.data['native_goal_acceptance_hash']
        assert db.get(Memory, outcome_id).status == db.get(Memory, graph_outcome_id).status == 'candidate'
    proof = {'schema': 'typed-graph-goal-journey-v131', 'number_canonical': _canonical(number),
        'task_id': current['id'], 'task_path': task_path, 'goal_path': goal_path,
        'instance_id': value['release']['instance_id'], 'release_id': value['release']['id'],
        'creation': {'path': creation_path, 'body': body}, 'capability_id': value['capability']['id'],
        'invocation_id': receipt['invocation_id'], 'ability_job_id': ability_job,
        'goal_data_receipt_id': outcome_id, 'graph_data_receipt_id': graph_outcome_id,
        'acceptance': {**acceptance, 'response': accepted}, 'commands': value['commands'] + [
            {'path': creation_path, 'body': body}, first, last, check_command],
        'raw_graph_receipt': receipt, 'candidate_only': True, 'physical_state_verified': False}
    experience = run_experience_for_accepted(client, proof)
    assert experience['memory']['status'] == 'trusted'
    assert experience['new_jobs'] == experience['new_invocations'] == experience['new_cloud_calls'] == 0
    recovery_body = {'operation': 'accept', 'original_request': acceptance['body'],
        'expected_version': reply(client.get(goal_path))['version'], 'reason': '只核对原浮点依据验收已保存', 'action': 'probe'}
    recovered = _raw(client, goal_path + '/requests/recover', recovery_body)
    assert recovered['saved']
    readbacks = [{'path': path, 'response': reply(client.get(path))} for path in (
        task_path, goal_path, goal_path + '/export', task_path + '/experience', value['adoption_recovery_path'])]
    for item in readbacks: _public(item['response'])
    _typed_number(readbacks[0]['response']['graph_runtime']['inputs']['typed_number'], number)
    _typed_number(readbacks[1]['response']['acceptance']['evaluation']['tool_results'][0]['output']['value'], number)
    with Session(engine()) as db: assert set(db.scalars(select(CloudCall.id))) == cloud_before
    result = {**proof, 'experience': experience, 'readbacks': readbacks,
        'readonly_requests': [{'path': goal_path + '/requests/recover', 'body': recovery_body, 'response': recovered}],
        'new_cloud_calls': 0, 'frontend_numeric_input_verified': False}
    _canonical(result)  # Proof is JSON-safe without replacing the original floats.
    return result


@pytest.mark.parametrize('number', [pytest.param(-0.0, id='negative-zero'),
    pytest.param(1e21, id='large-exponent-float')])
def test_actual_typed_graph_minimum_formal_acceptance_and_human_experience(typed_graph_seed, tmp_path, number):
    run_typed_graph_goal_journey(typed_graph_seed, tmp_path, number)
