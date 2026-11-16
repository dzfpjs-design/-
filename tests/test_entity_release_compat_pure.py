"""Adversarial in-memory definition proofs; no SQL engine or live product claim."""
import copy
import operator
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import entity_release_compat as compat
from generic_agents.contracts import digest
from generic_agents.instance_compiler import KINDS, identifier
from generic_agents.models import Audit, Capability, DomainInstance, Entity, InstanceRelease


@pytest.fixture(autouse=True)
def forbid_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('definition proof pure tests forbid SQL, services, network and models')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    monkeypatch.setattr(Session, 'execute', forbidden)
    monkeypatch.setattr(Session, 'commit', forbidden)


def declarations():
    fields = [{'key': identifier('field', label), 'label': label, 'kind': kind,
               'required': required, 'unit': unit} for label, kind, required, unit in [
                   ('标题', '文本', True, None), ('数量', '数值', True, '株'), ('备注', '文本', False, None)]]
    return {'key': identifier('object', '样地'), 'name': '样地', 'fields': fields}


def schema_for(catalog):
    props, required = {}, []
    for field in catalog['fields']:
        prop = {**KINDS[field['kind']], 'title': field['label']}
        if field['unit'] is not None:
            prop['unit'] = field['unit']
        if field['required']:
            required.append(field['key'])
            if field['kind'] == '文本':
                prop.update(minLength=1, pattern=r'\S')
        props[field['key']] = prop
    return {'type': 'object', 'title': catalog['name'], 'properties': props,
            'required': required, 'additionalProperties': False}


class FakeDB:
    """Evaluate only the bounded release/audit reads used by this module."""
    def __init__(self):
        self.rows = {model: {} for model in (Audit, Capability, DomainInstance, Entity, InstanceRelease)}
        self.reads = []

    def scalars(self, query):
        model = query.column_descriptions[0]['entity']
        assert model in {Audit, InstanceRelease}, 'pure adapter deliberately rejects unexpected queries'
        self.reads.append(model.__name__)
        rows = list(self.rows[model].values())
        for condition in query._where_criteria:
            assert condition.operator in {operator.eq, operator.lt}
            field = condition.left.key;value = condition.right.value
            rows = [row for row in rows if condition.operator(getattr(row, field), value)]
        if tuple(query._order_by_clauses):
            rows.sort(key=lambda row: row.created_at, reverse=True)
        return rows[:query._limit_clause.value] if query._limit_clause is not None else rows

    def scoped(self, db, model, identity, principal, lock=False):
        assert db is self
        self.reads.append((model.__name__, identity, lock))
        row = self.rows[model].get(identity)
        if row is None or row.workspace_id != principal.workspace_id:
            raise HTTPException(404, '记录不存在或不在当前工作区')
        return row

    def published(self, db, principal, identity, kind=None):
        row = self.scoped(db, Capability, identity, principal)
        if row.status != 'published' or kind is not None and row.kind != kind:
            raise HTTPException(409, '能力尚未发布或类型不匹配')
        if row.content_hash != digest(row.data) or row.validated_hash != row.content_hash:
            raise HTTPException(409, '已发布能力内容发生变化')
        return row


class World:
    def __init__(self, monkeypatch):
        self.db = FakeDB();self.p = NS(user_id='actor', workspace_id='workspace', role='operator')
        self.instance = NS(id='instance-a', namespace='instance_a', workspace_id=self.p.workspace_id,
                           active_release_id='release-2', status='partially_active')
        self.db.rows[DomainInstance][self.instance.id] = self.instance
        self.r1 = self.release(1)
        self.r2 = self.release(2, parent=self.r1.id)
        catalog = self.r1.data['snapshot']['object_types'][0]
        self.entity = NS(id='entity-original', workspace_id=self.p.workspace_id,
                         namespace=self.instance.namespace, schema_capability_id=self.r1.package_id,
                         entity_type=catalog['key'], external_id='original-reference', version=7,
                         data={identifier('field', '标题'): '合成样地', identifier('field', '数量'): 20})
        self.db.rows[Entity][self.entity.id] = self.entity
        monkeypatch.setattr(compat, 'scoped', self.db.scoped)
        monkeypatch.setattr(compat, 'published', self.db.published)

    def release(self, number, *, parent=None, catalog=None):
        value = copy.deepcopy(declarations() if catalog is None else catalog)
        snapshot = {'object_types': [value] if value else [],
                    'entity_schemas': {value['key']: schema_for(value)} if value else {},
                    'roles': {'核查人': {'user_id': 'actor-' + str(number)}}}
        pointer = {'schema': 'instance-object-parent-v1', 'instance_id': self.instance.id,
                   'parent_release_id': parent}
        row = NS(id='release-' + str(number), workspace_id=self.p.workspace_id,
                 instance_id=self.instance.id, package_id='package-' + str(number), release_number=number,
                 status='prepared', created_at=datetime(2026, 10, 16, tzinfo=timezone.utc) + timedelta(minutes=number),
                 configuration_hash=digest(snapshot), data={'snapshot': snapshot,
                     'object_lineage_parent': pointer, 'object_lineage_parent_hash': digest(pointer)})
        self.db.rows[InstanceRelease][row.id] = row;self.reseal(row)
        return row

    def reseal(self, release):
        snapshot = release.data['snapshot'];release.configuration_hash = digest(snapshot)
        package = NS(id=release.package_id, workspace_id=self.p.workspace_id, namespace=self.instance.namespace,
                     kind='domain_package', status='published', data={'specification': {
                         'configuration_hash': release.configuration_hash,
                         'object_catalog': copy.deepcopy(snapshot['object_types']),
                         'entity_schemas': copy.deepcopy(snapshot['entity_schemas'])}})
        package.content_hash = digest(package.data);package.validated_hash = package.content_hash
        self.db.rows[Capability][package.id] = package
        return package

    def task(self, *, release=None):
        release = release or self.r2
        catalog = release.data['snapshot']['object_types'][0]
        entity = self.entity
        record = compat.freeze_task_objects(self.db, self.p, self.instance, release, [entity])
        return NS(id='new-task', workspace_id=self.p.workspace_id, package_id=release.package_id, data={
            'instance_id': self.instance.id, 'instance_release_id': release.id,
            'entity_ids': [entity.id], 'object_snapshots': [{
                'id': entity.id, 'version': entity.version, 'type_name': catalog['name'],
                'attributes': copy.deepcopy(entity.data), 'fields': copy.deepcopy(catalog['fields'])}],
            compat.BINDINGS: record, compat.BINDINGS_HASH: digest(record)})


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def reseal_bindings(task):
    task.data[compat.BINDINGS_HASH] = digest(task.data[compat.BINDINGS])


def test_roles_only_release_reuses_exact_original_entity_and_provenance(world):
    before = copy.deepcopy(vars(world.entity));task = world.task()
    record = compat.verify_task_binding_snapshot(task, world.r2)
    context = compat.verify_task_objects(world.db, world.p, task, world.r2)
    proof = record['objects'][0]['definition']
    assert context.instance is world.instance
    assert proof['source_release_id'] == world.r1.id and proof['target_release_id'] == world.r2.id
    assert proof['source_package_id'] == world.entity.schema_capability_id
    assert record['objects'][0]['entity_id'] == world.entity.id and record['objects'][0]['entity_version'] == 7
    assert proof['type_identity'] and vars(world.entity) == before


@pytest.mark.parametrize('other_instance', [False, True])
def test_current_package_cannot_bypass_unique_source_release_proof(world, other_instance):
    world.entity.schema_capability_id = world.r2.package_id
    duplicate = copy.deepcopy(world.r2);duplicate.id = 'ambiguous-release'
    if other_instance:
        duplicate.instance_id = 'unrelated-instance'
    world.db.rows[InstanceRelease][duplicate.id] = duplicate
    with pytest.raises(HTTPException):
        compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)


def test_orphan_parent_hash_is_rejected_instead_of_falling_back_to_legacy(world):
    del world.r1.data['object_lineage_parent']
    with pytest.raises(HTTPException):
        compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)


@pytest.mark.parametrize('change', ['namespace', 'source-instance', 'source-workspace'])
def test_same_type_name_from_other_instance_or_workspace_never_grants_compatibility(world, change):
    if change == 'namespace':
        world.entity.namespace = 'other_namespace'
    elif change == 'source-instance':
        world.r1.instance_id = 'other-instance'
    else:
        world.r1.workspace_id = 'other-workspace'
    with pytest.raises(HTTPException):
        compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)


@pytest.mark.parametrize('change', ['unit', 'required', 'kind', 'unselected-field', 'extra-field'])
def test_full_definition_changes_block_even_if_current_values_remain_valid(world, change):
    catalog = world.r2.data['snapshot']['object_types'][0]
    field = next(f for f in catalog['fields'] if f['label'] == ('备注' if change == 'unselected-field' else '数量'))
    if change == 'unit':field['unit'] = '公顷'
    elif change == 'required':field['required'] = False
    elif change in {'kind', 'unselected-field'}:field.update(kind='整数' if change == 'kind' else '日期', unit=None)
    else:catalog['fields'].append({'key': identifier('field', '新增'), 'label': '新增', 'kind': '文本', 'required': False, 'unit': None})
    world.r2.data['snapshot']['entity_schemas'][catalog['key']] = schema_for(catalog);world.reseal(world.r2)
    with pytest.raises(HTTPException):
        compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)


@pytest.mark.parametrize('change', ['extra-constraint', 'additional-properties', 'unselected-property'])
def test_unrepresented_schema_constraints_are_not_lost_by_contract_projection(world, change):
    schema = world.r2.data['snapshot']['entity_schemas'][world.entity.entity_type]
    if change == 'extra-constraint':schema['properties'][identifier('field', '数量')]['minimum'] = 0
    elif change == 'additional-properties':schema['additionalProperties'] = True
    else:schema['properties'][identifier('field', '备注')]['maxLength'] = 10
    world.reseal(world.r2)
    with pytest.raises(HTTPException):
        compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)


def test_field_and_required_array_order_are_canonical_without_rewriting_snapshots(world):
    snapshot = world.r2.data['snapshot'];catalog = snapshot['object_types'][0]
    catalog['fields'].reverse();snapshot['entity_schemas'][catalog['key']]['required'].reverse();world.reseal(world.r2)
    before = copy.deepcopy(snapshot);task = world.task()
    assert compat.verify_task_binding_snapshot(task, world.r2)
    assert compat.verify_task_objects(world.db, world.p, task, world.r2)
    assert snapshot == before and task.data['object_snapshots'][0]['fields'] == catalog['fields']


def test_deleted_then_readded_same_named_type_has_new_identity(world):
    removed = world.release(3, parent=world.r2.id, catalog={})
    readded = world.release(4, parent=removed.id)
    with pytest.raises(HTTPException, match='不同来源|曾移除'):
        compat.require_entity(world.db, world.p, world.instance, readded, world.entity)


def test_explicit_rollback_branch_uses_its_saved_parent_instead_of_latest_release(world):
    world.release(3, parent=world.r2.id, catalog={})
    rollback_branch = world.release(4, parent=world.r1.id)
    proof = compat.require_entity(world.db, world.p, world.instance, rollback_branch, world.entity)
    assert proof['source_release_id'] == world.r1.id and proof['target_release_id'] == rollback_branch.id


def test_legacy_release_lineage_needs_real_earlier_activation_audit(world):
    for release in (world.r1, world.r2):
        del release.data['object_lineage_parent'];del release.data['object_lineage_parent_hash']
    with pytest.raises(HTTPException):
        compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)
    audit = NS(id='activation-audit', workspace_id=world.p.workspace_id, object_id=world.instance.id,
               action='instance.activated', created_at=world.r1.created_at + timedelta(seconds=1), data={'release': world.r1.id})
    world.db.rows[Audit][audit.id] = audit
    assert compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)


def test_equal_timestamp_legacy_activation_audits_are_ambiguous(world):
    del world.r2.data['object_lineage_parent'];del world.r2.data['object_lineage_parent_hash']
    for identity in ('audit-a', 'audit-b'):
        row = NS(id=identity, workspace_id=world.p.workspace_id, object_id=world.instance.id,
                 action='instance.activated', created_at=world.r1.created_at + timedelta(seconds=1), data={'release': world.r1.id})
        world.db.rows[Audit][row.id] = row
    with pytest.raises(HTTPException, match='歧义'):
        compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)


@pytest.mark.parametrize('change', ['source-retired', 'target-retired', 'source-package-hash', 'validated-hash',
                                   'release-hash', 'package-schema-mirror', 'source-missing', 'parent-cycle'])
def test_source_or_target_integrity_and_permission_failures_block_fresh_execution(world, change):
    task = world.task();before = copy.deepcopy(task.data)
    source = world.db.rows[Capability][world.r1.package_id]
    if change == 'source-retired':source.status = 'retired'
    elif change == 'target-retired':world.db.rows[Capability][world.r2.package_id].status = 'retired'
    elif change == 'source-package-hash':source.content_hash = '0' * 64
    elif change == 'validated-hash':source.validated_hash = '0' * 64
    elif change == 'release-hash':world.r1.configuration_hash = '0' * 64
    elif change == 'package-schema-mirror':
        source.data['specification']['entity_schemas'][world.entity.entity_type]['additionalProperties'] = True
        source.content_hash = digest(source.data);source.validated_hash = source.content_hash
    elif change == 'source-missing':del world.db.rows[InstanceRelease][world.r1.id]
    else:
        pointer = world.r1.data['object_lineage_parent'];pointer['parent_release_id'] = world.r2.id
        world.r1.data['object_lineage_parent_hash'] = digest(pointer)
    with pytest.raises(HTTPException):
        compat.verify_task_objects(world.db, world.p, task, world.r2)
    assert task.data == before


def test_valid_current_entity_update_does_not_rewrite_or_block_old_task_snapshot(world):
    task = world.task();before = copy.deepcopy(task.data)
    world.entity.version += 1;world.entity.data[identifier('field', '数量')] = 28
    assert compat.verify_task_objects(world.db, world.p, task, world.r2)
    assert task.data == before and task.data['object_snapshots'][0]['attributes'][identifier('field', '数量')] == 20


def test_pure_history_does_not_query_DB_or_republish_unavailable_original_source(world, monkeypatch):
    task = world.task();before = copy.deepcopy(task.data)
    del world.db.rows[InstanceRelease][world.r1.id]
    def forbidden(*args, **kwargs):pytest.fail('history reads must remain pure and grant no execution')
    monkeypatch.setattr(compat, 'scoped', forbidden);monkeypatch.setattr(compat, 'published', forbidden)
    record = compat.verify_task_binding_snapshot(task, world.r2)
    assert record == task.data[compat.BINDINGS] and record is not task.data[compat.BINDINGS]
    assert task.data == before


@pytest.mark.parametrize('mutation', ['attrs', 'version', 'type-name', 'field-unit', 'object-id', 'order', 'snapshot-version-bool'])
def test_snapshot_tampering_is_rejected_without_reading_sources(world, mutation):
    if mutation == 'snapshot-version-bool':world.entity.version = 1
    task = world.task();snapshot = task.data['object_snapshots'][0]
    if mutation == 'attrs':snapshot['attributes'][identifier('field', '数量')] = 999
    elif mutation == 'version':snapshot['version'] += 1
    elif mutation == 'type-name':snapshot['type_name'] = '另一对象'
    elif mutation == 'field-unit':snapshot['fields'][1]['unit'] = '公顷'
    elif mutation == 'object-id':snapshot['id'] = 'another-object'
    elif mutation == 'order':task.data['entity_ids'].append('another-object')
    else:snapshot['version'] = True
    with pytest.raises(HTTPException):
        compat.verify_task_binding_snapshot(task, world.r2)


@pytest.mark.parametrize('mutation', ['missing-source-id', 'missing-source-hash', 'wrong-schema', 'unknown-key',
                                     'invalid-hash', 'nonstring-source', 'blank-namespace', 'wrong-source-shape'])
def test_definition_shape_is_strict_even_when_binding_hash_is_recomputed(world, mutation):
    task = world.task();proof = task.data[compat.BINDINGS]['objects'][0]['definition']
    if mutation == 'missing-source-id':del proof['source_release_id']
    elif mutation == 'missing-source-hash':del proof['source_package_hash']
    elif mutation == 'wrong-schema':proof['schema'] = 'forged-v1'
    elif mutation == 'unknown-key':proof['allow_external'] = True
    elif mutation == 'invalid-hash':proof['source_configuration_hash'] = 'not-a-sha256'
    elif mutation == 'nonstring-source':proof['source_release_id'] = 12
    elif mutation == 'blank-namespace':proof['namespace'] = ' '
    else:proof['source_package_id'] = ['package-1']
    reseal_bindings(task)
    with pytest.raises(HTTPException):
        compat.verify_task_binding_snapshot(task, world.r2)


@pytest.mark.parametrize('key', [
    'schema', 'entity_id', 'entity_type', 'instance_id', 'namespace', 'source_release_id',
    'source_configuration_hash', 'source_package_id', 'source_package_hash', 'target_release_id',
    'target_configuration_hash', 'target_package_id', 'target_package_hash', 'type_identity', 'contract_hash'])
@pytest.mark.parametrize('mutation', ['missing', 'wrong-shape'])
def test_every_definition_field_is_required_with_a_strict_shape(world, key, mutation):
    task = world.task();proof = task.data[compat.BINDINGS]['objects'][0]['definition']
    if mutation == 'missing':del proof[key]
    else:proof[key] = ['invalid-field-shape']
    reseal_bindings(task)
    with pytest.raises(HTTPException):
        compat.verify_task_binding_snapshot(task, world.r2)


@pytest.mark.parametrize('change', ['extra-snapshot-key', 'tuple-fields', 'wrong-attribute-type', 'target-config-hash'])
def test_self_consistent_malformed_saved_payload_does_not_become_valid_authority(world, change):
    task = world.task();snapshot = task.data['object_snapshots'][0]
    binding = task.data[compat.BINDINGS]['objects'][0]
    if change == 'extra-snapshot-key':snapshot['allow_external'] = True
    elif change == 'tuple-fields':snapshot['fields'] = tuple(snapshot['fields'])
    elif change == 'wrong-attribute-type':
        snapshot['attributes'][identifier('field', '数量')] = 'twenty'
        binding['attributes_hash'] = digest(snapshot['attributes'])
    else:binding['definition']['target_configuration_hash'] = '0' * 64
    reseal_bindings(task)
    with pytest.raises(HTTPException):
        compat.verify_task_binding_snapshot(task, world.r2)


def test_self_consistent_snapshot_with_wrong_attribute_shape_is_rejected(world):
    task = world.task();snapshot = task.data['object_snapshots'][0]
    snapshot['attributes'] = ['not-an-object']
    task.data[compat.BINDINGS]['objects'][0]['attributes_hash'] = digest(snapshot['attributes']);reseal_bindings(task)
    with pytest.raises(HTTPException):
        compat.verify_task_binding_snapshot(task, world.r2)


def test_legacy_verifier_keeps_original_scope_checks_without_new_lineage_or_publication_gates(world, monkeypatch):
    task = world.task(release=world.r1)
    del task.data[compat.BINDINGS];del task.data[compat.BINDINGS_HASH]
    world.r1.status = 'legacy-state';world.r1.data = {'snapshot': {'legacy-record': True}}
    def forbidden(*args, **kwargs):pytest.fail('legacy identity guard must not impose new publication/schema gates')
    monkeypatch.setattr(compat, 'published', forbidden)
    assert compat.verify_task_objects(world.db, world.p, task, world.r1) is None


def test_legacy_missing_proof_never_silently_upgrades_cross_release_entities(world):
    task = world.task();del task.data[compat.BINDINGS];del task.data[compat.BINDINGS_HASH]
    with pytest.raises(HTTPException, match='旧任务缺少'):
        compat.verify_task_objects(world.db, world.p, task, world.r2)


def test_legacy_passed_release_must_match_the_original_task_id_not_just_package(world):
    task = world.task(release=world.r1);del task.data[compat.BINDINGS];del task.data[compat.BINDINGS_HASH]
    different = copy.deepcopy(world.r1);different.id = 'wrong-release-with-same-package'
    with pytest.raises(HTTPException, match='原实例版本不一致'):
        compat.verify_task_objects(world.db, world.p, task, different)


def test_orphan_task_binding_hash_cannot_fall_back_to_legacy(world):
    task = world.task();del task.data[compat.BINDINGS]
    with pytest.raises(HTTPException, match='证明缺失'):
        compat.verify_task_binding_snapshot(task, world.r2)
