"""Finite loopback AMQTT audit fixture; records real packets, never sensor truth.

The observer does not replace broker handling or manufacture subscriber results.
Only protocol metadata and simulated-payload hashes are persisted.
"""
import asyncio
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import sys
import time

from amqtt.broker import Broker
from amqtt.events import BrokerEvents, MQTTEvents


async def main(port, trace):
    audit_limit = int(sys.argv[4]) if len(sys.argv) > 4 else 2000
    assert 2000 <= audit_limit <= 12000
    commit_arg = sys.argv[5] if len(sys.argv) > 5 else 'all'
    commit_audit_limit = None if commit_arg == 'all' else int(commit_arg)
    assert commit_audit_limit is None or 1 <= commit_audit_limit <= 2000
    commit_audits = 0
    broker = Broker({
        'listeners': {'default': {'type': 'tcp', 'bind': f'127.0.0.1:{port}', 'max_connections': 12}},
        'plugins': {'amqtt.plugins.authentication.AnonymousAuthPlugin': {'allow_anonymous': True}},
    })
    original_fire = broker.plugins_manager.fire_event
    trace = Path(trace)
    count = 0
    delivered = {}

    async def independently_committed(raw_hash):
        # The broker interpreter deliberately contains no application packages.
        # A fresh app interpreter opens an independent PG session at PUBACK handling.
        script = '''import os, sys
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
url = os.environ["GA_MQTT_PROTOCOL_AUDIT_DATABASE_URL"]
assert make_url(url).database.startswith("ga_test_")
database = create_engine(url)
with database.connect() as connection:
    visible = connection.scalar(text("SELECT EXISTS (SELECT 1 FROM mqtt_receipts WHERE input_sha256=:sha)"), {"sha": sys.argv[1]})
print("VISIBLE" if visible else "MISSING")
database.dispose()
'''
        check = await asyncio.create_subprocess_exec(
            os.environ['GA_MQTT_PROTOCOL_AUDIT_PYTHON'], '-c', script, raw_hash,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(check.communicate(), timeout=5)
        except asyncio.TimeoutError:
            check.kill(); await check.wait()
            raise RuntimeError('Independent protocol receipt query timed out') from None
        return check.returncode == 0 and output.strip() == b'VISIBLE'

    async def observed_fire(event, *, wait=False, **kwargs):
        nonlocal count, commit_audits
        row = None
        if event in {MQTTEvents.PACKET_RECEIVED, MQTTEvents.PACKET_SENT}:
            packet, session = kwargs['packet'], kwargs.get('session')
            if session is not None and session.client_id.startswith('ga-read-'):
                row = {'event': str(event), 'client_id': session.client_id,
                       'packet': type(packet).__name__, 'mid': getattr(packet, 'packet_id', None)}
                if type(packet).__name__ == 'SubscribePacket':
                    row['topics'] = packet.payload.topics
                elif type(packet).__name__ == 'PublishPacket':
                    row.update(topic=packet.topic_name, qos=packet.qos,
                               sha256=sha256(packet.data).hexdigest())
                    if event == MQTTEvents.PACKET_SENT:
                        delivered[(session.client_id, packet.packet_id)] = row['sha256']
                elif type(packet).__name__ == 'PubackPacket' and event == MQTTEvents.PACKET_RECEIVED:
                    raw_hash = delivered.get((session.client_id, packet.packet_id))
                    if raw_hash and os.getenv('GA_MQTT_PROTOCOL_AUDIT_DATABASE_URL'):
                        row['sha256'] = raw_hash
                        if commit_audit_limit is None or commit_audits < commit_audit_limit:
                            commit_audits += 1
                            row['committed_visible_at_puback'] = await independently_committed(raw_hash)
                        else:
                            row['commit_audit'] = 'not_sampled'
        elif event in {BrokerEvents.CLIENT_CONNECTED, BrokerEvents.CLIENT_DISCONNECTED}:
            if kwargs['client_id'].startswith('ga-read-'):
                row = {'event': str(event), 'client_id': kwargs['client_id']}
        if row is not None:
            count += 1
            if count > audit_limit:
                raise RuntimeError('Loopback protocol fixture audit limit exceeded')
            row.update(broker_pid=os.getpid(), sequence=count, time=time.time())
            with trace.open('a', encoding='utf-8') as output:
                output.write(json.dumps(row, separators=(',', ':')) + '\n')
        await original_fire(event, wait=wait, **kwargs)

    broker.plugins_manager.fire_event = observed_fire
    await broker.start()
    print('READY', flush=True)
    try:
        # A lost test controller must not leave a permanent local listener.
        lifetime = int(sys.argv[3]) if len(sys.argv) > 3 else 240
        assert 1 <= lifetime <= 1800
        await asyncio.wait_for(asyncio.Event().wait(), timeout=lifetime)
    except asyncio.TimeoutError:
        pass
    finally:
        await broker.shutdown()


if __name__ == '__main__':
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
