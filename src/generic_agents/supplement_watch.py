"""Finite automatic local reads under generated business AND source read permissions."""
import copy
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select,func,or_
from .contracts import digest
from .models import SupplementWatch,SupplementPlan,EvidenceAssessment,VideoDomainBinding,StreamSession,MediaChannel,MediaGateway,Membership,User,DomainInstance
from .services import scoped,require,audit
from .supplement import checked as checked_plan,original,validate_action,propose,block
from .evidence_assessment import checked as checked_assessment,scoped_objects

CHINA=ZoneInfo('Asia/Shanghai')

def clock(db):return db.scalar(select(func.clock_timestamp()))
def checked(row):
 from .assessment_triggers import checked as checked_triggers
 checked_triggers(row)
 if digest(row.data.get('history',[]))!=row.data.get('history_hash'):raise HTTPException(409,'自动读取次数与历史不一致，请保留记录核对')
 if digest(row.data['authorization'])!=row.data.get('authorization_hash'):raise HTTPException(409,'自动读取授权与保存范围不一致，请保留记录核对')
 return row

def bounded(rule,flag,field,low,high):
 if not rule.get(flag):return None
 value=rule.get(field)
 if type(value) is not int or not low<=value<=high:raise HTTPException(409,'已确认的次数、间隔或时长设置无效')
 return value

def permissions(db,p,plan,action):
 assessment=original(db,p,plan)
 req=assessment.data['request']
 if len(req['entity_ids'])!=1 or req['required_modalities']!=['video']:raise HTTPException(409,'自动读取目前需单个对象且只核查视频；其他范围可使用一次确认方案')
 _,release,_=scoped_objects(db,p,plan.instance_id,req['entity_ids'])
 validate_action(db,p,action)
 binding=scoped(db,VideoDomainBinding,action['resource']['binding_id'],p)
 snap=release.data['snapshot'];business=next(x for x in snap['businesses'] if x['id']==binding.data['business_id'])
 rule=snap['automation']['policies'][business['name']]['rules'].get('observe',{})
 if rule.get('mode')!='自动运行':raise HTTPException(409,'此实例业务尚未允许自动读取资料；请在配置的自动化范围确认后生成并启用相应版本')
 if not any(s.get('execution',{}).get('automation_action')=='observe' and s.get('agent')!='人工确认' for s in business['flow']['steps']):raise HTTPException(409,'业务自动读取的真实流程入口尚未接通')
 source=action['resource']
 resource=next((r for r in snap['resources'] if r.get('category')=='video' and r.get('details',{}).get('capability_id')==source['source_id'] and r.get('details',{}).get('channel_id')==source['channel_id']),None)
 if not resource or resource['id'] not in rule.get('resources',[]) or business['name'] not in resource.get('businesses',[]):raise HTTPException(409,'此视频来源未列入该业务已确认的自动读取范围')
 equipment=snap.get('equipment')
 if not isinstance(equipment,dict):raise HTTPException(409,'此实例版本尚未保存设备读取边界，请在配置中确认后生成新版本')
 device=equipment.get('devices',{}).get(resource['id'],{})
 if device.get('type','camera')!='camera':raise HTTPException(409,'此视频只读适配器不支持当前设备分类的自动执行')
 base=equipment.get('types',{}).get('camera');rules=[base]
 if device.get('custom'):rules.append(device.get('rule'))
 if any(not isinstance(r,dict) or r.get('read') is not True for r in rules):raise HTTPException(409,'设备类型或单台规则未允许读取')
 if any(r.get('readScopeEnabled') for r in rules) or rule.get('scope','').strip():raise HTTPException(409,'附加文字范围尚无可验证的机器边界；请使用确切通道和对象关联，或逐次确认')
 limits=[bounded(rule,'limitsEnabled','limit',1,10000)]+[bounded(r,'limitEnabled','limit',1,10000) for r in rules]
 cooldown=[bounded(rule,'limitsEnabled','cooldown',0,1440)]+[bounded(r,'cooldownEnabled','cooldown',0,1440) for r in rules]+[bounded(r,'refreshOverride','interval',1,1440) for r in rules]
 duration=[bounded(r,'durationEnabled','duration',1,1440) for r in rules]
 return {'release_id':release.id,'business_id':business['id'],'business_name':business['name'],'resource_id':resource['id'],
  'daily_limit':min(x for x in limits if x is not None) if any(x is not None for x in limits) else None,
  'cooldown_minutes':max([x for x in cooldown if x is not None]+[0]),'max_read_seconds':min([x*60 for x in duration if x is not None]+[12]),
  'camera_time_window_required':False,'local_backend':source['backend'],'source_name':source['channel_name'],'control_authorized':False}

def valid(db,p,row):
 checked(row);require(p,'admin')
 plan=checked_plan(scoped(db,SupplementPlan,row.template_plan_id,p));auth=row.data['authorization']
 current=permissions(db,p,plan,auth['action'])
 if auth.get('business_check_triggers'):
  from .assessment_triggers import configured
  if configured(db,p,plan,current)!=auth['business_check_triggers']:raise HTTPException(409,'业务核查触发条件已改变，请重新确认')
 if current!=auth['permissions']:raise HTTPException(409,'业务或设备读取边界已改变，请重新核对授权')
 return plan

def stop_owned(db,p,row,reason):
 from .assessment_triggers import stop_owned as stop_checks
 stop_checks(db,p,row,reason)
 for plan in db.scalars(select(SupplementPlan).where(SupplementPlan.workspace_id==row.workspace_id,SupplementPlan.data['automatic_watch_id'].as_string()==row.id,SupplementPlan.status.in_(['reading','rechecking'])).with_for_update()):block(db,p,plan,reason)

def halt(db,p,row,status,reason):
 stop_owned(db,p,row,reason);row.status=status;row.version+=1;row.data={**row.data,'progress':reason};audit(db,p,'business.automatic_read_stopped',row.id,{'status':status,'reason':reason})

def plan_guard(db,p,plan):
 approved=plan.data.get('approved',{})
 identity=approved.get('automatic_watch_id') or plan.data.get('automatic_watch_id')
 if not identity:return
 if identity!=plan.data.get('automatic_watch_id') or approved.get('automatic_authorization_hash')!=plan.data.get('automatic_authorization_hash'):raise HTTPException(409,'自动补充方案与原授权关联不一致')
 row=checked(scoped(db,SupplementWatch,identity,p))
 if row.data['authorization_hash']!=approved.get('automatic_authorization_hash'):raise HTTPException(409,'自动读取方案的原授权摘要不一致')
 if row.status!='active' or clock(db)>=datetime.fromisoformat(row.data['authorization']['deadline']):raise HTTPException(409,'自动读取授权已停止或到期，已保存资料保留')
 valid(db,p,row)

def history(db,row):
 # Count all reserved reads, including failed ones and stopped/expired authorizations.
 auth=row.data['authorization'];rows=list(db.scalars(select(SupplementWatch).where(SupplementWatch.workspace_id==row.workspace_id,SupplementWatch.instance_id==row.instance_id,SupplementWatch.data['authorization']['permissions']['business_id'].as_string()==auth['permissions']['business_id'])))
 out=[]
 for r in rows:
  checked(r)
  if r.data['authorization']['permissions']['business_id']==auth['permissions']['business_id']:out.extend(r.data.get('history',[]))
 return [h for h in out if h.get('plan_id')]

def _lock_next_assessment(db,p,row):
 # valid()/permissions() lock the package through scoped_objects. If this
 # writer may create an automatic check, take the ordinary creator's exact
 # request -> Instance order before those source locks, not inside create_saved.
 checked(row);require(p,'admin');auth=row.data['authorization']
 if not isinstance(auth,dict) or not auth.get('business_check_triggers'):return
 from .evidence_assessment_api import lock_request
 request_id='auto-check-'+row.id+'-'+str(len(row.data.get('automatic_checks',[]))+1)
 lock_request(db,p,request_id)
 scoped(db,DomainInstance,row.instance_id,p,True)

def advance(db,p,row):
 if row.status!='active':return
 _lock_next_assessment(db,p,row)
 template=valid(db,p,row);auth=row.data['authorization'];time=clock(db)
 if time>=datetime.fromisoformat(auth['deadline']):halt(db,p,row,'expired','授权已到期；自己的未完成读取已停止，原件保留');return
 pending=db.scalar(select(SupplementPlan.id).where(SupplementPlan.workspace_id==row.workspace_id,SupplementPlan.data['automatic_watch_id'].as_string()==row.id,SupplementPlan.status.in_(['reading','rechecking'])))
 if pending:return
 used=len([h for h in row.data['history'] if h.get('plan_id')])
 if used>=auth['max_reads'] or len(row.data['history'])>=20:halt(db,p,row,'exhausted','本次授权次数或20份核查上限已用完；不会继续读取');return
 from .assessment_triggers import advance as trigger_checks
 if trigger_checks(db,p,row,time):return
 req=auth['criteria'];seen=[h['assessment_id'] for h in row.data['history']]
 assessment=db.scalar(select(EvidenceAssessment).where(EvidenceAssessment.workspace_id==row.workspace_id,EvidenceAssessment.instance_id==row.instance_id,
  or_(EvidenceAssessment.created_at>row.created_at,EvidenceAssessment.id.in_([c['assessment_id'] for c in row.data.get('automatic_checks',[])])),EvidenceAssessment.status=='completed',EvidenceAssessment.id.not_in(seen),
  EvidenceAssessment.data['request']['parent_id'].as_string().is_(None),
  EvidenceAssessment.data['request']['purpose'].as_string()==req['purpose'],EvidenceAssessment.data['request']['goal'].as_string()==req['goal'],
  EvidenceAssessment.data['request']['lookback_hours'].as_integer()==req['lookback_hours'],
  EvidenceAssessment.data['request']['entity_ids']==req['entity_ids'],EvidenceAssessment.data['request']['required_modalities']==req['required_modalities']).order_by(EvidenceAssessment.created_at,EvidenceAssessment.id).limit(1))
 if not assessment:row.data={**row.data,'progress':'已确认边界，等待以后相同条件的新核查；现在没有读取'};return
 assessment=checked_assessment(assessment)
 record={'assessment_id':assessment.id,'at':time.isoformat()}
 own_check=next((c for c in row.data.get('automatic_checks',[]) if c['assessment_id']==assessment.id),None)
 if own_check:
  from .assessment_triggers import signature as source_signature
  if own_check['source_signature']!=source_signature(db,p,row):
   record['decision']='对象资料已有变化，本次历史核查不用于补充；按已确认业务触发重新核查'
   row.data={**row.data,'history':row.data['history']+[record],'history_hash':digest(row.data['history']+[record]),'progress':record['decision']};row.version+=1;return
 proposal=propose(db,p,assessment)
 action=next((a for a in proposal['actions'] if a['resource']==auth['action']['resource']),None)
 if not action:
  record['decision']='没有已授权来源可补充的缺视频处理缺口；不读取'
  row.data={**row.data,'history':row.data['history']+[record],'history_hash':digest(row.data['history']+[record]),'progress':record['decision']};row.version+=1;return
 all_reads=history(db,row);day=time.astimezone(CHINA).date().isoformat();daily_limit=auth['permissions']['daily_limit']
 if daily_limit is not None and len([h for h in all_reads if datetime.fromisoformat(h['at']).astimezone(CHINA).date().isoformat()==day])>=daily_limit:
  row.data={**row.data,'progress':'业务每日次数已用完；在原授权期限内等待中国标准时间次日'};return
 same_source=[h for r in db.scalars(select(SupplementWatch).where(SupplementWatch.data['authorization']['physical_source']==auth['physical_source'])) if checked(r).data['authorization']['physical_source']==auth['physical_source'] for h in r.data['history'] if h.get('plan_id')]
 if daily_limit is not None and len({h['plan_id'] for h in all_reads+same_source if datetime.fromisoformat(h['at']).astimezone(CHINA).date().isoformat()==day})>=daily_limit:
  row.data={**row.data,'progress':'业务或来源每日次数已用完；不会更换授权扩大次数'};return
 last=max([datetime.fromisoformat(h['at']) for h in all_reads+same_source],default=None)
 if last and time<last+timedelta(minutes=auth['cooldown_minutes']):row.data={**row.data,'progress':'仍在已确认的读取间隔内，不重复读取'};return
 channel=scoped(db,MediaChannel,action['resource']['channel_id'],p);gateway=scoped(db,MediaGateway,channel.gateway_id,p)
 from .video_read_boundary import available,ReadBudgetWait
 try:available(db,p,channel,gateway,'automatic',auth['seconds'],time)
 except ReadBudgetWait as error:
  row.data={**row.data,'progress':str(error.detail)+'；在原授权期限内等待'};return
 for session in db.scalars(select(StreamSession).where(StreamSession.status.in_(['queued','connecting','capturing']))):
  other=db.get(MediaChannel,session.channel_id);node=db.get(MediaGateway,other.gateway_id)
  if (node.data['rtsp_origin'],other.path)==(gateway.data['rtsp_origin'],channel.path):row.data={**row.data,'progress':'原来源正在读取，等待原请求结束；不消耗一次读取次数'};return
 from .supplement_api import Approval,approve_saved
 request_id='automatic-watch-'+row.id+'-'+assessment.id
 plan=SupplementPlan(workspace_id=row.workspace_id,instance_id=row.instance_id,assessment_id=assessment.id,owner_id=row.owner_id,request_id=request_id,data={
  'creation_hash':digest({'assessment_id':assessment.id,'request_id':request_id}),'assessment_result_hash':assessment.data['result_hash'],'proposal':proposal,'proposal_hash':digest(proposal),'automatic_watch_id':row.id,'automatic_authorization_hash':row.data['authorization_hash'],'progress':'已确认边界内的自动补充需求'})
 db.add(plan);db.flush();original(db,p,plan,check_old_refs=True)
 plan=approve_saved(plan.id,Approval(version=plan.version,action_keys=[action['key']],seconds=auth['seconds'],confirm_one_time_read=True,request_id='automatic-approve-'+plan.id),p,db)
 record.update(plan_id=plan.id,decision='在已确认边界内读取一次，失败也计入次数')
 row.data={**row.data,'history':row.data['history']+[record],'history_hash':digest(row.data['history']+[record]),'progress':'自动补充读取已保存，原件在本机处理后另建核查'};row.version+=1
 audit(db,p,'business.automatic_read_queued',row.id,{'assessment_id':assessment.id,'plan_id':plan.id,'seconds':auth['seconds']})

def schedule_once(workspace_id=None):
 from sqlalchemy.orm import Session
 from .db import engine
 from .worker import actor
 from .media_monitor import node_lock
 from .security import Principal
 with Session(engine()) as db:
  # Global media lock precedes watch/plan locks, including when no reads are queued.
  node_lock(db)
  query=select(SupplementWatch).where(SupplementWatch.status=='active')
  if workspace_id is not None:query=query.where(SupplementWatch.workspace_id==workspace_id)
  rows=list(db.scalars(query.order_by(SupplementWatch.created_at).limit(20).with_for_update(skip_locked=True)))
  for row in rows:
   identity=row.id
   try:
    row=db.scalar(select(SupplementWatch).where(SupplementWatch.id==identity,True if workspace_id is None else SupplementWatch.workspace_id==workspace_id).with_for_update())
    if row is None:continue
    db.refresh(row)
    if row.status=='active':advance(db,actor(db,row.workspace_id,row.owner_id),row)
    db.flush()
   except (HTTPException,ValueError,KeyError,TypeError,StopIteration) as error:
    db.rollback();node_lock(db);row=db.scalar(select(SupplementWatch).where(SupplementWatch.id==identity,True if workspace_id is None else SupplementWatch.workspace_id==workspace_id).with_for_update())
    if row is None:continue
    if row.status!='active':continue
    halt(db,Principal(row.owner_id,row.workspace_id,'viewer','automatic-read-stop'),row,'blocked',error.detail if isinstance(error,HTTPException) and isinstance(error.detail,str) else '业务、来源、模型或保存授权未通过核对')
   db.commit();node_lock(db)
