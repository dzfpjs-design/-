"""Original criteria and cold-provider logic with explicit synthetic IO seams.

No PG, adopted creator, current IAM, lease, receiver or packet completion is
proved here. The actual packet.saved_result is an explicit IO boundary. Root's
runtime/PG checks must independently authenticate those records and actors.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import goals, native_goals, leases
from generic_agents import goal_evaluation_provider_v288 as subject
from generic_agents import workflow_goal_evaluation_packet_v288 as packets
from generic_agents import workflow_goal_evaluation_models_v288 as models
from generic_agents import goal_evaluation_maturity_v299 as maturity
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, DataRun, Invocation, Job, Memory, Task
from generic_agents.temporal import instant
from tests.test_workflow_goal_evaluation_contracts_v288 import scene, uid, CHECKED

REAL_BINDING = subject.binding


class IO:
    def __init__(self):
        self.now = instant(CHECKED); self.rows = {}; self.audits = []; self.memories = []
        self.flushes = 0; self.selection_job = None

    def scalar(self, statement):
        entity = statement.column_descriptions[0].get('entity')
        if entity is Job:
            assert self.selection_job is not None
            return self.selection_job
        assert 'clock_timestamp()' in str(statement)
        return self.now

    def scalars(self, statement):
        assert statement.column_descriptions[0].get('entity') is Audit
        actions = {value for key, value in statement.compile().params.items()
            if key.startswith('action_') and type(value) is str}
        if actions & {maturity.QUEUED, maturity.MATURED}:
            # Explicit IO absence, not a replacement of _head/verify_head:
            # this fixture's audit inventory contains original SELECTED only.
            # A genuine new candidate marker/key must never use this boundary.
            assert all(row.data.get('schema') == subject.HEAD for row in self.audits)
            for (model, _identity), row in self.rows.items():
                if model is Job:
                    assert maturity.KEY not in row.data
                    assert ':candidate-v299:' not in row.idempotency_key
            return []
        return list(self.audits)

    def add(self, row):
        assert type(row) is Memory, 'No query, model, event, observation or acceptance write'
        row.id = uid(500 + len(self.memories)); self.memories.append(row)
        self.rows[Memory, row.id] = row

    def flush(self): self.flushes += 1
    def commit(self): pytest.fail('provider owns no transaction boundary')


@pytest.fixture
def sources(monkeypatch):
    _unsigned, record, _kwargs, _q0 = scene(acquisition=False)
    p = NS(workspace_id=uid(1), user_id=uid(7), role='operator')
    frozen = record['goal_snapshot']
    task = NS(id=uid(2), workspace_id=p.workspace_id, package_id=uid(5), version=1,
        status='workflow_waiting', data={'goal_confirmed_by': p.user_id,
            'goal_spec': copy.deepcopy(frozen['spec']), 'goal_hash': frozen['hash'], 'goal_version': frozen['version'],
            'goal_window': copy.deepcopy(frozen['time']), 'native_goal_query_id': uid(10),
            'native_goal_context_hash': record['scope']['native_goal_context_hash'],
            'instance_id': uid(3), 'instance_release_id': uid(4), 'business_id': record['scope']['business_id'],
            'entity_ids': list(frozen['spec']['entity_ids']), 'fields': list(frozen['spec']['required_fields']),
            'workflow_runtime': {'receipts': [], 'current_step_id': 'first'}})
    original_report = copy.deepcopy(record['evaluation_result']['result'])
    original_report.update(status='insufficient', observation_evidence=[])
    original = NS(id=uid(10), workspace_id=p.workspace_id, version=2, data={
        'native_goal_task_id': task.id, 'native_goal_context_hash': task.data['native_goal_context_hash'],
        'request': copy.deepcopy(_q0['request']), 'result': {'status': 'insufficient', 'result': original_report},
        'goal_delegation': {'task_id': task.id, 'goal_snapshot': goals.snapshot(task)}})
    original.data['native_goal_request_hash'] = digest(original.data['request'])
    original.data['native_goal_result_hash'] = digest(original.data['result'])
    record['original_query_ref'] = subject.queries.query_ref(original)
    record['record_hash'] = subject.contracts.record_hash(record)
    row = NS(**models.row_values(record))
    selected = {'task': task, 'release': NS(id=uid(4), instance_id=uid(3)),
        'binding': {'preparation_policy': record['preparation_policy'],
            'binding_hash': record['scope']['source_binding_hash'], 'graph': {'fixture': True}}}
    # The actual publication/graph proof is a declared IO seam in this fixture.
    row.graph_hash = digest(selected['binding']['graph'])
    record['scope']['graph_hash'] = row.graph_hash
    record['producer']['scope_hash'] = subject.queries.hashed(record['scope'])
    record['record_hash'] = subject.contracts.record_hash(record)
    row.data = record; row.record_hash = record['record_hash']
    db = IO(); job = NS(id=row.job_id, workspace_id=p.workspace_id, status='succeeded',
        idempotency_key=record['request_key'], data={'inputs': {'local_plan': {'synthetic': True}},
            'task_id': task.id, 'actor_id': row.actor_id, 'activation_id': 'evaluation_packet'})
    call = NS(id=row.invocation_id, workspace_id=p.workspace_id, capability_id=record['producer']['capability_id'],
        idempotency_key=job.idempotency_key, status='succeeded',
        data={'job_id': job.id, 'lease_token': record['producer']['lease_token']})
    cap = NS(id=call.capability_id, workspace_id=p.workspace_id)
    for model, value in ((models.GoalEvaluationV288, row), (DataRun, original),
            (Job, job), (Invocation, call), (Capability, cap)):
        db.rows[model, value.id] = value
    fresh_calls, proof_calls = [], []

    def fresh(actual_db, reader, model, identity, *, lock=False):
        assert actual_db is db and reader is p
        fresh_calls.append((model, identity, lock))
        return db.rows[model, identity]

    def packet_proof(actual_db, reader, actual_task, actual_job, actual_call, actual_cap, inputs):
        assert (actual_db, reader, actual_task, actual_job, actual_call, actual_cap) == (db, p, task, job, call, cap)
        assert inputs == job.data['inputs']
        proof_calls.append(True)
        return {'evaluation_ref': subject._reference(row), 'packet': {'id': row.id, 'evidence_id': row.evidence_id}}

    monkeypatch.setattr(subject, '_fresh', fresh)
    monkeypatch.setattr(subject, 'binding', lambda actual_db, reader, actual_task, **kw: selected)
    monkeypatch.setattr(packets, 'saved_result', packet_proof)
    return NS(db=db, p=p, task=task, original=original, row=row, record=record, job=job,
        call=call, cap=cap, selected=selected, proof_calls=proof_calls, fresh_calls=fresh_calls)


def test_cold_record_keeps_original_goal_and_query_and_requires_actual_packet_boundary(sources, monkeypatch):
    s = sources; original = copy.deepcopy(s.original.data); goal = goals.snapshot(s.task)
    row, value, _trusted = subject._record(s.db, s.p, s.task, s.selected, subject._reference(s.row))
    assert row is s.row and value == s.record and s.proof_calls == [True]
    assert s.original.data == original and goals.snapshot(s.task) == goal
    monkeypatch.setattr(packets, 'saved_result', lambda *a: (_ for _ in ()).throw(HTTPException(409, 'actual producer incomplete')))
    with pytest.raises(HTTPException):
        subject._record(s.db, s.p, s.task, s.selected, subject._reference(s.row))


def test_resealed_foreign_business_scope_is_not_authenticated_by_its_own_hash(sources):
    s = sources; s.record['scope']['business_id'] = 'other_business'
    s.record['producer']['scope_hash'] = subject.queries.hashed(s.record['scope'])
    s.record['record_hash'] = subject.contracts.record_hash(s.record); s.row.record_hash = s.record['record_hash']
    with pytest.raises(subject.contracts.GoalEvaluationContractError):
        subject._record(s.db, s.p, s.task, s.selected, subject._reference(s.row))
    assert s.proof_calls == []


def test_waiting_evaluation_is_a_nonmutating_projection_not_a_fresh_query(sources):
    s = sources; before = copy.deepcopy(s.original.data); task = copy.deepcopy(s.task.data)
    result = subject.query_evidence(s.db, s.p, s.task)
    assert result['status'] == 'insufficient'
    assert result[subject.VIEW]['state'] == 'waiting_for_evaluation'
    assert result[subject.VIEW]['evaluation_ref'] is None
    result['gaps'].append({'local': 'unsaved'})
    assert s.original.data == before and s.task.data == task
    assert not s.db.memories and not s.db.audits


def test_expired_rolling_evaluation_never_rolls_the_cut_or_refreshes_records(sources):
    s = sources; frozen = copy.deepcopy(s.record)
    fresh = subject._evidence(s.row, s.record, instant(CHECKED))
    old = subject._evidence(s.row, s.record, instant(CHECKED) + timedelta(minutes=20))
    assert fresh['status'] == 'sufficient' and fresh[subject.VIEW]['state'] == 'current'
    assert old['status'] == 'insufficient' and old[subject.VIEW]['state'] == 'stale'
    assert old['stored_evidence_status'] == 'sufficient'
    assert old['resolved_time'] == fresh['resolved_time'] and s.record == frozen
    plan = NS(id='original-plan')
    assert goals.evaluate(s.task, plan, fresh)['ready_for_acceptance']
    assert not goals.evaluate(s.task, plan, old)['ready_for_acceptance']


def test_native_basis_distinguishes_identical_values_from_different_evaluations(sources):
    s = sources; first = subject._evidence(s.row, s.record, instant(CHECKED)); second = copy.deepcopy(first)
    second[subject.VIEW]['evaluation_ref']['id'] = uid(999)
    assert native_goals.basis(s.task, first) != native_goals.basis(s.task, second)


def test_current_marker_cannot_roll_back_to_an_older_independently_saved_head(sources):
    s = sources
    first = {'schema': subject.HEAD, 'task_id': s.task.id, 'evaluation_ref': subject._reference(s.row),
        'generation': s.row.generation, 'query_id': s.row.evaluation_query_id,
        'goal_snapshot_hash': s.row.goal_snapshot_hash, 'source_binding_hash': s.row.source_binding_hash,
        'goal_check_job_id': uid(700), 'actor_id': s.p.user_id, 'previous_hash': None}
    first['head_hash'] = digest(first)
    second = {**copy.deepcopy(first), 'goal_check_job_id': uid(701), 'previous_hash': first['head_hash']}
    second['head_hash'] = digest({key: value for key, value in second.items() if key != 'head_hash'})
    s.db.audits = [NS(actor_id=s.p.user_id, data=first), NS(actor_id=s.p.user_id, data=second)]
    s.task.data[subject.KEY] = first
    with pytest.raises(HTTPException): subject._head(s.db, s.p, s.task)
    s.db.rows[Job, uid(701)] = NS(id=uid(701), kind=subject.KIND, workspace_id=s.p.workspace_id,
        idempotency_key='native-goal:' + s.task.id + ':' + s.task.data['native_goal_context_hash'] + ':synthetic-check', data={
        'task_id': s.task.id, 'actor_id': s.p.user_id,
        'context_hash': s.task.data['native_goal_context_hash'], subject.JOB_REF: subject._reference(s.row)})
    s.task.data[subject.KEY] = second
    assert subject._head(s.db, s.p, s.task) == second


def test_goal_read_dispatch_does_not_call_the_old_query_or_learning_path(sources, monkeypatch):
    s = sources
    monkeypatch.setattr(goals, 'query_evidence', lambda *a, **kw: pytest.fail('fresh legacy query called'))
    result = goals.query_goal(s.db, s.p, s.task, learning=False, lock_sources=False)
    assert result[subject.VIEW]['state'] == 'waiting_for_evaluation'


@pytest.mark.parametrize('damage', ['running', 'report_hash', 'ref', 'head'])
def test_acceptance_and_analysis_require_the_same_completed_goal_check(sources, monkeypatch, damage):
    s = sources; head = {'evaluation_ref': subject._reference(s.row), 'goal_check_job_id': uid(700), 'head_hash': 'a' * 64,
        'query_id': s.row.evaluation_query_id, 'generation': s.row.generation, 'source_binding_hash': s.row.source_binding_hash}
    check = NS(id=uid(700), status='succeeded', data={'goal_check_id': uid(701)})
    report = {'goal_snapshot': goals.snapshot(s.task), subject.JOB_REF: subject._reference(s.row),
        'goal_evaluation_head_hash_v288': head['head_hash'],
        'evidence': {subject.VIEW: {'evaluation_ref': subject._reference(s.row)}}}
    memory = NS(id=uid(701), status='candidate', data={'type': 'native_goal_check', 'task_id': s.task.id,
        'report': report, 'report_hash': digest(report)})
    s.db.rows[Job, check.id] = check; s.db.rows[Memory, memory.id] = memory
    monkeypatch.setattr(subject, '_head', lambda *a: copy.deepcopy(head))
    assert subject.require_current(s.db, s.p, s.task)['goal_check_id'] == memory.id
    if damage == 'running': check.status = 'running'
    elif damage == 'report_hash': memory.data['report_hash'] = '0' * 64
    elif damage == 'ref': report[subject.JOB_REF] = {'id': uid(999), 'record_hash': s.row.record_hash}; memory.data['report_hash'] = digest(report)
    else: report['goal_evaluation_head_hash_v288'] = '0' * 64; memory.data['report_hash'] = digest(report)
    with pytest.raises(HTTPException): subject.require_current(s.db, s.p, s.task)


def test_explicit_goal_job_waits_for_producer_without_creating_a_check(sources):
    s = sources; waiting = NS(id=uid(700), kind=subject.KIND, workspace_id=s.p.workspace_id,
        idempotency_key='native-goal:' + s.task.id + ':' + s.task.data['native_goal_context_hash'] + ':synthetic-check',
        data={'task_id': s.task.id, 'actor_id': s.p.user_id, 'context_hash': s.task.data['native_goal_context_hash'],
            subject.JOB_REF: subject._reference(s.row), subject.JOB_PRODUCER: s.job.id})
    s.db.rows[Task, s.task.id] = s.task
    s.db.rows[Job, waiting.id] = waiting
    s.job.status = 'running'
    assert subject.job_ready(s.db, s.p, waiting) is False
    assert s.proof_calls == [] and not s.db.memories
    s.job.status = 'succeeded'
    assert subject.job_ready(s.db, s.p, waiting) is True
    assert s.proof_calls == [True]


def test_actual_append_schema_cannot_downgrade_to_legacy_when_candidate_witnesses_are_missing(sources):
    s = sources
    # This is an adversarial protocol-classification fixture, not a successful
    # append producer. Real verify_head sees no candidate marker/key/MATURED
    # audit and returns its legacy-compatible False; _head must reject that
    # result for an actual V299 row before adopting any source values.
    s.row.data = {**copy.deepcopy(s.record), 'schema': 'workflow-goal-evaluation-record-v299',
        'producer_family': 'append_command_v297'}
    head = {'schema': subject.HEAD, 'task_id': s.task.id, 'evaluation_ref': subject._reference(s.row),
        'generation': s.row.generation, 'query_id': s.row.evaluation_query_id,
        'goal_snapshot_hash': s.row.goal_snapshot_hash, 'source_binding_hash': s.row.source_binding_hash,
        'goal_check_job_id': uid(700), 'actor_id': s.p.user_id, 'previous_hash': None}
    head['head_hash'] = digest(head)
    s.db.audits = [NS(actor_id=s.p.user_id, data=copy.deepcopy(head))]
    s.db.rows[Job, uid(700)] = NS(id=uid(700), kind=subject.KIND, workspace_id=s.p.workspace_id,
        idempotency_key='native-goal:' + s.task.id + ':' + s.task.data['native_goal_context_hash'] + ':synthetic-check',
        data={'task_id': s.task.id, 'actor_id': s.p.user_id,
            'context_hash': s.task.data['native_goal_context_hash'], subject.JOB_REF: subject._reference(s.row)})
    s.task.data[subject.KEY] = copy.deepcopy(head)
    assert maturity.verify_head(s.db, s.p, s.task, head) is False
    with pytest.raises(HTTPException): subject._head(s.db, s.p, s.task)
    assert s.task.data[subject.KEY] == head and not s.db.memories and not s.proof_calls


def test_only_fenced_original_goal_job_can_select_an_evaluation(sources, monkeypatch):
    s = sources; target = NS(id=uid(700), kind=subject.KIND, workspace_id=s.p.workspace_id,
        status='running', idempotency_key='native-goal:' + s.task.id + ':' + s.task.data['native_goal_context_hash'] + ':synthetic-check', data={
            'task_id': s.task.id, 'actor_id': s.p.user_id, 'context_hash': s.task.data['native_goal_context_hash'],
            subject.JOB_REF: subject._reference(s.row), subject.JOB_PRODUCER: s.job.id})
    s.db.rows[Task, s.task.id] = s.task; s.db.rows[Job, target.id] = target; s.db.selection_job = target
    # Actor, lease, and the rest of native assessment are explicit boundaries.
    monkeypatch.setattr(leases, 'fenced', lambda db: False)
    monkeypatch.setattr(leases, 'ownership', lambda db: {'job_id': target.id, 'lease_token': uid(777)})
    with pytest.raises(HTTPException): subject.process_check(s.db, s.p, s.task, target)
    assert subject.KEY not in s.task.data and not s.db.audits and not s.db.memories


def test_goal_job_selects_saved_evaluation_and_original_criteria_without_refreshing_q0(sources, monkeypatch):
    from generic_agents import native_goal_time
    s = sources; original = copy.deepcopy(s.original.data); goal = goals.snapshot(s.task)
    target = NS(id=uid(700), kind=subject.KIND, workspace_id=s.p.workspace_id,
        status='running', idempotency_key='native-goal:' + s.task.id + ':' + s.task.data['native_goal_context_hash'] + ':synthetic-check', data={
            'task_id': s.task.id, 'actor_id': s.p.user_id, 'context_hash': s.task.data['native_goal_context_hash'],
            subject.JOB_REF: subject._reference(s.row), subject.JOB_PRODUCER: s.job.id})
    s.db.rows[Task, s.task.id] = s.task; s.db.rows[Job, target.id] = target; s.db.selection_job = target
    monkeypatch.setattr(leases, 'fenced', lambda db: True)
    monkeypatch.setattr(leases, 'ownership', lambda db: {'job_id': target.id, 'lease_token': uid(777)})
    monkeypatch.setattr(subject, 'audit', lambda db, p, action, identity, value:
        s.db.audits.append(NS(actor_id=p.user_id, data=copy.deepcopy(value))))
    monkeypatch.setattr(native_goal_time, 'arm', lambda *a, **kw: None)

    def original_criterion_evaluation(db, p, task):
        evidence = goals.query_goal(db, p, task, learning=False, lock_sources=False)
        result = goals.evaluate(task, NS(id='native-flow:' + task.id), evidence)
        return evidence, {**result, 'basis_hash': native_goals.basis(task, evidence)}

    monkeypatch.setattr(native_goals, 'assessment', original_criterion_evaluation)
    assert subject.process_check(s.db, s.p, s.task, target) is True
    assert len(s.db.memories) == len(s.db.audits) == 1
    assert s.task.data[subject.KEY]['evaluation_ref'] == subject._reference(s.row)
    report = s.db.memories[0].data['report']
    assert report['evaluation']['checks'] == goals.evaluate(s.task, NS(id='original'), report['evidence'])['checks']
    assert report['evaluation']['completed'] is False and 'native_goal_acceptance' not in s.task.data
    assert s.original.data == original and goals.snapshot(s.task) == goal
    with pytest.raises(HTTPException): subject.require_current(s.db, s.p, s.task)
    target.status = 'succeeded'
    assert subject.require_current(s.db, s.p, s.task)['goal_check_id'] == s.db.memories[0].id


def test_new_definition_defaults_china_without_mutating_input_and_freezes_policy(sources):
    s = sources; spec = copy.deepcopy(s.task.data['goal_spec']); spec['evidence']['time']['timezone'] = None
    result = subject.definition_spec(s.db, s.p, s.task, spec)
    assert result['evidence']['time']['timezone'] == 'Asia/Shanghai'
    assert spec['evidence']['time']['timezone'] is None
    pin = subject.definition_pin(s.db, s.p, s.task)
    assert pin[subject.PIN]['source_binding_hash'] == s.selected['binding']['binding_hash']
    assert pin[subject.PIN]['preparation_policy'] == s.record['preparation_policy']


@pytest.mark.parametrize('part', ['relation_query', 'data_query', 'tool_steps'])
def test_new_recipe_explicitly_refuses_unsupported_full_goal_instead_of_running_a_legacy_path(sources, part):
    s = sources; goal = copy.deepcopy(s.task.data['goal_spec']); goal[part] = [{'full': 'original requirement'}]
    with pytest.raises(HTTPException) as failure: subject.definition_spec(s.db, s.p, s.task, goal)
    assert failure.value.status_code == 422 and failure.value.detail['unsupported_goal_parts'] == [part]
    assert goal[part] == [{'full': 'original requirement'}]


def test_ordinary_task_is_untouched_but_a_marker_cannot_install_or_downgrade_this_family():
    task = NS(data={})
    assert REAL_BINDING(None, None, task) is None
    task.data['native_goal_context'] = {subject.PIN: {'self_hash': 'not authority'}}
    with pytest.raises(HTTPException): REAL_BINDING(None, None, task)


def test_goal_kind_and_context_marker_do_not_replace_the_original_queue_identity(sources):
    s = sources; job = NS(kind=subject.KIND, workspace_id=s.p.workspace_id, idempotency_key='invented-key',
        data={'task_id': s.task.id, 'context_hash': s.task.data['native_goal_context_hash']})
    with pytest.raises(HTTPException): subject._goal_job(s.task, job)
