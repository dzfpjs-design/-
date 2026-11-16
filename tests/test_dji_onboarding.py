"""Synthetic Cloud API 1 handshake tests: no real credentials or hardware."""
import json
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
import pytest

from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents.dji_onboarding_api import router, KIND
from generic_agents.dji_onboarding_runtime import reply_once
from generic_agents.dji_onboarding_protocol import prepare_reply, validate_config
from tests.test_managed_mqtt_grants import vault

if not any(getattr(r, 'path', None) == '/api/dji/onboarding/configurations' for r in app.routes):
    app.include_router(router)


@pytest.fixture(autouse=True)
def controlled_testing(monkeypatch):
    monkeypatch.setenv('GA_DJI_ONBOARDING_EXPERIMENTAL', '1')


def config():
    return {'dock_sn':'SYNTHDOCK', 'aircraft_sn':'SYNTHAIR', 'organization_id':'synthetic-org',
            'organization_name':'模拟组织', 'dock_binding_code':'synthetic-dock-code',
            'aircraft_binding_code':'synthetic-air-code', 'app_id':'synthetic-app-id',
            'app_key':'synthetic-app-key', 'app_license':'synthetic-license', 'ntp_host':'ntp.example.test'}


def packet(method, data, tid=None):
    return json.dumps({'method':method, 'tid':tid or str(uuid4()), 'bid':str(uuid4()),
                       'gateway':'SYNTHDOCK', 'data':data}).encode()


def test_protocol_exact_topic_binding_and_no_false_success():
    cfg = validate_config(config()); topic = 'thing/product/SYNTHDOCK/requests'
    raw = packet('airport_bind_status', {'devices':[{'sn':'SYNTHDOCK'}, {'sn':'SYNTHAIR'}]})
    out = prepare_reply(topic, raw, cfg)[1]
    assert all(x['is_device_bind_organization'] is False for x in json.loads(out)['data']['output']['bind_status'])
    out = prepare_reply(topic, raw, cfg, bound_devices=['SYNTHDOCK'])[1]
    assert [x['is_device_bind_organization'] for x in json.loads(out)['data']['output']['bind_status']] == [True, False]
    with pytest.raises(ValueError): prepare_reply('thing/product/OTHER/requests', raw, cfg)
    with pytest.raises(ValueError): prepare_reply(topic, packet('airport_bind_status', {'devices':[{'sn':'OTHER'}]}), cfg)
    with pytest.raises(ValueError): prepare_reply(topic, packet('airport_organization_get', {'organization_id':'synthetic-org', 'device_binding_code':'wrong'}), cfg)
    with pytest.raises(ValueError): prepare_reply(topic, b'{"method":"config","method":"config"}', cfg)


def test_experimental_onboarding_is_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv('GA_DJI_ONBOARDING_EXPERIMENTAL', raising=False)
    listing=client.get('/api/dji/onboarding/configurations')
    assert listing.status_code==200 and listing.json()['experimental_enabled'] is False
    assert client.post('/api/dji/onboarding/configurations', json={}).status_code==409


def test_config_saved_encrypted_then_handshake_and_revocation(client, account, vault):
    grant_body = {'request_id':str(uuid4()), 'name':'模拟服务', 'host':'192.168.0.10', 'port':8883,
                  'username':'synthetic-mqtt-user', 'password':'synthetic-mqtt-password'}
    response = client.post('/api/mqtt/authorizations', json=grant_body); assert response.status_code == 200
    grant = response.json()['authorization']
    body = {'request_id':str(uuid4()), 'broker_authorization_id':grant['id'], 'config':config(),
            'confirm':True, 'broker_acl_confirmed':True}
    assert client.post('/api/dji/onboarding/configurations', json={**body, 'broker_acl_confirmed':False}).status_code == 422
    response = client.post('/api/dji/onboarding/configurations', json=body); assert response.status_code == 200, response.text
    created = response.json(); item = created['configuration']; assert item['enabled'] is False
    for secret in ('synthetic-license', 'synthetic-app-key', 'synthetic-dock-code'):
        assert secret not in response.text
    assert client.post('/api/dji/onboarding/configurations', json=body).json() == created
    with Session(engine()) as db:
        row = db.scalar(select(Job).where(Job.id == item['id'], Job.kind == KIND))
        saved = json.dumps(row.data)
        assert all(value not in saved for value in ('synthetic-license', 'synthetic-app-key', 'synthetic-dock-code'))
    enabled = client.post('/api/dji/onboarding/configurations/'+item['id']+'/status', json={
        'request_id':str(uuid4()), 'version':item['version'], 'enabled':True, 'confirm':True})
    assert enabled.status_code == 200
    sent = []
    def publish(topic, payload): sent.append((topic, payload))
    raw = packet('config', {'config_type':'json', 'config_scope':'product'})
    assert reply_once(account[2], item['id'], 'thing/product/SYNTHDOCK/requests', raw, publish)
    assert json.loads(sent[0][1])['data']['app_key'] == 'synthetic-app-key'
    topo = packet('update_topo', {'device_secret':'synthetic-device-secret', 'nonce':'synthetic-nonce',
        'sub_devices':[{'sn':'SYNTHAIR', 'device_secret':'synthetic-air-secret', 'nonce':'synthetic-air-nonce'}]})
    assert reply_once(account[2], item['id'], 'sys/product/SYNTHDOCK/status', topo, publish)
    with Session(engine()) as db:
        rows = db.scalars(select(Job).where(Job.workspace_id == account[2], Job.kind == 'dji_onboarding_reply_v1')).all()
        raw_records = json.dumps([r.data for r in rows])
        assert all(s not in raw_records for s in ('synthetic-device-secret', 'synthetic-nonce', 'synthetic-app-key'))
    revoked = client.post('/api/mqtt/authorizations/'+grant['id']+'/status', json={
        'request_id':str(uuid4()), 'version':grant['version'], 'enabled':False})
    assert revoked.status_code == 200
    with pytest.raises(HTTPException): reply_once(account[2], item['id'], 'thing/product/SYNTHDOCK/requests', raw, publish)
    assert len(sent) == 2


@pytest.mark.parametrize('reject_reply,early_mode',[(False, ''), (True, ''), (False, 'early'), (False, 'early_fail'), (False, 'overflow')])
def test_real_mqtt5_tls_synthetic_handshake(client, account, vault, tmp_path, monkeypatch, reject_reply, early_mode):
    """Real TLS and MQTT 5 framing against a synthetic peer, not DJI's simulator."""
    import os, socket, subprocess, threading, time
    from pathlib import Path
    from generic_agents.dji_onboarding_runtime import start_workspace_onboarding
    host = subprocess.check_output(['ipconfig', 'getifaddr', 'en0'], text=True).strip()
    if not host.startswith('192.168.0.'): pytest.skip('没有隔离私网测试接口')
    cert = tmp_path/'ca.crt'; private = tmp_path/'ca.key'; result = tmp_path/'result.json'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
        '-keyout',str(private),'-out',str(cert),'-subj','/CN=synthetic-dji-mqtt',
        '-addext','subjectAltName=IP:'+host], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with socket.socket() as probe:
        probe.bind((host,0)); port = probe.getsockname()[1]
    env={**os.environ,'DJI_TEST_HOST':host,'DJI_TEST_PORT':str(port),'DJI_TEST_CERT':str(cert),
         'DJI_TEST_KEY':str(private),'DJI_TEST_RESULT':str(result)}
    if reject_reply:env['DJI_TEST_REJECT_REPLY']='1'
    if early_mode:env['DJI_TEST_EARLY_SUBACK']=early_mode
    broker=subprocess.Popen([str(Path(__file__).resolve().parents[1]/'.venv/bin/python'),
        str(Path(__file__).with_name('dji_mqtt5_synthetic_broker.py'))], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stop=threading.Event(); worker=None
    try:
        assert broker.stdout.readline().strip() == 'READY'
        monkeypatch.setenv('SSL_CERT_FILE',str(cert))
        grant = client.post('/api/mqtt/authorizations', json={'request_id':str(uuid4()),'name':'隔离服务',
            'host':host,'port':port,'username':'synthetic-mqtt-user','password':'synthetic-mqtt-password'}).json()['authorization']
        body={'request_id':str(uuid4()),'broker_authorization_id':grant['id'],'config':config(),
              'confirm':True,'broker_acl_confirmed':True}
        created=client.post('/api/dji/onboarding/configurations',json=body).json()['configuration']
        response=client.post('/api/dji/onboarding/configurations/'+created['id']+'/status',json={
            'request_id':str(uuid4()),'version':created['version'],'enabled':True,'confirm':True})
        assert response.status_code == 200
        worker=start_workspace_onboarding(account[2],stop)
        deadline=time.monotonic()+30
        while not result.exists() and broker.poll() is None and time.monotonic()<deadline: time.sleep(.1)
        assert result.exists(), '合成 MQTT 5 服务未收到完整握手'
        if reject_reply:
            assert json.loads(result.read_text()) == {'rejected':True,'request_acknowledged':False}
        else:
            assert json.loads(result.read_text()) == {'completed':True,'methods':6}
        with Session(engine()) as db:
            row=db.scalar(select(Job).where(Job.id==created['id']))
            if reject_reply:
                assert row.data['bound_devices']==[] and row.data['last_stage'] == 'update_topo'
                events=db.scalars(select(Job).where(Job.kind=='dji_onboarding_reply_v1')).all()
                assert [event.data['method'] for event in events]==['update_topo']
            else:
                assert row.data['bound_devices']==['SYNTHAIR','SYNTHDOCK']
                assert row.data['last_stage']=='airport_bind_status'
            assert 'synthetic-license' not in json.dumps(row.data)
    finally:
        stop.set()
        if worker: worker.join(timeout=20)
        if broker.poll() is None: broker.terminate()
        broker.wait(timeout=5)


def test_inflight_reply_and_revoke_are_serialized(client, account, vault):
    import threading, time
    from concurrent.futures import ThreadPoolExecutor
    grant = client.post('/api/mqtt/authorizations', json={'request_id':str(uuid4()),'name':'隔离服务',
        'host':'192.168.0.10','port':8883,'username':'synthetic-user','password':'synthetic-password'}).json()['authorization']
    body={'request_id':str(uuid4()),'broker_authorization_id':grant['id'],'config':config(),
          'confirm':True,'broker_acl_confirmed':True}
    item=client.post('/api/dji/onboarding/configurations',json=body).json()['configuration']
    assert client.post('/api/dji/onboarding/configurations/'+item['id']+'/status',json={
        'request_id':str(uuid4()),'version':item['version'],'enabled':True,'confirm':True}).status_code==200
    entered=threading.Event();release=threading.Event()
    def publish(_topic,_payload):entered.set();assert release.wait(10)
    raw=packet('config',{'config_type':'json','config_scope':'product'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        work=pool.submit(reply_once,account[2],item['id'],'thing/product/SYNTHDOCK/requests',raw,publish)
        assert entered.wait(5)
        revoke=pool.submit(client.post,'/api/mqtt/authorizations/'+grant['id']+'/status',json={
            'request_id':str(uuid4()),'version':grant['version'],'enabled':False})
        time.sleep(.2);assert not revoke.done()
        release.set();assert work.result(timeout=5);assert revoke.result(timeout=5).status_code==200
    with pytest.raises(HTTPException): reply_once(account[2],item['id'],'thing/product/SYNTHDOCK/requests',raw,publish)


def test_missing_key_marker_with_onboarding_ciphertext_cannot_be_reinitialized(client, account, vault):
    from cryptography.fernet import Fernet
    from generic_agents.managed_file_grants import KEY_MARKER
    grant = client.post('/api/mqtt/authorizations', json={'request_id':str(uuid4()),'name':'隔离服务',
        'host':'192.168.0.10','port':8883,'username':'synthetic-user','password':'synthetic-password'}).json()['authorization']
    body={'request_id':str(uuid4()),'broker_authorization_id':grant['id'],'config':config(),
          'confirm':True,'broker_acl_confirmed':True}
    assert client.post('/api/dji/onboarding/configurations',json=body).status_code==200
    with Session(engine()) as db:
        marker=db.scalar(select(Job).where(Job.kind==KEY_MARKER));db.delete(marker)
        # Simulate a restore where only onboarding ciphertext remains.
        rows=db.scalars(select(Job).where(Job.kind=='managed_mqtt_grant_v1')).all()
        for row in rows:db.delete(row)
        db.commit()
    vault.write_bytes(Fernet.generate_key());vault.chmod(0o600)
    response=client.post('/api/mqtt/authorizations',json={'request_id':str(uuid4()),'name':'新隔离服务',
        'host':'192.168.0.10','port':8883,'username':'synthetic-user','password':'synthetic-password'})
    assert response.status_code==503
