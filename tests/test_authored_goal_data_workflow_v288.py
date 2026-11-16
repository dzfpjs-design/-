"""Authored V288 contract and frozen graph specifications.

These tests use synthetic pins and do not prove database custody, acquisition,
independent ACK, real DataRun persistence, producer completion or cloud consent.
They were authored statically; no execution claim accompanies this file.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import authored_goal_data_workflow_contracts_v288 as commands
from generic_agents import authored_goal_data_workflow_graph_v288 as recipe
from generic_agents import authored_goal_data_workflow_store_v288 as store
from generic_agents import workflow_goal_evaluation_contracts_v288 as evaluation
from generic_agents.contracts import digest


def ident(index): return str(UUID(int=index))


def wire(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def policy(*, fixed=False):
    value = {'schema': evaluation.POLICY, 'time_basis': 'fixed_window' if fixed else 'rolling_latest',
        'allow_acquisition': not fixed, 'max_age_seconds': None if fixed else 600, 'timezone': 'Asia/Shanghai'}
    return {**value, 'policy_hash': evaluation.policy_hash(value)}


def command():
    tools = {key: {'capability_id': ident(index), 'capability_hash': 'a' * 64, 'actor_id': ident(90)}
        for index, key in enumerate(commands.OPERATIONS, 10)}
    return {'schema': 'authored-goal-data-workflow-preview-command-v288',
        'configuration_revision': 1, 'configuration_hash': 'b' * 64, 'business_name': '批准范围资料准备',
        'tool_bindings': tools, 'preparation_policy': policy(), 'candidate_specs': [{'candidate_ref': 'candidate_acquire',
            'proposal': '资料齐备后比较已登记方案，独立核对来源。', 'claims': [], 'capability_pins': [{
                'capability_ref': 'sampling', 'capability_id': tools['resense']['capability_id'],
                'capability_hash': tools['resense']['capability_hash'], 'release_version': '2.0.0',
                'delivery': 'requested_observation',
                'ports': [{'field': 'temperature_c', 'pointer': '/requested_observation/temperature_c'}]}]}],
        'decision_intent': 'information_acquisition', 'observation_extractors': [], 'reviewer_id': ident(92),
        'role_bindings': {'原人工核查人': ident(91)}, 'node_actions': dict(commands.NODE_ACTIONS),
        'external_node_ids': list(commands.EXTERNAL_NODE_IDS), 'prior_origin': None, 'simulation_only': True}


def flow():
    return {'trigger': '人工发起原范围核查', 'output': '人工核查记录', 'steps': [{'id': 'review',
        'agent': '人工确认', 'name': '核对实际资料与建议', 'role': '原人工核查人', 'output': '核查意见', 'nextId': 'end'}]}


def graph_inputs(body):
    pins, actors = {}, {}
    adapters = {'context_reader': 'builtin', 'local_prepare': 'workflow_service', 'resense': 'workflow_service',
        'evaluation_packet': 'workflow_service', 'packet': 'runtime_document_packet', 'reasoning': 'business_goal_ready_v288'}
    roles = {'原人工核查人': {'user_id': ident(91), 'username': 'reviewer'},
        recipe.review_role(body['business_name']): {'user_id': body['reviewer_id'], 'username': 'decision_reviewer'}}
    for key, pin in body['tool_bindings'].items():
        inputs, outputs = recipe._schemas(key)
        pins[key] = {'capability_id': pin['capability_id'], 'capability_hash': pin['capability_hash'],
            'agent_role': recipe.ROLES[key], 'input_bindings': {}, 'arguments': {}, 'input_schema': inputs,
            'output_schema': outputs, 'adapter': adapters[key], 'release_version': '2.0.0' if key == 'resense' else '1.0.0',
            'max_attempts': 1}
        actors[key] = pin['actor_id']
        roles[recipe.tool_role(body['business_name'], key)] = {'user_id': pin['actor_id'], 'username': 'executor'}
    return roles, pins, actors


def graph(body):
    roles, pins, actors = graph_inputs(body)
    return recipe.build_graph(flow(), roles, pins, actors, business_name=body['business_name'],
        candidate_specs=body['candidate_specs'], preparation_policy=body['preparation_policy'],
        reviewer_id=body['reviewer_id'], decision_intent=body['decision_intent'])


def cold_binding(body):
    built = graph(body); roles, _, _ = graph_inputs(body)
    targets = {pin['capability_id']: {key: pin[key] for key in ('capability_id', 'capability_hash', 'release_version')}
        for candidate in body['candidate_specs'] for pin in candidate['capability_pins']}
    return store.common._seal({'schema': commands.BINDING, 'authorship': 'human_authored', 'model_generated': False,
        'semantic_quality_verified': False, 'simulation_only': True, 'template': commands.TEMPLATE,
        'workspace_id': ident(1), 'configuration_id': ident(100), 'configuration_revision': body['configuration_revision'],
        'configuration_hash': body['configuration_hash'], 'business_name': body['business_name'], 'prior_origin': None,
        'flow': flow(), 'flow_signature': {}, 'flow_hash': digest(flow()), 'role_bindings': roles,
        'tool_bindings': body['tool_bindings'], 'preparation_policy': body['preparation_policy'],
        'candidate_specs': body['candidate_specs'], 'decision_intent': body['decision_intent'],
        'candidate_tool_pins': targets, 'observation_extractors': [], 'observation_extractor_pins': {},
        'graph': built['graph'], 'step_mapping': built['step_mapping'],
        'capability_pins': {node['id']: node['execution'] for node in built['graph']['nodes'] if node['kind'] == 'ability'},
        'service_delegations': built['service_delegations'], 'node_actions': body['node_actions'],
        'external_node_ids': body['external_node_ids'], 'event_source_bindings': {}, 'automation': {}, 'source_materials': []})


def reseal(value): return store.common._seal({key: item for key, item in value.items() if key != 'binding_hash'})


def targets(value, source):
    return {edge['on']: edge['to'] for edge in value['edges'] if edge['from'] == source}


def test_raw_command_preserves_bytes_and_uses_only_new_family():
    body = command()
    adopted = {**body, 'schema': 'authored-goal-data-workflow-adopt-command-v288', 'request_id': ident(101),
        'preview_hash': 'c' * 64, 'consent': True}
    raw = wire(adopted); parsed, exact, hashed = commands.parse(raw, commands.Adopt)
    assert exact.encode('utf-8') == raw and hashed == hashlib.sha256(raw).hexdigest()
    assert commands.preview_body(parsed) == body
    assert commands.PATH == '/api/setup/authored-goal-data-workflow-graphs-v288/adopt'
    assert commands.BINDING == 'authored-goal-data-workflow-binding-v288'


@pytest.mark.parametrize('field,value', [('schema', 'authored-decision-workflow-preview-command-v286'),
    ('simulation_only', 1), ('simulation_only', False), ('graph', {}), ('evaluation_result', {}),
    ('context_result', {}), ('external_node_ids', ['reasoning', 'local_prepare']), ('decision_intent', 'unsupported_intent')])
def test_closed_command_rejects_other_families_and_runtime_claims(field, value):
    body = command(); body[field] = value
    with pytest.raises(HTTPException): commands.parse(wire(body), commands.Preview)


@pytest.mark.parametrize('change', [
    lambda p: p.update(timezone='invalid/zone'), lambda p: p.update(max_age_seconds=None),
    lambda p: p.update(allow_acquisition=1), lambda p: p.update(max_age_seconds=True),
    lambda p: p.update(unrequested_field='temperature'),
])
def test_rehashed_policy_still_requires_explicit_legal_parameters(change):
    body = command(); change(body['preparation_policy'])
    body['preparation_policy']['policy_hash'] = evaluation.policy_hash(body['preparation_policy'])
    with pytest.raises(HTTPException): commands.parse(wire(body), commands.Preview)


def test_fixed_policy_does_not_enable_new_sensing_and_actual_goal_is_separate():
    value = policy(fixed=True)
    assert commands.configured_policy(value) == value
    value['allow_acquisition'] = True; value['policy_hash'] = evaluation.policy_hash(value)
    with pytest.raises(ValueError): commands.configured_policy(value)
    # Adoption has no actual Task/Goal input. The real Goal compatibility check
    # belongs to the local producer, never this saved configuration validator.
    assert 'goal_snapshot' not in commands.Preview.model_fields


def test_local_preparation_and_ack_are_before_the_only_model_node():
    body = command(); built = graph(body); value = built['graph']
    nodes = {node['id']: node for node in value['nodes']}
    assert value['entry'] == 'context_reader'
    assert {node['id'] for node in value['nodes'] if node['kind'] == 'ability'} == set(commands.OPERATIONS)
    assert targets(value, 'context_reader') == {'success': 'local_prepare'}
    assert targets(value, 'route_acquisition') == {'true': 'resense', 'false': 'route_ready'}
    assert targets(value, 'route_ready') == {'true': 'evaluation_packet', 'false': recipe.DATA_REVIEW_NODE}
    assert targets(value, 'resense') == {'success': 'evaluation_packet'}
    assert targets(value, 'evaluation_packet') == {'success': 'route_evaluated'}
    assert targets(value, 'route_evaluated') == {'true': 'packet', 'false': recipe.DATA_REVIEW_NODE}
    assert targets(value, 'packet') == {'success': 'reasoning'}
    assert targets(value, 'reasoning') == {'success': recipe.REVIEW_NODE}
    assert nodes['local_prepare']['execution']['input_bindings'] == {'context_result': 'original_reader_result'}
    assert nodes['local_prepare']['execution']['arguments'] == {}
    assert nodes['evaluation_packet']['execution']['input_bindings'] == {'local_plan': 'local_data_plan'}
    assert nodes['evaluation_packet']['execution']['arguments'] == {}
    assert 'preparation_policy' not in value['input_schema']['properties']
    assert value['references']['original_goal_evaluation_packet']['origin']['node_id'] == 'evaluation_packet'
    assert nodes['reasoning']['execution']['input_bindings']['data_packet'] == 'original_goal_evaluation_packet'
    assert value['references']['planned_context']['projection'] == {
        'op': 'select', 'pointer': '/resense_context', 'require_non_null': True}


def test_insufficient_reevaluation_and_manual_review_do_not_satisfy_goal():
    body = command(); built = graph(body); value = built['graph']; first = built['step_mapping'][0]['node_id']
    assert value['predicates']['evaluation_ready']['right']['value'] == 'data_ready'
    assert targets(value, recipe.DATA_REVIEW_NODE) == {'success': first}
    assert targets(value, recipe.REVIEW_NODE) == {'success': first}
    required = set(value['goals'][0]['required_nodes'])
    assert {'evaluation_packet', 'packet', 'reasoning', recipe.REVIEW_NODE, first} <= required
    assert recipe.DATA_REVIEW_NODE not in required and 'resense' not in required
    reviews = [node for node in value['nodes'] if node['id'] in {recipe.DATA_REVIEW_NODE, recipe.REVIEW_NODE}]
    assert all(node['kind'] == 'human' and node['actor_id'] == body['reviewer_id'] for node in reviews)


def test_service_delegations_are_exact_and_isolated_from_the_original_family():
    assert recipe.declarations() == {
        'local_prepare': {'service_actor': 'execution_service', 'operation': 'plan_approved_goal_data_v288', 'agent_role': 'business'},
        'resense': {'service_actor': 'execution_service', 'operation': 'resense_from_context', 'agent_role': 'business'},
        'evaluation_packet': {'service_actor': 'reader_service', 'operation': 'create_goal_evaluation_packet_v288', 'agent_role': 'data'},
    }
    binding = cold_binding(command())
    assert store.service_delegations(binding) == recipe.declarations()
    assert store.binding_body(binding) == command()


@pytest.mark.parametrize('change', ['delegation', 'packet_origin', 'early_cloud', 'skip_review',
    'relax_goal', 'candidate_hash', 'policy', 'repeat_policy', 'fake_extractor', 'producer_attempts'])
def test_rehashing_does_not_rewrite_the_frozen_graph_or_pins(change):
    binding = cold_binding(command()); assert store.check_binding(binding) == binding
    if change == 'delegation': binding['service_delegations']['evaluation_packet']['service_actor'] = 'execution_service'
    elif change == 'packet_origin': binding['graph']['references']['original_goal_evaluation_packet']['origin']['node_id'] = 'context_reader'
    elif change == 'early_cloud': next(edge for edge in binding['graph']['edges'] if edge['from'] == 'context_reader')['to'] = 'reasoning'
    elif change == 'skip_review': next(edge for edge in binding['graph']['edges'] if edge['from'] == 'reasoning')['to'] = binding['step_mapping'][0]['node_id']
    elif change == 'relax_goal': binding['graph']['goals'][0]['required_nodes'].remove('evaluation_packet')
    elif change == 'candidate_hash': next(iter(binding['candidate_tool_pins'].values()))['capability_hash'] = 'f' * 64
    elif change == 'policy':
        binding['preparation_policy']['max_age_seconds'] = 1200
        binding['preparation_policy']['policy_hash'] = evaluation.policy_hash(binding['preparation_policy'])
    elif change == 'repeat_policy': binding['capability_pins']['local_prepare']['arguments'] = {'preparation_policy': policy()}
    elif change == 'producer_attempts': binding['capability_pins']['evaluation_packet']['max_attempts'] = 2
    else: binding['observation_extractor_pins'][ident(200)] = copy.deepcopy(binding['capability_pins']['packet'])
    with pytest.raises(HTTPException): store.check_binding(reseal(binding))


def test_preview_scope_must_match_the_actual_workspace_and_is_detached():
    body = commands.Preview.model_validate(command()); binding = cold_binding(command())
    with pytest.raises(HTTPException): store.make_preview(NS(workspace_id=ident(999), user_id=ident(90)), body, binding)
    checked = store.check_binding(binding); checked['preparation_policy']['max_age_seconds'] = 1200
    assert binding['preparation_policy']['max_age_seconds'] == 600


def test_six_ports_and_original_human_posts_cannot_be_rebound():
    body = command(); body['tool_bindings']['evaluation_packet'] = copy.deepcopy(body['tool_bindings']['context_reader'])
    with pytest.raises(HTTPException): commands.parse(wire(body), commands.Preview)
    body = command(); roles, pins, actors = graph_inputs(body)
    roles[recipe.review_role(body['business_name'])]['user_id'] = ident(999)
    with pytest.raises(ValueError):
        recipe.build_graph(flow(), roles, pins, actors, business_name=body['business_name'], candidate_specs=body['candidate_specs'],
            preparation_policy=body['preparation_policy'], reviewer_id=body['reviewer_id'])


def test_ledger_operations_use_the_literal_new_path_without_substituting_data(monkeypatch):
    seen = []
    for name in ('adopt', 'recover', 'get_adoption'):
        def recorder(*args, _name=name, **kwargs):
            seen.append((_name, args, kwargs)); return _name
        monkeypatch.setattr(store.common, name, recorder)
    assert store.adopt('db', 'p', b'exact') == 'adopt'
    assert store.recover('db', 'p', ident(101), 'd' * 64) == 'recover'
    assert store.get_adoption('db', 'p', ident(102)) == 'get_adoption'
    assert all(item[2] == {'_family_path': commands.PATH} for item in seen)


@pytest.mark.parametrize('intent', ['information_acquisition', 'operational_action', 'business_conclusion'])
def test_post_data_intent_is_explicit_frozen_and_does_not_grant_any_execution(intent):
    body = command(); body['decision_intent'] = intent
    parsed, _exact, _hash = commands.parse(wire(body), commands.Preview)
    frozen = cold_binding(body)
    assert parsed.decision_intent == intent and store.check_binding(frozen)['decision_intent'] == intent
    nodes = {node['id']: node for node in frozen['graph']['nodes']}
    assert nodes['reasoning']['execution']['arguments']['decision_intent'] == intent
    assert nodes['review_decision']['kind'] == 'human'


@pytest.mark.parametrize('field,value', [('agent_role', 'business'), ('operation', 'resense_from_context')])
def test_data_service_cannot_borrow_another_server_role_or_operation(field, value):
    from generic_agents.report_full_semantic_graph_adoption import ability_capabilities
    body = command(); declarations = {key: {**item, 'side_effect': True}
        for key, item in recipe.declarations().items()}
    declarations['evaluation_packet'][field] = value
    with pytest.raises(HTTPException):
        ability_capabilities(None, None, graph(body)['graph'], ['reasoning'], service_delegations=declarations)
