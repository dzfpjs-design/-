"""Reduce late original native coordination without granting new execution.

The actual Task -> Gap -> Plan rows are locked before either legacy receiver
writes a business receipt.  Historical verification is separate from the
explicit current-state reasons which suppress coordination.  No HTTP error is
treated as a valid historical receipt.
"""
import copy
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .models import Acquisition, Audit, Capability, DataGap, DataRun, DomainInstance, Event, InstanceRelease, ObservationPlan, Task
from .security import Principal
from .services import audit

SCHEMA = 'original-native-coordination-receipt-v127'
ACTION = 'native_goal.coordination_retained'
RECEIPT_KEYS = {'schema', 'event_id', 'event_kind', 'origin', 'code', 'reason', 'derivation_suppressed', 'receipt_hash'}
CODES = {'goal_ended', 'query_ended', 'gap_ended', 'plan_ended', 'package_withdrawn', 'instance_not_active', 'release_changed', 'business_disabled'}


def _bad():
    raise HTTPException(409, '原业务协调的任务、查询、缺口或计划依据无法核对；原事件保留，未转为新任务')


def _row(db, model, identifier, p, lock=False):
    query = select(model).where(model.workspace_id == p.workspace_id, model.id == identifier)
    if lock: query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    if row is None: _bad()
    return row


def _native(task):
    return any(key in task.data for key in ('native_goal_context', 'native_goal_context_hash', 'native_goal_query_id'))


def _parents(db, p, run, gap, plans):
    delegation = run.data.get('goal_delegation')
    if delegation is not None and not isinstance(delegation, dict): _bad()
    rows = list(db.scalars(select(Task).where(Task.workspace_id == p.workspace_id,
        Task.data['native_goal_query_id'].as_string() == run.id).execution_options(populate_existing=True)))
    identifiers = {row.id for row in rows}
    declared = any(key in run.data for key in ('native_goal_task_id', 'native_goal_context_hash', 'native_goal_request_hash'))
    if run.data.get('native_goal_task_id'): identifiers.add(run.data['native_goal_task_id'])
    hints = [gap.data.get('business_task_id'), (delegation or {}).get('task_id')]
    for plan in plans:
        origin = plan.data.get('observation_request')
        if origin is not None and not isinstance(origin, dict): _bad()
        if (origin or {}).get('goal_delegation') is not None and not isinstance(origin['goal_delegation'], dict): _bad()
        hints.extend([plan.data.get('business_task_id'), ((origin or {}).get('goal_delegation') or {}).get('task_id')])
    for identifier in hints:
        if identifier:
            task = _row(db, Task, identifier, p)
            if _native(task): identifiers.add(task.id)
    if (declared and not identifiers) or len(identifiers) > 1: _bad()
    return next(iter(identifiers)) if identifiers else None


def _plans(db, p, gap, explicit=None):
    rows = list(db.scalars(select(ObservationPlan).where(ObservationPlan.workspace_id == p.workspace_id,
        ObservationPlan.data['observation_request']['gap_id'].as_string() == gap.id).execution_options(populate_existing=True)))
    identifiers = {row.id for row in rows}
    if gap.data.get('observation_plan_id'): identifiers.add(gap.data['observation_plan_id'])
    for identifier in gap.data.get('acquisition_ids', []):
        acquisition = _row(db, Acquisition, identifier, p)
        if acquisition.data.get('observation_plan_id'): identifiers.add(acquisition.data['observation_plan_id'])
    if explicit: identifiers.add(explicit.id)
    return [_row(db, ObservationPlan, identifier, p) for identifier in sorted(identifiers)]


def _acquisitions(db, p, gap, plans):
    """Recheck both directions after the parent/gap/plan locks are held."""
    by_plan = {row.id: row for row in plans}
    selected = {}
    for plan in plans:
        for acquisition in db.scalars(select(Acquisition).where(Acquisition.workspace_id == p.workspace_id,
            Acquisition.data['observation_plan_id'].as_string() == plan.id).execution_options(populate_existing=True)):
            selected[acquisition.id] = acquisition
    for identifier in gap.data.get('acquisition_ids', []):
        selected[identifier] = _row(db, Acquisition, identifier, p)
    for acquisition in selected.values():
        if acquisition.entity_id != gap.entity_id: _bad()
        identifier = acquisition.data.get('observation_plan_id')
        if identifier is None:
            if 'observation_plan_id' in acquisition.data: _bad()
            continue  # The original non-selection monitor path has no Plan.
        plan = by_plan.get(identifier)
        if (not plan or acquisition.data.get('policy_id') != plan.policy_id or acquisition.entity_id != plan.entity_id
            or acquisition.data.get('data_gap_id') != gap.id): _bad()


def _source_event(db, p, event, gap, plan):
    if event.kind == 'perception.acquisition_escalation':
        names = ('plan_id', 'entity_id', 'policy_id', 'actor_id', 'code', 'reason')
        base = {name: event.data.get(name) for name in names}
        if not plan or any(not isinstance(value, str) or not value for value in base.values()): _bad()
        if (base['plan_id'], base['entity_id'], base['policy_id']) != (plan.id, plan.entity_id, plan.policy_id): _bad()
        prefix = 'sensing-escalation:' + plan.id + ':' + base['code'] + ':'
        suffix = event.idempotency_key.removeprefix(prefix)
        if not event.idempotency_key.startswith(prefix) or not suffix.isdigit() or str(int(suffix)) != suffix or not 1 <= int(suffix) <= plan.version: _bad()
        if not any(item.get('kind') == 'escalated' and item.get('code') == base['code'] and item.get('reason') == base['reason']
            for item in plan.data.get('history', [])): _bad()
        issued = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == plan.id, Audit.action == 'perception.plan_escalated', Audit.actor_id == base['actor_id'])))
        if not any(row.data.get('code') == base['code'] for row in issued): _bad()
    elif event.kind == 'data.gap_coordination':
        if not gap: _bad()
        base = {name: event.data.get(name) for name in ('gap_id', 'entity_id', 'query_id', 'actor_id')}
        if base != {'gap_id': gap.id, 'entity_id': gap.entity_id, 'query_id': gap.query_id, 'actor_id': gap.data['actor_id']}: _bad()
        if event.idempotency_key != 'gap-coordination:' + gap.id: _bad()
    else: _bad()
    return digest({'id': event.id, 'workspace_id': event.workspace_id, 'kind': event.kind,
        'idempotency_key': event.idempotency_key, 'data': base})


@dataclass
class Coordination:
    gap: object = None
    plan: object = None
    task: object = None
    receipt: object = None


def _active_goal_guard(db, event, run):
    """Reacquire current execution IAM only after historical reduction checks.

    The receiver's deliberately reduced viewer identity proves saved origin;
    it cannot authorize new coordination. The event names the original actor,
    whose actual account/membership must still permit this operation now.
    """
    from .worker import actor, ActorRevoked
    from .relations import lock_current_actor
    from .goal_queries import guard
    try:
        current = actor(db, event.workspace_id, event.data['actor_id'])
    except ActorRevoked:
        raise HTTPException(403, '原协调账号或工作区执行权限已撤销；原事件保留，未继续补采') from None
    lock_current_actor(db, current)
    guard(db, current, run)




def _coordination_fields(run,gap,task):
    """New typed conflicts are review targets, never fabricated missing values.

    This only admits the existing coordination recipient. Device proposals
    still use their original ObservationRequest guard and cannot use an empty
    missing_fields list as permission to observe again.
    """
    missing=gap.data.get('missing_fields')
    if gap.data.get('kind') not in {'conflict','missing_and_conflict'}:
        return missing
    fields=gap.data.get('conflict_fields');refs=gap.data.get('conflict_refs')
    state=run.data.get('result')
    if (not isinstance(state,dict) or digest(state)!=run.data.get('native_goal_result_hash')
        or state.get('status')!='conflict' or not isinstance(state.get('result'),dict)
        or not isinstance(fields,list) or not fields or len(fields)!=len(set(fields))
        or not isinstance(refs,list) or not refs
        or not set(fields).issubset(task.data['goal_spec']['required_fields'])
        or not set(fields).issubset(run.data['request']['fields'])): _bad()
    frame=state['result'];actual=[]
    evidence={(item.get('entity_id'),item.get('observation_id')) for item in frame.get('observation_evidence',[])}
    for item in frame.get('conflicts',[]):
        if item.get('entity_id')!=gap.entity_id or item.get('field') not in fields:continue
        ids=item.get('observation_ids')
        if (not isinstance(ids,list) or len(ids)<2 or len(ids)!=len(set(ids))
            or any((gap.entity_id,identifier) not in evidence for identifier in ids)): _bad()
        actual.append({key:copy.deepcopy(item[key]) for key in ('field','observation_ids','observed_at','reason') if key in item})
    if (set(fields)!={item.get('field') for item in refs}
        or sorted(digest(item) for item in actual)!=sorted(digest(item) for item in refs)): _bad()
    if gap.data['kind']=='conflict':
        if missing!=[]: _bad()
    elif not isinstance(missing,list) or not missing: _bad()
    return sorted(set(missing or [])|set(fields))


def prepare(db, event):
    """Return locked actual rows and an optional fresh, verified reducing receipt.

    Active paths have receipt=None.  Only a native original proved against its
    real saved release may produce a receipt; independent monitoring retains
    the existing coordination behavior.  The Event is already locked by the
    outbox receiver; no Task/Gap/Plan is changed on the reducing path.
    """
    if not isinstance(event.data, dict) or not isinstance(event.data.get('actor_id'), str): _bad()
    p = Principal(event.data['actor_id'], event.workspace_id, 'viewer', 'original-coordination-receiver')
    actual = _row(db, Event, event.id, p)
    if actual.kind != event.kind or actual.idempotency_key != event.idempotency_key: _bad()
    event = actual
    plan = None; gap = None
    if event.kind == 'perception.acquisition_escalation':
        plan = _row(db, ObservationPlan, event.data.get('plan_id'), p)
        origin = plan.data.get('observation_request')
        if origin is not None and not isinstance(origin, dict): _bad()
        reverse = list(db.scalars(select(DataGap).where(DataGap.workspace_id == p.workspace_id,
            DataGap.data['observation_plan_id'].as_string() == plan.id).execution_options(populate_existing=True)))
        gap_ids = {row.id for row in reverse}
        acquisitions = list(db.scalars(select(Acquisition).where(Acquisition.workspace_id == p.workspace_id,
            Acquisition.data['observation_plan_id'].as_string() == plan.id).execution_options(populate_existing=True)))
        gap_ids.update(row.data['data_gap_id'] for row in acquisitions if row.data.get('data_gap_id'))
        for acquisition in acquisitions:
            gap_ids.update(db.scalars(select(DataGap.id).where(DataGap.workspace_id == p.workspace_id,
                DataGap.data['acquisition_ids'].contains([acquisition.id]))))
        if origin:
            if not origin.get('gap_id'): _bad()
            gap_ids.add(origin['gap_id'])
        if len(gap_ids) > 1: _bad()
        if gap_ids: gap = _row(db, DataGap, next(iter(gap_ids)), p)
    elif event.kind == 'data.gap_coordination':
        gap = _row(db, DataGap, event.data.get('gap_id'), p)
    else: _bad()
    if gap is None:
        plan = _row(db, ObservationPlan, plan.id, p, True)
        if plan.data.get('observation_request') is not None: _bad()
        # No reverse native/query linkage is silently converted to monitoring.
        if db.scalar(select(DataGap.id).where(DataGap.workspace_id == p.workspace_id,
            DataGap.data['observation_plan_id'].as_string() == plan.id)): _bad()
        if event.data.get('derivation_receipt') or db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == event.id, Audit.action == ACTION)): _bad()
        return Coordination(plan=plan)
    run = _row(db, DataRun, gap.query_id, p)
    plans = _plans(db, p, gap, plan)
    parent_id = _parents(db, p, run, gap, plans)
    task = _row(db, Task, parent_id, p, True) if parent_id else None
    # This is the shared native lock order: Task -> Gap -> Plan (sorted IDs).
    original_query = run.id
    gap = _row(db, DataGap, gap.id, p, True)
    if gap.query_id != original_query: _bad()
    run = _row(db, DataRun, original_query, p)
    locked = [_row(db, ObservationPlan, row.id, p, True) for row in plans]
    if {row.id for row in _plans(db, p, gap, plan)} != {row.id for row in locked}: _bad()
    if _parents(db, p, run, gap, locked) != parent_id: _bad()
    if plan: plan = next(row for row in locked if row.id == plan.id)
    old = event.data.get('derivation_receipt')
    issued = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == event.id, Audit.action == ACTION)))
    if task is None:
        if old is not None or issued: _bad()
        return Coordination(gap=gap, plan=plan)
    event_hash = _source_event(db, p, event, gap, plan)
    from .goals import snapshot
    from .native_goal_acquisitions import original, ended
    from .native_goals import saved_context
    context = saved_context(db, p, task)
    expected = {'task_id': task.id, 'goal_snapshot': snapshot(task)}
    target_fields = _coordination_fields(run,gap,task)
    if (task.data.get('native_goal_query_id') != run.id or run.data.get('native_goal_task_id') != task.id
        or run.data.get('native_goal_context_hash') != digest(context) or run.data.get('goal_delegation') != expected
        or digest(run.data.get('request')) != run.data.get('native_goal_request_hash')
        or gap.entity_id not in context['entity_ids'] or gap.entity_id not in gap.data['request']['entity_ids']
        or not target_fields or not set(target_fields).issubset(task.data['goal_spec']['required_fields'])
        or gap.data.get('business_task_id') not in (None, task.id)): _bad()
    need = run.data.get('need')
    if not isinstance(need, dict) or not isinstance(need.get('fields'), list) or not need['fields']: _bad()
    expected_request = {**run.data['request'], 'mode': 'structured', 'model_profile_id': None,
        'fields': need['fields'], 'relation_query': need.get('relation_query') or run.data['request'].get('relation_query')}
    if gap.data['request'] != expected_request or gap.id not in run.data.get('data_gap_ids', []): _bad()
    for row in locked:
        original(db, p, task, row)
        if row.data.get('business_task_id') not in (None, task.id): _bad()
    _acquisitions(db, p, gap, locked)
    release = _row(db, InstanceRelease, context['release_id'], p)
    package = _row(db, Capability, release.package_id, p)
    instance = _row(db, DomainInstance, context['instance_id'], p)
    origin = {'task_id': task.id, 'query_id': run.id, 'gap_id': gap.id, 'plan_ids': [plan.id] if plan else [],
        'instance_id': instance.id, 'release_id': release.id, 'package_id': package.id,
        'configuration_hash': release.configuration_hash, 'context_hash': digest(context),
        'goal_snapshot_hash': digest(snapshot(task)), 'request_hash': digest(run.data['request']),
        'gap_request_hash': digest(gap.data['request']), 'event_hash': event_hash}
    if old is not None or issued:
        if (not isinstance(old, dict) or set(old) != RECEIPT_KEYS or old.get('schema') != SCHEMA
            or old.get('event_id') != event.id or old.get('event_kind') != event.kind or old.get('origin') != origin
            or old.get('derivation_suppressed') is not True or old.get('code') not in CODES
            or not isinstance(old.get('reason'), str) or not old['reason']
            or digest({key: value for key, value in old.items() if key != 'receipt_hash'}) != old.get('receipt_hash')
            or len(issued) != 1 or issued[0].actor_id != p.user_id
            or issued[0].data != {'receipt_hash': old['receipt_hash'], 'origin': origin}): _bad()
        return Coordination(gap=gap, plan=plan, task=task, receipt=copy.deepcopy(old))
    code = reason = None
    if ended(task): code, reason = 'goal_ended', '原业务目标已停止、到期或验收；只保留原协调事件，不再改写任务'
    elif run.status == 'cancelled': code, reason = 'query_ended', '原资料查询已停止；只保留原协调事件'
    elif gap.status in {'resolved', 'cancelled'}: code, reason = 'gap_ended', '原资料缺口已满足或停止；只保留原协调事件'
    elif plan and plan.status in {'completed', 'cancelled'}: code, reason = 'plan_ended', '原观测计划已结束；只保留原协调事件'
    elif package.status != 'published': code, reason = 'package_withdrawn', '原业务配置已撤回；只保留原协调事件，不再发起业务协调'
    elif instance.status != 'partially_active' or not instance.active_release_id: code, reason = 'instance_not_active', '原实例已暂停；只保留原协调事件'
    elif instance.active_release_id != release.id: code, reason = 'release_changed', '原实例已换用其他版本；只保留原协调事件'
    elif context['business_id'] not in instance.data.get('enabled_business_ids', []): code, reason = 'business_disabled', '原业务已停用；只保留原协调事件'
    if code is None:
        # A valid historical origin grants no current object/source authority.
        # This is the non-dispatch parent guard: existing_only may coordinate.
        _active_goal_guard(db, event, run)
        return Coordination(gap=gap, plan=plan, task=task)
    receipt = {'schema': SCHEMA, 'event_id': event.id, 'event_kind': event.kind, 'origin': origin,
        'code': code, 'reason': reason, 'derivation_suppressed': True}
    receipt['receipt_hash'] = digest(receipt)
    event.data = {**event.data, 'derivation_receipt': receipt}
    audit(db, p, ACTION, event.id, {'receipt_hash': receipt['receipt_hash'], 'origin': origin})
    return Coordination(gap=gap, plan=plan, task=task, receipt=copy.deepcopy(receipt))
