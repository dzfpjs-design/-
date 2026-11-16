"""Original-key receipts for the external SIM provider; no tenant authority.

The fixed provider scope identifies the account behind this Node's bearer.
Workspace, grant, task and approval authority remain in the calling platform.
Only a newly committed intent's creator may invoke the old device ledger.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from .device_simulator_v175 import Command, DeviceLedger, MARK


PROVIDER_SCOPE = 'procurement-equipment-provider-v269'
INTENT_SCHEMA = 'device-registered-tool-intent-v269'
OUTPUT_SCHEMA = 'device-action-receipt-v269'
RECEIPT_SCHEMA = 'registered-tool-receipt-v201'
TABLE = 'device_registered_tool_requests_v269'
MAX_RAW_BYTES = 65536
MAX_RECEIPT_BYTES = 512 * 1024
KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
HASH = r'^[a-f0-9]{64}$'
ACTIONS = ('get_status', 'read_telemetry', 'start_mission',
           'return_to_dock', 'cancel_mission', 'set_ptz')
TERMINAL = {'succeeded', 'failed', 'cancelled'}
BODY_KEYS = {'device_id', 'action', 'expected_revision', 'arguments', 'task_id', 'request_key'}
COMMAND_KEYS = ('device_id', 'action', 'expected_revision', 'arguments')
INVALID = '原请求字段、类型或JSON不符合要求'
CONFLICT = '原请求、设备命令或冻结回执无法核对；未重新派发'
UNKNOWN = '原设备结果尚不能核对；保留原号，未重新派发'
FAILED = '原模拟设备动作已失败或取消；未重新派发'
MISSING = '原请求尚无可核对记录，不能证明未执行'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def supplier_key(original_key):
    _key(original_key)
    return 'reg269:' + hashlib.sha256(
        (PROVIDER_SCOPE + '\0' + original_key).encode('utf-8')).hexdigest()


def output_schema():
    """Shared manifest schema; evaluating it constructs no ledger or runtime."""
    properties = {
        'schema': {'const': OUTPUT_SCHEMA},
        'simulation': {'const': True, 'type': 'boolean'},
        'origin_kind': {'const': 'simulated_equipment'},
        'field_verified': {'const': False, 'type': 'boolean'},
        'physical_device_connected': {'const': False, 'type': 'boolean'},
        'observation_created': {'const': False, 'type': 'boolean'},
        'request_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
        'task_id': {'type': 'string', 'pattern':
                    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'},
        'device_id': {'type': 'string', 'minLength': 1, 'maxLength': 64},
        'action': {'enum': list(ACTIONS)},
        'supplier_request_id': {'type': 'string', 'pattern': r'^reg269:[a-f0-9]{64}$'},
        'supplier_input_hash': {'type': 'string', 'pattern': HASH},
        'supplier_receipt_hash': {'type': 'string', 'pattern': HASH},
        'supplier_receipt': {'type': 'object'},
    }
    return {'type': 'object', 'additionalProperties': False,
            'required': list(properties), 'properties': properties}


def _need(ok, message=CONFLICT, status=409):
    if not ok:
        raise HTTPException(status, message)


def _key(value):
    _need(type(value) is str and KEY.fullmatch(value) is not None, INVALID, 422)
    return value


def _strict_json(raw, maximum):
    _need(type(raw) is bytes and 0 < len(raw) <= maximum, INVALID, 422)

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate key')
            value[key] = item
        return value

    def nonfinite(_value):
        raise ValueError('nonfinite number')

    value = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs,
                       parse_constant=nonfinite)
    canonical(value).encode('utf-8', 'strict')
    return value


def _number(value, low, high, *, integer=False):
    _need(type(value) in {int, float} and math.isfinite(value)
          and (not integer or type(value) is int) and low <= value <= high, INVALID, 422)


def _command_arguments(command):
    """Reject malformed arguments before intent; physical authority stays old."""
    action, arguments = command['action'], command['arguments']
    if action in {'get_status', 'read_telemetry', 'return_to_dock', 'cancel_mission'}:
        _need(arguments == {}, INVALID, 422)
    elif action == 'start_mission':
        _need(set(arguments) == {'duration_seconds', 'speed_m_s', 'mission_name'}, INVALID, 422)
        _number(arguments['duration_seconds'], 1, 3240, integer=True)
        _number(arguments['speed_m_s'], 0.1, 15)
        name = arguments['mission_name']
        _need(type(name) is str and 1 <= len(name.strip()) and len(name) <= 120
              and '\0' not in name, INVALID, 422)
    else:
        _need(set(arguments) == {'pan_degrees', 'tilt_degrees', 'zoom'}, INVALID, 422)
        for field, low, high in (('pan_degrees', -180, 180),
                                 ('tilt_degrees', -90, 90), ('zoom', 1, 33)):
            _number(arguments[field], low, high)


def normalize_request(raw, key):
    """Strict full bound body plus the unchanged four-field supplier command."""
    _key(key)
    try:
        value = _strict_json(raw, MAX_RAW_BYTES)
        _need(type(value) is dict and set(value) == BODY_KEYS, INVALID, 422)
        _need(value['request_key'] == key and type(value['request_key']) is str,
              '原请求号与Idempotency-Key不一致', 409)
        task = value['task_id']
        _need(type(task) is str and str(UUID(task)) == task, INVALID, 422)
        command = Command.model_validate({name: value[name] for name in COMMAND_KEYS}).model_dump()
        _command_arguments(command)
        return value, command
    except HTTPException:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise HTTPException(422, INVALID) from None


def _proof(value):
    names = ('provider_scope', 'request_key', 'exact_input_sha256', 'canonical_input',
             'input_hash', 'task_id', 'command_json', 'command_hash', 'supplier_key')
    return digest({'schema': INTENT_SCHEMA, **{name: value[name] for name in names}})


class RegisteredToolAdapterV269:
    def __init__(self, ledger: DeviceLedger):
        self.ledger = ledger
        with ledger.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS ' + TABLE + ' ('
                'provider_scope TEXT NOT NULL,request_key TEXT NOT NULL,exact_input BLOB NOT NULL,'
                'exact_input_sha256 TEXT NOT NULL,canonical_input TEXT NOT NULL,input_hash TEXT NOT NULL,'
                'task_id TEXT NOT NULL,command_json TEXT NOT NULL,command_hash TEXT NOT NULL,'
                'supplier_key TEXT NOT NULL UNIQUE,proof_hash TEXT NOT NULL,phase TEXT NOT NULL,'
                'terminal_json TEXT,terminal_sha256 TEXT,PRIMARY KEY(provider_scope,request_key))')

    @staticmethod
    def _row(db, key):
        return db.execute('SELECT * FROM ' + TABLE + ' WHERE provider_scope=? AND request_key=?',
                          (PROVIDER_SCOPE, key)).fetchone()

    def _checked(self, row):
        try:
            value = dict(row)
            raw = bytes(value['exact_input'])
            body, command = normalize_request(raw, value['request_key'])
            _need(value['provider_scope'] == PROVIDER_SCOPE
                  and value['exact_input_sha256'] == hashlib.sha256(raw).hexdigest()
                  and value['canonical_input'] == canonical(body) and value['input_hash'] == digest(body)
                  and value['task_id'] == body['task_id'] and value['command_json'] == canonical(command)
                  and value['command_hash'] == digest(command)
                  and value['supplier_key'] == supplier_key(value['request_key'])
                  and value['proof_hash'] == _proof(value)
                  and value['phase'] in {'intent', 'pending', 'unknown', 'terminal'})
            terminal = value['terminal_json']
            _need((terminal is None) is (value['terminal_sha256'] is None))
            if terminal is not None:
                _need(value['phase'] == 'terminal'
                      and value['terminal_sha256'] == hashlib.sha256(terminal.encode('utf-8')).hexdigest())
                receipt = _strict_json(terminal.encode('utf-8'), MAX_RECEIPT_BYTES)
                _need(terminal == canonical(receipt))
                self._check_terminal(value, receipt)
            else:
                _need(value['phase'] != 'terminal')
            return value
        except (HTTPException, ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
            raise HTTPException(409, CONFLICT) from None

    @staticmethod
    def _check_supplier(mapping, supplier):
        command = json.loads(mapping['command_json'])
        _need(type(supplier) is dict and supplier.get('request_id') == mapping['supplier_key']
              and supplier.get('device_id') == command['device_id']
              and supplier.get('action') == command['action']
              and supplier.get('status') in TERMINAL | {'accepted', 'running'}
              and all(type(supplier.get(k)) is type(v) and supplier[k] == v for k, v in MARK.items())
              and type(supplier.get('device_revision')) is int and supplier['device_revision'] >= 1
              and 'result' in supplier
              and len(canonical(supplier).encode('utf-8')) <= MAX_RECEIPT_BYTES // 2)

    def _supplier(self, db, mapping):
        """Read-only binding check against the old ledger's saved command."""
        row = db.execute('SELECT * FROM receipts WHERE request_id=?', (mapping['supplier_key'],)).fetchone()
        if row is None:
            return None
        command = json.loads(mapping['command_json'])
        _need(row['body'] == mapping['command_json'] and row['request_hash'] == mapping['command_hash']
              and row['device_id'] == command['device_id'])
        supplier = _strict_json(row['data'].encode('utf-8'), MAX_RECEIPT_BYTES // 2)
        self._check_supplier(mapping, supplier)
        _need(row['status'] == supplier['status'])
        return supplier

    @staticmethod
    def _output(mapping, supplier):
        return {'schema': OUTPUT_SCHEMA, **MARK, 'physical_device_connected': False,
                'observation_created': False, 'request_id': mapping['request_key'],
                'task_id': mapping['task_id'], 'device_id': supplier['device_id'],
                'action': supplier['action'], 'supplier_request_id': mapping['supplier_key'],
                'supplier_input_hash': mapping['command_hash'], 'supplier_receipt_hash': digest(supplier),
                'supplier_receipt': copy.deepcopy(supplier)}

    @staticmethod
    def _envelope(mapping, status, *, output=None, error=None):
        receipt = {'schema': RECEIPT_SCHEMA, 'idempotency_key': mapping['request_key'],
                   'input_hash': mapping['input_hash'], 'status': status,
                   'terminal': status in {'succeeded', 'failed'}}
        if output is not None:
            receipt['output'] = output
        if error is not None:
            receipt['error'] = error
        return receipt

    def _check_terminal(self, mapping, receipt):
        _need(type(receipt) is dict and receipt.get('status') in {'succeeded', 'failed'})
        output = receipt.get('output')
        _need(type(output) is dict and type(output.get('supplier_receipt')) is dict)
        supplier = output['supplier_receipt']
        self._check_supplier(mapping, supplier)
        status = 'succeeded' if supplier['status'] == 'succeeded' else 'failed'
        _need(supplier['status'] in TERMINAL)
        expected = self._envelope(mapping, status, output=self._output(mapping, supplier),
                                  error=FAILED if status == 'failed' else None)
        _need(canonical(receipt) == canonical(expected))

    def _save(self, mapping, receipt):
        encoded = canonical(receipt)
        _need(len(encoded.encode('utf-8')) <= MAX_RECEIPT_BYTES)
        with self.ledger.transaction() as db:
            current = self._checked(self._row(db, mapping['request_key']))
            _need(current['proof_hash'] == mapping['proof_hash'])
            if current['terminal_json'] is not None:
                frozen = json.loads(current['terminal_json'])
                if receipt['terminal']:
                    _need(canonical(frozen) == encoded)
                return frozen
            if receipt['terminal']:
                self._check_terminal(current, receipt)
                db.execute('UPDATE ' + TABLE + ' SET phase=?,terminal_json=?,terminal_sha256=? '
                           'WHERE provider_scope=? AND request_key=?',
                           ('terminal', encoded, hashlib.sha256(encoded.encode('utf-8')).hexdigest(),
                            PROVIDER_SCOPE, mapping['request_key']))
            else:
                db.execute('UPDATE ' + TABLE + ' SET phase=? WHERE provider_scope=? AND request_key=?',
                           (receipt['status'], PROVIDER_SCOPE, mapping['request_key']))
        return json.loads(encoded)

    def receipt(self, key):
        _key(key)
        with self.ledger.transaction() as db:
            row = self._row(db, key)
            _need(row is not None, MISSING, 404)
            mapping = self._checked(row)
            if mapping['terminal_json'] is not None:
                return json.loads(mapping['terminal_json'])
        try:
            # Prove the supplier row exists and is this command BEFORE advancing.
            with self.ledger.transaction() as db:
                supplier = self._supplier(db, mapping)
            if supplier is None:
                return self._save(mapping, self._envelope(mapping, 'unknown', error=UNKNOWN))
            self.ledger.receipt(mapping['supplier_key'])
            with self.ledger.transaction() as db:
                supplier = self._supplier(db, mapping)
            if supplier is None:
                return self._save(mapping, self._envelope(mapping, 'unknown', error=UNKNOWN))
            raw_status = supplier['status']
            if raw_status in {'accepted', 'running'}:
                return self._save(mapping, self._envelope(mapping, 'pending'))
            status = 'succeeded' if raw_status == 'succeeded' else 'failed'
            return self._save(mapping, self._envelope(mapping, status,
                output=self._output(mapping, supplier), error=FAILED if status == 'failed' else None))
        except Exception:
            # A missing/corrupt/unreachable supplier is never proof of non-execution.
            return self._save(mapping, self._envelope(mapping, 'unknown', error=UNKNOWN))

    def submit(self, raw, key):
        _key(key)
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_RAW_BYTES, INVALID, 422)
        created = False
        with self.ledger.transaction() as db:
            previous = self._row(db, key)
            if previous is not None:
                mapping = self._checked(previous)
                _need(bytes(mapping['exact_input']) == raw,
                      '原请求号已绑定其他正文，不能更换任务、设备或参数')
            else:
                body, command = normalize_request(raw, key)
                mapping = {'provider_scope': PROVIDER_SCOPE, 'request_key': key, 'exact_input': raw,
                    'exact_input_sha256': hashlib.sha256(raw).hexdigest(), 'canonical_input': canonical(body),
                    'input_hash': digest(body), 'task_id': body['task_id'], 'command_json': canonical(command),
                    'command_hash': digest(command), 'supplier_key': supplier_key(key)}
                _need(db.execute('SELECT 1 FROM receipts WHERE request_id=?',
                                 (mapping['supplier_key'],)).fetchone() is None)
                names = ('provider_scope', 'request_key', 'exact_input', 'exact_input_sha256',
                         'canonical_input', 'input_hash', 'task_id', 'command_json', 'command_hash', 'supplier_key')
                db.execute('INSERT INTO ' + TABLE + ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (*[mapping[name] for name in names], _proof(mapping), 'intent', None, None))
                created = True
        # Both journal transaction and locks are released before the old invoke.
        if created:
            try:
                self.ledger.invoke(mapping['supplier_key'],
                                   Command.model_validate(json.loads(mapping['command_json'])))
            except Exception:
                pass  # Only the independently read original receipt can settle.
        return self.receipt(key)


async def _bounded_body(request):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        _need(size <= MAX_RAW_BYTES, INVALID, 422)
        chunks.append(chunk)
    return b''.join(chunks)


def register_routes(app: FastAPI, ledger: DeviceLedger, token: str):
    """Attach to the same external Node; no tenant routing or extra listener."""
    if type(token) is not str or len(token) < 24:
        raise ValueError('需提供至少24字符的独立SIM Bearer密钥')
    expected = ('Bearer ' + token).encode('utf-8')
    if getattr(app.state, 'registered_tool_adapter_v269', None) is not None:
        raise ValueError('V269路由已经注册')
    adapter = RegisteredToolAdapterV269(ledger)
    app.state.registered_tool_adapter_v269 = adapter

    def authorize(value):
        _need(type(value) is str and hmac.compare_digest(value.encode('utf-8'), expected),
              '模拟器认证失败', 401)

    @app.post('/registered-tools/invoke')
    async def invoke_route(request: Request, authorization: str | None = Header(default=None),
                           key: str | None = Header(default=None, alias='Idempotency-Key')):
        authorize(authorization)
        _key(key)
        raw = await _bounded_body(request)
        receipt = await run_in_threadpool(adapter.submit, raw, key)
        if receipt['status'] == 'succeeded':
            return receipt['output']
        if receipt['status'] == 'pending':
            return Response(status_code=202)
        raise HTTPException(409, FAILED if receipt['status'] == 'failed' else UNKNOWN)

    @app.get('/registered-tools/receipts')
    async def receipt_route(authorization: str | None = Header(default=None),
                            key: str | None = Header(default=None, alias='Idempotency-Key')):
        authorize(authorization)
        _key(key)
        return await run_in_threadpool(adapter.receipt, key)

    return adapter
