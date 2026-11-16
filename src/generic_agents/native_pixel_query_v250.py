"""Typed, bounded, SELECT-only machine queries; never ordinary field facts."""
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select, or_, and_

from . import models as m
from .services import scoped
from .setup_api import P, DB
from .native_pixel_provider_v250 import result, ASSOCIATION
from . import native_pixel_provenance_v250 as origin
from .native_pixel_daily_v250 import projection as daily_projection
from .native_pixel_daily_rebuild_v250 import Rebuild, rebuild, lookup

# The unprefixed workspace aggregate mounts these siblings with an explicit
# /api/workspace prefix once. Keep this router relative.
router = APIRouter()


def _guard_inventory(db, p, instance_id, task_id, entity_id):
    """A missing producer row must not silently disappear from a listing."""
    for action, run_key in (('workflow_pixel.input_frozen_v250', None),
                            ('workflow_pixel.result_frozen_v250', None),
                            (origin.ISSUED_ACTION, 'run_id')):
        identity = m.Audit.object_id if run_key is None else m.Audit.data[run_key].as_string()
        statement = select(m.Audit.id).join(m.Task, m.Task.id == m.Audit.data['task_id'].as_string())\
            .outerjoin(m.WorkflowPixelRun, m.WorkflowPixelRun.id == identity).where(
                m.Audit.workspace_id == p.workspace_id, m.Audit.action == action,
                m.Task.workspace_id == p.workspace_id, m.Task.data['instance_id'].as_string() == instance_id,
                or_(m.WorkflowPixelRun.id.is_(None), m.WorkflowPixelRun.workspace_id != p.workspace_id,
                    m.WorkflowPixelRun.task_id != m.Task.id))
        if task_id is not None:
            statement = statement.where(m.Task.id == task_id)
        if entity_id is not None:
            statement = statement.where(m.Task.data['entity_ids'].contains([entity_id]))
        origin.need(db.scalar(statement.limit(1)) is None)


def query(db, p, instance_id, *, task_id=None, entity_id=None, limit=20, before_id=None):
    instance = scoped(db, m.DomainInstance, instance_id, p)
    origin.need(type(limit) is int and 1 <= limit <= 50)
    if task_id is not None:
        task = scoped(db, m.Task, task_id, p)
        origin.need(task.data.get('instance_id') == instance_id)
        from .workflow_pixel_runs_v250 import runs_for_task
        runs_for_task(db, p, task)
    if entity_id is not None:
        entity = scoped(db, m.Entity, entity_id, p)
        origin.need(entity.namespace == instance.namespace)
    _guard_inventory(db, p, instance_id, task_id, entity_id)
    statement = select(m.WorkflowPixelRun).join(m.Task, m.Task.id == m.WorkflowPixelRun.task_id).where(
        m.WorkflowPixelRun.workspace_id == p.workspace_id, m.Task.workspace_id == p.workspace_id,
        m.Task.data['instance_id'].as_string() == instance_id)
    if task_id is not None:
        statement = statement.where(m.WorkflowPixelRun.task_id == task_id)
    if entity_id is not None:
        statement = statement.where(m.Task.data['entity_ids'].contains([entity_id]))
    if before_id is not None:
        cursor = scoped(db, m.WorkflowPixelRun, before_id, p)
        task = scoped(db, m.Task, cursor.task_id, p)
        origin.need(task.data.get('instance_id') == instance_id
            and (task_id is None or cursor.task_id == task_id)
            and (entity_id is None or entity_id in task.data.get('entity_ids', [])))
        statement = statement.where(or_(m.WorkflowPixelRun.created_at < cursor.created_at,
            and_(m.WorkflowPixelRun.created_at == cursor.created_at, m.WorkflowPixelRun.id < cursor.id)))
    rows = list(db.scalars(statement.order_by(m.WorkflowPixelRun.created_at.desc(), m.WorkflowPixelRun.id.desc())
                           .limit(limit + 1)))
    values = [result(db, p, run, data_only=True) for run in rows[:limit]]
    return {'schema': 'native-pixel-machine-results-v250', 'workspace_id': p.workspace_id,
        'instance_id': instance_id, 'task_id': task_id, 'entity_id': entity_id, 'records': values,
        'next_before_id': rows[limit - 1].id if len(rows) > limit else None,
        'has_more': len(rows) > limit, 'not_authoritative': True, 'coordinate_space': 'pixel',
        'association_basis': ASSOCIATION}


@router.get('/instances/{instance_id}/pixel-machine-results')
def results(instance_id: str, p: P, db: DB, task_id: str | None = None,
            entity_id: str | None = None, before_id: str | None = None,
            limit: Annotated[int, Query(ge=1, le=50)] = 20):
    return query(db, p, instance_id, task_id=task_id, entity_id=entity_id, limit=limit, before_id=before_id)


@router.get('/instances/{instance_id}/pixel-machine-daily')
def daily(instance_id: str, registration_day: str, p: P, db: DB):
    return daily_projection(db, p, instance_id, registration_day)


@router.post('/instances/{instance_id}/pixel-machine-daily/rebuild')
def rebuild_daily(instance_id: str, body: Rebuild, p: P, db: DB):
    value = rebuild(db, p, instance_id, body)
    db.commit()
    return value


@router.get('/instances/{instance_id}/pixel-machine-daily/rebuild-requests')
def recover_daily(instance_id: str, p: P, db: DB,
        request_id: Annotated[str, Query(min_length=12, max_length=100)],
        expected_body_hash: Annotated[str, Query(pattern=r'^[a-f0-9]{64}$')]):
    return lookup(db, p, instance_id, request_id, expected_body_hash)
