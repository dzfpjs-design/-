"""Independent enum-family reduction projection over unchanged V166 wire.

The strict original wire parser/reconciliation predicates are shared; no old
projection is accepted, converted or retagged. Source and HTTP authority remain
independent caller gates.
"""
import copy
import hashlib
from .report_evidence_reduction_wire_v168 import (
    ReductionWireV168, MAX_WIRE_BYTES, MAX_PROJECTION_BYTES,
    _parse, _inputs, _reconcile, _bytes, _need, _hash,
)
from .report_object_semantics_v166 import SCHEMA, validate_value

PROJECTION = 'report-evidence-reduction-projection-v172'


def build_reduction_projection(raw_arguments_bytes, *, catalog, allowed_span_refs,
                               context, input_order, input_items):
    wire = _parse(raw_arguments_bytes)
    claims = _inputs(catalog, allowed_span_refs, context, input_order, input_items)
    manual = {'schema': SCHEMA, 'result': copy.deepcopy(wire['result']), 'note': '',
        'object_identity_confirmations': [{'object_name': obj['name'],
            'acknowledge_platform_identity': False} for obj in wire['result']['objects']]}
    checked = validate_value(manual, catalog, allowed_span_refs, context)
    _need(_bytes(checked) == _bytes(manual))
    _reconcile(wire, input_order, claims)
    value = {'schema': PROJECTION,
        'wire_bytes_sha256': hashlib.sha256(raw_arguments_bytes).hexdigest(),
        'wire_bytes': len(raw_arguments_bytes), 'wire_value_hash': _hash(wire),
        'source_hash': catalog['descriptor']['source_hash'],
        'catalog_hash': catalog['descriptor']['catalog_hash'],
        'allowed_span_refs_hash': _hash(allowed_span_refs), 'context_hash': _hash(context),
        'input_order': copy.deepcopy(input_order), 'input_items_hash': _hash(input_items),
        'result': copy.deepcopy(wire['result']), 'reconciliation': copy.deepcopy(wire['reconciliation']),
        'result_hash': _hash(wire['result']), 'reconciliation_hash': _hash(wire['reconciliation']),
        'provider_delivery_verified': False, 'semantic_quality_verified': False,
        'platform_identity_confirmed': False, 'can_apply': False, 'can_publish': False}
    result = {**value, 'projection_hash': _hash(value)}
    _need(len(_bytes(result)) <= MAX_PROJECTION_BYTES)
    return result


def check_reduction_projection(projection, raw_arguments_bytes, **expected_inputs):
    expected = build_reduction_projection(raw_arguments_bytes, **expected_inputs)
    _need(_bytes(projection) == _bytes(expected))
    return expected
