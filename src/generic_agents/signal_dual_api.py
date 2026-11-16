"""Explicit dual human-routing commands; no device or model authorization."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Depends
from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from .contracts import Contract
from .models import DomainInstance
from .services import scoped
from .setup_api import P, DB
from .signal_dual_models import SignalDualHandoff
from . import signal_dual_handoffs as dual
from .signal_dual_read_session import read_session

router = APIRouter()
ReadDB = Annotated[object,Depends(read_session)]
Positive = Annotated[StrictInt,Field(ge=1)]
Hash = Annotated[StrictStr,Field(pattern=r'^[a-f0-9]{64}$')]
Business = Annotated[StrictStr,Field(pattern=r'^business_[a-f0-9]{16}$')]


class Pause(Contract):
    request_id: UUID
    expected_version: Positive
    reason: Annotated[StrictStr,Field(min_length=1,max_length=2000)]

    @model_validator(mode='after')
    def text(self):
        if not self.reason.strip() or '\x00' in self.reason: raise ValueError('请填写有效原因')
        self.reason.encode('utf-8')
        return self


class Enable(Pause):
    options_hash: Hash
    release_id: UUID
    source_id: UUID
    extractor_id: UUID
    entity_ids: list[UUID] = Field(min_length=1,max_length=50)
    response_business_id: Business
    verification_business_id: Business
    max_tasks: Annotated[StrictInt,Field(ge=2,le=100)] = 10
    confirm_human_review_only: StrictBool

    @model_validator(mode='after')
    def scope(self):
        if self.confirm_human_review_only is not True: raise ValueError('请明确只安排两路人工待办')
        if len(set(self.entity_ids)) != len(self.entity_ids): raise ValueError('对象不能重复')
        if self.response_business_id == self.verification_business_id: raise ValueError('请分别选择不同的响应与核验业务')
        return self


class Acknowledge(Contract):
    request_id: UUID
    expected_task_version: Positive
    parent_hash: Hash
    note: Annotated[StrictStr,Field(min_length=1,max_length=5000)]
    override_reason: Annotated[StrictStr,Field(max_length=500)] = ''

    @model_validator(mode='after')
    def text(self):
        if not self.note.strip(): raise ValueError('请填写接收说明')
        for value in (self.note,self.override_reason):
            if '\x00' in value: raise ValueError('说明含无效字符')
            value.encode('utf-8')
        return self


@router.get('/instances/{identity}/signal-routing/dual-options')
def options(identity:UUID,p:P,db:ReadDB):
    return dual.options(db,p,scoped(db,DomainInstance,str(identity),p))


@router.get('/instances/{identity}/signal-routing/dual/requests/{request_id}')
def request(identity:UUID,request_id:UUID,p:P,db:ReadDB):
    return dual.saved_request(db,scoped(db,DomainInstance,str(identity),p),p,str(request_id))


@router.get('/instances/{identity}/signal-routing/dual')
def routes(identity:UUID,p:P,db:ReadDB):
    return dual.reply(db,scoped(db,DomainInstance,str(identity),p))


@router.post('/instances/{identity}/signal-routing/enable-dual',status_code=201)
def enable(identity:UUID,body:Enable,p:P,db:DB):
    result=dual.command(db,p,str(identity),'enable_dual',body.model_dump(mode='json'));db.commit();return result


@router.post('/instances/{identity}/signal-routing/dual/{route_id}/pause')
def pause(identity:UUID,route_id:UUID,body:Pause,p:P,db:DB):
    result=dual.command(db,p,str(identity),'pause_dual',body.model_dump(mode='json'),str(route_id));db.commit();return result


@router.get('/instances/{identity}/signal-handoffs')
def inbox(identity:UUID,p:P,db:ReadDB,limit:int=Query(20,ge=1,le=100),after:str|None=None,mine_only:bool=True):
    return dual.listing(db,p,str(identity),limit,after,mine_only)


@router.get('/instances/{identity}/signal-inbox')
def inbox_v2(identity:UUID,p:P,db:ReadDB,limit:int=Query(20,ge=1,le=100),after:str|None=None,
             mine_only:bool=True,mode:Literal['pending','history']='pending'):
    from .signal_dual_inbox import listing
    return listing(db,p,str(identity),limit,after,mine_only,mode)


@router.get('/signal-handoffs/{identity}')
def handoff(identity:UUID,p:P,db:ReadDB):
    return dual.parent_view(db,p,scoped(db,SignalDualHandoff,str(identity),p))


@router.get('/signal-handoffs/{identity}/response/ack-requests/{request_id}')
def acknowledgement(identity:UUID,request_id:UUID,p:P,db:ReadDB):
    return dual.ack_request(db,p,scoped(db,SignalDualHandoff,str(identity),p),str(request_id))


@router.post('/signal-handoffs/{identity}/response/ack')
def acknowledge(identity:UUID,body:Acknowledge,p:P,db:DB):
    result=dual.acknowledge(db,p,str(identity),body.model_dump(mode='json'));db.commit();return result
