"""Lifecycle adapter: existing workspace setup worker -> UAV laboratory monitor.

The thread only attaches when both workspace-owned simulator ledgers already
exist. It only reads the simulator and persists task status via ``refresh``;
it does not issue device commands or publish a real-world alert.
"""
import threading
import time
from pathlib import Path
from uuid import UUID

from .config import settings
from .uav_simulated_monitor import SimulatedWorkspaceMonitor

INTERVAL_SECONDS = 10


def start_workspace_monitor(workspace_id, stop_event):
    if type(workspace_id) is not str or str(UUID(workspace_id)) != workspace_id:
        raise ValueError('工作区标识须为标准 UUID')

    def run():
        monitor = None
        last_diagnostic = float('-inf')

        def diagnostic(reason):
            nonlocal last_diagnostic
            if time.monotonic() - last_diagnostic >= 30:
                print('模拟无人机状态监测暂不可用：' + reason + '；主处理器继续运行', flush=True)
                last_diagnostic = time.monotonic()

        while not stop_event.is_set():
            try:
                storage_dir = Path(settings().storage_dir)
                root = storage_dir / 'uav-simulation' / workspace_id
                missions, device = root / 'missions.sqlite', root / 'device.sqlite'
                # No files or directories are made when a user has not created
                # this workspace's simulation through the normal API.
                present = all(not p.is_symlink() and p.is_file() for p in (missions, device))
                if present:
                    if monitor is None:
                        monitor = SimulatedWorkspaceMonitor(storage_dir, workspace_id)
                    result = monitor.tick(batch_size=32)
                    if result['errors']:
                        diagnostic('部分任务状态核对失败，共 ' + str(result['errors']) + ' 条')
                else:
                    monitor = None
                    if missions.exists() or device.exists():
                        diagnostic('模拟任务或设备台账缺失')
            except Exception as error:
                monitor = None
                diagnostic('错误类别 ' + type(error).__name__)
            if stop_event.wait(INTERVAL_SECONDS):
                break

    thread = threading.Thread(target=run, name='uav-simulation-monitor', daemon=True)
    thread.start()
    return thread
