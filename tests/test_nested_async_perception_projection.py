"""Pure nested publication proof. Registry authority belongs to the PG journey."""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_execution_runtime as runtime
from generic_agents import business_flow_execution_adoption as adoption
from generic_agents import business_flow_nested_adoption_v195 as nested
from generic_agents.contracts import digest
from generic_agents.instance_data_contract_dispatch import build_requirements, verify_requirements
from tests.test_business_flow_nested_adoption_v195 import command, fixture, identity, reseal, wire
from tests.test_instance_data_requirements_pure import snapshot as ordinary_snapshot


def test_nested_command_preserves_legacy_body_and_requires_explicit_valid_selection():
    original = command(fixture())
    assert adoption.parse(wire(original), nested.Adopt).model_dump(mode='json') == original
    selection = {'capability_id': identity('local async extractor'),
                 'capability_hash': 'c' * 64, 'actor_id': identity('local executor')}
    chosen = {**original, 'observation_extractors': [selection]}
    assert adoption.parse(wire(chosen), nested.Adopt).model_dump(mode='json') == chosen
    for invalid in (None, [], [selection, selection]):
        with pytest.raises(HTTPException):
            adoption.parse(wire({**original, 'observation_extractors': invalid}), nested.Adopt)


def resense_binding():
    value = fixture(service=True)
    package = value['source']
    plan = package['qualification']['value']
    source_action = plan['business']['flows'][0]['phases'][0]['branches'][0]['phases'][0]['branches'][0]['phases'][0]['steps'][0]
    source_action['actor'] = 'execution_service'
    node = next(row for row in value['execution_bindings']['nodes'].values()
        if row.get('service_actor') == 'task_center')
    node.update(service_actor='execution_service', operation='resense_with_receipt')
    value['causal_bindings']['source_plan_hash'] = digest(plan)
    translated = nested.compiler.build_execution_graph(plan, 0, value['execution_bindings'],
        scope_binding=package['scope_binding'], causal_bindings=value['causal_bindings'])
    value.update(graph=translated['graph'], translation=translated, flow=nested.derive_flow(package, 0, translated),
        capability_pins={row['id']: copy.deepcopy(row['execution']) for row in translated['graph']['nodes']
            if row['kind'] == 'ability'})
    return reseal(value)


def opt_in():
    value = resense_binding()
    action = next(node for node in value['graph']['nodes']
        if node['kind'] == 'ability')
    cap_id = identity('separate local async extractor')
    pin = {**copy.deepcopy(action['execution']), 'capability_id': cap_id,
        'capability_hash': 'c' * 64, 'agent_role': 'perception', 'adapter': 'builtin',
        'input_bindings': {}, 'arguments': {}}
    value['observation_extractors'] = [{'capability_id': cap_id,
        'capability_hash': pin['capability_hash'], 'actor_id': action['actor_id']}]
    value['observation_extractor_pins'] = {cap_id: pin}
    return reseal(value), cap_id


def snapshot(binding):
    value = ordinary_snapshot()
    name = binding['business_name']
    origin = nested.origin(identity('async projection adoption'), binding, 'e' * 64)
    roles = {key: {field: actor[field] for field in ('user_id', 'username')}
        for key, actor in binding['role_bindings'].items()}
    value['businesses'] = [{'id': identity('async projection business'), 'name': name,
        'available': True, 'issues': [], 'execution_mode': 'confirmed_graph_workflow',
        'flow': copy.deepcopy(binding['flow']), runtime.BUSINESS_MARKER: origin}]
    value['roles'] = roles
    value['semantic_runtime_plan'] = runtime.plan_value(value['businesses'],
        [{'binding': binding, 'origin': origin}], None, roles)
    for key in ('object_origins', 'full_semantic_schema_adoptions', 'relation_types', 'relation_origins'):
        value[key] = []
    return value


def test_nested_opt_in_is_separate_from_graph_output_and_sealed_into_release_requirements():
    binding, cap_id = opt_in()
    legacy = resense_binding()
    value = snapshot(binding)
    before = copy.deepcopy(value)
    requirements = build_requirements(value)
    legacy_outputs = build_requirements(snapshot(legacy))['businesses'][0]['outputs']
    outputs = requirements['businesses'][0]['outputs']
    assert [row for row in outputs if row['kind'] != 'asynchronous_perception'] == legacy_outputs
    declared, = [row for row in outputs if row['kind'] == 'asynchronous_perception']
    pin = binding['observation_extractor_pins'][cap_id]
    assert cap_id not in {node['execution']['capability_id']
        for node in binding['graph']['nodes'] if node['kind'] == 'ability'}
    assert declared['capability_id'] == cap_id and declared['node_id'] is None
    assert declared['capability_hash'] == pin['capability_hash']
    assert declared['release_version'] == pin['release_version']
    assert declared['output_schema'] == pin['output_schema']
    assert declared['output_schema_hash'] == digest(pin['output_schema'])
    assert declared['source_binding_hash'] == binding['binding_hash']
    assert declared['activation_identity'] == 'original_observation_processing_job'
    assert declared['declaration_kind'] == 'registered_observation_extractor'
    assert declared['status'] == 'candidate'
    assert value == before
    value['data_requirements'] = requirements
    verify_requirements(value)
    declared['source_binding_hash'] = 'f' * 64
    with pytest.raises(ValueError):
        verify_requirements(value)


def test_legacy_nested_binding_and_runtime_plan_are_neither_resealed_nor_implicitly_opted_in():
    binding = fixture()
    original = copy.deepcopy(binding)
    assert not {'observation_extractors', 'observation_extractor_pins'} & binding.keys()
    assert nested.check_binding(binding) == binding == original
    value = snapshot(binding)
    requirements = build_requirements(value)
    assert requirements['businesses'][0]['outputs']
    assert all(row['kind'] != 'asynchronous_perception' for row in requirements['businesses'][0]['outputs'])
    assert value['semantic_runtime_plan']['graph_adoptions'][0]['binding'] == original
    value['data_requirements'] = requirements
    verify_requirements(value)


@pytest.mark.parametrize('damage', ['missing_pins', 'wrong_hash'])
def test_resealed_nested_async_opt_in_requires_matching_explicit_pin(damage):
    value, cap_id = opt_in()
    if damage == 'missing_pins':
        value.pop('observation_extractor_pins')
    else:
        value['observation_extractor_pins'][cap_id]['capability_hash'] = 'f' * 64
    with pytest.raises(HTTPException):
        snapshot(reseal(value))
