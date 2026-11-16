"""Real wait/kernel/audit mechanics with explicit synthetic source/IAM IO.

No PG, lease-concurrency, adopted V288 creator, acquisition, ACK or model proof
is claimed. Each installed service's full manifest and input schema run; its
independent producer checker and the scheduler's actor lookup are labelled
spies. Wait records are produced by preflight, not fabricated by the fixture.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import models as m, worker
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import workflow_graph_goal_guard_v133 as goal_guard
from generic_agents import workflow_provenance_wait_v197 as subject
from generic_agents import workflow_goal_data_route_runtime_v288 as local
from generic_agents import workflow_goal_evaluation_packet_v288 as packet
from generic_agents.contracts import Manifest, digest
from tests.test_full_semantic_graph_v1 import base, control, edge
from tests.test_workflow_goal_data_policy_v288 import scene as data_scene, ident
from tests.test_workflow_provenance_wait_v197 import scene as wait_scene, producer
from tests.test_report_business_flow_wire_v184 import no_external


def _value(module):
    saved = data_scene()
    if module is local:
        return saved.result
    snapshot = saved.result['resense_context']['gap']['observation_request']['goal_delegation']['goal_snapshot']
    producer_pin = {'schema': local.PRODUCER,
        **{key: ident(index) for index, key in enumerate(
            ('workspace_id', 'task_id', 'job_id', 'invocation_id', 'actor_id', 'capability_id'), 500)},
        'activation_id': 'synthetic-reader-activation', 'node_id': 'context_reader',
        **{key: 'a' * 64 for key in ('capability_hash', 'input_hash', 'request_key_hash',
            'graph_hash', 'source_binding_hash', 'goal_snapshot_hash')},
        'output_hash': digest(saved.result), 'preparation_policy_hash': saved.policy['policy_hash']}
    return local._output(saved.result, snapshot, saved.policy, producer_pin)


def _bind(s, module, field, value):
    role, actor = ('business', 'execution_service') if module is local else ('data', 'reader_service')
    s.cap.data = Manifest(kind='tool', namespace='goal_wait_v288', name=field, version='1.0.0',
        owner='synthetic wait boundary', description='Local producer-wait fixture; no external execution',
        allowed_agent_roles=[role], input_schema=module.input_schema(), output_schema=module.output_schema(),
        execution={'adapter': 'workflow_service', 'service_actor': actor, 'operation': module.OP, 'side_effect': True},
        specification={'schema': module.CONTRACT}, data_policy={'cloud_allowed': False},
        dependencies=[], budget={'max_attempts': 1}).model_dump(mode='json')
    s.cap.content_hash = digest(s.cap.data)
    s.inputs = {field: copy.deepcopy(value)}
    node = copy.deepcopy(s.node)
    node['execution'].update(capability_hash=s.cap.content_hash, input_schema=module.input_schema(),
        output_schema=module.output_schema(), arguments=copy.deepcopy(s.inputs),
        adapter='workflow_service', agent_role=role)
    s.graph = base([node, control('goal-data-end', 'end')], [edge(node['id'], 'goal-data-end')], node['id'], [node['id']])
    s.state = kernel.new_run(s.graph, {}, 10); s.node = node
    activation_id = s.state['eligible_ability_ids'][0]; s.activation = s.state['activations'][activation_id]
    s.job.data.update(inputs=copy.deepcopy(s.inputs), input_hash=digest(s.inputs),
        graph_hash=s.state['graph_hash'], activation_id=activation_id)
    s.frozen.update(graph_hash=s.state['graph_hash'], activation_id=activation_id, node_id=node['id'])
    s.task.data['graph_runtime'] = copy.deepcopy(s.state)
    s.task.data['graph_jobs'] = {activation_id: {'job_id': s.job.id, 'invocation_key': s.key,
        'cancel_epoch': 0, 'input_hash': digest(s.inputs), 'phase': 'queued'}}


@pytest.fixture(params=[
    (local, 'context_result', 'goal_data_prepare_v288', 'goal_data_reader_producer_v288'),
    (packet, 'local_plan', 'goal_evaluation_packet_v288', 'goal_data_barrier_producer_v288'),
], ids=['local-prepare-reader-tail', 'evaluation-packet-resense-tail'])
def scene(monkeypatch, request):
    s = wait_scene.__wrapped__(monkeypatch)
    s.module, s.field, s.family, s.kind = request.param
    _bind(s, s.module, s.field, _value(s.module))
    s.original, s.original_call, _context = producer(s)
    # A packet's barrier may be the original resense SDK, whose output is not
    # the local_plan input. The immutable source output and consumer port must
    # retain separate hashes; the source proof below is the explicit IO seam.
    source_output = s.inputs[s.field] if s.module is local else {'original_resense_receipt': 'synthetic source IO'}
    s.original_call.data = {**s.original_call.data, 'output': copy.deepcopy(source_output)}
    s.source_output = copy.deepcopy(source_output); s.proof_calls = []; s.actor_calls = []

    def source_proof(db, p, task, job, inputs):
        assert (db, p, task, job) == (s.db, s.p, s.task, s.job)
        s.proof_calls.append(s.original.status)
        checked = s.module._inputs(inputs)
        subject.need(checked == s.inputs == s.job.data['inputs']
            and digest(checked) == s.job.data['input_hash']
            and s.original_call.status == 'succeeded'
            and s.original_call.data['output'] == s.source_output)
        if s.original.status == 'running':
            raise subject.reader.ProducerPending(s.original.id, s.original_call.id)
        subject.need(s.original.status == 'succeeded')
        return {'job_id': s.original.id, 'invocation_id': s.original_call.id,
            'actor_id': s.original.data['actor_id'], 'request_key_hash': digest(s.original.data['invocation_key'])}

    def current_reader(db, workspace_id, actor_id):
        assert (db, workspace_id, actor_id) == (s.db, s.p.workspace_id, s.job.data['actor_id'])
        s.actor_calls.append(actor_id)
        return s.p

    monkeypatch.setattr(s.module, 'producer_wait_pin', source_proof)
    monkeypatch.setattr(worker, 'actor', current_reader)
    return s


def test_tail_wait_has_own_kind_and_wakes_the_original_job_without_invocation(scene):
    s = scene
    before = (s.job.id, s.job.idempotency_key, s.job.attempts, s.job.available_at,
        s.job.lease_token, s.job.lease_until, copy.deepcopy(s.inputs), s.job.data['input_hash'])
    assert subject._family(s.cap) == s.family
    assert s.wait() is True and s.job.status == subject.PHASE
    record = subject._inventory(s.db, s.task, s.job)['history'][0]
    dependency = record['dependencies'][0]
    assert dependency['kind'] == s.kind and dependency['source_path'] == '/' + s.field
    assert dependency['source_hash'] == digest(s.inputs[s.field])
    assert dependency['invocation_output_hash'] == digest(s.source_output)
    assert dependency['pin']['actor_id'] == s.original.data['actor_id'] != s.p.user_id
    unchanged = copy.deepcopy((s.job.data, s.task.data))
    assert s.schedule() == 0 and (s.job.data, s.task.data) == unchanged
    s.original.status = 'succeeded'
    assert s.schedule() == 1 and s.job.status == 'queued'
    assert subject._inventory(s.db, s.task, s.job)['active_hash'] is None
    assert [event.action for event in s.db.events] == [subject.WAIT, subject.WAKE]
    assert (s.job.id, s.job.idempotency_key, s.job.attempts, s.job.available_at,
        s.job.lease_token, s.job.lease_until, s.job.data['inputs'], s.job.data['input_hash']) == before
    assert s.proof_calls == ['running', 'running', 'succeeded']
    assert s.actor_calls == [s.p.user_id, s.p.user_id]
    assert s.db.additions == [m.Audit, m.Audit] and s.db.present_call is None and s.db.commits == 0
    assert not s.task.data.get(goal_guard.KEY, {}).get('items')
    assert not set(s.task.data['graph_jobs'][s.activation['id']]) & {
        'dispatch_committed', 'dispatch_ownership', 'dispatch_input_hash', 'dispatch_authorization'}
    assert subject.can_start_after_wait(s.db, s.task, s.job) is True
    s.job.status = 'running'; s.job.attempts += 1
    assert s.wait() is False and len(s.db.events) == 2


def test_changed_barrier_output_does_not_wake_the_current_consumer(scene):
    s = scene; assert s.wait() is True
    s.original.status = 'succeeded'; s.original_call.data['output'] = {'replacement': 'not the saved source'}
    assert s.schedule() == 0 and s.job.status == 'blocked'
    assert not any(event.action == subject.WAKE for event in s.db.events)
    assert s.db.present_call is None and s.db.commits == 0


def test_existing_consumer_invocation_cannot_be_replaced_by_a_wait(scene):
    s = scene; s.db.present_call = s.original_call
    with pytest.raises(HTTPException): s.wait()
    assert subject.KEY not in s.job.data and not s.db.events and not s.proof_calls
    assert s.job.status == 'running' and s.db.commits == 0


def test_resealed_record_cannot_borrow_another_consumer_port(scene):
    s = scene; assert s.wait() is True
    ledger = copy.deepcopy(s.job.data[subject.KEY]); record = ledger['history'][0]
    record['dependencies'][0]['source_path'] = '/local_plan' if s.field == 'context_result' else '/context_result'
    record['record_hash'] = digest({key: value for key, value in record.items() if key != 'record_hash'})
    ledger['active_hash'] = record['record_hash']; s.job.data = {**s.job.data, subject.KEY: ledger}
    s.db.events[0].data = subject._wait_audit(s.job, record)
    s.task.data['graph_jobs'][s.activation['id']]['provenance_wait_hash'] = record['record_hash']
    s.original.status = 'succeeded'
    assert s.schedule() == 0 and s.job.status == 'blocked'
    assert not any(event.action == subject.WAKE for event in s.db.events)
    assert s.db.present_call is None and s.db.commits == 0
