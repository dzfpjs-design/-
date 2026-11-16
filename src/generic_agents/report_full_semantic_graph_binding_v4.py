"""Lossless graph translation plus explicitly frozen typed event authority.

The v3 projection is rebuilt unchanged; its hash remains independently pinned.
These pure checks grant neither producer registration nor permission to execute.
"""
import copy
from .contracts import digest
from . import report_full_semantic_graph_binding_v3 as v3

SCHEMA = 'report-full-semantic-graph-binding-v4'
KEYS = v3.KEYS | {'base_binding_hash', 'event_source_bindings', 'event_admission_policy'}


def build_binding(design, manifest, proof, business_name, graph, owner_bindings, *,
        proof_id, capability_pins, role_bindings, event_source_bindings, event_admission_policy):
    from .graph_wait_sources_v118 import check_bindings, check_policy
    original = v3.build_binding(design, manifest, proof, business_name, graph, owner_bindings,
        proof_id=proof_id, capability_pins=capability_pins, role_bindings=role_bindings)
    sources = check_bindings(original['graph'], event_source_bindings)
    policy = check_policy(event_admission_policy)
    value = {key: copy.deepcopy(item) for key, item in original.items() if key != 'binding_hash'}
    value.update(schema=SCHEMA, base_binding_hash=original['binding_hash'],
        event_source_bindings=sources, event_admission_policy=policy)
    return {**value, 'binding_hash': digest(value)}


def check_binding(value, design=None):
    v3._keys(value, KEYS, 'binding_v4_shape'); v3.source._json(value)
    expected = build_binding(value['source_design'] if design is None else design,
        value['source_manifest'], value['source_proof'], value['business_name'], value['graph'],
        value['owner_bindings'], proof_id=value['proof_id'], capability_pins=value['capability_pins'],
        role_bindings=value['role_bindings'], event_source_bindings=value['event_source_bindings'],
        event_admission_policy=value['event_admission_policy'])
    v3._need(v3._same(value, expected), 'binding_v4_changed')
    return expected
