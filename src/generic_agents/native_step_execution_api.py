"""Authenticated, recoverable execution commands for the original workflow Task."""
import json
from typing import Annotated
from urllib.parse import quote
from fastapi import APIRouter, Depends, Response
from .db import session
from .security import Principal, authenticated
from .workspace_api import saved_execution
from . import native_step_execution as execution

router = APIRouter(prefix='/api/workspace/tasks')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
ROOT = '/{task_id}/intelligent-plan/{plan_id}/execution'


@router.get(ROOT)
def listing(task_id: str, plan_id: str, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id)
    return execution.listing(db, p, task, plan_id)


@router.post(ROOT + '/preview')
def preview(task_id: str, plan_id: str, body: dict, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id)
    value = execution.preview(db, p, task, plan_id)
    value.pop('_contract')
    return value


def _result(db, p, task, plan_id):
    db.commit()
    return execution.listing(db, p, task, plan_id)


@router.post(ROOT + '/approve')
def approve(task_id: str, plan_id: str, body: execution.Approve, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    execution.approve(db, p, task, plan_id, body)
    return _result(db, p, task, plan_id)


@router.post(ROOT + '/retry')
def retry(task_id: str, plan_id: str, body: execution.Retry, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    execution.retry(db, p, task, plan_id, body)
    return _result(db, p, task, plan_id)


@router.post(ROOT + '/stop')
def stop(task_id: str, plan_id: str, body: execution.RunCommand, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    execution.stop(db, p, task, plan_id, body)
    return _result(db, p, task, plan_id)


@router.post(ROOT + '/review')
def review(task_id: str, plan_id: str, body: execution.Review, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    execution.review(db, p, task, plan_id, body)
    return _result(db, p, task, plan_id)


@router.post(ROOT + '/requests/recover')
def recover(task_id: str, plan_id: str, body: execution.Recovery, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    value = execution.recover(db, p, task, plan_id, body)
    db.commit()
    value['execution'] = execution.listing(db, p, task, plan_id)['execution']
    return value


@router.get(ROOT + '/export')
def export(task_id: str, plan_id: str, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id)
    value = execution.export(db, p, task, plan_id)
    return Response(json.dumps(value, ensure_ascii=False, default=str), media_type='application/json',
        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote('原任务只读执行与人工核查.json')})
