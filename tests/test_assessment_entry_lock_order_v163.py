"""Entry lock order only; not source authorization or a real PG race.

Real public functions and checked mapped rows reach a deliberately rejecting
first source gate. IO accepts only the exact advisory and scoped row queries.
The approval-commit boundary is additionally checked on the real confirm AST;
no successful source, media, approval, or dispatch validator is substituted.
"""
import ast
from copy import deepcopy
from datetime import datetime, timezone
import inspect
from operator import eq
import textwrap
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy.sql.elements import BinaryExpression, BindParameter

from generic_agents.contracts import digest
from generic_agents.models import DomainInstance, EvidenceAssessment, SignalAdvice, SupplementPlan, SupplementWatch
from generic_agents import evidence_assessment_api as assessments
from generic_agents import signal_advice_api as advice
from generic_agents import supplement
from generic_agents import supplement_watch as watches


def uid(n):
    return '163abe00-0000-4000-8000-' + f'{n:012x}'


def principal():
    return SimpleNamespace(workspace_id=uid(1), user_id=uid(2), role='admin')


def instance(p):
    return DomainInstance(id=uid(3), workspace_id=p.workspace_id, status='partially_active', data={})


class EntryIO:
    """Explicit read statements, no write/commit or unknown-query fallback."""
    def __init__(self, p, request_id, reads):
        self.p = p; self.request_id = request_id; self.reads = list(reads); self.events = []

    def execute(self, statement, parameters):
        assert str(statement) == 'SELECT pg_advisory_xact_lock(:key)'
        assert parameters == {'key': int(digest({'workspace': self.p.workspace_id,
            'request': self.request_id})[:16], 16) - (1 << 63)}
        self.events.append('advisory')

    def scalar(self, statement):
        assert self.reads, 'unexpected row read'
        model, fields, locked, result, event = self.reads.pop(0)
        assert statement.column_descriptions[0]['entity'] is model
        predicates = tuple(statement._where_criteria)
        assert len(predicates) == len(fields)
        for predicate, (name, value) in zip(predicates, fields):
            assert isinstance(predicate, BinaryExpression) and predicate.operator is eq
            assert predicate.left.compare(model.__table__.c[name])
            assert isinstance(predicate.right, BindParameter)
            assert type(predicate.right.value) is str and predicate.right.value == value
        lock = statement._for_update_arg
        assert (lock is not None) is locked
        if locked:
            assert lock.read is False and lock.key_share is False
        self.events.append(event)
        return result


def instance_read(p, row):
    return (DomainInstance, [('id', row.id), ('workspace_id', p.workspace_id)], True, row, 'instance')


def stop_at_gate(monkeypatch, module, name, db, events):
    error = HTTPException(409, '首来源门刻意拒绝；此测试不建立执行资格')

    def reject(*args, **kwargs):
        assert args[0] is db
        assert db.events == events, 'exact assessment advisory and Instance lock must precede source gate'
        assert db.reads == []
        raise error

    monkeypatch.setattr(module, name, reject)
    return error


def request(p, row):
    return assessments.Request(instance_id=row.id, release_id=uid(4), entity_ids=[uid(5)],
        purpose='锁序边界测试，不代表完成业务核查', required_modalities=['video'], lookback_hours=24,
        request_id='assessment-entry-lock-order-163')


def test_new_public_assessment_locks_instance_before_scoped_objects(monkeypatch):
    p = principal(); row = instance(p); body = request(p, row)
    prior_query = (EvidenceAssessment, [('workspace_id', p.workspace_id), ('request_id', body.request_id)],
        False, None, 'prior')
    db = EntryIO(p, body.request_id, [prior_query, instance_read(p, row)])
    failure = stop_at_gate(monkeypatch, assessments, 'scoped_objects', db, ['advisory', 'prior', 'instance'])
    outcome = {'created': True}
    with pytest.raises(HTTPException) as error:
        assessments.create_saved(body, p, db, outcome=outcome)
    assert error.value is failure and outcome == {'created': False}


def test_prior_public_assessment_returns_checked_original_without_instance_or_source_gate(monkeypatch):
    p = principal(); row = instance(p); body = request(p, row)
    content = body.model_dump(exclude={'question_id'})
    prior = EvidenceAssessment(id=uid(6), workspace_id=p.workspace_id, instance_id=row.id,
        owner_id=p.user_id, request_id=body.request_id, status='queued', version=1,
        data={'creation_hash': digest(content), 'request': content, 'request_hash': digest(content)})
    before = deepcopy(prior.data)
    db = EntryIO(p, body.request_id, [(EvidenceAssessment,
        [('workspace_id', p.workspace_id), ('request_id', body.request_id)], False, prior, 'prior')])
    stop_at_gate(monkeypatch, assessments, 'scoped_objects', db, ['unreachable'])
    outcome = {}
    assert assessments.create_saved(body, p, db, outcome=outcome) is prior
    assert db.events == ['advisory', 'prior'] and db.reads == []
    assert outcome == {'created': False} and prior.data == before


@pytest.mark.parametrize('completed_checks', [0, 2], ids=['first-check', 'third-check'])
def test_watch_advance_prelocks_actual_next_request_before_valid(monkeypatch, completed_checks):
    p = principal(); target = instance(p)
    auth = {'business_check_triggers': {'triggers': ['新观测到达']}}
    checks = [{'assessment_id': uid(20 + n)} for n in range(completed_checks)]
    row = SupplementWatch(id=uid(7), workspace_id=p.workspace_id, instance_id=target.id,
        owner_id=p.user_id, status='active', data={'authorization': auth, 'authorization_hash': digest(auth),
            'history': [], 'history_hash': digest([]), 'automatic_checks': checks,
            'automatic_checks_hash': digest(checks)})
    db = EntryIO(p, 'auto-check-' + row.id + '-' + str(completed_checks + 1), [instance_read(p, target)])
    failure = stop_at_gate(monkeypatch, watches, 'valid', db, ['advisory', 'instance'])
    with pytest.raises(HTTPException) as error:
        watches.advance(db, p, row)
    assert error.value is failure


def test_watch_without_automatic_check_trigger_keeps_original_no_extra_lock(monkeypatch):
    p = principal(); auth = {}
    row = SupplementWatch(id=uid(7), workspace_id=p.workspace_id, instance_id=uid(3),
        owner_id=p.user_id, status='active', data={'authorization': auth,
            'authorization_hash': digest(auth), 'history': [], 'history_hash': digest([])})
    db = EntryIO(p, 'must-not-lock', [])
    failure = stop_at_gate(monkeypatch, watches, 'valid', db, [])
    with pytest.raises(HTTPException) as error:
        watches.advance(db, p, row)
    assert error.value is failure


@pytest.mark.parametrize('status', ['reading', 'rechecking'])
def test_supplement_first_plan_guard_has_creation_lock_only_in_reading_state(monkeypatch, status):
    p = principal(); target = instance(p)
    row = SupplementPlan(id=uid(8), workspace_id=p.workspace_id, instance_id=target.id,
        owner_id=p.user_id, status=status, data={})
    reading = status == 'reading'
    db = EntryIO(p, 'supplement-assess-' + row.id, [instance_read(p, target)] if reading else [])
    failure = stop_at_gate(monkeypatch, watches, 'plan_guard', db, ['advisory', 'instance'] if reading else [])
    with pytest.raises(HTTPException) as error:
        supplement.advance(db, p, row)
    assert error.value is failure


def advice_case(resumed=False, dispatched=False):
    p = principal(); target = instance(p)
    body = advice.Confirm(version=1, request_id='advice-assessment-lock-order-163', acknowledge_limits=True)
    source = {'entity_id': uid(5)}
    action = advice.Action(route='evidence_check', purpose='核对原视频资料', criteria={
        'goal': 'current_state', 'required_modalities': ['video'], 'lookback_hours': 24, 'purpose': '核对原资料'}).model_dump()
    data = {'request': source, 'request_hash': digest(source), 'draft': advice.signed(action)}
    if resumed:
        data.update(approved=advice.signed(action), confirmation={'request_id': body.request_id,
            'action_hash': digest(body.model_dump(exclude={'version', 'acknowledge_new_local_processing'}))})
    if dispatched:
        data['dispatch'] = advice.signed({'route': 'evidence_check', 'assessment_id': uid(10)})
    row = SignalAdvice(id=uid(9), workspace_id=p.workspace_id, instance_id=target.id,
        owner_id=p.user_id, signal_id=uid(11), request_id='original-advice-request-163', version=1,
        status='applied' if dispatched else ('applying' if resumed else 'needs_confirmation'),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), data=data)
    read = (SignalAdvice, [('id', row.id), ('workspace_id', p.workspace_id)], True, row, 'advice')
    return p, target, row, body, read


@pytest.mark.parametrize('resumed', [False, True], ids=['first-confirmation', 'saved-approval-resume'])
def test_advice_actual_confirm_prelocks_before_first_current_gate(monkeypatch, resumed):
    p, target, row, body, read = advice_case(resumed=resumed)
    db = EntryIO(p, body.request_id, [read, instance_read(p, target)])
    failure = stop_at_gate(monkeypatch, advice, 'current', db, ['advice', 'advisory', 'instance'])
    with pytest.raises(HTTPException) as error:
        advice.confirm(row.id, body, p, db)
    assert error.value is failure


def test_advice_already_dispatched_confirmation_remains_readback_without_new_locks():
    p, _, row, body, read = advice_case(resumed=True, dispatched=True)
    before = deepcopy(row.data); db = EntryIO(p, body.request_id, [read])
    value = advice.confirm(row.id, body, p, db)
    assert value['id'] == row.id and value['dispatch'] == before['dispatch']
    assert row.data == before and db.events == ['advice'] and db.reads == []


def test_advice_confirm_reacquires_before_second_gate_after_durable_approval_commit():
    """Control-flow oracle, not an execution/authorization simulation."""
    function = ast.parse(textwrap.dedent(inspect.getsource(advice.confirm))).body[0]
    previous = next(node for node in function.body if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name) and node.test.id == 'previous')
    fresh = previous.orelse
    signature = lambda value: ast.dump(ast.parse(value, mode='eval').body, include_attributes=False)
    is_call = lambda node, value: isinstance(node, ast.Expr) and ast.dump(node.value, include_attributes=False) == signature(value)
    fresh_current = next(i for i, node in enumerate(fresh) if is_call(node, 'current(db,p,row)'))
    first_lock = next(node for node in fresh[:fresh_current] if isinstance(node, ast.If)
        and ast.dump(node.test, include_attributes=False) == signature("row.data.get('draft',{}).get('route')=='evidence_check'"))
    assert any(is_call(node, '_lock_evidence_assessment(db,p,row,body.request_id)') for node in first_lock.body)
    commit = next(node for node in fresh[fresh_current + 1:] if is_call(node, 'db.commit()'))
    common = function.body[function.body.index(previous) + 1:]
    second_lock = next(node for node in common if isinstance(node, ast.If)
        and ast.dump(node.test, include_attributes=False) == signature("action['route']=='evidence_check'"))
    assert any(is_call(node, '_lock_evidence_assessment(db,p,row,body.request_id)') for node in second_lock.body)
    second_gate = next(node for node in common if isinstance(node, ast.If)
        and ast.dump(node.test, include_attributes=False) == signature('not committed_task'))
    assert any(is_call(node, 'current(db,p,row)') for node in second_gate.body)
    assert commit.lineno < second_lock.lineno < second_gate.lineno
    assert not any(is_call(node, 'db.commit()') for node in common[:common.index(second_gate)])
