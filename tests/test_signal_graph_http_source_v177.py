"""Actual HTTP-source contract and routing gates with synthetic IO boundaries.

prepare/configured/checked_contract/guard/registration_matches, the numeric-rule
registration checks, mapping freeze and first-registration SQL are real. Current
resource lookup, independent mapped-origin/Exchange authority and extraction
receipt lookup are explicit adapter spies. This is not PostgreSQL, physical
device, source-authority, Worker or end-to-end Task acceptance evidence. No API
module or PG fixture is imported, and these tests cannot send HTTP or run tools.
"""
import copy
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from generic_agents import signal_graph_http_source_v177 as app
from generic_agents import http_observation_mapping_v177 as mapping
from generic_agents import http_observation_exchange_v177 as ledger
from generic_agents import http_observation_transport_v177 as transport
from generic_agents import mapped_acquisition_origin_v177 as mapped_origin
from generic_agents import observation_integrity
from generic_agents import simulation_loop_v176 as simulation_loop
from generic_agents.models import Audit, Capability, Entity, Observation


FIELD = 'field_1234567890abcdef'
TYPE = 'object_1234567890abcdef'
T = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def uid(label):
    return str(uuid5(NAMESPACE_URL, 'public-http-graph-source:' + label))


def same_statement(actual, expected):
    """Keep the entire actual SELECT, typed binds and scoped predicates."""
    left, right = actual.compile(), expected.compile()
    assert str(left) == str(right), 'unexpected SQL; the fixture must not weaken source scoping'
    assert mapping.canonical(left.params) == mapping.canonical(right.params)
    assert actual._for_update_arg is None, 'historical source checks must not take worker row locks'


class ReadIO:
    """Two narrowly supported read queries, never a general database emulator."""
    def __init__(self, principal):
        self.p = principal
        self.capabilities = []
        self.receipts = []
        self.observations = []
        self.exchange_id = None
        self.statements = []
        self.error = None

    def scalars(self, statement):
        if self.error is not None:
            raise self.error
        self.statements.append(statement)
        model = statement.column_descriptions[0]['entity']
        if model is Capability:
            same_statement(statement, select(Capability).where(
                Capability.workspace_id == self.p.workspace_id,
                Capability.kind == 'source', Capability.status == 'published'))
            return self.capabilities
        assert model is Audit
        same_statement(statement, select(Audit).where(
            Audit.workspace_id == self.p.workspace_id, Audit.action == ledger.ACTION,
            Audit.object_id == self.exchange_id,
            Audit.data['operation'].as_string() == 'receipt.recorded'))
        return [row for row in self.receipts if row.workspace_id == self.p.workspace_id
            and row.action == ledger.ACTION and row.object_id == self.exchange_id
            and row.data.get('operation') == 'receipt.recorded']

    def scalar(self, statement):
        if self.error is not None:
            raise self.error
        self.statements.append(statement)
        # The actual public first_registration SQL must span processing versions:
        # a later re-processing must not renew the original receipt's age.
        parameters = statement.compile().params
        identity = parameters['source_id_1']
        record_id = parameters['source_record_id_1']
        same_statement(statement, select(func.min(Observation.created_at)).where(
            Observation.workspace_id == self.p.workspace_id,
            Observation.source_id == identity, Observation.source_record_id == record_id))
        dates = [row.created_at for row in self.observations
            if row.workspace_id == self.p.workspace_id and row.source_id == identity
            and row.source_record_id == record_id]
        return min(dates) if dates else None


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Pure HTTP-source tests cannot connect or launch a process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def scene(monkeypatch):
    p = NS(user_id=uid('reader'), workspace_id=uid('workspace'), role='operator')
    instance = NS(id=uid('instance'), workspace_id=p.workspace_id, namespace='public_http_fixture')
    schemas = {TYPE: {'type': 'object', 'properties': {FIELD: {'type': 'number', 'unit': '°C'}}}}
    package_data = {'kind': 'domain_package', 'specification': {'entity_schemas': schemas}}
    package = NS(id=uid('package'), workspace_id=p.workspace_id, namespace=instance.namespace,
        kind='domain_package', status='published', data=package_data,
        content_hash=mapping.digest(package_data))
    release = NS(id=uid('release'), instance_id=instance.id, package_id=package.id,
        configuration_hash=mapping.digest({'public_configuration': 1}))
    environment_data = {'schema': 'simulation-environment-v176', 'simulation': True,
        'origin_kind': 'simulated_equipment', 'field_verified': False,
        'instance_id': instance.id, 'release_id': release.id, 'namespace': instance.namespace,
        'package_id': package.id, 'configuration_hash': release.configuration_hash,
        'created_by': uid('source-author'), 'note': '公开合成来源，仅用于分派单元验证'}
    env = NS(id=uid('environment'), workspace_id=p.workspace_id, data=environment_data)
    scope = {'workspace_id': p.workspace_id, 'environment_id': env.id,
        'instance_id': instance.id, 'release_id': release.id, 'package_id': package.id,
        'package_hash': package.content_hash, 'entity_type': TYPE}
    links = {'temperature_c': {'field_key': FIELD, 'unit': '°C'}}
    configured = {'schema': mapping.CONFIG_SCHEMA, 'scope': scope, 'links': links,
        'supplier_contract': mapping.SUPPLIER, 'time_semantics': mapping.TIME}
    extractor_data = {'kind': 'tool', 'description': '本地温度阈值规则',
        'allowed_agent_roles': ['perception'],
        'execution': {'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False},
        'data_policy': {'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False},
        'specification': {'rules': [{'id': 'temperature_high', 'field': FIELD,
            'operator': 'gt', 'threshold': 35.0, 'unit': '°C'}]}}
    extractor = NS(id=uid('extractor'), workspace_id=p.workspace_id, kind='tool', status='published',
        namespace='public_http_fixture', name='temperature_rule', version=1, data=extractor_data,
        content_hash=mapping.digest(extractor_data), validated_hash=mapping.digest(extractor_data))
    source_data = {'kind': 'source', 'description': '隔离 HTTP 模拟设备',
        'allowed_agent_roles': ['perception'],
        'execution': {'adapter': 'http', 'side_effect': True,
            'endpoint': 'http://fixture.invalid/acquisitions',
            'receipt_endpoint': 'http://fixture.invalid/acquisitions/receipts'},
        'data_policy': {'simulation': True, 'test_only': True,
            'cloud_allowed': False, 'deployment': 'local'},
        'specification': {'simulation_environment_id': env.id,
            transport.MAPPING_KEY: configured, transport.EXTRACTOR_ID: extractor.id,
            transport.EXTRACTOR_HASH: extractor.content_hash,
            'observation_capability': {'fields': [FIELD], 'modalities': ['numeric'], 'actions': ['observe']}}}
    source = NS(id=uid('source'), workspace_id=p.workspace_id, kind='source', status='published',
        name='public_http_source', version=1, data=source_data,
        content_hash=mapping.digest(source_data))
    entity = NS(id=uid('entity'), workspace_id=p.workspace_id, namespace=instance.namespace,
        schema_capability_id=package.id, entity_type=TYPE)
    c = NS(p=p, instance=instance, release=release, package=package, env=env, source=source,
        extractor=extractor, entity=entity, calls=[], authority_error=None,
        environment_error=None, receipt_error=None, extraction_error=None)
    c.db = ReadIO(p)
    records = {row.id: row for row in (source, extractor, package)}

    def published(db, principal, identity, kind=None):
        assert db is c.db and principal is c.p and identity in records
        row = records[identity]
        assert kind == row.kind
        c.calls.append(('published', identity, kind))
        return row

    def environment(db, principal, identity, *, live=True):
        assert db is c.db and principal is c.p and identity == env.id and live is True
        c.calls.append(('environment', live))
        if c.environment_error is not None:
            raise c.environment_error
        return env, instance, release

    def scoped(db, model, identity, principal):
        assert db is c.db and principal is c.p
        c.calls.append(('scoped', model, identity))
        if model is Entity:
            assert identity == entity.id
            return entity
        if model is app.Environment:
            assert identity == env.id
            return env
        assert model is Capability and identity in records
        return records[identity]

    monkeypatch.setattr(app, 'published', published)
    monkeypatch.setattr(transport, 'published', published)
    monkeypatch.setattr(app, 'scoped', scoped)
    monkeypatch.setattr(simulation_loop, 'environment', environment)
    c.contract = app.prepare(c.db, p, instance, release, source, extractor, [entity.id])
    c.result = {'attributes': {FIELD: 38.0}, 'anomalies': [{'rule_id': 'temperature_high'}]}
    original = {'source_id': source.id, 'source_record_id': 'http-acquisition:' + uid('exchange'),
        'processing_version': transport.PROCESSING_VERSION, 'entity_id': entity.id,
        'extractor_id': extractor.id}
    observation = Observation(id=uid('observation'), workspace_id=p.workspace_id,
        source_id=source.id, source_record_id=original['source_record_id'],
        processing_version=original['processing_version'], entity_id=entity.id, status='processed',
        created_at=T + timedelta(seconds=30), data={'extractor_id': extractor.id,
            'original_request': original, 'input_hash': mapping.digest(original),
            'invocation_id': uid('invocation'), 'extracted': copy.deepcopy(c.result)})
    c.observation = observation
    c.exchange = NS(id=uid('exchange'), observation_id=observation.id,
        data={'freeze': {'mapping': copy.deepcopy(c.contract['mapping'])}})
    c.proof = {'schema': 'http-mapped-checked-origin-v177', 'exchange_id': c.exchange.id,
        'environment_id': env.id, 'instance_id': instance.id, 'release_id': release.id,
        'source_id': source.id, 'extractor_id': extractor.id, 'entity_id': entity.id,
        'mapping_hash': c.contract['mapping']['mapping_hash'],
        'simulation': True, 'test_only': True, 'field_verified': False}

    def authority(db, principal, actual, *, execution=False, result=None):
        assert db is c.db and principal is c.p and actual is observation
        assert execution is False and mapping.canonical(result) == mapping.canonical(c.result)
        c.calls.append(('historical_origin', execution))
        if c.authority_error is not None:
            raise c.authority_error
        return copy.deepcopy(c.proof)

    def checked_exchange(db, principal, identity, *, lock=False):
        assert db is c.db and principal is c.p and identity == c.exchange.id and lock is False
        c.calls.append(('exchange', lock))
        if c.receipt_error is not None:
            raise c.receipt_error
        return c.exchange

    def checked_report(db, principal, actual):
        assert db is c.db and principal is c.p and actual is observation
        c.calls.append(('extraction_receipt',))
        if c.extraction_error is not None:
            raise c.extraction_error
        return {'extracted': copy.deepcopy(c.result)}

    monkeypatch.setattr(mapped_origin, 'guard', authority)
    monkeypatch.setattr(ledger, 'checked_store', checked_exchange)
    monkeypatch.setattr(observation_integrity, 'checked_report', checked_report)
    event_data = {'source_id': source.id, 'entity_id': entity.id, 'observation_id': observation.id,
        'invocation_id': observation.data['invocation_id'], 'extracted': copy.deepcopy(c.result),
        'simulation_origin': copy.deepcopy(c.proof), 'simulation': True,
        'field_verified': False, 'origin_kind': 'simulated_equipment'}
    c.event = NS(kind='perception.anomaly', workspace_id=p.workspace_id,
        created_at=T + timedelta(seconds=40), data=event_data)
    c.basis = {'schema': 'reported-anomaly-v1', 'event_hash': mapping.digest(event_data),
        'instance_id': instance.id, 'release_id': release.id, 'extracted': copy.deepcopy(c.result),
        'simulation_origin': copy.deepcopy(c.proof)}
    c.grant = {'workspace_id': p.workspace_id, 'instance_id': instance.id, 'release_id': release.id,
        'source_id': source.id, 'extractor_id': extractor.id, 'entity_ids': [entity.id],
        'enabled_at': T.isoformat(), 'binding': {app.CONTRACT_KEY: copy.deepcopy(c.contract)}}
    c.at = T + timedelta(seconds=50)
    c.db.exchange_id = c.exchange.id
    c.db.observations = [observation]
    c.db.capabilities = [source]
    c.db.receipts = [Audit(id=uid('first-receipt'), workspace_id=p.workspace_id,
        actor_id=uid('source-author'), action=ledger.ACTION, object_id=c.exchange.id,
        created_at=T + timedelta(seconds=10), data={'operation': 'receipt.recorded'})]
    c.calls.clear()
    return c


def prepare(c, entity_ids=None):
    return app.prepare(c.db, c.p, c.instance, c.release, c.source, c.extractor,
        [c.entity.id] if entity_ids is None else entity_ids)


def guard(c, contract=None, **kwargs):
    return app.guard(c.db, c.p, c.contract if contract is None else contract,
        c.observation, result=c.result, **kwargs)


def matches(c):
    return app.registration_matches(c.db, c.p, c.grant, c.event, c.observation, c.basis, c.at)


def test_prepare_uses_actual_registered_mapping_and_local_numeric_rule(scene):
    c = scene
    before = copy.deepcopy([c.source.data, c.extractor.data, c.package.data, c.env.data])
    value = prepare(c)
    assert set(value) == {'schema', 'family', 'scope', 'source_id', 'source_hash', 'extractor_id',
        'extractor_hash', 'environment_hash', 'configuration_hash', 'mapping', 'field_units',
        'simulation', 'test_only', 'field_verified'}
    assert value == c.contract and 'source_id' not in value['scope']
    expected = mapping.freeze_mapping(c.source.data,
        {**value['scope'], 'source_id': c.source.id},
        c.package.data['specification']['entity_schemas'],
        c.source.data['specification'][transport.MAPPING_KEY]['links'])
    assert value['mapping'] == expected and value['field_units'] == {FIELD: '°C'}
    assert ('environment', True) in c.calls
    assert ('published', c.extractor.id, 'tool') in c.calls
    assert not [call for call in c.calls if call[0] in {'historical_origin', 'exchange'}]
    value['mapping']['mapping']['scope']['entity_type'] = 'changed_return_copy'
    assert [c.source.data, c.extractor.data, c.package.data, c.env.data] == before


@pytest.mark.parametrize('damage', ['adapter', 'scope', 'package_hash', 'entity_type',
    'extractor_pin', 'extractor_content', 'rule_unit', 'rule_field', 'rule_side_effect', 'cloud'])
def test_prepare_rejects_current_scope_registration_or_mapping_changes(scene, damage):
    c = scene
    if damage == 'adapter': c.source.data['execution']['adapter'] = 'builtin'
    if damage == 'scope': c.source.data['specification'][transport.MAPPING_KEY]['scope']['release_id'] = uid('other-release')
    if damage == 'package_hash': c.package.content_hash = 'b' * 64
    if damage == 'entity_type': c.entity.entity_type = 'other_object'
    if damage == 'extractor_pin': c.source.data['specification'][transport.EXTRACTOR_HASH] = 'b' * 64
    if damage == 'extractor_content': c.extractor.data['description'] = 'changed after publication'
    if damage == 'rule_unit': c.extractor.data['specification']['rules'][0]['unit'] = 'K'
    if damage == 'rule_field': c.extractor.data['specification']['rules'][0]['field'] = 'other_field'
    if damage == 'rule_side_effect': c.extractor.data['execution']['side_effect'] = 0
    if damage == 'cloud': c.extractor.data['data_policy']['cloud_allowed'] = True
    with pytest.raises(HTTPException) as error:
        prepare(c)
    assert error.value.status_code == 409


def test_real_mapping_marker_cannot_fall_back_to_manual_or_builtin(scene):
    c = scene
    assert app.configured(c.source) is True
    c.source.data['specification'].pop(transport.MAPPING_KEY)
    assert app.configured(c.source) is False
    c.source.data['specification'][transport.MAPPING_KEY] = None
    with pytest.raises(HTTPException): prepare(c)
    c.source.data['execution']['adapter'] = 'builtin'
    with pytest.raises(HTTPException): app.configured(c.source)


@pytest.mark.parametrize('damage', ['unknown_family', 'unknown_schema', 'extra', 'scope_source_id',
    'simulation_int', 'test_only_int', 'field_verified_int', 'units', 'source_hash'])
def test_contract_is_closed_typed_and_does_not_accept_manual_contracts(scene, damage):
    value = copy.deepcopy(scene.contract)
    if damage == 'unknown_family': value['family'] = 'reported-manual'
    if damage == 'unknown_schema': value['schema'] = 'signal-graph-binding-v134'
    if damage == 'extra': value['reported_rule_contract'] = {}
    if damage == 'scope_source_id': value['scope']['source_id'] = value['source_id']
    if damage == 'simulation_int': value['simulation'] = 1
    if damage == 'test_only_int': value['test_only'] = 1
    if damage == 'field_verified_int': value['field_verified'] = 0
    if damage == 'units': value['field_units'][FIELD] = 'K'
    if damage == 'source_hash': value['source_hash'] = 'b' * 64
    with pytest.raises(HTTPException) as error: app.checked_contract(value)
    assert error.value.status_code == 409


def test_options_keep_actual_selected_source_and_skip_other_scope_or_family(scene):
    c = scene
    plain = copy.deepcopy(c.source)
    plain.id = uid('plain-source'); plain.data['specification'].pop(transport.MAPPING_KEY)
    bad = copy.deepcopy(c.source)
    bad.data['execution']['adapter'] = 'builtin'
    c.db.capabilities = [plain, bad, c.source]
    values = app.options(c.db, c.p, c.instance, c.release)
    assert len(values) == 1 and values[0]['source']['id'] == c.source.id
    assert values[0]['extractor']['id'] == c.extractor.id
    assert values[0]['extractor']['rules'][0]['field'] == FIELD
    c.environment_error = HTTPException(409, '当前环境范围不符')
    assert app.options(c.db, c.p, c.instance, c.release) == []


def test_historical_guard_calls_independent_origin_and_receipt_without_worker_execution(scene):
    c = scene
    value = guard(c, expected_origin=copy.deepcopy(c.proof))
    assert value == c.proof
    assert c.calls.count(('historical_origin', False)) == 1
    assert c.calls.count(('exchange', False)) == 1
    assert c.calls.count(('extraction_receipt',)) == 1
    assert not [call for call in c.calls if call[0] in {'published', 'environment'}]
    value['source_id'] = 'changed-copy'
    assert c.proof['source_id'] == c.source.id


@pytest.mark.parametrize('boundary', ['authority_error', 'receipt_error', 'extraction_error'])
def test_historical_independent_proof_failures_propagate_without_alternate_family(scene, boundary):
    c = scene
    failure = HTTPException(409, '独立原记录已缺失')
    setattr(c, boundary, failure)
    with pytest.raises(HTTPException) as error: guard(c)
    assert error.value is failure


@pytest.mark.parametrize('damage', ['proof_family', 'proof_scope', 'proof_mapping', 'exchange_mapping',
    'exchange_observation', 'environment', 'source_content', 'extractor_content', 'expected_origin'])
def test_historical_guard_rejects_wrong_scope_mapping_or_current_manifest(scene, damage):
    c = scene
    if damage == 'proof_family': c.proof['schema'] = 'simulation-checked-origin-v176'
    if damage == 'proof_scope': c.proof['release_id'] = uid('other-release')
    if damage == 'proof_mapping': c.proof['mapping_hash'] = 'b' * 64
    if damage == 'exchange_mapping': c.exchange.data['freeze']['mapping']['source_id'] = uid('other-source')
    if damage == 'exchange_observation': c.exchange.observation_id = uid('other-observation')
    if damage == 'environment': c.env.data['note'] = 'changed after freezing'
    if damage == 'source_content': c.source.data['description'] = 'changed after freezing'
    if damage == 'extractor_content': c.extractor.data['description'] = 'changed after freezing'
    expected = copy.deepcopy(c.proof)
    if damage == 'expected_origin': expected['source_id'] = uid('other-source')
    with pytest.raises(HTTPException) as error: guard(c, expected_origin=expected)
    assert error.value.status_code == 409


def test_registration_reads_actual_earliest_receipt_and_first_registration(scene):
    c = scene
    # Later GET/receipt records cannot replace the actual first receipt.
    c.db.receipts.append(Audit(id=uid('later-receipt'), workspace_id=c.p.workspace_id,
        actor_id=uid('source-author'), action=ledger.ACTION, object_id=c.exchange.id,
        created_at=T + timedelta(seconds=25), data={'operation': 'receipt.recorded'}))
    assert matches(c) is True
    assert len(c.db.statements) == 2
    assert ('historical_origin', False) in c.calls and ('exchange', False) in c.calls


@pytest.mark.parametrize('damage', ['earliest_receipt_before_grant', 'first_observation_before_grant',
    'receipt_after_first', 'observation_after_event', 'event_after_clock'])
def test_new_observation_or_reprocessing_cannot_renew_old_receipt_authority(scene, damage):
    c = scene
    if damage == 'earliest_receipt_before_grant':
        c.db.receipts.append(Audit(id=uid('old-receipt'), workspace_id=c.p.workspace_id,
            action=ledger.ACTION, object_id=c.exchange.id, data={'operation': 'receipt.recorded'},
            created_at=T - timedelta(seconds=1)))
    if damage == 'first_observation_before_grant':
        c.db.observations.append(Observation(id=uid('old-observation'), workspace_id=c.p.workspace_id,
            source_id=c.source.id, source_record_id=c.observation.source_record_id,
            processing_version='older-processing-version', created_at=T - timedelta(seconds=1)))
    if damage == 'receipt_after_first': c.db.receipts[0].created_at = T + timedelta(seconds=31)
    if damage == 'observation_after_event': c.observation.created_at = T + timedelta(seconds=41)
    if damage == 'event_after_clock': c.event.created_at = T + timedelta(seconds=51)
    assert matches(c) is False


def test_equal_time_boundaries_are_accepted_without_using_supplier_sample_time(scene):
    c = scene
    c.db.receipts[0].created_at = T
    c.observation.created_at = T
    c.event.created_at = T
    c.at = T
    c.observation.observed_at = T - timedelta(days=20)
    assert matches(c) is True


@pytest.mark.parametrize('unrelated', ['source', 'extractor', 'entity', 'event_kind', 'event_workspace'])
def test_unrelated_source_or_object_does_not_block_another_registration(scene, unrelated):
    c = scene
    if unrelated == 'source': c.observation.source_id = uid('other-source')
    if unrelated == 'extractor': c.observation.data['extractor_id'] = uid('other-extractor')
    if unrelated == 'entity': c.observation.entity_id = uid('other-entity')
    if unrelated == 'event_kind': c.event.kind = 'data.received'
    if unrelated == 'event_workspace': c.event.workspace_id = uid('other-workspace')
    c.authority_error = AssertionError('unrelated data must not enter another source authority')
    assert matches(c) is False and c.calls == [] and c.db.statements == []


@pytest.mark.parametrize('damage', ['missing_receipt', 'missing_registration', 'naive_receipt',
    'naive_observation', 'original_hash', 'event_hash', 'event_simulation', 'basis_origin', 'grant_scope'])
def test_matching_but_damaged_origin_never_degrades_to_manual_or_silent_skip(scene, damage):
    c = scene
    if damage == 'missing_receipt': c.db.receipts.clear()
    if damage == 'missing_registration': c.db.observations.clear()
    if damage == 'naive_receipt': c.db.receipts[0].created_at = T.replace(tzinfo=None)
    if damage == 'naive_observation': c.observation.created_at = T.replace(tzinfo=None)
    if damage == 'original_hash': c.observation.data['input_hash'] = 'b' * 64
    if damage == 'event_hash': c.basis['event_hash'] = 'b' * 64
    if damage == 'event_simulation':
        c.event.data['simulation'] = 1
        c.basis['event_hash'] = mapping.digest(c.event.data)
    if damage == 'basis_origin': c.basis['simulation_origin']['exchange_id'] = uid('other-exchange')
    if damage == 'grant_scope': c.grant['release_id'] = uid('other-release')
    with pytest.raises(HTTPException) as error: matches(c)
    assert error.value.status_code == 409


def test_sql_failure_is_not_swallowed_as_unrelated_or_missing(scene):
    c = scene
    failure = RuntimeError('synthetic SQL failure')
    c.db.error = failure
    with pytest.raises(RuntimeError) as error: matches(c)
    assert error.value is failure
