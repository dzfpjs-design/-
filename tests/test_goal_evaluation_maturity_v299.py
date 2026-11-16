"""Candidate -> actual Memory -> outbox -> mature/cold proof control flow.

SQL row selection, source/command authentication, composite Goal assessment,
current IAM and worker leases are explicit IO boundaries. The real candidate,
report, independent audit, private context, CAS and cold publication checks run.
This does not claim real PG, end-to-end assessment, queue or target-runtime proof.
"""
import copy
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import goal_evaluation_maturity_v299 as m
from generic_agents import native_goals, relations, leases
from generic_agents import goal_evaluation_append_store_v297 as append
from generic_agents import workflow_goal_evaluation_contracts_v299 as evaluation_contract
from generic_agents.contracts import digest
from tests.test_goal_evaluation_append_command_v297 import predicate


AT = datetime(2026, 11, 2, 15, tzinfo=timezone.utc)


def uid(number): return str(UUID(int=number))


@pytest.fixture
def scene(monkeypatch):
    from generic_agents import auto_evaluation_trigger_v356 as auto_trigger
    # Origin database validation has independent PG tests; these tests retain
    # the fake-I/O maturity fixture's normal manual-producer path.
    monkeypatch.setattr(auto_trigger,'authority',lambda *args:None)
    p = NS(workspace_id=uid(1), user_id=uid(2), role='operator')
    spec = {'purpose': '读取原目标范围', 'max_steps': 20, 'max_tool_calls': 20}
    task = m.Task(id=uid(3), workspace_id=p.workspace_id, package_id=uid(4), owner_id=p.user_id,
        title='候选评价成熟', status='workflow_active', version=1, data={'goal_spec': spec,
            'goal_hash': digest(spec), 'goal_version': 1, 'goal_confirmed_by': p.user_id,
            'goal_window': {'reference_at': AT.isoformat()}, 'native_goal_context': {'fixed': True},
            'native_goal_context_hash': digest({'fixed': True}), 'native_goal_query_id': uid(5),
            'native_goal_check_id': uid(6)})
    old = {'schema': m.provider.HEAD, 'task_id': task.id, 'evaluation_ref': {'id': uid(7), 'record_hash': 'a' * 64},
        'generation': 1, 'query_id': uid(8), 'goal_snapshot_hash': digest(m.snapshot(task)),
        'source_binding_hash': 'b' * 64, 'goal_check_job_id': uid(9), 'actor_id': p.user_id, 'previous_hash': None}
    old['head_hash'] = digest(old); task.data[m.provider.KEY] = copy.deepcopy(old)
    producer = m.Job(id=uid(10), workspace_id=p.workspace_id, kind='append_goal_evaluation_v297', status='running',
        idempotency_key='goal-evaluation-append:' + task.id + ':' + uid(11), attempts=1,
        lease_token=uid(12), lease_until=AT + timedelta(minutes=3), data={'task_id': task.id, 'actor_id': p.user_id})
    call = m.Invocation(id=uid(13), workspace_id=p.workspace_id, status='running', capability_id=uid(14),
        idempotency_key=producer.idempotency_key, data={'job_id': producer.id, 'lease_token': producer.lease_token})
    record = {'schema': evaluation_contract.RECORD, 'producer_family': evaluation_contract.PRODUCER_FAMILY,
        'scope': {'native_goal_context_hash': task.data['native_goal_context_hash']},
        'goal_snapshot': m.snapshot(task), 'producer': {'job_id': producer.id, 'invocation_id': call.id,
            'lease_token': producer.lease_token}, 'cut_at': AT.isoformat(),
        'preparation_policy': {'time_basis': 'rolling_latest', 'max_age_seconds': 3600},
        'evaluation_result': {'result': {'status': 'insufficient', 'evidence': [], 'observation_evidence': [],
            'gaps': [{'reason': '真实资料不足'}], 'conflicts': [], 'excluded_evidence': []}}}
    record['record_hash'] = evaluation_contract.record_hash(record)
    row = m.Evaluation(id=call.id, workspace_id=p.workspace_id, task_id=task.id, actor_id=p.user_id,
        goal_version=1, goal_hash=task.data['goal_hash'], goal_snapshot_hash=digest(m.snapshot(task)),
        original_query_id=task.data['native_goal_query_id'], source_binding_hash='b' * 64,
        job_id=producer.id, invocation_id=call.id, generation=2, evaluation_query_id=uid(15), record_hash=record['record_hash'],
        data=record)
    state = NS(p=p, task=task, row=row, record=record, producer=producer, call=call, old=old,
        time=AT, locks=[], reads=[], source_checks=[], guards=[], assessment_calls=0, counter=100, flushes=0,
        rows={m.Task: [task], m.Job: [producer], m.Invocation: [call], m.Evaluation: [row], m.Audit: [], m.Memory: []},
        owner={'job_id': producer.id, 'lease_token': producer.lease_token})
    class IO:
        @property
        def no_autoflush(self): return nullcontext()
        def scalars(self, statement):
            model = statement.column_descriptions[0]['entity']
            assert p.workspace_id in statement.compile().params.values()
            return [item for item in state.rows.get(model, []) if predicate(statement.whereclause, item)]
        def scalar(self, statement):
            assert 'clock_timestamp' in str(statement)
            return state.time
        def add(self, item):
            if item.id is None:
                state.counter += 1; item.id = uid(state.counter)
            if item.created_at is None: item.created_at = state.time
            if type(item) is m.Job:
                item.attempts = item.attempts or 0; item.version = item.version or 1
            state.rows.setdefault(type(item), []).append(item)
        def flush(self): state.flushes += 1
        def commit(self): pytest.fail('maturity must use its caller final commit')
    state.db = IO()
    def fresh(db, principal, model, identity, *, lock=False):
        assert db is state.db and principal is p
        state.reads.append((model, identity, lock))
        if lock: state.locks.append((model, identity))
        items = [item for item in state.rows.get(model, []) if item.id == identity and item.workspace_id == p.workspace_id]
        m.need(len(items) == 1); return items[0]
    monkeypatch.setattr(m.provider, '_fresh', fresh)
    def authority(db, principal, actual, kind, *, terminal=False):
        assert db is state.db and principal is p
        m.need(actual.workspace_id == p.workspace_id and actual.kind == kind and actual.data['actor_id'] == p.user_id
            and state.owner == {'job_id': actual.id, 'lease_token': actual.lease_token}
            and actual.status == ('succeeded' if terminal else 'running'))
        return copy.deepcopy(state.owner)
    monkeypatch.setattr(m, '_lease', authority)
    def checked_record(db, principal, actual_task, actual_row, *, complete):
        assert db is state.db and principal is p and actual_task is task and actual_row is row
        state.source_checks.append(complete)
        if complete: m.need(producer.status == call.status == 'succeeded')
        return row, copy.deepcopy(record)
    monkeypatch.setattr(m, '_evaluation', checked_record)
    def guarded(db, principal, actual, **options):
        assert db is state.db and principal is p and actual is task
        state.guards.append(options)
    monkeypatch.setattr(native_goals, 'guard', guarded)
    monkeypatch.setattr(relations, 'lock_current_actor', lambda *args: None)
    monkeypatch.setattr(m.provider, 'binding', lambda *args: {'binding': {'binding_hash': 'b' * 64}})
    monkeypatch.setattr(m.provider, '_head', lambda db, principal, actual: copy.deepcopy(actual.data.get(m.provider.KEY)))
    monkeypatch.setattr(append, '_mature_head', lambda *args: {'head': copy.deepcopy(old)})
    def normal_queue(db, principal, actual, key, **extra):
        assert db is state.db and principal is p and actual is task
        item = m.Job(id=uid(20), workspace_id=p.workspace_id, kind=m.provider.KIND, status='queued', attempts=0,
            idempotency_key='native-goal:' + task.id + ':' + task.data['native_goal_context_hash'] + ':' + key,
            data={'task_id': task.id, 'actor_id': p.user_id, 'context_hash': task.data['native_goal_context_hash'], **extra})
        state.db.add(item)
    monkeypatch.setattr(native_goals, 'queue', normal_queue)
    def assessment(db, principal, actual, *, lock_sources):
        assert db is state.db and principal is p and actual is task and lock_sources is False
        state.assessment_calls += 1
        evidence = m.assessment_evidence(db, principal, actual, lock_sources=False)
        return evidence, {'completed': False, 'ready_for_acceptance': False, 'basis_hash': digest(evidence)}
    monkeypatch.setattr(native_goals, 'assessment', assessment)
    return state


def candidate(s):
    job = m.queue_candidate(s.db, s.p, s.task, s.row, s.producer)
    s.producer.status = s.call.status = 'succeeded'; s.producer.lease_until = None
    job.status = 'running'; job.attempts = 1; job.lease_token = uid(21); job.lease_until = AT + timedelta(minutes=3)
    s.owner = {'job_id': job.id, 'lease_token': job.lease_token}
    return job


def outbox(s, job):
    assert m.process_candidate(s.db, s.p, s.task, job)
    job.status = 'succeeded'; job.lease_until = None
    before = s.flushes; reads = len(s.reads); locks = len(s.locks)
    assert m.enqueue_promotion_after_success(s.db, s.p, job)
    assert s.flushes == before
    assert len(s.locks) == locks and all(model is not m.Job for model, _, _ in s.reads[reads:])
    return next(item for item in s.rows[m.Job] if item.kind == m.KIND)


def promote(s, promoter):
    promoter.status = 'running'; promoter.lease_token = uid(22); promoter.lease_until = s.time + timedelta(minutes=3)
    s.owner = {'job_id': promoter.id, 'lease_token': promoter.lease_token}; s.locks.clear()
    assert m.process_promotion(s.db, s.p, promoter) == 'succeeded'
    assert s.locks[:3] == [(m.Task, s.task.id), (m.Job, uid(20)), (m.Job, promoter.id)]
    # Explicit ordinary-worker terminal boundary, not a claim of PG commit.
    promoter.status = 'succeeded'; promoter.lease_until = None
    promoter.data = {**promoter.data, 'finished_at': s.time.isoformat()}


def test_real_memory_insufficient_candidate_keeps_old_head_until_successful_promotion(scene):
    s = scene; old = copy.deepcopy(s.task.data); job = candidate(s)
    assert m.assessment_evidence(s.db, s.p, s.task) is None
    assert m.process_candidate(s.db, s.p, s.task, job)
    assert s.task.data == old and len(s.rows[m.Memory]) == 1 and job.data['goal_check_id'] != old['native_goal_check_id']
    memory = s.rows[m.Memory][0]
    assert memory.data['report']['evidence'][m.provider.VIEW]['evaluation_ref']['id'] == s.row.id
    assert memory.data['report']['evaluation']['completed'] is False
    assert m.assessment_evidence(s.db, s.p, s.task) is None
    assert s.assessment_calls == 1
    promoter = outbox(s, job)  # Existing prepared result is read, never reassessed.
    assert s.assessment_calls == 1 and s.task.data == old
    s.time += timedelta(hours=2)  # Maturity is historical completion, not freshness renewal.
    promote(s, promoter)
    head = s.task.data[m.provider.KEY]
    assert head['evaluation_ref']['id'] == s.row.id and head['previous_hash'] == s.old['head_hash']
    assert head['head_hash'] == memory.data['report']['goal_evaluation_head_hash_v288']
    assert s.task.data['native_goal_check_id'] == memory.id
    assert m.verify_head(s.db, s.p, s.task, head)
    assert not any(item.kind == 'dispatch_acquisition' for item in s.rows[m.Job])
    assert len(s.rows[m.Invocation]) == 1 and s.record['cut_at'] == AT.isoformat()


def test_same_original_producer_queue_reuses_the_real_candidate_and_independent_audit(scene):
    s = scene; job = m.queue_candidate(s.db, s.p, s.task, s.row, s.producer)
    before = (len(s.rows[m.Job]), len(s.rows[m.Audit]))
    assert m.queue_candidate(s.db, s.p, s.task, s.row, s.producer) is job
    assert (len(s.rows[m.Job]), len(s.rows[m.Audit])) == before
    assert s.task.data[m.provider.KEY] == s.old


@pytest.mark.parametrize('damage_original', [False, True])
def test_reclaimed_producer_current_lease_is_distinct_from_the_original_sdk_creator(scene, damage_original):
    s = scene; original = s.record['producer']['lease_token']
    s.producer.lease_token = uid(70)
    s.owner = {'job_id': s.producer.id, 'lease_token': s.producer.lease_token}
    if damage_original: s.call.data['lease_token'] = uid(71)
    if damage_original:
        with pytest.raises(HTTPException): m.queue_candidate(s.db, s.p, s.task, s.row, s.producer)
    else:
        assert m.queue_candidate(s.db, s.p, s.task, s.row, s.producer).status == 'queued'
        assert s.record['producer']['lease_token'] == s.call.data['lease_token'] == original
        assert s.producer.lease_token != original


@pytest.mark.parametrize('fault', ['producer_running', 'producer_failed', 'sdk_unknown'])
def test_no_candidate_assessment_before_real_producer_and_sdk_success(scene, fault):
    s = scene; job = candidate(s)
    if fault == 'producer_running': s.producer.status = 'running'
    elif fault == 'producer_failed': s.producer.status = 'failed'
    else: s.call.status = 'unknown'
    with pytest.raises(HTTPException): m.process_candidate(s.db, s.p, s.task, job)
    assert not s.rows[m.Memory] and s.task.data[m.provider.KEY] == s.old


@pytest.mark.parametrize('fault', ['deleted_marker', 'rehash_marker', 'foreign_goal', 'missing_queue_audit'])
def test_queue_scope_cannot_be_faked_or_downgraded_to_legacy(scene, fault):
    s = scene; job = candidate(s)
    if fault == 'deleted_marker': job.data.pop(m.KEY)
    elif fault == 'rehash_marker':
        marker = copy.deepcopy(job.data[m.KEY]); marker['producer_hash'] = 'f' * 64
        job.data[m.KEY] = m._seal({key: value for key, value in marker.items() if key != 'record_hash'})
    elif fault == 'foreign_goal': s.row.goal_version = 2
    else: s.rows[m.Audit].clear()
    assert m.handles(s.db, s.p, s.task, job)
    with pytest.raises(HTTPException): m.process_candidate(s.db, s.p, s.task, job)
    assert not s.rows[m.Memory] and s.task.data[m.provider.KEY] == s.old


def test_client_dictionary_cannot_activate_private_candidate_projection(scene):
    s = scene; token = m._ASSESSMENT.set({'schema': m.SCHEMA, 'evaluation_ref': m.provider._reference(s.row)})
    try:
        with pytest.raises(HTTPException): m.assessment_evidence(s.db, s.p, s.task)
    finally: m._ASSESSMENT.reset(token)
    assert m.assessment_evidence(s.db, s.p, s.task) is None


def test_assessment_exception_resets_private_projection_and_preserves_mature_head(scene, monkeypatch):
    s = scene; job = candidate(s)
    def failed(*args, **kwargs):
        assert m.assessment_evidence(s.db, s.p, s.task) is not None
        raise HTTPException(409, 'explicit composite assessment IO refusal')
    monkeypatch.setattr(native_goals, 'assessment', failed)
    with pytest.raises(HTTPException): m.process_candidate(s.db, s.p, s.task, job)
    assert m.assessment_evidence(s.db, s.p, s.task) is None and s.task.data[m.provider.KEY] == s.old
    assert not s.rows[m.Memory]


@pytest.mark.parametrize('fault', ['missing_prepared', 'changed_memory', 'changed_claim'])
def test_success_hook_does_not_treat_missing_or_self_rehashed_report_as_maturity(scene, fault):
    s = scene; job = candidate(s); assert m.process_candidate(s.db, s.p, s.task, job)
    job.status = 'succeeded'; job.lease_until = None
    if fault == 'missing_prepared': s.rows[m.Audit] = [a for a in s.rows[m.Audit] if a.action != m.PREPARED]
    elif fault == 'changed_claim': job.data[m.PREPARED_KEY]['record_hash'] = 'f' * 64
    else:
        memory = s.rows[m.Memory][0]; memory.data['report']['evaluation']['completed'] = True
        memory.data['report_hash'] = digest(memory.data['report'])
    before = s.flushes
    with pytest.raises(HTTPException): m.enqueue_promotion_after_success(s.db, s.p, job)
    assert s.flushes == before and not any(item.kind == m.KIND for item in s.rows[m.Job])
    assert s.task.data[m.provider.KEY] == s.old


@pytest.mark.parametrize('fault', ['goal_job_running', 'goal_job_failed', 'missing_outbox', 'outbox_drift'])
def test_promoter_requires_actual_goal_terminal_and_independent_outbox(scene, fault):
    s = scene; job = candidate(s); promoter = outbox(s, job)
    if fault == 'goal_job_running': job.status = 'running'
    elif fault == 'goal_job_failed': job.status = 'failed'
    elif fault == 'missing_outbox': s.rows[m.Audit] = [a for a in s.rows[m.Audit] if a.action != m.OUTBOX]
    else: promoter.data['report_hash'] = 'f' * 64
    with pytest.raises(HTTPException): promote(s, promoter)
    assert s.task.data[m.provider.KEY] == s.old and not any(a.action == m.MATURED for a in s.rows[m.Audit])


def test_lost_compare_and_set_preserves_newer_head_and_records_superseded(scene):
    s = scene; job = candidate(s); promoter = outbox(s, job)
    later = copy.deepcopy(s.old); later['generation'] = 3; later['head_hash'] = 'f' * 64
    s.task.data[m.provider.KEY] = copy.deepcopy(later)
    promote(s, promoter)
    assert s.task.data[m.provider.KEY] == later
    assert any(a.action == m.SUPERSEDED for a in s.rows[m.Audit])
    assert not any(a.action in {m.MATURED, m.provider.SELECTED} for a in s.rows[m.Audit])


@pytest.mark.parametrize('fault', ['promoter_running', 'missing_matured', 'report_drift'])
def test_cold_head_requires_real_publication_job_and_original_report(scene, fault):
    s = scene; job = candidate(s); promoter = outbox(s, job); promote(s, promoter)
    if fault == 'promoter_running': promoter.status = 'running'
    elif fault == 'missing_matured': s.rows[m.Audit] = [a for a in s.rows[m.Audit] if a.action != m.MATURED]
    else: s.rows[m.Memory][0].data['report']['evaluation']['completed'] = True
    with pytest.raises(HTTPException): m.verify_head(s.db, s.p, s.task, s.task.data[m.provider.KEY])


def test_publication_receipt_displays_a_real_published_result_without_reopening_source_proof(scene, monkeypatch):
    s = scene; job = candidate(s); promoter = outbox(s, job); promote(s, promoter)
    head = copy.deepcopy(s.task.data[m.provider.KEY]); before = copy.deepcopy(s.task.data)
    counts = {model: len(rows) for model, rows in s.rows.items()}
    source_checks = list(s.source_checks); assessment_calls = s.assessment_calls
    s.time += timedelta(hours=2)  # An expired original sample is still a real historical publication.
    monkeypatch.setattr(m, '_evaluation', lambda *args, **kwargs: pytest.fail('Display receipt reopened raw source proof'))
    monkeypatch.setattr(m.provider, '_record', lambda *args, **kwargs: pytest.fail('Display receipt read old source facts'))
    monkeypatch.setattr(m.provider, '_head', lambda *args: pytest.fail('Display receipt selected or authenticated the live head'))
    receipt = m.publication_receipt(s.db, s.p, s.task, head)
    assert receipt['row'] is s.row and receipt['record'] == s.record and receipt['head'] == head
    assert receipt['candidate']['base_head'] == s.old and receipt['goal_check_job'] is job
    assert receipt['promoter_job'] is promoter and receipt['memory'].id == job.data['goal_check_id']
    assert receipt['report_hash'] == receipt['memory'].data['report_hash']
    assert receipt['memory'].data['report']['evaluation']['completed'] is False
    assert {receipt[key].action for key in ('prepared_audit', 'outbox_audit', 'matured_audit', 'selected_audit')} == {
        m.PREPARED, m.OUTBOX, m.MATURED, m.provider.SELECTED}
    receipt['head']['evaluation_ref']['id'] = uid(90)
    receipt['record']['producer']['job_id'] = uid(91)
    receipt['candidate']['base_head']['head_hash'] = 'f' * 64
    again = m.publication_receipt(s.db, s.p, s.task, head)
    assert again['head'] == head and again['record'] == s.record and again['candidate']['base_head'] == s.old
    assert s.task.data == before and {model: len(rows) for model, rows in s.rows.items()} == counts
    assert s.source_checks == source_checks and s.assessment_calls == assessment_calls


@pytest.mark.parametrize('fault', ['missing_queued', 'missing_prepared', 'missing_outbox', 'missing_matured',
    'missing_selected', 'goal_running', 'goal_unsettled_lease', 'promoter_running', 'promoter_unsettled_lease',
    'report_drift', 'record_drift', 'record_rehashed', 'wrong_evaluation', 'head_hash_drift'])
def test_display_publication_receipt_requires_each_independent_completed_publication_proof(scene, monkeypatch, fault):
    s = scene; job = candidate(s); promoter = outbox(s, job); promote(s, promoter)
    head = copy.deepcopy(s.task.data[m.provider.KEY])
    missing = {'missing_queued': m.QUEUED, 'missing_prepared': m.PREPARED, 'missing_outbox': m.OUTBOX,
        'missing_matured': m.MATURED, 'missing_selected': m.provider.SELECTED}
    if fault in missing: s.rows[m.Audit] = [item for item in s.rows[m.Audit] if item.action != missing[fault]]
    elif fault == 'goal_running': job.status = 'running'
    elif fault == 'goal_unsettled_lease': job.lease_until = s.time + timedelta(minutes=1)
    elif fault == 'promoter_running': promoter.status = 'running'
    elif fault == 'promoter_unsettled_lease': promoter.lease_until = s.time + timedelta(minutes=1)
    elif fault == 'report_drift':
        memory = s.rows[m.Memory][0]; memory.data['report']['evaluation']['completed'] = True
        memory.data['report_hash'] = digest(memory.data['report'])
    elif fault in {'record_drift', 'record_rehashed'}:
        s.record['unproven_note'] = 'Changed stored evaluation bytes'
        if fault == 'record_rehashed':
            s.record['record_hash'] = evaluation_contract.record_hash(s.record); s.row.record_hash = s.record['record_hash']
    elif fault == 'wrong_evaluation':
        head['evaluation_ref']['id'] = uid(90)
        head['head_hash'] = digest({key: value for key, value in head.items() if key != 'head_hash'})
    else: head['head_hash'] = 'f' * 64
    monkeypatch.setattr(m, '_evaluation', lambda *args, **kwargs: pytest.fail('Invalid publication tried raw sources'))
    monkeypatch.setattr(m.provider, '_record', lambda *args, **kwargs: pytest.fail('Invalid publication read old source facts'))
    with pytest.raises(HTTPException): m.publication_receipt(s.db, s.p, s.task, head)


@pytest.mark.parametrize('field,value', [('schema', 'workflow-goal-evaluation-record-v288'),
    ('producer_family', 'unregistered_producer')])
def test_display_publication_receipt_has_a_closed_new_family_even_with_matching_publication_hashes(scene, field, value):
    s = scene
    # Source validation is the fixture's explicit IO spy; independently require
    # this public display path itself to reject a different stored record family.
    s.record[field] = value; s.record['record_hash'] = evaluation_contract.record_hash(s.record)
    s.row.record_hash = s.record['record_hash']
    job = candidate(s); promoter = outbox(s, job); promote(s, promoter)
    with pytest.raises(HTTPException): m.publication_receipt(s.db, s.p, s.task, s.task.data[m.provider.KEY])


def test_display_publication_receipt_does_not_grant_strong_source_authentication(scene, monkeypatch):
    s = scene; job = candidate(s); promoter = outbox(s, job); promote(s, promoter)
    checks = []
    def refused(db, principal, task, row, *, complete):
        assert db is s.db and principal is s.p and task is s.task and row is s.row
        checks.append(complete); raise HTTPException(409, 'Explicit original source IO refusal')
    monkeypatch.setattr(m, '_evaluation', refused)
    assert m.publication_receipt(s.db, s.p, s.task, s.task.data[m.provider.KEY])['row'] is s.row
    assert checks == []
    with pytest.raises(HTTPException): m.verify_head(s.db, s.p, s.task, s.task.data[m.provider.KEY])
    assert checks == [True]  # Strong authentication still performs its original complete proof.


def test_legacy_verify_head_false_does_not_become_a_display_publication_receipt(scene):
    s = scene
    legacy = m.Job(id=s.old['goal_check_job_id'], workspace_id=s.p.workspace_id, kind=m.provider.KIND,
        status='succeeded', lease_until=None,
        idempotency_key='native-goal:' + s.task.id + ':' + s.task.data['native_goal_context_hash'] + ':initial',
        data={'actor_id': s.p.user_id, 'task_id': s.task.id, 'context_hash': s.task.data['native_goal_context_hash']})
    s.rows[m.Job].append(legacy)
    assert m.verify_head(s.db, s.p, s.task, s.old) is False
    with pytest.raises(HTTPException): m.publication_receipt(s.db, s.p, s.task, s.old)


@pytest.mark.parametrize('wrong_reference', [False, True])
def test_completed_historical_base_remains_authentic_after_a_later_head_without_current_freshness(scene, monkeypatch, wrong_reference):
    s = scene; job = candidate(s); promoter = outbox(s, job); promote(s, promoter)
    head = copy.deepcopy(s.task.data[m.provider.KEY])
    s.task.data[m.provider.KEY] = {**head, 'head_hash': 'f' * 64, 'generation': 3}
    monkeypatch.setattr(m.provider, '_head', lambda *args: pytest.fail('Historical proof tried to select current head'))
    monkeypatch.setattr(m.provider.queries, '_reader', lambda *args: None)  # Explicit current-reader IAM IO.
    def record(db, principal, actual, selected, reference, **kwargs):
        assert reference == m.provider._reference(s.row)
        return s.row, s.record, s.time
    monkeypatch.setattr(m.provider, '_record', record)  # Explicit original Eval producer/raw source IO.
    reference = m.provider._reference(s.row)
    if wrong_reference: reference = {**reference, 'id': uid(80)}
    if wrong_reference:
        with pytest.raises(HTTPException):
            m.read_mature_history(s.db, s.p, s.task, head_hash=head['head_hash'], evaluation_ref=reference)
    else:
        result = m.read_mature_history(s.db, s.p, s.task, head_hash=head['head_hash'], evaluation_ref=reference)
        assert result['head'] == head and result['memory'].id == job.data['goal_check_id']


def test_real_terminal_sql_fence_preserves_pending_orm_success_and_never_refreshes(monkeypatch):
    p = NS(workspace_id=uid(1), user_id=uid(2))
    job = m.Job(id=uid(3), workspace_id=p.workspace_id, kind=m.provider.KIND, status='succeeded',
        lease_token=uid(4), lease_until=None, data={'actor_id': p.user_id})
    class DB:
        active = False
        @property
        def no_autoflush(self):
            from contextlib import contextmanager
            @contextmanager
            def protected():
                self.active = True
                try: yield
                finally: self.active = False
            return protected()
        def scalar(self, statement):
            assert self.active and statement.column_descriptions[0]['expr'] is m.Job.id
            sql = str(statement)
            assert 'clock_timestamp()' in sql and 'lease_token' in sql and 'status' in sql
            params = statement.compile().params
            assert 'running' in params.values() and job.lease_token in params.values()
            assert p.workspace_id in params.values() and p.user_id in params.values()
            return job.id
    db = DB(); monkeypatch.setattr(leases, 'fenced', lambda actual: actual is db)
    monkeypatch.setattr(leases, 'ownership', lambda actual: {'job_id': job.id, 'lease_token': job.lease_token})
    assert m._lease(db, p, job, m.provider.KIND, terminal=True)['job_id'] == job.id
    assert job.status == 'succeeded' and job.lease_until is None


def test_automatic_revocation_blocks_candidate_and_keeps_original_head(scene,monkeypatch):
    from generic_agents import auto_evaluation_trigger_v356 as auto_trigger
    s=scene;job=candidate(s);before=copy.deepcopy(s.task.data)
    monkeypatch.setattr(auto_trigger,'authority',lambda *args:(_ for _ in ()).throw(HTTPException(409,'策略撤销')))
    with pytest.raises(HTTPException):m.process_candidate(s.db,s.p,s.task,job)
    assert s.task.data==before and s.assessment_calls==0 and not s.rows.get(m.Memory,[])


def test_automatic_revocation_blocks_promotion_after_candidate(scene,monkeypatch):
    from generic_agents import auto_evaluation_trigger_v356 as auto_trigger
    s=scene;job=candidate(s);promoter=outbox(s,job);before=copy.deepcopy(s.task.data)
    promoter.status='running';promoter.lease_token=uid(22);promoter.lease_until=s.time+timedelta(minutes=3)
    s.owner={'job_id':promoter.id,'lease_token':promoter.lease_token}
    monkeypatch.setattr(auto_trigger,'authority',lambda *args:(_ for _ in ()).throw(HTTPException(409,'策略撤销')))
    with pytest.raises(HTTPException):m.process_promotion(s.db,s.p,promoter)
    assert s.task.data==before and not [x for x in s.rows[m.Audit] if x.action==m.MATURED]


def test_manual_producer_is_checked_at_candidate_and_promotion_without_changing_flow(scene,monkeypatch):
    from generic_agents import auto_evaluation_trigger_v356 as auto_trigger
    s=scene;calls=[]
    monkeypatch.setattr(auto_trigger,'authority',lambda db,p,task,producer:calls.append((task.id,producer.id)))
    job=candidate(s);promoter=outbox(s,job);promote(s,promoter)
    assert calls==[(s.task.id,s.producer.id),(s.task.id,s.producer.id)]
