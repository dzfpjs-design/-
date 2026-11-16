"""Same-instance object definitions across releases; never execution authority.

The source Entity and old Task are immutable here. New tasks carry a private
binding, while tool inputs continue to use their original object snapshots.
"""
import copy
import re

from fastapi import HTTPException
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from .contracts import digest
from .models import Audit, Capability, DomainInstance, Entity, InstanceRelease
from .services import published, scoped

BINDINGS = 'object_definition_bindings'
BINDINGS_HASH = 'object_definition_bindings_hash'
SCHEMA = 'instance-entity-bindings-v1'
DEFINITION_KEYS = frozenset({'schema', 'entity_id', 'entity_type', 'instance_id', 'namespace',
    'source_release_id', 'source_configuration_hash', 'source_package_id', 'source_package_hash',
    'target_release_id', 'target_configuration_hash', 'target_package_id', 'target_package_hash',
    'type_identity', 'contract_hash'})
DEFINITION_HASHES = frozenset({'source_configuration_hash', 'source_package_hash',
    'target_configuration_hash', 'target_package_hash', 'type_identity', 'contract_hash'})


def _text(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def _sha256(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _definition_structure(value):
    new = isinstance(value, dict) and value.get('schema') == 'instance-entity-definition-v170'
    keys = DEFINITION_KEYS | {'object_type_contract'} if new else DEFINITION_KEYS
    if (not isinstance(value, dict) or set(value) != keys
            or value['schema'] not in {'instance-entity-definition-v1', 'instance-entity-definition-v170'}
            or any(not _sha256(value[key]) for key in DEFINITION_HASHES)
            or any(not _text(value[key]) for key in DEFINITION_KEYS - DEFINITION_HASHES - {'schema'})):
        blocked('已保存对象来源定义结构无法核对')
    if new:
        metadata = value['object_type_contract']
        if (type(metadata) is not dict or set(metadata) != {'contract_schema', 'business_identity', 'platform_identity_confirmation'}
                or metadata['contract_schema'] != 'report-object-type-v170'):
            blocked('已保存的新对象身份合同无法核对')


def blocked(reason='对象来源版本或完整定义无法核对，原记录保留'):
    raise HTTPException(409, reason)


def object_contract(snapshot, key):
    """Reconcile the full generated catalog and schema, not selected fields."""
    from .instance_compiler import KINDS, ObjectType, identifier
    try:
        catalog = snapshot['object_types']
        matches = [row for row in catalog if row.get('key') == key]
        if len(matches) != 1:
            blocked('当前版本已移除该对象类型，需明确迁移后才能使用')
        row = matches[0]
        if row.get('contract_schema') == 'report-object-type-v170':
            from .object_type_contract_v170 import object_contract as new_contract
            if set(row) != {'key', 'name', 'fields', 'contract_schema', 'business_identity', 'platform_identity_confirmation'}:
                blocked()
            raw_fields = row['fields']
            if type(raw_fields) is not list or any(type(field) is not dict or
                    set(field) != {'key', 'label', 'kind', 'required', 'unit'} for field in raw_fields):
                blocked()
            expected = new_contract({'schema': row['contract_schema'], 'name': row['name'],
                'fields': [{k: v for k, v in field.items() if k != 'key'} for field in raw_fields],
                'business_identity': copy.deepcopy(row['business_identity']),
                'platform_identity_confirmation': copy.deepcopy(row['platform_identity_confirmation'])})
            catalog = {k: v for k, v in expected.items() if k != 'schema'}
            catalog['contract_schema'] = 'report-object-type-v170'
            if digest(catalog) != digest(row) or expected['key'] != key:
                blocked('新对象目录、技术身份或完整单位约束无法核对')
            schema = copy.deepcopy(snapshot['entity_schemas'][key])
            if (type(schema) is not dict or len(schema.get('required', [])) != len(set(schema.get('required', [])))):
                blocked()
            schema['required'] = sorted(schema.get('required', []))
            expected['schema']['required'] = sorted(expected['schema']['required'])
            if schema != expected['schema']:
                blocked('新对象目录与完整字段约束不一致')
            expected['fields'] = sorted(expected['fields'], key=lambda field: field['key'])
            return expected
        if set(row) != {'key', 'name', 'fields'}:
            blocked()
        raw_fields = row['fields']
        if any(set(f) - {'key', 'label', 'kind', 'required', 'unit'}
               or type(f.get('required')) is not bool for f in raw_fields):
            blocked()
        obj = ObjectType.model_validate({'name': row['name'], 'fields': [
            {k: v for k, v in f.items() if k != 'key'} for f in raw_fields]})
        if key != identifier('object', obj.name) or row['name'] != obj.name:
            blocked()
        fields, properties, required = [], {}, []
        for raw, field in zip(raw_fields, obj.fields):
            fid = identifier('field', field.label)
            normalized = {'key': fid, **field.model_dump()}
            if {**raw, 'unit': raw.get('unit')} != normalized:
                blocked()
            properties[fid] = {**KINDS[field.kind], 'title': field.label}
            if field.unit is not None:
                properties[fid]['unit'] = field.unit
            if field.required:
                required.append(fid)
                if field.kind == '文本':
                    properties[fid].update(minLength=1, pattern=r'\S')
            fields.append(normalized)
        schema = copy.deepcopy(snapshot['entity_schemas'][key])
        if len(schema.get('required', [])) != len(set(schema.get('required', []))):
            blocked()
        schema['required'] = sorted(schema.get('required', []))
        expected = {'type': 'object', 'title': obj.name, 'properties': properties,
                    'required': sorted(required), 'additionalProperties': False}
        if schema != expected:
            blocked('对象目录与完整字段约束不一致，不能只按名称沿用')
        return {'key': key, 'name': obj.name, 'fields': sorted(fields, key=lambda f: f['key']),
                'schema': schema}
    except (KeyError, TypeError, ValueError, AttributeError):
        blocked()


def _snapshot_contract(snapshot, key, definition=None):
    """Rebuild the supported full schema from an already frozen task catalog."""
    from .instance_compiler import KINDS
    fields = snapshot['fields']
    properties = {}
    required = []
    for field in fields:
        prop = {**KINDS[field['kind']], 'title': field['label']}
        if field.get('unit') is not None:
            prop['unit'] = field['unit']
        if field['required']:
            required.append(field['key'])
            if field['kind'] == '文本':
                prop.update(minLength=1, pattern=r'\S')
        properties[field['key']] = prop
    row = {'key': key, 'name': snapshot['type_name'], 'fields': fields}
    if definition is not None and definition['schema'] == 'instance-entity-definition-v170':
        row.update(copy.deepcopy(definition['object_type_contract']))
    return object_contract({'object_types': [row],
        'entity_schemas': {key: {'type': 'object', 'title': snapshot['type_name'],
            'properties': properties, 'required': required, 'additionalProperties': False}}}, key)


def verify_task_binding_snapshot(task, release=None):
    """Historical structure only. Reading a record does not publish its sources."""
    data = task.data
    if not isinstance(data, dict):
        blocked('已保存对象来源证明结构无法核对')
    if BINDINGS not in data:
        if BINDINGS_HASH in data:
            blocked('对象来源证明缺失，不能补猜或沿用旧授权')
        return None
    try:
        record = data[BINDINGS]
        if (not isinstance(record, dict) or set(record) != {'schema', 'instance_id', 'target_release_id', 'objects'}
                or record['schema'] != SCHEMA or digest(record) != data.get(BINDINGS_HASH)
                or not _text(record['instance_id']) or not _text(record['target_release_id'])
                or record['instance_id'] != data['instance_id']
                or record['target_release_id'] != data['instance_release_id']
                or release is not None and (release.id != record['target_release_id']
                    or release.instance_id != record['instance_id'] or release.package_id != task.package_id)):
            blocked('已保存对象来源证明与任务版本不一致')
        ids = data['entity_ids']
        snapshots = data['object_snapshots']
        if (not isinstance(ids, list) or not ids or any(not _text(x) for x in ids)
                or len(ids) != len(set(ids)) or not isinstance(snapshots, list)
                or not isinstance(record['objects'], list)
                or [x['entity_id'] for x in record['objects']] != ids
                or [x['id'] for x in snapshots] != ids):
            blocked('已保存对象范围不一致')
        for binding, snapshot in zip(record['objects'], snapshots):
            definition = binding['definition']
            _definition_structure(definition)
            if (not isinstance(binding, dict) or set(binding) != {'entity_id', 'entity_version', 'attributes_hash', 'definition'}
                    or not isinstance(snapshot, dict)
                    or set(snapshot) != {'id', 'version', 'type_name', 'attributes', 'fields'}
                    or type(binding['entity_version']) is not int or binding['entity_version'] < 1
                    or type(snapshot['version']) is not int or snapshot['version'] < 1
                    or binding['entity_version'] != snapshot['version']
                    or not _sha256(binding['attributes_hash']) or not isinstance(snapshot['attributes'], dict)
                    or not isinstance(snapshot['fields'], list)
                    or digest(snapshot['attributes']) != binding['attributes_hash']
                    or definition['entity_id'] != snapshot['id']
                    or definition['instance_id'] != record['instance_id']
                    or definition['target_release_id'] != record['target_release_id']
                    or definition['target_package_id'] != task.package_id
                    or release is not None and definition['target_configuration_hash'] != release.configuration_hash):
                blocked('原对象快照或字段定义已变化，不能替换已保存任务依据')
            contract = _snapshot_contract(snapshot, definition['entity_type'], definition)
            if digest(contract) != definition['contract_hash']:
                blocked('原对象快照或字段定义已变化，不能替换已保存任务依据')
            if list(Draft202012Validator(contract['schema'], format_checker=FormatChecker()).iter_errors(snapshot['attributes'])):
                blocked('已保存对象内容不符合原完整定义，不能替换任务依据')
        return copy.deepcopy(record)
    except (KeyError, TypeError, ValueError, AttributeError):
        blocked('已保存对象来源证明结构无法核对')


class EntityReleaseContext:
    """Request-local cache; callers still enforce active business/IAM/actions."""
    def __init__(self, db, p, instance, release, *, lock_sources=True):
        self.db, self.p, self.instance, self.target = db, p, instance, release
        self.lock_sources = lock_sources
        self.releases, self.packages, self.identities = {}, {}, {}
        if instance.workspace_id != p.workspace_id or release.instance_id != instance.id:
            blocked('对象不属于此业务实例')
        self._release(release)

    def _release(self, release, *, live=True):
        if (release.workspace_id != self.p.workspace_id or release.instance_id != self.instance.id
                or release.status != 'prepared' or not release.package_id):
            blocked()
        # Writers lock/refresh the real source before dispatch checks. Explicit
        # read-only projections validate the same source within their snapshot.
        cap = scoped(self.db, Capability, release.package_id, self.p, self.lock_sources)
        if live:
            cap = published(self.db, self.p, release.package_id, 'domain_package')
        if (cap.workspace_id != self.p.workspace_id or cap.namespace != self.instance.namespace
                or cap.kind != 'domain_package' or cap.content_hash != digest(cap.data)
                or cap.validated_hash != cap.content_hash):
            blocked()
        snapshot = release.data.get('snapshot')
        if (not isinstance(snapshot, dict) or digest(snapshot) != release.configuration_hash
                or cap.data.get('specification', {}).get('configuration_hash') != release.configuration_hash):
            blocked('来源版本快照或配置摘要无法核实')
        self.releases[release.id] = release
        self.packages[release.id] = cap
        return cap

    def _get_release(self, identity):
        if identity not in self.releases:
            row = scoped(self.db, InstanceRelease, identity, self.p)
            self._release(row, live=False)
        return self.releases[identity]

    def _definition(self, release, key):
        cap = self.packages[release.id]
        value = object_contract(release.data['snapshot'], key)
        spec = cap.data['specification']
        mirrored = object_contract({'object_types': spec.get('object_catalog'),
                                    'entity_schemas': spec.get('entity_schemas')}, key)
        if value != mirrored:
            blocked('来源包与对象版本目录不一致')
        return value

    def _parent(self, release):
        data = release.data
        if 'object_lineage_parent' not in data and 'object_lineage_parent_hash' in data:
            blocked('对象类型来源链证明缺失，不能退回旧版本猜测')
        if 'object_lineage_parent' in data:
            parent = data['object_lineage_parent']
            if (not isinstance(parent, dict) or set(parent) != {'schema', 'instance_id', 'parent_release_id'}
                    or parent['schema'] != 'instance-object-parent-v1'
                    or parent['instance_id'] != self.instance.id
                    or digest(parent) != data.get('object_lineage_parent_hash')):
                blocked('对象类型来源链无法核实')
            return parent['parent_release_id']
        # Legacy records are not rewritten. Only a real activation audit before
        # generation may establish their parent; configuration hashes cannot.
        events = list(self.db.scalars(select(Audit).where(Audit.workspace_id == self.p.workspace_id,
            Audit.object_id == self.instance.id, Audit.action == 'instance.activated',
            Audit.created_at < release.created_at).order_by(Audit.created_at.desc()).limit(2)))
        if not events:
            return None
        if len(events) > 1 and events[0].created_at == events[1].created_at:
            blocked('旧版本的生成来源存在时间歧义，不能猜测类型身份')
        return events[0].data.get('release') or blocked()

    def _identity(self, release, key, seen=()):
        cache_key = (release.id, key)
        if cache_key in self.identities:
            return self.identities[cache_key]
        if release.id in seen or len(seen) >= 128:
            blocked('对象版本来源链存在循环或超过核对上限')
        self._definition(release, key)
        parent_id = self._parent(release)
        anchor = release.id
        if parent_id is not None:
            if not isinstance(parent_id, str) or not parent_id:
                blocked()
            parent = self._get_release(parent_id)
            if parent.release_number >= release.release_number or parent.created_at > release.created_at:
                blocked('对象版本来源顺序不一致')
            kinds = parent.data['snapshot'].get('object_types', [])
            if any(row.get('key') == key for row in kinds):
                identity = self._identity(parent, key, (*seen, release.id))
                self.identities[cache_key] = identity
                return identity
        identity = digest({'schema': 'instance-object-identity-v1', 'instance_id': self.instance.id,
                           'origin_release_id': anchor, 'type_key': key})
        self.identities[cache_key] = identity
        return identity

    def _source(self, entity):
        matches = list(self.db.scalars(select(InstanceRelease).where(
            InstanceRelease.workspace_id == self.p.workspace_id,
            InstanceRelease.package_id == entity.schema_capability_id).limit(2)))
        if len(matches) != 1 or matches[0].instance_id != self.instance.id:
            blocked('对象来源不属于同一实例，不能根据同名类型沿用')
        return matches[0]

    def require(self, entity):
        if entity.workspace_id != self.p.workspace_id or entity.namespace != self.instance.namespace:
            blocked('对象不属于此业务实例')
        source = self._source(entity)
        self._release(source)
        before = self._definition(source, entity.entity_type)
        after = self._definition(self.target, entity.entity_type)
        if before != after:
            blocked('对象字段、单位或完整约束已变化，需明确迁移后才能使用')
        source_identity = self._identity(source, entity.entity_type)
        if source_identity != self._identity(self.target, entity.entity_type):
            blocked('对象类型曾移除或属于不同来源，不能按同名类型沿用')
        if list(Draft202012Validator(after['schema'], format_checker=FormatChecker()).iter_errors(entity.data)):
            blocked('已存对象内容不符合原完整定义，请先核对原记录')
        definition = {'schema': 'instance-entity-definition-v1', 'entity_id': entity.id,
            'entity_type': entity.entity_type, 'instance_id': self.instance.id,
            'namespace': self.instance.namespace, 'source_release_id': source.id,
            'source_configuration_hash': source.configuration_hash,
            'source_package_id': source.package_id, 'source_package_hash': self.packages[source.id].content_hash,
            'target_release_id': self.target.id, 'target_configuration_hash': self.target.configuration_hash,
            'target_package_id': self.target.package_id, 'target_package_hash': self.packages[self.target.id].content_hash,
            'type_identity': source_identity, 'contract_hash': digest(after)}
        if 'business_identity' in after:
            definition.update(schema='instance-entity-definition-v170', object_type_contract={
                'contract_schema': 'report-object-type-v170', 'business_identity': copy.deepcopy(after['business_identity']),
                'platform_identity_confirmation': copy.deepcopy(after['platform_identity_confirmation'])})
        return definition

    def view(self, entity):
        try:
            proof = self.require(entity)
            source = self.releases[proof['source_release_id']]
            contract = self._definition(source, entity.entity_type)
            label = next((entity.data.get(f['key']) for f in contract['fields']
                          if f['kind'] == '文本' and f['required'] and entity.data.get(f['key'])), entity.external_id)
            current = source.id == self.target.id
            return {'usable': True, 'status': 'current' if current else 'compatible',
                'reason': '当前版本对象' if current else '对象完整定义未变，可沿用原记录',
                'source_release_number': source.release_number,
                'source_label': str(label or '未命名对象')[:200], 'source_type': contract['name']}
        except HTTPException as error:
            reason = error.detail if error.status_code == 409 else '对象来源不可读取或无法核对，原记录保留'
            return {'usable': False, 'status': 'incompatible', 'reason': reason,
                    'source_release_number': None, 'source_label': None, 'source_type': None}


def require_entity(db, p, instance, release, entity, *, lock_sources=True):
    return EntityReleaseContext(db, p, instance, release, lock_sources=lock_sources).require(entity)


def freeze_task_objects(db, p, instance, release, entities):
    context = EntityReleaseContext(db, p, instance, release)
    return {'schema': SCHEMA, 'instance_id': instance.id, 'target_release_id': release.id,
        'objects': [{'entity_id': row.id, 'entity_version': row.version,
                     'attributes_hash': digest(row.data), 'definition': context.require(row)} for row in entities]}


def verify_task_objects(db, p, task, release=None, *, lock_sources=True):
    record = verify_task_binding_snapshot(task, release)
    instance = scoped(db, DomainInstance, task.data['instance_id'], p)
    release = release or scoped(db, InstanceRelease, task.data['instance_release_id'], p)
    if (release.workspace_id != p.workspace_id or release.id != task.data['instance_release_id']
            or release.instance_id != instance.id or release.package_id != task.package_id):
        blocked('任务与原实例版本不一致')
    if record is None:
        # Publication/execution permissions remain with existing callers. Do
        # not retrofit new schema or lineage requirements onto saved tasks.
        for identity in task.data['entity_ids']:
            entity = scoped(db, Entity, identity, p)
            if entity.namespace != instance.namespace or entity.schema_capability_id != task.package_id:
                blocked('旧任务缺少跨版本对象证明，原记录保留')
        return None
    context = EntityReleaseContext(db, p, instance, release, lock_sources=lock_sources)
    for index, identity in enumerate(task.data['entity_ids']):
        entity = scoped(db, Entity, identity, p)
        if context.require(entity) != record['objects'][index]['definition']:
            blocked('原对象来源、定义或权限已变化，请核对原任务依据')
    return context
