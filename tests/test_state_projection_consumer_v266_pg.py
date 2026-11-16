"""Root-run PostgreSQL state projection over normal producer and ACK paths.

Numeric inputs use normal reported-observation APIs and the real local SDK.
Pixel/sound positives require the installed CPU models; synthetic RGB/WAV only
prove engineering behavior. No producer result, ACK or ObjectState is invented.
The only substitutions below observe dispatch or inject explicit negative faults.
Assertions inspect committed ObjectState/Job/Audit before any state GET, because
that GET itself recomputes the projection and cannot prove consumer execution.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event as ThreadEvent
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import state_projection_consumer_v266 as consumer
from generic_agents import state_refresh, worker
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.sound_models import EnvironmentSoundRun
from tests.test_instance_workspace import OBJECTS, object_row, enable
from tests.test_perception_signals import setup as numeric_setup
from tests.test_vertical_slice import manifest, publish
from tests.test_workflow_job_dependencies_v264_pg import flow_scene, native_scene, _lost_final_commit
from tests.test_workflow_pixel_receipt_status_v263_pg import pytestmark as native_pytestmark
from tests.test_workflow_sound_native_pg import sound_scene, original_scene


MANUAL_ACTION = 'object_state.refresh_requested'


def _good(response, status=200):
    assert response.status_code == status, response.text[:1000]
    return response.json()


def _pin(row):
    extras = tuple((name, getattr(row, name)) for name in (
        'status', 'kind', 'attempts', 'lease_token', 'lease_until', 'available_at',
        'actor_id', 'object_id', 'action', 'entity_id', 'source_id', 'observed_at',
        'capability_id', 'content_hash', 'validated_hash', 'configuration_hash',
        'owner_id', 'package_id', 'received_at', 'active', 'role', 'idempotency_key',
        'namespace', 'entity_type', 'schema_capability_id', 'source_record_id',
        'processing_version', 'external_id', 'release_number', 'release_version',
        'instance_id', 'reserved_tokens', 'charged_tokens') if hasattr(row, name))
    return (getattr(row, 'version', None), digest(row.data), extras)


def _protected(workspace_id):
    """Saved sources/results/authority must survive projection without rewrites."""
    with Session(engine()) as db:
        result = {cls.__name__: {row.id: _pin(row) for row in db.scalars(
            select(cls).where(cls.workspace_id == workspace_id))}
            for cls in (m.Entity, m.Task, m.Observation, m.Event, m.Memory,
                m.Invocation, m.CloudCall, m.Capability, m.InstanceRelease, m.DomainInstance,
                m.WorkflowPixelRun, EnvironmentSoundRun)}
        result['Audit'] = {row.id: _pin(row) for row in db.scalars(select(m.Audit).where(
            m.Audit.workspace_id == workspace_id,
            m.Audit.action != 'object_state.background_checked'))}
        return result


def _jobs(workspace_id, *, entity_id=None, observation_id=None):
    with Session(engine()) as db:
        query = select(m.Job).where(m.Job.workspace_id == workspace_id, m.Job.kind == state_refresh.KIND)
        if entity_id is not None:
            query = query.where(m.Job.data['state_request']['entity_id'].as_string() == entity_id)
        if observation_id is not None:
            query = query.where(m.Job.data['state_request']['trigger_observation_id'].as_string() == observation_id)
        return [row.id for row in db.scalars(query.order_by(m.Job.created_at, m.Job.id))]


def _job_stock(workspace_id, *, except_ids=()):
    with Session(engine()) as db:
        return {row.id: _pin(row) for row in db.scalars(select(m.Job).where(
            m.Job.workspace_id == workspace_id)) if row.id not in set(except_ids)}


def _current_jobs(workspace_id, observation_id):
    with Session(engine()) as db:
        observation = db.get(m.Observation, observation_id)
        return list(db.scalars(select(m.Job.id).where(m.Job.workspace_id == workspace_id,
            m.Job.kind == state_refresh.KIND,
            m.Job.data['state_request']['trigger_observation_id'].as_string() == observation_id,
            m.Job.data['state_request']['trigger_observation_version'].as_integer() == observation.version)))


def _step(s, **scope):
    value = consumer.step(s.workspace_id, s.release['instance_id'], **scope)
    assert {'processed', 'after', 'waiting', 'errors', 'job_id'} <= set(value)
    assert type(value['processed']) is bool
    assert value['after'] is None or (isinstance(value['after'], tuple)
        and len(value['after']) == 2 and isinstance(value['after'][0], datetime)
        and isinstance(value['after'][1], str))
    assert value['job_id'] is None or isinstance(value['job_id'], str)
    assert type(value['waiting']) is list and type(value['errors']) is list
    for item in [*value['waiting'], *value['errors']]:
        assert isinstance(item['reason'], str) and item['reason']
    return value


def _watch_dispatch(monkeypatch):
    original = worker.process_once
    calls = []

    def observed(*args, **kwargs):
        identity = args[0] if args else kwargs.get('job_id')
        assert identity is not None, 'consumer must dispatch the exact original Job'
        assert kwargs.get('kinds') == [state_refresh.KIND]
        assert kwargs.get('deliver') is False
        calls.append((identity, copy.copy(kwargs)))
        return original(*args, **kwargs)

    monkeypatch.setattr(worker, 'process_once', observed)
    return calls


def _no_state(s, entity_id=None):
    with Session(engine()) as db:
        assert db.scalar(select(m.ObjectState.id).where(m.ObjectState.workspace_id == s.workspace_id,
            m.ObjectState.entity_id == (entity_id or s.entity['id']))) is None


def _producer(s, observation_id, *, receive=True):
    with Session(engine()) as db:
        rows = list(db.scalars(select(m.Job).where(m.Job.workspace_id == s.workspace_id,
            m.Job.kind == 'process_reported_observation',
            m.Job.data['observation_id'].as_string() == observation_id)))
        assert len(rows) == 1
        identity = rows[0].id
    assert worker.process_once(identity, kinds=['process_reported_observation'], deliver=False,
        workspace_id=s.workspace_id)
    with Session(engine()) as db:
        job = db.get(m.Job, identity)
        observation = db.get(m.Observation, observation_id)
        assert job.status == 'succeeded' and observation.status == 'processed'
        calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == s.workspace_id,
            m.Invocation.id == observation.data['invocation_id'])))
        assert len(calls) == 1 and calls[0].status == 'succeeded'
        assert calls[0].idempotency_key == 'observation:' + observation_id + ':attempt:1'
        assert calls[0].data['output'] == observation.data['extracted']
        proof = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == 'capability.invoke', m.Audit.object_id == calls[0].id)))
        assert len(proof) == 1 and proof[0].data == {'status': 'succeeded'}
    if receive:
        worker.deliver_events(workspace_id=s.workspace_id, observation_id=observation_id,
            event_kind='observation.data')
        _ack(s, observation_id)
    return identity


def _ack(s, observation_id):
    from generic_agents import instance_data_receipts
    with Session(engine()) as db:
        observation = db.get(m.Observation, observation_id)
        p = Principal(observation.data['submitted_by'], s.workspace_id, 'admin', 'v266-cold-ack-check')
        receipt = instance_data_receipts.observation_receipt(db, p, observation)['data_reception']
        assert receipt['observation_id'] == observation.id and receipt['entity_id'] == observation.entity_id
        assert receipt['source_id'] == observation.source_id
        assert receipt['receipt_hash'] == digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
        source_event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + observation_id))
        assert source_event.status == 'received' and source_event.received_at is not None
        memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == s.workspace_id,
            m.Memory.data['event_id'].as_string() == source_event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence')))
        assert len(memories) == 1 and memories[0].status == 'candidate'
        assert memories[0].data['data_reception'] == receipt
        return SimpleNamespace(event_id=source_event.id, memory_id=memories[0].id,
            receipt=copy.deepcopy(receipt))


@pytest.fixture
def numeric_scene(client, account):
    release, entity, observation, body, producer_id = numeric_setup(client, run=False)
    return SimpleNamespace(client=client, workspace_id=account[2], release=release,
        entity=entity, observation=observation, body=body, producer_id=producer_id)


def _register(s, *, entity=None, source_id=None, observed_at=None, value=65):
    body = {**s.body, 'source_record_id': str(uuid4()), 'entity_id': (entity or s.entity)['id'],
        'source_id': source_id or s.body['source_id'], 'attributes': {'公开登记值': value}}
    if observed_at is not None:
        body['observed_at'] = observed_at.isoformat()
        body['business_day'] = str((observed_at + timedelta(hours=8)).date())
    return _good(s.client.post('/api/workspace/instances/' + s.release['instance_id']
        + '/reported-observations', json=body), 201)


def _saved_projection(s, job_id, *, entity=None, observation_id=None):
    """This fresh connection is the proof; a subsequent API GET is only UI parity."""
    entity = entity or s.entity
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        assert job.workspace_id == s.workspace_id and job.kind == state_refresh.KIND
        assert job.status == 'succeeded' and job.attempts >= 1
        request = state_refresh.checked(job)
        actual = db.get(m.Entity, entity['id'])
        member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.workspace_id,
            m.Membership.user_id == job.data['actor_id']))
        assert member is not None
        p = Principal(job.data['actor_id'], s.workspace_id, member.role, 'v266-cold-projection-check')
        target = state_refresh.target(db, p, actual)
        assert target is not None and {key: request[key] for key in target} == target
        assert request['instance_id'] == s.release['instance_id']
        assert request['authorized_by'] == job.data['actor_id']
        assert request['trigger_observation_id'] == observation_id
        assert actual.version == entity['version'] and actual.data == entity['attributes']
        state = db.scalar(select(m.ObjectState).where(m.ObjectState.workspace_id == s.workspace_id,
            m.ObjectState.entity_id == entity['id']))
        assert state is not None
        snapshot = copy.deepcopy(state.data)
        result = job.data['state_result']
        assert digest(result) == job.data['state_result_hash']
        assert result['state_hash'] == snapshot['state_hash']
        assert result['state_version'] == state.version and result['status'] == snapshot['status']
        assert result['instance_id'] == request['instance_id'] and result['entity_id'] == actual.id
        assert result['cloud_calls'] == result['device_actions'] == 0
        assert result['entity_updated'] is result['physical_state_verified'] is False
        assert snapshot['physical_state'] == 'unknown' and snapshot['current_state_verified'] is False
        proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == 'object_state.background_checked', m.Audit.object_id == actual.id,
            m.Audit.data['job_id'].as_string() == job.id)))
        assert proofs and all(row.actor_id == request['authorized_by'] and row.data == {
            'job_id': job.id, 'state_version': state.version, 'state_hash': snapshot['state_hash']}
            for row in proofs)
        return SimpleNamespace(data=snapshot, version=state.version, request=copy.deepcopy(request),
            job_data=copy.deepcopy(job.data), proof_ids=[row.id for row in proofs])


def _manual(s, request_id=None, *, client=None):
    body = {'request_id': request_id or str(uuid4())}
    url = '/api/workspace/instances/' + s.release['instance_id'] + '/objects/' + s.entity['id'] + '/refresh-state'
    return (client or s.client).post(url, json=body), body, url


def _manual_proof(s, job_id, body):
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == MANUAL_ACTION, m.Audit.data['job_id'].as_string() == job_id)))
        assert len(rows) == 1
        proof = rows[0]
        assert proof.actor_id == job.data['actor_id'] and proof.object_id == job.id
        assert proof.data == {'job_id': job.id, 'entity_id': s.entity['id'],
            'instance_id': s.release['instance_id'], 'request_id': body['request_id'],
            'idempotency_key': job.idempotency_key, 'state_request_hash': job.data['state_request_hash'],
            'actor_id': job.data['actor_id']}
        assert state_refresh.checked(job)['trigger_observation_id'] is None
        return proof.id


def test_instance_scan_uses_every_actual_source_without_per_source_configuration(numeric_scene, monkeypatch):
    s = numeric_scene
    second_source = publish(s.client, manifest('source', name='v266_second_actual_source',
        allowed_agent_roles=['perception'], execution={'adapter': 'upload'},
        specification={'timezone': 'Asia/Shanghai'}, data_policy={'cloud_allowed': False}))
    second_entity = object_row(s.client, s.release, index=1)
    second = _register(s, entity=second_entity, source_id=second_source['id'], value=72)
    for observation in (s.observation, second):
        _producer(s, observation['id'])
    all_ids = _jobs(s.workspace_id)
    ids = [*_current_jobs(s.workspace_id, s.observation['id']),
        *_current_jobs(s.workspace_id, second['id'])]
    assert len(ids) == 2
    untouched = _job_stock(s.workspace_id, except_ids=ids)
    _no_state(s)
    _no_state(s, second_entity['id'])
    before = _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    after = None
    for _ in range(len(all_ids) + 2):
        outcome = _step(s, after=after, limit=1)
        after = outcome['after']
    assert calls and {identity for identity, _ in calls} == set(ids)
    assert not any(kwargs.get('workspace_id') not in {None, s.workspace_id} for _, kwargs in calls)
    for entity, observation, value in ((s.entity, s.observation, 65), (second_entity, second, 72)):
        last = next(identity for identity, _ in reversed(calls)
            if identity in _jobs(s.workspace_id, observation_id=observation['id']))
        saved = _saved_projection(s, last, entity=entity, observation_id=observation['id'])
        assert saved.data['fields']['公开登记值']['value'] == value
        candidate = saved.data['fields']['公开登记值']['candidates'][0]
        assert candidate['data_reception_verified'] is True
        assert candidate['data_reception_receipt_hash'] == _ack(s, observation['id']).receipt['receipt_hash']
        view = _good(s.client.get('/api/workspace/instances/' + s.release['instance_id']
            + '/objects/' + entity['id'] + '/state'))
        assert view['state']['state_hash'] == saved.data['state_hash']
        assert view['state']['state_version'] == saved.version
    assert _protected(s.workspace_id) == before
    with Session(engine()) as db:
        assert all(_pin(db.get(m.Job, key)) == value for key, value in untouched.items())


@pytest.mark.parametrize('scope_name', ['entity_id', 'source_id', 'observation_id', 'job_id'])
def test_optional_selector_only_narrows_the_real_instance_queue(numeric_scene, monkeypatch, scope_name):
    s = numeric_scene
    other_entity = object_row(s.client, s.release, index=1)
    other = _register(s, entity=other_entity)
    for observation in (s.observation, other):
        _producer(s, observation['id'])
    chosen = _jobs(s.workspace_id, observation_id=s.observation['id'])[-1]
    values = {'entity_id': s.entity['id'], 'source_id': s.body['source_id'],
        'observation_id': s.observation['id'], 'job_id': chosen}
    # Source narrowing intentionally includes both actual objects sharing it.
    eligible = set([*_current_jobs(s.workspace_id, s.observation['id']),
        *_current_jobs(s.workspace_id, other['id'])]) if scope_name == 'source_id' else (
        {chosen} if scope_name == 'job_id' else set(_current_jobs(s.workspace_id, s.observation['id'])))
    untouched = _job_stock(s.workspace_id, except_ids=eligible)
    before = _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    for _ in range(len(eligible) + 1):
        _step(s, **{scope_name: values[scope_name]})
    assert calls and {identity for identity, _ in calls} <= eligible
    # Original unselected Jobs cannot be claimed, cancelled or rewritten.
    with Session(engine()) as db:
        assert all(_pin(db.get(m.Job, key)) == value for key, value in untouched.items())
    assert _protected(s.workspace_id) == before


def test_instance_scope_leaves_other_instance_and_other_job_kinds_untouched(numeric_scene, monkeypatch):
    s = numeric_scene
    # Same workspace/configuration, explicit new-instance intent; never copy a
    # release row or invent a source destination to simulate namespace isolation.
    draft = _good(s.client.get('/api/setup/draft'))
    actor = _good(s.client.get('/api/me'))['user_id']
    body = {'revision': draft['revision'], 'objects': copy.deepcopy(OBJECTS),
        'role_bindings': {'核验负责人': actor}}
    checked = _good(s.client.post('/api/instances/check', json=body))
    release = _good(s.client.post('/api/instances', json={**body,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True,
        'generation_intent': 'new_instance', 'request_id': str(uuid4())}), 201)
    assert release['instance_id'] != s.release['instance_id']
    enable(s.client, release)
    foreign_entity = object_row(s.client, release)
    foreign = SimpleNamespace(client=s.client, workspace_id=s.workspace_id, release=release,
        entity=foreign_entity, body={**s.body, 'entity_id': foreign_entity['id']})
    foreign_observation = _register(foreign)
    _producer(s, s.observation['id'])
    _producer(foreign, foreign_observation['id'])
    eligible = _current_jobs(s.workspace_id, s.observation['id'])
    with Session(engine()) as db:
        for kind in ('execute_plan', 'diagnose_data', 'dispatch_acquisition',
            'process_observation', 'execute_workflow_step', 'install_specialist'):
            db.add(m.Job(workspace_id=s.workspace_id, kind=kind, status='queued',
                idempotency_key='v266-negative-inert-' + uuid4().hex,
                data={'actor_id': actor, 'negative_inert_scope_probe_v266': True}))
        db.commit()
    before = _protected(s.workspace_id)
    untouched = _job_stock(s.workspace_id, except_ids=eligible)
    calls = _watch_dispatch(monkeypatch)
    values = [_step(s) for _ in range(3)]
    assert sum(value['processed'] for value in values) == 1
    assert [identity for identity, _ in calls] == eligible
    _saved_projection(s, eligible[0], observation_id=s.observation['id'])
    with Session(engine()) as db:
        assert all(_pin(db.get(m.Job, key)) == value for key, value in untouched.items())
    _no_state(foreign)
    assert _protected(s.workspace_id) == before


def test_conflicting_optional_source_entity_and_observation_cannot_broaden_scope(numeric_scene, monkeypatch):
    s = numeric_scene
    other_entity = object_row(s.client, s.release, index=1)
    other_source = publish(s.client, manifest('source', name='v266_selector_other_source',
        allowed_agent_roles=['perception'], execution={'adapter': 'upload'},
        specification={'timezone': 'Asia/Shanghai'}, data_policy={'cloud_allowed': False}))
    other = _register(s, entity=other_entity, source_id=other_source['id'])
    for observation in (s.observation, other):
        _producer(s, observation['id'])
    chosen = _current_jobs(s.workspace_id, s.observation['id'])[0]
    jobs, protected = _job_stock(s.workspace_id), _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    for narrowed in ({'source_id': other_source['id']}, {'observation_id': other['id']},
            {'entity_id': other_entity['id']}):
        assert not _step(s, job_id=chosen, **narrowed)['processed']
    assert not calls and _job_stock(s.workspace_id) == jobs and _protected(s.workspace_id) == protected
    _no_state(s)
    _no_state(s, other_entity['id'])


def test_missing_independent_ack_is_waiting_and_consumer_never_receives_it(numeric_scene, monkeypatch):
    s = numeric_scene
    class OutboxUnavailable(RuntimeError): pass
    receiver_calls = []
    def receiver_unavailable(*args, **kwargs):
        assert not args and kwargs.get('workspace_id') == s.workspace_id
        assert kwargs.get('observation_id') == s.observation['id']
        receiver_calls.append(copy.copy(kwargs))
        raise OutboxUnavailable('disposable post-commit independent receiver failure')
    # The real reported producer commits Job/SDK before its mandatory targeted
    # local_workspace receiver, even with deliver=False. Fail only that call;
    # do not invent a successful producer or withdraw an existing ACK.
    with monkeypatch.context() as patch:
        patch.setattr(worker, 'deliver_events', receiver_unavailable)
        with pytest.raises(OutboxUnavailable, match='post-commit independent receiver failure'):
            worker.process_once(s.producer_id, kinds=['process_reported_observation'],
                deliver=False, workspace_id=s.workspace_id)
        with Session(engine()) as db:
            producer = db.get(m.Job, s.producer_id)
            observation = db.get(m.Observation, s.observation['id'])
            assert producer.status == 'succeeded' and observation.status == 'processed'
            assert observation.data.get('candidate_only', False) is False
            observation_version = observation.version
            calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == s.workspace_id,
                m.Invocation.idempotency_key.startswith('observation:' + observation.id + ':'))))
            assert len(calls) == 1 and calls[0].status == 'succeeded'
            assert calls[0].id == observation.data['invocation_id'] and calls[0].capability_id == s.body['extractor_id']
            assert calls[0].idempotency_key == 'observation:' + observation.id + ':attempt:1'
            assert calls[0].data['output'] == observation.data['extracted']
            sdk_proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
                m.Audit.action == 'capability.invoke', m.Audit.object_id == calls[0].id)))
            assert len(sdk_proofs) == 1 and sdk_proofs[0].data == {'status': 'succeeded'}
            assert sdk_proofs[0].actor_id == producer.data['actor_id']
        # A reported-record review commits normally and does not call the
        # candidate-only receiver. It provides a real current-version trigger.
        _good(s.client.post('/api/observations/' + s.observation['id'] + '/verify', json={
            'expected_version': observation_version, 'decision': 'confirmed',
            'reason': '公开工程登记已核对；独立接收尚未完成'}))
        assert len(receiver_calls) == 1
    with Session(engine()) as db:
        observation = db.get(m.Observation, s.observation['id'])
        source_event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + observation.id))
        assert observation.version == observation_version + 1
        assert observation.data['verification_history'][-1] == observation.data['human_verification']
        assert observation.data['human_verification']['decision'] == 'confirmed'
        proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == 'observation.verified', m.Audit.object_id == observation.id)))
        assert len(proofs) == 1 and proofs[0].actor_id == observation.data['human_verification']['reviewed_by']
        assert source_event.status == 'pending_delivery' and source_event.received_at is None
        assert 'data_reception' not in observation.data and 'data_reception' not in source_event.data
        assert db.scalar(select(m.Memory.id).where(m.Memory.workspace_id == s.workspace_id,
            m.Memory.data['event_id'].as_string() == source_event.id,
            m.Memory.data['type'].as_string() == 'observation_evidence')) is None
    ids = _current_jobs(s.workspace_id, s.observation['id'])
    assert len(ids) == 1
    before = _protected(s.workspace_id)
    jobs = _job_stock(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    outcomes = [_step(s) for _ in range(2)]
    assert not calls and all(not value['processed'] for value in outcomes)
    assert any(item['job_id'] in ids and item['state'] == 'waiting_data'
        for value in outcomes for item in value['waiting'])
    assert _protected(s.workspace_id) == before and _job_stock(s.workspace_id) == jobs
    _no_state(s)


@pytest.mark.parametrize('damage', [None, 'review_audit', 'history_hash'])
def test_other_real_reviewer_projects_original_issuer_data_only_with_complete_review_proof(numeric_scene, monkeypatch, damage):
    s = numeric_scene
    _producer(s, s.observation['id'])
    with Session(engine()) as db:
        original = db.get(m.Observation, s.observation['id'])
        original_issuer = original.data['submitted_by']
        expected_version = original.version
        original_call_id = original.data['invocation_id']
        original_call = _pin(db.get(m.Invocation, original_call_id))
        source_event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace_id,
            m.Event.idempotency_key == 'data:' + original.id))
        original_event = _pin(source_event)
        event_id = source_event.id
    username, password = 'v266_reviewer_' + uuid4().hex[:12], uuid4().hex
    reviewer = _good(s.client.post('/api/users', json={
        'username': username, 'password': password, 'role': 'operator'}), 201)
    assert reviewer['id'] != original_issuer
    reason = '对照原登记核对工程数值；核查人与原提交人分离，未证明现场真值'
    with TestClient(s.client.app) as client:
        login = _good(client.post('/api/login', json={'username': username, 'password': password}))
        client.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': s.workspace_id})
        assert _good(client.get('/api/me'))['user_id'] == reviewer['id']
        _good(client.post('/api/observations/' + s.observation['id'] + '/verify', json={
            'expected_version': expected_version, 'decision': 'confirmed', 'reason': reason}))
    ids = _current_jobs(s.workspace_id, s.observation['id'])
    assert len(ids) == 1
    identity = ids[0]
    with Session(engine()) as db:
        observation = db.get(m.Observation, s.observation['id'])
        assert observation.version == expected_version + 1
        assert observation.data['submitted_by'] == original_issuer
        assert observation.data['human_verification']['reviewed_by'] == reviewer['id']
        assert observation.data['verification_history'][-1] == observation.data['human_verification']
        job = db.get(m.Job, identity)
        assert job.data['actor_id'] == reviewer['id']
        assert state_refresh.checked(job)['reason'] == '人工核查结果已保存'
        proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == 'observation.verified', m.Audit.object_id == observation.id)))
        assert len(proofs) == 1 and proofs[0].actor_id == reviewer['id']
        assert proofs[0].data == {'decision': 'confirmed', 'reason': reason}
        assert _pin(db.get(m.Invocation, original_call_id)) == original_call
        assert _pin(db.get(m.Event, event_id)) == original_event
        if damage == 'review_audit': db.delete(proofs[0])
        elif damage == 'history_hash':
            observation.data = {**observation.data, 'verification_history_hash': '0' * 64}
        db.commit()
    protected = _protected(s.workspace_id)
    jobs = _job_stock(s.workspace_id)
    _no_state(s)
    calls = _watch_dispatch(monkeypatch)
    outcome = _step(s)
    if damage is None:
        assert outcome['processed'] and [key for key, _ in calls] == [identity]
        saved = _saved_projection(s, identity, observation_id=s.observation['id'])
        assert saved.data['fields']['公开登记值']['value'] == 65
    else:
        assert not outcome['processed'] and not calls
        assert any(item['job_id'] == identity and item['state'] == 'blocked_integrity'
            for item in outcome['waiting']) or outcome['errors']
        assert _job_stock(s.workspace_id) == jobs
        _no_state(s)
    assert _protected(s.workspace_id) == protected


@pytest.mark.parametrize('damage', ['request_hash', 'result_hash', 'ack_memory', 'ack_event',
    'source_hash', 'extracted_hash', 'release_hash', 'entity_version', 'actor', 'paused'])
def test_bad_scope_or_evidence_never_dispatches_or_repairs(numeric_scene, monkeypatch, damage):
    s = numeric_scene
    _producer(s, s.observation['id'])
    chosen = _jobs(s.workspace_id, observation_id=s.observation['id'])[-1]
    ack = _ack(s, s.observation['id'])
    with Session(engine()) as db:
        job = db.get(m.Job, chosen)
        if damage == 'request_hash':
            job.data = {**job.data, 'state_request_hash': '0' * 64}
        elif damage == 'result_hash':
            job.data = {**job.data, 'state_result': {'status': 'forged'}, 'state_result_hash': '0' * 64}
        elif damage == 'ack_memory':
            db.delete(db.get(m.Memory, ack.memory_id))
        elif damage == 'ack_event':
            row = db.get(m.Event, ack.event_id)
            row.data = {**row.data, 'entity_id': str(uuid4())}
        elif damage == 'source_hash':
            db.get(m.Capability, s.body['source_id']).content_hash = '0' * 64
        elif damage == 'extracted_hash':
            row = db.get(m.Observation, s.observation['id'])
            data = copy.deepcopy(row.data)
            data['extracted']['attributes']['公开登记值'] = 999
            row.data = data
        elif damage == 'release_hash':
            db.get(m.InstanceRelease, s.release['id']).configuration_hash = '0' * 64
        elif damage == 'entity_version':
            db.get(m.Entity, s.entity['id']).version += 1
        elif damage == 'actor':
            db.get(m.User, job.data['actor_id']).active = False
        elif damage == 'paused':
            db.get(m.DomainInstance, s.release['instance_id']).status = 'paused'
        db.commit()
    before = _protected(s.workspace_id)
    jobs = _job_stock(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    values = [_step(s, job_id=chosen) for _ in range(2)]
    assert not calls and all(not value['processed'] for value in values)
    assert any(value['errors'] or any(item['state'] in {'blocked_integrity', 'blocked_authorization'}
        for item in value['waiting']) for value in values)
    assert _protected(s.workspace_id) == before and _job_stock(s.workspace_id) == jobs
    _no_state(s)


def test_manual_original_api_has_independent_audit_and_needs_no_observation(numeric_scene, monkeypatch):
    s = numeric_scene
    # The original input remains genuinely queued. Manual reconciliation is
    # authorized by its own API audit and must not pretend extraction happened.
    response, body, url = _manual(s)
    job_id = _good(response)['id']
    proof_id = _manual_proof(s, job_id, body)
    assert _good(s.client.post(url, json=body))['id'] == job_id
    assert _manual_proof(s, job_id, body) == proof_id
    _no_state(s)
    protected = _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    assert _step(s, job_id=job_id)['processed']
    assert [identity for identity, _ in calls] == [job_id]
    saved = _saved_projection(s, job_id)
    assert saved.data['fields'] == {} and saved.data['status'] == 'insufficient'
    assert _protected(s.workspace_id) == protected
    assert _good(s.client.post(url, json=body))['id'] == job_id
    assert _manual_proof(s, job_id, body) == proof_id


def test_original_manual_nonce_cannot_move_to_another_real_entity(numeric_scene):
    s = numeric_scene
    response, body, _ = _manual(s)
    identity = _good(response)['id']
    _manual_proof(s, identity, body)
    other = object_row(s.client, s.release, index=1)
    stock, protected = _job_stock(s.workspace_id), _protected(s.workspace_id)
    url = '/api/workspace/instances/' + s.release['instance_id'] + '/objects/' + other['id'] + '/refresh-state'
    assert s.client.post(url, json=body).status_code == 409
    assert _job_stock(s.workspace_id) == stock and _protected(s.workspace_id) == protected
    _no_state(s)
    _no_state(s, other['id'])


@pytest.mark.parametrize('removed', ['audit', 'job'])
def test_manual_replay_does_not_recreate_either_missing_authority_side(numeric_scene, removed):
    s = numeric_scene
    response, body, url = _manual(s)
    identity = _good(response)['id']
    proof_id = _manual_proof(s, identity, body)
    with Session(engine()) as db:
        db.delete(db.get(m.Audit if removed == 'audit' else m.Job,
            proof_id if removed == 'audit' else identity))
        db.commit()
    before = _protected(s.workspace_id)
    jobs = _job_stock(s.workspace_id)
    assert s.client.post(url, json=body).status_code == 409
    assert _protected(s.workspace_id) == before and _job_stock(s.workspace_id) == jobs


def test_old_manual_job_without_request_audit_stays_unclaimed(numeric_scene, monkeypatch):
    s = numeric_scene
    with Session(engine()) as db:
        entity = db.get(m.Entity, s.entity['id'])
        actor = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.workspace_id))
        p = Principal(actor.user_id, s.workspace_id, actor.role, 'v266-historical-manual-fixture')
        nonce = str(uuid4())
        # Real legacy enqueue path; no independent positive proof is fabricated.
        job = state_refresh.enqueue(db, p, entity, '用户请求本地重新核对', 'manual:' + p.user_id + ':' + nonce)
        job_id = job.id
        db.commit()
    stock = _job_stock(s.workspace_id)
    protected = _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    value = _step(s, job_id=job_id)
    assert not value['processed'] and not calls
    assert value['waiting'] or value['errors']
    assert _job_stock(s.workspace_id) == stock and _protected(s.workspace_id) == protected
    _no_state(s)


def test_real_operator_new_nonce_recovers_after_original_issuer_revocation(numeric_scene, monkeypatch):
    s = numeric_scene
    response, original_body, _ = _manual(s)
    original = _good(response)['id']
    _manual_proof(s, original, original_body)
    username, password = 'v266_operator_' + uuid4().hex[:12], uuid4().hex
    operator = _good(s.client.post('/api/users', json={
        'username': username, 'password': password, 'role': 'operator'}), 201)
    with Session(engine()) as db:
        issuer = db.get(m.Job, original).data['actor_id']
        assert issuer != operator['id']
        db.get(m.User, issuer).active = False
        db.commit()
    with TestClient(s.client.app) as client:
        login = _good(client.post('/api/login', json={'username': username, 'password': password}))
        client.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': s.workspace_id})
        response, body, _ = _manual(s, client=client)
        fresh = _good(response)['id']
        assert fresh != original
        _manual_proof(s, fresh, body)
        stock = _job_stock(s.workspace_id, except_ids=[fresh])
        calls = _watch_dispatch(monkeypatch)
        assert not _step(s, job_id=original)['processed'] and not calls
        assert _step(s, job_id=fresh)['processed']
        assert [identity for identity, _ in calls] == [fresh]
        _saved_projection(s, fresh)
        with Session(engine()) as db:
            assert all(_pin(db.get(m.Job, key)) == value for key, value in stock.items())


def test_two_consumers_dispatch_only_one_original_job_and_persist_one_projection(numeric_scene, monkeypatch):
    s = numeric_scene
    _producer(s, s.observation['id'])
    identity = _jobs(s.workspace_id, observation_id=s.observation['id'])[-1]
    protected = _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    ready = Barrier(2)

    def execute():
        ready.wait(timeout=10)
        return _step(s, job_id=identity)

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: execute(), range(2)))
    assert sum(value['processed'] for value in values) == 1
    saved = _saved_projection(s, identity, observation_id=s.observation['id'])
    assert saved.version == 1 and len(saved.proof_ids) == 1
    with Session(engine()) as db:
        assert db.get(m.Job, identity).attempts == 1
    assert all(job_id == identity for job_id, _ in calls)
    assert not _step(s, job_id=identity)['processed']
    assert _protected(s.workspace_id) == protected


def test_restart_recovers_original_expired_lease_without_reprocessing_source(numeric_scene, monkeypatch):
    s = numeric_scene
    _producer(s, s.observation['id'])
    identity = _jobs(s.workspace_id, observation_id=s.observation['id'])[-1]
    lease = worker.claim(identity, kinds=[state_refresh.KIND], workspace_id=s.workspace_id)
    assert lease and lease[0] == identity
    with Session(engine()) as db:
        job = db.get(m.Job, identity)
        assert job.status == 'running' and job.lease_token == lease[1] and job.attempts == 1
        job.lease_until = db.scalar(text("SELECT clock_timestamp() - INTERVAL '1 second'"))
        db.commit()
    protected = _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    assert _step(s, job_id=identity)['processed']
    saved = _saved_projection(s, identity, observation_id=s.observation['id'])
    assert saved.version == 1 and len(saved.proof_ids) == 1
    with Session(engine()) as db:
        job = db.get(m.Job, identity)
        assert job.attempts == 2 and job.lease_token != lease[1]
        assert job.data['lease_recoveries'] == 1
    assert [job_id for job_id, _ in calls] == [identity]
    assert _protected(s.workspace_id) == protected


def test_restart_after_committed_projection_before_finalizer_preserves_expiry_and_ssot(numeric_scene, monkeypatch):
    s = numeric_scene
    _producer(s, s.observation['id'])
    identity = _current_jobs(s.workspace_id, s.observation['id'])[0]
    protected = _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    with _lost_final_commit(identity) as lost:
        assert _step(s, job_id=identity)['processed']
        assert len(lost) == 1
        with Session(engine()) as db:
            job = db.get(m.Job, identity)
            assert job.status == 'running' and job.attempts == 1 and job.lease_token == lost[0]
            state = db.scalar(select(m.ObjectState).where(m.ObjectState.workspace_id == s.workspace_id,
                m.ObjectState.entity_id == s.entity['id']))
            assert state is not None and state.version == 1
            frozen = copy.deepcopy(state.data)
            expiry_id = job.data['next_expiry_job_id']
            assert job.data['state_result']['state_hash'] == state.data['state_hash']
            assert db.get(m.Job, expiry_id).status == 'queued'
        assert not _step(s, job_id=identity)['processed']
    with Session(engine()) as db:
        job = db.get(m.Job, identity)
        job.lease_until = db.scalar(text("SELECT clock_timestamp() - INTERVAL '1 second'"))
        db.commit()
    # A new consumer iteration, fresh connection, and real worker lease recovery.
    assert _step(s, job_id=identity)['processed']
    saved = _saved_projection(s, identity, observation_id=s.observation['id'])
    assert saved.data == frozen and saved.version == 1 and len(saved.proof_ids) == 2
    assert saved.job_data['next_expiry_job_id'] == expiry_id
    with Session(engine()) as db:
        assert db.get(m.Job, identity).attempts == 2
        expiries = list(db.scalars(select(m.Job.id).where(m.Job.workspace_id == s.workspace_id,
            m.Job.kind == state_refresh.KIND, m.Job.status == 'queued',
            m.Job.data['state_request']['due_state_hash'].as_string().is_not(None))))
        assert expiries == [expiry_id]
    assert [job_id for job_id, _ in calls] == [identity, identity]
    assert _protected(s.workspace_id) == protected


def test_stopped_step_and_run_leave_queued_projection_and_source_unchanged(numeric_scene, monkeypatch):
    s = numeric_scene
    _producer(s, s.observation['id'])
    stop = ThreadEvent()
    stop.set()
    stock = _job_stock(s.workspace_id)
    protected = _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    assert not _step(s, stop_event=stop)['processed']
    consumer.run(s.workspace_id, s.release['instance_id'], stop_event=stop)
    assert not calls and _job_stock(s.workspace_id) == stock and _protected(s.workspace_id) == protected
    _no_state(s)


def test_run_finishes_current_projection_and_stops_before_another_job(numeric_scene, monkeypatch):
    s = numeric_scene
    _producer(s, s.observation['id'])
    identities = _jobs(s.workspace_id, observation_id=s.observation['id'])
    assert identities
    stop = ThreadEvent()

    def stop_after_finish(db):
        if any(isinstance(row, m.Job) and row.id in identities and row.status == 'succeeded'
            and row.data.get('finished_at') for row in db.identity_map.values()):
            stop.set()

    calls = _watch_dispatch(monkeypatch)
    event.listen(Session, 'before_commit', stop_after_finish)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(consumer.run, s.workspace_id, s.release['instance_id'], stop_event=stop)
            try:
                future.result(timeout=30)
            finally:
                stop.set()
    finally:
        stop.set()
        event.remove(Session, 'before_commit', stop_after_finish)
    assert len(calls) == 1
    _saved_projection(s, calls[0][0], observation_id=s.observation['id'])
    stock = _job_stock(s.workspace_id)
    assert not _step(s, stop_event=stop)['processed'] and _job_stock(s.workspace_id) == stock


def test_expiry_uses_committed_original_parent_result_and_audit_at_real_due_time(numeric_scene, monkeypatch):
    s = numeric_scene
    # Normal API supplies a historical declared time that expires shortly. The
    # database deadline and application clock are never rewritten to force due.
    clock = datetime.now(timezone.utc) - timedelta(days=1) + timedelta(seconds=8)
    observation = _register(s, observed_at=clock, value=27.5)
    _producer(s, observation['id'])
    original = _jobs(s.workspace_id, observation_id=observation['id'])[-1]
    assert _step(s, job_id=original)['processed']
    parent = _saved_projection(s, original, observation_id=observation['id'])
    assert parent.data['fields']['公开登记值']['value'] == 27.5
    expiry_id = parent.job_data['next_expiry_job_id']
    with Session(engine()) as db:
        expiry = db.get(m.Job, expiry_id)
        request = state_refresh.checked(expiry)
        due = expiry.available_at
        assert expiry.status == 'queued' and expiry.attempts == 0
        assert request['trigger_observation_id'] is None and request['reason'] == '登记依据时效到期'
        assert request['due_state_hash'] == parent.data['state_hash']
        assert expiry.idempotency_key == ('state-refresh:expiry:' + s.entity['id'] + ':'
            + parent.data['state_hash'] + ':' + original)
        assert datetime.fromisoformat(parent.job_data['next_expiry_at']) == due
    protected = _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    deadline = monotonic() + 12
    while datetime.now(timezone.utc) < due:
        assert monotonic() < deadline, 'real expiry deadline exceeded bounded test wait'
        sleep(.05)
    assert _step(s, job_id=expiry_id)['processed']
    expired = _saved_projection(s, expiry_id)
    assert {'field': '公开登记值', 'reason': 'stale'} in expired.data['gaps']
    assert expired.version > parent.version and [identity for identity, _ in calls] == [expiry_id]
    assert _protected(s.workspace_id) == protected


@pytest.mark.parametrize('damage', ['parent_audit', 'parent_result_hash'])
def test_due_expiry_cannot_use_missing_or_corrupt_original_parent_proof(numeric_scene, monkeypatch, damage):
    s = numeric_scene
    clock = datetime.now(timezone.utc) - timedelta(days=1) + timedelta(seconds=4)
    observation = _register(s, observed_at=clock)
    _producer(s, observation['id'])
    original = _current_jobs(s.workspace_id, observation['id'])[0]
    assert _step(s, job_id=original)['processed']
    parent = _saved_projection(s, original, observation_id=observation['id'])
    expiry_id = parent.job_data['next_expiry_job_id']
    with Session(engine()) as db:
        due = db.get(m.Job, expiry_id).available_at
        if damage == 'parent_audit':
            assert len(parent.proof_ids) == 1
            db.delete(db.get(m.Audit, parent.proof_ids[0]))
        else:
            job = db.get(m.Job, original)
            job.data = {**job.data, 'state_result_hash': '0' * 64}
        db.commit()
    protected, jobs = _protected(s.workspace_id), _job_stock(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    deadline = monotonic() + 8
    while datetime.now(timezone.utc) < due:
        assert monotonic() < deadline, 'real expiry deadline exceeded bounded test wait'
        sleep(.05)
    outcome = _step(s, job_id=expiry_id)
    assert not outcome['processed'] and not calls
    assert outcome['errors'] or any(item['state'] == 'blocked_integrity' for item in outcome['waiting'])
    assert _protected(s.workspace_id) == protected and _job_stock(s.workspace_id) == jobs
    with Session(engine()) as db:
        snapshot = db.scalar(select(m.ObjectState).where(m.ObjectState.workspace_id == s.workspace_id,
            m.ObjectState.entity_id == s.entity['id']))
        assert snapshot.data == parent.data and snapshot.version == parent.version


@native_pytestmark
def test_native_pixel_ack_projection_preserves_unknown_clock_and_candidate_truth(flow_scene, monkeypatch):
    source = flow_scene
    s = SimpleNamespace(client=source.client, workspace_id=source.workspace_id,
        release=source.release, entity=source.entity)
    entry = _good(s.client.get('/api/workspace/tasks/' + source.task_id + '/pixel-results'))['records'][0]
    assert entry['reception']['state'] == 'waiting_data'
    observation_id = entry['reception']['observation_id']
    _no_state(s)
    stock = _protected(s.workspace_id)
    assert not _step(s)['processed']
    assert _protected(s.workspace_id) == stock
    worker.deliver_events(workspace_id=s.workspace_id, observation_id=observation_id,
        event_kind='observation.data')
    _ack(s, observation_id)
    identity = _jobs(s.workspace_id, observation_id=observation_id)[-1]
    with Session(engine()) as db:
        observation = db.get(m.Observation, observation_id)
        run = db.get(m.WorkflowPixelRun, entry['run']['id'])
        task = db.get(m.Task, source.task_id)
        assert run.job_id == source.job_id and task.data['instance_id'] == s.release['instance_id']
        assert db.get(m.Job, run.job_id).status == 'succeeded'
        assert db.get(m.Invocation, run.invocation_id).status == 'succeeded'
        assert observation.observed_at is None
    stock = _protected(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    assert _step(s)['processed']
    saved = _saved_projection(s, identity, observation_id=observation_id)
    assert saved.data['fields'] == {} and saved.data['status'] == 'insufficient'
    assert any(item['observation_id'] == observation_id and item['source_clock_utc'] == 'unknown'
        and item['not_authoritative'] is True for item in saved.data['unclocked_candidates'])
    assert _protected(s.workspace_id) == stock and [job_id for job_id, _ in calls] == [identity]
    view = _good(s.client.get('/api/workspace/instances/' + s.release['instance_id']
        + '/objects/' + s.entity['id'] + '/state'))
    assert view['state']['state_hash'] == saved.data['state_hash']


def test_native_sound_candidate_stays_held_until_explicit_local_recheck(sound_scene, account, monkeypatch):
    source = sound_scene
    s = SimpleNamespace(client=source.client, workspace_id=account[2],
        release=source.current_release, entity=source.entity)
    observation_id = source.entry['reception']['observation_id']
    assert source.entry['reception']['state'] == 'waiting_data'
    _no_state(s)
    worker.deliver_events(workspace_id=s.workspace_id, observation_id=observation_id,
        event_kind='observation.data')
    received = _ack(s, observation_id)
    with Session(engine()) as db:
        run = db.get(EnvironmentSoundRun, source.run['id'])
        task = db.get(m.Task, source.task['id'])
        observation = db.get(m.Observation, observation_id)
        assert run.job_id == source.job_id and task.data['instance_id'] == s.release['instance_id']
        assert db.get(m.Job, run.job_id).status == 'succeeded'
        assert db.get(m.Invocation, run.invocation_id).status == 'succeeded'
        assert observation.observed_at is None
        source_event = db.get(m.Event, received.event_id)
        assert source_event.status == 'received'
        held = source_event.data['derivation_receipt']
        assert held['schema'] == 'observation-derivation-skipped-v1'
        assert held['event_id'] == source_event.id and held['observation_id'] == observation.id
        assert held['code'] == 'environment_sound_candidate_only'
        assert held['skipped'] == ['data_gaps', 'state_refresh', 'goal_queries', 'anomalies', 'quality']
        assert held['cloud_calls'] == 0 and held['device_actions'] is False and held['current_state_updated'] is False
        assert held['receipt_hash'] == digest({key: value for key, value in held.items() if key != 'receipt_hash'})
        skipped = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.action == 'observation.derivation_skipped', m.Audit.object_id == source_event.id)))
        assert len(skipped) == 1 and skipped[0].actor_id == observation.data['submitted_by']
        assert skipped[0].data == held
        assert db.get(m.Memory, received.memory_id).data['derivation_receipt'] == held
    assert _jobs(s.workspace_id, observation_id=observation_id) == []
    held_stock = _protected(s.workspace_id)
    held_jobs = _job_stock(s.workspace_id)
    calls = _watch_dispatch(monkeypatch)
    assert not _step(s)['processed'] and not calls
    _no_state(s)
    assert _protected(s.workspace_id) == held_stock and _job_stock(s.workspace_id) == held_jobs

    # Candidate-only ACK does not authorize automatic state derivation. A
    # current authorized person explicitly requests a separate local recheck
    # through the normal API; no source-trigger Job or trigger Obs is invented.
    response, body, _ = _manual(s)
    identity = _good(response)['id']
    _manual_proof(s, identity, body)
    assert _jobs(s.workspace_id, observation_id=observation_id) == []
    _no_state(s)
    stock = _protected(s.workspace_id)  # Include the legitimately saved manual request Audit.
    assert _step(s, job_id=identity)['processed']
    saved = _saved_projection(s, identity)
    assert saved.request['trigger_observation_id'] is None
    assert saved.data['fields'] == {} and saved.data['status'] == 'insufficient'
    assert any(item['observation_id'] == observation_id and item['source_clock_utc'] == 'unknown'
        and item['not_authoritative'] for item in saved.data['unclocked_candidates'])
    assert _protected(s.workspace_id) == stock and [job_id for job_id, _ in calls] == [identity]
    view = _good(s.client.get('/api/workspace/instances/' + s.release['instance_id']
        + '/objects/' + s.entity['id'] + '/state'))
    assert view['state']['state_hash'] == saved.data['state_hash']
