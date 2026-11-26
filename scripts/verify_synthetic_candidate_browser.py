#!/usr/bin/env python3
"""One isolated synthetic real-browser check; no cloud or real data.

macOS cannot nest Chrome's sandbox inside sandbox-exec. This synthetic test
uses Chrome --no-sandbox inside an outer network sandbox; it does not establish
production browser security. Build the runtime first and pass local binaries.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx
from websockets.sync.client import connect

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pg-bin',type=Path,required=True)
parser.add_argument('--chrome',type=Path,required=True)
args=parser.parse_args()
REPO=Path(__file__).resolve().parents[1]
PG=args.pg_bin.resolve(strict=True)
PY=Path(sys.executable).absolute()
CHROME=args.chrome.resolve(strict=True)
ANCHOR=REPO/'scripts/synthetic_process_anchor.py'
if (REPO/'.env').exists() or not (REPO/'design/resource-studio/dist/runtime/index.html').is_file():
    raise RuntimeError('requires an isolated checkout without .env and a runtime build')

def port():
    for _ in range(50):
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));p=s.getsockname()[1]
        if p not in (8766,55486) and p not in selected:
            selected.add(p);return p
    raise RuntimeError('no unique test port')

def wait_url(url, timeout=35):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        try:
            r=httpx.get(url,timeout=1,trust_env=False)
            if r.status_code<500:return r
        except Exception:pass
        time.sleep(.2)
    raise RuntimeError('test listener unavailable')

class CDP:
    def __init__(self,url):self.ws=connect(url,open_timeout=10,max_size=4_000_000);self.n=0
    def call(self,method,params=None):
        self.n+=1;n=self.n;self.ws.send(json.dumps({'id':n,'method':method,'params':params or {}}))
        while True:
            value=json.loads(self.ws.recv(timeout=30))
            if value.get('id')==n:
                if 'error' in value:raise RuntimeError('browser command failed')
                return value['result']
    def eval(self,expr):
        result=self.call('Runtime.evaluate',{'expression':expr,'returnByValue':True,'awaitPromise':True})
        if result.get('exceptionDetails'):raise RuntimeError('browser page evaluation failed')
        return result.get('result',{}).get('value')
    def until(self,expr,timeout=45):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.eval(expr):return
            time.sleep(.25)
        text=self.eval('document.body.innerText.slice(0,700)')
        raise RuntimeError('browser expected state missing: '+str(text))
    def click(self,label):
        result=self.eval("(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==="+json.dumps(label,ensure_ascii=False)+");if(!b||b.disabled)return false;b.click();return true})()")
        if not result:raise RuntimeError('button unavailable: '+label)

def anchored(command,profile,env,log):
    read_fd,write_fd=os.pipe()
    process=subprocess.Popen(['/usr/bin/sandbox-exec','-p',profile,str(PY),str(ANCHOR),str(write_fd),*map(str,command)],
        cwd=REPO,env=env,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=(write_fd,))
    os.close(write_fd);os.close(read_fd)
    return process

def stop(process):
    if process is None:return
    if process.poll() is None:
        try:process.stdin.write(b'S');process.stdin.flush();process.stdin.close()
        except (BrokenPipeError,OSError):pass
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:raise RuntimeError('test supervisor did not stop')
    deadline=time.monotonic()+5
    while True:
        groups=subprocess.check_output(['/bin/ps','-ax','-o','pgid='],text=True,timeout=2).split()
        if not groups or any(not item.isdecimal() for item in groups):raise RuntimeError('cannot verify test process exit')
        if str(process.pid) not in groups:return
        if time.monotonic()>=deadline:raise RuntimeError('test process exit remains unconfirmed')
        time.sleep(.05)

selected=set();pg_port,app_port,cdp_port=port(),port(),port()
root=Path(tempfile.mkdtemp(prefix='candidate-browser-isolated-'));os.chmod(root,0o700)
data=root/'data';storage=root/'storage';storage.mkdir(mode=0o700);db='ga_test_'+uuid4().hex[:12]
username='candidate_'+uuid4().hex[:10];password=secrets.token_urlsafe(24)
base={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','LC_ALL') if k in os.environ}
base.update(PYTHONPATH=str(REPO/'src')+':'+str(REPO),GA_DATABASE_URL=f'postgresql+psycopg://advice_test@127.0.0.1:{pg_port}/{db}',
 GA_REDIS_URL='redis://127.0.0.1:1/0',GA_STORAGE_DIR=str(storage),GA_COOKIE_SECURE='false',
 GA_CLOUD_ALLOWED_PROVIDERS='',GA_ALLOWED_ORIGINS=f'http://127.0.0.1:{app_port}',
 GA_SETUP_BUILD_DIR=str(REPO/'design/resource-studio/dist/runtime'),DEEPSEEK_API_KEY='',DASHSCOPE_API_KEY='',
 TEST_USERNAME=username,TEST_PASSWORD=password,TEST_ROOT=str(root),TEST_PG_PORT=str(pg_port))
pg_running=False;api_process=None;browser_process=None;log=None;ws=None
try:
    subprocess.run([PG/'initdb','-D',data,'-U','advice_test','-A','trust','--no-locale','-E','UTF8'],env=base,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
    subprocess.run([PG/'pg_ctl','-D',data,'-l',root/'pg.log','-o',f'-h 127.0.0.1 -p {pg_port} -k {root}','-w','start'],env=base,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60);pg_running=True
    subprocess.run([PG/'createdb','-h','127.0.0.1','-p',str(pg_port),'-U','advice_test',db],env=base,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
    seed=REPO/'tests'/'synthetic_candidate_browser_seed.py'
    profile=f'(version 1) (allow default) (deny network*) (allow network-outbound (remote ip "localhost:{pg_port}"))'
    with (root/'seed.log').open('w') as seedlog:
        subprocess.run(['/usr/bin/sandbox-exec','-p',profile,PY,seed],env=base,cwd=REPO,check=True,stdout=seedlog,stderr=subprocess.STDOUT,timeout=90)
    runtime_env={key:value for key,value in base.items() if key not in {'TEST_USERNAME','TEST_PASSWORD'}}
    log=(root/'service.log').open('w');api_profile=f'(version 1) (allow default) (deny network*) (allow network-inbound (local ip "localhost:{app_port}")) (allow network-outbound (remote ip "localhost:{pg_port}"))'
    api_process=anchored([PY,'-m','uvicorn','generic_agents.api:app','--host','127.0.0.1','--port',str(app_port),'--log-level','error'],api_profile,runtime_env,log)
    wait_url(f'http://127.0.0.1:{app_port}/setup/')
    browser_log=(root/'browser.log').open('w');browser_profile=f'(version 1) (allow default) (deny network*) (allow network-bind (local unix-socket)) (allow network-inbound (local unix-socket)) (allow network-outbound (remote unix-socket (subpath "{root.resolve()}"))) (allow network-inbound (local ip "localhost:{cdp_port}")) (allow network-outbound (remote ip "localhost:{app_port}")) (allow network-outbound (remote ip "localhost:{cdp_port}"))'
    browser_env={key:base[key] for key in ('PATH','HOME','LANG','LC_ALL') if key in base};browser_env['TMPDIR']=str(root.resolve())
    browser_process=anchored([CHROME,'--headless=new','--no-sandbox','--no-first-run','--disable-background-networking','--disable-sync','--disable-extensions','--disable-gpu',f'--user-data-dir={root}/chrome',f'--remote-debugging-port={cdp_port}','about:blank'],browser_profile,browser_env,browser_log)
    pages=wait_url(f'http://127.0.0.1:{cdp_port}/json/list').json();ws=CDP(pages[0]['webSocketDebuggerUrl']);ws.call('Page.enable');ws.call('Runtime.enable');ws.call('Emulation.setDeviceMetricsOverride',{'width':1365,'height':900,'deviceScaleFactor':1,'mobile':False})
    ws.call('Page.navigate',{'url':f'http://127.0.0.1:{app_port}/setup/?view=business'})
    ws.until("!!document.querySelector('input[autocomplete=username]')")
    ws.eval("(()=>{const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;for(const [sel,val] of "+json.dumps([['input[autocomplete=username]',username],['input[type=password]',password]])+"){const e=document.querySelector(sel);set.call(e,val);e.dispatchEvent(new Event('input',{bubbles:true}));}})()")
    ws.click('登录');ws.until("document.body.innerText.includes('已整理的候选')")
    ws.click('查看候选');ws.until("document.body.innerText.includes('合成检查')")
    ws.until("!!document.querySelector('dialog[open] .business-candidate-list')")
    (root/'candidate-preview.png').write_bytes(base64.b64decode(ws.call('Page.captureScreenshot',{'format':'png'})['data']))
    ws.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
    ws.call('Input.dispatchKeyEvent',{'type':'keyUp','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
    ws.until("!document.querySelector('dialog[open]')")
    ws.click('查看候选');ws.until("!!document.querySelector('dialog[open] .business-candidate-list')")
    ws.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':False})
    ws.until("(()=>{const d=document.querySelector('dialog[open]').getBoundingClientRect();return d.left>=0&&d.right<=innerWidth&&d.top>=0&&d.bottom<=innerHeight})()")
    (root/'candidate-narrow.png').write_bytes(base64.b64decode(ws.call('Page.captureScreenshot',{'format':'png'})['data']))
    ws.call('Emulation.setDeviceMetricsOverride',{'width':1365,'height':900,'deviceScaleFactor':1,'mobile':False})
    ws.click('全部加入业务清单')
    ws.until("!document.querySelector('dialog[open]')")
    ws.until("(async()=>{const r=await fetch('/api/setup/draft',{headers:{'X-Workspace-ID':sessionStorage.getItem('ga-setup-csrf-workspace')}});if(!r.ok)return false;const d=await r.json(),f=d.configuration.flows?.['合成检查'];return !!f&&!f.confirmedSignature&&f.steps.some(s=>s.candidateUnresolvedSources?.length)&&d.configuration.flows?.['已有业务']?.trigger==='既有触发'})()")
    ws.until("document.body.innerText.includes('依据待核')")
    assert ws.eval("[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='确认当前业务流程')?.disabled") is True
    ws.eval("document.querySelector('.setup-workbench').scrollIntoView({block:'start'})")
    (root/'candidate-after-save.png').write_bytes(base64.b64decode(ws.call('Page.captureScreenshot',{'format':'png'})['data']))
    ws.call('Page.navigate',{'url':'about:blank'});ws.until("location.href==='about:blank'")
    ws.call('Page.navigate',{'url':f'http://127.0.0.1:{app_port}/setup/?view=business'});ws.until("document.body.innerText.includes('合成检查')");ws.eval("[...document.querySelectorAll('button.category')].find(x=>x.innerText.includes('合成检查')).click()");ws.until("document.body.innerText.includes('依据待核')")
    ws.click('退出');ws.until("!!document.querySelector('input[autocomplete=username]')")
    ws.eval("(()=>{const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;for(const [sel,val] of "+json.dumps([['input[autocomplete=username]',username],['input[type=password]',password]])+"){const e=document.querySelector(sel);set.call(e,val);e.dispatchEvent(new Event('input',{bubbles:true}));}})()")
    ws.click('登录');ws.until("document.body.innerText.includes('合成检查')");ws.eval("[...document.querySelectorAll('button.category')].find(x=>x.innerText.includes('合成检查')).click()");ws.until("document.body.innerText.includes('依据待核')")
    stop(api_process);api_process=None
    subprocess.run([PG/'pg_ctl','-D',data,'-m','fast','-w','stop'],env=base,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60);pg_running=False
    subprocess.run([PG/'pg_ctl','-D',data,'-l',root/'pg.log','-o',f'-h 127.0.0.1 -p {pg_port} -k {root}','-w','start'],env=base,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60);pg_running=True
    api_process=anchored([PY,'-m','uvicorn','generic_agents.api:app','--host','127.0.0.1','--port',str(app_port),'--log-level','error'],api_profile,runtime_env,log)
    wait_url(f'http://127.0.0.1:{app_port}/setup/')
    ws.call('Page.navigate',{'url':'about:blank'});ws.until("location.href==='about:blank'")
    ws.call('Page.navigate',{'url':f'http://127.0.0.1:{app_port}/setup/?view=business'});ws.until("document.body.innerText.includes('合成检查')");ws.eval("[...document.querySelectorAll('button.category')].find(x=>x.innerText.includes('合成检查')).click()");ws.until("document.body.innerText.includes('依据待核')")
    ws.until("(async()=>{const r=await fetch('/api/setup/draft',{headers:{'X-Workspace-ID':sessionStorage.getItem('ga-setup-csrf-workspace')}});if(!r.ok)return false;const d=await r.json(),f=d.configuration.flows?.['合成检查'];return !!f&&!f.confirmedSignature&&f.steps.some(s=>s.candidateUnresolvedSources?.length)})()")
    print('PASS: Chrome login, preview, add, server persistence, reload, relogin and API/PG restart; unresolved evidence blocks confirmation')
finally:
    if ws:
        try:ws.ws.close()
        except Exception:pass
    try:stop(browser_process)
    finally:
        try:stop(api_process)
        finally:
            if pg_running:subprocess.run([PG/'pg_ctl','-D',data,'-m','fast','-w','stop'],env=base,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,check=True)
    print('Isolated browser, service and PostgreSQL stopped; synthetic artifacts retained at',root)
