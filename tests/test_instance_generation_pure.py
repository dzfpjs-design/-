"""Adversarial allocation/receipt proofs; no database or runtime success claim."""
import copy
import operator
import socket
import subprocess
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.sql.elements import BindParameter

from generic_agents import instance_api as api, instance_generation as receipts
from generic_agents.contracts import digest
from generic_agents.models import Audit, DomainInstance, InstanceRelease


NONCE = UUID('e0000000-0000-4000-8000-000000000001')


@pytest.fixture(autouse=True)
def forbid_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('generation pure tests forbid SQL, network, models and child processes')
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


def request(**changes):
    value = {'revision': 3, 'objects': [{'name': '样地', 'fields': [
        {'label': '编号', 'kind': '文本', 'required': True}]}],
        'confirmed_configuration_hash': 'configuration', 'confirm_configuration': True,
        'generation_intent': 'new_instance', 'request_id': NONCE}
    value.update(changes)
    return api.Generate.model_validate(value)


class FakeDB:
    """Evaluate the actual SQL predicates while recording forbidden write paths."""
    def __init__(self):
        self.rows = {model: [] for model in (Audit, DomainInstance, InstanceRelease)}
        self.events = [];self.commits = 0;self.flushes = 0

    def add(self, row):
        assert type(row) in self.rows, 'generation must not copy objects, memories or grants'
        self.rows[type(row)].append(row);self.events.append(('add', type(row).__name__))

    def flush(self):
        self.flushes += 1;self.events.append(('flush',))
        for rows in self.rows.values():
            for row in rows:
                if row.id is None:row.id = str(uuid4())
                if row.created_at is None:row.created_at = datetime(2026, 10, 16, tzinfo=timezone.utc)
                if row.version is None:row.version = 1
                if isinstance(row, DomainInstance) and row.status is None:row.status = 'draft'

    def commit(self):
        self.commits += 1;self.events.append(('commit',))

    def execute(self, statement, params=None):
        assert str(statement) == 'SELECT pg_advisory_xact_lock(:key)'
        assert set(params) == {'key'} and -(1 << 63) <= params['key'] < (1 << 63)
        self.events.append(('lock', params['key']))

    def value(self, expr, row):
        if isinstance(expr, BindParameter):return expr.value
        if getattr(expr, 'operator', None) is operator.eq:
            return self.value(expr.left, row) == self.value(expr.right, row)
        if getattr(getattr(expr, 'operator', None), '__name__', '') == 'json_getitem_op':
            return self.value(expr.left, row).get(self.value(expr.right, row))
        if getattr(expr, 'table', None) is not None:return getattr(row, expr.key)
        raise AssertionError('pure adapter rejects unexpected SQL expression')

    def selected(self, query):
        model = query.column_descriptions[0]['entity'];assert model in self.rows
        self.events.append(('read', model.__name__, query._for_update_arg is not None))
        rows = [row for row in self.rows[model]
                if all(self.value(condition, row) for condition in query._where_criteria)]
        if query._limit_clause is not None:rows = rows[:query._limit_clause.value]
        return rows

    def scalars(self, query):
        return self.selected(query)

    def scalar(self, query):
        rows = self.selected(query)
        if query.selected_columns[0].name == 'max':
            return max((row.release_number for row in rows), default=None)
        return rows[0] if rows else None


@pytest.fixture
def world(monkeypatch):
    db = FakeDB();p = NS(user_id='actor', workspace_id='workspace', role='admin')
    config = NS(version=3)
    snapshot = {'profile': {'name': '合成实例', 'domain': '纯函数核对'},
                'entity_schemas': {}, 'general_issues': ['尚未配置运行能力'], 'businesses': [],
                'data_requirements': {'schema': 'instance-data-requirements-v1', 'requirements_hash': 'a'*64}}
    def checked(db_arg, principal, body):
        assert db_arg is db;db.events.append(('checked',))
        return config, copy.deepcopy(snapshot), body.confirmed_configuration_hash
    monkeypatch.setattr(api, 'checked', checked)
    return NS(db=db, p=p, config=config, snapshot=snapshot)


def accepted(world, **changes):
    body = request(**changes);result = api.generate(body, world.p, world.db)
    record = next(row for row in world.db.rows[Audit] if row.action == receipts.ACTION
                  and row.data['request_id'] == str(body.request_id))
    return body, result, record


@pytest.mark.parametrize('offset', [-7, 0, 8])
def test_original_receipt_survives_database_timezone_rendering_without_rewriting_audit(world, offset):
    body, result, record = accepted(world)
    frozen = copy.deepcopy(record.data)
    release = next(row for row in world.db.rows[InstanceRelease] if row.id == result['id'])
    release.created_at = release.created_at.astimezone(timezone(timedelta(hours=offset)))
    recovered = receipts.recover_request(world.db, world.p, body.request_id, api.release_view, record=record)
    assert recovered['release'] == {k:v for k,v in result.items() if k!='generation_receipt'}
    assert record.data == frozen
    release.created_at += timedelta(seconds=1)
    with pytest.raises(HTTPException) as error:
        receipts.recover_request(world.db, world.p, body.request_id, api.release_view, record=record)
    assert error.value.status_code == 409


@pytest.mark.parametrize('changes', [
    {'request_id': None}, {'generation_intent': None},
    {'generation_intent': 'new_instance', 'instance_id': 'existing'},
    {'generation_intent': 'new_instance', 'instance_id': ''},
    {'generation_intent': 'new_release'}, {'generation_intent': 'new_release', 'instance_id': ''},
    {'generation_intent': 'new_release', 'instance_id': '  '}, {'request_id': 'not-a-uuid'},
    {'generation_intent': 'unknown'}, {'unknown_field': 'forbidden'}])
def test_explicit_contract_rejects_ambiguous_or_unbound_intent(changes):
    with pytest.raises(ValidationError):request(**changes)


def test_hash_uses_full_normalized_body_including_defaults_and_uuid():
    body = request(objects=[{'name': ' 样地 ', 'fields': [
        {'label': ' 编号 ', 'kind': '文本', 'required': True},
        {'label': ' 数量 ', 'kind': '数值', 'unit': ' 株 '}]}])
    canonical = body.model_dump(mode='json')
    assert canonical['request_id'] == str(NONCE) and canonical['instance_id'] is None
    assert canonical['role_bindings'] == {}
    assert canonical['objects'] == [{'name': '样地', 'fields': [
        {'label': '编号', 'kind': '文本', 'required': True, 'unit': None},
        {'label': '数量', 'kind': '数值', 'required': False, 'unit': '株'}]}]
    assert digest(canonical) == digest(request(**canonical).model_dump(mode='json'))


def test_two_explicit_nonces_allocate_independent_instances_for_identical_configuration(world):
    _, first, _ = accepted(world)
    _, second, _ = accepted(world, request_id=uuid4())
    assert first['configuration_hash'] == second['configuration_hash']
    assert first['id'] != second['id'] and first['instance_id'] != second['instance_id']
    instances = world.db.rows[DomainInstance]
    assert len(instances) == 2 and len({row.namespace for row in instances}) == 2
    assert all(row.active_release_id is None for row in instances)
    assert all(release.data['object_lineage_parent']['parent_release_id'] is None
               for release in world.db.rows[InstanceRelease])
    assert world.db.commits == 2


def test_receipt_audit_has_only_fingerprints_and_result_summary(world):
    body, result, record = accepted(world)
    assert record.object_id == result['id'] and record.data['request_hash'] == digest(body.model_dump(mode='json'))
    assert record.data['result_hash'] == digest({key: value for key, value in result.items() if key != 'generation_receipt'})
    assert set(record.data) == {'schema', 'request_id', 'request_hash', 'generation_intent', 'instance_id',
        'release_id', 'release_actor_id', 'release_number', 'configuration_hash', 'result_hash', 'record_hash'}
    assert result['generation_receipt']['actor_id'] == world.p.user_id
    assert result['generation_receipt']['workspace_id'] == world.p.workspace_id
    assert world.db.events[-2:] == [('add', 'Audit'), ('commit',)]


def test_same_nonce_recovers_exact_result_before_any_draft_or_compiler_access(world, monkeypatch):
    body, first, _ = accepted(world);before = {model: len(rows) for model, rows in world.db.rows.items()}
    world.config.version = 40;world.snapshot['profile']['name'] = '已改变'
    monkeypatch.setattr(api, 'checked', lambda *args: pytest.fail('saved nonce must not read draft or compile'))
    world.db.events.clear()
    again = api.generate(body, world.p, world.db)
    assert again == first and {model: len(rows) for model, rows in world.db.rows.items()} == before
    assert world.db.events[0][0] == 'lock' and world.db.events[1][0:2] == ('read', 'Audit')
    assert not any(event[0] in {'add', 'flush'} for event in world.db.events)


@pytest.mark.parametrize('changes', [
    {'revision': 4}, {'confirmed_configuration_hash': 'changed'}, {'role_bindings': {'负责人': 'other'}},
    {'objects': [{'name': '其他对象', 'fields': [{'label': '编号', 'kind': '文本', 'required': True}]}]},
    {'generation_intent': 'new_release', 'instance_id': 'other-instance'}])
def test_same_nonce_cannot_resign_body_after_acceptance(world, monkeypatch, changes):
    accepted(world)
    monkeypatch.setattr(api, 'checked', lambda *args: pytest.fail('conflict must precede draft access'))
    with pytest.raises(HTTPException) as error:api.generate(request(**changes), world.p, world.db)
    assert error.value.status_code == 409 and len(world.db.rows[DomainInstance]) == 1


@pytest.mark.parametrize('field', ['workspace_id', 'user_id'])
def test_nonce_is_globally_bound_to_original_actor_and_workspace(world, monkeypatch, field):
    accepted(world);principal = copy.copy(world.p);setattr(principal, field, 'different')
    monkeypatch.setattr(api, 'checked', lambda *args: pytest.fail('foreign nonce must not compile'))
    with pytest.raises(HTTPException) as error:api.generate(request(), principal, world.db)
    assert error.value.status_code == 409 and error.value.detail == receipts.CONFLICT
    assert 'actor' not in error.value.detail and 'workspace' not in error.value.detail


@pytest.mark.parametrize('role', ['operator', 'viewer'])
def test_post_requires_admin_even_when_receipt_exists(world, role):
    body, _, _ = accepted(world);world.p.role = role;world.db.events.clear()
    with pytest.raises(HTTPException) as error:api.generate(body, world.p, world.db)
    assert error.value.status_code == 403 and world.db.events == []


@pytest.mark.parametrize('role', ['admin', 'operator', 'viewer'])
def test_get_recovers_own_saved_request_after_role_downgrade_without_writes(world, role, monkeypatch):
    body, result, _ = accepted(world);world.p.role = role
    before = (world.db.commits, world.db.flushes)
    monkeypatch.setattr(api, 'checked', lambda *args: pytest.fail('GET must not compile'))
    world.db.events.clear();restored = api.generation_request(body.request_id, world.p, world.db)
    assert restored == {**result['generation_receipt'], 'release': {
        key: value for key, value in result.items() if key != 'generation_receipt'}}
    assert (world.db.commits, world.db.flushes) == before
    assert all(event[0] == 'read' for event in world.db.events)


@pytest.mark.parametrize('field', ['workspace_id', 'user_id'])
def test_get_foreign_receipt_is_indistinguishable_from_unknown_nonce(world, field):
    body, _, _ = accepted(world);principal = copy.copy(world.p);setattr(principal, field, 'different')
    assert api.generation_request(body.request_id, principal, world.db) == {
        'saved': False, 'request_id': str(body.request_id)}
    assert api.generation_request(uuid4(), world.p, world.db)['saved'] is False


@pytest.mark.parametrize('target', ['data', 'actor', 'workspace', 'object', 'release', 'release_actor',
                                    'request_hash', 'missing_release', 'missing_instance'])
def test_corrupt_or_unverifiable_receipt_never_claims_saved(world, target):
    body, _, record = accepted(world)
    if target == 'data':record.data = {**record.data, 'release_number': 2}
    elif target == 'actor':record.actor_id = 'other'
    elif target == 'workspace':record.workspace_id = 'other'
    elif target == 'object':record.object_id = 'other'
    elif target == 'release':world.db.rows[InstanceRelease][0].data['snapshot']['profile']['name'] = 'changed'
    elif target == 'release_actor':world.db.rows[InstanceRelease][0].actor_id = 'other'
    elif target == 'request_hash':record.data['request_hash'] = '0' * 64
    elif target == 'missing_release':world.db.rows[InstanceRelease].clear()
    elif target == 'missing_instance':world.db.rows[DomainInstance].clear()
    with pytest.raises(HTTPException) as error:api.generate(body, world.p, world.db)
    assert error.value.status_code == 409


def test_get_rejects_changed_receipt_nonce_and_hash(world):
    _, _, record = accepted(world);replacement = uuid4();record.data['request_id'] = str(replacement)
    with pytest.raises(HTTPException) as error:api.generation_request(replacement, world.p, world.db)
    assert error.value.status_code == 409


@pytest.mark.parametrize('status', ['paused', 'partially_active'])
def test_get_recovers_original_release_after_instance_status_or_active_release_changes(world, status):
    body, first, _ = accepted(world)
    _, latest, _ = accepted(world, generation_intent='new_release', instance_id=first['instance_id'],
        confirmed_configuration_hash='configuration-v2', request_id=uuid4())
    instance = world.db.rows[DomainInstance][0]
    instance.status = status;instance.active_release_id = latest['id'];instance.version = 5
    restored = api.generation_request(body.request_id, world.p, world.db)
    assert restored['release_id'] == first['id'] and restored['release']['id'] == first['id']
    assert restored['release'] == {key: value for key, value in first.items() if key != 'generation_receipt'}


def test_duplicate_receipts_reject_ambiguous_recovery(world):
    body, _, record = accepted(world);world.db.rows[Audit].append(copy.copy(record))
    with pytest.raises(HTTPException) as error:api.generation_request(body.request_id, world.p, world.db)
    assert error.value.status_code == 409


def test_new_release_preserves_same_instance_configuration_deduplication_and_row_lock(world):
    _, initial, _ = accepted(world);world.db.events.clear()
    body, result, _ = accepted(world, generation_intent='new_release', instance_id=initial['instance_id'], request_id=uuid4())
    assert result['id'] == initial['id'] and result['generation_receipt']['request_id'] == str(body.request_id)
    assert len(world.db.rows[InstanceRelease]) == 1 and len(world.db.rows[DomainInstance]) == 1
    assert ('read', 'DomainInstance', True) in world.db.events


def test_new_release_advances_number_within_selected_instance_only(world):
    _, first, _ = accepted(world)
    original = copy.deepcopy(first)
    _, result, _ = accepted(world, generation_intent='new_release', instance_id=first['instance_id'],
        confirmed_configuration_hash='configuration-v2', request_id=uuid4())
    assert result['instance_id'] == first['instance_id'] and result['release_number'] == 2
    assert api.release_view(world.db.rows[InstanceRelease][0]) == {
        key: value for key, value in original.items() if key != 'generation_receipt'}
    assert len(world.db.rows[DomainInstance]) == 1


def test_legacy_absent_fields_retain_workspace_configuration_deduplication_and_response_shape(world):
    _, initial, _ = accepted(world)
    legacy = request(generation_intent=None, request_id=None)
    count = len(world.db.rows[Audit]);world.db.events.clear()
    result = api.generate(legacy, world.p, world.db)
    assert result == {key: value for key, value in initial.items() if key != 'generation_receipt'}
    assert len(world.db.rows[DomainInstance]) == 1 and len(world.db.rows[Audit]) == count
    assert not any(event[0] == 'lock' for event in world.db.events)


def test_legacy_first_generation_keeps_original_release_response(world):
    result = api.generate(request(generation_intent=None, request_id=None), world.p, world.db)
    assert 'generation_receipt' not in result and world.db.commits == 1
    assert not any(record.action == receipts.ACTION for record in world.db.rows[Audit])


def test_fresh_explicit_request_still_requires_current_preview_and_profile(world, monkeypatch):
    monkeypatch.setattr(api, 'checked', lambda *args: (world.config, world.snapshot, 'new-signature'))
    with pytest.raises(HTTPException) as error:api.generate(request(), world.p, world.db)
    assert error.value.status_code == 409 and not world.db.rows[DomainInstance]
    monkeypatch.setattr(api, 'checked', lambda *args: (world.config, world.snapshot, 'configuration'))
    world.snapshot['profile']['domain'] = ' '
    with pytest.raises(HTTPException) as error:api.generate(request(), world.p, world.db)
    assert error.value.status_code == 422 and not world.db.rows[DomainInstance]


def test_nonce_lock_is_global_stable_and_precedes_receipt_and_compile(world):
    api.generate(request(), world.p, world.db)
    assert world.db.events[0][0] == 'lock'
    assert world.db.events[1][0:2] == ('read', 'Audit') and world.db.events[2] == ('checked',)
    key = world.db.events[0][1];world.db.events.clear()
    receipts.lock_request(world.db, str(NONCE))
    assert world.db.events == [('lock', key)]
    world.db.events.clear();receipts.lock_request(world.db, uuid4())
    assert world.db.events[0][1] != key
