"""Explicit, immutable instance raster inputs and task-scoped local spatial capability."""
import copy
from datetime import UTC
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import Manifest,digest
from .models import WorkflowScene,GeoScene,GeoRun,Task,Job
from .services import scoped,require,published,audit
from .geo_storage import path_for
from .geo_codec import RECIPE,RECIPE_HASH
from .local_specialists import ready_install,SAM_RECIPE


def view(row):return {'id':row.id,'version':row.version,'scene_id':row.scene_id,'instance_id':row.instance_id,'snapshot':row.data['snapshot'],'snapshot_hash':row.data['snapshot_hash'],'created_at':row.created_at.astimezone(UTC).isoformat()}


def checked_input(db,p,identity):
    row=scoped(db,WorkflowScene,identity,p);snapshot=row.data['snapshot'];ev,_=path_for(db,p,snapshot['original_evidence_id'])
    if digest(snapshot)!=row.data['snapshot_hash'] or ev.sha256!=snapshot['input_sha256']:raise HTTPException(409,'业务影像输入与固定原件或设置摘要不一致，请恢复原件；记录保留')
    scene=scoped(db,GeoScene,row.scene_id,p)
    if snapshot.get('quality_origin_v351') or scene.data.get('quality_origin_v351'):
        from .geo_quality_v351 import checked_scene
        if checked_scene(db,p,scene)!=snapshot.get('quality_origin_v351'):
            raise HTTPException(409,'业务影像的质量掩膜来源与固定依据不一致')
    if snapshot.get('wms_origin') or scene.data.get('wms_origin'):
        from .wms import source_provenance
        source_provenance(db,p,scene)
        if snapshot.get('wms_origin')!=scene.data.get('wms_origin'):raise HTTPException(409,'业务地图输入与固定WMS来源不一致')
    return row


def capture(scene,roi,quality=None):
    meta=scene.data['metadata'];roi=list(roi or (0,0,meta['width'],meta['height']))
    if not 0<=roi[0]<roi[2]<=meta['width'] or not 0<=roi[1]<roi[3]<=meta['height']:raise HTTPException(422,'业务分析范围超出原影像像素边界')
    if (roi[2]-roi[0])*(roi[3]-roi[1])>RECIPE['max_inference_pixels']:raise HTTPException(422,'本次业务影像范围超过1600万像素，请明确选择较小区域')
    return copy.deepcopy({'name':scene.data['name'],'scene_id':scene.id,'scene_generation':scene.data['generation'],'original_evidence_id':scene.evidence_id,
        'input_sha256':scene.data['input_sha256'],'size_bytes':scene.data['size_bytes'],'metadata':meta,'configuration':scene.data['configuration'],'roi':roi,'runtime_hash':scene.data['runtime_hash'],'preview':scene.data['preview'],
        **({'wms_origin':scene.data['wms_origin']} if scene.data.get('wms_origin') else {}),
        **({'quality_origin_v351':quality if quality is not None else scene.data['quality_origin_v351']} if scene.data.get('quality_origin_v351') else {})})


def snapshots(db,p,instance_id,ids):
    if len(ids)!=len(set(ids)):raise HTTPException(422,'不能重复选择同一业务影像输入')
    values=[]
    for identity in ids:
        row=checked_input(db,p,identity)
        if row.instance_id!=instance_id:raise HTTPException(409,'影像输入属于其他业务实例，请先明确关联到当前实例')
        values.append(view(row))
    return values


def verify(db,p,task):
    saved=task.data.get('scene_snapshots',[])
    if snapshots(db,p,task.data['instance_id'],[x['id'] for x in saved])!=saved:raise HTTPException(409,'任务空间输入与已保存版本不一致，不能替换旧任务依据')


def refs(task):return [{'input_scene_id':x['id'],'snapshot_hash':x['snapshot_hash']} for x in task.data.get('scene_snapshots',[])]


def local_inputs(db,p,task,step,inputs):
    if step['execution']['adapter']!='local_geospatial':return
    verify(db,p,task)
    if len(refs(task))!=1 or inputs.get('scene_refs')!=refs(task) or inputs.get('task_id')!=task.id:raise HTTPException(409,'此空间分割节点需一项明确选择的业务影像与固定设置，请拆分任务或核对任务输入')


def spatial_manifest(installation):
    from .config import settings
    if installation.pack_id!='sam2-1-tiny' or not settings().geo_python.is_file():raise HTTPException(409,'请先安装本地SAM和固定地理运行环境')
    return Manifest(kind='specialist_model',namespace='platform_local',name='sam2_geo_candidates',version=f'1.{installation.data["generation"]}.{int(RECIPE_HASH[:12],16)}',owner='平台本地空间模型包',
        description='按业务选定GeoTIFF及确认的RGB/范围提取通用区域候选，保留原坐标边界、像素/测地面积和原件。地面精度与语义未验证，不认定业务异常。',
        allowed_agent_roles=['perception','data'],input_schema={'type':'object','required':['scene_refs','task_id'],'additionalProperties':False,'properties':{
            'scene_refs':{'title':'本任务选定影像及固定设置','type':'array','minItems':1,'maxItems':1,'items':{'type':'object','required':['input_scene_id','snapshot_hash'],'additionalProperties':False,'properties':{'input_scene_id':{'type':'string'},'snapshot_hash':{'type':'string','pattern':'^[a-f0-9]{64}$'}}}},'task_id':{'title':'当前业务任务','type':'string'}}},
        output_schema={'type':'object','required':['attributes','anomalies','spatial_segmentation','geo_run_id'],'additionalProperties':False,'properties':{'attributes':{'type':'object'},'anomalies':{'type':'array','maxItems':0},'spatial_segmentation':{'type':'object'},'geo_run_id':{'type':'string'}}},
        execution={'adapter':'local_geospatial','installation_id':installation.id,'generation':installation.data['generation'],'recipe_hash':digest(SAM_RECIPE),'runtime_hash':RECIPE_HASH,'backend':installation.data['backend'],'side_effect':False,
            'input_bindings':{'scene_refs':'task_scene_refs','task_id':'task_identifier'}},data_policy={'cloud_allowed':False,'external_allowed':False,'deployment':'local'},
        specification={'pack_id':'sam2-1-tiny','domain_evaluation':'pending','automatic_domain_use':False,'license':SAM_RECIPE['license'],'license_evidence':SAM_RECIPE['license_evidence']})


def execute(db,p,cap,inputs,key):
    """Run within the owned workflow lease; no child job waiting on the same worker."""
    from .leases import ownership
    from .geo_inference import process_run
    from .geo_api import checked_run
    execution=cap.data['execution'];installation=ready_install(db,p,execution['installation_id'])
    if cap.data!=spatial_manifest(installation).model_dump():raise HTTPException(409,'空间模型契约或地理配方变化，请重新登记后核对业务配置')
    require(p,'admin','operator');task=scoped(db,Task,inputs['task_id'],p);lease=ownership(db);job=db.get(Job,lease.get('job_id'))
    from .workflow_graph_scope import owned_graph_job,graph_context,metadata,record_node
    graph_job=owned_graph_job(db,p);graph_meta={}
    if graph_job:
        task,job,node,activation_scope=graph_context(db,p,key,cap)
        if inputs!=job.data['inputs']:raise HTTPException(409,'图空间输入与冻结节点不一致')
        graph_meta=metadata(task,job,node,activation_scope,cap);logical_scope=activation_scope
    else:
        if not job or job.kind!='execute_workflow_step' or job.data.get('task_id')!=task.id or task.status!='workflow_running':raise HTTPException(409,'空间模型需要当前已授权业务节点的执行上下文，不接受临时任务或直接外部调用')
        logical_scope=job.data['scope']
    if inputs['scene_refs']!=refs(task):raise HTTPException(409,'空间模型输入不是当前任务的固定影像')
    verify(db,p,task);source=checked_input(db,p,inputs['scene_refs'][0]['input_scene_id']);snapshot=source.data['snapshot']
    if snapshot['runtime_hash']!=RECIPE_HASH:raise HTTPException(409,'业务空间输入使用了旧地理配方，请核对并重新关联影像，不覆盖原任务')
    scope=digest({'scope':logical_scope,'capability_hash':cap.content_hash,'inputs':inputs})
    row=db.scalar(select(GeoRun).where(GeoRun.workspace_id==p.workspace_id,GeoRun.data['workflow_scope'].as_string()==scope).with_for_update())
    if row and graph_job:record_node(db,p,task,row)
    if row and row.status=='succeeded':checked_run(db,p,row.id)
    else:
        if row:
            row.status='queued';row.version+=1;row.data={**row.data,'generation':row.data['generation']+1,'workflow_job_id':job.id,'job_id':job.id,'error':None}
        else:
            row=GeoRun(workspace_id=p.workspace_id,scene_id=source.scene_id,installation_id=installation.id,data={**copy.deepcopy(snapshot),
                'request_id':key,'request_hash':digest(inputs),'workflow_scope':scope,'workflow_task_id':task.id,'workflow_job_id':job.id,'input_scene_id':source.id,'input_scene_snapshot_hash':source.data['snapshot_hash'],
                'backend':installation.data['backend'],'installation_generation':installation.data['generation'],'sam_recipe_hash':digest(SAM_RECIPE),
                'generation':1,'stage':'requested','result':None,'error':None,'review':None,'review_history':[],'job_id':job.id,**graph_meta})
            db.add(row);db.flush()
        identity=row.id;generation=row.data['generation'];db.commit()
        process_run(db,p,SimpleNamespace(data={'run_id':identity,'generation':generation}))
        row=checked_run(db,p,identity)
    result=copy.deepcopy(row.data['result'])
    return {'geo_run_id':row.id,'attributes':{'spatial_candidate_count':len(result['masks']),'source_time':snapshot['configuration']['observed_at'],'not_authoritative':True},'anomalies':[],'spatial_segmentation':result}


def local_result(db,p,task,step,inputs,output):
    if step['execution']['adapter']!='local_geospatial':return
    from .geo_api import checked_run
    row=checked_run(db,p,output['geo_run_id']);snapshot=task.data['scene_snapshots'][0]['snapshot']
    if 'graph_runtime' in task.data or row.data.get('workflow_graph_scope'):
        from .workflow_graph_scope import record_node
        record_node(db,p,task,row,step)
    if row.data.get('workflow_task_id')!=task.id or row.data.get('input_scene_id')!=inputs['scene_refs'][0]['input_scene_id'] or row.data.get('input_scene_snapshot_hash')!=inputs['scene_refs'][0]['snapshot_hash'] or row.data.get('input_sha256')!=snapshot['input_sha256'] or row.data['roi']!=snapshot['roi'] or row.data['configuration']!=snapshot['configuration'] or output['spatial_segmentation']!=row.data['result']:
        raise HTTPException(409,'空间结果不属于当前任务的原件、范围和固定RGB设置')
    verify_result_masks(db,p,row)


def verify_result_masks(db,p,row):
    from .segmentation import mask_bytes
    from .evidence import evidence_bytes
    cp=row.data['rgb_checkpoint'];rgb,_=evidence_bytes(db,p,cp['rgb_evidence_id']);valid,_=evidence_bytes(db,p,cp['valid_evidence_id'])
    if rgb.sha256!=cp['rgb_sha256'] or valid.sha256!=cp['valid_sha256'] or any(ev.data.get('run_id')!=row.id or ev.data.get('source_sha256')!=row.data['input_sha256'] or ev.data.get('roi')!=row.data['roi'] for ev in [rgb,valid]):raise HTTPException(409,'任务分析输入与检查点不一致')
    for mask in row.data['result']['masks']:mask_bytes(db,p,row.data['result'],mask['mask_evidence_id'],rgb.id)


def verify_masks(db,p,task):
    from .geo_api import checked_run
    verify(db,p,task)
    for receipt in task.data['workflow_runtime'].get('receipts',[]):
        if receipt.get('output',{}).get('spatial_segmentation') is not None:
            row=checked_run(db,p,receipt['output']['geo_run_id'])
            if 'graph_runtime' in task.data or row.data.get('workflow_graph_scope'):
                from .workflow_graph_scope import record_node
                record_node(db,p,task,row)
            if row.data.get('workflow_task_id')!=task.id or row.data['result']!=receipt['output']['spatial_segmentation']:raise HTTPException(409,'空间回执不属于当前任务或结果不一致')
            verify_result_masks(db,p,row)
