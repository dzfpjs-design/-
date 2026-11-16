"""Isolated PostgreSQL and signed SMB2 fixture; encryption needs a separate SMB3 server."""
import json,os,socket,threading
from uuid import uuid4
import pytest
from impacket import smbserver
from impacket.ntlm import compute_lmhash,compute_nthash
from generic_agents.api import app
from generic_agents.smb_gateway_api import router
from generic_agents.models import Base
from generic_agents.db import engine
app.include_router(router)
Base.metadata.create_all(engine())

@pytest.fixture
def smb_gateway(tmp_path,monkeypatch,account):
    (tmp_path/'design.txt').write_text('field requirements',encoding='utf8');(tmp_path/'scene.tif').write_bytes(b'II*\x00fixture')
    # Impacket's test server leaves the directory FileID at zero. Supply the
    # fixture filesystem inode so the client's normal nonzero-ID rule is tested.
    original=smbserver.findFirst2
    def with_inode(*args,**kwargs):
        rows,count,error=original(*args,**kwargs)
        for item in rows or []:
            if type(item).__name__=='SMBFindFileIdBothDirectoryInfo':
                name=item['FileName'].decode('utf-16-le');item['FileID']=os.stat(tmp_path/name).st_ino
        return rows,count,error
    monkeypatch.setattr(smbserver,'findFirst2',with_inode)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=smbserver.SimpleSMBServer(listenAddress='127.0.0.1',listenPort=port);server.setSMB2Support(True);server.addShare('DATA',str(tmp_path));server.addCredential('fixture',0,compute_lmhash('fixture-password'),compute_nthash('fixture-password'));server.setLogFile(str(tmp_path/'server.log'))
    thread=threading.Thread(target=server.start,daemon=True);thread.start()
    ep={'host':'127.0.0.1','port':port,'share':'DATA'};grant={'name':'fixture','endpoint':ep,'username_ref':'GA_SMB_USERNAME_TEST','password_ref':'GA_SMB_PASSWORD_TEST','share_root_confirmed':True}
    monkeypatch.setenv('GA_SMB_ALLOW_LOOPBACK_FIXTURE','1');monkeypatch.setenv('GA_SMB_ALLOW_UNENCRYPTED_FIXTURE','1');monkeypatch.setenv('GA_SMB_USERNAME_TEST','fixture');monkeypatch.setenv('GA_SMB_PASSWORD_TEST','fixture-password')
    monkeypatch.setenv('GA_SMB_ALLOWED_ENDPOINTS',json.dumps([ep]));monkeypatch.setenv('GA_SMB_ACCESS_GRANTS',json.dumps({account[2]:[grant]}))
    yield
    server.stop();thread.join(timeout=2)

def post(client,path,body):
    response=client.post('/api/smb'+path,json=body);assert response.status_code==200,response.text
    receipt=response.json();assert receipt['state']=='succeeded',receipt
    return receipt

def test_smb_signed_fixture_persists_document_and_excludes_tiff(client,smb_gateway):
    grants=client.get('/api/smb/grants').json()['grants'];assert len(grants)==1
    creation={'request_id':str(uuid4()),'name':'remote','grant_id':grants[0]['id']}
    saved=post(client,'/connections',creation);con=saved['result'];base='/connections/'+con['id']
    listing=post(client,base+'/listings',{'request_id':str(uuid4()),'version':con['version']})
    assert 'scene.tif' not in [f['name'] for f in listing['result']['files']]
    doc=next(f for f in listing['result']['files'] if f['name']=='design.txt')
    submission={'request_id':str(uuid4()),'version':con['version'],'listing_id':listing['result']['listing_id'],'file_id':doc['id']}
    first=post(client,base+'/imports',submission);second=post(client,base+'/imports',submission)
    assert first['result']==second['result']
    assert client.get('/api/setup/materials/'+first['result']['material_id']).status_code==200

def test_unencrypted_smb_fails_closed_outside_fixture_override(account,smb_gateway,monkeypatch):
    from generic_agents import smb_transport
    grant=smb_transport.grants(account[2])[0]
    monkeypatch.delenv('GA_SMB_ALLOW_UNENCRYPTED_FIXTURE')
    with pytest.raises(smb_transport.SMBError):smb_transport.list_files(grant)

def test_smb_does_not_accept_other_endpoints_or_grants(account,smb_gateway,monkeypatch):
    from generic_agents import smb_transport
    assert smb_transport.grants(str(uuid4()))==[]
    with pytest.raises(smb_transport.SMBError):smb_transport._endpoint({'host':'8.8.8.8','port':445,'share':'DATA'})

def test_smb_revocation_keeps_receipts_but_blocks_new_reads(client,smb_gateway,monkeypatch):
    grant=client.get('/api/smb/grants').json()['grants'][0]
    con=post(client,'/connections',{'request_id':str(uuid4()),'name':'remote','grant_id':grant['id']})['result'];base='/connections/'+con['id']
    req={'request_id':str(uuid4()),'version':con['version']};old=post(client,base+'/listings',req)
    monkeypatch.setenv('GA_SMB_ACCESS_GRANTS','{}')
    exact=client.get('/api/smb/requests/'+req['request_id']);assert exact.status_code==200 and exact.json()==old
    history=client.get('/api/smb'+base+'/listings');assert history.status_code==200 and history.json()['source_available'] is False
    blocked=client.post('/api/smb'+base+'/listings',json={'request_id':str(uuid4()),'version':con['version']});assert blocked.status_code==409
    client.headers['X-Workspace-ID']=str(uuid4())
    assert client.get('/api/smb/requests/'+req['request_id']).status_code==403
