"""Typed structured graphs and a deterministic, JSON-persistable state reducer.

No database, network, clock, model, tool or account is consulted. The caller
authenticates graphs, actors, event sources, clocks and real Invocation results.
Ready ability activations are work to dispatch, never fabricated tool receipts.
Every operation returns a new state and requires an explicit monotonically
advancing time. Hashes detect damage, not authority to alter persisted state.
"""
import copy
import hashlib
import json
import math
import re
from functools import wraps

from jsonschema import Draft202012Validator, FormatChecker

from .contracts import check_schema
from .full_semantic_predicate_v1 import validate_predicate, evaluate_predicate


SCHEMA = 'full-semantic-graph-v1'
STATE_SCHEMA = 'full-semantic-graph-state-v1'
MAX_NODES = 128
MAX_EDGES = 256
MAX_ACTIVATIONS = 4096
MAX_EVENTS = 1024
MAX_COMMANDS = 4096
MAX_RECEIPTS = 8192
MAX_BYTES = 8 * 1024 * 1024
EXCLUSIVE_REFERENCE = 'exclusive-output-reference-v195'
GUARDS = {'start', 'precondition', 'stop', 'exception'}
_KINDS = {'human', 'ability', 'decision', 'fork', 'join', 'wait', 'end'}
_TERMINAL = {'succeeded', 'cancelled', 'stopped', 'failed'}
_SETTLED = {'completed', 'timed_out', 'cancelled'}
_ACTIVE = {'waiting_guard', 'ready', 'waiting_event', 'waiting_join'}
_IDENTIFIER = re.compile(r'[-A-Za-z0-9_][-A-Za-z0-9_.:]{0,199}')
_HASH = re.compile(r'[a-f0-9]{64}')
_GRAPH_KEYS = {'schema', 'id', 'entry', 'input_schema', 'references', 'predicates',
    'guards', 'nodes', 'edges', 'loops', 'subscriptions', 'goals'}
_STATE_KEYS = {'schema', 'graph_hash', 'input_hash', 'inputs', 'status', 'version',
    'cancel_epoch', 'clock', 'next_activation', 'next_scope', 'activations', 'fork_scopes',
    'events', 'commands', 'goal_receipts', 'predicate_receipts', 'eligible_ability_ids',
    'ready_human_ids', 'error', 'state_hash'}
_ACTIVATION_KEYS = {'id', 'node_id', 'status', 'version', 'epoch', 'created_at', 'completed_at',
    'output', 'draft', 'context', 'scope_id', 'branch_id', 'deadline', 'wait_started_at',
    'correlation_hash', 'inputs_snapshot', 'error'}


def _public(function):
    @wraps(function)
    def call(*args, **kwargs):
        try: return function(*args, **kwargs)
        except ValueError: raise
        except (KeyError, TypeError, AttributeError, IndexError, RecursionError, OverflowError):
            raise ValueError('graph_invalid_input') from None
    return call


def _need(ok, code):
    if not ok:
        raise ValueError('graph_' + code)


def _keys(value, expected, code):
    _need(type(value) is dict and set(value) == set(expected), code)


def _text(value, *, limit=5000, nonempty=True):
    _need(type(value) is str and len(value) <= limit and (bool(value) or not nonempty)
        and '\x00' not in value, 'text')
    try: value.encode('utf-8', 'strict')
    except UnicodeError: raise ValueError('graph_text') from None
    return value


def _id(value):
    _text(value, limit=200)
    _need(_IDENTIFIER.fullmatch(value) is not None, 'identifier')
    return value


def _integer(value, low=0, high=MAX_ACTIVATIONS):
    _need(type(value) is int and low <= value <= high, 'integer')


def _time(value):
    _need(type(value) is int and value >= 0 or type(value) is float
        and math.isfinite(value) and value >= 0, 'time')


def _safe(value):
    def visit(item, depth=0):
        _need(depth <= 64, 'json_depth')
        if type(item) is dict:
            for key, child in item.items():
                _text(key, limit=1000, nonempty=False); visit(child, depth + 1)
        elif type(item) is list:
            for child in item: visit(child, depth + 1)
        elif type(item) is str:
            _text(item, limit=MAX_BYTES, nonempty=False)
        else:
            _need(item is None or type(item) in (bool, int)
                or type(item) is float and math.isfinite(item), 'json_value')
    try:
        visit(value)
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode('utf-8')
        _need(len(encoded) <= MAX_BYTES, 'byte_limit')
        return encoded
    except (TypeError, OverflowError, RecursionError, UnicodeError):
        raise ValueError('graph_json') from None


def _hash(value):
    return hashlib.sha256(_safe(value)).hexdigest()


def _schema(value):
    _need(type(value) is dict and value.get('type') == 'object'
        and type(value.get('properties', {})) is dict, 'object_schema')
    def visit(item):
        if type(item) is dict:
            for key, child in item.items():
                _need(key not in {'$id', '$dynamicRef', '$recursiveRef'}, 'schema_reference')
                if key == '$ref':
                    _need(type(child) is str and (child == '#' or child.startswith('#/')), 'schema_reference')
                visit(child)
        elif type(item) is list:
            for child in item: visit(child)
    visit(value)
    try: check_schema(value)
    except Exception: raise ValueError('graph_json_schema') from None


def _validate_output(schema, value):
    _safe(value)
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except Exception: raise ValueError('graph_output_schema') from None


def _field(schema, name):
    _text(name, limit=200)
    root = schema
    def resolve(item):
        seen = set()
        while type(item) is dict and '$ref' in item:
            pointer = item['$ref']; _need(pointer not in seen and pointer.startswith('#/'), 'scalar_reference')
            seen.add(pointer); item = root
            for part in pointer[2:].split('/'):
                part = part.replace('~1', '/').replace('~0', '~')
                _need(type(item) is dict and part in item, 'scalar_reference'); item = item[part]
        return item
    properties = resolve(schema).get('properties', {})
    _need(name in properties, 'reference_field')
    item = resolve(properties[name]); _need(type(item) is dict, 'reference_field_schema')
    types = item.get('type'); types = [types] if type(types) is str else types
    if types is None:
        # JSON Schema permits closed enum/const fields without an explicit
        # type. Infer only their exact finite value kinds; this never narrows
        # a nullable product or makes a mixed-kind field assignable.
        values = [item['const']] if 'const' in item else item.get('enum')
        _need(type(values) is list and bool(values), 'reference_field_type')
        names = {type(None): 'null', bool: 'boolean', str: 'string',
            int: 'integer', float: 'number', dict: 'object', list: 'array'}
        _need(all(type(value) in names for value in values), 'reference_field_type')
        kinds = {names[type(value)] for value in values}
        if 'number' in kinds: kinds.discard('integer')
        types = sorted(kinds)
    _need(type(types) is list and all(type(kind) is str for kind in types), 'reference_field_type')
    nonnull = set(types) - {'null'}
    _need(len(nonnull) == 1 and nonnull <= {'boolean', 'string', 'integer', 'number', 'object', 'array'}, 'reference_field_type')
    kind = next(iter(nonnull))
    return ('date' if kind == 'string' and item.get('format') == 'date' else kind), 'null' in types


def _exclusive(reference):
    return type(reference) is dict and reference.get('schema') == EXCLUSIVE_REFERENCE


def _projection(reference):
    return type(reference) is dict and reference.get('schema') == 'guarded-port-projection-v198'


def _projection_definition(reference, nodes, subscriptions):
    from .guarded_port_projection_v198 import derive_schema, validate_reference_shape
    validate_reference_shape(reference)
    producer_id, consumer_id = reference['origin']['node_id'], reference['consumer_node_id']
    _need(consumer_id in nodes and nodes[consumer_id]['kind'] in {'human','ability','decision'},
        'projection_consumer')
    _need(producer_id in nodes and nodes[producer_id]['kind'] in {'human','ability','wait'},
        'projection_producer')
    producer = nodes[producer_id]
    schema = (producer['execution']['output_schema'] if producer['kind'] == 'ability'
        else subscriptions[producer['subscription_id']]['output_schema'] if producer['kind'] == 'wait'
        else producer['output_schema'])
    return derive_schema(schema, reference)


def _field_unit(schema, name):
    """Strict explicit unit metadata for the new reference family only."""
    def resolve(value, track=False):
        seen, units = set(), []
        while '$ref' in value:
            _need(set(value) <= {'$ref', 'title', 'description', 'x-unit'}, 'reference_unit_siblings')
            if track and 'x-unit' in value: units.append(value['x-unit'])
            pointer = value['$ref']
            _need(type(pointer) is str and pointer.startswith('#/') and pointer not in seen,
                'reference_unit_pointer')
            seen.add(pointer); value = schema
            for part in pointer[2:].split('/'):
                value = value[part.replace('~1', '/').replace('~0', '~')]
        if track and 'x-unit' in value: units.append(value['x-unit'])
        _need(not units or all(type(unit) is str and unit == units[0] for unit in units),
            'reference_unit_conflict')
        return value, units[0] if units else None
    root, _ = resolve(schema)
    _, unit = resolve(root['properties'][name], True)
    return unit


def _exclusive_definition(reference, nodes, subscriptions):
    _keys(reference, {'schema', 'source', 'kind', 'nullable', 'unit', 'consumer_node_id', 'candidates'},
        'exclusive_reference_shape')
    _need(reference['schema'] == EXCLUSIVE_REFERENCE and reference['source'] == 'exclusive_output'
        and type(reference['nullable']) is bool, 'exclusive_reference_family')
    consumer = reference['consumer_node_id']
    _need(type(consumer) is str and consumer in nodes and nodes[consumer]['kind'] in {'human', 'ability'},
        'exclusive_consumer')
    _need(reference['unit'] is None or type(reference['unit']) is str
        and bool(reference['unit'].strip()), 'exclusive_unit')
    candidates = reference['candidates']
    _need(type(candidates) is list and 2 <= len(candidates) <= 16, 'exclusive_candidates')
    seen = set()
    for candidate in candidates:
        _keys(candidate, {'node_id', 'field'}, 'exclusive_candidate')
        node_id = candidate['node_id']
        _need(type(node_id) is str and node_id in nodes and node_id != consumer
            and nodes[node_id]['kind'] in {'human', 'ability', 'wait'}, 'exclusive_producer')
        _text(candidate['field'], limit=200)
        pair = (node_id, candidate['field'])
        _need(pair not in seen, 'exclusive_duplicate_candidate'); seen.add(pair)
        producer = nodes[node_id]
        schema = (producer['execution']['output_schema'] if producer['kind'] == 'ability'
            else subscriptions[producer['subscription_id']]['output_schema'] if producer['kind'] == 'wait'
            else producer['output_schema'])
        _need(candidate['field'] in schema.get('required', [])
            and _field(schema, candidate['field']) == (reference['kind'], reference['nullable'])
            and _field_unit(schema, candidate['field']) == reference['unit'], 'exclusive_output_contract')


def _exclusive_upstream(graph, meta):
    """No future, sibling-before-join, other-flow or loop-carried candidate."""
    for reference in graph['references'].values():
        if not (_exclusive(reference) or _projection(reference)): continue
        candidates = reference['candidates'] if _exclusive(reference) else [reference['origin']]
        for candidate in candidates:
            reached, pending = set(), [candidate['node_id']]
            while pending:
                current = pending.pop()
                if current in reached: continue
                reached.add(current)
                pending.extend(edge['to'] for edge in meta['outgoing'][current] if edge['loop_id'] is None)
            _need(reference['consumer_node_id'] in reached, 'exclusive_not_upstream')


def _predicate_keys(ast):
    if ast['op'] == 'ref': return {ast['key']}
    if ast['op'] == 'value': return set()
    if ast['op'] == 'not': return _predicate_keys(ast['arg'])
    if ast['op'] in {'all', 'any'}:
        return set().union(*(_predicate_keys(arg) for arg in ast['args']))
    return _predicate_keys(ast['left']) | _predicate_keys(ast['right'])


def _guards(value, predicates):
    _keys(value, GUARDS, 'guards')
    for predicate in value.values():
        _need(predicate is None or type(predicate) is str and predicate in predicates, 'guard_predicate')


def _execution(value, references):
    _keys(value, {'capability_id', 'capability_hash', 'agent_role', 'input_bindings', 'arguments',
        'input_schema', 'output_schema', 'adapter', 'release_version', 'max_attempts'}, 'execution')
    _id(value['capability_id']); _text(value['adapter'], limit=100); _text(value['release_version'], limit=40)
    _need(type(value['capability_hash']) is str and _HASH.fullmatch(value['capability_hash']) is not None
        and value['agent_role'] in {'business', 'perception', 'data'}, 'execution_pin')
    _integer(value['max_attempts'], 1, 5)
    _schema(value['input_schema']); _schema(value['output_schema'])
    arguments, bindings = value['arguments'], value['input_bindings']
    _need(type(arguments) is dict and type(bindings) is dict and not set(arguments) & set(bindings), 'input_bindings')
    properties = value['input_schema'].get('properties', {})
    _need(set(arguments) | set(bindings) <= set(properties)
        and set(value['input_schema'].get('required', [])) <= set(arguments) | set(bindings), 'input_fields')
    for key in bindings:
        _need(type(bindings[key]) is str and bindings[key] in references, 'input_reference')
        target_kind, target_nullable = _field(value['input_schema'], key)
        reference = references[bindings[key]]
        if _exclusive(reference) or _projection(reference):
            _need((reference['kind'], reference['nullable']) == (target_kind, target_nullable)
                and _field_unit(value['input_schema'], key) == reference['unit'], 'exclusive_input_contract')
            continue
        _need(reference['kind'] == target_kind
            or reference['kind'] == 'integer' and target_kind == 'number'
            or reference['kind'] == 'date' and target_kind == 'string', 'input_reference_type')
        _need(target_nullable or not reference['nullable'], 'input_reference_nullable')


def _metadata(graph):
    nodes = {node['id']: node for node in graph['nodes']}
    edges = {edge['id']: edge for edge in graph['edges']}
    outgoing = {key: [] for key in nodes}; incoming = {key: [] for key in nodes}
    for edge in graph['edges']:
        outgoing[edge['from']].append(edge)
        if edge['loop_id'] is None: incoming[edge['to']].append(edge)
    indegree = {key: len(value) for key, value in incoming.items()}
    queue = sorted(key for key, count in indegree.items() if not count); order = []
    while queue:
        key = queue.pop(0); order.append(key)
        for edge in outgoing[key]:
            if edge['loop_id'] is not None: continue
            indegree[edge['to']] -= 1
            if indegree[edge['to']] == 0: queue.append(edge['to']); queue.sort()
    _need(len(order) == len(nodes) and order[0] == graph['entry']
        and all(incoming[key] for key in nodes if key != graph['entry'])
        and not incoming[graph['entry']], 'unbounded_or_disconnected_graph')
    scopes = {}; reached_scopes = {graph['entry']: [()]}
    for key in order:
        arrivals = reached_scopes.get(key, []); _need(bool(arrivals), 'unreachable_node')
        node = nodes[key]
        if node['kind'] == 'join':
            fork = nodes[node['fork_id']]
            _need(all(scope and scope[-1][0] == fork['id'] for scope in arrivals), 'join_scope')
            prefixes = {scope[:-1] for scope in arrivals}
            _need(len(prefixes) == 1 and {scope[-1][1] for scope in arrivals} == set(fork['branch_ids']), 'join_branches')
            scope = next(iter(prefixes))
        else:
            _need(len(set(arrivals)) == 1, 'unstructured_branch_merge'); scope = arrivals[0]
        _need(node['kind'] != 'end' or not scope, 'branch_ends_before_join')
        scopes[key] = scope
        for edge in outgoing[key]:
            if edge['loop_id'] is not None: continue
            target_scope = scope + ((key, edge['branch_id']),) if node['kind'] == 'fork' else scope
            reached_scopes.setdefault(edge['to'], []).append(target_scope)
    loops = {}
    reverse = {key: [edge['from'] for edge in incoming[key]] for key in nodes}
    for loop in graph['loops']:
        edge = edges[loop['edge_id']]; start, finish = edge['to'], edge['from']
        descendants = set(); todo = [start]
        while todo:
            key = todo.pop()
            if key in descendants: continue
            descendants.add(key)
            todo.extend(e['to'] for e in outgoing[key] if e['loop_id'] is None)
        ancestors = set(); todo = [finish]
        while todo:
            key = todo.pop()
            if key in ancestors: continue
            ancestors.add(key); todo.extend(reverse[key])
        _need(finish in descendants and scopes[start] == scopes[finish], 'loop_scope')
        loops[loop['id']] = {'definition': loop, 'members': descendants & ancestors}
    regions = [item['members'] for item in loops.values()]
    for left in regions:
        for right in regions:
            _need(not left & right or left <= right or right <= left, 'overlapping_loops')
    return {'nodes': nodes, 'edges': edges, 'outgoing': outgoing, 'scopes': scopes, 'loops': loops,
        'subscriptions': {item['id']: item for item in graph['subscriptions']},
        'reference_schema': {key: {field: value[field] for field in ('kind', 'nullable')}
            for key, value in graph['references'].items() if value['kind'] not in {'object', 'array'}}}


def validate_graph(graph):
    """Validate a closed typed graph and return an independent exact copy."""
    try:
        _safe(graph); _keys(graph, _GRAPH_KEYS, 'shape')
        _need(graph['schema'] == SCHEMA, 'schema'); _id(graph['id']); _id(graph['entry']); _schema(graph['input_schema'])
        _need(type(graph['nodes']) is list and 1 <= len(graph['nodes']) <= MAX_NODES
            and type(graph['edges']) is list and len(graph['edges']) <= MAX_EDGES, 'node_edge_limit')
        _need(type(graph['references']) is dict and len(graph['references']) <= 256
            and type(graph['predicates']) is dict and len(graph['predicates']) <= 128, 'reference_predicate_limit')
        nodes = {}
        extras = {'human': {'actor_id', 'role', 'output_schema'}, 'ability': {'actor_id', 'execution'},
            'decision': {'predicate_id'}, 'fork': {'join_id', 'branch_ids'}, 'join': {'fork_id', 'policy'},
            'wait': {'subscription_id', 'timeout_seconds'}, 'end': set()}
        for node in graph['nodes']:
            _need(type(node) is dict and type(node.get('kind')) is str and node['kind'] in _KINDS, 'node_kind')
            _keys(node, {'id', 'kind', 'source_step_id', 'guards'} | extras[node['kind']], 'node_shape')
            _id(node['id']); _need(node['id'] not in nodes, 'duplicate_node')
            _need(node['source_step_id'] is None or type(node['source_step_id']) is str, 'source_step')
            if node['source_step_id'] is not None: _id(node['source_step_id'])
            nodes[node['id']] = node
            if node['kind'] in {'human', 'ability'}: _id(node['actor_id'])
            if node['kind'] == 'human': _text(node['role'], limit=100); _schema(node['output_schema'])
            if node['kind'] == 'wait': _id(node['subscription_id']); _integer(node['timeout_seconds'], 1, 31536000)
        _need(graph['entry'] in nodes and any(node['kind'] == 'end' for node in nodes.values()), 'entry_or_end')
        _need(type(graph['subscriptions']) is list and len(graph['subscriptions']) <= MAX_NODES, 'subscriptions')
        subscriptions = {}
        for subscription in graph['subscriptions']:
            _keys(subscription, {'id', 'event_type', 'correlation_ref', 'output_schema'}, 'subscription')
            _id(subscription['id']); _text(subscription['event_type'], limit=200); _schema(subscription['output_schema'])
            _need(subscription['id'] not in subscriptions, 'duplicate_subscription')
            subscriptions[subscription['id']] = subscription
        _need(len({item['event_type'] for item in subscriptions.values()}) == len(subscriptions), 'event_type_ambiguous')
        for key, reference in graph['references'].items():
            _id(key)
            if _exclusive(reference):
                _exclusive_definition(reference, nodes, subscriptions)
                continue
            if _projection(reference):
                _projection_definition(reference, nodes, subscriptions)
                continue
            _keys(reference, {'kind', 'nullable', 'source', 'field', 'node_id'}, 'reference')
            _need(reference['source'] in {'input', 'output'} and type(reference['nullable']) is bool, 'reference_source')
            if reference['source'] == 'input':
                _need(reference['node_id'] is None, 'input_node'); output_schema = graph['input_schema']
            else:
                _need(type(reference['node_id']) is str and reference['node_id'] in nodes, 'output_node')
                producer = nodes[reference['node_id']]
                _need(producer['kind'] in {'human', 'ability', 'wait'}, 'output_reference_kind')
                output_schema = (producer['execution']['output_schema'] if producer['kind'] == 'ability'
                    else subscriptions[producer['subscription_id']]['output_schema'] if producer['kind'] == 'wait'
                    else producer['output_schema'])
            _need(_field(output_schema, reference['field']) == (reference['kind'], reference['nullable']), 'reference_type')
        reference_schema = {key: {field: value[field] for field in ('kind', 'nullable')}
            for key, value in graph['references'].items() if value['kind'] not in {'object', 'array'}}
        for key, predicate in graph['predicates'].items():
            _id(key); _need(validate_predicate(predicate, reference_schema) == predicate, 'predicate_changed')
        _guards(graph['guards'], graph['predicates'])
        for predicate in graph['guards'].values():
            if predicate is not None:
                _need(all(graph['references'][key]['source'] == 'input'
                    for key in _predicate_keys(graph['predicates'][predicate])), 'global_guard_scope')
        used_subscriptions = []
        for node in nodes.values():
            _guards(node['guards'], graph['predicates'])
            if node['kind'] == 'ability': _execution(node['execution'], graph['references'])
            if node['kind'] == 'decision': _need(node['predicate_id'] in graph['predicates'], 'decision_predicate')
            consumed = set(node['execution']['input_bindings'].values()) if node['kind'] == 'ability' else set()
            for predicate in node['guards'].values():
                if predicate is not None: consumed.update(_predicate_keys(graph['predicates'][predicate]))
            if node['kind'] == 'decision': consumed.update(_predicate_keys(graph['predicates'][node['predicate_id']]))
            for key in consumed:
                reference = graph['references'][key]
                if _exclusive(reference) or _projection(reference):
                    _need(reference['consumer_node_id'] == node['id'], 'exclusive_wrong_consumer')
            if node['kind'] == 'fork':
                _need(node['join_id'] in nodes and nodes[node['join_id']]['kind'] == 'join'
                    and nodes[node['join_id']]['fork_id'] == node['id'], 'fork_join_pair')
                _need(type(node['branch_ids']) is list and 2 <= len(node['branch_ids']) <= 16
                    and len(set(node['branch_ids'])) == len(node['branch_ids']), 'fork_branches')
                for branch in node['branch_ids']: _id(branch)
            if node['kind'] == 'join':
                _need(node['policy'] == 'all' and node['fork_id'] in nodes
                    and nodes[node['fork_id']]['kind'] == 'fork'
                    and nodes[node['fork_id']]['join_id'] == node['id'], 'join_fork_pair')
            if node['kind'] == 'wait':
                _need(node['subscription_id'] in subscriptions, 'wait_subscription')
                used_subscriptions.append(node['subscription_id'])
        _need(set(used_subscriptions) == set(subscriptions)
            and len(used_subscriptions) == len(set(used_subscriptions)), 'subscription_wait_pair')
        for subscription in subscriptions.values():
            _need(subscription['correlation_ref'] in graph['references']
                and graph['references'][subscription['correlation_ref']]['kind'] not in {'object', 'array'}
                and not _exclusive(graph['references'][subscription['correlation_ref']]), 'correlation_reference')
        _need(type(graph['loops']) is list and len(graph['loops']) <= 16, 'loops')
        loops = {}
        for loop in graph['loops']:
            _keys(loop, {'id', 'edge_id', 'max_iterations'}, 'loop'); _id(loop['id']); _id(loop['edge_id'])
            _integer(loop['max_iterations'], 1, 100); _need(loop['id'] not in loops, 'duplicate_loop')
            loops[loop['id']] = loop
        edge_ids = set(); outgoing = {key: [] for key in nodes}
        for edge in graph['edges']:
            _keys(edge, {'id', 'from', 'to', 'on', 'branch_id', 'loop_id'}, 'edge')
            _id(edge['id']); _need(edge['id'] not in edge_ids, 'duplicate_edge'); edge_ids.add(edge['id'])
            _need(edge['from'] in nodes and edge['to'] in nodes and edge['on'] in {'success', 'true', 'false', 'branch', 'timeout'}, 'edge_target')
            if edge['loop_id'] is not None:
                _need(edge['loop_id'] in loops and loops[edge['loop_id']]['edge_id'] == edge['id'], 'loop_edge')
            _need((edge['branch_id'] is not None) == (edge['on'] == 'branch'), 'branch_edge')
            outgoing[edge['from']].append(edge)
        _need({loop['edge_id'] for loop in loops.values()} <= edge_ids
            and all(any(edge['id'] == loop['edge_id'] and edge['loop_id'] == loop['id']
                for edge in graph['edges']) for loop in loops.values()), 'missing_loop_edge')
        for node in nodes.values():
            edges = outgoing[node['id']]; kind = node['kind']
            expected = ['branch'] * len(node['branch_ids']) if kind == 'fork' else ['true', 'false'] if kind == 'decision' else ['success', 'timeout'] if kind == 'wait' else [] if kind == 'end' else ['success']
            _need(sorted(edge['on'] for edge in edges) == sorted(expected), 'outgoing_edges')
            if kind == 'fork':
                _need({edge['branch_id'] for edge in edges} == set(node['branch_ids'])
                    and all(edge['loop_id'] is None for edge in edges), 'fork_edges')
        _need(type(graph['goals']) is list and 1 <= len(graph['goals']) <= 32, 'goals')
        goals = set()
        for goal in graph['goals']:
            _keys(goal, {'id', 'required_nodes'}, 'goal'); _id(goal['id'])
            _need(goal['id'] not in goals and type(goal['required_nodes']) is list
                and 1 <= len(goal['required_nodes']) <= MAX_NODES
                and len(set(goal['required_nodes'])) == len(goal['required_nodes'])
                and all(key in nodes and nodes[key]['kind'] in {'human', 'ability', 'wait'}
                    for key in goal['required_nodes']), 'goal_receipts')
            goals.add(goal['id'])
        meta = _metadata(graph)
        _exclusive_upstream(graph, meta)
        return copy.deepcopy(graph)
    except ValueError:
        raise
    except (KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        raise ValueError('graph_invalid') from None


def _context(meta):
    return {'loops': {key: 0 for key in meta['loops']}, 'forks': {}}


def _validate_context(context, meta, state):
    _keys(context, {'loops', 'forks'}, 'context')
    _need(type(context['loops']) is dict and set(context['loops']) == set(meta['loops'])
        and type(context['forks']) is dict, 'context_collections')
    for key, counter in context['loops'].items(): _integer(counter, 0, meta['loops'][key]['definition']['max_iterations'])
    for key, scope_id in context['forks'].items():
        _need(key in meta['nodes'] and meta['nodes'][key]['kind'] == 'fork'
            and scope_id in state['fork_scopes'] and state['fork_scopes'][scope_id]['fork_id'] == key, 'context_fork')


def _state(graph, state, now_epoch):
    _safe(state); _keys(state, _STATE_KEYS, 'state_shape'); _time(now_epoch)
    _need(state['schema'] == STATE_SCHEMA and state['graph_hash'] == _hash(graph)
        and state['input_hash'] == _hash(state['inputs']) and state['clock'] <= now_epoch
        and state['state_hash'] == _hash({key: value for key, value in state.items() if key != 'state_hash'}), 'state_changed')
    _integer(state['version'], 1, 1000000000); _integer(state['cancel_epoch'], 0, 1000000000)
    _time(state['clock']); _integer(state['next_activation'], 1, MAX_ACTIVATIONS + 1)
    _integer(state['next_scope'], 1, MAX_ACTIVATIONS + 1)
    _need(state['status'] in {'running', 'waiting', 'blocked'} | _TERMINAL
        and type(state['activations']) is dict and len(state['activations']) <= MAX_ACTIVATIONS
        and type(state['fork_scopes']) is dict, 'state_collections')
    _need(set(state['activations']) == {'a' + str(index) for index in range(1, state['next_activation'])}
        and set(state['fork_scopes']) == {'s' + str(index) for index in range(1, state['next_scope'])}, 'state_identity_sequence')
    meta = _metadata(graph)
    _validate_output(graph['input_schema'], state['inputs'])
    for key, activation in state['activations'].items():
        _keys(activation, _ACTIVATION_KEYS, 'activation_shape')
        _need(key == activation['id'] and activation['node_id'] in meta['nodes']
            and activation['status'] in _ACTIVE | _SETTLED, 'activation')
        _integer(activation['version'], 0, 1000000000); _integer(activation['epoch'], 0, state['cancel_epoch'])
        _time(activation['created_at']); _validate_context(activation['context'], meta, state)
        if activation['scope_id'] is not None:
            _need(activation['scope_id'] in state['fork_scopes'], 'activation_scope')
        for field in ('deadline', 'wait_started_at', 'completed_at'):
            if activation[field] is not None: _time(activation[field])
        _need(activation['created_at'] <= state['clock'] and (activation['completed_at'] is None
            or activation['created_at'] <= activation['completed_at'] <= state['clock']), 'activation_clock')
        node = meta['nodes'][activation['node_id']]
        if activation['status'] == 'completed' and node['kind'] in {'human', 'ability', 'wait'}:
            output_schema = (node['execution']['output_schema'] if node['kind'] == 'ability'
                else meta['subscriptions'][node['subscription_id']]['output_schema'] if node['kind'] == 'wait'
                else node['output_schema'])
            _validate_output(output_schema, activation['output'])
            _check_produced_scalars(graph, meta, node['id'], activation['output'])
        if node['kind'] == 'ability':
            if activation['inputs_snapshot'] is not None: _check_inputs_snapshot(graph, state, meta, activation)
            _need(activation['status'] not in {'ready', 'completed'} or activation['inputs_snapshot'] is not None, 'missing_inputs_snapshot')
        else: _need(activation['inputs_snapshot'] is None, 'unexpected_inputs_snapshot')
        if activation['correlation_hash'] is not None:
            _need(type(activation['correlation_hash']) is str and _HASH.fullmatch(activation['correlation_hash']) is not None, 'correlation_hash')
    for key, scope in state['fork_scopes'].items():
        _keys(scope, {'id', 'fork_id', 'join_id', 'parent_scope_id', 'parent_branch_id',
            'expected', 'arrived', 'join_activation_id', 'closed', 'context'}, 'fork_scope_shape')
        fork = meta['nodes'].get(scope['fork_id'])
        _need(scope['id'] == key and fork is not None and fork['kind'] == 'fork'
            and scope['join_id'] == fork['join_id'] and scope['expected'] == fork['branch_ids']
            and type(scope['arrived']) is dict and set(scope['arrived']) <= set(scope['expected'])
            and type(scope['closed']) is bool, 'fork_scope')
        if scope['parent_scope_id'] is not None:
            parent = state['fork_scopes'].get(scope['parent_scope_id'])
            _need(parent is not None and scope['parent_branch_id'] in parent['expected'], 'parent_fork_scope')
        else: _need(scope['parent_branch_id'] is None, 'parent_branch')
        if scope['join_activation_id'] is not None:
            joined = state['activations'].get(scope['join_activation_id'])
            _need(joined is not None and joined['node_id'] == scope['join_id']
                and joined['scope_id'] == key and joined['branch_id'] is None, 'join_activation')
        else: _need(not scope['arrived'] and not scope['closed'], 'missing_join_activation')
        if scope['closed']:
            _need(set(scope['arrived']) == set(scope['expected'])
                and state['activations'][scope['join_activation_id']]['status'] == 'completed', 'premature_join')
        _validate_context(scope['context'], meta, state)
        for branch, arrival in scope['arrived'].items():
            _keys(arrival, {'activation_id', 'context'}, 'arrival_shape')
            _need(arrival['activation_id'] in state['activations'], 'arrival_activation')
            _validate_context(arrival['context'], meta, state)
            source = state['activations'][arrival['activation_id']]
            nested = state['fork_scopes'].get(source['scope_id']) if meta['nodes'][source['node_id']]['kind'] == 'join' else None
            scope_matches = (source['scope_id'] == key and source['branch_id'] == branch
                or nested is not None and nested['parent_scope_id'] == key and nested['parent_branch_id'] == branch)
            _need(scope_matches
                and source['status'] in {'completed', 'timed_out'} and source['context'] == arrival['context'], 'arrival_identity')
    for field, maximum in [('events', MAX_EVENTS), ('commands', MAX_COMMANDS),
            ('goal_receipts', MAX_NODES), ('predicate_receipts', MAX_RECEIPTS)]:
        _need(type(state[field]) is list and len(state[field]) <= maximum, 'state_list_limit')
    command_ids = set()
    for command in state['commands']:
        _keys(command, {'request_id', 'op', 'actor_id', 'body_hash'}, 'command_shape')
        _id(command['request_id']); _id(command['actor_id'])
        _need(command['request_id'] not in command_ids and command['op'] in {'complete', 'draft', 'cancel'}
            and type(command['body_hash']) is str and _HASH.fullmatch(command['body_hash']) is not None, 'command_identity')
        command_ids.add(command['request_id'])
    event_ids = set()
    for item in state['events']:
        _keys(item, {'event_id', 'event_type', 'correlation', 'output', 'body_hash', 'received_at', 'consumed_by'}, 'event_shape')
        _id(item['event_id']); _time(item['received_at'])
        subscription = next((value for value in meta['subscriptions'].values() if value['event_type'] == item['event_type']), None)
        _need(item['event_id'] not in event_ids and item['received_at'] <= state['clock'] and subscription is not None
            and item['body_hash'] == _hash({key: item[key] for key in ('event_id', 'event_type', 'correlation', 'output')}), 'event_identity')
        event_ids.add(item['event_id']); _validate_output(subscription['output_schema'], item['output'])
        if item['consumed_by'] is not None:
            consumed = state['activations'].get(item['consumed_by'])
            _need(consumed is not None and consumed['status'] == 'completed'
                and meta['nodes'][consumed['node_id']]['kind'] == 'wait'
                and meta['nodes'][consumed['node_id']]['subscription_id'] == subscription['id']
                and consumed['output'] == item['output'] and consumed['correlation_hash'] == _hash(item['correlation'])
                and consumed['created_at'] <= item['received_at'] < consumed['deadline'], 'event_consumption')
    for receipt in state['predicate_receipts']:
        _keys(receipt, {'activation_id', 'phase', 'predicate_id', 'result'}, 'predicate_receipt')
        _need(receipt['predicate_id'] in graph['predicates'] and receipt['phase'] in GUARDS | {'decision'}
            and (receipt['activation_id'] is None or receipt['activation_id'] in state['activations']), 'predicate_receipt_identity')
        result = receipt['result']
        _keys(result, {'schema', 'ast_hash', 'reference_schema_hash', 'context_hash', 'truth',
            'referenced_keys', 'missing_keys', 'result_hash'}, 'predicate_result')
        _need(result['schema'] == 'full-semantic-predicate-result-v1' and result['truth'] in {'true', 'false', 'unknown'}
            and result['ast_hash'] == _hash(graph['predicates'][receipt['predicate_id']])
            and result['reference_schema_hash'] == _hash(meta['reference_schema'])
            and result['result_hash'] == _hash({key: value for key, value in result.items() if key != 'result_hash'})
            and type(result['referenced_keys']) is list and type(result['missing_keys']) is list
            and set(result['missing_keys']) <= set(result['referenced_keys']) <= set(graph['references']), 'predicate_result_identity')
    for field, kind in [('eligible_ability_ids', 'ability'), ('ready_human_ids', 'human')]:
        _need(type(state[field]) is list and len(state[field]) == len(set(state[field])), 'eligible_list')
        for identity in state[field]:
            activation = state['activations'].get(identity)
            _need(activation is not None and activation['status'] == 'ready'
                and meta['nodes'][activation['node_id']]['kind'] == kind, 'eligible_identity')
    if state['status'] == 'succeeded':
        expected = copy.deepcopy(state)
        _need(all(value['status'] in _SETTLED for value in state['activations'].values())
            and _goals(graph, expected, meta) and expected['goal_receipts'] == state['goal_receipts'], 'goal_receipts_changed')
    else: _need(state['goal_receipts'] == [], 'premature_goal_receipts')
    return copy.deepcopy(state), meta


def _observe_v264(observer, value):
    if observer is not None:
        _need(callable(observer), 'control_trace_observer')
        observer(copy.deepcopy(value))


def _spawn(state, meta, node_id, context, scope_id, branch_id, now, *, _observer=None):
    _need(len(state['activations']) < MAX_ACTIVATIONS, 'activation_limit')
    identity = 'a' + str(state['next_activation']); state['next_activation'] += 1
    state['activations'][identity] = {'id': identity, 'node_id': node_id, 'status': 'waiting_guard',
        'version': 0, 'epoch': state['cancel_epoch'], 'created_at': now, 'completed_at': None,
        'output': None, 'draft': None, 'context': copy.deepcopy(context), 'scope_id': scope_id,
        'branch_id': branch_id, 'deadline': None, 'wait_started_at': None, 'correlation_hash': None, 'error': None}
    state['activations'][identity]['inputs_snapshot'] = None
    _observe_v264(_observer, {'kind': 'spawn', 'activation_id': identity,
        'node_id': node_id, 'scope_id': scope_id, 'branch_id': branch_id})
    return identity


def _matches(activation, node_id, context, meta):
    if activation['node_id'] != node_id or activation['status'] != 'completed': return False
    for key, loop in meta['loops'].items():
        if node_id in loop['members'] and activation['context']['loops'][key] != context['loops'][key]: return False
    for fork_id, _ in meta['scopes'][node_id]:
        if activation['context']['forks'].get(fork_id) != context['forks'].get(fork_id): return False
    return True


def _exclusive_resolution(reference, state, meta, context, consumer_node_id):
    """Count actual candidate products, including nullable None, without max/first.

    Each (candidate node, field, completed activation) is a product. Identical
    values or two fields on one activation are never deduplicated into one.
    """
    if consumer_node_id != reference['consumer_node_id']:
        return {'status': 'missing', 'count': 0}
    products = []
    for candidate in reference['candidates']:
        for activation in state['activations'].values():
            if not _matches(activation, candidate['node_id'], context, meta): continue
            output = activation['output']
            if type(output) is dict and candidate['field'] in output:
                products.append((candidate, activation, output[candidate['field']]))
    if len(products) != 1:
        return {'status': 'missing' if not products else 'ambiguous', 'count': len(products)}
    candidate, activation, value = products[0]
    return {'status': 'unique', 'count': 1, 'value': copy.deepcopy(value),
        'producer_activation_id': activation['id'], 'node_id': candidate['node_id'], 'field': candidate['field']}


def _projection_resolution(reference, state, meta, context, consumer_node_id):
    from .guarded_port_projection_v198 import project, ProjectionUnavailable
    if consumer_node_id != reference['consumer_node_id']:
        return {'status':'missing','count':0}
    products = [item for item in state['activations'].values()
        if _matches(item, reference['origin']['node_id'], context, meta)]
    if len(products) != 1:
        return {'status':'missing' if not products else 'ambiguous','count':len(products)}
    producer = products[0]  # Exactly one actual context-bound product, never a preference.
    try: value = project(producer['output'], reference)
    except ProjectionUnavailable as error:
        return {'status':'unavailable','count':1,'reason':error.kind}
    return {'status':'unique','count':1,'value':value,'producer_activation_id':producer['id']}


def _resolution(graph, state, meta, context, *, consumer_node_id=None):
    values = {}; producers = {}
    for key, reference in graph['references'].items():
        if _exclusive(reference) or _projection(reference):
            resolver = _projection_resolution if _projection(reference) else _exclusive_resolution
            selected = resolver(reference, state, meta, context, consumer_node_id)
            if selected['status'] == 'unique':
                values[key] = selected['value']; producers[key] = selected['producer_activation_id']
            continue
        source = state['inputs'] if reference['source'] == 'input' else None
        if source is None:
            candidates = [activation for activation in state['activations'].values()
                if _matches(activation, reference['node_id'], context, meta)]
            if candidates:
                producer = max(candidates, key=lambda item: int(item['id'][1:]))
                source = producer['output']; producers[key] = producer['id']
        if type(source) is dict and reference['field'] in source:
            values[key] = copy.deepcopy(source[reference['field']])
            if reference['source'] == 'input': producers[key] = None
    # Validate supplied referenced scalars even when a particular predicate does
    # not use them. A nullable None remains present and subsequently unknown.
    evaluate_predicate({'op': 'value', 'kind': 'boolean', 'value': True}, meta['reference_schema'],
        {key: value for key, value in values.items() if key in meta['reference_schema']})
    return values, producers


def _resolved(graph, state, meta, context, *, consumer_node_id=None):
    return _resolution(graph, state, meta, context, consumer_node_id=consumer_node_id)[0]


def _predicate(graph, state, meta, predicate_id, context, activation_id, phase):
    consumer = state['activations'][activation_id]['node_id'] if activation_id in state['activations'] else None
    resolved = _resolved(graph, state, meta, context, consumer_node_id=consumer)
    result = evaluate_predicate(graph['predicates'][predicate_id], meta['reference_schema'],
        {key: value for key, value in resolved.items() if key in meta['reference_schema']})
    record = {'activation_id': activation_id, 'phase': phase, 'predicate_id': predicate_id, 'result': result}
    if record not in state['predicate_receipts']:
        _need(len(state['predicate_receipts']) < MAX_RECEIPTS, 'predicate_receipt_limit')
        state['predicate_receipts'].append(record)
    return result['truth']


def _halt(state, status, reason):
    state['status'] = status; state['error'] = reason; state['cancel_epoch'] += 1
    for activation in state['activations'].values():
        if activation['status'] in _ACTIVE:
            activation['status'] = 'cancelled'; activation['version'] += 1; activation['error'] = reason
    state['eligible_ability_ids'] = []; state['ready_human_ids'] = []


def _guarded(graph, state, meta, guards, context, activation_id):
    truths = {phase: _predicate(graph, state, meta, predicate, context, activation_id, phase)
        for phase, predicate in guards.items() if predicate is not None}
    if truths.get('exception') == 'true': _halt(state, 'failed', 'exception_guard'); return False
    if truths.get('stop') == 'true': _halt(state, 'stopped', 'stop_guard'); return False
    if any(truth == 'unknown' for truth in truths.values()): return False
    return all(truths.get(phase, 'true') == 'true' for phase in ('start', 'precondition'))


def _finish(activation, output, now, status='completed'):
    activation['status'] = status; activation['output'] = copy.deepcopy(output)
    activation['completed_at'] = now; activation['version'] += 1; activation['error'] = None


def _merge_context(contexts):
    result = copy.deepcopy(contexts[0])
    for context in contexts[1:]:
        for key, counter in context['loops'].items(): result['loops'][key] = max(result['loops'][key], counter)
        for key, scope_id in context['forks'].items():
            previous = result['forks'].get(key)
            if previous is None or int(scope_id[1:]) > int(previous[1:]): result['forks'][key] = scope_id
    return result


def _arrive(state, meta, source, target, context, scope_id, branch_id, now, *, _observer=None):
    _need(scope_id in state['fork_scopes'], 'join_without_scope')
    scope = state['fork_scopes'][scope_id]
    _need(scope['join_id'] == target and branch_id in scope['expected'] and not scope['closed'], 'join_arrival_scope')
    arrival = {'activation_id': source['id'], 'context': copy.deepcopy(context)}
    _observe_v264(_observer, {'kind': 'arrive', 'source_activation_id': source['id'],
        'scope_id': scope_id, 'branch_id': branch_id, 'node_id': target})
    previous = scope['arrived'].get(branch_id)
    _need(previous is None or previous == arrival, 'duplicate_branch_arrival')
    scope['arrived'][branch_id] = arrival
    if scope['join_activation_id'] is None:
        scope['join_activation_id'] = _spawn(state, meta, target, context, scope_id, None, now, _observer=_observer)
    _observe_v264(_observer, {'kind': 'join', 'activation_id': scope['join_activation_id'],
        'scope_id': scope_id, 'parents': sorted(v['activation_id'] for v in scope['arrived'].values())})
    activation = state['activations'][scope['join_activation_id']]
    activation['context'] = _merge_context([scope['context'], *[item['context'] for item in scope['arrived'].values()]])
    activation['status'] = 'waiting_join'


def _move(graph, state, meta, source, edge, context, scope_id, branch_id, now, *, _observer=None):
    context = copy.deepcopy(context)
    if edge['loop_id'] is not None:
        key = edge['loop_id']; loop = meta['loops'][key]
        if context['loops'][key] >= loop['definition']['max_iterations']:
            _halt(state, 'failed', 'loop_limit:' + key); return
        context['loops'][key] += 1
        for child, info in meta['loops'].items():
            if child != key and info['members'] < loop['members']: context['loops'][child] = 0
    target = edge['to']
    if meta['nodes'][target]['kind'] == 'join':
        _arrive(state, meta, source, target, context, scope_id, branch_id, now, _observer=_observer)
    else:
        _spawned_v264 = _spawn(state, meta, target, context, scope_id, branch_id, now, _observer=_observer)
        _observe_v264(_observer, {'kind': 'link', 'source_activation_id': source['id'],
            'activation_id': _spawned_v264, 'edge_id': edge['id']})


def _follow(graph, state, meta, activation, on, now, *, context=None, scope_id=None, branch_id=None, parent=False, _observer=None):
    edges = [edge for edge in meta['outgoing'][activation['node_id']] if edge['on'] == on]
    _need(len(edges) == 1, 'transition_edge')
    _move(graph, state, meta, activation, edges[0], activation['context'] if context is None else context,
        scope_id if parent else activation['scope_id'], branch_id if parent else activation['branch_id'], now, _observer=_observer)


def _ability_snapshot(graph, state, meta, activation):
    node = meta['nodes'][activation['node_id']]; execution = node['execution']
    values, producers = _resolution(graph, state, meta, activation['context'], consumer_node_id=node['id'])
    result = copy.deepcopy(execution['arguments']); bindings = {}
    for field, key in execution['input_bindings'].items():
        if key in values: result[field] = values[key]
        else: return None
        bindings[field] = {'reference_key': key, 'missing': key not in values,
            'producer_activation_id': producers.get(key),
            'value_hash': _hash(values[key]) if key in values else None}
    _validate_output(execution['input_schema'], result)
    snapshot = {'arguments': result, 'bindings': bindings, 'input_hash': _hash(result)}
    return {**snapshot, 'snapshot_hash': _hash(snapshot)}


def _check_inputs_snapshot(graph, state, meta, activation):
    snapshot = activation['inputs_snapshot']; execution = meta['nodes'][activation['node_id']]['execution']
    _keys(snapshot, {'arguments', 'bindings', 'input_hash', 'snapshot_hash'}, 'inputs_snapshot')
    _need(type(snapshot['arguments']) is dict and type(snapshot['bindings']) is dict
        and set(snapshot['bindings']) == set(execution['input_bindings'])
        and snapshot['input_hash'] == _hash(snapshot['arguments'])
        and snapshot['snapshot_hash'] == _hash({key: value for key, value in snapshot.items() if key != 'snapshot_hash'}), 'inputs_snapshot_hash')
    _validate_output(execution['input_schema'], snapshot['arguments'])
    expected = copy.deepcopy(execution['arguments'])
    for field, key in execution['input_bindings'].items():
        binding = snapshot['bindings'][field]
        _keys(binding, {'reference_key', 'missing', 'producer_activation_id', 'value_hash'}, 'input_binding_snapshot')
        _need(binding['reference_key'] == key and type(binding['missing']) is bool, 'input_binding_reference')
        reference = graph['references'][key]
        if binding['missing']:
            raise ValueError('graph_input_binding_unknown')
        if _exclusive(reference) or _projection(reference):
            resolver = _projection_resolution if _projection(reference) else _exclusive_resolution
            selected = resolver(reference, state, meta, activation['context'], activation['node_id'])
            _need(selected['status'] == 'unique'
                and binding['producer_activation_id'] == selected['producer_activation_id']
                and binding['value_hash'] == _hash(selected['value']), 'exclusive_snapshot_changed')
            expected[field] = copy.deepcopy(selected['value'])
            continue
        if reference['source'] == 'input':
            _need(binding['producer_activation_id'] is None, 'input_binding_producer')
            source = state['inputs']
        else:
            producer = state['activations'].get(binding['producer_activation_id'])
            _need(producer is not None and _matches(producer, reference['node_id'], activation['context'], meta), 'input_binding_producer')
            source = producer['output']
        _need(type(source) is dict and reference['field'] in source
            and binding['value_hash'] == _hash(source[reference['field']]), 'input_binding_value')
        expected[field] = copy.deepcopy(source[reference['field']])
    if any(_exclusive(graph['references'][key]) or _projection(graph['references'][key])
            for key in execution['input_bindings'].values()):
        _need(_hash(expected) == _hash(snapshot['arguments']), 'inputs_snapshot_changed')
    else:
        _need(expected == snapshot['arguments'], 'inputs_snapshot_changed')


def _check_produced_scalars(graph, meta, node_id, output):
    schema = {}; values = {}
    for key, reference in graph['references'].items():
        if reference['source'] == 'output' and reference['node_id'] == node_id:
            if reference['kind'] in {'object', 'array'}: continue
            schema[key] = {field: reference[field] for field in ('kind', 'nullable')}
            if reference['field'] in output: values[key] = output[reference['field']]
    evaluate_predicate({'op': 'value', 'kind': 'boolean', 'value': True}, schema, values)


def _goals(graph, state, meta):
    ends = [item for item in state['activations'].values()
        if meta['nodes'][item['node_id']]['kind'] == 'end' and item['status'] == 'completed']
    if not ends: return False
    context = max(ends, key=lambda item: int(item['id'][1:]))['context']; receipts = []
    for goal in graph['goals']:
        required = []
        for node_id in goal['required_nodes']:
            candidates = [item for item in state['activations'].values() if _matches(item, node_id, context, meta)]
            if not candidates: return False
            required.append(max(candidates, key=lambda item: int(item['id'][1:]))['id'])
        receipt = {'goal_id': goal['id'], 'required_activation_ids': required, 'context_hash': _hash(context)}
        receipts.append({**receipt, 'receipt_hash': _hash(receipt)})
    state['goal_receipts'] = receipts
    return True


def _advance(graph, state, meta, now, *, wait_hook=None, _observer=None):
    state['clock'] = now
    if state['status'] in _TERMINAL: return
    if not _guarded(graph, state, meta, graph['guards'], _context(meta), None):
        if state['status'] not in _TERMINAL:
            state['status'] = 'waiting'; state['eligible_ability_ids'] = []; state['ready_human_ids'] = []
        return
    for _ in range(MAX_ACTIVATIONS * 2):
        changed = False
        for identity in list(state['activations']):
            activation = state['activations'][identity]
            if activation['status'] not in _ACTIVE: continue
            node = meta['nodes'][activation['node_id']]; kind = node['kind']
            if kind == 'join':
                scope = state['fork_scopes'][activation['scope_id']]
                if set(scope['arrived']) != set(scope['expected']): continue
            # Explicit V195 references are dependencies of their pinned human
            # or ability consumer. Missing values wait; ambiguity is a stable,
            # observable block with ZERO dispatch, not a worker retry exception.
            missing = None
            for key, reference in graph['references'].items():
                if not (_exclusive(reference) or _projection(reference)) or reference['consumer_node_id'] != node['id']: continue
                projected = _projection(reference)
                resolver = _projection_resolution if projected else _exclusive_resolution
                selected = resolver(reference, state, meta, activation['context'], node['id'])
                prefix = 'guarded_port_projection_' if projected else 'exclusive_reference_'
                if selected['status'] in {'ambiguous','unavailable'}:
                    activation['status'] = 'waiting_guard'; activation['inputs_snapshot'] = None
                    activation['error'] = prefix + selected.get('reason',selected['status']) + ':' + key
                    state['status'] = 'blocked'; state['error'] = activation['error']
                    state['eligible_ability_ids'] = []; state['ready_human_ids'] = []
                    return
                if selected['status'] == 'missing': missing = prefix + 'missing:' + key
            if missing is not None:
                activation['status'] = 'waiting_guard'; activation['inputs_snapshot'] = None
                activation['error'] = missing
                continue
            if type(activation['error']) is str and activation['error'].startswith(('exclusive_reference_','guarded_port_projection_')):
                activation['error'] = None
            if not _guarded(graph, state, meta, node['guards'], activation['context'], identity):
                if state['status'] in _TERMINAL: return
                activation['status'] = 'waiting_guard'; continue
            if kind in {'human', 'ability'}:
                if kind == 'ability' and activation['inputs_snapshot'] is None:
                    try:
                        activation['inputs_snapshot'] = _ability_snapshot(graph, state, meta, activation)
                    except ValueError:
                        if not any(_projection(graph['references'][key])
                                for key in node['execution']['input_bindings'].values()): raise
                        activation['status'] = 'waiting_guard'
                        activation['error'] = 'guarded_port_projection_input_contract'
                        state['status'] = 'blocked'; state['error'] = activation['error']
                        state['eligible_ability_ids'] = []; state['ready_human_ids'] = []
                        return
                activation['status'] = 'ready' if kind == 'human' or activation['inputs_snapshot'] is not None else 'waiting_guard'
            elif kind == 'decision':
                truth = _predicate(graph, state, meta, node['predicate_id'], activation['context'], identity, 'decision')
                if truth == 'unknown': activation['status'] = 'waiting_guard'; continue
                _finish(activation, {'truth': truth}, now); _follow(graph, state, meta, activation, truth, now, _observer=_observer); changed = True
            elif kind == 'fork':
                _finish(activation, {}, now); scope_id = 's' + str(state['next_scope']); state['next_scope'] += 1
                context = copy.deepcopy(activation['context']); context['forks'][node['id']] = scope_id
                state['fork_scopes'][scope_id] = {'id': scope_id, 'fork_id': node['id'], 'join_id': node['join_id'],
                    'parent_scope_id': activation['scope_id'], 'parent_branch_id': activation['branch_id'],
                    'expected': list(node['branch_ids']), 'arrived': {}, 'join_activation_id': None,
                    'closed': False, 'context': copy.deepcopy(context)}
                for edge in meta['outgoing'][node['id']]:
                    _move(graph, state, meta, activation, edge, context, scope_id, edge['branch_id'], now, _observer=_observer)
                changed = True
            elif kind == 'join':
                _finish(activation, {}, now); scope['closed'] = True
                _follow(graph, state, meta, activation, 'success', now, context=activation['context'],
                    scope_id=scope['parent_scope_id'], branch_id=scope['parent_branch_id'], parent=True, _observer=_observer)
                changed = True
            elif kind == 'wait':
                if wait_hook is not None:
                    # Private, effect-free adapter seam. Public v1 transitions
                    # retain their original protocol and matching behavior.
                    changed = wait_hook(graph, state, meta, activation, node, now) or changed
                else:
                    subscription = meta['subscriptions'][node['subscription_id']]
                    values = _resolved(graph, state, meta, activation['context'])
                    correlation = values.get(subscription['correlation_ref'])
                    if correlation is None: activation['status'] = 'waiting_guard'; continue
                    if activation['deadline'] is None:
                        activation['wait_started_at'] = now; activation['deadline'] = now + node['timeout_seconds']
                        activation['correlation_hash'] = _hash(correlation)
                    activation['status'] = 'waiting_event'
                    matching = [item for item in state['events'] if item['consumed_by'] is None
                        and item['event_type'] == subscription['event_type']
                        and _hash(item['correlation']) == activation['correlation_hash']
                        and activation['created_at'] <= item['received_at'] < activation['deadline']]
                    if matching:
                        item = min(matching, key=lambda value: (value['received_at'], value['event_id']))
                        _validate_output(subscription['output_schema'], item['output'])
                        _check_produced_scalars(graph, meta, node['id'], item['output'])
                        item['consumed_by'] = identity; _finish(activation, item['output'], now)
                        _follow(graph, state, meta, activation, 'success', now, _observer=_observer); changed = True
                    elif now >= activation['deadline']:
                        _finish(activation, None, now, 'timed_out'); activation['error'] = 'wait_timeout'
                        _follow(graph, state, meta, activation, 'timeout', now, _observer=_observer); changed = True
            else:
                _finish(activation, {}, now); changed = True
            if state['status'] in _TERMINAL: return
        if not changed: break
    else: raise ValueError('graph_reducer_limit')
    state['eligible_ability_ids'] = [item['id'] for item in state['activations'].values()
        if item['status'] == 'ready' and meta['nodes'][item['node_id']]['kind'] == 'ability']
    state['ready_human_ids'] = [item['id'] for item in state['activations'].values()
        if item['status'] == 'ready' and meta['nodes'][item['node_id']]['kind'] == 'human']
    if all(item['status'] in _SETTLED for item in state['activations'].values()):
        state['status'] = 'succeeded' if _goals(graph, state, meta) else 'blocked'
        state['error'] = None if state['status'] == 'succeeded' else 'required_receipts_missing'
    else:
        state['status'] = 'running' if state['eligible_ability_ids'] or state['ready_human_ids'] else 'waiting'
        errors = [item['error'] for item in state['activations'].values()
            if item['status'] in _ACTIVE and type(item['error']) is str
            and item['error'].startswith('exclusive_reference_')]
        if errors: state['error'] = errors[0]
        elif type(state['error']) is str and state['error'].startswith('exclusive_reference_'):
            state['error'] = None


def _return(before, state):
    if before is not None:
        previous = {key: value for key, value in before.items() if key not in {'version', 'state_hash'}}
        current = {key: value for key, value in state.items() if key not in {'version', 'state_hash'}}
        state['version'] = before['version'] + int(previous != current)
    body = {key: value for key, value in state.items() if key != 'state_hash'}
    return {**body, 'state_hash': _hash(body)}


@_public
def new_run(graph, inputs, now_epoch, *, _observer=None):
    graph = validate_graph(graph); _time(now_epoch); _validate_output(graph['input_schema'], inputs)
    meta = _metadata(graph)
    state = {'schema': STATE_SCHEMA, 'graph_hash': _hash(graph), 'input_hash': _hash(inputs),
        'inputs': copy.deepcopy(inputs), 'status': 'waiting', 'version': 1, 'cancel_epoch': 0,
        'clock': now_epoch, 'next_activation': 1, 'next_scope': 1, 'activations': {}, 'fork_scopes': {},
        'events': [], 'commands': [], 'goal_receipts': [], 'predicate_receipts': [],
        'eligible_ability_ids': [], 'ready_human_ids': [], 'error': None}
    _resolved(graph, state, meta, _context(meta))
    _spawn(state, meta, graph['entry'], _context(meta), None, None, now_epoch, _observer=_observer)
    _advance(graph, state, meta, now_epoch, _observer=_observer)
    return _return(None, state)


@_public
def advance(graph, state, now_epoch, *, _observer=None):
    graph = validate_graph(graph); working, meta = _state(graph, state, now_epoch)
    _advance(graph, working, meta, now_epoch, _observer=_observer)
    return _return(state, working)


def _command(state, op, request_id, actor_id, body):
    _id(request_id); _id(actor_id); fingerprint = _hash(body)
    matches = [item for item in state['commands'] if item['request_id'] == request_id]
    if matches:
        _need(len(matches) == 1 and matches[0] == {'request_id': request_id, 'op': op,
            'actor_id': actor_id, 'body_hash': fingerprint}, 'request_conflict')
        return True
    _need(len(state['commands']) < MAX_COMMANDS, 'command_limit')
    state['commands'].append({'request_id': request_id, 'op': op, 'actor_id': actor_id, 'body_hash': fingerprint})
    return False


@_public
def complete(graph, state, activation_id, output, request_id, actor_id, expected_version, cancel_epoch, now_epoch, *, _observer=None):
    graph = validate_graph(graph); working, meta = _state(graph, state, now_epoch)
    body = {'activation_id': activation_id, 'output': output, 'actor_id': actor_id,
        'expected_version': expected_version, 'cancel_epoch': cancel_epoch}
    if _command(working, 'complete', request_id, actor_id, body):
        _advance(graph, working, meta, now_epoch, _observer=_observer); return _return(state, working)
    _advance(graph, working, meta, now_epoch, _observer=_observer)
    _need(activation_id in working['activations'], 'activation_not_found')
    activation = working['activations'][activation_id]; node = meta['nodes'][activation['node_id']]
    _integer(expected_version, 0, 1000000000); _integer(cancel_epoch, 0, 1000000000)
    _need(working['status'] not in _TERMINAL and activation['status'] == 'ready'
        and activation_id in working['eligible_ability_ids'] + working['ready_human_ids']
        and activation['version'] == expected_version and activation['epoch'] == cancel_epoch == working['cancel_epoch'], 'activation_stale')
    _need(node['kind'] in {'human', 'ability'} and node['actor_id'] == actor_id, 'actor')
    schema = node['output_schema'] if node['kind'] == 'human' else node['execution']['output_schema']
    _validate_output(schema, output); _check_produced_scalars(graph, meta, node['id'], output)
    _finish(activation, output, now_epoch); activation['draft'] = None
    _observe_v264(_observer, {'kind': 'complete', 'activation_id': activation_id})
    _follow(graph, working, meta, activation, 'success', now_epoch, _observer=_observer)
    _advance(graph, working, meta, now_epoch, _observer=_observer)
    return _return(state, working)


@_public
def save_draft(graph, state, activation_id, draft, request_id, actor_id, expected_version, cancel_epoch, now_epoch):
    graph = validate_graph(graph); working, meta = _state(graph, state, now_epoch); _safe(draft)
    _need(type(draft) in {str, dict} and len(_safe(draft)) <= 16000, 'draft')
    body = {'activation_id': activation_id, 'draft': draft, 'actor_id': actor_id,
        'expected_version': expected_version, 'cancel_epoch': cancel_epoch}
    if _command(working, 'draft', request_id, actor_id, body): return _return(state, working)
    _need(activation_id in working['activations'], 'activation_not_found')
    activation = working['activations'][activation_id]; node = meta['nodes'][activation['node_id']]
    _integer(expected_version, 0, 1000000000); _integer(cancel_epoch, 0, 1000000000)
    _need(node['kind'] == 'human' and node['actor_id'] == actor_id, 'actor')
    _need(working['status'] not in _TERMINAL and activation['status'] in {'ready', 'waiting_guard'}
        and activation['version'] == expected_version and activation['epoch'] == cancel_epoch == working['cancel_epoch'], 'activation_stale')
    activation['draft'] = copy.deepcopy(draft); activation['version'] += 1; working['clock'] = now_epoch
    return _return(state, working)


@_public
def cancel(graph, state, request_id, actor_id, expected_version, cancel_epoch, now_epoch, reason=''):
    graph = validate_graph(graph); working, meta = _state(graph, state, now_epoch)
    _text(reason, limit=1000, nonempty=False)
    body = {'actor_id': actor_id, 'expected_version': expected_version, 'cancel_epoch': cancel_epoch, 'reason': reason}
    if _command(working, 'cancel', request_id, actor_id, body): return _return(state, working)
    _integer(expected_version, 1, 1000000000); _integer(cancel_epoch, 0, 1000000000)
    _need(working['version'] == expected_version and working['cancel_epoch'] == cancel_epoch
        and working['status'] not in _TERMINAL, 'cancel_stale')
    working['clock'] = now_epoch; _halt(working, 'cancelled', reason or 'cancelled')
    return _return(state, working)


def _buffer_event(graph, working, event_id, event_type, correlation, output, now_epoch,
        *, received_at, explicit_received_at):
    """Validate and buffer one receipt without advancing any activation."""
    _time(received_at); _need(received_at <= now_epoch, 'event_received_at')
    _id(event_id); _text(event_type, limit=200); _safe(correlation)
    subscription = next((item for item in graph['subscriptions'] if item['event_type'] == event_type), None)
    _need(subscription is not None, 'event_type')
    reference = graph['references'][subscription['correlation_ref']]
    evaluate_predicate({'op': 'value', 'kind': 'boolean', 'value': True},
        {'correlation': {field: reference[field] for field in ('kind', 'nullable')}}, {'correlation': correlation})
    _need(correlation is not None, 'event_correlation')
    _validate_output(subscription['output_schema'], output)
    body = {'event_id': event_id, 'event_type': event_type, 'correlation': correlation, 'output': output}
    fingerprint = _hash(body); previous = [item for item in working['events'] if item['event_id'] == event_id]
    if previous:
        _need(len(previous) == 1 and previous[0]['body_hash'] == fingerprint, 'event_conflict')
        if explicit_received_at:
            _need(previous[0]['received_at'] == received_at, 'event_received_at_changed')
    else:
        _need(len(working['events']) < MAX_EVENTS, 'event_limit')
        working['events'].append({**copy.deepcopy(body), 'body_hash': fingerprint,
            'received_at': received_at, 'consumed_by': None})


@_public
def event(graph, state, event_id, event_type, correlation, output, now_epoch, *, received_at=None, _observer=None):
    graph = validate_graph(graph); working, meta = _state(graph, state, now_epoch)
    explicit_received_at = received_at is not None
    received_at = now_epoch if received_at is None else received_at
    _buffer_event(graph, working, event_id, event_type, correlation, output, now_epoch,
        received_at=received_at, explicit_received_at=explicit_received_at)
    _advance(graph, working, meta, now_epoch, _observer=_observer)
    return _return(state, working)


@_public
def events(graph, state, events, now_epoch, *, _observer=None):
    """Buffer a complete caller-authenticated receipt batch, then advance once.

    The caller supplies the durable acceptance cut and authenticates each source
    and original timestamp. This pure API grants no producer or runtime authority.
    Rows and persisted receipts retain their exact values; a failed batch never
    changes the input state. An empty batch has the same behavior as advance.
    """
    graph = validate_graph(graph); working, meta = _state(graph, state, now_epoch)
    _need(type(events) is list and len(events) <= MAX_EVENTS, 'event_batch_limit')
    _safe(events)
    for row in events:
        _keys(row, {'event_id', 'event_type', 'correlation', 'output', 'received_at'}, 'event_batch_row')
        _buffer_event(graph, working, row['event_id'], row['event_type'], row['correlation'],
            row['output'], now_epoch, received_at=row['received_at'], explicit_received_at=True)
    _advance(graph, working, meta, now_epoch, _observer=_observer)
    return _return(state, working)


@_public
def ability_inputs(graph, state, activation_id):
    graph = validate_graph(graph); working, meta = _state(graph, state, state['clock'])
    _need(activation_id in working['eligible_ability_ids'], 'ability_not_ready')
    return copy.deepcopy(working['activations'][activation_id]['inputs_snapshot']['arguments'])


@_public
def pending(graph, state):
    graph = validate_graph(graph); working, meta = _state(graph, state, state['clock'])
    return [{'activation_id': item['id'], 'node_id': item['node_id'], 'kind': meta['nodes'][item['node_id']]['kind'],
        'status': item['status'], 'version': item['version'], 'epoch': item['epoch'],
        'deadline': item['deadline'], 'draft': copy.deepcopy(item['draft'])}
        for item in working['activations'].values() if item['status'] in _ACTIVE]


@_public
def check_run(graph, state):
    """Cold-check the closed persisted state; hashes never replace DB authority."""
    graph = validate_graph(graph)
    return _state(graph, state, state['clock'])[0]
