"""Compact model wire with separately identified platform reconciliation.

This pure module does not inspect HTTP completion, call ownership or approval.
It preserves the new original wire and deterministically expands its groups,
then applies the existing complete-input, V166 and per-input validation gates.
An expansion is platform-derived data, never the model's old reconciliation.
"""
import copy
import hashlib
import json
from typing import Annotated

from pydantic import Field, model_validator

from . import report_evidence_reduction_wire_v168 as original
from .report_evidence_wire_v3 import ReconciliationWireV5
from .report_full_reading_semantics import Closed
from .report_object_semantics_v166 import CandidateObjects, SCHEMA, validate_value


WIRE = 'report-design-grouped-wire-v174'
PROJECTION = 'report-design-grouped-projection-v174'
EXPANSION = 'report-design-grouped-expansion-v174'
MAX_WIRE_BYTES = original.MAX_WIRE_BYTES
MAX_PROJECTION_BYTES = original.MAX_PROJECTION_BYTES
MAX_INPUTS = 1920
ERROR = '紧凑业务方案的原字节、归并分组或完整输入无法核对；未修写、裁剪或采用'
Position = Annotated[int, Field(strict=True, ge=0, le=MAX_INPUTS - 1)]
Disposition = ReconciliationWireV5.model_fields['disposition'].annotation
OutputKind = ReconciliationWireV5.model_fields['output_kind'].annotation


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


class ReconciliationGroupV174(Closed):
    output_kind: OutputKind
    output_index: Annotated[int, Field(strict=True, ge=0)]
    disposition: Disposition
    input_positions: list[Position] = Field(min_length=1, max_length=MAX_INPUTS)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def ordered_unique_positions(self):
        _need(bool(self.reason.strip()) and len(set(self.input_positions)) == len(self.input_positions)
            and self.input_positions == sorted(self.input_positions))
        return self


class DesignGroupedWireV174(Closed):
    result: CandidateObjects
    reconciliation_groups: list[ReconciliationGroupV174] = Field(min_length=1, max_length=MAX_INPUTS)


def parse_wire(raw_arguments_bytes):
    """Parse only the new shape; all original bytes remain caller-owned."""
    _need(type(raw_arguments_bytes) is bytes and 0 < len(raw_arguments_bytes) <= MAX_WIRE_BYTES)
    try:
        value = json.loads(raw_arguments_bytes.decode('utf-8', errors='strict'),
            object_pairs_hook=original._pairs, parse_constant=original._constant,
            parse_float=original._number)
        original._bytes(value)
        checked = DesignGroupedWireV174.model_validate(value).model_dump()
        _need(original._bytes(checked) == original._bytes(value))
        return checked
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def expand_groups(groups, input_count):
    """Structural expansion only; source, targets and semantics are not granted.

    Preserve original group order in the wire. The derived reconciliation is
    always ordered by the already frozen input positions, independent of group
    order. No input, group or duplicate is silently removed or reassigned.
    """
    _need(type(input_count) is int and 1 <= input_count <= MAX_INPUTS
        and type(groups) is list and 1 <= len(groups) <= input_count)
    expanded, targets = {}, set()
    for raw in groups:
        group = ReconciliationGroupV174.model_validate(raw).model_dump()
        _need(original._bytes(group) == original._bytes(raw))
        key = (group['output_kind'], group['output_index'], group['disposition'])
        _need(key not in targets)
        targets.add(key)
        for position in group['input_positions']:
            _need(position < input_count and position not in expanded)
            row = {key: copy.deepcopy(group[key]) for key in
                ('output_kind', 'output_index', 'disposition', 'reason')}
            row['input_position'] = position
            # Use the actual old strict entry contract, not an imitation of it.
            checked = ReconciliationWireV5.model_validate(row).model_dump()
            _need(original._bytes(checked) == original._bytes(row))
            expanded[position] = checked
    _need(set(expanded) == set(range(input_count)))
    return [expanded[position] for position in range(input_count)]


def build_reduction_projection(raw_arguments_bytes, *, catalog, allowed_span_refs,
                               context, input_order, input_items):
    """Check frozen inputs and every expanded position without any authority.

    Original input support remains 1..1920, including contiguous per-Map claim
    positions. Complete successful Map/capture coverage belongs to the caller.
    The old wire parser is intentionally not applied to expanded platform JSON:
    its byte limit describes original model output, not repeated derived reasons.
    """
    wire = parse_wire(raw_arguments_bytes)
    claims = original._inputs(catalog, allowed_span_refs, context, input_order, input_items)
    manual = {'schema': SCHEMA, 'result': copy.deepcopy(wire['result']), 'note': '',
        'object_identity_confirmations': [{'object_name': obj['name'],
            'acknowledge_platform_identity': False} for obj in wire['result']['objects']]}
    checked = validate_value(manual, catalog, allowed_span_refs, context)
    _need(original._bytes(checked) == original._bytes(manual))
    expanded = expand_groups(wire['reconciliation_groups'], len(input_order))
    original._reconcile({'result': wire['result'], 'reconciliation': expanded}, input_order, claims)
    expansion = {'schema': EXPANSION, 'input_order': copy.deepcopy(input_order),
        'reconciliation': copy.deepcopy(expanded)}
    value = {'schema': PROJECTION, 'wire_schema': WIRE,
        'wire_bytes_sha256': hashlib.sha256(raw_arguments_bytes).hexdigest(),
        'wire_bytes': len(raw_arguments_bytes), 'wire_value_hash': original._hash(wire),
        'wire': copy.deepcopy(wire),
        'source_hash': catalog['descriptor']['source_hash'],
        'catalog_hash': catalog['descriptor']['catalog_hash'],
        'allowed_span_refs_hash': original._hash(allowed_span_refs), 'context_hash': original._hash(context),
        'input_order': copy.deepcopy(input_order), 'input_items_hash': original._hash(input_items),
        'result': copy.deepcopy(wire['result']), 'result_hash': original._hash(wire['result']),
        'reconciliation_groups': copy.deepcopy(wire['reconciliation_groups']),
        'reconciliation_groups_hash': original._hash(wire['reconciliation_groups']),
        'expansion_schema': EXPANSION, 'expansion_hash': original._hash(expansion),
        'expanded_reconciliation': copy.deepcopy(expanded),
        'expanded_reconciliation_hash': original._hash(expanded),
        'provider_delivery_verified': False, 'semantic_quality_verified': False,
        'platform_identity_confirmed': False, 'can_apply': False, 'can_publish': False}
    projection = {**value, 'projection_hash': original._hash(value)}
    _need(len(original._bytes(projection)) <= MAX_PROJECTION_BYTES)
    return projection


def check_reduction_projection(projection, raw_arguments_bytes, **expected_inputs):
    expected = build_reduction_projection(raw_arguments_bytes, **expected_inputs)
    _need(type(projection) is dict and original._bytes(projection) == original._bytes(expected))
    return expected
