"""Owned localhost-only synthetic sensing contract. SQLite prevents repeated requests.
Not a device simulator, satellite archive, flight controller or accuracy proof.
"""
import json,sqlite3
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
from datetime import datetime,timezone
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];DATABASE=ROOT/'.runtime/synthetic-sensor.sqlite3'
with sqlite3.connect(DATABASE) as db:
    db.execute('CREATE TABLE IF NOT EXISTS request_attempts (id INTEGER PRIMARY KEY,request_key TEXT,asset_external_id TEXT,created_at TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS requests (request_key TEXT PRIMARY KEY,input TEXT,receipt TEXT,created_at TEXT)')
DATABASE.chmod(0o600)
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def reply(self,value,status=200):
        encoded=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    def do_GET(self):
        if self.path=='/health':self.reply({'synthetic_only':True});return
        if urlparse(self.path).path!='/receipt':self.reply({'error':'unsupported'},404);return
        key=parse_qs(urlparse(self.path).query).get('request_key',[''])[0]
        with sqlite3.connect(DATABASE) as db:row=db.execute('SELECT receipt FROM requests WHERE request_key=?',(key,)).fetchone()
        if not row:self.reply({'error':'not_received'},404);return
        self.reply(json.loads(row[0]))
    def do_POST(self):
        if self.path!='/observe':self.reply({'error':'unsupported'},404);return
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=16384:self.reply({'error':'invalid_length'},422);return
        body=json.loads(self.rfile.read(length));required={'request_key','target_external_id','fields','asset_external_id','action','max_cost_minor','currency','max_duration_seconds'}
        if set(body)!=required or self.headers.get('Idempotency-Key')!=body['request_key'] or body['currency']!='CNY' or body['max_cost_minor']!=0:
            self.reply({'error':'synthetic_contract_budget_scope'},422);return
        receipt={'receipt_id':uuid4().hex,'request_key':body['request_key'],'asset_external_id':body['asset_external_id'],
            'status':'rejected' if body['asset_external_id'].startswith('synthetic-reject-') else 'completed',
            'terminal':True,'no_future_device_activity':True,
            'no_future_observation':body['asset_external_id'].startswith('synthetic-reject-'),'synthetic_only':True}
        with sqlite3.connect(DATABASE) as db:
            db.execute('INSERT INTO request_attempts (request_key,asset_external_id,created_at) VALUES (?,?,?)',(body['request_key'],body['asset_external_id'],datetime.now(timezone.utc).isoformat()))
            row=db.execute('SELECT input,receipt FROM requests WHERE request_key=?',(body['request_key'],)).fetchone()
            if row:
                if json.loads(row[0])!=body:self.reply({'error':'conflicting_key'},409);return
                receipt=json.loads(row[1])
            else:db.execute('INSERT INTO requests VALUES (?,?,?,?)',(body['request_key'],json.dumps(body),json.dumps(receipt),datetime.now(timezone.utc).isoformat()))
        # Synthetic timeout happens after durable acceptance. GET reveals the same original receipt.
        if body['asset_external_id'].startswith('synthetic-unknown-'):
            self.connection.close();return
        self.reply(receipt)
if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',8888),Handler).serve_forever()
