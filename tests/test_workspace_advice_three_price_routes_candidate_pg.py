"""Test-only hook wiring for real ASGI price routes over a mature Goal.

The application source is not modified; test-only hooks enforce a candidate
nonce before real route logic. Synthetic local observations and fixed HTML
are used, not a field device, real price endpoint or cloud model.
"""
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from threading import Event, local
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_price_v307 as prices
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents import sensor_native_analysis_api as sensor_api
from generic_agents import workspace_advice_nonce_candidate_v2 as candidate
from generic_agents import workspace_command_nonce_lock_v312 as legacy
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Evidence
from tests.conftest import application
from tests.same_goal_advice_aligned_publication_fixture import deepseek_3600_publication
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network
from tests.test_same_goal_advice_manual_append_pg import matured_manual_case, managed, scene
from tests.test_same_goal_advice_price_v307 import html


def _blocked_by(owner, waiter, future):
    deadline = monotonic() + 8
    while monotonic() < deadline:
        with Session(engine()) as db:
            blockers = db.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiter})
        if owner in blockers: return
        assert not future.done(), 'second request finished without waiting for the first transaction'
        sleep(.02)
    raise AssertionError('no direct PostgreSQL blocking relationship observed')


def test_real_three_price_routes_commit_and_rollback_under_candidate_hooks(matured_manual_case, monkeypatch):
    case = matured_manual_case; model = case.publication.model
    model_pin = {key: model[key] for key in ('id', 'content_hash', 'release_version')}
    included = next(route for route in application.router.routes
        if getattr(route, 'original_router', None) is sensor_api.router)
    sensor_route = next(route for route in included.effective_candidates()
        if getattr(route, 'path', None) == '/api/sensor-native/tasks/{task_id}/analyses/price')
    actual_sensor = sensor_route.dependant.call
    actual_authorize = prices.authorize
    paths = {'workspace': '/api/workspace/business-advice/price-bases',
        'v310': '/api/workspace/tasks/' + case.task_id + '/business-advice/price-options',
        'sensor': '/api/sensor-native/tasks/' + case.task_id + '/analyses/price'}
    # v310 failure -> workspace; v310 <-> sensor in both commit/failure modes.
    scenarios = [('v310', 'workspace', True), ('v310', 'sensor', False),
        ('v310', 'sensor', True), ('sensor', 'v310', False), ('sensor', 'v310', True)]
    for first_kind, second_kind, fail_first in scenarios:
        request_id, reason = str(uuid4()), '隔离真实三入口编号竞争'
        regular_body = {'request_id': request_id, 'model_pin': model_pin,
            'validity_seconds': 3600, 'reason': reason}
        sensor_body = {'request_id': request_id, 'model_id': model_pin['id'], 'reason': reason}
        submitted = lambda kind: sensor_body if kind == 'sensor' else regular_body
        fetched = Event(); release = Event(); second_entered = Event()
        owner_pids = []; waiter_pids = []; fetches = []; failed = []; rolled_back_ids = []
        thread_state = local()
        def guarded_command(db, principal, **kwargs):
            pid = db.scalar(text('SELECT pg_backend_pid()'))
            if fetched.is_set(): waiter_pids.append(pid); second_entered.set()
            return candidate.acquire(db, principal, **kwargs)
        def guarded_authorize(db, principal, body):
            pid = db.scalar(text('SELECT pg_backend_pid()')); thread_state.price_pid = pid
            if fetched.is_set() and second_kind == 'workspace': waiter_pids.append(pid); second_entered.set()
            candidate.acquire(db, principal, action=candidate.PRICE_BASIS, task_id=None, body=body)
            result = actual_authorize(db, principal, body)
            if fail_first and not failed:
                row = db.get(Audit, result['basis_ref']['id']); value = row.data
                rolled_back_ids.append((row.id, value['official_evidence_ref']['evidence_id'],
                    value['upload_audit_ref']['id']))
                failed.append(True)
                raise HTTPException(503, '隔离测试：首次价格保存后、提交前失败')
            return result
        def held_html():
            fetches.append(1)
            if not fetched.is_set(): owner_pids.append(thread_state.price_pid); fetched.set()
            assert release.wait(20)
            return html(), 'text/html; charset=utf-8'
        @wraps(actual_sensor)
        def sensor_prelock(*args, **kwargs):
            db, principal, body = kwargs['db'], kwargs['p'], kwargs['body']
            assert db.scalar(text("SELECT current_setting('transaction_isolation')")) == 'read committed'
            pid = db.scalar(text('SELECT pg_backend_pid()'))
            if fetched.is_set(): waiter_pids.append(pid); second_entered.set()
            db.execute(text(legacy.LOCK_SQL), {'workspace_command_nonce_key':
                legacy.key(principal.workspace_id, str(body.request_id))})
            return actual_sensor(*args, **kwargs)
        with monkeypatch.context() as isolated:
            isolated.setattr(store, 'command_nonce', SimpleNamespace(acquire=guarded_command))
            isolated.setattr(prices, 'authorize', guarded_authorize)
            isolated.setattr(prices, '_fetch', held_html)
            isolated.setattr(sensor_route.dependant, 'call', sensor_prelock)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(case.client.post, paths[first_kind], json=submitted(first_kind))
                try:
                    assert fetched.wait(30)
                    second = pool.submit(case.client.post, paths[second_kind], json=submitted(second_kind))
                    assert second_entered.wait(15)
                    _blocked_by(owner_pids[0], waiter_pids[0], second)
                finally:
                    release.set()
                a, b = first.result(timeout=30), second.result(timeout=30)
            assert a.status_code == (503 if fail_first else (201 if first_kind != 'sensor' else 200)), a.text
            expected_second = 409 if (first_kind == 'sensor' and second_kind == 'v310' and not fail_first) else (
                200 if second_kind == 'sensor' else 201)
            assert b.status_code == expected_second, b.text
            assert len(fetches) == (2 if fail_first else 1)
        with Session(engine()) as db:
            rows = list(db.scalars(select(Audit).where(Audit.workspace_id == case.workspace_id,
                Audit.action.in_([candidate.PRICE_BASIS, candidate.PRICE_WRAPPER]),
                Audit.data['request_id'].as_string() == request_id)))
            bases = [row for row in rows if row.action == candidate.PRICE_BASIS]
            wrappers = [row for row in rows if row.action == candidate.PRICE_WRAPPER]
            assert len(bases) == 1
            assert len(wrappers) == (1 if (first_kind == 'v310' and not fail_first) or
                (second_kind == 'v310' and fail_first) else 0)
            ref = bases[0].data
            assert db.get(Evidence, ref['official_evidence_ref']['evidence_id']) is not None
            assert db.get(Audit, ref['upload_audit_ref']['id']) is not None
            if fail_first:
                assert len(rolled_back_ids) == 1
                old_price, old_evidence, old_upload = rolled_back_ids[0]
                assert db.get(Audit, old_price) is None and db.get(Evidence, old_evidence) is None
                assert db.get(Audit, old_upload) is None
            assert not list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == case.workspace_id)))
