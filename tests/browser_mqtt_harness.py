"""Explicit isolated browser/API/MQTT verification, never a production device service."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
import pytest
from datetime import datetime,timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.models import Base,MqttGateway,MqttReceipt,Job,Observation,Memory,CloudCall
from generic_agents.contracts import digest
from generic_agents.security import Principal
from generic_agents.evidence import evidence_bytes
from tests.test_vertical_slice import publish as register,manifest,entity
from tests.test_mqtt_protocol import broker,reader,ready_line,publish,finish,fresh_worker,fresh_data_receiver

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/mqtt-pipeline-v06/browser'
ACCESS=ROOT/'.runtime/m39-mqtt-browser-access.json'
CONTROL=ROOT/'.runtime/m39-mqtt-browser-control.json'


def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')


def durable():
    with engine().connect() as conn:
        return {t.name:{'count':len(rows),'digest':digest(sorted(rows))}
            for t in sorted(Base.metadata.tables.values(),key=lambda t:t.name)
            for rows in [[digest(json.loads(json.dumps(dict(r._mapping),default=str))) for r in conn.execute(select(t))]]}


def test_mqtt_browser_harness(client,account,broker,monkeypatch):
    if os.getenv('GA_M39_MQTT_BROWSER_HARNESS')!='1':pytest.skip('Explicit disposable browser runtime only')
    assert engine().url.database.startswith('ga_test_')
    assert not ACCESS.exists() and not (OUT/'harness-ready.json').exists(),'Resume original live handle'
    origin=f'mqtt://127.0.0.1:{broker}'
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers',origin)
    package=register(client,manifest());obj=entity(client,package)
    rule=register(client,manifest('tool',name='mqtt_browser_numeric_rule',description='隔离协议验证：温度超过38',
        allowed_agent_roles=['perception'],execution={'adapter':'builtin','handler':'numeric_rules'},
        data_policy={'cloud_allowed':False},specification={'rules':[{'id':'hot','field':'temperature','operator':'gt','threshold':38}]}))
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url='http://localhost:'+str(port)
    env={**os.environ,'GA_DATABASE_URL':settings().database_url,'GA_STORAGE_DIR':str(settings().storage_dir),
         'GA_MQTT_ALLOWED_BROKERS':origin,'GA_ALLOWED_ORIGINS':url,'DASHSCOPE_API_KEY':'','DEEPSEEK_API_KEY':''}
    log=open(ROOT/'.runtime/m39-mqtt-browser-api.log','a');process=None;raws={};before=None
    def start():
        child=subprocess.Popen([sys.executable,'-m','uvicorn','generic_agents.api:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            for _ in range(80):
                assert child.poll() is None
                try:
                    if httpx.get(url+'/health',trust_env=False,timeout=1).is_success:return child
                except httpx.HTTPError:pass
                time.sleep(.25)
            raise AssertionError('API not ready')
        except BaseException:
            if child.poll() is None:child.terminate();child.wait(timeout=10)
            raise
    try:
        process=start()
        with os.fdopen(os.open(ACCESS,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
            json.dump({'username':account[0],'password':account[1],'workspace_id':account[2],'url':url,'broker':origin},f)
        save('harness-ready',{'url':url,'broker_fixture':origin,'api_pid':process.pid,'isolated_database':True,
                              'synthetic_business_schema':True,'physical_device':False})
        print('Isolated browser API and MQTT broker ready; private access saved',flush=True)
        sequence=0;deadline=time.monotonic()+1800
        while time.monotonic()<deadline:
            assert process.poll() is None
            control=json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
            if control.get('sequence',0)<=sequence:time.sleep(.25);continue
            sequence=control['sequence'];command=control['command']
            if command=='publish':
                with Session(engine()) as db:
                    gateways=db.scalars(select(MqttGateway)).all();assert len(gateways)==1
                    gateway=gateways[0];assert gateway.status=='active';identity=gateway.id
                    assert gateway.data['topics']=={'sensor/north':'device-north'}
                raw={'device_id':'device-north','record_id':'browser-good','observed_at':datetime.now(timezone.utc).isoformat(),'values':{'t':39.5},'units':{'t':'C'}}
                bad={**raw,'record_id':'browser-missing-time'};bad.pop('observed_at')
                unit={**raw,'record_id':'browser-unit-mismatch','units':{'t':'F'}}
                raws={r['record_id']:json.dumps(r,separators=(',',':')).encode() for r in [raw,bad,unit]}
                subscriber=reader(identity,origin,4)
                try:
                    ready_line(subscriber,'SUBSCRIBED')
                    publish(broker,[('sensor/north',data) for data in raws.values()]+[('sensor/north',raws['browser-good'])])
                    report=finish(subscriber);assert report['received']==4
                finally:
                    if subscriber.poll() is None:subscriber.terminate();subscriber.wait(timeout=8)
                fresh_data_receiver(origin)
                with Session(engine()) as db:
                    receipts=db.scalars(select(MqttReceipt)).all();assert len(receipts)==3
                    assert len(db.scalars(select(Memory).where(Memory.data['type'].as_string()=='sensor_original')).all())==3
                save('published',{'real_protocol':True,'fresh_reader_os_process':True,'fresh_data_receiver_os_process':True,
                    'unique_originals':3,'duplicate_deliveries':1,'cloud_calls':0,'physical_device':False})
                print('Originals captured through real protocol and delivered by a fresh process',flush=True)
            elif command=='process':
                with Session(engine()) as db:
                    jobs=db.scalars(select(Job).where(Job.kind=='submit_mqtt_receipt',Job.status=='queued')).all();assert len(jobs)==1
                    submit_id=jobs[0].id
                fresh_worker(submit_id,origin)
                with Session(engine()) as db:
                    job=db.scalar(select(Job).where(Job.kind=='process_mqtt_observation'));assert job
                    extract_id=job.id
                fresh_worker(extract_id,origin);fresh_data_receiver(origin)
                with Session(engine()) as db:
                    observation=db.scalar(select(Observation));assert observation.status=='processed'
                    assert observation.data['extracted']['attributes']=={'temperature':39.5}
                    assert observation.data['extracted']['anomalies'][0]['rule_id']=='hot'
                save('processed',{'fresh_submission_os_process':True,'fresh_extraction_os_process':True,
                    'field_mapping':'air_temperature -> temperature','local_numeric_rule':True,'business_value_verified':False})
                print('Confirmed source mapping processed with local rules',flush=True)
            elif command=='restart':
                before=durable();save('before-restart',before)
                originals=client.get('/api/mqtt/gateways').json();assert len(originals)==1
                receipts=client.get('/api/mqtt/gateways/'+originals[0]['id']+'/receipts').json()
                for receipt in receipts:
                    content=client.get('/api/mqtt/receipts/'+receipt['id']+'/original').content
                    assert content==raws[receipt['decoded']['reported_record_id']]
                process.terminate();process.wait(timeout=10);process=start()
                save('harness-restarted',{'url':url,'api_pid':process.pid,'state':'restarted'})
            elif command=='finish':
                assert before is not None and durable()==before
                assert before[CloudCall.__tablename__]['count']==0
                save('after-restart',durable());save('verification',{'real_api_restart':True,
                    'all_table_digests_unchanged':True,'original_bytes_verified':True,'cloud_calls':0,
                    'synthetic_business_schema':True,'physical_device':False,'production_verified':False})
                print('API restart, saved drafts, originals and all table digests verified',flush=True);break
            else:raise AssertionError('Unknown control command')
        else:raise AssertionError('Browser harness deadline expired')
    except BaseException as error:
        save('interrupted',{'error_type':type(error).__name__,'message':str(error)[:300],'verified':False});raise
    finally:
        if process and process.poll() is None:process.terminate();process.wait(timeout=10)
        log.close();ACCESS.unlink(missing_ok=True);CONTROL.unlink(missing_ok=True)
