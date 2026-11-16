"""Explicit authorization/receipt API; no model or physical device permission."""
from uuid import UUID
from typing import Annotated

from fastapi import APIRouter
from pydantic import Field, StrictInt, StrictStr, StrictBool, model_validator

from .contracts import Contract
from .models import DomainInstance
from .setup_api import P, DB
from .services import scoped
from . import signal_routing as routing

router = APIRouter(prefix='/instances')
Positive = Annotated[StrictInt,Field(ge=1)]
Reason = Annotated[StrictStr,Field(min_length=1,max_length=2000)]


class Pause(Contract):
    request_id: UUID
    expected_version: Positive
    reason: Reason

    @model_validator(mode='after')
    def text(self):
        if not self.reason.strip() or '\x00' in self.reason: raise ValueError('请填写有效说明')
        self.reason.encode('utf-8')
        return self


class Enable(Pause):
    options_hash: Annotated[StrictStr,Field(pattern=r'^[a-f0-9]{64}$')]
    release_id: UUID
    source_id: UUID
    extractor_id: UUID
    entity_ids: list[UUID] = Field(min_length=1,max_length=50)
    business_id: Annotated[StrictStr,Field(pattern=r'^business_[a-f0-9]{16}$')]
    max_tasks: Annotated[StrictInt,Field(ge=1,le=100)] = 10
    confirm_human_review_only: StrictBool

    @model_validator(mode='after')
    def scope(self):
        if self.confirm_human_review_only is not True: raise ValueError('请明确仅创建人工核查任务')
        if len(set(self.entity_ids)) != len(self.entity_ids): raise ValueError('对象不能重复')
        return self


@router.get('/{identity}/signal-routing/options')
def options(identity:UUID,p:P,db:DB):
    return routing.options(db,p,scoped(db,DomainInstance,str(identity),p))


@router.get('/{identity}/signal-routing/requests/{request_id}')
def request(identity:UUID,request_id:UUID,p:P,db:DB):
    return routing.saved_request(scoped(db,DomainInstance,str(identity),p),p,str(request_id))


@router.get('/{identity}/signal-routing')
def listing(identity:UUID,p:P,db:DB):
    return routing.reply(scoped(db,DomainInstance,str(identity),p))


@router.post('/{identity}/signal-routing/enable',status_code=201)
def enable(identity:UUID,body:Enable,p:P,db:DB):
    result=routing.command(db,p,str(identity),'enable',body.model_dump(mode='json'));db.commit();return result


@router.post('/{identity}/signal-routing/{route_id}/pause')
def pause(identity:UUID,route_id:UUID,body:Pause,p:P,db:DB):
    result=routing.command(db,p,str(identity),'pause',body.model_dump(mode='json'),str(route_id));db.commit();return result


from .signal_graph_routing_api_v134 import router as graph_routing_router
router.include_router(graph_routing_router)
