"""Durable read-only intake for explicitly activated, server-approved gateways.

Each gateway owns one PostgreSQL session lock shared with the bounded CLI. The
lock-holding connection also commits receipts, so losing it cannot acknowledge
an uncommitted packet or silently reconnect under the old fencing token.
"""
from dataclasses import dataclass
from datetime import timedelta
from threading import Event, Thread
import signal
import os
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .contracts import digest
from .db import engine
from .models import Membership, MqttGateway, MqttReceiverState, User, now
from .mqtt_inbox import broker_address, validate_configuration
from .mqtt_subscriber import (AcknowledgementFailed, CredentialsUnavailable, LeaseBusy, LeaseLost,
                              capture_and_ack, mqtt_client, subscription_lease)
from .security import Principal


LIVE_STATES = {'connecting', 'subscribing', 'receiving', 'backoff'}


def receiver_client_id(gateway_id, version, config_hash):
    # Stable across process/network recovery for one authorized configuration.
    # A different generation cannot inherit old unacknowledged broker inflight.
    return f'ga-read-v2-{gateway_id}-v{version}-{config_hash[:12]}'


class SourceUnavailable(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Source:
    gateway_id: str
    workspace_id: str
    version: int
    config_hash: str
    principal: Principal
    config: dict


def _source(db, gateway_id, expected=None):
    if expected is not None:
        from .managed_mqtt_grants import lock_workspace
        lock_workspace(db,expected.workspace_id,shared=True)
    gateway = db.scalar(select(MqttGateway).where(MqttGateway.id == gateway_id)
                        .with_for_update().execution_options(populate_existing=True))
    if not gateway:
        raise SourceUnavailable('gateway_missing')
    if gateway.status != 'active':
        raise SourceUnavailable('gateway_paused')
    if expected and (gateway.version != expected.version or digest(gateway.data) != expected.config_hash
                     or gateway.owner_id != expected.principal.user_id):
        raise SourceUnavailable('configuration_changed')
    # Read current authorization on every guard, including within packet commits.
    # Hold authorization rows through the receipt COMMIT. A concurrent revoke
    # must commit after an already-authorized packet, never in its final-guard
    # to-COMMIT gap. Lock order is gateway -> user -> membership -> receiver.
    user_active = db.scalar(select(User.active).where(User.id == gateway.owner_id).with_for_update(read=True))
    role = db.scalar(select(Membership.role).where(Membership.user_id == gateway.owner_id,
                                                 Membership.workspace_id == gateway.workspace_id)
                     .with_for_update(read=True))
    if not user_active:
        raise SourceUnavailable('owner_unavailable')
    if role not in {'admin', 'operator'}:
        raise SourceUnavailable('owner_permission_removed')
    try:
        configuration = validate_configuration(gateway.data,workspace_id=gateway.workspace_id,db=db)
    except HTTPException as error:
        raise SourceUnavailable('broker_not_allowed' if error.status_code == 403 else 'configuration_invalid') from None
    from .managed_mqtt_grants import ref_id
    if configuration['username_ref'] and not ref_id(configuration['username_ref'],configuration['password_ref']) and (not os.environ.get(configuration['username_ref'])
                                          or not os.environ.get(configuration['password_ref'])):
        raise SourceUnavailable('credentials_missing')
    principal = Principal(gateway.owner_id, gateway.workspace_id, role, 'mqtt-receiver')
    return Source(gateway.id, gateway.workspace_id, gateway.version, digest(configuration), principal, configuration)


def _state(db, gateway_id):
    return db.scalar(select(MqttReceiverState).where(MqttReceiverState.gateway_id == gateway_id)
                     .with_for_update().execution_options(populate_existing=True))


def _write(state, status, error_code=None, *, token=None, lease_seconds=15, **events):
    timestamp = now()
    old = dict(state.data or {})
    history = list(old.get('history', []))[-20:]
    if (state.status != status or old.get('error_code') != error_code or not history
            or history[-1].get('gateway_version') != state.gateway_version):
        history.append({'at': timestamp.isoformat(), 'state': status, 'error_code': error_code,
                        'gateway_version': state.gateway_version})
        old['status_changed_at'] = timestamp.isoformat()
    state.status = status
    state.heartbeat_at = timestamp
    state.lease_token = token if status in LIVE_STATES else None
    state.lease_until = timestamp + timedelta(seconds=lease_seconds) if status in LIVE_STATES else None
    state.data = {**old, 'schema': 'mqtt-receiver-v1', 'error_code': error_code,
                  'history': history[-20:], **events}
    state.version = (state.version or 0) + 1


class GatewayListener:
    """One continuously connected, independently recoverable gateway session."""
    def __init__(self, source, *, lease_seconds=15, connect_timeout=3, client_factory=None):
        self.source = source
        self.lease_seconds = lease_seconds
        self.connect_timeout = connect_timeout
        # The bounded CLI predates this journal. Configuration generations also
        # need separate sessions: UNSUBACK cannot clear old unacknowledged inflight.
        self.client_factory = client_factory
        self.stop = Event()
        self.done = Event()
        self.token = str(uuid4())
        self.lease = None
        self._lease_context = None
        self.client = None
        self.failure = None
        self.subscribed = False
        self.attempt = 0
        self.unsubscribe_mid = None
        self.subscribe_mid = None
        self.pending_messages = []
        self.thread = Thread(target=self.run, name='mqtt-receiver-' + source.gateway_id, daemon=True)

    def _guard(self, db):
        self.lease.check(db)
        _source(db, self.source.gateway_id, self.source)
        state = _state(db, self.source.gateway_id)
        if (not state or state.lease_token != self.token or state.gateway_version != self.source.version
                or state.config_hash != self.source.config_hash or not state.lease_until or state.lease_until <= now()):
            raise SourceUnavailable('receiver_fenced')
        return state

    def _record(self, status, error_code=None, *, terminal=False, **events):
        with self.lease.session() as db:
            if terminal:
                # An old listener may close its own generation after pause/revoke,
                # but can never overwrite a newer leader's token or generation.
                self.lease.check(db)
                db.scalar(select(MqttGateway).where(MqttGateway.id == self.source.gateway_id).with_for_update())
                state = _state(db, self.source.gateway_id)
                if not state or state.lease_token != self.token:
                    return
            else:
                state = self._guard(db)
            _write(state, status, error_code, token=self.token, lease_seconds=self.lease_seconds, **events)
            db.commit()

    def _claim(self):
        with self.lease.session() as db:
            self.lease.check(db)
            _source(db, self.source.gateway_id, self.source)
            state = _state(db, self.source.gateway_id)
            if not state:
                state = MqttReceiverState(gateway_id=self.source.gateway_id, workspace_id=self.source.workspace_id,
                                          gateway_version=self.source.version, config_hash=self.source.config_hash,
                                          status='standby', data={})
                db.add(state)
            state.gateway_version = self.source.version
            state.config_hash = self.source.config_hash
            _write(state, 'connecting', token=self.token, lease_seconds=self.lease_seconds,
                   attempt=0, next_retry_at=None)
            db.commit()

    def reserve(self):
        # Reconcile reserves before allocating a thread, so busy gateways do not
        # consume this process's capacity or starve later independent sources.
        self._lease_context = subscription_lease(self.source.gateway_id)
        self.lease = self._lease_context.__enter__()

    def release(self):
        if self.lease is not None and self._lease_context is not None:
            self._lease_context.__exit__(None, None, None)
            self._lease_context = None

    def capacity_wait(self):
        with self.lease.session() as db:
            self.lease.check(db)
            _source(db, self.source.gateway_id, self.source)
            state = _state(db, self.source.gateway_id)
            if not state:
                state = MqttReceiverState(gateway_id=self.source.gateway_id, workspace_id=self.source.workspace_id,
                                          gateway_version=self.source.version, config_hash=self.source.config_hash,
                                          status='standby', data={})
                db.add(state)
            state.gateway_version = self.source.version
            state.config_hash = self.source.config_hash
            _write(state, 'standby', 'capacity_wait', next_retry_at=None)
            db.commit()

    def _saved(self, db, identifier):
        state = self._guard(db)
        _write(state, 'receiving', token=self.token, lease_seconds=self.lease_seconds,
               last_received_at=now().isoformat(), received_count=int(state.data.get('received_count', 0)) + 1)

    def _connect_callback(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.failure = 'broker_rejected'
            client.disconnect()
            return
        with self.lease.session() as db:
            state = self._guard(db)
            previous = set(state.data.get('subscribed_topics', []))
            current = set(self.source.config['topics'])
            union = previous | current
            # After several interrupted configuration handovers, clear the
            # already journalled topics first; the new intent is committed after
            # UNSUBACK and still before SUBSCRIBE. Never truncate a live journal.
            intent = previous if len(union) > 64 else union
            _write(state, 'subscribing', token=self.token, lease_seconds=self.lease_seconds,
                   connected_at=now().isoformat(), next_retry_at=None, subscribed_topics=sorted(intent))
            db.commit()  # Write intent before touching the persistent broker session.
        self.unsubscribe_mid = None
        if previous:
            result, self.unsubscribe_mid = client.unsubscribe(sorted(previous))
            if result != 0:
                self.failure = 'subscription_rejected'
                client.disconnect()
        else:
            self._subscribe(client)

    def _subscribe(self, client):
        result, self.subscribe_mid = client.subscribe([(topic, 1) for topic in self.source.config['topics']])
        if result != 0:
            self.failure = 'subscription_rejected'
            client.disconnect()

    def _unsubscribe_callback(self, client, userdata, mid, reason_codes, properties):
        if mid != self.unsubscribe_mid:
            return
        if reason_codes and any(code.is_failure for code in reason_codes):
            self.failure = 'subscription_rejected'
            client.disconnect()
            return
        self._record('subscribing', subscribed_topics=sorted(self.source.config['topics']))
        self.unsubscribe_mid = None
        self._subscribe(client)

    def _subscribe_callback(self, client, userdata, mid, reason_codes, properties):
        if mid != self.subscribe_mid:
            return
        if len(reason_codes) != len(self.source.config['topics']) or any(code.is_failure or code.value != 1 for code in reason_codes):
            self.failure = 'subscription_rejected'
            client.disconnect()
            return
        self.subscribed = True
        self._record('receiving', subscribed_at=now().isoformat(), next_retry_at=None)
        pending, self.pending_messages = self.pending_messages, []
        for message in pending:
            if self.failure:
                break
            self._message_callback(client, userdata, message)

    def _message_callback(self, client, userdata, message):
        if self.stop.is_set() or self.failure:
            client.disconnect()
            return
        if message.topic not in self.source.config['topics']:
            # Unexpected/removed input is never saved or acknowledged. Generation
            # isolation prevents old inflight from occupying this session's window.
            return
        if not self.subscribed:
            # Persistent sessions can replay QoS1 input before our SUBACK. Keep a
            # bounded transient queue, then commit/ack only after confirmation.
            if len(self.pending_messages) >= 32 or len(message.payload) > 65536:
                self.failure = 'subscription_rejected'
                client.disconnect()
            else:
                self.pending_messages.append(message)
            return
        try:
            capture_and_ack(self.source.principal, self.source.gateway_id, self.source.version, message,
                            client.ack, db_factory=self.lease.session, guard=self._guard, on_saved=self._saved)
        except (LeaseLost, SourceUnavailable):
            raise
        except AcknowledgementFailed:
            self.failure = 'ack_failed'
            client.disconnect()
        except HTTPException:
            self.failure = 'packet_rejected'
            client.disconnect()
        except Exception:
            self.lease.check()  # Database/session loss cannot be treated as a retryable save error.
            self.failure = 'storage_failed'
            client.disconnect()

    def _heartbeat(self):
        with self.lease.session() as db:
            state = self._guard(db)
            _write(state, state.status, state.data.get('error_code'), token=self.token,
                   lease_seconds=self.lease_seconds)
            db.commit()

    def _wait_retry(self, delay):
        deadline = time.monotonic() + delay
        while not self.stop.is_set() and time.monotonic() < deadline:
            self._heartbeat()  # Authorization and lock health also guard backoff.
            self.stop.wait(min(1, max(0, deadline - time.monotonic())))

    def _connection(self):
        self.failure = None
        self.subscribed = False
        self.pending_messages = []
        self._record('connecting', attempt=self.attempt, next_retry_at=None)
        try:
            # A concurrent credential revocation must wait for an already
            # started connection and prevent any new connection afterwards.
            with self.lease.session() as db:
                _source(db,self.source.gateway_id,self.source)
                config=self.source.config
                self.client=(self.client_factory(self.source.gateway_id,config) if self.client_factory else mqtt_client(
                    self.source.gateway_id,config,self.connect_timeout,
                    client_id=receiver_client_id(self.source.gateway_id,self.source.version,self.source.config_hash),
                    workspace_id=self.source.workspace_id,db=db))
                self.client.on_connect = self._connect_callback
                self.client.on_subscribe = self._subscribe_callback
                self.client.on_unsubscribe = self._unsubscribe_callback
                self.client.on_message = self._message_callback
                host,port,_=broker_address(config['broker'],workspace_id=self.source.workspace_id,db=db,
                                            refs=(config['username_ref'],config['password_ref']))
                result = self.client.connect(host, port, keepalive=30)
                db.commit()
            if result != 0:
                return 'broker_unreachable'
            next_guard = time.monotonic()  # Check promptly even if CONNACK never arrives.
            handshake_deadline = time.monotonic() + self.connect_timeout + 5
            while not self.stop.is_set() and not self.failure:
                if time.monotonic() >= next_guard:
                    self._heartbeat()
                    next_guard = time.monotonic() + 1
                result = self.client.loop(timeout=.2)
                if result != 0:
                    return self.failure or ('packet_rejected' if getattr(self.client, 'inbound_packet_rejected', False)
                                            else 'connection_lost')
                if not self.subscribed and time.monotonic() > handshake_deadline:
                    return 'subscription_rejected'
            return self.failure
        finally:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self.client = None

    def run(self):
        try:
            if self.lease is None:
                self.reserve()
            try:
                self._claim()
                try:
                    while not self.stop.is_set():
                        self.attempt += 1
                        try:
                            code = self._connection()
                        except (LeaseLost, SourceUnavailable, CredentialsUnavailable):
                            raise
                        except HTTPException:
                            code = 'packet_rejected'
                        except Exception:
                            # Check the original DB session before treating any
                            # failure as retryable. Driver details never persist.
                            self.lease.check()
                            code = 'broker_unreachable'
                        if self.stop.is_set():
                            break
                        delay = min(30, 2 ** min(self.attempt - 1, 5))
                        self._record('backoff', code or 'connection_lost', attempt=self.attempt,
                                     next_retry_at=(now() + timedelta(seconds=delay)).isoformat())
                        self._wait_retry(delay)
                except SourceUnavailable as error:
                    status = 'stopped' if error.code in {'gateway_paused', 'configuration_changed', 'gateway_missing'} else 'blocked'
                    self._record(status, error.code, terminal=True, next_retry_at=None)
                except CredentialsUnavailable:
                    self._record('blocked', 'credentials_missing', terminal=True, next_retry_at=None)
                else:
                    self._record('stopped', 'stopped', terminal=True, next_retry_at=None)
            finally:
                self.release()
        except LeaseBusy:
            pass  # A standby process must not overwrite the real leader's state.
        except Exception:
            # No DB writes after database/session loss. Existing heartbeat expires
            # and the API reports unknown until a new session claims this gateway.
            pass
        finally:
            self.done.set()


class MqttSupervisor:
    def __init__(self, max_receivers=None, poll_seconds=None, lease_seconds=None, client_factory=None,
                 db_factory=None):
        config = settings()
        self.max_receivers = max_receivers if max_receivers is not None else config.mqtt_receiver_concurrency
        self.poll_seconds = poll_seconds if poll_seconds is not None else config.mqtt_receiver_poll_seconds
        self.lease_seconds = lease_seconds if lease_seconds is not None else config.mqtt_receiver_lease_seconds
        if not 1 <= self.max_receivers <= 8 or not .2 <= self.poll_seconds <= 10 or not 5 <= self.lease_seconds <= 120:
            raise ValueError('后台接收容量、核对间隔或状态时限无效')
        self.connect_timeout = config.mqtt_receiver_connect_timeout_seconds
        self.client_factory = client_factory
        self.db_factory = db_factory or (lambda: Session(engine(), expire_on_commit=False))
        self.listeners = {}

    def _blocked(self, gateway_id, code):
        try:
            with subscription_lease(gateway_id) as lease:
                with lease.session() as db:
                    lease.check(db)
                    gateway = db.scalar(select(MqttGateway).where(MqttGateway.id == gateway_id).with_for_update())
                    if not gateway or gateway.status != 'active':
                        return
                    # Re-check after obtaining the lock: authorization may have
                    # recovered since the reconcile scan.
                    try:
                        _source(db, gateway_id)
                        return
                    except SourceUnavailable as error:
                        code = error.code
                    state = _state(db, gateway_id)
                    if not state:
                        state = MqttReceiverState(gateway_id=gateway_id, workspace_id=gateway.workspace_id,
                                                  gateway_version=gateway.version, config_hash=digest(gateway.data),
                                                  status='standby', data={})
                        db.add(state)
                    state.gateway_version = gateway.version
                    state.config_hash = digest(gateway.data)
                    _write(state, 'blocked', code, next_retry_at=None)
                    db.commit()
        except LeaseBusy:
            pass

    def reconcile(self):
        for identity, listener in list(self.listeners.items()):
            if listener.done.is_set():
                listener.thread.join(timeout=0)
                del self.listeners[identity]
        with self.db_factory() as db:
            identities = list(db.scalars(select(MqttGateway.id).where(MqttGateway.status == 'active')
                                         .order_by(MqttGateway.created_at, MqttGateway.id)))
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

    def close(self):
        for listener in self.listeners.values():
            listener.stop.set()
        for listener in self.listeners.values():
            listener.thread.join(timeout=self.connect_timeout + 6)

    def run(self, stop_requested=lambda: False):
        try:
            while not stop_requested():
                try:
                    self.reconcile()
                except Exception:
                    # A failed database scan cannot leave receivers running with
                    # stale permission. Each listener independently guards too.
                    for listener in self.listeners.values():
                        listener.stop.set()
                deadline = time.monotonic() + self.poll_seconds
                while not stop_requested() and time.monotonic() < deadline:
                    time.sleep(min(.2, max(0, deadline - time.monotonic())))
        finally:
            self.close()


def run_supervisor(stop_requested=lambda: False):
    MqttSupervisor().run(stop_requested=stop_requested)


def main():
    stopped = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    run_supervisor(stop_requested=stopped.is_set)


if __name__ == '__main__':
    main()
