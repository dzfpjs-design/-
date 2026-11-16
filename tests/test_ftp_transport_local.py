"""Real local FTP transport with a disposable server; never uses the user database."""
import json
import threading
import pytest
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
from generic_agents import ftp_transport as transport

@pytest.fixture
def ftp_server(tmp_path, monkeypatch):
    (tmp_path / 'requirements.txt').write_text('hello ftp', encoding='utf8')
    auth=DummyAuthorizer(); auth.add_user('operator', 'fixture-password', str(tmp_path), perm='elr')
    class Handler(FTPHandler): pass
    Handler.authorizer=auth; Handler.passive_ports=range(49000, 49100)
    server=FTPServer(('127.0.0.1',0), Handler); port=server.socket.getsockname()[1]
    stop=threading.Event()
    def run():
        while not stop.is_set(): server.serve_forever(timeout=0.05,blocking=False,handle_exit=False)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    endpoint={'host':'127.0.0.1','port':port,'data_port_min':49000,'data_port_max':49099}
    grant={'name':'fixture','endpoint':endpoint,'root':'/','username_ref':'GA_FTP_USERNAME_TEST','password_ref':'GA_FTP_PASSWORD_TEST','server_chroot_confirmed':True,'plaintext_risk_confirmed':True}
    monkeypatch.setenv('GA_FTP_ALLOW_LOOPBACK_FIXTURE','1'); monkeypatch.setenv('GA_FTP_USERNAME_TEST','operator');monkeypatch.setenv('GA_FTP_PASSWORD_TEST','fixture-password')
    monkeypatch.setenv('GA_FTP_ALLOWED_ENDPOINTS',json.dumps([endpoint]));monkeypatch.setenv('GA_FTP_ACCESS_GRANTS',json.dumps({'test-workspace':[grant]}))
    yield grant
    stop.set();thread.join(timeout=2);server.close_all()

def test_reads_real_ftp_and_rejects_changed_file(ftp_server,tmp_path):
    grant=transport.grants('test-workspace')[0]; files=transport.list_files(grant)
    entry=next(x for x in files if x['name']=='requirements.txt')
    content, sha=transport.read_file(grant, entry)
    assert content==b'hello ftp' and len(sha)==64
    (tmp_path/'requirements.txt').write_text('changed content',encoding='utf8')
    with pytest.raises(transport.FTPError): transport.read_file(grant,entry)

def test_rejects_unapproved_and_plaintext_not_confirmed(ftp_server,monkeypatch):
    grant=ftp_server;grant['plaintext_risk_confirmed']=False
    monkeypatch.setenv('GA_FTP_ACCESS_GRANTS',json.dumps({'test-workspace':[grant]}))
    with pytest.raises(transport.FTPError): transport.grants('test-workspace')
    with pytest.raises(transport.FTPError): transport._endpoint({'host':'8.8.8.8','port':21,'data_port_min':49000,'data_port_max':49100})
