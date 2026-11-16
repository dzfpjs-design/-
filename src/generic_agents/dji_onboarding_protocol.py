"""Bounded Cloud API 1 Dock onboarding messages; no raw secrets are persisted.

These are MQTT payload contracts for a Dock that already has an authorised
broker connection. They do not establish the authenticity of the aircraft or
prove that the DJI application licence is valid. Cloud API 2 is separate.
"""
from __future__ import annotations

import hmac
import json
import re
import time

from fastapi import HTTPException

_SN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')
_METHODS = {'update_topo', 'config', 'airport_bind_status', 'airport_organization_get', 'airport_organization_bind'}
MAX_PACKET = 65536


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _str(value, limit, *, empty=False):
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value.strip())
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('invalid string')
    return value


def validate_config(value):
    """Return validated config; callers must never log or persist this as plaintext."""
    keys = {'dock_sn', 'aircraft_sn', 'organization_id', 'organization_name',
            'dock_binding_code', 'aircraft_binding_code', 'app_id', 'app_key', 'app_license', 'ntp_host'}
    if not isinstance(value, dict) or set(value) != keys:
        raise HTTPException(422, '设备上云配置字段不完整')
    try:
        for field in ('dock_sn', 'aircraft_sn'):
            if not _SN.fullmatch(value[field]):
                raise ValueError('invalid device serial')
        if value['aircraft_sn'] == value['dock_sn']:
            raise ValueError('duplicate device serial')
        if not _ID.fullmatch(value['organization_id']):
            raise ValueError('invalid organisation')
        _str(value['organization_name'], 128)
        _str(value['dock_binding_code'], 256)
        _str(value['aircraft_binding_code'], 256)
        _str(value['app_id'], 128)
        _str(value['app_key'], 1024)
        _str(value['app_license'], 32768)
        _str(value['ntp_host'], 253)
        if any(c in value['ntp_host'] for c in '/:@?#'):
            raise ValueError('invalid ntp host')
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, '设备、组织或上云凭据无效') from None
    return dict(value)


def prepare_reply(topic, payload, config, *, bound_devices=(), timestamp_ms=None):
    """Validate one exact source and return MQTT response, with no durable writes.

    An administrator must have separately authorised both serial numbers and
    the organisation binding code. The returned bytes may contain application
    credentials and must only be sent to the authorized MQTT broker, never to
    logs, browser, normal evidence storage, or operation receipts.
    """
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_PACKET:
        raise ValueError('invalid packet')
    sn = config['dock_sn']
    topology = f'sys/product/{sn}/status'
    requests = f'thing/product/{sn}/requests'
    if topic not in {topology, requests}:
        raise ValueError('unauthorised topic')
    try:
        msg = json.loads(payload.decode('utf-8'), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        if not isinstance(msg, dict) or not isinstance(msg.get('data'), dict):
            raise ValueError('invalid envelope')
        method = msg.get('method')
        if method not in _METHODS or (topic == topology) != (method == 'update_topo'):
            raise ValueError('unsupported method')
        tid = _str(msg.get('tid'), 128)
        bid = _str(msg.get('bid'), 128)
        if any(not (c.isascii() and (c.isalnum() or c in '-_')) for c in tid + bid):
            raise ValueError('invalid correlation')
        if msg.get('gateway') not in (None, sn):
            raise ValueError('wrong gateway')
        data = msg['data']
        if method == 'update_topo':
            subs = data.get('sub_devices')
            if not isinstance(subs, list) or len(subs) > 1:
                raise ValueError('invalid topology')
            if subs and (not isinstance(subs[0], dict) or subs[0].get('sn') != config['aircraft_sn']):
                raise ValueError('unexpected aircraft')
            # device_secret and nonce are deliberately consumed only in memory.
            _str(data.get('device_secret'), 4096)
            _str(data.get('nonce'), 1024)
            for child in subs:
                _str(child.get('device_secret'), 4096)
                _str(child.get('nonce'), 1024)
            out = {'result': 0}
        elif method == 'config':
            if data.get('config_type') != 'json' or data.get('config_scope') != 'product':
                raise ValueError('unsupported config')
            out = {k: config[k] for k in ('ntp_host', 'app_id', 'app_key', 'app_license')}
            out['ntp_server_host'] = out.pop('ntp_host')
        elif method == 'airport_bind_status':
            devices = data.get('devices')
            allowed = {sn, config['aircraft_sn']}
            if not isinstance(devices, list) or not 1 <= len(devices) <= 2:
                raise ValueError('invalid device list')
            sns = [x.get('sn') for x in devices if isinstance(x, dict)]
            if len(sns) != len(devices) or len(set(sns)) != len(sns) or not set(sns) <= allowed:
                raise ValueError('unknown device')
            out = {'result': 0, 'output': {'bind_status': [
                {'sn': item, 'is_device_bind_organization': item in bound_devices,
                 'organization_id': config['organization_id'] if item in bound_devices else '',
                 'organization_name': config['organization_name'] if item in bound_devices else '', 'device_callsign': item}
                for item in sns]}}
        elif method == 'airport_organization_get':
            code = _str(data.get('device_binding_code'), 256)
            if (data.get('organization_id') != config['organization_id']
                    or not hmac.compare_digest(code.encode(), config['dock_binding_code'].encode())):
                raise ValueError('binding denied')
            out = {'result': 0, 'output': {'organization_name': config['organization_name']}}
        else:
            devices = data.get('bind_devices')
            allowed = {sn, config['aircraft_sn']}
            if not isinstance(devices, list) or not 1 <= len(devices) <= 2:
                raise ValueError('invalid binding')
            sns = []
            for device in devices:
                if not isinstance(device, dict): raise ValueError('invalid device')
                identity = device.get('sn')
                code = _str(device.get('device_binding_code'), 256)
                expected_code = config['dock_binding_code'] if identity == sn else config['aircraft_binding_code']
                if (identity not in allowed or device.get('organization_id') != config['organization_id']
                        or not hmac.compare_digest(code.encode(), expected_code.encode())):
                    raise ValueError('binding denied')
                sns.append(identity)
            if len(set(sns)) != len(sns): raise ValueError('duplicate device')
            out = {'result': 0, 'output': {'err_infos': [{'sn': item, 'err_code': 0} for item in sns]}}
        response = {'method': method, 'tid': tid, 'bid': bid,
                    'timestamp': timestamp_ms if timestamp_ms is not None else int(time.time() * 1000), 'data': out}
        if topic == requests:
            response['gateway'] = sn
        destination = f'sys/product/{sn}/status_reply' if topic == topology else f'thing/product/{sn}/requests_reply'
        return destination, json.dumps(response, ensure_ascii=False, separators=(',', ':')).encode(), method, tid, bid
    except (TypeError, KeyError, ValueError, UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError('设备上云报文无效或未授权') from None
