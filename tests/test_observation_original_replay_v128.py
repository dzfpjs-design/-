"""Real original POST recovery, without patching authority or model results.

Root executes these against the owned disposable PostgreSQL/storage fixture.
The file-extractor case only registers a queued input; it never invokes a
cloud provider or changes a budget.  SQL observers record or synchronize real
queries and never substitute a lookup, receipt, permission, or result.
"""
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event as sql_event, select, text
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import ObservationCreate, digest
from generic_agents.db import engine
from generic_agents.models import (Acquisition, Audit, Capability, DataRun, Entity, Event, Evidence,
    Invocation, Job, Memory, Membership, Observation, ObservationPlan, SensorAssetEvent, Task)
from generic_agents.observation_original_replay_v128 import validate
from generic_agents.security import Principal
from generic_agents.services import as_dict
from generic_agents.worker import deliver_events, process_once
from tests.test_original_acquisition_reception_v127 import run_reception_journey
from tests.test_sensing_selection import supplier, setup as sensing_setup, asset, monitor, scan, dispatch
from tests.test_vertical_slice import entity, manifest, publish


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _principal(client):
    me = _ok(client.get('/api/me'))
    return Principal(me['user_id'], me['workspace_id'], me['role'], 'original-post-read-only-test')


def _database():
    """Every public table/row, without rewriting or omitting any Task field."""
    with engine().connect() as connection:
        connection.execute(text("SET TIME ZONE 'UTC'"))
        tables = list(connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")))
        rows = []
        for table in tables:
            quoted = connection.dialect.identifier_preparer.quote(table)
            values = sorted(connection.scalars(text('SELECT row_to_json(t)::text FROM public.' + quoted + ' AS t')))
            rows.append([table, values])
    encoded = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    return {'sha256': hashlib.sha256(encoded).hexdigest(), 'counts': {name: len(values) for name, values in rows}}


def _files():
    root = settings().storage_dir
    rows = []
    if root.exists():
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink(): rows.append([relative, 'symlink', str(path.readlink())])
            elif path.is_dir(): rows.append([relative + '/', path.stat().st_mode & 0o777])
            else: rows.append([relative, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode & 0o777])
    return {'sha256': hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest(), 'entries': len(rows)}


def _snapshot(): return {'database': _database(), 'files': _files()}


def _basic(client, *, value=27):
    package = publish(client, manifest(name='original_post_' + uuid4().hex[:8])); obj = entity(client, package)
    source = publish(client, manifest('source', name='original_input_' + uuid4().hex[:8],
        allowed_agent_roles=['perception'], execution={'adapter': 'upload'}, specification={'timezone': 'UTC'}))
    extractor = publish(client, manifest('tool', name='original_rule_' + uuid4().hex[:8],
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        specification={'rules': [{'id': 'high', 'field': 'temperature', 'operator': 'gt', 'threshold': 90}]}))
    at = datetime.now(timezone.utc)
    return {'source_id': source['id'], 'source_record_id': str(uuid4()), 'entity_id': obj['id'],
        'observed_at': at.isoformat(), 'business_day': str(at.date()), 'attributes': {'temperature': value},
        'extractor_id': extractor['id'], 'startup_mode': 'manual'}


@contextmanager
def _damaged(model, identifier, **changes):
    """Explicit disposable-record corruption; restore exact original columns."""
    with Session(engine()) as db:
        row = db.get(model, identifier); original = {key: copy.deepcopy(getattr(row, key)) for key in changes}
        for key, value in changes.items(): setattr(row, key, value)
        db.commit()
    try: yield
    finally:
        with Session(engine()) as db:
            row = db.get(model, identifier)
            for key, value in original.items(): setattr(row, key, value)
            db.commit()


def run_original_replay_journey(client, supplier, tmp_path):
    """Preserve V127 proof keys and add a genuine closed original-POST readback."""
    proof = run_reception_journey(client, supplier, tmp_path)
    original = copy.deepcopy(proof['ingest']['body']); before = _snapshot()
    calls, reads = len(supplier['calls']), len(supplier['read_calls'])
    response = _ok(client.post(proof['ingest']['path'], json=original), 201)
    assert response['id'] == proof['observation_id'] and response['status'] == 'processed'
    assert response['data']['input_hash'] == proof['ingest']['input_hash']
    assert response['data']['data_reception']['receipt_hash'] == proof['event']['data']['data_reception']['receipt_hash']
    assert _snapshot() == before
    assert len(supplier['calls']) == calls and len(supplier['read_calls']) == reads
    return {**proof, 'original_submission_replay': {'path': proof['ingest']['path'], 'body': original,
        'response': response, 'unchanged': before, 'new_observations': 0, 'new_jobs': 0, 'new_invocations': 0}}


def test_closed_actual_native_original_post_is_read_only_and_all_original_gates_remain(client, supplier, tmp_path):
    proof = run_original_replay_journey(client, supplier, tmp_path)
    body = proof['ingest']['body']; observation_id = proof['observation_id']; p = _principal(client)
    with Session(engine()) as db:
        row = db.get(Observation, observation_id); saved = copy.deepcopy(as_dict(row)); statements = []
        def observed(connection, cursor, statement, parameters, context, executemany): statements.append(statement)
        sql_event.listen(engine(), 'before_cursor_execute', observed)
        try:
            result = validate(db, p, row, ObservationCreate.model_validate(body))
            assert result is row and as_dict(row) == saved
            assert not db.new and not db.dirty and not db.deleted
        finally: sql_event.remove(engine(), 'before_cursor_execute', observed)
        assert statements and all(statement.lstrip().upper().startswith(('SELECT', 'SET TRANSACTION')) for statement in statements)
        assert not any('FOR UPDATE' in statement.upper() or 'FOR SHARE' in statement.upper() for statement in statements)
        assert validate(db, p, None, ObservationCreate.model_validate(body)) is None
    before = _snapshot()
    # A new key is a new submission, not recovery permission for a closed action.
    for changed in ({**body, 'source_record_id': str(uuid4())}, {**body, 'attributes': {**body['attributes'], 'temperature': 96}},
            {**body, 'startup_mode': 'historical'}, {**body, 'sensor_modality': 'image'},
            {**body, 'evidence_ids': [str(uuid4())]}):
        assert client.post('/api/observations', json=changed).status_code in {404, 409, 422}
    assert _snapshot() == before
    # Every existing chain remains necessary; the proof hash is not authority
    # to overlook changed actual records, Invocation or Event/Memory payloads.
    with Session(engine()) as db:
        original = copy.deepcopy(db.get(Observation, observation_id).data)
        plan_id = db.get(Acquisition, proof['acquisition_id']).data['observation_plan_id']
        query_id = db.get(Task, proof['task_id']).data['native_goal_query_id']
        native = copy.deepcopy(db.get(Task, proof['task_id']).data)
        plan = copy.deepcopy(db.get(ObservationPlan, plan_id).data)
        query = copy.deepcopy(db.get(DataRun, query_id).data)
        call = copy.deepcopy(db.get(Invocation, proof['invocation']['id']).data)
        event = copy.deepcopy(db.get(Event, proof['event']['id']).data)
        memory = copy.deepcopy(db.get(Memory, proof['memory']['id']).data)
        acq = copy.deepcopy(db.get(Acquisition, proof['acquisition_id']).data)
    damaged_inputs = copy.deepcopy(original); damaged_inputs['attributes']['temperature'] = 96
    damaged_request = copy.deepcopy(original); damaged_request['original_request']['source_record_id'] = 'replacement'
    damaged_sensor = copy.deepcopy(original); damaged_sensor['sensor_evidence']['device_id'] = str(uuid4())
    cases = [
        (Observation, observation_id, {'data': damaged_inputs}),
        (Observation, observation_id, {'data': damaged_request}),
        (Observation, observation_id, {'data': damaged_sensor}),
        (Observation, observation_id, {'business_day': '2000-01-01'}),
        (Task, proof['task_id'], {'data': {**native, 'native_goal_context_hash': '0' * 64}}),
        (ObservationPlan, plan_id, {'data': {key: value for key, value in plan.items() if key != 'observation_request'}}),
        (DataRun, query_id, {'data': {key: value for key, value in query.items() if key != 'goal_delegation'}}),
        (Acquisition, proof['acquisition_id'], {'data': {**acq, 'closing_observation_id': str(uuid4())}}),
        (Acquisition, proof['acquisition_id'], {'status': 'cancelled'}),
        (Invocation, proof['invocation']['id'], {'data': {**call, 'input_hash': '0' * 64}}),
        (Invocation, proof['invocation']['id'], {'status': 'failed'}),
        (Event, proof['event']['id'], {'status': 'pending_delivery'}),
        (Event, proof['event']['id'], {'data': {**event, 'data_reception': {**event['data_reception'], 'receipt_hash': '0' * 64}}}),
        (Memory, proof['memory']['id'], {'data': {**memory, 'data_reception': {**memory['data_reception'], 'receipt_hash': '0' * 64}}}),
        (Capability, body['source_id'], {'status': 'revoked'}),
        (Capability, body['extractor_id'], {'status': 'revoked'}),
    ]
    for model, identifier, changes in cases:
        with _damaged(model, identifier, **changes):
            corrupted = _snapshot()
            assert client.post('/api/observations', json=body).status_code == 409, (model.__name__, tuple(changes))
            assert _snapshot() == corrupted
    with Session(engine()) as db:
        membership = db.scalars(select(Membership).where(Membership.workspace_id == p.workspace_id, Membership.user_id == p.user_id)).one()
        member_id = membership.id
    with _damaged(Membership, member_id, role='viewer'):
        assert client.post('/api/observations', json=body).status_code == 403
    assert client.post('/api/observations', json=body, headers={'X-Workspace-ID': str(uuid4())}).status_code == 403
    assert _snapshot() == before


@pytest.mark.parametrize('stage', ['queued', 'failed', 'processed_undelivered'])
def test_saved_generic_original_is_recovered_without_new_work_or_claiming_delivery(client, stage):
    body = _basic(client, value=None if stage == 'failed' else 27)
    # The caller loses/discards the successful first reply; only the original
    # body is retained for the second POST.  No transport result is substituted.
    assert client.post('/api/observations', json=body).status_code == 201
    with Session(engine()) as db:
        row = db.scalars(select(Observation).where(Observation.source_record_id == body['source_record_id'])).one()
        identifier = row.id; job_id = db.scalars(select(Job.id).where(Job.idempotency_key == 'extract:' + row.id)).one()
    if stage != 'queued': assert process_once(job_id, deliver=False)
    with Session(engine()) as db:
        saved = copy.deepcopy(as_dict(db.get(Observation, identifier)))
        assert saved['status'] == {'queued': 'received', 'failed': 'failed', 'processed_undelivered': 'processed'}[stage]
        assert 'data_reception' not in saved['data']
        if stage == 'processed_undelivered':
            event = db.scalars(select(Event).where(Event.idempotency_key == 'data:' + identifier)).one()
            assert event.status == 'pending_delivery' and 'data_reception' not in event.data
    before = _snapshot()
    result = _ok(client.post('/api/observations', json=body), 201)
    assert result == saved and _snapshot() == before
    assert result['id'] == identifier and 'data_reception' not in result['data']


def test_standalone_acquisition_only_replays_while_original_is_awaiting(client, supplier):
    obj, source = sensing_setup(client, supplier); selected = asset(client, obj, source)
    policy = monitor(client, obj, source, [selected]); identifier = scan(client, policy)[0]; dispatch(policy, identifier)
    at = datetime.now(timezone.utc)
    extractor = publish(client, manifest('tool', name='standalone_original_' + uuid4().hex[:8],
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules'}, specification={'rules': []}))
    body = {'source_id': source['id'], 'source_record_id': str(uuid4()), 'entity_id': obj['id'], 'observed_at': at.isoformat(),
        'business_day': str(at.date()), 'attributes': {'temperature': 27}, 'extractor_id': extractor['id'],
        'startup_mode': 'automatic', 'acquisition_id': identifier}
    first = _ok(client.post('/api/observations', json=body), 201)
    before = _snapshot(); assert _ok(client.post('/api/observations', json=body), 201)['id'] == first['id']; assert _snapshot() == before
    key = supplier['calls'][0]['request_key']
    supplier['receipts'][key].update(status='completed', terminal=True, no_future_device_activity=True)
    from generic_agents.perception import reconcile_acquisition
    from tests.test_sensing_selection import principal
    with Session(engine()) as db:
        reconcile_acquisition(db, principal(policy), identifier); db.commit()
        job_id = db.scalars(select(Job.id).where(Job.idempotency_key == 'extract:' + first['id'])).one()
    assert process_once(job_id); deliver_events(observation_id=first['id'])
    with Session(engine()) as db: assert db.get(Acquisition, identifier).status == 'succeeded'
    before = _snapshot(); assert client.post('/api/observations', json=body).status_code == 409; assert _snapshot() == before


def test_saved_file_reference_requires_the_original_bytes_and_never_calls_the_registered_cloud_model(client):
    from tests.test_file_perception import setup as file_setup
    obj, source, model, extractor = file_setup(client)
    content = b'public synthetic original temperature=27'
    ev = _ok(client.post('/api/evidence', content=content, headers={'Content-Type': 'text/plain'}), 201)
    at = datetime.now(timezone.utc)
    body = {'source_id': source['id'], 'source_record_id': str(uuid4()), 'entity_id': obj['id'], 'observed_at': at.isoformat(),
        'business_day': str(at.date()), 'extractor_id': extractor['id'], 'startup_mode': 'manual', 'evidence_ids': [ev['id']]}
    first = _ok(client.post('/api/observations', json=body), 201); before = _snapshot()
    assert _ok(client.post('/api/observations', json=body), 201)['id'] == first['id'] and _snapshot() == before
    with Session(engine()) as db:
        evidence = db.get(Evidence, ev['id']); path = settings().storage_dir / evidence.storage_key
        assert path.read_bytes() == content
    try:
        path.write_bytes(b'changed owned synthetic original')
        assert client.post('/api/observations', json=body).status_code == 409
    finally: path.write_bytes(content)
    assert _snapshot() == before


def test_unacquired_sensor_original_survives_actual_health_title_and_binding_review_updates(client, supplier):
    obj, source = sensing_setup(client, supplier); selected = asset(client, obj, source)
    extractor = publish(client, manifest('tool', name='sensor_original_' + uuid4().hex[:8],
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules'}, specification={'rules': []}))
    at = datetime.now(timezone.utc)
    body = {'source_id': source['id'], 'source_record_id': str(uuid4()), 'entity_id': obj['id'],
        'observed_at': at.isoformat(), 'business_day': str(at.date()), 'attributes': {'temperature': 27},
        'extractor_id': extractor['id'], 'startup_mode': 'manual', 'sensor_asset_id': selected['id'], 'sensor_modality': 'numeric'}
    first = _ok(client.post('/api/observations', json=body), 201)
    original_metadata = copy.deepcopy(first['data']['sensor_evidence'])
    changed = _ok(client.patch('/api/sensing/assets/' + selected['id'], json={
        'expected_version': selected['version'], 'title': '已核对健康说明的同一合成设备',
        'specification': {**selected['data'], 'health': 'degraded'}, 'reason': '只更新台账健康说明，不改原输入或身份'}))
    before = _snapshot(); recovered = _ok(client.post('/api/observations', json=body), 201)
    assert recovered['id'] == first['id'] and recovered['data']['sensor_evidence'] == original_metadata and _snapshot() == before
    current = _ok(client.get('/api/sensing/assets/' + selected['id'] + '/device'))
    _ok(client.post('/api/sensing/assets/' + selected['id'] + '/device', json={
        'expected_version': changed['version'], 'device_id': current['device']['id'],
        'device_expected_version': current['device']['version'], 'reconfirm': True, 'reason': '公开核对同一设备，不改写已存原输入'}))
    before = _snapshot(); recovered = _ok(client.post('/api/observations', json=body), 201)
    assert recovered['data']['sensor_evidence'] == original_metadata and _snapshot() == before
    with Session(engine()) as db:
        original = db.scalars(select(SensorAssetEvent).where(SensorAssetEvent.asset_id == selected['id'],
            SensorAssetEvent.data['after']['version'].as_integer() == original_metadata['asset_version'])).one()
        identity, data = original.id, copy.deepcopy(original.data)
    bad_snapshot = copy.deepcopy(data); bad_snapshot['after']['data']['resolution_m'] = 999
    bad_binding = copy.deepcopy(data); bad_binding['binding_after']['device_id'] = str(uuid4())
    for invalid in (bad_snapshot, bad_binding):
        with _damaged(SensorAssetEvent, identity, data=invalid):
            before = _snapshot(); assert client.post('/api/observations', json=body).status_code == 409; assert _snapshot() == before
    assert supplier['calls'] == []


def test_route_identity_does_not_cross_raw_and_reported_and_preserves_original_reported_epoch(client):
    from tests.test_perception_signals import setup as reported_setup
    release, obj, observation, body, job_id = reported_setup(client, run=False)
    path = '/api/workspace/instances/' + release['instance_id'] + '/reported-observations'
    before = _snapshot()
    assert client.post('/api/observations', json=body).status_code == 409
    assert _ok(client.post(path, json=body), 201)['id'] == observation['id'] and _snapshot() == before
    # A legitimate later Entity version does not rewrite the original route's
    # frozen epoch, and must not prevent an exact historical POST readback.
    _ok(client.patch('/api/entities/' + obj['id'], json={'expected_version': obj['version'],
        'attributes': obj['attributes'], 'reason': '重新核对同一登记对象，原处理请求保持不变'}))
    before = _snapshot(); recovered = _ok(client.post(path, json=body), 201)
    assert recovered['id'] == observation['id']
    assert recovered['data']['reported_rule_contract'] == observation['data']['reported_rule_contract']
    assert _snapshot() == before
    second = {**body, 'source_record_id': str(uuid4())}
    raw = _ok(client.post('/api/observations', json=second), 201); before = _snapshot()
    assert client.post(path, json=second).status_code == 409 and _snapshot() == before
    with Session(engine()) as db:
        assert db.get(Job, job_id).kind == 'process_reported_observation'
        assert db.scalars(select(Job).where(Job.idempotency_key == 'extract:' + raw['id'])).one().kind == 'process_observation'


def test_actual_concurrent_first_lookup_miss_creates_one_observation_job_and_ingest_audit(client):
    body = _basic(client); barrier = Barrier(2); guard = Lock(); seen = set(); arrivals = []
    def after_lookup(connection, cursor, statement, parameters, context, executemany):
        normalized = ' '.join(statement.lower().split())
        values = parameters.values() if isinstance(parameters, dict) else parameters
        if (not normalized.startswith('select ') or 'from observations' not in normalized
            or 'observations.source_record_id =' not in normalized or 'observations.processing_version =' not in normalized
            or body['source_record_id'] not in values): return
        with guard:
            if id(connection) in seen: return
            seen.add(id(connection)); arrivals.append(id(connection))
        # The actual SELECT has executed in both independent READ COMMITTED
        # transactions before either may insert; its result is never replaced.
        barrier.wait(timeout=15)
    sql_event.listen(engine(), 'after_cursor_execute', after_lookup)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(client.post, '/api/observations', json=body) for _ in range(2)]
            replies = [future.result(timeout=30) for future in futures]
    finally: sql_event.remove(engine(), 'after_cursor_execute', after_lookup)
    assert len(arrivals) == 2
    assert all(reply.status_code == 201 for reply in replies), [reply.text for reply in replies]
    assert replies[0].json()['id'] == replies[1].json()['id']
    with Session(engine()) as db:
        rows = list(db.scalars(select(Observation).where(Observation.source_id == body['source_id'],
            Observation.source_record_id == body['source_record_id'], Observation.processing_version == '1.0.0')))
        assert len(rows) == 1
        assert len(list(db.scalars(select(Job).where(Job.idempotency_key == 'extract:' + rows[0].id)))) == 1
        assert len(list(db.scalars(select(Audit).where(Audit.action == 'observation.ingest', Audit.object_id == rows[0].id)))) == 1
