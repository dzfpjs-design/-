"""Explicit candidate filters, no model rerun, field-state assertion or device action."""
import copy,math
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from fastapi import HTTPException
from . import models as m
from .contracts import digest
from .services import scoped,require,enqueue_event,check_version,audit
from .instance_api import active
from .observation_integrity import checked_report,CHECK_ERRORS

PROFILES={
 'GeoRun':('遥感区域候选数量','区域候选数量','number'),
 'GeoPair':('双影像差异候选像素数','差异候选像素数','number'),
 'SpeechActivityRun':('音频语音候选区间数','语音候选区间数','number'),
 'AudioRun':('整片机器转写文字','机器转写文字','text'),
 'SegmentedAudioRun':('分段机器转写文字','分段机器转写文字','text')}

def options(db,p):
    from .local_observations import options as local_options
    result=[]
    for row in local_options(db,p):
        cap=scoped(db,m.Capability,row['id'],p)
        kind='segmentation' if 'segmentation' in cap.data['output_schema']['properties'] else 'detection'
        result.append({'key':'local:'+cap.id,'name':row['name']+' · '+('区域候选数量' if kind=='segmentation' else '目标候选数量'),
            'value_kind':'number','family':'local','model_id':cap.id,'output_kind':kind})
    return result+[{'key':'native:'+k,'name':v[0],'value_kind':v[2],'family':'native','native_kind':k} for k,v in PROFILES.items()]

def definition(db,p,identity,body):
    require(p,'admin','operator');instance=scoped(db,m.DomainInstance,identity,p,True);instance,release=active(db,p,identity)
    obj=scoped(db,m.Entity,body.entity_id,p)
    requested_release=getattr(body,'release_id',None)
    if ((requested_release is not None and requested_release!=release.id)
        or (obj.schema_capability_id!=release.package_id and requested_release!=release.id)):
        raise HTTPException(409,'实例版本已变化，请核对当前版本并明确确认候选规则')
    from .entity_release_compat import require_entity
    proof=require_entity(db,p,instance,release,obj)
    profile=next((r for r in options(db,p) if r['key']==body.profile),None)
    if not profile:raise HTTPException(409,'请选择已就绪的本地模型或已支持的原生结果')
    if profile['value_kind']=='number' and (body.operator not in {'gt','gte'} or body.threshold is None or not math.isfinite(body.threshold) or body.threshold<0 or body.threshold>1e9):raise HTTPException(422,'请填写有限、非负的候选数值条件')
    if profile['value_kind']=='text' and (body.operator!='contains' or not body.needle or not body.needle.strip()):raise HTTPException(422,'请填写要核查的文字内容')
    if not body.confirm_candidate_only:raise HTTPException(422,'请确认此规则只产生待核查线索，不认定现场异常')
    if not math.isfinite(body.min_score) or not 0<=body.min_score<=1:raise HTTPException(422,'模型分数应在0至1之间')
    if body.label and profile.get('output_kind')!='detection':raise HTTPException(422,'此结果不含目标类别，不能指定类别筛选')
    if not body.name.strip():raise HTTPException(422,'请填写规则名称')
    if profile['value_kind']=='number' and (not float(body.threshold).is_integer() or (body.operator=='gte' and body.threshold<1)):raise HTTPException(422,'候选数量条件必须为整数，至少匹配时应为1或更多')
    value={'schema':'local-candidate-watch-v1','name':body.name,'instance_id':identity,'release_id':release.id,'package_id':release.package_id,
       'entity_id':obj.id,'entity_version':obj.version,'profile':profile,'operator':body.operator,
       'threshold':body.threshold if profile['value_kind']=='number' else None,'needle':body.needle.strip() if profile['value_kind']=='text' else None,
       'label':body.label.strip() if body.label and body.label.strip() else None,'min_score':body.min_score,
       'actor_id':p.user_id,'cloud_calls':0,'candidate_only':True,'entity_state_updated':False}
    if obj.schema_capability_id!=release.package_id:
        value.update(entity_source_package_id=obj.schema_capability_id,entity_definition_hash=digest(proof))
    if profile['family']=='local':value['model_hash']=digest(scoped(db,m.Capability,profile['model_id'],p).data)
    return value

def create(db,p,identity,body):
    require(p,'admin','operator');scoped(db,m.DomainInstance,identity,p,True)
    request_value=body.model_dump(exclude={'request_id'})
    if request_value.get('release_id') is None:request_value.pop('release_id',None)
    request_hash=digest(request_value)
    old=db.scalar(select(m.CandidateWatch).where(m.CandidateWatch.workspace_id==p.workspace_id,m.CandidateWatch.instance_id==identity,m.CandidateWatch.data['request_id'].as_string()==body.request_id))
    if old:
        if old.data['request_hash']!=request_hash:raise HTTPException(409,'原规则请求内容不一致，请先核对已保存规则')
        return old
    value=definition(db,p,identity,body);signature=digest(value)
    row=m.CandidateWatch(workspace_id=p.workspace_id,instance_id=identity,data={'definition':value,'definition_hash':signature,'request_id':body.request_id,'request_hash':request_hash,'effective_from':m.now().isoformat(),'history':[]})
    db.add(row);db.flush();audit(db,p,'candidate.watch_confirmed',row.id,{'definition_hash':signature});return row

def verify_definition(db,p,watch,*,enabled=False,allow_paused=False):
    from .sound_candidate_watches import handles_watch
    if handles_watch(db,p,watch):raise HTTPException(409,'请在声音类别关注中核对和修改此规则')
    value=watch.data['definition']
    if digest(value)!=watch.data['definition_hash'] or value['instance_id']!=watch.instance_id:raise ValueError('原线索规则摘要不一致')
    if enabled:
        require(p,'admin','operator')
        if p.user_id!=value['actor_id']:
            from .worker import actor
            actor(db,watch.workspace_id,value['actor_id'])
        if watch.status!='enabled' and not allow_paused:raise ValueError('线索规则已暂停')
        instance=scoped(db,m.DomainInstance,watch.instance_id,p,True);instance,release=active(db,p,instance.id)
        if release.id!=value['release_id']:raise ValueError('业务实例版本已变化，请重新确认规则')
        obj=scoped(db,m.Entity,value['entity_id'],p)
        from .entity_release_compat import require_entity
        proof=require_entity(db,p,instance,release,obj)
        has_proof=any(key in value for key in ('entity_source_package_id','entity_definition_hash'))
        if (release.package_id!=value['package_id'] or ((obj.schema_capability_id!=value['package_id'] or has_proof)
            and (value.get('entity_source_package_id')!=obj.schema_capability_id
                or value.get('entity_definition_hash')!=digest(proof)))):
            raise ValueError('对象归属或完整定义已经变化')
        if value['profile']['family']=='local':
            from .local_observations import checked_cap
            if digest(checked_cap(db,p,value['profile']['model_id']).data)!=value['model_hash']:raise ValueError('已批准的本地模型版本变化')
    return value

def source(db,p,row,*,event_id=None,job_id=None):
    from .native_sound_receipts import handles as sound_source
    if sound_source(db,p,row):
        from .sound_candidate_watches import source as sound_report
        return sound_report(db,p,row,event_id=event_id,job_id=job_id)
    if not row.data.get('candidate_only'):raise ValueError('此规则只读取本地模型候选')
    if row.data.get('native_instance_id'):
        from .native_receipts import checked,packet
        record=checked(db,p,row);value=row.data['native_packet']
        return {'family':'native','native_kind':value['source_key'].split(':')[0],'attributes':value['attributes'],'original':value,
            'source_name':record['name'],'review':record['review_state'],'machine_hash':digest(value),'extracted':row.data['extracted']}
    report=checked_report(db,p,row,allow_candidates=True)
    from .perception_signals import machine_basis
    native=report['extracted'].get('segmentation') or report['extracted'].get('detection')
    if not native or native.get('cloud_calls')!=0:raise ValueError('原结果不是可核对的本地候选')
    original=machine_basis(db,p,row,event_id=event_id,job_id=job_id)
    return {'family':'local','model_id':row.data['extractor_id'],'attributes':report['attributes'],'original':report['extracted'],
        'source_name':report['source_name'],'review':report['review'],'machine_hash':original['machine_hash'],
        'extracted':report['extracted'],'time_anchor':original['time_anchor'],'time_protocol':original['protocol']}

def selection(value,row,report):
    if report['family']=='environment_sound':
        from .sound_candidate_watches import selection as sound_selection
        return sound_selection(value,row,report)
    profile=value['profile']
    if row.entity_id!=value['entity_id'] or report['family']!=profile['family']:return None
    if profile['family']=='local':
        if report['model_id']!=profile['model_id']:return None
        native=report['extracted'][profile['output_kind']]
        rows=native['masks' if profile['output_kind']=='segmentation' else 'detections']
        metric=sum(r['score']>=value['min_score'] and (not value['label'] or r.get('label')==value['label']) for r in rows)
    else:
        if report['native_kind']!=profile['native_kind']:return None
        metric=report['attributes'][PROFILES[profile['native_kind']][1]]
    if profile['value_kind']=='number':
        if isinstance(metric,bool) or not isinstance(metric,(int,float)) or not math.isfinite(metric) or metric<0:raise ValueError('候选指标不是有限非负数值')
        hit=metric>value['threshold'] if value['operator']=='gt' else metric>=value['threshold']
    else:
        texts=[metric] if isinstance(metric,str) else metric
        if not isinstance(texts,list) or any(not isinstance(t,str) for t in texts):raise ValueError('机器文字结构不一致')
        hit=any(value['needle'] in t for t in texts)
    return {'field':profile['name'],'value':metric,'operator':value['operator'],'threshold':value['threshold'],'needle':value['needle'],'matched':hit}

def enqueue(db,p,watch,row,*,manual=False):
    if not manual and row.created_at<datetime.fromisoformat(watch.data['effective_from']):return None
    value=watch.data['definition'];profile=value['profile']
    if row.entity_id!=value['entity_id'] or not row.data.get('candidate_only'):return None
    if profile['family']=='local' and (row.data.get('native_instance_id') or row.data.get('extractor_id')!=profile['model_id']):return None
    if profile['family']=='native' and row.data.get('native_packet',{}).get('source_key','').split(':')[0]!=profile['native_kind']:return None
    key='candidate-check:'+watch.id+':'+str(watch.version)+':'+row.id
    db.execute(insert(m.Job).values(workspace_id=p.workspace_id,kind='check_candidate_watch',idempotency_key=key,
       data={'actor_id':value['actor_id'],'watch_id':watch.id,'watch_version':watch.version,'definition_hash':watch.data['definition_hash'],'observation_id':row.id}).on_conflict_do_nothing(index_elements=['workspace_id','idempotency_key']))
    return db.scalar(select(m.Job).where(m.Job.workspace_id==p.workspace_id,m.Job.idempotency_key==key))

def enqueue_new(db,p,row):
    if not row.data.get('candidate_only'):return
    for watch in db.scalars(select(m.CandidateWatch).where(m.CandidateWatch.workspace_id==p.workspace_id,m.CandidateWatch.status=='enabled',m.CandidateWatch.data['definition']['entity_id'].as_string()==row.entity_id)):
        from .sound_candidate_watches import handles_watch
        if handles_watch(db,p,watch):continue
        enqueue(db,p,watch,row)

def process(db,p,job):
    from .sound_candidate_watches import handles_job,process as sound_process
    if handles_job(db,p,job):return sound_process(db,p,job)
    from .workflow_abilities import WorkflowBlocked
    watch=scoped(db,m.CandidateWatch,job.data['watch_id'],p,True)
    try:value=verify_definition(db,p,watch,enabled=True)
    except CHECK_ERRORS:raise WorkflowBlocked('线索规则暂停、实例/模型版本或执行权限有变化；请核对规则后恢复')
    if watch.version!=job.data['watch_version'] or watch.data['definition_hash']!=job.data['definition_hash']:raise WorkflowBlocked('规则启停或条件版本变化，原检查不重放；请按当前规则明确核查')
    row=scoped(db,m.Observation,job.data['observation_id'],p)
    prior_event=None
    if not row.data.get('native_instance_id'):
        prior_event=db.scalar(select(m.Event).where(m.Event.workspace_id==p.workspace_id,
            m.Event.idempotency_key=='candidate:'+watch.id+':'+row.id))
    try:report=source(db,p,row,event_id=prior_event.id if prior_event else None,job_id=job.id)
    except CHECK_ERRORS:raise WorkflowBlocked('原模型结果、文件或对象关联未通过核对；恢复原依据后重试原检查')
    selected=selection(value,row,report)
    if not selected:raise ValueError('原结果与规则模型、对象或类型不一致')
    if report['review']=='rejected':raise WorkflowBlocked('原模型候选已被人工拒绝，请先回原处理核对已保存判断')
    result={'matched':selected['matched'],'selection':selected,'machine_hash':report['machine_hash'],'candidate_only':True,'cloud_calls':0}
    if report.get('time_anchor') is not None:result['time_anchor']=copy.deepcopy(report['time_anchor'])
    if job.data.get('result') and job.data['result']['machine_hash']!=report['machine_hash']:raise ValueError('原检查结果的机器依据发生变化，不能覆盖')
    if selected['matched']:
        payload={'observation_id':row.id,'entity_id':row.entity_id,'source_id':row.source_id,'watch_id':watch.id,
          'definition_hash':watch.data['definition_hash'],'watch_definition':copy.deepcopy(value),'selection':selected,
          'machine_hash':report['machine_hash'],'candidate_only':True,'evidence_ids':row.data.get('evidence_ids',[])}
        if report.get('time_anchor') is not None:payload['time_anchor']=copy.deepcopy(report['time_anchor'])
        event=None;key='candidate:'+watch.id+':'+row.id
        if prior_event:
            _,prior=event_report(db,p,prior_event)
            if prior_event.data['selection']!=selected or prior_event.data['watch_definition']!=value:raise ValueError('同一原处理的历史线索筛选依据不一致')
            event=prior_event
        if report['family']=='native':
            native=report['original']
            key='candidate-native:'+watch.id+':'+digest({'instance':watch.instance_id,'entity':row.entity_id,'source_key':native['source_key'],'source_machine_hash':native['source_machine_hash']})
            # Reuse original processing across normalizer v1/v2. Watch row lock serializes this check.
            event=db.scalar(select(m.Event).join(m.Observation,m.Observation.id==m.Event.data['observation_id'].as_string()).where(
                m.Event.workspace_id==p.workspace_id,m.Event.kind=='perception.candidate',m.Event.data['watch_id'].as_string()==watch.id,
                m.Observation.workspace_id==p.workspace_id,m.Observation.entity_id==row.entity_id,
                m.Observation.data['native_instance_id'].as_string()==watch.instance_id,
                m.Observation.data['native_packet']['source_key'].as_string()==native['source_key'],
                m.Observation.data['native_packet']['source_machine_hash'].as_string()==native['source_machine_hash']).order_by(m.Event.created_at).limit(1))
            if event:
                _,prior=event_report(db,p,event)
                if event.data['selection']!=selected or event.data['watch_definition']!=value:raise ValueError('同一原处理的历史线索筛选依据不一致')
        if not event:event=enqueue_event(db,p,'perception.candidate',key,payload)
        result['event_id']=event.id
    if report['family']=='local':
        from .observation_time_anchor_v135 import record_candidate_anchor
        record_candidate_anchor(db,p,job,result)
    job.data={**job.data,'result':result};return result

def event_report(db,p,event):
    from .sound_candidate_watches import handles_event,event_report as sound_event_report
    if handles_event(db,p,event):return sound_event_report(db,p,event)
    row=scoped(db,m.Observation,event.data['observation_id'],p);report=source(db,p,row,event_id=event.id)
    watch=scoped(db,m.CandidateWatch,event.data['watch_id'],p);value=verify_definition(db,p,watch)
    if event.kind!='perception.candidate' or digest(value)!=event.data['definition_hash'] or value!=event.data['watch_definition']:raise ValueError('候选通知与原规则不一致')
    selected=selection(value,row,report)
    if not selected or not selected['matched'] or selected!=event.data['selection'] or report['machine_hash']!=event.data['machine_hash'] or row.entity_id!=event.data['entity_id'] or row.source_id!=event.data['source_id'] or row.data.get('evidence_ids',[])!=event.data['evidence_ids']:
        raise ValueError('候选通知与原机器结果或筛选条件不一致')
    if digest(scoped(db,m.Capability,row.source_id,p).data)!=scoped(db,m.Capability,row.source_id,p).content_hash:raise ValueError('原来源版本摘要不一致')
    return row,{**report,'extracted':{'attributes':report['attributes'],'anomalies':[selected]},'basis':'explicit_local_candidate_filter'}

def deliver_pending(workspace_id=None):
    from .db import engine
    from sqlalchemy.orm import Session
    from .worker import deliver_events, _workspace_uuid
    if workspace_id is not None: workspace_id = _workspace_uuid(workspace_id)
    if workspace_id is not None:
        from .sound_candidate_watches import deliver_pending as deliver_sound
        deliver_sound(workspace_id)
    with Session(engine()) as db:
        from .sound_candidate_watches import EVENT_SCHEMA,ISSUED
        from sqlalchemy import func
        sound_audit=select(m.Audit.id).where(m.Audit.workspace_id==m.Event.workspace_id,
            m.Audit.action==ISSUED,m.Audit.object_id==m.Event.id).exists()
        query=select(m.Event.workspace_id,m.Event.data['observation_id'].as_string()).where(m.Event.kind=='perception.candidate',m.Event.status=='pending_delivery',
            func.coalesce(m.Event.data['schema'].as_string(),'')!=EVENT_SCHEMA,
            ~m.Event.idempotency_key.startswith('candidate-sound:'),~sound_audit)
        if workspace_id is not None: query=query.where(m.Event.workspace_id==workspace_id)
        rows=db.execute(query.limit(100)).all()
    for workspace,observation in rows:deliver_events(workspace_id=workspace,observation_id=observation)

def checks(db,p,watch):
    value=verify_definition(db,p,watch)
    rows=list(db.scalars(select(m.Job).where(m.Job.workspace_id==p.workspace_id,m.Job.kind=='check_candidate_watch',m.Job.data['watch_id'].as_string()==watch.id).order_by(m.Job.created_at.desc()).limit(51)))
    result=[]
    for job in rows[:50]:
        r={'id':job.id,'version':job.version,'status':job.status,'error':job.data.get('error'),'attempts':job.attempts,'result':None,'integrity_issue':None,'created_at':job.created_at.isoformat()}
        if job.data.get('result'):
            try:
                report=source(db,p,scoped(db,m.Observation,job.data['observation_id'],p),job_id=job.id);selected=selection(value,scoped(db,m.Observation,job.data['observation_id'],p),report)
                if report['machine_hash']!=job.data['result']['machine_hash'] or selected!=job.data['result']['selection']:raise ValueError('原模型依据已经变化')
                r['result']={**job.data['result'],'source_review':report['review']}
                if job.data['result'].get('event_id'):
                    event=scoped(db,m.Event,job.data['result']['event_id'],p);event_report(db,p,event)
                    r['result']['delivery_status']=event.status
            except CHECK_ERRORS:r['integrity_issue']='原模型或文件依据需恢复，暂不显示候选值'
        result.append(r)
    return {'records':result,'has_more':len(rows)>50}
