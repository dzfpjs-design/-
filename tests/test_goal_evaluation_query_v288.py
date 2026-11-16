"""Synthetic SQL/IAM/creator/receipt boundaries; real query engine and cuts.

Root executes these tests. They are not real PG, IAM, ACK, SDK, provider or
adopted-family proof. The actual creator/binding gate is explicitly replaced here.
"""
import copy
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from generic_agents import agents, data_requirements, data_tools, instance_data_receipts, leases
from generic_agents import state_receipt_guard_v245, services
from generic_agents import goal_evaluation_query_v288 as subject
from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.temporal import instant, resolve
from tests.test_workflow_goal_evaluation_contracts_v288 import goal_spec, uid, INITIAL, CUT, CHECKED


def same_sql(left, right):
    return str(left) == str(right) and left.compile().params == right.compile().params


class Rows(list):
    def all(self): return list(self)


def parent_data(s):
    request = {'entity_ids': s.goal['entity_ids'], 'fields': s.goal['required_fields'],
        'mode': 'structured', 'model_profile_id': None, 'tool_steps': [], 'allow_external_tools': False,
        'relation_query': None, 'evidence_requirement': copy.deepcopy(s.goal['evidence'])}
    result = {'status': 'insufficient'}
    return {'request': request, 'result': result, 'native_goal_task_id': s.task.id,
        'native_goal_context_hash': s.task.data['native_goal_context_hash'],
        'native_goal_request_hash': digest(request), 'native_goal_result_hash': digest(result),
        'goal_delegation': {'task_id': s.task.id, 'goal_snapshot': subject.snapshot(s.task)}}


class QueryDB:
    """An explicit query whitelist; no other table or write can be hidden."""
    def __init__(self, scene):
        self.scene = scene; self.rows = {}; self.events = []; self.additions = []; self.reads = 0
        self.info = {'worker_lease': {'job_id': scene.job.id, 'lease_token': scene.token}}

    @property
    def no_autoflush(self): return nullcontext()

    def scalar(self, statement):
        s = self.scene; self.reads += 1
        if same_sql(statement, select(func.clock_timestamp())): return s.trusted
        for principal in (s.p, s.reader):
            query = select(m.User.id).join(m.Membership, m.Membership.user_id == m.User.id).where(
                m.User.id == principal.user_id, m.User.active.is_(True),
                m.Membership.workspace_id == principal.workspace_id, m.Membership.role == principal.role,
                m.Membership.role.in_(['admin', 'operator', 'viewer']))
            if same_sql(statement, query): return principal.user_id if s.read_allowed else None
        unclocked = select(func.count()).select_from(m.Observation).where(
            m.Observation.workspace_id == s.p.workspace_id, m.Observation.entity_id == s.entity.id,
            m.Observation.status == 'extracted_unclocked')
        if same_sql(statement, unclocked): return 0
        for (model, identity), row in self.rows.items():
            query = select(model).where(model.workspace_id == s.p.workspace_id, model.id == identity)
            if same_sql(statement, query): return row
            query = select(model).where(model.id == identity, model.workspace_id == s.p.workspace_id)
            if same_sql(statement, query): return row
        raise AssertionError('Unknown query scalar SQL')

    def scalars(self, statement):
        s = self.scene; self.reads += 1
        query = select(m.DataRun).where(m.DataRun.workspace_id == s.p.workspace_id,
            m.DataRun.data[subject.KEY]['evaluation_id'].as_string() == s.evaluation_id).limit(2)
        if same_sql(statement, query):
            return Rows(row for (model, _key), row in self.rows.items()
                if model is m.DataRun and row.data.get(subject.KEY, {}).get('evaluation_id') == s.evaluation_id)
        query = select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action == subject.SAVED, m.Audit.data['evaluation_id'].as_string() == s.evaluation_id).limit(2)
        if same_sql(statement, query): return Rows(self.events)
        # Missing-audit negatives must still execute the actual empty SELECT.
        # Selecting from the event list itself lost the allowed SQL shape as
        # soon as that list was deliberately cleared.
        for (model, _identity), row in self.rows.items():
            if model is not m.DataRun:
                continue
            query = select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
                m.Audit.action == subject.SAVED, m.Audit.object_id == row.id).limit(2)
            if same_sql(statement, query): return Rows(item for item in self.events if item.object_id == row.id)
        for memory in s.memories:
            query = select(m.Memory).where(m.Memory.workspace_id == s.p.workspace_id,
                m.Memory.data['event_id'].as_string() == memory.data['event_id'],
                m.Memory.data['type'].as_string() == 'observation_evidence').limit(2)
            if same_sql(statement, query): return Rows([memory])
        window = resolve(s.goal['evidence']['time'], reference=s.expected_at)
        query = select(m.Observation).where(m.Observation.workspace_id == s.p.workspace_id,
            m.Observation.entity_id == s.entity.id, m.Observation.status == 'processed',
            m.Observation.observed_at <= s.expected_at,
            m.Observation.observed_at >= instant(window['start']))
        query = query.where(m.Observation.observed_at <= instant(window['end']) if window['mode'] == 'latest'
            else m.Observation.observed_at < instant(window['end']))
        query = query.order_by(m.Observation.observed_at.desc(), m.Observation.id).limit(21)
        if same_sql(statement, query):
            return Rows(row for row in s.observations
                if instant(window['start']) <= row.observed_at
                and (row.observed_at <= instant(window['end']) if window['mode'] == 'latest' else row.observed_at < instant(window['end']))
                and row.observed_at <= s.expected_at)
        raise AssertionError('Unknown query inventory SQL')

    def add(self, row):
        assert type(row) in {m.DataRun, m.Audit}, 'No Job, Invocation, gap, receipt or Goal write'
        row.id = uid(100 + len(self.additions)); self.additions.append(type(row))
        if type(row) is m.DataRun: self.rows[m.DataRun, row.id] = row
        else: self.events.append(row)

    def flush(self): pass
    def commit(self): pytest.fail('query wrapper owns no transaction boundary')


@pytest.fixture
def scene(monkeypatch):
    goal = goal_spec(); goal['evidence']['minimum_verification'] = 'unverified_extraction'
    p = NS(user_id=uid(7), workspace_id=uid(1), role='operator')
    task = NS(id=uid(2), workspace_id=p.workspace_id, data={
        'goal_confirmed_by': p.user_id, 'goal_spec': goal, 'goal_hash': digest(goal), 'goal_version': 1,
        'goal_window': resolve(goal['evidence']['time'], reference=instant(INITIAL)), 'native_goal_query_id': uid(10),
        'native_goal_context_hash': digest({'synthetic': 'original context'})})
    job = NS(id=uid(8)); token = uid(20)
    s = NS(p=p, reader=NS(user_id=uid(9), workspace_id=p.workspace_id, role='viewer'), task=task, job=job,
        evaluation_id=uid(90), token=token, goal=goal, observations=[], memories=[], receipts={},
        trusted=instant(CHECKED), expected_at=instant(CUT), now=instant(CUT) + timedelta(hours=1),
        read_allowed=True, creator_calls=0, clock=None)
    s.policy = subject.contract.make_preparation_policy(goal)
    s.db = QueryDB(s)
    s.parent = m.DataRun(id=uid(10), workspace_id=p.workspace_id, owner_id=p.user_id, version=2,
        question='原查询', status='insufficient', data=parent_data(s))
    s.entity = m.Entity(id=goal['entity_ids'][0], workspace_id=p.workspace_id,
        schema_capability_id=uid(5), created_at=instant(INITIAL), data={})
    s.db.rows[m.DataRun, s.parent.id] = s.parent; s.db.rows[m.Entity, s.entity.id] = s.entity
    for index in range(2):
        received = instant(CUT) - timedelta(seconds=20)
        receipt = {'schema': 'synthetic-original-receipt', 'event_id': uid(30 + index), 'receipt_hash': str(index + 1) * 64}
        observation = m.Observation(id=uid(40 + index), workspace_id=p.workspace_id, entity_id=s.entity.id,
            source_id=uid(50 + index), status='processed', version=2, created_at=received - timedelta(seconds=10),
            observed_at=received - timedelta(seconds=20), data={'extracted': {'attributes': {'temperature': index + 21}}})
        event = m.Event(id=receipt['event_id'], workspace_id=p.workspace_id, kind='observation.data',
            idempotency_key='data:' + observation.id, status='received', received_at=received,
            created_at=received - timedelta(seconds=5), data={'data_reception': receipt})
        memory = m.Memory(id=uid(60 + index), workspace_id=p.workspace_id, status='candidate', created_at=received,
            data={'event_id': event.id, 'type': 'observation_evidence', 'data_reception': receipt})
        s.observations.append(observation); s.memories.append(memory)
        s.db.rows[m.Event, event.id] = event; s.receipts[observation.id] = {'data_reception': receipt}
    # The second record has a real-looking saved receipt, but was not known at
    # the cut. Receipt authenticity is a spy; knowledge-time admission is real.
    s.db.rows[m.Event, uid(31)].received_at = instant(CUT) + timedelta(seconds=1)
    s.memories[1].created_at = instant(CUT) + timedelta(seconds=1)

    def creator(db, actual_p, actual_task, actual_job, evaluation_id, policy):
        assert (db, actual_p, actual_task, actual_job, evaluation_id) == (s.db, p, task, job, s.evaluation_id)
        s.creator_calls += 1
        scope = {'workspace_id': p.workspace_id, 'task_id': task.id, 'goal_snapshot_hash': subject.hashed(subject.snapshot(task))}
        identity = {'invocation_id': evaluation_id, 'job_id': job.id, 'actor_id': p.user_id,
            'lease_token': token, 'request_key': 'graph:original-evaluation:1'}
        return task, scope, identity, subject.contract.check_preparation_policy(goal, policy)
    monkeypatch.setattr(subject, '_assert_creator', creator)  # actual leased creator/approved binding boundary
    monkeypatch.setattr(leases, 'fenced', lambda db: db is s.db)
    monkeypatch.setattr(data_requirements, 'now', lambda: s.now)
    monkeypatch.setattr(instance_data_receipts, 'query_context', lambda *_args: None)
    monkeypatch.setattr(instance_data_receipts, 'observation_receipt', lambda db, p, row: copy.deepcopy(s.receipts[row.id]))
    monkeypatch.setattr(state_receipt_guard_v245, 'check', lambda *_args: {'status': 'received'})
    monkeypatch.setattr(data_tools, 'managed_include_trusted_cases', lambda *_args, **_kwargs: False)
    from generic_agents import multimodal_question_service_v262, pixel_question_service_v252
    from generic_agents import native_candidate_queries_v139, source_query_service
    for module in (multimodal_question_service_v262, pixel_question_service_v252, native_candidate_queries_v139):
        monkeypatch.setattr(module, 'has_trace', lambda *_args: False)
    monkeypatch.setattr(source_query_service, 'is_v2', lambda *_args, **_kwargs: False)
    actual_agent = agents.data_agent
    def recording_agent(*args, **kwargs):
        s.clock = kwargs.get('_goal_evaluation_cut')
        return actual_agent(*args, **kwargs)
    monkeypatch.setattr(agents, 'data_agent', recording_agent)
    s.create = lambda: subject.create_frozen(s.db, p, task, job, evaluation_id=s.evaluation_id,
        cut_at=CUT, parent_query_id=s.parent.id, preparation_policy=s.policy)
    return s


def test_real_three_seams_freeze_query_and_ack_cut_without_changing_q0_or_goal(scene):
    s = scene; before = copy.deepcopy((s.parent.data, s.task.data))
    row = s.create(); report = row.data['result']['result']
    assert report['evaluated_at'] == CUT and report['resolved_time']['end'] == CUT
    assert [item['observation_id'] for item in report['observation_evidence']] == [s.observations[0].id]
    assert report['observation_evidence'][0]['goal_evaluation_knowledge_cut_v288']['memory_id'] == s.memories[0].id
    assert any(item.get('status') == 'not_received_at_evaluation_cut' for item in report['excluded_evidence'])
    assert report['trusted_cases'] == [] and report['learning_decisions'] == [] and report['conflicts'] == []
    assert (s.parent.data, s.task.data) == before and row.id != s.parent.id
    assert not ({'native_goal_task_id', 'native_goal_context_hash', 'native_goal_request_hash', 'goal_delegation'} & set(row.data))
    assert s.db.additions == [m.DataRun, m.Audit] and s.db.events[0].action == subject.SAVED
    assert row.data[subject.KEY]['request_id'] == s.job.id
    assert row.data[subject.KEY]['evaluation_id'] == s.evaluation_id


def test_memory_created_after_cut_is_not_admitted_even_if_event_ack_is_earlier(scene):
    s = scene; s.db.rows[m.Event, uid(31)].received_at = instant(CUT) - timedelta(seconds=1)
    report = s.create().data['result']['result']
    assert [item['observation_id'] for item in report['observation_evidence']] == [s.observations[0].id]


def test_fixed_policy_keeps_original_goal_window_but_evaluates_knowledge_at_new_cut(scene):
    s = scene; s.goal['evidence']['time'] = goal_spec(fixed=True)['evidence']['time']
    s.task.data['goal_hash'] = digest(s.goal)
    s.task.data['goal_window'] = resolve(s.goal['evidence']['time'], reference=instant(INITIAL))
    s.parent.data = parent_data(s)  # A different synthetic Goal/Q0 fixture, before the tested call.
    original = copy.deepcopy(s.task.data['goal_window'])
    s.policy = subject.contract.make_preparation_policy(s.goal)
    report = s.create().data['result']['result']
    assert report['resolved_time'] == original and report['evaluated_at'] == CUT
    assert s.task.data['goal_window'] == original and not report['observation_evidence']


@pytest.mark.parametrize('confirmation_at', [None, CHECKED])
def test_unknown_or_later_confirmation_cannot_supply_confirmed_rank_at_cut(scene, confirmation_at):
    s = scene; observation = s.observations[0]
    fingerprint = digest({'extracted': observation.data['extracted'], 'evidence_ids': [], 'invocation_id': None})
    observation.data = {**observation.data, 'human_verification': {
        'decision': 'confirmed', 'input_hash': fingerprint, 'at': confirmation_at}}
    report = s.create().data['result']['result']
    assert not report['observation_evidence'] and report['status'] == 'insufficient'
    assert any(item.get('status') == 'confirmation_not_proven_at_evaluation_cut' for item in report['excluded_evidence'])


def test_replay_and_independent_reader_use_saved_bytes_not_new_query_clock_or_q0(scene):
    s = scene; row = s.create(); before = copy.deepcopy(row.data)
    assert s.create() is row and s.db.additions == [m.DataRun, m.Audit]
    s.parent.data = {**s.parent.data, 'result': {'later': 'Q0 independently changed'}}
    s.now += timedelta(days=2)
    frozen = subject.checked_frozen(s.db, s.reader, s.task, row.id, evaluation_id=s.evaluation_id)
    assert frozen['result'] == before['result'] and frozen['provenance'] == before[subject.KEY]
    assert row.data == before and s.db.additions == [m.DataRun, m.Audit]


@pytest.mark.parametrize('damage', ['audit', 'result', 'reader'])
def test_cold_snapshot_rejects_missing_original_audit_mutation_or_reader_iam(scene, damage):
    s = scene; row = s.create()
    if damage == 'audit': s.db.events.clear()
    elif damage == 'result': row.data['result']['result']['evaluated_at'] = CHECKED
    else: s.read_allowed = False
    with pytest.raises(HTTPException): subject.checked_frozen(s.db, s.reader, s.task, row.id, evaluation_id=s.evaluation_id)


@pytest.mark.parametrize('part', ['relation_query', 'data_query', 'tool_steps'])
def test_unsupported_goal_is_explicit_before_creator_or_data_run_creation(scene, part):
    s = scene; s.task.data['goal_spec'][part] = {'unsupported': 'retained'}
    s.task.data['goal_hash'] = digest(s.task.data['goal_spec'])
    with pytest.raises(HTTPException) as error: s.create()
    assert part in error.value.detail['unsupported_goal_parts']
    assert not s.db.additions and s.creator_calls == 0


def test_opaque_cut_cannot_be_a_json_request_field_or_rebound_to_another_query(scene):
    s = scene; row = s.create(); request = row.data['request']; before = s.db.reads
    with pytest.raises(HTTPException):
        agents.data_agent(s.db, s.p, request, row.id, task=s.task,
            **subject.CONTROLS, _goal_evaluation_cut={'cut_at': CUT})
    assert s.db.reads == before
    with pytest.raises(HTTPException):
        services.query_evidence(s.db, s.p, request['entity_ids'], request['fields'],
            learning=False, include_trusted_cases=False, route_key=uid(999),
            requirement=request['evidence_requirement'], fixed_window=request['resolved_time'], _goal_evaluation_cut=s.clock)
    assert s.db.reads == before


def test_owned_local_cut_cannot_enter_an_unrelated_multimodal_source_family(scene, monkeypatch):
    s = scene
    from generic_agents import multimodal_question_service_v262
    monkeypatch.setattr(multimodal_question_service_v262, 'has_trace', lambda *_args: True)
    monkeypatch.setattr(multimodal_question_service_v262, '_load', lambda *_args: pytest.fail('foreign family was entered'))
    with pytest.raises(HTTPException): s.create()
    assert not s.db.events


def test_old_none_clock_keeps_original_latest_rolling_behavior(scene):
    s = scene; row = s.create(); request = row.data['request']; s.expected_at = s.now
    report = services.query_evidence(s.db, s.p, request['entity_ids'], request['fields'],
        learning=False, include_trusted_cases=False, requirement=request['evidence_requirement'],
        fixed_window=request['resolved_time'])
    assert report['evaluated_at'] == s.now.isoformat() and report['resolved_time']['end'] == s.now.isoformat()
    assert report['resolved_time'] != request['resolved_time']


def test_invalid_adopted_binding_is_fail_closed():
    with pytest.raises(HTTPException): subject._check_creator_binding(None, None, None, None, None, None, None, None)


def test_future_cut_is_rejected_before_creating_independent_query(scene):
    s = scene; s.trusted = instant(CUT) - timedelta(seconds=1)
    with pytest.raises(HTTPException): s.create()
    assert not s.db.additions
