"""Scoped local OCR commands and immutable result views; native V205 is separate."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import select

from . import runtime_document_ocr as o, runtime_document_ocr_receipts as receipts
from .contracts import Contract
from .services import scoped
from .setup_api import P, DB
from .runtime_document_ocr_models import RuntimeDocumentOcrRun as Run, RuntimeDocumentOcrPage as Page
from .runtime_document_ocr_models import RuntimeDocumentOcrResult as Result, RuntimeDocumentOcrSearch as Search

router = APIRouter(prefix='/api/runtime-document-ocr')
Positive = Annotated[StrictInt, Field(ge=1)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
PageNumber = Annotated[StrictInt, Field(ge=1, le=500)]


class Command(Contract):
    request_id: UUID


class Start(Command):
    release_id: UUID
    profile_hash: Hash
    range_mode: Literal['missing_text', 'all', 'pages']
    pages: list[PageNumber] = Field(max_length=100)

    @model_validator(mode='after')
    def explicit_range(self):
        if len(set(self.pages)) != len(self.pages) or (bool(self.pages) != (self.range_mode == 'pages')):
            raise ValueError('指定页模式需要不重复的明确页码；其它模式不带页码')
        return self


class Action(Command):
    expected_version: Positive
    reason: str = Field(min_length=1, max_length=2000)


class SearchCreate(Command):
    release_id: UUID
    keyword: str = Field(min_length=1, max_length=200)


def body(value): return value.model_dump(mode='json')


def paginated(db, query, offset, view):
    rows = list(db.scalars(query.offset(offset).limit(21)))
    with o.native.verification_scope(db): records = [view(row) for row in rows[:20]]
    return {'records': records, 'offset': offset, 'has_more': len(rows) > 20}


@router.get('/catalog')
def catalog(p: P):
    installed = o.profile(cached=True)
    return {'profiles': [{'manifest_hash': o.PROFILE, 'name': installed['name'], 'languages': ['zh', 'en'],
        'ready': bool(installed['ready']), 'reason': installed.get('reason')}], 'local_only': True,
        'limits': {'original_bytes': o.native.MAX_BYTES, 'pdf_pages': 500, 'selected_pages': 100,
            'regions_per_page': 2000, 'characters_per_run': 2000000, 'documents_per_search': 100,
            'results_per_search': 500, 'scanned_regions': 2000, 'hits_per_search': 100, 'excerpt_characters': 1200}}


@router.get('/requests/{request_id}')
def recover(request_id: UUID, p: P, db: DB, expected_hash: Hash | None = None):
    return o.recover(db, p, str(request_id), expected_hash)


@router.get('/versions/{identity}/context')
def context(identity: str, p: P, db: DB): return o.context(db, p, identity)


@router.post('/versions/{identity}/runs', status_code=201)
def start(identity: str, value: Start, p: P, db: DB):
    result = o.start(db, p, identity, body(value)); db.commit(); return result


@router.get('/versions/{identity}/runs')
def runs(identity: str, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    scoped(db, o.Version, identity, p)
    return paginated(db, select(Run).where(Run.workspace_id == p.workspace_id, Run.document_version_id == identity)
        .order_by(Run.created_at.desc(), Run.id.desc()), offset, lambda row: o.run_view(db, p, row))


@router.get('/runs/{identity}')
def run(identity: str, p: P, db: DB):
    with o.native.verification_scope(db): return o.run_view(db, p, scoped(db, Run, identity, p))


@router.get('/runs/{identity}/pages')
def pages(identity: str, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    scoped(db, Run, identity, p)
    return paginated(db, select(Page).where(Page.workspace_id == p.workspace_id, Page.run_id == identity)
        .order_by(Page.page_number, Page.id), offset, lambda row: o.page_view(db, p, row))


@router.get('/pages/{identity}')
def page(identity: str, p: P, db: DB):
    with o.native.verification_scope(db): return o.page_view(db, p, scoped(db, Page, identity, p))


@router.post('/runs/{identity}/cancel')
def cancel(identity: str, value: Action, p: P, db: DB):
    result = o.control(db, p, identity, body(value), 'cancel'); db.commit(); return result


@router.post('/runs/{identity}/retry')
def retry_run(identity: str, value: Action, p: P, db: DB):
    result = o.control(db, p, identity, body(value), 'retry'); db.commit(); return result


@router.post('/pages/{identity}/retry')
def retry_page(identity: str, value: Action, p: P, db: DB):
    result = o.control(db, p, identity, body(value), 'retry', page_only=True); db.commit(); return result


@router.get('/results/{identity}/image')
def image(identity: str, p: P, db: DB):
    with o.native.verification_scope(db):
        result = scoped(db, Result, identity, p); _, page, outcome = o.checked_result(db, p, result)
        o.need(outcome['image'] is not None, '此次原结果未保存页图；未借用其它结果')
        raw = o.checked_image(db, p, page)
    return Response(raw, media_type='image/png', headers={'Cache-Control': 'private, no-store'})


@router.get('/results/{identity}/regions')
def regions(identity: str, p: P, db: DB, offset: int = Query(0, ge=0, le=2000)):
    with o.native.verification_scope(db):
        result = scoped(db, Result, identity, p); _, _, outcome = o.checked_result(db, p, result)
        image = outcome['image'] or {}; records = outcome['regions'][offset:offset + 101]
        return {'records': records[:100], 'offset': offset, 'has_more': len(records) > 100,
            'result_id': result.id, 'result_hash': result.data['result_hash'], 'image_sha256': image.get('sha256'),
            'image_width': image.get('width'), 'image_height': image.get('height')}


@router.get('/results/{identity}/citations/{region_id}')
def citation(identity: str, region_id: str, p: P, db: DB, expected_result_hash: Hash):
    with o.native.verification_scope(db):
        return o.citation(db, p, scoped(db, Result, identity, p), region_id, expected_result_hash)


@router.post('/results/{identity}/receipts/{event_id}/retry')
def retry_receipt(identity: str, event_id: str, value: Action, p: P, db: DB):
    result = receipts.retry(db, p, identity, event_id, body(value)); db.commit(); return result


@router.post('/instances/{identity}/searches', status_code=201)
def create_search(identity: str, value: SearchCreate, p: P, db: DB):
    result = o.start_search(db, p, identity, body(value)); db.commit(); return result


@router.get('/searches/{identity}')
def search(identity: str, p: P, db: DB):
    with o.native.verification_scope(db): return o.search_view(db, p, scoped(db, Search, identity, p))


@router.get('/instances/{identity}/searches')
def searches(identity: str, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    scoped(db, o.m.DomainInstance, identity, p)
    return paginated(db, select(Search).where(Search.workspace_id == p.workspace_id, Search.instance_id == identity)
        .order_by(Search.created_at.desc(), Search.id.desc()), offset, lambda row: o.search_view(db, p, row))
