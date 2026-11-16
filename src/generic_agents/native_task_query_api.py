"""Supplementary query history and explicit local execution, on the same Task."""
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from .db import session
from .security import Principal, authenticated
from .workspace_api import saved_execution
from . import native_task_queries as queries

router = APIRouter(prefix='/api/workspace/tasks')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
ROOT = '/{task_id}/supporting-queries'


@router.get(ROOT)
def listing(task_id: str, p: P, db: DB, limit: int=Query(20,ge=1,le=50), cursor: str|None=None):
    task, _, _ = saved_execution(db, p, task_id)
    return queries.listing(db, p, task, limit=limit, cursor=cursor)


@router.post(ROOT)
def execute(task_id: str, body: queries.Execute, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    row = queries.execute(db, p, task, body)
    db.commit()
    return {**queries.listing(db, p, task), 'run_id': row.id}


@router.post(ROOT + '/requests/recover')
def recover(task_id: str, body: queries.Recover, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    result = queries.recover(db, p, task, body)
    db.commit()
    return result


@router.post(ROOT + '/{run_id}/cancel')
def cancel(task_id: str, run_id: str, body: queries.Command, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    queries.cancel(db, p, task, run_id, body)
    db.commit()
    return queries.listing(db, p, task)
