"""Read orchestration with explicit original-custody, IAM and DB IO spies.

No real PG, released business, SDK execution, device, price or authorization is
claimed. Actual source hashing, current metadata/pins, immutable Start selection
checks and false permissions execute. Dedicated original tests use the existing
pure runtime fixture; its protected custody methods remain declared IO spies.
"""
import copy
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_continuation_sources_v312 as s
from generic_agents import same_goal_advice_contracts_v302 as advice
from generic_agents.contracts import digest
from generic_agents.models import Task
from generic_agents.security import Principal
from tests.test_goal_evaluation_append_command_v297 import predicate
from tests.test_same_goal_advice_contracts_v302 import ref
from tests import test_same_goal_advice_runtime_v308 as runtime_tests


def identity(label): return ref('continuation-' + label)['id']


@pytest.fixture
def scene(monkeypatch):
    workspace = identity('workspace'); business = identity('business-person')
    p = Principal(business, workspace, 'operator', 'current-actor-IO')
    users = [s.User(id=business, username='原业务人', active=True),
        s.User(id=identity('assigned'), username='被指派人', active=True),
        s.User(id=identity('admin'), username='管理员', active=True),
        s.User(id=identity('viewer'), username='查看人', active=True),
        s.User(id=identity('disabled'), username='停用账号', active=False)]
    roles = ['operator', 'operator', 'admin', 'viewer', 'operator']
    members = [s.Membership(user_id=user.id, workspace_id=workspace, role=role) for user, role in zip(users, roles)]
    data = {'name': '真实注册工具 IO', 'execution': {'adapter': 'builtin', 'side_effect': False}}
    tool = s.Capability(id=identity('tool'), workspace_id=workspace, kind='tool', status='published',
        release_version='1.0.0', data=data, content_hash=digest(data), validated_hash=digest(data))
    graph = {'nodes': [{'id': 'original-tool', 'kind': 'ability', 'execution': {
        'capability_id': tool.id, 'capability_hash': tool.content_hash, 'release_version': tool.release_version}}]}
    spec = {'purpose': '保持原目标范围'}
    task = Task(id=identity('task'), workspace_id=workspace, owner_id=business, title='原目标任务', version=3,
        status='workflow_active', data={'goal_confirmed_by': business, 'goal_spec': spec, 'goal_hash': digest(spec),
            'goal_version': 1, 'goal_window': {'fixed-original-window': True}, 'graph_runtime': {'graph_hash': digest(graph)}})
    goal = s.snapshot(task)
    plan = s.Plan(id=identity('advice'), workspace_id=workspace, task_id=task.id, status='reviewed')
    prep = {'business_actor_id': business, 'record_hash': 'a' * 64, 'model_pin': {
        'id': identity('model'), 'content_hash': 'b' * 64, 'release_version': '1.0.0'},
        'evaluation_cut': '2026-11-02T12:00:00+00:00', 'scope': {'release_id': identity('release'),
            'goal_hash': goal['hash'], 'goal_version': goal['version'], 'goal_snapshot_hash': digest(goal)}}
    review = {'decision': 'accepted', 'reason': '核对原件后拟定下一步', 'actor_id': business,
        'reviewed_at': '2026-11-02T12:00:00+00:00', 'review_ref': ref('real-independent-review')}
    recommendation = {'candidate_ref': 'candidate_one', 'reason': '原回答中的建议理由', 'supporting_fact_refs': ['fact_one']}
    result = {'model_value': {'recommendation': recommendation,
        'candidate_reviews': [{'candidate_ref': 'candidate_one', 'reason': '原模型审阅意见'}]}}
    preview = NS(id=identity('original-preview'), data={'record_hash': 'c' * 64,
        'frame_snapshot': {'original-frozen-frame': True}})
    start = NS(id=identity('real-start'), data={'original-start': True})
    started = {'calls': [NS(id=identity('original-sdk'))], 'start': start, 'preview': preview,
        'snapshot': {'preview': {'cloud_request': {'actual-original-model-input': True}}}}
    decision = {'decision_intent': 'information_acquisition',
        'candidates': [{'candidate_ref': 'candidate_one', 'proposal': '核对原目标许可范围内的资料'}]}
    coverage = {'sources': [{'review_scope': 'provided_excerpt_only'}]}
    selected = {'release': NS(id=prep['scope']['release_id']), 'business': {'id': 'actual-business'},
        'binding': {'graph': graph}}
    t = NS(p=p, task=task, plan=plan, prep=prep, review=review, result=result, started=started,
        decision=decision, coverage=coverage, selected=selected, tool=tool, users=users, members=members,
        current=True, allowed=True, events=[], rows={s.Plan: [plan], s.Capability: [tool]}, catalogue={})
    class DB:
        no_autoflush = nullcontext()
        def scalars(self, statement):
            kind = statement.column_descriptions[0]['entity']
            assert workspace in statement.compile().params.values()
            return [row for row in t.rows[kind] if predicate(statement.whereclause, row)]
        def execute(self, statement):
            assert workspace in statement.compile().params.values()
            assert ['admin', 'operator'] in statement.compile().params.values()
            return [(user, member) for user, member in zip(users, members)
                if user.active and member.workspace_id == workspace and member.role in {'admin', 'operator'}]
        def add(self, value): raise AssertionError('source reader cannot add records')
        def flush(self): raise AssertionError('source reader cannot flush')
        def commit(self): raise AssertionError('source reader cannot commit')
    t.db = DB()
    def reader(db, actor, *, write=False):
        pairs = [(user, member) for user, member in zip(users, members) if user.id == actor.user_id
            and user.active and member.workspace_id == actor.workspace_id == workspace and member.role == actor.role]
        if not t.allowed or not pairs: raise HTTPException(403, 'actual current workspace/member IO refused')
        if write and actor.role not in {'admin', 'operator'}: raise HTTPException(403, 'read only role')
    monkeypatch.setattr(s.store, '_reader', reader)
    def task_read(db, actor, identifier, *, lock=False):
        if identifier != task.id or actor.workspace_id != task.workspace_id: raise HTTPException(404, 'actual scoped task IO')
        if lock: t.events.append('task-lock-IO')
        return task
    monkeypatch.setattr(s.store.existing, '_task', task_read)
    monkeypatch.setattr(s, 'lock_current_actor', lambda *args: t.events.append('current-actor-lock-IO'))
    monkeypatch.setattr(s.store.provider, 'binding', lambda *args: selected)
    # Original SDK/price/capture/Start/Review authentication is explicitly IO
    # here; below tests exercise the actual _original selection separately.
    monkeypatch.setattr(s, '_original', lambda *args: (prep, result, review, started, decision, coverage))
    def guard(db, actor, actual, *, lock_sources):
        assert actual is task
        t.events.append(('current-Goal-IO', lock_sources))
        if not t.current: raise HTTPException(409, 'original Goal/release no longer current')
    monkeypatch.setattr(s.native_goals, 'guard', guard)
    monkeypatch.setattr(s.native_goal_tools, 'catalog', lambda *args: t.catalogue)
    def current_cap(db, actor, identifier):
        values = [cap for cap in t.rows[s.Capability] if cap.id == identifier and cap.status == 'published']
        if len(values) != 1: raise HTTPException(409, 'actual published capability IO refused')
        return values[0]
    monkeypatch.setattr(s, 'published', current_cap)
    return t


def test_source_uses_real_identity_and_immutable_hash_without_graph_or_execution_claim(scene):
    t = scene; value = s.load(t.db, t.p, t.task.id, t.plan.id)
    assert set(value) == {'task', 'advice_plan', 'prep', 'result', 'review', 'source', 'source_hash', 'business_actor_id', 'context'}
    source = value['source']; context = value['context']
    assert source['origin_kind'] == 'independent_advice' and source['bridge_state'] == 'not_bound'
    assert source['original_graph_hash'] == digest(t.selected['binding']['graph'])
    assert source['result_ref'] == {'id': t.started['calls'][0].id, 'record_hash': digest(t.result)}
    assert context['source_hash'] == digest(source) == value['source_hash']
    assert context['allowed_routes'] == ['information_acquisition']
    assert context['source']['recommendation'] == t.result['model_value']['recommendation']
    assert context['permissions'] == {'can_preview': True, 'can_create': True}
    assert all(source[key] is False and context[key] is False for key in s.c.FLAGS)
    assert not {'activation_id', 'node_id', 'price', 'checked_at', 'aliases', 'frame_snapshot'} & set(source)


@pytest.mark.parametrize('intent', ['information_acquisition', 'operational_action', 'business_conclusion'])
@pytest.mark.parametrize('decision', ['accepted', 'needs_revision', 'rejected'])
def test_original_intent_and_review_are_not_relabelled_as_execution_authority(scene, intent, decision):
    t = scene; t.decision['decision_intent'] = intent; t.review['decision'] = decision
    context = s.context(t.db, t.p, t.task.id, t.plan.id)
    assert context['allowed_routes'] == ([] if decision == 'rejected' else [intent])
    assert context['permissions']['can_create'] is (decision != 'rejected')
    assert context['permissions']['can_preview'] is (decision != 'rejected')
    assert context['execution_approved'] is False
    if decision == 'needs_revision': assert 'revision_required' in {item['code'] for item in context['blocked_reasons']}


def test_assigned_operator_can_preview_current_source_but_cannot_create_and_does_not_impersonate_business(scene):
    t = scene; user = t.users[1]; actor = Principal(user.id, t.p.workspace_id, 'operator', 'current-assigned-IO')
    value = s.load(t.db, actor, t.task.id, t.plan.id, write=True, lock=True)
    assert value['context']['permissions'] == {'can_preview': True, 'can_create': False}
    assert value['business_actor_id'] == t.p.user_id and actor.user_id != value['business_actor_id']
    assert t.events[:2] == ['task-lock-IO', 'current-actor-lock-IO']


@pytest.mark.parametrize('state', ['workflow_completed', 'workflow_cancelled', 'accepted', 'closed', 'goal_accepted'])
def test_completed_graph_is_not_goal_completion_but_terminal_goal_rejects_new_writes_and_preserves_history(scene, state):
    t = scene
    if state == 'goal_accepted': t.task.data = {**t.task.data, 'native_goal_acceptance': {'actual-original-outcome': True}}
    else: t.task.status = state
    history = s.context(t.db, t.p, t.task.id, t.plan.id)
    assert history['permissions']['can_create'] is (state == 'workflow_completed')
    if state == 'workflow_completed': assert s.load(t.db, t.p, t.task.id, t.plan.id, write=True)['context']['permissions']['can_create']
    else:
        with pytest.raises(HTTPException): s.load(t.db, t.p, t.task.id, t.plan.id, write=True)
    assert history['source']['result_ref']['record_hash'] == digest(t.result)


def test_historical_source_hash_does_not_include_current_goal_check_clock_directory_or_task_version(scene):
    t = scene; before = s.load(t.db, t.p, t.task.id, t.plan.id)
    t.current = False; t.task.version += 1; t.tool.status = 'retired'; t.users[1].active = False
    after = s.load(t.db, t.p, t.task.id, t.plan.id)
    assert after['source_hash'] == before['source_hash'] and after['source'] == before['source']
    assert after['context']['permissions'] == {'can_preview': False, 'can_create': False}
    assert after['context']['tools'] == [] and after['context']['task_version'] == 4
    with pytest.raises(HTTPException): s.load(t.db, t.p, t.task.id, t.plan.id, write=True)


def test_tool_union_checks_actual_published_pins_and_analysis_adapter_not_name(scene):
    t = scene; t.catalogue = {t.tool.id: {'capability': t.tool}}
    t.tool.data['name'] = 'business_goal_ready_v288'  # A name never classifies execution.
    t.tool.content_hash = t.tool.validated_hash = digest(t.tool.data)
    t.selected['binding']['graph']['nodes'][0]['execution']['capability_hash'] = t.tool.content_hash
    t.task.data['graph_runtime']['graph_hash'] = digest(t.selected['binding']['graph'])
    tools = s.context(t.db, t.p, t.task.id, t.plan.id)['tools']
    assert len(tools) == 1 and tools[0]['planning_available'] is True
    t.tool.data['execution']['adapter'] = 'business_goal_ready_v288'
    t.tool.content_hash = t.tool.validated_hash = digest(t.tool.data)
    t.selected['binding']['graph']['nodes'][0]['execution']['capability_hash'] = t.tool.content_hash
    t.task.data['graph_runtime']['graph_hash'] = digest(t.selected['binding']['graph'])
    tools = s.context(t.db, t.p, t.task.id, t.plan.id)['tools']
    assert tools[0]['planning_available'] is False and tools[0]['blocked_reason']


def test_graph_pin_drift_or_unpublished_catalogue_never_exposes_alternative_tools(scene):
    t = scene; t.tool.content_hash = 'f' * 64
    value = s.context(t.db, t.p, t.task.id, t.plan.id)
    assert value['tools'] == [] and 'tools_unavailable' in {item['code'] for item in value['blocked_reasons']}


def test_assignee_directory_contains_only_actual_active_workspace_admins_and_operators(scene):
    t = scene; context = s.context(t.db, t.p, t.task.id, t.plan.id)
    assert {row['id'] for row in context['assignees']} == {user.id for user in t.users[:3]}
    assert {row['role'] for row in context['assignees']} <= {'admin', 'operator'}


@pytest.mark.parametrize('fault', ['reader', 'foreign_plan', 'goal_hash', 'graph_hash', 'release'])
def test_scope_or_original_source_damage_never_becomes_an_empty_success(scene, fault):
    t = scene
    if fault == 'reader': t.allowed = False
    elif fault == 'foreign_plan': t.plan.task_id = identity('other-task')
    elif fault == 'goal_hash': t.prep['scope']['goal_hash'] = 'f' * 64
    elif fault == 'graph_hash': t.task.data['graph_runtime']['graph_hash'] = 'f' * 64
    else: t.selected['release'].id = identity('other-release')
    with pytest.raises(HTTPException): s.load(t.db, t.p, t.task.id, t.plan.id)


def test_actual_original_reader_calls_saved_paid_result_review_and_start_and_uses_the_start_preview(monkeypatch):
    t = runtime_tests.scene.__wrapped__(monkeypatch); result = runtime_tests._succeeded(t)
    t.plan.status = 'reviewed'; calls = []
    review = {'result_hash': digest(result), 'decision': 'accepted'}
    def saved(db, p, task, plan): calls.append('saved-original-IO'); return t.prep, t.job
    def paid(db, p, task, plan): calls.append('paid-cold-original-IO'); return result
    def reviewed(*args): calls.append('actual-review-IO'); return [review]
    def start(*args): calls.append('actual-Start-custody-IO'); return t.execution
    monkeypatch.setattr(s.store, '_saved', saved)
    monkeypatch.setattr(s.runtime, 'cold_result', paid)
    monkeypatch.setattr(s.reviews, 'checked', reviewed)
    monkeypatch.setattr(s.execution, 'checked', start)
    value = s._original(t.db, t.p, t.task, t.plan)
    assert value[1] == result and value[3]['preview'] is t.execution['preview']
    assert calls == ['saved-original-IO', 'paid-cold-original-IO', 'actual-review-IO', 'actual-Start-custody-IO']
    # A new candidate/latest preview cannot replace the one actually Start-bound.
    t.execution['preview'] = NS(id=identity('unrelated-latest-preview'), data=t.execution['preview'].data)
    with pytest.raises(HTTPException): s._original(t.db, t.p, t.task, t.plan)
