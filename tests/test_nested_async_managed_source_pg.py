"""Native nested engineering adoption through normal SIM source issuance.

The report/provider bodies are synthetic; the HTTP gateway fixture is the
actual loopback simulator. This test stops before acquisition or observation.
"""
import copy
import json
from pathlib import Path
from uuid import uuid4

from generic_agents import business_flow_nested_plan_v192 as plans
from generic_agents import device_gateway_sources_v270 as sources
from generic_agents import gateway
from generic_agents import report_business_flow_native_v193 as flow
from generic_agents import workflow_service_v195 as service
from generic_agents.contracts import Manifest, digest
from generic_agents.instance_compiler import identifier
from generic_agents.setup_automation import safe_policy
from generic_agents.worker import process_once
from tests.test_device_gateway_source_pg_v270 import OBJECTS, WEATHER_FIELDS, _runtime_counts
from tests.test_device_gateway_tools_v269 import managed, scene
from tests.test_device_gateway_v268 import BASE, nonce, read
from tests.test_instance_workspace import save
from tests.test_report_business_flow_native_v193 import answer as nested_answer
from tests.test_report_business_flow_protocol_v184 import answer as simple_plan
from tests.test_report_evidence_enum_postgres_v172 import (
    confirmed_catalogue, enum_scene, fence, full_source_scene, local_runtime, reading_scene, reply,
)
from tests.test_vertical_slice import publish


OP = 'resense_with_receipt'


def object_schema(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def register_tools(client):
    common = {'kind': 'tool', 'namespace': 'public_nested_sim', 'version': '1.0.0',
        'owner': '合成测试', 'description': '工程模拟的固定本地能力',
        'budget': {'max_attempts': 1, 'max_concurrency': 1}, 'data_policy': {'cloud_allowed': False}}
    resense = publish(client, Manifest(**common, name='resense_' + uuid4().hex[:8],
        allowed_agent_roles=['business'], input_schema=service.input_schema(OP),
        output_schema=service.output_schema(OP), execution={'adapter': 'workflow_service',
            'service_actor': 'execution_service', 'operation': OP, 'side_effect': True}).model_dump(mode='json'))
    extractor = publish(client, Manifest(**{**common, 'data_policy': {'cloud_allowed': False, 'deployment': 'local'}},
        name='extract_' + uuid4().hex[:8], allowed_agent_roles=['perception'],
        input_schema={'type': 'object'}, output_schema={'type': 'object', 'properties': {
            'attributes': {'type': 'object'}, 'anomalies': {'type': 'array'}},
            'required': ['attributes', 'anomalies']},
        execution={'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False},
        specification={'rules': []}).model_dump(mode='json'))
    return resense, extractor


def generate_nested_resense(client, report_source, patch):
    previous = gateway.httpx.Client

    class Transport(previous):
        def handle(self, request):
            if str(request.url) != flow.ENDPOINT:
                return super().handle(request)
            posted = json.loads(request.content)
            payload = json.loads(posted['messages'][-1]['content'])
            assert posted['model'] == flow.MODEL and payload['schema'] == flow.REQUEST_PAYLOAD_SCHEMA
            binding = payload['scope_binding']
            plan = simple_plan({'scope_binding': binding, 'payload': payload})
            plan['schema'] = plans.PLAN
            phase = plan['business']['flows'][0]['phases'][0]
            action = copy.deepcopy(phase['steps'][0])
            action.update(name='申请补采并等待原回执', actor='execution_service', role='工程补采岗位',
                inputs=['已批准采样策略', '原资料缺口'], outputs=['原回执处理说明'])
            phase['steps'].insert(0, action)
            return self.response(request, 'flow-v193-resense', nested_answer(plan, binding))

    patch.setattr(gateway.httpx, 'Client', Transport)
    manifest = json.loads((Path(__file__).parents[1] / 'examples/qwen.json').read_text())
    manifest.update(name='public_nested_resense_' + uuid4().hex[:8])
    manifest['execution'] = {'provider': flow.PROVIDER, 'model': flow.MODEL, 'max_tokens': 2048,
        'secret_ref': 'DEEPSEEK_API_KEY', 'report_evidence_output': {'schema': flow.OUTPUT_DECLARATION,
            'max_tokens': 65536, 'reduce_transport': flow.DELIVERY, 'endpoint': flow.ENDPOINT}}
    cap = publish(client, manifest)
    activity = nonce()
    body = {'plan_id': report_source['plan_id'], 'expected_plan_version': report_source['plan_version'],
        'plan_hash': report_source['plan_hash'], 'scope_id': report_source['scope_id'],
        'revision': report_source['revision'], 'model_id': cap['id'], 'note': '工程模拟补采应保留原回执',
        'activity_id': activity, 'generation_profile': 'flow-v193'}
    base = '/api/setup/report-scoped-design-attempts/v181'
    quote = reply(client.post(base + '/preview-flow-nested', json=body))
    started = reply(client.post(base + '/start-flow-nested', json={**body, 'request_id': activity,
        'consent': True, 'preview_hash': quote['preview_hash'], 'source_snapshot_hash': quote['source_snapshot_hash']}), 202)
    assert process_once(started['job_id'])
    attempt = reply(client.get(base + '/' + started['id']))
    assert attempt['status'] == 'review'
    return attempt


def engineering_qualification(client, attempt):
    path = '/api/setup/business-flow-reviews/attempts/' + attempt['id']
    original = reply(client.get(path))
    assert original['source_available'] is True
    saved = reply(client.post(path + '/save', json={'request_id': nonce(), 'expected_version': original['version'],
        'value': original['value'], 'note': '保留工程模拟流程原件'}))
    mode = 'engineering_simulation'
    preview = reply(client.get(path + '/nested-review-preview', params={'review_mode': mode}))
    assert preview['expected_version'] == saved['version']
    result = reply(client.post(path + '/qualify-nested', json={
        'schema': 'business-flow-nested-review-command-v195', 'request_id': nonce(),
        'expected_version': saved['version'], 'save_request_id': preview['save_request_id'],
        'value_hash': preview['value_hash'], 'ack_scope': True, 'ack_inferences': True,
        'reason': '仅供工程模拟，不表示完成了作者人工审查或取得生产资格', 'review_mode': mode}))
    assert result['status'] == 'engineering_reviewed' and result['simulation_only'] is True
    return result


def adoption_body(ctx, actor, name, resense, extractor):
    assert 'engineering_manual' in ctx['allowed_entry_modes'] and not ctx['controls']
    assert any(member['user_id'] == actor for member in ctx['members'])
    spec = resense['data']
    fields = {'original_material': {'type': 'string'}, **spec['input_schema']['properties']}
    references = {name: {'source': 'input', 'node_id': None, 'field': name, 'kind': 'string', 'nullable': False}
        for name in fields}
    contract = {'data_type': '已明确映射的工程模拟文本', 'unit': None}
    entries = {f'/entry_ports/{index}': field for index, field in enumerate(fields)}
    guards = dict.fromkeys(('start', 'precondition', 'stop', 'exception'))
    bindings = {'schema': 'business-flow-nested-execution-bindings-v195',
        'input_schema': object_schema(fields), 'references': references, 'predicates': {},
        'guards': guards, 'nodes': {}, 'entries': entries, 'conditions': [], 'loops': [],
        'contract_schemas': [{'contract': contract, 'kind': 'string', 'nullable': False}]}
    causal = {'schema': 'business-flow-nested-causal-bindings-v192',
        'source_plan_hash': ctx['source_plan_hash'], 'scope_binding_hash': ctx['scope_binding_hash'],
        'entry_ports': [{'path': path, 'flow_path': '/business/flows/0', 'contract': contract} for path in entries],
        'output_contracts': [], 'input_bindings': []}
    node_actions = {}
    for action in ctx['action_ports']:
        delegated = action['actor'] == 'execution_service'
        inputs = list(spec['input_schema']['properties']) if delegated else ['original_material']
        assert len(inputs) == len(action['input_ports'])
        outputs = ['result'] if delegated else [f'output_{item["ordinal"] + 1}' for item in action['output_ports']]
        assert len(outputs) == len(action['output_ports'])
        node = {'kind': 'ability' if delegated else 'human', 'actor_id': actor, 'guards': guards,
            'input_ports': [{'ordinal': port['ordinal'], 'reference': field}
                for port, field in zip(action['input_ports'], inputs)],
            'output_ports': [{'ordinal': port['ordinal'], 'field': field}
                for port, field in zip(action['output_ports'], outputs)]}
        if delegated:
            node.update(service_actor='execution_service', operation=OP, execution={
                'capability_id': resense['id'], 'capability_hash': resense['content_hash'],
                'release_version': resense['release_version'], 'adapter': 'workflow_service',
                'agent_role': 'business', 'max_attempts': 1, 'input_schema': spec['input_schema'],
                'output_schema': spec['output_schema'], 'input_bindings': {field: field for field in inputs}, 'arguments': {}})
            node_actions[action['graph_node_id']] = 'supplement'
        else:
            assert action['actor'] == 'human'
            node.update(role=action['role'], output_schema=object_schema({field: {'type': 'string'} for field in outputs}))
        bindings['nodes'][action['path']] = node
        causal['output_contracts'].extend({'path': port['path'], 'contract': contract} for port in action['output_ports'])
        causal['input_bindings'].extend({'input_path': port['path'], 'contract': contract,
            'sources': [next(path for path, name in entries.items() if name == field)]}
            for port, field in zip(action['input_ports'], inputs))
    assert len(node_actions) == 1
    return {'schema': 'business-flow-nested-execution-preview-command-v195',
        'qualification_id': ctx['qualification_id'], 'qualification_hash': ctx['qualification_hash'],
        'flow_index': 0, 'business_name': name, 'configuration_revision': ctx['configuration_revision'],
        'configuration_hash': ctx['configuration_hash'], 'execution_bindings': bindings,
        'causal_bindings': causal, 'role_bindings': dict.fromkeys(ctx['roles'], actor),
        'node_actions': node_actions, 'external_node_ids': [], 'entry_mode': 'engineering_manual',
        'observation_extractors': [{'capability_id': extractor['id'],
            'capability_hash': extractor['content_hash'], 'actor_id': actor}]}


def test_nested_engineering_resense_release_issues_real_sim_gateway_source(managed, enum_scene, monkeypatch, account):
    s = managed
    client = s.client
    assert enum_scene['client'] is client
    with monkeypatch.context() as transport:
        provider, report = confirmed_catalogue(enum_scene, transport, account, nested_flow=True)
        attempt = generate_nested_resense(client, report, transport)
        assert provider.requests[-1]['stage'] == 'flow-v193-resense'
    qualified = engineering_qualification(client, attempt)
    resense, extractor = register_tools(client)
    execution = '/api/setup/business-flow-executions'
    ctx = reply(client.get(execution + '/nested-context', params={'qualification_id': qualified['id'], 'flow_index': 0}))
    assert ctx['observation_extractor_options'] == [{
        'capability_id': extractor['id'], 'capability_hash': extractor['content_hash'],
        'release_version': extractor['release_version'], 'name': extractor['data']['name'],
    }]
    name = report['business_name']
    body = adoption_body(ctx, s.actor_id, name, resense, extractor)
    quote = reply(client.post(execution + '/nested-preview', json=body))
    adopted = reply(client.post(execution + '/nested-adopt', json={**body,
        'schema': 'business-flow-nested-execution-adopt-command-v195', 'request_id': nonce(),
        'preview_hash': quote['preview_hash'], 'consent': True, 'share_source': True}))
    assert adopted['simulation_only'] is True and adopted['can_execute'] is False
    current = reply(client.get('/api/setup/draft'))
    configuration = copy.deepcopy(current['configuration'])
    configuration['instanceProfile']['simulation_v176'] = True
    policy = safe_policy()
    policy['rules']['supplement'] = {'mode': '自动运行'}
    configuration.setdefault('automation', {'version': 1, 'policies': {}})['policies'][name] = policy
    publication = {'revision': save(client, configuration, review_automation=True),
        'objects': copy.deepcopy(OBJECTS), 'role_bindings': body['role_bindings']}
    checked = reply(client.post('/api/instances/check', json=publication))
    business = next(item for item in checked['businesses'] if item['name'] == name)
    assert not checked['general_issues'] and business['available'], business['issues']
    release = reply(client.post('/api/instances', json={**publication,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    published_business = next(item for item in release['snapshot']['data_requirements']['businesses'] if item['id'] == business['id'])
    declaration, = [item for item in published_business['outputs'] if item['kind'] == 'asynchronous_perception']
    assert declaration['node_id'] is None and declaration['capability_id'] == extractor['id']
    assert declaration['source_binding_hash'] == adopted['binding_hash']
    assert declaration['output_schema_hash'] == digest(extractor['data']['output_schema'])
    assert resense['id'] in [node['execution']['capability_id'] for node in quote['binding']['graph']['nodes']
        if node['kind'] == 'ability']
    assert extractor['id'] not in [node['execution']['capability_id'] for node in quote['binding']['graph']['nodes']
        if node['kind'] == 'ability']
    instance = next(item for item in reply(client.get('/api/instances')) if item['id'] == release['instance_id'])
    reply(client.post('/api/instances/' + instance['id'] + '/activate', json={'version': instance['version'],
        'release_id': release['id'], 'business_ids': [business['id']], 'confirm_workflow_scope': True}))
    environment = reply(client.post('/api/workspace/simulation-v176/environments', json={
        'request_id': nonce(), 'instance_id': instance['id'], 'release_id': release['id'],
        'note': '仅核验工程模拟天气来源登记，不代表现场数据', 'confirm_simulation': True}), 201)
    device = reply(client.get('/api/sensing/devices/' + s.grant['sensor_device_id']))
    reply(client.post('/api/sensing/devices/' + device['id'] + '/approve', json={
        'expected_version': device['version'], 'reason': '只确认工程模拟天气设备身份'}))
    connection = read(s)
    grant = next(row for row in connection['grants'] if row['id'] == s.grant['id'])
    before = _runtime_counts(s.workspace_id)
    issue = {'request_id': nonce(), 'expected_version': connection['version'], 'grant_id': grant['id'],
        'grant_expected_version': grant['version'], 'environment_id': environment['id'],
        'entity_type': identifier('object', OBJECTS[0]['name']),
        'links': {key: {'field_key': identifier('field', label), 'unit': unit}
            for key, (label, unit) in WEATHER_FIELDS.items()},
        'extractor_id': extractor['id'], 'extractor_hash': extractor['content_hash'],
        'confirm_simulation': True, 'reason': '明确登记工程模拟来源，保留原号，不启动采样'}
    url = BASE + '/' + s.connection_id + '/sources'
    issued = reply(client.post(url, json=issue))
    assert issued['operation'] == sources.OPERATION and issued['simulation'] is True
    assert issued['field_verified'] is False and issued['observation_created'] is False
    recovered = reply(client.get(BASE + '/requests/' + issue['request_id']))
    assert recovered['found'] is True and recovered['result'] == issued
    assert _runtime_counts(s.workspace_id) == before
