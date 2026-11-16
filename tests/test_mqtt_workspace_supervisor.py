"""Workspace-limited background receiver and sensitive DJI topic protection."""
from copy import deepcopy
from threading import Event
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Evidence,Membership,MqttGateway,MqttReceiverState,User,Workspace
from generic_agents.mqtt_inbox import capture,create_gateway,set_status,validate_configuration
from generic_agents.mqtt_workspace_supervisor import WorkspaceMqttSupervisor,start_workspace_mqtt
from generic_agents.security import Principal
from tests.test_mqtt_inbox import config,gateway,packet
from tests.test_mqtt_supervisor import Clients,state_of,wait_for


def test_workspace_supervisor_does_not_activate_other_workspaces(gateway):
    principal,identity,_=gateway
    with Session(engine(),expire_on_commit=False) as db:
        workspace=Workspace(name='other-workspace');user=User(username='other_'+uuid4().hex,password_hash='unused')
        db.add_all([workspace,user]);db.flush();db.add(Membership(user_id=user.id,workspace_id=workspace.id,role='admin'))
        other=Principal(user.id,workspace.id,'admin','mqtt-test')
        row=create_gateway(db,other,'other',config());set_status(db,other,row.id,row.version,'active');db.commit();other_id=row.id
    clients=Clients();supervisor=WorkspaceMqttSupervisor(principal.workspace_id,max_receivers=2,lease_seconds=5,client_factory=clients)
    try:
        assert supervisor.reconcile()==1
        wait_for(lambda: state_of(identity) and state_of(identity)['status']=='receiving')
        assert not clients.for_gateway(other_id)
        assert state_of(other_id) is None
        receiver=clients.for_gateway(identity)[0];receiver.send();wait_for(lambda: len(receiver.acks)==1)
    finally:
        supervisor.close()
    assert state_of(identity)['status']=='stopped' and state_of(other_id) is None


def test_scope_must_be_explicit_canonical_uuid():
    for invalid in ('',None,'not-a-uuid',str(uuid4()).upper()):
        with pytest.raises(ValueError):WorkspaceMqttSupervisor(invalid)


@pytest.mark.parametrize('topic',['sys/product/abc123/status','thing/product/abc123/requests','thing/product/abc123/requests_reply','thing/product/abc123/state'])
def test_sensitive_dji_topics_rejected_before_persisting(gateway,topic):
    p,identity,version=gateway
    with Session(engine(),expire_on_commit=False) as db:
        row=db.get(MqttGateway,identity);row.data={**row.data,'topics':{topic:'sensor-1'}};db.commit()
        with pytest.raises(HTTPException) as error:
            capture(db,p,identity,version,topic,b'{"device_secret":"synthetic-secret"}',1)
        assert error.value.status_code==422
        db.rollback()
        assert db.scalar(select(func.count()).select_from(Evidence))==0


def test_dji_osd_and_ordinary_topics_remain_supported(monkeypatch):
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','mqtt://127.0.0.1:1883')
    for topic in ('thing/product/abc123/osd','thing/product/abc123/events','sensors/one'):
        value=deepcopy(config());value['topics']={topic:'sensor-1'}
        assert validate_configuration(value)['topics']=={topic:'sensor-1'}


def test_thread_stops_without_process_signal(monkeypatch):
    from generic_agents import mqtt_workspace_supervisor as module
    entered=Event();left=Event()
    class Probe:
        def __init__(self,workspace):self.workspace=workspace
        def run(self,stop_requested):
            entered.set()
            while not stop_requested():left.wait(.01)
            left.set()
    monkeypatch.setattr(module,'WorkspaceMqttSupervisor',Probe)
    stop=Event();thread=start_workspace_mqtt(str(uuid4()),stop)
    assert entered.wait(1);stop.set();thread.join(timeout=2)
    assert not thread.is_alive() and left.is_set()

# Optional real-protocol check: isolated PostgreSQL, actual loopback MQTT broker,
# workspace-scoped supervisor, and a synthetic sensor publisher.
from tests.test_mqtt_supervisor_protocol import protocol_broker, publish, packet as real_packet, receipt as real_receipt

@pytest.mark.skipif(__import__('os').getenv('GA_MQTT_SUPERVISOR_REAL')!='1',reason='requires isolated real loopback broker')
def test_real_broker_workspace_scoped_and_stoppable(account,protocol_broker):
    from threading import Thread
    with Session(engine(),expire_on_commit=False) as db:
        user=db.scalar(select(User).where(User.username==account[0]));principal=Principal(user.id,account[2],'admin','mqtt-real-test')
        value=config(protocol_broker.origin);row=create_gateway(db,principal,'synthetic',value);set_status(db,principal,row.id,row.version,'active');db.commit();identity=row.id
    supervisor=WorkspaceMqttSupervisor(principal.workspace_id,poll_seconds=.2,lease_seconds=5);supervisor.connect_timeout=1
    stop=Event();thread=Thread(target=supervisor.run,kwargs={'stop_requested':stop.is_set},daemon=True);thread.start()
    try:
        wait_for(lambda:state_of(identity) and state_of(identity)['status']=='receiving',timeout=20)
        raw=real_packet('scope-'+uuid4().hex);publish(protocol_broker,[('sensors/one',raw)])
        wait_for(lambda:real_receipt(identity,raw),timeout=20)
    finally:
        stop.set();thread.join(timeout=15)
    assert not thread.is_alive()
    assert state_of(identity)['status']=='stopped'


@pytest.mark.parametrize('raw',[
    b'{"data":{"wireless_link_topo":{"secret_code":[1,2,3]}}}',
    b'{"data":{"nested":[{"appKey":"sensitive"}]}}',
    b'{"data":{"file_url":"https://example.invalid/file?auth=sensitive"}}',
    b'{"data":{"nonce":"sensitive"}}',
    b'{"data":{},"data":{"password":"sensitive"}}',
    b'not-json',
])
def test_dji_osd_sensitive_or_invalid_is_not_saved(gateway,raw):
    p,identity,version=gateway;topic='thing/product/abc123/osd'
    with Session(engine(),expire_on_commit=False) as db:
        row=db.get(MqttGateway,identity);row.data={**row.data,'topics':{topic:'sensor-1'}};db.commit()
        with pytest.raises(HTTPException) as error:capture(db,p,identity,version,topic,raw,1)
        assert error.value.status_code==422 and 'sensitive' not in str(error.value.detail)
        db.rollback();assert db.scalar(select(func.count()).select_from(Evidence))==0


def test_dji_safe_osd_remains_verbatim(gateway):
    p,identity,version=gateway;topic='thing/product/abc123/osd';raw=b'{"timestamp":1700000000000,"data":{"longitude":117.3,"latitude":24.5}}'
    with Session(engine(),expire_on_commit=False) as db:
        row=db.get(MqttGateway,identity);row.data={**row.data,'topics':{topic:'sensor-1'}};db.commit()
        saved=capture(db,p,identity,version,topic,raw,1);db.commit()
        from generic_agents.evidence import evidence_bytes
        assert evidence_bytes(db,p,saved.evidence_id)[1]==raw


def test_sensitive_osd_supervisor_reports_reason_without_recording_original(gateway):
    p,identity,version=gateway;topic='thing/product/abc123/osd'
    with Session(engine(),expire_on_commit=False) as db:
        row=db.get(MqttGateway,identity);row.data={**row.data,'topics':{topic:'sensor-1'}};db.commit()
    clients=Clients();supervisor=WorkspaceMqttSupervisor(p.workspace_id,max_receivers=1,lease_seconds=5,client_factory=clients)
    try:
        assert supervisor.reconcile()==1
        wait_for(lambda:state_of(identity) and state_of(identity)['status']=='receiving')
        client=clients.for_gateway(identity)[0];client.send(topic=topic,raw=b'{"data":{"password":"synthetic-secret"}}')
        wait_for(lambda:state_of(identity) and state_of(identity)['data'].get('error_code')=='packet_rejected')
        with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(Evidence))==0
        assert not client.acks
    finally:supervisor.close()
