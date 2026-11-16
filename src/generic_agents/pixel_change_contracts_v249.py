"""Pixel-only paired image inputs. Never permission for geospatial processing."""
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import Field, StrictBool, StrictInt, field_validator
from .contracts import Contract


class Create(Contract):
    schema: Literal['pixel-change-create-v249'] = 'pixel-change-create-v249'
    request_id: str = Field(min_length=12, max_length=100)
    instance_version: StrictInt = Field(ge=1)
    release_id: str
    before_image_id: str
    before_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    after_image_id: str
    after_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    model_installation_id: str
    model_generation: StrictInt = Field(ge=1)
    model_recipe_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    pixel_correspondence_confirmed: StrictBool
    full_image_present_confirmed: StrictBool
    input_order: Literal['before_after'] = 'before_after'
    threshold_text: str = Field(default='0.5', pattern=r'^0\.[0-9]{1,6}$')

    @field_validator('release_id', 'before_image_id', 'after_image_id', 'model_installation_id')
    @classmethod
    def resource_id(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('需要规范资源标识')
        return value

    @field_validator('threshold_text')
    @classmethod
    def threshold(cls, value):
        if not Decimal(0) < Decimal(value) < Decimal(1):
            raise ValueError('变化概率阈值必须大于0且小于1')
        return value

    @field_validator('pixel_correspondence_confirmed', 'full_image_present_confirmed')
    @classmethod
    def acknowledged(cls, value):
        if value is not True:
            raise ValueError('请明确确认像素对应关系和完整图像像素存在；不代表云或阴影已剔除')
        return value


class Command(Contract):
    schema: Literal['pixel-change-command-v249'] = 'pixel-change-command-v249'
    request_id: str = Field(min_length=12, max_length=100)
    version: StrictInt = Field(ge=1)
