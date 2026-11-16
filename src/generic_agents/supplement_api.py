"""Persist suggested observations, explicit approval, source receipts and remaining gaps."""
import copy
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import Field
from sqlalchemy import select,text
from .contracts import Contract,digest
from .setup_api import P,DB
from .models import SupplementPlan,EvidenceAssessment,StreamSession,now
from .services import scoped,require,check_version,audit
from .supplement import checked,original,propose,validate_action,stop_sessions,stop_followup,READ_CONFIGURATION
from .evidence_assessment import checked as checked_assessment
router=APIRouter(prefix='/api/supplement-plans')
class New(Contract):
 assessment_id:str
 request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
class Approval(Contract):
 version:int=Field(ge=1,strict=True)
 action_keys:list[str]=Field(min_length=1,max_length=3)
 seconds:int=Field(ge=4,le=12,strict=True)
 confirm_one_time_read:Literal[True]
 request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
class Action(Contract):version:int=Field(ge=1,strict=True)

def view(row,db=None,p=None):
 if db is not None:
  from .frame_supplement_v236 import is_family,plan_view
  if is_family(db,row):return plan_view(db,p,row)
 return {'id':row.id,'version':row.version,'instance_id':row.instance_id,'assessment_id':row.assessment_id,'status':row.status,'created_at':row.created_at.isoformat(),**row.data}

@router.post('',status_code=201)
def create(body:New,p:P,db:DB):
 require(p,'admin','operator');fingerprint=digest(body.model_dump());db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'workspace':p.workspace_id,'request':body.request_id})[:16],16)-(1<<63)})
 prior=db.scalar(select(SupplementPlan).where(SupplementPlan.workspace_id==p.workspace_id,SupplementPlan.request_id==body.request_id))
 if prior:
  from .frame_supplement_v236 import is_family
  if is_family(db,prior):raise HTTPException(409,'原请求属于任务固定画面补充，请通过原任务恢复')
  if prior.data['creation_hash']!=fingerprint:raise HTTPException(409,'同一请求不能更换原核查')
  return view(checked(prior))
 assessment=checked_assessment(scoped(db,EvidenceAssessment,body.assessment_id,p))
 if any(key in assessment.data.get('request',{}) for key in ('daily_task_frame_v233','supplement_frame_v236')):raise HTTPException(409,'固定任务画面请通过原任务补充入口，不能变成对象全局补充')
 proposal=propose(db,p,assessment)
 row=SupplementPlan(workspace_id=p.workspace_id,instance_id=assessment.instance_id,assessment_id=assessment.id,owner_id=p.user_id,request_id=body.request_id,data={
  'creation_hash':fingerprint,'assessment_result_hash':assessment.data['result_hash'],'proposal':proposal,'proposal_hash':digest(proposal),'progress':'补充需求已保存，请核对来源及执行范围'})
 db.add(row);db.flush();original(db,p,row,check_old_refs=True);audit(db,p,'business.supplement_proposed',row.id,{'actions':len(proposal['actions'])});db.commit();return view(row)

@router.get('')
def list_plans(instance_id:str,p:P,db:DB):
 from .models import DomainInstance
 scoped(db,DomainInstance,instance_id,p)
 return [view(checked(r),db,p) for r in db.scalars(select(SupplementPlan).where(SupplementPlan.workspace_id==p.workspace_id,SupplementPlan.instance_id==instance_id).order_by(SupplementPlan.created_at.desc()).limit(50))]

@router.get('/{identity}')
def get(identity:str,p:P,db:DB):return view(checked(scoped(db,SupplementPlan,identity,p)),db,p)

def approve_saved(identity,body,p,db):
 require(p,'admin');from .media_monitor import node_lock,queue_capture
 node_lock(db);row=checked(scoped(db,SupplementPlan,identity,p,True));fingerprint=digest(body.model_dump(exclude={'version'}))
 from .frame_supplement_v236 import is_family
 if is_family(db,row):raise HTTPException(409,'请通过原任务专用补充入口确认本次读取')
 if row.data.get('approval_request_id'):
  if row.data['approval_request_id']!=body.request_id or row.data['approval_action_hash']!=fingerprint:raise HTTPException(409,'原补充方案已经确认，请恢复原请求，不能更换动作')
  return row
 check_version(row,body.version)
 if row.status!='proposed':raise HTTPException(409,'当前方案不能确认，请保留记录另建')
 original(db,p,row,check_old_refs=True)
 if len(set(body.action_keys))!=len(body.action_keys):raise HTTPException(422,'不能重复选择同一读取动作')
 actions=[];resolved=[];identities=set()
 for key in body.action_keys:
  action=next((a for a in row.data['proposal']['actions'] if a['key']==key),None)
  if not action:raise HTTPException(409,'动作不在保存的补充方案内')
  channel,gateway,installation=validate_action(db,p,action);source_key=(gateway.data['rtsp_origin'],channel.path)
  if source_key in identities:raise HTTPException(409,'本次不能对同一来源重复读取')
  identities.add(source_key)
  for active in db.scalars(select(StreamSession).where(StreamSession.status.in_(['queued','connecting','capturing']))):
   from .models import MediaChannel,MediaGateway
   other=db.get(MediaChannel,active.channel_id);node=db.get(MediaGateway,other.gateway_id)
   if (node.data['rtsp_origin'],other.path)==source_key:raise HTTPException(409,'此来源正在读取，请等原请求结束，不能把其他请求当作本次补充')
  actions.append(copy.deepcopy(action));resolved.append((channel,gateway,installation))
 approved={'actions':actions,'seconds':body.seconds,'read_configuration':READ_CONFIGURATION,'once':True}
 if row.data.get('automatic_watch_id'):approved.update(automatic_watch_id=row.data['automatic_watch_id'],automatic_authorization_hash=row.data['automatic_authorization_hash'])
 sessions=[]
 for channel,gateway,installation in resolved:
  session=queue_capture(db,p,channel,gateway,installation,body.seconds,READ_CONFIGURATION,
    read_mode='automatic' if row.data.get('automatic_watch_id') else 'manual')
  session.data={**session.data,'supplement_plan_id':row.id};sessions.append({'id':session.id,'channel_id':channel.id,'job_id':session.data['job_id']})
 row.status='reading';row.version+=1;row.data={**row.data,'approved':approved,'approved_hash':digest(approved),'approved_by':p.user_id,'approved_at':now().isoformat(),
  'approval_request_id':body.request_id,'approval_action_hash':fingerprint,'sessions':sessions,'sessions_hash':digest(sessions),'progress':'一次读取与授权已保存，后台将保存原件并在本机处理'}
 audit(db,p,'business.supplement_approved',row.id,{'reads':len(sessions),'seconds':body.seconds});return row

@router.post('/{identity}/approve')
def approve(identity:str,body:Approval,p:P,db:DB):
 row=approve_saved(identity,body,p,db);db.commit();return view(row)

@router.post('/{identity}/cancel')
def cancel(identity:str,body:Action,p:P,db:DB):
 require(p,'admin','operator');row=checked(scoped(db,SupplementPlan,identity,p,True))
 from .frame_supplement_v236 import is_family
 if is_family(db,row):raise HTTPException(409,'请通过原任务专用补充入口停止并恢复原请求')
 if row.owner_id!=p.user_id and p.role!='admin':raise HTTPException(403,'请由发起人或管理员停止方案')
 if body.version>row.version:raise HTTPException(409,'版本无效，请重新读取')
 if row.status=='cancelled':return view(row)
 if row.status=='completed':raise HTTPException(409,'补充核查已结束，原记录保留')
 stop_sessions(db,p,row,'用户停止补充观察')
 stop_followup(db,p,row,'原补充方案已停止，条件与原件保留')
 row.status='cancelled';row.version+=1;row.data={**row.data,'progress':'已停止补充，已有原件与核查保留'};audit(db,p,'business.supplement_cancelled',row.id,{});db.commit();return view(row)

@router.get('/{identity}/export')
def export(identity:str,p:P,db:DB):
 row=checked(scoped(db,SupplementPlan,identity,p))
 from .frame_supplement_v236 import is_family
 return {'schema':'frame-supplement-export-v236' if is_family(db,row) else 'local-supplement-plan-v1','plan':view(row,db,p),'cloud_calls':0}
