"""Immutable instance audio and an owned-lease registered local ASR capability."""
import copy
from types import SimpleNamespace
from datetime import UTC
from fastapi import APIRouter,HTTPException
from pydantic import Field
from sqlalchemy import select,text
from .contracts import Contract,Manifest,digest
from .models import AudioRun,WorkflowAudio,Capability,Task,Job,Audit
from .setup_api import P,DB
from .services import scoped,require,audit,register,validate_capability,publish
from .local_audio import RECIPE,RECIPE_HASH,ready,checked

router=APIRouter(prefix='/api/workspace')

def manifest():
    return Manifest(kind='specialist_model',namespace='platform_local',name='sensevoice_task_audio',version='1.0.'+str(int(RECIPE_HASH[:12],16)),owner='本地语音处理服务',
        description='SenseVoiceSmall本地业务音频转写。按任务固定通道/语言读取原音频；片段文字为候选，不识别说话人身份、词级时间或业务异常。',allowed_agent_roles=['perception','data'],
        input_schema={'type':'object','required':['audio_ref','task_id'],'additionalProperties':False,'properties':{'task_id':{'title':'当前业务任务','type':'string'},'audio_ref':{'title':'本任务选定原音频和固定设置','type':'object','required':['input_audio_id','snapshot_hash'],'additionalProperties':False,'properties':{'input_audio_id':{'type':'string'},'snapshot_hash':{'type':'string','pattern':'^[a-f0-9]{64}$'}}}}},
        output_schema={'type':'object','required':['attributes','anomalies','audio_transcription','audio_run_id'],'additionalProperties':False,'properties':{'attributes':{'type':'object'},'anomalies':{'type':'array','maxItems':0},'audio_transcription':{'type':'object'},'audio_run_id':{'type':'string'}}},
        execution={'adapter':'local_audio','pack_id':RECIPE['pack_id'],'recipe_hash':RECIPE_HASH,'backend':'cpu','side_effect':False,'input_bindings':{'audio_ref':'task_audio_ref','task_id':'task_identifier'}},data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False},
        specification={'domain_evaluation':'pending','automatic_domain_use':False,'word_timestamps':False,'speaker_identity':False,'environment_classifier':False,'license':RECIPE['license'],'license_evidence':RECIPE['license_evidence']})

def validate_manifest(value):
    ready()
    if value.model_dump()!=manifest().model_dump():raise HTTPException(422,'本地语音能力必须锁定当前配方、CPU和任务原件，不得扩大外发或状态写入权限')

def publish_audio_capability(db,p):
    require(p,'admin')
    value=manifest();validate_manifest(value)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'workspace':p.workspace_id,'audio_cap':value.version})[:16],16)-(1<<63)})
    cap=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.namespace==value.namespace,Capability.name==value.name,Capability.release_version==value.version))
    if cap:
        if cap.status!='published' or cap.data!=value.model_dump():raise HTTPException(409,'已登记语音版本不一致或未就绪，请核对原记录')
    else:
        cap=register(db,p,value);validate_capability(db,p,cap.id,cap.version);publish(db,p,cap.id,cap.version,'本地转写候选；无云外发、设备控制、对象状态或经验发布权限')
    return cap

def register_from_run(db,p,row):
    require(p,'admin');checked(db,p,row)
    if row.status!='succeeded':raise HTTPException(409,'请先完成真实本地音频处理，不能用未验证原件登记能力')
    cap=publish_audio_capability(db,p)
    audit(db,p,'audio.ability_registered',cap.id,{'sample_run_id':row.id,'recipe_hash':RECIPE_HASH});db.commit()
    return {'capability_id':cap.id,'recipe_hash':RECIPE_HASH,'deployment':'local','backend':'cpu'}

def capture(row):
    if row.status!='succeeded' or row.data['recipe_hash']!=RECIPE_HASH:raise HTTPException(409,'音频尚未完成本地解析或使用不同配方')
    return copy.deepcopy({'source_run_id':row.id,'source_generation':row.data['generation'],'name':row.data['name'],'evidence_id':row.evidence_id,'input_sha256':row.data['input_sha256'],'media_type':row.data['media_type'],'size_bytes':row.data['size_bytes'],'configuration':row.data['configuration'],'audio_metadata':row.data['result']['audio'],'recipe_hash':row.data['recipe_hash'],**({'installation':row.data['installation']} if row.data.get('installation') else {})})

def view(row):return {'id':row.id,'instance_id':row.instance_id,'request_id':row.request_id,'snapshot':row.data['snapshot'],'snapshot_hash':row.data['snapshot_hash'],'created_at':row.created_at.astimezone(UTC).isoformat()}

def checked_input(db,p,identity):
    row=scoped(db,WorkflowAudio,identity,p);source=scoped(db,AudioRun,row.source_run_id,p);checked(db,p,source)
    if digest(row.data['snapshot'])!=row.data['snapshot_hash'] or capture(source)!=row.data['snapshot']:raise HTTPException(409,'业务音频原件、通道、语言或解析证据与关联时不一致')
    return row

def snapshots(db,p,instance,ids):
    if len(ids)!=len(set(ids)):raise HTTPException(422,'音频输入不能重复选择')
    values=[]
    for identity in ids:
        row=checked_input(db,p,identity)
        if row.instance_id!=instance:raise HTTPException(409,'音频关联属于其他实例，请先明确关联到当前实例')
        values.append(view(row))
    return values

def verify(db,p,task):
    saved=task.data.get('audio_snapshots',[])
    if snapshots(db,p,task.data['instance_id'],[s['id'] for s in saved])!=saved:raise HTTPException(409,'任务音频依据与冻结来源不一致')

def ref(task):
    saved=task.data.get('audio_snapshots',[])
    if len(saved)!=1:raise HTTPException(409,'本地语音节点需要一项明确选择的任务音频')
    return {'input_audio_id':saved[0]['id'],'snapshot_hash':saved[0]['snapshot_hash']}

def local_inputs(db,p,task,step,inputs):
    if step['execution']['adapter']!='local_audio':return
    verify(db,p,task)
    if inputs.get('task_id')!=task.id or inputs.get('audio_ref')!=ref(task):raise HTTPException(409,'本地语音节点不能替换任务原音频或固定设置')

def verify_result(db,p,task,row):
    if 'graph_runtime' in task.data or row.data.get('workflow_graph_scope'):
        from .workflow_graph_scope import record_node
        record_node(db,p,task,row)
    checked(db,p,row);verify(db,p,task);saved=task.data['audio_snapshots'][0]
    if row.status!='succeeded' or row.data.get('workflow_task_id')!=task.id or row.data.get('input_audio_id')!=saved['id'] or row.data.get('input_snapshot_hash')!=saved['snapshot_hash'] or row.evidence_id!=saved['snapshot']['evidence_id'] or row.data['configuration']!=saved['snapshot']['configuration']:raise HTTPException(409,'本地语音结果不是此任务选定原件、设置或已保存结果')

def local_result(db,p,task,step,inputs,output):
    if step['execution']['adapter']!='local_audio':return
    row=scoped(db,AudioRun,output['audio_run_id'],p);verify_result(db,p,task,row)
    if 'graph_runtime' in task.data:
        from .workflow_graph_scope import record_node
        record_node(db,p,task,row,step)
    if output['audio_transcription']!=row.data['result'] or output.get('anomalies'):raise HTTPException(409,'转写与实际保存结果不一致，不接受自动业务异常')

def verify_receipts(db,p,task):
    verify(db,p,task)
    from .workflow_segmented_audio import verify_receipts as segment_receipts
    segment_receipts(db,p,task)
    for r in task.data['workflow_runtime'].get('receipts',[]):
        out=r.get('output',{})
        if out.get('audio_transcription') is not None:
            row=scoped(db,AudioRun,out['audio_run_id'],p);verify_result(db,p,task,row)
            if out['audio_transcription']!=row.data['result']:raise HTTPException(409,'业务转写回执与保存结果不一致')

def execute(db,p,cap,inputs,key):
    from .leases import ownership
    from .local_audio import process
    require(p,'admin','operator');validate_manifest(Manifest.model_validate(cap.data));task=scoped(db,Task,inputs['task_id'],p)
    from .workflow_graph_scope import owned_graph_job,graph_context,metadata,record_node
    graph_job=owned_graph_job(db,p);graph_meta={}
    if graph_job:
        task,job,node,activation_scope=graph_context(db,p,key,cap)
        if inputs!=job.data['inputs']:raise HTTPException(409,'图语音输入与冻结节点不一致')
        graph_meta=metadata(task,job,node,activation_scope,cap);logical_scope=activation_scope
    else:
        lease=ownership(db);job=db.get(Job,lease.get('job_id'))
        if not job or job.kind!='execute_workflow_step' or job.data.get('task_id')!=task.id or task.status!='workflow_running':raise HTTPException(409,'业务语音处理仅在当前授权流程节点租约中执行')
        logical_scope=job.data['scope']
    local_inputs(db,p,task,{'execution':cap.data['execution']},inputs);saved=task.data['audio_snapshots'][0];s=saved['snapshot']
    scope=digest({'scope':logical_scope,'capability_hash':cap.content_hash,'inputs':inputs})
    row=db.scalar(select(AudioRun).where(AudioRun.workspace_id==p.workspace_id,AudioRun.request_id==scope).with_for_update())
    if row and graph_job:record_node(db,p,task,row)
    if row and row.status=='succeeded':verify_result(db,p,task,row)
    else:
        if row:
            row.status='queued';row.version+=1;row.data={**row.data,'generation':row.data['generation']+1,'job_id':job.id,'workflow_job_id':job.id,'error':None}
        else:
            row=AudioRun(workspace_id=p.workspace_id,request_id=scope,evidence_id=s['evidence_id'],data={'name':s['name'],'input_sha256':s['input_sha256'],'size_bytes':s['size_bytes'],'media_type':s['media_type'],'configuration':copy.deepcopy(s['configuration']),'recipe_hash':RECIPE_HASH,'request_hash':digest(inputs),'generation':1,'deployment':'local','stage':'queued','error':None,'result':None,'result_hash':None,'review':None,'review_history':[],
                **({'installation':s['installation']} if s.get('installation') else {}),'workflow_task_id':task.id,'workflow_job_id':job.id,'job_id':job.id,'input_audio_id':saved['id'],'input_snapshot_hash':saved['snapshot_hash'],**graph_meta})
            db.add(row);db.flush()
            from .native_audio_provenance_v136 import input_anchor,INPUT_ACTION
            audit(db,p,INPUT_ACTION,row.id,input_anchor(row,task,job,inputs,cap.content_hash))
        identity=row.id;generation=row.data['generation'];db.commit();process(db,p,SimpleNamespace(data={'run_id':identity,'generation':generation}));row=scoped(db,AudioRun,identity,p);verify_result(db,p,task,row)
    from .native_audio_provenance_v136 import input_audits,input_anchor,result_anchor,RESULT_ACTION,need,same
    anchors=input_audits(db,p,row.id)
    if anchors:
        need(len(anchors)==1 and anchors[0].actor_id==p.user_id
            and same(anchors[0].data,input_anchor(row,task,job,inputs,cap.content_hash)))
        receipts=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
            Audit.object_id==row.id,Audit.action==RESULT_ACTION).limit(2)))
        frozen=result_anchor(row,task,job,anchors[0])
        if receipts:need(len(receipts)==1 and receipts[0].actor_id==p.user_id and same(receipts[0].data,frozen))
        else:audit(db,p,RESULT_ACTION,row.id,frozen)
    result=copy.deepcopy(row.data['result'])
    return {'attributes':{'transcription_text_candidate':result['transcription']['text'],'not_authoritative':True},'anomalies':[],'audio_transcription':result,'audio_run_id':row.id}

class Attach(Contract):
    source_run_id:str
    source_snapshot_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')

@router.get('/instances/{identity}/audio-candidates')
def candidates(identity:str,p:P,db:DB):
    from .workspace_api import active
    active(db,p,identity)
    values=[]
    for row in db.scalars(select(AudioRun).where(AudioRun.workspace_id==p.workspace_id,AudioRun.status=='succeeded').order_by(AudioRun.created_at.desc())):
        if row.data.get('workflow_task_id'):continue
        checked(db,p,row);s=capture(row);values.append({'source_run_id':row.id,'snapshot':s,'snapshot_hash':digest(s)})
    return values

@router.get('/instances/{identity}/input-audio')
def inputs(identity:str,p:P,db:DB):
    from .workspace_api import active
    active(db,p,identity)
    return [view(checked_input(db,p,r.id)) for r in db.scalars(select(WorkflowAudio).where(WorkflowAudio.workspace_id==p.workspace_id,WorkflowAudio.instance_id==identity).order_by(WorkflowAudio.created_at.desc()))]

@router.post('/instances/{identity}/input-audio',status_code=201)
def attach(identity:str,body:Attach,p:P,db:DB):
    from .workspace_api import active
    require(p,'admin','operator');active(db,p,identity)
    source=scoped(db,AudioRun,body.source_run_id,p);checked(db,p,source);s=capture(source)
    if source.data.get('workflow_task_id'):raise HTTPException(409,'业务结果不能当作另一任务的原音频来源')
    if digest(s)!=body.source_snapshot_hash:raise HTTPException(409,'音频源设置或原件变化，请重新核对后关联')
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest({'instance':identity,'audio_input':body.request_id})[:16],16)-(1<<63)})
    row=db.scalar(select(WorkflowAudio).where(WorkflowAudio.instance_id==identity,WorkflowAudio.request_id==body.request_id))
    if row:
        if row.source_run_id!=source.id or row.data['snapshot_hash']!=digest(s):raise HTTPException(409,'原音频关联请求不能更换来源')
        return view(checked_input(db,p,row.id))
    row=WorkflowAudio(workspace_id=p.workspace_id,instance_id=identity,source_run_id=source.id,request_id=body.request_id,data={'snapshot':s,'snapshot_hash':digest(s)});db.add(row);db.flush();audit(db,p,'workflow.audio_attached',row.id,{'instance_id':identity,'input_sha256':s['input_sha256']});db.commit();return view(row)

@router.get('/instances/{identity}/input-audio/requests/{request_id}')
def recover(identity:str,request_id:str,source_snapshot_hash:str,p:P,db:DB):
    from .workspace_api import active
    active(db,p,identity)
    row=db.scalar(select(WorkflowAudio).where(WorkflowAudio.workspace_id==p.workspace_id,WorkflowAudio.instance_id==identity,WorkflowAudio.request_id==request_id))
    if not row:raise HTTPException(404,'尚未保存此音频关联请求')
    checked_input(db,p,row.id)
    if row.data['snapshot_hash']!=source_snapshot_hash:raise HTTPException(409,'保存的音频来源与原关联请求不一致')
    return view(row)
