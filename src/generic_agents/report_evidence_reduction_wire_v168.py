"""Independent native-nullable Reduce wire and pure consistency projection.

The result belongs to V166; it is never converted into a V5/V6 object model.
The caller supplies its complete, already verified new Map index and original
scope. This module cannot prove provider delivery, ownership, semantic quality,
complete report coverage, adoption, publication or execution permission.
"""
import copy
import hashlib
import json
import math
import re

from pydantic import Field

from .report_evidence_plan import check_evidence_source
from .report_evidence_semantics import _provided, _refs, _check_refs
from .report_evidence_wire_v3 import MapClaimWireV3, ReconciliationWireV5
from .report_full_reading_semantics import Closed, _safe
from .report_object_semantics_v166 import CandidateObjects, SCHEMA, validate_value
from .typed_payload_codec_v131 import canonical_json


PROJECTION = 'report-evidence-reduction-projection-v168'
MAX_WIRE_BYTES = 1024 * 1024
MAX_PROJECTION_BYTES = 8 * 1024 * 1024
ERROR = '声明归并的原字节、完整输入、来源或对象语义无法核对；未改写或截断原结果'
_INPUT = re.compile(r'^map-([1-9]|1[0-9]|2[0-4])/claims/(0|[1-9]|[1-7][0-9])$')


class ReductionWireV168(Closed):
    result: CandidateObjects
    reconciliation: list[ReconciliationWireV5] = Field(max_length=1920)


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _bytes(value):
    try:
        _safe(value)
        return canonical_json(value).encode('utf-8', errors='strict')
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _pairs(items):
    value = {}
    for key, item in items:
        _need(key not in value)
        value[key] = item
    return value


def _constant(_):
    raise ValueError(ERROR)


def _number(text):
    value = float(text)
    _need(math.isfinite(value))
    return value


def _parse(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES)
    try:
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_pairs,
            parse_constant=_constant, parse_float=_number)
        _need(type(value) is dict)
        _bytes(value)
        checked = ReductionWireV168.model_validate(value).model_dump()
        _need(_bytes(checked) == _bytes(value))
        return checked
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def _inputs(catalog, allowed_span_refs, context, input_order, input_items):
    check_evidence_source(catalog)
    _bytes([context, input_order, input_items, allowed_span_refs])
    _need(type(context) is dict and type(input_items) is dict and type(input_order) is list
        and 1 <= len(input_order) <= 1920
        and all(type(key) is str and _INPUT.fullmatch(key) for key in input_order)
        and len(set(input_order)) == len(input_order) and set(input_order) == set(input_items))
    _need(input_order == sorted(input_order,
        key=lambda key: tuple(map(int, _INPUT.fullmatch(key).groups()))))
    provided = _provided(catalog, allowed_span_refs)
    ordered = []
    for source in catalog['descriptor']['ordered_source_refs']:
        number = 1
        while (ref := f'{source}/s{number}') in catalog['index']:
            ordered.append(ref)
            number += 1
    _need([ref for ref in ordered if ref in provided] == allowed_span_refs)
    claims, groups = {}, {}
    for identity in input_order:
        original = input_items[identity]
        claim = MapClaimWireV3.model_validate(original).model_dump()
        _need(_bytes(claim) == _bytes(original))
        _check_refs(set(claim['span_refs']), provided, '/input_items/' + identity)
        claims[identity] = claim
        match = _INPUT.fullmatch(identity)
        groups.setdefault(int(match[1]), []).append(int(match[2]))
    for positions in groups.values():
        _need(positions == list(range(len(positions))))
    original_refs = set().union(*(set(claim['span_refs']) for claim in claims.values()))
    _need(set(provided) == original_refs)
    return claims


def _reconcile(wire, input_order, claims):
    entries = wire['reconciliation']
    positions = [item['input_position'] for item in entries]
    _need(len(positions) == len(set(positions)) and set(positions) == set(range(len(input_order))))
    for item in entries:
        targets = wire['result'][item['output_kind']]
        _need(item['output_index'] < len(targets) and bool(item['reason'].strip()))
        original = claims[input_order[item['input_position']]]
        if item['disposition'] == 'uncertain':
            _need(item['output_kind'] == 'questions')
            continue
        _need(original['status'] != 'unknown' and item['output_kind'] != 'questions')
        target = targets[item['output_index']]
        _need(not set(original['span_refs']) - _refs(target))
        if item['output_kind'] == 'global_claims':
            _need(not (original['status'] == 'inferred' and target['status'] == 'explicit'))
        elif original['status'] != 'explicit':
            _need(bool(target['inference_reason'].strip()))


def build_reduction_projection(raw_arguments_bytes, *, catalog, allowed_span_refs,
                               context, input_order, input_items):
    """Check original wire and all frozen declarations without authorizing use.

    input_items contains only the original five-field V3 Map claims, indexed by
    map-N/claims/i. Empty Maps have no entries; their existence and full planned
    coverage remain the caller's provenance obligation. Unresolved originals
    stay in that frozen index and can only be reconciled to explicit questions.
    """
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
        'result': copy.deepcopy(wire['result']),
        'reconciliation': copy.deepcopy(wire['reconciliation']),
        'result_hash': _hash(wire['result']), 'reconciliation_hash': _hash(wire['reconciliation']),
        'provider_delivery_verified': False, 'semantic_quality_verified': False,
        'platform_identity_confirmed': False, 'can_apply': False, 'can_publish': False}
    projection = {**value, 'projection_hash': _hash(value)}
    _need(len(_bytes(projection)) <= MAX_PROJECTION_BYTES)
    return projection


def check_reduction_projection(projection, raw_arguments_bytes, **expected_inputs):
    expected = build_reduction_projection(raw_arguments_bytes, **expected_inputs)
    _need(type(projection) is dict and _bytes(projection) == _bytes(expected))
    return expected
