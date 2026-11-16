"""Pure availability of declared values through the independent V192 AST.

All histories are declared possibilities, not evaluated conditions. Entry
contracts are proposals, never observed values or execution permissions. This
module neither edits an old paid protocol nor consults a source, DB or tool.

Capacity counts complete histories across flows. A recursive frontier is
bounded together with histories of already completed flows; sequential actions
do not consume additional scenario slots. All outcomes are retained or the
entire analysis fails. Parallel-all combines only descendants of the SAME
incoming history, including when either branch recursively branches again.
"""
import copy
from dataclasses import dataclass
from functools import wraps
from itertools import product

from . import business_flow_nested_plan_v192 as plans
from .contracts import digest
from .typed_payload_codec_v131 import canonical_json


SCHEMA = 'business-flow-nested-causal-availability-v192'
BINDINGS = 'business-flow-nested-causal-bindings-v192'
MAX_SCENARIOS = 4096
SCENARIO_POLICY = 'sum-of-complete-flow-histories-v192'
FLAGS = {'source_authority_verified': False, 'semantic_quality_verified': False,
    'entry_facts_verified': False, 'runtime_entry_binding_verified': False,
    'permissions_verified': False, 'external_dispatch_allowed': False,
    'whole_report_verified': False, 'can_apply': False, 'can_publish': False,
    'can_execute': False}


class CausalAvailabilityError(ValueError):
    def __init__(self, code, path=''):
        self.code, self.path = code, path
        super().__init__(code + (': ' + path if path else ''))


def _need(condition, code, path=''):
    if not condition:
        raise CausalAvailabilityError(code, path)


def _closed(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except CausalAvailabilityError:
            raise
        except (TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            raise CausalAvailabilityError('invalid_structure') from None
    return checked


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def _contract(value, path):
    _need(type(value) is dict and set(value) == {'data_type', 'unit'}, 'contract_shape', path)
    _need(type(value['data_type']) is str and bool(value['data_type'].strip())
        and (value['unit'] is None or type(value['unit']) is str and bool(value['unit'].strip())),
        'contract_type', path)
    return value


def _prepare(plan, bindings, scope_binding):
    _need(type(plan) is dict and type(scope_binding) is dict, 'plan_or_binding_shape')
    checked = plans.check_plan(plan, scope_binding=scope_binding)
    walked = plans.walk_action_ports(checked, scope_binding=scope_binding)
    _need(type(bindings) is dict and set(bindings) == {'schema', 'source_plan_hash',
        'scope_binding_hash', 'entry_ports', 'output_contracts', 'input_bindings'}
        and bindings['schema'] == BINDINGS
        and bindings['source_plan_hash'] == digest(checked)
        and bindings['scope_binding_hash'] == digest(scope_binding), 'binding_source_identity')
    # Typed canonical serialization rejects values that cannot be frozen. No
    # source plan is reconstructed from the sidecar's own hashes or copies.
    canonical_json(bindings)
    _need(type(bindings['entry_ports']) is list and type(bindings['output_contracts']) is list
        and type(bindings['input_bindings']) is list, 'binding_lists')

    flows = {'/business/flows/' + str(i) for i in range(len(checked['business']['flows']))}
    inputs, outputs = {}, {}
    for action in walked['actions']:
        for field, target in (('input_ports', inputs), ('output_ports', outputs)):
            for port in action[field]:
                _need(port['path'] not in target, 'duplicate_actual_port', port['path'])
                target[port['path']] = action['flow_path']
    _need(set(inputs) == set(walked['input_paths']) and set(outputs) == set(walked['output_paths']),
        'actual_port_inventory')

    contracts, owners = {}, {}
    entries = {fp: set() for fp in flows}
    for i, entry in enumerate(bindings['entry_ports']):
        path = '/entry_ports/' + str(i)
        _need(type(entry) is dict and set(entry) == {'path', 'flow_path', 'contract'}
            and type(entry['path']) is str and entry['path'] == path
            and type(entry['flow_path']) is str and entry['flow_path'] in flows,
            'entry_position', path)
        contracts[path] = _contract(entry['contract'], path)
        owners[path] = entry['flow_path']
        entries[entry['flow_path']].add(path)

    seen = set()
    for output in bindings['output_contracts']:
        _need(type(output) is dict and set(output) == {'path', 'contract'}
            and type(output['path']) is str and output['path'] in outputs
            and output['path'] not in seen, 'output_contract_position')
        path = output['path']
        seen.add(path)
        contracts[path] = _contract(output['contract'], path)
        owners[path] = outputs[path]
    _need(seen == set(outputs), 'output_contract_coverage')

    resolved = {}
    for item in bindings['input_bindings']:
        _need(type(item) is dict and set(item) == {'input_path', 'contract', 'sources'}
            and type(item['input_path']) is str and item['input_path'] in inputs
            and item['input_path'] not in resolved, 'input_binding_position')
        path = item['input_path']
        contract = _contract(item['contract'], path)
        sources = item['sources']
        _need(type(sources) is list and bool(sources)
            and all(type(ref) is str for ref in sources) and len(set(sources)) == len(sources),
            'input_sources', path)
        for ref in sources:
            _need(ref in contracts and owners[ref] == inputs[path], 'source_flow_or_position', path)
            _need(_same(contract, contracts[ref]), 'source_contract_mismatch', path)
        resolved[path] = copy.deepcopy(sources)
    _need(set(resolved) == set(inputs), 'input_binding_coverage')
    return checked, walked, copy.deepcopy(bindings), entries, resolved


@_closed
def check_bindings(plan, bindings, *, scope_binding):
    """Freeze exact hashes, real port coverage, same-flow and typed contracts.

    This does not claim that declared outputs are available at an input; use
    analyze_availability for that separate control-path check.
    """
    return _prepare(plan, bindings, scope_binding)[2]


@dataclass(frozen=True)
class _History:
    available: frozenset
    # Internal parent labels exist only while an enclosing parallel is open.
    # They are never plan IDs, source identities or model-supplied evidence.
    parents: tuple = ()


class _Availability:
    def __init__(self, bindings, maximum):
        self.bindings, self.maximum = bindings, maximum
        self.completed, self.global_peak, self.flow_peak = 0, 0, 0

    def bounded(self, histories, path):
        _need(bool(histories) and self.completed + len(histories) <= self.maximum,
            'scenario_capacity', path)
        self.flow_peak = max(self.flow_peak, len(histories))
        self.global_peak = max(self.global_peak, self.completed + len(histories))
        return histories

    def append(self, target, history, path):
        _need(self.completed + len(target) < self.maximum, 'scenario_capacity', path)
        target.append(history)

    def action(self, action, path, histories):
        self.bounded(histories, path)
        for i in range(len(action['inputs'])):
            ip = path + '/inputs/' + str(i)
            sources = self.bindings[ip]
            # Every listed alternative must be possible at this real action,
            # and every incoming history needs at least one available source.
            # An entry alternative cannot conceal an impossible future source.
            _need(all(any(ref in case.available for case in histories) for ref in sources),
                'source_not_available', ip)
            _need(all(any(ref in case.available for ref in sources) for case in histories),
                'input_not_guaranteed', ip)
        produced = frozenset(path + '/outputs/' + str(i) for i in range(len(action['outputs'])))
        return [_History(case.available | produced, case.parents) for case in histories]

    def steps(self, actions, path, histories):
        for i, action in enumerate(actions):
            histories = self.action(action, path + '/' + str(i), histories)
        return histories

    def phases(self, phases, path, histories):
        for i, phase in enumerate(phases):
            histories = self.phase(phase, path + '/' + str(i), histories)
        return histories

    def phase(self, phase, path, histories):
        kind = phase['kind']
        if kind in {'steps', 'repeat'}:
            result = self.steps(phase['steps'], path + '/steps', histories)
            if kind == 'repeat':
                # Only the first actual iteration is checked. There is no
                # implicit carry, initialization or claim of loop termination.
                result = self.action(phase['check'], path + '/check', result)
            return self.bounded(result, path)

        if kind == 'decision':
            incoming = self.action(phase['decision'], path + '/decision', histories)
            joined = []
            for i, branch in enumerate(phase['branches']):
                branch_path = path + '/branches/' + str(i) + '/phases'
                for case in self.phases(branch['phases'], branch_path, incoming):
                    # Empty branches deliberately preserve every incoming
                    # history; their inputs are not supplied by another branch.
                    self.append(joined, case, path)
            return self.bounded(joined, path)

        _need(kind == 'parallel', 'phase_kind', path)
        incoming = self.action(phase['dispatch'], path + '/dispatch', histories)
        forked = [_History(case.available, case.parents + ((path, i),))
            for i, case in enumerate(incoming)]
        branch_results = [self.phases(branch['phases'], path + '/branches/' + str(i) + '/phases', forked)
            for i, branch in enumerate(phase['branches'])]
        grouped = []
        for branch in branch_results:
            groups = [[] for _ in incoming]
            for case in branch:
                _need(bool(case.parents) and case.parents[-1][0] == path, 'parallel_parent', path)
                parent = case.parents[-1][1]
                _need(type(parent) is int and 0 <= parent < len(incoming)
                    and case.parents[:-1] == incoming[parent].parents, 'parallel_parent', path)
                groups[parent].append(case)
            _need(all(groups), 'parallel_parent_coverage', path)
            grouped.append(groups)

        joined = []
        if phase['wait_mode'] == 'all':
            for parent, original in enumerate(incoming):
                # Nested branches can return multiple histories PER parent.
                # zip, a global product or union across parents is incorrect.
                for combination in product(*(branch[parent] for branch in grouped)):
                    self.append(joined, _History(frozenset().union(
                        *(case.available for case in combination)), original.parents), path)
        else:
            _need(phase['wait_mode'] == 'any', 'parallel_wait_mode', path)
            for branch in grouped:
                for parent, cases in enumerate(branch):
                    for case in cases:
                        self.append(joined, _History(case.available, incoming[parent].parents), path)
        return self.bounded(joined, path)

    def flow(self, flow, path, entries):
        self.flow_peak = 0
        initial = self.bounded([_History(frozenset(entries))], path)
        histories = self.phases(flow['phases'], path + '/phases', initial)
        final = self.action(flow['completion'], path + '/completion', histories)
        self.bounded(final, path)
        _need(all(not case.parents for case in final), 'unfinished_parallel', path)
        self.completed += len(final)
        return final


@_closed
def analyze_availability(plan, bindings, *, scope_binding, max_scenarios=MAX_SCENARIOS):
    """Analyze every declared recursive history, without dispatch or fact claims.

    All independent flows share ONE capacity limit, counted by the sum of
    complete flow histories, not a Cartesian product of unrelated triggers.
    Each local frontier plus completed flows must fit as well. No history is
    sorted, deduplicated, trimmed or discarded to obtain a successful result.
    """
    _need(type(max_scenarios) is int and 1 <= max_scenarios <= MAX_SCENARIOS, 'scenario_limit')
    checked, walked, declared, entries, resolved = _prepare(plan, bindings, scope_binding)
    analyzer = _Availability(resolved, max_scenarios)
    results = []
    for i, flow in enumerate(checked['business']['flows']):
        fp = '/business/flows/' + str(i)
        final = analyzer.flow(flow, fp, entries[fp])
        actions = [row for row in walked['actions'] if row['flow_path'] == fp]
        results.append({'flow_path': fp, 'scenario_count': len(final),
            'peak_scenarios': analyzer.flow_peak, 'entry_count': len(entries[fp]),
            'action_count': len(actions), 'input_count': sum(len(row['input_ports']) for row in actions),
            'output_count': sum(len(row['output_ports']) for row in actions)})
    value = {'schema': SCHEMA, 'source_plan_hash': digest(checked),
        'scope_binding_hash': digest(scope_binding), 'paths': walked,
        'bindings': declared, 'bindings_hash': digest(declared),
        'max_scenarios': max_scenarios, 'scenario_count_policy': SCENARIO_POLICY,
        'total_scenarios': analyzer.completed, 'peak_scenarios': analyzer.global_peak,
        'flows': results, 'consistent': True, 'declared_inputs_available': True,
        'entry_contracts_are_proposals': True, 'causal_declaration_consistency_only': True, **FLAGS}
    return {**value, 'availability_hash': digest(value)}
