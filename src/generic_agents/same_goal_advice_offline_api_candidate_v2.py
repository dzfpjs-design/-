"""Opt-in, authenticated test-only local document delegation API.

The handler is wired through the normal workspace session/CSRF dependency but
retains the candidate's disposable database guard. It never authorizes model
sending, and no production background executor is installed by this module.
"""
from uuid import UUID
from datetime import timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import func, select
from .config import settings
from .setup_api import P, DB
from .models import Audit, Job, Membership, Plan, User
from . import same_goal_advice_store_v302 as old
from . import same_goal_advice_unlimited_store_v2 as v2
from . import same_goal_advice_offline_grant_candidate_v2 as grants
from . import same_goal_advice_document_lease_candidate_v2 as control
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_offline_worker_candidate_v2 as background
from .contracts import digest
from . import same_goal_advice_dispatch_permit_candidate_v2 as permit
from .temporal import instant

router=APIRouter()
BASE='/tasks/{task_id}/business-advice/offline-delegations'

class Issue(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: UUID

class Revoke(Issue):
    reason: StrictStr = Field(min_length=1,max_length=500)


def _enabled(db):
    if not settings().offline_document_delegation_enabled:
        raise HTTPException(404,'该功能尚未启用')
    guard.require_disposable_database(db)


def _plans(db,p,task_id):
    grants._http_actor(db,p)
    task=old.existing._task(db,p,task_id,lock=True)
    plans=old._rows(db,p,Plan,Plan.task_id==task.id,Plan.data['schema'].as_string()==v2.PLAN)
    return task,plans


def _one(db,p,task,plan):
    if plan.status not in {'queued','awaiting_consent'}: return None
    job_id=plan.data['jobs']['prepare']['id']
    job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==job_id).with_for_update())
    old.need(job is not None)
    prep,checked=(v2.read_prepared(db,p,task,plan) if plan.status=='awaiting_consent'
        else v2.read_queued(db,p,task,plan))
    old.need(job.id==checked.id)
    if p.role!='admin' and p.user_id!=prep['data_actor_id']:return None
    events=old._rows(db,p,Audit,Audit.action==grants.GRANT,Audit.object_id==plan.id)
    entries=[]
    for grant in events:
        value=grants._grant_data(grant,prep,task,plan,job)
        revoked=grants._revocations(db,p,grant)
        now=db.scalar(select(func.clock_timestamp()))
        status='revoked' if revoked else 'expired' if instant(value['expires_at'])<=now else 'authorized'
        entries.append((grant,value,status))
    entries.sort(key=lambda entry:(entry[0].created_at,entry[0].id))
    active=[entry for entry in entries if entry[2]=='authorized']
    old.need(len(active)<=1)
    current=active[0] if active else (entries[-1] if entries else None)
    data_actor_active=db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(
        User.id==prep['data_actor_id'],User.active.is_(True),Membership.workspace_id==p.workspace_id,
        Membership.role.in_(['admin','operator']))) is not None
    visible_state=('role_unavailable' if current and current[2]=='authorized' and not data_actor_active
        else current[2] if current else 'not_authorized')
    permit_exists=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,
        Audit.object_id==plan.id,Audit.action==permit.ACTION).limit(1)) is not None
    packet=prep['request']['document_source']['kind']=='document_packet_v207'
    clock=db.scalar(select(func.clock_timestamp()))
    heartbeats=old._rows(db,p,Job,Job.kind==background.HEARTBEAT_KIND,
        Job.idempotency_key.startswith(background.HEARTBEAT_PREFIX,autoescape=True))
    live=False
    if settings().offline_document_worker_enabled:
        for heartbeat in heartbeats:
            try:
                value=heartbeat.data;worker_id=value['worker_id'];seen=instant(value['checked_at'])
                if (str(UUID(worker_id))!=worker_id or heartbeat.status!='internal'
                    or heartbeat.idempotency_key!=background.HEARTBEAT_PREFIX+worker_id
                    or type(value['pid']) is not int or value['pid']<=0
                    or not clock-timedelta(seconds=20)<=seen<=clock):continue
                if value['state']=='idle' and all(value[key] is None for key in ('control_id','lease_hash','lease_until')):
                    live=True;break
                if value['state']=='processing':
                    active=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==value['control_id'],
                        Job.kind==control.KIND,Job.status=='running',Job.lease_until>clock))
                    if (active is not None and active.lease_until==instant(value['lease_until'])
                            and digest(active.lease_token)==value['lease_hash']):live=True;break
            except (ValueError,TypeError,KeyError,AttributeError):continue
    ctl=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='advice-v2-fence:'+job.id))
    work_state='complete' if job.status=='succeeded' else 'waiting'
    failure_kind=None
    if ctl is not None:
        expected={'schema':control.SCHEMA,'actor_id':prep['data_actor_id'],'target':{'task_id':task.id,
            'job_id':job.id,'plan_id':plan.id,'preparation_hash':prep['record_hash']}}
        registrations=old._rows(db,p,Audit,Audit.action==control.REGISTERED,Audit.object_id==ctl.id)
        old.need(ctl.kind==control.KIND and ctl.data==expected and len(registrations)==1
            and registrations[0].actor_id==prep['data_actor_id']
            and registrations[0].data=={'schema':control.SCHEMA,'control_hash':digest(expected)})
        if job.status!='succeeded' and current:
            failure=db.scalar(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==background.FAILURE,
                Audit.object_id==ctl.id,Audit.data['grant_id'].as_string()==current[0].id).limit(1))
            if failure is not None and type(failure.data) is dict:
                failure_kind=failure.data.get('kind') if failure.data.get('kind') in {'access_or_source','database','internal'} else 'internal'
            work_state='needs_attention' if failure else 'processing' if ctl.status=='running' else 'waiting'
    return {'job_id':job.id,'plan_id':plan.id,'task_title':task.title,'source_label':'已批准的业务资料包' if packet else '已批准的资料集合',
        'state':visible_state,'expires_at':current[1]['expires_at'] if current else None,
        'grant_id':current[0].id if current else None,
        'grant_request_id':current[1]['request_id'] if current else None,
        'revoke_request_id':grants._revocations(db,p,current[0])[0].data['request_id']
            if current and current[2]=='revoked' else None,
        'prepared':job.status=='succeeded',
        'can_authorize':p.user_id==prep['data_actor_id'] and packet and job.status=='queued' and not active,
        'can_revoke':bool(current and current[2]=='authorized' and (p.user_id==prep['data_actor_id'] or p.role=='admin')),
        'background_available':live,'work_state':work_state,'failure_kind':failure_kind,'permit_committed':permit_exists}


@router.get(BASE)
def listing(task_id:str,p:P,db:DB):
    if not settings().offline_document_delegation_enabled:return {'enabled':False,'items':[]}
    _enabled(db);task,plans=_plans(db,p,task_id)
    items=[item for plan in plans if (item:=_one(db,p,task,plan)) is not None]
    permit_exists=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,
        Audit.object_id.in_([plan.id for plan in plans]),Audit.action==permit.ACTION).limit(1)) is not None
    return {'enabled':True,'items':items,'permit_committed':permit_exists}


@router.post(BASE+'/{job_id}/grant')
def issue(task_id:str,job_id:str,body:Issue,p:P,db:DB):
    _enabled(db)
    # The control record is durable, but a separately enabled test executor is
    # still required. Creating it never starts a model or device.
    control.register(db,p,task_id,job_id)
    _,_,_,prep=grants._original(db,p,task_id,job_id)
    old.need(prep['request']['document_source']['kind']=='document_packet_v207',
        '此资料类型的后台整理尚未接通，未保存授权')
    result=grants.issue(db,p,task_id,job_id,str(body.request_id));db.commit()
    return {'grant_id':result['grant_id'],'state':'authorized','background_available':False}


@router.post(BASE+'/{job_id}/grants/{grant_id}/revoke')
def revoke(task_id:str,job_id:str,grant_id:str,body:Revoke,p:P,db:DB):
    _enabled(db)
    result=grants.revoke(db,p,task_id,job_id,grant_id,str(body.request_id),body.reason);db.commit()
    return {'grant_id':result['grant_id'],'state':'revoked'}
