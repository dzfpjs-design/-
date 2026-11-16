"""Cold command/recipe invariants only; no DB fixture or live authority claim.

The examples below are pure schema inputs. They do not create a producer,
adoption, capability, receipt or model response and cannot authorize execution.
"""
import copy
import hashlib
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import authored_service_workflow_contracts_v270 as commands
from generic_agents import authored_service_workflow_graph_v270 as recipe
from generic_agents import authored_service_workflow_store_v270 as store
from generic_agents.contracts import digest


def command():
    actor = str(uuid4())
    target = str(uuid4())
    return {'schema': 'authored-service-workflow-preview-command-v270',
        'configuration_revision': 1, 'configuration_hash': 'a' * 64,
        'business_name': '天气采样与资料核查', 'tool_bindings': {key: {
            'capability_id': str(uuid4()), 'capability_hash': 'b' * 64, 'actor_id': actor}
            for key in commands.OPERATIONS}, 'candidate_proposals': ['提交已批准的模拟天气采样请求'],
        'candidate_plans': [{'candidate_ref': 'candidate_1', 'steps': [{
            'capability_id': target, 'capability_hash': 'c' * 64, 'agent_role': 'business',
            'arguments': {}, 'allow_external_inputs': True}]}], 'observation_extractors': [],
        'role_bindings': {'原人工复核人': actor}, 'node_actions': copy.deepcopy(commands.NODE_ACTIONS),
        'external_node_ids': list(commands.EXTERNAL_NODE_IDS), 'prior_origin': None, 'simulation_only': True}


def wire(value):
    return ('  ' + json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8')


def flow():
    return {'trigger': '人工选择批准范围发起', 'output': '本次核查记录', 'steps': [{
        'id': 'review', 'agent': '人工确认', 'name': '核查实际资料', 'role': '原人工复核人',
        'output': '核查意见', 'nextId': 'end'}]}


def graph_inputs(body):
    selected = body['tool_bindings']
    adapters = {'packet': 'runtime_document_packet', 'reasoning': 'business_reasoning',
        'planner': 'workflow_service', 'context_reader': 'builtin', 'resense': 'workflow_service'}
    pins = {}
    for key, pin in selected.items():
        inputs, outputs = recipe._schemas(key)
        pins[key] = {'capability_id': pin['capability_id'], 'capability_hash': pin['capability_hash'],
            'agent_role': recipe.ROLES[key], 'input_bindings': {}, 'arguments': {},
            'input_schema': inputs, 'output_schema': outputs, 'adapter': adapters[key],
            'release_version': '1.0.0', 'max_attempts': 1}
    actor = body['role_bindings']['原人工复核人']
    roles = {'原人工复核人': {'user_id': actor, 'username': 'reviewer'}}
    for key, pin in selected.items():
        roles[recipe.tool_role(body['business_name'], key)] = {'user_id': pin['actor_id'], 'username': 'executor'}
    actors = {key: pin['actor_id'] for key, pin in selected.items()}
    return roles, pins, actors


def graph(body):
    roles, pins, actors = graph_inputs(body)
    return recipe.build_graph(flow(), roles, pins, actors, business_name=body['business_name'],
        candidate_proposals=body['candidate_proposals'], candidate_plans=body['candidate_plans'])


def cold_binding(body):
    """A pure typed value for tamper tests; never a saved-authority substitute."""
    built = graph(body)
    roles, _, _ = graph_inputs(body)
    targets = {step['capability_id']: {'capability_id': step['capability_id'],
        'capability_hash': step['capability_hash'], 'release_version': '1.0.0'}
        for plan in body['candidate_plans'] for step in plan['steps']}
    return store.common._seal({'schema': commands.BINDING, 'authorship': 'human_authored',
        'model_generated': False, 'semantic_quality_verified': False, 'simulation_only': True,
        'template': commands.TEMPLATE, 'configuration_id': str(uuid4()),
        'configuration_revision': body['configuration_revision'], 'configuration_hash': body['configuration_hash'],
        'business_name': body['business_name'], 'prior_origin': None, 'flow': flow(),
        'flow_signature': {}, 'flow_hash': digest(flow()), 'role_bindings': roles,
        'tool_bindings': body['tool_bindings'], 'candidate_proposals': body['candidate_proposals'],
        'candidate_plans': body['candidate_plans'], 'candidate_tool_pins': targets,
        'observation_extractors': [], 'observation_extractor_pins': {}, 'graph': built['graph'],
        'step_mapping': built['step_mapping'], 'capability_pins': {node['id']: node['execution']
            for node in built['graph']['nodes'] if node['kind'] == 'ability'},
        'service_delegations': built['service_delegations'], 'node_actions': body['node_actions'],
        'external_node_ids': body['external_node_ids'], 'event_source_bindings': {},
        'automation': {}, 'source_materials': []})


def reseal(value):
    value.pop('binding_hash', None)
    return store.common._seal(value)


def test_raw_adoption_preserves_exact_body_and_normalizes_only_preview_schema():
    value = command()
    adopted = {**value, 'schema': 'authored-service-workflow-adopt-command-v270',
        'request_id': str(uuid4()), 'preview_hash': 'd' * 64, 'consent': True}
    raw = wire(adopted)
    parsed, exact, hashed = commands.parse(raw, commands.Adopt)
    assert exact.encode('utf-8') == raw and hashed == hashlib.sha256(raw).hexdigest()
    assert commands.preview_body(parsed) == value


@pytest.mark.parametrize('field,value', [('simulation_only', 1), ('simulation_only', False),
    ('simulation_only', 'true'), ('graph', {}), ('reasoning_result', {}), ('context', {}),
    ('configuration_revision', True), ('external_node_ids', ['reasoning'])])
def test_client_cannot_inject_authority_graph_model_result_or_context(field, value):
    body = command()
    body[field] = value
    with pytest.raises(HTTPException):
        commands.parse(wire(body), commands.Preview)


@pytest.mark.parametrize('value', [1, False, 'true', None])
def test_adoption_requires_actual_boolean_consent(value):
    body = {**command(), 'schema': 'authored-service-workflow-adopt-command-v270',
        'request_id': str(uuid4()), 'preview_hash': 'd' * 64, 'consent': value}
    with pytest.raises(HTTPException):
        commands.parse(wire(body), commands.Adopt)


def test_candidate_plans_must_match_complete_ordered_reasoning_candidates():
    body = command()
    body['candidate_plans'][0]['candidate_ref'] = 'candidate_2'
    with pytest.raises(HTTPException):
        commands.parse(wire(body), commands.Preview)
    body = command()
    body['candidate_plans'][0]['steps'][0]['allow_external_inputs'] = 1
    with pytest.raises(HTTPException):
        commands.parse(wire(body), commands.Preview)


def test_graph_uses_actual_reasoning_and_reader_products_in_fixed_order():
    body = command()
    built = graph(body)
    nodes = built['graph']['nodes']
    assert [node['id'] for node in nodes[:5]] == list(commands.OPERATIONS)
    planner = next(node for node in nodes if node['id'] == 'planner')
    assert planner['execution']['input_bindings'] == {'reasoning_result': 'reasoning_result'}
    assert planner['execution']['arguments'] == {'candidate_plans': body['candidate_plans']}
    refs = built['graph']['references']
    assert refs['reasoning_result']['origin']['node_id'] == 'reasoning'
    assert refs['reasoning_result']['projection'] == {'op': 'copy', 'pointer': ''}
    assert refs['reader_context']['origin']['node_id'] == 'context_reader'
    assert refs['reader_context']['projection'] == {'op': 'select', 'pointer': '/resense_context', 'require_non_null': True}
    assert refs['policy_pin']['source'] == 'input' and refs['policy_pin']['nullable'] is True
    assert set(built['service_delegations']) == {'planner', 'resense'}


def test_graph_cannot_replace_fixed_port_schema_or_runtime_context_with_literals():
    body = command()
    roles, pins, actors = graph_inputs(body)
    pins['resense']['arguments'] = {'context': {}}
    with pytest.raises(ValueError):
        recipe.build_graph(flow(), roles, pins, actors, business_name=body['business_name'],
            candidate_proposals=body['candidate_proposals'], candidate_plans=body['candidate_plans'])
    roles, pins, actors = graph_inputs(body)
    pins['reasoning']['adapter'] = 'builtin'
    with pytest.raises(ValueError):
        recipe.build_graph(flow(), roles, pins, actors, business_name=body['business_name'],
            candidate_proposals=body['candidate_proposals'], candidate_plans=body['candidate_plans'])


@pytest.mark.parametrize('change', ['delegation', 'producer', 'simulation', 'observation_node'])
def test_recomputed_self_hash_cannot_change_service_family_producers_or_declarations(change):
    binding = cold_binding(command())
    assert store.check_binding(binding) == binding
    if change == 'delegation':
        binding['service_delegations']['planner']['operation'] = 'resense_from_context'
    elif change == 'producer':
        binding['graph']['references']['reasoning_result']['origin']['node_id'] = 'packet'
    elif change == 'simulation':
        binding['simulation_only'] = 1
    else:
        binding['observation_extractor_pins'][str(uuid4())] = copy.deepcopy(binding['capability_pins']['packet'])
    with pytest.raises(HTTPException):
        store.check_binding(reseal(binding))


def test_declarations_are_bounded_distinct_and_not_fake_graph_executions():
    body = command()
    pin = {'capability_id': str(uuid4()), 'capability_hash': 'e' * 64,
        'actor_id': body['tool_bindings']['packet']['actor_id']}
    body['observation_extractors'] = [pin]
    parsed = commands.parse(wire(body), commands.Preview)[0]
    assert len(parsed.observation_extractors) == 1
    assert set(node['id'] for node in graph(body)['graph']['nodes']).isdisjoint({pin['capability_id']})
    body['observation_extractors'] = [pin, pin]
    with pytest.raises(HTTPException):
        commands.parse(wire(body), commands.Preview)


def test_async_perception_output_is_a_frozen_declaration_with_no_graph_node():
    body = command()
    binding = cold_binding(body)
    identity = str(uuid4())
    declared = {'capability_id': identity, 'capability_hash': 'e' * 64,
        'actor_id': body['tool_bindings']['packet']['actor_id']}
    binding['observation_extractors'] = [declared]
    binding['observation_extractor_pins'] = {identity: {
        'capability_id': identity, 'capability_hash': 'e' * 64, 'agent_role': 'perception',
        'input_bindings': {}, 'arguments': {}, 'input_schema': {'type': 'object'},
        'output_schema': {'type': 'object'}, 'adapter': 'builtin', 'release_version': '1.0.0', 'max_attempts': 1}}
    binding = reseal(binding)
    assert store.check_binding(binding) == binding
    assert all(node['id'] != identity for node in binding['graph']['nodes'])
    assert recipe.projection(binding)['observation_extractors'] == [
        {'capability_id': identity, 'executes_in_this_graph': False}]
    binding['observation_extractor_pins'][identity]['agent_role'] = 'data'
    with pytest.raises(HTTPException):
        store.check_binding(reseal(binding))


@pytest.mark.parametrize('raw', [b'{"x":NaN}', b'{"x":1,"x":2}', b'[]', b'\xff', b''])
def test_bad_wire_is_not_reconstructed(raw):
    with pytest.raises(HTTPException):
        commands.parse(raw, commands.Preview)
