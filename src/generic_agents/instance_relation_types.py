"""Human-confirmed relationship definitions and display metadata, never inferred edges."""
import copy
from typing import Literal
from fastapi import HTTPException
from pydantic import Field, model_validator
from .contracts import Contract


class ConfirmedRelationType(Contract):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    source_type: str = Field(min_length=1, max_length=100)
    target_type: str = Field(min_length=1, max_length=100)
    cardinality: Literal['many', 'one_per_source', 'one_per_target', 'unspecified']
    allow_self: bool = False

    @model_validator(mode='after')
    def clean(self):
        if not self.description.strip():
            raise ValueError('请说明关系含义和人工确认依据')
        for key in ['name', 'source_type', 'target_type']:
            value = getattr(self, key)
            if value != value.strip() or not value.strip():
                raise ValueError('关系名称和两端对象类型不能为空或含首尾空白')
        return self


def compile_types(db, p, config, object_names):
    from .instance_compiler import identifier
    from .setup_relation_proposals import verify_origins, origin_material_ids
    raw = config.get('instanceRelationTypes', [])
    if not isinstance(raw, list) or len(raw) > 30:
        raise HTTPException(422, '最多配置30种对象关系')
    try:
        definitions = [ConfirmedRelationType.model_validate(item) for item in raw]
    except ValueError:
        raise HTTPException(422, '对象关系配置无效，请核对名称、两端对象类型与数量约束')
    if len({item.name for item in definitions}) != len(definitions):
        raise HTTPException(422, '对象关系名称不能重复')
    if any(item.source_type not in object_names or item.target_type not in object_names for item in definitions):
        raise HTTPException(422, '关系的两端须使用本次确认的对象类型；改名或删除后请同步核对关系')
    origins=config.get('instanceRelationOrigins',{})
    if not isinstance(origins,dict) or any(item.name in origins and not isinstance(origins[item.name],dict) for item in definitions):
        raise HTTPException(422,'关系来源记录格式无法核对；请保留原配置并恢复对应来源')
    verify_origins(db, p, config, [item.name for item in definitions])
    result = {}; issues = []
    for item in definitions:
        if item.cardinality == 'unspecified':
            issues.append('关系“'+item.name+'”尚未确认数量约束，请选择允许多条或每端只关联一条')
            continue
        result[identifier('relation', item.name)] = {
            'title': item.name, 'description': item.description,
            'source_types': [identifier('object', item.source_type)],
            'target_types': [identifier('object', item.target_type)],
            'attribute_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'cardinality': item.cardinality, 'allow_self': item.allow_self}
    return result, issues, sorted(origin_material_ids(config, [item.name for item in definitions]))


def relation_metadata(package, entities):
    """Only show objects supplied by the owning instance or original Task scope."""
    spec = package.data['specification']; schemas = spec['entity_schemas']
    types = [{'id': identity, **copy.deepcopy(value)} for identity, value in spec.get('relation_types', {}).items()]
    rows = []
    for entity in entities:
        item = entity if isinstance(entity, dict) else {
            'id': entity.id, 'entity_type': entity.entity_type, 'attributes': entity.data}
        kind = item.get('entity_type', item.get('type'))
        if kind is None and item.get('type_name'):
            kind = next((key for key,value in schemas.items() if value.get('title') == item['type_name']), None)
        schema = schemas.get(kind, {})
        properties = schema.get('properties', {}); attributes = item.get('attributes', {})
        text_keys = [key for key in schema.get('required', []) if properties.get(key, {}).get('type') == 'string']
        title = next((attributes[key] for key in text_keys
            if isinstance(attributes.get(key), str) and attributes[key].strip()), None)
        if title is None:
            title = schema.get('title', '对象')+' · '+str(item['id'])[:8]
        rows.append({'id': item['id'], 'type': kind, 'title': title,
            'type_title': schema.get('title', kind)})
    return {'package_id': package.id, 'relation_types': types, 'entities': rows}
