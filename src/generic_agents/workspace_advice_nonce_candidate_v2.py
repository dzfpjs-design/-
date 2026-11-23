"""Candidate shared nonce guard; intentionally not wired to routes or workers.

Requires every participating writer to call it before taking a Task or actor
lock. Old processes are not compatible. This is not a release-ready
integration: price, policy and command callers do not yet all use it.
"""
from functools import wraps
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import or_, text

from . import workspace_command_nonce_lock_v312 as legacy
from .contracts import digest
from .models import Audit, Plan

V1_POLICY_REQUEST = 'same_goal_advice.quota_authorized_v304'
V2_POLICY_REQUEST = 'same_goal_advice.unlimited_policy_request_v2'
PRICE_BASIS = 'same_goal_advice.official_price_registered_v307'
PRICE_WRAPPER = 'same_goal_advice.task_price_registered_v310'
QUOTA_WRAPPER = 'same_goal_advice.task_quota_configured_v310'
EXTRA_ACTIONS = frozenset({V1_POLICY_REQUEST, V2_POLICY_REQUEST, PRICE_BASIS})


def checked(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, StopIteration,
                UnicodeError, RecursionError, OverflowError):
            legacy.need(False, '原申请编号或关联凭据无法核对；请保留原记录，未创建替代申请')
    return wrapper


@checked
def acquire(db, p, *, action, task_id, body, subject_id=None, advice_id=None):
    """Serialize candidate command and authenticate any existing nonce owner.

    Does not grant authorization or create a record. Successful replays must
    still use the original route's receipt checks. Not safe with older API
    processes or any writer that does not participate in this protocol.
    """
    legacy.need(action in legacy.ACTIONS | {V1_POLICY_REQUEST, V2_POLICY_REQUEST, PRICE_BASIS})
    legacy.need(type(body) is dict and 'request_id' in body)
    request_id = legacy._id(body['request_id'])
    if action == PRICE_BASIS:
        legacy.need(task_id is None and type(body.get('model_pin')) is dict)
        model_id = legacy._id(body['model_pin'].get('id'))
        legacy.need(subject_id in {None, model_id} and advice_id is None)
    else:
        legacy._id(task_id)
    legacy._id(p.user_id)
    if subject_id is not None: legacy._id(subject_id)
    if advice_id is not None: legacy._id(advice_id)
    with db.no_autoflush:
        # A snapshot acquired before waiting on the advisory lock could miss
        # a just-committed owner. Require fresh statement snapshots instead.
        legacy.need(db.scalar(text("SELECT current_setting('transaction_isolation')")) == 'read committed',
            '当前数据库事务隔离级别不能安全核对请求编号；未创建申请')
        db.execute(text(legacy.LOCK_SQL), {'workspace_command_nonce_key': legacy.key(p.workspace_id, request_id)})
        events = legacy._rows(db, p, Audit, Audit.action.in_(legacy.ACTIONS | EXTRA_ACTIONS), or_(
            Audit.data['request_id'].as_string() == request_id,
            Audit.data['request']['request_id'].as_string() == request_id,
            Audit.data['body']['request_id'].as_string() == request_id))
        pointers = legacy._rows(db, p, Plan, or_(
            Plan.data['operations'].contains([{'request_id': request_id}]),
            Plan.data['revisions'].contains([{'request_id': request_id}])))
        legacy.need(not (len(events) == 1 and events[0].action in {QUOTA_WRAPPER, PRICE_WRAPPER}),
            '原配置缺少对应授权记录，请保留原记录核对')
        if len(events) <= 1 and not any(event.action in EXTRA_ACTIONS for event in events):
            if action in EXTRA_ACTIONS:
                legacy.need(not events and not pointers)
                return
            return legacy.acquire(db, p, action=action, task_id=task_id, body=body,
                subject_id=subject_id, advice_id=advice_id)
        legacy.need(not pointers)
        if len(events) == 1:
            event = events[0]
            if event.action == PRICE_BASIS:
                legacy.need(action == PRICE_BASIS and event.actor_id == p.user_id and event.object_id == model_id)
                from . import same_goal_advice_price_v307 as prices
                saved = prices._parse(prices.Registration, event.data)
                legacy.need(saved['workspace_id'] == p.workspace_id
                    and saved['actor_id'] == p.user_id and saved['model_pin'] == body['model_pin']
                    and saved['request_id'] == request_id and saved['body_hash'] == digest(body)
                    and digest(saved['body']) == digest(body))
                return
            legacy.need(event.action == action and action in {V1_POLICY_REQUEST, V2_POLICY_REQUEST}
                and event.actor_id == p.user_id and event.object_id == task_id
                and type(event.data) is dict and event.data.get('task_id') == task_id
                and digest(event.data.get('body')) == digest(body)
                and event.data.get('body_hash') == digest(body))
            return
        legacy.need(len(events) == 2)
        if {event.action for event in events} == {PRICE_WRAPPER, PRICE_BASIS}:
            from . import same_goal_advice_options_v310 as options
            wrapper = next(event for event in events if event.action == PRICE_WRAPPER)
            original = next(event for event in events if event.action == PRICE_BASIS)
            # _shape only needs the historical task identity. The standalone
            # price receipt remains recoverable even if the task is retired;
            # no task authorization is inferred from this lightweight value.
            historical_task = SimpleNamespace(id=legacy._id(wrapper.object_id))
            legacy.need(wrapper.actor_id == original.actor_id == p.user_id)
            value = options._shape(p, historical_task, wrapper)
            actual = options._price_anchor(db, p, wrapper, value)
            legacy.need(actual.id == original.id and digest(value['body']) == digest(body))
            if action == PRICE_WRAPPER:
                legacy.need(task_id == wrapper.object_id and subject_id in {None, task_id} and advice_id is None)
            else:
                legacy.need(action == PRICE_BASIS and original.object_id == model_id)
            return
        wrappers = [event for event in events if event.action == QUOTA_WRAPPER]
        originals = [event for event in events if event.action == V1_POLICY_REQUEST]
        legacy.need(len(wrappers) == len(originals) == 1)
        wrapper, original = wrappers[0], originals[0]
        legacy.need(wrapper.object_id == original.object_id == task_id
            and wrapper.actor_id == original.actor_id == p.user_id
            and (subject_id is None or (action == QUOTA_WRAPPER and subject_id == task_id))
            and advice_id is None)
        # This is the existing server-owned full v310 authorization, policy
        # and original price-evidence verification, not an Audit count guess.
        from . import same_goal_advice_options_v310 as options
        from . import same_goal_advice_store_v302 as store
        task = store.existing._task(db, p, task_id, lock=True)
        value = options._shape(p, task, wrapper)
        actual, _, _ = options._quota_anchors(db, p, task, wrapper, value)
        legacy.need(actual.id == original.id)
        expected = value['body'] if action == QUOTA_WRAPPER else value['authorization_body']
        legacy.need(action in {QUOTA_WRAPPER, V1_POLICY_REQUEST} and digest(expected) == digest(body))
