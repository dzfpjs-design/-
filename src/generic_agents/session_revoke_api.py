"""Revoke the authenticated cookie session without changing browser cookies."""
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .db import session
from .models import AuthSession
from .security import Principal, authenticated, token_hash
from .services import audit
from .config import settings


router = APIRouter()
DB = Annotated[object, Depends(session)]
NO_STORE = {'Cache-Control': 'private, no-store'}


def reject(status, detail):
    raise HTTPException(status, detail, headers=dict(NO_STORE))


def cookie_principal(request: Request, db: DB):
    if 'authorization' in request.headers or not request.cookies.get(settings().session_cookie_name):
        reject(401, 'Cookie session required')
    try:
        return authenticated(request, db)
    except HTTPException as exc:
        # Keep existing authentication/CSRF/Origin/membership decisions. This
        # endpoint supplies no cookie headers, including on rejected requests.
        reject(exc.status_code, exc.detail)


P = Annotated[Principal, Depends(cookie_principal)]


async def empty_request(request: Request):
    if request.query_params:
        reject(422, 'Session revocation takes no body or query target')
    async for chunk in request.stream():
        if chunk:
            reject(422, 'Session revocation takes no body or query target')


@router.post('/session/revoke')
def revoke_current_session(request: Request, p: P, db: DB,
                           _empty: Annotated[None, Depends(empty_request)]):
    try:
        row = db.scalar(select(AuthSession).where(AuthSession.id == p.session_id)
            .with_for_update().execution_options(populate_existing=True))
        if (not row or row.id != p.session_id or row.user_id != p.user_id
                or row.kind != 'cookie'
                or not secrets.compare_digest(row.token_hash,
                    token_hash(request.cookies.get(settings().session_cookie_name, '')))):
            reject(401, 'Current cookie session unavailable')
        # A second request already authenticated before this row was revoked
        # may arrive after the lock. Fresh requests remain rejected by auth.
        if not row.revoked:
            row.revoked = True
            audit(db, p, 'session.current_revoked', row.id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        reject(500, 'Current session revocation not confirmed')
    return JSONResponse({'revoked': True}, headers=dict(NO_STORE))
