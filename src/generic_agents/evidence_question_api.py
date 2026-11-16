"""Exact cloud preview, durable paid response and human-approved local assessment."""
import copy
from datetime import timezone
from typing import Literal
from pydantic import Field
from fastapi import APIRouter,HTTPException
from sqlalchemy import select,text
from .contracts import Contract,digest
from .setup_api import P,DB
from .models import EvidenceQuestion,EvidenceAssessment,Capability,Job,now
from .services import scoped,require,check_version,audit
from .evidence_question import prepare,model,checked,current,Criteria,signed
router=APIRouter(prefix='/api/evidence-questions')
class Preview(Contract):
 instance_id:str
 entity_ids:list[str]=Field(min_length=1,max_length=20)
 question:str=Field(min_length=1,max_length=3000)
 model_profile_id:str
class Start(Preview):
 preview_hash:str
 authorize_cloud_text:Literal[True]
 request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
 parent_id:str|None=None
 acknowledge_possible_cost:bool=False
class Draft(Contract):
 version:int=Field(ge=1,strict=True)
 criteria:Criteria
class Confirm(Contract):
 version:int=Field(ge=1,strict=True)
 request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
 acknowledge_clarifications:bool=False
 acknowledge_unsupported_parts:bool=False
class Action(Contract):version:int=Field(ge=1,strict=True)

def view(row):return {'id':row.id,'version':row.version,'status':row.status,'created_at':row.created_at.astimezone(timezone.utc).isoformat(),**{k:v for k,v in row.data.items() if k!='raw_response'}}
def editable(db,p,identity):
 require(p,'admin','operator');row=checked(scoped(db,EvidenceQuestion,identity,p,True))
 if row.owner_id!=p.user_id and p.role!='admin':raise HTTPException(403,'请由发起人或管理员核对问题')
 return row

@router.get('/models')
def options(p:P,db:DB):
 from .cloud_provider_policy import provider_enabled
 out=[]
 for cap in db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.kind=='foundation_model',Capability.status=='published')):
  try:model(db,p,cap.id)
  except HTTPException:continue
  ex=cap.data['execution']
  if not provider_enabled(ex['provider']):continue
  out.append({'id':cap.id,'name':cap.name,'provider':ex['provider'],'model':ex['model'],'max_output_tokens':ex.get('max_tokens',2048)})
 return out

@router.post('/preview')
def preview(body:Preview,p:P,db:DB):
 if not body.question.strip():raise HTTPException(422,'请先描述要核查的问题')
 request,cap=prepare(db,p,body)
 return {'preview_hash':request['preview_hash'],'cloud_input':request['cloud_input'],'provider':cap.data['execution']['provider'],'model':cap.data['execution']['model'],
  'max_output_tokens':cap.data['execution'].get('max_tokens',2048),'raw_media_sent':False,'transcripts_or_results_sent':False,'notice':'只发送下列文字；基础模式过滤不能保证识别所有个人或业务敏感信息，请核对后授权。'}

@router.post('',status_code=201)
def start(body:Start,p:P,db:DB):
 require(p,'admin','operator');hash_value=digest(body.model_dump());db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'workspace':p.workspace_id,'request':body.request_id})[:16],16)-(1<<63)})
 prior=db.scalar(select(EvidenceQuestion).where(EvidenceQuestion.workspace_id==p.workspace_id,EvidenceQuestion.request_id==body.request_id))
 if prior:
  if prior.data['creation_hash']!=hash_value:raise HTTPException(409,'同一问题请求不能更换外发内容或授权，请保留原请求另建理解')
  return view(checked(prior))
 request,cap=prepare(db,p,body)
 if request['preview_hash']!=body.preview_hash:raise HTTPException(409,'问题、对象或模型已变，请重新核对外发预览')
 if body.parent_id:
  parent=checked(scoped(db,EvidenceQuestion,body.parent_id,p))
  if parent.instance_id!=body.instance_id:raise HTTPException(409,'不能重试其他实例的问题')
  if parent.data.get('inflight') and not body.acknowledge_possible_cost:raise HTTPException(409,'原请求可能已计费，请明确确认重试费用；原响应不会被覆盖')
 row=EvidenceQuestion(workspace_id=p.workspace_id,instance_id=body.instance_id,owner_id=p.user_id,request_id=body.request_id,status='queued',data={'request':request,'request_hash':digest(request),'creation_hash':hash_value,'parent_id':body.parent_id,'authorized_by':p.user_id,'authorized_at':now().isoformat(),'progress':'外发授权与原问题已保存，等待理解'})
 db.add(row);db.flush();job=Job(workspace_id=p.workspace_id,kind='interpret_evidence_question',idempotency_key='evidence-question:'+row.id,data={'question_id':row.id,'actor_id':p.user_id});db.add(job);db.flush();row.data={**row.data,'job_id':job.id};audit(db,p,'business.evidence_question_requested',row.id,{'model':cap.id,'preview_hash':body.preview_hash});db.commit();return view(row)

@router.get('')
def rows(instance_id:str,p:P,db:DB):
 from .models import DomainInstance
 scoped(db,DomainInstance,instance_id,p)
 return [view(checked(r)) for r in db.scalars(select(EvidenceQuestion).where(EvidenceQuestion.workspace_id==p.workspace_id,EvidenceQuestion.instance_id==instance_id).order_by(EvidenceQuestion.created_at.desc()).limit(50))]
@router.get('/{identity}')
def get(identity:str,p:P,db:DB):return view(checked(scoped(db,EvidenceQuestion,identity,p)))

@router.put('/{identity}/draft')
def draft(identity:str,body:Draft,p:P,db:DB):
 row=editable(db,p,identity);check_version(row,body.version);current(db,p,row)
 if row.status not in {'needs_confirmation','needs_clarification','unsupported'}:raise HTTPException(409,'当前问题不接受修改；请保留原记录另建理解')
 value=body.criteria.model_dump()
 if not value['purpose'].strip() or len(set(value['required_modalities']))!=len(value['required_modalities']):raise HTTPException(422,'请说明核查用途且不能重复资料类型')
 row.version+=1;row.data={**row.data,'draft':signed(value),'draft_by':p.user_id,'draft_at':now().isoformat()};audit(db,p,'business.evidence_question_draft_saved',row.id,{});db.commit();return view(row)

@router.post('/{identity}/confirm')
def confirm(identity:str,body:Confirm,p:P,db:DB):
 row=editable(db,p,identity)
 previous=row.data.get('confirmation')
 if previous:
  if previous['request_id']!=body.request_id:raise HTTPException(409,'该问题已确认，请恢复原请求；如需新核查请另建')
  # Lost-response retries must use exactly the previously confirmed acknowledgement.
  if previous['action_hash']!=digest(body.model_dump(exclude={'version'})):raise HTTPException(409,'不能更换原确认内容')
  if row.data.get('assessment_id'):return {'question':view(row),'assessment_id':row.data['assessment_id']}
 else:
  check_version(row,body.version);current(db,p,row)
  if row.status not in {'needs_confirmation','needs_clarification','unsupported'} or not row.data.get('draft'):raise HTTPException(409,'请先保存明确条件，再确认执行')
  proposal=row.data['proposal']
  if proposal['clarifications'] and not body.acknowledge_clarifications:raise HTTPException(409,'原问题尚有澄清，请核对修改并明确确认')
  if (proposal['unsupported_parts'] or proposal['goal']=='unsupported') and not body.acknowledge_unsupported_parts:raise HTTPException(409,'原问题含未支持内容，请明确确认本次只核查已支持部分')
  row.status='applying';row.version+=1;row.data={**row.data,'approved':signed({k:v for k,v in row.data['draft'].items() if k!='hash'}),'confirmation':{'request_id':body.request_id,'action_hash':digest(body.model_dump(exclude={'version'})),'by':p.user_id,'at':now().isoformat()}}
  db.commit()
 from .evidence_assessment_api import create,Request
 criteria={k:v for k,v in row.data['approved'].items() if k!='hash'};request=row.data['request']
 result=create(Request(instance_id=row.instance_id,entity_ids=request['entity_ids'],request_id=body.request_id,question_id=row.id,**criteria),p,db)
 row=checked(scoped(db,EvidenceQuestion,row.id,p,True));row.status='applied';row.version+=1;row.data={**row.data,'assessment_id':result['id'],'progress':'已按人工确认条件创建本地核查'};audit(db,p,'business.evidence_question_applied',row.id,{'assessment_id':result['id']});db.commit();return {'question':view(row),'assessment_id':result['id']}

@router.post('/{identity}/cancel')
def cancel(identity:str,body:Action,p:P,db:DB):
 row=editable(db,p,identity);check_version(row,body.version)
 if row.status in {'applying','applied','cancelled'}:raise HTTPException(409,'问题已确认或停止，请查看保存的本地核查')
 row.status='cancelled';row.version+=1;row.data={**row.data,'progress':'已停止，原问题和已有付费回执保留'};audit(db,p,'business.evidence_question_cancelled',row.id,{});db.commit();return view(row)


def reference(db,p,body):
 row=checked(scoped(db,EvidenceQuestion,body.question_id,p));current(db,p,row)
 if row.status not in {'applying','applied'} or not row.data.get('approved'):raise HTTPException(409,'问题尚未按保存的条件确认，不能执行')
 request=row.data['request'];criteria={k:v for k,v in row.data['approved'].items() if k!='hash'}
 if row.instance_id!=body.instance_id or request['entity_ids']!=body.entity_ids or row.data['confirmation']['request_id']!=body.request_id or criteria!={k:getattr(body,k) for k in criteria}:raise HTTPException(409,'不能更换问题确认的对象或条件')
 return {'question_id':row.id,'original_question':request['question'],'model_profile_id':request['model_profile_id'],'model_receipt':copy.deepcopy(row.data.get('model_receipt')),'proposal':copy.deepcopy(row.data['proposal']),'confirmed_criteria':copy.deepcopy(criteria),'confirmed_at':row.data['confirmation']['at'],'not_an_open_business_answer':True}
