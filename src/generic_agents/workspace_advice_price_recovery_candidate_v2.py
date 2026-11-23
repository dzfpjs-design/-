"""Recover a single existing price original; not wired to any route/worker.

The caller owns the transaction. This helper cannot register a new price or
grant a send; it does not implement policy, v310 wrapper or deployment fencing.
All participating writers and old-process isolation remain release blockers.
"""
from sqlalchemy import select, text

from . import same_goal_advice_price_v307 as prices
from . import workspace_advice_nonce_candidate_v2 as candidate
from . import workspace_command_nonce_lock_v312 as legacy
from .contracts import digest
from .models import Audit
from .relations import lock_current_actor
from .security import Principal, require


@candidate.checked
def recover_only(db, p, body):
    """Read exactly one existing original using the old full receipt reader.

    The model lock prevents another original for the same model from being
    minted concurrently. The matching Audit share lock prevents deletion or
    replacement between the presence check and the original receipt reader.
    The shared workspace nonce lock must precede both.
    """
    require(p, 'admin'); legacy.need(type(p) is Principal)
    body = prices._parse(prices.Authorize, body)
    with db.no_autoflush:
        prices.store._reader(db, p, write=True)
        legacy.need(db.scalar(text("SELECT current_setting('transaction_isolation')")) == 'read committed',
            '当前数据库事务隔离级别不能安全恢复原回执')
        db.execute(text(legacy.LOCK_SQL), {'workspace_command_nonce_key':
            legacy.key(p.workspace_id, body['request_id'])})
        model = prices._model(db, p, body['model_pin'], write=True, current=False)
        lock_current_actor(db, p); prices.store._member(db, p, p.user_id, admin=True)
        events = prices._events(db, p, model)  # Preserve the original bounded model inventory.
        matches = [event for event in events if event.data.get('request_id') == body['request_id']]
        legacy.need(len(matches) == 1, '原模型价格回执不唯一或不存在，请保留原记录核对')
        originals = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == prices.PRICE_BASIS, Audit.object_id == model.id,
            Audit.data['request_id'].as_string() == body['request_id'])
            .with_for_update(read=True).limit(2).execution_options(populate_existing=True)))
        legacy.need(len(originals) == 1 and originals[0].id == matches[0].id,
            '原模型价格回执不唯一或不存在，请保留原记录核对')
        original = originals[0]
        saved = prices._parse(prices.Registration, original.data)
        legacy.need(original.actor_id == p.user_id and saved['actor_id'] == p.user_id
            and saved['workspace_id'] == p.workspace_id and saved['model_pin'] == body['model_pin']
            and saved['request_id'] == body['request_id'] and saved['body_hash'] == digest(body)
            and digest(saved['body']) == digest(body), '原价格申请正文或身份无法核对；未创建替代申请')
        expected = {'id': original.id, 'record_hash': digest(original.data)}
        # Use the same fixed receipt reader as the existing replay branch;
        # never call a possibly wrapped authorize or enter its new-price path.
        result = prices._read(db, p, body['model_pin'], original, now=prices._clock(db),
            priced_at=original.created_at, current=False)
        legacy.need(result['basis_ref'] == expected)
        # A session may expire while the original evidence is being read.
        lock_current_actor(db, p)
        return {**result, 'purpose': 'original-registration-receipt-not-send-permission'}
