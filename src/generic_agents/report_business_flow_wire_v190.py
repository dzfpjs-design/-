"""Independent causal-plan wire over the unchanged V184 AST and V189 checks.

Selectors address original action/port ordinals, never names or the compiler's
set-normalized arrays. Entry contracts are proposals, not real trigger data,
permissions, tool bindings, semantic correctness or adoption authority.
"""
import copy
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from . import report_business_flow_wire_v182 as base
from . import report_business_flow_wire_v184 as plans
from . import business_flow_causal_paths_v189 as causal
from .contracts import digest
from .report_full_reading_semantics import Closed


WIRE = 'report-business-flow-causal-plan-v190'
PROJECTION = 'report-business-flow-projection-v190'
BINDING = plans.BINDING
MANUAL = base.MANUAL
MAX_WIRE_BYTES = plans.MAX_WIRE_BYTES
MAX_PROJECTION_BYTES = plans.MAX_PROJECTION_BYTES
ERROR = '完整流程、逻辑动作位置或因果声明无法核对；原答保留，未猜测、截断或授权'
Index = Annotated[int, Field(strict=True, ge=0)]
Text = Annotated[str, Field(min_length=1, pattern=r'\S')]
_PARTS = ('step', 'decision', 'dispatch', 'branch_step', 'repeat_step', 'check', 'completion')
_FLAGS = {'all_input_semantics_verified': False, 'semantic_quality_verified': False,
    'branch_completeness_verified': False, 'whole_source_coverage_verified': False,
    'aggregate_verified': False, 'source_authority_verified': False,
    'entry_facts_verified': False, 'permissions_verified': False,
    'external_dispatch_allowed': False, 'whole_report_verified': False,
    'can_apply': False, 'can_publish': False, 'can_execute': False}

check_binding = plans.check_binding
manual_value_schema = base.manual_value_schema
validate_value = base.validate_value
confirm_value = base.confirm_value


class BusinessFlowCausalCapacityError(ValueError):
    """The complete new answer/projection must fit; no partial recovery here."""


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _bytes(value):
    return base._bytes(value)


def _same(left, right):
    return _bytes(left) == _bytes(right)


class ActionSelectorV190(Closed):
    flow_index: Index
    phase_index: Index | None
    part: Literal['step', 'decision', 'dispatch', 'branch_step', 'repeat_step', 'check', 'completion']
    branch_index: Index | None
    step_index: Index | None

    @model_validator(mode='after')
    def actual_part_shape(self):
        if self.part == 'completion':
            _need(self.phase_index is None and self.branch_index is None and self.step_index is None)
        else:
            _need(self.phase_index is not None)
            _need((self.branch_index is not None) == (self.part == 'branch_step'))
            _need((self.step_index is not None) == (self.part in {'step', 'repeat_step', 'branch_step'}))
        return self


class PortContractV190(Closed):
    data_type: Text
    unit: Text | None


class EntryPortV190(Closed):
    flow_index: Index
    contract: PortContractV190


class OutputPortV190(Closed):
    action: ActionSelectorV190
    ordinal: Index
    contract: PortContractV190


class EntrySourceV190(Closed):
    kind: Literal['entry']
    index: Index


class OutputSourceV190(Closed):
    kind: Literal['output']
    action: ActionSelectorV190
    ordinal: Index


class InputBindingV190(Closed):
    action: ActionSelectorV190
    ordinal: Index
    contract: PortContractV190
    sources: list[EntrySourceV190 | OutputSourceV190] = Field(min_length=1)


class CausalityV190(Closed):
    entry_ports: list[EntryPortV190]
    output_contracts: list[OutputPortV190] = Field(min_length=1)
    input_bindings: list[InputBindingV190]


class BusinessFlowCausalPlanV190(Closed):
    schema: Literal['report-business-flow-causal-plan-v190']
    plan: plans.BusinessFlowPlanV184
    causality: CausalityV190


def _schema_for_shape(name, binding_hash):
    _need(type(name) is str and 1 <= len(name) <= 200 and bool(name.strip()))
    base._hash(binding_hash)
    schema = BusinessFlowCausalPlanV190.model_json_schema()
    schema['$defs']['BusinessDefinitionV184']['properties']['name']['const'] = name
    schema['$defs']['BusinessFlowPlanV184']['properties']['binding_hash']['const'] = binding_hash
    # Expose the same null/position combinations checked by the local model.
    # This does not claim that a provider enforces JSON Schema or real paths.
    schema['$defs']['ActionSelectorV190']['oneOf'] = [
        {'properties': {'part': {'const': part},
            'phase_index': {'type': 'null' if part == 'completion' else 'integer'},
            'branch_index': {'type': 'integer' if part == 'branch_step' else 'null'},
            'step_index': {'type': 'integer' if part in {'step', 'repeat_step', 'branch_step'} else 'null'}}}
        for part in _PARTS]
    return schema


def output_schema(scope_binding):
    binding = check_binding(scope_binding)
    return _schema_for_shape(binding['name'], digest(binding))


def _action(selector, walked):
    flow_path = '/business/flows/' + str(selector['flow_index'])
    part = selector['part']
    if part == 'completion':
        path = flow_path + '/completion'
    else:
        phase_path = flow_path + '/phases/' + str(selector['phase_index'])
        if part in {'step', 'repeat_step'}:
            path = phase_path + '/steps/' + str(selector['step_index'])
        elif part == 'branch_step':
            path = phase_path + '/branches/' + str(selector['branch_index']) + '/steps/' + str(selector['step_index'])
        else:
            path = phase_path + '/' + part
    matches = [item for item in walked['actions'] if item['path'] == path]
    _need(len(matches) == 1 and matches[0]['part'] == part
        and matches[0]['flow_index'] == selector['flow_index']
        and matches[0]['phase_index'] == selector['phase_index'])
    return matches[0]


def _port(selector, ordinal, field, walked):
    action = _action(selector, walked)
    _need(type(ordinal) is int and 0 <= ordinal < len(action[field]))
    port = action[field][ordinal]
    _need(port['ordinal'] == ordinal)
    return port['path']


def _sidecar(value, binding):
    plan, declared = value['plan'], value['causality']
    walked = causal.walk_action_ports(plan, scope_binding=binding)
    entries = []
    for index, entry in enumerate(declared['entry_ports']):
        _need(entry['flow_index'] < len(plan['business']['flows']))
        entries.append({'path': '/entry_ports/' + str(index),
            'flow_path': '/business/flows/' + str(entry['flow_index']),
            'contract': copy.deepcopy(entry['contract'])})
    outputs = [{'path': _port(item['action'], item['ordinal'], 'output_ports', walked),
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
                sources.append(_port(source['action'], source['ordinal'], 'output_ports', walked))
        inputs.append({'input_path': _port(item['action'], item['ordinal'], 'input_ports', walked),
            'contract': copy.deepcopy(item['contract']), 'sources': sources})
    sidecar = {'schema': causal.BINDINGS, 'source_plan_hash': digest(plan),
        'scope_binding_hash': digest(binding), 'entry_ports': entries,
        'output_contracts': outputs, 'input_bindings': inputs}
    # Reuse the frozen V189 closed/full-coverage gate, not a parallel relaxed
    # implementation. Actual causal availability is required at projection.
    return causal._bindings(sidecar, plan, binding, walked)


def parse_plan(raw, *, scope_binding):
    """Strict V190 values/selectors/coverage; no source/availability claim."""
    try:
        binding = check_binding(scope_binding)
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES)
        original = json.loads(raw.decode('utf-8', errors='strict'),
            object_pairs_hook=base._pairs, parse_constant=base._constant)
        checked = BusinessFlowCausalPlanV190.model_validate(original).model_dump()
        _need(_same(original, checked))
        actual_plan = plans.parse_plan(_bytes(checked['plan']), scope_binding=binding)
        _need(_same(actual_plan, checked['plan']))
        _sidecar(checked, binding)
        return copy.deepcopy(checked)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def build_projection(raw, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    value = parse_plan(raw, scope_binding=scope_binding)
    binding = check_binding(scope_binding)
    projected = plans.build_projection(_bytes(value['plan']), scope_binding=binding,
        catalog=catalog, allowed_span_refs=allowed_span_refs, context=context, input_claims=input_claims)
    sidecar = _sidecar(value, binding)
    analysis = causal.analyze_availability(value['plan'], sidecar, scope_binding=binding)
    _need(analysis['availability']['declared_inputs_available'] is True
        and analysis['causal_declaration_consistency_only'] is True)
    result = {key: copy.deepcopy(item) for key, item in projected.items() if key not in {'schema', 'projection_hash'}}
    result.update(schema=PROJECTION, raw_causal_plan_schema=WIRE, raw_causal_plan=value,
        raw_causal_plan_value_hash=digest(value), raw_causal_plan_bytes_sha256=hashlib.sha256(raw).hexdigest(),
        raw_causal_plan_bytes=len(raw), compiled_sidecar=sidecar, compiled_sidecar_hash=digest(sidecar),
        causal_analysis=analysis, causal_analysis_hash=analysis['analysis_hash'],
        plan_projection=projected, plan_projection_hash=projected['projection_hash'],
        causal_declaration_consistency_only=True, **_FLAGS)
    result['projection_hash'] = digest(result)
    if len(_bytes(result)) > MAX_PROJECTION_BYTES:
        raise BusinessFlowCausalCapacityError('完整因果计划、编译结果和声明超过8MiB；未截断或生成部分投影')
    return result


def check_projection(value, raw, **expected_inputs):
    expected = build_projection(raw, **expected_inputs)
    _need(type(value) is dict and _same(value, expected))
    return expected


def new_manual(projection):
    """Create only an unconfirmed V182 draft, after independent cold checking.

    Editing this separate manual value does not update or inherit the original
    candidate's causal analysis. Fresh author/execution gates remain separate.
    """
    _need(type(projection) is dict and projection.get('schema') == PROJECTION
        and projection.get('projection_hash') == digest({key: value for key, value in projection.items()
            if key != 'projection_hash'})
        and projection.get('causal_declaration_consistency_only') is True
        and all(projection.get(key) is False for key in _FLAGS))
    binding = check_binding(projection['scope_binding'])
    value = parse_plan(_bytes(projection['raw_causal_plan']), scope_binding=binding)
    sidecar = _sidecar(value, binding)
    analysis = causal.analyze_availability(value['plan'], sidecar, scope_binding=binding)
    inner = projection['plan_projection']
    _need(projection['raw_causal_plan_value_hash'] == digest(value)
        and _same(projection['raw_plan'], value['plan'])
        and _same(projection['compiled_sidecar'], sidecar)
        and projection['compiled_sidecar_hash'] == digest(sidecar)
        and _same(projection['causal_analysis'], analysis)
        and projection['causal_analysis_hash'] == analysis['analysis_hash']
        and inner['schema'] == plans.PROJECTION
        and inner['projection_hash'] == digest({key: item for key, item in inner.items() if key != 'projection_hash'})
        and projection['plan_projection_hash'] == inner['projection_hash']
        and _same(inner['raw_plan'], value['plan']) and _same(inner['scope_binding'], binding)
        and _same(projection['wire'], inner['wire'])
        and projection['wire_value_hash'] == digest(inner['wire']))
    view = {'schema': base.PROJECTION, 'wire': copy.deepcopy(projection['wire']),
        'scope_binding': copy.deepcopy(binding)}
    view['projection_hash'] = digest(view)
    return base.new_manual(view)
