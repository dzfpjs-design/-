"""Isolated UAV mission laboratory. Never authorizes or controls real aircraft.

The straight-line route is deliberately only a simulation, not an aviation
flight plan.  All flight actions are sent exclusively to DeviceLedger, which
itself has no physical control path.  The caller must authenticate the manager
and enforce workspace permissions before using this internal service.
"""
import hashlib
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from .device_simulator_v175 import Command, DeviceLedger


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _check(condition, message, status=409):
    if not condition:
        raise HTTPException(status, message)


def _point(value):
    _check(isinstance(value, (list, tuple)) and len(value) == 2, '经纬度格式无效', 422)
    _check(all(type(v) in (int, float) and math.isfinite(v) for v in value), '经纬度必须是有效数字', 422)
    lon, lat = value
    _check(-180 <= lon <= 180 and -90 <= lat <= 90, '经纬度超出范围', 422)
    return [lon, lat]


class SimulatedMissionService:
    """Durable isolated mission ledger, with manager approval and receipt recovery."""

    def __init__(self, path: Path, device_ledger: DeviceLedger, *, clock=time.time):
        # Avoid treating a duck-typed hardware adapter as the laboratory.
        if type(device_ledger) is not DeviceLedger:
            raise TypeError('仅支持内置设备模拟器，真实无人机尚未接入')
        self.devices, self.clock, self.path = device_ledger, clock, Path(path)
        if self.path.is_symlink():
            raise ValueError('任务台账不能为符号链接')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY, workspace TEXT NOT NULL, body TEXT NOT NULL)')

    @contextmanager
    def _tx(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _load(db, workspace, mission_id):
        row = db.execute('SELECT body FROM missions WHERE id=? AND workspace=?', (mission_id, workspace)).fetchone()
        _check(row is not None, '任务不存在', 404)
        return json.loads(row[0])

    @staticmethod
    def _save(db, m):
        # Distinguish even same-status intervening writes (for example a
        # failed refresh) from the exact dispatch snapshot being completed.
        m['version'] = m.get('version', 0) + 1
        db.execute('UPDATE missions SET body=? WHERE id=? AND workspace=?', (_encoded(m), m['id'], m['workspace']))

    def prepare(self, workspace, event_id, start, destination, *, speed_m_s=10, observation_seconds=60, request_id=None):
        _check(isinstance(workspace, str) and 1 <= len(workspace) <= 128 and isinstance(event_id, str) and 1 <= len(event_id) <= 128, '工作区或事件无效', 422)
        a, b = _point(start), _point(destination)
        _check(type(speed_m_s) in (int, float) and math.isfinite(speed_m_s) and 1 <= speed_m_s <= 15, '模拟速度须在 1 至 15 米/秒之间', 422)
        _check(type(observation_seconds) is int and 1 <= observation_seconds <= 600, '模拟观察时长无效', 422)
        if request_id is not None:
            try:
                _check(str(uuid.UUID(request_id)) == request_id, '请求标识无效', 422)
            except (ValueError, TypeError, AttributeError):
                raise HTTPException(422, '请求标识无效') from None
        request_id = request_id or str(uuid.uuid4())
        spec = {'event_id': event_id, 'start': a, 'destination': b,
                'speed_m_s': speed_m_s, 'observation_seconds': observation_seconds}
        with self._tx() as db:
            existing = db.execute('SELECT workspace,body FROM missions WHERE id=?', (request_id,)).fetchone()
            if existing:
                _check(existing[0] == workspace, '请求标识不可用')
                prior = json.loads(existing[1])
                _check(prior['request_spec'] == spec, '请求标识已绑定其他参数，请核对原任务')
                return prior
        lat1, lat2 = math.radians(a[1]), math.radians(b[1])
        dlat, dlon = lat2 - lat1, math.radians(b[0] - a[0])
        h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        distance = 2*6371000*math.asin(math.sqrt(min(1, max(0, h))))
        duration = math.ceil(distance*2/speed_m_s + observation_seconds)
        device = self.devices.device('sim-dock-01')
        state = device['state']
        reasons = []
        if not state['online'] or state['fault'] or state['phase'] != 'idle': reasons.append('模拟无人机不在线、存在故障或正在执行任务')
        if state['wind_m_s'] > 10 or not -20 <= state['temperature_c'] <= 50: reasons.append('模拟风速或温度不满足起飞条件')
        if duration > 3240: reasons.append('往返与观察时间超出模拟续航')
        if state['battery_percent'] - duration / 3240 * 80 < 25: reasons.append('预计返航余量不足')
        plan = {'start': a, 'destination': b, 'distance_m': round(distance), 'duration_seconds': duration,
                'speed_m_s': speed_m_s, 'observation_seconds': observation_seconds,
                'device_revision': device['revision'], 'battery_percent': state['battery_percent'],
                'wind_m_s': state['wind_m_s'], 'temperature_c': state['temperature_c'],
                'limitations': '模拟直线路径未核验空域、地形、障碍物或真实天气，不能用于实际飞行'}
        m = {'id': request_id, 'workspace': workspace, 'event_id': event_id, 'request_spec': spec, 'device_id': 'sim-dock-01',
             'version': 1,
             'simulated': True, 'plan': plan, 'plan_hash': hashlib.sha256(_encoded(plan).encode()).hexdigest(),
             'status': 'blocked' if reasons else 'awaiting_approval', 'reasons': reasons,
             'created_at': self.clock(), 'approval': None, 'alerts': [], 'receipt': None}
        with self._tx() as db:
            existing = db.execute('SELECT workspace,body FROM missions WHERE id=?', (request_id,)).fetchone()
            if existing:
                _check(existing[0] == workspace, '请求标识不可用')
                prior = json.loads(existing[1])
                _check(prior['request_spec'] == spec, '请求标识已绑定其他参数，请核对原任务')
                return prior
            db.execute('INSERT INTO missions VALUES (?,?,?)', (m['id'], workspace, _encoded(m)))
        return m

    def list(self, workspace):
        with self._tx() as db:
            rows = db.execute('SELECT body FROM missions WHERE workspace=? ORDER BY rowid DESC LIMIT 100', (workspace,)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def get(self, workspace, mission_id):
        with self._tx() as db:
            return self._load(db, workspace, mission_id)

    def approve_and_start(self, workspace, mission_id, *, manager, plan_hash):
        _check(isinstance(manager, str) and 1 <= len(manager.strip()) <= 128, '需要管理人员确认', 422)
        # Holding a durable dispatch intent before crossing the boundary means a
        # crash cannot cause a later caller to re-issue an uncertain command.
        with self._tx() as db:
            m = self._load(db, workspace, mission_id)
            _check(m['status'] == 'awaiting_approval', '任务不可再次派发，请查看原回执')
            _check(plan_hash == m['plan_hash'], '路线已变化，请重新确认')
            current = self.devices.device(m['device_id'])
            _check(current['revision'] == m['plan']['device_revision'], '设备状态已变化，请重新规划并确认')
            m.update(status='dispatch_unknown', approval={'manager': manager, 'at': self.clock(), 'plan_hash': plan_hash},
                     request_id='sim-uav-' + mission_id)
            self._save(db, m)
            dispatch_version, dispatch_request = m['version'], m['request_id']
        try:
            receipt = self.devices.invoke(m['request_id'], Command(device_id=m['device_id'], action='start_mission',
                expected_revision=m['plan']['device_revision'], arguments={'duration_seconds': m['plan']['duration_seconds'],
                'speed_m_s': m['plan']['speed_m_s'], 'mission_name': '模拟事件核查 ' + mission_id}))
        except Exception:
            return self.get(workspace, mission_id)
        with self._tx() as db:
            m = self._load(db, workspace, mission_id)
            if (m.get('version') == dispatch_version and m['status'] == 'dispatch_unknown'
                    and m.get('request_id') == dispatch_request and not m.get('return_request_id')
                    and (m.get('approval') or {}).get('plan_hash') == plan_hash):
                m.update(status='flying' if receipt['status'] in ('accepted', 'running') else 'dispatch_unknown', receipt=receipt)
                self._save(db, m)
        return m

    def refresh(self, workspace, mission_id):
        with self._tx() as db:
            m = self._load(db, workspace, mission_id)
            if not m.get('request_id'):
                return m
            if m['status'] in ('completed', 'returned'):
                return m
            try:
                device = self.devices.device(m['device_id'])
                receipt = self.devices.receipt(m.get('return_request_id', m['request_id']))
            except Exception:
                m['status'] = 'return_unknown' if m.get('return_request_id') else 'dispatch_unknown'
                self._save(db, m)
                return m
            m['receipt'] = receipt
            state = device['state']
            if not state['online']:
                m['status'] = 'connection_lost'
                alert = '模拟无人机失联：位置和处置结果未知，请立即升级并安排人员核查'
            elif state['fault']:
                m['status'] = 'needs_recovery'
                alert = '模拟无人机故障：请查看原设备回执并处理'
            elif receipt['status'] in ('failed', 'cancelled'):
                m['status'] = 'needs_recovery'
                alert = '模拟任务未正常完成，请查看原回执'
            elif receipt['status'] == 'succeeded':
                m['status'] = 'returned' if m.get('return_request_id') else 'completed'
                alert = None
            elif state['battery_percent'] <= 25:
                m['status'] = 'returning' if m.get('return_request_id') else 'low_battery'
                alert = '模拟无人机电量不足：建议立即返航并安排人员核查'
            else:
                m['status'] = 'returning' if m.get('return_request_id') else 'flying'
                alert = None
            if alert and alert not in m['alerts']:
                m['alerts'].append(alert)
            m['last_device'] = device
            self._save(db, m)
            return m

    def confirm_return(self, workspace, mission_id, *, manager):
        _check(isinstance(manager, str) and 1 <= len(manager.strip()) <= 128, '需要管理人员确认', 422)
        with self._tx() as db:
            m = self._load(db, workspace, mission_id)
            _check(m['status'] in ('flying', 'low_battery'), '当前状态不可发起返航，请先检查设备')
            device = self.devices.device(m['device_id'])
            _check(device['state']['online'] and not device['state']['fault'], '设备离线或故障，返航结果未知')
            m.update(status='return_unknown', return_request_id='sim-uav-return-' + mission_id,
                     return_approval={'manager': manager, 'at': self.clock()})
            self._save(db, m)
        try:
            self.devices.invoke(m['return_request_id'], Command(device_id=m['device_id'], action='return_to_dock',
                expected_revision=device['revision'], arguments={}))
        except Exception:
            return self.get(workspace, mission_id)
        return self.refresh(workspace, mission_id)
