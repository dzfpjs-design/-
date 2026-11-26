"""Unlimited DeepSeek requests retain custody while generic Goal budgets stay enforced.

The long test uses the real synthetic approval/source checkpoint and a single
local mock provider. Only the already-consumed generic counters are substituted;
the Goal and its historical records are never edited.
"""
import os
import json
import re
import tempfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import goal_queries, native_goals
from generic_agents import goal_budget_versioned_snapshot_candidate_v3 as capacity
from generic_agents import same_goal_advice_quota_v304 as legacy
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from tests.test_advice_v2_real_fresh_preview_candidate_pg import (
    test_real_mature_approved_source_to_durable_v2_preview as mature_flow,
)
from tests import test_advice_v2_real_fresh_preview_candidate_pg as source


@pytest.fixture
def independent_evidence_dir(monkeypatch):
    # Each checkpoint restore requires an empty, private, dedicated directory.
    # Never clear the shared session fixture or a non-test evidence location.
    with tempfile.TemporaryDirectory(prefix='ga-test-evidence-') as directory:
        os.chmod(directory, 0o700)
        monkeypatch.setattr(source.settings(), 'storage_dir', Path(directory))
        yield


@pytest.mark.parametrize(('calls', 'steps'), [(12, 0), (0, 12)])
def test_generic_legacy_policy_keeps_its_existing_goal_limits(monkeypatch, calls, steps):
    monkeypatch.setattr(legacy, '_configuration_base', lambda *_: {
        'goal': {'spec': {'max_tool_calls': 12, 'max_steps': 12}},
    })
    monkeypatch.setattr(goal_queries, 'call_count', lambda *_: calls)
    monkeypatch.setattr(native_goals, 'total_steps', lambda *_: steps)
    with pytest.raises(HTTPException) as error:
        legacy._configuration(None, None, None, None)
    assert error.value.status_code == 409


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_FRESH_CHECKPOINT'), reason='isolated synthetic checkpoint only')
def test_mature_policy_ignores_capacity_but_rejects_unverifiable_inventory(monkeypatch, independent_evidence_dir):
    monkeypatch.setattr(source.settings(), 'cloud_allowed_providers', '')
    task_id = source.restore_to_disposable_test_db(os.environ['GA_SYNTHETIC_FRESH_CHECKPOINT'])
    login = json.loads((source.settings().storage_dir / '__synthetic_checkpoint_login__.json').read_text())
    with TestClient(source.app) as client:
        auth = client.post('/api/login', json={'username': login['username'], 'password': login['password']})
        assert auth.status_code == 200
        client.headers.update({'X-CSRF-Token': auth.json()['csrf_token'], 'X-Workspace-ID': login['workspace_id']})
        with Session(source.engine()) as db:
            user = db.scalar(select(source.User).where(source.User.username == login['username']))
            session = db.scalar(select(source.AuthSession).where(source.AuthSession.user_id == user.id,
                source.AuthSession.revoked.is_(False), source.AuthSession.expires_at > func.clock_timestamp())
                .order_by(source.AuthSession.created_at.desc()))
            p = source.Principal(user.id, login['workspace_id'], 'admin', session.id)
            task = db.get(source.Task, task_id); head = task.data[source.KEY]
            caps = list(db.scalars(select(source.Capability).where(source.Capability.workspace_id == p.workspace_id,
                source.Capability.data['specification']['device_gateway_binding']['origin'].as_string().is_not(None))))
            origins = {cap.data['specification']['device_gateway_binding']['origin'] for cap in caps}
            assert len(origins) == 1 and all(re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', o) for o in origins)
            monkeypatch.setattr(source.settings(), 'device_gateway_allowed_origins', next(iter(origins)))
            selected = source.binding(db, p, task)
            node = next(n for n in selected['binding']['graph']['nodes'] if n['id'] == 'reasoning')
            capability = db.get(source.Capability, node['execution']['capability_id'])
            manifest = source.contracts.build_manifest(capability.data, source.old_store._pin(capability))
            goal_hash = task.data['goal_hash']; head_hash = head['head_hash']
        assert source.publish(client, manifest)['status'] == 'published'
        with Session(source.engine()) as db:
            now = db.scalar(select(func.clock_timestamp()))
            body = {'request_id': str(uuid4()), 'expected_goal_hash': goal_hash,
                'expected_mature_head_hash': head_hash,
                'limits': {'max_tokens': 512, 'max_request_bytes': 50000, 'max_evidence_items': 2},
                'expires_at': (now + timedelta(hours=2)).isoformat(), 'reason': '合成额度验证'}
        # The unrelated workflow counters are exhausted, but the original
        # approved Goal and all original records remain untouched.
        monkeypatch.setattr(goal_queries, 'call_count', lambda *_: 10**6)
        monkeypatch.setattr(native_goals, 'total_steps', lambda *_: 10**6)
        monkeypatch.setattr(capacity, '_counts', lambda *_: (10**6, 10**6))
        original_read = capacity.read_locked
        with monkeypatch.context() as local:
            def invalid_inventory(*_args, **_kwargs):
                raise HTTPException(409, '原库存无法核对')
            local.setattr(capacity, 'read_locked', invalid_inventory)
            with Session(source.engine()) as db:
                with pytest.raises(HTTPException): policy.authorize(db, p, task_id, body)
                db.rollback()
                assert not db.scalar(select(func.count()).select_from(source.Audit).where(
                    source.Audit.object_id == task_id, source.Audit.action == policy.AUTHORIZATION))
        assert capacity.read_locked is original_read
        # A committed orphan in this disposable PG must also fail the actual
        # inventory check, not merely a substituted error. The synthetic row
        # is removed afterwards solely to continue the clean-history case.
        from generic_agents import same_goal_advice_unlimited_store_v2 as v2_store
        orphan_id = str(uuid4())
        with Session(source.engine()) as db:
            db.add(source.Audit(id=orphan_id, workspace_id=p.workspace_id, actor_id=p.user_id,
                action=v2_store.ACCEPTED, object_id=task_id, data={'task_id': task_id}))
            db.commit()
        with Session(source.engine()) as db:
            with pytest.raises(HTTPException): policy.authorize(db, p, task_id, body)
            db.rollback()
            assert not db.scalar(select(func.count()).select_from(source.Audit).where(
                source.Audit.object_id == task_id, source.Audit.action == policy.AUTHORIZATION))
        with Session(source.engine()) as db:
            db.delete(db.get(source.Audit, orphan_id)); db.commit()
        with Session(source.engine()) as db:
            issued = policy.authorize(db, p, task_id, body); db.commit()
            assert issued['limits']['cost_policy'] == 'unlimited' and not issued['cloud_send_authorized']
        from generic_agents import same_goal_advice_unlimited_authority_v2 as authority
        with Session(source.engine()) as db:
            result = authority.current_policy(db, p, task_id, issued['policy_ref'])
            assert result['limits']['cost_policy'] == 'unlimited'
            db.rollback()


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_FRESH_CHECKPOINT')
    or not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'), reason='isolated synthetic checkpoint only')
def test_mature_deepseek_flow_at_generic_goal_capacity(monkeypatch, independent_evidence_dir):
    # Simulate exhausted unrelated tool/step capacity without altering the
    # approved Goal, its source, recorded operations or the real PG history.
    original = policy.authorize
    def authorize_at_capacity(*args, **kwargs):
        with monkeypatch.context() as local:
            local.setattr(goal_queries, 'call_count', lambda *_: 10**6)
            local.setattr(native_goals, 'total_steps', lambda *_: 10**6)
            return original(*args, **kwargs)
    monkeypatch.setattr(policy, 'authorize', authorize_at_capacity)
    monkeypatch.setattr(capacity, '_counts', lambda *_: (10**6, 10**6))
    # Exercises policy, real synthetic approvals, preview, Start, lease,
    # reservation, one loopback provider call, accounting and no resend.
    mature_flow(monkeypatch)
