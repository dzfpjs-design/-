from sqlalchemy import select
from datetime import date
import re
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from .db import engine
from .models import Job, CloudCall, now
from .config import settings
from .gateway import ModelBlocked
from .contracts import digest
from .cloud_dispatch_v130 import ReservationDenied, current_attempt


class BudgetDenied(ModelBlocked, ReservationDenied):
    """The quota gate rejected before any reservation commit was attempted."""
    pass


def reserve(p, cap, token_bound, request_hash):
    if not p or not getattr(cap, "id", None) or cap.workspace_id != p.workspace_id:
        raise ModelBlocked("云调用缺少已发布能力与工作区授权")
    dispatch = current_attempt()
    dispatch_data = {}
    if dispatch is not None:
        identity = dispatch.identity
        if (identity['workspace_id']!=p.workspace_id or identity['actor_id']!=p.user_id
            or identity['capability_id']!=cap.id):
            raise ModelBlocked('实际预算预留与网关调用身份不一致')
        dispatch_data = {'gateway_dispatch':{'schema':'cloud-dispatch-v130',
            'identity':identity,'attempt_id':dispatch.attempt_id,
            'reservation_request_hash':request_hash}}
    day = str(now().date())
    key = "cloud-budget:" + day
    with Session(engine()) as db:
        from .same_goal_advice_spending_v308 import prepare_reservation, bind_reservation
        spending = prepare_reservation(db, p, cap, token_bound, request_hash)
        # Capability editors lock the capability before any daily ledger. Take
        # a SHARE lock in the same order; no request metadata grants exemption.
        from .cloud_budget_policy import budget_status, _provider, MARKER_KEY, DEEPSEEK_UNLIMITED
        _provider(db, p, cap, lock=True)
        db.execute(insert(Job).values(workspace_id=p.workspace_id, kind="cloud_budget", status="internal",
            idempotency_key=key, data={"day": day, "tokens": 0, "calls": 0})
            .on_conflict_do_nothing(index_elements=["workspace_id", "idempotency_key"]))
        budget = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key == key).with_for_update())
        admission = budget_status(db, p, cap, token_bound, day=day)
        if not admission['available']:
            raise BudgetDenied("已达到工作区每日云模型预算，请等待额度恢复或由管理员调整")
        budget.data = {**budget.data, "tokens": budget.data["tokens"]+token_bound, "calls": budget.data["calls"]+1}
        policy_data = ({MARKER_KEY: DEEPSEEK_UNLIMITED} if admission['unlimited'] else {})
        call = CloudCall(workspace_id=p.workspace_id, capability_id=cap.id, reserved_tokens=token_bound,
            charged_tokens=token_bound, data={"day": day, "request_hash": request_hash, "actor_id": p.user_id, **dispatch_data, **policy_data})
        db.add(call); db.flush()
        bind_reservation(db, p, cap, call, spending)
        call_id = call.id; db.commit(); return call_id


def finish_tx(db, call_id, receipt=None, status="failed"):
    """The original finish operation inside its caller's transaction, no commit.

    Return whether a reserved row changed. Authorization and the meaning of an
    unknown response belong to the caller; this is never a dispatch permit.
    The legacy wrapper below retains its previous arguments and exact behavior.
    """
    call = db.scalar(select(CloudCall).where(CloudCall.id == call_id).with_for_update())
    if call.status != "reserved":
        return False
    charged = (receipt or {}).get("usage", {}).get("total_tokens", call.reserved_tokens)
    if not isinstance(charged, int) or charged < 0:
        charged = call.reserved_tokens
    budget = db.scalar(select(Job).where(Job.workspace_id == call.workspace_id,
        Job.idempotency_key == "cloud-budget:" + call.data["day"]).with_for_update())
    budget.data = {**budget.data, "tokens": budget.data["tokens"]-call.reserved_tokens+charged}
    call.status = status; call.charged_tokens = charged
    call.data = {**call.data, "receipt": receipt or {}, "finished_at": now().isoformat()}
    return True


def finish(call_id, receipt=None, status="failed"):
    with Session(engine()) as db:
        if not finish_tx(db, call_id, receipt, status):
            return
        db.commit()


def settle_paid_response(db, call_id, receipt, expected, status):
    """在调用者事务内保存已付响应的结算；不提交，也不重新授予业务权限。

    expected 固定七项来源声明；由调用者从已冻结请求构造。只允许
    succeeded/failed。没有可信 total_tokens 时保留供应商原 receipt，按
    reserved_tokens 保守记入预算并标为 usage_unavailable，不能捏造用量。
    已结算账本只核对归属与关键回执，原状态/用量/时间保持不变。
    """
    keys = {'workspace_id', 'capability_id', 'actor_id', 'request_hash',
            'dispatch_options_hash', 'provider', 'model'}
    if (not isinstance(expected, dict) or set(expected) != keys
            or any(not isinstance(value, str) or not value for value in expected.values())
            or any(not re.fullmatch(r'[0-9a-f]{64}', expected[key])
                   for key in ('request_hash', 'dispatch_options_hash'))
            or not isinstance(call_id, str) or not call_id
            or not isinstance(status, str) or status not in {'succeeded', 'failed'}):
        raise ModelBlocked('已付响应缺少完整的冻结调用来源')
    if (not isinstance(receipt, dict) or receipt.get('call_id') != call_id
            or receipt.get('provider') != expected['provider']
            or receipt.get('model') != expected['model']):
        raise ModelBlocked('已付响应回执与冻结模型调用不一致')
    call = db.scalar(select(CloudCall).where(CloudCall.id == call_id)
                     .with_for_update().execution_options(populate_existing=True))
    if (not call or not isinstance(call.data, dict)
            or call.workspace_id != expected['workspace_id']
            or call.capability_id != expected['capability_id']
            or any(call.data.get(key) != expected[key]
                   for key in ('actor_id', 'request_hash', 'dispatch_options_hash'))):
        raise ModelBlocked('已付响应调用账本的工作区或来源核对失败')
    day = call.data.get('day')
    try:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError:
        raise ModelBlocked('已付响应调用账本的预算日期无效') from None
    budget = db.scalar(select(Job).where(Job.workspace_id == call.workspace_id,
        Job.idempotency_key == 'cloud-budget:' + day)
        .with_for_update().execution_options(populate_existing=True))
    if (not budget or budget.kind != 'cloud_budget' or budget.status != 'internal'
            or not isinstance(budget.data, dict) or budget.data.get('day') != day
            or type(budget.data.get('tokens')) is not int or budget.data['tokens'] < 0
            or type(budget.data.get('calls')) is not int or budget.data['calls'] < 1):
        raise ModelBlocked('已付响应对应的每日预算账本核对失败')
    if call.status in {'succeeded', 'failed'}:
        saved = call.data.get('receipt')
        critical = ('call_id', 'provider', 'model', 'usage', 'request_id')
        if (not isinstance(saved, dict)
                or digest({key: saved[key] for key in critical if key in saved})
                   != digest({key: receipt[key] for key in critical if key in receipt})):
            raise ModelBlocked('已结算调用与本次已付响应回执不同')
        return call
    if (call.status != 'reserved' or type(call.reserved_tokens) is not int
            or call.reserved_tokens < 0 or budget.data['tokens'] < call.reserved_tokens):
        raise ModelBlocked('已付响应调用的预留预算状态无效')
    usage = receipt.get('usage')
    total = usage.get('total_tokens') if isinstance(usage, dict) else None
    usage_available = type(total) is int and total >= 0
    charged = total if usage_available else call.reserved_tokens
    budget.data = {**budget.data, 'tokens': budget.data['tokens'] - call.reserved_tokens + charged}
    call.status = status
    call.charged_tokens = charged
    call.data = {**call.data, 'receipt': receipt, 'finished_at': now().isoformat(),
                 'usage_unavailable': not usage_available}
    return call
