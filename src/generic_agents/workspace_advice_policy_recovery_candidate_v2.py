"""Read an existing v1 policy receipt; no route, new quota or send authority.

Call before taking any Task/actor lock. All common nonce writers and
old-process isolation still require integration before this can be deployed.
"""
from sqlalchemy import select, text

from . import same_goal_advice_quota_v304 as quota
from . import workspace_advice_nonce_candidate_v2 as candidate
from . import workspace_command_nonce_lock_v312 as legacy
from .contracts import digest
from .models import Audit
from .relations import lock_current_actor
from .security import Principal, require


@candidate.checked
def recover_only(db, p, task_id, body):
    """Return the original immutable v1 receipt; never create or renew one."""
    require(p, 'admin'); legacy.need(type(p) is Principal)
    body = quota.Authorize.model_validate(body).model_dump(mode='json')
    task_id = legacy._id(task_id)
    with db.no_autoflush:
        quota.store._reader(db, p, write=True)
        legacy.need(db.scalar(text("SELECT current_setting('transaction_isolation')")) == 'read committed',
            '当前数据库事务隔离级别不能安全恢复原回执')
        db.execute(text(legacy.LOCK_SQL), {'workspace_command_nonce_key':
            legacy.key(p.workspace_id, body['request_id'])})
        task = quota.store.existing._task(db, p, task_id, lock=True)
        lock_current_actor(db, p); quota.store._member(db, p, p.user_id, admin=True)
        originals = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == quota.AUTHORIZATION, Audit.object_id == task.id,
            Audit.data['request_id'].as_string() == body['request_id'])
            .with_for_update(read=True).limit(2).execution_options(populate_existing=True)))
        legacy.need(len(originals) == 1, '原任务政策回执不唯一或不存在，请保留原记录核对')
        event = originals[0]
        legacy.need(event.actor_id == p.user_id and event.data.get('body_hash') == digest(body)
            and digest(event.data.get('body')) == digest(body), '原政策正文或账号无法核对；未创建替代申请')
        reference = event.data['policy_ref']
        policy = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == quota.store.QUOTA_POLICY, Audit.object_id == task.id,
            Audit.id == reference['id']).with_for_update(read=True).execution_options(populate_existing=True))
        legacy.need(policy is not None and digest(policy.data) == reference['record_hash'])
        result = quota._receipt(db, p, task, body, event, quota._clock(db))
        legacy.need(result['quota_policy_ref'] == reference and result['cloud_send_authorized'] is False)
        lock_current_actor(db, p)
        return result
