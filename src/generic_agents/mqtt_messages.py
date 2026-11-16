"""Bounded sensor message decoding; not a subscriber or a trusted measurement."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SensorFields:
    record_key: str = 'record_id'
    device_key: str = 'device_id'
    time_key: str = 'observed_at'
    values_key: str = 'values'
    units_key: str = 'units'


def _no_duplicate_keys(items):
    value = {}
    for key, item in items:
        if key in value: raise ValueError('报文含重复字段，需核对原件')
        value[key] = item
    return value


def decode_sensor_message(payload: bytes, mapping: dict, fields=SensorFields()):
    """Missing time/unit/value is quarantinable, never replaced by arrival time."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= 65536:
        raise ValueError('传感报文须为非空字节且不超过64KiB')
    selectors=(fields.record_key,fields.device_key,fields.time_key,fields.values_key,fields.units_key)
    if (any(not isinstance(key,str) or not 0<len(key)<=80 for key in selectors)
        or fields.device_key in {fields.record_key,fields.time_key}
        or fields.values_key==fields.units_key
        or any(key in {fields.record_key,fields.device_key,fields.time_key} for key in (fields.values_key,fields.units_key))):
        raise ValueError('报文字段用途冲突或字段名超限')
    if not isinstance(mapping,dict) or not mapping or len(mapping)>64 or any(not isinstance(k,str) or not k or not isinstance(v,dict) or set(v)!={'attribute','unit'}
            or not isinstance(v.get('attribute'),str) or not v['attribute']
            or not isinstance(v.get('unit'),str) or len(k)>80 or len(v['attribute'])>80 or len(v['unit'])>40
            for k,v in mapping.items()):
        raise ValueError('字段映射须来自已确认的对象字段和单位')
    if len({v['attribute'] for v in mapping.values()}) != len(mapping):
        raise ValueError('多个输入字段不能覆盖同一个对象属性')
    try:
        raw = json.loads(payload.decode('utf-8'), object_pairs_hook=_no_duplicate_keys,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限数值')))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('传感报文不是可读取的UTF-8 JSON') from error
    if not isinstance(raw,dict) or len(raw)>100:
        raise ValueError('传感报文须为有界对象')
    for key in (fields.record_key, fields.device_key):
        if not isinstance(raw.get(key),str) or not 0<len(raw[key])<=128 or any(ord(c)<32 for c in raw[key]):
            raise ValueError('缺少稳定的设备编号或数据编号，不能用到达顺序代替')
    values=raw.get(fields.values_key); units=raw.get(fields.units_key,{})
    if not isinstance(values,dict) or len(values)>100 or not isinstance(units,dict) or len(units)>100:
        raise ValueError('观测值与单位须为有界字段对象')
    issues=[]; observed=None; assumed_zone=False
    reported_time=raw.get(fields.time_key)
    if reported_time is None or reported_time=='':
        issues.append('missing_capture_time')
    elif not isinstance(reported_time,str) or len(reported_time)>80:
        issues.append('invalid_capture_time')
    else:
        try:
            if not (('T' in reported_time or ' ' in reported_time) and ':' in reported_time):
                raise ValueError('未提供具体采集时刻')
            observed=datetime.fromisoformat(reported_time)
            if observed.tzinfo is None:
                observed=observed.replace(tzinfo=ZoneInfo('Asia/Shanghai')); assumed_zone=True
            observed.astimezone(ZoneInfo('Asia/Shanghai'))
        except (ValueError, OverflowError): observed=None; issues.append('invalid_capture_time')
    attributes={}; unit_origin={}
    for input_key, spec in mapping.items():
        value=values.get(input_key)
        if type(value) not in (int,float) or abs(value)>1e15 or not math.isfinite(value):
            issues.append('invalid_value:'+spec['attribute']); continue
        if input_key in units and units[input_key] != spec['unit']:
            issues.append('unit_conflict:'+spec['attribute']); continue
        attributes[spec['attribute']]=value
        unit_origin[spec['attribute']]='message' if input_key in units else 'confirmed_source_mapping'
    stable_id=hashlib.sha256(json.dumps([raw[fields.device_key],raw[fields.record_key]],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return {'schema':'mqtt-sensor-message-v1','input_sha256':hashlib.sha256(payload).hexdigest(),
            'source_record_id':'mqtt:'+stable_id,'device_external_id':raw[fields.device_key],
            'reported_record_id':raw[fields.record_key],'observed_at':observed.isoformat() if observed else None,
            'business_day':str(observed.astimezone(ZoneInfo('Asia/Shanghai')).date()) if observed else None,
            'timezone_assumed_china':assumed_zone,'attributes':attributes,'unit_origin':unit_origin,
            'issues':issues,'usable':not issues,'ignored_fields':len(set(values)-set(mapping))}
