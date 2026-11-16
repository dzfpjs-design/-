"""One explicitly approved read-only run inside the original human workflow Task.

Plan content is immutable after approval. Mutable progress belongs exclusively to a
sealed Task ledger; no legacy planner state or workflow transition is written here.
"""
import copy
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, StrictBool, StrictInt, ValidationError
from sqlalchemy import select

from .contracts import Contract, digest
from .models import Capability, Invocation, Job, Membership, Plan, Task, User, now
from .services import audit, check_version, published, scoped, validate
from .security import require
from . import native_step_planning as planning
from .business_tools import evidence_hash

JOB_KIND = 'execute_native_step_plan'
STATE = 'native_step_execution'
SCHEMA = 'native-step-execution-v1'
ACTIVE = {'queued', 'running'}
SETTLED = {'accepted', 'rejected', 'cancelled'}
NOTICE = ('仅执行下面固定输入的已登记只读工具；HTTP/MCP会向所列服务发送完整输入，'
          '可能产生服务费用。不会调用云模型、推进人工流程或验收目标。停止不能撤回已发送请求；'
          '未知或中断结果不会自动重试。请逐项核对结果后明确采用或拒绝。')


class Command(Contract):
    request_id: UUID
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class Approve(Command):
    expected_plan_version: StrictInt = Field(ge=1)
    approval_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    authorize_external_inputs: StrictBool = False


class RunCommand(Command):
    expected_execution_version: StrictInt = Field(ge=1)


class Retry(RunCommand):
    acknowledge_possible_charge: Literal[True]


class Review(RunCommand):
    decision: Literal['accepted', 'rejected']
    result_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class Recovery(Contract):
    operation: Literal['approve', 'retry', 'stop', 'review']
    original_request: dict
    action: Literal['probe', 'abandon'] = 'probe'
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


def fail(reason, code=409):
    raise HTTPException(code, reason)


def _body(body):
    return body.model_dump(mode='json')


def _ledger(task):
    value = copy.deepcopy(task.data.get(STATE, {'schema': SCHEMA, 'runs': {}, 'commands': []}))
    seal = value.pop('ledger_hash', None)
    if (STATE in task.data and (not seal or seal != digest(value))) or value.get('schema') != SCHEMA:
        fail('原任务执行记录摘要无法核对，历史保留')
    return value


def _save(task, ledger):
    value = copy.deepcopy(ledger)
    value.pop('ledger_hash', None)
    value['ledger_hash'] = digest(value)
    task.data = {**task.data, STATE: value}
    task.version += 1


def _run(task, plan_id):
    return _ledger(task)['runs'].get(plan_id)


def _write(task, run):
    value = _ledger(task)
    run = copy.deepcopy(run)
    run['version'] += 1
    value['runs'][run['id']] = run
    _save(task, value)
    return run


def step_count(task):
    from .workflow_graph_goal_guard_v133 import pending_steps
    return sum(len(r.get('started_steps', [])) for r in _ledger(task)['runs'].values()) + pending_steps(task)


def _member(db, p):
    require(p, 'admin', 'operator')
    user = db.get(User, p.user_id)
    member = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
        Membership.user_id == p.user_id))
    if not user or not user.active or not member or member.role != p.role:
        fail('当前执行账号权限已撤回或变化', 403)


def _owner(db, p, task, contract=None, *, recovery=False):
    _member(db, p)
    if recovery:
        run = _run(task, contract['plan_id']) if contract else None
        if run and p.user_id != run['actor_id'] and p.role != 'admin':
            fail('请由原执行提交者或管理员停止或恢复原记录', 403)
        if not run and p.role != 'admin':
            from .workspace_api import saved_execution
            _, release, business = saved_execution(db, p, task.id)
            current_id = contract['context']['step_id'] if contract else task.data['workflow_runtime']['current_step_id']
            step = next((s for s in business['flow']['steps'] if s['id'] == current_id), None)
            if not step or release.data['snapshot']['roles'][step['role']]['user_id'] != p.user_id:
                fail('请由原请求提交者或管理员恢复执行请求', 403)
        return
    from .workspace_api import saved_execution
    _, release, business = saved_execution(db, p, task.id)
    step_id = contract['context']['step_id'] if contract else task.data['workflow_runtime']['current_step_id']
    step = next((s for s in business['flow']['steps'] if s['id'] == step_id), None)
    if not step or step['agent'] != '人工确认':
        fail('原执行未绑定人工步骤')
    if p.role != 'admin' and release.data['snapshot']['roles'][step['role']]['user_id'] != p.user_id:
        fail('请由原步骤负责人或管理员核对和采用结果', 403)


def _context(db, p, task, *, contract=None, active=True):
    """Model-free fixed basis; historical checks tolerate later human flow progress."""
    from .workspace_api import saved_execution
    from .native_goals import guard, saved_context, verify_acceptance_inputs
    from .native_planning_grants import eligible, pin
    from .goals import query_goal, snapshot
    saved_context(db, p, task)
    if active:
        guard(db, p, task)
    _, release, business = saved_execution(db, p, task.id)
    if active and task.status != 'workflow_waiting':
        fail('执行只属于原任务当前等待的人工步骤')
    step_id = contract['context']['step_id'] if contract else task.data['workflow_runtime']['current_step_id']
    step = next((s for s in business['flow']['steps'] if s['id'] == step_id), None)
    if not step or step['agent'] != '人工确认':
        fail('原人工步骤不存在或已变化')
    if active and task.data['workflow_runtime']['current_step_id'] != step_id:
        fail('人工流程已进入其他步骤，不执行旧计划')
    package = published(db, p, task.package_id, 'domain_package')
    expected = step.get('planning_tools', [])
    if expected != package.data['specification'].get('planning_grants', {}).get(business['id'], {}).get(step_id, []):
        fail('原人工步骤工具授权与固定业务包不一致')
    actual = pin(db, p, [{'capability_id': g['capability_id'], 'allow_external': g['allow_external']} for g in expected])
    if actual != expected:
        fail('原工具版本或外发授权已变化')
    allowed = set(package.data['specification'].get('business_tool_ids', [])) & set(package.data['dependencies'])
    if {g['capability_id'] for g in actual} - allowed:
        fail('原工具不在固定包授权内')
    tools = [published(db, p, g['capability_id']) for g in actual]
    for cap in tools:
        eligible(cap)
    verify_acceptance_inputs(db, p, task)
    evidence = query_goal(db, p, task, learning=False)
    if evidence.get('conflicts') or evidence['status'] == 'conflict':
        fail('当前原目标证据存在冲突')
    goal = snapshot(task)
    if (goal['spec'].get('data_query') or goal['spec'].get('relation_query')) and evidence['status']!='sufficient':
        fail('原目标查询或关系依据未通过当前来源核对，不能执行旧候选')
    evidence = {k: copy.deepcopy(v) for k, v in evidence.items()
        if k not in {'evaluated_at', 'resolved_time', 'learning_decisions'}}
    evidence['resolved_time'] = copy.deepcopy(goal['time'])
    receipts = task.data['workflow_runtime']['receipts']
    prefix_len = contract['context']['workflow_prefix_length'] if contract else len(receipts)
    if len(receipts) < prefix_len or active and len(receipts) != prefix_len:
        fail('原人工流程前缀已变化')
    context = {'task_id': task.id, 'owner_id': task.owner_id,
        'instance_id': task.data['instance_id'], 'release_id': release.id,
        'configuration_hash': release.configuration_hash, 'package_id': package.id,
        'package_hash': package.content_hash, 'business_id': business['id'],
        'step_id': step_id, 'step_name': step['name'], 'step_hash': digest(step),
        'workflow_prefix_length': prefix_len, 'workflow_prefix_hash': digest(receipts[:prefix_len]),
        'goal_snapshot': goal, 'native_goal_context_hash': task.data['native_goal_context_hash'],
        'inputs_hash': digest({k: task.data.get(k) for k in ['object_snapshots', 'material_snapshots',
            'image_snapshots', 'scene_snapshots', 'comparison_scene_snapshots', 'audio_snapshots',
            'perception_event', 'perception_signal']}),
        'evidence_hash': evidence_hash(evidence), 'evidence_status': evidence['status'],
        'observation_sources': planning._observation_sources(db, p, evidence),
        'tools': [{'id': c.id, 'hash': c.content_hash, 'version': c.release_version} for c in tools]}
    from .business_tools import object_definition_pin
    context.update(object_definition_pin(task))
    return context, evidence, tools


def _contract(db, p, task, plan, *, active=True):
    contract = copy.deepcopy(plan.data.get('execution_contract'))
    if not contract or contract.get('schema') != SCHEMA or contract['task_id'] != task.id or contract['plan_id'] != plan.id:
        fail('原执行批准合同不存在或关联错误')
    if plan.approved_hash != digest(contract):
        fail('原执行批准合同摘要无法核对')
    original = {k: v for k, v in plan.data.items() if k not in {'execution_contract', 'record_hash'}}
    if digest(original) != contract['candidate_record_hash'] or digest(plan.data.get('proposal')) != contract['proposal_hash']:
        fail('原云候选、原答或冻结资料与批准来源不一致')
    if plan.approved_by != contract['actor_id']:
        fail('原执行批准人关联无法核对')
    context, _, _ = _context(db, p, task, contract=contract, active=active)
    if context != contract['context']:
        fail('原执行步骤、目标、输入、来源或工具授权已变化')
    if active and planning._state(task).get('current_plan_id') != plan.id:
        fail('旧计划已被替代，不继续原执行')
    return contract


def _checked(db, p, task, plan_id, lock=False):
    return planning.checked(db, p, plan_id, task, lock)


def _budget(db,p,task, extra):
    from .native_goal_queries import step_count as query_step_count
    used = len(task.data['workflow_runtime']['receipts']) + step_count(task) + query_step_count(db,p,task)
    if used + extra + 1 > task.data['goal_spec']['max_steps']:
        fail('原目标累计步骤预算不足，须保留当前人工完成步骤；不会自动加额')


def _prior(task, p, plan_id, body, operation, *, allow_admin=False):
    old = next((x for x in _ledger(task)['commands'] if x['request_id'] == str(body.request_id)), None)
    if old and (old['body_hash'] != digest(_body(body)) or old['operation'] != operation or old['plan_id'] != plan_id
            or old['actor_id'] != p.user_id and not (allow_admin and p.role == 'admin')):
        fail('原执行请求编号不能更换内容、账号、计划或操作')
    return old


def _record(task, p, plan_id, body, operation, *, abandoned=False):
    ledger = _ledger(task)
    if len(ledger['commands']) >= 300:
        fail('执行请求历史已达上限，记录保留')
    ledger['commands'].append({'request_id': str(body.request_id), 'body_hash': digest(_body(body)),
        'operation': operation, 'plan_id': plan_id, 'actor_id': p.user_id,
        'abandoned': abandoned, 'at': now().isoformat()})
    _save(task, ledger)


def _queue(db, p, task, run):
    serial = len(run['job_ids'])
    job = Job(workspace_id=p.workspace_id, kind=JOB_KIND,
        idempotency_key='native-step-execution:' + run['id'] + ':' + str(serial),
        data={'task_id': task.id, 'plan_id': run['id'], 'run_id': run['id'],
            'approval_hash': run['approval_hash'], 'actor_id': p.user_id})
    db.add(job)
    db.flush()
    run['job_ids'].append(job.id)
    run['active_job_id'] = job.id
    return run


def _step_view(step, index):
    external = step.get('adapter') in {'http', 'mcp'}
    return {'index': index, 'title': step['title'], 'agent': step['agent'], 'action': step['action'],
        'input': copy.deepcopy(step.get('input_snapshot', {})), 'external': external,
        'destination': step.get('destination') if external else None,
        **({'capability_id': step['capability_id'], 'output_fields': step.get('output_fields', [])}
            if 'capability_id' in step else {}),
        **({'capability_name': step['capability_name']} if 'capability_name' in step else {})}


def preview(db, p, task, plan_id):
    _owner(db, p, task)
    plan = _checked(db, p, task, plan_id)
    if plan.status != 'needs_review' or _run(task, plan_id) or planning._state(task).get('current_plan_id') != plan_id:
        fail('只能核对当前尚未批准的候选计划')
    context, evidence, tools = _context(db, p, task)
    frozen = plan.data['frozen']['context']
    expected = {k: v for k, v in frozen.items() if k not in {'actor_id', 'remaining_steps', 'remaining_calls'}}
    expected['workflow_prefix_length'] = len(task.data['workflow_runtime']['receipts'])
    if context != expected:
        fail('原候选依据已变化，请保留历史并重新规划')
    proposal = copy.deepcopy(plan.data.get('proposal'))
    if not proposal or not proposal.get('steps'):
        fail('候选计划尚无可执行步骤')
    _budget(db,p,task, len(proposal['steps']))
    from .goal_queries import call_count
    calls = sum(s['action'] == 'invoke_capability' for s in proposal['steps'])
    if call_count(db, p, task) + calls > task.data['goal_spec']['max_tool_calls']:
        fail('原目标累计工具调用预算不足')
    available = {c.id: c for c in tools}
    for step in proposal['steps']:
        action = step['action']
        if action == 'invoke_capability':
            cap = available.get(step['capability_id'])
            if not cap or cap.content_hash != step['capability_hash'] or digest(cap.data) != cap.content_hash:
                fail('候选工具版本不再符合原授权')
            if step['agent'] not in cap.data['allowed_agent_roles'] or digest(step['input_snapshot']) != step['input_hash']:
                fail('候选工具输入或角色无法核对')
            from .business_tools import bind_inputs
            if bind_inputs(cap, step['arguments'], task, evidence, step['request_key']) != step['input_snapshot']:
                fail('候选工具输入已脱离服务器原证据绑定')
            validate(cap.data['input_schema'], step['input_snapshot'])
            step['adapter'] = cap.data['execution']['adapter']
            step['destination'] = cap.data['execution'].get('endpoint')
            step['capability_name'] = cap.data['description']
            step['output_fields'] = [{'key': k, 'label': v.get('title') or k,
                'kind': next((t for t in v.get('type', []) if t != 'null'), None) if isinstance(v.get('type'), list) else v.get('type'),
                'unit': v.get('unit') if isinstance(v.get('unit'), str) else None}
                for k, v in cap.data['output_schema'].get('properties', {}).items()
                if v.get('type') in ('number', 'integer', 'string', 'boolean')
                or isinstance(v.get('type'), list) and len([t for t in v['type'] if t != 'null']) == 1
                    and next(t for t in v['type'] if t != 'null') in ('number', 'integer', 'string', 'boolean')]
        elif (action, step['agent']) not in {('query_evidence', 'data'), ('inspect_observation', 'perception'), ('report', 'business')}:
            fail('候选动作不属于本次只读执行契约')
    if proposal['steps'][-1]['action'] != 'report':
        fail('执行计划必须以只读报告结束')
    contract = {'schema': SCHEMA, 'task_id': task.id, 'plan_id': plan.id,
        'candidate_record_hash': plan.data['record_hash'], 'actor_id': p.user_id, 'actor_role': p.role,
        'proposal_hash': digest(plan.data['proposal']), 'context': context, 'evidence': evidence,
        'steps': proposal['steps'], 'summary': proposal['summary']}
    return {'task_version': task.version, 'plan_version': plan.version,
        'approval_hash': digest(contract), 'steps': [_step_view(s, i) for i, s in enumerate(contract['steps'])],
        'evidence': evidence, 'notice': NOTICE, '_contract': contract}


def approve(db, p, task, plan_id, body):
    _owner(db, p, task)
    old = _prior(task, p, plan_id, body, 'approve')
    if old:
        if old['abandoned']:
            fail('原执行批准请求已作废')
        return
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task)
    check_version(task, body.expected_version)
    plan = _checked(db, p, task, plan_id, True)
    check_version(plan, body.expected_plan_version)
    if not body.reason.strip():
        fail('请说明本次批准或管理员代办依据', 422)
    value = preview(db, p, task, plan_id)
    if value['approval_hash'] != body.approval_hash:
        fail('执行预览已变化，请重新核对完整输入')
    contract = value['_contract']
    for step in contract['steps']:
        if step['action'] == 'invoke_capability':
            cap = published(db, p, step['capability_id'])
            if cap.data['execution'].get('approval_policy') == 'admin':
                require(p, 'admin')
    if any(s['external'] for s in value['steps']) and not body.authorize_external_inputs:
        fail('请明确批准向所列服务发送本次完整输入', 422)
    plan.data = {**plan.data, 'execution_contract': contract}
    planning._seal(plan)
    plan.approved_by = p.user_id
    plan.approved_hash = digest(contract)
    plan.version += 1
    run = {'id': plan.id, 'version': 1, 'actor_id': p.user_id,
        'step_id': contract['context']['step_id'], 'status': 'queued',
        'approval_hash': plan.approved_hash, 'progress': '批准已保存，等待后台只读执行', 'error': None,
        'review': None, 'history': [], 'steps': [{**s, 'status': 'pending', 'output': None,
            'attempt': 0} for s in value['steps']], 'started_steps': [], 'job_ids': [],
        'result_hash': None, 'possible_charge': any(s['external'] for s in value['steps'])}
    _queue(db, p, task, run)
    ledger = _ledger(task)
    ledger['runs'][plan.id] = run
    _save(task, ledger)
    _record(task, p, plan_id, body, 'approve')
    audit(db, p, 'native_step_execution.approved', plan.id, {'task_id': task.id,
        'approval_hash': plan.approved_hash, 'reason': body.reason})


def _guard(db, p, task, plan, run):
    _owner(db, p, task, {'plan_id': plan.id, 'context': plan.data['execution_contract']['context']})
    contract = _contract(db, p, task, plan)
    if run['approval_hash'] != plan.approved_hash or run['actor_id'] != contract['actor_id']:
        fail('执行运行与原批准无法核对')
    if run['status'] in SETTLED:
        fail('原执行已结束，不能继续旧执行')
    return contract


def _approval(plan, run, index):
    return {'native_step_execution': True, 'plan_id': plan.id, 'run_id': run['id'],
        'step_index': index, 'attempt': run['steps'][index]['attempt'], 'approval_hash': plan.approved_hash}


def _call_key(step, attempt):
    return step['request_key'] + (':retry:' + str(attempt) if attempt else '')


def validate_call_approval(db, p, cap, role, inputs, key, approval):
    plan = scoped(db, Plan, approval.get('plan_id'), p, True)
    task = scoped(db, Task, plan.task_id, p, True)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task)
    plan = _checked(db, p, task, plan.id)
    run = _run(task, plan.id)
    if not run or approval.get('native_step_execution') is not True or run['status'] != 'running':
        fail('原工具没有活动的明确执行批准')
    contract = _guard(db, p, task, plan, run)
    index = approval.get('step_index')
    if type(index) is not int or not 0 <= index < len(contract['steps']):
        fail('原工具批准步骤无效')
    step = contract['steps'][index]
    if (approval != _approval(plan, run, index) or step['action'] != 'invoke_capability'
            or step['capability_id'] != cap.id or role != step['agent']
            or key != _call_key(step, run['steps'][index]['attempt'])
            or digest(inputs) != step['input_hash'] or inputs != step['input_snapshot']
            or run['steps'][index]['status'] != 'running'):
        fail('实际工具调用与原批准步骤、输入或尝试不一致')
    if cap.content_hash != step['capability_hash'] or digest(cap.data) != cap.content_hash:
        fail('实际工具契约已变化')
    from .native_planning_grants import eligible
    eligible(cap)
    if cap.data['execution'].get('approval_policy') == 'admin':
        require(p, 'admin')
    approver = db.get(User, contract['actor_id'])
    membership = db.scalar(select(Membership).where(Membership.workspace_id == p.workspace_id,
        Membership.user_id == contract['actor_id']))
    if not approver or not approver.active or not membership or membership.role != contract['actor_role']:
        fail('原批准人的执行权限已撤回，原回执保留，不分派新调用', 403)
    job_id = db.info.get('worker_lease', {}).get('job_id')
    if job_id and run['active_job_id'] != job_id:
        fail('旧后台任务不能分派新调用')
    from .goal_queries import call_count
    previous = db.scalar(select(Invocation.id).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key))
    if not previous and call_count(db, p, task) >= task.data['goal_spec']['max_tool_calls']:
        fail('原目标累计工具调用预算已耗尽')


def before_dispatch(db, p, cap, approval):
    if not approval or approval.get('native_step_execution') is not True:
        fail('原工具缺少明确执行批准')
    db.expire_all()
    plan = scoped(db, Plan, approval['plan_id'], p, True)
    task = scoped(db, Task, plan.task_id, p, True)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task,record_actor=True)
    run = _run(task, plan.id)
    index = approval['step_index']
    if not run:
        fail('原执行运行记录不存在')
    step = plan.data['execution_contract']['steps'][index]
    current_cap = published(db, p, cap.id)
    validate_call_approval(db, p, current_cap, step['agent'], step['input_snapshot'],
        _call_key(step, run['steps'][index]['attempt']), approval)


def history_commit(db, callback):
    """Persist only actual invocation history, never execution permission; lease stays."""
    actor = db.info.pop('worker_actor', None)
    try:
        result = callback()
        db.commit()
        return result
    finally:
        if actor is not None:
            db.info['worker_actor'] = actor


def _result_hash(run, contract):
    return digest({'approval_hash': run['approval_hash'], 'context': contract['context'],
        'steps': [{k: s.get(k) for k in ['index', 'status', 'input', 'output', 'invocation_id', 'attempt', 'error']}
            for s in run['steps']]})


def _call_result(db, p, task, plan, run, index, call):
    contract = plan.data['execution_contract']
    step = contract['steps'][index]
    approval = _approval(plan, run, index)
    if (call.workspace_id != task.workspace_id or call.capability_id != step['capability_id']
            or call.idempotency_key != _call_key(step, approval['attempt'])
            or call.data.get('approval') != approval or call.data.get('input_hash') != step['input_hash']
            or call.data.get('capability_hash') != step['capability_hash']
            or call.data.get('role') != step['agent']
            or call.data.get('inputs') != step['input_snapshot']):
        fail('原真实调用与批准、输入或回执关联无法核对')
    result = run['steps'][index]
    result.update(status=call.status, output=copy.deepcopy(call.data.get('output')),
        invocation_id=call.id, output_hash=digest(call.data.get('output')), error=call.data.get('error'),
        receipt={'status': call.status, 'adapter': step.get('adapter'),
            'started_at': call.data.get('started_at'), 'finished_at': call.data.get('finished_at'),
            'duration_ms': call.data.get('duration_ms')})
    if call.status == 'succeeded':
        original = call.data.get('capability_snapshot')
        if not original or digest(original) != step['capability_hash']:
            fail('真实调用固定能力快照无法核对')
        validate(original['output_schema'], result['output'])
    return run


def _blocked(task, run, reason, status='blocked'):
    if run['status'] in SETTLED:
        return run['status']
    run.update(status=status, error=reason, progress='执行已停止，原调用及输入保留；不会自动重试')
    _write(task, run)
    return status


def process(db, p, job):
    from .leases import checkpoint
    task = scoped(db, Task, job.data['task_id'], p, True)
    plan = _checked(db, p, task, job.data['plan_id'], True)
    run = _run(task, plan.id)
    if not run or run['status'] in SETTLED or run['status'] == 'needs_review':
        return run['status'] if run else 'blocked'
    if run['active_job_id'] != job.id or job.data.get('approval_hash') != run['approval_hash']:
        return run['status']
    try:
        _guard(db, p, task, plan, run)
    except HTTPException as error:
        return _blocked(task, run, str(error.detail))
    run.update(status='running', progress='正在执行已批准的只读步骤', error=None)
    _write(task, run)
    checkpoint(db)
    for index in range(len(plan.data['execution_contract']['steps'])):
        task = scoped(db, Task, task.id, p, True)
        plan = _checked(db, p, task, plan.id, True)
        run = _run(task, plan.id)
        if run['status'] in SETTLED:
            return run['status']
        try:
            contract = _guard(db, p, task, plan, run)
        except HTTPException as error:
            return _blocked(task, run, str(error.detail))
        current = run['steps'][index]
        if current['status'] == 'succeeded':
            if current.get('invocation_id'):
                _call_result(db, p, task, plan, run, index, scoped(db, Invocation, current['invocation_id'], p))
            continue
        if current['status'] != 'pending':
            return _blocked(task, run, '原调用结果尚待核查，请明确恢复或重试', 'failed')
        try:
            _budget(db,p,task, 1)
        except HTTPException as error:
            return _blocked(task, run, str(error.detail))
        current.update(status='running', error=None)
        run['started_steps'].append({'index': index, 'attempt': current['attempt'], 'at': now().isoformat()})
        _write(task, run)
        checkpoint(db)
        step = contract['steps'][index]
        try:
            if step['action'] == 'invoke_capability':
                from .gateway import invoke
                call = invoke(db, p, step['capability_id'], step['agent'], copy.deepcopy(step['input_snapshot']),
                    _call_key(step, current['attempt']), approval=_approval(plan, run, index))
                # Gateway commits release Task locks: never write the old Task snapshot.
                task = scoped(db, Task, task.id, p, True)
                plan = _checked(db, p, task, plan.id, True)
                run = _run(task, plan.id)
                run = _call_result(db, p, task, plan, run, index, call)
                if run['status'] in SETTLED:
                    _write(task, run)
                    return run['status']
                _guard(db, p, task, plan, run)
            elif step['action'] == 'query_evidence':
                _, evidence, _ = _context(db, p, task, contract=contract)
                run['steps'][index].update(status='succeeded', output=evidence)
            elif step['action'] == 'inspect_observation':
                from .execution import inspect_observations
                output = inspect_observations(db, p, task.data['entity_ids'], task.data.get('fields', []), task=task)
                run['steps'][index].update(status='succeeded' if output['status'] == 'sufficient' else 'blocked_evidence', output=output)
            else:
                _, evidence, _ = _context(db, p, task, contract=contract)
                output = {'schema': 'native-step-execution-report-v1', 'evidence': evidence,
                    'tool_results': [copy.deepcopy(s) for s in run['steps'] if s['action'] == 'invoke_capability'],
                    'summary': '真实只读调用与原资料核对报告；专业结论及业务结果仍需人工核查',
                    'physical_state_verified': False, 'cloud_calls': 0, 'workflow_advanced': False}
                run['steps'][index].update(status='succeeded', output=output)
        except HTTPException as error:
            task = scoped(db, Task, task.id, p, True)
            run = _run(task, plan.id)
            if run['status'] in SETTLED:
                return run['status']
            run['steps'][index].update(status='blocked_evidence', error=str(error.detail))
            return _blocked(task, run, str(error.detail))
        _write(task, run)
        checkpoint(db)
        if run['steps'][index]['status'] != 'succeeded':
            task = scoped(db, Task, task.id, p, True)
            run = _run(task, plan.id)
            return _blocked(task, run, run['steps'][index].get('error') or '原工具结果未成功，请核对后明确重试', 'failed')
    task = scoped(db, Task, task.id, p, True)
    plan = _checked(db, p, task, plan.id, True)
    run = _run(task, plan.id)
    try:
        contract = _guard(db, p, task, plan, run)
    except HTTPException as error:
        return _blocked(task, run, str(error.detail))
    run.update(status='needs_review', progress='真实结果已保存，请核对后明确采用或拒绝', error=None)
    run['result_hash'] = _result_hash(run, contract)
    _write(task, run)
    audit(db, p, 'native_step_execution.results_saved', plan.id, {'task_id': task.id, 'result_hash': run['result_hash']})
    return 'needs_review'


def recover_invocation(db, p, job, call):
    approval = call.data.get('approval') or {}
    if approval.get('native_step_execution') is not True:
        return False
    task = scoped(db, Task, job.data['task_id'], p, True)
    plan = _checked(db, p, task, approval['plan_id'], True)
    run = _run(task, plan.id)
    if not run or approval.get('run_id') != run['id']:
        fail('恢复调用与原执行运行不一致')
    if run['active_job_id'] != job.id:
        return True
    index = approval.get('step_index')
    if type(index) is not int or not 0 <= index < len(run['steps']):
        fail('恢复调用的原步骤无法核对')
    if approval != _approval(plan, run, index):
        # An earlier explicit attempt remains history and cannot overwrite a newer one.
        return True
    if call.status == 'running':
        call.status = 'failed'
        call.version += 1
        call.data = {**call.data, 'error': '只读请求执行中断，结果可能未知；不会自动重试',
            'native_interrupted': True}
    run = _call_result(db, p, task, plan, run, approval['step_index'], call)
    if run['status'] not in SETTLED and call.status != 'succeeded':
        run.update(status='failed', error=call.data.get('error'), progress='原调用已恢复为历史，请明确核对或重试')
    _write(task, run)
    return True


def recover_job(db, p, job, reason):
    task = scoped(db, Task, job.data['task_id'], p, True)
    plan = _checked(db, p, task, job.data['plan_id'], True)
    run = _run(task, plan.id)
    if not run or run['status'] in SETTLED or run['status'] == 'needs_review':
        return run['status'] if run else 'blocked'
    if run['active_job_id'] != job.id or job.data.get('approval_hash') != run['approval_hash']:
        return run['status']
    for call in db.scalars(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
            Invocation.data['approval']['run_id'].as_string() == plan.id)):
        recover_invocation(db, p, job, call)
    task = scoped(db, Task, task.id, p, True)
    run = _run(task, plan.id)
    return _blocked(task, run, reason, 'failed')


def _no_live_job(db, p, run):
    live = db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id,
        Job.id.in_(run['job_ids']), Job.status.in_(['queued', 'running'])))
    if live:
        fail('原后台任务尚未结束，请等待保存或中断恢复后重试')


def retry(db, p, task, plan_id, body):
    plan = _checked(db, p, task, plan_id, True)
    _owner(db, p, task, plan.data.get('execution_contract'))
    old = _prior(task, p, plan_id, body, 'retry')
    if old:
        if old['abandoned']:
            fail('原重试请求已作废')
        return
    check_version(task, body.expected_version)
    run = _run(task, plan_id)
    if not run:
        fail('原执行运行不存在')
    if run['version'] != body.expected_execution_version:
        fail('执行记录已更新，请刷新后核对')
    _guard(db, p, task, plan, run)
    _no_live_job(db, p, run)
    if run['status'] not in {'failed', 'blocked'}:
        fail('原执行没有可明确重试的受阻步骤')
    if not body.reason.strip():
        fail('请说明只读重试依据', 422)
    failed = [s for s in run['steps'] if s['status'] not in {'pending', 'succeeded'}]
    if len(failed) > 1:
        fail('请先核查原执行的唯一受阻步骤')
    _budget(db,p,task, sum(s['status'] != 'succeeded' for s in run['steps']))
    from .goal_queries import call_count
    remaining_calls = sum(s['status'] != 'succeeded' and s['action'] == 'invoke_capability' for s in run['steps'])
    if call_count(db, p, task) + remaining_calls > task.data['goal_spec']['max_tool_calls']:
        fail('原目标累计工具调用预算不足，不能批准重试')
    for result in failed:
        index = result['index']
        step = plan.data['execution_contract']['steps'][index]
        if step['action'] == 'invoke_capability':
            cap = published(db, p, step['capability_id'])
            if result['attempt'] + 1 >= min(int(cap.data['budget'].get('max_attempts', 2)), 3):
                fail('登记工具重试次数已耗尽')
        run['history'].append({'step': copy.deepcopy(result), 'reason': body.reason,
            'actor_id': p.user_id, 'at': now().isoformat()})
        result.update(status='pending', output=None, error=None, attempt=result['attempt'] + 1)
        result.pop('invocation_id', None)
        result.pop('output_hash', None)
    run.update(status='queued', progress='明确只读重试已保存，原成功步骤不重放', error=None, result_hash=None)
    _queue(db, p, task, run)
    _write(task, run)
    _record(task, p, plan_id, body, 'retry')


def stop(db, p, task, plan_id, body):
    plan = _checked(db, p, task, plan_id, True)
    _owner(db, p, task, plan.data.get('execution_contract'), recovery=True)
    old = _prior(task, p, plan_id, body, 'stop')
    if old:
        if old['abandoned']:
            fail('原停止请求已作废')
        return
    check_version(task, body.expected_version)
    run = _run(task, plan_id)
    if not run or run['version'] != body.expected_execution_version:
        fail('原执行记录已变化')
    if not body.reason.strip():
        fail('请说明停止依据', 422)
    if task.data.get('native_goal_acceptance') or run['status'] in {'rejected', 'cancelled'}:
        fail('原执行已结束，保留原结果')
    stop_for_plan(db, p, task, plan_id, body.reason)
    _record(task, p, plan_id, body, 'stop')


def stop_for_plan(db, p, task, plan_id, reason):
    if task.data.get('native_goal_acceptance'):
        fail('原目标已正式验收，不能撤回已验收采用依据')
    run = _run(task, plan_id)
    if run and run['status'] not in {'rejected', 'cancelled'}:
        run['history'].append({'operation': 'withdraw_adoption' if run['status'] == 'accepted' else 'stop',
            'previous_review': copy.deepcopy(run.get('review')), 'reason': reason,
            'actor_id': p.user_id, 'at': now().isoformat()})
        run.update(status='cancelled', progress='已停止后续执行；已发送请求或可能费用保留，迟到结果仅历史', error=None)
        _write(task, run)


def stop_all(db, p, task, reason):
    for plan_id in list(_ledger(task)['runs']):
        stop_for_plan(db, p, task, plan_id, reason)


def active_for_plan(db, p, task, plan_id):
    run = _run(task, plan_id)
    return bool(run and run['status'] not in SETTLED)


def unresolved_for_step(task, step_id):
    return any(run['status'] not in SETTLED
        and run.get('step_id') == step_id for run in _ledger(task)['runs'].values())


def guard_step_advance(db, p, task):
    if unresolved_for_step(task, task.data['workflow_runtime']['current_step_id']):
        fail('当前步骤的已批准执行尚未核查，请先采用、拒绝或明确停止，原人工草稿保留')


def _verify_results(db, p, task, plan, run, *, active=False, lock=False):
    contract = _contract(db, p, task, plan, active=active)
    if run['approval_hash'] != plan.approved_hash or run['result_hash'] != _result_hash(run, contract):
        fail('原结果或批准摘要无法核对')
    if any(s['status'] != 'succeeded' for s in run['steps']):
        fail('原执行步骤尚未全部成功')
    receipts = []
    for index, step in enumerate(contract['steps']):
        if step['action'] != 'invoke_capability':
            continue
        result = run['steps'][index]
        call = scoped(db, Invocation, result['invocation_id'], p, lock)
        before = copy.deepcopy(result)
        checked = _call_result(db, p, task, plan, copy.deepcopy(run), index, call)['steps'][index]
        if checked != before or call.status != 'succeeded':
            fail('真实工具输出与已核查报告不一致')
        receipts.append({'status': 'succeeded', 'method': 'registered_capability',
            'capability_id': step['capability_id'], 'output': copy.deepcopy(call.data['output']),
            'invocation_id': call.id, 'input_hash': step['input_hash'], 'output_hash': digest(call.data['output']),
            'capability_hash': step['capability_hash'], 'release_version': step['release_version'],
            'plan_id': plan.id, 'run_id': run['id'], 'approval_hash': plan.approved_hash,
            'result_hash': run['result_hash'], 'goal_snapshot': contract['context']['goal_snapshot']})
    return receipts


def review(db, p, task, plan_id, body):
    plan = _checked(db, p, task, plan_id, True)
    _owner(db, p, task, plan.data.get('execution_contract'))
    old = _prior(task, p, plan_id, body, 'review')
    if old:
        if old['abandoned']:
            fail('原核查请求已作废')
        return
    check_version(task, body.expected_version)
    run = _run(task, plan_id)
    if not run or run['version'] != body.expected_execution_version or run['status'] not in {'needs_review', 'accepted', 'rejected'}:
        fail('原执行尚无可核查结果或已经核查')
    if not body.reason.strip():
        fail('请记录实际核查结论', 422)
    from .native_goals import guard
    guard(db, p, task)
    if body.decision == 'accepted':
        _verify_results(db, p, task, plan, run, active=False, lock=True)
    elif run['result_hash'] != _result_hash(run, plan.data['execution_contract']):
        fail('原核查结果摘要无法核对')
    if body.result_hash != run['result_hash']:
        fail('原核查结果已变化')
    if run.get('review'):
        run['history'].append({'operation': 'review_changed', 'previous_review': copy.deepcopy(run['review']),
            'actor_id': p.user_id, 'reason': body.reason, 'at': now().isoformat()})
    run['review'] = {'decision': body.decision, 'reason': body.reason, 'actor_id': p.user_id, 'at': now().isoformat()}
    run.update(status=body.decision, progress='已明确采用原结果，仍需完成原人工流程和最终目标验收' if body.decision == 'accepted' else '已拒绝原结果，历史保留')
    _write(task, run)
    _record(task, p, plan_id, body, 'review')
    audit(db, p, 'native_step_execution.reviewed', plan_id, {'decision': body.decision, 'result_hash': body.result_hash})


def recover(db, p, task, plan_id, body):
    plan = _checked(db, p, task, plan_id)
    contract = plan.data.get('execution_contract') or {'plan_id': plan.id, 'context': plan.data['frozen']['context']}
    _owner(db, p, task, contract, recovery=True)
    model = {'approve': Approve, 'retry': Retry, 'stop': RunCommand, 'review': Review}[body.operation]
    try:
        original = model.model_validate(body.original_request)
    except ValidationError:
        fail('原执行请求结构无法核对', 422)
    old = _prior(task, p, plan_id, original, body.operation, allow_admin=True)
    if not old and body.action == 'abandon':
        if task.data.get('native_goal_acceptance'):
            fail('原目标已验收，只能读取原请求结果')
        check_version(task, body.expected_version)
        if not body.reason.strip():
            fail('请说明作废原请求的依据', 422)
        _record(task, p, plan_id, original, body.operation, abandoned=True)
    return {'saved': bool(old and not old['abandoned']), 'abandoned': bool(old and old['abandoned'] or not old and body.action == 'abandon'),
        'task_version': task.version, 'execution': _run(task, plan_id)}


def goal_gate(db, p, task, accepted=False):
    receipts, runs, proofs = [], [], []
    all_reviewed = True
    for plan_id, run in sorted(_ledger(task)['runs'].items()):
        runs.append(copy.deepcopy(run))
        proof = {'plan_id': plan_id, 'version': run['version'], 'status': run['status'],
            'approval_hash': run['approval_hash'], 'result_hash': run['result_hash'],
            'review': copy.deepcopy(run['review']), 'history_hash': digest(run['history']),
            'started_steps_hash': digest(run['started_steps'])}
        proofs.append(proof)
        if run['status'] == 'accepted':
            try:
                plan = _checked(db, p, task, plan_id, accepted)
                if not run.get('review') or run['review']['decision'] != 'accepted':
                    fail('原结果没有明确人工采用')
                verified = _verify_results(db, p, task, plan, run, active=False, lock=accepted)
                receipts.extend(verified)
                proof['verified_receipts_hash'] = digest(verified)
            except HTTPException as error:
                all_reviewed = False
                runs[-1]['scope_error'] = str(error.detail)
        elif run['status'] not in {'rejected', 'cancelled'}:
            all_reviewed = False
    return {'receipts': receipts, 'basis_hash': digest(proofs), 'all_reviewed': all_reviewed, 'runs': runs}


def listing(db, p, task, plan_id):
    plan = _checked(db, p, task, plan_id)
    run = _run(task, plan_id)
    scope_error = None
    can_preview = can_approve = can_retry = can_stop = can_review = can_recover = False
    try:
        _owner(db, p, task, plan.data.get('execution_contract'))
        if run:
            if run['status'] in {'needs_review', 'accepted', 'rejected'}:
                from .native_goals import guard
                guard(db, p, task)
                can_review = True
                try:
                    _verify_results(db, p, task, plan, run, active=False)
                except HTTPException as error:
                    scope_error = str(error.detail)
            else:
                _guard(db, p, task, plan, run)
            if run['status'] in {'failed', 'blocked'}:
                _no_live_job(db, p, run)
                can_retry = True
        else:
            preview(db, p, task, plan_id)
            can_preview = can_approve = True
    except HTTPException as error:
        scope_error = str(error.detail)
    try:
        contract = plan.data.get('execution_contract') or {'plan_id': plan.id, 'context': plan.data['frozen']['context']}
        _owner(db, p, task, contract, recovery=True)
        can_recover = True
        can_stop = bool(run and run['status'] not in {'rejected', 'cancelled'} and not task.data.get('native_goal_acceptance'))
    except HTTPException:
        pass
    view = copy.deepcopy(run)
    if view:
        contract = plan.data['execution_contract']
        view['source'] = copy.deepcopy(contract['context'])
        view['evidence'] = copy.deepcopy(contract['evidence'])
    return {'task_version': task.version, 'plan_id': plan_id, 'execution': view,
        'can_preview': can_preview, 'can_approve': can_approve, 'can_retry': can_retry,
        'can_stop': can_stop, 'can_review': can_review, 'can_recover': can_recover, 'scope_error': scope_error}


def export(db, p, task, plan_id):
    plan = _checked(db, p, task, plan_id)
    run = _run(task, plan_id)
    calls = []
    if run:
        for call in db.scalars(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
                Invocation.data['approval']['run_id'].as_string() == run['id']).order_by(Invocation.created_at, Invocation.id)):
            calls.append({'id': call.id, 'version': call.version, 'status': call.status,
                'capability_id': call.capability_id, 'idempotency_key': call.idempotency_key,
                'data': copy.deepcopy(call.data)})
    return {'schema': 'native-step-execution-export-v1', 'task_id': task.id, 'plan_id': plan.id,
        'plan_record_hash': plan.data['record_hash'], 'execution_contract': copy.deepcopy(plan.data.get('execution_contract')),
        'approved_hash': plan.approved_hash, 'execution': listing(db, p, task, plan_id)['execution'],
        'commands': [c for c in _ledger(task)['commands'] if c['plan_id'] == plan_id], 'invocations': calls,
        'workflow_status': task.status, 'execution_advanced_workflow': False, 'physical_state_verified': False,
        'cloud_calls': 0, 'production_ready': False}
