"""Admin-authored configuration ceilings, not a cloud send or price grant.

Only protected Audit rows are written. The normal sender still needs actual
current token, consent, Goal budget, price/money and single-call gates. No price
is invented here; max_cost_minor_units is an administrator's ceiling.
"""
import copy
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import Field, StrictInt, StrictStr, field_validator
from sqlalchemy import func, select

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as store
from .contracts import digest
from .goals import snapshot
from .models import Audit, Capability, uid
from .relations import lock_current_actor
from .security import Principal, require
from .services import published
from .temporal import instant

AUTHORIZATION = 'same_goal_advice.quota_authorized_v304'
SCHEMA = 'same-goal-advice-quota-authorization-v304'
MAX_VALIDITY = timedelta(hours=24)
POLICY_FIELDS = frozenset({'schema', 'scope', 'source_business_capability_ref',
    'advice_capability_ref', 'model_pin', 'issued_at', 'expires_at', 'limits', 'record_hash'})


class Limits(c.Closed):
    max_tokens: Annotated[StrictInt, Field(ge=128, le=8192)]
    max_request_bytes: Annotated[StrictInt, Field(ge=1, le=50000)]
    max_evidence_items: Annotated[StrictInt, Field(ge=1, le=2)]
    max_cost_minor_units: Annotated[StrictInt, Field(ge=1, le=10**9)]
    currency: Literal['CNY', 'USD']


class Authorize(c.Closed):
    request_id: c.ID
    expected_goal_hash: c.Hash
    expected_mature_head_hash: c.Hash
    limits: Limits
    expires_at: StrictStr
    reason: Annotated[StrictStr, Field(min_length=1, max_length=2000, pattern=r'\S')]

    @field_validator('expires_at')
    @classmethod
    def aware_expiry(cls, value):
        instant(value)
        return value


def _clock(db):
    value = db.scalar(select(func.clock_timestamp()))
    store.need(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value


def _published(db, p, identity, kind):
    # Lock and refresh the actual original row before ordinary publication
    # checking; a retained ORM object or supplied manifest is not authority.
    cap = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.id == identity).with_for_update(read=True).execution_options(populate_existing=True))
    store.need(cap is not None and cap.kind == kind)
    actual = published(db, p, identity, kind)
    store.need(actual is cap and cap.status == 'published'
        and cap.content_hash == cap.validated_hash == digest(cap.data))
    return cap


def _configuration_base(db, p, task, body):
    """Original Goal/release/source/advice/model, without the policy cycle."""
    from .workspace_api import saved_execution
    from .native_goals import guard, saved_context
    guard(db, p, task, lock_sources=False); saved_context(db, p, task)
    actual, release, business = saved_execution(db, p, task.id)
    selected = store.provider.binding(db, p, task)
    store.need(selected is not None and actual.id == task.id
        and release.id == selected['release'].id and business['execution_mode'] == 'confirmed_graph_workflow')
    nodes = {node['id']: node for node in selected['binding']['graph']['nodes']}
    reasoning, packet = nodes['reasoning'], nodes['packet']
    store.need(reasoning['kind'] == packet['kind'] == 'ability'
        and reasoning['execution']['agent_role'] == 'business' and packet['execution']['agent_role'] == 'data')
    store._member(db, p, reasoning['actor_id']); store._member(db, p, packet['actor_id'])
    source = _published(db, p, reasoning['execution']['capability_id'], 'tool')
    store.need(source.content_hash == reasoning['execution']['capability_hash']
        and source.release_version == reasoning['execution']['release_version'])
    manifest = c.build_manifest(source.data, store._pin(source))
    advice_rows = store._rows(db, p, Capability, Capability.kind == 'tool', Capability.status == 'published',
        Capability.data['execution']['adapter'].as_string() == c.ADAPTER,
        Capability.data['specification']['source_business_capability_ref']['id'].as_string() == source.id)
    store.need(len(advice_rows) == 1, '请先登记并发布唯一的同目标建议能力，额度配置不会代替能力发布')
    advice = _published(db, p, advice_rows[0].id, 'tool')
    store.need(advice.content_hash == digest(manifest) and advice.release_version == manifest['version'])
    c.validate_manifest(advice.data, source_manifest=source.data, source_ref=store._pin(source))
    model = _published(db, p, source.data['execution']['model_profile_id'], 'foundation_model')
    store.need(model.content_hash == source.data['execution']['model_profile_hash']
        and 'business' in model.data['allowed_agent_roles']
        and model.data['execution']['provider'] in {'qwen', 'deepseek'}
        and model.data['execution'].get('task') != 'embedding'
        and model.data['execution'].get('report_evidence_output') is None
        and model.data['data_policy'].get('cloud_allowed') is True)
    goal = snapshot(task)
    mature = store.existing._mature_head(db, p, task, selected)
    store.need(body['expected_goal_hash'] == goal['hash']
        and body['expected_mature_head_hash'] == mature['head']['head_hash'])
    spec = goal['spec']
    store.need(type(spec['max_tool_calls']) is int and spec['max_tool_calls'] >= 1
        and type(spec['max_steps']) is int and spec['max_steps'] >= 1)
    scope = {'workspace_id': p.workspace_id, 'task_id': task.id,
        'instance_id': task.data['instance_id'], 'release_id': release.id,
        'package_id': task.package_id, 'business_id': business['id'],
        'goal_version': goal['version'], 'goal_hash': goal['hash'], 'goal_snapshot_hash': digest(goal),
        'native_goal_context_hash': task.data['native_goal_context_hash'],
        'source_binding_hash': selected['binding']['binding_hash']}
    return {'scope': scope, 'source': source, 'advice': advice, 'model': model,
        'goal': goal, 'document_required': manifest['specification']['document_required']}


def _configuration(db,p,task,body):
    # New preparation/Start capacity. A running sender must authenticate its
    # existing reservation independently, never bypass this gate with a flag.
    from .goal_queries import call_count
    from .native_goals import total_steps
    context=_configuration_base(db,p,task,body)
    store.need(call_count(db,p,task)<context['goal']['spec']['max_tool_calls'],
        '原目标调用额度已用尽，不能增加本次建议额度')
    store.need(total_steps(db,p,task)<context['goal']['spec']['max_steps'],
        '原目标步骤额度已用尽，不能增加本次建议额度')
    return context


def _policy_shape(event, now):
    value = event.data
    store.need(type(value) is dict and set(value) == POLICY_FIELDS and value['schema'] == store.POLICY_SCHEMA
        and value['record_hash'] == c.record_hash(value) and type(value['limits']) is dict)
    issued, expires = instant(value['issued_at']), instant(value['expires_at'])
    store.need(event.created_at is not None and event.created_at.tzinfo is not None
        and issued <= event.created_at <= now and issued < expires)
    parsed = c.Budget.model_validate({**value['limits'],
        'quota_policy_ref': {'id': event.id, 'record_hash': digest(value)}}).model_dump(mode='json')
    store.need(set(value['limits']) == set(c.Budget.model_fields) - {'quota_policy_ref'})
    return value, parsed


def _authorization(task, p, body, policy):
    return {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'actor_id': p.user_id, 'request_id': body['request_id'], 'body': copy.deepcopy(body),
        'body_hash': digest(body), 'policy_ref': {'id': policy.id, 'record_hash': digest(policy.data)},
        'issued_at': policy.data['issued_at'], 'pricing_verified': False, 'cloud_send_authorized': False}


def _receipt(db, p, task, body, event, now):
    value = event.data
    store.need(event.workspace_id == p.workspace_id and event.object_id == task.id and event.actor_id == p.user_id
        and value['request_id'] == body['request_id'] and value['body_hash'] == digest(body)
        and digest(value['body']) == digest(body))
    rows = store._rows(db, p, Audit, Audit.action == store.QUOTA_POLICY, Audit.object_id == task.id,
        Audit.id == value['policy_ref']['id'])
    store.need(len(rows) == 1)
    policy = rows[0]; stored, _budget = _policy_shape(policy, now)
    store.need(policy.actor_id == event.actor_id and policy.created_at == event.created_at
        and digest(value) == digest(_authorization(task, p, body, policy))
        and stored['scope']['workspace_id'] == p.workspace_id and stored['scope']['task_id'] == task.id
        and stored['scope']['goal_hash'] == body['expected_goal_hash']
        and stored['expires_at'] == instant(body['expires_at']).isoformat())
    return {'schema': SCHEMA, 'task_id': task.id, 'request_id': body['request_id'],
        'quota_policy_ref': copy.deepcopy(value['policy_ref']), 'limits': copy.deepcopy(stored['limits']),
        'issued_at': stored['issued_at'], 'expires_at': stored['expires_at'],
        'configuration_only': True, 'pricing_verified': False, 'cloud_send_authorized': False,
        'message': '已保存一次调用的额度上限；发送前仍需核对当前授权、实际令牌与费用，未调用模型'}


@store.checked
def authorize(db, p, task_id, body):
    require(p, 'admin')
    store.need(type(p) is Principal)
    body = Authorize.model_validate(body).model_dump(mode='json')
    store._reader(db, p, write=True)
    task = store.existing._task(db, p, task_id, lock=True)
    lock_current_actor(db, p); store._member(db, p, p.user_id, admin=True)
    now = _clock(db)
    originals = store._rows(db, p, Audit, Audit.action == AUTHORIZATION, Audit.object_id == task.id,
        Audit.data['request_id'].as_string() == body['request_id'])
    store.need(len(originals) <= 1)
    if originals:
        # Original receipt, not a renewed policy: expiry, cut, head and money
        # ceiling are never extended by retrying the nonce.
        return _receipt(db, p, task, body, originals[0], now)
    # Cross-version checks share the task lock. Existing receipts remain
    # replayable; a new v1 request cannot conceal or supersede a v2 policy.
    from . import same_goal_advice_unlimited_policy_v2 as unlimited
    store.need(not store._rows(db, p, Audit, Audit.action == unlimited.AUTHORIZATION,
        Audit.object_id == task.id, Audit.data['request_id'].as_string() == body['request_id']),
        '原请求编号已用于另一版政策')
    context = _configuration(db, p, task, body)
    unlimited.assert_no_active_v2(db, p, task, context['scope'], now)
    expires = instant(body['expires_at'])
    store.need(now < expires <= now + MAX_VALIDITY, '额度有效期必须晚于服务器当前时间且不超过24小时')
    deadline = context['goal']['spec'].get('deadline')
    store.need(deadline is None or expires <= instant(deadline), '额度有效期不能超过原目标截止时间')
    model = context['model']; requested = body['limits']
    max_tokens = model.data['execution'].get('max_tokens', 1024)
    store.need(type(max_tokens) is int and requested['max_tokens'] <= max_tokens,
        '输出令牌上限不能超过已发布模型的原配置')
    store.need(not context['document_required'] or requested['max_evidence_items'] == 2,
        '本建议需要数据与文档两份证据，证据数量上限必须为2')
    limits = {**requested, 'provider': model.data['execution']['provider'],
        'max_actual_calls': 1, 'reserved_calls': 1, 'reserved_steps': 1,
        'goal_max_tool_calls': context['goal']['spec']['max_tool_calls'],
        'goal_max_steps': context['goal']['spec']['max_steps']}
    policy_id = uid()
    limits = c.Budget.model_validate({**limits,
        'quota_policy_ref': {'id': policy_id, 'record_hash': '0' * 64}}).model_dump(mode='json')
    limits.pop('quota_policy_ref')
    for previous in store._rows(db, p, Audit, Audit.action == store.QUOTA_POLICY, Audit.object_id == task.id):
        value, _ = _policy_shape(previous, now)
        store.need(value['scope'] != context['scope'] or instant(value['expires_at']) <= now,
            '原目标已有尚未到期的额度政策，请保留原授权；未叠加第二份额度')
    value = {'schema': store.POLICY_SCHEMA, 'scope': copy.deepcopy(context['scope']),
        'source_business_capability_ref': store._pin(context['source']),
        'advice_capability_ref': store._pin(context['advice']), 'model_pin': store._pin(model),
        'issued_at': now.isoformat(), 'expires_at': expires.isoformat(), 'limits': limits}
    value['record_hash'] = c.record_hash(value)
    policy = Audit(id=policy_id, workspace_id=p.workspace_id, actor_id=p.user_id,
        action=store.QUOTA_POLICY, object_id=task.id, created_at=now, data=value)
    authorization = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        action=AUTHORIZATION, object_id=task.id, created_at=now, data=_authorization(task, p, body, policy))
    with db.begin_nested():
        db.add(policy); db.add(authorization); db.flush()
        return _receipt(db, p, task, body, authorization, now)

