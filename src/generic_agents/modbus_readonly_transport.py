"""Explicitly allowlisted, read-only Modbus/TCP. No writes or DNS resolution."""
from __future__ import annotations
import hashlib
import ipaddress
import json
import math
import os
import secrets
import socket
import struct
import time

class ModbusError(Exception):
    pass

_RFC1918 = tuple(map(ipaddress.ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))

def grants(workspace, db=None):
    try:
        entries = json.loads(os.getenv('GA_MODBUS_READ_GRANTS', '[]'))
        if not isinstance(entries, list): raise ValueError()
        result = []
        for e in entries:
            if not isinstance(e, dict): raise ValueError()
            if e.get('workspace') != workspace: continue
            ip = ipaddress.IPv4Address(e['host'])
            if not any(ip in net for net in _RFC1918): raise ValueError()
            port, unit, start, end = (e[k] for k in ('port', 'unit', 'start', 'end'))
            if any(type(n) is not int for n in (port, unit, start, end)) or not (1 <= port <= 65535 and 1 <= unit <= 247 and 0 <= start <= end <= 65535): raise ValueError()
            funcs = e['functions']
            if not isinstance(funcs, list) or not funcs or any(type(f) is not int or f not in (3,4) for f in funcs): raise ValueError()
            canonical = {'workspace': workspace, 'host': str(ip), 'port': port, 'unit': unit, 'start': start, 'end': end, 'functions': sorted(set(funcs))}
            canonical['id'] = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',',':')).encode()).hexdigest()
            result.append(canonical)
        from .managed_modbus_grants import managed_decisions
        decisions=managed_decisions(workspace,db)
        # An explicit workspace denial also overrides an identical legacy grant.
        result=[grant for grant in result if grant['id'] not in decisions]
        result.extend(grant for grant in decisions.values() if grant is not None)
        return result
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise ModbusError('Modbus 访问授权无效，请联系系统管理员检查配置') from None

def get_grant(workspace, ident, db=None):
    for grant in grants(workspace,db):
        if grant['id'] == ident: return grant
    raise ModbusError('授权不存在或已撤销')

def _exact(sock, count, deadline):
    data = b''
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise ModbusError('设备读取超时，请检查网络后重试')
        sock.settimeout(remaining)
        part = sock.recv(count-len(data))
        if not part: raise ModbusError('设备连接提前结束')
        data += part
    return data

def read_registers(host, port, unit, function, start, count):
    if function not in (3,4) or not (1 <= count <= 32 and 0 <= start <= 65535 and start+count <= 65536 and 1 <= unit <= 247):
        raise ModbusError('读取范围或功能码无效')
    transaction = secrets.randbelow(65536)
    packet = struct.pack('>HHHBBHH', transaction, 0, 6, unit, function, start, count)
    try:
        deadline = time.monotonic() + 3
        with socket.create_connection((host, port), timeout=3) as sock:
            sock.settimeout(max(0.001, deadline-time.monotonic())); sock.sendall(packet)
            h = _exact(sock, 7, deadline); tx, protocol, length, reply_unit = struct.unpack('>HHHB', h)
            if tx != transaction or protocol != 0 or reply_unit != unit or not 3 <= length <= 67:
                raise ModbusError('设备响应头不匹配')
            payload = _exact(sock, length-1, deadline)
    except (OSError, TimeoutError):
        raise ModbusError('设备未连接或读取超时，请检查网络和设备后重试') from None
    if payload[0] == function | 0x80: raise ModbusError('设备拒绝读取该寄存器，请检查地址和权限')
    if len(payload) != 2 + count*2 or payload[0] != function or payload[1] != count*2:
        raise ModbusError('设备响应数据长度或功能码不匹配')
    return list(struct.unpack('>'+'H'*count, payload[2:]))

def decode_registers(values, kind, word_order, scale, offset):
    if kind not in ('uint16','int16','uint32','int32','float32') or word_order not in ('big','little'):
        raise ModbusError('数值类型或寄存器顺序无效')
    count = 1 if kind.endswith('16') else 2
    if len(values) != count or not all(type(v) is int and 0<=v<=65535 for v in values): raise ModbusError('寄存器数量与数值类型不匹配')
    if not all(type(n) in (float,int) and math.isfinite(n) and abs(n) <= 1e12 for n in (scale, offset)): raise ModbusError('系数或偏移无效')
    words = values[::-1] if word_order == 'little' else values
    value = struct.unpack({'uint16':'>H','int16':'>h','uint32':'>I','int32':'>i','float32':'>f'}[kind], b''.join(struct.pack('>H', x) for x in words))[0]
    value = value * scale + offset
    if not math.isfinite(value): raise ModbusError('设备返回了无效数值')
    return value
