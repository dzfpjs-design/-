"""Closed quality-review wire and human completeness, never source authority."""
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, StrictBool, StrictStr, ValidationError, field_validator, model_validator

from .contracts import Contract
from .threshold_evolution_protocol_v147 import read_wire
from .typed_payload_codec_v131 import canonical_json

ROOT='/api/local-rules/evolution/threshold-quality-reviews'
ERROR='请保留完整的质量审核原请求、原版本和明确确认'
DRAFT_KEYS={'evaluation_id','expected_evaluation_version','shadow_id','expected_shadow_version','title',
    'scope_statement','sampling_statement','limitations','unresolved_items','representative_confirmation','label_sources'}


def canonical_uuid(value):
    if type(value) is not str or str(UUID(value))!=value:raise ValueError(ERROR)
    return value


class MaterialReference(Contract):
    material_id: StrictStr
    sha256: StrictStr
    locator: StrictStr = Field(min_length=1,max_length=2000)

    @field_validator('material_id')
    @classmethod
    def identity(cls,value):return canonical_uuid(value)

    @field_validator('sha256')
    @classmethod
    def digest(cls,value):
        if len(value)!=64 or set(value)-set('0123456789abcdef'):raise ValueError(ERROR)
        return value

    @field_validator('locator')
    @classmethod
    def location(cls,value):
        canonical_json(value)
        if not value.strip():raise ValueError(ERROR)
        return value


class LabelSource(Contract):
    feedback_id: StrictStr
    expected_version: int = Field(strict=True,ge=1)
    material_refs: list[MaterialReference] = Field(max_length=20)

    @field_validator('feedback_id')
    @classmethod
    def identity(cls,value):return canonical_uuid(value)

    @model_validator(mode='after')
    def unique_materials(self):
        ids=[ref.material_id for ref in self.material_refs]
        if len(ids)!=len(set(ids)):raise ValueError(ERROR)
        return self


class Command(Contract):
    request_id: StrictStr

    @field_validator('request_id','evaluation_id','shadow_id',check_fields=False)
    @classmethod
    def identity(cls,value):return canonical_uuid(value)


class QualityDraft(Command):
    schema_name: Literal['threshold-quality-draft-v152'] = Field(alias='schema')
    evaluation_id: StrictStr
    expected_evaluation_version: int = Field(strict=True,ge=1)
    shadow_id: StrictStr
    expected_shadow_version: int = Field(strict=True,ge=1)
    title: StrictStr = Field(max_length=200)
    scope_statement: StrictStr = Field(max_length=2000)
    sampling_statement: StrictStr = Field(max_length=2000)
    limitations: StrictStr = Field(max_length=2000)
    unresolved_items: list[StrictStr] = Field(max_length=20)
    representative_confirmation: StrictBool
    label_sources: list[LabelSource] = Field(max_length=60)

    @field_validator('title','scope_statement','sampling_statement','limitations')
    @classmethod
    def text(cls,value):canonical_json(value);return value

    @field_validator('unresolved_items')
    @classmethod
    def issues(cls,value):
        for item in value:
            canonical_json(item)
            if not item.strip() or len(item)>2000:raise ValueError(ERROR)
        return value

    @model_validator(mode='after')
    def unique_labels(self):
        ids=[row.feedback_id for row in self.label_sources]
        if len(ids)!=len(set(ids)):raise ValueError(ERROR)
        return self


class UpdateQuality(QualityDraft):
    expected_version: int = Field(strict=True,ge=1)


class ControlQuality(Command):
    schema_name: Literal['threshold-quality-control-v152'] = Field(alias='schema')
    expected_version: int = Field(strict=True,ge=1)
    reason: StrictStr = Field(min_length=1,max_length=2000)
    confirm: Literal[True]

    @field_validator('reason')
    @classmethod
    def reason_text(cls,value):
        canonical_json(value)
        if not value.strip():raise ValueError(ERROR)
        return value

    @field_validator('confirm',mode='before')
    @classmethod
    def confirmed(cls,value):
        if value is not True:raise ValueError(ERROR)
        return value


def read_command(raw):
    value=read_wire(raw)
    if type(value) is not dict:raise ValueError(ERROR)
    canonical_uuid(value.get('request_id'));return value


def identify(method,path):
    if method!='POST' or type(path) is not str or not path.isascii():raise ValueError(ERROR)
    if path==ROOT:return None,'create',QualityDraft
    if path.startswith(ROOT+'/'):
        parts=path[len(ROOT)+1:].split('/')
        if len(parts)==2 and parts[1] in {'update','approve','reject','revoke'}:
            return canonical_uuid(parts[0]),parts[1],UpdateQuality if parts[1]=='update' else ControlQuality
    raise ValueError(ERROR)


def parse_command(model,value):
    try:
        body=model.model_validate(value)
        if canonical_json(body.model_dump(by_alias=True))!=canonical_json(value):raise ValueError(ERROR)
        return body
    except (ValidationError,ValueError,TypeError,UnicodeError,RecursionError):
        raise HTTPException(422,ERROR) from None


def approval_issues(draft,purpose,required_feedback_ids):
    """Human completeness only; actual labels, receipt inventories and SHA stay in store."""
    if type(draft) is not dict or set(draft)!=DRAFT_KEYS or purpose not in {'mechanism_check','business_assessment'}:
        raise ValueError(ERROR)
    if type(required_feedback_ids) is not list or not required_feedback_ids or len(set(required_feedback_ids))!=len(required_feedback_ids):
        raise ValueError(ERROR)
    issues=[]
    for field,label in (('title','审核名称'),('scope_statement','适用范围'),('sampling_statement','抽样与覆盖说明'),('limitations','限制说明')):
        if type(draft[field]) is not str or not draft[field].strip():issues.append('请完整填写'+label)
    if draft['representative_confirmation'] is not True:issues.append('请明确确认样本代表性声明；系统不认证现场真值')
    if draft['unresolved_items']:issues.append('仍有未解决问题，不能批准')
    refs=draft['label_sources']
    if type(refs) is not list or {ref['feedback_id'] for ref in refs}!=set(required_feedback_ids) or len(refs)!=len(required_feedback_ids):
        issues.append('原评估和全部影子输入的标签引用必须完整且不重复')
    if purpose=='business_assessment' and any(not ref['material_refs'] for ref in refs):
        issues.append('业务审核每条标签都需要真实可读原件与人工定位')
    return issues
