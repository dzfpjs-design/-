"""Contract/control-flow fixtures only; real PG and dispatch are separate gates."""
import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import same_goal_advice_quota_v304 as v1
from generic_agents import same_goal_advice_unlimited_policy_v2 as v2
from generic_agents.models import Audit
from tests.test_same_goal_advice_quota_v304 import scene


def body(s):
    value = copy.deepcopy(s.body)
    value['limits'].pop('max_cost_minor_units')
    value['limits'].pop('currency')
    return value


def call(s, monkeypatch, value=None):
    # This suite deliberately substitutes the v1 fixture's identity/context IO;
    # it does not prove real membership, publication, goal or DB locking.
    monkeypatch.setattr(v2, 'lock_current_actor', v1.lock_current_actor)
    return v2.authorize(s.db, s.p, s.task.id, value or body(s))


def test_versioned_policy_records_no_cost_and_replays_without_new_audit(scene, monkeypatch):
    s = scene
    original = body(s)
    receipt = call(s, monkeypatch, original)
    assert receipt['configuration_only'] is True and receipt['cloud_send_authorized'] is False
    assert receipt['limits']['provider'] == 'deepseek'
    assert receipt['limits']['cost_policy'] == 'unlimited'
    assert receipt['limits']['allocation_basis'] == 'per_plan'
    assert receipt['limits']['max_actual_calls'] == receipt['limits']['reserved_calls'] == 1
    assert 'max_cost_minor_units' not in receipt['limits']
    assert len(s.rows[Audit]) == 2
    prior = copy.deepcopy([row.data for row in s.rows[Audit]])
    s.time += timedelta(hours=2)
    assert call(s, monkeypatch, original) == receipt
    assert [row.data for row in s.rows[Audit]] == prior


def test_v1_and_v2_active_policies_cannot_overlap_in_either_order(scene, monkeypatch):
    s = scene
    call(s, monkeypatch)
    changed = {**s.body, 'request_id': str(uuid4())}
    with pytest.raises(HTTPException):
        v1.authorize(s.db, s.p, s.task.id, changed)
    assert len(s.rows[Audit]) == 2


def test_v2_rejects_existing_v1_policy_without_changing_it(scene, monkeypatch):
    s = scene
    v1.authorize(s.db, s.p, s.task.id, s.body)
    before = copy.deepcopy([row.data for row in s.rows[Audit]])
    with pytest.raises(HTTPException):
        call(s, monkeypatch, {**body(s), 'request_id': str(uuid4())})
    assert [row.data for row in s.rows[Audit]] == before


def test_reusing_other_version_request_id_is_rejected_even_after_expiry(scene, monkeypatch):
    s = scene
    call(s, monkeypatch)
    s.time += timedelta(hours=2)
    with pytest.raises(HTTPException):
        v1.authorize(s.db, s.p, s.task.id, s.body)
    assert len(s.rows[Audit]) == 2


def test_non_deepseek_and_missing_permission_do_not_create_policy(scene, monkeypatch):
    s = scene
    s.context['model'].data['execution']['provider'] = 'qwen'
    with pytest.raises(HTTPException):
        call(s, monkeypatch)
    assert not s.rows[Audit]
    s.context['model'].data['execution']['provider'] = 'deepseek'
    s.allowed = False
    with pytest.raises(HTTPException):
        call(s, monkeypatch)
    assert not s.rows[Audit]


def test_policy_schema_rejects_cost_or_bool_for_single_call(scene, monkeypatch):
    with pytest.raises(ValidationError):
        v2.Authorize.model_validate({**body(scene), 'limits': {**body(scene)['limits'], 'max_cost_minor_units': 99}})
    receipt = call(scene, monkeypatch)
    event = next(row for row in scene.rows[Audit] if row.action == v2.POLICY)
    assert v2.policy_shape(event, scene.time)[1]['policy_ref'] == receipt['policy_ref']
    damaged = copy.deepcopy(event.data)
    damaged['limits']['max_actual_calls'] = True
    event.data = damaged
    with pytest.raises((HTTPException, ValueError, ValidationError)):
        v2.policy_shape(event, scene.time)


def test_original_request_cannot_be_rebound_to_a_different_mature_head(scene, monkeypatch):
    original = body(scene)
    receipt = call(scene, monkeypatch, original)
    event = next(row for row in scene.rows[Audit] if row.action == v2.POLICY)
    assert event.data['mature_head_hash'] == original['expected_mature_head_hash']
    assert receipt['policy_ref']['record_hash'] == v2.digest(event.data)
    changed = copy.deepcopy(event.data)
    changed['mature_head_hash'] = 'e' * 64
    changed['record_hash'] = v2.c.record_hash(changed)
    event.data = changed
    with pytest.raises(HTTPException):
        call(scene, monkeypatch, original)
