"""Pure protocol and ingest/ledger seams; not HTTP, PG or a business ACK."""
import copy
import hashlib
import sys
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, BooleanClauseList

from generic_agents import http_observation_transport_v177 as transport
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from generic_agents.models import Acquisition, Capability, Entity, Job, Observation, SensorAsset

protocol = transport.protocol
ledger = transport.ledger


def uid(number): return str(UUID(int=number))
P = SimpleNamespace(user_id=uid(1), workspace_id=uid(2), role='operator')
FIELD = 'field_aabbccdd00112233'
TYPE = 'object_aabbccdd00112233'


class Rows:
    def __init__(self): self.rows = []; self.commits = 0
    def add(self, row):
        if row.id is None: row.id = uid(1000 + len(self.rows))
        self.rows.append(row)
    def scalars(self, statement):
        model = statement.column_descriptions[0]['entity']
        def value(row, node):
            if isinstance(node, BindParameter): return node.value
            return getattr(row, node.key)
        def matches(row, node):
            if isinstance(node, BooleanClauseList): return all(matches(row, item) for item in node.clauses)
            assert node.operator is operators.eq
            return value(row, node.left) == value(row, node.right)
        return [row for row in self.rows if type(row) is model and matches(row, statement.whereclause)]
    def scalar(self, statement):
        rows = self.scalars(statement); return rows[0] if rows else None
    def commit(self): self.commits += 1


def capability(identity, kind, data):
    return Capability(id=identity, workspace_id=P.workspace_id, kind=kind, status='published',
        data=copy.deepcopy(data), content_hash=protocol.digest(data), validated_hash=protocol.digest(data),
        namespace='fixture', name=kind, release_version='1.0.0', version=1)


@pytest.fixture
def seam(monkeypatch):
    db = Rows()
    schemas = {TYPE: {'type': 'object', 'properties': {FIELD: {'type': 'number', 'unit': '°C'}}}}
    package = capability(uid(3), 'domain_package', {'specification': {'entity_schemas': schemas}})
    extractor = capability(uid(4), 'tool', {'allowed_agent_roles': ['perception'],
        'execution': {'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False},
        'data_policy': {'deployment': 'local', 'cloud_allowed': False},
        'specification': {'rules': [{'id': 'heat', 'field': FIELD, 'operator': 'gt', 'threshold': 35}]}})
    scope = {'workspace_id': P.workspace_id, 'environment_id': uid(5), 'instance_id': uid(6),
        'release_id': uid(7), 'package_id': package.id, 'package_hash': package.content_hash, 'entity_type': TYPE}
    links = {'temperature_c': {'field_key': FIELD, 'unit': '°C'}}
    configured = {'schema': protocol.CONFIG_SCHEMA, 'scope': scope, 'links': links,
        'supplier_contract': protocol.SUPPLIER, 'time_semantics': protocol.TIME}
    source = capability(uid(8), 'source', {'kind': 'source', 'allowed_agent_roles': ['perception'],
        'execution': {'adapter': 'http', 'side_effect': True, 'endpoint': 'http://fixture/acquisitions',
            'receipt_endpoint': 'http://fixture/acquisitions/receipts'},
        'data_policy': {'simulation': True, 'test_only': True, 'cloud_allowed': False, 'deployment': 'local'},
        'specification': {'simulation_environment_id': uid(5), transport.MAPPING_KEY: configured,
            transport.EXTRACTOR_ID: extractor.id, transport.EXTRACTOR_HASH: extractor.content_hash,
            'observation_capability': {'fields': [FIELD], 'modalities': ['numeric'], 'actions': ['observe']}}})
    entity = Entity(id=uid(9), workspace_id=P.workspace_id, entity_type=TYPE,
        schema_capability_id=package.id, namespace='sim', external_id='对象一', data={})
    asset = SensorAsset(id=uid(10), workspace_id=P.workspace_id, source_id=source.id,
        external_id='sim-weather-01', version=2, data={})
    acq = Acquisition(id=uid(11), workspace_id=P.workspace_id, entity_id=entity.id,
        capability_id=source.id, idempotency_key='sensing:' + uid(11) + ':1', status='dispatching',
        data={'sensor_asset_id': asset.id, 'sensor_asset_version': asset.version, 'actor_id': P.user_id})
    job = Job(id=uid(12), workspace_id=P.workspace_id, kind='dispatch_acquisition', status='running',
        lease_token=uid(13), data={'actor_id': P.user_id, 'acquisition_id': acq.id})
    for row in (package, extractor, source, entity, asset, acq, job): db.add(row)
    payload = {'request_key': acq.idempotency_key, 'target_external_id': entity.external_id,
        'fields': [FIELD], 'max_duration_seconds': 30, 'asset_external_id': asset.external_id,
        'action': 'observe', 'max_cost_minor': 0, 'currency': 'CNY'}
    mapping = protocol.freeze_mapping(source.data, {**scope, 'source_id': source.id}, schemas, links)
    pair = protocol.to_supplier_request(payload, mapping)
    frozen = {'mapping': mapping, **pair, 'parent_pins': {'entity_id': entity.id, 'sensor_asset_id': asset.id},
        'original_request_key': payload['request_key']}
    exchange = Exchange(id=uid(14), workspace_id=P.workspace_id, actor_id=P.user_id,
        acquisition_id=acq.id, source_id=source.id, environment_id=scope['environment_id'],
        instance_id=scope['instance_id'], release_id=scope['release_id'], dispatch_job_id=job.id,
        observation_id=None, status='awaiting_receipt', version=2,
        data={'freeze': frozen, 'freeze_hash': protocol.digest(frozen), 'projection': None,
            'receipt_history': [], 'observation_pin': None, 'latest_receipt_hash': None})
    db.add(exchange)
    api = SimpleNamespace(db=db, source=source, extractor=extractor, package=package, schemas=schemas,
        entity=entity, asset=asset, acq=acq, job=job, payload=payload, mapping=mapping, exchange=exchange,
        calls=[], created=True, process=None, observation=None)
    monkeypatch.setattr(ledger, '_leased', lambda db, p: (api.job, {'job_id': api.job.id, 'lease_token': api.job.lease_token}))
    monkeypatch.setattr(ledger, 'checked_store', lambda db, p, identity, **kwargs: exchange)
    monkeypatch.setattr(ledger, 'lock_current_actor', lambda db, p: None)
    def create(db, p, acquisition_id, job_id, current_mapping, domain, wire):
        api.calls.append(('create', copy.deepcopy(current_mapping), copy.deepcopy(domain), copy.deepcopy(wire)))
        assert acquisition_id == acq.id and job_id == api.job.id
        if api.created: exchange.status = 'prepared'; exchange.version = 1
        return exchange, api.created
    def mark(db, p, identity, version):
        api.calls.append(('mark', identity, version))
        assert identity == exchange.id and version == 1
        exchange.status = 'awaiting_receipt'; exchange.version = 2
        return exchange
    monkeypatch.setattr(ledger, 'create', create)
    monkeypatch.setattr(ledger, 'mark_awaiting_receipt', mark)
    def origin(db, p, identity):
        assert identity == exchange.id
        api.calls.append(('origin', identity))
        if exchange.data['receipt_history'] and exchange.data['receipt_history'][-1]['receipt_origin']['job_id'] == api.job.id:
            return copy.deepcopy(exchange.data['receipt_history'][-1]['receipt_origin'])
        return {'job_id': api.job.id, 'exchange_version': exchange.version,
            'operation': 'original_receipt_get' if api.job.kind == ledger.READ_KIND else 'dispatch_post'}
    def record(db, p, identity, version, raw, *, receipt_origin):
        assert identity == exchange.id and receipt_origin['job_id'] == api.job.id and version == exchange.version
        api.calls.append(('record', version))
        if exchange.data['latest_receipt_hash'] == protocol.digest(raw): return exchange
        projection = protocol.from_supplier_observation(raw, mapping, pair['domain_frozen'], pair['wire_frozen'])
        exchange.data = {**exchange.data, 'projection': projection, 'latest_receipt_hash': protocol.digest(raw),
            'receipt_history': [{'raw': copy.deepcopy(raw), 'evidence_id': uid(15),
                'receipt_origin': copy.deepcopy(receipt_origin)}]}
        exchange.status = ('awaiting_receipt' if projection['status'] == 'waiting' else
            'received_no_data' if projection['status'] == 'no_data' else 'received')
        exchange.version += 1
        return exchange
    monkeypatch.setattr(ledger, 'receipt_origin', origin)
    monkeypatch.setattr(ledger, 'record_receipt', record)
    def ingest(body, p, db):
        api.calls.append(('ingest', body.model_dump(mode='json')))
        original = transport._request(body)
        value = body.model_dump(mode='json', exclude={'source_id', 'source_record_id', 'processing_version',
            'entity_id', 'observed_at', 'business_day'})
        observation = Observation(id=uid(20), workspace_id=p.workspace_id, source_id=body.source_id,
            source_record_id=body.source_record_id, processing_version=body.processing_version,
            entity_id=body.entity_id, observed_at=datetime.fromisoformat(body.observed_at),
            business_day=body.business_day, status='received',
            data={**value, 'original_request': original, 'input_hash': protocol.digest(original), 'submitted_by': p.user_id})
        process = Job(id=uid(21), workspace_id=p.workspace_id, kind='process_observation', status='queued',
            attempts=0, idempotency_key='extract:' + observation.id,
            data={'observation_id': observation.id, 'actor_id': p.user_id})
        db.add(observation); db.add(process)
        api.observation, api.process = observation, process
        return {'id': observation.id}
    monkeypatch.setattr(transport, '_ingest', ingest)
    return api


def packet(api, *, status='completed', observed='2026-10-23T23:30:00+00:00'):
    frozen = api.exchange.data['freeze']; wire = frozen['wire_frozen']; request = wire['request']
    signature = hashlib.sha256(request['request_key'].encode()).hexdigest()
    command_key, record_id = 'acq-v176:' + signature, 'sim-acq-v176:' + signature
    supplier = {**protocol.MARK, 'request_id': command_key, 'device_id': request['asset_external_id'],
        'action': 'read_telemetry', 'status': 'succeeded', 'device_revision': 1,
        'result': {**protocol.MARK, 'id': request['asset_external_id'], 'revision': 1,
            'sampled_at': observed, 'state': {'temperature_c': 38},
            'time_semantics': protocol.TIME, 'physical_device_connected': False}}
    obs = {**protocol.MARK, 'schema': 'sim-telemetry-observation-v176', 'source_record_id': record_id,
        'request_key': request['request_key'], 'device_command_key': command_key,
        'asset_external_id': request['asset_external_id'], 'target_external_id': request['target_external_id'],
        'attributes': {'temperature_c': 38}, 'field_units': {'temperature_c': '°C'}, 'observed_at': observed,
        'time_semantics': protocol.TIME, 'source_time_verified': False, 'physical_device_connected': False,
        'image_evidence_ids': [], 'supplier_receipt_hash': protocol.digest(supplier)}
    return {**protocol.MARK, 'schema': protocol.SUPPLIER, 'request_key': request['request_key'],
        'asset_external_id': request['asset_external_id'], 'target_external_id': request['target_external_id'],
        'receipt_id': record_id, 'status': status, 'raw_device_status': 'running' if status == 'accepted' else 'succeeded',
        'terminal': status != 'accepted', 'no_future_device_activity': status != 'accepted',
        'no_future_observation': status != 'accepted', 'request_body_sha256': wire['body_sha256'],
        'request_hash': wire['request_hash'], 'device_command_key': command_key,
        'device_command_sha256': protocol.digest({'device_id': request['asset_external_id'],
            'action': 'read_telemetry', 'expected_revision': None, 'arguments': {}}),
        'cost_minor': 0, 'currency': 'CNY', 'artifacts': [], 'observation': obs if status == 'completed' else None,
        'observation_available': status == 'completed', 'source_time_verified': False,
        'field_observation_created': False, 'supplier_receipt': supplier}


def reseal(cap): cap.content_hash = cap.validated_hash = protocol.digest(cap.data)


def test_prepare_rebuilds_actual_domain_schema_and_preserves_exact_supplier_bytes(seam):
    original = copy.deepcopy(seam.payload)
    row, created = transport.prepare_dispatch(seam.db, P, seam.acq, seam.source, seam.asset, seam.payload)
    assert created is True and row.status == 'awaiting_receipt' and row.version == 2
    call = next(item for item in seam.calls if item[0] == 'create')
    assert call[1] == seam.mapping and call[2]['request'] == original
    assert call[3]['request']['fields'] == ['temperature_c']
    assert call[3]['body'] == protocol.canonical(call[3]['request'])
    assert seam.payload == original and seam.db.commits == 0
    assert [item[0] for item in seam.calls] == ['create', 'mark']


def test_existing_exchange_is_lookup_only_and_never_remarked(seam):
    seam.created = False
    state = copy.deepcopy(seam.exchange.data)
    row, created = transport.prepare_dispatch(seam.db, P, seam.acq, seam.source, seam.asset, seam.payload)
    assert row is seam.exchange and created is False and row.data == state
    assert [item[0] for item in seam.calls] == ['create']


def test_numeric_rules_legacy_two_key_execution_is_also_local_readonly(seam):
    del seam.extractor.data['execution']['side_effect']; reseal(seam.extractor)
    seam.source.data['specification'][transport.EXTRACTOR_HASH] = seam.extractor.content_hash
    reseal(seam.source)
    result = transport.prepare_dispatch(seam.db, P, seam.acq, seam.source, seam.asset, seam.payload)
    assert result[1] is True


@pytest.mark.parametrize('execution', [
    {'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': True},
    {'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': 0},
    {'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False, 'endpoint': 'http://fixture'},
])
def test_numeric_tool_side_effect_type_and_closed_execution_are_not_coerced(seam, execution):
    seam.extractor.data['execution'] = execution; reseal(seam.extractor)
    seam.source.data['specification'][transport.EXTRACTOR_HASH] = seam.extractor.content_hash
    reseal(seam.source)
    with pytest.raises(HTTPException):
        transport.prepare_dispatch(seam.db, P, seam.acq, seam.source, seam.asset, seam.payload)
    assert not seam.calls


@pytest.mark.parametrize('damage', ['field', 'operator', 'bool', 'infinite', 'unit', 'not_list', 'oversize'])
def test_numeric_rule_fields_units_and_finite_conditions_are_checked(seam, damage):
    rules = seam.extractor.data['specification']['rules']
    if damage == 'field': rules[0]['field'] = 'temperature_c'
    if damage == 'operator': rules[0]['operator'] = 'gte'
    if damage == 'bool': rules[0]['threshold'] = True
    if damage == 'infinite':
        # Keep a valid JSON manifest while testing the numeric-rule magnitude
        # bound; NaN/Infinity cannot even acquire a valid published typed hash.
        rules[0]['threshold'] = 1e16
    if damage == 'unit': rules[0]['unit'] = 'K'
    if damage == 'not_list': seam.extractor.data['specification']['rules'] = {}
    if damage == 'oversize': seam.extractor.data['specification']['rules'] = [copy.deepcopy(rules[0])] * 65
    reseal(seam.extractor)
    seam.source.data['specification'][transport.EXTRACTOR_HASH] = seam.extractor.content_hash
    reseal(seam.source)
    with pytest.raises(HTTPException):
        transport.prepare_dispatch(seam.db, P, seam.acq, seam.source, seam.asset, seam.payload)
    assert not seam.calls


@pytest.mark.parametrize('denied', [False, True])
def test_managed_numeric_rule_requires_original_current_approval(seam, monkeypatch, denied):
    checked = []
    def current(db, p, cap):
        checked.append(cap.id)
        if denied: raise HTTPException(409, 'current managed approval absent')
    monkeypatch.setitem(sys.modules, 'generic_agents.local_rules_api', SimpleNamespace(require_current_check=current))
    seam.extractor.namespace = 'platform_numeric_rules'
    spec = seam.extractor.data['specification']
    spec['managed_numeric_rule'] = {'package_id': seam.package.id, 'entity_type': TYPE}
    spec['rules'][0]['unit'] = '°C'; reseal(seam.extractor)
    seam.source.data['specification'][transport.EXTRACTOR_HASH] = seam.extractor.content_hash
    reseal(seam.source)
    if denied:
        with pytest.raises(HTTPException):
            transport.prepare_dispatch(seam.db, P, seam.acq, seam.source, seam.asset, seam.payload)
        assert not seam.calls
    else:
        assert transport.prepare_dispatch(seam.db, P, seam.acq, seam.source, seam.asset, seam.payload)[1] is True
    assert checked == [seam.extractor.id]


def test_only_absent_mapping_can_use_legacy_central_path(seam):
    del seam.source.data['specification'][transport.MAPPING_KEY]; reseal(seam.source)
    assert transport.prepare_dispatch(seam.db, P, seam.acq, seam.source, seam.asset, seam.payload) is None
    assert seam.calls == []


@pytest.mark.parametrize('damage', ['null', 'scope', 'schema_unit', 'asset', 'pin_missing', 'pin_hash', 'remote'])
def test_invalid_new_source_never_falls_back_or_creates_dispatch(seam, damage):
    if damage == 'null': seam.source.data['specification'][transport.MAPPING_KEY] = None
    if damage == 'scope': seam.source.data['specification'][transport.MAPPING_KEY]['scope']['workspace_id'] = uid(99)
    if damage == 'schema_unit':
        seam.package.data['specification']['entity_schemas'][TYPE]['properties'][FIELD]['unit'] = 'K'
        reseal(seam.package)
    if damage == 'asset': seam.asset.version += 1
    if damage == 'pin_missing': del seam.source.data['specification'][transport.EXTRACTOR_ID]
    if damage == 'pin_hash': seam.source.data['specification'][transport.EXTRACTOR_HASH] = 'a' * 64
    if damage == 'remote': seam.extractor.data['data_policy']['cloud_allowed'] = True; reseal(seam.extractor)
    reseal(seam.source)
    with pytest.raises(HTTPException):
        transport.prepare_dispatch(seam.db, P, seam.acq, seam.source, seam.asset, seam.payload)
    assert not seam.calls


def test_success_receipt_uses_normal_ingest_and_waits_for_actual_parent_success(seam):
    raw = packet(seam); result = transport.receive(seam.db, P, seam.exchange.id, raw)
    observation, process = seam.observation, seam.process
    assert result.observation_id == observation.id and result.status == 'received'
    assert observation.source_record_id == 'http-acquisition:' + result.id
    assert result.data['projection']['source_record_id'] == raw['observation']['source_record_id']
    assert result.data['observation_pin']['projection_hash'] == protocol.digest(result.data['projection'])
    assert observation.processing_version == transport.PROCESSING_VERSION
    assert observation.data['original_request']['observed_at'] == '2026-10-23T23:30:00+00:00'
    assert observation.business_day == '2026-10-24'
    assert observation.data['attributes'] == {FIELD: 38} and type(observation.data['attributes'][FIELD]) is int
    assert observation.data['field_units'] == {FIELD: '°C'}
    assert observation.data['http_mapped_origin_v177'] == {'schema': transport.ORIGIN_SCHEMA, 'exchange_id': result.id}
    for key, value in ledger.MARK.items(): assert observation.data[key] is value or observation.data[key] == value
    assert observation.data['physical_device_connected'] is False and observation.data['source_time_verified'] is False
    assert observation.data['date_basis'] == protocol.TIME
    assert observation.data['evidence_ids'] == [] and 'evidence_ids' not in observation.data['original_request']
    assert observation.data['http_mapped_receipt_evidence_id_v177'] == result.data['receipt_history'][-1]['evidence_id']
    assert process.kind == transport.PROCESS and process.status == 'waiting_parent' and process.attempts == 0
    assert process.data['http_mapped_parent_job_id'] == seam.job.id
    assert process.data['http_mapped_exchange_id'] == result.id
    assert seam.job.status == 'running' and seam.acq.status == 'dispatching'
    assert [item[0] for item in seam.calls] == ['origin', 'record', 'ingest']
    assert seam.db.commits == 0


def test_explicit_receipt_get_is_the_child_parent_not_the_old_dispatch_job(seam):
    original = seam.job
    seam.job = Job(id=uid(50), workspace_id=P.workspace_id, kind=ledger.READ_KIND, status='running',
        lease_token=uid(51), data={'actor_id': P.user_id, 'exchange_id': seam.exchange.id})
    seam.db.add(seam.job)
    transport.receive(seam.db, P, seam.exchange.id, packet(seam))
    assert seam.exchange.dispatch_job_id == original.id
    assert seam.process.data['http_mapped_parent_job_id'] == seam.job.id
    assert original.status == 'running' and seam.process.status == 'waiting_parent'


@pytest.mark.parametrize('status,observed', [('accepted', '2026-10-23T00:00:00+00:00'),
    ('completed_no_data', '2026-10-23T00:00:00+00:00'), ('completed', None), ('completed', '2026-10-23T00:00:00')])
def test_pending_no_data_or_unknown_time_never_becomes_a_domain_observation(seam, status, observed):
    result = transport.receive(seam.db, P, seam.exchange.id, packet(seam, status=status, observed=observed))
    assert result.observation_id is None and seam.observation is None and seam.process is None
    assert not any(item[0] == 'ingest' for item in seam.calls)
    assert seam.acq.status == 'dispatching'


def test_ownership_rejection_precedes_any_receipt_or_ingest(seam, monkeypatch):
    def denied(*args): raise HTTPException(409, 'no actual owned lease')
    monkeypatch.setattr(ledger, 'receipt_origin', denied)
    with pytest.raises(HTTPException): transport.receive(seam.db, P, seam.exchange.id, packet(seam))
    assert seam.calls == [] and seam.observation is None


def test_changed_source_cannot_lend_new_extractor_to_old_exchange(seam):
    seam.source.data['description'] = 'a different published source'; reseal(seam.source)
    with pytest.raises(HTTPException): transport.receive(seam.db, P, seam.exchange.id, packet(seam))
    assert seam.observation is None and not any(item[0] == 'ingest' for item in seam.calls)


def test_exact_original_receipt_never_reingests_or_resets_finished_child(seam):
    raw = packet(seam); transport.receive(seam.db, P, seam.exchange.id, raw)
    seam.process.status = 'succeeded'; seam.process.attempts = 1; seam.observation.status = 'extracted'
    original = copy.deepcopy(seam.observation.data)
    transport.receive(seam.db, P, seam.exchange.id, raw)
    assert len([item for item in seam.calls if item[0] == 'ingest']) == 1
    assert seam.observation.data == original and seam.process.status == 'succeeded'
    assert len([row for row in seam.db.rows if type(row) is Observation]) == 1


@pytest.mark.parametrize('damage', ['body_type', 'origin', 'parent', 'unit'])
def test_existing_observation_cannot_be_relabelled_or_silently_recreated(seam, damage):
    raw = packet(seam); transport.receive(seam.db, P, seam.exchange.id, raw)
    if damage == 'body_type': seam.observation.data['original_request']['attributes'][FIELD] = 38.0
    if damage == 'origin': seam.observation.data['http_mapped_origin_v177']['exchange_id'] = uid(99)
    if damage == 'parent': seam.process.data['http_mapped_parent_job_id'] = uid(99)
    if damage == 'unit': seam.observation.data['field_units'][FIELD] = 'K'
    with pytest.raises(HTTPException): transport.receive(seam.db, P, seam.exchange.id, raw)
    assert len([item for item in seam.calls if item[0] == 'ingest']) == 1


def test_reused_ingest_result_with_started_job_is_not_reset(seam, monkeypatch):
    ingest = transport._ingest
    def started(body, p, db):
        result = ingest(body, p, db); seam.process.status = 'running'; seam.process.attempts = 1
        return result
    monkeypatch.setattr(transport, '_ingest', started)
    with pytest.raises(HTTPException): transport.receive(seam.db, P, seam.exchange.id, packet(seam))
    assert seam.process.kind == 'process_observation' and seam.process.status == 'running'
    assert seam.exchange.observation_id is None and 'http_mapped_origin_v177' not in seam.observation.data


def test_normal_ingest_signature_keeps_parent_transaction_open(monkeypatch):
    body, db = object(), object()
    received = []
    def ingest(actual_body, p, actual_db, *, commit):
        received.append((actual_body, p, actual_db, commit))
        return {'id': uid(90)}
    monkeypatch.setitem(sys.modules, 'generic_agents.api', SimpleNamespace(ingest_observation_record=ingest))
    assert transport._ingest(body, P, db) == {'id': uid(90)}
    assert received == [(body, P, db, False)]


def test_saved_receipt_mode_checks_exact_owned_historical_proof_and_does_not_record_again(seam):
    raw = packet(seam)
    origin = ledger.receipt_origin(seam.db, P, seam.exchange.id)
    ledger.record_receipt(seam.db, P, seam.exchange.id, seam.exchange.version, raw, receipt_origin=origin)
    records = len([item for item in seam.calls if item[0] == 'record'])
    transport.receive(seam.db, P, seam.exchange.id, raw, saved_receipt=True)
    assert len([item for item in seam.calls if item[0] == 'record']) == records
    assert seam.exchange.observation_id == seam.observation.id


@pytest.mark.parametrize('damage', ['missing', 'raw', 'origin', 'flag_type'])
def test_saved_receipt_flag_is_not_a_bypass_for_unproven_inputs(seam, damage, monkeypatch):
    raw = packet(seam)
    origin = ledger.receipt_origin(seam.db, P, seam.exchange.id)
    ledger.record_receipt(seam.db, P, seam.exchange.id, seam.exchange.version, raw, receipt_origin=origin)
    if damage == 'missing': seam.exchange.data['receipt_history'] = []
    if damage == 'raw': raw['extra'] = 'a different original'
    if damage == 'origin':
        monkeypatch.setattr(ledger, 'receipt_origin', lambda *args: {**origin, 'job_id': uid(99)})
    with pytest.raises(HTTPException):
        transport.receive(seam.db, P, seam.exchange.id, raw, saved_receipt=1 if damage == 'flag_type' else True)
    assert seam.observation is None
