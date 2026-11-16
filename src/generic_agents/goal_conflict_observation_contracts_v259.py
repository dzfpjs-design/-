"""Explicit once-only latest conflict observation; no new device grant."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator


class ObserveOnce(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    schema: Literal['goal-conflict-observe-once-v259']
    request_id: StrictStr = Field(min_length=12, max_length=100)
    gap_id: StrictStr
    context_hash: StrictStr = Field(pattern=r'^[0-9a-f]{64}$', min_length=64, max_length=64)
    monitor_policy_id: StrictStr
    monitor_policy_version: StrictInt = Field(ge=1)
    confirm_new_observation: StrictBool
    reason: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator('request_id', 'reason')
    @classmethod
    def unicode_text(cls, value):
        if '\0' in value: raise ValueError('Text contains NUL')
        value.encode('utf-8')
        if not value.strip(): raise ValueError('Text is empty')
        return value

    @field_validator('gap_id', 'monitor_policy_id')
    @classmethod
    def identity(cls, value):
        if str(UUID(value)) != value: raise ValueError('Canonical resource identity is required')
        return value

    @field_validator('confirm_new_observation')
    @classmethod
    def confirmed(cls, value):
        if value is not True: raise ValueError('An explicit once-only observation confirmation is required')
        return value
