"""Isolated PG only: old records remain readable, with no new send or write.

The v2 Goal/source and the v1 view are synthetic substitutes. These tests do
not establish real-workspace or HTTP integration readiness.
"""
import copy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_store_v302 as legacy
from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intents
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, CloudCall, Plan, Task, uid
from generic_agents.security import Principal
from tests.test_advice_v2_cloud_reservation_candidate_pg import started, _reserve
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_same_goal_advice_v1_history_compat_pg import add_cancelled


@pytest.fixture
def history(started, monkeypatch):
    s, created, owned = started
    # The preview fixture updates its released v2 capability. Keep the v1
    # history pinned to its own still-published synthetic capability, as a
    # genuine older release would be; do not change the v2 original.
    from tests import test_same_goal_advice_common_inventory_v2_pg as common_fixture
    from tests.test_same_goal_advice_contracts_v302 import scene
    sample = scene(); cap_id = uid()
    sample.manifest['namespace'] = 'synthetic_v1_history'
    sample.preparation['advice_capability_ref']['id'] = cap_id
    sample.preparation['advice_capability_ref']['content_hash'] = digest(sample.manifest)
    monkeypatch.setattr(common_fixture, 'contract_scene', lambda: sample)
    with Session(engine()) as db:
        db.add(Capability(id=cap_id, workspace_id=s['principal'].workspace_id,
            namespace=sample.manifest['namespace'], name=sample.manifest['name'], release_version='1.0.0',
            kind='tool', status='published', content_hash=digest(sample.manifest),
            validated_hash=digest(sample.manifest), data=sample.manifest))
        db.flush()
        old_id = add_cancelled(db, s)
        data = copy.deepcopy(db.get(Plan, old_id).data)
        db.commit()
    with Session(engine()) as db:
        reserved = _reserve(db, started)
        db.commit()
    def get_task(db, p, identity, **kwargs):
        task = db.get(Task, identity)
        if task is None or task.workspace_id != p.workspace_id:
            raise HTTPException(404)
        return task
    monkeypatch.setattr(legacy.existing, '_task', get_task)
    monkeypatch.setattr(legacy, '_view', lambda db, p, task, plan, prep, job:
        {'id': plan.id, 'status': plan.status})
    return s, created, owned, reserved, old_id, data


@pytest.mark.parametrize('with_intent', [False, True])
def test_verified_reservation_allows_old_history_and_exact_replay_only(history, with_intent):
    s, created, owned, reserved, old_id, data = history
    p, task, v2id = s['principal'], s['task_id'], created['plan_id']
    if with_intent:
        with Session(engine()) as db:
            intents.issue(db, p, task, v2id, owned['job_id'], owned['lease_token'], reserved['cloud_call_id'])
            db.commit()
    kwargs = {'reserved_plan_test_only': v2id, 'intent_test_only': with_intent}
    with Session(engine()) as db:
        audit_count = db.scalar(select(func.count()).select_from(Audit))
        plan_count = db.scalar(select(func.count()).select_from(Plan))
        with pytest.raises(HTTPException): legacy.listing(db, p, task)
        db.rollback()
    with Session(engine()) as db:
        assert legacy.listing(db, p, task, **kwargs)['records'] == [{'id': old_id, 'status': 'cancelled'}]
        assert legacy.get(db, p, task, old_id, **kwargs)['id'] == old_id
        assert legacy.get_operation(db, p, task, data['operations'][0]['request_id'], **kwargs)['id'] == old_id
        assert legacy.prepare(db, p, task, data['operations'][0]['body'], **kwargs)['id'] == old_id
        assert legacy.cancel(db, p, task, old_id, data['operations'][1]['body'], **kwargs)['id'] == old_id
        db.commit()
    with Session(engine()) as db:
        changed = copy.deepcopy(data['operations'][0]['body']); changed['request_id'] = str(uuid4())
        with pytest.raises(HTTPException): legacy.prepare(db, p, task, changed, **kwargs)
        db.rollback()
    with Session(engine()) as db:
        changed = copy.deepcopy(data['operations'][0]['body']); changed['reason'] += '变更'
        with pytest.raises(HTTPException): legacy.prepare(db, p, task, changed, **kwargs)
        db.rollback()
    with Session(engine()) as db:
        changed = copy.deepcopy(data['operations'][1]['body']); changed['request_id'] = str(uuid4())
        with pytest.raises(HTTPException): legacy.cancel(db, p, task, old_id, changed, **kwargs)
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 1
        assert db.scalar(select(func.count()).select_from(Audit)) == audit_count
        assert db.scalar(select(func.count()).select_from(Plan)) == plan_count
        assert db.get(Plan, old_id).data == data


def test_scoped_history_rejects_another_workspace(history):
    s, created, _, _, _, _ = history
    p = s['principal']
    foreign = Principal(p.user_id, uid(), p.role, p.session_id)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            legacy.listing(db, foreign, s['task_id'], reserved_plan_test_only=created['plan_id'])


def test_real_http_does_not_expose_test_only_history_option(history, client):
    s, created, _, _, old_id, _ = history
    base = '/api/workspace/tasks/' + s['task_id'] + '/business-advice/preparations'
    params = {'reserved_plan_test_only': created['plan_id'], 'intent_test_only': 'true'}
    assert client.get(base, params=params).status_code == 409
    assert client.get(base + '/' + old_id, params=params).status_code == 409


@pytest.mark.parametrize('fault', ['wrong_plan', 'unannounced_intent', 'tampered_call', 'orphan_audit'])
def test_scoped_history_never_hides_uncertain_records(history, fault):
    s, created, owned, reserved, _, _ = history
    p, task, v2id = s['principal'], s['task_id'], created['plan_id']
    kwargs = {'reserved_plan_test_only': v2id}
    with Session(engine()) as db:
        if fault == 'wrong_plan': kwargs['reserved_plan_test_only'] = uid()
        elif fault == 'unannounced_intent':
            intents.issue(db, p, task, v2id, owned['job_id'], owned['lease_token'], reserved['cloud_call_id'])
        elif fault == 'tampered_call': db.get(CloudCall, reserved['cloud_call_id']).reserved_tokens += 1
        else:
            db.add(Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
                action='same_goal_advice.unlimited_unknown', object_id=task, data={'task_id': task}))
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): legacy.listing(db, p, task, **kwargs)
