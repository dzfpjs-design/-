"""Conservative field-reading disambiguation, never query authority.

Only actual selected Entity/published Schema rows construct the internal
context. The original question stays untouched; tools, scope, dates and data
ACKs remain the responsibility of the existing query pipeline.
"""
import re
from dataclasses import dataclass

# An explicit operator anywhere wins, including in negations and quoted text.
# Do not remove such clauses and then call field reading a statistical answer.
HARD=re.compile(r'平均|汇总|总计|合计|总数|总量|计数|统计|求和|计算|趋势|极值|最大|最小|最高|最低|'
    r'aggregate|average|trend|count\b|\bsum\b|\bmean\b|\bmaximum\b|\bminimum\b',re.I)
DIRECT=re.compile(r'\s*(?:请)?(?:只需|仅需|只|仅)?(?P<per_row>逐条|分别)?'
    r'(?:读取|查看|核对|列出|展示)'
    r'(?:(?:本|该|此|所选|已选)?(?:对象|记录)(?:的)?)?'
    r'(?P<registered>已登记|已保存|登记的|保存的)?(?:的)?'
    r'(?:数量|“数量”|"数量"|「数量」|『数量』)'
    r'(?P<field_value>字段的值|字段值|字段|的值|值)?'
    r'(?:[，,；;]\s*(?:不调用云或设备|不调用云模型或设备))?[。.]?\s*')


@dataclass(frozen=True)
class SelectedFields:
    """Internal immutable result of a bounded server Schema read, not a DTO."""
    entity_ids:tuple[str,...]
    fields:tuple[str,...]
    quantity_fields:tuple[str,...]


def direct_read(question):
    if type(question) is not str or HARD.search(question):return None
    matched=DIRECT.fullmatch(question)
    if matched is None or not (matched['registered'] or matched['field_value']):return None
    return matched


def unsupported(question,field_context=None):
    if HARD.search(question):return True
    if '数量' not in question:return False
    matched=direct_read(question)
    if matched is None or not isinstance(field_context,SelectedFields):return True
    context=field_context
    if (any(type(items) is not tuple for items in (context.entity_ids,context.fields,context.quantity_fields))
        or any(type(value) is not str or not value for items in (context.entity_ids,context.fields,context.quantity_fields) for value in items)
        or not context.entity_ids or not context.fields or not context.quantity_fields
        or len(set(context.entity_ids))!=len(context.entity_ids)
        or len(set(context.fields))!=len(context.fields)
        or not set(context.quantity_fields).issubset(context.fields)):
        return True
    # More than one selected object must explicitly request individual values.
    return len(context.entity_ids)>1 and matched['per_row'] is None


def context_for_request(db,p,request):
    """Load only original explicitly selected fields; no tool/model discovery.

    Missing/inconsistent field definitions remain ambiguous. Cross-workspace,
    withdrawn or corrupted package errors retain the existing service errors.
    A model's proposed fields and public extra JSON are never inputs here.
    """
    if direct_read(request['question']) is None:return None
    # An absent repository/session is absence of Schema proof, never a local
    # shortcut that grants the quantity exception (also keeps pure callers safe).
    if db is None:return None
    identities=request.get('entity_ids');fields=request.get('fields')
    if (type(identities) is not list or type(fields) is not list or not identities or not fields
        or len(identities)>30 or len(fields)>30
        or any(type(value) is not str or not value for value in [*identities,*fields])
        or len(identities)!=len(set(identities)) or len(fields)!=len(set(fields))):
        return None
    from .models import Entity
    from .services import scoped,published
    from .contracts import digest
    packages={};original=None;quantities=None;type_identity=None
    for identity in identities:
        entity=scoped(db,Entity,identity,p);db.refresh(entity)
        actual_type=(entity.schema_capability_id,entity.entity_type)
        if type_identity is not None and actual_type!=type_identity:return None
        type_identity=actual_type
        if entity.schema_capability_id not in packages:
            package=published(db,p,entity.schema_capability_id,'domain_package');db.refresh(package)
            packages[package.id]=published(db,p,package.id,'domain_package')
        package=packages[entity.schema_capability_id]
        specification=package.data.get('specification')
        if type(specification) is not dict:return None
        schemas=specification.get('entity_schemas')
        if type(schemas) is not dict:return None
        schema=schemas.get(entity.entity_type)
        if type(schema) is not dict or schema.get('type')!='object':return None
        properties=schema.get('properties')
        if type(properties) is not dict or any(type(properties.get(field)) is not dict for field in fields):return None
        selected={field:properties[field] for field in fields}
        current=digest(selected)
        if original is not None and current!=original:return None
        original=current
        quantities=tuple(field for field in fields if properties[field].get('title')=='数量')
        if not quantities:return None
    return SelectedFields(tuple(identities),tuple(fields),quantities)
