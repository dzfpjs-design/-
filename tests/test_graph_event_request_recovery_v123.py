"""Exact unsaved-command retirement, real PG nonce races and cold recovery.

All integration records start through actual source APIs. Named direct edits
are storage/health fault controls; no fake producer authority or saved receipt
is inserted, and no source/model/permission gate is patched.
"""
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import graph_event_request_recovery_v123 as recovery
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventProducer, GraphEventIntent, GraphEventAdmission
from generic_agents.graph_event_revocation_v122 import GraphEventProducerRevocation
from generic_agents.models import Audit, Capability, CloudCall, Event, Invocation, Job, Membership, Task, User
from generic_agents.security import password_hash
from tests.test_graph_event_pipeline import BASE, ok, scene
from tests.test_graph_event_revocation_v122 import _command as revocation_command


POST = BASE + '/command-retirements'
RECORD = recovery.GraphEventSourceRetirement


def _original(value, kind):
    if kind == 'register':
        return {**value['commands'][0], 'request_id': str(uuid4()), 'label': '尚未保存的明确来源登记🙂'}
    return revocation_command(value['producers'][0])


def _body(kind, original):
    return {'schema': 'graph-event-source-retirement-v1', 'request_id': str(uuid4()),
        'kind': kind, 'original': copy.deepcopy(original), 'reason': '保留原正文并阻止迟到提交🙂'}


def _state_path(kind, nonce):
    return BASE + '/command-requests/' + kind + '/' + nonce


def _receipt_path(body):
    return POST + '/requests/' + body['request_id']


def _send_original(client, kind, original):
    return client.post(BASE if kind == 'register' else BASE + '/revocations', json=original)


def _counts():
    with Session(engine()) as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Task, Job, Invocation, CloudCall, Event, GraphEventProducer,
                GraphEventIntent, GraphEventAdmission, GraphEventProducerRevocation, RECORD, Audit)}


def _cold(kind):
    p = NS(user_id=str(uuid4()), workspace_id=str(uuid4()))
    if kind == 'register':
        original = {'schema': 'graph-event-producer-command-v1', 'request_id': str(uuid4()),
            'label': '保留旧Register允许的前后空白 ', 'adapter_id': 'processed-observation-v1',
            'source_id': str(uuid4()), 'source_hash': 'a' * 64,
            'extractor_id': str(uuid4()), 'extractor_hash': 'b' * 64}
    else:
        original = revocation_command({'id': str(uuid4()), 'version': 2, 'contract_hash': 'a' * 64})
    command = _body(kind, original); source_hash = recovery.original_hash(p, kind, original)
    row = NS(id=str(uuid4()), workspace_id=p.workspace_id, actor_id=p.user_id, kind=kind,
        original_request_id=original['request_id'], request_id=command['request_id'],
        original_hash=source_hash, command_hash=recovery.command_hash(p, command), version=1,
        data={'command': copy.deepcopy(command), 'result': {'outcome': 'retired', 'kind': kind,
            'original_request_id': original['request_id'], 'original_command_hash': source_hash}})
    return p, row


@pytest.mark.parametrize('kind', ['register', 'revoke'])
def test_pure_exact_retirement_is_detached_and_keeps_original_legacy_values(kind):
    p, row = _cold(kind); before = copy.deepcopy(row.__dict__)
    receipt = recovery.checked_receipt(row, p, request_id=row.request_id,
        kind=kind, original_request_id=row.original_request_id)
    assert set(receipt) == {'id', 'request_id', 'actor_id', 'kind', 'original_request_id',
        'original_command_hash', 'command_hash', 'command', 'result'}
    assert receipt['command']['original'] == row.data['command']['original']
    if kind == 'register': assert receipt['command']['original']['label'].endswith(' ')
    receipt['command']['original']['request_id'] = str(uuid4())
    receipt['result']['outcome'] = 'saved'
    assert row.__dict__ == before


@pytest.mark.parametrize('change', ['extra', 'kind_mismatch', 'same_nonce', 'reason_empty',
    'reason_untrimmed', 'reason_long', 'original_extra', 'original_normalization', 'original_strict_type'])
def test_pure_typed_retirement_never_normalizes_or_accepts_another_command_family(change):
    _, row = _cold('register'); command = copy.deepcopy(row.data['command'])
    if change == 'extra': command['extra'] = True
    elif change == 'kind_mismatch': command['kind'] = 'revoke'
    elif change == 'same_nonce': command['request_id'] = command['original']['request_id']
    elif change == 'reason_empty': command['reason'] = ''
    elif change == 'reason_untrimmed': command['reason'] = ' 原因 '
    elif change == 'reason_long': command['reason'] = 'x' * 1001
    elif change == 'original_extra': command['original']['extra'] = True
    elif change == 'original_normalization':
        command['original']['request_id'] = '{' + command['original']['request_id'] + '}'
    else: command['original']['label'] = 3
    with pytest.raises(ValidationError): recovery.Retirement.model_validate(command)


@pytest.mark.parametrize(('field', 'bad'), [('label', '\ud800'), ('label', '\x00'),
    ('label', float('nan')), ('extra', float('inf'))])
def test_pure_unsafe_original_json_has_a_safe_non_echoing_error(field, bad):
    _, row = _cold('register'); command = copy.deepcopy(row.data['command'])
    command['original'][field] = bad
    with pytest.raises(HTTPException) as caught: recovery.Retirement.model_validate(command)
    assert caught.value.status_code == 422 and type(caught.value.detail) is str


@pytest.mark.parametrize('kind', ['register', 'revoke'])
@pytest.mark.parametrize('change', ['data_extra', 'data_missing', 'command_extra', 'reason', 'original',
    'original_nonce', 'nonce', 'kind', 'workspace', 'original_hash', 'command_hash', 'version',
    'boolean_version', 'result_extra', 'result_outcome', 'result_kind', 'result_nonce', 'result_hash'])
def test_pure_cold_corruption_fails_without_repair(kind, change):
    p, row = _cold(kind)
    if change == 'data_extra': row.data['extra'] = True
    elif change == 'data_missing': row.data.pop('result')
    elif change == 'command_extra': row.data['command']['extra'] = True
    elif change == 'reason': row.data['command']['reason'] = '未签入的新理由'
    elif change == 'original': row.data['command']['original']['schema'] = '另一种原命令'
    elif change == 'original_nonce': row.original_request_id = str(uuid4())
    elif change == 'nonce': row.request_id = str(uuid4())
    elif change == 'kind': row.kind = 'revoke' if kind == 'register' else 'register'
    elif change == 'workspace': row.workspace_id = str(uuid4())
    elif change in {'original_hash', 'command_hash'}: setattr(row, change, 'f' * 64)
    elif change == 'version': row.version = 2
    elif change == 'boolean_version': row.version = True
    elif change == 'result_extra': row.data['result']['extra'] = True
    elif change == 'result_outcome': row.data['result']['outcome'] = 'saved'
    elif change == 'result_kind': row.data['result']['kind'] = 'other'
    elif change == 'result_nonce': row.data['result']['original_request_id'] = str(uuid4())
    else: row.data['result']['original_command_hash'] = 'f' * 64
    before = copy.deepcopy(row.__dict__)
    with pytest.raises(HTTPException) as caught: recovery.checked_receipt(row, p)
    assert caught.value.status_code == 409 and row.__dict__ == before


def test_pure_foreign_actor_precedes_corrupt_private_record():
    p, row = _cold('register'); row.actor_id = str(uuid4()); row.data = None
    with pytest.raises(HTTPException) as caught: recovery.checked_receipt(row, p)
    assert caught.value.status_code == 403


@pytest.mark.parametrize('kind', ['register', 'revoke'])
def test_actual_unsaved_original_retires_exactly_and_late_post_never_wins_even_with_stale_sources(client, kind):
    value = scene(client); original = _original(value, kind); body = _body(kind, original)
    path = _state_path(kind, original['request_id']); before = _counts()
    assert ok(client.get(path)) == {'schema': 'graph-event-source-request-state-v1', 'kind': kind,
        'request_id': original['request_id'], 'outcome': 'not_found', 'command_hash': None,
        'saved': None, 'retirement': None}
    assert ok(client.get(_receipt_path(body))) == {'found': False, 'retirement': None}
    assert _counts() == before
    with Session(engine()) as db:
        db.get(Capability, value['source']['id']).status = 'retired'
        db.get(Capability, value['extractor']['id']).status = 'retired'
        db.commit()
    accepted = ok(client.post(POST, json=body)); receipt = accepted['retirement']
    assert accepted['replayed'] is False and receipt['command'] == body
    assert receipt['original_command_hash'] == recovery.original_hash(value['principal'], kind, original)
    assert receipt['command_hash'] == recovery.command_hash(value['principal'], body)
    assert _counts() == {**before, RECORD.__tablename__: before[RECORD.__tablename__] + 1,
        Audit.__tablename__: before[Audit.__tablename__] + 1}
    after = _counts()
    assert ok(client.get(_receipt_path(body))) == {'found': True, 'retirement': receipt}
    assert ok(client.post(POST, json=body)) == {'retirement': receipt, 'replayed': True}
    state = ok(client.get(path))
    assert state['outcome'] == 'retired' and state['saved'] is None
    assert state['retirement'] == receipt and state['command_hash'] == receipt['original_command_hash']
    assert _send_original(client, kind, original).status_code == 409
    assert client.post(POST, json={**body, 'reason': '同退休编号不能更换理由'}).status_code == 409
    assert client.post(POST, json={**body, 'request_id': str(uuid4())}).status_code == 409
    assert _counts() == after
    with Session(engine()) as db:
        producer = db.get(GraphEventProducer, value['producers'][0]['id'])
        assert producer.status == 'active' and producer.version == value['producers'][0]['version']
        member = db.scalar(select(Membership).where(Membership.user_id == value['principal'].user_id,
            Membership.workspace_id == value['principal'].workspace_id))
        member.role = 'viewer'; db.commit()
    assert ok(client.get(path)) == state
    assert ok(client.get(_receipt_path(body))) == {'found': True, 'retirement': receipt}
    assert client.post(POST, json=body).status_code == 403
    assert _counts() == after


@pytest.mark.parametrize('kind', ['register', 'revoke'])
def test_actual_saved_original_is_history_not_a_new_retirement_and_does_not_need_live_source(client, kind):
    value = scene(client)
    if kind == 'register': original = value['commands'][0]
    else:
        original = _original(value, kind)
        ok(_send_original(client, kind, original))
    body = _body(kind, original); before = _counts()
    with Session(engine()) as db:
        db.get(Capability, value['source']['id']).status = 'retired'; db.commit()
    state = ok(client.get(_state_path(kind, original['request_id'])))
    assert state['outcome'] == 'saved' and state['retirement'] is None
    assert state['command_hash'] == recovery.original_hash(value['principal'], kind, original)
    if kind == 'register': assert state['saved']['registration']['command'] == original
    else: assert state['saved']['command'] == original
    assert client.post(POST, json=body).status_code == 409
    assert ok(client.get(_receipt_path(body))) == {'found': False, 'retirement': None}
    assert _counts() == before


def test_actual_other_admin_cannot_read_replay_or_rebind_the_original_retirement(client):
    value = scene(client); original = _original(value, 'register'); body = _body('register', original)
    receipt = ok(client.post(POST, json=body))['retirement']
    username, password = 'source_retire_' + uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password)); db.add(user); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=value['principal'].workspace_id, role='admin'))
        db.commit(); job_ids = set(db.scalars(select(Job.id)))
    before = _counts()
    with TestClient(app) as other:
        login = ok(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': value['principal'].workspace_id})
        after = _counts()
        assert after == {**before, Job.__tablename__: before[Job.__tablename__] + 1}
        with Session(engine()) as db:
            added = db.execute(select(Job.kind, Job.status).where(Job.id.not_in(job_ids))).all()
            assert [tuple(row) for row in added] == [('login_limit', 'internal')]
        assert other.get(_receipt_path(body)).status_code == 403
        assert other.get(_state_path('register', original['request_id'])).status_code == 403
        assert other.post(POST, json=body).status_code == 403
        assert other.post(POST, json={**body, 'request_id': str(uuid4())}).status_code == 403
        assert _send_original(other, 'register', original).status_code == 403
        assert _counts() == after
    assert ok(client.get(_receipt_path(body))) == {'found': True, 'retirement': receipt}


def test_actual_unsafe_json_returns_safe_422_without_creating_retirement_or_source_work(client):
    value = scene(client); body = _body('register', _original(value, 'register')); before = _counts()
    for field, bad in (('label', '\ud800'), ('label', '\x00'), ('label', float('nan')), ('extra', float('inf'))):
        payload = copy.deepcopy(body); payload['original'][field] = bad
        # Raw JSON preserves the explicit unsafe-input fault instead of having
        # httpx's own JSON encoder reject NaN before it reaches the actual API.
        response = client.post(POST, content=json.dumps(payload, ensure_ascii=True),
            headers={'Content-Type': 'application/json'})
        assert response.status_code == 422, response.text
        assert type(response.json()['detail']) is str
    assert _counts() == before


@pytest.mark.parametrize('kind', ['register', 'revoke'])
def test_actual_corrupt_retirement_blocks_all_recovery_and_late_mutation_without_repair(client, kind):
    value = scene(client); original = _original(value, kind); body = _body(kind, original)
    accepted = ok(client.post(POST, json=body)); identity = accepted['retirement']['id']
    with Session(engine()) as db:
        row = db.get(RECORD, identity)
        baseline = {key: copy.deepcopy(getattr(row, key)) for key in ('data', 'original_hash', 'command_hash', 'version')}
    before = _counts()
    for change in ('reason', 'original', 'original_hash', 'command_hash', 'closed_record', 'result', 'version'):
        with Session(engine()) as db:
            row = db.get(RECORD, identity)
            for key, content in baseline.items(): setattr(row, key, copy.deepcopy(content))
            data = copy.deepcopy(row.data)
            if change == 'reason': data['command']['reason'] = '未签入的新理由'
            elif change == 'original': data['command']['original']['request_id'] = str(uuid4())
            elif change in {'original_hash', 'command_hash'}: setattr(row, change, 'f' * 64)
            elif change == 'closed_record': data['extra'] = True
            elif change == 'result': data['result']['outcome'] = 'saved'
            else: row.version = 2
            row.data = data; db.commit()
            corrupted = {key: copy.deepcopy(getattr(row, key)) for key in baseline}
        assert client.get(_receipt_path(body)).status_code == 409, change
        assert client.get(_state_path(kind, original['request_id'])).status_code == 409, change
        assert client.post(POST, json=body).status_code == 409, change
        assert _send_original(client, kind, original).status_code == 409, change
        assert _counts() == before, change
        with Session(engine()) as db:
            row = db.get(RECORD, identity)
            assert {key: copy.deepcopy(getattr(row, key)) for key in baseline} == corrupted
    with Session(engine()) as db:
        row = db.get(RECORD, identity)
        for key, content in baseline.items(): setattr(row, key, copy.deepcopy(content))
        db.commit()
    assert ok(client.get(_receipt_path(body))) == {'found': True, 'retirement': accepted['retirement']}


def _wait_for_lock(futures, pattern, count):
    deadline = time.monotonic() + 5
    while True:
        with engine().connect() as connection:
            waiting = connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query ILIKE :pattern"), {'pattern': pattern})
        if waiting >= count: return
        assert all(not future.done() for future in futures), 'request did not reach the actual PG lock barrier'
        assert time.monotonic() < deadline, 'actual PG lock was not observed'
        time.sleep(.02)


@pytest.mark.parametrize('kind', ['register', 'revoke'])
def test_actual_original_post_and_retirement_serialize_on_the_same_nonce_and_cannot_both_win(client, kind):
    value = scene(client); original = _original(value, kind); body = _body(kind, original); before = _counts()
    with Session(engine()) as blocker, ThreadPoolExecutor(max_workers=2) as pool:
        recovery.lock_original(blocker, value['principal'], kind, original['request_id'])
        original_future = pool.submit(_send_original, client, kind, original)
        retired_future = pool.submit(client.post, POST, json=body)
        try:
            _wait_for_lock([original_future, retired_future], '%pg_advisory_xact_lock%', 2)
        finally:
            blocker.rollback()
        saved_response, retired_response = original_future.result(timeout=10), retired_future.result(timeout=10)
    success = 201 if kind == 'register' else 200
    assert (saved_response.status_code, retired_response.status_code) in {(success, 409), (409, 200)}, (
        saved_response.text, retired_response.text)
    state = ok(client.get(_state_path(kind, original['request_id'])))
    expected = {**before, Audit.__tablename__: before[Audit.__tablename__] + 1}
    if saved_response.status_code == success:
        assert state['outcome'] == 'saved' and state['retirement'] is None
        model = GraphEventProducer if kind == 'register' else GraphEventProducerRevocation
        expected[model.__tablename__] += 1
        assert ok(client.get(_receipt_path(body))) == {'found': False, 'retirement': None}
    else:
        assert state['outcome'] == 'retired' and state['saved'] is None
        expected[RECORD.__tablename__] += 1
        assert state['retirement'] == retired_response.json()['retirement']
    assert _counts() == expected


def test_actual_actor_role_change_while_waiting_for_actor_lock_blocks_new_retirement(client):
    value = scene(client); original = _original(value, 'register'); body = _body('register', original)
    before = _counts()
    with Session(engine()) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        member = blocker.scalar(select(Membership).where(Membership.user_id == value['principal'].user_id,
            Membership.workspace_id == value['principal'].workspace_id).with_for_update())
        member.role = 'viewer'; blocker.flush()
        future = pool.submit(client.post, POST, json=body)
        try:
            _wait_for_lock([future], '%memberships%', 1)
        finally:
            blocker.commit()
        response = future.result(timeout=10)
    assert response.status_code == 403, response.text
    assert _counts() == before
    state = ok(client.get(_state_path('register', original['request_id'])))
    assert state['outcome'] == 'not_found' and state['retirement'] is None
