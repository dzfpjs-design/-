"""PG timestamptz session zones preserve the original immutable hold hash."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_spending_v308 as spending
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.gateway import ModelBlocked
from generic_agents.models import Audit, User, uid


def test_cross_session_timezone_preserves_same_instant_but_rejects_changed_time(account):
    with Session(engine()) as db:
        db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        actor = db.scalar(select(User).where(User.username == account[0]))
        at = db.scalar(select(func.clock_timestamp())); plan_id = uid()
        refs = {key: uid() for key in ('actor_id', 'task_id', 'plan_id', 'job_id', 'invocation_id', 'lease_token')}
        refs.update(workspace_id=account[2], actor_id=actor.id, plan_id=plan_id, execution_hash='e' * 64)
        context = {'spending_quote': {'record_hash': 'a' * 64, 'currency': 'CNY', 'maximum_cost_minor_units': 2},
            'spending_budget': {'quota_policy_ref': {'id': uid(), 'record_hash': 'b' * 64}, 'max_cost_minor_units': 5},
            'start': SimpleNamespace(id=uid(), data={'original': 'synthetic'}),
            'preview': SimpleNamespace(id=uid(), data={'record_hash': 'c' * 64}),
            'price_ref': {'id': uid(), 'record_hash': 'd' * 64},
            'prep': {'model_pin': {'id': uid(), 'content_hash': 'f' * 64, 'release_version': '1.0.0'}},
            'prepared': {'request_hash': '1' * 64, 'dispatch_options_hash': '2' * 64}}
        call = SimpleNamespace(id=uid()); identity = {'task_id': refs['task_id']}; attempt = uid()
        original = spending._hold(context, call, refs, identity, attempt, at)
        event = Audit(id=uid(), workspace_id=account[2], actor_id=actor.id, object_id=plan_id,
            action=spending.HOLD, created_at=at, data=original)
        intent_data = spending._intent_data(context, refs, event, original, call, identity, attempt, at)
        intent = Audit(id=uid(), workspace_id=account[2], actor_id=actor.id, object_id=plan_id,
            action=spending.INTENT, created_at=at, data=intent_data)
        db.add_all([event, intent]); db.commit(); event_id, intent_id = event.id, intent.id
    with Session(engine()) as db:
        db.execute(text("SET LOCAL TIME ZONE 'Asia/Shanghai'"))
        stored = db.get(Audit, event_id)
        assert stored.created_at.utcoffset() == timedelta(hours=8)
        rebuilt = spending._hold(context, call, refs, identity, attempt, stored.created_at)
        assert rebuilt == original and digest(rebuilt) == digest(stored.data)
        changed = spending._hold(context, call, refs, identity, attempt, stored.created_at + timedelta(seconds=1))
        assert digest(changed) != digest(stored.data)
        saved_intent = db.get(Audit, intent_id)
        rechecked = spending._intent_data(context, refs, stored, rebuilt, call, identity, attempt, saved_intent.created_at)
        assert rechecked == intent_data and digest(rechecked) == digest(saved_intent.data)
        altered = spending._intent_data(context, refs, stored, rebuilt, call, identity, attempt,
            saved_intent.created_at + timedelta(seconds=1))
        assert digest(altered) != digest(saved_intent.data)
        with pytest.raises(ModelBlocked):
            spending._hold(context, call, refs, identity, attempt, stored.created_at.replace(tzinfo=None))
        with pytest.raises(ModelBlocked):
            spending._intent_data(context, refs, stored, rebuilt, call, identity, attempt,
                saved_intent.created_at.replace(tzinfo=None))
