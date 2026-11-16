"""Durable human quality review commands; no execution or policy approval."""
from copy import deepcopy

from fastapi import APIRouter, HTTPException, Query, Request, Response

from .models import uid
from .relations import lock_current_actor
from .security import require
from .services import audit, check_version
from .setup_api import DB, P
from .quality_review_models_v152 import ThresholdQualityReview as Review, ThresholdQualityCommand as Command
from .quality_review_protocol_v152 import canonical_uuid, identify, parse_command, read_command
from .threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from . import quality_review_store_v152 as store

router=APIRouter(prefix='/evolution')


def _uuid(value):
    try:return canonical_uuid(value)
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请保留质量审核原编号') from None


def _identify(method,path):
    try:return identify(method,path)
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请保留质量审核原方法和完整路径') from None


def _save_command(db,p,identity,raw,path,operation,target_id,outcome,result):
    sha=wire_sha256(raw)
    row=Command(id=identity,workspace_id=p.workspace_id,actor_id=p.user_id,version=1,
        request_id=read_command(raw)['request_id'],method='POST',path=path,wire_sha256=sha,
        fingerprint=command_fingerprint('POST',path,p.workspace_id,p.user_id,sha),operation=operation,
        target_id=target_id,outcome=outcome,command_hash='',data={
            'schema':'threshold-quality-command-record-v152','wire_text':raw.decode('utf-8'),
            'result_snapshot':deepcopy(result)})
    row.command_hash=store.hash_value(store.command_payload(row));db.add(row);db.flush()
    audit(db,p,store.COMMAND_ACTION,row.id,store.command_pin(row));db.flush();return row


def _context(db,p,body):
    actual=store._source(store.evidence_context,db,p,body.evaluation_id,body.shadow_id,lock=True)
    store.need(actual['evaluation'].version==body.expected_evaluation_version
        and actual['shadow'].version==body.expected_shadow_version,'原评估或影子版本已变化，请保留草稿重新核对')
    draft=store.draft_data(body)
    store._source(store.validate_refs,db,p,draft,actual['context'],lock=True)
    return draft,actual['context']


def _create(db,p,body,command_id):
    draft,context=_context(db,p,body)
    row=Review(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,evaluation_id=body.evaluation_id,
        shadow_id=body.shadow_id,status='draft',approved_by=None,approval_hash=None,record_hash='',version=1,
        data={'schema':'threshold-quality-review-record-v152','draft':draft,'context':context,'frozen':None})
    store.seal_review(db,p,row,'create',command_id);return row


def _change(db,p,identity,operation,body,command_id):
    row=store.get_review(db,p,identity,lock=True);check_version(row,body.expected_version)
    if operation=='update':
        store.need(p.role=='admin' or row.actor_id==p.user_id,'只能保存自己尚未提交的质量审核草稿',403)
        store.need(row.status=='draft','只有草稿可修改，原批准或撤回记录保留')
        draft,context=_context(db,p,body)
        row.evaluation_id=body.evaluation_id;row.shadow_id=body.shadow_id
        row.data={**row.data,'draft':draft,'context':context}
    elif operation=='approve':
        store.need(row.status=='draft','请先读取尚未批准的质量审核草稿')
        frozen,issues,_=store.freeze_data(db,p,row,lock=True)
        store.need(not issues,'；'.join(issues))
        row.status='approved';row.approved_by=p.user_id;row.approval_hash=store.hash_value(frozen)
        row.data={**row.data,'frozen':frozen}
    elif operation=='reject':
        store.need(row.status=='draft','只能拒绝尚未批准的质量审核草稿')
        row.status='rejected'
    elif operation=='revoke':
        store.need(row.status=='approved','只能撤回已经批准的质量审核')
        row.status='revoked'
    else:raise HTTPException(422,'未声明的质量审核操作')
    store.seal_review(db,p,row,operation,command_id);return row


async def _write(request,p,db,response):
    require(p,'admin','operator');lock_current_actor(db,p)
    raw=await request.body()
    try:value=read_command(raw)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise HTTPException(422,'请保留完整的原JSON与质量审核请求编号') from None
    path=request.url.path
    if request.scope.get('raw_path',b'').decode('ascii',errors='replace')!=path:
        raise HTTPException(422,'质量审核路径不能编码或替换原编号')
    identity,operation,model=_identify(request.method,path)
    if operation in {'approve','reject','revoke'}:require(p,'admin')
    store.lock_nonce(db,p,value['request_id']);_,commands=store.inventory(db,p)
    previous=next((row for row in commands if row.actor_id==p.user_id and row.request_id==value['request_id']),None)
    if previous:
        store.need(previous.method=='POST' and previous.path==path and previous.wire_sha256==wire_sha256(raw),
            '原请求编号已绑定另一份正文或操作；请保留原请求核对')
        if previous.outcome=='rejected':
            result=previous.data['result_snapshot'];raise HTTPException(result['status'],result['detail'])
        response.status_code=201 if operation=='create' else 200
        return store.view(db,p,store.get_review(db,p,previous.target_id))
    # A lost original target/Command/Audit is an integrity failure, not a new
    # human rejection signed onto the submitted nonce.
    if identity is not None:store.get_review(db,p,identity,lock=True)
    command_id=uid()
    try:
        with db.begin_nested():
            body=parse_command(model,value)
            row=_create(db,p,body,command_id) if operation=='create' else _change(db,p,identity,operation,body,command_id)
            saved=store.snapshot(row)
    except (store.QualityInventoryError,store.QualitySourceError):raise
    except HTTPException as error:
        if error.status_code not in {409,422}:raise
        _save_command(db,p,command_id,raw,path,operation,identity,'rejected',{
            'status':error.status_code,'detail':str(error.detail)[:2000]})
        db.commit();raise
    _save_command(db,p,command_id,raw,path,operation,row.id,'saved',saved)
    store.get_review(db,p,row.id);db.commit()
    response.status_code=201 if operation=='create' else 200
    return store.view(db,p,row)


@router.post('/threshold-quality-reviews')
@router.post('/threshold-quality-reviews/{identity}/update')
@router.post('/threshold-quality-reviews/{identity}/approve')
@router.post('/threshold-quality-reviews/{identity}/reject')
@router.post('/threshold-quality-reviews/{identity}/revoke')
async def write_review(request:Request,p:P,db:DB,response:Response):
    return await _write(request,p,db,response)


@router.get('/threshold-quality-options')
def quality_options(p:P,db:DB,evaluation_id:str,shadow_id:str|None=None):
    return store.options(db,p,_uuid(evaluation_id),_uuid(shadow_id) if shadow_id is not None else None)


@router.get('/threshold-quality-requests/{request_id}')
def original_request(request_id:str,p:P,db:DB,expected_sha256:str,original_method:str,original_path:str):
    nonce=_uuid(request_id);_identify(original_method,original_path)
    if len(expected_sha256)!=64 or set(expected_sha256)-set('0123456789abcdef'):
        raise HTTPException(422,'请保留质量审核原请求SHA256')
    _,commands=store.inventory(db,p)
    candidates=[row for row in commands if row.request_id==nonce and row.actor_id==p.user_id]
    if not candidates and p.role=='admin':candidates=[row for row in commands if row.request_id==nonce]
    result={'schema':'threshold-quality-request-state-v152','status':'unknown','target_id':None,
        'version':None,'http_status':None,'detail':None}
    if not candidates:return result
    matches=[row for row in candidates if row.method==original_method and row.path==original_path
        and row.wire_sha256==expected_sha256]
    store.need(len(matches)==1,'原请求的方法、路径、作者或SHA无法唯一核对')
    row=matches[0]
    if row.outcome=='rejected':
        value=row.data['result_snapshot'];result.update(status='rejected',target_id=row.target_id,
            http_status=value['status'],detail=value['detail'])
    else:
        store.get_review(db,p,row.target_id)
        result.update(status='saved',target_id=row.target_id,version=row.data['result_snapshot']['version'],
            http_status=201 if row.operation=='create' else 200)
    return result


@router.get('/threshold-quality-reviews')
def reviews(p:P,db:DB,evaluation_id:str|None=None,limit:int=Query(50,ge=1,le=50),after:str|None=None):
    rows,_=store.inventory(db,p)
    evaluation_id=_uuid(evaluation_id) if evaluation_id is not None else None
    after=_uuid(after) if after is not None else None
    visible=sorted((row for row in rows if (evaluation_id is None or row.evaluation_id==evaluation_id)
        and (after is None or row.id>after)),key=lambda row:row.id)[:limit+1]
    return {'schema':'threshold-quality-review-list-v152','workspace_id':p.workspace_id,
        'reviews':[store.view(db,p,row) for row in visible[:limit]],
        'next_after':visible[limit-1].id if len(visible)>limit else None}


@router.get('/threshold-quality-reviews/{identity}')
def review(identity:str,p:P,db:DB):
    return store.view(db,p,store.get_review(db,p,_uuid(identity)))
