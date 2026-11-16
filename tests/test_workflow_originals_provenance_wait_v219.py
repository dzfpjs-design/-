"""V219 wait mechanics, with an explicit synthetic producer-authority IO seam.

These tests do not register a real release, produce a real quality report, or
prove either independent producer Audit.  The V219 PostgreSQL tests own those
boundaries; here the original wait ledger, frozen kernel input, same-Job wake,
and failure fences are exercised without dispatch or remote activity.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import models as m
from generic_agents import workflow_data_quality_v219 as quality
from generic_agents import workflow_provenance_wait_v197 as waits
from generic_agents import workflow_task_context_v196 as reader
from generic_agents import worker
from generic_agents.contracts import digest
from tests.test_workflow_provenance_wait_v197 import (
    scene as legacy_scene, consumer_inputs, producer, identity, no_external,
)


@pytest.fixture
def scene(monkeypatch):
    s = legacy_scene.__wrapped__(monkeypatch)
    s.producer_job, s.producer_call, original = producer(s)
    s.pin = {**original['producer'], 'workspace_id': s.p.workspace_id, 'task_id': s.task.id,
        'capability_id': s.producer_call.capability_id,
        'activation_id': 'synthetic-producer-activation', 'node_id': 'synthetic-producer-node',
        'capability_hash': digest('synthetic registered producer'),
        'graph_hash': digest('synthetic source graph IO'), 'input_hash': digest('synthetic source input IO')}
    report = {'schema': quality.REPORT, 'operation': 'quality', 'producer': copy.deepcopy(s.pin),
        'source_inventory_hash': digest([]), 'entity_context_hash': digest([]),
        'quality_status': 'needs_review',
        'attributes': {key: 0 for key in quality.attributes_schema()['properties']},
        'files': [], 'issues': [], 'duplicate_groups': [], 'digest_groups': [],
        'covered_source_kinds': [], **quality.FLAGS}
    report['report_hash'] = digest(report)
    s.cap.data = quality.manifest(quality.DEDUP).model_dump(mode='json')
    s.node['execution']['output_schema'] = copy.deepcopy(s.cap.data['output_schema'])
    consumer_inputs(s, copy.deepcopy(s.cap.data['execution']),
        {'scope_ref': quality.scope_ref(), 'quality_report': report}, copy.deepcopy(s.cap.data['input_schema']))
    s.fresh_pin = copy.deepcopy(s.pin)
    s.pending_override = None
    s.boundary_error = None
    s.source_checks = []

    def checked_producer(db, principal, task, job, inputs):
        # Explicit authority adapter, not a substitute for V219 cold PG proof.
        assert db is s.db and principal is s.p and task is s.task and job is s.job
        assert inputs == job.data['inputs']
        s.source_checks.append(s.producer_job.status)
        if s.boundary_error is not None:
            raise s.boundary_error
        if s.pending_override is not None:
            raise reader.ProducerPending(*s.pending_override)
        if s.producer_job.status == 'running':
            raise reader.ProducerPending(s.producer_job.id, s.producer_call.id)
        if s.producer_job.status != 'succeeded':
            raise HTTPException(409, 'Synthetic source gate rejects a non-successful producer')
        return copy.deepcopy(s.fresh_pin)

    def consumer_actor(db, workspace_id, actor_id):
        assert db is s.db and workspace_id == s.task.workspace_id and actor_id == s.job.data['actor_id']
        return s.p

    monkeypatch.setattr(quality, 'producer_wait_pin', checked_producer)
    monkeypatch.setattr(worker, 'actor', consumer_actor)
    return s


def test_only_the_exact_registered_dedup_contract_joins_the_new_wait_family():
    cap = NS(data=quality.manifest(quality.DEDUP).model_dump(mode='json'))
    assert waits._family(cap) == 'originals_dedup_v219'
    cap.data = quality.manifest(quality.QUALITY).model_dump(mode='json')
    assert waits._family(cap) is None


@pytest.mark.parametrize('fault', ['side_effect', 'operation', 'cloud'])
def test_malformed_dedup_contract_is_not_a_wait_alias(fault):
    cap = NS(data=quality.manifest(quality.DEDUP).model_dump(mode='json'))
    if fault == 'side_effect': cap.data['execution']['side_effect'] = True
    elif fault == 'operation': cap.data['execution']['operation'] = 'deduplicate'
    else: cap.data['data_policy']['cloud_allowed'] = True
    with pytest.raises(HTTPException) as caught:
        waits._family(cap)
    assert caught.value.status_code == 422


def test_same_dedup_job_waits_then_wakes_only_after_original_producer_finishes(scene):
    s = scene
    original = (s.job.id, s.job.idempotency_key, s.job.attempts)
    assert s.wait() is True
    assert s.job.status == waits.PHASE and s.db.present_call is None
    record = s.job.data[waits.KEY]['history'][0]
    assert len(record['dependencies']) == 1
    dependency = record['dependencies'][0]
    assert dependency['kind'] == 'originals_quality_producer_v219'
    assert dependency['source_path'] == '/quality_report'
    assert dependency['source_hash'] == digest(s.inputs['quality_report'])
    assert dependency['pin'] == s.pin
    assert dependency['invocation_output_hash'] == digest(s.producer_call.data['output'])
    assert s.schedule() == 0 and s.job.status == waits.PHASE
    s.producer_job.status = 'succeeded'
    assert s.schedule() == 1 and s.job.status == 'queued'
    assert (s.job.id, s.job.idempotency_key, s.job.attempts) == original
    assert s.db.present_call is None and s.db.commits == 0
    assert s.db.additions == [m.Audit, m.Audit]
    assert [event.action for event in s.db.events] == [waits.WAIT, waits.WAKE]
    assert s.source_checks == ['running', 'running', 'succeeded']


def test_already_finished_producer_does_not_register_a_wait(scene):
    s = scene; s.producer_job.status = 'succeeded'
    assert s.wait() is False
    assert s.job.status == 'running' and waits.KEY not in s.job.data
    assert s.db.present_call is None and not s.db.additions


@pytest.mark.parametrize('fault', ['other_job', 'other_invocation', 'queued', 'failed'])
def test_pending_signal_must_match_the_actual_running_original(scene, fault):
    s = scene
    pending = [s.producer_job.id, s.producer_call.id]
    if fault == 'other_job': pending[0] = identity('foreign original producer')
    elif fault == 'other_invocation': pending[1] = identity('foreign original invocation')
    else: s.producer_job.status = fault
    s.pending_override = tuple(pending)
    with pytest.raises(HTTPException) as caught:
        s.wait()
    assert caught.value.status_code == 409
    assert s.job.status == 'running' and waits.KEY not in s.job.data
    assert not s.db.additions and s.db.present_call is None


@pytest.mark.parametrize('error', [HTTPException(409, 'Independent producer proof is missing'),
    ValueError('Original producer evidence is unreadable')])
def test_source_gate_errors_are_not_reinterpreted_as_waiting(scene, error):
    s = scene; s.boundary_error = error
    with pytest.raises(type(error)) as caught:
        s.wait()
    assert caught.value is error
    assert waits.KEY not in s.job.data and not s.db.additions
    assert s.db.present_call is None


@pytest.mark.parametrize('fault', ['pin_hash', 'pin_extra', 'output', 'source', 'audit', 'producer_failed'])
def test_dedup_wake_rechecks_full_original_pin_output_input_and_wait_witness(scene, fault):
    s = scene; assert s.wait() is True
    original = (s.job.id, s.job.idempotency_key, s.job.attempts)
    s.producer_job.status = 'succeeded'
    if fault == 'pin_hash': s.fresh_pin['graph_hash'] = digest('different original graph')
    elif fault == 'pin_extra': s.fresh_pin['unregistered_origin'] = True
    elif fault == 'output': s.producer_call.data['output'] = {'changed_original': True}
    elif fault == 'source': s.job.data['inputs']['quality_report']['attributes']['file_count'] = 1
    elif fault == 'audit': s.db.events.clear()
    else: s.producer_job.status = 'failed'
    assert s.schedule() == 0 and s.job.status == 'blocked'
    assert (s.job.id, s.job.idempotency_key, s.job.attempts) == original
    assert s.db.present_call is None
    assert not any(event.action == waits.WAKE for event in s.db.events)
