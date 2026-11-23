"""Long, synthetic end-to-end prerequisite followed by real policy lock races.

This test uses the full matured manual Goal fixture, published capabilities,
actual membership and PostgreSQL transactions. It never contacts a real model,
modifies a prior Audit, or tests the unfinished v2 sender/UI. Two sessions
contend on the real Task row; pg_blocking_pids is the blocking evidence.
"""
from datetime import timedelta
from queue import Queue
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_contracts_v302 as c
from generic_agents import same_goal_advice_quota_v304 as v1
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents import same_goal_advice_unlimited_policy_v2 as v2
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Task
from generic_agents.security import Principal
from tests.test_authored_service_workflow_pg_v270 import publish
from tests.test_same_goal_advice_manual_append_pg import (
    matured_manual_case, deepseek_3600_publication, managed, scene, reject_non_loopback_network,
)


def _server_time():
    with Session(engine()) as db:
        return db.scalar(select(func.clock_timestamp()))


def _body(case, *, legacy, expires):
    limits = {'max_tokens': 512, 'max_request_bytes': 50000, 'max_evidence_items': 2}
    if legacy:
        limits.update(max_cost_minor_units=100, currency='CNY')
    return {'request_id': str(uuid4()), 'expected_goal_hash': case.goal_hash,
        'expected_mature_head_hash': case.head['head_hash'], 'limits': limits,
        'expires_at': expires.isoformat(), 'reason': '隔离软件数据政策互斥验证'}


def _wait_until_expired(expiry):
    # Real DB time only: never rewrite or delete a historical policy Audit.
    deadline = monotonic() + 140
    while _server_time() <= expiry:
        assert monotonic() < deadline, '隔离政策未在预期时间自然到期'
        sleep(.2)


def _race(case, principal, *, first_version):
    expiry = _server_time() + timedelta(seconds=90)
    first_api, second_api = (v1, v2) if first_version == 'v1' else (v2, v1)
    first_body = _body(case, legacy=first_api is v1, expires=expiry)
    second_body = _body(case, legacy=second_api is v1, expires=expiry)
    signals = Queue(); finished = Event(); result = {}
    first = Session(engine(), expire_on_commit=False)
    contender = None
    try:
        first_pid = first.scalar(select(func.pg_backend_pid()))
        accepted = first_api.authorize(first, principal, case.task_id, first_body)
        assert accepted['cloud_send_authorized'] is False

        def second_transaction():
            with Session(engine(), expire_on_commit=False) as db:
                try:
                    db.execute(text("SET LOCAL statement_timeout = '45000ms'"))
                    signals.put(db.scalar(select(func.pg_backend_pid())))
                    result['value'] = second_api.authorize(db, principal, case.task_id, second_body)
                    db.commit()
                except BaseException as exc:
                    db.rollback(); result['error'] = exc
                finally:
                    finished.set()

        contender = Thread(target=second_transaction, daemon=True); contender.start()
        second_pid = signals.get(timeout=10)
        deadline = monotonic() + 10
        while True:
            with Session(engine()) as observer:
                blocked_by = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': second_pid})
            if first_pid in blocked_by:
                break
            assert not finished.is_set(), '第二个授权在真实行锁竞争前已经结束'
            assert monotonic() < deadline, '未能证明第二个事务等待原 Task 行锁'
            sleep(.05)
        assert _server_time() < expiry, '原政策在释放行锁前已过期，不能证明互斥'
        first.commit()
        assert finished.wait(20), '第二事务释放 Task 行锁后没有结束'
        contender.join(timeout=1)
        error = result.get('error')
        assert isinstance(error, HTTPException) and error.status_code == 409
        assert '尚未到期' in str(error.detail)
        assert 'value' not in result
        return accepted, first_body, expiry
    finally:
        first.rollback(); first.close()
        if contender is not None and contender.is_alive():
            contender.join(timeout=50)


def test_synthetic_deepseek_derivative_registers_before_long_race(deepseek_3600_publication):
    """Cheap real-registry preflight; it does not prove Goal or authorization."""
    source = deepseek_3600_publication.pins['reasoning']
    pin = {'id': source['id'], 'content_hash': source['content_hash'], 'release_version': source['release_version']}
    registered = publish(deepseek_3600_publication.scene.client, c.build_manifest(source['data'], pin))
    assert registered['status'] == 'published'
    assert registered['data']['specification']['source_business_capability_ref'] == pin


def test_real_mature_goal_v1_v2_policy_serialize_in_both_orders(matured_manual_case):
    case = matured_manual_case
    publication = case.publication
    source = publication.pins['reasoning']
    pin = {'id': source['id'], 'content_hash': source['content_hash'], 'release_version': source['release_version']}
    registered = publish(case.client, c.build_manifest(source['data'], pin))
    assert registered['status'] == 'published'

    with Session(engine()) as db:
        # Use the real active browser login's session, not a fabricated IAM
        # decision or test substitute for lock_current_actor.
        task = db.get(Task, case.task_id)
        assert task is not None and task.workspace_id == case.workspace_id
        case.goal_hash = task.data['goal_hash']
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == case.principal.user_id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        assert session is not None
        principal = Principal(case.principal.user_id, case.workspace_id, 'admin', session.id)
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == case.workspace_id)))

    # First v1 wins without any v2 policy: proves ordinary v1 issuance still
    # works on the real matured Goal. v2 then must see the committed conflict.
    first, first_body, expiry = _race(case, principal, first_version='v1')
    assert first['limits']['provider'] == 'deepseek'
    with Session(engine()) as db:
        actions = list(db.scalars(select(Audit.action).where(Audit.workspace_id == case.workspace_id,
            Audit.object_id == case.task_id, Audit.action.in_([store.QUOTA_POLICY, v1.AUTHORIZATION,
                v2.POLICY, v2.AUTHORIZATION]))))
        assert sorted(actions) == sorted([store.QUOTA_POLICY, v1.AUTHORIZATION])
    _wait_until_expired(expiry)

    # A fresh request uses the same matured source; reversal proves a genuine
    # v2 policy also blocks a competing new v1 authorization.
    second, second_body, _expiry = _race(case, principal, first_version='v2')
    assert second['limits']['provider'] == 'deepseek'
    assert second['limits']['cost_policy'] == 'unlimited'
    assert second['limits']['allocation_basis'] == 'per_plan'
    with Session(engine()) as db:
        actions = list(db.scalars(select(Audit.action).where(Audit.workspace_id == case.workspace_id,
            Audit.object_id == case.task_id, Audit.action.in_([store.QUOTA_POLICY, v1.AUTHORIZATION,
                v2.POLICY, v2.AUTHORIZATION]))))
        assert sorted(actions) == sorted([store.QUOTA_POLICY, v1.AUTHORIZATION, v2.POLICY, v2.AUTHORIZATION])
        assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == case.workspace_id)))
        # Replaying either original returns its original receipt; never renews
        # the old expiry or silently creates a third policy.
        assert v1.authorize(db, principal, case.task_id, first_body) == first
        assert v2.authorize(db, principal, case.task_id, second_body) == second
