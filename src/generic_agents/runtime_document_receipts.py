"""Independent archival custody ACK; complete searchable text is a separate fact."""
import copy
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import runtime_documents as d, models as m
from .runtime_document_models import RuntimeDocumentVersion as Version, RuntimeDocumentParseAttempt as Attempt
from .contracts import digest
from .db import engine
from .security import Principal
from .services import scoped, audit, check_version

ACK = 'runtime_document.received_v205'


def verify_event(db, p, event):
    d.need(event.workspace_id == p.workspace_id and event.kind == 'runtime_document.parsed')
    value = event.data['receipt']; version = scoped(db, Version, value['version_id'], p)
    attempt = scoped(db, Attempt, value['attempt_id'], p); result = d.checked_attempt(db, p, version, attempt)
    expected = {'schema': 'runtime-document-receipt-v205', 'version_id': version.id, 'document_id': version.document_id,
        'instance_id': version.instance_id, 'attempt_id': attempt.id, 'actor_id': attempt.actor_id,
        'raw_sha256': version.data['origin']['raw_sha256'], 'origin_hash': digest(version.data['origin']),
        'attempt_hash': digest(d.attempt_origin(attempt)), 'parse_hash': attempt.data['parse_hash'],
        'status': result['status'], 'blocks_hash': digest(result['blocks']), 'searchable': result['status'] == 'parsed'}
    d.need(d.same(value, expected) and attempt.data['event_id'] == event.id
        and event.idempotency_key == 'runtime-document-result:' + attempt.id)
    return value


def memory_data(event, value):
    return {'type': 'runtime_document_archive', 'event_id': event.id, 'receipt': copy.deepcopy(value),
        'not_authoritative': True, 'searchable': value['searchable']}


def checked_ack(db, p, event, value):
    memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        m.Memory.data['event_id'].as_string() == event.id)))
    d.need(len(memories) == 1 and memories[0].status == 'candidate'
        and d.same(memories[0].data, memory_data(event, value)))
    d.need(d.anchored(db, p, ACK, event.id, {'memory_id': memories[0].id, 'receipt_hash': digest(value)}).actor_id == value['actor_id'])


def delivery_view(db, p, attempt):
    event = scoped(db, m.Event, attempt.data['event_id'], p)
    previous = event.data.get('delivery_error')
    result = {'event_id': event.id, 'version': event.version, 'status': event.status, 'verified': False,
        'can_retry': event.status == 'blocked_delivery', 'error': previous if event.status == 'blocked_delivery' else None,
        'previous_error': previous if event.status != 'blocked_delivery' else None}
    if event.status == 'received':
        try:
            value = verify_event(db, p, event); checked_ack(db, p, event, value)
            d.need(event.received_at is not None); result['verified'] = True
        except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError):
            result.update(status='integrity_error', can_retry=False, error='原件、解析或独立接收依据无法核对；未确认接收')
    return result


def consume_once(workspace_id):
    try:
        if type(workspace_id) is not str or str(UUID(workspace_id)) != workspace_id: raise ValueError()
    except (ValueError, TypeError, AttributeError): raise ValueError('文档接收必须指定标准工作区UUID') from None
    with Session(engine()) as db:
        events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == workspace_id,
            m.Event.kind == 'runtime_document.parsed', m.Event.status == 'pending_delivery')
            .order_by(m.Event.created_at, m.Event.id).with_for_update(skip_locked=True).limit(20)))
        p = Principal('runtime-document-receiver', workspace_id, 'viewer', 'runtime-document-receiver')
        with d.verification_scope(db):
            for event in events:
                try:
                    with db.begin_nested():
                        value = verify_event(db, p, event)
                        old = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == workspace_id,
                            m.Memory.data['event_id'].as_string() == event.id)))
                        if old: checked_ack(db, p, event, value)
                        else:
                            d.need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == workspace_id,
                                m.Audit.action == ACK, m.Audit.object_id == event.id)) is None)
                            memory = m.Memory(workspace_id=workspace_id, status='candidate',
                                text='运行文档原件及解析结果已收档；正文与业务事实仍须核对', data=memory_data(event, value))
                            db.add(memory); db.flush()
                            audit(db, Principal(value['actor_id'], workspace_id, 'viewer', 'runtime-document-receiver'),
                                ACK, event.id, {'memory_id': memory.id, 'receipt_hash': digest(value)})
                        event.status = 'received'; event.version += 1; event.attempts += 1; event.received_at = m.now()
                except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError):
                    event.status = 'blocked_delivery'; event.version += 1; event.attempts += 1
                    event.data = {**event.data, 'delivery_error': '原件、版本、解析或独立来源依据无法核对，尚未确认收档'}
        db.commit(); return len(events)


def retry(db, p, version_id, event_id, body):
    scope = 'receipt:' + version_id + ':' + event_id
    old = d.prior(db, p, body, scope)
    if old is not None: return old
    d.current_actor(db, p)
    version = scoped(db, Version, version_id, p)
    event = scoped(db, m.Event, event_id, p, True); check_version(event, body['expected_version'])
    value = verify_event(db, p, event)
    d.need(value['version_id'] == version.id and event.status == 'blocked_delivery', '仅能恢复原受阻收档接收，不重新解析或上传')
    event.status = 'pending_delivery'; event.version += 1
    audit(db, p, 'runtime_document.delivery_retried_v205', event.id, {'reason': body['reason'], 'receipt_hash': digest(value)})
    return d.saved(db, p, body, scope, d.version_view(db, p, version))
