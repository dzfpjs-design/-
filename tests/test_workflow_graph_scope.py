"""Synthetic ownership-unit checks; real PostgreSQL/lease acceptance is separate."""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents.models import Audit, Job
from generic_agents import full_semantic_graph_v1 as pure
from generic_agents import workflow_graph_scope as scope
from generic_agents import workflow_graph
from generic_agents import layered_approval_store_v175 as approvals
from tests.test_full_semantic_graph_v1 import ability, base, control, edge, object_schema, input_reference


def fixture(monkeypatch):
    node = ability('audio', {'approved': 'approved'})
    cap_data = {'execution': {'adapter': 'builtin'},
        'input_schema': node['execution']['input_schema'], 'output_schema': node['execution']['output_schema']}
    cap = NS(id='registered_ability', data=cap_data, content_hash=digest(cap_data),
        status='published', validated_hash=digest(cap_data), release_version='1.0.0')
    node['execution']['capability_hash'] = cap.content_hash
    graph = base([node, control('end', 'end')], [edge('audio', 'end')], 'audio', ['audio'])
    graph['input_schema'] = object_schema(approved={'type': 'boolean'})
    graph['references']['approved'] = input_reference('approved', 'boolean')
    state = pure.new_run(graph, {'approved': True}, 10)
    activation = state['activations'][state['eligible_ability_ids'][0]]
    arguments = pure.ability_inputs(graph, state, activation['id'])
    job = NS(id='job', kind=scope.KIND, data={'task_id': 'task', 'activation_id': activation['id'],
        'actor_id': node['actor_id'], 'graph_hash': state['graph_hash'], 'cancel_epoch': 0,
        'activation_version': 0, 'invocation_key': 'original-key',
        'inputs': arguments, 'input_hash': digest(arguments)})
    transport = {'job_id': job.id, 'invocation_key': 'original-key', 'cancel_epoch': 0,
        'input_hash': digest(arguments), 'phase': 'running'}
    task = NS(id='task', workspace_id='workspace', version=1, data={'graph_runtime': state, 'graph_jobs': {activation['id']: transport}, 'instance_id': 'instance'})
    release = NS(id='release', instance_id='instance'); business = {'execution_mode': 'confirmed_graph_workflow'}
    p = NS(user_id=node['actor_id'], workspace_id='workspace')
    call = NS(id='invocation', status='running', capability_id=cap.id, data={
        'capability_hash': cap.content_hash, 'role': 'data', 'input_hash': digest(arguments),
        'job_id': job.id, 'lease_token': 'lease'})

    class Database:
        def __init__(self): self.pending = [job, call]; self.commits = 0
        def scalar(self, query):
            # These are independent absence reads for a genuinely no-Goal
            # graph. They must not consume the original Job/call/clock oracle.
            descriptions = query.column_descriptions
            if len(descriptions) == 1 and (descriptions[0].get('entity') in {Audit, approvals.Managed, approvals.Policy}
                or descriptions[0].get('entity') is Job and descriptions[0].get('name') == 'id'):
                return None
            return self.pending.pop(0)
        def refresh(self, row): pass
        def commit(self): self.commits += 1

    db = Database()
    monkeypatch.setattr(scope, 'ownership', lambda db: {'job_id': job.id, 'lease_token': 'lease'})
    monkeypatch.setattr(scope, '_frozen', lambda db, p, task_id, activation_id:
        (task, release, business, graph, state, activation, node))
    monkeypatch.setattr(scope, 'published', lambda db, p, identity: cap)
    monkeypatch.setattr(scope, 'scoped', lambda db, model, identity, p:
        NS(id='instance', active_release_id='newer_release') if model is scope.DomainInstance else job if model is scope.Job else cap)
    monkeypatch.setattr(workflow_graph, 'live', lambda *args: None)
    return NS(db=db, p=p, task=task, graph=graph, state=state, activation=activation,
        node=node, job=job, call=call, cap=cap, transport=transport)


def test_context_uses_the_actual_graph_activation_and_never_creates_legacy_automatic(monkeypatch):
    f = fixture(monkeypatch)
    before = copy.deepcopy(f.task.data)
    task, job, node, frozen = scope.graph_context(f.db, f.p, 'original-key', f.cap)
    assert task is f.task and job is f.job and node is f.node
    assert frozen['activation_id'] == f.activation['id'] and frozen['cancel_epoch'] == 0
    assert frozen['input_hash'] == digest({'approved': True})
    assert f.task.data == before and 'workflow_runtime' not in f.task.data
    metadata = scope.metadata(task, job, node, frozen, f.cap)
    assert metadata['workflow_graph_scope'] == frozen
    assert metadata['workflow_capability_snapshot'] == f.cap.data
    metadata['workflow_graph_scope']['cancel_epoch'] = 99
    assert frozen['cancel_epoch'] == 0


@pytest.mark.parametrize('change', ['epoch', 'phase', 'actor', 'inputs', 'transport',
    'call_capability', 'call_hash', 'call_role', 'call_inputs', 'call_job', 'call_lease', 'not_running', 'cap_changed'])
def test_context_rejects_mismatched_transport_or_real_invocation(monkeypatch, change):
    f = fixture(monkeypatch)
    if change == 'epoch': f.state['cancel_epoch'] = 1
    elif change == 'phase': f.transport['phase'] = 'queued'
    elif change == 'actor': f.p.user_id = 'different_actor'
    elif change == 'inputs': f.job.data['inputs'] = {'approved': False}; f.job.data['input_hash'] = digest(f.job.data['inputs'])
    elif change == 'transport': f.transport['job_id'] = 'other_job'
    elif change == 'call_capability': f.call.capability_id = 'other_capability'
    elif change == 'call_hash': f.call.data['capability_hash'] = 'b' * 64
    elif change == 'call_role': f.call.data['role'] = 'business'
    elif change == 'call_inputs': f.call.data['input_hash'] = digest({'approved': False})
    elif change == 'call_job': f.call.data['job_id'] = 'other_job'
    elif change == 'call_lease': f.call.data['lease_token'] = 'expired_lease'
    elif change == 'not_running': f.call.status = 'succeeded'
    elif change == 'cap_changed': f.cap.status = 'archived'
    with pytest.raises(HTTPException) as error:
        scope.graph_context(f.db, f.p, 'original-key', f.cap)
    assert error.value.status_code == 409


def test_historical_record_retains_frozen_provenance_after_completion_without_live_authority(monkeypatch):
    f = fixture(monkeypatch)
    frozen = scope._scope(f.task, f.job, f.node, f.activation)
    row = NS(data=scope.metadata(f.task, f.job, f.node, frozen, f.cap))
    # record_node authenticates frozen identity, rather than reauthorizing IO.
    monkeypatch.setattr(workflow_graph, 'live', lambda *args: pytest.fail('historical receipt requested live execution'))
    f.activation['status'] = 'completed'; f.activation['version'] = 1
    f.transport['phase'] = 'succeeded'; f.cap.status = 'archived'
    assert scope.record_node(f.db, f.p, f.task, row) is f.node
    row.data['workflow_graph_scope']['activation_id'] = 'other_activation'
    with pytest.raises(HTTPException):
        scope.record_node(f.db, f.p, f.task, row)


def test_result_from_sibling_source_step_cannot_satisfy_this_node(monkeypatch):
    f = fixture(monkeypatch)
    row = NS(data=scope.metadata(f.task, f.job, f.node, scope._scope(f.task, f.job, f.node, f.activation), f.cap))
    wrong_step = {'id': 'source_other_branch', 'execution': f.node['execution']}
    with pytest.raises(HTTPException):
        scope.record_node(f.db, f.p, f.task, row, wrong_step)


def dispatch_fixture(monkeypatch):
    from generic_agents import relations
    f = fixture(monkeypatch)
    f.db.pending = [f.job, f.task, f.job, f.call, datetime(2026, 10, 19, tzinfo=timezone.utc)]
    monkeypatch.setattr(relations, 'lock_current_actor', lambda db, p: None)
    monkeypatch.setattr(scope, 'audit', lambda *args, **kwargs: None)
    return f


def test_dispatch_authorization_commits_exact_identity_and_preserves_pure_state(monkeypatch):
    f = dispatch_fixture(monkeypatch)
    before = copy.deepcopy(f.state)
    authorization = scope.before_dispatch(f.db, f.p, 'original-key', f.cap)
    assert set(authorization) == {'epoch', 'job_id', 'lease_token', 'input_hash',
        'capability_hash', 'actor_id', 'authorized_at'}
    assert authorization == {'epoch': 0, 'job_id': 'job', 'lease_token': 'lease',
        'input_hash': digest({'approved': True}), 'capability_hash': f.cap.content_hash,
        'actor_id': f.p.user_id, 'authorized_at': '2026-10-19T00:00:00+00:00'}
    assert f.task.data['graph_jobs'][f.activation['id']]['dispatch_authorization'] == authorization
    assert f.state == before and f.task.data['graph_runtime'] == before
    assert f.task.version == 2 and f.db.commits == 1


def test_cancel_committed_before_task_lock_cannot_authorize_a_dispatch(monkeypatch):
    f = dispatch_fixture(monkeypatch)
    cancelled = pure.cancel(f.graph, f.state, 'cancel_won', 'task_owner', f.state['version'], 0, 11)
    f.state.clear(); f.state.update(cancelled)
    f.activation.update(cancelled['activations'][f.activation['id']])
    with pytest.raises(HTTPException) as error:
        scope.before_dispatch(f.db, f.p, 'original-key', f.cap)
    assert error.value.status_code == 409 and f.db.commits == 0
    assert 'dispatch_authorization' not in f.task.data['graph_jobs'][f.activation['id']]


def test_persisted_dispatch_authorization_cannot_be_reused_to_send_again(monkeypatch):
    f = dispatch_fixture(monkeypatch)
    first = scope.before_dispatch(f.db, f.p, 'original-key', f.cap)
    f.db.pending = [f.job, f.task, f.job, f.call]
    with pytest.raises(HTTPException):
        scope.before_dispatch(f.db, f.p, 'original-key', f.cap)
    assert f.task.data['graph_jobs'][f.activation['id']]['dispatch_authorization'] == first
    assert f.db.commits == 1
