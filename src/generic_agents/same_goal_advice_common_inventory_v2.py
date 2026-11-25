"""Closed cross-version inventory; not an execution or creation authority.

Only the original v1 lifecycle and queued or locally prepared v2 records are readable.
This independent reader has no route/worker and does not replace the v1 API.
Unknown versions, incomplete originals, and unexplained records fail closed.
"""
from uuid import UUID

from sqlalchemy import func, or_, select

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_policy_v2 as policy
from . import same_goal_advice_unlimited_store_v2 as v2
from . import same_goal_advice_read_session_v2 as read_session
from .models import Audit, AuthSession, CloudCall, Evidence, Invocation, Job, Plan, Task


@old.checked
def read(db, p, task):
    """Return validated v1 reservations and the number of open v2 plans.

    The Task lock precedes all custody reads. Callers that mutate commands
    must acquire the workspace nonce lock before calling this function. This
    reader itself does not take that lock or authorize a future mutation.
    """
    with read_session.isolated(db, task.id) as reader:
        return _read(reader, p, task.id)


@old.checked
def read_snapshot(db, p, task):
    """Display only, from this actual PG repeatable-read read-only snapshot.

    This does not authorize a write or sender. Revocations committed after the
    snapshot starts are visible only to a later request/snapshot.
    """
    try:
        UUID(p.session_id)
    except (ValueError, TypeError, AttributeError):
        old.need(False, '只读展示必须使用真实用户会话')
    with read_session.isolated(db, task.id) as reader:
        old.need(reader.get_bind().dialect.name == 'postgresql')
        isolation, read_only = reader.execute(select(func.current_setting('transaction_isolation'),
            func.current_setting('transaction_read_only'))).one()
        old.need(read_only == 'on' and isolation in {'repeatable read', 'serializable'},
            '只读展示需要一致的数据库快照；未用于授权或执行')
        return _read(reader, p, task.id, lock=False)


def _read(db, p, task_id, *, lock=True):
    from . import same_goal_advice_execution_v308 as execution

    with db.no_autoflush:
        old._reader(db, p)
        query = select(Task).where(Task.workspace_id == p.workspace_id, Task.id == task_id)
        if lock: query = query.with_for_update(read=True)
        task = db.scalar(query.execution_options(populate_existing=True))
        old.need(task is not None)
        http_session = v2._lock_current_reader(db, p) if lock else v2._lock_current_reader(db, p, lock=False)
        plans = old._rows(db, p, Plan, or_(
            (Plan.task_id == task.id) & Plan.data['schema'].as_string().startswith('same-goal-advice-', autoescape=True),
            Plan.data['preparation_snapshot']['scope']['task_id'].as_string() == task.id))
        legacy, queued, closed = [], [], []
        for plan in plans:
            schema = plan.data.get('schema') if type(plan.data) is dict else None
            if schema == c.PLAN:
                prep, job = old._saved(db, p, task, plan)
                legacy.append((plan, prep, job, execution.checked(db, p, task, plan, prep, job)))
            elif schema == v2.PLAN:
                started = plan.status == 'awaiting_dispatch'
                prepared = plan.status in {'awaiting_consent','awaiting_dispatch'}
                cancellations = old._rows(db, p, Audit, Audit.action == v2.CANCELLED_EXPIRED, Audit.object_id == plan.id)
                cancelled = bool(cancellations)
                prep, job = ((v2.read_expired_cancelled(db, p, task, plan) if cancelled else
                    v2.read_started(db, p, task, plan) if started else
                    v2.read_prepared(db, p, task, plan) if prepared else v2.read_queued(db, p, task, plan))
                    if lock else v2._read_queued(db, p, task.id, plan.id, lock=False, prepared=prepared, cancelled=cancelled, started=started))
                (closed if cancelled else queued).append((plan, prep, job))
            else:
                old.need(False, '业务建议记录版本未知，保留原记录等待核对')
        old.need(len(queued) <= 1 and not (queued and any(old._preparation_open(row[0]) for row in legacy)),
            '存在多个未完成的业务建议，保留原记录等待核对')

        ids = [row[0].id for row in legacy]
        prefix = 'same-goal-advice-prepare:' + task.id + ':'
        jobs = old._rows(db, p, Job, or_(
            Job.kind.in_([c.PREPARE_KIND, c.SEND_KIND]) & (Job.data['task_id'].as_string() == task.id),
            Job.idempotency_key.startswith(prefix, autoescape=True), Job.data['plan_id'].as_string().in_(ids)))
        audits = old._rows(db, p, Audit, Audit.action.in_([old.ACCEPTED, old.CANCELLED]), or_(
            Audit.data['task_id'].as_string() == task.id, Audit.object_id.in_(ids),
            Audit.data['job_key'].as_string().startswith(prefix, autoescape=True)))
        states = [row[3] for row in legacy if row[3] is not None]
        old.need({job.id for job in jobs} == {row[2].id for row in legacy} | {s['job'].id for s in states}
            and {event.id for event in audits} == {op['audit_id'] for row in legacy for op in row[0].data['operations']})
        calls = old._rows(db, p, Invocation, or_(Invocation.data['job_id'].as_string().in_([job.id for job in jobs]),
            Invocation.data['approval']['plan_ref']['id'].as_string().in_(ids),
            Invocation.idempotency_key.startswith(prefix, autoescape=True)))
        old.need({call.id for call in calls} == {call.id for state in states for call in state['calls']})

        send_prefix = 'same-goal-advice-send:' + task.id + ':'
        starts = old._rows(db, p, Audit, Audit.action == execution.STARTED, or_(
            Audit.object_id.in_(ids), Audit.data['task_id'].as_string() == task.id,
            Audit.data['job_ref']['key'].as_string().startswith(send_prefix, autoescape=True)))
        send_jobs = old._rows(db, p, Job, or_(Job.idempotency_key.startswith(send_prefix, autoescape=True),
            (Job.kind == c.SEND_KIND) & (Job.data['task_id'].as_string() == task.id),
            (Job.kind == c.SEND_KIND) & Job.data['plan_id'].as_string().in_(ids)))
        send_calls = old._rows(db, p, Invocation, or_(Invocation.idempotency_key.startswith(send_prefix, autoescape=True),
            Invocation.data['job_id'].as_string().in_([job.id for job in send_jobs]),
            Invocation.data['approval']['plan_ref']['id'].as_string().in_(ids),
            Invocation.data['inputs']['plan_ref']['id'].as_string().in_(ids)))
        old.need({event.id for event in starts} == {state['start'].id for state in states}
            and {job.id for job in send_jobs} == {state['job'].id for state in states}
            and {call.id for call in send_calls} == {call.id for state in states for call in state['calls']})
        # These paid-family events can remain even after all Plan/Start/Job
        # rows disappear. Require every task-bound original to be owned by a
        # validated legacy Plan, whose paid inventory has already checked it.
        paid = old._rows(db, p, Audit, Audit.action.in_(execution._SDK_ACTIONS | execution._PLAN_ACTIONS),
            Audit.data['task_id'].as_string() == task.id)
        old.need(all(type(event.data) is dict and event.data.get('plan_id') in ids for event in paid),
            '原业务建议存在未归属的付费或调用凭据，保留记录等待核对')
        paid_cloud = old._rows(db, p, CloudCall,
            CloudCall.data['gateway_dispatch']['identity']['task_id'].as_string() == task.id,
            CloudCall.data.has_key(execution._SPENDING))
        old.need(all(type(row.data) is dict and type(row.data.get(execution._SPENDING)) is dict
            and row.data[execution._SPENDING].get('plan_id') in ids for row in paid_cloud),
            '原业务建议存在未归属的云调用记录，保留记录等待核对')

        # Reverse inventory: a missing Plan must not make a visible v2 job or
        # acceptance disappear. Future v2 variants are intentionally blocked.
        v2records = queued + closed
        v2ids = [row[0].id for row in v2records]
        v2prefix = 'same-goal-advice-prepare-v2:' + task.id + ':'
        v2send = 'same-goal-advice-send-v2:' + task.id + ':'
        v2jobs = old._rows(db, p, Job, or_(
            Job.idempotency_key.startswith(v2prefix, autoescape=True), Job.idempotency_key.startswith(v2send, autoescape=True),
            Job.kind.startswith('prepare_same_goal_advice_unlimited', autoescape=True) & (Job.data['task_id'].as_string() == task.id),
            Job.kind.startswith('generate_same_goal_advice_unlimited', autoescape=True) & (Job.data['task_id'].as_string() == task.id),
            Job.data['plan_id'].as_string().in_(v2ids)))
        v2audits = old._rows(db, p, Audit,
            Audit.action.startswith('same_goal_advice.unlimited_', autoescape=True),
            Audit.action.not_in([policy.POLICY, policy.AUTHORIZATION]), or_(
                Audit.object_id == task.id, Audit.object_id.in_(v2ids),
                Audit.data['task_id'].as_string() == task.id,
                Audit.data['plan_id'].as_string().in_(v2ids),
                Audit.data['job_key'].as_string().startswith(v2prefix, autoescape=True)))
        expected_audits = {row[0].data['operations'][0]['audit_id'] for row in v2records}
        expected_audits |= {row[0].data['document_preparation']['receipt_ref']['id']
            for row in v2records if row[0].status in {'awaiting_consent','awaiting_dispatch'}}
        expected_audits |= {event.id for plan, _, _ in closed for event in old._rows(db, p, Audit,
            Audit.action == v2.CANCELLED_EXPIRED, Audit.object_id == plan.id)}
        from . import same_goal_advice_unlimited_preview_candidate_v2 as previews
        expected_audits |= {event.id for plan, _, _ in queued if plan.status in {'awaiting_consent','awaiting_dispatch'}
            for event in old._rows(db, p, Audit, Audit.action == previews.ACTION, Audit.object_id == plan.id)}
        from . import same_goal_advice_unlimited_start_candidate_v2 as starts
        expected_audits |= {plan.data[starts.POINTER]['audit_id'] for plan, _, _ in queued if plan.status=='awaiting_dispatch'}
        expected_jobs = {row[2].id for row in v2records} | {
            plan.data[starts.POINTER]['job_id'] for plan, _, _ in queued if plan.status=='awaiting_dispatch'}
        old.need({job.id for job in v2jobs} == expected_jobs
            and {event.id for event in v2audits} == expected_audits,
            '业务建议存在未识别的任务或凭据，保留原记录等待核对')
        # Reverse-search task-bound v2 transport and document markers even if
        # Plan, Job, and accepted Audit are all missing. An unrelated or wholly
        # unidentifiable historical record cannot be proven absent here.
        v2calls = old._rows(db, p, Invocation, or_(
            Invocation.idempotency_key.startswith(v2prefix, autoescape=True),
            Invocation.idempotency_key.startswith(v2send, autoescape=True),
            Invocation.data['same_goal_advice_unlimited_v2']['task_id'].as_string() == task.id,
            (Invocation.data['task_id'].as_string() == task.id) & Invocation.data.has_key('same_goal_advice_unlimited_v2'),
            Invocation.data['approval']['plan_ref']['id'].as_string().in_(v2ids),
            Invocation.data['inputs']['plan_ref']['id'].as_string().in_(v2ids)))
        cloud = old._rows(db, p, CloudCall, or_(
            CloudCall.data['same_goal_advice_unlimited_v2']['task_id'].as_string() == task.id,
            CloudCall.data['same_goal_advice_unlimited_v2']['plan_id'].as_string().in_(v2ids),
            (CloudCall.data['gateway_dispatch']['identity']['task_id'].as_string() == task.id)
            & CloudCall.data.has_key('same_goal_advice_unlimited_v2'),
            (CloudCall.data['gateway_dispatch']['identity']['task_id'].as_string() == task.id)
            & (CloudCall.data['gateway_dispatch']['identity']['family'].as_string() == 'same_goal_advice_unlimited_v2')))
        evidence = old._rows(db, p, Evidence, or_(
            Evidence.data[v2.DOCUMENT_KEY]['task_id'].as_string() == task.id,
            Evidence.data[v2.DOCUMENT_KEY]['plan_id'].as_string().in_(v2ids)))
        expected_files = {row[0].data['document_preparation']['evidence_ref']['evidence_id']
            for row in v2records if row[0].status in {'awaiting_consent','awaiting_dispatch'}
            and row[1]['request']['document_source']['kind'] == 'approved_source_set'}
        old.need(not v2calls and not cloud and {item.id for item in evidence} == expected_files,
            '业务建议存在未识别的调用或资料记录，保留原记录等待核对')
        if http_session:
            valid = db.scalar(select(AuthSession.id).where(AuthSession.id == p.session_id,
                AuthSession.user_id == p.user_id, AuthSession.revoked.is_(False),
                AuthSession.expires_at > func.clock_timestamp()))
            old.need(valid == p.session_id, '当前会话已过期；未读取资料')
        return {'v1_calls': len(states), 'queued_v2': len(queued)}
