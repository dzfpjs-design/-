"""Storage-level candidate tests, not proof of the real authorization chain.

The rows below are deliberately synthetic; real v310 anchors, live API
integration, legacy deployments and model calls are outside this test.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import workspace_advice_nonce_candidate_v2 as candidate
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, User
from generic_agents.security import Principal
from tests.test_workspace_command_nonce_lock_pg_v312 import wait_for_real_waiter


@pytest.fixture(autouse=True)
def disposable_pg_only(disposable_database):
    assert engine().dialect.name == 'postgresql' and engine().url.database.startswith('ga_test_')


@pytest.fixture
def scene(account):
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        principal = Principal(user.id, account[2], 'admin', 'candidate-test-only')
    return {'principal': principal, 'first': str(uuid4()), 'second': str(uuid4()),
        'body': {'request_id': str(uuid4()), 'reason': 'synthetic original'}}


def receipt(scene, action, task):
    body = copy.deepcopy(scene['body'])
    return Audit(id=str(uuid4()), workspace_id=scene['principal'].workspace_id,
        actor_id=scene['principal'].user_id, object_id=task, action=action,
        data={'task_id': task, 'request_id': body['request_id'], 'body': body, 'body_hash': digest(body)})


@pytest.mark.parametrize('first_action,second_action', [
    (candidate.V1_POLICY_REQUEST, candidate.V2_POLICY_REQUEST),
    (candidate.V2_POLICY_REQUEST, candidate.V1_POLICY_REQUEST),
    (candidate.V1_POLICY_REQUEST, 'same_goal_advice.preparation_accepted_v302'),
    ('same_goal_advice.preparation_accepted_v302', candidate.V2_POLICY_REQUEST),
])
@pytest.mark.parametrize('commit_first', [True, False])
def test_two_transactions_wait_before_different_tasks(scene, first_action, second_action, commit_first):
    s = scene; started = Event(); pids = []
    with Session(engine(), expire_on_commit=False) as first:
        candidate.acquire(first, s['principal'], action=first_action, task_id=s['first'], body=s['body'])
        original = receipt(s, first_action, s['first']); first.add(original); first.flush()

        def run_other():
            with Session(engine(), expire_on_commit=False) as db:
                db.execute(text("SET LOCAL lock_timeout='5000ms'"))
                pids.append(db.scalar(text('SELECT pg_backend_pid()'))); started.set()
                try:
                    candidate.acquire(db, s['principal'], action=second_action, task_id=s['second'], body=s['body'])
                    row = receipt(s, second_action, s['second']); db.add(row); db.commit()
                    return ('saved', row.id)
                except HTTPException as exc:
                    db.rollback(); return (exc.status_code, None)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_other)
            try:
                assert started.wait(3); wait_for_real_waiter(first, pids[0], future)
                if commit_first: first.commit()
                else: first.rollback()
                outcome, row_id = future.result(timeout=6)
            finally:
                first.rollback()
        assert outcome == (409 if commit_first else 'saved')
    with Session(engine()) as db:
        rows = list(db.scalars(select(Audit).where(Audit.workspace_id == s['principal'].workspace_id,
            Audit.data['request_id'].as_string() == s['body']['request_id'])))
        assert len(rows) == 1 and rows[0].id == (original.id if commit_first else row_id)


@pytest.mark.parametrize('action', [candidate.V1_POLICY_REQUEST, candidate.V2_POLICY_REQUEST])
def test_policy_storage_replay_and_modified_body_rejection(scene, action):
    s = scene
    with Session(engine()) as db:
        original = receipt(s, action, s['first']); db.add(original); db.commit()
    with Session(engine()) as db:
        candidate.acquire(db, s['principal'], action=action, task_id=s['first'], body=s['body'])
        with pytest.raises(HTTPException):
            candidate.acquire(db, s['principal'], action=action, task_id=s['first'],
                body={**s['body'], 'reason': 'changed'})
        db.rollback()


def test_unsupported_price_chain_cannot_be_reused(scene):
    s = scene
    with Session(engine()) as db:
        db.add(receipt(s, candidate.PRICE_BASIS, s['first'])); db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            candidate.acquire(db, s['principal'], action=candidate.V2_POLICY_REQUEST,
                task_id=s['second'], body=s['body'])


def test_new_price_wrapper_may_take_the_nonce_before_its_task_lock(scene):
    s = scene
    with Session(engine()) as db:
        candidate.acquire(db, s['principal'], action=candidate.PRICE_WRAPPER,
            task_id=s['first'], subject_id=s['first'], body=s['body'])
        db.rollback()


def test_incomplete_quota_wrapper_never_replays_as_a_complete_request(scene):
    s = scene
    with Session(engine()) as db:
        db.add(receipt(s, candidate.QUOTA_WRAPPER, s['first'])); db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            candidate.acquire(db, s['principal'], action=candidate.QUOTA_WRAPPER,
                task_id=s['first'], subject_id=s['first'], body=s['body'])
