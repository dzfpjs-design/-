"""Release the completed auth read before a separate read-only snapshot.

Only the dedicated snapshot GET dependencies use this boundary. Original auth,
ordinary request sessions and every writer keep their existing lifecycle.
"""
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from .db import session
from .security import Principal, authenticated


def snapshot_authenticated(principal: Principal = Depends(authenticated),
                           auth_db: Session = Depends(session)):
    # FastAPI's normal dependency cache supplies the very same Session that
    # authenticated used. Principal already contains independent string values;
    # no ORM object, business write or snapshot depends on this transaction.
    if auth_db.new or auth_db.dirty or auth_db.deleted:
        raise HTTPException(409, '认证读取包含尚未完成的修改，未开始资料快照；请重新核对请求')
    # close rolls back the read and returns its connection. Never commit or
    # continue to snapshot checkout if returning the auth connection fails.
    auth_db.close()
    return principal
