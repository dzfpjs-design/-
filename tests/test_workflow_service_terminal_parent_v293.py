"""Bounded terminal-parent writer/reader checks with explicit IO boundaries.

Actual manifest, STATE/STARTED, original invoke and terminal-Audit hashes run.
DB rows/clock/fence and the normal reconcile source/ACK boundary are spies;
these tests prove no PG/actual lease, acquisition, observation or SDK success.
Root separately runs the retained original Job and full parent_pin path.
"""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import leases, workflow_graph
from generic_agents import workflow_job_dependencies_v264 as subject
from generic_agents import workflow_service_v195 as shared, workflow_service_context_v196 as variant
from generic_agents.contracts import Manifest, digest


def uid(value): return str(UUID(int=value))


def scene(monkeypatch):
    at = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    p = NS(workspace_id=uid(1), user_id=uid(2), role='operator')
    manifest = Manifest(kind='tool', namespace='terminal_parent_test_v293', name='context_resense',
        version=variant.RELEASE_VERSION, owner='独立协议核验', description='固定原异步服务端口',
        allowed_agent_roles=['business'], input_schema=variant.input_schema(), output_schema=variant.output_schema(),
        execution={'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': variant.OP, 'side_effect': True}, budget={'max_attempts': 1, 'max_concurrency': 1}).model_dump(mode='json')
    cap = NS(id=uid(3), workspace_id=p.workspace_id, data=manifest, content_hash=digest(manifest),
        validated_hash=digest(manifest), release_version=variant.RELEASE_VERSION)
    task = NS(id=uid(4), workspace_id=p.workspace_id, data={})
    node = {'id': 'resense', 'kind': 'ability', 'actor_id': p.user_id,
        'execution': {'capability_id': cap.id, 'capability_hash': cap.content_hash}}
    inputs = {'context': {'explicit_original_source_IO_boundary': True}}
    key = 'graph:' + task.id + ':original_resense_activation:1'
    job = NS(id=uid(5), workspace_id=p.workspace_id, kind='execute_full_semantic_graph_node', status='running',
        lease_token=uid(6), lease_until=at + timedelta(minutes=1), idempotency_key=key,
        data={'task_id': task.id, 'actor_id': p.user_id, 'activation_id': 'original_resense_activation',
            'graph_hash': 'a' * 64, 'cancel_epoch': 0, 'input_hash': digest(inputs), 'inputs': inputs})
    approval = shared.make_approval(task, job, node, inputs)
    binding = {'schema': 'authored-goal-data-workflow-binding-v288', 'binding_hash': 'b' * 64, 'simulation_only': True}
    call = NS(id=uid(7), workspace_id=p.workspace_id, status='succeeded', idempotency_key=key,
        data={'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs), 'approval': approval,
            'service_previous_status': 'waiting_receipt', 'service_receipt_verified_at': at.isoformat(),
            'output': {'explicit_terminal_source_and_ACK_IO_boundary': True}})
    state = {'schema': shared.STATE, 'operation': variant.OP, 'task_id': task.id, 'job_id': job.id,
        'activation_id': job.data['activation_id'], 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'input_hash': digest(inputs), 'approval_hash': digest(approval), 'source_binding_hash': binding['binding_hash'],
        'invocation_id': call.id, 'request_key': key, 'actor_id': p.user_id,
        'created_at': (at - timedelta(minutes=1)).isoformat(), 'simulation_only': True,
        'operation_data': {'explicit_real_effect_inventory_IO_boundary': True}}
    state['state_hash'] = digest(state); call.data['service_state'] = state
    source = {'explicit_original_source_proof_IO_boundary': True}
    call.data[variant.PROOF_KEY] = {**source, 'proof_hash': digest(source)}
    initial = NS(id=uid(8), actor_id=p.user_id, data={'status': 'waiting_receipt'})
    rows = {'capability.invoke': [initial], shared.STARTED: [NS(actor_id=p.user_id, data=shared._audit_data(state))],
        'workflow.service.receipt_verified': [NS(actor_id=p.user_id,
            data={'job_id': job.id, 'activation_id': job.data['activation_id']})], subject.SERVICE_TERMINAL: []}
    reads = []
    class DB:
        info = {'worker_lease': {'job_id': job.id, 'lease_token': job.lease_token}, 'explicit_fence_IO_boundary': True}
        def scalar(self, statement):
            assert 'clock_timestamp' in str(statement)
            return at
        def scalars(self, statement):
            compiled = str(statement.compile(compile_kwargs={'literal_binds': True}))
            assert p.workspace_id in compiled and call.id in compiled
            matches = [action for action in rows if "'" + action + "'" in compiled]
            assert len(matches) == 1
            assert matches[0] == shared.STARTED or 'LIMIT' in compiled
            reads.append(matches[0]); return rows[matches[0]]
        def add(self, row):
            assert row.action == subject.SERVICE_TERMINAL and row.object_id == call.id
            rows[row.action].append(row)
    db = DB()
    monkeypatch.setattr(leases, 'fenced', lambda actual: actual is db and db.info['explicit_fence_IO_boundary'])
    monkeypatch.setattr(workflow_graph, 'business_binding', lambda *args: copy.deepcopy(binding))
    def original_receipt(actual_db, reader, actual_task, original_job, original_call, actual_cap, actual_node, actual_binding, release):
        assert actual_db is db and reader is p and actual_task is task and original_job is job and original_call is call
        assert actual_cap is cap and actual_node is node and actual_binding == binding
        reads.append('explicit_immutable_terminal_receipt_IO_boundary')
        return copy.deepcopy(call.data['output'])
    monkeypatch.setattr(subject, '_terminal_service_receipt', original_receipt)
    from generic_agents import workflow_resense_goal_receipts_v271 as receipts
    monkeypatch.setattr(receipts, '_source_proof', lambda *args: copy.deepcopy(call.data[variant.PROOF_KEY]))
    monkeypatch.setattr(subject, '_terminal_reader_context_v288', lambda *args: copy.deepcopy(inputs['context']))
    receipt = {'actor': p.user_id, 'output': copy.deepcopy(call.data['output'])}
    return NS(p=p, task=task, job=job, call=call, cap=cap, node=node, binding=binding, approval=approval,
        inputs=inputs, db=db, rows=rows, reads=reads, initial=initial, receipt=receipt)


def write(s):
    return subject.record_terminal_service_parent(s.db, s.p, s.task, s.job, s.call, s.cap,
        s.node, s.binding, s.inputs, s.approval)


def read(s):
    return subject._invoke_parent_proof(s.db, s.p, s.task, s.job, s.call, s.cap, s.receipt, NS(), NS(), s.node)


def test_normal_waiting_invoke_writer_adds_independent_terminal_hash_then_reader_checks_original_receipt(monkeypatch):
    s = scene(monkeypatch); original = copy.deepcopy((s.initial.data, s.call.data, s.job.data))
    assert write(s) is True
    assert s.job.status == 'running'  # Parent handler is atomically saving control, not yet succeeded.
    proof = s.rows[subject.SERVICE_TERMINAL][0].data
    assert proof['output_hash'] == digest(s.call.data['output']) and proof['state_hash'] == s.call.data['service_state']['state_hash']
    assert proof['initial_invoke_audit_id'] == s.initial.id
    assert proof['record_hash'] == digest({key: value for key, value in proof.items() if key != 'record_hash'})
    read(s)
    assert 'explicit_immutable_terminal_receipt_IO_boundary' in s.reads
    assert (s.initial.data, s.call.data, s.job.data) == original


@pytest.mark.parametrize('fault', ['not_fenced', 'wrong_owner', 'expired'])
def test_running_parent_writer_requires_the_actual_current_fence_without_pretending_job_succeeded(monkeypatch, fault):
    s = scene(monkeypatch)
    if fault == 'not_fenced': s.db.info['explicit_fence_IO_boundary'] = False
    if fault == 'wrong_owner': s.db.info['worker_lease']['lease_token'] = uid(99)
    if fault == 'expired': s.job.lease_until = datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc)
    with pytest.raises(HTTPException): write(s)
    assert not s.rows[subject.SERVICE_TERMINAL] and s.job.status == 'running'


@pytest.mark.parametrize('fault', ['unknown_initial', 'stateless'])
def test_unknown_original_or_missing_typed_state_cannot_become_a_terminal_parent(monkeypatch, fault):
    s = scene(monkeypatch)
    if fault == 'unknown_initial': s.initial.data = {'status': 'unknown'}
    else: s.call.data.pop('service_state')
    with pytest.raises(HTTPException): write(s)
    assert not s.rows[subject.SERVICE_TERMINAL]


@pytest.mark.parametrize('fault', ['terminal_audit', 'output', 'duplicate_initial'])
def test_rehashed_output_or_audit_and_ambiguous_initial_sdk_never_qualify(monkeypatch, fault):
    s = scene(monkeypatch); write(s)
    if fault == 'terminal_audit':
        proof = s.rows[subject.SERVICE_TERMINAL][0].data
        proof['output_hash'] = '0' * 64
        proof['record_hash'] = digest({key: value for key, value in proof.items() if key != 'record_hash'})
    if fault == 'output':
        s.call.data['output'] = {'different_terminal_output': True}; s.receipt['output'] = copy.deepcopy(s.call.data['output'])
    if fault == 'duplicate_initial': s.rows['capability.invoke'].append(copy.deepcopy(s.initial))
    with pytest.raises(HTTPException): read(s)


def test_original_synchronous_succeeded_invoke_path_is_preserved(monkeypatch):
    s = scene(monkeypatch); s.initial.data = {'status': 'succeeded'}
    read(s)
    assert s.reads == ['capability.invoke'] and not s.rows[subject.SERVICE_TERMINAL]


def test_original_terminal_audit_survives_normal_job_lease_reclaim_between_handler_and_final_commit(monkeypatch):
    s = scene(monkeypatch); write(s)
    original = copy.deepcopy(s.rows[subject.SERVICE_TERMINAL][0].data)
    s.job.lease_token = uid(90)  # Normal reclaim changes owner, never the committed result.
    s.job.status = 'succeeded'
    read(s)
    assert s.rows[subject.SERVICE_TERMINAL][0].data == original
    assert s.initial.data == {'status': 'waiting_receipt'}


@pytest.mark.parametrize('fault', [None, 'present_inputs_drift', 'present_snapshot_drift', 'present_snapshot_boolean_drift'])
def test_normal_builtin_reader_omitted_sdk_metadata_uses_frozen_original_proofs_without_fabricating_authorization(monkeypatch, fault):
    from generic_agents import workflow_task_context_v196 as reader, workflow_resense_goal_receipts_v271 as receipts
    # Preserve the real helper before scene installs its normal writer IO seam.
    real_original_reader = subject._terminal_reader_context_v288
    s = scene(monkeypatch)
    body = Manifest(kind='tool', namespace='terminal_parent_test_v293', name='original_reader', version='1.0.0',
        owner='独立协议核验', description='原builtin读取契约', allowed_agent_roles=['data'],
        input_schema=reader.input_schema(), output_schema=reader.output_schema(),
        execution={'adapter': 'builtin', 'handler': reader.HANDLER, 'side_effect': False}).model_dump(mode='json')
    cap = NS(id=uid(31), data=body, content_hash=digest(body), validated_hash=digest(body))
    inputs = {'policy_pin': None}; output = {'actual_source_proof_IO_boundary': True}
    job = NS(id=uid(32), kind='execute_full_semantic_graph_node', status='succeeded',
        idempotency_key='original-reader-key', data={'inputs': inputs, 'input_hash': digest(inputs), 'invocation_key': 'original-reader-key'})
    call = NS(id=uid(33), capability_id=cap.id, status='succeeded', idempotency_key=job.idempotency_key,
        data={'job_id': job.id, 'role': 'data', 'side_effect': False, 'input_hash': digest(inputs), 'output': output})
    value = {'producer': {'node_id': 'context_reader', 'job_id': job.id, 'invocation_id': call.id,
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'input_hash': digest(inputs),
        'activation_id': 'original-reader-activation', 'actor_id': uid(34)}}
    pin = {'job_id': job.id, 'invocation_id': call.id, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'input_hash': digest(inputs), 'output_hash': digest(output), 'activation_id': 'original-reader-activation', 'actor_id': uid(34)}
    if fault == 'present_inputs_drift': call.data['inputs'] = {'policy_pin': {'changed': True}}
    if fault == 'present_snapshot_drift': call.data['capability_snapshot'] = {'copied_label_only': True}
    if fault == 'present_snapshot_boolean_drift':
        call.data['capability_snapshot'] = copy.deepcopy(cap.data)
        call.data['capability_snapshot']['execution']['side_effect'] = 0
    # Complete original SDK/PRODUCED/Goal/Q0 proof and parent-pin custody are
    # explicit IO boundaries; optional metadata and real manifest checks run.
    order = []
    def fresh(db, p, cls, identity):
        order.append(cls)
        return job if cls is subject.Job else call if cls is subject.Invocation else cap
    def producer(*args, **kwargs):
        order.append('original_PRODUCED_proof')
        return NS()
    monkeypatch.setattr(reader, '_checked_producer_context', producer)
    monkeypatch.setattr(receipts, 'fresh', fresh)
    monkeypatch.setattr(subject, 'parent_pin', lambda *args: copy.deepcopy(pin))
    before = copy.deepcopy(call.data)
    if fault:
        with pytest.raises(HTTPException): real_original_reader(s.db, s.p, s.task, value)
    else:
        assert real_original_reader(s.db, s.p, s.task, value) == value
        assert 'inputs' not in call.data and 'capability_snapshot' not in call.data
        assert 'dispatch_authorization' not in call.data
    assert call.data == before
    assert order[:4] == [subject.Job, subject.Invocation, subject.Capability, 'original_PRODUCED_proof']
