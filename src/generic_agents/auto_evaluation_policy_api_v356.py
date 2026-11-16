from typing import Annotated
from uuid import UUID
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from .setup_api import P,DB
from . import auto_evaluation_policy_v356 as policy

router=APIRouter()

def automatic_runner_available():
    # Fail closed until reception witnesses, trigger scheduling and every
    # producer/candidate/promotion revocation fence are verified together.
    return False

class Command(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    request_id: StrictStr
    expected_version: Annotated[StrictInt,Field(ge=0)]
    reason: Annotated[StrictStr,Field(min_length=1,max_length=500,pattern=r'\S')]
    @field_validator('request_id')
    @classmethod
    def canonical(cls,value):
        try:
            if str(UUID(value))==value:return value
        except (ValueError,TypeError,AttributeError):pass
        raise ValueError('请求编号无效')
class Enable(Command):
    min_interval_minutes: Annotated[StrictInt,Field(ge=1,le=10080)]
    max_evaluations_per_day: Annotated[StrictInt,Field(ge=1,le=100)]

@router.get('/tasks/{identity}/auto-evaluation-policy')
def read(identity:str,p:P,db:DB):
    return {**policy.read(db,p,identity),'can_enable':automatic_runner_available(),
        'unavailable_reason':None if automatic_runner_available() else '自动评价处理链尚未接通，暂不能启用'}
@router.put('/tasks/{identity}/auto-evaluation-policy')
def enable(identity:str,body:Enable,p:P,db:DB):
    if not automatic_runner_available():raise HTTPException(409,'自动评价处理链尚未接通，暂不能启用')
    result=policy.change(db,p,identity,'enable',body.model_dump(mode='json'));db.commit();return result
@router.post('/tasks/{identity}/auto-evaluation-policy/revoke')
def revoke(identity:str,body:Command,p:P,db:DB):
    result=policy.change(db,p,identity,'revoke',body.model_dump(mode='json'));db.commit();return result
@router.get('/tasks/{identity}/auto-evaluation-policy/operations/{request_id}')
def operation(identity:str,request_id:str,p:P,db:DB):return policy.operation(db,p,identity,request_id)

@router.get('/tasks/{identity}/auto-evaluation-policy/reception-status')
def reception_status(identity:str,p:P,db:DB):
    from .auto_evaluation_ack_witness_v356 import task_summary
    return task_summary(db,p,identity)

class Retire(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    reason: Annotated[StrictStr,Field(min_length=1,max_length=500,pattern=r'\S')]

@router.post('/tasks/{identity}/auto-evaluation-policy/operations/{request_id}/retire')
def retire(identity:str,request_id:str,body:Retire,p:P,db:DB):
    result=policy.retire(db,p,identity,request_id,body.reason);db.commit();return result

@router.get('/tasks/{identity}/auto-evaluation-policy/scan-status')
def scan_status(identity:str,p:P,db:DB):
    from .auto_evaluation_scanner_v356 import task_status
    return task_status(db,p,identity)
