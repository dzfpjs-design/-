"""Frozen media-day outbox and independent data receiver, without inference.

The producer's existing candidate Memory is a collection snapshot, not an ACK.
Each actual Daily version gets one immutable Event and publication Audit. A
separate transaction validates originals and writes the data Memory + ACK.
Late source changes create a new revision; old payloads and ACKs stay intact.
"""
import copy
from datetime import timezone
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import and_, exists, func, or_, select, text
from sqlalchemy.orm import Session

from .contracts import Contract, digest
from .models import (AudioDaily, GeoDaily, VideoDaily, AudioRun, SpeechActivityRun,
    SegmentedAudioRun, GeoScene, GeoRun, GeoPair, VideoRun, StreamSession,
    Evidence, Event, Memory, Job, Audit, User, now)
from .security import Principal
from .services import scoped, audit, require
from .business_flow_review_store import _bytes, _same
from .sound_models import EnvironmentSoundRun

KIND = 'media.daily'
PAYLOAD = 'media-daily-frozen-revision-v203'
PUBLICATION = 'media.daily.published_v203'
TRANSITION = 'media.daily.delivery_v203'
RECEIPT = 'media-daily-data-receipt-v203'
ERROR = '原每日整理、原件、实际来源或独立接收审计无法核对；原记录保留'
DAILIES = {'video': VideoDaily, 'audio': AudioDaily, 'imagery': GeoDaily}
SOURCES = {'video_upload': (VideoRun, 'run_id'), 'video_capture': (StreamSession, 'session_id'),
    'transcription': (AudioRun, 'run_id'), 'activity': (SpeechActivityRun, 'run_id'),
    'segments': (SegmentedAudioRun, 'run_id'), 'environment_sound': (EnvironmentSoundRun, 'task_id'), 'scene': (GeoScene, 'scene_id'),
    'segmentation': (GeoRun, 'run_id'), 'comparison': (GeoPair, 'pair_id')}
SOURCE_ACTIONS = {'video.queued', 'media.capture.queued', 'audio.queued', 'audio.retry',
    'audio.activity.queued', 'audio.segments.queued', 'audio.workflow_input_frozen_v136',
    'audio.transcription.saved', 'audio.activity.saved', 'audio.segments.saved',
    'geo.scene_queued', 'geo.rgb_confirmed', 'geo.analysis_queued', 'geo.pair_queued',
    'geo.scene_checked', 'geo.candidates_saved', 'geo.pair_saved'}


class Retry(Contract):
    request_id: UUID
    expected_version: int = Field(strict=True, ge=1)
    payload_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(min_length=1, max_length=1000)


def need(ok, message=ERROR):
    if not ok:
        raise HTTPException(409, message)


def workspace(value):
    need(type(value) is str and str(UUID(value)) == value)
    return value


def stamp(value):
    if value is None:
        return None
    need(value.tzinfo is not None)
    return value.astimezone(timezone.utc).isoformat()


def summary_value(daily):
    return {key: copy.deepcopy(value) for key, value in daily.data.items()
        if key not in {'receipt_hash', 'data_memory_id', 'updated_at', 'recovery_history'}}


def _source_rows(db, p, modality, record):
    name = ('video_upload' if record['mode'] == 'upload' else 'video_capture') if modality == 'video' else record['kind']
    if name=='environment_sound':
        from .sound_daily import source_pin
        return source_pin(db,p,record['id'],saved_record=record)
    cls, field = SOURCES[name]
    row = scoped(db, cls, record['id'], p)
    link = 'workflow_task_id' if row.data.get('workflow_task_id') else 'continuous_id' if row.data.get('continuous_id') else None
    field = 'task_id' if link == 'workflow_task_id' else 'stream_id' if link == 'continuous_id' else field
    job_value = row.data[link] if link else row.id
    job_id = row.data.get('job_id') or row.data.get('workflow_job_id')
    job = scoped(db, Job, job_id, p) if job_id else db.scalar(
        select(Job).where(Job.workspace_id == p.workspace_id, Job.data[field].as_string() == job_value)
        .order_by(Job.created_at).limit(1))
    need(job is not None and job.workspace_id == p.workspace_id and job.data.get(field) == job_value)
    actor = job.data.get('actor_id')
    need(type(actor) is str and db.scalar(select(User.id).where(User.id == actor)) == actor)
    anchor = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == row.id, Audit.actor_id == actor, Audit.action.in_(SOURCE_ACTIONS))
        .order_by(Audit.created_at).limit(1))
    if anchor is None and link == 'workflow_task_id':
        anchor = db.scalar(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == job_value, Audit.actor_id == actor,
            Audit.action == 'workflow.graph.dispatch_authorized',
            Audit.data['job_id'].as_string() == job.id).order_by(Audit.created_at).limit(1))
    need(anchor is not None)
    if modality == 'imagery':
        from .geo_daily import sources
        refs = [{'id': item['evidence_id'], 'sha256': item['sha256']} for item in sources(db, p, name, row)]
    elif modality == 'audio':
        source = row.data.get('source', row.data)
        identity = source.get('evidence_id') or getattr(row, 'evidence_id', None)
        refs = [{'id': identity, 'sha256': source.get('input_sha256')}] if identity else []
    else:
        identity = row.evidence_id
        refs = [{'id': identity, 'sha256': scoped(db, Evidence, identity, p).sha256}] if identity else []
        if name == 'video_upload':
            need(bool(refs) and row.data.get('input_sha256') == refs[0]['sha256'])
    for ref in refs:
        evidence = scoped(db, Evidence, ref['id'], p)
        need(evidence.workspace_id == p.workspace_id and ref['sha256'] == evidence.sha256)
    if anchor.data.get('input_sha256') is not None:
        need(bool(refs) and anchor.data['input_sha256'] == refs[0]['sha256'])
    return {'kind': name, 'id': row.id, 'source_version': row.version,
        'job_id': job.id, 'job_key': job.idempotency_key, 'actor_id': actor,
        'job_field': field, 'job_value': job_value, 'source_link': link,
        'audit': {'id': anchor.id, 'action': anchor.action, 'object_id': anchor.object_id,
            'actor_id': anchor.actor_id, 'data_hash': digest(anchor.data)},
        'evidence': refs}


def _initial(event):
    return {'version': 1, 'status': 'pending_delivery', 'attempts': 0,
        'received_at': None, 'memory_id': None, 'memory_hash': None, 'error': None}


def publish_revision(db, daily, modality):
    """Producer transaction only. Never consumes, resets or acknowledges."""
    need(modality in DAILIES and isinstance(daily, DAILIES[modality]))
    p = SimpleNamespace(workspace_id=workspace(daily.workspace_id))
    value = summary_value(daily)
    need(digest(value) == daily.data['receipt_hash'])
    original = scoped(db, Memory, daily.data['data_memory_id'], p)
    expected = {**copy.deepcopy(value), 'type': {'video': 'video_daily_summary',
        'audio': 'audio_daily_summary', 'imagery': 'imagery_daily_summary'}[modality],
        'daily_id': daily.id, 'receipt_hash': digest(value)}
    if modality == 'video':
        expected.update(source_key=daily.source_key, received_day=daily.business_day)
    need(original.status == 'candidate' and _same(original.data, expected))
    key = 'media-daily:' + modality + ':' + daily.id + ':' + str(daily.version)
    prior = db.scalar(select(Event).where(Event.workspace_id == p.workspace_id, Event.idempotency_key == key))
    if prior is not None:
        proof = checked(db, p, prior)
        need(proof['body']['daily_revision'] == daily.version
            and proof['body']['source_hash'] == digest(value)
            and proof['body']['producer_memory_id'] == original.id
            and proof['body']['producer_data_hash'] == digest(original.data))
        return prior
    pins = [_source_rows(db, p, modality, record) for record in value['records']]
    need(pins and 1 <= daily.version and len(pins) <= 10000)
    actor = pins[0]['actor_id']
    payload = {'schema': PAYLOAD, 'workspace_id': p.workspace_id, 'modality': modality,
        'daily_id': daily.id, 'daily_revision': daily.version,
        'day': daily.business_day if modality == 'video' else daily.received_day,
        'source_key': daily.source_key if modality == 'video' else None,
        'summary': value, 'source_hash': digest(value), 'source_pins': pins,
        'producer_memory_id': original.id, 'producer_data': copy.deepcopy(original.data),
        'producer_data_hash': digest(original.data), 'actor_id': actor,
        'initiator': 'system_daily_collector', 'not_authoritative': True,
        'physical_state_verified': False, 'cloud_calls': 0}
    payload['payload_hash'] = digest(payload)
    _bytes(payload, 32 * 1024 * 1024)
    event = Event(workspace_id=p.workspace_id, kind=KIND, idempotency_key=key,
        data=payload, version=1, attempts=0, status='pending_delivery')
    db.add(event); db.flush()
    audit(db, Principal(actor, p.workspace_id, 'viewer', 'media-daily-producer'), PUBLICATION,
        event.id, {'schema': PAYLOAD, 'payload': copy.deepcopy(payload), 'initial_state': _initial(event)})
    db.flush()
    return event


def publish_collection(db, daily, modality):
    """Keep incomplete legacy collections visible without inventing origins.

    A source lacking its actual Job/actor is not published. The API reports
    not_published and the next explicit reconciliation may try publication
    after that original source is restored. Existing blocked Events stay put.
    """
    try:
        with db.begin_nested():
            return publish_revision(db, daily, modality)
    except (HTTPException, ValueError, KeyError, TypeError, OSError):
        return None


def checked(db, p, event):
    """Historical immutable payload/Audit/state proof; no file IO or grants."""
    need(event.workspace_id == p.workspace_id and event.kind == KIND)
    body = event.data
    _bytes(body, 32 * 1024 * 1024)
    need(body['schema'] == PAYLOAD and body['workspace_id'] == p.workspace_id
        and body['modality'] in DAILIES and type(body['daily_revision']) is int and body['daily_revision'] >= 1
        and body['payload_hash'] == digest({key: value for key, value in body.items() if key != 'payload_hash'})
        and body['source_hash'] == digest(body['summary'])
        and body['producer_data_hash'] == digest(body['producer_data'])
        and body['initiator'] == 'system_daily_collector' and body['not_authoritative'] is True
        and body['physical_state_verified'] is False and type(body['cloud_calls']) is int and body['cloud_calls'] == 0
        and event.idempotency_key == 'media-daily:' + body['modality'] + ':' + body['daily_id'] + ':' + str(body['daily_revision']))
    expected = {**copy.deepcopy(body['summary']), 'type': {'video': 'video_daily_summary',
        'audio': 'audio_daily_summary', 'imagery': 'imagery_daily_summary'}[body['modality']],
        'daily_id': body['daily_id'], 'receipt_hash': body['source_hash']}
    if body['modality'] == 'video':
        expected.update(source_key=body['source_key'], received_day=body['day'])
    need(_same(expected, body['producer_data']) and len(body['source_pins']) == len(body['summary']['records'])
        and bool(body['source_pins']))
    for pin, record in zip(body['source_pins'], body['summary']['records']):
        kind = ('video_upload' if record['mode'] == 'upload' else 'video_capture') if body['modality'] == 'video' else record['kind']
        need(pin['kind'] == kind and pin['id'] == record['id'])
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == event.id, Audit.action.in_([PUBLICATION, TRANSITION]))))
    published = [row for row in events if row.action == PUBLICATION]
    need(len(published) == 1 and published[0].actor_id == body['actor_id']
        and _same(published[0].data, {'schema': PAYLOAD, 'payload': body, 'initial_state': _initial(event)}))
    state = _initial(event)
    history = sorted([row for row in events if row.action == TRANSITION], key=lambda row: row.data['state']['version'])
    for row in history:
        value = row.data
        need(value['schema'] == RECEIPT and value['event_id'] == event.id
            and value['payload_hash'] == body['payload_hash']
            and value['previous_state_hash'] == digest(state)
            and value['record_hash'] == digest({key: item for key, item in value.items() if key != 'record_hash'}))
        next_state = value['state']
        need(next_state['version'] == state['version'] + 1 and state['status'] != 'received')
        if value['operation'] == 'retry':
            need(state['status'] == 'blocked_delivery' and next_state['status'] == 'pending_delivery'
                and next_state['attempts'] == state['attempts'] and next_state['received_at'] is None
                and next_state['memory_id'] is None and next_state['memory_hash'] is None
                and value['command']['expected_version'] == state['version']
                and value['command']['payload_hash'] == body['payload_hash']
                and value['body_hash'] == digest({'actor_id': row.actor_id, 'body': value['command']}))
        else:
            need(value['operation'] in {'received', 'blocked'} and state['status'] == 'pending_delivery'
                and row.actor_id == body['actor_id'] and next_state['attempts'] == state['attempts'] + 1
                and next_state['status'] == ('received' if value['operation'] == 'received' else 'blocked_delivery'))
            if value['operation'] == 'received':
                need(next_state['received_at'] is not None and next_state['error'] is None)
            else:
                need(next_state['received_at'] is None and next_state['memory_id'] is None
                    and next_state['memory_hash'] is None and type(next_state['error']) is str)
        state = copy.deepcopy(next_state)
    need(state['version'] == event.version and state['status'] == event.status
        and state['attempts'] == event.attempts and state['received_at'] == stamp(event.received_at))
    if state['status'] == 'received':
        memory = scoped(db, Memory, state['memory_id'], p)
        need(digest(memory.data) == state['memory_hash'] and _same(memory.data,
            {'schema': RECEIPT, 'type': 'media_daily_received', 'event_id': event.id,
                'payload_hash': body['payload_hash'], 'revision': copy.deepcopy(body),
                'not_authoritative': True, 'physical_state_verified': False, 'cloud_calls': 0}))
    return {'body': copy.deepcopy(body), 'state': state, 'history': history}


def validate_sources(db, p, body):
    """Frozen old revisions survive late processing; originals cannot change."""
    daily = scoped(db, DAILIES[body['modality']], body['daily_id'], p)
    need(daily.version >= body['daily_revision']
        and (daily.business_day if body['modality'] == 'video' else daily.received_day) == body['day']
        and (daily.source_key if body['modality'] == 'video' else None) == body['source_key'])
    producer = scoped(db, Memory, body['producer_memory_id'], p)
    need(_same(producer.data, body['producer_data']) and digest(producer.data) == body['producer_data_hash'])
    from .geo_storage import path_for
    for pin in body['source_pins']:
        if pin.get('kind')=='environment_sound':
            from .sound_daily import validate_pin
            records=[row for row in body['summary']['records'] if row.get('kind')=='environment_sound' and row.get('id')==pin.get('id')]
            need(len(records)==1)
            validate_pin(db,p,pin,records[0])
            continue
        cls, field = SOURCES[pin['kind']]
        source = scoped(db, cls, pin['id'], p)
        job = scoped(db, Job, pin['job_id'], p)
        expected_field = 'task_id' if pin['source_link'] == 'workflow_task_id' else 'stream_id' if pin['source_link'] == 'continuous_id' else field
        need(pin['source_link'] in {None, 'workflow_task_id', 'continuous_id'}
            and pin['job_field'] == expected_field
            and pin['job_value'] == (source.data.get(pin['source_link']) if pin['source_link'] else source.id)
            and source.version >= pin['source_version'] and job.data.get(pin['job_field']) == pin['job_value']
            and job.idempotency_key == pin['job_key'] and job.data.get('actor_id') == pin['actor_id']
            and db.scalar(select(User.id).where(User.id == pin['actor_id'])) == pin['actor_id'])
        anchor = scoped(db, Audit, pin['audit']['id'], p)
        need(anchor.actor_id == pin['actor_id'] == pin['audit']['actor_id']
            and anchor.action == pin['audit']['action'] and anchor.object_id == pin['audit']['object_id']
            and digest(anchor.data) == pin['audit']['data_hash']
            and (anchor.object_id == source.id and anchor.action in SOURCE_ACTIONS
                or pin['source_link'] == 'workflow_task_id' and anchor.object_id == pin['job_value']
                and anchor.action == 'workflow.graph.dispatch_authorized' and anchor.data.get('job_id') == job.id))
        if body['modality'] == 'imagery':
            from .geo_daily import sources
            refs = [{'id': item['evidence_id'], 'sha256': item['sha256']}
                for item in sources(db, p, pin['kind'], source)]
        elif body['modality'] == 'audio':
            saved = source.data.get('source', source.data)
            identity = saved.get('evidence_id') or getattr(source, 'evidence_id', None)
            refs = [{'id': identity, 'sha256': saved.get('input_sha256')}] if identity else []
        else:
            refs = [{'id': source.evidence_id, 'sha256': scoped(db, Evidence, source.evidence_id, p).sha256}] if source.evidence_id else []
            if pin['kind'] == 'video_upload':
                need(bool(refs) and source.data.get('input_sha256') == refs[0]['sha256'])
        if anchor.data.get('input_sha256') is not None:
            need(bool(pin['evidence']) and anchor.data['input_sha256'] == pin['evidence'][0]['sha256'])
        # A capture which had no saved original can later acquire one. The
        # old revision remains a truthful receipt of the earlier failed/pending
        # attempt; it must not acquire the new file or new machine values.
        if pin['evidence']:
            need(_same(refs, pin['evidence']))
        for ref in pin['evidence']:
            evidence, _path = path_for(db, p, ref['id'])
            need(evidence.sha256 == ref['sha256'])


def _transition(db, p, event, prior, operation, *, memory=None, command=None, error=None):
    state = {'version': event.version + 1,
        'status': {'received': 'received', 'blocked': 'blocked_delivery', 'retry': 'pending_delivery'}[operation],
        'attempts': event.attempts + (operation != 'retry'),
        'received_at': stamp(now()) if operation == 'received' else None,
        'memory_id': memory.id if memory else None, 'memory_hash': digest(memory.data) if memory else None,
        'error': error}
    value = {'schema': RECEIPT, 'event_id': event.id, 'payload_hash': event.data['payload_hash'],
        'previous_state_hash': digest(prior), 'operation': operation, 'state': state}
    if command is not None:
        value.update(command=command, body_hash=digest({'actor_id': p.user_id, 'body': command}))
    value['record_hash'] = digest(value)
    event.version = state['version']; event.status = state['status']; event.attempts = state['attempts']
    if state['received_at']:
        from datetime import datetime
        event.received_at = datetime.fromisoformat(state['received_at'])
    else:
        event.received_at = None
    audit(db, p, TRANSITION, event.id, value)
    db.flush()


def receive(db, p, event):
    proof = checked(db, p, event)
    if event.status != 'pending_delivery':
        return False  # No automatic reset of blocked or re-ACK of received.
    body = proof['body']
    receiver = Principal(body['actor_id'], p.workspace_id, 'viewer', 'media-daily-data-receiver')
    try:
        validate_sources(db, receiver, body)
    except (HTTPException, ValueError, KeyError, TypeError, OSError):
        _transition(db, receiver, event, proof['state'], 'blocked', error='原件或来源未通过核对；恢复依据后可按原修订重试接收')
        return False
    memory = Memory(workspace_id=p.workspace_id, status='candidate',
        text=body['day'] + '媒体每日整理已独立接收（候选资料，非现场结论）',
        data={'schema': RECEIPT, 'type': 'media_daily_received', 'event_id': event.id,
            'payload_hash': body['payload_hash'], 'revision': body,
            'not_authoritative': True, 'physical_state_verified': False, 'cloud_calls': 0})
    db.add(memory); db.flush()
    _transition(db, receiver, event, proof['state'], 'received', memory=memory)
    return True


def consume_once(workspace_id):
    """Root hook: explicit workspace only, independent bounded transactions."""
    workspace(workspace_id)
    from .db import engine
    catch_up_heads(workspace_id)
    with Session(engine()) as db:
        identities = _pending_batch(db, workspace_id)
        db.commit()
    received = blocked = 0
    for identity in identities:
        with Session(engine()) as db:
            event = db.scalar(select(Event).where(Event.workspace_id == workspace_id, Event.id == identity,
                Event.kind == KIND, Event.status == 'pending_delivery').with_for_update(skip_locked=True))
            if event is None:
                continue
            try:
                ok = receive(db, SimpleNamespace(workspace_id=workspace_id), event)
                db.commit()
                received += int(ok); blocked += int(not ok)
            except (HTTPException, ValueError, KeyError, TypeError, OSError):
                # A missing/tampered independent publication cannot be replaced
                # with a manufactured ACK/actor. API integrity status exposes it.
                db.rollback(); blocked += 1
    return {'received': received, 'blocked': blocked}


def _pending_batch(db, workspace_id):
    query = select(Event).where(Event.workspace_id == workspace_id, Event.kind == KIND,
        Event.status == 'pending_delivery')
    return _scan_batch(db, workspace_id, query, 'events')


def catch_up_heads(workspace_id):
    """Publish extant current heads, including closed days older than 8 days.

    This is a new delivery of the saved candidate, not a backdated model or
    human action. Existing revision keys, including blocked ones, are skipped.
    """
    workspace(workspace_id)
    from .db import engine
    for modality, cls in DAILIES.items():
        with Session(engine()) as db:
            present = exists(select(Event.id).where(Event.workspace_id == workspace_id,
                Event.kind == KIND, Event.idempotency_key == func.concat(
                    'media-daily:' + modality + ':', cls.id, ':', cls.version)))
            query = select(cls).where(cls.workspace_id == workspace_id, ~present)
            identities = _scan_batch(db, workspace_id, query, 'heads:' + modality)
            db.commit()
        for identity in identities:
            with Session(engine()) as db:
                daily = db.scalar(select(cls).where(cls.workspace_id == workspace_id,
                    cls.id == identity).with_for_update(skip_locked=True))
                if daily is not None:
                    publish_collection(db, daily, modality)
                    db.commit()


def _scan_batch(db, workspace_id, query, name):
    """Durable keyset fairness; this internal cursor grants no data authority.

    An invalid publication cannot acquire a forged blocked Audit, but must not
    starve later valid Events. A closed high-water mark also makes the scan
    wrap under continuous arrivals. A crash merely delays a row until wrap;
    every Event still undergoes its original independent receipt checks.
    """
    from datetime import datetime
    model = query.column_descriptions[0]['entity']
    lock_key = int(digest({'media_daily_cursor': workspace_id, 'name': name})[:15], 16)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_key})
    rows = list(db.scalars(select(Memory).where(Memory.workspace_id == workspace_id,
        Memory.data['type'].as_string() == 'media_daily_scan_cursor_v203',
        Memory.data['name'].as_string() == name).with_for_update()))
    need(len(rows) <= 1)
    cursor = rows[0] if rows else None
    saved = copy.deepcopy(cursor.data) if cursor else {}
    if saved and (saved.get('cursor_hash') != digest({key: value for key, value in saved.items() if key != 'cursor_hash'})
            or saved.get('schema') != 'media-daily-scan-cursor-v203'):
        saved = {}  # Restarting a read scan neither edits nor replays an Event.
    def position(row): return {'created_at': stamp(row.created_at), 'id': row.id}
    def time_at(value):
        value = datetime.fromisoformat(value)
        need(value.tzinfo is not None)
        return value
    horizon = saved.get('horizon')
    after = saved.get('after')
    if horizon is None:
        end = db.scalar(query.order_by(model.created_at.desc(), model.id.desc()).limit(1))
        if end is None: return []
        horizon = position(end); after = None
    bounded = query.where(or_(model.created_at < time_at(horizon['created_at']),
        and_(model.created_at == time_at(horizon['created_at']), model.id <= horizon['id'])))
    if after:
        bounded = bounded.where(or_(model.created_at > time_at(after['created_at']),
            and_(model.created_at == time_at(after['created_at']), model.id > after['id'])))
    batch = list(db.scalars(bounded.order_by(model.created_at, model.id).limit(50)))
    data = {'schema': 'media-daily-scan-cursor-v203', 'type': 'media_daily_scan_cursor_v203',
        'name': name,
        'after': position(batch[-1]) if len(batch) == 50 else None,
        'horizon': horizon if len(batch) == 50 else None}
    data['cursor_hash'] = digest(data)
    if cursor is None:
        cursor = Memory(workspace_id=workspace_id, status='internal',
            text='媒体每日接收扫描位置（内部调度，不是业务经验）', data=data)
        db.add(cursor)
    else:
        cursor.data = data; cursor.version += 1
    db.flush()
    return [row.id for row in batch]


def view(db, p, event):
    proof = checked(db, p, event)
    body, state = proof['body'], proof['state']
    return {'event_id': event.id, 'version': event.version, 'modality': body['modality'],
        'daily_id': body['daily_id'], 'revision': body['daily_revision'], 'day': body['day'],
        'payload_hash': body['payload_hash'], 'source_hash': body['source_hash'],
        'status': state['status'], 'received_at': state['received_at'], 'attempts': state['attempts'],
        'error': state['error'], 'cloud_calls': 0, 'physical_state_verified': False,
        'can_retry': state['status'] == 'blocked_delivery' and p.role in {'admin', 'operator'}}


def retry(db, p, event, body):
    require(p, 'admin', 'operator')
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    proof = checked(db, p, event)
    command = body.model_dump(mode='json')
    old = [row for row in proof['history'] if row.data['operation'] == 'retry'
        and row.data['command']['request_id'] == command['request_id']]
    if old:
        need(len(old) == 1 and old[0].actor_id == p.user_id and _same(old[0].data['command'], command),
            '原重试请求不能更换账号或内容')
        return view(db, p, event)
    need(event.status == 'blocked_delivery' and event.version == body.expected_version
        and event.data['payload_hash'] == body.payload_hash and bool(body.reason.strip()),
        '原修订或接收状态已变化，请核对原请求')
    validate_sources(db, p, proof['body'])
    _transition(db, p, event, proof['state'], 'retry', command=command)
    return view(db, p, event)
