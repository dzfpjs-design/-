"""Pure state/protocol/ORM seams, not a real PG/transport/business journey."""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, BooleanClauseList

from generic_agents import http_observation_exchange_v177 as store
from generic_agents import evidence, leases
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from generic_agents.models import Acquisition, Audit, Job, Observation


def uid(value): return str(UUID(int=value))
P = SimpleNamespace(user_id=uid(1), workspace_id=uid(2), role='operator', session_id='exchange-seam')
F = 'field_1234567890abcdef'
TYPE = 'object_1234567890abcdef'


def protocol_freeze():
    scope = {'workspace_id': P.workspace_id, 'environment_id': uid(3), 'instance_id': uid(4),
        'release_id': uid(5), 'package_id': uid(6), 'package_hash': 'a' * 64, 'entity_type': TYPE}
    links = {'temperature_c': {'field_key': F, 'unit': '°C'}}
    configured = {'schema': store.protocol.CONFIG_SCHEMA, 'scope': scope, 'links': links,
        'supplier_contract': store.protocol.SUPPLIER, 'time_semantics': store.protocol.TIME}
    source = {'kind': 'source', 'allowed_agent_roles': ['perception'],
        'execution': {'adapter': 'http', 'side_effect': True, 'endpoint': 'http://fixture/acquisitions',
            'receipt_endpoint': 'http://fixture/acquisitions/receipts'},
        'data_policy': {'simulation': True, 'test_only': True, 'cloud_allowed': False, 'deployment': 'local'},
        'specification': {'simulation_environment_id': uid(3), 'http_observation_mapping_v177': configured,
            'observation_capability': {'fields': [F], 'modalities': ['numeric'], 'actions': ['observe']}}}
    schemas = {TYPE: {'type': 'object', 'properties': {F: {'type': 'number', 'unit': '°C'}}}}
    return store.protocol.freeze_mapping(source, {**scope, 'source_id': uid(7)}, schemas, links)


def fixture_row():
    mapping = protocol_freeze()
    request = {'request_key': 'sensing:' + uid(8) + ':1', 'target_external_id': '目标一',
        'fields': [F], 'max_duration_seconds': 30, 'asset_external_id': 'sim-weather-01',
        'action': 'observe', 'max_cost_minor': 0, 'currency': 'CNY'}
    pair = store.protocol.to_supplier_request(request, mapping)
    frozen = {'schema': 'http-observation-exchange-freeze-v177', 'mapping': mapping, **pair,
        'original_request_key': request['request_key'],
        'parent_pins': {'entity_id': uid(9), 'sensor_asset_id': uid(10)},
        'dispatch_identity': {'job_id': uid(11), 'kind': 'dispatch_acquisition',
            'idempotency_key': 'dispatch:' + uid(12), 'actor_id': P.user_id, 'lease_token': uid(13)}}
    return Exchange(id=uid(14), workspace_id=P.workspace_id, actor_id=P.user_id,
        acquisition_id=uid(12), source_id=uid(7), environment_id=uid(3), instance_id=uid(4),
        release_id=uid(5), dispatch_job_id=uid(11), observation_id=None, version=1, status='prepared',
        data={'schema': store.SCHEMA, **store.MARK, 'freeze': frozen, 'freeze_hash': store.digest(frozen),
            'receipt_history': [], 'latest_receipt_hash': None, 'projection': None,
            'observation_pin': None, 'read_jobs': [], 'active_read_job_id': None})


def packet(row, *, status='completed', observed='2026-10-23T12:00:00+08:00'):
    frozen = row.data['freeze']; wire = frozen['wire_frozen']; request = wire['request']
    import hashlib
    signature = hashlib.sha256(request['request_key'].encode()).hexdigest()
    command_key, record_id = 'acq-v176:' + signature, 'sim-acq-v176:' + signature
    supplier = {**store.protocol.MARK, 'request_id': command_key,
        'device_id': 'sim-weather-01', 'action': 'read_telemetry', 'status': 'succeeded', 'device_revision': 1,
        'result': {**store.protocol.MARK, 'id': 'sim-weather-01', 'revision': 1,
            'sampled_at': observed, 'state': {'temperature_c': 38},
            'time_semantics': store.protocol.TIME, 'physical_device_connected': False}}
    obs = {**store.protocol.MARK, 'schema': 'sim-telemetry-observation-v176',
        'source_record_id': record_id, 'request_key': request['request_key'], 'device_command_key': command_key,
        'asset_external_id': request['asset_external_id'], 'target_external_id': request['target_external_id'],
        'attributes': {'temperature_c': 38}, 'field_units': {'temperature_c': '°C'}, 'observed_at': observed,
        'time_semantics': store.protocol.TIME, 'source_time_verified': False,
        'physical_device_connected': False, 'image_evidence_ids': [], 'supplier_receipt_hash': store.digest(supplier)}
    return {**store.protocol.MARK, 'schema': store.protocol.SUPPLIER,
        'request_key': request['request_key'], 'asset_external_id': request['asset_external_id'],
        'target_external_id': request['target_external_id'], 'receipt_id': record_id, 'status': status,
        'raw_device_status': 'running' if status == 'accepted' else 'succeeded',
        'terminal': status != 'accepted', 'no_future_device_activity': status != 'accepted',
        'no_future_observation': status != 'accepted', 'request_body_sha256': wire['body_sha256'],
        'request_hash': wire['request_hash'], 'device_command_key': command_key,
        'device_command_sha256': store.digest({'device_id': 'sim-weather-01', 'action': 'read_telemetry',
            'expected_revision': None, 'arguments': {}}), 'cost_minor': 0, 'currency': 'CNY', 'artifacts': [],
        'observation': obs if status == 'completed' else None, 'observation_available': status == 'completed',
        'source_time_verified': False, 'field_observation_created': False, 'supplier_receipt': supplier}


class Rows:
    def __init__(self): self.rows = []; self.statements = []; self.on_scalars = None; self.commits = 0
    def add(self, row):
        if row.id is None: row.id = uid(1000 + len(self.rows))
        self.rows.append(row)
    def scalars(self, statement):
        if self.on_scalars:
            callback, self.on_scalars = self.on_scalars, None; callback()
        model = statement.column_descriptions[0]['entity']
        def value(row, node):
            if isinstance(node, BindParameter): return node.value
            if getattr(node, 'operator', None) is operators.json_getitem_op:
                return value(row, node.left).get(value(row, node.right))
            return getattr(row, node.key)
        def matches(row, node):
            if isinstance(node, BooleanClauseList): return all(matches(row, item) for item in node.clauses)
            left, right = value(row, node.left), value(row, node.right)
            if node.operator is operators.in_op: return left in right
            assert node.operator is operators.eq
            return left == right
        return [row for row in self.rows if type(row) is model and matches(row, statement.whereclause)]
    def scalar(self, statement):
        rows = self.scalars(statement); return rows[0] if rows else None
    def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        if getattr(statement, 'column_descriptions', None):
            row = next(item for item in self.rows if type(item) is Exchange)
            ids = [item.id for item in self.rows if type(item) is Audit and item.action == store.ACTION and item.object_id == row.id]
            return SimpleNamespace(first=lambda: (copy.deepcopy(row), ids))
        return None
    def commit(self): self.commits += 1


def anchor(db, row, operation='create', previous=None):
    store._record(db, P, row, operation, previous)
    return next(item for item in reversed(db.rows) if type(item) is Audit)


def test_state_requires_an_independent_complete_typed_chain():
    row = fixture_row(); db = Rows(); first = anchor(db, row)
    state = store.snapshot(row)
    assert store.verify_authority(state, [first.data]) == store.digest(state)
    previous = store.digest(state); row.status = 'awaiting_receipt'; row.version += 1
    second = anchor(db, row, 'await_original_receipt', previous)
    assert store.verify_authority(store.snapshot(row), [second.data, first.data]) == store.digest(store.snapshot(row))
    row.status = 'received'  # row-only re-sealing cannot claim a supplier result
    with pytest.raises(HTTPException): store.verify_authority(store.snapshot(row), [first.data, second.data])


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'previous', 'value_type', 'workspace', 'actor'])
def test_omitted_forked_or_relabelled_state_never_restores_authority(damage):
    row = fixture_row(); db = Rows(); first = anchor(db, row)
    previous = store.digest(store.snapshot(row)); row.status = 'awaiting_receipt'; row.version = 2
    second = anchor(db, row, 'await_original_receipt', previous)
    values = copy.deepcopy([first.data, second.data]); state = store.snapshot(row)
    if damage == 'missing': values.pop(0)
    if damage == 'duplicate': values[1] = copy.deepcopy(values[0])
    if damage == 'previous': values[1]['previous_state_hash'] = 'b' * 64
    if damage == 'value_type': state['data']['freeze']['wire_frozen']['request']['max_cost_minor'] = 0.0
    if damage == 'workspace': state['workspace_id'] = uid(99)
    if damage == 'actor': values[1]['actor_id'] = uid(99)
    with pytest.raises(HTTPException): store.verify_authority(state, values)


def test_read_captures_row_and_audit_inventory_atomically_without_readonly_row_locks():
    row = fixture_row(); db = Rows(); db.add(row); anchor(db, row)
    actual = store.checked_store(db, P, row.id)
    assert actual.version == 1
    statement = db.statements[0]
    assert statement._for_update_arg is None
    assert statement.get_execution_options()['populate_existing'] is True
    assert len(statement.column_descriptions) == 2


def test_read_does_not_report_legal_concurrent_next_version_as_corruption():
    row = fixture_row(); db = Rows(); db.add(row); anchor(db, row)
    def concurrent_commit():
        previous = store.digest(store.snapshot(row)); row.status = 'awaiting_receipt'; row.version = 2
        anchor(db, row, 'await_original_receipt', previous)
    db.on_scalars = concurrent_commit
    captured = store.checked_store(db, P, row.id)
    assert captured.version == 1 and row.version == 2


def test_rollback_row_cannot_hide_a_later_independent_audit_even_in_reader():
    row = fixture_row(); db = Rows(); db.add(row); anchor(db, row)
    previous = store.digest(store.snapshot(row)); row.status = 'awaiting_receipt'; row.version = 2
    anchor(db, row, 'await_original_receipt', previous)
    row.status = 'prepared'; row.version = 1
    with pytest.raises(HTTPException): store.checked_store(db, P, row.id)


@pytest.fixture
def seam(monkeypatch):
    row = fixture_row(); row.status = 'awaiting_receipt'; row.version = 2
    db = Rows(); db.add(row)
    job = Job(id=row.dispatch_job_id, workspace_id=P.workspace_id, status='running',
        kind='dispatch_acquisition', lease_token=uid(13), idempotency_key='dispatch:' + row.acquisition_id,
        data={'actor_id': P.user_id, 'acquisition_id': row.acquisition_id})
    db.add(job)
    api = SimpleNamespace(row=row, db=db, job=job, evidence={})
    monkeypatch.setattr(store, 'checked_store', lambda db, p, identity, **kwargs: row)
    monkeypatch.setattr(store, 'lock_current_actor', lambda db, p: None)
    monkeypatch.setattr(leases, 'ownership', lambda db: {'job_id': api.job.id, 'lease_token': api.job.lease_token})
    monkeypatch.setattr(leases, 'checkpoint', lambda db: db.commit())
    def save(db, p, raw, media_type, metadata):
        import hashlib
        result = SimpleNamespace(id=uid(2000 + len(api.evidence)), workspace_id=p.workspace_id,
            media_type=media_type, sha256=hashlib.sha256(raw).hexdigest(), data=metadata)
        api.evidence[result.id] = result, bytes(raw)
        return result
    monkeypatch.setattr(evidence, 'save_immutable', save)
    return api


def test_record_uses_actual_owned_dispatch_receipt_and_never_settles_business(seam):
    row, db = seam.row, seam.db; raw = packet(row)
    origin = store.receipt_origin(db, P, row.id)
    result = store.record_receipt(db, P, row.id, 2, raw, receipt_origin=origin)
    assert result.status == 'received' and result.version == 3 and result.observation_id is None
    entry = result.data['receipt_history'][0]
    assert entry['raw'] == raw and entry['raw_hash'] == store.digest(raw)
    assert entry['projection']['mapped_attributes'] == {F: 38}
    assert entry['receipt_origin']['lease_token'] == uid(13)
    ev, original = seam.evidence[entry['evidence_id']]
    assert original == store.protocol.canonical(raw).encode('utf-8')
    assert ev.data['native_http_bytes'] is False
    assert db.commits == 1
    receipt_audit = next(item for item in db.rows if type(item) is Audit)
    assert receipt_audit.data['state_hash'] == store.digest(store.snapshot(row))
    assert not any(type(item) is Acquisition for item in db.rows)


@pytest.mark.parametrize('change', ['token', 'job', 'actor', 'key', 'version'])
def test_caller_supplied_origin_is_not_receipt_authority(seam, change):
    origin = store.receipt_origin(seam.db, P, seam.row.id)
    origin[{'token': 'lease_token', 'job': 'job_id', 'actor': 'actor_id', 'key': 'original_request_key',
        'version': 'exchange_version'}[change]] = uid(99)
    with pytest.raises(HTTPException):
        store.record_receipt(seam.db, P, seam.row.id, 2, packet(seam.row), receipt_origin=origin)
    assert seam.row.version == 2 and not seam.evidence


def test_expired_lease_cannot_freeze_a_late_success(seam, monkeypatch):
    origin = store.receipt_origin(seam.db, P, seam.row.id)
    def lost(db): raise leases.LeaseLost('expired')
    monkeypatch.setattr(leases, 'checkpoint', lost)
    with pytest.raises(leases.LeaseLost):
        store.record_receipt(seam.db, P, seam.row.id, 2, packet(seam.row), receipt_origin=origin)
    assert seam.row.version == 2 and seam.evidence == {}


@pytest.mark.parametrize('status,expected', [('accepted', 'awaiting_receipt'), ('completed_no_data', 'received_no_data')])
def test_pending_or_no_data_never_creates_an_observation_or_completes_gap(seam, status, expected):
    origin = store.receipt_origin(seam.db, P, seam.row.id)
    store.record_receipt(seam.db, P, seam.row.id, 2, packet(seam.row, status=status), receipt_origin=origin)
    assert seam.row.status == expected and seam.row.observation_id is None
    assert seam.row.data['projection']['mapped_attributes'] == {}


def test_terminal_result_cannot_be_overwritten_and_exact_receipt_is_read_only(seam):
    row, db = seam.row, seam.db; raw = packet(row)
    store.record_receipt(db, P, row.id, 2, raw, receipt_origin=store.receipt_origin(db, P, row.id))
    origin = store.receipt_origin(db, P, row.id)
    store.record_receipt(db, P, row.id, 3, raw, receipt_origin=origin)
    assert row.version == 3 and len(seam.evidence) == 1
    changed = copy.deepcopy(raw); changed['extra'] = 'not the original terminal package'
    with pytest.raises(HTTPException): store.record_receipt(db, P, row.id, 3, changed, receipt_origin=origin)


def test_read_query_is_explicit_cas_owned_and_has_independent_enqueue_audit(seam):
    row, db = seam.row, seam.db
    row, reader = store.enqueue_receipt_read(db, P, row.id, 2, uid(30))
    assert reader.kind == store.READ_KIND and reader.status == 'queued' and row.version == 3
    assert reader.data['original_request_key'] == row.data['freeze']['original_request_key']
    reader.status = 'running'; reader.lease_token = uid(31); seam.job = reader
    origin = store.receipt_origin(db, P, row.id)
    assert origin['operation'] == 'original_receipt_get' and origin['read_enqueue_audit_id']
    store.record_receipt(db, P, row.id, 3, packet(row), receipt_origin=origin)
    assert row.status == 'received' and row.version == 4
    # The actual GET remains bound to its original enqueued version after its
    # parsed receipt was durably saved; continuing never edits that instruction.
    original_instruction = copy.deepcopy(reader.data)
    saved_origin = store.receipt_origin(db, P, row.id)
    assert saved_origin == origin and saved_origin['exchange_version'] == 3
    store.record_receipt(db, P, row.id, 4, packet(row), receipt_origin=saved_origin)
    assert reader.data == original_instruction and row.version == 4 and len(seam.evidence) == 1
    # Historical reader may be another workspace member; provenance author is
    # always original dispatch actor, rather than whoever views the record.
    reader.status = 'succeeded'
    other = SimpleNamespace(**{**vars(P), 'user_id': uid(99)})
    store._saved_receipt_origin(db, other, row, row.data['receipt_history'][0]['receipt_origin'])
    db.rows = [item for item in db.rows if not (type(item) is Audit and item.action == store.READ_AUDIT)]
    with pytest.raises(HTTPException):
        store._saved_receipt_origin(db, other, row, row.data['receipt_history'][0]['receipt_origin'])


def test_saved_receipt_cannot_be_borrowed_by_a_new_lease_or_relabelled_get_version(seam):
    row, db = seam.row, seam.db
    row, reader = store.enqueue_receipt_read(db, P, row.id, 2, uid(30))
    reader.status = 'running'; reader.lease_token = uid(31); seam.job = reader
    origin = store.receipt_origin(db, P, row.id)
    store.record_receipt(db, P, row.id, row.version, packet(row), receipt_origin=origin)
    reader.lease_token = uid(99)
    with pytest.raises(HTTPException): store.receipt_origin(db, P, row.id)
    reader.lease_token = uid(31)
    reader.data = {**reader.data, 'exchange_version': row.version}
    with pytest.raises(HTTPException): store.receipt_origin(db, P, row.id)


def test_attaching_existing_clocked_observation_preserves_raw_time_and_never_claims_ack(seam):
    row, db = seam.row, seam.db
    store.record_receipt(db, P, row.id, 2, packet(row), receipt_origin=store.receipt_origin(db, P, row.id))
    projection = row.data['projection']
    body = {'source_id': row.source_id, 'source_record_id': 'http-acquisition:' + row.id,
        'entity_id': uid(9), 'observed_at': projection['observed_at'], 'attributes': {F: 38},
        'acquisition_id': row.acquisition_id, 'sensor_asset_id': uid(10),
        'sensor_modality': 'numeric', 'startup_mode': 'automatic'}
    obs = Observation(id=uid(40), workspace_id=P.workspace_id, source_id=row.source_id,
        entity_id=uid(9), source_record_id=body['source_record_id'], processing_version='http-v177',
        observed_at=datetime(2026, 10, 23, 4, tzinfo=timezone.utc), status='received',
        data={'original_request': body, 'input_hash': store.digest(body), 'submitted_by': P.user_id,
            'attributes': {F: 38}, 'acquisition_id': row.acquisition_id,
            'sensor_asset_id': uid(10), 'sensor_modality': 'numeric'})
    db.add(obs)
    store.attach_observation(db, P, row.id, 3, obs.id)
    assert row.observation_id == obs.id and row.status == 'received' and row.version == 4
    assert obs.status == 'received'  # not extracted/ACK/gap complete
    assert row.data['observation_pin']['original_request']['observed_at'] == '2026-10-23T12:00:00+08:00'
    assert row.data['projection']['source_record_id'] == packet(row)['observation']['source_record_id']
    assert row.data['observation_pin']['projection_hash'] == store.digest(row.data['projection'])


def test_unknown_timestamp_cannot_attach_even_when_numeric_values_are_valid(seam):
    row, db = seam.row, seam.db
    store.record_receipt(db, P, row.id, 2, packet(row, observed=None), receipt_origin=store.receipt_origin(db, P, row.id))
    assert row.status == 'received' and row.data['projection']['status'] == 'unclocked'
    with pytest.raises(HTTPException): store.attach_observation(db, P, row.id, 3, uid(99))
    assert row.observation_id is None


def test_explicit_get_resumes_saved_receipt_without_rewriting_original_source(seam):
    row, db = seam.row, seam.db; raw = packet(row)
    store.record_receipt(db, P, row.id, row.version, raw, receipt_origin=store.receipt_origin(db, P, row.id))
    original = copy.deepcopy(row.data['receipt_history'][0])
    old_job = seam.job; old_job.status = 'failed'
    old_identity = (old_job.status, old_job.lease_token, copy.deepcopy(old_job.data))
    row, reader = store.enqueue_receipt_read(db, P, row.id, row.version, uid(50))
    pin = reader.data[store.RESUME]
    assert pin == {'schema': store.RESUME_SCHEMA, 'receipt_hash': original['raw_hash'],
        'projection_hash': original['projection_hash'], 'receipt_origin': original['receipt_origin']}
    assert row.status == 'received' and row.observation_id is None
    assert store.RESUME in store._read_instruction(reader)
    reader.status = 'running'; reader.lease_token = uid(51); seam.job = reader
    origin = store.receipt_origin(db, P, row.id)
    store.record_receipt(db, P, row.id, row.version, raw, receipt_origin=origin)
    assert len(row.data['receipt_history']) == 2 and row.data['receipt_history'][0] == original
    new = row.data['receipt_history'][1]
    assert new['raw_hash'] == original['raw_hash'] and new['projection_hash'] == original['projection_hash']
    assert new['raw'] == original['raw'] and new['evidence_id'] != original['evidence_id']
    assert new['receipt_origin']['job_id'] == reader.id and row.observation_id is None
    assert (old_job.status, old_job.lease_token, old_job.data) == old_identity
    store._saved_receipt_origin(db, P, row, new['receipt_origin'])
    # Removing the original response cannot turn a resume GET into an ordinary
    # collection origin, even if this low-level seam skips the state audit gate.
    row.data = {**row.data, 'receipt_history': [new]}
    with pytest.raises(HTTPException): store._saved_receipt_origin(db, P, row, new['receipt_origin'])


@pytest.mark.parametrize('terminal', ['no_data', 'unclocked', 'already_bound'])
def test_saved_response_without_usable_new_observation_never_grants_resume(seam, terminal):
    row, db = seam.row, seam.db
    raw = packet(row, status='completed_no_data' if terminal == 'no_data' else 'completed',
        observed=None if terminal == 'unclocked' else '2026-10-23T12:00:00+08:00')
    store.record_receipt(db, P, row.id, row.version, raw, receipt_origin=store.receipt_origin(db, P, row.id))
    if terminal == 'already_bound': row.observation_id = uid(90)
    version = row.version
    assert store.enqueue_receipt_read(db, P, row.id, version, uid(50)) == (row, None)
    assert row.version == version and not row.data['read_jobs']


def test_same_acquisition_orphan_audit_blocks_replacement_exchange(seam, monkeypatch):
    row, db = seam.row, seam.db
    acq = Acquisition(id=row.acquisition_id, workspace_id=P.workspace_id, entity_id=uid(9),
        capability_id=row.source_id, idempotency_key=row.data['freeze']['original_request_key'], data={'actor_id': P.user_id})
    db.add(acq); db.rows.remove(row)
    anchor(db, row)
    with pytest.raises(HTTPException):
        store.create(db, P, acq.id, seam.job.id, row.data['freeze']['mapping'],
            row.data['freeze']['domain_frozen'], row.data['freeze']['wire_frozen'])
    assert not any(type(item) is Exchange for item in db.rows)
