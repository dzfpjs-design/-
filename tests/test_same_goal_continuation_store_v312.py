"""Actual store/SQL predicates over explicit source, account and persistence IO.

Not PostgreSQL, real authentication, released-capability/source authority,
paid-result durability or browser workflow proof. No worker/model is mocked
as a success: this family deliberately creates neither jobs nor invocations.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList, Grouping

from generic_agents import same_goal_continuation_store_v312 as s
from generic_agents.contracts import digest
from generic_agents.models import Task


def uid(number): return str(UUID(int=number))


def contains(left, right):
    if isinstance(right, dict):
        return isinstance(left, dict) and all(key in left and contains(left[key], value) for key, value in right.items())
    if isinstance(right, list):
        return isinstance(left, list) and all(any(contains(item, value) for item in left) for value in right)
    return type(left) is type(right) and left == right


def predicate(expression, row):
    """Interpret actual scoped and JSONB-containment SQL; no permissive fallback."""
    if isinstance(expression, BindParameter): return expression.value
    if isinstance(expression, Grouping): return predicate(expression.element, row)
    if isinstance(expression, BooleanClauseList):
        values = [predicate(item, row) for item in expression.clauses]
        if expression.operator is operators.and_: return all(values)
        if expression.operator is operators.or_: return any(values)
        raise AssertionError('Unsupported boolean SQL')
    if isinstance(expression, BinaryExpression):
        left, right = predicate(expression.left, row), predicate(expression.right, row);op = expression.operator
        if op in (operators.json_getitem_op, operators.json_path_getitem_op): return left.get(right) if isinstance(left, dict) else None
        if op is operators.eq: return left is not None and right is not None and type(left) is type(right) and left == right
        if op is operators.in_op: return left is not None and left in right
        if op is operators.is_: return left is right
        if getattr(op, 'opstring', None) == '@>': return contains(left, right)
        raise AssertionError('Unsupported SQL operator ' + str(op))
    name = getattr(expression, '__visit_name__', None)
    if name == 'null': return None
    if name == 'true': return True
    if name == 'false': return False
    if hasattr(expression, 'table') and getattr(expression, 'key', None): return getattr(row, expression.key)
    raise AssertionError('Unsupported SQL expression ' + type(expression).__name__)


@pytest.fixture
def scene(monkeypatch):
    at = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    p = NS(user_id=uid(1), workspace_id=uid(10), role='admin', session_id='explicit-IAM-IO')
    business, assigned, other = uid(2), uid(3), uid(4)
    task = Task(id=uid(20), workspace_id=p.workspace_id, owner_id=business, package_id=uid(21),
        version=4, status='workflow_waiting', data={'goal_hash': 'a' * 64,
            'goal_spec': {'retained_original_goal_IO': True}, 'native_goal_query_id': uid(22),
            'graph_runtime': {'original_frozen_IO': True}})
    advice = s.Plan(id=uid(30), workspace_id=p.workspace_id, task_id=task.id, status='reviewed', version=9,
        approved_by=None, approved_hash=None, created_at=at - timedelta(hours=1),
        data={'schema': 'same-goal-advice-plan-v302', 'retained_paid_original_IO': True, 'operations': []})
    source = {'schema': s.c.SOURCE, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'advice_plan_id': advice.id, 'origin_kind': 'independent_advice', 'bridge_state': 'not_bound',
        'goal_hash': task.data['goal_hash'], 'goal_version': 1, 'goal_snapshot_hash': 'b' * 64,
        'release_id': uid(31), 'original_graph_hash': 'c' * 64, 'review_decision': 'accepted',
        'result_ref': {'id': uid(32), 'record_hash': 'd' * 64}, 'review_ref': {'id': uid(33), 'record_hash': 'e' * 64},
        'start_ref': {'id': uid(34), 'record_hash': 'f' * 64}, 'preview_ref': {'id': uid(35), 'record_hash': 'a' * 64},
        'preparation_snapshot_hash': 'b' * 64, 'model_pin': {'id': uid(36), 'content_hash': 'c' * 64, 'release_version': '1.0.0'},
        'decision_intent': 'information_acquisition', 'data_as_of': at.isoformat()}
    cap = NS(id=uid(40), kind='tool', release_version='1.0.0', data={'fixed_published_capability_IO': True})
    cap.content_hash = cap.validated_hash = digest(cap.data)
    pin = {'id': cap.id, 'content_hash': cap.content_hash, 'release_version': cap.release_version}
    context = {'schema': s.c.CONTEXT, 'task_id': task.id, 'advice_plan_id': advice.id, 'task_version': task.version,
        'source_hash': digest(source), 'source': copy.deepcopy(source), 'allowed_routes': ['information_acquisition'],
        'candidates': [{'candidate_ref': 'candidate_a', 'proposal': '核对原建议', 'model_review': None}],
        'tools': [{**pin, 'name': '固定本地能力', 'kind': 'tool', 'planning_available': True, 'blocked_reason': None}],
        'assignees': [{'id': assigned, 'name': '实际负责人IO', 'role': 'operator'}, {'id': other, 'name': '另一成员IO', 'role': 'operator'}],
        'permissions': {'can_preview': True, 'can_create': True}, 'blocked_reasons': [], **s.c.FLAGS}
    v = NS(p=p, task=task, advice=advice, source=source, context=context, cap=cap, pin=pin,
        business=business, assigned=assigned, other=other, clock=at, source_calls=[], actor_locks=[],
        task_locks=[], nonce_calls=[], flushes=0, clock_reads=0, live=True, fail_flush=False, counter=100,
        rows={s.Plan: [advice], s.Audit: []},
        members={uid(1): {'role': 'admin', 'active': True}, business: {'role': 'operator', 'active': True},
            assigned: {'role': 'operator', 'active': True}, other: {'role': 'operator', 'active': True}})

    class DB:
        @property
        @contextmanager
        def no_autoflush(self): yield
        def scalar(self, statement):
            if 'clock_timestamp' in str(statement): v.clock_reads += 1;return v.clock
            model = statement.column_descriptions[0]['entity']
            if model is Task:
                v.task_locks.append(statement._for_update_arg is not None)
                return task if predicate(statement.whereclause, task) else None
            assert model is s.User
            # Explicit joined User/Membership IO rows; the production WHERE
            # predicates and actual FOR SHARE selections are still interpreted.
            for identity, member in v.members.items():
                joined = NS(id=identity, user_id=identity, workspace_id=p.workspace_id, **member)
                if predicate(statement.whereclause, joined): return identity
            return None
        def scalars(self, statement):
            model = statement.column_descriptions[0]['entity'];assert model in v.rows
            assert statement._limit_clause.value == s.MAX_ROWS + 1
            assert p.workspace_id in statement.compile().params.values()
            return [row for row in v.rows[model] if predicate(statement.whereclause, row)]
        def add(self, row):
            assert type(row) in v.rows, 'no Job/Invocation/CloudCall/Task may be created';v.rows[type(row)].append(row)
        def flush(self):
            v.flushes += 1
            if v.fail_flush: raise HTTPException(503, 'explicit transaction persistence IO failed')
        def commit(self): pytest.fail('the actual API owns transaction commit')
        @contextmanager
        def begin_nested(self):
            rows = {model: list(values) for model, values in v.rows.items()}
            saved = [(row, row.status, row.version, copy.deepcopy(row.data)) for row in v.rows[s.Plan]]
            try: yield
            except BaseException:
                v.rows = rows
                for row, status, version, data in saved: row.status, row.version, row.data = status, version, data
                raise
    v.db = DB()
    def nonce_io(db, principal, *, action, task_id, body, subject_id=None, advice_id=None):
        # Explicit PG advisory-lock + cross-family positive-original IO only.
        # The real family _nonce/_saved/reverse inventory remains exercised;
        # this fixture never claims to prove the new utility's SQL or lock.
        assert db is v.db and principal is p and task_id == task.id
        assert str(UUID(advice_id)) == advice_id
        assert action in set(s._ACTION.values()) and type(body) is dict and 'request_id' in body
        assert subject_id is None if action == s._ACTION['create'] else isinstance(subject_id, str)
        v.nonce_calls.append({'action': action, 'task_id': task_id, 'body': copy.deepcopy(body),
            'subject_id': subject_id, 'advice_id': advice_id,
            'task_locks_before': len(v.task_locks), 'actor_locks_before': len(v.actor_locks)})
    monkeypatch.setattr(s.command_nonce, 'acquire', nonce_io)

    def lock_actor(db, principal):
        assert db is v.db and principal is p
        member = v.members.get(p.user_id)
        if not member or not member['active'] or member['role'] != p.role: raise HTTPException(403, 'explicit current IAM IO denied')
        v.actor_locks.append(p.user_id)
    def source_io(db, principal, task_id, advice_id, *, write=False, lock=False):
        assert db is v.db and principal is p and task_id == task.id and advice_id == advice.id
        v.source_calls.append((write, lock, principal.user_id))
        if not v.live: raise HTTPException(409, '原目标已过期，不能新写办理草案')
        return {'task': task, 'advice_plan': advice, 'prep': {'original_sealed_preparation_IO': True},
            'result': {'actual_paid_cold_result_IO': True}, 'review': {'actual_review_IO': True},
            'source': copy.deepcopy(v.source), 'source_hash': digest(v.source), 'business_actor_id': business,
            'context': copy.deepcopy(v.context)}
    monkeypatch.setattr(s, 'lock_current_actor', lock_actor)
    monkeypatch.setattr(s.sources, 'load', source_io)
    def published_io(db, principal, identity):
        assert db is v.db and principal is p and identity == cap.id;return cap
    monkeypatch.setattr(s, 'published', published_io)
    v.draft = {'purpose': '根据原建议准备补充资料，尚不执行'}
    def request(draft=None):
        v.counter += 1
        return {'request_id': uid(v.counter), 'expected_task_version': task.version,
            'source_hash': digest(v.source), 'draft': copy.deepcopy(v.draft if draft is None else draft), 'reason': '保留人工办理说明'}
    v.request = request
    def body(draft=None):
        value = request(draft);preview = s.preview(v.db, p, task.id, advice.id, value)
        return {**value, 'preview_hash': preview['preview_hash']}
    v.body = body
    v.create = lambda raw=None: s.create(v.db, p, task.id, advice.id, raw if raw is not None else body())
    return v


def test_preview_is_actual_read_only_and_keeps_incomplete_human_draft(scene):
    v = scene;before = copy.deepcopy(v.task.data);value = s.preview(v.db, v.p, v.task.id, v.advice.id, v.request())
    assert value['normalized_draft'] == s.c.draft(v.draft) and value['missing_fields']
    assert value['source_refs'] == v.source and all(value[key] is False for key in s.c.FLAGS)
    assert v.flushes == v.clock_reads == 0 and not v.rows[s.Audit] and v.rows[s.Plan] == [v.advice]
    assert v.task.data == before
    assert v.nonce_calls == []  # Pure preview has no durable command nonce.


def test_create_writes_only_plan_and_protected_audit_and_history_read_has_no_source_calls(scene):
    v = scene;task_before = copy.deepcopy(v.task.data);advice_before = copy.deepcopy(v.advice.data);body = v.body()
    view = v.create(body);plan = v.rows[s.Plan][-1];calls = list(v.source_calls)
    assert view['phase'] == 'draft' and view['revision']['draft']['purpose'] == v.draft['purpose']
    assert view['revision']['reason'] == body['reason'] and view['permissions'] == {'can_revise': True, 'can_withdraw': True}
    assert plan.status == 'draft' and plan.version == 1 and plan.approved_by is None and plan.approved_hash is None
    assert set(plan.data) == s._PLAN_FIELDS and len(v.rows[s.Audit]) == v.flushes == 1
    assert v.nonce_calls == [{'action': s._ACTION['create'], 'task_id': v.task.id,
        'body': s.c.dump(s.c.SaveRequest, body), 'subject_id': None, 'advice_id': v.advice.id,
        'task_locks_before': 0, 'actor_locks_before': 0}]
    assert v.task.data == task_before and v.advice.data == advice_before and v.task.version == 4 and v.advice.version == 9
    assert s.get(v.db, v.p, v.task.id, v.advice.id, plan.id) == view
    assert s.listing(v.db, v.p, v.task.id, v.advice.id)['records'] == [view]
    receipt = s.get_operation(v.db, v.p, v.task.id, v.advice.id, body['request_id'])
    assert receipt['result'] == view and receipt['body_hash'] == digest(s.c.dump(s.c.SaveRequest, body))
    assert v.source_calls == calls and all(view[key] is False for key in s.c.FLAGS)
    assert len(v.nonce_calls) == 1  # listing/get/operation receipt did not reacquire a write lock.


def test_revise_appends_frozen_revision_without_rewriting_original_source_or_user_work(scene):
    v = scene;created = v.create();plan = v.rows[s.Plan][-1];original = copy.deepcopy(plan.data['revisions'][0]);source = copy.deepcopy(plan.data['source'])
    raw = {**v.body({'purpose': '修改后仍是待审批草案', 'work_description': '保留分段人工内容\n第二段'}), 'expected_continuation_version': 1}
    view = s.revise(v.db, v.p, v.task.id, v.advice.id, plan.id, raw)
    assert view['version'] == 2 and plan.data['revisions'][0] == original and plan.data['source'] == source
    assert view['history'][1]['previous_hash'] == original['record_hash'] and len(v.rows[s.Audit]) == 2
    assert view['revision']['draft']['work_description'].endswith('第二段')
    assert s.revise(v.db, v.p, v.task.id, v.advice.id, plan.id, raw) == view
    assert created['revision']['record_hash'] == original['record_hash']


def test_history_exposes_exact_saved_old_body_and_detaches_it_without_rewriting_current_or_source(scene):
    v = scene;old = {'purpose': '  原人工目的\r\n', 'work_description': '逐字保留第一段😀\n第二段',
        'expected_result': '原期望结果', 'due_at': '2000-01-01T18:00:00+08:00'}
    created = v.create(v.body(old));plan = v.rows[s.Plan][-1]
    original = copy.deepcopy(plan.data['revisions'][0]);source = copy.deepcopy(plan.data['source'])
    raw = {**v.body({'purpose': '当前第二版', 'work_description': '这不是第一版正文'}), 'expected_continuation_version': 1}
    s.revise(v.db, v.p, v.task.id, v.advice.id, plan.id, raw)
    view = s.get(v.db, v.p, v.task.id, v.advice.id, plan.id)
    assert view['history'][0]['draft'] == s.c.draft(old) == created['revision']['draft']
    assert view['history'][0]['record_hash'] == original['record_hash'] and view['history'][0]['previous_hash'] is None
    assert view['history'][1]['previous_hash'] == original['record_hash']
    assert view['history'][1]['draft'] == view['revision']['draft'] and view['revision']['draft']['purpose'] == '当前第二版'
    view['history'][0]['draft']['work_description'] = '本机查看或恢复编辑不会改封存原稿'
    assert plan.data['revisions'][0] == original and plan.data['source'] == source
    reread = s.get(v.db, v.p, v.task.id, v.advice.id, plan.id)
    assert reread['history'][0]['draft']['work_description'] == old['work_description']
    assert reread['revision']['draft']['purpose'] == '当前第二版' and len(plan.data['revisions']) == 2


def test_assigned_operator_can_edit_self_but_cannot_reassign_or_create_as_business_writer(scene):
    v = scene;view = v.create(v.body({'purpose': '交给负责人补充草案', 'assignee_id': v.assigned}));plan = v.rows[s.Plan][-1]
    v.p.user_id = v.assigned;v.p.role = 'operator'
    raw = {**v.body({'purpose': '负责人保存自己的工作内容', 'assignee_id': v.assigned}), 'expected_continuation_version': 1}
    assert s.revise(v.db, v.p, v.task.id, v.advice.id, plan.id, raw)['version'] == 2
    changed = {**v.body({'purpose': '尝试改派', 'assignee_id': v.other}), 'expected_continuation_version': 2}
    with pytest.raises(HTTPException, match='不能转派'): s.revise(v.db, v.p, v.task.id, v.advice.id, plan.id, changed)
    with pytest.raises(HTTPException, match='原业务负责人'): v.create(v.body())
    assert view['source_hash'] == plan.data['source_hash'] and plan.version == 2


def test_other_operator_cannot_cover_draft_and_business_actor_cannot_withdraw_admin_origin(scene):
    v = scene;view = v.create();plan = v.rows[s.Plan][-1];v.p.user_id = v.other;v.p.role = 'operator'
    raw = {**v.body(), 'expected_continuation_version': 1}
    with pytest.raises(HTTPException, match='不能修改'): s.revise(v.db, v.p, v.task.id, v.advice.id, plan.id, raw)
    v.p.user_id = v.business
    assert s.get(v.db, v.p, v.task.id, v.advice.id, plan.id)['permissions'] == {'can_revise': True, 'can_withdraw': False}
    with pytest.raises(HTTPException): s.withdraw(v.db, v.p, v.task.id, v.advice.id, plan.id,
        {'request_id': uid(500), 'expected_continuation_version': 1, 'reason': '不能覆盖原作者撤回权限'})
    assert plan.version == view['version']


def test_withdraw_after_goal_expiry_preserves_material_and_original_nonce_receipt(scene):
    v = scene;view = v.create();plan = v.rows[s.Plan][-1];before = copy.deepcopy(plan.data['revisions']);calls = list(v.source_calls)
    v.live = False;raw = {'request_id': uid(501), 'expected_continuation_version': 1, 'reason': '目标结束后保留材料并撤回草案'}
    withdrawn = s.withdraw(v.db, v.p, v.task.id, v.advice.id, plan.id, raw)
    assert withdrawn['phase'] == 'withdrawn' and withdrawn['version'] == 2 and plan.data['revisions'] == before
    assert s.withdraw(v.db, v.p, v.task.id, v.advice.id, plan.id, raw) == withdrawn
    assert s.get_operation(v.db, v.p, v.task.id, v.advice.id, raw['request_id'])['result_status'] == 'available'
    assert v.source_calls == calls and len(v.rows[s.Audit]) == 2 and v.task.version == 4


def test_same_original_create_is_read_before_expired_source_and_never_mints_replacement(scene):
    v = scene;raw = v.body();view = v.create(raw);calls = list(v.source_calls);v.live = False
    assert v.create(raw) == view and v.source_calls == calls
    with pytest.raises(HTTPException): v.create({**raw, 'request_id': uid(502)})
    assert len(v.rows[s.Plan]) == 2 and len(v.rows[s.Audit]) == 1


@pytest.mark.parametrize('fault', ['task_version', 'source_hash', 'preview_hash', 'blank'])
def test_invalid_snapshot_or_blank_content_has_no_local_write(scene, fault):
    v = scene;raw = v.body()
    if fault == 'task_version': raw['expected_task_version'] += 1
    elif fault == 'source_hash': raw['source_hash'] = '0' * 64
    elif fault == 'preview_hash': raw['preview_hash'] = '0' * 64
    else: raw['draft'] = {'purpose': ' \n '}
    with pytest.raises(HTTPException): v.create(raw)
    assert not v.rows[s.Audit] and v.rows[s.Plan] == [v.advice] and not v.flushes


@pytest.mark.parametrize('fault', ['route', 'candidate', 'tool_pin', 'tool_unavailable', 'assignee_inactive'])
def test_draft_choices_require_actual_source_release_and_current_member(scene, fault):
    v = scene;draft = {'purpose': '保留人工内容'}
    if fault == 'route': draft['route'] = 'unapproved_domain_action'
    if fault == 'candidate': draft['candidate_ref'] = 'unrelated_candidate'
    if fault == 'tool_pin': draft['selected_tools'] = [{**v.pin, 'content_hash': '0' * 64}]
    if fault == 'tool_unavailable': draft['selected_tools'] = [v.pin];v.context['tools'][0]['planning_available'] = False
    if fault == 'assignee_inactive': draft['assignee_id'] = v.assigned;v.members[v.assigned]['active'] = False
    with pytest.raises(HTTPException): s.preview(v.db, v.p, v.task.id, v.advice.id, v.request(draft))
    assert not v.rows[s.Audit] and not v.flushes


@pytest.mark.parametrize('fault', ['body', 'actor', 'operation', 'advice_scope'])
def test_nonce_cannot_change_body_actor_operation_or_advice_scope(scene, fault):
    v = scene;raw = v.body();view = v.create(raw)
    if fault == 'body':
        with pytest.raises(HTTPException): v.create({**raw, 'reason': '同号改写正文'})
    elif fault == 'actor':
        v.p.user_id = v.business;v.p.role = 'operator'
        with pytest.raises(HTTPException): v.create(raw)
    elif fault == 'operation':
        with pytest.raises(HTTPException): s.withdraw(v.db, v.p, v.task.id, v.advice.id, view['continuation_id'],
            {'request_id': raw['request_id'], 'expected_continuation_version': 1, 'reason': '同号不能改操作'})
    else:
        with pytest.raises(HTTPException): s.create(v.db, v.p, v.task.id, uid(999), raw)
    assert len(v.rows[s.Audit]) == 1 and len(v.rows[s.Plan]) == 2


@pytest.mark.parametrize('fault', ['orphan_plan', 'orphan_audit', 'wrong_plan_task', 'duplicate_audit', 'revision_drift'])
def test_positive_reverse_inventory_blocks_a_different_nonce_for_orphan_or_corruption(scene, fault):
    v = scene;v.create();plan = v.rows[s.Plan][-1];raw = v.body()
    if fault == 'orphan_plan': v.rows[s.Audit].clear()
    elif fault == 'orphan_audit': v.rows[s.Plan].remove(plan)
    elif fault == 'wrong_plan_task': plan.task_id = uid(998)
    elif fault == 'duplicate_audit':
        event = v.rows[s.Audit][0];v.rows[s.Audit].append(s.Audit(id=uid(997), workspace_id=v.p.workspace_id,
            actor_id=event.actor_id, action=event.action, object_id=event.object_id, created_at=event.created_at, data=copy.deepcopy(event.data)))
    else: plan.data = {**plan.data, 'revisions': [{**plan.data['revisions'][0], 'reason': '损坏的正文'}]}
    calls = list(v.source_calls)
    with pytest.raises(HTTPException): v.create(raw)
    assert v.source_calls == calls


def test_saved_command_body_survives_missing_plan_as_needs_check_not_unapplied(scene):
    v = scene;raw = v.body();view = v.create(raw);v.rows[s.Plan].pop()
    receipt = s.get_operation(v.db, v.p, v.task.id, v.advice.id, raw['request_id'])
    assert receipt['found'] is True and receipt['result_status'] == 'needs_check' and receipt['result'] is None
    assert receipt['continuation_id'] == view['continuation_id'] and receipt['body_hash'] == digest(s.c.dump(s.c.SaveRequest, raw))
    with pytest.raises(HTTPException): v.create(raw)
    assert len(v.rows[s.Audit]) == 1


def test_nonce_reader_does_not_call_missing_protected_audit_absent_when_plan_pointer_remains(scene):
    v = scene;raw = v.body();v.create(raw);v.rows[s.Audit].clear()
    with pytest.raises(HTTPException) as caught: s.get_operation(v.db, v.p, v.task.id, v.advice.id, raw['request_id'])
    assert caught.value.status_code == 409


@pytest.mark.parametrize('status', [401, 403, 500, 503])
def test_command_reader_does_not_swallow_auth_or_internal_failures_as_needs_check(scene, monkeypatch, status):
    v = scene;raw = v.body();v.create(raw)
    def fail(*args, **kwargs): raise HTTPException(status, 'explicit unavailable result IO')
    monkeypatch.setattr(s, '_inventory', fail)
    with pytest.raises(HTTPException) as caught: s.get_operation(v.db, v.p, v.task.id, v.advice.id, raw['request_id'])
    assert caught.value.status_code == status


@pytest.mark.parametrize('kind', ['audit', 'plan_pointer'])
def test_existing_paid_advice_nonce_cannot_be_reused_even_when_one_original_is_missing(scene, kind):
    v = scene;raw = v.body()
    if kind == 'audit': v.rows[s.Audit].append(s.Audit(id=uid(800), workspace_id=v.p.workspace_id, actor_id=v.p.user_id,
        action='same_goal_advice.started_v308', object_id=v.advice.id, created_at=v.clock,
        data={'request': {'request_id': raw['request_id']}}))
    else: v.advice.data = {**v.advice.data, 'operations': [{'request_id': raw['request_id'], 'original_IO': True}]}
    with pytest.raises(HTTPException): v.create(raw)
    assert len(v.rows[s.Plan]) == 1 and not v.flushes


def test_foreign_workspace_positive_orphan_does_not_poison_original_task(scene):
    v = scene;v.rows[s.Audit].append(s.Audit(id=uid(801), workspace_id=uid(9999), actor_id=v.p.user_id,
        action=s.c.ACTION_SAVE, object_id=uid(802), created_at=v.clock,
        data={'task_id': v.task.id, 'request_id': uid(803), 'malformed_foreign_IO': True}))
    assert v.create()['phase'] == 'draft'


def test_revision_cannot_replace_frozen_source_after_review_or_release_drift(scene):
    v = scene;view = v.create();plan = v.rows[s.Plan][-1];before = copy.deepcopy(plan.data)
    v.source['review_ref']['record_hash'] = '0' * 64;v.context['source'] = copy.deepcopy(v.source);v.context['source_hash'] = digest(v.source)
    raw = {**v.body(), 'expected_continuation_version': 1}
    with pytest.raises(HTTPException, match='旧草案来源不改写'): s.revise(v.db, v.p, v.task.id, v.advice.id, plan.id, raw)
    assert plan.data == before and plan.version == view['version']


def test_rejected_advice_cannot_create_a_continuation_draft(scene):
    v = scene;raw = v.body();v.source['review_decision'] = 'rejected';v.context['source'] = copy.deepcopy(v.source);v.context['source_hash'] = digest(v.source)
    raw['source_hash'] = digest(v.source)
    with pytest.raises(HTTPException, match='审核'): v.create(raw)
    assert not v.rows[s.Audit]


def test_actual_business_operator_can_create_and_revise_without_admin_or_fake_data_principal(scene):
    v = scene;v.p.user_id = v.business;v.p.role = 'operator';created = v.create();plan = v.rows[s.Plan][-1]
    assert created['permissions'] == {'can_revise': True, 'can_withdraw': True}
    raw = {**v.body({'purpose': '业务负责人保存修订', 'candidate_ref': 'candidate_a', 'selected_tools': [v.pin]}),
        'expected_continuation_version': 1}
    view = s.revise(v.db, v.p, v.task.id, v.advice.id, plan.id, raw)
    assert view['version'] == 2 and view['revision']['actor_id'] == v.business and v.p.role == 'operator'
    assert all(actor == v.business for _, _, actor in v.source_calls)


def test_structurally_damaged_plan_result_keeps_protected_command_receipt_readable(scene):
    v = scene;raw = v.body();v.create(raw);plan = v.rows[s.Plan][-1]
    damaged = copy.deepcopy(plan.data);del damaged['source']['advice_plan_id'];plan.data = damaged
    receipt = s.get_operation(v.db, v.p, v.task.id, v.advice.id, raw['request_id'])
    assert receipt['found'] is True and receipt['result_status'] == 'needs_check' and receipt['result'] is None
    assert receipt['actor_id'] == v.p.user_id and receipt['body_hash'] == digest(s.c.dump(s.c.SaveRequest, raw))


@pytest.mark.parametrize('operation', ['create', 'revise'])
def test_failed_persistence_rolls_back_only_this_draft_slice_without_a_success_receipt(scene, operation):
    v = scene
    if operation == 'revise': v.create()
    before = [(row, copy.deepcopy(row.data), row.status, row.version) for row in v.rows[s.Plan]]
    audit_ids = [event.id for event in v.rows[s.Audit]];task_before = copy.deepcopy(v.task.data)
    raw = v.body({'purpose': '这份人工新稿尚未持久化成功'});v.fail_flush = True
    with pytest.raises(HTTPException) as caught:
        if operation == 'create': v.create(raw)
        else: s.revise(v.db, v.p, v.task.id, v.advice.id, v.rows[s.Plan][-1].id,
            {**raw, 'expected_continuation_version': 1})
    assert caught.value.status_code == 503 and [event.id for event in v.rows[s.Audit]] == audit_ids
    assert v.rows[s.Plan] == [row for row, _, _, _ in before]
    assert all((row.data, row.status, row.version) == (data, status, version) for row, data, status, version in before)
    assert v.task.data == task_before
