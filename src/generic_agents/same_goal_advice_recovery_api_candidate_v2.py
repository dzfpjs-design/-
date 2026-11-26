"""Authenticated recovery discovery, disabled and disposable-PG-only.

This candidate reads only original permit records for the selected task. It
never dispatches, retries, removes a spool, or infers that a request was unsent.
The encrypted response is never returned by these endpoints.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import case, event, func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .db import engine
from .gateway import ModelBlocked
from .models import Audit, AuthSession, CloudCall, Job, Plan
from .relations import lock_current_actor
from .setup_api import DB, P
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_dispatch_permit_candidate_v2 as permits
from . import same_goal_advice_transport_intent_candidate_v2 as intents
from . import same_goal_advice_cloud_claim_test_v2 as claims
from . import same_goal_advice_cloud_reservation_candidate_v2 as reservation
from . import same_goal_advice_first_response_storage_candidate_v2 as storage
from . import same_goal_advice_first_response_candidate_v2 as response
from . import same_goal_advice_response_spool_test_v2 as spool
from . import same_goal_advice_spool_recovery_candidate_v2 as recovery

router = APIRouter()
BASE = '/tasks/{task_id}/business-advice/response-recovery'
PAGE_SIZE = 25
UUID_PATTERN = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'


def _access(db, p, task_id):
    if not settings().v2_advice_recovery_console_enabled:
        raise HTTPException(404, '该功能尚未启用')
    guard.require_disposable_database(db)
    old.need(settings().v2_response_spool_enabled and settings().v2_response_spool_root is not None)
    old._reader(db, p, write=True)
    old.need(p.role == 'admin', '仅工作区管理员可核对恢复状态')
    lock_current_actor(db, p)
    old.existing._task(db, p, task_id)


def _identities(db, p, task_id, permit_id=None, cursor=None):
    _access(db, p, task_id)
    # Match any surviving original link, not only a possibly damaged task_id.
    # Every subquery is workspace-scoped; the subsequent original-record gate
    # validates the complete chain before showing ready or allowing recovery.
    plans = select(Plan.id).where(Plan.workspace_id == p.workspace_id, Plan.task_id == task_id)
    jobs = select(Job.id).where(Job.workspace_id == p.workspace_id, or_(
        Job.data['task_id'].as_string() == task_id, Job.data['plan_id'].as_string().in_(plans)))
    calls = select(CloudCall.id).where(CloudCall.workspace_id == p.workspace_id, or_(
        CloudCall.data['same_goal_advice_unlimited_v2']['task_id'].as_string() == task_id,
        CloudCall.data['same_goal_advice_unlimited_v2']['plan_id'].as_string().in_(plans),
        CloudCall.data['same_goal_advice_unlimited_v2']['job_id'].as_string().in_(jobs)))
    conditions = (Audit.workspace_id == p.workspace_id,
        or_(Audit.action.in_([permits.ACTION, intents.ACTION, claims.ACTION]),
            Audit.data['schema'].as_string().in_([permits.SCHEMA, intents.SCHEMA, reservation.SCHEMA])),
        or_(Audit.data['task_id'].as_string() == task_id, Audit.object_id.in_(plans),
            Audit.data['plan_id'].as_string().in_(plans), Audit.data['job_id'].as_string().in_(jobs),
            Audit.data['cloud_call_id'].as_string().in_(calls), Audit.data['call_id'].as_string().in_(calls)))
    if permit_id is not None:
        rows = [(row, None) for row in db.scalars(select(Audit).where(*conditions,
            Audit.id == old.existing._id(permit_id), Audit.action == permits.ACTION))]
        next_cursor = None
    else:
        raw_key = func.coalesce(Audit.data['cloud_call_id'].as_string(), Audit.data['call_id'].as_string())
        group_key = case((raw_key.op('~')(UUID_PATTERN), raw_key), else_=Audit.id)
        priority = case((Audit.action == permits.ACTION, 0), (Audit.action == intents.ACTION, 1), else_=2)
        ranked = select(Audit.id.label('id'), group_key.label('group_key'),
            func.row_number().over(partition_by=group_key,
                order_by=(priority, Audit.created_at, Audit.id)).label('rank')).where(*conditions).subquery()
        query = select(Audit, ranked.c.group_key).join(ranked, Audit.id == ranked.c.id).where(ranked.c.rank == 1)
        if cursor is not None:
            query = query.where(ranked.c.group_key > old.existing._id(cursor))
        rows = list(db.execute(query.order_by(ranked.c.group_key).limit(PAGE_SIZE + 1)))
        next_cursor = rows[PAGE_SIZE-1][1] if len(rows) > PAGE_SIZE else None
        rows = rows[:PAGE_SIZE]
    result = []
    for row, _ in rows:
        value = row.data if type(row.data) is dict else {}
        ref = value.get('intent_ref')
        result.append((row.id, value.get('cloud_call_id'), ref.get('id') if type(ref) is dict else None,
            row.created_at.isoformat(), row.action == permits.ACTION))
    return result, next_cursor


def _status(workspace_id, permit_id, call_id, intent_id):
    try:
        context = recovery.current_context(workspace_id, call_id, intent_id, permit_id)
        with Session(engine()) as db:
            saved = storage.read(db, workspace_id, call_id, intent_id)
            call = db.get(CloudCall, call_id)
            if type(saved) is tuple and len(saved) == 3 and call is not None and call.workspace_id == workspace_id:
                if call.status in {'succeeded', 'failed'}:
                    if not _settled_consistent(db, call, saved[2], intent_id):
                        return 'needs_attention'
                    if call.status == 'failed':
                        return 'failed_usage_unknown' if call.data['usage_unavailable'] else 'failed'
                    return 'settled_usage_unknown' if call.data['usage_unavailable'] else 'settled'
                return 'needs_settlement'
        spool.load(settings().v2_response_spool_root, context)
        return 'ready'
    except (HTTPException, ModelBlocked, spool.SpoolError, TypeError, ValueError):
        return 'needs_attention'


def _authorize_capture(db, p, task_id):
    _access(db, p, task_id)
    session_id = old.existing._id(p.session_id)
    def before_commit(session):
        valid = session.scalar(select(AuthSession.id).where(AuthSession.id == session_id,
            AuthSession.user_id == p.user_id, AuthSession.revoked.is_(False),
            AuthSession.expires_at > func.clock_timestamp()))
        old.need(valid == session_id, '登录已过期，未保存恢复结果')
    event.listen(db, 'before_commit', before_commit, once=True)


def _settled_consistent(db, call, sealed, intent_id):
    """Read-only, conservative check before claiming the usage is saved."""
    proven = response.proven_receipt(sealed, cloud_call_id=call.id, intent_audit_id=intent_id)
    try:
        response.parse_saved(sealed, cloud_call_id=call.id, intent_audit_id=intent_id,
            schema=contracts.model_value_schema())
        answer_state = 'succeeded'
    except ModelBlocked:
        answer_state = 'failed'
    if call.status != answer_state:
        return False
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
def listing(task_id: str, p: P, db: DB, cursor: str | None = None):
    if not settings().v2_advice_recovery_console_enabled:
        return {'enabled': False, 'items': []}
    identities, next_cursor = _identities(db, p, task_id, cursor=cursor)
    db.rollback()  # Never hold account/task locks across file IO.
    items = [{'permit_id': permit, 'state': _status(p.workspace_id, permit, call, intent) if is_permit else 'needs_attention',
        'recorded_at': recorded_at, 'ledger_verified': False} for permit, call, intent, recorded_at, is_permit in identities]
    _access(db, p, task_id)  # Recheck current role/session after IO.
    return {'enabled': True, 'items': items, 'next_cursor': next_cursor}


@router.post(BASE + '/{permit_id}/recover')
def recover(task_id: str, permit_id: str, p: P, db: DB):
    identities, _ = _identities(db, p, task_id, permit_id)
    if len(identities) != 1:
        raise HTTPException(404, '未找到属于当前业务的原记录')
    permit, call, intent, _, _ = identities[0]
    db.rollback()
    try:
        outcome = recovery.recover(p.workspace_id, call, intent, permit,
            authorize_capture=lambda session: _authorize_capture(session, p, task_id))
    except (HTTPException, ModelBlocked, spool.SpoolError, TypeError, ValueError):
        raise HTTPException(409, '原结果无法自动恢复，请核对；不会重复发送') from None
    _access(db, p, task_id)
    return {'state': outcome['state'], 'answer_state': outcome['answer_state'],
        'usage_unavailable': outcome['usage_unavailable'], 'ledger_verified': False, 'retry_authorized': False}
