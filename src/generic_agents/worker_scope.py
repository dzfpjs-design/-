"""Scope secondary reads in periodic work, including stale relationship IDs."""
from fastapi import HTTPException
from sqlalchemy import select


def related(db, model, identity, workspace_id, *, allow_missing=False):
    if workspace_id is None:
        return db.get(model, identity)
    row = db.scalar(select(model).where(model.id == identity, model.workspace_id == workspace_id)
                    .execution_options(populate_existing=True))
    if row is None and allow_missing:
        return None
    if row is None or row.workspace_id != workspace_id:
        raise HTTPException(409, '后台资料的工作区关联无法核对，原记录保留')
    return row
