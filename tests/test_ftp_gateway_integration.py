"""Isolated PostgreSQL + real local FTP service; no running application data."""
import io,json,threading
from uuid import uuid4
import pytest
from PIL import Image
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
from generic_agents.api import app
from generic_agents.ftp_gateway_api import router
app.include_router(router)
# app imports late-declared ORM tables after test database migration.
from generic_agents.models import Base
from generic_agents.db import engine
Base.metadata.create_all(engine())

@pytest.fixture
def ftp_gateway(tmp_path, monkeypatch, account):
    (tmp_path/'design.txt').write_text('field requirements',encoding='utf8')
    Image.new('RGB',(16,16),(120,180,80)).save(tmp_path/'scene.tif')
    auth=DummyAuthorizer();auth.add_user('operator','fixture-password',str(tmp_path),perm='elr')
    class Handler(FTPHandler):pass
    Handler.authorizer=auth;Handler.passive_ports=range(49000,49100)
    server=FTPServer(('127.0.0.1',0),Handler);port=server.socket.getsockname()[1];stop=threading.Event()
    def run():
        while not stop.is_set():server.serve_forever(timeout=0.05,blocking=False,handle_exit=False)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    endpoint={'host':'127.0.0.1','port':port,'data_port_min':49000,'data_port_max':49099}
    grant={'name':'fixture','endpoint':endpoint,'root':'/','username_ref':'GA_FTP_USERNAME_TEST','password_ref':'GA_FTP_PASSWORD_TEST','server_chroot_confirmed':True,'plaintext_risk_confirmed':True}
    monkeypatch.setenv('GA_FTP_ALLOW_LOOPBACK_FIXTURE','1');monkeypatch.setenv('GA_FTP_USERNAME_TEST','operator');monkeypatch.setenv('GA_FTP_PASSWORD_TEST','fixture-password')
    monkeypatch.setenv('GA_FTP_ALLOWED_ENDPOINTS',json.dumps([endpoint]));monkeypatch.setenv('GA_FTP_ACCESS_GRANTS',json.dumps({account[2]:[grant]}))
    yield
    stop.set();thread.join(timeout=2);server.close_all()

def post(client,path,body):
    result=client.post('/api/ftp'+path,json=body)
    assert result.status_code==200,result.text
    receipt=result.json();assert receipt['state']=='succeeded',receipt
    return receipt

def test_ftp_real_files_persist_and_keep_raster_separate(client,ftp_gateway):
    grants=client.get('/api/ftp/grants').json()['grants'];assert len(grants)==1
    creation={'request_id':str(uuid4()),'name':'remote','grant_id':grants[0]['id']}
    saved=post(client,'/connections',creation);con=saved['result'];base='/connections/'+con['id']
    listing=post(client,base+'/listings',{'request_id':str(uuid4()),'version':con['version']})
    assert [f['name'] for f in listing['result']['files']]==['design.txt']
    doc=listing['result']['files'][0];submission={'request_id':str(uuid4()),'version':con['version'],'listing_id':listing['result']['listing_id'],'file_id':doc['id']}
    first=post(client,base+'/imports',submission);second=post(client,base+'/imports',submission)
    assert first['result']==second['result']
    assert client.get('/api/setup/materials/'+first['result']['material_id']).status_code==200
