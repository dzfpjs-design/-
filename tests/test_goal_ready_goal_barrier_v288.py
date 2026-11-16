"""Actual V197 wait mechanics and closed Goal-job barrier with explicit IO spies.

The adopted scope, real packet cold proof and provider Memory/audit proof are
separate labelled boundaries here. These tests do not prove real PG, IAM,
reception, Goal execution or a cloud call. No wait record is hand-constructed.
"""
import copy

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from generic_agents import models as m, worker
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import workflow_provenance_wait_v197 as waits
from generic_agents import workflow_goal_ready_goal_barrier_v288 as barrier
from generic_agents import business_goal_ready_runtime_v288 as runtime
from generic_agents import workflow_graph_goal_guard_v133 as goal_guard
from generic_agents.contracts import Manifest, digest
from generic_agents.goals import snapshot
from tests.test_full_semantic_graph_v1 import base, control, edge
from tests.test_workflow_provenance_wait_v197 import scene as wait_scene
from tests.test_workflow_task_context_v196 import identity, same_sql
from tests.test_authored_goal_data_workflow_v288 import command
from tests.test_report_business_flow_wire_v184 import no_external


@pytest.fixture
def scene(monkeypatch):
    s = wait_scene.__wrapped__(monkeypatch)
    s.ref = {'id': identity('evaluation'), 'record_hash': '1' * 64}
    s.inputs = {'question': '按已独立核对的资料比较业务建议',
        'data_packet': {'id': s.ref['id'], 'evidence_id': identity('evaluation-evidence'), 'sha256': '2' * 64,
            'version': 1, 'simulation_only': True, 'proposal_only': True, 'cloud_authorized': False},
        'document_packet': {'id': identity('document-packet'), 'evidence_id': identity('document-evidence'),
            'sha256': '3' * 64, 'version': 1, 'source_set_id': identity('source-set'), 'source_set_hash': '4' * 64},
        'candidate_specs': command()['candidate_specs'], 'decision_intent': 'information_acquisition'}
    profile = identity('model-profile')
    s.cap.data = Manifest(kind='tool', namespace='goal_ready_wait', name='reasoning', version='1.0.0',
        owner='synthetic Goal barrier IO', description='No model execution in this wait fixture',
        allowed_agent_roles=['business'], input_schema=runtime.input_schema(), output_schema=runtime.output_schema(),
        execution={'adapter': runtime.ADAPTER, 'model_profile_id': profile, 'model_profile_hash': '5' * 64,
            'side_effect': False}, specification={'schema': runtime.CONTRACT, 'instruction': '保留来源与不确定性。'},
        dependencies=[profile], data_policy={'cloud_allowed': True}, budget={'max_attempts': 1}).model_dump(mode='json')
    s.cap.workspace_id = s.p.workspace_id; s.cap.kind = 'tool'; s.cap.status = 'published'
    s.cap.content_hash = s.cap.validated_hash = digest(s.cap.data); s.cap.release_version = '1.0.0'
    node = copy.deepcopy(s.node); node['id'] = 'reasoning'
    node['execution'].update(capability_hash=s.cap.content_hash, input_schema=runtime.input_schema(),
        output_schema=runtime.output_schema(), arguments=copy.deepcopy(s.inputs),
        adapter=runtime.ADAPTER, agent_role='business', release_version='1.0.0', max_attempts=1)
    s.graph = base([node, control('goal-ready-end', 'end')], [edge('reasoning', 'goal-ready-end')], 'reasoning', ['reasoning'])
    s.state = kernel.new_run(s.graph, {}, 10); s.node = node
    activation_id = s.state['eligible_ability_ids'][0]; s.activation = s.state['activations'][activation_id]
    s.job.data.update(inputs=copy.deepcopy(s.inputs), input_hash=digest(s.inputs),
        graph_hash=s.state['graph_hash'], activation_id=activation_id)
    s.frozen.update(graph_hash=s.state['graph_hash'], activation_id=activation_id, node_id='reasoning')
    s.task.data['graph_runtime'] = copy.deepcopy(s.state)
    s.task.data['graph_jobs'] = {activation_id: {'job_id': s.job.id, 'invocation_key': s.key,
        'cancel_epoch': 0, 'input_hash': digest(s.inputs), 'phase': 'queued'}}
    s.row = type('SyntheticEvaluation', (), {})()
    for key, value in {'id': s.ref['id'], 'record_hash': s.ref['record_hash'], 'workspace_id': s.p.workspace_id,
            'task_id': s.task.id, 'instance_id': s.release.instance_id, 'release_id': s.release.id,
            'package_id': s.task.package_id, 'actor_id': identity('evaluation-actor'),
            'job_id': identity('evaluation-producer'), 'source_binding_hash': '6' * 64,
            'graph_hash': digest(s.graph), 'goal_snapshot_hash': digest(snapshot(s.task))}.items():
        setattr(s.row, key, value)
    s.row.data = {'producer': {'actor_id': s.row.actor_id},
        'scope': {'native_goal_context_hash': s.task.data['native_goal_context_hash']}}
    s.binding = {'schema': barrier.provider.BINDING, 'binding_hash': s.row.source_binding_hash, 'graph': s.graph}
    s.lookup_key = 'native-goal:' + s.task.id + ':' + s.task.data['native_goal_context_hash'] + ':goal-evaluation-v288:' + s.row.id
    s.goal = m.Job(id=identity('evaluation-goal-check'), workspace_id=s.p.workspace_id,
        kind=barrier.provider.KIND, status='queued', idempotency_key=s.lookup_key,
        data={'task_id': s.task.id, 'actor_id': s.row.actor_id,
            'context_hash': s.task.data['native_goal_context_hash'],
            barrier.provider.JOB_REF: copy.deepcopy(s.ref), barrier.provider.JOB_PRODUCER: s.row.job_id})
    s.db.rows[m.Job, s.goal.id] = s.goal
    s.source_calls = []; s.completed_calls = []; s.provider_error = None; s.actor_calls = []
    previous_scalar = s.db.scalar

    def scalar(statement):
        expected = select(m.Job).where(m.Job.workspace_id == s.p.workspace_id,
            m.Job.kind == barrier.provider.KIND, m.Job.idempotency_key == s.lookup_key)
        if same_sql(statement, expected): return s.goal
        return previous_scalar(statement)

    def fresh(db, p, model, identifier):
        # Actual scope/source cold verification is explicitly replaced at this
        # IO boundary; the barrier's independent cross-comparisons still run.
        assert (db, p) == (s.db, s.p)
        if model is m.Capability and identifier == s.cap.id: return s.cap
        if model is barrier.Evaluation and identifier == s.row.id: return s.row
        raise AssertionError('unexpected barrier scope lookup')

    def original_packet(db, p, task, reference, *, allow_pending, live):
        assert (db, p, task) == (s.db, s.p, s.task) and live is True and allow_pending is True
        assert reference == s.inputs['data_packet']
        s.source_calls.append(copy.deepcopy(reference))
        return s.release, '7' * 64, {'packet': copy.deepcopy(reference), 'evaluation_record': copy.deepcopy(s.row.data)}

    def current(db, p, task, *, expected_ref):
        # The production provider verifies actual selected Audit + Memory +
        # terminal Goal Job. This spy exercises mandatory delegation/identity.
        assert (db, p, task) == (s.db, s.p, s.task)
        s.completed_calls.append(copy.deepcopy(expected_ref))
        if s.provider_error: raise s.provider_error
        assert s.goal.status == 'succeeded' and expected_ref == s.ref
        return {'evaluation_ref': copy.deepcopy(s.ref), 'goal_check_job_id': s.goal.id,
            'goal_check_id': identity('goal-memory'), 'report_hash': '8' * 64}

    def actor(db, workspace_id, actor_id):
        assert (db, workspace_id, actor_id) == (s.db, s.p.workspace_id, s.job.data['actor_id'])
        s.actor_calls.append(actor_id)
        return s.p

    monkeypatch.setattr(s.db, 'scalar', scalar)
    monkeypatch.setattr(barrier.sources, 'fresh', fresh)
    monkeypatch.setattr(barrier.sources, '_data', original_packet)
    monkeypatch.setattr(barrier.provider, 'binding', lambda db, p, task: {
        'task': s.task, 'release': s.release, 'binding': s.binding})
    monkeypatch.setattr(barrier.provider, 'require_current', current)
    monkeypatch.setattr(worker, 'actor', actor)
    return s


@pytest.mark.parametrize('pending_status', ['queued', 'running'])
def test_actual_preflight_waits_for_same_evaluation_goal_job_and_requeues_without_sdk(scene, pending_status):
    s = scene; s.goal.status = pending_status
    before = (s.job.id, s.job.idempotency_key, s.job.attempts, s.job.available_at,
        s.job.lease_token, s.job.lease_until, copy.deepcopy(s.inputs), s.job.data['input_hash'])
    assert waits._family(s.cap) == barrier.FAMILY and s.wait() is True
    ledger = waits._inventory(s.db, s.task, s.job); dependency = ledger['history'][0]['dependencies'][0]
    assert dependency['kind'] == barrier.KIND and dependency['source_path'] == '/data_packet'
    assert dependency['job_id'] == s.goal.id and dependency['pin']['evaluation_ref'] == s.ref
    assert dependency['pin']['actor_id'] == s.row.actor_id != s.p.user_id
    assert dependency['invocation_id'] is dependency['invocation_output_hash'] is None
    assert s.schedule() == 0 and s.job.status == waits.PHASE and s.completed_calls == []
    s.goal.status = 'succeeded'
    assert s.schedule() == 1 and s.job.status == 'queued' and s.completed_calls == [s.ref]
    assert (s.job.id, s.job.idempotency_key, s.job.attempts, s.job.available_at,
        s.job.lease_token, s.job.lease_until, s.job.data['inputs'], s.job.data['input_hash']) == before
    assert [item.action for item in s.db.events] == [waits.WAIT, waits.WAKE]
    assert s.db.additions == [m.Audit, m.Audit] and s.db.present_call is None and s.db.commits == 0
    assert not s.task.data.get(goal_guard.KEY, {}).get('items')
    assert not set(s.task.data['graph_jobs'][s.activation['id']]) & {
        'dispatch_committed', 'dispatch_ownership', 'dispatch_input_hash', 'dispatch_authorization'}


@pytest.mark.parametrize('fault', ['ref', 'producer', 'actor', 'context', 'key'])
def test_marker_or_a_similar_goal_job_cannot_replace_original_queue_identity(scene, fault):
    s = scene
    if fault == 'ref': s.goal.data[barrier.provider.JOB_REF] = {'id': identity('other-evaluation'), 'record_hash': '1' * 64}
    elif fault == 'producer': s.goal.data[barrier.provider.JOB_PRODUCER] = identity('other-producer')
    elif fault == 'actor': s.goal.data['actor_id'] = s.p.user_id
    elif fault == 'context': s.goal.data['context_hash'] = '0' * 64
    else: s.goal.idempotency_key += ':replacement'
    with pytest.raises(HTTPException): s.wait()
    assert waits.KEY not in s.job.data and s.db.present_call is None and not s.db.events


def test_terminal_job_and_task_head_do_not_replace_actual_provider_completion(scene):
    s = scene; assert s.wait() is True
    s.goal.status = 'succeeded'; s.task.data[barrier.provider.KEY] = {'evaluation_ref': copy.deepcopy(s.ref)}
    s.provider_error = HTTPException(409, 'synthetic missing actual Memory/audit proof')
    assert s.schedule() == 0 and s.job.status == 'blocked' and s.completed_calls == [s.ref]
    assert not any(item.action == waits.WAKE for item in s.db.events)
    assert s.db.present_call is None and s.db.commits == 0


@pytest.mark.parametrize('status', ['failed', 'unknown', 'cancelled'])
def test_failed_or_unknown_goal_check_preserves_original_wait_and_never_calls_model(scene, status):
    s = scene; assert s.wait() is True
    original_wait = copy.deepcopy(s.job.data[waits.KEY]); s.goal.status = status
    assert s.schedule() == 0 and s.job.status == 'blocked'
    assert s.job.data[waits.KEY] == original_wait and s.goal.status == status and s.completed_calls == []
    assert s.db.present_call is None and s.db.commits == 0
