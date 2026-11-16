"""Query original need requests by nonce; never submit or retry one."""
from fastapi import APIRouter,Query
from .setup_api import P,DB
from .workspace_need_request_recovery_v248 import lookup

router=APIRouter(prefix='/api/workspace/instances')


@router.get('/{identity}/data-need-requests')
def original(identity:str,p:P,db:DB,request_id:str=Query(min_length=12,max_length=100),
        expected_body_hash:str=Query(pattern=r'^[a-f0-9]{64}$')):
    return lookup(db,p,identity,request_id,expected_body_hash)
