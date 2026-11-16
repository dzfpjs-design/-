import json
import os
import hashlib
import re
import ssl
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch
import httpx

from generic_agents.onvif_camera_enrolment import EnrollmentError, device_url, grant, stream_uri, configure_gateway, gateway_state

SECRET='GA_ONVIF_CAMERA_SECRET_TEST_ONLY'
IP='192.168.45.32'
DEVICE='https://192.168.45.32/onvif/device_service'
MEDIA='https://192.168.45.32/onvif/media_service'
WS='urn:test-workspace'
PATH='onvif_'+'a'*32
E='<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body>{}</s:Body></s:Envelope>'

class EnrolmentTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{SECRET:json.dumps({'username':'fixture','password':'fixture-secret'})})
        self.env.start();self.addCleanup(self.env.stop)

    def test_only_confirmed_ip_and_https_are_allowed(self):
        self.assertEqual(device_url(DEVICE,IP),DEVICE)
        for bad in ['http://192.168.45.32/onvif','https://192.168.45.33/onvif',
                    'https://fixture:secret@192.168.45.32/onvif','https://192.168.45.32/onvif?token=secret']:
            with self.subTest(bad=bad),self.assertRaises(EnrollmentError):device_url(bad,IP)

    def test_grant_is_exact_and_workspace_scoped(self):
        item={'source_ip':IP,'service_address':DEVICE,'credential_ref':SECRET,'gateway_id':'gateway-1','gateway_origin':'http://127.0.0.1:19997'}
        with patch.dict(os.environ,{'GA_ONVIF_ENROLLMENT_GRANTS':json.dumps({WS:[item]})}):
            self.assertEqual(grant(WS,IP,DEVICE,SECRET,'gateway-1',item['gateway_origin']),item)
            with self.assertRaises(EnrollmentError):grant('other',IP,DEVICE,SECRET,'gateway-1',item['gateway_origin'])
            with self.assertRaises(EnrollmentError):grant(WS,IP,DEVICE,SECRET,'gateway-2',item['gateway_origin'])

    def test_onvif_media_one_sequence_and_cross_host_rejection(self):
        calls=[];missing=[None]
        def device(request):
            calls.append(request)
            action=request.headers['content-type']
            if 'GetCapabilities' in action:
                body='<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media><tt:XAddr>'+MEDIA+'</tt:XAddr></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse>'
            elif 'GetProfiles' in action:
                body='<trt:GetProfilesResponse><trt:Profiles token="profile1"><tt:VideoEncoderConfiguration><tt:Encoding>H264</tt:Encoding></tt:VideoEncoderConfiguration></trt:Profiles></trt:GetProfilesResponse>'
            else:
                body='<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://192.168.45.32:554/live</tt:Uri><tt:InvalidAfterConnect>false</tt:InvalidAfterConnect><tt:InvalidAfterReboot>false</tt:InvalidAfterReboot><tt:Timeout>PT0S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse>'
                if missing[0]:body=re.sub(r'<tt:'+missing[0]+r'>[^<]*</tt:'+missing[0]+r'>','',body)
            return httpx.Response(200,content=E.format(body).encode())
        self.assertEqual(stream_uri(DEVICE,IP,SECRET,transport=httpx.MockTransport(device)),'rtsp://192.168.45.32:554/live')
        self.assertEqual(len(calls),3)
        self.assertTrue(all('fixture-secret' not in request.content.decode() for request in calls))
        with self.assertRaises(EnrollmentError):device_url('rtsp://192.168.45.33:554/live',IP,stream=True)
        for field in ('InvalidAfterConnect','InvalidAfterReboot','Timeout'):
            missing[0]=field
            with self.subTest(missing=field),self.assertRaises(EnrollmentError):
                stream_uri(DEVICE,IP,SECRET,transport=httpx.MockTransport(device))

    def test_gateway_add_is_idempotent_and_never_overwrites(self):
        state={};posts=[]
        def gateway(request):
            if '/get/' in request.url.path:
                return httpx.Response(200,json=state) if state else httpx.Response(404)
            posts.append(request)
            state.update(json.loads(request.content));return httpx.Response(200,json={})
        kwargs={'transport':httpx.MockTransport(gateway)}
        uri='rtsp://192.168.45.32:554/live'
        self.assertEqual(configure_gateway('http://127.0.0.1:19997',PATH,uri,SECRET,{},**kwargs),'created')
        self.assertEqual(configure_gateway('http://127.0.0.1:19997',PATH,uri,SECRET,{},**kwargs),'existing')
        self.assertEqual(len(posts),1)
        self.assertEqual(gateway_state('http://127.0.0.1:19997',PATH,uri,SECRET,{},**kwargs),'matches')
        state['source']='rtsp://different'
        self.assertEqual(gateway_state('http://127.0.0.1:19997',PATH,uri,SECRET,{},**kwargs),'conflict')
        with self.assertRaises(EnrollmentError):configure_gateway('http://127.0.0.1:19997',PATH,uri,SECRET,{},**kwargs)
        self.assertEqual(len(posts),1)
        state.clear()
        self.assertEqual(gateway_state('http://127.0.0.1:19997',PATH,uri,SECRET,{},**kwargs),'missing')
        self.assertEqual(configure_gateway('http://127.0.0.1:19997',PATH,uri,SECRET,{},**kwargs),'created')
        self.assertEqual(len(posts),2)

    def test_gateway_errors_do_not_expose_secret_or_camera_url(self):
        def fail(_):raise httpx.ReadTimeout('fixture-secret')
        with self.assertRaises(EnrollmentError) as caught:
            configure_gateway('http://127.0.0.1:19997',PATH,'rtsp://192.168.45.32/live',SECRET,{},transport=httpx.MockTransport(fail))
        self.assertNotIn('fixture-secret',str(caught.exception))
        self.assertNotIn('192.168.45.32',str(caught.exception))

    def test_real_loopback_tls_digest_and_gateway_configuration(self):
        """Real sockets to explicitly synthetic servers, never a user's device."""
        with tempfile.TemporaryDirectory() as directory:
            key=directory+'/key.pem'; cert=directory+'/cert.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                '-keyout',key,'-out',cert,'-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            state={'post_count':0}; secret_ref='GA_ONVIF_CAMERA_SECRET_FIXTURE_TEST'
            class Camera(BaseHTTPRequestHandler):
                def log_message(self,*_):pass
                def do_POST(self):
                    auth=self.headers.get('Authorization','')
                    parts={}
                    for name,quoted,bare in re.findall(r'(\w+)=(?:"([^"]*)"|([^, ]+))',auth):parts[name]=quoted or bare
                    md5=lambda text:hashlib.md5(text.encode()).hexdigest()
                    a1=md5('fixture:ONVIF_FIXTURE:fixture-secret');a2=md5('POST:'+parts.get('uri',''))
                    expected=md5(':'.join((a1,'nonce',parts.get('nc',''),parts.get('cnonce',''),'auth',a2)))
                    if not auth.startswith('Digest ') or parts.get('username')!='fixture' or parts.get('response')!=expected:
                        self.send_response(401);self.send_header('WWW-Authenticate','Digest realm="ONVIF_FIXTURE", nonce="nonce", qop="auth", algorithm=MD5');self.end_headers();return
                    n=int(self.headers.get('Content-Length','0'));self.rfile.read(n);action=self.headers.get('Content-Type','')
                    if 'GetCapabilities' in action:
                        body=f'<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media><tt:XAddr>https://127.0.0.1:{self.server.server_port}/onvif/media</tt:XAddr></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse>'
                    elif 'GetProfiles' in action:
                        body='<trt:GetProfilesResponse><trt:Profiles token="p1"><tt:VideoEncoderConfiguration><tt:Encoding>H264</tt:Encoding></tt:VideoEncoderConfiguration></trt:Profiles></trt:GetProfilesResponse>'
                    else:
                        body='<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://127.0.0.1:18554/synthetic</tt:Uri><tt:InvalidAfterConnect>false</tt:InvalidAfterConnect><tt:InvalidAfterReboot>false</tt:InvalidAfterReboot><tt:Timeout>PT0S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse>'
                    data=E.format(body).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            class Gateway(BaseHTTPRequestHandler):
                def log_message(self,*_):pass
                def do_GET(self):
                    if 'source' not in state:self.send_response(404);self.end_headers();return
                    data=json.dumps({'source':state['source'],'sourceOnDemand':state['sourceOnDemand']}).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                def do_POST(self):
                    state.update(json.loads(self.rfile.read(int(self.headers['Content-Length']))));state['post_count']+=1;self.send_response(200);self.end_headers()
            camera=ThreadingHTTPServer(('127.0.0.1',0),Camera);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key);camera.socket=context.wrap_socket(camera.socket,server_side=True)
            gateway=ThreadingHTTPServer(('127.0.0.1',0),Gateway)
            threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (camera,gateway)]
            for thread in threads:thread.start()
            try:
                env={'GA_ONVIF_ALLOW_LOOPBACK_FIXTURE':'1','GA_ONVIF_TRUSTED_CA_FILE':cert,secret_ref:json.dumps({'username':'fixture','password':'fixture-secret'})}
                with patch.dict(os.environ,env):
                    url=stream_uri(f'https://127.0.0.1:{camera.server_port}/onvif/device','127.0.0.1',secret_ref)
                    api=f'http://127.0.0.1:{gateway.server_port}'
                    self.assertEqual(configure_gateway(api,PATH,url,secret_ref,{}),'created')
                    self.assertEqual(configure_gateway(api,PATH,url,secret_ref,{}),'existing')
                    self.assertEqual(state['post_count'],1)
            finally:
                for server in (camera,gateway):server.shutdown();server.server_close()
                for thread in threads:thread.join()

if __name__=='__main__':unittest.main()
