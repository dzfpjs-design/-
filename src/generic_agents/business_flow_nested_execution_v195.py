"""Independent V195 lowering with explicit exclusive upstream products.

The source remains the complete V192 AST. Frozen V192 control/actor/compiler
methods are reused without changing their source or relabelling their bindings.
This module has its own binding and translation contracts and exact port gate.
Any joins and mixed entry/output alternatives are still rejected. Service
actors require an explicit side-effecting workflow_service delegation; this
pure compiler does not establish that the registered capability or handler is
available. Entry facts, members, capabilities, source/current authority,
real human-input presentation and adoption remain the caller's separate gates.
"""
import copy

from . import business_flow_nested_execution_v192 as original
from . import business_flow_nested_plan_v192 as plans
from . import business_flow_nested_causal_v192 as causal
from . import full_semantic_graph_v1 as native
from .contracts import digest
from .typed_payload_codec_v131 import canonical_json


SCHEMA = 'business-flow-nested-execution-v195'
BINDINGS = 'business-flow-nested-execution-bindings-v195'
EXCLUSIVE_REFERENCE = native.EXCLUSIVE_REFERENCE
ACTORS = original.ACTORS
SERVICE_OPERATIONS = {'task_center': 'in_app_notice',
    'execution_service': 'resense_with_receipt', 'reader_service': 'create_goal_evaluation_packet_v288'}
SERVICE_CHOICES = {'task_center': {'in_app_notice', 'dispatch_tasks'},
    'execution_service': {'resense_with_receipt', 'resense_from_context', 'adjust_monitoring_schedule',
        'execute_registered_tool_plan', 'plan_business_decision_v277', 'plan_business_decision_review_v285', 'plan_approved_goal_data_v288'},
    'reader_service': {'create_goal_evaluation_packet_v288'}}
FLAGS = original.FLAGS
_need, _keys, _index = original._need, original._keys, original._index


def service_delegation(service_actor, operation):
    """Closed proposed delegation, never a runtime permission or completion."""
    _need(type(service_actor) is str and service_actor in SERVICE_OPERATIONS
        and type(operation) is str and operation in SERVICE_CHOICES[service_actor],
        'service_delegation')
    return {'service_actor': service_actor, 'operation': operation,
        'agent_role': 'data' if service_actor == 'reader_service' else 'business', 'side_effect': True}


def graph_action_ids(plan, flow_index, *, scope_binding):
    """Server-derived path -> graph ID, with the compiler's exact checked walk.

    These identities do not establish source truth, membership, capability
    publication, adoption, or permission to execute a node.
    """
    walked = plans.walk_action_ports(plan, scope_binding=scope_binding)
    _need(_index(flow_index, len(plan['business']['flows'])), 'flow_index')
    actions = [row for row in walked['actions'] if row['flow_index'] == flow_index]
    return {row['path']: 'a_' + str(index + 1).zfill(2) for index, row in enumerate(actions)}


class _Compiler(original._Compiler):
    @staticmethod
    def _whole_output(schema,field):
        # A real published '$output' property retains its legacy meaning.
        return field == '$output' and field not in schema.get('required',[])

    def _projection_covered(self, path, key, primary):
        refs = self.bindings['references']
        if key in primary: return True
        ref = refs.get(key)
        if not native._projection(ref) or ref['consumer_node_id'] != path: return False
        return any(native._projection(refs.get(other))
            and refs[other]['consumer_node_id'] == path
            and canonical_json(refs[other]['origin']) == canonical_json(ref['origin']) for other in primary)

    def _output_contract(self, schema, field, contract):
        if not self._whole_output(schema,field): return self._field_contract(schema, field, contract)
        from .guarded_port_projection_v198 import derive_schema
        ref = {'schema':'guarded-port-projection-v198','source':'port_projection','kind':'object',
            'nullable':False,'unit':schema.get('x-unit'),'consumer_node_id':'consumer',
            'origin':{'node_id':'producer','port_ordinal':0,'output_pointer':''},
            'projection':{'op':'copy','pointer':''}}
        root = derive_schema(schema, ref)
        return self._field_contract({'type':'object','properties':{'value':root}},'value',contract)

    def __init__(self, plan, flow_index, supplied, scope_binding, causal_bindings):
        native._safe(supplied); native._safe(causal_bindings)
        self.plan = plans.check_plan(plan, scope_binding=scope_binding)
        _need(_index(flow_index, len(self.plan['business']['flows'])), 'flow_index')
        self.analysis = causal.analyze_availability(self.plan, causal_bindings, scope_binding=scope_binding)
        self.sidecar = causal.check_bindings(self.plan, causal_bindings, scope_binding=scope_binding)
        self.binding = plans.check_binding(scope_binding)
        self.flow_index, self.flow_path = flow_index, '/business/flows/' + str(flow_index)
        self.flow = self.plan['business']['flows'][flow_index]
        self.bindings = copy.deepcopy(supplied)
        _keys(supplied, {'schema', 'input_schema', 'references', 'predicates', 'guards',
            'nodes', 'entries', 'conditions', 'loops', 'contract_schemas'}, 'binding_shape')
        _need(supplied['schema'] == BINDINGS, 'binding_schema')
        self.actions = [row for row in self.analysis['paths']['actions'] if row['flow_index'] == flow_index]
        self.action_ids = graph_action_ids(self.plan, flow_index, scope_binding=self.binding)
        self.nodes, self.edges, self.loops, self.controls = [], [], [], []
        self.action_map, self.edge_map = [], []
        self.conditions, self.loop_specs, self.phase_map = {}, {}, {}
        self._phases(self.flow['phases'], self.flow_path + '/phases')
        self._control_bindings(); self._contract_bindings()
        self._action_bindings(); self._port_bindings()

    def _action_bindings(self):
        rows = self.bindings['nodes']
        _need(type(rows) is dict and set(rows) == set(self.action_ids), 'action_coverage')
        self.inputs, self.outputs = {}, {}
        for action in self.actions:
            path, identity = action['path'], self.action_ids[action['path']]
            source_action = original._at(self.plan, path)
            actor, row = action['actor'], rows[path]
            service = actor in SERVICE_OPERATIONS
            _need(actor == 'human' or actor in ACTORS or service, 'service_actor_not_registered')
            kind = 'human' if actor == 'human' else 'ability'
            fields = {'kind', 'actor_id', 'guards', 'input_ports', 'output_ports'} | (
                {'role', 'output_schema'} if kind == 'human' else {'execution'})
            if service:
                fields |= {'service_actor', 'operation'}
                _need(type(row) is dict and row.get('service_actor') == actor
                    and 'operation' in row, 'service_actor_not_registered')
            _keys(row, fields, 'action_binding_shape')
            _need(row['kind'] == kind, 'actor_kind')
            delegation = None
            if kind == 'human':
                _need(row['role'] == source_action['role'], 'human_role')
                schema = row['output_schema']
                extra = {'role': row['role'], 'output_schema': copy.deepcopy(schema)}
            else:
                execution = row['execution']
                _need(type(execution) is dict, 'agent_role')
                if service:
                    delegation = service_delegation(row['service_actor'], row['operation'])
                    _need(execution.get('adapter') == 'workflow_service'
                        and execution.get('agent_role') == delegation['agent_role'], 'service_execution')
                else:
                    _need(execution.get('adapter') != 'workflow_service', 'service_actor_not_registered')
                    _need(execution.get('agent_role') == ACTORS[actor], 'agent_role')
                schema = execution['output_schema']
                extra = {'execution': copy.deepcopy(execution)}
            native._schema(schema)
            inputs = self._ports(row['input_ports'], 'reference', len(source_action['inputs']))
            outputs = self._ports(row['output_ports'], 'field', len(source_action['outputs']))
            for ordinal,field in outputs.items():
                if self._whole_output(schema,field):
                    _need(len(outputs) == 1, 'whole_output_requires_single_port')
                    _need(any(native._projection(ref) and type(ref.get('origin')) is dict
                        and ref['origin'].get('node_id') == path and type(ref['origin'].get('port_ordinal')) is int
                        and ref['origin']['port_ordinal'] == ordinal and ref['origin'].get('output_pointer') == ''
                        for ref in self.bindings['references'].values()), 'whole_output_requires_projection')
                    continue
                _need(field in schema.get('required', []), 'output_not_required')
                native._field(schema, field)
            output_contracts = {item['path']: item['contract'] for item in self.sidecar['output_contracts']}
            for ordinal, field in outputs.items():
                self._output_contract(schema, field, output_contracts[path + '/outputs/' + str(ordinal)])
            if kind == 'ability':
                physical = set(row['execution']['input_bindings'].values())
                _need(set(inputs.values()) <= physical
                    and all(self._projection_covered(path,key,set(inputs.values())) for key in physical),
                    'input_binding_coverage')
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
                        _need(self._projection_covered(path,reference,set(inputs.values())), 'undeclared_guard_dependency')
            self.inputs[path], self.outputs[path] = inputs, outputs
            self.nodes.append({'id': identity, 'kind': kind, 'source_step_id': identity,
                'guards': copy.deepcopy(row['guards']), 'actor_id': row['actor_id'], **extra})
            mapping = {'ast_path': path, 'graph_node_id': identity,
                'source_action': copy.deepcopy(source_action), 'source_action_hash': digest(source_action),
                'input_ports': copy.deepcopy(row['input_ports']), 'output_ports': copy.deepcopy(row['output_ports'])}
            if delegation is not None:
                mapping['service_delegation'] = delegation
            self.action_map.append(mapping)

    def _producer(self, source):
        producer, ordinal = source.rsplit('/outputs/', 1)
        _need(producer in self.action_ids and ordinal.isdecimal()
            and str(int(ordinal)) == ordinal and int(ordinal) in self.outputs[producer], 'output_source_changed')
        row = self.bindings['nodes'][producer]
        schema = row['output_schema'] if row['kind'] == 'human' else row['execution']['output_schema']
        return {'node_id': producer, 'field': self.outputs[producer][int(ordinal)]}, schema

    def _port_bindings(self):
        refs = self.bindings['references']
        _need(type(refs) is dict and type(self.bindings['entries']) is dict, 'reference_shape')
        entries = {row['path'] for row in self.sidecar['entry_ports'] if row['flow_path'] == self.flow_path}
        _need(set(self.bindings['entries']) == entries, 'entry_coverage')
        for key in self.bindings['entries'].values():
            _need(type(key) is str and key in refs and refs[key].get('source') == 'input'
                and refs[key]['field'] in self.bindings['input_schema'].get('required', []), 'entry_binding')
        for row in self.sidecar['entry_ports']:
            if row['flow_path'] == self.flow_path:
                ref = refs[self.bindings['entries'][row['path']]]
                self._field_contract(self.bindings['input_schema'], ref['field'], row['contract'])
        for fields in self.outputs.values():
            _need(len(set(fields.values())) == len(fields), 'output_ordinals_collapsed')

        by_input = {row['input_path']: row for row in self.sidecar['input_bindings']}
        exclusive_used, projection_used = set(), set()
        for action in self.actions:
            path = action['path']
            for port in action['input_ports']:
                declared = by_input[port['path']]
                sources, contract = declared['sources'], declared['contract']
                key = self.inputs[path][port['ordinal']]
                _need(key in refs and type(refs[key]) is dict, 'input_reference')
                ref = refs[key]
                if native._projection(ref):
                    _need(len(sources) == 1 and sources[0] not in entries, 'projection_single_declared_output')
                    expected, schema = self._producer(sources[0])
                    ordinal = int(sources[0].rsplit('/outputs/',1)[1])
                    pointer = '' if self._whole_output(schema,expected['field']) else '/' + expected['field'].replace('~','~0').replace('/','~1')
                    origin = {'node_id':expected['node_id'],'port_ordinal':ordinal,'output_pointer':pointer}
                    self._output_contract(schema,expected['field'],contract)
                    consumer = self.bindings['nodes'][path]
                    physical = set(consumer['execution']['input_bindings'].values()) if consumer['kind'] == 'ability' else {key}
                    for predicate in consumer['guards'].values():
                        if predicate is not None: physical.update(native._predicate_keys(self.bindings['predicates'][predicate]))
                    siblings = {item for item in physical if native._projection(refs.get(item))
                        and refs[item]['consumer_node_id'] == path and canonical_json(refs[item]['origin']) == canonical_json(origin)}
                    _need(key in siblings, 'projection_source_changed')
                    from .guarded_port_projection_v198 import derive_schema
                    for item in siblings:
                        derive_schema(schema,refs[item])
                    projection_used.update(siblings)
                    continue
                if len(sources) == 1:
                    _need(not native._exclusive(ref), 'single_source_as_exclusive')
                    source = sources[0]
                    if source in entries:
                        _need(ref == refs[self.bindings['entries'][source]], 'entry_source_changed')
                        self._field_contract(self.bindings['input_schema'], ref['field'], contract)
                    else:
                        expected, schema = self._producer(source)
                        _need(ref.get('source') == 'output' and ref.get('node_id') == expected['node_id']
                            and ref.get('field') == expected['field'], 'output_source_changed')
                        self._field_contract(schema, ref['field'], contract)
                else:
                    _need(2 <= len(sources) <= 16 and all(source not in entries for source in sources),
                        'exclusive_output_only')
                    _keys(ref, {'schema', 'source', 'kind', 'nullable', 'unit', 'consumer_node_id', 'candidates'},
                        'exclusive_reference_shape')
                    codec = self.contracts[digest(contract)]
                    _need(ref['schema'] == EXCLUSIVE_REFERENCE and ref['source'] == 'exclusive_output'
                        and ref['consumer_node_id'] == path and ref['kind'] == codec['kind']
                        and type(ref['nullable']) is bool and ref['nullable'] is codec['nullable']
                        and ref['unit'] == contract['unit'], 'exclusive_contract')
                    candidates = []
                    for source in sources:
                        candidate, schema = self._producer(source)
                        self._field_contract(schema, candidate['field'], contract)
                        candidates.append(candidate)
                    _need(canonical_json(ref['candidates']) == canonical_json(candidates)
                        and len({(row['node_id'], row['field']) for row in candidates}) == len(candidates),
                        'exclusive_sources_changed')
                    exclusive_used.add(key)
                consumer = self.bindings['nodes'][path]
                if consumer['kind'] == 'ability':
                    execution = consumer['execution']
                    for field, consumed in execution['input_bindings'].items():
                        if consumed == key:
                            self._field_contract(execution['input_schema'], field, contract)
        _need({key for key, ref in refs.items() if native._exclusive(ref)} == exclusive_used,
            'undeclared_exclusive_dependency')
        _need({key for key, ref in refs.items() if native._projection(ref)} == projection_used,
            'undeclared_projection_dependency')

    def build(self):
        completion = self.action_ids[self.flow_path + '/completion']
        end = self.control(self.flow_path + '/completion', 'end')
        self.edge(completion, end, [self.flow_path + '/completion'])
        entry = self.block(self.flow['phases'], self.flow_path + '/phases', completion)
        refs = copy.deepcopy(self.bindings['references'])
        for ref in refs.values():
            if native._projection(ref):
                _need(ref['consumer_node_id'] in self.action_ids and ref['origin']['node_id'] in self.action_ids,
                    'projection_outside_flow')
                ref['consumer_node_id'] = self.action_ids[ref['consumer_node_id']]
                ref['origin']['node_id'] = self.action_ids[ref['origin']['node_id']]
            elif native._exclusive(ref):
                _need(ref['consumer_node_id'] in self.action_ids, 'exclusive_consumer_outside_flow')
                ref['consumer_node_id'] = self.action_ids[ref['consumer_node_id']]
                for candidate in ref['candidates']:
                    _need(candidate['node_id'] in self.action_ids, 'exclusive_producer_outside_flow')
                    candidate['node_id'] = self.action_ids[candidate['node_id']]
            elif ref.get('source') == 'output':
                _need(ref.get('node_id') in self.action_ids, 'output_reference_outside_flow')
                ref['node_id'] = self.action_ids[ref['node_id']]
        graph = {'schema': native.SCHEMA, 'id': 'nested_flow_v195_' + str(self.flow_index), 'entry': entry,
            'input_schema': copy.deepcopy(self.bindings['input_schema']), 'references': refs,
            'predicates': copy.deepcopy(self.bindings['predicates']), 'guards': copy.deepcopy(self.bindings['guards']),
            'nodes': self.nodes, 'edges': self.edges, 'loops': self.loops, 'subscriptions': [],
            'goals': [{'id': 'completed', 'required_nodes': [completion]}]}
        checked = native.validate_graph(graph)
        _need(canonical_json(checked) == canonical_json(graph), 'native_value_changed')
        value = {'schema': SCHEMA, 'source_plan': copy.deepcopy(self.plan), 'source_plan_hash': digest(self.plan),
            'scope_binding_hash': digest(self.binding), 'flow_index': self.flow_index,
            'execution_bindings': copy.deepcopy(self.bindings), 'causal_bindings': copy.deepcopy(self.sidecar),
            'causal_analysis_hash': self.analysis['availability_hash'], 'graph': checked, 'graph_hash': digest(checked),
            'source_mapping': {'actions': self.action_map, 'controls': self.controls, 'edges': self.edge_map},
            'human_inputs_are_mapping_metadata': True, **FLAGS}
        native._safe(value)
        return {**value, 'translation_hash': digest(value)}


def build_execution_graph(plan, flow_index, execution_bindings, *, scope_binding, causal_bindings):
    """Explicit V195 bindings through the existing kernel, never fake receipts."""
    try:
        return _Compiler(plan, flow_index, execution_bindings, scope_binding, causal_bindings).build()
    except ValueError:
        raise
    except (KeyError, TypeError, IndexError, AttributeError, RecursionError, OverflowError):
        raise ValueError('nested_execution_v195_invalid_binding') from None


def check_execution_graph(result, plan, flow_index, execution_bindings, *, scope_binding, causal_bindings):
    expected = build_execution_graph(plan, flow_index, execution_bindings,
        scope_binding=scope_binding, causal_bindings=causal_bindings)
    _need(canonical_json(result) == canonical_json(expected), 'projection_changed')
    return expected
