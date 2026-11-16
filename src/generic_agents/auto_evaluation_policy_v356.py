"""Opt-in task-bound authority for automatic *new* independent evaluations.

This module grants no scheduler or execution authority by itself. Every use
must revalidate policy and all the original append/approval guards.
"""
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import CheckConstraint, DDL, ForeignKey, String, UniqueConstraint, select, func, event
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload, Audit
from .contracts import digest
from .services import audit
from .goal_evaluation_append_store_v297 import _reader, _task, _context

class AutoEvaluationPolicyV356(Row, Scoped, Payload, Base):
    __tablename__='auto_evaluation_policies_v356'
    __table_args__=(UniqueConstraint('workspace_id','task_id'),)
    task_id: Mapped[str]=mapped_column(ForeignKey('tasks.id'),index=True)
    status: Mapped[str]=mapped_column(String(20))

class AutoEvaluationPolicyCommandV356(Row, Scoped, Payload, Base):
    __tablename__='auto_evaluation_policy_commands_v356'
    __table_args__=(UniqueConstraint('workspace_id','task_id','request_id'),CheckConstraint('version = 1',name='ck_auto_evaluation_policy_command_v356_version'))
    task_id: Mapped[str]=mapped_column(ForeignKey('tasks.id'),index=True)
    request_id: Mapped[str]=mapped_column(String(36))
    actor_id: Mapped[str]=mapped_column(ForeignKey('users.id'))


class AutoEvaluationPolicyRetirementV356(Row, Scoped, Payload, Base):
    __tablename__='auto_evaluation_policy_retirements_v356'
    __table_args__=(UniqueConstraint('workspace_id','task_id','request_id'),CheckConstraint('version = 1',name='ck_auto_evaluation_policy_retirement_v356_version'))
    task_id: Mapped[str]=mapped_column(ForeignKey('tasks.id'),index=True)
    request_id: Mapped[str]=mapped_column(String(36))
    actor_id: Mapped[str]=mapped_column(ForeignKey('users.id'))


PG_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION ga_auto_evaluation_policy_command_v356_immutable()
RETURNS trigger LANGUAGE plpgsql AS $v356$
BEGIN
  RAISE EXCEPTION 'AutoEvaluationPolicyCommandV356 records are append-only' USING ERRCODE = '55000';
END;
$v356$;
"""
PG_GUARD_TRIGGER = """
CREATE TRIGGER tr_auto_evaluation_policy_command_v356_immutable
BEFORE UPDATE OR DELETE ON %(fullname)s
FOR EACH ROW EXECUTE FUNCTION ga_auto_evaluation_policy_command_v356_immutable()
"""
event.listen(AutoEvaluationPolicyCommandV356.__table__,'after_create',DDL(PG_GUARD_FUNCTION).execute_if(dialect='postgresql'))
event.listen(AutoEvaluationPolicyCommandV356.__table__,'after_create',DDL(PG_GUARD_TRIGGER).execute_if(dialect='postgresql'))
event.listen(AutoEvaluationPolicyRetirementV356.__table__,'after_create',DDL(PG_GUARD_FUNCTION).execute_if(dialect='postgresql'))
event.listen(AutoEvaluationPolicyRetirementV356.__table__,'after_create',DDL(PG_GUARD_TRIGGER.replace('tr_auto_evaluation_policy_command_v356_immutable','tr_auto_evaluation_policy_retirement_v356_immutable')).execute_if(dialect='postgresql'))


def current(db,p,task_id,lock=False):
    query=select(AutoEvaluationPolicyV356).where(AutoEvaluationPolicyV356.workspace_id==p.workspace_id,AutoEvaluationPolicyV356.task_id==task_id)
    if lock:query=query.with_for_update()
    return db.scalar(query.execution_options(populate_existing=True))


def view(row):
    if row is None:return {'enabled':False,'version':0,'policy':None}
    return {'enabled':row.status=='enabled','version':row.version,'policy':row.data}



def _checked_command(db,p,task,command):
    """Cold-check an immutable command against its independently written audit."""
    bad='自动评价策略授权记录无法核对，已停止自动执行'
    try:
        data=command.data;body=data['body'];result=data['result'];snapshot=result['policy_snapshot']
        action=data['action'];expected_version=body['expected_version']
        assert command.workspace_id==p.workspace_id and command.task_id==task.id
        assert command.version==1 and action in ('enable','revoke')
        assert command.request_id==body['request_id']==result['request_id'] and result['action']==action
        assert data['body_hash']==digest({'action':action,'body':body})
        assert type(expected_version) is int and expected_version>=0 and snapshot['version']==expected_version+1
        assert snapshot['enabled']==(action=='enable') and type(snapshot['policy']) is dict
        values=snapshot['policy'];raw={k:v for k,v in values.items() if k!='policy_hash'}
        assert values['policy_hash']==digest(raw) and values['workspace_id']==p.workspace_id and values['task_id']==task.id
        if action=='enable':
            assert values['actor_id']==command.actor_id
            assert values['min_interval_minutes']==body['min_interval_minutes'] and values['max_evaluations_per_day']==body['max_evaluations_per_day']
        details={'request_id':command.request_id,'policy_version':snapshot['version'],'policy_hash':values['policy_hash'],'reason':body['reason']}
        events=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==task.id,
            Audit.action=='auto_evaluation_policy.'+action,Audit.data['request_id'].as_string()==command.request_id).limit(2)))
        assert len(events)==1 and events[0].actor_id==command.actor_id and events[0].data==details
        return result
    except (KeyError,TypeError,ValueError,AssertionError,AttributeError):
        raise HTTPException(409,bad) from None


def _checked_current(db,p,task,row):
    if row is None:return
    commands=list(db.scalars(select(AutoEvaluationPolicyCommandV356).where(
        AutoEvaluationPolicyCommandV356.workspace_id==p.workspace_id,AutoEvaluationPolicyCommandV356.task_id==task.id,
        AutoEvaluationPolicyCommandV356.data['result']['policy_snapshot']['version'].as_integer()==row.version).limit(2)))
    if len(commands)!=1:raise HTTPException(409,'自动评价策略授权记录无法核对，已停止自动执行')
    result=_checked_command(db,p,task,commands[0])
    if result['policy_snapshot']!=view(row):raise HTTPException(409,'自动评价策略与授权记录不一致，已停止自动执行')

def _signature(task,context,p,minutes,maximum,enabled_at):
    return {'workspace_id':p.workspace_id,'task_id':task.id,'instance_id':task.data['instance_id'],
        'release_id':context['selected']['release'].id,'actor_id':p.user_id,
        'goal_hash':context['goal']['hash'],'entity_ids':sorted(task.data['entity_ids']),
        'capability_id':context['cap'].id,'capability_hash':context['cap'].content_hash,
        'min_interval_minutes':minutes,'max_evaluations_per_day':maximum,'enabled_at':enabled_at}


def _local_context(db,p,task):
    context=_context(db,p,task)
    # Only the fixed, built-in local evaluation packet is eligible. Older
    # manifests omitted deployment/external_allowed; reject explicit contrary
    # values while still requiring cloud_allowed=false and exact adapter.
    from .workflow_goal_evaluation_packet_v288 import validate_manifest
    policy=context['cap'].data.get('data_policy',{})
    if type(policy) is not dict or policy.get('deployment','local')!='local' or policy.get('cloud_allowed') is not False or policy.get('external_allowed',False) is not False:
        raise HTTPException(409,'自动评价仅允许无外部访问的本地评价能力')
    validate_manifest(context['cap'].data)
    return context


def verify(db,p,task,row):
    """Caller must hold Task then Policy locks, and keep them through use."""
    if row is None or row.status!='enabled' or row.workspace_id!=p.workspace_id or row.task_id!=task.id:
        raise HTTPException(409,'自动评价策略未启用或范围不符')
    _reader(db,p,write=True)
    from .native_goals import manage
    manage(p,task)
    _checked_current(db,p,task,row)
    context=_local_context(db,p,task)
    data=row.data;expected=_signature(task,context,p,data.get('min_interval_minutes'),data.get('max_evaluations_per_day'),data.get('enabled_at'))
    from .temporal import instant
    instant(data.get('enabled_at'))
    if type(expected['min_interval_minutes']) is not int or not 1<=expected['min_interval_minutes']<=10080 or type(expected['max_evaluations_per_day']) is not int or not 1<=expected['max_evaluations_per_day']<=100 or data!={**expected,'policy_hash':digest(expected)}:
        raise HTTPException(409,'自动评价策略或原任务授权已改变，请重新确认')
    return context


def read(db,p,task_id):
    _reader(db,p);task=_task(db,p,task_id);row=current(db,p,task.id);_checked_current(db,p,task,row);return view(row)



def _retirement(db,p,task_id,request_id):
    return db.scalar(select(AutoEvaluationPolicyRetirementV356).where(
        AutoEvaluationPolicyRetirementV356.workspace_id==p.workspace_id,
        AutoEvaluationPolicyRetirementV356.task_id==task_id,
        AutoEvaluationPolicyRetirementV356.request_id==request_id))


def _checked_retirement(db,p,task,row):
    try:
        assert row.workspace_id==p.workspace_id and row.task_id==task.id and row.version==1
        assert row.actor_id==p.user_id
        expected={'request_id':row.request_id,'retirement_id':row.id,'reason':row.data['reason']}
        rows=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==task.id,
            Audit.action=='auto_evaluation_policy.retire',Audit.data['retirement_id'].as_string()==row.id).limit(2)))
        assert len(rows)==1 and rows[0].actor_id==row.actor_id and rows[0].data==expected
    except (KeyError,TypeError,ValueError,AssertionError,AttributeError):
        raise HTTPException(409,'原请求退役凭证无法核对') from None


def retire(db,p,task_id,request_id,reason):
    _reader(db,p,write=True);task=_task(db,p,task_id,lock=True)
    from .native_goals import manage
    manage(p,task)
    try:
        if str(UUID(request_id))!=request_id:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请求编号无效') from None
    command=db.scalar(select(AutoEvaluationPolicyCommandV356).where(AutoEvaluationPolicyCommandV356.workspace_id==p.workspace_id,
        AutoEvaluationPolicyCommandV356.task_id==task.id,AutoEvaluationPolicyCommandV356.request_id==request_id))
    retired=_retirement(db,p,task.id,request_id)
    if command is not None:
        if retired is not None or command.actor_id!=p.user_id:raise HTTPException(409,'原请求归属或状态无法核对')
        return {'request_id':request_id,'phase':'recorded','operation':_checked_command(db,p,task,command)}
    if retired is not None:
        _checked_retirement(db,p,task,retired)
        if retired.data['reason']!=reason:raise HTTPException(409,'该请求已有不同退役理由')
    else:
        retired=AutoEvaluationPolicyRetirementV356(workspace_id=p.workspace_id,task_id=task.id,
            request_id=request_id,actor_id=p.user_id,version=1,data={'reason':reason})
        db.add(retired);db.flush()
        audit(db,p,'auto_evaluation_policy.retire',task.id,{'request_id':request_id,'retirement_id':retired.id,'reason':reason})
    return {'request_id':request_id,'phase':'retired','operation':None}

def operation(db,p,task_id,request_id):
    _reader(db,p);task=_task(db,p,task_id)
    try:
        if str(UUID(request_id))!=request_id:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请求编号无效') from None
    command=db.scalar(select(AutoEvaluationPolicyCommandV356).where(AutoEvaluationPolicyCommandV356.workspace_id==p.workspace_id,AutoEvaluationPolicyCommandV356.task_id==task.id,AutoEvaluationPolicyCommandV356.request_id==request_id))
    if command is None:raise HTTPException(404,'未找到原策略操作')
    if command.actor_id!=p.user_id:raise HTTPException(409,'原策略操作属于其他账号')
    return _checked_command(db,p,task,command)


def change(db,p,task_id,action,body):
    _reader(db,p,write=True);task=_task(db,p,task_id,lock=True)
    from .native_goals import manage
    manage(p,task)
    retired=_retirement(db,p,task.id,body['request_id'])
    if retired is not None:
        _checked_retirement(db,p,task,retired)
        raise HTTPException(409,'该原请求已退役，不能再提交')
    old=db.scalar(select(AutoEvaluationPolicyCommandV356).where(AutoEvaluationPolicyCommandV356.workspace_id==p.workspace_id,AutoEvaluationPolicyCommandV356.task_id==task.id,AutoEvaluationPolicyCommandV356.request_id==body['request_id']))
    body_hash=digest({'action':action,'body':body})
    if old:
        if old.actor_id!=p.user_id or old.data.get('body_hash')!=body_hash:raise HTTPException(409,'请求编号已用于其他策略操作')
        return _checked_command(db,p,task,old)
    row=current(db,p,task.id,lock=True);version=row.version if row else 0
    if version!=body['expected_version']:raise HTTPException(409,'策略已发生变化，请重新读取')
    _checked_current(db,p,task,row)
    if action=='enable':
        context=_local_context(db,p,task)
        values=_signature(task,context,p,body['min_interval_minutes'],body['max_evaluations_per_day'],db.scalar(select(func.clock_timestamp())).isoformat())
        payload={**values,'policy_hash':digest(values)}
        if row is None:
            row=AutoEvaluationPolicyV356(workspace_id=p.workspace_id,task_id=task.id,status='enabled',version=1,data=payload);db.add(row)
        else:row.version+=1;row.status='enabled';row.data=payload
    elif action=='revoke':
        if row is None or row.status!='enabled':raise HTTPException(409,'策略未启用')
        row.version+=1;row.status='revoked'
    else:raise ValueError('unknown action')
    db.flush()
    result={'request_id':body['request_id'],'action':action,'policy_snapshot':view(row)}
    command=AutoEvaluationPolicyCommandV356(workspace_id=p.workspace_id,task_id=task.id,request_id=body['request_id'],actor_id=p.user_id,version=1,data={'body_hash':body_hash,'body':body,'action':action,'result':result})
    db.add(command);audit(db,p,'auto_evaluation_policy.'+action,task.id,{'request_id':body['request_id'],'policy_version':row.version,'policy_hash':row.data['policy_hash'],'reason':body['reason']})
    return result
