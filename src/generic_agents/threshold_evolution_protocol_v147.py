"""Pure wire identity and the bounded V147 observation-selection command.

Wire identity is independent of Pydantic defaults and canonical numeric text.
Neither a fingerprint nor a valid DTO grants permission to select an extractor.
"""
import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from .contracts import Contract
from .typed_payload_codec_v131 import canonical_json


MAX_WIRE_BYTES = 64 * 1024
ERROR = '阈值策略请求的原文、作用域或字段不符合协议'
_ROOT = '/api/local-rules/evolution/'
_SHA = re.compile(r'[0-9a-f]{64}')
_SEGMENT = re.compile(r'[A-Za-z0-9_-]+')


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _pairs(items):
    value = {}
    for key, item in items:
        _need(key not in value)
        value[key] = item
    return value


def _constant(_value):
    raise ValueError(ERROR)


def read_wire(raw: bytes):
    """Parse original UTF-8 bytes, preserving Python JSON numeric kinds."""
    try:
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES)
        value = json.loads(raw.decode('utf-8', errors='strict'),
            object_pairs_hook=_pairs, parse_constant=_constant)
        # Validate finite values, Unicode, keys and plain JSON containers without
        # replacing the original tree or using this spelling as the wire hash.
        canonical_json(value)
        return value
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError(ERROR) from None


def wire_sha256(raw: bytes):
    """Hash actual UTF-8 bytes; whitespace and numeric spelling remain bound."""
    try:
        _need(type(raw) is bytes)
        raw.decode('utf-8', errors='strict')
        return hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, UnicodeError):
        raise ValueError(ERROR) from None


def command_fingerprint(method, path, workspace_id, actor_id, raw_sha256):
    """Bind one original method/path/actor/workspace/wire, without authority."""
    try:
        _need(type(method) is str and method in {'POST', 'PUT'})
        _need(type(path) is str and path.startswith(_ROOT) and path.isascii())
        segments = path[len(_ROOT):].split('/')
        _need(bool(segments) and all(_SEGMENT.fullmatch(segment) for segment in segments))
        for identity in (workspace_id, actor_id):
            _need(type(identity) is str and str(UUID(identity)) == identity)
        _need(type(raw_sha256) is str and _SHA.fullmatch(raw_sha256) is not None)
        envelope = {'schema': 'threshold-evolution-command-fingerprint-v147',
            'method': method, 'path': path, 'workspace_id': workspace_id,
            'actor_id': actor_id, 'raw_sha256': raw_sha256}
        return hashlib.sha256(canonical_json(envelope).encode('utf-8')).hexdigest()
    except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
        raise ValueError(ERROR) from None


class ObservationSelectionCommand(Contract):
    schema_name: Literal['threshold-evolution-observation-command-v147'] = Field(alias='schema')
    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    expected_choice: Literal['candidate', 'baseline']
    source_record_id: str = Field(min_length=1, max_length=200, strict=True)
    processing_version: str = Field(min_length=1, max_length=40, strict=True)
    observed_at: str = Field(strict=True)
    business_day: str = Field(strict=True)
    attributes: dict[str, Any] = Field(max_length=30)

    @field_validator('request_id', mode='before')
    @classmethod
    def original_uuid(cls, value):
        _need(type(value) is str and str(UUID(value)) == value)
        return value

    @field_validator('source_record_id', 'processing_version')
    @classmethod
    def safe_identity(cls, value):
        canonical_json(value)
        return value

    @field_validator('observed_at')
    @classmethod
    def aware_time(cls, value):
        parsed = datetime.fromisoformat(value)
        _need(parsed.tzinfo is not None and parsed.utcoffset() is not None)
        canonical_json(value)
        return value

    @field_validator('business_day')
    @classmethod
    def calendar_day(cls, value):
        _need(date.fromisoformat(value).isoformat() == value)
        return value

    @field_validator('attributes', mode='before')
    @classmethod
    def original_attributes(cls, value):
        _need(type(value) is dict)
        canonical_json(value)
        return value
