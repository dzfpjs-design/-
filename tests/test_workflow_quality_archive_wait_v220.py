"""V220 same-Job wait mechanics with an explicit synthetic source IO seam.

No real source, producer Audit, Artifact, permission grant, or formal archive
is manufactured here. The separate V220 PG tests must prove those boundaries.
These tests retain the real wait ledger, input freezing and original wake path.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import models as m
from generic_agents import workflow_data_quality_v219 as quality
from generic_agents import workflow_quality_archive_v220 as archive
from generic_agents import workflow_provenance_wait_v197 as waits
from generic_agents import workflow_task_context_v196 as reader
from generic_agents.contracts import digest
from tests.test_workflow_originals_provenance_wait_v219 import scene as dedup_scene
from tests.test_workflow_provenance_wait_v197 import consumer_inputs, identity, no_external


def output(report):
    return {'schema': quality.RESULT, 'report': copy.deepcopy(report),
        **{key: copy.deepcopy(report[key]) for key in ('quality_status', 'attributes', 'files', 'issues',
            'duplicate_groups', 'digest_groups')}, **quality.FLAGS}


@pytest.fixture
def scene(monkeypatch):
    s = dedup_scene.__wrapped__(monkeypatch)
    quality_report = copy.deepcopy(s.inputs['quality_report'])
    quality_report['producer'] = {**quality_report['producer'],
        'job_id': identity('archive-quality-original-job'),
        'invocation_id': identity('archive-quality-original-call'),
        'capability_id': identity('archive-quality-original-capability'),
        'activation_id': 'synthetic-original-quality-activation', 'node_id': 'synthetic-original-quality-node',
        'request_key_hash': digest('graph:archive-quality-original')}
    quality_report['report_hash'] = digest({k: v for k, v in quality_report.items() if k != 'report_hash'})
    qpin = quality_report['producer']
    s.quality_job = m.Job(id=qpin['job_id'], workspace_id=s.p.workspace_id, kind=waits.scope.KIND,
        status='succeeded', idempotency_key='graph:archive-quality-original',
        data={'task_id': s.task.id, 'actor_id': qpin['actor_id'], 'invocation_key': 'graph:archive-quality-original'})
    s.quality_call = m.Invocation(id=qpin['invocation_id'], workspace_id=s.p.workspace_id,
        capability_id=qpin['capability_id'], status='succeeded', idempotency_key=s.quality_job.idempotency_key,
        data={'job_id': s.quality_job.id, 'role': 'data', 'output': output(quality_report)})
    s.db.rows[m.Job, s.quality_job.id] = s.quality_job
    s.db.rows[m.Invocation, s.quality_call.id] = s.quality_call
    dedup_report = copy.deepcopy(s.inputs['quality_report'])
    dedup_report['operation'] = 'deduplicate'
    dedup_report['report_hash'] = digest({k: v for k, v in dedup_report.items() if k != 'report_hash'})
    s.producer_call.data = {**s.producer_call.data, 'role': 'data', 'output': output(dedup_report)}
    s.producer_job.data = {**s.producer_job.data, 'inputs': {
        'scope_ref': quality.scope_ref(), 'quality_report': copy.deepcopy(quality_report)}}
    s.cap.data = archive.manifest().model_dump(mode='json')
    s.node['execution']['output_schema'] = copy.deepcopy(s.cap.data['output_schema'])
    consumer_inputs(s, copy.deepcopy(s.cap.data['execution']),
        {'quality_report': quality_report, 'dedup_report': dedup_report}, copy.deepcopy(s.cap.data['input_schema']))
    s.archive_checks = []

    def checked_archive_sources(db, principal, task, job, inputs):
        # This boundary is deliberately synthetic; no producer evidence is repaired.
        assert db is s.db and principal is s.p and task is s.task and job is s.job
        assert inputs == job.data['inputs']
        s.archive_checks.append((s.quality_job.status, s.producer_job.status))
        if s.boundary_error is not None:
            raise s.boundary_error
        if s.quality_job.status != 'succeeded':
            raise HTTPException(409, 'Synthetic source gate rejects the nonterminal n3 chain')
        if s.pending_override is not None:
            raise reader.ProducerPending(*s.pending_override)
        if s.producer_job.status == 'running':
            raise reader.ProducerPending(s.producer_job.id, s.producer_call.id)
        if s.producer_job.status != 'succeeded':
            raise HTTPException(409, 'Synthetic source gate rejects the unsuccessful n4 producer')
        return copy.deepcopy(s.fresh_pin)

    monkeypatch.setattr(archive, 'producer_wait_pin', checked_archive_sources)
    return s


def test_archive_family_is_exact_and_the_older_quality_families_stay_distinct():
    assert waits._family(NS(data=archive.manifest().model_dump(mode='json'))) == 'quality_archive_v220'
    assert waits._family(NS(data=quality.manifest(quality.DEDUP).model_dump(mode='json'))) == 'originals_dedup_v219'
    assert waits._family(NS(data=quality.manifest(quality.QUALITY).model_dump(mode='json'))) is None


@pytest.mark.parametrize('fault', ['side_effect', 'execution_operation', 'missing_input'])
def test_archive_handler_without_the_complete_registered_contract_is_rejected(fault):
    data = archive.manifest().model_dump(mode='json')
    if fault == 'side_effect': data['execution']['side_effect'] = True
    elif fault == 'execution_operation': data['execution']['operation'] = 'archive'
    else: data['input_schema']['properties'].pop('quality_report')
    with pytest.raises(HTTPException) as caught:
        waits._family(NS(data=data))
    assert caught.value.status_code == 422


def test_archive_waits_only_for_the_same_dedup_job_then_wakes_without_new_invocation(scene):
    s = scene; original = (s.job.id, s.job.idempotency_key, s.job.attempts)
    assert s.producer_job.data['inputs']['quality_report'] == s.inputs['quality_report']
    assert s.wait() is True and s.job.status == waits.PHASE
    dependencies = s.job.data[waits.KEY]['history'][0]['dependencies']
    assert len(dependencies) == 1
    dependency = dependencies[0]
    assert dependency['kind'] == 'quality_archive_dedup_producer_v220'
    assert dependency['source_path'] == '/dedup_report'
    assert dependency['source_hash'] == digest(s.inputs['dedup_report'])
    assert dependency['job_id'] == s.producer_job.id != s.quality_job.id
    assert dependency['pin'] == s.inputs['dedup_report']['producer'] == s.pin
    assert dependency['invocation_output_hash'] == digest(s.producer_call.data['output'])
    assert s.schedule() == 0 and s.job.status == waits.PHASE
    s.producer_job.status = 'succeeded'
    assert s.schedule() == 1 and s.job.status == 'queued'
    assert (s.job.id, s.job.idempotency_key, s.job.attempts) == original
    assert s.db.present_call is None and s.db.commits == 0
    assert s.db.additions == [m.Audit, m.Audit]
    assert [event.action for event in s.db.events] == [waits.WAIT, waits.WAKE]
    assert s.archive_checks == [('succeeded', 'running'), ('succeeded', 'running'), ('succeeded', 'succeeded')]


def test_archive_with_both_terminal_sources_has_no_wait_or_fabricated_result(scene):
    s = scene; s.producer_job.status = 'succeeded'
    assert s.wait() is False
    assert s.job.status == 'running' and waits.KEY not in s.job.data
    assert not s.db.additions and s.db.present_call is None


@pytest.mark.parametrize('status', ['running', 'queued', 'failed'])
def test_nonterminal_or_failed_n3_is_an_invalid_chain_not_an_n4_wait(scene, status):
    s = scene; s.quality_job.status = status
    with pytest.raises(HTTPException) as caught:
        s.wait()
    assert caught.value.status_code == 409
    assert s.job.status == 'running' and waits.KEY not in s.job.data
    assert not s.db.additions and s.db.present_call is None


@pytest.mark.parametrize('fault', ['n3_pending', 'foreign_job', 'foreign_invocation', 'queued', 'failed'])
def test_pending_must_identify_the_real_still_running_dedup_producer(scene, fault):
    s = scene; pending = [s.producer_job.id, s.producer_call.id]
    if fault == 'n3_pending': pending = [s.quality_job.id, s.quality_call.id]
    elif fault == 'foreign_job': pending[0] = identity('archive-foreign-job')
    elif fault == 'foreign_invocation': pending[1] = identity('archive-foreign-invocation')
    else: s.producer_job.status = fault
    s.pending_override = tuple(pending)
    with pytest.raises(HTTPException) as caught:
        s.wait()
    assert caught.value.status_code == 409
    assert waits.KEY not in s.job.data and not s.db.additions
    assert s.db.present_call is None


@pytest.mark.parametrize('fault', ['quality_input', 'dedup_input', 'n3_running', 'n3_pending',
    'cold_source_error', 'pin_hash', 'pin_extra', 'original_output', 'wait_audit'])
def test_archive_wake_cold_rechecks_both_inputs_original_pin_output_and_witness(scene, fault):
    s = scene; assert s.wait() is True
    original = (s.job.id, s.job.idempotency_key, s.job.attempts)
    s.producer_job.status = 'succeeded'
    if fault == 'quality_input': s.job.data['inputs']['quality_report']['attributes']['file_count'] = 1
    elif fault == 'dedup_input': s.job.data['inputs']['dedup_report']['attributes']['file_count'] = 1
    elif fault == 'n3_running': s.quality_job.status = 'running'
    elif fault == 'n3_pending': s.pending_override = (s.quality_job.id, s.quality_call.id)
    elif fault == 'cold_source_error': s.boundary_error = HTTPException(409, 'Synthetic missing original producer proof')
    elif fault == 'pin_hash': s.fresh_pin['input_hash'] = digest('different original n4 inputs')
    elif fault == 'pin_extra': s.fresh_pin['unregistered_origin'] = True
    elif fault == 'original_output': s.producer_call.data['output'] = {'changed_original': True}
    else: s.db.events.clear()
    assert s.schedule() == 0 and s.job.status == 'blocked'
    assert (s.job.id, s.job.idempotency_key, s.job.attempts) == original
    assert s.db.present_call is None
    assert not any(event.action == waits.WAKE for event in s.db.events)
