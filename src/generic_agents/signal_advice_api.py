"""Saved preview/consent, human decisions and idempotent local delegation."""
from typing import Literal
from pydantic import Field
from fastapi import APIRouter,HTTPException
from sqlalchemy import select,text
from .contracts import Contract,digest
from .models import SignalAdvice,PerceptionSignal,Job,DomainInstance,now
from .setup_api import P,DB
from .services import scoped,require,check_version,audit
from .signal_advice import prepare,checked,current,Action,signed,validate_action

router=APIRouter(prefix='/api/signal-advice')
class Preview(Contract):
    instance_id:str
    signal_id:str
    model_profile_id:str
class Start(Preview):
    preview_hash:str
    authorize_cloud_text:Literal[True]
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    parent_id:str|None=None
    acknowledge_possible_cost:bool=False
class Draft(Contract):
    version:int=Field(ge=1,strict=True)
    action:Action
class Confirm(Contract):
    version:int=Field(ge=1,strict=True)
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    acknowledge_limits:Literal[True]
    acknowledge_new_local_processing:bool=False
class Revalidate(Contract):
    version:int=Field(ge=1,strict=True)
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
class Version(Contract):version:int=Field(ge=1,strict=True)

def view(row):
    return {'id':row.id,'request_id':row.request_id,'signal_id':row.signal_id,'instance_id':row.instance_id,'version':row.version,'status':row.status,
        'created_at':row.created_at.isoformat(),'has_saved_response':bool(row.data.get('raw_response')),**{k:v for k,v in row.data.items() if k!='raw_response'}}
def editable(db,p,identity):
    require(p,'admin','operator');row=checked(scoped(db,SignalAdvice,identity,p,True))
    if row.owner_id!=p.user_id and p.role!='admin':raise HTTPException(403,'请由发起人或管理员核对处置建议')
    return row

def _lock_evidence_assessment(db,p,row,request_id):
    # current()/prepare() lock package/context rows. Preserve the creator's
    # request -> Instance -> package order on each side of the approval commit.
    from .evidence_assessment_api import lock_request
    lock_request(db,p,request_id)
    scoped(db,DomainInstance,row.instance_id,p,True)

@router.post('/preview')
def preview(body:Preview,p:P,db:DB):
    request,cap=prepare(db,p,body)
    return {'preview_hash':request['preview_hash'],'cloud_input':request['cloud_input'],'provider':cap.data['execution']['provider'],
        'model':cap.data['execution']['model'],'max_output_tokens':cap.data['execution'].get('max_tokens',2048),
        'raw_media_sent':False,'notice':'只发送下列文字；过滤不能识别所有敏感业务内容，请核对后授权。候选建议不会自动执行。'}

@router.post('',status_code=201)
def start(body:Start,p:P,db:DB):
    require(p,'admin','operator');creation_hash=digest(body.model_dump())
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'advice_workspace':p.workspace_id,'request':body.request_id})[:16],16)-(1<<63)})
    prior=db.scalar(select(SignalAdvice).where(SignalAdvice.workspace_id==p.workspace_id,SignalAdvice.request_id==body.request_id))
    if prior:
        if prior.data['creation_hash']!=creation_hash:raise HTTPException(409,'不能更换原处置请求的内容或授权')
        return view(checked(prior))
    request,cap=prepare(db,p,body)
    if request['preview_hash']!=body.preview_hash:raise HTTPException(409,'上下文或模型已变，请重新核对外发预览')
    if body.parent_id:
        parent=editable(db,p,body.parent_id)
        if parent.signal_id!=body.signal_id:raise HTTPException(409,'不能另建其它线索的重试')
        if parent.data.get('inflight') and not body.acknowledge_possible_cost:raise HTTPException(409,'原请求可能已经计费，请明确确认另建费用')
    row=SignalAdvice(workspace_id=p.workspace_id,instance_id=body.instance_id,signal_id=body.signal_id,owner_id=p.user_id,request_id=body.request_id,status='queued',data={
        'request':request,'request_hash':digest(request),'creation_hash':creation_hash,'parent_id':body.parent_id,
        'authorized_by':p.user_id,'authorized_at':now().isoformat(),'progress':'外发范围与授权已保存，等待生成处置建议'})
    db.add(row);db.flush();job=Job(workspace_id=p.workspace_id,kind='advise_signal',idempotency_key='signal-advice:'+row.id,data={'advice_id':row.id,'actor_id':p.user_id})
    db.add(job);db.flush();row.data={**row.data,'job_id':job.id};audit(db,p,'business.signal_advice_requested',row.id,{'preview_hash':body.preview_hash,'model':cap.id});db.commit();return view(row)

@router.get('')
def rows(instance_id:str,signal_id:str,p:P,db:DB):
    signal=scoped(db,PerceptionSignal,signal_id,p)
    if signal.instance_id!=instance_id:raise HTTPException(409,'线索不属于当前实例')
    return [view(checked(r)) for r in db.scalars(select(SignalAdvice).where(SignalAdvice.workspace_id==p.workspace_id,SignalAdvice.signal_id==signal_id).order_by(SignalAdvice.created_at.desc()).limit(30))]
@router.get('/{identity}')
def get(identity:str,p:P,db:DB):return view(checked(scoped(db,SignalAdvice,identity,p)))
@router.get('/{identity}/export')
def export(identity:str,p:P,db:DB):return view(checked(scoped(db,SignalAdvice,identity,p)))

@router.post('/{identity}/revalidate')
def revalidate(identity:str,body:Revalidate,p:P,db:DB):
    row=editable(db,p,identity);previous=row.data.get('local_revalidation')
    if previous and previous['request_id']==body.request_id:
        if previous['body_hash']!=digest(body.model_dump(exclude={'version'})):raise HTTPException(409,'不能更换原本地核对请求')
        return view(row)
    check_version(row,body.version)
    if row.status!='failed' or not row.data.get('raw_response'):raise HTTPException(409,'只能本地核对已保存响应的失败建议，不能创建新云调用')
    job=scoped(db,Job,row.data['job_id'],p)
    if job.status in {'queued','running'}:raise HTTPException(409,'原后台任务尚未结束，请保留原请求等待')
    current(db,p,row)
    from .signal_advice import normalize,ModelBlocked
    from .advice_basis import VALIDATION_VERSION
    record={'request_id':body.request_id,'body_hash':digest(body.model_dump(exclude={'version'})),
        'by':p.user_id,'at':now().isoformat(),'validator_version':VALIDATION_VERSION,
        'raw_response_hash':row.data['raw_response']['hash'],'previous_error':row.data.get('error'),
        'cloud_calls':0,'device_actions':0}
    try:proposal=normalize(row.data['raw_response']['value'],row.data['request'])
    except ModelBlocked as error:
        record.update(status='failed',error=str(error));row.version+=1
        row.data={**row.data,'local_revalidation':signed(record),'error':str(error),
            'progress':'原响应本地核对仍未通过，原文与费用回执保留；没有新云调用'}
    else:
        record.update(status='passed',error=None);row.status='needs_confirmation';row.version+=1
        row.data={**row.data,'local_revalidation':signed(record),'proposal':signed(proposal),'draft':signed(proposal['action']),
            'error':None,'inflight':False,'progress':'原响应已通过本地引用核对，仍需人工核对建议与具体动作；没有新云调用或派发'}
    audit(db,p,'business.signal_advice_locally_revalidated',row.id,{'status':record['status'],'cloud_calls':0});db.commit();return view(row)

@router.put('/{identity}/draft')
def draft(identity:str,body:Draft,p:P,db:DB):
    row=editable(db,p,identity);check_version(row,body.version);current(db,p,row)
    if row.status!='needs_confirmation':raise HTTPException(409,'当前建议不接受修改，请保留原记录')
    action=body.action.model_dump();validate_action(action,row.data['request'])
    row.version+=1;row.data={**row.data,'draft':signed(action),'draft_by':p.user_id,'draft_at':now().isoformat()}
    audit(db,p,'business.signal_advice_draft_saved',row.id,{});db.commit();return view(row)

@router.post('/{identity}/confirm')
def confirm(identity:str,body:Confirm,p:P,db:DB):
    row=editable(db,p,identity);previous=row.data.get('confirmation')
    if previous:
        if previous['request_id']!=body.request_id or previous['action_hash']!=digest(body.model_dump(exclude={'version'} if body.acknowledge_new_local_processing else {'version','acknowledge_new_local_processing'})):raise HTTPException(409,'不能更换原确认请求，请恢复原请求')
        if row.data.get('dispatch'):return view(row)
    else:
        check_version(row,body.version)
        if row.data.get('draft',{}).get('route')=='evidence_check':
            _lock_evidence_assessment(db,p,row,body.request_id)
        current(db,p,row)
        if row.status!='needs_confirmation':raise HTTPException(409,'请先核对已保存建议')
        action={k:v for k,v in row.data['draft'].items() if k!='hash'};validate_action(action,row.data['request'])
        if action['route']=='needs_clarification':raise HTTPException(409,'请先澄清并保存一个受支持动作，再确认')
        workflow_request=None
        if action['route']=='business_workflow':
            if not body.acknowledge_new_local_processing:raise HTTPException(409,'请明确确认新流程会运行本地能力，不是只打开原结果')
            from .advice_workflows import choice
            from .workspace_api import NewTask
            _,selection=choice(action,row.data['request'])
            workflow_request=NewTask(release_id=row.data['request']['release_id'],business_id=action['business_id'],entity_ids=[row.data['request']['entity_id']],title=action['purpose'][:300],request_id=body.request_id,advice_id=row.id,**selection['inputs']).model_dump()
        row.status='applying';row.version+=1;row.data={**row.data,'approved':signed(action),**({'workflow_request':workflow_request,'workflow_request_hash':digest(workflow_request)} if workflow_request else {}),
            'confirmation':{'request_id':body.request_id,'action_hash':digest(body.model_dump(exclude={'version'} if body.acknowledge_new_local_processing else {'version','acknowledge_new_local_processing'})),'by':p.user_id,'at':now().isoformat()}}
        db.commit()
    request=row.data['request'];action=row.data['approved'];dispatch={'route':action['route'],'entity_state_updated':False,'device_action_executed':False}
    if action['route']=='evidence_check':
        _lock_evidence_assessment(db,p,row,body.request_id)
    # Recheck after the durable approval checkpoint. Only a committed manual Task
    # may have changed source context legitimately; create() verifies its exact body.
    committed_task=None
    if action['route'] in {'manual_business','business_workflow'}:
        from .models import Task
        committed_task=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.data['instance_id'].as_string()==row.instance_id,
            Task.data['creation_request_id'].as_string()==body.request_id))
    if not committed_task:current(db,p,row)
    if action['route']=='evidence_check':
        from .evidence_assessment_api import Request,create_saved
        assessment=create_saved(Request(instance_id=row.instance_id,entity_ids=[request['entity_id']],request_id=body.request_id,**action['criteria']),p,db)
        dispatch['assessment_id']=assessment.id
    elif action['route']=='manual_business':
        from .workspace_api import NewTask,create
        task=create(row.instance_id,NewTask(release_id=request['release_id'],business_id=action['business_id'],entity_ids=[request['entity_id']],
            title=action['purpose'][:300],request_id=body.request_id,signal_id=row.signal_id,signal_version=request['signal_version'],signal_basis_hash=request['basis_hash']),p,db)
        dispatch['task_id']=task['id']
    elif action['route']=='business_workflow':
        from .workspace_api import NewTask,create
        task=create(row.instance_id,NewTask.model_validate(row.data['workflow_request']),p,db)
        dispatch['task_id']=task['id']
        dispatch['new_local_processing']=True
    elif action['route']=='original_task':
        from .workspace_api import NativeSignalLink
        from .native_signal_reviews import link,validate_link
        signal=scoped(db,PerceptionSignal,row.signal_id,p,True);original=request['original_task']
        if original['already_linked']:
            validate_link(db,p,signal)
            if signal.data['source_task_link']['task_id']!=original['task_id']:raise HTTPException(409,'原关联任务已变化，请保留建议核对')
        else:link(db,p,signal,NativeSignalLink(version=request['signal_version'],task_id=original['task_id'],task_version=original['task_version'],basis_hash=request['basis_hash'],request_id=body.request_id))
        dispatch['task_id']=original['task_id']
    row=editable(db,p,identity);row.status='applied';row.version+=1;row.data={**row.data,'dispatch':signed(dispatch),'progress':'确认动作已保存并交接，请打开核查或任务查看实际结果'}
    audit(db,p,'business.signal_advice_dispatched',row.id,dispatch);db.commit();return view(row)

@router.post('/{identity}/cancel')
def cancel(identity:str,body:Version,p:P,db:DB):
    row=editable(db,p,identity)
    if row.status=='cancelled':return view(row)
    check_version(row,body.version)
    if row.status in {'applying','applied'}:raise HTTPException(409,'动作已确认，不能取消原交接；请到具体任务处理')
    row.status='cancelled';row.version+=1;row.data={**row.data,'progress':'已停止等待，原预览和已付费响应保留；不撤销已发生的模型费用'}
    audit(db,p,'business.signal_advice_cancelled',row.id,{});db.commit();return view(row)
