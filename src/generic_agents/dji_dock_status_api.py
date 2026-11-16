"""Read DJI Cloud API v1 Dock OSD already captured by an authorised MQTT gateway.

This adapter never creates subscriptions, changes gateway grants or publishes a
command. Its observations are reported device claims, not proof of a safe flight.
"""
from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select
from .models import MqttGateway, MqttReceipt, MqttReceiverState
from .setup_api import P, DB
from .services import scoped
from .evidence import evidence_bytes

router = APIRouter(prefix='/api/dji/dock-status', tags=['大疆机场只读状态'])
_TOPIC = re.compile(r'^thing/product/([A-Za-z0-9_-]{1,64})/osd$')


def _pairs(items):
    value = {}
    for key, val in items:
        if key in value:
            raise ValueError('状态报文含重复字段')
        value[key] = val
    return value


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        return None
    return value


def parse_osd(raw):
    """Extract bounded documented fields; never infer health, identity or flight safety."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 65536:
        raise ValueError('状态报文大小无效')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限值')))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('状态报文不是有效 UTF-8 JSON') from exc
    if not isinstance(value, dict) or not isinstance(value.get('data'), dict):
        raise ValueError('状态报文缺少 data 对象')
    data = value['data']
    if len(data) > 512:
        raise ValueError('状态字段过多')
    stamp = value.get('timestamp')
    source_time = None
    if type(stamp) is int and 946684800000 <= stamp <= 4102444800000:
        source_time = datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat()
    # Missing or invalid calibration must never make a coordinate authoritative.
    validity = data.get('home_position_is_valid')
    if type(validity) is not int or validity not in (0, 1, 2, 3):
        validity = None
    latitude = _number(data.get('latitude'), -90, 90)
    longitude = _number(data.get('longitude'), -180, 180)
    position = None
    if validity in (1, 3) and latitude is not None and longitude is not None:
        position = {'latitude': latitude, 'longitude': longitude, 'calibration': 'device_reported_valid'}
    charge = data.get('drone_charge_state')
    percent = _number(charge.get('capacity_percent'), 0, 100) if isinstance(charge, dict) else None
    mode = data.get('mode_code')
    if type(mode) is not int or not 0 <= mode <= 256:
        mode = None
    docked = data.get('drone_in_dock')
    if type(docked) is not int or docked not in (0, 1):
        docked = None
    rainfall = data.get('rainfall')
    if type(rainfall) is not int or rainfall not in (0, 1, 2, 3):
        rainfall = None
    return {'reported_at': source_time, 'mode_code': mode, 'aircraft_in_dock': docked,
            'battery_percent': percent, 'wind_m_s': _number(data.get('wind_speed'), 0, 100),
            'temperature_c': _number(data.get('environment_temperature'), -80, 100),
            'rainfall_code': rainfall, 'position': position,
            'position_note': '坐标未通过设备定位有效性校验' if position is None else '设备报告有效，仍需现场核验'}


def _topics(row):
    value = row.data.get('topics') if isinstance(row.data, dict) else None
    if not isinstance(value, dict):
        return []
    result = []
    for topic, name in value.items():
        m = _TOPIC.fullmatch(topic) if isinstance(topic, str) else None
        if m and isinstance(name, str) and 0 < len(name) <= 128:
            result.append({'topic': topic, 'device_sn': m.group(1), 'name': name})
    return result


def _source(row, selected):
    return {'gateway_id': row.id, 'gateway_name': row.name, 'gateway_status': row.status,
            'gateway_version': row.version, **selected}


@router.get('/sources')
def sources(response: Response, p: P, db: DB):
    response.headers['Cache-Control'] = 'no-store'
    rows = db.scalars(select(MqttGateway).where(MqttGateway.workspace_id == p.workspace_id)
        .order_by(MqttGateway.created_at.desc()).limit(101)).all()
    if len(rows) > 100:
        raise HTTPException(409, 'MQTT 接入过多，请先缩小范围')
    return {'sources': [_source(row, topic) for row in rows for topic in _topics(row)],
            'notice': '仅显示已明确授权的大疆状态主题；设备型号和真实性仍需现场核验。'}


@router.get('/sources/{gateway_id}/{device_sn}')
def latest(gateway_id: str, device_sn: str, response: Response, p: P, db: DB):
    response.headers['Cache-Control'] = 'no-store'
    gateway = scoped(db, MqttGateway, gateway_id, p)
    match = next((v for v in _topics(gateway) if v['device_sn'] == device_sn), None)
    if match is None:
        raise HTTPException(404, '该设备状态主题尚未在当前接入中授权')
    base = {'source': _source(gateway, match), 'latest': None, 'receiver': None,
            'notice': '状态为设备报文中的声明；接收状态、坐标和电量均不等于安全起飞许可。'}
    receiver = db.scalar(select(MqttReceiverState).where(MqttReceiverState.workspace_id == p.workspace_id,
        MqttReceiverState.gateway_id == gateway.id))
    if receiver:
        base['receiver'] = {'status': receiver.status,
            'heartbeat_at': receiver.heartbeat_at.astimezone(timezone.utc).isoformat() if receiver.heartbeat_at else None,
            'gateway_version': receiver.gateway_version}
    receipt = db.scalar(select(MqttReceipt).where(MqttReceipt.workspace_id == p.workspace_id,
        MqttReceipt.gateway_id == gateway.id, MqttReceipt.data['topic'].as_string() == match['topic'])
        .order_by(MqttReceipt.created_at.desc(), MqttReceipt.id.desc()).limit(1))
    if receipt is None:
        return base
    evidence, raw = evidence_bytes(db, p, receipt.evidence_id)
    if evidence.sha256 != receipt.input_sha256 or sha256(raw).hexdigest() != receipt.input_sha256:
        raise HTTPException(409, '原始状态报文摘要无法核对')
    try:
        parsed = parse_osd(raw)
    except ValueError:
        base['latest'] = {'receipt_id': receipt.id, 'parsed': False,
            'received_at': receipt.data.get('received_at'),
            'reason': '最新报文不符合大疆状态格式，原件已保留，请核对来源。'}
        return base
    base['latest'] = {'receipt_id': receipt.id, 'parsed': True,
        'received_at': receipt.data.get('received_at'), 'gateway_version': receipt.data.get('gateway_version'),
        'retained': receipt.data.get('retain') is True, 'qos': receipt.data.get('qos'),
        'evidence_id': evidence.id, 'reported': parsed,
        'state_note': '保留消息或历史版本不代表设备当前在线' if receipt.data.get('retain') or receipt.data.get('gateway_version') != gateway.version else '当前连接收到的报文，状态真实性及设备身份仍需核验'}
    return base
