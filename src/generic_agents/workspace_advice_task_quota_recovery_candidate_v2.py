"""Read one existing v310 quota wrapper; no routing, issuance or send grant.

The helper retains the original v1 authorization and price provenance. It
does not eliminate the need for workspace nonce migration or process fencing.
"""
from sqlalchemy import select, text

from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_options_v310 as options
from . import same_goal_advice_price_v307 as prices
from . import workspace_advice_nonce_candidate_v2 as candidate
from . import workspace_advice_policy_recovery_candidate_v2 as policy_recovery
from . import workspace_command_nonce_lock_v312 as legacy
from .contracts import digest
from .models import Audit, Evidence
from .relations import lock_current_actor
from .security import Principal, require


@candidate.checked
def recover_only(db, p, task_id, body):
    """Read the original wrapper and its policy/price evidence, never renew."""
    require(p, 'admin'); legacy.need(type(p) is Principal)
    task_id = legacy._id(task_id)
    body = contracts._parse(options.ConfigureQuota, body)
    with db.no_autoflush:
        options.store._reader(db, p, write=True)
        legacy.need(db.scalar(text("SELECT current_setting('transaction_isolation')")) == 'read committed',
            '当前数据库事务隔离级别不能安全恢复原回执')
        db.execute(text(legacy.LOCK_SQL), {'workspace_command_nonce_key':
            legacy.key(p.workspace_id, body['request_id'])})
        task = options._admin_task(db, p, task_id)
        rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == options.QUOTA_ACTION, Audit.object_id == task.id,
            Audit.data['request_id'].as_string() == body['request_id'])
            .with_for_update(read=True).limit(2).execution_options(populate_existing=True)))
        legacy.need(len(rows) == 1, '原任务额度配置回执不唯一或不存在，请保留原记录核对')
        wrapper = rows[0]; value = options._shape(p, task, wrapper)
        legacy.need(wrapper.actor_id == p.user_id and digest(value['body']) == digest(body),
            '原任务额度申请正文或账号无法核对；未创建替代申请')
        authorization = policy_recovery.recover_only(db, p, task.id, value['authorization_body'])
        legacy.need(authorization['quota_policy_ref'] == value['policy_ref'])
        original, _, _ = options._quota_anchors(db, p, task, wrapper, value)
        basis = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.id == value['price_basis_ref']['id'], Audit.action == prices.PRICE_BASIS)
            .with_for_update(read=True).execution_options(populate_existing=True))
        legacy.need(basis is not None and digest(basis.data) == value['price_basis_ref']['record_hash'])
        price = prices._parse(prices.Registration, basis.data)
        evidence = db.scalar(select(Evidence).where(Evidence.workspace_id == p.workspace_id,
            Evidence.id == price['official_evidence_ref']['evidence_id']).with_for_update(read=True)
            .execution_options(populate_existing=True))
        upload = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.id == price['upload_audit_ref']['id']).with_for_update(read=True)
            .execution_options(populate_existing=True))
        legacy.need(evidence is not None and upload is not None and upload.object_id == evidence.id
            and upload.action == 'evidence.upload' and upload.actor_id == basis.actor_id,
            '原价格证据或上传记录无法核对；请保留原记录')
        # Authenticate the preserved evidence at the real authorization time,
        # never convert it into a current quote or call the price fetcher.
        prices._read(db, p, value['model_pin'], basis, now=prices._clock(db),
            priced_at=original.created_at, current=False)
        result = options._quota_result(db, p, task, wrapper, value)
        legacy.need(result['quota_policy_ref'] == value['policy_ref']
            and result['cloud_send_authorized'] is False)
        lock_current_actor(db, p)
        return result
