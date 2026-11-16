"""Explicit administrator commands bound to the original business Task.

These wrappers preserve request identity; neither configuration nor its receipt
is a model send, price lock, consent or a new call allocation. Only the existing
price writer may fetch its fixed official page, on a NEW explicit command.
"""
import copy
from datetime import timedelta
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr, TypeAdapter
from sqlalchemy import or_

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_quota_v304 as quota
from . import same_goal_advice_price_v307 as prices
from .contracts import digest
from .models import Audit, Capability, uid
from .relations import lock_current_actor
from .security import Principal, require
from .temporal import instant

PRICE_SCHEMA = 'same-goal-advice-task-price-option-v310'
QUOTA_SCHEMA = 'same-goal-advice-task-quota-option-v310'
PRICE_ACTION = 'same_goal_advice.task_price_registered_v310'
QUOTA_ACTION = 'same_goal_advice.task_quota_configured_v310'
ACTIONS = frozenset({PRICE_ACTION, QUOTA_ACTION})
need = store.need


class PriceRequest(prices.Authorize):
    pass


class ConfigureQuota(c.Closed):
    request_id: c.ID
    expected_goal_hash: c.Hash
    expected_mature_head_hash: c.Hash
    max_cost_minor_units: Annotated[StrictInt, Field(ge=1, le=10**9)]
    currency: Literal['CNY', 'USD']
    validity_seconds: Annotated[StrictInt, Field(ge=300, le=86400)]
    reason: Annotated[StrictStr, Field(min_length=1, max_length=2000, pattern=r'\S')]


class PriceRecord(c.Closed):
    schema: Literal['same-goal-advice-task-price-option-v310']
    workspace_id: c.ID
    task_id: c.ID
    actor_id: c.ID
    request_id: c.ID
    body: PriceRequest
    body_hash: c.Hash
    price_basis_ref: c.Ref
    recorded_at: StrictStr
    record_hash: c.Hash


class QuotaRecord(c.Closed):
    schema: Literal['same-goal-advice-task-quota-option-v310']
    workspace_id: c.ID
    task_id: c.ID
    actor_id: c.ID
    request_id: c.ID
    body: ConfigureQuota
    body_hash: c.Hash
    model_pin: c.CapabilityPin
    authorization_body: quota.Authorize
    authorization_ref: c.Ref
    policy_ref: c.Ref
    price_basis_ref: c.Ref
    recorded_at: StrictStr
    record_hash: c.Hash


def _ref(event):
    return {'id': event.id, 'record_hash': digest(event.data)}


def _rows(db, p, task, request_id):
    return store._rows(db, p, Audit, Audit.action.in_(ACTIONS),
        or_(Audit.object_id == task.id, Audit.data['task_id'].as_string() == task.id),
        or_(Audit.data['request_id'].as_string() == request_id,
            Audit.data['body']['request_id'].as_string() == request_id))


def _shape(p, task, event):
    kind = PriceRecord if event.action == PRICE_ACTION else QuotaRecord
    need(event.action in ACTIONS)
    value = c._parse(kind, event.data)
    need(event.workspace_id == value['workspace_id'] == p.workspace_id
        and event.object_id == value['task_id'] == task.id and event.actor_id == value['actor_id']
        and value['request_id'] == value['body']['request_id']
        and value['body_hash'] == digest(value['body'])
        and value['record_hash'] == c.record_hash(value)
        and event.created_at is not None and event.created_at.tzinfo is not None
        and instant(value['recorded_at']) == event.created_at)
    return value


def _price_anchor(db, p, event, value):
    original = store._fresh(db, p, Audit, value['price_basis_ref']['id'])
    registered = c._parse(prices.Registration, original.data)
    need(_ref(original) == value['price_basis_ref'] and original.action == prices.PRICE_BASIS
        and original.workspace_id == p.workspace_id and original.actor_id == event.actor_id
        and original.object_id == value['body']['model_pin']['id']
        and registered['actor_id'] == event.actor_id and registered['workspace_id'] == p.workspace_id
        and registered['body_hash'] == value['body_hash'] and digest(registered['body']) == value['body_hash']
        and registered['request_id'] == value['request_id']
        and registered['model_pin'] == registered['body']['model_pin'] == value['body']['model_pin']
        and original.created_at is not None and original.created_at.tzinfo is not None
        and original.created_at <= event.created_at)
    return original


def _quota_anchors(db, p, task, event, value):
    original = store._fresh(db, p, Audit, value['authorization_ref']['id'])
    policy = store._fresh(db, p, Audit, value['policy_ref']['id'])
    resolved = c._parse(quota.Authorize, value['authorization_body']); body = value['body']
    need(original.action == quota.AUTHORIZATION and policy.action == store.QUOTA_POLICY
        and original.workspace_id == policy.workspace_id == p.workspace_id
        and original.object_id == policy.object_id == task.id
        and original.actor_id == policy.actor_id == event.actor_id
        and _ref(original) == value['authorization_ref'] and _ref(policy) == value['policy_ref']
        and original.created_at is not None and policy.created_at == original.created_at
        and original.created_at <= event.created_at
        and resolved['request_id'] == body['request_id']
        and resolved['expected_goal_hash'] == body['expected_goal_hash']
        and resolved['expected_mature_head_hash'] == body['expected_mature_head_hash']
        and resolved['reason'] == body['reason']
        and resolved['limits']['max_cost_minor_units'] == body['max_cost_minor_units']
        and resolved['limits']['currency'] == body['currency']
        and resolved['limits']['max_request_bytes'] == 50000 and resolved['limits']['max_evidence_items'] == 2
        and 128 <= resolved['limits']['max_tokens'] <= 4096)
    expected = {'schema': quota.SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'actor_id': event.actor_id, 'request_id': body['request_id'], 'body': resolved,
        'body_hash': digest(resolved), 'policy_ref': value['policy_ref'],
        'issued_at': policy.data['issued_at'], 'pricing_verified': False, 'cloud_send_authorized': False}
    need(digest(original.data) == digest(expected))
    stored, budget = quota._policy_shape(policy, quota._clock(db))
    need(stored['scope']['workspace_id'] == p.workspace_id and stored['scope']['task_id'] == task.id
        and stored['scope']['goal_hash'] == body['expected_goal_hash']
        and stored['model_pin'] == value['model_pin']
        and stored['expires_at'] == instant(resolved['expires_at']).isoformat()
        and all(digest(stored['limits'][key]) == digest(item) for key, item in resolved['limits'].items())
        and budget['max_actual_calls'] == budget['reserved_calls'] == budget['reserved_steps'] == 1)
    price_event = store._fresh(db, p, Audit, value['price_basis_ref']['id'])
    price_value = c._parse(prices.Registration, price_event.data)
    need(price_event.action == prices.PRICE_BASIS and _ref(price_event) == value['price_basis_ref']
        and price_event.actor_id == price_value['actor_id'] and price_value['workspace_id'] == p.workspace_id
        and price_event.object_id == value['model_pin']['id']
        and price_value['model_pin'] == price_value['body']['model_pin'] == value['model_pin']
        and price_value['body_hash'] == digest(price_value['body'])
        and price_value['request_id'] == price_value['body']['request_id']
        and price_event.created_at is not None and price_event.created_at <= original.created_at
        and instant(price_value['checked_at']) <= original.created_at < instant(price_value['expires_at'])
        and body['currency'] == price_value['parsed_rates']['currency'])
    return original, policy, stored


def _price_result(db, p, original, value):
    return prices.checked_original(db, p, value['body']['model_pin'],
        basis_ref=value['price_basis_ref'], priced_at=original.created_at)


def _quota_result(db, p, task, event, value):
    _original, policy, stored = _quota_anchors(db, p, task, event, value)
    return {'schema': quota.SCHEMA, 'task_id': task.id, 'request_id': value['request_id'],
        'quota_policy_ref': _ref(policy), 'limits': copy.deepcopy(stored['limits']),
        'issued_at': stored['issued_at'], 'expires_at': stored['expires_at'],
        'configuration_only': True, 'pricing_verified': False, 'cloud_send_authorized': False,
        'message': '已保存一次调用的额度上限；发送前仍需核对当前授权、实际令牌与费用，未调用模型'}


@store.checked
def read_receipts(db, p, task, request_id):
    """Protected command custody; underlying result readers never mutate."""
    with db.no_autoflush:
        store._reader(db, p)
        task = store.existing._task(db, p, task.id)
        request_id = TypeAdapter(c.ID).validate_python(request_id)
        rows = _rows(db, p, task, request_id); need(len(rows) <= 1)
        result = []
        for event in rows:
            value = _shape(p, task, event); need(value['request_id'] == request_id)
            if event.action == PRICE_ACTION:
                original = _price_anchor(db, p, event, value)
                def read_result(original=original, value=value):
                    return _price_result(db, p, original, value)
                operation = 'price'
            else:
                _quota_anchors(db, p, task, event, value)
                def read_result(event=event, value=value):
                    return _quota_result(db, p, task, event, value)
                operation = 'quota'
            result.append({'plan': None, 'operation': operation, 'body': copy.deepcopy(value['body']),
                'actor': event.actor_id, 'audit_id': event.id, 'read_result': read_result})
        return result


def _admin_task(db, p, task_id):
    require(p, 'admin'); need(type(p) is Principal)
    store._reader(db, p, write=True)
    task = store.existing._task(db, p, task_id, lock=True)
    lock_current_actor(db, p); store._member(db, p, p.user_id, admin=True)
    return task


def _prior(db, p, task, operation, body):
    candidates = read_receipts(db, p, task, body['request_id'])
    if not candidates: return None
    need(len(candidates) == 1)
    candidate = candidates[0]
    need(candidate['operation'] == operation and candidate['actor'] == p.user_id
        and digest(candidate['body']) == digest(body), '原配置请求已保存其他内容，请保留原编号核查')
    return candidate


def _save(db, p, task, schema, action, body, extra):
    clock = quota._clock(db)
    value = {'schema': schema, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'actor_id': p.user_id, 'request_id': body['request_id'], 'body': copy.deepcopy(body),
        'body_hash': digest(body), 'recorded_at': clock.isoformat(), **copy.deepcopy(extra)}
    value['record_hash'] = c.record_hash(value)
    value = c._parse(PriceRecord if action == PRICE_ACTION else QuotaRecord, value)
    event = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        action=action, object_id=task.id, created_at=clock, data=value)
    db.add(event); db.flush()
    return event


def _fixed_model(db, p, task):
    selected = store.provider.binding(db, p, task); need(selected is not None)
    nodes = [node for node in selected['binding']['graph']['nodes'] if node['id'] == 'reasoning']
    need(len(nodes) == 1 and nodes[0]['kind'] == 'ability' and nodes[0]['execution']['agent_role'] == 'business')
    pin = nodes[0]['execution']; source = store._fresh(db, p, Capability, pin['capability_id'])
    need(source.kind == 'tool' and source.content_hash == source.validated_hash == digest(source.data) == pin['capability_hash']
        and source.release_version == pin['release_version'])
    model = store._fresh(db, p, Capability, source.data['execution']['model_profile_id'])
    need(model.kind == 'foundation_model' and model.content_hash == model.validated_hash == digest(model.data)
        == source.data['execution']['model_profile_hash'])
    return model


@store.checked
def price(db, p, task_id, body):
    body = c._parse(PriceRequest, body)
    require(p, 'admin')
    store.command_nonce.acquire(db,p,action=PRICE_ACTION,task_id=task_id,body=body,subject_id=task_id)
    task = _admin_task(db, p, task_id)
    prior = _prior(db, p, task, 'price', body)
    if prior: return prior['read_result']()
    model = _fixed_model(db, p, task)
    need(body['model_pin'] == store._pin(model), '价格依据只能登记给原任务固定的发布模型')
    need(not [event for event in prices._events(db, p, model) if event.data.get('request_id') == body['request_id']],
        '同编号底层价格收件已存在但任务配置回执缺失，请保留原编号核查，未重建')
    result = prices.authorize(db, p, body)
    event = _save(db, p, task, PRICE_SCHEMA, PRICE_ACTION, body, {'price_basis_ref': result['basis_ref']})
    value = _shape(p, task, event); _price_anchor(db, p, event, value)
    return result


@store.checked
def configure_quota(db, p, task_id, body):
    body = c._parse(ConfigureQuota, body)
    require(p, 'admin')
    store.command_nonce.acquire(db,p,action=QUOTA_ACTION,task_id=task_id,body=body,subject_id=task_id)
    task = _admin_task(db, p, task_id)
    prior = _prior(db, p, task, 'quota', body)
    if prior: return prior['read_result']()
    need(not store._rows(db, p, Audit, Audit.action == quota.AUTHORIZATION, Audit.object_id == task.id,
        Audit.data['request_id'].as_string() == body['request_id']),
        '同编号底层额度收件已存在但任务配置回执缺失，请保留原编号核查，未重建')
    configured = quota._configuration_base(db, p, task, body)
    model = configured['model']; pin = store._pin(model)
    current_price = prices.current(db, p, pin)
    need(current_price['price']['currency'] == body['currency'], '费用上限币种必须与原官方价格一致，不能换算')
    now = quota._clock(db)
    for previous in store._rows(db, p, Audit, Audit.action == store.QUOTA_POLICY, Audit.object_id == task.id):
        saved, budget = quota._policy_shape(previous, now)
        if saved['scope'] != configured['scope']: continue
        need(instant(saved['expires_at']) <= now,
            '原额度尚未到期，请保留原授权；未续期或退还原调用费用')
    max_tokens = model.data['execution']['max_tokens']
    need(type(max_tokens) is int and 128 <= max_tokens <= 8192)
    expires = now + timedelta(seconds=body['validity_seconds'])
    deadline = configured['goal']['spec'].get('deadline')
    if deadline is not None: expires = min(expires, instant(deadline))
    need(expires > now)
    resolved = c._parse(quota.Authorize, {'request_id': body['request_id'],
        'expected_goal_hash': body['expected_goal_hash'], 'expected_mature_head_hash': body['expected_mature_head_hash'],
        'limits': {'max_tokens': min(4096, max_tokens, 8192), 'max_request_bytes': 50000,
            'max_evidence_items': 2, 'max_cost_minor_units': body['max_cost_minor_units'], 'currency': body['currency']},
        'expires_at': expires.isoformat(), 'reason': body['reason']})
    result = quota.authorize(db, p, task.id, resolved)
    originals = store._rows(db, p, Audit, Audit.action == quota.AUTHORIZATION, Audit.object_id == task.id,
        Audit.data['request_id'].as_string() == body['request_id'])
    need(len(originals) == 1)
    event = _save(db, p, task, QUOTA_SCHEMA, QUOTA_ACTION, body, {'model_pin': pin,
        'authorization_body': resolved, 'authorization_ref': _ref(originals[0]),
        'policy_ref': result['quota_policy_ref'], 'price_basis_ref': current_price['basis_ref']})
    value = _shape(p, task, event); _quota_anchors(db, p, task, event, value)
    return result
