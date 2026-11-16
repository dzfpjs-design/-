"""Independent all-optional object types; no source or execution authority.

Callers must authenticate a genuine issued V170 schema origin before choosing
this family. A client marker alone cannot select it for compilation. Business
identity and explicit platform-record acknowledgement remain separate from
attribute constraints; neither creates a unique business key or data value.
The legacy compiler ObjectType and proposal contracts are not imported/changed.
"""
import copy
import hashlib
from typing import Literal

from pydantic import Field, model_validator

from .report_full_reading_semantics import Closed
from .report_object_semantics_v166 import BusinessIdentity, IdentityConfirmation
from .typed_payload_codec_v131 import canonical_json

SCHEMA = 'report-object-type-v170'
_KINDS = {'文本': {'type': 'string', 'maxLength': 2000},
    '整数': {'type': 'integer'}, '数值': {'type': 'number'},
    '是否': {'type': 'boolean'}, '日期': {'type': 'string', 'format': 'date'}}


class ObjectFieldV170(Closed):
    label: str = Field(min_length=1, max_length=80)
    kind: Literal['文本', '整数', '数值', '是否', '日期']
    required: bool = Field(strict=True)
    unit: str | None = Field(max_length=40)

    @model_validator(mode='after')
    def exact_declared_field(self):
        if self.label != self.label.strip() or not self.label:
            raise ValueError('字段名称须明确且无首尾空白，不能自动改写')
        if self.unit is not None:
            if not self.unit or self.unit != self.unit.strip():
                raise ValueError('单位须为原明确文字或null，不能自动去空白或补造单位')
            if self.kind not in {'整数', '数值'}:
                raise ValueError('仅整数或数值字段可声明单位，不能改写字段类型')
        return self


class ObjectTypeV170(Closed):
    schema: Literal['report-object-type-v170']
    name: str = Field(min_length=1, max_length=100)
    fields: list[ObjectFieldV170] = Field(min_length=1, max_length=40)
    business_identity: BusinessIdentity
    platform_identity_confirmation: IdentityConfirmation

    @model_validator(mode='after')
    def explicit_identity(self):
        labels = [field.label for field in self.fields]
        if self.name != self.name.strip() or not self.name or len(labels) != len(set(labels)):
            raise ValueError('对象名称须明确，字段不能为空或重复，不能自动补名称')
        if set(self.business_identity.field_labels) - set(labels):
            raise ValueError('业务标识只能引用本对象真实已有字段')
        confirmation = self.platform_identity_confirmation
        if confirmation.object_name != self.name or confirmation.acknowledge_platform_identity is not True:
            raise ValueError('须明确确认本对象的技术记录身份；不能借用其他对象或业务编号')
        return self


def check_object_type(value):
    """Return the exact detached five-key value, without trimming/defaults."""
    if type(value) is not dict:
        raise ValueError('新对象类型必须为闭合JSON对象')
    original = canonical_json(value)
    checked = ObjectTypeV170.model_validate(value).model_dump()
    if canonical_json(checked) != original:
        raise ValueError('新对象字段、单位或身份说明发生隐式转换')
    return copy.deepcopy(checked)


def _identifier(prefix, label):
    # Same stable field/type key derivation as the existing compiler, without
    # borrowing its required-text validation or fabricating an attribute.
    return prefix + '_' + hashlib.sha256(label.encode('utf-8', 'strict')).hexdigest()[:16]


def _attribute_schema(checked):
    properties, required = {}, []
    for field in checked['fields']:
        key = _identifier('field', field['label'])
        if key in properties:
            raise ValueError('字段键发生冲突，不能覆盖另一原字段')
        prop = {**copy.deepcopy(_KINDS[field['kind']]), 'title': field['label']}
        if field['unit'] is not None:
            prop['unit'] = field['unit']
        if field['required']:
            required.append(key)
            if field['kind'] == '文本':
                prop.update(minLength=1, pattern=r'\S')
        properties[key] = prop
    return {'type': 'object', 'title': checked['name'], 'properties': properties,
        'required': required, 'additionalProperties': False}


def build_attribute_schema(value):
    """Build only declared attribute constraints; optional does not mean null.

    An all-optional object has required=[], so attributes={} is valid. Source
    citations and human identity descriptions belong to the separately pinned
    type/origin, not to invented required/unique business attributes.
    """
    return _attribute_schema(check_object_type(value))


def object_contract(value):
    """Detached pure definition for catalog/schema cold rebuilding.

    Closed output keys: key/name/fields/schema/business_identity/
    platform_identity_confirmation. Here schema is the JSON attribute schema;
    callers retain the original type's SCHEMA marker in their issued origin.
    This helper does not load or authorize an origin, release, package or Task.
    """
    checked = check_object_type(value)
    return {'key': _identifier('object', checked['name']), 'name': checked['name'],
        'fields': [{'key': _identifier('field', field['label']), **copy.deepcopy(field)}
            for field in checked['fields']],
        'schema': _attribute_schema(checked),
        'business_identity': copy.deepcopy(checked['business_identity']),
        'platform_identity_confirmation': copy.deepcopy(checked['platform_identity_confirmation'])}
