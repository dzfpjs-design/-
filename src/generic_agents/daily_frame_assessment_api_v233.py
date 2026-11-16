"""Task-specific, raw-request recoverable local frame assessments."""
import copy
import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request as HTTPRequest, Response
from pydantic import Field, StrictStr
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from .contracts import Contract, digest
from .db import session
from .security import Principal, authenticated
from .models import Audit, EvidenceAssessment, Job, Task
from .services import scoped, audit, require
from . import daily_frame_assessment_source_v233 as source
from . import media_daily_task_adapter as daily

CREATE = 'data.daily_frame_assessment_created_v233'
MAX_BODY = 16384
MAX_HISTORY = 2000
PAGE_SIZE = 20
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter(prefix='/api/workspace/tasks/{task_id}/daily-frame')


class Create(Contract):
    schema: Literal['daily-frame-assessment-create-v233']
    request_id: UUID
    expected_source_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    expected_frame_sha256: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    purpose: StrictStr = Field(min_length=1, max_length=500)
    parent_assessment_id: UUID | None


def bound_request(**values):
    # Lazy to keep setup router composition independent of the original
    # assessment module's setup_api.P/DB import order.
    from .evidence_assessment_api import Request as OriginalRequest
    class BoundRequest(OriginalRequest):
        """Private server-only subtype; public Request remains unchanged."""
        daily_task_frame_v233: dict
    return BoundRequest(**values)


def parse(raw):
    daily.need(type(raw) is bytes and 0 < len(raw) <= MAX_BODY, '请求为空或超过16KB', 413)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        daily.need(type(value) is dict and set(value) == set(Create.model_fields), '请求字段不完整或含未知字段', 422)
        result = Create.model_validate(value)
        daily.need(result.purpose.strip(), '请填写核查用途', 422)
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422, '画面核查请求格式无效，未创建核查') from None


def _records(db, p, task_id):
    daily.current_actor(db, p); scoped(db, Task, task_id, p)
    rows = list(db.scalars(select(EvidenceAssessment).where(EvidenceAssessment.workspace_id == p.workspace_id,
        EvidenceAssessment.data['request'][source.MARKER]['task_id'].as_string() == task_id).limit(MAX_HISTORY+1)))
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == CREATE, Audit.data['task_id'].as_string() == task_id).limit(MAX_HISTORY+1)))
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == 'assess_evidence',
        Job.data[source.MARKER]['task_id'].as_string() == task_id).limit(MAX_HISTORY+1)))
    source.need(len(rows) <= MAX_HISTORY and len(events) <= MAX_HISTORY and len(jobs) <= MAX_HISTORY)
    source.need({row.id for row in rows} == {event.object_id for event in events} and len(events) == len(rows))
    source.need({row.id for row in rows} == {job.data['assessment_id'] for job in jobs} and len(jobs) == len(rows))
    return rows, events


def checked_association(db, p, row, event, task_id):
    from .evidence_assessment import checked_request
    try:
        checked_request(row); value = event.data; marker = row.data['request'][source.MARKER]
        original = parse(value['exact_body'].encode('utf-8'))
        source.need(event.object_id == row.id and event.actor_id == row.owner_id
            and value['actor_id'] == row.owner_id and value['task_id'] == task_id == marker['task_id']
            and row.request_id == str(original.request_id) == value['request_id']
            and hashlib.sha256(value['exact_body'].encode('utf-8')).hexdigest() == value['body_sha256']
            and value['record_hash'] == digest({key: item for key, item in value.items() if key != 'record_hash'})
            and value['request_hash'] == row.data['request_hash'] and value['creation_hash'] == row.data['creation_hash']
            and value['marker_hash'] == digest(marker) and value['job_id'] == row.data['job_id']
            and original.expected_source_hash == marker['source_hash']
            and original.expected_frame_sha256 == marker['frame_sha256']
            and original.purpose == row.data['request']['purpose']
            and (str(original.parent_assessment_id) if original.parent_assessment_id else None) == row.data['request']['parent_id'])
        job = scoped(db, Job, row.data['job_id'], p)
        source.need(job.kind == 'assess_evidence' and job.idempotency_key == 'assessment:'+row.id
            and job.data['assessment_id'] == row.id and job.data['actor_id'] == row.owner_id
            and job.data[source.MARKER] == {'task_id': task_id, 'request_id': row.request_id,
                'marker_hash': value['marker_hash']})
        originals = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'data.evidence_assessment_requested', Audit.object_id == row.id).limit(2)))
        source.need(len(originals) == 1 and originals[0].actor_id == row.owner_id
            and originals[0].data == {'objects': 1, 'modalities': ['video']})
        return job
    except HTTPException: raise
    except (KeyError, ValueError, TypeError, AttributeError): raise HTTPException(409, source.ERROR) from None


def _associated(db, p, task_id, identity):
    rows, events = _records(db, p, task_id)
    row = next((item for item in rows if item.id == identity), None)
    daily.need(row is not None, '此任务没有该画面核查', 404)
    matches = [event for event in events if event.object_id == row.id]
    source.need(len(matches) == 1)
    return row, checked_association(db, p, row, matches[0], task_id)


def summary(db, p, row, job):
    status = row.status; error = None; result = None; result_hash = None
    try:
        source.checked_result(db, p, row)
        if 'result' in row.data:
            result = copy.deepcopy(row.data['result']['records'][0]['daily_frame_result_v233'])
            result_hash = row.data['result_hash']
    except (HTTPException, KeyError, TypeError, ValueError):
        status = 'blocked_integrity'; error = '已保存核查的结果或独立证明无法核对，原记录保留。'
    if status != 'blocked_integrity' and (job.status.startswith('failed') or job.status.startswith('blocked') or job.status == 'lease_lost'):
        status = 'failed'; error = '后台核查未能完成，请保留原记录并查看失败；需要时另建核查。'
    if status != 'blocked_integrity' and job.status == 'cancelled': status = 'cancelled'
    marker = row.data['request'][source.MARKER]
    return {'id': row.id, 'version': row.version, 'status': status, 'created_at': row.created_at.isoformat(),
        'progress': row.data.get('progress', ''), 'purpose': row.data['request']['purpose'], 'task_id': marker['task_id'],
        'source_hash': marker['source_hash'], 'frame_sha256': marker['frame_sha256'],
        'parent_id': row.data['request'].get('parent_id'), 'job_id': job.id,
        'can_cancel': status in {'queued', 'running'} and p.role in {'admin', 'operator'}
            and (row.owner_id == p.user_id or p.role == 'admin'),
        'error': error, 'result_hash': result_hash, 'result': result}


def recover(db, p, task_id, request_id, expected_sha256):
    daily.current_actor(db, p); scoped(db, Task, task_id, p)
    daily.need(daily.originals.SHA.fullmatch(expected_sha256), '原请求摘要无效', 422)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == CREATE,
        Audit.data['request_id'].as_string() == request_id).limit(2)))
    rows = list(db.scalars(select(EvidenceAssessment).where(EvidenceAssessment.workspace_id == p.workspace_id,
        EvidenceAssessment.request_id == request_id).limit(2)))
    source.need(len(events) <= 1 and len(rows) <= 1)
    if not events:
        source.need(not rows)
        _records(db, p, task_id)
        return {'schema': 'daily-frame-assessment-recovery-v233', 'task_id': task_id, 'request_id': request_id,
            'body_sha256': expected_sha256, 'found': False, 'result': None}
    event = events[0]
    daily.need(event.workspace_id == p.workspace_id and event.actor_id == p.user_id, '请由原操作者在原工作区核对', 403)
    source.need(event.data['task_id'] == task_id and event.data['body_sha256'] == expected_sha256 and len(rows) == 1)
    row = rows[0]; job = checked_association(db, p, row, event, task_id)
    result = {'schema': 'daily-frame-assessment-command-result-v233', 'operation': 'create', 'task_id': task_id,
        'request_id': request_id, 'body_sha256': expected_sha256, 'assessment': summary(db, p, row, job)}
    return {'schema': 'daily-frame-assessment-recovery-v233', 'task_id': task_id, 'request_id': request_id,
        'body_sha256': expected_sha256, 'found': True, 'result': result}


def is_frame_record(db, row):
    request = row.data.get('request')
    if type(request) is dict and source.MARKER in request: return True
    if db.scalar(select(Audit.id).where(Audit.workspace_id == row.workspace_id,
            Audit.object_id == row.id, Audit.action.in_([CREATE, source.RESULT_AUDIT])).limit(1)): return True
    return db.scalar(select(Job.id).where(Job.workspace_id == row.workspace_id,
        Job.data['assessment_id'].as_string() == row.id,
        Job.data[source.MARKER].is_not(None)).limit(1)) is not None


def guard_record(db, p, row):
    """Metadata-only seal check, including reverse traces after marker loss."""
    if not is_frame_record(db, row): return row
    source.need(type(row.data.get('request')) is dict and type(row.data['request'].get(source.MARKER)) is dict)
    checked_row, _ = _associated(db, p, row.data['request'][source.MARKER]['task_id'], row.id)
    source.need(checked_row.id == row.id)
    return source.checked_result(db, p, row)


def create_assessment(db, p, task_id, raw):
    from . import evidence_assessment_api as normal
    body = parse(raw); nonce = str(body.request_id); sha = hashlib.sha256(raw).hexdigest()
    normal.lock_request(db, p, nonce)
    prior = recover(db, p, task_id, nonce, sha)
    if prior['found']: return prior['result'], False
    require(p, 'admin', 'operator')
    task = scoped(db, Task, task_id, p)
    from .models import DomainInstance
    scoped(db, DomainInstance, task.data['instance_id'], p, True)
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    bundle = source.read_source(db, p, task_id)
    daily.need(bundle['source_hash'] == body.expected_source_hash and bundle['frame_sha256'] == body.expected_frame_sha256,
        '画面或固定来源已变化，请重新核对后另建请求')
    parent = str(body.parent_assessment_id) if body.parent_assessment_id else None
    if parent: _associated(db, p, task_id, parent)
    from .instance_api import active
    _, release = active(db, p, bundle['pin']['instance_id'])
    bound = bound_request(instance_id=bundle['pin']['instance_id'], release_id=release.id,
        entity_ids=bundle['pin']['entity_ids'], purpose=body.purpose, goal='current_state', required_modalities=['video'],
        lookback_hours=24, request_id=nonce, parent_id=parent, daily_task_frame_v233=source.marker(bundle))
    outcome = {}; row = normal.create_saved(bound, p, db, outcome=outcome)
    source.need(outcome.get('created') is True)
    job = scoped(db, Job, row.data['job_id'], p)
    job.data = {**job.data, source.MARKER: {'task_id': task_id, 'request_id': nonce,
        'marker_hash': digest(row.data['request'][source.MARKER])}}
    value = {'schema': 'daily-frame-assessment-created-proof-v233', 'task_id': task_id, 'actor_id': p.user_id,
        'request_id': nonce, 'exact_body': raw.decode('utf-8'), 'body_sha256': sha,
        'creation_hash': row.data['creation_hash'], 'request_hash': row.data['request_hash'],
        'marker_hash': digest(row.data['request'][source.MARKER]), 'job_id': row.data['job_id']}
    value['record_hash'] = digest(value); audit(db, p, CREATE, row.id, value); db.commit()
    found = recover(db, p, task_id, nonce, sha)
    return found['result'], True


def history(db, p, task_id, cursor=None):
    rows, events = _records(db, p, task_id)
    ordered = sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)
    for row in ordered:
        matches = [event for event in events if event.object_id == row.id]
        source.need(len(matches) == 1); checked_association(db, p, row, matches[0], task_id)
    offset = 0
    if cursor is not None:
        indices = [index for index, row in enumerate(ordered) if row.id == cursor]
        daily.need(len(indices) == 1, '历史游标不属于此任务', 422); offset = indices[0]+1
    page = ordered[offset:offset+PAGE_SIZE]
    return {'schema': 'daily-frame-assessment-list-v233', 'task_id': task_id,
        'items': [summary(db, p, row, scoped(db, Job, row.data['job_id'], p)) for row in page],
        'next_cursor': page[-1].id if page and offset+len(page) < len(ordered) else None}


def current_freshness(db, p, task_id, identity):
    from .evidence_assessment import freshness
    row, _ = _associated(db, p, task_id, identity)
    try:
        validation = freshness(db, p, row)
        unchanged = row.status == 'completed' and validation['metadata_versions_unchanged']
        rechecked = validation['physical_files_rechecked']; message = validation['message']
    except (HTTPException, KeyError, TypeError, ValueError, OSError):
        unchanged = rechecked = False; message = '原固定画面、核查结果或来源证明未通过当前冷核，原记录保留。'
    return {'schema': 'daily-frame-assessment-freshness-v233', 'task_id': task_id, 'assessment_id': identity,
        'result_hash': row.data.get('result_hash'), 'unchanged': unchanged,
        'source_originals_rechecked': rechecked, 'new_sources_checked': False, 'message': message}


def transaction(db, function, *args):
    try: return function(db, *args)
    except BaseException:
        db.rollback(); raise


@router.get('')
def get_source(task_id: UUID, p: P, db: DB): return source.read_source(db, p, str(task_id))['view']


@router.get('/content')
def content(task_id: UUID, request: HTTPRequest, p: P, db: DB):
    query = request.query_params
    daily.need(set(query) == {'expected_sha256'} and len(query.getlist('expected_sha256')) == 1, '请携带唯一画面摘要', 422)
    bundle = source.read_source(db, p, str(task_id)); image = bundle['view']['image']
    daily.need(query['expected_sha256'] == image['sha256'], '画面摘要不一致')
    return Response(bundle['content'], headers={'Content-Type': image['media_type'], 'Content-Length': str(len(bundle['content'])),
        'X-Content-SHA256': image['sha256'], 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})


@router.post('/assessments')
async def create(task_id: UUID, request: HTTPRequest, response: Response, p: P, db: DB):
    raw = bytearray()
    async for part in request.stream():
        daily.need(len(raw)+len(part) <= MAX_BODY, '请求超过16KB，未创建核查', 413); raw.extend(part)
    result, created = await run_in_threadpool(transaction, db, create_assessment, p, str(task_id), bytes(raw))
    response.status_code = 201 if created else 200
    return result


@router.get('/assessment-requests/{request_id}')
def request_result(task_id: UUID, request_id: UUID, request: HTTPRequest, p: P, db: DB):
    query = request.query_params
    daily.need(set(query) == {'expected_sha256'} and len(query.getlist('expected_sha256')) == 1, '请携带唯一原请求摘要', 422)
    return recover(db, p, str(task_id), str(request_id), query['expected_sha256'])


@router.get('/assessments')
def list_history(task_id: UUID, request: HTTPRequest, p: P, db: DB):
    query = request.query_params
    daily.need(not set(query)-{'cursor'} and len(query.getlist('cursor')) <= 1, '历史仅接受一个游标', 422)
    return history(db, p, str(task_id), query.get('cursor'))


@router.get('/assessments/{assessment_id}')
def get_assessment(task_id: UUID, assessment_id: UUID, p: P, db: DB):
    row, job = _associated(db, p, str(task_id), str(assessment_id))
    return {'schema': 'daily-frame-assessment-view-v233', 'task_id': str(task_id), 'assessment': summary(db, p, row, job)}


@router.get('/assessments/{assessment_id}/freshness')
def get_freshness(task_id: UUID, assessment_id: UUID, p: P, db: DB):
    return current_freshness(db, p, str(task_id), str(assessment_id))
