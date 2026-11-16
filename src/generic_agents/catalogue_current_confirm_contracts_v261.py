"""Strict requests for explicit Current catalogue confirmation, not authority.

The store must independently check the actual ledger, scope inventory, current
configuration, owner and source. Parsing this body creates no confirmed Plan.
"""
import json
import math
from typing import Literal

from pydantic import Field, StrictStr, field_validator, model_validator

from .catalogue_current_context_contracts_v260 import (
    Closed, ConfigurationBasis, DraftRef, identity, sha)
from .report_scoped_design_plan_v180 import MAX_SCOPES

SCHEMA = 'catalogue-current-confirm-v261'
MAX_RAW = 1024 * 1024
MAX_REASON = 2000
ERROR = '目录确认请求的原文、版本、分类或运行期选择不符合合同'
ScopeKind = Literal['runtime_business', 'platform_support',
    'equipment_configuration', 'project_construction', 'unresolved']


def _text(value):
    value.encode('utf-8', 'strict')
    if '\x00' in value or not value.strip():
        raise ValueError(ERROR)
    return value


def scope_identity(value):
    # Scope IDs are existing ledger identifiers, not UUIDs. Match the original
    # planner's bounded identifier domain without inventing a new identity.
    if (type(value) is not str or not 1 <= len(value) <= 128
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        raise ValueError(ERROR)
    value.encode('utf-8', 'strict')
    return value


class ScopeClassification(Closed):
    scope_id: StrictStr
    kind: ScopeKind
    reason: StrictStr = Field(min_length=1, max_length=MAX_REASON)

    @field_validator('scope_id')
    @classmethod
    def scope(cls, value):
        return scope_identity(value)

    @field_validator('reason')
    @classmethod
    def reason_text(cls, value):
        return _text(value)


class ConfirmBody(Closed):
    schema: Literal['catalogue-current-confirm-v261']
    request_id: StrictStr
    binding_id: StrictStr
    binding_record_hash: StrictStr
    working_draft_ref: DraftRef
    target_configuration: ConfigurationBasis
    expected_context_hash: StrictStr
    scope_classifications: list[ScopeClassification] = Field(min_length=1, max_length=MAX_SCOPES)
    runtime_scope_ids: list[StrictStr] = Field(min_length=1, max_length=MAX_SCOPES)
    confirm: Literal[True]
    reason: StrictStr = Field(min_length=1, max_length=MAX_REASON)

    @field_validator('request_id', 'binding_id')
    @classmethod
    def ids(cls, value):
        return identity(value)

    @field_validator('binding_record_hash', 'expected_context_hash')
    @classmethod
    def hashes(cls, value):
        if not sha(value):
            raise ValueError(ERROR)
        return value

    @field_validator('confirm', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError(ERROR)
        return value

    @field_validator('reason')
    @classmethod
    def reason_text(cls, value):
        return _text(value)

    @model_validator(mode='after')
    def exact_selection(self):
        ids = [item.scope_id for item in self.scope_classifications]
        if ids != sorted(set(ids)):
            raise ValueError(ERROR)
        runtime = [scope_identity(value) for value in self.runtime_scope_ids]
        if runtime != sorted(set(runtime)):
            raise ValueError(ERROR)
        allowed = {item.scope_id for item in self.scope_classifications
            if item.kind == 'runtime_business'}
        if not set(runtime).issubset(allowed):
            raise ValueError(ERROR)
        return self


def parse(raw):
    """Return the strict body and unchanged UTF-8 text, with a real byte bound.

    Duplicate fields, invalid Unicode and excessive structures are rejected
    before a caller can freeze a body hash. This helper does not hash or sign it.
    """
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_RAW:
            raise ValueError(ERROR)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError(ERROR)
                result[key] = value
            return result
        def constant(_value):
            raise ValueError(ERROR)
        exact = raw.decode('utf-8', 'strict')
        value = json.loads(exact, object_pairs_hook=pairs, parse_constant=constant)
        stack = [(value, 0)]; count = 0
        while stack:
            item, depth = stack.pop(); count += 1
            if depth > 24 or count > 4096:
                raise ValueError(ERROR)
            if type(item) is dict:
                for key, child in item.items():
                    key.encode('utf-8', 'strict')
                    if '\x00' in key:
                        raise ValueError(ERROR)
                    stack.append((child, depth + 1))
            elif type(item) is list:
                stack.extend((child, depth + 1) for child in item)
            elif type(item) is str:
                item.encode('utf-8', 'strict')
                if '\x00' in item:
                    raise ValueError(ERROR)
            elif type(item) is float and not math.isfinite(item):
                raise ValueError(ERROR)
        return ConfirmBody.model_validate(value), exact
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None
