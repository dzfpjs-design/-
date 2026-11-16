"""Disposable PostgreSQL, real isolated FTP/SMB servers; never application data."""
import json,os,threading,time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents import managed_file_grants as managed
from tests.test_ftp_gateway_integration import ftp_gateway
from tests.test_smb_gateway_integration import smb_gateway

if not any(getattr(route,'path',None)=='/api/file-gateway-authorizations/grants' for route in app.routes):app.include_router(managed.router)

@pytest.fixture
def secure_config(tmp_path,monkeypatch):
    key=tmp_path/'server-file-key';key.write_bytes(Fernet.generate_key());key.chmod(0o600)
    monkeypatch.setenv('GA_FILE_GATEWAY_KEY_FILE',str(key));monkeypatch.setenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','1')
    monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS','192.168.0.0/16')
    # Fixtures bind only loopback. This test-local override cannot be enabled by a workspace request.
    monkeypatch.setattr(managed,'in_ceiling',lambda host:host=='127.0.0.1')
    return key


def body_for(protocol):
    ep=json.loads(os.environ['GA_'+protocol.upper()+'_ALLOWED_ENDPOINTS'])[0]
    v={'request_id':str(uuid4()),'protocol':protocol,'name':'合成文件源','host':ep['host'],'port':ep['port'],
       'username':'operator' if protocol=='ftp' else 'fixture','password':'fixture-password'}
    if protocol=='ftp':v.update(root='/',data_port_min=ep['data_port_min'],data_port_max=ep['data_port_max'],server_chroot_confirmed=True,plaintext_risk_confirmed=True)
    else:v.update(share=ep['share'],share_root_confirmed=True)
    return v


def success(response):
    assert response.status_code==200,response.text
    value=response.json();assert value['state']=='succeeded';return value


def test_active_quota_preserves_access_replay_and_historical_recovery(secure_config,ftp_gateway,client,monkeypatch):
    path='/api/file-gateway-authorizations/grants'
    # A low test limit exercises the same boundary without a large HTTP loop.
    monkeypatch.setattr(managed,'MAX_ACTIVE_GRANTS',2)
    body=body_for('ftp');first=success(client.post(path,json=body))
    second=success(client.post(path,json=body_for('ftp')))
    assert client.post(path,json=body_for('ftp')).status_code==409
    assert success(client.post(path,json=body))==first  # idempotent replay at capacity
    assert client.get('/api/ftp/grants').status_code==200
    assert client.get(path).status_code==200

    # Simulate an older installation that already saved more than the limit.
    monkeypatch.setattr(managed,'MAX_ACTIVE_GRANTS',3)
    third=success(client.post(path,json=body_for('ftp')))
    monkeypatch.setattr(managed,'MAX_ACTIVE_GRANTS',2)
    listed=client.get(path);assert listed.status_code==200
    assert len([g for g in listed.json()['grants'] if g['origin']=='workspace'])==3
    assert len(client.get('/api/ftp/grants').json()['grants'])>=3
    for grant in (second['grant'],third['grant']):
        success(client.post(path+'/'+grant['id']+'/revoke',json={'request_id':str(uuid4()),'protocol':'ftp','version':grant['version']}))
    # Revoked history does not permanently consume the active limit.
    assert success(client.post(path,json=body_for('ftp')))['grant']['enabled']


def test_active_quota_is_serialized_between_administrators(secure_config,ftp_gateway,account,client,monkeypatch):
    monkeypatch.setattr(managed,'MAX_ACTIVE_GRANTS',1)
    other=TestClient(app)
    login=other.post('/api/login',json={'username':account[0],'password':account[1]})
    assert login.status_code==200
    other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
    try:
        gate=threading.Barrier(2)
        def submit(current):
            body=body_for('ftp');gate.wait(timeout=5)
            return current.post('/api/file-gateway-authorizations/grants',json=body).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(submit,[client,other]))
        assert sorted(responses)==[200,409]
        grants=client.get('/api/file-gateway-authorizations/grants')
        assert grants.status_code==200
        assert sum(g['enabled'] for g in grants.json()['grants'] if g['origin']=='workspace')==1
    finally:other.close()


@pytest.mark.parametrize('protocol,fixture',[('ftp','ftp_gateway'),('smb','smb_gateway')])
def test_real_file_protocol_authorize_import_revoke_and_no_plaintext(protocol,fixture,request,secure_config,client):
    request.getfixturevalue(fixture);body=body_for(protocol);r=success(client.post('/api/file-gateway-authorizations/grants',json=body));grant=r['grant']
    assert 'fixture-password' not in json.dumps(r) and success(client.post('/api/file-gateway-authorizations/grants',json=body))==r
    path=f'/api/{protocol}';connect=success(client.post(path+'/connections',json={'request_id':str(uuid4()),'name':'合成目录','grant_id':grant['id']}))['result']
    listed=success(client.post(path+'/connections/'+connect['id']+'/listings',json={'request_id':str(uuid4()),'version':connect['version']}))['result']
    item=next(v for v in listed['files'] if v['name']=='design.txt')
    imported=success(client.post(path+'/connections/'+connect['id']+'/imports',json={'request_id':str(uuid4()),'version':connect['version'],'listing_id':listed['listing_id'],'file_id':item['id']}))
    assert client.get('/api/setup/materials/'+imported['result']['material_id']).status_code==200
    with Session(engine()) as db:
        saved=db.scalars(select(Job).where(Job.workspace_id==connect.get('workspace_id',request.getfixturevalue('account')[2]))).all()
        assert 'fixture-password' not in json.dumps([v.data for v in saved])
    revoke={'request_id':str(uuid4()),'protocol':protocol,'version':grant['version']}
    done=success(client.post('/api/file-gateway-authorizations/grants/'+grant['id']+'/revoke',json=revoke));assert not done['grant']['enabled']
    assert success(client.post('/api/file-gateway-authorizations/grants/'+grant['id']+'/revoke',json=revoke))==done
    assert client.post(path+'/connections/'+connect['id']+'/listings',json={'request_id':str(uuid4()),'version':connect['version']}).status_code==409
    assert client.get('/api/setup/materials/'+imported['result']['material_id']).status_code==200
    # Revoking a managed grant also denies any older server grant to the same endpoint/root.
    assert client.get(path+'/grants').json()['grants']==[]


def test_key_loss_replacement_cancel_fence_and_secret_free_errors(secure_config,ftp_gateway,client):
    body=body_for('ftp');secret=body['password'];path='/api/file-gateway-authorizations/grants'
    first=success(client.post(path,json=body));assert first['state']=='succeeded'
    secure_config.unlink();next_body={**body,'request_id':str(uuid4())};bad=client.post(path,json=next_body)
    assert bad.status_code==503 and not secure_config.exists() and secret not in bad.text
    secure_config.write_bytes(Fernet.generate_key());secure_config.chmod(0o600)
    bad=client.post(path,json=next_body);assert bad.status_code==503 and secret not in bad.text
    rid=str(uuid4());cancel=client.post('/api/file-gateway-authorizations/operations/'+rid+'/cancel');assert cancel.json()['state']=='cancelled'
    # Restore original key only in the separate replay test; no request is silently accepted with a replacement.
    other={**body,'request_id':rid};assert client.post(path,json=other).status_code==503


def test_missing_key_cannot_be_created_automatically(secure_config,ftp_gateway,client):
    secure_config.unlink();response=client.post('/api/file-gateway-authorizations/grants',json=body_for('ftp'))
    assert response.status_code==503 and not secure_config.exists()


def test_legacy_grant_revocation_is_durable_and_cannot_be_reused(secure_config,ftp_gateway,client):
    old=client.get('/api/ftp/grants').json()['grants'][0]
    revoke={'request_id':str(uuid4()),'protocol':'ftp','version':0}
    result=success(client.post('/api/file-gateway-authorizations/grants/'+old['id']+'/revoke',json=revoke))
    assert result['grant']['enabled'] is False
    assert client.get('/api/ftp/grants').json()['grants']==[]
    status=client.get('/api/file-gateway-authorizations/operations/'+revoke['request_id'])
    assert status.status_code==200 and status.json()==result
    new=success(client.post('/api/file-gateway-authorizations/grants',json=body_for('ftp')))['grant']
    grants=client.get('/api/ftp/grants').json()['grants']
    assert [x['id'] for x in grants]==[new['id']] and old['id']!=new['id']


def test_revocation_survives_disabling_self_service(secure_config,ftp_gateway,client,monkeypatch):
    old=client.get('/api/ftp/grants').json()['grants'][0]
    revoke={'request_id':str(uuid4()),'protocol':'ftp','version':0}
    success(client.post('/api/file-gateway-authorizations/grants/'+old['id']+'/revoke',json=revoke))
    monkeypatch.delenv('GA_MANAGED_FILE_GATEWAYS_ENABLED',raising=False)
    response=client.get('/api/ftp/grants')
    assert response.status_code==200 and response.json()['grants']==[]


def test_revoke_waits_for_inflight_import_before_taking_effect(secure_config,ftp_gateway,account,client,monkeypatch):
    from generic_agents import ftp_gateway_api
    grant=success(client.post('/api/file-gateway-authorizations/grants',json=body_for('ftp')))['grant']
    con=success(client.post('/api/ftp/connections',json={'request_id':str(uuid4()),'name':'模拟源','grant_id':grant['id']}))['result']
    url='/api/ftp/connections/'+con['id'];listing=success(client.post(url+'/listings',json={'request_id':str(uuid4()),'version':con['version']}))['result']
    item=next(x for x in listing['files'] if x['name']=='design.txt');entered=threading.Event();release=threading.Event();original=ftp_gateway_api.read_file
    def delayed(*args,**kwargs):
        entered.set()
        if not release.wait(10):raise RuntimeError('test timeout')
        return original(*args,**kwargs)
    monkeypatch.setattr(ftp_gateway_api,'read_file',delayed)
    extra=TestClient(app);login=extra.post('/api/login',json={'username':account[0],'password':account[1]});assert login.status_code==200
    extra.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
    request_body={'request_id':str(uuid4()),'version':con['version'],'listing_id':listing['listing_id'],'file_id':item['id']}
    revoke={'request_id':str(uuid4()),'protocol':'ftp','version':grant['version']}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(client.post,url+'/imports',json=request_body)
            assert entered.wait(8)
            second=pool.submit(extra.post,'/api/file-gateway-authorizations/grants/'+grant['id']+'/revoke',json=revoke)
            time.sleep(.25);assert not second.done()
            release.set();saved=success(first.result(timeout=15));done=success(second.result(timeout=15))
        assert not done['grant']['enabled'] and client.get('/api/setup/materials/'+saved['result']['material_id']).status_code==200
        assert client.post(url+'/listings',json={'request_id':str(uuid4()),'version':con['version']}).status_code==409
    finally:
        release.set();extra.close()


def test_cancel_before_create_and_secret_free_conflict(secure_config,ftp_gateway,client):
    body=body_for('ftp');rid=body['request_id']
    stopped=client.post('/api/file-gateway-authorizations/operations/'+rid+'/cancel')
    assert stopped.status_code==200 and stopped.json()['state']=='cancelled'
    assert client.post('/api/file-gateway-authorizations/grants',json=body).json()['state']=='cancelled'
    assert client.get('/api/file-gateway-authorizations/operations/'+rid).json()['state']=='cancelled'
    body=body_for('ftp');success(client.post('/api/file-gateway-authorizations/grants',json=body))
    changed={**body,'password':'a-distinct-sensitive-password'}
    bad=client.post('/api/file-gateway-authorizations/grants',json=changed)
    assert bad.status_code==409 and changed['password'] not in bad.text


def test_real_private_ipv4_ceiling_for_ftp(tmp_path,monkeypatch,account,client):
    import ipaddress,socket
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    host=os.getenv('GA_MANAGED_TEST_PRIVATE_IP','')
    if not host:pytest.skip('需要显式提供本机 RFC1918 监听地址，仅用于隔离测试')
    ip=ipaddress.IPv4Address(host);net=ipaddress.IPv4Network(host+'/16',strict=False)
    assert ip.is_private and not ip.is_loopback
    (tmp_path/'private-source.txt').write_text('仅为隔离测试资料。',encoding='utf8')
    auth=DummyAuthorizer();auth.add_user('only-test-user','only-test-password',str(tmp_path),perm='elr')
    class Handler(FTPHandler):pass
    Handler.authorizer=auth;Handler.passive_ports=range(49200,49220)
    server=FTPServer((host,0),Handler);port=server.socket.getsockname()[1];stop=threading.Event()
    def run():
        while not stop.is_set():server.serve_forever(timeout=.05,blocking=False,handle_exit=False)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    key=tmp_path/'server-key';key.write_bytes(Fernet.generate_key());key.chmod(0o600)
    monkeypatch.setenv('GA_FILE_GATEWAY_KEY_FILE',str(key));monkeypatch.setenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','1');monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS',str(net));monkeypatch.setenv('GA_FTP_ALLOWED_ENDPOINTS','[]');monkeypatch.setenv('GA_FTP_ACCESS_GRANTS','{}')
    try:
        body={'request_id':str(uuid4()),'protocol':'ftp','name':'真实协议隔离夹具','host':host,'port':port,'root':'/','data_port_min':49200,'data_port_max':49219,'username':'only-test-user','password':'only-test-password','server_chroot_confirmed':True,'plaintext_risk_confirmed':True}
        denied={**body,'request_id':str(uuid4()),'host':'8.8.8.8'};bad=client.post('/api/file-gateway-authorizations/grants',json=denied);assert bad.status_code==422 and 'only-test-password' not in bad.text
        grant=success(client.post('/api/file-gateway-authorizations/grants',json=body))['grant']
        connection=success(client.post('/api/ftp/connections',json={'request_id':str(uuid4()),'name':'隔离 FTP','grant_id':grant['id']}))['result']
        listing=success(client.post('/api/ftp/connections/'+connection['id']+'/listings',json={'request_id':str(uuid4()),'version':connection['version']}))['result']
        assert [x['name'] for x in listing['files']]==['private-source.txt']
        monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS','10.0.0.0/16')
        assert client.post('/api/ftp/connections/'+connection['id']+'/listings',json={'request_id':str(uuid4()),'version':connection['version']}).status_code==409
    finally:
        stop.set();thread.join(timeout=2);server.close_all()


def test_original_actor_can_resolve_after_role_downgrade_and_other_workspace_cannot(secure_config,ftp_gateway,account,client):
    from generic_agents.models import Membership,Workspace
    body=body_for('ftp');result=success(client.post('/api/file-gateway-authorizations/grants',json=body))
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='viewer'
        other=Workspace(name='不可访问的工作区');db.add(other);db.commit();other_id=other.id
    resolved=client.get('/api/file-gateway-authorizations/operations/'+body['request_id'])
    assert resolved.status_code==200 and resolved.json()==result
    assert client.get('/api/file-gateway-authorizations/grants').status_code==403
    assert client.post('/api/file-gateway-authorizations/grants',json=body_for('ftp')).status_code==403
    rid=str(uuid4());cancel=client.post('/api/file-gateway-authorizations/operations/'+rid+'/cancel')
    assert cancel.status_code==200 and cancel.json()['state']=='cancelled'
    assert client.get('/api/file-gateway-authorizations/operations/'+body['request_id'],headers={'X-Workspace-ID':other_id}).status_code==403


def test_ciphertext_contains_no_plaintext_credentials(secure_config,ftp_gateway,account,client):
    user='only-user-'+uuid4().hex;password='only-password-'+uuid4().hex
    body={**body_for('ftp'),'username':user,'password':password};result=success(client.post('/api/file-gateway-authorizations/grants',json=body));identity=result['grant']['id']
    with Session(engine()) as db:
        row=db.scalar(select(Job).where(Job.workspace_id==account[2],Job.kind==managed.KIND,Job.idempotency_key=='managed-file-grant:'+identity))
        wire=json.dumps(row.data);assert user not in wire and password not in wire and len(row.data['cipher'])>40
    response=client.get('/api/file-gateway-authorizations/grants')
    assert response.status_code==200 and user not in response.text and password not in response.text


def test_key_initializer_never_overwrites_and_refuses_after_key_loss(tmp_path,monkeypatch,ftp_gateway,account,client):
    import subprocess
    from pathlib import Path
    from generic_agents.config import settings
    root=Path(__file__).resolve().parents[1];key=tmp_path/'dedicated-file.key'
    env=os.environ.copy();env.update(GA_DATABASE_URL=settings().database_url,GA_REDIS_URL='redis://127.0.0.1:1/0',GA_STORAGE_DIR=str(settings().storage_dir),GA_FILE_GATEWAY_KEY_FILE=str(key))
    cmd=[str(root/'.venv/bin/python'),str(root/'scripts/initialize_file_gateway_key.py')]
    first=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True);assert first.returncode==0 and key.is_file()
    initial=key.read_bytes();second=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True)
    assert second.returncode!=0 and key.read_bytes()==initial
    monkeypatch.setenv('GA_FILE_GATEWAY_KEY_FILE',str(key));monkeypatch.setenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','1');monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS','192.168.0.0/16');monkeypatch.setattr(managed,'in_ceiling',lambda host:host=='127.0.0.1')
    success(client.post('/api/file-gateway-authorizations/grants',json=body_for('ftp')))
    key.unlink();lost=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True)
    assert lost.returncode!=0 and not key.exists()


def test_single_connection_pool_authorize_and_import(secure_config,ftp_gateway,client,monkeypatch):
    from sqlalchemy import create_engine
    import generic_agents.db as database
    single=create_engine(str(engine().url),pool_size=1,max_overflow=0,pool_timeout=1)
    monkeypatch.setattr(database,'engine',lambda:single)
    monkeypatch.setattr(managed,'engine',lambda:single)
    try:
        grant=success(client.post('/api/file-gateway-authorizations/grants',json=body_for('ftp')))['grant']
        path='/api/ftp';con=success(client.post(path+'/connections',json={'request_id':str(uuid4()),'name':'单连接池','grant_id':grant['id']}))['result']
        listed=success(client.post(path+'/connections/'+con['id']+'/listings',json={'request_id':str(uuid4()),'version':con['version']}))['result']
        item=next(x for x in listed['files'] if x['name']=='design.txt')
        success(client.post(path+'/connections/'+con['id']+'/imports',json={'request_id':str(uuid4()),'version':con['version'],'listing_id':listed['listing_id'],'file_id':item['id']}))
    finally:single.dispose()
