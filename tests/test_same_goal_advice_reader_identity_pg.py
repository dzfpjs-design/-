"""Real-PG proof that custody reads never discard caller-owned ORM state."""
import copy
import math

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_read_session_v2 as read_session
from generic_agents import same_goal_advice_unlimited_store_v2 as v2
from generic_agents.db import engine
from generic_agents.models import Job, Plan, Task, uid
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def read(db, p, task, plan, kind):
    return common.read(db, p, task) if kind == 'common' else v2.read_queued(db, p, task, plan)


@pytest.mark.parametrize('kind', ['common', 'v2'])
@pytest.mark.parametrize('fault', ['dirty_task_data', 'dirty_task_status', 'dirty_task_version',
    'dirty_plan', 'dirty_job', 'new_job', 'deleted_job'])
def test_pending_orm_changes_are_rejected_without_mutation_and_rollback(synthetic_custody, kind, fault):
    s = synthetic_custody
    with Session(engine(), expire_on_commit=False) as db:
        task, plan, job = db.get(Task, s['task_id']), db.get(Plan, s['plan_id']), db.get(Job, s['job_id'])
        original = (task.title, copy.deepcopy(task.data), task.status, task.version, plan.status, job.status)
        new_job = None
        if fault == 'dirty_task_data': task.data = {**task.data, 'pending': '待保存'}
        elif fault == 'dirty_task_status': task.status = 'workflow_paused'
        elif fault == 'dirty_task_version': task.version += 1
        elif fault == 'dirty_plan': plan.status = 'running'
        elif fault == 'dirty_job': job.status = 'running'
        elif fault == 'deleted_job': db.delete(job)
        else:
            new_job = Job(id=uid(), workspace_id=s['principal'].workspace_id, kind='synthetic_unsaved',
                status='queued', attempts=0, idempotency_key='synthetic-' + uid(), data={})
            db.add(new_job)
        changed = (task.title, copy.deepcopy(task.data), task.status, task.version, plan.status, job.status)
        with pytest.raises(HTTPException) as error:
            read(db, s['principal'], task, plan, kind)
        assert error.value.status_code == 409
        assert (task.title, task.data, task.status, task.version, plan.status, job.status) == changed
        assert (job in db.deleted) == (fault == 'deleted_job')
        assert (new_job in db.new) if new_job is not None else not db.new
        db.rollback()
        restored = db.get(Task, task.id)
        assert (restored.title, restored.data, restored.status, restored.version,
            db.get(Plan, plan.id).status, db.get(Job, job.id).status) == original
        if new_job is not None: assert db.get(Job, new_job.id) is None


@pytest.mark.parametrize('kind', ['common', 'v2'])
@pytest.mark.parametrize('target', ['task', 'plan', 'job'])
def test_untracked_in_place_json_is_not_overwritten_by_reader(synthetic_custody, kind, target):
    s = synthetic_custody
    with Session(engine()) as db:
        task, plan, job = db.get(Task, s['task_id']), db.get(Plan, s['plan_id']), db.get(Job, s['job_id'])
        row = {'task': task, 'plan': plan, 'job': job}[target]
        row_id = row.id
        before = copy.deepcopy(row.data); row.data['unsaved_local_hint'] = '仅内存中的内容'
        assert row not in db.dirty
        with pytest.raises(HTTPException) as error:
            read(db, s['principal'], task, plan, kind)
        assert error.value.status_code == 409
        assert row.data == {**before, 'unsaved_local_hint': '仅内存中的内容'}
        db.rollback()
    with Session(engine()) as db:
        stored = db.get({'task': Task, 'plan': Plan, 'job': Job}[target], row_id)
        assert stored.data == before


@pytest.mark.parametrize('kind', ['common', 'v2'])
@pytest.mark.parametrize('original,replacement', [(True, 1), (1, True), (1, 1.0), (1.0, 1),
    ({'items': [True, 1]}, {'items': [1, 1]})])
def test_untracked_json_type_change_is_not_treated_as_equal(synthetic_custody, kind, original, replacement):
    s = synthetic_custody
    with Session(engine()) as db:
        task = db.get(Task, s['task_id']); task.data = {'payload': {'value': original}}; db.commit()
    with Session(engine()) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, s['plan_id'])
        task.data['payload']['value'] = replacement
        assert task not in db.dirty
        with pytest.raises(HTTPException) as error:
            read(db, s['principal'], task, plan, kind)
        assert error.value.status_code == 409
        assert type(task.data['payload']['value']) is type(replacement)
        assert task.data['payload']['value'] == replacement


@pytest.mark.parametrize('kind', ['common', 'v2'])
def test_multiple_loaded_unrelated_jobs_are_checked_without_overwriting(synthetic_custody, kind):
    s = synthetic_custody; identities = [uid() for _ in range(3)]
    with Session(engine()) as db:
        db.add_all([Job(id=identity, workspace_id=s['principal'].workspace_id, kind='synthetic_other',
            status='queued', attempts=0, idempotency_key='synthetic-' + uid(), data={'nested': {'n': i}})
            for i, identity in enumerate(identities)])
        db.commit()
    with Session(engine()) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, s['plan_id'])
        jobs = [db.get(Job, identity) for identity in identities]
        read(db, s['principal'], task, plan, kind)
        jobs[1].data['nested']['n'] = 7
        assert jobs[1] not in db.dirty
        with pytest.raises(HTTPException):
            read(db, s['principal'], task, plan, kind)
        assert [job.data['nested']['n'] for job in jobs] == [0, 7, 2]


@pytest.mark.parametrize('kind', ['common', 'v2'])
def test_concurrent_scalar_change_before_lock_is_rejected_without_overwriting(synthetic_custody, monkeypatch, kind):
    s = synthetic_custody; first_check = read_session._check_loaded_state; changed = []
    def concurrent_change(caller, reader, task_id):
        first_check(caller, reader, task_id)
        if not changed:
            changed.append(True)
            # Separate committed PG transaction after the first comparison,
            # but before the reader takes its Task lock; JSON stays unchanged.
            with Session(engine()) as writer:
                row = writer.get(Task, s['task_id']); row.status = 'workflow_paused'; row.version += 1
                writer.commit()
    monkeypatch.setattr(read_session, '_check_loaded_state', concurrent_change)
    with Session(engine()) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, s['plan_id'])
        old = (task.status, task.version, copy.deepcopy(task.data))
        with pytest.raises(HTTPException) as error:
            read(db, s['principal'], task, plan, kind)
        assert error.value.status_code == 409 and changed
        assert (task.status, task.version, task.data) == old
    with Session(engine()) as db:
        stored = db.get(Task, s['task_id'])
        assert stored.status == 'workflow_paused' and stored.version == old[1] + 1 and stored.data == old[2]


@pytest.mark.parametrize('kind', ['common', 'v2'])
def test_invalid_untracked_json_and_loaded_row_limit_fail_closed(synthetic_custody, monkeypatch, kind):
    s = synthetic_custody
    with Session(engine()) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, s['plan_id'])
        task.data['invalid'] = float('nan')
        assert task not in db.dirty
        with pytest.raises(HTTPException):
            read(db, s['principal'], task, plan, kind)
        assert math.isnan(task.data['invalid'])
        db.rollback()
    with Session(engine()) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, s['plan_id'])
        db.get(Job, s['job_id'])
        monkeypatch.setattr(read_session, 'MAX_LOADED_ROWS', 1)
        with pytest.raises(HTTPException):
            read(db, s['principal'], task, plan, kind)
        assert task.id == s['task_id'] and plan.id == s['plan_id']


@pytest.mark.parametrize('kind', ['common', 'v2'])
def test_nested_savepoint_rejection_preserves_outer_transaction(synthetic_custody, kind):
    s = synthetic_custody
    with Session(engine(), expire_on_commit=False) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, s['plan_id'])
        original = copy.deepcopy(task.data)
        nested = db.begin_nested()
        read(db, s['principal'], task, plan, kind)
        assert nested.is_active and db.in_transaction()
        task.data = {**task.data, 'pending': '嵌套事务修改'}
        with pytest.raises(HTTPException):
            read(db, s['principal'], task, plan, kind)
        assert task.data['pending'] == '嵌套事务修改' and nested.is_active
        nested.rollback()
        assert db.in_transaction() and task.data == original
        task.title = '外层事务仍可保存'
        db.commit()
    with Session(engine()) as db:
        assert db.get(Task, s['task_id']).title == '外层事务仍可保存'


@pytest.mark.parametrize('kind', ['common', 'v2'])
def test_unrelated_task_edit_and_target_title_edit_survive_read_and_commit(synthetic_custody, kind):
    s = synthetic_custody; other_id = uid()
    with Session(engine()) as db:
        task = db.get(Task, s['task_id'])
        db.add(Task(id=other_id, workspace_id=task.workspace_id, owner_id=task.owner_id,
            package_id=task.package_id, status='workflow_active', title='另一个任务', data={}))
        db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        task, plan, other = db.get(Task, s['task_id']), db.get(Plan, s['plan_id']), db.get(Task, other_id)
        task.title = '等待保存的标题'; other.data = {'note': '另一个任务的编辑'}
        read(db, s['principal'], task, plan, kind)
        assert task.title == '等待保存的标题' and other.data == {'note': '另一个任务的编辑'}
        assert task in db.dirty and other in db.dirty
        db.commit()
    with Session(engine()) as db:
        assert db.get(Task, s['task_id']).title == '等待保存的标题'
        assert db.get(Task, other_id).data == {'note': '另一个任务的编辑'}


@pytest.mark.parametrize('kind', ['common', 'v2'])
def test_new_unrelated_task_and_same_value_assignment_do_not_block(synthetic_custody, kind):
    s = synthetic_custody
    with Session(engine()) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, s['plan_id'])
        task.status = task.status  # SQLAlchemy marks dirty without a value change.
        other = Task(id=uid(), workspace_id=task.workspace_id, owner_id=task.owner_id,
            package_id=task.package_id, status='workflow_active', title='尚未保存的另一个任务', data={})
        db.add(other)
        read(db, s['principal'], task, plan, kind)
        assert other in db.new and task.status == 'workflow_active'
        db.rollback()


@pytest.mark.parametrize('kind', ['common', 'v2'])
def test_untracked_unrelated_task_edit_is_preserved_in_memory(synthetic_custody, kind):
    s = synthetic_custody; other_id = uid()
    with Session(engine()) as db:
        task = db.get(Task, s['task_id'])
        db.add(Task(id=other_id, workspace_id=task.workspace_id, owner_id=task.owner_id,
            package_id=task.package_id, status='workflow_active', title='另一个任务', data={}))
        db.commit()
    with Session(engine()) as db:
        task, plan, other = db.get(Task, s['task_id']), db.get(Plan, s['plan_id']), db.get(Task, other_id)
        other.data['draft'] = '尚未跟踪的编辑'
        assert other not in db.dirty
        read(db, s['principal'], task, plan, kind)
        assert other.data == {'draft': '尚未跟踪的编辑'} and other not in db.dirty
        db.rollback()


@pytest.mark.parametrize('kind', ['common', 'v2'])
def test_pending_duplicate_target_task_is_rejected_without_flush(synthetic_custody, kind):
    s = synthetic_custody
    with Session(engine()) as db:
        task, plan = db.get(Task, s['task_id']), db.get(Plan, s['plan_id'])
        duplicate = Task(id=task.id, workspace_id=task.workspace_id, owner_id=task.owner_id,
            package_id=task.package_id, status='workflow_active', title='重复任务', data={})
        db.add(duplicate)
        with pytest.raises(HTTPException):
            read(db, s['principal'], task, plan, kind)
        assert duplicate in db.new and db.in_transaction()
        db.rollback()
