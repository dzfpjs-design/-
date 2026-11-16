"""Pinned message-check recovery, including the old receipt-only contract.

The journey cases use the real issued synthetic V118 fixture, worker and PG
records. Only the explicitly identified cut-read fault is injected. The small
unit cases separately exercise historical DTOs and both target-pin boundaries;
their mocked reads do not claim to certify database authority or concurrency.
"""
import copy
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import workflow_graph_api as api
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Invocation, Job, Task
from generic_agents.worker import process_once
from tests.test_graph_wait_journey_v118 import (
    waiting_release, graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene,
    start, admit_observation, reply,
)


def _path(identity, *, legacy=False):
    return '/api/workspace/tasks/' + identity + '/graph/retry-messages' + ('' if legacy else '-v2')


def _body(task, reason='恢复指定的原消息检查，保留原窗口和输入'):
    error = task['graph_message_status']['error']
    return {'request_id': str(uuid4()), 'job_id': error['job_id'],
        'job_version': error['job_version'], 'reason': reason}


def _fail_poll(value, monkeypatch, task):
    from generic_agents import graph_wait_sources_v118 as sources
    identity = task['graph_wait_poll']['job_id']
    with Session(engine()) as db:
        available_at = db.get(Job, identity).available_at.timestamp()
    # Exercise the actual scheduled Job rather than manufacturing a failed row.
    time.sleep(max(0, available_at - time.time() + 0.06))
    with monkeypatch.context() as fault:
        def unavailable(db, p):
            raise HTTPException(409, '合成切点读取故障，原等待窗口未推进')
        fault.setattr(sources, 'take_cut', unavailable)
        assert process_once(identity, deliver=False)
    result = reply(value['client'].get('/api/workspace/tasks/' + task['id']))
    error = result['graph_message_status']['error']
    assert result['graph_message_status']['can_retry']
    assert error['job_id'] == identity and error['status'] == 'failed'
    return result


def _snapshot(identity):
    with Session(engine()) as db:
        task = db.get(Task, identity)
        jobs = db.scalars(select(Job).where(Job.workspace_id == task.workspace_id,
            Job.data['task_id'].as_string() == identity).order_by(Job.id)).all()
        return {'task': (task.version, copy.deepcopy(task.data)),
            'jobs': [(row.id, row.version, row.status, row.attempts, row.available_at,
                row.lease_token, row.lease_until, copy.deepcopy(row.data)) for row in jobs],
            'invocations': set(db.scalars(select(Invocation.id).where(
                Invocation.workspace_id == task.workspace_id))),
            'cloud_calls': set(db.scalars(select(CloudCall.id).where(
                CloudCall.workspace_id == task.workspace_id)))}


def test_legacy_message_dto_is_unchanged_and_v2_signs_required_job_identity():
    old = {'request_id': str(uuid4()), 'job_version': 4, 'reason': '恢复原检查'}
    assert set(api.MessageRetry.model_fields) == {'request_id', 'job_version', 'reason'}
    assert api.MessageRetry.model_validate(old).model_dump(mode='json') == old
    with pytest.raises(ValidationError):
        api.MessageRetry.model_validate({**old, 'job_id': str(uuid4())})
    with pytest.raises(ValidationError):
        api.MessageRetryV2.model_validate(old)
    new = {**old, 'job_id': str(uuid4())}
    dumped = api.MessageRetryV2.model_validate(new).model_dump(mode='json')
    assert dumped == new
    assert set(api.MessageRetryV2.model_json_schema()['required']) == set(new)
    assert digest({'actor_id': 'original-actor', 'body': dumped}) != digest({
        'actor_id': 'original-actor', 'body': {**dumped, 'job_id': str(uuid4())}})
    assert digest({'actor_id': 'original-actor', 'body': dumped}) != digest({
        'actor_id': 'original-actor', 'body': old})


class _NoDB:
    def __getattr__(self, name):
        raise AssertionError('historical/unit branch unexpectedly performed DB I/O: ' + name)


def test_unit_old_nonce_can_be_replayed_and_read_after_poll_changed_without_new_execution(monkeypatch):
    p = SimpleNamespace(user_id='original-actor', role='admin')
    body = api.MessageRetry(request_id=uuid4(), job_version=4, reason='历史原检查')
    old_job = str(uuid4())
    receipt = {'actor_id': p.user_id,
        'body_hash': digest({'actor_id': p.user_id, 'body': body.model_dump(mode='json')}),
        'job_id': old_job}
    task = SimpleNamespace(data={'graph_runtime': {'commands': []},
        'graph_wait_retries': {str(body.request_id): copy.deepcopy(receipt)},
        'graph_wait_poll': {'job_id': str(uuid4())}})
    before = copy.deepcopy(task.data)
    monkeypatch.setattr(api, 'context', lambda *args: (task, None, None, None))
    monkeypatch.setattr(api.runtime, 'public_view', lambda *args: copy.deepcopy(task.data))
    assert api.retry_messages('task', body, p, _NoDB()) == before
    recovered = api.request('task', body.request_id, p, _NoDB())
    assert recovered['found'] and recovered['request'] == receipt
    assert task.data == before
    with pytest.raises(HTTPException) as conflict:
        api.retry_messages('task', body.model_copy(update={'reason': '替换历史内容'}), p, _NoDB())
    assert conflict.value.status_code == 409 and task.data == before
    new = api.MessageRetryV2(**body.model_dump(), job_id=UUID(old_job))
    with pytest.raises(HTTPException) as downgrade:
        api.retry_messages_v2('task', new, p, _NoDB())
    assert downgrade.value.status_code == 409 and task.data == before


@pytest.mark.parametrize('changed_at', ['before_job_lock', 'after_job_lock'])
def test_unit_v2_pins_target_before_and_after_job_task_locks(monkeypatch, changed_at):
    from generic_agents import relations
    old_job, next_job = str(uuid4()), str(uuid4())
    p = SimpleNamespace(user_id='original-actor', role='admin')
    body = api.MessageRetryV2(request_id=uuid4(), job_id=UUID(old_job), job_version=4, reason='原检查')
    def task(job):
        return SimpleNamespace(data={'graph_runtime': {'commands': []},
            'graph_wait_poll': {'job_id': job}}, owner_id=p.user_id)
    first = task(next_job if changed_at == 'before_job_lock' else old_job)
    locked = task(next_job)
    snapshots = [copy.deepcopy(first.data), copy.deepcopy(locked.data)]
    locks = []
    monkeypatch.setattr(api, 'context', lambda db, actor, identity, lock=False:
        (locked if lock else first, None, None, None))
    def scoped(db, model, identity, actor, lock=False):
        assert model is Job and lock is True
        locks.append(identity)
        return SimpleNamespace(id=identity, version=4, status='failed')
    monkeypatch.setattr(api, 'scoped', scoped)
    monkeypatch.setattr(relations, 'lock_current_actor', lambda *args: None)
    with pytest.raises(HTTPException) as conflict:
        api.retry_messages_v2('task', body, p, _NoDB())
    assert conflict.value.status_code == 409
    assert locks == ([] if changed_at == 'before_job_lock' else [old_job])
    assert [first.data, locked.data] == snapshots


def test_unit_v2_replay_checks_receipt_job_independently_of_full_body_hash(monkeypatch):
    p = SimpleNamespace(user_id='original-actor', role='admin')
    body = api.MessageRetryV2(request_id=uuid4(), job_id=uuid4(), job_version=4, reason='原检查')
    task = SimpleNamespace(data={'graph_runtime': {'commands': []}, 'graph_wait_retries': {
        str(body.request_id): {'actor_id': p.user_id,
            'body_hash': digest({'actor_id': p.user_id, 'body': body.model_dump(mode='json')}),
            'job_id': str(uuid4())}}})
    before = copy.deepcopy(task.data)
    monkeypatch.setattr(api, 'context', lambda *args: (task, None, None, None))
    with pytest.raises(HTTPException) as conflict:
        api.retry_messages_v2('task', body, p, _NoDB())
    assert conflict.value.status_code == 409 and task.data == before


def test_actual_v2_recovers_same_job_and_nonce_while_new_v1_requests_are_closed(waiting_release, monkeypatch):
    value = waiting_release; client = value['client']; task = start(value)
    receipt_id, _ = admit_observation(value)
    task = _fail_poll(value, monkeypatch, task)
    body = _body(task); identity = body['job_id']; before = _snapshot(task['id'])
    old = {key: value for key, value in body.items() if key != 'job_id'}
    response = client.post(_path(task['id'], legacy=True), json=old)
    assert response.status_code == 409 and '带原检查标识' in response.json()['detail']
    assert _snapshot(task['id']) == before
    assert client.post(_path(task['id']), json=old).status_code == 422
    assert client.post(_path(task['id'], legacy=True), json=body).status_code == 422
    occupied = task['graph_runtime']['commands'][0]['request_id']
    assert client.post(_path(task['id']), json={**body, 'request_id': occupied}).status_code == 409
    assert _snapshot(task['id']) == before

    accepted = reply(client.post(_path(task['id']), json=body))
    expected = {'actor_id': accepted['graph_wait_retries'][body['request_id']]['actor_id'],
        'body_hash': digest({'actor_id': accepted['graph_wait_retries'][body['request_id']]['actor_id'], 'body': body}),
        'job_id': identity}
    assert accepted['graph_wait_retries'][body['request_id']] == expected
    assert accepted['graph_wait_poll']['job_id'] == identity
    assert accepted['graph_wait_authority'] == task['graph_wait_authority']
    assert accepted['graph_runtime'] == task['graph_runtime']
    with Session(engine()) as db:
        job = db.get(Job, identity)
        assert job.status == 'queued' and job.attempts == 1
        assert job.version == body['job_version'] + 1
    queued = _snapshot(task['id'])
    assert reply(client.post(_path(task['id']), json=body))['graph_runtime'] == accepted['graph_runtime']
    assert _snapshot(task['id']) == queued
    recovered = reply(client.get('/api/workspace/tasks/' + task['id'] + '/graph/requests/' + body['request_id']))
    assert recovered['found'] and recovered['request'] == expected
    for changed in ({**body, 'job_id': str(uuid4())}, {**body, 'reason': '不能更换原因'}):
        assert client.post(_path(task['id']), json=changed).status_code == 409
    assert client.post(_path(task['id'], legacy=True), json=old).status_code == 409
    assert _snapshot(task['id']) == queued
    assert process_once(identity, deliver=False)
    result = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert result['graph_message_status']['error'] is None
    assert next(iter(result['graph_wait_authority']['windows'].values()))['event_id'] == receipt_id
    with Session(engine()) as db:
        assert db.get(Job, identity).status == 'succeeded'
        assert db.get(Job, identity).attempts == 2
    after = _snapshot(task['id'])
    assert after['invocations'] == before['invocations']
    assert after['cloud_calls'] == before['cloud_calls']


def test_actual_equal_version_replacement_job_is_rejected_but_saved_old_nonce_still_recovers(waiting_release, monkeypatch):
    value = waiting_release; client = value['client']
    first = _fail_poll(value, monkeypatch, start(value))
    stale = _body(first, '原页面只确认这个检查')
    accepted_body = {**stale, 'request_id': str(uuid4()), 'reason': '另一明确申请恢复原检查'}
    accepted = reply(client.post(_path(first['id']), json=accepted_body))
    assert process_once(stale['job_id'], deliver=False)
    advanced = reply(client.get('/api/workspace/tasks/' + first['id']))
    assert advanced['graph_wait_poll']['job_id'] != stale['job_id']
    current = _fail_poll(value, monkeypatch, advanced)
    error = current['graph_message_status']['error']
    assert error['job_version'] == stale['job_version'], 'actual distinct Jobs share this CAS version'
    assert error['job_id'] != stale['job_id']
    before = _snapshot(first['id'])
    response = client.post(_path(first['id']), json=stale)
    assert response.status_code == 409 and '已变化' in response.json()['detail']
    assert _snapshot(first['id']) == before
    assert stale['request_id'] not in current['graph_wait_retries']
    replayed = reply(client.post(_path(first['id']), json=accepted_body))
    assert replayed['graph_wait_retries'][accepted_body['request_id']] == accepted['graph_wait_retries'][accepted_body['request_id']]
    assert replayed['graph_wait_poll']['job_id'] == error['job_id']
    recovered = reply(client.get('/api/workspace/tasks/' + first['id'] + '/graph/requests/' + accepted_body['request_id']))
    assert recovered['found'] and recovered['request']['job_id'] == stale['job_id']
    assert client.post(_path(first['id']), json={**accepted_body, 'job_id': error['job_id']}).status_code == 409
    assert _snapshot(first['id']) == before
