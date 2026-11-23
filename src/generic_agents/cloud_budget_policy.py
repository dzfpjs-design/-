"""Server-owned cloud admission policy over the existing durable daily ledger.

The ledger remains the total for every provider. Only newly recorded DeepSeek
reservations carrying our server-generated marker can be excluded when checking
other providers' limits; older usage cannot be safely attributed retroactively.
This policy does not authorize dispatch, retries, report jobs or scope changes.
"""
from datetime import date
from sqlalchemy import and_, func, select
from .models import Capability, CloudCall, Job, now
from .config import settings
from .contracts import digest
from .gateway import ModelBlocked

MARKER_KEY = 'budget_policy_v1'
DEEPSEEK_UNLIMITED = 'deepseek_unlimited'


def _provider(db, p, cap, *, lock=False):
    if not p or not getattr(cap, 'id', None) or getattr(cap, 'workspace_id', None) != p.workspace_id:
        raise ModelBlocked('模型能力或工作区无法核对，未发送')
    # Read raw persisted columns rather than the session identity-map object;
    # an unflushed change to cap.data must not become admission authority.
    stmt = select(Capability.kind, Capability.status, Capability.content_hash,
                  Capability.validated_hash, Capability.data).where(
        Capability.id == cap.id, Capability.workspace_id == p.workspace_id)
    if lock:
        stmt = stmt.with_for_update(read=True)
    with db.no_autoflush:
        persisted = db.execute(stmt).one_or_none()
    if (not persisted or persisted.kind != 'foundation_model' or persisted.status != 'published'
            or not persisted.content_hash or persisted.validated_hash != persisted.content_hash
            or digest(persisted.data) != persisted.content_hash
            or getattr(cap, 'content_hash', None) != persisted.content_hash
            or getattr(cap, 'data', None) != persisted.data):
        raise ModelBlocked('已发布模型能力或当前执行配置无法核对，未发送')
    execution = persisted.data.get('execution') if isinstance(persisted.data, dict) else None
    provider = execution.get('provider') if isinstance(execution, dict) else None
    if provider not in {'deepseek', 'qwen'}:
        raise ModelBlocked('已发布模型的供应商无法核对，未发送')
    return provider


def budget_status(db, p, cap, token_bound, *, day=None, lock=False):
    """Read-only admission snapshot, never a permit or provider usage report.

    used_tokens/calls describe the *limited-provider pool*, including legacy
    unattributed usage; they are not DeepSeek's own usage. Actual reserve must
    recheck under the daily row lock, regardless of this preview result.
    """
    if type(token_bound) is not int or token_bound < 0:
        raise ModelBlocked('模型调用用量无法核对，未发送')
    provider = _provider(db, p, cap, lock=lock)
    day = day or str(now().date())
    try:
        if type(day) is not str or date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError:
        raise ModelBlocked('云调用账本日期无法核对，未发送') from None
    # Read total and tagged calls in one SQL statement. A preview does not hold
    # the ledger lock, so separate SELECTs could straddle a settled transaction
    # and falsely report an inconsistent ledger. The reservation caller has
    # already locked the total row before this single-snapshot read.
    query = (select(Job,
                    func.coalesce(func.sum(CloudCall.charged_tokens).over(), 0),
                    func.count(CloudCall.id).over(), func.min(CloudCall.charged_tokens).over())
        .select_from(Job)
        .outerjoin(CloudCall, and_(CloudCall.workspace_id == p.workspace_id,
                                  CloudCall.data['day'].astext == day,
                                  CloudCall.data[MARKER_KEY].astext == DEEPSEEK_UNLIMITED))
        .where(Job.workspace_id == p.workspace_id, Job.idempotency_key == 'cloud-budget:' + day)
        .limit(1).execution_options(populate_existing=True))
    record = db.execute(query).one_or_none()
    if record is None:
        # The typed Job payload cannot be decoded from an outer-joined NULL
        # entity. A second read checks for impossible orphan markers; a new
        # concurrent reservation can only make this preview fail safely.
        orphan = db.scalar(select(func.count(CloudCall.id)).where(
            CloudCall.workspace_id == p.workspace_id, CloudCall.data['day'].astext == day,
            CloudCall.data[MARKER_KEY].astext == DEEPSEEK_UNLIMITED))
        if orphan:
            raise ModelBlocked('云调用用量记录与总账不一致，未发送')
        data = {'day': day, 'tokens': 0, 'calls': 0}
        deep_tokens, deep_calls, minimum = 0, 0, None
    else:
        ledger, deep_tokens, deep_calls, minimum = record
        if ledger.kind != 'cloud_budget' or ledger.status != 'internal':
            raise ModelBlocked('云调用账本无法核对，未发送')
        data = ledger.data
    if (not isinstance(data, dict) or data.get('day') != day
            or type(data.get('tokens')) is not int or data['tokens'] < 0
            or type(data.get('calls')) is not int or data['calls'] < 0):
        raise ModelBlocked('云调用账本无法核对，未发送')
    if (type(deep_tokens) is not int or type(deep_calls) is not int
            or (minimum is not None and minimum < 0)
            or deep_tokens > data['tokens'] or deep_calls > data['calls']):
        raise ModelBlocked('云调用用量记录与总账不一致，未发送')
    used_tokens, used_calls = data['tokens'] - deep_tokens, data['calls'] - deep_calls
    token_limit, call_limit = settings().cloud_daily_token_limit, settings().cloud_daily_call_limit
    if type(token_limit) is not int or token_limit < 0 or type(call_limit) is not int or call_limit < 0:
        raise ModelBlocked('云调用用量策略无法核对，未发送')
    unlimited = provider == 'deepseek'
    return {'provider': provider, 'unlimited': unlimited,
            'used_tokens': used_tokens, 'used_calls': used_calls,
            'token_limit': None if unlimited else token_limit,
            'call_limit': None if unlimited else call_limit,
            'available': unlimited or (used_tokens + token_bound <= token_limit and used_calls < call_limit)}
