"""Actual scheduler orchestration with explicit early SQL/authority seams.

No PostgreSQL contention, valid source, permission, video, assessment or receipt
is established here. Candidates are lightweight synthetic SQL-return carriers;
accepted lock paths stop before actual actor/source validation. Root separately
proves real concurrency. The cleanup seam also stops before changing any work.
"""
import ast
import inspect
import textwrap
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import orm

from generic_agents import db as database
from generic_agents import supplement, worker
from generic_agents.contracts import digest
from generic_agents.models import Audit, DomainInstance, SupplementPlan
from generic_agents.frame_supplement_v236 import COMMAND, STATE, FAMILY


WORKSPACE = '163fed00-0000-4000-8000-000000000001'
INSTANCE = '163fed00-0000-4000-8000-000000000002'
OWNER = '163fed00-0000-4000-8000-000000000003'
PLAN = '163fed00-0000-4000-8000-000000000004'
OTHER_PLAN = '163fed00-0000-4000-8000-000000000005'


class BeforeAuthority(Exception):
    pass


def candidate(identity=PLAN, status='reading'):
    return SimpleNamespace(id=identity, instance_id=INSTANCE,
        workspace_id=WORKSPACE, status=status)


class SchedulerIO:
    """Only the scheduler's finite SELECTs; no arbitrary-query fallback."""
    def __init__(self, candidates, *, busy=None, changed_status=None, recovery_busy=False, new_family=False):
        self.candidates = candidates; self.busy = busy
        self.changed_status = changed_status; self.recovery_busy = recovery_busy
        self.events = []; self.try_count = 0; self.plan_reads = 0
        self.family_reads = []; self.peeked = None; self.new_family = new_family
        self.rows = {item.id: SimpleNamespace(id=item.id, instance_id=item.instance_id,
            workspace_id=item.workspace_id, status=item.status,
            data={'approved_by': OWNER, **({FAMILY: {}} if new_family else {})}) for item in candidates}

    def __enter__(self):
        return self

    def __exit__(self, *_error):
        self.events.append('close')
        return False

    def execute(self, statement):
        descriptions = statement.column_descriptions
        assert [item['name'] for item in descriptions] == [
            'id', 'instance_id', 'workspace_id', 'status']
        assert all(item['entity'] is SupplementPlan for item in descriptions)
        assert statement._for_update_arg is None, 'candidate peek must not lock any Plan'
        assert statement._limit_clause.value == 20
        values = [item.value for predicate in statement._where_criteria
            for item in predicate.get_children() if hasattr(item, 'value')]
        assert WORKSPACE in values
        self.events.append('peek')
        return self.candidates

    def get(self, model, identity, *args, **kwargs):
        # Exact family-metadata peek only: this does not increment the locked
        # Plan-read counter, acquire a row lock or supply any actor authority.
        assert model is SupplementPlan and not args and not kwargs
        reading = [item for item in self.candidates if item.status == 'reading']
        assert identity == reading[self.try_count].id and identity in self.rows
        self.peeked = identity
        self.family_reads.append(('plan-metadata', identity, tuple(self.events)))
        return self.rows[identity]

    def scalar(self, statement):
        description = statement.column_descriptions[0]
        model = description.get('entity')
        if model is Audit:
            # Only is_family's workspace/object/two-action ID probe is known.
            # Unknown SELECTs still fail; this is not an always-empty fallback.
            assert description['name'] == 'id'
            assert statement._for_update_arg is None
            assert statement._limit_clause.value == 1
            assert len(statement._where_criteria) == 3
            assert statement.compile().params == {'workspace_id_1': WORKSPACE,
                'object_id_1': self.peeked, 'action_1': [COMMAND, STATE], 'param_1': 1}
            assert self.peeked in self.rows and not self.new_family
            self.family_reads.append(('audit-metadata', self.peeked, tuple(self.events)))
            return None  # The explicitly synthetic legacy candidate has no new-family Audit.
        if model is None:
            function, = statement.selected_columns
            assert function.name == 'pg_try_advisory_xact_lock'
            argument, = function.clauses
            reading = [item for item in self.candidates if item.status == 'reading']
            item = reading[self.try_count]; self.try_count += 1
            nonce = ('frame-supplement-assess-' if self.new_family else 'supplement-assess-') + item.id
            assert argument.value == int(digest({'workspace': WORKSPACE,
                'request': nonce})[:16], 16) - (1 << 63)
            self.events.append('try:' + item.id)
            return False if self.busy == 'advisory' and self.try_count == 1 else True
        lock = statement._for_update_arg
        assert lock is not None and lock.skip_locked is True
        assert not lock.read and not lock.key_share
        parameters = statement.compile().params
        assert WORKSPACE in parameters.values()
        if model is DomainInstance:
            assert INSTANCE in parameters.values()
            self.events.append('instance')
            return None if self.busy == 'instance' else SimpleNamespace(id=INSTANCE)
        assert model is SupplementPlan
        self.plan_reads += 1
        identity = next(identity for identity in self.rows if identity in parameters.values())
        if self.plan_reads == 1 or not self.recovery_busy:
            self.events.append('plan:' + identity)
        else:
            self.events.append('recovery-plan-busy')
            return None
        if self.busy == 'plan': return None
        return self.rows[identity]

    def refresh(self, row):
        assert row is self.rows[row.id]
        self.events.append('refresh:' + row.id)
        if self.changed_status is not None: row.status = self.changed_status

    def rollback(self):
        self.events.append('rollback')

    def commit(self):
        self.events.append('commit')


def install(monkeypatch, io, *, actor_error=None, stop_in_cleanup=False):
    engine = object()
    monkeypatch.setattr(database, 'engine', lambda: engine)

    def session(actual_engine):
        assert actual_engine is engine
        return io

    def actor(actual_db, workspace_id, owner_id):
        assert actual_db is io and workspace_id == WORKSPACE and owner_id == OWNER
        io.events.append('actor')
        if actor_error is not None: raise actor_error
        raise BeforeAuthority('stop before any permission/source success is supplied')

    def cleanup(actual_db, principal, row, reason):
        assert stop_in_cleanup and actual_db is io and row is io.rows[row.id]
        assert principal.workspace_id == WORKSPACE and principal.user_id == OWNER
        assert principal.role == 'viewer' and principal.session_id == 'supplement-stop'
        assert reason == 'actual actor rejection seam'
        io.events.append('cleanup')
        raise BeforeAuthority('stop before metadata cleanup is manufactured')

    def unexpected_advance(*_args):
        pytest.fail('test must stop before actual authority or advance is supplied')

    monkeypatch.setattr(orm, 'Session', session)
    monkeypatch.setattr(worker, 'actor', actor)
    monkeypatch.setattr(supplement, 'advance', unexpected_advance)
    monkeypatch.setattr(supplement, 'block', cleanup)


@pytest.mark.parametrize('status', ['reading', 'rechecking'])
def test_actual_scheduler_locks_instance_before_plan_and_before_actor(monkeypatch, status):
    io = SchedulerIO([candidate(status=status)]); install(monkeypatch, io)
    with pytest.raises(BeforeAuthority):
        supplement.schedule_once(workspace_id=WORKSPACE)
    expected = ['peek', 'rollback']
    if status == 'reading': expected.append('try:' + PLAN)
    assert io.events == expected + ['instance', 'plan:' + PLAN,
        'refresh:' + PLAN, 'actor', 'close']
    assert io.rows[PLAN].status == status
    assert io.family_reads == ([('plan-metadata', PLAN, ('peek', 'rollback')),
        ('audit-metadata', PLAN, ('peek', 'rollback'))] if status == 'reading' else [])


def test_new_family_metadata_selects_its_exact_nonce_without_taking_a_plan_or_actor_lock(monkeypatch):
    """Synthetic family hint only; stop before validating any real new Plan."""
    io = SchedulerIO([candidate()], new_family=True); install(monkeypatch, io)
    with pytest.raises(BeforeAuthority):
        supplement.schedule_once(workspace_id=WORKSPACE)
    assert io.family_reads == [('plan-metadata', PLAN, ('peek', 'rollback'))]
    assert io.events == ['peek', 'rollback', 'try:' + PLAN, 'instance',
        'plan:' + PLAN, 'refresh:' + PLAN, 'actor', 'close']


@pytest.mark.parametrize('busy', ['advisory', 'instance', 'plan'])
def test_busy_lock_rolls_back_and_does_not_block_or_advance_business(monkeypatch, busy):
    io = SchedulerIO([candidate()], busy=busy); install(monkeypatch, io)
    supplement.schedule_once(workspace_id=WORKSPACE)
    expected = ['peek', 'rollback', 'try:' + PLAN]
    if busy != 'advisory': expected.append('instance')
    if busy == 'plan': expected.append('plan:' + PLAN)
    assert io.events == expected + ['rollback', 'close']
    assert io.rows[PLAN].status == 'reading'


@pytest.mark.parametrize('peek_status,current_status', [
    ('reading', 'rechecking'), ('rechecking', 'reading'), ('reading', 'cancelled'),
])
def test_changed_status_releases_locks_for_next_poll_before_any_actor_gate(monkeypatch, peek_status, current_status):
    io = SchedulerIO([candidate(status=peek_status)], changed_status=current_status)
    install(monkeypatch, io)
    supplement.schedule_once(workspace_id=WORKSPACE)
    assert 'actor' not in io.events and 'cleanup' not in io.events
    assert io.events[-2:] == ['rollback', 'close']
    assert io.rows[PLAN].status == current_status


def test_busy_first_candidate_releases_its_transaction_before_second_candidate(monkeypatch):
    io = SchedulerIO([candidate(), candidate(OTHER_PLAN)], busy='advisory')
    install(monkeypatch, io)
    with pytest.raises(BeforeAuthority):
        supplement.schedule_once(workspace_id=WORKSPACE)
    assert io.events == ['peek', 'rollback', 'try:' + PLAN, 'rollback',
        'try:' + OTHER_PLAN, 'instance', 'plan:' + OTHER_PLAN,
        'refresh:' + OTHER_PLAN, 'actor', 'close']
    assert all(row.status == 'reading' for row in io.rows.values())


def test_actual_actor_failure_cleanup_does_not_reacquire_instance_after_plan(monkeypatch):
    io = SchedulerIO([candidate()])
    install(monkeypatch, io, actor_error=HTTPException(403, 'actual actor rejection seam'),
        stop_in_cleanup=True)
    with pytest.raises(BeforeAuthority):
        supplement.schedule_once(workspace_id=WORKSPACE)
    assert io.events == ['peek', 'rollback', 'try:' + PLAN, 'instance',
        'plan:' + PLAN, 'refresh:' + PLAN, 'actor', 'rollback',
        'plan:' + PLAN, 'refresh:' + PLAN, 'cleanup', 'close']
    assert io.rows[PLAN].status == 'reading'


def test_busy_cleanup_plan_is_skipped_instead_of_marked_blocked(monkeypatch):
    io = SchedulerIO([candidate()], recovery_busy=True)
    install(monkeypatch, io, actor_error=HTTPException(403, 'actual actor rejection seam'))
    supplement.schedule_once(workspace_id=WORKSPACE)
    assert io.events[-3:] == ['recovery-plan-busy', 'rollback', 'close']
    assert 'cleanup' not in io.events and 'commit' not in io.events
    assert io.rows[PLAN].status == 'reading'


def test_empty_candidate_read_has_no_plan_locks_or_actor_work(monkeypatch):
    io = SchedulerIO([]); install(monkeypatch, io)
    supplement.schedule_once(workspace_id=WORKSPACE)
    assert io.events == ['peek', 'rollback', 'close']


def test_success_branch_commits_each_item_before_loop_can_start_another_transaction():
    """Control-flow check only; no fake successful advance/receipt is run."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(supplement.schedule_once)))
    loop, = [node for node in ast.walk(tree) if isinstance(node, ast.For)]
    transaction, = [node for node in loop.body if isinstance(node, ast.Try)]
    names = [node.value.func.id if isinstance(node.value.func, ast.Name)
        else node.value.func.attr for node in transaction.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)]
    assert names[-2:] == ['advance', 'commit']
