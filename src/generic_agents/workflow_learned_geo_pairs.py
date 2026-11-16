"""Pinned local learned candidates, bound to task-authorized A/B raster inputs."""
import copy
import math
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select,text
from .contracts import Manifest,digest
from .models import Capability,GeoPair,Task,Job,DomainInstance
from .services import scoped,require,audit,register,validate_capability,publish,published
from .local_changer_install import installed,trace,PACK,RECIPE_HASH as MODEL_HASH,PIPELINE_HASH,RECIPE as MODEL
from .learned_geo_pairs import KIND,ARTIFACTS,model_check
from .geo_pairs import checked,artifact_bytes,process
from .workflow_geo_pairs import normalize,refs,validate_pair,verify_inputs,manifest as rgb_manifest

ADAPTER='local_learned_geo_pair'


def manifest(model_trace,threshold,alignment):
    expected={'installation_id','pack_id','generation','recipe_hash','pipeline_hash','weights_sha256','backend'}
    if (not isinstance(model_trace,dict) or set(model_trace)!=expected
        or not isinstance(model_trace['installation_id'],str) or not 1<=len(model_trace['installation_id'])<=100
        or type(model_trace['generation']) is not int or model_trace['generation']<1
        or model_trace['pack_id']!=PACK or model_trace['recipe_hash']!=MODEL_HASH
        or model_trace['pipeline_hash']!=PIPELINE_HASH or model_trace['weights_sha256']!=MODEL['files'][0]['sha256']
        or model_trace['backend']!='cpu' or type(threshold) not in {int,float} or not math.isfinite(threshold)
        or not 0<threshold<1 or alignment not in {'same_grid','reproject_after_to_before'}):
        raise HTTPException(422,'学习型双影像能力需要固定本地模型版本、概率阈值及对齐方式')
    common=rgb_manifest(40,'same_grid');label=digest({'model':model_trace,'threshold':threshold,'alignment':alignment})[:20]
    return Manifest(kind='specialist_model',namespace=ADAPTER,name='change_candidates_'+label,version='1.0.0',owner='本地双影像专业模型',
        description='本地模型变化候选（概率阈值'+str(threshold)+'，'+('原网格一致' if alignment=='same_grid' else 'B重投影到A')+'）。固定A/B原件和各自授权范围；建筑变化预训练，业务质量未核验，不自动认定对象状态或业务异常。',
        allowed_agent_roles=['perception','data'],input_schema=copy.deepcopy(common.input_schema),output_schema=copy.deepcopy(common.output_schema),
        execution={'adapter':ADAPTER,'model_trace':copy.deepcopy(model_trace),'runtime_hash':PIPELINE_HASH,'probability_threshold':threshold,
                   'alignment':alignment,'side_effect':False,'input_bindings':{'scene_refs':'task_comparison_refs','task_id':'task_identifier'}},
        data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False},
        specification={'learned_model':True,'candidate_only':True,'domain_evaluation':'pending','production_license_verified':False,
                       'automatic_domain_use':False,'comparison_input_order':['A','B'],'adapter_contract':'task-local-changer-v1'})


def validate_manifest(db,p,value,live=True):
    execution=value.execution
    if value.model_dump()!=manifest(execution.get('model_trace'),execution.get('probability_threshold'),execution.get('alignment')).model_dump():
        raise HTTPException(422,'学习型双影像必须使用固定本地候选契约，不能更改输入来源或扩大外发权限')
    if live:
        current=installed(db,p,execution['model_trace']['installation_id'])
        if trace(current)!=execution['model_trace']:raise HTTPException(409,'原本地模型安装代次已变化，请明确登记新版本；旧结果保留')


def register_from_pair(db,p,row):
    require(p,'admin');checked(db,p,row)
    if row.data.get('request_kind')!=KIND:raise HTTPException(422,'请先完成学习型双影像分析，不使用颜色差异样例登记模型')
    model_check(db,p,row,verify=True)
    for kind in ARTIFACTS:artifact_bytes(db,p,row,kind)
    value=manifest(row.data['model_trace'],row.data['request']['probability_threshold'],row.data['request']['alignment'])
    validate_manifest(db,p,value)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'workspace':p.workspace_id,'learned_pair_ability':value.name})[:16],16)-(1<<63)})
    cap=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.namespace==ADAPTER,
        Capability.name==value.name,Capability.release_version==value.version))
    if cap:
        if cap.data!=value.model_dump() or cap.status!='published':raise HTTPException(409,'已有同名本地模型能力契约不一致或未就绪，请核对原登记')
        published(db,p,cap.id)
    else:
        cap=register(db,p,value);validate_capability(db,p,cap.id,cap.version)
        publish(db,p,cap.id,cap.version,'仅本地候选处理，不授予外发、设备控制、对象状态写入或经验发布权限；业务质量未核验')
    audit(db,p,'geo.learned_pair_ability_registered',cap.id,{'reference_pair_id':row.id,'model_trace_hash':row.data['model_trace_hash'],'cloud_calls':0})
    db.commit()
    return {'capability_id':cap.id,'runtime_hash':PIPELINE_HASH,'model_trace_hash':row.data['model_trace_hash'],
            'probability_threshold':value.execution['probability_threshold'],'alignment':value.execution['alignment'],'kind':'specialist_model','learned_model':True}


def validate_task(db,p,sources,ack,execution):
    validate_pair(sources,ack)
    a,b=[s['snapshot'] for s in sources];x0,y0,x1,y1=a['roi']
    if min(x1-x0,y1-y0)<32:raise HTTPException(422,'学习型变化分析每边至少32像素，请核对关联范围；不会自动扩大或缩放')
    if execution['alignment']=='same_grid' and any(a['metadata'][key]!=b['metadata'][key] for key in ('width','height','transform','crs_wkt')):
        raise HTTPException(422,'此模型能力要求A/B原始网格一致，请核对原件或明确登记允许重投影的能力')


def local_inputs(db,p,task,step,inputs):
    if step['execution']['adapter']!=ADAPTER:return
    cap=published(db,p,step['execution']['capability_id']);validate_manifest(db,p,Manifest.model_validate(cap.data))
    verify_inputs(db,p,task);validate_task(db,p,task.data.get('comparison_scene_snapshots',[]),task.data.get('comparison_confirmed'),cap.data['execution'])
    if inputs.get('scene_refs')!=refs(task) or inputs.get('task_id')!=task.id:
        raise HTTPException(409,'学习型模型只能读取此任务明确选定的实例A/B输入，不能替换为样例或其他来源')


def request_value(task,execution,request_id):
    return {'schema':'task-local-changer-request-v1','request_id':request_id,'task_id':task.id,'scene_refs':refs(task),
            'model_trace':copy.deepcopy(execution['model_trace']),'roi':list(task.data['comparison_scene_snapshots'][0]['snapshot']['roi']),
            'alignment':execution['alignment'],'probability_threshold':execution['probability_threshold'],'rgb_comparability_confirmed':True}


def guard_workflow(db,p,row):
    if not row.data.get('workflow_task_id'):return
    if row.data.get('workflow_graph_scope'):
        from .workflow_graph_scope import guard_record
        guard_record(db,p,row)
        return
    from .leases import ownership
    from .workspace_api import execution
    task,release,business=execution(db,p,row.data['workflow_task_id'])
    job_id=ownership(db).get('job_id');job=db.get(Job,job_id) if job_id else None
    automatic=task.data['workflow_runtime'].get('automatic',{})
    instance=scoped(db,DomainInstance,task.data['instance_id'],p)
    step=next((s for s in business['flow']['steps'] if s['id']==row.data['workflow_step_id']),None)
    if (not job or job.kind!='execute_workflow_step' or job.data.get('task_id')!=task.id or job.id!=row.data['workflow_job_id']
        or job.workspace_id!=p.workspace_id or not step or step['execution'].get('capability_id')!=row.data['workflow_capability_id']
        or step['execution'].get('capability_hash')!=row.data['workflow_capability_hash']
        or task.status!='workflow_running' or automatic.get('job_id')!=job.id or automatic.get('step_id')!=row.data['workflow_step_id']
        or instance.status!='partially_active' or instance.active_release_id!=release.id
        or business['id'] not in instance.data.get('enabled_business_ids',[])):
        raise HTTPException(409,'当前业务已暂停、取消或版本变化；停止本地分析，原件与记录保留')
    cap=published(db,p,row.data['workflow_capability_id'])
    if cap.content_hash!=row.data['workflow_capability_hash']:raise HTTPException(409,'固定业务能力已变化；停止原模型请求')


def execute(db,p,cap,inputs,key):
    from .leases import ownership
    require(p,'admin','operator');validate_manifest(db,p,Manifest.model_validate(cap.data))
    task=scoped(db,Task,inputs['task_id'],p);job_id=ownership(db).get('job_id');job=db.get(Job,job_id) if job_id else None
    from .workflow_graph_scope import owned_graph_job,graph_context,metadata,record_node
    graph_job=owned_graph_job(db,p);graph_meta={}
    if graph_job:
        task,job,node,activation_scope=graph_context(db,p,key,cap)
        if inputs!=job.data['inputs']:raise HTTPException(409,'图学习型输入与冻结节点不一致')
        graph_meta=metadata(task,job,node,activation_scope,cap)
    elif not job or job.kind!='execute_workflow_step' or job.data.get('task_id')!=task.id or task.status!='workflow_running':
        raise HTTPException(409,'学习型变化模型仅在当前已授权业务节点的租约中执行')
    local_inputs(db,p,task,{'execution':{**cap.data['execution'],'capability_id':cap.id}},inputs)
    scope=digest({'scope':activation_scope,'capability_hash':cap.content_hash,'inputs':inputs}) if graph_job else digest({'task':task.id,'step':job.data['scope']['step_id'],'visit':job.data['scope']['visit'],'capability_hash':cap.content_hash,'inputs':inputs})
    row=db.scalar(select(GeoPair).where(GeoPair.workspace_id==p.workspace_id,GeoPair.data['workflow_scope'].as_string()==scope).with_for_update())
    if row and graph_job:record_node(db,p,task,row)
    if row and row.status=='succeeded':guard_workflow(db,p,row);verify_pair_result(db,p,task,row)
    else:
        created=row is None
        if row:
            model_check(db,p,row,verify=True)
            if row.data['request']!=request_value(task,cap.data['execution'],row.data['request_id']):raise HTTPException(409,'业务重试不能替换原件、范围或模型')
            previous={'generation':row.data['generation'],'status':row.status,'error':row.data.get('error'),'job_id':row.data['workflow_job_id']}
            row.status='queued';row.version+=1
            row.data={**row.data,'generation':row.data['generation']+1,'workflow_job_id':job.id,'job_id':job.id,'error':None,
                      'attempts':row.data.get('attempts',[])+[previous]}
        else:
            sources=[normalize(s) for s in task.data['comparison_scene_snapshots']];ex=cap.data['execution'];model_trace=ex['model_trace']
            request_id=digest({'scope':scope,'first_invocation':key});body=request_value(task,ex,request_id)
            at,bt=[s['configuration'].get('observed_at') for s in sources]
            row=GeoPair(workspace_id=p.workspace_id,before_scene_id=sources[0]['scene_id'],after_scene_id=sources[1]['scene_id'],data={
                'request_kind':KIND,'request_id':request_id,'request_hash':digest(body),'request':body,'sources':sources,'sources_hash':digest(sources),
                'runtime_hash':PIPELINE_HASH,'model_trace':copy.deepcopy(model_trace),'model_trace_hash':digest(model_trace),
                'after_allowed_window':list(task.data['comparison_scene_snapshots'][1]['snapshot']['roi']),
                'workflow_scope':scope,'workflow_task_id':task.id,'workflow_instance_id':task.data['instance_id'],'workflow_job_id':job.id,
                'workflow_step_id':(node['source_step_id'] or node['id']) if graph_job else job.data['scope']['step_id'],'workflow_capability_id':cap.id,'workflow_capability_hash':cap.content_hash,
                'workflow_capability_snapshot':copy.deepcopy(cap.data),'input_refs':copy.deepcopy(inputs['scene_refs']),
                'chronology':'user_declared_ordered_times' if at and bt else 'unknown_dates_A_B_comparison','generation':1,'job_id':job.id,
                'result':None,'result_hash':None,'error':None,'review':None,'review_history':[],'attempts':[],**graph_meta})
            db.add(row);db.flush()
        from .native_learned_geo_pair_provenance_v138 import record_input
        record_input(db,p,row,task,job,inputs,cap,created=created)
        guard_workflow(db,p,row);identity=row.id;generation=row.data['generation'];db.commit()
        process(db,p,SimpleNamespace(data={'pair_id':identity,'generation':generation}));row=scoped(db,GeoPair,identity,p)
        verify_pair_result(db,p,task,row)
    result=copy.deepcopy(row.data['result'])
    return {'geo_pair_id':row.id,'attributes':{'learned_change_candidate_pixels':result['candidate_pixels'],
            'common_valid_pixels':result['common_valid_pixels'],'unknown_pixels':result['unknown_pixels'],'not_authoritative':True},
            'anomalies':[],'spatial_comparison':result}


def verify_pair_result(db,p,task,row):
    from .workspace_api import execution
    checked(db,p,row);verify_inputs(db,p,task);_,_,business=execution(db,p,task.id)
    saved=row.data.get('workflow_capability_snapshot')
    try:value=Manifest.model_validate(saved);validate_manifest(db,p,value,live=False)
    except (ValueError,TypeError,KeyError,HTTPException):raise HTTPException(409,'已保存业务模型契约损坏，原记录与原件保留') from None
    if 'graph_runtime' in task.data or row.data.get('workflow_graph_scope'):
        from .workflow_graph_scope import record_node
        node=record_node(db,p,task,row)
        step={'id':node['source_step_id'] or node['id'],'execution':node['execution']}
    else:
        step=next((s for s in business['flow']['steps'] if s['id']==row.data.get('workflow_step_id')),None)
    if (not step or step['execution'].get('adapter')!=ADAPTER or step['execution']['capability_hash']!=row.data.get('workflow_capability_hash')
        or digest(saved)!=row.data.get('workflow_capability_hash') or row.data.get('workflow_task_id')!=task.id
        or row.data.get('input_refs')!=refs(task) or row.data['sources']!=[normalize(s) for s in task.data['comparison_scene_snapshots']]
        or row.data['request']!=request_value(task,value.execution,row.data['request_id'])
        or row.data['after_allowed_window']!=task.data['comparison_scene_snapshots'][1]['snapshot']['roi']
        or row.data['model_trace']!=value.execution['model_trace']):
        raise HTTPException(409,'学习型变化候选与此业务、A/B授权范围或固定模型版本不一致')
    for kind in ARTIFACTS:artifact_bytes(db,p,row,kind)


def local_result(db,p,task,step,inputs,output):
    if step['execution']['adapter']!=ADAPTER:return
    row=scoped(db,GeoPair,output['geo_pair_id'],p);verify_pair_result(db,p,task,row)
    if 'graph_runtime' in task.data:
        from .workflow_graph_scope import record_node
        record_node(db,p,task,row,step)
    if output['spatial_comparison']!=row.data['result'] or output.get('anomalies'):
        raise HTTPException(409,'学习型业务结果与保存证据不一致；不接受自动业务异常结论')
