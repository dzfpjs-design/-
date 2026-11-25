"""DB-authenticated policy reading, not a Plan, preview or send authority.

The caller owns the transaction. No route/worker uses this helper yet. The
future preview must separately authenticate the saved v2 Plan, original data,
document receipt, current per-model consent and derived projection.
"""
import copy
from datetime import datetime

from sqlalchemy import func, select

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_price_v307 as prices
from . import same_goal_advice_quota_v304 as old
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_unlimited_policy_v2 as policy
from .contracts import digest
from .models import Audit
from .relations import lock_current_actor
from .security import Principal
from .temporal import instant


@store.checked
def current_policy(db, p, task_id, policy_ref, *, reserved_plan_test_only=None, intent_test_only=False):
    """Lock and authenticate one real policy against the real current Goal.

    The returned value never authorizes preview creation, a reservation or an
    external send. It must be checked again by the eventual actual sender.
    """
    store.need(type(p) is Principal)
    reference = c._parse(c.Ref, policy_ref)
    store._reader(db, p, write=True)
    task = store.existing._task(db, p, task_id, lock=True)
    lock_current_actor(db, p)
    now = db.scalar(select(func.clock_timestamp()))
    store.need(type(now) is datetime and now.tzinfo is not None and now.utcoffset() is not None)
    event = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == policy.POLICY, Audit.id == reference['id'])
        .with_for_update(read=True).execution_options(populate_existing=True))
    store.need(event is not None and reference['record_hash'] == digest(event.data),
        '原 DeepSeek 政策不存在或摘要无法核对；未创建预览')
    saved, limits = policy.policy_shape(event, now)
    store.need(instant(saved['expires_at']) > now, '原 DeepSeek 政策已到期；未创建预览')
    requests = store._rows(db, p, Audit, Audit.action == policy.AUTHORIZATION,
        Audit.object_id == task.id, Audit.data['policy_ref']['id'].as_string() == event.id)
    store.need(len(requests) == 1 and requests[0].actor_id == event.actor_id,
        '原 DeepSeek 政策发放记录无法核对；未创建预览')
    issued = requests[0]
    store._member(db, p, issued.actor_id, admin=True)
    request = policy.Authorize.model_validate(issued.data['body']).model_dump(mode='json')
    issuer = Principal(issued.actor_id, p.workspace_id, 'admin', 'policy-audit-reader')
    receipt = policy._receipt(db, issuer, task, request, issued, now)
    store.need(receipt['policy_ref'] == reference
        and all(saved['limits'][name] == request['limits'][name] for name in request['limits']))
    # This fixed server resolver rechecks the Goal, maturity, graph binding,
    # published capability/model hashes, current source actors and capacity.
    # A saved pristine v2 preparation is unknown to the v1-only counters in
    # old._configuration. Use the still-unrouted shared reader here; it holds
    # Task/custody locks and fails closed for unknown v2 states. The optional
    # isolated test-only path rechecks one original reservation as the same
    # prospective slot, not authority for another Plan or an external send.
    from . import goal_budget_versioned_snapshot_candidate_v3 as capacity
    context = old._configuration_base(db, p, task, request)
    used = capacity.read_locked(db, p, task, reserved_plan_test_only=reserved_plan_test_only,
        intent_test_only=intent_test_only)
    store.need(used['calls'] < context['goal']['spec']['max_tool_calls'],
        '原目标调用额度已用尽，不能增加本次建议额度')
    store.need(used['steps'] < context['goal']['spec']['max_steps'],
        '原目标步骤额度已用尽，不能增加本次建议额度')
    store.need(context['scope'] == saved['scope']
        and store._pin(context['source']) == saved['source_business_capability_ref']
        and store._pin(context['advice']) == saved['advice_capability_ref']
        and store._pin(context['model']) == saved['model_pin']
        and context['model'].data['execution']['provider'] == 'deepseek'
        and saved['mature_head_hash'] == request['expected_mature_head_hash']
        and limits['max_tokens'] <= context['model'].data['execution']['max_tokens']
        and limits['goal_max_tool_calls'] == context['goal']['spec']['max_tool_calls']
        and limits['goal_max_steps'] == context['goal']['spec']['max_steps'])
    binding = store.provider.binding(db, p, task)
    store.need(binding is not None)
    nodes = {node['id']: node for node in binding['binding']['graph']['nodes']}
    store.need(store._prepare_actor_allowed(p, nodes['reasoning']['actor_id'], nodes['packet']['actor_id']),
        '当前账号不属于本任务业务或资料岗位；未创建预览')
    return {'policy_ref': copy.deepcopy(reference), 'limits': copy.deepcopy(limits),
        'model_pin': copy.deepcopy(saved['model_pin']), 'scope': copy.deepcopy(saved['scope']),
        'mature_head_hash': saved['mature_head_hash'],
        'preview_authorized': False, 'cloud_send_authorized': False}


@store.checked
def current_price(db, p, model_pin, basis_ref):
    """Optional price: None means unknown; supplied basis must authenticate.

    Invalid, stale, foreign or tampered price evidence fails explicitly; it is
    never silently presented as a verified estimate or as zero cost.
    """
    store.need(type(p) is Principal)
    store._reader(db, p, write=True)
    lock_current_actor(db, p)
    if basis_ref is None:
        return None
    reference = c._parse(c.Ref, basis_ref)
    pinned = c._parse(c.CapabilityPin, model_pin)
    value = prices.current(db, p, pinned, basis_ref=reference)
    store.need(value['basis_ref'] == reference)
    return copy.deepcopy(value)
