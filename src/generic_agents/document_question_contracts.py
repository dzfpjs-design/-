"""Explicit opt-in single-report command; the old V2 Create is unchanged."""
import json
from typing import Literal
from pydantic import Field, StrictStr, field_validator, model_validator
from fastapi import HTTPException
from .contracts import Contract
from .source_query_contracts import MAX_BODY, need, uuid, sha


class Selection(Contract):
    instance_id: StrictStr
    release_id: StrictStr
    business_ids: list[StrictStr] = Field(min_length=1,max_length=30)
    document_version_id: StrictStr
    parse_attempt_id: StrictStr
    raw_sha256: StrictStr
    parse_hash: StrictStr

    @field_validator('instance_id','release_id','document_version_id','parse_attempt_id')
    @classmethod
    def identity(cls,value):return uuid(value)

    @field_validator('raw_sha256','parse_hash')
    @classmethod
    def hash(cls,value):return sha(value)

    @model_validator(mode='after')
    def businesses(self):
        if len(self.business_ids)!=len(set(self.business_ids)) or any(not value.strip() or len(value)>100 for value in self.business_ids):
            raise ValueError('请明确选择不重复的已启用业务')
        return self


class Create(Contract):
    schema: Literal['document-question-create-v1']
    request_id: StrictStr
    question: StrictStr = Field(min_length=1,max_length=2000)
    expected_catalog_hash: StrictStr
    selection: Selection
    parent_query_id: StrictStr | None

    @field_validator('request_id','parent_query_id')
    @classmethod
    def identity(cls,value):return uuid(value) if value is not None else None

    @field_validator('expected_catalog_hash')
    @classmethod
    def hash(cls,value):return sha(value)

    @field_validator('question')
    @classmethod
    def text(cls,value):
        if not value.strip():raise ValueError('请填写报告问题')
        return value


def parse(raw):
    need(type(raw) is bytes and 0<len(raw)<=MAX_BODY,'请求为空或超过24KiB，未执行',413)
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('重复字段')
            result[key]=value
        return result
    try:
        value=json.loads(raw.decode('utf8'),object_pairs_hook=pairs,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError('非有限值')))
        need(type(value) is dict and set(value)==set(Create.model_fields),'报告提问字段不完整或含未知项',422)
        need(type(value['selection']) is dict and set(value['selection'])==set(Selection.model_fields),'报告选择字段不完整或含未知项',422)
        return Create.model_validate(value).model_dump()
    except HTTPException:raise
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        raise HTTPException(422,'报告提问格式无效，未保存或执行') from None
