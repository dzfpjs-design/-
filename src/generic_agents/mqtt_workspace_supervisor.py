"""Run the existing read-only MQTT supervisor for one explicit workspace.

The legacy supervisor scans every active workspace. A workspace worker must
never silently activate other workspaces' sources, so its scan is scoped here.
"""
from threading import Thread
from uuid import UUID

from sqlalchemy import select

from .models import MqttGateway
from .mqtt_subscriber import LeaseBusy
from .mqtt_supervisor import GatewayListener, MqttSupervisor, SourceUnavailable, _source


class WorkspaceMqttSupervisor(MqttSupervisor):
    def __init__(self, workspace_id, **kwargs):
        try:
            if not isinstance(workspace_id, str) or str(UUID(workspace_id)) != workspace_id:
                raise ValueError()
        except (ValueError, AttributeError, TypeError):
            raise ValueError('必须提供有效的工作区标识') from None
        self.workspace_id = workspace_id
        super().__init__(**kwargs)

    def reconcile(self):
        for identity, listener in list(self.listeners.items()):
            if listener.done.is_set():
                listener.thread.join(timeout=0)
                del self.listeners[identity]
        with self.db_factory() as db:
            identities = list(db.scalars(select(MqttGateway.id).where(
                MqttGateway.workspace_id == self.workspace_id,
                MqttGateway.status == 'active').order_by(MqttGateway.created_at, MqttGateway.id)))
        active = set(identities)
        for identity, listener in self.listeners.items():
            if identity not in active:
                listener.stop.set()
        for identity in identities:
            if identity in self.listeners:
                continue
            try:
                with self.db_factory() as db:
                    source = _source(db, identity)
                if source.workspace_id != self.workspace_id:
                    continue
            except SourceUnavailable as error:
                self._blocked(identity, error.code)
                continue
            listener = GatewayListener(source, lease_seconds=self.lease_seconds,
                                       connect_timeout=self.connect_timeout, client_factory=self.client_factory)
            try:
                listener.reserve()
            except LeaseBusy:
                continue
            if len(self.listeners) >= self.max_receivers:
                try:
                    listener.capacity_wait()
                finally:
                    listener.release()
                continue
            try:
                self.listeners[identity] = listener
                listener.thread.start()
            except Exception:
                del self.listeners[identity]
                listener.release()
                raise
        return len(self.listeners)


def start_workspace_mqtt(workspace_id, stop_event):
    """Start a stoppable receiver supervisor; caller owns stop and join."""
    supervisor = WorkspaceMqttSupervisor(workspace_id)
    thread = Thread(target=supervisor.run, kwargs={'stop_requested': stop_event.is_set},
                    name='mqtt-workspace-' + workspace_id[:8], daemon=True)
    thread.start()
    return thread
