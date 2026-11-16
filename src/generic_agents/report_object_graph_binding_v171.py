"""Independent object graph bindings with unchanged typed event semantics.

The base is built directly from a V169 design, V170 owners/proof and schema
origins. No legacy binding is retagged. All database/source/IAM/consent and
event producer authority remains with the caller's independent cold gates.
"""
import copy

from .contracts import digest
from . import report_full_semantic_graph_binding_v3 as kernel

SCHEMA = 'report-object-graph-binding-v171'
BASE_SCHEMA = 'report-object-graph-base-binding-v171'
KEYS = kernel.KEYS | {'base_binding_hash', 'event_source_bindings', 'event_admission_policy'}


def required_owner_fields(design):
    """Complete original-order owners, including each identity and actual ack index."""
    return kernel._required_owner_fields(design, profile=kernel._OBJECT_PROFILE)


def build_base_binding(design, manifest, proof, business_name, graph, owner_bindings, *,
        proof_id, capability_pins, role_bindings):
    """Build the new base directly; self-consistent origins are not DB authority."""
    return kernel._build_binding(design, manifest, proof, business_name, graph, owner_bindings,
        proof_id=proof_id, capability_pins=capability_pins, role_bindings=role_bindings,
        profile=kernel._OBJECT_PROFILE)


def build_binding(design, manifest, proof, business_name, graph, owner_bindings, *,
        proof_id, capability_pins, role_bindings, event_source_bindings, event_admission_policy):
    from .graph_wait_sources_v118 import check_bindings, check_policy
    original = build_base_binding(design, manifest, proof, business_name, graph, owner_bindings,
        proof_id=proof_id, capability_pins=capability_pins, role_bindings=role_bindings)
    sources = check_bindings(original['graph'], event_source_bindings)
    policy = check_policy(event_admission_policy)
    value = {key: copy.deepcopy(item) for key, item in original.items() if key != 'binding_hash'}
    value.update(schema=SCHEMA, base_binding_hash=original['binding_hash'],
        event_source_bindings=sources, event_admission_policy=policy)
    return {**value, 'binding_hash': digest(value)}


def check_binding(value, design=None):
    kernel._need(type(value) is dict and value.get('schema') == SCHEMA, 'object_binding_family')
    kernel._keys(value, KEYS, 'object_binding_shape'); kernel.source._json(value)
    expected = build_binding(value['source_design'] if design is None else design,
        value['source_manifest'], value['source_proof'], value['business_name'], value['graph'],
        value['owner_bindings'], proof_id=value['proof_id'], capability_pins=value['capability_pins'],
        role_bindings=value['role_bindings'], event_source_bindings=value['event_source_bindings'],
        event_admission_policy=value['event_admission_policy'])
    kernel._need(kernel._same(value, expected), 'object_binding_changed')
    return expected
