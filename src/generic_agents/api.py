import secrets
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from jsonschema.exceptions import SchemaError
from pydantic import Field, ValidationError
from .config import settings
from .db import session
from .models import (User, AuthSession, Membership, Workspace, Capability, Entity, EntityEvent,
                     DataRun, Task, Plan, Observation, Event, Issue, Audit, Memory, Job, Governance, Acquisition,
                     MonitorPolicy, ObjectState, CloudCall, Evidence, now)
from .security import (Principal, authenticated, require, verify_password, password_hash,
                       new_session,session_restore_csrf)
from .contracts import Contract, Manifest, EntityCreate, VersionAction, DataRequest, GoalSpec, digest
from .services import (register, validate_capability, publish, create_entity, update_entity,
                       scoped, published, as_dict, audit, check_version, timestamp, diagnose)
from .agents import data_agent, business_agent
from .map_access import route_guard, router as map_access_router

app = FastAPI(title="通用三智能体平台", version="0.1.0", dependencies=[Depends(route_guard)])
app.include_router(map_access_router)
DB = Annotated[object, Depends(session)]
P = Annotated[Principal, Depends(authenticated)]

from .external_capability_registry import router as external_capability_registry_router
app.include_router(external_capability_registry_router)


from .relation_api import router as relation_router
from .instance_relation_api import router as instance_relation_router
app.include_router(instance_relation_router)
from .native_planning_grants import router as native_planning_grants_router
app.include_router(native_planning_grants_router)
from .native_goal_tools import router as native_goal_tools_router
app.include_router(native_goal_tools_router)
from .native_step_planning_api import router as native_step_planning_router
app.include_router(native_step_planning_router)
from .native_step_execution_api import router as native_step_execution_router
app.include_router(native_step_execution_router)
from .native_query_proposal_api import router as native_query_proposal_router
app.include_router(native_query_proposal_router)
from .native_task_query_api import router as native_task_query_router
app.include_router(native_task_query_router)
app.include_router(relation_router)
from .query_api import router as query_router
app.include_router(query_router)
from .quality_api import router as quality_router
app.include_router(quality_router)
from .gap_api import router as gap_router
app.include_router(gap_router)
from .sensing_api import router as sensing_router
from .sensing_selection import SelectionRequirement
app.include_router(sensing_router)
from .setup_api import router as setup_router
from . import setup_understanding  # Register proposal routes before including the router.
from . import setup_recovery
app.include_router(setup_router)
from .mqtt_api import router as mqtt_router
app.include_router(mqtt_router)
from .managed_mqtt_grants import router as managed_mqtt_grants_router
app.include_router(managed_mqtt_grants_router)
from .mqtt_unit_quality_api_v357 import router as mqtt_unit_quality_router_v357
app.include_router(mqtt_unit_quality_router_v357)
from .local_rules_api import router as local_rules_router
app.include_router(local_rules_router)
from .specialist_api import router as specialist_router
from .audio_daily_api import router as audio_daily_router
app.include_router(audio_daily_router)
from .supplement_api import router as supplement_router
app.include_router(supplement_router)
from .supplement_watch_api import router as supplement_watch_router
app.include_router(supplement_watch_router)
from .evidence_question_api import router as evidence_question_router
app.include_router(evidence_question_router)
from .signal_advice_api import router as signal_advice_router
app.include_router(signal_advice_router)
from .evidence_assessment_api import router as evidence_assessment_router
app.include_router(evidence_assessment_router)
from .geo_daily_api import router as geo_daily_router
app.include_router(geo_daily_router)
from .audio_api import router as audio_router
app.include_router(audio_router)
from .speech_activity_api import router as speech_activity_router
app.include_router(speech_activity_router)
from .segmented_audio_api import router as segmented_audio_router
app.include_router(segmented_audio_router)
from .workflow_audio import router as workflow_audio_router
app.include_router(workflow_audio_router)
app.include_router(specialist_router)
from .geo_api import router as geo_router
app.include_router(geo_router)
from .geo_quality_api_v351 import router as geo_quality_router_v351
app.include_router(geo_quality_router_v351)
from .geo_pairs import router as geo_pairs_router
app.include_router(geo_pairs_router)
from .learned_geo_pairs import router as learned_geo_pairs_router
app.include_router(learned_geo_pairs_router)
from .wms_api import router as wms_router
app.include_router(wms_router)
from .geo_upload_limit import GeoUploadLimit
app.add_middleware(GeoUploadLimit)
from .video_api import router as video_router
app.include_router(video_router)
from .media_api import router as media_router
app.include_router(media_router)
from .media_gateway_registration import router as media_gateway_registration_router
app.include_router(media_gateway_registration_router)
from .onvif_discovery_api import router as onvif_discovery_router
app.include_router(onvif_discovery_router)
from .onvif_camera_enrolment_api import router as onvif_enrollment_router
app.include_router(onvif_enrollment_router)
from .camera_secure_config_api import router as camera_secure_config_router
app.include_router(camera_secure_config_router)
from .map_services_api import router as map_services_router
app.include_router(map_services_router)
from .map_workspace_grants import router as map_workspace_grants_router
app.include_router(map_workspace_grants_router)
from .ftps_gateway_api import router as ftps_gateway_router
app.include_router(ftps_gateway_router)
from .ftp_gateway_api import router as ftp_gateway_router
app.include_router(ftp_gateway_router)
from .smb_gateway_api import router as smb_gateway_router
app.include_router(smb_gateway_router)
from .managed_file_grants import router as managed_file_grants_router
app.include_router(managed_file_grants_router)
from .managed_ftps_grants import router as managed_ftps_grants_router
app.include_router(managed_ftps_grants_router)
from .ftp_geo_gateway_api import router as ftp_geo_router
app.include_router(ftp_geo_router)
from .smb_geo_gateway_api import router as smb_geo_router
app.include_router(smb_geo_router)
from .modbus_gateway_api import router as modbus_gateway_router
app.include_router(modbus_gateway_router)
from .modbus_binding import router as modbus_binding_router
app.include_router(modbus_binding_router)
from .managed_modbus_grants import router as managed_modbus_grants_router
app.include_router(managed_modbus_grants_router)
from .opcua_gateway_api import router as opcua_gateway_router
app.include_router(opcua_gateway_router)
from .managed_opcua_grants import router as managed_opcua_grants_router
app.include_router(managed_opcua_grants_router)
from .dji_dock_status_api import router as dji_dock_status_router
app.include_router(dji_dock_status_router)
from .dji_cloud_api import router as dji_cloud_router
app.include_router(dji_cloud_router)
from .sensor_automation_api import router as sensor_automation_router
app.include_router(sensor_automation_router)
from .sensor_native_packet_api import router as sensor_native_packet_router
app.include_router(sensor_native_packet_router)
from .sensor_native_analysis_api import router as sensor_native_analysis_router
app.include_router(sensor_native_analysis_router)
from .uav_simulated_missions_api import router as uav_simulated_missions_router
app.include_router(uav_simulated_missions_router)
from .map_saved_source import router as map_saved_source_router
app.include_router(map_saved_source_router)
from .wmts_position_api import router as wmts_position_router
app.include_router(wmts_position_router)
from .feature_gateway_api import router as feature_gateway_router
app.include_router(feature_gateway_router)
from .video_read_boundary_api import router as video_read_boundary_router
app.include_router(video_read_boundary_router)
from .media_monitor_api import router as media_monitor_router
app.include_router(media_monitor_router)
from .video_domain_api import router as video_domain_router
app.include_router(video_domain_router)
from .continuous_media_api import router as continuous_router
app.include_router(continuous_router)
from .instance_api import router as instance_router
app.include_router(instance_router)
from .public_observations import router as public_observations_router
app.include_router(public_observations_router)
from .instance_observation_catalog import router as instance_observation_catalog_router
app.include_router(instance_observation_catalog_router)
from .instance_live_video import router as instance_live_video_router
app.include_router(instance_live_video_router)
from .mqtt_domain_options import router as mqtt_domain_options_router
app.include_router(mqtt_domain_options_router)
from .workspace_api import router as workspace_router
app.include_router(workspace_router)
from .native_cases_api import router as native_cases_router
app.include_router(native_cases_router)
from .native_method_suggestions_api import router as native_method_suggestions_router
app.include_router(native_method_suggestions_router)
from fastapi.staticfiles import StaticFiles
setup_build = settings().setup_build_dir or Path(__file__).resolve().parents[2] / 'design/resource-studio/dist/runtime'
if setup_build.is_dir():
    app.mount('/setup', StaticFiles(directory=setup_build, html=True), name='setup')


@app.middleware("http")
async def private_api_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(('/api/','/assets/')) or request.url.path=='/':
        # Credentials, evidence and workspace records must not survive sign-out in shared caches.
        response.headers["Cache-Control"] = "private, no-store"
    return response


@app.exception_handler(IntegrityError)
async def constraint_error(request, error):
    return JSONResponse(status_code=409, content={"detail": "记录已存在或关联数据发生变化"})


@app.exception_handler(SchemaError)
async def schema_error(request, error):
    return JSONResponse(status_code=422, content={"detail": "JSON Schema定义不合法"})


class Login(Contract):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=300)


@app.get("/health")
def health(db: DB):
    from sqlalchemy import text
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "postgresql", "version": "0.1.0"}


@app.post("/api/login")
def login(body: Login, request: Request, response: Response, db: DB):
    origin = request.headers.get("origin")
    if origin and origin not in settings().allowed_origins.split(","):
        raise HTTPException(403, "不受信任的来源")
    # Durable account-level rate limiting, including unknown usernames.
    from .contracts import digest
    from .models import uid
    key = "login:" + digest(body.username)
    limiter = db.scalar(select(Job).where(Job.idempotency_key == key, Job.kind == "login_limit").with_for_update())
    workspace = db.scalar(select(Workspace).order_by(Workspace.created_at).limit(1))
    if not workspace:
        raise HTTPException(503, "请先通过命令行创建管理员")
    if not limiter:
        limiter = Job(id=uid(), workspace_id=workspace.id, idempotency_key=key, kind="login_limit",
                      data={"count": 0, "since": now().isoformat()}, status="internal")
        db.add(limiter); db.flush()
    since = timestamp(limiter.data["since"])
    count = limiter.data["count"] if now() - since < timedelta(minutes=5) else 0
    if count >= 10:
        raise HTTPException(429, "登录尝试过多，请稍后重试")
    limiter.data = {"count": count + 1, "since": since.isoformat() if count else now().isoformat()}
    user = db.scalar(select(User).where(User.username == body.username, User.active.is_(True)))
    # Constant work for nonexistent users, without recording password values.
    encoded = user.password_hash if user else password_hash("invalid-account-password")
    valid = verify_password(encoded, body.password)
    if not user or not valid:
        db.commit(); raise HTTPException(401, "用户名或密码错误")
    limiter.data = {"count": 0, "since": now().isoformat()}
    _, token, csrf = new_session(db, user); db.commit()
    response.set_cookie(settings().session_cookie_name, token, httponly=True, secure=settings().cookie_secure,
                        samesite="strict", max_age=settings().session_hours * 3600)
    return {"csrf_token": csrf, "username": user.username}


@app.get("/api/me")
def me(p: P, db: DB):
    user = db.get(User, p.user_id)
    memberships = db.scalars(select(Membership).where(Membership.user_id == p.user_id)).all()
    return {"user_id":p.user_id,"username": user.username, "role": p.role, "workspace_id": p.workspace_id,
            "workspaces": [{"id": m.workspace_id, "name": db.get(Workspace, m.workspace_id).name,
                            "role": m.role} for m in memberships]}


@app.get('/api/session/restore')
def restore_session(request:Request,response:Response,db:DB):
    if 'authorization' in request.headers:
        raise HTTPException(401,'Cookie session required')
    if request.headers.get('x-session-restore')!='1':
        raise HTTPException(403,'Explicit session restore required')
    origin=request.headers.get('origin')
    if origin is not None and (origin in {'','null','*'} or origin not in settings().allowed_origins.split(',')):
        raise HTTPException(403,'Origin not allowed')
    p=authenticated(request,db)
    row=db.get(AuthSession,p.session_id);user=db.get(User,p.user_id)
    response.headers['Cache-Control']='private, no-store'
    return {'csrf_token':session_restore_csrf(request.cookies[settings().session_cookie_name],row),
        'username':user.username,'user_id':p.user_id,'workspace_id':p.workspace_id,'role':p.role}


@app.post("/api/logout")
def logout(response: Response, p: P, db: DB):
    db.get(AuthSession, p.session_id).revoked = True; db.commit(); response.delete_cookie(settings().session_cookie_name)
    return {"status": "signed_out"}


@app.post("/api/tokens")
def api_token(p: P, db: DB):
    _, token, _ = new_session(db, db.get(User, p.user_id), "api")
    audit(db, p, "session.token_created", p.user_id); db.commit()
    return {"token": token, "expires_in_hours": settings().session_hours}


class NewUser(Contract):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=300)
    role: str = Field(pattern="^(admin|operator|viewer)$")


@app.post("/api/users", status_code=201)
def add_user(body: NewUser, p: P, db: DB):
    require(p, "admin")
    user = User(username=body.username, password_hash=password_hash(body.password)); db.add(user); db.flush()
    db.add(Membership(user_id=user.id, workspace_id=p.workspace_id, role=body.role))
    audit(db, p, "user.create", user.id, {"role": body.role}); db.commit()
    return {"id": user.id, "username": user.username, "role": body.role}


@app.get("/api/capabilities")
def capabilities(p: P, db: DB):
    from .map_access import filter_capabilities
    rows = list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id)
                           .order_by(Capability.created_at.desc()).limit(100)))
    return [as_dict(x) for x in filter_capabilities(db, p, rows)]


@app.post("/api/capabilities", status_code=201)
def register_capability(body: Manifest, p: P, db: DB):
    row = register(db, p, body); db.commit(); return as_dict(row)


@app.post("/api/capabilities/{object_id}/validate")
def validate_registration(object_id: str, body: VersionAction, p: P, db: DB):
    row = validate_capability(db, p, object_id, body.expected_version); db.commit(); return as_dict(row)


@app.post("/api/capabilities/{object_id}/publish")
def publish_registration(object_id: str, body: VersionAction, p: P, db: DB):
    row = publish(db, p, object_id, body.expected_version, body.reason); db.commit(); return as_dict(row)


@app.get("/api/entities")
def entities(p: P, db: DB):
    return [as_dict(x) for x in db.scalars(select(Entity).where(Entity.workspace_id == p.workspace_id)
                                         .order_by(Entity.created_at.desc()).limit(100))]


@app.post("/api/entities", status_code=201)
def add_entity(body: EntityCreate, p: P, db: DB):
    row = create_entity(db, p, body); db.commit(); return as_dict(row)


class EntityUpdate(VersionAction):
    attributes: dict


@app.patch("/api/entities/{object_id}")
def edit_entity(object_id: str, body: EntityUpdate, p: P, db: DB):
    row = update_entity(db, p, object_id, body.attributes, body.expected_version, body.reason)
    db.commit(); return as_dict(row)


@app.get("/api/entities/{object_id}/history")
def entity_history(object_id: str, p: P, db: DB):
    scoped(db, Entity, object_id, p)
    return [as_dict(x) for x in db.scalars(select(EntityEvent).where(
        EntityEvent.workspace_id == p.workspace_id, EntityEvent.entity_id == object_id)
        .order_by(EntityEvent.created_at.desc()).limit(100))]


@app.post("/api/data/queries")
def data_query(body: DataRequest, p: P, db: DB):
    if body.mode == "cloud" or body.tool_steps:
        require(p, "admin", "operator")
    for entity_id in body.entity_ids:
        scoped(db, Entity, entity_id, p)
    from .data_requirements import prepare_request
    prepared=prepare_request(db,p,body.model_dump())
    # Persist the human request before model work. Cloud work survives API restarts.
    run = DataRun(workspace_id=p.workspace_id, question=body.question, owner_id=p.user_id,
                  status="queued", data={"request": prepared})
    db.add(run); db.flush()
    if ((body.mode == "cloud" and body.model_profile_id) or body.tool_steps) and not prepared['clarifications']:
        if body.model_profile_id:published(db, p, body.model_profile_id, "foundation_model")
        db.add(Job(workspace_id=p.workspace_id, kind="data_query", idempotency_key="query:"+run.id,
                   data={"query_id": run.id, "actor_id": p.user_id}))
    else:
        state = data_agent(db, p, prepared, run.id)
        run.status = state["status"]; run.data = state
    audit(db, p, "data.query", run.id, {"status": run.status}); db.commit()
    return as_dict(run)


@app.get("/api/data/queries")
def data_queries(p: P, db: DB):
    return [as_dict(x) for x in db.scalars(select(DataRun).where(DataRun.workspace_id == p.workspace_id)
                                         .order_by(DataRun.created_at.desc()).limit(100))]


@app.post("/api/data/diagnose")
def data_diagnose(p: P, db: DB):
    result = diagnose(db, p); db.commit(); return result


class TaskCreate(Contract):
    title: str = Field(min_length=1, max_length=300)
    package_id: str
    entity_ids: list[str] = Field(min_length=1, max_length=30)
    fields: list[str] = Field(default_factory=list, max_length=30)
    observation_mode: Literal["auto", "existing_only", "allow_new"] = "auto"
    goal_spec: GoalSpec | None = None


@app.post("/api/tasks", status_code=201)
def add_task(body: TaskCreate, p: P, db: DB):
    require(p, "admin", "operator"); published(db, p, body.package_id, "domain_package")
    for entity_id in body.entity_ids:
        entity = scoped(db, Entity, entity_id, p)
        if entity.schema_capability_id != body.package_id:
            raise HTTPException(422, "任务对象不属于所选业务包")
    row = Task(workspace_id=p.workspace_id, title=body.title, package_id=body.package_id,
               owner_id=p.user_id, data=body.model_dump(exclude={"title", "package_id"}))
    db.add(row); db.flush()
    if body.goal_spec:
        from .goals import initialize
        initialize(db,p,row,body.goal_spec.model_dump(),'创建任务时提交结构化目标')
    else:
        row.data={**row.data,'goal_spec':None,'goal_clarifications':['请确认目的、日期范围与完成标准，再生成正式计划']}
    audit(db, p, "task.create", row.id); db.commit(); return as_dict(row)


class GoalConfirmation(VersionAction):
    goal_spec: GoalSpec


class GoalInterpretation(VersionAction):
    model_profile_id:str
    business_timezone:str | None = Field(default=None,max_length=100)


@app.post('/api/tasks/{object_id}/goal/interpret')
def interpret_task_goal(object_id:str,body:GoalInterpretation,p:P,db:DB):
    require(p,'admin','operator')
    task=scoped(db,Task,object_id,p,True)
    if task.data.get('workflow_runtime'):raise HTTPException(409,'请在原业务工作区管理目标和验收，不能切换旧计划执行器')
    check_version(task,body.expected_version)
    if task.status not in {'draft','needs_clarification','goal_review','blocked_model','review','waiting_evidence','planning_failed'}:
        raise HTTPException(409,'当前目标不能重新解析，请先完成活动执行或核对未知动作')
    if any(r.get('step',{}).get('side_effect') and r.get('result',{}).get('status') in {'running','unknown'}
           for r in task.data.get('step_receipts',{}).values()):raise HTTPException(409,'未知动作尚未核对，不能修改目标')
    published(db,p,body.model_profile_id,'foundation_model')
    from sqlalchemy import func
    plans=select(Plan.id).where(Plan.task_id==task.id,Plan.workspace_id==p.workspace_id)
    if db.scalar(select(Job.id).where(Job.workspace_id==p.workspace_id,Job.status=='running',
        Job.lease_until > func.clock_timestamp(),Job.data['plan_id'].as_string().in_(plans)).limit(1)):
        raise HTTPException(409,'旧执行仍有有效租约，请等待结束后解析新目标')
    from .temporal import resolve
    resolve({'timezone':body.business_timezone})
    request={'request_id':secrets.token_hex(12),'model_profile_id':body.model_profile_id,
        'business_timezone':body.business_timezone,'requested_at':now().isoformat()}
    task.data={**task.data,'goal_interpretation_request':request};task.status='interpreting_goal';task.version+=1
    db.add(Job(workspace_id=p.workspace_id,kind='interpret_goal',idempotency_key='goal:'+task.id+':'+request['request_id'],
        data={'task_id':task.id,'request':request,'actor_id':p.user_id}))
    audit(db,p,'goal.interpret_requested',task.id,{'reason':body.reason});db.commit();return as_dict(task)


class GoalDraft(VersionAction):
    draft:dict


@app.put('/api/tasks/{object_id}/goal/draft')
def save_goal_draft(object_id:str,body:GoalDraft,p:P,db:DB):
    require(p,'admin','operator')
    task=scoped(db,Task,object_id,p,True)
    if task.data.get('workflow_runtime'):raise HTTPException(409,'请在原业务工作区管理目标和验收，不能切换旧计划执行器')
    check_version(task,body.expected_version)
    if task.status in {'queued','running','interpreting_goal','completed','cancelled','awaiting_acceptance','planning','waiting_data'}:
        raise HTTPException(409,'活动执行或已结束目标不能修改草稿')
    import json
    if len(json.dumps(body.draft).encode())>30000:raise HTTPException(422,'目标草稿超出大小上限')
    task.data={**task.data,'goal_draft':body.draft};task.version+=1
    audit(db,p,'goal.draft_saved',task.id,{'reason':body.reason});db.commit();return as_dict(task)


@app.put('/api/tasks/{object_id}/goal')
def confirm_task_goal(object_id:str,body:GoalConfirmation,p:P,db:DB):
    from .goals import confirm
    row=scoped(db,Task,object_id,p,True)
    if row.data.get('workflow_runtime'):raise HTTPException(409,'请在原业务工作区管理目标和验收，不能切换旧计划执行器')
    confirm(db,p,row,body.expected_version,body.reason,body.goal_spec.model_dump());db.commit();return as_dict(row)


class ModelSelection(VersionAction):
    model_profile_id: str | None = None
    observation_mode: Literal["auto", "existing_only", "allow_new"] | None = None


@app.post("/api/tasks/{object_id}/plan")
def generate_plan(object_id: str, body: ModelSelection, p: P, db: DB):
    from .planning import request
    task=scoped(db,Task,object_id,p,True)
    if task.data.get('workflow_runtime'):raise HTTPException(409,'请在原业务工作区管理目标和验收，不能切换旧计划执行器')
    row=request(db,p,task,body.expected_version,body.reason,body.model_profile_id,body.observation_mode)
    db.commit();return as_dict(row)


@app.get('/api/plans/{object_id}')
def read_plan(object_id:str,p:P,db:DB):
    from .native_query_proposals import public_plan
    return public_plan(as_dict(scoped(db,Plan,object_id,p)))


@app.post('/api/tasks/{object_id}/retry_execution')
def retry_task_execution(object_id:str,body:VersionAction,p:P,db:DB):
    from .execution import retry_execution
    row=retry_execution(db,p,object_id,body.expected_version,body.reason);db.commit();return as_dict(row)


@app.post('/api/invocations/{object_id}/reconcile')
def reconcile_tool_invocation(object_id:str,body:VersionAction,p:P,db:DB):
    from .tool_recovery import reconcile
    row=reconcile(db,p,object_id,body.expected_version,body.reason);db.commit();return as_dict(row)


@app.get('/api/invocations/{object_id}')
def tool_invocation_state(object_id:str,p:P,db:DB):
    from .models import Invocation
    row=scoped(db,Invocation,object_id,p)
    return {'id':row.id,'version':row.version,'status':row.status,'error':row.data.get('error'),
        'receipt_checks':row.data.get('receipt_checks',[])}


from .contracts import ObservationCreate

@app.post("/api/observations", status_code=201)
def ingest_observation(body: ObservationCreate, p: P, db: DB):
    return ingest_observation_record(body,p,db)


def ingest_observation_record(body,p,db,*,reported_contract=None,mqtt_receipt_id=None,commit=True):
    require(p, "admin", "operator")
    source = published(db, p, body.source_id, "source"); scoped(db, Entity, body.entity_id, p)
    from zoneinfo import ZoneInfo
    observed_at = timestamp(body.observed_at)
    extractor = published(db, p, body.extractor_id)
    mqtt_input=source.namespace=='platform_mqtt' or bool(source.data['specification'].get('mqtt_binding_id'))
    if mqtt_input:
        if not mqtt_receipt_id:raise HTTPException(409,'MQTT观测必须由已保存原收件生成')
        from .mqtt_observations import verify_request,binding_hash
        mqtt_receipt,mqtt_binding=verify_request(db,p,mqtt_receipt_id,body)
    elif mqtt_receipt_id:raise HTTPException(409,'收件来源与MQTT绑定不一致')
    local_file=bool(body.evidence_ids) and extractor.data['execution'].get('adapter')=='local_specialist'
    if observed_at is None and not local_file:raise HTTPException(422, "必须提供观测时间")
    tz=ZoneInfo(source.data['specification'].get('timezone','UTC'))
    if local_file:
        from .local_observations import file_inputs
        local_original=file_inputs(db,p,extractor,body.evidence_ids,body.attributes)
    business_day=str((observed_at if observed_at else local_original.created_at).astimezone(tz).date())
    if body.business_day!=business_day:raise HTTPException(422,'业务日期与来源时间或原件上传日期不一致')
    if local_file and (body.acquisition_id or body.sensor_asset_id or body.sensor_modality):
        raise HTTPException(422,'本地上传候选不能作为设备补采或现场测量回执')
    if body.acquisition_id:
        acq = scoped(db, Acquisition, body.acquisition_id, p)
        if acq.entity_id != body.entity_id or acq.capability_id != body.source_id:
            raise HTTPException(409, "此观测不能关闭指定补采请求")
    extractor = published(db, p, body.extractor_id)
    if "perception" not in extractor.data["allowed_agent_roles"]:
        raise HTTPException(403, "提取器未授权感知智能体")
    if not body.attributes and not body.evidence_ids:
        raise HTTPException(422, "请提供观测属性或已保存的文件证据")
    if body.evidence_ids:
        if extractor.data["execution"].get("adapter") != "cloud_extractor" and not local_file and not mqtt_input:
            raise HTTPException(409, "所选提取器没有已实现的文件证据处理能力")
        for evidence_id in body.evidence_ids:
            scoped(db, Evidence, evidence_id, p)
    original_key = select(Observation).where(Observation.workspace_id == p.workspace_id,
        Observation.source_id == body.source_id, Observation.source_record_id == body.source_record_id,
        Observation.processing_version == body.processing_version)
    existing = db.scalar(original_key)
    record_hash = digest(body.model_dump(exclude={"evidence_ids"} if not body.evidence_ids else set()))
    value = body.model_dump(exclude={"source_id", "source_record_id", "processing_version", "entity_id",
                                   "observed_at", "business_day"})
    if mqtt_input:
        value.update({'mqtt_receipt_id':mqtt_receipt.id,'mqtt_binding_id':mqtt_binding.id,
                      'mqtt_binding_hash':binding_hash(mqtt_binding),'source_time_verified':False,
                      'date_basis':'reported_capture_time'})
    if local_file:
        value.update({'candidate_only':True,'local_capability_hash':digest(extractor.data),
            'local_source_hash':digest(source.data),'local_original_sha256':local_original.sha256,
            'date_basis':'reported_capture_time_unverified' if observed_at else 'file_upload_request'})
        managed=source.data['specification'].get('managed_instance')
        if managed:
            from .instance_api import active
            instance,release=active(db,p,managed);entity=scoped(db,Entity,body.entity_id,p)
            if entity.namespace!=instance.namespace or entity.schema_capability_id!=release.package_id:
                raise HTTPException(409,'对象不属于上传来源的当前实例版本')
            value['local_instance']={'id':instance.id,'release_id':release.id}
    sensor_id=body.sensor_asset_id
    if body.acquisition_id and acq.data.get('sensor_asset_id'):
        if sensor_id and sensor_id!=acq.data['sensor_asset_id']:raise HTTPException(409,'观测设备与原补采资产不匹配')
        sensor_id=acq.data['sensor_asset_id']
    if sensor_id:
        from .models import SensorAsset
        asset=scoped(db,SensorAsset,sensor_id,p)
        if asset.source_id!=body.source_id or body.entity_id not in asset.data['coverage_entity_ids']:raise HTTPException(409,'设备来源或登记覆盖不匹配')
        snap=acq.data.get('dispatch_asset') if body.acquisition_id else None
        snap=snap or asset.data
        modality=body.sensor_modality or (snap['modalities'][0] if len(snap['modalities'])==1 else None)
        if modality not in snap['modalities']:raise HTTPException(422,'请指定属于该设备登记能力的观测模态')
        from .device_identity import binding as device_binding
        link=device_binding(db,p,asset.id)
        identity_meta={'device_id':acq.data.get('sensor_device_id'),'device_version':acq.data.get('sensor_device_version'),'binding_version':acq.data.get('device_binding_version')} if body.acquisition_id else {'device_id':link.device_id if link else None,'binding_version':link.version if link else None}
        value['sensor_evidence']={'asset_id':asset.id,'asset_version':acq.data['sensor_asset_version'] if body.acquisition_id and acq.data.get('sensor_asset_id') else asset.version,
            'modality':modality,'resolution_m':snap['resolution_m'],'basis':'registered_asset_declaration_not_independently_measured',**identity_meta}
    elif body.sensor_modality:raise HTTPException(422,'指定观测模态时须绑定传感资产')
    def original_saved(row):
        from .observation_original_replay_v128 import validate
        if row.data.get('input_hash') != record_hash:
            raise HTTPException(409, '该观测编号已用于不同内容')
        actual = validate(db, p, row, body)
        from .observation_submission_route_v128 import validate as validate_route
        mqtt = ({key: value[key] for key in ('mqtt_receipt_id', 'mqtt_binding_id', 'mqtt_binding_hash')}
            if mqtt_input else None)
        local = ({key: value[key] for key in ('candidate_only', 'local_capability_hash',
            'local_source_hash', 'local_original_sha256', 'date_basis', 'local_instance') if key in value}
            if local_file else None)
        validate_route(db, p, actual, reported_contract=reported_contract, mqtt=mqtt, local=local)
        return as_dict(actual)
    if existing:
        return original_saved(existing)
    if body.acquisition_id:
        # Serialize a fresh input with the original acquisition closing.  A
        # readback above acquires no action lock and never changes its state.
        acq = scoped(db, Acquisition, body.acquisition_id, p, True)
        if acq.status != 'awaiting_observation' or acq.entity_id != body.entity_id or acq.capability_id != body.source_id:
            raise HTTPException(409, '此观测不能关闭指定补采请求')
    row = Observation(workspace_id=p.workspace_id, source_id=body.source_id,
        source_record_id=body.source_record_id, processing_version=body.processing_version,
        entity_id=body.entity_id, observed_at=timestamp(body.observed_at), business_day=body.business_day,
        data={**value, "input_hash": record_hash, "submitted_by": p.user_id,
              "original_request":body.model_dump(exclude={"evidence_ids"} if not body.evidence_ids else set()),
              **({"reported_rule_contract":reported_contract} if reported_contract else {})})
    try:
        with db.begin_nested():
            db.add(row); db.flush()
    except IntegrityError as error:
        if getattr(error.orig, 'sqlstate', None) != '23505':raise
        winner = db.scalar(original_key.execution_options(populate_existing=True))
        if winner is None:raise
        # PostgreSQL has waited for the winning transaction.  Read and verify
        # that exact original instead of creating another job or repairing it.
        return original_saved(winner)
    db.add(Job(workspace_id=p.workspace_id, kind="process_mqtt_observation" if mqtt_input else "process_reported_observation" if reported_contract else "process_local_observation" if local_file else "process_observation", idempotency_key="extract:"+row.id,
               data={"observation_id": row.id, "actor_id": p.user_id, "extractor_id": body.extractor_id}))
    from .state_refresh import source_changed
    source_changed(db,p,row,'新登记已保存，等待处理')
    audit(db, p, "observation.ingest", row.id)
    if commit:db.commit()
    return as_dict(row)


@app.get("/api/overview")
def overview(p: P, db: DB):
    def rows(model):
        result=[as_dict(x) for x in db.scalars(select(model).where(model.workspace_id == p.workspace_id)
            .order_by(model.created_at.desc()).limit(100))]
        if model in {Task, Plan}:
            from .native_query_proposals import public_task, public_plan
            result=[(public_task(x) if model is Task else public_plan(x)) for x in result]
        if model is Memory:
            from .relations import case_valid
            for item in result:
                item['relation_reuse_blocked']=not case_valid(db,p,item['data'].get('evidence',{}))
        return result
    return {"tasks": rows(Task), "plans": rows(Plan), "observations": rows(Observation),
            "events": rows(Event), "issues": rows(Issue), "memories": rows(Memory),
            "governance": rows(Governance), "acquisitions": rows(Acquisition),
            "monitor_policies": rows(MonitorPolicy), "object_states": rows(ObjectState),
            "cloud_calls": rows(CloudCall),
            "jobs": [r for r in rows(Job) if r["kind"] not in {"login_limit", "cloud_budget"}]}


@app.post("/api/plans/{object_id}/approve")
def plan_approval(object_id: str, body: VersionAction, p: P, db: DB):
    from .execution import approve_plan
    row = approve_plan(db, p, object_id, body.expected_version, body.reason); db.commit(); return as_dict(row)


@app.post("/api/tasks/{object_id}/accept")
def task_acceptance(object_id: str, body: VersionAction, p: P, db: DB):
    task=scoped(db,Task,object_id,p)
    if task.data.get('workflow_runtime'):raise HTTPException(409,'请在原业务工作区管理目标和验收，不能切换旧计划执行器')
    from .execution import accept_task
    row = accept_task(db, p, object_id, body.expected_version, body.reason); db.commit(); return as_dict(row)


@app.post("/api/tasks/{object_id}/cancel")
def cancel_task(object_id: str, body: VersionAction, p: P, db: DB):
    require(p, "admin", "operator")
    row = scoped(db, Task, object_id, p, True); check_version(row, body.expected_version)
    if row.data.get('workflow_runtime'):raise HTTPException(409,'请在原业务工作区管理目标和验收，不能切换旧计划执行器')
    if row.status in {"completed", "cancelled"}:
        raise HTTPException(409, "任务已结束")
    row.status = "cancelled"; row.version += 1
    if row.data.get('current_plan_id'):
        plan=scoped(db,Plan,row.data['current_plan_id'],p,True)
        if plan.status in {'queued','running','waiting_data'} and plan.data.get('planning_job_id'):
            plan.status='cancelled';plan.version+=1
            job=scoped(db,Job,plan.data['planning_job_id'],p)
            if job.status=='queued':job.status='cancelled'
    audit(db, p, "task.cancel", row.id, {"reason": body.reason}); db.commit(); return as_dict(row)


class RepairProposal(Contract):
    attributes: dict
    expected_entity_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


@app.post("/api/issues/{object_id}/repair")
def propose_governance(object_id: str, body: RepairProposal, p: P, db: DB):
    from .execution import propose_repair
    row = propose_repair(db, p, object_id, body.attributes, body.expected_entity_version, body.reason)
    db.commit(); return as_dict(row)


@app.post("/api/governance/{object_id}/approve")
def governance_approval(object_id: str, body: VersionAction, p: P, db: DB):
    from .execution import approve_repair
    row = approve_repair(db, p, object_id, body.expected_version, body.reason); db.commit(); return as_dict(row)


class MemoryReview(VersionAction):
    decision: str = Field(pattern="^(trust|reject)$")
    valid_until: str | None = None


@app.post("/api/memories/{object_id}/review")
def review_memory(object_id: str, body: MemoryReview, p: P, db: DB):
    require(p, "admin", "operator")
    row = scoped(db, Memory, object_id, p, True); check_version(row, body.expected_version)
    if row.data.get('type') in {'native_procedure_case', 'native_method_evaluation'}:
        raise HTTPException(409, '请回到原业务任务的方法核查与修订入口操作，保留原权限、版本和审核记录')
    if row.status != "candidate":
        raise HTTPException(409, "此记忆已经审核")
    if body.decision == "trust":
        if row.data.get("type") != "verified_case" or not row.task_id:
            raise HTTPException(409, "观测提取及每日摘要不是已验证经验，不能直接转为可信案例")
        task = scoped(db, Task, row.task_id, p)
        if task.status != "completed":
            raise HTTPException(409, "关联任务尚未验收")
        from .relations import case_valid
        if not case_valid(db,p,row.data.get('evidence',{})):
            raise HTTPException(409,'关系依据已变化，请重新核查任务，不能把旧案例确认为可信')
        row.valid_until = timestamp(body.valid_until)
        row.status = "trusted"
    else:
        row.status = "rejected"
    row.data = {**row.data, "reviewed_by": p.user_id, "review_reason": body.reason}; row.version += 1
    if body.decision == "trust":
        db.add(Job(workspace_id=p.workspace_id,kind="propose_learning",idempotency_key="learning-proposal:"+row.id,
                   data={"memory_id":row.id,"actor_id":p.user_id}))
    audit(db, p, "memory.review", row.id, {"decision": body.decision}); db.commit(); return as_dict(row)


class MonitorCreate(Contract):
    sensor_asset_ids:list[str]=Field(default_factory=list,max_length=100)
    selection_requirement:SelectionRequirement|None=None
    timezone:str="UTC"
    title: str = Field(min_length=1, max_length=200)
    entity_ids: list[str] = Field(min_length=1, max_length=100)
    source_ids: list[str] = Field(min_length=1, max_length=10)
    fields: list[str] = Field(min_length=1, max_length=30)
    max_age_seconds: int = Field(default=86400, ge=60, le=2592000)
    cooldown_seconds: int = Field(default=3600, ge=300, le=2592000)
    max_acquisitions_per_day: int = Field(default=2, ge=1, le=100)
    max_duration_seconds: int = Field(default=300, ge=1, le=3600)
    valid_until: str


@app.post("/api/monitor-policies", status_code=201)
def add_monitor(body: MonitorCreate, p: P, db: DB):
    require(p, "admin", "operator")
    valid_until = timestamp(body.valid_until)
    if not valid_until or valid_until <= now():
        raise HTTPException(422, "监测授权有效期必须为未来时间")
    for entity_id in body.entity_ids:
        scoped(db, Entity, entity_id, p)
    for source_id in body.source_ids:
        published(db, p, source_id, "source")
    from .sensing_selection import validate_policy
    validate_policy(db,p,body.model_dump())
    value=body.model_dump()
    if body.selection_requirement:
        from .device_identity import scope_snapshot
        value["device_scope"]=scope_snapshot(db,p,body.sensor_asset_ids)
    row = MonitorPolicy(workspace_id=p.workspace_id, data={**value,
        "valid_until": valid_until.isoformat(), "proposed_by": p.user_id})
    db.add(row); db.flush(); audit(db, p, "monitor.propose", row.id); db.commit(); return as_dict(row)


@app.post("/api/monitor-policies/{object_id}/approve")
def monitor_approval(object_id: str, body: VersionAction, p: P, db: DB):
    from .perception import approve_monitor
    row = approve_monitor(db, p, object_id, body.expected_version, body.reason); db.commit(); return as_dict(row)


@app.post("/api/monitor-policies/{object_id}/scan")
def scan_monitor(object_id: str, p: P, db: DB):
    require(p, "admin", "operator")
    from .perception import propose_acquisitions
    created = propose_acquisitions(db, p, object_id); db.commit(); return {"acquisition_ids": created}


@app.post("/api/monitor-policies/{object_id}/revoke")
def revoke_monitor(object_id: str, body: VersionAction, p: P, db: DB):
    require(p, "admin")
    row = scoped(db, MonitorPolicy, object_id, p, True); check_version(row, body.expected_version)
    row.status = "revoked"; row.version += 1
    from .sensing_selection import revoke
    revoke(db,p,row,body.reason)
    audit(db, p, "monitor.revoke", row.id, {"reason": body.reason}); db.commit(); return as_dict(row)


@app.get("/api/entities/{object_id}/perception-state")
def perception_state(object_id: str, p: P, db: DB):
    from .perception import object_state
    state = object_state(db, p, object_id); db.commit(); return state


@app.post("/api/acquisitions/{object_id}/reconcile")
def acquisition_reconciliation(object_id: str, p: P, db: DB):
    from .perception import reconcile_acquisition
    row = reconcile_acquisition(db, p, object_id); db.commit(); return as_dict(row)


@app.post("/api/evidence", status_code=201)
async def upload_evidence(request: Request, p: P, db: DB, cloud_model_id: str | None = None):
    require(p, "admin", "operator")
    from .evidence import save_evidence
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > settings().max_upload_bytes:
            raise HTTPException(413, "文件超过大小上限")
        content.extend(chunk)
    media_type = request.headers.get("content-type", "").split(";")[0]
    row = save_evidence(db, p, bytes(content), media_type, cloud_model_id)
    db.commit()
    result = as_dict(row); result.pop("storage_key", None); return result


@app.get("/api/evidence")
def list_evidence(p: P, db: DB):
    rows = db.scalars(select(Evidence).where(Evidence.workspace_id == p.workspace_id)
                      .order_by(Evidence.created_at.desc()).limit(100)).all()
    result = [as_dict(row) for row in rows]
    for item in result:
        item.pop("storage_key", None)
    return result


@app.get("/api/evidence/{object_id}/content")
def download_evidence(object_id: str, p: P, db: DB):
    from .evidence import evidence_bytes
    from .map_access import require_evidence
    require_evidence(db, p, object_id)
    row, content = evidence_bytes(db, p, object_id)
    return Response(content, media_type=row.media_type, headers={"Content-Disposition": "attachment",
                    "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"})


class EvidenceAuthorization(VersionAction):
    model_profile_id: str


class ObservationVerification(VersionAction):
    decision:Literal['confirmed','rejected']
    request_id:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    expected_input_hash:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')


@app.post('/api/observations/{object_id}/verify')
def verify_observation(object_id:str,body:ObservationVerification,p:P,db:DB):
    require(p,'admin','operator')
    row=scoped(db,Observation,object_id,p,True)
    if bool(body.request_id)!=bool(body.expected_input_hash):raise HTTPException(422,'恢复核查需同时固定原请求编号和原输入摘要')
    history=row.data.get('verification_history',[])
    if row.data.get('verification_history_hash') and digest(history)!=row.data['verification_history_hash']:raise HTTPException(409,'原核查历史无法核实')
    if body.request_id:
        old=next((r for r in history if r.get('request_id')==body.request_id),None)
        if old:
            if old.get('body_hash')!=digest(body.model_dump()) or old['reviewed_by']!=p.user_id:raise HTTPException(409,'原核查请求不能更换内容或提交者')
            return as_dict(row)
        from .observation_integrity import checked_report,CHECK_ERRORS
        try:checked_report(db,p,row)
        except CHECK_ERRORS:raise HTTPException(409,'原输入与提取回执无法核对，不能确认')
        if not isinstance(row.data.get('original_request',{}).get('attributes'),dict):raise HTTPException(409,'缺少原登记输入，不能由此确认记录一致')
        actual=digest({'extracted':row.data.get('extracted',{}),'evidence_ids':row.data.get('evidence_ids',[]),'invocation_id':row.data.get('invocation_id')})
        if actual!=body.expected_input_hash:raise HTTPException(409,'原输入或处理结果已变化，请重新核对')
    check_version(row,body.expected_version)
    if row.data.get('native_packet'):raise HTTPException(409,'请回到原处理任务核查；归集记录不能替代原流程的人工核查')
    if row.status!='processed':raise HTTPException(409,'只有已提取观测可核对；失败记录请先恢复提取')
    from .evidence import evidence_bytes
    for identifier in row.data.get('evidence_ids',[]):evidence_bytes(db,p,identifier)
    review={'decision':body.decision,'reviewed_by':p.user_id,'reviewed_at':now().isoformat(),'reason':body.reason,
        'input_hash':digest({'extracted':row.data.get('extracted',{}),'evidence_ids':row.data.get('evidence_ids',[]),
                             'invocation_id':row.data.get('invocation_id')})}
    if body.request_id:review={**review,'request_id':body.request_id,'body_hash':digest(body.model_dump()),'physical_state_verified':False}
    history=[*history,review]
    row.data={**row.data,'human_verification':review,'verification_history':history,'verification_history_hash':digest(history)};row.version+=1
    audit(db,p,'observation.verified',row.id,{'decision':body.decision,'reason':body.reason})
    from .state_refresh import source_changed
    source_changed(db,p,row,'人工核查结果已保存')
    from .retrieval import invalidate_entity
    invalidate_entity(db,p,row.entity_id)
    from .quality import changed
    changed(db,p,scoped(db,Entity,row.entity_id,p).schema_capability_id,
        'observation-review:'+row.id+':'+str(row.version))
    from .data_gaps import changed as gap_changed
    gap_changed(db,p,row.entity_id,'observation-review:'+row.id+':'+str(row.version))
    if row.data.get('acquisition_id'):
        acq=scoped(db,Acquisition,row.data['acquisition_id'],p)
        if acq.data.get('observation_plan_id') and acq.status=='awaiting_observation':
            from .perception import receive_acquisition_observation
            receive_acquisition_observation(db,p,row)
    if row.data.get('candidate_only'):
        from .local_observations import reconcile
        reconcile(db,p,row.source_id,row.business_day)
    db.commit()
    if row.data.get('candidate_only'):
        from .worker import deliver_events
        deliver_events(workspace_id=p.workspace_id)
    return as_dict(row)


@app.post("/api/evidence/{object_id}/authorize")
def authorize_evidence(object_id: str, body: EvidenceAuthorization, p: P, db: DB):
    from .evidence import authorize_cloud
    row = authorize_cloud(db, p, object_id, body.model_profile_id, body.expected_version, body.reason)
    db.commit(); result = as_dict(row); result.pop("storage_key", None); return result


class ObservationRetry(VersionAction):
    request_id:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9_-]{12,100}$')

@app.post("/api/observations/{object_id}/retry")
def retry_observation(object_id: str, body: ObservationRetry, p: P, db: DB):
    require(p, "admin", "operator")
    job = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
                 Job.idempotency_key == "extract:" + object_id).with_for_update())
    row = scoped(db, Observation, object_id, p, True)
    retries=row.data.get('retry_requests',[])
    if retries and digest(retries)!=row.data.get('retry_requests_hash'):raise HTTPException(409,'原重试回执不一致，请保留记录核对')
    request_hash=digest(body.model_dump())
    if body.request_id:
        saved=next((r for r in retries if r['request_id']==body.request_id),None)
        if saved:
            if saved['request_hash']!=request_hash or saved['actor_id']!=p.user_id:raise HTTPException(409,'原重试请求不能替换条件或发起人')
            return as_dict(row)
        from .observation_inputs import view as input_view
        if not input_view(db,p,row)['retry_available']:raise HTTPException(409,'此本地原输入尚不满足重试条件，请先核对来源和处理回执')
    if row.data.get('correction_requests'):raise HTTPException(409,'已有独立更正登记，请核对更正结果，不再重放旧输入')
    check_version(row, body.expected_version)
    if row.status != "failed" or not job or job.status != "failed" or job.attempts >= 3:
        raise HTTPException(409, "此观测不满足重试条件或已达到次数上限")
    published(db, p, row.data["extractor_id"])
    row.status = "received"; row.version += 1
    row.data = {**row.data, "previous_failure": row.data.get("processing_error"),
                "processing_error": None, "failure_type": None}
    job.status = "queued"; job.available_at = now(); job.lease_token = None; job.lease_until = None
    job.data = {**job.data, "actor_id": p.user_id, "retry_reason": body.reason}
    if body.request_id:
        retries=[*retries,{'request_id':body.request_id,'request_hash':request_hash,'actor_id':p.user_id,'original_version':body.expected_version,
            'queued_version':row.version,'attempt':job.attempts+1,'at':now().isoformat()}]
        row.data={**row.data,'retry_requests':retries,'retry_requests_hash':digest(retries)}
    from .state_refresh import source_changed
    source_changed(db,p,row,'已保存原输入重试，等待处理')
    audit(db, p, "observation.retry", row.id, {"reason": body.reason, "next_attempt": job.attempts + 1})
    db.commit(); return as_dict(row)


@app.get("/api/audit")
def audits(p: P, db: DB):
    require(p, "admin", "operator")
    return [as_dict(x) for x in db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id)
        .order_by(Audit.created_at.desc()).limit(100))]


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "web" / "index.html")


@app.get("/assets/operator.js")
def operator_script():
    return FileResponse(Path(__file__).parent / "web" / "operator.js", media_type="text/javascript")


@app.get("/assets/evolution.js")
def evolution_script():
    return FileResponse(Path(__file__).parent / "web" / "evolution.js", media_type="text/javascript")


@app.get('/assets/goal-data.js')
def goal_data_script():
    return FileResponse(Path(__file__).parent/'web'/'goal-data.js',media_type='text/javascript')


@app.get('/assets/relations.js')
def relations_script():
    return FileResponse(Path(__file__).parent/'web'/'relations.js',media_type='text/javascript')


@app.get('/assets/query-needs.js')
def query_needs_script():
    return FileResponse(Path(__file__).parent/'web'/'query-needs.js',media_type='text/javascript')

@app.get('/assets/quality.js')
def quality_script():
    return FileResponse(Path(__file__).parent/'web'/'quality.js',media_type='text/javascript')

@app.get('/assets/query-tools.js')
def query_tools_script():
    return FileResponse(Path(__file__).parent/'web'/'query-tools.js',media_type='text/javascript')

@app.get('/assets/goal-queries.js')
def goal_queries_script():
    return FileResponse(Path(__file__).parent/'web'/'goal-queries.js',media_type='text/javascript')

@app.get('/assets/data-gaps.js')
def gap_script():
    return FileResponse(Path(__file__).parent/'web'/'data-gaps.js',media_type='text/javascript')


from .evolution_api import router as evolution_router
app.include_router(evolution_router)

@app.get('/assets/sensing.js')
def sensing_script():
    return FileResponse(Path(__file__).parent/'web'/'sensing.js',media_type='text/javascript')

@app.get('/assets/device-identity.js')
def device_identity_script():
    return FileResponse(Path(__file__).parent/'web'/'device-identity.js',media_type='text/javascript')
