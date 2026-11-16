#!/usr/bin/env python3
"""Synthetic ONVIF Media-1 camera; loopback only, never a real camera.

Creates short-lived test-only credentials and TLS CA under a 0700 directory.
Only local, explicitly configured RTSP relay is referenced. Never prints the
credentials or stream URI; all HTTP request logging is disabled.
"""
import hashlib
import json
import os
import re
import secrets
import ssl
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PRIVATE=Path.home()/'.codex/forestry-public-testing-tools/onvif-fixture'
PRIVATE.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(PRIVATE,0o700);os.umask(0o077)
KEY=PRIVATE/'test-key.pem';CERT=PRIVATE/'test-ca.pem';CREDS=PRIVATE/'test-credentials.json'
if not KEY.exists() or not CERT.exists():
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','2',
        '-keyout',str(KEY),'-out',str(CERT),'-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'],
        check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
if not CREDS.exists():CREDS.write_text(json.dumps({'username':'fixture_user','password':secrets.token_urlsafe(24)}))
for item in (KEY,CERT,CREDS):os.chmod(item,0o600)
fixture=json.loads(CREDS.read_text());PORT=int(os.getenv('GA_ONVIF_FIXTURE_HTTPS_PORT','18893'))
RTSP_PORT=int(os.getenv('GA_ONVIF_FIXTURE_RTSP_PORT','18554'));RTSP_PATH=os.getenv('GA_ONVIF_FIXTURE_RTSP_PATH','synthetic')
if not (1<=PORT<=65535 and 1<=RTSP_PORT<=65535 and re.fullmatch(r'[A-Za-z0-9_/-]{1,120}',RTSP_PATH)):
    raise ValueError('invalid synthetic fixture settings')
E='<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body>{}</s:Body></s:Envelope>'

class Camera(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_POST(self):
        if self.client_address[0]!='127.0.0.1':self.send_error(403);return
        auth=self.headers.get('Authorization','');parts={}
        for name,quoted,bare in re.findall(r'(\w+)=(?:"([^"]*)"|([^, ]+))',auth):parts[name]=quoted or bare
        md5=lambda value:hashlib.md5(value.encode()).hexdigest()
        a1=md5(fixture['username']+':ONVIF_FIXTURE:'+fixture['password']);a2=md5('POST:'+parts.get('uri',''))
        expected=md5(':'.join((a1,'synthetic-nonce',parts.get('nc',''),parts.get('cnonce',''),'auth',a2)))
        if not auth.startswith('Digest ') or parts.get('username')!=fixture['username'] or parts.get('response')!=expected:
            self.send_response(401);self.send_header('WWW-Authenticate','Digest realm="ONVIF_FIXTURE", nonce="synthetic-nonce", qop="auth", algorithm=MD5');self.end_headers();return
        length=int(self.headers.get('Content-Length','0'))
        if length>32768:self.send_error(413);return
        self.rfile.read(length);action=self.headers.get('Content-Type','')
        if 'GetCapabilities' in action:
            body=f'<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media><tt:XAddr>https://127.0.0.1:{PORT}/onvif/media</tt:XAddr></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse>'
        elif 'GetProfiles' in action:
            body='<trt:GetProfilesResponse><trt:Profiles token="fixture-h264"><tt:VideoEncoderConfiguration><tt:Encoding>H264</tt:Encoding></tt:VideoEncoderConfiguration></trt:Profiles></trt:GetProfilesResponse>'
        elif 'GetStreamUri' in action:
            body=f'<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://127.0.0.1:{RTSP_PORT}/{RTSP_PATH}</tt:Uri><tt:InvalidAfterConnect>false</tt:InvalidAfterConnect><tt:InvalidAfterReboot>false</tt:InvalidAfterReboot><tt:Timeout>PT0S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse>'
        else:self.send_error(404);return
        data=E.format(body).encode();self.send_response(200);self.send_header('Content-Type','application/soap+xml');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

server=ThreadingHTTPServer(('127.0.0.1',PORT),Camera);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(CERT,KEY);server.socket=context.wrap_socket(server.socket,server_side=True)
print('Synthetic ONVIF loopback fixture ready',flush=True)
server.serve_forever()
