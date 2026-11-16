"""Explicit single-report sibling routes; reads never prepare or wake Jobs."""
from typing import Annotated
from fastapi import APIRouter,Depends,Request,Response
from starlette.concurrency import run_in_threadpool
from .security import authenticated,Principal
from .db import session
from . import document_question_service as service
from .document_question_contracts import parse
from .source_query_contracts import MAX_BODY,need,uuid

router=APIRouter(prefix='/api/v2/data/document-questions')
P=Annotated[Principal,Depends(authenticated)]
DB=Annotated[object,Depends(session)]


async def read_body(request):
    size=0;parts=[]
    async for part in request.stream():
        size+=len(part);need(size<=MAX_BODY,'报告问题超过24KiB，未执行',413);parts.append(part)
    return b''.join(parts)


def transaction(db,p,raw):
    try:
        value,created=service.create(db,p,raw);db.commit();return value,created
    except BaseException:db.rollback();raise


@router.get('/catalog')
def catalog(instance_id:str,p:P,db:DB):
    try:uuid(instance_id)
    except ValueError:need(False,'实例编号格式无效',422)
    return service.catalog(db,p,instance_id)


@router.post('')
async def create(request:Request,response:Response,p:P,db:DB):
    raw=await read_body(request);parse(raw)
    result,created=await run_in_threadpool(transaction,db,p,raw)
    response.status_code=201 if created else 200
    return result


@router.get('/requests/{request_id}')
def recover(request_id:str,expected_sha256:str,p:P,db:DB):
    try:uuid(request_id)
    except ValueError:need(False,'原申请编号格式无效',422)
    return service.recover(db,p,request_id,expected_sha256)


@router.get('')
def history(instance_id:str,p:P,db:DB,cursor:str|None=None):
    try:uuid(instance_id)
    except ValueError:need(False,'实例编号格式无效',422)
    rows=sorted(service.inventory(db,p,instance_id),key=lambda row:(row.created_at,row.id),reverse=True)
    if cursor is not None:
        positions=[index for index,row in enumerate(rows) if row.id==cursor]
        need(len(positions)==1,'分页位置不属于此实例报告提问',422);rows=rows[positions[0]+1:]
    selected=rows[:20]
    return {'schema':'document-question-list-v1','instance_id':instance_id,
        'items':[service.view(db,p,row) for row in selected],
        'next_cursor':selected[-1].id if len(rows)>20 else None}


@router.get('/{query_id}')
def get(query_id:str,p:P,db:DB):return service.view(db,p,service.get(db,p,query_id))
