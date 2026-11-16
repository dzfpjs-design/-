"""Synthetic editor projections with explicit mock ledger authority.

Root executes these tests. No test fixture grants real consent, reads a private
source, starts a service, executes a graph or calls a provider. Source/manifest
inventory builders use invented inline text; ledger acceptance is mocked.
"""
import copy
import json
from types import SimpleNamespace as NS
from uuid import UUID

from fastapi import HTTPException
import pytest

from generic_agents.contracts import digest
from generic_agents import report_full_semantic_graph_editor as editor
from tests.test_report_full_semantic_schema_v1_pure import inputs, build, uid


class ReadOnlyDB:
    def __init__(self):
        self.tables = {}; self.schema_rows = {}; self.calls = []

    def scalars(self, statement):
        table = statement.get_final_froms()[0].name
        params = statement.compile().params
        workspace = next(value for key, value in params.items() if key.startswith('workspace_id'))
        handoff = next(value for key, value in params.items() if key.startswith('handoff_id'))
        self.calls.append((table, workspace, handoff))
        rows = self.tables.get(table, [])
        return [row.id for row in rows if row.workspace_id == workspace and row.handoff_id == handoff][
            :statement._limit_clause.value]

    def get(self, model, identity):
        assert model is editor.schemas.Adoption
        return self.schema_rows.get(identity)


@pytest.fixture
def scene(monkeypatch):
    values = inputs(); design, manifest, proof = values[:3]; plan = build(values)
    identity = design['handoff_id']; p = NS(workspace_id=uid(50), user_id=uid(51))
    header = {'id': uid(600), 'plan_hash': plan['plan_hash'], 'material_ids': [uid(700), uid(701)]}
    authority = {'header': header, 'objects': plan['objects'], 'relationships': plan['relationships'],
        'object_origins': {item['name']: editor.schemas._origin(header['id'], plan, item['name'], 'object')
            for item in plan['objects']},
        'relationship_origins': {item['name']: editor.schemas._origin(header['id'], plan, item['name'], 'relationship')
            for item in plan['relationships']}}
    config = {'fullSemanticSchemaAdoptions': [copy.deepcopy(header)],
        'instanceObjects': copy.deepcopy(authority['objects']),
        'instanceRelationTypes': copy.deepcopy(authority['relationships']),
        'instanceObjectOrigins': copy.deepcopy(authority['object_origins']),
        'instanceRelationOrigins': copy.deepcopy(authority['relationship_origins']),
        editor.base.CONFIG_KEY: {}}
    configuration = NS(id=uid(60), version=12, data=config)
    db = ReadOnlyDB()
    schema_row = NS(id=header['id'], workspace_id=p.workspace_id, handoff_id=identity)
    db.tables[editor.schemas.Adoption.__tablename__] = [schema_row]
    db.schema_rows[schema_row.id] = schema_row
    proof_row = NS(id=uid(800), workspace_id=p.workspace_id, handoff_id=identity, actor_id=uid(52))
    db.tables[editor.positions.Proof.__tablename__] = [proof_row]
    s = NS(identity=identity, p=p, db=db, design=design, manifest=manifest, proof=proof,
        configuration=configuration, active=True, authorities={header['id']: authority},
        authority_calls=[], proof_calls=[], checked_calls=[], lookup_calls=[], rows={}, data={}, bad=set(),
        sources={identity: {'design': design}}, children={},
        public={'id': proof_row.id, 'proof': proof, 'current_source_verified': True, 'reason': '',
            'receipt': {'not_for_editor': 'private proof request metadata'}})

    def context(db, principal, identity, **kwargs):
        assert db is s.db and principal is s.p and str(identity) == s.identity and not kwargs
        return (s.configuration, {'design': s.design, 'private_review': 'NEVER_PRIVATE_REVIEW'},
            {s.identity: {}} if s.active else {}, s.manifest, {'not_returned': 'NEVER_CATALOG'},
            {'not_returned': 'NEVER_PRIVATE_SOURCE'})

    def checked_schema(db, principal, header):
        assert db is s.db and principal is s.p
        s.authority_calls.append(copy.deepcopy(header))
        actual = s.authorities.get(header['id'])
        if actual is None or actual['header'] != header:
            raise HTTPException(409, '没有真实的结构采用账本')
        return copy.deepcopy(actual)

    def public_proof(identity, proof_id, principal, db):
        assert str(identity) == s.identity and principal is s.p and db is s.db
        s.proof_calls.append(str(proof_id))
        return copy.deepcopy(s.public)

    def lookup(db, identity, lock=False):
        assert db is s.db and lock is False
        s.lookup_calls.append(identity)
        return s.rows.get(identity)

    def checked(db, principal, row):
        assert db is s.db and principal is s.p
        if row is None or row.workspace_id != principal.workspace_id or row.id in s.bad:
            raise HTTPException(409, '实际采用历史无法核对')
        s.checked_calls.append(row.id)
        return copy.deepcopy(s.data[row.id])

    monkeypatch.setattr(editor.review, 'context', context)
    monkeypatch.setattr(editor.review.replacement, 'record_set',
        lambda db, p: (copy.deepcopy(s.sources), {}, copy.deepcopy(s.children)))
    monkeypatch.setattr(editor.schemas, 'protection_authority', checked_schema)
    monkeypatch.setattr(editor.positions, 'get_proof', public_proof)
    monkeypatch.setattr(editor.base, 'lookup_adoption', lookup, raising=False)
    monkeypatch.setattr(editor.base, 'checked', checked)
    return s


def result(s):
    return editor.get_context(UUID(s.identity), s.p, s.db)


def graph_row(s, number, *, handoff=None, parent=None, root=None, revision=2, external=False):
    identity = uid(number); handoff = handoff or s.identity
    if handoff not in s.sources:
        design = copy.deepcopy(s.design); design['handoff_id'] = handoff
        s.sources[handoff] = {'design': design}
    business = s.design['result']['businesses'][0]['name']
    graph = {'nodes': [{'id': 'human', 'kind': 'human'}]}
    actions = {}; external_ids = []
    if external:
        graph['nodes'].append({'id': 'ability', 'kind': 'ability', 'execution': {'adapter': 'http'}})
        actions = {'ability': 'observe'}; external_ids = ['ability']
    binding = {'source_design': copy.deepcopy(s.sources[handoff]['design']),
        'business_name': business, 'graph': graph}
    origin = {'schema': 'report-full-semantic-graph-origin-v3', 'adoption_id': identity,
        'handoff_id': handoff, 'business_name': business, 'binding_hash': 'a' * 64,
        'command_hash': 'b' * 64}
    record = {'id': identity, 'binding': binding, 'origin': origin, 'accepted_revision': revision}
    model = editor.base.Adoption
    if parent is not None:
        record.update(parent_id=parent, root_id=root)
        model = editor.Revision
    row = NS(id=identity, handoff_id=handoff, configuration_id=s.configuration.id,
        workspace_id=s.p.workspace_id, actor_id=uid(52), business_name=business,
        data={'unverified_raw_data': 'NEVER_RAW_ROW'})
    s.rows[identity] = row
    s.db.tables.setdefault(model.__tablename__, []).append(row)
    s.data[identity] = {'record': record, 'receipt': {'id': identity, 'actor_id': uid(52),
        'receipt_hash': str(number % 10) * 64}, 'command': {'node_actions': actions,
        'external_node_ids': external_ids, 'private_unprojected': 'NEVER_COMMAND'},
        'schema': 'mock-only-real-authority-is-not-granted', 'ledger_hash': 'c' * 64}
    return identity


def current(s, identity):
    origin = s.data[identity]['record']['origin']
    s.configuration.data[editor.base.CONFIG_KEY][origin['business_name']] = copy.deepcopy(origin)


def denied(call, status=409):
    with pytest.raises(HTTPException) as error:
        call()
    assert error.value.status_code == status


def test_closed_read_only_context_uses_complete_source_inventory_and_real_schema_definitions(scene):
    s = scene; root = graph_row(s, 1000); child = graph_row(s, 1001, parent=root, root=root,
        revision=3, external=True); current(s, child)
    before = copy.deepcopy((s.configuration.data, s.design, s.manifest, s.data))
    value = result(s)
    assert set(value) == {'schema', 'handoff_id', 'active', 'design', 'manifest', 'configuration',
        'owners', 'proofs', 'schema_origins', 'adoptions', 'event_wait'}
    assert value['schema'] == editor.SCHEMA and value['active'] is True
    assert value['configuration'] == {'revision': 12, 'hash': digest(s.configuration.data)}
    assert value['owners'] == editor.required_owner_fields(s.design)
    assert {item['owner_path'] for item in value['owners']} == {item['path'] for item in s.manifest['owners']}
    assert all(item['source_fields'] for item in value['owners'])
    assert value['proofs'] == [{'id': uid(800), 'proof_hash': s.proof['proof_hash'],
        'current_source_verified': True, 'reason': ''}]
    assert set(value['schema_origins']) == {'objects', 'relationships'}
    for label, definitions in (('objects', 'instanceObjects'), ('relationships', 'instanceRelationTypes')):
        assert [item['definition'] for item in value['schema_origins'][label]] == s.configuration.data[definitions]
        assert all(set(item) == {'name', 'origin', 'definition'} for item in value['schema_origins'][label])
    assert [item['origin']['adoption_id'] for item in value['adoptions']] == [root, child]
    assert [item['is_current'] for item in value['adoptions']] == [False, True]
    assert value['adoptions'][0]['root_id'] == root and value['adoptions'][0]['parent_id'] is None
    assert value['adoptions'][1]['root_id'] == root and value['adoptions'][1]['parent_id'] == root
    assert value['adoptions'][1]['execution_scope'] == {
        'node_actions': {'ability': 'observe'}, 'external_node_ids': ['ability']}
    assert all(set(item) == {'business_name', 'origin', 'receipt', 'binding', 'execution_scope',
        'root_id', 'parent_id', 'is_current'} for item in value['adoptions'])
    assert value['event_wait'] == {'available': False, 'reason': editor.UNAVAILABLE}
    assert set(s.checked_calls) == {root, child} and len(s.authority_calls) == 1
    assert s.proof_calls == [uid(800)]
    encoded = json.dumps(value, ensure_ascii=False)
    assert not any(token in encoded for token in ('NEVER_', 'private_unprojected', 'not_for_editor'))
    assert before == (s.configuration.data, s.design, s.manifest, s.data)
    value['design']['result']['objects'].clear(); value['adoptions'][0]['binding']['graph']['nodes'].clear()
    assert before == (s.configuration.data, s.design, s.manifest, s.data)


def test_read_scope_is_shared_workspace_read_not_original_actor_recovery(scene):
    s = scene; root = graph_row(s, 1000); current(s, root)
    foreign_proof = NS(id=uid(999), workspace_id=uid(123), handoff_id=s.identity)
    s.db.tables[editor.positions.Proof.__tablename__].append(foreign_proof)
    value = result(s)
    assert value['adoptions'][0]['receipt']['actor_id'] != s.p.user_id
    assert [item['id'] for item in value['proofs']] == [uid(800)]
    assert editor.get_context.__annotations__['p'] == editor.review.pipeline.ReadP
    assert [(route.path, route.methods) for route in editor.router.routes] == [
        (editor.base.PREFIX + '/editor-context', {'GET'})]
    assert all(workspace == s.p.workspace_id and handoff == s.identity
        for _, workspace, handoff in s.db.calls)


def test_inactive_history_stays_readable_without_renewing_source_proof(scene):
    s = scene; s.active = False
    root = graph_row(s, 1000); current(s, root)
    value = result(s)
    assert value['active'] is False and value['adoptions'][0]['is_current'] is True
    assert value['schema_origins']['objects']
    assert value['proofs'][0]['current_source_verified'] is False
    assert value['proofs'][0]['reason'] == editor.INACTIVE_REASON


def test_actual_source_stale_reason_is_preserved_and_not_turned_into_approval(scene):
    s = scene; s.public.update(current_source_verified=False, reason='正式副本已归档')
    assert result(s)['proofs'][0] == {'id': uid(800), 'proof_hash': s.proof['proof_hash'],
        'current_source_verified': False, 'reason': '正式副本已归档'}


def test_verified_public_proof_keeps_actual_null_reason(scene):
    scene.public['reason'] = None
    value = result(scene)['proofs'][0]
    assert value['current_source_verified'] is True and value['reason'] is None


def test_public_proof_failure_is_not_silently_skipped(scene, monkeypatch):
    def unavailable(*args):
        raise HTTPException(409, '原公开证明不能冷核')
    monkeypatch.setattr(editor.positions, 'get_proof', unavailable)
    denied(lambda: result(scene))


def test_malformed_stored_proof_identity_is_an_integrity_conflict(scene):
    s = scene; s.db.tables[editor.positions.Proof.__tablename__][0].id = 'not-a-proof-uuid'
    denied(lambda: result(s))
    assert not s.proof_calls


def test_source_read_authority_failure_is_not_bypassed(scene, monkeypatch):
    def missing(*args):
        raise HTTPException(404, '不属于可读工作区')
    monkeypatch.setattr(editor.review, 'context', missing)
    denied(lambda: result(scene), 404)
    assert scene.db.calls == []


@pytest.mark.parametrize('mutation', ['all_markers_deleted', 'changed_definition', 'extra_header', 'wrong_owner'])
def test_adopted_schema_cannot_be_represented_as_manual_or_forged_current_hash(scene, mutation):
    s = scene; config = s.configuration.data
    if mutation == 'all_markers_deleted':
        config['fullSemanticSchemaAdoptions'] = []
        config['instanceObjectOrigins'] = {}; config['instanceRelationOrigins'] = {}
    elif mutation == 'changed_definition':
        config['instanceObjects'][0]['fields'][0]['required'] = False
    elif mutation == 'extra_header':
        config['fullSemanticSchemaAdoptions'].append({'id': uid(999), 'plan_hash': 'f' * 64, 'material_ids': []})
    else:
        name = config['instanceObjects'][0]['name']
        config['instanceObjectOrigins'][name]['owner_value_hash'] = 'f' * 64
        s.authorities[uid(600)]['object_origins'][name]['owner_value_hash'] = 'f' * 64
    denied(lambda: result(s))


def test_same_schema_name_from_another_handoff_is_not_claimed_for_this_design(scene):
    s = scene
    s.db.schema_rows[uid(600)].handoff_id = uid(900)
    assert result(s)['schema_origins'] == {'objects': [], 'relationships': []}
    assert s.authority_calls  # It is still checked, never accepted by name/hash alone.


def test_new_design_includes_actual_current_predecessor_and_parent_chain(scene):
    s = scene; previous = uid(900)
    root = graph_row(s, 1000, handoff=previous)
    child = graph_row(s, 1001, handoff=previous, parent=root, root=root, revision=3)
    s.children[previous] = s.identity; current(s, child)
    value = result(s)
    assert [item['origin']['adoption_id'] for item in value['adoptions']] == [root, child]
    assert all(item['binding']['source_design']['handoff_id'] == previous for item in value['adoptions'])
    assert value['adoptions'][-1]['is_current'] is True
    assert set(s.checked_calls) == {root, child}


def test_same_business_name_without_real_replacement_lineage_is_a_conflict(scene):
    s = scene; unrelated = uid(900)
    root = graph_row(s, 1000, handoff=unrelated); current(s, root)
    denied(lambda: result(s))


def test_active_design_cannot_select_a_later_design_as_its_predecessor(scene):
    s = scene; later = uid(900)
    root = graph_row(s, 1000, handoff=later); current(s, root)
    s.children[s.identity] = later
    denied(lambda: result(s))


def test_inactive_design_can_read_actual_later_head_without_becoming_editable(scene):
    s = scene; s.active = False; later = uid(900)
    root = graph_row(s, 1000)
    child = graph_row(s, 1001, handoff=later, parent=root, root=root, revision=3)
    current(s, child); s.children[s.identity] = later
    value = result(s)
    assert value['active'] is False
    assert [item['is_current'] for item in value['adoptions']] == [False, True]


def test_self_consistent_binding_cannot_substitute_another_design_with_same_id(scene):
    s = scene; root = graph_row(s, 1000); current(s, root)
    s.data[root]['record']['binding']['source_design']['result']['objects'][0]['name'] = '假名'
    denied(lambda: result(s))


def test_read_that_observes_a_later_adoption_cannot_label_an_older_config_current(scene):
    s = scene; root = graph_row(s, 1000, revision=s.configuration.version + 1); current(s, root)
    denied(lambda: result(s))


@pytest.mark.parametrize('mutation', ['bad_ledger', 'missing_parent', 'cycle', 'two_children', 'wrong_root'])
def test_history_authority_and_chain_fail_closed(scene, mutation):
    s = scene; root = graph_row(s, 1000)
    child = graph_row(s, 1001, parent=root, root=root, revision=3); current(s, child)
    if mutation == 'bad_ledger': s.bad.add(root)
    elif mutation == 'missing_parent': s.rows.pop(root)
    elif mutation == 'cycle':
        s.data[root]['record'].update(parent_id=child, root_id=root)
    elif mutation == 'two_children':
        graph_row(s, 1002, parent=root, root=root, revision=4)
    else: s.data[child]['record']['root_id'] = uid(1234)
    denied(lambda: result(s))


@pytest.mark.parametrize('mutation', ['missing_action', 'human_action', 'wrong_action',
    'missing_external', 'human_external', 'duplicate_external'])
def test_execution_scope_is_original_explicit_command_and_exact_node_scope(scene, mutation):
    s = scene; root = graph_row(s, 1000, external=True); current(s, root)
    command = s.data[root]['command']
    if mutation == 'missing_action': command['node_actions'] = {}
    elif mutation == 'human_action': command['node_actions']['human'] = 'observe'
    elif mutation == 'wrong_action': command['node_actions']['ability'] = 'write'
    elif mutation == 'missing_external': command['external_node_ids'] = []
    elif mutation == 'human_external': command['external_node_ids'] = ['ability', 'human']
    else: command['external_node_ids'] = ['ability', 'ability']
    denied(lambda: result(s))


def test_response_capacity_does_not_return_a_partial_proof_history(scene, monkeypatch):
    s = scene; monkeypatch.setattr(editor, 'MAX_PROOFS', 1)
    s.db.tables[editor.positions.Proof.__tablename__].append(
        NS(id=uid(801), workspace_id=s.p.workspace_id, handoff_id=s.identity))
    denied(lambda: result(s), 413)


def test_parent_chain_has_an_independent_bound(scene, monkeypatch):
    s = scene; root = graph_row(s, 1000)
    child = graph_row(s, 1001, parent=root, root=root, revision=3); current(s, child)
    monkeypatch.setattr(editor, 'MAX_CHAIN', 1)
    denied(lambda: result(s))


def capability_options(monkeypatch, budget):
    from generic_agents import workspace_api
    cap = NS(id=uid(9000), kind='tool', content_hash='d' * 64, release_version='registered-7',
        data={'description': '合成登记能力', 'version': '1.2.3',
            'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string'}}},
            'output_schema': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}},
            'allowed_agent_roles': ['business'], 'execution': {'adapter': 'builtin',
                'secret_ref': 'NEVER_EXECUTION_SECRET'}, 'budget': copy.deepcopy(budget)})
    monkeypatch.setattr(workspace_api, 'published', lambda db, p, identity: cap)
    return workspace_api.ability_options(NS(workspace_id=uid(50)), NS(scalars=lambda statement: [cap])), cap


def test_ability_options_pin_actual_row_metadata_and_only_closed_runtime_budget(monkeypatch):
    values, cap = capability_options(monkeypatch, {'max_attempts': 3, 'max_concurrency': 2,
        'secret_ref': 'NEVER_BUDGET_SECRET', 'operator_note': 'NEVER_PRIVATE_NOTE'})
    assert len(values) == 1
    value = values[0]
    assert value['content_hash'] == cap.content_hash
    assert value['release_version'] == cap.release_version != value['version']
    assert value['max_attempts'] == 3
    assert value['budget'] == {'max_attempts': 3, 'max_concurrency': 2}
    assert value['input_schema'] == cap.data['input_schema']
    assert value['output_schema'] == cap.data['output_schema']
    assert 'NEVER_' not in json.dumps(value)
    assert 'secret_ref' not in json.dumps(value)
    value['budget']['max_attempts'] = 1
    assert cap.data['budget']['max_attempts'] == 3


def test_ability_options_defaults_match_actual_runtime_gates(monkeypatch):
    values, _ = capability_options(monkeypatch, {})
    assert values[0]['max_attempts'] == 2
    assert values[0]['budget'] == {'max_attempts': 2, 'max_concurrency': 1}


@pytest.mark.parametrize('concurrency', [True, False, 0, -1, 1.5, '2',
    'secret_ref:NEVER_SECRET', {'secret_ref': 'NEVER_SECRET'}, [1], None])
def test_ability_options_does_not_offer_malformed_or_secret_budget_values(monkeypatch, concurrency):
    values, _ = capability_options(monkeypatch, {'max_attempts': 2, 'max_concurrency': concurrency})
    assert values == []
