"""Pure reconstruction of the V288 publication projection and closure.

Synthetic pins exercise the actual structural checker and pure compiler. They
do not prove registry custody, publication, IAM, acquisition, ACK or persistence.
The separate PostgreSQL publication test owns that verification boundary.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import authored_goal_data_workflow_store_v288 as family
from generic_agents import authored_workflow_runtime_v208 as runtime
from generic_agents import authored_workflow_store_v208 as common
from generic_agents.authored_service_data_outputs_v270 import outputs
from generic_agents.contracts import digest
from generic_agents.instance_graph_data_requirements_v3 import build_requirements
from tests.test_authored_goal_data_workflow_v288 import command, cold_binding, ident, reseal
from tests.test_instance_data_requirements_pure import snapshot as ordinary_snapshot


EXTRACTOR, CANDIDATE = ident(70), ident(71)


def binding():
    body = command()
    body['candidate_specs'][0]['capability_pins'].append({
        'capability_ref': 'independent_comparison', 'capability_id': CANDIDATE,
        'capability_hash': 'd' * 64, 'release_version': '1.0.0',
        'delivery': 'output', 'ports': []})
    value = cold_binding(body)
    value['observation_extractors'] = [{'capability_id': EXTRACTOR,
        'capability_hash': 'c' * 64, 'actor_id': ident(90)}]
    value['observation_extractor_pins'] = {EXTRACTOR: {
        'capability_id': EXTRACTOR, 'capability_hash': 'c' * 64,
        'agent_role': 'perception', 'input_bindings': {}, 'arguments': {},
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'output_schema': {'type': 'object', 'properties': {'anomalies': {
            'type': 'array', 'items': {'type': 'string'}}}, 'additionalProperties': False},
        'adapter': 'builtin', 'release_version': '1.0.0', 'max_attempts': 1}}
    value['automation'] = {'policy': {'triggers': ['人工发起']}}
    return family.check_binding(reseal(value))


def compiled_snapshot():
    value = ordinary_snapshot(); selected = binding()
    issued = {'schema': common.ORIGIN, 'adoption_id': ident(72),
        'business_name': selected['business_name'], 'binding_hash': selected['binding_hash'],
        'command_hash': 'e' * 64}
    business = {'id': ident(73), 'name': selected['business_name'], 'available': True,
        'issues': [], 'execution_mode': 'confirmed_graph_workflow',
        'flow': copy.deepcopy(selected['flow']), runtime.BUSINESS_MARKER: issued}
    value['businesses'] = [business]
    value['roles'] = copy.deepcopy(selected['role_bindings'])
    value['semantic_runtime_plan'] = runtime.plan_value(value['businesses'],
        [{'origin': issued, 'binding': selected}], value['roles'])
    for key in ('object_origins', 'full_semantic_schema_adoptions', 'relation_types', 'relation_origins'):
        value[key] = []
    return value


def test_v288_compiler_preserves_separate_extractor_and_non_node_dependencies():
    value = compiled_snapshot(); before = copy.deepcopy(value)
    selected = value['semantic_runtime_plan']['graph_adoptions'][0]['binding']
    abilities = {node['execution']['capability_id'] for node in selected['graph']['nodes']
        if node['kind'] == 'ability'}
    assert EXTRACTOR not in abilities and CANDIDATE not in abilities
    assert set(runtime.dependencies(value)) == abilities | {EXTRACTOR, CANDIDATE}
    result = build_requirements(value)
    declarations = [item for business in result['businesses'] for item in business['outputs']
        if item['kind'] == 'asynchronous_perception']
    assert declarations == outputs(selected) and len(declarations) == 1
    declaration = declarations[0]
    assert declaration['capability_id'] == EXTRACTOR and declaration['node_id'] is None
    assert declaration['source_binding_hash'] == selected['binding_hash']
    assert declaration['output_schema_hash'] == digest(declaration['output_schema'])
    assert declaration['status'] == 'candidate'
    assert declaration['activation_identity'] == 'original_observation_processing_job'
    assert value == before


def test_resealed_extractor_identity_mismatch_still_fails_before_projection():
    value = binding()
    value['observation_extractor_pins'][EXTRACTOR]['capability_hash'] = 'f' * 64
    with pytest.raises(HTTPException):
        outputs(reseal(value))


def test_unknown_family_cannot_reuse_the_asynchronous_projection():
    value = binding(); value['schema'] = 'authored-goal-data-workflow-binding-v289-unknown'
    with pytest.raises(ValueError, match='Unsupported asynchronous perception declaration family'):
        outputs(reseal(value))


def test_v288_manual_start_uses_original_explicit_policy():
    runtime.require_task_start(binding(), verified_anomaly_handoff=False)


@pytest.mark.parametrize('handoff', [True, None, 0, 'false'])
def test_v288_manual_start_rejects_unsupported_handoff(handoff):
    with pytest.raises(HTTPException):
        runtime.require_task_start(binding(), verified_anomaly_handoff=handoff)


@pytest.mark.parametrize('triggers', [[], ['定时运行'], ['人工发起', '定时运行']])
def test_v288_manual_start_does_not_expand_automatic_authority(triggers):
    value = binding(); value['automation']['policy']['triggers'] = triggers
    with pytest.raises(HTTPException):
        runtime.require_task_start(reseal(value), verified_anomaly_handoff=False)
