"""Explicit release of one original managed data event, never extraction.

The ordinary terminal Job records only the accepted delivery command. It is
not a successful data ACK or runnable background work. Existing receivers own
the actual Memory/receipt/ACK transaction and downstream state refresh.
"""
import copy
from datetime import datetime,timezone
from typing import Literal,Annotated
from fastapi import HTTPException
from pydantic import Field,model_validator
from sqlalchemy import select,text
from . import models as m
from .contracts import Contract,digest
from .layered_approval_contract_v175 import ID,SHA,original_json
from .services import scoped,audit,require
from .media_daily_task_adapter import current_actor

KIND='authorize_observation_data_delivery_v245'
ACTION='observation.data_delivery_command_v245'
MAX_BODY=16384
ERROR='原观测、独立数据接收或原命令证明无法核对；未补造历史或重新提取'


def need(value,message=ERROR,status=409):
    if not value:raise HTTPException(status,message)


class Retry(Contract):
    schema:Literal['observation-data-delivery-retry-command-v245']
    request_id:ID
    expected_context_hash:SHA
    reason:Annotated[str,Field(strict=True,min_length=1,max_length=2000)]
    confirm_retry:Literal[True]

    @model_validator(mode='before')
    @classmethod
    def consent(cls,value):
        if type(value) is not dict or value.get('confirm_retry') is not True:raise ValueError('请明确批准仅重试原数据投递')
        return value

    @model_validator(mode='after')
    def reason_text(self):
        if not self.reason.strip() or '\x00' in self.reason:raise ValueError('请填写有效恢复原因')
        return self


def parse(raw):
    need(type(raw) is bytes and 0<len(raw)<=MAX_BODY,'请求为空或超过16KiB，未执行',413)
    try:return original_json(raw,Retry)
    except (ValueError,TypeError,UnicodeError,RecursionError,OverflowError):
        raise HTTPException(422,'原数据投递重试请求格式无效，未执行') from None


def _lock(db,p,nonce):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest(['data-delivery-v245',p.workspace_id,nonce])[:15],16)})


def _iso(value):
    if value is None:return None
    need(isinstance(value,datetime) and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat()


def _base(db,p,instance_id,observation_id,*,lock=False):
    current_actor(db,p)
    instance=scoped(db,m.DomainInstance,instance_id,p)
    # Match the production receiver's Event -> Observation row order.
    query=select(m.Event).where(m.Event.workspace_id==p.workspace_id,m.Event.kind=='observation.data',
        m.Event.idempotency_key=='data:'+observation_id)
    if lock:query=query.with_for_update().execution_options(populate_existing=True)
    event=db.scalar(query)
    observation=scoped(db,m.Observation,observation_id,p,lock)
    entity=scoped(db,m.Entity,observation.entity_id,p)
    need(entity.namespace==instance.namespace,'原观测不属于此实例',404)
    return instance,observation,event


def _memories(db,p,event):
    return list(db.scalars(select(m.Memory).where(m.Memory.workspace_id==p.workspace_id,
        m.Memory.data['event_id'].as_string()==event.id,
        m.Memory.data['type'].as_string()=='observation_evidence').limit(2)))


def _view(db,p,instance,observation,event):
    from . import instance_data_receipts as receipts
    proof={'id':observation.id,'version':observation.version,'status':observation.status,
        'entity_id':observation.entity_id,'source_id':observation.source_id,'source_record_id':observation.source_record_id,
        'processing_version':observation.processing_version,'observed_at':_iso(observation.observed_at),
        'business_day':observation.business_day,'data_hash':digest(observation.data)}
    state='pending';reason=None;summary=None;can_retry=False
    payload=digest(event.data) if event is not None else None
    try:
        need(observation.status=='processed' and event is not None)
        from .instance_domain import generated_release
        entity=scoped(db,m.Entity,observation.entity_id,p)
        origin=generated_release(db,p,entity)
        need(origin is not None and origin.instance_id==instance.id)
        destination,_,_=receipts._destination(db,p,observation)
        need(destination is not None and destination.instance_id==instance.id and receipts.verify_release(db,p,destination) is not None)
        expected=receipts._receipt(db,p,event,observation)
        need(expected is not None)
        if event.status=='received':
            checked=receipts.observation_receipt(db,p,observation)
            need(checked.get('data_reception')==expected and event.received_at is not None)
            memories=_memories(db,p,event);need(len(memories)==1)
            summary={'event_id':event.id,'memory_id':memories[0].id,'received_at':_iso(event.received_at),'receipt_hash':digest(expected)}
            state='received'
        elif event.status in {'pending_delivery','blocked_delivery'}:
            # A Memory or received timestamp beside a nonreceived Event is
            # an inconsistent/unknown settlement, never a fresh retry grant.
            need(not _memories(db,p,event) and event.received_at is None
                and 'data_reception' not in observation.data and 'data_reception' not in event.data)
            state='blocked' if event.status=='blocked_delivery' else 'pending'
            can_retry=state=='blocked' and p.role in {'admin','operator'}
            reason='原数据接收受阻，核对并修复原因后可明确重试原事件' if state=='blocked' else '原事件仍待接收，不能据此判断处理未执行'
        else:need(False)
    except HTTPException as error:
        if error.status_code==403:raise
        state='blocked_integrity';reason=ERROR;can_retry=False
    except (KeyError,TypeError,ValueError,AttributeError,OSError):
        state='blocked_integrity';reason=ERROR;can_retry=False
    value={'schema':'observation-data-delivery-context-v245','workspace_id':p.workspace_id,'instance_id':instance.id,
        'observation_id':observation.id,'observation_version':observation.version,'entity_id':observation.entity_id,
        'event_id':event.id if event else None,'event_status':event.status if event else None,'payload_hash':payload,
        'observation_proof_hash':digest(proof),'state':state,'can_retry':can_retry,'reason':reason,'receipt':summary,
        'cloud_calls':0,'device_actions':0}
    return {**value,'context_hash':digest(value)}


def context(db,p,instance_id,observation_id):return _view(db,p,*_base(db,p,instance_id,observation_id))


def recover(db,p,instance_id,observation_id,request_id,expected_sha256):
    current_actor(db,p);_lock(db,p,request_id)
    key='observation-data-delivery:'+request_id
    rows=list(db.scalars(select(m.Job).where(m.Job.workspace_id==p.workspace_id,m.Job.idempotency_key==key).limit(2)))
    events=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==ACTION,
        m.Audit.data['request_id'].as_string()==request_id).limit(2)))
    need(len(rows)<=1 and len(events)==len(rows))
    body=result=None
    if rows:
        try:
            job=rows[0];value=job.data;typed,exact,hashed=parse(value['exact_body'].encode('utf8'))
            need(job.kind==KIND and job.status=='succeeded' and job.attempts==0 and job.lease_token is None and job.lease_until is None
                and value['schema']=='observation-data-delivery-command-record-v245'
                and value['operation']=='save_delivery_authorization_only' and value['actor_id']==p.user_id==events[0].actor_id
                and value['instance_id']==instance_id and value['observation_id']==observation_id and typed.request_id==request_id
                and hashed==expected_sha256==value['body_sha256'] and value['record_hash']==digest({k:v for k,v in value.items() if k!='record_hash'})
                and events[0].object_id==job.id and events[0].data==value)
            body=typed.model_dump(mode='json');result=copy.deepcopy(value['result'])
            need(result['schema']=='observation-data-delivery-command-v245' and result['decision'] in {'released_for_delivery','pending_retained','already_received'}
                and result['request_id']==request_id and result['body_sha256']==hashed and result['observation_id']==observation_id
                and result['event_id']==value['event_id'] and result['cloud_calls']==result['device_actions']==0)
        except HTTPException as error:
            if error.status_code==409:raise
            need(False)
        except (KeyError,TypeError,ValueError,AttributeError,UnicodeError):need(False)
    # Original command recovery survives a later custody problem; current is
    # explicitly blocked_integrity rather than an invented successful ACK.
    return {'schema':'observation-data-delivery-recovery-v245','found':bool(rows),'body':body,'result':result,
        'current':context(db,p,instance_id,observation_id)}


def retry(db,p,instance_id,observation_id,raw):
    body,exact,hashed=parse(raw)
    original=recover(db,p,instance_id,observation_id,body.request_id,hashed)
    if original['found']:
        # The read-only recovery retains the old result even if custody later
        # breaks. POST never turns that history into a fresh retry approval.
        need(original['current']['state']!='blocked_integrity')
        return original['result']
    require(p,'admin','operator')
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    instance,observation,event=_base(db,p,instance_id,observation_id,lock=True)
    value=_view(db,p,instance,observation,event)
    need(value['context_hash']==body.expected_context_hash,'原状态已变化，请保留原请求并重新核对')
    need(value['state'] in {'blocked','pending','received'} and event is not None)
    old={'event_id':event.id,'status':event.status,'attempts':event.attempts,'received_at':_iso(event.received_at),
        'payload_hash':value['payload_hash'],'observation_proof_hash':value['observation_proof_hash'],
        'delivery_error':copy.deepcopy(event.data.get('delivery_error'))}
    decision='already_received' if value['state']=='received' else 'pending_retained'
    if value['state']=='blocked':
        need(value['can_retry'])
        event.status='pending_delivery';decision='released_for_delivery'
    result={'schema':'observation-data-delivery-command-v245','request_id':body.request_id,'body_sha256':hashed,
        'observation_id':observation.id,'event_id':event.id,'decision':decision,'cloud_calls':0,'device_actions':0}
    saved={'schema':'observation-data-delivery-command-record-v245','actor_id':p.user_id,'instance_id':instance.id,
        'observation_id':observation.id,'event_id':event.id,'request_id':body.request_id,'exact_body':exact,'body_sha256':hashed,
        'operation':'save_delivery_authorization_only','previous':old,'result':result}
    saved={**saved,'record_hash':digest(saved)}
    job=m.Job(workspace_id=p.workspace_id,kind=KIND,idempotency_key='observation-data-delivery:'+body.request_id,
        status='succeeded',attempts=0,data=saved)
    db.add(job);db.flush();audit(db,p,ACTION,job.id,saved);db.flush()
    return result
