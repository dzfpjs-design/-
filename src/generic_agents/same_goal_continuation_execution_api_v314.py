"""Normal, scoped execution commands with commit-before-success semantics."""
from fastapi import APIRouter

from .setup_api import P, DB
from . import same_goal_continuation_execution_contracts_v314 as c
from . import same_goal_continuation_execution_sources_v314 as sources
from . import same_goal_continuation_execution_store_v314 as store

router = APIRouter()
BASE = '/tasks/{identity}/business-advice/preparations/{advice_id}/continuations/{continuation_id}/executions'


@router.get(BASE + '/context')
def context(identity: str, advice_id: str, continuation_id: str, p: P, db: DB):
    return sources.context(db, p, identity, advice_id, continuation_id)


@router.post(BASE + '/preview')
def preview(identity: str, advice_id: str, continuation_id: str, body: c.PreviewRequest, p: P, db: DB):
    value = sources.build_proposal(db, p, identity, advice_id, continuation_id, body.model_dump(mode='json'))
    return sources.proposal_view(value['proposal'])


@router.get(BASE + '/operations/{request_id}')
def original(identity: str, advice_id: str, continuation_id: str, request_id: str, p: P, db: DB):
    return store.get_operation(db, p, identity, advice_id, continuation_id, request_id)


@router.get(BASE)
def listing(identity: str, advice_id: str, continuation_id: str, p: P, db: DB):
    return store.listing(db, p, identity, advice_id, continuation_id)


@router.post(BASE, status_code=201)
def submit(identity: str, advice_id: str, continuation_id: str, body: c.SubmitRequest, p: P, db: DB):
    value = store.submit(db, p, identity, advice_id, continuation_id, body.model_dump(mode='json'))
    db.commit(); return value


@router.get(BASE + '/{plan_id}')
def read(identity: str, advice_id: str, continuation_id: str, plan_id: str, p: P, db: DB):
    return store.get(db, p, identity, advice_id, continuation_id, plan_id)


def command(operation, identity, advice_id, continuation_id, plan_id, body, p, db):
    value = getattr(store, operation)(db, p, identity, advice_id, continuation_id, plan_id, body.model_dump(mode='json'))
    db.commit(); return value


@router.post(BASE + '/{plan_id}/decide')
def decide(identity: str, advice_id: str, continuation_id: str, plan_id: str, body: c.DecideRequest, p: P, db: DB):
    return command('decide', identity, advice_id, continuation_id, plan_id, body, p, db)


@router.post(BASE + '/{plan_id}/start')
def start(identity: str, advice_id: str, continuation_id: str, plan_id: str, body: c.StartRequest, p: P, db: DB):
    return command('start', identity, advice_id, continuation_id, plan_id, body, p, db)


@router.post(BASE + '/{plan_id}/cancel')
def cancel(identity: str, advice_id: str, continuation_id: str, plan_id: str, body: c.CancelRequest, p: P, db: DB):
    return command('cancel', identity, advice_id, continuation_id, plan_id, body, p, db)


@router.post(BASE + '/{plan_id}/review')
def review(identity: str, advice_id: str, continuation_id: str, plan_id: str, body: c.ReviewRequest, p: P, db: DB):
    return command('review', identity, advice_id, continuation_id, plan_id, body, p, db)


@router.post(BASE + '/{plan_id}/recover')
def recover(identity: str, advice_id: str, continuation_id: str, plan_id: str, body: c.RecoverRequest, p: P, db: DB):
    return command('recover', identity, advice_id, continuation_id, plan_id, body, p, db)
