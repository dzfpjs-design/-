"""Normal V208-ledger preview, adoption and original-command recovery routes."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from . import authored_decision_workflow_store_v278 as store
from .authored_workflow_api_v208 import raw_body
from .authored_workflow_store_v208 import Hash
from .db import session
from .security import Principal, authenticated

router = APIRouter(prefix=store.PREFIX)
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


@router.get('/context')
def context(p: P, db: DB, business_name: str = Query(min_length=1, max_length=100)):
    return store.context(db, p, business_name)


@router.post('/preview')
async def preview(request: Request, p: P, db: DB):
    body = store.parse(await raw_body(request), store.Preview)[0]
    return await run_in_threadpool(store.preview, db, p, body)


@router.post('/adopt', status_code=201)
async def adopt(request: Request, p: P, db: DB):
    return await run_in_threadpool(store.adopt, db, p, await raw_body(request))


@router.get('/requests/{identity}')
def recover(identity: UUID, p: P, db: DB, expected_hash: Hash):
    result = store.recover(db, p, str(identity), expected_hash)
    db.commit()
    return result


@router.get('/adoptions/{identity}')
def get_adoption(identity: UUID, p: P, db: DB):
    return store.get_adoption(db, p, str(identity))


# Keep original paths intact while exposing the new fixed family separately.
from .authored_decision_workflow_api_v286 import router as _review_router
_legacy_router = router
router = APIRouter()
router.include_router(_legacy_router)
router.include_router(_review_router)
from .business_decision_review_api_v284 import router as _context_router
from .monitor_policy_options_v287 import router as _policy_options_router
router.include_router(_context_router)
router.include_router(_policy_options_router, prefix='/api/workspace')
