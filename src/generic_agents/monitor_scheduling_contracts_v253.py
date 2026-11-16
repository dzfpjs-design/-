"""Closed public scheduling command, separate from monitoring permission."""
from typing import Literal
from uuid import UUID
from pydantic import Field,StrictInt,field_validator
from .contracts import Contract

class ModeCommand(Contract):
    schema:Literal['monitor-scheduling-command-v253']
    request_id:str=Field(min_length=12,max_length=100)
    instance_id:str
    expected_version:StrictInt=Field(ge=0)
    expected_policy_version:StrictInt=Field(ge=1)
    approved_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    mode:Literal['periodic','goal_only']
    reason:str=Field(min_length=1,max_length=2000)

    @field_validator('instance_id')
    @classmethod
    def identity(cls,value):
        if str(UUID(value))!=value:raise ValueError('需要标准小写实例标识')
        return value


class RetireCommand(Contract):
    schema:Literal['monitor-scheduling-retire-v253']
    request_id:str=Field(min_length=12,max_length=100)
    instance_id:str
    original_raw:str=Field(min_length=1,max_length=32768)
    raw_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    body_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    reason:str=Field(min_length=1,max_length=2000)

    @field_validator('instance_id')
    @classmethod
    def identity(cls,value):
        if str(UUID(value))!=value:raise ValueError('需要标准小写实例标识')
        return value
