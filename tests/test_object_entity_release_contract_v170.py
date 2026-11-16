"""Public, in-memory V170 definitions; not PG registration or origin authority.

The real type/schema, compatibility, task-freezing and compiler refusal paths
run unchanged. The existing bounded release/package IO adapter supplies only
invented rows; it does not replace an integrity validator or issue an origin.
Root alone executes these authored tests.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import select

from generic_agents import entity_release_compat as compat
from generic_agents import instance_compiler as compiler
from generic_agents import object_type_contract_v170 as types
from generic_agents.contracts import digest
from generic_agents.models import Capability
from generic_agents.report_configuration_models import ReportConfigurationRecord
from generic_agents.report_full_semantic_schema_models import FullSemanticSchemaAdoption
from tests import test_entity_release_compat_pure as legacy
from tests.test_object_type_contract_v170 import value as object_value


@pytest.fixture(autouse=True)
def no_external_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Public definition tests cannot contact a DB, service or model')
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


def proposal():
    value = object_value()
    value['business_identity'] = {'status': 'referenced', 'field_labels': ['数量'],
        'citations': [{'span_ref': 'w:p4/s1'}],
        'explanation': '引用字段尚未建立业务唯一性；技术记录身份独立。'}
    return value


def catalog_and_schema(value):
    # This is the actual pure definition helper used by compile_configuration,
    # not a successful compiler/ledger substitute. No origin is issued here.
    contract = types.object_contract(value)
    schema = contract.pop('schema')
    return {**contract, 'contract_schema': types.SCHEMA}, schema


def install_contract(world, release, value):
    row, schema = catalog_and_schema(value)
    snapshot = release.data['snapshot']
    snapshot['object_types'] = [row]
    snapshot['entity_schemas'] = {row['key']: schema}
    world.reseal(release)


@pytest.fixture
def world(monkeypatch):
    world = legacy.World(monkeypatch)
    for release in (world.r1, world.r2):
        install_contract(world, release, proposal())
    world.entity.entity_type = types.object_contract(proposal())['key']
    world.entity.data = {}
    return world


def test_numeric_optional_catalog_definition_and_task_keep_all_identity_and_unit_metadata(world):
    value = proposal(); before = copy.deepcopy(vars(world.entity))
    snapshot = world.r1.data['snapshot']; row = snapshot['object_types'][0]
    assert set(row) == {'key', 'name', 'fields', 'contract_schema',
        'business_identity', 'platform_identity_confirmation'}
    assert row['contract_schema'] == types.SCHEMA
    assert row['business_identity'] == value['business_identity']
    assert row['platform_identity_confirmation'] == value['platform_identity_confirmation']
    assert [{key: field[key] for key in ('label', 'kind', 'required', 'unit')}
        for field in row['fields']] == value['fields']
    assert all(not field['required'] and field['kind'] in {'整数', '数值'} for field in row['fields'])
    schema = snapshot['entity_schemas'][row['key']]
    assert schema['required'] == [] and not list(Draft202012Validator(schema).iter_errors({}))
    assert world.db.rows[Capability][world.r1.package_id].data['specification']['object_catalog'] == [row]
    definition = compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)
    assert definition['schema'] == 'instance-entity-definition-v170'
    assert definition['object_type_contract'] == {key: row[key] for key in
        ('contract_schema', 'business_identity', 'platform_identity_confirmation')}
    task = world.task(); frozen = copy.deepcopy(task.data)
    record = compat.verify_task_binding_snapshot(task, world.r2)
    assert record['objects'][0]['definition'] == definition
    assert compat.verify_task_objects(world.db, world.p, task, world.r2).instance is world.instance
    object_snapshot = task.data['object_snapshots'][0]
    assert object_snapshot['attributes'] == {} and object_snapshot['fields'] == row['fields']
    rebuilt = compat._snapshot_contract(object_snapshot, world.entity.entity_type, definition)
    assert rebuilt['business_identity'] == value['business_identity']
    assert rebuilt['platform_identity_confirmation'] == value['platform_identity_confirmation']
    assert digest(rebuilt) == definition['contract_hash']
    assert vars(world.entity) == before and task.data == frozen
    assert world.entity.external_id == 'original-reference', 'Business identity must not become a technical ID'


def test_field_order_is_canonical_but_original_catalog_and_frozen_task_are_not_rewritten(world):
    target = world.r2.data['snapshot']; target['object_types'][0]['fields'].reverse()
    world.reseal(world.r2); before = copy.deepcopy(target)
    task = world.task()
    assert compat.verify_task_binding_snapshot(task, world.r2)
    assert compat.verify_task_objects(world.db, world.p, task, world.r2)
    assert task.data['object_snapshots'][0]['fields'] == target['object_types'][0]['fields']
    assert target == before
    returned = compat.object_contract(target, world.entity.entity_type)
    returned['business_identity']['field_labels'].clear()
    returned['fields'][0]['unit'] = '只改返回值'
    assert target == before


def test_historical_task_rebuild_does_not_renew_current_package_publication(world):
    task = world.task(); before = copy.deepcopy(task.data)
    for release in (world.r1, world.r2):
        world.db.rows[Capability][release.package_id].status = 'retired'
    assert compat.verify_task_binding_snapshot(task, world.r2)
    with pytest.raises(HTTPException):
        compat.verify_task_objects(world.db, world.p, task, world.r2)
    assert task.data == before


@pytest.mark.parametrize('fault', ['family-missing', 'family-unknown', 'identity-missing',
    'ack-missing', 'ack-false', 'ack-wrong-name', 'unit-missing', 'required-numeric'])
def test_catalog_cannot_drop_family_metadata_or_coerce_an_original_field(world, fault):
    snapshot = copy.deepcopy(world.r1.data['snapshot']); row = snapshot['object_types'][0]
    if fault == 'family-missing': row.pop('contract_schema')
    elif fault == 'family-unknown': row['contract_schema'] = 'report-object-type-v171'
    elif fault == 'identity-missing': row.pop('business_identity')
    elif fault == 'ack-missing': row.pop('platform_identity_confirmation')
    elif fault == 'ack-false': row['platform_identity_confirmation']['acknowledge_platform_identity'] = False
    elif fault == 'ack-wrong-name': row['platform_identity_confirmation']['object_name'] = '别的对象'
    elif fault == 'unit-missing': row['fields'][0].pop('unit')
    else: row['fields'][0]['required'] = 0
    before = copy.deepcopy(snapshot)
    with pytest.raises(HTTPException) as error:
        compat.object_contract(snapshot, world.entity.entity_type)
    assert error.value.status_code == 409 and snapshot == before


@pytest.mark.parametrize('change', ['unit', 'identity-explanation', 'identity-unestablished', 'required'])
def test_valid_but_different_new_release_contract_is_not_inferred_compatible(world, change):
    value = proposal()
    if change == 'unit': value['fields'][0]['unit'] = '棵'
    elif change == 'identity-explanation': value['business_identity']['explanation'] += '改变后的人工判断'
    elif change == 'identity-unestablished':
        value['business_identity'] = {'status': 'not_established', 'field_labels': [], 'citations': [],
            'explanation': '后续尚未明确业务标识，须另行核对迁移。'}
    else: value['fields'][0]['required'] = True
    assert types.check_object_type(value) == value
    install_contract(world, world.r2, value); before = copy.deepcopy(vars(world.entity))
    with pytest.raises(HTTPException) as error:
        compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)
    assert error.value.status_code == 409 and vars(world.entity) == before


@pytest.mark.parametrize('fault', ['metadata-missing', 'old-family', 'unknown-family',
    'identity-explanation', 'ack-false', 'unit', 'invalid-attributes'])
def test_resealed_task_binding_still_rebuilds_its_complete_original_contract(world, fault):
    task = world.task(); binding = task.data[compat.BINDINGS]['objects'][0]
    definition = binding['definition']; snapshot = task.data['object_snapshots'][0]
    if fault == 'metadata-missing': definition.pop('object_type_contract')
    elif fault == 'old-family':
        definition['schema'] = 'instance-entity-definition-v1'; definition.pop('object_type_contract')
    elif fault == 'unknown-family': definition['schema'] = 'instance-entity-definition-v171'
    elif fault == 'identity-explanation':
        definition['object_type_contract']['business_identity']['explanation'] += '伪造后重封父摘要'
    elif fault == 'ack-false':
        definition['object_type_contract']['platform_identity_confirmation']['acknowledge_platform_identity'] = False
    elif fault == 'unit': snapshot['fields'][0]['unit'] = '厘米'
    else:
        key = next(field['key'] for field in snapshot['fields'] if field['kind'] == '整数')
        snapshot['attributes'][key] = True
        binding['attributes_hash'] = digest(snapshot['attributes'])
    legacy.reseal_bindings(task); before = copy.deepcopy(task.data)
    with pytest.raises(HTTPException) as error:
        compat.verify_task_binding_snapshot(task, world.r2)
    assert error.value.status_code == 409 and task.data == before


@pytest.mark.parametrize('change', ['unit', 'identity', 'family'])
def test_validated_package_self_hash_cannot_replace_the_release_metadata_mirror(world, change):
    package = world.db.rows[Capability][world.r2.package_id]
    spec = package.data['specification']; row = spec['object_catalog'][0]
    if change == 'unit':
        row['fields'][0]['unit'] = '棵'
        spec['entity_schemas'][row['key']]['properties'][row['fields'][0]['key']]['unit'] = '棵'
    elif change == 'identity': row['business_identity']['explanation'] += '包中另写的身份说明'
    else:
        for key in ('contract_schema', 'business_identity', 'platform_identity_confirmation'): row.pop(key)
    package.content_hash = digest(package.data); package.validated_hash = package.content_hash
    before = copy.deepcopy(vars(world.entity))
    with pytest.raises(HTTPException) as error:
        compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity)
    assert error.value.status_code == 409 and vars(world.entity) == before


def test_legacy_contract_is_unchanged_and_optional_numeric_values_cannot_be_retagged_legacy(monkeypatch):
    old = legacy.World(monkeypatch); task = old.task()
    definition = compat.verify_task_binding_snapshot(task, old.r2)['objects'][0]['definition']
    assert definition['schema'] == 'instance-entity-definition-v1' and 'object_type_contract' not in definition
    assert compat.verify_task_objects(old.db, old.p, task, old.r2)
    value = proposal()
    with pytest.raises(ValueError):
        compiler.ObjectType.model_validate({'name': value['name'], 'fields': value['fields']})
    row, schema = catalog_and_schema(value)
    for key in ('contract_schema', 'business_identity', 'platform_identity_confirmation'): row.pop(key)
    with pytest.raises(HTTPException):
        compat.object_contract({'object_types': [row], 'entity_schemas': {row['key']: schema}}, row['key'])


@pytest.mark.parametrize('origin_present', [False, True])
def test_actual_compiler_requires_new_origin_and_then_an_actual_adoption_ledger(origin_present):
    principal = NS(user_id='synthetic-actor', workspace_id='synthetic-workspace')
    value = proposal(); adoption_id = '00000000-0000-0000-0000-000000000170'
    config = {'version': 1, 'resources': [], 'connections': [], 'materials': [], 'businesses': [],
        'instanceProfile': {'name': '纯合同验证', 'domain': '公开合成'}, 'flows': {},
        'instanceObjects': [copy.deepcopy(value)], 'instanceObjectOrigins': {}}
    if origin_present:
        config['instanceObjectOrigins'][value['name']] = {'schema': 'report-object-schema-origin-v170',
            'adoption_id': adoption_id, 'plan_hash': 'a' * 64, 'kind': 'object', 'name': value['name'],
            'owner_path': '/objects/0', 'owner_value_hash': 'b' * 64}
    class EmptyInventory:
        gets = None
        def __init__(self): self.gets = []
        def scalars(self, query):
            expected = select(ReportConfigurationRecord).where(
                ReportConfigurationRecord.workspace_id == principal.workspace_id,
                ReportConfigurationRecord.kind.notin_(('full_handoff', 'full_replace', 'object_adopt_v169')))
            assert query.compare(expected)
            assert query.compile().params == expected.compile().params
            return []
        def get(self, model, identity):
            assert model is FullSemanticSchemaAdoption and identity == adoption_id
            self.gets.append((model, identity)); return None
    db = EmptyInventory(); before = copy.deepcopy((config, value))
    with pytest.raises(HTTPException) as error:
        compiler.compile_configuration(db, principal, config, [value], {})
    assert error.value.status_code == (404 if origin_present else 409)
    assert db.gets == ([(FullSemanticSchemaAdoption, adoption_id)] if origin_present else [])
    assert (config, value) == before
