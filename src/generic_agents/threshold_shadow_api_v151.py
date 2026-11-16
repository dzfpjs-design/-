"""Explicit administrator shadow grants; baseline production is unchanged."""
from copy import deepcopy
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func, select

from .models import uid
from .relations import lock_current_actor
from .security import require
from .services import audit, check_version
from .setup_api import DB, P
from .threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from .threshold_shadow_models_v151 import ThresholdShadowCommand as Command, ThresholdShadowGrant as Grant
from .threshold_shadow_protocol_v151 import ROOT, canonical_uuid, identify, parse_command, read_command
from . import threshold_shadow_store_v151 as store

router=APIRouter(prefix='/evolution')


def _uuid(value):
    try:return canonical_uuid(value)
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请保留原影子观察编号') from None


def _identify(method,path):
    try:return identify(method,path)
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请保留原影子请求的方法和路径') from None


def _save_command(db,p,command_id,raw,path,operation,target_id,outcome,result):
    sha=wire_sha256(raw)
    row=Command(id=command_id,workspace_id=p.workspace_id,actor_id=p.user_id,version=1,
        request_id=read_command(raw)['request_id'],method='POST',path=path,wire_sha256=sha,
        fingerprint=command_fingerprint('POST',path,p.workspace_id,p.user_id,sha),operation=operation,
        target_id=target_id,outcome=outcome,command_hash='',data={
            'schema':'threshold-shadow-command-record-v151','wire_text':raw.decode('utf-8'),
            'result_snapshot':deepcopy(result)})
    row.command_hash=store.hash_value(store.command_payload(row));db.add(row);db.flush()
    audit(db,p,store.COMMAND_ACTION,row.id,store.command_pin(row));db.flush()
    return row


def _create(db,p,body,command_id):
    evaluation,scope,pin=store.evaluation_context(db,p,body.evaluation_id)
    store.need(evaluation.version==body.expected_evaluation_version,'原评估版本已变化，请保留原稿后重新核对')
    store.lock_scope(db,scope)
    store.lock_resources(db,p,scope)
    # Refresh after the scope lock; an earlier read grants no authority.
    evaluation,scope,pin=store.evaluation_context(db,p,body.evaluation_id)
    store.need(evaluation.version==body.expected_evaluation_version,'原评估版本已变化，请重新核对')
    grants,_=store.inventory(db,p);key=store.scope_key(scope)
    store.need(not any(row.scope_key==key and row.status!='ended' for row in grants),
        '此原范围已有未结束的影子观察，请先核对或结束原授权')
    now=db.scalar(select(func.clock_timestamp()));expires=datetime.fromisoformat(body.expires_at)
    store.need(now<expires<=now+timedelta(days=7),'影子截止时间须在未来且不超过7天',422)
    cut=store.frontier(db,p,scope)
    row=Grant(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,evaluation_id=evaluation.id,
        scope_key=key,status='active',version=1,record_hash='',data={
            'schema':'threshold-shadow-grant-record-v151','scope':scope,'evaluation':pin,
            'frontier':cut,'epoch':1,'max_inputs':body.max_inputs,'expires_at':body.expires_at,
            'title':body.title,'reason':body.reason})
    store.seal_grant(db,p,row,'create',command_id)
    return row


def _control(db,p,identity,operation,body,command_id):
    row=store.get_grant(db,p,identity,lock=True)
    store.lock_scope(db,row.data['scope'])
    check_version(row,body.expected_version)
    store.need(row.status!='ended','影子观察已永久结束，原记录保留，不能恢复')
    if operation=='pause':
        store.need(row.status=='active','当前影子观察不是运行状态，请核对原记录')
        row.status='paused'
    elif operation=='resume':
        store.need(row.status=='paused','请先读取已暂停的原影子观察')
        store.fresh_grant(db,p,row)
        now=db.scalar(select(func.clock_timestamp()))
        store.need(datetime.fromisoformat(row.data['expires_at'])>now,'原期限已到，恢复不能延长授权')
        from .threshold_shadow_execution_v151 import consumption_inventory
        store.need(len(consumption_inventory(db,p,row))<row.data['max_inputs'],
            '影子观察处理次数已用完；恢复不退款、不重置次数')
        cut=store.frontier(db,p,row.data['scope'])
        for key in ('observation_ids','source_record_ids'):
            if not set(row.data['frontier'][key])<=set(cut[key]):raise store.ShadowInventoryError()
        row.status='active';row.data={**row.data,'frontier':cut,'epoch':row.data['epoch']+1}
    elif operation=='end':row.status='ended'
    else:raise HTTPException(422,'未声明的影子观察操作')
    row.data={**row.data,'reason':body.reason}
    store.seal_grant(db,p,row,operation,command_id)
    return row


async def _write(request,p,db,response):
    require(p,'admin');lock_current_actor(db,p)
    raw=await request.body()
    try:value=read_command(raw)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise HTTPException(422,'请保留完整的原JSON和影子请求编号') from None
    path=request.url.path
    if request.scope.get('raw_path',b'').decode('ascii',errors='replace')!=path:
        raise HTTPException(422,'影子请求路径不能编码或替换原编号')
    identity,operation,model=_identify(request.method,path)
    store.lock_nonce(db,p,value['request_id']);_,commands=store.inventory(db,p)
    previous=next((c for c in commands if c.actor_id==p.user_id and c.request_id==value['request_id']),None)
    if previous:
        store.need(previous.method=='POST' and previous.path==path and previous.wire_sha256==wire_sha256(raw),
            '原编号已绑定另一份请求，请核对原正文，不能更换范围或操作')
        if previous.outcome=='rejected':
            result=previous.data['result_snapshot'];raise HTTPException(result['status'],result['detail'])
        response.status_code=201 if operation=='create' else 200
        return store.view(db,p,store.get_grant(db,p,previous.target_id))
    # Missing/damaged target evidence is never signed as a human rejection.
    if identity is not None:store.get_grant(db,p,identity,lock=True)
    command_id=uid()
    try:
        with db.begin_nested():
            body=parse_command(model,value)
            row=_create(db,p,body,command_id) if operation=='create' else _control(db,p,identity,operation,body,command_id)
            original=store.snapshot(row)
    except store.ShadowInventoryError:raise
    except HTTPException as exc:
        from .threshold_study_store_v149 import StudyInventoryError
        from .threshold_evolution_api_v147 import ERROR as FEEDBACK_ERROR, LEGACY_ERROR
        from .typed_payload_codec_v131 import ERROR as TYPED_ERROR
        if isinstance(exc,StudyInventoryError) or exc.status_code not in {409,422} or (
            type(exc.detail) is str and exc.detail in {FEEDBACK_ERROR,LEGACY_ERROR,TYPED_ERROR}):
            raise
        _save_command(db,p,command_id,raw,path,operation,identity,'rejected',{
            'status':exc.status_code,'detail':str(exc.detail)[:2000]})
        db.commit();raise
    _save_command(db,p,command_id,raw,path,operation,row.id,'saved',original)
    store.get_grant(db,p,row.id)
    db.commit();response.status_code=201 if operation=='create' else 200
    return store.view(db,p,row)


@router.post('/threshold-shadows')
@router.post('/threshold-shadows/{identity}/pause')
@router.post('/threshold-shadows/{identity}/resume')
@router.post('/threshold-shadows/{identity}/end')
async def write_shadow(request:Request,p:P,db:DB,response:Response):
    return await _write(request,p,db,response)


@router.get('/threshold-shadow-options')
def shadow_options(p:P,db:DB,evaluation_id:str):
    return store.options(db,p,_uuid(evaluation_id))


@router.get('/threshold-shadow-requests/{request_id}')
def original_request(request_id:str,p:P,db:DB,expected_sha256:str,original_method:str,original_path:str):
    nonce=_uuid(request_id);_identify(original_method,original_path)
    if len(expected_sha256)!=64 or set(expected_sha256)-set('0123456789abcdef'):
        raise HTTPException(422,'请保留原影子请求SHA256')
    _,commands=store.inventory(db,p)
    candidates=[row for row in commands if row.request_id==nonce and row.actor_id==p.user_id]
    if not candidates and p.role=='admin':
        candidates=[row for row in commands if row.request_id==nonce]
    result={'schema':'threshold-shadow-request-state-v151','status':'unknown','target_id':None,
            'version':None,'http_status':None,'detail':None}
    if not candidates:return result
    matches=[row for row in candidates if row.method==original_method and row.path==original_path
             and row.wire_sha256==expected_sha256]
    store.need(len(matches)==1,'原影子请求的方法、路径、作者或SHA无法唯一核对')
    row=matches[0]
    if row.outcome=='rejected':
        saved=row.data['result_snapshot'];result.update(status='rejected',target_id=row.target_id,
            http_status=saved['status'],detail=saved['detail'])
    else:
        grant=store.get_grant(db,p,row.target_id)
        result.update(status='saved',target_id=grant.id,version=row.data['result_snapshot']['version'],
            http_status=201 if row.operation=='create' else 200)
    return result


@router.get('/threshold-shadows')
def shadows(p:P,db:DB,evaluation_id:str|None=None,limit:int=Query(50,ge=1,le=50),after:str|None=None):
    checked=store.inventory(db,p)
    checked=(*checked,{row.id:store.projection_pin(row) for row in checked[0]})
    evaluation_id=_uuid(evaluation_id) if evaluation_id else None
    after=_uuid(after) if after else None
    rows=sorted((row for row in checked[0] if
        (evaluation_id is None or row.evaluation_id==evaluation_id)
        and (after is None or row.id>after)),key=lambda row:row.id)[:limit+1]
    visible=rows[:limit]
    readonly_context={}
    views=[store.view(db,p,row,_verified_inventory=checked,_readonly_context=readonly_context) for row in visible]
    store.need(all(store._same(checked[2][row.id],store.projection_pin(row)) for row in visible))
    return {'schema':'threshold-shadow-list-v151','workspace_id':p.workspace_id,
        'grants':views,
        'next_after':visible[-1].id if len(rows)>limit else None}


@router.get('/threshold-shadows/{identity}')
def shadow(identity:str,p:P,db:DB):
    return store.view(db,p,store.get_grant(db,p,_uuid(identity)))
