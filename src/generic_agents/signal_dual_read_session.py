"""One consistent read-only snapshot per dual-handoff/task detail GET.

Authentication retains its original checks; its completed read connection is
returned before this separate snapshot starts. Authentication SQL must not start
the snapshot or prevent setting repeatable-read isolation in PostgreSQL.
No POST, writer, dispatcher or acknowledgement uses this dependency.
"""
from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import select
from fastapi import HTTPException

from .db import engine
from .security import Principal
from .snapshot_authentication_v141 import snapshot_authenticated
from .models import User, Membership


def read_actor(db, workspace_id, user_id):
    """The same eligibility predicate as routing.actor, without a GET row lock.

    This is only the choices projection, never an authorization to create or
    dispatch a Task. Fresh writes retain the original actor FOR SHARE check.
    """
    row = db.execute(select(User.id, Membership.role).join(Membership,Membership.user_id == User.id)
        .where(User.id == user_id,User.active.is_(True),Membership.workspace_id == workspace_id)).first()
    if not row or row.role not in {'admin','operator'}:
        raise HTTPException(403,'路由授权人已停用或失去资料/业务操作权限')
    return Principal(user_id,workspace_id,row.role,'explicit-signal-routing')


def read_session(_principal: Principal = Depends(snapshot_authenticated)):
    with engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        transaction = connection.begin()
        try:
            # First application SQL, before any parent/Task/receipt is loaded.
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            with Session(bind=connection, expire_on_commit=False, autoflush=False,
                         join_transaction_mode='rollback_only') as db:
                from .report_full_source_read_scope import _attested_task_read_session
                with _attested_task_read_session(db, _principal, connection, transaction):
                    yield db
        finally:
            if transaction.is_active:
                transaction.rollback()
