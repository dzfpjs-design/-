"""Strict pregraph lifecycle regression; explicit IO/approval proof boundaries.

Uses the real server initializing context, authority hash and sealed row. The
managed-row lookup and approval_starter are spies, not proof of PG commands,
IAM, submissions or receipts. Real approved Task/start verification remains a
separate target-runtime requirement. No query, model or graph is initialized.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import goal_evaluation_provider_v288 as subject
from generic_agents import layered_approval_store_v175 as approval
from generic_agents import workflow_graph as graphs
from generic_agents import workspace_api
from generic_agents import authored_goal_data_workflow_store_v288 as family
from generic_agents.contracts import digest


KEY = 'layered_approval_initializing_v175'


@pytest.fixture
def seam(monkeypatch):
    db = NS(info={}, transaction=True)
    db.in_transaction = lambda: db.transaction
    p = NS(workspace_id='workspace', user_id='original-executor')
    task = NS(id='original-task', workspace_id=p.workspace_id,
        package_id='package', status='workflow_waiting', data={})
    row = NS(id='managed', workspace_id=p.workspace_id, task_id=task.id,
        policy_id='policy', policy_version_id='policy-version', owner_id='owner',
        status='executing', version=2, data={})
    approval.seal(row, {'execution': {'initialized': False, 'actor_id': p.user_id}})
    business = {'id': 'business', 'name': 'approved business', 'flow': [],
        'execution_mode': 'confirmed_graph_workflow'}
    release = NS(id='release', instance_id='instance', package_id=task.package_id,
        data={'snapshot': {'businesses': [business]}})
    value = {'schema': subject.BINDING, 'graph': {'frozen': 'published graph'},
        'workspace_id': p.workspace_id, 'business_name': business['name'], 'flow': business['flow']}
    calls = []

    def managed(actual_db, actor, actual_task):
        assert (actual_db, actor, actual_task) == (db, p, task)
        return row

    def starter(actual_db, actor, actual_task):
        assert (actual_db, actor, actual_task) == (db, p, task)
        calls.append('original approval gate')

    def graph_for(actual_release, actual_business):
        assert (actual_release, actual_business) == (release, business)
        calls.append('frozen release graph')
        return value['graph']

    monkeypatch.setattr(approval, 'managed', managed)
    monkeypatch.setattr(approval, 'approval_starter', starter)
    monkeypatch.setattr(graphs, 'graph_for', graph_for)
    return NS(db=db, p=p, task=task, row=row, business=business, release=release,
        value=value, calls=calls)


def check(s, *, definition=True):
    return subject._definition_graph(s.db, s.p, s.task, s.release, s.business,
        s.value, definition=definition)


def test_real_initializing_scope_admits_definition_without_installing_a_graph(seam):
    s = seam; before = copy.deepcopy(s.task.data)
    with approval.initializing(s.db, s.row, 'independent-approved-command-hash'):
        assert check(s) is None
        # Goal definition_spec and definition_pin both run before graph init.
        s.task.data['goal_spec'] = {'already_initialized_goal': True}
        assert check(s) is None
    assert KEY not in s.db.info and 'graph_runtime' not in s.task.data
    assert s.task.data == {**before, 'goal_spec': {'already_initialized_goal': True}}
    assert s.calls == ['original approval gate', 'frozen release graph'] * 2


def test_definition_boolean_and_saved_task_marker_are_not_initializing_authority(seam):
    s = seam
    s.task.data[KEY] = {'managed_id': s.row.id, 'state_hash': digest(approval.authority(s.row))}
    with pytest.raises(HTTPException): check(s)
    assert s.calls == []


@pytest.mark.parametrize('invalid', [
    'no_transaction', 'wrong_managed_id', 'wrong_state_hash', 'extra_transition_key',
    'wrong_workspace', 'wrong_task', 'wrong_status', 'wrong_actor',
    'initialized', 'saved_goal_context', 'wrong_task_status', 'runtime_read',
])
def test_uncommitted_scope_is_restricted_to_original_start(seam, invalid):
    s = seam
    with approval.initializing(s.db, s.row, 'independent-approved-command-hash'):
        if invalid == 'no_transaction': s.db.transaction = False
        elif invalid == 'wrong_managed_id': s.db.info[KEY]['managed_id'] = 'another-managed'
        elif invalid == 'wrong_state_hash': s.db.info[KEY]['state_hash'] = 'untrusted'
        elif invalid == 'extra_transition_key': s.db.info[KEY]['client_override'] = True
        elif invalid == 'wrong_workspace': s.row.workspace_id = 'foreign-workspace'
        elif invalid == 'wrong_task': s.row.task_id = 'foreign-task'
        elif invalid == 'wrong_status': s.row.status = 'approved'
        elif invalid == 'saved_goal_context': s.task.data['native_goal_context'] = {'saved': True}
        elif invalid == 'wrong_task_status': s.task.status = 'approval_approved'
        elif invalid in {'wrong_actor', 'initialized'}:
            data = approval.checked(s.row)
            if invalid == 'wrong_actor': data['execution']['actor_id'] = 'other-actor'
            else: data['execution']['initialized'] = True
            approval.seal(s.row, data)
        with pytest.raises(HTTPException):
            check(s, definition=invalid != 'runtime_read')
    assert 'frozen release graph' not in s.calls
    assert KEY not in s.db.info


def test_real_approval_gate_failure_cannot_be_replaced_by_context_hash(seam, monkeypatch):
    s = seam

    def rejected(*args):
        raise HTTPException(403, 'original actor or independent approval chain rejected')

    monkeypatch.setattr(approval, 'approval_starter', rejected)
    with approval.initializing(s.db, s.row, 'independent-approved-command-hash'):
        with pytest.raises(HTTPException) as error: check(s)
    assert error.value.status_code == 403 and s.calls == [] and KEY not in s.db.info


def test_definition_requires_actual_frozen_release_graph(seam, monkeypatch):
    s = seam
    monkeypatch.setattr(graphs, 'graph_for', lambda *a: {'another': 'published graph'})
    with approval.initializing(s.db, s.row, 'independent-approved-command-hash'):
        with pytest.raises(HTTPException): check(s)


@pytest.mark.parametrize('runtime', [None, {}, {'graph_hash': 'another graph'}])
def test_existing_broken_graph_never_falls_back_to_initializing_scope(seam, runtime):
    s = seam; s.task.data['graph_runtime'] = runtime
    with approval.initializing(s.db, s.row, 'independent-approved-command-hash'):
        with pytest.raises(HTTPException): check(s)
    assert s.calls == []


@pytest.mark.parametrize('definition', [False, True])
def test_existing_actual_graph_hash_remains_required_without_start_context(seam, definition):
    s = seam; s.task.data['graph_runtime'] = {'graph_hash': digest(s.value['graph'])}
    assert check(s, definition=definition) is None and s.calls == []
    s.task.data['graph_runtime']['graph_hash'] = 'wrong hash'
    with pytest.raises(HTTPException): check(s, definition=definition)


def test_real_binding_checks_original_published_scope_before_pregraph_seam(seam, monkeypatch):
    s = seam
    s.task.data.update(workflow_runtime={'original': True}, instance_id=s.release.instance_id,
        instance_release_id=s.release.id, business_id=s.business['id'])
    monkeypatch.setattr(subject, '_fresh', lambda *a, **k: s.release)
    monkeypatch.setattr(graphs, 'business_binding', lambda *a: copy.deepcopy(s.value))
    monkeypatch.setattr(subject.queries, '_reader', lambda db, p: None)
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *a: (s.task, s.release, s.business))
    monkeypatch.setattr(family, 'check_binding', lambda value: value)
    with approval.initializing(s.db, s.row, 'independent-approved-command-hash'):
        result = subject.binding(s.db, s.p, s.task, definition=True)
        assert result['task'] is s.task and result['binding'] == s.value
        s.calls.clear()
        s.value['workspace_id'] = 'foreign-workspace'
        with pytest.raises(HTTPException): subject.binding(s.db, s.p, s.task, definition=True)
        assert s.calls == []
