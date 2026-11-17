"""Workspace-local Cloud API 2 read-only SDK process supervisor.

Each configured connection receives a separate SDK JVM. No flight commands are
supported. Credential and device payloads are never included in logs.
"""
from __future__ import annotations

import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import engine
from .dji_cloud2_connections import KIND, acquire_snapshot, is_current, publish_state, publish_websocket_state
from .dji_cloud2_process import Bridge, BridgeError, BridgeRevoked, init_frame
from .models import Job
from .mqtt_subscriber import subscription_lease, LeaseBusy

def _command():
    """Fail closed until SDK network egress confinement has been proven.

    The inspected DJI SDK follows redirects with custom authentication headers.
    An initial host allowlist and certificate verification cannot constrain the
    redirected destination. No environment switch enables real connections.
    """
    return None


def _connections(workspace):
    with Session(engine()) as db:
        rows = db.scalars(select(Job).where(Job.workspace_id == workspace, Job.kind == KIND,
                           Job.status == 'internal').order_by(Job.id)).all()
        return [row.id for row in rows if row.data.get('enabled') is True]


def _snapshot(workspace, identity):
    with Session(engine(), expire_on_commit=False) as db:
        return acquire_snapshot(db, workspace, identity)


def _current(workspace, identity, version):
    with Session(engine()) as db:
        return is_current(db, workspace, identity, version)


def _report(lease, workspace, identity, version, state, count=None):
    # The session advisory lease and the publication must share one DB
    # connection. Otherwise loss of the lease can allow an old process to
    # publish after its replacement has started.
    with lease.session() as db:
        ok = publish_state(db, workspace, identity, version, state, device_count=count)
        db.commit()
        return ok


def _report_websocket(lease, workspace, identity, version, state):
    with lease.session() as db:
        current, last_failure = state
        ok = publish_websocket_state(db, workspace, identity, version, current, last_failure)
        db.commit()
        return ok


def _run_connection(workspace, identity, stop, *, command_factory=_command):
    delay = 1
    while not stop.is_set():
        try:
            with subscription_lease('dji-cloud2:' + workspace + ':' + identity) as lease:
                bridge = None
                version = None
                try:
                    snapshot = _snapshot(workspace, identity)
                    version = snapshot['version']
                    command = command_factory()
                    if not command:
                        _report(lease, workspace, identity, version, 'sdk_unavailable')
                        stop.wait(15)
                        continue
                    if not _report(lease, workspace, identity, version, 'connecting'):
                        return

                    def allowed():
                        lease.check()
                        lease.connection.commit()
                        return _current(workspace, identity, version)

                    bridge = Bridge(command, allowed, stop, timeout=15, interval=.5)
                    http = {item['name']: item['value'] for item in snapshot['http_headers']}
                    ws = {item['name']: item['value'] for item in snapshot['ws_headers']}
                    bridge.start(init_frame(snapshot['host'], snapshot['port'], http, ws))
                    del http, ws, snapshot
                    while not stop.is_set():
                        try:
                            devices = bridge.list_devices()
                        except BridgeError as error:
                            # Only a valid, complete HTTP error response leaves the
                            # protocol synchronized. On timeout/EOF/revocation,
                            # never issue a further request to the old process.
                            if type(error) is BridgeError and str(error) in ('authentication_failed', 'upstream_error'):
                                previous_timeout = bridge.timeout
                                try:
                                    bridge.timeout = min(previous_timeout, 1)
                                    status = bridge.websocket_status()
                                    _report_websocket(lease, workspace, identity, version, status)
                                except BridgeRevoked:
                                    raise
                                except BridgeError:
                                    pass
                                finally:
                                    bridge.timeout = previous_timeout
                            raise
                        if not _report(lease, workspace, identity, version, 'connected', len(devices)):
                            raise BridgeRevoked('revoked')
                        del devices
                        delay = 1
                        for second in range(30):
                            if second % 5 == 0:
                                ws_state = bridge.websocket_status()
                                if not _report_websocket(lease, workspace, identity, version, ws_state):
                                    raise BridgeRevoked('revoked')
                            if stop.wait(1): break
                            if not allowed(): raise BridgeRevoked('revoked')
                    # A normal worker shutdown is not evidence the socket remains
                    # live. Revocation is fenced again in the DB publication.
                    _report(lease, workspace, identity, version, 'disconnected')
                    return
                except BridgeRevoked:
                    if version is not None:
                        try: _report(lease, workspace, identity, version, 'disconnected')
                        except Exception: pass
                    return
                except Exception as error:
                    if version is not None:
                        try:
                            state = ('authentication_failed' if isinstance(error, BridgeError) and str(error) == 'authentication_failed' else
                                     'sdk_unavailable' if isinstance(error, BridgeError) and str(error) == 'sdk_unsupported' else 'disconnected')
                            _report(lease, workspace, identity, version, state)
                        except Exception:
                            pass
                finally:
                    if bridge is not None:
                        bridge.close()
        except LeaseBusy:
            stop.wait(5)
            continue
        except Exception:
            # No publication is safe after losing or failing to acquire lease.
            pass
        if stop.wait(delay): return
        delay = min(delay * 2, 30)


class _CombinedStop:
    def __init__(self, outer, inner): self.outer, self.inner = outer, inner
    def is_set(self): return self.outer.is_set() or self.inner.is_set()
    def wait(self, timeout):
        # Bound shutdown response without relying on either event notifying the other.
        remaining = timeout
        while remaining > 0 and not self.is_set():
            interval = min(.25, remaining)
            self.inner.wait(interval)
            remaining -= interval
        return self.is_set()


def start_workspace_cloud2(workspace, stop):
    def supervise():
        children = {}
        try:
            while not stop.is_set():
                try:
                    identities = set(_connections(workspace))
                    # Never silently exceed the configured per-workspace limit.
                    if len(identities) > 8:
                        identities = set()
                except Exception:
                    # Stop all SDK sessions when the system of record is unavailable.
                    identities = set()
                for identity, (thread, local_stop) in list(children.items()):
                    if identity not in identities or not thread.is_alive():
                        local_stop.set()
                        thread.join(timeout=3)
                        if not thread.is_alive(): del children[identity]
                for identity in identities - children.keys():
                    local_stop = threading.Event()
                    thread = threading.Thread(target=_run_connection,
                        args=(workspace, identity, _CombinedStop(stop, local_stop)), daemon=True)
                    children[identity] = (thread, local_stop)
                    thread.start()
                stop.wait(2)
        finally:
            for _, local_stop in children.values(): local_stop.set()
            for thread, _ in children.values(): thread.join(timeout=3)

    thread = threading.Thread(target=supervise, name='dji-cloud2-workspace', daemon=True)
    thread.start()
    return thread
