"""Workspace-only monitor coordination; queueing is not device authority.

The root performs SQL only. One durable Job consumes one approved policy due;
the existing acquisition Job owns every device HTTP boundary and unknown-state
recovery. A failed original due is retained until policy/head approval changes.
"""
import copy
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .contracts import digest
from .db import engine
from .models import Audit, Job, MonitorPolicy, uid
from .security import Principal
from .services import audit
from .workflow_monitor_schedule_models_v200 import WorkflowMonitorScheduleHeadV200 as Head

KIND = 'consume_workspace_monitor_tick'
PIN = 'workspace-monitor-tick-v203'
RESULT = 'workspace-monitor-tick-result-v203'
QUEUED = 'workspace.monitor.tick_queued_v203'
CHECKED = 'workspace.monitor.tick_checked_v203'
KEY = 'workspace_monitor_tick'
RESULT_KEY = 'workspace_monitor_tick_result'
OPEN = {'queued', 'running', 'unknown', 'waiting_service', 'waiting_receipt'}
ERROR = '原监测检查任务、工作区、批准或独立审计无法核对；未安排新的设备动作'


def need(ok, message=ERROR):
    if not ok:
        raise HTTPException(409, message)


def workspace(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('监测协调必须提供标准小写工作区 UUID') from None
    return value


def _time(value):
    if type(value) is str:
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            need(False)
    need(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)


def _iso(value):
    return _time(value).isoformat()


def _head_pin(head):
    from .workflow_monitor_schedule_v200 import policy_tick_pin
    return policy_tick_pin(head)


def due_hint(policy, head, latest, at, *, control=None):
    """Pure scheduling hint. The consumer authenticates the entire V200 head.

    Raw head JSON can suppress/queue a check, never authorize fallback or IO.
    An unfinished or failed original due never becomes a new automatic retry.
    """
    at = _time(at)
    approved = policy.data.get('approved_hash')
    actor_id = policy.approved_by
    need(type(approved) is str and len(approved) == 64 and all(c in '0123456789abcdef' for c in approved)
        and type(actor_id) is str and actor_id)
    pin = _head_pin(head)
    legacy = head is None or head.status == 'expired' or (
        head.status == 'cancelled' and head.data.get('restore_legacy') is True)
    mode = 'legacy' if legacy else 'schedule' if head.status == 'active' and head.next_due is not None else 'suppressed'
    context = {'workspace_id': policy.workspace_id, 'policy_id': policy.id,
        'policy_version': policy.version, 'approved_hash': approved, 'actor_id': actor_id,
        'head': pin, 'mode': mode}
    if control is not None:context['control']=copy.deepcopy(control)
    context_hash = digest(context)
    interval = None
    if mode == 'schedule':
        due = _time(head.next_due)
    elif mode == 'suppressed':
        due = _time(head.created_at)
    else:
        cooldown = policy.data.get('cooldown_seconds')
        need(type(cooldown) is int and cooldown >= 0)
        interval = max(60, cooldown)
        due = _time(policy.created_at)
        if latest is not None and latest.data.get(KEY, {}).get('schedule_hint', {}).get('context_hash') == context_hash:
            if latest.status != 'succeeded':
                return None
            result = latest.data.get(RESULT_KEY)
            need(type(result) is dict and result.get('state') == 'checked'
                and result.get('record_hash') == digest({k: v for k, v in result.items() if k != 'record_hash'}))
            due = _time(result['checked_at']) + timedelta(seconds=interval)
    if at < due:
        return None
    return {'mode': mode, 'head': pin, 'due_at': _iso(due), 'interval_seconds': interval,
        'context_hash': context_hash, **({'control':copy.deepcopy(control)} if control is not None else {})}


def _request(policy, hint):
    return {'schema': PIN, 'workspace_id': policy.workspace_id, 'policy_id': policy.id,
        'policy_version': policy.version, 'approved_hash': policy.data['approved_hash'],
        'actor_id': policy.approved_by, 'schedule_hint': copy.deepcopy(hint)}


def request_key(pin):
    return 'workspace-monitor:' + pin['policy_id'] + ':' + digest(pin)


def _queue_anchor(job_id, pin):
    return {'schema': PIN, 'job_id': job_id, 'request_hash': digest(pin), 'pin': copy.deepcopy(pin)}


def _head(db, workspace_id, policy_id):
    return db.scalar(select(Head).where(Head.workspace_id == workspace_id,
        Head.policy_id == policy_id).execution_options(populate_existing=True))


def _enqueue(db, workspace_id, policy_id, *, _stop_event=None):
    # This queue-only mutex is never acquired by the policy consumer. No Task,
    # Policy or source lock is held while waiting for it, preserving V200 order.
    key = int(digest({'workspace_monitor_queue': workspace_id, 'policy_id': policy_id})[:15], 16)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
    if _stop_event is not None and _stop_event.is_set():
        return None
    policy = db.scalar(select(MonitorPolicy).where(MonitorPolicy.workspace_id == workspace_id,
        MonitorPolicy.id == policy_id, MonitorPolicy.status == 'active').execution_options(populate_existing=True))
    if policy is None:
        return None
    active = db.scalar(select(Job.id).where(Job.workspace_id == workspace_id, Job.kind == KIND,
        Job.data['policy_id'].as_string() == policy.id, Job.status.in_(OPEN)).limit(1))
    if active is not None:
        return None
    latest = db.scalar(select(Job).where(Job.workspace_id == workspace_id, Job.kind == KIND,
        Job.data['policy_id'].as_string() == policy.id).order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
    from .monitor_scheduling_service_v253 import periodic_gate
    gate=periodic_gate(db,Principal(policy.approved_by,workspace_id,'viewer','monitor-queue-mode'),policy)
    if not gate['allowed']:return None
    head = _head(db, workspace_id, policy.id)
    hint = due_hint(policy, head, latest, db.scalar(select(func.clock_timestamp())),control=gate['pin'])
    if hint is None:
        return None
    pin = _request(policy, hint)
    identity = uid()
    created = db.scalar(insert(Job).values(id=identity, workspace_id=workspace_id, kind=KIND,
        idempotency_key=request_key(pin), status='queued', available_at=_time(hint['due_at']),
        data={'actor_id': policy.approved_by, 'policy_id': policy.id, KEY: pin, 'request_hash': digest(pin)})
        .on_conflict_do_nothing(index_elements=['workspace_id', 'idempotency_key']).returning(Job.id))
    if created is None:
        return None
    p = Principal(policy.approved_by, workspace_id, 'viewer', 'workspace-monitor-queue')
    audit(db, p, QUEUED, created, _queue_anchor(created, pin))
    return created


def schedule_once(workspace_id, *, _stop_event=None):
    """Queue policy checks for exactly one workspace, without model/device IO."""
    workspace_id = workspace(workspace_id)
    if _stop_event is not None and _stop_event.is_set():
        return 0
    created = 0
    cursor = None
    while True:
        if _stop_event is not None and _stop_event.is_set():
            break
        with Session(engine()) as db:
            query = select(MonitorPolicy.id, MonitorPolicy.created_at).where(
                MonitorPolicy.workspace_id == workspace_id, MonitorPolicy.status == 'active')
            if cursor is not None:
                query = query.where(tuple_(MonitorPolicy.created_at, MonitorPolicy.id) > tuple_(*cursor))
            candidates = list(db.execute(query.order_by(MonitorPolicy.created_at, MonitorPolicy.id).limit(64)))
        if not candidates:
            break
        for candidate in candidates:
            if _stop_event is not None and _stop_event.is_set():
                return created
            with Session(engine()) as db:
                result = _enqueue(db, workspace_id, candidate.id, _stop_event=_stop_event)
                if _stop_event is not None and _stop_event.is_set():
                    db.rollback()
                    return created
                db.commit()
                created += int(result is not None)
        cursor = (candidates[-1].created_at, candidates[-1].id)
        if len(candidates) < 64:
            break
    return created


def _original(db, p, job):
    pin = job.data.get(KEY)
    need(type(pin) is dict and set(pin) == {'schema', 'workspace_id', 'policy_id',
        'policy_version', 'approved_hash', 'actor_id', 'schedule_hint'}
        and pin['schema'] == PIN and pin['workspace_id'] == job.workspace_id == p.workspace_id
        and job.kind == KIND and pin['actor_id'] == job.data.get('actor_id') == p.user_id
        and pin['policy_id'] == job.data.get('policy_id') and job.data.get('request_hash') == digest(pin)
        and job.idempotency_key == request_key(pin))
    hint = pin['schedule_hint']
    need(type(hint) is dict and set(hint) in ({'mode', 'head', 'due_at', 'interval_seconds', 'context_hash'},
        {'mode', 'head', 'due_at', 'interval_seconds', 'context_hash','control'})
        and hint['mode'] in {'legacy', 'schedule', 'suppressed'}
        and hint['context_hash'] == digest({**{name: pin[name] for name in (
            'workspace_id', 'policy_id', 'policy_version', 'approved_hash', 'actor_id')},
            'head': hint['head'], 'mode': hint['mode'],
            **({'control':hint['control']} if 'control' in hint else {})}))
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == job.workspace_id,
        Audit.object_id == job.id, Audit.action == QUEUED)))
    need(len(events) == 1 and events[0].actor_id == pin['actor_id']
        and events[0].data == _queue_anchor(job.id, pin))
    return copy.deepcopy(pin)


def _saved_result(db, p, job):
    value = job.data.get(RESULT_KEY)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == job.workspace_id,
        Audit.object_id == job.id, Audit.action == CHECKED)))
    if value is None:
        need(not events)
        return None
    need(type(value) is dict and value.get('schema') == RESULT
        and value.get('job_id') == job.id and value.get('workspace_id') == job.workspace_id
        and value.get('policy_id') == job.data['policy_id'] and value.get('request_hash') == job.data['request_hash']
        and value.get('record_hash') == digest({k: v for k, v in value.items() if k != 'record_hash'})
        and len(events) == 1 and events[0].actor_id == p.user_id and events[0].data == value)
    return copy.deepcopy(value)


def _mode_blocked(db,p,job,policy,pin,reason,at):
    value={'schema':RESULT,'workspace_id':p.workspace_id,'job_id':job.id,'policy_id':policy.id,
        'request_hash':digest(pin),'checked_at':_iso(at),'state':'blocked','reason':reason,
        'schedule_consumed':False,'policy_check_performed':False,'acquisition_ids':[],'next_due':None,
        'physical_state_verified':False}
    value['record_hash']=digest(value);job.data={**job.data,RESULT_KEY:value}
    audit(db,p,CHECKED,job.id,copy.deepcopy(value));db.flush();_saved_result(db,p,job)
    return copy.deepcopy(value)


def _process(db, p, job):
    pin = _original(db, p, job)
    saved = _saved_result(db, p, job)
    if saved is not None:
        return saved  # committed first check; never consume/propose it again
    policy = db.scalar(select(MonitorPolicy).where(MonitorPolicy.workspace_id == p.workspace_id,
        MonitorPolicy.id == pin['policy_id']).execution_options(populate_existing=True))
    need(policy is not None and policy.status == 'active' and policy.approved_by == pin['actor_id']
        and policy.version == pin['policy_version'] and policy.data.get('approved_hash') == pin['approved_hash'],
        '原监测策略已暂停、撤权或重新批准；旧检查任务保留，未安排设备动作')
    at = db.scalar(select(func.clock_timestamp()))
    need(_time(at) >= _time(pin['schedule_hint']['due_at']))
    from .monitor_scheduling_service_v253 import periodic_gate
    gate=periodic_gate(db,p,policy,expected=pin['schedule_hint'].get('control'),check_expected=True)
    if not gate['allowed']:return _mode_blocked(db,p,job,policy,pin,gate['reason'],at)
    from . import workflow_monitor_schedule_v200 as monitor
    before = _head(db, p.workspace_id, policy.id)
    previous = _head_pin(before)
    handled = monitor.consume_policy_tick(db, p, policy,
        expected_head_pin=pin['schedule_hint']['head'],expected_control_pin=pin['schedule_hint'].get('control'), expected_policy_pin={
            'workspace_id': pin['workspace_id'], 'policy_id': pin['policy_id'],
            'version': pin['policy_version'], 'approved_hash': pin['approved_hash'],
            'approved_by': pin['actor_id'], 'status': 'active'})
    need(type(handled) is bool)
    gate=periodic_gate(db,p,policy,expected=pin['schedule_hint'].get('control'),check_expected=True)
    if not gate['allowed']:return _mode_blocked(db,p,job,policy,pin,gate['reason'],at)
    created = []; state = 'checked'; reason = None; next_due = None
    if handled is False:
        # Only the original consumer proves genuine legacy/explicit restore.
        # Missing/tampered head records or cancelled schedules cannot imply it.
        from .perception import propose_acquisitions
        created = propose_acquisitions(db, p, policy.id)
    else:
        head, _installs, cycles = monitor._checked(db, p, policy.id)
        need(head is not None)
        next_due = _iso(head.next_due) if head.next_due is not None else None
        if head.status == 'active' and previous == _head_pin(head):
            state = 'not_due'
        elif head.status != 'active':
            state = 'cancelled' if head.status == 'cancelled' else 'blocked'
            reason = '原监测检查已停止、到期或受阻；原记录保留，未恢复旧策略或派发动作'
        if previous != _head_pin(head) and cycles and head.data.get('cycle_ids'):
            original = next(row for row in cycles if row.id == head.data['cycle_ids'][-1])
            if original.data['head_version'] == head.version:
                created = copy.deepcopy(original.data['acquisition_ids'])
                reason = original.data.get('reason') or reason
    need(type(created) is list and len(created) == len(set(created))
        and all(type(value) is str for value in created))
    value = {'schema': RESULT, 'workspace_id': p.workspace_id, 'job_id': job.id,
        'policy_id': policy.id, 'request_hash': digest(pin), 'checked_at': _iso(at),
        'state': state, 'reason': reason, 'schedule_consumed': handled,
        'policy_check_performed': state == 'checked', 'acquisition_ids': created,
        'next_due': next_due, 'physical_state_verified': False}
    value['record_hash'] = digest(value)
    job.data = {**job.data, RESULT_KEY: value}
    audit(db, p, CHECKED, job.id, copy.deepcopy(value))
    db.flush()
    _saved_result(db, p, job)
    return copy.deepcopy(value)


def process(db, p, job):
    try:
        return _process(db, p, job)
    except HTTPException as error:
        from .workflow_abilities import WorkflowBlocked
        raise WorkflowBlocked(str(error.detail)) from None
