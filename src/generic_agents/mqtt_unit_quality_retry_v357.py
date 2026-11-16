"""Idempotently resume only the original failed local unit diagnostic.

No MQTT message, device action, model call, corrected observation or fact update
is performed. The caller commits; the original Job is reused, never replaced.
"""
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import DDL,ForeignKey,String,UniqueConstraint,event,select,text
from sqlalchemy.orm import Mapped,mapped_column
from .contracts import digest
from .models import Base,Row,Scoped,Payload,Audit,Job,Membership,User,now
from .mqtt_unit_quality_dispatch_v357 import KIND,verify
from .services import scoped

class MqttUnitDiagnosticRetryV357(Row,Scoped,Payload,Base):
    __tablename__='mqtt_unit_diagnostic_retries_v357'
    __table_args__=(UniqueConstraint('workspace_id','request_id'),)
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id'),index=True)
    request_id:Mapped[str]=mapped_column(String(36))
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))

class MqttUnitDiagnosticRetryRetirementV357(Row,Scoped,Payload,Base):
    __tablename__='mqtt_unit_diagnostic_retry_retirements_v357'
    __table_args__=(UniqueConstraint('workspace_id','request_id'),)
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id'),index=True)
    request_id:Mapped[str]=mapped_column(String(36))
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))

_guard="""CREATE OR REPLACE FUNCTION ga_mqtt_unit_retry_v357_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'unit diagnostic recovery evidence is immutable' USING ERRCODE='55000'; END; $$;"""
event.listen(MqttUnitDiagnosticRetryV357.__table__,'after_create',DDL(_guard).execute_if(dialect='postgresql'))
event.listen(MqttUnitDiagnosticRetryV357.__table__,'after_create',DDL('CREATE TRIGGER ga_mqtt_unit_retry_v357_guard BEFORE UPDATE OR DELETE ON %(fullname)s FOR EACH ROW EXECUTE FUNCTION ga_mqtt_unit_retry_v357_immutable()').execute_if(dialect='postgresql'))
event.listen(MqttUnitDiagnosticRetryRetirementV357.__table__,'after_create',DDL(_guard).execute_if(dialect='postgresql'))
event.listen(MqttUnitDiagnosticRetryRetirementV357.__table__,'after_create',DDL('CREATE TRIGGER ga_mqtt_unit_retry_retirement_v357_guard BEFORE UPDATE OR DELETE ON %(fullname)s FOR EACH ROW EXECUTE FUNCTION ga_mqtt_unit_retry_v357_immutable()').execute_if(dialect='postgresql'))


def _need(ok,message='原诊断请求或接收依据无法核对；原记录保留'):
    if not ok:raise HTTPException(409,message)


def _member(db,p):
    if p.role not in {'admin','operator'}:raise HTTPException(403,'当前账号无权恢复此诊断')
    member=db.scalar(select(Membership).join(User,User.id==Membership.user_id).where(
        Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id,
        Membership.role==p.role,User.active.is_(True)).with_for_update(read=True))
    if member is None:raise HTTPException(403,'当前账号的工作区权限已改变')


def _saved(db,p,row,body):
    _need(row.actor_id==p.user_id and row.data.get('body')==body and row.data.get('body_hash')==digest(body))
    events=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action=='quality.mqtt_unit_diagnostic_retry_v357',Audit.object_id==row.id).limit(2)))
    _need(len(events)==1 and events[0].actor_id==p.user_id and events[0].data=={
        'request_id':row.request_id,'job_id':row.job_id,'body_hash':row.data['body_hash']})
    return row


def _lock_request(db,p,request_id):
    try:
        if type(request_id) is not str or str(UUID(request_id))!=request_id:raise ValueError()
    except (TypeError,ValueError,AttributeError):raise HTTPException(422,'请求编号无效') from None
    acquired=db.scalar(text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'),
        {'key':'mqtt-unit-retry-v357:'+p.workspace_id+':'+request_id})
    _need(acquired,'原恢复请求正在处理，请稍后查询同一请求')


def _job(db,p,job_id):
    job=scoped(db,Job,job_id,p,lock=True)
    _need(job.kind==KIND and job.data.get('actor_id')==p.user_id,'须由原执行账号恢复该诊断；原任务保留')
    return job


def _history(db,p,job,request_id):
    old=db.scalar(select(MqttUnitDiagnosticRetryV357).where(MqttUnitDiagnosticRetryV357.workspace_id==p.workspace_id,
        MqttUnitDiagnosticRetryV357.request_id==request_id))
    retired=db.scalar(select(MqttUnitDiagnosticRetryRetirementV357).where(
        MqttUnitDiagnosticRetryRetirementV357.workspace_id==p.workspace_id,
        MqttUnitDiagnosticRetryRetirementV357.request_id==request_id))
    _need(not (old and retired),'原恢复请求记录冲突，需要核查')
    if old:
        _need(old.job_id==job.id and isinstance(old.data.get('body'),dict)
            and old.data['body'].get('job_id')==job.id,'请求编号已用于其他诊断')
        _saved(db,p,old,old.data['body'])
        return {'state':'accepted','receipt':old.id,'request_id':request_id,'job_id':job.id,
            'subject_id':old.actor_id,'body_hash':old.data['body_hash']}
    if retired:
        _need(retired.job_id==job.id and retired.actor_id==p.user_id,'请求编号已用于其他诊断')
        expected={'request_id':request_id,'job_id':job.id,'actor_id':p.user_id}
        _need(retired.data==expected)
        audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
            Audit.action=='quality.mqtt_unit_diagnostic_retry_retired_v357',Audit.object_id==retired.id).limit(2)))
        _need(len(audits)==1 and audits[0].actor_id==p.user_id and audits[0].data==expected)
        return {'state':'retired','receipt':retired.id,'request_id':request_id,
            'job_id':job.id,'subject_id':retired.actor_id}
    return {'state':'unknown','retire_required':True}


def request_status(db,p,job_id,request_id):
    _member(db,p);_lock_request(db,p,request_id)
    return _history(db,p,_job(db,p,job_id),request_id)


def retire_request(db,p,job_id,request_id):
    _member(db,p);_lock_request(db,p,request_id);job=_job(db,p,job_id)
    result=_history(db,p,job,request_id)
    if result['state']!='unknown':return result
    data={'request_id':request_id,'job_id':job.id,'actor_id':p.user_id}
    row=MqttUnitDiagnosticRetryRetirementV357(workspace_id=p.workspace_id,request_id=request_id,
        job_id=job.id,actor_id=p.user_id,data=data)
    db.add(row);db.flush()
    db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,
        action='quality.mqtt_unit_diagnostic_retry_retired_v357',object_id=row.id,data=data))
    return {'state':'retired','receipt':row.id,'request_id':request_id,
        'job_id':job.id,'subject_id':p.user_id}


def retry(db,p,job_id,request_id,expected_version,reason):
    if type(expected_version) is not int or expected_version<1 or type(reason) is not str or not reason.strip() or len(reason)>500:
        raise HTTPException(422,'请核对版本并填写不超过 500 字的恢复原因')
    _member(db,p);_lock_request(db,p,request_id);job=_job(db,p,job_id)
    body={'job_id':job.id,'expected_version':expected_version,'reason':reason}
    history=_history(db,p,job,request_id)
    _need(history['state']!='retired','该恢复请求已退役，请创建新的请求')
    old=db.scalar(select(MqttUnitDiagnosticRetryV357).where(MqttUnitDiagnosticRetryV357.workspace_id==p.workspace_id,
        MqttUnitDiagnosticRetryV357.request_id==request_id))
    if old is not None:
        _need(old.job_id==job.id);return _saved(db,p,old,body)
    _need(job.version==expected_version,'原诊断状态已变化，请先重新核对')
    _need(job.status=='failed' and job.lease_until is None and job.attempts<3
        and not job.data.get('recovery_only') and 'unit_quality_result' not in job.data,
        '原诊断未满足安全恢复条件，未创建新任务')
    verify(db,p,job)
    failure={'error':job.data.get('error'),'finished_at':job.data.get('finished_at'),'attempts':job.attempts}
    payload={'body':body,'body_hash':digest(body),'previous_failure':failure,
        'same_job':True,'mqtt_republished':False,'device_action':False}
    command=MqttUnitDiagnosticRetryV357(workspace_id=p.workspace_id,job_id=job.id,
        request_id=request_id,actor_id=p.user_id,data=payload)
    db.add(command);db.flush()
    history=job.data.get('unit_quality_retry_history',[])
    _need(isinstance(history,list) and len(history)<3)
    clean={k:v for k,v in job.data.items() if k not in {'error','finished_at'}}
    job.data={**clean,'unit_quality_retry_history':[*history,{'command_id':command.id,'body_hash':payload['body_hash'],
        'attempts':job.attempts,'previous_failure':failure}]}
    job.status='queued';job.available_at=now();job.lease_until=None;job.lease_token=None;job.version+=1
    db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action='quality.mqtt_unit_diagnostic_retry_v357',
        object_id=command.id,data={'request_id':request_id,'job_id':job.id,'body_hash':payload['body_hash']}))
    return command
