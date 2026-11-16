"""Serialize the same workspace nonce before every advice/continuation writer.

The fixed namespace is shared across families and Tasks. A lock or a matching
route is not authorization: the original family still authenticates its full
command, positive effects, source, current actor and normal replay behavior.
"""
import hashlib
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select, text

from .contracts import digest
from .models import Audit, Plan

NAMESPACE = 'workspace-advice-continuation-command-nonce-v312'
MAX_ROWS = 1000
ACTIONS = frozenset({'same_goal_advice.preparation_accepted_v302',
    'same_goal_advice.preparation_cancelled_v302', 'same_goal_advice.preview_prepared_v307',
    'same_goal_advice.started_v308', 'same_goal_advice.human_reviewed_v308',
    'same_goal_advice.task_price_registered_v310', 'same_goal_advice.task_quota_configured_v310',
    'same_goal_continuation.saved_v312', 'same_goal_continuation.revised_v312',
    'same_goal_continuation.withdrawn_v312',
    'same_goal_continuation.grant_issued_v313',
    'same_goal_continuation.grant_revoked_v313',
    'same_goal_continuation.execution_submitted_v314', 'same_goal_continuation.execution_decided_v314',
    'same_goal_continuation.execution_started_v314', 'same_goal_continuation.execution_cancelled_v314',
    'same_goal_continuation.execution_reviewed_v314', 'same_goal_continuation.execution_recovered_v314',
    'workflow.formal_continuation.create_v345', 'workflow.formal_continuation.activate_v345',
    'workflow.formal_continuation.complete_step_v345', 'workflow.formal_continuation.return_step_v345',
    'workflow.formal_continuation.withdraw_v345', 'workflow.formal_continuation.abandoned_v345'})
LOCK_SQL = 'SELECT pg_advisory_xact_lock(:workspace_command_nonce_key)'


def need(value, detail='此请求编号已用于不同申请或原件不完整，请保留原编号和正文核查'):
    if not value: raise HTTPException(409, detail)


def _id(value):
    try: need(type(value) is str and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError): need(False)
    return value


def key(workspace_id, request_id):
    """Pure key; a rare digest collision only serializes unrelated requests."""
    payload = NAMESPACE.encode('ascii') + b'\x00' + UUID(_id(workspace_id)).bytes + UUID(_id(request_id)).bytes
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], byteorder='big', signed=True)


def _rows(db, p, model, *where):
    rows = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id, *where)
        .limit(MAX_ROWS + 1).execution_options(populate_existing=True)))
    need(len(rows) <= MAX_ROWS)
    return rows


def acquire(db, p, *, action, task_id, body, subject_id=None, advice_id=None):
    """Lock first, then reject a foreign positive original; never mint/commit.

    All callers invoke this before Task/Plan/actor locks and their own nonce
    lookups. The lock lasts to the caller's real commit/rollback, including
    normal original-body replay. Pure preview and GET never call this helper.
    """
    need(action in ACTIONS and type(body) is dict and 'request_id' in body)
    request_id = _id(body['request_id']);task_id = _id(task_id)
    if subject_id is not None: _id(subject_id)
    if advice_id is not None: _id(advice_id)
    _id(p.user_id)
    with db.no_autoflush:
        db.execute(text(LOCK_SQL), {'workspace_command_nonce_key': key(p.workspace_id, request_id)})
        events = _rows(db, p, Audit, Audit.action.in_(ACTIONS), or_(
            Audit.data['request_id'].as_string() == request_id,
            Audit.data['request']['request_id'].as_string() == request_id,
            Audit.data['body']['request_id'].as_string() == request_id))
        pointers = _rows(db, p, Plan, or_(
            Plan.data['operations'].contains([{'request_id': request_id}]),
            Plan.data['revisions'].contains([{'request_id': request_id}])))
        need(len(events) <= 1)
        if not events:
            need(not pointers, '原申请仍有已保存正文但保护回执缺失，未按空编号创建替代申请')
            return
        event = events[0];value = event.data
        need(type(value) is dict and event.action == action and event.actor_id == p.user_id
            and value.get('task_id') == task_id
            and (subject_id is None or event.object_id == subject_id)
            and (advice_id is None or value.get('advice_plan_id') == advice_id))
        # Preview's old request_hash intentionally wraps Task/Plan/body. Check
        # its actual closed request, preserving that existing hash unchanged.
        if 'request' in value: need(type(value['request']) is dict and digest(value['request']) == digest(body))
        elif 'body' in value: need(type(value['body']) is dict and digest(value['body']) == digest(body))
        else: need(value.get('request_id') == request_id and value.get('body_hash') == digest(body))
        need(len(pointers) <= 1 and all(row.id == event.object_id and row.task_id == task_id for row in pointers))
