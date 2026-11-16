"""Explicit supplementary reads on the original Task; never a formal goal gate.

Cloud proposals do not execute this module. Human adoption and a separate local
execution command are required. Its DataRuns share the original goal's budget.
"""
import copy
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, StrictInt, field_validator
from sqlalchemy import select, or_, and_

from .contracts import Contract, digest
from .models import Audit, DataRun, Job, Task, Invocation, now
from .services import scoped, audit, check_version, query_evidence, require

JOB_KIND = 'execute_native_task_query'
MARKER = 'native_task_query_contract'
LIVE = {'queued', 'running'}


class Command(Contract):
    request_id: str
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('request_id')
    @classmethod
    def valid_uuid(cls, value):
        try:
            if str(UUID(value)) != value:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('请使用固定 UUID 请求编号')
        return value


class Execute(Command):
    proposal_id: str = Field(min_length=1, max_length=100)


class Recover(Contract):
    operation: Literal['execute', 'cancel'] = 'execute'
    run_id: str | None = None
    original_request: Execute | Command
    action: str = Field(pattern='^(probe|abandon)$')
    reason: str = Field(min_length=1, max_length=2000)


def _manage(db, p, task):
    from .native_goals import manage
    from .relations import lock_current_actor
    manage(p, task)
    lock_current_actor(db, p)


def _guard(db, p, task, key=None):
    from .native_goals import guard
    _manage(db, p, task)
    if not task.data.get('native_goal_context'):
        raise HTTPException(409, '请先确认原任务目标；未确认时可采用到目标草稿')
    guard(db, p, task, key)


def _journal(db, p, task, request_id):
    return db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'native_task_query.command', Audit.object_id == task.id,
        Audit.data['request_id'].as_string() == request_id))


def _check_command(entry, p, body, operation, *, run_id=None, allow_abandoned=False):
    if (entry.actor_id != p.user_id or entry.data.get('body_hash') != digest(body.model_dump())
        or entry.data.get('operation') != operation
        or run_id is not None and entry.data.get('run_id') != run_id):
        raise HTTPException(409, '请求编号不能更换提交者、操作或完整输入')
    if entry.data.get('abandoned') and not allow_abandoned:
        raise HTTPException(409, '此请求已明确作废，迟到的原请求不会执行')


def _record(db, p, task, body, operation, run_id=None, abandoned=False):
    audit(db, p, 'native_task_query.command', task.id, {
        'request_id': body.request_id, 'body_hash': digest(body.model_dump()),
        'operation': operation, 'run_id': run_id, 'abandoned': abandoned})


def _base(db, p, request, *, task=None):
    from .data_tools import managed_include_trusted_cases
    include_cases=managed_include_trusted_cases(db,p,task)
    return query_evidence(db, p, request['entity_ids'], request['fields'], learning=False,
        requirement=request['evidence_requirement'], fixed_window=request['resolved_time'],
        relation_query=request.get('relation_query'),**({'include_trusted_cases':False} if not include_cases else {}))


def _budget(db, p, task, additional=0):
    from .goal_queries import call_count
    from .native_goal_queries import step_count
    from .native_step_execution import step_count as execution_steps
    spec = task.data['goal_spec']
    used_calls = call_count(db, p, task) or 0
    used_steps = (len(task.data['workflow_runtime']['receipts']) + execution_steps(task)
        + step_count(db, p, task))
    reserve = int(task.status != 'workflow_completed')
    if used_calls + additional > spec['max_tool_calls']:
        raise HTTPException(409, '原任务剩余累计调用预算不足，原结果保留')
    if used_steps + additional + reserve > spec['max_steps']:
        raise HTTPException(409, '原任务剩余步骤预算不足，需保留人工完成步骤')


def _linked(db, p, task, row):
    from .goals import snapshot
    from .native_query_catalog import catalog_for_task
    contract = row.data.get(MARKER)
    if (not contract or digest(contract) != row.data.get(MARKER + '_hash')
        or contract.get('task_id') != task.id or contract.get('actor_id') != row.owner_id
        or contract.get('goal_snapshot') != snapshot(task)
        or contract.get('context_hash') != task.data.get('native_goal_context_hash')
        or contract.get('object_definition_bindings_hash') != task.data.get('object_definition_bindings_hash')
        or row.data.get('request') != contract.get('request')
        or row.question != contract['request']['question']
        or row.data.get('reference_only') is not True):
        raise HTTPException(409, '补充查询与原任务、固定目标或完整输入无法核对')
    capabilities = {cap.id: cap for cap in catalog_for_task(db, p, task)}
    for identity, signature in contract['capabilities'].items():
        cap = capabilities.get(identity)
        if not cap or cap.content_hash != signature or digest(cap.data) != signature:
            raise HTTPException(409, '本机只读查询能力的固定版本已变化')
    return contract, capabilities


def execute(db, p, task, body):
    _manage(db, p, task)
    entry = _journal(db, p, task, body.request_id)
    if entry:
        _check_command(entry, p, body, 'execute')
        return scoped(db, DataRun, entry.data['run_id'], p)
    _guard(db, p, task)
    check_version(task, body.expected_version)
    from .native_query_proposals import adopted_reference
    adoption = adopted_reference(db, p, task, body.proposal_id)
    request = copy.deepcopy(adoption['request'])
    if (request.get('mode') != 'structured' or request.get('allow_external_tools')
        or request['entity_ids'] != task.data['entity_ids']):
        raise HTTPException(409, '补充查询只允许原任务全部对象范围内的本机只读请求')
    from .data_requirements import prepare_request
    from .data_tools import managed_task_kwargs
    request = prepare_request(db,p,request,_confirmed_plan=True,**managed_task_kwargs(task))
    if request.get('clarifications'):
        raise HTTPException(422, '；'.join(request['clarifications']))
    from .native_goal_queries import _source_hash
    from .native_query_catalog import catalog_for_task
    from .data_tools import validate_steps
    capabilities = {cap.id: cap for cap in catalog_for_task(db, p, task)}
    steps = validate_steps(db,p,request,request['tool_steps'],**managed_task_kwargs(task))
    if any(step['capability_id'] not in capabilities for step in steps):
        raise HTTPException(409, '补充查询只能采用本业务版本配备的三种本机只读能力')
    _budget(db, p, task, len(steps))
    # A single active query per Task prevents concurrent reservations from
    # consuming a budget before the actual Invocation fences can account for it.
    live = db.scalar(select(DataRun.id).where(DataRun.workspace_id == p.workspace_id,
        DataRun.status.in_(LIVE),
        (DataRun.data[MARKER]['task_id'].as_string() == task.id)
        | (DataRun.data['native_query_contract']['task_id'].as_string() == task.id)))
    if live:
        raise HTTPException(409, '原任务已有查询在执行，请保留草稿并等待结果')
    base = _base(db, p, request,task=task)
    from .goals import snapshot
    contract = {'schema': 'native-task-query-v1', 'task_id': task.id,
        'actor_id': p.user_id, 'context_hash': task.data['native_goal_context_hash'],
        'session_id': p.session_id,
        'goal_snapshot': snapshot(task), 'proposal_id': body.proposal_id,
        'adoption': copy.deepcopy(adoption), 'request': request,
        'source_hash': _source_hash(base), 'capabilities': {
            step['capability_id']: capabilities[step['capability_id']].content_hash for step in steps},
        **({'object_definition_bindings_hash':task.data['object_definition_bindings_hash']}
            if 'object_definition_bindings' in task.data or 'object_definition_bindings_hash' in task.data else {})}
    row = DataRun(workspace_id=p.workspace_id, owner_id=p.user_id,
        question=request['question'], status='queued', data={'request': request,
            MARKER: contract, MARKER + '_hash': digest(contract), 'reference_only': True,
            'progress': {'stage': 'queued', 'saved_at': now().isoformat()}})
    db.add(row); db.flush()
    job = Job(workspace_id=p.workspace_id, kind=JOB_KIND,
        idempotency_key='native-task-query:' + row.id,
        data={'task_id': task.id, 'query_id': row.id, 'actor_id': p.user_id,
            'contract_hash': digest(contract)})
    db.add(job); db.flush()
    row.data = {**row.data, 'native_query_job_id': job.id}
    _record(db, p, task, body, 'execute', row.id)
    task.version += 1
    return row


def applies(db, p, key):
    if not isinstance(key, str) or not key.startswith('data-tool:') or len(key.split(':')) != 3:
        return False
    row = db.scalar(select(DataRun).where(DataRun.workspace_id == p.workspace_id,
        DataRun.id == key.split(':')[1]))
    return bool(row and row.data.get(MARKER))


def before_dispatch(db, p, cap, inputs, key):
    if not applies(db, p, key):
        return False
    row = scoped(db, DataRun, key.split(':')[1], p)
    task = scoped(db, Task, row.data[MARKER]['task_id'], p, True)
    row = scoped(db, DataRun, row.id, p, True)
    _guard(db, p, task, key)
    contract, capabilities = _linked(db, p, task, row)
    _origin_actor(db, p, contract)
    if (row.status not in LIVE or p.user_id != row.owner_id
        or db.info.get('worker_lease', {}).get('job_id') != row.data['native_query_job_id']):
        raise HTTPException(409, '补充查询已停止或不属于当前后台执行者')
    _budget(db, p, task)
    from .native_goal_queries import _source_hash, _inputs, _receipt
    from .data_tools import frame
    base = _base(db, p, row.data['request'],task=task)
    if base['status'] != 'sufficient' or _source_hash(base) != contract['source_hash']:
        raise HTTPException(409, '补充查询执行前原资料或关系依据已变化')
    frames = {'evidence': frame(base)}
    traces = row.data.get('tool_trace', [])
    for index, step in enumerate(row.data['request']['tool_steps']):
        if step['id'] == key.split(':')[2]:
            expected = _inputs(row.data['request'], capabilities[step['capability_id']],
                step, frames[step['input_from']], key)
            if cap.id != step['capability_id'] or inputs != expected or len(traces) != index + 1:
                raise HTTPException(409, '补充查询调用与已确认步骤或来源链不一致')
            return True
        if index >= len(traces):
            raise HTTPException(409, '补充查询缺少前序成功回执')
        frames[step['id']] = _receipt(db, p, row, capabilities[step['capability_id']],
            step, traces[index], frames[step['input_from']])
    raise HTTPException(409, '补充查询调用没有对应已确认步骤')


def verify_result(db, p, task, row):
    """Read-only provenance verification, with no formal acceptance side effects."""
    from .native_goal_queries import _source_hash, _relations, _receipt
    from .data_tools import frame, validate_steps
    contract, capabilities = _linked(db, p, task, row)
    result = row.data.get('result')
    if not result or digest(result) != row.data.get('result_hash'):
        raise HTTPException(409, '补充查询结果摘要无法核对')
    base = _base(db, p, row.data['request'],task=task)
    from .data_gaps import receipts_ready
    if (base['status'] != 'sufficient' or not receipts_ready(db,p,{'result':base})
        or contract['source_hash'] != _source_hash(base) or frame(result) != frame(base)
        or _relations(result) != _relations(base)):
        raise HTTPException(409, '资料或关系已变化；这里保留的是原时点查询结果')
    if row.status != 'sufficient' or result.get('status') != 'sufficient':
        return False
    from .data_tools import managed_task_kwargs
    steps = validate_steps(db,p,row.data['request'],row.data['request']['tool_steps'],**managed_task_kwargs(task))
    if not steps:
        if result.get('status') != base['status'] or result.get('derived_records') or result.get('tool_trace'):
            raise HTTPException(409, '结构化读取结果无法核对原资料')
        return True
    context = row.data.get('tool_context', {})
    signature = digest({'records': frame(base), 'steps': steps,
        'capabilities': {s['capability_id']: capabilities[s['capability_id']].content_hash for s in steps},
        'requirement': row.data['request']['evidence_requirement'], 'relations': _relations(base)})
    traces = result.get('tool_trace', [])
    if (context.get('signature') != signature or context.get('source_records') != frame(base)
        or context.get('steps') != steps or result.get('query_tool_steps') != steps
        or result.get('derivation_verification') != 'deterministic_registered_computation'
        or traces != row.data.get('tool_trace') or len(traces) != len(steps)):
        raise HTTPException(409, '补充查询的固定输入或逐步回执无法核对')
    frames = {'evidence': frame(base)}
    for step, item in zip(steps, traces):
        frames[step['id']] = _receipt(db, p, row, capabilities[step['capability_id']],
            step, item, frames[step['input_from']])
    if result.get('derived_records') != frames[steps[-1]['id']]:
        raise HTTPException(409, '补充查询最终结果与完整来源链不一致')
    return True


def process(db, p, job):
    task = scoped(db, Task, job.data['task_id'], p, True)
    row = scoped(db, DataRun, job.data['query_id'], p, True)
    contract, _ = _linked(db, p, task, row)
    if (job.kind != JOB_KIND or job.id != row.data.get('native_query_job_id')
        or job.data.get('actor_id') != row.owner_id
        or job.data.get('contract_hash') != digest(contract)):
        raise HTTPException(409, '补充查询作业与固定请求不匹配')
    if row.status not in LIVE:
        return row.status
    _guard(db, p, task)
    _origin_actor(db, p, contract)
    from .native_goal_queries import _source_hash
    if contract['source_hash'] != _source_hash(_base(db, p, row.data['request'],task=task)):
        row.status = 'blocked_evidence'; row.version += 1
        row.data = {**row.data, 'error': '排队期间原资料或关系已变化，请核对后明确新建查询'}
        return row.status
    row.status = 'running'; row.version += 1
    row.data = {**row.data, 'progress': {'stage': 'running', 'saved_at': now().isoformat()}}
    from .leases import checkpoint
    checkpoint(db)
    from .agents import data_agent
    from .data_tools import managed_task_kwargs
    state = data_agent(db, p, row.data['request'], row.id,
        cached_only=bool(job.data.get('recovery_only') or job.data.get('lease_recoveries')),
        emit_gaps=False, learning=False,**managed_task_kwargs(task))
    task = scoped(db, Task, task.id, p, True)
    row = scoped(db, DataRun, row.id, p, True)
    if row.status == 'cancelled':
        row.data = {**row.data, 'late_result': copy.deepcopy(state), 'late_result_hash': digest(state)}
        return row.status
    row.status = state['status']; row.version += 1
    row.data = {**row.data, **state, 'result_hash': digest(state.get('result')),
        'error': state.get('error') or (state.get('result') or {}).get('tool_blocker'),
        'progress': {'stage': row.status, 'saved_at': now().isoformat()}}
    try:
        _guard(db, p, task)
        if row.status == 'sufficient':
            verify_result(db, p, task, row)
    except HTTPException as error:
        row.status = 'blocked_evidence'; row.data = {**row.data, 'error': str(error.detail)}
    audit(db, p, 'native_task_query.completed', task.id, {'query_id': row.id, 'status': row.status})
    return row.status


def recover_job(db, p, job, reason):
    row = scoped(db, DataRun, job.data['query_id'], p, True)
    if row.status in LIVE:
        row.status = 'failed'; row.version += 1
        row.data = {**row.data, 'error': reason, 'progress': {'stage': 'failed', 'saved_at': now().isoformat()}}
    return row.status


def _origin_actor(db, p, contract):
    from .security import Principal
    from .relations import lock_current_actor
    lock_current_actor(db, Principal(p.user_id, p.workspace_id, p.role, contract['session_id']))


def cancel(db, p, task, run_id, body):
    _manage(db, p, task)
    entry = _journal(db, p, task, body.request_id)
    if entry:
        _check_command(entry, p, body, 'cancel', run_id=run_id)
        return scoped(db, DataRun, entry.data['run_id'], p)
    check_version(task, body.expected_version)
    row = scoped(db, DataRun, run_id, p, True)
    if row.data.get(MARKER, {}).get('task_id') != task.id:
        raise HTTPException(404, '未找到此任务的补充查询')
    if row.status in LIVE:
        row.status = 'cancelled'; row.version += 1
        row.data = {**row.data, 'cancelled_at': now().isoformat(), 'cancel_reason': body.reason}
        job = scoped(db, Job, row.data['native_query_job_id'], p, True)
        if job.status == 'queued':
            job.status = 'cancelled'
    _record(db, p, task, body, 'cancel', row.id)
    task.version += 1
    return row


def recover(db, p, task, body):
    _manage(db, p, task)
    original = body.original_request
    if body.operation == 'execute' and not isinstance(original, Execute):
        raise HTTPException(422, '请保留完整执行原请求')
    if body.operation == 'cancel' and (not body.run_id or isinstance(original, Execute)):
        raise HTTPException(422, '请保留取消目标和完整取消原请求')
    if body.operation == 'cancel':
        target=scoped(db,DataRun,body.run_id,p)
        if target.data.get(MARKER,{}).get('task_id')!=task.id:
            raise HTTPException(404,'未找到此任务的取消目标')
    entry = _journal(db, p, task, original.request_id)
    if entry:
        _check_command(entry, p, original, body.operation,
            run_id=body.run_id if body.operation=='cancel' else None, allow_abandoned=True)
        if entry.data.get('abandoned'):
            return {'status': 'abandoned', 'can_resend': False}
        return {'status': 'saved', 'run_id': entry.data['run_id'], 'can_resend': False}
    if body.action == 'abandon':
        _record(db, p, task, original, body.operation,
            run_id=body.run_id if body.operation=='cancel' else None, abandoned=True)
        return {'status': 'abandoned', 'can_resend': False}
    return {'status': 'not_found', 'can_resend': True}


def listing(db, p, task, *, limit=20, cursor=None):
    from .data_tools import frame
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=50:
        raise HTTPException(422,'每页查询记录须为1至50条')
    query=select(DataRun).where(DataRun.workspace_id == p.workspace_id,
        DataRun.data[MARKER]['task_id'].as_string() == task.id)
    if cursor:
        previous=scoped(db,DataRun,cursor,p)
        if previous.data.get(MARKER,{}).get('task_id')!=task.id:
            raise HTTPException(409,'历史翻页位置不属于原任务')
        query=query.where(or_(DataRun.created_at<previous.created_at,
            and_(DataRun.created_at==previous.created_at,DataRun.id<previous.id)))
    rows=list(db.scalars(query.order_by(DataRun.created_at.desc(),DataRun.id.desc()).limit(limit+1)))
    has_more=len(rows)>limit
    rows=rows[:limit]
    permission = {'can_execute': False, 'execute_reason': None}
    try:
        _guard(db, p, task)
        _budget(db, p, task)
        permission['can_execute'] = not bool(db.scalar(select(DataRun.id).where(
            DataRun.workspace_id==p.workspace_id,DataRun.data[MARKER]['task_id'].as_string()==task.id,
            DataRun.status.in_(LIVE)).limit(1)))
        if not permission['can_execute']:
            permission['execute_reason'] = '原任务已有补充查询在执行'
    except HTTPException as error:
        permission['execute_reason'] = str(error.detail)
    items = []
    for row in rows:
        valid = False; error = None
        if row.data.get('result'):
            try:
                valid = verify_result(db, p, task, row)
            except HTTPException as failure:
                error = str(failure.detail)
        result = copy.deepcopy(row.data.get('result'))
        selected = ((result or {}).get('derived_records', [])
            if row.data['request']['tool_steps'] else frame(result or {}))
        limits = row.data[MARKER].get('adoption', {}).get('limitations', [])
        items.append({'id': row.id, 'version': row.version,
            'created_at':row.created_at.isoformat(),
            'completed_at':(row.data.get('progress') or {}).get('saved_at') if row.status not in LIVE else None,
            'proposal_id': row.data[MARKER]['proposal_id'], 'status': row.status,
            'reference_only': True, 'question': row.question,
            'progress': row.data.get('progress'), 'error': row.data.get('error'),
            'current_valid': valid, 'scope_error': error, 'result': result,
            'result_summary': {'fields': row.data['request']['fields'], 'rows': selected,
                'record_count': len(selected), 'limitations': limits},
            'can_cancel': p.role in {'admin', 'operator'} and (p.role == 'admin' or task.owner_id == p.user_id)
                and row.status in LIVE})
    return {'task_id': task.id, 'task_version': task.version, 'items': items, 'permissions': permission,
        'has_more':has_more,'next_cursor':rows[-1].id if has_more else None}
