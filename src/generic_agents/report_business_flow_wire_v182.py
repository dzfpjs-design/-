"""Independent partial business-flow draft, without objects or reconciliation.

The model supplies a small graph and a deduplicated table of original quotes.
This module preserves all selected raw inputs in the platform projection but
does NOT infer that the graph covers their semantics. Literal source matching,
graph consistency and a pure acknowledgement never grant adoption/execution.
"""
import copy
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field

from .contracts import digest
from .report_full_reading_semantics import Closed, _safe
from .report_evidence_plan import resolve_evidence
from .report_evidence_wire_v3 import MapClaimWireV3
from .typed_payload_codec_v131 import canonical_json

BINDING = 'report-business-flow-binding-v182'
WIRE = 'report-business-flow-wire-v182'
PROJECTION = 'report-business-flow-projection-v182'
MANUAL = 'report-business-flow-review-v182'
MAX_WIRE_BYTES = 1024 * 1024
MAX_PROJECTION_BYTES = 8 * 1024 * 1024
MAX_INPUTS = 1920
ERROR = '业务流程的范围、原文引用或连线无法核对；原稿保留，未修写或执行'
_BINDING_KEYS = set(('schema scope_id name plan_hash source_snapshot_hash source_hash catalog_hash '
    'context_hash global_input_count global_input_claims_hash global_input_order_hash '
    'selected_global_positions selected_global_input_ids selected_claims_hash allowed_span_refs_hash spans_hash').split())
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Identifier = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,47}$')]
Text = Annotated[str, Field(min_length=1, max_length=1200, pattern=r'\S')]
Name = Annotated[str, Field(min_length=1, max_length=200, pattern=r'\S')]
OutputName = Annotated[str, Field(min_length=2, max_length=200, pattern=r'\S')]
SpanRef = Annotated[str, Field(pattern=r'^[wo]:p[1-9][0-9]*/s[1-9][0-9]*$', max_length=100)]


def _need(condition):
    if not condition: raise ValueError(ERROR)


def _bytes(value):
    _safe(value)
    return canonical_json(value).encode('utf-8', errors='strict')


def _same(left, right): return _bytes(left) == _bytes(right)


def _hash(value):
    _need(type(value) is str and len(value) == 64 and all(char in '0123456789abcdef' for char in value))


def check_binding(scope_binding):
    """Shape and internal consistency only; no source/actor/plan authority."""
    b = scope_binding
    _need(type(b) is dict and set(b) == _BINDING_KEYS and b['schema'] == BINDING)
    _bytes(b)
    _need(type(b['scope_id']) is str and 1 <= len(b['scope_id']) <= 128
        and b['scope_id'] == b['scope_id'].strip() and all(ord(char) >= 32 for char in b['scope_id'])
        and type(b['name']) is str and 1 <= len(b['name']) <= 200 and bool(b['name'].strip()))
    for key in ('plan_hash', 'source_snapshot_hash', 'source_hash', 'catalog_hash', 'context_hash',
        'global_input_claims_hash', 'global_input_order_hash', 'selected_claims_hash',
        'allowed_span_refs_hash', 'spans_hash'): _hash(b[key])
    _need(type(b['global_input_count']) is int and 1 <= b['global_input_count'] <= MAX_INPUTS)
    positions = b['selected_global_positions']; identities = b['selected_global_input_ids']
    _need(type(positions) is list and 1 <= len(positions) <= b['global_input_count'])
    previous = -1
    for position in positions:
        _need(type(position) is int and previous < position < b['global_input_count']); previous = position
    _need(type(identities) is list and len(identities) == len(positions)
        and all(type(item) is str and bool(item) for item in identities)
        and len(set(identities)) == len(identities))
    return copy.deepcopy(b)


class QuoteV182(Closed):
    span_ref: SpanRef
    quote: str = Field(min_length=1, max_length=1000000)


class AttributionV182(Closed):
    # Both direct readings and proposed inferences retain related original
    # evidence and a required explanation. There is no status-dependent hidden
    # "explicit but citations=[]" validator which the provider cannot see.
    citations: list[SpanRef] = Field(min_length=1, max_length=16)
    inferred: bool
    reason: Text


class QuestionV182(Closed):
    question: Text
    reason: Text
    citations: list[SpanRef] = Field(max_length=16)


class TriggerV182(Closed):
    kind: Literal['manual', 'schedule', 'event', 'data_arrival']
    description: Text
    attribution: AttributionV182


class NodeV182(Closed):
    node_id: Identifier
    name: Name
    actor: Literal['business_agent', 'perception_agent', 'data_agent',
        'task_center', 'execution_service', 'human']
    role: str = Field(min_length=2, max_length=200, pattern=r'\S')
    responsibility: Text
    inputs: list[Name] = Field(max_length=20)
    outputs: list[OutputName] = Field(min_length=1, max_length=20)
    attribution: AttributionV182


class SequenceEdgeV182(Closed):
    kind: Literal['sequence']
    from_node: Identifier
    to_node: Identifier
    attribution: AttributionV182


class ConditionEdgeV182(Closed):
    kind: Literal['condition']
    from_node: Identifier
    to_node: Identifier
    condition: Text
    attribution: AttributionV182


class ParallelEdgeV182(Closed):
    kind: Literal['parallel']
    from_node: Identifier
    to_node: Identifier
    group: Identifier
    attribution: AttributionV182


class WaitEdgeV182(Closed):
    kind: Literal['wait']
    from_nodes: list[Identifier] = Field(min_length=1, max_length=32)
    to_node: Identifier
    mode: Literal['all', 'any']
    attribution: AttributionV182


class DeliverableV182(Closed):
    name: OutputName
    description: Text
    produced_by: list[Identifier] = Field(min_length=1, max_length=32)
    attribution: AttributionV182


class FlowV182(Closed):
    flow_id: Identifier
    name: Name
    trigger: TriggerV182
    start_node: Identifier
    end_nodes: list[Identifier] = Field(min_length=1, max_length=32)
    nodes: list[NodeV182] = Field(min_length=1, max_length=32)
    edges: list[SequenceEdgeV182 | ConditionEdgeV182 | ParallelEdgeV182 | WaitEdgeV182] = Field(max_length=128)
    deliverables: list[DeliverableV182] = Field(min_length=1, max_length=32)


class BusinessDefinitionV182(Closed):
    name: Name
    flows: list[FlowV182] = Field(min_length=1, max_length=16)
    questions: list[QuestionV182] = Field(max_length=80)


class BusinessFlowWireV182(Closed):
    schema: Literal['report-business-flow-wire-v182']
    binding_hash: Hash
    business: BusinessDefinitionV182
    evidence: list[QuoteV182] = Field(min_length=1, max_length=256)


class ManualValueV182(Closed):
    schema: Literal['report-business-flow-review-v182']
    result: BusinessFlowWireV182
    note: str = Field(max_length=12000)
    acknowledge_partial_scope: bool
    acknowledge_inferences: bool


def _schema_for_shape(name, binding_hash):
    """Declaration shape only; it does not verify a saved source binding."""
    _need(type(name) is str and 1 <= len(name) <= 200 and bool(name.strip())); _hash(binding_hash)
    value = BusinessFlowWireV182.model_json_schema()
    value['properties']['binding_hash']['const'] = binding_hash
    value['$defs']['BusinessDefinitionV182']['properties']['name']['const'] = name
    return value


def output_schema(scope_binding):
    b = check_binding(scope_binding)
    return _schema_for_shape(b['name'], digest(b))


def manual_value_schema(scope_binding):
    b = check_binding(scope_binding); value = ManualValueV182.model_json_schema()
    value['$defs']['BusinessFlowWireV182']['properties']['binding_hash']['const'] = digest(b)
    value['$defs']['BusinessDefinitionV182']['properties']['name']['const'] = b['name']
    return value


def _unique(values): _need(len(values) == len(set(values)))


def _attributions(result):
    for flow in result['business']['flows']:
        yield flow['trigger']['attribution']
        for collection in ('nodes', 'edges', 'deliverables'):
            for item in flow[collection]: yield item['attribution']


def _reachable(start, graph):
    seen, pending = set(), list(start)
    while pending:
        item = pending.pop()
        if item not in seen:
            seen.add(item); pending.extend(graph[item] - seen)
    return seen


def _graph(result):
    flows = result['business']['flows']
    _unique([flow['flow_id'] for flow in flows]); _unique([flow['name'] for flow in flows])
    for flow in flows:
        nodes = flow['nodes']; ids = [node['node_id'] for node in nodes]
        _unique(ids); known = set(ids); by_id = {node['node_id']: node for node in nodes}
        _need(flow['start_node'] in known and set(flow['end_nodes']) <= known); _unique(flow['end_nodes'])
        adjacency = {node: set() for node in ids}; reverse = {node: set() for node in ids}
        _unique([canonical_json(edge) for edge in flow['edges']]); parallel = {}
        for edge in flow['edges']:
            origins = edge['from_nodes'] if edge['kind'] == 'wait' else [edge['from_node']]
            _unique(origins); _need(set(origins) <= known and edge['to_node'] in known)
            if edge['kind'] != 'condition': _need(edge['to_node'] not in origins)
            for origin in origins:
                adjacency[origin].add(edge['to_node']); reverse[edge['to_node']].add(origin)
            if edge['kind'] == 'parallel':
                parallel.setdefault(edge['group'], []).append((edge['from_node'], edge['to_node']))
        for members in parallel.values():
            _need(len(members) >= 2 and len({pair[0] for pair in members}) == 1
                and len({pair[1] for pair in members}) == len(members))
        _need(not any(adjacency[node] for node in flow['end_nodes'])
            and _reachable([flow['start_node']], adjacency) == known
            and _reachable(flow['end_nodes'], reverse) == known)
        for node in nodes:
            _unique(node['inputs']); _unique(node['outputs'])
        _unique([item['name'] for item in flow['deliverables']])
        for item in flow['deliverables']:
            _unique(item['produced_by']); _need(set(item['produced_by']) <= known)
            _need(all(item['name'] in by_id[identity]['outputs'] for identity in item['produced_by']))
    evidence_refs = [item['span_ref'] for item in result['evidence']]; _unique(evidence_refs)
    cited = set()
    for attribution in _attributions(result):
        _unique(attribution['citations']); cited.update(attribution['citations'])
    for question in result['business']['questions']:
        _unique(question['citations']); cited.update(question['citations'])
    # The quote table records actual references used in this answer, not an
    # unused bibliography which could be mistaken for semantic coverage.
    _need(cited == set(evidence_refs))
    return result


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result); result[key] = value
    return result


def _constant(_): raise ValueError(ERROR)


def _wire_value(value, scope_binding=None):
    checked = BusinessFlowWireV182.model_validate(value).model_dump()
    _need(_same(checked, value)); _graph(checked)
    if scope_binding is not None:
        b = check_binding(scope_binding)
        _need(checked['binding_hash'] == digest(b) and checked['business']['name'] == b['name'])
    return checked


def parse_wire(raw, *, scope_binding):
    """Only strict structure, actual binding and graph logic; no source proof."""
    try:
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES)
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_pairs, parse_constant=_constant)
        return copy.deepcopy(_wire_value(value, scope_binding))
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def _source(result, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    b = check_binding(scope_binding)
    _need(type(context) is dict and digest(context) == b['context_hash']
        and type(input_claims) is list and len(input_claims) == len(b['selected_global_positions'])
        and digest(input_claims) == b['selected_claims_hash'])
    for claim in input_claims:
        _need(type(claim) is dict and _same(MapClaimWireV3.model_validate(claim).model_dump(), claim))
    spans = resolve_evidence(catalog, allowed_span_refs, allowed_span_refs)
    _need(catalog['descriptor']['source_hash'] == b['source_hash']
        and catalog['descriptor']['catalog_hash'] == b['catalog_hash']
        and digest(allowed_span_refs) == b['allowed_span_refs_hash']
        and digest([[item['span_ref'], item['text']] for item in spans]) == b['spans_hash']
        and set(allowed_span_refs) == set().union(*(set(claim['span_refs']) for claim in input_claims)))
    originals = {item['span_ref']: item['text'] for item in spans}
    for item in result['evidence']:
        _need(item['span_ref'] in originals and item['quote'] == originals[item['span_ref']])
    actual_used = {item['span_ref'] for item in result['evidence']}
    return b, [ref for ref in allowed_span_refs if ref in actual_used]


def build_projection(raw, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    result = parse_wire(raw, scope_binding=scope_binding)
    b, used_refs = _source(result, scope_binding=scope_binding, catalog=catalog,
        allowed_span_refs=allowed_span_refs, context=context, input_claims=input_claims)
    value = {'schema': PROJECTION, 'wire_schema': WIRE, 'wire': result,
        'wire_bytes_sha256': hashlib.sha256(raw).hexdigest(), 'wire_bytes': len(raw),
        'wire_value_hash': digest(result), 'scope_binding': b, 'scope_binding_hash': digest(b),
        'selected_input_claims': copy.deepcopy(input_claims), 'selected_input_count': len(input_claims),
        'selected_claims_hash': digest(input_claims), 'used_refs': used_refs,
        'used_refs_hash': digest(used_refs), 'partial_scope': True,
        'all_input_semantics_verified': False, 'semantic_quality_verified': False,
        'branch_completeness_verified': False, 'whole_source_coverage_verified': False,
        'aggregate_verified': False, 'provider_delivery_verified': False,
        'source_authority_verified': False, 'can_apply': False, 'can_publish': False, 'can_execute': False}
    projection = {**value, 'projection_hash': digest(value)}
    _need(len(_bytes(projection)) <= MAX_PROJECTION_BYTES)
    return projection


def check_projection(value, raw, **expected_inputs):
    expected = build_projection(raw, **expected_inputs)
    _need(type(value) is dict and _same(value, expected)); return expected


def _manual(value):
    checked = ManualValueV182.model_validate(value).model_dump()
    _need(_same(checked, value)); _wire_value(checked['result'])
    return checked


def new_manual(projection):
    """Create a draft after real build_projection; self hashes grant nothing."""
    _need(type(projection) is dict and projection.get('schema') == PROJECTION
        and projection.get('projection_hash') == digest({key: item for key, item in projection.items() if key != 'projection_hash'}))
    result = _wire_value(projection['wire'], projection['scope_binding'])
    return {'schema': MANUAL, 'result': copy.deepcopy(result), 'note': '',
        'acknowledge_partial_scope': False, 'acknowledge_inferences': False}


def validate_value(value, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    """Validate the unchanged human draft against actual independent source."""
    checked = _manual(value); _wire_value(checked['result'], scope_binding)
    _source(checked['result'], scope_binding=scope_binding, catalog=catalog,
        allowed_span_refs=allowed_span_refs, context=context, input_claims=input_claims)
    return copy.deepcopy(checked)


def confirm_value(value):
    """Acknowledgements only; validate_value with actual source FIRST.

    Questions stay unresolved. This is neither persistent human confirmation,
    whole-report acceptance, execution permission nor automatic acknowledgement.
    """
    checked = _manual(value)
    _need(checked['acknowledge_partial_scope'] is True)
    if any(item['inferred'] for item in _attributions(checked['result'])):
        _need(checked['acknowledge_inferences'] is True)
    return copy.deepcopy(checked)
