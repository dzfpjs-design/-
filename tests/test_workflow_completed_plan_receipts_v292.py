"""Historical collection proof with explicit original-source/ACK IO boundaries.

The real V292 witness, review, field/temporal validation and V195 routing run.
SQL selection is inspected; raw mapped/extractor/ACK authority is an explicit
spy, not a fabricated claim that these pure fixtures prove real PG or devices.
"""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import workflow_completed_plan_receipts_v292 as proof
from generic_agents import workflow_service_v195 as service
from generic_agents import acquisition_ack_settlement_v245 as settlement
from generic_agents.contracts import EvidenceRequirement, digest
from generic_agents.temporal import matches, resolve


AT = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)


def identity():
    return str(uuid4())


def scene(monkeypatch):
    from generic_agents import mapped_acquisition_origin_v177 as mapped
    from generic_agents import observation_integrity, instance_data_receipts
    p = NS(workspace_id=identity(), user_id=identity())
    task = NS(id=identity(), package_id=identity(), data={'instance_id': identity(), 'instance_release_id': identity()})
    context = {'task': task, 'release': NS(id=task.data['instance_release_id']),
        'binding': {'schema': 'authored-goal-data-workflow-binding-v288'}}
    request = {'entity_id': identity(), 'fields': ['temperature_c'], 'gap_id': identity()}
    requirement = {'source': 'observations', 'minimum_verification': 'confirmed',
        'minimum_records_per_entity': 1, 'time': {'mode': 'latest', 'timezone': 'Asia/Shanghai', 'max_age_seconds': 3600}}
    policy = NS(id=identity(), data={'approved_hash': 'a' * 64, 'observation_evidence_requirement': requirement,
        'selection_requirement': {'allowed_modalities': ['numeric'], 'max_resolution_m': None}})
    plan = NS(id=identity(), workspace_id=p.workspace_id, status='completed', policy_id=policy.id,
        entity_id=request['entity_id'], data={'policy_hash': policy.data['approved_hash'], 'observation_request': copy.deepcopy(request)})
    child = NS(id=identity(), workspace_id=p.workspace_id, entity_id=plan.entity_id, capability_id=identity(),
        status='succeeded', idempotency_key='original-device-request', data={'observation_plan_id': plan.id,
            'policy_id': policy.id, 'policy_hash': policy.data['approved_hash'], 'actor_id': p.user_id, 'source_hash': 'b' * 64})
    observation = NS(id=identity(), workspace_id=p.workspace_id, entity_id=plan.entity_id,
        source_id=child.capability_id, version=3, status='processed', created_at=AT - timedelta(minutes=8),
        observed_at=AT - timedelta(minutes=9), data={'acquisition_id': child.id, 'input_hash': 'c' * 64,
            'invocation_id': identity(), 'extracted': {'attributes': {'temperature_c': 27}, 'anomalies': []},
            'evidence_ids': [], 'sensor_evidence': {'modality': 'numeric', 'resolution_m': None}})
    child.data['closing_observation_id'] = observation.id
    received = NS(id=identity(), workspace_id=p.workspace_id, status='received', received_at=AT - timedelta(minutes=4))
    reception = {'observation_id': observation.id, 'event_id': received.id,
        'instance_id': task.data['instance_id'], 'release_id': context['release'].id, 'package_id': task.package_id}
    mapped_proof = {'acquisition_id': child.id, 'instance_id': task.data['instance_id'], 'release_id': context['release'].id}
    fingerprint = digest({'extracted': observation.data['extracted'], 'evidence_ids': [],
        'invocation_id': observation.data['invocation_id']})
    review = {'decision': 'confirmed', 'reason': '核对原样本技术质量，保留模拟标识', 'request_id': identity(),
        'reviewed_by': p.user_id, 'reviewed_at': (AT - timedelta(minutes=6)).isoformat(), 'input_hash': fingerprint}
    review['body_hash'] = digest({'expected_version': 2, 'reason': review['reason'], 'decision': 'confirmed',
        'request_id': review['request_id'], 'expected_input_hash': fingerprint})
    observation.data.update(human_verification=review, verification_history=[copy.deepcopy(review)],
        verification_history_hash=digest([review]))
    report = {'attributes': copy.deepcopy(observation.data['extracted']['attributes']), 'review': 'confirmed'}
    item = {'observation_id': observation.id, 'entity_id': plan.entity_id, 'source_id': observation.source_id,
        'version': observation.version, 'attributes': copy.deepcopy(report['attributes']),
        'observed_at': observation.observed_at.isoformat(), 'data_reception': copy.deepcopy(reception),
        'invocation_id': observation.data['invocation_id'], 'evidence_ids': [], 'anomalies': [],
        'source': 'processed_observation', 'verification': 'confirmed',
        'sensor_evidence': copy.deepcopy(observation.data['sensor_evidence'])}
    plan.data['result'] = {'status': 'sufficient', 'gaps': [], 'conflicts': [], 'observation_evidence': [item],
        'evaluated_at': (AT - timedelta(seconds=5)).isoformat(),
        'evidence_requirement': EvidenceRequirement.model_validate(requirement).model_dump()}
    pin = {'schema': settlement.SCHEMA, 'workspace_id': p.workspace_id, 'instance_id': task.data['instance_id'],
        'acquisition_id': child.id, 'observation_id': observation.id, 'event_id': received.id, 'actor_id': p.user_id,
        'input_hash': observation.data['input_hash'], 'receipt_hash': digest(reception),
        'family': 'http_mapped_v177', 'mapped_origin_hash': digest(mapped_proof), 'original_acquisition_hash': 'd' * 64}
    result = {'schema': settlement.SCHEMA, 'acquisition_id': child.id, 'observation_id': observation.id,
        'event_id': received.id, 'status': 'succeeded', 'plan_status': 'completed',
        'cloud_calls': 0, 'device_actions': 0, 'new_acquisitions': 0}
    job = NS(id=identity(), workspace_id=p.workspace_id, kind=settlement.KIND, status='succeeded',
        idempotency_key='acquisition-data-ack:' + child.id + ':' + observation.id,
        data={'actor_id': p.user_id, 'settlement': copy.deepcopy(pin), 'settlement_hash': digest(pin),
            'settlement_result': copy.deepcopy(result), 'settlement_result_hash': digest(result)})
    def audit(action, object_id, at, data):
        return NS(id=identity(), workspace_id=p.workspace_id, actor_id=p.user_id,
            action=action, object_id=object_id, created_at=at, data=copy.deepcopy(data))
    queued = audit(settlement.ACTION, job.id, AT - timedelta(minutes=3),
        {'job_id': job.id, 'settlement': pin, 'settlement_hash': digest(pin)})
    settled = audit(proof.SETTLED, child.id, AT, {'job_id': job.id, **result})
    closed = audit('acquisition.close', child.id, AT - timedelta(seconds=10),
        {'observation_id': observation.id, 'verified_collection': True})
    verified = audit('observation.verified', observation.id, AT - timedelta(minutes=5),
        {'decision': 'confirmed', 'reason': review['reason']})
    rows = {(proof.Job, job.id): job, (proof.Observation, observation.id): observation,
        (proof.Event, received.id): received, (service.ObservationPlan, plan.id): plan}
    class ReadIO:
        def __init__(self):
            self.audits = [queued, settled, closed, verified]; self.children = [child]; self.jobs = [job]; self.reads = []
        def scalars(self, statement):
            model = statement.column_descriptions[0]['entity']; params = statement.compile().params
            self.reads.append((model, params)); assert p.workspace_id in params.values()
            if model is proof.Acquisition:
                assert plan.id in params.values()
                return self.children
            if model is proof.Job:
                assert 'acquisition-data-ack:' + child.id + ':' in params.values()
                return self.jobs
            assert model is proof.Audit
            target = params.get('object_id_1')
            if isinstance(target, str):
                return [row for row in self.audits if row.action == params['action_1'] and row.object_id == target]
            assert params['action_1'] == settlement.ACTION
            return [row for row in self.audits if row.action == settlement.ACTION and (
                row.data.get('settlement', {}).get('acquisition_id') == child.id
                or row.data.get('settlement', {}).get('observation_id') == observation.id
                or row.object_id in (target or []))]
    db = ReadIO(); checks = []
    def scoped(_db, model, identifier, current, *args, **kwargs):
        assert _db is db and current is p
        row = rows[(model, identifier)]; assert row.workspace_id == p.workspace_id
        return row
    def raw_mapped(_db, current, original, **kwargs):
        assert _db is db and current is p and original is observation and kwargs['execution'] is False
        checks.append('mapped-original'); return copy.deepcopy(mapped_proof)
    def raw_report(_db, current, original, **kwargs):
        assert _db is db and current is p and original is observation
        checks.append('extractor-original'); return copy.deepcopy(report)
    def raw_ack(_db, current, original):
        assert _db is db and current is p and original is observation
        checks.append('data-ack'); return {'data_reception': copy.deepcopy(reception)}
    def original_settlement(_db, current, actual_event, original):
        assert _db is db and current is p and actual_event is received and original is observation
        checks.append('original-settlement-input'); return copy.deepcopy(pin)
    monkeypatch.setattr(proof, 'scoped', scoped); monkeypatch.setattr(service, 'scoped', scoped)
    monkeypatch.setattr(mapped, 'guard', raw_mapped); monkeypatch.setattr(observation_integrity, 'checked_report', raw_report)
    monkeypatch.setattr(instance_data_receipts, 'observation_receipt', raw_ack)
    monkeypatch.setattr(settlement, 'checked_input', original_settlement)
    state = {'simulation_only': False, 'operation_data': {'acquisition_pins': [service._acquisition_pin(db, p, child, policy, request)]}}
    return NS(db=db, p=p, context=context, policy=policy, request=request, state=state, plan=plan, child=child,
        observation=observation, received=received, reception=reception, report=report, item=item, pin=pin,
        job=job, queued=queued, settled=settled, closed=closed, verified=verified, checks=checks)


def checked(s):
    return proof.completed_plan(s.db, s.p, s.context, s.policy, s.request, s.state, s.plan, max_acquisitions=20)


def test_real_normal_single_review_at_witness_can_complete_old_collection_without_refresh(monkeypatch):
    s = scene(monkeypatch); before = copy.deepcopy((s.plan.data, s.child.data, s.job.data, s.observation.data))
    current_window = resolve(s.policy.data['observation_evidence_requirement']['time'], reference=AT + timedelta(hours=2))
    assert not matches(s.observation.observed_at, current_window, evaluated_at=AT + timedelta(hours=2))
    result = checked(s)
    assert result['evidence'] == s.plan.data['result'] and result['evidence'] is not s.plan.data['result']
    assert result['evidence_hash'] != digest(s.plan.data['result'])
    assert 'original-settlement-input' in s.checks and 'mapped-original' in s.checks
    assert (s.plan.data, s.child.data, s.job.data, s.observation.data) == before


@pytest.mark.parametrize('fault', ['no_family', 'legacy_unmapped', 'awaiting_settled'])
def test_without_completed_witness_the_old_current_path_is_retained(monkeypatch, fault):
    s = scene(monkeypatch)
    if fault in {'no_family', 'legacy_unmapped'}:
        s.db.audits.remove(s.settled); s.db.audits.remove(s.queued); s.db.jobs.clear()
        if fault == 'legacy_unmapped':
            s.child.status = 'unknown'; s.db.audits.remove(s.closed)
    else:
        s.settled.data['plan_status'] = 'awaiting_collection'
        s.job.data['settlement_result']['plan_status'] = 'awaiting_collection'
        s.job.data['settlement_result_hash'] = digest(s.job.data['settlement_result'])
    assert checked(s) is None
    if fault in {'no_family', 'legacy_unmapped'}:
        assert not s.checks


@pytest.mark.parametrize('fault', ['failed_job', 'missing_job', 'missing_settled', 'missing_queued',
    'queued_drift', 'result_rehash', 'result_boolean_drift', 'orphan_key_only',
    'missing_close', 'close_after_cut', 'ack_after_close', 'raw_value_drift', 'coverage_removed',
    'stale_at_completion', 'late_review', 'missing_review_audit', 'late_review_audit', 'unknown_child'])
def test_self_hashes_cannot_replace_independent_original_history(monkeypatch, fault):
    s = scene(monkeypatch)
    if fault == 'failed_job': s.job.status = 'failed'
    elif fault == 'missing_job': s.db.jobs.clear()
    elif fault == 'missing_settled': s.db.audits.remove(s.settled)
    elif fault == 'orphan_key_only':
        s.db.audits.remove(s.queued); s.db.audits.remove(s.settled)
        s.child.data.pop('closing_observation_id'); s.job.data['settlement'].pop('acquisition_id')
    elif fault == 'missing_queued': s.db.audits.remove(s.queued)
    elif fault == 'queued_drift': s.queued.data['settlement_hash'] = 'f' * 64
    elif fault == 'result_rehash':
        s.job.data['settlement_result']['plan_status'] = 'awaiting_collection'
        s.job.data['settlement_result_hash'] = digest(s.job.data['settlement_result'])
    elif fault == 'result_boolean_drift': s.job.data['settlement_result']['device_actions'] = False
    elif fault == 'missing_close': s.db.audits.remove(s.closed)
    elif fault == 'close_after_cut': s.closed.created_at = AT + timedelta(seconds=1)
    elif fault == 'ack_after_close': s.received.received_at = AT
    elif fault == 'raw_value_drift': s.report['attributes']['temperature_c'] = 99
    elif fault == 'coverage_removed':
        s.report['attributes']['temperature_c'] = None
        s.plan.data['result']['observation_evidence'][0]['attributes']['temperature_c'] = None
    elif fault == 'stale_at_completion':
        s.observation.observed_at = AT - timedelta(hours=2)
        s.plan.data['result']['observation_evidence'][0]['observed_at'] = s.observation.observed_at.isoformat()
    elif fault == 'late_review':
        review = s.observation.data['human_verification']; review['reviewed_at'] = (AT + timedelta(seconds=1)).isoformat()
        s.observation.data['verification_history'] = [copy.deepcopy(review)]
        s.observation.data['verification_history_hash'] = digest([review])
    elif fault == 'missing_review_audit': s.db.audits.remove(s.verified)
    elif fault == 'late_review_audit': s.verified.created_at = AT + timedelta(seconds=1)
    else: s.child.status = 'unknown'
    with pytest.raises((HTTPException, service.PendingService, KeyError)):
        checked(s)


def test_v195_historical_branch_does_not_run_current_evidence_and_still_reads_actual_ack(monkeypatch):
    from generic_agents import observation_requests, sensing_selection, data_gaps
    s = scene(monkeypatch)
    monkeypatch.setattr(observation_requests, 'effective', lambda *args, **kwargs: s.policy)
    monkeypatch.setattr(sensing_selection, 'evidence', lambda *args, **kwargs: pytest.fail('completed collection was re-queried as current Goal data'))
    monkeypatch.setattr(data_gaps, 'receipts_ready', lambda _db, p, value: value['result']['observation_evidence'][0]['data_reception'] == s.reception)
    receipts, followups = service._complete_plans(s.db, s.p, s.context, s.policy, s.request, s.state)
    assert followups == [] and receipts == [{'plan_id': s.plan.id, 'evidence_hash': checked(s)['evidence_hash'],
        'supporting_observations': [{'observation_id': s.observation.id,
            'data_receipt_hash': digest({'data_reception': s.reception})}]}]


def test_v195_malformed_present_witness_fails_closed_without_fresh_fallback(monkeypatch):
    from generic_agents import observation_requests, sensing_selection
    s = scene(monkeypatch); s.job.data['settlement_result_hash'] = 'f' * 64
    monkeypatch.setattr(observation_requests, 'effective', lambda *args, **kwargs: s.policy)
    monkeypatch.setattr(sensing_selection, 'evidence', lambda *args, **kwargs: pytest.fail('malformed witness fell back'))
    with pytest.raises(HTTPException):
        service._complete_plans(s.db, s.p, s.context, s.policy, s.request, s.state)
