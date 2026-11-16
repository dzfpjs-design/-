"""Closed, byte-preserving shadow commands; parsing grants no permission."""
from datetime import datetime
import re
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, StrictStr, ValidationError, field_validator

from .contracts import Contract
from .threshold_evolution_protocol_v147 import read_wire
from .typed_payload_codec_v131 import canonical_json

ROOT = '/api/local-rules/evolution/threshold-shadows'
ERROR = '请保留完整的影子观察原请求、编号和明确确认'
_AWARE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}'
                    r'(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})')


def canonical_uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError(ERROR)
    return value


class Command(Contract):
    request_id: StrictStr

    @field_validator('request_id', 'evaluation_id', check_fields=False)
    @classmethod
    def identity(cls, value): return canonical_uuid(value)

    @field_validator('title', 'reason', check_fields=False)
    @classmethod
    def meaningful(cls, value):
        canonical_json(value)
        if not value.strip(): raise ValueError(ERROR)
        return value

    @field_validator('confirm', 'confirm_shadow_only', mode='before', check_fields=False)
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True: raise ValueError(ERROR)
        return value


class CreateShadow(Command):
    schema_name: Literal['threshold-shadow-create-v151'] = Field(alias='schema')
    evaluation_id: StrictStr
    expected_evaluation_version: int = Field(strict=True, ge=1)
    title: StrictStr = Field(min_length=1, max_length=200)
    reason: StrictStr = Field(min_length=1, max_length=2000)
    max_inputs: int = Field(strict=True, ge=1, le=30)
    expires_at: StrictStr = Field(min_length=1, max_length=100)
    confirm_shadow_only: Literal[True]

    @field_validator('expires_at')
    @classmethod
    def aware_expiry(cls, value):
        # Match the shared reader's vocabulary without normalizing old bytes.
        if _AWARE.fullmatch(value) is None: raise ValueError(ERROR)
        parsed = datetime.fromisoformat(value)
        if value != value.strip() or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(ERROR)
        return value


class ControlShadow(Command):
    schema_name: Literal['threshold-shadow-control-v151'] = Field(alias='schema')
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=1, max_length=2000)
    confirm: Literal[True]


def read_command(raw):
    value = read_wire(raw)
    if type(value) is not dict: raise ValueError(ERROR)
    canonical_uuid(value.get('request_id'))
    return value


def identify(method, path):
    if type(path) is not str or not path.isascii() or method != 'POST':
        raise ValueError(ERROR)
    if path == ROOT: return None, 'create', CreateShadow
    if path.startswith(ROOT + '/'):
        parts = path[len(ROOT)+1:].split('/')
        if len(parts) == 2 and parts[1] in {'pause', 'resume', 'end'}:
            return canonical_uuid(parts[0]), parts[1], ControlShadow
    raise ValueError(ERROR)


def parse_command(model, value):
    try:
        body = model.model_validate(value)
        if canonical_json(body.model_dump(by_alias=True)) != canonical_json(value):
            raise ValueError(ERROR)
        return body
    except (ValidationError, ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422, ERROR) from None
