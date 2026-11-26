"""Confirmed manual and registered-ability workflows with immutable evidence."""
import copy
from typing import Literal,Annotated
from pydantic import Field,StrictInt,StrictFloat,model_serializer
from fastapi import APIRouter,HTTPException,UploadFile,File,Query,Depends
from fastapi.responses import Response
from urllib.parse import quote
from sqlalchemy import select
from .setup_api import P,DB
from .contracts import Contract,GoalSpec,digest,VersionAction
from .models import DomainInstance,InstanceRelease,Task,Entity,Memory,VideoAlert,VideoDaily,Observation,Evidence,now
from .services import scoped,require,check_version,published,audit
from .instance_api import active,view
from .instance_compiler import material_ids_for_flow,verify_material_snapshots
from .evidence import evidence_bytes
from .signal_dual_read_session import read_session as dual_read_session
from .report_text_parse_scope import report_text_parse_scope

router=APIRouter(prefix='/api/workspace')
from .auto_evaluation_policy_api_v356 import router as auto_evaluation_policy_router_v356
router.include_router(auto_evaluation_policy_router_v356)
from .auto_evaluation_investigation_api_v357 import router as auto_evaluation_investigation_router_v357
router.include_router(auto_evaluation_investigation_router_v357)
# The endpoint is present but its server-owned rollout gate remains disabled.
from .auto_evaluation_stop_api_v360 import router as auto_evaluation_stop_router_v360
router.include_router(auto_evaluation_stop_router_v360)
from .goal_evaluation_append_api_v299 import router as evaluation_append_router
router.include_router(evaluation_append_router)
from .same_goal_advice_api_v304 import router as business_advice_router
router.include_router(business_advice_router)
from .same_goal_advice_offline_api_candidate_v2 import router as offline_document_router
router.include_router(offline_document_router)
from .same_goal_advice_recovery_api_candidate_v2 import router as advice_recovery_router
router.include_router(advice_recovery_router)
from .same_goal_advice_evidence_explain_candidate_v2 import router as advice_evidence_explain_router
router.include_router(advice_evidence_explain_router)
from .same_goal_continuation_api_v312 import router as business_continuation_router
router.include_router(business_continuation_router)
from .same_goal_continuation_grant_api_v313 import router as continuation_grant_router
router.include_router(continuation_grant_router)
from .same_goal_continuation_execution_api_v314 import router as continuation_execution_router
router.include_router(continuation_execution_router)
from .formal_continuation_api_v344 import router as formal_continuation_router
router.include_router(formal_continuation_router)
from .formal_continuation_api_v345 import router as formal_continuation_write_router
router.include_router(formal_continuation_write_router)
from .workflow_graph_api import router as workflow_graph_router
router.include_router(workflow_graph_router)
from .saved_imagery_api_v163 import router as saved_imagery_router
router.include_router(saved_imagery_router)
from .native_video_review_api_v165 import router as native_video_review_router
router.include_router(native_video_review_router)
from .report_object_revision_api_v166 import router as report_object_revision_router
router.include_router(report_object_revision_router)
from .report_object_adoption_api_v169 import router as report_object_adoption_router
router.include_router(report_object_adoption_router)
from .layered_approval_api_v175 import router as layered_approval_router
router.include_router(layered_approval_router)
from .device_gateway_api_v268 import router as device_gateway_router_v268
router.include_router(device_gateway_router_v268)
from .device_gateway_tools_api_v269 import router as device_gateway_tools_router_v269
router.include_router(device_gateway_tools_router_v269)
from .device_gateway_sources_api_v270 import router as device_gateway_sources_router_v270
router.include_router(device_gateway_sources_router_v270)
TaskReadDB=Annotated[object,Depends(dual_read_session)]

class LocalFileObservation(Contract):
    release_id:str|None=None
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    entity_id:str
    extractor_id:str
    evidence_id:str

@router.get('/instances/{identity}/local-observation-models')
def local_observation_models(identity:str,p:P,db:DB):
    instance=scoped(db,DomainInstance,identity,p)
    if instance.status!='partially_active' or not instance.active_release_id:return []
    from .local_observations import options
    return options(db,p)

def local_observation_row(db,p,identity,observation_id):
    scoped(db,DomainInstance,identity,p)
    row=scoped(db,Observation,observation_id,p)
    if not row.data.get('candidate_only') or row.data.get('local_instance',{}).get('id')!=identity:
        raise HTTPException(404,'本实例没有此本地观测记录')
    return row

@router.post('/instances/{identity}/local-observations',status_code=201)
def create_local_observation(identity:str,body:LocalFileObservation,p:P,db:DB):
    require(p,'admin','operator')
    instance,release=active(db,p,identity,True);entity=scoped(db,Entity,body.entity_id,p)
    from .entity_release_compat import require_entity
    require_entity(db,p,instance,release,entity)
    from .instance_domain import require_destination_version
    require_destination_version(release,[entity],body.release_id)
    from .local_observations import checked_cap,source,view
    checked_cap(db,p,body.extractor_id);ev,_=evidence_bytes(db,p,body.evidence_id)
    src=source(db,p,instance)
    from .contracts import ObservationCreate
    from .managed_local_ingest import ingest
    from zoneinfo import ZoneInfo
    payload=ObservationCreate(source_id=src.id,source_record_id=body.request_id,entity_id=entity.id,
        observed_at=None,business_day=str(ev.created_at.astimezone(ZoneInfo('Asia/Shanghai')).date()),
        evidence_ids=[ev.id],extractor_id=body.extractor_id,startup_mode='manual')
    result=ingest(payload,p,db,expected_local_context={'id':instance.id,'release_id':release.id})
    return view(db,p,scoped(db,Observation,result['id'],p))

@router.get('/instances/{identity}/local-observation-requests/{request_id}')
def local_observation_request(identity:str,request_id:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    row=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.source_record_id==request_id,
        Observation.data['local_instance']['id'].as_string()==identity))
    if not row:raise HTTPException(404,'没有已保存的原请求；可重试相同原件和条件')
    from .local_observations import view
    return view(db,p,row)

@router.get('/instances/{identity}/local-observations')
def local_observations(identity:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    rows=list(db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,
        Observation.data['local_instance']['id'].as_string()==identity).order_by(Observation.created_at.desc()).limit(21)))
    return {'records':[{'id':r.id,'status':r.status,'entity_id':r.entity_id,'created_at':r.created_at.isoformat()} for r in rows[:20]],'has_more':len(rows)>20}

@router.get('/instances/{identity}/local-observations/{observation_id}')
def local_observation(identity:str,observation_id:str,p:P,db:DB):
    from .local_observations import view
    return view(db,p,local_observation_row(db,p,identity,observation_id))

@router.get('/instances/{identity}/local-observations/{observation_id}/export')
def local_observation_export(identity:str,observation_id:str,p:P,db:DB):
    value=local_observation(identity,observation_id,p,db)
    if value['integrity_error']:raise HTTPException(409,value['integrity_error'])
    return {'schema':'local-file-observation-v1','observation':value,'cloud_calls':0,'entity_state_updated':False}

@router.get('/instances/{identity}/local-observation-daily')
def local_observation_daily(identity:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    rows=list(db.scalars(select(Memory).where(Memory.workspace_id==p.workspace_id,
        Memory.data['schema'].as_string()=='local-file-daily-v1',Memory.data['instance_id'].as_string()==identity)
        .order_by(Memory.created_at.desc()).limit(100)))
    seen=set();result=[]
    for row in rows:
        key=(row.data['source_id'],row.data['business_day'])
        if key in seen:continue
        seen.add(key);result.append({'id':row.id,'received_day':row.data['business_day'],'status':row.data['status'],
            'processed':len(row.data['processed_ids']),'failed':len(row.data['failed_ids']),'pending':len(row.data['pending_ids']),
            'reviewed':len(row.data['reviewed_ids']),'not_authoritative':True,'date_basis':'file_upload_request'})
    return result

def verify_perception_evidence(db,p,task,*,lock_sources=True):
    from .video_workflow_handoff_v347 import verify_task as verify_video_handoff
    verify_video_handoff(db,p,task,lock_sources=lock_sources,require_active=lock_sources)
    from .advice_workflows import verify as verify_advice
    verify_advice(db,p,task,lock_sources=lock_sources)
    from .signal_routing import verify_task as verify_routed_task
    verify_routed_task(db,p,task,lock_sources=lock_sources)
    from .signal_dual_handoffs import verify_task as verify_dual_task
    dual_parent=verify_dual_task(db,p,task)
    if task.data.get('perception_signal'):
        from .models import PerceptionSignal
        from .perception_signals import checked
        frozen=task.data['perception_signal'];signal=scoped(db,PerceptionSignal,frozen['signal_id'],p)
        checked(db,p,signal,allow_rejected=bool(dual_parent and task.data['dual_handoff']['slot']=='response'),
            lock_sources=lock_sources)
        if signal.data['basis_hash']!=frozen['basis_hash']:raise HTTPException(409,'任务的异常依据版本不一致')
    value=task.data.get('perception_event')
    if not value:return
    for kind in ['original','frame']:
        row,_=evidence_bytes(db,p,value[kind+'_evidence_id'])
        if row.sha256!=value[kind+'_sha256']:raise HTTPException(409,'视频或画面原件版本不一致，请恢复原件；处理记录保留')


def saved_execution(db,p,identity,lock=False):
    """Read the immutable original workflow; this grants no execution permission."""
    if lock:
        from .signal_dual_handoffs import prelock_task
        prelock_task(db,p,identity)
    task=scoped(db,Task,identity,p,lock)
    if not task.data.get('workflow_runtime'):raise HTTPException(409,'此任务尚未绑定确认的流程，不能当作流程任务执行')
    instance=scoped(db,DomainInstance,task.data['instance_id'],p)
    release=scoped(db,InstanceRelease,task.data['instance_release_id'],p)
    if release.instance_id!=instance.id or release.package_id!=task.package_id:raise HTTPException(409,'任务的实例版本不一致')
    from .models import Capability
    package=scoped(db,Capability,task.package_id,p)
    if package.kind!='domain_package' or package.content_hash!=digest(package.data) or package.validated_hash!=package.content_hash:
        raise HTTPException(409,'原业务配置内容无法核实，保存记录保留')
    if digest(release.data['snapshot'])!=release.configuration_hash or package.data['specification'].get('configuration_hash')!=release.configuration_hash:
        raise HTTPException(409,'原实例配置快照摘要无法核实，保存记录保留')
    from .instance_data_receipts import verify_release
    verify_release(db,p,release)
    from .instance_semantic_runtime_dispatch import verify_release as verify_full_semantics
    if release.data['snapshot'].get('semantic_runtime_plan',{}).get('schema') in {
            'instance-full-semantic-runtime-plan-v3', 'instance-full-semantic-runtime-plan-v4',
            'instance-object-graph-runtime-plan-v171', 'instance-business-flow-graph-runtime-v1',
            'instance-authored-workflow-runtime-v208'}:
        from .instance_semantic_runtime_dispatch import modern
        modern(release.data['snapshot']).verify_release(db,p,release,live=False)
    else:verify_full_semantics(db,p,release)
    business=next((x for x in release.data['snapshot']['businesses'] if x['id']==task.data['business_id']),None)
    if not business or not business['available']:raise HTTPException(409,'原版本业务仍有执行缺口')
    from .signal_routing import verify_task as verify_routed_task
    verify_routed_task(db,p,task,lock_sources=lock)
    if lock:
        from .signal_dual_handoffs import authorize_mutation
        authorize_mutation(db,p,task)
    return task,release,business

def execution(db,p,identity,lock=False,*,lock_sources=True):
    task,release,business=saved_execution(db,p,identity,lock)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task,lock_sources=lock_sources)
    if release.data['snapshot'].get('semantic_runtime_plan',{}).get('schema') in {
            'instance-full-semantic-runtime-plan-v3', 'instance-full-semantic-runtime-plan-v4',
            'instance-object-graph-runtime-plan-v171', 'instance-business-flow-graph-runtime-v1'}:
        from .instance_semantic_runtime_dispatch import modern
        selected=modern(release.data['snapshot'])
        if lock_sources:selected.verify_release(db,p,release,business_name=business['name'])
        else:selected.verify_release(db,p,release,business_name=business['name'],live=False)
    published(db,p,task.package_id,'domain_package')
    from .entity_release_compat import verify_task_objects
    if lock_sources:verify_task_objects(db,p,task,release)
    else:verify_task_objects(db,p,task,release,lock_sources=False)
    return task,release,business

def current_step(task,business):
    if business.get('execution_mode')=='confirmed_graph_workflow':raise HTTPException(409,'此任务按多个协同节点分别处理，请打开对应待办；不能按旧单步骤接口推进')
    if task.status!='workflow_waiting':raise HTTPException(409,'任务已完成或取消，请核对已保存记录')
    step=next((s for s in business['flow']['steps'] if s['id']==task.data['workflow_runtime']['current_step_id']),None)
    if not step:raise HTTPException(409,'任务已结束或步骤状态需要核对')
    return step

def authorize_step(p,release,step,override=''):
    require(p,'admin','operator')
    if step['agent']!='人工确认':raise HTTPException(409,'自动节点没有运行回执，不能填写人工结果冒充完成')
    actor=release.data['snapshot']['roles'][step['role']]['user_id']
    if p.user_id!=actor and (p.role!='admin' or not override.strip()):
        raise HTTPException(403,'请由已指定的负责人处理；管理员代为处理需说明原因')

@router.get('/instances/{identity}/tasks')
def tasks(identity:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    return [view(x) for x in db.scalars(select(Task).where(Task.workspace_id==p.workspace_id,
        Task.data['instance_id'].as_string()==identity).order_by(Task.created_at.desc()).limit(200))]

from .workflow_pixel_contracts_v250 import PixelPairSelection
from .video_workflow_handoff_v347 import Selection as VideoWorkflowSelection

class NewTask(Contract):
    release_id:str
    business_id:str
    entity_ids:list[str]=Field(min_length=1,max_length=20)
    title:str=Field(min_length=1,max_length=300)
    graph_inputs:dict=Field(default_factory=dict)
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    goal_spec:GoalSpec|None=None
    image_ids:list[str]=Field(default_factory=list,max_length=3)
    pixel_pair:PixelPairSelection|None=None
    scene_ids:list[str]=Field(default_factory=list,max_length=3)
    comparison_scene_ids:list[str]=Field(default_factory=list,max_length=2)
    comparison_confirmed:bool=False
    assessment_id:str|None=None
    audio_ids:list[str]=Field(default_factory=list,max_length=1)
    sound_ids:list[str]=Field(default_factory=list,max_length=1)
    advice_id:str|None=None
    signal_id:str|None=None
    signal_version:int|None=Field(default=None,ge=1)
    signal_basis_hash:str|None=None
    video_handoff:VideoWorkflowSelection|None=None

    @model_serializer(mode='wrap')
    def preserve_older_task_payloads(self, handler):
        value=handler(self)
        if self.video_handoff is None:value.pop('video_handoff',None)
        return value

def creation_body_hash(body:NewTask):
    """统一冻结发起体；空的可选输入兼容旧记录，明确目标和媒体保持签入。"""
    optional={'goal_spec','image_ids','scene_ids','comparison_scene_ids','comparison_confirmed',
              'audio_ids','sound_ids','assessment_id','signal_id','signal_version','signal_basis_hash','advice_id','graph_inputs','pixel_pair','video_handoff'}
    return digest(body.model_dump(exclude={key for key in optional if not getattr(body,key)}))


@router.post('/instances/{identity}/task-requests/lookup')
def lookup_task_creation(identity: str, body: NewTask, p: P, db: DB):
    """Read-only original-body lookup, independent of current execution health."""
    scoped(db, DomainInstance, identity, p, True)
    rows = db.scalars(select(Task).where(Task.workspace_id == p.workspace_id,
        Task.data['instance_id'].as_string() == identity,
        Task.data['creation_request_id'].as_string() == body.request_id).limit(2)).all()
    if len(rows) > 1:
        raise HTTPException(409, '同一原发起请求存在多条记录，请核对；未新增任务')
    request = None
    if rows:
        row = rows[0]
        if row.owner_id != p.user_id:
            raise HTTPException(403, '请由原发起账号核对这项请求')
        if row.data['creation_hash'] != creation_body_hash(body):
            raise HTTPException(409, '原发起请求的内容不一致，原任务保留')
        request = {'request_id': body.request_id, 'actor_id': p.user_id,
            'instance_id': identity, 'release_id': row.data['instance_release_id'],
            'business_id': row.data['business_id'], 'task_id': row.id,
            'creation_hash': row.data['creation_hash']}
    from .task_creation_recovery_v120 import find, checked_view
    retired = find(db,p,identity,body.request_id)
    if request and retired:
        raise HTTPException(409, '原请求同时存在任务与保留记录，请核对；未新增任务')
    retirement = checked_view(retired,p,creation_body_hash(body)) if retired else None
    return {'schema': 'workflow-task-request-recovery-v1', 'found': request is not None,
        'request': request, 'retirement': retirement}


class RetireTaskRequest(Contract):
    original: NewTask
    reason: str = Field(min_length=1,max_length=1000)


@router.post('/instances/{identity}/task-requests/retire')
def retire_task_request(identity: str, body: RetireTaskRequest, p: P, db: DB):
    require(p,'admin','operator'); scoped(db,DomainInstance,identity,p,True)
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    if not body.reason.strip(): raise HTTPException(422,'请说明保留原请求并重新填写的原因')
    from .task_creation_recovery_v120 import retire
    value=retire(db,p,identity,body.original,creation_body_hash(body.original),body.reason)
    audit(db,p,'workflow.creation_request_retired',identity,{'request_id':body.original.request_id,'command_hash':value['command_hash']})
    db.commit();return value

def create_task(identity:str,body:NewTask,p,db,*,outcome=None,dual_handoff=None,graph_handoff=None,simulation_handoff=None,automation_handoff=None,signal_approval_handoff=None):
    """Create the existing workflow in the caller's transaction; never commit."""
    require(p,'admin','operator')
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    scoped(db,DomainInstance,identity,p,True)
    creation_hash=creation_body_hash(body)
    from .task_creation_recovery_v120 import find as retired_request
    if retired_request(db,p,identity,body.request_id):
        raise HTTPException(409, '此原发起请求已保留并停止使用，请重新填写后使用新请求')
    prior=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.data['instance_id'].as_string()==identity,
        Task.data['creation_request_id'].as_string()==body.request_id))
    if prior:
        if signal_approval_handoff is not None:
            raise HTTPException(409,'审批草稿的原请求须由独立发生核对，不能采用预占任务')
        if automation_handoff is not None:
            raise HTTPException(409,'自动入口的原请求须先核对独立发生与任务证明，不能采用预占任务')
        if prior.owner_id != p.user_id:
            raise HTTPException(403, '请由原发起账号核对这项请求')
        if prior.data['creation_hash']!=creation_hash:raise HTTPException(409,'同一任务请求不能更换内容')
        if body.video_handoff:
            from .video_workflow_handoff_v347 import KEY as video_key
            saved=prior.data.get(video_key)
            if not isinstance(saved,dict) or saved.get('origin',{}).get('source_task_id')!=body.video_handoff.source_task_id or saved.get('origin',{}).get('basis_hash')!=body.video_handoff.basis_hash:
                raise HTTPException(409,'原任务未绑定相同视频线索，请保留原请求核对')
        if dual_handoff is not None:
            from .signal_dual_handoffs import verify_task
            parent=verify_task(db,p,prior)
            if not parent or parent.id!=dual_handoff[0].id or prior.data['dual_handoff']['slot']!=dual_handoff[1]:
                raise HTTPException(409,'双路任务原请求已绑定不同任务，原记录保留')
        if graph_handoff is not None:
            from .signal_graph_routing_v134 import verify_task as verify_graph_task
            origin=verify_graph_task(db,p,prior)
            if origin is None or origin.id!=graph_handoff.id:
                raise HTTPException(409,'完整流程原请求已绑定不同交接，原记录保留')
        if outcome is not None:outcome['created']=False
        return prior
    instance,release=active(db,p,identity)
    if release.id!=body.release_id:raise HTTPException(409,'实例版本已变化，请重新核对业务')
    if body.business_id not in instance.data['enabled_business_ids']:raise HTTPException(409,'所选业务尚未启用')
    business=next(x for x in release.data['snapshot']['businesses'] if x['id']==body.business_id)
    video_origin=None
    if body.video_handoff:
        if any(value is not None for value in (dual_handoff,graph_handoff,simulation_handoff,automation_handoff,signal_approval_handoff)) or body.signal_id or body.assessment_id or body.advice_id:
            raise HTTPException(422,'视频线索交接不能混入其他自动或异常交接')
        from .video_workflow_handoff_v347 import prepare
        video_origin=prepare(db,p,body.video_handoff,identity,body,business)
    if signal_approval_handoff is not None:
        if any(value is not None for value in (dual_handoff,graph_handoff,simulation_handoff,automation_handoff)):
            raise HTTPException(422,'审批草稿交接不能混用其它自动来源')
    if automation_handoff is not None:
        if any(value is not None for value in (dual_handoff,graph_handoff,simulation_handoff)):
            raise HTTPException(422,'自动入口不能混用其他交接')
        from .business_automation_admission import validate_handoff
        validate_handoff(db,p,automation_handoff,body,instance,release)
    from .graph_task_inputs_v120 import validate_associations
    validate_associations(release,business,body.graph_inputs,body.entity_ids)
    if len(set(body.entity_ids))!=len(body.entity_ids) or not body.title.strip():raise HTTPException(422,'请填写任务名称且不能重复选择对象')
    advice=None
    if body.advice_id:
        if body.signal_id or body.assessment_id:raise HTTPException(422,'处置建议流程不能混入另一路交接')
        from .advice_workflows import packet
        advice=packet(db,p,body,instance,release)
    signal=None
    if body.signal_id:
        if body.assessment_id or body.image_ids or body.scene_ids or body.comparison_scene_ids or body.audio_ids or body.sound_ids:
            raise HTTPException(422,'异常交接单独冻结原依据，请不要混入其它发起资料')
        if body.signal_version is None or body.signal_basis_hash is None:raise HTTPException(422,'请核对异常版本再交接')
        if signal_approval_handoff is not None:
            from .signal_approval_routing_v247 import validate_handoff
            signal=validate_handoff(db,p,signal_approval_handoff,body,instance,release)
        elif graph_handoff is not None:
            if dual_handoff is not None:raise HTTPException(422,'完整流程不能混入另一条异常交接')
            from .signal_graph_routing_v134 import prepare_handoff
            signal=prepare_handoff(db,p,graph_handoff,body,instance,release)
        elif simulation_handoff is not None:
            if dual_handoff is not None:raise HTTPException(422,'模拟异常不能混入另一交接路径')
            from .simulation_loop_v176 import validate_handoff
            signal=validate_handoff(db,p,simulation_handoff,body,instance,release)
        elif any(step['agent']!='人工确认' for step in business['flow']['steps']):raise HTTPException(409,'请选择已启用的人工核查业务；此交接不启动自动能力或设备')
        elif dual_handoff is not None:
            from .signal_dual_handoffs import task_snapshot
            signal=task_snapshot(db,p,*dual_handoff,body,instance,release)
        else:
            from .perception_signals import snapshot
            signal=snapshot(db,p,body.signal_id,instance,release,body.entity_ids,body.signal_version,body.signal_basis_hash)
    elif body.signal_version is not None or body.signal_basis_hash is not None or graph_handoff is not None or signal_approval_handoff is not None:raise HTTPException(422,'缺少异常来源')
    assessment=None
    if body.assessment_id:
        if any(step['agent']!='人工确认' for step in business['flow']['steps']):raise HTTPException(409,'资料缺口需交给已启用的人工核查流程；不能直接启动自动能力或设备')
        from .evidence_assessment_api import task_snapshot
        assessment=task_snapshot(db,p,body.assessment_id,identity,body.entity_ids)
    source_ids=material_ids_for_flow(business['flow'])|set(release.data['snapshot'].get('relation_material_ids',[]))
    if business.get('execution_mode')=='confirmed_graph_workflow':
        from .instance_semantic_runtime_dispatch import modern
        issued_binding=modern(release.data['snapshot']).business_binding(release,business)
        if issued_binding.get('schema') in {'business-flow-execution-binding-v1', 'business-flow-nested-execution-binding-v195'}:
            from .business_flow_execution_runtime import require_task_start
            require_task_start(issued_binding,verified_anomaly_handoff=graph_handoff is not None,automation_handoff=automation_handoff)
            source_ids.update(m['id'] for m in issued_binding['source_materials'])
        elif issued_binding.get('schema') in {'authored-workflow-binding-v208','authored-image-workflow-binding-v243'}:
            from .authored_workflow_runtime_v208 import require_task_start
            require_task_start(issued_binding,verified_anomaly_handoff=bool(graph_handoff is not None
                or body.signal_id or body.assessment_id or body.advice_id or dual_handoff is not None
                or simulation_handoff is not None),automation_handoff=automation_handoff)
            source_ids.update(m['id'] for m in issued_binding['source_materials'])
    sources=copy.deepcopy([x for x in release.data['snapshot']['materials'] if x['id'] in source_ids])
    if source_ids!={x['id'] for x in sources}:raise HTTPException(409,'原版本的资料引用不完整，请重新核对配置')
    verify_material_snapshots(db,p,sources)
    from .workflow_images import snapshots
    images=snapshots(db,p,identity,body.image_ids)
    from .workflow_pixel_sources_v250 import freeze as freeze_pixel_pair
    pixel_pair=freeze_pixel_pair(db,p,instance,release,business,body.pixel_pair,body.entity_ids)
    if pixel_pair is not None:
        from .workflow_pixel_sources_v250 import legacy_images_allowed
        if body.image_ids and not legacy_images_allowed(release,business):
            raise HTTPException(422,'此流程没有独立通用图片输入节点，请只填写像素比较的有序两图')
    from .workflow_scenes import snapshots as scene_inputs
    scenes=scene_inputs(db,p,identity,body.scene_ids)
    comparison=scene_inputs(db,p,identity,body.comparison_scene_ids)
    if comparison or body.comparison_confirmed or any(s.get('execution',{}).get('adapter') in {'local_geo_pair','local_learned_geo_pair'} for s in business['flow']['steps']):
        from .workflow_geo_pairs import validate_pair
        validate_pair(comparison,body.comparison_confirmed)
    for step in business['flow']['steps']:
        if step.get('execution',{}).get('adapter')=='local_learned_geo_pair':
            from .workflow_learned_geo_pairs import validate_task
            cap=published(db,p,step['execution']['capability_id'])
            validate_task(db,p,comparison,body.comparison_confirmed,cap.data['execution'])
    from .workflow_audio import snapshots as audio_inputs
    audio=audio_inputs(db,p,identity,body.audio_ids)
    from .workflow_sound import snapshots as sound_inputs
    sounds=sound_inputs(db,p,identity,body.sound_ids)
    from .native_sound_data_plan import verify as verify_sound_plan
    sound_plan=verify_sound_plan(release.data['snapshot'])
    sound_required=bool(sound_plan and any(pin['business_id']==business['id'] for pin in sound_plan['bindings']))
    if sound_required and len(sounds)!=1:raise HTTPException(422,'此业务需要明确选择一项已保存原音进行环境声音分析')
    if sound_required and len(body.entity_ids)!=1:raise HTTPException(422,'环境声音分析请明确选择一个主要对象；整段原音不能自动归属于多个对象')
    if any(step.get('execution',{}).get('adapter') in {'local_audio','local_task_segmented_audio'} for step in business['flow']['steps']) and len(audio)!=1:raise HTTPException(422,'此业务需要明确选择一项关联原音频')
    from .entity_release_compat import require_entity,freeze_task_objects
    selected=[];entities=[]
    for eid in body.entity_ids:
        obj=scoped(db,Entity,eid,p)
        require_entity(db,p,instance,release,obj)
        catalog=next(x for x in release.data['snapshot']['object_types'] if x['key']==obj.entity_type)
        selected.append({'id':obj.id,'version':obj.version,'type_name':catalog['name'],'attributes':copy.deepcopy(obj.data),'fields':copy.deepcopy(catalog['fields'])})
        entities.append(obj)
    object_bindings=freeze_task_objects(db,p,instance,release,entities)
    task=Task(workspace_id=p.workspace_id,package_id=release.package_id,owner_id=p.user_id,title=body.title.strip(),status='workflow_waiting',
        data={'instance_id':identity,'instance_release_id':release.id,'instance_release_number':release.release_number,
            'business_id':business['id'],'business_name':business['name'],'entity_ids':body.entity_ids,'object_snapshots':selected,
            'object_definition_bindings':object_bindings,'object_definition_bindings_hash':digest(object_bindings),
            **({'evidence_assessment':assessment} if assessment else {}),
            **({'perception_signal':signal} if signal else {}),
            **({'business_advice':advice} if advice else {}),
            'material_snapshots':sources,'image_snapshots':images,**({'pixel_pair_snapshot':pixel_pair} if pixel_pair is not None else {}),'scene_snapshots':scenes,**({'comparison_scene_snapshots':comparison,'comparison_confirmed':body.comparison_confirmed} if comparison else {}),**({'audio_snapshots':audio} if audio else {}),**({'sound_snapshots':sounds} if sounds else {}),'creation_request_id':body.request_id,'creation_hash':creation_hash,
            'workflow_runtime':{'current_step_id':business['flow']['steps'][0]['id'],'receipts':[],'drafts':{},'max_steps':100},
            'execution_mode':business['execution_mode'],'goal_spec':None,'not_current_state_update':True})
    db.add(task);db.flush()
    if video_origin is not None:
        from .video_workflow_handoff_v347 import attach
        attach(db,p,task,*video_origin,creation_hash)
    if automation_handoff is not None:
        from .business_automation_admission import attach_task
        attach_task(db,p,task,automation_handoff,body)
    if graph_handoff is not None:
        # The actual origin, source-family ledger and receipt must precede
        # Goal creation and initialize(), which can queue the first real Jobs.
        from .perception_signals import attach
        from .signal_graph_routing_v134 import attach_origin
        attach(db,p,task,signal)
        attach_origin(db,p,task,graph_handoff,body,signal)
    from .layered_approval_store_v175 import register as register_approval
    approval_managed=register_approval(db,p,task,body)
    if signal_approval_handoff is not None:
        if not approval_managed or task.status!='approval_draft':
            raise HTTPException(409,'本次异常必须先保存正式审批草稿，缺少有效审批策略不能直接执行')
        from .signal_approval_routing_v247 import attach_task as attach_approval_task
        from .signal_approval_targets_v247 import preflight
        attach_approval_task(db,p,task,signal_approval_handoff,body)
        preflight(db,p,task,release,business)
    if business.get('execution_mode')=='confirmed_graph_workflow':
        from .workflow_graph import graph_for
        from .runtime_task_documents import ports as document_source_ports
        if document_source_ports(graph_for(release,business)):
            if not approval_managed:
                raise HTTPException(409,'运行期资料须先保存任务草稿并审批；请先配置本业务的层级审批策略')
            from .layered_approval_store_v175 import validated_value
            validated_value(db,p,task,{'title':task.title,'note':'','graph_inputs':body.graph_inputs,
                'goal_spec':body.goal_spec.model_dump() if body.goal_spec else None},allow_deferred=True)
    if body.goal_spec and not approval_managed:
        from .native_goals import define,GoalDefinition
        define(db,p,task,GoalDefinition(request_id='creation_'+digest(body.request_id),expected_version=task.version,
            reason='发起原任务时确认业务目标和完成条件',goal_spec=body.goal_spec))
    if signal and graph_handoff is None and signal_approval_handoff is None:
        if dual_handoff is not None:
            from .signal_dual_handoffs import attach_task
            attach_task(db,p,task,*dual_handoff)
        else:
            from .perception_signals import attach
            attach(db,p,task,signal)
    if assessment:
        from .models import EvidenceAssessment
        origin=scoped(db,EvidenceAssessment,assessment['assessment_id'],p,True);db.refresh(origin)
        checked_result=origin.data.get('result_hash')
        if checked_result!=assessment['result_hash']:raise HTTPException(409,'交接期间核查依据改变，请保留原请求并重新核对')
        origin.version+=1;origin.data={**origin.data,'sent_tasks':[*origin.data.get('sent_tasks',[]),{'task_id':task.id,'business_name':business['name'],'task_title':task.title,'at':now().isoformat()}]}
    if approval_managed:
        # Approval and the later explicit start are separate durable commands.
        # No goal timer, graph activation or ability job exists yet.
        pass
    elif business.get('execution_mode')=='confirmed_graph_workflow':
        from .workflow_graph import initialize
        initialize(db,p,task,release,business,body.graph_inputs)
    else:
        if body.graph_inputs:raise HTTPException(422,'此业务未采用完整协同图，不能接受图任务输入')
        from .workflow_abilities import continue_flow
        continue_flow(db,p,task,release,business)
    audit(db,p,'workflow.created',task.id,{'release':release.id,'business':business['id']})
    if outcome is not None:outcome['created']=True
    return task

@router.post('/instances/{identity}/tasks',status_code=201)
def create(identity:str,body:NewTask,p:P,db:DB):
    outcome={}
    task=create_task(identity,body,p,db,outcome=outcome)
    if outcome['created']:db.commit()
    return view(task)

@router.post('/instances/{identity}/input-images',status_code=201)
async def upload_image(identity:str,p:P,db:DB,file:UploadFile=File()):
    require(p,'admin','operator');active(db,p,identity)
    media=file.content_type or ''
    if media not in {'image/png','image/jpeg'}:raise HTTPException(415,'请选择PNG或JPEG任务图片；文档在初始化资料中上传')
    from .evidence import save_evidence
    from .workflow_images import view as image_view
    content=await file.read(20*1024*1024+1)
    row=save_evidence(db,p,content,media)
    row.data={**row.data,'purpose':'workflow_input_image','instance_id':identity,
        'name':(file.filename or '任务图片').replace('\\','/').split('/')[-1][:250]}
    audit(db,p,'workflow.image_saved',row.id,{'instance':identity,'sha256':row.sha256,'cloud_calls':0})
    db.commit();return image_view(row)

@router.get('/instances/{identity}/input-images')
def input_images(identity:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    from .workflow_images import view as image_view
    return [image_view(x) for x in db.scalars(select(Evidence).where(Evidence.workspace_id==p.workspace_id,
        Evidence.data['purpose'].as_string()=='workflow_input_image',Evidence.data['instance_id'].as_string()==identity)
        .order_by(Evidence.created_at.desc()).limit(100))]

def image_response(row,content):
    return Response(content,media_type=row.media_type,headers={'Content-Disposition':"inline; filename*=UTF-8''"+quote(row.data['name']),
        'X-Content-SHA256':row.sha256,'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})

@router.get('/instances/{identity}/input-images/{image_id}/content')
def input_image_content(identity:str,image_id:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    from .workflow_images import snapshots
    snapshots(db,p,identity,[image_id]);row,content=evidence_bytes(db,p,image_id)
    return image_response(row,content)

@router.get('/tasks/{identity}/images/{image_id}/content')
def task_image_content(identity:str,image_id:str,p:P,db:DB):
    task,_,_=execution(db,p,identity)
    from .workflow_images import verify
    if image_id not in [x['id'] for x in task.data.get('image_snapshots',[])]:raise HTTPException(404,'此图片不在任务原件中')
    verify(db,p,task);row,content=evidence_bytes(db,p,image_id)
    return image_response(row,content)

@router.get('/tasks/{identity}/masks/{mask_id}/content')
def task_mask_content(identity:str,mask_id:str,p:P,db:DB):
    task,_,_=execution(db,p,identity)
    from .segmentation import mask_bytes
    for receipt in task.data['workflow_runtime']['receipts']:
        result=receipt.get('output',{}).get('segmentation',{})
        if receipt.get('candidate_only') and any(m.get('mask_evidence_id')==mask_id for m in result.get('masks',[])):
            ids=receipt.get('input_snapshot',{}).get('evidence_ids',[])
            if len(ids)!=1:raise HTTPException(409,'实例掩膜缺少固定原图')
            row,content=mask_bytes(db,p,result,mask_id,ids[0])
            return Response(content,media_type='image/png',headers={'X-Content-SHA256':row.sha256,
                'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    raise HTTPException(404,'此掩膜不属于本任务的保存结果')

@router.get('/tasks/{identity}/video-handoff-context')
def video_handoff_context(identity:str,p:P,db:TaskReadDB):
    task,release,business=saved_execution(db,p,identity)
    from .video_workflow_handoff_v347 import context
    return context(db,p,task,business)


@router.get('/tasks/{identity}/video-data-reception')
def video_data_reception(identity:str,p:P,db:TaskReadDB):
    task,_,business=saved_execution(db,p,identity)
    from .video_workflow_handoff_v347 import task_reception
    return task_reception(db,p,task,business)


@router.get('/tasks/{identity}')
def detail(identity:str,p:P,db:TaskReadDB):
    from .report_full_source_read_scope import task_full_source_read_scope
    with report_text_parse_scope(), task_full_source_read_scope(db,p,identity):
        return _detail_report_text_operation(identity,p,db)

def _detail_report_text_operation(identity,p,db):
    task,release,business=saved_execution(db,p,identity)
    from .video_workflow_handoff_v347 import is_candidate
    if is_candidate(task,business):
        return {**view(task),'business':business,'roles':release.data['snapshot']['roles'],
            'profile':release.data['snapshot']['profile'],'completed':False,'video_workflow_inbox':True,
            'permissions':{'can_save_note':False,'can_complete_step':False,'can_run_ability':False,
                'reason':'视频线索已保存；请核对依据并发起已配置的业务流程'}}
    from .layered_approval_store_v175 import detail as approval_detail
    approval=approval_detail(db,p,task)
    if approval is not None and approval['execution'] is None:
        return {**view(task),'business':business,'roles':release.data['snapshot']['roles'],
            'profile':release.data['snapshot']['profile'],'completed':False,
            'layered_approval_v175':approval,'permissions':{'can_save_note':False,
                'can_complete_step':False,'can_run_ability':False,'can_retry_ability':False,
                'reason':'请先完成逐级审批，再由执行岗位明确启动任务'}}
    if business.get('execution_mode')=='confirmed_graph_workflow':
        from .workflow_graph import public_view
        return {**public_view(task,release,business,p,db=db),
            'layered_approval_v175':approval}
    blocked=False;object_block=None
    if 'object_definition_bindings' in task.data or 'object_definition_bindings_hash' in task.data:
        from .entity_release_compat import verify_task_objects
        try:verify_task_objects(db,p,task,release,lock_sources=False)
        except HTTPException as error:
            blocked=True;object_block=str(error.detail)
    if task.data.get('native_goal_context'):
        from .native_goals import guard
        try:guard(db,p,task,lock_sources=False)
        except HTTPException:blocked=True
    from .native_step_execution import unresolved_for_step
    execution_waiting=unresolved_for_step(task,task.data['workflow_runtime']['current_step_id'])
    from .workflow_cancellation import permissions
    authority=permissions(db,p,task,release,business,authorize_step,execution_waiting=execution_waiting,lock_sources=False)
    from .workflow_ability_requests import permissions as ability_permissions
    ability_authority=ability_permissions(p,task,business,blocked=blocked)
    authority={**authority,**{key:value for key,value in ability_authority.items() if key!='reasons'},
        'reasons':{**authority.get('reasons',{}),**ability_authority['reasons']}}
    from .native_pixel_receipts_v250 import task_reception_gate
    pixel_gate=task_reception_gate(db,p,task)
    if not pixel_gate['allowed']:
        authority={**authority,'can_complete_step':False,
            'reasons':{**authority.get('reasons',{}),'complete_step':pixel_gate['reason']}}
    if task.data.get('sound_snapshots'):
        from .workflow_sound import require_task_received as require_sound_received
        try:require_sound_received(db,p,task)
        except HTTPException as error:
            if error.status_code!=409:raise
            authority={**authority,'can_complete_step':False,
                'reasons':{**authority.get('reasons',{}),'complete_step':str(error.detail)}}
    if object_block:
        draft_notice='；当前处理说明仍可保存，流程推进与能力执行暂不可用' if authority.get('can_save_note') else ''
        authority={**authority,'can_complete_step':False,
            'can_run_ability':False,'can_retry_ability':False,'reason':object_block+draft_notice,'blocked_reason':object_block,
            'reasons':{**authority['reasons'],**{key:object_block for key in ['complete_step','run_ability','retry_ability']}}}
    from .workflow_worker_status import automatic
    from .signal_dual_handoffs import verify_task as verify_dual_task,parent_view,ack_permissions
    parent=verify_dual_task(db,p,task);dual_view={}
    if parent:
        dual_view={'dual_handoff_view':parent_view(db,p,parent)}
        authority={**authority,**ack_permissions(db,p,parent)}
    return {**view(task),**dual_view,'layered_approval_v175':approval,'native_execution_waiting':execution_waiting,'business':business,'roles':release.data['snapshot']['roles'],
        'profile':release.data['snapshot']['profile'],'completed':task.status=='workflow_completed','native_execution_blocked':blocked,
        'permissions':authority,'automatic_worker':automatic(db,p,task)}

class Note(Contract):
    version:int=Field(ge=1)
    step_id:str
    note:str=Field(max_length=5000)
    override_reason:str=Field(default='',max_length=500)
    branch:bool|None=None
    perception_decision:Literal['abnormal','not_abnormal','uncertain']|None=None

@router.put('/tasks/{identity}/note')
def note(identity:str,body:Note,p:P,db:DB):
    task,release,business=saved_execution(db,p,identity,True);check_version(task,body.version);step=current_step(task,business)
    from .signal_dual_handoffs import verify_task as verify_dual_task,reject_response_verdict
    reject_response_verdict(verify_dual_task(db,p,task),task,body.perception_decision)
    authorize_step(p,release,step,body.override_reason)
    if step['id']!=body.step_id:raise HTTPException(409,'流程已到另一步骤，原输入保留，请核对')
    runtime=copy.deepcopy(task.data['workflow_runtime']);runtime['drafts'][step['id']]={'note':body.note,'branch':body.branch,'override_reason':body.override_reason,'perception_decision':body.perception_decision,'actor':p.user_id,'at':now().isoformat()}
    task.version+=1;task.data={**task.data,'workflow_runtime':runtime};audit(db,p,'workflow.note_saved',task.id)
    db.commit();return view(task)

class Complete(Note):
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')

@router.post('/tasks/{identity}/complete-step')
def complete(identity:str,body:Complete,p:P,db:DB):
    require(p,'admin','operator');task,release,business=execution(db,p,identity,True)
    from .signal_dual_handoffs import verify_task as verify_dual_task,reject_response_verdict,guard_completion
    dual_parent=verify_dual_task(db,p,task);reject_response_verdict(dual_parent,task,body.perception_decision)
    runtime=copy.deepcopy(task.data['workflow_runtime']);signature=digest(body.model_dump(exclude={'version'}))
    prior=next((x for x in runtime['receipts'] if x['request_id']==body.request_id),None)
    if prior:
        if prior['actor']!=p.user_id:raise HTTPException(403,'请由原提交账号核对重试请求')
        if prior['request_hash']!=signature:raise HTTPException(409,'同一回执不能更换结果')
        return view(task)
    from .native_pixel_receipts_v250 import require_task_received
    require_task_received(db,p,task)
    from .workflow_sound import require_task_received as require_sound_received
    require_sound_received(db,p,task)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task,record_actor=True)
    if task.data.get('native_goal_context'):
        from .native_goals import guard
        guard(db,p,task,step=True)
    from .native_step_execution import guard_step_advance
    guard_step_advance(db,p,task)
    check_version(task,body.version);step=current_step(task,business);authorize_step(p,release,step,body.override_reason)
    if step['id']!=body.step_id or not body.note.strip():raise HTTPException(422,'请核对当前步骤并填写必需的处理结果')
    if len(runtime['receipts'])>=runtime['max_steps']:raise HTTPException(409,'已达到流程次数上限，原记录保留，请调整流程后再安排任务')
    if step.get('condition','').strip() and body.branch is None:raise HTTPException(422,'请根据证据核对分支条件是否满足')
    if not step.get('condition','').strip() and body.branch is not None:raise HTTPException(422,'当前步骤没有分支条件')
    verify_material_snapshots(db,p,task.data.get('material_snapshots',[]),allow_archived=True)
    from .workflow_images import verify
    verify(db,p,task)
    from .workflow_images import verify_masks
    verify_masks(db,p,task)
    from .workflow_scenes import verify_masks as scene_masks
    scene_masks(db,p,task)
    from .workflow_audio import verify_receipts as verify_audio_receipts
    verify_audio_receipts(db,p,task)
    from .workflow_sound import verify_receipts as verify_sound_receipts
    verify_sound_receipts(db,p,task)
    from .workflow_geo_pairs import verify_receipts
    verify_receipts(db,p,task)
    steps=business['flow']['steps'];index=next(i for i,x in enumerate(steps) if x['id']==step['id'])
    next_id=step.get('elseId','end') if body.branch is False else step.get('nextId','auto')
    if next_id=='auto':next_id=steps[index+1]['id'] if index+1<len(steps) else 'end'
    verify_perception_evidence(db,p,task)
    if dual_parent:guard_completion(db,p,task,next_id,body.perception_decision)
    if (task.data.get('perception_event') or task.data.get('perception_signal')) and next_id=='end' and body.perception_decision is None and not (dual_parent and task.data['dual_handoff']['slot']=='response'):
        raise HTTPException(422,'请对感知线索明确选择业务异常判断；证据不足时选择尚不能判断')
    runtime['receipts'].append({'request_id':body.request_id,'request_hash':signature,'step_id':step['id'],'step_name':step['name'],
        'output_name':step['output'],'note':body.note.strip(),'perception_decision':body.perception_decision,'branch':body.branch,'actor':p.user_id,'role':step['role'],
        'override_reason':body.override_reason.strip(),'at':now().isoformat(),'evidence_basis':'pinned_object_and_configured_materials','material_snapshots':task.data.get('material_snapshots',[]),'image_snapshots':task.data.get('image_snapshots',[]),'scene_snapshots':task.data.get('scene_snapshots',[]),**({'audio_snapshots':task.data['audio_snapshots']} if task.data.get('audio_snapshots') else {}),**({'sound_snapshots':task.data['sound_snapshots']} if task.data.get('sound_snapshots') else {})})
    runtime['drafts'].pop(step['id'],None);runtime['current_step_id']=None if next_id=='end' else next_id
    if next_id=='end':
        task.status='workflow_completed'
        if task.data.get('perception_signal'):
            from .perception_signals import finish
            finish(db,p,task,body.perception_decision,body.note.strip())
        if task.data.get('perception_event'):
            alert=scoped(db,VideoAlert,task.data['perception_event']['video_alert_id'],p,True)
            previous=alert.data.get('business_review')
            review={'task_id':task.id,'decision':body.perception_decision,'actor':p.user_id,'at':now().isoformat(),'note':body.note.strip(),
                'source_clock_utc':'unknown','entity_state_updated':False,'device_action_authorized':False}
            alert.data={**alert.data,'business_review':review,'business_review_history':[*alert.data.get('business_review_history',[]),*([previous] if previous else [])][-30:]}
            alert.version+=1
            task.data={**task.data,'perception_review':review}
        memory=Memory(workspace_id=p.workspace_id,status='candidate',text=f'人工业务流程“{business["name"]}”已按确认图保存结果',
            data={'type':'workflow_result','task_id':task.id,'instance_release_id':release.id,'object_snapshots':task.data['object_snapshots'],
                'perception_event':task.data.get('perception_event'),'perception_signal':task.data.get('perception_signal'),'perception_review':task.data.get('perception_review'),
                'receipts':runtime['receipts'],'material_snapshots':task.data.get('material_snapshots',[]),'image_snapshots':task.data.get('image_snapshots',[]),'scene_snapshots':task.data.get('scene_snapshots',[]),**({'comparison_scene_snapshots':task.data['comparison_scene_snapshots']} if task.data.get('comparison_scene_snapshots') else {}),**({'audio_snapshots':task.data['audio_snapshots']} if task.data.get('audio_snapshots') else {}),**({'sound_snapshots':task.data['sound_snapshots']} if task.data.get('sound_snapshots') else {}),'source_clock':'not_inferred','entity_state_updated':False,'auto_publish':False})
        db.add(memory);db.flush();runtime['data_receipt_id']=memory.id
    task.version+=1;task.data={**task.data,'workflow_runtime':runtime}
    audit(db,p,'workflow.step_completed',task.id,{'step':step['id'],'next':next_id})
    from .native_pixel_provider_v250 import record_human_receipt
    record_human_receipt(db,p,task,runtime['receipts'][-1])
    if task.data.get('native_goal_context'):
        from .native_goals import queue
        queue(db,p,task,'flow:'+str(len(runtime['receipts'])))
    if next_id!='end':
        from .workflow_abilities import continue_flow
        continue_flow(db,p,task,release,business)
    db.commit();return view(task)

from .workflow_cancellation import Cancel

def audio_task(db,p,identity,run_id):
    from .workflow_audio import verify_receipts
    from .models import AudioRun
    task,release,business=execution(db,p,identity);verify_receipts(db,p,task)
    receipt=next((r for r in task.data['workflow_runtime'].get('receipts',[]) if r.get('output',{}).get('audio_run_id')==run_id),None)
    if not receipt:raise HTTPException(404,'此音频不是当前业务任务的已保存执行结果')
    row=scoped(db,AudioRun,run_id,p)
    return task,row

@router.get('/tasks/{identity}/audio/{run_id}/original')
def task_audio_original(identity:str,run_id:str,p:P,db:DB):
    from .local_audio import checked
    task,row=audio_task(db,p,identity,run_id);ev,content=checked(db,p,row)
    return Response(content,media_type=ev.media_type,headers={'X-Content-SHA256':ev.sha256,'X-Content-Type-Options':'nosniff'})

@router.get('/tasks/{identity}/audio/{run_id}/export')
def task_audio_export(identity:str,run_id:str,p:P,db:DB):
    task,row=audio_task(db,p,identity,run_id)
    return {'schema':'business-audio-review-v1','task_title':task.title,'task_status':task.status,'audio_snapshots':task.data['audio_snapshots'],'result':row.data['result'],
        'review_receipts':[r for r in task.data['workflow_runtime']['receipts'] if r.get('execution_mode')!='registered_ability'],'source_time':'unknown','entity_state_updated':False,'candidate_only':True}

def task_segments(db,p,identity,run_id):
    from .models import SegmentedAudioRun
    from .local_segmented import checked
    from .workflow_segmented_audio import verify_result
    task,_,_=execution(db,p,identity);row=scoped(db,SegmentedAudioRun,run_id,p)
    if row.data.get('workflow_task_id')!=task.id:raise HTTPException(404,'此分段音频不属于当前业务任务')
    saved=task.data['audio_snapshots'][0]
    if row.data['input_audio_id']!=saved['id'] or row.data['input_snapshot_hash']!=saved['snapshot_hash']:raise HTTPException(409,'分段原音频与任务冻结来源不一致')
    source,content=checked(db,p,row)
    if row.status=='succeeded':verify_result(db,p,task,row)
    return task,row,source,content

@router.get('/tasks/{identity}/segmented-audio')
def task_segment_rows(identity:str,p:P,db:DB):
    from .models import SegmentedAudioRun,SpeechActivityRun
    from .segmented_audio_api import view as segment_view
    task,_,_=execution(db,p,identity)
    rows=db.scalars(select(SegmentedAudioRun).where(SegmentedAudioRun.workspace_id==p.workspace_id,SegmentedAudioRun.data['workflow_task_id'].astext==task.id).order_by(SegmentedAudioRun.created_at.desc())).all()
    values=[]
    for r in rows:
        task_segments(db,p,identity,r.id);values.append(segment_view(r))
    activity=db.scalars(select(SpeechActivityRun).where(SpeechActivityRun.workspace_id==p.workspace_id,SpeechActivityRun.data['workflow_task_id'].astext==task.id).order_by(SpeechActivityRun.created_at.desc())).all()
    return {'runs':values,'activity':[{'id':r.id,'status':r.status,'stage':r.data['stage'],'error':r.data.get('error'),'interval_count':len(r.data['result']['activity']['intervals']) if r.data.get('result') else None} for r in activity]}

@router.get('/tasks/{identity}/segmented-audio/{run_id}/original')
def task_segment_original(identity:str,run_id:str,p:P,db:DB):
    task,row,source,content=task_segments(db,p,identity,run_id)
    return Response(content,media_type=source['media_type'],headers={'X-Content-SHA256':source['input_sha256'],'X-Content-Type-Options':'nosniff'})

@router.get('/tasks/{identity}/segmented-audio/{run_id}/export')
def task_segment_export(identity:str,run_id:str,p:P,db:DB):
    task,row,source,content=task_segments(db,p,identity,run_id)
    from .segmented_audio_api import view as segment_view
    return {'schema':'business-segmented-audio-review-v1','task_title':task.title,'task_status':task.status,'audio_snapshots':task.data['audio_snapshots'],'record':segment_view(row),'partial':row.status!='succeeded','review_receipts':[r for r in task.data['workflow_runtime']['receipts'] if r.get('execution_mode')!='registered_ability'],'source_time':'unknown','entity_state_updated':False,'candidate_only':True,'cloud_calls':0}

@router.post('/tasks/{identity}/cancel')
def cancel(identity:str,body:Cancel,p:P,db:DB):
    from .workflow_cancellation import authorize,prior,record,CANCELLABLE,require_not_accepted
    task,release,business=saved_execution(db,p,identity,True)
    authorize(p,task)
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    saved=prior(task,p,body)
    if saved:return {**view(task),'cancel_request':saved}
    from .video_workflow_handoff_v347 import is_candidate,KEY as video_key
    if is_candidate(task,business) and task.data.get(video_key):
        raise HTTPException(409,'原视频线索已交接业务任务，请在该业务任务中处理，原线索依据保留')
    require_not_accepted(db,p,task)
    if task.status=='workflow_cancelled':return view(task)
    check_version(task,body.version)
    if task.status not in CANCELLABLE or not body.reason.strip():raise HTTPException(409,'只有待处理任务可以取消，且需填写原因')
    if task.data.get('native_goal_context'):
        from .native_goals import saved_context
        from .native_goal_time import cancel as cancel_timer
        from .native_goal_acquisitions import stop_coordination
        saved_context(db,p,task)
        cancel_timer(db,p,task,'原流程任务已明确取消')
        stop_coordination(db,p,task,'原流程任务已明确取消')
        from .native_step_execution import stop_all
        stop_all(db,p,task,'原流程任务已明确取消')
        from .native_goal_queries import stop_all as stop_queries
        stop_queries(db,p,task,'原流程任务已明确取消')
    task.status='workflow_cancelled';task.version+=1
    from .signal_dual_handoffs import task_cancelled
    task_cancelled(db,p,task)
    task.data={**task.data,'cancellation':{'reason':body.reason.strip(),'retained_note':body.retained_note,'actor':p.user_id,'at':now().isoformat()}}
    saved=record(task,p,body)
    audit(db,p,'workflow.cancelled',task.id,{'reason':body.reason.strip(),
        **({'request_id':body.request_id,'body_hash':saved['request_hash']} if saved else {})})
    db.commit();return {**view(task),**({'cancel_request':saved} if saved else {})}


@router.get('/tasks/{identity}/cancel-requests/{request_id}')
def cancel_request(identity:str,request_id:str,p:P,db:DB):
    from .workflow_cancellation import probe
    task,_,_=saved_execution(db,p,identity)
    return probe(task,p,request_id)


class StartReview(Contract):
    version:int=Field(ge=1)
    override_reason:str=Field(default='',max_length=500)

@router.post('/tasks/{identity}/start-review')
def start_review(identity:str,body:StartReview,p:P,db:DB):
    task,release,business=execution(db,p,identity,True)
    from .video_workflow_handoff_v347 import is_candidate
    if is_candidate(task,business):raise HTTPException(409,'此视频线索需发起已配置的协同业务流程，不能使用旧步骤接口办理')
    if not task.data.get('perception_event'):raise HTTPException(409,'此任务不是感知线索核查任务')
    check_version(task,body.version)
    if task.status not in {'workflow_pending','workflow_waiting'}:
        raise HTTPException(409,'任务已完成或取消，请核对保存记录')
    step=next(s for s in business['flow']['steps'] if s['id']==task.data['workflow_runtime']['current_step_id'])
    if step['agent']=='人工确认':authorize_step(p,release,step,body.override_reason)
    else:
        require(p,'admin','operator')
        if task.owner_id!=p.user_id and p.role!='admin':raise HTTPException(403,'请由任务发起人或管理员开始核查')
    if task.status=='workflow_waiting':return view(task)
    instance=scoped(db,DomainInstance,task.data['instance_id'],p,True)
    if instance.status!='partially_active' or task.data['business_id'] not in instance.data.get('enabled_business_ids',[]):
        raise HTTPException(409,'实例或接收业务已暂停，请核对配置后再开始处理')
    verify_perception_evidence(db,p,task)
    verify_material_snapshots(db,p,task.data.get('material_snapshots',[]),allow_archived=True)
    task.status='workflow_waiting';task.version+=1
    task.data={**task.data,'review_started':{'actor':p.user_id,'at':now().isoformat(),'override_reason':body.override_reason}}
    audit(db,p,'workflow.perception_review_started',task.id)
    from .workflow_abilities import continue_flow
    continue_flow(db,p,task,release,business)
    db.commit();return view(task)

@router.get('/instances/{identity}/observations')
def observations(identity:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    return [{'id':x.id,'status':x.status,'entity_id':x.entity_id,'run_id':x.data['run_id'],'video_seconds':x.data['video_seconds'],
        'business_name':x.data['domain_binding']['business_name'],'source_clock_utc':'unknown',
        'counts':x.data['extracted']['candidate_state']['label_counts']} for x in db.scalars(select(Observation).where(
            Observation.workspace_id==p.workspace_id,Observation.data['domain_binding']['instance_id'].as_string()==identity)
            .order_by(Observation.created_at.desc()).limit(100))]

@router.get('/instances/{identity}/daily')
def daily(identity:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    receipts=[]
    for row in db.scalars(select(VideoDaily).where(VideoDaily.workspace_id==p.workspace_id).order_by(VideoDaily.business_day.desc()).limit(100)):
        records=[r for r in row.data['records'] if (r.get('business_target') or {}).get('instance_id')==identity]
        if records:
            receipts.append({'id':row.id,'version':row.version,'received_day':row.business_day,'source_name':row.data['source_name'],
                'status':'incomplete' if any(r['capture_status'] in ['queued','connecting','capturing','failed','partial','cancelled'] or r['processing_status'] in ['queued','running','failed','cancelled'] for r in records) else 'collected_with_limits',
                'source_receipt_status':row.data['status'],'data_memory_id':row.data['data_memory_id'],'records':records,
                'not_authoritative':True,'source_observation_day_verified':False})
    return receipts


@router.get('/ability-options')
def ability_options(p:P,db:DB):
    from .models import Capability
    from .workflow_abilities import eligible,external,ROLES,MANIFEST_BINDINGS
    from urllib.parse import urlsplit
    result=[]
    for cap in db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.status=='published',Capability.kind.in_(['tool','specialist_model']))):
        agents=[]
        for label,role in ROLES.items():
            try:published(db,p,cap.id);eligible(cap,role);agents.append(label)
            except HTTPException:pass
        if agents:
            budget={'max_attempts':cap.data['budget'].get('max_attempts',2),
                'max_concurrency':cap.data['budget'].get('max_concurrency',1)}
            # An open manifest budget may contain arbitrary private metadata.
            # Only the actual numeric execution limits belong in this picker.
            if type(budget['max_concurrency']) is not int or budget['max_concurrency']<1:
                continue
            result.append({'id':cap.id,'name':cap.data['description'],'agents':agents,'version':cap.data['version'],
                'content_hash':cap.content_hash,'release_version':cap.release_version,
                'max_attempts':budget['max_attempts'],'budget':budget,
                'input_schema':cap.data['input_schema'],'output_schema':cap.data['output_schema'],'external':external(cap),'adapter':cap.data['execution']['adapter'],
                'fixed_bindings':{k:MANIFEST_BINDINGS[v] for k,v in cap.data['execution'].get('input_bindings',{}).items()},
                'destination':urlsplit(cap.data['execution'].get('endpoint','')).hostname or ('云端登记模型' if external(cap) else '本地'),
                'deployment':'local' if not external(cap) else 'registered_external'})
    return result


class AutomaticAction(Contract):
    version:int=Field(ge=1)
    step_id:str
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    retry:bool=False

@router.post('/tasks/{identity}/run-ability')
def run_ability(identity:str,body:AutomaticAction,p:P,db:DB):
    require(p,'admin','operator');task,release,business=execution(db,p,identity,True)
    from .workflow_ability_requests import authorize,prior,record,journal
    authorize(p,task)
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    saved=prior(task,p,body)
    if saved:return {**view(task),'ability_request':saved}
    rt=copy.deepcopy(task.data['workflow_runtime'])
    check_version(task,body.version)
    if task.status not in {'workflow_waiting','workflow_failed','workflow_blocked'} or body.step_id!=rt['current_step_id']:
        raise HTTPException(409,'当前任务不是等待此能力执行的状态，请核对已保存进度')
    if len(journal(task))>=500:raise HTTPException(409,'任务执行请求已达到上限')
    visit=len(rt.get('receipts',[]))
    from .workflow_abilities import schedule
    if not schedule(db,p,task,release,business,confirmed=True,retry=body.retry,request_id=body.request_id):
        raise HTTPException(409,'当前是人工步骤，不能冒充能力执行')
    saved=record(task,p,body,visit)
    db.commit();return {**view(task),'ability_request':saved}


@router.get('/tasks/{identity}/ability-requests/{request_id}')
def ability_request(identity:str,request_id:str,p:P,db:DB):
    require(p,'admin','operator')
    task,_,_=saved_execution(db,p,identity)
    from .workflow_ability_requests import probe
    return probe(task,p,request_id)


class SceneInput(Contract):
    scene_id:str
    scene_version:int=Field(ge=1)
    roi:tuple[StrictInt,StrictInt,StrictInt,StrictInt]|None=None
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')


@router.post('/instances/{identity}/input-scenes',status_code=201)
def attach_scene(identity:str,body:SceneInput,p:P,db:DB):
    from .models import WorkflowScene,GeoScene
    from .workflow_scenes import capture,view as scene_view
    from .geo_storage import path_for
    require(p,'admin','operator');active(db,p,identity,True);signature=digest(body.model_dump())
    old=db.scalar(select(WorkflowScene).where(WorkflowScene.workspace_id==p.workspace_id,WorkflowScene.instance_id==identity,WorkflowScene.data['request_id'].as_string()==body.request_id))
    if old:
        if old.data['request_hash']!=signature:raise HTTPException(409,'同一影像关联请求不能更换原件、设置或范围')
        return scene_view(old)
    scene=scoped(db,GeoScene,body.scene_id,p,True);check_version(scene,body.scene_version)
    if scene.status!='ready':raise HTTPException(409,'请先在资源与能力中保存影像RGB设置并完成本地预览')
    if scene.data.get('wms_origin'):
        from .wms import source_provenance
        source_provenance(db,p,scene)
    ev,_=path_for(db,p,scene.evidence_id)
    if ev.sha256!=scene.data['input_sha256']:raise HTTPException(409,'影像原件摘要不一致，不能关联到业务实例')
    from .geo_quality_v351 import checked_scene as checked_quality_scene
    quality=checked_quality_scene(db,p,scene)
    snapshot=capture(scene,body.roi,quality=quality)
    row=WorkflowScene(workspace_id=p.workspace_id,instance_id=identity,scene_id=scene.id,data={'snapshot':snapshot,'snapshot_hash':digest(snapshot),'request_id':body.request_id,'request_hash':signature,'actor_id':p.user_id})
    db.add(row);db.flush();audit(db,p,'workflow.scene_attached',row.id,{'instance_id':identity,'scene_generation':snapshot['scene_generation'],'cloud_calls':0});db.commit();return scene_view(row)


@router.get('/instances/{identity}/scene-associations/{request_id}')
def scene_association(identity:str,request_id:str,p:P,db:DB,expected_hash:str|None=None):
    import re
    from .models import WorkflowScene
    from .workflow_scenes import checked_input,view
    scoped(db,DomainInstance,identity,p)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}',request_id):raise HTTPException(422,'关联请求格式不正确')
    row=db.scalar(select(WorkflowScene).where(WorkflowScene.workspace_id==p.workspace_id,WorkflowScene.instance_id==identity,WorkflowScene.data['request_id'].as_string()==request_id))
    if not row:raise HTTPException(404,'此关联请求尚未保存，请用原请求重试')
    if expected_hash is not None and (not re.fullmatch(r'[a-f0-9]{64}',expected_hash) or expected_hash!=row.data['request_hash']):raise HTTPException(409,'原请求与当前恢复内容不一致，已保存关联保留，请核对原请求')
    return view(checked_input(db,p,row.id))


@router.get('/instances/{identity}/input-scenes')
def input_scenes(identity:str,p:P,db:DB):
    from .models import WorkflowScene
    from .workflow_scenes import view as scene_view
    scoped(db,DomainInstance,identity,p)
    return [scene_view(r) for r in db.scalars(select(WorkflowScene).where(WorkflowScene.workspace_id==p.workspace_id,WorkflowScene.instance_id==identity).order_by(WorkflowScene.created_at.desc()).limit(100))]


@router.get('/tasks/{identity}/scenes/{input_id}/original')
def task_scene_original(identity:str,input_id:str,p:P,db:DB,workspace_id:str|None=None):
    p=download_workspace(db,p,workspace_id)
    from fastapi.responses import FileResponse
    from .workflow_scenes import verify
    from .geo_storage import path_for
    task,_,_=execution(db,p,identity);verify(db,p,task)
    saved=next((x for x in task.data.get('scene_snapshots',[]) if x['id']==input_id),None)
    if not saved:raise HTTPException(404,'影像不在此任务固定输入中')
    ev,path=path_for(db,p,saved['snapshot']['original_evidence_id'])
    return FileResponse(path,media_type='image/tiff',filename=saved['snapshot']['name'],headers={'X-Content-SHA256':ev.sha256,'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


def spatial_task_result(db,p,identity,run_id):
    from .workflow_scenes import verify_masks
    task,_,_=execution(db,p,identity);verify_masks(db,p,task)
    if not any(r.get('output',{}).get('geo_run_id')==run_id for r in task.data['workflow_runtime']['receipts']):raise HTTPException(404,'空间候选不属于此任务的已保存回执')
    return task


@router.get('/tasks/{identity}/spatial/{run_id}/rgb')
def task_scene_rgb(identity:str,run_id:str,p:P,db:DB):
    from .geo_api import run_rgb
    spatial_task_result(db,p,identity,run_id);return run_rgb(run_id,p,db)


@router.get('/tasks/{identity}/spatial/{run_id}/masks/{mask_id}')
def task_scene_mask(identity:str,run_id:str,mask_id:str,p:P,db:DB):
    from .geo_api import run_mask
    spatial_task_result(db,p,identity,run_id);return run_mask(run_id,mask_id,p,db)


@router.get('/tasks/{identity}/spatial/{run_id}/native-geojson')
def task_scene_export(identity:str,run_id:str,p:P,db:DB):
    from .geo_api import native_export
    task=spatial_task_result(db,p,identity,run_id);response=native_export(run_id,p,db)
    # Include the real business receipts, not unrelated sample-studio review notes.
    import json
    value=json.loads(response.body);value['business_review']={'task_title':task.title,'status':task.status,'receipts':[{k:v for k,v in r.items() if k in {'step_name','note','role','at','branch','source_clock','entity_state_updated'}} for r in task.data['workflow_runtime']['receipts']]}
    response.body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode();response.headers['content-length']=str(len(response.body));return response


def comparison_task(db,p,identity,pair_id):
    from .workflow_geo_pairs import verify_receipts
    task,_,_=execution(db,p,identity);verify_receipts(db,p,task)
    if not any(r.get('output',{}).get('geo_pair_id')==pair_id for r in task.data['workflow_runtime']['receipts']):raise HTTPException(404,'双影像候选不属于此业务任务的已保存回执')
    return task


@router.get('/tasks/{identity}/comparisons/{pair_id}/artifacts/{kind}')
def task_pair_artifact(identity:str,pair_id:str,kind:str,p:P,db:DB,workspace_id:str|None=None):
    from .geo_pairs import artifact,workspace_principal
    p=workspace_principal(db,p,workspace_id)
    comparison_task(db,p,identity,pair_id);return artifact(pair_id,kind,p,db)


@router.get('/tasks/{identity}/comparisons/{pair_id}/export')
def task_pair_export(identity:str,pair_id:str,p:P,db:DB,workspace_id:str|None=None):
    import json
    from .geo_pairs import workspace_principal,export
    p=workspace_principal(db,p,workspace_id);task=comparison_task(db,p,identity,pair_id);response=export(pair_id,p,db)
    value=json.loads(response.body);value['business_review']={'task_title':task.title,'status':task.status,'receipts':[{k:v for k,v in r.items() if k in {'step_name','note','role','at','branch','source_clock','entity_state_updated'}} for r in task.data['workflow_runtime']['receipts']]}
    response.body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode();response.headers['content-length']=str(len(response.body));return response


def download_workspace(db,p,workspace_id):
    if workspace_id and workspace_id!=p.workspace_id:
        from .models import Membership
        from .security import Principal
        membership=db.scalar(select(Membership).where(Membership.workspace_id==workspace_id,Membership.user_id==p.user_id))
        if not membership:raise HTTPException(403,'没有此工作区的访问权限')
        return Principal(p.user_id,membership.workspace_id,membership.role,p.session_id)
    return p


def scene_preview(db,p,row):
    from .workflow_scenes import checked_input
    from .evidence import evidence_bytes
    checked_input(db,p,row.id);snapshot=row.data['snapshot'];saved=snapshot['preview'];image,raw=evidence_bytes(db,p,saved['evidence_id'])
    if image.sha256!=saved['sha256'] or image.data.get('scene_id')!=snapshot['scene_id'] or image.data.get('generation')!=snapshot['scene_generation'] or image.data.get('scene_sha256')!=snapshot['input_sha256']:
        raise HTTPException(409,'固定影像预览与原件或设置版本不一致')
    return Response(raw,media_type='image/png',headers={'Cache-Control':'private, no-store','X-Content-SHA256':image.sha256})


@router.get('/instances/{identity}/input-scenes/{input_id}/preview')
def input_scene_preview(identity:str,input_id:str,p:P,db:DB):
    from .workflow_scenes import checked_input
    scoped(db,DomainInstance,identity,p);row=checked_input(db,p,input_id)
    if row.instance_id!=identity:raise HTTPException(404,'影像不属于当前业务实例')
    return scene_preview(db,p,row)


@router.get('/tasks/{identity}/scenes/{input_id}/preview')
def task_scene_preview(identity:str,input_id:str,p:P,db:DB):
    from .workflow_scenes import checked_input,verify
    task,_,_=execution(db,p,identity);verify(db,p,task)
    if input_id not in [x['id'] for x in task.data.get('scene_snapshots',[])]:raise HTTPException(404,'影像不在此任务固定输入中')
    return scene_preview(db,p,checked_input(db,p,input_id))

class NativeReceiptSync(Contract):
    preview_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    entity_ids:list[str]|None=Field(default=None,min_length=1,max_length=20)

from .native_candidate_queries_v139 import Submit as NativeCandidateQueryV139

@router.get('/instances/{identity}/native-candidate-queries')
def native_candidate_queries(identity:str,p:P,db:DB):
    from .native_candidate_queries_v139 import listing
    return listing(db,p,identity)

@router.post('/instances/{identity}/native-candidate-queries',status_code=201)
def create_native_candidate_query(identity:str,body:NativeCandidateQueryV139,p:P,db:DB):
    from .native_candidate_queries_v139 import submit
    result=submit(db,p,identity,body);db.commit();return result

@router.get('/instances/{identity}/native-candidate-queries/requests/{request_id}')
def native_candidate_query_request(identity:str,request_id:str,p:P,db:DB,
        expected_hash:str=Query(pattern=r'^[a-f0-9]{64}$')):
    from .native_candidate_queries_v139 import lookup
    return lookup(db,p,identity,request_id,expected_hash)

@router.get('/instances/{identity}/native-candidate-queries/{query_id}')
def native_candidate_query(identity:str,query_id:str,p:P,db:DB):
    from .native_candidate_queries_v139 import detail
    return detail(db,p,identity,query_id)

@router.get('/instances/{identity}/native-receipts')
def native_receipt_list(identity:str,p:P,db:DB,entity_ids:list[str]|None=Query(default=None,min_length=1,max_length=20)):
    scoped(db,DomainInstance,identity,p)
    from .native_receipts import candidates,packet,latest,checked_daily,view as receipt_view
    instance=scoped(db,DomainInstance,identity,p);records=[];cut=False;values=[];current_issue=None
    if instance.status=='partially_active' and instance.active_release_id:
        try:
            records,cut=candidates(db,p,identity,entity_ids)
            values=[packet(db,p,r,e) for r in records if r['status']=='succeeded' and r['integrity_verified'] for e in r['entity_ids']]
        except HTTPException as error:
            if error.status_code not in {409,422}:raise
            records=[];cut=False;values=[]
            current_issue='当前业务配置或来源暂不可用于新归集；已保存结果和人工意见仍可读取。'
    query=select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.data['native_instance_id'].as_string()==identity)
    if entity_ids is not None:query=query.where(Observation.entity_id.in_(entity_ids))
    rows=list(db.scalars(query.order_by(Observation.created_at.desc()).limit(101)))
    # Keep a durable route to saved human work when its machine basis is damaged.
    # This is an entry hint; the review endpoint independently verifies inventory.
    from .native_video_review_models_v165 import NativeVideoReview,NativeVideoReviewCommand
    observed_ids=[row.id for row in rows[:100]]
    review_entries=set(db.scalars(select(NativeVideoReview.observation_id).where(
        NativeVideoReview.workspace_id==p.workspace_id,NativeVideoReview.instance_id==identity,
        NativeVideoReview.actor_id==p.user_id,NativeVideoReview.observation_id.in_(observed_ids))))
    review_entries.update(db.scalars(select(NativeVideoReviewCommand.observation_id).where(
        NativeVideoReviewCommand.workspace_id==p.workspace_id,NativeVideoReviewCommand.instance_id==identity,
        NativeVideoReviewCommand.operation=='submit',NativeVideoReviewCommand.observation_id.in_(observed_ids))))
    def receipt_with_review_entry(row):
        value=receipt_view(db,p,row)
        if row.id in review_entries:value['native_video_review_available']=True
        return value
    memories=list(db.scalars(select(Memory).where(Memory.workspace_id==p.workspace_id,Memory.data['schema'].as_string()=='native-multimodal-daily-v1',Memory.data['instance_id'].as_string()==identity).order_by(Memory.created_at.desc()).limit(100)))
    seen=set();daily=[]
    for mem in memories:
        day=mem.data['business_day']
        if day in seen:continue
        seen.add(day)
        try:checked_daily(db,p,mem)
        except (HTTPException,KeyError,TypeError,ValueError):
            daily.append({'day':day,'integrity_error':'每日回执需恢复，请重新归集；原处理和核查仍保留。'});continue
        items=mem.data['records'];daily.append({'day':day,'saved':len(items),'blocked':sum(bool(v['integrity_error']) for v in items),'reviewed':sum(bool(v['record'] and v['record']['review_state']=='confirmed') for v in items),'not_authoritative':True})
    return {'preview_hash':digest(values),'truncated':cut,'available':len(values),'current_issue':current_issue,'not_ready':sum(r['status']!='succeeded' or not r['integrity_verified'] for r in records),
        'candidates':[{'name':r['name'],'modality':r['modality'],'status':r['status'],'verified':r['integrity_verified'],'task_id':r['task_id'],'entity_ids':r['entity_ids']} for r in records],
        'records':[receipt_with_review_entry(r) for r in latest(rows[:100])],'history_count':len(rows[:100])-len(latest(rows[:100])),'has_more':len(rows)>100,'daily':daily}

@router.post('/instances/{identity}/native-receipts/sync')
def native_receipt_sync(identity:str,body:NativeReceiptSync,p:P,db:DB):
    from .native_receipts import sync
    result=sync(db,p,identity,body.entity_ids,expected_preview=body.preview_hash);db.commit()
    from .worker import deliver_events
    deliver_events(workspace_id=p.workspace_id)
    return result

@router.get('/instances/{identity}/native-receipts/{observation_id}/export')
def native_receipt_export(identity:str,observation_id:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p);row=scoped(db,Observation,observation_id,p)
    if row.data.get('native_instance_id')!=identity:raise HTTPException(404,'本实例没有此归集记录')
    from .native_receipts import view as receipt_view
    result=receipt_view(db,p,row)
    if result['integrity_error']:raise HTTPException(409,result['integrity_error'])
    return {'schema':'native-observation-export-v1','observation':result,'cloud_calls':0,'entity_state_updated':False}


@router.get('/instances/{identity}/objects/{entity_id}/state')
def instance_object_state(identity:str,entity_id:str,p:P,db:DB):
    instance,release=active(db,p,identity)
    entity=scoped(db,Entity,entity_id,p)
    from .entity_release_compat import require_entity
    require_entity(db,p,instance,release,entity)
    from .perception import object_state
    result=object_state(db,p,entity.id)
    db.commit()
    from .state_refresh import overview as background_state
    return {'instance_id':identity,'release_id':release.id,'state':result,'background':background_state(db,p,entity.id),'cloud_calls':0,
            'entity_state_updated':False}


class StateRefreshRequest(Contract):
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')

@router.post('/instances/{identity}/objects/{entity_id}/refresh-state')
def refresh_instance_object_state(identity:str,entity_id:str,body:StateRefreshRequest,p:P,db:DB):
    require(p,'admin','operator');instance,release=active(db,p,identity)
    entity=scoped(db,Entity,entity_id,p)
    from .entity_release_compat import require_entity
    require_entity(db,p,instance,release,entity)
    from .state_refresh import view
    from .state_projection_consumer_v266 import request_manual
    job=request_manual(db,p,identity,entity,body.request_id)
    db.commit();return view(job)


@router.get('/instances/{identity}/objects/{entity_id}/state-background')
def object_state_background(identity:str,entity_id:str,p:P,db:TaskReadDB):
    from .state_projection_admission_v267 import background
    return background(db,p,identity,entity_id)


@router.get('/instances/{identity}/objects/{entity_id}/refresh-state/requests/{request_id}')
def object_state_refresh_request(identity:str,entity_id:str,request_id:str,p:P,db:TaskReadDB):
    from .state_projection_admission_v267 import lookup
    return lookup(db,p,identity,entity_id,request_id)


@router.get('/instances/{identity}/objects/{entity_id}/state-inputs/{observation_id}')
def state_input(identity:str,entity_id:str,observation_id:str,p:P,db:DB,expected_state_hash:str=Query(min_length=64,max_length=64)):
    instance,release=active(db,p,identity);entity=scoped(db,Entity,entity_id,p)
    from .entity_release_compat import require_entity
    require_entity(db,p,instance,release,entity)
    row=scoped(db,Observation,observation_id,p)
    if row.entity_id!=entity.id:raise HTTPException(404,'原输入不属于此对象')
    from .perception import object_state
    current=object_state(db,p,entity.id);db.commit()
    if current['state_hash']!=expected_state_hash:raise HTTPException(409,'对象依据已变化，请重新核对状态后查看原输入')
    from .observation_inputs import view as input_view
    return input_view(db,p,row)


@router.get('/instances/{identity}/objects/{entity_id}/state-records/{observation_id}')
def instance_state_record(identity:str,entity_id:str,observation_id:str,p:P,db:DB,expected_state_hash:str|None=Query(default=None,pattern=r"^[a-f0-9]{64}$")):
    instance,release=active(db,p,identity)
    entity=scoped(db,Entity,entity_id,p)
    from .entity_release_compat import require_entity
    require_entity(db,p,instance,release,entity)
    observation=scoped(db,Observation,observation_id,p)
    if observation.entity_id!=entity.id:raise HTTPException(404,'登记不属于所选对象')
    if expected_state_hash:
        from .perception import object_state
        current=object_state(db,p,entity.id)
        db.commit()
        if current['state_hash']!=expected_state_hash:
            raise HTTPException(409,'状态依据已变化，请重新核对已保存状态')
        if observation.id not in current['evidence_ids']:
            raise HTTPException(409,'此登记已不属于当前采用的状态依据')
    from .observation_integrity import checked_report,CHECK_ERRORS
    try:report=checked_report(db,p,observation)
    except CHECK_ERRORS:raise HTTPException(409,'原登记、调用或文件依据未通过核对；请重新核查状态')
    return {'source_name':report['source_name'],'reported_at':observation.observed_at.isoformat(),
        'original_attributes':observation.data['attributes'],'extracted_attributes':report['attributes'],
        'original_request_saved':observation.data.get('original_request') is not None,
        'review':report['review'],'review_reason':observation.data.get('human_verification',{}).get('reason') if report['review'] else None,
        'evidence_count':len(observation.data.get('evidence_ids',[])),
        'receipt_verified':True,'source_time_verified':False,'physical_state_verified':False}


@router.get('/instances/{identity}/signals')
def instance_signals(identity:str,p:P,db:DB):
    from .models import PerceptionSignal
    from .perception_signals import view as signal_view
    instance=scoped(db,DomainInstance,identity,p)
    rows=list(db.scalars(select(PerceptionSignal).where(PerceptionSignal.workspace_id==p.workspace_id,
        PerceptionSignal.instance_id==instance.id).order_by(PerceptionSignal.created_at.desc()).limit(101)))
    return {'records':[signal_view(db,p,r) for r in rows[:100]],'has_more':len(rows)>100}


def signal_row(db,p,identity,signal_id,lock=False):
    from .models import PerceptionSignal
    scoped(db,DomainInstance,identity,p)
    row=scoped(db,PerceptionSignal,signal_id,p,lock)
    if row.instance_id!=identity:raise HTTPException(404,'异常不属于所选实例')
    return row


class SignalNote(Contract):
    version:int=Field(ge=1)
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    note:str=Field(max_length=5000)


@router.put('/instances/{identity}/signals/{signal_id}/note')
def signal_note(identity:str,signal_id:str,body:SignalNote,p:P,db:DB):
    require(p,'admin','operator');row=signal_row(db,p,identity,signal_id,True)
    from .perception_signals import view as signal_view
    signature=digest({'note':body.note,'actor':p.user_id})
    prior=next((r for r in row.data['note_requests'] if r['request_id']==body.request_id),None)
    if prior:
        if prior['hash']!=signature:raise HTTPException(409,'同一保存请求不能更换核查说明')
        return signal_view(db,p,row)
    check_version(row,body.version)
    if len(row.data['note_requests'])>=100:raise HTTPException(409,'已达到本条异常的说明保存次数上限，原记录保留')
    entry={'request_id':body.request_id,'hash':signature,'actor':p.user_id,'note':body.note,'at':now().isoformat()}
    row.version+=1;row.data={**row.data,'note':body.note,'note_requests':[*row.data['note_requests'],entry]}
    audit(db,p,'signal.note_saved',row.id);db.commit();return signal_view(db,p,row)


@router.get('/instances/{identity}/signals/{signal_id}/export')
def signal_export(identity:str,signal_id:str,p:P,db:DB):
    row=signal_row(db,p,identity,signal_id)
    from .perception_signals import checked,view as signal_view
    checked(db,p,row)
    return {'signal':signal_view(db,p,row),'basis':row.data['basis'],'notes':row.data['note_requests'],
        'business_review':row.data.get('business_review'),'entity_state_updated':False,'auto_publish':False,
        'source_task_link':row.data.get('source_task_link'),'business_review_history':row.data.get('business_review_history',[])}


@router.get('/instances/{identity}/reported-observation-options')
def reported_options(identity:str,p:P,db:DB):
    active(db,p,identity)
    from .models import Capability
    sources=[];rules=[]
    for cap in db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.status=='published')):
        try:published(db,p,cap.id)
        except HTTPException:continue
        e=cap.data['execution'];local=not e.get('side_effect') and cap.data['data_policy'].get('cloud_allowed') is False
        if not local:continue
        label={'id':cap.id,'name':cap.data['description'],'version':cap.version,'content_hash':cap.content_hash}
        if cap.kind=='source' and (e.get('adapter')=='upload' or e.get('adapter')=='builtin' and e.get('handler')=='numeric_rules'):sources.append({**label,'timezone':cap.data['specification'].get('timezone','UTC')})
        if e.get('adapter')=='builtin' and e.get('handler')=='numeric_rules' and 'perception' in cap.data['allowed_agent_roles']:
            from .reported_form import registration_form
            rules.append({**label,'rules':cap.data['specification'].get('rules',[]),**registration_form(cap.data)})
    return {'sources':sources,'rules':rules,'cloud_calls':0,'physical_device':False}


from .contracts import ObservationCreate

class InstanceReportedObservation(ObservationCreate):
    release_id:str|None=None

@router.post('/instances/{identity}/reported-observations',status_code=201)
def reported_observation(identity:str,body:InstanceReportedObservation,p:P,db:DB):
    require(p,'admin','operator');instance,release=active(db,p,identity)
    obj=scoped(db,Entity,body.entity_id,p)
    from .entity_release_compat import require_entity
    require_entity(db,p,instance,release,obj)
    from .instance_domain import require_destination_version
    require_destination_version(release,[obj],body.release_id)
    if body.startup_mode!='manual' or body.evidence_ids or body.acquisition_id or body.sensor_asset_id or body.sensor_modality:
        raise HTTPException(422,'此入口仅保存人工登记，不上传文件、控制设备或关闭补采')
    from .perception_signals import rule_contract
    source=published(db,p,body.source_id,'source');extractor=published(db,p,body.extractor_id)
    contract=rule_contract(db,p,instance,release,obj,source,extractor)
    if len(body.attributes)>30:raise HTTPException(422,'请分批登记，本次最多30个字段')
    prior=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.source_id==body.source_id,
        Observation.source_record_id==body.source_record_id,Observation.processing_version==body.processing_version))
    if prior and not prior.data.get('reported_rule_contract'):raise HTTPException(409,'该编号已有旧处理路径记录，请保留原编号并核查原记录')
    from .api import ingest_observation_record
    return ingest_observation_record(ObservationCreate(**body.model_dump(exclude={'release_id'})),p,db,reported_contract=contract)


@router.get('/instances/{identity}/reported-observations')
def reported_records(identity:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    from .reported_form import reported_record_view
    rows=list(db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,
        Observation.data['reported_rule_contract']['instance_id'].as_string()==identity)
        .order_by(Observation.created_at.desc()).limit(51)))
    values=[reported_record_view(db,p,row) for row in rows[:50]]
    return {'records':values,'has_more':len(rows)>50}


@router.post('/instances/{identity}/reported-observations/{observation_id}/retry-data-delivery')
def retry_reported_delivery(identity:str,observation_id:str,body:VersionAction,p:P,db:DB):
    """Retry delivery of the same saved output, never rerun extraction or change scope."""
    require(p,'admin','operator');scoped(db,DomainInstance,identity,p)
    row=scoped(db,Observation,observation_id,p)
    if row.data.get('reported_rule_contract',{}).get('instance_id')!=identity:
        raise HTTPException(404,'原登记不存在或不属于当前实例')
    from .models import Event
    event=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,Event.kind=='observation.data',
        Event.idempotency_key=='data:'+row.id).with_for_update())
    if event is None or row.status!='processed':raise HTTPException(409,'原处理尚未完成，不能重试接收')
    from .instance_data_receipts import receive_observation,observation_receipt
    if event.status=='received':
        observation_receipt(db,p,row)
    elif event.status=='blocked_delivery':
        check_version(row,body.expected_version)
        # Recheck original immutable bytes/output/version before releasing the
        # existing event; a retry never repairs a bad record or creates a new id.
        if receive_observation(db,p,event,row) is None:
            raise HTTPException(409,'此旧记录不属于自动数据方案接收范围，请按原处理路径核查')
        event.status='pending_delivery'
        event.data={**event.data,'previous_delivery_error':event.data.get('delivery_error'),'delivery_error':None}
        audit(db,p,'observation.data_delivery_retried',row.id,{'event_id':event.id,'reason':body.reason})
    elif event.status!='pending_delivery':raise HTTPException(409,'此接收状态不能重试，原记录保留')
    db.commit()
    from .worker import deliver_events
    deliver_events(workspace_id=p.workspace_id,observation_id=row.id,event_kind='observation.data')
    db.expire_all();row=scoped(db,Observation,observation_id,p)
    from .reported_form import reported_record_view
    return reported_record_view(db,p,row)


@router.post('/instances/{identity}/reported-observations/resolve')
def resolve_reported_observation(identity:str,body:InstanceReportedObservation,p:P,db:DB):
    """Read an original idempotency key; never create or retry an observation."""
    scoped(db,DomainInstance,identity,p)
    row=db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,
        Observation.source_id==body.source_id,Observation.source_record_id==body.source_record_id,
        Observation.processing_version==body.processing_version))
    if not row:
        return {'found':False,'request_matches':None,'record':None}
    if row.data.get('reported_rule_contract',{}).get('instance_id')!=identity:
        raise HTTPException(404,'原登记不存在或不属于当前实例')
    from .reported_form import reported_record_view
    original=body.model_dump(exclude={'evidence_ids','release_id'})
    matches=(not body.evidence_ids and row.data.get('original_request')==original
        and row.data.get('input_hash')==digest(original))
    return {'found':True,'request_matches':bool(matches),'record':reported_record_view(db,p,row)}


@router.get('/instances/{identity}/reported-observations/{observation_id}/review-requests/{request_id}')
def reported_review_request(identity:str,observation_id:str,request_id:str,p:P,db:DB):
    import re
    if not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}',request_id):
        raise HTTPException(422,'核查请求编号格式不正确')
    scoped(db,DomainInstance,identity,p)
    row=scoped(db,Observation,observation_id,p)
    if row.data.get('reported_rule_contract',{}).get('instance_id')!=identity:
        raise HTTPException(404,'原登记不存在或不属于当前实例')
    from .reported_form import review_request,current_verification
    from .observation_integrity import checked_report,CHECK_ERRORS
    result=review_request(row.data,request_id)
    try:current=current_verification(row.data,checked_report(db,p,row))
    except CHECK_ERRORS:current=None
    return {**result,'actor_matches':bool(result['review'] and result['review'].get('reviewed_by')==p.user_id),
        'current':bool(result['review'] and current==result['review']),
        'observation_id':row.id,'request_id':request_id,'version':row.version}


class SignalRestore(Contract):
    version:int=Field(ge=1)

@router.post('/instances/{identity}/signals/{signal_id}/recheck')
def signal_recheck(identity:str,signal_id:str,body:SignalRestore,p:P,db:DB):
    require(p,'admin','operator');row=signal_row(db,p,identity,signal_id,True);check_version(row,body.version)
    from .models import Event
    from .perception_signals import freeze,checked,view as signal_view
    from .observation_integrity import CHECK_ERRORS
    event=scoped(db,Event,row.event_id,p);instance=scoped(db,DomainInstance,identity,p);entity=scoped(db,Entity,row.entity_id,p)
    if digest(event.data)!=row.data['raw_event_hash']:raise HTTPException(409,'原异常通知改变，请先恢复原通知')
    if row.data.get('basis'):
        checked(db,p,row)
    else:
        if instance.active_release_id!=row.data['receiving_release_id']:raise HTTPException(409,'接收实例版本已变化，请核对原版本；不能迁移旧通知冒充新依据')
        try:basis=freeze(db,p,event,instance,entity)
        except (*CHECK_ERRORS,AttributeError):raise HTTPException(409,'原依据仍未通过核对，请先恢复原登记和提取回执')
        row.data={**row.data,'basis':basis,'basis_hash':digest(basis)}
    row.version+=1;row.data={**row.data,'receipt_issue':None}
    if row.status=='blocked_evidence':row.status='unassigned'
    audit(db,p,'signal.rechecked',row.id);db.commit();return signal_view(db,p,row)

# Explicit local model candidate filters; no cloud inference or device action.
class CandidateWatchBody(Contract):
    release_id:str|None=None
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    name:str=Field(min_length=1,max_length=120)
    entity_id:str
    profile:str
    operator:Literal['gt','gte','contains']
    threshold:StrictFloat|None=None
    needle:str|None=Field(default=None,max_length=100)
    label:str|None=Field(default=None,max_length=80)
    min_score:StrictFloat=0.0
    confirm_candidate_only:bool=False


def candidate_watch_view(row):
    return {'id':row.id,'version':row.version,'status':row.status,'definition':row.data['definition'],
       'definition_hash':row.data['definition_hash'],'request_id':row.data['request_id'],'effective_from':row.data['effective_from']}

@router.get('/instances/{identity}/candidate-watch-options')
def candidate_watch_options(identity:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    from .candidate_watches import options
    return options(db,p)

@router.get('/instances/{identity}/candidate-watches')
def candidate_watch_list(identity:str,p:P,db:DB):
    scoped(db,DomainInstance,identity,p)
    from .models import CandidateWatch
    from .sound_candidate_watches import KEY,COMMAND,ENQUEUED,SCHEMA
    from .models import Audit,Job
    from sqlalchemy import func
    sound_audit=select(Audit.id).where(Audit.workspace_id==CandidateWatch.workspace_id,
        ((Audit.action==COMMAND)&(Audit.object_id==CandidateWatch.id))|
        ((Audit.action==ENQUEUED)&(Audit.data['watch_id'].as_string()==CandidateWatch.id))).exists()
    sound_job=select(Job.id).where(Job.workspace_id==CandidateWatch.workspace_id,
        Job.idempotency_key.startswith('sound-candidate-check:'),Job.data['watch_id'].as_string()==CandidateWatch.id).exists()
    rows=list(db.scalars(select(CandidateWatch).where(CandidateWatch.workspace_id==p.workspace_id,CandidateWatch.instance_id==identity,
        ~CandidateWatch.data.has_key(KEY),func.coalesce(CandidateWatch.data['definition']['schema'].as_string(),'')!=SCHEMA,
        ~sound_audit,~sound_job).order_by(CandidateWatch.created_at.desc()).limit(101)))
    return {'records':[candidate_watch_view(r) for r in rows[:100]],'has_more':len(rows)>100}

@router.post('/instances/{identity}/candidate-watches',status_code=201)
def candidate_watch_create(identity:str,body:CandidateWatchBody,p:P,db:DB):
    from .candidate_watches import create
    row=create(db,p,identity,body);db.commit();return candidate_watch_view(row)

class CandidateWatchStatus(Contract):
    version:int=Field(ge=1)
    status:Literal['enabled','paused']


def candidate_watch_row(db,p,identity,watch_id,lock=False):
    from .models import CandidateWatch
    scoped(db,DomainInstance,identity,p)
    row=scoped(db,CandidateWatch,watch_id,p,lock)
    if row.instance_id!=identity:raise HTTPException(404,'规则不属于当前实例')
    from .sound_candidate_watches import handles_watch
    if handles_watch(db,p,row):raise HTTPException(409,'请在声音类别关注中核对和修改此规则')
    return row

@router.put('/instances/{identity}/candidate-watches/{watch_id}/status')
def candidate_watch_status(identity:str,watch_id:str,body:CandidateWatchStatus,p:P,db:DB):
    require(p,'admin','operator');row=candidate_watch_row(db,p,identity,watch_id,True);check_version(row,body.version)
    from .candidate_watches import verify_definition
    verify_definition(db,p,row,enabled=True,allow_paused=True) if body.status=='enabled' else None
    if body.status=='enabled':
        instance,release=active(db,p,identity)
        if release.id!=row.data['definition']['release_id']:raise HTTPException(409,'实例版本已变化，请重新确认规则')
    if row.status!=body.status:
        row.status=body.status;row.version+=1
        row.data={**row.data,'effective_from':now().isoformat() if body.status=='enabled' else row.data['effective_from'],
            'history':[*row.data['history'],{'status':body.status,'actor':p.user_id,'at':now().isoformat()}][-100:]}
        audit(db,p,'candidate.watch_status',row.id,{'status':body.status})
    db.commit()
    return candidate_watch_view(row)

@router.post('/instances/{identity}/candidate-watches/{watch_id}/check-existing')
def candidate_watch_existing(identity:str,watch_id:str,body:SignalRestore,p:P,db:DB):
    require(p,'admin','operator');row=candidate_watch_row(db,p,identity,watch_id,True);check_version(row,body.version)
    from .candidate_watches import verify_definition,enqueue
    value=verify_definition(db,p,row,enabled=True)
    profile=value['profile']
    condition=Observation.data['extractor_id'].as_string()==profile['model_id'] if profile['family']=='local' else Observation.data['native_packet']['source_key'].as_string().startswith(profile['native_kind']+':')
    observations=list(db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.entity_id==value['entity_id'],Observation.status=='processed',Observation.data['candidate_only'].as_boolean()==True,condition).order_by(Observation.created_at.desc()).limit(101)))
    jobs=[job.id for obs in observations[:100] if (job:=enqueue(db,p,row,obs,manual=True))]
    db.commit()
    return {'job_ids':jobs,'has_more':len(observations)>100,'cloud_calls':0}

@router.get('/instances/{identity}/candidate-watches/{watch_id}/checks')
def candidate_watch_checks(identity:str,watch_id:str,p:P,db:DB):
    from .candidate_watches import checks
    return checks(db,p,candidate_watch_row(db,p,identity,watch_id))

@router.post('/instances/{identity}/candidate-watches/{watch_id}/checks/{job_id}/retry')
def candidate_watch_retry(identity:str,watch_id:str,job_id:str,body:SignalRestore,p:P,db:DB):
    require(p,'admin','operator');watch=candidate_watch_row(db,p,identity,watch_id,True)
    from .models import Job
    from .candidate_watches import verify_definition
    verify_definition(db,p,watch,enabled=True);job=scoped(db,Job,job_id,p,True);check_version(job,body.version)
    if job.kind!='check_candidate_watch' or job.data['watch_id']!=watch.id:raise HTTPException(404,'检查不属于此规则')
    if job.status!='failed' or job.attempts>=3 or job.data['watch_version']!=watch.version:raise HTTPException(409,'此检查不能重试，请核对规则版本与恢复次数')
    job.status='queued';job.version+=1;job.data={**job.data,'error':None}
    db.commit()
    return {'id':job.id,'status':job.status,'version':job.version}

# Explicit native Task supplementary review, without restarting its workflow.
class NativeSignalLink(Contract):
    version:int=Field(ge=1)
    task_id:str
    task_version:int=Field(ge=1)
    basis_hash:str
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')

@router.post('/instances/{identity}/signals/{signal_id}/original-task')
def native_signal_link(identity:str,signal_id:str,body:NativeSignalLink,p:P,db:DB):
    require(p,'admin','operator');row=signal_row(db,p,identity,signal_id,True)
    from .native_signal_reviews import link
    value=link(db,p,row,body);db.commit();return value

@router.get('/tasks/{identity}/linked-signals')
def native_task_signals(identity:str,p:P,db:DB):
    task,_,_=execution(db,p,identity)
    from .native_signal_reviews import listing
    return listing(db,p,task)

class NativeSignalReview(Contract):
    version:int=Field(ge=1)
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    note:str=Field(max_length=5000)
    decision:Literal['abnormal','not_abnormal','uncertain']|None=None
    override_reason:str=Field(default='',max_length=500)
    revision_reason:str=Field(default='',max_length=1000)

@router.put('/instances/{identity}/signals/{signal_id}/original-task-draft')
def native_review_draft(identity:str,signal_id:str,body:NativeSignalReview,p:P,db:DB):
    row=signal_row(db,p,identity,signal_id,True)
    from .native_signal_reviews import save
    value=save(db,p,row,body);db.commit();return value

@router.post('/instances/{identity}/signals/{signal_id}/original-task-review')
def native_review_complete(identity:str,signal_id:str,body:NativeSignalReview,p:P,db:DB):
    row=signal_row(db,p,identity,signal_id,True)
    from .native_signal_reviews import save
    value=save(db,p,row,body,final=True);db.commit();return value


from .observation_corrections import Correction
@router.post('/instances/{identity}/objects/{entity_id}/state-inputs/{observation_id}/correct',status_code=201)
def correct_state_input(identity:str,entity_id:str,observation_id:str,body:Correction,p:P,db:DB):
    from .models import Job
    from .observation_corrections import create
    require(p,'admin','operator');instance,release=active(db,p,identity)
    entity=scoped(db,Entity,entity_id,p)
    from .entity_release_compat import require_entity
    require_entity(db,p,instance,release,entity)
    db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='extract:'+observation_id).with_for_update())
    row=scoped(db,Observation,observation_id,p,True)
    if row.entity_id!=entity.id or row.data.get('reported_rule_contract',{}).get('instance_id')!=identity:raise HTTPException(404,'此对象没有该人工登记')
    child=create(db,p,row,body);db.commit()
    return {'observation_id':child.id,'status':child.status,'original_observation_id':row.id,'original_preserved':True,'physical_state_verified':False}

from .workspace_needs import NeedCreate,NeedCheck

def need_row(db,p,identity,query_id,lock=False):
    from .models import DataRun
    from .workspace_needs import context
    row=scoped(db,DataRun,query_id,p,lock)
    if context(row)['instance_id']!=identity:raise HTTPException(404,'此实例没有该资料核查')
    return row

@router.get('/instances/{identity}/data-needs/options')
def data_need_options(identity:str,p:P,db:DB):
    from .workspace_needs import objects,catalog,field_labels
    instance,release,rows,truncated=objects(db,p,identity)
    def label(row):
        kind=next((x for x in release.data['snapshot']['object_types'] if x['key']==row.entity_type),None)
        return next((str(row.data[f['key']]) for f in (kind['fields'] if kind else []) if f.get('required') and f.get('kind')=='文本' and row.data.get(f['key'])),'未命名业务对象')
    return {'release_id':release.id,'businesses':[{'id':x['id'],'name':x['name']} for x in release.data['snapshot']['businesses'] if x['id'] in instance.data.get('enabled_business_ids',[])],
        'objects':[{'id':x.id,'name':label(x),'fields':catalog(db,p,x,release),'field_labels':field_labels(x,release)} for x in rows],'objects_truncated':truncated,'existing_records_only':True}

@router.get('/instances/{identity}/data-needs')
def data_needs(identity:str,p:P,db:DB):
    from .models import DataRun
    from .workspace_needs import view
    scoped(db,DomainInstance,identity,p)
    rows=list(db.scalars(select(DataRun).where(DataRun.workspace_id==p.workspace_id,
        DataRun.data['workspace_need_context']['instance_id'].as_string()==identity).order_by(DataRun.created_at.desc()).limit(101)))
    return {'items':[view(db,p,r) for r in rows[:100]],'truncated':len(rows)>100}

@router.post('/instances/{identity}/data-needs',status_code=201)
def create_data_need(identity:str,body:NeedCreate,p:P,db:DB):
    from .workspace_needs import create,view
    row=create(db,p,identity,body);db.commit();return view(db,p,row)

@router.get('/instances/{identity}/data-needs/{query_id}')
def data_need_detail(identity:str,query_id:str,p:P,db:DB):
    from .workspace_needs import view
    return view(db,p,need_row(db,p,identity,query_id),detail=True)

@router.post('/instances/{identity}/data-needs/{query_id}/check')
def check_data_need(identity:str,query_id:str,body:NeedCheck,p:P,db:DB):
    from .workspace_needs import request_check,view
    row=need_row(db,p,identity,query_id,True);request_check(db,p,row,body);db.commit();return view(db,p,row)

@router.get('/instances/{identity}/data-needs/{query_id}/export')
def export_data_need(identity:str,query_id:str,p:P,db:DB):
    import json
    from .models import DataGap,GapCheck
    from .workspace_needs import view,scoped_gap
    row=need_row(db,p,identity,query_id)
    gaps=[scoped_gap(db,p,row,k) for k in row.data.get('data_gap_ids',[])]
    payload={'schema':'workspace-data-need-export-v1','request':row.data['request'],'context':row.data['workspace_need_context'],
        'context_hash':row.data['workspace_need_context_hash'],'detail':view(db,p,row,detail=True),
        'gap_history':[{'status':g.status,'data':g.data,'checks':[x.data for x in db.scalars(select(GapCheck).where(GapCheck.workspace_id==p.workspace_id,GapCheck.gap_id==g.id).order_by(GapCheck.created_at))]} for g in gaps],
        'check_requests':row.data.get('workspace_check_requests',[]),'physical_state_verified':False,'parent_goal_completed':False}
    return Response(json.dumps(payload,ensure_ascii=False,default=str),media_type='application/json',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote('业务资料需求与依据.json')})

from .native_goals import GoalDefinition,GoalCommand,GoalAcceptance
@router.get('/tasks/{identity}/goal')
def native_goal(identity:str,p:P,db:DB):
    with report_text_parse_scope():
        return _native_goal_report_text_operation(identity,p,db)

def _native_goal_report_text_operation(identity,p,db):
    from .native_goals import view
    task,_,_=saved_execution(db,p,identity);return view(db,p,task)

@router.post('/tasks/{identity}/goal')
def define_native_goal(identity:str,body:GoalDefinition,p:P,db:DB):
    from .native_goals import define,view
    task,_,_=execution(db,p,identity,True);define(db,p,task,body);db.commit();return view(db,p,task)

@router.post('/tasks/{identity}/goal/check')
def check_native_goal(identity:str,body:GoalCommand,p:P,db:DB):
    with report_text_parse_scope():
        return _check_native_goal_report_text_operation(identity,body,p,db)

def _check_native_goal_report_text_operation(identity,body,p,db):
    from .native_goals import request_check,view
    task,_,_=execution(db,p,identity,True);request_check(db,p,task,body);db.commit();return view(db,p,task)

@router.post('/tasks/{identity}/goal/accept')
def accept_native_goal(identity:str,body:GoalAcceptance,p:P,db:DB):
    from .native_goals import accept,view
    task,_,_=execution(db,p,identity,True);accept(db,p,task,body);db.commit();return view(db,p,task)

@router.post('/tasks/{identity}/goal/stop')
def stop_native_goal(identity:str,body:GoalCommand,p:P,db:DB):
    from .native_goals import stop,view
    task,_,_=saved_execution(db,p,identity,True);stop(db,p,task,body);db.commit();return view(db,p,task)

@router.get('/tasks/{identity}/goal/export')
def export_native_goal(identity:str,p:P,db:DB):
    import json
    from .native_goals import view
    from .models import GapCheck,DataGap,DataRun
    task,_,_=saved_execution(db,p,identity)
    data={'schema':'native-business-goal-export-v1','goal':view(db,p,task),'workflow_runtime':task.data['workflow_runtime'],
        'goal_context':task.data.get('native_goal_context'),'goal_context_hash':task.data.get('native_goal_context_hash'),
        'acceptance_hash':task.data.get('native_goal_acceptance_hash'),'commands':task.data.get('native_goal_commands',[]),
        'physical_state_verified':False,'learning_auto_published':False,'production_ready':False}
    from .native_goal_acquisitions import view as acquisitions_view
    data['original_acquisitions']=acquisitions_view(db,p,task)
    query_id=task.data.get('native_goal_query_id')
    if query_id:
        run=scoped(db,DataRun,query_id,p);data['original_data_request']=run.data['request']
        data['gap_history']=[{'data':g.data,'status':g.status,'checks':[c.data for c in db.scalars(select(GapCheck).where(GapCheck.workspace_id==p.workspace_id,GapCheck.gap_id==g.id).order_by(GapCheck.created_at))]} for g in db.scalars(select(DataGap).where(DataGap.workspace_id==p.workspace_id,DataGap.query_id==query_id))]
    return Response(json.dumps(data,ensure_ascii=False,default=str),media_type='application/json',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote('原业务目标与验收依据.json')})

from .contracts import VersionAction
@router.get('/tasks/{identity}/goal/acquisitions')
def native_goal_acquisitions(identity:str,p:P,db:DB):
    from .native_goal_acquisitions import view
    task,_,_=saved_execution(db,p,identity);return view(db,p,task)

@router.post('/tasks/{identity}/goal/acquisitions/{acquisition_id}/receipt')
def native_goal_acquisition_receipt(identity:str,acquisition_id:str,body:VersionAction,p:P,db:DB):
    from .native_goal_acquisitions import reconcile
    task,_,_=saved_execution(db,p,identity);result=reconcile(db,p,task,acquisition_id,body);db.commit();return result

from .native_goals import GoalRecovery
@router.post('/tasks/{identity}/goal/requests/recover')
def recover_native_goal_request(identity:str,body:GoalRecovery,p:P,db:DB):
    from .native_goals import recover
    task,_,_=saved_execution(db,p,identity,True);result=recover(db,p,task,body);db.commit();return result

# Export the existing workspace paths plus the canonical retry adapter. api.py
# includes this router before its frozen v1 retry declaration, so HTTP callers
# (including clients without a nonce) cannot bypass the new execution guard.
from .observation_retry_api import router as observation_retry_router
from .observation_data_delivery_api_v245 import router as observation_data_delivery_router
workspace_routes=router
router=APIRouter()
router.include_router(workspace_routes)
router.include_router(observation_retry_router)
router.include_router(observation_data_delivery_router)
from .workspace_need_request_recovery_api_v248 import router as workspace_need_request_recovery_router_v248
router.include_router(workspace_need_request_recovery_router_v248)
from .pixel_change_api_v249 import router as pixel_change_router_v249
router.include_router(pixel_change_router_v249)
from .workflow_pixel_api_v250 import router as workflow_pixel_router_v250
router.include_router(workflow_pixel_router_v250)
from .native_pixel_query_v250 import router as native_pixel_query_router_v250
router.include_router(native_pixel_query_router_v250,prefix='/api/workspace')

from .signal_routing_api import router as signal_routing_router
router.include_router(signal_routing_router,prefix='/api/workspace')
from .signal_approval_api_v247 import router as signal_approval_router_v247
router.include_router(signal_approval_router_v247,prefix='/api/workspace')

from .signal_dual_api import router as signal_dual_router
router.include_router(signal_dual_router,prefix='/api/workspace')

from .graph_event_native_producer_api_v136 import router as native_graph_producer_router
router.include_router(native_graph_producer_router, prefix='/api/workspace')

from .graph_event_native_learned_geo_producer_api_v141 import router as learned_graph_producer_router
router.include_router(learned_graph_producer_router, prefix='/api/workspace')

from .simulation_loop_api_v176 import router as simulation_loop_router
router.include_router(simulation_loop_router)

from .simulation_evolution_v176 import router as simulation_evolution_router
router.include_router(simulation_evolution_router)

from .simulation_sample_recovery_v176 import router as simulation_sample_recovery_router
router.include_router(simulation_sample_recovery_router)

from .workflow_task_dispatch_api_v198 import router as task_dispatch_router
router.include_router(task_dispatch_router, prefix='/api/workspace')
from .workflow_task_archive_api_v198 import router as task_archive_router
router.include_router(task_archive_router)
from .workflow_task_packet_api_v199 import router as task_packet_router
router.include_router(task_packet_router)
from .workflow_monitor_schedule_api_v200 import router as monitor_schedule_router
router.include_router(monitor_schedule_router)

from .media_daily_delivery_api import router as media_daily_delivery_router
router.include_router(media_daily_delivery_router)

from .business_data_api import router as business_data_router
router.include_router(business_data_router)

from .runtime_document_api import router as runtime_document_router
router.include_router(runtime_document_router)

from .runtime_document_ocr_api import router as runtime_document_ocr_router
router.include_router(runtime_document_ocr_router)

from .runtime_task_document_api import router as runtime_task_document_router
router.include_router(runtime_task_document_router)

from .layered_case_api_v209 import router as layered_case_router_v209
router.include_router(layered_case_router_v209)

from .layered_case_use_api_v210 import router as layered_case_use_router_v210
router.include_router(layered_case_use_router_v210)

from .pixel_question_api_v252 import router as pixel_question_router_v252
router.include_router(pixel_question_router_v252,prefix='/api/workspace')

from .sound_api import router as sound_router
router.include_router(sound_router)

from .sound_result_settlement_api import router as sound_settlement_router
router.include_router(sound_settlement_router)

from .sound_candidate_watch_api import router as sound_candidate_watch_router
router.include_router(sound_candidate_watch_router)
from .audio_stream_api import router as audio_stream_router
router.include_router(audio_stream_router)
from .audio_stream_recovery import router as audio_stream_recovery_router
router.include_router(audio_stream_recovery_router)
from .pixel_annotation_api_v256 import router as pixel_annotation_router_v256
router.include_router(pixel_annotation_router_v256)
from .pixel_diagnostic_api_v257 import router as pixel_diagnostic_router_v257
router.include_router(pixel_diagnostic_router_v257)
from .goal_conflict_observation_api_v259 import router as goal_conflict_observation_router_v259
router.include_router(goal_conflict_observation_router_v259)

from .multimodal_question_api_v262 import router as multimodal_question_router_v262
router.include_router(multimodal_question_router_v262,prefix='/api/workspace')
