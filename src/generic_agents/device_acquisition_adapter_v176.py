"""Persistent selected-Acquisition protocol for the standalone SIM ledger.

This adapter never writes platform state, creates imagery or re-dispatches a
persisted intent. An absent supplier receipt after intent is an unknown result.
Root owns platform authorization, observation intake and data collection.
"""
import hashlib
import json
import math
import re
from datetime import datetime
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

MARK = {'simulation': True, 'origin_kind': 'simulated_equipment', 'field_verified': False}
UNITS = {'temperature_c': '°C', 'humidity_percent': '%', 'wind_m_s': 'm/s',
    'pressure_hpa': 'hPa', 'battery_percent': '%', 'pan_degrees': '°',
    'tilt_degrees': '°', 'zoom': '倍'}
FIELDS = {'sim-weather-01': {'temperature_c', 'humidity_percent', 'wind_m_s', 'pressure_hpa'},
    'sim-dock-01': {'battery_percent', 'wind_m_s'},
    'sim-camera-01': {'pan_degrees', 'tilt_degrees', 'zoom'}}
IMMUTABLE = ('request_key', 'exact_body', 'body_sha256', 'request_json',
    'device_id', 'device_key', 'command_json', 'command_sha256')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def need(condition, message='补采原请求或设备回执无法核对；保留原号与占用，不重新下发', code=409):
    if not condition:
        raise HTTPException(code, message)


class AcquisitionRequestV176(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_key: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
    target_external_id: str = Field(min_length=1, max_length=200)
    fields: list[str] = Field(min_length=1, max_length=8)
    max_duration_seconds: StrictInt = Field(ge=1, le=86400)
    asset_external_id: str = Field(min_length=1, max_length=64)
    action: Literal['read_existing', 'observe']
    max_cost_minor: StrictInt = Field(ge=0, le=10**12)
    currency: Literal['CNY']

    @field_validator('target_external_id')
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or '\x00' in value:
            raise ValueError('需有明确目标标识')
        return value

    @field_validator('fields')
    @classmethod
    def actual_fields(cls, value):
        if len(set(value)) != len(value) or any(field not in UNITS for field in value):
            raise ValueError('只接受明确单位的模拟遥测字段，不接收影像请求')
        return value


def normalize_request(raw_bytes, idempotency_key):
    """Return (strict DTO, exact UTF-8 text, exact-byte SHA256)."""
    try:
        need(type(raw_bytes) is bytes and 0 < len(raw_bytes) <= 65536,
            '补采正文须为有效且有界的JSON', 422)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError('重复字段')
                result[key] = value
            return result
        text = raw_bytes.decode('utf-8', 'strict')
        value = json.loads(text, object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非有限数值')))
        canonical(value).encode('utf-8', 'strict')
        body = AcquisitionRequestV176.model_validate(value)
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(422, '补采正文的字段、类型或原始JSON不符合要求') from None
    need(type(idempotency_key) is str and body.request_key == idempotency_key,
        'Idempotency-Key必须与原补采request_key完全一致', 409)
    need(body.asset_external_id in FIELDS
        and set(body.fields) <= FIELDS[body.asset_external_id],
        '本地模拟资产或所选遥测字段不受支持；不生成新图像', 422)
    need(body.asset_external_id != 'sim-camera-01' or body.action == 'read_existing',
        '此补采协议没有云台角度参数，请使用明确的云台命令；不推测动作', 422)
    return body, text, hashlib.sha256(raw_bytes).hexdigest()


def command_for(body, revision):
    if body.asset_external_id == 'sim-dock-01' and body.action == 'observe':
        return {'device_id': body.asset_external_id, 'action': 'start_mission',
            'expected_revision': revision, 'arguments': {
                'duration_seconds': min(2, body.max_duration_seconds), 'speed_m_s': 1,
                'mission_name': ('模拟补充观测 ' + body.target_external_id)[:120]}}
    return {'device_id': body.asset_external_id, 'action': 'read_telemetry',
        'expected_revision': None, 'arguments': {}}


class AcquisitionAdapterV176:
    def __init__(self, ledger):
        self.ledger = ledger
        with ledger.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS acquisition_adapter_v176 ('
                'request_key TEXT PRIMARY KEY, exact_body TEXT NOT NULL, body_sha256 TEXT NOT NULL,'
                'request_json TEXT NOT NULL, device_id TEXT NOT NULL, device_key TEXT NOT NULL UNIQUE,'
                'command_json TEXT NOT NULL, command_sha256 TEXT NOT NULL, intent_hash TEXT NOT NULL,'
                'phase TEXT NOT NULL, receipt_json TEXT, receipt_sha256 TEXT, problem TEXT)')

    def _checked(self, row):
        row = dict(row)
        body, text, signature = normalize_request(row['exact_body'].encode('utf-8'), row['request_key'])
        command = json.loads(row['command_json'])
        need(signature == row['body_sha256'] and text == row['exact_body']
            and canonical(body.model_dump()) == row['request_json']
            and row['device_id'] == body.asset_external_id
            and row['device_key'] == 'acq-v176:' + sha(body.request_key)
            and row['command_sha256'] == sha(row['command_json'])
            and row['command_json'] == canonical(command)
            and row['intent_hash'] == sha(canonical({k: row[k] for k in IMMUTABLE})))
        revision = command.get('expected_revision')
        if command.get('action') == 'start_mission':
            need(type(revision) is int and revision >= 1)
        need(canonical(command) == canonical(command_for(body, revision)))
        if row['receipt_json'] is not None:
            need(row['receipt_sha256'] == sha(row['receipt_json']))
        return row, body, command

    def _problem(self, request_key, code):
        with self.ledger.transaction() as db:
            db.execute("UPDATE acquisition_adapter_v176 SET phase=CASE WHEN phase IN "
                "('completed','completed_no_data') THEN phase ELSE 'unknown' END,problem=? WHERE request_key=?",
                (code, request_key))

    def submit(self, raw_bytes, idempotency_key):
        need(type(idempotency_key) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', idempotency_key),
            '需要有效的补采Idempotency-Key', 422)
        # Known nonces are bound before parsing a replacement body. Even an
        # invalid replacement must not silently become a fresh request.
        existing_key = False
        with self.ledger.transaction() as db:
            old = db.execute('SELECT * FROM acquisition_adapter_v176 WHERE request_key=?',
                (idempotency_key,)).fetchone()
            if old is not None:
                existing_key = True
                saved, _, _ = self._checked(old)
                need(type(raw_bytes) is bytes and saved['exact_body'].encode('utf-8') == raw_bytes,
                    '同一补采原号已绑定其他正文，不能更换目标、字段或参数')
        if existing_key:
            return self.receipt(idempotency_key)
        body, exact, signature = normalize_request(raw_bytes, idempotency_key)
        dispatch = False
        with self.ledger.transaction() as db:
            existing = db.execute('SELECT * FROM acquisition_adapter_v176 WHERE request_key=?',
                (body.request_key,)).fetchone()
            if existing is not None:
                saved, _, command = self._checked(existing)
                need(saved['exact_body'] == exact and saved['body_sha256'] == signature,
                    '同一补采原号已绑定其他正文，不能更换目标、字段或参数')
                device_key = saved['device_key']
            else:
                row, profile, _ = self.ledger._advance(db, body.asset_external_id)
                need(row['id'] == body.asset_external_id and profile['id'] == body.asset_external_id)
                command = command_for(body, row['revision'])
                device_key = 'acq-v176:' + sha(body.request_key)
                # A pre-existing unrelated supplier command is not this intent.
                need(db.execute('SELECT 1 FROM receipts WHERE request_id=?', (device_key,)).fetchone() is None)
                saved = {'request_key': body.request_key, 'exact_body': exact, 'body_sha256': signature,
                    'request_json': canonical(body.model_dump()), 'device_id': body.asset_external_id,
                    'device_key': device_key, 'command_json': canonical(command),
                    'command_sha256': sha(canonical(command))}
                intent_hash = sha(canonical(saved))
                db.execute('INSERT INTO acquisition_adapter_v176 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (*[saved[k] for k in IMMUTABLE], intent_hash, 'intent', None, None, None))
                dispatch = True
        # The intent is committed before any device command. Only its creator
        # may cross this boundary. Every replay follows the read-only path.
        if dispatch:
            from .device_simulator_v175 import Command
            try:
                self.ledger.invoke(device_key, Command.model_validate(command))
            except Exception:
                self._problem(body.request_key, 'initial_dispatch_not_confirmed')
                raise HTTPException(409, '原补采意图已保存，设备结果尚不能核对；不得换号或自动重发') from None
        return self.receipt(body.request_key)

    def _supplier(self, db, mapping):
        row = db.execute('SELECT * FROM receipts WHERE request_id=?', (mapping['device_key'],)).fetchone()
        need(row is not None, '原补采意图存在但设备回执缺失；不能证明未执行或自动重发')
        output = json.loads(row['data'])
        need(row['device_id'] == mapping['device_id'] and row['body'] == mapping['command_json']
            and row['request_hash'] == mapping['command_sha256']
            and output.get('request_id') == mapping['device_key']
            and output.get('device_id') == mapping['device_id']
            and output.get('action') == json.loads(mapping['command_json'])['action']
            and output.get('status') == row['status']
            and all(type(output.get(k)) is type(v) and output[k] == v for k, v in MARK.items()))
        return output

    def _project(self, mapping, body, supplier):
        status = supplier['status']
        need(status in {'accepted', 'running', 'succeeded'},
            '原设备动作失败、取消或状态不明；继续保留占用，不视为未执行')
        observation = None
        completed = status == 'succeeded'
        if completed and supplier['action'] == 'read_telemetry':
            sample = supplier.get('result')
            need(type(sample) is dict and sample.get('id') == body.asset_external_id
                and sample.get('time_semantics') == 'simulator_sample_time'
                and sample.get('physical_device_connected') is False
                and all(type(sample.get(k)) is type(v) and sample[k] == v for k, v in MARK.items()))
            need(type(sample.get('revision')) is int and sample['revision'] >= 1
                and type(supplier.get('device_revision')) is int
                and sample['revision'] == supplier['device_revision'])
            observed = sample.get('sampled_at')
            try:
                parsed = datetime.fromisoformat(observed)
                need(parsed.tzinfo is not None and parsed.utcoffset() is not None)
            except (ValueError, TypeError):
                raise HTTPException(409, '原模拟取样时间无法核对，不能伪造设备现场时钟') from None
            attributes = {field: sample.get('state', {}).get(field) for field in body.fields}
            need(all(type(value) in {int, float} and math.isfinite(value) for value in attributes.values()),
                '原模拟遥测字段缺失或类型无效，不生成替代观测')
            observation = {**MARK, 'schema': 'sim-telemetry-observation-v176',
                'source_record_id': 'sim-acq-v176:' + sha(body.request_key),
                'request_key': body.request_key, 'device_command_key': mapping['device_key'],
                'asset_external_id': body.asset_external_id, 'target_external_id': body.target_external_id,
                'attributes': attributes, 'field_units': {field: UNITS[field] for field in body.fields},
                'observed_at': observed, 'time_semantics': 'simulator_sample_time',
                'source_time_verified': False, 'physical_device_connected': False,
                'image_evidence_ids': [], 'supplier_receipt_hash': sha(canonical(supplier))}
        projected = ('completed' if observation is not None else 'completed_no_data') if completed else 'accepted'
        return {**MARK, 'schema': 'sim-acquisition-receipt-v176',
            'request_key': body.request_key, 'asset_external_id': body.asset_external_id,
            'target_external_id': body.target_external_id, 'receipt_id': 'sim-acq-v176:' + sha(body.request_key),
            'status': projected, 'raw_device_status': status, 'terminal': completed,
            'no_future_device_activity': completed, 'no_future_observation': completed,
            'request_body_sha256': mapping['body_sha256'], 'device_command_key': mapping['device_key'],
            'request_hash': sha(mapping['request_json']),
            'device_command_sha256': mapping['command_sha256'],
            'cost_minor': 0, 'currency': 'CNY', 'artifacts': [],
            'observation': observation, 'observation_available': observation is not None,
            'source_time_verified': False, 'field_observation_created': False,
            'supplier_receipt': supplier,
            'problem': '原模拟动作已结束但没有产生新影像或观测资料' if projected == 'completed_no_data' else None}

    def receipt(self, request_key):
        need(type(request_key) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', request_key),
            '需要有效的原补采request_key', 422)
        found = False
        try:
            with self.ledger.transaction() as db:
                row = db.execute('SELECT * FROM acquisition_adapter_v176 WHERE request_key=?', (request_key,)).fetchone()
                need(row is not None, '没有此原补采映射；不能据此证明未执行', 404)
                found = True
                mapping, body, _ = self._checked(row)
                # Check existence before advancing: recovery never calls invoke.
                self._supplier(db, mapping)
            self.ledger.receipt(mapping['device_key'])
            with self.ledger.transaction() as db:
                row = db.execute('SELECT * FROM acquisition_adapter_v176 WHERE request_key=?', (request_key,)).fetchone()
                mapping, body, _ = self._checked(row)
                supplier = self._supplier(db, mapping)
                value = self._project(mapping, body, supplier)
                encoded = canonical(value)
                if mapping['phase'] in {'completed', 'completed_no_data'}:
                    need(mapping['receipt_json'] == encoded,
                        '原已结束补采的冻结回执发生变化，不重新取样或覆盖')
                else:
                    db.execute('UPDATE acquisition_adapter_v176 SET phase=?,receipt_json=?,receipt_sha256=?,problem=NULL WHERE request_key=?',
                        (value['status'], encoded, sha(encoded), request_key))
        except (HTTPException, ValueError, KeyError, TypeError) as error:
            if not found and isinstance(error, HTTPException) and error.status_code == 404:
                raise
            self._problem(request_key, 'original_receipt_not_confirmed')
            raise HTTPException(409, '尚不能核对原设备终态或资料，原请求与占用保留；未重新下发') from None
        return json.loads(encoded)  # Never share a mutable supplier/cached result.
