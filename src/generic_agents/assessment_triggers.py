"""Configured business triggers inspect saved local evidence; never probe devices or cloud."""
import copy,re
from datetime import datetime,timedelta
from fastapi import HTTPException
from sqlalchemy import select,or_
from .contracts import digest
from .services import scoped,audit
from .models import InstanceRelease,EvidenceAssessment,VideoRun,VideoFrame,Task
from .evidence_assessment import ref,checked as checked_assessment

POLL_SECONDS=15

def configured(db,p,plan,permissions):
 release=scoped(db,InstanceRelease,permissions['release_id'],p)
 policy=release.data['snapshot']['automation']['policies'][permissions['business_name']]
 triggers=policy.get('triggers',[])
 if not isinstance(triggers,list) or any(not isinstance(v,str) for v in triggers):raise HTTPException(409,'业务触发条件无效，请回配置核对')
 supported=[t for t in ['新观测到达','定时运行'] if t in triggers]
 if not supported:raise HTTPException(409,'当前业务没有启用“收到新数据”或定时触发；旧授权继续可等待独立核查')
 time=policy.get('time') if '定时运行' in supported else None
 if time is not None and (not isinstance(time,str) or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',time)):raise HTTPException(409,'请在业务自动化范围设置有效的每日运行时间')
 if '定时运行' in supported and time is None:raise HTTPException(409,'每日运行时间尚未确认，请回业务自动化配置补齐')
 return {'triggers':supported,'time':time,'timezone':'Asia/Shanghai','initial_local_check':True,'poll_seconds':POLL_SECONDS}

def checked(row):
 for name in ['automatic_checks','trigger_state']:
  if name in row.data and digest(row.data[name])!=row.data.get(name+'_hash'):raise HTTPException(409,'自动核查触发历史与保存摘要不一致，请保留记录核对')
 return row

def signature(db,p,row):
 auth=row.data['authorization'];entity=auth['criteria']['entity_ids'][0]
 runs=list(db.scalars(select(VideoRun).where(VideoRun.workspace_id==row.workspace_id,VideoRun.status.in_(['succeeded','failed','cancelled']),
  VideoRun.data['source_context']['domain_binding']['entity_id'].as_string()==entity,
  VideoRun.data['source_context']['domain_binding']['instance_release_id'].as_string()==auth['permissions']['release_id']).order_by(VideoRun.created_at.desc(),VideoRun.id).limit(301)))
 tasks=list(db.scalars(select(Task).where(Task.workspace_id==row.workspace_id,Task.data['instance_id'].as_string()==row.instance_id,Task.data['entity_ids'].contains([entity])).order_by(Task.created_at.desc(),Task.id).limit(201)))
 if len(runs)>300 or len(tasks)>200:raise HTTPException(409,'对象已有资料超过当前自动核查目录上限，请人工查看分页范围，不能假装全部已查')
 # Frame identities/versions include saved reviews without reading raw image/video bytes.
 frames=list(db.scalars(select(VideoFrame).where(VideoFrame.workspace_id==row.workspace_id,VideoFrame.run_id.in_([r.id for r in runs])).order_by(VideoFrame.run_id,VideoFrame.sample_index).limit(12001)))
 if len(frames)>12000:raise HTTPException(409,'保存画面超过当前自动核查上限，请人工核对范围')
 from .audio_daily import task_review
 reviews=[{'id':t.id,'perception_review':t.data.get('perception_review'),'receipts':t.data.get('workflow_runtime',{}).get('receipts',[])} for t in tasks if t.data.get('run_id') in {r.id for r in runs} and task_review(t)=='confirmed']
 return digest({'runs':[ref(r) for r in runs],'frames':[ref(r) for r in frames],'confirmed_reviews':reviews})

def reference(row,watch):
 expected={'watch_id':watch.id,'authorization_hash':watch.data['authorization_hash']}
 if row.instance_id!=watch.instance_id or row.data.get('automatic_check_reference')!=expected or digest(expected)!=row.data.get('automatic_check_reference_hash'):raise HTTPException(409,'自动核查与原有限授权不一致，请保留记录核对')
 return checked_assessment(row)

def stop_owned(db,p,watch,reason):
 # Cancellation may still stop its own rows if the trigger-history digest is broken.
 for row in db.scalars(select(EvidenceAssessment).where(EvidenceAssessment.workspace_id==watch.workspace_id,EvidenceAssessment.instance_id==watch.instance_id,
  EvidenceAssessment.data['automatic_check_reference']['watch_id'].as_string()==watch.id).with_for_update()):
  if row.status in ['queued','running']:
   row.status='cancelled';row.version+=1;row.data={**row.data,'progress':reason}

def guard(db,p,row):
 if not row.data.get('automatic_check_reference'):return
 from .supplement_watch import valid,clock,checked as checked_watch
 from .models import SupplementWatch
 watch=checked_watch(scoped(db,SupplementWatch,row.data['automatic_check_reference']['watch_id'],p))
 reference(row,watch)
 if watch.status!='active' or clock(db)>=datetime.fromisoformat(watch.data['authorization']['deadline']):raise HTTPException(409,'原自动核查授权已停止或到期，原条件与结果保留')
 valid(db,p,watch)


def advance(db,p,watch,time):
 auth=watch.data['authorization'];cfg=auth.get('business_check_triggers')
 if not cfg:return False
 checked(watch)
 checks=watch.data.get('automatic_checks',[])
 for item in checks:
  row=reference(scoped(db,EvidenceAssessment,item['assessment_id'],p),watch)
  if row.status in ['queued','running']:return True
  if row.status!='completed':raise HTTPException(409,'自动核查没有完成，请查看保存的条件与失败记录；不会自动再试')
 if len(checks)>=20:return False
 state=watch.data.get('trigger_state',{})
 if state.get('next_poll_at') and time<datetime.fromisoformat(state['next_poll_at']):return False
 from .supplement_watch import CHINA
 local=time.astimezone(CHINA);day=local.date().isoformat();fingerprint=signature(db,p,watch);reasons=[]
 due='定时运行' in cfg['triggers'] and local.strftime('%H:%M')>=cfg['time'] and state.get('scheduled_day')!=day
 if not checks:reasons.append('确认后的首次本地资料核查')
 if due:reasons.append('业务每日计划核查')
 if checks and '新观测到达' in cfg['triggers'] and fingerprint!=state.get('source_signature'):reasons.append('对象关联资料或核查结果已变化')
 next_state={**state,'next_poll_at':(time+timedelta(seconds=POLL_SECONDS)).isoformat()}
 if not reasons:
  watch.data={**watch.data,'trigger_state':next_state,'trigger_state_hash':digest(next_state)};return False
 from .evidence_assessment_api import Request,create_saved
 request_id='auto-check-'+watch.id+'-'+str(len(checks)+1)
 row=create_saved(Request(instance_id=watch.instance_id,**auth['criteria'],request_id=request_id),p,db)
 origin={'watch_id':watch.id,'authorization_hash':watch.data['authorization_hash']}
 row.data={**row.data,'automatic_check_reference':origin,'automatic_check_reference_hash':digest(origin)}
 entry={'assessment_id':row.id,'at':time.isoformat(),'reasons':reasons,'source_signature':fingerprint,'scheduled_day':day if due else None}
 checks=checks+[entry];next_state.update(source_signature=fingerprint)
 if due:next_state['scheduled_day']=day
 watch.data={**watch.data,'automatic_checks':checks,'automatic_checks_hash':digest(checks),'trigger_state':next_state,'trigger_state_hash':digest(next_state),'progress':'按已确认业务触发保存本地核查；尚未读取设备'};watch.version+=1
 audit(db,p,'data.automatic_assessment_queued',watch.id,{'assessment_id':row.id,'reasons':reasons})
 return True
