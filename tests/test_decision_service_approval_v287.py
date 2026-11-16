"""V286 service approval regression with explicit authority/SQL boundary spies.

The actual V195 validator, V286 complete binding checker, V285 manifest,
service eligibility and input validation run here. Fixed graph/lease/IAM/source
boundaries are spies, not proof of real adoption, SQL locking, IAM or E2E work.
No planner producer, model, Acquisition, dispatch or ACK is manufactured.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from generic_agents import workflow_service_v195 as subject
from generic_agents import workflow_decision_tool_planner_v285 as planner
from generic_agents import authored_decision_workflow_store_v286 as family
from generic_agents.contracts import digest
from tests.test_authored_decision_workflow_v286 import command, cold_binding, ident
from tests.test_workflow_decision_tool_planner_v285 import case


def _sql(statement):
    return str(statement.compile(compile_kwargs={'literal_binds': True}))


def scene(monkeypatch):
    from generic_agents import full_semantic_graph_v1 as kernel
    from generic_agents import workflow_graph as workflow, workflow_graph_scope as scope
    from generic_agents import leases, relations, layered_approval_store_v175 as approvals
    from generic_agents import workflow_graph_goal_guard_v133 as goals

    value = case()
    cap = value.cap
    cap.kind = 'tool'; cap.status = 'published'
    cap.validated_hash = cap.content_hash; cap.release_version = cap.data['version']
    body = command()
    body['tool_bindings']['planner'].update(capability_id=cap.id, capability_hash=cap.content_hash)
    body['tool_bindings']['resense'].update({key: value.target[key]
        for key in ('capability_id', 'capability_hash')})
    body['candidate_specs'][0]['capability_pins'][0].update(value.target)
    binding = cold_binding(body)
    # This is the real complete family checker, not a delegation-only stub.
    assert family.check_binding(binding) == binding
    graph = binding['graph']; node = next(row for row in graph['nodes'] if row['id'] == 'planner')
    p = NS(workspace_id=ident(501), user_id=node['actor_id'], role='operator')
    inputs = {'decision_result': copy.deepcopy(value.result)}
    activation = {'id': 'planner_activation', 'node_id': node['id'], 'status': 'ready', 'epoch': 0}
    state = {'status': 'running', 'cancel_epoch': 0, 'graph_hash': digest(graph),
        'eligible_ability_ids': [activation['id']]}
    task = NS(id=ident(502), workspace_id=p.workspace_id, data={'graph_runtime': state})
    key = 'graph:' + task.id + ':' + activation['id'] + ':1'
    job = NS(id=ident(503), workspace_id=p.workspace_id, status='running', kind=scope.KIND,
        lease_token=ident(504), data={'task_id': task.id, 'actor_id': p.user_id,
            'activation_id': activation['id'], 'graph_hash': state['graph_hash'],
            'cancel_epoch': 0, 'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs),
            'invocation_key': key})
    transport = {'phase': 'running'}
    release, business = NS(id=ident(505)), {'name': binding['business_name']}
    approval = subject.make_approval(task, job, node, inputs)
    calls = []; faults = set()

    class IO:
        """Match only the two actual SELECTs; no replacement persistence API."""
        def scalar(self, query):
            expected_job = select(subject.Job).where(subject.Job.id == approval['job_id'],
                subject.Job.workspace_id == p.workspace_id, subject.Job.kind == scope.KIND,
                subject.Job.status == 'running', subject.Job.lease_token == job.lease_token,
                subject.Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True)
            expected_task = select(subject.Task).where(subject.Task.workspace_id == p.workspace_id,
                subject.Task.id == approval['task_id']).with_for_update().execution_options(populate_existing=True)
            assert query.get_execution_options().get('populate_existing') is True
            if _sql(query) == _sql(expected_job): return job
            assert _sql(query) == _sql(expected_task), 'unrecognized SQL is rejected'
            return task

        def commit(self): pytest.fail('validator must not commit')
        def flush(self): pytest.fail('validator must not flush')

    db = IO()
    monkeypatch.setattr(subject, 'published', lambda actual_db, actual_p, identity: cap)
    monkeypatch.setattr(leases, 'ownership', lambda actual_db:
        {'job_id': job.id, 'lease_token': job.lease_token})
    monkeypatch.setattr(scope, '_frozen', lambda *args:
        (task, release, business, graph, state, activation, node))
    monkeypatch.setattr(scope, '_job_matches', lambda *args: transport)
    monkeypatch.setattr(kernel, 'ability_inputs', lambda *args: copy.deepcopy(inputs))
    monkeypatch.setattr(workflow, 'business_binding', lambda *args: binding)
    actual_delegations = family.service_delegations

    def delegations(actual):
        calls.append('complete-binding')
        return actual_delegations(actual)

    monkeypatch.setattr(family, 'service_delegations', delegations)

    def gate(name):
        def check(*args, **kwargs):
            calls.append(name)
            if name in faults: raise HTTPException(409, 'synthetic boundary denial: ' + name)
        return check

    monkeypatch.setattr(approvals, 'guard', gate('approval'))
    monkeypatch.setattr(workflow, 'live', gate('live'))
    monkeypatch.setattr(goals, 'before_dispatch', gate('before-dispatch'))
    monkeypatch.setattr(relations, 'lock_current_actor', gate('actor'))
    return NS(db=db, p=p, cap=cap, inputs=inputs, key=key, approval=approval,
        task=task, job=job, node=node, binding=binding, transport=transport,
        release=release, calls=calls, faults=faults)


def checked(s):
    # Keep the production function body, installed catalog branch and manifest.
    return subject.validate_call_approval(s.db, s.p, s.cap, 'business',
        s.inputs, s.key, s.approval, _variant=planner)


def test_v286_delegation_continues_through_every_original_gate_and_returns_context(monkeypatch):
    s = scene(monkeypatch)
    result = checked(s)
    assert set(result) == {'task', 'job', 'node', 'release', 'binding', 'transport', 'delegation', 'capability'}
    assert result['task'] is s.task and result['job'] is s.job and result['capability'] is s.cap
    assert result['binding'] is s.binding and result['transport'] is s.transport
    assert result['delegation'] == {'service_actor': 'execution_service',
        'operation': planner.OP, 'agent_role': 'business'}
    assert s.calls == ['complete-binding', 'approval', 'live', 'before-dispatch', 'actor']


@pytest.mark.parametrize('boundary', ['approval', 'live', 'before-dispatch', 'actor'])
def test_v286_does_not_skip_original_denial_after_binding_check(monkeypatch, boundary):
    s = scene(monkeypatch); s.faults.add(boundary)
    with pytest.raises(HTTPException, match='synthetic boundary denial'):
        checked(s)
    assert s.calls[0] == 'complete-binding' and s.calls[-1] == boundary
    expected = ['complete-binding', 'approval', 'live', 'before-dispatch', 'actor']
    assert s.calls == expected[:expected.index(boundary) + 1]


def test_v286_node_capability_mismatch_is_rejected_before_late_authority(monkeypatch):
    s = scene(monkeypatch)
    # Re-seal a changed actual capability; the frozen graph still pins its old
    # hash. Common node/capability checks must reject it after the family gate.
    s.cap.data['description'] += ' changed'
    s.cap.content_hash = s.cap.validated_hash = digest(s.cap.data)
    with pytest.raises(HTTPException): checked(s)
    assert s.calls == ['complete-binding']


def test_v286_mismatched_actual_actor_is_rejected_before_delegation(monkeypatch):
    s = scene(monkeypatch); s.p.user_id = ident(599)
    with pytest.raises(HTTPException): checked(s)
    assert s.calls == []


def test_v286_tampered_delegation_is_not_a_complete_binding(monkeypatch):
    s = scene(monkeypatch)
    s.binding['service_delegations']['planner']['operation'] = 'resense_from_context'
    # A matching self-declared binding hash does not make the graph rebuild or
    # fixed family delegation agree with the forged command.
    s.binding['binding_hash'] = digest({key: value for key, value in s.binding.items() if key != 'binding_hash'})
    with pytest.raises(HTTPException): checked(s)
    assert s.calls == ['complete-binding']
