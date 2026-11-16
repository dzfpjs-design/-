"""Pure identity for ending an original feedback request, never write authority.

The original request may fail its business DTO. Only its exact wire, nonce and
closed route/owner identity are checked here; actual scope, author, inventory,
locks and the permanent fence belong to the transaction service.
"""
import hashlib
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictStr, field_validator

from .contracts import Contract
from .threshold_evolution_protocol_v147 import read_wire, wire_sha256
from .typed_payload_codec_v131 import canonical_json


MAX_RETIREMENT_WIRE_BYTES = 512 * 1024
ERROR = '原反馈请求的原文、编号、路径或结束回执不符合协议'
ROOT = '/api/local-rules/evolution'
_SHA = re.compile(r'[0-9a-f]{64}')
_RECEIPT_KEYS = {'schema', 'id', 'request_id', 'original_request_id', 'actor_id',
    'workspace_id', 'original_method', 'original_path', 'original_sha256', 'reason', 'outcome'}


def _need(condition):
    if not condition: raise ValueError(ERROR)


def _uuid(value):
    try:
        _need(type(value) is str and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError):
        raise ValueError(ERROR) from None
    return value


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)
    return value


def _text(value):
    _need(type(value) is str)
    canonical_json(value)
    return value


def _reason(value):
    _text(value)
    _need(1 <= len(value) <= 2000 and bool(value.strip()))
    return value


def _pairs(items):
    value = {}
    for key, item in items:
        _need(key not in value)
        value[key] = item
    return value


def _constant(_value):
    raise ValueError(ERROR)


def read_retirement_wire(raw: bytes):
    """Read the envelope without canonicalizing either original wire string."""
    try:
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_RETIREMENT_WIRE_BYTES)
        value = json.loads(raw.decode('utf-8', errors='strict'),
                           object_pairs_hook=_pairs, parse_constant=_constant)
        _need(type(value) is dict)
        canonical_json(value)
        return value
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError(ERROR) from None


class RetirementCommand(Contract):
    schema_name: Literal['threshold-request-retirement-command-v148'] = Field(alias='schema')
    request_id: StrictStr
    original_method: Literal['POST', 'PUT']
    original_path: StrictStr
    original_wire_text: StrictStr
    original_sha256: StrictStr
    reason: StrictStr = Field(min_length=1, max_length=2000)
    confirm: Literal[True]

    @field_validator('request_id')
    @classmethod
    def canonical_nonce(cls, value): return _uuid(value)

    @field_validator('original_path', 'original_wire_text')
    @classmethod
    def original_text(cls, value): return _text(value)

    @field_validator('original_sha256')
    @classmethod
    def original_sha(cls, value): return _sha(value)

    @field_validator('reason')
    @classmethod
    def meaningful_reason(cls, value): return _reason(value)

    @field_validator('confirm', mode='before')
    @classmethod
    def explicit_confirm(cls, value):
        _need(value is True)
        return value


def _route(method, path):
    _need(type(method) is str and method in {'POST', 'PUT'}
          and type(path) is str and path.isascii() and path.startswith(ROOT + '/'))
    if method == 'POST' and path == ROOT + '/feedback':
        return 'feedback_create', None
    parts = path[len(ROOT) + 1:].split('/')
    _need(len(parts) in (2, 3) and parts[0] == 'feedback')
    owner = _uuid(parts[1])
    if len(parts) == 2:
        _need(method == 'PUT')
        return 'feedback_save', owner
    _need(method == 'POST' and parts[2] in {'submit', 'cancel', 'revisions'})
    return 'feedback_' + parts[2], owner


def retirement_identity(original_request_id, command):
    """Extract reducing identity; never repair or validate business DTO fields."""
    try:
        _need(type(command) is RetirementCommand)
        # Pydantic objects can be constructed or modified without validation.
        # Recheck the envelope only; its original_wire_text is never rebuilt.
        checked = RetirementCommand.model_validate(command.model_dump(by_alias=True))
        old = _uuid(original_request_id)
        _need(checked.request_id != old)
        raw = checked.original_wire_text.encode('utf-8', errors='strict')
        original = read_wire(raw)  # The original V147 64 KiB and strict JSON boundary.
        _need(type(original) is dict and _uuid(original.get('request_id')) == old
              and wire_sha256(raw) == checked.original_sha256)
        operation, owner = _route(checked.original_method, checked.original_path)
        if operation == 'feedback_create':
            observation_id = _uuid(original.get('observation_id'))
            rule_id = original.get('rule_id')
            _text(rule_id)
            _need(1 <= len(rule_id) <= 80 and rule_id == rule_id.strip())
            owners = {'observation_id': observation_id, 'rule_id': rule_id}
        else:
            owners = {'feedback_id': owner}
        return {'request_id': old, 'method': checked.original_method,
            'path': checked.original_path, 'wire_sha256': checked.original_sha256,
            'operation': operation, 'owner_refs': owners}
    except (ValueError, TypeError, AttributeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError(ERROR) from None


def retirement_receipt_hash(unsigned):
    """Hash exactly the public retired receipt's eleven unsigned fields."""
    try:
        _need(type(unsigned) is dict and set(unsigned) == _RECEIPT_KEYS
              and unsigned['schema'] == 'threshold-request-retirement-receipt-v148'
              and unsigned['outcome'] == 'retired')
        for key in ('id', 'request_id', 'original_request_id', 'actor_id', 'workspace_id'):
            _uuid(unsigned[key])
        _need(unsigned['request_id'] != unsigned['original_request_id'])
        _route(unsigned['original_method'], unsigned['original_path'])
        _sha(unsigned['original_sha256']); _reason(unsigned['reason'])
        return hashlib.sha256(canonical_json(unsigned).encode('utf-8')).hexdigest()
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError(ERROR) from None
