"""Isolated GUI broker credentials, workspace confinement and revoke fencing."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json,time
from uuid import uuid4
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Job,User,MqttGateway
from generic_agents.security import Principal
from generic_agents import managed_mqtt_grants as managed
from generic_agents.mqtt_inbox import create_gateway,set_status,capture,validate_configuration
from tests.test_mqtt_inbox import config,packet

if not any(getattr(r,'path',None)=='/api/mqtt/authorizations' for r in app.routes):app.include_router(managed.router)

@pytest.fixture
def vault(tmp_path,monkeypatch):
    path=tmp_path/'mqtt-vault.key';path.write_bytes(Fernet.generate_key());path.chmod(0o600)
    monkeypatch.setenv('GA_FILE_GATEWAY_KEY_FILE',str(path));monkeypatch.setenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','1');monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS','192.168.0.0/24')
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','mqtt://127.0.0.1:1883')
    return path

def test_ui_credentials_encrypted_real_authorization_and_revocation(client,account,vault):
    body={'request_id':str(uuid4()),'name':'测试服务','host':'192.168.0.10','port':8883,'username':'synthetic-user','password':'synthetic-pass'}
    response=client.post('/api/mqtt/authorizations',json=body);assert response.status_code==200,response.text
    saved=response.json();grant=saved['authorization'];assert saved['state']=='succeeded'
    assert 'synthetic-user' not in response.text and 'synthetic-pass' not in response.text
    assert client.post('/api/mqtt/authorizations',json=body).json()==saved
    assert client.post('/api/mqtt/authorizations',json={**body,'password':'changed'}).status_code==409
    public=client.get('/api/mqtt/authorizations').json();assert public['authorizations'][0]['id']==grant['id']
    with Session(engine(),expire_on_commit=False) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','test')
        row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==managed.KIND));raw=json.dumps(row.data)
        assert 'synthetic-pass' not in raw and 'synthetic-user' not in raw
        assert managed.credentials(p.workspace_id,grant['id'],db)==('synthetic-user','synthetic-pass')
        c=config();c.update(broker=grant['broker'],username_ref=grant['username_ref'],password_ref=grant['password_ref'])
        gateway=create_gateway(db,p,'测试传感器',c);set_status(db,p,gateway.id,gateway.version,'active');db.commit();identity=gateway.id;version=gateway.version
        capture(db,p,identity,version,'sensors/one',packet(),1)
        request={'request_id':str(uuid4()),'version':grant['version'],'enabled':False}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(client.post,'/api/mqtt/authorizations/'+grant['id']+'/status',json=request)
            time.sleep(.2);assert not future.done()
            db.commit();revoke=future.result(timeout=5)
        assert revoke.status_code==200,revoke.text
        assert client.get('/api/mqtt/authorizations/operations/'+request['request_id']).json()['state']=='succeeded'
        with pytest.raises(HTTPException):capture(db,p,identity,version,'sensors/one',packet(record_id='after-revoke'),1)
    restore=client.post('/api/mqtt/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':revoke.json()['authorization']['version'],'enabled':True})
    assert restore.status_code==200,restore.text

def test_invalid_network_cannot_create_and_key_mismatch_is_closed(client,account,vault):
    body={'request_id':str(uuid4()),'name':'测试服务','host':'8.8.8.8','port':8883,'username':'u','password':'p'}
    assert client.post('/api/mqtt/authorizations',json=body).status_code==422
    body['host']='192.168.0.12';grant=client.post('/api/mqtt/authorizations',json=body).json()['authorization']
    vault.write_bytes(Fernet.generate_key());vault.chmod(0o600)
    listing=client.get('/api/mqtt/authorizations').json();assert listing['key_ready'] is False
    with pytest.raises(HTTPException):managed.get_grant(account[2],grant['id'])
    other={**body,'request_id':str(uuid4())};assert client.post('/api/mqtt/authorizations',json=other).status_code==503

def test_managed_grant_does_not_cross_workspace(client,account,vault):
    body={'request_id':str(uuid4()),'name':'测试服务','host':'192.168.0.10','port':8883,'username':'u','password':'p'}
    grant=client.post('/api/mqtt/authorizations',json=body).json()['authorization']
    with pytest.raises(HTTPException):managed.get_grant(str(uuid4()),grant['id'])
    c=config();c.update(broker=grant['broker'],username_ref=grant['username_ref'],password_ref=grant['password_ref'])
    with pytest.raises(HTTPException):validate_configuration(c,workspace_id=str(uuid4()))

def test_recovery_fence_does_not_need_original_password(client,vault):
    rid=str(uuid4());r=client.post('/api/mqtt/authorizations/operations/'+rid+'/cancel');assert r.json()['state']=='cancelled'
    body={'request_id':rid,'name':'测试服务','host':'192.168.0.10','port':8883,'username':'u','password':'p'}
    assert client.post('/api/mqtt/authorizations',json=body).json()['state']=='cancelled'


def test_real_tls_private_broker_and_revocation(client,account,vault,tmp_path,monkeypatch):
    """Real MQTT/TLS with a synthetic CA in this test process only; no field source."""
    import os,socket,subprocess,ssl,threading
    import paho.mqtt.client as mqtt
    from generic_agents.mqtt_workspace_supervisor import WorkspaceMqttSupervisor
    from tests.test_mqtt_supervisor import wait_for,state_of
    host=subprocess.check_output(['ipconfig','getifaddr','en0'],text=True).strip()
    if not host.startswith('192.168.0.'):pytest.skip('isolated private test interface unavailable')
    cert=tmp_path/'ca.crt';key=tmp_path/'ca.key';userfile=tmp_path/'users'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(key),'-out',str(cert),'-subj','/CN=synthetic-mqtt','-addext','subjectAltName=IP:'+host],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    broker_python=Path(__file__).resolve().parents[1]/'.runtime/venvs/mqtt-broker/bin/python'
    password_hash=subprocess.check_output([str(broker_python),'-c','from pwdlib import PasswordHash;print(PasswordHash.recommended().hash("synthetic-pass"))'],text=True).strip()
    userfile.write_text('synthetic-user:'+password_hash+'\n');userfile.chmod(0o600)
    with socket.socket() as sock:sock.bind((host,0));port=sock.getsockname()[1]
    env={**os.environ,'TEST_BIND':host+':'+str(port),'TEST_CERT':str(cert),'TEST_KEY':str(key),'TEST_PASSWORD_FILE':str(userfile)}
    broker=subprocess.Popen([str(broker_python),str(Path(__file__).with_name('mqtt_tls_broker_fixture.py'))],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    supervisor=None;thread=None
    try:
        assert broker.stdout.readline().strip()=='READY'
        monkeypatch.setenv('SSL_CERT_FILE',str(cert))
        body={'request_id':str(uuid4()),'name':'合成TLS服务','host':host,'port':port,'username':'synthetic-user','password':'synthetic-pass'}
        response=client.post('/api/mqtt/authorizations',json=body);assert response.status_code==200,response.text;grant=response.json()['authorization']
        with Session(engine(),expire_on_commit=False) as db:
            user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','mqtt-tls-test')
            c=config();c.update(broker=grant['broker'],username_ref=grant['username_ref'],password_ref=grant['password_ref'])
            gateway=create_gateway(db,p,'测试TLS传感器',c);set_status(db,p,gateway.id,gateway.version,'active');db.commit();identity=gateway.id
        supervisor=WorkspaceMqttSupervisor(p.workspace_id,poll_seconds=.2,lease_seconds=5);supervisor.connect_timeout=2
        stop=threading.Event();thread=threading.Thread(target=supervisor.run,kwargs={'stop_requested':stop.is_set},daemon=True);thread.start()
        wait_for(lambda:state_of(identity) and state_of(identity)['status']=='receiving',timeout=15)
        sender=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='synthetic-publisher');sender.username_pw_set('synthetic-user','synthetic-pass');sender.tls_set_context(ssl.create_default_context());sender.connect(host,port);sender.loop_start()
        try:
            msg=sender.publish('sensors/one',packet(),qos=1);msg.wait_for_publish(timeout=5);assert msg.is_published()
            wait_for(lambda:state_of(identity) and state_of(identity)['data'].get('received_count',0)>=1,timeout=15)
            revoke=client.post('/api/mqtt/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':grant['version'],'enabled':False});assert revoke.status_code==200,revoke.text
            wait_for(lambda:state_of(identity) and state_of(identity)['status']=='blocked',timeout=15)
        finally:sender.disconnect();sender.loop_stop()
    finally:
        if thread:stop.set();thread.join(timeout=15);assert not thread.is_alive()
        broker.terminate()
        try:broker.wait(timeout=5)
        except subprocess.TimeoutExpired:broker.kill();broker.wait(timeout=3)


def test_tls_hostname_mismatch_is_not_bypassed(client,account,vault,tmp_path,monkeypatch):
    import os,socket,ssl,subprocess,threading
    from generic_agents.mqtt_subscriber import mqtt_client
    host=subprocess.check_output(['ipconfig','getifaddr','en0'],text=True).strip()
    if not host.startswith('192.168.0.'):pytest.skip('isolated private interface unavailable')
    cert=tmp_path/'mismatch.crt';key=tmp_path/'mismatch.key'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(key),'-out',str(cert),'-subj','/CN=synthetic-mqtt','-addext','subjectAltName=IP:192.168.0.99'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    monkeypatch.setenv('SSL_CERT_FILE',str(cert))
    server=socket.socket();server.bind((host,0));server.listen();port=server.getsockname()[1]
    server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_context.load_cert_chain(cert,key)
    def accept():
        try:
            raw,_=server.accept()
            with raw:
                try:server_context.wrap_socket(raw,server_side=True)
                except ssl.SSLError:pass
        finally:server.close()
    t=threading.Thread(target=accept,daemon=True);t.start()
    try:
        body={'request_id':str(uuid4()),'name':'TLS mismatch','host':host,'port':port,'username':'user','password':'password'}
        grant=client.post('/api/mqtt/authorizations',json=body).json()['authorization']
        cfg=config();cfg.update(broker=grant['broker'],username_ref=grant['username_ref'],password_ref=grant['password_ref'])
        device=mqtt_client('tls-mismatch',cfg,workspace_id=account[2]);assert device._ssl_context.check_hostname and device._ssl_context.verify_mode==ssl.CERT_REQUIRED
        with pytest.raises(ssl.SSLCertVerificationError):device.connect(host,port)
    finally:server.close();t.join(timeout=3)

from tests.test_mqtt_observations import prepared_mqtt
from tests.test_mqtt_binding_routes import create as create_route

def test_managed_broker_supports_object_binding_and_resume(client,prepared_mqtt,vault):
    original,obj,rule,p=prepared_mqtt
    response=client.post('/api/mqtt/authorizations',json={'request_id':str(uuid4()),'name':'managed','host':'192.168.0.10','port':8883,'username':'test','password':'test'})
    assert response.status_code==200,response.text;grant=response.json()['authorization']
    cfg={'broker':grant['broker'],'topics':{'sensor/one':'device-one'},'mapping':{'t':{'attribute':'temperature','unit':'C'}},'fields':{},'username_ref':grant['username_ref'],'password_ref':grant['password_ref']}
    updated=client.put('/api/mqtt/gateways/'+original['id'],json={'expected_version':original['version'],'name':'managed source','configuration':cfg})
    assert updated.status_code==200,updated.text
    activated=client.post('/api/mqtt/gateways/'+original['id']+'/status',json={'expected_version':updated.json()['version'],'status':'active'})
    assert activated.status_code==200,activated.text
    route,_=create_route(client,(activated.json(),obj,rule,p));url='/api/mqtt/routes/'+route['id']+'/status'
    paused=client.post(url,json={'expected_version':route['version'],'request_id':str(uuid4()),'status':'paused'});assert paused.status_code==200,paused.text
    resumed=client.post(url,json={'expected_version':paused.json()['version'],'request_id':str(uuid4()),'status':'active'});assert resumed.status_code==200,resumed.text
    revoked=client.post('/api/mqtt/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':grant['version'],'enabled':False});assert revoked.status_code==200
    paused=client.post(url,json={'expected_version':resumed.json()['version'],'request_id':str(uuid4()),'status':'paused'});assert paused.status_code==200
    rejected=client.post(url,json={'expected_version':paused.json()['version'],'request_id':str(uuid4()),'status':'active'});assert rejected.status_code==403


def test_legacy_cli_connection_and_revoke_are_serialized(client,account,vault,monkeypatch):
    from threading import Event
    from generic_agents import mqtt_subscriber
    body={'request_id':str(uuid4()),'name':'测试服务','host':'192.168.0.10','port':8883,'username':'u','password':'p'}
    grant=client.post('/api/mqtt/authorizations',json=body).json()['authorization']
    with Session(engine(),expire_on_commit=False) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','cli-test')
        c=config();c.update(broker=grant['broker'],username_ref=grant['username_ref'],password_ref=grant['password_ref'])
        gateway=create_gateway(db,p,'legacy cli',c);set_status(db,p,gateway.id,gateway.version,'active');db.commit();identity=gateway.id;version=gateway.version
    entered,release,stop=Event(),Event(),Event();connections=[]
    class BlockingClient:
        def connect(self,host,port,keepalive=30):
            connections.append((host,port));entered.set();assert release.wait(5);stop.set();return 0
        def disconnect(self):return 0
    monkeypatch.setattr(mqtt_subscriber,'mqtt_client',lambda *args,**kwargs:BlockingClient())
    reader=mqtt_subscriber.MqttSubscriber(p,identity,version)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running=pool.submit(reader.run,timeout_seconds=10,stop_requested=stop.is_set)
        assert entered.wait(5)
        revoke=pool.submit(client.post,'/api/mqtt/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':grant['version'],'enabled':False})
        time.sleep(.2);assert not revoke.done()
        release.set();result=running.result(timeout=5);response=revoke.result(timeout=5)
    assert response.status_code==200,response.text
    assert result['received']==0 and len(connections)==1
    with pytest.raises(HTTPException):mqtt_subscriber.MqttSubscriber(p,identity,version).run(timeout_seconds=2)
    assert len(connections)==1
