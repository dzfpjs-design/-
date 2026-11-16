"""Pure, loss-preserving lowering of ONE explicitly selected flow.

``execution_bindings`` is a closed JSON object with EXACT keys::

    schema, input_schema, references, predicates, guards, nodes, conditions,
    parallel_groups, loops, goals

schema is ``business-flow-execution-bindings-v1``.  input_schema, references,
predicates and guards use full_semantic_graph_v1's existing contracts.  nodes is
a map keyed by EVERY selected source node_id (no extra keys).  A human entry is
exactly {kind, actor_id, role, output_schema, input_ports, output_ports, guards};
an ability entry is exactly {kind, actor_id, execution, input_ports,
output_ports, guards}.  input_ports maps EVERY original input label to a graph
reference key; output_ports maps EVERY original output label to an actual,
required output-schema field.  execution uses the existing graph's TEN fields,
not workflow_abilities.pin's legacy step envelope.  The source actor must match
human or the corresponding business/perception/data ability role.
For abilities every original input_ports reference must actually occur in
execution.input_bindings.values(); a constant cannot replace that consumption.
Different original labels may explicitly use the same reference, and additional
registered server inputs remain allowed.  Human input_ports are preservation
metadata: the native human node has no input-consumption mechanism, so the
calling task UI must expose these actual bound inputs before claiming them as
shown to the operator.  This pure translation does not prove that UI behavior.

conditions is [{true_edge_index, false_edge_index, predicate_id}]: two distinct
original condition edges from the same source, explicitly assigned outcomes.
parallel_groups is [{group, wait_edge_index}]: an original parallel group paired
with one original wait(all).  loops is [{edge_index, max_iterations}]: explicit
original sequence/condition back edges, native limit 1..100.  goals is
[{id, required_nodes, deliverable_indices}]: original receipt-node IDs and an
exact partition of the selected flow's deliverables; every declared producer
of a mapped deliverable must be required by its goal.  All positions are strict
zero-based integers, never booleans/floats.  No field has an inferred default.
An output reference's supplied node_id is the ORIGINAL selected node_id; the
converter replaces it with its deterministic runtime ID.  A goal cannot demand
receipts from both exclusive sides of the same decision in one loop context.

V1 retains only its manual trigger.  V2 is
``business-flow-execution-bindings-v2`` with exactly the same ten keys plus
``trigger: {mode: 'signal_graph_anomaly_v134'}``; it is valid ONLY when the
selected source trigger.kind is event.  This declares the separate existing
signal-graph anomaly route, not a native subscription or routing permission.
The caller must independently verify that instance's actual graph handoff and
route authorization before creating any Task.  No Job, Task, subscription,
automation, device or acquisition authorization is created here.
task_center/execution_service,
any-joins and ambiguous/unstructured control are blocked, never relabelled.
Original source_value (ALL flows and quotes), bindings and every source edge,
port and deliverable remain in the detached result.  Control nodes are explicit
synthetic implementation details.  No original answer or source is rewritten.

The caller must independently cold-check the saved author-review/source,
current members, registered capability/schema/hash, automation, external data
consent, object origins and configuration CAS.  This module does NO IO and
grants NO source, scheduling, tool, adoption, publication or execution authority.
"""
import copy
import hashlib
import math

from jsonschema import Draft202012Validator, FormatChecker

from . import full_semantic_graph_v1 as native
from . import report_business_flow_wire_v182 as wire
from .typed_payload_codec_v131 import canonical_json


SCHEMA = 'business-flow-execution-graph-v1'
BINDINGS = 'business-flow-execution-bindings-v1'
BINDINGS_V2 = 'business-flow-execution-bindings-v2'
EVENT_TRIGGER_MODE = 'signal_graph_anomaly_v134'
MAX_BYTES = 8 * 1024 * 1024
FLAGS = {'semantic_quality_verified': False, 'can_apply': False,
    'can_publish': False, 'can_execute': False}
BINDING_KEYS = {'schema', 'input_schema', 'references', 'predicates', 'guards',
    'nodes', 'conditions', 'parallel_groups', 'loops', 'goals'}
BINDING_KEYS_V2 = BINDING_KEYS | {'trigger'}
_ACTORS = {'business_agent': 'business', 'perception_agent': 'perception', 'data_agent': 'data'}
_EXECUTION_KEYS = {'capability_id', 'capability_hash', 'agent_role', 'input_bindings',
    'arguments', 'input_schema', 'output_schema', 'adapter', 'release_version', 'max_attempts'}


def _bytes(value):
    def walk(item, depth=0):
        if depth > 64: raise ValueError('execution_graph_json_depth')
        if item is None or type(item) in {bool, int}: return
        if type(item) is float:
            if not math.isfinite(item): raise ValueError('execution_graph_json_number')
            return
        if type(item) is str:
            if '\x00' in item: raise ValueError('execution_graph_json_text')
            item.encode('utf-8', errors='strict'); return
        if type(item) is list:
            for child in item: walk(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for key, child in item.items(): walk(key, depth + 1); walk(child, depth + 1)
            return
        raise ValueError('execution_graph_json_type')
    try:
        walk(value)
        raw = canonical_json(value).encode('utf-8', errors='strict')
        if len(raw) > MAX_BYTES: raise ValueError('execution_graph_capacity')
        return raw
    except (TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError('execution_graph_json') from None


def _hash(value): return hashlib.sha256(_bytes(value)).hexdigest()


def _same(left, right): return _bytes(left) == _bytes(right)


def _position(value, count): return type(value) is int and 0 <= value < count


class _Builder:
    def __init__(self, value, flow_id, bindings, scope_binding):
        # Real old strong shape/graph validation; no repair/model_dump defaults.
        self.value = wire.parse_wire(_bytes(value), scope_binding=scope_binding)
        if not _same(self.value, value): raise ValueError('execution_graph_source_changed')
        matches = [(i, item) for i, item in enumerate(self.value['business']['flows'])
            if item['flow_id'] == flow_id]
        if len(matches) != 1: raise ValueError('execution_graph_flow_selection')
        self.flow_index, self.flow = matches[0]
        self.flow_id = flow_id; self.bindings = copy.deepcopy(bindings); _bytes(bindings)
        self.issues = []; self.nodes = []; self.edges = []; self.loops = []; self.goals = []
        self.by_id = {node['node_id']: node for node in self.flow['nodes']}
        self.node_ids = {key: 'n_' + _hash([flow_id, key])[:24] for key in self.by_id}
        self.source_edges = self.flow['edges']; self.tails = {}; self.groups = []
        self.outgoing = {key: [] for key in self.by_id}
        for index, edge in enumerate(self.source_edges):
            for key in edge.get('from_nodes', [edge.get('from_node')]):
                self.outgoing[key].append(index)
        base = '/business/flows/' + str(self.flow_index)
        self.mapping = {'source_value_hash': _hash(self.value),
            'scope_binding_hash': _hash(scope_binding), 'flow_hash': _hash(self.flow),
            'flows': [{'flow_id': item['flow_id'], 'source_hash': _hash(item),
                'selected': item['flow_id'] == flow_id} for item in self.value['business']['flows']],
            'nodes': [{'source_path': base + '/nodes/' + str(i), 'source_node_id': item['node_id'],
                'source_hash': _hash(item), 'graph_node_id': self.node_ids[item['node_id']],
                'input_ports': [], 'output_ports': []} for i, item in enumerate(self.flow['nodes'])],
            'edges': [{'source_path': base + '/edges/' + str(i), 'source_edge_index': i,
                'source_hash': _hash(item), 'kind': item['kind'],
                'from_nodes': copy.deepcopy(item.get('from_nodes', [item.get('from_node')])),
                'to_node': item['to_node'], 'graph_edge_ids': [], 'synthetic_node_ids': [],
                'origin_arrivals': []} for i, item in enumerate(self.source_edges)],
            'deliverables': [], 'synthetic_nodes': [], 'parallel_groups': [],
            'terminals': [], 'loops': [],
            'evidence': [{'source_path': '/evidence/' + str(i), 'span_ref': item['span_ref'],
                'quote_hash': _hash(item['quote'])} for i, item in enumerate(self.value['evidence'])]}

    def issue(self, code, path, message):
        row = {'code': code, 'path': path, 'message': message}
        if row not in self.issues: self.issues.append(row)

    def keys(self, value, expected, path):
        okay = type(value) is dict and set(value) == set(expected)
        if not okay: self.issue('binding_shape', path, '请提供这一项的完整闭合字段，不补默认值或忽略额外字段。')
        return okay

    def collection(self, name, limit):
        value = self.bindings[name]
        if type(value) is not list or len(value) > limit:
            self.issue('binding_shape', '/execution_bindings/' + name, '此绑定清单类型或数量无效。'); return []
        return value

    def control(self, identity, kind, source_indices, **extra):
        self.nodes.append({'id': identity, 'kind': kind, 'source_step_id': None,
            'guards': {key: None for key in native.GUARDS}, **extra})
        self.mapping['synthetic_nodes'].append({'id': identity, 'kind': kind,
            'source_edge_indices': list(source_indices)})

    def edge(self, identity, source, target, on='success', *, branch=None, origins=()):
        self.edges.append({'id': identity, 'from': source, 'to': target, 'on': on,
            'branch_id': branch, 'loop_id': None})
        for index in origins: self.mapping['edges'][index]['graph_edge_ids'].append(identity)
        return identity

    def node_bindings(self):
        rows = self.bindings['nodes']; refs = self.bindings['references']
        if type(rows) is not dict or set(rows) != set(self.by_id):
            self.issue('node_bindings_incomplete', '/execution_bindings/nodes',
                '每个原步骤都须恰好一个执行绑定，不能缺少、增加或替换职责。')
            if type(rows) is not dict: return
        for index, original in enumerate(self.flow['nodes']):
            key = original['node_id']; path = '/execution_bindings/nodes/' + key
            supplied = rows.get(key); actor = original['actor']; mapping = self.mapping['nodes'][index]
            if actor not in {'human', *_ACTORS}:
                self.issue('unsupported_actor', mapping['source_path'] + '/actor',
                    '任务中心或执行服务尚无对应运行节点；不能改名为智能体或删除此职责。'); continue
            kind = 'human' if actor == 'human' else 'ability'
            fields = {'kind', 'actor_id', 'input_ports', 'output_ports', 'guards'} | (
                {'role', 'output_schema'} if kind == 'human' else {'execution'})
            if not self.keys(supplied, fields, path): continue
            if supplied['kind'] != kind:
                self.issue('actor_kind_mismatch', path + '/kind', '执行节点类型必须与原职责相符。'); continue
            schema = supplied['output_schema'] if kind == 'human' else (
                supplied['execution'].get('output_schema') if type(supplied['execution']) is dict else None)
            if kind == 'human' and (supplied['role'] != original['role'] or len(original['role']) > 100):
                self.issue('human_role_mismatch', path + '/role', '岗位须保持原值且符合运行合同；不能截断或暗换岗位。')
            if kind == 'ability':
                execution = supplied['execution']
                if not self.keys(execution, _EXECUTION_KEYS, path + '/execution'): continue
                if execution['agent_role'] != _ACTORS[actor]:
                    self.issue('agent_role_mismatch', path + '/execution/agent_role', '智能体执行角色与原职责不一致。')
                try:
                    # Validate the native LOCAL-only schema contract BEFORE
                    # asking jsonschema to resolve a constant's definition.
                    # In particular an untrusted external $ref must never
                    # cause retrieval even on a subsequently blocked graph.
                    native._schema(execution['input_schema'])
                    native._schema(execution['output_schema'])
                    for field, argument in execution['arguments'].items():
                        definition = execution['input_schema']['properties'][field]
                        Draft202012Validator(execution['input_schema'], format_checker=FormatChecker()).evolve(
                            schema=definition).validate(argument)
                except Exception:
                    self.issue('ability_argument_invalid', path + '/execution/arguments', '能力常量参数不符合实际输入合同。')
            for collection, source_names in (('input_ports', original['inputs']), ('output_ports', original['outputs'])):
                ports = supplied[collection]
                if type(ports) is not dict or set(ports) != set(source_names) or any(type(v) is not str for v in ports.values()):
                    self.issue('ports_incomplete', path + '/' + collection, '原输入及产出名称须逐项保留并绑定，不能裁掉或补名。'); continue
                if collection == 'input_ports':
                    for name, reference in ports.items():
                        mapping['input_ports'].append({'name': name, 'reference_key': reference})
                        if type(refs) is not dict or reference not in refs:
                            self.issue('input_reference_missing', path + '/input_ports', '原输入必须绑定实际声明的引用。')
                        if kind == 'ability':
                            actual = supplied['execution']['input_bindings']
                            if type(actual) is not dict or reference not in actual.values():
                                self.issue('original_input_not_consumed', path + '/input_ports',
                                    '原输入引用必须实际进入能力的运行参数，不能只保留装饰映射或改用常量。')
                else:
                    if len(set(ports.values())) != len(ports):
                        self.issue('outputs_collapsed', path + '/output_ports', '两个原产出不能静默合并成一个运行字段。')
                    for name, field in ports.items():
                        mapping['output_ports'].append({'name': name, 'field': field})
                        try:
                            native._field(schema, field)
                            if field not in schema.get('required', []): raise ValueError()
                        except (ValueError, KeyError, TypeError, AttributeError):
                            self.issue('output_field_missing', path + '/output_ports', '每项原产出须绑定真实且必需的输出字段。')
            self.nodes.append({'id': self.node_ids[key], 'kind': kind, 'source_step_id': key,
                'guards': copy.deepcopy(supplied['guards']), 'actor_id': supplied['actor_id'],
                **({'role': supplied['role'], 'output_schema': copy.deepcopy(schema)} if kind == 'human'
                    else {'execution': copy.deepcopy(supplied['execution'])})})

    def lower_references(self):
        refs = copy.deepcopy(self.bindings['references'])
        if type(refs) is not dict:
            self.issue('binding_shape', '/execution_bindings/references', '引用须使用原生闭合引用合同。'); return {}
        for key, reference in refs.items():
            if not self.keys(reference, {'kind', 'nullable', 'source', 'field', 'node_id'},
                    '/execution_bindings/references/' + key): continue
            if reference['source'] == 'output':
                original_id = reference['node_id']
                if type(original_id) is not str or original_id not in self.node_ids:
                    self.issue('output_reference_missing', '/execution_bindings/references/' + key,
                        '输出引用须指向本次选中流程的真实原步骤。')
                else: reference['node_id'] = self.node_ids[original_id]
        return refs

    def controls(self):
        count = len(self.source_edges); handled = set(); condition_sources = set()
        for position, row in enumerate(self.collection('conditions', 128)):
            path = '/execution_bindings/conditions/' + str(position)
            if not self.keys(row, {'true_edge_index', 'false_edge_index', 'predicate_id'}, path): continue
            ti, fi = row['true_edge_index'], row['false_edge_index']
            if (not _position(ti, count) or not _position(fi, count) or ti == fi
                    or self.source_edges[ti]['kind'] != 'condition' or self.source_edges[fi]['kind'] != 'condition'):
                self.issue('condition_pair_invalid', path, '请明确选择两条不同的原条件边作为真、假出口。'); continue
            true, false = self.source_edges[ti], self.source_edges[fi]; source = true['from_node']
            if false['from_node'] != source or source in condition_sources or set(self.outgoing[source]) != {ti, fi}:
                self.issue('condition_pair_invalid', path, '二路条件须同源且完整，不得混入其他出口或按顺序猜否则路径。'); continue
            if type(row['predicate_id']) is not str or type(self.bindings['predicates']) is not dict or row['predicate_id'] not in self.bindings['predicates']:
                self.issue('condition_predicate_missing', path, '条件须显式绑定已声明的类型化判断。'); continue
            try:
                if not native._predicate_keys(self.bindings['predicates'][row['predicate_id']]):
                    self.issue('condition_without_reference', path, '条件判断必须使用实际引用，不能以常量真替代业务条件。')
            except (KeyError, TypeError, AttributeError, RecursionError):
                self.issue('condition_predicate_invalid', path, '条件判断结构无法核对。')
            decision = 'decision_' + str(ti) + '_' + str(fi)
            self.control(decision, 'decision', [ti, fi], predicate_id=row['predicate_id'])
            connector = self.edge('condition_in_' + str(ti), self.node_ids[source], decision, origins=[ti, fi])
            for index, edge, outcome in ((ti, true, 'true'), (fi, false, 'false')):
                identity = self.edge('source_edge_' + str(index), decision, self.node_ids[edge['to_node']], outcome, origins=[index])
                self.tails[index] = identity
                self.mapping['edges'][index]['synthetic_node_ids'].append(decision)
            condition_sources.add(source); handled.update([ti, fi])
        original_groups = {}
        for i, edge in enumerate(self.source_edges):
            if edge['kind'] == 'parallel': original_groups.setdefault(edge['group'], []).append(i)
        paired = set(); supplied_groups = set()
        for position, row in enumerate(self.collection('parallel_groups', 128)):
            path = '/execution_bindings/parallel_groups/' + str(position)
            if not self.keys(row, {'group', 'wait_edge_index'}, path): continue
            group, wi = row['group'], row['wait_edge_index']
            if type(group) is not str or group not in original_groups or group in supplied_groups or not _position(wi, count) or wi in paired:
                self.issue('parallel_pair_invalid', path, '每个原并行组须唯一配对原全部完成汇聚，不按近邻猜测。'); continue
            indices = original_groups[group]; wait = self.source_edges[wi]
            if wait['kind'] != 'wait' or wait['mode'] != 'all' or len(wait['from_nodes']) != len(indices):
                self.issue('parallel_pair_invalid', path, '并行组须对应每分支一个原前驱的全部完成汇聚，不能改写任意完成。'); continue
            source = self.source_edges[indices[0]]['from_node']
            if set(self.outgoing[source]) != set(indices) or any(self.outgoing[key] != [wi] for key in wait['from_nodes']):
                self.issue('parallel_pair_invalid', path, '并行入口或各分支尾有额外出口，不能删边或越过汇聚。'); continue
            fork, join = 'fork_' + str(indices[0]), 'join_' + str(wi)
            branches = ['branch_' + str(i) for i in indices]
            self.control(fork, 'fork', indices, join_id=join, branch_ids=branches)
            self.control(join, 'join', [wi], fork_id=fork, policy='all')
            connector = self.edge('fork_in_' + str(indices[0]), self.node_ids[source], fork, origins=indices)
            for index, branch in zip(indices, branches):
                edge = self.source_edges[index]
                self.edge('source_edge_' + str(index), fork, self.node_ids[edge['to_node']], 'branch', branch=branch, origins=[index])
                self.mapping['edges'][index]['synthetic_node_ids'].extend([fork, join])
            arrivals = []
            for n, key in enumerate(wait['from_nodes']):
                identity = self.edge('wait_' + str(wi) + '_arrival_' + str(n), self.node_ids[key], join, origins=[wi])
                arrivals.append({'source_node_id': key, 'graph_edge_id': identity})
            self.edge('wait_' + str(wi) + '_out', join, self.node_ids[wait['to_node']], origins=[wi])
            self.mapping['edges'][wi]['synthetic_node_ids'].extend([fork, join])
            self.mapping['edges'][wi]['origin_arrivals'] = arrivals
            info = {'group': group, 'fork_id': fork, 'join_id': join, 'wait_edge_index': wi,
                'branches': [{'source_edge_index': i, 'branch_id': branch,
                    'source_node_id': self.source_edges[i]['to_node']} for i, branch in zip(indices, branches)],
                'from_nodes': copy.deepcopy(wait['from_nodes'])}
            self.groups.append(info); self.mapping['parallel_groups'].append(copy.deepcopy(info))
            supplied_groups.add(group); paired.add(wi); handled.update(indices); handled.add(wi)
        for index, edge in enumerate(self.source_edges):
            if index in handled: continue
            path = self.mapping['edges'][index]['source_path']
            if edge['kind'] == 'wait' and edge['mode'] == 'any':
                self.issue('any_join_unsupported', path, '现有运行内核不支持任意完成汇聚；不能改成全部完成或丢分支。'); continue
            if edge['kind'] == 'sequence' or edge['kind'] == 'wait' and len(edge['from_nodes']) == 1:
                source = edge.get('from_node', edge.get('from_nodes', [None])[0])
                if self.outgoing[source] != [index]:
                    self.issue('ambiguous_outgoing', path, '原步骤有多个未经明确解释的出口，不能删边或暗加顺序。'); continue
                identity = self.edge('source_edge_' + str(index), self.node_ids[source], self.node_ids[edge['to_node']], origins=[index])
                self.tails[index] = identity
                if edge['kind'] == 'wait': self.mapping['edges'][index]['origin_arrivals'] = [
                    {'source_node_id': source, 'graph_edge_id': identity}]
                continue
            code = 'condition_unbound' if edge['kind'] == 'condition' else 'parallel_unbound' if edge['kind'] == 'parallel' else 'all_join_unbound'
            self.issue(code, path, '此原连线尚无完整、明确且可运行的控制绑定；原边保持不变。')
        for key in self.flow['end_nodes']:
            end = 'end_' + _hash(key)[:20]; self.control(end, 'end', [])
            identity = self.edge('terminal_' + _hash(key)[:20], self.node_ids[key], end)
            self.mapping['terminals'].append({'source_node_id': key, 'graph_end_id': end, 'graph_edge_id': identity})

    def bind_loops(self):
        seen = set(); count = len(self.source_edges)
        for position, row in enumerate(self.collection('loops', 16)):
            path = '/execution_bindings/loops/' + str(position)
            if not self.keys(row, {'edge_index', 'max_iterations'}, path): continue
            index, limit = row['edge_index'], row['max_iterations']
            if (not _position(index, count) or index in seen or type(limit) is not int or not 1 <= limit <= 100
                    or self.source_edges[index]['kind'] not in {'sequence', 'condition'} or index not in self.tails):
                self.issue('loop_binding_invalid', path, '循环须指定真实回边及严格次数上限，不能猜回边、使用布尔值或无界次数。'); continue
            identity = 'loop_' + str(index); target = next(edge for edge in self.edges if edge['id'] == self.tails[index])
            target['loop_id'] = identity
            self.loops.append({'id': identity, 'edge_id': target['id'], 'max_iterations': limit})
            self.mapping['loops'].append({'source_edge_index': index, **copy.deepcopy(self.loops[-1])}); seen.add(index)

    def bind_goals(self):
        count = len(self.flow['deliverables']); covered = set(); ids = set()
        bindings = self.bindings['nodes'] if type(self.bindings['nodes']) is dict else {}
        for position, row in enumerate(self.collection('goals', 32)):
            path = '/execution_bindings/goals/' + str(position)
            if not self.keys(row, {'id', 'required_nodes', 'deliverable_indices'}, path): continue
            required, indices = row['required_nodes'], row['deliverable_indices']
            if (type(row['id']) is not str or row['id'] in ids or type(required) is not list or not required
                    or any(type(key) is not str or key not in self.by_id for key in required)
                    or len(set(required)) != len(required) or type(indices) is not list or not indices
                    or any(not _position(i, count) for i in indices) or len(set(indices)) != len(indices)
                    or covered.intersection(indices)):
                self.issue('goal_binding_invalid', path, '完成标准须明确真实回执节点，并将每项原最终产出恰好绑定一次。'); continue
            for index in indices:
                original = self.flow['deliverables'][index]; producers = []
                if not set(original['produced_by']) <= set(required):
                    self.issue('deliverable_producer_missing', path, '完成标准不能遗漏原产出声明的任何生产步骤。')
                for key in original['produced_by']:
                    ports = bindings.get(key, {}).get('output_ports', {}) if type(bindings.get(key)) is dict else {}
                    producers.append({'source_node_id': key, 'graph_node_id': self.node_ids[key],
                        'output_field': ports.get(original['name']) if type(ports) is dict else None})
                self.mapping['deliverables'].append({'source_path': '/business/flows/' + str(self.flow_index) + '/deliverables/' + str(index),
                    'source_index': index, 'source_hash': _hash(original), 'goal_id': row['id'], 'producers': producers})
            self.goals.append({'id': row['id'], 'required_nodes': [self.node_ids[key] for key in required]})
            covered.update(indices); ids.add(row['id'])
        if covered != set(range(count)):
            self.issue('deliverables_unmapped', '/execution_bindings/goals', '所有原最终产出都必须有完整的实际输出和完成回执映射。')

    def build(self):
        event_binding = type(self.bindings) is dict and self.bindings.get('schema') == BINDINGS_V2
        expected_keys = BINDING_KEYS_V2 if event_binding else BINDING_KEYS
        if not self.keys(self.bindings, expected_keys, '/execution_bindings'): return self.finish(None)
        if self.bindings['schema'] not in (BINDINGS, BINDINGS_V2):
            self.issue('binding_family_unknown', '/execution_bindings/schema', '执行绑定版本未知，不能降级使用其他合同。')
        if event_binding:
            trigger = self.bindings['trigger']
            if self.keys(trigger, {'mode'}, '/execution_bindings/trigger'):
                if trigger['mode'] != EVENT_TRIGGER_MODE:
                    self.issue('trigger_mode_unknown', '/execution_bindings/trigger/mode',
                        '仅支持明确选择既有信号图异常路由；不能按文字猜测触发方式。')
            if self.flow['trigger']['kind'] != 'event':
                self.issue('trigger_not_connected', '/business/flows/' + str(self.flow_index) + '/trigger',
                    '此绑定仅声明既有信号图异常事件入口，不能用于人工、定时或数据到达触发。')
        elif self.flow['trigger']['kind'] != 'manual':
            self.issue('trigger_not_connected', '/business/flows/' + str(self.flow_index) + '/trigger',
                '此触发方式尚未绑定真实运行入口；不能把文字计划或事件当成已接通的触发器。')
        if self.value['business']['questions']:
            self.issue('unresolved_source_questions', '/business/questions', '仍有明确待核问题，不能作为完整运行设计。')
        self.node_bindings(); references = self.lower_references()
        self.controls(); self.bind_loops(); self.bind_goals()
        for row in self.mapping['edges']:
            if not row['graph_edge_ids']:
                self.issue('source_edge_unmapped', row['source_path'], '这条原连线没有运行映射；原文未删除，转换已阻止。')
        graph = {'schema': native.SCHEMA, 'id': 'flow_' + _hash([self.value, self.flow_id, self.bindings])[:24],
            'entry': self.node_ids[self.flow['start_node']], 'input_schema': copy.deepcopy(self.bindings['input_schema']),
            'references': references, 'predicates': copy.deepcopy(self.bindings['predicates']),
            'guards': copy.deepcopy(self.bindings['guards']), 'nodes': self.nodes, 'edges': self.edges,
            'loops': self.loops, 'subscriptions': [], 'goals': self.goals}
        if not self.issues:
            try:
                graph = native.validate_graph(graph)
                metadata = native._metadata(graph)
                for group in self.groups:
                    scopes = [metadata['scopes'][self.node_ids[key]] for key in group['from_nodes']]
                    expected = {(group['fork_id'], row['branch_id']) for row in group['branches']}
                    if (any(not scope or scope[-1] not in expected for scope in scopes)
                            or len({scope[-1] for scope in scopes}) != len(expected)):
                        self.issue('parallel_arrivals_ambiguous', '/execution_bindings/parallel_groups',
                            '全部完成汇聚须每条实际并行分支恰有一个原前驱，不能重复一支代替另一支。')
                # The native kernel permits declarative goals that no selected
                # branch can satisfy.  Reject the concrete binary contradiction
                # instead of presenting that known dead end as a translation.
                # Ignore explicit back edges: receipts must match the current
                # loop context, not combine different iterations as evidence.
                def reachable(start):
                    seen, pending = set(), [start]
                    while pending:
                        node_id = pending.pop()
                        if node_id in seen: continue
                        seen.add(node_id)
                        pending.extend(edge['to'] for edge in self.edges
                            if edge['from'] == node_id and edge['loop_id'] is None)
                    return seen
                for node in self.nodes:
                    if node['kind'] != 'decision': continue
                    exits = {edge['on']: edge['to'] for edge in self.edges if edge['from'] == node['id']}
                    left, right = reachable(exits['true']), reachable(exits['false'])
                    for index, goal in enumerate(self.goals):
                        required = set(goal['required_nodes'])
                        if required.intersection(left - right) and required.intersection(right - left):
                            self.issue('goal_mutually_exclusive', '/execution_bindings/goals/' + str(index),
                                '此完成标准同时要求互斥条件两侧的回执，任何一次选择都不能满足；请明确完成含义。')
            except (ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError, OverflowError):
                self.issue('native_graph_invalid', '/execution_bindings',
                    '原生图合同拒绝此结构、类型、引用或循环；不能串行化、放宽或部分运行。')
        return self.finish(None if self.issues else graph)

    def finish(self, graph):
        value = {'schema': SCHEMA, 'graph': graph, 'source_value': copy.deepcopy(self.value),
            'flow_id': self.flow_id, 'unselected_flow_ids': [row['flow_id'] for row in self.value['business']['flows']
                if row['flow_id'] != self.flow_id], 'execution_bindings': copy.deepcopy(self.bindings),
            'source_mapping': copy.deepcopy(self.mapping), 'issues': copy.deepcopy(self.issues), **FLAGS}
        return {**value, 'translation_hash': _hash(value)}


def build_execution_graph(value, flow_id, execution_bindings, *, scope_binding):
    """Detached pure translation; issues always imply graph=None, never fallback."""
    return _Builder(value, flow_id, execution_bindings, scope_binding).build()


def check_execution_graph(result, value, flow_id, execution_bindings, *, scope_binding):
    """Full deterministic rebuild, not a self-reported hash/authority check."""
    expected = build_execution_graph(value, flow_id, execution_bindings, scope_binding=scope_binding)
    if type(result) is not dict or not _same(result, expected):
        raise ValueError('execution_graph_translation_mismatch')
    return expected
