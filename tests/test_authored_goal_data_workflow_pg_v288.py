"""Real authenticated registry/adoption/recovery/publication, no model execution.

Actual owned loopback discovery and material/configuration fixtures are reused.
No Task, measurement, GoalEvaluation or paid answer is fabricated by adopting.
"""
import copy
import hashlib
import json
from uuid import uuid4
from sqlalchemy.orm import Session

from generic_agents import workflow_task_context_v196 as reader
from generic_agents import workflow_goal_data_route_runtime_v288 as local
from generic_agents import workflow_goal_evaluation_packet_v288 as packet
from generic_agents import business_goal_ready_runtime_v288 as reasoning
from generic_agents import authored_goal_data_workflow_contracts_v288 as commands
from generic_agents.contracts import Manifest
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import identifier
from generic_agents import models as m
from tests.test_authored_decision_workflow_pg_v286 import prepared_decision, counts
from tests.test_authored_service_workflow_pg_v270 import good, publish
from tests.test_authored_goal_data_workflow_v288 import policy
from tests.test_device_gateway_v268 import scene
from tests.test_device_gateway_tools_v269 import managed
from tests.test_instance_workspace import OBJECTS


def tool(module, role, execution, *, dependencies=None, cloud=False):
    return Manifest(kind='tool', namespace='goal_data_pg_v288', name='tool_' + uuid4().hex[:12],
        version='1.0.0', owner='独立工程核验账号', description='独立资料准备能力登记；不授予采集、出域或验收权限',
        allowed_agent_roles=[role], input_schema=module.input_schema(), output_schema=module.output_schema(),
        execution=execution, specification={'schema': module.CONTRACT,
            **({'instruction': '根据已纳入的真实资料核对业务方案；保留模拟和不确定性，不宣称现场事实。'} if cloud else {})},
        data_policy={'cloud_allowed': cloud}, dependencies=dependencies or [],
        budget={'max_attempts': 1, 'max_concurrency': 1}).model_dump(mode='json')


def test_real_six_port_adoption_recovers_original_request_and_publishes_fixed_preparation_policy(managed):
    s = managed
    original, old_pins, model, candidate, extractor, _material = prepared_decision(s)
    field_keys = [identifier('field', label) for label in ('气温', '相对湿度')]
    for item in original['candidate_specs']:
        for pin in item['capability_pins']:
            if pin['delivery'] == 'requested_observation':
                pin['ports'] = [{'field': field, 'pointer': '/requested_observation/' + field}
                                for field in field_keys]
    listed = good(s.client.get('/api/capabilities'))
    context = [row for row in listed if row['data']['execution'].get('handler') == reader.HANDLER]
    assert len(context) == 1
    plan = publish(s.client, tool(local, 'business', {'adapter': 'workflow_service',
        'service_actor': 'execution_service', 'operation': local.OP, 'side_effect': True}))
    evaluation = publish(s.client, tool(packet, 'data', {'adapter': 'workflow_service',
        'service_actor': 'reader_service', 'operation': packet.OP, 'side_effect': True}))
    reason = publish(s.client, tool(reasoning, 'business', {'adapter': reasoning.ADAPTER,
        'model_profile_id': model['id'], 'model_profile_hash': model['content_hash'], 'side_effect': False},
        dependencies=[model['id']], cloud=True))
    pins = {'context_reader': context[0], 'local_prepare': plan, 'resense': old_pins['resense'],
        'evaluation_packet': evaluation, 'packet': old_pins['packet'], 'reasoning': reason}
    body = {**original, 'schema': 'authored-goal-data-workflow-preview-command-v288',
        'preparation_policy': policy(), 'node_actions': commands.NODE_ACTIONS,
        'external_node_ids': commands.EXTERNAL_NODE_IDS,
        'tool_bindings': {name: {'capability_id': row['id'], 'capability_hash': row['content_hash'],
            'actor_id': s.actor_id} for name, row in pins.items()}}
    before = counts(s.workspace_id)
    preview = good(s.client.post(commands.PREFIX + '/preview', json=body))
    request = {**body, 'schema': 'authored-goal-data-workflow-adopt-command-v288',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True}
    raw = (' ' + json.dumps(request, ensure_ascii=False) + '\n').encode()
    response = s.client.post(commands.PATH, content=raw)
    assert response.status_code == 201, response.text[:700]
    del response
    recovered = good(s.client.get(commands.PREFIX + '/requests/' + request['request_id'],
        params={'expected_hash': hashlib.sha256(raw).hexdigest()}))
    assert recovered['found'] and recovered['body'] == request
    assert recovered['schema'] == 'authored-goal-data-workflow-recovery-v288'
    adopted = recovered['result']
    assert good(s.client.post(commands.PATH, content=raw), 201) == adopted
    view = good(s.client.get(commands.PREFIX + '/adoptions/' + adopted['id']))
    assert view['graph'] == preview['binding']['graph'] and not view['can_execute']
    current = good(s.client.get('/api/setup/draft'))
    objects = copy.deepcopy(OBJECTS)
    objects[0]['fields'].extend([{'label': '气温', 'kind': '数值', 'required': False, 'unit': '℃'},
                                 {'label': '相对湿度', 'kind': '数值', 'required': False, 'unit': '%'}])
    publication = {'revision': current['revision'], 'objects': objects,
        'role_bindings': {key: actor['user_id'] for key, actor in preview['binding']['role_bindings'].items()}}
    checked = good(s.client.post('/api/instances/check', json=publication))
    assert not checked['general_issues'] and all(row['available'] for row in checked['businesses'])
    release = good(s.client.post('/api/instances', json={**publication,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    actual = release['snapshot']['semantic_runtime_plan']['graph_adoptions'][0]['binding']
    assert actual == preview['binding'] and actual['preparation_policy'] == body['preparation_policy']
    assert actual['graph']['entry'] == 'context_reader'
    declarations = [output for business in release['snapshot']['data_requirements']['businesses']
        for output in business['outputs'] if output.get('kind') == 'asynchronous_perception']
    assert len(declarations) == 1 and declarations[0]['capability_id'] == extractor['id']
    assert declarations[0]['capability_hash'] == extractor['content_hash']
    assert declarations[0]['release_version'] == extractor['release_version']
    assert declarations[0]['output_schema'] == extractor['data']['output_schema']
    assert declarations[0]['output_schema_hash'] == digest(extractor['data']['output_schema'])
    assert declarations[0]['source_binding_hash'] == actual['binding_hash']
    assert declarations[0]['node_id'] is None and declarations[0]['status'] == 'candidate'
    assert declarations[0]['declaration_kind'] == 'registered_observation_extractor'
    ability_caps = {node['execution']['capability_id'] for node in actual['graph']['nodes'] if node['kind'] == 'ability'}
    assert candidate['id'] not in ability_caps and extractor['id'] not in ability_caps
    with Session(engine()) as db:
        package = db.get(m.Capability, release['package_id'])
        assert package.content_hash == package.validated_hash == digest(package.data)
        assert ability_caps | {candidate['id'], extractor['id']} <= set(package.data['dependencies'])
    assert counts(s.workspace_id) == before
