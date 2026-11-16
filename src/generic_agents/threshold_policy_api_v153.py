"""Explicit finite numeric policy commands and original-request recovery."""
from copy import deepcopy
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .config import settings
from .models import uid
from .relations import lock_current_actor
from .security import require
from .services import audit, check_version
from .setup_api import DB, P
from .threshold_policy_models_v152 import ThresholdPolicyGrant as Grant, ThresholdPolicyCommand as Command, ThresholdPolicySelection as Selection
from .threshold_policy_protocol_v152 import canonical_uuid, identify, parse_command, read_command
from .threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from . import threshold_policy_store_v153 as store

router=APIRouter(prefix='/evolution')


def _uuid(value):
    try:return canonical_uuid(value)
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请保留有限策略原编号') from None


def _identify(method,path):
    try:return identify(method,path)
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请保留有限策略原方法和完整路径') from None


def wire_meta(p,path,raw):
    sha=wire_sha256(raw)
    return {'request_id':read_command(raw)['request_id'],'method':'POST','path':path,'wire_text':raw.decode('utf-8'),
        'wire_sha256':sha,'fingerprint':command_fingerprint('POST',path,p.workspace_id,p.user_id,sha)}


def _save_command(db,p,identity,raw,path,operation,target_id,policy_id,outcome,result):
    meta=wire_meta(p,path,raw)
    row=Command(id=identity,workspace_id=p.workspace_id,actor_id=p.user_id,version=1,
        request_id=meta['request_id'],method='POST',path=path,wire_sha256=meta['wire_sha256'],fingerprint=meta['fingerprint'],
        operation=operation,target_id=target_id,policy_id=policy_id,outcome=outcome,command_hash='',data={
            'schema':'threshold-policy-command-record-v153','wire_text':meta['wire_text'],'result_snapshot':deepcopy(result)})
    row.command_hash=store.hash_value(store.command_payload(row));db.add(row);db.flush()
    audit(db,p,store.COMMAND_ACTION,row.id,store.command_pin(row));db.flush();return row


def _create(db,p,body,command_id):
    from . import quality_review_store_v152 as quality
    from .threshold_shadow_store_v151 import frontier
    # Read only enough immutable scope to take the common scope lock first.
    preliminary=quality.get_review(db,p,body.review_id)
    store.need(preliminary.status=='approved','请先明确批准完整的质量审核')
    scope=preliminary.data['frozen']['scope'];store.lock_scope(db,scope)
    review,scope=store._source(quality.require_approved_review,db,p,body.review_id,lock=True)
    store.need(review.version==body.expected_review_version,'质量审核版本已变化，请保留原稿重新核对')
    environment=store._environment(review.data['frozen']['purpose'])
    grants,_=store.inventory(db,p);key=store.scope_key(scope)
    store.need(not any(g.scope_key==key and g.status!='rolled_back' for g in grants),
        '同一原范围已有未回滚策略，请先读取原策略')
    now=db.scalar(select(func.clock_timestamp()));expires=datetime.fromisoformat(body.expires_at)
    store.need(now<expires<=now+timedelta(days=7),'有限策略期限须在未来且不超过7天',422)
    cut=store._source(frontier,db,p,scope);frozen=review.data['frozen']
    row=Grant(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,review_id=review.id,
        scope_key=key,status='active',version=1,record_hash='',data={
            'schema':'threshold-policy-grant-record-v153','scope':deepcopy(scope),
            'review':{'pin':quality.review_pin(review),'approved_snapshot':quality.snapshot(review)},
            'baseline_manifest':deepcopy(frozen['baseline_manifest']),'candidate_manifest':deepcopy(frozen['candidate_manifest']),
            'purpose':frozen['purpose'],'criteria':deepcopy(frozen['criteria']),'frontier':cut,'epoch':1,
            'max_candidate_inputs':body.max_candidate_inputs,'expires_at':body.expires_at,'title':body.title,
            'reason':body.reason,'rollback':None,'environment_at_approval':environment})
    store.seal_grant(db,p,row,'create',command_id)
    from .threshold_policy_execution_v153 import ensure_monitor
    ensure_monitor(db,p,row);return row


def _control(db,p,row,operation,body,command_id):
    from .threshold_shadow_store_v151 import frontier
    check_version(row,body.expected_version);store.lock_scope(db,row.data['scope'])
    store.need(row.status!='rolled_back','策略已经永久回滚；原记录保留，不能恢复候选')
    if operation=='pause':
        store.need(row.status=='active','请先读取正在运行的原策略');row.status='paused'
    elif operation=='resume':
        store.need(row.status=='paused','只能明确恢复已暂停的原策略')
        store._source(store.fresh_candidate,db,p,row)
        store.need(datetime.fromisoformat(row.data['expires_at'])>db.scalar(select(func.clock_timestamp())),
            '原策略已到期；恢复不能延长期限')
        _,used=store.selection_usage(db,p,row)
        store.need(used<row.data['max_candidate_inputs'],'原候选次数已用完，恢复不退款')
        cut=store._source(frontier,db,p,row.data['scope'])
        for key in ('observation_ids','source_record_ids'):
            store.need(set(row.data['frontier'][key])<=set(cut[key]))
        row.status='active';row.data={**row.data,'frontier':cut,'epoch':row.data['epoch']+1}
    elif operation=='rollback':
        row.status='rolled_back';row.data={**row.data,'rollback':{
            'reason':body.reason,'origin':'human','selection_id':None,'basis':None}}
    else:raise HTTPException(422,'未声明的有限策略操作')
    row.data={**row.data,'reason':body.reason};store.seal_grant(db,p,row,operation,command_id)
    if operation=='resume':
        from .threshold_policy_execution_v153 import ensure_monitor
        ensure_monitor(db,p,row)
    return row


def _selection(db,p,identity):
    row=db.scalar(select(Selection).where(Selection.workspace_id==p.workspace_id,Selection.id==identity)
        .execution_options(populate_existing=True))
    store.need(row is not None);return row


def _result(db,p,command):
    if command.operation=='input':
        store.get_grant(db,p,command.policy_id)
        from .threshold_policy_execution_v153 import selection_view
        return selection_view(db,p,_selection(db,p,command.target_id))
    return store.view(db,p,command.policy_id)


async def _write(request,p,db,response):
    require(p,'admin','operator');lock_current_actor(db,p)
    raw=await request.body()
    try:value=read_command(raw)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise HTTPException(422,'请保留完整原JSON及有限策略请求编号') from None
    path=request.url.path
    if not path.isascii() or request.scope.get('raw_path',b'').decode('ascii',errors='replace')!=path:
        raise HTTPException(422,'策略路径不能编码或替换原编号')
    identity,operation,model=_identify(request.method,path)
    if operation!='input':require(p,'admin')
    store.lock_nonce(db,p,value['request_id']);_,commands=store.inventory(db,p)
    previous=next((c for c in commands if c.actor_id==p.user_id and c.request_id==value['request_id']),None)
    if previous:
        store.need(previous.path==path and previous.method=='POST' and previous.wire_sha256==wire_sha256(raw),
            '原编号已绑定另一份策略请求；请按原正文核对')
        if previous.outcome=='rejected':
            saved=previous.data['result_snapshot'];raise HTTPException(saved['status'],saved['detail'])
        response.status_code=202 if operation=='input' else 201 if operation=='create' else 200
        return _result(db,p,previous)
    row=store.get_grant(db,p,identity,lock=True) if identity is not None else None
    command_id=uid();target_id=identity;policy_id=identity
    try:
        with db.begin_nested():
            body=parse_command(model,value)
            if operation=='create':
                row=_create(db,p,body,command_id);saved=store.snapshot(row);target_id=policy_id=row.id
            elif operation=='input':
                check_version(row,body.expected_version)
                store.lock_source_key(db,p,row.data['scope']['source_id'],body.source_record_id)
                choice=store.current_choice(db,p,row)
                store.need(choice==body.expected_choice,'当前规则选择与原预览不一致，不会静默换规则')
                from .threshold_policy_execution_v153 import submit_input
                selection=submit_input(db,p,row,body,command_id,wire_meta(p,path,raw))
                saved=store.selection_snapshot(selection);target_id=selection.id
            else:row=_control(db,p,row,operation,body,command_id);saved=store.snapshot(row)
    except (store.PolicyInventoryError,store.PolicySourceError):raise
    except IntegrityError as error:
        code=getattr(error.orig,'sqlstate',None) or getattr(error.orig,'pgcode',None)
        if operation!='input' or code!='23505':raise
        detail='此原来源键或登记已被另一请求保存；不能通过新处理版本重复占用策略次数'
        _save_command(db,p,command_id,raw,path,operation,identity,identity,'rejected',{'status':409,'detail':detail})
        db.commit();raise HTTPException(409,detail) from None
    except HTTPException as error:
        from .quality_review_store_v152 import QualityInventoryError,QualitySourceError
        from .threshold_shadow_store_v151 import ShadowInventoryError
        if isinstance(error,(QualityInventoryError,QualitySourceError,ShadowInventoryError)) or error.status_code not in {409,422}:raise
        _save_command(db,p,command_id,raw,path,operation,identity,identity,'rejected',{
            'status':error.status_code,'detail':str(error.detail)[:2000]})
        db.commit();raise
    command=_save_command(db,p,command_id,raw,path,operation,target_id,policy_id,'saved',saved)
    store.get_grant(db,p,policy_id);db.commit()
    response.status_code=202 if operation=='input' else 201 if operation=='create' else 200
    return _result(db,p,command)


@router.post('/threshold-policies')
@router.post('/threshold-policies/{identity}/pause')
@router.post('/threshold-policies/{identity}/resume')
@router.post('/threshold-policies/{identity}/rollback')
@router.post('/threshold-policies/{identity}/inputs')
async def write_policy(request:Request,p:P,db:DB,response:Response):return await _write(request,p,db,response)


@router.get('/threshold-policy-options')
def policy_options(p:P,db:DB,review_id:str):return store.options(db,p,_uuid(review_id))


@router.get('/threshold-policy-requests/{request_id}')
def original_request(request_id:str,p:P,db:DB,expected_sha256:str,original_method:str,original_path:str):
    nonce=_uuid(request_id);_identify(original_method,original_path)
    if len(expected_sha256)!=64 or set(expected_sha256)-set('0123456789abcdef'):
        raise HTTPException(422,'请保留原策略请求SHA256')
    _,commands=store.inventory(db,p)
    candidates=[c for c in commands if c.actor_id==p.user_id and c.request_id==nonce]
    if not candidates and p.role=='admin':candidates=[c for c in commands if c.request_id==nonce]
    result={'schema':'threshold-policy-request-state-v153','status':'unknown','target_id':None,'policy_id':None,
        'version':None,'http_status':None,'detail':None}
    if not candidates:return result
    matches=[c for c in candidates if c.method==original_method and c.path==original_path and c.wire_sha256==expected_sha256]
    store.need(len(matches)==1,'原策略请求的方法、路径、作者或SHA无法唯一核对')
    command=matches[0]
    if command.outcome=='rejected':
        saved=command.data['result_snapshot'];result.update(status='rejected',target_id=command.target_id,
            policy_id=command.policy_id,http_status=saved['status'],detail=saved['detail'])
    else:
        store.get_grant(db,p,command.policy_id)
        result.update(status='saved',target_id=command.target_id,policy_id=command.policy_id,
            version=command.data['result_snapshot']['version'],
            http_status=202 if command.operation=='input' else 201 if command.operation=='create' else 200)
    return result


@router.get('/threshold-policies')
def policies(p:P,db:DB,review_id:str|None=None,limit:int=Query(50,ge=1,le=50),after:str|None=None):
    rows,_=store.inventory(db,p);review_id=_uuid(review_id) if review_id is not None else None
    after=_uuid(after) if after is not None else None
    visible=sorted((r for r in rows if (review_id is None or r.review_id==review_id)
        and (after is None or r.id>after)),key=lambda r:r.id)[:limit+1]
    return {'schema':'threshold-policy-list-v153','workspace_id':p.workspace_id,
        'grants':[store.view(db,p,row) for row in visible[:limit]],'next_after':visible[limit-1].id if len(visible)>limit else None}


@router.get('/threshold-policies/{identity}')
def policy(identity:str,p:P,db:DB):return store.view(db,p,_uuid(identity))


@router.get('/threshold-policies/{identity}/selections/{selection_id}')
def selected_input(identity:str,selection_id:str,p:P,db:DB):
    row=store.get_grant(db,p,_uuid(identity));selection=_selection(db,p,_uuid(selection_id))
    store.need(selection.policy_id==row.id)
    from .threshold_policy_execution_v153 import selection_view
    return selection_view(db,p,selection)
