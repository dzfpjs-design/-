"""Retire one provably unclaimed obsolete reasoning job; never dispatch a model.

The immutable graph activation and its original input remain unchanged.  This
is the first durable step toward a separate continuation, not Goal acceptance.
"""
import copy
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, StrictBool, field_validator
from sqlalchemy import or_, select, text
from sqlalchemy.exc import OperationalError

from .contracts import Contract, digest
from .models import Audit, Capability, CloudCall, Invocation, Job, Task, uid
from .relations import lock_current_actor
from .services import require, scoped
from . import workflow_graph as workflow, workflow_graph_scope as scope

SCHEMA = 'graph-reasoning-retirement-v337'
KEY = 'graph_reasoning_retirements_v337'
ACTION = 'workflow.graph.reasoning_retired_v337'
ERROR = '原推理步骤无法证明尚未领取或已发生调用；原记录保留，请核对原流程'


class Retire(Contract):
    request_id: UUID
    activation_id: str = Field(min_length=1, max_length=200)
    version: int = Field(strict=True, ge=0)
    cancel_epoch: int = Field(strict=True, ge=0)
    expected_head_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm_retire: StrictBool
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def valid_reason(cls, value):
        if not value.strip() or '\x00' in value:
            raise ValueError('请填写有效的停用说明')
        value.encode('utf-8', errors='strict')
        return value


def need(value, message=ERROR):
    if not value:
        raise HTTPException(409, message)


def is_retired(task, activation_id):
    """Fail closed for a retained retirement marker, including malformed data."""
    records = task.data.get(KEY, {})
    need(type(records) is dict and len(records) <= 64, '原步骤停用记录无法核对，未继续执行')
    for record in records.values():
        need(type(record) is dict and record.get('schema') == SCHEMA
             and record.get('status') == 'retired' and type(record.get('activation_id')) is str,
             '原步骤停用记录无法核对，未继续执行')
        if record['activation_id'] == activation_id:
            return True
    return False


def historical_record(db, p, task, request_id, *, original_actor_only=True):
    records = task.data.get(KEY, {})
    need(type(records) is dict and len(records) <= 64)
    record = records.get(str(request_id))
    if record is None:
        return None
    need(type(record) is dict and set(record) == {'schema','request_id','actor_id','body_hash','activation_id',
         'job_id','graph_hash','input_hash','head_hash','audit_id','status','reason_hash'}
         and record.get('schema') == SCHEMA and record.get('status') == 'retired'
         and record.get('request_id') == str(request_id) and type(record.get('actor_id')) is str
         and (not original_actor_only or record.get('actor_id') == p.user_id))
    event = scoped(db, Audit, record['audit_id'], p)
    reason = event.data.get('reason') if isinstance(event.data, dict) else None
    need(type(reason) is str and digest(reason) == record.get('reason_hash')
         and event.action == ACTION and event.object_id == task.id and event.actor_id == record['actor_id']
         and event.data == {**record, 'reason': reason})
    job = scoped(db, Job, record['job_id'], p)
    transport = task.data.get(workflow.TRANSPORT, {}).get(record['activation_id'], {})
    state = task.data.get('graph_runtime', {})
    activation = state.get('activations', {}).get(record['activation_id'], {})
    need(job.status == 'cancelled' and job.attempts == 0 and job.lease_token is None and job.lease_until is None
         and job.data.get('task_id') == task.id and job.data.get('activation_id') == record['activation_id']
         and state.get('graph_hash') == job.data.get('graph_hash') == record['graph_hash']
         and activation.get('node_id') == 'reasoning'
         and digest(activation.get('inputs_snapshot', {}).get('arguments')) == record['input_hash']
         and job.data.get('input_hash') == record['input_hash']
         and transport.get('job_id') == job.id and transport.get('phase') == 'retired_for_new_evaluation')
    return copy.deepcopy(record)


def public_request(record):
    return {key: record[key] for key in ('actor_id', 'body_hash', 'activation_id', 'job_id')}


def _locked_job(db, p, identity):
    try:
        row = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == identity)
                        .with_for_update(nowait=True).execution_options(populate_existing=True))
    except OperationalError as error:
        if getattr(error.orig, 'sqlstate', None) == '55P03':
            raise HTTPException(409, '原步骤正在由后台核对，请稍后读取原任务；没有创建续段') from error
        raise
    need(row is not None)
    return row


def _no_prior_dispatch(db, p, task, job, activation, transport, model_id, *, retired=False):
    """Fail closed on any in-flight/orphan evidence that can identify this job."""
    need(job.status == ('cancelled' if retired else 'queued') and type(job.attempts) is int and job.attempts == 0
         and job.lease_token is None and job.lease_until is None
         and job.data.get('attempt') == 1 and not job.data.get('recovery_only')
         and not job.data.get('lease_recoveries'))
    need(set(transport) == {'job_id', 'invocation_key', 'input_hash', 'phase', 'cancel_epoch', 'attempt'}
         and transport['phase'] == ('retired_for_new_evaluation' if retired else 'queued') and transport['attempt'] == 1)
    siblings = list(db.scalars(select(Job.id).where(Job.workspace_id == p.workspace_id,
        Job.data['task_id'].as_string() == task.id,
        Job.data['activation_id'].as_string() == activation['id']).limit(2)))
    need(siblings == [job.id])
    calls = db.scalar(select(Invocation.id).where(Invocation.workspace_id == p.workspace_id, or_(
        Invocation.idempotency_key == transport['invocation_key'],
        Invocation.data['job_id'].as_string() == job.id,
        (Invocation.data['workflow_graph_scope']['task_id'].as_string() == task.id)
        & (Invocation.data['workflow_graph_scope']['activation_id'].as_string() == activation['id']))).limit(1))
    need(calls is None)
    dispatch = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_(['workflow.graph.dispatch', 'business_goal_ready.prepared_v288',
                          'business_goal_ready.reserved_v288']), or_(
        Audit.object_id == job.id, Audit.data['job_id'].as_string() == job.id,
        (Audit.object_id == task.id) & (Audit.data['activation_id'].as_string() == activation['id']))).limit(1))
    need(dispatch is None)
    # An unlinked call for the same model is ambiguous regardless of its wall
    # clock. Calls demonstrably belonging to other jobs do not block it.
    model_calls = db.scalars(select(CloudCall).where(CloudCall.workspace_id == p.workspace_id,
        CloudCall.capability_id == model_id)).yield_per(100)
    for row in model_calls:
        data = row.data if isinstance(row.data, dict) else {}
        gateway = data.get('gateway_dispatch')
        identity = gateway.get('identity') if isinstance(gateway, dict) else None
        linked = identity.get('job_id') if isinstance(identity, dict) else data.get('job_id')
        need(type(linked) is str and bool(linked) and linked != job.id,
             '同一模型存在无法归属的调用记录；请先核对原调用，未创建续段')


def retire(db, p, task_id, request):
    from . import workflow_graph_api as api
    from . import goal_evaluation_provider_v288 as provider
    from .graph_reasoning_evaluation_status_v335 import project

    require(p, 'admin', 'operator')
    body = Retire.model_validate(request).model_dump(mode='json')
    need(body['confirm_retire'] is True, '请明确确认停止原步骤；任务及原记录不会被删除')
    initial, _, _, _ = api.context(db, p, task_id)
    candidate = initial.data.get(workflow.TRANSPORT, {}).get(body['activation_id'])
    need(isinstance(candidate, dict) and isinstance(candidate.get('job_id'), str))
    # Match the worker/retry order: original Job, optional parent, then Task.
    # Existing graph cancellation can hold Task while waiting for a Job. Bound
    # both acquisitions rather than relying on Job NOWAIT alone to avoid a
    # Job→Task / Task→Job deadlock or an unbounded human-facing wait.
    db.execute(text("SET LOCAL lock_timeout = '1000ms'"))
    try:
        job = _locked_job(db, p, candidate['job_id'])
        db.expire(initial)
        task, release, business, graph = api.context(db, p, task_id, True)
        # Refresh only after the Task lock, then repeat all context checks.
        db.refresh(task)
        from .signal_dual_handoffs import parent_for_task
        need('dual_handoff' not in task.data and parent_for_task(db, p, task.id) is None,
             '此任务有跨任务交接；请先核对原交接，未停止任何步骤')
        task, release, business, graph = api.context(db, p, task_id)
        lock_current_actor(db, p)
    except OperationalError as error:
        if getattr(error.orig, 'sqlstate', None) == '55P03':
            raise HTTPException(409, '原任务正在被其他操作处理，请稍后读取原任务；没有创建续段') from error
        raise
    api.exclusive_nonce(task, body['request_id'], 'reasoning_retirement')
    saved = copy.deepcopy(task.data.get(KEY, {}))
    need(type(saved) is dict and len(saved) <= 64)
    signature = digest({'actor_id': p.user_id, 'body': body})
    prior = saved.get(body['request_id'])
    if prior is not None:
        need(prior.get('actor_id') == p.user_id and prior.get('body_hash') == signature
             and prior.get('activation_id') == body['activation_id'] and prior.get('job_id') == job.id)
        need(historical_record(db, p, task, body['request_id']) == prior)
        return {'schema': SCHEMA, 'status': 'retired', 'task': workflow.public_view(task, release, business, p, db=db)}
    need(len(saved) < 64)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db, p, task)
    # The independent-advice path takes the same Task lock before creating or
    # starting a plan. Serialize the two paths rather than risk retiring the
    # original while an independently prepared or in-flight analysis exists.
    from .same_goal_advice_store_v302 import _inventory, _preparation_open
    need(not any(_preparation_open(plan) for plan, _, _ in _inventory(db, p, task)),
         '本任务已有未结清的业务分析，请先核对并处理原分析；没有停止原步骤')
    need(not any(item.get('activation_id') == body['activation_id'] for item in saved.values()))
    state = task.data['graph_runtime']; activation = state['activations'].get(body['activation_id'])
    need(isinstance(activation, dict) and state['status'] in {'running', 'waiting'}
         and state['cancel_epoch'] == body['cancel_epoch'] == activation.get('epoch')
         and activation.get('version') == body['version'] and activation.get('status') == 'ready')
    node = workflow.node_for(graph, activation)
    need(node.get('id') == 'reasoning' and node.get('kind') == 'ability'
         and node['execution']['adapter'] == 'business_goal_ready_v288')
    if node.get('actor_id') != p.user_id:
        raise HTTPException(403, '请由原业务步骤负责人办理此操作')
    current_transport = task.data.get(workflow.TRANSPORT, {}).get(body['activation_id'])
    need(current_transport is not None and current_transport.get('job_id') == job.id)
    scope._job_matches(task, job, state, activation, node)
    expected = workflow.invoke_pure(workflow.graph_api.ability_inputs, graph, state, activation['id'])
    need(expected == job.data['inputs'])
    capability = scoped(db, Capability, node['execution']['capability_id'], p)
    need(capability.content_hash == capability.validated_hash == digest(capability.data)
         == node['execution']['capability_hash'])
    execution = capability.data.get('execution', {})
    need(execution.get('adapter') == 'business_goal_ready_v288' and type(execution.get('model_profile_id')) is str)
    _no_prior_dispatch(db, p, task, job, activation, current_transport, execution['model_profile_id'])
    head = provider._head(db, p, task)
    need(head is not None and head['head_hash'] == body['expected_head_hash'], '最新评价已变化，请重新核对原任务')
    evaluated = project(db, p, task, release, business, graph)
    need(evaluated['status'] == 'superseded' and evaluated['activation_id'] == activation['id'],
         '原步骤是否已被新评价替换暂无法核对；原任务没有变化')
    audit_id = uid()
    record = {'schema': SCHEMA, 'request_id': body['request_id'], 'actor_id': p.user_id,
              'body_hash': signature, 'activation_id': activation['id'], 'job_id': job.id,
              'graph_hash': state['graph_hash'], 'input_hash': job.data['input_hash'],
              'head_hash': head['head_hash'], 'audit_id': audit_id, 'status': 'retired',
              'reason_hash': digest(body['reason'])}
    transports = copy.deepcopy(task.data[workflow.TRANSPORT])
    transports[activation['id']] = {**current_transport, 'phase': 'retired_for_new_evaluation'}
    saved[body['request_id']] = record
    job.status = 'cancelled'; job.version += 1
    task.data = {**task.data, workflow.TRANSPORT: transports, KEY: saved}; task.version += 1
    db.add(Audit(id=audit_id, workspace_id=p.workspace_id, actor_id=p.user_id,
                 action=ACTION, object_id=task.id, data={**record, 'reason': body['reason']}))
    db.flush()
    return {'schema': SCHEMA, 'status': 'retired', 'task': workflow.public_view(task, release, business, p, db=db)}
