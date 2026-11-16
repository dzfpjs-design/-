"""Task-bound local RGB comparison; explicit A/B, fixed parameters and saved candidates."""
import copy
from datetime import datetime
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select,text
from .contracts import Manifest,digest
from .models import GeoPair,Capability,Task,Job
from .services import scoped,require,audit,published,register,validate_capability,publish
from .geo_pair_codec import V2,ready
from .workflow_scenes import snapshots,checked_input
from .geo_pairs import checked,artifact_bytes,KINDS


def manifest(threshold,alignment):
    if type(threshold) is not int or not 1<=threshold<=255 or alignment not in {'same_grid','reproject_after_to_before'}:raise HTTPException(422,'双影像能力阈值或对齐方式无效')
    config={'threshold':threshold,'alignment':alignment};label=digest(config)[:16]
    return Manifest(kind='tool',namespace='local_geo_pair',name='rgb_difference_'+label,version='0.1.0',owner='本地双影像处理服务',
        description=f'本地双影像像素差异（阈值{threshold}，'+('原网格一致' if alignment=='same_grid' else 'B重投影到A')+'）。仅输出候选，未知区域独立保留；不是深度变化检测或业务异常结论。',
        allowed_agent_roles=['perception','data'],input_schema={'type':'object','required':['scene_refs','task_id'],'additionalProperties':False,'properties':{
            'scene_refs':{'title':'本任务A/B影像及固定RGB/范围','type':'array','minItems':2,'maxItems':2,'description':'第一项为A参考影像，第二项为B；均为任务明确选择的固定实例输入','items':{'type':'object','required':['input_scene_id','snapshot_hash'],'additionalProperties':False,'properties':{'input_scene_id':{'type':'string'},'snapshot_hash':{'type':'string','pattern':'^[a-f0-9]{64}$'}}}},'task_id':{'title':'当前业务任务','type':'string'}}},
        output_schema={'type':'object','required':['attributes','anomalies','spatial_comparison','geo_pair_id'],'additionalProperties':False,'properties':{'attributes':{'type':'object'},'anomalies':{'type':'array','maxItems':0},'spatial_comparison':{'type':'object'},'geo_pair_id':{'type':'string'}}},
        execution={'adapter':'local_geo_pair','runtime_hash':digest(V2),'recipe_id':V2['id'],'threshold':threshold,'alignment':alignment,'side_effect':False,'input_bindings':{'scene_refs':'task_comparison_refs','task_id':'task_identifier'}},
        data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False},
        specification={'learned_model':False,'algorithm':'rgb_max_channel_absolute_difference','domain_evaluation':'pending','automatic_domain_use':False,'comparison_input_order':['A','B'],'registration_accuracy':'not_verified'})


def validate_manifest(value):
    execution=value.execution;ready(V2['id'])
    if value.model_dump()!=manifest(execution.get('threshold'),execution.get('alignment')).model_dump():raise HTTPException(422,'双影像工具必须使用固定本地候选契约，不能扩大权限或修改输入来源')


def register_from_pair(db,p,row):
    require(p,'admin');checked(db,p,row)
    for kind in KINDS:artifact_bytes(db,p,row,kind)
    value=manifest(row.data['request']['threshold'],row.data['request']['alignment']);validate_manifest(value)
    lock=int(digest({'workspace':p.workspace_id,'pair_ability':value.name})[:16],16)-(1<<63)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':lock})
    cap=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.namespace==value.namespace,Capability.name==value.name,Capability.release_version==value.version))
    if cap:
        if cap.data!=value.model_dump() or cap.status!='published':raise HTTPException(409,'已有同名能力未就绪或契约不一致，请核对登记记录')
    else:
        cap=register(db,p,value);validate_capability(db,p,cap.id,cap.version);publish(db,p,cap.id,cap.version,'本地RGB差异候选；不授予外发、设备控制、对象状态写入或经验发布权限')
    audit(db,p,'geo.pair_ability_registered',cap.id,{'reference_pair_id':row.id,'runtime_hash':digest(V2),'learned_model':False});db.commit()
    return {'capability_id':cap.id,'runtime_hash':digest(V2),'threshold':value.execution['threshold'],'alignment':value.execution['alignment'],'kind':'tool','learned_model':False}


def refs(task):return [{'input_scene_id':x['id'],'snapshot_hash':x['snapshot_hash']} for x in task.data.get('comparison_scene_snapshots',[])]


def verify_inputs(db,p,task):
    saved=task.data.get('comparison_scene_snapshots',[])
    if saved and snapshots(db,p,task.data['instance_id'],[x['id'] for x in saved])!=saved:raise HTTPException(409,'业务双影像输入与已保存原件/范围不一致，不能替换旧依据')


def validate_pair(sources,ack):
    if len(sources)!=2 or ack is not True:raise HTTPException(422,'请分别选择A/B影像，并确认RGB含义和范围可以比较')
    a,b=[x['snapshot'] for x in sources]
    from .geo_quality_v351 import validate_pair as validate_quality_pair
    validate_quality_pair(a,b)
    if a['scene_id']==b['scene_id'] or a['input_sha256']==b['input_sha256']:raise HTTPException(422,'A/B原件相同，请选择需要对照的不同影像')
    for s in (a,b):
        if s['runtime_hash']!=V2['base_recipe_hash'] or not s['configuration'].get('map_image_confirmed') or s['metadata']['georeference_status']!='affine_available':raise HTTPException(422,'两幅业务输入均需确认本地地图参考与当前地理配方')
    at=a['configuration'].get('observed_at');bt=b['configuration'].get('observed_at')
    if at and bt and datetime.fromisoformat(bt)<=datetime.fromisoformat(at):raise HTTPException(422,'已填写日期时，B采集时间应晚于A；请核对顺序')


def local_inputs(db,p,task,step,inputs):
    if step['execution']['adapter']!='local_geo_pair':return
    verify_inputs(db,p,task);validate_pair(task.data.get('comparison_scene_snapshots',[]),task.data.get('comparison_confirmed'))
    if inputs.get('scene_refs')!=refs(task) or inputs.get('task_id')!=task.id:raise HTTPException(409,'双影像工具必须使用此任务明确选择的A/B固定输入，不能传入其他样例')


def normalize(source):
    s=source['snapshot']
    return copy.deepcopy({'scene_id':s['scene_id'],'scene_generation':s['scene_generation'],'name':s['name'],'evidence_id':s['original_evidence_id'],'sha256':s['input_sha256'],'metadata':s['metadata'],'configuration':s['configuration'],'runtime_hash':s['runtime_hash'],'input_scene_id':source['id'],'input_snapshot_hash':source['snapshot_hash'],
        **({'quality_origin_v351':s['quality_origin_v351']} if s.get('quality_origin_v351') else {})})


def execute(db,p,cap,inputs,key):
    from .leases import ownership
    from .geo_pairs import process
    require(p,'admin','operator');validate_manifest(Manifest.model_validate(cap.data));task=scoped(db,Task,inputs['task_id'],p)
    lease=ownership(db);job=db.get(Job,lease.get('job_id'))
    from .workflow_graph_scope import owned_graph_job,graph_context,metadata,record_node
    graph_job=owned_graph_job(db,p);graph_meta={}
    if graph_job:
        task,job,node,activation_scope=graph_context(db,p,key,cap)
        if inputs!=job.data['inputs']:raise HTTPException(409,'图双影像输入与冻结节点不一致')
        graph_meta=metadata(task,job,node,activation_scope,cap);logical_scope=activation_scope
    else:
        if not job or job.kind!='execute_workflow_step' or job.data.get('task_id')!=task.id or task.status!='workflow_running':raise HTTPException(409,'双影像比较仅在当前已授权业务节点的租约中执行')
        logical_scope=job.data['scope']
    local_inputs(db,p,task,{'execution':cap.data['execution']},inputs)
    scope=digest({'scope':logical_scope,'capability_hash':cap.content_hash,'inputs':inputs});row=db.scalar(select(GeoPair).where(GeoPair.workspace_id==p.workspace_id,GeoPair.data['workflow_scope'].as_string()==scope).with_for_update())
    if row and graph_job:record_node(db,p,task,row)
    if row and row.status=='succeeded':verify_pair_result(db,p,task,row)
    else:
        created=row is None
        if row:
            row.status='queued';row.version+=1;row.data={**row.data,'generation':row.data['generation']+1,'workflow_job_id':job.id,'job_id':job.id,'error':None}
        else:
            sources=[normalize(s) for s in task.data['comparison_scene_snapshots']];a,b=task.data['comparison_scene_snapshots'];execution=cap.data['execution']
            at=sources[0]['configuration'].get('observed_at');bt=sources[1]['configuration'].get('observed_at')
            row=GeoPair(workspace_id=p.workspace_id,before_scene_id=sources[0]['scene_id'],after_scene_id=sources[1]['scene_id'],data={
                'request_id':key,'request_hash':digest(inputs),'request':{'roi':a['snapshot']['roi'],'threshold':execution['threshold'],'alignment':execution['alignment']},
                'sources':sources,'sources_hash':digest(sources),'runtime_hash':digest(V2),'after_allowed_window':b['snapshot']['roi'],
                'workflow_scope':scope,'workflow_task_id':task.id,'workflow_job_id':job.id,'input_refs':copy.deepcopy(inputs['scene_refs']),
                'chronology':'user_declared_ordered_times' if at and bt else 'unknown_dates_A_B_comparison','generation':1,'job_id':job.id,'result':None,'result_hash':None,'error':None,'review':None,'review_history':[],**graph_meta})
            db.add(row);db.flush()
        from .native_geo_pair_provenance_v137 import record_input
        record_input(db,p,row,task,job,inputs,cap,created=created)
        identity=row.id;generation=row.data['generation'];db.commit();process(db,p,SimpleNamespace(data={'pair_id':identity,'generation':generation}));row=scoped(db,GeoPair,identity,p)
        verify_pair_result(db,p,task,row)
    result=copy.deepcopy(row.data['result'])
    return {'geo_pair_id':row.id,'attributes':{'pixel_difference_candidate_pixels':result['candidate_pixels'],'common_valid_pixels':result['common_valid_pixels'],'unknown_pixels':result['unknown_pixels'],'not_authoritative':True},'anomalies':[],'spatial_comparison':result}


def verify_pair_result(db,p,task,row):
    if 'graph_runtime' in task.data or row.data.get('workflow_graph_scope'):
        from .workflow_graph_scope import record_node
        record_node(db,p,task,row)
    if row.data.get('request_kind')=='learned_binary_change':
        from .workflow_learned_geo_pairs import verify_pair_result as verify_learned
        return verify_learned(db,p,task,row)
    checked(db,p,row);verify_inputs(db,p,task)
    if row.data.get('workflow_task_id')!=task.id or row.data.get('input_refs')!=refs(task) or row.data['sources']!=[normalize(s) for s in task.data['comparison_scene_snapshots']] or row.data['request']['roi']!=task.data['comparison_scene_snapshots'][0]['snapshot']['roi'] or row.data['after_allowed_window']!=task.data['comparison_scene_snapshots'][1]['snapshot']['roi']:
        raise HTTPException(409,'双影像候选不属于当前任务、A/B次序、范围或原件版本')
    for kind in KINDS:artifact_bytes(db,p,row,kind)


def local_result(db,p,task,step,inputs,output):
    if step['execution']['adapter']!='local_geo_pair':return
    row=scoped(db,GeoPair,output['geo_pair_id'],p);verify_pair_result(db,p,task,row)
    if 'graph_runtime' in task.data:
        from .workflow_graph_scope import record_node
        record_node(db,p,task,row,step)
    if output['spatial_comparison']!=row.data['result'] or output.get('anomalies'):raise HTTPException(409,'双影像结果与保存证据不一致；不接受自动业务异常结论')


def verify_receipts(db,p,task):
    verify_inputs(db,p,task)
    for receipt in task.data['workflow_runtime'].get('receipts',[]):
        if receipt.get('output',{}).get('spatial_comparison') is not None:
            row=scoped(db,GeoPair,receipt['output']['geo_pair_id'],p);verify_pair_result(db,p,task,row)
            if receipt['output']['spatial_comparison']!=row.data['result']:raise HTTPException(409,'业务比较回执与已保存结果不一致')
