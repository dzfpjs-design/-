"""Real original worker recovery after one explicitly injected cut-read failure."""
import copy
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Job, Invocation
from generic_agents.worker import process_once
from tests.test_graph_wait_journey_v118 import (
    waiting_release, graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene,
    start, admit_observation, reply,
)


def test_failed_poll_is_visible_and_same_job_nonce_recovers_without_reprocessing(waiting_release, monkeypatch):
    from generic_agents import graph_wait_sources_v118 as sources
    value = waiting_release; client = value['client']; task = start(value)
    receipt, _ = admit_observation(value)
    before = copy.deepcopy(task['graph_wait_authority']); identity = task['graph_wait_poll']['job_id']
    with Session(engine()) as db:
        from sqlalchemy import select
        invocations = set(db.scalars(select(Invocation.id)))
    with monkeypatch.context() as fault:
        def unavailable(db, p): raise HTTPException(409, '合成数据库切点读取故障，未推进窗口')
        fault.setattr(sources, 'take_cut', unavailable)
        assert process_once(identity, deliver=False)
    from generic_agents import workflow_graph as runtime
    from generic_agents.models import Task
    from generic_agents.security import Principal
    with Session(engine()) as db:
        row = db.get(Task, task['id']); timer = db.get(Job, next(iter(task['graph_timers'].values())))
        runtime.fail_job(db, Principal(row.owner_id, row.workspace_id, 'viewer', 'worker-recovery'),
            timer, '合成期限检查错误，不能覆盖原消息检查恢复入口')
        db.commit()
    task = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert task['graph_wait_authority'] == before
    message = task['graph_message_status']
    assert message['can_retry'] and message['error']['job_id'] == identity
    assert message['error']['status'] == 'failed'
    body = {'request_id': str(uuid4()), 'job_id': identity, 'job_version': message['error']['job_version'],
        'reason': '恢复原消息检查；保留原窗口、期限与处理回执'}
    path = '/api/workspace/tasks/' + task['id'] + '/graph/retry-messages-v2'
    occupied = task['graph_runtime']['commands'][0]['request_id']
    assert client.post(path, json={**body, 'request_id': occupied}).status_code == 409
    accepted = reply(client.post(path, json=body))
    assert not accepted['graph_message_status']['can_retry']
    assert reply(client.post(path, json=body))['graph_runtime'] == accepted['graph_runtime']
    recovered = reply(client.get('/api/workspace/tasks/' + task['id'] + '/graph/requests/' + body['request_id']))
    assert recovered['found'] and recovered['request']['job_id'] == identity
    conflict = {**body, 'reason': '同请求不能换内容'}
    assert client.post(path, json=conflict).status_code == 409
    assert process_once(identity, deliver=False)
    result = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert result['graph_message_status']['error'] is None
    assert next(iter(result['graph_wait_authority']['windows'].values()))['event_id'] == receipt
    with Session(engine()) as db:
        assert db.get(Job, identity).status == 'succeeded'
        assert db.get(Job, identity).attempts == 2
        assert set(db.scalars(select(Invocation.id))) == invocations


def test_late_failed_poll_does_not_overwrite_new_frontier_or_cancelled_task(waiting_release):
    from generic_agents import workflow_graph as runtime
    from generic_agents.security import Principal
    from generic_agents.models import Task
    value = waiting_release; client = value['client']; task = start(value)
    old_poll = task['graph_wait_poll']['job_id']; admit_observation(value)
    assert process_once(old_poll, deliver=False)
    result = reply(client.get('/api/workspace/tasks/' + task['id']))
    with Session(engine()) as db:
        row = db.get(Task, task['id']); job = db.get(Job, old_poll)
        p = Principal(row.owner_id, row.workspace_id, 'viewer', 'worker-recovery')
        # A late diagnostic belongs to the old poll. Its window was consumed;
        # retaining this error would misrepresent the new human frontier.
        runtime.fail_job(db, p, job, '合成迟到错误'); db.commit()
    result = reply(client.get('/api/workspace/tasks/' + task['id']))
    assert result['graph_message_status']['error'] is None
