"""Bounded recursive planning and exact original ports, with no authority.

Old paid ASTs/compilers are unchanged. A nested branch contains phases, never
an implicitly flattened step list. All counts include every declared branch;
source citations remain declarations until independently resolved by callers.
"""
from __future__ import annotations

import copy
import json
from typing import Annotated, Literal

from pydantic import Field

from . import report_business_flow_wire_v182 as base
from . import report_business_flow_wire_v184 as original
from .contracts import digest
from .report_full_reading_semantics import Closed
from .typed_payload_codec_v131 import canonical_json


PLAN = 'report-business-flow-nested-plan-v192'
PATHS = 'business-flow-nested-causal-paths-v192'
MAX_WIRE_BYTES = original.MAX_WIRE_BYTES
MAX_PHASES_PER_FLOW = 12
MAX_ACTIONS_PER_FLOW = 32
MAX_CONTROL_DEPTH = 3  # Only decision/parallel/repeat count; steps are leaves.
_FLAGS = {'source_authority_verified': False, 'semantic_quality_verified': False,
    'entry_facts_verified': False, 'permissions_verified': False,
    'can_apply': False, 'can_publish': False, 'can_execute': False}
ERROR = '递归流程、完整原位置或绑定无法核对；未展平、裁剪、猜测或授权'
check_binding = original.check_binding
_ACTION_KEYS = {'name', 'actor', 'role', 'responsibility', 'inputs', 'outputs', 'attribution'}


class NestedPlanCapacityError(ValueError):
    """The complete plan must fit; no partial AST or path inventory is issued."""


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _capacity(condition):
    if not condition:
        raise NestedPlanCapacityError('完整递归流程超过深度、总量或字节容量；原输入保留，未截断')


def _bytes(value):
    return canonical_json(value).encode('utf-8', errors='strict')


class DecisionBranchV192(Closed):
    condition: original.Text
    attribution: original.Attribution
    phases: list[PhaseV192] = Field(max_length=MAX_PHASES_PER_FLOW)


class ParallelBranchV192(Closed):
    phases: list[PhaseV192] = Field(max_length=MAX_PHASES_PER_FLOW)


class DecisionV192(Closed):
    kind: Literal['decision']
    attribution: original.Attribution
    decision: original.ActionV184
    branches: list[DecisionBranchV192] = Field(min_length=2, max_length=6)


class ParallelV192(Closed):
    kind: Literal['parallel']
    attribution: original.Attribution
    dispatch: original.ActionV184
    branches: list[ParallelBranchV192] = Field(min_length=2, max_length=6)
    wait_mode: Literal['all', 'any']
    join_attribution: original.Attribution


PhaseV192 = Annotated[original.StepsV184 | DecisionV192 | ParallelV192 | original.RepeatV184,
    Field(discriminator='kind')]


class FlowV192(Closed):
    name: str = Field(min_length=1, max_length=180, pattern=r'\S')
    trigger: original.Trigger
    phase_order: original.Attribution
    phases: list[PhaseV192] = Field(max_length=MAX_PHASES_PER_FLOW)
    completion: original.ActionV184


class BusinessDefinitionV192(Closed):
    name: base.Name
    flows: list[FlowV192] = Field(min_length=1, max_length=16)
    questions: list[original.Question] = Field(max_length=80)


class BusinessFlowNestedPlanV192(Closed):
    schema: Literal['report-business-flow-nested-plan-v192']
    binding_hash: base.Hash
    business: BusinessDefinitionV192


for _model in (DecisionBranchV192, ParallelBranchV192, DecisionV192, ParallelV192,
        FlowV192, BusinessDefinitionV192, BusinessFlowNestedPlanV192):
    _model.model_rebuild(_types_namespace={'PhaseV192': PhaseV192})


def _preflight(value):
    """Iterative counts precede recursive Pydantic validation, even on failure."""
    _capacity(len(_bytes(value)) <= MAX_WIRE_BYTES)
    _need(type(value) is dict and set(value) == {'schema', 'binding_hash', 'business'} and value['schema'] == PLAN)
    business = value['business']
    _need(type(business) is dict and set(business) == {'name', 'flows', 'questions'}
        and type(business['flows']) is list and 1 <= len(business['flows']) <= 16
        and type(business['questions']) is list and len(business['questions']) <= 80)
    for flow in business['flows']:
        _need(type(flow) is dict and set(flow) == {'name', 'trigger', 'phase_order', 'phases', 'completion'}
            and type(flow['phases']) is list and type(flow['completion']) is dict
            and set(flow['completion']) == _ACTION_KEYS)
        phases, actions = 0, 1  # completion is an actual business action.
        pending = [(phase, 0) for phase in reversed(flow['phases'])]
        while pending:
            phase, parent_control_depth = pending.pop()
            phases += 1
            _capacity(phases <= MAX_PHASES_PER_FLOW)
            _need(type(phase) is dict and type(phase.get('kind')) is str)
            kind = phase['kind']
            depth = parent_control_depth + (1 if kind in {'decision', 'parallel', 'repeat'} else 0)
            _capacity(depth <= MAX_CONTROL_DEPTH)
            if kind in {'steps', 'repeat'}:
                fields = {'kind', 'attribution', 'steps'} | ({'check', 'repeat_if', 'finish_if'} if kind == 'repeat' else set())
                _need(set(phase) == fields and type(phase['steps']) is list and 1 <= len(phase['steps']) <= 16
                    and all(type(action) is dict and set(action) == _ACTION_KEYS for action in phase['steps']))
                actions += len(phase['steps'])
                if kind == 'repeat':
                    _need(type(phase['check']) is dict and set(phase['check']) == _ACTION_KEYS)
                    actions += 1
            else:
                _need(kind in {'decision', 'parallel'})
                action_key = 'decision' if kind == 'decision' else 'dispatch'
                fields = {'kind', 'attribution', action_key, 'branches'} | (
                    {'wait_mode', 'join_attribution'} if kind == 'parallel' else set())
                _need(set(phase) == fields and type(phase[action_key]) is dict
                    and set(phase[action_key]) == _ACTION_KEYS and type(phase['branches']) is list
                    and 2 <= len(phase['branches']) <= 6)
                actions += 1
                for branch in reversed(phase['branches']):
                    fields = {'condition', 'attribution', 'phases'} if kind == 'decision' else {'phases'}
                    _need(type(branch) is dict and set(branch) == fields and type(branch['phases']) is list)
                    pending.extend((child, depth) for child in reversed(branch['phases']))
            _capacity(actions <= MAX_ACTIONS_PER_FLOW)


def _typed_plan(value):
    _preflight(value)
    checked = BusinessFlowNestedPlanV192.model_validate(value).model_dump()
    _need(_bytes(checked) == _bytes(value))
    return checked


def check_plan(value, *, scope_binding):
    try:
        checked = _typed_plan(value)
        binding = check_binding(scope_binding)
        _need(checked['binding_hash'] == digest(binding) and checked['business']['name'] == binding['name'])
        return copy.deepcopy(checked)
    except NestedPlanCapacityError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def parse_plan(raw, *, scope_binding):
    try:
        _need(type(raw) is bytes and bool(raw))
        _capacity(len(raw) <= MAX_WIRE_BYTES)
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=base._pairs,
            parse_constant=base._constant)
        return check_plan(value, scope_binding=scope_binding)
    except NestedPlanCapacityError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def _schema_for_shape(name, binding_hash):
    _need(type(name) is str and 1 <= len(name) <= 200 and bool(name.strip()))
    base._hash(binding_hash)
    schema = BusinessFlowNestedPlanV192.model_json_schema()
    schema['properties']['binding_hash']['const'] = binding_hash
    schema['$defs']['BusinessDefinitionV192']['properties']['name']['const'] = name
    schema['description'] = ('完整递归计划：每流程累计最多12个阶段、32个业务动作（含completion）；'
        'decision、parallel、repeat最多3层控制嵌套，steps叶子不增加控制深度。'
        '深度和累计总量由本地预检强制，不能截断或展平。')
    return schema


def output_schema(scope_binding):
    binding = check_binding(scope_binding)
    return _schema_for_shape(binding['name'], digest(binding))


def _walk(checked, binding):
    actions, inputs, outputs = [], [], []

    def add(action, path, fi, pi, part, ancestors):
        def ports(labels, field, target):
            result = []
            for ordinal, label in enumerate(labels):
                pointer = path + '/' + field + '/' + str(ordinal)
                target.append(pointer)
                result.append({'path': pointer, 'ordinal': ordinal, 'label': label})
            return result
        actions.append({'path': path, 'flow_path': '/business/flows/' + str(fi),
            'flow_index': fi, 'phase_index': pi, 'part': part, 'name': action['name'],
            'actor': action['actor'], 'role': action['role'], 'control_ancestors': copy.deepcopy(ancestors),
            'input_ports': ports(action['inputs'], 'inputs', inputs),
            'output_ports': ports(action['outputs'], 'outputs', outputs)})

    def visit(phases, prefix, fi, ancestors):
        for pi, phase in enumerate(phases):
            path = prefix + '/' + str(pi)
            kind = phase['kind']
            parents = ancestors + [{'path': path, 'kind': kind, 'branch_index': None}]
            if kind in {'steps', 'repeat'}:
                for ai, action in enumerate(phase['steps']):
                    add(action, path + '/steps/' + str(ai), fi, pi,
                        'repeat_step' if kind == 'repeat' else 'step', parents)
                if kind == 'repeat':
                    add(phase['check'], path + '/check', fi, pi, 'check', parents)
            else:
                key = 'decision' if kind == 'decision' else 'dispatch'
                add(phase[key], path + '/' + key, fi, pi, key, parents)
                for bi, branch in enumerate(phase['branches']):
                    bp = path + '/branches/' + str(bi)
                    visit(branch['phases'], bp + '/phases', fi, parents + [
                        {'path': bp, 'kind': kind + '_branch', 'branch_index': bi}])

    for fi, flow in enumerate(checked['business']['flows']):
        path = '/business/flows/' + str(fi)
        visit(flow['phases'], path + '/phases', fi, [])
        add(flow['completion'], path + '/completion', fi, None, 'completion', [])
    _need(len({row['path'] for row in actions}) == len(actions)
        and len(set(inputs)) == len(inputs) and len(set(outputs)) == len(outputs))
    value = {'schema': PATHS, 'source_plan_schema': PLAN, 'source_plan_hash': digest(checked),
        'scope_binding_hash': digest(binding), 'actions': actions, 'input_paths': inputs,
        'output_paths': outputs, 'causal_declaration_consistency_only': True, **_FLAGS}
    return {**value, 'walk_hash': digest(value)}


def walk_action_ports(plan, *, scope_binding):
    """Full pointers are identities; local phase_index is not a V190 selector."""
    checked = check_plan(plan, scope_binding=scope_binding)
    return _walk(checked, check_binding(scope_binding))


def attribution_refs(checked):
    """Collect every declared attribution; this never resolves source authority."""
    try:
        value = _typed_plan(checked)
        references = []

        def add(attribution):
            references.extend(attribution['citations'])

        def visit(phases):
            for phase in phases:
                add(phase['attribution'])
                if phase['kind'] in {'steps', 'repeat'}:
                    for action in phase['steps']:
                        add(action['attribution'])
                    if phase['kind'] == 'repeat':
                        add(phase['check']['attribution'])
                else:
                    add(phase['decision' if phase['kind'] == 'decision' else 'dispatch']['attribution'])
                    if phase['kind'] == 'parallel':
                        add(phase['join_attribution'])
                    for branch in phase['branches']:
                        if phase['kind'] == 'decision':
                            add(branch['attribution'])
                        visit(branch['phases'])

        for flow in value['business']['flows']:
            add(flow['trigger']['attribution']); add(flow['phase_order'])
            visit(flow['phases']); add(flow['completion']['attribution'])
        for question in value['business']['questions']:
            references.extend(question['citations'])
        return list(dict.fromkeys(references))
    except NestedPlanCapacityError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None
