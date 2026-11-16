"""Real V197 preflight/wait/inventory/scheduler with synthetic source IO.

Reuse the existing exact-SQL wait fixture and real kernel inputs/Job/Audit
checks. V285's independent paid-producer proof and worker IAM are explicit
spies, not real SQL/IAM/model evidence. No wait record is hand-constructed.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import models as m, worker
from generic_agents import workflow_provenance_wait_v197 as subject
from generic_agents import workflow_decision_tool_planner_v285 as planner
from generic_agents import workflow_graph_goal_guard_v133 as goal_guard
from generic_agents.contracts import digest
from tests.test_workflow_provenance_wait_v197 import scene as wait_scene, consumer_inputs, producer
from tests.test_workflow_decision_tool_planner_v285 import case
from tests.test_report_business_flow_wire_v184 import no_external


@pytest.fixture
def scene(monkeypatch):
    s = wait_scene.__wrapped__(monkeypatch)
    declared = case()
    declared.frame['decision_input']['scope']['task_id'] = s.task.id
    declared.model['recommendation'] = {'candidate_ref': None,
        'reason': '保留当前资料缺口，暂不推荐具体方案', 'supporting_fact_refs': []}
    result = planner.decision.derive_result(declared.frame['decision_input'], declared.model)
    s.cap.data = copy.deepcopy(declared.cap.data)
    s.node['execution']['output_schema'] = planner.output_schema()
    consumer_inputs(s, s.cap.data['execution'], {'decision_result': result}, planner.input_schema())
    s.original, s.original_call, _context = producer(s)
    s.original_call.data = {**s.original_call.data, 'output': copy.deepcopy(result)}
    s.proof_calls = []; s.actor_calls = []

    def source_proof(db, p, task, job, inputs):
        # This is the explicit V285 producer/SDK/capture IO boundary. The V197
        # caller, dispatch choice, dependency construction and all ledger and
        # terminal proof comparisons remain actual production code.
        assert (db, p, task, job) == (s.db, s.p, s.task, s.job)
        s.proof_calls.append(s.original.status)
        value = planner._inputs(inputs)
        subject.need(value == s.job.data['inputs']
            and digest(value) == s.job.data['input_hash']
            and value['decision_result'] == s.original_call.data['output'])
        if s.original.status == 'running':
            raise subject.reader.ProducerPending(s.original.id, s.original_call.id)
        subject.need(s.original.status == 'succeeded')
        return {'job_id': s.original.id, 'invocation_id': s.original_call.id,
            'actor_id': s.original.data['actor_id'],
            'request_key_hash': digest(s.original.data['invocation_key'])}

    def actual_reader(db, workspace_id, actor_id):
        # Current reader IAM is separate from the original producer actor.
        assert (db, workspace_id, actor_id) == (s.db, s.p.workspace_id, s.job.data['actor_id'])
        s.actor_calls.append(actor_id)
        return s.p

    monkeypatch.setattr(planner, 'producer_wait_pin', source_proof)
    monkeypatch.setattr(worker, 'actor', actual_reader)
    return s


def test_actual_v285_preflight_tail_wait_inventory_and_scheduler_requeue_same_job(scene):
    s = scene
    original_identity = (s.job.id, s.job.idempotency_key, s.job.attempts, s.job.available_at,
        s.job.lease_token, s.job.lease_until, copy.deepcopy(s.job.data['inputs']), s.job.data['input_hash'])
    assert s.wait() is True and s.job.status == subject.PHASE
    # Read the record produced by the real preflight: this failed before the
    # V285 kind was admitted to the inventory's fixed whitelist.
    ledger = subject._inventory(s.db, s.task, s.job)
    record = ledger['history'][0]; dependency = record['dependencies'][0]
    assert dependency['kind'] == 'decision_reasoning_producer_v285'
    assert dependency['source_path'] == '/decision_result'
    assert dependency['source_hash'] == digest(s.inputs['decision_result'])
    assert dependency['job_id'] == s.original.id and dependency['invocation_id'] == s.original_call.id
    assert dependency['pin']['actor_id'] == s.original.data['actor_id'] != s.p.user_id
    assert ledger['active_hash'] == record['record_hash']
    assert [event.action for event in s.db.events] == [subject.WAIT]
    before = copy.deepcopy((s.job.data, s.task.data))
    assert s.schedule() == 0 and (s.job.data, s.task.data) == before
    s.original.status = 'succeeded'
    assert s.schedule() == 1 and s.job.status == 'queued'
    assert subject._inventory(s.db, s.task, s.job)['active_hash'] is None
    assert [event.action for event in s.db.events] == [subject.WAIT, subject.WAKE]
    assert (s.job.id, s.job.idempotency_key, s.job.attempts, s.job.available_at,
        s.job.lease_token, s.job.lease_until, s.job.data['inputs'], s.job.data['input_hash']) == original_identity
    assert s.proof_calls == ['running', 'running', 'succeeded']
    assert s.actor_calls == [s.p.user_id, s.p.user_id]
    assert s.db.additions == [m.Audit, m.Audit] and s.db.commits == 0 and s.db.present_call is None
    assert not s.task.data.get(goal_guard.KEY, {}).get('items')
    assert not set(s.task.data['graph_jobs'][s.activation['id']]) & {
        'dispatch_committed', 'dispatch_ownership', 'dispatch_input_hash', 'dispatch_authorization'}
    assert subject.can_start_after_wait(s.db, s.task, s.job) is True
    # The original worker's next normal preflight still checks its current
    # lease, frozen inputs and original producer, without a new wait or Call.
    s.job.status = 'running'; s.job.attempts += 1
    assert s.wait() is False and s.db.present_call is None
    assert len(s.db.events) == 2


@pytest.mark.parametrize('fault', ['wait_audit', 'producer_output', 'consumer_input', 'producer_failed'])
def test_changed_original_proof_blocks_wake_without_new_invocation_or_rights(scene, fault):
    s = scene; assert s.wait() is True
    s.original.status = 'succeeded'
    if fault == 'wait_audit': s.db.events = []
    elif fault == 'producer_output': s.original_call.data['output'] = {'changed': True}
    elif fault == 'consumer_input': s.job.data['input_hash'] = '0' * 64
    else: s.original.status = 'failed'
    assert s.schedule() == 0 and s.job.status == 'blocked'
    assert not any(event.action == subject.WAKE for event in s.db.events)
    assert s.db.present_call is None and s.db.commits == 0
    assert all(model is m.Audit for model in s.db.additions)
    assert not s.task.data.get(goal_guard.KEY, {}).get('items')


@pytest.mark.parametrize('fault', ['lease', 'input_hash', 'invocation', 'dispatch'])
def test_original_lease_input_and_no_intent_gates_still_precede_v285_wait_registration(scene, fault):
    s = scene
    if fault == 'lease': s.job.lease_token = None
    elif fault == 'input_hash': s.job.data['input_hash'] = '0' * 64
    elif fault == 'invocation': s.db.present_call = s.original_call
    else: s.task.data['graph_jobs'][s.activation['id']]['dispatch_committed'] = True
    with pytest.raises(HTTPException): s.wait()
    assert subject.KEY not in s.job.data and not s.db.events and not s.proof_calls
    assert s.job.status == 'running' and s.db.commits == 0
