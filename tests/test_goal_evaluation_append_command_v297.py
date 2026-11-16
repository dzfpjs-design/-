"""Command custody tests with explicit Task/source/IAM/SQL result boundaries.

Actual contracts, immutable command seal, acceptance/idempotency, Job/approval
and pending/budget checks run. No real PG, worker, SDK, Qe or persistence proof
is claimed; Root must install and verify the eventual vertical slice.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, BinaryExpression, BooleanClauseList, Grouping

from generic_agents import goal_evaluation_append_contracts_v297 as c
from generic_agents import goal_evaluation_append_store_v297 as s
from generic_agents import goal_queries, native_step_execution, native_goal_queries
from generic_agents.contracts import digest

ACTUAL_CONTEXT = s._context


def predicate(expression, row):
    """Interpret the actual scoped SQL predicate; unsupported SQL must fail."""
    if isinstance(expression,BindParameter):return expression.value
    if isinstance(expression,Grouping):return predicate(expression.element,row)
    if isinstance(expression,BooleanClauseList):
        values=[predicate(item,row) for item in expression.clauses]
        if expression.operator is operators.and_:return all(values)
        if expression.operator is operators.or_:return any(values)
        raise AssertionError('Unsupported boolean SQL operator')
    if isinstance(expression,BinaryExpression):
        left,right=predicate(expression.left,row),predicate(expression.right,row)
        op=expression.operator
        if op in (operators.json_getitem_op,operators.json_path_getitem_op):
            return left.get(right) if isinstance(left,dict) else None
        if op is operators.eq:return left is not None and right is not None and left==right
        if op is operators.in_op:return left is not None and left in right
        if op is operators.startswith_op:return type(left) is str and left.startswith(right)
        if op is operators.is_:return left is right
        if op is operators.is_not:return left is not right
        raise AssertionError('Unsupported binary SQL operator')
    name=getattr(expression,'__visit_name__',None)
    if name=='null':return None
    if name=='true':return True
    if name=='false':return False
    if hasattr(expression,'table') and getattr(expression,'key',None):
        return getattr(row,expression.key)
    raise AssertionError('Unsupported SQL expression '+type(expression).__name__)


def uid(value):return str(UUID(int=value))


@pytest.fixture
def scene(monkeypatch):
    at=datetime(2026,11,2,12,tzinfo=timezone.utc);p=NS(workspace_id=uid(1),user_id=uid(2),role='admin')
    spec={'max_tool_calls':10,'max_steps':10}
    task=s.Task(id=uid(3),workspace_id=p.workspace_id,package_id=uid(4),owner_id=p.user_id,
        title='追加资料核对',status='workflow_active',version=1,data={'goal_spec':spec,'goal_hash':digest(spec),
            'goal_version':1,'goal_confirmed_by':p.user_id,'goal_window':{'reference_at':at.isoformat()},
            'native_goal_context_hash':'a'*64,'workflow_runtime':{'receipts':[]}})
    goal=s.snapshot(task);head={'head_hash':'b'*64,'evaluation_ref':{'id':uid(5),'record_hash':'c'*64}}
    context={'goal':goal,'selected':{'binding':{'binding_hash':'d'*64}},
        'cap':NS(id=uid(6),content_hash='e'*64,release_version='1.0.0'),
        'inputs':{'local_plan':{'explicit_authenticated_original_plan_IO':True}},
        'q0_ref':{'query_id':uid(7),'version':1,'request_hash':'f'*64,'result_hash':'f'*64,'data_hash':'f'*64},
        'scope':{'workspace_id':p.workspace_id,'task_id':task.id},
        'mature':{'head':head,'goal_check_job':NS(id=uid(8)),'memory':NS(id=uid(9)),'report_hash':'f'*64}}
    body={'request_id':uid(10),'expected_goal_hash':goal['hash'],'expected_mature_head_hash':head['head_hash'],
        'reason':'新资料已独立接收，重新核对'}
    state=NS(p=p,task=task,context=context,body=body,commands=[],jobs=[],audits=[],calls=[],evaluations=[],reads=[],flushes=0)
    class DB:
        def scalar(self,statement):
            text=str(statement)
            if 'clock_timestamp' in text:return at
            assert statement.column_descriptions[0]['entity'] is s.Task
            params=statement.compile().params
            assert p.workspace_id in params.values() and task.id in params.values()
            state.reads.append(('task',statement._for_update_arg is not None))
            return task if predicate(statement.whereclause,task) else None
        def scalars(self,statement):
            model=statement.column_descriptions[0]['entity'];params=statement.compile().params
            assert p.workspace_id in params.values()
            assert s.MAX_COMMANDS+1 in params.values()
            inventories={s.Command:state.commands,s.Job:state.jobs,s.Audit:state.audits,s.Invocation:state.calls,
                s.Evaluation:state.evaluations}
            assert model in inventories
            return [row for row in inventories[model] if predicate(statement.whereclause,row)]
        def add(self,row):
            if type(row) is s.Job:
                row.attempts=0;row.version=1;state.jobs.append(row)
            elif type(row) is s.Command:state.commands.append(row)
            else:assert type(row) is s.Audit;state.audits.append(row)
        def flush(self):state.flushes+=1
        @contextmanager
        def begin_nested(self):
            before=(list(state.commands),list(state.jobs),list(state.audits))
            try:yield
            except BaseException:
                state.commands[:],state.jobs[:],state.audits[:]=before
                raise
        def commit(self):pytest.fail('caller owns the transaction')
    state.db=DB()
    monkeypatch.setattr(s,'_reader',lambda *args,**kwargs:None)
    def source(*args):state.reads.append(('original_actor_goal_q0_maturity_IO',True));return copy.deepcopy(context)
    monkeypatch.setattr(s,'_context',source)
    monkeypatch.setattr(goal_queries,'call_count',lambda *args:len(state.commands))
    monkeypatch.setattr(native_step_execution,'step_count',lambda *args:0)
    monkeypatch.setattr(native_goal_queries,'step_count',lambda *args:len(state.commands))
    return state


def accept(v,body=None):return s.accept(v.db,v.p,v.task.id,body or v.body)


@pytest.mark.parametrize('extra',['cut_at','generation','model_id','query_id'])
def test_request_cannot_supply_execution_coordinates(extra):
    body={'request_id':uid(1),'expected_goal_hash':'a'*64,'expected_mature_head_hash':'b'*64,'reason':'重新核对',extra:1}
    with pytest.raises(ValidationError):c.AppendRequest.model_validate(body)


def test_accept_and_get_preserve_original_task_and_freeze_real_new_job(scene):
    v=scene;before=copy.deepcopy(v.task.data);result=accept(v)
    assert result['phase']=='queued' and len(v.commands)==len(v.jobs)==len(v.audits)==1
    row=v.commands[0];job=v.jobs[0]
    assert job.kind==c.KIND and job.data['command_ref']=={'id':row.id,'record_hash':row.record_hash}
    assert job.data['approval']['schema']==c.APPROVAL and job.data['inputs']==v.context['inputs']
    assert not {'activation_id','cancel_epoch','cut_at','generation'} & set(job.data)
    assert not v.calls and v.task.data==before and v.reads[0]==('task',True)
    assert s.get_operation(v.db,v.p,v.task.id,v.body['request_id'])==result
    assert v.reads[-1]==('task',False)


def test_same_request_returns_original_without_rechecking_changed_head_or_goal(scene,monkeypatch):
    v=scene;result=accept(v)
    monkeypatch.setattr(s,'_context',lambda *args:pytest.fail('idempotent read must not re-adopt'))
    v.task.data={**v.task.data,'native_goal_context_hash':'f'*64}
    assert accept(v)==result and len(v.jobs)==len(v.commands)==len(v.audits)==1


def test_same_request_different_body_cannot_change_original(scene):
    v=scene;accept(v);before=copy.deepcopy(v.commands[0].data)
    with pytest.raises(HTTPException):accept(v,{**v.body,'reason':'不同请求内容'})
    assert v.commands[0].data==before and len(v.jobs)==1


def test_failed_readback_rolls_back_command_job_and_audit_together(scene,monkeypatch):
    monkeypatch.setattr(s,'_view',lambda *args:(_ for _ in ()).throw(HTTPException(409,'readback IO refusal')))
    with pytest.raises(HTTPException):accept(scene)
    assert not scene.commands and not scene.jobs and not scene.audits


@pytest.mark.parametrize('job_status',['queued','running','failed','cancelled'])
def test_unsettled_original_blocks_another_append_even_after_job_failure(scene,job_status):
    v=scene;accept(v);v.jobs[0].status=job_status
    with pytest.raises(HTTPException,match='尚未结清'):
        accept(v,{**v.body,'request_id':uid(11)})
    assert len(v.commands)==len(v.jobs)==1


def test_failed_job_with_real_pinned_unknown_sdk_still_holds_the_original_operation(scene):
    v=scene;snapshot={'explicit_original_capability_IO':True}
    v.context['cap'].content_hash=digest(snapshot);accept(v)
    record=v.commands[0].data;job=v.jobs[0];job.status='failed'
    v.calls.append(s.Invocation(id=uid(40),workspace_id=v.p.workspace_id,status='unknown',
        capability_id=record['capability_id'],idempotency_key=record['invocation_key'],data={
            'job_id':job.id,'role':'data','side_effect':True,'capability_hash':record['capability_hash'],
            'capability_snapshot':snapshot,'inputs':copy.deepcopy(record['inputs']),
            'input_hash':record['input_hash'],'approval':s.approval_for(record)}))
    result=s.get_operation(v.db,v.p,v.task.id,v.body['request_id'])
    assert result['phase']=='needs_review' and result['invocation_id']==uid(40)
    with pytest.raises(HTTPException,match='尚未结清'):
        accept(v,{**v.body,'request_id':uid(11)})
    assert len(v.jobs)==len(v.commands)==len(v.calls)==1


@pytest.mark.parametrize('fault',['goal','head'])
def test_compare_and_set_uses_actual_original_hashes(scene,fault):
    v=scene;body=copy.deepcopy(v.body)
    body['expected_goal_hash' if fault=='goal' else 'expected_mature_head_hash']='0'*64
    with pytest.raises(HTTPException,match='已变化'):accept(v,body)
    assert not v.commands and not v.jobs


@pytest.mark.parametrize('fault',['missing_command','missing_job','missing_audit','self_rehash','foreign_job'])
def test_orphan_or_self_sealed_inventory_cannot_replace_independent_acceptance(scene,fault):
    v=scene;accept(v)
    if fault=='missing_command':v.commands.clear()
    elif fault=='missing_job':v.jobs.clear()
    elif fault=='missing_audit':v.audits.clear()
    elif fault=='foreign_job':v.jobs[0].workspace_id=uid(99)
    else:
        row=v.commands[0];record=copy.deepcopy(row.data);record['body']['reason']='伪造重封'
        record['body_hash']=c.body_hash(v.task.id,record['body'])
        record['record_hash']=digest({k:value for k,value in record.items() if k!='record_hash'})
        row.data=record;row.body_hash=record['body_hash'];row.record_hash=record['record_hash']
        v.jobs[0].data=s.job_data_for(record)
    with pytest.raises(HTTPException):accept(v)


@pytest.mark.parametrize('fault',['orphan_job','orphan_accepted_audit','new_task_key_only'])
def test_new_nonce_and_budget_cannot_skip_a_missing_command(scene,fault):
    v=scene;accept(v);job=v.jobs[0]
    v.commands.clear()
    if fault=='orphan_accepted_audit':v.jobs.clear()
    else:
        v.audits.clear();job.status='failed'
        if fault=='new_task_key_only':
            # Only the stable original key identifies the requested new Task;
            # its mutable job task pin still contradicts that Task.
            v.task.id=uid(30)
            job.idempotency_key=c.invocation_key(v.task.id,v.body['request_id'])
    before=(len(v.jobs),len(v.audits),len(v.commands))
    source_reads=v.reads.count(('original_actor_goal_q0_maturity_IO',True))
    with pytest.raises(HTTPException):accept(v,{**v.body,'request_id':uid(31)})
    with pytest.raises(HTTPException):s.additional_budget_calls(v.db,v.p,v.task)
    assert (len(v.jobs),len(v.audits),len(v.commands))==before
    assert v.reads.count(('original_actor_goal_q0_maturity_IO',True))==source_reads


@pytest.mark.parametrize('fault',['duplicate_job','duplicate_audit','wrong_command_task'])
def test_all_same_task_commands_require_unique_closed_reverse_inventory(scene,fault):
    v=scene;accept(v)
    if fault=='duplicate_job':
        extra=copy.deepcopy(v.jobs[0]);extra.id=uid(32);extra.idempotency_key+=':duplicate';v.jobs.append(extra)
    elif fault=='duplicate_audit':
        extra=copy.deepcopy(v.audits[0]);extra.id=uid(33);v.audits.append(extra)
    else:v.commands[0].task_id=uid(34)
    with pytest.raises(HTTPException):accept(v,{**v.body,'request_id':uid(35)})
    with pytest.raises(HTTPException):s.additional_budget_calls(v.db,v.p,v.task)


def test_foreign_workspace_orphan_with_same_key_prefix_does_not_poison_own_task(scene):
    v=scene
    v.jobs.append(s.Job(id=uid(36),workspace_id=uid(37),kind=c.KIND,status='failed',
        idempotency_key=c.invocation_key(v.task.id,uid(38)),data={'schema':c.JOB_SCHEMA,'task_id':v.task.id}))
    result=accept(v)
    assert result['phase']=='queued' and len(v.commands)==len(v.audits)==1
    assert s.additional_budget_calls(v.db,v.p,v.task)==1


def test_pending_reservations_are_additional_to_legacy_goal_budget(scene,monkeypatch):
    v=scene;accept(v)
    # A completed-head receipt is an explicit source boundary for this budget
    # case; no claim that a real producer or Goal-check ran is made.
    monkeypatch.setattr(s,'_progress',lambda *args:('completed',None))
    monkeypatch.setattr(goal_queries,'call_count',lambda *args:10)
    with pytest.raises(HTTPException,match='预算'):
        accept(v,{**v.body,'request_id':uid(11)})
    assert s.additional_budget_calls(v.db,v.p,v.task)==1 and len(v.jobs)==1


def test_actor_must_be_the_actual_frozen_data_role(scene,monkeypatch):
    v=scene;binding={'graph':{'nodes':[{'id':'evaluation_packet','kind':'ability','actor_id':uid(99),'execution':{}}]},
        'capability_pins':{'evaluation_packet':{'agent_role':'data'}}}
    monkeypatch.setattr(s.provider,'binding',lambda *args:{'binding':binding})
    with pytest.raises(HTTPException):ACTUAL_CONTEXT(v.db,v.p,v.task)


@pytest.mark.parametrize('fault',[None,'goal_job_running','report_hash'])
def test_mature_completion_is_required_but_current_freshness_is_not(scene,monkeypatch,fault):
    v=scene;reference=v.context['mature']['head']['evaluation_ref']
    row=NS(job_id=uid(20),evaluation_query_id=uid(21),generation=1,source_binding_hash='d'*64)
    head={'evaluation_ref':reference,'query_id':row.evaluation_query_id,'generation':1,
        'source_binding_hash':row.source_binding_hash,'goal_check_job_id':uid(22),'actor_id':v.p.user_id,'head_hash':'b'*64}
    report={'goal_snapshot':s.snapshot(v.task),s.provider.JOB_REF:reference,
        'evidence':{s.provider.VIEW:{'evaluation_ref':reference,'state':'stale'}},'goal_evaluation_head_hash_v288':head['head_hash']}
    memory=NS(id=uid(23),status='candidate',data={'type':'native_goal_check','task_id':v.task.id,'report':report,'report_hash':digest(report)})
    goal_job=NS(status='succeeded',lease_until=None,data={s.provider.JOB_REF:reference,
        s.provider.JOB_PRODUCER:row.job_id,'actor_id':v.p.user_id,'goal_check_id':memory.id})
    if fault=='goal_job_running':goal_job.status='running'
    if fault=='report_hash':memory.data['report_hash']='0'*64
    monkeypatch.setattr(s.provider,'_head',lambda *args:head)
    monkeypatch.setattr(s.provider,'_record',lambda *args,**kwargs:(row,{'explicit_cold_record_IO':True},None))
    monkeypatch.setattr(s.provider,'_fresh',lambda db,p,cls,identity:goal_job if cls is s.Job else memory)
    monkeypatch.setattr(s.provider,'_goal_job',lambda *args:None)
    monkeypatch.setattr(s.provider,'_evidence',lambda *args:pytest.fail('historical maturity is not current freshness'))
    if fault:
        with pytest.raises(HTTPException):s._mature_head(v.db,v.p,v.task,{})
    else:assert s._mature_head(v.db,v.p,v.task,{})['head']==head


@pytest.mark.parametrize('rejected',[False,True])
def test_base_history_delegates_exact_scope_and_propagates_routing_rejection_without_full_sources(scene,monkeypatch,rejected):
    """Actual wrapper only; checked_base authority is an explicit IO boundary.

    The routing and maturity suites separately authenticate their real SDK,
    MATURED and publication inventories; this wrapper must not duplicate them.
    """
    from generic_agents import goal_evaluation_maturity_v299 as maturity
    from generic_agents import goal_history_routing_v302 as routing
    v=scene;accept(v);record=copy.deepcopy(v.commands[0].data)
    selected=v.context['selected'];value={'original_routing_receipt_IO':True};calls=[]
    denial=HTTPException(409,'explicit checked_base original proof rejection')
    def checked_base(db,p,task,actual_selected,actual_record):
        assert db is v.db and p is v.p and task is v.task
        assert actual_selected is selected and actual_record is record
        calls.append((task.id,record['base_evaluation_ref']))
        if rejected:raise denial
        return value
    monkeypatch.setattr(routing,'checked_base',checked_base)
    def forbidden(*args,**kwargs):pytest.fail('base-history wrapper cannot reread full ancestor sources')
    for module,names in ((s,('_mature_value','_rows')),
            (s.provider,('binding','_head','_record','_evidence')),
            (maturity,('verify_head','read_mature_history'))):
        for name in names:monkeypatch.setattr(module,name,forbidden)
    monkeypatch.setattr(v.db,'scalar',forbidden);monkeypatch.setattr(v.db,'scalars',forbidden)
    if rejected:
        with pytest.raises(HTTPException) as error:s._base_history(v.db,v.p,v.task,selected,record)
        assert error.value is denial
    else:assert s._base_history(v.db,v.p,v.task,selected,record) is value
    assert calls==[(v.task.id,record['base_evaluation_ref'])]


@pytest.mark.parametrize('published',[False,True])
def test_completed_operation_requires_new_head_publication_even_with_a_selected_audit(scene,monkeypatch,published):
    from generic_agents import goal_evaluation_maturity_v299 as maturity
    v=scene;accept(v);record=copy.deepcopy(v.commands[0].data);job=v.jobs[0];job.status='succeeded'
    capdata={'explicit_original_capability_IO':True};record['capability_hash']=digest(capdata)
    call=s.Invocation(id=uid(510),workspace_id=v.p.workspace_id,capability_id=record['capability_id'],
        idempotency_key=record['invocation_key'],status='succeeded',data={'job_id':job.id,'role':'data',
            'side_effect':True,'capability_hash':record['capability_hash'],'capability_snapshot':capdata,
            'inputs':record['inputs'],'input_hash':record['input_hash'],'approval':s.approval_for(record)})
    v.calls.append(call)
    head={'evaluation_ref':{'id':call.id,'record_hash':'f'*64},'goal_snapshot_hash':record['goal_snapshot_hash'],
        'goal_check_job_id':uid(511)}
    v.audits.append(s.Audit(id=uid(512),workspace_id=v.p.workspace_id,actor_id=v.p.user_id,
        action=s.provider.SELECTED,object_id=v.task.id,data=head))
    monkeypatch.setattr(s.provider,'binding',lambda *args:{})
    monkeypatch.setattr(s.provider,'_head',lambda *args:head)
    monkeypatch.setattr(s.provider,'_fresh',lambda *args:NS(status='succeeded'))
    monkeypatch.setattr(s,'_mature_value',lambda *args:{'row':NS(job_id=job.id,invocation_id=call.id),
        'record':{'schema':'workflow-goal-evaluation-record-v299','producer':{
            'capability_id':record['capability_id'],'input_hash':record['input_hash']}}})
    calls=[]
    monkeypatch.setattr(maturity,'verify_head',lambda *args:(calls.append(True) or published))
    if published:assert s._progress(v.db,v.p,v.task,record,job)==('completed',call)
    else:
        with pytest.raises(HTTPException):s._progress(v.db,v.p,v.task,record,job)
    assert calls==[True]


@pytest.fixture
def operation_receipt(scene,monkeypatch):
    """Actual GET/status/SDK/Eval/journal checks, publication IO explicit.

    The maturity module has separate actual publication-receipt tests. This
    fixture does not claim original source/PG/IAM or promoter authority proof.
    """
    from generic_agents import workflow_goal_evaluation_append_v299 as producer
    from generic_agents import goal_evaluation_maturity_v299 as maturity
    from generic_agents import workflow_goal_evaluation_contracts_v299 as contract
    from generic_agents.workflow_goal_evaluation_models_v288 import row_values
    from tests.test_goal_evaluation_append_contracts_v299 import record as example
    v=scene;value,_kwargs=example();goal=value['goal_snapshot'];capdata={'actual_capability_snapshot_IO':True}
    v.task.data.update(goal_spec=goal['spec'],goal_hash=goal['hash'],goal_window=goal['time'],
        native_goal_query_id=value['original_query_ref']['query_id'])
    v.context['goal']=s.snapshot(v.task);v.context['q0_ref']=copy.deepcopy(value['original_query_ref'])
    v.context['cap'].content_hash=digest(capdata);v.body['expected_goal_hash']=v.task.data['goal_hash']
    accept(v);command=v.commands[0].data;job=v.jobs[0];job.status='succeeded'
    value.update(evaluation_id=uid(601),generation=2,request_id=job.id,request_key=command['invocation_key'],
        command_ref={'id':command['command_id'],'record_hash':command['record_hash']},goal_snapshot=s.snapshot(v.task))
    value['scope'].update(workspace_id=v.p.workspace_id,task_id=v.task.id,package_id=v.task.package_id,
        goal_snapshot_hash=command['goal_snapshot_hash'],source_binding_hash=command['source_binding_hash'],
        native_goal_context_hash=command['native_goal_context_hash'])
    value['producer'].update(workspace_id=v.p.workspace_id,task_id=v.task.id,scope_hash=digest(value['scope']),
        invocation_id=value['evaluation_id'],job_id=job.id,actor_id=v.p.user_id,capability_id=command['capability_id'],
        capability_hash=command['capability_hash'],input_hash=command['input_hash'],
        request_key_hash=digest(command['invocation_key']),lease_token=uid(602))
    for ack in value['ack_refs']:ack.update(workspace_id=v.p.workspace_id,task_id=v.task.id)
    value['trigger']={'kind':'append_requested','command_ref':value['command_ref'],
        'base_evaluation_ref':command['base_evaluation_ref'],'base_mature_head_hash':command['base_mature_head_hash'],
        'request_hash':command['body_hash']}
    value.pop('record_hash')
    value=contract.seal_record(value,expected_scope=value['scope'],goal_snapshot=value['goal_snapshot'],
        preparation_policy=value['preparation_policy'],trusted_now=value['cut_checked_at'])
    row=s.Evaluation(**row_values(value));v.evaluations.append(row);output=producer.old._output(row)
    journal={'schema':producer.STATE,'command_ref':value['command_ref'],'task_id':v.task.id,'job_id':job.id,
        'invocation_id':row.id,'capability_id':command['capability_id'],'capability_hash':command['capability_hash'],
        'input_hash':command['input_hash'],'approval_hash':digest(s.approval_for(command)),
        'evaluation_record_hash':row.record_hash,'output':output}
    journal['journal_hash']=digest(journal)
    call=s.Invocation(id=row.id,workspace_id=v.p.workspace_id,capability_id=command['capability_id'],status='succeeded',
        idempotency_key=command['invocation_key'],data={'job_id':job.id,'role':'data','side_effect':True,
            'capability_hash':command['capability_hash'],'capability_snapshot':capdata,'inputs':command['inputs'],
            'input_hash':command['input_hash'],'approval':s.approval_for(command),
            'lease_token':value['producer']['lease_token'],'output':output,producer.KEY:journal})
    v.calls.append(call)
    head={'schema':s.provider.HEAD,'task_id':v.task.id,'evaluation_ref':{'id':row.id,'record_hash':row.record_hash},
        'generation':row.generation,'query_id':row.evaluation_query_id,'goal_snapshot_hash':row.goal_snapshot_hash,
        'source_binding_hash':row.source_binding_hash,'goal_check_job_id':uid(603),'actor_id':v.p.user_id,
        'previous_hash':command['base_mature_head_hash']}
    head['head_hash']=digest(head)
    for number,action,object_id,data in (
        (610,'capability.invoke',call.id,{'status':'succeeded'}),
        (611,producer.CREATED,call.id,producer._anchor(journal)),
        (612,s.provider.SELECTED,v.task.id,head),
        (613,maturity.MATURED,v.task.id,{'producer_job_id':job.id})):
        v.audits.append(s.Audit(id=uid(number),workspace_id=v.p.workspace_id,actor_id=v.p.user_id,
            action=action,object_id=object_id,data=copy.deepcopy(data)))
    v.receipt_reads=[];v.promoter_status='succeeded';v.row=row;v.call=call;v.head=head
    def publication(db,p,task,selected):
        assert db is v.db and p is v.p and task is v.task and selected==head
        v.receipt_reads.append(selected);s.need(v.promoter_status=='succeeded')
        return {'row':row,'record':row.data,'head':head,'candidate':{'base_head':{
            'head_hash':command['base_mature_head_hash'],'evaluation_ref':command['base_evaluation_ref']}}}
    monkeypatch.setattr(maturity,'publication_receipt',publication)
    def forbidden(*args,**kwargs):pytest.fail('operation GET must not authenticate current facts or traverse sources')
    for module,names in ((s.provider,('binding','_head','_record','_evidence','require_current')),
            (s,('_progress','_mature_value','_base_history','runner_authority')),
            (maturity,('_evaluation','verify_head')),(producer,('checked_record','saved_result'))):
        for name in names:monkeypatch.setattr(module,name,forbidden)
    v.get=lambda:s.get_operation(v.db,v.p,v.task.id,v.body['request_id'])
    return v


def test_operation_get_uses_positive_completion_receipt_without_reading_any_sources(operation_receipt):
    v=operation_receipt;before=copy.deepcopy((v.task.data,v.call.data,v.row.data));view=v.get()
    assert view['phase']=='completed' and view['invocation_id']==v.call.id and len(v.receipt_reads)==1
    assert view['message']=='后台核对已完成；使用结果时会重新核对时效'
    assert not {'facts','evidence','sufficient','current','evaluation_result'} & set(view)
    assert before==(v.task.data,v.call.data,v.row.data)


def test_operation_get_without_publication_is_waiting_instead_of_completed(operation_receipt):
    from generic_agents import goal_evaluation_maturity_v299 as maturity
    v=operation_receipt;v.audits[:]=[event for event in v.audits if event.action not in {s.provider.SELECTED,maturity.MATURED}]
    assert v.get()['phase']=='awaiting_goal_check' and not v.receipt_reads


@pytest.mark.parametrize('fault',['missing_selected','missing_matured','wrong_sdk','command_ref','output',
    'created_missing','invoke_missing','promoter_running','goal_changed'])
def test_operation_get_cannot_invent_completion_from_partial_or_conflicting_receipts(operation_receipt,fault):
    from generic_agents import goal_evaluation_maturity_v299 as maturity
    from generic_agents import workflow_goal_evaluation_append_v299 as producer
    v=operation_receipt
    if fault=='missing_selected':v.audits[:]=[event for event in v.audits if event.action!=s.provider.SELECTED]
    if fault=='missing_matured':v.audits[:]=[event for event in v.audits if event.action!=maturity.MATURED]
    if fault=='wrong_sdk':v.call.data={**v.call.data,'input_hash':'0'*64}
    if fault=='command_ref':v.row.data={**v.row.data,'command_ref':{'id':uid(999),'record_hash':'0'*64}}
    if fault=='output':v.call.data={**v.call.data,'output':{'plausible':True}}
    if fault=='created_missing':v.audits[:]=[event for event in v.audits if event.action!=producer.CREATED]
    if fault=='invoke_missing':v.audits[:]=[event for event in v.audits if event.action!='capability.invoke']
    if fault=='promoter_running':v.promoter_status='running'
    if fault=='goal_changed':v.task.data={**v.task.data,'goal_hash':'0'*64}
    with pytest.raises(HTTPException):v.get()
