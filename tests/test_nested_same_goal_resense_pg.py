"""Recursive V193 binding with a real same-Task context producer.

The report design response is synthetic. Source discovery and weather
acquisition use the managed fixture's actual isolated simulator.
"""
import copy
from uuid import uuid4

from generic_agents import workflow_service_context_v196 as resense
from generic_agents import workflow_task_context_v196 as reader
from generic_agents.contracts import Manifest
from tests import test_nested_async_managed_source_pg as original
from tests.test_nested_async_managed_source_pg import (
    confirmed_catalogue, enum_scene, fence, full_source_scene, local_runtime,
    managed, reading_scene, scene,
)
from tests.test_vertical_slice import publish


def _generated_answer(render, plan, binding):
    plan = copy.deepcopy(plan)
    steps = plan['business']['flows'][0]['phases'][0]['steps']
    assert steps[0]['actor'] == 'execution_service'
    producer = copy.deepcopy(steps[0])
    producer.update(name='读取本任务原目标与获批监测策略', actor='data_agent',
        role='工程资料岗位', inputs=['已批准采样策略'], outputs=['同任务补采上下文'])
    steps[0]['inputs'] = ['同任务补采上下文']
    steps.insert(0, producer)
    return render(plan, binding)


def _tool(client, module, role, execution):
    return publish(client, Manifest(kind='tool', namespace='nested_same_goal',
        name='tool_' + uuid4().hex[:16], version=getattr(module, 'RELEASE_VERSION', '1.0.0'), owner='隔离工程测试',
        description='递归流程原目标资料和补采', allowed_agent_roles=[role],
        input_schema=module.input_schema(), output_schema=module.output_schema(),
        execution=execution, data_policy={'cloud_allowed': False},
        budget={'max_attempts': 1, 'max_concurrency': 1}).model_dump(mode='json'))


def _execution(cap, role, input_bindings):
    data = cap['data']
    return {'capability_id': cap['id'], 'capability_hash': cap['content_hash'],
        'release_version': cap['release_version'], 'adapter': data['execution']['adapter'],
        'agent_role': role, 'max_attempts': 1, 'input_schema': data['input_schema'],
        'output_schema': data['output_schema'], 'input_bindings': input_bindings, 'arguments': {}}


def _adoption(ctx, actor, name, reader_cap, service_cap, extractor):
    policy = {'data_type': '已批准采样策略', 'unit': None}
    material = {'data_type': '已明确映射的工程模拟文本', 'unit': None}
    context = {'data_type': '同任务补采上下文', 'unit': None}
    producer, = [a for a in ctx['action_ports'] if a['actor'] == 'data_agent']
    consumer, = [a for a in ctx['action_ports'] if a['actor'] == 'execution_service']
    references = {
        'policy_pin': {'source': 'input', 'node_id': None, 'field': 'policy_pin',
            'kind': 'object', 'nullable': True},
        'original_material': {'source': 'input', 'node_id': None, 'field': 'original_material',
            'kind': 'string', 'nullable': False},
        'selected_context': {'schema': 'guarded-port-projection-v198', 'source': 'port_projection',
            'kind': 'object', 'nullable': False, 'unit': None, 'consumer_node_id': consumer['path'],
            'origin': {'node_id': producer['path'], 'port_ordinal': 0, 'output_pointer': '/resense_context'},
            'projection': {'op': 'select', 'pointer': '', 'require_non_null': True}},
    }
    entries = {'/entry_ports/0': 'policy_pin', '/entry_ports/1': 'original_material'}
    entry_contracts = [{'path': path, 'flow_path': '/business/flows/0',
        'contract': policy if field == 'policy_pin' else material} for path, field in entries.items()]
    bindings = {'schema': 'business-flow-nested-execution-bindings-v195',
        'input_schema': original.object_schema({'policy_pin': reader.input_schema()['properties']['policy_pin'],
            'original_material': {'type': 'string'}}),
        'references': references, 'predicates': {}, 'guards': dict.fromkeys(('start', 'precondition', 'stop', 'exception')),
        'nodes': {}, 'entries': entries, 'conditions': [], 'loops': [],
        'contract_schemas': [{'contract': contract, 'kind': kind, 'nullable': nullable}
            for contract, kind, nullable in ((policy, 'object', True), (material, 'string', False),
                (context, 'object', True))]}
    causal = {'schema': 'business-flow-nested-causal-bindings-v192',
        'source_plan_hash': ctx['source_plan_hash'], 'scope_binding_hash': ctx['scope_binding_hash'],
        'entry_ports': entry_contracts, 'output_contracts': [], 'input_bindings': []}
    actions = {}
    for action in ctx['action_ports']:
        role = action['actor']
        node = {'kind': 'ability' if role != 'human' else 'human', 'actor_id': actor,
            'guards': dict.fromkeys(('start', 'precondition', 'stop', 'exception'))}
        if role == 'data_agent':
            inputs, outputs = ['policy_pin'], ['resense_context']
            node['execution'] = _execution(reader_cap, 'data', {'policy_pin': 'policy_pin'})
            actions[action['graph_node_id']] = 'data'
        elif role == 'execution_service':
            inputs, outputs = ['selected_context'], ['result']
            node.update(service_actor=role, operation=resense.OP,
                execution=_execution(service_cap, 'business', {'context': 'selected_context'}))
            actions[action['graph_node_id']] = 'supplement'
        else:
            assert role == 'human'
            inputs = ['original_material']
            outputs = [f'output_{row["ordinal"] + 1}' for row in action['output_ports']]
            node.update(role=action['role'],
                output_schema=original.object_schema({field: {'type': 'string'} for field in outputs}))
        assert len(inputs) == len(action['input_ports']) and len(outputs) == len(action['output_ports'])
        node['input_ports'] = [{'ordinal': port['ordinal'], 'reference': field}
            for port, field in zip(action['input_ports'], inputs)]
        node['output_ports'] = [{'ordinal': port['ordinal'], 'field': field}
            for port, field in zip(action['output_ports'], outputs)]
        bindings['nodes'][action['path']] = node
        causal['output_contracts'].extend({'path': port['path'],
            'contract': context if role == 'data_agent' else material} for port in action['output_ports'])
        for port, field in zip(action['input_ports'], inputs):
            if field == 'selected_context':
                source, contract = producer['output_ports'][0]['path'], context
            else:
                source = next(path for path, key in entries.items() if key == field)
                contract = policy if field == 'policy_pin' else material
            causal['input_bindings'].append({'input_path': port['path'], 'contract': contract, 'sources': [source]})
    return {'schema': 'business-flow-nested-execution-preview-command-v195',
        'qualification_id': ctx['qualification_id'], 'qualification_hash': ctx['qualification_hash'],
        'flow_index': 0, 'business_name': name, 'configuration_revision': ctx['configuration_revision'],
        'configuration_hash': ctx['configuration_hash'], 'execution_bindings': bindings,
        'causal_bindings': causal, 'role_bindings': dict.fromkeys(ctx['roles'], actor),
        'node_actions': actions, 'external_node_ids': [], 'entry_mode': 'engineering_manual',
        'observation_extractors': [{'capability_id': extractor['id'],
            'capability_hash': extractor['content_hash'], 'actor_id': actor}]}


def test_recursive_publication_binds_same_task_reader_output_instead_of_gap_entry(
        managed, enum_scene, monkeypatch, account):
    s = managed
    client = s.client
    assert enum_scene['client'] is client
    with monkeypatch.context() as transport:
        provider, report = confirmed_catalogue(enum_scene, transport, account, nested_flow=True)
        render = original.nested_answer
        transport.setattr(original, 'nested_answer', lambda plan, binding: _generated_answer(render, plan, binding))
        attempt = original.generate_nested_resense(client, report, transport)
        assert provider.requests[-1]['stage'] == 'flow-v193-resense'
    qualified = original.engineering_qualification(client, attempt)
    _legacy, extractor = original.register_tools(client)
    producer = _tool(client, reader, 'data',
        {'adapter': 'builtin', 'handler': reader.HANDLER, 'side_effect': False})
    service = _tool(client, resense, 'business',
        {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': resense.OP, 'side_effect': True})
    path = '/api/setup/business-flow-executions'
    ctx = original.reply(client.get(path + '/nested-context',
        params={'qualification_id': qualified['id'], 'flow_index': 0}))
    name = report['business_name']
    body = _adoption(ctx, s.actor_id, name, producer, service, extractor)
    quote = original.reply(client.post(path + '/nested-preview', json=body))
    adopted = original.reply(client.post(path + '/nested-adopt', json={**body,
        'schema': 'business-flow-nested-execution-adopt-command-v195', 'request_id': original.nonce(),
        'preview_hash': quote['preview_hash'], 'consent': True, 'share_source': True}))
    graph = quote['binding']['graph']
    assert set(graph['input_schema']['properties']) == {'policy_pin', 'original_material'}
    nodes = {node['execution']['capability_id']: node for node in graph['nodes'] if node['kind'] == 'ability'}
    consumer = nodes[service['id']]
    ref = graph['references'][consumer['execution']['input_bindings']['context']]
    assert ref['origin']['node_id'] == nodes[producer['id']]['id']
    assert ref['origin']['output_pointer'] == '/resense_context'
    assert ref['projection'] == {'op': 'select', 'pointer': '', 'require_non_null': True}
    current = original.reply(client.get('/api/setup/draft'))
    configuration = copy.deepcopy(current['configuration'])
    configuration['instanceProfile']['simulation_v176'] = True
    policy = original.safe_policy()
    for action in ('data', 'supplement'):
        policy['rules'][action] = {'mode': '自动运行'}
    configuration.setdefault('automation', {'version': 1, 'policies': {}})['policies'][name] = policy
    publication = {'revision': original.save(client, configuration, review_automation=True),
        'objects': copy.deepcopy(original.OBJECTS), 'role_bindings': body['role_bindings']}
    checked = original.reply(client.post('/api/instances/check', json=publication))
    business = next(item for item in checked['businesses'] if item['name'] == name)
    assert not checked['general_issues'] and business['available'], business['issues']
    release = original.reply(client.post('/api/instances', json={**publication,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    declared = next(item for item in release['snapshot']['data_requirements']['businesses'] if item['id'] == business['id'])
    output, = [item for item in declared['outputs'] if item['kind'] == 'asynchronous_perception']
    assert output['source_binding_hash'] == adopted['binding_hash']
    assert output['capability_id'] == extractor['id']
