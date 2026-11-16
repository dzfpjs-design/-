"""Human-readable delivery status and explicit same-revision data retry."""
import copy
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .setup_api import P, DB
from .models import Event
from .services import scoped
from . import media_daily_delivery as service

router = APIRouter(prefix='/api/media-daily')


@router.get('/{modality}/{daily_id}')
def deliveries(modality: str, daily_id: str, p: P, db: DB):
    if modality not in service.DAILIES:
        raise HTTPException(404, '没有此类每日整理')
    daily = scoped(db, service.DAILIES[modality], daily_id, p)
    events = list(db.scalars(select(Event).where(Event.workspace_id == p.workspace_id,
        Event.kind == service.KIND, Event.data['daily_id'].as_string() == daily.id,
        Event.data['modality'].as_string() == modality).order_by(Event.created_at.desc()).limit(100)))
    rows = []
    for event in events:
        try:
            rows.append(service.view(db, p, event))
        except (HTTPException, KeyError, ValueError, TypeError):
            rows.append({'event_id': event.id, 'version': event.version,
                'revision': None, 'status': 'integrity_error', 'can_retry': False,
                'error': '原发布或独立接收审计无法核对，未展示为已收讫；原记录保留'})
    current = next((row for row in rows if row['revision'] == daily.version), None)
    if current is None:
        current = {'revision': daily.version, 'status': 'not_published', 'can_retry': False,
            'error': '已保存整理候选；实际来源或发布依据待恢复，尚未发布独立接收请求。请核对原处理后重新整理。'}
    return {'schema': 'media-daily-delivery-status-v203', 'daily_id': daily.id,
        'modality': modality, 'daily_version': daily.version, 'current': current,
        'revisions': rows, 'not_authoritative': True, 'physical_state_verified': False}


@router.post('/events/{identity}/retry')
def retry(identity: str, body: service.Retry, p: P, db: DB):
    event = scoped(db, Event, identity, p, True)
    value = service.retry(db, p, event, body)
    db.commit()
    return value


@router.get('/events/{identity}/requests/{request_id}')
def request(identity: str, request_id: UUID, p: P, db: DB):
    event = scoped(db, Event, identity, p)
    proof = service.checked(db, p, event)
    matches = [row for row in proof['history'] if row.data['operation'] == 'retry'
        and row.data['command']['request_id'] == str(request_id)]
    service.need(len(matches) <= 1)
    if matches and matches[0].actor_id != p.user_id:
        raise HTTPException(403, '请由原提交账号核对接收重试请求')
    return {'schema': 'media-daily-retry-request-v203', 'found': bool(matches),
        'body': copy.deepcopy(matches[0].data['command']) if matches else None,
        'result': service.view(db, p, event) if matches else None}
