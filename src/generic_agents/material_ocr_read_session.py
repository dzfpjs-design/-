"""One immutable database snapshot per OCR GET, separate from authentication.

Authentication keeps its existing checks and returns its completed read connection
before the OCR transaction: PostgreSQL cannot change isolation after a SELECT.
No OCR writer uses this dependency; its connection never commits.
"""
from fastapi import Depends
from sqlalchemy.orm import Session

from .db import engine
from .security import Principal
from .snapshot_authentication_v141 import snapshot_authenticated


def read_session(_principal: Principal = Depends(snapshot_authenticated)):
    # Set isolation before BEGIN; READ ONLY is the first statement of the new
    # transaction, before a material, run, unit, version or evidence is loaded.
    with engine().connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection, expire_on_commit=False, autoflush=False,
                         join_transaction_mode="rollback_only") as db:
                yield db
        finally:
            if transaction.is_active:
                transaction.rollback()
