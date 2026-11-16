"""Call-local reuse tests, not substitutes for the real PG source/ledger gates.

Only the expensive cold boundary is replaced in the projection tests; its
required current/historical mode, actual row/scope and every subsequent header,
accepted-revision/origin/definition check remain observable. Existing issued PG
schema tests still exercise the unchanged checked_adoption implementation.
"""
import copy
import inspect
from types import SimpleNamespace as NS
from uuid import uuid4

from fastapi import HTTPException
import pytest

from generic_agents.api import app
from generic_agents import report_full_semantic_graph_adoption as graph
from generic_agents import report_full_semantic_schema_adoption as schema


def _origin(name, kind='object'):
    return {'schema': schema.ORIGIN, 'adoption_id': str(uuid4()), 'plan_hash': 'a' * 64,
        'kind': kind, 'name': name, 'owner_path': '/objects/0', 'owner_value_hash': 'b' * 64}


def _target(origin, field=None):
    return {'kind': 'schema_origin', 'id': origin['name'], 'origin': copy.deepcopy(origin),
        'field': field, 'field_type': None if field is None else 'number'}


def _owners(*targets):
    return [{'clauses': [{'source_field': 'value', 'targets': list(targets)}]}]


def reference_fixture(monkeypatch):
    a, b, relation = _origin('同名'), _origin('另一个'), _origin('同名', 'relationship')
    config = {'instanceObjectOrigins': {a['name']: a, b['name']: b},
        'instanceRelationOrigins': {relation['name']: relation}}
    calls = []

    def checked(db, p, value, name, kind):
        calls.append((db, p, value, name, kind))
        return {'material'}

    monkeypatch.setattr(schema, 'verify_origin', checked)
    return NS(a=a, b=b, relation=relation, config=config, calls=calls, db=object(), p=object())


def test_duplicate_fields_and_owners_verify_whole_definition_once_per_kind_and_name(monkeypatch):
    f = reference_fixture(monkeypatch)
    rows = _owners(_target(f.a), _target(f.a, '面积'), _target(f.b), _target(f.relation))
    rows.extend(_owners(_target(f.a, '数量'), _target(f.b), _target(f.relation)))
    graph.schema_references(f.db, f.p, f.config, rows)
    assert [(call[3], call[4]) for call in f.calls] == [('同名', 'object'), ('另一个', 'object'), ('同名', 'relationship')]
    assert all(call[:3] == (f.db, f.p, f.config) for call in f.calls)


def test_each_later_target_is_compared_before_duplicate_verification_is_skipped(monkeypatch):
    f = reference_fixture(monkeypatch)
    different = _target(f.a, '数量'); different['origin']['owner_value_hash'] = 'f' * 64
    with pytest.raises(HTTPException) as caught:
        graph.schema_references(f.db, f.p, f.config, _owners(_target(f.a), different))
    assert caught.value.status_code == 409 and len(f.calls) == 1


def test_configuration_origin_change_between_targets_is_not_hidden_by_local_deduplication(monkeypatch):
    f = reference_fixture(monkeypatch)
    rows = _owners(_target(f.a), _target(f.a, '面积'))
    def changed(db, p, config, name, kind):
        f.calls.append((name, kind)); config['instanceObjectOrigins'][name] = {**config['instanceObjectOrigins'][name], 'plan_hash': 'f' * 64}
    monkeypatch.setattr(schema, 'verify_origin', changed)
    with pytest.raises(HTTPException): graph.schema_references(f.db, f.p, f.config, rows)
    assert f.calls == [('同名', 'object')]


@pytest.mark.parametrize('change', ['kind', 'name', 'missing_origin'])
def test_later_invalid_or_foreign_target_does_not_reuse_a_previous_origin(monkeypatch, change):
    f = reference_fixture(monkeypatch); bad = _target(f.a)
    if change == 'kind': bad['origin']['kind'] = 'manual'
    elif change == 'name': bad['id'] = '不存在'
    else: bad.pop('origin')
    with pytest.raises(HTTPException): graph.schema_references(f.db, f.p, f.config, _owners(_target(f.a), bad))
    assert len(f.calls) == 1


def test_verification_inventory_is_not_shared_between_calls_principals_or_databases(monkeypatch):
    f = reference_fixture(monkeypatch); rows = _owners(_target(f.a), _target(f.a, '面积'))
    graph.schema_references(f.db, f.p, f.config, rows)
    graph.schema_references(f.db, f.p, f.config, rows)
    second_db, second_p = object(), object()
    graph.schema_references(second_db, second_p, copy.deepcopy(f.config), rows)
    assert len(f.calls) == 3
    assert f.calls[-1][:2] == (second_db, second_p)


def test_failed_verification_is_not_reused_by_a_later_invocation(monkeypatch):
    f = reference_fixture(monkeypatch); rows = _owners(_target(f.a), _target(f.a, '面积'))
    def stale(*args): f.calls.append(args); raise HTTPException(409, 'source revoked')
    monkeypatch.setattr(schema, 'verify_origin', stale)
    with pytest.raises(HTTPException): graph.schema_references(f.db, f.p, f.config, rows)
    with pytest.raises(HTTPException): graph.schema_references(f.db, f.p, f.config, rows)
    assert len(f.calls) == 2


def projection_fixture(monkeypatch):
    p = NS(workspace_id=str(uuid4()), user_id=str(uuid4()))
    row = NS(id=str(uuid4()), workspace_id=p.workspace_id, configuration_id=str(uuid4()), version=1)
    object_names = ['对象甲', '对象乙']; relationship = '关系甲'
    plan = {'plan_hash': 'c' * 64,
        'objects': [{'name': name, 'fields': [{'label': '面积', 'kind': 'number', 'required': True}]} for name in object_names],
        'relationships': [{'name': relationship, 'source_type': object_names[0], 'target_type': object_names[1],
            'cardinality': 'many', 'allow_self': False}],
        'object_sources': {name: {'owner_path': '/objects/' + str(index), 'owner_value_hash': 'd' * 64}
            for index, name in enumerate(object_names)},
        'relationship_sources': {relationship: {'owner_path': '/relationships/0', 'owner_value_hash': 'e' * 64}}}
    header = schema._header(row.id, plan, [str(uuid4()), str(uuid4())])
    data = {'plan': copy.deepcopy(plan), 'receipt': {'accepted_revision': 7}}
    accepted = NS(version=7, data={'fullSemanticSchemaAdoptions': [copy.deepcopy(header)]})
    config = {'fullSemanticSchemaAdoptions': [copy.deepcopy(header)],
        'instanceObjects': copy.deepcopy(plan['objects']), 'instanceRelationTypes': copy.deepcopy(plan['relationships']),
        'instanceObjectOrigins': {name: schema._origin(row.id, plan, name, 'object') for name in object_names},
        'instanceRelationOrigins': {relationship: schema._origin(row.id, plan, relationship, 'relationship')}}
    calls, revisions, gets = [], [], []
    status = NS(current=True)

    class ReadOnlyDB:
        def get(self, model, identity):
            gets.append((model, identity))
            return row if model is schema.Adoption and identity == row.id else None
        def add(self, *args): pytest.fail('origin projection wrote')
        def commit(self): pytest.fail('origin projection committed')

    db = ReadOnlyDB()
    def checked(database, principal, actual, *, require_current=False):
        calls.append((database, principal, actual, require_current))
        if actual is None or actual.workspace_id != principal.workspace_id:
            raise HTTPException(404, 'actual workspace row is unavailable')
        if actual.version != 1: raise HTTPException(409, 'actual adoption row changed')
        if require_current and not status.current: raise HTTPException(409, 'actual source is stale')
        return copy.deepcopy(data)

    def revision(database, principal, identity, version):
        revisions.append((database, principal, identity, version))
        assert identity == row.configuration_id and version == data['receipt']['accepted_revision']
        return accepted

    monkeypatch.setattr(schema, 'checked_adoption', checked)
    monkeypatch.setattr(schema, '_revision', revision)
    return NS(db=db, p=p, row=row, plan=plan, data=data, config=config, header=header,
        accepted=accepted, calls=calls, revisions=revisions, gets=gets, status=status,
        object_names=object_names, relationship=relationship)


@pytest.mark.parametrize('kind', ['object', 'relationship'])
def test_current_origin_cold_checks_real_row_once_then_keeps_accepted_header_and_definition_checks(monkeypatch, kind):
    f = projection_fixture(monkeypatch)
    name = f.object_names[0] if kind == 'object' else f.relationship
    assert schema.verify_origin(f.db, f.p, f.config, name, kind) == set(f.header['material_ids'])
    assert f.calls == [(f.db, f.p, f.row, True)]
    assert f.gets == [(schema.Adoption, f.row.id)]
    assert f.revisions == [(f.db, f.p, f.row.configuration_id, 7)]


def test_public_historical_authority_always_performs_its_own_cold_check_and_detaches_results(monkeypatch):
    f = projection_fixture(monkeypatch); f.status.current = False
    result = schema.protection_authority(f.db, f.p, f.header)
    assert f.calls == [(f.db, f.p, f.row, False)]
    assert set(result) == {'header', 'objects', 'relationships', 'object_origins', 'relationship_origins'}
    result['header']['material_ids'].clear(); result['objects'][0]['fields'].clear()
    result['relationships'][0]['source_type'] = '伪来源'
    result['object_origins'][f.object_names[0]]['owner_path'] = '/objects/99'
    again = schema.protection_authority(f.db, f.p, f.header)
    assert len(f.calls) == 2 and all(call[-1] is False for call in f.calls)
    assert again['header'] == f.header and again['objects'] == f.plan['objects']
    assert again['relationships'] == f.plan['relationships']
    assert list(inspect.signature(schema.protection_authority).parameters) == ['db', 'p', 'header']


@pytest.mark.parametrize('change', ['header_extra', 'header_materials', 'header_hash', 'duplicate_header',
    'missing_header', 'definition', 'duplicate_definition', 'origin_hash', 'origin_path',
    'accepted_missing', 'accepted_header', 'source_revoked', 'version', 'workspace'])
def test_projection_reuse_keeps_header_scope_definition_current_source_and_actual_version_gates(monkeypatch, change):
    f = projection_fixture(monkeypatch); name = f.object_names[0]; principal = f.p
    if change == 'header_extra': f.config['fullSemanticSchemaAdoptions'][0]['approved'] = True
    elif change == 'header_materials': f.config['fullSemanticSchemaAdoptions'][0]['material_ids'] = []
    elif change == 'header_hash': f.config['fullSemanticSchemaAdoptions'][0]['plan_hash'] = 'f' * 64
    elif change == 'duplicate_header': f.config['fullSemanticSchemaAdoptions'].append(copy.deepcopy(f.header))
    elif change == 'missing_header': f.config['fullSemanticSchemaAdoptions'].clear()
    elif change == 'definition': f.config['instanceObjects'][0]['fields'][0]['label'] = '被改名'
    elif change == 'duplicate_definition': f.config['instanceObjects'].append(copy.deepcopy(f.plan['objects'][0]))
    elif change == 'origin_hash': f.config['instanceObjectOrigins'][name]['owner_value_hash'] = 'f' * 64
    elif change == 'origin_path': f.config['instanceObjectOrigins'][name]['owner_path'] = '/objects/99'
    elif change == 'accepted_missing': f.accepted.data = {}
    elif change == 'accepted_header': f.accepted.data['fullSemanticSchemaAdoptions'][0]['material_ids'] = []
    elif change == 'source_revoked': f.status.current = False
    elif change == 'version': f.row.version = 2
    else: principal = NS(workspace_id=str(uuid4()), user_id=f.p.user_id)
    with pytest.raises(HTTPException) as caught: schema.verify_origin(f.db, principal, f.config, name, 'object')
    assert caught.value.status_code in {404, 409}
    assert len(f.calls) == 1 and f.calls[0][-1] is True


def test_later_source_revocation_row_change_and_scope_are_rechecked_without_cross_call_reuse(monkeypatch):
    f = projection_fixture(monkeypatch); name = f.object_names[0]
    assert schema.verify_origin(f.db, f.p, f.config, name, 'object')
    f.status.current = False
    with pytest.raises(HTTPException): schema.verify_origin(f.db, f.p, f.config, name, 'object')
    f.status.current = True; f.row.version = 2
    with pytest.raises(HTTPException): schema.verify_origin(f.db, f.p, f.config, name, 'object')
    f.row.version = 1
    other = NS(workspace_id=str(uuid4()), user_id=f.p.user_id)
    with pytest.raises(HTTPException): schema.verify_origin(f.db, other, f.config, name, 'object')
    assert len(f.calls) == 4 and all(call[-1] is True for call in f.calls)


def test_public_projection_cannot_bypass_its_cold_checker_on_stale_or_corrupt_rows(monkeypatch):
    f = projection_fixture(monkeypatch)
    def reject(*args, **kwargs): raise HTTPException(409, 'actual history failed cold verification')
    monkeypatch.setattr(schema, 'checked_adoption', reject)
    with pytest.raises(HTTPException): schema.protection_authority(f.db, f.p, f.header)
    with pytest.raises(HTTPException): schema.verify_origin(f.db, f.p, f.config, f.object_names[0], 'object')
    assert f.revisions == []
