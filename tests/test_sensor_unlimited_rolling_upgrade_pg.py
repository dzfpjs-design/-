"""Old→new API and analysis-thread processes on one disposable PG instance.

No setup-worker, device, external network or real cloud request is involved.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job
from tests.test_sensor_native_analysis_pg import setup_analysis
from tests.test_sensor_unlimited_worker_restart_pg import device, reject_non_loopback_network, WORKER, wait_until, stop, state


def test_legacy_api_worker_upgrade_keeps_requests_and_never_resends(client,account,device,monkeypatch,tmp_path):
    old=Path(os.environ['SENSOR_OLD_SOURCE']).resolve()
    new=Path(__file__).resolve().parents[1]
    assert old != new and (old/'src/generic_agents/sensor_native_analysis.py').is_file()
    fence=tmp_path/'fence';fence.mkdir()
    (fence/'sitecustomize.py').write_text('''import socket\n_original=socket.socket.connect\ndef local_only(self,address):\n if self.family in (socket.AF_INET,socket.AF_INET6) and (not isinstance(address,tuple) or address[0] not in ("127.0.0.1","::1")): raise PermissionError("non-loopback forbidden")\n return _original(self,address)\nsocket.socket.connect=local_only\n''')
    env=os.environ.copy();env.update(GA_CLOUD_ALLOWED_PROVIDERS='deepseek',DEEPSEEK_API_KEY='synthetic-only',
        DASHSCOPE_API_KEY='',GA_STORAGE_DIR=str(settings().storage_dir),GA_COOKIE_SECURE='false')
    # Explicit identity ensures children cannot use an application DB or credentials.
    assert 'ga_test_' in env['GA_DATABASE_URL'] and '127.0.0.1:' in env['GA_DATABASE_URL']
    assert os.environ['GA_TEST_PG_SYSTEM_IDENTIFIER'].isdecimal()
    monkeypatch.setattr('generic_agents.modbus_readonly_transport.read_registers',lambda *args:[25])
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    processes=[]
    def run(release,kind,mode=None):
        e={**env,'PYTHONPATH':str(fence)+':'+str(release/'src')+':'+str(release)}
        if kind=='api': args=[sys.executable,'-m','uvicorn','generic_agents.api:app','--host','127.0.0.1','--port',str(port)]
        else: args=[sys.executable,'-c',WORKER,mode,account[2],str(tmp_path/'calls')]
        output=open(tmp_path/(kind+'-'+str(len(processes))+'.log'),'w')
        p=subprocess.Popen(args,cwd=release,env=e,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        output.close();processes.append(p);return p
    http=httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=3)
    def ready(process):
        def check():
            assert process.poll() is None, 'API exited before readiness'
            try:return http.get('/health').status_code == 200
            except httpx.HTTPError:return False
        wait_until(check)
    try:
        print('stage: old API startup and synthetic login',flush=True)
        old_api=run(old,'api');ready(old_api)
        login=http.post('/api/login',json={'username':account[0],'password':account[1]})
        assert login.status_code==200,login.text
        http.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        payload={'request_id':str(uuid4()),'packet_id':packet['id'],'model_id':model,
            'max_output_tokens':256,'max_cost_fen':5}
        r=http.post(base+'/analyses/preview',json=payload);assert r.status_code==200,r.text
        old_preview=r.json();assert old_preview['quote'].get('schema')!='sensor-native-unlimited-quote-v2'
        old_req={'request_id':str(uuid4()),'preview_hash':old_preview['preview_hash'],'consent':True}
        r=http.post(base+'/analyses/'+old_preview['preview_id']+'/send',json=old_req);assert r.status_code==200,r.text
        old_id=r.json()['analysis']['id']
        with Session(engine()) as db:assert db.get(Job,old_id).kind=='sensor_native_analysis_v1'
        print('stage: old analysis thread saved first synthetic answer',flush=True)
        old_worker=run(old,'worker','save')
        wait_until(lambda: state(old_id)=='response_saved' or (old_worker.poll() is not None and (_ for _ in ()).throw(AssertionError('old worker exited'))))
        stop(old_worker);stop(old_api)
        assert (tmp_path/'calls').read_text().splitlines()==['request']
        print('stage: new API and new analysis thread recover old request',flush=True)
        engine().dispose();new_api=run(new,'api');ready(new_api)
        assert http.get(base+'/analyses/requests/'+old_req['request_id']).json()['state']=='response_saved'
        new_worker=run(new,'worker','recover')
        wait_until(lambda: state(old_id)=='completed' or (new_worker.poll() is not None and (_ for _ in ()).throw(AssertionError('new worker exited'))))
        stop(new_worker)
        assert http.get(base+'/analyses/requests/'+old_req['request_id']).json()['state']=='completed'
        assert (tmp_path/'calls').read_text().splitlines()==['request']
        print('stage: new unlimited request and killed analysis thread',flush=True)
        payload={'request_id':str(uuid4()),'packet_id':packet['id'],'model_id':model,'max_output_tokens':256}
        r=http.post(base+'/analyses/preview',json=payload);assert r.status_code==200,r.text
        preview=r.json();assert preview['quote']['schema']=='sensor-native-unlimited-quote-v2'
        req={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
        r=http.post(base+'/analyses/'+preview['preview_id']+'/send',json=req);assert r.status_code==200,r.text
        identity=r.json()['analysis']['id']
        with Session(engine()) as db:assert db.get(Job,identity).kind=='sensor_native_analysis_unlimited_v2'
        worker=run(new,'worker','crash')
        wait_until(lambda: len((tmp_path/'calls').read_text().splitlines())==2)
        stop(worker,kill=True);assert state(identity)=='dispatching'
        stop(new_api);engine().dispose()
        print('stage: new API and analysis thread restart with unknown request',flush=True)
        new_api=run(new,'api');ready(new_api)
        worker=run(new,'worker','recover')
        time.sleep(7);assert worker.poll() is None
        assert http.get(base+'/analyses/requests/'+req['request_id']).json()['state']=='response_unknown'
        assert http.get(base+'/analyses/requests/'+old_req['request_id']).json()['state']=='completed'
        assert (tmp_path/'calls').read_text().splitlines()==['request','request']
        with Session(engine()) as db:assert len(list(db.scalars(select(CloudCall))))==2
        print('stage: verified without additional synthetic sends',flush=True)
    finally:
        http.close()
        for p in reversed(processes):stop(p)
