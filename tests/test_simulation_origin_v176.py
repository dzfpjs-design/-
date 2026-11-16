"""Synthetic source-reader unit tests, NOT API/Postgres/device acceptance.

FakeDB interprets only this reader's public select predicates. Disk Evidence and
lease checkpoint are explicit boundary spies; validators and guard stay real.
No specimen here represents a physical field observation or native HTTP bytes.
"""
import copy
import hashlib
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import simulation_origin_v176 as m
from generic_agents.contracts import ObservationCreate, digest
from generic_agents.leases import LeaseLost, ExecutionUnauthorized


def output():
    marks = {'simulation': True, 'origin_kind': 'simulated_equipment', 'field_verified': False}
    return {**marks, 'request_id': 'simulation-sample:sample', 'device_id': 'sim-weather-01',
        'action': 'read_telemetry', 'status': 'succeeded', 'device_revision': 2,
        'result': {**marks, 'id': 'sim-weather-01', 'revision': 2,
            'state': {'temperature_c': 38.0, 'humidity_percent': 70, 'wind_m_s': 2.0, 'pressure_hpa': 1005},
            'sampled_at': '2026-10-23T00:00:01+00:00', 'time_semantics': 'simulator_sample_time',
            'physical_device_connected': False}}


def test_four_weather_fields_preserve_typed_values_raw_clock_units_and_input():
    value = output(); before = copy.deepcopy(value)
    result = m.sample_values(value, 'sim-weather-01', list(m.WEATHER_UNITS))
    assert value == before
    assert result == {'attributes': {'temperature_c': 38.0, 'humidity_percent': 70,
        'wind_m_s': 2.0, 'pressure_hpa': 1005}, 'observed_at': value['result']['sampled_at'],
        'field_units': {'temperature_c': '°C', 'humidity_percent': '%', 'wind_m_s': 'm/s', 'pressure_hpa': 'hPa'}}
    assert type(result['attributes']['temperature_c']) is float
    assert type(result['attributes']['humidity_percent']) is int


@pytest.mark.parametrize('fields', [[], ['temperature_c', 'temperature_c'], ['battery_percent'],
    ['temperature_c', []], 'temperature_c', None])
def test_unknown_duplicate_or_malformed_weather_selection_rejected(fields):
    with pytest.raises(HTTPException) as error:
        m.sample_values(output(), 'sim-weather-01', fields)
    assert error.value.status_code == 409


@pytest.mark.parametrize('field,value', [('temperature_c', True), ('temperature_c', '38'),
    ('temperature_c', float('nan')), ('temperature_c', float('inf')),
    ('temperature_c', 'huge_integer'), ('temperature_c', -50.1), ('temperature_c', 100.1),
    ('humidity_percent', -1), ('humidity_percent', 101), ('wind_m_s', 75.1), ('pressure_hpa', 499)])
def test_invalid_measurement_never_defaults_or_escapes_overflow(field, value):
    if value == 'huge_integer':
        value = 10 ** 10000
    data = output(); data['result']['state'][field] = value
    with pytest.raises(HTTPException):
        m.sample_values(data, 'sim-weather-01', [field])


@pytest.mark.parametrize('target,key,value', [
    ('envelope', 'status', 'running'), ('envelope', 'status', 'accepted'),
    ('envelope', 'simulation', 1), ('envelope', 'device_id', 'real-weather'),
    ('envelope', 'action', 'get_status'), ('result', 'physical_device_connected', True),
    ('result', 'sampled_at', '2026-10-23T00:00:01'), ('result', 'sampled_at', None),
    ('result', 'time_semantics', 'field_time'), ('result', 'id', 'other-device'),
    ('result', 'field_verified', True)])
def test_protocol_and_time_meaning_cannot_be_relabeled(target, key, value):
    data = output(); (data if target == 'envelope' else data['result'])[key] = value
    with pytest.raises(HTTPException):
        m.sample_values(data, 'sim-weather-01', ['temperature_c'])


class FakeDB:
    def __init__(self):
        self.rows = {}; self.info = {}; self.checkpoints = 0

    def put(self, cls, identity, **kwargs):
        row = NS(id=identity, workspace_id='workspace', **kwargs)
        self.rows.setdefault(cls, []).append(row)
        return row

    def scalars(self, statement):
        cls = statement.column_descriptions[0]['entity']
        params = statement.compile().params
        result = list(self.rows.get(cls, []))
        for column in ('workspace_id', 'id', 'observation_id', 'idempotency_key', 'action', 'object_id'):
            key = column + '_1'
            if key in params:
                result = [r for r in result if getattr(r, column, None) == params[key]]
        if cls is m.Environment and params.get('data_1') == 'namespace':
            result = [r for r in result if r.data.get('namespace') == params['param_1']]
        return result


@pytest.fixture
def scene(monkeypatch):
    db = FakeDB(); p = NS(workspace_id='workspace', user_id='actor')
    def cap(identity, kind, data, namespace='sim_namespace', name=None):
        return db.put(m.Capability, identity, kind=kind, namespace=namespace, name=name or identity,
            data=data, content_hash=digest(data), validated_hash=digest(data), status='published')
    package = cap('package', 'domain_package', {'specification': {'entity_schemas': {'weather': {}}}})
    instance = db.put(m.DomainInstance, 'instance', namespace='sim_namespace', active_release_id='release', status='active')
    snapshot = {'profile': {'simulation_v176': True}, 'businesses': [{'id': 'business'}],
        'entity_schemas': {'weather': {'type': 'object'}}}
    release = db.put(m.InstanceRelease, 'release', instance_id=instance.id, package_id=package.id,
        configuration_hash=digest(snapshot), data={'snapshot': snapshot}, status='prepared')
    entity = db.put(m.Entity, 'entity', namespace=instance.namespace, schema_capability_id=package.id,
        entity_type='weather')
    env = db.put(m.Environment, 'environment', instance_id=instance.id, release_id=release.id, actor_id='admin',
        data={'schema': 'simulation-environment-v176', **m._MARK, 'instance_id': instance.id,
            'release_id': release.id, 'namespace': instance.namespace, 'package_id': package.id,
            'configuration_hash': release.configuration_hash, 'created_by': 'admin', 'note': 'pure synthetic fixture'})
    source = cap('source', 'source', {'specification': {'simulation_environment_id': env.id},
        'data_policy': {'test_only': True, 'cloud_allowed': False, 'deployment': 'local'}})
    extractor = cap('extractor', 'tool', {'execution': {'adapter': 'builtin', 'handler': 'numeric_rules'},
        'data_policy': {'cloud_allowed': False}})
    gateway = cap('gateway', 'tool', {'execution': {'adapter': 'http', 'side_effect': False},
        'data_policy': {'simulation': True, 'test_only': True}}, namespace='equipment_simulation', name='read_equipment')
    definition = db.put(m.Definition, 'definition', environment_id=env.id, actor_id='admin', status='enabled',
        data={'schema': 'simulation-definition-v176', 'environment_hash': digest(env.data),
            'instance_id': instance.id, 'release_id': release.id, 'entity_id': entity.id,
            'source_id': source.id, 'extractor_id': extractor.id, 'gateway_id': gateway.id,
            'business_id': 'business', 'device_id': 'sim-weather-01', 'fields': ['temperature_c'],
            'interval_seconds': 60, 'valid_until': '2026-01-01T00:00:00+00:00', 'goal_spec': None,
            'pins': {c.id: c.content_hash for c in (source, extractor, gateway)}})
    inputs = {'device_id': 'sim-weather-01', 'action': 'read_telemetry', 'expected_revision': None, 'arguments': {}}
    sampler = db.put(m.Job, 'sampler', kind='sample_simulation_v176', status='succeeded',
        idempotency_key='simulation-sample:sample', data={'sample_id': 'sample', 'actor_id': 'actor'})
    call = db.put(m.Invocation, 'call', capability_id=gateway.id, idempotency_key='simulation-sample:sample',
        status='succeeded', data={'capability_hash': gateway.content_hash, 'input_hash': digest(inputs),
            'output': output(), 'role': 'perception', 'side_effect': False, 'job_id': sampler.id, 'lease_token': 'sampling-lease'})
    raw = m._bytes(call.data['output'])
    evidence = NS(id='receipt', workspace_id=p.workspace_id, media_type='application/json', size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(), data={**m._MARK, 'purpose': 'parsed_device_receipt_copy_v176',
            'native_http_bytes': False, 'invocation_id': call.id, 'cloud_model_ids': []})
    body = ObservationCreate(source_id=source.id, source_record_id='simulation-sample:sample',
        processing_version='simulation-v176', entity_id=entity.id, observed_at=output()['result']['sampled_at'],
        business_day='2026-10-23', attributes={'temperature_c': 38.0}, extractor_id=extractor.id, startup_mode='automatic')
    original = body.model_dump(exclude={'evidence_ids'})
    origin = {'schema': m.SCHEMA, 'environment_id': env.id, 'definition_id': definition.id,
        'sample_id': 'sample', 'invocation_id': call.id, 'receipt_evidence_id': evidence.id}
    observation = db.put(m.Observation, 'observation', source_id=source.id, entity_id=entity.id,
        source_record_id=body.source_record_id, processing_version=body.processing_version,
        observed_at=datetime.fromisoformat(body.observed_at), business_day=body.business_day, status='received',
        data={'simulation_origin': origin, 'original_request': original, 'input_hash': digest(original),
            'attributes': copy.deepcopy(body.attributes), 'extractor_id': extractor.id, 'evidence_ids': [],
            'submitted_by': 'actor', 'date_basis': 'simulator_sample_time', 'source_time_verified': False,
            'field_units': {'temperature_c': '°C'}})
    process = db.put(m.Job, 'process', kind=m.PROCESS, status='queued', lease_token='process-lease',
        idempotency_key='extract:' + observation.id, data={'observation_id': observation.id,
            'extractor_id': extractor.id, 'actor_id': 'actor'})
    sample = db.put(m.Sample, 'sample', definition_id=definition.id, actor_id='actor', request_id='sample-request',
        status='received', invocation_id=call.id, observation_id=observation.id,
        data={'schema': 'simulation-sample-v176', 'definition_hash': digest(definition.data),
            'environment_hash': digest(env.data), 'inputs': inputs, 'input_hash': digest(inputs), 'job_id': sampler.id,
            'output_hash': digest(call.data['output']), 'receipt_evidence_id': evidence.id, 'receipt_sha256': evidence.sha256,
            'observation_request_hash': digest(original), 'processing_job_id': process.id,
            'received_at': '2026-10-23T00:00:02+00:00', 'task_id': None})
    def audit(identity, action, row, data):
        return db.put(m.Audit, identity, action=action, object_id=row.id, actor_id=row.actor_id, data=data)
    audit('env-audit', 'simulation.environment.created_v176', env, {'record_hash': digest(env.data)})
    audit('def-audit', 'simulation.definition.created_v176', definition, {'record_hash': digest(definition.data)})
    audit('sample-audit', 'simulation.sample.received_v176', sample, {'observation_id': observation.id,
        'invocation_id': call.id, 'origin_hash': digest(origin), 'output_hash': digest(call.data['output']),
        'record_hash': digest(m.receipt_basis(sample.data))})
    audit('enqueue-audit', 'simulation.sample.enqueued_v176', sample,
          {'definition_id': definition.id, 'request_id': sample.request_id})
    db.put(m.Audit, 'ingest', action='observation.ingest', object_id=observation.id, actor_id='actor', data={})
    disk = {'raw': raw}
    monkeypatch.setattr(m, 'evidence_bytes', lambda actual_db, actual_p, identity:
        (evidence, disk['raw']) if actual_db is db and actual_p is p and identity == evidence.id else pytest.fail('unexpected evidence boundary'))
    def checkpoint(actual_db):
        assert actual_db is db
        db.checkpoints += 1
    monkeypatch.setattr(m, 'checkpoint', checkpoint)
    return NS(**locals())


def test_real_guard_keeps_snapshot_semantics_without_live_expiry_or_admin_grant(scene):
    before = copy.deepcopy(scene.observation.data)
    value = m.guard(scene.db, scene.p, scene.observation)
    assert value['simulation'] is True and value['field_verified'] is False
    assert value['receipt_representation'] == 'canonical_gateway_output_snapshot'
    assert value['native_http_bytes'] is False
    assert value['environment_id'] == scene.env.id and value['release_id'] == scene.release.id
    assert scene.observation.data == before and scene.db.checkpoints == 0


def test_receipt_basis_excludes_only_explicit_three_handoff_projections(scene):
    base = m.receipt_basis(scene.sample.data)
    scene.sample.data.update(task_id='task', signal_id='signal', handoff_issue='temporary downstream issue')
    assert m.receipt_basis(scene.sample.data) == base
    assert m.guard(scene.db, scene.p, scene.observation)['sample_id'] == scene.sample.id
    scene.sample.data['unapproved_receipt_extension'] = 'included, never silently dropped'
    assert m.receipt_basis(scene.sample.data) != base
    with pytest.raises(HTTPException):
        m.guard(scene.db, scene.p, scene.observation)


@pytest.mark.parametrize('cls,identity', [(m.Sample, 'sample'), (m.Environment, 'environment'),
    (m.Definition, 'definition'), (m.Invocation, 'call'), (m.Job, 'sampler'),
    (m.Audit, 'env-audit'), (m.Audit, 'def-audit'), (m.Audit, 'sample-audit'),
    (m.Audit, 'enqueue-audit'), (m.Audit, 'ingest')])
def test_missing_independent_authority_is_not_legacy_or_missing_success(scene, cls, identity):
    scene.db.rows[cls] = [row for row in scene.db.rows[cls] if row.id != identity]
    with pytest.raises(HTTPException) as error:
        m.guard(scene.db, scene.p, scene.observation)
    assert error.value.status_code == 409


@pytest.mark.parametrize('target,key,value', [('origin', 'release_id', 'extra'),
    ('data', 'reported_rule_contract', {}), ('data', 'mqtt_receipt_id', 'mqtt'),
    ('data', 'candidate_only', True), ('data', 'field_units', {'temperature_c': 'F'}),
    ('data', 'source_time_verified', True), ('data', 'acquisition_id', 'another-acquisition')])
def test_mixed_families_and_false_source_claims_are_rejected(scene, target, key, value):
    (scene.origin if target == 'origin' else scene.observation.data)[key] = value
    with pytest.raises(HTTPException):
        m.guard(scene.db, scene.p, scene.observation)


def test_deleting_origin_and_source_markers_still_sees_independent_environment_and_sample(scene):
    del scene.observation.data['simulation_origin']
    del scene.source.data['specification']['simulation_environment_id']
    with pytest.raises(HTTPException):
        m.guard(scene.db, scene.p, scene.observation)


@pytest.mark.parametrize('target,key,value', [('entity', 'namespace', 'real-business'),
    ('sample', 'invocation_id', 'other-call'), ('sample', 'status', 'awaiting_receipt'),
    ('process', 'kind', 'process_reported_observation'), ('call', 'status', 'unknown'),
    ('call', 'idempotency_key', 'replacement-key')])
def test_cross_scope_wrong_call_or_wrong_job_cannot_reuse_original(scene, target, key, value):
    setattr(getattr(scene, target), key, value)
    with pytest.raises(HTTPException):
        m.guard(scene.db, scene.p, scene.observation)


def test_canonical_evidence_must_equal_saved_output_not_a_new_pretty_copy(scene):
    scene.disk['raw'] = scene.disk['raw'] + b'\n'
    with pytest.raises(HTTPException):
        m.guard(scene.db, scene.p, scene.observation)


def test_resealing_modified_origin_or_sample_cannot_replace_independent_audit(scene):
    scene.sample.data['received_at'] = '2026-10-23T00:00:03+00:00'
    with pytest.raises(HTTPException):
        m.guard(scene.db, scene.p, scene.observation)


def test_execution_requires_exact_running_original_processing_lease(scene):
    with pytest.raises(HTTPException):
        m.guard(scene.db, scene.p, scene.observation, execution=True)
    scene.process.status = 'running'
    scene.db.info['worker_lease'] = {'job_id': scene.process.id, 'lease_token': scene.process.lease_token}
    assert m.guard(scene.db, scene.p, scene.observation, execution=True)['simulation'] is True
    assert scene.db.checkpoints == 1
    scene.db.info['worker_lease']['lease_token'] = 'wrong'
    with pytest.raises(HTTPException):
        m.guard(scene.db, scene.p, scene.observation, execution=True)


@pytest.mark.parametrize('exception', [LeaseLost, ExecutionUnauthorized])
def test_lease_and_iam_loss_propagate_without_conversion(scene, monkeypatch, exception):
    scene.process.status = 'running'
    scene.db.info['worker_lease'] = {'job_id': scene.process.id, 'lease_token': scene.process.lease_token}
    def lost(db):
        raise exception('boundary fixture loss')
    monkeypatch.setattr(m, 'checkpoint', lost)
    with pytest.raises(exception):
        m.guard(scene.db, scene.p, scene.observation, execution=True)


def test_unmarked_legacy_observation_with_no_entity_returns_none(scene):
    scene.observation.data.pop('simulation_origin')
    scene.source.data['specification'] = {}
    scene.observation.entity_id = None
    scene.db.rows[m.Sample] = []
    assert m.guard(scene.db, scene.p, scene.observation) is None


def test_unmarked_non_simulated_entity_remains_separate(scene):
    scene.observation.data.pop('simulation_origin')
    scene.source.data['specification'] = {}
    scene.db.rows[m.Sample] = []; scene.db.rows[m.Environment] = []
    assert m.guard(scene.db, scene.p, scene.observation) is None


@pytest.fixture
def recovered_scene(scene, monkeypatch):
    """Canonical parsed GET + real guard; synthetic rows, no transport or DB."""
    s = scene
    s.call.version = 3
    original = m._call_snapshot(s.call)
    original['status'] = 'unknown'; original['version'] = 2
    original['data'].pop('output')
    receipt = {'idempotency_key': s.call.idempotency_key, 'input_hash': s.sample.data['input_hash'],
        'status': 'succeeded', 'output': copy.deepcopy(s.call.data['output'])}
    raw = m._bytes(receipt)
    get_evidence = NS(id='get-receipt', workspace_id=s.p.workspace_id, media_type='application/json',
        size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), data={**m._MARK,
            'purpose': 'parsed_simulation_get_receipt_v176', 'native_http_bytes': False,
            'sample_id': s.sample.id, 'invocation_id': s.call.id, 'recovery_job_id': 'recover'})
    basis = {'sample_id': s.sample.id, 'actor_id': s.sample.actor_id,
        'definition_id': s.definition.id, 'definition_hash': digest(s.definition.data),
        'environment_id': s.env.id, 'environment_hash': digest(s.env.data),
        'instance_id': s.instance.id, 'release_id': s.release.id,
        'original_sampler_job_id': s.sampler.id, 'original_key': s.sampler.idempotency_key,
        'input_hash': s.sample.data['input_hash'], 'gateway_id': s.gateway.id,
        'gateway_hash': s.gateway.content_hash, 'enqueue_audit_id': 'enqueue-audit'}
    instruction = {'schema': 'simulation-sample-recovery-instruction-v176',
        'sample_id': s.sample.id, 'actor_id': s.sample.actor_id, 'original_sampler_job_id': s.sampler.id,
        'sample_version': 2, 'source_basis_hash': digest(basis), 'request_id': 'recovery-request'}
    recovery_job = s.db.put(m.Job, 'recover', kind='recover_simulation_sample_v176', status='succeeded',
        idempotency_key='simulation-sample:unused', data={'sample_id': s.sample.id,
            'actor_id': s.sample.actor_id, 'original_sampler_job_id': s.sampler.id, 'instruction': instruction})
    recovery_job.idempotency_key = 'simulation-sample-recovery:' + s.sample.id + ':' + instruction['request_id']
    recovery = {'schema': 'simulation-sample-recovery-v176', 'job_id': recovery_job.id,
        'request_id': instruction['request_id'], 'original_sampler_job_id': s.sampler.id,
        'source_basis_hash': digest(basis), 'status': 'verified_succeeded', 'previous_recovery_job_id': None,
        'checked_at': '2026-10-23T00:00:02+00:00', 'invocation_id': s.call.id,
        'output_hash': digest(s.call.data['output']), 'original_sample_state': {'status': 'awaiting_receipt',
            'version': 2, 'invocation_id': s.call.id, 'issue': 'original reply unknown'},
        'query_only': True, 'receipt_query_performed': True, 'post_replayed': False}
    s.sample.data['recovery'] = recovery
    proof = {'schema': 'simulation-read-receipt-recovery-v176', 'sample_id': s.sample.id,
        'recovery_job_id': recovery_job.id, 'original_state': original, 'original_state_hash': digest(original),
        'receipt': receipt, 'receipt_hash': digest(receipt), 'receipt_evidence_id': get_evidence.id,
        'receipt_sha256': get_evidence.sha256, 'checked_at': '2026-10-23T00:00:02+00:00',
        'query_only': True, 'post_replayed': False}
    s.call.data['simulation_receipt_recovery_v176'] = proof
    enqueued = s.db.put(m.Audit, 'recovery-enqueue', action='simulation.sample.recovery.enqueued_v176',
        object_id=recovery_job.id, actor_id=s.sample.actor_id, data={'sample_id': s.sample.id,
            'instruction_hash': digest(instruction), 'job_key': recovery_job.idempotency_key})
    checked = s.db.put(m.Audit, 'recovery-checked', action='simulation.sample.recovery.checked_v176',
        object_id=recovery_job.id, actor_id=s.sample.actor_id, data={'sample_id': s.sample.id,
            'status': 'verified_succeeded', 'recovery_hash': digest(recovery),
            'recovery_snapshot': copy.deepcopy(recovery), 'post_replayed': False})
    read = s.db.put(m.Audit, 'recovery-read', action='simulation.sample.receipt_read_v176',
        object_id=s.call.id, actor_id=s.sample.actor_id, data={'sample_id': s.sample.id,
            'recovery_job_id': recovery_job.id, 'receipt_evidence_id': get_evidence.id,
            'receipt_sha256': get_evidence.sha256, 'receipt_hash': proof['receipt_hash'],
            'original_state_hash': proof['original_state_hash'], 'resulting_call_hash': digest(m._call_snapshot(s.call)),
            'query_only': True, 'post_replayed': False})
    def reseal_sample():
        next(row for row in s.db.rows[m.Audit] if row.id == 'sample-audit').data['record_hash'] = digest(m.receipt_basis(s.sample.data))
    def reseal_read():
        read.data['original_state_hash'] = proof['original_state_hash']
        read.data['resulting_call_hash'] = digest(m._call_snapshot(s.call))
    reseal_sample()
    disk = {'raw': raw}
    def evidence_bytes(db, p, identity):
        assert db is s.db and p.workspace_id == s.p.workspace_id
        if identity == s.evidence.id:
            return s.evidence, s.disk['raw']
        assert identity == get_evidence.id
        return get_evidence, disk['raw']
    monkeypatch.setattr(m, 'evidence_bytes', evidence_bytes)
    return NS(scene=s, **{k: v for k, v in locals().items() if k not in {'s', 'scene', 'monkeypatch'}})


def test_recovered_source_retains_original_identity_for_owner_and_other_reader(recovered_scene):
    r = recovered_scene; s = r.scene
    before = copy.deepcopy((s.sample.data, s.call.data))
    owner = m.guard(s.db, s.p, s.observation)
    reader = m.guard(s.db, NS(workspace_id=s.p.workspace_id, user_id='other-authorized-reader'), s.observation)
    assert reader == owner and reader['sample_id'] == s.sample.id and reader['invocation_id'] == s.call.id
    assert before == (s.sample.data, s.call.data) and s.db.checkpoints == 0


@pytest.mark.parametrize('missing', ['sample', 'proof', 'read', 'enqueue', 'checked', 'job'])
def test_recovery_marker_or_independent_authority_missing_cannot_downgrade(recovered_scene, missing):
    r = recovered_scene; s = r.scene
    if missing == 'sample':
        s.sample.data.pop('recovery'); r.reseal_sample()
    elif missing == 'proof':
        s.call.data.pop('simulation_receipt_recovery_v176')
    elif missing == 'job':
        s.db.rows[m.Job].remove(r.recovery_job)
    else:
        s.db.rows[m.Audit].remove({'read': r.read, 'enqueue': r.enqueued, 'checked': r.checked}[missing])
    with pytest.raises(HTTPException) as error:
        m.guard(s.db, s.p, s.observation)
    assert error.value.status_code == 409


def test_read_anchor_alone_prevents_normal_sample_fallback(recovered_scene):
    r = recovered_scene; s = r.scene
    s.sample.data.pop('recovery'); s.call.data.pop('simulation_receipt_recovery_v176'); r.reseal_sample()
    with pytest.raises(HTTPException):
        m.guard(s.db, s.p, s.observation)


@pytest.mark.parametrize('target', ['read_actor', 'job_actor', 'instruction_actor', 'source_basis',
    'original_sampler', 'post_replayed', 'original_lease', 'original_input', 'proof_sample', 'proof_job'])
def test_recovery_identity_and_frozen_original_bindings_cannot_be_resealed(recovered_scene, target):
    r = recovered_scene; s = r.scene
    if target == 'read_actor': r.read.actor_id = 'other-reader'
    elif target == 'job_actor': r.recovery_job.data['actor_id'] = 'other-reader'
    elif target == 'instruction_actor': r.instruction['actor_id'] = 'other-reader'
    elif target == 'source_basis': r.instruction['source_basis_hash'] = 'f' * 64
    elif target == 'original_sampler': r.recovery_job.data['original_sampler_job_id'] = 'other-job'
    elif target == 'post_replayed': r.proof['post_replayed'] = True
    elif target == 'proof_sample': r.proof['sample_id'] = 'other-sample'
    elif target == 'proof_job': r.proof['recovery_job_id'] = s.sampler.id
    else:
        r.original['data']['lease_token' if target == 'original_lease' else 'input_hash'] = 'changed'
        r.proof['original_state_hash'] = digest(r.original)
    r.reseal_read()
    with pytest.raises(HTTPException):
        m.guard(s.db, s.p, s.observation)


@pytest.mark.parametrize('target', ['bytes', 'sha', 'purpose', 'native', 'sample', 'invocation', 'job'])
def test_recovery_get_evidence_is_separately_cold_read(recovered_scene, target):
    r = recovered_scene; s = r.scene
    if target == 'bytes': r.disk['raw'] += b' '
    elif target == 'sha': r.get_evidence.sha256 = 'f' * 64
    elif target == 'purpose': r.get_evidence.data['purpose'] = 'parsed_device_receipt_copy_v176'
    elif target == 'native': r.get_evidence.data['native_http_bytes'] = True
    else: r.get_evidence.data[{'sample': 'sample_id', 'invocation': 'invocation_id', 'job': 'recovery_job_id'}[target]] = 'other'
    with pytest.raises(HTTPException):
        m.guard(s.db, s.p, s.observation)


@pytest.mark.parametrize('same_output', [True, False])
def test_already_successful_call_requires_real_get_and_unchanged_typed_output(recovered_scene, same_output):
    r = recovered_scene; s = r.scene
    r.original['status'] = 'succeeded'
    r.original['data']['output'] = copy.deepcopy(s.call.data['output'])
    if not same_output: r.original['data']['output']['result']['state']['temperature_c'] = 37.0
    r.proof['original_state_hash'] = digest(r.original); r.reseal_read()
    if same_output:
        assert m.guard(s.db, s.p, s.observation)['sample_id'] == s.sample.id
    else:
        with pytest.raises(HTTPException): m.guard(s.db, s.p, s.observation)


def test_history_reader_does_not_acquire_original_processing_execution(recovered_scene):
    s = recovered_scene.scene; s.process.status = 'running'
    s.db.info['worker_lease'] = {'job_id': s.process.id, 'lease_token': s.process.lease_token}
    with pytest.raises(HTTPException):
        m.guard(s.db, NS(workspace_id=s.p.workspace_id, user_id='other-reader'), s.observation, execution=True)


@pytest.fixture
def later_recovery_scene(recovered_scene):
    r = recovered_scene; s = r.scene
    instruction = copy.deepcopy(r.instruction)
    instruction.update(request_id='later-recovery-request', sample_version=3)
    latest = s.db.put(m.Job, 'later-recover', kind='recover_simulation_sample_v176', status='succeeded',
        idempotency_key='simulation-sample-recovery:' + s.sample.id + ':' + instruction['request_id'],
        data={'sample_id': s.sample.id, 'actor_id': s.sample.actor_id,
            'original_sampler_job_id': s.sampler.id, 'instruction': instruction})
    recovery = copy.deepcopy(r.recovery)
    recovery.update(job_id=latest.id, request_id=instruction['request_id'],
        previous_recovery_job_id=r.recovery_job.id, receipt_query_performed=False)
    s.sample.data['recovery'] = recovery
    s.db.put(m.Audit, 'later-enqueue', action='simulation.sample.recovery.enqueued_v176',
        object_id=latest.id, actor_id=s.sample.actor_id, data={'sample_id': s.sample.id,
            'instruction_hash': digest(instruction), 'job_key': latest.idempotency_key})
    s.db.put(m.Audit, 'later-checked', action='simulation.sample.recovery.checked_v176',
        object_id=latest.id, actor_id=s.sample.actor_id, data={'sample_id': s.sample.id,
            'status': 'verified_succeeded', 'recovery_hash': digest(recovery),
            'recovery_snapshot': copy.deepcopy(recovery), 'post_replayed': False})
    r.reseal_sample()
    r.latest = latest
    return r


def test_later_recovery_job_can_consume_original_get_proof_with_both_job_identities_preserved(later_recovery_scene):
    r = later_recovery_scene; s = r.scene
    assert r.proof['recovery_job_id'] != s.sample.data['recovery']['job_id']
    assert m.guard(s.db, NS(workspace_id=s.p.workspace_id, user_id='other-reader'), s.observation)['invocation_id'] == s.call.id
    s.db.rows[m.Audit].remove(r.enqueued)
    with pytest.raises(HTTPException):
        m.guard(s.db, s.p, s.observation)


def test_failed_get_job_derivation_keeps_saved_read_authority_for_direct_successor(later_recovery_scene):
    r = later_recovery_scene; s = r.scene
    r.recovery_job.status = 'failed'
    before = copy.deepcopy((r.proof, s.call.data, r.original))
    assert m.guard(s.db, NS(workspace_id=s.p.workspace_id, user_id='other-reader'), s.observation)['sample_id'] == s.sample.id
    assert before == (r.proof, s.call.data, r.original)


@pytest.mark.parametrize('status', ['queued', 'running', 'cancelled', 'waiting_parent'])
def test_nonterminal_or_cancelled_read_job_is_never_an_inherited_authority(later_recovery_scene, status):
    r = later_recovery_scene; r.recovery_job.status = status
    with pytest.raises(HTTPException): m.guard(r.scene.db, r.scene.p, r.scene.observation)


def test_same_failed_job_cannot_claim_latest_success(recovered_scene):
    r = recovered_scene; r.recovery_job.status = 'failed'
    with pytest.raises(HTTPException): m.guard(r.scene.db, r.scene.p, r.scene.observation)


@pytest.mark.parametrize('previous', [None, 'unrelated-recovery'])
def test_failed_read_job_requires_exact_direct_predecessor(later_recovery_scene, previous):
    r = later_recovery_scene; s = r.scene; r.recovery_job.status = 'failed'
    s.sample.data['recovery']['previous_recovery_job_id'] = previous
    checked = next(row for row in s.db.rows[m.Audit]
        if row.action == 'simulation.sample.recovery.checked_v176' and row.object_id == r.latest.id)
    checked.data['recovery_snapshot'] = copy.deepcopy(s.sample.data['recovery'])
    checked.data['recovery_hash'] = digest(s.sample.data['recovery']); r.reseal_sample()
    with pytest.raises(HTTPException): m.guard(s.db, s.p, s.observation)


@pytest.mark.parametrize('target', ['basis', 'lease', 'key', 'raw', 'owner'])
def test_failed_get_job_inheritance_cannot_relax_original_proof(later_recovery_scene, target):
    r = later_recovery_scene; s = r.scene; r.recovery_job.status = 'failed'
    if target == 'basis': r.instruction['source_basis_hash'] = 'f' * 64
    elif target == 'lease': r.original['data']['lease_token'] = 'other-lease'
    elif target == 'key': r.original['idempotency_key'] = 'other-request'
    elif target == 'raw': r.disk['raw'] += b' '
    else: r.recovery_job.data['actor_id'] = 'other-owner'
    r.proof['original_state_hash'] = digest(r.original); r.reseal_read()
    with pytest.raises(HTTPException): m.guard(s.db, s.p, s.observation)
