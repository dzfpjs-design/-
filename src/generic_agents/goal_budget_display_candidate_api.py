"""Display-only Goal counters; never an execution authority."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .config import settings
from .db import engine
from .goal_budget_snapshot_candidate_v2 import read_snapshot
from .security import authenticated

router = APIRouter(prefix='/api/workspace/tasks')


def display_snapshot(request: Request):
    """One private read transaction; authenticate only after it begins."""
    if not settings().goal_budget_display_preview_enabled:
        raise HTTPException(404, '目标计数展示暂不可用')
    bind = engine()
    if bind.dialect.name != 'postgresql':
        raise HTTPException(503, '目标计数暂不可用，请稍后重试')
    with Session(bind=bind, autoflush=False, expire_on_commit=False) as snapshot:
        snapshot.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        # First SELECT establishes the snapshot and authenticates the actual
        # presented cookie/token, user, selected workspace, role and expiry.
        principal = authenticated(request, snapshot)
        yield snapshot, principal


@router.get('/{identity:uuid}/goal/budget-display')
def goal_budget_display(identity: UUID, request: Request, response: Response,
                        context: Annotated[tuple, Depends(display_snapshot)]):
    """Show committed Goal totals from one fresh snapshot; no write authority."""
    response.headers['Cache-Control'] = 'no-store, private'
    snapshot, principal = context
    observed_at = snapshot.scalar(select(func.clock_timestamp()))
    from .workspace_api import saved_execution
    task, _, _ = saved_execution(snapshot, principal, str(identity))
    if not task.data.get('native_goal_context'):
        raise HTTPException(409, '目标尚未确认，暂不能查看计数')
    counts = read_snapshot(snapshot, principal, task)
    # Natural expiry is checked again after the full read. Concurrent role or
    # token revocation is visible to the next fresh request/snapshot.
    if authenticated(request, snapshot) != principal:
        raise HTTPException(403, '账号或工作区权限已变化，请重新核对')
    return {'task_id': task.id, 'calls': counts['calls'], 'steps': counts['steps'],
        'observed_at': observed_at.isoformat(), 'basis': 'saved_snapshot'}
