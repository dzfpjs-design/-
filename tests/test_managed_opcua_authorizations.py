"""Real encrypted test server plus graphical certificate authorization."""
import base64,json,threading,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job,Membership,User
from tests.test_opcua_secure_transport import secure_server

@pytest.fixture
def configured(account,secure_server,monkeypatch,tmp_path):
    monkeypatch.setenv('GA_MANAGED_OPCUA_ENABLED','1');monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS','192.168.0.0/16');monkeypatch.setenv('GA_OPCUA_READ_GRANTS','[]')
    key=tmp_path/'vault.key';key.write_bytes(Fernet.generate_key());key.chmod(0o600);monkeypatch.setenv('GA_OPCUA_VAULT_KEY_FILE',str(key))
    entry=secure_server
    body={'request_id':str(uuid4()),'name':'模拟节点','host':entry['host'],'port':entry['port'],'path':entry['path'],'node_id':entry['node_id']}
    for field in ('server_certificate','client_certificate','client_private_key'):body[field]=base64.b64encode(Path(entry[field]).read_bytes()).decode()
    return body,key


def test_gui_authorization_encrypts_and_reads_real_simulator(client,account,configured):
    body,key=configured;response=client.post('/api/opcua/authorizations',json=body);assert response.status_code==200,response.text
    result=response.json()['result'];assert body['client_private_key'] not in response.text
    same=client.post('/api/opcua/authorizations',json=body);assert same.json()['result']==result
    with Session(engine()) as db:
        row=db.scalar(select(Job).where(Job.workspace_id==account[2],Job.kind=='managed_opcua_grant_v1'));saved=json.dumps(row.data)
        assert body['client_private_key'] not in saved and 'PRIVATE KEY' not in saved and row.data['ciphertext']
    grants=client.get('/api/opcua/grants').json()['grants'];assert grants[0]['id']==result['id']
    conn=client.post('/api/opcua/connections',json={'request_id':str(uuid4()),'name':'模拟测点','grant_id':result['id'],'unit_label':'℃'});assert conn.status_code==200,conn.text
    identity=conn.json()['result']['connection_id'];reading=client.post('/api/opcua/connections/'+identity+'/readings',json={'request_id':str(uuid4()),'version':1});assert reading.status_code==200,reading.text
    assert reading.json()['result']['quality']=='good' and reading.json()['result']['value']==23.5
    backup=key.with_suffix('.bak');key.rename(backup)
    try:
        assert client.get('/api/opcua/grants').json()['grants']==[]
        assert client.get('/api/opcua/authorizations').json()['vault_ready'] is False
    finally:backup.rename(key)
    assert client.get('/api/opcua/grants').status_code==200
    change={'request_id':str(uuid4()),'version':result['version'],'enabled':False}
    assert client.post('/api/opcua/authorizations/'+result['id']+'/status',json=change).status_code==200
    assert client.get('/api/opcua/grants').json()['grants']==[]


def test_bad_secret_never_echoed_and_role_loss_can_fence(client,account,configured):
    body,key=configured;secret='UNIQUE_PRIVATE_SENTINEL_NOT_FOR_OUTPUT'
    bad=client.post('/api/opcua/authorizations',json={**body,'client_private_key':secret*3000});assert bad.status_code in (413,422) and secret not in bad.text
    request=body['request_id']
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==account[2]));member.role='viewer';db.commit()
    resolved=client.post('/api/opcua/authorizations/operations/'+request+'/resolve',json={'kind':'create'});assert resolved.status_code==200 and resolved.json()['state']=='cancelled'
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='admin';db.commit()
    retry=client.post('/api/opcua/authorizations',json=body);assert retry.status_code==200 and retry.json()['state']=='cancelled'
    assert client.get('/api/opcua/grants').json()['grants']==[]


def test_revoke_serializes_with_inflight_read(client,configured,monkeypatch):
    import generic_agents.opcua_gateway_api as api
    body,_=configured;grant=client.post('/api/opcua/authorizations',json=body).json()['result']
    connection=client.post('/api/opcua/connections',json={'request_id':str(uuid4()),'name':'模拟测点','grant_id':grant['id'],'unit_label':'℃'}).json()['result']['connection_id']
    entered=threading.Event();release=threading.Event();original=api.read_value
    def slow(value):entered.set();assert release.wait(8);return original(value)
    monkeypatch.setattr(api,'read_value',slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(client.post,'/api/opcua/connections/'+connection+'/readings',json={'request_id':str(uuid4()),'version':1});assert entered.wait(6)
        revoke=pool.submit(client.post,'/api/opcua/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':grant['version'],'enabled':False});time.sleep(.2);assert not revoke.done();release.set()
        assert future.result(timeout=15).status_code==200 and revoke.result(timeout=15).status_code==200
    assert not client.get('/api/opcua/grants').json()['grants']


def test_invalid_one_does_not_disable_other_authorized_device(client,account,configured):
    body,_=configured;first=client.post('/api/opcua/authorizations',json=body).json()['result']
    other={**body,'request_id':str(uuid4()),'name':'另一测点','node_id':body['node_id']+'-other'}
    second=client.post('/api/opcua/authorizations',json=other).json()['result']
    with Session(engine()) as db:
        row=db.scalar(select(Job).where(Job.workspace_id==account[2],Job.idempotency_key=='managed-opcua-grant:'+second['id']));row.data={**row.data,'ciphertext':'corrupted'};db.commit()
    response=client.get('/api/opcua/grants');assert response.status_code==200
    assert [x['id'] for x in response.json()['grants']]==[first['id']]
    rows=client.get('/api/opcua/authorizations').json()['authorizations'];broken=next(x for x in rows if x['id']==second['id'])
    assert not broken['available'] and '不可用' in broken['reason']


def test_disabled_self_service_does_not_revive_identical_legacy_authorization(client,account,configured,secure_server,monkeypatch):
    body,_=configured;grant=client.post('/api/opcua/authorizations',json=body).json()['result']
    legacy={**secure_server,'workspace':account[2]};monkeypatch.setenv('GA_OPCUA_READ_GRANTS',json.dumps([legacy]))
    result=client.post('/api/opcua/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':grant['version'],'enabled':False});assert result.status_code==200
    assert client.get('/api/opcua/grants').json()['grants']==[]
    monkeypatch.setenv('GA_MANAGED_OPCUA_ENABLED','0')
    assert client.get('/api/opcua/grants').json()['grants']==[]


def test_replacement_key_cannot_create_mixed_generation(client,configured):
    body,key=configured;first=client.post('/api/opcua/authorizations',json=body);assert first.status_code==200
    original=key.read_bytes();key.write_bytes(Fernet.generate_key());key.chmod(0o600)
    try:
        status=client.get('/api/opcua/authorizations').json();assert status['vault_ready'] is False
        changed={**body,'request_id':str(uuid4()),'node_id':body['node_id']+'-other'}
        assert client.post('/api/opcua/authorizations',json=changed).status_code==503
        assert client.get('/api/opcua/grants').json()['grants']==[]
    finally:key.write_bytes(original);key.chmod(0o600)
    assert len(client.get('/api/opcua/grants').json()['grants'])==1


def test_key_initializer_never_overwrites_or_replaces_lost_key(client,configured,tmp_path,monkeypatch):
    import subprocess,sys,os
    from generic_agents.config import settings
    body,_=configured;target=tmp_path/'new-independent.key';env=os.environ.copy();env['GA_OPCUA_VAULT_KEY_FILE']=str(target);env['GA_STORAGE_DIR']=str(settings().storage_dir)
    program='scripts/initialize_opcua_gateway_key.py'
    first=subprocess.run([sys.executable,program],env=env,capture_output=True,text=True);assert first.returncode==0
    original=target.read_bytes();second=subprocess.run([sys.executable,program],env=env,capture_output=True,text=True);assert second.returncode!=0 and target.read_bytes()==original
    monkeypatch.setenv('GA_OPCUA_VAULT_KEY_FILE',str(target));assert client.post('/api/opcua/authorizations',json=body).status_code==200
    target.unlink();third=subprocess.run([sys.executable,program],env=env,capture_output=True,text=True);assert third.returncode!=0 and not target.exists()
