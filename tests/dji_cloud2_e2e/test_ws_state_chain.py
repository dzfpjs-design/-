"""Real JNI -> IPC -> workspace DB -> revoked-publication fence, synthetic only.

This manually orchestrates the same bridge and publication primitives used by
the worker. It does not claim to exercise the worker's automatic loop.
"""
import json
import os
import threading
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents import dji_cloud2_sandbox_journal as journal
from generic_agents.dji_cloud2_connections import is_current, publish_state
from generic_agents.dji_cloud2_process import Bridge, BridgeRevoked
from generic_agents.dji_cloud2_runtime import _report_websocket
from generic_agents.dji_cloud2_sandbox import SandboxSpec
from generic_agents.models import Job
from generic_agents.mqtt_subscriber import subscription_lease
from tests.test_managed_mqtt_grants import vault
from tests.dji_cloud2_e2e.test_sandbox_sdk import (
    DOCKER, SYNTHETIC_SECRET, TestOnlySubnetSandbox, assets, docker, events,
    make_connection, remove_owned_container, wait_for,
)

pytestmark = pytest.mark.skipif(os.getenv('GA_DJI2_E2E_ENABLE') != '1', reason='explicit isolated Linux Docker E2E opt-in required')


def test_jni_status_reaches_workspace_and_revoke_fences_late_result(client, account, vault, monkeypatch, assets):
    guard, sdk, _control, fixture, subnet = assets
    host = str(subnet.network_address + 10)
    record = make_connection(client, host, subnet, monkeypatch, ws_secret='synthetic-wrong-ws')
    workspace = account[2]
    spec = SandboxSpec(workspace_id=workspace, connection_id=record['id'], generation=record['version'],
        host=host, port=18443, policy_digest='a'*64, guard_image=guard, sdk_image=sdk, allowed_networks=(str(subnet),))

    def authorized(_spec=None):
        with Session(engine()) as db:
            return is_current(db, workspace, record['id'], record['version'])

    sandbox = TestOnlySubnetSandbox(spec, authorized, docker=DOCKER, subnet=subnet, allow_test_fixtures=True)
    name = 'ga-dji2-e2e-ws-source-' + uuid4().hex
    lease_id = sandbox.guard_name.rsplit('-', 1)[1]
    journal_id = None
    try:
        with Session(engine()) as db:
            journal_id = journal.begin(db, sandbox); db.commit()
        sandbox.prepare_network()
        with Session(engine()) as db:
            journal.record_network(db, workspace, journal_id, sandbox); db.commit()
        docker(['run', '--detach', '--rm', '--name', name, '--network', sandbox.network_id, '--ip', host,
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--no-healthcheck', '--read-only',
            '--label', 'org.generic-agents.component=dji-cloud2-e2e-fixture',
            '--label', 'org.generic-agents.lease='+lease_id,
            '--env', 'FIXTURE_ROLE=source', '--env', 'FIXTURE_MODE=normal', fixture])
        wait_for(lambda: any(item.get('kind') == 'ready' for item in events(name)))
        proc = sandbox.start(); sandbox.preflight_sdk()
        journal.send_init(workspace, journal_id, sandbox); sandbox.read_sdk_ready()
        with Session(engine()) as db:
            journal.mark_active(db, workspace, journal_id, sandbox); db.commit()

        # Adopt the already sandboxed subprocess; status reads and decoding are
        # the production Bridge methods, not a hand-constructed status frame.
        bridge = Bridge(None, authorized, threading.Event(), timeout=5)
        bridge.process = proc
        assert bridge.list_devices() == []
        with Session(engine()) as db:
            assert publish_state(db, workspace, record['id'], record['version'], 'connected', 0)
            db.commit()
        wait_for(lambda: any(item.get('kind') == 'unauthorized' for item in events(name)))
        def observed_auth():
            observed = bridge.websocket_status()
            return observed if observed == ('disconnected', 'auth_401') else None
        observed = wait_for(observed_auth, timeout=20)

        with subscription_lease('dji-cloud2:' + workspace + ':' + record['id']) as lease:
            assert _report_websocket(lease, workspace, record['id'], record['version'], observed)
            with Session(engine()) as db:
                saved = db.get(Job, record['id'])
                assert saved.data['websocket_state'] == 'disconnected'
                assert saved.data['websocket_last_failure'] == 'auth_401'
                assert SYNTHETIC_SECRET not in json.dumps(saved.data)
            shown = client.get('/api/dji/cloud2/connections').json()['connections'][0]
            assert shown['websocket_status'] == 'disconnected' and shown['websocket_last_failure'] == 'auth_401'

            response = client.post('/api/dji/cloud2/connections/'+record['id']+'/revoke', json={
                'request_id': str(uuid4()), 'version': record['version'], 'confirm': True})
            assert response.status_code == 200
            assert not _report_websocket(lease, workspace, record['id'], record['version'], observed)
            with pytest.raises(BridgeRevoked): bridge.websocket_status()
            shown = client.get('/api/dji/cloud2/connections').json()['connections'][0]
            assert shown['status'] == 'revoked' and shown['websocket_status'] == 'unknown'
    finally:
        if sandbox.network_id:
            docker(['network', 'disconnect', '--force', sandbox.network_id, name], check=False)
        outcome = sandbox.stop()
        remove_owned_container(name, 'dji-cloud2-e2e-fixture', lease_id)
        if journal_id:
            with Session(engine()) as db:
                journal.mark_stop(db, workspace, journal_id, sandbox, outcome); db.commit()
        if not outcome.network_removed or not outcome.containers_stopped:
            raise AssertionError('test cleanup unverified; inspect only resources '+sandbox.network_name+' '+sandbox.guard_name+' '+sandbox.sdk_name)
