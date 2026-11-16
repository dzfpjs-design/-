"""Closed source-aware V2 wire, independent of the unchanged V1 DataRequest."""
import json
from datetime import date
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import Field, StrictStr, field_validator, model_validator
from .contracts import Contract

MAX_BODY = 24 * 1024
KINDS = ('observation_day', 'supplement_captures', 'document_searches')
MARKER = 'source_query_v2'
CREATE_AUDIT = 'data.source_query_created_v2'
RESULT_AUDIT = 'data.source_query_answered_v2'
COMMAND_AUDIT = 'data.source_query_command_v2'


def need(value, message='资料查询原请求、来源或独立证明无法核对', status=409):
    if not value: raise HTTPException(status, message)


def uuid(value):
    if type(value) is not str or str(UUID(value)) != value: raise ValueError('编号必须为规范UUID')
    return value


def sha(value):
    if type(value) is not str or len(value) != 64 or set(value)-set('0123456789abcdef'):
        raise ValueError('摘要无效')
    return value


def day(value):
    if type(value) is not str or len(value) != 10 or str(date.fromisoformat(value)) != value:
        raise ValueError('请选择中国批次日期YYYY-MM-DD')
    return value


class Scope(Contract):
    instance_id: StrictStr
    release_id: StrictStr
    business_ids: list[StrictStr] = Field(max_length=30)
    entity_ids: list[StrictStr] = Field(max_length=30)
    batch_day: StrictStr
    source_kinds: list[Literal['observation_day', 'supplement_captures', 'document_searches']] = Field(min_length=1, max_length=3)
    document_search_ids: list[StrictStr] = Field(max_length=20)

    @field_validator('instance_id', 'release_id')
    @classmethod
    def identities(cls, value): return uuid(value)

    @field_validator('batch_day')
    @classmethod
    def batch(cls, value): return day(value)

    @model_validator(mode='after')
    def scope_sets(self):
        for key in ('business_ids', 'entity_ids', 'source_kinds', 'document_search_ids'):
            values = getattr(self, key)
            if len(values) != len(set(values)): raise ValueError('所选范围不可重复')
        if any(not value.strip() or len(value) > 100 for value in self.business_ids): raise ValueError('业务编号无效')
        for value in self.entity_ids + self.document_search_ids: uuid(value)
        if not self.entity_ids and self.source_kinds != ['document_searches']:
            raise ValueError('无对象范围仅可用于明确实例文档')
        if self.document_search_ids and 'document_searches' not in self.source_kinds:
            raise ValueError('正文搜索必须属于所选资料类型')
        return self


class Create(Contract):
    schema: Literal['data-query-create-v2']
    request_id: StrictStr
    question: StrictStr = Field(min_length=1, max_length=2000)
    expected_catalog_hash: StrictStr
    source_scope: Scope
    parent_query_id: StrictStr | None

    @field_validator('request_id', 'parent_query_id')
    @classmethod
    def identity(cls, value): return uuid(value) if value is not None else None

    @field_validator('expected_catalog_hash')
    @classmethod
    def hash(cls, value): return sha(value)

    @field_validator('question')
    @classmethod
    def text(cls, value):
        if not value.strip(): raise ValueError('请填写问题')
        return value


class Cancel(Contract):
    schema: Literal['data-query-cancel-v2']
    request_id: StrictStr
    version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator('request_id')
    @classmethod
    def identity(cls, value): return uuid(value)

    @field_validator('reason')
    @classmethod
    def text(cls, value):
        if not value.strip(): raise ValueError('请填写停止原因')
        return value


class Note(Contract):
    schema: Literal['data-query-note-v2']
    request_id: StrictStr
    version: int = Field(strict=True, ge=1)
    text: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator('request_id')
    @classmethod
    def identity(cls, value): return uuid(value)

    @field_validator('text')
    @classmethod
    def text_value(cls, value):
        if not value.strip(): raise ValueError('请填写人工解释')
        return value


TYPES = {'create': Create, 'cancel': Cancel, 'note': Note}


def parse(raw, operation):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BODY, '请求为空或超过24KiB，未执行', 413)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('重复字段')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf8'), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限数')))
        kind = TYPES[operation]
        need(type(value) is dict and set(value) == set(kind.model_fields), '请求字段不完整或含未知项', 422)
        if operation == 'create':
            need(type(value['source_scope']) is dict and set(value['source_scope']) == set(Scope.model_fields), '资料范围字段不完整或含未知项', 422)
        return kind.model_validate(value).model_dump()
    except HTTPException: raise
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422, '资料查询请求格式无效，未保存或执行') from None
