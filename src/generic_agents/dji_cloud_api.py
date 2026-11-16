"""Workspace-scoped DJI Cloud API v1 Dock OSD intake, with no device control.

The device must already publish to a third-party broker. We subscribe only to
an explicit Dock OSD topic; this does not perform DJI onboarding or subscribe
to status/topology topics that can contain device secrets.
"""
from uuid import UUID
from typing import Literal
import os
import re
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import Field, StrictInt, StrictStr
from sqlalchemy import select
from .contracts import Contract, digest
from .config import settings
from .models import AuthSession, Capability, Job, Membership, MqttGateway, User, now
from .mqtt_inbox import broker_address, create_gateway, set_status, validate_configuration
from .mqtt_reception import reception_view
from .dji_dock_status_api import latest as dock_latest
from .security import require
from .services import audit, scoped
from .setup_api import P, DB
from .ftps_gateway_api import _lock, _current_admin

router = APIRouter(prefix='/api/dji/cloud', tags=['大疆机场状态只读接入'])
SOURCE = 'dji_cloud_osd_source_v1'
OP = 'dji_cloud_operation_v1'
STOP = 'dji_cloud_stop_v1'
NOTICE = '仅接收已上云机场的状态报文；不执行设备上云握手、无人机任务或飞行控制。报文不代表真实设备身份、实时在线或安全起飞许可。'

class Create(Contract):
    request_id: UUID
    name: StrictStr = Field(min_length=1, max_length=100)
    broker: StrictStr = Field(default='', max_length=300)
    device_sn: StrictStr = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    username_ref: StrictStr = Field(default='', max_length=88)
    password_ref: StrictStr = Field(default='', max_length=88)
    authorization_id: StrictStr | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

class Change(Contract):
    request_id: UUID
    expected_version: StrictInt = Field(ge=1)
    status: Literal['active', 'paused']

class PublishTool(Contract):
    request_id: UUID
    expected_version: StrictInt = Field(ge=1)
    confirm: Literal[True]


def _op_key(p, rid): return f'dji-cloud-op:{p.user_id}:{rid}'
def _source_key(identity): return f'dji-cloud-source:{identity}'


def _member(db, p):
    query = (select(User.id).join(Membership, Membership.user_id == User.id)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
               Membership.role.in_(['admin', 'operator', 'viewer']), AuthSession.id == p.session_id,
               AuthSession.revoked.is_(False), AuthSession.expires_at > now())
        .with_for_update(read=True, of=[User, Membership, AuthSession]).execution_options(populate_existing=True))
    if db.execute(query).first() is None:
        raise HTTPException(403, '当前账号或工作区权限已失效')


def _operation(db, p, rid):
    return db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == _op_key(p, rid)).execution_options(populate_existing=True))


def _receipt(row):
    if row.kind == STOP:
        return {'request_id': row.data['request_id'], 'state': 'cancelled'}
    if row.kind != OP or row.status != 'internal' or row.data.get('state') != 'succeeded':
        raise HTTPException(409, '原操作记录无法核对')
    if row.data['kind'] == 'publish_tool':
        return {'request_id':row.data['request_id'],'state':'succeeded','kind':'publish_tool',
                'connection_id':row.data['connection_id'],'capability':row.data['result']}
    return {'request_id': row.data['request_id'], 'state': 'succeeded',
            'kind': row.data['kind'], 'connection': row.data['result']}


def _source(db, p, identity):
    gateway = scoped(db, MqttGateway, identity, p)
    row = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == _source_key(gateway.id), Job.kind == SOURCE, Job.status == 'internal'))
    if not row or not isinstance(row.data, dict) or row.data.get('gateway_id') != gateway.id:
        raise HTTPException(404, '未找到此工作区的大疆状态接入')
    d = row.data
    sn = d.get('device_sn')
    if (not isinstance(sn, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', sn)
        or d.get('topic') != 'thing/product/' + sn + '/osd'
        or not isinstance(d.get('broker'), str) or not re.fullmatch(r'[0-9a-f]{64}', d.get('config_hash',''))):
        raise HTTPException(409, '接入登记记录无法核对')
    return gateway, row


def _connection(db, p, gateway, registration):
    d = registration.data
    valid = (digest(gateway.data) == d['config_hash'] and gateway.data.get('broker') == d['broker']
             and gateway.data.get('topics') == {d['topic']: d['device_sn']})
    from .dji_cloud_tool import NAMESPACE, _name, validate_manifest, HANDLER
    capability = db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,
        Capability.namespace==NAMESPACE,Capability.name==_name(gateway.id),Capability.release_version=='1.0.0'))
    tool = None
    if capability:
        try:
            validate_manifest(capability.data)
            if capability.content_hash != digest(capability.data): raise ValueError()
            tool={'id':capability.id,'name':capability.data['description'],'status':capability.status,
                  'version':capability.version,'handler':HANDLER}
        except (HTTPException,ValueError,KeyError,TypeError):
            tool={'id':capability.id,'name':'工具记录待核对','status':'invalid','version':capability.version,'handler':HANDLER}
    authorized = False
    if valid:
        try:
            validate_configuration(gateway.data,workspace_id=p.workspace_id,db=db)
            authorized = True
        except HTTPException:
            pass
    return {'id': gateway.id, 'name': gateway.name, 'version': gateway.version, 'status': gateway.status,
            'broker': d['broker'], 'device_sn': d['device_sn'], 'topic': d['topic'],
            'configuration_valid': valid, 'configuration_hash': d['config_hash'], 'authorization_available':authorized,
            'capability':tool, 'receiver_state': reception_view(db, p, gateway.id)['state'] if valid else 'configuration_changed'}


def read_authorized_dock_status(db, p, gateway_id: str, device_sn: str):
    """Read one authorised OSD source as task evidence in the caller's transaction.

    Callers must bind both identifiers to the task/tool authorization and keep
    the transaction open while using the result. No subscription or publication
    is initiated; timestamps and retained/stale messages are explicitly kept.
    """
    require(p, 'admin', 'operator')
    from .managed_mqtt_grants import lock_workspace, ref_id, credentials
    lock_workspace(db,p.workspace_id,shared=True)
    gateway = scoped(db, MqttGateway, str(gateway_id), p, lock=True)
    gateway, registration = _source(db, p, gateway.id)
    if registration.data['device_sn'] != device_sn:
        raise HTTPException(403, '此接入未授权该机场序列号')
    if gateway.status != 'active': raise HTTPException(409, '状态接入已暂停')
    if not _connection(db, p, gateway, registration)['configuration_valid']:
        raise HTTPException(409, '状态接入配置已变化')
    users = sorted({p.user_id, gateway.owner_id})
    active_users = set(db.scalars(select(User.id).where(User.id.in_(users), User.active.is_(True))
        .order_by(User.id).with_for_update(read=True)))
    memberships = list(db.scalars(select(Membership).where(Membership.workspace_id == p.workspace_id,
        Membership.user_id.in_(users)).order_by(Membership.user_id).with_for_update(read=True)))
    roles = {m.user_id: m.role for m in memberships}
    if active_users != set(users) or any(roles.get(uid) not in {'admin', 'operator'} for uid in users):
        raise HTTPException(403, '接入所有者或执行人的工作区权限已失效')
    config = validate_configuration(gateway.data,workspace_id=p.workspace_id,db=db)
    managed_identity=ref_id(config['username_ref'],config['password_ref'])
    if managed_identity:credentials(p.workspace_id,managed_identity,db)
    if (config['username_ref'] and not managed_identity
        and (not os.environ.get(config['username_ref']) or not os.environ.get(config['password_ref']))):
        raise HTTPException(409, 'MQTT 凭据不可用，无法核对接入状态')
    receiver = reception_view(db, p, gateway.id)
    state = dock_latest(gateway.id, device_sn, Response(), p, db)
    return {'connection': _connection(db, p, gateway, registration), 'receiver': receiver,
            'latest': state['latest'], 'notice': NOTICE}


@router.get('/options')
def options(p: P, db: DB, response: Response):
    require(p, 'admin', 'operator', 'viewer')
    response.headers['Cache-Control'] = 'no-store'
    values = []
    for origin in dict.fromkeys(x.strip() for x in settings().mqtt_allowed_brokers.split(',') if x.strip()):
        try: broker_address(origin)
        except HTTPException: continue
        values.append({'value': origin, 'label': origin, 'broker':origin, 'authorization_id':None})
    if p.role=='admin':
        from .managed_mqtt_grants import listing as mqtt_authorizations, get_grant, lock_workspace
        lock_workspace(db,p.workspace_id,shared=True)
        for item in mqtt_authorizations(p,db)['authorizations']:
            if not item.get('enabled'):continue
            try: grant=get_grant(p.workspace_id,item['id'],db)
            except HTTPException:continue
            values.append({'value':grant['id'],'label':grant['name'],'broker':grant['broker'],'authorization_id':grant['id']})
    return {'brokers': values, 'credential_note': '如需认证，请填写运维已配置的服务端 MQTT 用户名和密码引用；本页面不接收明文凭据。', 'notice': NOTICE}


@router.get('/connections')
def connections(p: P, db: DB, response: Response, page: int = Query(default=1, ge=1, le=10000)):
    require(p, 'admin', 'operator', 'viewer')
    response.headers['Cache-Control'] = 'no-store'
    rows = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == SOURCE,
        Job.status == 'internal').order_by(Job.created_at.desc(), Job.id.desc()).offset((page-1)*50).limit(51)))
    items = []
    for row in rows[:50]:
        identity = row.data.get('gateway_id') if isinstance(row.data, dict) else None
        if not identity: continue
        gateway, registration = _source(db, p, identity)
        items.append(_connection(db, p, gateway, registration))
    return {'connections': items, 'page': page, 'has_more': len(rows) > 50, 'notice': NOTICE}


@router.post('/connections')
def create(body: Create, p: P, db: DB):
    require(p, 'admin')
    rid = str(body.request_id); name = body.name.strip()
    if not name or any(ord(c) < 32 for c in name): raise HTTPException(422, '请填写有效接入名称')
    data = {'name': name, 'broker': body.broker, 'device_sn': body.device_sn,
            'username_ref': body.username_ref, 'password_ref': body.password_ref,'authorization_id':body.authorization_id}
    fingerprint = digest({'kind': 'create', **data})
    _lock(db, p.workspace_id, _op_key(p, rid))
    from .managed_mqtt_grants import lock_workspace, get_grant, _refs
    lock_workspace(db,p.workspace_id,shared=True); _current_admin(db, p)
    old = _operation(db, p, rid)
    if old:
        if old.kind == STOP: return _receipt(old)
        if old.data.get('fingerprint') != fingerprint: raise HTTPException(409, '原请求已绑定其他输入，请核对')
        return _receipt(old)
    if body.authorization_id:
        if body.broker or body.username_ref or body.password_ref:raise HTTPException(422,'图形授权与手工地址不能混用')
        grant=get_grant(p.workspace_id,body.authorization_id,db)
        broker=grant['broker'];username_ref,password_ref=_refs(grant['id'])
    else:
        if not body.broker:raise HTTPException(422,'请选择已授权的 MQTT 服务')
        broker=body.broker;username_ref=body.username_ref;password_ref=body.password_ref
    topic = 'thing/product/' + body.device_sn + '/osd'
    config = {'broker': broker, 'topics': {topic: body.device_sn},
              'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}},
              'fields': {}, 'username_ref': username_ref, 'password_ref': password_ref}
    gateway = create_gateway(db, p, name, config, request_id=rid)
    registration = Job(workspace_id=p.workspace_id, kind=SOURCE, status='internal',
        idempotency_key=_source_key(gateway.id), data={'gateway_id': gateway.id, 'device_sn': body.device_sn,
        'broker': broker, 'topic': topic, 'config_hash': digest(gateway.data), 'actor_id': p.user_id})
    db.add(registration); db.flush()
    result = _connection(db, p, gateway, registration)
    op = Job(workspace_id=p.workspace_id, kind=OP, status='internal', idempotency_key=_op_key(p, rid),
        data={'request_id': rid, 'kind': 'create', 'fingerprint': fingerprint, 'actor_id': p.user_id,
              'state': 'succeeded', 'result': result})
    db.add(op); audit(db, p, 'dji.cloud.osd_registered', gateway.id, {'gateway_id': gateway.id})
    db.commit(); return _receipt(op)


@router.post('/connections/{identity}/status')
def status(identity: UUID, body: Change, p: P, db: DB):
    require(p, 'admin')
    rid = str(body.request_id); identity = str(identity)
    fingerprint = digest({'kind': 'status', 'connection_id': identity,
                          'expected_version': body.expected_version, 'status': body.status})
    _lock(db, p.workspace_id, _op_key(p, rid))
    from .managed_mqtt_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    # Gateway before member matches the receiver's authorization-lock order.
    gateway = scoped(db, MqttGateway, identity, p, lock=True)
    _current_admin(db, p)
    old = _operation(db, p, rid)
    if old:
        if old.kind == STOP: return _receipt(old)
        if old.data.get('fingerprint') != fingerprint: raise HTTPException(409, '原请求已绑定其他输入，请核对')
        return _receipt(old)
    gateway, registration = _source(db, p, identity)
    if body.status == 'active' and digest(gateway.data) != registration.data['config_hash']:
        raise HTTPException(409, '接入配置已变化，请重新登记并核对')
    gateway = set_status(db, p, identity, body.expected_version, body.status)
    result = _connection(db, p, gateway, registration)
    op = Job(workspace_id=p.workspace_id, kind=OP, status='internal', idempotency_key=_op_key(p, rid),
        data={'request_id': rid, 'kind': 'status', 'fingerprint': fingerprint, 'actor_id': p.user_id,
              'state': 'succeeded', 'result': result})
    db.add(op); db.commit(); return _receipt(op)


@router.get('/connections/{identity}/status')
def connection_status(identity: UUID, p: P, db: DB, response: Response):
    require(p, 'admin', 'operator', 'viewer'); response.headers['Cache-Control'] = 'no-store'
    gateway, registration = _source(db, p, str(identity))
    view = _connection(db, p, gateway, registration)
    receiver = reception_view(db, p, gateway.id) if view['configuration_valid'] else None
    latest = dock_latest(gateway.id, registration.data['device_sn'], response, p, db)['latest'] if view['configuration_valid'] else None
    if latest and not view['authorization_available']:
        latest={**latest,'state_note':'来源授权不可用；此为历史报文，不代表当前在线'}
    return {'connection': view, 'receiver': receiver, 'latest': latest, 'notice': NOTICE}


@router.post('/connections/{identity}/capability')
def publish_tool(identity: UUID, body: PublishTool, p: P, db: DB):
    """Register, validate and publish exactly one bounded read-only tool."""
    from .dji_cloud_tool import manifest, NAMESPACE, _name, HANDLER, validate_manifest
    from .services import register, validate_capability, publish
    require(p, 'admin'); identity=str(identity); rid=str(body.request_id)
    fingerprint=digest({'kind':'publish_tool','connection_id':identity,'expected_version':body.expected_version,'confirm':True})
    _lock(db,p.workspace_id,_op_key(p,rid))
    from .managed_mqtt_grants import lock_workspace
    lock_workspace(db,p.workspace_id,shared=True)
    gateway=scoped(db,MqttGateway,identity,p,lock=True); _current_admin(db,p)
    old=_operation(db,p,rid)
    if old:
        if old.kind==STOP:return _receipt(old)
        if old.data.get('fingerprint')!=fingerprint:raise HTTPException(409,'原请求已绑定其他输入，请核对')
        return _receipt(old)
    gateway,registration=_source(db,p,identity)
    if gateway.version!=body.expected_version:raise HTTPException(409,'接入版本已变化，请重新核对')
    read_authorized_dock_status(db,p,identity,registration.data['device_sn'])
    definition=manifest(identity,registration.data['device_sn'],registration.data['config_hash'])
    expected=digest(definition.model_dump(mode='json'))
    cap=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.namespace==NAMESPACE,
        Capability.name==_name(identity),Capability.release_version=='1.0.0'))
    if cap:
        validate_manifest(cap.data)
        if cap.content_hash!=expected or digest(cap.data)!=expected or cap.status!='published':
            raise HTTPException(409,'同名工具已有未发布或不同版本，请先核对能力目录')
    else:
        cap=register(db,p,definition)
        cap=validate_capability(db,p,cap.id,cap.version)
        cap=publish(db,p,cap.id,cap.version,'管理员确认发布固定设备范围的只读状态工具')
    result={'id':cap.id,'name':cap.data['description'],'status':cap.status,'version':cap.version,'handler':HANDLER}
    op=Job(workspace_id=p.workspace_id,kind=OP,status='internal',idempotency_key=_op_key(p,rid),
        data={'request_id':rid,'kind':'publish_tool','fingerprint':fingerprint,'actor_id':p.user_id,
              'connection_id':identity,'state':'succeeded','result':result})
    db.add(op);audit(db,p,'dji.cloud.read_tool_published',cap.id,{'gateway_id':identity});db.commit()
    return _receipt(op)


@router.get('/operations/{request_id}')
def operation(request_id: UUID, p: P, db: DB, response: Response):
    require(p, 'admin', 'operator', 'viewer'); response.headers['Cache-Control'] = 'no-store'
    row = _operation(db, p, str(request_id))
    if not row: raise HTTPException(404, '尚未找到原操作，可安全结束后重新操作')
    return _receipt(row)


@router.post('/operations/{request_id}/cancel')
def cancel(request_id: UUID, p: P, db: DB):
    require(p, 'admin', 'operator', 'viewer'); rid = str(request_id)
    _lock(db, p.workspace_id, _op_key(p, rid)); _member(db, p)
    old = _operation(db, p, rid)
    if old: return _receipt(old)
    row = Job(workspace_id=p.workspace_id, kind=STOP, status='internal', idempotency_key=_op_key(p, rid),
              data={'request_id': rid, 'actor_id': p.user_id})
    db.add(row); audit(db, p, 'dji.cloud.request_stopped', rid); db.commit(); return _receipt(row)
