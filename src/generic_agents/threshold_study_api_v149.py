"""Human-owned immutable threshold studies with original-command recovery."""
from copy import deepcopy
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select

from .db import session
from .models import Audit, Job, uid
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit, check_version
from .threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from .threshold_study_models_v149 import (ThresholdStudyCandidate as Candidate,
    ThresholdStudyEvaluation as Evaluation, ThresholdStudyCommand as Command)
from .threshold_study_protocol_v149 import canonical_uuid, read_command
from . import threshold_study_store_v149 as store

router = APIRouter(prefix='/evolution')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


def _uuid(value):
    try: return canonical_uuid(value)
    except (ValueError, TypeError): raise HTTPException(422, '请保留原小写UUID编号') from None


def _owner(p, row):
    if p.user_id != row.actor_id and p.role != 'admin':
        raise HTTPException(403, '仅原填写人或工作区管理员可以修改，其他成员可以查阅')


def _view(db,p,kind,row,*,snapshot_only=False,inventory_checked=False):
    from .threshold_study_views_v149 import candidate_view,evaluation_view
    return (candidate_view if kind=='candidate' else evaluation_view)(
        db,p,row,snapshot_only=snapshot_only,inventory_checked=inventory_checked)


def _draft(body):
    return {key:deepcopy(value) for key,value in body.model_dump(by_alias=True).items()
            if key not in {'schema','request_id','expected_version'}}


def _validate_draft(db,p,kind,row):
    if kind=='candidate':
        store.candidate_freeze_data(db,p,row,require_error=False)
    else:
        candidate=store.get_record(db,p,'candidate',row.candidate_id)
        frozen=store.frozen_candidate_body(db,p,candidate)
        store.need(candidate.version==row.data['draft']['expected_candidate_version'],
                   '原候选版本已变化，请核对原候选后保存')
        refs=row.data['draft']['feedback_refs'];store._lock_refs(db,p,refs)
        store.current_samples(db,p,refs,frozen['baseline_manifest'],'evaluation')


def _apply(db,p,kind,identity,operation,body,command_id):
    action=operation.split('.')[1]
    if action=='create':
        if kind=='candidate':
            row=Candidate(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,
                version=1,status='draft',record_hash='',baseline_capability_id=body.baseline_capability_id,
                candidate_capability_id=body.candidate_capability_id,
                data={'schema':'threshold-study-candidate-record-v149','draft':_draft(body),
                      'freeze':None,'freeze_hash':None})
        else:
            row=Evaluation(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,
                candidate_id=body.candidate_id,version=1,status='draft',record_hash='',
                data={'schema':'threshold-study-evaluation-record-v149','draft':_draft(body),
                      'freeze':None,'freeze_hash':None,'job_id':None,'job_actor_id':None,
                      'reservations':{},'receipts':{},'result':None,'error':None})
        _validate_draft(db,p,kind,row)
        store.change_state(db,p,row,operation,command_id=command_id,initial=True)
        return row
    row=store.get_record(db,p,kind,identity,lock=True);_owner(p,row)
    check_version(row,body.expected_version)
    if action=='update':
        store.need(row.status=='draft','已冻结方案不能覆盖，原稿和比较结果保留')
        if kind=='candidate':
            store.need(body.baseline_capability_id==row.baseline_capability_id,
                '原基线不能替换，请另建候选方案')
            row.candidate_capability_id=body.candidate_capability_id
        else: store.need(body.candidate_id==row.candidate_id,'原候选方案不能替换，请另建评估')
        row.data={**row.data,'draft':_draft(body)}
        _validate_draft(db,p,kind,row)
    elif action=='freeze':
        store.need(row.status=='draft','此方案已冻结，不能重复冻结或覆盖')
        frozen=(store.candidate_freeze_data if kind=='candidate' else store.evaluation_freeze_data)(db,p,row)
        row.data={**row.data,'freeze':frozen,'freeze_hash':store.hash_value(frozen)};row.status='frozen'
    elif action=='run':
        store.need(row.status=='frozen' and row.data['job_id'] is None,
                   '原比较已经安排或已终止，请刷新原结果，不会创建第二个后台任务')
        store.need(body.expected_freeze_hash==row.data['freeze_hash'],'原冻结摘要已变化，请核对原评估')
        store.fresh_evaluation(db,p,row)
        job=Job(id=uid(),workspace_id=p.workspace_id,kind='evaluate_threshold_study_v149',
                status='queued',attempts=0,idempotency_key='threshold-study:'+row.id,
                data={'actor_id':p.user_id,'candidate_id':row.candidate_id,
                    'eval_id':row.id,'freeze_hash':row.data['freeze_hash']})
        db.add(job)
        row.data={**row.data,'job_id':job.id,'job_actor_id':p.user_id};row.status='queued'
    elif action=='cancel':
        store.need(row.status in {'frozen','queued','running'},'此评估已经结束或尚未冻结，原记录保留')
        row.status='cancelled';row.data={**row.data,'error':body.reason}
    else: raise HTTPException(422,'未声明的研究操作')
    store.change_state(db,p,row,operation,command_id=command_id)
    return row


def _save_command(db,p,*,identity,raw,method,path,kind,operation,target_id,outcome,result):
    sha=wire_sha256(raw)
    command=Command(id=identity,workspace_id=p.workspace_id,actor_id=p.user_id,
        version=1,request_id=read_command(raw)['request_id'],method=method,path=path,wire_sha256=sha,
        fingerprint=command_fingerprint(method,path,p.workspace_id,p.user_id,sha),operation=operation,
        target_kind=kind,target_id=target_id,outcome=outcome,command_hash='',
        data={'schema':'threshold-study-command-record-v149','wire_text':raw.decode('utf-8'),
              'result_snapshot':deepcopy(result)})
    command.command_hash=store.hash_value(store.command_payload(command));db.add(command);db.flush()
    audit(db,p,store.COMMAND_ACTION,command.id,store.command_pin(command));db.flush()
    return command


async def _command(request,p,db,response):
    require(p,'admin','operator');lock_current_actor(db,p)
    raw=await request.body()
    try: value=read_command(raw)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise HTTPException(422,'请求须为完整原JSON和原请求编号，请保留输入后核对') from None
    method,path=request.method,request.url.path
    if request.scope.get('raw_path',b'').decode('ascii',errors='replace')!=path:
        raise HTTPException(422,'请使用原始研究路径，不能编码或替换原记录编号')
    kind,identity,operation,model=store.identify(method,path)
    store.lock_nonce(db,p,value['request_id'])
    commands=store.command_inventory(db,p)
    previous=next((c for c in commands if c.actor_id==p.user_id and c.request_id==value['request_id']),None)
    status=201 if operation.endswith('.create') else 202 if operation.endswith('.run') else 200
    if previous:
        store.need(previous.method==method and previous.path==path and previous.wire_sha256==wire_sha256(raw),
                   '原请求编号已绑定另一份原文，请先核对原研究请求')
        if previous.outcome=='rejected':
            raise HTTPException(previous.data['result_snapshot']['status'],previous.data['result_snapshot']['reason'])
        store.get_record(db,p,kind,previous.target_id)
        original=store.MODELS[kind](**deepcopy(previous.data['result_snapshot']))
        response.status_code=status
        return _view(db,p,kind,original,snapshot_only=True)
    if identity is not None:
        existing=store.get_record(db,p,kind,identity,lock=True);_owner(p,existing)
    command_id=uid()
    try:
        with db.begin_nested():
            body=store.parse_command(model,value)
            row=_apply(db,p,kind,identity,operation,body,command_id)
            original=store.snapshot(row)
    except store.StudyInventoryError: raise
    except HTTPException as exc:
        # Damaged feedback/typed inventory is not a decision on this human form.
        from .threshold_evolution_api_v147 import ERROR as FEEDBACK_INVENTORY
        from .typed_payload_codec_v131 import ERROR as TYPED_INVENTORY
        if exc.status_code not in {409,422} or exc.detail in {FEEDBACK_INVENTORY,TYPED_INVENTORY}:
            raise
        _save_command(db,p,identity=command_id,raw=raw,method=method,path=path,kind=kind,
            operation=operation,target_id=identity,outcome='rejected',result={
                'schema':'threshold-study-rejected-v149','status':exc.status_code,'reason':str(exc.detail)})
        db.commit();raise
    _save_command(db,p,identity=command_id,raw=raw,method=method,path=path,kind=kind,
        operation=operation,target_id=row.id,outcome='saved',result=original)
    store.get_record(db,p,kind,row.id)
    db.commit();response.status_code=status
    return _view(db,p,kind,row)


@router.post('/threshold-candidates')
@router.put('/threshold-candidates/{identity}')
@router.post('/threshold-candidates/{identity}/freeze')
@router.post('/threshold-evaluations')
@router.put('/threshold-evaluations/{identity}')
@router.post('/threshold-evaluations/{identity}/freeze')
@router.post('/threshold-evaluations/{identity}/run')
@router.post('/threshold-evaluations/{identity}/cancel')
async def write_study(request:Request,p:P,db:DB,response:Response):
    return await _command(request,p,db,response)


@router.get('/threshold-study-options')
def options(p:P,db:DB,baseline_capability_id:str):
    from .threshold_study_views_v149 import options_view
    return options_view(db,p,_uuid(baseline_capability_id))


@router.get('/threshold-study-requests/{request_id}')
def original_request(request_id:str,p:P,db:DB,expected_sha256:str,
                     original_method:str,original_path:str):
    nonce=_uuid(request_id)
    if len(expected_sha256)!=64 or set(expected_sha256)-set('0123456789abcdef'):
        raise HTTPException(422,'请保留原请求摘要')
    store.identify(original_method,original_path)
    command=next((c for c in store.command_inventory(db,p)
                  if c.actor_id==p.user_id and c.request_id==nonce),None)
    result={'schema':'threshold-study-request-state-v149','found':command is not None,
            'saved':None,'rejected':None}
    if command is None: return result
    store.need(command.method==original_method and command.path==original_path
        and command.wire_sha256==expected_sha256,'原请求的方法、路径或摘要不一致，请保留原文后核对')
    if command.outcome=='rejected':
        result['rejected']={'command_id':command.id,**{key:command.data['result_snapshot'][key]
            for key in ('status','reason')},'wire_sha256':command.wire_sha256}
    else:
        store.get_record(db,p,command.target_kind,command.target_id)
        result['saved']={'command_id':command.id,'target_kind':command.target_kind,'target_id':command.target_id,
            'version':command.data['result_snapshot']['version'],
            'result_hash':store.hash_value(command.data['result_snapshot'])}
    return result


def _list(db,p,kind,limit,after,filter_id):
    store.command_inventory(db,p)
    model=store.MODELS[kind];query=select(model).where(model.workspace_id==p.workspace_id)
    if filter_id:
        column=model.baseline_capability_id if kind=='candidate' else model.candidate_id
        query=query.where(column==_uuid(filter_id))
    if after: query=query.where(model.id>_uuid(after))
    rows=list(db.scalars(query.order_by(model.id).limit(limit+1)))
    visible=rows[:limit]
    return {'schema':'threshold-study-'+kind+'-list-v149','workspace_id':p.workspace_id,
        'candidates' if kind=='candidate' else 'evaluations':[
            _view(db,p,kind,store.get_record(db,p,kind,row.id),inventory_checked=True) for row in visible],
        'next_after':visible[-1].id if len(rows)>limit else None}


@router.get('/threshold-candidates')
def candidates(p:P,db:DB,limit:int=Query(50,ge=1,le=50),after:str|None=None,
               baseline_capability_id:str|None=None):
    return _list(db,p,'candidate',limit,after,baseline_capability_id)


@router.get('/threshold-evaluations')
def evaluations(p:P,db:DB,limit:int=Query(50,ge=1,le=50),after:str|None=None,candidate_id:str|None=None):
    return _list(db,p,'evaluation',limit,after,candidate_id)


@router.get('/threshold-candidates/{identity}')
def candidate(identity:str,p:P,db:DB):
    return _view(db,p,'candidate',store.get_record(db,p,'candidate',_uuid(identity)),inventory_checked=True)


@router.get('/threshold-evaluations/{identity}')
def evaluation(identity:str,p:P,db:DB):
    return _view(db,p,'evaluation',store.get_record(db,p,'evaluation',_uuid(identity)),inventory_checked=True)
