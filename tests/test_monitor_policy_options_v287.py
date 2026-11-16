"""Pure exact-SQL I/O and in-process API dependency spies; no live IAM/E2E claim.

The original entity-release, approved-policy and asset/device declaration
validators execute unchanged. The read adapter supplies only explicit fixture
SELECTs and rejects writes, locks and unknown SQL. API authentication/session
dependencies are deliberate spies; Root must also verify the real readonly GET.
"""
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from generic_agents import monitor_policy_options_v287 as subject
from generic_agents.contracts import digest
from generic_agents.instance_compiler import KINDS, ObjectType, identifier
from generic_agents.models import (AssetDeviceBinding, Capability, DomainInstance, Entity,
    InstanceRelease, Membership, MonitorPolicy, SensorAsset, SensorDevice, User)
from generic_agents.security import Principal
from generic_agents.snapshot_authentication_v141 import snapshot_authenticated as authenticated


def ident(n):
    return '28700000-0000-4000-8000-' + format(n, '012x')


def signature(query):
    compiled = query.compile()
    sql = str(compiled)
    assert sql.lstrip().startswith('SELECT ') and 'FOR UPDATE' not in sql and 'FOR SHARE' not in sql
    return sql, json.dumps(compiled.params, sort_keys=True)


class ReadIO:
    def __init__(self):
        self.scalar_reads = {}; self.scalars_reads = {}; self.execute_reads = {}; self.reads = []

    def _read(self, query, inventory):
        key = signature(query); self.reads.append(key)
        assert key in inventory, 'Unknown SQL is never accepted by this fixture'
        value = inventory[key]
        return value() if callable(value) else value

    def scalar(self, query):
        return self._read(query, self.scalar_reads)

    def scalars(self, query):
        return self._read(query, self.scalars_reads)

    def execute(self, query):
        return NS(first=lambda: self._read(query, self.execute_reads))

    def add(self, *args):
        pytest.fail('GET attempted a write')

    def flush(self, *args):
        pytest.fail('GET attempted a flush')

    def commit(self, *args):
        pytest.fail('GET attempted a commit')

    def delete(self, *args):
        pytest.fail('GET attempted a delete')


@pytest.fixture
def world(monkeypatch):
    at = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(subject, 'now', lambda: at)
    from generic_agents import perception
    monkeypatch.setattr(perception, 'now', lambda: at)
    db = ReadIO(); p = Principal(ident(1), ident(2), 'operator', 'explicit-pure-read-boundary')
    original = ObjectType.model_validate({'name': '观测点', 'fields': [
        {'label': '名称', 'kind': '文本', 'required': True, 'unit': None},
        {'label': '温度', 'kind': '数值', 'required': False, 'unit': None}]})
    kind = identifier('object', original.name)
    catalog = {'key': kind, 'name': original.name, 'fields': [
        {'key': identifier('field', item.label), **item.model_dump()} for item in original.fields]}
    field = next(item['key'] for item in catalog['fields'] if item['label'] == '温度')
    name_field = next(item['key'] for item in catalog['fields'] if item['label'] == '名称')
    schema = {'type': 'object', 'title': original.name, 'properties': {
        name_field: {**KINDS['文本'], 'title': '名称', 'minLength': 1, 'pattern': r'\S'},
        field: {**KINDS['数值'], 'title': '温度'}}, 'required': [name_field], 'additionalProperties': False}
    snapshot = {'object_types': [catalog], 'entity_schemas': {kind: schema}}
    instance = NS(id=ident(3), workspace_id=p.workspace_id, namespace='instance_policy_choices',
        status='partially_active', active_release_id=ident(4))
    parent = {'schema': 'instance-object-parent-v1', 'instance_id': instance.id, 'parent_release_id': None}
    release = NS(id=ident(4), workspace_id=p.workspace_id, instance_id=instance.id, package_id=ident(5),
        status='prepared', release_number=1, created_at=at, configuration_hash=digest(snapshot),
        data={'snapshot': snapshot, 'object_lineage_parent': parent, 'object_lineage_parent_hash': digest(parent)})
    package_data = {'kind': 'domain_package', 'specification': {'configuration_hash': release.configuration_hash,
        'object_catalog': copy.deepcopy(snapshot['object_types']), 'entity_schemas': copy.deepcopy(snapshot['entity_schemas'])}}
    package = NS(id=release.package_id, workspace_id=p.workspace_id, namespace=instance.namespace,
        kind='domain_package', status='published', data=package_data,
        content_hash=digest(package_data), validated_hash=digest(package_data))
    entity = NS(id=ident(6), workspace_id=p.workspace_id, namespace=instance.namespace,
        schema_capability_id=package.id, entity_type=kind, data={name_field: '公开测试观测点'},
        external_id='synthetic-point', version=1)
    source_data = {'kind': 'source', 'execution': {}, 'specification': {'observation_capability': {
        'fields': [field], 'modalities': ['numeric'], 'actions': ['observe'], 'receipt_contract': 'request_asset_terminal_v1'}}}
    source = NS(id=ident(7), workspace_id=p.workspace_id, kind='source', status='published', data=source_data,
        content_hash=digest(source_data), validated_hash=digest(source_data))
    approver = ident(8)
    asset = NS(id=ident(9), workspace_id=p.workspace_id, source_id=source.id, data={
        'fields': [field], 'modalities': ['numeric'], 'actions': ['observe'], 'coverage_entity_ids': [entity.id]})
    device = NS(id=ident(10), workspace_id=p.workspace_id, status='enabled', approved_by=approver,
        identity_scheme='inventory_tag', identity_issuer='synthetic fixture', identity_reference='point-one')
    binding = NS(id=ident(11), workspace_id=p.workspace_id, asset_id=asset.id, device_id=device.id, approved_by=approver)
    data = {'title': '已批准温度补充观测', 'entity_ids': [entity.id], 'fields': [field], 'source_ids': [source.id],
        'sensor_asset_ids': [asset.id], 'selection_requirement': {'allowed_modalities': ['numeric']},
        'timezone': 'UTC', 'valid_until': (at + timedelta(days=1)).isoformat(),
        'device_scope': {asset.id: {'binding_id': binding.id, 'device_id': device.id,
            'identity_hash': digest({'scheme': device.identity_scheme, 'issuer': device.identity_issuer,
                'reference': device.identity_reference})}}}
    policy = NS(id=ident(12), workspace_id=p.workspace_id, status='active', approved_by=approver,
        created_at=at, data={**data, 'approved_hash': digest(data), 'approval_reason': 'Synthetic approved record'})
    member = NS(role=p.role)
    w = NS(db=db, p=p, at=at, instance=instance, release=release, package=package, entity=entity,
        source=source, asset=asset, device=device, binding=binding, policy=policy, field=field,
        approver=approver, member=member, inventory=[policy], approved=True)
    for model, row in [(DomainInstance, instance), (InstanceRelease, release), (Capability, package),
            (Entity, entity), (Capability, source), (SensorAsset, asset), (SensorDevice, device)]:
        query = select(model).where(model.id == row.id, model.workspace_id == p.workspace_id)
        db.scalar_reads[signature(query)] = row
    db.scalars_reads[signature(select(InstanceRelease).where(InstanceRelease.workspace_id == p.workspace_id,
        InstanceRelease.package_id == package.id).limit(2))] = [release]
    db.scalar_reads[signature(select(AssetDeviceBinding).where(AssetDeviceBinding.workspace_id == p.workspace_id,
        AssetDeviceBinding.asset_id == asset.id))] = binding
    admin = select(User.id).join(Membership, Membership.user_id == User.id).where(User.id == approver,
        User.active.is_(True), Membership.workspace_id == p.workspace_id, Membership.role == 'admin')
    db.scalar_reads[signature(admin)] = lambda: approver if w.approved else None
    reader = select(User.id, Membership.role).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id, Membership.role == p.role)
    db.execute_reads[signature(reader)] = member
    inventory = select(MonitorPolicy).where(MonitorPolicy.workspace_id == p.workspace_id,
        MonitorPolicy.status == 'active', MonitorPolicy.data['entity_ids'].contains([entity.id])).order_by(
            MonitorPolicy.created_at, MonitorPolicy.id).limit(subject.MAX_POLICIES + 1)
    db.scalars_reads[signature(inventory)] = lambda: w.inventory
    return w


def read(w):
    return subject.options(w.db, w.p, w.instance.id, w.release.id, [w.entity.id])


def reseal(w):
    w.policy.data['approved_hash'] = digest({key: value for key, value in w.policy.data.items()
        if key not in {'approved_hash', 'approval_reason'}})


def test_valid_original_approval_and_release_are_read_without_writes_or_fake_units(world):
    w = world; before = copy.deepcopy(w.policy.data)
    result = read(w)
    assert set(result) == {'schema', 'instance_id', 'release_id', 'entity_ids', 'options', 'can_dispatch'}
    assert result['can_dispatch'] is False and result['entity_ids'] == [w.entity.id]
    assert result['options'] == [{'policy_id': w.policy.id, 'approved_hash': before['approved_hash'],
        'title': before['title'], 'fields': [{'key': w.field, 'label': '温度'}],
        'valid_until': before['valid_until'], 'scope_label': '适用本次已选的 1 个对象；仍须核对实际执行条件'}]
    assert w.policy.data == before and w.db.reads
    assert 'unit' not in result['options'][0]['fields'][0]


@pytest.mark.parametrize('damage', ['status', 'workspace', 'hash', 'expired', 'approver', 'coverage',
    'field', 'duplicate_field', 'no_selection', 'device_scope', 'source_changed'])
def test_unusable_original_policies_are_not_repaired_or_offered(world, damage):
    w = world
    if damage == 'status': w.policy.status = 'review'
    elif damage == 'workspace': w.policy.workspace_id = ident(99)
    elif damage == 'hash': w.policy.data['approved_hash'] = '0' * 64
    elif damage == 'expired': w.policy.data['valid_until'] = (w.at - timedelta(seconds=1)).isoformat(); reseal(w)
    elif damage == 'approver': w.approved = False
    elif damage == 'coverage': w.policy.data['entity_ids'] = [ident(99)]; reseal(w)
    elif damage == 'field': w.policy.data['fields'] = ['invented_field']; reseal(w)
    elif damage == 'duplicate_field': w.policy.data['fields'] *= 2; reseal(w)
    elif damage == 'no_selection': w.policy.data['selection_requirement'] = None; reseal(w)
    elif damage == 'device_scope': w.policy.data['device_scope'] = {}; reseal(w)
    else: w.source.data['specification']['observation_capability']['fields'] = []
    before = copy.deepcopy(w.policy.data)
    assert read(w)['options'] == [] and w.policy.data == before


@pytest.mark.parametrize('damage', [None, 'draft', 'bad_hash'])
def test_every_reader_source_pin_must_remain_published_even_without_a_selected_asset(world, damage):
    w = world
    data = copy.deepcopy(w.source.data)
    extra = NS(id=ident(20), workspace_id=w.p.workspace_id, kind='source', status='published',
        data=data, content_hash=digest(data), validated_hash=digest(data))
    query = select(Capability).where(Capability.id == extra.id, Capability.workspace_id == w.p.workspace_id)
    w.db.scalar_reads[signature(query)] = extra
    w.policy.data['source_ids'].append(extra.id)
    reseal(w)
    if damage == 'draft':
        extra.status = 'draft'
    elif damage == 'bad_hash':
        extra.content_hash = '0' * 64
    before = copy.deepcopy(w.policy.data)
    result = read(w)
    assert len(result['options']) == (1 if damage is None else 0)
    assert w.policy.data == before and w.asset.source_id != extra.id
    assert signature(query) in w.db.reads


@pytest.mark.parametrize('damage', ['inactive', 'release', 'package_namespace', 'entity_namespace', 'entity_workspace',
    'entity_type', 'configuration', 'member_role'])
def test_scope_damage_blocks_the_entire_read_instead_of_a_cross_scope_fallback(world, damage):
    w = world
    if damage == 'inactive': w.instance.status = 'paused'
    elif damage == 'release': w.instance.active_release_id = ident(99)
    elif damage == 'package_namespace': w.package.namespace = 'another_instance'
    elif damage == 'entity_namespace': w.entity.namespace = 'another_instance'
    elif damage == 'entity_workspace': w.entity.workspace_id = ident(99)
    elif damage == 'entity_type': w.entity.entity_type = 'invented_type'
    elif damage == 'configuration': w.release.configuration_hash = '0' * 64
    else: w.member.role = 'viewer'
    with pytest.raises(HTTPException): read(w)


def test_selection_is_exact_and_inventory_cannot_be_truncated_as_success(world):
    w = world
    for value in ['', 'bad', w.entity.id + ',', w.entity.id + ',' + w.entity.id, ident(15).upper()]:
        with pytest.raises(HTTPException): subject.selected_ids(value)
    assert subject.selected_ids(w.entity.id) == [w.entity.id]
    w.inventory = [w.policy] * (subject.MAX_POLICIES + 1)
    with pytest.raises(HTTPException, match='') as failure: read(w)
    assert failure.value.status_code == 409


def test_another_workspace_principal_has_no_fallback_to_the_original_instance(world):
    w = world; other = Principal(w.p.user_id, ident(99), w.p.role, w.p.session_id)
    query = select(User.id, Membership.role).join(Membership, Membership.user_id == User.id).where(
        User.id == other.user_id, User.active.is_(True), Membership.workspace_id == other.workspace_id,
        Membership.role == other.role)
    w.db.execute_reads[signature(query)] = None
    with pytest.raises(HTTPException) as failure:
        subject.options(w.db, other, w.instance.id, w.release.id, [w.entity.id])
    assert failure.value.status_code == 403 and len(w.db.reads) == 1


def api(w):
    app = FastAPI(); app.include_router(subject.router, prefix='/api/workspace')
    app.dependency_overrides[authenticated] = lambda: w.p
    app.dependency_overrides[subject.read_session] = lambda: w.db
    return app


def test_api_exact_url_dto_and_readonly_dependency(world):
    w = world; app = api(w)
    route = next(route for route in subject.router.routes if route.path.endswith('monitor-policy-options-v287'))
    assert any(dependency.call is subject.read_session for dependency in route.dependant.dependencies)
    with TestClient(app) as client:
        result = client.get('/api/workspace/instances/' + w.instance.id + '/monitor-policy-options-v287',
            params={'release_id': w.release.id, 'entity_ids': w.entity.id})
        assert result.status_code == 200 and result.json() == read(w)
        invalid = client.get('/api/workspace/instances/' + w.instance.id + '/monitor-policy-options-v287',
            params={'release_id': w.release.id, 'entity_ids': ''})
        assert invalid.status_code == 422


def test_api_authentication_failure_never_uses_the_projection(world):
    w = world; app = api(w)
    def denied():
        raise HTTPException(401, 'Authentication required')
    app.dependency_overrides[authenticated] = denied
    with TestClient(app) as client:
        result = client.get('/api/workspace/instances/' + w.instance.id + '/monitor-policy-options-v287',
            params={'release_id': w.release.id, 'entity_ids': w.entity.id})
        assert result.status_code == 401 and not w.db.reads
