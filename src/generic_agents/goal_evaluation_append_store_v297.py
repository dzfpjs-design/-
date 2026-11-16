"""Accept/read append commands; no query, SDK, cut or Goal result is minted.

Root installs the dedicated runner/API separately. Commands are immutable;
progress and single-pending exclusion derive from actual Jobs and mature Goal
receipts, including unknown/failed work that has not safely finished.
"""
import copy
from functools import wraps
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from . import goal_evaluation_append_contracts_v297 as c
from . import goal_evaluation_provider_v288 as provider
from . import goal_evaluation_query_v288 as queries
from . import workflow_goal_evaluation_packet_v288 as packet
from .contracts import digest
from .goals import snapshot
from .models import Audit, Capability, Invocation, Job, Membership, Memory, Task, User, uid
from .services import published, require
from .temporal import instant
from .goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297 as Command
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation
from .authored_goal_binding_parse_scope_v300 import authored_goal_binding_parse_scope as binding_structure_scope

MAX_COMMANDS = 1000
ERROR = '原追加评估命令、执行归属或成熟结果无法核对；原记录保留，未创建替代操作'


def need(value, detail=ERROR):
    if not value: raise HTTPException(409, detail)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try: return function(*args, **kwargs)
        except HTTPException: raise
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, StopIteration,
                UnicodeError, RecursionError, OverflowError): need(False)
    return wrapped


def _id(value):
    need(type(value) is str and str(UUID(value)) == value)
    return value


def _reader(db, p, *, write=False):
    if write: require(p, 'admin', 'operator')
    roles = ['admin', 'operator'] if write else ['admin', 'operator', 'viewer']
    allowed = roles if write or p.role == 'viewer' else ['admin','operator'] if p.role == 'operator' else ['admin']
    need(p.role in roles and db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(allowed), *([Membership.role == p.role] if write else []))) == p.user_id)


def _task(db, p, task_id, *, lock=False):
    query = select(Task).where(Task.workspace_id == p.workspace_id, Task.id == _id(task_id))
    if lock: query = query.with_for_update()
    task = db.scalar(query.execution_options(populate_existing=True))
    if task is None: raise HTTPException(404, '业务任务不存在或不属于当前工作区')
    return task


def _rows(db, p, model, *conditions):
    rows = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id,
        *conditions).limit(MAX_COMMANDS + 1).execution_options(populate_existing=True)))
    need(len(rows) <= MAX_COMMANDS)
    return rows


def _mature_value(db, p, task, selected, head):
    """The real require_current completion proof, without its freshness gate.

    A stale/insufficient assessment may be an authentic completed assessment.
    No evaluator, provider._evidence or Goal state write is used here.
    """
    row, value, _ = provider._record(db, p, task, selected, head['evaluation_ref'], lock_sources=False)
    goal_job = provider._fresh(db, p, Job, head['goal_check_job_id'])
    provider._goal_job(task, goal_job)
    need(goal_job.status == 'succeeded' and goal_job.lease_until is None
        and goal_job.data[provider.JOB_REF] == head['evaluation_ref']
        and goal_job.data[provider.JOB_PRODUCER] == row.job_id
        and goal_job.data['actor_id'] == head['actor_id'])
    memory = provider._fresh(db, p, Memory, goal_job.data['goal_check_id']); report = memory.data['report']
    need(head['query_id'] == row.evaluation_query_id and head['generation'] == row.generation
        and head['source_binding_hash'] == row.source_binding_hash
        and memory.status == 'candidate' and memory.data['type'] == 'native_goal_check'
        and memory.data['task_id'] == task.id and memory.data['report_hash'] == digest(report)
        and report['goal_snapshot'] == snapshot(task)
        and report[provider.JOB_REF] == head['evaluation_ref']
        and report['evidence'][provider.VIEW]['evaluation_ref'] == head['evaluation_ref']
        and report['goal_evaluation_head_hash_v288'] == head['head_hash'])
    return {'head': copy.deepcopy(head), 'row': row, 'record': value,
        'goal_check_job': goal_job, 'memory': memory, 'report_hash': memory.data['report_hash']}


def _mature_head(db, p, task, selected):
    head = provider._head(db, p, task)
    need(head is not None, '请先完成原评估的正式目标核对，再追加新的评估')
    return _mature_value(db, p, task, selected, head)


def _context(db, p, task):
    from .native_goals import guard, saved_context
    selected = provider.binding(db, p, task)
    need(selected is not None)
    binding = selected['binding']; pin = binding['capability_pins']['evaluation_packet']
    nodes = [node for node in binding['graph']['nodes'] if node['id'] == 'evaluation_packet']
    need(len(nodes) == 1 and nodes[0]['kind'] == 'ability' and nodes[0]['actor_id'] == p.user_id
        and nodes[0]['execution'] == pin and pin['agent_role'] == 'data')
    cap = published(db, p, pin['capability_id'], 'tool'); packet.validate_manifest(cap.data)
    need(cap.content_hash == cap.validated_hash == digest(cap.data) == pin['capability_hash']
        and cap.release_version == pin['release_version']
        and cap.data['input_schema'] == pin['input_schema'] and cap.data['output_schema'] == pin['output_schema'])
    guard(db, p, task, lock_sources=False); saved_context(db, p, task)
    q0 = provider._q0(db, p, task); mature = _mature_head(db, p, task, selected)
    previous = provider._fresh(db, p, Invocation, mature['row'].invocation_id)
    inputs = packet._inputs(previous.data['inputs'])
    need(previous.capability_id == cap.id and previous.status == 'succeeded'
        and digest(inputs) == previous.data['input_hash']
        and mature['record']['original_query_ref'] == queries.query_ref(q0))
    scope = {'workspace_id': p.workspace_id, 'task_id': task.id, 'instance_id': task.data['instance_id'],
        'release_id': selected['release'].id, 'package_id': task.package_id,
        'business_id': task.data['business_id'], 'graph_hash': digest(binding['graph'])}
    return {'selected': selected, 'cap': cap, 'inputs': inputs, 'q0_ref': queries.query_ref(q0),
        'mature': mature, 'scope': scope, 'goal': snapshot(task)}


def _anchor(record):
    return {key: record[key] for key in ('schema', 'command_id', 'workspace_id', 'task_id', 'actor_id',
        'request_id', 'body_hash', 'goal_version', 'goal_hash', 'scope_hash', 'source_binding_hash',
        'capability_id', 'capability_hash', 'input_hash', 'job_id', 'invocation_key', 'record_hash')}


def approval_for(record):
    return {'schema': c.APPROVAL, 'command_ref': {'id': record['command_id'], 'record_hash': record['record_hash']},
        **{key: record[key] for key in ('workspace_id', 'task_id', 'actor_id', 'goal_version', 'goal_hash',
            'scope_hash', 'source_binding_hash', 'capability_id', 'capability_hash', 'input_hash')}}


def job_data_for(record):
    return {'schema': c.JOB_SCHEMA, 'command_ref': {'id': record['command_id'], 'record_hash': record['record_hash']},
        'task_id': record['task_id'], 'actor_id': record['actor_id'], 'invocation_key': record['invocation_key'],
        'inputs': copy.deepcopy(record['inputs']), 'input_hash': record['input_hash'], 'approval': approval_for(record)}


def _saved(db, p, task, row):
    record = c.AppendRecord.model_validate(row.data).model_dump(mode='json')
    need(row.workspace_id == p.workspace_id == task.workspace_id and row.task_id == task.id
        and row.version == 1 and digest(record) == digest(row.data)
        and record['record_hash'] == digest({key: value for key, value in record.items() if key != 'record_hash'})
        and record['command_id'] == row.id and record['workspace_id'] == p.workspace_id
        and record['task_id'] == task.id and record['scope_hash'] == digest(record['scope'])
        and record['input_hash'] == digest(record['inputs'])
        and record['body_hash'] == c.body_hash(task.id, record['body'])
        and record['request_id'] == record['body']['request_id']
        and record['goal_hash'] == record['body']['expected_goal_hash']
        and record['base_mature_head_hash'] == record['body']['expected_mature_head_hash']
        and record['invocation_key'] == c.invocation_key(task.id, record['request_id'])
        and instant(record['accepted_at']) == row.created_at)
    for name in ('actor_id','goal_version','goal_hash','request_id','body_hash','capability_id',
            'capability_hash','input_hash','job_id','invocation_key','record_hash'):
        need(getattr(row, name) == record[name])
    events = _rows(db, p, Audit, Audit.action == c.ACTION, or_(Audit.object_id == row.id,
        (Audit.data['task_id'].as_string() == task.id) & (Audit.data['request_id'].as_string() == row.request_id)))
    need(len(events) == 1 and events[0].id == record['accepted_audit_id']
        and events[0].actor_id == row.actor_id and events[0].object_id == row.id
        and events[0].created_at == row.created_at and digest(events[0].data) == digest(_anchor(record)))
    jobs = _rows(db, p, Job, or_(Job.id == row.job_id, Job.idempotency_key == row.invocation_key,
        Job.data['command_ref']['id'].as_string() == row.id))
    need(len(jobs) == 1)
    job = jobs[0]; expected = job_data_for(record)
    need(job.id == row.job_id and job.kind == c.KIND and job.idempotency_key == row.invocation_key
        and all(key in job.data and digest(job.data[key]) == digest(value) for key,value in expected.items())
        and not {'activation_id','graph_hash','cancel_epoch','scope'} & set(job.data))
    return record, job


def _progress(db, p, task, record, job):
    calls = _rows(db, p, Invocation, or_(Invocation.idempotency_key == record['invocation_key'],
        Invocation.data['job_id'].as_string() == job.id))
    need(len(calls) <= 1)
    if not calls:
        return ('queued' if job.status == 'queued' else 'running' if job.status == 'running' else 'needs_review'), None
    call = calls[0]
    need(call.capability_id == record['capability_id'] and call.idempotency_key == record['invocation_key']
        and call.data['job_id'] == job.id and call.data['role'] == 'data' and call.data['side_effect'] is True
        and call.data['capability_hash'] == record['capability_hash']
        and digest(call.data['capability_snapshot']) == record['capability_hash']
        and digest(call.data['inputs']) == record['input_hash'] == call.data['input_hash']
        and digest(call.data['approval']) == digest(approval_for(record)))
    if call.status == 'running' and job.status == 'running': return 'running', call
    if call.status != 'succeeded' or job.status != 'succeeded': return 'needs_review', call
    if task.data.get('goal_hash') != record['goal_hash'] or task.data.get('goal_version') != record['goal_version']:
        return 'needs_review', call
    # The validated chain retains past mature heads when a later append wins.
    # An old completed operation must not become pending again after Eval3.
    selected = provider.binding(db, p, task); provider._head(db, p, task)
    heads = _rows(db, p, Audit, Audit.action == provider.SELECTED, Audit.object_id == task.id,
        Audit.data['goal_snapshot_hash'].as_string() == record['goal_snapshot_hash'],
        Audit.data['evaluation_ref']['id'].as_string() == call.id)
    closed = []
    for event in heads:
        goal_job = provider._fresh(db, p, Job, event.data['goal_check_job_id'])
        if goal_job.status != 'succeeded': continue
        mature = _mature_value(db, p, task, selected, event.data)
        if mature['record'].get('schema')=='workflow-goal-evaluation-record-v299':
            from .goal_evaluation_maturity_v299 import verify_head
            need(verify_head(db,p,task,event.data))
        need(mature['row'].job_id == job.id and mature['row'].invocation_id == call.id
            and mature['record']['producer']['capability_id'] == record['capability_id']
            and mature['record']['producer']['input_hash'] == record['input_hash'])
        closed.append(mature)
    need(len(closed) <= 1)
    return ('completed' if closed else 'awaiting_goal_check'), call


def _operation_progress(db,p,task,record,job):
    """GET-only completion receipt; no fact, freshness or execution authority.

    Full sources remain mandatory in _progress, runner and result consumers.
    A protected publication receipt proves only this original operation ended.
    """
    from . import goal_evaluation_maturity_v299 as maturity
    from . import workflow_goal_evaluation_append_v299 as producer
    from .workflow_goal_evaluation_models_v288 import row_values
    calls=_rows(db,p,Invocation,or_(Invocation.idempotency_key==record['invocation_key'],
        Invocation.data['job_id'].as_string()==job.id))
    need(len(calls)<=1)
    pins=[Audit.data['producer_job_id'].as_string()==job.id]
    if calls:pins.append(Audit.data['evaluation_ref']['id'].as_string()==calls[0].id)
    matured=_rows(db,p,Audit,Audit.action==maturity.MATURED,Audit.object_id==task.id,or_(*pins))
    need(len(matured)<=1)
    if not calls:
        need(not matured)
        return ('queued' if job.status=='queued' else 'running' if job.status=='running' else 'needs_review'),None
    call=calls[0]
    need(call.capability_id==record['capability_id'] and call.idempotency_key==record['invocation_key']
        and call.data['job_id']==job.id and call.data['role']=='data' and call.data['side_effect'] is True
        and call.data['capability_hash']==record['capability_hash']
        and digest(call.data['capability_snapshot'])==record['capability_hash']
        and digest(call.data['inputs'])==record['input_hash']==call.data['input_hash']
        and digest(call.data['approval'])==digest(approval_for(record))
        and not any(key in call.data for key in producer.FOREIGN))
    if call.status=='running' and job.status=='running':
        need(not matured);return 'running',call
    if call.status!='succeeded' or job.status!='succeeded':
        need(not matured);return 'needs_review',call
    if task.data.get('goal_hash')!=record['goal_hash'] or task.data.get('goal_version')!=record['goal_version']:
        need(not matured);return 'needs_review',call
    rows=_rows(db,p,Evaluation,or_(Evaluation.id==call.id,Evaluation.invocation_id==call.id,
        Evaluation.job_id==job.id,Evaluation.request_key==record['invocation_key']))
    need(len(rows)==1);row=rows[0];value=row.data
    need(value['schema']=='workflow-goal-evaluation-record-v299' and value['producer_family']=='append_command_v297'
        and value['command_ref']=={'id':record['command_id'],'record_hash':record['record_hash']}
        and all(name=='data' or getattr(row,name)==expected for name,expected in row_values(value).items())
        and row.id==row.invocation_id==call.id and row.job_id==job.id and row.task_id==task.id
        and row.actor_id==record['actor_id'] and row.goal_hash==record['goal_hash']
        and row.goal_version==record['goal_version'] and row.goal_snapshot_hash==record['goal_snapshot_hash']
        and row.source_binding_hash==record['source_binding_hash']
        and value['goal_snapshot']==snapshot(task)
        and value['scope']['native_goal_context_hash']==record['native_goal_context_hash']
        and value['original_query_ref']==record['original_query_ref']
        and value['trigger']=={'kind':'append_requested','command_ref':value['command_ref'],
            'base_evaluation_ref':record['base_evaluation_ref'],'base_mature_head_hash':record['base_mature_head_hash'],
            'request_hash':record['body_hash']}
        and value['producer']['workspace_id']==p.workspace_id and value['producer']['task_id']==task.id
        and value['producer']['invocation_id']==call.id and value['producer']['job_id']==job.id
        and value['producer']['actor_id']==record['actor_id']
        and value['producer']['capability_id']==call.capability_id
        and value['producer']['capability_hash']==record['capability_hash']
        and value['producer']['input_hash']==record['input_hash']
        and value['producer']['lease_token']==call.data['lease_token'])
    journal=call.data[producer.KEY];output=producer.old._output(row)
    need(journal['schema']==producer.STATE and journal['journal_hash']==digest({
        key:item for key,item in journal.items() if key!='journal_hash'})
        and journal['command_ref']==value['command_ref'] and journal['task_id']==task.id
        and journal['job_id']==job.id and journal['invocation_id']==call.id
        and journal['capability_id']==call.capability_id and journal['capability_hash']==record['capability_hash']
        and journal['input_hash']==record['input_hash'] and journal['approval_hash']==digest(approval_for(record))
        and journal['evaluation_record_hash']==row.record_hash
        and digest(journal['output'])==digest(call.data['output'])==digest(output))
    producer._audit(db,p,producer.CREATED,call.id,record['actor_id'],producer._anchor(journal))
    # Detached IDs only feed the existing terminal-hash comparison; all
    # command/SDK custody was read and checked above, without a live grant.
    current={'task':task,'job':job,'record':record,'command':SimpleNamespace(id=record['command_id']),
        'cap':SimpleNamespace(id=call.capability_id),'approval':approval_for(record)}
    producer._success(db,p,current,call,row,journal,{'query_ref':value['evaluation_query_ref']})
    heads=_rows(db,p,Audit,Audit.action==provider.SELECTED,Audit.object_id==task.id,
        Audit.data['evaluation_ref']['id'].as_string()==call.id)
    need(len(heads)<=1)
    if not heads:
        need(not matured);return 'awaiting_goal_check',call
    head=heads[0].data
    need(head['evaluation_ref']=={'id':row.id,'record_hash':row.record_hash}
        and head['previous_hash']==record['base_mature_head_hash'])
    receipt=maturity.publication_receipt(db,p,task,head)
    need(receipt['row'].id==row.id and receipt['row'].record_hash==row.record_hash
        and digest(receipt['record'])==digest(value) and digest(receipt['head'])==digest(head)
        and receipt['candidate']['base_head']['head_hash']==record['base_mature_head_hash']
        and receipt['candidate']['base_head']['evaluation_ref']==record['base_evaluation_ref']
        and len(matured)==1)
    return 'completed',call


def _view(db, p, task, row, *, operation_receipt=False):
    record,job=_saved(db,p,task,row)
    phase,call=(_operation_progress if operation_receipt else _progress)(db,p,task,record,job)
    messages = {'queued':'已保存，等待后台处理','running':'正在核对最新资料',
        'needs_review':'原操作需要核查，请保留原号继续处理',
        'awaiting_goal_check':'评估已保存，等待目标核对完成','completed':'本次追加评估已完成'}
    if operation_receipt and phase=='completed':
        messages['completed']='后台核对已完成；使用结果时会重新核对时效'
    return {'operation_id': row.id, 'task_id': task.id, 'request_id': row.request_id,
        'job_id': job.id, 'invocation_id': call.id if call else None, 'phase': phase,
        'message': messages[phase], 'accepted_at': record['accepted_at'], 'reason': record['body']['reason'],
        'goal_version': row.goal_version, 'expected_goal_hash': record['goal_hash'],
        'base_evaluation_ref': copy.deepcopy(record['base_evaluation_ref']),
        'base_mature_head_hash': record['base_mature_head_hash']}


def _commands(db, p, task):
    prefix = 'goal-evaluation-append:' + task.id + ':'
    return _rows(db, p, Command, or_(Command.task_id == task.id,
        Command.data['task_id'].as_string() == task.id,
        Command.invocation_key.startswith(prefix, autoescape=True)))


def _inventory(db, p, task):
    """Discover all three independent directions before declaring no pending.

    An original Task key survives a lost command or mutable Job task pin. An
    orphaned Job/Audit is a restriction, never permission for another nonce.
    """
    commands = _commands(db, p, task)
    command_ids = [row.id for row in commands]; job_ids = [row.job_id for row in commands]
    prefix = 'goal-evaluation-append:' + task.id + ':'
    jobs = _rows(db, p, Job, or_(
        ((Job.kind == c.KIND) | (Job.data['schema'].as_string() == c.JOB_SCHEMA))
            & (Job.data['task_id'].as_string() == task.id),
        Job.idempotency_key.startswith(prefix, autoescape=True),
        Job.data['invocation_key'].as_string().startswith(prefix, autoescape=True),
        Job.id.in_(job_ids), Job.data['command_ref']['id'].as_string().in_(command_ids)))
    audits = _rows(db, p, Audit, Audit.action == c.ACTION, or_(
        Audit.data['task_id'].as_string() == task.id,
        Audit.data['invocation_key'].as_string().startswith(prefix, autoescape=True),
        Audit.object_id.in_(command_ids), Audit.data['job_id'].as_string().in_([row.id for row in jobs])))
    need(len(commands) == len(jobs) == len(audits)
        and len(set(command_ids)) == len(commands)
        and len({row.id for row in jobs}) == len(jobs)
        and len({row.id for row in audits}) == len(audits))
    result = []
    for row in commands:
        record, job = _saved(db, p, task, row)
        result.append((row, record, job))
    need({row.id for row in jobs} == {record['job_id'] for _,record,_ in result}
        and {row.id for row in audits} == {record['accepted_audit_id'] for _,record,_ in result})
    return result


@checked
def additional_budget_calls(db, p, task):
    """One reservation per immutable append command, including unknown work.

    Integrate into shared Goal counting before enabling the new runner/API.
    The legacy counter currently excludes this new Job kind.
    """
    return len(_inventory(db, p, task))


def _base_history(db, p, task, selected, record):
    # Only the accepted command's historical routing is authenticated here.
    # New Qe facts/ACK/originals and current dispatch gates stay at their normal
    # readers; an ancestor's sensor tree is not a new fact dependency.
    from .goal_history_routing_v302 import checked_base
    return checked_base(db,p,task,selected,record)


@checked
@binding_structure_scope()
def runner_authority(db,p,job,*,cap=None,inputs=None,key=None,approval=None,call=None,require_lease=True):
    """Live dispatch or grantless original producer custody; never fake a graph."""
    _reader(db,p,write=require_lease)
    need(job.workspace_id==p.workspace_id and job.kind==c.KIND)
    task=_task(db,p,job.data['task_id'],lock=require_lease)
    found=[entry for entry in _inventory(db,p,task) if entry[2].id==job.id]
    need(len(found)==1);command,record,actual_job=found[0]
    selected=provider.binding(db,p,task);need(selected is not None)
    binding=selected['binding'];scope={'workspace_id':p.workspace_id,'task_id':task.id,
        'instance_id':task.data['instance_id'],'release_id':selected['release'].id,
        'package_id':task.package_id,'business_id':task.data['business_id'],'graph_hash':digest(binding['graph'])}
    need(digest(scope)==record['scope_hash'] and digest(record['scope'])==digest(scope)
        and record['source_binding_hash']==binding['binding_hash']
        and record['native_goal_context_hash']==task.data['native_goal_context_hash']
        and record['goal_hash']==task.data['goal_hash'] and record['goal_version']==task.data['goal_version']
        and record['goal_snapshot_hash']==digest(snapshot(task))
        and record['original_query_ref']==queries.query_ref(provider._q0(db,p,task)))
    actual_cap=provider._fresh(db,p,Capability,record['capability_id']);packet.validate_manifest(actual_cap.data)
    pin=binding['capability_pins']['evaluation_packet']
    need(actual_cap.content_hash==actual_cap.validated_hash==digest(actual_cap.data)==record['capability_hash']
        and actual_cap.id==pin['capability_id'] and actual_cap.content_hash==pin['capability_hash']
        and actual_cap.release_version==record['release_version']==pin['release_version'])
    expected=approval_for(record)
    need((cap is None or cap.id==actual_cap.id and cap.content_hash==actual_cap.content_hash)
        and (inputs is None or digest(inputs)==record['input_hash'])
        and (key is None or key==record['invocation_key'])
        and (approval is None or digest(approval)==digest(expected)))
    if call is not None:
        actual_call=provider._fresh(db,p,Invocation,call.id)
        need(actual_call.workspace_id==p.workspace_id and actual_call.capability_id==actual_cap.id
            and actual_call.idempotency_key==record['invocation_key']
            and actual_call.data['job_id']==actual_job.id and actual_call.data['role']=='data'
            and actual_call.data['side_effect'] is True
            and actual_call.data['capability_hash']==record['capability_hash']
            and digest(actual_call.data['capability_snapshot'])==record['capability_hash']
            and digest(actual_call.data['inputs'])==record['input_hash']==actual_call.data['input_hash']
            and digest(actual_call.data['approval'])==digest(expected))
        evaluations=_rows(db,p,Evaluation,or_(Evaluation.id==actual_call.id,Evaluation.invocation_id==actual_call.id))
        need(len(evaluations)<=1)
        if evaluations:
            base=provider._fresh(db,p,Evaluation,record['base_evaluation_ref']['id']);produced=evaluations[0]
            need(base.record_hash==record['base_evaluation_ref']['record_hash']
                and base.task_id==produced.task_id==task.id and base.goal_version==produced.goal_version
                and type(base.generation) is int and type(produced.generation) is int
                and base.generation<produced.generation)
    _base_history(db,p,task,selected,record)
    if require_lease:
        from .leases import fenced,ownership
        owner=ownership(db)
        need(fenced(db) and actual_job.status=='running' and p.user_id==record['actor_id']
            and owner=={'job_id':actual_job.id,'lease_token':actual_job.lease_token} and bool(actual_job.lease_token))
        with db.no_autoflush:
            leased=db.scalar(select(Job.id).where(Job.workspace_id==p.workspace_id,Job.id==actual_job.id,
                Job.kind==c.KIND,Job.status=='running',Job.lease_token==owner['lease_token'],
                Job.lease_until>func.clock_timestamp()))
        need(leased==actual_job.id)
        live=_context(db,p,task)
        need(live['mature']['head']['head_hash']==record['base_mature_head_hash']
            and live['cap'].id==actual_cap.id and digest(live['inputs'])==record['input_hash'])
        from .goal_queries import call_count
        from .native_step_execution import step_count
        from .native_goal_queries import step_count as query_step_count
        need(call_count(db,p,task)<=task.data['goal_spec']['max_tool_calls'], '原目标能力调用预算已超限')
        used=len(task.data['workflow_runtime']['receipts'])+step_count(task)+query_step_count(db,p,task)
        need(used<=task.data['goal_spec']['max_steps'], '原目标步骤预算已超限')
        # Live automatic operations retain their policy origin and revocation
        # fence; manual append commands follow their unchanged authority path.
        from .auto_evaluation_trigger_v356 import authority as auto_authority
        auto_authority(db,p,task,actual_job)
    return {'task':task,'job':actual_job,'cap':actual_cap,'command':command,'record':record,
        'selected':selected,'inputs':copy.deepcopy(record['inputs']),'key':record['invocation_key'],'approval':expected}


@checked
@binding_structure_scope()
def accept(db, p, task_id, body):
    body = c.AppendRequest.model_validate(body).model_dump(mode='json')
    _reader(db, p, write=True); task = _task(db, p, task_id, lock=True)
    inventory = _inventory(db, p, task)
    old = [row for row,_,_ in inventory if row.request_id == body['request_id']]
    need(len(old) <= 1)
    if old:
        record, _ = _saved(db, p, task, old[0])
        need(record['actor_id'] == p.user_id and record['body_hash'] == c.body_hash(task.id, body),
            '此追加请求已保存为不同内容或账号；请读取原操作，未再次排队')
        return _view(db, p, task, old[0])
    key = c.invocation_key(task.id, body['request_id'])
    context = _context(db, p, task); goal = context['goal']; mature = context['mature']
    need(goal['hash'] == body['expected_goal_hash']
        and mature['head']['head_hash'] == body['expected_mature_head_hash'],
        '原目标或成熟评估已变化，请读取最新结果后再提出新的追加请求')
    for row,record,job in inventory:
        if row.goal_version == task.data['goal_version']:
            phase, _ = _progress(db, p, task, record, job)
            need(phase == 'completed', '本目标已有尚未结清的追加评估，请继续原操作，未另建任务')
    from .goal_queries import call_count
    from .native_step_execution import step_count
    from .native_goal_queries import step_count as query_step_count
    need(call_count(db,p,task) < goal['spec']['max_tool_calls'], '原目标能力调用预算已用尽')
    used_steps = len(task.data['workflow_runtime']['receipts']) + step_count(task) + query_step_count(db,p,task)
    need(used_steps < goal['spec']['max_steps'], '原目标步骤预算已用尽')
    clock = db.scalar(select(func.clock_timestamp())); identity, job_id, audit_id = uid(), uid(), uid()
    record = {'schema': c.SCHEMA, 'command_id': identity, 'workspace_id': p.workspace_id,
        'task_id': task.id, 'actor_id': p.user_id, 'request_id': body['request_id'], 'body': body,
        'body_hash': c.body_hash(task.id,body), 'goal_version': goal['version'], 'goal_hash': goal['hash'],
        'goal_snapshot_hash': digest(goal), 'native_goal_context_hash': task.data['native_goal_context_hash'],
        'scope': context['scope'], 'scope_hash': digest(context['scope']),
        'source_binding_hash': context['selected']['binding']['binding_hash'],
        'capability_id': context['cap'].id, 'capability_hash': context['cap'].content_hash,
        'release_version': context['cap'].release_version, 'inputs': context['inputs'],
        'input_hash': digest(context['inputs']), 'original_query_ref': context['q0_ref'],
        'base_evaluation_ref': mature['head']['evaluation_ref'], 'base_mature_head_hash': mature['head']['head_hash'],
        'base_goal_check_job_id': mature['goal_check_job'].id, 'base_goal_check_memory_id': mature['memory'].id,
        'base_goal_check_report_hash': mature['report_hash'], 'job_id': job_id, 'invocation_key': key,
        'accepted_audit_id': audit_id, 'accepted_at': clock.isoformat()}
    record['record_hash'] = digest(record); record = c.AppendRecord.model_validate(record).model_dump(mode='json')
    job = Job(id=job_id,workspace_id=p.workspace_id,kind=c.KIND,status='queued',idempotency_key=key,
        data=job_data_for(record))
    row = Command(id=identity,workspace_id=p.workspace_id,task_id=task.id,version=1,created_at=clock,
        **{name:record[name] for name in ('actor_id','goal_version','goal_hash','request_id','body_hash',
            'capability_id','capability_hash','input_hash','job_id','invocation_key','record_hash')},data=record)
    with db.begin_nested():
        db.add(job); db.flush(); db.add(row)
        db.add(Audit(id=audit_id,workspace_id=p.workspace_id,actor_id=p.user_id,action=c.ACTION,
            object_id=row.id,created_at=clock,data=_anchor(record)))
        db.flush()
        result = _view(db,p,task,row)
    return result


@checked
@binding_structure_scope()
def get_operation(db, p, task_id, request_id):
    _reader(db,p);task=_task(db,p,task_id)
    request_id = _id(request_id)
    rows = [row for row,_,_ in _inventory(db,p,task) if row.request_id == request_id]
    if not rows: raise HTTPException(404,'追加评估操作不存在或不属于当前业务任务')
    need(len(rows)==1)
    return _view(db,p,task,rows[0],operation_receipt=True)
