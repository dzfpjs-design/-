"""Closed human commands for processing saved rasters, never capture authority."""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from .threshold_evolution_protocol_v147 import read_wire

ERROR = '已保存影像补充请求无效；请保留原稿和原请求编号'


def canonical_uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError(ERROR)
    return value


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_id: StrictStr = Field(min_length=12, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')


class Choices(Command):
    business_id: StrictStr | None
    comparison_scene_ids: list[StrictStr] = Field(max_length=2)
    comparison_confirmed: StrictBool
    title: StrictStr = Field(max_length=300)
    reason: StrictStr = Field(max_length=1000)

    @field_validator('business_id')
    @classmethod
    def business(cls, value):
        # Compiled business keys are business_<digest>, not model UUIDs.
        if value is not None and (not value or value != value.strip() or len(value) > 100):
            raise ValueError(ERROR)
        return value

    @field_validator('comparison_scene_ids')
    @classmethod
    def scenes(cls, value):
        if len(value) != len(set(value)):
            raise ValueError(ERROR)
        for item in value:
            canonical_uuid(item)
        return value


class Draft(Choices):
    assessment_id: StrictStr
    release_id: StrictStr

    @field_validator('assessment_id', 'release_id')
    @classmethod
    def identities(cls, value):
        return canonical_uuid(value)


class Update(Choices):
    version: StrictInt = Field(ge=1)


class Versioned(Command):
    version: StrictInt = Field(ge=1)


class CreateTask(Versioned):
    confirm_create_task: StrictBool

    @field_validator('confirm_create_task')
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError(ERROR)
        return value


class Reassess(Versioned):
    confirm_reassess: StrictBool

    @field_validator('confirm_reassess')
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError(ERROR)
        return value


class Stop(Versioned):
    reason: StrictStr = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError(ERROR)
        return value


def parse_wire(wire: str, model_class):
    """Keep exact UTF-8 externally; validate plain typed JSON without coercion."""
    try:
        if type(wire) is not str or model_class not in {Draft, Update, CreateTask, Reassess, Stop}:
            raise ValueError(ERROR)
        raw = wire.encode('utf-8', errors='strict')
        if not 0 < len(raw) <= 16 * 1024:
            raise ValueError(ERROR)
        value = read_wire(raw)
        if type(value) is not dict:
            raise ValueError(ERROR)
        return model_class.model_validate(value, strict=True)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None
