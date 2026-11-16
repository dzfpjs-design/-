"""Loss-preserving recursive control lowering to the existing graph kernel.

This is a pure compiler, not an adoption or permission grant. Every original
action needs an explicit real runtime binding. Synthetic fork/join/decision
nodes carry control only; an empty branch never manufactures business data.
The caller must still verify source, members, capabilities, actual task inputs,
approval and automation live. Human input mappings are metadata until shown by
the task UI. Any joins and alternative-value runtime selection remain blocked;
they are never silently converted to all or to a guessed producer.

Bindings contain input_schema/references/predicates/guards, nodes keyed by exact
action paths, entries keyed by declared entry paths, conditions/loops keyed by
phase paths and complete contract_schemas [{contract, kind, nullable}]. Node
input_ports/output_ports are ordinal lists, not name guesses. Every live ability
input and node guard reference is declared as an original input port. Branch
predicates compare one mapped result reference with one explicit scalar value;
compound business rules must produce a result in the actual bound decision
action. Codecs/units are explicit proposed configuration, not live registry or
physical calibration proof. The result preserves all of these qualifications.
"""
import copy

from . import business_flow_nested_plan_v192 as plans
from . import business_flow_nested_causal_v192 as causal
from . import full_semantic_graph_v1 as native
from .contracts import digest
from .report_business_flow_wire_v184 import check_binding


SCHEMA = 'business-flow-nested-execution-v192'
BINDINGS = 'business-flow-nested-execution-bindings-v192'
FLAGS = {'semantic_quality_verified': False, 'source_authority_verified': False,
    'entry_facts_verified': False, 'permissions_verified': False,
    'can_apply': False, 'can_publish': False, 'can_execute': False}
ACTORS = {'business_agent': 'business', 'perception_agent': 'perception', 'data_agent': 'data'}


def _need(value, code):
    if not value:
        raise ValueError('nested_execution_' + code)


def _keys(value, fields, code):
    _need(type(value) is dict and set(value) == set(fields), code)


def _index(value, count):
    return type(value) is int and 0 <= value < count


def _at(value, path):
    for part in path.split('/')[1:]:
        value = value[int(part)] if type(value) is list else value[part]
    return value


def _guards():
    return {key: None for key in ('start', 'precondition', 'stop', 'exception')}


class _Compiler:
    def __init__(self, plan, flow_index, supplied, scope_binding, causal_bindings):
        native._safe(supplied)
        native._safe(causal_bindings)
        self.plan = plans.check_plan(plan, scope_binding=scope_binding)
        _need(_index(flow_index, len(self.plan['business']['flows'])), 'flow_index')
        self.analysis = causal.analyze_availability(self.plan, causal_bindings, scope_binding=scope_binding)
        self.sidecar = causal.check_bindings(self.plan, causal_bindings, scope_binding=scope_binding)
        self.binding = check_binding(scope_binding)
        self.flow_index = flow_index
        self.flow_path = '/business/flows/' + str(flow_index)
        self.flow = self.plan['business']['flows'][flow_index]
        self.bindings = copy.deepcopy(supplied)
        _keys(supplied, {'schema', 'input_schema', 'references', 'predicates', 'guards',
            'nodes', 'entries', 'conditions', 'loops', 'contract_schemas'}, 'binding_shape')
        _need(supplied['schema'] == BINDINGS, 'binding_schema')
        self.actions = [x for x in self.analysis['paths']['actions'] if x['flow_index'] == flow_index]
        self.action_ids = {x['path']: 'a_' + str(i + 1).zfill(2) for i, x in enumerate(self.actions)}
        self.nodes, self.edges, self.loops, self.controls = [], [], [], []
        self.action_map, self.edge_map = [], []
        self.conditions, self.loop_specs = {}, {}
        self.phase_map = {}
        self._phases(self.flow['phases'], self.flow_path + '/phases')
        self._control_bindings()
        self._contract_bindings()
        self._action_bindings()
        self._port_bindings()

    def _phases(self, phases, path):
        for i, phase in enumerate(phases):
            key = path + '/' + str(i)
            self.phase_map[key] = phase
            for j, branch in enumerate(phase.get('branches', [])):
                self._phases(branch['phases'], key + '/branches/' + str(j) + '/phases')

    def _control_bindings(self):
        decisions = {p for p, x in self.phase_map.items() if x['kind'] == 'decision'}
        repeats = {p for p, x in self.phase_map.items() if x['kind'] == 'repeat'}
        for row in self.bindings['conditions']:
            _keys(row, {'path', 'predicate_id', 'true_branch_index', 'false_branch_index'}, 'condition_shape')
            path = row['path']
            _need(type(path) is str and path in decisions and path not in self.conditions, 'condition_path')
            _need(len(self.phase_map[path]['branches']) == 2
                and _index(row['true_branch_index'], 2) and _index(row['false_branch_index'], 2)
                and row['true_branch_index'] != row['false_branch_index'], 'binary_outcomes')
            self.conditions[path] = row
        for row in self.bindings['loops']:
            _keys(row, {'path', 'predicate_id', 'repeat_on', 'max_iterations'}, 'loop_shape')
            path = row['path']
            _need(type(path) is str and path in repeats and path not in self.loop_specs
                and row['repeat_on'] in ('true', 'false') and type(row['max_iterations']) is int
                and 1 <= row['max_iterations'] <= 100, 'loop_binding')
            self.loop_specs[path] = row
        _need(set(self.conditions) == decisions and set(self.loop_specs) == repeats, 'control_coverage')

    def _ports(self, supplied, field, count):
        _need(type(supplied) is list and len(supplied) == count, 'port_count')
        result = {}
        for row in supplied:
            _keys(row, {'ordinal', field}, 'port_shape')
            _need(_index(row['ordinal'], count) and row['ordinal'] not in result
                and type(row[field]) is str and bool(row[field]), 'port_ordinal')
            result[row['ordinal']] = row[field]
        return result

    def _contract_bindings(self):
        """Explicit proposed semantic-contract codecs, never registry authority.

        No free-text data_type is guessed to mean a JSON primitive. The caller
        supplies the closed kind/nullability binding and actual fields must
        match it. Non-null units must also be explicit x-unit schema metadata;
        physical calibration/source units still need their separate live gate.
        """
        contracts = {}
        for row in self.sidecar['entry_ports']:
            if row['flow_path'] == self.flow_path:
                contracts[digest(row['contract'])] = row['contract']
        for collection, pointer in [('output_contracts', 'path'), ('input_bindings', 'input_path')]:
            for row in self.sidecar[collection]:
                if row[pointer].startswith(self.flow_path + '/'):
                    contracts[digest(row['contract'])] = row['contract']
        _need(type(self.bindings['contract_schemas']) is list, 'contract_schema_shape')
        self.contracts = {}
        for row in self.bindings['contract_schemas']:
            _keys(row, {'contract', 'kind', 'nullable'}, 'contract_schema_shape')
            key = digest(row['contract'])
            _need(key in contracts and row['contract'] == contracts[key] and key not in self.contracts
                and row['kind'] in {'boolean', 'string', 'integer', 'number', 'object', 'array', 'date'}
                and type(row['nullable']) is bool, 'contract_schema_binding')
            self.contracts[key] = row
        _need(set(self.contracts) == set(contracts), 'contract_schema_coverage')

    def _field_unit(self, schema, field):
        def resolve(value, *, track_unit=False):
            seen, units = set(), []
            while '$ref' in value:
                # Native scalar lookup follows a ref, while JSON Schema also
                # applies sibling constraints. Do not erase unsupported sibling
                # types/compositions or silently select one conflicting unit.
                _need(set(value) <= {'$ref', 'title', 'description', 'x-unit'}, 'field_schema_ref_siblings')
                if track_unit and 'x-unit' in value: units.append(value['x-unit'])
                pointer = value['$ref']
                _need(type(pointer) is str and pointer.startswith('#/') and pointer not in seen, 'field_schema_reference')
                seen.add(pointer); value = schema
                for part in pointer[2:].split('/'):
                    value = value[part.replace('~1', '/').replace('~0', '~')]
            if track_unit and 'x-unit' in value: units.append(value['x-unit'])
            _need(not units or all(unit == units[0] for unit in units), 'field_schema_unit_conflict')
            return value, units[0] if units else None
        root, _ = resolve(schema)
        _, unit = resolve(root['properties'][field], track_unit=True)
        return unit

    def _field_contract(self, schema, field, contract):
        row = self.contracts[digest(contract)]
        _need(native._field(schema, field) == (row['kind'], row['nullable']), 'contract_runtime_type')
        _need(self._field_unit(schema, field) == contract['unit'], 'contract_runtime_unit')

    def _action_bindings(self):
        rows = self.bindings['nodes']
        _need(type(rows) is dict and set(rows) == set(self.action_ids), 'action_coverage')
        self.inputs, self.outputs = {}, {}
        for action in self.actions:
            path, identity = action['path'], self.action_ids[action['path']]
            original = _at(self.plan, path)
            actor, row = action['actor'], rows[path]
            _need(actor == 'human' or actor in ACTORS, 'service_actor_not_registered')
            kind = 'human' if actor == 'human' else 'ability'
            fields = {'kind', 'actor_id', 'guards', 'input_ports', 'output_ports'} | (
                {'role', 'output_schema'} if kind == 'human' else {'execution'})
            _keys(row, fields, 'action_binding_shape')
            _need(row['kind'] == kind, 'actor_kind')
            if kind == 'human':
                _need(row['role'] == original['role'], 'human_role')
                schema = row['output_schema']
                extra = {'role': row['role'], 'output_schema': copy.deepcopy(schema)}
            else:
                _need(type(row['execution']) is dict
                    and row['execution'].get('agent_role') == ACTORS[actor], 'agent_role')
                schema = row['execution']['output_schema']
                extra = {'execution': copy.deepcopy(row['execution'])}
            native._schema(schema)
            inputs = self._ports(row['input_ports'], 'reference', len(original['inputs']))
            outputs = self._ports(row['output_ports'], 'field', len(original['outputs']))
            for field in outputs.values():
                _need(field in schema.get('required', []), 'output_not_required')
                native._field(schema, field)
            output_contracts = {x['path']: x['contract'] for x in self.sidecar['output_contracts']}
            for ordinal, field in outputs.items():
                self._field_contract(schema, field, output_contracts[path + '/outputs/' + str(ordinal)])
            if kind == 'ability':
                _need(set(inputs.values()) == set(row['execution']['input_bindings'].values()), 'input_binding_coverage')
                for reference in row['execution']['input_bindings'].values():
                    ref = self.bindings['references'].get(reference)
                    _need(type(ref) is dict, 'input_reference')
                    _need(ref.get('source') != 'output' or reference in inputs.values(),
                        'undeclared_output_dependency')
            for predicate in row['guards'].values():
                if predicate is not None:
                    _need(predicate in self.bindings['predicates'], 'predicate_missing')
                    for reference in native._predicate_keys(self.bindings['predicates'][predicate]):
                        ref = self.bindings['references'].get(reference)
                        _need(type(ref) is dict, 'guard_reference')
                        _need(reference in inputs.values(),
                            'undeclared_guard_dependency')
            self.inputs[path], self.outputs[path] = inputs, outputs
            self.nodes.append({'id': identity, 'kind': kind, 'source_step_id': identity,
                'guards': copy.deepcopy(row['guards']), 'actor_id': row['actor_id'], **extra})
            self.action_map.append({'ast_path': path, 'graph_node_id': identity,
                'source_action': copy.deepcopy(original), 'source_action_hash': digest(original),
                'input_ports': copy.deepcopy(row['input_ports']), 'output_ports': copy.deepcopy(row['output_ports'])})

    def _port_bindings(self):
        refs = self.bindings['references']
        _need(type(refs) is dict and type(self.bindings['entries']) is dict, 'reference_shape')
        entries = {x['path'] for x in self.sidecar['entry_ports'] if x['flow_path'] == self.flow_path}
        _need(set(self.bindings['entries']) == entries, 'entry_coverage')
        for key in self.bindings['entries'].values():
            _need(type(key) is str and key in refs and refs[key].get('source') == 'input'
                and refs[key]['field'] in self.bindings['input_schema'].get('required', []), 'entry_binding')
        for row in self.sidecar['entry_ports']:
            if row['flow_path'] == self.flow_path:
                ref = refs[self.bindings['entries'][row['path']]]
                self._field_contract(self.bindings['input_schema'], ref['field'], row['contract'])
        by_input = {x['input_path']: x for x in self.sidecar['input_bindings']}
        for action in self.actions:
            path = action['path']
            for port in action['input_ports']:
                sources = by_input[port['path']]['sources']
                _need(len(sources) == 1, 'alternative_value_binding_not_supported')
                reference = self.inputs[path][port['ordinal']]
                _need(reference in refs, 'input_reference')
                ref = refs[reference]
                if ref.get('source') == 'input':
                    schema = self.bindings['input_schema']
                else:
                    producer = self.bindings['nodes'].get(ref.get('node_id'))
                    _need(type(producer) is dict, 'input_producer')
                    schema = producer['output_schema'] if producer['kind'] == 'human' else producer['execution']['output_schema']
                self._field_contract(schema, ref['field'], by_input[port['path']]['contract'])
                consumer = self.bindings['nodes'][path]
                if consumer['kind'] == 'ability':
                    execution = consumer['execution']
                    for field, consumed in execution['input_bindings'].items():
                        if consumed == reference:
                            self._field_contract(execution['input_schema'], field, by_input[port['path']]['contract'])
                source = sources[0]
                if source in entries:
                    _need(refs[reference] == refs[self.bindings['entries'][source]], 'entry_source_changed')
                else:
                    producer, ordinal = source.rsplit('/outputs/', 1)
                    _need(producer in self.action_ids and refs[reference].get('source') == 'output'
                        and refs[reference].get('node_id') == producer
                        and refs[reference].get('field') == self.outputs[producer][int(ordinal)], 'output_source_changed')

    def control(self, path, kind, **extra):
        identity = 'c_' + str(len(self.controls) + 1).zfill(2)
        self.nodes.append({'id': identity, 'kind': kind, 'source_step_id': None,
            'guards': _guards(), **extra})
        self.controls.append({'ast_path': path, 'graph_node_id': identity, 'kind': kind})
        return identity

    def edge(self, source, target, paths, *, on='success', branch=None, loop=None):
        identity = 'e_' + str(len(self.edges) + 1).zfill(3)
        self.edges.append({'id': identity, 'from': source, 'to': target,
            'on': on, 'branch_id': branch, 'loop_id': loop})
        self.edge_map.append({'graph_edge_id': identity, 'ast_paths': list(paths)})
        return identity

    def predicate(self, path, action_path, supplied):
        key = supplied['predicate_id']
        _need(key in self.bindings['predicates'], 'predicate_missing')
        predicate = self.bindings['predicates'][key]
        # Branch outcomes compare an actual result port to an explicit scalar
        # policy value. Ref=ref, decorated constants and compound expressions
        # cannot masquerade as consumption of the confirmation result.
        _need(type(predicate) is dict and set(predicate) == {'op', 'left', 'right'}
            and predicate['op'] in {'eq', 'ne', 'lt', 'lte', 'gt', 'gte'}
            and type(predicate['left']) is dict and set(predicate['left']) == {'op', 'key'}
            and predicate['left']['op'] == 'ref' and type(predicate['right']) is dict
            and set(predicate['right']) == {'op', 'kind', 'value'} and predicate['right']['op'] == 'value',
            'decision_comparison_shape')
        refs = native._predicate_keys(predicate)
        output_fields = set(self.outputs[action_path].values())
        def own_output(ref):
            source = self.bindings['references'].get(ref, {})
            return (source.get('source') == 'output' and source.get('node_id') == action_path
                and source.get('field') in output_fields)
        _need(all(own_output(ref) for ref in refs),
            'predicate_dependency_not_consumed')
        _need(any(own_output(ref) for ref in refs),
            'decision_result_not_consumed')
        decision = self.control(path, 'decision', predicate_id=key)
        self.edge(self.action_ids[action_path], decision, [action_path, path])
        return decision

    def block(self, phases, path, following):
        entry = following
        for i in reversed(range(len(phases))):
            phase, key = phases[i], path + '/' + str(i)
            kind = phase['kind']
            if kind == 'steps':
                entry = self.steps(phase['steps'], key + '/steps', entry)
            elif kind == 'decision':
                decision = self.predicate(key, key + '/decision', self.conditions[key])
                for outcome in ('true', 'false'):
                    branch_index = self.conditions[key][outcome + '_branch_index']
                    branch_path = key + '/branches/' + str(branch_index)
                    target = self.block(phase['branches'][branch_index]['phases'], branch_path + '/phases', entry)
                    self.edge(decision, target, [key, branch_path], on=outcome)
                entry = self.action_ids[key + '/decision']
            elif kind == 'parallel':
                _need(phase['wait_mode'] == 'all', 'any_join_not_supported')
                # Reserve both IDs before recursing, retaining each fork branch
                # identity even when its conditional body has an empty exit.
                fork = 'c_' + str(len(self.controls) + 1).zfill(2)
                join = 'c_' + str(len(self.controls) + 2).zfill(2)
                branches = ['b_' + str(j) for j in range(len(phase['branches']))]
                _need(self.control(key, 'fork', join_id=join, branch_ids=branches) == fork, 'fork_identity')
                _need(self.control(key, 'join', fork_id=fork, policy='all') == join, 'join_identity')
                self.edge(self.action_ids[key + '/dispatch'], fork, [key + '/dispatch', key])
                self.edge(join, entry, [key + '/join_attribution'])
                for j, branch in enumerate(phase['branches']):
                    branch_path = key + '/branches/' + str(j)
                    target = self.block(branch['phases'], branch_path + '/phases', join)
                    self.edge(fork, target, [key, branch_path], on='branch', branch=branches[j])
                entry = self.action_ids[key + '/dispatch']
            else:
                spec = self.loop_specs[key]
                decision = self.predicate(key, key + '/check', spec)
                body = self.steps(phase['steps'], key + '/steps', self.action_ids[key + '/check'])
                loop = 'loop_' + str(len(self.loops) + 1)
                back = self.edge(decision, body, [key, key + '/repeat_if'], on=spec['repeat_on'], loop=loop)
                self.edge(decision, entry, [key, key + '/finish_if'],
                    on='false' if spec['repeat_on'] == 'true' else 'true')
                self.loops.append({'id': loop, 'edge_id': back, 'max_iterations': spec['max_iterations']})
                entry = body
        return entry

    def steps(self, actions, path, following):
        entry = following
        for i in reversed(range(len(actions))):
            source = self.action_ids[path + '/' + str(i)]
            self.edge(source, entry, [path + '/' + str(i)])
            entry = source
        return entry

    def build(self):
        completion = self.action_ids[self.flow_path + '/completion']
        end = self.control(self.flow_path + '/completion', 'end')
        self.edge(completion, end, [self.flow_path + '/completion'])
        entry = self.block(self.flow['phases'], self.flow_path + '/phases', completion)
        refs = copy.deepcopy(self.bindings['references'])
        for ref in refs.values():
            if ref.get('source') == 'output':
                _need(ref.get('node_id') in self.action_ids, 'output_reference_outside_flow')
                ref['node_id'] = self.action_ids[ref['node_id']]
        graph = {'schema': native.SCHEMA, 'id': 'nested_flow_' + str(self.flow_index), 'entry': entry,
            'input_schema': copy.deepcopy(self.bindings['input_schema']), 'references': refs,
            'predicates': copy.deepcopy(self.bindings['predicates']), 'guards': copy.deepcopy(self.bindings['guards']),
            'nodes': self.nodes, 'edges': self.edges, 'loops': self.loops, 'subscriptions': [],
            'goals': [{'id': 'completed', 'required_nodes': [completion]}]}
        checked = native.validate_graph(graph)
        _need(checked == graph, 'native_value_changed')
        value = {'schema': SCHEMA, 'source_plan': copy.deepcopy(self.plan), 'source_plan_hash': digest(self.plan),
            'scope_binding_hash': digest(self.binding), 'flow_index': self.flow_index,
            'execution_bindings': copy.deepcopy(self.bindings), 'causal_bindings': copy.deepcopy(self.sidecar),
            'causal_analysis_hash': self.analysis['availability_hash'], 'graph': checked, 'graph_hash': digest(checked),
            'source_mapping': {'actions': self.action_map, 'controls': self.controls, 'edges': self.edge_map},
            'human_inputs_are_mapping_metadata': True, **FLAGS}
        native._safe(value)
        return {**value, 'translation_hash': digest(value)}


def build_execution_graph(plan, flow_index, execution_bindings, *, scope_binding, causal_bindings):
    """Require complete explicit bindings; unsupported control fails as a whole."""
    try:
        return _Compiler(plan, flow_index, execution_bindings, scope_binding, causal_bindings).build()
    except ValueError:
        raise
    except (KeyError, TypeError, IndexError, AttributeError, RecursionError, OverflowError):
        raise ValueError('nested_execution_invalid_binding') from None


def check_execution_graph(result, plan, flow_index, execution_bindings, *, scope_binding, causal_bindings):
    expected = build_execution_graph(plan, flow_index, execution_bindings,
        scope_binding=scope_binding, causal_bindings=causal_bindings)
    _need(native._safe(result) == native._safe(expected), 'projection_changed')
    return expected
