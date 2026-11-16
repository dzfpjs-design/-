"""Source-only authored catalog tests; Root executes against its owned PostgreSQL.

Pure rows below exercise projection and invalid-data boundaries, never producer
authority. API cases register their actual source/extractor/producer through the
existing real fixture API. Deliberate corrupt rows are never accepted as sources.
"""
import copy
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from generic_agents import graph_event_catalog_v122 as catalog
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventProducer, GraphEventIntent, GraphEventAdmission
from generic_agents.models import Capability, CloudCall, Invocation, Job, Membership, Observation
from tests.test_graph_event_pipeline import scene, ok
from tests.test_graph_event_observation_sources import contract_fixture


BASE = '/api/setup/graph-event-producers/catalog'


def _pure_capability(kind='source'):
    data = {'kind': kind, 'version': '1.2.0', 'description': '合成公开说明🙂',
        'allowed_agent_roles': ['perception'],
        'output_schema': {'type': 'object', 'properties': {'数量': {'type': 'number', 'minimum': 1.0}},
            'required': ['数量'], 'additionalProperties': False},
        'execution': {'adapter': 'synthetic-only', 'credential_ref': 'not-for-catalog'},
        'specification': {'internal': 'not-for-catalog'}}
    signature = digest(data)
    return NS(id=str(uuid4()), workspace_id=str(uuid4()), name='公开合成来源', release_version='1.2.0',
        kind=kind, status='published', content_hash=signature, validated_hash=signature, data=data)


def _workspace(client):
    return ok(client.get('/api/me'))['workspace_id']


def _counts(workspace):
    with Session(engine()) as db:
        return tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace))
            for model in (Capability, GraphEventProducer, GraphEventIntent, GraphEventAdmission,
                          Invocation, CloudCall, Job, Observation))


def test_pure_capability_projection_is_detached_bounded_and_never_contains_execution_or_credentials():
    row = _pure_capability(); row.data['description'] = '公' * 700
    row.content_hash = row.validated_hash = digest(row.data)
    before = copy.deepcopy(row.__dict__)
    value = catalog.capability_item(row, 'source')
    assert set(value) == {'id', 'name', 'description', 'kind', 'content_hash', 'release_version'}
    assert value['description'] == '公' * 500
    assert 'not-for-catalog' not in str(value)
    value['name'] = '改变响应副本'
    assert row.__dict__ == before
    for kind in ('tool', 'specialist_model', 'foundation_model'):
        assert catalog.capability_item(_pure_capability(kind), 'extractor')['kind'] == kind


@pytest.mark.parametrize('change', ['draft', 'validated', 'hash', 'validated_hash', 'kind', 'version',
    'identity', 'name', 'role', 'role_string', 'role_dict', 'output_schema', 'unsafe_data'])
def test_pure_ineligible_or_corrupt_capabilities_are_not_offered(change):
    row = _pure_capability('tool'); kind = 'extractor'
    if change in {'draft', 'validated'}: row.status = change
    elif change in {'hash', 'validated_hash'}:
        setattr(row, 'content_hash' if change == 'hash' else change, 'f' * 64)
    elif change == 'kind': row.kind = 'domain_package'
    elif change == 'version': row.release_version = '1.2.1'
    elif change == 'identity': row.id = 'not-an-identity'
    elif change == 'name': row.name = '\ud800'
    else:
        if change == 'role': row.data['allowed_agent_roles'] = ['business']
        elif change == 'role_string': row.data['allowed_agent_roles'] = 'perception'
        elif change == 'role_dict': row.data['allowed_agent_roles'] = {'perception': True}
        elif change == 'output_schema': row.data['output_schema'] = {'$ref': 'https://invalid.example/schema'}
        else: row.data['unexpected'] = float('inf')
        if change != 'unsafe_data': row.content_hash = row.validated_hash = digest(row.data)
    before = copy.deepcopy(row.__dict__)
    assert catalog.capability_item(row, kind) is None
    assert row.__dict__ == before


def test_pure_pagination_checks_and_permission_labels_never_grant_receipt_authority():
    for value in (0, 51, True, '20', None):
        with pytest.raises(HTTPException) as caught: catalog._limit(value)
        assert caught.value.status_code == 422
    assert catalog._cursor(None) is None
    identity = uuid4(); assert catalog._cursor(identity) == str(identity)
    with pytest.raises(HTTPException): catalog._cursor('invalid-cursor')
    contract = contract_fixture()
    for role, status, code in [('viewer', 'active', 'no_execution_permission'),
                               ('admin', 'revoked', 'registration_stopped')]:
        value = catalog.current_availability(None, NS(role=role), NS(status=status), contract)
        assert value == {'available': False, 'reason_code': code, 'label': catalog._LABELS[code]}


def test_database_errors_propagate_instead_of_becoming_unavailable_or_empty():
    class BrokenDB:
        def scalar(self, query):
            raise OperationalError('synthetic current contract read', {}, Exception('synthetic offline DB'))
        def scalars(self, query):
            raise OperationalError('synthetic catalog read', {}, Exception('synthetic offline DB'))
        def execute(self, query):
            raise OperationalError('synthetic actual actor read', {}, Exception('synthetic offline DB'))
    p = NS(user_id=str(uuid4()), workspace_id=str(uuid4()), role='admin', session_id='synthetic-catalog-test')
    with pytest.raises(OperationalError): catalog.list_capabilities(BrokenDB(), p, 'source')
    with pytest.raises(OperationalError): catalog.list_registrations(BrokenDB(), p)
    with pytest.raises(OperationalError):
        catalog.current_availability(BrokenDB(), p, NS(status='active', contract_hash='a' * 64), contract_fixture())


def test_actual_catalog_uses_real_published_source_and_extractor_without_starting_any_processing(client):
    value = scene(client); workspace = _workspace(client); before = _counts(workspace)
    for kind, original in [('source', value['source']), ('extractor', value['extractor'])]:
        response = ok(client.get(BASE + '/capabilities', params={'kind': kind}))
        assert set(response) == {'items', 'next_after', 'can_register'} and response['can_register'] is True
        rows = [row for row in response['items'] if row['id'] == original['id']]
        assert len(rows) == 1
        assert set(rows[0]) <= {'id', 'name', 'description', 'kind', 'content_hash', 'release_version'}
        assert rows[0]['name'] == original['name'] and rows[0]['release_version'] == original['release_version']
        assert 'data' not in rows[0] and 'execution' not in rows[0]
    producer = value['producers'][0]
    page = ok(client.get(BASE + '/registrations'))
    row = next(row for row in page['items'] if row['id'] == producer['id'])
    assert set(row) == set(producer) | {'source_name', 'extractor_name', 'current_availability', 'receipt_verified'}
    assert {key: row[key] for key in producer} == producer
    assert row['source_name'] == value['source']['name'] and row['extractor_name'] == value['extractor']['name']
    assert row['current_availability']['available'] is True
    assert row['current_availability']['reason_code'] == 'available'
    assert row['receipt_verified'] is False and row['execution_granted'] is False and row['physical_state_verified'] is False
    assert ok(client.get(BASE + '/registrations/' + producer['id'])) == row
    assert _counts(workspace) == before


def test_actual_bounded_raw_keyset_can_return_empty_pages_and_continue_past_corrupt_rows(client):
    value = scene(client); workspace = _workspace(client)
    # Invalid low-id rows are explicit corruption fixtures, not fabricated
    # published source authority. The valid source still came from its real API.
    with Session(engine()) as db:
        for number in range(1, 26):
            db.add(Capability(id=str(UUID(int=number)), workspace_id=workspace, namespace='catalog-corruption',
                name='invalid_' + str(number), release_version='1.0.0', kind='source', status='published',
                content_hash='f' * 64, validated_hash='f' * 64, data={'private': 'never-return-this-invalid-data'}))
        db.commit()
    before = _counts(workspace)
    after = None; cursors = []; found = []
    for index in range(4):
        response = ok(client.get(BASE + '/capabilities', params={'kind': 'source', 'limit': 10, **({'after': after} if after else {})}))
        assert 'never-return-this-invalid-data' not in str(response)
        if index < 2:
            assert response['items'] == [] and response['next_after'] == str(UUID(int=(index + 1) * 10))
        found.extend(row['id'] for row in response['items'])
        after = response['next_after']
        if after is None: break
        assert not cursors or after > cursors[-1]
        cursors.append(after)
    assert value['source']['id'] in found and len(found) == len(set(found)) and after is None
    assert _counts(workspace) == before


def test_actual_keyset_registration_pages_do_not_duplicate_or_skip_same_workspace_records(client):
    value = scene(client, ('processed-observation-v1', 'anomaly-candidate-v1'))
    first = ok(client.get(BASE + '/registrations', params={'limit': 1}))
    assert len(first['items']) == 1 and first['next_after'] == first['items'][0]['id']
    second = ok(client.get(BASE + '/registrations', params={'limit': 1, 'after': first['next_after']}))
    assert len(second['items']) == 1 and second['next_after'] is None
    ids = [first['items'][0]['id'], second['items'][0]['id']]
    assert ids == sorted(row['id'] for row in value['producers'])
    assert client.get(BASE + '/registrations/' + str(uuid4())).status_code == 404
    for params in ({'kind': 'unknown'}, {'kind': 'source', 'after': 'bad'}, {'kind': 'source', 'limit': 0}, {'kind': 'source', 'limit': 51}):
        assert client.get(BASE + '/capabilities', params=params).status_code == 422


def test_actual_unpublished_changed_source_and_revoked_producer_keep_cold_history_visible(client):
    value = scene(client); producer = value['producers'][0]; path = BASE + '/registrations/' + producer['id']
    with Session(engine()) as db:
        source = db.get(Capability, value['source']['id']); original_status = source.status
        source.status = 'validated'; db.commit()
    before = _counts(_workspace(client))
    unavailable = ok(client.get(path))
    assert unavailable['current_availability']['available'] is False
    assert unavailable['current_availability']['reason_code'] == 'source_unavailable'
    assert unavailable['registration'] == producer['registration'] and unavailable['contract'] == producer['contract']
    assert value['source']['id'] not in [row['id'] for row in ok(client.get(BASE + '/capabilities', params={'kind': 'source'}))['items']]
    assert _counts(_workspace(client)) == before
    with Session(engine()) as db:
        source = db.get(Capability, value['source']['id']); source.status = original_status; db.commit()
    ok(client.post('/api/setup/graph-event-producers/' + producer['id'] + '/revoke', json={'version': producer['version'], 'reason': '目录历史仍可回查'}))
    stopped = ok(client.get(path))
    assert stopped['current_availability']['reason_code'] == 'registration_stopped'
    assert stopped['registration'] == producer['registration'] and stopped['receipt_verified'] is False


def test_actual_operator_and_viewer_reads_preserve_history_without_granting_registration(client):
    value = scene(client); producer = value['producers'][0]; me = ok(client.get('/api/me'))
    try:
        for role, available in [('operator', True), ('viewer', False)]:
            with Session(engine()) as db:
                member = db.scalar(select(Membership).where(Membership.user_id == me['user_id'], Membership.workspace_id == me['workspace_id']))
                member.role = role; db.commit()
            page = ok(client.get(BASE + '/registrations'))
            assert page['can_register'] is False
            row = next(row for row in page['items'] if row['id'] == producer['id'])
            assert row['registration'] == producer['registration'] and row['current_availability']['available'] is available
            if role == 'viewer': assert row['current_availability']['reason_code'] == 'no_execution_permission'
            assert ok(client.get(BASE + '/capabilities', params={'kind': 'extractor'}))['can_register'] is False
    finally:
        with Session(engine()) as db:
            member = db.scalar(select(Membership).where(Membership.user_id == me['user_id'], Membership.workspace_id == me['workspace_id']))
            member.role = me['role']; db.commit()


def test_actual_corrupt_registration_is_not_downgraded_to_a_healthy_or_unavailable_history_row(client):
    value = scene(client); producer = value['producers'][0]
    with Session(engine()) as db:
        row = db.get(GraphEventProducer, producer['id']); original = copy.deepcopy(row.data)
        row.data = {**row.data, 'label': '未签入的改写'}; db.commit()
    try:
        assert client.get(BASE + '/registrations/' + producer['id']).status_code == 409
        assert client.get(BASE + '/registrations').status_code == 409
    finally:
        with Session(engine()) as db:
            row = db.get(GraphEventProducer, producer['id']); row.data = original; db.commit()


def test_actual_catalog_and_detail_never_expose_another_workspace_registration(client):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    from generic_agents.models import User, Workspace
    from generic_agents.security import password_hash
    own = scene(client)
    username = 'catalog_' + uuid4().hex[:12]; password = uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password))
        workspace = Workspace(name='另一独立合成目录工作区')
        db.add_all([user, workspace]); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role='admin'))
        other_workspace = workspace.id; db.commit()
    with TestClient(app) as other:
        login = ok(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': other_workspace})
        foreign = scene(other)
        assert other.get(BASE + '/registrations/' + own['producers'][0]['id']).status_code == 404
        assert client.get(BASE + '/registrations/' + foreign['producers'][0]['id']).status_code == 404
        own_sources = ok(client.get(BASE + '/capabilities', params={'kind': 'source'}))['items']
        other_sources = ok(other.get(BASE + '/capabilities', params={'kind': 'source'}))['items']
        assert own['source']['id'] in [row['id'] for row in own_sources]
        assert foreign['source']['id'] not in [row['id'] for row in own_sources]
        assert foreign['source']['id'] in [row['id'] for row in other_sources]
        assert own['source']['id'] not in [row['id'] for row in other_sources]
        assert [row['id'] for row in ok(client.get(BASE + '/registrations'))['items']] == [own['producers'][0]['id']]
