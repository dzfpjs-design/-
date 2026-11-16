"""Task draft source artifacts: selection is separate from normal175 revise."""
from uuid import UUID
from fastapi import APIRouter, Query, Request, HTTPException
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from .setup_api import P, DB
from .services import scoped
from . import runtime_task_documents as d
from .runtime_task_document_models_v207 import RuntimeTaskDocumentSourceSet as SourceSet
from .layered_approval_contract_v175 import original_json

router = APIRouter(prefix='/api/runtime-task-documents')


@router.get('/tasks/{identity}/context')
def context(identity: str, p: P, db: DB): return d.context(db, p, identity)


@router.post('/tasks/{identity}/source-sets', status_code=201)
async def create(identity: str, request: Request, p: P, db: DB):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 65536: raise HTTPException(413, '来源选择请求超过容量，原选择保留且未截断提交')
    try: value, _, _ = original_json(bytes(raw), d.CreateSourceSet)
    except (ValueError, TypeError, UnicodeError): raise HTTPException(422, '来源选择、字符范围或原请求不符合契约') from None
    def apply():
        result = d.create_set(db, p, identity, value.model_dump(mode='json')); db.commit(); return result
    return await run_in_threadpool(apply)


@router.get('/tasks/{identity}/source-sets')
def source_sets(identity: str, p: P, db: DB, offset: int = Query(0, ge=0, le=100000)):
    scoped(db, d.m.Task, identity, p)
    rows = list(db.scalars(select(SourceSet).where(SourceSet.workspace_id == p.workspace_id,
        SourceSet.task_id == identity).order_by(SourceSet.created_at.desc(), SourceSet.id.desc()).offset(offset).limit(21)))
    with d.native.verification_scope(db):
        records = [d.source_view(d.checked_set(db, p, row)) for row in rows[:20]]
    return {'records': records, 'offset': offset, 'has_more': len(rows) > 20}


@router.get('/source-sets/{identity}')
def source_set(identity: str, p: P, db: DB):
    with d.native.verification_scope(db): return d.source_view(d.checked_set(db, p, scoped(db, SourceSet, identity, p)))


@router.get('/requests/{identity}')
def recover(identity: UUID, p: P, db: DB, expected_hash: d.Hash):
    return d.recover(db, p, str(identity), expected_hash)


@router.get('/tasks/{identity}/packets')
def packets(identity: str, p: P, db: DB):
    from . import workflow_document_packet_v207 as packet
    return packet.list_packets(db, p, identity)


@router.get('/packets/{identity}')
def packet(identity: str, p: P, db: DB):
    from . import workflow_document_packet_v207 as packets
    return packets.get_packet(db, p, identity)
