"""Actual retry retirement with real original-Job/advisory lock barriers.

Source/Invocation/actor/admission authority stays real. The reused transport
failure seam creates an actual failed delivery; named corruption/missing-Job
edits are test faults, never fabricated source or independent saved receipts.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import graph_event_delivery_retirement_v125 as retirement
from generic_agents import graph_event_delivery_recovery_v124 as delivery
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventAdmission, GraphEventProducer, GraphEventStream
from generic_agents.models import Audit, Capability, CloudCall, Invocation, Job, Membership, User, Workspace
from generic_agents.worker import process_once
from generic_agents.security import password_hash
from tests.test_graph_event_pipeline import BASE, ok
from tests.test_graph_event_delivery_recovery_v124 import (
    _body as retry_body, _cold as retry_cold, _counts as delivery_counts,
    _failed, _retry_path, _request_path, _seed_legacy_history, _wait_lock,
)


RECORD = retirement.GraphEventIntentRetryRetirement


def _post(identity): return BASE + '/intents/' + identity + '/retry-retirements'
def _request(identity, nonce): return _post(identity) + '/requests/' + nonce
def _state(identity, nonce): return BASE + '/intents/' + identity + '/retry-command-requests/' + nonce


def _body(original, nonce=None):
    return {'schema': retirement.COMMAND_SCHEMA, 'request_id': nonce or str(uuid4()),
        'original': copy.deepcopy(original), 'reason': ' 明确结束本次未保存的恢复申请🙂 '}


def _counts():
    counts = delivery_counts()
    with Session(engine()) as db:
        counts[RECORD.__tablename__] = db.scalar(select(func.count()).select_from(RECORD))
    return counts


def _cold():
    p, original_row = retry_cold('queued')
    original = copy.deepcopy(original_row.data['command']); original['reason'] = ' \t\n '
    body = _body(original); source_hash = delivery.command_hash(p, original_row.intent_id, original)
    value = {'schema': retirement.SCHEMA, 'id': str(uuid4()), 'request_id': body['request_id'],
        'actor_id': p.user_id, 'intent_id': original_row.intent_id, 'producer_id': original_row.producer_id,
        'job_id': original_row.job_id, 'intent_hash': original_row.data['intent_hash'],
        'original_request_id': original['request_id'], 'original_command_hash': source_hash,
        'command_hash': retirement.command_hash(p, original_row.intent_id, body), 'command': body,
        'result': {'outcome': 'retired', 'original_request_id': original['request_id'], 'original_command_hash': source_hash}}
    value['receipt_hash'] = digest(value)
    row = NS(id=value['id'], workspace_id=p.workspace_id, actor_id=p.user_id,
        intent_id=value['intent_id'], producer_id=value['producer_id'], job_id=value['job_id'],
        request_id=value['request_id'], original_request_id=value['original_request_id'],
        original_hash=source_hash, command_hash=value['command_hash'], receipt_hash=value['receipt_hash'], version=1, data=value)
    return p, row


def test_pure_retirement_keeps_both_original_texts_exact_and_returns_a_detached_closed_receipt():
    p, row = _cold(); before = copy.deepcopy(row.__dict__)
    value = retirement._checked_value(row, p, identity=row.intent_id, nonce=row.request_id)
    assert value['command']['reason'].startswith(' ') and value['command']['reason'].endswith(' ')
    assert value['command']['original']['reason'] == ' \t\n '
    assert value['original_command_hash'] == delivery.command_hash(p, row.intent_id, value['command']['original'])
    value['command']['reason'] = '不可替换原输入'; value['result']['outcome'] = 'saved'
    assert row.__dict__ == before


@pytest.mark.parametrize('change', ['extra', 'missing', 'reason', 'original', 'command_extra', 'nonce',
    'original_nonce', 'intent', 'producer', 'job', 'workspace', 'version', 'boolean_version',
    'original_hash', 'command_hash', 'receipt_hash', 'result_extra', 'result_outcome', 'result_nonce', 'result_hash'])
def test_pure_cold_corruption_is_409_without_repair(change):
    p, row = _cold()
    if change == 'extra': row.data['extra'] = True
    elif change == 'missing': row.data.pop('command')
    elif change == 'reason': row.data['command']['reason'] = '未签入的新理由'
    elif change == 'original': row.data['command']['original']['reason'] = '未签入的新原正文'
    elif change == 'command_extra': row.data['command']['extra'] = True
    elif change == 'nonce': row.request_id = str(uuid4())
    elif change == 'original_nonce': row.original_request_id = str(uuid4())
    elif change == 'intent': row.intent_id = str(uuid4())
    elif change == 'producer': row.producer_id = str(uuid4())
    elif change == 'job': row.job_id = str(uuid4())
    elif change == 'workspace': row.workspace_id = str(uuid4())
    elif change == 'version': row.version = 2
    elif change == 'boolean_version': row.version = True
    elif change in {'original_hash', 'command_hash', 'receipt_hash'}: setattr(row, change, 'f' * 64)
    elif change == 'result_extra': row.data['result']['extra'] = True
    elif change == 'result_outcome': row.data['result']['outcome'] = 'saved'
    elif change == 'result_nonce': row.data['result']['original_request_id'] = str(uuid4())
    else: row.data['result']['original_command_hash'] = 'f' * 64
    before = copy.deepcopy(row.__dict__)
    with pytest.raises(HTTPException) as caught: retirement._checked_value(row, p)
    assert caught.value.status_code == 409 and row.__dict__ == before


@pytest.mark.parametrize('reason', ['', ' ', '\x1c', '\x85', '\x00', '\ud800', True, 'x' * 1001])
def test_pure_new_reason_is_meaningful_and_safe_without_trimming_valid_text(reason):
    _, row = _cold(); body = copy.deepcopy(row.data['command']); body['reason'] = reason
    with pytest.raises(ValidationError): retirement.Retirement.model_validate(body)


def test_pure_new_nonce_is_independent_and_actor_403_precedes_corrupt_body():
    p, row = _cold(); body = copy.deepcopy(row.data['command'])
    body['request_id'] = body['original']['request_id']
    with pytest.raises(ValidationError): retirement.Retirement.model_validate(body)
    row.actor_id = str(uuid4()); row.data = None
    with pytest.raises(HTTPException) as caught: retirement._checked_value(row, p)
    assert caught.value.status_code == 403


def test_actual_advanced_job_original_not_found_retires_and_late_retry_cannot_change_any_delivery_state(client, monkeypatch):
    value, identity, job_id, version, _ = _failed(client, monkeypatch)
    original = retry_body(version); original['reason'] = ' \t\n '
    # Another genuine command advances this same Job and its actual admission.
    ok(client.post(_retry_path(identity), json=retry_body(version)))
    assert process_once(job_id, deliver=False)
    state = ok(client.get(_state(identity, original['request_id'])))
    assert state == {'schema': retirement.STATE_SCHEMA, 'intent_id': identity,
        'request_id': original['request_id'], 'outcome': 'not_found', 'command_hash': None,
        'saved': None, 'legacy': None, 'retirement': None}
    body = _body(original); before = _counts()
    accepted = ok(client.post(_post(identity), json=body)); receipt = accepted['retirement']
    assert accepted['replayed'] is False and receipt['job_id'] == job_id and receipt['command'] == body
    assert receipt['original_command_hash'] == delivery.command_hash(value['principal'], identity, original)
    assert receipt['result'] == {'outcome': 'retired', 'original_request_id': original['request_id'],
        'original_command_hash': receipt['original_command_hash']}
    assert _counts() == {**before, RECORD.__tablename__: before[RECORD.__tablename__] + 1,
        Audit.__tablename__: before[Audit.__tablename__] + 1}
    after = _counts()
    assert client.post(_retry_path(identity), json=original).status_code == 409
    assert ok(client.get(_request(identity, body['request_id']))) == {'found': True, 'retirement': receipt}
    assert ok(client.post(_post(identity), json=body)) == {'retirement': receipt, 'replayed': True}
    assert client.post(_post(identity), json={**body, 'reason': '同号不能换新理由'}).status_code == 409
    assert client.post(_post(identity), json={**body, 'request_id': str(uuid4())}).status_code == 409
    assert _counts() == after
    with Session(engine()) as db:
        job = db.get(Job, job_id); assert job.status == 'succeeded'
        producer = db.get(GraphEventProducer, value['producers'][0]['id']); producer.status = 'revoked'; producer.version += 1
        db.get(Capability, value['source']['id']).status = 'retired'
        db.get(Capability, value['extractor']['id']).status = 'retired'
        job.data = {'named_current_job_health_fault': True}; job.version += 1
        member = db.scalar(select(Membership).where(Membership.user_id == value['principal'].user_id,
            Membership.workspace_id == value['principal'].workspace_id)); member.role = 'viewer'
        db.commit()
    final = ok(client.get(_state(identity, original['request_id'])))
    assert final['outcome'] == 'retired' and final['retirement'] == receipt
    assert final['saved'] is None and final['legacy'] is None
    assert ok(client.get(_request(identity, body['request_id']))) == {'found': True, 'retirement': receipt}
    assert ok(client.post(_post(identity), json=body)) == {'retirement': receipt, 'replayed': True}
    assert client.post(_retry_path(identity), json=original).status_code == 409
    assert _counts() == after
    with Session(engine()) as db:
        assert db.get(Job, job_id).data == {'named_current_job_health_fault': True}
        assert db.scalar(select(GraphEventStream.last_sequence)) == 1
        assert db.scalar(select(func.count()).select_from(Invocation)) == 1 and db.scalar(select(CloudCall.id)) is None


@pytest.mark.parametrize('kind', ['new', 'legacy'])
def test_actual_saved_new_or_real_legacy_command_cannot_be_retired(client, monkeypatch, kind):
    value, identity, job_id, version, _ = _failed(client, monkeypatch)
    if kind == 'new':
        original = retry_body(version)
        ok(client.post(_retry_path(identity), json=original))
    else:
        original, _ = _seed_legacy_history(value, identity, job_id, version)
    before = _counts(); body = _body(original)
    state = ok(client.get(_state(identity, original['request_id'])))
    assert state['outcome'] == 'saved' and state['retirement'] is None
    assert state['command_hash'] == delivery.command_hash(value['principal'], identity, original)
    assert (state['saved'] is not None) is (kind == 'new')
    assert (state['legacy'] is not None) is (kind == 'legacy')
    assert client.post(_post(identity), json=body).status_code == 409
    assert ok(client.get(_request(identity, body['request_id']))) == {'found': False, 'retirement': None}
    assert ok(client.post(_retry_path(identity), json=original))['replayed'] is True
    assert _counts() == before


@pytest.mark.parametrize('fault', ['missing_job', 'bad_history'])
def test_actual_unverifiable_legacy_absence_never_authorizes_fresh_retirement(client, monkeypatch, fault):
    _, identity, job_id, version, _ = _failed(client, monkeypatch)
    original = retry_body(version); body = _body(original)
    with Session(engine()) as db:
        job = db.get(Job, job_id)
        if fault == 'missing_job': db.delete(job)
        else: job.data = {**job.data, 'graph_event_retries': [{'unverifiable': True}]}
        db.commit()
    before = _counts()
    assert client.post(_post(identity), json=body).status_code == 409
    assert client.get(_state(identity, original['request_id'])).status_code == 409
    assert ok(client.get(_request(identity, body['request_id']))) == {'found': False, 'retirement': None}
    assert _counts() == before


def test_actual_same_original_and_retirement_nonces_are_independent_across_intents(client, monkeypatch):
    value, first, _, version, _ = _failed(client, monkeypatch)
    _, second, _, other_version, _ = _failed(client, monkeypatch, value=value, temperature=29)
    original_nonce, retirement_nonce = str(uuid4()), str(uuid4())
    for identity, current in ((first, version), (second, other_version)):
        original = retry_body(current, original_nonce); body = _body(original, retirement_nonce)
        ok(client.post(_post(identity), json=body))
        receipt = ok(client.get(_request(identity, retirement_nonce)))['retirement']
        assert receipt['intent_id'] == identity and receipt['original_request_id'] == original_nonce
        assert client.post(_retry_path(identity), json=original).status_code == 409
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(RECORD)) == 2
        assert db.scalar(select(func.count()).select_from(Invocation)) == 2
        assert db.scalar(select(GraphEventAdmission.id)) is None and db.scalar(select(CloudCall.id)) is None


def test_actual_corrupt_retirement_blocks_all_lookup_replay_and_late_retry_without_repair(client, monkeypatch):
    _, identity, job_id, version, _ = _failed(client, monkeypatch)
    original = retry_body(version); body = _body(original)
    accepted = ok(client.post(_post(identity), json=body)); row_id = accepted['retirement']['id']
    with Session(engine()) as db:
        row = db.get(RECORD, row_id)
        baseline = {key: copy.deepcopy(getattr(row, key))
            for key in ('data', 'original_hash', 'command_hash', 'receipt_hash', 'version')}
    before = _counts()
    for change in ('reason', 'original', 'original_hash', 'command_hash', 'receipt_hash', 'extra', 'result', 'version'):
        with Session(engine()) as db:
            row = db.get(RECORD, row_id)
            for key, item in baseline.items(): setattr(row, key, copy.deepcopy(item))
            data = copy.deepcopy(row.data)
            if change == 'reason': data['command']['reason'] = '未签入的新理由'
            elif change == 'original': data['command']['original']['reason'] = '未签入的新原正文'
            elif change in {'original_hash', 'command_hash', 'receipt_hash'}: setattr(row, change, 'f' * 64)
            elif change == 'extra': data['extra'] = True
            elif change == 'result': data['result']['outcome'] = 'saved'
            else: row.version = 2
            row.data = data; db.commit(); broken = {key: copy.deepcopy(getattr(row, key)) for key in baseline}
        assert client.get(_request(identity, body['request_id'])).status_code == 409, change
        assert client.get(_state(identity, original['request_id'])).status_code == 409, change
        assert client.post(_post(identity), json=body).status_code == 409, change
        assert client.post(_retry_path(identity), json=original).status_code == 409, change
        assert _counts() == before
        with Session(engine()) as db:
            row = db.get(RECORD, row_id)
            assert {key: copy.deepcopy(getattr(row, key)) for key in baseline} == broken
            assert db.get(Job, job_id).status == 'failed' and db.get(Job, job_id).version == version
    with Session(engine()) as db:
        row = db.get(RECORD, row_id)
        for key, item in baseline.items(): setattr(row, key, copy.deepcopy(item))
        db.commit()
    assert ok(client.get(_request(identity, body['request_id'])))['retirement'] == accepted['retirement']


def test_actual_unsafe_retirement_dto_errors_are_safe_and_no_original_state_changes(client, monkeypatch):
    _, identity, job_id, version, _ = _failed(client, monkeypatch)
    original = retry_body(version); body = _body(original); before = _counts()
    cases = []
    for reason in ('', ' ', '\x1c', '\x00', '\ud800'):
        cases.append(({**body, 'reason': reason}, 422))
    cases.append(({**body, 'request_id': 'not-a-uuid'}, 422))
    cases.append(({**body, 'request_id': original['request_id']}, 422))
    cases.append(({**body, 'original': {**original, 'reason': '\x00'}}, 409))
    cases.append(({**body, 'original': {**original, 'reason': '\ud800'}}, 422))
    for payload, status in cases:
        response = client.post(_post(identity), content=json.dumps(payload, ensure_ascii=True),
            headers={'Content-Type': 'application/json'})
        assert response.status_code == status, response.text
        assert type(response.json()['detail']) is str and 'input' not in response.json()
        assert _counts() == before
    assert ok(client.get(_state(identity, original['request_id'])))['outcome'] == 'not_found'
    with Session(engine()) as db:
        assert db.get(Job, job_id).status == 'failed' and db.get(Job, job_id).version == version


def test_actual_other_actor_and_workspace_cannot_read_or_retire_the_original_operation(client, monkeypatch):
    value, identity, _, version, _ = _failed(client, monkeypatch)
    original = retry_body(version); body = _body(original)
    accepted = ok(client.post(_post(identity), json=body))
    username, password = 'retry_retire_' + uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password)); db.add(user); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=value['principal'].workspace_id, role='operator'))
        db.commit(); job_ids = set(db.scalars(select(Job.id)))
    before = _counts()
    with TestClient(app) as other:
        login = ok(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': value['principal'].workspace_id})
        after = _counts()
        assert after == {**before, Job.__tablename__: before[Job.__tablename__] + 1}
        with Session(engine()) as db:
            added = db.execute(select(Job.kind, Job.status).where(Job.id.not_in(job_ids))).all()
            assert [tuple(item) for item in added] == [('login_limit', 'internal')]
        assert other.get(_request(identity, body['request_id'])).status_code == 403
        assert other.get(_state(identity, original['request_id'])).status_code == 403
        assert other.post(_post(identity), json=body).status_code == 403
        assert other.post(_retry_path(identity), json=original).status_code == 403
        assert _counts() == after
    with Session(engine()) as db:
        workspace = Workspace(name='独立的申请工作区'); db.add(workspace); db.flush()
        db.add(Membership(user_id=value['principal'].user_id, workspace_id=workspace.id, role='admin'))
        db.commit(); other_workspace = workspace.id
    headers = {'X-Workspace-ID': other_workspace}
    assert client.get(_request(identity, body['request_id']), headers=headers).status_code == 404
    assert client.get(_state(identity, original['request_id']), headers=headers).status_code == 404
    assert client.post(_post(identity), json=body, headers=headers).status_code == 404
    assert ok(client.get(_request(identity, body['request_id'])))['retirement'] == accepted['retirement']


@pytest.mark.parametrize('first', ['retry', 'retirement'])
def test_actual_both_directions_job_and_original_nonce_barriers_allow_only_one_outcome(client, monkeypatch, first):
    _, identity, job_id, version, _ = _failed(client, monkeypatch)
    original = retry_body(version); body = _body(original); before = _counts()
    with Session(engine()) as blocker, ThreadPoolExecutor(max_workers=2) as pool:
        blocker.get(Job, job_id, with_for_update=True)
        if first == 'retry':
            a = pool.submit(client.post, _retry_path(identity), json=original)
        else:
            a = pool.submit(client.post, _post(identity), json=body)
        b = None
        try:
            _wait_lock([a], '%jobs%')
            if first == 'retry': b = pool.submit(client.post, _post(identity), json=body)
            else: b = pool.submit(client.post, _retry_path(identity), json=original)
            _wait_lock([a, b], '%pg_advisory_xact_lock%')
        finally:
            blocker.rollback()
        first_response, second_response = a.result(timeout=10), b.result(timeout=10)
    assert first_response.status_code == 200 and second_response.status_code == 409, (
        first_response.text, second_response.text)
    state = ok(client.get(_state(identity, original['request_id'])))
    expected = {**before, Audit.__tablename__: before[Audit.__tablename__] + 1}
    if first == 'retry':
        assert state['outcome'] == 'saved' and state['retirement'] is None
        expected[delivery.GraphEventIntentRetryReceipt.__tablename__] += 1
        assert ok(client.get(_request(identity, body['request_id']))) == {'found': False, 'retirement': None}
    else:
        assert state['outcome'] == 'retired' and state['saved'] is None and state['legacy'] is None
        expected[RECORD.__tablename__] += 1
        assert state['retirement'] == first_response.json()['retirement']
    assert _counts() == expected
    with Session(engine()) as db:
        job = db.get(Job, job_id)
        assert job.version == version + (first == 'retry')
        assert job.status == ('queued' if first == 'retry' else 'failed')
        assert db.scalar(select(func.count()).select_from(Invocation)) == 1
        assert db.scalar(select(GraphEventAdmission.id)) is None and db.scalar(select(CloudCall.id)) is None


def test_actual_actor_lock_race_blocks_new_retirement_before_any_job_mutation(client, monkeypatch):
    value, identity, job_id, version, _ = _failed(client, monkeypatch)
    original = retry_body(version); body = _body(original); before = _counts()
    with Session(engine()) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        member = blocker.scalar(select(Membership).where(Membership.user_id == value['principal'].user_id,
            Membership.workspace_id == value['principal'].workspace_id).with_for_update())
        member.role = 'viewer'; blocker.flush()
        future = pool.submit(client.post, _post(identity), json=body)
        try: _wait_lock([future], '%memberships%')
        finally: blocker.commit()
        response = future.result(timeout=10)
    assert response.status_code == 403, response.text
    assert _counts() == before
    assert ok(client.get(_state(identity, original['request_id'])))['outcome'] == 'not_found'
    with Session(engine()) as db:
        assert db.get(Job, job_id).status == 'failed' and db.get(Job, job_id).version == version
