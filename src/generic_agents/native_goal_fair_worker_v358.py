"""Opt-in task-scoped fairness. State is a hint; original claim is authoritative."""
from contextlib import nullcontext
from datetime import datetime, timedelta
from time import monotonic
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from . import models as m
from .contracts import digest
from .db import engine
from .native_goal_fair_state_v358 import NativeGoalFairStateV358, require_schema
from .native_goal_scoped_worker_v355 import scope, _rows, _prove, _pin
from .native_goal_time import timer
from .workflow_task_consumer_v265 import _created
from .native_goals import KIND

LANES = ('recovery_timer', 'promotion', 'candidate', 'auto_append', 'ordinary')
PAGE = 50
BUDGET_SECONDS = 0.5
RESCAN_SECONDS = 2


def _uuid(value):
    try: return type(value) is str and str(UUID(value)) == value
    except (TypeError, ValueError, AttributeError): return False


def _clock(value):
    if type(value) is not str: raise ValueError('invalid scheduler clock')
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None: raise ValueError('invalid scheduler clock')
    return result


def _initial(workspace, instance, task):
    return {'workspace': workspace, 'instance': instance, 'task': task, 'next_lane': 0,
            'lanes': {name: {'horizon': None, 'cursor': None} for name in LANES}}


def _validate(data, workspace, instance, task, now=None):
    if (type(data) is not dict or set(data) != {'workspace','instance','task','next_lane','lanes'}
        or (data['workspace'], data['instance'], data['task']) != (workspace, instance, task)
        or type(data['next_lane']) is not int or not 0 <= data['next_lane'] < len(LANES)
        or type(data['lanes']) is not dict or set(data['lanes']) != set(LANES)):
        raise ValueError('公平调度状态范围或格式错误')
    for lane in data['lanes'].values():
        if type(lane) is not dict or set(lane) != {'horizon','cursor'}: raise ValueError('invalid lane')
        horizon = _clock(lane['horizon']) if lane['horizon'] is not None else None
        if horizon is not None and now is not None and horizon > now:
            raise ValueError('invalid future horizon')
        cursor = lane['cursor']
        if cursor is not None:
            if type(cursor) is not list or len(cursor) != 2 or horizon is None or not _uuid(cursor[1]):
                raise ValueError('invalid scheduling cursor')
            if _clock(cursor[0]) > horizon: raise ValueError('invalid cursor horizon')


def fair_tick(workspace, instance, task_id, stop):
    """At most one real claim; failure, missing schema or invalid state is closed."""
    from .worker import process_once
    from .native_goal_scoped_authority_v355 import selected
    scope(workspace, instance, task_id)
    if stop.is_set(): return {'processed': 0, 'blocked': 0, 'waiting': 0, 'retry_mode': 'stopped'}
    chosen = None; blocked = 0; waiting = 0; scanned = 0; active = False
    with Session(engine()) as db:
        # The fairness state and all proof reads must resolve to the verified
        # application schema; pg_temp is explicitly after public.
        db.execute(text('SET LOCAL search_path = public, pg_temp'))
        db.execute(text("SET LOCAL statement_timeout = '750ms'"))
        db.execute(text("SET LOCAL lock_timeout = '500ms'"))
        require_schema(db.connection())
        lock = int(digest({'scope':'native-goal-fair-v358','workspace':workspace,'task':task_id})[:16],16)-(1<<63)
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':lock})
        task = db.scalar(select(m.Task).where(m.Task.workspace_id == workspace, m.Task.id == task_id))
        if task is None: raise HTTPException(404, '原业务任务不存在')
        _created(db, workspace, instance, task, None)
        state = db.scalar(select(NativeGoalFairStateV358).where(NativeGoalFairStateV358.workspace_id == workspace,
                          NativeGoalFairStateV358.task_id == task_id).with_for_update())
        now = db.scalar(select(func.clock_timestamp()))
        if state is None:
            state = NativeGoalFairStateV358(workspace_id=workspace, task_id=task_id, instance_id=instance,
                                           data=_initial(workspace, instance, task_id), version=1)
            db.add(state); db.flush()
        if state.instance_id != instance or type(state.version) is not int or state.version < 1:
            raise ValueError('公平调度状态范围错误')
        from copy import deepcopy
        data = deepcopy(state.data); _validate(data, workspace, instance, task_id, now)
        if state.next_due_at is not None and state.next_due_at > now + timedelta(seconds=RESCAN_SECONDS):
            raise ValueError('公平调度重扫时间超出上限')
        if state.next_due_at is not None and state.next_due_at > now:
            return {'processed':0, 'blocked':0, 'waiting':0, 'retry_mode':'scheduled_rescan'}
        pin = _pin(task); clock_timer = timer(task)
        deadline = monotonic() + BUDGET_SECONDS
        for _ in range(len(LANES)):
            if stop.is_set() or monotonic() >= deadline: active = True; break
            name = LANES[data['next_lane']]; data['next_lane'] = (data['next_lane'] + 1) % len(LANES)
            lane = data['lanes'][name]
            if lane['horizon'] is None: lane['horizon'] = now.isoformat()
            horizon = _clock(lane['horizon'])
            after = (_clock(lane['cursor'][0]), lane['cursor'][1]) if lane['cursor'] else None
            rows = _rows(db, workspace, task, pin, clock_timer, after, lane=name, horizon=horizon, limit=PAGE)
            scanned += 1; exhausted = True
            for identity, available in rows:
                if stop.is_set() or monotonic() >= deadline: active = True; exhausted = False; break
                lane['cursor'] = [available.isoformat(), identity]
                try: proof = _prove(db, workspace, instance, task_id, identity)
                except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                    blocked += 1; continue
                if proof is None: waiting += 1; continue
                chosen = (identity, *proof); active = True; exhausted = False; break
            if exhausted and len(rows) < PAGE: lane['cursor'] = lane['horizon'] = None
            elif rows: active = True
            if chosen: break
        # A complete snapshot is not proof of an empty queue. The persistent
        # next deadline bounds idle retries; a restart retains the deadline.
        state.next_due_at = None if active else now + timedelta(seconds=RESCAN_SECONDS)
        state.data = data; state.version += 1; db.commit()
    processed = 0
    if chosen and not stop.is_set():
        identity,kind,family,value = chosen
        context = selected(workspace,task_id,identity,family,value) if kind == KIND else nullcontext()
        with context:
            processed = int(bool(process_once(job_id=identity,kinds=[kind],deliver=False,workspace_id=workspace,_stop_event=stop)))
    return {'processed':processed,'blocked':blocked,'waiting':waiting,'retry_mode':'continue_bounded' if active else 'scheduled_rescan','pages':scanned}
