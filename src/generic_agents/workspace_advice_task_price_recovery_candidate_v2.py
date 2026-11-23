"""Read an existing v310 task-price receipt; no route, write or send grant.

All workspace nonce writers and older-process isolation remain release gates.
This helper never constructs a task wrapper from a standalone price record.
"""
from sqlalchemy import select, text

from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_options_v310 as options
from . import workspace_advice_nonce_candidate_v2 as candidate
from . import workspace_advice_price_recovery_candidate_v2 as price_recovery
from . import workspace_command_nonce_lock_v312 as legacy
from .contracts import digest
from .models import Audit
from .relations import lock_current_actor
from .security import Principal, require


@candidate.checked
def recover_only(db, p, task_id, body):
    """Return one authentic existing wrapper's original price receipt."""
    require(p, 'admin'); legacy.need(type(p) is Principal)
    task_id = legacy._id(task_id)
    body = contracts._parse(options.PriceRequest, body)
    with db.no_autoflush:
        options.store._reader(db, p, write=True)
        legacy.need(db.scalar(text("SELECT current_setting('transaction_isolation')")) == 'read committed',
            '当前数据库事务隔离级别不能安全恢复原回执')
        db.execute(text(legacy.LOCK_SQL), {'workspace_command_nonce_key':
            legacy.key(p.workspace_id, body['request_id'])})
        task = options._admin_task(db, p, task_id)
        rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == options.PRICE_ACTION, Audit.object_id == task.id,
            Audit.data['request_id'].as_string() == body['request_id'])
            .with_for_update(read=True).limit(2).execution_options(populate_existing=True)))
        legacy.need(len(rows) == 1, '原任务价格回执不唯一或不存在，请保留原记录核对')
        wrapper = rows[0]; value = options._shape(p, task, wrapper)
        legacy.need(wrapper.actor_id == p.user_id and digest(value['body']) == digest(body),
            '原任务价格申请正文或账号无法核对；未创建替代申请')
        # Locks and authenticates the original model, price Audit, Evidence and
        # upload Audit; this cannot register a price or call external HTTP.
        base = price_recovery.recover_only(db, p, body)
        legacy.need(base['basis_ref'] == value['price_basis_ref'])
        original = options._price_anchor(db, p, wrapper, value)
        # Preserve the existing v310 reader's original pricing time and
        # output semantics rather than treating this as a current quote.
        result = options._price_result(db, p, original, value)
        legacy.need(result['basis_ref'] == value['price_basis_ref'])
        lock_current_actor(db, p)
        return result
