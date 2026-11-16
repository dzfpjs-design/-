"""Human confirmation of a finite future-read scope, never a control authorization."""
import copy
from datetime import timedelta
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import Field
from sqlalchemy import select,func
from .contracts import Contract,digest
from .setup_api import P,DB
from .services import scoped,require,check_version,audit
from .models import SupplementWatch,SupplementPlan,DomainInstance,EvidenceAssessment,MediaChannel,MediaGateway
from .supplement import checked as checked_plan
from .supplement_watch import permissions,clock,checked,halt
from .media_monitor import node_lock
router=APIRouter(prefix='/api/supplement-watches')
class Scope(Contract):
 plan_id:str
 action_key:str
class Confirm(Scope):
 request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
 seconds:int=Field(strict=True,ge=4,le=12)
 max_reads:int=Field(strict=True,ge=1,le=10)
 minutes:int=Field(strict=True,ge=1,le=1440)
 cooldown_minutes:int=Field(strict=True,ge=1,le=1440)
 confirm_automatic_read:Literal[True]
 check_business_triggers:bool=False
class Stop(Contract):version:int=Field(strict=True,ge=1)

def view(row):return {'id':row.id,'instance_id':row.instance_id,'template_plan_id':row.template_plan_id,'version':row.version,'status':row.status,'created_at':row.created_at.isoformat(),**row.data}

def scope(body,p,db):
 plan=checked_plan(scoped(db,SupplementPlan,body.plan_id,p))
 action=next((a for a in plan.data['proposal']['actions'] if a['key']==body.action_key),None)
 if not action:raise HTTPException(409,'来源不在原保存的补充需求内')
 return plan,action,permissions(db,p,plan,action)

@router.post('/check')
def check(body:Scope,p:P,db:DB):
 require(p,'admin','operator');plan,action,perms=scope(body,p,db)
 from .assessment_triggers import configured
 try:triggers=configured(db,p,plan,perms);trigger_issue=None
 except HTTPException as error:triggers=None;trigger_issue=error.detail
 return {'business_check_triggers':triggers,'business_trigger_issue':trigger_issue,'plan_id':plan.id,'action_key':action['key'],'permissions':perms,'criteria':scoped(db,EvidenceAssessment,plan.assessment_id,p).data['request'],'ready':True,'reads_queued':0}

@router.post('',status_code=201)
def create(body:Confirm,p:P,db:DB):
 require(p,'admin');node_lock(db);fingerprint=digest(body.model_dump(exclude={'check_business_triggers'} if not body.check_business_triggers else set()))
 prior=db.scalar(select(SupplementWatch).where(SupplementWatch.workspace_id==p.workspace_id,SupplementWatch.request_id==body.request_id))
 if prior:
  if prior.data['creation_hash']!=fingerprint:raise HTTPException(409,'同一授权请求不能改变范围或预算，请恢复原确认')
  return view(checked(prior))
 if db.scalar(select(func.count()).select_from(SupplementWatch).where(SupplementWatch.status=='active'))>=20:raise HTTPException(409,'当前运行节点自动读取授权已达20份，请先停止不用的授权')
 plan,action,perms=scope(body,p,db)
 if body.seconds>perms['max_read_seconds']:raise HTTPException(409,'单次时长超过已确认的设备边界')
 from .models import EvidenceAssessment
 assessment=scoped(db,EvidenceAssessment,plan.assessment_id,p);req=assessment.data['request']
 channel=scoped(db,MediaChannel,action['resource']['channel_id'],p)
 gateway=scoped(db,MediaGateway,channel.gateway_id,p)
 physical=[gateway.data['rtsp_origin'],channel.path]
 for old in db.scalars(select(SupplementWatch).where(SupplementWatch.status=='active')):
  if checked(old).data['authorization'].get('physical_source')==physical:raise HTTPException(409,'此来源已有自动补充授权，请先停止原授权；不能通过另一通道扩大次数')
 time=clock(db);criteria={k:copy.deepcopy(req[k]) for k in ['entity_ids','purpose','goal','required_modalities','lookback_hours']}
 auth={'action':copy.deepcopy(action),'permissions':perms,'physical_source':physical,'criteria':criteria,'seconds':body.seconds,
  'max_reads':min(body.max_reads,perms['daily_limit']) if perms['daily_limit'] else body.max_reads,'cooldown_minutes':max(body.cooldown_minutes,perms['cooldown_minutes']),
  'deadline':(time+timedelta(minutes=body.minutes)).isoformat(),'max_checks':20,'control_authorized':False,'raw_media_cloud_allowed':False}
 if body.check_business_triggers:
  from .assessment_triggers import configured
  auth['business_check_triggers']=configured(db,p,plan,perms)
 row=SupplementWatch(workspace_id=p.workspace_id,instance_id=plan.instance_id,template_plan_id=plan.id,owner_id=p.user_id,request_id=body.request_id,created_at=time,
  data={'creation_hash':fingerprint,'authorization':auth,'authorization_hash':digest(auth),'history':[],'history_hash':digest([]),'progress':'有限授权已保存，等待系统首次本地核查；尚未读取设备' if body.check_business_triggers else '已保存有限授权，等待以后相同条件的新核查；没有读取'})
 db.add(row);db.flush();audit(db,p,'business.automatic_read_confirmed',row.id,{'reads':auth['max_reads'],'seconds':auth['seconds'],'deadline':auth['deadline']});db.commit();return view(row)

@router.post('/stop-confirmation')
def stop_confirmation(body:Confirm,p:P,db:DB):
 require(p,'admin');node_lock(db);fingerprint=digest(body.model_dump(exclude={'check_business_triggers'} if not body.check_business_triggers else set()))
 row=db.scalar(select(SupplementWatch).where(SupplementWatch.workspace_id==p.workspace_id,SupplementWatch.request_id==body.request_id).with_for_update())
 if row:
  checked(row)
  if row.data['creation_hash']!=fingerprint:raise HTTPException(409,'请求与原确认不一致，不能停止另一份授权')
  if row.status=='active':halt(db,p,row,'stopped','管理员停止了原确认请求；自己的未完成读取已停止，原件保留')
 else:
  plan=scoped(db,SupplementPlan,body.plan_id,p)
  row=SupplementWatch(workspace_id=p.workspace_id,instance_id=plan.instance_id,template_plan_id=plan.id,owner_id=p.user_id,request_id=body.request_id,status='stopped',created_at=clock(db),
   data={'creation_hash':fingerprint,'authorization':None,'authorization_hash':digest(None),'history':[],'history_hash':digest([]),'progress':'原确认请求已停止；迟到或重试的同一请求不会启用自动读取'})
  db.add(row);db.flush();audit(db,p,'business.automatic_read_confirmation_stopped',row.id,{})
 db.commit();return view(row)

@router.get('')
def list_rows(instance_id:str,p:P,db:DB):
 scoped(db,DomainInstance,instance_id,p)
 return [view(checked(row)) for row in db.scalars(select(SupplementWatch).where(SupplementWatch.workspace_id==p.workspace_id,SupplementWatch.instance_id==instance_id).order_by(SupplementWatch.created_at.desc()).limit(50))]

@router.get('/{identity}')
def get(identity:str,p:P,db:DB):return view(checked(scoped(db,SupplementWatch,identity,p)))

@router.post('/{identity}/stop')
def stop(identity:str,body:Stop,p:P,db:DB):
 require(p,'admin');node_lock(db);row=checked(scoped(db,SupplementWatch,identity,p,True))
 if body.version>row.version:raise HTTPException(409,'版本无效，请读取保存记录')
 if row.status!='active':return view(row)
 halt(db,p,row,'stopped','管理员停止了自动补充授权；自己的未完成读取已停止，原件与结果保留');db.commit();return view(row)

@router.get('/{identity}/export')
def export(identity:str,p:P,db:DB):return {'schema':'local-automatic-read-v1','authorization':view(checked(scoped(db,SupplementWatch,identity,p))),'cloud_calls':0}
