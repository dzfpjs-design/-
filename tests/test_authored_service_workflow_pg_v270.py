"""Normal registry/confirmation/adoption/publication, without a paid model call.

The managed target fixture has a real loopback supplier and saved discovery;
this test proves publication and recovery, not execution or field accuracy.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import business_reasoning_v197 as reasoning
from generic_agents import workflow_task_context_v196 as reader
from generic_agents import workflow_service_context_v196 as resense
from generic_agents import workflow_registered_tool_plan_v201 as planner
from generic_agents import authored_service_workflow_contracts_v270 as contracts
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents.worker import process_once
from tests.test_device_gateway_v268 import scene
from tests.test_device_gateway_tools_v269 import managed
from tests.test_authored_workflow_pg_v208 import published_packet
from tests.test_instance_workspace import configuration, save, OBJECTS
from tests.test_business_reasoning_v197 import manifest as model_manifest
from tests.test_workflow_registered_tool_plan_v201 import wrapper_manifest
from tests.test_setup import upload

BASE = contracts.PREFIX


def good(response, status=200):
    assert response.status_code == status, response.text[:500]
    return response.json()


def publish(client, value):
    registered = good(client.post('/api/capabilities', json=value), 201)
    checked = good(client.post('/api/capabilities/' + registered['id'] + '/validate',
        json={'expected_version': registered['version'], 'reason': '核对真实登记合同'}))
    return good(client.post('/api/capabilities/' + registered['id'] + '/publish',
        json={'expected_version': checked['version'], 'reason': '用于明确确认的模拟软件链'}))


def prepared(s):
    client = s.client
    packet = published_packet(client)
    model = publish(client, model_manifest())
    common = {'contract_version': '1.0', 'kind': 'tool', 'namespace': 'service_contract_v270',
        'version': '1.0.0', 'owner': '集成核验岗位', 'description': '已登记固定端口，不代表执行完成',
        'data_policy': {'cloud_allowed': False}, 'dependencies': [],
        'budget': {'max_attempts': 1, 'max_concurrency': 1}, 'specification': {}}
    reason = publish(client, {**common, 'name': 'compare_evidence', 'allowed_agent_roles': ['business'],
        'input_schema': reasoning.input_schema(), 'output_schema': reasoning.output_schema(),
        'execution': {'adapter': reasoning.ADAPTER, 'model_profile_id': model['id'],
            'model_profile_hash': model['content_hash'], 'side_effect': False},
        'dependencies': [model['id']], 'data_policy': {'cloud_allowed': True},
        'specification': {'schema': reasoning.CONTRACT, 'instruction': '只比较已选择原件支持的候选；缺少依据应明确不足。'}})
    target = next(row for row in good(client.get('/api/capabilities')) if row['id'] == s.capability_id)
    plan_manifest = wrapper_manifest([NS(id=target['id'], content_hash=target['content_hash'], release_version=target['release_version'])])
    plan_manifest['data_policy']['external_allowed'] = True
    plan = publish(client, plan_manifest)
    context = publish(client, {**common, 'name': 'read_actual_context', 'allowed_agent_roles': ['data'],
        'input_schema': reader.input_schema(), 'output_schema': reader.output_schema(),
        'execution': {'adapter': 'builtin', 'handler': reader.HANDLER, 'side_effect': False}})
    supplement = publish(client, {**common, 'name': 'supplement_actual_context', 'version': resense.RELEASE_VERSION,
        'allowed_agent_roles': ['business'],
        'input_schema': resense.input_schema(), 'output_schema': resense.output_schema(),
        'execution': {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': resense.OP, 'side_effect': True}})
    extractor = publish(client, {**common, 'name': 'extract_local_numbers', 'allowed_agent_roles': ['perception'],
        'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object', 'properties': {
            'attributes': {'type': 'object'}, 'anomalies': {'type': 'array'}},
            'required': ['attributes', 'anomalies']},
        'execution': {'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False},
        'data_policy': {'cloud_allowed': False, 'deployment': 'local'}, 'specification': {'rules': []}})
    raw = '公开软件核验要求：采用设备参数模拟采样，保留原始回执；不代表现场天气。'.encode()
    material = upload(client, raw, '普通需求材料.txt')
    assert process_once(job_id=material['job_id'], deliver=False)
    material = good(client.get('/api/setup/materials/' + material['id']))
    assert material['status'] == 'parsed'
    block = material['blocks'][0]
    cfg = configuration()
    cfg['materials'] = [material]
    cfg['flows']['资料核验']['citations'] = [{'material_id': material['id'], 'block_id': block['id'], 'quote': block['text']}]
    policy = cfg['automation']['policies']['资料核验']
    policy.update(triggers=['人工发起'])
    for action in ('data', 'write', 'supplement'):
        policy['rules'][action] = {'mode': '自动运行'}
    cfg['flows']['资料核验']['confirmedSignature'] = json.dumps(signature_value(cfg, cfg['flows']['资料核验']), ensure_ascii=False)
    save(client, cfg, review_automation=True)
    current = good(client.get('/api/setup/draft'))
    pins = {'packet': packet, 'reasoning': reason, 'planner': plan, 'context_reader': context, 'resense': supplement}
    actor = s.actor_id
    command = {'schema': 'authored-service-workflow-preview-command-v270',
        'configuration_revision': current['revision'], 'configuration_hash': digest(current['configuration']),
        'business_name': '资料核验', 'tool_bindings': {key: {'capability_id': value['id'],
            'capability_hash': value['content_hash'], 'actor_id': actor} for key, value in pins.items()},
        'candidate_proposals': ['按本次已批准范围提交模拟采样，并保留原号回执。'],
        'candidate_plans': [{'candidate_ref': 'candidate_1', 'steps': [{'capability_id': target['id'],
            'capability_hash': target['content_hash'], 'agent_role': 'business', 'arguments': {
                'device_id': target['data']['specification']['device_gateway_binding']['device_external_id'],
                'action': 'read_telemetry', 'expected_revision': None, 'arguments': {}}, 'allow_external_inputs': True}]}],
        'observation_extractors': [{'capability_id': extractor['id'], 'capability_hash': extractor['content_hash'], 'actor_id': actor}],
        'role_bindings': {'核验负责人': actor}, 'node_actions': contracts.NODE_ACTIONS,
        'external_node_ids': contracts.EXTERNAL_NODE_IDS, 'prior_origin': None, 'simulation_only': True}
    return command, extractor, material


def counts(workspace):
    with Session(engine()) as db:
        return tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace))
            for model in (m.Task, m.Invocation, m.CloudCall))


def test_normal_txt_adoption_recovers_exact_command_and_publishes_async_declarations(managed):
    s = managed
    body, extractor, material = prepared(s)
    before = counts(s.workspace_id)
    preview = good(s.client.post(BASE + '/preview', json=body))
    assert preview['binding']['source_materials'][0]['id'] == material['id']
    command = {**body, 'schema': 'authored-service-workflow-adopt-command-v270',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True}
    raw = ('  ' + json.dumps(command, ensure_ascii=False) + '\n').encode()
    adopted = good(s.client.post(BASE + '/adopt', content=raw), 201)
    assert counts(s.workspace_id) == before
    recovered = good(s.client.get(BASE + '/requests/' + command['request_id'],
        params={'expected_hash': hashlib.sha256(raw).hexdigest()}))
    assert recovered['found'] and recovered['result'] == adopted and recovered['body'] == command
    assert recovered['schema'] == 'authored-service-workflow-recovery-v270'
    view = good(s.client.get(BASE + '/adoptions/' + adopted['id']))
    assert view['schema'] == 'authored-service-workflow-adoption-view-v270'
    current = good(s.client.get('/api/setup/draft'))
    publish_body = {'revision': current['revision'], 'objects': OBJECTS,
        'role_bindings': {key: actor['user_id'] for key, actor in preview['binding']['role_bindings'].items()}}
    checked = good(s.client.post('/api/instances/check', json=publish_body))
    assert not checked['general_issues'] and all(row['available'] for row in checked['businesses']), [
        (row['name'], row['issues']) for row in checked['businesses']]
    release = good(s.client.post('/api/instances', json={**publish_body,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    outputs = [row for business in release['snapshot']['data_requirements']['businesses'] for row in business['outputs']
        if row.get('kind') == 'asynchronous_perception']
    assert len(outputs) == 1
    assert outputs[0]['capability_id'] == extractor['id'] and outputs[0]['node_id'] is None
    assert outputs[0]['source_binding_hash'] == preview['binding']['binding_hash']
    graph = release['snapshot']['semantic_runtime_plan']['graph_adoptions'][0]['binding']['graph']
    assert extractor['id'] not in [node['execution']['capability_id'] for node in graph['nodes'] if node['kind'] == 'ability']
    assert counts(s.workspace_id) == before


def test_changed_material_cannot_renew_adoption_or_publish(managed):
    s = managed
    body, _, material = prepared(s)
    preview = good(s.client.post(BASE + '/preview', json=body))
    with Session(engine()) as db:
        row = db.get(m.SetupMaterial, material['id'])
        row.archived = True; row.version += 1
        db.commit()
    response = s.client.post(BASE + '/adopt', json={**body,
        'schema': 'authored-service-workflow-adopt-command-v270', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'consent': True})
    assert response.status_code == 409
    assert counts(s.workspace_id) == (0, 0, 0)
