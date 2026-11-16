from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Membership, MqttGateway, MqttReceiverState, Workspace, now
from generic_agents.mqtt_reception import reception_view
from generic_agents.security import Principal
from tests.test_mqtt_inbox import gateway


def report(gateway, *, status='receiving', age=0, lease=True, **values):
    p, identifier, version = gateway
    timestamp = now()
    with Session(engine(), expire_on_commit=False) as db:
        source = db.get(MqttGateway, identifier)
        row = MqttReceiverState(workspace_id=p.workspace_id, gateway_id=identifier, gateway_version=version,
            config_hash=digest(source.data), status=status, lease_token='test-token' if lease else None,
            lease_until=timestamp+timedelta(seconds=15) if lease else None,
            heartbeat_at=timestamp-timedelta(seconds=age),
            data={'schema': 'mqtt-receiver-v1', 'attempt': 2, 'connected_at': timestamp.isoformat(),
                  'last_received_at': timestamp.isoformat(), 'error_code': None, **values})
        db.add(row); db.commit()
        return row.id


def read(gateway):
    with Session(engine()) as db:
        return reception_view(db, gateway[0], gateway[1])


def test_active_is_authorization_not_connection(gateway):
    value = read(gateway)
    assert value['authorization_state'] == 'active'
    assert value['state'] == 'waiting_receiver' and not value['heartbeat_fresh']
    assert value['last_received_at'] is None


def test_only_live_matching_lease_reports_receiving(gateway):
    report(gateway)
    value = read(gateway)
    assert value['state'] == 'receiving' and value['heartbeat_fresh']
    assert value['last_reported_at'] and value['heartbeat_ttl_seconds'] == 15
    with Session(engine()) as db:
        row = db.scalar(select(MqttReceiverState)); row.lease_until = now()-timedelta(seconds=1); db.commit()
    value = read(gateway)
    assert value['state'] == 'stale' and not value['heartbeat_fresh']
    assert value['last_received_at']  # Past receipts are not a live connection proof.


@pytest.mark.parametrize('age', [46, -30])
def test_old_or_future_heartbeat_is_unknown(gateway, age):
    report(gateway, age=age)
    assert read(gateway)['state'] == 'stale'


def test_current_pause_overrides_receiving_and_preserves_history(gateway):
    timestamp = now().isoformat()
    report(gateway, history=[{'at': timestamp, 'state': 'receiving', 'error_code': None,
                             'gateway_version': gateway[2]}])
    with Session(engine()) as db:
        source = db.get(MqttGateway, gateway[1]); source.status = 'paused'; source.version += 1; db.commit()
    value = read(gateway)
    assert value['state'] == 'paused' and not value['heartbeat_fresh']
    assert len(value['history']) == 1 and value['gateway_version'] == gateway[2]+1


def test_changed_config_and_version_never_reuse_previous_green(gateway):
    report(gateway)
    with Session(engine()) as db:
        source = db.get(MqttGateway, gateway[1]); source.version += 1; db.commit()
    value = read(gateway)
    assert value['state'] == 'waiting_receiver' and value['error_code'] == 'configuration_changed'
    assert not value['heartbeat_fresh']


def test_current_owner_revocation_overrides_receiver_state(gateway):
    report(gateway)
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.user_id == gateway[0].user_id))
        member.role = 'viewer'; db.commit()
    value = read(gateway)
    assert value['state'] == 'blocked' and value['error_code'] == 'permission_changed'


def test_server_allowlist_removal_blocks_health_immediately(gateway, monkeypatch):
    from generic_agents.config import settings
    report(gateway)
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', '')
    value = read(gateway)
    assert value['state'] == 'blocked' and value['error_code'] == 'broker_rejected'


def test_dto_limits_history_and_never_leaks_internal_fields(gateway):
    timestamp = now().isoformat()
    items = [{'at': timestamp, 'state': 'backoff', 'gateway_version': gateway[2],
              'error_code': 'mqtts://secret-user:secret-pass@private', 'token': 'secret-token'} for _ in range(30)]
    report(gateway, status='backoff', history=items, error_code='private driver exception',
           subscribed_topics=['private/sensors'], credential_ref='GA_MQTT_PRIVATE', attempt=True)
    value = read(gateway)
    assert len(value['history']) == 20 and value['attempts'] == 0
    assert value['error_code'] == 'receiver_unavailable'
    assert all(item['error_code'] == 'receiver_unavailable' for item in value['history'])
    assert 'secret' not in str(value) and 'GA_MQTT' not in str(value) and 'private' not in str(value)


def test_corrupt_report_data_is_safe_and_cannot_claim_live(gateway):
    identifier = report(gateway, history=[{'state': {}, 'at': []}], error_code={'private': 'value'})
    with Session(engine()) as db:
        row = db.get(MqttReceiverState, identifier); row.data = {**row.data, 'schema': 'invalid'}; db.commit()
    value = read(gateway)
    assert value['state'] == 'waiting_receiver' and not value['heartbeat_fresh'] and not value['history']


def test_health_scope_hides_other_workspaces(gateway):
    with Session(engine()) as db:
        workspace = Workspace(name='other'); db.add(workspace); db.flush()
        outsider = Principal(gateway[0].user_id, workspace.id, 'admin', 'test')
        with pytest.raises(HTTPException) as error:
            reception_view(db, outsider, gateway[1])
        assert error.value.status_code == 404


def test_health_get_is_read_only_and_not_cached(gateway, client):
    identifier = report(gateway)
    with Session(engine()) as db:
        before = db.get(MqttReceiverState, identifier)
        version, data = before.version, dict(before.data)
    response = client.get('/api/mqtt/gateways/'+gateway[1]+'/reception')
    assert response.status_code == 200 and 'no-store' in response.headers['cache-control']
    assert response.json()['state'] == 'receiving'
    with Session(engine()) as db:
        after = db.get(MqttReceiverState, identifier)
        assert after.version == version and after.data == data
