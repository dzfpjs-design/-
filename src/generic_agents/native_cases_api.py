"""Recoverable, task-scoped native method review and current evidence checks."""
from typing import Annotated
from fastapi import APIRouter, Depends, Query, Path
from .db import session
from .security import Principal, authenticated
from .workspace_api import saved_execution
from . import native_cases as cases
from . import native_method_evolution as evolution

router = APIRouter(prefix='/api/workspace/tasks')
DB = Annotated[object, Depends(session)]
P = Annotated[Principal, Depends(authenticated)]


@router.get('/{identity}/experience')
def experience(identity: str, p: P, db: DB):
    task,_,_ = saved_execution(db,p,identity)
    return cases.view(db,p,task)


def change(identity, body, operation, p, db):
    task,_,_ = saved_execution(db,p,identity,True)
    result = cases.execute(db,p,task,body,operation)
    value = cases.view(db,p,task)
    if result is not None: value['operation_result'] = result
    db.commit()
    return value


@router.post('/{identity}/experience/draft')
def draft(identity: str, body: cases.Draft, p: P, db: DB):
    return change(identity,body,'draft',p,db)


@router.get('/{identity}/experience/draft-revisions')
def revisions(identity:str,p:P,db:DB,limit:Annotated[int,Query(ge=1,le=50)]=20,
              after_version:Annotated[int|None,Query(ge=1)]=None):
    task,_,_=saved_execution(db,p,identity)
    return cases.draft_revisions(db,p,task,limit=limit,after_version=after_version)


@router.get('/{identity}/experience/draft-revisions/{revision}')
def revision(identity:str,revision:Annotated[int,Path(ge=1)],p:P,db:DB):
    task,_,_=saved_execution(db,p,identity)
    return cases.draft_revisions(db,p,task,revision=revision)


@router.post('/{identity}/experience/restore')
def restore(identity:str,body:cases.Restore,p:P,db:DB):
    return change(identity,body,'restore',p,db)


@router.post('/{identity}/experience/submit')
def submit(identity: str, body: cases.CaseCommand, p: P, db: DB):
    return change(identity,body,'submit',p,db)


@router.post('/{identity}/experience/review')
def review(identity: str, body: cases.Review, p: P, db: DB):
    return change(identity,body,'review',p,db)


@router.post('/{identity}/experience/revoke')
def revoke(identity: str, body: cases.CaseCommand, p: P, db: DB):
    return change(identity,body,'revoke',p,db)


@router.post('/{identity}/experience/adopt')
def adopt(identity: str, body: cases.Adopt, p: P, db: DB):
    return change(identity,body,'adopt',p,db)


@router.post('/{identity}/experience/confirm')
def confirm(identity: str, body: cases.Confirm, p: P, db: DB):
    return change(identity,body,'confirm',p,db)


@router.post('/{identity}/experience/disengage')
def disengage(identity: str, body: cases.UseCommand, p: P, db: DB):
    return change(identity,body,'disengage',p,db)


@router.post('/{identity}/experience/feedback')
def feedback(identity: str, body: cases.Feedback, p: P, db: DB):
    return change(identity,body,'feedback',p,db)


@router.post('/{identity}/experience/requests/recover')
def recovery(identity: str, body: cases.Recovery, p: P, db: DB):
    task,_,_ = saved_execution(db,p,identity,True)
    result = cases.recover(db,p,task,body)
    result['view'] = cases.view(db,p,task)
    db.commit()
    return result


@router.post('/{identity}/experience/revise')
def revise(identity: str, body: evolution.Revise, p: P, db: DB):
    return change(identity,body,'revise',p,db)


@router.post('/{identity}/experience/evaluate')
def evaluate(identity: str, body: evolution.Evaluate, p: P, db: DB):
    return change(identity,body,'evaluate',p,db)
