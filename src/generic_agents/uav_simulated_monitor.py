"""Bounded, workspace-isolated background monitoring of *simulated* missions.

Nothing starts this module automatically. A trusted process may instantiate a
monitor for an explicitly configured workspace and schedule ``tick`` or call
``run_periodic``. It queries only the local laboratory ledger and never sends
device commands, dispatches a task, delivers notifications, or connects to a
physical aircraft. UI push and incident notifications require separate wiring.
"""
import json
import time
import uuid
from pathlib import Path

from .device_simulator_v175 import DeviceLedger
from .uav_simulated_missions import SimulatedMissionService


MONITORED = frozenset({'dispatch_unknown', 'flying', 'low_battery', 'connection_lost',
                       'return_unknown', 'returning', 'needs_recovery'})


class SimulatedWorkspaceMonitor:
    """One bounded scanner bound permanently to a server-selected workspace."""

    def __init__(self, storage_dir: Path, workspace_id: str, *, clock=time.time):
        try:
            if type(workspace_id) is not str or str(uuid.UUID(workspace_id)) != workspace_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('工作区标识须为标准 UUID') from None
        self.workspace_id, self.clock = workspace_id, clock
        root = Path(storage_dir) / 'uav-simulation' / workspace_id
        self.service = SimulatedMissionService(root / 'missions.sqlite',
            DeviceLedger(root / 'device.sqlite', clock=clock), clock=clock)
        with self.service._tx() as db:
            db.execute('CREATE INDEX IF NOT EXISTS uav_monitor_scan ON missions(workspace,id)')
            db.execute('CREATE TABLE IF NOT EXISTS uav_monitor_cursor '
                       '(id INTEGER PRIMARY KEY CHECK (id=1), cursor TEXT NOT NULL, '
                       'lease_token TEXT, lease_until REAL NOT NULL)')
            db.execute("INSERT OR IGNORE INTO uav_monitor_cursor VALUES (1,'',NULL,0)")

    def tick(self, *, batch_size=32):
        """Inspect at most ``batch_size`` rows; return counts, never mission data.

        The cursor is checkpointed before the read-only observations. A crash
        may delay a skipped item until the next full pass; no action is retried.
        An expiring process lease avoids simultaneous scans, and a token guards
        release after expiry. Each refresh is separately serialized by the
        mission ledger, including against a manager's approval/return action.
        """
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError('每次扫描数量须为 1 至 100')
        token = str(uuid.uuid4())
        with self.service._tx() as db:
            cursor, old_token, lease_until = db.execute(
                'SELECT cursor,lease_token,lease_until FROM uav_monitor_cursor WHERE id=1').fetchone()
            now = self.clock()
            if old_token and lease_until > now:
                return {'status': 'busy', 'scanned': 0, 'refreshed': 0, 'errors': 0}
            db.execute('UPDATE uav_monitor_cursor SET lease_token=?,lease_until=? WHERE id=1',
                       (token, now + 120))
            rows = db.execute('SELECT id,body FROM missions WHERE workspace=? AND id>? ORDER BY id LIMIT ?',
                              (self.workspace_id, cursor, batch_size)).fetchall()
            if not rows and cursor:
                rows = db.execute('SELECT id,body FROM missions WHERE workspace=? ORDER BY id LIMIT ?',
                                  (self.workspace_id, batch_size)).fetchall()
            next_cursor = rows[-1][0] if rows else ''
            db.execute('UPDATE uav_monitor_cursor SET cursor=? WHERE id=1', (next_cursor,))
        refreshed = errors = 0
        try:
            for mission_id, raw in rows:
                try:
                    # The snapshot merely selects candidates. refresh re-reads
                    # the authoritative task while holding its ledger lock.
                    if json.loads(raw).get('status') in MONITORED:
                        self.service.refresh(self.workspace_id, mission_id)
                        refreshed += 1
                except Exception:
                    errors += 1
        finally:
            with self.service._tx() as db:
                db.execute('UPDATE uav_monitor_cursor SET lease_token=NULL,lease_until=0 '
                           'WHERE id=1 AND lease_token=?', (token,))
        return {'status': 'ok', 'scanned': len(rows), 'refreshed': refreshed, 'errors': errors}

    def run_periodic(self, stop_event, *, interval_seconds=10, batch_size=32, on_result=None):
        """Run until explicitly stopped; callers manage process lifecycle/logging."""
        if type(interval_seconds) not in (int, float) or not 1 <= interval_seconds <= 60:
            raise ValueError('轮询间隔须为 1 至 60 秒')
        while not stop_event.is_set():
            try:
                result = self.tick(batch_size=batch_size)
            except Exception:
                # No device payload or credentials in callbacks/logging.
                result = {'status': 'error', 'scanned': 0, 'refreshed': 0, 'errors': 1}
            if on_result:
                on_result(result)
            if stop_event.wait(interval_seconds):
                break
