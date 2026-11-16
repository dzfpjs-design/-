"""Declare separately executed perception contracts, never imaginary graph nodes.

Publication authenticates the original model registrations and adoption. This
pure projection is rebuilt from that verified binding during data-plan creation;
the ordinary receiver still needs an actual Observation, Invocation and event.
"""
import copy
from .contracts import digest


def outputs(binding):
    from .authored_workflow_store_v208 import check_binding
    if binding.get('schema') not in {'authored-service-workflow-binding-v270', 'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286', 'authored-goal-data-workflow-binding-v288'}:
        raise ValueError('Unsupported asynchronous perception declaration family')
    value = check_binding(binding)
    return project_outputs(value['observation_extractor_pins'], value['binding_hash'])


def project_outputs(pins, binding_hash):
    """Project exact pins from an already verified adoption binding."""
    result = []
    for identity, pin in sorted(pins.items()):
        schema = pin['output_schema']
        result.append({'step_id': 'perception:' + identity, 'node_id': None,
            'step_name': '观测资料的异步信息提取', 'kind': 'asynchronous_perception',
            'declaration_kind': 'registered_observation_extractor',
            'agent_role': 'perception', 'capability_id': identity,
            'capability_hash': pin['capability_hash'], 'release_version': pin['release_version'],
            'output_schema': copy.deepcopy(schema), 'output_schema_hash': digest(schema),
            'input_bindings': {},
            'fields': [{'name': key, 'schema': copy.deepcopy(item)}
                for key, item in sorted(schema.get('properties', {}).items())],
            'storage': 'existing_evidence_and_receipts', 'status': 'candidate',
            'source_binding_hash': binding_hash,
            'activation_identity': 'original_observation_processing_job'})
    return result
