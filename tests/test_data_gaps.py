"""Data-to-perception protocol against isolated real PostgreSQL; no real device/cloud calls."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.db import engine
from generic_agents.models import Event,Observation,DataGap,Acquisition,Membership,MonitorPolicy,now
from generic_agents.worker import process_once,deliver_events
from generic_agents.security import Principal
from generic_agents.data_gaps import recheck
from .test_vertical_slice import manifest,publish,entity
from .test_monitor import policy

def post(c,path,v):
 r=c.post(path,json=v);assert r.status_code in {200,201},r.text;return r.json()

def setup(c):
 pkg=publish(c,manifest());obj=entity(c,pkg)
 source=publish(c,manifest('source',name='gap_camera',execution={'adapter':'upload'},specification={'timezone':'UTC'}))
 extractor=publish(c,manifest('tool',name='gap_numeric',allowed_agent_roles=['perception'],
  execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[{'id':'high','field':'temperature','operator':'gt','threshold':90}]}))
 return pkg,obj,source,extractor

def query(c,obj,**overrides):
 value={'question':'核对登记对象温度','entity_ids':[obj['id']],'fields':['temperature'],'mode':'structured',
  'evidence_requirement':{'source':'observations','minimum_verification':'unverified_extraction',
   'time':{'mode':'latest','max_age_seconds':3600}}}
 value.update(overrides);return post(c,'/api/data/queries',value)

def gap(c,run):
 values=c.get('/api/data/gaps',params={'query_id':run['id']}).json()['items'];assert len(values)==1;return values[0]

def drain():
 for _ in range(30):
  if not process_once():return
 raise AssertionError('Unexpected unbounded queue')

def observe(c,obj,source,extractor,attrs=None,at=None):
 at=at or now()-timedelta(seconds=1)
 return post(c,'/api/observations',{'source_id':source['id'],'source_record_id':uuid4().hex,
  'entity_id':obj['id'],'observed_at':at.isoformat(),'business_day':str(at.date()),
  'attributes':attrs or {'temperature':27},'extractor_id':extractor['id'],'startup_mode':'manual'})

def test_gap_delivered_without_permission_no_device_then_observation_rechecks_original_immutable(client):
 c=client;pkg,obj,source,extractor=setup(c);run=query(c,obj);assert run['status']=='insufficient'
 assert gap(c,run)['status']=='pending_delivery';drain();g=gap(c,run);assert g['status']=='needs_authorization'
 assert not c.get('/api/overview').json()['acquisitions']
 obs=observe(c,obj,source,extractor);drain();g=gap(c,run);assert g['status']=='resolved'
 child=c.get('/api/data/queries/'+g['data']['latest_recheck_query_id']).json()
 assert child['status']=='sufficient' and child['data']['parent_query_id']==run['id']
 assert child['data']['result']['observation_evidence'][0]['attributes']['temperature']==27
 saved=c.get('/api/data/queries/'+run['id']).json()
 from datetime import datetime
 assert datetime.fromisoformat(saved.pop('created_at'))==datetime.fromisoformat(run['created_at'])
 assert saved=={k:v for k,v in run.items() if k!='created_at'}
 h=c.get('/api/data/gaps/'+g['id']+'/history').json()['items']
 assert {x['data']['operation'] for x in h}>={'detected','received','perception_decision','verification_failed','verified'}
 assert not c.get('/api/overview').json()['cloud_calls']

def test_missing_control_honest_block_and_shared_inflight_acquisition_reused(client):
 pkg,obj,source,extractor=setup(client);mon=policy(client,obj,source)
 a=query(client,obj);drain();g=gap(client,a);assert g['status']=='blocked_connector'
 rows=client.get('/api/overview').json()['acquisitions'];assert len(rows)==1
 # Explicit simulated accepted command, not a real device integration assertion.
 with Session(engine()) as db:
  acq=db.get(Acquisition,rows[0]['id']);acq.status='awaiting_observation';db.commit()
 b=query(client,obj);drain();g2=gap(client,b)
 assert g2['status']=='awaiting_observation' and g2['data']['acquisition_ids']==[rows[0]['id']]
 assert len(client.get('/api/overview').json()['acquisitions'])==1

def test_fresh_unconfirmed_evidence_requires_review_not_another_device(client):
 pkg,obj,source,extractor=setup(client);observe(client,obj,source,extractor);drain()
 req={'source':'observations','minimum_verification':'confirmed','time':{'mode':'latest','max_age_seconds':3600}}
 run=query(client,obj,evidence_requirement=req);drain();g=gap(client,run)
 assert g['status']=='needs_review' and not client.get('/api/overview').json()['acquisitions']
 obs=client.get('/api/overview').json()['observations'][0]
 post(client,'/api/observations/'+obs['id']+'/verify',{'expected_version':obs['version'],'decision':'confirmed','reason':'核对合成原始测量'})
 drain();assert gap(client,run)['status']=='resolved'

def test_past_and_archive_need_records_not_new_devices(client):
 pkg,obj,source,extractor=setup(client);policy(client,obj,source)
 past={'source':'observations','minimum_verification':'unverified_extraction','time':{
  'mode':'range','start':(now()-timedelta(days=3)).isoformat(),'end':(now()-timedelta(days=2)).isoformat()}}
 run=query(client,obj,evidence_requirement=past);drain();assert gap(client,run)['status']=='needs_historical_evidence'
 expired=entity(client,pkg,valid_until=(now()-timedelta(days=1)).isoformat())
 old=query(client,expired,evidence_requirement={'source':'archive'});drain();assert gap(client,old)['status']=='needs_record'
 assert not client.get('/api/overview').json()['acquisitions']

def test_collection_receipt_required_before_closing(client):
 pkg,obj,source,extractor=setup(client);run=query(client,obj);drain()
 obs=observe(client,obj,source,extractor)
 with Session(engine()) as db:
  row=db.get(Observation,obs['id']);row.status='processed';row.data={**row.data,'extracted':{'attributes':{'temperature':28}}};db.commit()
  p=Principal(run['owner_id'],run['workspace_id'],'admin','test')
  g=recheck(db,p,gap(client,run)['id']);assert g.status=='awaiting_collection';db.commit()
  event=Event(workspace_id=p.workspace_id,kind='observation.data',idempotency_key='data:'+row.id,
   data={'observation_id':row.id,'entity_id':obj['id'],'source_id':source['id'],'extracted':row.data['extracted']})
  db.add(event);db.commit()
 deliver_events();drain();assert gap(client,run)['status']=='resolved'

def test_wrong_field_or_too_old_observation_does_not_close(client):
 pkg,obj,source,extractor=setup(client);run=query(client,obj);drain()
 observe(client,obj,source,extractor,attrs={'other':1});drain();assert gap(client,run)['status']!='resolved'
 observe(client,obj,source,extractor,at=now()-timedelta(days=2));drain();assert gap(client,run)['status']!='resolved'

def test_rejected_and_future_observations_cannot_satisfy_perception(client):
 pkg,obj,source,extractor=setup(client)
 observe(client,obj,source,extractor);observe(client,obj,source,extractor,at=now()+timedelta(days=1));drain()
 obs=next(o for o in client.get('/api/overview').json()['observations'] if o['observed_at']<now().isoformat())
 post(client,'/api/observations/'+obs['id']+'/verify',{'expected_version':obs['version'],'decision':'rejected','reason':'合成记录撤回，不能视为对象正常'})
 state=client.get('/api/entities/'+obj['id']+'/perception-state').json()
 assert state['status']=='insufficient' and not state['evidence_ids']

def test_revoked_monitor_admin_blocks_dispatch_even_operator_scan(client):
 pkg,obj,source,extractor=setup(client);mon=policy(client,obj,source)
 with Session(engine()) as db:
  row=db.scalar(select(Membership).where(Membership.workspace_id==mon['workspace_id']));row.role='operator';db.commit()
 run=query(client,obj);drain();assert gap(client,run)['status']=='needs_authorization'
 assert client.post('/api/monitor-policies/'+mon['id']+'/scan').status_code==409
 assert not client.get('/api/overview').json()['acquisitions']

def test_cancel_version_and_readonly_permissions(client):
 pkg,obj,source,extractor=setup(client);run=query(client,obj);g=gap(client,run)
 cancelled=post(client,'/api/data/gaps/'+g['id']+'/cancel',{'expected_version':g['version'],'reason':'停止自有合成补证'})
 assert cancelled['status']=='cancelled';drain();assert not client.get('/api/overview').json()['acquisitions']
 assert client.post('/api/data/gaps/'+g['id']+'/recheck',json={'expected_version':g['version'],'reason':'旧版本'}).status_code==409
 with Session(engine()) as db:
  row=db.scalar(select(Membership).where(Membership.workspace_id==g['workspace_id']));row.role='viewer';db.commit()
 assert client.get('/api/data/gaps/'+g['id']+'/history').status_code==200
 assert client.post('/api/data/gaps/'+g['id']+'/retry',json={'expected_version':cancelled['version'],'reason':'越权'}).status_code==403

def test_policy_retry_after_approval_and_static_operator_asset(client):
 pkg,obj,source,extractor=setup(client);run=query(client,obj);drain();g=gap(client,run)
 policy(client,obj,source)
 post(client,'/api/data/gaps/'+g['id']+'/retry',{'expected_version':g['version'],'reason':'核查新增限定监测批准'})
 drain();assert gap(client,run)['status']=='blocked_connector'
 assert client.get('/assets/data-gaps.js').status_code==200

def test_business_coordination_received_once_and_gap_does_not_expand_policy(client):
 pkg,obj,source,extractor=setup(client);other=entity(client,pkg);policy(client,other,source)
 run=query(client,obj);drain();g=gap(client,run)
 tasks=client.get('/api/overview').json()['tasks'];assert len(tasks)==1
 assert tasks[0]['id']==g['data']['business_task_id'] and tasks[0]['data']['entity_ids']==[obj['id']]
 assert g['data']['business_received_at'] and not client.get('/api/overview').json()['acquisitions']
 post(client,'/api/data/gaps/'+g['id']+'/retry',{'expected_version':g['version'],'reason':'复核原授权范围'})
 drain();assert len(client.get('/api/overview').json()['tasks'])==1

def test_gap_history_and_read_are_workspace_scoped(client):
 pkg,obj,source,extractor=setup(client);run=query(client,obj);g=gap(client,run)
 from generic_agents.models import Workspace,User
 from generic_agents.security import password_hash
 from fastapi.testclient import TestClient
 from generic_agents.api import app
 username=uuid4().hex;password=uuid4().hex
 with Session(engine()) as db:
  user=User(username=username,password_hash=password_hash(password));w=Workspace(name='独立缺口隔离工作区');db.add_all([user,w]);db.flush()
  db.add(Membership(user_id=user.id,workspace_id=w.id,role='admin'));db.commit()
 with TestClient(app) as other:
  login=other.post('/api/login',json={'username':username,'password':password}).json();other.headers['X-CSRF-Token']=login['csrf_token']
  assert other.get('/api/data/gaps/'+g['id']).status_code==404
  assert other.get('/api/data/gaps/'+g['id']+'/history').status_code==404
  assert other.post('/api/data/gaps/'+g['id']+'/retry',json={'expected_version':g['version'],'reason':'越界'}).status_code==404
