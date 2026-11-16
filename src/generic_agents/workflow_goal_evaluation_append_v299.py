"""Real append-command producer; no old graph activation or SDK is replaced.

The ordinary gateway creates its SDK. This handler owns only a new local Qe,
immutable Eval and candidate queue. Cold readers authenticate the original
command/SDK/query/output; only a real producer lease may read an inflight row.
"""
import copy

from sqlalchemy import func, or_, select

from . import goal_evaluation_append_store_v297 as commands
from . import goal_evaluation_append_query_v299 as queries
from . import workflow_goal_evaluation_contracts_v299 as contract
from . import workflow_goal_evaluation_store_v288 as old
from . import workflow_goal_evaluation_packet_v288 as ports
from . import goal_evaluation_query_v288 as q
from .contracts import digest
from .models import Audit, DataGap, DataRun, Evidence, Invocation, Job
from .services import audit
from .temporal import instant
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation, row_values

KEY = 'workflow_goal_evaluation_append_v299'
STATE = 'workflow-goal-evaluation-append-state-v299'
CREATED = 'workflow.goal_evaluation_append.created_v299'
TERMINAL = 'workflow.goal_evaluation_append.terminal_v299'
RECOVERED = 'goal_evaluation_append_reconciled_v299'
FOREIGN = (old.KEY, *old.FOREIGN)
MAX_ROWS = 1000
need, checked = commands.need, commands.checked
runner_authority = commands.runner_authority


def _rows(db,p,model,*where):
    rows=list(db.scalars(select(model).where(model.workspace_id==p.workspace_id,*where)
        .limit(MAX_ROWS+1).execution_options(populate_existing=True)))
    need(len(rows)<=MAX_ROWS)
    return rows


def _audit(db,p,action,object_id,actor,expected=None):
    rows=_rows(db,p,Audit,Audit.action==action,Audit.object_id==object_id)
    need(len(rows)==1 and rows[0].actor_id==actor
        and (expected is None or digest(rows[0].data)==digest(expected)))
    return rows[0]


def _call(db,p,current,supplied=None):
    job,record=current['job'],current['record']
    rows=_rows(db,p,Invocation,or_(Invocation.idempotency_key==current['key'],
        Invocation.data['job_id'].as_string()==job.id))
    need(len(rows)==1)
    call=rows[0]
    need((supplied is None or supplied.id==call.id) and call.capability_id==current['cap'].id
        and not any(key in call.data for key in FOREIGN)
        and call.idempotency_key==current['key'] and call.data['job_id']==job.id
        and call.data['role']=='data' and call.data['side_effect'] is True
        and call.data['capability_hash']==record['capability_hash']
        and digest(call.data['capability_snapshot'])==record['capability_hash']
        and digest(call.data['inputs'])==record['input_hash']==call.data['input_hash']
        and digest(call.data['approval'])==digest(current['approval']))
    return call


def _command_ref(current):
    return {'id':current['command'].id,'record_hash':current['record']['record_hash']}


def _trigger(current):
    command=current['record']
    return {'kind':'append_requested','command_ref':_command_ref(current),
        'base_evaluation_ref':copy.deepcopy(command['base_evaluation_ref']),
        'base_mature_head_hash':command['base_mature_head_hash'],'request_hash':command['body_hash']}


def _anchor(journal):
    return {'schema':STATE,'journal_hash':journal['journal_hash'],'journal':copy.deepcopy(journal)}


def _generation(db,p,task,row,command):
    base=q._fresh(db,p,Evaluation,command['base_evaluation_ref']['id'])
    need(base.record_hash==command['base_evaluation_ref']['record_hash']
        and base.task_id==row.task_id==task.id and base.goal_version==row.goal_version
        and type(base.generation) is int and type(row.generation) is int and base.generation<row.generation)


def _terminal_value(current,call,row,journal,frozen,initial):
    value={'schema':STATE,'command_ref':_command_ref(current),'task_id':current['task'].id,
        'job_id':current['job'].id,'invocation_id':call.id,'capability_id':current['cap'].id,
        'capability_hash':current['record']['capability_hash'],'input_hash':current['record']['input_hash'],
        'approval_hash':digest(current['approval']),'initial_unknown_audit_id':initial.id,
        'evaluation_record_hash':row.record_hash,'query_ref':frozen['query_ref'],
        'journal_hash':journal['journal_hash'],'output_hash':digest(journal['output'])}
    return {**value,'record_hash':digest(value)}


def _success(db,p,current,call,row,journal,frozen):
    initial=_audit(db,p,'capability.invoke',call.id,current['record']['actor_id'])
    if initial.data=={'status':'succeeded'}:return
    need(initial.data=={'status':'unknown'} and call.data.get(RECOVERED)=={
        'previous_status':'unknown','output_hash':digest(journal['output'])})
    expected=_terminal_value(current,call,row,journal,frozen,initial)
    _audit(db,p,TERMINAL,call.id,current['record']['actor_id'],expected)


def _read(db,p,current,call,*,producer_complete=False):
    journal=call.data.get(KEY)
    events=_rows(db,p,Audit,Audit.action==CREATED,Audit.object_id==call.id)
    rows=_rows(db,p,Evaluation,or_(Evaluation.id==call.id,Evaluation.invocation_id==call.id,
        Evaluation.job_id==current['job'].id,Evaluation.request_key==current['key']))
    if journal is None:
        need(not events and not rows,'原追加成果或审计存在但journal缺失；未重建成果')
        return None
    need(len(rows)==len(events)==1)
    row=rows[0];command=current['record'];task=current['task']
    _generation(db,p,task,row,command)
    need(journal['schema']==STATE and journal['journal_hash']==digest({
        key:value for key,value in journal.items() if key!='journal_hash'})
        and journal['command_ref']==_command_ref(current) and journal['task_id']==task.id
        and journal['job_id']==current['job'].id and journal['invocation_id']==call.id
        and journal['capability_id']==current['cap'].id and journal['capability_hash']==command['capability_hash']
        and journal['input_hash']==command['input_hash'] and journal['approval_hash']==digest(current['approval'])
        and journal['evaluation_record_hash']==row.record_hash
        and events[0].actor_id==command['actor_id'] and digest(events[0].data)==digest(_anchor(journal)))
    frozen=queries.checked_frozen(db,p,task,row.evaluation_query_id,evaluation_id=call.id)
    provenance=frozen['provenance']
    record=contract.check_record(row.data,expected_scope=provenance['scope'],
        goal_snapshot=provenance['goal_snapshot'],preparation_policy=provenance['preparation_policy'],
        trusted_now=row.data['cut_checked_at'])
    need(all(key=='data' or getattr(row,key)==value for key,value in row_values(record).items())
        and record['producer_family']==contract.PRODUCER_FAMILY and record['command_ref']==_command_ref(current)
        and record['trigger']==_trigger(current) and record['evaluation_query_ref']==frozen['query_ref']
        and record['original_query_ref']==command['original_query_ref']
        and record['producer']=={key:provenance['creator'][key] for key in contract.ProducerPin.model_fields}
        and record['ack_refs']==old._acks(db,p,task,frozen['result'],instant(record['cut_at']),record['derived_window']))
    from .auto_evaluation_evidence_gate_v357 import verify_if_auto
    verify_if_auto(db,p,task,current['job'],row,frozen)
    evidence,content=old.evidence_bytes(db,p,row.evidence_id)
    need(content==old._document(task,record) and evidence.sha256==record['evidence_ref']['sha256']
        and evidence.size_bytes==record['evidence_ref']['size_bytes'] and evidence.media_type=='text/plain')
    _audit(db,p,'evidence.upload',evidence.id,command['actor_id'],{'media_type':'text/plain',
        'sha256':evidence.sha256,'cloud_model_id':None,'consent':False})
    output=old._output(row)
    need(digest(output)==digest(journal['output']))
    if call.status=='succeeded':
        need(digest(call.data['output'])==digest(output))
        _success(db,p,current,call,row,journal,frozen)
    else:need(not producer_complete and call.status in {'running','unknown'})
    if producer_complete:need(current['job'].status=='succeeded' and call.status=='succeeded')
    return {'row':row,'record':record,'output':output,'current':current,'frozen':frozen,'journal':journal}


def _query_inventory(db,p,current,call):
    rows=_rows(db,p,DataRun,DataRun.data[queries.KEY]['evaluation_id'].as_string()==call.id)
    events=_rows(db,p,Audit,Audit.action.in_([queries.SAVED,q.SAVED]),
        Audit.data['evaluation_id'].as_string()==call.id)
    need(len(rows)==len(events) and len(rows)<=1)
    if not rows:return None
    need(events[0].action==queries.SAVED and events[0].object_id==rows[0].id)
    return queries.checked_frozen(db,p,current['task'],rows[0].id,evaluation_id=call.id)


def _no_later_effects(db,p,current,call,frozen):
    from .goal_evaluation_maturity_v299 import QUEUED as candidate_queued
    initial=_audit(db,p,'capability.invoke',call.id,current['record']['actor_id'],{'status':'unknown'})
    start=instant(frozen['provenance']['cut_checked_at']);end=initial.created_at
    need(end is not None and end.tzinfo is not None and start<=end)
    conflicts=[
        _rows(db,p,Evaluation,or_(Evaluation.id==call.id,Evaluation.invocation_id==call.id,
            Evaluation.job_id==current['job'].id,Evaluation.evaluation_query_id==frozen['query_ref']['query_id'])),
        _rows(db,p,Audit,Audit.action.in_([CREATED,TERMINAL]),Audit.object_id==call.id),
        _rows(db,p,Job,or_(Job.data['goal_evaluation_ref_v288']['id'].as_string()==call.id,
            Job.data['goal_evaluation_candidate_v299']['evaluation_ref']['id'].as_string()==call.id)),
        _rows(db,p,Audit,Audit.action==candidate_queued,
            Audit.data['evaluation_ref']['id'].as_string()==call.id),
        _rows(db,p,Evidence,Evidence.created_at>=start,Evidence.created_at<=end),
        _rows(db,p,Audit,Audit.action=='evidence.upload',Audit.created_at>=start,Audit.created_at<=end),
        _rows(db,p,DataGap,DataGap.query_id==frozen['query_ref']['query_id'])]
    need(all(not rows for rows in conflicts))


def _materialize(db,p,current,call,frozen):
    task,job=current['task'],current['job'];provenance=frozen['provenance']
    acks=old._acks(db,p,task,frozen['result'],instant(provenance['cut_at']),provenance['derived_window'])
    from .auto_evaluation_trigger_v356 import authority as auto_authority
    from . import auto_evaluation_evidence_gate_v357 as auto_evidence
    origin=auto_authority(db,p,task,job)
    if origin is not None:
        auto_evidence.check_admitted(p,task,origin,frozen,acks)
    previous=_rows(db,p,Evaluation,Evaluation.task_id==task.id,Evaluation.goal_version==task.data['goal_version'])
    generations=sorted(row.generation for row in previous)
    need(generations==list(range(1,len(previous)+1)))
    record={'schema':contract.RECORD,'producer_family':contract.PRODUCER_FAMILY,'command_ref':_command_ref(current),
        'evaluation_id':call.id,'scope':provenance['scope'],'goal_snapshot':provenance['goal_snapshot'],
        'preparation_policy':provenance['preparation_policy'],'generation':len(previous)+1,
        'request_id':job.id,'request_key':current['key'],'original_query_ref':provenance['original_query_ref'],
        'evaluation_query_ref':frozen['query_ref'],'evaluation_request':frozen['request'],
        'evaluation_result':frozen['result'],'query_controls':copy.deepcopy(queries.CONTROLS),
        'cut_at':provenance['cut_at'],'cut_checked_at':provenance['cut_checked_at'],
        'derived_window':provenance['derived_window'],'trigger':_trigger(current),
        'producer':{key:provenance['creator'][key] for key in contract.ProducerPin.model_fields},
        'ack_refs':acks,'simulation_only':current['selected']['binding']['simulation_only']
            or any(item['simulation_only'] for item in acks),
        'test_only':any(item['test_only'] for item in acks),**old.contract.FLAGS}
    evidence=old.save_evidence(db,p,old._document(task,record),'text/plain',cloud_model_id=None)
    need(evidence.data.get('cloud_model_ids')==[])
    record['evidence_ref']={'evidence_id':evidence.id,'sha256':evidence.sha256,
        'size_bytes':evidence.size_bytes,'media_type':evidence.media_type}
    record=contract.seal_record(record,expected_scope=provenance['scope'],goal_snapshot=provenance['goal_snapshot'],
        preparation_policy=provenance['preparation_policy'],trusted_now=provenance['cut_checked_at'])
    row=Evaluation(**row_values(record));_generation(db,p,task,row,current['record']);db.add(row)
    db.flush()
    if origin is not None:
        auto_evidence.bind(db,p,task,job,origin,row,frozen)
    output=old._output(row)
    journal={'schema':STATE,'command_ref':_command_ref(current),'task_id':task.id,'job_id':job.id,
        'invocation_id':call.id,'capability_id':current['cap'].id,
        'capability_hash':current['record']['capability_hash'],'input_hash':current['record']['input_hash'],
        'approval_hash':digest(current['approval']),'evaluation_record_hash':row.record_hash,'output':output}
    journal['journal_hash']=digest(journal);call.data={**call.data,KEY:journal}
    audit(db,p,CREATED,call.id,_anchor(journal));db.flush()
    from .goal_evaluation_maturity_v299 import queue_candidate
    candidate=queue_candidate(db,p,task,row,job)
    need(candidate is not None)
    return copy.deepcopy(output)


@checked
def validate_call_approval(db,p,cap,role,inputs,key,approval):
    need(role=='data' and type(approval) is dict and approval.get('schema')==commands.c.APPROVAL)
    command_id=approval['command_ref']['id']
    command=q._fresh(db,p,commands.Command,command_id)
    job=q._fresh(db,p,Job,command.job_id)
    return commands.runner_authority(db,p,job,cap=cap,inputs=inputs,key=key,approval=approval,require_lease=True)


@checked
def execute(db,p,cap,inputs,key,approval):
    current=validate_call_approval(db,p,cap,'data',inputs,key,approval);call=_call(db,p,current)
    from .auto_evaluation_recovery_v360 import require_unfenced, rollout_enabled
    if rollout_enabled():
        require_unfenced(db,workspace_id=p.workspace_id,task_id=current['task'].id,job_id=current['job'].id)
    need(call.status=='running')
    with db.begin_nested():
        saved=_read(db,p,current,call)
        if saved is not None:return copy.deepcopy(saved['output'])
        frozen=_query_inventory(db,p,current,call)
        if frozen is None:
            need(call.data['lease_token']==current['job'].lease_token
                and not _rows(db,p,Audit,Audit.action=='capability.invoke',Audit.object_id==call.id))
            cut=db.scalar(select(func.clock_timestamp())).isoformat()
            query=queries.create_frozen(db,p,current,call,cut_at=cut)
            frozen=queries.checked_frozen(db,p,current['task'],query.id,evaluation_id=call.id)
        return _materialize(db,p,current,call,frozen)


@checked
def reconcile(db,p,cap,inputs,key,approval,call):
    current=validate_call_approval(db,p,cap,'data',inputs,key,approval);call=_call(db,p,current,call)
    from .auto_evaluation_recovery_v360 import require_unfenced, rollout_enabled
    if rollout_enabled():
        require_unfenced(db,workspace_id=p.workspace_id,task_id=current['task'].id,job_id=current['job'].id)
    need(call.status in {'unknown','running'})
    with db.begin_nested():
        saved=_read(db,p,current,call)
        if saved is not None:return copy.deepcopy(saved['output'])
        frozen=_query_inventory(db,p,current,call)
        need(frozen is not None,'原追加操作没有完整独立查询；不重跑查询或改换原时点')
        need(call.status=='unknown','原运行调用尚无独立失败结案证明，未重放未保存的材料化阶段')
        _no_later_effects(db,p,current,call,frozen)
        return _materialize(db,p,current,call,frozen)


@checked
@commands.binding_structure_scope()
def checked_record(db,p,task,row,*,producer_complete=True):
    job=q._fresh(db,p,Job,row.job_id);call=q._fresh(db,p,Invocation,row.invocation_id)
    # Check generation before following base-history references, so a forged
    # self/cyclic producer cannot recurse through its own mature head.
    found=[entry for entry in commands._inventory(db,p,task) if entry[2].id==job.id]
    need(len(found)==1);_generation(db,p,task,row,found[0][1])
    current=commands.runner_authority(db,p,job,call=call,require_lease=not producer_complete)
    result=_read(db,p,current,call,producer_complete=producer_complete)
    need(result is not None and result['row'].id==row.id and result['row'].record_hash==row.record_hash)
    return result


@checked
def saved_result(db,p,task,job,call,cap,inputs):
    row=q._fresh(db,p,Evaluation,call.id)
    result=checked_record(db,p,task,row,producer_complete=True)
    need(result['current']['job'].id==job.id and result['current']['cap'].id==cap.id
        and digest(inputs)==result['current']['record']['input_hash'])
    return copy.deepcopy(result['output'])


@checked
@commands.binding_structure_scope()
def run(db,p,job):
    current=commands.runner_authority(db,p,job,require_lease=True)
    from .auto_evaluation_recovery_v360 import require_unfenced, rollout_enabled
    if rollout_enabled():
        require_unfenced(db,workspace_id=p.workspace_id,task_id=current['task'].id,job_id=job.id)
    rows=_rows(db,p,Invocation,or_(Invocation.idempotency_key==current['key'],
        Invocation.data['job_id'].as_string()==job.id))
    need(len(rows)<=1)
    if not rows:
        from .gateway import invoke
        call=invoke(db,p,current['cap'].id,'data',current['inputs'],current['key'],approval=current['approval'])
    else:call=rows[0]
    current=commands.runner_authority(db,p,job,call=call,require_lease=True)
    # invoke() can commit and release prior locks; reacquire after it returns.
    if rollout_enabled():
        require_unfenced(db,workspace_id=p.workspace_id,task_id=current['task'].id,job_id=job.id)
    call=_call(db,p,current,call)
    if call.status=='succeeded':
        result=_read(db,p,current,call);need(result is not None)
        return 'succeeded'
    need(call.status in {'unknown','running'})
    output=reconcile(db,p,current['cap'],current['inputs'],current['key'],current['approval'],call)
    initial=_audit(db,p,'capability.invoke',call.id,current['record']['actor_id'],{'status':'unknown'})
    with db.begin_nested():
        call.status='succeeded';call.data={**call.data,'output':output,
            RECOVERED:{'previous_status':'unknown','output_hash':digest(output)}}
        # Flush normal handler status before any cold refresh can discard it.
        db.flush()
        journal=call.data[KEY];row=q._fresh(db,p,Evaluation,call.id)
        frozen=queries.checked_frozen(db,p,current['task'],row.evaluation_query_id,evaluation_id=call.id)
        value=_terminal_value(current,call,row,journal,frozen,initial)
        need(not _rows(db,p,Audit,Audit.action==TERMINAL,Audit.object_id==call.id))
        audit(db,p,TERMINAL,call.id,value);db.flush()
        result=_read(db,p,current,call);need(result is not None)
    return 'succeeded'
