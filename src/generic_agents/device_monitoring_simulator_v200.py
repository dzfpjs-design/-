"""Bounded laboratory patrol/focus schedules over the unchanged V175 ledger.

Use a new, private SQLite file. Route/preset references are frozen selections,
not a waypoint interpreter, field observation or hardware authorization. Every
timer tick first commits its original key/body, then calls the existing device
ledger outside that transaction. A retained tick is only receipt-read again.
"""
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from pathlib import Path
import argparse
import asyncio
import copy
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import time

from fastapi import Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .device_simulator_v175 import Command, DeviceLedger, KEY, MARK, _json, _profiles, _request_hash, _utc

INSTALL = 'device-monitoring-install-v200'
STOP = 'device-monitoring-stop-v200'
RECEIPT = 'device-monitoring-command-receipt-v200'
VIEW = 'device-monitoring-plan-view-v200'
TICK = 'device-monitoring-tick-v200'
MAX_RAW_BYTES = 65536
MAX_CYCLES = 100
MAX_ROWS = 10000
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z')
_PENDING = {'prepared', 'unknown', 'accepted', 'running'}
_TERMINAL = {'succeeded', 'failed', 'cancelled', 'blocked'}
_FLAGS = {**MARK, 'physical_device_connected': False, 'flight_path_verified': False,
    'image_generation': False, 'hardware_authorized': False}


def need(ok, message='原监测配置、请求或模拟设备回执无法核对；未重新派发', status=409):
    if not ok: raise HTTPException(status, message)


def _digest(value): return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()
def _sha(raw): return hashlib.sha256(raw).hexdigest()
def _equal(a, b): return _json(a) == _json(b)


def _text(value, maximum=120):
    need(type(value) is str and bool(value.strip()) and '\x00' not in value
        and len(value) <= maximum, '需提供完整明确的名称或引用', 422)


def _integer(value, low, high):
    need(type(value) is int and low <= value <= high, '版本、次数或时间间隔须为范围内的整数', 422)


def _instant(value):
    need(type(value) is str and _TIME.fullmatch(value), '需提供带时区的明确生效/到期时间', 422)
    try: return datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value).timestamp()
    except (ValueError, OverflowError): need(False, '生效/到期时间无效', 422)


def _configuration(value, device_id):
    need(type(value) is dict, status=422)
    common = {'kind', 'valid_from', 'valid_until'}
    drone = {'route_ref', 'route_hash', 'duration_seconds', 'speed_m_s', 'mission_name',
        'interval_seconds', 'max_cycles'}
    camera = {'preset_ref', 'preset_hash', 'pan_degrees', 'tilt_degrees', 'zoom'}
    kind = value.get('kind'); need(kind in {'drone', 'camera'}, status=422)
    need(set(value) == common | (drone if kind == 'drone' else camera), status=422)
    profiles = {row['id']: row for row in _profiles()}
    need(device_id in profiles and profiles[device_id]['kind'] ==
        ('drone_dock' if kind == 'drone' else 'ptz_camera'), '设备与监测配置类型不一致', 422)
    start, end = _instant(value['valid_from']), _instant(value['valid_until'])
    need(start < end and end - start <= 7 * 86400, 'SIM计划窗口须非空且不超过七天', 422)
    ref = 'route_ref' if kind == 'drone' else 'preset_ref'
    _text(value[ref]); need(type(value[ref.replace('_ref', '_hash')]) is str
        and _HASH.fullmatch(value[ref.replace('_ref', '_hash')]), status=422)
    if kind == 'drone':
        _integer(value['duration_seconds'], 1, 3240); _integer(value['interval_seconds'], 5, 86400)
        _integer(value['max_cycles'], 1, MAX_CYCLES); _text(value['mission_name'])
        need(value['interval_seconds'] >= value['duration_seconds'], 'SIM间隔不能短于本次模拟飞行时长', 422)
        DeviceLedger._number(value, 'speed_m_s', 0.1, 15)
    else:
        DeviceLedger._number(value, 'pan_degrees', -180, 180)
        DeviceLedger._number(value, 'tilt_degrees', -90, 90)
        DeviceLedger._number(value, 'zoom', 1, 33)
    _json(value)
    return copy.deepcopy(value)


def parse_wire(raw):
    try:
        need(type(raw) is bytes and 0 < len(raw) <= MAX_RAW_BYTES, status=422)
        def pairs(items):
            result = {}
            for key, value in items:
                need(key not in result, '原JSON不能包含重复键', 422); result[key] = value
            return result
        def nonfinite(_value): need(False, '原JSON不能包含非有限数值', 422)
        body = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=nonfinite)
        need(type(body) is dict and body.get('schema') in {INSTALL, STOP}, status=422)
        keys = {'schema', 'device_id', 'expected_version'} | (
            {'expected_device_revision', 'configuration'} if body['schema'] == INSTALL else {'reason'})
        need(set(body) == keys, status=422); _text(body['device_id'], 64)
        _integer(body['expected_version'], 0, 1000)
        if body['schema'] == INSTALL:
            _integer(body['expected_device_revision'], 1, 2 ** 63 - 1)
            _configuration(body['configuration'], body['device_id'])
        else: _text(body['reason'], 2000)
        _json(body).encode('utf-8'); return body
    except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError):
        need(False, '原JSON无效；未安装或修改监测配置', 422)


class MonitoringLedger:
    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        need(not self.path.is_symlink(), '监测状态文件不能为符号链接', 422)
        if self.path.exists():
            # Never add V200 tables to a pre-existing V175-only state file.
            with sqlite3.connect(self.path.absolute().as_uri() + '?mode=ro', uri=True) as db:
                need(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='monitoring_meta_v200'").fetchone()
                    is not None, '须指定新的独立V200状态文件，不能接管旧175台账', 422)
        self.devices = DeviceLedger(self.path, clock)
        self.lock_path = self.path.with_name(self.path.name + '.monitor-v200.lock')
        with self.devices.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS monitoring_meta_v200 (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute("INSERT OR IGNORE INTO monitoring_meta_v200 VALUES ('schema','1')")
            need(db.execute("SELECT value FROM monitoring_meta_v200 WHERE key='schema'").fetchone()[0] == '1')
            db.execute('CREATE TABLE IF NOT EXISTS monitoring_commands_v200 (request_key TEXT PRIMARY KEY,raw TEXT NOT NULL,raw_sha256 TEXT NOT NULL,result TEXT NOT NULL,result_hash TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS monitoring_versions_v200 (device_id TEXT NOT NULL,version INTEGER NOT NULL,request_key TEXT NOT NULL UNIQUE,record TEXT NOT NULL,record_hash TEXT NOT NULL,PRIMARY KEY(device_id,version))')
            db.execute('CREATE TABLE IF NOT EXISTS monitoring_slots_v200 (device_id TEXT PRIMARY KEY,version INTEGER NOT NULL,runtime TEXT NOT NULL,runtime_hash TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS monitoring_ticks_v200 (request_key TEXT PRIMARY KEY,device_id TEXT NOT NULL,version INTEGER NOT NULL,ordinal INTEGER NOT NULL,record TEXT NOT NULL,record_hash TEXT NOT NULL,UNIQUE(device_id,version,ordinal))')

    @contextmanager
    def locked(self):
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

    def _now(self):
        value = self.clock(); need(type(value) in (int, float) and math.isfinite(value))
        # The old ledger's public timestamps have microsecond precision. Use
        # that same instant for the durable due calculation, not an unrecorded
        # sub-microsecond float that cannot survive a cold reconstruction.
        return _instant(_utc(value))

    @staticmethod
    def _slot(db, device_id, version, runtime):
        db.execute('INSERT INTO monitoring_slots_v200 VALUES (?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET version=excluded.version,runtime=excluded.runtime,runtime_hash=excluded.runtime_hash',
            (device_id, version, _json(runtime), _digest(runtime)))

    @staticmethod
    def _tick(db, record):
        db.execute('UPDATE monitoring_ticks_v200 SET record=?,record_hash=? WHERE request_key=?',
            (_json(record), _digest(record), record['request_key']))

    def _inventory(self, db):
        try: return self._checked_inventory(db)
        except HTTPException as error:
            if error.status_code == 422: need(False)
            raise
        except (ValueError, KeyError, TypeError, UnicodeError, OverflowError, RecursionError):
            need(False)

    def _checked_inventory(self, db):
        commands = {row['request_key']: dict(row) for row in db.execute('SELECT * FROM monitoring_commands_v200')}
        versions = list(db.execute('SELECT * FROM monitoring_versions_v200 ORDER BY device_id,version'))
        slots = {row['device_id']: dict(row) for row in db.execute('SELECT * FROM monitoring_slots_v200')}
        ticks = list(db.execute('SELECT * FROM monitoring_ticks_v200 ORDER BY device_id,version,ordinal'))
        need(len(commands) == len(versions) <= MAX_ROWS and len(ticks) <= MAX_ROWS)
        histories, configs, decoded_ticks = {}, {}, {}
        for row in versions:
            command = commands.get(row['request_key']); need(command is not None)
            raw = command['raw'].encode('utf-8'); body = parse_wire(raw); record = json.loads(row['record'])
            previous = histories.get(row['device_id'], [])
            need(row['version'] == len(previous) + 1 and body['expected_version'] == row['version'] - 1
                and body['device_id'] == row['device_id'] and command['raw_sha256'] == _sha(raw)
                and row['record_hash'] == _digest(record)
                and record['previous_hash'] == (previous[-1]['record_hash'] if previous else None)
                and record['device_id'] == row['device_id'] and record['version'] == row['version']
                and record['request_key'] == row['request_key'] and record['body_sha256'] == command['raw_sha256'])
            if body['schema'] == INSTALL:
                need(record['operation'] == 'install' and record['configuration_version'] == row['version']
                    and _equal(record['configuration'], body['configuration']))
            else:
                need(previous and record['operation'] == 'stop' and record['configuration_version'] == previous[-1]['record']['configuration_version']
                    and _equal(record['configuration'], previous[-1]['record']['configuration']))
            need(record['configuration_hash'] == _digest(record['configuration']))
            result = json.loads(command['result'])
            need(command['result_hash'] == _digest(result) and _equal(result, self._creation_receipt(record)))
            current = {'record': record, 'record_hash': row['record_hash']}
            histories.setdefault(row['device_id'], []).append(current)
            if body['schema'] == INSTALL: configs[(row['device_id'], row['version'])] = record
        need(set(histories) == set(slots))
        for row in ticks:
            tick = json.loads(row['record']); config = configs.get((row['device_id'], row['version']))
            need(config is not None and row['record_hash'] == _digest(tick)
                and tick['request_key'] == row['request_key'] and tick['device_id'] == row['device_id']
                and tick['configuration_version'] == row['version'] and tick['ordinal'] == row['ordinal']
                and tick['configuration_hash'] == config['configuration_hash']
                and tick['input_hash'] == _request_hash(tick['command'])
                and tick['command']['device_id'] == row['device_id']
                and tick['command']['action'] == ('start_mission' if config['configuration']['kind'] == 'drone' else 'set_ptz')
                and tick['status'] in _PENDING | _TERMINAL)
            expected_key = 'monitor200:' + _digest({'device_id': row['device_id'], 'version': row['version'],
                'ordinal': row['ordinal'], 'install_request_key': config['request_key']})
            need(tick['request_key'] == expected_key and _equal(tick['command']['arguments'], self._arguments(config['configuration'])))
            receipt = tick['receipt']
            if receipt is not None:
                stored = db.execute('SELECT * FROM receipts WHERE request_id=?', (tick['request_key'],)).fetchone()
                need(stored is not None and stored['request_hash'] == tick['input_hash']
                    and _equal(json.loads(stored['body']), tick['command']))
                self._receipt(tick, receipt)
                need(receipt['status'] == tick['status'])
                actual = json.loads(stored['data'])
                need(receipt['accepted_at'] == actual['accepted_at'])
                if receipt['status'] in {'succeeded', 'failed', 'cancelled'}:
                    need(_equal(receipt, actual))
            else:
                need(tick['status'] in {'prepared', 'unknown', 'blocked'})
            decoded_ticks[tick['request_key']] = tick
        for device_id, slot in slots.items():
            latest = histories[device_id][-1]['record']; runtime = json.loads(slot['runtime'])
            selected = [tick for tick in decoded_ticks.values() if tick['device_id'] == device_id
                and tick['configuration_version'] == latest['configuration_version']]
            need(slot['version'] == latest['version'] and slot['runtime_hash'] == _digest(runtime)
                and runtime['configuration_version'] == latest['configuration_version']
                and runtime['cycles_started'] == len(selected)
                and [tick['ordinal'] for tick in selected] == list(range(1, len(selected) + 1))
                and runtime['first_action_verified'] is any(tick['status'] == 'succeeded' for tick in selected)
                and runtime['status'] in {'installed', 'waiting_receipt', 'active', 'completed', 'stopped', 'expired', 'blocked', 'unknown'})
            config = latest['configuration']; maximum = config['max_cycles'] if config['kind'] == 'drone' else 1
            need(len(selected) <= maximum)
            if runtime['status'] == 'installed': need(not selected)
            if runtime['status'] in {'active', 'completed'}:
                need(selected and selected[-1]['status'] == 'succeeded')
            if runtime['status'] == 'completed': need(len(selected) == maximum)
            if runtime['status'] in {'stopped', 'expired'}:
                expected_due = None
            elif not selected:
                expected_due = max(_instant(latest['created_at']), _instant(config['valid_from']))
            elif config['kind'] == 'drone' and len(selected) < maximum:
                expected_due = _instant(selected[-1]['prepared_at']) + config['interval_seconds']
            else: expected_due = None
            need(_equal(runtime['next_due'], expected_due))
            if latest['operation'] == 'stop': need(runtime['status'] == 'stopped')
            slot.update(runtime=runtime, record=latest)
        return commands, slots, decoded_ticks

    @staticmethod
    def _arguments(config):
        keys = ('duration_seconds', 'speed_m_s', 'mission_name') if config['kind'] == 'drone' else ('pan_degrees', 'tilt_degrees', 'zoom')
        return {key: copy.deepcopy(config[key]) for key in keys}

    @staticmethod
    def _creation_receipt(record):
        return {'schema': RECEIPT, **_FLAGS, 'request_key': record['request_key'],
            'body_sha256': record['body_sha256'], 'device_id': record['device_id'], 'version': record['version'],
            'configuration_hash': record['configuration_hash'], 'status': 'installed' if record['operation'] == 'install' else 'stopped',
            'created_at': record['created_at'], 'in_flight_tick_keys': record['in_flight_tick_keys'],
            'first_action_verified': False, 'stop_cancels_in_flight_device_action': False}

    def _write_command(self, db, key, raw, record):
        result = self._creation_receipt(record)
        db.execute('INSERT INTO monitoring_commands_v200 VALUES (?,?,?,?,?)',
            (key, raw.decode('utf-8'), _sha(raw), _json(result), _digest(result)))
        db.execute('INSERT INTO monitoring_versions_v200 VALUES (?,?,?,?,?)',
            (record['device_id'], record['version'], key, _json(record), _digest(record)))
        return result

    def install(self, key, raw):
        need(type(key) is str and KEY.fullmatch(key), status=422); body = parse_wire(raw)
        need(body['schema'] == INSTALL, status=422)
        with self.locked():
            with self.devices.transaction() as db:
                commands, slots, ticks = self._inventory(db)
                if key in commands:
                    need(commands[key]['raw'] == raw.decode('utf-8')); return json.loads(commands[key]['result'])
                old = slots.get(body['device_id']); version = old['version'] if old else 0
                need(body['expected_version'] == version, '监测配置版本已变化，原请求未应用')
                need(not any(tick['device_id'] == body['device_id'] and tick['status'] in _PENDING for tick in ticks.values()),
                    '原动作或未知回执尚未处理，不能安装另一份计划')
                previous_hash = _digest(old['record']) if old else None
            device = self.devices.device(body['device_id'])
            need(device['revision'] == body['expected_device_revision'], '设备版本已变化，未安装配置')
            need(device['state']['online'] and device['state']['fault'] is None and device['state']['active_request_id'] is None,
                '设备离线、故障或占用，未安装另一份监测计划')
            config = body['configuration']; now = self._now()
            need(now < _instant(config['valid_until']) <= now + 7 * 86400,
                'SIM配置须在未来七天内到期，未安装过期或无限期计划')
            record = {'device_id': body['device_id'], 'version': version + 1, 'operation': 'install',
                'configuration_version': version + 1, 'configuration': config, 'configuration_hash': _digest(config),
                'previous_hash': previous_hash, 'request_key': key, 'body_sha256': _sha(raw),
                'created_at': _utc(now), 'in_flight_tick_keys': []}
            runtime = {'configuration_version': version + 1, 'status': 'installed',
                'next_due': max(now, _instant(config['valid_from'])), 'cycles_started': 0,
                'first_action_verified': False, 'reason': '配置已保存；尚待实际模拟动作完成回执。'}
            with self.devices.transaction() as db:
                self._inventory(db); result = self._write_command(db, key, raw, record)
                self._slot(db, body['device_id'], version + 1, runtime); self._inventory(db)
                return result

    def stop(self, key, raw):
        need(type(key) is str and KEY.fullmatch(key), status=422); body = parse_wire(raw)
        need(body['schema'] == STOP, status=422)
        with self.locked(), self.devices.transaction() as db:
            commands, slots, ticks = self._inventory(db)
            if key in commands:
                need(commands[key]['raw'] == raw.decode('utf-8')); return json.loads(commands[key]['result'])
            old = slots.get(body['device_id']); need(old and body['expected_version'] == old['version'])
            inflight = [tick['request_key'] for tick in ticks.values() if tick['device_id'] == body['device_id'] and tick['status'] in _PENDING]
            record = {**copy.deepcopy(old['record']), 'version': old['version'] + 1, 'operation': 'stop',
                'previous_hash': _digest(old['record']), 'request_key': key, 'body_sha256': _sha(raw),
                'created_at': _utc(self._now()), 'in_flight_tick_keys': inflight}
            result = self._write_command(db, key, raw, record)
            runtime = {**old['runtime'], 'status': 'stopped', 'next_due': None,
                'reason': '仅停止后续计划；已有飞行或云台动作仍由原回执管理，不自动返航。'}
            self._slot(db, body['device_id'], record['version'], runtime); self._inventory(db)
            return result

    @staticmethod
    def _receipt(tick, receipt):
        need(type(receipt) is dict and receipt.get('request_id') == tick['request_key']
            and receipt.get('device_id') == tick['device_id'] and receipt.get('action') == tick['command']['action']
            and receipt.get('status') in {'accepted', 'running', 'succeeded', 'failed', 'cancelled'}
            and all(type(receipt.get(key)) is type(value) and receipt[key] == value for key, value in MARK.items()))

    def _save_receipt(self, key, receipt=None, *, status=None, reason=None):
        with self.devices.transaction() as db:
            _, slots, ticks = self._inventory(db); tick = ticks[key]
            if receipt is not None: self._receipt(tick, receipt)
            tick = {**tick, 'status': receipt['status'] if receipt is not None else status,
                'receipt': copy.deepcopy(receipt), 'reason': reason}
            self._tick(db, tick); slot = slots[tick['device_id']]; runtime = slot['runtime']
            if runtime['configuration_version'] == tick['configuration_version']:
                runtime = {**runtime, 'first_action_verified': runtime['first_action_verified'] or tick['status'] == 'succeeded'}
                if runtime['status'] not in {'stopped', 'expired'}:
                    if tick['status'] == 'succeeded': runtime['status'] = 'completed' if runtime['next_due'] is None else 'active'
                    elif tick['status'] in {'accepted', 'running'}: runtime['status'] = 'waiting_receipt'
                    else: runtime.update(status='unknown' if tick['status'] == 'unknown' else 'blocked', reason=reason or '原设备动作未成功；没有重新派发。')
                self._slot(db, tick['device_id'], slot['version'], runtime)
            self._inventory(db); return copy.deepcopy(tick)

    def _poll(self, tick):
        try: receipt = self.devices.receipt(tick['request_key'])
        except HTTPException as error:
            if error.status_code != 404: raise
            return self._save_receipt(tick['request_key'], status='unknown', reason='原tick已保存但原设备回执未找到；不能据此重派。')
        return self._save_receipt(tick['request_key'], receipt)

    def consume_due(self):
        """Poll original receipts, then dispatch at most one due tick per device."""
        with self.locked():
            with self.devices.transaction() as db: _, _, ticks = self._inventory(db)
            for tick in ticks.values():
                if tick['status'] in _PENDING: self._poll(tick)
            with self.devices.transaction() as db: _, slots, ticks = self._inventory(db)
            for device_id in sorted(slots):
                slot = slots[device_id]; runtime, config = slot['runtime'], slot['record']['configuration']; now = self._now()
                if runtime['status'] in {'stopped', 'expired', 'blocked', 'unknown', 'completed'}: continue
                if now >= _instant(config['valid_until']):
                    with self.devices.transaction() as db:
                        self._slot(db, device_id, slot['version'], {**runtime, 'status': 'expired', 'next_due': None, 'reason': '原有效期已结束；不安排后续tick。'})
                    continue
                pending = any(t['device_id'] == device_id and t['status'] in _PENDING for t in ticks.values())
                if pending or runtime['next_due'] is None or now < runtime['next_due']: continue
                duration = config['duration_seconds'] if config['kind'] == 'drone' else 1
                if now + duration > _instant(config['valid_until']):
                    with self.devices.transaction() as db:
                        self._slot(db, device_id, slot['version'], {**runtime, 'status': 'expired', 'next_due': None,
                            'reason': '剩余有效窗口不足以完成已声明模拟动作；未安排新tick。'})
                    continue
                device = self.devices.device(device_id)
                ordinal = runtime['cycles_started'] + 1
                key = 'monitor200:' + _digest({'device_id': device_id, 'version': runtime['configuration_version'],
                    'ordinal': ordinal, 'install_request_key': slot['record']['request_key']})
                command = Command(device_id=device_id, action='start_mission' if config['kind'] == 'drone' else 'set_ptz',
                    expected_revision=device['revision'], arguments=self._arguments(config)).model_dump()
                tick = {'schema': TICK, **_FLAGS, 'request_key': key, 'device_id': device_id,
                    'configuration_version': runtime['configuration_version'], 'configuration_hash': slot['record']['configuration_hash'],
                    'ordinal': ordinal, 'scheduled_for': runtime['next_due'], 'prepared_at': _utc(now),
                    'command': command, 'input_hash': _request_hash(command), 'status': 'prepared', 'receipt': None, 'reason': None}
                maximum = config['max_cycles'] if config['kind'] == 'drone' else 1
                need(ordinal <= maximum)
                updated = {**runtime, 'cycles_started': ordinal, 'status': 'waiting_receipt',
                    'next_due': now + config['interval_seconds'] if config['kind'] == 'drone' and ordinal < maximum else None}
                with self.devices.transaction() as db:
                    self._inventory(db)
                    db.execute('INSERT INTO monitoring_ticks_v200 VALUES (?,?,?,?,?,?)',
                        (key, device_id, runtime['configuration_version'], ordinal, _json(tick), _digest(tick)))
                    self._slot(db, device_id, slot['version'], updated); self._inventory(db)
                # No SQLite transaction is held across the actual old ledger.
                try: receipt = self.devices.invoke(key, Command.model_validate(command))
                except HTTPException as error:
                    self._save_receipt(key, status='blocked', reason='原设备安全边界拒绝：' + str(error.detail)); continue
                except Exception:
                    self._save_receipt(key, status='unknown', reason='原设备动作回应未知；只核对原号，不重复调用。'); continue
                self._save_receipt(key, receipt)
            with self.devices.transaction() as db:
                _, slots, ticks = self._inventory(db)
                return {'schema': 'device-monitoring-consume-v200', **_FLAGS,
                    'plans': [self._view(slot, ticks) for _, slot in sorted(slots.items())]}

    @staticmethod
    def _view(slot, ticks):
        return {'schema': VIEW, **_FLAGS, 'device_id': slot['record']['device_id'], 'version': slot['version'],
            'configuration': copy.deepcopy(slot['record']['configuration']),
            'configuration_hash': slot['record']['configuration_hash'], **copy.deepcopy(slot['runtime']),
            'ticks': [copy.deepcopy(tick) for tick in ticks.values() if tick['device_id'] == slot['record']['device_id']]}

    def current(self, device_id):
        with self.locked(), self.devices.transaction() as db:
            _, slots, ticks = self._inventory(db); need(device_id in slots, '尚无该设备的监测配置', 404)
            return self._view(slots[device_id], ticks)

    def original(self, key, expected_sha256):
        need(type(key) is str and KEY.fullmatch(key) and type(expected_sha256) is str and _HASH.fullmatch(expected_sha256), status=422)
        with self.locked(), self.devices.transaction() as db:
            commands, _, _ = self._inventory(db); need(key in commands, '原监测命令未找到；不能当作已安装或自动换号', 404)
            need(commands[key]['raw_sha256'] == expected_sha256, '原编号不能替换正文')
            return copy.deepcopy(json.loads(commands[key]['result']))


def create_app(state_path: Path, token: str, *, clock=time.time, auto_consume=True, poll_seconds=0.25):
    from .device_simulator_v175 import create_app as original_app
    need(type(auto_consume) is bool and type(poll_seconds) in (int, float)
        and math.isfinite(poll_seconds) and 0.1 <= poll_seconds <= 5, status=422)
    need(type(token) is str and len(token) >= 24, '需提供至少24字符的独立SIM Bearer密钥', 422)
    ledger = MonitoringLedger(state_path, clock)
    app = original_app(state_path, token, clock=clock)
    app.state.monitoring_ledger = ledger; app.state.monitoring_error = None
    @asynccontextmanager
    async def lifespan(_app):
        async def timer():
            while True:
                await asyncio.sleep(poll_seconds)
                try:
                    await run_in_threadpool(ledger.consume_due); app.state.monitoring_error = None
                except Exception: app.state.monitoring_error = 'monitoring_tick_blocked'
        task = asyncio.create_task(timer()) if auto_consume else None
        try: yield
        finally:
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
    app.router.lifespan_context = lifespan
    def authorize(value):
        need(type(value) is str and hmac.compare_digest(value.encode(), ('Bearer ' + token).encode()), '模拟器认证失败', 401)
    @app.post('/monitoring/install')
    async def install_route(request: Request, authorization: str | None = Header(default=None),
            key: str | None = Header(default=None, alias='Idempotency-Key')):
        authorize(authorization); return await run_in_threadpool(ledger.install, key, await request.body())
    @app.post('/monitoring/stop')
    async def stop_route(request: Request, authorization: str | None = Header(default=None),
            key: str | None = Header(default=None, alias='Idempotency-Key')):
        authorize(authorization); return await run_in_threadpool(ledger.stop, key, await request.body())
    @app.get('/monitoring/requests/{request_key}')
    def original_route(request_key: str, expected_sha256: str, authorization: str | None = Header(default=None)):
        authorize(authorization); return ledger.original(request_key, expected_sha256)
    @app.get('/monitoring/devices/{device_id}')
    def current_route(device_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization); return ledger.current(device_id)
    @app.post('/monitoring/consume')
    async def consume_route(request: Request, authorization: str | None = Header(default=None)):
        authorize(authorization); need(await request.body() in {b'', b'{}'}, status=422)
        return await run_in_threadpool(ledger.consume_due)
    @app.get('/monitoring/health')
    def health_route(authorization: str | None = Header(default=None)):
        authorize(authorization); return {**_FLAGS, 'status': 'blocked' if app.state.monitoring_error else 'ok',
            'problem': app.state.monitoring_error, 'lab_interval_min_seconds': 5}
    return app


def main():
    parser = argparse.ArgumentParser(description='仅127.0.0.1的SIM监测计划；不控制真实硬件')
    parser.add_argument('--state', required=True, type=Path); parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535: parser.error('端口需在1024至65535之间')
    token = os.environ.get('GA_MONITORING_SIMULATOR_TOKEN', '')
    app = create_app(args.state, token)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)


if __name__ == '__main__': main()
