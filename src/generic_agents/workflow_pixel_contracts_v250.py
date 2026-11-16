"""Typed original selections and commands; no client-provided execution grant."""
from typing import Literal
from uuid import UUID
from pydantic import Field, StrictBool, StrictInt, field_validator
from .contracts import Contract


class PixelPairSelection(Contract):
    before_image_id: str
    after_image_id: str
    pixel_correspondence_confirmed: StrictBool
    full_image_present_confirmed: StrictBool

    @field_validator('before_image_id', 'after_image_id')
    @classmethod
    def identity(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('请选择保存的原图')
        return value

    @field_validator('pixel_correspondence_confirmed', 'full_image_present_confirmed')
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError('请明确确认两图像素对应和完整像素存在，不能推定云或阴影已剔除')
        return value


class Registration(Contract):
    schema: Literal['workflow-pixel-register-v250'] = 'workflow-pixel-register-v250'
    request_id: str = Field(min_length=12, max_length=100)
    version: StrictInt = Field(ge=1)


class Settlement(Contract):
    schema: Literal['workflow-pixel-settlement-v250'] = 'workflow-pixel-settlement-v250'
    request_id: str = Field(min_length=12, max_length=100)
    version: StrictInt = Field(ge=1)
