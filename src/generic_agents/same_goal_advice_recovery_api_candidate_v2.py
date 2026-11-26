"""Authenticated recovery discovery, disabled and disposable-PG-only.

This candidate reads only original permit records for the selected task. It
never dispatches, retries, removes a spool, or infers that a request was unsent.
The encrypted response is never returned by these endpoints.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import engine
from .gateway import ModelBlocked
from .models import Audit, CloudCall, Job
from .relations import lock_current_actor
from .setup_api import DB, P
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_dispatch_permit_candidate_v2 as permits
from . import same_goal_advice_first_response_storage_candidate_v2 as storage
from . import same_goal_advice_first_response_candidate_v2 as response
from . import same_goal_advice_response_spool_test_v2 as spool
from . import same_goal_advice_spool_recovery_candidate_v2 as recovery

router = APIRouter()
BASE = '/tasks/{task_id}/business-advice/response-recovery'
MAX_ITEMS = 100


def _access(db, p, task_id):
    if not settings().v2_advice_recovery_console_enabled:
        raise HTTPException(404, '该功能尚未启用')
    guard.require_disposable_database(db)
    old.need(settings().v2_response_spool_enabled and settings().v2_response_spool_root is not None)
    old._reader(db, p, write=True)
    old.need(p.role == 'admin', '仅工作区管理员可核对恢复状态')
    lock_current_actor(db, p)
    old.existing._task(db, p, task_id)


def _identities(db, p, task_id, permit_id=None):
    _access(db, p, task_id)
    query = select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == permits.ACTION, Audit.data['task_id'].as_string() == task_id)
    if permit_id is not None:
        query = query.where(Audit.id == old.existing._id(permit_id))
    rows = list(db.scalars(query.order_by(Audit.created_at.desc(), Audit.id.desc()).limit(MAX_ITEMS + 1)))
    old.need(len(rows) <= MAX_ITEMS, '记录过多，暂无法安全核对')
    result = []
    for row in rows:
        value = row.data if type(row.data) is dict else {}
        ref = value.get('intent_ref')
        result.append((row.id, value.get('cloud_call_id'), ref.get('id') if type(ref) is dict else None,
            row.created_at.isoformat()))
    return result


def _status(workspace_id, permit_id, call_id, intent_id):
    try:
        context = recovery.current_context(workspace_id, call_id, intent_id, permit_id)
        with Session(engine()) as db:
            saved = storage.read(db, workspace_id, call_id, intent_id)
            call = db.get(CloudCall, call_id)
            if type(saved) is tuple and len(saved) == 3 and call is not None and call.workspace_id == workspace_id:
                if call.status in {'succeeded', 'failed'}:
                    return 'settled' if _settled_consistent(db, call, saved[2], intent_id) else 'needs_attention'
                return 'needs_settlement'
        spool.load(settings().v2_response_spool_root, context)
        return 'ready'
    except (HTTPException, ModelBlocked, spool.SpoolError, TypeError, ValueError):
        return 'needs_attention'


def _settled_consistent(db, call, sealed, intent_id):
    """Read-only, conservative check before claiming the usage is saved."""
    proven = response.proven_receipt(sealed, cloud_call_id=call.id, intent_audit_id=intent_id)
    data = call.data if type(call.data) is dict else {}
    receipt = data.get('receipt')
    keys = ('call_id', 'provider', 'model', 'usage', 'request_id')
    if type(receipt) is not dict or {k:receipt.get(k) for k in keys} != {k:proven.get(k) for k in keys}:
        return False
    usage = proven.get('usage'); total = usage.get('total_tokens') if type(usage) is dict else None
    available = type(total) is int and total >= 0
    charged = total if available else call.reserved_tokens
    if call.charged_tokens != charged or data.get('usage_unavailable') is not (not available):
        return False
    day = data.get('day')
    if type(day) is not str:
        return False
    budget = db.scalar(select(Job).where(Job.workspace_id == call.workspace_id,
        Job.idempotency_key == 'cloud-budget:' + day))
    return bool(budget and budget.kind == 'cloud_budget' and budget.status == 'internal'
        and type(budget.data) is dict and budget.data.get('day') == day
        and type(budget.data.get('tokens')) is int and budget.data['tokens'] >= charged
        and type(budget.data.get('calls')) is int and budget.data['calls'] >= 1)


@router.get(BASE)
def listing(task_id: str, p: P, db: DB):
    if not settings().v2_advice_recovery_console_enabled:
        return {'enabled': False, 'items': []}
    identities = _identities(db, p, task_id)
    db.rollback()  # Never hold account/task locks across file IO.
    items = [{'permit_id': permit, 'state': _status(p.workspace_id, permit, call, intent), 'recorded_at': recorded_at}
        for permit, call, intent, recorded_at in identities]
    _access(db, p, task_id)  # Recheck current role/session after IO.
    return {'enabled': True, 'items': items}


@router.post(BASE + '/{permit_id}/recover')
def recover(task_id: str, permit_id: str, p: P, db: DB):
    identities = _identities(db, p, task_id, permit_id)
    if len(identities) != 1:
        raise HTTPException(404, '未找到属于当前业务的原记录')
    permit, call, intent, _ = identities[0]
    db.rollback()
    try:
        outcome = recovery.recover(p.workspace_id, call, intent, permit,
            authorize_capture=lambda session: _access(session, p, task_id))
    except (HTTPException, ModelBlocked, spool.SpoolError, TypeError, ValueError):
        raise HTTPException(409, '原结果无法自动恢复，请核对；不会重复发送') from None
    _access(db, p, task_id)
    return {'state': outcome['state'], 'answer_state': outcome['answer_state'],
        'retry_authorized': False}
