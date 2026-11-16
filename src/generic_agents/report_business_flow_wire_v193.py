"""Independent nested causal-plan wire and a dedicated human draft contract.

The original model answer remains V193; its plan and every human result remain
the V192 recursive AST. No flattening or reinterpretation as an old paid wire
occurs. Platform sidecars address actual original ports, including duplicates.
Literal source checks and declared availability prove neither useful business
semantics, real entry data, current authority nor execution qualification.
"""
import copy
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from . import business_flow_nested_plan_v192 as plans
from . import business_flow_nested_causal_v192 as causal
from . import report_business_flow_wire_v182 as source_contract
from .contracts import digest
from .report_full_reading_semantics import Closed


WIRE = 'report-business-flow-nested-causal-plan-v193'
PROJECTION = 'report-business-flow-projection-v193'
MANUAL = 'report-business-flow-manual-v193'
BINDING = source_contract.BINDING
MAX_WIRE_BYTES = source_contract.MAX_WIRE_BYTES
MAX_PROJECTION_BYTES = source_contract.MAX_PROJECTION_BYTES
MAX_EVIDENCE = 256
MAX_ADDRESS = plans.MAX_CONTROL_DEPTH + 1  # Three controls and a leaf steps phase.
ERROR = '完整递归流程、来源或因果声明无法核对；原回答保留，未展平、修补或授权'
Index = Annotated[int, Field(strict=True, ge=0)]
Text = Annotated[str, Field(min_length=1, pattern=r'\S')]
_PARTS = ('step', 'decision', 'dispatch', 'repeat_step', 'check', 'completion')
_FLAGS = {'all_input_semantics_verified': False, 'semantic_quality_verified': False,
    'branch_completeness_verified': False, 'whole_source_coverage_verified': False,
    'aggregate_verified': False, 'provider_delivery_verified': False,
    'source_authority_verified': False, 'entry_facts_verified': False,
    'runtime_entry_binding_verified': False, 'permissions_verified': False,
    'external_dispatch_allowed': False, 'whole_report_verified': False,
    'can_apply': False, 'can_publish': False, 'can_execute': False}

check_binding = plans.check_binding


class BusinessFlowNestedCausalCapacityError(ValueError):
    """The complete new value must fit; no silent trimming or partial result."""


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _bytes(value):
    return source_contract._bytes(value)


def _same(left, right):
    return _bytes(left) == _bytes(right)


class PhaseAddressV193(Closed):
    phase_index: Index
    branch_index: Index | None


class ActionSelectorV193(Closed):
    flow_index: Index
    address: list[PhaseAddressV193] = Field(max_length=MAX_ADDRESS)
    part: Literal['step', 'decision', 'dispatch', 'repeat_step', 'check', 'completion']
    step_index: Index | None

    @model_validator(mode='after')
    def address_shape(self):
        if self.part == 'completion':
            _need(not self.address and self.step_index is None)
        else:
            _need(bool(self.address) and self.address[-1].branch_index is None
                and all(item.branch_index is not None for item in self.address[:-1]))
            _need((self.step_index is not None) == (self.part in {'step', 'repeat_step'}))
        return self


class PortContractV193(Closed):
    data_type: Text
    unit: Text | None


class EntryPortV193(Closed):
    flow_index: Index
    contract: PortContractV193


class OutputPortV193(Closed):
    action: ActionSelectorV193
    ordinal: Index
    contract: PortContractV193


class EntrySourceV193(Closed):
    kind: Literal['entry']
    index: Index


class OutputSourceV193(Closed):
    kind: Literal['output']
    action: ActionSelectorV193
    ordinal: Index


class InputBindingV193(Closed):
    action: ActionSelectorV193
    ordinal: Index
    contract: PortContractV193
    sources: list[EntrySourceV193 | OutputSourceV193] = Field(min_length=1)


class CausalityV193(Closed):
    entry_ports: list[EntryPortV193]
    output_contracts: list[OutputPortV193] = Field(min_length=1)
    input_bindings: list[InputBindingV193]


class BusinessFlowNestedCausalPlanV193(Closed):
    schema: Literal['report-business-flow-nested-causal-plan-v193']
    plan: plans.BusinessFlowNestedPlanV192
    causality: CausalityV193


class ManualValueV193(Closed):
    schema: Literal['report-business-flow-manual-v193']
    result: plans.BusinessFlowNestedPlanV192
    note: str = Field(max_length=12000)
    acknowledge_partial_scope: bool
    acknowledge_inferences: bool


def _schema_for_shape(name, binding_hash):
    """Complete declaration shape; no source or provider enforcement claim."""
    _need(type(name) is str and 1 <= len(name) <= 200 and bool(name.strip()))
    source_contract._hash(binding_hash)
    schema = BusinessFlowNestedCausalPlanV193.model_json_schema()
    schema['$defs']['BusinessDefinitionV192']['properties']['name']['const'] = name
    schema['$defs']['BusinessFlowNestedPlanV192']['properties']['binding_hash']['const'] = binding_hash
    # Expose the strict local null/address combinations. Actual branches and
    # ordinals still require traversal of the independently bound real AST.
    selector = schema['$defs']['ActionSelectorV193']
    selector['properties']['address']['oneOf'] = [{'maxItems': 0}] + [
        {'minItems': length, 'maxItems': length, 'prefixItems': [
            {'properties': {'branch_index': {'type': 'null' if i == length - 1 else 'integer'}}}
            for i in range(length)]} for length in range(1, MAX_ADDRESS + 1)]
    selector['oneOf'] = [{'properties': {'part': {'const': part},
        'address': {'maxItems': 0} if part == 'completion' else {'minItems': 1},
        'step_index': {'type': 'integer' if part in {'step', 'repeat_step'} else 'null'}}}
        for part in _PARTS]
    return schema


def output_schema(scope_binding):
    binding = check_binding(scope_binding)
    return _schema_for_shape(binding['name'], digest(binding))


def manual_value_schema(scope_binding):
    binding = check_binding(scope_binding)
    schema = ManualValueV193.model_json_schema()
    schema['$defs']['BusinessDefinitionV192']['properties']['name']['const'] = binding['name']
    schema['$defs']['BusinessFlowNestedPlanV192']['properties']['binding_hash']['const'] = digest(binding)
    return schema


def _action(selector, plan, walked):
    fi, part = selector['flow_index'], selector['part']
    _need(type(fi) is int and 0 <= fi < len(plan['business']['flows']))
    flow = plan['business']['flows'][fi]
    prefix = '/business/flows/' + str(fi)
    address = selector['address']
    if part == 'completion':
        _need(address == [] and selector['step_index'] is None)
        path = prefix + '/completion'
    else:
        _need(bool(address))
        phases = flow['phases']
        for i, item in enumerate(address):
            pi, bi = item['phase_index'], item['branch_index']
            _need(type(pi) is int and 0 <= pi < len(phases))
            phase = phases[pi]
            path = prefix + '/phases/' + str(pi)
            if i != len(address) - 1:
                _need(phase['kind'] in {'decision', 'parallel'} and type(bi) is int
                    and 0 <= bi < len(phase['branches']))
                prefix = path + '/branches/' + str(bi)
                phases = phase['branches'][bi]['phases']
                continue
            _need(bi is None)
            if part in {'step', 'repeat_step'}:
                _need(phase['kind'] == ('steps' if part == 'step' else 'repeat'))
                si = selector['step_index']
                _need(type(si) is int and 0 <= si < len(phase['steps']))
                path += '/steps/' + str(si)
            else:
                _need(selector['step_index'] is None and part in {'decision', 'dispatch', 'check'}
                    and phase['kind'] == {'decision': 'decision', 'dispatch': 'parallel', 'check': 'repeat'}[part])
                path += '/' + part
    matches = [item for item in walked['actions'] if item['path'] == path]
    _need(len(matches) == 1 and matches[0]['part'] == part and matches[0]['flow_index'] == fi)
    return matches[0]


def _port(selector, ordinal, field, plan, walked):
    action = _action(selector, plan, walked)
    _need(type(ordinal) is int and 0 <= ordinal < len(action[field]))
    port = action[field][ordinal]
    _need(port['ordinal'] == ordinal)
    return port['path']


def _sidecar(value, binding):
    plan, declared = value['plan'], value['causality']
    walked = plans.walk_action_ports(plan, scope_binding=binding)
    entries = []
    for i, entry in enumerate(declared['entry_ports']):
        _need(entry['flow_index'] < len(plan['business']['flows']))
        entries.append({'path': '/entry_ports/' + str(i),
            'flow_path': '/business/flows/' + str(entry['flow_index']),
            'contract': copy.deepcopy(entry['contract'])})
    outputs = [{'path': _port(item['action'], item['ordinal'], 'output_ports', plan, walked),
        'contract': copy.deepcopy(item['contract'])} for item in declared['output_contracts']]
    inputs = []
    for item in declared['input_bindings']:
        sources = []
        for source in item['sources']:
            if source['kind'] == 'entry':
                _need(source['index'] < len(entries))
                sources.append(entries[source['index']]['path'])
            else:
                _need(source['kind'] == 'output')
                sources.append(_port(source['action'], source['ordinal'], 'output_ports', plan, walked))
        inputs.append({'input_path': _port(item['action'], item['ordinal'], 'input_ports', plan, walked),
            'contract': copy.deepcopy(item['contract']), 'sources': sources})
    sidecar = {'schema': causal.BINDINGS, 'source_plan_hash': digest(plan),
        'scope_binding_hash': digest(binding), 'entry_ports': entries,
        'output_contracts': outputs, 'input_bindings': inputs}
    return causal.check_bindings(plan, sidecar, scope_binding=binding)


def parse_plan(raw, *, scope_binding):
    """Exact V193 structure, real recursive addresses and closed port coverage."""
    try:
        binding = check_binding(scope_binding)
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES)
        original = json.loads(raw.decode('utf-8', errors='strict'),
            object_pairs_hook=source_contract._pairs, parse_constant=source_contract._constant)
        _need(type(original) is dict and set(original) == {'schema', 'plan', 'causality'}
            and original['schema'] == WIRE)
        # Bound the complete recursive tree BEFORE the outer model descends
        # into it. The recursive AST's own pure preflight is the authority for
        # depth, phase/action totals and exact typed plan identity.
        actual_plan = plans.check_plan(original['plan'], scope_binding=binding)
        checked = BusinessFlowNestedCausalPlanV193.model_validate(original).model_dump()
        _need(_same(original, checked))
        _need(_same(actual_plan, checked['plan']))
        _sidecar(checked, binding)
        return copy.deepcopy(checked)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def _source(plan, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    """Source-only adapter, not an old graph validator for a nested plan."""
    references = plans.attribution_refs(plan)  # Includes all control/branch/question attributions.
    spans = source_contract.resolve_evidence(catalog, allowed_span_refs, allowed_span_refs)
    originals = {item['span_ref']: item['text'] for item in spans}
    _need(set(references) <= set(allowed_span_refs) and set(references) <= set(originals))
    evidence = [{'span_ref': ref, 'quote': originals[ref]} for ref in allowed_span_refs if ref in references]
    if len(evidence) > MAX_EVIDENCE:
        raise BusinessFlowNestedCausalCapacityError('完整原文依据超过256条；未删除引用或形成部分结果')
    for item in evidence:
        typed = source_contract.QuoteV182.model_validate(item).model_dump()
        _need(_same(typed, item))
    # Reuse the actual frozen context/claim/catalog/allowed/spans binding and
    # exact-quote gate only. Never submit this recursive AST to V182._graph.
    binding, used_refs = source_contract._source({'evidence': evidence}, scope_binding=scope_binding,
        catalog=catalog, allowed_span_refs=allowed_span_refs, context=context, input_claims=input_claims)
    return binding, evidence, used_refs


def validate_wire_value(value, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    """Check a nested HUMAN result against independent actual source inputs.

    No causal declarations accompany this value. It cannot inherit the original
    model's analysis merely because shape and literal citations remain valid.
    """
    try:
        _need(type(value) is dict and len(_bytes(value)) <= MAX_WIRE_BYTES)
        checked = plans.check_plan(value, scope_binding=scope_binding)
        _source(checked, scope_binding=scope_binding, catalog=catalog, allowed_span_refs=allowed_span_refs,
            context=context, input_claims=input_claims)
        return copy.deepcopy(checked)
    except BusinessFlowNestedCausalCapacityError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def _counts(plan, walked):
    def count(phases):
        return sum(1 + sum(count(branch['phases']) for branch in phase.get('branches', [])) for phase in phases)
    return {'flow_count': len(plan['business']['flows']),
        'phase_count': sum(count(flow['phases']) for flow in plan['business']['flows']),
        'action_count': len(walked['actions']), 'input_count': len(walked['input_paths']),
        'output_count': len(walked['output_paths'])}


def build_projection(raw, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    value = parse_plan(raw, scope_binding=scope_binding)
    binding, evidence, used_refs = _source(value['plan'], scope_binding=scope_binding,
        catalog=catalog, allowed_span_refs=allowed_span_refs, context=context, input_claims=input_claims)
    sidecar = _sidecar(value, binding)
    analysis = causal.analyze_availability(value['plan'], sidecar, scope_binding=binding)
    _need(analysis['consistent'] is True and analysis['declared_inputs_available'] is True
        and analysis['causal_declaration_consistency_only'] is True)
    plan_bytes = _bytes(value['plan'])
    result = {'schema': PROJECTION, 'wire_schema': plans.PLAN, 'wire': copy.deepcopy(value['plan']),
        'wire_value_hash': digest(value['plan']),
        'wire_canonical_bytes_sha256': hashlib.sha256(plan_bytes).hexdigest(), 'wire_canonical_bytes': len(plan_bytes),
        'raw_plan_schema': plans.PLAN, 'raw_plan': copy.deepcopy(value['plan']),
        'raw_plan_value_hash': digest(value['plan']), 'raw_causal_plan_schema': WIRE,
        'raw_causal_plan': value, 'raw_causal_plan_value_hash': digest(value),
        'raw_causal_plan_bytes_sha256': hashlib.sha256(raw).hexdigest(), 'raw_causal_plan_bytes': len(raw),
        'scope_binding': binding, 'scope_binding_hash': digest(binding),
        'selected_input_claims': copy.deepcopy(input_claims), 'selected_input_count': len(input_claims),
        'selected_claims_hash': digest(input_claims), 'evidence': evidence, 'evidence_hash': digest(evidence),
        'quote_origin': 'platform_resolved_source', 'used_refs': used_refs, 'used_refs_hash': digest(used_refs),
        'compiled_sidecar': sidecar, 'compiled_sidecar_hash': digest(sidecar),
        'causal_analysis': analysis, 'causal_analysis_hash': analysis['availability_hash'],
        'plan_counts': _counts(value['plan'], analysis['paths']), 'partial_scope': True,
        'entry_contracts_are_proposals': True, 'causal_declaration_consistency_only': True, **_FLAGS}
    result['projection_hash'] = digest(result)
    if len(_bytes(result)) > MAX_PROJECTION_BYTES:
        raise BusinessFlowNestedCausalCapacityError('完整递归计划、原文和因果声明超过8MiB；未截断')
    return result


def check_projection(value, raw, **expected_inputs):
    expected = build_projection(raw, **expected_inputs)
    _need(type(value) is dict and _same(value, expected))
    return expected


def new_manual(projection):
    """Make an unconfirmed independent draft after caller cold-source checking."""
    _need(type(projection) is dict and projection.get('schema') == PROJECTION
        and projection.get('projection_hash') == digest({key: item for key, item in projection.items()
            if key != 'projection_hash'}) and len(_bytes(projection)) <= MAX_PROJECTION_BYTES
        and all(projection.get(key) is False for key in _FLAGS)
        and projection.get('partial_scope') is True and projection.get('entry_contracts_are_proposals') is True
        and projection.get('causal_declaration_consistency_only') is True)
    binding = check_binding(projection['scope_binding'])
    value = parse_plan(_bytes(projection['raw_causal_plan']), scope_binding=binding)
    sidecar = _sidecar(value, binding)
    analysis = causal.analyze_availability(value['plan'], sidecar, scope_binding=binding)
    canonical = _bytes(value['plan'])
    references = set(plans.attribution_refs(value['plan']))
    evidence = projection['evidence']
    _need(type(evidence) is list and 1 <= len(evidence) <= MAX_EVIDENCE)
    refs = []
    for item in evidence:
        _need(_same(source_contract.QuoteV182.model_validate(item).model_dump(), item))
        refs.append(item['span_ref'])
    _need(len(set(refs)) == len(refs) and set(refs) == references
        and projection['wire_schema'] == projection['raw_plan_schema'] == plans.PLAN
        and projection['raw_causal_plan_schema'] == WIRE
        and _same(projection['wire'], value['plan']) and _same(projection['raw_plan'], value['plan'])
        and projection['wire_value_hash'] == projection['raw_plan_value_hash'] == digest(value['plan'])
        and projection['wire_canonical_bytes_sha256'] == hashlib.sha256(canonical).hexdigest()
        and type(projection['wire_canonical_bytes']) is int and projection['wire_canonical_bytes'] == len(canonical)
        and projection['raw_causal_plan_value_hash'] == digest(value)
        and type(projection['raw_causal_plan_bytes']) is int
        and 0 < projection['raw_causal_plan_bytes'] <= MAX_WIRE_BYTES
        and projection['scope_binding_hash'] == digest(binding)
        and _same(projection['compiled_sidecar'], sidecar)
        and projection['compiled_sidecar_hash'] == digest(sidecar)
        and _same(projection['causal_analysis'], analysis)
        and projection['causal_analysis_hash'] == analysis['availability_hash']
        and _same(projection['plan_counts'], _counts(value['plan'], analysis['paths']))
        and projection['evidence_hash'] == digest(evidence) and projection['quote_origin'] == 'platform_resolved_source'
        and projection['used_refs'] == refs and projection['used_refs_hash'] == digest(refs)
        and projection['selected_claims_hash'] == digest(projection['selected_input_claims']) == binding['selected_claims_hash']
        and type(projection['selected_input_count']) is int
        and projection['selected_input_count'] == len(projection['selected_input_claims']) == len(binding['selected_global_positions']))
    source_contract._hash(projection['raw_causal_plan_bytes_sha256'])
    # Exact original raw bytes/capture and independent source inputs are checked
    # by check_projection at the caller boundary, never inferred from self-hash.
    return {'schema': MANUAL, 'result': copy.deepcopy(value['plan']), 'note': '',
        'acknowledge_partial_scope': False, 'acknowledge_inferences': False}


def _manual(value):
    _need(type(value) is dict and set(value) == {'schema', 'result', 'note',
        'acknowledge_partial_scope', 'acknowledge_inferences'} and value['schema'] == MANUAL)
    plans._typed_plan(value['result'])
    checked = ManualValueV193.model_validate(value).model_dump()
    _need(_same(checked, value) and len(_bytes(checked)) <= MAX_WIRE_BYTES)
    # The real shared pure capacity/typed-shape gate, without inventing a scope
    # binding for the no-source confirm_value API. No source authority follows.
    return checked


def validate_value(value, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    checked = _manual(value)
    validate_wire_value(checked['result'], scope_binding=scope_binding, catalog=catalog,
        allowed_span_refs=allowed_span_refs, context=context, input_claims=input_claims)
    return copy.deepcopy(checked)


def _attributions(plan):
    def phases(items):
        for phase in items:
            yield phase['attribution']
            if phase['kind'] in {'steps', 'repeat'}:
                yield from (action['attribution'] for action in phase['steps'])
                if phase['kind'] == 'repeat':
                    yield phase['check']['attribution']
            else:
                yield phase['decision' if phase['kind'] == 'decision' else 'dispatch']['attribution']
                if phase['kind'] == 'parallel':
                    yield phase['join_attribution']
                for branch in phase['branches']:
                    if phase['kind'] == 'decision':
                        yield branch['attribution']
                    yield from phases(branch['phases'])
    for flow in plan['business']['flows']:
        yield flow['trigger']['attribution']
        yield flow['phase_order']
        yield from phases(flow['phases'])
        yield flow['completion']['attribution']


def confirm_value(value):
    """Acknowledgements only, after validate_value; no causal or runtime grant."""
    checked = _manual(value)
    _need(checked['acknowledge_partial_scope'] is True)
    if any(item['inferred'] for item in _attributions(checked['result'])):
        _need(checked['acknowledge_inferences'] is True)
    return copy.deepcopy(checked)
