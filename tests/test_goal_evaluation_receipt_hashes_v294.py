"""Actual V195 wrapper hashing -> V288 ACK custody and store orchestration.

Original receipt/integrity authentication, SQL inventories, IAM, creator,
query and evidence persistence are explicit IO spies from the store fixtures.
The V195 hash writer, _acks comparisons, execute coverage and record checks
run unchanged. These tests do not prove actual PG, SDK, receiver or authority.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import observation_integrity, workflow_service_v195
from generic_agents import workflow_goal_evaluation_store_v288 as subject
from generic_agents.contracts import digest
from generic_agents.temporal import instant
from tests.test_workflow_goal_evaluation_store_v288 import presentation, receipts, runtime, invoke
from tests.test_workflow_goal_evaluation_contracts_v288 import CUT, CHECKED, uid


ACTUAL_ACKS = subject._acks


@pytest.fixture
def hashes(receipts, monkeypatch):
    s = receipts
    s.task.package_id = uid(5); s.task.data['instance_release_id'] = uid(4)
    s.origin.task = s.task
    inner = s.memory.data['data_reception']
    inner.update(observation_id=s.obs.id, instance_id=s.task.data['instance_id'],
        release_id=s.task.data['instance_release_id'], package_id=s.task.package_id,
        simulation_origin={'simulation': True, 'test_only': True, 'field_verified': False})
    inner['receipt_hash'] = digest({key: value for key, value in inner.items() if key != 'receipt_hash'})
    # The real V195 writer calls these already-spied receipt/integrity IO
    # boundaries; neither their return value nor a self hash grants authority.
    monkeypatch.setattr(observation_integrity, 'checked_report', lambda *_args, **_kwargs: None)
    s.wrapper = {'data_reception': inner}
    s.wrapper_hash = workflow_service_v195._observation_receipt_hash(
        s.db, s.p, {'task': s.task}, s.obs, {'simulation_only': True})
    assert s.wrapper_hash == digest(s.wrapper) != inner['receipt_hash']
    s.resense = {'service_receipt': {'observations': [{'acquisition_id': s.origin.acquisition.id,
        'observation_id': s.obs.id, 'data_receipt_hash': s.wrapper_hash}], 'followup_observations': []}}
    return s


@pytest.mark.parametrize('admission', ['admitted', 'excluded', 'outside_window'])
def test_actual_producer_wrapper_hash_keeps_independent_inner_ack_and_original_admission(hashes, admission):
    s = hashes
    if admission == 'outside_window': s.obs.observed_at = instant('2026-11-01T11:00:00+00:00')
    state = s.state if admission == 'admitted' else {'result': {'observation_evidence': []}}
    before = copy.deepcopy((s.resense, state, s.obs.data, s.wrapper))
    refs = ACTUAL_ACKS(s.db, s.p, s.task, state, instant(CUT), s.window, s.resense)
    assert len(refs) == 1 and refs[0]['query_admitted'] is (admission == 'admitted')
    assert refs[0]['data_reception_hash'] == s.wrapper['data_reception']['receipt_hash'] != s.wrapper_hash
    assert refs[0]['origin_task_id'] == s.task.id and refs[0]['observation_id'] == s.obs.id
    assert (s.resense, state, s.obs.data, s.wrapper) == before


@pytest.mark.parametrize('wrong_hash', ['inner_hash', 'digest_inner', 'other_wrapper'])
def test_inner_or_other_wrapper_hash_cannot_replace_original_v195_wrapper(hashes, wrong_hash):
    s = hashes
    wrong = {'inner_hash': s.wrapper['data_reception']['receipt_hash'],
        'digest_inner': digest(s.wrapper['data_reception']),
        'other_wrapper': digest({'data_reception': {**s.wrapper['data_reception'], 'event_id': uid(999)}})}
    s.resense['service_receipt']['observations'][0]['data_receipt_hash'] = wrong[wrong_hash]
    before = copy.deepcopy((s.state, s.obs.data, s.wrapper))
    with pytest.raises(HTTPException):
        ACTUAL_ACKS(s.db, s.p, s.task, s.state, instant(CUT), s.window, s.resense)
    assert (s.state, s.obs.data, s.wrapper) == before


@pytest.mark.parametrize('fault', ['wrong_origin_task', 'wrong_acquisition', 'late_ack'])
def test_correct_wrapper_does_not_replace_original_identity_or_receipt_cut(hashes, fault):
    s = hashes
    if fault == 'wrong_origin_task':
        s.origin.task = copy.deepcopy(s.task); s.origin.task.id = uid(999)
    elif fault == 'wrong_acquisition':
        s.resense['service_receipt']['observations'][0]['acquisition_id'] = uid(999)
    else:
        s.event.received_at = instant(CHECKED)
    with pytest.raises(HTTPException):
        ACTUAL_ACKS(s.db, s.p, s.task, s.state, instant(CUT), s.window, s.resense)


@pytest.mark.parametrize('admitted', [True, False])
def test_execute_and_cold_reconcile_keep_wrapper_to_inner_bridge_without_requery(runtime, hashes, monkeypatch, admitted):
    s, r = runtime, hashes
    record = s.record; report = record['evaluation_result']['result']
    report['observation_evidence'] = copy.deepcopy(r.state['result']['observation_evidence']) if admitted else []
    record['evaluation_result']['status'] = report['status'] = 'sufficient' if admitted else 'insufficient'
    if not admitted:
        report['gaps'] = [{'entity_id': r.obs.entity_id, 'missing_fields': ['temperature']}]
    record['evaluation_query_ref']['result_hash'] = subject.hashed(record['evaluation_result'])
    record['preparation_policy']['allow_acquisition'] = True
    record['preparation_policy']['policy_hash'] = subject.contract.policy_hash(record['preparation_policy'])
    record['trigger'].update(kind='acquisition_completed', reader_context_hash='a' * 64,
        resense_invocation_id=uid(999), resense_receipt_hash='b' * 64)
    monkeypatch.setattr(subject, '_upstream', lambda *_args: {'binding': {
        'preparation_policy': record['preparation_policy'], 'simulation_only': True},
        'trigger': record['trigger'], 'resense': copy.deepcopy(r.resense)})

    def receipt_io(actual_db, principal, task, state, cut, window, original):
        assert actual_db is s.db and principal is s.p and task.id == r.task.id
        # Route only the SQL/receipt IO to the explicit receipt fixture DB;
        # exercise actual _acks, including the normal V195 wrapper hash.
        return ACTUAL_ACKS(r.db, principal, task, state, cut, window, original)
    monkeypatch.setattr(subject, '_acks', receipt_io)
    before = copy.deepcopy((s.task.data, r.resense, r.obs.data, r.wrapper))
    output = invoke(s)
    saved = s.created[0].data
    assert output['route'] == ('data_ready' if admitted else 'review_required')
    assert saved['ack_refs'][0]['query_admitted'] is admitted
    assert saved['ack_refs'][0]['data_reception_hash'] == r.wrapper['data_reception']['receipt_hash'] != r.wrapper_hash
    assert subject.reconcile(s.db, s.p, s.cap, s.inputs, s.job.idempotency_key, s.approval, s.call) == output
    assert s.queries == 1 and len(s.created) == len(s.events) == 1
    assert (s.task.data, r.resense, r.obs.data, r.wrapper) == before
