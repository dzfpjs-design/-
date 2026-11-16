"""Original-bound asynchronous unit-diagnostic request, no network or facts.

`enqueue` is called in the original packet reception transaction. It creates
only an idempotent Job; the independent ACK must commit before `run` can use
that original. Both callers retain their own transaction and lease fences.
"""
from fastapi import HTTPException
from sqlalchemy import select,text
from .contracts import digest
from .models import Audit,Event,Job,Memory,MqttReceipt
from .mqtt_data import raw_report
from .mqtt_unit_quality_v357 import diagnose
from .security import Principal
from .services import scoped

KIND='diagnose_mqtt_units_v357'
ACTION='quality.mqtt_unit_diagnostic_queued_v357'


def need(ok,message='原传感接收或诊断任务无法核对；原记录保留'):
    if not ok:raise HTTPException(409,message)


def _proof(db,receipt,event):
    p=Principal(receipt.data.get('received_by',''),receipt.workspace_id,'viewer','mqtt-unit-system-proof')
    report=raw_report(db,p,receipt)
    need(event.workspace_id==receipt.workspace_id and event.kind=='perception.sensor_packet'
        and event.idempotency_key=='mqtt-packet:'+receipt.id and event.data.get('report')==report
        and event.data.get('report_hash')==digest(report))
    memories=list(db.scalars(select(Memory).where(Memory.workspace_id==p.workspace_id,
        Memory.data['event_id'].as_string()==event.id,Memory.data['type'].as_string()=='sensor_original').limit(2)))
    need(len(memories)==1 and memories[0].status=='candidate' and all(memories[0].data.get(k)==v for k,v in report.items()))
    packet_audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action=='mqtt.packet.saved',Audit.object_id==receipt.id).limit(2)))
    need(len(packet_audits)==1 and packet_audits[0].actor_id==p.user_id
        and packet_audits[0].data.get('input_sha256')==receipt.input_sha256)
    value={'schema':'mqtt-unit-diagnostic-request-v357','workspace_id':p.workspace_id,'event_id':event.id,
        'receipt_id':receipt.id,'gateway_id':receipt.gateway_id,'input_sha256':receipt.input_sha256,
        'report_hash':event.data['report_hash'],'actor_id':p.user_id,'mode':'automatic_original_receive'}
    return {**value,'proof_hash':digest(value)}


def enqueue(db,event,receipt):
    """Retain one request, but never claim that the not-yet-committed ACK exists."""
    from sqlalchemy.orm import object_session
    need(object_session(event) is db and object_session(receipt) is db and db.get_transaction() is not None
        and event.status=='pending_delivery')
    conflicts=[x for x in (receipt.data.get('decoded') or {}).get('issues',[])
        if isinstance(x,str) and x.startswith('unit_conflict:')]
    if not conflicts:return None
    db.flush()  # savepoint/lookup must see original Memory; parent must handle errors separately.
    proof=_proof(db,receipt,event);key='mqtt-unit-diagnostic-v357:'+event.id
    locked=db.scalar(text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'),{'key':proof['workspace_id']+':'+key})
    need(locked,'原单位诊断正在登记；原收件保留，等待核对')
    old=db.scalar(select(Job).where(Job.workspace_id==proof['workspace_id'],Job.idempotency_key==key).with_for_update())
    if old is not None:
        need(old.kind==KIND and old.data.get('unit_request')==proof and old.data.get('actor_id')==proof['actor_id'])
        _queue_audit(db,old,proof);return old
    job=Job(workspace_id=proof['workspace_id'],kind=KIND,idempotency_key=key,
        data={'actor_id':proof['actor_id'],'gateway_id':receipt.gateway_id,'unit_request':proof})
    db.add(job);db.flush()
    db.add(Audit(workspace_id=proof['workspace_id'],actor_id=proof['actor_id'],action=ACTION,object_id=job.id,
        data={'request_hash':proof['proof_hash'],'origin':'automatic_original_receive','event_id':event.id}))
    db.flush();return job


def _queue_audit(db,job,proof):
    audits=list(db.scalars(select(Audit).where(Audit.workspace_id==job.workspace_id,Audit.object_id==job.id,
        Audit.action==ACTION).limit(2)))
    need(len(audits)==1 and audits[0].actor_id==proof['actor_id'] and audits[0].data=={
        'request_hash':proof['proof_hash'],'origin':'automatic_original_receive','event_id':proof['event_id']})


def verify(db,p,job,*,require_received=True):
    proof=job.data.get('unit_request');need(isinstance(proof,dict) and proof.get('proof_hash')==digest({k:v for k,v in proof.items() if k!='proof_hash'}))
    need(job.workspace_id==p.workspace_id==proof.get('workspace_id') and job.kind==KIND
        and job.data.get('actor_id')==proof.get('actor_id')==p.user_id
        and job.idempotency_key=='mqtt-unit-diagnostic-v357:'+str(proof.get('event_id'))
        and job.data.get('gateway_id')==proof.get('gateway_id'))
    receipt=scoped(db,MqttReceipt,proof['receipt_id'],p);event=scoped(db,Event,proof['event_id'],p)
    need(not require_received or event.status=='received')
    need(_proof(db,receipt,event)==proof);_queue_audit(db,job,proof)
    return receipt,event


def run(db,p,job):
    from .leases import fenced,ownership
    need(fenced(db) and ownership(db)=={'job_id':job.id,'lease_token':job.lease_token}
        and job.status=='running' and bool(job.lease_token),'原诊断租约无法核对')
    receipt,event=verify(db,p,job)
    issues=diagnose(db,p,receipt.id,mode='automatic')
    job.data={**job.data,'unit_quality_result':{'issue_ids':[x.id for x in issues],
        'receipt_id':receipt.id,'event_id':event.id,'issue_count':len(issues),
        'original_modified':False,'object_fact_updated':False,'conversion_applied':False}}
