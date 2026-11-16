"""Pure command/graph invariants and explicit publication-boundary spies.

No saved adoption, SQL, IAM, model, packet producer or equipment is manufactured
by these tests. The real V208 ledger and shared family routes are separate gates.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import authored_decision_workflow_contracts_v286 as commands
from generic_agents import authored_decision_workflow_graph_v286 as recipe
from generic_agents import authored_decision_workflow_store_v286 as store
from generic_agents.contracts import digest


def ident(index): return str(UUID(int=index))


def wire(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def command():
    tools = {key: {'capability_id': ident(index), 'capability_hash': 'a' * 64, 'actor_id': ident(90)}
        for index, key in enumerate(commands.OPERATIONS, 10)}
    return {'schema': 'authored-decision-workflow-preview-command-v286',
        'configuration_revision': 1, 'configuration_hash': 'b' * 64, 'business_name': '天气原来源决策',
        'tool_bindings': tools, 'candidate_specs': [{'candidate_ref': 'candidate_acquire',
            'proposal': '按已批准范围申请缺失观测，另行核对回执。', 'claims': [], 'capability_pins': [{
                'capability_ref': 'sampling', 'capability_id': tools['resense']['capability_id'],
                'capability_hash': tools['resense']['capability_hash'], 'release_version': '2.0.0',
                'delivery': 'requested_observation',
                'ports': [{'field': 'temperature_c', 'pointer': '/requested_observation/temperature_c'}]}]}],
        'decision_intent': 'information_acquisition', 'observation_extractors': [], 'reviewer_id': ident(92),
        'role_bindings': {'原人工核查人': ident(91)}, 'node_actions': dict(commands.NODE_ACTIONS),
        'external_node_ids': list(commands.EXTERNAL_NODE_IDS), 'prior_origin': None, 'simulation_only': True}


def flow():
    return {'trigger': '人工发起原范围核查', 'output': '人工核查记录', 'steps': [{'id': 'review',
        'agent': '人工确认', 'name': '核查两份原来源与处理结果', 'role': '原人工核查人', 'output': '核查意见', 'nextId': 'end'}]}


def graph_inputs(body):
    pins, actors = {}, {}
    adapters = {'data_packet': 'task_evidence_packet', 'packet': 'runtime_document_packet',
        'reasoning': 'business_decision_v284', 'planner': 'workflow_service', 'resense': 'workflow_service'}
    roles = {'原人工核查人': {'user_id': ident(91), 'username': 'reviewer'}, recipe.review_role(body['business_name']): {'user_id': body['reviewer_id'], 'username': 'decision_reviewer'}}
    for key, pin in body['tool_bindings'].items():
        inputs, outputs = recipe._schemas(key)
        pins[key] = {'capability_id': pin['capability_id'], 'capability_hash': pin['capability_hash'],
            'agent_role': recipe.ROLES[key], 'input_bindings': {}, 'arguments': {}, 'input_schema': inputs,
            'output_schema': outputs, 'adapter': adapters[key], 'release_version': '2.0.0' if key == 'resense' else '1.0.0', 'max_attempts': 1}
        actors[key] = pin['actor_id']
        roles[recipe.tool_role(body['business_name'], key)] = {'user_id': pin['actor_id'], 'username': 'executor'}
    return roles, pins, actors


def graph(body):
    roles, pins, actors = graph_inputs(body)
    return recipe.build_graph(flow(), roles, pins, actors, business_name=body['business_name'],
        candidate_specs=body['candidate_specs'], reviewer_id=body['reviewer_id'], decision_intent=body['decision_intent'])


def cold_binding(body):
    built = graph(body); roles, _, _ = graph_inputs(body)
    targets = {pin['capability_id']: {key: pin[key] for key in ('capability_id', 'capability_hash', 'release_version')}
        for candidate in body['candidate_specs'] for pin in candidate['capability_pins']}
    return store.common._seal({'schema': commands.BINDING, 'authorship': 'human_authored', 'model_generated': False,
        'semantic_quality_verified': False, 'simulation_only': True, 'template': commands.TEMPLATE,
        'configuration_id': ident(100), 'configuration_revision': body['configuration_revision'],
        'configuration_hash': body['configuration_hash'], 'business_name': body['business_name'], 'prior_origin': None,
        'flow': flow(), 'flow_signature': {}, 'flow_hash': digest(flow()), 'role_bindings': roles,
        'tool_bindings': body['tool_bindings'], 'candidate_specs': body['candidate_specs'], 'decision_intent': body['decision_intent'],
        'candidate_tool_pins': targets, 'observation_extractors': [], 'observation_extractor_pins': {},
        'graph': built['graph'], 'step_mapping': built['step_mapping'],
        'capability_pins': {node['id']: node['execution'] for node in built['graph']['nodes'] if node['kind'] == 'ability'},
        'service_delegations': built['service_delegations'], 'node_actions': body['node_actions'],
        'external_node_ids': body['external_node_ids'], 'event_source_bindings': {}, 'automation': {}, 'source_materials': []})


def reseal(value): return store.common._seal({key: item for key, item in value.items() if key != 'binding_hash'})


def test_raw_command_uses_its_new_family_and_exact_original_bytes():
    body = command()
    adopted = {**body, 'schema': 'authored-decision-workflow-adopt-command-v286', 'request_id': ident(101),
        'preview_hash': 'c' * 64, 'consent': True}
    raw = wire(adopted); parsed, exact, hashed = commands.parse(raw, commands.Adopt)
    assert exact.encode('utf-8') == raw and hashed == hashlib.sha256(raw).hexdigest()
    assert commands.preview_body(parsed) == body
    assert commands.PATH == '/api/setup/authored-decision-workflow-graphs-v286/adopt'


@pytest.mark.parametrize('field,value', [('schema', 'authored-service-workflow-preview-command-v270'),
    ('simulation_only', 1), ('simulation_only', False), ('graph', {}), ('decision_result', {}),
    ('context', {}), ('external_node_ids', ['reasoning', 'planner']), ('decision_intent', 'operational_action')])
def test_command_does_not_accept_other_families_or_fabricated_runtime_values(field, value):
    body = command(); body[field] = value
    with pytest.raises(HTTPException): commands.parse(wire(body), commands.Preview)


def test_two_real_packet_ports_precede_reasoning_without_pair_or_reader_node():
    built = graph(command()); value = built['graph']; nodes = {node['id']: node for node in value['nodes']}
    assert value['entry'] == 'data_packet' and 'context_reader' not in nodes
    assert set(node['id'] for node in value['nodes'] if node['kind'] == 'ability') == set(commands.OPERATIONS)
    inputs = nodes['reasoning']['execution']['input_bindings']
    assert inputs == {'question': 'analysis_question', 'data_packet': 'original_data_packet', 'document_packet': 'original_document_packet'}
    assert 'evidence_ids' not in inputs and 'evidence_ids' not in nodes['reasoning']['execution']['arguments']
    assert value['references']['original_data_packet']['origin']['node_id'] == 'data_packet'
    assert value['references']['original_document_packet']['origin']['node_id'] == 'packet'
    assert nodes['data_packet']['execution']['input_bindings'] == {'policy_pin': 'policy_pin'}


def test_four_routes_require_real_extra_review_only_on_non_execution_branches():
    body=command();built=graph(body);value=built['graph'];first=built['step_mapping'][0]['node_id']
    branches={edge['on']:edge['to'] for edge in value['edges'] if edge['from']=='route_acquisition'}
    continuation={edge['on']:edge['to'] for edge in value['edges'] if edge['from']=='route_continue'}
    assert branches=={'true':'resense','false':'route_continue'}
    assert continuation=={'true':first,'false':recipe.REVIEW_NODE}
    assert value['predicates']['needs_acquisition']['right']['value']=='acquire'
    assert value['predicates']['can_continue']['right']['value']=='continue'
    assert [e['to'] for e in value['edges'] if e['from']==recipe.REVIEW_NODE]==[first]
    review=next(n for n in value['nodes'] if n['id']==recipe.REVIEW_NODE)
    assert review['kind']=='human' and review['actor_id']==body['reviewer_id'] and review['source_step_id'] is None
    assert set(value['goals'][0]['required_nodes'])=={'data_packet','packet','reasoning','planner',first}
    projection=recipe.projection(cold_binding(body));assert any(s['node_id']==recipe.REVIEW_NODE and s['kind']=='human' for s in projection['steps'])
    assert 'resense' not in value['goals'][0]['required_nodes'] and recipe.REVIEW_NODE not in value['goals'][0]['required_nodes']


@pytest.mark.parametrize('change', ['delegation', 'packet_origin', 'branch', 'required_resense', 'candidate_hash', 'fake_extractor'])
def test_rehashed_self_description_cannot_change_original_family_graph(change):
    binding = cold_binding(command()); assert store.check_binding(binding) == binding
    if change == 'delegation': binding['service_delegations']['planner']['operation'] = 'execute_registered_tool_plan'
    elif change == 'packet_origin': binding['graph']['references']['original_data_packet']['origin']['node_id'] = 'packet'
    elif change == 'branch': next(edge for edge in binding['graph']['edges'] if edge['on'] == 'false')['to'] = 'resense'
    elif change == 'required_resense': binding['graph']['goals'][0]['required_nodes'].append('resense')
    elif change == 'candidate_hash': next(iter(binding['candidate_tool_pins'].values()))['capability_hash'] = 'f' * 64
    else: binding['observation_extractor_pins'][ident(200)] = copy.deepcopy(binding['capability_pins']['packet'])
    with pytest.raises(HTTPException): store.check_binding(reseal(binding))


def test_candidate_capability_identity_cannot_be_aliased_or_given_inconsistent_ports():
    body = command(); other = copy.deepcopy(body['candidate_specs'][0]); other['candidate_ref'] = 'candidate_other'
    other['capability_pins'][0]['capability_ref'] = 'other_alias'; body['candidate_specs'].append(other)
    with pytest.raises(HTTPException): commands.parse(wire(body), commands.Preview)


def test_normal_adoption_and_recovery_delegate_to_original_common_ledger(monkeypatch):
    seen = []
    monkeypatch.setattr(store.common, 'adopt', lambda *args, **kwargs: seen.append(('adopt', args, kwargs)) or 'saved')
    monkeypatch.setattr(store.common, 'recover', lambda *args, **kwargs: seen.append(('recover', args, kwargs)) or 'original')
    assert store.adopt('db', 'p', b'exact') == 'saved'
    assert store.recover('db', 'p', ident(101), 'd' * 64) == 'original'
    assert all(item[2] == {'_family_path': commands.PATH} for item in seen)


def test_explicit_review_actor_cannot_be_omitted_or_replaced_by_tool_identity():
    body=command();missing=copy.deepcopy(body);del missing['reviewer_id']
    with pytest.raises(HTTPException):commands.parse(wire(missing),commands.Preview)
    roles,pins,actors=graph_inputs(body);roles[recipe.review_role(body['business_name'])]['user_id']=ident(999)
    with pytest.raises(ValueError):recipe.build_graph(flow(),roles,pins,actors,business_name=body['business_name'],candidate_specs=body['candidate_specs'],reviewer_id=body['reviewer_id'])
    binding=cold_binding(body);recovered=store.binding_body(binding)
    assert recovered['reviewer_id']==body['reviewer_id'] and recipe.review_role(body['business_name'])not in recovered['role_bindings']
