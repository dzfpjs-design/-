"""Real disposable PostgreSQL fencing with bounded, in-memory MQTT clients."""
from queue import Empty, Queue
from contextlib import ExitStack
from threading import Event, Lock, Thread
from types import SimpleNamespace
import time
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Membership, MqttGateway, MqttReceipt, MqttReceiverState, User
from generic_agents.mqtt_subscriber import BoundedMqttClient, MAX_MQTT_PACKET_BODY, LeaseBusy, LeaseLost, subscription_lease
from generic_agents.mqtt_supervisor import GatewayListener, MqttSupervisor, _source, receiver_client_id
from tests.test_mqtt_inbox import gateway, packet


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    assert predicate(), '后台接收状态未在限定时间内变化'


def state_of(identity):
    with Session(engine()) as db:
        state = db.scalar(select(MqttReceiverState).where(MqttReceiverState.gateway_id == identity))
        return None if not state else {'status': state.status, 'token': state.lease_token,
                                       'version': state.gateway_version, 'data': dict(state.data),
                                       'heartbeat': state.heartbeat_at}


def source_of(identity):
    with Session(engine()) as db:
        return _source(db, identity)


class FakeClient:
    def __init__(self, identity):
        self.identity = identity
        self.events = Queue()
        self.acks = []
        self.subscriptions = []
        self.unsubscriptions = []
        self.disconnected = Event()
        self.mid = 0

    def connect(self, host, port, keepalive):
        self.events.put(('connect', None))
        return 0

    def subscribe(self, topics):
        self.subscriptions.append(topics)
        self.mid += 1
        self.events.put(('subscribe', self.mid))
        return 0, self.mid

    def unsubscribe(self, topics):
        self.unsubscriptions.append(topics)
        self.mid += 1
        self.events.put(('unsubscribe', self.mid))
        return 0, self.mid

    def ack(self, mid, qos):
        # A separate database session must see the committed original before ACK.
        with Session(engine()) as db:
            count = db.scalar(select(func.count()).select_from(MqttReceipt)
                              .where(MqttReceipt.gateway_id == self.identity))
            assert count > 0
        self.acks.append((mid, qos))
        return 0

    def loop(self, timeout):
        try:
            action, value = self.events.get(timeout=timeout)
        except Empty:
            return 0
        ok = SimpleNamespace(is_failure=False, value=1)
        if action == 'connect':
            self.on_connect(self, None, {}, ok, None)
        elif action == 'subscribe':
            self.on_subscribe(self, None, value, [ok], None)
        elif action == 'unsubscribe':
            self.on_unsubscribe(self, None, value, [], None)
        elif action == 'message':
            self.on_message(self, None, value)
        elif action == 'lost':
            return 7
        return 0

    def disconnect(self):
        self.disconnected.set()
        return 0

    def send(self, *, topic='sensors/one', raw=None, mid=1):
        self.events.put(('message', SimpleNamespace(topic=topic, payload=raw or packet(),
                        qos=1, retain=False, dup=False, mid=mid)))


class Clients:
    def __init__(self):
        self.clients = []
        self.lock = Lock()

    def __call__(self, identity, config):
        client = FakeClient(identity)
        with self.lock:
            self.clients.append(client)
        return client

    def for_gateway(self, identity):
        with self.lock:
            return [client for client in self.clients if client.identity == identity]


@pytest.fixture
def running(gateway):
    clients = Clients()
    listener = GatewayListener(source_of(gateway[1]), client_factory=clients, lease_seconds=5)
    listener.thread.start()
    try:
        wait_for(lambda: state_of(gateway[1]) and state_of(gateway[1])['status'] == 'receiving')
        yield listener, clients
    finally:
        listener.stop.set()
        listener.thread.join(timeout=6)
        assert listener.done.is_set()


def test_continuous_reader_commits_before_ack_and_has_no_unbounded_receipt_list(gateway, running):
    listener, clients = running
    client = clients.for_gateway(gateway[1])[0]
    for index in range(40):
        client.send(raw=packet(record_id='continuous-' + str(index)), mid=index + 1)
    wait_for(lambda: len(client.acks) == 40)
    state = state_of(gateway[1])
    assert state['data']['received_count'] == 40
    assert state['data']['last_received_at']
    assert not hasattr(listener, 'received_ids')
    assert len(state['data']['history']) == 3  # connecting/subscribing/receiving
    assert state['data']['subscribed_topics'] == ['sensors/one']
    assert clients.for_gateway(gateway[1]) == [client]  # No periodic reconnect.


def test_supervisor_and_bounded_reader_share_one_session_lock(gateway, running):
    leader, clients = running
    original = state_of(gateway[1])
    with pytest.raises(LeaseBusy):
        with subscription_lease(gateway[1]):
            pass
    standby = GatewayListener(source_of(gateway[1]), client_factory=clients, lease_seconds=5)
    standby.thread.start()
    assert standby.done.wait(2)
    standby.thread.join(timeout=1)
    assert state_of(gateway[1])['token'] == original['token'] == leader.token
    assert len(clients.for_gateway(gateway[1])) == 1


@pytest.mark.parametrize('change,status,code', [
    ('pause', 'stopped', 'gateway_paused'),
    ('role', 'blocked', 'owner_permission_removed'),
    ('user', 'blocked', 'owner_unavailable'),
    ('broker', 'blocked', 'broker_not_allowed'),
    ('version', 'stopped', 'configuration_changed'),
    ('config', 'stopped', 'configuration_changed'),
])
def test_current_authorization_is_checked_inside_each_packet_transaction(gateway, running, monkeypatch, change, status, code):
    listener, clients = running
    client = clients.for_gateway(gateway[1])[0]
    with Session(engine()) as db:
        gateway_row = db.get(MqttGateway, gateway[1])
        if change == 'pause':
            gateway_row.status = 'paused'
            gateway_row.version += 1
        elif change == 'version':
            gateway_row.version += 1
        elif change == 'config':
            gateway_row.data = {**gateway_row.data, 'topics': {'new/topic': 'sensor-1'}}
        elif change == 'role':
            member = db.scalar(select(Membership).where(Membership.user_id == gateway[0].user_id))
            member.role = 'viewer'
        elif change == 'user':
            db.get(User, gateway[0].user_id).active = False
        db.commit()
    if change == 'broker':
        monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', '')
    client.send()
    assert listener.done.wait(3)
    assert not client.acks
    state = state_of(gateway[1])
    assert state['status'] == status and state['data']['error_code'] == code
    assert state['token'] is None
    with Session(engine()) as db:
        assert not db.scalars(select(MqttReceipt)).all()


def test_stale_receiver_token_cannot_commit_or_overwrite_a_new_leader(gateway, running):
    listener, clients = running
    replacement = str(uuid4())
    with Session(engine()) as db:
        row = db.scalar(select(MqttReceiverState).where(MqttReceiverState.gateway_id == gateway[1]))
        row.lease_token = replacement
        db.commit()
    clients.for_gateway(gateway[1])[0].send()
    assert listener.done.wait(3)
    assert state_of(gateway[1])['token'] == replacement
    assert not clients.for_gateway(gateway[1])[0].acks
    with Session(engine()) as db:
        assert not db.scalars(select(MqttReceipt)).all()


def test_lost_database_session_never_reconnects_under_its_old_lock(gateway, running):
    listener, clients = running
    before = state_of(gateway[1])
    with engine().begin() as db:
        assert db.scalar(text('SELECT current_database()')).startswith('ga_test_')
        assert db.scalar(text('SELECT pg_terminate_backend(:pid)'), {'pid': listener.lease.pid})
    clients.for_gateway(gateway[1])[0].send()
    assert listener.done.wait(3)
    assert listener.lease.lost
    assert not clients.for_gateway(gateway[1])[0].acks
    # It cannot forge a stopped/receiving heartbeat after the database session died.
    assert state_of(gateway[1])['heartbeat'] == before['heartbeat']
    with pytest.raises(LeaseLost):
        listener.lease.check()
    with subscription_lease(gateway[1]) as fresh:
        fresh.check()


def test_reconnect_keeps_originals_and_resubscribes_only_after_journalled_cleanup(gateway, running):
    listener, clients = running
    first = clients.for_gateway(gateway[1])[0]
    first.send()
    wait_for(lambda: len(first.acks) == 1)
    first.events.put(('lost', None))
    wait_for(lambda: state_of(gateway[1])['status'] == 'backoff')
    assert state_of(gateway[1])['data']['next_retry_at']
    wait_for(lambda: len(clients.for_gateway(gateway[1])) == 2, timeout=4)
    second = clients.for_gateway(gateway[1])[-1]
    wait_for(lambda: state_of(gateway[1])['status'] == 'receiving')
    assert second.unsubscriptions == [['sensors/one']]
    assert second.subscriptions == [[('sensors/one', 1)]]
    second.send()
    wait_for(lambda: len(second.acks) == 1)
    with Session(engine()) as db:
        rows = db.scalars(select(MqttReceipt)).all()
        assert len(rows) == 1 and rows[0].data['deliveries'] == 2


def test_reconcile_keeps_sources_independent_and_obeys_capacity(gateway):
    from generic_agents.mqtt_inbox import create_gateway, set_status
    from tests.test_mqtt_inbox import config
    with Session(engine(), expire_on_commit=False) as db:
        second = create_gateway(db, gateway[0], '另一个模拟接入', config())
        set_status(db, gateway[0], second.id, second.version, 'active')
        db.commit()
        second_id = second.id
    clients = Clients()
    supervisor = MqttSupervisor(max_receivers=2, lease_seconds=5, client_factory=clients)
    try:
        assert supervisor.reconcile() == 2
        wait_for(lambda: all(state_of(identity) and state_of(identity)['status'] == 'receiving'
                             for identity in [gateway[1], second_id]))
        clients.for_gateway(gateway[1])[0].events.put(('lost', None))
        wait_for(lambda: state_of(gateway[1])['status'] == 'backoff')
        second_client = clients.for_gateway(second_id)[0]
        second_client.send(raw=packet(record_id='independent'))
        wait_for(lambda: len(second_client.acks) == 1)
        assert state_of(second_id)['status'] == 'receiving'
    finally:
        supervisor.close()
    with Session(engine()) as db:
        for row in db.scalars(select(MqttReceiverState)):
            assert row.status == 'stopped' and row.lease_token is None


def test_reconcile_blocks_unavailable_owner_without_opening_a_protocol_client(gateway):
    with Session(engine()) as db:
        db.get(User, gateway[0].user_id).active = False
        db.commit()
    clients = Clients()
    supervisor = MqttSupervisor(max_receivers=1, lease_seconds=5, client_factory=clients)
    try:
        assert supervisor.reconcile() == 0
        assert state_of(gateway[1])['status'] == 'blocked'
        assert state_of(gateway[1])['data']['error_code'] == 'owner_unavailable'
        assert not clients.clients
    finally:
        supervisor.close()


def new_gateway(gateway, name):
    from generic_agents.mqtt_inbox import create_gateway, set_status
    from tests.test_mqtt_inbox import config
    with Session(engine(), expire_on_commit=False) as db:
        row = create_gateway(db, gateway[0], name, config())
        set_status(db, gateway[0], row.id, row.version, 'active')
        db.commit()
        return row.id


def test_four_other_leaders_cannot_starve_the_fifth_gateway(gateway):
    identities = [gateway[1]] + [new_gateway(gateway, '模拟来源 ' + str(index)) for index in range(4)]
    clients = Clients()
    supervisor = MqttSupervisor(max_receivers=1, lease_seconds=5, client_factory=clients)
    try:
        with ExitStack() as leases:
            for identity in identities[:4]:
                leases.enter_context(subscription_lease(identity))
            assert supervisor.reconcile() == 1
            wait_for(lambda: state_of(identities[4]) and state_of(identities[4])['status'] == 'receiving')
            assert list(supervisor.listeners) == identities[4:]
            assert [client.identity for client in clients.clients] == identities[4:]
            assert all(state_of(identity) is None for identity in identities[:4])
    finally:
        supervisor.close()


def test_capacity_wait_is_durable_then_receives_when_a_slot_is_released(gateway):
    second = new_gateway(gateway, '等待接收名额的来源')
    clients = Clients()
    supervisor = MqttSupervisor(max_receivers=1, lease_seconds=5, client_factory=clients)
    try:
        assert supervisor.reconcile() == 1
        wait_for(lambda: state_of(gateway[1]) and state_of(gateway[1])['status'] == 'receiving')
        waiting = state_of(second)
        assert waiting['status'] == 'standby' and waiting['data']['error_code'] == 'capacity_wait'
        assert waiting['token'] is None
        with Session(engine()) as db:
            db.get(MqttGateway, gateway[1]).status = 'paused'
            db.commit()
        assert supervisor.listeners[gateway[1]].done.wait(3)
        assert supervisor.reconcile() == 1
        wait_for(lambda: state_of(second)['status'] == 'receiving')
    finally:
        supervisor.close()


def test_config_handover_unsubscribes_old_topics_and_ignores_their_queued_packets(gateway):
    clients = Clients()
    supervisor = MqttSupervisor(max_receivers=1, lease_seconds=5, client_factory=clients)
    try:
        supervisor.reconcile()
        wait_for(lambda: state_of(gateway[1]) and state_of(gateway[1])['status'] == 'receiving')
        previous = supervisor.listeners[gateway[1]]
        with Session(engine()) as db:
            row = db.get(MqttGateway, gateway[1])
            row.data = {**row.data, 'topics': {'new/topic': 'sensor-1'}}
            row.version += 1
            db.commit()
        assert previous.done.wait(3)
        supervisor.reconcile()
        wait_for(lambda: len(clients.clients) == 2 and state_of(gateway[1])['status'] == 'receiving')
        client = clients.clients[-1]
        assert client.unsubscriptions == [['sensors/one']]
        assert client.subscriptions == [[('new/topic', 1)]]
        client.send(topic='sensors/one', raw=packet(record_id='obsolete'), mid=1)
        client.send(topic='new/topic', raw=packet(record_id='current'), mid=2)
        wait_for(lambda: client.acks == [(2, 1)])
        assert state_of(gateway[1])['data']['subscribed_topics'] == ['new/topic']
        with Session(engine()) as db:
            rows = db.scalars(select(MqttReceipt)).all()
            assert len(rows) == 1 and rows[0].data['topic'] == 'new/topic'
    finally:
        supervisor.close()


@pytest.mark.parametrize('stage,expected_count,code', [('store', 0, 'storage_failed'),
                                                   ('commit', 0, 'storage_failed'), ('ack', 1, 'ack_failed')])
def test_commit_and_ack_failure_have_distinct_safe_states(gateway, running, monkeypatch, stage, expected_count, code):
    import generic_agents.mqtt_subscriber as subscriber
    listener, clients = running
    client = clients.for_gateway(gateway[1])[0]
    if stage == 'store':
        def failing_store(*args, **kwargs):
            raise OSError('fake storage failure contains no real secret')
        monkeypatch.setattr(subscriber, 'capture', failing_store)
    elif stage == 'commit':
        original_session = listener.lease.session
        def failing_commit_session():
            db = original_session()
            original_commit = db.commit
            def commit():
                if any(isinstance(row, MqttReceipt) for row in db.identity_map.values()):
                    raise RuntimeError('isolated transaction commit failure')
                original_commit()
            db.commit = commit
            return db
        monkeypatch.setattr(listener.lease, 'session', failing_commit_session)
    else:
        client.ack = lambda *_: 4
    client.send()
    wait_for(lambda: state_of(gateway[1])['status'] == 'backoff')
    state = state_of(gateway[1])
    assert state['data']['error_code'] == code
    assert 'fake storage' not in str(state['data'])
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(MqttReceipt)) == expected_count


def test_credentials_block_without_creating_a_socket_and_recover_when_supplied(gateway, monkeypatch):
    with Session(engine()) as db:
        row = db.get(MqttGateway, gateway[1])
        row.data = {**row.data, 'username_ref': 'GA_MQTT_TEST_USER', 'password_ref': 'GA_MQTT_TEST_PASS'}
        row.version += 1
        db.commit()
    monkeypatch.delenv('GA_MQTT_TEST_USER', raising=False)
    monkeypatch.delenv('GA_MQTT_TEST_PASS', raising=False)
    clients = Clients()
    supervisor = MqttSupervisor(max_receivers=1, lease_seconds=5, client_factory=clients)
    try:
        assert supervisor.reconcile() == 0
        assert state_of(gateway[1])['data']['error_code'] == 'credentials_missing'
        assert not clients.clients
        monkeypatch.setenv('GA_MQTT_TEST_USER', 'isolated-test-user')
        monkeypatch.setenv('GA_MQTT_TEST_PASS', 'isolated-test-password')
        assert supervisor.reconcile() == 1
        wait_for(lambda: state_of(gateway[1])['status'] == 'receiving')
        assert 'GA_MQTT_TEST' not in str(state_of(gateway[1])['data'])
        assert 'isolated-test' not in str(state_of(gateway[1])['data'])
    finally:
        supervisor.close()


@pytest.mark.parametrize('field', ['version', 'hash', 'ttl'])
def test_state_generation_and_expired_ttl_fence_packets(gateway, running, field):
    from datetime import timedelta
    from generic_agents.models import now
    listener, clients = running
    with Session(engine()) as db:
        row = db.scalar(select(MqttReceiverState).where(MqttReceiverState.gateway_id == gateway[1]))
        if field == 'version':
            row.gateway_version += 1
        elif field == 'hash':
            row.config_hash = '0' * 64
        else:
            row.lease_until = now() - timedelta(seconds=1)
        db.commit()
    clients.for_gateway(gateway[1])[0].send()
    assert listener.done.wait(3)
    assert not clients.for_gateway(gateway[1])[0].acks
    with Session(engine()) as db:
        assert not db.scalars(select(MqttReceipt)).all()


@pytest.mark.parametrize('change,code', [('user', 'owner_unavailable'), ('role', 'owner_permission_removed')])
def test_concurrent_revocation_cannot_commit_between_final_guard_and_packet_commit(gateway, running, monkeypatch, change, code):
    listener, clients = running
    client = clients.for_gateway(gateway[1])[0]
    guarded = Event()
    release = Event()
    committing = Event()
    committed = Event()
    revoker_pid = []
    errors = []
    original_saved = listener._saved
    def hold_final_guard(db, identifier):
        original_saved(db, identifier)
        guarded.set()
        assert release.wait(4)
    monkeypatch.setattr(listener, '_saved', hold_final_guard)
    def revoke():
        try:
            with Session(engine()) as db:
                revoker_pid.append(db.scalar(text('SELECT pg_backend_pid()')))
                if change == 'user':
                    db.get(User, gateway[0].user_id).active = False
                else:
                    member = db.scalar(select(Membership).where(Membership.user_id == gateway[0].user_id))
                    member.role = 'viewer'
                committing.set()
                db.commit()
                committed.set()
        except Exception as error:
            errors.append(type(error).__name__)
    client.send(raw=packet(record_id='accepted-before-revocation'), mid=1)
    assert guarded.wait(3)
    revoker = Thread(target=revoke)
    revoker.start()
    try:
        assert committing.wait(2)
        def blocked_by_receiver():
            with engine().connect() as db:
                return listener.lease.pid in db.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': revoker_pid[0]})
        wait_for(blocked_by_receiver, timeout=2)
        assert not committed.is_set()
        release.set()
        assert committed.wait(3)
        wait_for(lambda: client.acks == [(1, 1)])
        client.send(raw=packet(record_id='rejected-after-revocation'), mid=2)
        assert listener.done.wait(3)
        assert client.acks == [(1, 1)]
        assert state_of(gateway[1])['data']['error_code'] == code
        with Session(engine()) as db:
            assert db.scalar(select(func.count()).select_from(MqttReceipt)) == 1
    finally:
        release.set()
        revoker.join(timeout=4)
    assert not errors


def test_client_session_is_stable_only_for_the_current_authorized_generation(gateway):
    source = source_of(gateway[1])
    identity = receiver_client_id(source.gateway_id, source.version, source.config_hash)
    assert identity == receiver_client_id(source.gateway_id, source.version, source.config_hash)
    assert identity != receiver_client_id(source.gateway_id, source.version + 1, source.config_hash)
    assert identity != receiver_client_id(source.gateway_id, source.version, '0' * 64)


def remaining_length(length):
    result = bytearray()
    while True:
        digit = length % 128
        length //= 128
        result.append(digit | (128 if length else 0))
        if not length:
            return bytes(result)


class WireSocket:
    def __init__(self, wire):
        self.wire = bytearray(wire)
        self.requests = []

    def recv(self, size):
        self.requests.append(size)
        if not self.wire:
            raise BlockingIOError()
        result = bytes(self.wire[:size])
        del self.wire[:size]
        return result

    def close(self):
        pass


def frame_client(wire):
    import paho.mqtt.client as mqtt
    client = BoundedMqttClient(mqtt.CallbackAPIVersion.VERSION2, client_id='isolated-wire-test',
                              clean_session=False, manual_ack=True)
    client._sock = WireSocket(wire)
    return client


@pytest.mark.parametrize('length', [MAX_MQTT_PACKET_BODY + 1, 268435455])
def test_wire_limit_rejects_frame_before_reading_or_allocating_its_body(length):
    import paho.mqtt.client as mqtt
    client = frame_client(b'\x32' + remaining_length(length))
    assert client._packet_read() == mqtt.MQTT_ERR_CONN_LOST
    assert client.inbound_packet_rejected
    assert all(size == 1 for size in client._sock.requests)
    assert not client._in_packet['packet']


def test_wire_limit_survives_fragmented_remaining_length():
    import paho.mqtt.client as mqtt
    encoded = remaining_length(MAX_MQTT_PACKET_BODY + 1)
    client = frame_client(b'\x32' + encoded[:1])
    assert client._packet_read() == mqtt.MQTT_ERR_AGAIN
    assert not client.inbound_packet_rejected
    client._sock.wire.extend(encoded[1:])
    assert client._packet_read() == mqtt.MQTT_ERR_CONN_LOST
    assert client.inbound_packet_rejected and not client._in_packet['packet']
    assert all(size == 1 for size in client._sock.requests)


def test_maximum_allowed_wire_frame_is_processed_normally(monkeypatch):
    import paho.mqtt.client as mqtt
    client = frame_client(b'\x32' + remaining_length(MAX_MQTT_PACKET_BODY) + b'x' * MAX_MQTT_PACKET_BODY)
    handled = []
    monkeypatch.setattr(client, '_packet_handle', lambda: handled.append(len(client._in_packet['packet'])) or mqtt.MQTT_ERR_SUCCESS)
    assert client._packet_read() == mqtt.MQTT_ERR_SUCCESS
    assert handled == [MAX_MQTT_PACKET_BODY] and not client.inbound_packet_rejected
    assert not client._in_packet['packet']


def test_nonconforming_qos2_frame_cannot_fill_paho_pending_handshake_memory():
    import paho.mqtt.client as mqtt
    client = frame_client(b'')
    client._in_packet['command'] = 0x34
    assert client._handle_publish() == mqtt.MQTT_ERR_PROTOCOL
    assert client.inbound_packet_rejected and not client._in_messages


def test_persistent_replay_before_suback_is_bounded_then_committed_after_confirmation(gateway):
    clients = []
    class ReplayClient(FakeClient):
        def connect(self, host, port, keepalive):
            result = super().connect(host, port, keepalive)
            self.send(raw=packet(record_id='queued-before-suback'))
            return result
    def replay_factory(identity, config):
        client = ReplayClient(identity)
        clients.append(client)
        return client
    listener = GatewayListener(source_of(gateway[1]), client_factory=replay_factory, lease_seconds=5)
    listener.thread.start()
    try:
        wait_for(lambda: clients and clients[0].acks == [(1, 1)])
        assert listener.subscribed and not listener.pending_messages
        assert state_of(gateway[1])['status'] == 'receiving'
    finally:
        listener.stop.set()
        listener.thread.join(timeout=4)


def test_no_active_source_never_constructs_a_network_client():
    clients = Clients()
    supervisor = MqttSupervisor(client_factory=clients)
    try:
        assert supervisor.reconcile() == 0
        assert not clients.clients
    finally:
        supervisor.close()


def test_failed_reconcile_database_scan_stops_existing_listeners(gateway):
    clients = Clients()
    supervisor = MqttSupervisor(max_receivers=1, poll_seconds=.2, lease_seconds=5, client_factory=clients)
    supervisor.reconcile()
    listener = supervisor.listeners[gateway[1]]
    wait_for(lambda: state_of(gateway[1]) and state_of(gateway[1])['status'] == 'receiving')
    def unavailable_scan():
        raise ConnectionError('isolated scan failure')
    supervisor.db_factory = unavailable_scan
    supervisor.run(stop_requested=listener.done.is_set)
    assert listener.done.is_set() and clients.clients[0].disconnected.is_set()
    assert state_of(gateway[1])['status'] == 'stopped'
    assert 'isolated scan' not in str(state_of(gateway[1])['data'])
