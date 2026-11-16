"""Read-only approved policy choices for one actual instance/release/object set.

This projects existing approval records. It never creates a policy, reservation,
Task, observation, provider call, device probe or permission to dispatch.
Mount this relative router beneath the existing /api/workspace router.
"""
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, StrictStr
from sqlalchemy import select

from .contracts import Contract
from .entity_release_compat import EntityReleaseContext, object_contract
from .models import DomainInstance, Entity, InstanceRelease, Membership, MonitorPolicy, User, now
from .perception import authorized_monitor
from .security import Principal, require
from .services import published, scoped
from .snapshot_authentication_v141 import snapshot_authenticated as authenticated
from .signal_dual_read_session import read_session
from .sensing_selection import validate_policy

SCHEMA = 'monitor-policy-options-v287'
MAX_OBJECTS = 200
MAX_POLICIES = 200
router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
ReadDB = Annotated[object, Depends(read_session)]
Identifier = Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
Hash = Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{64}$')]
Label = Annotated[StrictStr, Field(min_length=1, max_length=1000)]


class OptionField(Contract):
    key: Label
    label: Label


class PolicyOption(Contract):
    policy_id: Identifier
    approved_hash: Hash
    title: Label
    fields: list[OptionField] = Field(min_length=1, max_length=30)
    valid_until: StrictStr
    scope_label: Label


class PolicyOptions(Contract):
    schema: Literal['monitor-policy-options-v287']
    instance_id: Identifier
    release_id: Identifier
    entity_ids: list[Identifier] = Field(min_length=1, max_length=MAX_OBJECTS)
    options: list[PolicyOption] = Field(max_length=MAX_POLICIES)
    can_dispatch: Literal[False]


def need(ok, message='当前实例、发布版本或策略范围无法核对；未授予执行权限', status=409):
    if not ok:
        raise HTTPException(status, message)


def identifier(value):
    try:
        need(type(value) is str and str(UUID(value)) == value, '请使用当前页面选定的记录范围', 422)
    except (ValueError, TypeError, AttributeError):
        need(False, '记录范围格式无效；未读取观测策略', 422)
    return value


def selected_ids(raw):
    need(type(raw) is str and 0 < len(raw) <= MAX_OBJECTS * 37, '请先明确选择本次任务对象', 422)
    values = raw.split(',')
    need(0 < len(values) <= MAX_OBJECTS and len(values) == len(set(values)), '任务对象不能为空或重复', 422)
    return [identifier(value) for value in values]


def _instant(value):
    need(type(value) is str)
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        need(result.tzinfo is not None and result.utcoffset() is not None)
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        need(False)


def _reader(db, p):
    require(p, 'admin', 'operator', 'viewer')
    row = db.execute(select(User.id, Membership.role).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role == p.role)).first()
    need(row is not None and row.role == p.role, '当前账号或工作区权限已变化；未读取策略选项', 403)


def _scope(db, p, instance_id, release_id, entities):
    instance = scoped(db, DomainInstance, instance_id, p)
    need(instance.status == 'partially_active' and instance.active_release_id == release_id,
        '实例尚未启用此发布版本，原选择保留')
    release = scoped(db, InstanceRelease, release_id, p)
    need(release.instance_id == instance.id and release.workspace_id == p.workspace_id)
    package = published(db, p, release.package_id, 'domain_package')
    need(package.namespace == instance.namespace, '发布业务包不属于当前实例')
    context = EntityReleaseContext(db, p, instance, release, lock_sources=False)
    rows, contracts = [], {}
    for identity in entities:
        entity = scoped(db, Entity, identity, p)
        context.require(entity)  # Original package/namespace/type and complete release compatibility.
        rows.append(entity)
        if entity.entity_type not in contracts:
            contracts[entity.entity_type] = object_contract(release.data['snapshot'], entity.entity_type)
    return instance, release, rows, contracts


def _option(db, p, policy, entities, contracts, at):
    data = policy.data
    need(policy.workspace_id == p.workspace_id and policy.status == 'active'
        and type(data) is dict and type(data.get('entity_ids')) is list
        and type(data.get('fields')) is list and 0 < len(data['fields']) <= 30
        and len(data['fields']) == len(set(data['fields']))
        and all(type(key) is str and key.strip() for key in data['fields'])
        and {row.id for row in entities} <= set(data['entity_ids'])
        and bool(data.get('selection_requirement')) and bool(data.get('device_scope')))
    authorized_monitor(db, p, policy)
    from .workflow_task_context_v196 import _source_pins
    # Match the real context reader: even an additional, unbound source must
    # still be published and hash-valid. Asset validation alone checks only
    # the source of each selected asset and cannot establish this full pin set.
    _source_pins(db, p, policy)
    validate_policy(db, p, data)  # SQL declarations/approved device identity only; no health probe or dispatch.
    need(_instant(data.get('valid_until')) > at)
    fields = []
    for key in data['fields']:
        labels = []
        for entity in entities:
            choices = [field for field in contracts[entity.entity_type]['fields'] if field['key'] == key]
            need(len(choices) == 1 and type(choices[0].get('label')) is str and choices[0]['label'].strip(),
                '策略字段不属于全部已选对象的真实发布定义')
            if choices[0]['label'] not in labels:
                labels.append(choices[0]['label'])
        fields.append({'key': key, 'label': ' / '.join(labels)})
    title = data.get('title')
    if type(title) is not str or not title.strip():
        title = '、'.join(field['label'] for field in fields) + '补充观测策略'
    return PolicyOption.model_validate({'policy_id': policy.id, 'approved_hash': data['approved_hash'],
        'title': title, 'fields': fields, 'valid_until': _instant(data['valid_until']).isoformat(),
        'scope_label': '适用本次已选的 ' + str(len(entities)) + ' 个对象；仍须核对实际执行条件'}).model_dump(mode='json')


def options(db, p, instance_id, release_id, entity_ids):
    """One bounded SELECT-only projection inside the original snapshot session."""
    identifier(instance_id); identifier(release_id)
    need(type(entity_ids) is list and 0 < len(entity_ids) <= MAX_OBJECTS
        and len(entity_ids) == len(set(entity_ids)), '请明确选择不重复的任务对象', 422)
    identities = [identifier(value) for value in entity_ids]
    _reader(db, p)
    instance, release, entities, contracts = _scope(db, p, instance_id, release_id, identities)
    query = select(MonitorPolicy).where(MonitorPolicy.workspace_id == p.workspace_id,
        MonitorPolicy.status == 'active', MonitorPolicy.data['entity_ids'].contains(identities)).order_by(
            MonitorPolicy.created_at, MonitorPolicy.id).limit(MAX_POLICIES + 1)
    inventory = list(db.scalars(query))
    need(len(inventory) <= MAX_POLICIES, '可适用策略超过完整核对上限；请缩小范围或核对策略，未截断显示')
    at = now(); result = []
    for policy in inventory:
        try:
            result.append(_option(db, p, policy, entities, contracts, at))
        except HTTPException as error:
            if error.status_code not in {403, 404, 409, 422}:
                raise
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            continue  # An invalid original is never repaired or presented as an approved choice.
    return PolicyOptions.model_validate({'schema': SCHEMA, 'instance_id': instance.id, 'release_id': release.id,
        'entity_ids': identities, 'options': result, 'can_dispatch': False}).model_dump(mode='json')


@router.get('/instances/{instance_id}/monitor-policy-options-v287', response_model=PolicyOptions)
def directory(instance_id: UUID, release_id: UUID, p: P, db: ReadDB,
              entity_ids: str = Query(min_length=1, max_length=MAX_OBJECTS * 37)):
    return options(db, p, str(instance_id), str(release_id), selected_ids(entity_ids))
