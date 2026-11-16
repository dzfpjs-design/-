"""Scoped normal API for saved manual continuation records."""
from fastapi import APIRouter
from .setup_api import P,DB
from . import formal_continuation_contracts_v345 as c
from . import formal_continuation_store_v345 as store

router=APIRouter()
BASE='/tasks/{identity}/business-advice/preparations/{advice_id}/formal-continuation'
@router.post(BASE+'/preview')
def preview(identity:str,advice_id:str,body:c.Preview,p:P,db:DB):
    return store.preview(db,p,identity,advice_id,body.model_dump(mode='json'))
@router.get(BASE)
def listing(identity:str,advice_id:str,p:P,db:DB):return store.listing(db,p,identity,advice_id)
@router.get(BASE+'/operations/{request_id}')
def original(identity:str,advice_id:str,request_id:str,p:P,db:DB):return store.original(db,p,identity,advice_id,request_id)
@router.post(BASE+'/operations/{request_id}/abandon')
def abandon(identity:str,advice_id:str,request_id:str,body:c.Abandon,p:P,db:DB):
    value=store.abandon(db,p,identity,advice_id,request_id,body.model_dump(mode='json'));db.commit();return value
@router.post(BASE,status_code=201)
def create(identity:str,advice_id:str,body:c.Create,p:P,db:DB):
    value=store.create(db,p,identity,advice_id,body.model_dump(mode='json'));db.commit();return value
@router.get(BASE+'/{plan_id}')
def read(identity:str,advice_id:str,plan_id:str,p:P,db:DB):return store.read(db,p,identity,advice_id,plan_id)
def command(identity,advice_id,plan_id,operation,body,p,db):
    value=store.action(db,p,identity,advice_id,plan_id,operation,body.model_dump(mode='json'));db.commit();return value
@router.post(BASE+'/{plan_id}/activate')
def activate(identity:str,advice_id:str,plan_id:str,body:c.Action,p:P,db:DB):return command(identity,advice_id,plan_id,'activate',body,p,db)
@router.post(BASE+'/{plan_id}/complete-step')
def complete(identity:str,advice_id:str,plan_id:str,body:c.CompleteStep,p:P,db:DB):return command(identity,advice_id,plan_id,'complete_step',body,p,db)
@router.post(BASE+'/{plan_id}/return-step')
def return_step(identity:str,advice_id:str,plan_id:str,body:c.ReturnStep,p:P,db:DB):return command(identity,advice_id,plan_id,'return_step',body,p,db)
@router.post(BASE+'/{plan_id}/withdraw')
def withdraw(identity:str,advice_id:str,plan_id:str,body:c.Action,p:P,db:DB):return command(identity,advice_id,plan_id,'withdraw',body,p,db)
