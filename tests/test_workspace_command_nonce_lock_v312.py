"""Actual nonce routing and writer ordering over explicit SQL/IAM IO spies.

Not a real advisory lock or permission/source/product proof. The companion
isolated PG suite owns real two-connection transaction serialization.
"""
import copy
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import workspace_command_nonce_lock_v312 as n
from generic_agents import same_goal_advice_store_v302 as advice
from generic_agents import same_goal_advice_preview_v307 as previews
from generic_agents import same_goal_advice_execution_v308 as execution
from generic_agents import same_goal_advice_review_v308 as reviews
from generic_agents import same_goal_advice_options_v310 as options
from generic_agents import same_goal_advice_commands_v310 as commands
from generic_agents import same_goal_continuation_store_v312 as continuation
from generic_agents.contracts import digest
from generic_agents.models import Audit, Plan
from generic_agents.security import Principal
from tests.test_same_goal_continuation_store_v312 import predicate


def uid(number): return str(UUID(int=number))


@pytest.fixture
def scene():
    p = Principal(uid(1), uid(2), 'admin', 'explicit-nonce-IO')
    v = NS(p=p, task_id=uid(3), advice_id=uid(4), subject_id=uid(5),
        body={'request_id': uid(6), 'reason': '保留原请求😀\n逐字核对'}, rows={Audit: [], Plan: []}, calls=[])
    class DB:
        @property
        @contextmanager
        def no_autoflush(self): yield
        def execute(self, statement, parameters):
            assert str(statement) == n.LOCK_SQL
            assert parameters == {'workspace_command_nonce_key': n.key(p.workspace_id, v.body['request_id'])}
            v.calls.append(('advisory-transaction-lock-IO', copy.deepcopy(parameters)))
        def scalars(self, statement):
            assert v.calls and v.calls[0][0] == 'advisory-transaction-lock-IO'
            model = statement.column_descriptions[0]['entity'];assert model in v.rows
            assert statement._limit_clause.value == n.MAX_ROWS + 1
            assert p.workspace_id in statement.compile().params.values()
            v.calls.append(('actual-route-SQL', model))
            return [row for row in v.rows[model] if predicate(statement.whereclause, row)]
        def add(self, row): pytest.fail('the nonce guard creates no records')
        def flush(self): pytest.fail('the nonce guard does not flush')
        def commit(self): pytest.fail('the caller owns the transaction')
    v.db = DB()
    v.acquire = lambda **extra: n.acquire(v.db, p, action=extra.pop('action', 'same_goal_continuation.saved_v312'),
        task_id=extra.pop('task_id', v.task_id), body=extra.pop('body', v.body), **extra)
    def original(action='same_goal_continuation.saved_v312', *, spelling='request'):
        data = {'task_id': v.task_id, 'advice_plan_id': v.advice_id, 'request_id': v.body['request_id']}
        if spelling in {'request', 'body'}: data[spelling] = copy.deepcopy(v.body)
        else: data['body_hash'] = digest(v.body)
        event = Audit(id=uid(7 + len(v.rows[Audit])), workspace_id=p.workspace_id, actor_id=p.user_id,
            action=action, object_id=v.subject_id, data=data)
        v.rows[Audit].append(event);return event
    v.original = original
    return v


def test_key_is_workspace_nonce_only_fixed_signed_64_and_has_no_task_or_operation_partition(scene):
    v = scene;k = n.key(v.p.workspace_id, v.body['request_id'])
    assert -(2 ** 63) <= k < 2 ** 63 and n.NAMESPACE == 'workspace-advice-continuation-command-nonce-v312'
    assert k == n.key(v.p.workspace_id, v.body['request_id'])
    assert k != n.key(uid(999), v.body['request_id']) and k != n.key(v.p.workspace_id, uid(998))
    from generic_agents.formal_continuation_contracts_v345 import ACTIONS as formal_actions
    assert len(n.ACTIONS) == 24 and set(formal_actions.values()) <= n.ACTIONS
    assert 'workflow.formal_continuation.abandoned_v345' in n.ACTIONS


def test_first_command_locks_before_scoped_positive_inventory_and_writes_nothing(scene):
    v = scene;assert v.acquire() is None
    assert [row[0] for row in v.calls] == ['advisory-transaction-lock-IO', 'actual-route-SQL', 'actual-route-SQL']
    assert v.rows == {Audit: [], Plan: []}


@pytest.mark.parametrize('spelling', ['request', 'body', 'body_hash_only'])
def test_matching_original_body_is_only_routed_to_original_family_without_rewriting_old_hashes(scene, spelling):
    v = scene;event = v.original(spelling=spelling);before = copy.deepcopy(event.data)
    assert v.acquire(subject_id=v.subject_id, advice_id=v.advice_id) is None
    assert event.data == before


def test_old_preview_wrapped_request_hash_is_not_replaced_by_a_new_hash_format(scene):
    v = scene;event = v.original('same_goal_advice.preview_prepared_v307')
    event.data['request_hash'] = digest({'task_id': v.task_id, 'plan_id': v.subject_id, 'body': v.body})
    before = copy.deepcopy(event.data);v.acquire(action=event.action, subject_id=v.subject_id)
    assert event.data == before and event.data['request_hash'] != digest(v.body)


@pytest.mark.parametrize('fault', ['action', 'task', 'actor', 'subject', 'advice', 'body', 'duplicate', 'pointer_task'])
def test_foreign_or_conflicting_original_cannot_be_reused_after_the_same_lock(scene, fault):
    v = scene;event = v.original();kwargs = {'subject_id': v.subject_id, 'advice_id': v.advice_id}
    if fault == 'action': kwargs['action'] = 'same_goal_advice.preparation_accepted_v302'
    elif fault == 'task': kwargs['task_id'] = uid(800)
    elif fault == 'actor': event.actor_id = uid(801)
    elif fault == 'subject': kwargs['subject_id'] = uid(802)
    elif fault == 'advice': kwargs['advice_id'] = uid(803)
    elif fault == 'body': kwargs['body'] = {**v.body, 'reason': '同号改正文'}
    elif fault == 'duplicate': v.original()
    else: v.rows[Plan].append(Plan(id=v.subject_id, workspace_id=v.p.workspace_id, task_id=uid(804),
        data={'operations': [{'request_id': v.body['request_id']}]}))
    with pytest.raises(HTTPException) as caught: v.acquire(**kwargs)
    assert caught.value.status_code == 409 and v.calls[0][0] == 'advisory-transaction-lock-IO'


@pytest.mark.parametrize('field', ['operations', 'revisions'])
def test_orphan_body_pointer_never_becomes_an_unused_nonce(scene, field):
    v = scene;v.rows[Plan].append(Plan(id=v.subject_id, workspace_id=v.p.workspace_id, task_id=v.task_id,
        data={field: [{'request_id': v.body['request_id']}]}))
    with pytest.raises(HTTPException, match='保护回执缺失'): v.acquire()


def test_foreign_workspace_same_nonce_is_filtered_and_never_shared_authority(scene):
    v = scene;event = v.original();event.workspace_id = uid(900)
    v.rows[Plan].append(Plan(id=v.subject_id, workspace_id=uid(900), task_id=v.task_id,
        data={'operations': [{'request_id': v.body['request_id']}]}))
    assert v.acquire() is None


class StopBeforeTask(Exception): pass


def bodies():
    base = {'request_id': uid(60), 'reason': '完整原命令'}
    goal = {'expected_goal_hash': 'a' * 64, 'expected_mature_head_hash': 'b' * 64}
    version = {'expected_plan_version': 1}
    source = {'kind': 'approved_source_set', 'source_set_ref': {'source_set_id': uid(61), 'source_set_hash': 'c' * 64}}
    cont = {**base, 'expected_task_version': 1, 'source_hash': 'd' * 64, 'draft': {'purpose': '原人工草案'}, 'preview_hash': 'e' * 64}
    return [
        ('prepare', lambda db,p,t,a,c,b: advice.prepare(db,p,t,b), {**base, **goal, 'document_source': source},
            advice.ACCEPTED, None, None),
        ('cancel', lambda db,p,t,a,c,b: advice.cancel(db,p,t,a,b), {**base, **version}, advice.CANCELLED, uid(51), None),
        ('preview', lambda db,p,t,a,c,b: previews.prepare(db,p,t,a,b), {**base, **goal, **version}, previews.ACTION, uid(51), None),
        ('start', lambda db,p,t,a,c,b: execution.start(db,p,t,a,b),
            {**base, **goal, **version, 'preview_hash': 'c' * 64, 'cloud_authorized': True}, execution.STARTED, uid(51), None),
        ('review', lambda db,p,t,a,c,b: reviews.review(db,p,t,a,b),
            {**base, **version, 'result_hash': 'c' * 64, 'decision': 'accepted'}, reviews.ACTION, uid(51), None),
        ('quota', lambda db,p,t,a,c,b: options.configure_quota(db,p,t,b),
            {**base, **goal, 'max_cost_minor_units': 100, 'currency': 'CNY', 'validity_seconds': 7200}, options.QUOTA_ACTION, uid(50), None),
        ('price', lambda db,p,t,a,c,b: options.price(db,p,t,b),
            {**base, 'model_pin': {'id': uid(62), 'content_hash': 'd' * 64, 'release_version': '1.0.0'}, 'validity_seconds': 3600}, options.PRICE_ACTION, uid(50), None),
        ('create', lambda db,p,t,a,c,b: continuation.create(db,p,t,a,b), cont, continuation.c.ACTION_SAVE, None, uid(51)),
        ('revise', lambda db,p,t,a,c,b: continuation.revise(db,p,t,a,c,b),
            {**cont, 'expected_continuation_version': 1}, continuation.c.ACTION_REVISE, uid(52), uid(51)),
        ('withdraw', lambda db,p,t,a,c,b: continuation.withdraw(db,p,t,a,c,b),
            {**base, 'expected_continuation_version': 1}, continuation.c.ACTION_WITHDRAW, uid(52), uid(51))]


@pytest.mark.parametrize('name,call,body,action,subject,advice_id', bodies())
def test_each_real_writer_reaches_shared_nonce_before_any_task_or_actor_lock(monkeypatch, name, call, body, action, subject, advice_id):
    p = Principal(uid(1), uid(2), 'admin', 'explicit-entry-order-IAM-IO');events = []
    db = NS(no_autoflush=nullcontext())
    monkeypatch.setattr(advice, '_reader', lambda *args, **kwargs: events.append('current-reader-IO'))
    monkeypatch.setattr(continuation, '_reader', lambda *args, **kwargs: events.append('current-reader-IO'))
    def forbidden(*args, **kwargs): pytest.fail('Task/actor/nonce lookup preceded shared workspace nonce lock')
    monkeypatch.setattr(advice.existing, '_task', forbidden)
    monkeypatch.setattr(advice, 'lock_current_actor', forbidden)
    monkeypatch.setattr(continuation, 'lock_current_actor', forbidden)
    monkeypatch.setattr(continuation, '_nonce', forbidden)
    def locked(actual_db, principal, **value):
        assert actual_db is db and principal is p and value['action'] == action and value['task_id'] == uid(50)
        assert value.get('subject_id') == subject and value.get('advice_id') == advice_id
        assert value['body']['request_id'] == body['request_id'] and value['body']['reason'] == body['reason']
        events.append('shared-nonce');raise StopBeforeTask()
    monkeypatch.setattr(n, 'acquire', locked)
    with pytest.raises(StopBeforeTask): call(db,p,uid(50),uid(51),uid(52),body)
    assert events[-1] == 'shared-nonce'


@pytest.mark.parametrize('reader', ['advice_get', 'advice_list', 'advice_original', 'preview_get', 'command_get',
    'continuation_get', 'continuation_list', 'continuation_original', 'continuation_preview'])
def test_readers_and_pure_continuation_preview_never_take_command_nonce_lock(monkeypatch, reader):
    p = Principal(uid(1), uid(2), 'admin', 'explicit-read-IAM-IO');db = NS(no_autoflush=nullcontext())
    monkeypatch.setattr(advice, '_reader', lambda *args, **kwargs: None)
    monkeypatch.setattr(continuation, '_reader', lambda *args, **kwargs: None)
    def forbidden(*args, **kwargs): pytest.fail('a GET/pure preview acquired a writer nonce lock')
    def reached_read(*args, **kwargs): raise StopBeforeTask()
    monkeypatch.setattr(n, 'acquire', forbidden)
    monkeypatch.setattr(advice.existing, '_task', reached_read)
    monkeypatch.setattr(continuation.sources, 'load', reached_read)
    calls = {'advice_get': lambda: advice.get(db,p,uid(50),uid(51)), 'advice_list': lambda: advice.listing(db,p,uid(50)),
        'advice_original': lambda: advice.get_operation(db,p,uid(50),uid(60)), 'preview_get': lambda: previews.get(db,p,uid(50),uid(51)),
        'command_get': lambda: commands.read(db,p,uid(50),uid(60)), 'continuation_get': lambda: continuation.get(db,p,uid(50),uid(51),uid(52)),
        'continuation_list': lambda: continuation.listing(db,p,uid(50),uid(51)),
        'continuation_original': lambda: continuation.get_operation(db,p,uid(50),uid(51),uid(60)),
        'continuation_preview': lambda: continuation.preview(db,p,uid(50),uid(51),
            {'request_id': uid(60), 'expected_task_version': 1, 'source_hash': 'a' * 64, 'draft': {'purpose': '只读预览'}, 'reason': '未写入'})}
    with pytest.raises(StopBeforeTask): calls[reader]()


@pytest.mark.parametrize('name', ['quota', 'price'])
def test_admin_options_reject_role_before_global_nonce_lookup_or_lock(monkeypatch, name):
    row = next(value for value in bodies() if value[0] == name);_, call, body, _, _, _ = row
    p = Principal(uid(1), uid(2), 'operator', 'explicit-role-order-only')
    def forbidden(*args, **kwargs): pytest.fail('non-admin request reached global nonce inventory')
    monkeypatch.setattr(n, 'acquire', forbidden)
    with pytest.raises(HTTPException) as caught: call(NS(),p,uid(50),uid(51),uid(52),body)
    assert caught.value.status_code == 403
