"""Independent localhost-only synthetic HTTP/MCP validation service, not an industry model.

Run explicitly on port 8877. Modes come only from a local checkpoint file.
SQLite receipts persist across service restarts; no credentials or domain datasets.
"""
import json
import sqlite3
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from uuid import uuid4
from datetime import datetime,timezone
import hashlib

ROOT=Path(__file__).resolve().parents[1]
MODE=ROOT/'.runtime/reference-tools-mode.json'
DATABASE=ROOT/'.runtime/reference-tools.sqlite3'
INPUT={'type':'object','additionalProperties':False,'required':['records','field','request_key'],
    'properties':{'records':{'type':'array','title':'已授权对象档案','items':{'type':'object'}},
        'field':{'type':'string','title':'待分析字段'},'request_key':{'type':'string','title':'服务调用编号'}}}
OUTPUT={'type':'object','additionalProperties':False,'required':['value','samples','method','receipt_id'],
    'properties':{'value':{'type':'number','title':'档案均值'},'samples':{'type':'integer','title':'参与计算对象数'},
        'method':{'type':'string','title':'方法'},'receipt_id':{'type':'string','title':'服务回执编号'}}}


def result(inputs):
    values=[row['attributes'][inputs['field']] for row in inputs['records']]
    fingerprint=hashlib.sha256(json.dumps(inputs,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    with sqlite3.connect(DATABASE) as db:
        db.execute('CREATE TABLE IF NOT EXISTS receipts (request_key TEXT PRIMARY KEY, input_hash TEXT, output TEXT, created_at TEXT)')
        prior=db.execute('SELECT input_hash,output FROM receipts WHERE request_key=?',(inputs['request_key'],)).fetchone()
        if prior:
            if prior[0]!=fingerprint:raise ValueError('Conflicting receipt key')
            return json.loads(prior[1])
        output={'value':sum(values)/len(values),'samples':len(values),'method':'independent_synthetic_archive_mean','receipt_id':uuid4().hex}
        db.execute('INSERT INTO receipts VALUES (?,?,?,?)',(inputs['request_key'],fingerprint,json.dumps(output),datetime.now(timezone.utc).isoformat()))
    DATABASE.chmod(0o600);return output


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,value,status=200,headers=None):
        data=json.dumps(value).encode() if value is not None else b''
        self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path=='/health':self.respond({'scope':'synthetic local protocol service'});return
        if self.path=='/receipts':
            key=self.headers.get('Idempotency-Key','')
            with sqlite3.connect(DATABASE) as db:
                row=db.execute('SELECT input_hash,output FROM receipts WHERE request_key=?',(key,)).fetchone()
            if not row:self.respond({'error':'receipt not available'},404);return
            self.respond({'idempotency_key':key,'input_hash':row[0],'status':'succeeded','output':json.loads(row[1])});return
        self.respond({'error':'unsupported'},405)
    def do_DELETE(self):self.respond(None,204)
    def do_POST(self):
        if self.headers.get('Origin') not in {None,'http://127.0.0.1:8765'}:
            self.respond({'error':'unapproved origin'},403);return
        length=int(self.headers.get('Content-Length','0'))
        if length>1024*1024:self.respond({'error':'too large'},413);return
        body=json.loads(self.rfile.read(length));identifier=body.get('id')
        if self.path=='/mcp':
            method=body['method']
            if method=='initialize':
                self.respond({'jsonrpc':'2.0','id':identifier,'result':{'protocolVersion':'2025-11-25','capabilities':{'tools':{}},
                    'serverInfo':{'name':'independent-synthetic-service','version':'1'}}},headers={'MCP-Session-Id':'validation-'+uuid4().hex});return
            if method=='notifications/initialized':self.respond(None,202);return
            if not self.headers.get('MCP-Session-Id') or self.headers.get('MCP-Protocol-Version')!='2025-11-25':
                self.respond({'error':'session required'},400);return
            if method=='tools/list':
                self.respond({'jsonrpc':'2.0','id':identifier,'result':{'tools':[{'name':'archive_mean','description':'Synthetic archive mean protocol probe',
                    'inputSchema':INPUT,'outputSchema':OUTPUT,'annotations':{'readOnlyHint':True}}]}});return
            if method!='tools/call' or body['params']['name']!='archive_mean':self.respond({'error':'unsupported'},400);return
            inputs=body['params']['arguments']
        elif self.path=='/analyze':inputs=body
        else:self.respond({'error':'unsupported'},404);return
        mode=json.loads(MODE.read_text()).get(self.path,'normal') if MODE.exists() else 'normal'
        if mode=='fail':self.respond({'error':'synthetic outage'},503);return
        output=result(inputs)
        if mode=='unknown_after_commit':self.connection.close();return
        if mode=='bad_output':output={'unsupported':True}
        self.respond({'jsonrpc':'2.0','id':identifier,'result':{'structuredContent':output,'content':[]}}
            if self.path=='/mcp' else output)


if __name__=='__main__':
    print('localhost:8877 independent synthetic tool service',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8877),Handler).serve_forever()
