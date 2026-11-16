"""Original audio upload/recovery and Task-owned environmental-sound views."""
import hashlib
import re
from typing import Annotated, Literal
from urllib.parse import quote
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import Field, StrictInt
from sqlalchemy import select, func
from .db import session
from .security import authenticated, Principal
from .contracts import Contract, digest
from .models import DomainInstance
from .services import scoped
from .sound_models import SoundInput, EnvironmentSoundRun
from . import sound_inputs as store

P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
router = APIRouter()


@router.get('/api/workspace/instances/{identity}/input-sounds/options')
def options(identity: str, p: P, db: DB):
    instance = scoped(db, DomainInstance, identity, p)
    allowed = p.role in {'admin', 'operator'} and instance.status == 'partially_active' and instance.active_release_id is not None
    return {'schema': 'sound-input-options-v1', 'workspace_id': p.workspace_id, 'instance_id': identity,
        'max_bytes': store.MAX_BYTES, 'max_analysis_seconds': 60, 'accepted_media_types': list(store.MEDIA),
        'channel_choices': ['mono', 'mix', '1', '2', '3', '4', '5', '6', '7', '8'], 'threshold_default': .5,
        'requires_model_for_upload': False, 'analysis_after_upload': False, 'metadata_status': 'not_decoded',
        'can_upload': allowed,
        'blocked_reason': None if allowed else '仅已启用业务实例的授权人员可上传；原件和原请求仍可读取'}


@router.get('/api/workspace/instances/{identity}/input-sounds')
def inputs(identity: str, p: P, db: DB, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
    scoped(db, DomainInstance, identity, p)
    filters = (SoundInput.workspace_id == p.workspace_id, SoundInput.instance_id == identity)
    total = db.scalar(select(func.count()).select_from(SoundInput).where(*filters))
    rows = list(db.scalars(select(SoundInput).where(*filters).order_by(SoundInput.created_at.desc(), SoundInput.id).offset(offset).limit(limit)))
    return {'schema': 'sound-input-list-v1', 'workspace_id': p.workspace_id, 'instance_id': identity,
        'offset': offset, 'limit': limit, 'total': total, 'has_more': offset + len(rows) < total,
        'records': [store.view(store.checked(db, p, row.id)) for row in rows]}


@router.post('/api/workspace/instances/{identity}/input-sounds')
async def upload(identity: str, p: P, db: DB, file: UploadFile = File(), intent: str = Form()):
    content = await file.read(store.MAX_BYTES + 1)
    row, created = store.save(db, p, identity, intent, content)
    db.commit()
    return JSONResponse(store.view(row), status_code=201 if created else 200)


@router.get('/api/workspace/instances/{identity}/input-sounds/requests')
def recovery(identity: str, p: P, db: DB, request_id: str,
        raw_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'), input_sha256: str = Query(pattern=r'^[0-9a-f]{64}$')):
    scoped(db, DomainInstance, identity, p)
    result = {'schema': 'sound-input-recovery-v1', 'workspace_id': p.workspace_id, 'instance_id': identity,
        'request_id': request_id, 'raw_sha256': raw_sha256, 'input_sha256': input_sha256,
        'found': False, 'input': None, 'intent_raw': None, 'body_hash': None}
    row = store.find_original(db, p, identity, request_id)
    if row is None:
        return result
    store.need(row.data['raw_sha256'] == raw_sha256 and row.data['snapshot']['input_sha256'] == input_sha256,
        '原上传申请或原音摘要不一致，未换号恢复')
    return {**result, 'found': True, 'input': store.view(row), 'intent_raw': row.data['intent_raw'], 'body_hash': row.data['body_hash']}


@router.get('/api/sounds/inputs/{identity}')
def input_detail(identity: str, p: P, db: DB):
    return store.view(store.checked(db, p, identity))


def audio_response(request, evidence, content, name):
    headers = {'X-Content-SHA256': evidence.sha256, 'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'private, no-store', 'Accept-Ranges': 'bytes',
        'Content-Disposition': "inline; filename*=UTF-8''" + quote(name)}
    requested = request.headers.get('range')
    if requested:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
        if not match or not any(match.groups()):
            raise HTTPException(416, '仅支持一个合法音频字节范围', headers={'Content-Range': 'bytes */' + str(len(content))})
        left, right = match.groups()
        start = int(left) if left else max(0, len(content) - int(right))
        end = min(int(right), len(content) - 1) if left and right else len(content) - 1
        if start >= len(content) or start > end or not left and int(right) == 0:
            raise HTTPException(416, '音频播放范围超出原件', headers={'Content-Range': 'bytes */' + str(len(content))})
        headers['Content-Range'] = f'bytes {start}-{end}/{len(content)}'
        return Response(content[start:end + 1], status_code=206, media_type=evidence.media_type, headers=headers)
    return Response(content, media_type=evidence.media_type, headers=headers)


@router.get('/api/sounds/inputs/{identity}/content')
def input_content(identity: str, request: Request, p: P, db: DB):
    row = store.checked(db, p, identity); evidence, content = store.input_bytes(db, p, row)
    return audio_response(request, evidence, content, row.data['snapshot']['name'])


@router.get('/api/sounds/runs/{identity}')
def result_detail(identity: str, p: P, db: DB):
    from .workflow_sound import checked_run, run_view
    context = checked_run(db, p, identity)
    return run_view(context['run'])


@router.get('/api/sounds/runs/{identity}/content')
def result_content(identity: str, request: Request, p: P, db: DB):
    from .workflow_sound import checked_run
    context = checked_run(db, p, identity); row = context['input']
    evidence, content = store.input_bytes(db, p, row)
    return audio_response(request, evidence, content, row.data['snapshot']['name'])


@router.get('/api/workspace/tasks/{identity}/sounds')
def task_results(identity: str, p: P, db: DB):
    from .workflow_sound import task_view
    return task_view(db, p, identity)


class SoundReview(Contract):
    schema: Literal['sound-review-v1']
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    version: StrictInt = Field(ge=1)
    verdict: Literal['matches', 'false_positive', 'uncertain']
    note: str = Field(min_length=1, max_length=5000)


@router.post('/api/sounds/runs/{identity}/reviews')
def review(identity: str, body: SoundReview, p: P, db: DB):
    from .workflow_sound import review_run
    value, created = review_run(db, p, identity, body.model_dump(mode='json'))
    db.commit()
    return JSONResponse(value, status_code=201 if created else 200)


@router.get('/api/sounds/runs/{identity}/reviews/requests')
def review_recovery(identity: str, p: P, db: DB,
        request_id: str = Query(pattern=r'^[a-zA-Z0-9_-]{12,100}$'),
        body_hash: str = Query(pattern=r'^[0-9a-f]{64}$'), expected_version: int = Query(ge=1)):
    from .workflow_sound import recover_review
    return recover_review(db, p, identity, request_id, body_hash, expected_version)


@router.post('/api/sounds/register-business')
def register_business(p: P, db: DB):
    from .workflow_sound import publish_sound_capability
    cap = publish_sound_capability(db, p); db.commit()
    return {'schema': 'sound-business-registration-v1', 'capability': {
        'id': cap.id, 'version': cap.version, 'content_hash': cap.content_hash,
        'name': cap.name, 'release_version': cap.release_version, 'status': cap.status},
        'candidate_only': True, 'domain_quality_verified': False}
