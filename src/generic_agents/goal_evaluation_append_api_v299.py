"""User commands for a new evaluation of the same confirmed business Task."""
from fastapi import APIRouter

from .goal_evaluation_append_contracts_v297 import AppendRequest
from .goal_evaluation_append_store_v297 import accept, get_operation
from .setup_api import P, DB

router=APIRouter()


@router.post('/tasks/{identity}/goal-evaluations/append',status_code=201)
def append_evaluation(identity:str,body:AppendRequest,p:P,db:DB):
    value=accept(db,p,identity,body.model_dump(mode='json'))
    db.commit()
    return value


@router.get('/tasks/{identity}/goal-evaluations/operations/{request_id}')
def read_operation(identity:str,request_id:str,p:P,db:DB):
    return get_operation(db,p,identity,request_id)
