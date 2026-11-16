import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import session
from .config import settings
from .models import User, Membership, AuthSession, now

hasher = PasswordHasher()


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def session_restore_csrf(cookie,row):
    """Session-bound CSRF only; the HttpOnly cookie remains the authentication proof."""
    message=b'ga-session-csrf-restore-v1\x00'+row.id.encode()+b'\x00'+row.csrf_hash.encode('ascii')
    return 'ga_csrf_restore_v1.'+hmac.new(cookie.encode(),message,hashlib.sha256).hexdigest()


def password_hash(value):
    if len(value) < 12:
        raise ValueError("Password must contain at least 12 characters")
    return hasher.hash(value)


def verify_password(encoded, value):
    try:
        return hasher.verify(encoded, value)
    except (VerificationError, InvalidHashError):
        return False


def new_session(db, user, kind="cookie"):
    token = secrets.token_urlsafe(40)
    csrf = secrets.token_urlsafe(32)
    row = AuthSession(user_id=user.id, token_hash=token_hash(token), csrf_hash=token_hash(csrf),
                      expires_at=now() + timedelta(hours=settings().session_hours), kind=kind)
    db.add(row); db.flush()
    return row, token, csrf


@dataclass(frozen=True)
class Principal:
    user_id: str
    workspace_id: str
    role: str
    session_id: str


def authenticated(request: Request, db: Session = Depends(session)):
    authorization = request.headers.get("authorization", "")
    bearer = authorization.startswith("Bearer ")
    token = authorization[7:] if bearer else request.cookies.get(settings().session_cookie_name, "")
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token),
                    AuthSession.revoked.is_(False), AuthSession.expires_at > now()))
    if not row or (bearer and row.kind != "api") or (not bearer and row.kind != "cookie"):
        raise HTTPException(401, "Authentication required")
    user = db.get(User, row.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Account unavailable")
    if request.method not in ["GET", "HEAD", "OPTIONS"] and not bearer:
        csrf = request.headers.get("x-csrf-token", "")
        original_valid=secrets.compare_digest(token_hash(csrf),row.csrf_hash)
        restored_valid=secrets.compare_digest(csrf.encode(),session_restore_csrf(token,row).encode('ascii'))
        if not (original_valid or restored_valid):
            raise HTTPException(403, "CSRF validation failed")
        origin = request.headers.get("origin")
        if origin and origin not in settings().allowed_origins.split(","):
            raise HTTPException(403, "Origin not allowed")
    memberships = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    selected = request.headers.get("x-workspace-id")
    membership = next((m for m in memberships if m.workspace_id == selected), None) if selected else next(iter(memberships), None)
    if not membership:
        raise HTTPException(403, "Workspace access denied")
    return Principal(user.id, membership.workspace_id, membership.role, row.id)


def require(principal, *roles):
    if principal.role not in roles:
        raise HTTPException(403, "Role does not permit this operation")
