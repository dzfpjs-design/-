"""Full setup-worker upgrade against synthetic data on an OS-isolated PG.

External and device network access must be denied by the runner sandbox.
"""
import os
import errno
import pytest
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
from tests.test_local_specialists import mocked_runtime
from tests.test_sensor_native_analysis_pg import setup_analysis
from tests.test_sensor_unlimited_worker_restart_pg import device, reject_non_loopback_network, WORKER, wait_until, stop, state


def require_network_fence():
    # A loopback port other than the dedicated PG/API must be blocked by the OS;
    # unlike Python monkeypatches, this restriction is inherited by subprocesses.
    with socket.socket() as sock:
        assert sock.connect_ex(('127.0.0.1',1)) == errno.EPERM, 'OS network fence is not active'


@pytest.mark.skipif(os.environ.get('GA_TEST_OS_NETWORK_FENCED') != '1', reason='requires explicit isolated OS network fence')
def test_full_setup_worker_upgrade_keeps_requests_and_never_resends(client,account,device,monkeypatch,tmp_path):
    require_network_fence()
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
    draft=client.get('/api/setup/draft'); assert draft.status_code == 200, (draft.status_code,draft.json())
    _,base,packet,model=setup_analysis(client,account,device,monkeypatch)
    before_context=client.get(base+'/context'); assert before_context.status_code==200
    assert any(r['kind']=='data' for r in before_context.json()['records'])
    port=int(os.environ['GA_TEST_API_PORT']); assert 1024 < port < 65536
    processes=[]
    def run(release,kind,mode=None):
        e={**env,'PYTHONPATH':str(fence)+':'+str(release/'src')+':'+str(release)}
        if kind=='api': args=[sys.executable,'-m','uvicorn','generic_agents.api:app','--host','127.0.0.1','--port',str(port)]
        else:
            tail='stop=threading.Event()\nsvc.start_workspace_analyzer(workspace,stop)\nwhile True: time.sleep(1)'
            assert WORKER.count(tail)==1
            setup_worker=WORKER.replace(tail,"""from generic_agents import local_observations
import traceback
original_schedule = local_observations.schedule
def traced_schedule(*args, **kwargs):
 try: return original_schedule(*args, **kwargs)
 except Exception: traceback.print_exc(); raise
local_observations.schedule = traced_schedule
from generic_agents.worker import run_setup
raise SystemExit(run_setup(workspace))""")
            args=[sys.executable,'-c',setup_worker,mode,account[2],str(tmp_path/'calls')]
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
        print('stage: old full setup worker saved first synthetic answer',flush=True)
        old_worker=run(old,'worker','save')
        wait_until(lambda: state(old_id)=='response_saved' or (old_worker.poll() is not None and (_ for _ in ()).throw(AssertionError('old worker exited'))))
        stop(old_worker);stop(old_api)
        assert (tmp_path/'calls').read_text().splitlines()==['request']
        print('stage: new API and full setup worker recover old request',flush=True)
        engine().dispose();new_api=run(new,'api');ready(new_api)
        assert http.get(base+'/analyses/requests/'+old_req['request_id']).json()['state']=='response_saved'
        new_worker=run(new,'worker','recover')
        wait_until(lambda: state(old_id)=='completed' or (new_worker.poll() is not None and (_ for _ in ()).throw(AssertionError('new worker exited'))))
        stop(new_worker)
        old_completed=http.get(base+'/analyses/requests/'+old_req['request_id']).json()
        assert old_completed['state']=='completed'
        assert (tmp_path/'calls').read_text().splitlines()==['request']
        print('stage: new unlimited request and killed full setup worker',flush=True)
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
        print('stage: new API and full setup worker restart with unknown request',flush=True)
        new_api=run(new,'api');ready(new_api)
        worker=run(new,'worker','recover')
        time.sleep(7);assert worker.poll() is None
        assert http.get(base+'/analyses/requests/'+req['request_id']).json()['state']=='response_unknown'
        assert http.get(base+'/analyses/requests/'+old_req['request_id']).json()==old_completed
        replay=http.post(base+'/analyses/'+preview['preview_id']+'/send',json=req)
        assert replay.status_code==200 and replay.json()['state']=='response_unknown'
        assert (tmp_path/'calls').read_text().splitlines()==['request','request']
        with Session(engine()) as db:assert len(list(db.scalars(select(CloudCall))))==2
        after_context=http.get(base+'/context'); assert after_context.status_code==200
        assert after_context.json()['records']==before_context.json()['records']
        print('stage: verified without additional synthetic sends',flush=True)
    finally:
        http.close()
        for p in reversed(processes):stop(p)


@pytest.mark.skipif(os.environ.get('GA_TEST_OS_NETWORK_FENCED') != '1', reason='requires explicit isolated OS network fence')
def test_unclocked_candidates_do_not_crash_or_starve_dated_local_daily(client, account, mocked_runtime):
    """Unknown dates remain unknown and are excluded before the bounded group scan."""
    require_network_fence()
    from generic_agents.local_observations import schedule
    import copy
    from generic_agents.models import Capability, Event, Observation, Workspace
    from tests.test_local_observations import setup as local_setup, finish as local_finish
    release, obj, base, _, row = local_setup(client)
    local_finish(client, base, row)
    with Session(engine()) as db:
        local = db.get(Observation, row['id'])
        assert local.business_day is not None
        local_source, local_day = local.source_id, local.business_day
        original_source=db.get(Capability,local_source)
        other=Workspace(name='synthetic other workspace');db.add(other);db.flush()
        other_source=Capability(workspace_id=other.id,namespace='synthetic_foreign',name='foreign',
            release_version='1',kind='source',status='published',content_hash=original_source.content_hash,
            validated_hash=original_source.validated_hash,data=copy.deepcopy(original_source.data))
        db.add(other_source);db.flush();other_workspace,other_source_id=other.id,other_source.id
        foreign=Observation(workspace_id=other_workspace,source_id=other_source_id,source_record_id='foreign',
            processing_version='1',observed_at=None,business_day=local_day,status='processed',
            data={'candidate_only':True,'extracted':{}})
        db.add(foreign)
        missing_ids=[]
        for i in range(105):
            source = Capability(workspace_id=account[2], namespace='synthetic_unclocked_test', name='s'+str(i),
                release_version='1',kind='source',status='published',content_hash='a'*64,
                validated_hash='a'*64,data={})
            db.add(source);db.flush()
            obs=Observation(workspace_id=account[2],source_id=source.id,source_record_id='sensor-'+str(i),
                processing_version='1',entity_id=obj['id'],observed_at=None,business_day=None,
                status='extracted_unclocked',data={'schema':('opcua-unverified-observation-v1' if i==0 else 'modbus-unclocked-observation-v1'),'candidate_only':True})
            db.add(obs);db.flush();missing_ids.append(obs.id)
        db.commit()
    schedule(workspace_id=account[2]); schedule(workspace_id=account[2])
    with Session(engine()) as db:
        rows=list(db.scalars(select(Observation).where(Observation.id.in_(missing_ids))))
        assert len(rows)==105 and all(r.business_day is None and r.observed_at is None for r in rows)
        assert {r.data['schema'] for r in rows}=={'modbus-unclocked-observation-v1','opcua-unverified-observation-v1'}
        assert db.scalar(select(Event.id).where(Event.workspace_id==other_workspace,Event.idempotency_key.startswith('local-daily:'))) is None
        events=list(db.scalars(select(Event).where(Event.workspace_id==account[2],
            Event.idempotency_key.startswith('local-daily:'+local_source+':'+local_day+':'))))
        assert len(events)==1 and events[0].data['business_day']==local_day
        assert row['id'] in events[0].data['processed_ids']


@pytest.mark.skipif(os.environ.get('GA_TEST_OS_NETWORK_FENCED') != '1', reason='requires explicit isolated OS network fence')
def test_unclocked_modbus_and_opcua_review_preserves_originals(client,account,device,monkeypatch,tmp_path):
    """Real API-created sensors remain intact; direct missing-day reconcile is read-only."""
    import copy, json
    from datetime import datetime, timezone
    from generic_agents.models import Observation, Event
    from tests.test_sensor_native_packet_pg import setup_native
    from tests.test_sensor_automation_pg import entity
    from tests.test_opcua_secure_transport import _certificate
    require_network_fence()
    monkeypatch.setattr('generic_agents.modbus_readonly_transport.read_registers',lambda *_: [25])
    setup_native(client,account,device,monkeypatch)
    with Session(engine()) as db:
        modbus=db.scalar(select(Observation).where(Observation.workspace_id==account[2],
            Observation.data['schema'].as_string()=='modbus-unclocked-observation-v1'))
        assert modbus and modbus.business_day is None and modbus.status=='extracted_unclocked'
        modbus_id=modbus.id
    ip='10.255.255.253'
    server_cert,_,_=_certificate(tmp_path,'synthetic-server',ip,'urn:test:server',True)
    client_cert,client_key,_=_certificate(tmp_path,'synthetic-client',ip,'urn:test:client',False)
    grant={'workspace':account[2],'host':ip,'port':15020,'path':'/sensor/','node_id':'ns=2;s=temperature',
        'server_certificate':str(server_cert),'client_certificate':str(client_cert),'client_private_key':str(client_key),
        'server_app_uri':'urn:test:server','client_app_uri':'urn:test:client'}
    monkeypatch.setenv('GA_OPCUA_CERT_ROOT',str(tmp_path));monkeypatch.setenv('GA_OPCUA_READ_GRANTS',json.dumps([grant]))
    monkeypatch.setattr('generic_agents.opcua_gateway_api.read_value',lambda _:{'value':23.5,'status_code':0,'status_name':'Good',
        'quality':'good','source_timestamp':None,'server_timestamp':None,'received_at':datetime.now(timezone.utc).isoformat()})
    grants=client.get('/api/opcua/grants'); assert grants.status_code==200 and len(grants.json()['grants'])==1
    registered=client.post('/api/opcua/connections',json={'request_id':str(uuid4()),'name':'合成 OPC UA',
        'grant_id':grants.json()['grants'][0]['id'],'unit_label':'℃'})
    assert registered.status_code==200,registered.text
    connection_id=registered.json()['result']['connection_id']; target=entity(account)
    binding=client.post('/api/opcua/connections/'+connection_id+'/binding',json={'request_id':str(uuid4()),
        'version':1,'entity_id':target,'field':'temperature'});assert binding.status_code==200,binding.text
    response=client.post('/api/opcua/connections/'+connection_id+'/readings',json={'request_id':str(uuid4()),'version':1})
    assert response.status_code==200,response.text
    opcua_id=response.json()['result']['observation_id'];original_ids=[modbus_id,opcua_id]
    with Session(engine()) as db:
        saved={i: (db.get(Observation,i).status,db.get(Observation,i).version,copy.deepcopy(db.get(Observation,i).data)) for i in original_ids}
        assert {saved[i][2]['schema'] for i in original_ids}=={'modbus-unclocked-observation-v1','opcua-unverified-observation-v1'}
    for identity in original_ids:
        r=client.post('/api/observations/'+identity+'/verify',json={'expected_version':saved[identity][1],
            'decision':'confirmed','reason':'合成测试核查'})
        assert r.status_code==409
    engine().dispose()
    with Session(engine(),expire_on_commit=False) as db:
        for identity in original_ids:
            row=db.get(Observation,identity)
            assert (row.status,row.version,row.data)==saved[identity] and row.business_day is None
    # Independently call the shared reconcile entry point with actual sensor
    # source capabilities and an unknown date; no daily event or write is valid.
    from generic_agents.local_observations import reconcile
    from generic_agents.security import Principal
    with Session(engine()) as db:
        actor=db.get(Observation,modbus_id).data['submitted_by']
        principal=Principal(actor,account[2],'admin','synthetic-direct-reconcile')
        original_event_ids=set(db.scalars(select(Event.id).where(Event.workspace_id==account[2])))
        for identity in original_ids:
            row=db.get(Observation,identity)
            reconcile(db,principal,row.source_id,None)
        assert not db.new and not db.dirty and not db.deleted
        db.commit()
    engine().dispose()
    with Session(engine()) as db:
        assert set(db.scalars(select(Event.id).where(Event.workspace_id==account[2])))==original_event_ids
        for identity in original_ids:
            row=db.get(Observation,identity);assert (row.status,row.version,row.data)==saved[identity]
            assert row.business_day is None and row.observed_at is None
