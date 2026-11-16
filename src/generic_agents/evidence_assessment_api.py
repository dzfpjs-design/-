"""Persist a user's explicit local assessment before bounded background checks."""
import copy
from datetime import timedelta,timezone
from typing import Literal
from pydantic import Field
from fastapi import APIRouter,HTTPException
from sqlalchemy import select,text
from .contracts import Contract,digest
from .setup_api import P,DB
from .models import EvidenceAssessment,Job,now
from .services import scoped,require,check_version,audit
from .evidence_assessment import checked,checked_request,scoped_objects,ref,freshness,MODALITIES
router=APIRouter(prefix='/api/evidence-assessments')
class Request(Contract):
 release_id:str|None=None
 instance_id:str
 entity_ids:list[str]=Field(min_length=1,max_length=20)
 purpose:str=Field(min_length=1,max_length=1000)
 goal:Literal['current_state','available_evidence']='current_state'
 required_modalities:list[Literal['imagery','audio','video','archive','observations']]=Field(min_length=1,max_length=5)
 lookback_hours:int=Field(ge=1,le=8760,strict=True)
 request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
 parent_id:str|None=None
 question_id:str|None=None
class Action(Contract):version:int=Field(ge=1,strict=True)

def view(row):return {'id':row.id,'version':row.version,'status':row.status,'created_at':row.created_at.astimezone(timezone.utc).isoformat(),**row.data}

def sealed(db,p,row):
 from .daily_frame_assessment_api_v233 import guard_record
 from .frame_supplement_source_v236 import checked_result as supplement_checked
 return supplement_checked(db,p,guard_record(db,p,checked(row)))

def safe_view(row,db=None,p=None):
 checked_request(row)
 try:
  if db is not None:return view(sealed(db,p,row))
  if any(key in row.data['request'] for key in ('daily_task_frame_v233','supplement_frame_v236')):raise HTTPException(409,'任务画面核查需要完整保存证明')
  return view(checked(row))
 except HTTPException:
  value=view(row);value.pop('result',None);value.pop('result_hash',None);value.update(status='blocked_integrity',error='保存结果不一致，原条件保留；请另建核查，原异常结果不会被覆盖。')
  return value

def lock_request(db,p,request_id):
 """Serialize this exact assessment nonce before current source locks."""
 db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'workspace':p.workspace_id,'request':request_id})[:16],16)-(1<<63)})

def create_saved(body,p,db,*,outcome=None):
 if outcome is not None:
  if type(outcome) is not dict:raise HTTPException(422,'核查创建结果容器无效')
  outcome['created']=False
 require(p,'admin','operator')
 if not body.purpose.strip() or len(set(body.required_modalities))!=len(body.required_modalities):raise HTTPException(422,'请填写核查用途，资料类型不能重复')
 excluded=({'question_id'} if body.question_id is None else set())|({'release_id'} if body.release_id is None else set())
 content=body.model_dump(exclude=excluded);request_hash=digest(content)
 lock_request(db,p,body.request_id)
 prior=db.scalar(select(EvidenceAssessment).where(EvidenceAssessment.workspace_id==p.workspace_id,EvidenceAssessment.request_id==body.request_id))
 if prior:
  if prior.data['creation_hash']!=request_hash:raise HTTPException(409,'同一核查请求不能更换条件，请保留原请求另建核查')
  return checked(prior)
 # Match coordinators before locking package/context rows. The later INSERT
 # needs this Instance's FK KEY SHARE; acquiring package first can deadlock
 # with a different-nonce coordinator that already owns Instance FOR UPDATE.
 from .models import DomainInstance
 scoped(db,DomainInstance,body.instance_id,p,True)
 instance,release,objects=scoped_objects(db,p,body.instance_id,body.entity_ids)
 from .instance_domain import require_destination_version
 require_destination_version(release,objects,body.release_id)
 if body.parent_id:
  parent=checked_request(scoped(db,EvidenceAssessment,body.parent_id,p))
  if parent.instance_id!=body.instance_id:raise HTTPException(409,'不能在其他实例下重查此结果')
 catalog=release.data['snapshot']['object_types'];names=[]
 for r in objects:
  spec=next(c for c in catalog if c['key']==r.entity_type)
  name=next((r.data[f['key']] for f in spec['fields'] if f['kind']=='文本' and r.data.get(f['key'])),r.external_id)
  names.append({'id':r.id,'name':str(name),'type_name':spec['name'],'version':r.version})
 question_ref=None
 if body.question_id:
  from .evidence_question_api import reference
  question_ref=reference(db,p,body)
 end=now();request={**content,'release_id':release.id,'object_refs':[ref(r) for r in objects],'objects':names,'window_end':end.isoformat(),'window_start':(end-timedelta(hours=body.lookback_hours)).isoformat()}
 if {'archive','observations'}&set(body.required_modalities):request['source_catalog_version']=2
 row=EvidenceAssessment(workspace_id=p.workspace_id,instance_id=instance.id,owner_id=p.user_id,request_id=body.request_id,status='queued',data={'creation_hash':request_hash,'request':request,'request_hash':digest(request),**({'question_reference':question_ref,'question_reference_hash':digest(question_ref)} if question_ref else {}),'progress':'核查条件已保存，等待后台核对'})
 db.add(row);db.flush();job=Job(workspace_id=p.workspace_id,kind='assess_evidence',idempotency_key='assessment:'+row.id,data={'assessment_id':row.id,'actor_id':p.user_id});db.add(job);db.flush();row.data={**row.data,'job_id':job.id}
 audit(db,p,'data.evidence_assessment_requested',row.id,{'objects':len(objects),'modalities':body.required_modalities})
 if outcome is not None:outcome['created']=True
 return row

@router.post('',status_code=201)
def create(body:Request,p:P,db:DB):
 row=create_saved(body,p,db);db.commit();return view(row)

@router.get('')
def rows(instance_id:str,p:P,db:DB):
 from .models import DomainInstance
 scoped(db,DomainInstance,instance_id,p)
 return [safe_view(r,db,p) for r in db.scalars(select(EvidenceAssessment).where(EvidenceAssessment.workspace_id==p.workspace_id,EvidenceAssessment.instance_id==instance_id).order_by(EvidenceAssessment.created_at.desc()).limit(50))]

@router.get('/{identity}')
def get(identity:str,p:P,db:DB):return view(sealed(db,p,scoped(db,EvidenceAssessment,identity,p)))

@router.get('/{identity}/freshness')
def current(identity:str,p:P,db:DB):return freshness(db,p,checked(scoped(db,EvidenceAssessment,identity,p)))

@router.get('/{identity}/export')
def export(identity:str,p:P,db:DB):return {'schema':'local-evidence-assessment-v1','assessment':view(sealed(db,p,scoped(db,EvidenceAssessment,identity,p))),'validation':freshness(db,p,scoped(db,EvidenceAssessment,identity,p)),'cloud_calls':0}

@router.post('/{identity}/cancel')
def cancel(identity:str,body:Action,p:P,db:DB):
 require(p,'admin','operator');row=scoped(db,EvidenceAssessment,identity,p,True)
 from .daily_frame_assessment_api_v233 import is_frame_record,guard_record
 if is_frame_record(db,row):
  from .relations import lock_current_actor
  lock_current_actor(db,p);guard_record(db,p,row)
 from .frame_supplement_source_v236 import is_record as supplement_record,checked_result as supplement_checked
 if supplement_record(db,row):
  from .relations import lock_current_actor
  lock_current_actor(db,p);supplement_checked(db,p,row)
 if row.owner_id!=p.user_id and p.role!='admin':raise HTTPException(403,'请由发起人或管理员停止此核查')
 check_version(row,body.version)
 if row.status not in {'queued','running'}:raise HTTPException(409,'核查已经结束，原结果继续保留')
 row.status='cancelled';row.version+=1;row.data={**row.data,'progress':'已停止核查，原请求保留'};audit(db,p,'data.evidence_assessment_cancelled',row.id,{});db.commit();return view(row)


def task_snapshot(db,p,identity,instance_id,entity_ids):
 row=checked(scoped(db,EvidenceAssessment,identity,p))
 if row.status!='completed' or row.instance_id!=instance_id or set(row.data['request']['entity_ids'])!=set(entity_ids):raise HTTPException(409,'核查尚未完成或对象范围不一致，请保留原请求并重新核对')
 if not freshness(db,p,row)['metadata_versions_unchanged']:raise HTTPException(409,'核查依据版本改变，请另建核查后交给业务')
 return {'assessment_id':row.id,'result_hash':row.data['result_hash'],'request':copy.deepcopy(row.data['request']),'result':copy.deepcopy(row.data['result']),**({'question_reference':copy.deepcopy(row.data['question_reference'])} if row.data.get('question_reference') else {}),'historical_not_current_state':True}
