"""Gate and replacement proof tests with explicit persistence IO spies."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_continuation_execution_goal_v314 as g
from generic_agents import same_goal_continuation_execution_runtime_v314 as runtime
from generic_agents import same_goal_continuation_result_receiver_v314 as receiver
from generic_agents.contracts import digest
from generic_agents.models import Audit, Event, Invocation, Memory


@pytest.fixture
def case(monkeypatch):
    at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    spec = {'purpose': '原目标'}
    task = NS(id='original-task', data={'goal_confirmed_by': 'operator', 'goal_spec': spec,
        'goal_hash': digest(spec), 'goal_version': 1, 'goal_window': {},
        'native_goal_context_hash': 'context'})
    t = NS(db=NS(no_autoflush=nullcontext()), p=NS(workspace_id='workspace'), task=task,
        at=at, plans=[], calls=[], audits=[], result_events=[], memories=[], acknowledgements=[],
        verified=[], inventory_reads=0)
    def inventory(*args):
        t.inventory_reads += 1
        return t.plans
    def rows(db, p, model, *where):
        if model is Invocation:
            return t.calls
        if model is Event:
            return t.result_events
        if model is Memory:
            return t.memories
        assert model is Audit
        if receiver.RECEIVED in where[0].compile().params.values():
            return t.acknowledgements
        return t.audits
    def results(db, p, task, saved, *, verify):
        t.verified.append((saved['plan'].id, verify))
        if saved.get('broken'):
            raise HTTPException(409, '独立接收核验失败')
        return saved['output'], saved['receipt']
    monkeypatch.setattr(g.store, '_inventory', inventory)
    monkeypatch.setattr(g.store, '_rows', rows)
    monkeypatch.setattr(g.store, '_results', results)
    monkeypatch.setattr(g.store, '_clock', lambda *args: at)
    return t


def add(t, identity, phase, *, previous=None, job_status=None, call_status=None):
    goal = g.snapshot(t.task)
    proposal = {'record_hash': 'proposal-' + identity, 'source_hash': 'source', 'advice_plan_id': 'advice',
        'continuation_ref': {'id': 'draft'}, 'supersedes_plan_id': previous,
        'grant_ref': {'id': 'grant-' + identity}, 'grant': {'basis': {'goal': {
            'hash': goal['hash'], 'version': goal['version'], 'snapshot_hash': digest(goal),
            'native_context_hash': 'context'}}}}
    plan = NS(id=identity, status=phase, version=2, data={'operations': ['audit-' + identity]})
    job = None if job_status is None else NS(id='job-' + identity, status=job_status,
        attempts=0 if job_status == 'cancelled' else 1, lease_until=None, lease_token=None)
    saved = {'proposal': proposal, 'plan': plan, 'job': job, 'start': job, 'output': None,
        'receipt': None, 'events': {}}
    t.plans.append(saved)
    if call_status:
        assert job is not None
        call = NS(id='sdk-' + identity, status=call_status, idempotency_key=g.store._key(identity),
            data={'job_id': job.id, 'approval': {'schema': g.c.APPROVAL, 'task_id': t.task.id,
                'plan_id': identity, 'job_id': job.id}})
        t.calls.append(call)
    if phase in {'adopted', 'result_returned'}:
        deliver(t, saved, received=True)
        review = NS(id='review-' + identity, action=g.c.ACTION_REVIEW,
            data={'request': {'decision': 'adopt' if phase == 'adopted' else 'return'}})
        saved['events'][review.id] = review
    return saved


def deliver(t, saved, *, received):
    identity, job = saved['plan'].id, saved['job']
    source = {'task_id': t.task.id, 'plan_id': identity, 'job_id': job.id}
    output = NS(id='output-' + identity, object_id=identity, action=g.store.OUTPUT,
        data={**source, 'invocation_id': 'sdk-' + identity, 'output_hash': 'value-' + identity})
    dispatch = NS(id='dispatch-' + identity, object_id=identity, action=runtime.DISPATCHED,
        data={**source, 'invocation_id': 'sdk-' + identity, 'data_actor_id': 'receiver'})
    saved['output'] = output
    t.audits.extend([output, dispatch])
    event = NS(id='event-' + identity, kind=receiver.KIND, idempotency_key='continuation-read-result-v314:' + identity,
        status='received' if received else 'pending_delivery', received_at=t.at if received else None,
        data={**source, 'schema': receiver.EVENT_SCHEMA, 'data_actor_id': 'receiver',
            'submission_hash': saved['proposal']['record_hash'], 'output_ref': g.store._ref(output),
            'output_hash': output.data['output_hash']})
    t.result_events.append(event)
    if received:
        memory = NS(id='memory-' + identity, data={**source, 'type': receiver.MEMORY_TYPE, 'event_id': event.id})
        ack = NS(id='ack-' + identity, action=receiver.RECEIVED, object_id=event.id,
            data={**source, 'event_id': event.id})
        t.memories.append(memory)
        t.acknowledgements.append(ack)
        saved['receipt'] = NS(id='receipt-' + identity, object_id=identity, action=g.store.RECEIVED,
            data={**source, 'ack': {'event_id': event.id, 'memory_id': memory.id, 'receipt_audit_ref': g.store._ref(ack)}})
        t.audits.append(saved['receipt'])
    return event


def gate(t):
    return g.goal_gate(t.db, t.p, t.task)


def test_even_unmarked_task_reads_positive_inventory(case):
    assert gate(case) == {'all_reviewed': True, 'runs': [], 'basis_hash': digest([])}
    assert case.inventory_reads == 1
    case.audits.append(NS(action=runtime.DISPATCHED, object_id='missing-plan',
        data={'task_id': case.task.id, 'invocation_id': 'orphan-sdk'}))
    with pytest.raises(HTTPException): gate(case)


def test_explicit_transitive_adoption_only_settles_its_own_lineage(case):
    t = case
    add(t, 'a', 'returned')
    add(t, 'b', 'result_returned', previous='a', job_status='succeeded', call_status='succeeded')
    add(t, 'c', 'adopted', previous='b', job_status='succeeded', call_status='succeeded')
    add(t, 'd', 'returned')
    result = gate(t)
    assert result['all_reviewed'] is False
    assert [row['adopted_successor_id'] for row in result['runs']] == ['c', 'c', None, None]
    assert dict(t.verified)['b'] and dict(t.verified)['c']
    t.plans.pop()
    assert gate(t)['all_reviewed'] is True
    assert gate(t)['basis_hash'] != result['basis_hash']


@pytest.mark.parametrize('phase', ['in_review', 'approved', 'queued', 'cancel_requested', 'returned'])
def test_pending_execution_never_passes_formal_acceptance(case, phase):
    t = case
    add(t, 'work', phase)
    assert gate(t)['all_reviewed'] is False
    with pytest.raises(HTTPException) as error:
        g.require_settled(t.db, t.p, t.task)
    assert error.value.status_code == 409


def test_unclaimed_cancel_is_settled_but_cannot_satisfy_a_returned_parent(case):
    t = case
    add(t, 'a', 'returned')
    row = add(t, 'b', 'cancelled', previous='a', job_status='cancelled')
    assert [run['settled'] for run in gate(t)['runs']] == [False, True]
    row['job'].attempts = 1
    with pytest.raises(HTTPException): gate(t)


@pytest.mark.parametrize('fault', ['ack', 'review', 'sdk', 'independent_receipt', 'lease'])
def test_adopted_flag_alone_cannot_certify_a_result(case, fault):
    t = case
    row = add(t, 'work', 'adopted', job_status='succeeded', call_status='succeeded')
    if fault == 'ack':
        t.audits.remove(row['receipt'])
        row['receipt'] = None
    elif fault == 'review':
        row['events'].clear()
    elif fault == 'sdk':
        t.calls[0].status = 'unknown'
    elif fault == 'independent_receipt':
        row['broken'] = True
    else:
        row['job'].lease_until = t.at + timedelta(seconds=1)
    with pytest.raises(HTTPException): gate(t)


@pytest.mark.parametrize('fault', ['unknown', 'cycle', 'cross_scope', 'duplicate', 'spent_grant', 'orphan'])
def test_invalid_correction_cannot_discharge_a_predecessor(case, fault):
    t = case
    old = add(t, 'a', 'queued', job_status='failed', call_status='failed')
    successor = add(t, 'b', 'adopted', previous='a', job_status='succeeded', call_status='succeeded')
    assert gate(t)['all_reviewed'] is True
    if fault == 'unknown':
        t.calls[0].status = 'unknown'
    elif fault == 'cycle':
        old['plan'].status = 'returned'
        old['proposal']['supersedes_plan_id'] = 'b'
    elif fault == 'cross_scope':
        successor['proposal']['continuation_ref']['id'] = 'another-draft'
    elif fault == 'duplicate':
        add(t, 'c', 'returned', previous='a')
    elif fault == 'spent_grant':
        successor['proposal']['grant_ref']['id'] = old['proposal']['grant_ref']['id']
    else:
        successor['proposal']['supersedes_plan_id'] = 'missing'
    with pytest.raises(HTTPException): gate(t)


def halted(t):
    row = add(t, 'halted', 'cancel_requested', job_status='cancelled')
    job = row['job']
    job.attempts = 1
    job.lease_token = 'original-lease'
    job.data = {'actor_id': 'executor'}
    row['start'] = NS(actor_id='executor')
    event = NS(id='halt-audit', action=runtime.HALTED, object_id=row['plan'].id, actor_id='executor',
        data={'task_id': t.task.id, 'plan_id': row['plan'].id, 'job_id': job.id,
            'submission_hash': row['proposal']['record_hash'], 'lease_token': job.lease_token,
            'reason': 'cancelled_before_sdk'})
    t.audits.append(event)
    return row, event


def test_worker_halt_settles_only_after_job_terminal_cancel(case):
    row, event = halted(case)
    row['job'].status = 'running'
    row['job'].lease_until = case.at + timedelta(seconds=1)
    before = gate(case)
    assert before['all_reviewed'] is False
    row['job'].status = 'cancelled'
    row['job'].lease_until = None
    after = gate(case)
    assert after['all_reviewed'] is True and after['basis_hash'] != before['basis_hash']
    assert after['runs'][0]['phase'] == row['plan'].status == 'cancel_requested'
    assert after['runs'][0]['halted_before_sdk'] is True
    assert row['job'].attempts == 1


@pytest.mark.parametrize('fault', ['other_lease', 'actor', 'submission', 'sdk', 'event', 'memory', 'ack'])
def test_halt_cannot_settle_with_wrong_fence_or_effect_witness(case, fault):
    row, event = halted(case)
    if fault == 'other_lease':
        event.data['lease_token'] = 'other-lease'
    elif fault == 'actor':
        event.actor_id = 'different-user'
    elif fault == 'submission':
        event.data['submission_hash'] = 'other-submission'
    elif fault == 'sdk':
        job = row['job']
        case.calls.append(NS(id='unknown-call', status='unknown', idempotency_key=g.store._key(row['plan'].id),
            data={'job_id': job.id, 'approval': {'schema': g.c.APPROVAL, 'task_id': case.task.id,
                'plan_id': row['plan'].id, 'job_id': job.id}}))
    elif fault == 'event':
        case.result_events.append(NS(id='orphan-result-event',
            data={'task_id': case.task.id, 'plan_id': row['plan'].id, 'job_id': row['job'].id}))
    elif fault == 'memory':
        case.memories.append(NS(id='orphan-memory',
            data={'task_id': case.task.id, 'plan_id': row['plan'].id, 'job_id': row['job'].id}))
    else:
        case.acknowledgements.append(NS(id='orphan-ack',
            data={'task_id': case.task.id, 'plan_id': row['plan'].id, 'job_id': row['job'].id}))
    with pytest.raises(HTTPException): gate(case)


@pytest.mark.parametrize('inventory', ['result_events', 'memories', 'acknowledgements'])
def test_sole_receiver_witness_blocks_even_when_task_index_is_absent(case, inventory):
    getattr(case, inventory).append(NS(id='orphan', data={'task_id': case.task.id,
        'plan_id': 'missing', 'job_id': 'missing-job'}))
    with pytest.raises(HTTPException): gate(case)


@pytest.mark.parametrize('status', ['pending_delivery', 'blocked_delivery'])
def test_pending_delivery_matches_verified_output_and_remains_unsettled(case, status):
    t = case
    row = add(t, 'work', 'queued', job_status='succeeded', call_status='succeeded')
    event = deliver(t, row, received=False)
    event.status = status
    assert gate(t)['all_reviewed'] is False
    assert t.verified == [('work', True)]
    event.idempotency_key = 'wrong-key'
    with pytest.raises(HTTPException): gate(t)


@pytest.mark.parametrize('fault', ['wrong_event_key', 'wrong_job', 'wrong_ack_ref', 'duplicate_memory'])
def test_adopted_receiver_inventory_must_be_exact(case, fault):
    t = case
    add(t, 'work', 'adopted', job_status='succeeded', call_status='succeeded')
    assert gate(t)['all_reviewed'] is True
    if fault == 'wrong_event_key':
        t.result_events[0].idempotency_key = 'wrong-key'
    elif fault == 'wrong_job':
        t.memories[0].data['job_id'] = 'other-job'
    elif fault == 'wrong_ack_ref':
        t.acknowledgements[0].id = 'another-audit'
    else:
        t.memories.append(NS(id='duplicate', data=t.memories[0].data))
    with pytest.raises(HTTPException): gate(t)
