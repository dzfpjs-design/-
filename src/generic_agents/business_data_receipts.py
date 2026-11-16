"""Independent receipt verification and workspace-only business-data day ledger."""
import copy
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import business_data as b, models as m
from .business_data_models import BusinessDataSource as Source, BusinessDataRead as Read, BusinessDataBinding as Binding
from .business_data_models import BusinessDataImport as Import, BusinessDataImportRow as ImportRow, BusinessDataRecordLink as Link
from .contracts import digest
from .db import engine
from .security import Principal
from .services import audit, scoped

ACK = 'business_data.received_v204'
DAY = 'business-data-daily-v204'
DAY_AUDIT = 'business_data.daily_frozen_v204'


def workspace(value):
    try:
        if type(value) is not str or str(UUID(value)) != value: raise ValueError()
    except (ValueError, TypeError, AttributeError): raise ValueError('业务数据接收必须指定标准工作区UUID') from None
    return value


def verify_import_event(db, p, event):
    b.need(event.workspace_id == p.workspace_id and event.kind == 'business_data.imported'
        and event.data.get('schema') == 'business-data-import-event-v204')
    receipt = event.data['receipt']
    row = scoped(db, ImportRow, receipt['row_id'], p)
    batch = scoped(db, Import, row.import_id, p)
    binding = scoped(db, Binding, batch.binding_id, p)
    read = scoped(db, Read, batch.read_id, p)
    b.checked_read(db, p, read); b.checked_binding(db, p, binding)
    b.checked_preview(db, p, batch, binding, read); pin = b.checked_row(db, p, row)
    b.need(pin in batch.data['preview']['rows'] and row.status == 'imported'
        and b.same(row.data.get('receipt'), receipt) and row.data.get('event_id') == event.id
        and event.idempotency_key == 'business-data-imported:' + row.id
        and receipt['schema'] == 'business-data-row-receipt-v204'
        and receipt['import_id'] == batch.id and receipt['read_id'] == read.id
        and receipt['binding_id'] == binding.id and receipt['source_id'] == batch.source_id == binding.source_id == read.source_id
        and receipt['instance_id'] == binding.instance_id and receipt['release_id'] == binding.release_id
        and receipt['object_type'] == binding.object_type and receipt['schema_hash'] == binding.data['schema_hash']
        and receipt['binding_hash'] == digest(binding.data) and receipt['raw_sha256'] == read.data['raw_sha256']
        and receipt['preview_hash'] == digest(row.preview) and receipt['confirm_hash'] == batch.data['confirm_hash'])
    confirmation = batch.data['confirmation']
    b.anchored(db, p, 'business_data.confirmed_v204', batch.id, confirmation)
    b.need(confirmation['preview_hash'] == receipt['confirm_hash'])
    origin = b.anchored(db, p, b.IMPORTED_AUDIT, row.id, {'event_id': event.id, 'receipt': receipt})
    b.need(origin.actor_id == receipt['import_actor_id'])
    instance = scoped(db, m.DomainInstance, binding.instance_id, p)
    release = scoped(db, m.InstanceRelease, binding.release_id, p)
    b.need(release.instance_id == instance.id and release.configuration_hash == binding.data['configuration_hash']
        and release.configuration_hash == digest(release.data['snapshot'])
        and digest(release.data['snapshot']['entity_schemas'][binding.object_type]) == receipt['schema_hash'])
    group = next(item for item in read.data['parsed']['record_sets'] if item['key'] == binding.data['record_set'])
    record = next(item for item in group['records'] if item['index'] == row.preview['record_index'])
    b.need(digest(record['value']) == row.preview['raw_value_hash'] and record['location'] == row.preview['location']
        and b.external_key(record['value'], binding.data['external_id_path']) == receipt['external_key'] == row.preview['external_key']
        and b.same(b.mapped(record['value'], binding), row.preview['mapped']))
    entity = scoped(db, m.Entity, receipt['entity_id'], p)
    b.need(entity.namespace == instance.namespace and entity.entity_type == binding.object_type
        and entity.external_id == receipt['external_key'] and entity.schema_capability_id == receipt['entity_schema_capability_id']
        and entity.version >= receipt['entity_version'] and b.same(receipt['attributes'], row.preview['after']))
    entity_event = scoped(db, m.EntityEvent, receipt['entity_event_id'], p)
    b.need(entity_event.entity_id == entity.id and b.same(entity_event.data, receipt['entity_event'])
        and entity_event.actor_id == receipt['entity_event_actor']
        and entity_event.data['entity_version'] == receipt['entity_version']
        and b.same(entity_event.data['after'], receipt['attributes']))
    expected_version = 1 if receipt['operation'] == 'new' else row.preview['target_version'] + int(receipt['operation'] == 'updated')
    b.need(receipt['entity_version'] == expected_version and receipt['operation'] == row.preview['status'])
    if receipt['operation'] in {'new', 'updated'}:
        b.need(entity_event.actor_id == receipt['import_actor_id']
            and entity_event.data['operation'] == ('create' if receipt['operation'] == 'new' else 'update'))
    link = scoped(db, Link, receipt['link_id'], p)
    b.need(link.entity_id == entity.id and link.source_id == batch.source_id and link.instance_id == instance.id
        and link.object_type == binding.object_type and link.external_key == receipt['external_key']
        and link.version >= receipt['link_version'] and b.same(receipt['link_snapshot']['mapped'], row.preview['mapped'])
        and receipt['link_snapshot']['entity_event_id'] == entity_event.id
        and receipt['link_snapshot']['entity_hash'] == digest(receipt['attributes']))
    # A later human/managed head does not invalidate an authentic old import.
    return receipt


def verify_link_snapshot(db, p, link):
    row = scoped(db, ImportRow, link.data['import_row_id'], p)
    receipt = verify_import_event(db, p, scoped(db, m.Event, row.data['event_id'], p))
    b.need(receipt['link_id'] == link.id and receipt['link_version'] == link.version
        and b.same(receipt['link_snapshot'], link.data), '原来源关联的上次导入基线无法核对')


@b.projection
def delivery_view(db, p, event):
    previous = event.data.get('delivery_error')
    result = {'event_id': event.id, 'status': event.status, 'version': event.version,
        'can_retry': event.status == 'blocked_delivery',
        'error': previous if event.status == 'blocked_delivery' else None,
        'previous_error': previous if event.status != 'blocked_delivery' else None, 'verified': False}
    if event.status != 'received': return result
    try:
        value = verify_import_event(db, p, event) if event.kind == 'business_data.imported' else verify_day(db, p, event)
        memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
            m.Memory.data['event_id'].as_string() == event.id)))
        b.need(len(memories) == 1 and memories[0].status == 'candidate' and event.received_at is not None)
        expected = {'type': 'business_data_evidence' if event.kind == 'business_data.imported' else 'business_data_daily',
            'event_id': event.id, 'receipt': value, 'not_authoritative': True}
        b.need(b.same(memories[0].data, expected))
        ack = b.anchored(db, p, ACK, event.id, {'memory_id': memories[0].id, 'receipt_hash': digest(value)})
        actor = value['import_actor_id'] if event.kind == 'business_data.imported' else value['actor_id']
        b.need(ack.actor_id == actor)
        result['verified'] = True
    except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError, StopIteration):
        result.update(status='integrity_error', stored_status=event.status, can_retry=False,
            integrity_error=True, error='原接收回执、候选记录或独立审计无法核对；未确认数据已接收')
    return result


def _day_anchor(event): return {'event_id': event.id, 'hash': digest(event.data['receipt'])}


def verify_day(db, p, event):
    value = event.data['receipt']
    b.need(event.workspace_id == p.workspace_id and event.kind == 'business_data.daily' and value['schema'] == DAY
        and event.idempotency_key == 'business-data-day:' + value['source_id'] + ':' + value['day'] + ':' + digest(value))
    b.anchored(db, p, DAY_AUDIT, event.id, _day_anchor(event))
    scoped(db, Source, value['source_id'], p)
    for identity in value['read_ids']:
        read = scoped(db, Read, identity, p); b.need(read.source_id == value['source_id'])
        if read.evidence_id: b.checked_read(db, p, read, parsed=False)
    return value


def _receive(db, p, event):
    value = verify_import_event(db, p, event) if event.kind == 'business_data.imported' else verify_day(db, p, event)
    old = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        m.Memory.data['event_id'].as_string() == event.id)))
    ack = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.object_id == event.id, m.Audit.action == ACK)))
    data = {'type': 'business_data_evidence' if event.kind == 'business_data.imported' else 'business_data_daily',
        'event_id': event.id, 'receipt': copy.deepcopy(value), 'not_authoritative': True}
    if old:
        actor = value['import_actor_id'] if event.kind == 'business_data.imported' else value['actor_id']
        b.need(len(old) == len(ack) == 1 and old[0].status == 'candidate' and ack[0].actor_id == actor and b.same(old[0].data, data)
            and b.same(ack[0].data, {'memory_id': old[0].id, 'receipt_hash': digest(value)}))
    else:
        b.need(not ack)
        memory = m.Memory(workspace_id=p.workspace_id, status='candidate',
            text='业务数据已接收，来源声明仍需业务核查' if event.kind == 'business_data.imported' else '业务数据每日整理已保存（含未完成与冲突）', data=data)
        db.add(memory); db.flush()
        actor = value['import_actor_id'] if event.kind == 'business_data.imported' else value['actor_id']
        audit(db, Principal(actor, p.workspace_id, 'viewer', 'business-data-receiver'), ACK, event.id,
            {'memory_id': memory.id, 'receipt_hash': digest(value)})
    event.status = 'received'; event.received_at = m.now(); event.attempts += 1; event.version += 1


def consume_once(workspace_id):
    workspace_id = workspace(workspace_id)
    with Session(engine()) as db:
        events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == workspace_id,
            m.Event.kind.in_(['business_data.imported', 'business_data.daily']), m.Event.status == 'pending_delivery')
            .order_by(m.Event.created_at, m.Event.id).with_for_update(skip_locked=True).limit(25)))
        for event in events:
            try:
                with db.begin_nested(): _receive(db, Principal('business-data-receiver', workspace_id, 'viewer', 'business-data-receiver'), event)
            except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError, StopIteration):
                event.status = 'blocked_delivery'; event.attempts += 1; event.version += 1
                event.data = {**event.data, 'delivery_error': '原件、映射、对象写入或独立来源依据无法核对；未确认接收'}
        db.commit()
        return len(events)


def _day(read):
    stamp = datetime.fromisoformat(read.data['received_at']) if read.data.get('received_at') else read.created_at
    return str(stamp.astimezone(ZoneInfo('Asia/Shanghai')).date())


def schedule_once(workspace_id):
    workspace_id = workspace(workspace_id)
    after = ''
    while True:
        with Session(engine()) as db:
            sources = list(db.scalars(select(Source.id).where(Source.workspace_id == workspace_id, Source.id > after).order_by(Source.id).limit(64)))
        if not sources: break
        for identity in sources:
            with Session(engine()) as db:
                reads = list(db.scalars(select(Read).where(Read.workspace_id == workspace_id, Read.source_id == identity).order_by(Read.created_at, Read.id)))
                groups = {}
                for read in reads: groups.setdefault(_day(read), []).append(read)
                for day, items in groups.items():
                    key = int(digest({'business_data_day': workspace_id, 'source_id': identity, 'day': day})[:15], 16)
                    if not db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': key}): continue
                    p = Principal(items[0].actor_id, workspace_id, 'viewer', 'business-data-daily')
                    batches = list(db.scalars(select(Import).where(Import.workspace_id == workspace_id,
                        Import.read_id.in_([read.id for read in items])).order_by(Import.created_at, Import.id)))
                    totals = {key: 0 for key in ('new', 'updated', 'unchanged', 'conflict', 'invalid', 'imported', 'data_received', 'failed', 'cancelled')}
                    for batch in batches:
                        for name, value in b.counts(db, p, batch).items(): totals[name] += value
                    totals['read_failures'] = sum(read.status == 'failed' for read in items)
                    totals['unreceived'] = totals['imported'] - totals['data_received']
                    value = {'schema': DAY, 'source_id': identity, 'day': day, 'actor_id': p.user_id,
                        'day_basis': 'original_platform_reception_day_Asia_Shanghai',
                        'read_ids': [read.id for read in items], 'import_ids': [batch.id for batch in batches], 'counts': totals,
                        'read_stages': [{'id': read.id, 'status': read.status, 'raw_sha256': read.data.get('raw_sha256')} for read in items],
                        'import_stages': [{'id': batch.id, 'status': batch.status} for batch in batches],
                        'provider_time_verified': False, 'physical_state_verified': False}
                    nonce = 'business-data-day:' + identity + ':' + day + ':' + digest(value)
                    if db.scalar(select(m.Event.id).where(m.Event.workspace_id == workspace_id, m.Event.idempotency_key == nonce)): continue
                    event = m.Event(workspace_id=workspace_id, kind='business_data.daily', idempotency_key=nonce, data={'receipt': value})
                    db.add(event); db.flush(); audit(db, p, DAY_AUDIT, event.id, _day_anchor(event))
                db.commit()
            after = identity
        if len(sources) < 64: break


@b.projection
def days(db, p, source_id):
    scoped(db, Source, source_id, p)
    events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.kind == 'business_data.daily', m.Event.data['receipt']['source_id'].as_string() == source_id)
        .order_by(m.Event.created_at.desc(), m.Event.id.desc()).limit(100)))
    return [day_view(db, p, event) for event in events]


@b.projection
def day_view(db, p, event):
    delivery = delivery_view(db, p, event)
    return {'id': event.id, 'source_id': event.data['receipt']['source_id'], 'version': event.version,
        'status': delivery['status'], 'data_delivery': delivery, 'day': event.data['receipt']['day'],
        'counts': event.data['receipt']['counts'], 'read_ids': event.data['receipt']['read_ids'],
        'import_ids': event.data['receipt']['import_ids'], 'error': delivery.get('error'), 'physical_state_verified': False}


def retry_day_delivery(db, p, source_id, event_id, body):
    prior = b.request_prior(db, p, body, 'retry_day_delivery:' + source_id + ':' + event_id)
    if prior is not None: return prior
    b.current_actor(db, p)
    scoped(db, Source, source_id, p, True)
    event = scoped(db, m.Event, event_id, p, True)
    from .services import check_version
    check_version(event, body['expected_version'])
    b.need(event.kind == 'business_data.daily' and event.data['receipt']['source_id'] == source_id)
    verify_day(db, p, event)
    b.need(event.status == 'blocked_delivery', '仅能恢复原受阻日整理接收，不重新读取或导入')
    event.status = 'pending_delivery'; event.version += 1
    audit(db, p, 'business_data.daily_delivery_retried_v204', event.id,
        {'reason': body['reason'], 'source_id': source_id, 'receipt_hash': digest(event.data['receipt'])})
    return b.command_saved(db, p, body, day_view(db, p, event))


def retry_delivery(db, p, import_id, row_id, body):
    prior = b.request_prior(db, p, body, 'retry_delivery:' + import_id + ':' + row_id)
    if prior is not None: return prior
    b.current_actor(db, p)
    batch = scoped(db, Import, import_id, p); row = scoped(db, ImportRow, row_id, p)
    b.need(row.import_id == batch.id and row.status == 'imported')
    event = scoped(db, m.Event, row.data['event_id'], p, True)
    from .services import check_version
    check_version(event, body['expected_version'])
    verify_import_event(db, p, event)
    b.need(event.status == 'blocked_delivery', '仅能恢复原受阻接收，不重新导入或提取')
    event.status = 'pending_delivery'; event.version += 1
    audit(db, p, 'business_data.delivery_retried_v204', event.id, {'reason': body['reason'], 'receipt_hash': digest(event.data['receipt'])})
    return b.command_saved(db, p, body, b.row_view(db, p, row))
