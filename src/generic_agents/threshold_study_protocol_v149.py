"""Closed human commands for local threshold studies; no execution authority."""
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictStr, field_validator

from .contracts import Contract
from .threshold_evolution_protocol_v147 import read_wire
from .typed_payload_codec_v131 import canonical_json


def canonical_uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError('请保留原小写UUID编号')
    return value


class Reference(Contract):
    feedback_id: StrictStr
    expected_version: int = Field(strict=True, ge=1)

    @field_validator('feedback_id')
    @classmethod
    def identity(cls, value): return canonical_uuid(value)


class Command(Contract):
    request_id: StrictStr

    @field_validator('request_id', 'baseline_capability_id', 'candidate_capability_id',
                     'candidate_id', check_fields=False)
    @classmethod
    def identity(cls, value): return canonical_uuid(value)

    @field_validator('title', 'rationale', 'limitations', 'reason', check_fields=False)
    @classmethod
    def meaningful(cls, value):
        canonical_json(value)
        if not value.strip(): raise ValueError('请填写有内容的说明')
        return value

    @field_validator('confirm', 'confirm_compatible_objects', mode='before', check_fields=False)
    @classmethod
    def actual_confirmation(cls, value):
        if value is not True: raise ValueError('请明确确认这项操作')
        return value


class CandidateDraft(Command):
    schema_name: Literal['threshold-candidate-draft-v149'] = Field(alias='schema')
    title: StrictStr = Field(min_length=1, max_length=200)
    rationale: StrictStr = Field(min_length=1, max_length=2000)
    baseline_capability_id: StrictStr
    candidate_capability_id: StrictStr
    feedback_refs: list[Reference] = Field(min_length=1, max_length=30)


class UpdateCandidate(CandidateDraft):
    expected_version: int = Field(strict=True, ge=1)


class Criteria(Contract):
    max_additional_reviews: int = Field(strict=True, ge=0, le=30)


class EvaluationDraft(Command):
    schema_name: Literal['threshold-evaluation-draft-v149'] = Field(alias='schema')
    candidate_id: StrictStr
    expected_candidate_version: int = Field(strict=True, ge=1)
    title: StrictStr = Field(min_length=1, max_length=200)
    feedback_refs: list[Reference] = Field(min_length=1, max_length=30)
    criteria: Criteria
    purpose: Literal['mechanism_check', 'business_assessment']
    limitations: StrictStr = Field(min_length=1, max_length=2000)
    confirm_compatible_objects: Literal[True]


class UpdateEvaluation(EvaluationDraft):
    expected_version: int = Field(strict=True, ge=1)


class FreezeCommand(Command):
    schema_name: Literal['threshold-study-confirm-v149'] = Field(alias='schema')
    expected_version: int = Field(strict=True, ge=1)
    confirm: Literal[True]


class RunCommand(FreezeCommand):
    expected_freeze_hash: StrictStr

    @field_validator('expected_freeze_hash')
    @classmethod
    def signature(cls, value):
        if len(value) != 64 or any(ch not in '0123456789abcdef' for ch in value):
            raise ValueError('请保留原冻结摘要')
        return value


class CancelCommand(Command):
    schema_name: Literal['threshold-study-cancel-v149'] = Field(alias='schema')
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=1, max_length=2000)


def read_command(raw):
    value = read_wire(raw)
    if type(value) is not dict: raise ValueError('请保留完整原JSON对象')
    canonical_uuid(value.get('request_id'))
    return value
