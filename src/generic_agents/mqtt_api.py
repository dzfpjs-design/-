"""Saved read-only sensor connections and original packet review; no fake connection test."""
from typing import Literal
from datetime import timezone
from fastapi import APIRouter, Query, Request, HTTPException
from fastapi.responses import Response, StreamingResponse, HTMLResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.background import BackgroundTask
from pydantic import Field
from sqlalchemy import select,func,or_,and_
from .contracts import Contract, digest
from .setup_api import P, DB
from .models import MqttGateway, MqttReceipt, AuthSession, User, Membership, now
from .security import authenticated, Principal
from .mqtt_inbox import create_gateway, set_status, validate_configuration
from .services import scoped, require, check_version, audit
from .evidence import evidence_bytes

router = APIRouter(prefix='/api/mqtt')


class GatewayCreate(Contract):
    name: str = Field(min_length=1, max_length=200)
    configuration: dict
    request_id:str|None=Field(default=None,pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


class GatewayUpdate(Contract):
    name: str = Field(min_length=1,max_length=200)
    configuration:dict
    expected_version: int = Field(ge=1)


class GatewayStatus(Contract):
    expected_version: int = Field(ge=1)
    status: Literal['active', 'paused']


class BindingCreate(Contract):
    expected_version: int = Field(ge=1)
    topic: str = Field(min_length=1, max_length=256)
    entity_id: str
    extractor_id: str
    field_links:dict[str,str]|None=None
    instance_release_id:str|None=Field(default=None,min_length=1,max_length=36)


def binding_request(body):
    # Old pending requests retain their exact body and idempotency fingerprint.
    return body.model_dump(exclude={'instance_release_id'} if body.instance_release_id is None else set())


class ReceiptSubmit(Contract):
    expected_version: int = Field(ge=1)


class RouteCreate(BindingCreate):
    request_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    include_existing: bool = False


class RouteStatus(GatewayStatus):
    request_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    include_existing: bool = False


class DailyInspection(Contract):
    request_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    action: Literal['start','retry']
    expected_version: int|None = Field(default=None,ge=1)


@router.get('/gateways/{identifier}/days')
def gateway_days(identifier:str,p:P,db:DB,limit:int=Query(default=20,ge=1,le=100),
                 after:str|None=Query(default=None,max_length=2000),business_day:str|None=Query(default=None,max_length=20)):
    from .mqtt_daily import days
    return days(db,p,gateway_id=identifier,limit=limit,after=after,business_day=business_day)


@router.get('/instances/{identifier}/days')
def instance_days(identifier:str,p:P,db:DB,limit:int=Query(default=20,ge=1,le=100),
                 after:str|None=Query(default=None,max_length=2000),business_day:str|None=Query(default=None,max_length=20)):
    from .mqtt_daily import days
    return days(db,p,instance_id=identifier,limit=limit,after=after,business_day=business_day)


@router.get('/daily/{identifier}')
def daily_summary(identifier:str,p:P,db:DB,revision:int|None=Query(default=None,ge=1),instance_id:str|None=None):
    from .mqtt_daily import summary
    return summary(db,p,identifier,revision,instance_id)


@router.get('/daily/{identifier}/records')
def daily_records(identifier:str,p:P,db:DB,revision:int=Query(ge=1),instance_id:str|None=None,
    kind:Literal['original','processing']='original',status:Literal['all','pending','received','succeeded','failed','blocked','needs_review']='all',
    limit:int=Query(default=100,ge=1,le=100),after:str|None=Query(default=None,max_length=2000)):
    from .mqtt_daily import records
    return records(db,p,identifier,revision,instance_id,kind,status,limit,after)


def _file_principal(request,db,workspace_id):
    p=authenticated(request,db)
    selected=request.headers.get('x-workspace-id')
    if selected and selected!=workspace_id:raise HTTPException(403,'下载工作区与当前查看范围不一致')
    return _recheck_file_principal(db,p,workspace_id)


def _recheck_file_principal(db,p,workspace_id):
    # SQL直接重读真实会话、用户和所选工作区授权；不信查询参数中的角色，也不复用identity map旧值。
    pair=db.execute(select(Membership.role).select_from(AuthSession).join(User,User.id==AuthSession.user_id)
        .outerjoin(Membership,(Membership.user_id==User.id)&(Membership.workspace_id==workspace_id))
        .where(AuthSession.id==p.session_id,AuthSession.user_id==p.user_id,AuthSession.revoked.is_(False),
               AuthSession.expires_at>now(),User.active.is_(True))).one_or_none()
    if pair is None:raise HTTPException(401,'登录或账号授权已失效，请回到工作区重新登录后恢复下载')
    if pair[0] is None:raise HTTPException(403,'当前账号没有此工作区下载权限，请回到原页核对')
    return Principal(p.user_id,workspace_id,pair[0],p.session_id)


def _file_failure(request,error):
    if 'application/json' in request.headers.get('accept','') and 'text/html' not in request.headers.get('accept',''):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':error.detail},status_code=error.status_code,
            headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    from html import escape
    return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>下载未开始</title>'
        '<body><h1>下载未开始</h1><p>'+escape(str(error.detail))+'</p>'
        '<p>原清单和已保存的准备进度保留。请回到原页面核对范围、恢复原件或重新登录后再试。</p>'
        '<a href="/setup/">返回工作区</a></body></html>',status_code=error.status_code,
        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


class _DownloadRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def protected(request):
            try:return await handler(request)
            except RequestValidationError:
                return _file_failure(request,HTTPException(422,'下载范围不完整或格式错误，请回到原页重新核对固定清单'))
            except HTTPException as error:return _file_failure(request,error)
        return protected


file_router=APIRouter(route_class=_DownloadRoute)


@file_router.get('/daily/{identifier}/export.csv')
def daily_csv_file(identifier:str,request:Request,db:DB,workspace_id:str=Query(min_length=1,max_length=36),
    revision:int=Query(ge=1),expected_revision_hash:str=Query(pattern=r'^[0-9a-f]{64}$'),
    expected_total:int=Query(ge=0),instance_id:str|None=None,
    kind:Literal['original','processing']='original',
    status:Literal['all','pending','received','succeeded','failed','blocked','needs_review']='all'):
    from .mqtt_daily import prepare_csv
    prepared=None
    try:
        p=_file_principal(request,db,workspace_id)
        prepared=prepare_csv(db,p,identifier,revision,expected_revision_hash,expected_total,instance_id,kind,status)
        _recheck_file_principal(db,p,workspace_id)
    except HTTPException as error:
        if prepared:prepared['stream'].close()
        return _file_failure(request,error)
    except BaseException:
        if prepared:prepared['stream'].close()
        raise
    stream=prepared['stream']
    async def chunks():
        try:
            while block:=stream.read(65536):yield block
        finally:stream.close()
    return StreamingResponse(chunks(),media_type='text/csv; charset=utf-8',
        background=BackgroundTask(stream.close),headers={
        'Content-Disposition':'attachment; filename="'+prepared['filename']+'"',
        'Content-Length':str(prepared['size']),'X-Content-SHA256':prepared['sha256'],
        'X-Mqtt-Revision':str(prepared['revision']),'X-Mqtt-Revision-Hash':prepared['revision_hash'],
        'X-Mqtt-Record-Count':str(prepared['total']),'X-Mqtt-Scope-Hash':prepared['scope_hash'],
        'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})


@file_router.get('/daily/{identifier}/original/{receipt_id}/download')
def daily_original_file(identifier:str,receipt_id:str,request:Request,db:DB,
    workspace_id:str=Query(min_length=1,max_length=36),revision:int=Query(ge=1),
    expected_revision_hash:str=Query(pattern=r'^[0-9a-f]{64}$'),
    expected_input_sha256:str=Query(pattern=r'^[0-9a-f]{64}$'),instance_id:str|None=None):
    from .mqtt_daily import original_bytes,_snapshot
    try:
        p=_file_principal(request,db,workspace_id)
        _,frozen=_snapshot(db,p,identifier,revision)
        if frozen.content_hash!=expected_revision_hash:raise HTTPException(409,'原件下载不属于原固定修订，请回到原页核对')
        content,sha=original_bytes(db,p,identifier,receipt_id,revision,instance_id)
        if sha!=expected_input_sha256:raise HTTPException(409,'原件下载摘要与已核对原件不一致')
        _recheck_file_principal(db,p,workspace_id)
    except HTTPException as error:return _file_failure(request,error)
    scope={'workspace_id':workspace_id,'ledger_id':identifier,'revision':revision,'revision_hash':expected_revision_hash,
           'instance_id':instance_id,'receipt_id':receipt_id,'input_sha256':expected_input_sha256}
    return Response(content,media_type='application/octet-stream',headers={
        'Content-Disposition':'attachment; filename="mqtt-original.bin"','X-Content-SHA256':sha,
        'X-Mqtt-Revision':str(revision),'X-Mqtt-Revision-Hash':expected_revision_hash,
        'X-Mqtt-Scope-Hash':digest(scope),
        'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})


@router.get('/daily/{identifier}/inspection')
def daily_inspection_state(identifier:str,p:P,db:DB,instance_id:str|None=None):
    from .mqtt_daily import inspection_state
    return inspection_state(db,p,identifier,instance_id)


@router.get('/daily/{identifier}/original/{receipt_id}')
def daily_original(identifier:str,receipt_id:str,p:P,db:DB,revision:int=Query(ge=1),instance_id:str|None=None):
    from .mqtt_daily import original_bytes
    content,sha=original_bytes(db,p,identifier,receipt_id,revision,instance_id)
    return Response(content,media_type='application/octet-stream',headers={
        'Content-Disposition':'attachment; filename="mqtt-original.bin"',
        'X-Content-SHA256':sha,'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})


@router.get('/gateways/{identifier}/daily-index')
def gateway_daily_index(identifier:str,p:P,db:DB):
    from .mqtt_daily import backfill_state
    return backfill_state(db,p,identifier)


@router.post('/gateways/{identifier}/daily-index')
def gateway_daily_index_request(identifier:str,body:DailyInspection,p:P,db:DB):
    from .mqtt_daily import request_backfill,checked_checkpoint_view
    row=request_backfill(db,p,identifier,body.model_dump());db.commit()
    return checked_checkpoint_view(db,p,row)


@router.post('/daily/{identifier}/inspection')
def daily_inspection_request(identifier:str,body:DailyInspection,p:P,db:DB,instance_id:str|None=None):
    from .mqtt_daily import request_inspection,checked_checkpoint_view
    row=request_inspection(db,p,identifier,body.model_dump(),instance_id);db.commit()
    return checked_checkpoint_view(db,p,row)


@router.get('/gateways/{identifier}/routes')
def routes(identifier: str, p: P, db: DB):
    from .mqtt_binding_routes import list_routes
    return list_routes(db,p,identifier)


@router.post('/gateways/{identifier}/routes',status_code=201)
def confirm_route(identifier: str, body: RouteCreate, p: P, db: DB):
    from .mqtt_binding_routes import create_route, route_view
    row=create_route(db,p,identifier,binding_request(body));db.commit()
    return route_view(db,p,route=row)


@router.put('/routes/{identifier}')
def revise_route(identifier: str, body: RouteCreate, p: P, db: DB):
    from .mqtt_binding_routes import update_route, route_view
    row=update_route(db,p,identifier,binding_request(body));db.commit()
    return route_view(db,p,route=row)


@router.post('/routes/{identifier}/status')
def change_route_status(identifier: str, body: RouteStatus, p: P, db: DB):
    from .mqtt_binding_routes import set_route_status, route_view
    row=set_route_status(db,p,identifier,body.model_dump());db.commit()
    return route_view(db,p,route=row)


@router.get('/routes/{identifier}/revisions')
def route_revisions(identifier: str, p: P, db: DB):
    from .mqtt_binding_routes import revisions
    return revisions(db,p,identifier)


@router.post('/deliveries/{identifier}/submit')
def submit_delivery(identifier: str, body: ReceiptSubmit, p: P, db: DB):
    from .mqtt_observations import promote_delivery
    from .services import as_dict
    row=promote_delivery(db,p,identifier,body.expected_version);db.commit()
    return as_dict(row)


@router.post('/receipts/{identifier}/retry-data')
def retry_data(identifier:str,body:ReceiptSubmit,p:P,db:DB):
    from .models import Event
    from .mqtt_data import raw_report
    require(p,'admin','operator')
    receipt=scoped(db,MqttReceipt,identifier,p)
    event=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,
        Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id).with_for_update())
    if not event:
        from fastapi import HTTPException
        raise HTTPException(409,'原数据送达记录不存在，请核查原接收记录')
    if event.status=='received':return {'receipt_id':receipt.id,'status':'received'}
    check_version(receipt,body.expected_version)
    report=raw_report(db,p,receipt)
    if (event.status!='blocked_delivery' or event.data['report']!=report
        or event.data['report_hash']!=digest(report) or event.attempts>=3):
        from fastapi import HTTPException
        raise HTTPException(409,'原件或送达依据仍未恢复，或已达到重试次数上限')
    event.status='pending_delivery'
    event.data={**event.data,'previous_delivery_error':event.data.get('delivery_error'),'delivery_error':None}
    audit(db,p,'mqtt.data_delivery.retry',receipt.id,{'event_id':event.id})
    db.commit()
    return {'receipt_id':receipt.id,'status':'pending_delivery'}


def binding_view(row):
    return {'id':row.id,'gateway_id':row.gateway_id,'topic':row.topic,'entity_id':row.entity_id,
            'source_id':row.source_id,'extractor_id':row.extractor_id,'config_hash':row.config_hash,
            'field_links':row.data.get('field_links'), 'field_units':row.data.get('field_units'),
            'device_external_id':row.data['device_external_id'], 'source_time_verified':False}


def gateway_view(row):
    return {'id': row.id, 'name': row.name, 'version': row.version, 'status': row.status,
            'configuration': row.data,'config_hash':digest(row.data),'request_id':row.creation_request_id,
            'created_at': row.created_at.astimezone(timezone.utc).isoformat(),
            'status_meaning': '接收已授权；实际连接由读取进程确认' if row.status == 'active' else '接收已暂停',
            'device_commands_supported': False}


def receipt_view(row,submission=None,observation=None,data_delivery=None,associations=None):
    return {'id': row.id, 'gateway_id': row.gateway_id, 'version': row.version, 'status': row.status,
            'input_sha256': row.input_sha256, 'received_at': row.data['received_at'],
            'topic': row.data['topic'], 'qos': row.data['qos'], 'retain': row.data['retain'],
            'deliveries': row.data['deliveries'], 'gateway_version': row.data['gateway_version'],
            'decoded': row.data['decoded'], 'issues': row.data['issues'],
            'measurement_units':{spec['attribute']:spec['unit'] for spec in row.data['config_snapshot']['mapping'].values()},
            'observation_id':row.data.get('observation_id'),
            'submission':{'status':submission.status,'error':submission.data.get('error'),
                          'attempts':submission.attempts} if submission else None,
            'processing':{'status':observation.status,'error':observation.data.get('processing_error')} if observation else None,
            'data_received':bool(data_delivery and data_delivery.status=='received'),
            'data_delivery':{'status':data_delivery.status,'error':data_delivery.data.get('delivery_error')} if data_delivery else None,
            'associations':associations or [],
            'accepted_as_observation': bool(row.data.get('observation_id')) and row.status != 'quarantined'}


@router.get('/gateways/{identifier}/bindings')
def bindings(identifier: str, p: P, db: DB):
    from .models import MqttBinding
    scoped(db,MqttGateway,identifier,p)
    rows=db.scalars(select(MqttBinding).where(MqttBinding.workspace_id==p.workspace_id,
        MqttBinding.gateway_id==identifier).order_by(MqttBinding.created_at.desc()).limit(100)).all()
    return [binding_view(row) for row in rows]


@router.get('/gateways/{identifier}/reception')
def reception(identifier: str, p: P, db: DB, response: Response):
    from .mqtt_reception import reception_view
    response.headers['Cache-Control'] = 'no-store'
    return reception_view(db, p, identifier)


@router.post('/gateways/{identifier}/bindings',status_code=201)
def confirm_binding(identifier: str, body: BindingCreate,p:P,db:DB):
    from .mqtt_observations import bind
    row=bind(db,p,identifier,body.expected_version,body.topic,body.entity_id,body.extractor_id,
             field_links=body.field_links,instance_release_id=body.instance_release_id)
    db.commit()
    return binding_view(row)


@router.post('/receipts/{identifier}/submit')
def submit(identifier:str,body:ReceiptSubmit,p:P,db:DB):
    from .mqtt_observations import promote
    from .services import as_dict
    row=promote(db,p,identifier,body.expected_version)
    db.commit()
    return as_dict(row)


@router.get('/gateways')
def gateways(p: P, db: DB):
    rows = db.scalars(select(MqttGateway).where(MqttGateway.workspace_id == p.workspace_id)
                      .order_by(MqttGateway.created_at.desc()).limit(100)).all()
    return [gateway_view(row) for row in rows]


@router.post('/gateways', status_code=201)
def create(body: GatewayCreate, p: P, db: DB):
    row = create_gateway(db, p, body.name, body.configuration,body.request_id)
    db.commit()
    return gateway_view(row)


@router.put('/gateways/{identifier}')
def update(identifier: str, body: GatewayUpdate, p: P, db: DB):
    require(p, 'admin')
    from .managed_mqtt_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    row = scoped(db, MqttGateway, identifier, p, lock=True)
    check_version(row, body.expected_version)
    configuration = validate_configuration(body.configuration,workspace_id=p.workspace_id,db=db)
    if not body.name.strip():
        from fastapi import HTTPException
        raise HTTPException(422, '请填写接入名称')
    row.name = body.name.strip(); row.data = configuration; row.status = 'paused'; row.version += 1
    audit(db, p, 'mqtt.gateway.updated', row.id, {'config_hash': digest(configuration), 'version': row.version})
    db.commit()
    return gateway_view(row)


@router.post('/gateways/{identifier}/status')
def change_status(identifier: str, body: GatewayStatus, p: P, db: DB):
    row = set_status(db, p, identifier, body.expected_version, body.status)
    db.commit()
    return gateway_view(row)


@router.get('/gateways/{identifier}/receipts')
def receipts(identifier: str, p: P, db: DB,response:Response, limit: int = Query(default=50, ge=1, le=100),
             after:str|None=Query(default=None,max_length=36)):
    scoped(db, MqttGateway, identifier, p)
    base=[MqttReceipt.gateway_id==identifier,MqttReceipt.workspace_id==p.workspace_id]
    response.headers['X-Total-Count']=str(db.scalar(select(func.count()).select_from(MqttReceipt).where(*base)))
    query=select(MqttReceipt).where(*base)
    if after:
        cursor=scoped(db,MqttReceipt,after,p)
        if cursor.gateway_id!=identifier:
            from fastapi import HTTPException
            raise HTTPException(404,'此接入的翻页位置不存在')
        query=query.where(or_(MqttReceipt.created_at<cursor.created_at,
            and_(MqttReceipt.created_at==cursor.created_at,MqttReceipt.id<cursor.id)))
    found=db.scalars(query.order_by(MqttReceipt.created_at.desc(),MqttReceipt.id.desc()).limit(limit+1)).all()
    rows=found[:limit]
    response.headers['X-Next-Cursor']=rows[-1].id if len(found)>limit else ''
    from .models import Job,Observation,Event
    ids=[row.id for row in rows]
    submissions=db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='submit_mqtt_receipt',
        Job.data['receipt_id'].as_string().in_(ids))).all() if ids else []
    by_receipt={job.data['receipt_id']:job for job in submissions if not job.data.get('delivery_id')}
    observation_ids=[row.data['observation_id'] for row in rows if row.data.get('observation_id')]
    observations=db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,
        Observation.id.in_(observation_ids))).all() if observation_ids else []
    by_id={row.id:row for row in observations}
    deliveries=db.scalars(select(Event).where(Event.workspace_id==p.workspace_id,Event.kind=='perception.sensor_packet',
        Event.data['receipt_id'].as_string().in_(ids))).all() if ids else []
    by_delivery={event.data['receipt_id']:event for event in deliveries}
    from .models import MqttReceiptDelivery
    from .mqtt_binding_routes import delivery_view
    assigned=db.scalars(select(MqttReceiptDelivery).where(MqttReceiptDelivery.workspace_id==p.workspace_id,
        MqttReceiptDelivery.receipt_id.in_(ids)).order_by(MqttReceiptDelivery.route_id,MqttReceiptDelivery.revision)).all() if ids else []
    route_observation_ids=[row.observation_id for row in assigned if row.observation_id]
    route_observations=db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,
        Observation.id.in_(route_observation_ids))).all() if route_observation_ids else []
    route_observation_map={row.id:row for row in route_observations}
    job_by_delivery={job.data['delivery_id']:job for job in submissions if job.data.get('delivery_id')}
    grouped={}
    for assigned_row in assigned:
        grouped.setdefault(assigned_row.receipt_id,[]).append(delivery_view(db,p,assigned_row,
            job_by_delivery.get(assigned_row.id),route_observation_map.get(assigned_row.observation_id)))
    return [receipt_view(row,by_receipt.get(row.id),by_id.get(row.data.get('observation_id')),by_delivery.get(row.id),
        grouped.get(row.id,[])) for row in rows]


@router.get('/receipts/{identifier}/original')
def original(identifier: str, p: P, db: DB):
    row = scoped(db, MqttReceipt, identifier, p)
    evidence, content = evidence_bytes(db, p, row.evidence_id)
    if evidence.sha256 != row.input_sha256:
        from fastapi import HTTPException
        raise HTTPException(409, '接收记录与原报文摘要不一致')
    return Response(content, media_type='application/octet-stream', headers={
        'Content-Disposition': f'attachment; filename="mqtt-packet-{row.id}.bin"',
        'X-Content-SHA256': row.input_sha256, 'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'private, no-store'})


router.include_router(file_router)

from .mqtt_test_origin_v297 import router as test_origin_router
router.include_router(test_origin_router)
