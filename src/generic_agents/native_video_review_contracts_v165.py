"""Closed video-review commands; original UTF-8 bytes remain the identity."""
import hashlib
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from .threshold_evolution_protocol_v147 import read_wire

ERROR = '视频核查请求的原文、版本或帧选择无效；请保留原稿和原请求'
DECISIONS = ('consistent', 'needs_correction', 'uncertain')


def identity(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError(ERROR)
    return value


class Draft(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    request_id: StrictStr = Field(min_length=12, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    expected_version: StrictInt = Field(ge=0)
    note: StrictStr = Field(max_length=3000)
    frame_ids: list[StrictStr] = Field(max_length=120)
    decision: Literal['consistent', 'needs_correction', 'uncertain'] | None

    @field_validator('frame_ids')
    @classmethod
    def frames(cls, values):
        if len(values) != len(set(values)):
            raise ValueError(ERROR)
        for value in values:
            identity(value)
        return values


class Submit(Draft):
    confirm_candidate_only: StrictBool

    @field_validator('confirm_candidate_only')
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError(ERROR)
        return value

    @field_validator('note')
    @classmethod
    def explained(cls, value):
        if len(value.strip()) < 5:
            raise ValueError(ERROR)
        return value

    @field_validator('frame_ids')
    @classmethod
    def selected(cls, values):
        if not values:
            raise ValueError(ERROR)
        return values

    @field_validator('decision')
    @classmethod
    def decided(cls, value):
        if value not in DECISIONS:
            raise ValueError(ERROR)
        return value


def parse_wire(raw, model):
    try:
        if model not in (Draft, Submit) or type(raw) is not bytes or not 0 < len(raw) <= 64 * 1024:
            raise ValueError(ERROR)
        value = read_wire(raw)
        if type(value) is not dict:
            raise ValueError(ERROR)
        return model.model_validate(value, strict=True)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError(ERROR) from None


def wire_sha256(raw):
    if type(raw) is not bytes:
        raise ValueError(ERROR)
    raw.decode('utf-8', errors='strict')
    return hashlib.sha256(raw).hexdigest()


def root_path(instance_id):
    return '/api/workspace/instances/' + identity(instance_id) + '/native-video-reviews'


def command_path(instance_id, observation_id, operation):
    if operation not in ('draft', 'submit'):
        raise ValueError(ERROR)
    return root_path(instance_id) + '/observations/' + identity(observation_id) + '/' + operation
