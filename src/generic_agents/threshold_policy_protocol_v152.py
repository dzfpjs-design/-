"""Closed human finite-policy commands; no source execution authority."""
import math
import re
from datetime import date,datetime
from typing import Any,Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import Field,StrictStr,field_validator,ValidationError
from .contracts import Contract
from .threshold_evolution_protocol_v147 import read_wire
from .typed_payload_codec_v131 import canonical_json

ROOT='/api/local-rules/evolution/threshold-policies'
ERROR='请保留完整的有限策略原请求、编号与明确选择'
_AWARE=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})')


def canonical_uuid(value):
    if type(value) is not str or str(UUID(value))!=value:raise ValueError(ERROR)
    return value


def aware_text(value):
    if type(value) is not str or not _AWARE.fullmatch(value):raise ValueError(ERROR)
    parsed=datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:raise ValueError(ERROR)
    return value


class Command(Contract):
    request_id:StrictStr

    @field_validator('request_id','review_id',check_fields=False)
    @classmethod
    def identity(cls,value):return canonical_uuid(value)

    @field_validator('title','reason','source_record_id','processing_version',check_fields=False)
    @classmethod
    def meaningful(cls,value):
        canonical_json(value)
        if not value.strip():raise ValueError(ERROR)
        return value

    @field_validator('confirm','confirm_use',mode='before',check_fields=False)
    @classmethod
    def consent(cls,value):
        if value is not True:raise ValueError(ERROR)
        return value


class CreatePolicy(Command):
    schema_name:Literal['threshold-policy-create-v152']=Field(alias='schema')
    review_id:StrictStr
    expected_review_version:int=Field(strict=True,ge=1)
    title:StrictStr=Field(min_length=1,max_length=200)
    reason:StrictStr=Field(min_length=1,max_length=2000)
    max_candidate_inputs:int=Field(strict=True,ge=1,le=30)
    expires_at:StrictStr=Field(min_length=1,max_length=100)
    confirm_use:Literal[True]

    @field_validator('expires_at')
    @classmethod
    def expiry(cls,value):return aware_text(value)


class ControlPolicy(Command):
    schema_name:Literal['threshold-policy-control-v152']=Field(alias='schema')
    expected_version:int=Field(strict=True,ge=1)
    reason:StrictStr=Field(min_length=1,max_length=2000)
    confirm:Literal[True]


class PolicyInput(Command):
    schema_name:Literal['threshold-policy-input-v152']=Field(alias='schema')
    expected_version:int=Field(strict=True,ge=1)
    expected_choice:Literal['candidate','baseline']
    source_record_id:StrictStr=Field(min_length=1,max_length=200)
    processing_version:StrictStr=Field(min_length=1,max_length=40)
    observed_at:StrictStr|None
    business_day:StrictStr=Field(min_length=10,max_length=10)
    attributes:dict[str,Any]=Field(min_length=1,max_length=1)

    @field_validator('observed_at')
    @classmethod
    def observation_time(cls,value):return None if value is None else aware_text(value)

    @field_validator('business_day')
    @classmethod
    def day(cls,value):
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value) or date.fromisoformat(value).isoformat()!=value:raise ValueError(ERROR)
        return value

    @field_validator('attributes')
    @classmethod
    def actual_numeric(cls,value):
        canonical_json(value)
        if any(type(v) not in {int,float}
            or type(v) is int and abs(v)>2**53-1
            or type(v) is float and not math.isfinite(v) for v in value.values()):raise ValueError(ERROR)
        return value


def read_command(raw):
    value=read_wire(raw)
    if type(value) is not dict:raise ValueError(ERROR)
    canonical_uuid(value.get('request_id'))
    return value


def identify(method,path):
    if method!='POST' or type(path) is not str:raise ValueError(ERROR)
    if path==ROOT:return None,'create',CreatePolicy
    if path.startswith(ROOT+'/'):
        parts=path[len(ROOT)+1:].split('/')
        if len(parts)==2:
            identity=canonical_uuid(parts[0]);action=parts[1]
            if action in {'pause','resume','rollback'}:return identity,action,ControlPolicy
            if action=='inputs':return identity,'input',PolicyInput
    raise ValueError(ERROR)


def parse_command(model,value):
    try:
        body=model.model_validate(value)
        if canonical_json(body.model_dump(by_alias=True))!=canonical_json(value):raise ValueError(ERROR)
        return body
    except (ValidationError,ValueError,TypeError,UnicodeError,RecursionError):
        raise HTTPException(422,ERROR) from None
