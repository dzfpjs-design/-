"""Consume only original-backed unit checks for one workspace and gateway."""
import signal,threading
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select,or_,and_,func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from .db import engine
from .models import Job,Event,MqttGateway,User,Membership
from .security import Principal
from .mqtt_unit_quality_dispatch_v357 import KIND,verify


def _uuid(value):
    try:return type(value) is str and str(UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False


def _scope(workspace,gateway):
    if not _uuid(workspace) or not _uuid(gateway):raise ValueError('须指定原工作区和网关')


def tick(workspace,gateway,*,after=None,stop=None):
    from .worker import MQTT_UNIT_QUALITY_KIND,actor,process_once
    _scope(workspace,gateway)
    if KIND!=MQTT_UNIT_QUALITY_KIND:raise HTTPException(409,'单位质量后台尚未接通，原请求保留')
    stop=stop or threading.Event()
    if stop.is_set():return {'attempted':0,'blocked':0,'after':after}
    with Session(engine()) as db:
        owner=db.scalar(select(MqttGateway).where(MqttGateway.workspace_id==workspace,MqttGateway.id==gateway))
        if owner is None:raise HTTPException(404,'原网关不存在')
        query=select(Job.id,Job.available_at).where(Job.workspace_id==workspace,Job.kind==KIND,
            Job.data['gateway_id'].as_string()==gateway,Job.available_at<=func.clock_timestamp(),
            or_(Job.status=='queued',and_(Job.status=='running',or_(Job.lease_until.is_(None),Job.lease_until<func.clock_timestamp()))))
        if after:query=query.where(or_(Job.available_at>after[0],and_(Job.available_at==after[0],Job.id>after[1])))
        rows=list(db.execute(query.order_by(Job.available_at,Job.id).limit(50)))
    result={'attempted':0,'blocked':0,'after':None}
    for identity,available in rows:
        if stop.is_set():break
        result['after']=(available,identity)
        try:
            with Session(engine()) as db:
                job=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.kind==KIND,Job.id==identity))
                if job is None or job.data.get('gateway_id')!=gateway:raise HTTPException(409,'原网关任务不一致')
                p=actor(db,workspace,job.data['actor_id']);receipt,_=verify(db,p,job)
                if receipt.gateway_id!=gateway:raise HTTPException(409,'原收件不属于此网关')
            if process_once(job_id=identity,kinds=[KIND],deliver=False,workspace_id=workspace,
                _stop_event=stop,_mqtt_gateway_id=gateway):result['attempted']+=1
        except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError,DBAPIError):result['blocked']+=1
    return result


def gateway_status(db,p,gateway,limit=100):
    """Read saved task status only; a successful Job is not a repair receipt."""
    _scope(p.workspace_id,gateway)
    from .security import require
    require(p,'admin','operator','viewer')
    member=db.scalar(select(Membership).join(User,User.id==Membership.user_id).where(
        Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id,Membership.role==p.role,User.active.is_(True)))
    if member is None:raise HTTPException(403,'当前账号的工作区权限已改变')
    owner=db.scalar(select(MqttGateway).where(MqttGateway.workspace_id==p.workspace_id,MqttGateway.id==gateway))
    if owner is None:raise HTTPException(404,'原网关不存在')
    if type(limit) is not int or not 1<=limit<=100:raise HTTPException(422,'数量范围无效')
    rows=db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND,
        Job.data['gateway_id'].as_string()==gateway).order_by(Job.created_at.desc()).limit(limit+1)).all()
    failed_events=db.scalars(select(Event).where(Event.workspace_id==p.workspace_id,
        Event.kind=='perception.sensor_packet',Event.status=='received',
        Event.data['report']['gateway_id'].as_string()==gateway,
        Event.data['unit_quality_enqueue']['state'].as_string()=='blocked')
        .order_by(Event.created_at.desc()).limit(limit+1)).all()
    from .worker import actor,ActorRevoked
    database_now=db.scalar(select(func.clock_timestamp()))
    records=[]
    for j in rows:
        state={'queued':'waiting_worker','running':'processing',
            'succeeded':'job_finished_needs_evidence_check','failed':'needs_review',
            'cancelled':'needs_review'}.get(j.status,'needs_review')
        if j.status=='running' and (j.lease_until is None or j.lease_until<=database_now):
            state='lease_expired_waiting_recovery'
        evidence_verified=False
        if j.status in {'queued','running','failed'}:
            try:
                actual=actor(db,p.workspace_id,j.data['actor_id'])
            except (ActorRevoked,KeyError,ValueError,TypeError,AttributeError):
                state='actor_unavailable'
            else:
                try:
                    # A read-only projection, not a grant or a Job claim.
                    verify(db,actual,j)
                    evidence_verified=True
                except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
                    state='evidence_unverifiable'
        can_retry=(j.status=='failed' and evidence_verified and p.user_id==j.data.get('actor_id')
            and p.role in {'admin','operator'} and j.lease_until is None and j.attempts<3
            and not j.data.get('recovery_only') and 'unit_quality_result' not in j.data)
        records.append({'received_time':j.created_at.isoformat(),'state':state,
            'reference':j.id,'version':j.version,'can_retry':can_retry,'not_repaired':True})
    records.extend({'received_time':e.created_at.isoformat(),'state':'enqueue_blocked',
        'reference':e.id,'can_retry':False,'not_repaired':True} for e in failed_events)
    records.sort(key=lambda x:x['received_time'],reverse=True)
    return {'records':records[:limit],'has_more':len(records)>limit,'worker_online_verified':False}


def run(workspace,gateway):
    _scope(workspace,gateway);stop=threading.Event();previous={};after=None
    def halt(*_):stop.set()
    for sig in (signal.SIGINT,signal.SIGTERM):previous[sig]=signal.getsignal(sig);signal.signal(sig,halt)
    try:
        while not stop.is_set():
            try:
                state=tick(workspace,gateway,after=after,stop=stop);after=state['after']
                if not state['attempted']:stop.wait(2 if state['blocked'] else 1)
            except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError,DBAPIError):stop.wait(2)
    finally:
        for sig,old in previous.items():signal.signal(sig,old)


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--workspace-id',required=True);parser.add_argument('--gateway-id',required=True)
    args=parser.parse_args();run(args.workspace_id,args.gateway_id)
if __name__=='__main__':main()
