"""Independent human object semantics; never repair old provider answers.

All-optional fields are valid here. Platform record identity is acknowledged
separately and does not make any business attribute required or unique. The
caller still owns source-ledger, account, revision and persistence authority.
"""
import copy
import json
from typing import Literal

from pydantic import Field, model_validator

from .contracts import digest
from .report_full_reading_semantics import Closed, ShortText, _grounded
from .report_evidence_semantics import (
    SpanCitation, SpanField, SpanRelationship, _provided, _refs, _check_refs,
    _legacy_projection, validate_evidence_result,
)
from .report_evidence_wire_v3 import MapClaimWireV3, BusinessWireV5, CandidateWireV5, Hash, candidate_with_ids
from .report_evidence_manual_v5 import ManualReviewValueV5
from .setup_object_proposals import unit_declared

SCHEMA = 'report-object-semantic-review-v166'
CANDIDATE = 'report-object-semantic-candidate-wire-v166'
MAX_BYTES = 512 * 1024
DEFAULT_IDENTITY_EXPLANATION = '当前人工结构稿尚未明确业务标识；需由核查人确认，不能据此认定原文没有标识。'


class BusinessIdentity(Closed):
    status: Literal['not_established', 'referenced']
    field_labels: list[str] = Field(max_length=40)
    citations: list[SpanCitation] = Field(max_length=4)
    explanation: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def explicit_state(self):
        if not self.explanation.strip() or len(self.field_labels) != len(set(self.field_labels)):
            raise ValueError('业务标识状态须保留明确说明，字段引用不能重复')
        if self.status == 'not_established' and self.field_labels:
            raise ValueError('尚未明确业务标识时不能同时指定标识字段')
        if self.status == 'referenced' and (not self.field_labels or not self.citations):
            raise ValueError('引用业务标识须明确已有字段与原片段依据')
        return self


class ObjectSemanticsV166(Closed):
    # Deliberately independent of FullObject/SpanObject and their required-text
    # validator. SpanField retains the exact field/unit/required type rules.
    name: ShortText
    fields: list[SpanField] = Field(min_length=1, max_length=40)
    citations: list[SpanCitation] = Field(max_length=4)
    inference_reason: str = Field(max_length=500)
    business_identity: BusinessIdentity

    @model_validator(mode='after')
    def complete_fields(self):
        labels = [field.label for field in self.fields]
        if self.name != self.name.strip() or not self.name or len(labels) != len(set(labels)):
            raise ValueError('对象名称须明确，字段不能为空或重复')
        if set(self.business_identity.field_labels) - set(labels):
            raise ValueError('业务标识只能引用本对象已有字段，不能自动补造字段')
        return self


class CandidateObjects(Closed):
    schema: Literal['report-object-semantic-candidate-wire-v166']
    source_hash: Hash
    catalog_hash: Hash
    domain: str = Field(max_length=200)
    global_claims: list[MapClaimWireV3] = Field(max_length=160)
    objects: list[ObjectSemanticsV166] = Field(max_length=40)
    relationships: list[SpanRelationship] = Field(max_length=30)
    businesses: list[BusinessWireV5] = Field(max_length=20)
    questions: CandidateWireV5.model_fields['questions'].annotation = Field(max_length=30)


class IdentityConfirmation(Closed):
    object_name: ShortText
    acknowledge_platform_identity: bool


class ManualValueV166(Closed):
    schema: Literal['report-object-semantic-review-v166']
    result: CandidateObjects
    note: str = Field(max_length=12000)
    object_identity_confirmations: list[IdentityConfirmation] = Field(max_length=40)

    @model_validator(mode='after')
    def one_confirmation_per_object(self):
        names = [item.name for item in self.result.objects]
        confirmations = [item.object_name for item in self.object_identity_confirmations]
        if (len(names) != len(set(names)) or len(confirmations) != len(set(confirmations))
                or set(names) != set(confirmations)):
            raise ValueError('每个对象须保留唯一对应的技术记录身份确认，不得遗漏或借用其他对象')
        return self


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8', errors='strict')


def _value(value):
    if type(value) is not dict:
        raise ValueError('新人工结构稿必须是闭合JSON对象')
    parsed = ManualValueV166.model_validate(value).model_dump()
    if _bytes(parsed) != _bytes(value) or len(_bytes(value)) > MAX_BYTES:
        raise ValueError('人工结构稿发生隐式转换或超出保存上限；原稿不能被裁剪')
    return copy.deepcopy(value)


def new_from_value(old_verified_value):
    """Fork a caller-verified V5 human value without changing its provenance.

    This structural function cannot establish that the old value was really
    saved, grounded or owned. The new Store must separately verify that basis.
    """
    original = copy.deepcopy(old_verified_value)
    checked = ManualReviewValueV5.model_validate(old_verified_value).model_dump()
    if _bytes(checked) != _bytes(old_verified_value):
        raise ValueError('原人工值不能在分叉时隐式改写')
    result = copy.deepcopy(checked['result'])
    result['schema'] = CANDIDATE
    for obj in result['objects']:
        obj['business_identity'] = {'status': 'not_established', 'field_labels': [],
            'citations': [], 'explanation': DEFAULT_IDENTITY_EXPLANATION}
    value = {'schema': SCHEMA, 'result': result, 'note': checked['note'],
        'object_identity_confirmations': [{'object_name': obj['name'],
            'acknowledge_platform_identity': False} for obj in result['objects']]}
    if old_verified_value != original:
        raise ValueError('原人工值在独立分叉中发生变化')
    return _value(value)


def validate_value(value, catalog, allowed_span_refs, context):
    """Return the unchanged complete value after structural/literal checks.

    The only old full-result validation view contains no objects. New object
    names are admitted there only after their own full shape, grounding, unit
    and identity checks, so no dummy required field enters an old validator.
    """
    checked = _value(value)
    result = checked['result']
    if type(context) is not dict:
        raise ValueError('缺少原冻结上下文')
    provided = _provided(catalog, allowed_span_refs)
    if (result['source_hash'] != catalog['descriptor']['source_hash']
            or result['catalog_hash'] != catalog['descriptor']['catalog_hash']):
        raise ValueError('新人工结构稿不属于原冻结来源与片段目录')
    _check_refs(_refs(result), provided, '/')
    existing = context.get('object_names', [])
    if (type(existing) is not list or any(type(name) is not str or not name.strip() for name in existing)
            or len(existing) != len(set(existing))):
        raise ValueError('原上下文对象名称无效')
    names = [obj['name'] for obj in result['objects']]
    if set(names) & set(existing):
        raise ValueError('新人工结构稿不能覆盖原上下文已有对象')
    # This is only a lexical quote view for the new objects; it is never stored
    # or validated as an old FullObject, and never receives invented fields.
    projected, source_index = _legacy_projection(result, provided)
    for index, obj in enumerate(projected['objects']):
        path = '/objects/' + str(index)
        _grounded(obj, source_index, path)
        identity = obj['business_identity']
        _grounded({'citations': identity['citations'], 'inference_reason': identity['explanation']},
            source_index, path + '/business_identity')
        for field_index, field in enumerate(obj['fields']):
            field_path = path + '/fields/' + str(field_index)
            _grounded(field, source_index, field_path)
            if field['unit'] is not None and not any(
                    unit_declared(field['label'], field['unit'], cite['quote'])
                    and unit_declared(field['label'], field['unit'], source_index[cite['source_ref']]['text'])
                    for cite in field['citations']):
                raise ValueError(field_path + ': 单位须由同一原片段对应字段，不能借用或换算')
    structural = candidate_with_ids(result, lambda prefix, path: prefix + digest({
        'domain': 'report-object-semantic-local-validation-v166', 'path': path})[:32])
    structural['objects'] = []
    structural['schema'] = 'report-evidence-candidate-v4'
    validation_context = copy.deepcopy(context)
    validation_context['object_names'] = [*existing, *names]
    if validate_evidence_result(structural, catalog, allowed_span_refs, validation_context) != structural:
        raise ValueError('业务、关系或局部引用在验证中变化')
    return checked


def confirm_value(value):
    """Confirm acknowledgements only; call validate_value with real scope first."""
    checked = _value(value)
    if not all(item['acknowledge_platform_identity'] is True
            for item in checked['object_identity_confirmations']):
        raise ValueError('须逐项确认系统生成技术记录身份不代表业务唯一性')
    return checked
