"""Bounded, workspace-scoped Cloud API 1 Dock handshake responder.

Never subscribes through the generic raw-evidence receiver. Incoming secrets
and credential-bearing replies are processed only in memory; audit stores
only a keyed digest, method and delivery status. This is not Cloud API 2.
"""
from __future__ import annotations
import hashlib
import json
import ssl
import threading
import time
from queue import Queue, Empty, Full
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import engine
from .managed_file_grants import _key, _mac
from .managed_mqtt_grants import credentials, get_grant, lock_workspace
from .models import Job, Membership, User
from .dji_onboarding_api import KIND, decrypt, experimental_enabled
from .dji_onboarding_protocol import prepare_reply

EVENT = 'dji_onboarding_reply_v1'


def _permitted(db, workspace, identity):
    if not experimental_enabled(): raise HTTPException(403, '受控上云测试未启用')
    lock_workspace(db, workspace, shared=True)
    row = db.scalar(select(Job).where(Job.id == identity, Job.workspace_id == workspace,
        Job.kind == KIND, Job.status == 'internal').with_for_update())
    if not row or row.data.get('enabled') is not True:
        raise HTTPException(403, '上云配置已停用')
    user = db.scalar(select(User).where(User.id == row.data['actor_id'], User.active.is_(True)).with_for_update(read=True))
    role = db.scalar(select(Membership.role).where(Membership.workspace_id == workspace,
        Membership.user_id == row.data['actor_id']).with_for_update(read=True))
    if not user or role != 'admin': raise HTTPException(403, '配置人权限已失效')
    grant = get_grant(workspace, row.data['broker_authorization_id'], db)
    if grant['broker'] != row.data['broker']: raise HTTPException(403, 'MQTT 授权已变化')
    return row, grant, decrypt(db, row)


def reply_once(workspace, identity, topic, payload, publish, *, lease=None):
    """Process one message under revocation lock; return True only when sent.

    ``publish`` receives a topic and bytes and must return only after a QoS 1
    acknowledgement. The database commits after that result. A crash may cause
    a duplicate response to the same request, which is safe for these response
    methods; no flight command is ever retried or emitted here.
    """
    if lease: lease.check()
    with (lease.session() if lease else Session(engine(), expire_on_commit=False)) as db:
        row, grant, config = _permitted(db, workspace, identity)
        bound = row.data.get('bound_devices', [])
        destination, response, method, tid, bid = prepare_reply(topic, payload, config, bound_devices=bound)
        fingerprint = _mac(_key(), {'topic': topic, 'payload_sha256': hashlib.sha256(payload).hexdigest()})
        key = 'dji-onboard-reply:' + hashlib.sha256((identity+'\0'+method+'\0'+tid+'\0'+bid).encode()).hexdigest()
        existing = db.scalar(select(Job).where(Job.workspace_id == workspace, Job.idempotency_key == key))
        if existing and (existing.kind != EVENT or existing.data.get('fingerprint') != fingerprint):
            raise HTTPException(409, '同一上云请求编号对应不同内容')
        # Binding state is an explicit server-side acceptance, not evidence the
        # aircraft completed onboarding. Do not store the request or reply.
        if method == 'airport_organization_bind':
            data = json.loads(response.decode())['data']['output']['err_infos']
            bound = sorted(set(bound) | {item['sn'] for item in data})
        publish(destination, response)
        if lease: lease.check(db)
        if not existing:
            db.add(Job(workspace_id=workspace, kind=EVENT, status='internal', idempotency_key=key,
                       data={'configuration_id': identity, 'method': method, 'fingerprint': fingerprint,
                             'delivery': 'broker_acknowledged'}))
        row.data = {**row.data, 'bound_devices': bound, 'last_stage': method,
                    'last_reply_at': datetime.now(timezone.utc).isoformat()}
        row.version += 1; db.commit()
        return True


def _report(workspace, identity, state):
    """Record only a fixed diagnostic state, never the broker error or payload."""
    try:
        with Session(engine()) as db:
            row = db.scalar(select(Job).where(Job.id == identity, Job.workspace_id == workspace,
                Job.kind == KIND, Job.status == 'internal').with_for_update())
            if row and row.data.get('receiver_state') != state:
                row.data = {**row.data, 'receiver_state': state,
                            'receiver_reported_at': datetime.now(timezone.utc).isoformat()}; row.version += 1; db.commit()
    except Exception:
        pass


def _listen(workspace, identity, stop):
    import paho.mqtt.client as mqtt
    from urllib.parse import urlsplit
    from .mqtt_subscriber import BoundedMqttClient, subscription_lease, LeaseBusy
    queue = Queue(maxsize=32)
    publish_results = {}
    subscription_confirmed = threading.Event()
    subscription_failed = threading.Event()
    try:
      with subscription_lease('dji-onboarding:'+workspace+':'+identity) as lease:
        client = None
        try:
            _report(workspace, identity, 'connecting')
            lease.check(); lease.connection.commit()
            with Session(engine(), expire_on_commit=False) as db:
                row, grant, config = _permitted(db, workspace, identity)
                username, password = credentials(workspace, grant['id'], db)
                address = urlsplit(grant['broker']); sn = config['dock_sn']
                client = BoundedMqttClient(mqtt.CallbackAPIVersion.VERSION2,
                    client_id='ga-onboard-'+identity.replace('-', '')[:24],
                    protocol=mqtt.MQTTv5, manual_ack=True)
                client.username_pw_set(username, password); client.tls_set_context(ssl.create_default_context())
                def connected(c, _u, _f, reason, _props):
                    if reason == 0:
                        # MQTT 5 normally clears RETAIN for messages published
                        # after subscription. Preserve it and do not request
                        # retained history, or a stale/forged config message
                        # can solicit credentials from the responder.
                        options = mqtt.SubscribeOptions(qos=1, retainAsPublished=True, retainHandling=2)
                        c.subscribe([(f'sys/product/{sn}/status', options),
                                     (f'thing/product/{sn}/requests', options)])
                    else:
                        _report(workspace, identity, 'broker_rejected')
                def subscribed(_c, _u, _mid, reasons, _props):
                    accepted = len(reasons or []) == 2 and all(not x.is_failure and x.value == 1 for x in reasons)
                    if accepted: subscription_confirmed.set()
                    else:
                        subscription_confirmed.clear()
                        subscription_failed.set()
                        # Do not acknowledge queued QoS 1 messages when the
                        # complete subscription cannot be established.
                        _c.disconnect()
                    _report(workspace, identity, 'receiving' if accepted else 'subscription_rejected')
                def message(_c, _u, msg):
                    # MQTT permits PUBLISH before SUBACK. Buffer only in
                    # memory (the packet reader and queue bound its size),
                    # and never acknowledge or process before both grants.
                    try: queue.put_nowait((msg.topic, bytes(msg.payload), msg.mid, msg.qos, msg.retain))
                    except Full:
                        subscription_failed.set()
                        _c.disconnect()  # leave pending packets unacknowledged
                def published(_c, _u, mid, reason, _props):
                    # is_published() also becomes true for a negative PUBACK.
                    publish_results[mid] = not reason.is_failure
                client.on_connect = connected; client.on_subscribe = subscribed; client.on_message = message
                client.on_publish = published
                client.connect(address.hostname, address.port, keepalive=30,
                               clean_start=mqtt.MQTT_CLEAN_START_FIRST_ONLY); db.commit()
            last_guard = time.monotonic()
            while not stop.is_set():
                lease.check(); lease.connection.commit()
                if client.loop(timeout=.2) != mqtt.MQTT_ERR_SUCCESS: break
                if subscription_failed.is_set(): break
                if time.monotonic() - last_guard >= 2:
                    with Session(engine()) as guard:
                        _permitted(guard, workspace, identity)
                    last_guard = time.monotonic()
                if not subscription_confirmed.is_set(): continue
                try: topic, payload, mid, qos, retained = queue.get_nowait()
                except Empty: continue
                if retained:
                    if qos: client.ack(mid, qos)
                    payload = b''
                    continue
                def send(out_topic, out_payload):
                    packet = client.publish(out_topic, out_payload, qos=1, retain=False)
                    if packet.rc != mqtt.MQTT_ERR_SUCCESS: raise TimeoutError('broker publish unavailable')
                    deadline = time.monotonic()+10
                    while packet.mid not in publish_results and time.monotonic()<deadline and not stop.is_set() and not subscription_failed.is_set():
                        if client.loop(timeout=.1) != mqtt.MQTT_ERR_SUCCESS: break
                    if subscription_failed.is_set() or publish_results.pop(packet.mid, None) is not True:
                        raise TimeoutError('broker acknowledgement unavailable or rejected')
                try:
                    if reply_once(workspace, identity, topic, payload, send, lease=lease): client.ack(mid, qos)
                except (ValueError, HTTPException):
                    # Malformed/unapproved packets receive no reply and do not
                    # enter raw storage. Drop them to avoid unbounded replay.
                    client.ack(mid, qos)
                finally:
                    payload = b''
        except Exception as error:
            # Exception class only; broker messages can contain credentials.
            print('DJI 受控上云接收中断：'+type(error).__name__, flush=True)
            _report(workspace, identity, 'unavailable')
        finally:
            if client:
                try: client.disconnect()
                except Exception: pass
            _report(workspace, identity, 'stopped' if stop.is_set() else 'unavailable')
    except LeaseBusy:
        pass


def start_workspace_onboarding(workspace, stop_event):
    """Start only the named workspace; caller owns stop and join."""
    def supervise():
        workers = {}
        while not stop_event.is_set():
            try:
                with Session(engine()) as db:
                    ids = list(db.scalars(select(Job.id).where(Job.workspace_id == workspace,
                        Job.kind == KIND, Job.status == 'internal', Job.data['enabled'].as_boolean().is_(True))
                        .order_by(Job.created_at, Job.id).limit(100)))
                for key, thread in list(workers.items()):
                    if not thread.is_alive(): workers.pop(key)
                for identity in ids:
                    if len(workers) >= 4: break
                    if identity not in workers:
                        thread = threading.Thread(target=_listen, args=(workspace, identity, stop_event), daemon=True)
                        workers[identity] = thread; thread.start()
            except Exception:
                pass
            stop_event.wait(5)
        for thread in workers.values(): thread.join(timeout=15)
    thread = threading.Thread(target=supervise, name='dji-onboarding-'+workspace[:8], daemon=True)
    thread.start(); return thread
