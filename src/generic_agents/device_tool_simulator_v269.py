"""V200 equipment Node plus opt-in V201 receipts on the existing listener."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import time

from .device_monitoring_simulator_v200 import create_app as monitoring_app
from .device_registered_tool_adapter_v269 import register_routes
from .device_simulator_v175 import DeviceLedger


def create_app(state_path: Path, token: str, *, clock=time.time,
               auto_consume=True, poll_seconds=0.25):
    app = monitoring_app(state_path, token, clock=clock,
                         auto_consume=auto_consume, poll_seconds=poll_seconds)
    # V175 keeps its ledger in a closure. This handle uses that SAME SQLite
    # file, with normal old ledger methods, rather than another device state.
    register_routes(app, DeviceLedger(state_path, clock), token)
    return app


def main():
    parser = argparse.ArgumentParser(description='同一外部SIM设备Node的原号工具回执；不控制真实硬件')
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--no-auto-consume', action='store_true')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('端口需在1024至65535之间')
    token = os.environ.get('GA_MONITORING_SIMULATOR_TOKEN', '')
    app = create_app(args.state, token, auto_consume=not args.no_auto_consume)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
