"""One approved local stream read closes a saved-material gap, never a quality/time gap."""
import copy
from fastapi import HTTPException
from sqlalchemy import select
from .models import (SupplementPlan,EvidenceAssessment,VideoDomainBinding,MediaChannel,MediaGateway,DomainInstance,
                     SpecialistInstallation,StreamSession,VideoRun,MediaMonitor,Membership,User,now)
from .services import scoped,published,require,audit
from .contracts import digest
from .evidence_assessment import checked as checked_assessment,scoped_objects,freshness,ref
from .local_specialists import ready_detection_install
from .media import validated_channel,channel_manifest,CAPTURE_HASH
from .local_video import PIPELINE_HASH
from .video_domain import snapshot
from .video_api import Configuration

READ_CONFIGURATION=Configuration(sample_hz=1,presence_rule=None).model_dump(mode='json')
TERMINAL={'completed','cancelled','failed','blocked'}

def checked(row):
 if 'frame_supplement_v236' in row.data:return row
 for k in ['proposal','approved','sessions','source_receipts','comparison']:
  if k in row.data and digest(row.data[k])!=row.data.get(k+'_hash'):raise HTTPException(409,'保存的补充方案不一致，请保留原记录核对')
 return row

def original(db,p,row,*,check_old_refs=False):
 from .frame_supplement_v236 import is_family
 if is_family(db,row):raise HTTPException(409,'固定画面补充需要通过原任务专用入口核对，不能变成旧对象全局流程')
 checked(row);assessment=checked_assessment(scoped(db,EvidenceAssessment,row.assessment_id,p))
 if assessment.status!='completed' or assessment.data['result_hash']!=row.data['assessment_result_hash']:raise HTTPException(409,'原资料核查不完整或已变，不能继续原补充方案')
 request=assessment.data['request'];_,release,objects=scoped_objects(db,p,row.instance_id,request['entity_ids'])
 if release.id!=request['release_id'] or [ref(o) for o in objects]!=request['object_refs']:raise HTTPException(409,'实例或对象已改变，请重新核查后规划补充')
 if check_old_refs and not freshness(db,p,assessment)['metadata_versions_unchanged']:raise HTTPException(409,'原引用资料或人工核查已改变，请先重新核查再补充')
 return assessment

def resource(db,p,binding,installation):
 channel,gateway=validated_channel(db,p,binding.channel_id)
 source=published(db,p,channel.data.get('capability_id'),'source')
 if source.data!=channel_manifest(channel,gateway).model_dump(mode='json'):raise HTTPException(409,'来源能力已改变，请先重新登记验证')
 current=snapshot(db,p,channel)
 if not current or current['binding_id']!=binding.id or current['entity_version']!=binding.data['entity_version']:raise HTTPException(409,'场景关联已变化')
 from .instance_domain import resolve_target
 from .models import Entity
 entity=scoped(db,Entity,binding.entity_id,p);target=resolve_target(db,p,entity,binding.data.get('instance_release_id'),binding.data.get('business_id'))
 if not target:raise HTTPException(409,'对象没有当前启用的实例目的地')
 if entity.version!=binding.data['entity_version'] or any(binding.data.get(k)!=v for k,v in target.items()):raise HTTPException(409,'对象版本或业务目的地已变化')
 installation=ready_detection_install(db,p,installation.id)
 return {'channel_id':channel.id,'channel_name':channel.data['name'],'gateway_id':gateway.id,'source_id':source.id,'source_hash':source.content_hash,
  'channel_generation':channel.data['generation'],'gateway_generation':gateway.data['generation'],'binding_id':binding.id,'binding_generation':binding.data['generation'],
  'entity_id':entity.id,'entity_version':entity.version,'installation_id':installation.id,'installation_generation':installation.data['generation'],'backend':installation.data['backend'],
  'capture_hash':CAPTURE_HASH,'pipeline_hash':PIPELINE_HASH,'instance_release_id':target['instance_release_id']}

def propose(db,p,assessment):
 if assessment.status!='completed':raise HTTPException(409,'请等原核查完成再规划补充资料')
 request=assessment.data['request'];scoped_objects(db,p,assessment.instance_id,request['entity_ids'])
 if not freshness(db,p,assessment)['metadata_versions_unchanged']:raise HTTPException(409,'原依据已改变，请先重新核查')
 objects=[];actions=[];installations=list(db.scalars(select(SpecialistInstallation).where(SpecialistInstallation.workspace_id==p.workspace_id,SpecialistInstallation.status=='ready')))
 for obj in assessment.data['result']['objects']:
  needs=[]
  for gap in obj['gaps']:
   item={**copy.deepcopy(gap),'automatically_resolvable':False}
   if gap['kind']=='missing_saved_processing':
    item['message']='需要补充已关联此对象的'+{'video':'视频','audio':'音频','imagery':'影像','archive':'业务档案','observations':'登记观测数据'}.get(gap['modality'],'未支持类型资料')+'原件及处理结果。'
    if gap['modality']=='video':
     bindings=list(db.scalars(select(VideoDomainBinding).where(VideoDomainBinding.workspace_id==p.workspace_id,VideoDomainBinding.entity_id==obj['object']['id'],VideoDomainBinding.status=='active')))
     for binding in bindings:
      if binding.data.get('instance_id')!=assessment.instance_id:continue
      for installation in installations:
       try:frozen=resource(db,p,binding,installation)
       except HTTPException:continue
       candidate={'key':digest(frozen)[:24],'kind':'read_video','target_gap':'missing_saved_processing','modality':'video','object_name':obj['object']['name'],'resource':frozen,
        'name':'读取 '+frozen['channel_name']+' 的一个视频片段','default_seconds':6,'limits':'仅H264视频，只读，不转动摄像头、不保存音轨；采集时间和完整覆盖仍未核实。'}
       actions.append(candidate);item['automatically_resolvable']=True;break
    if not item['automatically_resolvable']:item['resource_issue']='当前没有通过验证且已关联该对象的可执行来源；不能用其他模态代替。'
   needs.append(item)
  objects.append({'object':obj['object'],'needs':needs})
 return {'objects':objects,'actions':actions,'read_configuration':READ_CONFIGURATION,'max_selected_actions':3,'cloud_calls':0,'entity_state_update':False}

def validate_action(db,p,action):
 frozen=action['resource'];binding=scoped(db,VideoDomainBinding,frozen['binding_id'],p);installation=scoped(db,SpecialistInstallation,frozen['installation_id'],p)
 if resource(db,p,binding,installation)!=frozen:raise HTTPException(409,'通道、场景关联、本地模型或执行版本已变，请另建补充方案')
 channel=scoped(db,MediaChannel,frozen['channel_id'],p);gateway=scoped(db,MediaGateway,frozen['gateway_id'],p)
 from .continuous_media import reject_source_conflict
 reject_source_conflict(db,channel,gateway)
 if db.scalar(select(MediaMonitor.id).where(MediaMonitor.channel_id==channel.id,MediaMonitor.status.in_(['active','paused','blocked']))):raise HTTPException(409,'该通道已有读取计划，请先处理原计划')
 return channel,gateway,installation

def stop_sessions(db,p,row,reason):
 for item in row.data.get('sessions',[]):
  session=scoped(db,StreamSession,item['id'],p,True)
  if session.data.get('supplement_plan_id')!=row.id:continue
  if session.status in ['queued','connecting','capturing']:
   session.status='cancelled';session.version+=1;session.data={**session.data,'stop_reason':reason}
  if session.video_run_id:
   run=scoped(db,VideoRun,session.video_run_id,p,True)
   if run.status in ['queued','running']:run.status='cancelled';run.version+=1;run.data={**run.data,'error':reason}

def followup(db,p,row,identity=None):
 result=checked_assessment(scoped(db,EvidenceAssessment,identity or row.data['followup_assessment_id'],p,True))
 if result.instance_id!=row.instance_id or result.request_id!='supplement-assess-'+row.id or result.data['request'].get('parent_id')!=row.assessment_id:raise HTTPException(409,'补充核查关联不一致，请保留原记录核对')
 return result

def stop_followup(db,p,row,reason,identity=None):
 if not identity and not row.data.get('followup_assessment_id'):return
 result=followup(db,p,row,identity)
 if result.status in {'queued','running'}:result.status='cancelled';result.version+=1;result.data={**result.data,'progress':reason}

def block(db,p,row,reason):
 from .frame_supplement_v236 import is_family,block as frame_block
 if is_family(db,row):return frame_block(db,p,row,reason)
 if row.status not in {'reading','rechecking'}:return
 stop_sessions(db,p,row,reason);stop_followup(db,p,row,'补充方案已阻止，核查条件保留');row.status='blocked';row.version+=1;row.data={**row.data,'error':reason,'progress':'补充观察已阻止，已保存原件保留'};audit(db,p,'business.supplement_blocked',row.id,{'reason':reason})

def advance(db,p,row):
 from .frame_supplement_v236 import is_family,advance as frame_advance
 if is_family(db,row):return frame_advance(db,p,row)
 if row.status not in {'reading','rechecking'}:return
 if row.status=='reading':
  # This state may create the fixed follow-up after source validation. Both
  # plan_guard() and original() can already lock package/context rows, so take
  # the ordinary assessment request -> Instance order before either call.
  from .evidence_assessment_api import lock_request
  lock_request(db,p,'supplement-assess-'+row.id)
  scoped(db,DomainInstance,row.instance_id,p,True)
 from .supplement_watch import plan_guard
 plan_guard(db,p,row)
 checked(row);assessment=original(db,p,row)
 # The original administrator's scope remains necessary for this approved plan.
 if not db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(User.id==row.data['approved_by'],User.active.is_(True),Membership.workspace_id==p.workspace_id,Membership.role=='admin')):raise HTTPException(409,'原补充方案批准人的管理员授权已撤销')
 for action in row.data['approved']['actions']:validate_action(db,p,action)
 if row.status=='reading':
  proofs=[];pending=False;failed=False
  for item in row.data['sessions']:
   session=scoped(db,StreamSession,item['id'],p)
   if session.data.get('supplement_plan_id')!=row.id:raise HTTPException(409,'补充读取关联不一致，请保留记录核对')
   run=scoped(db,VideoRun,session.video_run_id,p) if session.video_run_id else None
   pending=pending or session.status in ['queued','connecting','capturing'] or bool(run and run.status in ['queued','running'])
   successful=session.status=='captured' and run and run.status=='succeeded';failed=failed or (not successful and not pending)
   proofs.append({'session_id':session.id,'capture_status':session.status,'video_run_id':session.video_run_id,'processing_status':run.status if run else None,'evidence_id':session.evidence_id,'input_sha256':session.data.get('input_sha256')})
  if pending:return
  # Even a partial/failed read is rechecked; it never appears as all needs resolved.
  from .evidence_assessment_api import Request,create
  req=assessment.data['request'];body=Request(instance_id=row.instance_id,entity_ids=req['entity_ids'],purpose=req['purpose'],goal=req['goal'],required_modalities=req['required_modalities'],lookback_hours=req['lookback_hours'],parent_id=assessment.id,request_id='supplement-assess-'+row.id)
  row.data={**row.data,'source_receipts':proofs,'source_receipts_hash':digest(proofs),'has_failed_or_partial_reads':failed,'progress':'原件与处理状态已保存，正在重新核查'};db.flush()
  result=create(body,p,db)
  row=scoped(db,SupplementPlan,row.id,p,True);db.refresh(row)
  if row.status!='reading':
   stop_followup(db,p,row,'原补充方案已停止或阻止，核查条件保留',result['id'])
   row.data={**row.data,'followup_assessment_id':result['id']};db.commit();return
  row.status='rechecking';row.version+=1;row.data={**row.data,'followup_assessment_id':result['id']};db.commit();return
 result=followup(db,p,row)
 if result.status in {'queued','running'}:return
 if result.status!='completed':raise HTTPException(409,'新资料核查未完成，请查看已保存请求并恢复')
 comparisons=[]
 for new in result.data['result']['objects']:
  old=next(o for o in assessment.data['result']['objects'] if o['object']['id']==new['object']['id'])
  comparisons.append({'object':new['object'],'before_processing_records':old['processing_records'],'after_processing_records':new['processing_records'],
   'remaining_gaps':copy.deepcopy(new['gaps']),'current_state_sufficient':new['current_state_sufficient'],'state_conclusion':new['state_conclusion']})
 row.status='completed';row.version+=1;row.data={**row.data,'comparison':comparisons,'comparison_hash':digest(comparisons),'progress':'增补资料已重新核查，剩余缺口保留'};audit(db,p,'data.supplement_rechecked',row.id,{'assessment_id':result.id})

def schedule_once(workspace_id=None):
 from sqlalchemy import func
 from sqlalchemy.orm import Session
 from .db import engine
 from .worker import actor
 with Session(engine()) as db:
  # Do not prelock a batch of Plans. A watch expiry can own Instance while
  # stopping one of them; all source-processing work must acquire Instance
  # before its Plan, one transaction at a time.
  query=select(SupplementPlan.id,SupplementPlan.instance_id,SupplementPlan.workspace_id,
   SupplementPlan.status).where(SupplementPlan.status.in_(['reading','rechecking']))
  if workspace_id is not None:query=query.where(SupplementPlan.workspace_id==workspace_id)
  rows=list(db.execute(query.order_by(SupplementPlan.created_at,SupplementPlan.id).limit(20)))
  db.rollback()
  for candidate in rows:
   identity=candidate.id
   try:
    if candidate.status=='reading':
     # Exact ordinary lock_request namespace; a busy request must not stall
     # this scheduler or turn an otherwise valid plan into blocked work.
     from .frame_supplement_v236 import is_family,assessment_nonce
     pending=db.get(SupplementPlan,identity)
     nonce=assessment_nonce(pending) if is_family(db,pending) else 'supplement-assess-'+identity
     key=int(digest({'workspace':candidate.workspace_id,
      'request':nonce})[:16],16)-(1<<63)
     if db.scalar(select(func.pg_try_advisory_xact_lock(key))) is not True:
      db.rollback();continue
    instance=db.scalar(select(DomainInstance).where(DomainInstance.id==candidate.instance_id,
     DomainInstance.workspace_id==candidate.workspace_id).with_for_update(skip_locked=True)
     .execution_options(populate_existing=True))
    if instance is None:
     db.rollback();continue
    row=db.scalar(select(SupplementPlan).where(SupplementPlan.id==identity,
     SupplementPlan.workspace_id==candidate.workspace_id,SupplementPlan.instance_id==candidate.instance_id)
     .with_for_update(skip_locked=True).execution_options(populate_existing=True))
    if row is None:
     db.rollback();continue
    db.refresh(row)
    if row.status not in {'reading','rechecking'} or row.status!=candidate.status or row.instance_id!=candidate.instance_id:
     db.rollback();continue
    p=actor(db,row.workspace_id,row.data['approved_by']);advance(db,p,row);db.commit()
   except (HTTPException,ValueError,KeyError,TypeError) as error:
    db.rollback();row=db.scalar(select(SupplementPlan).where(SupplementPlan.id==identity,
     SupplementPlan.workspace_id==candidate.workspace_id).with_for_update(skip_locked=True)
     .execution_options(populate_existing=True))
    if row is None:
     db.rollback();continue
    db.refresh(row)
    if row.instance_id!=candidate.instance_id:
     db.rollback();continue
    from .security import Principal
    # block() only stops saved sessions/runs/follow-ups and writes metadata;
    # it never reacquires Instance or current source/package authority.
    p=Principal(row.data['approved_by'],row.workspace_id,'viewer','supplement-stop');block(db,p,row,error.detail if isinstance(error,HTTPException) and isinstance(error.detail,str) else '执行授权、来源关联或已保存方案未通过核对');db.commit()
