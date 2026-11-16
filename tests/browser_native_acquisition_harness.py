"""Opt-in disposable browser runtime; never targets the application database or hardware."""
import os,json,time,socket,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
import httpx,pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents import models as m
from tests.test_sensing_selection import supplier,dispatch,observe
from tests.test_native_goal_acquisitions import prepared

ROOT=Path(__file__).resolve().parents[1]
STAGE=os.getenv('GA_BROWSER_ACQUISITION_STAGE','m34')
assert STAGE in {'m34','m35'}
OUT=ROOT/('docs/evidence/original-acquisition-v06' if STAGE=='m34' else 'docs/evidence/saved-goal-recovery-v06')
CONTROL=ROOT/('.runtime/'+STAGE+'-native-acquisition-control.json')
ACCESS=ROOT/('.runtime/'+STAGE+'-native-acquisition-access.json')
def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True);(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')
def durable():
    models=[m.Task,m.DataRun,m.DataGap,m.GapCheck,m.Acquisition,m.ObservationPlan,m.Observation,m.Invocation,m.Event,m.Memory,m.SensorReservation,m.DeviceReservation]
    with Session(engine()) as db:
        return {model.__name__+':'+r.id:digest(json.loads(json.dumps({col.name:getattr(r,col.name) for col in model.__table__.columns},default=str))) for model in models for r in db.scalars(select(model))}

def test_browser_harness(client,account,supplier):
    if os.getenv('GA_M34_BROWSER_HARNESS')!='1':pytest.skip('Explicit isolated runtime only')
    assert engine().url.database.startswith('ga_test_')
    assert not (OUT/'harness-ready.json').exists(),'Continue original harness; no duplicate fixture'
    p=prepared(client,supplier,verification='confirmed');supplier['modes'][p['asset']['external_id']]='unknown';dispatch(p['monitor'],p['acquisition_id'])
    if STAGE=='m35':
        instance=next(row for row in client.get('/api/instances').json() if row['id']==p['r']['instance_id'])
        paused=client.post('/api/instances/'+p['r']['instance_id']+'/pause',json={'version':instance['version']})
        assert paused.status_code==200,paused.text
    key=supplier['calls'][0]['request_key'];supplier['receipts'][key].update(status='accepted',terminal=False,no_future_device_activity=False)
    # localhost cookie origin differs from 127.0.0.1 application; no user login is displaced.
    with socket.socket() as candidate:candidate.bind(('127.0.0.1',0));port=candidate.getsockname()[1]
    env={**os.environ,'GA_STORAGE_DIR':str(settings().storage_dir),'GA_DEVELOPMENT_CONNECTOR_URLS':supplier['url']+'/observe,'+supplier['url']+'/receipt'}
    url='http://localhost:'+str(port)
    env.update(GA_ALLOWED_ORIGINS=url,DASHSCOPE_API_KEY='',DEEPSEEK_API_KEY='')  # Exact local origin only; no cloud credentials in the fixture.
    log=open(ROOT/('.runtime/'+STAGE+'-native-acquisition-api.log'),'a');process=None
    def start():
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','generic_agents.api:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
        for _ in range(80):
            assert proc.poll() is None,'Isolated API exited; inspect local log'
            try:
                if httpx.get('http://127.0.0.1:'+str(port)+'/health',trust_env=False,timeout=1).is_success:return proc
            except httpx.HTTPError:pass
            time.sleep(.25)
        proc.terminate();proc.wait(timeout=10);raise AssertionError('Isolated API readiness timed out')
    try:
        process=start();username,password,workspace=account
        ACCESS.write_text(json.dumps({'username':username,'password':password,'workspace_id':workspace,'url':url}));ACCESS.chmod(0o600)
        manifest={'api_url':url,'api_pid':process.pid,'harness_pid':os.getpid(),'task_id':p['task']['id'],'instance_id':p['r']['instance_id'],'acquisition_id':p['acquisition_id'],'task_title':p['task']['title'],'field':p['field'],'isolated_database':True,'real_hardware':False,'state':'ready','configuration_paused':STAGE=='m35'}
        save('harness-ready',manifest);print('M34 isolated API ready; private access file saved; no application DB or hardware changes',flush=True)
        sequence=0;before=None;deadline=time.monotonic()+3600
        while time.monotonic()<deadline:
            assert process.poll() is None,'Isolated API no longer live'
            control=json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
            if control.get('sequence',0)<=sequence:time.sleep(.25);continue
            sequence=control['sequence'];command=control['command']
            if command=='terminal':
                supplier['receipts'][key].update(status='completed',terminal=True,no_future_device_activity=True)
                save('terminal-ready',{'same_original_request':True,'device_posts':len(supplier['calls']),'receipt_reads':len(supplier.get('read_calls',[]))})
            elif command=='observation':
                assert client.get(p['base']).json()['cancelled']
                obs=observe(client,p['monitor'],p['obj'],p['source'],p['acquisition_id'],{p['field']:p['obj']['attributes'][p['field']]})
                save('observation-ready',{'observation_id':obs['id'],'same_original_acquisition':True,'device_posts':len(supplier['calls']),'receipt_reads':len(supplier.get('read_calls',[]))})
            elif command=='restart':
                goal=client.get(p['base']).json();rows=client.get(p['base']+'/acquisitions').json();item=rows['items'][0]
                assert goal['cancelled'] and not goal['acceptance'] and item['status']=='succeeded' and not item['reservation_held'] and item['observations']['items'][0]['review']=='confirmed'
                assert len(supplier['calls'])==1 and len(supplier['read_calls'])==2
                before=durable();save('before-restart',before);save('before-restart-export',client.get(p['base']+'/export').json())
                process.terminate();process.wait(timeout=10);process=start()
                manifest.update(api_pid=process.pid,state='restarted');save('harness-restarted',manifest)
            elif command=='finish':
                assert before is not None and durable()==before
                with Session(engine()) as db:assert len(list(db.scalars(select(m.Task))))==1 and not list(db.scalars(select(m.CloudCall)))
                export=client.get(p['base']+'/export').json();assert export['original_acquisitions']['items'][0]['status']=='succeeded' and export['goal']['cancelled'] and not export['goal']['acceptance']
                proof={'durability_after_real_api_restart':True,'one_original_native_task':True,'original_device_http_posts':len(supplier['calls']),'original_receipt_http_gets':len(supplier['read_calls']),
                    'late_original_data_review_saved':True,'original_goal_remained_stopped':True,'formal_goal_accepted':False,'cloud_calls':0,'specialist_inferences':0,'real_hardware':False,'application_database_changed':False,'production_ready':False,'durable_records':len(before)}
                save('after-restart-export',export);save('verification',proof);print('M34 actual isolated API/browser/PG journey and restart verified; fixture is disposable, not hardware acceptance',flush=True);break
            else:raise AssertionError('Unknown harness command')
        else:raise AssertionError('Browser harness expired without acceptance')
    except BaseException as error:
        # Preserve committed human work before the disposable fixture is cleaned up.
        save('interrupted',{'error_type':type(error).__name__,'error':str(error)[:500],
            'api_live':bool(process and process.poll() is None),'acceptance_verified':False})
        try:
            save('interrupted-durable',durable())
            save('interrupted-export',client.get(p['base']+'/export').json())
        except Exception as checkpoint_error:
            save('checkpoint-error',{'error_type':type(checkpoint_error).__name__})
        raise
    finally:
        if process and process.poll() is None:process.terminate();process.wait(timeout=10)
        log.close();ACCESS.unlink(missing_ok=True);CONTROL.unlink(missing_ok=True)
