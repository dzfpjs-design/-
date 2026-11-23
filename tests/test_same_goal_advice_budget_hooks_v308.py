"""Actual reserve/transport/protocol seams with explicit DB and financial IO spies.

These are not PG, vendor HTTP, paid-call or current authority proofs. Reserve's
ordering and V197's actual single POST construction run; IO adapters record the
same-transaction boundary. Protocol cases retain actual raw and local journals.
"""
import copy
from types import SimpleNamespace as NS

import pytest

from generic_agents import cloud_budget as budget
from generic_agents import same_goal_advice_spending_v308 as spending
from generic_agents import same_goal_advice_runtime_v308 as runtime
from generic_agents.contracts import digest
from generic_agents.gateway import ModelBlocked
from generic_agents.models import CloudCall, Job
from tests.test_business_reasoning_v197 import scene as model_scene, ident
from tests.test_cloud_business_reasoning_v197 import install, envelope
from tests import test_same_goal_advice_runtime_v308 as protocol_tests


def reserve_io(monkeypatch, *, deny=None):
    s = model_scene(); events = []; added = []; witness = object()
    daily = Job(workspace_id=s.p.workspace_id, kind='cloud_budget', status='internal',
        data={'day': '2026-11-02', 'tokens': 0, 'calls': 0})
    class DB:
        def __enter__(self): return self
        def __exit__(self, kind, *_):
            if kind is not None: events.append('rollback-on-exit')
            return False
        def execute(self, statement):
            assert 'INSERT INTO jobs' in str(statement)
            events.append('daily-insert')
        def scalar(self, statement):
            assert statement.column_descriptions[0]['entity'] is Job
            assert statement._for_update_arg is not None
            events.append('daily-lock'); return daily
        def add(self, row):
            assert type(row) is CloudCall
            added.append(row); events.append('cloud-call-added')
        def flush(self):
            assert len(added) == 1
            added[0].id = ident(91); events.append('cloud-call-flushed')
        def commit(self): events.append('reserve-commit')
    db = DB()
    def prepare(actual, p, cap, bound, request_hash):
        assert actual is db and p is s.p and cap is s.model
        assert bound == 1234 and request_hash == digest('actual-messages') and not events
        events.append('money-prepare')
        if deny == 'prepare': raise ModelBlocked('money prepare refused')
        return witness
    def bind(actual, p, cap, call, context):
        assert actual is db and p is s.p and cap is s.model and context is witness
        assert added == [call] and call.id == ident(91) and daily.data['calls'] == 1
        assert call.reserved_tokens == call.charged_tokens == 1234
        assert 'cloud-call-flushed' in events and 'reserve-commit' not in events
        events.append('money-bind')
        if deny == 'bind': raise ModelBlocked('money bind refused')
    monkeypatch.setattr(budget, 'Session', lambda engine: db)
    monkeypatch.setattr(budget, 'engine', lambda: None)
    monkeypatch.setattr(budget, 'settings', lambda: NS(cloud_daily_token_limit=10000, cloud_daily_call_limit=10))
    # These tests isolate same-goal money hooks with a deliberately minimal DB
    # spy. Real provider/ledger admission is covered by separate PostgreSQL tests.
    from generic_agents import cloud_budget_policy
    monkeypatch.setattr(cloud_budget_policy, '_provider', lambda *args, **kwargs: 'qwen')
    monkeypatch.setattr(cloud_budget_policy, 'budget_status', lambda _db, _p, _cap, bound, **kw:
        {'available': daily.data['tokens'] + bound <= 10000 and daily.data['calls'] < 10,
         'unlimited': False})
    monkeypatch.setattr(spending, 'prepare_reservation', prepare)
    monkeypatch.setattr(spending, 'bind_reservation', bind)
    return s, events, added, daily


def test_real_reserve_binds_actual_flushed_call_before_same_original_commit(monkeypatch):
    s, events, added, daily = reserve_io(monkeypatch)
    result = budget.reserve(s.p, s.model, 1234, digest('actual-messages'))
    assert result == added[0].id
    assert events == ['money-prepare', 'daily-insert', 'daily-lock', 'cloud-call-added',
        'cloud-call-flushed', 'money-bind', 'reserve-commit']
    assert daily.data['tokens'] == 1234 and daily.data['calls'] == 1


@pytest.mark.parametrize('stage', ['prepare', 'bind'])
def test_reserve_guard_failure_never_commits_or_returns_call(monkeypatch, stage):
    s, events, added, _daily = reserve_io(monkeypatch, deny=stage)
    with pytest.raises(ModelBlocked): budget.reserve(s.p, s.model, 1234, digest('actual-messages'))
    assert 'reserve-commit' not in events and events[-1] == 'rollback-on-exit'
    if stage == 'prepare': assert added == [] and 'daily-insert' not in events


def test_daily_token_denial_does_not_bind_or_commit_financial_hold(monkeypatch):
    s, events, added, daily = reserve_io(monkeypatch)
    daily.data = {**daily.data, 'tokens': 10000}
    with pytest.raises(budget.BudgetDenied): budget.reserve(s.p, s.model, 1234, digest('actual-messages'))
    assert added == [] and 'money-bind' not in events and 'reserve-commit' not in events


def test_real_transport_calls_financial_guard_after_durable_reserved_and_before_original_checkpoint(monkeypatch):
    s = model_scene(); f = install(monkeypatch, envelope(s.output))
    monkeypatch.setattr(spending, 'before_transport', lambda: f.events.append('financial-before-http'))
    result, _ = f.call()
    assert result == s.output and len(f.posts) == len(f.captures) == 1
    assert f.events.index('on_reserved') < f.events.index('financial-before-http')
    assert f.events.index('financial-before-http') < f.events.index('before_transport') < f.events.index('post')


def test_financial_pre_http_denial_keeps_one_existing_reservation_and_never_posts(monkeypatch):
    s = model_scene(); f = install(monkeypatch, envelope(s.output))
    def deny():
        f.events.append('financial-before-http'); raise ModelBlocked('actual money denied')
    monkeypatch.setattr(spending, 'before_transport', deny)
    with pytest.raises(ModelBlocked): f.call()
    assert f.events.count('reserved') == 1 and f.posts == f.captures == []
    assert 'on_reserved' in f.events and 'before_transport' not in f.events
    assert len(f.finished) == 1 and f.finished[0][0] == 'failed'


def test_reserved_callback_failure_never_reaches_money_intent_or_http(monkeypatch):
    s = model_scene(); f = install(monkeypatch, envelope(s.output))
    def unexpected(): raise AssertionError('financial intent must follow durable SDK callback')
    def deny(_): raise ModelBlocked('SDK reserved callback failed')
    monkeypatch.setattr(spending, 'before_transport', unexpected)
    with pytest.raises(ModelBlocked): f.call(on_reserved=deny)
    assert f.posts == f.captures == [] and len(f.finished) == 1


def test_raw_state_and_no_answer_recovery_do_not_require_money_proof(monkeypatch):
    t = protocol_tests.scene.__wrapped__(monkeypatch)
    protocol_tests._prepared(t)
    def unexpected(*args, **kwargs): raise AssertionError('raw custody must precede financial adoption')
    monkeypatch.setattr(spending, 'checked_original_hold', unexpected)
    assert t.state()['capture'] is None
    with pytest.raises(ModelBlocked): runtime.recover(t.db, t.p, t.advice, t.inputs, t.key, invocation=t.call)


@pytest.mark.parametrize('path', ['recover', 'sdk_result'])
def test_post_capture_financial_loss_blocks_adoption_but_keeps_original_bytes_and_journals(monkeypatch, path):
    t = protocol_tests.scene.__wrapped__(monkeypatch)
    if path == 'sdk_result': protocol_tests._succeeded(t)
    else: protocol_tests._captured(t)
    before = copy.deepcopy(t.call.data); rows = {kind: list(values) for kind, values in t.rows.items()}
    def deny(db, p, context, cloud_call, *, require_intent):
        assert context['invocation'].id == t.call.id and require_intent
        assert context['prepared'] == before[runtime.STATE_KEY]['request']['prepared']
        assert context['frozen_request_hash'] == before[runtime.STATE_KEY]['request']['request_hash']
        assert cloud_call.id == before[runtime.STATE_KEY]['call_id']
        raise ModelBlocked('retained financial evidence needs review')
    monkeypatch.setattr(spending, 'checked_original_hold', deny)
    with pytest.raises(ModelBlocked):
        if path == 'recover': runtime.recover(t.db, t.p, t.advice, t.inputs, t.key, invocation=t.call)
        else: runtime._sdk_result(t.db, t.p, t.task, t.plan, t.execution, t.call, t.advice)
    assert t.call.data == before and t.commits == 0
    assert all(t.rows[kind] == values for kind, values in rows.items())
