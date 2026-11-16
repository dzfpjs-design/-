"""Independent raw/result custody, never an OCR-accuracy or business-fact ACK."""
import copy
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import runtime_document_ocr as o, models as m
from .runtime_document_ocr_models import RuntimeDocumentOcrResult as Result
from .contracts import digest
from .db import engine
from .security import Principal
from .services import scoped, audit, check_version

ACK = 'runtime_document_ocr.received_v206'


def verify_event(db, p, event):
    o.need(event.workspace_id == p.workspace_id and event.kind == 'runtime_document.ocr_result')
    value = event.data['receipt']; result = scoped(db, Result, value['result_id'], p)
    run, page, outcome = o.checked_result(db, p, result)
    o.need(result.data['event_id'] == event.id and event.idempotency_key == 'runtime-document-ocr-result:' + result.id
        and o.same(value, o.receipt(run, page, result)))
    return value


def payload(event, value):
    return {'type': 'runtime_document_ocr_archive', 'event_id': event.id, 'receipt': copy.deepcopy(value),
        'not_authoritative': True, 'unreviewed': True, 'searchable': value['status'] == 'text'}


def checked_ack(db, p, event, value):
    rows = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        m.Memory.data['event_id'].as_string() == event.id)))
    o.need(len(rows) == 1 and rows[0].status == 'candidate' and o.same(rows[0].data, payload(event, value)))
    o.need(o.anchored(db, p, ACK, event.id, {'memory_id': rows[0].id, 'receipt_hash': digest(value)}).actor_id == value['actor_id'])


def delivery_view(db, p, result):
    event = scoped(db, m.Event, result.data['event_id'], p); previous = event.data.get('delivery_error')
    value = {'event_id': event.id, 'version': event.version, 'status': event.status, 'verified': False,
        'can_retry': event.status == 'blocked_delivery', 'error': previous if event.status == 'blocked_delivery' else None,
        'previous_error': previous if event.status != 'blocked_delivery' else None}
    if event.status == 'received':
        try:
            receipt = verify_event(db, p, event); checked_ack(db, p, event, receipt)
            o.need(event.received_at is not None); value['verified'] = True
        except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError):
            value.update(status='integrity_error', can_retry=False, error='原件、页图、结果或独立接收依据无法核对')
    return value


def consume_once(workspace_id):
    try:
        if type(workspace_id) is not str or str(UUID(workspace_id)) != workspace_id: raise ValueError()
    except (ValueError, TypeError, AttributeError): raise ValueError('运行OCR接收必须指定标准工作区UUID') from None
    with Session(engine()) as db:
        events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == workspace_id,
            m.Event.kind == 'runtime_document.ocr_result', m.Event.status == 'pending_delivery')
            .order_by(m.Event.created_at, m.Event.id).with_for_update(skip_locked=True).limit(20)))
        p = Principal('runtime-ocr-receiver', workspace_id, 'viewer', 'runtime-ocr-receiver')
        with o.native.verification_scope(db):
            for event in events:
                try:
                    with db.begin_nested():
                        value = verify_event(db, p, event)
                        rows = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == workspace_id,
                            m.Memory.data['event_id'].as_string() == event.id)))
                        if rows: checked_ack(db, p, event, value)
                        else:
                            o.need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == workspace_id,
                                m.Audit.action == ACK, m.Audit.object_id == event.id)) is None)
                            memory = m.Memory(workspace_id=workspace_id, status='candidate',
                                text='运行文档OCR原件及结果已收档；机器识别尚未经人工核对', data=payload(event, value))
                            db.add(memory); db.flush()
                            audit(db, Principal(value['actor_id'], workspace_id, 'viewer', 'runtime-ocr-receiver'), ACK,
                                event.id, {'memory_id': memory.id, 'receipt_hash': digest(value)})
                        event.status = 'received'; event.version += 1; event.attempts += 1; event.received_at = m.now()
                except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError):
                    event.status = 'blocked_delivery'; event.version += 1; event.attempts += 1
                    event.data = {**event.data, 'delivery_error': '原件、页图、引擎回执或独立来源无法核对，尚未确认收档'}
        db.commit(); return len(events)


def retry(db, p, identity, event_id, body):
    scope = 'receipt:' + identity + ':' + event_id
    old = o.prior(db, p, body, scope)
    if old is not None: return old
    o.native.current_actor(db, p)
    result = scoped(db, Result, identity, p); event = scoped(db, m.Event, event_id, p, True)
    check_version(event, body['expected_version']); value = verify_event(db, p, event)
    o.need(value['result_id'] == result.id and event.status == 'blocked_delivery', '仅重试原受阻接收，不重新识别')
    event.status = 'pending_delivery'; event.version += 1
    audit(db, p, 'runtime_document_ocr.delivery_retried_v206', event.id, {'reason': body['reason'], 'receipt_hash': digest(value)})
    return o.saved(db, p, body, scope, o.result_view(db, p, result))
