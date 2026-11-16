"""Authenticated relationship records, evidence, review and recoverable commands."""
from uuid import UUID
from fastapi import APIRouter, Query
from .setup_api import P, DB
from . import instance_relations as service

router=APIRouter(prefix='/api/instances/{instance_id}/relations')

@router.get('/catalog')
def catalog(instance_id:str,release_id:str,p:P,db:DB):
    return service.catalog(db,p,instance_id,release_id)

@router.get('/draft')
def draft(instance_id:str,release_id:str,p:P,db:DB):
    return service.get_draft(db,p,instance_id,release_id)

@router.put('/draft')
def save_draft(instance_id:str,body:service.Draft,p:P,db:DB):
    result=service.save_draft(db,p,instance_id,body);db.commit();return result

@router.get('/requests/{request_id}')
def request_status(instance_id:str,request_id:UUID,release_id:str,p:P,db:DB):
    return service.recover(db,p,instance_id,request_id,release_id)

@router.post('/requests/{request_id}')
def recover(instance_id:str,request_id:UUID,body:service.Recovery,p:P,db:DB):
    return service.recover(db,p,instance_id,request_id,body.release_id,body)

@router.post('/requests/{request_id}/abandon')
def abandon(instance_id:str,request_id:UUID,body:service.Abandon,p:P,db:DB):
    result=service.recover(db,p,instance_id,request_id,body.release_id,body,abandon=True);db.commit();return result

@router.get('')
def list_records(instance_id:str,release_id:str,p:P,db:DB,limit:int=Query(default=30,ge=1,le=100),cursor:UUID|None=None):
    return service.list_records(db,p,instance_id,release_id,limit,str(cursor) if cursor else None)

@router.post('',status_code=201)
def create(instance_id:str,body:service.Claim,p:P,db:DB):
    result=service.mutate(db,p,instance_id,body,'create');db.commit();return result

@router.get('/{relation_id}')
def get(instance_id:str,relation_id:str,release_id:str,p:P,db:DB):
    return service.get_record(db,p,instance_id,release_id,relation_id)

@router.get('/{relation_id}/history')
def history(instance_id:str,relation_id:str,release_id:str,p:P,db:DB):
    return service.history(db,p,instance_id,release_id,relation_id)

@router.post('/{relation_id}/correct')
def correct(instance_id:str,relation_id:str,body:service.Claim,p:P,db:DB):
    result=service.mutate(db,p,instance_id,body,'correct',relation_id);db.commit();return result

@router.post('/{relation_id}/review')
def review(instance_id:str,relation_id:str,body:service.Review,p:P,db:DB):
    result=service.mutate(db,p,instance_id,body,'review',relation_id);db.commit();return result
