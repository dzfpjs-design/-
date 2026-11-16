import asyncio
import ipaddress
import json
import os
import socket
import threading
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
from asyncua import Server,ua
from generic_agents.opcua_readonly_transport import grants,read_value,OpcuaError

SERVERS={}

def _certificate(directory,name,ip,uri,is_server):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)])
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
      .not_valid_before(datetime.now(timezone.utc)-timedelta(days=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=3))
      .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(uri),x509.IPAddress(ipaddress.ip_address(ip))]),critical=False)
      .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
      .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=True,data_encipherment=True,key_agreement=False,key_cert_sign=False,crl_sign=False,encipher_only=False,decipher_only=False),critical=True)
      .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if is_server else ExtendedKeyUsageOID.CLIENT_AUTH]),critical=False)
      .sign(key,hashes.SHA256()))
    cp=directory/(name+'.der');kp=directory/(name+'-key.pem')
    cp.write_bytes(cert.public_bytes(serialization.Encoding.DER));kp.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));kp.chmod(0o600)
    return cp,kp,cert

@pytest.fixture
def secure_server(tmp_path,monkeypatch):
    ip=os.getenv('GA_OPCUA_TEST_IP','192.168.0.100')
    server_cert,server_key,server_x509=_certificate(tmp_path,'server',ip,'urn:test:server',True)
    client_cert,client_key,client_x509=_certificate(tmp_path,'client',ip,'urn:test:client',False)
    sock=socket.socket();
    try:sock.bind((ip,0))
    except OSError:pytest.skip('需要本机 RFC1918 测试地址')
    port=sock.getsockname()[1];sock.close();loop=asyncio.new_event_loop();ready=threading.Event();state={}
    def run():
      asyncio.set_event_loop(loop)
      async def start():
        server=Server();await server.init();await server.set_application_uri('urn:test:server');server.set_endpoint(f'opc.tcp://{ip}:{port}/sensor/')
        await server.load_certificate(server_cert);await server.load_private_key(server_key)
        server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
        async def validate(cert,application):
            if cert.fingerprint(hashes.SHA256())!=client_x509.fingerprint(hashes.SHA256()):raise ValueError('client certificate untrusted')
        server.set_certificate_validator(validate)
        ns=await server.register_namespace('urn:test:sensors');obj=await server.nodes.objects.add_object(ns,'Probe')
        variable=await obj.add_variable(ua.NodeId('temperature',ns),'Temperature',23.5)
        reading=ua.DataValue(ua.Variant(23.5,ua.VariantType.Double),SourceTimestamp=datetime(2026,1,2,3,4,5,tzinfo=timezone.utc))
        await variable.write_value(reading);await server.start();state['server']=server;state['variable']=variable;state['node']=f'ns={ns};s=temperature'
      try:loop.run_until_complete(start())
      except Exception as e:state['error']=e;ready.set();return
      ready.set();loop.run_forever();loop.run_until_complete(state['server'].stop());loop.close()
    thread=threading.Thread(target=run,daemon=True);thread.start();assert ready.wait(10)
    if 'error' in state:raise state['error']
    monkeypatch.setenv('GA_OPCUA_CERT_ROOT',str(tmp_path))
    entry={'workspace':str(uuid4()),'host':ip,'port':port,'path':'/sensor/','node_id':state['node'],
           'server_certificate':str(server_cert),'client_certificate':str(client_cert),'client_private_key':str(client_key),
           'server_app_uri':'urn:test:server','client_app_uri':'urn:test:client'}
    monkeypatch.setenv('GA_OPCUA_READ_GRANTS',json.dumps([entry]))
    SERVERS[entry['workspace']]=(state,loop)
    yield entry
    SERVERS.pop(entry['workspace'],None)
    loop.call_soon_threadsafe(loop.stop);thread.join(5)

def test_encrypted_certificate_pinned_read_preserves_status_and_device_time(secure_server):
    grant=grants(secure_server['workspace'])[0];value=read_value(grant)
    assert value['value']==23.5 and value['quality']=='good' and value['status_code']==0
    assert value['source_timestamp'].startswith('2026-01-02T03:04:05')
    assert value['received_at'] and value['server_timestamp'] is not None

def test_wrong_identity_and_private_key_permissions_fail_closed(secure_server,monkeypatch):
    entry=dict(secure_server,server_app_uri='urn:wrong');monkeypatch.setenv('GA_OPCUA_READ_GRANTS',json.dumps([entry]))
    with pytest.raises(OpcuaError):grants(entry['workspace'])
    entry=secure_server;Path(entry['client_private_key']).chmod(0o644);monkeypatch.setenv('GA_OPCUA_READ_GRANTS',json.dumps([entry]))
    with pytest.raises(OpcuaError):grants(entry['workspace'])


def test_untrusted_but_identity_matching_server_certificate_is_rejected(secure_server,monkeypatch):
    entry=dict(secure_server);cp,_,_=_certificate(Path(os.environ['GA_OPCUA_CERT_ROOT']),'other-server',entry['host'],entry['server_app_uri'],True)
    entry['server_certificate']=str(cp);monkeypatch.setenv('GA_OPCUA_READ_GRANTS',json.dumps([entry]))
    with pytest.raises(OpcuaError):read_value(grants(entry['workspace'])[0])

def test_real_uncertain_status_is_preserved(secure_server):
    state,loop=SERVERS[secure_server['workspace']]
    value=ua.DataValue(ua.Variant(21.0,ua.VariantType.Double),StatusCode=ua.StatusCode(ua.StatusCodes.Uncertain),SourceTimestamp=datetime(2026,1,3,tzinfo=timezone.utc))
    asyncio.run_coroutine_threadsafe(state['variable'].write_value(value),loop).result(timeout=5)
    result=read_value(grants(secure_server['workspace'])[0]);assert result['quality']=='uncertain' and result['status_code']==ua.StatusCodes.Uncertain
