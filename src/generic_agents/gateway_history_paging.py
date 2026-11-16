"""Stable workspace-scoped cursor pagination for file gateway records."""
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import or_,and_,select
from .models import Job

PAGE_SIZE=100

def page(db, filters, cursor=None):
    if cursor:
        try:
            if not isinstance(cursor,str) or str(UUID(cursor))!=cursor:raise ValueError()
        except (TypeError,ValueError):raise HTTPException(422,'历史位置无效，请重新读取') from None
        anchor=db.scalar(select(Job).where(*filters,Job.id==cursor))
        if not anchor:raise HTTPException(409,'历史位置已变化，请重新读取')
        filters=[*filters,or_(Job.created_at<anchor.created_at,and_(Job.created_at==anchor.created_at,Job.id<anchor.id))]
    rows=list(db.scalars(select(Job).where(*filters).order_by(Job.created_at.desc(),Job.id.desc()).limit(PAGE_SIZE+1)))
    return rows[:PAGE_SIZE],rows[PAGE_SIZE-1].id if len(rows)>PAGE_SIZE else None
