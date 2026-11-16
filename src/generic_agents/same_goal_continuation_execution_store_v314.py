"""Approval and receipt custody for a single continuation read-tool allocation."""
import copy

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError

from . import layered_approval_store_v175 as approval
from .layered_approval_contract_v175 import independent
from . import same_goal_continuation_execution_contracts_v314 as c
from . import same_goal_continuation_execution_sources_v314 as sources
from . import same_goal_continuation_grant_store_v313 as grants
from . import workspace_command_nonce_lock_v312 as nonce
from .contracts import digest
from .models import Audit, Invocation, Job, Plan, uid
from .relations import lock_current_actor

need, checked = grants.need, grants.checked
_reader = grants.drafts._reader
_task = grants.drafts.existing._task
_id = grants.drafts.existing._id
_clock = grants._clock
_rows = grants._rows
_ref = grants._ref
OUTPUT = 'same_goal_continuation.execution_output_v314'
RECEIVED = 'same_goal_continuation.execution_received_v314'
_OPERATIONS = {
    'submit': (c.ACTION_SUBMIT, c.SubmitRequest),
    'decide': (c.ACTION_DECIDE, c.DecideRequest),
    'start': (c.ACTION_START, c.StartRequest),
    'cancel': (c.ACTION_CANCEL, c.CancelRequest),
    'review': (c.ACTION_REVIEW, c.ReviewRequest),
    'recover': (c.ACTION_RECOVER, c.RecoverRequest),
}
_PLAN_KEYS = {'schema', 'proposal', 'operations', 'output_ref', 'receipt_ref'}
_COMMAND_KEYS = {'schema', 'operation', 'workspace_id', 'task_id', 'advice_plan_id',
    'continuation_id', 'plan_id', 'actor_id', 'request_id', 'request', 'body_hash',
    'submission_hash', 'before', 'after', 'phase', 'detail', 'record_hash'}


def _same(left, right):
    return digest(left) == digest(right)


def _key(plan_id):
    return 'continuation-read-v314:' + plan_id + ':1'


def _job_data(proposal, plan_id, actor_id):
    return {'schema': c.JOB, 'task_id': proposal['task_id'], 'plan_id': plan_id,
        'actor_id': actor_id, 'submission_hash': proposal['record_hash'],
        'input_hash': proposal['grant']['basis']['input_hash'], 'invocation_key': _key(plan_id)}


def _command(event, p):
    value = event.data
    need(type(value) is dict and set(value) == _COMMAND_KEYS and value['schema'] == c.COMMAND
        and value['operation'] in _OPERATIONS and event.action == _OPERATIONS[value['operation']][0]
        and event.workspace_id == value['workspace_id'] == p.workspace_id
        and event.actor_id == value['actor_id'] and event.object_id == value['plan_id']
        and value['record_hash'] == c.record_hash(value))
    for key in ('task_id', 'advice_plan_id', 'continuation_id', 'plan_id', 'actor_id', 'request_id'):
        _id(value[key])
    request = c.dump(_OPERATIONS[value['operation']][1], value['request'])
    need(_same(request, value['request']) and value['request_id'] == request['request_id']
        and value['body_hash'] == digest(request) and type(value['before']) is int
        and type(value['after']) is int and value['after'] == value['before'] + 1
        and type(value['detail']) is dict)
    if value['operation'] != 'submit':
        need(request['expected_execution_version'] == value['before'])
    return value


def _independent(actor, proposal, approvers=(), executors=(), *, acceptance=False):
    try:
        independent(actor, proposal['business_actor_id'], approvers, executors, acceptance=acceptance)
        independent(actor, proposal['submitter_id'], approvers, executors, acceptance=acceptance)
    except ValueError as error:
        raise HTTPException(403, str(error)) from None


def _transition(value, proposal, phase, approvers, start):
    operation, actor = value['operation'], value['actor_id']
    request, detail = value['request'], value['detail']
    stages = proposal['grant']['basis']['policy']['spec']['stages']
    if operation == 'submit':
        need(phase is None and value['before'] == 0 and actor == proposal['submitter_id']
            and detail == {} and request['preview_hash'] == proposal['record_hash']
            and _same(request['grant_ref'], proposal['grant_ref'])
            and request['source_hash'] == proposal['source_hash']
            and request['revision_hash'] == proposal['continuation_ref']['revision_hash']
            and request['expected_continuation_version'] == proposal['continuation_ref']['version']
            and request['supersedes_plan_id'] == proposal['supersedes_plan_id']
            and request['rationale'] == proposal['rationale'])
        return 'in_review'
    if operation == 'decide':
        need(phase == 'in_review' and len(approvers) < len(stages) and actor not in approvers
            and request['submission_hash'] == proposal['record_hash']
            and set(detail) == {'stage', 'role_ref'} and type(detail['stage']) is int
            and detail['stage'] == len(approvers))
        _independent(actor, proposal)
        if request['decision'] == 'return':
            return 'returned'
        approvers.append(actor)
        return 'approved' if len(approvers) == len(stages) else 'in_review'
    if operation == 'start':
        need(phase == 'approved' and start is None and len(approvers) == len(stages)
            and set(detail) == {'job_id', 'job_data', 'role_ref'}
            and _same(detail['job_data'], _job_data(proposal, value['plan_id'], actor)))
        _id(detail['job_id'])
        return 'queued'
    if operation == 'cancel':
        need(phase in {'in_review', 'approved', 'queued', 'cancel_requested'}
            and set(detail) == {'queued_job_stopped'} and type(detail['queued_job_stopped']) is bool
            and (start is not None or not detail['queued_job_stopped']))
        return 'cancelled' if start is None or detail['queued_job_stopped'] else 'cancel_requested'
    if operation == 'recover':
        need(phase in {'queued', 'cancel_requested'} and start is not None and detail == {})
        return phase
    need(operation == 'review' and phase in {'queued', 'cancel_requested'} and start is not None
        and set(detail) == {'output_ref', 'receipt_ref', 'role_ref'})
    _independent(actor, proposal, approvers, [start.actor_id], acceptance=True)
    return 'adopted' if request['decision'] == 'adopt' else 'result_returned'


def _saved(plan, events, p, task):
    data = plan.data
    need(plan.workspace_id == p.workspace_id and plan.task_id == task.id and type(data) is dict
        and set(data) == _PLAN_KEYS and data['schema'] == c.PLAN
        and plan.approved_by is None and plan.approved_hash is None
        and type(data['operations']) is list and bool(data['operations']))
    proposal = c.check_proposal(data['proposal'])
    need(proposal['workspace_id'] == p.workspace_id and proposal['task_id'] == task.id)
    indexed = {row.id: row for row in events}
    need(len(indexed) == len(events) == len(data['operations']))
    phase = None
    version = 0
    start = None
    approvers = []
    requests = set()
    for pointer in data['operations']:
        event = indexed[pointer['id']]
        value = _command(event, p)
        need(pointer == {**_ref(event), 'request_id': value['request_id']}
            and value['task_id'] == task.id and value['plan_id'] == plan.id
            and value['advice_plan_id'] == proposal['advice_plan_id']
            and value['continuation_id'] == proposal['continuation_ref']['id']
            and value['submission_hash'] == proposal['record_hash'] and value['before'] == version
            and value['request_id'] not in requests)
        requests.add(value['request_id'])
        try:
            phase = _transition(value, proposal, phase, approvers, start)
        except HTTPException as error:
            if error.status_code != 403:
                raise
            raise HTTPException(409, '已保存审批链的独立责任人无法核对') from None
        need(value['phase'] == phase)
        if value['operation'] == 'start':
            start = event
        version = value['after']
    need(phase == plan.status and type(plan.version) is int and version == plan.version)
    return {'plan': plan, 'proposal': proposal, 'start': start, 'approvers': approvers,
        'events': indexed, 'job': None}


@checked
def _binding(submit_event):
    return {'plan_id': submit_event.object_id,
        'submission_hash': submit_event.data['submission_hash'], 'submit_ref': _ref(submit_event)}


def _inventory(db, p, task):
    scope = or_((Plan.task_id == task.id) & (Plan.data['schema'].as_string() == c.PLAN),
        Plan.data['proposal']['task_id'].as_string() == task.id)
    plans = _rows(db, p, Plan, scope)
    events = _rows(db, p, Audit, Audit.action.in_(c.ACTIONS), or_(
        Audit.data['task_id'].as_string() == task.id, Audit.object_id.in_([row.id for row in plans])))
    values = [_command(row, p) for row in events]
    need(all(value['task_id'] == task.id for value in values))
    plans = _rows(db, p, Plan, or_(scope, Plan.id.in_([value['plan_id'] for value in values])))
    need({row.id for row in plans} == {value['plan_id'] for value in values}
        and len({value['request_id'] for value in values}) == len(values))
    saved = [_saved(plan, [event for event in events if event.object_id == plan.id], p, task) for plan in plans]
    bindings = task.data.get(c.INDEX, [])
    expected = {event.object_id: _binding(event) for event in events if event.action == c.ACTION_SUBMIT}
    need(type(bindings) is list and len(bindings) == len(expected) == len(plans),
        '任务的办理执行关联无法核对')
    for binding in bindings:
        need(type(binding) is dict and type(binding.get('plan_id')) is str
            and _same(binding, expected.pop(binding['plan_id'], None)), '任务的办理执行关联无法核对')
    jobs = _rows(db, p, Job, or_((Job.kind == c.KIND) & (Job.data['task_id'].as_string() == task.id),
        Job.data['plan_id'].as_string().in_([row.id for row in plans]),
        Job.idempotency_key.in_([_key(row.id) for row in plans]),
        Job.id.in_([row['start'].data['detail']['job_id'] for row in saved if row['start']])))
    grants_used = set()
    job_ids = set()
    for row in saved:
        if row['start'] is None:
            continue
        proposal, plan = row['proposal'], row['plan']
        detail = row['start'].data['detail']
        matches = [job for job in jobs if job.id == detail['job_id'] and job.idempotency_key == _key(plan.id)]
        need(len(matches) == 1)
        job = matches[0]
        need(job.kind == c.KIND
            and all(_same(job.data.get(key), value) for key, value in detail['job_data'].items())
            and (plan.status != 'cancelled' or job.status == 'cancelled'))
        grant_id = proposal['grant_ref']['id']
        need(grant_id not in grants_used and job.id not in job_ids)
        grants_used.add(grant_id)
        job_ids.add(job.id)
        row['job'] = job
    need(job_ids == {job.id for job in jobs})
    return saved


def accounting(db, p, task):
    """Original reservations and Job custody. This never performs a live guard."""
    with db.no_autoflush:
        return [{**row, 'invocation_key': row['job'].idempotency_key,
            'capability_pin': copy.deepcopy(row['proposal']['grant']['basis']['capability_pin']),
            'input_hash': row['proposal']['grant']['basis']['input_hash'],
            'submission_hash': row['proposal']['record_hash']}
            for row in _inventory(db, p, task) if row['start']]


def _find(inventory, advice_id, continuation_id, plan_id):
    rows = [row for row in inventory if row['plan'].id == _id(plan_id)
        and row['proposal']['advice_plan_id'] == advice_id
        and row['proposal']['continuation_ref']['id'] == continuation_id]
    if not rows:
        raise HTTPException(404, '原执行申请不存在或不属于此办理草案')
    need(len(rows) == 1)
    return rows[0]


def _original(db, p, request_id):
    request_id = _id(request_id)
    events = _rows(db, p, Audit, Audit.action.in_(c.ACTIONS), or_(
        Audit.data['request_id'].as_string() == request_id,
        Audit.data['request']['request_id'].as_string() == request_id))
    pointers = _rows(db, p, Plan, Plan.data['operations'].contains([{'request_id': request_id}]))
    need(len(events) <= 1)
    if not events:
        need(not pointers)
        return None
    value = _command(events[0], p)
    need(len(pointers) <= 1 and all(row.id == value['plan_id'] for row in pointers))
    return value


def _scope(value, task, advice_id, continuation_id):
    need(value['task_id'] == task.id and value['advice_plan_id'] == advice_id
        and value['continuation_id'] == continuation_id)


def _invocations(db, p, saved):
    job = saved['job']
    if job is None:
        return []
    rows = _rows(db, p, Invocation, or_(Invocation.idempotency_key == job.idempotency_key,
        Invocation.data['job_id'].as_string() == job.id))
    need(len(rows) <= 1 and all(row.idempotency_key == job.idempotency_key for row in rows))
    return rows


def _results(db, p, task, saved, *, verify=True):
    plan = saved['plan']
    result = {}
    for field, action in [('output_ref', OUTPUT), ('receipt_ref', RECEIVED)]:
        rows = _rows(db, p, Audit, Audit.action == action, Audit.object_id == plan.id)
        ref = plan.data[field]
        if ref is None:
            need(not rows)
            result[field] = None
            continue
        need(len(rows) == 1 and ref == _ref(rows[0]) and saved['job'] is not None)
        event = rows[0]
        value = event.data
        need(value['task_id'] == task.id and value['plan_id'] == plan.id
            and value['job_id'] == saved['job'].id and value['submission_hash'] == saved['proposal']['record_hash'])
        result[field] = event
    output, receipt = result['output_ref'], result['receipt_ref']
    need(receipt is None or (output is not None and receipt.data['output_ref'] == _ref(output)))
    if output is not None:
        need(set(output.data) == {'task_id', 'plan_id', 'job_id', 'invocation_id',
            'submission_hash', 'output', 'output_hash'}
            and output.actor_id == saved['start'].actor_id
            and output.data['output_hash'] == digest(output.data['output']))
        if receipt is not None:
            need(set(receipt.data) == {'task_id', 'plan_id', 'job_id', 'submission_hash', 'output_ref', 'ack'})
        if verify:
            from . import same_goal_continuation_execution_runtime_v314 as runtime
            calls = _invocations(db, p, saved)
            need(len(calls) == 1 and calls[0].id == output.data['invocation_id'])
            actual = runtime.checked_output(db, p, task, saved, saved['job'], calls[0])
            need(_same(actual, output.data['output']))
            if receipt is not None:
                actual_ack = runtime.checked_receipt(db, p, task, saved, output, receipt.data['ack'])
                need(_same(actual_ack, receipt.data['ack']))
    for row in saved['events'].values():
        if row.action == c.ACTION_REVIEW:
            need(output is not None and receipt is not None
                and row.data['detail']['output_ref'] == _ref(output)
                and row.data['detail']['receipt_ref'] == _ref(receipt)
                and row.data['request']['output_hash'] == output.data['output_hash'])
    return output, receipt


def _manage_allowed(p, saved):
    proposal, start = saved['proposal'], saved['start']
    return (p.role == 'admin' or p.user_id in {proposal['submitter_id'], proposal['business_actor_id']}
        or (start is not None and p.user_id == start.actor_id))


def _stage_role(db, p, task, saved, *, lock):
    proposal = saved['proposal']
    _independent(p.user_id, proposal)
    need(p.user_id not in saved['approvers'])
    policy = sources.policy(db, p, task, proposal['grant']['basis']['policy']['ref'], lock=lock)
    return approval.role_allowed(db, p, policy['spec']['stages'][len(saved['approvers'])], lock_sources=lock)


def _start_role(db, p, task, saved, *, lock):
    proposal, plan = saved['proposal'], saved['plan']
    if not lock:
        # Avoid rebuilding the input records for a reader who lacks the
        # executor role. The live check still verifies the complete policy.
        executor = proposal['grant']['basis']['policy']['spec']['executor']
        approval.role_allowed(db, p, executor, lock_sources=False)
    current = sources.live(db, p, task, proposal, execution_plan_id=plan.id, lock=lock)
    spec = current['policy']['spec']
    for decision in saved['events'].values():
        if decision.action == c.ACTION_DECIDE:
            stage = decision.data['detail']['stage']
            prior_role = approval.role_allowed(db, p, spec['stages'][stage],
                actor_id=decision.actor_id, lock_sources=lock)
            need(_ref(prior_role) == decision.data['detail']['role_ref'])
    return approval.role_allowed(db, p, spec['executor'], lock_sources=lock)


def _review_role(db, p, task, saved, *, lock):
    proposal = saved['proposal']
    _independent(p.user_id, proposal, saved['approvers'], [saved['start'].actor_id], acceptance=True)
    policy = sources.policy(db, p, task, proposal['grant']['basis']['policy']['ref'], lock=lock)
    return approval.role_allowed(db, p, policy['spec']['acceptance'], lock_sources=lock)


def _available(check):
    try:
        check()
    except HTTPException as error:
        if error.status_code in {403, 409, 410, 422}:
            return False
        raise
    return True


def _recoverable(db, p, task, saved, output):
    job = saved['job']
    need(job is not None and job.status in {'failed', 'running', 'succeeded'}
        and (job.lease_until is None or job.lease_until <= _clock(db)))
    calls = _invocations(db, p, saved)
    need(len(calls) == 1 and calls[0].status == 'succeeded')
    if output is None:
        from . import same_goal_continuation_execution_runtime_v314 as runtime
        runtime.checked_output(db, p, task, saved, job, calls[0])


def _permissions(db, p, task, saved, output, receipt):
    result = dict.fromkeys(('can_decide', 'can_start', 'can_cancel', 'can_review', 'can_recover'), False)
    if p.role not in {'admin', 'operator'}:
        return result
    phase, job = saved['plan'].status, saved['job']
    manages = _manage_allowed(p, saved)
    if phase == 'in_review':
        result['can_decide'] = _available(lambda: _stage_role(db, p, task, saved, lock=False))
    elif phase == 'approved':
        result['can_start'] = _available(lambda: _start_role(db, p, task, saved, lock=False))
    if phase in {'queued', 'cancel_requested'}:
        if job is not None and job.status == 'succeeded' and output is not None and receipt is not None:
            result['can_review'] = _available(lambda: _review_role(db, p, task, saved, lock=False))
        if manages and receipt is None:
            result['can_recover'] = _available(lambda: _recoverable(db, p, task, saved, output))
    result['can_cancel'] = manages and phase in {'in_review', 'approved', 'queued', 'cancel_requested'}
    return result


@checked
def _view(db, p, task, saved):
    output, receipt = _results(db, p, task, saved)
    plan, proposal, job = saved['plan'], saved['proposal'], saved['job']
    stages = proposal['grant']['basis']['policy']['spec']['stages']
    stage = len(saved['approvers'])
    label = None
    if plan.status == 'in_review' and stage < len(stages):
        label = stages[stage].get('name') if type(stages[stage]) is dict else None
        label = label or f'第 {stage + 1} 级审批'
    return {'schema': c.VIEW, 'task_id': task.id, 'advice_plan_id': proposal['advice_plan_id'],
        'continuation_id': proposal['continuation_ref']['id'], 'plan_id': plan.id,
        'version': plan.version, 'phase': plan.status, 'submission_hash': proposal['record_hash'],
        'proposal': sources.proposal_view(proposal), 'stage': stage,
        'current_stage_label': label, 'policy_stage_count': len(stages),
        'permissions': _permissions(db, p, task, saved, output, receipt),
        'operations': [{'operation': row.data['operation'], 'actor_id': row.actor_id,
            'request_id': row.data['request_id'], 'receipt_ref': _ref(row),
            'decision': row.data['request'].get('decision'),
            'reason': row.data['request'].get('reason', row.data['request'].get('rationale')),
            'occurred_at': row.created_at.isoformat() if row.created_at else None} for row in saved['events'].values()],
        'job': None if job is None else {'id': job.id, 'status': job.status},
        'output': None if output is None else copy.deepcopy(output.data['output']),
        'output_hash': None if output is None else output.data['output_hash'],
        'data_received': receipt is not None, **c.FLAGS}


def _stop_queued(db, p, saved):
    job = saved['job']
    if job is None or job.status != 'queued':
        return False
    try:
        with db.begin_nested():
            locked = db.scalar(select(Job).where(Job.id == job.id, Job.workspace_id == p.workspace_id)
                .with_for_update(nowait=True).execution_options(populate_existing=True))
            need(locked is not None and locked.kind == c.KIND and locked.idempotency_key == _key(saved['plan'].id))
            if (locked.status != 'queued' or locked.attempts != 0 or locked.lease_token is not None
                    or locked.lease_until is not None or _invocations(db, p, saved)):
                return False
            locked.status = 'cancelled'
            db.flush()
            return True
    except DBAPIError as error:
        if getattr(error.orig, 'sqlstate', None) != '55P03' and getattr(error.orig, 'pgcode', None) != '55P03':
            raise
        return False


def _write(db, p, task_id, advice_id, continuation_id, body, operation, plan_id=None):
    body = c.dump(_OPERATIONS[operation][1], body)
    advice_id, continuation_id = _id(advice_id), _id(continuation_id)
    with db.no_autoflush:
        _reader(db, p, write=True)
        nonce.acquire(db, p, action=_OPERATIONS[operation][0], task_id=task_id, body=body,
            subject_id=plan_id, advice_id=advice_id)
        task = _task(db, p, task_id, lock=True)
        lock_current_actor(db, p)
        original = _original(db, p, body['request_id'])
        if original is not None:
            _scope(original, task, advice_id, continuation_id)
            need(original['operation'] == operation and original['actor_id'] == p.user_id
                and original['body_hash'] == digest(body) and (plan_id is None or original['plan_id'] == plan_id))
            return _view(db, p, task, _find(_inventory(db, p, task), advice_id, continuation_id, original['plan_id']))
        rows = _inventory(db, p, task)
        job = None
        detail = {}
        if operation == 'submit':
            need(task.version == body['expected_task_version'], '任务版本已变化，请重新核对提交内容')
            proposal = c.check_proposal(sources.build_proposal(db, p, task.id, advice_id, continuation_id, body, lock=True)['proposal'])
            need(proposal['record_hash'] == body['preview_hash'] and proposal['submitter_id'] == p.user_id)
            _scope({**proposal, 'continuation_id': proposal['continuation_ref']['id']}, task, advice_id, continuation_id)
            need(not any(row['proposal']['grant_ref']['id'] == proposal['grant_ref']['id']
                and (row['start'] is not None or row['plan'].status not in {'returned', 'cancelled'}) for row in rows))
            plan = Plan(id=uid(), workspace_id=p.workspace_id, task_id=task.id,
                approved_by=None, approved_hash=None, version=1, status='in_review')
            saved = {'plan': plan, 'proposal': proposal, 'start': None, 'approvers': [], 'job': None}
            before, phase = 0, None
        else:
            saved = _find(rows, advice_id, continuation_id, plan_id)
            plan, proposal = saved['plan'], saved['proposal']
            before, phase = plan.version, plan.status
            need(body['expected_execution_version'] == before)
            if operation == 'start':
                need(phase == 'approved')
                need(not any(row['start'] and row['proposal']['grant_ref']['id'] == proposal['grant_ref']['id'] for row in rows),
                    '原授权的一次执行额度已分配')
                role = _start_role(db, p, task, saved, lock=True)
                job = Job(id=uid(), workspace_id=p.workspace_id, kind=c.KIND,
                    idempotency_key=_key(plan.id), status='queued', attempts=0,
                    data=_job_data(proposal, plan.id, p.user_id))
                detail = {'job_id': job.id, 'job_data': copy.deepcopy(job.data), 'role_ref': _ref(role)}
            elif operation == 'decide':
                need(phase == 'in_review')
                stage = len(saved['approvers'])
                role = _stage_role(db, p, task, saved, lock=True)
                detail = {'stage': stage, 'role_ref': _ref(role)}
            elif operation == 'review':
                output, receipt = _results(db, p, task, saved)
                need(output is not None and receipt is not None and saved['job'].status == 'succeeded'
                    and output.data['output_hash'] == body['output_hash'])
                role = _review_role(db, p, task, saved, lock=True)
                detail = {'output_ref': _ref(output), 'receipt_ref': _ref(receipt), 'role_ref': _ref(role)}
            else:
                need(_manage_allowed(p, saved))
                if operation == 'cancel':
                    need(phase in {'in_review', 'approved', 'queued', 'cancel_requested'})
                else:
                    _results(db, p, task, saved)
            # The Task lock serializes all writers for this Plan. Re-read the
            # execution row after any live grant/draft/policy locks.
            locked = _rows(db, p, Plan, Plan.id == plan.id, lock=True)
            need(len(locked) == 1 and locked[0].version == before and locked[0].status == phase)
        with db.begin_nested():
            if operation == 'cancel':
                detail = {'queued_job_stopped': _stop_queued(db, p, saved)}
            if operation == 'recover':
                need(phase in {'queued', 'cancel_requested'} and saved['start'] is not None)
                from . import same_goal_continuation_execution_runtime_v314 as runtime
                runtime.recover_saved(db, p, task, saved, body)
            event = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
                action=_OPERATIONS[operation][0], object_id=plan.id)
            value = {'schema': c.COMMAND, 'operation': operation, 'workspace_id': p.workspace_id,
                'task_id': task.id, 'advice_plan_id': advice_id, 'continuation_id': continuation_id,
                'plan_id': plan.id, 'actor_id': p.user_id, 'request_id': body['request_id'],
                'request': copy.deepcopy(body), 'body_hash': digest(body),
                'submission_hash': proposal['record_hash'], 'before': before, 'after': before + 1,
                'detail': detail}
            value['phase'] = _transition(value, proposal, phase, list(saved['approvers']), saved['start'])
            value['record_hash'] = c.record_hash(value)
            event.data = value
            prior = plan.data if before else {'schema': c.PLAN, 'proposal': proposal, 'operations': [],
                'output_ref': None, 'receipt_ref': None}
            plan.data = {**prior, 'operations': [*prior['operations'], {**_ref(event), 'request_id': body['request_id']}]}
            plan.status, plan.version = value['phase'], value['after']
            if before == 0:
                task.data = {**task.data, c.INDEX: [*task.data.get(c.INDEX, []), _binding(event)]}
                task.version += 1
                db.add(plan)
            db.add(event)
            if job is not None:
                db.add(job)
            db.flush()
            return _view(db, p, task, _find(_inventory(db, p, task), advice_id, continuation_id, plan.id))


@checked
def submit(db,p,task_id,advice_id,continuation_id,body):
    return _write(db,p,task_id,advice_id,continuation_id,body,'submit')


@checked
def decide(db,p,task_id,advice_id,continuation_id,plan_id,body):
    return _write(db,p,task_id,advice_id,continuation_id,body,'decide',_id(plan_id))


@checked
def start(db,p,task_id,advice_id,continuation_id,plan_id,body):
    return _write(db,p,task_id,advice_id,continuation_id,body,'start',_id(plan_id))


@checked
def cancel(db,p,task_id,advice_id,continuation_id,plan_id,body):
    return _write(db,p,task_id,advice_id,continuation_id,body,'cancel',_id(plan_id))


@checked
def review(db,p,task_id,advice_id,continuation_id,plan_id,body):
    return _write(db,p,task_id,advice_id,continuation_id,body,'review',_id(plan_id))


@checked
def recover(db,p,task_id,advice_id,continuation_id,plan_id,body):
    return _write(db,p,task_id,advice_id,continuation_id,body,'recover',_id(plan_id))


@checked
def get(db,p,task_id,advice_id,continuation_id,plan_id):
    with db.no_autoflush:
        _reader(db,p)
        task = _task(db,p,task_id)
        return _view(db,p,task,_find(_inventory(db,p,task),_id(advice_id),_id(continuation_id),plan_id))


@checked
def listing(db,p,task_id,advice_id,continuation_id):
    with db.no_autoflush:
        _reader(db,p)
        task = _task(db,p,task_id)
        advice_id,continuation_id = _id(advice_id),_id(continuation_id)
        return {'schema': c.VIEW+'/list', 'task_id':task.id, 'advice_plan_id':advice_id,
            'continuation_id':continuation_id,'records':[_view(db,p,task,row) for row in _inventory(db,p,task)
                if row['proposal']['advice_plan_id']==advice_id and row['proposal']['continuation_ref']['id']==continuation_id]}


@checked
def get_operation(db,p,task_id,advice_id,continuation_id,request_id):
    with db.no_autoflush:
        _reader(db,p)
        task = _task(db,p,task_id)
        original = _original(db,p,request_id)
        if original is None:
            raise HTTPException(404,'尚未查到原申请，请保留原编号核对')
        _scope(original,task,_id(advice_id),_id(continuation_id))
        status = 'available'
        try:
            result = _view(db,p,task,_find(_inventory(db,p,task),advice_id,continuation_id,original['plan_id']))
        except HTTPException as error:
            if error.status_code not in {404,409,410}:
                raise
            result,status = None,'needs_check'
        return {'schema':c.RECEIPT,'found':True,'task_id':task.id,'advice_plan_id':advice_id,
            'continuation_id':continuation_id,'plan_id':original['plan_id'],'operation':original['operation'],
            'request_id':original['request_id'],'actor_id':original['actor_id'],'body_hash':original['body_hash'],
            'result':result,'result_status':status}


def _output_target(db, p, task, plan_id, job, call):
    rows = [row for row in _inventory(db, p, task) if row['plan'].id == _id(plan_id)]
    need(len(rows) == 1)
    saved = rows[0]
    need(saved['job'] is not None and saved['job'].id == job.id
        and job.workspace_id == p.workspace_id and call.workspace_id == p.workspace_id
        and saved['plan'].status in {'queued', 'cancel_requested', 'adopted', 'result_returned'})
    calls = _invocations(db, p, saved)
    need(len(calls) == 1 and calls[0].id == call.id)
    return saved, calls[0]


@checked
def save_output(db, p, task_id, plan_id, job, call):
    """Seal only a verified saved invocation under the original worker fence."""
    from . import leases
    from . import same_goal_continuation_execution_runtime_v314 as runtime
    with db.no_autoflush:
        task = _task(db, p, task_id, lock=True)
        saved, actual = _output_target(db, p, task, plan_id, job, call)
        original, _receipt = _results(db, p, task, saved)
        if original is not None:
            return {'output_ref': _ref(original), 'output': copy.deepcopy(original.data['output']),
                'output_hash': original.data['output_hash']}
        worker = saved['job']
        need(leases.fenced(db) and worker.status == 'running' and worker.lease_token is not None
            and worker.lease_until is not None and worker.lease_until > _clock(db)
            and leases.ownership(db) == {'job_id': worker.id, 'lease_token': worker.lease_token}
            and worker.data['actor_id'] == p.user_id)
        output = runtime.checked_output(db, p, task, saved, worker, actual)
        plan = saved['plan']
        event = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
            action=OUTPUT, object_id=plan.id, data={'task_id': task.id, 'plan_id': plan.id,
                'job_id': worker.id, 'invocation_id': actual.id,
                'submission_hash': saved['proposal']['record_hash'], 'output': copy.deepcopy(output),
                'output_hash': digest(output)})
        db.add(event)
        plan.data = {**plan.data, 'output_ref': _ref(event)}
        db.flush()
        return {'output_ref': _ref(event), 'output': copy.deepcopy(output), 'output_hash': digest(output)}


@checked
def attach_receipt(db, p, task_id, plan_id, job, call, ack):
    """Attach a durable Data receiver proof after re-reading the original output."""
    from . import same_goal_continuation_execution_runtime_v314 as runtime
    with db.no_autoflush:
        task = _task(db, p, task_id, lock=True)
        saved, actual = _output_target(db, p, task, plan_id, job, call)
        output, previous = _results(db, p, task, saved)
        need(output is not None and output.data['invocation_id'] == actual.id)
        canonical = runtime.checked_receipt(db, p, task, saved, output, ack)
        need(_same(canonical, ack))
        if previous is not None:
            need(_same(previous.data['ack'], ack))
            return _view(db, p, task, saved)
        plan = saved['plan']
        event = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
            action=RECEIVED, object_id=plan.id, data={'task_id': task.id, 'plan_id': plan.id,
                'job_id': saved['job'].id, 'submission_hash': saved['proposal']['record_hash'],
                'output_ref': _ref(output), 'ack': copy.deepcopy(ack)})
        db.add(event)
        plan.data = {**plan.data, 'receipt_ref': _ref(event)}
        db.flush()
        return _view(db, p, task, saved)
