"""Root-run PostgreSQL/API evidence for saved state/background reads.

Fixtures create real reported inputs, local SDK results, independent ACKs and
normal manual requests. The original consumer saves ObjectState/Job/Audit before
these GETs. No state GET is used to compute a successful fixture. Read guards
only fail on forbidden work; no successful database or authorization is mocked.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import perception, state_refresh, worker
from generic_agents import state_projection_consumer_v266 as consumer
from generic_agents import state_projection_admission_v267 as subject
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from tests.test_instance_workspace import OBJECTS, enable, object_row
from tests.test_state_projection_consumer_v266_pg import (
    numeric_scene, _producer, _current_jobs, _manual, _manual_proof,
    _saved_projection, _protected, _job_stock, _pin,
)


BACKGROUND_KEYS = {'schema', 'workspace_id', 'actor_id', 'instance_id', 'entity_id',
    'stored_state', 'cache', 'background', 'permissions', 'checked_at', 'recomputed', 'cloud_calls'}
JOB_KEYS = {'id', 'status', 'available_at', 'created_at', 'reason', 'result',
    'error', 'cancel_reason', 'admission'}


def _good(response, status=200):
    assert response.status_code == status, response.text[:1000]
    return response.json()


def _base(s):
    return '/api/workspace/instances/' + s.release['instance_id'] + '/objects/' + s.entity['id']


def _background(s, *, client=None):
    return (client or s.client).get(_base(s) + '/state-background')


def _lookup(s, body, *, client=None):
    return (client or s.client).get(_base(s) + '/refresh-state/requests/' + body['request_id'])


def _inventory(s):
    """Include all background Audits and cache bytes that V266 protects separately."""
    protected = _protected(s.workspace_id)
    jobs = _job_stock(s.workspace_id)
    with Session(engine()) as db:
        return {'protected': protected, 'jobs': jobs,
            'states': {row.id: _pin(row) for row in db.scalars(select(m.ObjectState).where(
                m.ObjectState.workspace_id == s.workspace_id))},
            'audits': {row.id: _pin(row) for row in db.scalars(select(m.Audit).where(
                m.Audit.workspace_id == s.workspace_id))}}


def _forbid_read_side_work(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('saved-background GET attempted computation, enqueue, dispatch or another reader')

    for module, name in ((perception, 'object_state'), (state_refresh, 'enqueue'),
            (state_refresh, 'process'), (worker, 'claim'), (worker, 'process_once'),
            (worker, 'deliver_events'), (consumer, 'step'), (consumer, 'run'),
            (consumer, 'request_manual'), (consumer, '_reader')):
        monkeypatch.setattr(module, name, forbidden)


def _seed(s, kind):
    if kind == 'source':
        _producer(s, s.observation['id'])
        identities = _current_jobs(s.workspace_id, s.observation['id'])
        assert len(identities) == 1
        identity, body, proof_id = identities[0], None, None
        observation_id = s.observation['id']
    else:
        response, body, _ = _manual(s)
        identity = _good(response)['id']
        proof_id = _manual_proof(s, identity, body)
        observation_id = None
    outcome = consumer.step(s.workspace_id, s.release['instance_id'], job_id=identity)
    assert outcome['processed'] is True and outcome['job_id'] == identity
    saved = _saved_projection(s, identity, observation_id=observation_id)
    return identity, body, proof_id, saved


def _assert_background(s, value):
    assert set(value) == BACKGROUND_KEYS
    assert value['schema'] == subject.SCHEMA and value['workspace_id'] == s.workspace_id
    assert value['instance_id'] == s.release['instance_id'] and value['entity_id'] == s.entity['id']
    assert value['recomputed'] is False and value['cloud_calls'] == 0
    assert datetime.fromisoformat(value['checked_at']).utcoffset() is not None
    assert set(value['background']) == {'latest', 'history', 'next_expiry', 'history_limit'}
    assert value['background']['history_limit'] == 30
    assert set(value['permissions']) == {'can_request_new', 'reason'}
    for job in value['background']['history']:
        assert set(job) == JOB_KEYS
        assert set(job['admission']) == {'state', 'reason', 'can_request_new'}
    return value


def _saved_job(value, identity):
    jobs = [row for row in value['background']['history'] if row['id'] == identity]
    assert len(jobs) == 1
    return jobs[0]


def _assert_lookup(s, value, identity, body, proof_id):
    assert value['schema'] == subject.LOOKUP and value['found'] is True
    assert value['workspace_id'] == s.workspace_id and value['instance_id'] == s.release['instance_id']
    assert value['entity_id'] == s.entity['id'] and value['request_id'] == body['request_id']
    assert value['recomputed'] is False and value['cloud_calls'] == 0
    assert value['not_found_proves_unexecuted'] is False
    assert set(value['job']) == JOB_KEYS and value['job']['id'] == identity
    with Session(engine()) as db:
        job = db.get(m.Job, identity)
        proof = db.get(m.Audit, proof_id)
        assert proof is not None and proof.actor_id == job.data['actor_id'] == value['actor_id']
        assert value['request'] == {'request_id': body['request_id'], 'actor_id': proof.actor_id,
            'instance_id': s.release['instance_id'], 'entity_id': s.entity['id'], 'job_id': identity,
            'idempotency_key': job.idempotency_key, 'state_request_hash': job.data['state_request_hash'],
            'canonical_body_hash': digest({'request_id': body['request_id']}),
            'audit': {'id': proof.id, 'action': proof.action, 'object_id': proof.object_id,
                'actor_id': proof.actor_id, 'data_hash': digest(proof.data)}}
        assert set(value['request']['audit']) == {'id', 'action', 'object_id', 'actor_id', 'data_hash'}
    return value


@contextmanager
def _login(s, username, password, *, role):
    with TestClient(s.client.app) as client:
        login = _good(client.post('/api/login', json={'username': username, 'password': password}))
        client.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': s.workspace_id})
        me = _good(client.get('/api/me'))
        assert me['workspace_id'] == s.workspace_id and me['role'] == role
        with Session(engine()) as db:
            user = db.get(m.User, me['user_id'])
            membership = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.workspace_id,
                m.Membership.user_id == me['user_id']))
            assert user is not None and user.active and membership is not None and membership.role == role
        yield client, me['user_id']


def _new_admin(s):
    username, password = 'v267_admin_' + uuid4().hex[:12], uuid4().hex
    user = _good(s.client.post('/api/users', json={
        'username': username, 'password': password, 'role': 'admin'}), 201)
    with Session(engine()) as db:
        actual = db.get(m.User, user['id'])
        member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.workspace_id,
            m.Membership.user_id == user['id']))
        assert actual is not None and actual.active and member is not None and member.role == 'admin'
    return username, password, user['id']


@pytest.mark.parametrize('kind', ['source', 'manual'])
def test_saved_producer_consumer_projection_is_read_without_recompute_or_writes(numeric_scene, monkeypatch, kind):
    s = numeric_scene
    identity, body, proof_id, saved = _seed(s, kind)
    before = _inventory(s)
    _forbid_read_side_work(monkeypatch)
    value = _assert_background(s, _good(_background(s)))
    job = _saved_job(value, identity)
    assert job['status'] == 'succeeded' and job['admission']['state'] == 'historical'
    assert job['result'] == saved.job_data['state_result']
    stored = value['stored_state']
    payload = {key: val for key, val in saved.data.items()
        if key not in {'state_hash', 'evaluated_at', 'history', 'history_truncated'}}
    assert stored == {'version': saved.version, 'state_hash': saved.data['state_hash'],
        'evaluated_at': saved.data['evaluated_at'], 'value': payload}
    assert value['cache']['status'] == 'saved' and value['cache']['background_result_matched'] is True
    assert value['permissions']['can_request_new'] is True
    if kind == 'source':
        assert stored['value']['fields']['公开登记值']['value'] == 65
    else:
        lookup = _assert_lookup(s, _good(_lookup(s, body)), identity, body, proof_id)
        assert lookup['job']['result'] == saved.job_data['state_result']
    assert _inventory(s) == before


def test_missing_cache_is_reported_without_computing_or_enqueuing(numeric_scene, monkeypatch):
    s = numeric_scene
    before = _inventory(s)
    assert before['states'] == {}
    _forbid_read_side_work(monkeypatch)
    value = _assert_background(s, _good(_background(s)))
    assert value['stored_state'] is None and value['cache']['status'] == 'missing'
    assert value['cache']['background_result_matched'] is False
    assert _inventory(s) == before


def test_namespace_collision_cannot_cross_the_real_source_instance_for_saved_reads(numeric_scene, monkeypatch):
    s = numeric_scene
    first_id, first_body, first_proof, first_saved = _seed(s, 'manual')
    # Both instances, entities and saved caches are produced normally. Current
    # generation gives distinct namespaces; the collision below is explicitly
    # an isolated corruption probe, not a claim that the normal API creates it.
    draft = _good(s.client.get('/api/setup/draft'))
    actor = _good(s.client.get('/api/me'))['user_id']
    body = {'revision': draft['revision'], 'objects': copy.deepcopy(OBJECTS),
        'role_bindings': {'核验负责人': actor}}
    check = _good(s.client.post('/api/instances/check', json=body))
    release = _good(s.client.post('/api/instances', json={**body,
        'confirmed_configuration_hash': check['configuration_hash'], 'confirm_configuration': True,
        'generation_intent': 'new_instance', 'request_id': str(uuid4())}), 201)
    assert release['instance_id'] != s.release['instance_id']
    enable(s.client, release)
    foreign = SimpleNamespace(client=s.client, workspace_id=s.workspace_id,
        release=release, entity=object_row(s.client, release))
    second_id, second_body, second_proof, second_saved = _seed(foreign, 'manual')
    with Session(engine()) as db:
        first_instance = db.get(m.DomainInstance, s.release['instance_id'])
        second_instance = db.get(m.DomainInstance, release['instance_id'])
        first_entity = db.get(m.Entity, s.entity['id'])
        second_entity = db.get(m.Entity, foreign.entity['id'])
        assert first_instance.namespace == first_entity.namespace
        assert second_instance.namespace == second_entity.namespace
        assert first_instance.namespace != second_instance.namespace
        assert first_entity.schema_capability_id == s.release['package_id']
        assert second_entity.schema_capability_id == release['package_id']
        assert first_entity.schema_capability_id != second_entity.schema_capability_id
    ordinary_before = _inventory(s)
    _forbid_read_side_work(monkeypatch)
    for requested, other, nonce in ((s, foreign, second_body), (foreign, s, first_body)):
        wrong = '/api/workspace/instances/' + requested.release['instance_id'] + '/objects/' + other.entity['id']
        for suffix in ('/state-background', '/refresh-state/requests/' + nonce['request_id']):
            response = s.client.get(wrong + suffix)
            assert response.status_code == 404
            assert 'stored_state' not in response.text and 'state_hash' not in response.text
    assert _inventory(s) == ordinary_before
    with Session(engine()) as db:
        first_instance = db.get(m.DomainInstance, s.release['instance_id'])
        second_instance = db.get(m.DomainInstance, release['instance_id'])
        second_entity = db.get(m.Entity, foreign.entity['id'])
        # Penetrate the namespace-only predicate without changing either real
        # source release, saved request/Audit, cache payload or its checksum.
        second_instance.namespace = first_instance.namespace
        second_entity.namespace = first_instance.namespace
        db.commit()
    before = _inventory(s)
    for requested, other, nonce in ((s, foreign, second_body), (foreign, s, first_body)):
        wrong = '/api/workspace/instances/' + requested.release['instance_id'] + '/objects/' + other.entity['id']
        for suffix in ('/state-background', '/refresh-state/requests/' + nonce['request_id']):
            response = s.client.get(wrong + suffix)
            assert response.status_code == 404
            assert 'stored_state' not in response.text and 'state_hash' not in response.text
    for own, identity, nonce, proof, saved in ((s, first_id, first_body, first_proof, first_saved),
            (foreign, second_id, second_body, second_proof, second_saved)):
        view = _assert_background(own, _good(_background(own)))
        assert view['stored_state']['state_hash'] == saved.data['state_hash']
        assert _saved_job(view, identity)['status'] == 'succeeded'
        _assert_lookup(own, _good(_lookup(own, nonce)), identity, nonce, proof)
    assert _inventory(s) == before


def test_ready_manual_request_get_does_not_claim_or_compute_it(numeric_scene, monkeypatch):
    s = numeric_scene
    response, body, _ = _manual(s)
    identity = _good(response)['id']
    proof_id = _manual_proof(s, identity, body)
    before = _inventory(s)
    _forbid_read_side_work(monkeypatch)
    value = _assert_background(s, _good(_background(s)))
    job = _saved_job(value, identity)
    assert job['status'] == 'queued' and job['result'] is None and job['admission']['state'] == 'ready'
    assert value['stored_state'] is None and value['cache']['status'] == 'missing'
    lookup = _assert_lookup(s, _good(_lookup(s, body)), identity, body, proof_id)
    assert lookup['job']['status'] == 'queued' and lookup['job']['admission']['state'] == 'ready'
    assert _inventory(s) == before
    with Session(engine()) as db:
        job = db.get(m.Job, identity)
        assert job.attempts == 0 and job.lease_token is None and job.lease_until is None


def test_corrupt_cache_is_blocked_and_original_successful_job_is_not_recomputed(numeric_scene, monkeypatch):
    s = numeric_scene
    identity, body, proof_id, saved = _seed(s, 'manual')
    with Session(engine()) as db:
        row = db.scalar(select(m.ObjectState).where(m.ObjectState.workspace_id == s.workspace_id,
            m.ObjectState.entity_id == s.entity['id']))
        data = copy.deepcopy(row.data)
        data['fields']['v267_invalid_unverified_value'] = {'value': 999}
        row.data = data  # Disposable negative corruption; do not repair it through a state GET.
        db.commit()
    before = _inventory(s)
    _forbid_read_side_work(monkeypatch)
    value = _assert_background(s, _good(_background(s)))
    assert value['stored_state'] is None and value['cache']['status'] == 'blocked_integrity'
    assert value['cache']['background_result_matched'] is False
    job = _saved_job(value, identity)
    assert job['status'] == 'succeeded' and job['result'] == saved.job_data['state_result']
    lookup = _assert_lookup(s, _good(_lookup(s, body)), identity, body, proof_id)
    assert lookup['job']['status'] == 'succeeded' and lookup['job']['result'] == saved.job_data['state_result']
    assert 'v267_invalid_unverified_value' not in str(value)
    assert _inventory(s) == before


@pytest.mark.parametrize('damage', ['manual_audit', 'job'])
def test_lookup_rejects_broken_manual_authority_without_recreating_missing_side(numeric_scene, monkeypatch, damage):
    s = numeric_scene
    identity, body, proof_id, saved = _seed(s, 'manual')
    with Session(engine()) as db:
        if damage == 'manual_audit':
            proof = db.get(m.Audit, proof_id)
            proof.data = {**proof.data, 'state_request_hash': '0' * 64}
        else:
            db.delete(db.get(m.Job, identity))
        db.commit()
    before = _inventory(s)
    _forbid_read_side_work(monkeypatch)
    assert _lookup(s, body).status_code == 409
    value = _assert_background(s, _good(_background(s)))
    if damage == 'manual_audit':
        job = _saved_job(value, identity)
        assert job['status'] == 'succeeded' and job['result'] is None
        assert job['admission']['state'] == 'blocked_integrity'
        assert value['cache']['background_result_matched'] is False
    else:
        assert all(row['id'] != identity for row in value['background']['history'])
    assert value['stored_state']['state_hash'] == saved.data['state_hash']
    assert _inventory(s) == before


def test_viewer_owner_recovers_own_saved_receipt_and_another_admin_same_nonce_gets_no_result(numeric_scene, account, monkeypatch):
    s = numeric_scene
    identity, body, proof_id, saved = _seed(s, 'manual')
    username, password, another_id = _new_admin(s)
    with Session(engine()) as db:
        issuer = db.get(m.Job, identity).data['actor_id']
        assert issuer != another_id
        membership = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.workspace_id,
            m.Membership.user_id == issuer))
        membership.role = 'viewer'  # Actual saved permission downgrade after the authorized request.
        db.commit()
    with _login(s, account[0], account[1], role='viewer') as (owner, owner_id):
        assert owner_id == issuer
        with _login(s, username, password, role='admin') as (another, current_id):
            assert current_id == another_id
            # Authentication may create login-limit rows. Pin only after both
            # real logins so those rows cannot be mistaken for GET writes.
            before = _inventory(s)
            _forbid_read_side_work(monkeypatch)
            own = _assert_lookup(s, _good(_lookup(s, body, client=owner)), identity, body, proof_id)
            assert own['job']['status'] == 'succeeded' and own['job']['result'] == saved.job_data['state_result']
            assert own['job']['admission']['state'] == 'historical'
            assert own['job']['admission']['can_request_new'] is False
            background = _assert_background(s, _good(_background(s, client=owner)))
            assert background['permissions']['can_request_new'] is False
            response = _lookup(s, body, client=another)
            missing = _good(response)
            assert missing['actor_id'] == another_id and missing['found'] is False
            assert missing['job'] is None and missing['request'] is None
            assert missing['not_found_proves_unexecuted'] is False and missing['recomputed'] is False
            assert identity not in response.text and proof_id not in response.text
            assert saved.job_data['state_request_hash'] not in response.text
            assert _inventory(s) == before


@pytest.mark.parametrize('revocation', ['inactive', 'viewer'])
def test_historical_source_result_survives_original_issuer_losing_authority(numeric_scene, monkeypatch, revocation):
    s = numeric_scene
    identity, _, _, saved = _seed(s, 'source')
    username, password, another_id = _new_admin(s)
    with Session(engine()) as db:
        issuer = db.get(m.Job, identity).data['actor_id']
        assert issuer != another_id
        if revocation == 'inactive':
            db.get(m.User, issuer).active = False
        else:
            membership = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.workspace_id,
                m.Membership.user_id == issuer))
            membership.role = 'viewer'
        db.commit()
    with _login(s, username, password, role='admin') as (reader, reader_id):
        assert reader_id == another_id
        before = _inventory(s)
        _forbid_read_side_work(monkeypatch)
        value = _assert_background(s, _good(_background(s, client=reader)))
        job = _saved_job(value, identity)
        assert job['status'] == 'succeeded' and job['admission']['state'] == 'historical'
        assert job['result'] == saved.job_data['state_result']
        assert value['stored_state']['state_hash'] == saved.data['state_hash']
        assert value['cache']['background_result_matched'] is True
        assert _inventory(s) == before


@pytest.mark.parametrize('endpoint', ['background', 'lookup'])
def test_two_connection_pool_serves_new_get_on_caller_rr_ro_without_third_checkout(numeric_scene, monkeypatch, endpoint):
    s = numeric_scene
    identity, body, proof_id, saved = _seed(s, 'manual')
    before = _inventory(s)
    _forbid_read_side_work(monkeypatch)
    prior = engine()
    assert prior.pool.checkedout() == 0
    prior.dispose()
    observations = []
    original = getattr(subject, endpoint)
    held_pid = None

    with monkeypatch.context() as patch:
        patch.setattr(settings(), 'database_pool_size', 2)
        patch.setattr(settings(), 'database_max_overflow', 0)
        engine.cache_clear()
        bounded = engine()
        assert bounded.pool.size() == 2 and bounded.pool._max_overflow == 0

        def observe(db, p, *args):
            connection = db.get_bind()
            # dual_read_session's attestation already owns both transactions;
            # the route must preserve that caller rather than borrow a reader.
            transaction = connection.get_transaction()
            session_transaction = db.get_transaction()
            assert transaction is not None and transaction.is_active
            assert session_transaction is not None and session_transaction.is_active
            assert not db.new and not db.dirty and not db.deleted
            result = original(db, p, *args)
            assert db.get_transaction() is session_transaction
            assert db.get_bind() is connection and connection.get_transaction() is transaction
            assert db.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
            assert db.scalar(text('SHOW transaction_read_only')) == 'on'
            backend = db.scalar(text('SELECT pg_backend_pid()'))
            assert held_pid is not None and backend != held_pid and bounded.pool.checkedout() == 2
            stamp = db.scalar(text('SELECT transaction_timestamp()')).astimezone(timezone.utc).isoformat()
            assert result['checked_at'] == stamp
            assert db.get_transaction() is session_transaction and transaction.is_active
            assert not db.new and not db.dirty and not db.deleted
            observations.append((backend, result))
            return result

        patch.setattr(subject, endpoint, observe)
        try:
            # Hold a real unrelated transaction for the entire HTTP request.
            # Releasing it before joining a timed-out request prevents a pool
            # starvation failure from being hidden by the executor's cleanup.
            with ThreadPoolExecutor(max_workers=1) as requests:
                with bounded.connect() as holder:
                    held = holder.begin()
                    try:
                        held_pid = holder.scalar(text('SELECT pg_backend_pid()'))
                        assert bounded.pool.checkedout() == 1
                        started = monotonic()
                        request = requests.submit(_background if endpoint == 'background' else _lookup,
                            s, *(() if endpoint == 'background' else (body,)))
                        response = request.result(timeout=8)
                        assert monotonic() - started < 8
                        value = _good(response)
                        assert observations and observations[-1][1] == value
                        if endpoint == 'background':
                            _assert_background(s, value)
                            assert _saved_job(value, identity)['result'] == saved.job_data['state_result']
                        else:
                            # Do the DB-backed assertion after releasing the
                            # holder; it is test inspection, not GET SQL.
                            assert value['found'] is True and value['job']['id'] == identity
                    finally:
                        held.rollback()
            if endpoint == 'lookup':
                _assert_lookup(s, value, identity, body, proof_id)
            assert bounded.pool.checkedout() == 0
            assert _inventory(s) == before
        finally:
            bounded.dispose()
            engine.cache_clear()
