"""Existing readonly DTO over fixed V288 runtime/Frame IO boundaries.

Real graph invocation identity, native/invoke/recovery audit comparisons and
the V284 result contract run here. Raw/CloudCall/Frame/receiver/current IAM
storage boundaries are explicit fixtures/spies, not PG/model/ACK proof.
"""
import copy
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import business_decision_review_api_v284 as subject
from generic_agents import business_goal_ready_runtime_v288 as ready
from generic_agents import business_goal_ready_frame_v288 as ready_frame
from generic_agents.business_reasoning_protocols_v276 import recovery_record
from generic_agents.contracts import digest
from generic_agents.models import Audit
from generic_agents.workflow_goal_evaluation_contracts_v288 import RECORD
from tests.test_business_decision_review_api_v284 import fixture as original_fixture, ident


def sql(query): return str(query.compile(compile_kwargs={'literal_binds': True}))


def fixture(monkeypatch):
    s = original_fixture(monkeypatch)
    reference = {'id': ident(20), 'evidence_id': ident(21), 'sha256': 'd' * 64,
        'version': 1, 'simulation_only': True, 'proposal_only': True, 'cloud_authorized': False}
    s.job.data['inputs']['data_packet'] = reference
    inputs = s.job.data['inputs']
    value_hash = digest(inputs)
    s.job.data['input_hash'] = s.call.data['input_hash'] = s.transport['input_hash'] = value_hash
    s.transport['dispatch_authorization']['input_hash'] = value_hash
    s.activation['inputs_snapshot']['arguments'] = copy.deepcopy(inputs)
    s.audits['workflow.graph.dispatch_authorized'][0].data['input_hash'] = value_hash
    s.cap.data['execution']['adapter'] = ready.ADAPTER
    s.cap.content_hash = s.cap.validated_hash = digest(s.cap.data)
    s.node['execution']['adapter'] = ready.ADAPTER
    s.node['execution']['capability_hash'] = s.call.data['capability_hash'] = s.cap.content_hash
    s.transport['dispatch_authorization']['capability_hash'] = s.cap.content_hash
    s.frame['sources']['schema'] = ready.sources.SCHEMA
    s.frame['sources']['data']['packet'] = copy.deepcopy(reference)
    s.frame['sources']['data']['evaluation_record'] = {'schema': RECORD, 'evaluation_id': reference['id'],
        'evidence_ref': {'evidence_id': reference['evidence_id'], 'sha256': reference['sha256']}, 'simulation_only': True}
    s.saved['request']['frame_hash'] = digest(s.frame)
    s.call.data[ready.STATE_KEY] = {'synthetic_original_state': True}

    def checked_frame(db, reader, task, job, cap, actual_inputs, frozen, *, _server_protocol):
        assert db is s.db and reader is s.p and reader.user_id != s.actor
        assert (task, job, cap, actual_inputs, frozen) == (s.task, s.job, s.cap, inputs, s.frame)
        assert _server_protocol == ready.ADAPTER
        s.seen.append('new_frame_sources_boundary')
        return copy.deepcopy(frozen)

    def saved_result(db, reader, task, job, call, cap, actual_inputs):
        assert db is s.db and reader is s.p and call is s.call
        ready_frame.checked_frame(db, reader, task, job, cap, actual_inputs, s.frame,
            _server_protocol=ready.ADAPTER)
        s.seen.append('new_runtime_raw_sdk_boundary')
        return copy.deepcopy(s.result)

    def checked_state(db, reader, cap, actual_inputs, key, call, *, actor_id):
        assert db is s.db and reader is s.p and actor_id == s.actor != reader.user_id
        subject.need(ready.STATE_KEY in call.data and subject.runtime.STATE_KEY not in call.data)
        assert cap is s.cap and actual_inputs == inputs and key == s.job.idempotency_key
        s.seen.append('new_original_actor_state_boundary')
        return copy.deepcopy(s.saved)

    monkeypatch.setattr(ready_frame, 'checked_frame', checked_frame)
    monkeypatch.setattr(ready, 'saved_result', saved_result)
    monkeypatch.setattr(ready, '_checked_state_for_actor', checked_state)
    monkeypatch.setattr(ready, '_manifest_shape', lambda value: value)
    monkeypatch.setattr(ready, '_cloud_call', lambda db, reader, identity:
        s.ledger if db is s.db and reader is s.p and identity == s.ledger.id else pytest.fail('foreign CloudCall'))
    monkeypatch.setattr(subject.runtime, 'saved_result', lambda *a: pytest.fail('new source used old runtime'))
    marker, action = recovery_record(ready.ADAPTER)
    events = []; query = sql(select(Audit).where(Audit.workspace_id == s.p.workspace_id,
        Audit.action == action, Audit.object_id == s.call.id).limit(2))
    original_io = s.db

    class IO:
        @property
        def no_autoflush(self): return original_io.no_autoflush
        def scalar(self, statement): return original_io.scalar(statement)
        def scalars(self, statement): return events if sql(statement) == query else original_io.scalars(statement)
        def add(self, *a): pytest.fail('readonly added a row')
        def flush(self, *a): pytest.fail('readonly flushed')
        def commit(self, *a): pytest.fail('readonly committed')
        def execute(self, *a): pytest.fail('readonly wrote or queried a fresh source')

    s.db = IO(); s.new_recovery = events; s.new_marker = marker; s.reference = reference
    return s


def read(s): return subject.context(s.db, s.p, s.task.id, s.activation_id)


def test_new_brain_is_read_through_fixed_new_runtime_and_frame_with_same_ui_contract(monkeypatch):
    s = fixture(monkeypatch); before = copy.deepcopy((s.task.data, s.job.data, s.call.data, s.frame))
    result = read(s)
    assert result['schema'] == subject.SCHEMA
    assert result['result'] == s.result and result['decision_input'] == s.frame['decision_input']
    assert result['fields'][0]['label'] == '温度' and result['simulation_only'] is True
    assert next(item['text'] for item in result['fact_values'] if item['fact_ref'] == 'fact_temperature') == '9007199254740993'
    assert s.seen == ['no_autoflush', 'saved_execution_and_frozen_boundary',
        'new_frame_sources_boundary', 'new_runtime_raw_sdk_boundary', 'new_original_actor_state_boundary']
    assert (s.task.data, s.job.data, s.call.data, s.frame) == before
    body = json.dumps(result, ensure_ascii=False)
    assert 'LOCAL_ONLY' not in body and RECORD not in body and 'evaluation_record' not in body


def test_new_producer_still_saving_returns_explicit_wait_without_reading_a_half_result(monkeypatch):
    s = fixture(monkeypatch); s.job.status = 'running'
    with pytest.raises(HTTPException) as failure: read(s)
    assert failure.value.status_code == 409 and failure.value.detail == subject.PENDING
    assert 'new_runtime_raw_sdk_boundary' not in s.seen


@pytest.mark.parametrize('damage', ['old_state', 'missing_record', 'record_id', 'packet_hash', 'source_schema', 'sdk_pending', 'capture'])
def test_new_protocol_cannot_display_an_old_or_mismatched_original(monkeypatch, damage):
    s = fixture(monkeypatch)
    if damage == 'old_state': s.call.data[subject.runtime.STATE_KEY] = s.call.data.pop(ready.STATE_KEY)
    elif damage == 'missing_record': s.frame['sources']['data'].pop('evaluation_record')
    elif damage == 'record_id': s.frame['sources']['data']['evaluation_record']['evaluation_id'] = ident(99)
    elif damage == 'packet_hash': s.frame['sources']['data']['evaluation_record']['evidence_ref']['sha256'] = '0' * 64
    elif damage == 'source_schema': s.frame['sources']['schema'] = subject.sources.SCHEMA
    elif damage == 'sdk_pending': s.ledger.status = 'reserved'
    else: s.saved['capture'] = None
    s.saved['request']['frame_hash'] = digest(s.frame)
    with pytest.raises(HTTPException): read(s)


def test_audited_new_protocol_recovery_is_not_confused_with_the_old_state(monkeypatch):
    from types import SimpleNamespace as NS
    s = fixture(monkeypatch)
    s.audits['capability.invoke'][0].data = {'status': 'failed'}
    saved = {'previous_status': 'failed', 'output_hash': digest(s.result), 'recovered_at': '2026-11-01T12:30:00+00:00'}
    s.call.data[s.new_marker] = saved
    s.new_recovery.append(NS(actor_id=s.actor, data={'job_id': s.job.id, 'activation_id': s.activation_id,
        'previous_status': 'failed', 'output_hash': digest(s.result)}))
    assert read(s)['result'] == s.result
    s.new_recovery[0].actor_id = s.p.user_id
    with pytest.raises(HTTPException): read(s)
