"""Authenticated original-Task query proposal APIs; mutations are UUID recoverable."""
from typing import Annotated
from fastapi import APIRouter, Depends
from .db import session
from .security import Principal, authenticated
from .workspace_api import saved_execution
from . import native_query_proposals as service

router = APIRouter(prefix='/api/workspace/tasks/{task_id}/query-proposals')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


def result(db, p, task):
    db.commit()
    return service.listing(db, p, task)


@router.get('')
def listing(task_id: str, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id)
    return service.listing(db, p, task)


@router.put('/draft')
def draft(task_id: str, body: service.Draft, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    service.save_draft(db, p, task, body)
    return result(db, p, task)


@router.post('/preview')
def preview(task_id: str, body: service.Preview, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id)
    return service.preview_view(task, service.preview(db, p, task, body.model_profile_id))


@router.post('')
def create(task_id: str, body: service.Create, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    service.create(db, p, task, body)
    return result(db, p, task)


@router.post('/requests/recover')
def recover(task_id: str, body: service.Recovery, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    value = service.recover(db, p, task, body)
    db.commit()
    return value


@router.get('/{proposal_id}')
def detail(task_id: str, proposal_id: str, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id)
    return service.detail(db, p, task, proposal_id)


@router.post('/{proposal_id}/review')
def review(task_id: str, proposal_id: str, body: service.Review, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    service.review(db, p, task, proposal_id, body)
    return result(db, p, task)


@router.post('/{proposal_id}/adopt')
def adopt(task_id: str, proposal_id: str, body: service.Adopt, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    service.adopt(db, p, task, proposal_id, body)
    return result(db, p, task)


@router.post('/{proposal_id}/cancel')
def cancel(task_id: str, proposal_id: str, body: service.ProposalCommand, p: P, db: DB):
    task, _, _ = saved_execution(db, p, task_id, True)
    service.cancel(db, p, task, proposal_id, body)
    return result(db, p, task)
