"""Exact original V184 action/port locations for independent causal design.

No compiled set positions, display names, inferred data, execution rights or
old paid-answer reinterpretation are used. Source/binding checks here are pure
protocol checks; declared entry contracts are not observed trigger facts.
"""
import copy

from . import report_business_flow_wire_v184 as plans
from .contracts import digest
from .typed_payload_codec_v131 import canonical_json


PATHS = 'business-flow-action-ports-v189'
BINDINGS = 'business-flow-causal-bindings-v189'
ANALYSIS = 'business-flow-causal-analysis-v189'
ERROR = '原计划、动作端口位置或完整因果声明无法核对；未猜测、去重或授权'
_FLAGS = {'source_authority_verified': False, 'semantic_quality_verified': False,
    'entry_facts_verified': False, 'permissions_verified': False,
    'can_apply': False, 'can_publish': False, 'can_execute': False}


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _bytes(value):
    return canonical_json(value).encode('utf-8', errors='strict')


def _checked(plan, scope_binding):
    _need(type(plan) is dict)
    binding = plans.check_binding(scope_binding)
    checked = plans.parse_plan(_bytes(plan), scope_binding=binding)
    return checked, binding


def _walk(checked, binding):
    actions, inputs, outputs = [], [], []
    def add(action, path, flow_index, phase_index, part, ancestors):
        def ports(values, field, collection):
            result = []
            for ordinal, label in enumerate(values):
                pointer = path + '/' + field + '/' + str(ordinal)
                collection.append(pointer)
                result.append({'path': pointer, 'ordinal': ordinal, 'label': label})
            return result
        actions.append({'path': path, 'flow_path': '/business/flows/' + str(flow_index),
            'flow_index': flow_index, 'phase_index': phase_index, 'part': part,
            'name': action['name'], 'actor': action['actor'], 'role': action['role'],
            'control_ancestors': copy.deepcopy(ancestors),
            'input_ports': ports(action['inputs'], 'inputs', inputs),
            'output_ports': ports(action['outputs'], 'outputs', outputs)})
    for flow_index, flow in enumerate(checked['business']['flows']):
        flow_path = '/business/flows/' + str(flow_index)
        for phase_index, phase in enumerate(flow['phases']):
            path = flow_path + '/phases/' + str(phase_index)
            kind = phase['kind']
            parents = [{'path': path, 'kind': kind, 'branch_index': None}]
            if kind == 'steps':
                for index, action in enumerate(phase['steps']):
                    add(action, path + '/steps/' + str(index), flow_index, phase_index, 'step', parents)
            elif kind == 'decision':
                add(phase['decision'], path + '/decision', flow_index, phase_index, 'decision', parents)
                for branch_index, branch in enumerate(phase['branches']):
                    branch_path = path + '/branches/' + str(branch_index)
                    ancestors = parents + [{'path': branch_path, 'kind': 'decision_branch', 'branch_index': branch_index}]
                    for index, action in enumerate(branch['steps']):
                        add(action, branch_path + '/steps/' + str(index), flow_index, phase_index, 'branch_step', ancestors)
            elif kind == 'parallel':
                add(phase['dispatch'], path + '/dispatch', flow_index, phase_index, 'dispatch', parents)
                for branch_index, branch in enumerate(phase['branches']):
                    branch_path = path + '/branches/' + str(branch_index)
                    ancestors = parents + [{'path': branch_path, 'kind': 'parallel_branch', 'branch_index': branch_index}]
                    for index, action in enumerate(branch['steps']):
                        add(action, branch_path + '/steps/' + str(index), flow_index, phase_index, 'branch_step', ancestors)
            elif kind == 'repeat':
                for index, action in enumerate(phase['steps']):
                    add(action, path + '/steps/' + str(index), flow_index, phase_index, 'repeat_step', parents)
                add(phase['check'], path + '/check', flow_index, phase_index, 'check', parents)
            else:
                _need(False)
        add(flow['completion'], flow_path + '/completion', flow_index, None, 'completion', [])
    _need(len({item['path'] for item in actions}) == len(actions)
        and len(set(inputs)) == len(inputs) and len(set(outputs)) == len(outputs))
    value = {'schema': PATHS, 'source_plan_schema': plans.WIRE,
        'source_plan_hash': digest(checked), 'scope_binding_hash': digest(binding),
        'actions': actions, 'input_paths': inputs, 'output_paths': outputs,
        'causal_declaration_consistency_only': True, **_FLAGS}
    return {**value, 'walk_hash': digest(value)}


def walk_action_ports(plan, *, scope_binding):
    """Keep each original action and every input/output ordinal, even repeats."""
    checked, binding = _checked(plan, scope_binding)
    return _walk(checked, binding)


def _contract(value):
    _need(type(value) is dict and set(value) == {'data_type', 'unit'}
        and type(value['data_type']) is str and bool(value['data_type'].strip())
        and (value['unit'] is None or type(value['unit']) is str and bool(value['unit'].strip())))
    _bytes(value)
    return value


def _bindings(value, checked, binding, walked):
    """Closed, complete design declarations; never facts or source grants."""
    _need(type(value) is dict and set(value) == {'schema', 'source_plan_hash', 'scope_binding_hash',
        'entry_ports', 'output_contracts', 'input_bindings'}
        and value['schema'] == BINDINGS and value['source_plan_hash'] == digest(checked)
        and value['scope_binding_hash'] == digest(binding))
    _bytes(value)
    flows = {'/business/flows/' + str(index) for index in range(len(checked['business']['flows']))}
    _need(type(value['entry_ports']) is list and type(value['output_contracts']) is list
        and type(value['input_bindings']) is list)
    entry_paths = set()
    for index, entry in enumerate(value['entry_ports']):
        _need(type(entry) is dict and set(entry) == {'path', 'flow_path', 'contract'}
            and entry['path'] == '/entry_ports/' + str(index)
            and type(entry['flow_path']) is str and entry['flow_path'] in flows)
        _contract(entry['contract'])
        entry_paths.add(entry['path'])
    output_paths = []
    for output in value['output_contracts']:
        _need(type(output) is dict and set(output) == {'path', 'contract'}
            and type(output['path']) is str and output['path'] in walked['output_paths'])
        _contract(output['contract'])
        output_paths.append(output['path'])
    _need(len(output_paths) == len(set(output_paths)) and set(output_paths) == set(walked['output_paths']))
    sources = entry_paths | set(output_paths)
    input_paths = []
    for item in value['input_bindings']:
        _need(type(item) is dict and set(item) == {'input_path', 'contract', 'sources'}
            and type(item['input_path']) is str and item['input_path'] in walked['input_paths']
            and type(item['sources']) is list and bool(item['sources'])
            and all(type(source) is str and source in sources for source in item['sources'])
            and len(set(item['sources'])) == len(item['sources']))
        _contract(item['contract'])
        input_paths.append(item['input_path'])
    _need(len(input_paths) == len(set(input_paths)) and set(input_paths) == set(walked['input_paths']))
    return copy.deepcopy(value)


def analyze_availability(plan, bindings, *, scope_binding, max_scenarios=4096):
    """Analyze declared causal availability after real plan/coverage checks.

    Entries are proposals. Even a consistent result neither proves their real
    existence or trigger semantics nor grants a tool, device or execution right.
    """
    _need(type(max_scenarios) is int and 1 <= max_scenarios <= 4096)
    checked, binding = _checked(plan, scope_binding)
    walked = _walk(checked, binding)
    declared = _bindings(bindings, checked, binding, walked)
    from . import business_flow_causal_availability_v189 as availability
    analysis = availability.analyze_availability(copy.deepcopy(checked), action_ports=copy.deepcopy(walked['actions']),
        entry_ports=copy.deepcopy(declared['entry_ports']), input_bindings=copy.deepcopy(declared['input_bindings']),
        output_contracts=copy.deepcopy(declared['output_contracts']), max_scenarios=max_scenarios)
    _need(type(analysis) is dict)
    value = {'schema': ANALYSIS, 'source_plan_hash': digest(checked), 'scope_binding_hash': digest(binding),
        'walk_hash': walked['walk_hash'], 'paths': walked, 'bindings': declared,
        'bindings_hash': digest(declared), 'availability': copy.deepcopy(analysis),
        'max_scenarios': max_scenarios, 'causal_declaration_consistency_only': True, **_FLAGS}
    return {**value, 'analysis_hash': digest(value)}
