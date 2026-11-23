"""Real PG read/guard test, not an end-to-end authorization or dispatch test."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_unlimited_policy_v2 as v2
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, User, uid
from generic_agents.security import Principal
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene


def test_v2_policy_guard_reads_a_durable_pg_record(account):
    # The policy payload is deliberately synthetic; the issuance path, mature
    # Goal and transport have separate tests and are not proved by this case.
    sample = contract_scene()
    scope = deepcopy(sample.preparation['scope'])
    scope.pop('analysis_plan_id')
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'test')
        scope.update(workspace_id=p.workspace_id, task_id=uid())
        now = db.scalar(select(func.clock_timestamp()))
        value = {'schema': v2.POLICY_SCHEMA, 'scope': scope,
            'source_business_capability_ref': sample.preparation['source_business_capability_ref'],
            'advice_capability_ref': sample.preparation['advice_capability_ref'],
            'model_pin': sample.preparation['model_pin'],
            'mature_head_hash': sample.preparation['mature_head']['head_hash'], 'issued_at': now.isoformat(),
            'expires_at': (now + timedelta(minutes=15)).isoformat(),
            'limits': {'provider': 'deepseek', 'cost_policy': 'unlimited', 'allocation_basis': 'per_plan',
                'max_tokens': 512, 'max_request_bytes': 4096, 'max_evidence_items': 2,
                'max_actual_calls': 1, 'reserved_calls': 1, 'reserved_steps': 1,
                'goal_max_tool_calls': 8, 'goal_max_steps': 8}}
        value['record_hash'] = v2.c.record_hash(value)
        event = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, object_id=scope['task_id'],
            action=v2.POLICY, created_at=now, data=value)
        db.add(event); db.commit()
        identity = event.id
    engine().dispose()
    with Session(engine()) as db:
        event = db.get(Audit, identity)
        saved, limits = v2.policy_shape(event, db.scalar(select(func.clock_timestamp())))
        assert saved['record_hash'] == value['record_hash']
        assert limits['policy_ref'] == {'id': identity, 'record_hash': digest(value)}
        assert limits['allocation_basis'] == 'per_plan'
        with pytest.raises(HTTPException):
            v2.assert_no_active_v2(db, p, SimpleNamespace(id=scope['task_id']), scope,
                db.scalar(select(func.clock_timestamp())))
        other = deepcopy(scope); other['goal_hash'] = 'a' * 64
        v2.assert_no_active_v2(db, p, SimpleNamespace(id=scope['task_id']), other,
            db.scalar(select(func.clock_timestamp())))
        assert not list(db.scalars(select(CloudCall)))
