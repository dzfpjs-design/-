"""Normal task-scoped tool grants; these routes never approve or run a tool."""
from fastapi import APIRouter

from . import same_goal_continuation_grant_contracts_v313 as contracts
from . import same_goal_continuation_grant_sources_v313 as sources
from . import same_goal_continuation_grant_store_v313 as store
from .setup_api import P, DB

router = APIRouter()
BASE = '/tasks/{identity}/business-advice/preparations/{advice_id}/continuations/{continuation_id}/tool-grants'


@router.get(BASE + '/context')
def context(identity: str, advice_id: str, continuation_id: str, p: P, db: DB):
    return sources.context(db, p, identity, advice_id, continuation_id)


@router.get(BASE + '/operations/{request_id}')
def original(identity: str, advice_id: str, continuation_id: str, request_id: str, p: P, db: DB):
    return store.get_operation(db, p, identity, advice_id, continuation_id, request_id)


@router.get(BASE)
def listing(identity: str, advice_id: str, continuation_id: str, p: P, db: DB):
    return store.listing(db, p, identity, advice_id, continuation_id)


@router.post(BASE, status_code=201)
def issue(identity: str, advice_id: str, continuation_id: str,
          body: contracts.IssueRequest, p: P, db: DB):
    result = store.issue(db, p, identity, advice_id, continuation_id, body.model_dump(mode='json'))
    db.commit()
    return result


@router.get(BASE + '/{grant_id}')
def read(identity: str, advice_id: str, continuation_id: str, grant_id: str, p: P, db: DB):
    return store.get(db, p, identity, advice_id, continuation_id, grant_id)


@router.post(BASE + '/{grant_id}/revoke')
def revoke(identity: str, advice_id: str, continuation_id: str, grant_id: str,
           body: contracts.RevokeRequest, p: P, db: DB):
    result = store.revoke(db, p, identity, advice_id, continuation_id, grant_id, body.model_dump(mode='json'))
    db.commit()
    return result
