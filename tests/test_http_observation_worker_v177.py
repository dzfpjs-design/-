"""Pure worker/ledger seams; no actual HTTP, PG or production completion."""
import copy
import hashlib
import sys
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid5

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, BooleanClauseList

from generic_agents import evidence, leases
from generic_agents import http_observation_worker_v177 as worker
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from generic_agents.models import Acquisition, Audit, Capability, Entity, Job, Observation, SensorAsset

ledger, protocol, transport = worker.ledger, worker.protocol, worker.transport


def uid(number): return str(UUID(int=number))
P = SimpleNamespace(user_id=uid(1), workspace_id=uid(2), role='operator', session_id='worker-seam')
FIELD, TYPE = 'field_aabb001122334455', 'object_aabb001122334455'


class Rows:
    def __init__(self):
        self.rows = []; self.commits = 0; self.depth = 0; self.events = []; self.info = {'worker_actor': P}
    def add(self, row):
        if row.id is None: row.id = uid(1000 + len(self.rows))
        self.rows.append(row)
    def scalars(self, statement):
        model = statement.column_descriptions[0]['entity']
        def value(row, node):
            if isinstance(node, BindParameter): return node.value
            if getattr(node, 'operator', None) is operators.json_getitem_op:
                return value(row, node.left).get(value(row, node.right))
            return getattr(row, node.key)
        def matches(row, node):
            if isinstance(node, BooleanClauseList): return all(matches(row, part) for part in node.clauses)
            assert node.operator is operators.eq
            return value(row, node.left) == value(row, node.right)
        return [row for row in self.rows if type(row) is model and matches(row, statement.whereclause)]
    def scalar(self, statement):
        rows = self.scalars(statement); return rows[0] if rows else None
    def commit(self):
        assert self.depth == 0, 'no checkpoint is allowed inside derived savepoint'
        self.events.append('checkpoint')
        self.commits += 1
    def begin_nested(self):
        db = self
        class Nested:
            def __enter__(self):
                self.rows = list(db.rows)
                self.values = [(row, copy.deepcopy({k: v for k, v in vars(row).items()
                    if k != '_sa_instance_state'})) for row in db.rows]
                db.depth += 1
            def __exit__(self, kind, error, trace):
                db.depth -= 1
                if kind:
                    db.rows = self.rows
                    for row, values in self.values:
                        for key in list(vars(row)):
                            if key != '_sa_instance_state': del row.__dict__[key]
                        row.__dict__.update(values)
                return False
        return Nested()


def packet(api, *, status='completed', observed='2026-10-23T23:30:00+00:00'):
    wire = api.row.data['freeze']['wire_frozen']; request = wire['request']
    sign = hashlib.sha256(request['request_key'].encode()).hexdigest()
    command_key, record_id = 'acq-v176:' + sign, 'sim-acq-v176:' + sign
    supplier = {**protocol.MARK, 'request_id': command_key, 'device_id': request['asset_external_id'],
        'action': 'read_telemetry', 'status': 'succeeded', 'device_revision': 1,
        'result': {**protocol.MARK, 'id': request['asset_external_id'], 'revision': 1,
            'sampled_at': observed, 'state': {'temperature_c': 38},
            'time_semantics': protocol.TIME, 'physical_device_connected': False}}
    observation = {**protocol.MARK, 'schema': 'sim-telemetry-observation-v176', 'source_record_id': record_id,
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
        'cost_minor': 0, 'currency': 'CNY', 'artifacts': [], 'observation': observation if status == 'completed' else None,
        'observation_available': status == 'completed', 'source_time_verified': False,
        'field_observation_created': False, 'supplier_receipt': supplier}


@pytest.fixture
def seam(monkeypatch):
    db = Rows()
    scope = {'workspace_id': P.workspace_id, 'environment_id': uid(3), 'instance_id': uid(4),
        'release_id': uid(5), 'package_id': uid(6), 'package_hash': 'a' * 64, 'entity_type': TYPE}
    links = {'temperature_c': {'field_key': FIELD, 'unit': '°C'}}
    manifest = {'kind': 'source', 'allowed_agent_roles': ['perception'], 'output_schema': {'type': 'object'},
        'execution': {'adapter': 'http', 'side_effect': True, 'endpoint': 'http://fixture/acquisitions',
            'receipt_endpoint': 'http://fixture/acquisitions/receipts', 'secret_ref': 'GA_CONNECTOR_SECRET_SEAM'},
        'data_policy': {'simulation': True, 'test_only': True, 'cloud_allowed': False, 'deployment': 'local'},
        'specification': {'simulation_environment_id': scope['environment_id'], transport.MAPPING_KEY: {
            'schema': protocol.CONFIG_SCHEMA, 'scope': scope, 'links': links,
            'supplier_contract': protocol.SUPPLIER, 'time_semantics': protocol.TIME},
            'observation_capability': {'fields': [FIELD], 'modalities': ['numeric'], 'actions': ['observe']}}}
    source = Capability(id=uid(7), workspace_id=P.workspace_id, kind='source', status='published',
        data=manifest, content_hash=protocol.digest(manifest), validated_hash=protocol.digest(manifest))
    schemas = {TYPE: {'type': 'object', 'properties': {FIELD: {'type': 'number', 'unit': '°C'}}}}
    mapping = protocol.freeze_mapping(manifest, {**scope, 'source_id': source.id}, schemas, links)
    entity = Entity(id=uid(8), workspace_id=P.workspace_id, entity_type=TYPE,
        external_id='对象一', schema_capability_id=scope['package_id'], data={})
    asset = SensorAsset(id=uid(9), workspace_id=P.workspace_id, source_id=source.id,
        external_id='sim-weather-01', version=1, data={})
    acq = Acquisition(id=uid(10), workspace_id=P.workspace_id, capability_id=source.id, entity_id=entity.id,
        idempotency_key='sensing:' + uid(10) + ':1', status='unknown', version=4,
        data={'actor_id': P.user_id, 'source_hash': source.content_hash, 'sensor_asset_id': asset.id,
            'selected_fields': [FIELD], 'selected_action': 'observe', 'reserved_cost_minor': 0, 'currency': 'CNY',
            'dispatch_execution': copy.deepcopy(manifest['execution']), worker.ACQ_EXCHANGE_KEY: uid(11)})
    dispatch = Job(id=uid(12), workspace_id=P.workspace_id, kind='dispatch_acquisition', status='succeeded',
        lease_token=uid(13), idempotency_key='dispatch:' + acq.id,
        data={'actor_id': P.user_id, 'acquisition_id': acq.id})
    request = {'request_key': acq.idempotency_key, 'target_external_id': entity.external_id,
        'fields': [FIELD], 'max_duration_seconds': 30, 'asset_external_id': asset.external_id,
        'action': 'observe', 'max_cost_minor': 0, 'currency': 'CNY'}
    pair = protocol.to_supplier_request(request, mapping)
    frozen = {'schema': 'http-observation-exchange-freeze-v177', 'mapping': mapping, **pair,
        'parent_pins': {'entity_id': entity.id, 'sensor_asset_id': asset.id},
        'original_request_key': request['request_key'], 'dispatch_identity': {
            'job_id': dispatch.id, 'kind': dispatch.kind, 'idempotency_key': dispatch.idempotency_key,
            'actor_id': P.user_id, 'lease_token': dispatch.lease_token}}
    row = Exchange(id=uid(11), workspace_id=P.workspace_id, actor_id=P.user_id, acquisition_id=acq.id,
        source_id=source.id, environment_id=scope['environment_id'], instance_id=scope['instance_id'],
        release_id=scope['release_id'], dispatch_job_id=dispatch.id, observation_id=None,
        status='awaiting_receipt', version=3, data={'schema': ledger.SCHEMA, **ledger.MARK,
            'freeze': frozen, 'freeze_hash': protocol.digest(frozen), 'receipt_history': [],
            'latest_receipt_hash': None, 'projection': None, 'observation_pin': None,
            'read_jobs': [], 'active_read_job_id': uid(14)})
    job = Job(id=uid(14), workspace_id=P.workspace_id, kind=ledger.READ_KIND, status='running',
        lease_token=uid(15), idempotency_key='http-observation-receipt:' + row.id + ':' + uid(16),
        data={'exchange_id': row.id, 'actor_id': P.user_id, 'request_id': uid(16),
            'original_request_key': acq.idempotency_key, 'wire_request_hash': pair['wire_frozen']['request_hash'],
            'exchange_version': row.version})
    instruction_hash = protocol.digest(ledger._read_instruction(job))
    row.data = {**row.data, 'read_jobs': [{'job_id': job.id, 'request_id': uid(16), 'instruction_hash': instruction_hash}]}
    for item in (source, entity, asset, acq, dispatch, row, job): db.add(item)
    db.add(Audit(id=uid(17), workspace_id=P.workspace_id, actor_id=P.user_id,
        action=ledger.READ_AUDIT, object_id=job.id,
        data={'exchange_id': row.id, 'request_id': uid(16), 'instruction_hash': instruction_hash, 'job_key': job.idempotency_key}))
    db.add(Audit(id=uid(18), workspace_id=P.workspace_id, actor_id=P.user_id,
        action=ledger.ACTION, object_id=row.id, data={'acquisition_id': acq.id}))
    db.info['worker_lease'] = {'job_id': job.id, 'lease_token': job.lease_token}
    api = SimpleNamespace(db=db, source=source, entity=entity, asset=asset, acq=acq, dispatch=dispatch,
        row=row, job=job, gets=[], after=[], derived=[], evidence={}, observation=None, child=None)
    api.raw = packet(api)
    monkeypatch.setattr(ledger, 'checked_store', lambda db, p, identity, **kwargs: next(
        item for item in db.rows if type(item) is Exchange and item.id == identity))
    monkeypatch.setattr(worker, 'lock_current_actor', lambda db, p: None)
    monkeypatch.setattr(ledger, 'lock_current_actor', lambda db, p: None)
    monkeypatch.setattr(worker, '_lock_acquisition', lambda db, p, identity: next(
        item for item in db.rows if type(item) is Acquisition and item.id == identity))
    def get(execution, key): api.gets.append((copy.deepcopy(execution), key)); return copy.deepcopy(api.raw)
    monkeypatch.setattr(worker, '_get', get)
    def save(db, p, raw, media_type, metadata):
        ev = SimpleNamespace(id=uid(2000 + len(api.evidence)), sha256=hashlib.sha256(raw).hexdigest(), data=metadata)
        db.events.append('receipt_evidence')
        api.evidence[ev.id] = (ev, bytes(raw)); return ev
    monkeypatch.setattr(evidence, 'save_immutable', save)
    def after(db, p, acq, raw):
        db.events.append('terminal')
        api.after.append(protocol.digest(raw))
        acq.status = 'rejected' if raw['status'] == 'completed_no_data' else 'awaiting_observation'
        acq.data = {**acq.data, 'device_action_terminal': raw['terminal']}
    monkeypatch.setattr(worker, '_after_receipt', after)
    def derive(db, p, identity, raw, *, saved_receipt):
        assert db.depth == 1 and saved_receipt is True
        db.events.append('derive')
        actual = ledger.receipt_origin(db, p, identity)
        assert ledger.same(actual, row.data['receipt_history'][-1]['receipt_origin'])
        api.derived.append((db.commits, protocol.digest(raw)))
        if row.observation_id: return row
        body = transport._body(row, uid(30)); original = transport._request(body)
        value = body.model_dump(mode='json', exclude={'source_id', 'source_record_id', 'processing_version',
            'entity_id', 'observed_at', 'business_day'})
        observation = Observation(id=uid(31), workspace_id=p.workspace_id, source_id=body.source_id,
            source_record_id=body.source_record_id, processing_version=body.processing_version,
            entity_id=body.entity_id, business_day=body.business_day, observed_at=datetime.fromisoformat(body.observed_at),
            status='received', data={**value, 'original_request': original, 'input_hash': protocol.digest(original),
                'submitted_by': p.user_id, **transport._labels(row)})
        child = Job(id=uid(32), workspace_id=p.workspace_id, kind=transport.PROCESS, status='waiting_parent',
            attempts=0, idempotency_key='extract:' + observation.id, data={'observation_id': observation.id,
                'actor_id': p.user_id, 'http_mapped_exchange_id': row.id, 'http_mapped_parent_job_id': actual['job_id']})
        db.add(observation); db.add(child)
        api.observation, api.child = observation, child
        return ledger.attach_observation(db, p, row.id, row.version, observation.id)
    api.derive = derive
    monkeypatch.setattr(transport, 'receive', derive)
    return api


def test_find_uses_real_acquisition_and_refuses_dangling_new_family(seam):
    assert worker.find_exchange(seam.db, P, SimpleNamespace(id=seam.acq.id)) is seam.row
    seam.db.rows.remove(seam.row)
    with pytest.raises(HTTPException): worker.find_exchange(seam.db, P, seam.acq)


@pytest.mark.parametrize('trace', ['audit', 'source', 'marker'])
def test_missing_exchange_with_any_independent_trace_cannot_become_legacy(seam, trace):
    seam.db.rows.remove(seam.row)
    if trace != 'audit': seam.db.rows = [r for r in seam.db.rows if not (type(r) is Audit and r.action == ledger.ACTION)]
    if trace != 'marker': del seam.acq.data[worker.ACQ_EXCHANGE_KEY]
    if trace != 'source': del seam.source.data['specification'][transport.MAPPING_KEY]
    seam.source.content_hash = seam.source.validated_hash = protocol.digest(seam.source.data)
    with pytest.raises(HTTPException): worker.find_exchange(seam.db, P, seam.acq)


def test_true_legacy_without_mapping_marker_or_exchange_audit_remains_legacy(seam):
    seam.db.rows = [r for r in seam.db.rows if type(r) is not Exchange and not (type(r) is Audit and r.action == ledger.ACTION)]
    del seam.acq.data[worker.ACQ_EXCHANGE_KEY]; del seam.source.data['specification'][transport.MAPPING_KEY]
    seam.source.content_hash = seam.source.validated_hash = protocol.digest(seam.source.data)
    assert worker.find_exchange(seam.db, P, seam.acq) is None


def test_queue_is_cas_explicit_and_second_click_returns_original_queued_job(seam):
    seam.row.data = {**seam.row.data, 'active_read_job_id': None, 'read_jobs': []}
    expected = str(uuid5(UUID(seam.row.id), 'receipt:3:4'))
    row, queued = worker.queue_read(seam.db, P, seam.acq)
    assert queued.status == 'queued' and queued.kind == ledger.READ_KIND and queued.data['request_id'] == expected
    instruction = copy.deepcopy(queued.data); version = row.version
    assert worker.queue_read(seam.db, P, seam.acq)[1] is queued
    assert row.version == version and queued.data == instruction and seam.gets == []
    assert seam.acq.status == 'unknown' and worker.QUEUED_MESSAGE == '核对原回执已排队'


def test_queue_terminal_is_readonly_and_other_operator_cannot_query(seam):
    seam.row.status = 'received_no_data'
    assert worker.queue_read(seam.db, P, seam.acq) == (seam.row, None)
    other = SimpleNamespace(**{**vars(P), 'user_id': uid(99)})
    with pytest.raises(HTTPException) as error: worker.queue_read(seam.db, other, seam.acq)
    assert error.value.status_code == 403 and not seam.gets


@pytest.mark.parametrize('damage', ['audit', 'instruction', 'source', 'key', 'lease'])
def test_bad_original_binding_is_rejected_before_network(seam, damage):
    if damage == 'audit': seam.db.rows = [r for r in seam.db.rows if not (type(r) is Audit and r.action == ledger.READ_AUDIT)]
    if damage == 'instruction': seam.job.data['exchange_version'] = 999
    if damage == 'source': seam.source.data['execution']['receipt_endpoint'] = 'http://other/receipts'
    if damage == 'key': seam.job.data['original_request_key'] += ':other'
    if damage == 'lease': seam.db.info['worker_lease']['lease_token'] = uid(99)
    with pytest.raises(HTTPException): worker.run_read(seam.db, P, seam.job)
    assert seam.gets == [] and seam.after == []


def test_receipt_is_durable_before_derived_rows_and_child_waits_for_running_parent(seam):
    instruction = copy.deepcopy(seam.job.data)
    worker.run_read(seam.db, P, seam.job)
    assert len(seam.gets) == 1 and seam.gets[0][1] == seam.acq.idempotency_key
    assert len(seam.evidence) == 1 and len(seam.after) == 1
    assert seam.derived[0][0] >= 3 and seam.child.status == 'waiting_parent'
    assert {k: seam.job.data[k] for k in instruction} == instruction
    assert seam.job.data['exchange_version'] == 3 and seam.row.version > 3
    assert seam.job.data[worker.RESULT]['status'] == 'observation_saved'
    assert seam.job.data[worker.RESULT]['goal_completed'] is False
    commits = seam.db.commits
    worker.finalize_parent(seam.db, seam.job)
    assert seam.child.status == 'queued' and seam.job.status == 'running' and seam.db.commits == commits


def test_saved_receipt_continuation_uses_original_get_without_network_or_version_rewrite(seam):
    worker.run_read(seam.db, P, seam.job)
    original = copy.deepcopy(seam.row.data['receipt_history'])
    worker.run_read(seam.db, P, seam.job)
    assert len(seam.gets) == 1 and len(seam.evidence) == 1
    assert seam.row.data['receipt_history'] == original and seam.job.data['exchange_version'] == 3
    assert len([r for r in seam.db.rows if type(r) is Observation]) == 1


@pytest.mark.parametrize('failure', ['404', 'pending', 'wrong_key', 'timeout'])
def test_failed_or_absent_receipt_stays_unknown_without_release_or_derivation(seam, monkeypatch, failure):
    if failure == '404':
        def missing(*args): raise httpx.HTTPStatusError('missing', request=httpx.Request('GET', 'http://fixture'), response=httpx.Response(404))
        monkeypatch.setattr(worker, '_get', missing)
    if failure == 'timeout':
        def timeout(*args): raise httpx.ReadTimeout('timeout')
        monkeypatch.setattr(worker, '_get', timeout)
    if failure == 'pending': seam.raw = None
    if failure == 'wrong_key': seam.raw['request_key'] += ':other'
    worker.run_read(seam.db, P, seam.job)
    assert seam.acq.status == 'unknown' and seam.after == [] and seam.derived == [] and seam.evidence == {}
    assert seam.row.observation_id is None and seam.job.data[worker.RESULT]['status'] == 'unknown'


@pytest.mark.parametrize('status,observed', [('accepted', '2026-10-23T00:00:00+00:00'),
    ('completed_no_data', '2026-10-23T00:00:00+00:00'), ('completed', None)])
def test_accepted_no_data_or_unknown_time_only_saves_receipt(seam, status, observed):
    seam.raw = packet(seam, status=status, observed=observed)
    worker.run_read(seam.db, P, seam.job)
    assert seam.evidence and not seam.derived and seam.row.observation_id is None
    assert seam.job.data[worker.RESULT]['status'] == 'receipt_saved'


def test_business_block_rolls_back_all_partial_derived_rows_but_retains_receipt(seam, monkeypatch):
    def blocked(*args, **kwargs):
        seam.derive(*args, **kwargs)
        raise HTTPException(409, '原业务已撤回')
    monkeypatch.setattr(transport, 'receive', blocked)
    worker.run_read(seam.db, P, seam.job)
    assert seam.evidence and seam.row.observation_id is None
    assert not any(type(r) is Observation for r in seam.db.rows)
    assert not any(type(r) is Job and r.kind == transport.PROCESS for r in seam.db.rows)
    assert seam.job.data[worker.RESULT]['status'] == 'derived_blocked'
    assert seam.job.data[worker.RESULT]['retain_receipt'] is True and '撤回' in seam.acq.data['reason']


@pytest.mark.parametrize('error', [HTTPException(403, 'current role revoked'),
    leases.LeaseLost('lease lost'), leases.ExecutionUnauthorized('account revoked')])
def test_permission_and_lease_failures_escape_and_do_not_commit_partial_observation(seam, monkeypatch, error):
    def denied(*args, **kwargs):
        seam.derive(*args, **kwargs); raise error
    monkeypatch.setattr(transport, 'receive', denied)
    with pytest.raises(type(error)): worker.run_read(seam.db, P, seam.job)
    assert seam.row.observation_id is None and not any(type(r) is Observation for r in seam.db.rows)
    assert worker.RESULT not in seam.job.data


@pytest.mark.parametrize('damage', ['kind', 'attempts', 'body_type', 'origin', 'parent', 'pin', 'missing'])
def test_finalizer_refuses_tampered_child_or_observation_without_committing(seam, damage):
    worker.run_read(seam.db, P, seam.job); commits = seam.db.commits
    if damage == 'kind': seam.child.kind = 'process_observation'
    if damage == 'attempts': seam.child.attempts = 1
    if damage == 'body_type': seam.observation.data['original_request']['attributes'][FIELD] = 38.0
    if damage == 'origin': seam.observation.data['http_mapped_origin_v177']['exchange_id'] = uid(99)
    if damage == 'parent': seam.child.data['http_mapped_parent_job_id'] = uid(99)
    if damage == 'pin': seam.row.data['observation_pin']['input_hash'] = 'b' * 64
    if damage == 'missing': seam.db.rows.remove(seam.child)
    with pytest.raises(HTTPException): worker.finalize_parent(seam.db, seam.job)
    assert seam.child.status == 'waiting_parent' and seam.db.commits == commits and seam.job.status == 'running'


def test_dispatch_parent_can_only_activate_its_own_receipt_child(seam):
    seam.dispatch.status = 'running'
    seam.db.info['worker_lease'] = {'job_id': seam.dispatch.id, 'lease_token': seam.dispatch.lease_token}
    worker.settle_receipt(seam.db, P, seam.row.id, seam.raw)
    commits = seam.db.commits
    worker.finalize_parent(seam.db, seam.dispatch)
    assert seam.child.status == 'queued' and seam.dispatch.status == 'running' and seam.db.commits == commits


@pytest.mark.parametrize('parent_kind', ['get', 'post'])
def test_shared_settlement_persists_validated_receipt_before_any_terminal_or_child_write(seam, parent_kind):
    if parent_kind == 'post':
        seam.dispatch.status = 'running'
        seam.db.info['worker_lease'] = {'job_id': seam.dispatch.id, 'lease_token': seam.dispatch.lease_token}
    worker.settle_receipt(seam.db, P, seam.row.id, seam.raw)
    events = seam.db.events
    receipt_at, terminal_at, derived_at = (events.index(key) for key in ('receipt_evidence', 'terminal', 'derive'))
    assert receipt_at < terminal_at < derived_at
    assert 'checkpoint' in events[receipt_at + 1:terminal_at]
    assert 'checkpoint' in events[terminal_at + 1:derived_at]
    assert seam.child.status == 'waiting_parent'
    assert seam.job.status == 'running' and seam.dispatch.status in {'running', 'succeeded'}


@pytest.mark.parametrize('parent_kind', ['get', 'post'])
@pytest.mark.parametrize('damage', ['request_hash', 'no_data_request_hash', 'simulation', 'unit', 'changed_clock'])
def test_invalid_complete_packet_never_reaches_checkpoint_or_resource_terminal_mutation(seam, parent_kind, damage):
    if parent_kind == 'post':
        seam.dispatch.status = 'running'
        seam.db.info['worker_lease'] = {'job_id': seam.dispatch.id, 'lease_token': seam.dispatch.lease_token}
    raw = copy.deepcopy(seam.raw)
    if damage == 'request_hash': raw['request_hash'] = 'b' * 64
    if damage == 'no_data_request_hash':
        raw = packet(seam, status='completed_no_data'); raw['request_hash'] = 'b' * 64
    if damage == 'simulation': raw['simulation'] = False
    if damage == 'unit': raw['observation']['field_units']['temperature_c'] = 'K'
    if damage == 'changed_clock': raw['observation']['observed_at'] = '2026-10-24T00:30:00+00:00'
    # Generic key/asset/terminal fields remain plausible: this is the exact
    # regression that used to release before the stricter SIM mapping gate.
    assert raw['request_key'] == seam.acq.idempotency_key and raw['terminal'] is True
    original = copy.deepcopy(seam.acq.data)
    with pytest.raises(HTTPException): worker.settle_receipt(seam.db, P, seam.row.id, raw)
    assert seam.db.events == [] and seam.db.commits == 0
    assert seam.after == [] and seam.evidence == {} and seam.derived == []
    assert seam.acq.data == original and seam.acq.status == 'unknown'
    assert seam.row.data['receipt_history'] == [] and seam.row.observation_id is None


def test_actual_get_seam_is_bounded_no_redirect_no_proxy_and_uses_only_original_key(monkeypatch):
    seen = {}
    monkeypatch.setitem(sys.modules, 'generic_agents.gateway', SimpleNamespace(
        validate_endpoint=lambda execution: execution['endpoint']))
    class Client:
        def __init__(self, **kwargs): seen['client'] = kwargs
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def bounded(client, method, endpoint, **kwargs):
        seen.update(method=method, endpoint=endpoint, request=kwargs)
        return {'raw': 'original'}, {}
    monkeypatch.setattr(worker.httpx, 'Client', Client)
    monkeypatch.setattr(worker.connectors, 'bounded_response', bounded)
    monkeypatch.setattr(worker.connectors, 'headers', lambda execution: {'Authorization': 'Bearer test-only'})
    execution = {'secret_ref': 'GA_CONNECTOR_SECRET_TEST_ONLY', 'receipt_endpoint': 'http://fixture/original-receipts'}
    assert worker._get(execution, 'the-original-key') == {'raw': 'original'}
    assert seen['client']['follow_redirects'] is False and seen['client']['trust_env'] is False
    assert seen['client']['timeout'].connect == 5 and seen['client']['timeout'].read == 15
    assert seen['method'] == 'GET' and seen['endpoint'] == execution['receipt_endpoint']
    assert seen['request']['params'] == {'request_key': 'the-original-key'}
    assert seen['request']['headers']['Idempotency-Key'] == 'the-original-key'
    assert 'json' not in seen['request'] and 'content' not in seen['request']


def retained_post_receipt(seam, monkeypatch):
    seam.dispatch.status = 'running'
    seam.db.info['worker_lease'] = {'job_id': seam.dispatch.id, 'lease_token': seam.dispatch.lease_token}
    def blocked(*args, **kwargs): raise HTTPException(409, '旧接收门暂不可用')
    monkeypatch.setattr(transport, 'receive', blocked)
    worker.settle_receipt(seam.db, P, seam.row.id, seam.raw)
    assert seam.row.status == 'received' and seam.row.observation_id is None
    assert seam.dispatch.data[worker.RESULT]['retain_receipt'] is True
    seam.dispatch.status = 'failed'
    # Existing current GET in the generic fixture predates this POST and is not
    # an actual active query for this test's retained-response scenario.
    seam.job.status = 'succeeded'
    monkeypatch.setattr(transport, 'receive', seam.derive)
    return copy.deepcopy(seam.row.data['receipt_history'][0])


def test_explicit_queue_restores_previously_blocked_original_receipt_end_to_end_at_seam(seam, monkeypatch):
    original = retained_post_receipt(seam, monkeypatch)
    dispatch_state = (seam.dispatch.status, seam.dispatch.lease_token, copy.deepcopy(seam.dispatch.data))
    row, reader = worker.queue_read(seam.db, P, seam.acq)
    assert reader is not None and reader.kind == ledger.READ_KIND and reader.status == 'queued'
    assert reader.data[ledger.RESUME]['receipt_hash'] == original['raw_hash']
    assert worker.queue_read(seam.db, P, seam.acq)[1] is reader
    reader.status = 'running'; reader.lease_token = uid(80)
    seam.db.info['worker_lease'] = {'job_id': reader.id, 'lease_token': reader.lease_token}
    worker.run_read(seam.db, P, reader)
    assert len(seam.gets) == 1 and seam.gets[0][1] == seam.acq.idempotency_key
    assert row.observation_id == seam.observation.id and row.data['receipt_history'][0] == original
    assert len(row.data['receipt_history']) == 2 and len(seam.evidence) == 2
    latest = row.data['receipt_history'][-1]
    assert latest['raw'] == original['raw'] and latest['raw_hash'] == original['raw_hash']
    assert latest['receipt_origin']['job_id'] == reader.id
    assert latest['sha256'] == original['sha256']
    assert seam.observation.data['original_request']['observed_at'] == original['projection']['observed_at']
    assert seam.child.status == 'waiting_parent' and seam.child.data['http_mapped_parent_job_id'] == reader.id
    assert (seam.dispatch.status, seam.dispatch.lease_token, seam.dispatch.data) == dispatch_state
    worker.finalize_parent(seam.db, reader)
    assert seam.child.status == 'queued' and reader.status == 'running'
    # The confirmed existing observation is never downgraded or queued again.
    assert worker.queue_read(seam.db, P, seam.acq) == (row, None)


def test_explicit_resume_rejects_new_values_instead_of_relabelling_as_the_original_package(seam, monkeypatch):
    original = retained_post_receipt(seam, monkeypatch)
    _, reader = worker.queue_read(seam.db, P, seam.acq)
    reader.status = 'running'; reader.lease_token = uid(80)
    seam.db.info['worker_lease'] = {'job_id': reader.id, 'lease_token': reader.lease_token}
    seam.raw['observation']['attributes']['temperature_c'] = 39
    seam.raw['supplier_receipt']['result']['state']['temperature_c'] = 39
    seam.raw['observation']['supplier_receipt_hash'] = protocol.digest(seam.raw['supplier_receipt'])
    # Structurally valid provider data for the same wire request is still a
    # different saved package; this recovery cannot rewrite its original clock
    # or source facts and cannot manufacture a new acquisition.
    before_terminal = len(seam.after)
    with pytest.raises(HTTPException): worker.run_read(seam.db, P, reader)
    assert seam.row.data['receipt_history'] == [original]
    assert len(seam.after) == before_terminal and seam.row.observation_id is None
    assert len(seam.evidence) == 1 and seam.child is None


def test_resume_capsule_is_covered_by_independent_read_instruction_hash(seam, monkeypatch):
    original = retained_post_receipt(seam, monkeypatch)
    _, reader = worker.queue_read(seam.db, P, seam.acq)
    reader.status = 'running'; reader.lease_token = uid(80)
    seam.db.info['worker_lease'] = {'job_id': reader.id, 'lease_token': reader.lease_token}
    reader.data = {**reader.data, ledger.RESUME: {**reader.data[ledger.RESUME], 'receipt_hash': 'b' * 64}}
    with pytest.raises(HTTPException): worker.run_read(seam.db, P, reader)
    assert not seam.gets and seam.row.data['receipt_history'] == [original]
