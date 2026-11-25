"""Isolated-only cancellation of a never-started, expired v2 preparation.

No API or worker calls this candidate. It preserves the original Plan and Job,
adds one immutable audit receipt, never extends a policy or sends a request.
Legacy writers and full lifecycle recovery remain incompatible; do not deploy.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from . import same_goal_advice_common_inventory_v2 as inventory
from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_policy_v2 as policy
from . import same_goal_advice_unlimited_store_v2 as v2
from . import workspace_advice_nonce_candidate_v2 as nonces
from .contracts import digest
from .models import Audit, Plan
from .relations import lock_current_actor


@old.checked
def cancel(db, p, task_id, plan_id, body):
    guard.require_disposable_database(db)
    try: UUID(p.session_id)
    except (ValueError, TypeError, AttributeError): old.need(False, '需要有效登录会话，未取消')
    request = c.Cancel.model_validate(body).model_dump(mode='json')
    old._reader(db, p, write=True)
    nonces.acquire(db, p, action=v2.CANCELLED_EXPIRED, task_id=task_id, body=request, subject_id=plan_id)
    task = old.existing._task(db, p, task_id, lock=True)
    lock_current_actor(db, p); v2._lock_current_reader(db, p)
    inventory.read(db, p, task)
    plan = db.scalar(select(Plan).where(Plan.id == old.existing._id(plan_id),
        Plan.workspace_id == p.workspace_id, Plan.task_id == task.id).with_for_update())
    old.need(plan is not None)
    events = old._rows(db, p, Audit, Audit.action == v2.CANCELLED_EXPIRED, Audit.object_id == plan.id)
    old.need(len(events) <= 1)
    if events:
        prep, job = v2.read_expired_cancelled(db, p, task, plan)
        event = events[0]
        old.need(event.actor_id == p.user_id and digest(event.data['body']) == digest(request),
            '原取消请求与当前内容不一致，保留原记录')
        return {'plan_id': plan.id, 'job_id': job.id, 'request_id': request['request_id'],
            'state': 'cancelled_expired', 'replayed': True}
    prep, job = v2.read_queued(db, p, task, plan)
    old.need(p.role == 'admin' or p.user_id in {prep['requested_by'], prep['data_actor_id'], prep['business_actor_id']})
    old.need(request['expected_plan_version'] == plan.version and request['request_id'] != prep['request']['request_id'])
    ref = prep['budget']['policy_ref']
    issued = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.object_id == task.id,
        Audit.id == ref['id'], Audit.action == policy.POLICY).with_for_update(read=True))
    clock = db.scalar(select(func.clock_timestamp()))
    old.need(issued is not None and type(clock) is datetime and clock.tzinfo is not None)
    saved, _ = policy.policy_shape(issued, clock)
    old.need(policy.instant(saved['expires_at']) <= clock, '政策尚未到期；此到期恢复入口不会取消原申请')
    payload = {'schema': v2.CANCELLED_EXPIRED, 'task_id': task.id, 'plan_id': plan.id,
        'job_id': job.id, 'job_key': job.idempotency_key, 'actor_id': p.user_id,
        'request_id': request['request_id'], 'body': request, 'body_hash': digest(request),
        'preparation_snapshot_hash': prep['record_hash'], 'policy_ref': ref}
    db.add(Audit(workspace_id=p.workspace_id, object_id=plan.id, actor_id=p.user_id,
        action=v2.CANCELLED_EXPIRED, created_at=clock, data=payload))
    db.flush()
    v2.read_expired_cancelled(db, p, task, plan)
    return {'plan_id': plan.id, 'job_id': job.id, 'request_id': request['request_id'],
        'state': 'cancelled_expired', 'replayed': False}
