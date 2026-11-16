"""Fixed registered-tool transport over the real, isolated V200 SIM ledger.

An installation receipt is not a successful device effect. The bridge retains
the exact tool bytes before calling install, never re-posts a retained command,
and freezes its result only after a real successful original device tick. It
does not authorize a platform Task, physical device, route or cloud transfer.
"""
from contextlib import contextmanager
from pathlib import Path
import argparse
import copy
import fcntl
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import device_monitoring_simulator_v200 as monitoring
from .device_simulator_v175 import KEY, MARK, _json, _request_hash, _utc

PROTOCOL = 'registered-tool-receipt-v201'
COMMAND = 'device-monitoring-tool-command-v201'
OUTPUT = 'device-monitoring-tool-output-v201'
MAX_RESULTS = 10000
MAX_RESULT_BYTES = 256000
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_FLAGS = {**MARK, 'physical_device_connected': False, 'flight_path_verified': False,
    'image_generation': False, 'hardware_authorized': False}
_ERRORS = {'original_monitoring_install_unknown', 'original_device_action_unknown',
    'monitoring_install_rejected', 'monitoring_first_effect_failed', 'monitoring_plan_not_active'}

# The plan executor owns this envelope's settlement semantics. This standalone
# bridge never emits not_executed: missing/failed/unknown is not that proof.
RECEIPT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['schema', 'idempotency_key', 'input_hash', 'status', 'terminal'],
    'properties': {
        'schema': {'const': PROTOCOL},
        'idempotency_key': {'type': 'string', 'pattern': KEY.pattern},
        'input_hash': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
        'status': {'enum': ['succeeded', 'pending', 'failed', 'unknown', 'not_executed']},
        'terminal': {'type': 'boolean'}, 'output': {'type': 'object'},
        'error': {'type': 'string', 'minLength': 1, 'maxLength': 120}},
    'allOf': [
        {'if': {'properties': {'status': {'const': 'succeeded'}}},
         'then': {'required': ['output'], 'properties': {'terminal': {'const': True}},
                  'not': {'required': ['error']}}},
        {'if': {'properties': {'status': {'enum': ['pending', 'unknown']}}},
         'then': {'properties': {'terminal': {'const': False}}, 'not': {'required': ['output']}}},
        {'if': {'properties': {'status': {'enum': ['failed', 'not_executed']}}},
         'then': {'properties': {'terminal': {'const': True}}, 'not': {'required': ['output']}}},
        {'if': {'properties': {'status': {'enum': ['failed', 'unknown']}}},
         'then': {'required': ['error']}},
        {'if': {'properties': {'status': {'const': 'pending'}}},
         'then': {'not': {'required': ['error']}}}]}


def input_schema():
    """The actual V200 install arguments; no key, URL, secret or free text DSL."""
    def closed(properties):
        return {'type': 'object', 'additionalProperties': False,
            'required': list(properties), 'properties': properties}
    text = {'type': 'string', 'minLength': 1, 'maxLength': 120}
    sha = {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
    stamp = {'type': 'string', 'format': 'date-time', 'maxLength': 40}
    drone = closed({'kind': {'const': 'drone'}, 'valid_from': stamp, 'valid_until': stamp,
        'route_ref': text, 'route_hash': sha, 'duration_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 3240},
        'speed_m_s': {'type': 'number', 'minimum': .1, 'maximum': 15}, 'mission_name': text,
        'interval_seconds': {'type': 'integer', 'minimum': 5, 'maximum': 86400},
        'max_cycles': {'type': 'integer', 'minimum': 1, 'maximum': monitoring.MAX_CYCLES}})
    camera = closed({'kind': {'const': 'camera'}, 'valid_from': stamp, 'valid_until': stamp,
        'preset_ref': text, 'preset_hash': sha,
        'pan_degrees': {'type': 'number', 'minimum': -180, 'maximum': 180},
        'tilt_degrees': {'type': 'number', 'minimum': -90, 'maximum': 90},
        'zoom': {'type': 'number', 'minimum': 1, 'maximum': 33}})
    value = closed({'schema': {'const': monitoring.INSTALL},
        'device_id': {'enum': ['sim-dock-01', 'sim-camera-01']},
        'expected_version': {'type': 'integer', 'minimum': 0, 'maximum': 1000},
        'expected_device_revision': {'type': 'integer', 'minimum': 1, 'maximum': 2 ** 63 - 1},
        'configuration': {'oneOf': [drone, camera]}})
    value['allOf'] = [{'if': {'properties': {'device_id': {'const': 'sim-dock-01'}}},
        'then': {'properties': {'configuration': drone}},
        'else': {'properties': {'configuration': camera}}}]
    return copy.deepcopy(value)


def output_schema():
    """The frozen first-effect output, rather than a dynamic device view.

    The bridge separately checks all hashes, original inventory and relations;
    schema validity alone does not prove an effect or execution permission.
    """
    def closed(properties):
        return {'type': 'object', 'additionalProperties': False,
            'required': list(properties), 'properties': properties}
    flags = {key: {'const': value} for key, value in _FLAGS.items()}
    key = {'type': 'string', 'pattern': KEY.pattern}
    sha = {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
    positive = {'type': 'integer', 'minimum': 1}
    stamp = {'type': 'string', 'format': 'date-time', 'maxLength': 40}
    devices = {'enum': ['sim-dock-01', 'sim-camera-01']}
    configs = input_schema()['properties']['configuration']
    drone_args = closed({'duration_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 3240},
        'speed_m_s': {'type': 'number', 'minimum': .1, 'maximum': 15},
        'mission_name': {'type': 'string', 'minLength': 1, 'maxLength': 120}})
    camera_args = closed({'pan_degrees': {'type': 'number', 'minimum': -180, 'maximum': 180},
        'tilt_degrees': {'type': 'number', 'minimum': -90, 'maximum': 90},
        'zoom': {'type': 'number', 'minimum': 1, 'maximum': 33}})
    command = closed({'device_id': devices, 'action': {'enum': ['start_mission', 'set_ptz']},
        'expected_revision': positive, 'arguments': {'oneOf': [drone_args, camera_args]}})
    command['allOf'] = [{'if': {'properties': {'action': {'const': 'start_mission'}}},
        'then': {'properties': {'device_id': {'const': 'sim-dock-01'}, 'arguments': drone_args}},
        'else': {'properties': {'device_id': {'const': 'sim-camera-01'}, 'arguments': camera_args}}}]
    device_receipt = closed({**{name: {'const': value} for name, value in MARK.items()},
        'request_id': key, 'device_id': devices, 'action': {'enum': ['start_mission', 'set_ptz']},
        'status': {'const': 'succeeded'}, 'device_revision': positive,
        'accepted_at': stamp, 'updated_at': stamp,
        'result': closed({'simulated': {'const': True}, 'device_revision': positive,
            'artifacts': {'type': 'array', 'maxItems': 0}, 'field_observation_created': {'const': False}}),
        'completion_requires_receipt': {'const': True}})
    tick = closed({'schema': {'const': monitoring.TICK}, **flags,
        'request_key': key, 'device_id': devices, 'configuration_version': positive,
        'configuration_hash': sha, 'ordinal': {'type': 'integer', 'minimum': 1, 'maximum': monitoring.MAX_CYCLES},
        'scheduled_for': {'type': 'number'}, 'prepared_at': stamp,
        'command': command, 'input_hash': sha, 'status': {'const': 'succeeded'},
        'receipt': device_receipt, 'reason': {'type': 'null'}})
    installed = closed({'schema': {'const': monitoring.RECEIPT}, **flags,
        'request_key': key, 'body_sha256': sha, 'device_id': devices, 'version': positive,
        'configuration_hash': sha, 'status': {'const': 'installed'}, 'created_at': stamp,
        'in_flight_tick_keys': {'type': 'array', 'maxItems': 0},
        'first_action_verified': {'const': False}, 'stop_cancels_in_flight_device_action': {'const': False}})
    return copy.deepcopy(closed({'schema': {'const': OUTPUT}, **flags,
        'install_receipt': installed, 'configuration': configs,
        'first_successful_tick': tick, 'device_revision': positive, 'first_effect_completed': {'const': True}}))


def need(ok, message='原工具请求、首个实际设备回执或固定结果无法核对；未重新安装或派发', status=409):
    if not ok: raise HTTPException(status, message)


def _sha(raw): return hashlib.sha256(raw).hexdigest()
def _digest(value): return _request_hash(value)
def _same(a, b): return _json(a) == _json(b)


def _loads(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('duplicate_key')
            value[key] = item
        return value
    def invalid(_value): raise ValueError('nonfinite')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    _json(value).encode('utf-8')
    return value


def _envelope(command, status, *, output=None, error=None):
    value = {'schema': PROTOCOL, 'idempotency_key': command['request_key'],
        'input_hash': command['input_hash'], 'status': status,
        'terminal': status in {'succeeded', 'failed'}}
    if output is not None: value['output'] = copy.deepcopy(output)
    if error is not None:
        need(error in _ERRORS); value['error'] = error
    return value


class ToolBridge:
    def __init__(self, ledger: monitoring.MonitoringLedger):
        need(isinstance(ledger, monitoring.MonitoringLedger), status=422)
        self.ledger = ledger
        self.lock_path = ledger.path.with_name(ledger.path.name + '.tool-bridge-v201.lock')
        with self.ledger.devices.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS monitoring_tool_commands_v201 (request_key TEXT PRIMARY KEY,raw TEXT NOT NULL,record TEXT NOT NULL,record_hash TEXT NOT NULL,result_hash TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS monitoring_tool_results_v201 (request_key TEXT PRIMARY KEY,result TEXT NOT NULL,result_hash TEXT NOT NULL)')

    @contextmanager
    def locked(self):
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX); yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    def _inventory(self, db):
        try:
            commands = {row['request_key']: dict(row) for row in db.execute('SELECT * FROM monitoring_tool_commands_v201')}
            results = {row['request_key']: dict(row) for row in db.execute('SELECT * FROM monitoring_tool_results_v201')}
            need(len(commands) <= MAX_RESULTS and set(results) <= set(commands))
            source = self.ledger._inventory(db)
            for key, row in commands.items():
                raw = row['raw'].encode('utf-8'); body = monitoring.parse_wire(raw); record = _loads(row['record'])
                need(body['schema'] == monitoring.INSTALL and type(key) is str and KEY.fullmatch(key)
                    and type(record) is dict and set(record) == {'schema', 'request_key', 'input_hash',
                        'raw_sha256', 'device_id', 'configuration_hash', 'created_at'}
                    and record['schema'] == COMMAND and record['request_key'] == key
                    and record['input_hash'] == _digest(body) and record['raw_sha256'] == _sha(raw)
                    and record['device_id'] == body['device_id']
                    and record['configuration_hash'] == _digest(body['configuration'])
                    and row['record_hash'] == _digest(record))
                monitoring._instant(record['created_at'])
                row.update(record=record, body=body)
                result = results.get(key)
                need((row['result_hash'] is None) == (result is None))
                if result is not None:
                    value = _loads(result['result'])
                    need(type(value) is dict and len(result['result'].encode('utf-8')) <= MAX_RESULT_BYTES
                        and result['result_hash'] == row['result_hash'] == _digest(value))
                    if value.get('status') == 'failed' and value.get('error') == 'monitoring_install_rejected':
                        need(key not in source[0]); expected = _envelope(record, 'failed', error='monitoring_install_rejected')
                    else: expected = self._current(row, source)
                    need(expected['terminal'] is True and _same(value, expected))
                    result['value'] = value
            return commands, results, source
        except HTTPException as error:
            if error.status_code == 422: need(False)
            raise
        except (ValueError, KeyError, TypeError, UnicodeError, OverflowError, RecursionError):
            need(False)

    @staticmethod
    def _current(command, source):
        record, body = command['record'], command['body']; installed = source[0].get(record['request_key'])
        if installed is None:
            return _envelope(record, 'unknown', error='original_monitoring_install_unknown')
        need(installed['raw'] == command['raw'] and installed['raw_sha256'] == record['raw_sha256'])
        original = _loads(installed['result'])
        need(original['schema'] == monitoring.RECEIPT and original['request_key'] == record['request_key']
            and original['device_id'] == body['device_id'] and original['configuration_hash'] == record['configuration_hash']
            and original['status'] == 'installed')
        ticks = sorted([tick for tick in source[2].values()
            if tick['device_id'] == body['device_id'] and tick['configuration_version'] == original['version']],
            key=lambda tick: tick['ordinal'])
        successes = [tick for tick in ticks if tick['status'] == 'succeeded']
        if successes:
            tick = successes[0]; receipt = tick['receipt']
            need(type(receipt) is dict and tick['schema'] == monitoring.TICK and receipt['status'] == 'succeeded'
                and tick['configuration_hash'] == record['configuration_hash']
                and all(type(tick.get(key)) is type(value) and tick[key] == value for key, value in _FLAGS.items()))
            need(type(receipt.get('result')) is dict and 'device_revision' in receipt['result'])
            revision = receipt['result']['device_revision']
            need(type(revision) is int and revision >= 1)
            output = {'schema': OUTPUT, **_FLAGS, 'install_receipt': copy.deepcopy(original),
                'configuration': copy.deepcopy(body['configuration']), 'first_successful_tick': copy.deepcopy(tick),
                'device_revision': revision, 'first_effect_completed': True}
            return _envelope(record, 'succeeded', output=output)
        if any(tick['status'] in {'prepared', 'unknown'} for tick in ticks):
            return _envelope(record, 'unknown', error='original_device_action_unknown')
        if any(tick['status'] in {'accepted', 'running'} for tick in ticks):
            return _envelope(record, 'pending')
        if any(tick['status'] in {'failed', 'cancelled', 'blocked'} for tick in ticks):
            return _envelope(record, 'failed', error='monitoring_first_effect_failed')
        slot = source[1].get(body['device_id']); need(slot is not None)
        if slot['runtime']['configuration_version'] != original['version'] or slot['runtime']['status'] in {
                'stopped', 'expired', 'blocked', 'completed'}:
            return _envelope(record, 'failed', error='monitoring_plan_not_active')
        return _envelope(record, 'pending')

    @staticmethod
    def _store_result(db, key, value):
        encoded = _json(value); need(len(encoded.encode('utf-8')) <= MAX_RESULT_BYTES)
        hashed = _digest(value)
        db.execute('INSERT INTO monitoring_tool_results_v201 VALUES (?,?,?)', (key, encoded, hashed))
        db.execute('UPDATE monitoring_tool_commands_v201 SET result_hash=? WHERE request_key=?', (hashed, key))

    def _answer_locked(self, key):
        with self.ledger.devices.transaction() as db:
            commands, results, source = self._inventory(db)
            need(key in commands, '原工具请求未找到；不能证明未执行或自动换号', 404)
            if key in results: return copy.deepcopy(results[key]['value'])
            value = self._current(commands[key], source)
            if value['terminal']:
                self._store_result(db, key, value); self._inventory(db)
            return copy.deepcopy(value)

    def install(self, key, raw):
        need(type(key) is str and KEY.fullmatch(key), '原工具编号无效', 422)
        need(type(raw) is bytes and 0 < len(raw) <= monitoring.MAX_RAW_BYTES, '原工具正文无效或超过上限', 422)
        with self.locked():
            with self.ledger.devices.transaction() as db:
                commands, _, _ = self._inventory(db)
                if key in commands:
                    need(commands[key]['raw'].encode('utf-8') == raw, '原工具编号已绑定其他正文')
                    retained = True
                else:
                    body = monitoring.parse_wire(raw); need(body['schema'] == monitoring.INSTALL, status=422)
                    need(len(commands) < MAX_RESULTS)
                    record = {'schema': COMMAND, 'request_key': key, 'input_hash': _digest(body),
                        'raw_sha256': _sha(raw), 'device_id': body['device_id'],
                        'configuration_hash': _digest(body['configuration']), 'created_at': _utc(self.ledger._now())}
                    db.execute('INSERT INTO monitoring_tool_commands_v201 VALUES (?,?,?,?,NULL)',
                        (key, raw.decode('utf-8'), _json(record), _digest(record)))
                    self._inventory(db); retained = False
            if not retained:
                # The exact key/body are durable before install. No bridge
                # SQLite transaction remains open while V200 opens its own.
                try: self.ledger.install(key, raw)
                except sqlite3.Error: raise
                except HTTPException:
                    with self.ledger.devices.transaction() as db:
                        commands, _, source = self._inventory(db)
                        if key not in source[0]:
                            value = _envelope(commands[key]['record'], 'failed', error='monitoring_install_rejected')
                            self._store_result(db, key, value); self._inventory(db)
                except Exception:
                    # A response can be lost after installation. Original
                    # inventory below, not this exception, determines status.
                    pass
            return self._answer_locked(key)

    def receipt(self, key):
        need(type(key) is str and KEY.fullmatch(key), '原工具编号无效', 422)
        with self.locked(): return self._answer_locked(key)


def create_app(state_path: Path, token: str, *, clock=time.time, auto_consume=True, poll_seconds=.25):
    app = monitoring.create_app(state_path, token, clock=clock, auto_consume=auto_consume, poll_seconds=poll_seconds)
    bridge = ToolBridge(app.state.monitoring_ledger); app.state.monitoring_tool_bridge = bridge
    def authorize(value):
        need(type(value) is str and hmac.compare_digest(value.encode(), ('Bearer ' + token).encode()), '模拟工具认证失败', 401)
    def response(value):
        return JSONResponse(value, status_code=200 if value['terminal'] else 202)
    @app.post('/tools/monitoring/install')
    async def install(request: Request, authorization: str | None = Header(default=None),
            key: str | None = Header(default=None, alias='Idempotency-Key')):
        authorize(authorization); need(not request.query_params, '工具接口不接受地址或动态查询参数', 422)
        value = await run_in_threadpool(bridge.install, key, await request.body())
        return response(value)
    @app.get('/tools/monitoring/receipt')
    def receipt(request: Request, authorization: str | None = Header(default=None),
            key: str | None = Header(default=None, alias='Idempotency-Key')):
        authorize(authorization); need(not request.query_params, '原工具回执只接受原编号header', 422)
        # Generic bounded_response deliberately discards HTTP202 bodies. A
        # receipt GET must preserve the actual pending/unknown JSON as well.
        return JSONResponse(bridge.receipt(key), status_code=200)
    return app


def main():
    parser = argparse.ArgumentParser(description='仅127.0.0.1的SIM监测注册工具桥；不接真实硬件')
    parser.add_argument('--state', required=True, type=Path); parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535: parser.error('端口需在1024至65535之间')
    app = create_app(args.state, os.environ.get('GA_MONITORING_TOOL_BRIDGE_TOKEN', ''))
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)


if __name__ == '__main__': main()
