import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import User, Membership, MqttReceipt, Evidence, CloudCall, Observation
from generic_agents.security import Principal
from generic_agents.evidence import evidence_bytes
from generic_agents.mqtt_inbox import create_gateway, set_status, capture, broker_address
from generic_agents.mqtt_subscriber import capture_and_ack, subscription_lease


def packet(**changes):
    value = {'record_id': 'r1', 'device_id': 'sensor-1', 'observed_at': '2026-10-10T23:00:00Z',
             'values': {'t': 39.5}, 'units': {'t': 'C'}}
    value.update(changes)
    return json.dumps(value, ensure_ascii=False).encode()


def config(origin='mqtt://127.0.0.1:1883'):
    return {'broker': origin, 'topics': {'sensors/one': 'sensor-1'},
            'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}},
            'fields': {}, 'username_ref': '', 'password_ref': ''}


@pytest.fixture
def gateway(account, monkeypatch):
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', 'mqtt://127.0.0.1:1883')
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'mqtt-test')
        row = create_gateway(db, p, '传感接入', config())
        set_status(db, p, row.id, row.version, 'active'); db.commit()
        return p, row.id, row.version


def save(gateway, raw, **metadata):
    p, identity, version = gateway
    with Session(engine(), expire_on_commit=False) as db:
        row = capture(db, p, identity, version, metadata.pop('topic', 'sensors/one'), raw,
                      metadata.pop('qos', 1), **metadata)
        db.commit()
        return row


def test_exact_original_survives_new_session_and_duplicate_delivery(gateway):
    row = save(gateway, packet()); again = save(gateway, packet(), duplicate=True)
    assert row.id == again.id and again.data['deliveries'] == 2
    assert row.status == 'awaiting_object_binding' and row.data['decoded']['business_day'] == '2026-10-11'
    with Session(engine()) as db:
        saved = db.get(MqttReceipt, row.id)
        original, content = evidence_bytes(db, gateway[0], saved.evidence_id)
        assert content == packet() and original.sha256 == saved.input_sha256
        assert db.scalar(select(func.count()).select_from(Evidence)) == 1
        assert not db.scalars(select(CloudCall)).all() and not db.scalars(select(Observation)).all()


@pytest.mark.parametrize('raw,issue', [(b'\xff', 'unreadable_packet'), (b'', 'unreadable_packet'),
    (packet(observed_at=None), 'missing_capture_time'), (packet(units={'t': 'F'}), 'unit_conflict:temperature'),
    (packet(device_id='other'), 'device_topic_binding_mismatch')])
def test_bad_packets_are_durable_quarantine_not_false_state(gateway, raw, issue):
    row = save(gateway, raw)
    assert row.status == 'quarantined' and issue in row.data['issues']
    with Session(engine()) as db:
        assert evidence_bytes(db, gateway[0], row.evidence_id)[1] == raw
        assert not db.scalars(select(Observation)).all()


def test_same_record_different_original_retains_both_and_quarantines_both(gateway):
    first = save(gateway, packet()); second = save(gateway, packet(values={'t': 40}))
    assert first.id != second.id and second.status == 'quarantined'
    with Session(engine()) as db:
        one = db.get(MqttReceipt, first.id)
        assert one.status == 'quarantined' and 'record_content_conflict' in one.data['issues']
        assert evidence_bytes(db, gateway[0], one.evidence_id)[1] == packet()
        assert evidence_bytes(db, gateway[0], second.evidence_id)[1] == packet(values={'t': 40})


def test_spoofed_device_on_other_topic_does_not_quarantine_valid_record(gateway):
    first = save(gateway, packet())
    p, identity, version = gateway
    with Session(engine()) as db:
        from generic_agents.models import MqttGateway
        gw = db.get(MqttGateway, identity)
        gw.data = {**gw.data, 'topics': {**gw.data['topics'], 'sensors/two': 'sensor-2'}}
        gw.version += 1; version = gw.version; db.commit()
    bad = save((p, identity, version), packet(values={'t': 40}), topic='sensors/two')
    assert bad.status == 'quarantined'
    with Session(engine()) as db: assert db.get(MqttReceipt, first.id).status == 'awaiting_object_binding'


def test_earlier_spoof_cannot_poison_later_legitimate_packet(gateway):
    p,identity,version=gateway
    with Session(engine()) as db:
        from generic_agents.models import MqttGateway
        gw=db.get(MqttGateway,identity)
        gw.data={**gw.data,'topics':{**gw.data['topics'],'sensors/two':'sensor-2'}}
        gw.version+=1;version=gw.version;db.commit()
    fixture=(p,identity,version)
    spoof=save(fixture,packet(values={'t':1}),topic='sensors/two')
    assert 'device_topic_binding_mismatch' in spoof.data['issues']
    valid=save(fixture,packet())
    assert valid.status=='awaiting_object_binding' and not valid.data['issues']


@pytest.mark.parametrize('metadata,issue', [({'retain': True}, 'retained_packet_needs_time_review'),
                                           ({'qos': 0}, 'qos_not_verified'), ({'qos': 2}, 'qos_not_verified')])
def test_unverified_protocol_semantics_are_not_fresh_observations(gateway, metadata, issue):
    row = save(gateway, packet(), **metadata)
    assert row.status == 'quarantined' and issue in row.data['issues']


def test_puback_happens_only_after_another_connection_can_read_committed_original(gateway):
    p, identity, version = gateway; acknowledged = []
    def acknowledge(mid, qos):
        with Session(engine()) as independent:
            saved = independent.scalar(select(MqttReceipt).where(MqttReceipt.gateway_id == identity))
            assert saved and evidence_bytes(independent, p, saved.evidence_id)[1] == packet()
        acknowledged.append((mid, qos)); return 0
    message = SimpleNamespace(topic='sensors/one', payload=packet(), qos=1, retain=False, dup=False, mid=7)
    capture_and_ack(p, identity, version, message, acknowledge)
    assert acknowledged == [(7, 1)]


def test_database_commit_failure_never_acknowledges(gateway):
    p, identity, version = gateway; acknowledged = []
    class FailSession(Session):
        def commit(self): raise RuntimeError('isolated transaction commit failure')
    message = SimpleNamespace(topic='sensors/one', payload=packet(), qos=1, retain=False, dup=False, mid=7)
    with pytest.raises(RuntimeError):
        capture_and_ack(p, identity, version, message, lambda *args: acknowledged.append(args),
                        db_factory=lambda: FailSession(engine()))
    with Session(engine()) as db: assert not db.scalars(select(MqttReceipt)).all()
    assert acknowledged == []


def test_ack_failure_keeps_original_and_redelivery_is_idempotent(gateway):
    p, identity, version = gateway
    message = SimpleNamespace(topic='sensors/one', payload=packet(), qos=1, retain=False, dup=False, mid=7)
    with pytest.raises(RuntimeError): capture_and_ack(p, identity, version, message, lambda *args: 4)
    row = save(gateway, packet())
    assert row.data['deliveries'] == 2


def test_pause_role_change_or_foreign_workspace_blocks_new_receive(gateway):
    p, identity, version = gateway
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.user_id == p.user_id))
        member.role = 'viewer'; db.commit()
    with pytest.raises(HTTPException) as error: save(gateway, packet())
    assert error.value.status_code == 403
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.user_id == p.user_id)); member.role = 'admin'
        set_status(db, p, identity, version, 'paused'); db.commit()
    with pytest.raises(HTTPException) as error: save(gateway, packet())
    assert error.value.status_code == 409
    with Session(engine()) as db:
        foreign = Principal(p.user_id, 'not-this-workspace', 'admin', 'mqtt-test')
        with pytest.raises(HTTPException): capture(db, foreign, identity, version, 'sensors/one', packet(), 1)


def test_subscription_leader_cannot_take_over_existing_client(gateway):
    with subscription_lease(gateway[1]):
        with pytest.raises(RuntimeError):
            with subscription_lease(gateway[1]): pass
    with subscription_lease(gateway[1]): pass


@pytest.mark.parametrize('origin', ['mqtt://10.0.0.1:1883', 'mqtt://127.0.0.1:1884',
    'mqtt://name:password@127.0.0.1:1883', 'mqtt://127.0.0.1:1883/', 'mqtt://127.0.0.1:1883?secret=1'])
def test_unapproved_plain_remote_or_embedded_credentials_rejected(gateway, origin):
    with pytest.raises(HTTPException): broker_address(origin)


def test_bad_topic_unknown_secret_namespace_and_mapping_are_rejected(gateway):
    p, _, _ = gateway
    for changes in ({'topics': {'#': 'one'}}, {'username_ref': 'DEEPSEEK_API_KEY'},
                    {'mapping': {'t': {'attribute': 'temperature', 'unit': 'C', 'scale': .1}}}):
        with Session(engine()) as db:
            with pytest.raises(HTTPException): create_gateway(db, p, 'bad', {**config(), **changes})


def test_oversize_unauthorized_topic_and_corrupt_original_do_not_ack(gateway):
    with pytest.raises(HTTPException): save(gateway, b'x' * 65537)
    with pytest.raises(HTTPException): save(gateway, packet(), topic='sensors/not-confirmed')
    row = save(gateway, packet())
    with Session(engine()) as db:
        original = db.get(Evidence, row.evidence_id)
        (settings().storage_dir / original.storage_key).write_bytes(b'broken')
    with pytest.raises(HTTPException): save(gateway, packet())


def test_corrupted_preexisting_blob_cannot_be_acknowledged_as_a_new_receipt(gateway):
    from hashlib import sha256
    path = settings().storage_dir / gateway[0].workspace_id / (sha256(packet()).hexdigest() + '.bin')
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'not the original')
    with pytest.raises(HTTPException): save(gateway, packet())
    with Session(engine()) as db: assert not db.scalars(select(MqttReceipt)).all()
