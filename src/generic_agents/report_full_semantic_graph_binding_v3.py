"""Pure, lossless source-to-graph bindings; never database/source authority.

The caller cold-verifies issued designs, current formal proofs, schema origins,
capabilities, real role memberships and explicit shared adoption consent. This
module preserves every source owner and checks typed graph coverage, rather
than interpreting prose or treating citations/self-consistent hashes as consent.
Unresolved owners block only their explicitly recorded business scopes.
"""
import copy

from .contracts import digest
from .report_full_semantic_configuration_v1 import check_design
from .report_full_semantic_source_manifest_v1 import _owner_inputs
from . import report_full_semantic_schema_v1 as source


SCHEMA = 'report-full-semantic-graph-binding-v3'
_OBJECT_BASE_SCHEMA = 'report-object-graph-base-binding-v171'
_OBJECT_PROFILE = 'objects-v171'
MODE = 'workspace_graph_design'
OWNER_KEYS = {'owner_path', 'value_hash', 'ref_hashes', 'resolution', 'reason', 'clauses', 'scope_businesses'}
FIELDS = {
    'domain': ('value',), 'question': ('value',), 'global_claim': ('kind', 'statement'),
    'business': ('trigger', 'output'),
    'step': ('name', 'agent', 'role', 'purpose', 'output', 'next_index', 'branch_condition', 'else_index'),
    'participant': ('agent', 'responsibility'),
    'interaction': ('from_participant_index', 'to_participant_index', 'purpose', 'condition', 'step_indices'),
    'condition': ('kind', 'statement', 'step_indices'),
    'coordination': ('kind', 'step_indices', 'start_rule', 'wait_rule', 'join_rule'),
    'object': ('name',), 'field': ('label', 'kind', 'required', 'unit'),
    'relationship': ('name', 'description', 'source_type', 'target_type', 'cardinality'),
}
KEYS = {'schema', 'handoff_id', 'design_hash', 'business_name', 'candidate_index', 'business_hash',
    'source_design', 'source_business', 'source_manifest', 'source_proof', 'proof_id', 'manifest_hash',
    'proof_hash', 'graph', 'owner_bindings', 'capability_pins', 'role_bindings', 'blocked_businesses',
    'semantic_mode', 'source_semantics_status', 'coverage_hash', 'binding_hash'}


def _need(ok, code):
    if not ok:
        raise ValueError('full_semantic_graph_binding_' + code)


def _keys(value, expected, code):
    _need(type(value) is dict and set(value) == set(expected), code)


def _same(left, right):
    return source._json(left) == source._json(right)


def _text(value, *, empty=False, maximum=4000):
    _need(type(value) is str and len(value) <= maximum and (empty or bool(value.strip())), 'text')


def required_owner_fields(design):
    """Fixed typed owner/field inventory for editor templates and cold coverage."""
    return _required_owner_fields(design, profile='legacy')


def _profile_design(design, profile):
    _need(profile in {'legacy', _OBJECT_PROFILE}, 'profile')
    schema = ('report-object-semantic-design-v169' if profile == _OBJECT_PROFILE
        else 'report-full-semantic-design-v1')
    _need(type(design) is dict and design.get('schema') == schema, 'design_family')
    return check_design(design)


def _inventory(design, profile):
    if profile == _OBJECT_PROFILE:
        from .report_object_source_manifest_v170 import owner_inputs
        return owner_inputs(design)
    return list(_owner_inputs(design['result']))


def _fields(profile):
    if profile == _OBJECT_PROFILE:
        return {**FIELDS, 'business_identity': ('status', 'field_labels', 'explanation'),
            'platform_identity_confirmation': ('object_name', 'acknowledge_platform_identity')}
    return FIELDS


def _required_owner_fields(design, *, profile):
    design = _profile_design(design, profile)
    fields = _fields(profile)
    return [{'owner_path': path, 'kind': kind, 'value_hash': digest(value),
        'source_fields': list(fields[kind])}
        for path, kind, _, value, _, _ in _inventory(design, profile)]


def _graph_context(graph, business, binding):
    from .full_semantic_graph_v1 import validate_graph
    checked = validate_graph(graph)
    _need(_same(checked, graph), 'graph_changed')
    nodes = {node['id']: node for node in graph['nodes']}
    edges = {edge['id']: edge for edge in graph['edges']}
    steps = binding['step_ids']
    step_nodes = {}
    for node in nodes.values():
        identity = node['source_step_id']
        if identity is None:
            continue
        _need(identity in steps and identity not in step_nodes, 'source_step_mapping')
        index = steps.index(identity); original = business['steps'][index]
        _need(node['kind'] in {'human', 'ability'}, 'source_step_node_kind')
        if original['agent'] == '人工确认':
            _need(node['kind'] == 'human' and node['role'] == original['role'], 'source_step_role')
        else:
            roles = {'业务智能体': 'business', '多模感知智能体': 'perception', '数据管理智能体': 'data'}
            _need(node['kind'] == 'ability'
                and node['execution']['agent_role'] == roles[original['agent']], 'source_step_agent')
        step_nodes[identity] = node
    _need(set(step_nodes) == set(steps), 'missing_source_steps')
    return {'graph': graph, 'nodes': nodes, 'edges': edges, 'step_nodes': step_nodes, 'step_ids': steps,
        'subscriptions': {item['id']: item for item in graph['subscriptions']},
        'goals': {item['id']: item for item in graph['goals']}}


def _schema_target(ref, result, *, origin_schema='report-full-semantic-schema-origin-v1'):
    _keys(ref, {'kind', 'id', 'field', 'field_type', 'origin'}, 'schema_target_shape')
    origin = ref['origin']
    _keys(origin, {'schema', 'adoption_id', 'plan_hash', 'kind', 'name', 'owner_path', 'owner_value_hash'}, 'schema_origin_shape')
    _need(origin['schema'] == origin_schema
        and origin['kind'] in {'object', 'relationship'} and origin['name'] == ref['id'], 'schema_origin_kind')
    source._uuid(origin['adoption_id']); source._hash(origin['plan_hash']); source._hash(origin['owner_value_hash'])
    collection = 'objects' if origin['kind'] == 'object' else 'relationships'
    found = [(index, item) for index, item in enumerate(result[collection]) if item['name'] == ref['id']]
    _need(len(found) == 1, 'schema_origin_target')
    index, item = found[0]
    _need(origin['owner_path'] == '/' + collection + '/' + str(index)
        and origin['owner_value_hash'] == digest(item), 'schema_origin_source')
    if ref['field'] is None:
        _need(ref['field_type'] is None, 'schema_object_field_type')
    else:
        _need(collection == 'objects', 'relationship_field')
        fields = [field for field in item['fields'] if field['label'] == ref['field']]
        _need(len(fields) == 1 and ref['field_type'] == fields[0]['kind'], 'schema_field_type')


def _target(ref, ctx, result, roles, *, origin_schema='report-full-semantic-schema-origin-v1'):
    _need(type(ref) is dict and type(ref.get('kind')) is str, 'target_shape')
    if ref['kind'] == 'schema_origin':
        _schema_target(ref, result, origin_schema=origin_schema)
        return
    _keys(ref, {'kind', 'id'}, 'target_shape'); _text(ref['id'], maximum=1000)
    kind, identity = ref['kind'], ref['id']
    if kind in {'node', 'fork', 'join', 'wait'}:
        node = ctx['nodes'].get(identity)
        _need(node is not None and (kind == 'node' or node['kind'] == kind), 'target_node')
    elif kind == 'edge': _need(identity in ctx['edges'], 'target_edge')
    elif kind == 'predicate': _need(identity in ctx['graph']['predicates'], 'target_predicate')
    elif kind == 'value_ref': _need(identity in ctx['graph']['references'], 'target_value_ref')
    elif kind == 'subscription': _need(identity in ctx['subscriptions'], 'target_subscription')
    elif kind == 'goal': _need(identity in ctx['goals'], 'target_goal')
    elif kind == 'role_binding': _need(identity in roles, 'target_role')
    else: _need(False, 'target_kind')


def _refs(clause, kinds, *, required=True):
    refs = clause['targets']
    _need((bool(refs) or not required) and all(ref['kind'] in kinds for ref in refs), 'clause_target_kind')
    return refs


def _ids(refs, kind):
    return [ref['id'] for ref in refs if ref['kind'] == kind]


def _step_targets(clause, indices, ctx):
    refs = _refs(clause, {'node'}, required=bool(indices))
    expected = [ctx['step_nodes'][ctx['step_ids'][index]]['id'] for index in indices]
    _need(_ids(refs, 'node') == expected, 'source_step_scope')


def _frontier(ctx, start):
    """First source steps, traversing only explicit synthetic control nodes."""
    seen = set(); source_steps = set(); todo = [start]
    while todo:
        identity = todo.pop()
        if identity in seen: continue
        seen.add(identity); node = ctx['nodes'][identity]
        if node['source_step_id'] is not None:
            source_steps.add(node['source_step_id']); continue
        todo.extend(edge['to'] for edge in ctx['edges'].values() if edge['from'] == identity)
    return source_steps


def _before_source(ctx, start, target):
    todo = [start]; seen = set()
    while todo:
        identity = todo.pop()
        if identity in seen: continue
        seen.add(identity)
        node = ctx['nodes'][identity]
        if identity != start and node['source_step_id'] is not None: continue
        if identity == target: return True
        todo.extend(edge['to'] for edge in ctx['edges'].values() if edge['from'] == identity)
    return False


def _guard_predicates(ctx, slot, indices):
    if not indices:
        values = [ctx['graph']['guards'][slot]]
    else:
        values = [ctx['step_nodes'][ctx['step_ids'][index]]['guards'][slot] for index in indices]
    _need(all(value is not None for value in values), 'condition_scope_not_applied')
    return set(values)


def _used_predicates(ctx):
    result = set(ctx['graph']['guards'].values()) - {None}
    for node in ctx['nodes'].values():
        result.update(set(node['guards'].values()) - {None})
        if node['kind'] == 'decision': result.add(node['predicate_id'])
    return result


def _pins(ctx, capability_pins, role_bindings):
    _need(type(capability_pins) is dict and type(role_bindings) is dict, 'pins_shape')
    for role, actor in role_bindings.items():
        _text(role, maximum=100)
        _keys(actor, {'user_id', 'username', 'membership_role'}, 'role_pin_shape')
        source._uuid(actor['user_id']); _text(actor['username'], maximum=100)
        _need(actor['membership_role'] in {'admin', 'operator'}, 'role_permission')
    ability_nodes = {node['id']: node for node in ctx['nodes'].values() if node['kind'] == 'ability'}
    _need(set(capability_pins) == set(ability_nodes), 'capability_pin_inventory')
    actors = {actor['user_id'] for actor in role_bindings.values()}
    for node in ctx['nodes'].values():
        if node['kind'] == 'human':
            _need(node['role'] in role_bindings and node['actor_id'] == role_bindings[node['role']]['user_id'], 'human_role_pin')
        elif node['kind'] == 'ability':
            _need(node['actor_id'] in actors and _same(node['execution'], capability_pins[node['id']]), 'capability_pin_changed')


def _parallel(ctx, rows, business_index):
    groups = []
    for row, original, kind in rows:
        if kind != 'coordination' or not row['owner_path'].startswith('/businesses/' + str(business_index) + '/'):
            continue
        if row['resolution'] != 'typed' or original['kind'] not in {'parallel', 'unspecified'}: continue
        clauses = {item['source_field']: item for item in row['clauses']}
        forks = _ids(clauses['kind']['targets'], 'fork')
        if original['kind'] == 'unspecified' and not forks: continue
        joins = _ids(clauses['join_rule']['targets'], 'join')
        _need(len(forks) == len(joins) == 1 and ctx['nodes'][forks[0]]['join_id'] == joins[0], 'parallel_pair')
        expected = {ctx['step_ids'][index] for index in original['step_indices']}
        actual = set()
        for edge in ctx['edges'].values():
            if edge['from'] == forks[0]: actual.update(_frontier(ctx, edge['to']))
        _need(actual == expected, 'parallel_source_scope')
        groups.append({'steps': expected, 'fork': forks[0], 'join': joins[0], 'clauses': clauses, 'reason': row['reason']})
    return groups


def _source_edge(clause, expected_index, step, node, ctx, groups, *, otherwise=False):
    refs = _refs(clause, {'edge'})
    _need(len(refs) == 1, 'source_edge_count'); edge = ctx['edges'][refs[0]['id']]
    _need(_before_source(ctx, node['id'], edge['from']), 'source_edge_from')
    on = 'false' if otherwise else 'true' if step['branch_condition'].strip() else 'success'
    _need(edge['on'] == on, 'source_edge_outcome')
    frontier = _frontier(ctx, edge['to'])
    if expected_index is None:
        if frontier:
            # A source branch may end at its explicitly approved join; a join's
            # onward edge must itself be recorded in the source join clause.
            allowed = [group for group in groups if node['source_step_id'] in group['steps']
                and edge['to'] == group['join'] and group['reason'].strip()
                and any(ctx['edges'][ref['id']]['from'] == group['join']
                    for ref in group['clauses']['join_rule']['targets'] if ref['kind'] == 'edge')]
            _need(bool(allowed), 'source_end_changed')
        return edge
    expected = ctx['step_ids'][expected_index]
    _need(expected in frontier, 'source_next_changed')
    extras = frontier - {expected}
    _need(not extras or any(frontier <= group['steps'] and _before_source(ctx, edge['to'], group['fork'])
        for group in groups), 'unapproved_parallel_targets')
    return edge


def _near_source(ctx, identity, source_nodes):
    frontier = _frontier(ctx, identity)
    return any(_before_source(ctx, node['id'], identity) or node['source_step_id'] in frontier
        for node in source_nodes)


def _sequence(clause, indices, ctx):
    refs = _refs(clause, {'edge'}, required=len(indices) > 1)
    _need(len(refs) == max(0, len(indices) - 1), 'sequence_edge_count')
    for ref, first, second in zip(refs, indices, indices[1:]):
        edge = ctx['edges'][ref['id']]
        previous = ctx['step_nodes'][ctx['step_ids'][first]]
        _need(_before_source(ctx, previous['id'], edge['from'])
            and _frontier(ctx, edge['to']) == {ctx['step_ids'][second]}, 'sequence_source_order')


def _typed_owner(row, original, kind, ctx, result, business_index, participant_rows, groups):
    clauses = {item['source_field']: item for item in row['clauses']}
    local = row['owner_path'].startswith('/businesses/' + str(business_index) + '/') or row['owner_path'] == '/businesses/' + str(business_index)
    if kind in {'domain', 'question'}:
        _refs(clauses['value'], {'goal', 'predicate', 'node', 'wait', 'value_ref'})
    elif kind == 'global_claim':
        for clause in clauses.values(): _refs(clause, {'predicate', 'goal', 'schema_origin', 'role_binding', 'node', 'wait', 'value_ref'})
    elif kind == 'object':
        refs = _refs(clauses['name'], {'schema_origin'})
        _need(all(ref['id'] == original['name'] and ref['field'] is None and ref['origin']['kind'] == 'object' for ref in refs), 'object_origin')
    elif kind in {'business_identity', 'platform_identity_confirmation'}:
        # These are declarations about one complete object, not a business key
        # or a field value. Its independently issued schema origin retains the
        # whole identity/acknowledgement; no clause can borrow another object.
        name = (result['objects'][int(row['owner_path'].split('/')[2])]['name']
            if kind == 'business_identity' else original['object_name'])
        for clause in clauses.values():
            refs = _refs(clause, {'schema_origin'})
            _need(all(ref['id'] == name and ref['field'] is None and ref['field_type'] is None
                and ref['origin']['kind'] == 'object' for ref in refs), 'identity_object_origin')
    elif kind == 'field':
        oi = int(row['owner_path'].split('/')[2])
        name = result['objects'][oi]['name']
        for clause in clauses.values():
            refs = _refs(clause, {'schema_origin', 'value_ref'})
            origins = [ref for ref in refs if ref['kind'] == 'schema_origin']
            _need(bool(origins) and all(ref['id'] == name and ref['field'] == original['label']
                and ref['field_type'] == original['kind'] for ref in origins), 'field_origin')
            for ref in refs:
                if ref['kind'] != 'value_ref': continue
                target = ctx['graph']['references'][ref['id']]
                expected_kind = {'文本': 'string', '整数': 'integer', '数值': 'number', '是否': 'boolean', '日期': 'date'}[original['kind']]
                _need(target['kind'] == expected_kind and target['nullable'] is (not original['required']), 'field_reference_type')
    elif kind == 'relationship':
        for field, clause in clauses.items():
            refs = _refs(clause, {'schema_origin'})
            expected = original[field] if field in {'source_type', 'target_type'} else original['name']
            expected_kind = 'object' if field in {'source_type', 'target_type'} else 'relationship'
            _need(all(ref['id'] == expected and ref['field'] is None and ref['origin']['kind'] == expected_kind for ref in refs), 'relationship_origin')
    elif kind == 'business':
        _need(local, 'cross_business_graph')
        for ref in _refs(clauses['trigger'], {'predicate', 'subscription', 'node'}):
            if ref['kind'] == 'node': _need(ref['id'] == ctx['graph']['entry'], 'trigger_entry')
            elif ref['kind'] == 'predicate':
                _need(ref['id'] in {ctx['graph']['guards']['start'], ctx['nodes'][ctx['graph']['entry']]['guards']['start']}, 'trigger_not_applied')
            else:
                waits = [node for node in ctx['nodes'].values() if node['kind'] == 'wait' and node['subscription_id'] == ref['id']]
                _need(ctx['nodes'][ctx['graph']['entry']]['source_step_id'] is None
                    and any(_before_source(ctx, ctx['graph']['entry'], node['id']) for node in waits), 'trigger_subscription_scope')
        _refs(clauses['output'], {'goal'})
    elif kind == 'participant':
        _need(local, 'cross_business_graph')
        _refs(clauses['agent'], {'role_binding'})
        _refs(clauses['responsibility'], {'node', 'goal', 'role_binding', 'subscription'})
    elif kind == 'step':
        _need(local, 'cross_business_graph'); index = int(row['owner_path'].split('/')[-1])
        node = ctx['step_nodes'][ctx['step_ids'][index]]
        for field in ('name', 'agent', 'role', 'purpose', 'output'):
            refs = _refs(clauses[field], {'node', 'role_binding'}, required=bool(original[field]))
            _need(all(ref['id'] == node['id'] if ref['kind'] == 'node' else
                node['kind'] == 'human' and ref['id'] == node['role'] for ref in refs), 'step_target')
        next_edge = _source_edge(clauses['next_index'], original['next_index'], original, node, ctx, groups)
        if original['branch_condition'].strip():
            predicates = _ids(_refs(clauses['branch_condition'], {'predicate'}), 'predicate')
            else_edge = _source_edge(clauses['else_index'], original['else_index'], original, node, ctx, groups, otherwise=True)
            decision = ctx['nodes'][next_edge['from']]
            _need(len(predicates) == 1 and next_edge['from'] == else_edge['from']
                and decision['kind'] == 'decision' and decision['predicate_id'] == predicates[0], 'branch_predicate_not_applied')
        else:
            _need(not clauses['branch_condition']['targets'] and not clauses['else_index']['targets'], 'invented_source_branch')
    elif kind == 'condition':
        _need(local, 'cross_business_graph')
        slot = {'trigger': 'start', 'precondition': 'precondition', 'stop': 'stop', 'exception': 'exception'}[original['kind']]
        applied = _guard_predicates(ctx, slot, original['step_indices'])
        for field in ('kind', 'statement'):
            refs = _refs(clauses[field], {'predicate'})
            _need(set(_ids(refs, 'predicate')) == applied, 'condition_not_applied')
        _step_targets(clauses['step_indices'], original['step_indices'], ctx)
    elif kind == 'interaction':
        _need(local, 'cross_business_graph')
        for field, part_field in (('from_participant_index', 'from_participant_index'), ('to_participant_index', 'to_participant_index')):
            target = participant_rows[original[part_field]]
            expected = {ref['id'] for clause in target['clauses'] if clause['source_field'] == 'agent' for ref in clause['targets']}
            refs = _refs(clauses[field], {'role_binding'})
            _need(set(_ids(refs, 'role_binding')) == expected and bool(expected), 'interaction_participant')
        nodes = [ctx['step_nodes'][ctx['step_ids'][index]] for index in original['step_indices']]
        related = list(nodes)
        for ref in _refs(clauses['purpose'], {'edge', 'wait', 'subscription'}):
            if ref['kind'] == 'edge': controls = [ctx['nodes'][ctx['edges'][ref['id']]['from']]]
            elif ref['kind'] == 'wait': controls = [ctx['nodes'][ref['id']]]
            else: controls = [node for node in ctx['nodes'].values() if node['kind'] == 'wait' and node['subscription_id'] == ref['id']]
            _need(not nodes or any(_near_source(ctx, control['id'], nodes) for control in controls), 'interaction_control_scope')
            related.extend(controls)
        predicates = _ids(_refs(clauses['condition'], {'predicate'}, required=bool(original['condition'].strip())), 'predicate')
        applied = set(ctx['graph']['guards'].values()) - {None}
        for control in related:
            applied.update(set(control['guards'].values()) - {None})
            if control['kind'] == 'decision': applied.add(control['predicate_id'])
        _need(set(predicates) <= applied, 'interaction_condition_not_applied')
        _step_targets(clauses['step_indices'], original['step_indices'], ctx)
    elif kind == 'coordination':
        _need(local, 'cross_business_graph'); _text(row['reason'])
        _step_targets(clauses['step_indices'], original['step_indices'], ctx)
        kinds = {'parallel': {'fork'}, 'sequence': {'edge'}, 'branch': {'node'},
            'unspecified': {'node', 'edge', 'fork'}}[original['kind']]
        _refs(clauses['kind'], kinds, required=original['kind'] != 'sequence' or len(original['step_indices']) > 1)
        if original['kind'] == 'sequence': _sequence(clauses['kind'], original['step_indices'], ctx)
        start = _ids(_refs(clauses['start_rule'], {'predicate'}, required=bool(original['start_rule'].strip())), 'predicate')
        nodes = [ctx['step_nodes'][ctx['step_ids'][index]] for index in original['step_indices']]
        for ref in clauses['kind']['targets']:
            if ref['kind'] == 'fork': nodes.append(ctx['nodes'][ref['id']])
            if ref['kind'] == 'node': _need(ctx['nodes'][ref['id']]['kind'] == 'decision', 'coordination_node_kind')
        applied = {ctx['graph']['guards']['start']} | {node['guards']['start'] for node in nodes}
        _need(set(start) <= applied, 'coordination_start_not_applied')
        wait_refs = _refs(clauses['wait_rule'], {'wait', 'join', 'edge'}, required=bool(original['wait_rule'].strip()))
        join_refs = _refs(clauses['join_rule'], {'join', 'edge', 'goal'}, required=bool(original['join_rule'].strip()))
        for ref in wait_refs + join_refs:
            if ref['kind'] == 'goal': continue
            identity = ctx['edges'][ref['id']]['from'] if ref['kind'] == 'edge' else ref['id']
            _need(not nodes or _near_source(ctx, identity, nodes), 'coordination_control_scope')
        if original['kind'] == 'parallel' or any(ref['kind'] == 'fork' for ref in clauses['kind']['targets']):
            group = next(group for group in groups if group['clauses'] == clauses)
            _need(group['join'] in _ids(clauses['wait_rule']['targets'], 'join'), 'parallel_wait_join')


def build_binding(design, manifest, proof, business_name, graph, owner_bindings, *, proof_id, capability_pins, role_bindings):
    """Legacy-only public builder; a new design never acquires an old binding."""
    return _build_binding(design, manifest, proof, business_name, graph, owner_bindings,
        proof_id=proof_id, capability_pins=capability_pins, role_bindings=role_bindings, profile='legacy')


def _build_binding(design, manifest, proof, business_name, graph, owner_bindings, *,
        proof_id, capability_pins, role_bindings, profile):
    try:
        for value in (design, manifest, proof, graph, owner_bindings, capability_pins, role_bindings): source._json(value)
        checked = _profile_design(design, profile); _need(_same(checked, design), 'source_design_changed'); design = checked
        source._manifest(design, manifest); source._proof(design, manifest, proof); source._uuid(proof_id)
        _text(business_name, maximum=100)
        found = [(index, item) for index, item in enumerate(design['result']['businesses']) if item['name'] == business_name]
        _need(len(found) == 1, 'business'); business_index, business = found[0]
        binding = design['business_bindings'][business_index]
        ctx = _graph_context(graph, business, binding); _pins(ctx, capability_pins, role_bindings)
        inventory = _inventory(design, profile); fields = _fields(profile)
        origin_schema = ('report-object-schema-origin-v170' if profile == _OBJECT_PROFILE
            else 'report-full-semantic-schema-origin-v1')
        _need(type(owner_bindings) is list and len(owner_bindings) == len(inventory), 'owner_inventory')
        names = [item['name'] for item in design['result']['businesses']]; rows = []; blocked = set()
        for row, owner, (path, kind, _, original, _, _) in zip(owner_bindings, manifest['owners'], inventory):
            _keys(row, OWNER_KEYS, 'owner_shape')
            _need(row['owner_path'] == path and row['value_hash'] == owner['value_hash']
                and _same(row['ref_hashes'], owner['ref_hashes']), 'owner_source')
            _need(row['resolution'] in {'typed', 'informational', 'unresolved'}, 'resolution')
            _text(row['reason'], empty=row['resolution'] == 'typed')
            scope = row['scope_businesses']
            _need(type(scope) is list and bool(scope) and all(type(name) is str for name in scope)
                and scope == [name for name in names if name in scope], 'owner_scope')
            if path.startswith('/businesses/'):
                expected_name = names[int(path.split('/')[2])]
                _need(scope == [expected_name], 'local_owner_scope')
            if row['resolution'] == 'informational': _need(kind in {'domain', 'question'}, 'constraint_informational')
            if row['resolution'] == 'unresolved': blocked.update(scope)
            clauses = row['clauses']
            _need(type(clauses) is list and len(clauses) == len(fields[kind]), 'clause_inventory')
            for clause, field in zip(clauses, fields[kind]):
                _keys(clause, {'source_field', 'targets'}, 'clause_shape')
                _need(clause['source_field'] == field and type(clause['targets']) is list
                    and len(clause['targets']) <= 80, 'source_field')
                seen = set()
                for target in clause['targets']:
                    _target(target, ctx, design['result'], role_bindings, origin_schema=origin_schema)
                    signature = source._json(target); _need(signature not in seen, 'duplicate_target'); seen.add(signature)
                if row['resolution'] == 'informational': _need(not clause['targets'], 'informational_targets')
            if row['resolution'] == 'typed': _need(business_name in scope, 'typed_graph_outside_scope')
            rows.append((row, original, kind))
        groups = _parallel(ctx, rows, business_index)
        participants = [row for row, _, kind in rows if kind == 'participant'
            and row['owner_path'].startswith('/businesses/' + str(business_index) + '/')]
        for row, original, kind in rows:
            if row['resolution'] == 'typed':
                _typed_owner(row, original, kind, ctx, design['result'], business_index, participants, groups)
        claimed = [ref for row, _, _ in rows if row['resolution'] == 'typed'
            for clause in row['clauses'] for ref in clause['targets']]
        for kind in ('fork', 'join', 'wait'):
            _need({node['id'] for node in ctx['nodes'].values() if node['kind'] == kind}
                <= {ref['id'] for ref in claimed if ref['kind'] == kind}, 'unclaimed_control_node')
        _need(set(graph['predicates']) <= {ref['id'] for ref in claimed if ref['kind'] == 'predicate'}, 'unclaimed_predicate')
        _need({ref['id'] for ref in claimed if ref['kind'] == 'predicate'} <= _used_predicates(ctx), 'predicate_not_applied')
        _need(set(ctx['goals']) <= {ref['id'] for ref in claimed if ref['kind'] == 'goal'}, 'unclaimed_goal')
        references = {ref['id'] for ref in claimed if ref['kind'] == 'value_ref'}
        _need(set(graph['references']) <= references, 'unclaimed_value_reference')
        for row, _, kind in rows:
            if row['resolution'] == 'typed' and kind != 'field' and any(ref['kind'] == 'value_ref'
                    for clause in row['clauses'] for ref in clause['targets']):
                _text(row['reason'])
        for node in ctx['nodes'].values():
            if node['source_step_id'] is None and node['kind'] in {'human', 'ability'}:
                _need(any(row['reason'].strip() and any(ref['kind'] == 'node' and ref['id'] == node['id']
                    for clause in row['clauses'] for ref in clause['targets']) for row, _, _ in rows if row['resolution'] == 'typed'),
                    'unclaimed_workspace_action')
        coverage = [copy.deepcopy(row) for row, _, _ in rows]
        value = {'schema': _OBJECT_BASE_SCHEMA if profile == _OBJECT_PROFILE else SCHEMA,
            'handoff_id': design['handoff_id'], 'design_hash': design['design_hash'],
            'business_name': business_name, 'candidate_index': business_index, 'business_hash': digest(business),
            'source_design': copy.deepcopy(design), 'source_business': copy.deepcopy(business),
            'source_manifest': copy.deepcopy(manifest), 'source_proof': copy.deepcopy(proof), 'proof_id': proof_id,
            'manifest_hash': manifest['manifest_hash'], 'proof_hash': proof['proof_hash'], 'graph': copy.deepcopy(graph),
            'owner_bindings': copy.deepcopy(owner_bindings), 'capability_pins': copy.deepcopy(capability_pins),
            'role_bindings': copy.deepcopy(role_bindings), 'blocked_businesses': [name for name in names if name in blocked],
            'semantic_mode': MODE, 'source_semantics_status': 'not_certified', 'coverage_hash': digest(coverage)}
        source._json(value)
        return {**value, 'binding_hash': digest(value)}
    except (KeyError, TypeError, AttributeError, StopIteration, IndexError, RecursionError, UnicodeError, OverflowError):
        raise ValueError('full_semantic_graph_binding_invalid_input') from None


def check_binding(value, design=None):
    if type(value) is dict and value.get('schema') == 'report-object-graph-binding-v171':
        from .report_object_graph_binding_v171 import check_binding as check_objects
        return check_objects(value, design)
    if type(value) is dict and value.get('schema') == 'report-full-semantic-graph-binding-v4':
        from .report_full_semantic_graph_binding_v4 import check_binding as check_v4
        return check_v4(value, design)
    _need(type(value) is dict and value.get('schema') == SCHEMA, 'binding_family')
    _keys(value, KEYS, 'binding_shape'); source._json(value)
    expected = build_binding(value['source_design'] if design is None else design,
        value['source_manifest'], value['source_proof'], value['business_name'], value['graph'], value['owner_bindings'],
        proof_id=value['proof_id'], capability_pins=value['capability_pins'], role_bindings=value['role_bindings'])
    _need(_same(value, expected), 'binding_changed')
    return expected
