"""Advice chooses pinned local workflows; it never supplies tool parameters."""
import copy
from fastapi import HTTPException
from .contracts import digest
from .models import Task,SignalAdvice,PerceptionSignal
from .services import scoped,published
from .evidence_assessment import ref

LOCAL={'builtin','local_specialist','local_geospatial','local_geo_pair','local_learned_geo_pair','local_audio','local_environment_sound','local_task_segmented_audio'}
MEDIA={'task_image_ids':('image_ids','image_snapshots'), 'task_scene_refs':('scene_ids','scene_snapshots'),
       'task_comparison_refs':('comparison_scene_ids','comparison_scene_snapshots'),'task_audio_ref':('audio_ids','audio_snapshots'),'task_sound_ref':('sound_ids','sound_snapshots')}

def source_task(db,p,signal):
    if not (signal.data.get('basis',{}).get('original_model_result') or {}).get('task_id'):return None
    from .native_signal_reviews import origin
    task,_,_,_,_=origin(db,p,signal)
    from .workspace_api import verify_perception_evidence
    verify_perception_evidence(db,p,task)
    return task

def progress(task,business):
    if not task:return None
    runtime=task.data['workflow_runtime'];steps=business['flow']['steps']
    current=next((s['name'] for s in steps if s['id']==runtime['current_step_id']),None)
    return {'title':task.title,'status':task.status,'current_step':current,
        'completed_steps':[{'name':r['step_name'],'role':r['role'],'note':r['note'],'at':r['at'],
            'execution_mode':r.get('execution_mode','human_review')} for r in runtime['receipts']],
        'original_progress_must_not_be_overwritten':True}

def catalog(db,p,instance,release,signal):
    from .workflow_abilities import eligible,pin,ROLES
    original=source_task(db,p,signal);workflows=[];blocked=[]
    for business in release.data['snapshot']['businesses']:
        if not business.get('available') or business['id'] not in instance.data.get('enabled_business_ids',[]):continue
        if business.get('execution_mode')!='registered_ability_workflow':continue
        automatic=[s for s in business['flow']['steps'] if s['agent']!='人工确认']
        if not automatic:continue
        references=[];media=set();issue=None
        try:
            for step in automatic:
                ex=step['execution'];cap=published(db,p,ex['capability_id']);eligible(cap,ROLES[step['agent']])
                if ex['adapter'] not in LOCAL or cap.data['execution'].get('cloud') or cap.data['execution'].get('cloud_allowed'):
                    raise HTTPException(409,'此路由仅运行本地能力；外部调用需要独立范围预览')
                if ex['capability_hash']!=cap.content_hash or pin(db,p,step,release.data['snapshot']['automation']['policies'][business['name']])!=ex:
                    raise HTTPException(409,'能力或执行授权已变化')
                references.append(ref(cap));sources={v['source'] for v in ex.get('bindings',{}).values()}
                if 'perception_frame_ids' in sources:raise HTTPException(409,'此路由尚未绑定视频帧输入，不能伪造')
                media.update(sources&set(MEDIA))
            inputs={};source_refs=[]
            if media:
                if not original:raise HTTPException(409,'缺少关联原分析任务的固定媒体输入')
                for source in sorted(media):
                    field,key=MEDIA[source];saved=original.data.get(key,[])
                    if not saved:raise HTTPException(409,'原任务没有此节点必需的输入')
                    ids=[v['id'] for v in saved]
                    if source=='task_image_ids':from .workflow_images import snapshots
                    elif source=='task_audio_ref':from .workflow_audio import snapshots
                    elif source=='task_sound_ref':from .workflow_sound import snapshots
                    else:from .workflow_scenes import snapshots
                    current=snapshots(db,p,instance.id,ids)
                    if current!=saved:raise HTTPException(409,'原任务媒体或固定设置已改变')
                    inputs[field]=ids;source_refs.extend(copy.deepcopy(saved))
                if 'task_comparison_refs' in media:
                    from .workflow_geo_pairs import validate_pair
                    validate_pair(source_refs if len(media)==1 else original.data['comparison_scene_snapshots'],original.data.get('comparison_confirmed',False))
                    inputs['comparison_confirmed']=True
            selection={'id':'original_media' if media else 'object_records','label':'原分析任务已固定的媒体（会重新在本地处理）' if media else '本线索对象当前登记资料',
                'inputs':inputs,'media_names':[s.get('snapshot',{}).get('name',s.get('name','原任务固定原件')) for s in source_refs],'source_hash':digest(source_refs),'source_task_ref':ref(original) if media else None}
            workflows.append({'business_id':business['id'],'name':business['name'],'input_sets':[selection],
                'automatic_steps':[{'name':s['name'],'agent':s['agent'],'mode':s['execution']['mode'],'adapter':s['execution']['adapter']} for s in automatic],
                'capability_refs':references,'new_local_processing':True})
        except (HTTPException,KeyError,TypeError,ValueError) as error:issue=str(getattr(error,'detail',error))
        if issue:blocked.append({'business_id':business['id'],'name':business['name'],'reason':issue})
        # Unavailable workflows are absent from executable directory; configured flows
        # still appear in general context with explicit limitations.
    return workflows,original,blocked

def choice(action,request):
    flow=next((b for b in request.get('workflow_catalog',[]) if b['business_id']==action['business_id']),None)
    selection=next((s for s in (flow or {}).get('input_sets',[]) if s['id']==action.get('input_set')),None)
    if not flow or not selection:raise HTTPException(409,'只能安排本次预览内已确认的本地流程和固定输入')
    return flow,selection

def packet(db,p,body,instance,release):
    from .signal_advice import checked,current
    row=checked(scoped(db,SignalAdvice,body.advice_id,p,True))
    if row.status not in {'applying','applied'} or not row.data.get('confirmation'):raise HTTPException(409,'业务建议尚未明确批准，不能发起流程')
    if row.owner_id!=p.user_id and p.role!='admin':raise HTTPException(403,'请由发起人或管理员确认处置')
    expected=row.data.get('workflow_request')
    if not expected or digest(body.model_dump())!=digest(expected):raise HTTPException(409,'不能更换已批准流程、对象、原件或请求')
    current(db,p,row);request=row.data['request'];choice(row.data['approved'],request)
    if row.instance_id!=instance.id or request['release_id']!=release.id or body.entity_ids!=[request['entity_id']]:raise HTTPException(409,'批准范围与流程对象或版本不一致')
    value={'advice_id':row.id,'request_hash':row.data['request_hash'],'approved_hash':row.data['approved']['hash'],
        'workflow_request_hash':digest(expected),'signal_id':row.signal_id,'basis_hash':request['basis_hash'],
        'purpose':row.data['approved']['purpose'],'signal_note':request['cloud_input']['references'][0]['text']}
    return {**value,'hash':digest(value)}

def verify(db,p,task,*,lock_sources=True):
    value=task.data.get('business_advice')
    if not value:return
    if digest({k:v for k,v in value.items() if k!='hash'})!=value.get('hash'):raise HTTPException(409,'业务建议来源快照不一致，请保留原任务核对')
    from .signal_advice import checked
    from .perception_signals import checked as signal_checked
    row=checked(scoped(db,SignalAdvice,value['advice_id'],p));signal=scoped(db,PerceptionSignal,value['signal_id'],p)
    signal_checked(db,p,signal,allow_rejected=False,lock_sources=lock_sources)
    if row.data['request_hash']!=value['request_hash'] or row.data['approved']['hash']!=value['approved_hash'] or digest(row.data['workflow_request'])!=value['workflow_request_hash'] or signal.data['basis_hash']!=value['basis_hash']:
        raise HTTPException(409,'原处置授权或线索依据发生变化，处理暂停，旧结果保留')
    expected=row.data['workflow_request']
    from .workspace_api import NewTask,creation_body_hash
    body=NewTask.model_validate(expected)
    if task.data.get('creation_hash')!=creation_body_hash(body):
        raise HTTPException(409,'处置Task发起依据与原批准体不一致')
    for source,(field,snapshot_key) in MEDIA.items():
        if getattr(body,field)!=[s['id'] for s in task.data.get(snapshot_key,[])]:
            raise HTTPException(409,'处置Task媒体与已批准原件不一致')
    if body.comparison_confirmed!=task.data.get('comparison_confirmed',False):
        raise HTTPException(409,'处置Task影像比较确认与原批准不一致')
    if expected['request_id']!=task.data['creation_request_id'] or expected['business_id']!=task.data['business_id'] or expected['entity_ids']!=task.data['entity_ids'] or expected['release_id']!=task.data['instance_release_id']:
        raise HTTPException(409,'处置Task与已批准范围不一致')
