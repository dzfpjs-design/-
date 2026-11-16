"""Normal workspace planning commands; saving a draft never dispatches work."""
from fastapi import APIRouter

from . import same_goal_continuation_contracts_v312 as contracts
from . import same_goal_continuation_sources_v312 as sources
from . import same_goal_continuation_store_v312 as store
from .setup_api import P, DB

router=APIRouter()
BASE='/tasks/{identity}/business-advice/preparations/{advice_id}/continuations'


@router.get(BASE+'/context')
def context(identity:str,advice_id:str,p:P,db:DB):
    return sources.context(db,p,identity,advice_id)


@router.post(BASE+'/preview')
def preview(identity:str,advice_id:str,body:contracts.PreviewRequest,p:P,db:DB):
    return store.preview(db,p,identity,advice_id,body.model_dump(mode='json'))


@router.get(BASE+'/operations/{request_id}')
def original(identity:str,advice_id:str,request_id:str,p:P,db:DB):
    return store.get_operation(db,p,identity,advice_id,request_id)


@router.get(BASE)
def listing(identity:str,advice_id:str,p:P,db:DB):
    return store.listing(db,p,identity,advice_id)


@router.post(BASE,status_code=201)
def create(identity:str,advice_id:str,body:contracts.SaveRequest,p:P,db:DB):
    result=store.create(db,p,identity,advice_id,body.model_dump(mode='json'))
    db.commit()
    return result


@router.get(BASE+'/{continuation_id}')
def read(identity:str,advice_id:str,continuation_id:str,p:P,db:DB):
    return store.get(db,p,identity,advice_id,continuation_id)


@router.post(BASE+'/{continuation_id}/revisions')
def revise(identity:str,advice_id:str,continuation_id:str,body:contracts.ReviseRequest,p:P,db:DB):
    result=store.revise(db,p,identity,advice_id,continuation_id,body.model_dump(mode='json'))
    db.commit()
    return result


@router.post(BASE+'/{continuation_id}/withdraw')
def withdraw(identity:str,advice_id:str,continuation_id:str,body:contracts.WithdrawRequest,p:P,db:DB):
    result=store.withdraw(db,p,identity,advice_id,continuation_id,body.model_dump(mode='json'))
    db.commit()
    return result
