"""Real local numeric extraction -> transactional business inbox -> manual workflow."""
import copy
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import Job,Event,Task,Memory,Entity,Observation,PerceptionSignal,CloudCall
from generic_agents.db import engine
from generic_agents.worker import process_once,deliver_events
from tests.test_instance_workspace import prepared,enable,object_row
from tests.test_vertical_slice import manifest,publish


def setup(client,run=True):
 r,_,_=prepared(client);enable(client,r);obj=object_row(client,r)
 src=publish(client,manifest('source',name='manual_signal_source',allowed_agent_roles=['perception'],
  execution={'adapter':'upload'},specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
 cap=publish(client,manifest('tool',name='local_signal_threshold',allowed_agent_roles=['perception'],
  execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[{'id':'high','field':'公开登记值','operator':'gt','threshold':50}]},data_policy={'cloud_allowed':False}))
 clock=datetime.now(timezone.utc)-timedelta(seconds=10)
 body={'source_id':src['id'],'source_record_id':str(uuid4()),'entity_id':obj['id'],'extractor_id':cap['id'],
  'attributes':{'公开登记值':65},'observed_at':clock.isoformat(),'business_day':str((clock+timedelta(hours=8)).date()),'startup_mode':'manual'}
 response=client.post(f"/api/workspace/instances/{r['instance_id']}/reported-observations",json=body)
 assert response.status_code==201,response.text;obs=response.json()
 with Session(engine()) as db:
  job=db.scalar(select(Job).where(Job.data['observation_id'].as_string()==obs['id']));jobid=job.id
  assert job.kind=='process_reported_observation'
 if run:assert process_once(jobid,kinds=['process_reported_observation'],deliver=False)
 return r,obj,obs,body,jobid


def signals(client,r):
 response=client.get(f"/api/workspace/instances/{r['instance_id']}/signals");assert response.status_code==200,response.text
 return response.json()['records']


def task_body(r,obj,s):return {'release_id':r['id'],'business_id':r['available_business_ids'][0],
 'entity_ids':[obj['id']],'title':'核查公开人工登记规则异常','request_id':str(uuid4()),
 'signal_id':s['id'],'signal_version':s['version'],'signal_basis_hash':s['basis_hash']}


def complete(client,t,decision=None):
 body={'version':t['version'],'step_id':t['workflow_runtime']['current_step_id'],'note':'工程规则候选已核对，非现场异常证明','request_id':str(uuid4())}
 if decision:body['perception_decision']=decision
 return client.post('/api/workspace/tasks/'+t['id']+'/complete-step',json=body)


def test_local_rule_automatically_receives_signal_data_and_handoff_is_idempotent(client):
 r,obj,obs,body,jobid=setup(client);s=signals(client,r)[0]
 assert s['status']=='unassigned' and not s['integrity_issue']
 assert s['basis']['extracted']['anomalies'][0]['value']==65
 assert s['basis']['trigger_rules'][0]['threshold']==50
 assert not s['basis']['source_time_verified']
 saved=client.put(f"/api/workspace/instances/{r['instance_id']}/signals/{s['id']}/note",
  json={'version':s['version'],'request_id':str(uuid4()),'note':'核对公开工程数据，先人工判断'});assert saved.status_code==200,saved.text;s=saved.json()
 assert signals(client,r)[0]['note']==s['note']
 b=task_body(r,obj,s);url=f"/api/workspace/instances/{r['instance_id']}/tasks"
 response=client.post(url,json=b);assert response.status_code==201,response.text;t=response.json()
 assert t['perception_signal']['note']==s['note'] and t['status']=='workflow_waiting'
 assert client.post(url,json=b).json()['id']==t['id']
 one=complete(client,t);assert one.status_code==200,one.text;t=one.json()
 assert complete(client,t).status_code==422
 final=complete(client,t,'uncertain');assert final.status_code==200,final.text;t=final.json()
 latest=signals(client,r)[0];assert latest['status']=='reviewed' and latest['business_review']['decision']=='uncertain'
 export=client.get(f"/api/workspace/instances/{r['instance_id']}/signals/{s['id']}/export")
 assert export.status_code==200 and export.json()['notes'][0]['note']==s['note']
 with Session(engine()) as db:
  memory=db.get(Memory,t['workflow_runtime']['data_receipt_id']);assert memory.status=='candidate' and not memory.data['entity_state_updated']
  assert memory.data['perception_signal']['signal_id']==s['id']
  assert db.get(Entity,obj['id']).version==obj['version']
  assert not db.scalars(select(CloudCall)).all()
  assert len(db.scalars(select(Task)).all())==1
  event=db.scalar(select(Event).where(Event.idempotency_key=='anomaly:'+obs['id']));event.status='pending_delivery';db.commit()
 deliver_events(workspace_id=obj['workspace_id'] if 'workspace_id' in obj else body.get('workspace_id'))
 assert len(signals(client,r))==1
 assert client.get(f"/api/workspace/instances/{r['instance_id']}/reported-observations").json()['records'][0]['data_received']


def test_corrupt_source_and_note_conflict_preserve_human_work(client):
 r,obj,obs,_,_=setup(client);s=signals(client,r)[0]
 b={'version':s['version'],'request_id':str(uuid4()),'note':'人工核对记录已保存'}
 first=client.put(f"/api/workspace/instances/{r['instance_id']}/signals/{s['id']}/note",json=b)
 assert first.status_code==200,first.text
 assert client.put(f"/api/workspace/instances/{r['instance_id']}/signals/{s['id']}/note",json=b).json()['version']==first.json()['version']
 with Session(engine()) as db:
  row=db.get(Observation,obs['id']);data=copy.deepcopy(row.data);data['extracted']['attributes']['公开登记值']=999;row.data=data;db.commit()
 latest=signals(client,r)[0];assert latest['basis'] is None and latest['integrity_issue'] and latest['note']==b['note']
 assert client.post(f"/api/workspace/instances/{r['instance_id']}/tasks",json=task_body(r,obj,latest)).status_code==409
 assert client.get(f"/api/workspace/instances/{r['instance_id']}/signals/{s['id']}/export").status_code==409
 assert client.put(f"/api/workspace/instances/{r['instance_id']}/signals/{s['id']}/note",json={**b,'request_id':str(uuid4())}).status_code==409


def test_paused_instance_blocks_queued_rule_and_does_not_open_legacy_queue(client):
 r,obj,obs,_,jobid=setup(client,run=False)
 instance=next(i for i in client.get('/api/instances').json() if i['id']==r['instance_id'])
 assert client.post('/api/instances/'+instance['id']+'/pause',json={'version':instance['version']}).status_code==200
 assert process_once(jobid,kinds=['process_reported_observation'],deliver=False)
 assert not signals(client,r)
 with Session(engine()) as db:
  assert db.get(Observation,obs['id']).status=='failed'
  assert not db.scalars(select(CloudCall)).all()


def test_signal_scope_and_automatic_business_are_blocked(client,account):
 r,obj,obs,body,_=setup(client);s=signals(client,r)[0]
 # Entity binding is server checked before ingestion; caller cannot use another namespace.
 with Session(engine()) as db:
  original=db.get(Entity,obj['id']);foreign=Entity(workspace_id=account[2],namespace='other',entity_type=original.entity_type,
   schema_capability_id=original.schema_capability_id,external_id=str(uuid4()),data=copy.deepcopy(original.data))
  db.add(foreign);db.flush();foreign_id=foreign.id;db.commit()
 response=client.post(f"/api/workspace/instances/{r['instance_id']}/reported-observations",json={**body,'entity_id':foreign_id,'source_record_id':str(uuid4())})
 assert response.status_code==409
 assert client.post(f"/api/workspace/instances/{r['instance_id']}/reported-observations",json={**body,'source_record_id':str(uuid4()),'evidence_ids':['bad']}).status_code==422


def test_durable_reported_delivery_recovers_without_consuming_old_queue(client,account):
 from generic_agents.perception_signals import deliver_pending
 r,obj,obs,body,_=setup(client)
 old=client.post('/api/observations',json={**body,'source_record_id':str(uuid4())});assert old.status_code==201
 with Session(engine()) as db:
  event=db.scalar(select(Event).where(Event.idempotency_key=='anomaly:'+obs['id']));event.status='pending_delivery'
  legacy=Event(workspace_id=account[2],kind='perception.anomaly',idempotency_key='legacy-notice',
   data={**event.data,'observation_id':old.json()['id']})
  db.add(legacy);db.flush();legacy_id=legacy.id;db.commit()
 deliver_pending()
 assert len(signals(client,r))==1
 with Session(engine()) as db:
  assert db.get(Event,legacy_id).status=='pending_delivery'
  old_job=db.scalar(select(Job).where(Job.data['observation_id'].as_string()==old.json()['id']))
  assert old_job.kind=='process_observation' and old_job.status=='queued'
  assert not db.scalars(select(CloudCall)).all()


def test_blocked_first_receipt_rechecks_restored_original_and_keeps_notes(client,account):
 from generic_agents.perception_signals import deliver_pending
 r,obj,obs,_,_=setup(client)
 with Session(engine()) as db:
  original=db.get(Observation,obs['id']);saved=copy.deepcopy(original.data)
  altered=copy.deepcopy(saved);altered['extracted']['attributes']['公开登记值']=999;original.data=altered
  receipt=db.scalar(select(PerceptionSignal));db.delete(receipt)
  event=db.scalar(select(Event).where(Event.idempotency_key=='anomaly:'+obs['id']));event.status='pending_delivery';db.commit()
 deliver_pending();s=signals(client,r)[0];assert s['status']=='blocked_evidence' and not s['basis']
 response=client.put(f"/api/workspace/instances/{r['instance_id']}/signals/{s['id']}/note",json={'version':s['version'],
  'request_id':str(uuid4()),'note':'原依据恢复前保留人工核对记录'});assert response.status_code==200,response.text;s=response.json()
 with Session(engine()) as db:
  original=db.get(Observation,obs['id']);original.data=saved;db.commit()
 response=client.post(f"/api/workspace/instances/{r['instance_id']}/signals/{s['id']}/recheck",json={'version':s['version']})
 assert response.status_code==200,response.text
 assert response.json()['basis']['extracted']['anomalies'][0]['value']==65 and response.json()['note']==s['note']


def test_revoked_operator_and_automatic_flow_are_not_bypassed(client,account):
 from generic_agents.models import InstanceRelease,Membership
 r,obj,obs,_,_=setup(client);s=signals(client,r)[0]
 with Session(engine()) as db:
  release=db.get(InstanceRelease,r['id']);data=copy.deepcopy(release.data)
  chosen=next(b for b in data['snapshot']['businesses'] if b['id']==r['available_business_ids'][0])
  chosen['flow']['steps'][0]['agent']='多模感知智能体';release.data=data;db.commit()
 response=client.post(f"/api/workspace/instances/{r['instance_id']}/tasks",json=task_body(r,obj,s))
 assert response.status_code==409 and '人工核查' in response.text
 with Session(engine()) as db:
  member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='viewer';db.commit()
 response=client.put(f"/api/workspace/instances/{r['instance_id']}/signals/{s['id']}/note",json={'version':s['version'],
  'request_id':str(uuid4()),'note':'不允许越权保存'})
 assert response.status_code==403
 with Session(engine()) as db:assert not db.scalars(select(Task)).all() and not db.scalars(select(CloudCall)).all()
