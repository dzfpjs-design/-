"""Human-authored local numeric rules and durable, non-observation sample checks."""
import copy
import math
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import Field, field_validator, model_validator
from sqlalchemy import and_, func, or_, select, text

from .contracts import Contract, Manifest, digest
from .models import Audit, Capability, Entity, Membership, User
from .services import (audit, check_version, publish, published, register, require,
                       scoped, validate, validate_capability)
from .setup_api import DB, P

router = APIRouter(prefix='/api/local-rules')
from .threshold_evolution_api_v147 import router as evolution_router
router.include_router(evolution_router)
from .threshold_request_recovery_api_v148 import router as evolution_recovery_router
router.include_router(evolution_recovery_router)
from .threshold_feedback_history_v148 import router as evolution_history_router
router.include_router(evolution_history_router)
from .threshold_study_api_v149 import router as threshold_study_router
router.include_router(threshold_study_router)
from .threshold_shadow_api_v151 import router as threshold_shadow_router
router.include_router(threshold_shadow_router)
from .quality_review_api_v152 import router as threshold_quality_router
router.include_router(threshold_quality_router)
from .threshold_policy_api_v153 import router as threshold_policy_router
router.include_router(threshold_policy_router)
NAMESPACE = 'platform_numeric_rules'
SCHEMA = 'local-numeric-rule-v1'
CHECK_SCHEMA = 'local-numeric-rule-check-v1'
CREATE_ACTION = 'local_rule.created'
CHECK_ACTION = 'local_rule.checked'


def _write_access(db, p, *roles):
    require(p, *roles)
    membership = db.scalar(select(Membership).join(User, User.id == Membership.user_id)
        .where(Membership.workspace_id == p.workspace_id, Membership.user_id == p.user_id, User.active.is_(True))
        .with_for_update(of=[Membership, User]).execution_options(populate_existing=True))
    if not membership or membership.role not in roles:
        raise HTTPException(403, '当前账号或工作区操作权限已变化，请刷新后核对')


def _finite(value):
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except (OverflowError, TypeError):
        return False


class NumericRule(Contract):
    id: str = Field(min_length=1, max_length=80)
    field: str = Field(min_length=1, max_length=100)
    unit: str | None = Field(default=None, max_length=80)
    operator: Literal['gt', 'lt']
    threshold: int | float

    @field_validator('id', 'field', 'unit')
    @classmethod
    def meaningful(cls, value):
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError('规则标识、字段和单位不能留空或带首尾空格')
        return value

    @field_validator('threshold', mode='before')
    @classmethod
    def finite_threshold(cls, value):
        if not _finite(value) or abs(value) > 1e15:
            raise ValueError('阈值须为绝对值不超过1e15的有限数值，不能使用布尔值')
        return value


class Definition(Contract):
    title: str = Field(min_length=1, max_length=200)
    entity_id: str = Field(min_length=1, max_length=36)
    rationale: str = Field(min_length=1, max_length=2000)
    rules: list[NumericRule] = Field(min_length=1, max_length=32)

    @field_validator('title', 'rationale')
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError('请填写规则名称和阈值依据')
        return value

    @model_validator(mode='after')
    def unique_ids(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError('每条规则须使用唯一标识')
        return self


class CreateRule(Definition):
    request_id: UUID


class UpdateRule(Definition):
    expected_version: int = Field(ge=1, strict=True)


class CheckRule(Contract):
    expected_version: int = Field(ge=1, strict=True)
    values: dict[str, Any] = Field(min_length=1, max_length=32)

    @field_validator('values')
    @classmethod
    def finite_values(cls, values):
        if any(not _finite(value) for value in values.values()):
            raise ValueError('试算值须为有限数值，不能使用布尔值或数字字符串')
        return values


class PublishRule(Contract):
    expected_version: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=2000)
    confirm: Literal[True]

    @field_validator('confirm', mode='before')
    @classmethod
    def explicit_confirm(cls, value):
        if value is not True:
            raise ValueError('请明确确认发布')
        return value

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError('请填写发布依据')
        return value


def _metadata(cap):
    specification = cap.data.get('specification', {})
    metadata = specification.get('managed_numeric_rule') if isinstance(specification, dict) else None
    if not isinstance(metadata, dict) or metadata.get('schema') != SCHEMA:
        raise HTTPException(422, '规则缺少受管理的本地数值规则契约')
    return metadata


def _managed(db, p, identity, lock=False):
    cap = scoped(db, Capability, identity, p, lock)
    if cap.namespace != NAMESPACE or not isinstance(cap.data.get('specification'), dict) or 'managed_numeric_rule' not in cap.data['specification']:
        raise HTTPException(404, '本地规则不存在或不在当前工作区')
    return cap


def _context(db, p, definition, lock=False):
    entity = scoped(db, Entity, definition.entity_id, p, lock)
    package = published(db, p, entity.schema_capability_id, 'domain_package')
    schemas = package.data.get('specification', {}).get('entity_schemas', {})
    schema = schemas.get(entity.entity_type) if isinstance(schemas, dict) else None
    if not isinstance(schema, dict) or entity.namespace != package.namespace:
        raise HTTPException(422, '对象类型与已发布业务包不一致')
    properties = schema.get('properties', {})
    field_units = {}
    fields = {}
    for rule in definition.rules:
        field_schema = properties.get(rule.field)
        if not isinstance(field_schema, dict) or field_schema.get('type') not in {'number', 'integer'}:
            raise HTTPException(422, '规则字段须属于此对象类型已发布的数值字段')
        if field_schema.get('unit') is not None and field_schema['unit'] != rule.unit:
            raise HTTPException(422, '规则单位与业务字段声明不一致；请核对，不自动换算')
        if rule.field in field_units and field_units[rule.field] != rule.unit:
            raise HTTPException(422, '同一业务字段的规则须使用一致单位')
        fields[rule.field] = copy.deepcopy(field_schema)
        field_units[rule.field] = rule.unit
    return entity, package, fields, field_units


def _manifest(db, p, definition, request_id, fingerprint, owner, lock=False):
    entity, package, fields, field_units = _context(db, p, definition, lock)
    return _assemble_manifest(definition, request_id, fingerprint, owner, package.id, entity.entity_type,
                              entity.id, fields, field_units)


def _assemble_manifest(definition, request_id, fingerprint, owner, package_id, entity_type, entity_id,
                       fields, field_units):
    metadata = {'schema': SCHEMA, 'title': definition.title, 'rationale': definition.rationale,
                'package_id': package_id, 'entity_type': entity_type, 'entity_id': entity_id,
                'field_units': field_units, 'creation_request_id': str(request_id),
                'creation_fingerprint': fingerprint}
    return Manifest(kind='tool', namespace=NAMESPACE, name='rule_' + request_id.hex,
        version='1.0.0', owner=owner, description=definition.title,
        allowed_agent_roles=['perception'],
        execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False},
        dependencies=[package_id],
        input_schema={'type': 'object', 'required': ['attributes'], 'properties': {
            'attributes': {'type': 'object', 'required': sorted(fields), 'properties': fields}}},
        output_schema={'type': 'object', 'required': ['attributes', 'anomalies', 'method'],
            'properties': {'attributes': {'type': 'object'}, 'anomalies': {'type': 'array'},
                           'method': {'const': 'registered_numeric_rules'}}},
        specification={'rules': [rule.model_dump() for rule in definition.rules],
                       'managed_numeric_rule': metadata})


def _creation(db, p, cap):
    row = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == cap.id, Audit.action == CREATE_ACTION).order_by(Audit.created_at, Audit.id).limit(1))
    if not row:
        raise HTTPException(422, '规则缺少持久化创建依据，不能作为受管理规则发布')
    return row


def _saved_manifest(db, p, cap):
    """Verify the immutable authoring audit and saved contract without live dependencies."""
    if cap.workspace_id != p.workspace_id:
        raise HTTPException(404, '记录不存在或不在当前工作区')
    metadata = _metadata(cap)
    created = _creation(db, p, cap)
    try:
        request_id = UUID(metadata['creation_request_id'])
        UUID(metadata['package_id'])
        UUID(metadata['entity_id'])
        if not isinstance(metadata['entity_type'], str) or not metadata['entity_type'].strip():
            raise ValueError()
        definition = Definition.model_validate({'title': metadata['title'], 'rationale': metadata['rationale'],
            'entity_id': metadata['entity_id'], 'rules': cap.data['specification']['rules']})
        fields = cap.data['input_schema']['properties']['attributes']['properties']
        if (not isinstance(fields, dict) or set(fields) != {rule.field for rule in definition.rules}
                or any(not isinstance(schema, dict) or schema.get('type') not in {'number', 'integer'}
                       for schema in fields.values())):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, '规则名称、依据、对象或条件不符合本地契约')
    if (created.data.get('schema') != SCHEMA or created.data.get('creation_request_id') != str(request_id)
            or created.data.get('creation_fingerprint') != metadata.get('creation_fingerprint')):
        raise HTTPException(409, '规则的创建请求依据已变化，请核对保存记录')
    field_units = {}
    for rule in definition.rules:
        if (rule.field in field_units and field_units[rule.field] != rule.unit
                or fields[rule.field].get('unit') is not None and fields[rule.field]['unit'] != rule.unit):
            raise HTTPException(422, '保存规则的字段单位不一致')
        field_units[rule.field] = rule.unit
    try:
        canonical = _assemble_manifest(definition, request_id, metadata['creation_fingerprint'], created.actor_id,
            metadata['package_id'], metadata['entity_type'], metadata['entity_id'], fields, field_units).model_dump(mode='json')
    except ValueError:
        raise HTTPException(422, '保存规则的字段契约无效')
    if (cap.kind != 'tool' or cap.namespace != NAMESPACE or cap.name != canonical['name']
            or cap.release_version != '1.0.0' or cap.data != canonical):
        raise HTTPException(422, '本地规则须使用固定感知契约、已发布业务字段和本地数据策略')
    if cap.content_hash != digest(cap.data):
        raise HTTPException(409, '规则内容与定义摘要不一致，请重新核对保存版本')
    latest_definition = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == cap.id, Audit.action.in_([CREATE_ACTION, 'local_rule.updated']))
        .order_by(Audit.created_at.desc(), Audit.id.desc()).limit(1))
    if not latest_definition or latest_definition.data.get('definition_hash') != cap.content_hash:
        raise HTTPException(409, '规则内容与持久化编辑依据不一致，请核对保存版本')
    return definition


def validate_managed_manifest(db, p, cap):
    """Read-only canonical contract validation for both this API and public Registry."""
    definition = _saved_manifest(db, p, cap)
    metadata = _metadata(cap)
    created = _creation(db, p, cap)
    canonical = _manifest(db, p, definition, UUID(metadata['creation_request_id']),
        metadata['creation_fingerprint'], created.actor_id).model_dump(mode='json')
    if cap.data != canonical:
        raise HTTPException(422, '规则与当前业务对象、字段或单位不一致，请重新核对')
    return definition


def _calculate(db, p, cap, values):
    validate_managed_manifest(db, p, cap)
    return _calculate_saved(cap, values)


def _calculate_saved(cap, values):
    metadata = _metadata(cap)
    definition = Definition.model_validate({'title': metadata['title'], 'rationale': metadata['rationale'],
        'entity_id': metadata['entity_id'], 'rules': cap.data['specification']['rules']})
    fields = cap.data['input_schema']['properties']['attributes']['properties']
    if set(values) != set(fields) or any(not _finite(value) for value in values.values()):
        raise HTTPException(422, '请为规则涉及的每个业务字段填写有限数值，不能遗漏或另填字段')
    for field, value in values.items():
        validate(fields[field], value)
    return [{'id': rule.id, 'field': rule.field, 'value': values[rule.field], 'unit': rule.unit,
             'operator': rule.operator, 'threshold': rule.threshold,
             'matched': values[rule.field] > rule.threshold if rule.operator == 'gt' else values[rule.field] < rule.threshold}
            for rule in definition.rules]


def _current_check(db, p, cap):
    # A successful check validates the same transaction's version. Publishing adds one version.
    checked_version = cap.version - (1 if cap.status == 'published' else 0)
    if cap.status not in {'validated', 'published'}:
        return None
    return db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == cap.id, Audit.action == CHECK_ACTION,
        Audit.data['definition_hash'].as_string() == cap.content_hash,
        Audit.data['checked_version'].as_string() == str(checked_version))
        .order_by(Audit.created_at.desc(), Audit.id.desc()).limit(1))


def _check_view(db, p, cap, proof):
    value = proof.data
    if (not isinstance(value, dict) or value.get('schema') != CHECK_SCHEMA or value.get('definition_hash') != digest(cap.data)
            or value.get('check_mode') != 'local_sample_only' or value.get('is_device_observation') is not False
            or not isinstance(value.get('values'), dict)):
        raise HTTPException(409, '已保存试算依据与当前规则不一致，请重新试算')
    try:
        expected = _calculate_saved(cap, value['values'])
    except (HTTPException, ValueError, KeyError, TypeError):
        raise HTTPException(409, '已保存试算输入与当前字段契约不一致，请重新试算')
    if digest(value.get('results')) != digest(expected):
        raise HTTPException(409, '已保存逐条试算结果与当前规则不一致，请重新试算')
    return {'id': proof.id, 'definition_hash': value['definition_hash'], 'values': value['values'],
            'results': value['results'], 'created_at': proof.created_at.isoformat(),
            'check_mode': 'local_sample_only', 'is_device_observation': False}


def require_current_check(db, p, cap):
    """Return the current persistent sample proof; no execution or state writes."""
    validate_managed_manifest(db, p, cap)
    proof = _current_check(db, p, cap)
    if not proof:
        raise HTTPException(409, '请先对当前规则版本试算并保存结果，再确认发布')
    _check_view(db, p, cap, proof)
    return proof


def view(db, p, cap):
    _saved_manifest(db, p, cap)
    metadata = _metadata(cap)
    proof = _current_check(db, p, cap)
    context_warning = None
    try:
        validate_managed_manifest(db, p, cap)
    except HTTPException as error:
        context_warning = error.detail if isinstance(error.detail, str) else '业务字段已变化，历史规则仍保留；请核对后新建草稿'
    return {'id': cap.id, 'title': metadata['title'], 'rationale': metadata['rationale'],
            'entity_id': metadata['entity_id'], 'package_id': metadata['package_id'],
            'entity_type': metadata['entity_type'], 'rules': cap.data['specification']['rules'],
            'status': cap.status, 'version': cap.version, 'definition_hash': cap.content_hash,
            'last_check': _check_view(db, p, cap, proof) if proof else None,
            'context_available': context_warning is None, 'context_warning': context_warning}


@router.get('')
def list_rules(p: P, db: DB, response: Response, limit: int = Query(default=50, ge=1, le=100), cursor: str | None = None):
    filters = [Capability.workspace_id == p.workspace_id, Capability.namespace == NAMESPACE,
               Capability.data['specification']['managed_numeric_rule']['schema'].as_string() == SCHEMA,
               select(Audit.id).where(Audit.workspace_id == p.workspace_id,
                   Audit.object_id == Capability.id, Audit.action == CREATE_ACTION).exists()]
    response.headers['X-Total-Count'] = str(db.scalar(select(func.count()).select_from(Capability).where(*filters)))
    query = select(Capability).where(*filters)
    if cursor:
        anchor = _managed(db, p, cursor)
        query = query.where(or_(Capability.created_at < anchor.created_at,
            and_(Capability.created_at == anchor.created_at, Capability.id < anchor.id)))
    rows = list(db.scalars(query.order_by(Capability.created_at.desc(), Capability.id.desc()).limit(limit + 1)))
    response.headers['X-Next-Cursor'] = rows[limit - 1].id if len(rows) > limit else ''
    return [view(db, p, cap) for cap in rows[:limit]]


@router.post('', status_code=201)
def create_rule(body: CreateRule, p: P, db: DB):
    _write_access(db, p, 'admin', 'operator')
    fingerprint = digest(body.model_dump(mode='json'))
    key = int(digest({'local_rule_workspace': p.workspace_id, 'request': str(body.request_id)})[:16], 16) - (1 << 63)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
    prior = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.namespace == NAMESPACE, Capability.name == 'rule_' + body.request_id.hex,
        Capability.release_version == '1.0.0').with_for_update().execution_options(populate_existing=True))
    if prior:
        created = _creation(db, p, prior)
        if created.data.get('creation_fingerprint') != fingerprint:
            raise HTTPException(409, '同一创建请求已用于其他规则内容，请核对已保存草稿')
        result = view(db, p, prior)
        db.commit()
        return result
    manifest = _manifest(db, p, body, body.request_id, fingerprint, p.user_id, lock=True)
    cap = register(db, p, manifest)
    audit(db, p, CREATE_ACTION, cap.id, {'schema': SCHEMA, 'creation_request_id': str(body.request_id),
                                       'creation_fingerprint': fingerprint, 'definition_hash': cap.content_hash})
    db.flush()
    result = view(db, p, cap)
    db.commit()
    return result


@router.get('/{identity}')
def get_rule(identity: str, p: P, db: DB):
    return view(db, p, _managed(db, p, identity))


@router.put('/{identity}')
def update_rule(identity: str, body: UpdateRule, p: P, db: DB):
    _write_access(db, p, 'admin', 'operator')
    cap = _managed(db, p, identity, lock=True)
    check_version(cap, body.expected_version)
    validate_managed_manifest(db, p, cap)
    if cap.status not in {'draft', 'validated'}:
        raise HTTPException(409, '已发布规则不能覆盖修改；请复制为新草稿')
    metadata = _metadata(cap)
    created = _creation(db, p, cap)
    manifest = _manifest(db, p, body, UUID(metadata['creation_request_id']), metadata['creation_fingerprint'],
                         created.actor_id, lock=True)
    cap.data = manifest.model_dump(mode='json')
    cap.content_hash = digest(cap.data)
    cap.validated_hash = None
    cap.status = 'draft'
    cap.version += 1
    audit(db, p, 'local_rule.updated', cap.id, {'definition_hash': cap.content_hash, 'version': cap.version})
    db.flush()
    result = view(db, p, cap)
    db.commit()
    return result


@router.post('/{identity}/check')
def check_rule(identity: str, body: CheckRule, p: P, db: DB):
    _write_access(db, p, 'admin', 'operator')
    cap = _managed(db, p, identity, lock=True)
    check_version(cap, body.expected_version)
    if cap.status not in {'draft', 'validated'}:
        raise HTTPException(409, '已发布规则已冻结；请复制草稿后试算')
    results = _calculate(db, p, cap, body.values)
    audit(db, p, CHECK_ACTION, cap.id, {'schema': CHECK_SCHEMA, 'definition_hash': cap.content_hash,
        'checked_version': cap.version + 1, 'values': body.values, 'results': results,
        'check_mode': 'local_sample_only', 'is_device_observation': False})
    cap.status = 'draft'
    cap.validated_hash = None
    db.flush()
    validate_capability(db, p, cap.id, body.expected_version)
    result = view(db, p, cap)
    db.commit()
    return result


@router.post('/{identity}/publish')
def publish_rule(identity: str, body: PublishRule, p: P, db: DB):
    _write_access(db, p, 'admin')
    cap = _managed(db, p, identity, lock=True)
    check_version(cap, body.expected_version)
    require_current_check(db, p, cap)
    if cap.status == 'published':
        published(db, p, cap.id, 'tool')
    else:
        publish(db, p, cap.id, body.expected_version, body.reason)
    result = view(db, p, cap)
    db.commit()
    return result
