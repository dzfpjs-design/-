"""Source-bound task waits; no legacy Event or caller-supplied output path.

The task transaction owns its Task/Job locks before taking a short independent
stream cut. A durable poll is saved alongside every open frontier, covering
admissions committed between that cut and the new window's commit.
"""
import copy
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, func

from .contracts import digest
from .models import Job

KIND = 'drain_full_semantic_graph_admissions'
POLL_SECONDS = 5
AUTHORITY = 'graph_wait_authority'
BINDING_SCHEMA = 'report-full-semantic-graph-binding-v4'
BINDING_SCHEMAS = {BINDING_SCHEMA, 'report-object-graph-binding-v171'}


def enabled(binding):
    return binding.get('schema') in BINDING_SCHEMAS


def scope(task):
    values = task.data.get('entity_ids')
    snapshots = task.data.get('object_snapshots')
    if (type(values) is not list or not values or len(set(values)) != len(values)
            or type(snapshots) is not list or len(snapshots) != len(values)
            or {x.get('id') for x in snapshots if type(x) is dict} != set(values)):
        raise HTTPException(409, '请先明确任务管理的对象；等待消息不能猜测或扩大对象范围')
    return list(values), digest(snapshots)


def checked(task, binding):
    from . import graph_wait_kernel_v118 as kernel
    if not enabled(binding):
        if AUTHORITY in task.data:
            raise HTTPException(409, '任务的来源等待合同不能降级，原记录保留')
        return None
    entities, scope_hash = scope(task)
    try:
        if task.data.get('graph_wait_sources') != source_index(binding, scope_hash):
            raise ValueError('wait_source_index')
        authority = kernel.check(binding['graph'], task.data['graph_runtime'], task.data.get(AUTHORITY),
            binding_hash=binding['binding_hash'], entity_ids=entities, scope_hash=scope_hash)
        by_kind = {s['event_type']: binding['event_source_bindings'][s['id']]
            for s in binding['graph']['subscriptions']}
        # The pure engine does not know published producer identities; retain
        # that immutable binding check here on every historical or live read.
        for value in authority['receipts'].values():
            pin = by_kind.get(value['event_type'])
            if (pin is None or value['producer_id'] != pin['producer_id']
                    or value['contract_hash'] != pin['contract_hash']):
                raise ValueError('wait_receipt_source_pin')
        return authority
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(409, '任务等待窗口、来源或对象范围无法核对，原记录保留') from None


def source_index(binding, scope_hash):
    return {'binding_hash': binding['binding_hash'], 'scope_hash': scope_hash,
        'producer_ids': sorted({v['producer_id'] for v in binding['event_source_bindings'].values()})}


def initialize(db, p, task, binding, values, *, _observer=None):
    from .graph_wait_sources_v118 import verify_bindings, check_policy, take_cut
    from . import graph_wait_kernel_v118 as kernel
    entities, scope_hash = scope(task)
    check_policy(binding['event_admission_policy'])
    verify_bindings(db, p, binding['graph'], binding['event_source_bindings'], lock=True)
    cut = take_cut(db, p)
    try:
        state, authority = kernel.new_run(binding['graph'], values, cut,
            binding_hash=binding['binding_hash'], entity_ids=entities, scope_hash=scope_hash, _observer=_observer)
    except ValueError as error:
        raise HTTPException(409, '等待窗口尚不能建立，原记录保留：' + str(error)) from None
    task.data = {**task.data, AUTHORITY: authority, 'graph_wait_sources': source_index(binding, scope_hash)}
    return state


def transition(db, p, task, binding, *, operation='advance', command=None, _observer=None):
    from .graph_wait_sources_v118 import verify_bindings, check_policy, take_cut, read_receipts
    from . import graph_wait_kernel_v118 as kernel
    from .workflow_job_dependencies_v264 import trace_for, save_control
    owned_observer = _observer is None
    if owned_observer: _observer = trace_for(db, p, task)
    authority = checked(task, binding)
    check_policy(binding['event_admission_policy'])
    verify_bindings(db, p, binding['graph'], binding['event_source_bindings'], lock=True)
    cut = take_cut(db, p)
    receipts = read_receipts(db, p, task, binding['graph'], binding['event_source_bindings'],
        after=authority['cursor']['sequence'], cut=cut)
    try:
        state, result = kernel.transition(binding['graph'], task.data['graph_runtime'], authority,
            cut, receipts, scope_hash=authority['scope_hash'], operation=operation, command=command, _observer=_observer)
    except ValueError as error:
        raise HTTPException(409, '等待窗口暂未推进，原记录保留：' + str(error)) from None
    data = {**task.data, AUTHORITY: result}; data.pop('graph_wait_error', None)
    task.data = data
    if owned_observer and _observer is not None:
        task.data = {**task.data, 'graph_runtime': state}
        save_control(db, p, task, state, _observer)
    return state


def sync(task, binding, state):
    """Save/cancel is available without current source access and never drains."""
    from . import graph_wait_kernel_v118 as kernel
    authority = checked(task, binding)
    try:
        result = kernel.sync(binding['graph'], state, authority, authority['cursor'],
            scope_hash=authority['scope_hash'])
    except ValueError as error:
        raise HTTPException(409, '原等待窗口无法核对，记录保留：' + str(error)) from None
    task.data = {**task.data, AUTHORITY: result}


def replay(task, binding, command):
    """Check a saved complete nonce without sources, a new cut, or dispatch."""
    from . import graph_wait_kernel_v118 as kernel
    authority = checked(task, binding)
    if not any(c['request_id'] == command['request_id'] for c in task.data['graph_runtime']['commands']):
        raise HTTPException(409, '原完成请求不存在，不能作为历史恢复')
    try:
        state, result = kernel.transition(binding['graph'], task.data['graph_runtime'], authority,
            authority['cursor'], [], scope_hash=authority['scope_hash'], operation='complete', command=command)
        if state != task.data['graph_runtime'] or result != authority:
            raise ValueError('historical_replay_changed_state')
    except ValueError as error:
        raise HTTPException(409, '原完成请求不一致，记录保留：' + str(error)) from None


def schedule(db, p, task, binding, state):
    authority = checked(task, binding)
    if authority is None or state['status'] in {'cancelled', 'succeeded', 'stopped', 'failed', 'blocked'}:
        return
    windows = [w for w in authority['windows'].values() if w['status'] == 'open']
    if not windows:
        return
    available = min(authority['cursor']['at'] + POLL_SECONDS, *(w['deadline'] for w in windows))
    previous = task.data.get('graph_wait_poll', {})
    generation = previous.get('generation', 0)
    if type(generation) is not int or not 0 <= generation < (1 << 63) - 1:
        raise HTTPException(409, '等待消息的检查次数无法核对，原记录保留')
    generation += 1
    key = 'graph-admissions:' + task.id + ':' + authority['authority_hash'] + ':' + str(generation)
    data = {'actor_id': task.owner_id, 'task_id': task.id, 'graph_hash': state['graph_hash'],
        'binding_hash': binding['binding_hash'], 'authority_hash': authority['authority_hash'],
        'cancel_epoch': state['cancel_epoch'], 'generation': generation}
    prior = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.idempotency_key == key))
    if prior is None:
        prior = Job(workspace_id=p.workspace_id, kind=KIND, status='queued', idempotency_key=key,
            available_at=datetime.fromtimestamp(available, timezone.utc), data=data)
        db.add(prior); db.flush()
    elif (prior.kind != KIND or any(prior.data.get(k) != v for k, v in data.items())):
        raise HTTPException(409, '等待消息的原后台检查任务无法核对')
    task.data = {**task.data, 'graph_wait_poll': {'job_id': prior.id,
        'authority_hash': authority['authority_hash'], 'generation': generation, 'available_at': available}}


def process(db, p, job, task, release, business, binding):
    """The caller already owns the worker lease and the actual Task row."""
    from . import workflow_graph as runtime
    authority = checked(task, binding)
    state = task.data['graph_runtime']
    generation = job.data.get('generation')
    if (job.kind != KIND or job.workspace_id != p.workspace_id or job.data.get('actor_id') != p.user_id
            or job.data.get('task_id') != task.id or job.data.get('graph_hash') != state['graph_hash']
            or job.data.get('binding_hash') != binding['binding_hash']
            or type(generation) is not int or not 1 <= generation < (1 << 63)
            or job.idempotency_key != ('graph-admissions:' + task.id + ':'
                + job.data.get('authority_hash', '') + ':' + str(generation))):
        raise HTTPException(409, '消息检查任务与实际流程或后台账号不一致')
    if (job.data.get('cancel_epoch') != state['cancel_epoch']
            or state['status'] in {'cancelled', 'succeeded', 'stopped', 'failed'}):
        return 'cancelled'
    # Superseded polls are harmless; the new frontier and its poll were saved
    # in the same Task transaction. They cannot manufacture a new window.
    pointer = task.data.get('graph_wait_poll', {})
    if (job.data['authority_hash'] != authority['authority_hash']
            or pointer.get('job_id') != job.id or pointer.get('generation') != generation):
        return 'succeeded'
    runtime.live(db, p, task, release, business)
    result = transition(db, p, task, binding)
    runtime.settle(db, p, task, release, business, result)
    return 'succeeded'


def wake_admission(db, p, result):
    """Expedite existing polls after admission commit; never execute a Task.

    Missing a not-yet-committed Task is safe: its durable periodic poll is
    committed with its window. No Task/source/cursor lock is acquired here.
    """
    from .models import Task
    from .graph_event_models import GraphEventAdmission
    row = db.get(GraphEventAdmission, result['admission_id'])
    if row is None or row.workspace_id != p.workspace_id or row.sequence != result['sequence']:
        raise HTTPException(409, '消息接纳后唤醒的原回执无法核对')
    entity = row.data['projection']['correlation']
    pointers = db.execute(select(Task.id, Task.data['graph_wait_poll']).where(
        Task.workspace_id == p.workspace_id, Task.status == 'workflow_waiting',
        Task.data['entity_ids'].contains([entity]),
        Task.data['graph_wait_sources']['producer_ids'].contains([row.producer_id]))
        .order_by(Task.id)).all()
    woken = 0
    for task_id, pointer in pointers:
        if type(pointer) is not dict: continue
        job = db.scalar(select(Job).where(Job.id == pointer.get('job_id'),
            Job.workspace_id == p.workspace_id, Job.kind == KIND, Job.status == 'queued')
            .with_for_update(skip_locked=True))
        if (job is None or job.data.get('task_id') != task_id
                or job.data.get('authority_hash') != pointer.get('authority_hash')):
            continue
        job.available_at = func.clock_timestamp(); job.version += 1; woken += 1
    return woken


def public(task, binding, p=None):
    authority = checked(task, binding)
    if authority is None:
        return None
    error = copy.deepcopy(task.data.get('graph_wait_error'))
    can_retry = False
    if error:
        from sqlalchemy.orm import object_session
        db = object_session(task)
        job = db.get(Job, error.get('job_id')) if db is not None else None
        if job is not None and job.workspace_id == task.workspace_id and job.data.get('task_id') == task.id:
            error.update(job_version=job.version, status=job.status, attempts=job.attempts)
            can_retry = bool(p and p.role in {'admin', 'operator'} and (p.role == 'admin' or p.user_id == task.owner_id)
                and job.kind == KIND and job.status in {'failed', 'blocked'}
                and task.data.get('graph_wait_poll', {}).get('job_id') == job.id
                and task.data['graph_runtime']['status'] not in {'cancelled', 'succeeded', 'stopped', 'failed'})
    return {'arrival_basis': 'durable_admission', 'last_checked_at': authority['cursor']['at'],
        'error': error, 'can_retry': can_retry,
        'windows': [{key: copy.deepcopy(value[key]) for key in (
            'activation_id', 'status', 'opened_at', 'deadline', 'closed_at', 'event_id')}
            for value in authority['windows'].values()],
        'candidate_only': True, 'physical_state_verified': False}
