"""Bounded reader and shared session lease. Never sends device commands."""
from contextlib import contextmanager
from hashlib import sha256
import os
import ssl
import time
import paho.mqtt.client as mqtt
from sqlalchemy import text
from sqlalchemy.orm import Session
from .db import engine
from .mqtt_inbox import authorized_gateway, broker_address, capture


class AcknowledgementFailed(RuntimeError):
    """The original is committed, but protocol acknowledgement did not complete."""


def capture_and_ack(principal, gateway_id, version, message, acknowledge, db_factory=None, guard=None,
                    on_saved=None):
    factory = db_factory or (lambda: Session(engine(), expire_on_commit=False))
    with factory() as db:
        if guard:
            guard(db)
        receipt = capture(db, principal, gateway_id, version, message.topic, bytes(message.payload),
                          message.qos, bool(message.retain), bool(message.dup))
        identifier = receipt.id
        if guard:
            guard(db)
        if on_saved:
            on_saved(db, identifier)
        db.commit()  # The separate acknowledgement can fail without losing this receipt.
    if message.qos:
        try:
            if acknowledge(message.mid, message.qos) != 0:
                raise AcknowledgementFailed()
        except Exception:
            raise AcknowledgementFailed('原报文已保存，协议确认失败；重连后按原件去重') from None
    return identifier


class LeaseBusy(RuntimeError):
    """Another bounded reader or supervisor already owns this gateway."""


class LeaseLost(RuntimeError):
    """The original lock-holding database session cannot be trusted."""


class SubscriptionLease:
    def __init__(self, connection, key, pid):
        self.connection = connection
        self.key = key
        self.pid = pid
        self.lost = False

    def check(self, db=None):
        # A SQLAlchemy invalidated connection can reconnect automatically. Never
        # let that new session impersonate the session holding the advisory lock.
        if self.lost or self.connection.closed or self.connection.invalidated:
            self.lost = True
            raise LeaseLost('接收数据库会话已失效')
        unsigned = self.key & ((1 << 64) - 1)
        statement = text('''SELECT pg_backend_pid() = :pid AND EXISTS (
            SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted
            AND pid = pg_backend_pid() AND classid = :high AND objid = :low AND objsubid = 1)''')
        try:
            owns = (db or self.connection).scalar(statement, {
                'pid': self.pid, 'high': unsigned >> 32, 'low': unsigned & ((1 << 32) - 1)})
            if not owns:
                raise LeaseLost('接收独占会话已失效')
        except Exception:
            self.lost = True
            raise LeaseLost('接收数据库会话已失效') from None

    def session(self):
        self.check()
        # check() begins a connection transaction. Session must commit that actual
        # transaction before the MQTT acknowledgement, not merely a savepoint.
        return Session(bind=self.connection, expire_on_commit=False,
                       join_transaction_mode='control_fully')


@contextmanager
def subscription_lease(gateway_id):
    # Keep the same database connection until disconnect; a crash releases this lease.
    key = int.from_bytes(sha256(('mqtt-reader:' + gateway_id).encode()).digest()[:8], 'big', signed=True)
    with engine().connect() as connection:
        acquired = connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': key})
        if not acquired:
            raise LeaseBusy('此接入已有正在运行的读取进程')
        pid = connection.scalar(text('SELECT pg_backend_pid()'))
        connection.commit()  # Session advisory locks survive COMMIT.
        lease = SubscriptionLease(connection, key, pid)
        try:
            yield lease
        finally:
            # Cleanup must not reconnect a failed session or conceal its failure.
            if not connection.closed and not connection.invalidated:
                try:
                    connection.rollback()
                    connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
                    connection.commit()
                except Exception:
                    connection.invalidate()


class CredentialsUnavailable(RuntimeError):
    pass


MAX_MQTT_PACKET_BODY = 65536 + 260  # 64KiB original + topic length/topic/PUBLISH id.


class BoundedMqttClient(mqtt.Client):
    """Paho 2.1.0 framing guard before its reader allocates the packet body.

    _packet_read decodes Remaining Length, sets have_remaining, then calls
    _sock_recv for body bytes. Guarding that boundary bounds memory for both
    complete and fragmented incoming frames; callback payload checks are later.
    """
    inbound_packet_rejected = False

    def _sock_recv(self, bufsize):
        packet = self._in_packet
        if packet['have_remaining'] and packet['remaining_length'] > MAX_MQTT_PACKET_BODY:
            self.inbound_packet_rejected = True
            raise OSError('MQTT接收帧超过原件保存上限')
        return super()._sock_recv(bufsize)

    def _handle_publish(self):
        if ((self._in_packet['command'] >> 1) & 3) > 1:
            # Our subscriptions require QoS1. Reject a nonconforming QoS2 broker
            # before Paho stores an unbounded set of pending inbound handshakes.
            self.inbound_packet_rejected = True
            return mqtt.MQTT_ERR_PROTOCOL
        return super()._handle_publish()


def mqtt_client(gateway_id, config, connect_timeout=3, client_id_prefix='ga-read-', client_id=None,
                workspace_id=None, db=None):
    """Create a read-only protocol client; credentials never enter receiver state."""
    client = BoundedMqttClient(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id or client_id_prefix + gateway_id,
                              clean_session=False, protocol=mqtt.MQTTv311, manual_ack=True)
    client.connect_timeout = connect_timeout
    if config['username_ref']:
        from .managed_mqtt_grants import ref_id,credentials
        identity=ref_id(config['username_ref'],config['password_ref'])
        if identity:
            if not workspace_id:raise CredentialsUnavailable('MQTT 授权缺少工作区')
            username,password=credentials(workspace_id,identity,db)
        else:
            username = os.environ.get(config['username_ref'])
            password = os.environ.get(config['password_ref'])
        if not username or not password:
            raise CredentialsUnavailable('已配置的MQTT访问凭据未准备好')
        client.username_pw_set(username, password)
    if broker_address(config['broker'],workspace_id=workspace_id,db=db,
                      refs=(config['username_ref'],config['password_ref']))[2]:
        client.tls_set_context(ssl.create_default_context())
    client.max_inflight_messages_set(10)
    return client


class MqttSubscriber:
    def __init__(self, principal, gateway_id, version):
        self.principal = principal; self.gateway_id = gateway_id; self.version = version
        self.received_ids = []; self.failure = None; self.subscribed = False

    def _configuration(self):
        with self._lease.session() as db:
            self._lease.check(db)
            row = authorized_gateway(db, self.principal, self.gateway_id, self.version)
            return dict(row.data)

    def run(self, *, timeout_seconds=60, max_messages=None, stop_requested=lambda: False,
            on_subscribed=lambda: None):
        if not 0 < timeout_seconds <= 3600 or (max_messages is not None and not 0 < max_messages <= 10000):
            raise ValueError('读取时长或报文数量超限')
        import paho.mqtt.client as mqtt
        with subscription_lease(self.gateway_id) as lease:
            self._lease = lease
            config = self._configuration()
            host, port, use_tls = broker_address(config['broker'],workspace_id=self.principal.workspace_id,
                                                  refs=(config['username_ref'],config['password_ref']))
            client = mqtt_client(self.gateway_id, config,workspace_id=self.principal.workspace_id)

            def connected(current, userdata, flags, reason_code, properties):
                if reason_code.is_failure:
                    self.failure = '代理拒绝连接'; current.disconnect(); return
                try:
                    current_config = self._configuration()
                    result, _ = current.subscribe([(topic, 1) for topic in current_config['topics']])
                    if result != mqtt.MQTT_ERR_SUCCESS: raise RuntimeError()
                except Exception:
                    self.failure = '接入许可变化或订阅失败'; current.disconnect()

            def subscribed(current, userdata, mid, reason_codes, properties):
                if not reason_codes or any(code.is_failure or code.value != 1 for code in reason_codes):
                    self.failure = '代理未确认QoS1订阅'; current.disconnect()
                else:
                    self.subscribed = True
                    on_subscribed()

            def received(current, userdata, message):
                try:
                    identity = capture_and_ack(self.principal, self.gateway_id, self.version, message, current.ack,
                                               db_factory=lease.session, guard=lease.check)
                    self.received_ids.append(identity)
                    if max_messages is not None and len(self.received_ids) >= max_messages:
                        current.disconnect()
                except Exception:
                    # Do not leak driver errors/credentials; do not ACK an uncommitted original.
                    self.failure = '报文接收未完成，已保存的原件保留；请检查许可或存储后重连'
                    current.disconnect()

            client.on_connect = connected; client.on_subscribe = subscribed; client.on_message = received
            deadline = time.monotonic() + timeout_seconds
            next_guard = time.monotonic() + 1
            try:
                with lease.session() as db:
                    lease.check(db)
                    authorized_gateway(db,self.principal,self.gateway_id,self.version)
                    client.connect(host, port, keepalive=30)
                    db.commit()
                while time.monotonic() < deadline and not stop_requested() and not self.failure:
                    result = client.loop(timeout=.2)
                    if result != mqtt.MQTT_ERR_SUCCESS:
                        if max_messages is not None and len(self.received_ids) >= max_messages: break
                        self.failure = '代理连接已中断，原收件保留'; break
                    if max_messages is not None and len(self.received_ids) >= max_messages: break
                    if time.monotonic() >= next_guard:
                        self._configuration(); next_guard = time.monotonic() + 1
            finally:
                client.disconnect()
            return {'received': len(self.received_ids), 'receipt_ids': list(self.received_ids),
                    'subscribed': self.subscribed, 'failure': self.failure}


def main():
    """Operator-only explicit reader process; no automatic device/control worker activation."""
    import argparse
    import json
    from sqlalchemy import select
    from .models import MqttGateway, Membership
    from .security import Principal
    parser = argparse.ArgumentParser()
    parser.add_argument('--gateway', required=True)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--messages', type=int)
    args = parser.parse_args()
    with Session(engine()) as db:
        row = db.get(MqttGateway, args.gateway)
        if not row: raise SystemExit('接入不存在')
        membership = db.scalar(select(Membership).where(Membership.workspace_id == row.workspace_id,
                                                       Membership.user_id == row.owner_id))
        if not membership: raise SystemExit('接入执行身份不可用')
        principal = Principal(row.owner_id, row.workspace_id, membership.role, 'mqtt-reader')
        version = row.version
    try:
        result = MqttSubscriber(principal, args.gateway, version).run(
            timeout_seconds=args.seconds, max_messages=args.messages,
            on_subscribed=lambda: print('SUBSCRIBED', flush=True))
    except Exception:
        raise SystemExit('接入未完成；请核对许可、存储和代理连接。原收件保留。')
    print(json.dumps(result), flush=True)
    if result['failure']: raise SystemExit(1)


if __name__ == '__main__':
    main()
