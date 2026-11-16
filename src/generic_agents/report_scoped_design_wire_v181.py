"""Strict partial-scope V166 candidate, never an entire-report adoption source.

Original local groups stay unchanged. A detached platform validation view uses
continuous local Map IDs and the complete existing V174/V166 quality gates.
Only its verified local coverage is mapped back to frozen global positions.
"""
import copy
import hashlib
import json
from typing import Annotated
from pydantic import Field
from . import report_design_grouped_wire_v174 as quality
from . import report_evidence_reduction_wire_v168 as original
from . import report_indexed_inputs_v178 as codec
from .report_evidence_wire_v3 import ReconciliationWireV5
from .report_full_reading_semantics import Closed
from .report_object_semantics_v166 import CandidateObjects
from .report_evidence_plan import resolve_evidence

WIRE = 'report-scoped-design-wire-v181'
PROJECTION = 'report-scoped-design-projection-v181'
EXPANSION = 'report-scoped-design-expansion-v181'
BINDING = 'report-scoped-design-binding-v181'
MAX_WIRE_BYTES = original.MAX_WIRE_BYTES
MAX_PROJECTION_BYTES = original.MAX_PROJECTION_BYTES
MAX_INPUTS = codec.MAX_INPUTS
ERROR = '单业务候选、局部编号或原范围无法核对；原回答保留，未修写、拼接或采用'
_BINDING_KEYS = set(('schema scope_id scope_name plan_hash scope_ledger_hash source_snapshot_hash '
    'source_hash catalog_hash context_hash global_input_count global_indexed_inputs_hash '
    'global_input_claims_hash global_input_order_hash local_to_global global_input_ids '
    'input_count indexed_inputs_hash input_claims_hash model_payload_hash selection_roles '
    'unanchored_input_positions allowed_span_refs_hash spans_hash').split())

def _need(condition):
    if not condition: raise ValueError(ERROR)

def _hash_string(value):
    _need(type(value) is str and len(value) == 64 and all(char in '0123456789abcdef' for char in value))

def _scope_id(value):
    _need(type(value) is str and 1 <= len(value) <= 128 and value == value.strip()
        and all(ord(char) >= 32 for char in value))

def check_scope_binding(scope_binding, indexed, *, input_claims, source_snapshot_hash):
    """Supplied consistency only; preparation separately checks the full cold plan."""
    frozen = codec.check_indexed_inputs(indexed, input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
    value = scope_binding
    _need(type(value) is dict and set(value) == _BINDING_KEYS and value['schema'] == BINDING)
    codec.canonical(value); _scope_id(value['scope_id'])
    _need(type(value['scope_name']) is str and 1 <= len(value['scope_name']) <= 200 and bool(value['scope_name'].strip()))
    for key in ('plan_hash', 'scope_ledger_hash', 'source_snapshot_hash', 'source_hash', 'catalog_hash',
        'context_hash', 'global_indexed_inputs_hash', 'global_input_claims_hash', 'global_input_order_hash',
        'indexed_inputs_hash', 'input_claims_hash', 'model_payload_hash', 'allowed_span_refs_hash', 'spans_hash'): _hash_string(value[key])
    count = frozen['input_count']
    _need(type(value['global_input_count']) is int and count <= value['global_input_count'] <= MAX_INPUTS
        and type(value['input_count']) is int and value['input_count'] == count
        and value['source_snapshot_hash'] == source_snapshot_hash
        and value['indexed_inputs_hash'] == frozen['inputs_hash']
        and value['input_claims_hash'] == frozen['input_claims_hash']
        and value['model_payload_hash'] == frozen['model_payload_hash'])
    positions = value['local_to_global']; previous = -1
    _need(type(positions) is list and len(positions) == count)
    for position in positions:
        _need(type(position) is int and previous < position < value['global_input_count']); previous = position
    ids = value['global_input_ids']
    _need(type(ids) is list and len(ids) == count and all(type(item) is str and bool(item) for item in ids)
        and len(set(ids)) == count)
    roles = value['selection_roles']
    _need(type(roles) is list and len(roles) == count
        and all(type(role) is str and role in {'primary', 'shared', 'context'} for role in roles))
    unanchored = [i for i, claim in enumerate(input_claims) if not claim['span_refs']]
    _need(type(value['unanchored_input_positions']) is list
        and codec.canonical(value['unanchored_input_positions']) == codec.canonical(unanchored))
    return copy.deepcopy(value)

class ReconciliationGroupV181(Closed):
    output_kind: ReconciliationWireV5.model_fields['output_kind'].annotation
    output_index: Annotated[int, Field(strict=True, ge=0)]
    disposition: ReconciliationWireV5.model_fields['disposition'].annotation
    input_positions: list[Annotated[int, Field(strict=True, ge=0, le=MAX_INPUTS - 1)]] = Field(min_length=1, max_length=MAX_INPUTS)
    reason: str = Field(min_length=1, max_length=1000)

class ScopedDesignWireV181(Closed):
    scope_id: str = Field(min_length=1, max_length=128)
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    result: CandidateObjects
    input_count: Annotated[int, Field(strict=True, ge=1, le=MAX_INPUTS)]
    reconciliation_groups: list[ReconciliationGroupV181] = Field(min_length=1, max_length=MAX_INPUTS)

def _schema_for_shape(count, scope_id, plan_hash, scope_name, source_hash, catalog_hash):
    _need(type(count) is int and 1 <= count <= MAX_INPUTS); _scope_id(scope_id)
    for value in (plan_hash, source_hash, catalog_hash): _hash_string(value)
    _need(type(scope_name) is str and 1 <= len(scope_name) <= 200 and bool(scope_name.strip()))
    schema = ScopedDesignWireV181.model_json_schema()
    schema['properties']['scope_id']['const'] = scope_id; schema['properties']['plan_hash']['const'] = plan_hash
    schema['properties']['input_count'].update(const=count, minimum=count, maximum=count)
    schema['properties']['reconciliation_groups']['maxItems'] = count
    positions = schema['$defs']['ReconciliationGroupV181']['properties']['input_positions']
    positions.update(maxItems=count, uniqueItems=True); positions['items']['maximum'] = count - 1
    candidate = schema['$defs']['CandidateObjects']['properties']
    candidate['source_hash']['const'] = source_hash; candidate['catalog_hash']['const'] = catalog_hash
    candidate['businesses'].update(minItems=1, maxItems=1)
    schema['$defs']['BusinessWireV5']['properties']['name']['const'] = scope_name
    return schema

def output_schema(indexed, *, input_claims, source_snapshot_hash, scope_binding):
    b = check_scope_binding(scope_binding, indexed, input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
    return _schema_for_shape(b['input_count'], b['scope_id'], b['plan_hash'], b['scope_name'], b['source_hash'], b['catalog_hash'])

def parse_wire(raw_arguments_bytes, *, indexed, input_claims, source_snapshot_hash, scope_binding):
    _need(type(raw_arguments_bytes) is bytes and 0 < len(raw_arguments_bytes) <= MAX_WIRE_BYTES)
    try:
        b = check_scope_binding(scope_binding, indexed, input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
        value = json.loads(raw_arguments_bytes.decode('utf-8', errors='strict'), object_pairs_hook=original._pairs,
            parse_constant=original._constant, parse_float=original._number)
        checked = ScopedDesignWireV181.model_validate(value).model_dump()
        _need(original._bytes(value) == original._bytes(checked)
            and value['scope_id'] == b['scope_id'] and value['plan_hash'] == b['plan_hash']
            and value['result']['source_hash'] == b['source_hash'] and value['result']['catalog_hash'] == b['catalog_hash']
            and len(value['result']['businesses']) == 1 and value['result']['businesses'][0]['name'] == b['scope_name'])
        codec.validate_reconciliation({key: value[key] for key in ('input_count', 'reconciliation_groups')},
            indexed, input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
        return copy.deepcopy(value)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None

def build_reduction_projection(raw_arguments_bytes, *, catalog, allowed_span_refs, context,
                               indexed, input_claims, source_snapshot_hash, scope_binding):
    b = check_scope_binding(scope_binding, indexed, input_claims=input_claims, source_snapshot_hash=source_snapshot_hash)
    value = parse_wire(raw_arguments_bytes, indexed=indexed, input_claims=input_claims,
        source_snapshot_hash=source_snapshot_hash, scope_binding=b)
    spans = [[item['span_ref'], item['text']] for item in resolve_evidence(catalog, allowed_span_refs, allowed_span_refs)]
    _need(original._hash(context) == b['context_hash'] and original._hash(allowed_span_refs) == b['allowed_span_refs_hash']
        and original._hash(spans) == b['spans_hash'])
    # Explicit PLATFORM IDs only. Never relabel sparse original Map IDs as
    # contiguous original input. Their actual IDs are retained in the binding.
    items = {f'map-{i // 80 + 1}/claims/{i % 80}': copy.deepcopy(claim) for i, claim in enumerate(input_claims)}
    derived = {'result': copy.deepcopy(value['result']), 'reconciliation_groups': copy.deepcopy(value['reconciliation_groups'])}
    derived_bytes = original._bytes(derived)
    validated = quality.build_reduction_projection(derived_bytes, catalog=catalog,
        allowed_span_refs=allowed_span_refs, context=context, input_order=list(items), input_items=items)
    local = copy.deepcopy(validated['expanded_reconciliation'])
    mapped = [{**copy.deepcopy(item), 'global_input_position': b['local_to_global'][item['input_position']],
        'global_input_id': b['global_input_ids'][item['input_position']]} for item in local]
    expansion = {'schema': EXPANSION, 'scope_binding_hash': original._hash(b),
        'local_reconciliation': local, 'global_reconciliation': mapped}
    result = {'schema': PROJECTION, 'wire_schema': WIRE, 'scope_id': b['scope_id'], 'plan_hash': b['plan_hash'],
        'scope_binding': b, 'scope_binding_hash': original._hash(b), 'source_snapshot_hash': source_snapshot_hash,
        'input_count': value['input_count'], 'source_hash': b['source_hash'], 'catalog_hash': b['catalog_hash'],
        'allowed_span_refs_hash': original._hash(allowed_span_refs), 'context_hash': original._hash(context),
        'wire': copy.deepcopy(value), 'wire_bytes_sha256': hashlib.sha256(raw_arguments_bytes).hexdigest(),
        'wire_bytes': len(raw_arguments_bytes), 'wire_value_hash': original._hash(value),
        'indexed_inputs_hash': indexed['inputs_hash'], 'input_claims_hash': original._hash(input_claims),
        'result': copy.deepcopy(value['result']), 'result_hash': original._hash(value['result']),
        'reconciliation_groups': copy.deepcopy(value['reconciliation_groups']),
        'expanded_local_reconciliation': local, 'mapped_global_reconciliation': mapped,
        'expansion_schema': EXPANSION, 'expansion_hash': original._hash(expansion),
        'platform_quality_view_schema': quality.PROJECTION,
        'platform_quality_view_wire_sha256': hashlib.sha256(derived_bytes).hexdigest(),
        'platform_quality_view_hash': validated['projection_hash'], 'partial_scope': True,
        'aggregate_verified': False, 'whole_source_coverage_verified': False, 'source_anchors_verified': False,
        'unanchored_input_positions': b['unanchored_input_positions'], 'provider_delivery_verified': False,
        'semantic_quality_verified': False, 'platform_identity_confirmed': False, 'can_apply': False, 'can_publish': False}
    projection = {**result, 'projection_hash': original._hash(result)}
    _need(len(original._bytes(projection)) <= MAX_PROJECTION_BYTES)
    return projection

def check_reduction_projection(projection, raw_arguments_bytes, **expected_inputs):
    expected = build_reduction_projection(raw_arguments_bytes, **expected_inputs)
    _need(type(projection) is dict and original._bytes(projection) == original._bytes(expected))
    return expected
