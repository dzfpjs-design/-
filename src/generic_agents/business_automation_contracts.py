"""Closed continuous business-automation commands; no caller source facts."""
import json
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import Field, StrictStr, field_validator
from .contracts import Contract

MAX_BODY = 24 * 1024
PROVIDER = 'received_camera_frame_v1'
JOB_KIND = 'business_automation_start'
CREATED = 'business.automation_created_v1'
COMMAND = 'business.automation_command_v1'
HEAD = 'business.automation_head_v1'
ORIGIN = 'business.automation_occurrence_v1'
STATE = 'business.automation_occurrence_state_v1'
TASK = 'business.automation_task_v1'
ERROR = '持续业务授权、发生记录或原任务的独立证明无法核对；原记录保留'


def need(value, message=ERROR, status=409):
    if not value: raise HTTPException(status, message)


def uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError('编号必须为规范UUID')
    return value


def sha(value):
    if type(value) is not str or len(value) != 64 or set(value)-set('0123456789abcdef'):
        raise ValueError('摘要无效')
    return value


class Limits(Contract):
    max_task_starts_per_day: int | None = Field(strict=True, ge=1, le=1000)
    min_interval_seconds: int = Field(strict=True, ge=0, le=86400)


class Selection(Contract):
    release_id: StrictStr
    business_id: StrictStr = Field(min_length=1, max_length=100)
    trigger_id: Literal['new_data', 'daily_time']
    provider_id: Literal['received_camera_frame_v1']
    entity_id: StrictStr
    camera_id: StrictStr
    limits: Limits

    @field_validator('release_id', 'entity_id', 'camera_id')
    @classmethod
    def identities(cls, value): return uuid(value)

    @field_validator('business_id')
    @classmethod
    def business(cls, value):
        if not value.strip(): raise ValueError('请选择实际业务')
        return value


class Preview(Contract):
    schema: Literal['business-automation-preview-request-v1']
    selection: Selection
    expected_options_hash: StrictStr

    @field_validator('expected_options_hash')
    @classmethod
    def hash(cls, value): return sha(value)


class Enable(Contract):
    schema: Literal['business-automation-enable-v1']
    request_id: StrictStr
    selection: Selection
    expected_preview_hash: StrictStr
    confirm_continuous: Literal[True]

    @field_validator('request_id')
    @classmethod
    def identity(cls, value): return uuid(value)

    @field_validator('expected_preview_hash')
    @classmethod
    def hash(cls, value): return sha(value)

    @field_validator('confirm_continuous', mode='before')
    @classmethod
    def consent(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError('请明确确认持续执行范围')
        return value


class Pause(Contract):
    schema: Literal['business-automation-pause-v1']
    request_id: StrictStr
    version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator('request_id')
    @classmethod
    def identity(cls, value): return uuid(value)

    @field_validator('reason')
    @classmethod
    def text(cls, value):
        if not value.strip(): raise ValueError('请填写停用原因')
        return value


class Resume(Enable):
    schema: Literal['business-automation-resume-v1']
    version: int = Field(strict=True, ge=1)


TYPES = {'preview': Preview, 'enable': Enable, 'pause': Pause, 'resume': Resume}


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
        if 'selection' in value:
            need(type(value['selection']) is dict and set(value['selection']) == set(Selection.model_fields), status=422)
            need(type(value['selection']['limits']) is dict
                and set(value['selection']['limits']) == set(Limits.model_fields), status=422)
        return kind.model_validate(value).model_dump()
    except HTTPException: raise
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422, '持续业务请求格式无效，未保存或执行') from None
