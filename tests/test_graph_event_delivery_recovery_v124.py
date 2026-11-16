"""Actual source/Invocation/worker retry recovery, plus named legacy fixtures.

Only a transport-process failure seam is injected. Admission/source/actor
validators are never patched into granting authority. Legacy history fixtures
explicitly represent the old Job format, not new independent API receipts.
"""
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import graph_event_delivery_recovery_v124 as recovery
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_event_api import RetryIntent
from generic_agents.graph_event_models import GraphEventAdmission, GraphEventIntent, GraphEventProducer, GraphEventStream
from generic_agents.models import Audit, Capability, CloudCall, Invocation, Job, Membership, Task, User, Workspace
from generic_agents.security import password_hash
from generic_agents.worker import process_once
from tests.test_graph_event_admission_pure import ledger
from tests.test_graph_event_pipeline import BASE, delivery_job, observe, ok, pending_intents, scene


RECORD = recovery.GraphEventIntentRetryReceipt


def _retry_path(identity): return BASE + '/intents/' + identity + '/retry'
def _detail_path(identity): return BASE + '/intents/' + identity + '/delivery-record'
def _request_path(identity, nonce): return BASE + '/intents/' + identity + '/retry-requests/' + nonce


def _body(version, nonce=None):
    return {'request_id': nonce or str(uuid4()), 'job_version': version, 'reason': '核对原结果后恢复同一条消息投递🙂'}


def _counts():
    with Session(engine()) as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Task, Job, Invocation, CloudCall, GraphEventIntent, GraphEventAdmission, RECORD, Audit)}


def _fault_process(monkeypatch, *, after_admission=False):
    from generic_agents import graph_event_dispatch
    actual = graph_event_dispatch.process

    def lost_transport(db, p, job):
        if after_admission: actual(db, p, job)
        raise RuntimeError('synthetic transport failure; source and admission authority are unchanged')
    monkeypatch.setattr(graph_event_dispatch, 'process', lost_transport)


def _failed(client, monkeypatch, *, admitted=False, value=None, temperature=28):
    value = value or scene(client)
    observation, _, _ = observe(client, value, temperature)
    identity = pending_intents(observation['id'])[0][0]
    job_id, _ = delivery_job(identity)
    with monkeypatch.context() as fault:
        _fault_process(fault, after_admission=admitted)
        assert process_once(job_id, deliver=False)
    with Session(engine()) as db:
        job = db.get(Job, job_id); intent = db.get(GraphEventIntent, identity)
        assert job.status == 'failed' and bool(intent.receipt_id) is admitted
        version, receipt_id = job.version, intent.receipt_id
        assert len(list(db.scalars(select(Invocation)))) >= 1
        assert db.scalar(select(CloudCall.id)) is None
    return value, identity, job_id, version, receipt_id


def _cold(outcome):
    p, producer, intent, admission = ledger()
    raw = _body(3)
    value = {'schema': recovery.SCHEMA, 'id': str(uuid4()), 'request_id': raw['request_id'],
        'actor_id': p.user_id, 'intent_id': intent.id, 'producer_id': producer.id, 'job_id': str(uuid4()),
        'intent_hash': intent.data['intent_hash'], 'command_hash': recovery.command_hash(p, intent.id, raw),
        'command': raw, 'result': {'outcome': outcome, 'accepted_job_version': 4,
            'reconciled': outcome == 'already_admitted',
            'admission': copy.deepcopy(admission.data) if outcome == 'already_admitted' else None}}
    value['receipt_hash'] = digest(value)
    row = NS(id=value['id'], workspace_id=p.workspace_id, actor_id=p.user_id, intent_id=intent.id,
        producer_id=producer.id, job_id=value['job_id'], request_id=raw['request_id'],
        command_hash=value['command_hash'], receipt_hash=value['receipt_hash'], version=1, outcome=outcome, data=value)
    return p, row


@pytest.mark.parametrize('outcome', ['queued', 'already_admitted'])
def test_pure_saved_result_is_closed_detached_and_uses_the_unchanged_command_hash(outcome):
    p, row = _cold(outcome); before = copy.deepcopy(row.__dict__)
    value = recovery._checked_value(row, p, identity=row.intent_id, nonce=row.request_id)
    assert RetryIntent.model_validate(value['command']).model_dump(mode='json') == value['command']
    assert value['command_hash'] == digest({'scope': 'graph-event-retry-v1', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'intent_id': row.intent_id, 'command': value['command']})
    value['command']['reason'] = '不能改变原回执'
    value['result']['reconciled'] = False
    assert row.__dict__ == before


@pytest.mark.parametrize('outcome', ['queued', 'already_admitted'])
@pytest.mark.parametrize('change', ['extra', 'missing', 'command', 'command_extra', 'nonce', 'intent',
    'producer', 'job', 'workspace', 'version', 'boolean_version', 'command_hash', 'receipt_hash',
    'result_extra', 'result_version', 'result_outcome', 'reconciled_type'])
def test_pure_corrupt_receipts_never_become_replayed_results(outcome, change):
    p, row = _cold(outcome)
    if change == 'extra': row.data['extra'] = True
    elif change == 'missing': row.data.pop('result')
    elif change == 'command': row.data['command']['reason'] = '未签入的新理由'
    elif change == 'command_extra': row.data['command']['extra'] = True
    elif change == 'nonce': row.request_id = str(uuid4())
    elif change == 'intent': row.intent_id = str(uuid4())
    elif change == 'producer': row.producer_id = str(uuid4())
    elif change == 'job': row.job_id = str(uuid4())
    elif change == 'workspace': row.workspace_id = str(uuid4())
    elif change == 'version': row.version = 2
    elif change == 'boolean_version': row.version = True
    elif change in {'command_hash', 'receipt_hash'}: setattr(row, change, 'f' * 64)
    elif change == 'result_extra': row.data['result']['extra'] = True
    elif change == 'result_version': row.data['result']['accepted_job_version'] = True
    elif change == 'result_outcome': row.data['result']['outcome'] = 'another'
    else: row.data['result']['reconciled'] = 1
    before = copy.deepcopy(row.__dict__)
    with pytest.raises(HTTPException) as caught: recovery._checked_value(row, p)
    assert caught.value.status_code == 409 and row.__dict__ == before


def test_pure_legacy_valid_whitespace_reason_is_not_silently_normalized():
    p, row = _cold('queued'); row.data['command']['reason'] = ' 旧正文\t\n '
    row.command_hash = row.data['command_hash'] = recovery.command_hash(p, row.intent_id, row.data['command'])
    row.receipt_hash = row.data['receipt_hash'] = digest({key: value for key, value in row.data.items() if key != 'receipt_hash'})
    assert recovery._checked_value(row, p)['command']['reason'] == ' 旧正文\t\n '


def test_pure_actor_403_precedes_corrupt_private_body():
    p, row = _cold('queued'); row.actor_id = str(uuid4()); row.data = None
    with pytest.raises(HTTPException) as caught: recovery._checked_value(row, p)
    assert caught.value.status_code == 403


def test_actual_admitted_receipt_repair_is_nonce_exact_cold_and_independent_of_current_job_role_and_source(client, monkeypatch):
    value, identity, job_id, version, admission_id = _failed(client, monkeypatch, admitted=True)
    body = _body(version); path = _retry_path(identity); lookup = _request_path(identity, body['request_id'])
    # A real admission does not imply that this retry command was ever saved.
    assert ok(client.get(lookup)) == {'found': False, 'receipt': None, 'legacy': None}
    before = _counts()
    accepted = ok(client.post(path, json=body))
    assert set(accepted) == {'already_admitted', 'admission', 'delivery'} and accepted['already_admitted']
    assert accepted['admission']['id'] == admission_id and accepted['delivery']['status'] == 'succeeded'
    restored = ok(client.get(lookup)); receipt = restored['receipt']
    assert restored['found'] and restored['legacy'] is None and receipt['job_id'] == job_id
    assert receipt['result']['outcome'] == 'already_admitted' and receipt['result']['reconciled'] is True
    assert receipt['result']['admission'] == accepted['admission']
    assert receipt['result']['accepted_job_version'] == version + 1
    after = _counts()
    assert after == {**before, RECORD.__tablename__: before[RECORD.__tablename__] + 1,
        Audit.__tablename__: before[Audit.__tablename__] + 1}
    assert client.post(path, json={**body, 'reason': '同编号不能改理由'}).status_code == 409
    assert ok(client.post(path, json=body))['admission'] == accepted['admission']
    with Session(engine()) as db:
        job = db.get(Job, job_id)
        assert 'graph_event_retries' not in job.data and job.attempts == 1
        producer = db.get(GraphEventProducer, value['producers'][0]['id'])
        producer.status = 'revoked'; producer.version += 1
        db.get(Capability, value['source']['id']).status = 'retired'
        db.get(Capability, value['extractor']['id']).status = 'retired'
        # Current Job corruption is a named health fault, never repaired by GET.
        job.data = {'current_job_health_fault': True}; job.version += 1
        member = db.scalar(select(Membership).where(Membership.user_id == value['principal'].user_id,
            Membership.workspace_id == value['principal'].workspace_id))
        member.role = 'viewer'; db.commit()
    assert ok(client.get(lookup)) == restored
    replay = ok(client.post(path, json=body))
    assert replay['already_admitted'] and replay['admission'] == accepted['admission'] and replay['delivery'] is None
    assert _counts() == after
    with Session(engine()) as db:
        assert db.get(Job, job_id).data == {'current_job_health_fault': True}
        assert db.scalar(select(GraphEventStream.last_sequence)) == 1
        assert db.scalar(select(func.count()).select_from(Invocation)) == 1
        assert db.scalar(select(CloudCall.id)) is None


def test_actual_queued_retry_replays_after_worker_progress_and_does_not_change_legacy_job_keys(client, monkeypatch):
    value, identity, job_id, version, _ = _failed(client, monkeypatch)
    body = _body(version); before = _counts()
    accepted = ok(client.post(_retry_path(identity), json=body))
    assert set(accepted) == {'replayed', 'retry', 'delivery'} and accepted['replayed'] is False
    assert accepted['retry']['accepted_job_version'] == version + 1 and accepted['delivery']['status'] == 'queued'
    receipt = ok(client.get(_request_path(identity, body['request_id'])))['receipt']
    assert receipt['result'] == {'outcome': 'queued', 'accepted_job_version': version + 1,
        'reconciled': False, 'admission': None}
    with Session(engine()) as db:
        job = db.get(Job, job_id); assert job.attempts == 1 and 'graph_event_retries' not in job.data
    assert process_once(job_id, deliver=False)
    replay = ok(client.post(_retry_path(identity), json=body))
    assert replay['replayed'] and replay['retry'] == accepted['retry'] and replay['delivery']['status'] == 'succeeded'
    assert ok(client.get(_request_path(identity, body['request_id'])))['receipt'] == receipt
    assert client.post(_retry_path(identity), json={**body, 'reason': '不同原正文'}).status_code == 409
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(RECORD)) == 1
        assert db.scalar(select(func.count()).select_from(Invocation)) == before[Invocation.__tablename__]
        assert db.scalar(select(CloudCall.id)) is None
        assert db.scalar(select(GraphEventStream.last_sequence)) == 1
        assert db.get(Job, job_id).attempts == 2


def test_actual_unsafe_retry_reason_is_controlled_before_pg_commit_and_legacy_blank_text_is_preserved(client, monkeypatch):
    _, identity, job_id, version, _ = _failed(client, monkeypatch)
    before = _counts()
    for reason in ('\x00', '\ud800'):
        body = {**_body(version), 'reason': reason}
        response = client.post(_retry_path(identity), content=json.dumps(body, ensure_ascii=True),
            headers={'Content-Type': 'application/json'})
        assert response.status_code == (409 if reason == '\x00' else 422), response.text
        assert type(response.json()['detail']) is str and reason not in response.json()['detail']
        assert _counts() == before
        with Session(engine()) as db:
            assert db.get(Job, job_id).status == 'failed' and db.get(Job, job_id).version == version
    body = {**_body(version), 'reason': ' \t\n '}
    ok(client.post(_retry_path(identity), json=body))
    receipt = ok(client.get(_request_path(identity, body['request_id'])))['receipt']
    assert receipt['command']['reason'] == body['reason']


def _seed_legacy_history(value, identity, job_id, version):
    # Explicit pre-v124 storage fixture on an actual source Job/intent. Current
    # APIs cannot honestly manufacture an old independent receipt or save time.
    body = _body(version)
    body['reason'] = ' 旧协议原样保留的恢复原因 '
    entry = {'request_id': body['request_id'], 'actor_id': value['principal'].user_id,
        'command_hash': recovery.command_hash(value['principal'], identity, body),
        'command': copy.deepcopy(body), 'accepted_job_version': version + 1}
    with Session(engine()) as db:
        job = db.get(Job, job_id); assert job.status == 'failed' and job.version == version
        job.status = 'queued'; job.version += 1
        job.data = {key: item for key, item in {**job.data, 'graph_event_retries': [entry]}.items()
            if key not in {'error', 'finished_at'}}
        db.commit()
    return body, entry


def test_actual_legacy_history_is_explicitly_legacy_not_promoted_and_corruption_is_rejected(client, monkeypatch):
    value, identity, job_id, version, _ = _failed(client, monkeypatch)
    body, entry = _seed_legacy_history(value, identity, job_id, version); before = _counts()
    lookup = _request_path(identity, body['request_id'])
    saved = ok(client.get(lookup))
    assert saved == {'found': True, 'receipt': None, 'legacy': {'origin': 'job_history_v1',
        'job_id': job_id, 'intent_id': identity, 'producer_id': value['producers'][0]['id'], 'retry': entry}}
    assert 'id' not in saved['legacy'] and 'created_at' not in saved['legacy']
    assert ok(client.post(_retry_path(identity), json=body))['retry'] == entry
    assert client.post(_retry_path(identity), json={**body, 'reason': '不能替换旧正文'}).status_code == 409
    assert _counts() == before
    assert ok(client.get(_detail_path(identity)))['action']['remaining_retries'] == 31
    with monkeypatch.context() as fault:
        _fault_process(fault)
        assert process_once(job_id, deliver=False)
    with Session(engine()) as db: current = db.get(Job, job_id).version
    new_body = _body(current)
    ok(client.post(_retry_path(identity), json=new_body))
    assert ok(client.get(_detail_path(identity)))['action']['remaining_retries'] == 30
    independent = ok(client.get(_request_path(identity, new_body['request_id'])))
    assert independent['receipt'] is not None
    before = _counts()
    with Session(engine()) as db:
        job = db.get(Job, job_id); data = copy.deepcopy(job.data)
        data['graph_event_retries'][0]['command']['reason'] = '未签入的损坏理由'
        job.data = data; db.commit()
    assert client.get(lookup).status_code == 409
    assert client.post(_retry_path(identity), json=body).status_code == 409
    assert ok(client.get(_request_path(identity, new_body['request_id']))) == independent
    assert _counts() == before


def test_actual_new_receipt_corruption_blocks_get_and_post_without_repair(client, monkeypatch):
    value, identity, job_id, version, _ = _failed(client, monkeypatch, admitted=True)
    body = _body(version); ok(client.post(_retry_path(identity), json=body))
    with Session(engine()) as db:
        row = db.scalar(select(RECORD).where(RECORD.intent_id == identity))
        row_id = row.id; baseline = {key: copy.deepcopy(getattr(row, key))
            for key in ('data', 'command_hash', 'receipt_hash', 'version', 'outcome')}
    before = _counts()
    for change in ('reason', 'hash', 'extra', 'result', 'version', 'resealed_admission'):
        with Session(engine()) as db:
            row = db.get(RECORD, row_id)
            for key, item in baseline.items(): setattr(row, key, copy.deepcopy(item))
            data = copy.deepcopy(row.data)
            if change == 'reason': data['command']['reason'] = '未签入的新理由'
            elif change == 'hash': row.command_hash = 'f' * 64
            elif change == 'extra': data['extra'] = True
            elif change == 'result': data['result']['accepted_job_version'] = True
            elif change == 'version': row.version = 2
            else:
                admission = data['result']['admission']; admission['sequence'] += 1
                admission['admission_hash'] = digest({key: item for key, item in admission.items() if key != 'admission_hash'})
                data['receipt_hash'] = row.receipt_hash = digest({key: item for key, item in data.items() if key != 'receipt_hash'})
            row.data = data; db.commit(); corrupted = {key: copy.deepcopy(getattr(row, key)) for key in baseline}
        assert client.get(_request_path(identity, body['request_id'])).status_code == 409, change
        assert client.post(_retry_path(identity), json=body).status_code == 409, change
        assert _counts() == before
        with Session(engine()) as db:
            row = db.get(RECORD, row_id)
            assert {key: copy.deepcopy(getattr(row, key)) for key in baseline} == corrupted
            assert db.get(Job, job_id).status == 'succeeded'
    with Session(engine()) as db:
        row = db.get(RECORD, row_id)
        for key, item in baseline.items(): setattr(row, key, copy.deepcopy(item))
        db.commit()
    assert ok(client.get(_request_path(identity, body['request_id'])))['found']


def test_actual_paginated_projection_is_actor_scoped_bounded_and_hides_private_proof_and_job_error(client, monkeypatch):
    value = scene(client); own_ids = []
    for temperature in (27, 28, 29):
        observation, _, _ = observe(client, value, temperature)
        own_ids.append(pending_intents(observation['id'])[0][0])
    first_job, _ = delivery_job(own_ids[0])
    with monkeypatch.context() as fault:
        _fault_process(fault)
        assert process_once(first_job, deliver=False)
    with Session(engine()) as db: version = db.get(Job, first_job).version
    original_retry = _body(version)
    ok(client.post(_retry_path(own_ids[0]), json=original_retry))
    username, password = 'delivery_reader_' + uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password)); db.add(user); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=value['principal'].workspace_id, role='operator'))
        db.commit()
    with TestClient(app) as other:
        login = ok(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': value['principal'].workspace_id})
        observation, _, _ = observe(other, value, 30)
        foreign = pending_intents(observation['id'])[0][0]
        assert client.get(_detail_path(foreign)).status_code == 403
        assert other.get(_detail_path(own_ids[0])).status_code == 403
        assert other.get(_request_path(own_ids[0], original_retry['request_id'])).status_code == 403
        assert other.post(_retry_path(own_ids[0]), json=original_retry).status_code == 403
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.idempotency_key == 'typed-graph-source:' + own_ids[0]))
        job.data = {**job.data, 'error': 'NEVER_RENDER_RAW_PRIVATE_JOB_ERROR'}; db.commit()
    before = _counts(); after = None; seen = []
    while True:
        params = {'limit': 1, **({'after': after} if after else {})}
        page = ok(client.get(BASE + '/' + value['producers'][0]['id'] + '/delivery-records', params=params))
        assert set(page) == {'schema', 'producer_id', 'items', 'next_after'}
        assert len(page['items']) <= 1
        for item in page['items']:
            assert item['actor_id'] == value['principal'].user_id and item['result'] is None
            seen.append(item['intent_id'])
        after = page['next_after']
        if after is None: break
    assert seen == sorted(own_ids) and foreign not in seen
    detail = ok(client.get(_detail_path(own_ids[0])))
    assert detail['object']['id'] == value['object']['id'] and detail['intent_status'] == 'pending'
    assert detail['result']['value']['attributes']['temperature'] == 27
    assert detail['result']['schema'] == value['producers'][0]['contract']['result_schema']
    encoded = json.dumps(detail)
    for forbidden in ('"proof"', '"model_receipt"', '"execution"', '"secret_ref"', '"error"', 'NEVER_RENDER_'):
        assert forbidden not in encoded
    assert _counts() == before
    assert client.get(BASE + '/' + value['producers'][0]['id'] + '/delivery-records', params={'limit': 51}).status_code == 422
    with Session(engine()) as db:
        workspace = Workspace(name='另一个明确工作区'); db.add(workspace); db.flush()
        db.add(Membership(user_id=value['principal'].user_id, workspace_id=workspace.id, role='admin'))
        db.commit(); other_workspace = workspace.id
    headers = {'X-Workspace-ID': other_workspace}
    assert client.get(_detail_path(own_ids[0]), headers=headers).status_code == 404
    assert client.get(_request_path(own_ids[0], str(uuid4())), headers=headers).status_code == 404


def test_actual_same_nonce_remains_legal_on_different_intent_paths(client, monkeypatch):
    value, first, _, version, _ = _failed(client, monkeypatch)
    _, second, _, other_version, _ = _failed(client, monkeypatch, value=value, temperature=29)
    nonce = str(uuid4())
    for identity, current in ((first, version), (second, other_version)):
        ok(client.post(_retry_path(identity), json=_body(current, nonce)))
        assert ok(client.get(_request_path(identity, nonce)))['receipt']['intent_id'] == identity
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(RECORD)) == 2
        assert db.scalar(select(func.count()).select_from(Invocation)) == 2
        assert db.scalar(select(CloudCall.id)) is None


def test_actual_queue_limit_counts_new_receipts_without_resetting_attempts_or_adding_legacy_keys(client, monkeypatch):
    value, identity, job_id, _, _ = _failed(client, monkeypatch)
    with monkeypatch.context() as fault:
        _fault_process(fault)
        for index in range(32):
            with Session(engine()) as db: version = db.get(Job, job_id).version
            result = ok(client.post(_retry_path(identity), json=_body(version)))
            assert result['replayed'] is False
            assert process_once(job_id, deliver=False)
    before = _counts()
    with Session(engine()) as db:
        job = db.get(Job, job_id); version = job.version
        assert job.status == 'failed' and job.attempts == 3 and 'graph_event_retries' not in job.data
    assert client.post(_retry_path(identity), json=_body(version)).status_code == 409
    detail = ok(client.get(_detail_path(identity)))
    assert detail['action']['allowed'] is False and detail['action']['reason_code'] == 'retry_limit'
    assert detail['action']['remaining_retries'] == 0
    legacy = ok(client.get(BASE + '/' + value['producers'][0]['id'] + '/intents'))[0]
    assert legacy['delivery']['can_retry'] is False
    assert _counts() == before
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(RECORD)) == 32
        assert db.scalar(select(func.count()).select_from(Invocation)) == 1
        assert db.scalar(select(CloudCall.id)) is None
        assert db.scalar(select(GraphEventAdmission.id)) is None


def _wait_lock(futures, pattern):
    deadline = time.monotonic() + 5
    while True:
        with engine().connect() as connection:
            count = connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query ILIKE :pattern"), {'pattern': pattern})
        if count: return
        assert all(not item.done() for item in futures), 'request missed the actual lock barrier'
        assert time.monotonic() < deadline, 'actual lock was not observed'
        time.sleep(.02)


def test_actual_same_nonce_concurrency_waits_job_before_intent_and_saves_once(client, monkeypatch):
    _, identity, job_id, version, _ = _failed(client, monkeypatch)
    body = _body(version); before = _counts()
    with Session(engine()) as blocker, ThreadPoolExecutor(max_workers=2) as pool:
        blocker.get(Job, job_id, with_for_update=True)
        a = pool.submit(client.post, _retry_path(identity), json=body)
        b = pool.submit(client.post, _retry_path(identity), json=body)
        try:
            _wait_lock([a, b], '%jobs%')
            _wait_lock([a, b], '%pg_advisory_xact_lock%')
        finally:
            blocker.rollback()
        first, second = ok(a.result(timeout=10)), ok(b.result(timeout=10))
    assert sorted([first['replayed'], second['replayed']]) == [False, True]
    assert first['retry'] == second['retry']
    assert _counts() == {**before, RECORD.__tablename__: before[RECORD.__tablename__] + 1,
        Audit.__tablename__: before[Audit.__tablename__] + 1}


def test_actual_actor_lock_race_blocks_fresh_retry_without_changing_original_job(client, monkeypatch):
    value, identity, job_id, version, _ = _failed(client, monkeypatch)
    body = _body(version); before = _counts()
    with Session(engine()) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        member = blocker.scalar(select(Membership).where(Membership.user_id == value['principal'].user_id,
            Membership.workspace_id == value['principal'].workspace_id).with_for_update())
        member.role = 'viewer'; blocker.flush()
        future = pool.submit(client.post, _retry_path(identity), json=body)
        try: _wait_lock([future], '%memberships%')
        finally: blocker.commit()
        response = future.result(timeout=10)
    assert response.status_code == 403, response.text
    assert _counts() == before
    assert ok(client.get(_request_path(identity, body['request_id']))) == {'found': False, 'receipt': None, 'legacy': None}
    with Session(engine()) as db:
        assert db.get(Job, job_id).status == 'failed' and db.get(Job, job_id).version == version
