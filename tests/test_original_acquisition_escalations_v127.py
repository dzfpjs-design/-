"""Actual queued coordination keeps stopped native Tasks byte-for-byte intact.

Only the downstream fan-out observer is instrumented.  Compilation, goal
definition, supplier calls, the original cold chain and event receivers are real.
"""
import copy
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventIntent
from generic_agents.models import Acquisition, Audit, Capability, CloudCall, DataGap, DataRun, Entity, Event, Job, ObservationPlan, Task
from generic_agents.original_acquisition_coordination_v127 import ACTION, SCHEMA
from generic_agents.services import as_dict
from generic_agents.worker import deliver_events
from tests.test_native_goal_acquisitions import prepared, stopped
from tests.test_native_goals import setup as native_setup, define, drain
from tests.test_original_acquisition_reception_v127 import _block, _restore, _two_business_prepared
from tests.test_sensing_selection import supplier, asset, monitor, dispatch, principal
from tests.test_vertical_slice import manifest, publish


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _without_acquisition(client, supplier):
    release, obj, task, base, body = native_setup(client, source='observations', observation_mode='allow_new')
    field = body['goal_spec']['required_fields'][0]
    source = publish(client, manifest('source', name='native_no_asset_' + uuid4().hex[:8],
        allowed_agent_roles=['perception'], execution={'adapter': 'http', 'endpoint': supplier['url'] + '/observe',
            'receipt_endpoint': supplier['url'] + '/receipt', 'side_effect': True},
        specification={'timezone': 'UTC', 'observation_capability': {
            'fields': [field], 'modalities': ['numeric'], 'actions': ['observe']}}))
    selected = asset(client, obj, source, fields=[field], health='offline')
    policy = monitor(client, obj, source, [selected], fields=[field])
    define(client, base, body); drain(task)
    with Session(engine()) as db:
        assert not list(db.scalars(select(Acquisition)))
    return {'r': release, 'obj': obj, 'task': task, 'base': base, 'field': field,
        'source': source, 'asset': selected, 'monitor': policy}


def _queued(client, supplier, kind='perception.acquisition_escalation', *, two_businesses=False, no_acquisition=False):
    p = (_without_acquisition(client, supplier) if no_acquisition else
        _two_business_prepared(client, supplier) if two_businesses else prepared(client, supplier))
    if not no_acquisition:
        supplier['modes'][p['asset']['external_id']] = 'unknown'
        dispatch(p['monitor'], p['acquisition_id'])
    with Session(engine()) as db:
        parent = db.get(Task, p['task']['id'])
        gap = db.scalars(select(DataGap).where(DataGap.query_id == parent.data['native_goal_query_id'])).one()
        plan = db.get(ObservationPlan, gap.data['observation_plan_id'])
        assert plan.data['observation_request']['goal_delegation']['task_id'] == parent.id
        assert plan.status == 'escalated'
        p.update(gap_id=gap.id, plan_id=plan.id, query_id=gap.query_id)
        if kind == 'data.gap_coordination':
            from generic_agents.data_gaps import route
            assert route(db, principal(p['monitor']), gap.id) == 'succeeded'
            db.commit()
        event = db.scalars(select(Event).where(Event.kind == kind,
            (Event.data['plan_id'].as_string() == plan.id) if kind == 'perception.acquisition_escalation'
            else (Event.data['gap_id'].as_string() == gap.id))).one()
        assert event.status == 'pending_delivery'
        p['event_id'] = event.id
    return p


def _goal_projection(value):
    # This is a live DB-clock projection, never a Task/goal receipt field.
    # Verify its type/timezone separately; compare every other response field.
    result = copy.deepcopy(value)
    clock = result['check_worker']['database_now']
    assert clock is None or isinstance(clock, str) and datetime.fromisoformat(clock).tzinfo is not None
    result['check_worker']['database_now'] = None
    return result


def _snapshot(client, p, *, with_goal=True):
    goal = _goal_projection(_ok(client.get(p['base']))) if with_goal else None
    export = _ok(client.get(p['base'] + '/export')) if with_goal else None
    if export is not None: export['goal'] = _goal_projection(export['goal'])
    with Session(engine()) as db:
        return {'task': copy.deepcopy(as_dict(db.get(Task, p['task']['id']))),
            'gap': copy.deepcopy(as_dict(db.get(DataGap, p['gap_id']))),
            'plan': copy.deepcopy(as_dict(db.get(ObservationPlan, p['plan_id']))),
            'tasks': set(db.scalars(select(Task.id))), 'jobs': set(db.scalars(select(Job.id))),
            'intents': set(db.scalars(select(GraphEventIntent.id))), 'clouds': set(db.scalars(select(CloudCall.id))),
            'goal': goal, 'export': export}


def _receiver(db, event):
    if event.kind == 'perception.acquisition_escalation':
        from generic_agents.sensing_selection import receive_escalation
        return receive_escalation(db, event)
    from generic_agents.data_gaps import coordinate
    return coordinate(db, event)


def _forbid_target_fanout(monkeypatch, p):
    from generic_agents import worker
    actual = worker.enqueue_graph_event
    calls = []
    def observed(db, event):
        if event.id == p['event_id']:
            raise AssertionError('已关闭原业务协调不能再派生图事件')
        calls.append(event.id)
        return actual(db, event)
    monkeypatch.setattr(worker, 'enqueue_graph_event', observed)
    return calls


@pytest.mark.parametrize('kind', ['perception.acquisition_escalation', 'data.gap_coordination'])
@pytest.mark.parametrize('closure', ['goal_stopped', 'package_withdrawn', 'instance_not_active', 'release_changed', 'business_disabled'])
def test_queued_original_coordination_is_retained_without_mutating_closed_native_work(client, supplier, monkeypatch, kind, closure):
    p = _queued(client, supplier, kind, two_businesses=closure == 'business_disabled')
    if closure == 'goal_stopped': stopped(client, p)
    else: _block(client, p, closure)
    before = _snapshot(client, p)
    _forbid_target_fanout(monkeypatch, p)
    calls = len(supplier['calls']); reads = len(supplier.get('read_calls', []))
    deliver_events(event_kind=kind)
    assert _snapshot(client, p) == before
    with Session(engine()) as db:
        event = db.get(Event, p['event_id']); receipt = copy.deepcopy(event.data['derivation_receipt'])
        assert event.status == 'received' and receipt['schema'] == SCHEMA
        assert receipt['event_id'] == event.id and receipt['event_kind'] == kind and receipt['derivation_suppressed'] is True
        assert receipt['code'] == ('goal_ended' if closure == 'goal_stopped' else closure)
        assert receipt['origin']['task_id'] == p['task']['id'] and receipt['origin']['gap_id'] == p['gap_id']
        assert receipt['receipt_hash'] == digest({key: value for key, value in receipt.items() if key != 'receipt_hash'})
        issued = list(db.scalars(select(Audit).where(Audit.action == ACTION, Audit.object_id == event.id)))
        assert len(issued) == 1 and issued[0].data['receipt_hash'] == receipt['receipt_hash']
        assert _receiver(db, event) == receipt
        db.commit()
    assert _snapshot(client, p) == before
    assert len(supplier['calls']) == calls and len(supplier.get('read_calls', [])) == reads


def test_no_source_escalation_before_any_acquisition_still_keeps_stopped_parent_intact(client, supplier, monkeypatch):
    p = _queued(client, supplier, no_acquisition=True)
    stopped(client, p); before = _snapshot(client, p); _forbid_target_fanout(monkeypatch, p)
    deliver_events(event_kind='perception.acquisition_escalation')
    assert _snapshot(client, p) == before and supplier['calls'] == []
    with Session(engine()) as db:
        assert not list(db.scalars(select(Acquisition)))
        assert db.get(Event, p['event_id']).data['derivation_receipt']['code'] == 'goal_ended'


@pytest.mark.parametrize('kind', ['perception.acquisition_escalation', 'data.gap_coordination'])
def test_active_native_coordination_and_duplicate_delivery_use_the_real_original_task(client, supplier, monkeypatch, kind):
    p = _queued(client, supplier, kind)
    # Historical Task/Run/Gap/Plan hashes alone cannot authorize current
    # coordination when the actual source object's definition has drifted.
    with Session(engine()) as db:
        entity = db.get(Entity, p['obj']['id']); original_type = entity.entity_type
        entity.entity_type = original_type + '_changed'; db.flush()
        task = copy.deepcopy(as_dict(db.get(Task, p['task']['id'])))
        with pytest.raises(HTTPException) as rejected: _receiver(db, db.get(Event, p['event_id']))
        assert rejected.value.status_code == 409
        assert as_dict(db.get(Task, p['task']['id'])) == task
        db.rollback(); assert db.get(Entity, p['obj']['id']).entity_type == original_type
    from generic_agents import worker
    actual = worker.enqueue_graph_event; calls = []
    def observed(db, event):
        if event.id == p['event_id']: calls.append(event.id)
        return actual(db, event)
    monkeypatch.setattr(worker, 'enqueue_graph_event', observed)
    deliver_events(event_kind=kind)
    assert calls == [p['event_id']]
    with Session(engine()) as db:
        event = db.get(Event, p['event_id']); task = db.get(Task, p['task']['id'])
        assert event.status == 'received' and 'derivation_receipt' not in event.data
        assert not list(db.scalars(select(Audit).where(Audit.action == ACTION)))
        assert set(db.scalars(select(Task.id))) == {task.id}
        if kind == 'perception.acquisition_escalation':
            assert task.data['perception_escalations'].count(event.id) == 1
        saved = copy.deepcopy(as_dict(task)); plan = copy.deepcopy(as_dict(db.get(ObservationPlan, p['plan_id'])))
        assert _receiver(db, event) is True
        db.commit()
        assert as_dict(task) == saved and as_dict(db.get(ObservationPlan, p['plan_id'])) == plan


def test_existing_only_native_gap_can_coordinate_without_borrowing_device_permission(client):
    release, obj, task, base, body = native_setup(client, source='observations', observation_mode='existing_only')
    define(client, base, body); drain(task)
    with Session(engine()) as db:
        parent = db.get(Task, task['id'])
        gap = db.scalars(select(DataGap).where(DataGap.query_id == parent.data['native_goal_query_id'])).one()
        event = db.scalars(select(Event).where(Event.kind == 'data.gap_coordination', Event.data['gap_id'].as_string() == gap.id)).one()
        assert event.status == 'received' and gap.data['business_task_id'] == parent.id
        assert not list(db.scalars(select(ObservationPlan))) and not list(db.scalars(select(Acquisition)))
        assert _receiver(db, event) is True
        assert 'derivation_receipt' not in event.data
        event_id = event.id
        db.commit()
    stopped(client, {'base': base})
    with Session(engine()) as db:
        parent = db.get(Task, task['id']); saved = copy.deepcopy(as_dict(parent))
        event = db.get(Event, event_id); result = _receiver(db, event)
        assert result['code'] == 'goal_ended' and result['origin']['plan_ids'] == []
        db.commit(); assert as_dict(parent) == saved


def test_retained_event_stays_reducing_after_real_instance_resume(client, supplier):
    p = _queued(client, supplier)
    _block(client, p, 'instance_not_active')
    deliver_events(event_kind='perception.acquisition_escalation')
    with Session(engine()) as db:
        old = copy.deepcopy(db.get(Event, p['event_id']).data['derivation_receipt'])
    # A newly active page may recompute its current assessment timestamp.  The
    # retained original receipt and complete durable Task/Gap/Plan stay fixed.
    _restore(client, p); before = _snapshot(client, p, with_goal=False)
    with Session(engine()) as db:
        assert _receiver(db, db.get(Event, p['event_id'])) == old
        db.commit()
    assert _snapshot(client, p, with_goal=False) == before


@pytest.mark.parametrize('damage', ['plan_origin_missing', 'plan_origin_none', 'plan_delegation_missing', 'plan_delegation_none',
    'query_native_missing', 'query_delegation_missing', 'task_query_missing', 'context_hash', 'request_hash',
    'gap_request', 'event_reason', 'event_audit', 'package_hash'])
def test_declared_original_corruption_is_blocked_without_generic_fallback_or_task_mutation(client, supplier, monkeypatch, damage):
    p = _queued(client, supplier); stopped(client, p)
    with Session(engine()) as db:
        task = db.get(Task, p['task']['id']); plan = db.get(ObservationPlan, p['plan_id'])
        run = db.get(DataRun, p['query_id']); gap = db.get(DataGap, p['gap_id']); event = db.get(Event, p['event_id'])
        if damage.startswith('plan_origin'):
            value = copy.deepcopy(plan.data)
            if damage.endswith('none'): value['observation_request'] = None
            else:
                value.pop('observation_request')
                gap.data = {key: item for key, item in gap.data.items() if key != 'observation_plan_id'}
                acquisition = db.get(Acquisition, p['acquisition_id'])
                acquisition.data = {key: item for key, item in acquisition.data.items() if key != 'data_gap_id'}
            plan.data = value
        elif damage.startswith('plan_delegation'):
            value = copy.deepcopy(plan.data)
            if damage.endswith('none'): value['observation_request']['goal_delegation'] = None
            else: value['observation_request'].pop('goal_delegation')
            plan.data = value
        elif damage == 'query_native_missing': run.data = {key: value for key, value in run.data.items() if key != 'native_goal_task_id'}
        elif damage == 'query_delegation_missing': run.data = {key: value for key, value in run.data.items() if key != 'goal_delegation'}
        elif damage == 'task_query_missing': task.data = {key: value for key, value in task.data.items() if key != 'native_goal_query_id'}
        elif damage == 'context_hash': task.data = {**task.data, 'native_goal_context_hash': '0' * 64}
        elif damage == 'request_hash': run.data = {**run.data, 'native_goal_request_hash': '0' * 64}
        elif damage == 'gap_request': gap.data = {**gap.data, 'request': {**gap.data['request'], 'question': '未经原请求确认的替代要求'}}
        elif damage == 'event_reason': event.data = {**event.data, 'reason': '不是实际升级历史的原因'}
        elif damage == 'event_audit':
            issued = db.scalars(select(Audit).where(Audit.action == 'perception.plan_escalated', Audit.object_id == plan.id)).one()
            issued.data = {'code': 'different'}
        else:
            package = db.get(Capability, task.package_id); package.content_hash = '0' * 64
        db.commit()
        saved = {model: copy.deepcopy(as_dict(row)) for model, row in [('task', task), ('gap', gap), ('plan', plan)]}
        ids = {model: set(db.scalars(select(model.id))) for model in (Task, Job, GraphEventIntent, CloudCall)}
    _forbid_target_fanout(monkeypatch, p)
    deliver_events(event_kind='perception.acquisition_escalation')
    with Session(engine()) as db:
        event = db.get(Event, p['event_id']); assert event.status == 'blocked_delivery' and 'delivery_error' in event.data
        assert 'derivation_receipt' not in event.data
        assert as_dict(db.get(Task, p['task']['id'])) == saved['task']
        assert as_dict(db.get(DataGap, p['gap_id'])) == saved['gap'] and as_dict(db.get(ObservationPlan, p['plan_id'])) == saved['plan']
        assert all(set(db.scalars(select(model.id))) == identifiers for model, identifiers in ids.items())
        assert not list(db.scalars(select(Audit).where(Audit.action == ACTION, Audit.object_id == event.id)))


@pytest.mark.parametrize('damage', ['marker_missing', 'audit_missing', 'duplicate_audit', 'receipt_hash'])
def test_retention_receipt_cannot_be_deleted_or_reissued_after_current_scope_recovers(client, supplier, damage):
    p = _queued(client, supplier); _block(client, p, 'instance_not_active')
    deliver_events(event_kind='perception.acquisition_escalation'); _restore(client, p)
    with Session(engine()) as db:
        event = db.get(Event, p['event_id']); value = copy.deepcopy(event.data)
        issued = db.scalars(select(Audit).where(Audit.object_id == event.id, Audit.action == ACTION)).one()
        if damage == 'marker_missing': value.pop('derivation_receipt')
        elif damage == 'audit_missing': db.delete(issued)
        elif damage == 'duplicate_audit':
            db.add(Audit(workspace_id=issued.workspace_id, actor_id=issued.actor_id, object_id=issued.object_id,
                action=issued.action, data=copy.deepcopy(issued.data)))
        else: value['derivation_receipt']['receipt_hash'] = '0' * 64
        event.data = value; db.commit()
        saved = copy.deepcopy(as_dict(db.get(Task, p['task']['id'])))
        with pytest.raises(HTTPException) as rejected: _receiver(db, event)
        assert rejected.value.status_code == 409
        db.rollback(); assert as_dict(db.get(Task, p['task']['id'])) == saved
