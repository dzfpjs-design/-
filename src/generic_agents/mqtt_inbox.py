"""Read-only MQTT intake. A committed receipt is not an accepted observation."""
from copy import deepcopy
from hashlib import sha256
from urllib.parse import urlsplit
import json
import re
from fastapi import HTTPException
from sqlalchemy import select, text
from .config import settings
from .contracts import digest
from .evidence import save_immutable, evidence_bytes
from .models import MqttGateway, MqttReceipt, User, Membership, now
from .mqtt_messages import SensorFields, decode_sensor_message
from .security import require
from .services import scoped, audit, check_version


# DJI topology and onboarding replies can contain device secrets, application
# keys, and licences. The generic receiver stores complete raw payloads as
# evidence, so these topics require a dedicated secret-aware adapter instead.
_DJI_SECRET_TOPICS = re.compile(r'^(?:sys/product/[^/]+/status|thing/product/[^/]+/(?:state|requests(?:_reply)?))$')
_DJI_OSD_TOPIC = re.compile(r'^thing/product/[^/]+/osd$')


def _check_dji_osd(payload):
    """Reject known secrets before raw evidence is saved; never log payloads.

    This conservative field detector is not a guarantee about future/unknown
    DJI attributes. A dedicated allowlisted or encrypted intake is needed for
    a comprehensive confidentiality boundary.
    """
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    try:
        value = json.loads(payload.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite')))
        if not isinstance(value, dict) or not isinstance(value.get('data'), dict):
            raise ValueError('not an OSD object')
        sensitive = ('secret', 'token', 'password', 'credential', 'authorization',
                     'signature', 'license', 'privatekey', 'apikey', 'appkey',
                     'accesskey', 'devicebindingcode', 'securitycode')
        pending = [(value, 0)]
        count = 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > 32 or count > 10000:
                raise ValueError('nesting limit')
            if isinstance(item, dict):
                for key, child in item.items():
                    normalized = re.sub(r'[^a-z0-9]', '', key.lower())
                    if normalized == 'nonce' or any(word in normalized for word in sensitive):
                        raise ValueError('sensitive field')
                    pending.append((child, depth + 1))
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
            elif isinstance(item, str) and '://' in item:
                url = urlsplit(item)
                if url.username or url.password or url.query or url.fragment:
                    raise ValueError('sensitive link')
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise HTTPException(422, '大疆状态报文无法安全保存；请检查设备来源或使用专用安全接入。') from None


def broker_address(origin, *, workspace_id=None, db=None, refs=None):
    if not isinstance(origin, str) or len(origin) > 300:
        raise HTTPException(422, '代理地址无效')
    try:
        url = urlsplit(origin)
        port = url.port
    except ValueError as error:
        raise HTTPException(422, '代理地址无效') from error
    if (url.scheme not in {'mqtt', 'mqtts'} or not url.hostname or not port
            or url.username is not None or url.password is not None
            or url.path or url.query or url.fragment
            or origin != f'{url.scheme}://{url.hostname}:{port}'):
        raise HTTPException(422, '代理地址只允许协议、主机和明确端口，凭据另行引用')
    if url.scheme == 'mqtt' and url.hostname not in {'127.0.0.1', 'localhost'}:
        raise HTTPException(422, '非本机代理必须使用经验证的TLS连接')
    from .managed_mqtt_grants import ref_id,get_grant
    identity=ref_id(*refs) if refs is not None else None
    if identity:
        if not workspace_id:
            raise HTTPException(403, 'MQTT 图形授权缺少工作区范围')
        grant=get_grant(workspace_id,identity,db)
        if grant['broker']!=origin or url.scheme!='mqtts':
            raise HTTPException(403, 'MQTT 授权与服务地址不一致')
    else:
        allowed = {item.strip() for item in settings().mqtt_allowed_brokers.split(',') if item.strip()}
        if origin not in allowed:
            raise HTTPException(403, '此代理尚未获得服务端接入许可')
    return url.hostname, port, url.scheme == 'mqtts'


def validate_configuration(config, *, workspace_id=None, db=None):
    if not isinstance(config, dict) or set(config) != {'broker', 'topics', 'mapping', 'fields', 'username_ref', 'password_ref'}:
        raise HTTPException(422, '传感接入配置字段不完整或含未支持的配置')
    broker_address(config['broker'],workspace_id=workspace_id,db=db,
                   refs=(config['username_ref'],config['password_ref']))
    topics = config['topics']
    if not isinstance(topics, dict) or not 0 < len(topics) <= 32:
        raise HTTPException(422, '须明确确认1至32个具体主题')
    for topic, device in topics.items():
        if (not isinstance(topic, str) or not topic or len(topic.encode()) > 256
                or any(ord(c) < 32 for c in topic) or any(c in topic for c in '#+') or topic.startswith('$')
                or not isinstance(device, str) or not device.strip() or len(device) > 128
                or any(ord(c) < 32 for c in device)):
            raise HTTPException(422, '主题须为具体路径，设备编号须由用户确认')
        if _DJI_SECRET_TOPICS.fullmatch(topic):
            raise HTTPException(422, '该大疆设备上云主题可能包含密钥，普通传感器接收器不支持；请使用安全的大疆专用接入。')
    if len(set(topics.values())) != len(topics):
        raise HTTPException(422, '每台设备须使用唯一已确认主题，避免编号归属含糊')
    refs = (config['username_ref'], config['password_ref'])
    if any(not isinstance(ref, str) or (ref and not re.fullmatch(r'GA_MQTT_[A-Z0-9_]{1,80}', ref)) for ref in refs):
        raise HTTPException(422, '访问凭据须使用专属服务端密钥引用')
    if bool(refs[0]) != bool(refs[1]):
        raise HTTPException(422, '用户名与密码引用须同时配置')
    try:
        if not isinstance(config['fields'], dict):
            raise ValueError()
        fields = SensorFields(**config['fields'])
        # Empty valid JSON reaches selector/mapping validation, then rejects missing identity.
        try:
            decode_sensor_message(b'{}', config['mapping'], fields)
        except ValueError as error:
            if str(error) != '缺少稳定的设备编号或数据编号，不能用到达顺序代替':
                raise
    except (ValueError, TypeError) as error:
        raise HTTPException(422, '报文字段名、映射或单位未通过核对') from error
    return deepcopy(config)


def create_gateway(db, principal, name, config, request_id=None):
    require(principal, 'admin')
    from .managed_mqtt_grants import lock_workspace
    lock_workspace(db,principal.workspace_id,shared=True)
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise HTTPException(422, '请填写接入名称')
    fingerprint=digest({'name':name.strip(),'configuration':config})
    if request_id:
        lock=int.from_bytes(sha256((principal.workspace_id+':mqtt-create:'+request_id).encode()).digest()[:8],'big',signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':lock})
        old=db.scalar(select(MqttGateway).where(MqttGateway.workspace_id==principal.workspace_id,
            MqttGateway.creation_request_id==request_id))
        if old:
            if old.owner_id!=principal.user_id or old.creation_hash!=fingerprint:
                raise HTTPException(409,'此保存请求已有不同的接入记录，请读取原记录再核对')
            return old
    row = MqttGateway(workspace_id=principal.workspace_id, owner_id=principal.user_id,
                      name=name.strip(), status='paused', data=validate_configuration(config,workspace_id=principal.workspace_id,db=db),
                      creation_request_id=request_id,creation_hash=fingerprint if request_id else None)
    db.add(row); db.flush()
    audit(db, principal, 'mqtt.gateway.created', row.id, {'config_hash': digest(row.data)})
    return row


def set_status(db, principal, identifier, version, status):
    require(principal, 'admin')
    from .managed_mqtt_grants import lock_workspace
    lock_workspace(db,principal.workspace_id,shared=True)
    row = scoped(db, MqttGateway, identifier, principal, lock=True)
    check_version(row, version)
    if status not in {'active', 'paused'}:
        raise HTTPException(422, '接入状态无效')
    if status == 'active': validate_configuration(row.data,workspace_id=principal.workspace_id,db=db)
    row.status = status; row.version += 1
    if status=='active':
        from .mqtt_observations import schedule_receipts
        schedule_receipts(db,principal,row)
    audit(db, principal, 'mqtt.gateway.' + status, row.id, {'version': row.version})
    return row


def authorized_gateway(db, principal, identifier, version):
    require(principal, 'admin', 'operator')
    from .managed_mqtt_grants import lock_workspace
    lock_workspace(db,principal.workspace_id,shared=True)
    # Shared authorization locks serialize commit against revoke, while letting
    # multiple sources owned by the same account receive concurrently.
    gateway = scoped(db, MqttGateway, identifier, principal, lock=True)
    user = db.scalar(select(User).where(User.id == principal.user_id)
                     .with_for_update(read=True).execution_options(populate_existing=True))
    member = db.scalar(select(Membership).where(Membership.workspace_id == principal.workspace_id,
                                                Membership.user_id == principal.user_id)
                       .with_for_update(read=True).execution_options(populate_existing=True))
    if not user or not user.active or not member or member.role not in {'admin', 'operator'}:
        raise HTTPException(403, '接收者不再具有此工作区的接入权限')
    if gateway.status != 'active' or gateway.version != version or gateway.owner_id != principal.user_id:
        raise HTTPException(409, '接入已暂停、版本变化或执行身份不符')
    validate_configuration(gateway.data,workspace_id=principal.workspace_id,db=db)
    return gateway


def capture(db, principal, gateway_id, version, topic, payload, qos, retain=False, duplicate=False):
    """Caller must COMMIT before PUBACK. All originals remain below observation acceptance."""
    gateway = authorized_gateway(db, principal, gateway_id, version)
    if topic not in gateway.data['topics']:
        raise HTTPException(403, '收到未授权主题，未确认接收')
    if not isinstance(payload, bytes) or len(payload) > 65536:
        raise HTTPException(413, '报文超过64KiB，未确认接收')
    if _DJI_OSD_TOPIC.fullmatch(topic):
        _check_dji_osd(payload)
    if type(qos) is not int or qos not in {0, 1, 2} or type(retain) is not bool or type(duplicate) is not bool:
        raise HTTPException(422, '协议接收元数据无效')
    content_hash = sha256(payload).hexdigest()
    config_hash = digest(gateway.data)
    key = digest({'config_hash': config_hash, 'version': version, 'topic': topic, 'sha256': content_hash})
    saved = db.scalar(select(MqttReceipt).where(MqttReceipt.gateway_id == gateway.id, MqttReceipt.packet_key == key))
    if saved:
        original, _ = evidence_bytes(db, principal, saved.evidence_id)
        if original.sha256 != content_hash or saved.input_sha256 != content_hash:
            raise HTTPException(409, '原报文摘要不一致，停止确认接收')
        saved.data = {**saved.data, 'deliveries': saved.data['deliveries'] + 1,
                      'last_delivery_at': now().isoformat()}
        saved.version += 1
        return saved
    evidence = save_immutable(db, principal, payload, 'application/octet-stream',
                              {'modality': 'sensor_packet', 'gateway_id': gateway.id, 'topic': topic})
    # The immutable store may already contain this digest; a corrupted existing file
    # must not become a successfully acknowledged new packet.
    evidence_bytes(db, principal, evidence.id)
    try:
        decoded = decode_sensor_message(payload, gateway.data['mapping'], SensorFields(**gateway.data['fields']))
        issues = list(decoded['issues'])
    except ValueError:
        decoded = None; issues = ['unreadable_packet']
    if decoded and decoded['device_external_id'] != gateway.data['topics'][topic]:
        issues.append('device_topic_binding_mismatch')
    if qos != 1: issues.append('qos_not_verified')
    if retain: issues.append('retained_packet_needs_time_review')
    prior = []
    if decoded and 'device_topic_binding_mismatch' not in issues:
        prior = db.scalars(select(MqttReceipt).where(MqttReceipt.gateway_id == gateway.id,
            MqttReceipt.source_record_id == decoded['source_record_id'])).all()
        prior = [row for row in prior if 'device_topic_binding_mismatch' not in row.data['issues']]
        if any(row.input_sha256 != content_hash for row in prior):
            issues.append('record_content_conflict')
            for row in prior:
                row.status = 'quarantined'
                row.data = {**row.data, 'issues': sorted(set(row.data['issues'] + ['record_content_conflict']))}
                row.version += 1
                from .mqtt_observations import invalidate_conflict
                invalidate_conflict(db,principal,row)
        elif prior:
            # Same measurement under a new subscription config is retained, not processed twice.
            issues.append('record_already_received')
    receipt = MqttReceipt(workspace_id=principal.workspace_id, gateway_id=gateway.id, evidence_id=evidence.id,
        packet_key=key, source_record_id=decoded['source_record_id'] if decoded else None,
        input_sha256=content_hash, status='quarantined' if issues else 'awaiting_object_binding',
        data={'schema': 'mqtt-receipt-v1', 'topic': topic, 'qos': qos, 'retain': retain,
              'duplicate_flag': duplicate, 'received_at': now().isoformat(), 'deliveries': 1,
              'gateway_version': version, 'config_hash': config_hash, 'config_snapshot': deepcopy(gateway.data),
              'decoded': decoded, 'issues': sorted(set(issues)), 'intake_issues':sorted(set(issues)),
              'received_by': principal.user_id})
    db.add(receipt); db.flush()
    audit(db, principal, 'mqtt.packet.saved', receipt.id, {'input_sha256': content_hash, 'status': receipt.status})
    from .mqtt_data import enqueue_packet
    enqueue_packet(db,principal,receipt)
    from .mqtt_observations import enqueue_receipt
    enqueue_receipt(db,principal,gateway,receipt)
    return receipt
