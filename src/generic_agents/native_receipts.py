"""Reuse verified local media outputs; never invent an inference or a capture clock."""
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select,text
from . import models as m
from .contracts import digest,Manifest
from .services import scoped,require,published,enqueue_event,audit
from .evidence_assessment import collect,ref,scoped_objects
from .instance_api import active
from .evidence import evidence_bytes

TYPES={c.__name__:c for c in [m.GeoRun,m.GeoPair,m.AudioRun,m.SpeechActivityRun,m.SegmentedAudioRun,m.VideoRun]}
LABELS={'candidate_masks':'区域候选数量','candidate_pixels':'差异候选像素数','unknown_pixels':'未知像素数','saved_asr_segments':'保存的转写段数','vad_intervals':'语音候选区间数','whole_asr_performed':'已执行整片转写','saved_frames':'已保存画面数'}

def candidates(db,p,identity,entity_ids=None):
    instance,release=active(db,p,identity)
    if entity_ids is None:
        objects=list(db.scalars(select(m.Entity).where(m.Entity.workspace_id==p.workspace_id,m.Entity.namespace==instance.namespace).order_by(m.Entity.id).limit(21)))
        truncated=len(objects)>20;eligible=[]
        from .entity_release_compat import require_entity
        for entity in objects[:20]:
            try:require_entity(db,p,instance,release,entity)
            except HTTPException:continue
            eligible.append(entity)
        objects=eligible;entity_ids=[r.id for r in objects]
    else:
        _,_,objects=scoped_objects(db,p,identity,entity_ids);truncated=False
    if not objects:return [],truncated
    request={'instance_id':identity,'release_id':release.id,'entity_ids':entity_ids,'object_refs':[ref(r) for r in objects],
        'required_modalities':['imagery','audio','video'],'objects':[{'id':r.id,'name':r.external_id} for r in objects]}
    records,_,cut=collect(db,p,request)
    # Pixel results have a dedicated producer and ACK. Discovery must never
    # feed them through the old native-packet writer or invent a GeoPair.
    records=[r for r in records if not r['key'].startswith('WorkflowPixelRun:')]
    return records,truncated or cut

def historical_candidates(db,p,identity,entity_id,source_key):
    """Check original bytes/results without granting a current operation."""
    instance=scoped(db,m.DomainInstance,identity,p);entity=scoped(db,m.Entity,entity_id,p)
    from .instance_domain import generated_release
    release=generated_release(db,p,entity)
    if release is None:raise HTTPException(409,'原对象来源版本无法核对，原归集记录保留')
    from .entity_release_compat import require_entity
    require_entity(db,p,instance,release,entity)
    request={'instance_id':identity,'release_id':release.id,'entity_ids':[entity.id],'object_refs':[ref(entity)],
        'required_modalities':['imagery','audio','video'],'objects':[{'id':entity.id,'name':entity.external_id}]}
    records,_,_=collect(db,p,request,historical=True,source_key=source_key)
    return records

def packet(db,p,record,entity_id,version='native-observation-v2'):
    if record['key'].startswith('WorkflowPixelRun:'):
        raise HTTPException(409,'像素成果使用其原任务独立交接，不通过旧多模归集重新签发')
    kind,identity=record['key'].split(':',1);row=scoped(db,TYPES[kind],identity,p)
    if row.status!='succeeded' or not record['integrity_verified'] or entity_id not in record['entity_ids']:
        raise HTTPException(409,'原处理尚未完成、原件不一致或对象关联不一致；请先查看原任务')
    calculated={'GeoRun':{'candidate_masks'},'GeoPair':{'candidate_pixels','unknown_pixels'},'AudioRun':{'whole_asr_performed'},'SpeechActivityRun':{'vad_intervals'},'SegmentedAudioRun':{'saved_asr_segments'},'VideoRun':{'saved_frames'}}
    fields={LABELS[k]:v for k,v in record['metrics'].items() if v is not None and k in LABELS and (version=='native-observation-v1' or k in calculated[kind])}
    if kind=='AudioRun':fields['机器转写文字']=row.data['result']['transcription']['text']
    if kind=='SpeechActivityRun':fields['语音候选区间']=row.data['result']['activity']['intervals']
    if kind=='SegmentedAudioRun':fields['分段机器转写文字']=[s['text'] for s in row.data['segments']]
    if kind in {'GeoRun','GeoPair'}:
        from .geo_daily import sources
        originals=sources(db,p,record['kind'],row);day=originals[0]['upload_day'];ids=[s['evidence_id'] for s in originals]
        date_basis='A_original_source_request' if kind=='GeoPair' else 'original_source_request'
    elif kind in {'AudioRun','SpeechActivityRun','SegmentedAudioRun'}:
        from .audio_daily import origin,TZ
        _,root,_=origin(db,p,record['kind'],row);day=str(root.created_at.astimezone(TZ).date());ids=[root.evidence_id];date_basis='original_audio_upload_request'
    else:
        ev,_=evidence_bytes(db,p,row.evidence_id);day=str(ev.created_at.astimezone(ZoneInfo('Asia/Shanghai')).date());ids=[ev.id];date_basis='original_video_saved_request'
    # Machine input/result and binding remain fixed; human review is read from the original workflow.
    machine={c.name:getattr(row,c.name) for c in row.__table__.columns if c.name not in {'data','version','created_at'} and not c.info.get('typed_payload_internal')}
    machine['data']={k:v for k,v in row.data.items() if k not in {'review','review_draft','review_history','reviewed_at'}}
    return {'schema':version,'source_key':record['key'],'source_machine_hash':digest(machine),'entity_id':entity_id,
        'task_id':record['task_id'],'modality':record['modality'],'kind':record['kind'],'attributes':fields,'evidence_ids':ids,
        'business_day':day,'date_basis':date_basis,'source_times':record['source_times'],'source_sha256':record['source_sha256'],
        'candidate_only':True,'cloud_calls':0,'entity_state_updated':False}

def source(db,p,instance):
    from .local_observations import source as derived_source
    manifest=Manifest(kind='source',namespace='platform_native_receipts',name='instance_'+instance.id.replace('-',''),version='1.0.0',
        owner='平台数据管理',description='业务关联的本地多模处理回执',allowed_agent_roles=['perception','data'],execution={'adapter':'upload'},
        input_schema={'type':'object'},output_schema={'type':'object'},specification={'managed_instance':instance.id,'timezone':'Asia/Shanghai','date_basis':'original_source_request'},
        data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False})
    return derived_source(db,p,instance,manifest=manifest)

def sync(db,p,identity,entity_ids=None,expected_preview=None):
    require(p,'admin','operator');records,cut=candidates(db,p,identity,entity_ids)
    eligible=[r for r in records if r['status']=='succeeded' and r['integrity_verified']]
    values=[packet(db,p,r,e) for r in eligible for e in r['entity_ids']]
    if cut or len(values)>300:raise HTTPException(409,'资料达到归集上限，请按对象范围分批；原结果保留')
    if expected_preview is not None and digest(values)!=expected_preview:raise HTTPException(409,'原处理或对象关联已变化，请重新读取归集清单')
    return _save_packets(db,p,identity,values,not_ready=len(records)-len(eligible))

def sync_queued_video(db,p,job):
    """Issue the exact new-family video that queued this job, never older runs."""
    from . import native_video_provenance_v164 as origin
    run_id=job.data.get('original_video_run_id')
    if not run_id or not origin.has_run_trace(db,p,run_id):
        return sync(db,p,job.data['instance_id'],job.data['entity_ids'])
    require(p,'admin','operator')
    ctx=origin.checked_run(db,p,run_id,live=True)
    if (job.workspace_id!=p.workspace_id or job.data['actor_id']!=p.user_id
        or job.idempotency_key!='native-sync:'+ctx['job'].id
        or job.data['instance_id']!=ctx['instance'].id
        or job.data['entity_ids']!=[ctx['entity'].id]):
        raise HTTPException(409,'视频归集任务与原处理、对象或业务范围不一致，原记录保留')
    return _save_packets(db,p,ctx['instance'].id,[ctx['packet']])

def _save_packets(db,p,identity,values,*,not_ready=0):
    """Only actually new observations capture a source intent in this transaction."""
    instance=scoped(db,m.DomainInstance,identity,p);src=source(db,p,instance);created=[];existing=[]
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'native_sync':identity})[:16],16)-(1<<63)})
    for value in values:
        record_id=value['source_key']+':'+value['entity_id']
        processing_version=digest(value)[:40]
        pair_origin=None
        video_context=None
        if value['source_key'].startswith('VideoRun:'):
            from . import native_video_provenance_v164 as video_origin
            original_rows=list(db.scalars(select(m.Observation).where(m.Observation.workspace_id==p.workspace_id,
                m.Observation.source_id==src.id,m.Observation.source_record_id==record_id)))
            if original_rows:
                for original in original_rows:
                    checked(db,p,original,historical=True)
                    if not video_origin.same(original.data['native_packet'],value):
                        raise HTTPException(409,'原视频来源已有不同机器结果或范围，原记录保留，不能替换')
                existing.extend(original.id for original in original_rows)
                continue
            run_id=value['source_key'].split(':',1)[1]
            if video_origin.has_run_trace(db,p,run_id):
                video_context=video_origin.checked_run(db,p,run_id,live=True)
                if not video_origin.same(video_context['packet'],value):
                    raise HTTPException(409,'原视频处理包与保存范围不一致，未新建收件')
            else:
                from .instance_data_receipts import verify_release
                run=scoped(db,m.VideoRun,run_id,p)
                source_context=run.data.get('source_context')
                if source_context is not None and type(source_context) is not dict:
                    raise HTTPException(409,'原视频来源范围格式无法核对')
                association=source_context.get('domain_binding') if source_context is not None else None
                if association is not None:
                    if type(association) is not dict or type(association.get('instance_release_id')) is not str or not association['instance_release_id']:
                        raise HTTPException(409,'原视频关联缺少准确业务版本，不能按旧记录接收')
                    release=scoped(db,m.InstanceRelease,association['instance_release_id'],p)
                    if release.instance_id!=identity or association.get('instance_id')!=identity:
                        raise HTTPException(409,'原视频关联的业务版本与本次归集不一致')
                else:
                    from .instance_domain import generated_release
                    release=generated_release(db,p,scoped(db,m.Entity,value['entity_id'],p))
                    if release is not None and release.instance_id!=identity:
                        raise HTTPException(409,'原视频对象的业务归属与本次归集不一致')
                if release is not None and verify_release(db,p,release) is not None:
                    raise HTTPException(409,'旧视频没有读取前的范围记录，不能补签为现代收件；原件与处理结果保留')
        if value['source_key'].startswith('GeoPair:'):
            from . import native_learned_geo_pair_provenance_v138 as learned_origin
            from . import native_geo_pair_provenance_v137 as rgb_origin
            pair_id=value['source_key'].split(':',1)[1]
            if learned_origin.has_pair_trace(db,p,pair_id):pair_origin=learned_origin
            elif rgb_origin.input_audits(db,p,pair_id):pair_origin=rgb_origin
            if pair_origin is not None:
                # Refuse incomplete new-family inventory before any new
                # Observation/Event is inserted, even when packet hash changed.
                pair_origin.checked_pair(db,p,pair_id)
                # A new comparison cannot be reissued under another normalizer
                # or a changed machine result. Old rows are never overwritten.
                original_rows=list(db.scalars(select(m.Observation).where(m.Observation.workspace_id==p.workspace_id,
                    m.Observation.source_id==src.id,m.Observation.source_record_id==record_id)))
                if original_rows:
                    for original in original_rows:
                        checked(db,p,original,historical=True)
                        if original.data['native_packet']['source_machine_hash']!=value['source_machine_hash']:
                            raise HTTPException(409,'原双影像来源编号已有不同机器结果，原记录保留，不能替换')
                    existing.extend(original.id for original in original_rows)
                    continue
        if value['source_key'].startswith('AudioRun:'):
            original_rows=list(db.scalars(select(m.Observation).where(m.Observation.workspace_id==p.workspace_id,
                m.Observation.source_id==src.id,m.Observation.source_record_id==record_id)))
            if original_rows:
                for original in original_rows:
                    checked(db,p,original)
                    if original.data['native_packet']['source_machine_hash']!=value['source_machine_hash']:
                        raise HTTPException(409,'原语音来源编号已有不同机器结果，不能替换或重新接纳')
                existing.extend(original.id for original in original_rows)
                continue
        row=db.scalar(select(m.Observation).where(m.Observation.workspace_id==p.workspace_id,m.Observation.source_id==src.id,m.Observation.source_record_id==record_id,m.Observation.processing_version==processing_version))
        if row:
            if row.data['native_packet']!=value:raise HTTPException(409,'原归集条件或机器结果已经变化，原记录保留，不能覆盖')
            existing.append(row.id);continue
        row=m.Observation(workspace_id=p.workspace_id,source_id=src.id,source_record_id=record_id,processing_version=processing_version,entity_id=value['entity_id'],observed_at=None,
            business_day=value['business_day'],status='processed',data={'candidate_only':True,'native_packet':value,'native_packet_hash':digest(value),
                'native_instance_id':identity,'source_hash':digest(src.data),'submitted_by':p.user_id,'evidence_ids':value['evidence_ids'],'extracted':{'attributes':value['attributes']}})
        db.add(row);db.flush();created.append(row.id)
        event=enqueue_event(db,p,'observation.data','data:'+row.id,{'observation_id':row.id,'entity_id':row.entity_id,'source_id':src.id,'evidence_ids':value['evidence_ids'],
            'native_receipt':value,'observed_at':None,'candidate_only':True,'extracted':row.data['extracted'],'invocation_id':None})
        audit(db,p,'observation.native_receipt_saved',row.id,{'source_key':value['source_key'],'instance_id':identity})
        if video_context is not None:
            marker=video_origin.issued_anchor(video_context,row,event)
            row.data={**row.data,video_origin.ISSUED:marker}
            audit(db,p,video_origin.ISSUED_ACTION,row.id,marker)
            db.flush()
        if pair_origin is not None:
            context=pair_origin.checked_pair(db,p,value['source_key'].split(':',1)[1])
            db.flush()
            marker=pair_origin.issued_anchor(context,row,event)
            row.data={**row.data,pair_origin.ISSUED:marker}
            audit(db,p,pair_origin.ISSUED_ACTION,row.id,marker)
            db.flush()
        from .native_audio_provenance_v136 import input_audits
        if value['source_key'].startswith('AudioRun:') and input_audits(db,p,value['source_key'].split(':',1)[1]):
            from .native_audio_provenance_v136 import checked_run,ISSUED,ISSUED_ACTION
            context=checked_run(db,p,value['source_key'].split(':',1)[1])
            db.flush()
            marker={'schema':'native-audio-issued-v136','run_id':context['run'].id,
                'task_id':context['task'].id,'job_id':context['job'].id,
                'activation_id':context['run'].data.get('workflow_graph_scope',{}).get('activation_id'),
                'event_id':event.id,'native_packet_hash':digest(value),'source_id':src.id,'entity_id':row.entity_id,
                'input_snapshot_hash':context['attached'].data['snapshot_hash'],
                'installation_hash':digest(context['installation']),'receipt_actor_id':p.user_id}
            row.data={**row.data,ISSUED:marker}
            audit(db,p,ISSUED_ACTION,row.id,marker)
            db.flush()
            from .graph_event_native_capture_v136 import capture
            capture(db,p,event,row)
    db.flush()
    from .candidate_watches import enqueue_new
    for oid in created:enqueue_new(db,p,scoped(db,m.Observation,oid,p))
    reconcile(db,p,identity)
    return {'created':len(created),'already_saved':len(existing),'not_ready':not_ready,'observation_ids':created+existing,'cloud_calls':0}

def sync_audio_run(db,p,run_id,*,task_id,activation_id=None):
    """Exact successful Run/Task/activation handoff; never scan an instance."""
    require(p,'admin','operator')
    from .native_audio_provenance_v136 import checked_run,packet_for_run,need
    context=checked_run(db,p,run_id)
    need(context['task'].id==task_id
        and context['run'].data.get('workflow_graph_scope',{}).get('activation_id')==activation_id)
    values=[packet_for_run(db,p,context,entity_id) for entity_id in context['task'].data['entity_ids']]
    return _save_packets(db,p,context['release'].instance_id,values)

def checked(db,p,row,*,historical=False):
    from .native_sound_receipts import handles as sound_receipt
    if sound_receipt(db,p,row):
        raise HTTPException(409,'环境声音成果请从原任务查看并核查，不能按语音转写或旧归集类别使用')
    from . import native_video_provenance_v164 as video_origin
    if video_origin.has_issued(db,p,row):
        context=video_origin.checked_observation(db,p,row,live=not historical)
        return video_origin.record_for_run(context)
    from . import native_learned_geo_pair_provenance_v138 as learned_origin
    if learned_origin.has_issued(db,p,row):
        context=learned_origin.checked_observation(db,p,row)
        return learned_origin.record_for_pair(context)
    from . import native_geo_pair_provenance_v137 as pair_origin
    if pair_origin.has_issued(db,p,row):
        context=pair_origin.checked_observation(db,p,row)
        return pair_origin.record_for_pair(context)
    from .native_audio_provenance_v136 import has_issued,checked_observation
    if has_issued(db,p,row):
        context=checked_observation(db,p,row,live=not historical)
        from .native_audio_provenance_v136 import record_for_run
        return record_for_run(context)
    v=row.data['native_packet']
    if v['schema'] not in {'native-observation-v1','native-observation-v2'}:raise HTTPException(409,'未知的归集规范，原记录保留')
    if digest(v)!=row.data['native_packet_hash'] or row.entity_id!=v['entity_id'] or row.business_day!=v['business_day'] or row.observed_at is not None or row.source_record_id!=v['source_key']+':'+v['entity_id'] or row.processing_version not in {'native-v1',digest(v)[:40]} or not row.data.get('candidate_only') or row.status!='processed' or row.data['extracted']!={'attributes':v['attributes']} or row.data['evidence_ids']!=v['evidence_ids']:
        raise HTTPException(409,'归集记录与原机器结果不一致，原说明保留')
    src=published(db,p,row.source_id,'source')
    if digest(src.data)!=row.data['source_hash'] or src.data['specification']['managed_instance']!=row.data['native_instance_id']:
        raise HTTPException(409,'归集来源归属已经变化')
    records=historical_candidates(db,p,row.data['native_instance_id'],row.entity_id,v['source_key']);record=next((r for r in records if r['key']==v['source_key']),None)
    if not record or packet(db,p,record,row.entity_id,version=v['schema'])!=v:raise HTTPException(409,'原处理、原件或对象关联已经变化，请查看原任务')
    return record

def view(db,p,row):
    from .native_sound_receipts import handles as sound_receipt
    if sound_receipt(db,p,row):
        from .native_sound_provider import legacy_receipt_view
        return legacy_receipt_view(db,p,row)
    from .native_pixel_receipts_v250 import handles as pixel_receipt
    if pixel_receipt(db,p,row):
        from .native_pixel_provider_v250 import legacy_receipt_view
        return legacy_receipt_view(db,p,row)
    result={'id':row.id,'created_at':row.created_at.isoformat(),'received_day':row.business_day,'entity_id':row.entity_id,'integrity_error':None,'record':None,'data_received':False}
    try:
        record=checked(db,p,row,historical=True);result['record']={**row.data['native_packet'],'name':record['name'],'review_state':record['review_state']}
        event=db.scalar(select(m.Event).where(m.Event.workspace_id==p.workspace_id,m.Event.idempotency_key=='data:'+row.id));result['data_received']=bool(event and event.status=='received')
        if row.data['native_packet']['source_key'].startswith('VideoRun:'):
            from . import native_video_provenance_v164 as video_origin
            issued=video_origin.has_issued(db,p,row)
            if issued and result['data_received']:
                from .instance_data_receipts import observation_receipt
                result.update(observation_receipt(db,p,row))
            result['data_reception_support']={'schema':'native-video-receipt-support-v164',
                'status':'issued_with_scope' if issued else 'legacy_without_scope',
                'can_replay_as_new_message':False,
                'message':('原读取范围、保存画面和数据接收分别核对；候选不代表现场状态或流程步骤已完成。' if issued else
                    '旧视频未保存读取前的范围依据，不能补签现代收件；原件和原结果保留。')}
        from . import native_geo_pair_provenance_v137 as pair_origin
        from . import native_learned_geo_pair_provenance_v138 as learned_origin
        if learned_origin.has_issued(db,p,row):
            if result['data_received']:
                from .instance_data_receipts import observation_receipt
                result.update(observation_receipt(db,p,row))
            result['data_reception_support']={'schema':'native-learned-geo-pair-receipt-support-v138',
                'status':'issued_with_scope','can_replay_as_new_message':False,
                'message':'原本地专业模型、A/B范围及候选可核对，不认定现场变化。来源登记后的新输入需实际接收后才可供应等待；历史不补发。'}
            result['graph_message_support']={'schema':'native-learned-geo-graph-receipt-support-v141',
                'status':'new_inputs_only','can_replay_as_new_message':False,
                'message':'归集、数据接收、消息接纳和等待匹配分别核对；当前或历史已接收不等于已向流程供应消息。'}
        elif pair_origin.has_issued(db,p,row):
            if result['data_received']:
                from .instance_data_receipts import observation_receipt
                result.update(observation_receipt(db,p,row))
            result['data_reception_support']={'schema':'native-geo-pair-receipt-support-v137',
                'status':'issued_with_scope','can_replay_as_new_message':False,
                'message':'原双影像工程结果及业务范围可核对；仅保存候选，不认定现场变化，不供应等待消息。'}
        elif row.data.get('native_packet',{}).get('source_key','').startswith('GeoPair:'):
            result['data_reception_support']={'schema':'native-geo-pair-receipt-support-v137',
                'status':'legacy_without_scope','can_replay_as_new_message':False,
                'message':'旧比较没有本版原执行范围锚，原件和原包保留；不能补签为现代数据接收回执。'}
        if row.data['native_packet']['source_key'].startswith('AudioRun:'):
            from .native_audio_provenance_v136 import has_issued
            issued=has_issued(db,p,row)
            if issued and result['data_received']:
                from .instance_data_receipts import observation_receipt
                result.update(observation_receipt(db,p,row))
            result['graph_message_support']={'schema':'native-audio-receipt-support-v136',
                'status':'issued_with_scope' if issued else 'legacy_without_scope',
                'scope_anchor':'actual_workflow_input_audit_v136' if issued else None,
                'can_replay_as_new_message':False,
                'message':('原任务范围和原生回执可核对；接纳状态请查看独立消息回执，原记录不会补发。' if issued else
                    '旧处理没有本版实际执行范围锚，原件和原包保留；不能补签或补发为新的等待消息。')}
    except (HTTPException,KeyError,TypeError,ValueError,OSError):
        result['data_received']=False;result['record']=None;result.pop('data_reception',None)
        result['integrity_error']='原处理、文件或对象关联需要核对，暂不显示机器值；原记录保留。'
    return result

def reconcile(db,p,identity):
    rows=latest(list(db.scalars(select(m.Observation).where(m.Observation.workspace_id==p.workspace_id,m.Observation.data['native_instance_id'].as_string()==identity).order_by(m.Observation.created_at.desc(),m.Observation.id))))
    groups={}
    from .native_pixel_receipts_v250 import handles as pixel_receipt
    from .native_sound_receipts import handles as sound_receipt
    for row in rows:
        if pixel_receipt(db,p,row) or sound_receipt(db,p,row):continue  # Registration-day typed results have their own daily accounting.
        groups.setdefault(row.business_day,[]).append(view(db,p,row))
    for day,values in groups.items():
        value={'schema':'native-multimodal-daily-v1','instance_id':identity,'business_day':day,'records':values,'not_authoritative':True,'date_basis':'original_source_request'}
        key='native-daily:'+identity+':'+day+':'+digest(value)
        old=db.scalar(select(m.Memory).where(m.Memory.workspace_id==p.workspace_id,m.Memory.data['schema'].as_string()=='native-multimodal-daily-v1',m.Memory.data['instance_id'].as_string()==identity,m.Memory.data['business_day'].as_string()==day).order_by(m.Memory.created_at.desc()).limit(1))
        if old:
            try:
                from .native_daily_display_compat_v137 import reuse_saved_daily
                reused=reuse_saved_daily(db,p,old,value)
                if reused is not None:value,key=reused['value'],reused['key']
            except HTTPException:key+=':repair:'+old.id
        enqueue_event(db,p,'perception.daily',key,value)

def checked_daily(db,p,memory):
    event=scoped(db,m.Event,memory.data.get('event_id'),p)
    value={k:v for k,v in memory.data.items() if k not in {'event_id','type'}}
    expected='native-daily:'+value['instance_id']+':'+value['business_day']+':'+digest(value)
    if event.kind!='perception.daily' or event.status!='received' or event.data!=value or memory.data.get('type')!='daily_summary' or not (event.idempotency_key==expected or event.idempotency_key.startswith(expected+':repair:')):
        raise HTTPException(409,'每日回执与原交付不一致，请重新归集；原记录保留')
    return value

def latest(rows):
    seen=set();result=[]
    for row in rows:
        key=(row.source_id,row.source_record_id)
        if key in seen:continue
        seen.add(key);result.append(row)
    return result

def schedule(workspace_id=None):
    from .db import engine
    from .security import Principal
    from .worker import deliver_events
    from sqlalchemy.orm import Session
    with Session(engine()) as db:
        query=select(m.Observation.workspace_id,m.Observation.data['native_instance_id'].as_string()).where(m.Observation.data['native_instance_id'].as_string().is_not(None))
        if workspace_id is not None:query=query.where(m.Observation.workspace_id==workspace_id)
        groups=db.execute(query.distinct().limit(100)).all()
    for workspace,identity in groups:
        with Session(engine()) as db:
            p=Principal('native-data-receiver',workspace,'viewer','native-data-receiver');reconcile(db,p,identity);db.commit()
        deliver_events(workspace_id=workspace)

def enqueue_task(db,p,job):
    task=scoped(db,m.Task,job.data['task_id'],p)
    if not any(r.get('candidate_only') for r in task.data.get('workflow_runtime',{}).get('receipts',[])):return
    key='native-sync:'+job.id
    if db.scalar(select(m.Job.id).where(m.Job.workspace_id==p.workspace_id,m.Job.idempotency_key==key)):return
    db.add(m.Job(workspace_id=p.workspace_id,kind='sync_native_receipts',idempotency_key=key,
        data={'instance_id':task.data['instance_id'],'entity_ids':task.data['entity_ids'],'actor_id':p.user_id,'original_task_id':task.id}))

def enqueue_video(db,p,job):
    row=scoped(db,m.VideoRun,job.data['run_id'],p)
    if row.status!='succeeded':return
    context=row.data.get('source_context')
    # Ordinary uploads explicitly save None. They have no generated-business
    # destination and still need their original processing Job to finalize.
    if context is None:return
    if type(context) is not dict:raise HTTPException(409,'视频原来源上下文格式无法核对，未安排业务归集')
    binding=context.get('domain_binding')
    if binding is None:return
    # A genuine legacy scene binding has no generated instance. Do not
    # misclassify an incomplete modern binding as legacy: require the exact
    # original capture session and saved frame. Later binding/entity changes
    # do not invalidate the already saved video or authorize new delivery.
    legacy_fields={'binding_id','binding_generation','entity_id','entity_version','source_id',
        'package_id','package_hash','entity_label','association','not_physical_identity','channel_generation'}
    if type(binding) is dict and set(binding)==legacy_fields:
        frame=db.scalar(select(m.VideoFrame).where(m.VideoFrame.workspace_id==p.workspace_id,
            m.VideoFrame.run_id==row.id).order_by(m.VideoFrame.sample_index).limit(1))
        if frame is None or frame.data.get('source_context')!=context:
            raise HTTPException(409,'旧视频场景关联与原画面无法核对，未安排业务归集')
        session=scoped(db,m.StreamSession,context.get('session_id'),p)
        if (session.video_run_id!=row.id or session.evidence_id!=row.evidence_id
            or session.channel_id!=context.get('channel_id') or session.data.get('domain_binding')!=binding
            or session.data.get('channel_generation')!=binding.get('channel_generation')):
            raise HTTPException(409,'旧视频场景关联与原读取回执不一致，未安排业务归集')
        return
    if (type(binding) is not dict or any(type(binding.get(key)) is not str or not binding[key].strip()
            for key in ('instance_id','entity_id'))):
        raise HTTPException(409,'视频原业务绑定格式无法核对，未安排业务归集')
    key='native-sync:'+job.id
    if db.scalar(select(m.Job.id).where(m.Job.workspace_id==p.workspace_id,m.Job.idempotency_key==key)):return
    db.add(m.Job(workspace_id=p.workspace_id,kind='sync_native_receipts',idempotency_key=key,
        data={'instance_id':binding['instance_id'],'entity_ids':[binding['entity_id']],'actor_id':p.user_id,'original_video_run_id':row.id}))
