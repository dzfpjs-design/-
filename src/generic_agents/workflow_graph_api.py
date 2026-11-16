"""Human-facing, per-activation commands. Graph transport is never editable JSON."""
import copy
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select,update
from .contracts import Contract, digest
from .models import Job, Event, Invocation, now
from .setup_api import P, DB
from .services import require, scoped, audit
from . import workflow_graph as runtime
from .report_text_parse_scope import report_text_parse_scope
from .signal_dual_read_session import read_session
from . import workflow_graph_human_budget as human_budget

router = APIRouter()
TaskReadDB = Annotated[object, Depends(read_session)]


def context(db, p, identity, lock=False):
    from .workspace_api import saved_execution
    task, release, business = saved_execution(db, p, identity, lock)
    if business.get('execution_mode') != 'confirmed_graph_workflow' or not runtime.marked(task):
        raise HTTPException(409, '此任务未绑定完整协同执行设计')
    graph = runtime.graph_for(release, business)
    runtime.invoke_pure(runtime.graph_api.check_run, graph, task.data['graph_runtime'])
    from .workflow_graph_waits import checked
    checked(task, runtime.business_binding(release, business))
    return task, release, business, graph


@router.get('/tasks/{identity}/graph/human-budget')
def read_human_budget(identity: str, p: P, db: TaskReadDB):
    task, _, _, graph = context(db, p, identity)
    return human_budget.read(db, p, task, graph)


@router.get('/tasks/{identity}/graph/reasoning-evaluation-status')
def read_reasoning_evaluation_status(identity: str, p: P, db: TaskReadDB):
    task, release, business, graph = context(db, p, identity)
    from .graph_reasoning_evaluation_status_v335 import project
    return project(db, p, task, release, business, graph)


@router.post('/tasks/{identity}/graph/human-budget', status_code=201)
def submit_human_budget(identity: str, body: human_budget.Submit, p: P, db: DB):
    require(p, 'admin', 'operator')
    task, _, _, graph = context(db, p, identity, True)
    result = human_budget.submit(db, p, task, graph, body)
    db.commit()
    return result


@router.post('/tasks/{identity}/graph/human-budget/decide')
def decide_human_budget(identity: str, body: human_budget.Decide, p: P, db: DB):
    require(p, 'admin', 'operator')
    task, _, _, graph = context(db, p, identity, True)
    result = human_budget.decide(db, p, task, graph, body)
    db.commit()
    return result


class NodeCommand(Contract):
    request_id: UUID
    activation_id: str = Field(min_length=1, max_length=200)
    version: int = Field(strict=True, ge=0)
    cancel_epoch: int = Field(strict=True, ge=0)


class Result(NodeCommand):
    output: dict


class CommandHash(Contract):
    schema: Literal['workflow-graph-command-hash-command-v1']
    operation: Literal['complete']
    command: Result


class Draft(NodeCommand):
    draft: dict | str


class Cancel(Contract):
    request_id: UUID
    version: int = Field(strict=True, ge=1)
    cancel_epoch: int = Field(strict=True, ge=0)
    reason: str = Field(min_length=1, max_length=1000)


class Retry(NodeCommand):
    expected_attempt:int=Field(strict=True,ge=1,le=5)
    confirm_repeat_read:bool=False


def args(body, p, state):
    return (body.activation_id, body.output if isinstance(body, Result) else body.draft,
        str(body.request_id), p.user_id, body.version, body.cancel_epoch, state['clock'])


def nonce_records(task, identity):
    records = [('graph', c) for c in task.data['graph_runtime']['commands'] if c['request_id'] == identity]
    budget_command = human_budget.nonce_record(task, identity)
    if budget_command is not None:
        records.append(('human_budget', budget_command))
    for family, key in (('ability_retry', 'graph_retry_requests'), ('message_retry', 'graph_wait_retries'),
            ('reasoning_retirement', 'graph_reasoning_retirements_v337'),
            ('simulation_review', 'graph_simulation_reviews_v202')):
        if identity in task.data.get(key, {}): records.append((family, task.data[key][identity]))
    if len(records) > 1:
        raise HTTPException(409, '同一原请求存在不同操作记录，请核对；未新增执行')
    return records


def exclusive_nonce(task, identity, family):
    records = nonce_records(task, str(identity))
    if records and records[0][0] != family:
        raise HTTPException(409, '此请求标识已用于另一项操作，不能替换原请求')


from .graph_reasoning_retirement_v337 import Retire as RetireReasoning


@router.post('/tasks/{identity}/graph/retire-obsolete-reasoning')
def retire_obsolete_reasoning(identity: str, body: RetireReasoning, p: P, db: DB):
    from .graph_reasoning_retirement_v337 import retire
    result = retire(db, p, identity, body.model_dump(mode='json'))
    db.commit()
    return result


@router.post('/tasks/{identity}/graph/command-hash')
def command_hash(identity: str, body: CommandHash, p: P, db: DB):
    """Read-only typed preparation; final completion repeats every live gate.

    No command, clock, activation, event cursor or audit is written here. The
    response is not an execution grant and cannot be used to refresh an old
    nonce: saved commands recover exclusively through the historical endpoint.
    """
    require(p, 'admin', 'operator')
    task, _, _, graph = context(db, p, identity)
    command = body.command
    if nonce_records(task, str(command.request_id)):
        raise HTTPException(409, '原请求已有保存记录，请核对原请求，不能重新准备正文')
    state = task.data['graph_runtime']
    activation = state['activations'].get(command.activation_id)
    if not activation:
        raise HTTPException(409, '原人工待办已变化，请保留输入后重新核对')
    node = runtime.node_for(graph, activation)
    if node['kind'] != 'human':
        raise HTTPException(409, '能力结果只能由真实后台回执完成，不能准备人工替代结果')
    if node['actor_id'] != p.user_id:
        raise HTTPException(403, '请由此人工待办的原负责人核对结果')
    if (state['status'] in {'succeeded', 'cancelled', 'stopped', 'failed'}
            or activation['status'] != 'ready'
            or command.activation_id not in state['ready_human_ids']
            or activation['version'] != command.version
            or activation['epoch'] != command.cancel_epoch or state['cancel_epoch'] != command.cancel_epoch):
        raise HTTPException(409, '原人工待办版本或取消范围已变化，请保留输入后重新核对')
    runtime.invoke_pure(runtime.graph_api._validate_output, node['output_schema'], command.output)
    runtime.invoke_pure(runtime.graph_api._check_produced_scalars,
        graph, runtime.graph_api._metadata(graph), node['id'], command.output)
    fingerprint = {'activation_id': command.activation_id, 'output': command.output,
        'actor_id': p.user_id, 'expected_version': command.version, 'cancel_epoch': command.cancel_epoch}
    from .report_full_semantic_graph_wire import canonical_wire
    return canonical_wire({'schema': 'workflow-graph-command-hash-v1',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'graph_hash': state['graph_hash'], 'operation': body.operation,
        'command': command.model_dump(mode='json'),
        'body_hash': runtime.invoke_pure(runtime.graph_api._hash, fingerprint)})


@router.put('/tasks/{identity}/graph/draft')
def draft(identity: str, body: Draft, p: P, db: DB):
    require(p, 'admin', 'operator')
    task, release, business, graph = context(db, p, identity, True)
    exclusive_nonce(task, body.request_id, 'graph')
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    from . import workflow_graph_waits as waits
    binding = runtime.business_binding(release, business)
    state = runtime.invoke_pure(runtime.graph_api.save_draft, graph, task.data['graph_runtime'],
        body.activation_id, body.draft, str(body.request_id), p.user_id, body.version, body.cancel_epoch,
        task.data['graph_runtime']['clock'] if waits.enabled(binding) else runtime.clock())
    if waits.enabled(binding): waits.sync(task, binding, state)
    # Saving existing human work neither starts new nodes nor requires a current
    # source. Do not call settle/advance or enqueue a capability from a draft.
    task.data = {**task.data, 'graph_runtime': state}; task.version += 1
    audit(db, p, 'workflow.graph.draft_saved', task.id, {'activation_id': body.activation_id})
    db.commit(); return runtime.public_view(task, release, business, p, db=db)


@router.post('/tasks/{identity}/graph/complete')
def complete(identity: str, body: Result, p: P, db: DB):
    require(p, 'admin', 'operator')
    task, release, business, graph = context(db, p, identity, True)
    exclusive_nonce(task, body.request_id, 'graph')
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    state = task.data['graph_runtime']
    # An exact lost-response replay only checks the original command; it never
    # requires renewed authority or dispatches work from a historical receipt.
    prior = next((c for c in state['commands'] if c['request_id'] == str(body.request_id)), None)
    if prior is not None:
        if 'graph_human_budget_v1' in task.data:
            human_budget.verify(db, p, task)
        from . import workflow_graph_waits as waits
        binding = runtime.business_binding(release, business)
        if waits.enabled(binding):
            waits.replay(task, binding, {'activation_id': body.activation_id, 'output': body.output,
                'request_id': str(body.request_id), 'actor_id': p.user_id,
                'expected_version': body.version, 'cancel_epoch': body.cancel_epoch})
        else: runtime.invoke_pure(runtime.graph_api.complete, graph, state, *args(body, p, state))
        return runtime.public_view(task, release, business, p, db=db)
    activation = state['activations'].get(body.activation_id)
    if not activation or runtime.node_for(graph, activation)['kind'] != 'human':
        raise HTTPException(409, '注册能力只能由真实后台回执完成，不能手工填写冒充')
    from .native_pixel_receipts_v250 import require_task_received
    require_task_received(db, p, task)
    from .workflow_sound import require_task_received as require_sound_received
    require_sound_received(db,p,task)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task,record_actor=True)
    from .workflow_graph_goal_guard_v133 import human_step as goal_human_step
    allowance = goal_human_step(db, p, task, body.activation_id)
    runtime.live(db, p, task, release, business)
    from . import workflow_graph_waits as waits
    binding = runtime.business_binding(release, business)
    from .business_flow_execution_inputs import require_inputs as require_original_inputs
    require_original_inputs(binding, state, body.activation_id)
    from .workflow_job_dependencies_v264 import trace_for, observer_options, save_control
    observer = trace_for(db, p, task)
    result = (waits.transition(db, p, task, binding, operation='complete', command={
        'activation_id': body.activation_id, 'output': body.output, 'request_id': str(body.request_id),
        'actor_id': p.user_id, 'expected_version': body.version, 'cancel_epoch': body.cancel_epoch}, **observer_options(observer))
        if waits.enabled(binding) else runtime.invoke_pure(runtime.graph_api.complete, graph, state,
            body.activation_id, body.output, str(body.request_id), p.user_id, body.version,
            body.cancel_epoch, runtime.clock(), **observer_options(observer)))
    node = runtime.node_for(graph, activation)
    receipt = {'request_id': str(body.request_id), 'activation_id': body.activation_id,
        'step_id': node['source_step_id'] or node['id'], 'actor': p.user_id, 'at': now().isoformat(),
        'execution_mode': 'human_graph_result', 'output': copy.deepcopy(body.output), 'entity_state_updated': False}
    if allowance is not None:
        receipt['human_budget'] = allowance
    task.data = {**task.data, 'workflow_runtime': {**task.data['workflow_runtime'],
        'receipts': [*task.data['workflow_runtime']['receipts'], receipt]}}
    if observer is not None:
        task.data = {**task.data, 'graph_runtime': result}
        save_control(db, p, task, result, observer)
    runtime.settle(db, p, task, release, business, result)
    audit_data = {'activation_id': body.activation_id}
    if allowance is not None:
        audit_data.update(human_budget=allowance, receipt_hash=digest(receipt), request_id=receipt['request_id'])
    audit(db, p, 'workflow.graph.human_completed', task.id, audit_data)
    from .native_pixel_provider_v250 import record_human_receipt
    record_human_receipt(db, p, task, receipt)
    db.commit(); return runtime.public_view(task, release, business, p, db=db)


@router.post('/tasks/{identity}/graph/cancel')
def cancel(identity: str, body: Cancel, p: P, db: DB):
    task, release, business, graph = context(db, p, identity, True)
    exclusive_nonce(task, body.request_id, 'graph')
    from .workflow_cancellation import authorize, require_not_accepted
    from .relations import lock_current_actor
    authorize(p, task); lock_current_actor(db, p)
    require_not_accepted(db, p, task)
    from . import workflow_graph_waits as waits
    binding = runtime.business_binding(release, business)
    result = runtime.invoke_pure(runtime.graph_api.cancel, graph, task.data['graph_runtime'],
        str(body.request_id), p.user_id, body.version, body.cancel_epoch,
        task.data['graph_runtime']['clock'] if waits.enabled(binding) else runtime.clock(), body.reason)
    if waits.enabled(binding): waits.sync(task, binding, result)
    runtime.settle(db, p, task, release, business, result)
    # Running calls may have left this process. Preserve their real Invocation
    # and lease; a late result is recorded but cannot advance the cancelled epoch.
    # Source-bound jobs observe the atomically persisted epoch under their own
    # Job→Task lock order. Do not lock queued Jobs after holding the Task here.
    if not waits.enabled(binding):
        db.execute(update(Job).where(Job.workspace_id==p.workspace_id,
            Job.data['task_id'].as_string()==task.id,Job.status=='queued',
            Job.kind.in_([runtime.KIND,'advance_full_semantic_graph','receive_full_semantic_graph_event']))
            .values(status='cancelled',version=Job.version+1))
    audit(db, p, 'workflow.graph.cancelled', task.id, {'request_id': str(body.request_id)})
    db.commit(); return runtime.public_view(task, release, business, p, db=db)


@router.get('/tasks/{identity}/graph/requests/{request_id}')
def request(identity: str, request_id: UUID, p: P, db: TaskReadDB):
    from .report_full_source_read_scope import task_full_source_read_scope
    with report_text_parse_scope(), task_full_source_read_scope(db, p, identity):
        task, release, business, graph = context(db, p, identity)
        records = nonce_records(task, str(request_id)); found = records[0][1] if records else None
        if found is not None and found['actor_id'] != p.user_id:
            raise HTTPException(403, '请由原提交账号核对请求')
        if records and records[0][0] == 'reasoning_retirement':
            from .graph_reasoning_retirement_v337 import historical_record, public_request
            found = public_request(historical_record(db, p, task, request_id))
        if records and records[0][0] == 'simulation_review':
            from .workflow_simulation_review_v202 import historical_record, public_request
            found = public_request(historical_record(db, task, request_id))
        if found is not None and found['actor_id'] != p.user_id:
            raise HTTPException(403, '请由原提交账号核对请求')
        return {'schema': 'workflow-graph-request-recovery-v1', 'found': found is not None,
            'request': copy.deepcopy(found), 'task': runtime.public_view(task, release, business, p, db=db)}


from .workflow_simulation_review_v202 import ReviewCommand


@router.get('/tasks/{identity}/graph/simulation-review')
def simulation_review_basis(identity: str, activation_id: str, p: P, db: TaskReadDB):
    from .report_full_source_read_scope import task_full_source_read_scope
    from .workflow_simulation_review_v202 import describe
    with report_text_parse_scope(), task_full_source_read_scope(db, p, identity):
        task, _, _, _ = context(db, p, identity)
        return describe(db, p, task, activation_id)


@router.post('/tasks/{identity}/graph/simulation-review')
def simulation_review_submit(identity: str, body: ReviewCommand, p: P, db: DB):
    require(p, 'admin')
    # Preserve worker Job -> Task lock order. Replays read their immutable
    # request instead of acquiring a new Job lock or re-queuing any work.
    initial, _, _, _ = context(db, p, identity)
    exclusive_nonce(initial, body.request_id, 'simulation_review')
    job = None
    if str(body.request_id) not in initial.data.get('graph_simulation_reviews_v202', {}):
        transport = initial.data.get(runtime.TRANSPORT, {}).get(body.activation_id)
        if type(transport) is not dict or not transport.get('job_id'):
            raise HTTPException(409, '此节点没有可核对的原后台工作')
        job = scoped(db, Job, transport['job_id'], p, True)
    task, _, _, _ = context(db, p, identity, True)
    exclusive_nonce(task, body.request_id, 'simulation_review')
    from .workflow_simulation_review_v202 import submit
    result = submit(db, p, task, job, body)
    db.commit()
    return result


def receive_event(db, p, task, release, business, event):
    """Only persisted, workspace-scoped source events can satisfy a wait."""
    graph = runtime.graph_for(release, business)
    from .workflow_graph_waits import enabled
    if enabled(runtime.business_binding(release, business)):
        raise HTTPException(409, '此流程只接纳已核对的消息回执，旧事件不能替代')
    if event.workspace_id != p.workspace_id: raise HTTPException(403, '事件不属于此工作区')
    if event.kind not in {s['event_type'] for s in graph['subscriptions']}: return False
    from .workflow_graph_goal_guard_v133 import progress as goal_progress
    goal_progress(db, p, task)
    runtime.live(db, p, task, release, business)
    if 'correlation' not in event.data or 'output' not in event.data:
        raise HTTPException(409, '来源事件未提供已登记的关联标识和类型化输出，不能猜测关联')
    from .workflow_job_dependencies_v264 import trace_for, observer_options, save_control
    observer = trace_for(db, p, task)
    state = runtime.invoke_pure(runtime.graph_api.event, graph, task.data['graph_runtime'],
        event.id, event.kind, event.data['correlation'], event.data['output'], runtime.clock(),
        received_at=event.created_at.timestamp(), **observer_options(observer))
    if observer is not None:
        task.data = {**task.data, 'graph_runtime': state}
        save_control(db, p, task, state, observer)
    runtime.settle(db, p, task, release, business, state)
    audit(db, p, 'workflow.graph.event_received', task.id, {'event_id': event.id, 'event_hash': digest(event.data)})
    return True


class EventReceipt(Contract):
    event_id: UUID


class MessageRetry(Contract):
    request_id: UUID
    job_version: int = Field(strict=True, ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class MessageRetryV2(MessageRetry):
    job_id: UUID


@router.post('/tasks/{identity}/graph/retry-messages')
def retry_messages(identity: str, body: MessageRetry, p: P, db: DB):
    return _retry_messages(identity, body, p, db)


@router.post('/tasks/{identity}/graph/retry-messages-v2')
def retry_messages_v2(identity: str, body: MessageRetryV2, p: P, db: DB):
    return _retry_messages(identity, body, p, db, expected_job_id=str(body.job_id))


def _retry_messages(identity, body, p, db, *, expected_job_id=None):
    from . import workflow_graph_waits as waits
    require(p, 'admin', 'operator')
    first, first_release, first_business, _ = context(db, p, identity)
    exclusive_nonce(first, body.request_id, 'message_retry')
    signature = digest({'actor_id': p.user_id, 'body': body.model_dump(mode='json')})
    prior = first.data.get('graph_wait_retries', {}).get(str(body.request_id))
    if prior is not None:
        if (prior['actor_id'] != p.user_id or prior['body_hash'] != signature
                or expected_job_id is not None and prior['job_id'] != expected_job_id):
            raise HTTPException(409, '原恢复请求不能更换账号或内容')
        return runtime.public_view(first, first_release, first_business, p, db=db)
    if expected_job_id is None:
        # The old command never signed its target Job. Its historical receipts
        # remain readable, but it cannot select today's poll for a new request.
        raise HTTPException(409, '请使用带原检查标识的新恢复方式；已有请求可按原编号核对')
    pointer = first.data.get('graph_wait_poll', {})
    if pointer.get('job_id') != expected_job_id:
        raise HTTPException(409, '原消息检查已变化，请核对当前记录')
    job = scoped(db, Job, expected_job_id, p, True)  # Job→parent→Task.
    task, release, business, graph = context(db, p, identity, True)
    exclusive_nonce(task, body.request_id, 'message_retry')
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    if p.role != 'admin' and p.user_id != task.owner_id:
        raise HTTPException(403, '请由任务负责人或管理员恢复消息检查')
    saved = copy.deepcopy(task.data.get('graph_wait_retries', {}))
    if str(body.request_id) in saved:
        prior = saved[str(body.request_id)]
        if (prior['actor_id'] != p.user_id or prior['body_hash'] != signature
                or prior['job_id'] != expected_job_id):
            raise HTTPException(409, '原恢复请求不能更换账号或内容')
        return runtime.public_view(task, release, business, p, db=db)
    if task.data.get('graph_wait_poll', {}).get('job_id') != expected_job_id or job.id != expected_job_id:
        raise HTTPException(409, '原消息检查已变化，请核对当前记录')
    binding = runtime.business_binding(release, business)
    if not waits.enabled(binding) or task.data.get('graph_wait_poll', {}).get('job_id') != job.id:
        raise HTTPException(409, '原消息检查已变化，请核对当前记录')
    authority = waits.checked(task, binding); state = task.data['graph_runtime']
    if (job.kind != waits.KIND or job.version != body.job_version or job.status not in {'failed', 'blocked'}
            or job.data.get('authority_hash') != authority['authority_hash']
            or job.data.get('cancel_epoch') != state['cancel_epoch']
            or state['status'] in {'cancelled', 'succeeded', 'stopped', 'failed'}
            or len(saved) >= 32):
        raise HTTPException(409, '原消息检查不可恢复，请核对状态；等待窗口和输入保留')
    runtime.live(db, p, task, release, business)
    from .worker import actor as worker_actor
    try: worker_actor(db, p.workspace_id, job.data['actor_id'])
    except ValueError:
        raise HTTPException(409, '原后台执行账号已暂停，请核对负责人；原等待窗口和输入保留') from None
    job.status = 'queued'; job.available_at = now(); job.lease_token = None; job.lease_until = None
    job.version += 1; job.data = {**job.data, 'recovery_only': job.attempts >= 3}
    saved[str(body.request_id)] = {'actor_id': p.user_id, 'body_hash': signature, 'job_id': job.id}
    task.data = {**task.data, 'graph_wait_retries': saved}; task.version += 1
    audit(db, p, 'workflow.graph.message_retry_requested', task.id,
        {'job_id': job.id, 'request_id': str(body.request_id)})
    db.commit(); return runtime.public_view(task, release, business, p, db=db)


@router.post('/tasks/{identity}/graph/receive-event')
def event_receipt(identity: str, body: EventReceipt, p: P, db: DB):
    require(p, 'admin', 'operator')
    task, release, business, graph = context(db, p, identity, True)
    event = scoped(db, Event, str(body.event_id), p)
    if not receive_event(db, p, task, release, business, event):
        raise HTTPException(409, '此事件类型不在当前流程的等待范围内')
    db.commit(); return runtime.public_view(task, release, business, p, db=db)


@router.post('/tasks/{identity}/graph/retry')
def retry(identity:str,body:Retry,p:P,db:DB):
    require(p,'admin','operator')
    # Freeze the candidate from an unlocked read, then preserve the worker's
    # Job→parent→Task order. Never chase a changed transport under a Task lock.
    initial, _, _, _ = context(db, p, identity)
    exclusive_nonce(initial, body.request_id, 'ability_retry')
    candidate = initial.data.get(runtime.TRANSPORT, {}).get(body.activation_id)
    original_job = None
    if str(body.request_id) not in initial.data.get('graph_retry_requests', {}):
        if candidate is None:
            raise HTTPException(409, '此节点当前没有可恢复的受阻执行')
        original_job = scoped(db, Job, candidate['job_id'], p, True)
    task,release,business,graph=context(db,p,identity,True)
    exclusive_nonce(task, body.request_id, 'ability_retry')
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    saved=copy.deepcopy(task.data.get('graph_retry_requests',{}))
    signature=digest({'actor_id':p.user_id,'body':body.model_dump(mode='json')})
    prior=saved.get(str(body.request_id))
    if prior:
        if prior['actor_id']!=p.user_id or prior['body_hash']!=signature:raise HTTPException(409,'原恢复请求不能更换账号、节点或尝试')
        return runtime.public_view(task,release,business,p, db=db)
    state=task.data['graph_runtime'];activation=state['activations'].get(body.activation_id)
    if not activation or activation['version']!=body.version or state['cancel_epoch']!=body.cancel_epoch:
        raise HTTPException(409,'节点已变化，请保留原输入并核对')
    node=runtime.node_for(graph,activation)
    if node['kind']!='ability' or node['actor_id']!=p.user_id:raise HTTPException(403,'请由已指定的执行账号恢复注册能力')
    from .graph_reasoning_retirement_v337 import is_retired
    if is_retired(task, body.activation_id):
        raise HTTPException(409, '原步骤已停用并保留；不能重新运行原请求')
    item=task.data[runtime.TRANSPORT].get(body.activation_id)
    if not item or item['attempt']!=body.expected_attempt or item['phase']!='blocked':
        raise HTTPException(409,'此节点当前没有可恢复的受阻执行')
    if original_job is None or item['job_id'] != original_job.id:
        raise HTTPException(409, '原后台工作已变化，请保留原请求并重新核对')
    job=original_job
    if job.status not in {'failed','cancelled','blocked'}:raise HTTPException(409,'原后台工作尚在运行，请等待保存结果')
    call=db.scalar(select(Invocation).where(Invocation.workspace_id==p.workspace_id,
        Invocation.idempotency_key==item['invocation_key']).with_for_update())
    service = node['execution']['adapter'] == 'workflow_service'
    from .business_reasoning_protocols_v276 import applies as reasoning_adapter, original_capture
    reasoning = reasoning_adapter(node['execution']['adapter'])
    reasoning_original = original_capture(node['execution']['adapter'], call, task, job, node)
    local_pre_dispatch = None
    resense_pre_dispatch = None
    if service:
        from .workflow_service_catalog_v197 import monitoring_receipt_retained
        from .workflow_service_v195 import STATE as service_state_schema, STATE_KEYS as service_state_keys
        service_state = call.data.get('service_state') if call is not None else None
        recoverable = call is not None and (call.status in {'succeeded', 'waiting_receipt'}
            or call.status == 'unknown' and type(service_state) is dict
            and set(service_state) == service_state_keys and service_state.get('schema') == service_state_schema
            or monitoring_receipt_retained(call, db=db, p=p, task=task, job=job))
        if not recoverable:
            if call is not None:
                raise HTTPException(409, '服务原调用尚无安全可核对的回执状态；原请求保留，不创建新的调用或补采')
            binding = runtime.business_binding(release, business)
            if binding.get('schema') == 'authored-goal-data-workflow-binding-v288' and node['id'] == 'resense':
                from .workflow_resense_pre_dispatch_recovery_v290 import (KEY as resense_recovery_key,
                    prepare as prepare_resense, prepare_continuation as continue_resense)
                prepare_resense_recovery = continue_resense if resense_recovery_key in job.data else prepare_resense
                resense_pre_dispatch = prepare_resense_recovery(db,p,task,release,business,graph,
                    activation,node,job,item,body,signature)
            else:
                from .workflow_local_pre_dispatch_recovery_v287 import (KEY as local_recovery_key,
                    prepare, prepare_continuation)
                prepare_local_recovery = prepare_continuation if local_recovery_key in job.data else prepare
                local_pre_dispatch = prepare_local_recovery(db,p,task,release,business,graph,
                    activation,node,job,item,body,signature)
        # This only schedules a fresh check of the ORIGINAL Job. The worker's
        # current lease/IAM/approval and service state's independent Audit are
        # still checked by process/reconcile; a marker is never permission.
    elif reasoning and call is not None and call.status != 'succeeded' and not reasoning_original:
        raise HTTPException(409,'原业务分析尚无可核对的首答；保留原回答与费用，不重新调用模型')
    elif call is not None and call.status in {'unknown','running'} and not reasoning_original:
        raise HTTPException(409,'原调用的执行状态仍不确定；只核对原回执，不创建新的调用')
    runtime.live(db,p,task,release,business)
    transports=copy.deepcopy(task.data[runtime.TRANSPORT])
    if resense_pre_dispatch is not None:
        from .workflow_graph_goal_guard_v133 import progress as resense_goal_progress
        resense_goal_progress(db,p,task)
        from .workflow_resense_pre_dispatch_recovery_v290 import requeue as requeue_resense_recovery
        requeue_resense_recovery(db,p,job,resense_pre_dispatch)
        transports[body.activation_id]={**item,'phase':'queued','reason':None}
    elif local_pre_dispatch is not None:
        from .workflow_graph_goal_guard_v133 import progress as local_goal_progress
        local_goal_progress(db,p,task)
        from .workflow_local_pre_dispatch_recovery_v287 import requeue as requeue_local_recovery
        requeue_local_recovery(db,p,job,local_pre_dispatch)
        transports[body.activation_id]={**item,'phase':'queued','reason':None}
    elif service or reasoning_original or call is not None and call.status=='succeeded':
        job.status='queued';job.available_at=now();job.lease_token=None;job.lease_until=None;job.version+=1
        job.data={**job.data,'recovery_only':True}
        transports[body.activation_id]={**item,'phase':'queued','reason':None}
    else:
        from .workflow_graph_goal_guard_v133 import new_attempt as goal_new_attempt
        goal_new_attempt(db,p,task)
        if body.confirm_repeat_read is not True:raise HTTPException(409,'请明确确认再次执行只读处理；原记录仍保留')
        if call is None and item.get('dispatch_committed') is True:
            raise HTTPException(409,'派发已提交但原调用回执缺失，请核对原请求；不能据此认定未执行')
        attempt=item['attempt']+1
        if attempt>node['execution']['max_attempts']:raise HTTPException(409,'已达到此能力明确登记的尝试上限')
        inputs=runtime.invoke_pure(runtime.graph_api.ability_inputs,graph,state,body.activation_id)
        key='graph:'+task.id+':'+body.activation_id+':'+str(attempt)
        from .workflow_job_dependencies_v264 import KEY as DEPENDENCY_KEY, bind_graph
        next_data={k:v for k,v in job.data.items() if k!=DEPENDENCY_KEY}
        next_job=Job(workspace_id=p.workspace_id,kind=runtime.KIND,status='queued',idempotency_key=key,
            data={**next_data,'inputs':inputs,'input_hash':digest(inputs),'invocation_key':key,'attempt':attempt,
                'recovery_only':False,'retry_request_id':str(body.request_id)})
        db.add(next_job);db.flush()
        bind_graph(db,p,task,next_job)
        transports[body.activation_id]={'job_id':next_job.id,'invocation_key':key,'input_hash':digest(inputs),
            'phase':'queued','cancel_epoch':state['cancel_epoch'],'attempt':attempt,
            'previous':[*item.get('previous',[]),copy.deepcopy(item)]}
    saved[str(body.request_id)]={'actor_id':p.user_id,'body_hash':signature,
        'activation_id':body.activation_id,'job_id':transports[body.activation_id]['job_id']}
    task.data={**task.data,runtime.TRANSPORT:transports,'graph_retry_requests':saved};task.version+=1
    audit(db,p,'workflow.graph.recovery_requested',task.id,{'activation_id':body.activation_id,'request_id':str(body.request_id)})
    db.commit();return runtime.public_view(task,release,business,p, db=db)


@router.get('/instances/{identity}/graph-task-inputs')
def graph_task_inputs(identity: str, release_id: str, business_id: str, p: P, db: DB):
    from .graph_task_inputs_v120 import describe
    return describe(db, p, identity, release_id, business_id)
