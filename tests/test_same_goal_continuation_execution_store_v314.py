"""Actual ledger, transition and SQL predicate paths with explicit IO fixtures.

IAM, published grants, current sources, database transactions, SDK and ACK
authority are separate integration checks. This fixture runs no provider.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_continuation_execution_store_v314 as s
from generic_agents.contracts import digest
from generic_agents.models import Task
from tests.test_same_goal_continuation_store_v312 import predicate


def uid(number):
    return str(UUID(int=number))


@pytest.fixture
def scene(monkeypatch):
    c = s.c
    p = NS(user_id=uid(1), workspace_id=uid(2), role='operator')
    task = Task(id=uid(3), workspace_id=p.workspace_id, owner_id=p.user_id, package_id=uid(4),
        title='原任务', status='workflow_active', version=7, data={'unchanged_original_IO': True})
    advice_id, continuation_id = uid(5), uid(6)
    source = {'decision_intent': 'business_conclusion', 'original_custody_IO': True}
    source_hash = digest(source)
    draft = {'schema': c.drafts.REVISION, 'continuation_id': continuation_id, 'version': 2,
        'source_hash': source_hash, 'draft': c.drafts.draft({'purpose': '复核读数',
            'work_description': '只读汇总实际数据', 'expected_result': '平均值及缺测说明'})}
    draft['record_hash'] = c.record_hash(draft)
    cont = {'id': continuation_id, 'version': 2, 'revision_hash': draft['record_hash'],
        'source_hash': source_hash}
    spec = {'stages': ['department', 'office'], 'executor': 'executor', 'acceptance': 'acceptance'}
    role_ids = {'department': uid(10), 'office': uid(11), 'executor': uid(12), 'acceptance': uid(13)}
    inputs = {'field': 'humidity', 'operation': 'mean', 'records': [
        {'entity_id': uid(14), 'version': 1, 'attributes': {'humidity': 62}}]}
    basis = {'schema': c.grants.BASIS, 'continuation_ref': cont, 'inputs': inputs,
        'input_hash': digest(inputs), 'input_schema_hash': digest(c.grants.input_schema()),
        'output_schema_hash': digest(c.grants.output_schema()), 'agent_role': 'data',
        'max_calls': 1, 'side_effect': False, 'external_allowed': False, 'cloud_allowed': False,
        'policy': {'ref': {'policy_IO': True}, 'spec': spec}, 'capability_pin': {'id': uid(15)},
        'field': {'key': 'humidity', 'label': '湿度', 'unit': '%'}, 'operation': 'mean',
        'records': inputs['records'], 'evaluation': {'cut_at': '2026-11-02T12:00:00+00:00'},
        'simulation_only': True, 'test_only': True}
    basis['record_hash'] = c.record_hash(basis)
    grant = {'schema': c.grants.GRANT, 'grant_id': uid(16), 'continuation_id': continuation_id,
        'workspace_id': p.workspace_id, 'task_id': task.id, 'advice_plan_id': advice_id,
        'source_hash': source_hash, 'source': source, 'basis': basis}
    grant['record_hash'] = c.record_hash(grant)
    proposal = {'schema': c.PROPOSAL, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'advice_plan_id': advice_id, 'continuation_ref': cont, 'grant_ref': {
            'id': grant['grant_id'], 'record_hash': grant['record_hash'], 'version': 1},
        'grant': grant, 'source_hash': source_hash, 'source': source, 'draft': draft,
        'plan_origin': 'human_revision', 'supersedes_plan_id': None, 'rationale': '补充已收资料的只读计算',
        'original_candidate_ref': None, 'submitter_id': p.user_id, 'business_actor_id': p.user_id,
        'resources': dict(c.RESOURCES)}
    proposal['record_hash'] = c.record_hash(proposal)
    v = NS(p=p, task=task, proposal=proposal, advice_id=advice_id, continuation_id=continuation_id,
        counter=100, live=True, trace=[], rows={s.Plan: [], s.Audit: [], s.Job: [], s.Invocation: []})

    class DB:
        @property
        @contextmanager
        def no_autoflush(self):
            yield

        def scalars(self, statement):
            model = statement.column_descriptions[0]['entity']
            assert p.workspace_id in statement.compile().params.values()
            return [row for row in v.rows[model] if predicate(statement.whereclause, row)]

        def scalar(self, statement):
            assert statement.column_descriptions[0]['entity'] is s.Job
            assert statement._for_update_arg.nowait is True
            return next(iter(self.scalars(statement)), None)

        def add(self, row):
            v.rows[type(row)].append(row)

        def flush(self):
            pass

        def commit(self):
            pytest.fail('API owns commit')

        @contextmanager
        def begin_nested(self):
            originals = {model: list(rows) for model, rows in v.rows.items()}
            task_before = (task.version, copy.deepcopy(task.data))
            before = [(row, row.status, row.version, copy.deepcopy(row.data))
                for model in (s.Plan, s.Job) for row in originals[model]]
            try:
                yield
            except BaseException:
                v.rows = originals
                task.version, task.data = task_before
                for row, status, version, data in before:
                    row.status, row.version, row.data = status, version, data
                raise

    v.db = DB()

    def task_io(db, actor, identity, *, lock=False):
        assert db is v.db and actor is p and identity == task.id
        v.trace.append(('task', lock))
        return task

    def nonce_io(db, actor, **value):
        assert db is v.db and actor is p and value['task_id'] == task.id
        v.trace.append(('nonce', value['action']))

    def source_io(db, actor, actual_task, value, execution_plan_id=None, lock=True):
        assert db is v.db and actor is p and actual_task is task and value == proposal
        v.trace.append(('source', execution_plan_id))
        if not v.live:
            raise HTTPException(409, 'current source no longer available')
        return {'policy': {'spec': spec}}

    def role_io(db, actor, stage, *, actor_id=None, **kwargs):
        identity = actor_id or actor.user_id
        if identity != role_ids[stage]:
            raise HTTPException(403, 'explicit role IO denied')
        return NS(id=identity, data={'scope_role_IO': stage})

    monkeypatch.setattr(s, '_reader', lambda *args, **kwargs: None)
    monkeypatch.setattr(s, '_task', task_io)
    monkeypatch.setattr(s, '_clock', lambda db: datetime(2026, 11, 2, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(s, 'lock_current_actor', lambda *args: v.trace.append(('actor', p.user_id)))
    monkeypatch.setattr(s.nonce, 'acquire', nonce_io)
    monkeypatch.setattr(s.sources, 'build_proposal', lambda *args, **kwargs: {'proposal': copy.deepcopy(proposal)})
    monkeypatch.setattr(s.sources, 'live', source_io)
    monkeypatch.setattr(s.sources, 'policy', lambda *args, **kwargs: {'spec': spec})
    monkeypatch.setattr(s.approval, 'role_allowed', role_io)

    def request(**extra):
        v.counter += 1
        return {'request_id': uid(v.counter), **extra}

    def submit(body=None):
        body = body or request(expected_task_version=task.version, expected_continuation_version=2,
            revision_hash=cont['revision_hash'], source_hash=source_hash, grant_ref=proposal['grant_ref'],
            plan_origin='human_revision', rationale=proposal['rationale'], preview_hash=proposal['record_hash'])
        return s.submit(v.db,p,task.id,advice_id,continuation_id,body)

    def change(name, view, **extra):
        body = request(expected_execution_version=view['version'], reason='明确记录本次办理依据', **extra)
        return getattr(s, name)(v.db,p,task.id,advice_id,continuation_id,view['plan_id'],body)

    def approve():
        view = submit()
        for stage in spec['stages']:
            p.user_id = role_ids[stage]
            view = change('decide', view, submission_hash=view['submission_hash'], decision='approve')
        return view

    v.submit, v.change, v.approve = submit, change, approve
    v.role_ids = role_ids
    return v


def test_real_ledger_requires_each_stage_and_creates_one_fixed_job_only_on_explicit_start(scene):
    v = scene
    original_task = copy.deepcopy(v.task.data)
    original_version = v.task.version
    view = v.approve()
    assert view['phase'] == 'approved' and not v.rows[s.Job]
    v.p.user_id = v.role_ids['executor']
    started = v.change('start', view)
    assert started['phase'] == 'queued' and len(v.rows[s.Job]) == 1
    job = v.rows[s.Job][0]
    rows = s.accounting(v.db,v.p,v.task)
    assert rows[0]['job'] is job and job.idempotency_key == 'continuation-read-v314:'+view['plan_id']+':1'
    assert job.data['submission_hash'] == v.proposal['record_hash']
    assert job.data['input_hash'] == v.proposal['grant']['basis']['input_hash']
    submit_event = next(event for event in v.rows[s.Audit] if event.action == s.c.ACTION_SUBMIT)
    assert v.task.data == {**original_task, s.c.INDEX: [{
        'plan_id': view['plan_id'], 'submission_hash': view['submission_hash'],
        'submit_ref': {'id': submit_event.id, 'record_hash': s.digest(submit_event.data)},
    }]}
    assert v.task.version == original_version + 1
    assert all(started[key] is False for key in s.c.FLAGS)
    assert [label for label,_ in v.trace[:3]] == ['nonce','task','actor']


@pytest.mark.parametrize('damage', ['missing', 'wrong_hash', 'duplicate', 'orphan'])
def test_task_binding_is_required_and_matches_protected_submit_inventory(scene, damage):
    v = scene
    v.submit()
    bindings = copy.deepcopy(v.task.data[s.c.INDEX])
    if damage == 'missing':
        del v.task.data[s.c.INDEX]
    else:
        if damage == 'wrong_hash':
            bindings[0]['submit_ref']['record_hash'] = 'f' * 64
        elif damage == 'duplicate':
            bindings.append(copy.deepcopy(bindings[0]))
        else:
            bindings[0]['plan_id'] = uid(999)
        v.task.data[s.c.INDEX] = bindings
    with pytest.raises(HTTPException, match='任务的办理执行关联无法核对'):
        s.accounting(v.db, v.p, v.task)


def test_original_submit_replay_preserves_task_binding_and_version(scene):
    v = scene
    first = v.submit()
    event = next(event for event in v.rows[s.Audit] if event.action == s.c.ACTION_SUBMIT)
    data, version = copy.deepcopy(v.task.data), v.task.version
    v.live = False
    again = v.submit(copy.deepcopy(event.data['request']))
    assert again['plan_id'] == first['plan_id']
    assert v.task.version == version and v.task.data == data


def test_cold_submit_rejects_different_superseded_plan_than_the_sealed_proposal(scene):
    v = scene
    v.submit()
    submit = next(event for event in v.rows[s.Audit] if event.action == s.c.ACTION_SUBMIT)
    altered = copy.deepcopy(submit.data)
    altered['request']['supersedes_plan_id'] = uid(999)
    with pytest.raises(HTTPException) as rejected:
        s._transition(altered, v.proposal, None, [], None)
    assert rejected.value.status_code == 409


def test_admin_flag_cannot_substitute_executor_role_or_skip_approval(scene):
    v = scene;view = v.submit();v.p.role = 'admin'
    with pytest.raises(HTTPException):
        v.change('start',view)
    assert not v.rows[s.Job]
    v.p.role = 'operator';v.p.user_id = uid(1)
    approved = view
    for stage in ('department','office'):
        v.p.user_id = v.role_ids[stage]
        approved = v.change('decide',approved,submission_hash=approved['submission_hash'],decision='approve')
    v.p.user_id = uid(99);v.p.role = 'admin'
    with pytest.raises(HTTPException) as error:
        v.change('start',approved)
    assert error.value.status_code == 403 and not v.rows[s.Job]


def test_origin_actor_cannot_be_first_stage_even_with_real_role_io(scene, monkeypatch):
    v = scene;view = v.submit()
    monkeypatch.setattr(s.approval,'role_allowed',lambda *args, **kwargs:NS(id=uid(70),data={'explicit_role_IO':True}))
    with pytest.raises(HTTPException) as error:
        v.change('decide',view,submission_hash=view['submission_hash'],decision='approve')
    assert error.value.status_code == 403 and len(v.rows[s.Audit]) == 1


def test_queued_cancel_locks_original_job_and_preserves_single_start_allocation(scene):
    v = scene;view = v.approve();v.p.user_id = v.role_ids['executor'];started = v.change('start',view)
    cancelled = v.change('cancel',started)
    assert cancelled['phase'] == 'cancelled' and v.rows[s.Job][0].status == 'cancelled'
    assert len(s.accounting(v.db,v.p,v.task)) == 1 and not v.rows[s.Invocation]


def test_running_cancel_does_not_rewrite_original_job_to_unsent(scene):
    v = scene;view = v.approve();v.p.user_id = v.role_ids['executor'];started = v.change('start',view)
    job = v.rows[s.Job][0];job.status = 'running'
    result = v.change('cancel',started)
    assert result['phase'] == 'cancel_requested' and job.status == 'running'
    assert len(v.rows[s.Job]) == 1


def test_original_receipt_keeps_body_custody_when_projection_is_corrupt(scene):
    v = scene;view = v.submit();event = v.rows[s.Audit][0]
    v.rows[s.Plan].clear()
    value = s.get_operation(v.db,v.p,v.task.id,v.advice_id,v.continuation_id,event.data['request_id'])
    assert value['plan_id'] == view['plan_id'] and value['body_hash'] == event.data['body_hash']
    assert value['result_status'] == 'needs_check' and value['result'] is None


def test_view_reports_actual_stage_role_and_viewer_has_no_actions(scene):
    v = scene
    view = v.submit()
    assert view['policy_stage_count'] == 2 and view['current_stage_label']
    assert view['permissions']['can_decide'] is False and view['permissions']['can_cancel'] is True
    args = (v.db, v.p, v.task.id, v.advice_id, v.continuation_id, view['plan_id'])
    v.p.user_id = v.role_ids['department']
    assert s.get(*args)['permissions']['can_decide'] is True
    v.p.role = 'viewer'
    assert all(value is False for value in s.get(*args)['permissions'].values())


def test_expired_source_disables_start_but_preserves_custody_and_owner_cancel(scene):
    v = scene
    view = v.approve()
    args = (v.db, v.p, v.task.id, v.advice_id, v.continuation_id, view['plan_id'])
    v.p.user_id = v.role_ids['executor']
    assert s.get(*args)['permissions']['can_start'] is True
    v.live = False
    assert s.get(*args)['permissions']['can_start'] is False
    v.p.user_id = v.proposal['submitter_id']
    actual = s.get(*args)
    assert actual['phase'] == 'approved' and actual['submission_hash'] == view['submission_hash']
    assert actual['permissions']['can_cancel'] is True and actual['permissions']['can_start'] is False
