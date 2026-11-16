"""Isolated TLS fixture tests for graphical FTPS access; never real devices."""
import json,os,socket,subprocess,time,ipaddress,threading
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from pathlib import Path
from uuid import uuid4
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents import managed_ftps_grants as managed

if not any(getattr(r,'path',None)=='/api/ftps-authorizations/grants' for r in app.routes):app.include_router(managed.router)

def ok(response):
 assert response.status_code==200,response.text
 result=response.json();assert result['state']=='succeeded';return result

def test_tls_gui_authorization_read_import_revoke_and_recovery(client,account,tmp_path,monkeypatch):
 host=subprocess.check_output(['ipconfig','getifaddr','en0'],text=True).strip()
 if not host.startswith(('10.','192.168.','172.')):raise RuntimeError('test requires a private local interface')
 key=tmp_path/'vault';key.write_bytes(Fernet.generate_key());key.chmod(0o600)
 monkeypatch.setenv('GA_FILE_GATEWAY_KEY_FILE',str(key));monkeypatch.setenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','1');monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS',str(ipaddress.ip_network(host+'/24',strict=False)))
 cert=tmp_path/'ca.crt';private=tmp_path/'tls.key';files=tmp_path/'files';files.mkdir();(files/'synthetic.txt').write_text('synthetic content')
 subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(private),'-out',str(cert),'-subj','/CN=isolated-test','-addext','subjectAltName=IP:'+host],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 sock=socket.socket();sock.bind((host,0));port=sock.getsockname()[1];sock.close()
 server='''from pyftpdlib.authorizers import DummyAuthorizer\nfrom pyftpdlib.handlers import TLS_FTPHandler\nfrom pyftpdlib.servers import FTPServer\nimport os\na=DummyAuthorizer();a.add_user("synthetic-user","synthetic-pass",os.environ["FILES"],perm="elr")\nclass Handler(TLS_FTPHandler):pass\nHandler.authorizer=a;Handler.certfile=os.environ["CERT"];Handler.keyfile=os.environ["PRIVATE"];Handler.tls_control_required=True;Handler.tls_data_required=True;Handler.passive_ports=range(49220,49240)\ns=FTPServer((os.environ["HOST"],int(os.environ["PORT"])),Handler);print("ready",flush=True);s.serve_forever()\n'''
 env={**os.environ,'FILES':str(files),'CERT':str(cert),'PRIVATE':str(private),'HOST':host,'PORT':str(port)}
 py=str(Path.home()/'.codex/forestry-public-testing-tools/ftps-fixture-venv/bin/python');proc=subprocess.Popen([py,'-I','-c',server],env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
 try:
  assert proc.stdout.readline().strip()=='ready'
  body={'request_id':str(uuid4()),'name':'隔离 FTPS','host':host,'port':port,'tls_name':host,'root':'/','data_port_min':49220,'data_port_max':49239,'username':'synthetic-user','password':'synthetic-pass','ca_pem':cert.read_text(),'server_chroot_confirmed':True}
  url='/api/ftps-authorizations/grants';created=ok(client.post(url,json=body));g=created['grant'];assert ok(client.post(url,json=body))==created
  assert 'synthetic-pass' not in json.dumps(created)
  with Session(engine()) as db:
   row=db.scalar(select(Job).where(Job.workspace_id==account[2],Job.kind==managed.KIND));raw=json.dumps(row.data);assert 'synthetic-pass' not in raw and 'BEGIN CERTIFICATE' not in raw
  # A trusted CA never bypasses host-name verification.
  mismatch=ok(client.post(url,json={**body,'request_id':str(uuid4()),'name':'错误证书名','tls_name':'not-the-server.invalid'}))['grant']
  badcon=ok(client.post('/api/ftps/connections',json={'request_id':str(uuid4()),'name':'错误证书名连接','grant_id':mismatch['id']}))['result']
  bad=client.post('/api/ftps/connections/'+badcon['id']+'/listings',json={'request_id':str(uuid4()),'version':badcon['version']})
  assert bad.status_code==200 and bad.json()['state']=='failed'
  con=ok(client.post('/api/ftps/connections',json={'request_id':str(uuid4()),'name':'合成连接','grant_id':g['id']}))['result'];base='/api/ftps/connections/'+con['id'];listing=ok(client.post(base+'/listings',json={'request_id':str(uuid4()),'version':con['version']}))['result'];assert listing['files'][0]['name']=='synthetic.txt'
  doc=listing['files'][0];assert ok(client.post(base+'/imports',json={'request_id':str(uuid4()),'version':con['version'],'listing_id':listing['listing_id'],'file_id':doc['id']}))['result']['name']=='synthetic.txt'
  # A legacy environment grant to the same target remains visible until
  # revoked, then the persistent denial survives disabling the GUI feature.
  from generic_agents.config import settings
  from generic_agents.ftps_transport import grants as effective_grants
  ep={'host':host,'port':port,'tls_name':host,'data_port_min':49220,'data_port_max':49239}
  legacy={'name':'预先配置','endpoint':ep,'root':'/','username_ref':'GA_FTPS_USERNAME_LEGACY','password_ref':'GA_FTPS_PASSWORD_LEGACY','ca_ref':'','server_chroot_confirmed':True}
  monkeypatch.setattr(settings(),'ftps_allowed_endpoints',json.dumps([ep]));monkeypatch.setattr(settings(),'ftps_access_grants',json.dumps({account[2]:[legacy]}))
  assert any(x['name']=='预先配置' for x in effective_grants(account[2]))
  assert client.post(url,json={**body,'tls_name':'changed.invalid'}).status_code==409
  stop_id=str(uuid4());assert client.post('/api/ftps-authorizations/operations/'+stop_id+'/cancel').json()['state']=='cancelled'
  assert client.post(url,json={**body,'request_id':stop_id}).json()['state']=='cancelled'
  bad_ca=client.post(url,json={**body,'request_id':str(uuid4()),'ca_pem':'-----BEGIN PRIVATE KEY-----secret'})
  assert bad_ca.status_code==422 and 'secret' not in bad_ca.text
  # A revocation must wait until the in-flight authorized TLS read commits.
  from generic_agents import ftps_gateway_api
  entered=threading.Event();release=threading.Event();real_list=ftps_gateway_api.list_files
  def delayed(grant):
   entered.set();assert release.wait(10);return real_list(grant)
  monkeypatch.setattr(ftps_gateway_api,'list_files',delayed);revoke_id=str(uuid4())
  with TestClient(app) as other:
   login=other.post('/api/login',json={'username':account[0],'password':account[1]});assert login.status_code==200
   other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
   with ThreadPoolExecutor(max_workers=2) as pool:
    reading=pool.submit(client.post,base+'/listings',json={'request_id':str(uuid4()),'version':con['version']});assert entered.wait(10)
    revoking=pool.submit(other.post,url+'/'+g['id']+'/revoke',json={'request_id':revoke_id,'version':g['version']});time.sleep(.25)
    try:assert not revoking.done()
    finally:release.set()
    assert ok(reading.result(timeout=15));revoked=ok(revoking.result(timeout=15));assert revoked['grant']['enabled'] is False
  assert client.get('/api/ftps-authorizations/operations/'+revoke_id).json()['grant']['enabled'] is False
  assert client.post(base+'/listings',json={'request_id':str(uuid4()),'version':con['version']}).status_code==409
  assert client.get(base+'/imports').json()['source_available'] is False
  monkeypatch.setenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','0');assert not any(x['name']=='预先配置' for x in effective_grants(account[2]));assert client.post(base+'/listings',json={'request_id':str(uuid4()),'version':con['version']}).status_code==409
  # If the identity marker is lost but FTPS ciphertext remains, the
  # initialization tool must refuse to generate a replacement key.
  from sqlalchemy import delete
  with Session(engine()) as db:
   db.execute(delete(Job).where(Job.kind==managed.KEY_MARKER));db.commit()
  replacement=tmp_path/'replacement-key';child={**os.environ,'GA_FILE_GATEWAY_KEY_FILE':str(replacement),'PYTHONPATH':str(Path.cwd()/'src')}
  probe=subprocess.run([str(Path.cwd()/'.venv/bin/python'),'scripts/initialize_file_gateway_key.py'],env=child,capture_output=True,text=True)
  assert probe.returncode!=0 and not replacement.exists()
  monkeypatch.setenv('GA_MANAGED_FILE_GATEWAYS_ENABLED','1');assert client.get(url).json()['key_ready'] is False
 finally:
  proc.terminate();proc.wait(timeout=10)

def test_ftps_pagination_filters_before_limit_and_keeps_all_ties(client,account):
 from datetime import datetime,timezone
 w=account[2];stamp=datetime(2025,1,1,tzinfo=timezone.utc)
 with Session(engine()) as db:
  connections=[]
  for n in range(102):
   row=Job(workspace_id=w,kind='ftps_connection_v1',status='internal',idempotency_key='paginated-con-'+str(n),created_at=stamp,data={'name':'目录 '+str(n),'grant_id':'a'*64,'status':'active'});db.add(row);connections.append(row)
  db.flush();target=connections[0].id;other=connections[1].id
  for n in range(1204):
   rid=str(uuid4());identity=target if n<102 else other
   db.add(Job(workspace_id=w,kind='ftps_request_v1',status='internal',idempotency_key='paginated-req-'+str(n),created_at=stamp,data={'request_id':rid,'state':'succeeded','kind':'listing','submission':{'connection_id':identity},'result':{'listing_id':rid,'files':[]}}))
  db.commit()
 first=client.get('/api/ftps/connections');assert first.status_code==200;first=first.json();assert len(first['connections'])==100 and first['next_cursor']
 second=client.get('/api/ftps/connections',params={'cursor':first['next_cursor']});assert second.status_code==200;second=second.json();assert len(second['connections'])==2 and second['next_cursor'] is None
 assert len({x['id'] for x in first['connections']+second['connections']})==102
 path='/api/ftps/connections/'+target+'/listings';page=client.get(path);assert page.status_code==200;page=page.json();assert len(page['listings'])==100 and page['next_cursor'] and page['source_available'] is False
 more=client.get(path,params={'cursor':page['next_cursor']});assert more.status_code==200;more=more.json();assert len(more['listings'])==2 and more['next_cursor'] is None
 assert len({x['request_id'] for x in page['listings']+more['listings']})==102
 assert client.get('/api/ftps/connections/'+other+'/listings',params={'cursor':page['next_cursor']}).status_code==409
