"""Normal instance workspace documents; configuration uploads are separate."""
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import Field, StrictInt, ValidationError
from sqlalchemy import select

from . import runtime_documents as d, runtime_document_receipts as receipts
from .contracts import Contract
from .services import scoped
from .setup_api import P, DB
from .runtime_document_models import RuntimeDocument as Document, RuntimeDocumentVersion as Version
from .runtime_document_models import RuntimeDocumentParseAttempt as Attempt, RuntimeDocumentSearch as Search

router = APIRouter(prefix='/api/runtime-documents')
Positive = Annotated[StrictInt, Field(ge=1)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class Command(Contract):
    request_id: UUID


class Upload(Command):
    release_id: UUID
    title: str = Field(min_length=1, max_length=200)
    file_name: str = Field(min_length=1, max_length=250)
    file_size: Annotated[StrictInt, Field(gt=0, le=d.MAX_BYTES)]
    file_sha256: Hash


class NewVersion(Upload):
    expected_version: Positive
    prior_version_id: UUID


class Action(Command):
    expected_version: Positive
    reason: str = Field(min_length=1, max_length=2000)


class SearchCreate(Command):
    release_id: UUID
    keyword: str = Field(min_length=1, max_length=200)


def body(value): return value.model_dump(mode='json')


def integer(value):
    if type(value) is not str or not value.isdecimal() or str(int(value)) != value:
        raise HTTPException(422, '版本与文件大小必须为标准正整数')
    return int(value)


async def uploaded(file, fields, update=False):
    try:
        fields['file_size'] = integer(fields['file_size'])
        if update: fields['expected_version'] = integer(fields['expected_version'])
        value = (NewVersion if update else Upload).model_validate(fields)
    except (ValidationError, ValueError, TypeError):
        await file.close(); raise HTTPException(422, '上传请求、文件元数据或版本不符合契约') from None
    try:
        d.need(file.filename == value.file_name, '实际文件名与原请求不一致')
        raw = await file.read(d.MAX_BYTES + 1)
    finally: await file.close()
    return body(value), raw


def paginated(query, db, offset, view):
    rows = list(db.scalars(query.offset(offset).limit(21)))
    with d.verification_scope(db): records = [view(row) for row in rows[:20]]
    return {'records': records, 'offset': offset, 'has_more': len(rows) > 20}


@router.get('/catalog')
def catalog(p: P):
    return {'schema': 'runtime-documents-catalog-v205', 'allowed_extensions': list(d.MEDIA),
        'limits': {'original_bytes': d.MAX_BYTES, 'blocks': 12000, 'characters': 2000000, 'pdf_pages': 500,
            'expanded_bytes': 80 * 1024 * 1024, 'parse_seconds': 60, 'documents_per_search': 100, 'hits_per_search': 100,
            'scan_blocks': d.SCAN_BLOCK_LIMIT, 'scan_characters': d.SCAN_CHARACTER_LIMIT, 'hit_text_characters': d.HIT_TEXT_LIMIT},
        'ocr_supported': False, 'remote_supported': False, 'semantic_supported': False}


@router.get('/requests/{request_id}')
def recover(request_id: UUID, p: P, db: DB, expected_hash: Hash | None = None):
    return d.recover(db, p, str(request_id), expected_hash)


@router.get('/instances/{instance_id}/context')
def context(instance_id: str, p: P, db: DB): return d.context(db, p, instance_id)


@router.get('/instances/{instance_id}/documents')
def documents(instance_id: str, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    scoped(db, d.m.DomainInstance, instance_id, p)
    return paginated(select(Document).where(Document.workspace_id == p.workspace_id, Document.instance_id == instance_id)
        .order_by(Document.created_at.desc(), Document.id.desc()), db, offset, lambda row: d.document_view(db, p, row))


@router.post('/instances/{instance_id}/documents', status_code=201)
async def create(instance_id: str, p: P, db: DB, request_id: str = Form(...), release_id: str = Form(...),
    title: str = Form(...), file_name: str = Form(...), file_size: str = Form(...), file_sha256: str = Form(...), file: UploadFile = File(...)):
    value, raw = await uploaded(file, {'request_id': request_id, 'release_id': release_id, 'title': title,
        'file_name': file_name, 'file_size': file_size, 'file_sha256': file_sha256})
    result = d.upload(db, p, instance_id, value, raw); db.commit(); return result


@router.get('/documents/{identity}')
def document(identity: str, p: P, db: DB):
    with d.verification_scope(db): return d.document_view(db, p, scoped(db, Document, identity, p))


@router.post('/documents/{identity}/versions', status_code=201)
async def update(identity: str, p: P, db: DB, request_id: str = Form(...), release_id: str = Form(...),
    title: str = Form(...), file_name: str = Form(...), file_size: str = Form(...), file_sha256: str = Form(...),
    expected_version: str = Form(...), prior_version_id: str = Form(...), file: UploadFile = File(...)):
    document = scoped(db, Document, identity, p)
    value, raw = await uploaded(file, {'request_id': request_id, 'release_id': release_id, 'title': title,
        'file_name': file_name, 'file_size': file_size, 'file_sha256': file_sha256,
        'expected_version': expected_version, 'prior_version_id': prior_version_id}, True)
    result = d.upload(db, p, document.instance_id, value, raw, identity); db.commit(); return result


@router.get('/documents/{identity}/versions')
def versions(identity: str, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    scoped(db, Document, identity, p)
    return paginated(select(Version).where(Version.workspace_id == p.workspace_id, Version.document_id == identity)
        .order_by(Version.sequence.desc(), Version.id.desc()), db, offset, lambda row: d.version_view(db, p, row))


@router.get('/versions/{identity}')
def version(identity: str, p: P, db: DB):
    with d.verification_scope(db): return d.version_view(db, p, scoped(db, Version, identity, p))


@router.get('/versions/{identity}/original')
def original(identity: str, p: P, db: DB):
    row = scoped(db, Version, identity, p); raw = d.checked_version(db, p, row)
    return Response(raw, media_type=row.data['origin']['media_type'], headers={'Cache-Control': 'private, no-store',
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(row.name, safe='')})


@router.get('/versions/{identity}/blocks')
def blocks(identity: str, p: P, db: DB, offset: int = Query(0, ge=0, le=12000),
    attempt_id: str | None = None, expected_parse_hash: Hash | None = None):
    version = scoped(db, Version, identity, p)
    with d.verification_scope(db): attempt, result = d.selected_attempt(db, p, version, attempt_id, expected_parse_hash)
    rows = result['blocks'][offset:offset + 21]
    return {'records': rows[:20], 'offset': offset, 'has_more': len(rows) > 20,
        'attempt_id': attempt.id, 'parse_hash': attempt.data['parse_hash']}


@router.get('/versions/{identity}/citations/{block_id}')
def citation(identity: str, block_id: str, p: P, db: DB, attempt_id: str | None = None, expected_parse_hash: Hash | None = None):
    with d.verification_scope(db):
        return d.citation(db, p, scoped(db, Version, identity, p), block_id, attempt_id, expected_parse_hash)


@router.get('/versions/{identity}/attempts')
def attempts(identity: str, p: P, db: DB):
    version = scoped(db, Version, identity, p)
    rows = list(db.scalars(select(Attempt).where(Attempt.workspace_id == p.workspace_id, Attempt.document_version_id == identity)
        .order_by(Attempt.ordinal.desc(), Attempt.id.desc()).limit(101)))
    with d.verification_scope(db):
        for row in rows[:100]:
            if row.status in d.TERMINAL: d.checked_attempt(db, p, version, row)
        return {'records': [d.attempt_view(db, p, row) for row in rows[:100]], 'has_more': len(rows) > 100}


@router.post('/versions/{identity}/retry')
def retry(identity: str, value: Action, p: P, db: DB):
    result = d.control(db, p, identity, body(value), 'retry'); db.commit(); return result


@router.post('/versions/{identity}/cancel')
def cancel(identity: str, value: Action, p: P, db: DB):
    result = d.control(db, p, identity, body(value), 'cancel'); db.commit(); return result


@router.post('/versions/{identity}/receipts/{event_id}/retry')
def retry_receipt(identity: str, event_id: str, value: Action, p: P, db: DB):
    result = receipts.retry(db, p, identity, event_id, body(value)); db.commit(); return result


@router.post('/instances/{identity}/searches', status_code=201)
def create_search(identity: str, value: SearchCreate, p: P, db: DB):
    result = d.start_search(db, p, identity, body(value)); db.commit(); return result


@router.get('/searches/{identity}')
def search(identity: str, p: P, db: DB):
    with d.verification_scope(db): return d.search_view(db, p, scoped(db, Search, identity, p))


@router.get('/instances/{identity}/searches')
def searches(identity: str, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    scoped(db, d.m.DomainInstance, identity, p)
    return paginated(select(Search).where(Search.workspace_id == p.workspace_id, Search.instance_id == identity)
        .order_by(Search.created_at.desc(), Search.id.desc()), db, offset, lambda row: d.search_view(db, p, row))
