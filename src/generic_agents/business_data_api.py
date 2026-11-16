"""Normal operator-facing business-data commands, views and exact recovery."""
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import Field, StrictInt, StrictBool, ValidationError
from sqlalchemy import select

from . import business_data as b, business_data_receipts as receipts
from .business_data_models import BusinessDataSource as Source, BusinessDataRead as Read, BusinessDataBinding as Binding
from .business_data_models import BusinessDataImport as Import, BusinessDataImportRow as ImportRow
from .contracts import Contract
from .services import scoped
from .setup_api import P, DB

router = APIRouter(prefix='/api/business-data')
Positive = Annotated[StrictInt, Field(ge=1)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class Command(Contract):
    request_id: UUID


class SourceCreate(Command):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal['http_json', 'csv', 'json', 'xlsx']
    endpoint: str | None = Field(default=None, max_length=2000)
    secret_ref: str | None = Field(default=None, max_length=150)


class SourceEdit(SourceCreate):
    expected_version: Positive


class Version(Command):
    expected_version: Positive


class Action(Version):
    reason: str = Field(min_length=1, max_length=2000)


class Upload(Version):
    file_name: str = Field(min_length=1, max_length=250)
    file_size: Annotated[StrictInt, Field(gt=0, le=b.MAX_BYTES)]
    file_sha256: Hash


class Mapping(Contract):
    target: str = Field(min_length=1, max_length=200)
    source: str = Field(max_length=1000)
    conversion: Literal['identity', 'string', 'integer', 'number', 'boolean', 'date']


class BindingCreate(Command):
    source_id: str
    source_expected_version: Positive
    read_id: str
    instance_id: str
    release_id: str
    object_type: str
    schema_hash: Hash
    record_set: str = Field(max_length=1000)  # Empty RFC6901 root is a real dataset.
    external_id_path: str = Field(max_length=1000)
    fields: list[Mapping] = Field(min_length=1, max_length=256)


class Preview(Command):
    read_id: str


class Confirm(Version):
    confirm_hash: Hash
    accept_partial: StrictBool = False


def body(value): return value.model_dump(mode='json')


@router.get('/catalog')
def catalog(p: P):
    from .business_data_reader import MAX_EXPANDED_BYTES, MAX_RECORDS, MAX_PARSED_BYTES, MAX_HTTP_BYTES
    return {'schema': 'business-data-catalog-v204', 'kinds': ['http_json', 'csv', 'json', 'xlsx'],
        'limits': {'original_bytes': b.MAX_BYTES, 'expanded_bytes': MAX_EXPANDED_BYTES,
            'records_per_set': MAX_RECORDS, 'parsed_bytes': MAX_PARSED_BYTES, 'http_bytes': MAX_HTTP_BYTES,
            'mapping_fields': 256},
        'scheduled_import': False, 'database_direct': False, 'source_time_verified': False}


@router.get('/requests/{request_id}')
def recover(request_id: UUID, p: P, db: DB, expected_hash: Hash | None = None):
    return b.recover_request(db, p, str(request_id), expected_hash)


@router.get('/sources')
def sources(p: P, db: DB):
    return [b.source_view(db, p, row) for row in db.scalars(select(Source).where(Source.workspace_id == p.workspace_id)
        .order_by(Source.created_at, Source.id))]


@router.post('/sources', status_code=201)
def create_source(value: SourceCreate, p: P, db: DB):
    result = b.save_source(db, p, body(value)); db.commit(); return result


@router.put('/sources/{identity}')
def edit_source(identity: str, value: SourceEdit, p: P, db: DB):
    result = b.save_source(db, p, body(value), identity); db.commit(); return result


@router.post('/sources/{identity}/pause')
def pause_source(identity: str, value: Action, p: P, db: DB):
    result = b.source_status(db, p, identity, body(value), 'paused'); db.commit(); return result


@router.post('/sources/{identity}/resume')
def resume_source(identity: str, value: Action, p: P, db: DB):
    result = b.source_status(db, p, identity, body(value), 'active'); db.commit(); return result


@router.post('/sources/{identity}/read', status_code=201)
def start_read(identity: str, value: Version, p: P, db: DB):
    result = b.start_read(db, p, identity, body(value)); db.commit(); return result


def _integer(value):
    try:
        parsed = int(value)
        if str(parsed) != value: raise ValueError()
        return parsed
    except (ValueError, TypeError): raise HTTPException(422, '上传版本与大小必须是明确整数') from None


@router.post('/sources/{identity}/uploads', status_code=201)
async def upload(identity: str, p: P, db: DB, request_id: str = Form(), expected_version: str = Form(),
        file_name: str = Form(), file_size: str = Form(), file_sha256: str = Form(), file: UploadFile = File()):
    try:
        value = Upload(request_id=request_id, expected_version=_integer(expected_version), file_name=file_name,
            file_size=_integer(file_size), file_sha256=file_sha256)
    except ValidationError:
        raise HTTPException(422, '上传请求、版本、文件名、大小或摘要不符合契约') from None
    b.need(file.filename == file_name and '/' not in file_name and '\\' not in file_name
        and not any(ord(char) < 32 for char in file_name), '原上传文件名与请求元数据不一致')
    content = await file.read(b.MAX_BYTES + 1); await file.close()
    result = b.start_read(db, p, identity, body(value), content); db.commit(); return result


@router.get('/reads')
def reads(source_id: str, p: P, db: DB):
    scoped(db, Source, source_id, p)
    return [b.read_view(row) for row in db.scalars(select(Read).where(Read.workspace_id == p.workspace_id,
        Read.source_id == source_id).order_by(Read.created_at.desc(), Read.id.desc()).limit(100))]


@router.get('/reads/{identity}')
def read(identity: str, p: P, db: DB): return b.read_view(scoped(db, Read, identity, p))


@router.get('/reads/{identity}/original')
def original(identity: str, p: P, db: DB):
    row = scoped(db, Read, identity, p); content = b.checked_read(db, p, row, parsed=False)
    return Response(content, media_type=row.data['raw_media_type'], headers={'Cache-Control': 'private, no-store',
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(row.data['name'], safe='')})


@router.post('/reads/{identity}/retry')
def retry_read(identity: str, value: Action, p: P, db: DB):
    result = b.retry_read(db, p, identity, body(value)); db.commit(); return result


@router.get('/instances/{identity}/options')
def target_options(identity: str, p: P, db: DB): return b.options(db, p, identity)[0]


@router.post('/bindings', status_code=201)
def binding(value: BindingCreate, p: P, db: DB):
    result = b.bind(db, p, body(value)); db.commit(); return result


@router.get('/bindings/{identity}')
def binding_detail(identity: str, p: P, db: DB):
    row = scoped(db, Binding, identity, p); b.checked_binding(db, p, row); return b.binding_view(row)


@router.post('/bindings/{identity}/previews', status_code=201)
def preview(identity: str, value: Preview, p: P, db: DB):
    result = b.start_preview(db, p, identity, body(value)); db.commit(); return result


@router.get('/imports')
def imports(source_id: str, p: P, db: DB, instance_id: str | None = None):
    scoped(db, Source, source_id, p)
    query = select(Import).where(Import.workspace_id == p.workspace_id, Import.source_id == source_id)
    if instance_id is not None:
        scoped(db, b.m.DomainInstance, instance_id, p)
        query = query.join(Binding, Binding.id == Import.binding_id).where(Binding.workspace_id == p.workspace_id, Binding.instance_id == instance_id)
    return [b.import_view(db, p, row) for row in db.scalars(query.order_by(Import.created_at.desc(), Import.id.desc()).limit(100))]


@router.get('/imports/{identity}')
def import_detail(identity: str, p: P, db: DB): return b.import_view(db, p, scoped(db, Import, identity, p))


@router.get('/imports/{identity}/rows')
def rows(identity: str, p: P, db: DB, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), status: str | None = None):
    scoped(db, Import, identity, p)
    query = select(ImportRow).where(ImportRow.workspace_id == p.workspace_id, ImportRow.import_id == identity)
    if status is not None: query = query.where(ImportRow.status == status)
    return [b.row_view(db, p, row) for row in db.scalars(query.order_by(ImportRow.ordinal).offset(offset).limit(limit))]


@router.post('/imports/{identity}/confirm')
def confirm(identity: str, value: Confirm, p: P, db: DB):
    result = b.confirm_import(db, p, identity, body(value)); db.commit(); return result


@router.post('/imports/{identity}/cancel')
def cancel(identity: str, value: Action, p: P, db: DB):
    result = b.control_import(db, p, identity, body(value), 'cancel'); db.commit(); return result


@router.post('/imports/{identity}/resume')
def resume(identity: str, value: Action, p: P, db: DB):
    result = b.control_import(db, p, identity, body(value), 'resume'); db.commit(); return result


@router.post('/imports/{identity}/rows/{row_id}/retry-data-delivery')
def retry_delivery(identity: str, row_id: str, value: Action, p: P, db: DB):
    result = receipts.retry_delivery(db, p, identity, row_id, body(value)); db.commit(); return result


@router.get('/sources/{identity}/days')
def days(identity: str, p: P, db: DB): return receipts.days(db, p, identity)


@router.get('/reads/{identity}/history')
def read_history(identity: str, p: P, db: DB, cursor: str | None = Query(None, max_length=768)):
    from .business_data_history import history
    return history(db, p, 'read', identity, cursor)


@router.get('/imports/{identity}/history')
def import_history(identity: str, p: P, db: DB, cursor: str | None = Query(None, max_length=768)):
    from .business_data_history import history
    return history(db, p, 'import', identity, cursor)


@router.post('/sources/{source_id}/days/{event_id}/retry-data-delivery')
def retry_day_delivery(source_id: str, event_id: str, value: Action, p: P, db: DB):
    result = receipts.retry_day_delivery(db, p, source_id, event_id, body(value)); db.commit(); return result
