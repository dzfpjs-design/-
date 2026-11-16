from typing import Annotated
from datetime import timedelta
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field
from sqlalchemy import select
from .db import session
from .security import authenticated,Principal,require
from .contracts import Contract,VersionAction
from .models import QualityPolicy,Issue,IssueCheck,Job,now
from .services import scoped,published,check_version,as_dict,audit
from . import quality

router=APIRouter(prefix='/api/quality')
P=Annotated[Principal,Depends(authenticated)]
DB=Annotated[object,Depends(session)]

class PolicyCreate(Contract):
    name:str=Field(min_length=1,max_length=200)
    package_ids:list[str]=Field(min_length=1,max_length=10)
    interval_seconds:int=Field(ge=60,le=86400)
    reason:str=Field(min_length=1,max_length=2000)

@router.get('')
def overview(p:P,db:DB):
    rows=[]
    for policy in db.scalars(select(QualityPolicy).where(QualityPolicy.workspace_id==p.workspace_id)
        .order_by(QualityPolicy.created_at.desc()).limit(100)):
        job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='diagnose_data',
            Job.data['policy_id'].as_string()==policy.id).order_by(Job.created_at.desc()).limit(1))
        rows.append({**as_dict(policy),'latest_job':{'status':job.status,'error':job.data.get('error'),
            'attempts':job.attempts,'created_at':job.created_at.isoformat()} if job else None})
    return {'policies':rows}

@router.post('/policies',status_code=201)
def create(body:PolicyCreate,p:P,db:DB):
    require(p,'admin')
    if len(body.package_ids)!=len(set(body.package_ids)):raise HTTPException(422,'业务包不能重复')
    for identifier in body.package_ids:published(db,p,identifier,'domain_package')
    row=QualityPolicy(workspace_id=p.workspace_id,name=body.name,actor_id=p.user_id,
        next_run_at=now()+timedelta(seconds=body.interval_seconds),data={
            'package_ids':body.package_ids,'interval_seconds':body.interval_seconds,'approval_reason':body.reason})
    db.add(row);db.flush();quality.enqueue(db,row,'activation')
    audit(db,p,'quality.policy_approved',row.id,{'reason':body.reason});db.commit();return as_dict(row)

@router.post('/policies/{identifier}/pause')
def pause(identifier:str,body:VersionAction,p:P,db:DB):
    require(p,'admin');row=scoped(db,QualityPolicy,identifier,p,True);check_version(row,body.expected_version)
    if row.status!='active':raise HTTPException(409,'策略当前未启用')
    row.status='paused';row.version+=1;row.data={**row.data,'pause_reason':body.reason}
    audit(db,p,'quality.policy_paused',row.id,{'reason':body.reason});db.commit();return as_dict(row)

@router.post('/policies/{identifier}/resume')
def resume(identifier:str,body:VersionAction,p:P,db:DB):
    require(p,'admin');row=scoped(db,QualityPolicy,identifier,p,True);check_version(row,body.expected_version)
    if row.status not in {'paused','blocked_authorization'}:raise HTTPException(409,'策略当前不需要重新批准')
    row.status='active';row.actor_id=p.user_id;row.version+=1
    row.next_run_at=now()+timedelta(seconds=row.data['interval_seconds'])
    row.data={**row.data,'authorization_error':None,'resume_reason':body.reason}
    quality.enqueue(db,row,'resumed')
    audit(db,p,'quality.policy_resumed',row.id,{'reason':body.reason});db.commit();return as_dict(row)

@router.get('/issues/{identifier}/history')
def history(identifier:str,p:P,db:DB):
    scoped(db,Issue,identifier,p)
    return [as_dict(r) for r in db.scalars(select(IssueCheck).where(IssueCheck.workspace_id==p.workspace_id,
        IssueCheck.issue_id==identifier).order_by(IssueCheck.created_at.desc()).limit(100))]

@router.post('/issues/{identifier}/verify')
def verify(identifier:str,body:VersionAction,p:P,db:DB):
    require(p,'admin','operator')
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:workspace,0))'),{'workspace':p.workspace_id})
    row=scoped(db,Issue,identifier,p,True);check_version(row,body.expected_version)
    if row.status!='open':raise HTTPException(409,'问题已经结案，原验证记录继续保留')
    if row.kind not in {'relation_source','stale','invalid_schema'}:raise HTTPException(409,'该问题尚无已注册结案验证器')
    verified=quality.verify(db,p,row,body.reason);db.commit()
    return {'verified':verified,'issue':as_dict(row)}
