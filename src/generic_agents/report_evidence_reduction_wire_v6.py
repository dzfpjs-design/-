"""Unconnected required/closed Reduce V6 wire and exact pure codec.

No gateway, journal, pipeline, API, DB, filesystem, credentials or approval is
used here. The future caller must independently verify the whole original Map
plan, each HTTP/journal/profile/arguments chain, scope and paid authorization.
``input_order``/``input_items`` are that caller's frozen verified inputs, not a
substitute for that proof. Hashes below prove deterministic consistency only.

The complete CandidateWireV5 model has three nullable field locations: each
step's next_index/else_index and each object field's unit. No existing model
field has a default or an open dictionary. V6 replaces precisely those nullable
values by required closed tagged objects. Every other field and array order is
retained; no edge, unit, enum, reference or uncertainty is inferred or repaired.

The 1 MiB bound is this new protocol's explicit bound, not an increase of
the old 256 KiB journal/wire limit or a promise that 131072 output tokens fit.
No codec result may be treated as an accepted existing V5 provider response.
This module is not connected to a provider, paid dispatch or a user workflow.
"""
import copy
import hashlib
import json
import math
import re
from typing import Literal

from pydantic import Field

from generic_agents.contracts import digest
from generic_agents.report_evidence_plan import check_evidence_source
from generic_agents.report_evidence_semantics import SpanCitation, SpanObject, validate_evidence_merge
from generic_agents.report_evidence_ref_schema_v2 import compile_ref_schema
from generic_agents.report_evidence_wire_v3 import (CandidateWireV5, ReduceResultWireV5,
    StepWireV5, BusinessWireV5, Index, candidate_with_ids)
from generic_agents.report_full_reading_semantics import Closed, FullField, _safe


WIRE = 'report-evidence-candidate-wire-v6'
CODEC = 'report-evidence-reduction-codec-v6'
PROJECTION = 'report-evidence-reduction-projection-v6'
CANDIDATE = 'report-evidence-candidate-projection-v6'
IDS = 'report-evidence-reduction-ids-v6'
MAX_WIRE_BYTES = 1024 * 1024
MAX_PROJECTION_BYTES = 8 * 1024 * 1024
_INPUT = re.compile(r'^map-([1-9]|1[0-9]|2[0-4])/claims/(0|[1-9]|[1-7][0-9])$')


class EndTarget(Closed):
    kind: Literal['end']


class StepTarget(Closed):
    kind: Literal['step']
    index: Index


class UnspecifiedUnit(Closed):
    kind: Literal['unspecified']


class SpecifiedUnit(Closed):
    kind: Literal['specified']
    value: str = Field(min_length=1, max_length=40)


class FieldWireV6(Closed):
    # FullField's after-validator expects str|None, so do not inherit and feed a
    # tag object to it. The exact decoded FullField is validated below instead.
    label: str = Field(min_length=1, max_length=80)
    kind: FullField.model_fields['kind'].annotation
    required: bool
    unit: UnspecifiedUnit | SpecifiedUnit
    citations: list[SpanCitation] = Field(max_length=4)
    inference_reason: str = Field(max_length=500)


class ObjectWireV6(SpanObject):
    fields: list[FieldWireV6] = Field(min_length=1, max_length=40)


class StepWireV6(StepWireV5):
    next_index: EndTarget | StepTarget
    else_index: EndTarget | StepTarget


class BusinessWireV6(BusinessWireV5):
    steps: list[StepWireV6] = Field(max_length=16)


class CandidateWireV6(CandidateWireV5):
    schema: Literal['report-evidence-candidate-wire-v6']
    objects: list[ObjectWireV6] = Field(max_length=40)
    businesses: list[BusinessWireV6] = Field(max_length=20)


class ReductionWireV6(ReduceResultWireV5):
    result: CandidateWireV6


def _json_bytes(value):
    _safe(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8', errors='strict')


def _size(value, maximum):
    if len(_json_bytes(value)) > maximum:
        raise ValueError('reduction_v6_capacity_exceeded')


def _checked(value, model):
    if type(value) is not dict:
        raise ValueError('reduction_v6_wire_object_required')
    result = model.model_validate(value).model_dump()
    if _json_bytes(result) != _json_bytes(value):
        raise ValueError('reduction_v6_wire_changed_during_validation')
    _size(result, MAX_WIRE_BYTES)
    return result


def decode_reduction_wire_v6(value):
    """Return exact V5 codec value; never call the old wire-size adapter."""
    decoded = _checked(value, ReductionWireV6)
    decoded['result']['schema'] = 'report-evidence-candidate-wire-v5'
    for business in decoded['result']['businesses']:
        for step in business['steps']:
            for name in ('next_index', 'else_index'):
                target = step[name]
                step[name] = None if target['kind'] == 'end' else target['index']
    for obj in decoded['result']['objects']:
        for field in obj['fields']:
            unit = field['unit']
            field['unit'] = None if unit['kind'] == 'unspecified' else unit['value']
    # This includes the old field-name/unit validators, complete object shape,
    # all original enums and strict integer/boolean distinctions. Cross-array,
    # lexical and reconciliation checks run with the resolver in projection().
    return _checked(decoded, ReduceResultWireV5)


def encode_reduction_wire_v6(value):
    """Exact inverse of a complete typed V5 value; no fallback for bad values."""
    encoded = _checked(value, ReduceResultWireV5)
    encoded['result']['schema'] = WIRE
    for business in encoded['result']['businesses']:
        for step in business['steps']:
            for name in ('next_index', 'else_index'):
                index = step[name]
                step[name] = {'kind': 'end'} if index is None else {'kind': 'step', 'index': index}
    for obj in encoded['result']['objects']:
        for field in obj['fields']:
            unit = field['unit']
            field['unit'] = {'kind': 'unspecified'} if unit is None else {'kind': 'specified', 'value': unit}
    return _checked(encoded, ReductionWireV6)


def provider_schema_proof():
    """Audited provider subset; no assertion of provider API acceptance/quality."""
    local = json.loads(_json_bytes(ReductionWireV6.model_json_schema()))
    def closed(node):
        if type(node) is dict:
            if 'default' in node or node.get('type') == 'null' or type(node.get('type')) is list:
                raise ValueError('reduction_v6_schema_default_or_nullable')
            if 'properties' in node:
                if (node.get('type') != 'object' or node.get('additionalProperties') is not False
                        or set(node.get('required', [])) != set(node['properties'])):
                    raise ValueError('reduction_v6_schema_open_or_optional_object')
            for child in node.values(): closed(child)
        elif type(node) is list:
            for child in node: closed(child)
    closed(local)
    return compile_ref_schema(local)


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value: raise ValueError('reduction_v6_duplicate_json_key')
        value[key] = item
    return value


def _constant(_): raise ValueError('reduction_v6_nonfinite_json')


def _number(text):
    value = float(text)
    if not math.isfinite(value): raise ValueError('reduction_v6_nonfinite_json')
    return value


def _parse(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_WIRE_BYTES:
        raise ValueError('reduction_v6_raw_arguments_capacity_or_type')
    try:
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_pairs,
            parse_constant=_constant, parse_float=_number)
        return _checked(value, ReductionWireV6)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError('reduction_v6_raw_arguments_invalid') from None


def build_reduction_projection(raw_arguments_bytes, *, catalog, allowed_span_refs,
                               context, input_order, input_items):
    """Strict bytes -> wire6 -> codec5 -> full local semantic/reconcile view.

    All expected resolver/Map inputs must come from the future caller's already
    verified full chain. This function cannot establish HTTP or journal origin,
    complete planned Map coverage, authorization, approval or an executable DAG.
    Explicit multi-step cycles retain the existing semantic rules unchanged.
    """
    wire = _parse(raw_arguments_bytes); decoded = decode_reduction_wire_v6(wire)
    check_evidence_source(catalog); _safe([context, input_order, input_items, allowed_span_refs])
    if (type(context) is not dict or type(input_items) is not dict or type(input_order) is not list
            or not 1 <= len(input_order) <= 1920 or any(type(key) is not str or not _INPUT.fullmatch(key) for key in input_order)
            or len(set(input_order)) != len(input_order) or set(input_order) != set(input_items)
            or input_order != sorted(input_order, key=lambda key: tuple(map(int, _INPUT.fullmatch(key).groups())))
            or type(allowed_span_refs) is not list or any(type(ref) is not str for ref in allowed_span_refs)
            or len(set(allowed_span_refs)) != len(allowed_span_refs)):
        raise ValueError('reduction_v6_frozen_input_order_or_scope')
    positions = [item['input_position'] for item in decoded['reconciliation']]
    if len(positions) != len(set(positions)) or set(positions) != set(range(len(input_order))):
        raise ValueError('reduction_v6_reconciliation_not_bijective')
    metadata = {'schema': PROJECTION, 'codec': CODEC, 'id_policy': IDS,
        'wire_bytes_sha256': hashlib.sha256(raw_arguments_bytes).hexdigest(), 'wire_bytes': len(raw_arguments_bytes),
        'wire_value_hash': digest(wire), 'codec_value_hash': digest(decoded),
        'source_hash': catalog['descriptor']['source_hash'], 'catalog_hash': catalog['descriptor']['catalog_hash'],
        'input_order': copy.deepcopy(input_order), 'input_items_hash': digest(input_items),
        'allowed_span_refs_hash': digest(allowed_span_refs), 'context_hash': digest(context)}
    context_hash = digest(metadata)
    def identifier(prefix, path):
        return prefix + digest({'domain': IDS, 'derivation_context_hash': context_hash, 'path': path})[:32]
    result = candidate_with_ids(decoded['result'], identifier)
    reconciliation = [{'input_id': input_order[item['input_position']], **{key: copy.deepcopy(value)
        for key, value in item.items() if key != 'input_position'}} for item in decoded['reconciliation']]
    internal = {'result': {**copy.deepcopy(result), 'schema': 'report-evidence-candidate-v4'},
        'reconciliation': reconciliation}
    if _json_bytes(validate_evidence_merge(internal, catalog, allowed_span_refs, context, input_items)) != _json_bytes(internal):
        raise ValueError('reduction_v6_local_semantic_view_changed')
    ordered_refs = []
    for source in catalog['descriptor']['ordered_source_refs']:
        number = 1
        while (ref := f'{source}/s{number}') in catalog['index']:
            if ref in allowed_span_refs: ordered_refs.append(ref)
            number += 1
    if ordered_refs != allowed_span_refs:
        raise ValueError('reduction_v6_evidence_order_differs')
    result['schema'] = CANDIDATE
    value = {**metadata, 'derivation_context_hash': context_hash, 'result': result, 'reconciliation': reconciliation}
    projection = {**value, 'projection_hash': digest(value)}
    _size(projection, MAX_PROJECTION_BYTES)
    return projection


def check_reduction_projection(projection, raw_arguments_bytes, **expected_inputs):
    expected = build_reduction_projection(raw_arguments_bytes, **expected_inputs)
    if type(projection) is not dict or _json_bytes(projection) != _json_bytes(expected):
        raise ValueError('reduction_v6_projection_does_not_cold_rebuild')
    return expected
