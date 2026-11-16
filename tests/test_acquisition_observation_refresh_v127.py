"""Real observation worker/reception/acquisition flow, with a stale ORM witness.

Uses only the existing localhost supplier and builtin numeric extractor. The
refresh/receive spies call their actual implementations; named corruption and
lease takeover faults do not patch any source, evidence, receipt or lease gate.
Root alone executes these tests against the disposable PostgreSQL service.
"""
import copy
from contextlib import nullcontext
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import inspect as orm_inspect, select, func
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import leases, perception, worker
from generic_agents.db import engine
from generic_agents.models import (Acquisition, CloudCall, Event, Invocation, Job,
    Memory, Observation, SensorReservation, now)
from tests.test_native_goal_acquisitions import prepared
from tests.test_sensing_selection import supplier, dispatch
from tests.test_vertical_slice import manifest, publish


def observation_job(client, supplier, *, verification='unverified_extraction', terminal=True):
    value = prepared(client, supplier, verification=verification)
    if not terminal:
        supplier['modes'][value['asset']['external_id']] = 'running'
    dispatch(value['monitor'], value['acquisition_id'])
    with Session(engine()) as db:
        acquisition = db.get(Acquisition, value['acquisition_id'])
        assert acquisition.status == 'awaiting_observation'
        assert acquisition.data['device_action_terminal'] is terminal
        before_calls = db.scalar(select(func.count()).select_from(Invocation))
    extractor = publish(client, manifest('tool', name='refresh_numeric_' + uuid4().hex[:8],
        allowed_agent_roles=['perception'], execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        specification={'rules': []}))
    at = now()
    response = client.post('/api/observations', json={'source_id': value['source']['id'],
        'source_record_id': uuid4().hex, 'entity_id': value['obj']['id'], 'observed_at': at.isoformat(),
        'business_day': str(at.date()), 'attributes': {value['field']: value['obj']['attributes'][value['field']]},
        'extractor_id': extractor['id'], 'startup_mode': 'automatic', 'acquisition_id': value['acquisition_id']})
    assert response.status_code == 201, response.text
    observation_id = response.json()['id']
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.idempotency_key == 'extract:' + observation_id))
        assert job.kind == 'process_observation' and job.status == 'queued'
        assert 'data_reception' not in db.get(Observation, observation_id).data
        job_id = job.id
    return {**value, 'observation_id': observation_id, 'job_id': job_id, 'extractor': extractor,
        'before_invocations': before_calls}


def watch_real_handoff(monkeypatch, value, *, fault=None):
    """Observe the exact worker object across the actual other-Session commit."""
    seen = {'refreshes': 0, 'receives': 0, 'dispatch_commits': 0}
    actual_agent, actual_dispatch = worker.perception_agent, worker.deliver_events
    actual_refresh, actual_receive = Session.refresh, perception.receive_acquisition_observation

    def agent(db, principal, observation, *args, **kwargs):
        result = actual_agent(db, principal, observation, *args, **kwargs)
        if observation.id == value['observation_id']:
            seen['worker_session'] = db
            seen['worker_object'] = observation
            seen['processed_cache'] = copy.deepcopy(orm_inspect(observation).dict['data'])
            assert 'data_reception' not in seen['processed_cache']
        return result

    def dispatched(*args, **kwargs):
        result = actual_dispatch(*args, **kwargs)
        if seen['dispatch_commits']:
            return result
        with Session(engine()) as other:
            observation = other.get(Observation, value['observation_id'])
            if observation is None or 'data_reception' not in observation.data:
                return result
            event = other.scalar(select(Event).where(Event.idempotency_key == 'data:' + observation.id,
                Event.workspace_id == observation.workspace_id))
            memories = list(other.scalars(select(Memory).where(Memory.workspace_id == observation.workspace_id,
                Memory.data['event_id'].as_string() == event.id,
                Memory.data['type'].as_string() == 'observation_evidence')))
            assert event.status == 'received' and len(memories) == 1
            assert observation.data['data_reception'] == event.data['data_reception'] == memories[0].data['data_reception']
            seen['dispatch_commits'] += 1
            seen['committed_reception'] = copy.deepcopy(observation.data['data_reception'])
            seen['other_session_data_before_fault'] = copy.deepcopy(observation.data)
            if fault == 'event':
                event.data = {**event.data, 'data_reception': {**event.data['data_reception'], 'receipt_hash': 'f' * 64}}
            elif fault == 'invocation':
                call = other.get(Invocation, observation.data['invocation_id'])
                output = copy.deepcopy(call.data['output']); output['attributes'][value['field']] = 'corrupted actual invocation output'
                call.data = {**call.data, 'output': output}
            elif fault == 'memory':
                memories[0].data = {**memories[0].data,
                    'data_reception': {**memories[0].data['data_reception'], 'receipt_hash': 'f' * 64}}
            elif fault == 'fake_field':
                observation.data = {**observation.data,
                    'data_reception': {'schema': 'pretend-received', 'receipt_hash': 'f' * 64}}
            other.commit()
        if fault == 'takeover':
            # The old worker has committed/released its Job before dispatch.
            # Renewals are disabled only for this deterministic takeover test.
            with Session(engine()) as other:
                job = other.get(Job, value['job_id'])
                seen['old_token'] = job.lease_token
                job.lease_until = now() - timedelta(seconds=1); other.commit()
            acquired = worker.claim(value['job_id'])
            assert acquired and acquired[1] != seen['old_token']
            seen['new_token'] = acquired[1]
        return result

    def refresh(db, instance, *args, **kwargs):
        identity = orm_inspect(instance).identity
        tracked = (seen['dispatch_commits'] == 1 and isinstance(instance, Observation) and identity == (value['observation_id'],)
            and db.info.get('worker_lease', {}).get('job_id') == value['job_id'])
        if tracked:
            assert db is seen['worker_session'] and instance is seen['worker_object']
            assert 'data_reception' not in orm_inspect(instance).dict['data']
            seen['stale_before_refresh'] = copy.deepcopy(orm_inspect(instance).dict['data'])
        result = actual_refresh(db, instance, *args, **kwargs)
        if tracked:
            seen['refreshes'] += 1
            seen['full_refreshed_data'] = copy.deepcopy(instance.data)
            with Session(engine()) as other:
                assert instance.data == other.get(Observation, value['observation_id']).data
        return result

    def receive(db, principal, observation):
        if observation.id == value['observation_id']:
            seen['receives'] += 1
            seen['used_data'] = copy.deepcopy(observation.data)
            seen['used_status'] = observation.status
        return actual_receive(db, principal, observation)

    monkeypatch.setattr(worker, 'perception_agent', agent)
    monkeypatch.setattr(worker, 'deliver_events', dispatched)
    monkeypatch.setattr(Session, 'refresh', refresh)
    monkeypatch.setattr(perception, 'receive_acquisition_observation', receive)
    return seen


def assert_single_real_extraction(value, supplier):
    with Session(engine()) as db:
        observation = db.get(Observation, value['observation_id'])
        assert observation.status == 'processed'
        calls = list(db.scalars(select(Invocation).where(Invocation.capability_id == value['extractor']['id'])))
        assert len(calls) == 1 and calls[0].status == 'succeeded'
        assert observation.data['invocation_id'] == calls[0].id
        assert db.scalar(select(func.count()).select_from(Invocation)) == value['before_invocations'] + 1
        assert db.scalar(select(CloudCall.id)) is None
    assert len(supplier['calls']) == 1 and not supplier.get('read_calls')


def test_worker_reloads_actual_committed_reception_before_closing_original_acquisition(client, supplier, monkeypatch):
    value = observation_job(client, supplier)
    seen = watch_real_handoff(monkeypatch, value)
    assert worker.process_once(value['job_id'])
    assert seen['dispatch_commits'] == seen['refreshes'] == seen['receives'] == 1
    assert 'data_reception' not in seen['stale_before_refresh']
    assert seen['used_status'] == 'processed'
    assert seen['used_data'] == seen['full_refreshed_data'] == seen['other_session_data_before_fault']
    assert seen['used_data']['data_reception'] == seen['committed_reception']
    assert {key: seen['used_data'][key] for key in seen['processed_cache']} == seen['processed_cache']
    with Session(engine()) as db:
        acquisition = db.get(Acquisition, value['acquisition_id'])
        assert acquisition.status == 'succeeded' and acquisition.data['closing_observation_id'] == value['observation_id']
        assert db.scalar(select(SensorReservation).where(SensorReservation.acquisition_id == acquisition.id)) is None
        job = db.get(Job, value['job_id']); assert job.status == 'succeeded' and job.attempts == 1
        assert db.get(Observation, value['observation_id']).data == seen['used_data']
    assert_single_real_extraction(value, supplier)


@pytest.mark.parametrize('fault', ['event', 'invocation', 'memory', 'fake_field'])
def test_refresh_does_not_turn_corrupt_proof_or_a_fake_reception_field_into_acquisition_success(client, supplier, monkeypatch, fault):
    value = observation_job(client, supplier)
    seen = watch_real_handoff(monkeypatch, value, fault=fault)
    assert worker.process_once(value['job_id'])
    assert seen['dispatch_commits'] == seen['refreshes'] == seen['receives'] == 1
    assert 'data_reception' not in seen['stale_before_refresh']
    assert seen['used_data'] == seen['full_refreshed_data']
    with Session(engine()) as db:
        acquisition = db.get(Acquisition, value['acquisition_id'])
        assert acquisition.status == 'awaiting_observation' and not acquisition.data.get('closing_observation_id')
        assert db.scalar(select(SensorReservation).where(SensorReservation.acquisition_id == acquisition.id)) is not None
        observation = db.get(Observation, value['observation_id'])
        if fault == 'fake_field': assert observation.data['data_reception']['schema'] == 'pretend-received'
        elif fault == 'event':
            event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + observation.id))
            assert event.data['data_reception']['receipt_hash'] == 'f' * 64
        elif fault == 'memory':
            event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + observation.id))
            memory = db.scalar(select(Memory).where(Memory.data['event_id'].as_string() == event.id,
                Memory.data['type'].as_string() == 'observation_evidence'))
            assert memory.data['data_reception']['receipt_hash'] == 'f' * 64
        else: assert db.get(Invocation, observation.data['invocation_id']).data['output']['attributes'][value['field']] == 'corrupted actual invocation output'
    assert_single_real_extraction(value, supplier)


@pytest.mark.parametrize('blocked', ['human_review', 'nonterminal_device'])
def test_complete_fresh_receipt_still_requires_original_quality_and_device_terminal_evidence(client, supplier, monkeypatch, blocked):
    value = observation_job(client, supplier, verification='confirmed' if blocked == 'human_review' else 'unverified_extraction',
        terminal=blocked != 'nonterminal_device')
    seen = watch_real_handoff(monkeypatch, value)
    assert worker.process_once(value['job_id'])
    assert seen['dispatch_commits'] == seen['refreshes'] == seen['receives'] == 1
    assert seen['used_data']['data_reception'] == seen['committed_reception']
    with Session(engine()) as db:
        acquisition = db.get(Acquisition, value['acquisition_id'])
        assert acquisition.status == 'awaiting_observation' and not acquisition.data.get('closing_observation_id')
        assert db.scalar(select(SensorReservation).where(SensorReservation.acquisition_id == acquisition.id)) is not None
        assert db.get(Job, value['job_id']).status == 'succeeded'
    assert_single_real_extraction(value, supplier)


def test_refresh_new_transaction_keeps_original_job_lease_fence_after_actual_takeover(client, supplier, monkeypatch):
    value = observation_job(client, supplier)
    monkeypatch.setattr(leases, 'heartbeat', lambda *args: nullcontext())
    seen = watch_real_handoff(monkeypatch, value, fault='takeover')
    assert worker.process_once(value['job_id'])
    assert seen['dispatch_commits'] == 1 and 'new_token' in seen
    assert seen['refreshes'] == seen['receives'] == 0
    with Session(engine()) as db:
        job = db.get(Job, value['job_id'])
        assert job.status == 'running' and job.lease_token == seen['new_token'] and job.attempts == 2
        acquisition = db.get(Acquisition, value['acquisition_id'])
        assert acquisition.status == 'awaiting_observation' and not acquisition.data.get('closing_observation_id')
        assert db.scalar(select(SensorReservation).where(SensorReservation.acquisition_id == acquisition.id)) is not None
    assert_single_real_extraction(value, supplier)
