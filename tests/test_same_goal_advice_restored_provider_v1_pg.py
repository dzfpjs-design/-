"""Resume one synthetic Start in a new disposable PG, never on real data."""
import os
import re
import inspect
import sys
import traceback
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_execution_v308 as execution
from generic_agents import same_goal_advice_spending_v308 as spending
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.models import Audit, AuthSession, Capability, CloudCall, Plan, Task
from generic_agents.security import Principal
from tests import test_same_goal_advice_common_provider_v1_pg as provider_test
from tests.synthetic_advice_checkpoint_restore import restore_to_disposable_test_db
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network


def restore_case(monkeypatch):
    root = os.environ['GA_SYNTHETIC_ADVICE_RESTORE']
    monkeypatch.setattr(settings(), 'device_gateway_key_file', settings().device_gateway_key_file)
    restore_to_disposable_test_db(root)
    with Session(engine()) as db:
        plan = db.scalar(select(Plan).where(Plan.data['schema'].as_string() == contracts.PLAN))
        assert plan is not None
        task = db.get(Task, plan.task_id)
        start = db.scalar(select(Audit).where(Audit.object_id == plan.id, Audit.action == execution.STARTED))
        assert start is not None
        auth = db.scalar(select(AuthSession).where(AuthSession.user_id == start.actor_id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        assert auth is not None, '原合成会话已过期或撤权，不能伪造用户'
        actor = Principal(start.actor_id, task.workspace_id, 'admin', auth.id)
        caps = list(db.scalars(select(Capability).where(Capability.workspace_id == task.workspace_id,
            Capability.data['specification']['device_gateway_binding']['origin'].as_string().is_not(None))))
        origins = {cap.data['specification']['device_gateway_binding']['origin'] for cap in caps}
        assert len(origins) == 1 and all(re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', origin) for origin in origins)
        # Recreate the isolated fixture's exact public loopback allowlist. The
        # original service/port is not fabricated or contacted by this step.
        monkeypatch.setattr(settings(), 'device_gateway_allowed_origins', next(iter(origins)))
        assert not list(db.scalars(select(CloudCall)))
        assert execution.budget_calls(db, actor, task) == 1
        assert common.read(db, actor, task) == {'v1_calls': 1, 'queued_v2': 0}
        case = SimpleNamespace(workspace_id=task.workspace_id, task_id=task.id, principal=actor)
    return case


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_ADVICE_RESTORE'),
    reason='requires an explicitly created isolated synthetic Start checkpoint')
def test_resume_synthetic_original_start_with_all_original_guards(monkeypatch, reject_non_loopback_network):
    case = restore_case(monkeypatch)
    # Only skips the expensive test-fixture builder; all original DB, source,
    # role, policy, lease, consent, accounting and provider guards stay real.
    monkeypatch.setattr(provider_test, 'prepare_and_start', lambda _case, _monkeypatch: None)
    original_need = spending.need
    def observe_need(value):
        if not value:
            caller = inspect.currentframe().f_back
            active = sys.exc_info()[1]
            frames = [] if active is None else [
                {'module': os.path.basename(frame.filename), 'function': frame.name, 'line': frame.lineno}
                for frame in traceback.extract_tb(active.__traceback__) if '/generic_agents/' in frame.filename][-8:]
            print('SYNTHETIC_SPENDING_REJECTION', {'function': caller.f_code.co_name,
                'line': caller.f_lineno, 'wrapped_exception_type': type(active).__name__ if active else None,
                'wrapped_frames': frames}, flush=True)
            if caller.f_code.co_name == 'before_transport' and caller.f_lineno == 341:
                local = caller.f_locals; event = local['event']; expected = local['expected']
                marker = {'schema': spending.SCHEMA, 'plan_id': local['refs']['plan_id'],
                    'quota_policy_ref': local['budget']['quota_policy_ref'],
                    'hold_ref': {'id': event.id, 'record_hash': digest(expected)}, 'intent_ref': None}
                keys = set(event.data) | set(expected)
                print('SYNTHETIC_HOLD_COMPARISON', {
                    'object_matches': event.object_id == local['refs']['plan_id'],
                    'actor_matches': event.actor_id == local['p'].user_id,
                    'hold_matches': digest(event.data) == digest(expected),
                    'marker_matches': local['actual'].data[spending.KEY] == marker,
                    'different_hold_fields': sorted(key for key in keys
                        if digest(event.data.get(key)) != digest(expected.get(key)))}, flush=True)
        return original_need(value)
    # Observe the original exception path, then execute the original guard.
    monkeypatch.setattr(spending, 'need', observe_need)
    provider_test.test_v1_loopback_response_is_persisted_and_counted_once(case, monkeypatch, reject_non_loopback_network)
