"""New indexed design wire, with an explicitly platform-derived quality view.

Only a NEW V179 answer may enter here. Original bytes, count and groups remain
unchanged. The V174 pure quality validator receives a detached derived view
only after the complete V179 structure and exact indexed coverage pass. That
view is not another model answer, an old capture, a source grant or adoption.
"""
import copy
import hashlib
import json
from typing import Annotated

from pydantic import Field

from . import report_design_grouped_wire_v174 as quality
from . import report_evidence_reduction_wire_v168 as original
from . import report_indexed_inputs_v178 as indexed_codec
from .report_evidence_wire_v3 import ReconciliationWireV5
from .report_full_reading_semantics import Closed
from .report_object_semantics_v166 import CandidateObjects


WIRE = 'report-design-grouped-wire-v179'
PROJECTION = 'report-design-grouped-projection-v179'
EXPANSION = 'report-design-grouped-expansion-v179'
MAX_WIRE_BYTES = original.MAX_WIRE_BYTES
MAX_PROJECTION_BYTES = original.MAX_PROJECTION_BYTES
MAX_INPUTS = indexed_codec.MAX_INPUTS
ERROR = '编号业务方案的原字节、实际输入总数、完整对照或来源质量门无法核对；未修写、裁剪或采用'
Position = Annotated[int, Field(strict=True, ge=0, le=MAX_INPUTS - 1)]
Disposition = ReconciliationWireV5.model_fields['disposition'].annotation
OutputKind = ReconciliationWireV5.model_fields['output_kind'].annotation


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


class ReconciliationGroupV179(Closed):
    output_kind: OutputKind
    output_index: Annotated[int, Field(strict=True, ge=0)]
    disposition: Disposition
    input_positions: list[Position] = Field(min_length=1, max_length=MAX_INPUTS)
    reason: str = Field(min_length=1, max_length=1000)


class DesignGroupedWireV179(Closed):
    result: CandidateObjects
    input_count: Annotated[int, Field(strict=True, ge=1, le=MAX_INPUTS)]
    reconciliation_groups: list[ReconciliationGroupV179] = Field(min_length=1, max_length=MAX_INPUTS)


def _schema_for_count(count):
    """Structural schema only; a count alone never establishes actual source."""
    _need(type(count) is int and 1 <= count <= MAX_INPUTS)
    schema = DesignGroupedWireV179.model_json_schema()
    schema['properties']['input_count'].update(const=count, maximum=count, minimum=count)
    schema['properties']['reconciliation_groups']['maxItems'] = count
    positions = schema['$defs']['ReconciliationGroupV179']['properties']['input_positions']
    positions.update(maxItems=count, uniqueItems=True)
    positions['items']['maximum'] = count - 1
    return schema


def output_schema(indexed, *, input_claims, source_snapshot_hash):
    """Complete V166 plus dynamic bounds, rebuilt from actual frozen inputs.

    uniqueItems stays in this complete LOCAL schema. The independent native
    protocol documents its removal only from the transport subset. This local
    parser still requires uniqueness and ascending/exact coverage; a transport
    schema is never substituted for the full local validation contract.
    """
    frozen = indexed_codec.check_indexed_inputs(indexed, input_claims=input_claims,
        source_snapshot_hash=source_snapshot_hash)
    return _schema_for_count(frozen['input_count'])


def parse_wire(raw_arguments_bytes, *, indexed, input_claims, source_snapshot_hash):
    """Strict whole wire + dynamic fragment, preserving the model's raw groups.

    The Pydantic model validates every V166/result/group field without coercion;
    the indexed codec enforces every dynamic count/position constraint from
    output_schema, plus cross-group uniqueness, order and exact coverage.
    """
    _need(type(raw_arguments_bytes) is bytes and 0 < len(raw_arguments_bytes) <= MAX_WIRE_BYTES)
    try:
        # Rebuild first: neither a self-reported count nor a resealed manifest
        # can replace the actual source supplied independently by the caller.
        output_schema(indexed, input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
        value = json.loads(raw_arguments_bytes.decode('utf-8', errors='strict'),
            object_pairs_hook=original._pairs, parse_constant=original._constant,
            parse_float=original._number)
        original._bytes(value)
        checked = DesignGroupedWireV179.model_validate(value).model_dump()
        _need(original._bytes(checked) == original._bytes(value))
        indexed_codec.validate_reconciliation({key: value[key] for key in
            ('input_count', 'reconciliation_groups')}, indexed,
            input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
        return copy.deepcopy(value)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def build_reduction_projection(raw_arguments_bytes, *, catalog, allowed_span_refs,
                               context, input_order, input_items, indexed, source_snapshot_hash):
    """Full existing semantic/reference gates, never model-quality authority.

    Complete successful producer/capture/source ownership remains the caller's
    obligation. Only the actual checked ordered inputs determine N. The old
    validator's byte hashes explicitly describe a PLATFORM view, not the new
    original response. No old response is decoded or relabeled by this module.
    """
    claims = original._inputs(catalog, allowed_span_refs, context, input_order, input_items)
    ordered = [copy.deepcopy(claims[identity]) for identity in input_order]
    frozen = indexed_codec.check_indexed_inputs(indexed, input_claims=ordered,
        source_snapshot_hash=source_snapshot_hash)
    wire = parse_wire(raw_arguments_bytes, indexed=frozen, input_claims=ordered,
        source_snapshot_hash=source_snapshot_hash)
    derived_wire = {'result': copy.deepcopy(wire['result']),
        'reconciliation_groups': copy.deepcopy(wire['reconciliation_groups'])}
    # Explicit detached platform projection, after the new full schema and
    # indexed gate. This reuses the real V166/manual/reference/per-input gates.
    derived_bytes = original._bytes(derived_wire)
    validated = quality.build_reduction_projection(derived_bytes, catalog=catalog,
        allowed_span_refs=allowed_span_refs, context=context, input_order=input_order, input_items=input_items)
    expanded = copy.deepcopy(validated['expanded_reconciliation'])
    expansion = {'schema': EXPANSION, 'input_order': copy.deepcopy(input_order),
        'input_count': frozen['input_count'], 'indexed_inputs_hash': frozen['inputs_hash'],
        'reconciliation': expanded}
    value = {'schema': PROJECTION, 'wire_schema': WIRE,
        'wire_bytes_sha256': hashlib.sha256(raw_arguments_bytes).hexdigest(),
        'wire_bytes': len(raw_arguments_bytes), 'wire_value_hash': original._hash(wire),
        'wire': copy.deepcopy(wire), 'input_count': wire['input_count'],
        'source_snapshot_hash': source_snapshot_hash, 'indexed_inputs_hash': frozen['inputs_hash'],
        'input_claims_hash': frozen['input_claims_hash'], 'model_payload_hash': frozen['model_payload_hash'],
        'source_hash': validated['source_hash'], 'catalog_hash': validated['catalog_hash'],
        'allowed_span_refs_hash': validated['allowed_span_refs_hash'], 'context_hash': validated['context_hash'],
        'input_order': copy.deepcopy(input_order), 'input_items_hash': validated['input_items_hash'],
        'result': copy.deepcopy(wire['result']), 'result_hash': original._hash(wire['result']),
        'reconciliation_groups': copy.deepcopy(wire['reconciliation_groups']),
        'reconciliation_groups_hash': original._hash(wire['reconciliation_groups']),
        'expansion_schema': EXPANSION, 'expansion_hash': original._hash(expansion),
        'expanded_reconciliation': expanded, 'expanded_reconciliation_hash': original._hash(expanded),
        'platform_quality_view_schema': quality.PROJECTION,
        'platform_quality_view_wire_sha256': hashlib.sha256(derived_bytes).hexdigest(),
        'platform_quality_view_hash': validated['projection_hash'],
        'provider_delivery_verified': False, 'semantic_quality_verified': False,
        'platform_identity_confirmed': False, 'can_apply': False, 'can_publish': False}
    projection = {**value, 'projection_hash': original._hash(value)}
    _need(len(original._bytes(projection)) <= MAX_PROJECTION_BYTES)
    return projection


def check_reduction_projection(projection, raw_arguments_bytes, **expected_inputs):
    expected = build_reduction_projection(raw_arguments_bytes, **expected_inputs)
    _need(type(projection) is dict and original._bytes(projection) == original._bytes(expected))
    return expected

