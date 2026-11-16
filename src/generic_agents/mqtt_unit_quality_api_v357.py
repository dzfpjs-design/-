"""Bounded MQTT diagnostic evidence and restricted recovery of its original job."""
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictInt,StrictStr
from sqlalchemy import select
from .setup_api import P,DB
from .models import Issue,Job,MqttGateway
from .mqtt_unit_quality_v357 import KIND
from .mqtt_unit_quality_worker_v357 import gateway_status
from .mqtt_unit_quality_view_v357 import view_issue
from .services import scoped

router=APIRouter(prefix='/api/mqtt')

class RetryDiagnostic(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:StrictStr
    expected_version:StrictInt=Field(ge=1)
    reason:StrictStr=Field(min_length=1,max_length=500)

@router.get('/gateways/{identity}/unit-quality')
def read_unit_quality(identity:str,p:P,db:DB):
    result=gateway_status(db,p,identity)
    gateway=scoped(db,MqttGateway,identity,p)
    rows=db.scalars(select(Issue).where(Issue.workspace_id==p.workspace_id,Issue.kind==KIND,
        Issue.data['proof']['gateway_id'].as_string()==identity).order_by(Issue.created_at.desc()).limit(101)).all()
    issues=[]
    for row in rows[:100]:
        try:issues.append(view_issue(db,p,row,gateway))
        except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
            issues.append({'reference':row.id,'status':'evidence_unverifiable',
                'reason':'原报文或检测依据无法核对，原记录保留；不能换算或结案。',
                'can_convert':False,'can_close':False})
    return {**result,'issues':issues,'issues_has_more':len(rows)>100}


@router.post('/gateways/{identity}/unit-quality/jobs/{job_id}/retry')
def retry_unit_quality(identity:str,job_id:str,body:RetryDiagnostic,p:P,db:DB):
    from .mqtt_unit_quality_retry_v357 import retry
    scoped(db,MqttGateway,identity,p)
    job=scoped(db,Job,job_id,p)
    if job.data.get('gateway_id')!=identity:raise HTTPException(404,'原诊断不属于该网关')
    command=retry(db,p,job_id,body.request_id,body.expected_version,body.reason)
    receipt={'state':'accepted','receipt':command.id,'request_id':command.request_id,
        'job_id':command.job_id,'subject_id':command.actor_id,'body_hash':command.data['body_hash']}
    db.commit()
    # This is a historical receipt, never a claim that a worker is running.
    return {**receipt,'accepted':True,'same_job':True,
        'message':'恢复请求已记录；请刷新状态查看后续进展。'}


def _original_job(db,p,identity,job_id):
    scoped(db,MqttGateway,identity,p)
    job=scoped(db,Job,job_id,p)
    if job.data.get('gateway_id')!=identity:raise HTTPException(404,'原诊断不属于该网关')


@router.get('/gateways/{identity}/unit-quality/jobs/{job_id}/requests/{request_id}')
def read_retry_request(identity:str,job_id:str,request_id:str,p:P,db:DB):
    from .mqtt_unit_quality_retry_v357 import request_status
    _original_job(db,p,identity,job_id)
    return request_status(db,p,job_id,request_id)


@router.post('/gateways/{identity}/unit-quality/jobs/{job_id}/requests/{request_id}/retire')
def retire_retry_request(identity:str,job_id:str,request_id:str,p:P,db:DB):
    from .mqtt_unit_quality_retry_v357 import retire_request
    _original_job(db,p,identity,job_id)
    result=retire_request(db,p,job_id,request_id)
    db.commit()
    return result
