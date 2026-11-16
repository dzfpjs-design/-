"""Synthetic pure graph bindings. Root executes; these do not grant authority.

Real source/design/manifest/proof builders consume invented inline text only.
No DB, document files, browser, account, provider, clock or private data is used.
"""
import copy

import pytest

from generic_agents.contracts import digest
from generic_agents.report_full_semantic_graph_binding_v3 import (
    build_binding, check_binding, required_owner_fields,
)
from tests.test_report_full_semantic_schema_v1_pure import inputs, uid


def ref(kind, identity):
    return {'kind': kind, 'id': identity}


def guards(**values):
    return {**dict.fromkeys(('start', 'precondition', 'stop', 'exception')), **values}


def edge(identity, start, end, on='success', branch=None):
    return {'id': identity, 'from': start, 'to': end, 'on': on,
        'branch_id': branch, 'loop_id': None}


def schema(properties=None):
    return {'type': 'object', 'properties': properties or {},
        'required': list(properties or {}), 'additionalProperties': False}


def row(values, path):
    return next(item for item in values['owner_bindings'] if item['owner_path'] == path)


def clause(values, path, field):
    return next(item for item in row(values, path)['clauses'] if item['source_field'] == field)


def owner_rows(design, manifest, business_name, targets_by_path, *, resolutions=None, scopes=None, reasons=None):
    """Build synthetic test rows from explicit targets and the actual manifest.

    This does not infer semantic intent, create origins, grant approval or read
    a ledger. Integration tests supply their issued schema origins and all
    clause targets explicitly, then use the real preview/adoption path.
    """
    resolutions = resolutions or {}; scopes = scopes or {}; reasons = reasons or {}
    inventory = required_owner_fields(design)
    assert set(targets_by_path) == {item['owner_path'] for item in inventory}
    assert len(inventory) == len(manifest['owners'])
    rows = []
    for spec, owner in zip(inventory, manifest['owners']):
        path = spec['owner_path']; targets = targets_by_path[path]
        assert path == owner['path'] and set(targets) == set(spec['source_fields'])
        rows.append({'owner_path': path, 'value_hash': owner['value_hash'],
            'ref_hashes': copy.deepcopy(owner['ref_hashes']), 'resolution': resolutions.get(path, 'typed'),
            'reason': reasons.get(path, '合成测试明确选择的工作区设计；不构成来源事实或账户授权'),
            'scope_businesses': copy.deepcopy(scopes.get(path, [business_name])),
            'clauses': [{'source_field': field, 'targets': copy.deepcopy(targets[field])} for field in spec['source_fields']]})
    return rows


def fixture(*, branch=False, wait=False, ability=False, extra_business=False, change=None):
    def source_change(result):
        business = result['businesses'][0]
        for index, step in enumerate(business['steps']):
            step['next_index'] = None
            step['role'] = ('职责甲', '职责乙')[index]
        business['coordination'][0]['step_indices'] = [0, 1]
        if branch:
            business['steps'][0].update(next_index=1, branch_condition='显式分支条件', else_index=None)
            business['coordination'][0]['kind'] = 'branch'
        if ability:
            business['steps'][1].update(agent='业务智能体', role='')
        if extra_business:
            other = copy.deepcopy(business)
            other['name'] = '另一个独立业务'
            result['businesses'].append(other)
        if change: change(result)
    design, manifest, proof, _, _ = inputs(source_change)
    business = design['result']['businesses'][0]
    source_ids = design['business_bindings'][0]['step_ids']
    roles = {role: {'user_id': uid(900 + i), 'username': '合成成员' + str(i), 'membership_role': 'operator'}
        for i, role in enumerate(('职责甲', '职责乙'))}
    nodes = [
        {'id': 'a', 'kind': 'human', 'source_step_id': source_ids[0], 'guards': guards(start='p_start'),
            'actor_id': roles['职责甲']['user_id'], 'role': '职责甲', 'output_schema': schema({'ok': {'type': 'boolean'}})},
        {'id': 'b', 'kind': 'human', 'source_step_id': source_ids[1], 'guards': guards(stop='p_stop'),
            'actor_id': roles['职责乙']['user_id'], 'role': '职责乙', 'output_schema': schema({'ok': {'type': 'boolean'}})},
        {'id': 'e', 'kind': 'end', 'source_step_id': None, 'guards': guards()},
    ]
    pins = {}
    if ability:
        execution = {'capability_id': 'capability.synthetic', 'capability_hash': 'c' * 64,
            'agent_role': 'business', 'input_bindings': {}, 'arguments': {},
            'input_schema': schema(), 'output_schema': schema({'ok': {'type': 'boolean'}}),
            'adapter': 'synthetic-only', 'release_version': '1', 'max_attempts': 2}
        nodes[1] = {key: value for key, value in nodes[1].items() if key not in {'role', 'output_schema'}}
        nodes[1].update(kind='ability', execution=execution)
        pins['b'] = copy.deepcopy(execution)
    predicates = {'p_start': {'op': 'value', 'kind': 'boolean', 'value': True},
        'p_stop': {'op': 'value', 'kind': 'boolean', 'value': False},
        'p_coord': {'op': 'value', 'kind': 'boolean', 'value': True}}
    references = {}
    subscriptions = []
    graph_guards = guards()
    if branch:
        nodes.append({'id': 'd', 'kind': 'decision', 'source_step_id': None,
            'guards': guards(), 'predicate_id': 'p_branch'})
        predicates['p_branch'] = {'op': 'value', 'kind': 'boolean', 'value': True}
        edges = [edge('a_d', 'a', 'd'), edge('d_true', 'd', 'b', 'true'),
            edge('d_false', 'd', 'e', 'false'), edge('b_e', 'b', 'e')]
        entry = 'a'; graph_guards['start'] = 'p_coord'; required_nodes = ['a']
    else:
        nodes.extend([
            {'id': 'f', 'kind': 'fork', 'source_step_id': None, 'guards': guards(start='p_coord'),
                'join_id': 'j', 'branch_ids': ['A', 'B']},
            {'id': 'j', 'kind': 'join', 'source_step_id': None, 'guards': guards(), 'fork_id': 'f', 'policy': 'all'},
        ])
        edges = [edge('f_a', 'f', 'a', 'branch', 'A'), edge('f_b', 'f', 'b', 'branch', 'B'),
            edge('a_j', 'a', 'j'), edge('b_j', 'b', 'j'), edge('j_e', 'j', 'e')]
        entry = 'f'; required_nodes = ['a', 'b']
    input_schema = schema()
    if wait:
        assert not branch
        nodes.append({'id': 'w', 'kind': 'wait', 'source_step_id': None, 'guards': guards(),
            'subscription_id': 'sub', 'timeout_seconds': 60})
        edges = [item for item in edges if item['id'] != 'a_j'] + [edge('a_w', 'a', 'w'),
            edge('w_j', 'w', 'j'), edge('w_timeout', 'w', 'j', 'timeout')]
        input_schema = schema({'case': {'type': 'string'}})
        references['case'] = {'kind': 'string', 'nullable': False, 'source': 'input', 'field': 'case', 'node_id': None}
        subscriptions = [{'id': 'sub', 'event_type': 'synthetic.case_ready', 'correlation_ref': 'case', 'output_schema': schema()}]
    graph = {'schema': 'full-semantic-graph-v1', 'id': 'synthetic_graph', 'entry': entry,
        'input_schema': input_schema, 'references': references, 'predicates': predicates,
        'guards': graph_guards, 'nodes': nodes, 'edges': edges, 'loops': [], 'subscriptions': subscriptions,
        'goals': [{'id': 'done', 'required_nodes': required_nodes}]}

    def origin(collection, index, field=None):
        item = design['result'][collection][index]
        source_origin = {'schema': 'report-full-semantic-schema-origin-v1', 'adoption_id': uid(950),
            'plan_hash': 'd' * 64, 'kind': 'object' if collection == 'objects' else 'relationship',
            'name': item['name'], 'owner_path': '/' + collection + '/' + str(index), 'owner_value_hash': digest(item)}
        return {'kind': 'schema_origin', 'id': item['name'], 'field': field,
            'field_type': None if field is None else next(value['kind'] for value in item['fields'] if value['label'] == field),
            'origin': source_origin}

    inventory = required_owner_fields(design)
    owners = []
    for spec, owner in zip(inventory, manifest['owners']):
        path = spec['owner_path']; kind = spec['kind']
        targets = {field: [] for field in spec['source_fields']}
        resolution = 'typed'; reason = '用户明确采用的合成工作区设计🙂'
        scope = [business['name']]
        if path.startswith('/businesses/1'):
            resolution = 'unresolved'; scope = ['另一个独立业务']
        elif kind in {'domain', 'question'}:
            resolution = 'informational'; reason = '保留上下文或待核问题，未将其当作运行约束'
        elif kind == 'global_claim':
            targets = {field: [ref('goal', 'done')] for field in targets}
        elif kind == 'object':
            targets['name'] = [origin('objects', int(path.split('/')[2]))]
        elif kind == 'field':
            oi, fi = int(path.split('/')[2]), int(path.split('/')[4])
            target = origin('objects', oi, design['result']['objects'][oi]['fields'][fi]['label'])
            targets = {field: [copy.deepcopy(target)] for field in targets}
            if wait and oi == fi == 0:
                targets['kind'].append(ref('value_ref', 'case'))
        elif kind == 'relationship':
            targets = {field: [origin('relationships', 0)] for field in targets}
            targets['source_type'] = [origin('objects', 0)]; targets['target_type'] = [origin('objects', 1)]
        elif kind == 'business':
            targets.update(trigger=[ref('node', entry)], output=[ref('goal', 'done')])
        elif kind == 'step':
            index = int(path.split('/')[-1]); identity = chr(ord('a') + index)
            for field in ('name', 'agent', 'role', 'purpose', 'output'): targets[field] = [ref('node', identity)]
            if branch and index == 0:
                targets.update(next_index=[ref('edge', 'd_true')], else_index=[ref('edge', 'd_false')],
                    branch_condition=[ref('predicate', 'p_branch')])
            else:
                target = 'b_e' if branch else 'a_w' if wait and index == 0 else identity + '_j'
                targets['next_index'] = [ref('edge', target)]
        elif kind == 'participant':
            index = int(path.split('/')[-1])
            targets.update(agent=[ref('role_binding', ('职责甲', '职责乙')[index])],
                responsibility=[ref('node', ('a', 'b')[index])])
        elif kind == 'interaction':
            purpose = ref('wait', 'w') if wait else ref('edge', 'a_d' if branch else 'a_j')
            targets.update(from_participant_index=[ref('role_binding', '职责甲')],
                to_participant_index=[ref('role_binding', '职责乙')], purpose=[purpose],
                step_indices=[ref('node', 'a')])
        elif kind == 'condition':
            index = int(path.split('/')[-1]); predicate = 'p_stop' if index == 0 else 'p_start'
            targets.update(kind=[ref('predicate', predicate)], statement=[ref('predicate', predicate)],
                step_indices=[ref('node', 'b' if index == 0 else 'a')])
        elif kind == 'coordination':
            targets.update(kind=[ref('node', 'd')] if branch else [ref('fork', 'f')],
                step_indices=[ref('node', 'a'), ref('node', 'b')], start_rule=[ref('predicate', 'p_coord')],
                wait_rule=[ref('edge', 'a_d')] if branch else [ref('join', 'j')],
                join_rule=[ref('goal', 'done')] if branch else [ref('join', 'j')])
            if wait: targets['wait_rule'].append(ref('wait', 'w'))
        owners.append({'owner_path': path, 'value_hash': owner['value_hash'], 'ref_hashes': copy.deepcopy(owner['ref_hashes']),
            'resolution': resolution, 'reason': reason, 'clauses': [{'source_field': field, 'targets': targets[field]}
                for field in spec['source_fields']], 'scope_businesses': scope})
    return {'design': design, 'manifest': manifest, 'proof': proof, 'business_name': business['name'], 'graph': graph,
        'owner_bindings': owners, 'proof_id': uid(980), 'capability_pins': pins, 'role_bindings': roles}


def build(values):
    return build_binding(**values)


def three_step_fixture():
    """Actual source IDs: Human || builtin -> all join -> decision -> Human.

    The original parallel branch ends are kept as None. Their explicitly
    adopted join_rule includes the onward edge before the extra source step.
    """
    def change(result):
        business = result['businesses'][0]
        third = copy.deepcopy(business['steps'][0])
        third.update(name='汇合后人工复核', role='职责甲', next_index=None)
        business['steps'].append(third)
    values = fixture(ability=True, change=change)
    graph = values['graph']; ids = values['design']['business_bindings'][0]['step_ids']
    graph['nodes'].extend([
        {'id': 'd', 'kind': 'decision', 'source_step_id': None, 'guards': guards(), 'predicate_id': 'p_after'},
        {'id': 'c', 'kind': 'human', 'source_step_id': ids[2], 'guards': guards(),
            'actor_id': values['role_bindings']['职责甲']['user_id'], 'role': '职责甲',
            'output_schema': schema({'ok': {'type': 'boolean'}})},
    ])
    graph['predicates']['p_after'] = {'op': 'value', 'kind': 'boolean', 'value': True}
    graph['edges'] = [item for item in graph['edges'] if item['id'] != 'j_e'] + [
        edge('j_d', 'j', 'd'), edge('d_c', 'd', 'c', 'true'), edge('d_e', 'd', 'e', 'false'), edge('c_e', 'c', 'e')]
    targets = {item['owner_path']: {part['source_field']: copy.deepcopy(part['targets']) for part in item['clauses']}
        for item in values['owner_bindings']}
    targets['/businesses/0/steps/2'] = {field: [ref('node', 'c')] for field in ('name', 'agent', 'role', 'purpose', 'output')}
    targets['/businesses/0/steps/2'].update(next_index=[ref('edge', 'c_e')], branch_condition=[], else_index=[])
    targets['/businesses/0/coordination/0']['join_rule'].append(ref('edge', 'j_d'))
    targets['/global_claims/0']['statement'].append(ref('predicate', 'p_after'))
    resolutions = {item['owner_path']: item['resolution'] for item in values['owner_bindings']}
    values['owner_bindings'] = owner_rows(values['design'], values['manifest'], values['business_name'], targets,
        resolutions=resolutions)
    return values


def sequence_fixture():
    def change(result):
        business = result['businesses'][0]
        business['steps'][0]['next_index'] = 1
        business['coordination'][0]['kind'] = 'sequence'
    values = fixture(change=change); graph = values['graph']
    graph['nodes'] = [item for item in graph['nodes'] if item['id'] not in {'f', 'j'}]
    graph.update(entry='a', edges=[edge('a_b', 'a', 'b'), edge('b_e', 'b', 'e')], guards=guards(start='p_coord'))
    clause(values, '/businesses/0', 'trigger')['targets'] = [ref('node', 'a')]
    clause(values, '/businesses/0/steps/0', 'next_index')['targets'] = [ref('edge', 'a_b')]
    clause(values, '/businesses/0/steps/1', 'next_index')['targets'] = [ref('edge', 'b_e')]
    clause(values, '/businesses/0/interactions/0', 'purpose')['targets'] = [ref('edge', 'a_b')]
    clause(values, '/businesses/0/coordination/0', 'kind')['targets'] = [ref('edge', 'a_b')]
    clause(values, '/businesses/0/coordination/0', 'wait_rule')['targets'] = [ref('edge', 'b_e')]
    clause(values, '/businesses/0/coordination/0', 'join_rule')['targets'] = [ref('goal', 'done')]
    return values


def test_complex_parallel_preserves_all_source_owners_unicode_and_closed_hashes():
    values = fixture(); before = copy.deepcopy(values); value = build(values)
    assert value['schema'] == 'report-full-semantic-graph-binding-v3'
    assert value['semantic_mode'] == 'workspace_graph_design'
    assert value['source_semantics_status'] == 'not_certified'
    assert value['blocked_businesses'] == []
    assert value['source_design'] == values['design']
    assert value['source_business'] == values['design']['result']['businesses'][0]
    assert value['source_manifest'] == values['manifest'] and value['source_proof'] == values['proof']
    assert [item['owner_path'] for item in value['owner_bindings']] == [item['path'] for item in values['manifest']['owners']]
    assert value['source_business']['coordination'][0]['kind'] == 'parallel'
    assert len(value['source_design']['result']['global_claims']) == 2
    assert value['source_design']['result']['questions'] == ['原具体待核问题🙂\n']
    assert check_binding(value) == value == check_binding(value, values['design']) and values == before
    assert value['binding_hash'] == digest({key: item for key, item in value.items() if key != 'binding_hash'})
    value['source_business']['steps'].clear(); value['graph']['nodes'].clear()
    assert values == before and build(values)['source_business']['steps']


def test_real_typed_branch_has_matching_predicate_and_explicit_true_false_edges():
    values = fixture(branch=True); value = build(values)
    assert clause(values, '/businesses/0/steps/0', 'next_index')['targets'] == [ref('edge', 'd_true')]
    assert clause(values, '/businesses/0/steps/0', 'else_index')['targets'] == [ref('edge', 'd_false')]
    assert value['source_business']['steps'][0]['branch_condition'] == '显式分支条件'
    assert check_binding(value) == value


def test_explicit_sequence_is_checked_against_source_order_not_an_unrelated_edge():
    values = sequence_fixture(); value = build(values)
    assert check_binding(value) == value
    clause(values, '/businesses/0/coordination/0', 'kind')['targets'] = [ref('edge', 'b_e')]
    with pytest.raises(ValueError, match='sequence_source_order'): build(values)


def test_source_unspecified_coordination_can_receive_an_explicit_workspace_parallel_design():
    values = fixture(change=lambda result: result['businesses'][0]['coordination'][0].update(kind='unspecified'))
    value = build(values)
    assert value['source_business']['coordination'][0]['kind'] == 'unspecified'
    assert check_binding(value) == value


def test_source_branch_coordination_cannot_be_bound_to_an_ordinary_sequence_edge():
    values = fixture(branch=True)
    clause(values, '/businesses/0/coordination/0', 'kind')['targets'] = [ref('edge', 'a_d')]
    with pytest.raises(ValueError, match='clause_target_kind'): build(values)


def test_wait_subscription_and_field_type_origin_are_separately_bound():
    values = fixture(wait=True); value = build(values)
    assert value['graph']['subscriptions'][0]['correlation_ref'] == 'case'
    assert ref('wait', 'w') in clause(values, '/businesses/0/coordination/0', 'wait_rule')['targets']
    assert ref('value_ref', 'case') in clause(values, '/objects/0/fields/0', 'kind')['targets']
    assert check_binding(value) == value


def test_ability_pins_and_actual_role_actor_are_exact_not_prose_agent_names():
    values = fixture(ability=True); value = build(values)
    assert value['capability_pins']['b']['agent_role'] == 'business'
    assert check_binding(value) == value


def test_three_source_steps_parallel_builtin_join_decision_is_not_single_manual_projection():
    values = three_step_fixture(); value = build(values)
    assert len(value['source_business']['steps']) == 3
    assert {item['kind'] for item in value['graph']['nodes']} >= {'human', 'ability', 'fork', 'join', 'decision'}
    assert value['source_business']['steps'][0]['next_index'] is None
    assert ref('edge', 'j_d') in clause(values, '/businesses/0/coordination/0', 'join_rule')['targets']
    assert check_binding(value) == value
    clause(values, '/businesses/0/coordination/0', 'join_rule')['targets'].pop()
    with pytest.raises(ValueError, match='source_end_changed'): build(values)


def test_unresolved_owners_block_only_explicit_business_scopes():
    values = fixture(extra_business=True); value = build(values)
    assert value['blocked_businesses'] == ['另一个独立业务']
    assert value['business_name'] not in value['blocked_businesses']
    assert value['source_design']['result']['businesses'][1]['name'] == '另一个独立业务'
    claim = row(values, '/global_claims/1'); claim.update(resolution='unresolved', reason='该业务约束待核')
    claim['clauses'] = [{'source_field': item['source_field'], 'targets': []} for item in claim['clauses']]
    assert build(values)['blocked_businesses'] == ['合成业务', '另一个独立业务']


@pytest.mark.parametrize('path', ['/global_claims/0', '/businesses/0/steps/0', '/businesses/0/participants/0',
    '/businesses/0/interactions/0', '/businesses/0/conditions/0', '/businesses/0/coordination/0',
    '/objects/0/fields/0', '/relationships/0'])
def test_constraint_owners_cannot_become_informational(path):
    values = fixture(); row(values, path)['resolution'] = 'informational'
    for item in row(values, path)['clauses']: item['targets'] = []
    with pytest.raises(ValueError, match='constraint_informational'): build(values)


@pytest.mark.parametrize('mutation', ['deleted', 'extra', 'reordered', 'hash', 'refs', 'field', 'field_extra', 'scope', 'boolean_scope'])
def test_owner_inventory_fields_hashes_and_business_scope_are_closed(mutation):
    values = fixture(); owners = values['owner_bindings']
    if mutation == 'deleted': owners.pop()
    elif mutation == 'extra': owners.append(copy.deepcopy(owners[0]))
    elif mutation == 'reordered': owners[1], owners[2] = owners[2], owners[1]
    elif mutation == 'hash': owners[0]['value_hash'] = '0' * 64
    elif mutation == 'refs': owners[1]['ref_hashes'] = []
    elif mutation == 'field': clause(values, '/businesses/0/steps/0', 'next_index')['source_field'] = 'next'
    elif mutation == 'field_extra': owners[0]['clauses'].append({'source_field': 'approved', 'targets': []})
    elif mutation == 'scope': row(values, '/businesses/0/steps/0')['scope_businesses'] = ['陌生业务']
    elif mutation == 'boolean_scope': owners[0]['scope_businesses'] = [True]
    with pytest.raises(ValueError): build(values)


@pytest.mark.parametrize('mutation', ['missing_step', 'duplicate_step', 'foreign_step', 'wrong_kind', 'wrong_role', 'wrong_actor'])
def test_every_selected_source_step_maps_once_with_exact_kind_role_and_actor(mutation):
    values = fixture(); nodes = values['graph']['nodes']; a, b = nodes[:2]
    if mutation == 'missing_step': b['source_step_id'] = None
    elif mutation == 'duplicate_step': b['source_step_id'] = a['source_step_id']
    elif mutation == 'foreign_step': b['source_step_id'] = uid(9999)
    elif mutation == 'wrong_kind': a['source_step_id'], nodes[3]['source_step_id'] = None, a['source_step_id']
    elif mutation == 'wrong_role': a['role'] = '职责乙'
    elif mutation == 'wrong_actor': a['actor_id'] = values['role_bindings']['职责乙']['user_id']
    with pytest.raises(ValueError): build(values)


@pytest.mark.parametrize('field,target', [('kind', ref('node', 'f')), ('start_rule', ref('node', 'f')),
    ('wait_rule', ref('node', 'f')), ('join_rule', ref('node', 'f'))])
def test_parallel_start_wait_join_cannot_be_one_ordinary_node_string(field, target):
    values = fixture(); clause(values, '/businesses/0/coordination/0', field)['targets'] = [target]
    with pytest.raises(ValueError): build(values)


def test_parallel_scope_and_condition_each_affected_step_cannot_be_lost():
    values = fixture(); clause(values, '/businesses/0/coordination/0', 'step_indices')['targets'].pop()
    with pytest.raises(ValueError, match='source_step_scope'): build(values)
    values = fixture(change=lambda result: result['businesses'][0]['conditions'][0].update(step_indices=[0, 1]))
    clause(values, '/businesses/0/conditions/0', 'step_indices')['targets'] = [ref('node', 'a'), ref('node', 'b')]
    with pytest.raises(ValueError, match='condition_scope_not_applied'): build(values)


def test_source_end_cannot_be_silently_replaced_by_another_source_step():
    values = fixture(branch=True)
    clause(values, '/businesses/0/steps/1', 'next_index')['targets'] = [ref('edge', 'd_true')]
    with pytest.raises(ValueError, match='source_edge_from'): build(values)
    values = fixture(branch=True, change=lambda result: result['businesses'][0]['steps'][0].update(next_index=None))
    with pytest.raises(ValueError, match='source_end_changed'): build(values)


def test_branch_predicate_cannot_be_an_unrelated_applied_predicate_or_swapped_outcome():
    values = fixture(branch=True)
    clause(values, '/businesses/0/steps/0', 'branch_condition')['targets'] = [ref('predicate', 'p_start')]
    with pytest.raises(ValueError, match='branch_predicate_not_applied'): build(values)
    values = fixture(branch=True)
    clause(values, '/businesses/0/steps/0', 'next_index')['targets'] = [ref('edge', 'd_false')]
    with pytest.raises(ValueError, match='source_edge_outcome'): build(values)


@pytest.mark.parametrize('mutation', ['field_type', 'field_origin', 'source_hash', 'origin_shape', 'unbound_ref', 'wrong_ref_type', 'nullable'])
def test_schema_origins_and_typed_value_references_cannot_be_forged_or_omitted(mutation):
    values = fixture(wait=True)
    target = clause(values, '/objects/0/fields/0', 'kind')['targets'][0]
    if mutation == 'field_type': target['field_type'] = '数值'
    elif mutation == 'field_origin': target['field'] = '其他字段'
    elif mutation == 'source_hash': target['origin']['owner_value_hash'] = '0' * 64
    elif mutation == 'origin_shape': target['origin']['verified'] = True
    elif mutation == 'unbound_ref': clause(values, '/objects/0/fields/0', 'kind')['targets'].pop()
    elif mutation == 'wrong_ref_type':
        values['graph']['references']['case']['kind'] = 'number'
        values['graph']['input_schema']['properties']['case'] = {'type': 'number'}
    elif mutation == 'nullable':
        values['graph']['references']['case']['nullable'] = True
        values['graph']['input_schema']['properties']['case'] = {'type': ['string', 'null']}
    with pytest.raises(ValueError): build(values)


def test_structured_workspace_input_is_explicitly_bound_without_becoming_a_source_scalar():
    values = fixture(); graph = values['graph']
    graph['input_schema'] = schema({'payload': schema({'name': {'type': 'string'}})})
    graph['references']['payload'] = {'kind': 'object', 'nullable': False,
        'source': 'input', 'field': 'payload', 'node_id': None}
    clause(values, '/global_claims/0', 'statement')['targets'].append(ref('value_ref', 'payload'))
    value = build(values)
    assert check_binding(value) == value
    row(values, '/global_claims/0')['reason'] = ''
    with pytest.raises(ValueError, match='text'): build(values)


@pytest.mark.parametrize('mutation', ['missing_target', 'duplicate_target', 'unknown_target', 'extra_target_key',
    'unapplied_predicate', 'unclaimed_goal', 'informational_reason', 'proof_hash', 'manifest_hash'])
def test_graph_targets_and_preserved_source_position_proof_fail_closed(mutation):
    values = fixture()
    if mutation == 'missing_target': clause(values, '/businesses/0/steps/0', 'purpose')['targets'] = []
    elif mutation == 'duplicate_target': clause(values, '/businesses/0/steps/0', 'purpose')['targets'] *= 2
    elif mutation == 'unknown_target': clause(values, '/businesses/0/steps/0', 'purpose')['targets'] = [ref('node', 'missing')]
    elif mutation == 'extra_target_key': clause(values, '/businesses/0/steps/0', 'purpose')['targets'][0]['approved'] = True
    elif mutation == 'unapplied_predicate':
        values['graph']['predicates']['p_hidden'] = {'op': 'value', 'kind': 'boolean', 'value': True}
        clause(values, '/global_claims/0', 'statement')['targets'].append(ref('predicate', 'p_hidden'))
    elif mutation == 'unclaimed_goal': values['graph']['goals'].append({'id': 'hidden', 'required_nodes': ['a']})
    elif mutation == 'informational_reason': row(values, '/domain')['reason'] = ' '
    elif mutation == 'proof_hash': values['proof']['proof_hash'] = '0' * 64
    elif mutation == 'manifest_hash': values['manifest']['manifest_hash'] = '0' * 64
    with pytest.raises(ValueError): build(values)


@pytest.mark.parametrize('mutation', ['pin_extra', 'pin_changed', 'role_extra', 'role_permission', 'role_username', 'unbound_actor'])
def test_capabilities_and_role_pins_are_exact_closed_snapshots(mutation):
    values = fixture(ability=True)
    if mutation == 'pin_extra': values['capability_pins']['a'] = copy.deepcopy(values['capability_pins']['b'])
    elif mutation == 'pin_changed': values['capability_pins']['b']['capability_hash'] = '0' * 64
    elif mutation == 'role_extra': values['role_bindings']['职责甲']['verified'] = True
    elif mutation == 'role_permission': values['role_bindings']['职责甲']['membership_role'] = 'viewer'
    elif mutation == 'role_username': values['role_bindings']['职责甲']['username'] = ''
    elif mutation == 'unbound_actor': values['graph']['nodes'][1]['actor_id'] = uid(9999)
    with pytest.raises(ValueError): build(values)


@pytest.mark.parametrize('field', ['source_business', 'source_design', 'manifest_hash', 'proof_hash',
    'graph', 'owner_bindings', 'capability_pins', 'role_bindings', 'blocked_businesses',
    'coverage_hash', 'semantic_mode', 'source_semantics_status', 'candidate_index'])
def test_cold_check_rebuild_rejects_self_resealed_or_type_confused_values(field):
    values = fixture(); value = build(values)
    if field == 'source_business': value[field]['trigger'] = '伪造触发'
    elif field == 'source_design': value[field]['result']['domain'] += '改'
    elif field in {'manifest_hash', 'proof_hash', 'coverage_hash'}: value[field] = '0' * 64
    elif field == 'graph': value[field]['nodes'][0]['actor_id'] = uid(9999)
    elif field == 'owner_bindings': value[field][0]['reason'] += '改'
    elif field == 'capability_pins': value[field] = {'fake': {}}
    elif field == 'role_bindings': value[field]['职责甲']['user_id'] = uid(9999)
    elif field == 'blocked_businesses': value[field] = ['合成业务']
    elif field == 'candidate_index': value[field] = False
    else: value[field] = 'forged'
    value['binding_hash'] = digest({key: item for key, item in value.items() if key != 'binding_hash'})
    with pytest.raises(ValueError): check_binding(value)


def test_hashes_only_close_the_design_and_never_grant_an_issued_approval():
    values = fixture(); original = build(values)
    row(values, '/global_claims/0')['reason'] = '另一个明确的工作区解释，需另行真实采用'
    changed = build(values)
    assert changed['coverage_hash'] != original['coverage_hash']
    assert changed['binding_hash'] != original['binding_hash']
    assert check_binding(changed) == changed
    # A valid new pure design is not the original issued adoption. The DB layer
    # must bind exact hash, actor, command consent and accepted SetupRevision.
