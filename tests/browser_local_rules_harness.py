"""Explicit disposable browser runtime for human rule creation; no cloud/device service."""
import json,os,socket,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
import httpx,pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.models import Base,Capability,MqttGateway,MqttBinding,MqttReceipt,Observation,CloudCall,User,InstanceRelease,Job,Membership
from generic_agents.contracts import digest
from generic_agents.security import Principal,password_hash
from generic_agents.mqtt_inbox import capture
from generic_agents.instance_compiler import signature_value
from tests.test_vertical_slice import manifest,publish,entity
from tests.test_instance_workspace import configuration,save as save_draft
from tests.test_mqtt_protocol import fresh_worker,fresh_data_receiver

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/local-rules-v06'
ACCESS=ROOT/'.runtime/local-rules-browser-access.json'
CONTROL=ROOT/'.runtime/local-rules-browser-control.json'

def write(name,value):
 OUT.mkdir(parents=True,exist_ok=True);(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')

def durable():
 with engine().connect() as conn:
  return {t.name:{'count':len(rows),'digest':digest(sorted(rows))} for t in sorted(Base.metadata.tables.values(),key=lambda t:t.name)
   for rows in [[digest(json.loads(json.dumps(dict(r._mapping),default=str))) for r in conn.execute(select(t))]]}

def test_local_rules_browser(client,account,monkeypatch):
 if os.getenv('GA_LOCAL_RULES_BROWSER')!='1':pytest.skip('Explicit disposable browser verification only')
 assert engine().url.database.startswith('ga_test_') and not ACCESS.exists()
 origin='mqtt://127.0.0.1:1883';monkeypatch.setattr(settings(),'mqtt_allowed_brokers',origin)
 package=publish(client,manifest());obj=entity(client,package)
 conf={'broker':origin,'topics':{'sensor/north':'device-north'},'mapping':{'t':{'attribute':'air_temperature','unit':'C'}},'fields':{},'username_ref':'','password_ref':''}
 gw=client.post('/api/mqtt/gateways',json={'name':'隔离核对来源','configuration':conf});assert gw.status_code==201
 config=configuration();config['instanceProfile']['name']='本地规则交互核对';config['instanceProfile']['domain']='通用资料核查'
 for flow in config['flows'].values():flow['confirmedSignature']=json.dumps(signature_value(config,flow),ensure_ascii=False)
 save_draft(client,config)
 with Session(engine()) as db:
  user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','browser-test')
  viewer_name='viewer_'+os.urandom(5).hex();viewer_password=os.urandom(16).hex()
  viewer=User(username=viewer_name,password_hash=password_hash(viewer_password));db.add(viewer);db.flush()
  db.add(Membership(user_id=viewer.id,workspace_id=account[2],role='viewer'));db.commit()
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 url='http://localhost:'+str(port)
 env={**os.environ,'GA_DATABASE_URL':settings().database_url,'GA_STORAGE_DIR':str(settings().storage_dir),
  'GA_MQTT_ALLOWED_BROKERS':origin,'GA_ALLOWED_ORIGINS':url,'DASHSCOPE_API_KEY':'','DEEPSEEK_API_KEY':''}
 log=open(ROOT/'.runtime/local-rules-browser-api.log','a');process=None;before=None
 def start():
  child=subprocess.Popen([sys.executable,'-m','uvicorn','generic_agents.api:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
  for _ in range(80):
   assert child.poll() is None
   try:
    if httpx.get(url+'/health',trust_env=False,timeout=1).is_success:return child
   except httpx.HTTPError:pass
   time.sleep(.25)
  child.terminate();child.wait(timeout=10);raise AssertionError('API not ready')
 try:
  process=start()
  with os.fdopen(os.open(ACCESS,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
   json.dump({'username':account[0],'password':account[1],'url':url,'entity_id':obj['id'],'gateway_id':gw.json()['id'],
    'viewer_username':viewer_name,'viewer_password':viewer_password},f)
  write('ready',{'isolated_database':True,'url':url,'synthetic_business_schema':True,'real_device':False,'cloud_keys_available':False})
  print('Disposable rule browser API ready; no rule pre-registered',flush=True)
  sequence=0;deadline=time.monotonic()+1500
  while time.monotonic()<deadline:
   assert process.poll() is None
   control=json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
   if control.get('sequence',0)<=sequence:time.sleep(.2);continue
   sequence=control['sequence'];command=control['command']
   if command=='process':
    with Session(engine()) as db:
     rule=db.scalar(select(Capability).where(Capability.namespace=='platform_numeric_rules'));assert rule.status=='published'
     assert rule.data['specification']['rules'][0]['threshold']==37
     gateway=db.get(MqttGateway,gw.json()['id']);assert gateway.status=='active'
     binding=db.scalar(select(MqttBinding));assert binding.entity_id==obj['id'] and binding.extractor_id==rule.id
     raw=json.dumps({'device_id':'device-north','record_id':'rule-ui-sample','observed_at':datetime.now(timezone.utc).isoformat(),
      'values':{'t':39.5},'units':{'t':'C'}}).encode()
     receipt=capture(db,p,gateway.id,gateway.version,'sensor/north',raw,1);db.commit();receipt_id=receipt.id
    with Session(engine()) as db:
     job=db.scalar(select(Job).where(Job.kind=='submit_mqtt_receipt',Job.data['receipt_id'].as_string()==receipt_id));assert job
     submit_id=job.id
    fresh_worker(submit_id,origin)
    with Session(engine()) as db:
     job=db.scalar(select(Job).where(Job.kind=='process_mqtt_observation'));assert job;extract_id=job.id
    fresh_worker(extract_id,origin);fresh_data_receiver(origin)
    with Session(engine()) as db:
     receipt=db.get(MqttReceipt,receipt_id);observation=db.get(Observation,receipt.data['observation_id']) if receipt.data.get('observation_id') else db.scalar(select(Observation))
     assert observation.status=='processed' and observation.data['extracted']['attributes']=={'temperature':39.5}
     assert observation.data['extracted']['anomalies'][0]['value']==39.5
    write('processed',{'ui_created_published_rule':True,'confirmed_field_links':True,'local_extraction':True,
     'real_os_worker':True,'synthetic_packet':True,'broker_connected':False,'cloud_calls':0})
    print('UI-created rule and confirmed source produced saved local extraction',flush=True)
   elif command=='restart':
    before=durable();write('before-restart',before);process.terminate();process.wait(timeout=10);process=start();write('restarted',{'real_api_restart':True})
   elif command=='finish':
    assert before is not None and durable()==before
    with Session(engine()) as db:
     assert not db.scalars(select(CloudCall)).all()
     release=db.scalar(select(InstanceRelease));assert release and release.data['snapshot']['sensor_sources'][0]['name']=='隔离核对来源'
    write('after-restart',durable());write('verification',{'real_api_restart':True,'all_table_digests_unchanged':True,
      'rule_created_via_browser':True,'instance_generated_via_browser':True,'source_summary_frozen':True,
      'cloud_calls':0,'broker_connected':False,'real_device':False,'production_verified':False})
    print('Rule, source, generated summary and all durable state unchanged after restart',flush=True);break
   else:raise AssertionError('Unknown control command')
  else:raise AssertionError('Browser verification expired')
 finally:
  if process and process.poll() is None:process.terminate();process.wait(timeout=10)
  log.close();ACCESS.unlink(missing_ok=True);CONTROL.unlink(missing_ok=True)
