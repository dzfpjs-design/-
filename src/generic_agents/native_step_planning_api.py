"""Authenticated native-step plan previews and durable request recovery."""
from typing import Annotated
from fastapi import APIRouter, Depends
from .db import session
from .security import Principal, authenticated
from .workspace_api import saved_execution
from . import native_step_planning as planning

router = APIRouter(prefix='/api/workspace/tasks')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
ROOT = '/{task_id}/intelligent-plan'


@router.get(ROOT)
def listing(task_id: str, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id)
    return planning.listing(db, p, task)


@router.post(ROOT + '/preview')
def preview(task_id: str, body: planning.Preview, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id)
    frozen = planning.preview(db, p, task, body.model_profile_id)
    return planning.preview_view(task, frozen, planning.model(db, p, body.model_profile_id))


def _result(db, p, task, row):
    db.commit()
    return planning.listing(db, p, task)


@router.post(ROOT)
def start(task_id: str, body: planning.Start, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    return _result(db, p, task, planning.start(db, p, task, body))


@router.post(ROOT + '/requests/recover')
def recover(task_id: str, body: planning.Recovery, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    result = planning.recover(db, p, task, body)
    db.commit()
    return result


@router.post(ROOT + '/{plan_id}/stop')
def stop(task_id: str, plan_id: str, body: planning.Stop, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    return _result(db, p, task, planning.stop(db, p, task, plan_id, body))
