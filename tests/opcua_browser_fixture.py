"""Independent OPC UA test server. Never use with a real workspace or device."""
import asyncio,ipaddress,json,os,signal,socket,tempfile
from pathlib import Path
from uuid import UUID
from asyncua import Server,ua
from cryptography.hazmat.primitives import hashes
from test_opcua_secure_transport import _certificate

async def main():
    workspace=str(UUID(os.environ['GA_OPCUA_FIXTURE_WORKSPACE']))
    host=os.environ.get('GA_OPCUA_FIXTURE_HOST','192.168.0.100')
    if not any(ipaddress.ip_address(host) in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):raise ValueError('fixture requires local RFC1918 address')
    sock=socket.socket();sock.bind((host,0));port=sock.getsockname()[1];sock.close()
    root=Path(tempfile.mkdtemp(prefix='ga-opcua-browser-fixture-'));root.chmod(0o700)
    sc,sk,_=_certificate(root,'server',host,'urn:ga:test:server',True);cc,ck,client_cert=_certificate(root,'client',host,'urn:ga:test:client',False)
    server=Server();await server.init();await server.set_application_uri('urn:ga:test:server');server.set_endpoint(f'opc.tcp://{host}:{port}/fixture/')
    await server.load_certificate(sc);await server.load_private_key(sk);server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
    async def validate(cert,application):
        if cert.fingerprint(hashes.SHA256())!=client_cert.fingerprint(hashes.SHA256()):raise ValueError('untrusted test client')
    server.set_certificate_validator(validate)
    ns=await server.register_namespace('urn:ga:test:sensors');obj=await server.nodes.objects.add_object(ns,'TestSensor');await obj.add_variable(ua.NodeId('temperature',ns),'Temperature',23.5)
    entry={'workspace':workspace,'host':host,'port':port,'path':'/fixture/','node_id':f'ns={ns};s=temperature','server_certificate':str(sc),'client_certificate':str(cc),'client_private_key':str(ck),'server_app_uri':'urn:ga:test:server','client_app_uri':'urn:ga:test:client'}
    grant_file=root/'grant.json';grant_file.write_text(json.dumps([entry]),encoding='utf-8');grant_file.chmod(0o600)
    await server.start();print(json.dumps({'status':'ready','certificate_root':str(root),'grant_file':str(grant_file),'endpoint':f'opc.tcp://{host}:{port}/fixture/','node':entry['node_id'],'notice':'isolated simulated test device'},ensure_ascii=False),flush=True)
    event=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,event.set)
    try:await event.wait()
    finally:await server.stop()

if __name__=='__main__':asyncio.run(main())
