"""Exact original hold filtering with real reservation SQL and explicit IO gates.

No real IAM, PG transaction, lease or device send is proved. The actual target
runtime must independently exercise its normal original-plan retry.
"""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import sensing_selection as subject, device_identity
from generic_agents import observation_requests, monitor_scheduling_service_v253 as scheduling
from generic_agents import goal_conflict_observation_service_v259 as conflict
from generic_agents.models import ObservationPlan, SensorAsset, SensorReservation, DeviceReservation


ASSET_BUSY = '资产被未完成或未知动作占用'
DEVICE_BUSY = '统一设备被其他接口的未完成或未知动作占用'
AT = datetime(2026, 11, 1, tzinfo=timezone.utc)


def uid(number): return str(UUID(int=number))
def sql(statement): return str(statement.compile(compile_kwargs={'literal_binds': True}))


@pytest.fixture
def scene(monkeypatch):
    p = NS(workspace_id=uid(1), user_id=uid(2))
    acq = NS(id=uid(3), workspace_id=p.workspace_id, entity_id=uid(4), status='authorized',
        data={'actor_id': p.user_id, 'observation_plan_id': uid(5), 'sensor_asset_id': uid(6),
            'sensor_asset_version': 2, 'sensor_device_id': uid(7), 'sensor_device_version': 3,
            'device_binding_id': uid(8), 'device_binding_version': 4,
            'source_hash': 'a' * 64, 'selected_fields': ['temperature'], 'budget_day': 'day'})
    policy = NS(approved_by=p.user_id, data={'selection_requirement': {'max_total_cost_minor': 100},
        'max_acquisitions_per_day': 10})
    plan = NS(id=uid(5), status='collecting', data={'deadline': (AT + timedelta(hours=1)).isoformat()})
    asset = NS(id=uid(6), version=2)
    asset_hold = NS(workspace_id=p.workspace_id, asset_id=asset.id, acquisition_id=acq.id)
    device_hold = NS(workspace_id=p.workspace_id, device_id=uid(7), acquisition_id=acq.id)
    choice = {'asset_id': asset.id, 'source_hash': 'a' * 64, 'device_id': uid(7), 'device_version': 3,
        'binding_id': uid(8), 'binding_version': 4, 'reasons': [ASSET_BUSY, DEVICE_BUSY]}
    calls = []
    s = NS(p=p, acq=acq, policy=policy, plan=plan, asset=asset, asset_hold=asset_hold,
        device_hold=device_hold, choice=choice, calls=calls)

    class LockedReadIO:
        def scalar(self, statement):
            assert statement._for_update_arg is not None, 'own-hold exception needs a locked original row'
            expected_asset = select(SensorReservation).where(SensorReservation.workspace_id == p.workspace_id,
                SensorReservation.asset_id == acq.data['sensor_asset_id']).with_for_update()
            expected_device = select(DeviceReservation).where(DeviceReservation.workspace_id == p.workspace_id,
                DeviceReservation.device_id == acq.data['sensor_device_id']).with_for_update()
            if sql(statement) == sql(expected_asset): calls.append('locked asset hold'); return s.asset_hold
            assert sql(statement) == sql(expected_device)
            calls.append('locked device hold'); return s.device_hold
        def add(self, row): pytest.fail('pre-dispatch cannot manufacture a hold')
        def commit(self): pytest.fail('pre-dispatch cannot grant a new transaction')

    db = LockedReadIO(); s.db = db
    monkeypatch.setattr(subject, 'now', lambda: AT)
    monkeypatch.setattr(device_identity, 'lock', lambda actual, actor: calls.append('identity lock')
        if actual is db and actor is p else pytest.fail('wrong lock scope'))

    def scoped(actual, model, identity, actor, locked=False):
        assert actual is db and actor is p and locked is True
        row = plan if model is ObservationPlan else asset
        assert model in {ObservationPlan, SensorAsset} and row.id == identity
        calls.append('locked plan' if model is ObservationPlan else 'locked asset')
        return row

    def effective(actual, actor, original, request, *, dispatch=False):
        assert (actual, actor, original, request, dispatch) == (db, p, policy, None, True)
        calls.append('current original request'); return policy

    def scheduling_gate(actual, actor, original, acquisition, request):
        assert (actual, actor, original, acquisition, request) == (db, p, policy, acq, None)
        calls.append('current schedule permit')

    def conflict_gate(actual, actor, original_plan, *, acquisition):
        assert (actual, actor, original_plan, acquisition) == (db, p, plan, acq)
        calls.append('current conflict permit')

    monkeypatch.setattr(subject, 'scoped', scoped)
    monkeypatch.setattr(observation_requests, 'effective', effective)
    monkeypatch.setattr(scheduling, 'require_dispatch', scheduling_gate)
    monkeypatch.setattr(conflict, 'require_new_action', conflict_gate)
    monkeypatch.setattr(subject, 'candidates', lambda *a: [copy.deepcopy(choice)])
    monkeypatch.setattr(subject, 'used_budget', lambda *a: ([], 5, 1, 'day'))
    return s


@pytest.mark.parametrize('status', ['authorized', 'dispatching'])
def test_same_original_request_can_pass_second_presend_check_without_stealing_or_releasing_holds(scene, status):
    s = scene; s.acq.status = status
    before = copy.deepcopy((s.asset_hold, s.device_hold, s.acq.data, s.choice))
    assert subject.pre_dispatch(s.db, s.p, s.policy, s.acq) is s.asset
    assert (s.asset_hold, s.device_hold, s.acq.data, s.choice) == before
    assert s.calls == ['identity lock', 'locked plan', 'current original request',
        'current schedule permit', 'current conflict permit', 'locked asset',
        'locked asset hold', 'locked device hold']


@pytest.mark.parametrize('damage', ['asset_missing', 'device_missing', 'asset_other_acq', 'device_other_acq',
    'asset_other_workspace', 'device_other_workspace', 'asset_other_id', 'device_other_id',
    'unknown', 'waiting_observation', 'post_marker', 'empty_post_marker', 'other_actor', 'other_approver'])
def test_missing_foreign_or_uncertain_hold_is_never_ignored(scene, damage):
    s = scene
    if damage == 'asset_missing': s.asset_hold = None
    elif damage == 'device_missing': s.device_hold = None
    elif damage == 'asset_other_acq': s.asset_hold.acquisition_id = uid(99)
    elif damage == 'device_other_acq': s.device_hold.acquisition_id = uid(99)
    elif damage == 'asset_other_workspace': s.asset_hold.workspace_id = uid(99)
    elif damage == 'device_other_workspace': s.device_hold.workspace_id = uid(99)
    elif damage == 'asset_other_id': s.asset_hold.asset_id = uid(99)
    elif damage == 'device_other_id': s.device_hold.device_id = uid(99)
    elif damage == 'unknown': s.acq.status = 'unknown'
    elif damage == 'waiting_observation': s.acq.status = 'awaiting_observation'
    elif damage == 'post_marker': s.acq.data['dispatch_started_at'] = AT.isoformat()
    elif damage == 'empty_post_marker': s.acq.data['dispatch_started_at'] = None
    elif damage == 'other_actor': s.acq.data['actor_id'] = uid(99)
    elif damage == 'other_approver': s.policy.approved_by = uid(99)
    with pytest.raises(HTTPException) as error: subject.pre_dispatch(s.db, s.p, s.policy, s.acq)
    assert error.value.status_code == 423


@pytest.mark.parametrize('damage', ['asset_version', 'source_hash', 'device_version', 'binding_version', 'health'])
def test_own_holds_do_not_clear_other_version_health_or_identity_denials(scene, damage):
    s = scene
    if damage == 'asset_version': s.asset.version += 1
    elif damage == 'source_hash': s.choice['source_hash'] = 'b' * 64
    elif damage == 'device_version': s.choice['device_version'] += 1
    elif damage == 'binding_version': s.choice['binding_version'] += 1
    elif damage == 'health': s.choice['reasons'].append('健康声明未知、离线、降级或已过期')
    with pytest.raises(HTTPException) as error: subject.pre_dispatch(s.db, s.p, s.policy, s.acq)
    assert error.value.status_code == 409


@pytest.mark.parametrize('gate', ['request', 'schedule', 'conflict'])
def test_original_current_permission_failures_still_stop_before_hold_exception(scene, monkeypatch, gate):
    s = scene
    def rejected(*args, **kwargs): raise HTTPException(403, 'current original permission rejected')
    module, name = {'request': (observation_requests, 'effective'),
        'schedule': (scheduling, 'require_dispatch'), 'conflict': (conflict, 'require_new_action')}[gate]
    monkeypatch.setattr(module, name, rejected)
    with pytest.raises(HTTPException) as error: subject.pre_dispatch(s.db, s.p, s.policy, s.acq)
    assert error.value.status_code == 403
    assert 'locked asset hold' not in s.calls and 'locked device hold' not in s.calls
