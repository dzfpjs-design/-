"""Pure declared-path availability for the unchanged V184 action structure.

Each scenario is one declared history, never a claim that a condition is true.
Entries are proposed interface contracts, not facts, permissions or dispatch
grants. No catalogue, source, IAM, provider, database or runtime is consulted.
The caller independently checks the sidecar/source hashes and scope binding.
"""
from itertools import product

from . import report_business_flow_wire_v184 as wire
from .contracts import digest
from .typed_payload_codec_v131 import canonical_json


SCHEMA = 'business-flow-causal-availability-v189'
MAX_SCENARIOS = 4096
FLAGS = {'semantic_quality_verified': False, 'source_authority_verified': False,
    'entry_facts_verified': False, 'permissions_verified': False,
    'external_dispatch_allowed': False, 'whole_report_verified': False,
    'can_apply': False, 'can_publish': False, 'can_execute': False}
_ACTION_KEYS = set(('path flow_path flow_index phase_index part name actor role '
    'control_ancestors input_ports output_ports').split())


class CausalAvailabilityError(ValueError):
    def __init__(self, code, path=''):
        self.code, self.path = code, path
        super().__init__(code + (': ' + path if path else ''))


def _need(condition, code, path=''):
    if not condition:
        raise CausalAvailabilityError(code, path)


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def _contract(value, path):
    _need(type(value) is dict and set(value) == {'data_type', 'unit'}, 'contract_shape', path)
    _need(type(value['data_type']) is str and bool(value['data_type'].strip())
        and (value['unit'] is None or type(value['unit']) is str and bool(value['unit'].strip())),
        'contract_type', path)
    return value


def _layout(plan):
    """Exact source-order positions; no label lookup or set-normalized ports."""
    rows = []
    for fi, flow in enumerate(plan['business']['flows']):
        fp = '/business/flows/' + str(fi)

        def add(action, path, pi, part):
            rows.append(({'path': path, 'flow_path': fp, 'flow_index': fi,
                'phase_index': pi, 'part': part, 'name': action['name'],
                'actor': action['actor'], 'role': action['role'],
                'input_ports': [{'path': path + '/inputs/' + str(i), 'ordinal': i, 'label': label}
                    for i, label in enumerate(action['inputs'])],
                'output_ports': [{'path': path + '/outputs/' + str(i), 'ordinal': i, 'label': label}
                    for i, label in enumerate(action['outputs'])]}, action))

        for pi, phase in enumerate(flow['phases']):
            pp = fp + '/phases/' + str(pi)
            kind = phase['kind']
            if kind in {'steps', 'repeat'}:
                for ai, action in enumerate(phase['steps']):
                    add(action, pp + '/steps/' + str(ai), pi, 'repeat_step' if kind == 'repeat' else 'step')
                if kind == 'repeat':
                    add(phase['check'], pp + '/check', pi, 'check')
            else:
                key = 'decision' if kind == 'decision' else 'dispatch'
                add(phase[key], pp + '/' + key, pi, key)
                for bi, branch in enumerate(phase['branches']):
                    for ai, action in enumerate(branch['steps']):
                        add(action, pp + '/branches/' + str(bi) + '/steps/' + str(ai), pi, 'branch_step')
        add(flow['completion'], fp + '/completion', None, 'completion')
    return rows


def _inventory(plan, action_ports, entry_ports, input_bindings, output_contracts):
    rows = _layout(plan)
    _need(type(action_ports) is list and len(action_ports) == len(rows), 'action_inventory')
    inputs, outputs = {}, {}
    for supplied, (expected, _) in zip(action_ports, rows, strict=True):
        _need(type(supplied) is dict and set(supplied) == _ACTION_KEYS, 'action_shape')
        _need(_same({k: v for k, v in supplied.items() if k != 'control_ancestors'}, expected),
            'action_position', expected['path'])
        # Traversal below uses only actual AST control, never this metadata.
        _need(type(supplied['control_ancestors']) is list, 'action_ancestors', expected['path'])
        for ancestor in supplied['control_ancestors']:
            _need(type(ancestor) is dict and set(ancestor) == {'path', 'kind', 'branch_index'}
                and type(ancestor['path']) is str and expected['path'].startswith(ancestor['path'] + '/')
                and ancestor['kind'] in {'steps', 'decision', 'parallel', 'repeat', 'decision_branch', 'parallel_branch'}
                and (ancestor['branch_index'] is None or type(ancestor['branch_index']) is int
                    and ancestor['branch_index'] >= 0), 'action_ancestors', expected['path'])
        for item in expected['input_ports']:
            inputs[item['path']] = expected['flow_path']
        for item in expected['output_ports']:
            outputs[item['path']] = expected['flow_path']
    flows = {'/business/flows/' + str(i) for i in range(len(plan['business']['flows']))}
    _need(type(entry_ports) is list and type(output_contracts) is list and type(input_bindings) is list,
        'binding_lists')
    contracts, owners, entries = {}, {}, {fp: set() for fp in flows}
    for i, entry in enumerate(entry_ports):
        path = '/entry_ports/' + str(i)
        _need(type(entry) is dict and set(entry) == {'path', 'flow_path', 'contract'}
            and entry['path'] == path and type(entry['flow_path']) is str and entry['flow_path'] in flows,
            'entry_position', path)
        contracts[path] = _contract(entry['contract'], path)
        owners[path] = entry['flow_path']
        entries[entry['flow_path']].add(path)
    seen = set()
    for output in output_contracts:
        _need(type(output) is dict and set(output) == {'path', 'contract'}
            and type(output['path']) is str and output['path'] in outputs
            and output['path'] not in seen, 'output_contract_position')
        path = output['path']; seen.add(path)
        contracts[path] = _contract(output['contract'], path)
        owners[path] = outputs[path]
    _need(seen == set(outputs), 'output_contract_coverage')
    bindings = {}
    for binding in input_bindings:
        _need(type(binding) is dict and set(binding) == {'input_path', 'contract', 'sources'}
            and type(binding['input_path']) is str and binding['input_path'] in inputs
            and binding['input_path'] not in bindings, 'input_binding_position')
        path = binding['input_path']; contract = _contract(binding['contract'], path)
        sources = binding['sources']
        _need(type(sources) is list and bool(sources) and all(type(ref) is str for ref in sources)
            and len(set(sources)) == len(sources), 'input_sources', path)
        for ref in sources:
            _need(ref in contracts and owners[ref] == inputs[path], 'source_flow_or_position', path)
            _need(_same(contract, contracts[ref]), 'source_contract_mismatch', path)
        bindings[path] = sources
    _need(set(bindings) == set(inputs), 'input_binding_coverage')
    return rows, entries, bindings


class _Availability:
    def __init__(self, bindings, maximum):
        self.bindings, self.maximum, self.peak = bindings, maximum, 1

    def bounded(self, scenarios, path):
        _need(0 < len(scenarios) <= self.maximum, 'scenario_capacity', path)
        self.peak = max(self.peak, len(scenarios))
        return scenarios

    def append(self, target, scenario, path):
        _need(len(target) < self.maximum, 'scenario_capacity', path)
        target.append(scenario)

    def action(self, action, path, scenarios):
        self.bounded(scenarios, path)
        for i in range(len(action['inputs'])):
            ip = path + '/inputs/' + str(i)
            sources = self.bindings[ip]
            # Both directions matter: entry+future cannot hide a dead source,
            # and a source valid on one branch cannot supply another history.
            _need(all(any(ref in case for case in scenarios) for ref in sources), 'source_not_available', ip)
            _need(all(any(ref in case for ref in sources) for case in scenarios), 'input_not_guaranteed', ip)
        produced = frozenset(path + '/outputs/' + str(i) for i in range(len(action['outputs'])))
        return [case | produced for case in scenarios]

    def steps(self, actions, path, scenarios):
        for i, action in enumerate(actions):
            scenarios = self.action(action, path + '/' + str(i), scenarios)
        return scenarios

    def flow(self, flow, path, entry):
        scenarios = [frozenset(entry)]
        for i, phase in enumerate(flow['phases']):
            pp = path + '/phases/' + str(i)
            if phase['kind'] in {'steps', 'repeat'}:
                scenarios = self.steps(phase['steps'], pp + '/steps', scenarios)
                if phase['kind'] == 'repeat':
                    # Validate the first real iteration. No implicit initial
                    # value, carry/backedge or conversion is supported.
                    scenarios = self.action(phase['check'], pp + '/check', scenarios)
                continue
            key = 'decision' if phase['kind'] == 'decision' else 'dispatch'
            scenarios = self.action(phase[key], pp + '/' + key, scenarios)
            branches = [self.steps(branch['steps'], pp + '/branches/' + str(bi) + '/steps', scenarios)
                for bi, branch in enumerate(phase['branches'])]
            joined = []
            if phase['kind'] == 'parallel' and phase['wait_mode'] == 'all':
                # Current V184 branch bodies are deterministic step lists.
                # Keep each original history separate; never cartesian across
                # different incoming histories. Phi checks above still see
                # all histories reaching each actual action.
                for history in range(len(scenarios)):
                    for combination in product(*([branch[history]] for branch in branches)):
                        self.append(joined, frozenset().union(*combination), pp)
            else:
                # Decision: each declared branch (including empty) is possible.
                # Parallel any: each branch could be the sole winning branch.
                for branch in branches:
                    for case in branch:
                        self.append(joined, case, pp)
            scenarios = self.bounded(joined, pp)
        return self.action(flow['completion'], path + '/completion', scenarios)


def analyze_availability(checked_plan, *, action_ports, entry_ports, input_bindings,
                         output_contracts, max_scenarios=MAX_SCENARIOS):
    """Check declared causality only; source hashes/coverage remain caller gates.

    Exact pointer contracts are independently checked here. Conditions are not
    evaluated or assumed mutually exclusive/exhaustive; all declared branches
    are retained. Loop-carried bindings and transformations have no accepted
    representation, and extra binding fields are rejected rather than guessed.
    """
    _need(type(max_scenarios) is int and 1 <= max_scenarios <= MAX_SCENARIOS, 'scenario_limit')
    parsed = wire.BusinessFlowPlanV184.model_validate(checked_plan).model_dump()
    _need(_same(parsed, checked_plan), 'plan_typed_identity')
    rows, entries, bindings = _inventory(parsed, action_ports, entry_ports, input_bindings, output_contracts)
    results = []
    for i, flow in enumerate(parsed['business']['flows']):
        path = '/business/flows/' + str(i)
        analyzer = _Availability(bindings, max_scenarios)
        final = analyzer.flow(flow, path, entries[path])
        flow_rows = [row for row, _ in rows if row['flow_path'] == path]
        results.append({'flow_path': path, 'scenario_count': len(final), 'peak_scenarios': analyzer.peak,
            'entry_count': len(entries[path]), 'action_count': len(flow_rows),
            'input_count': sum(len(row['input_ports']) for row in flow_rows),
            'output_count': sum(len(row['output_ports']) for row in flow_rows)})
    result = {'schema': SCHEMA, 'source_plan_hash': digest(parsed),
        'action_ports_hash': digest(action_ports), 'entry_ports_hash': digest(entry_ports),
        'input_bindings_hash': digest(input_bindings), 'output_contracts_hash': digest(output_contracts),
        'max_scenarios': max_scenarios, 'flows': results,
        'causal_declaration_consistency_only': True, 'declared_inputs_available': True, **FLAGS}
    result['availability_hash'] = digest(result)
    return result
