"""Versioned DeepSeek advice policy; records configuration, never dispatches.

There is deliberately no HTTP route, worker registration or cloud sender here.
An accepted policy is not permission to send. The existing source, preview,
lease, reservation, transport-intent and receipt gates must all be adapted to
this version before it can be used for an analysis.
"""
import copy
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr, TypeAdapter, field_validator
from sqlalchemy import func, select

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_quota_v304 as old
from . import same_goal_advice_store_v302 as store
from .contracts import digest
from .models import Audit, uid
from .relations import lock_current_actor
from .security import Principal, require
from .temporal import instant

POLICY = 'same_goal_advice.unlimited_policy_authorized_v2'
AUTHORIZATION = 'same_goal_advice.unlimited_policy_request_v2'
POLICY_SCHEMA = 'same-goal-advice-unlimited-policy-v2'
AUTHORIZATION_SCHEMA = 'same-goal-advice-unlimited-request-v2'


class RequestedLimits(c.Closed):
    max_tokens: Annotated[StrictInt, Field(ge=128, le=8192)]
    max_request_bytes: Annotated[StrictInt, Field(ge=1, le=50000)]
    max_evidence_items: Annotated[StrictInt, Field(ge=1, le=2)]


class Authorize(c.Closed):
    request_id: c.ID
    expected_goal_hash: c.Hash
    expected_mature_head_hash: c.Hash
    limits: RequestedLimits
    expires_at: StrictStr
    reason: Annotated[StrictStr, Field(min_length=1, max_length=2000, pattern=r'\S')]

    @field_validator('expires_at')
    @classmethod
    def aware_expiry(cls, value):
        instant(value)
        return value


class Limits(RequestedLimits):
    policy_ref: c.Ref
    provider: Literal['deepseek']
    cost_policy: Literal['unlimited']
    # One limit applies to each separately identified Plan, not to the
    # lifetime of this policy. Sequential new Plans must not exhaust it.
    allocation_basis: Literal['per_plan']
    max_actual_calls: Literal[1]
    reserved_calls: Literal[1]
    reserved_steps: Literal[1]
    goal_max_tool_calls: c.Positive
    goal_max_steps: c.Positive

    @field_validator('max_actual_calls', 'reserved_calls', 'reserved_steps', mode='before')
    @classmethod
    def exact_one(cls, value):
        c.need(type(value) is int and value == 1)
        return value


def _scope(value):
    c.need(type(value) is dict and 'analysis_plan_id' not in value)
    checked = c._parse(c.ExecutionScope, {**value, 'analysis_plan_id': uid()})
    checked.pop('analysis_plan_id')
    c.need(checked == value)
    return checked


def policy_shape(event, now):
    value = event.data
    store.need(event.action == POLICY and type(value) is dict
        and set(value) == {'schema', 'scope', 'source_business_capability_ref',
            'advice_capability_ref', 'model_pin', 'mature_head_hash', 'issued_at', 'expires_at', 'limits', 'record_hash'}
        and value['schema'] == POLICY_SCHEMA and value['record_hash'] == c.record_hash(value))
    scope = _scope(value['scope'])
    store.need(scope['workspace_id'] == event.workspace_id and scope['task_id'] == event.object_id)
    for name in ('source_business_capability_ref', 'advice_capability_ref', 'model_pin'):
        c._parse(c.CapabilityPin, value[name])
    TypeAdapter(c.Hash).validate_python(value['mature_head_hash'])
    issued, expires = instant(value['issued_at']), instant(value['expires_at'])
    store.need(type(now) is datetime and now.tzinfo is not None
        and event.created_at is not None and event.created_at.tzinfo is not None
        and issued <= event.created_at <= now and issued < expires
        and expires <= issued + old.MAX_VALIDITY)
    parsed = c._parse(Limits, {**value['limits'], 'policy_ref': {'id': event.id, 'record_hash': digest(value)}})
    store.need(set(value['limits']) == set(Limits.model_fields) - {'policy_ref'})
    return value, parsed


def assert_no_active_v2(db, p, task, scope, now):
    """Must be called while holding the same task row lock as old authorize."""
    _scope(scope)
    for event in store._rows(db, p, Audit, Audit.action == POLICY, Audit.object_id == task.id):
        value, _ = policy_shape(event, now)
        store.need(value['scope'] != scope or instant(value['expires_at']) <= now,
            '原目标已有尚未到期的 DeepSeek 不限金额政策，未叠加第二份政策')


def _auth_body(task, p, body, policy):
    return {'schema': AUTHORIZATION_SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'actor_id': p.user_id, 'request_id': body['request_id'], 'body': copy.deepcopy(body),
        'body_hash': digest(body), 'policy_ref': {'id': policy.id, 'record_hash': digest(policy.data)},
        'issued_at': policy.data['issued_at'], 'cloud_send_authorized': False}


def _receipt(db, p, task, body, event, now):
    value = event.data
    store.need(event.workspace_id == p.workspace_id and event.object_id == task.id and event.actor_id == p.user_id
        and value['request_id'] == body['request_id'] and value['body_hash'] == digest(body)
        and digest(value['body']) == digest(body))
    rows = store._rows(db, p, Audit, Audit.action == POLICY, Audit.object_id == task.id,
        Audit.id == value['policy_ref']['id'])
    store.need(len(rows) == 1)
    policy = rows[0]
    saved, _ = policy_shape(policy, now)
    store.need(policy.actor_id == event.actor_id and policy.created_at == event.created_at
        and digest(value) == digest(_auth_body(task, p, body, policy))
        and saved['scope']['goal_hash'] == body['expected_goal_hash']
        and saved['mature_head_hash'] == body['expected_mature_head_hash']
        and saved['expires_at'] == instant(body['expires_at']).isoformat())
    return {'schema': AUTHORIZATION_SCHEMA, 'task_id': task.id, 'request_id': body['request_id'],
        'policy_ref': copy.deepcopy(value['policy_ref']), 'limits': copy.deepcopy(saved['limits']),
        'issued_at': saved['issued_at'], 'expires_at': saved['expires_at'],
        'configuration_only': True, 'cloud_send_authorized': False}


@store.checked
def authorize(db, p, task_id, body):
    """Record a new policy under the v1 task lock; caller owns transaction.

    No transport/start route is installed. Deploying alongside an older API
    process that does not understand this policy is not safe.
    """
    require(p, 'admin')
    store.need(type(p) is Principal)
    body = Authorize.model_validate(body).model_dump(mode='json')
    store._reader(db, p, write=True)
    task = store.existing._task(db, p, task_id, lock=True)
    lock_current_actor(db, p)
    store._member(db, p, p.user_id, admin=True)
    now = db.scalar(select(func.clock_timestamp()))
    store.need(type(now) is datetime and now.tzinfo is not None)
    originals = store._rows(db, p, Audit, Audit.action == AUTHORIZATION, Audit.object_id == task.id,
        Audit.data['request_id'].as_string() == body['request_id'])
    store.need(len(originals) <= 1)
    if originals:
        return _receipt(db, p, task, body, originals[0], now)
    store.need(not store._rows(db, p, Audit, Audit.action == old.AUTHORIZATION, Audit.object_id == task.id,
        Audit.data['request_id'].as_string() == body['request_id']), '原请求编号已用于另一版政策')
    # This policy is exclusively for the DeepSeek unlimited-usage path.
    # Preserve the original Goal, source, actor and published-model checks,
    # but do not charge this request against the Goal's generic tool/step
    # capacity; that capacity continues to apply to other workflow tools.
    context = old._configuration_base(db, p, task, body)
    expires = instant(body['expires_at'])
    store.need(now < expires <= now + old.MAX_VALIDITY)
    deadline = context['goal']['spec'].get('deadline')
    store.need(deadline is None or expires <= instant(deadline))
    model, requested = context['model'], body['limits']
    execution = model.data['execution']
    store.need(execution['provider'] == 'deepseek' and type(execution.get('max_tokens')) is int
        and requested['max_tokens'] <= execution['max_tokens'])
    # Removing a usage limit must not make unknown or inconsistent historical
    # operations acceptable. Recheck the versioned inventory under its normal
    # locks, without comparing unrelated workflow counters to Goal budgets.
    from . import goal_budget_versioned_snapshot_candidate_v3 as capacity
    capacity.read_locked(db, p, task)
    store.need(not context['document_required'] or requested['max_evidence_items'] == 2)
    assert_no_active_v2(db, p, task, context['scope'], now)
    for previous in store._rows(db, p, Audit, Audit.action == store.QUOTA_POLICY, Audit.object_id == task.id):
        saved, _ = old._policy_shape(previous, now)
        store.need(saved['scope'] != context['scope'] or instant(saved['expires_at']) <= now,
            '原目标已有尚未到期的旧版政策，未叠加第二份政策')
    identity = uid()
    limits = {**requested, 'provider': 'deepseek', 'cost_policy': 'unlimited', 'allocation_basis': 'per_plan',
        'max_actual_calls': 1, 'reserved_calls': 1, 'reserved_steps': 1,
        'goal_max_tool_calls': context['goal']['spec']['max_tool_calls'],
        'goal_max_steps': context['goal']['spec']['max_steps']}
    limits = c._parse(Limits, {**limits, 'policy_ref': {'id': identity, 'record_hash': '0' * 64}})
    limits.pop('policy_ref')
    value = {'schema': POLICY_SCHEMA, 'scope': _scope(context['scope']),
        'source_business_capability_ref': store._pin(context['source']),
        'advice_capability_ref': store._pin(context['advice']), 'model_pin': store._pin(model),
        'mature_head_hash': body['expected_mature_head_hash'],
        'issued_at': now.isoformat(), 'expires_at': expires.isoformat(), 'limits': limits}
    value['record_hash'] = c.record_hash(value)
    policy = Audit(id=identity, workspace_id=p.workspace_id, actor_id=p.user_id,
        action=POLICY, object_id=task.id, created_at=now, data=value)
    authorization = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        action=AUTHORIZATION, object_id=task.id, created_at=now, data=_auth_body(task, p, body, policy))
    with db.begin_nested():
        db.add(policy); db.add(authorization); db.flush()
        return _receipt(db, p, task, body, authorization, now)
