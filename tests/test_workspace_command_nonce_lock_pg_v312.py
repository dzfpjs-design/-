"""Real isolated PG workspace nonce lock and original-route serialization.

Actual account/FK rows, two connections, advisory locks, visibility, rollback
and protected Audit storage are unmocked. Task/advice/continuation IDs and the
minimal Audit payload are deliberate route fixtures, NOT valid paid-source,
approval, business command or tool execution proof. No model/HTTP runs.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import workspace_command_nonce_lock_v312 as n
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, User
from generic_agents.security import Principal

CREATE = 'same_goal_continuation.saved_v312'
PREPARE = 'same_goal_advice.preparation_accepted_v302'


@pytest.fixture(autouse=True)
def disposable_pg_only(disposable_database):
    assert engine().dialect.name == 'postgresql' and engine().url.database.startswith('ga_test_')


@pytest.fixture
def scene(account):
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'nonce-lock-storage-proof-only')
    return {'p': p, 'first_task': str(uuid4()), 'second_task': str(uuid4()),
        'advice_id': str(uuid4()), 'subject_id': str(uuid4()),
        'body': {'request_id': str(uuid4()), 'reason': '同一个完整原申请😀\n不重发'}}


def original(s, task_id, action):
    return Audit(id=str(uuid4()), workspace_id=s['p'].workspace_id, actor_id=s['p'].user_id,
        action=action, object_id=s['subject_id'], data={'task_id': task_id,
            'advice_plan_id': s['advice_id'], 'request_id': s['body']['request_id'], 'request': copy.deepcopy(s['body'])})


def wait_for_real_waiter(db, pid, future):
    end = monotonic() + 3
    while monotonic() < end:
        waiting = db.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND locktype='advisory' AND NOT granted)"), {'pid': pid})
        if waiting: return
        assert not future.done(), 'the second connection bypassed the held workspace nonce lock'
        sleep(0.01)
    pytest.fail('the second connection did not enter a real advisory-lock wait')


@pytest.mark.parametrize('second_action', [CREATE, PREPARE, 'same_goal_continuation.grant_issued_v313'])
@pytest.mark.parametrize('commit_first', [True, False])
def test_real_two_task_same_nonce_serializes_and_preserves_exact_original_or_rollback(scene, second_action, commit_first):
    s = scene;entered = Event();second_pid = []
    with Session(engine(), expire_on_commit=False) as first:
        assert first.connection().get_isolation_level() == 'READ COMMITTED'
        n.acquire(first, s['p'], action=CREATE, task_id=s['first_task'], body=s['body'], advice_id=s['advice_id'])
        event = original(s, s['first_task'], CREATE);before = copy.deepcopy(event.data)
        first.add(event);first.flush()

        def second_writer():
            with Session(engine(), expire_on_commit=False) as db:
                assert db.connection().get_isolation_level() == 'READ COMMITTED'
                db.execute(text("SET LOCAL lock_timeout='5000ms'"))
                second_pid.append(db.scalar(text('SELECT pg_backend_pid()')));entered.set()
                try:
                    n.acquire(db, s['p'], action=second_action, task_id=s['second_task'], body=s['body'],
                        **({'advice_id': s['advice_id']} if second_action == CREATE else {}))
                    row = original(s, s['second_task'], second_action);db.add(row);db.commit()
                    return {'status': 'saved', 'id': row.id}
                except HTTPException as error:
                    db.rollback();return {'status': error.status_code}

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(second_writer)
            try:
                assert entered.wait(3);wait_for_real_waiter(first, second_pid[0], future)
                assert not future.done()
                if commit_first: first.commit()
                else: first.rollback()
                result = future.result(timeout=6)
            finally:
                # Every failure path releases the first transaction so a real
                # failed assertion cannot strand a waiting connection.
                first.rollback()
        assert result['status'] == (409 if commit_first else 'saved')
    with Session(engine()) as db:
        rows = list(db.scalars(select(Audit).where(Audit.workspace_id == s['p'].workspace_id,
            Audit.action.in_(n.ACTIONS), Audit.data['request_id'].as_string() == s['body']['request_id'])))
        assert len(rows) == 1
        if commit_first:
            assert rows[0].id == event.id and rows[0].data == before and digest(rows[0].data) == digest(before)
            # The same original route remains readable after the first commit;
            # this guard alone does not authenticate a business replay result.
            n.acquire(db, s['p'], action=CREATE, task_id=s['first_task'], body=s['body'], advice_id=s['advice_id'])
            assert db.get(Audit, event.id).data == before
        else:
            assert rows[0].id == result['id'] and rows[0].data['task_id'] == s['second_task']
        db.rollback()
