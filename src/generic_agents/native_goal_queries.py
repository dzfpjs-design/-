"""Durable local data derivation owned by the original configured Task.

Source collection stays in native_goal_query_id.  This module owns the separate
data_query_run_id; it never creates a Task/Plan or changes workflow progress.
"""
import copy
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, func, or_

from .contracts import digest
from .models import Task, DataRun, Job, Invocation, Entity, Observation, Relation, now
from .services import scoped, published, validate, audit, require, query_evidence

JOB_KIND = 'execute_native_goal_query'
LIVE = {'queued', 'running'}


def _raw(db, p, task, *, lock_sources=True):
    spec = task.data['goal_spec']
    from .data_tools import managed_include_trusted_cases
    include_cases=managed_include_trusted_cases(db,p,task,lock_sources=lock_sources)
    return query_evidence(db, p, spec['entity_ids'], spec['required_fields'], learning=False,
        requirement=spec['evidence'], fixed_window=task.data['goal_window'],
        relation_query=spec.get('relation_query'),**({'include_trusted_cases':False} if not include_cases else {}))


def _source_hash(evidence):
    from .business_tools import evidence_hash
    return evidence_hash({key: value for key, value in evidence.items()
        if key not in {'data_query', 'derived_records', 'tool_trace', 'query_tool_steps', 'derivation_verification'}})


def _relations(evidence):
    from .business_tools import relation_bindings
    return relation_bindings(evidence) if evidence.get('relations') else None


def _request(db, p, task, *, authorization_task=None,lock_sources=True):
    from .goal_queries import request_for
    from .data_tools import managed_task_kwargs
    return request_for(db,p,task,lock_sources=lock_sources,
        **({'authorization_task':authorization_task} if managed_task_kwargs(authorization_task) else {}))


def _ledgers(task):
    history = task.data.get('data_query_history', [])
    refreshes = task.data.get('native_goal_query_refreshes', {})
    if (not isinstance(history, list) or not isinstance(refreshes, dict)
        or 'native_goal_query_history_hash' in task.data
        and digest(history) != task.data['native_goal_query_history_hash']
        or 'native_goal_query_refreshes_hash' in task.data
        and digest(refreshes) != task.data['native_goal_query_refreshes_hash']):
        raise HTTPException(409, '原任务查询代际或重查历史摘要无法核对')
    return history, refreshes


def _remember(task, token, row):
    if token:
        _, refreshes = _ledgers(task)
        if token not in refreshes and len(refreshes) >= 100:
            raise HTTPException(409, '原任务查询重查次数已达保存上限，请保留历史')
        refreshes = {**refreshes, token: row.id}
        task.data = {**task.data, 'native_goal_query_refreshes': refreshes,
            'native_goal_query_refreshes_hash': digest(refreshes)}
    return row


def step_count(db, p, task):
    """Every actual query invocation counts, including older/failed generations."""
    actual = db.scalar(select(func.count()).select_from(Invocation).join(DataRun,
        Invocation.idempotency_key.like('data-tool:' + DataRun.id + ':%')).where(
            Invocation.workspace_id == p.workspace_id, DataRun.workspace_id == p.workspace_id,
            or_(DataRun.data['native_query_contract']['task_id'].as_string() == task.id,
                DataRun.data['native_task_query_contract']['task_id'].as_string() == task.id))) or 0
    from .goal_evaluation_append_store_v297 import additional_budget_calls
    return actual+additional_budget_calls(db,p,task)


def validate_spec(db, p, task, spec, *, lock_sources=True):
    """Validate fixed, locally executable data tools; no new execution grant."""
    query = spec.get('data_query')
    if not query:
        return
    if query.get('allow_external_tools'):
        raise HTTPException(422, '原业务查询目前只使用本机内置能力，尚未批准外部查询服务')
    from .native_query_catalog import catalog_for_task, output_schema_for_steps
    from .data_tools import validate_manifest, validate_steps, HANDLERS,managed_task_kwargs
    available = {cap.id: cap for cap in catalog_for_task(db, p, task,lock_sources=lock_sources)}
    for step in query['tool_steps']:
        cap = available.get(step['capability_id'])
        if not cap:
            raise HTTPException(422, '查询步骤只能使用原业务生成版本固定的本机数据能力')
        validate_manifest(cap.data)
        execution = cap.data['execution']
        if (execution.get('adapter') != 'builtin' or execution.get('handler') not in HANDLERS
            or execution.get('side_effect') is not False or cap.content_hash != digest(cap.data)):
            raise HTTPException(422, '原业务查询能力须为版本完整且明确只读的本机内置能力')
    from .temporal import resolve
    temporary = SimpleNamespace(data={**task.data, 'goal_spec': spec,
        'goal_window': task.data.get('goal_window') or resolve(spec['evidence']['time'])})
    request = _request(db,p,temporary,lock_sources=lock_sources,
        **({'authorization_task':task} if managed_task_kwargs(task) else {}))
    if request.get('clarifications'):
        raise HTTPException(422, '；'.join(request['clarifications']))
    validate_steps(db,p,request,query['tool_steps'],lock_sources=lock_sources,**managed_task_kwargs(task))
    output_schema_for_steps(db, p, task, spec,lock_sources=lock_sources)


def _contract(task, request, evidence, capabilities):
    from .goals import snapshot
    return {'schema': 'native-data-query-v1', 'task_id': task.id,
        'owner_id': task.data['native_goal_context']['actor_id'],
        'context_hash': task.data['native_goal_context_hash'], 'goal_snapshot': snapshot(task),
        'request': copy.deepcopy(request), 'source_hash': _source_hash(evidence),
        'capabilities': {step['capability_id']: capabilities[step['capability_id']].content_hash
            for step in request['tool_steps']},
        **({'object_definition_bindings_hash':task.data['object_definition_bindings_hash']}
            if 'object_definition_bindings' in task.data or 'object_definition_bindings_hash' in task.data else {})}


def _linked(db, p, task, row, *, lock=False,lock_sources=True):
    """A DataRun cannot be reassigned to another goal, actor or request."""
    _ledgers(task)
    from .goals import snapshot
    contract = row.data.get('native_query_contract')
    if (not contract or digest(contract) != row.data.get('native_query_contract_hash')
        or contract['task_id'] != task.id or contract['owner_id'] != row.owner_id
        or row.owner_id != task.data['native_goal_context']['actor_id']
        or contract['context_hash'] != task.data['native_goal_context_hash']
        or contract.get('object_definition_bindings_hash') != task.data.get('object_definition_bindings_hash')
        or contract['goal_snapshot'] != snapshot(task)
        or row.data.get('native_goal_task_id') != task.id
        or row.data.get('native_goal_context_hash') != contract['context_hash']
        or row.data.get('goal_delegation') != {'task_id': task.id, 'goal_snapshot': snapshot(task)}
        or row.data.get('request') != contract['request']
        or row.data.get('native_goal_request_hash') != digest(contract['request'])
        or row.data['request'] != _request(db, p, task,lock_sources=lock_sources)):
        raise HTTPException(409, '原任务委托查询、来源范围或固定版本无法核对，旧回执保留')
    from .native_query_catalog import catalog_for_task
    capabilities = {cap.id: cap for cap in catalog_for_task(db, p, task,lock_sources=lock_sources)}
    for identity, signature in contract['capabilities'].items():
        cap = capabilities.get(identity)
        if not cap or cap.content_hash != signature or digest(cap.data) != signature:
            raise HTTPException(409, '原任务查询能力的固定版本已经改变')
    validate_spec(db, p, task, task.data['goal_spec'],lock_sources=lock_sources)
    return contract, capabilities


def ensure(db, p, task, *, refresh=False):
    """Create the first query once; only an explicit new check may refresh it.

    A request UUID passed as refresh is recorded so retrying the same check cannot
    create another generation after a failed worker. Automatic checks pass False.
    """
    if not task.data['goal_spec'].get('data_query'):
        return None
    from .native_goals import guard
    from .native_query_catalog import catalog_for_task
    from .data_gaps import receipts_ready
    from .goal_queries import call_count
    require(p, 'admin', 'operator'); guard(db, p, task)
    validate_spec(db, p, task, task.data['goal_spec'])
    history, refreshes = _ledgers(task)
    old_id = task.data.get('data_query_run_id')
    old = scoped(db, DataRun, old_id, p, True) if old_id else None
    if old:
        _linked(db, p, task, old)
    token = refresh if isinstance(refresh, str) else None
    if token and token in refreshes:
        return scoped(db, DataRun, refreshes[token], p)
    base = _raw(db, p, task)
    if base['status'] != 'sufficient' or not receipts_ready(db, p, {'result': base}):
        return _remember(task, token, old) if old else None
    if old:
        if old.status in LIVE:
            unchanged = old.data['native_query_contract']['source_hash'] == _source_hash(base)
            if old.status == 'running' or unchanged or not refresh:
                return _remember(task, token, old)
            old.status = 'cancelled'; old.version += 1
            old.data = {**old.data, 'cancelled_at': now().isoformat(),
                'error': '明确重查已变化来源，原排队查询作废且历史保留'}
            old_job = scoped(db, Job, old.data['native_query_job_id'], p, True)
            if old_job.status == 'queued':
                old_job.status = 'cancelled'; old_job.data = {**old_job.data,
                    'cancel_reason': '原任务明确重新核对已变化来源'}
        if old.status == 'sufficient':
            try:
                gate(db, p, task, base)
                return _remember(task, token, old)
            except HTTPException:
                pass
        if not refresh:
            return old
    request = _request(db, p, task)
    if call_count(db, p, task) + len(request['tool_steps']) > task.data['goal_spec']['max_tool_calls']:
        raise HTTPException(409, '原任务剩余累计调用预算不足以重查，已保存结果保留')
    from .native_step_execution import step_count as execution_steps
    used = len(task.data['workflow_runtime']['receipts']) + execution_steps(task) + step_count(db, p, task)
    if used + len(request['tool_steps']) + 1 > task.data['goal_spec']['max_steps']:
        raise HTTPException(409, '原任务剩余步骤预算不足以查询并保留人工完成步骤，旧结果保留')
    capabilities = {cap.id: cap for cap in catalog_for_task(db, p, task)}
    contract = _contract(task, request, base, capabilities)
    row = DataRun(workspace_id=p.workspace_id, owner_id=contract['owner_id'],
        question=task.data['goal_spec']['purpose'], status='queued', data={
            'request': request, 'goal_delegation': {'task_id': task.id, 'goal_snapshot': contract['goal_snapshot']},
            'native_goal_task_id': task.id, 'native_goal_context_hash': contract['context_hash'],
            'native_goal_request_hash': digest(request), 'native_query_contract': contract,
            'native_query_contract_hash': digest(contract), 'progress': {'stage': 'queued', 'saved_at': now().isoformat()}})
    db.add(row); db.flush()
    job = Job(workspace_id=p.workspace_id, kind=JOB_KIND, idempotency_key='native-query:' + row.id,
        data={'task_id': task.id, 'query_id': row.id, 'actor_id': row.owner_id,
            'context_hash': contract['context_hash'], 'contract_hash': digest(contract)})
    db.add(job); db.flush()
    row.data = {**row.data, 'native_query_job_id': job.id}
    if old:
        history = [*history, {'query_id': old.id, 'status': old.status,
            'result_hash': old.data.get('goal_result_hash'), 'superseded_at': now().isoformat()}]
    if token:
        if len(refreshes) >= 100:
            raise HTTPException(409, '原任务查询重查次数已达保存上限，请保留历史')
        refreshes = {**refreshes, token: row.id}
    task.data = {**task.data, 'data_query_run_id': row.id, 'data_query_history': history,
        'native_goal_query_history_hash': digest(history), 'native_goal_query_refreshes': refreshes,
        'native_goal_query_refreshes_hash': digest(refreshes)}
    audit(db, p, 'native_goal.data_delegated', task.id, {'query_id': row.id, 'job_id': job.id,
        'previous_query_id': old.id if old else None})
    return row


def _inputs(request, cap, step, records, key):
    bound = {'data_records': records, 'data_question': request['question'],
        'data_scope': {'entity_ids': request['entity_ids'], 'fields': request['fields'],
            'evidence_requirement': request.get('evidence_requirement')}, 'request_key': key}
    return {**step['parameters'], **{k: bound[v] for k, v in cap.data['execution']['input_bindings'].items()}}


def _receipt(db, p, row, cap, step, item, records, *, lock=False):
    from .data_tools import builtin, check_output
    key = 'data-tool:' + row.id + ':' + step['id']
    inputs = _inputs(row.data['request'], cap, step, records, key)
    call = scoped(db, Invocation, item.get('invocation_id'), p, lock)
    output = item.get('output')
    if (item.get('step') != step or item.get('capability_id') != cap.id
        or item.get('capability_hash') != cap.content_hash or item.get('release_version') != cap.release_version
        or item.get('inputs') != inputs or item.get('input_hash') != digest(inputs)
        or item.get('status') != 'succeeded' or call.status != 'succeeded'
        or call.capability_id != cap.id or call.idempotency_key != key
        or call.data.get('role') != 'data' or call.data.get('input_hash') != digest(inputs)
        or call.data.get('capability_hash') != cap.content_hash or call.data.get('output') != output
        or call.data.get('job_id') != row.data['native_query_job_id']):
        raise HTTPException(409, '原目标查询调用、角色、输入或保存回执不一致，不能采纳')
    from .temporal import instant
    started = instant(call.data.get('started_at'))
    finished = instant(call.data.get('finished_at'))
    context_start = instant(row.data.get('tool_context', {}).get('started_at'))
    if (started < context_start or finished < started
        or finished > context_start + timedelta(seconds=row.data['request']['max_tool_seconds'])
        or call.data.get('duration_ms') != max(0, round((finished - started).total_seconds() * 1000))):
        raise HTTPException(409, '原目标查询回执已超过固定时间预算或执行时间无法核对，旧回执保留')
    validate(cap.data['input_schema'], inputs); validate(cap.data['output_schema'], output)
    check_output(output, records)
    if digest(output) != digest(builtin(cap.data['execution']['handler'], inputs)):
        raise HTTPException(409, '原目标查询的确定性回执与原输入重算不一致')
    return output['records']


def applies(db, p, key):
    if not isinstance(key, str) or not key.startswith('data-tool:'):
        return False
    parts = key.split(':')
    if len(parts) != 3:
        return False
    row = db.scalar(select(DataRun).where(DataRun.workspace_id == p.workspace_id, DataRun.id == parts[1]))
    return bool(row and row.data.get('native_query_contract'))


def before_dispatch(db, p, cap, inputs, key):
    """Gateway fence after committed intent, immediately before local execution."""
    if not key.startswith('data-tool:'):
        return False
    parts = key.split(':')
    if len(parts) != 3:
        return False
    row = scoped(db, DataRun, parts[1], p)
    if not row.data.get('native_query_contract'):
        return False
    task = scoped(db, Task, row.data['native_query_contract']['task_id'], p, True)
    row = scoped(db, DataRun, row.id, p, True)
    from .native_goals import guard
    from .goal_queries import call_count
    from .native_step_execution import step_count as execution_steps
    from .data_tools import frame
    require(p, 'admin', 'operator'); guard(db, p, task, key)
    contract, capabilities = _linked(db, p, task, row)
    if (row.status not in LIVE or task.data.get('data_query_run_id') != row.id
        or p.user_id != row.owner_id
        or db.info.get('worker_lease', {}).get('job_id') != row.data['native_query_job_id']):
        raise HTTPException(409, '原任务查询已停止、替换或不属于当前后台执行者')
    spec = task.data['goal_spec']
    if call_count(db, p, task) > spec['max_tool_calls']:
        raise HTTPException(409, '原任务累计能力调用预算已用尽')
    used = len(task.data['workflow_runtime']['receipts']) + execution_steps(task) + step_count(db, p, task)
    reserve = int(task.status != 'workflow_completed')
    if used + reserve > spec['max_steps']:
        raise HTTPException(409, '原任务累计步骤预算已用尽，已保存调用回执保留')
    base = _raw(db, p, task)
    if base['status'] != 'sufficient' or _source_hash(base) != contract['source_hash']:
        raise HTTPException(409, '本机查询执行前原资料或关系依据已变化')
    frames = {'evidence': frame(base)}
    traces = row.data.get('tool_trace', [])
    for index, step in enumerate(row.data['request']['tool_steps']):
        if step['id'] == parts[2]:
            expected = _inputs(row.data['request'], capabilities[step['capability_id']], step,
                frames[step['input_from']], key)
            if cap.id != step['capability_id'] or inputs != expected or len(traces) != index + 1:
                raise HTTPException(409, '原任务查询执行输入不属于当前固定步骤与来源链')
            return True
        if index >= len(traces):
            raise HTTPException(409, '原任务查询缺少前序已保存成功回执')
        frames[step['id']] = _receipt(db, p, row, capabilities[step['capability_id']], step,
            traces[index], frames[step['input_from']])
    raise HTTPException(409, '原任务查询执行编号没有对应已确认步骤')


def _wake(db, p, task, row):
    from .native_goals import queue
    if (task.data.get('data_query_run_id') == row.id and not task.data.get('native_goal_cancelled')
        and not task.data.get('native_goal_acceptance') and not task.data.get('native_goal_timed_out')
        and task.status != 'workflow_cancelled'):
        queue(db, p, task, 'data-query:' + row.id + ':' + str(row.version))


def process(db, p, job):
    require(p, 'admin', 'operator')
    task = scoped(db, Task, job.data['task_id'], p, True)
    row = scoped(db, DataRun, job.data['query_id'], p, True)
    contract, _ = _linked(db, p, task, row)
    if (job.kind != JOB_KIND or job.data.get('actor_id') != row.owner_id
        or job.id != row.data.get('native_query_job_id')
        or job.data.get('context_hash') != contract['context_hash']
        or job.data.get('contract_hash') != digest(contract)):
        raise HTTPException(409, '原任务后台查询与持久委托不匹配')
    if task.data.get('data_query_run_id') != row.id or row.status == 'cancelled':
        return 'cancelled'
    if row.status not in LIVE:
        _wake(db, p, task, row)
        return row.status
    from .native_goals import guard
    guard(db, p, task)
    _ledgers(task)
    if contract['source_hash'] != _source_hash(_raw(db, p, task)):
        row.status = 'blocked_evidence'; row.version += 1
        row.data = {**row.data, 'error': '排队期间原资料或关系依据已变化，请明确重新核对查询'}
        _wake(db, p, task, row)
        return row.status
    row.status = 'running'; row.version += 1
    row.data = {**row.data, 'progress': {'stage': 'running', 'saved_at': now().isoformat()}}
    from .leases import checkpoint
    checkpoint(db)
    from .agents import data_agent
    cached_only = bool(job.data.get('recovery_only') or job.data.get('lease_recoveries'))
    from .data_tools import managed_task_kwargs
    state = data_agent(db, p, row.data['request'], row.id, cached_only=cached_only,
        emit_gaps=False, learning=False,**managed_task_kwargs(task))
    task = scoped(db, Task, task.id, p, True); row = scoped(db, DataRun, row.id, p, True)
    result = state.get('result')
    try:
        guard(db, p, task)
        _linked(db, p, task, row)
        if task.data.get('data_query_run_id') != row.id or row.status == 'cancelled':
            raise HTTPException(409, '原查询已停止或已由明确重查替换')
    except HTTPException as error:
        row.data = {**row.data, 'late_result': copy.deepcopy(state), 'late_result_hash': digest(state),
            'error': str(error.detail), 'progress': {'stage': row.status, 'saved_at': now().isoformat()}}
        if row.status in LIVE:
            row.status = 'cancelled'
        row.version += 1
        return row.status
    row.status = state['status']; row.version += 1
    row.data = {**row.data, **state, 'goal_result_hash': digest(result) if result else None,
        'progress': {'stage': row.status, 'saved_at': now().isoformat()},
        'error': state.get('error') or (result or {}).get('tool_blocker')}
    if row.status == 'sufficient':
        try:
            gate(db, p, task, _raw(db, p, task))
        except HTTPException as error:
            row.status = 'blocked_evidence'; row.version += 1
            row.data = {**row.data, 'error': str(error.detail)}
    audit(db, p, 'native_goal.data_completed', task.id, {'query_id': row.id, 'status': row.status})
    _wake(db, p, task, row)
    return row.status


def gate(db, p, task, base=None, *, lock=False,lock_sources=True):
    """Replay deterministic receipt provenance; this never invokes a capability."""
    from .native_goals import guard
    from .data_tools import frame, validate_steps
    from .data_gaps import receipts_ready
    guard(db, p, task,lock_sources=lock_sources)
    identifier = task.data.get('data_query_run_id')
    if not identifier:
        raise HTTPException(409, '原目标委托查询尚未取得已保存结果')
    row = scoped(db, DataRun, identifier, p, lock)
    contract, capabilities = _linked(db, p, task, row, lock=lock,lock_sources=lock_sources)
    result = row.data.get('result')
    if (row.status != 'sufficient' or not result or result.get('status') != 'sufficient'
        or digest(result) != row.data.get('goal_result_hash')):
        raise HTTPException(409, row.data.get('error') or '原目标查询尚未完成或结果摘要无法核对')
    if lock:
        for identity in sorted(task.data['entity_ids']):
            scoped(db, Entity, identity, p, True)
        for item in result.get('observation_evidence', []):
            scoped(db, Observation, item['observation_id'], p, True)
        for edge in result.get('relations', {}).get('relation_evidence', []):
            scoped(db, Relation, edge['relation_id'], p, True)
        from .models import SetupMaterial
        for identity in sorted({ref['material_id'] for edge in result.get('relations',{}).get('relation_evidence',[])
            for ref in edge.get('document_evidence',[])}):
            scoped(db,SetupMaterial,identity,p,True)
        base = _raw(db, p, task,lock_sources=lock_sources)
    else:
        base = base if base is not None else _raw(db, p, task,lock_sources=lock_sources)
    if (base['status'] != 'sufficient' or not receipts_ready(db, p, {'result': base})
        or contract['source_hash'] != _source_hash(base)
        or frame(result) != frame(base) or _relations(result) != _relations(base)):
        raise HTTPException(409, '原目标查询所用资料、核查版本或关系依据已变化，请明确重新核对')
    from .data_tools import managed_task_kwargs
    request = row.data['request']; steps = validate_steps(db,p,request,request['tool_steps'],
        lock_sources=lock_sources,**managed_task_kwargs(task))
    context = row.data.get('tool_context', {})
    expected_signature = digest({'records': frame(base), 'steps': steps,
        'capabilities': {s['capability_id']: capabilities[s['capability_id']].content_hash for s in steps},
        'requirement': request.get('evidence_requirement'), 'relations': _relations(base)})
    if (context.get('source_records') != frame(base) or context.get('steps') != steps
        or context.get('signature') != expected_signature
        or result.get('query_tool_steps') != steps
        or result.get('derivation_verification') != 'deterministic_registered_computation'):
        raise HTTPException(409, '原目标查询的固定输入、步骤或计算核验方式无法核对')
    traces = result.get('tool_trace', [])
    if len(traces) != len(steps) or traces != row.data.get('tool_trace'):
        raise HTTPException(409, '原目标查询缺少完整的逐步持久回执')
    frames = {'evidence': frame(base)}
    for step, item in zip(steps, traces):
        cap = published(db, p, step['capability_id'])
        if lock:
            from .models import Capability
            cap = scoped(db, Capability, cap.id, p, True)
        records = frames[step['input_from']]
        frames[step['id']] = _receipt(db, p, row, cap, step, item, records, lock=lock)
    if not steps or not frames[steps[-1]['id']] or result.get('derived_records') != frames[steps[-1]['id']]:
        raise HTTPException(409, '原目标查询最终派生结果与完整来源链不一致')
    return row


def read(db, p, task, base, *, lock=False,lock_sources=True):
    """Adopt verified saved derivation; reading cannot queue, invoke or charge."""
    if not task.data.get('goal_spec', {}).get('data_query'):
        return base
    identifier = task.data.get('data_query_run_id')
    summary = {'id': identifier, 'status': 'not_requested'}
    try:
        row = gate(db, p, task, base, lock=lock,lock_sources=lock_sources)
        result = row.data['result']
        adopted = {key: copy.deepcopy(result[key]) for key in
            ['derived_records', 'tool_trace', 'derivation_verification', 'query_tool_steps']}
        return {**base, **adopted, 'data_query': {**summary, 'status': row.status,
            'reason': None, 'version': row.version, 'result_hash': row.data['goal_result_hash']}}
    except HTTPException as error:
        if identifier:
            row = scoped(db, DataRun, identifier, p)
            summary['status'] = row.status
        status = base['status'] if base['status'] != 'sufficient' else (
            summary['status'] if summary['status'] in LIVE else 'blocked_evidence')
        return {**base, 'status': status, 'data_query': {**summary, 'reason': str(error.detail)}}


def stop_all(db, p, task, reason):
    rows = db.scalars(select(DataRun).where(DataRun.workspace_id == p.workspace_id,
        DataRun.data['native_query_contract']['task_id'].as_string() == task.id).with_for_update()).all()
    for row in rows:
        if row.status not in LIVE:
            continue
        row.status = 'cancelled'; row.version += 1
        row.data = {**row.data, 'cancelled_at': now().isoformat(), 'error': reason,
            'cancel_reason': reason, 'progress': {'stage': 'cancelled', 'saved_at': now().isoformat()}}
        job = scoped(db, Job, row.data['native_query_job_id'], p, True)
        if job.status == 'queued':
            job.status = 'cancelled'; job.data = {**job.data, 'cancel_reason': reason}


def recover_job(db, p, job, reason):
    task = scoped(db, Task, job.data['task_id'], p, True)
    row = scoped(db, DataRun, job.data['query_id'], p, True)
    if (row.data.get('native_query_job_id') != job.id
        or row.data.get('native_query_contract', {}).get('task_id') != task.id):
        raise HTTPException(409, '后台恢复记录不属于原任务查询')
    if row.status in LIVE:
        row.status = 'failed'; row.version += 1
        row.data = {**row.data, 'error': reason,
            'progress': {'stage': 'failed', 'saved_at': now().isoformat()}, 'recovery_requires_explicit_check': True}
        _wake(db, p, task, row)


def worker_view(db, p, task, row, *, historical=False, scope_error=None):
    """Read a precisely linked Job and the database clock; never recover it.

    A persisted running flag is not proof of a live worker. Tokens are private;
    only verified job identity, lease deadline and recovery state reach the UI.
    """
    unknown = {'state': 'unverified', 'job_id': None, 'job_status': None,
        'lease_until': None, 'database_now': None, 'attempts': None,
        'lease_recoveries': None, 'recovery_only': None,
        'reason': '后台任务归属或租约资料无法核对，原回执保留'}
    contract = row.data.get('native_query_contract')
    context = task.data.get('native_goal_context')
    if not isinstance(contract, dict) or not isinstance(context, dict):
        return unknown
    signature = digest(contract)
    if (row.workspace_id != p.workspace_id or task.workspace_id != p.workspace_id
        or row.data.get('native_query_contract_hash') != signature
        or contract.get('task_id') != task.id or contract.get('owner_id') != row.owner_id
        or row.owner_id != context.get('actor_id')
        or contract.get('context_hash') != digest(context)
        or task.data.get('native_goal_context_hash') != digest(context)
        or row.data.get('native_goal_context_hash') != digest(context)
        or contract.get('request') != row.data.get('request')
        or row.data.get('native_goal_request_hash') != digest(row.data.get('request'))):
        return unknown
    identity = row.data.get('native_query_job_id')
    if not isinstance(identity, str) or not identity:
        return unknown
    job = db.scalar(select(Job).where(Job.id == identity, Job.workspace_id == p.workspace_id)
        .execution_options(populate_existing=True))
    if (not job or job.id != identity or job.workspace_id != p.workspace_id or job.kind != JOB_KIND
        or not isinstance(job.data, dict)
        or job.idempotency_key != 'native-query:' + row.id
        or job.data.get('task_id') != task.id or job.data.get('query_id') != row.id
        or job.data.get('actor_id') != row.owner_id
        or job.data.get('context_hash') != contract['context_hash']
        or job.data.get('contract_hash') != signature
        or job.status not in {'queued', 'running', 'succeeded', 'failed', 'cancelled'}):
        return unknown
    recoveries = job.data.get('lease_recoveries', 0)
    recovery_only = job.data.get('recovery_only', False)
    if (type(job.attempts) is not int or job.attempts < 0
        or type(recoveries) is not int or recoveries < 0 or type(recovery_only) is not bool):
        return unknown
    clock = db.scalar(select(func.clock_timestamp()))
    if (not isinstance(clock, datetime) or clock.tzinfo is None
        or job.lease_until is not None and (not isinstance(job.lease_until, datetime) or job.lease_until.tzinfo is None)):
        return unknown
    state, reason = job.status, None
    if row.status == 'cancelled' or task.data.get('native_goal_cancelled') or task.status == 'workflow_cancelled':
        state, reason = 'cancelled', '查询已停止；已开始调用的旧回执仍可能到达并作为历史保留'
    elif scope_error or task.data.get('native_goal_timed_out'):
        state, reason = 'stale', scope_error or '目标已到截止时间，旧执行不再作为当前结果'
    elif job.status == 'running':
        if job.lease_until is None or job.lease_until <= clock:
            state, reason = 'waiting_recovery', '执行租约已失效，等待恢复处理器领取；本页不会重新执行任务'
        else:
            try:
                token_valid = isinstance(job.lease_token, str) and str(UUID(job.lease_token)) == job.lease_token
            except (ValueError, TypeError, AttributeError):
                token_valid = False
            if not token_valid:
                state, reason = 'stale', '执行租约缺少有效归属，不能确认处理器仍在运行'
    return {'state': state, 'job_id': job.id, 'job_status': job.status,
        'lease_until': job.lease_until.isoformat() if job.lease_until else None,
        'database_now': clock.isoformat(), 'attempts': job.attempts,
        'lease_recoveries': recoveries, 'recovery_only': recovery_only,
        'historical': historical, 'reason': reason}


def view(db, p, task):
    """Task-centered saved status and complete local receipts, without dispatch."""
    identifier = task.data.get('data_query_run_id')
    history, _ = _ledgers(task)
    if not identifier:
        return {'id': None, 'status': 'not_requested', 'history': history,
            'reason': '原资料充分并完成归集后，后台执行已确认的本机查询'}
    row = scoped(db, DataRun, identifier, p)
    scope_error = None
    historical = bool(task.data.get('native_goal_acceptance') or task.data.get('native_goal_cancelled')
        or task.data.get('native_goal_timed_out') or task.status == 'workflow_cancelled')
    current_valid = False
    try:
        _linked(db, p, task, row,lock_sources=False)
        if not historical:
            from .native_goals import guard
            guard(db, p, task,lock_sources=False)
            base = _raw(db, p, task,lock_sources=False)
            if row.data['native_query_contract']['source_hash'] != _source_hash(base):
                raise HTTPException(409, '原目标查询所用资料或关系依据已变化，旧结果作为历史保留')
            if row.status == 'sufficient':
                gate(db, p, task, base,lock_sources=False)
                current_valid = True
    except HTTPException as error:
        scope_error = str(error.detail)
    result = row.data.get('result') or {}
    return {'id': row.id, 'version': row.version, 'status': row.status,
        'reason': scope_error or row.data.get('error') or result.get('tool_blocker'),
        'scope_error': scope_error, 'current_valid': current_valid, 'historical': historical,
        'progress': copy.deepcopy(row.data.get('progress')), 'history': copy.deepcopy(history),
        'request': copy.deepcopy(row.data['request']), 'result_hash': row.data.get('goal_result_hash'),
        'derived_records': copy.deepcopy(result.get('derived_records', [])),
        'tool_trace': copy.deepcopy(row.data.get('tool_trace', [])),
        'result': copy.deepcopy(result),
        'late_result_preserved': bool(row.data.get('late_result')),
        'worker': worker_view(db, p, task, row, historical=historical, scope_error=scope_error),
        'local_only': True, 'new_model_inferences': 0}
