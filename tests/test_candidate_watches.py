"""Actual DB/API jobs and receipts; native inference separately proven in browser."""
import copy
import pytest
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.worker import process_once,deliver_events
from tests.test_local_observations import setup,finish,mocked_runtime
from tests.test_instance_workspace import task_row,complete,object_row

@pytest.fixture
def candidate_runtime(mocked_runtime,monkeypatch):
 import tests.test_local_specialists as runtime
 original=runtime.output
 def result(*args,**kw):
  value=original(*args,**kw);value['detections']=[{'label':'person','label_id':0,'score':0.8,'bbox':[1,1,15,15]}];return value
 monkeypatch.setattr(runtime,'output',result)

def watch(client,base,obj,profile,**kw):
 body={'request_id':uuid4().hex,'name':'公开候选只读筛选','entity_id':obj['id'],'profile':profile,'operator':'gte','threshold':1,'confirm_candidate_only':True,**kw}
 response=client.post(base+'/candidate-watches',json=body);assert response.status_code==201,response.text
 return response.json(),body

def jobs(client,base,w):
 response=client.post(base+'/candidate-watches/'+w['id']+'/check-existing',json={'version':w['version']});assert response.status_code==200,response.text
 for j in response.json()['job_ids']:
  with Session(engine()) as db:status=db.get(m.Job,j).status
  if status=='queued':assert process_once(j,deliver=False)
  else:assert status=='succeeded'
 deliver_events()
 return response.json()['job_ids']

def test_local_candidate_real_receipt_explicit_rule_and_manual_business_no_state_or_cloud(client,candidate_runtime):
 release,obj,base,body,row=setup(client);w,b=watch(client,base,obj,'local:'+body['extractor_id']);finish(client,base,row)
 # An upload created before policy confirmation is not silently backfilled.
 with Session(engine()) as db:assert not db.scalars(select(m.Job).where(m.Job.kind=='check_candidate_watch')).all()
 ids=jobs(client,base,w);assert len(ids)==1
 assert client.post(base+'/candidate-watches',json=b).json()['id']==w['id']
 checks=client.get(base+'/candidate-watches/'+w['id']+'/checks').json()['records'];assert checks[0]['result']['matched']
 signal=client.get(base+'/signals').json()['records'][0];assert signal['basis']['candidate_only'] and signal['basis']['reported_at'] is None and not signal['integrity_issue']
 taskbody={'release_id':release['id'],'business_id':release['available_business_ids'][0],'entity_ids':[obj['id']],
    'title':'核查公开本地模型线索','request_id':uuid4().hex,'signal_id':signal['id'],'signal_version':signal['version'],'signal_basis_hash':signal['basis_hash']}
 task=client.post(base+'/tasks',json=taskbody);assert task.status_code==201,task.text
 first=complete(client,task.json());assert first.status_code==200,first.text
 task=first.json();failed=complete(client,task);assert failed.status_code==422,failed.text
 done=complete(client,task,perception_decision='uncertain');assert done.status_code==200,done.text
 export=client.get(base+'/signals/'+signal['id']+'/export');assert export.status_code==200 and export.json()['business_review']['decision']=='uncertain'
 jobs(client,base,w);deliver_events()
 assert len(client.get(base+'/signals').json()['records'])==1
 with Session(engine()) as db:
  assert db.get(m.Entity,obj['id']).version==1 and not db.scalars(select(m.CloudCall)).all()
  assert db.get(m.Memory,done.json()['workflow_runtime']['data_receipt_id']).status=='candidate'

def test_future_local_receipt_auto_schedules_and_pause_is_versioned(client,candidate_runtime):
 release,obj,base,body,row=setup(client);w,_=watch(client,base,obj,'local:'+body['extractor_id'])
 body={**body,'request_id':uuid4().hex};response=client.post(base+'/local-observations',json=body);assert response.status_code==201
 finish(client,base,response.json())
 with Session(engine()) as db:
  job=db.scalar(select(m.Job).where(m.Job.kind=='check_candidate_watch'));assert job is not None;identity=job.id
 paused=client.put(base+'/candidate-watches/'+w['id']+'/status',json={'version':w['version'],'status':'paused'});assert paused.status_code==200
 assert process_once(identity,deliver=False)
 assert not client.get(base+'/signals').json()['records']
 with Session(engine()) as db:assert db.get(m.Job,identity).status=='failed'
 assert client.put(base+'/candidate-watches/'+w['id']+'/status',json={'version':w['version'],'status':'enabled'}).status_code==409
 resumed=client.put(base+'/candidate-watches/'+w['id']+'/status',json={'version':paused.json()['version'],'status':'enabled'});assert resumed.status_code==200,resumed.text
 jobs(client,base,resumed.json());assert len(client.get(base+'/signals').json()['records'])==1

def test_nonmatch_rejection_corrupt_receipt_and_notes_recover_without_new_inference(client,candidate_runtime):
 release,obj,base,body,row=setup(client);finish(client,base,row)
 none,_=watch(client,base,obj,'local:'+body['extractor_id'],threshold=99);jobs(client,base,none)
 assert not client.get(base+'/signals').json()['records']
 w,_=watch(client,base,obj,'local:'+body['extractor_id']);jobs(client,base,w);signal=client.get(base+'/signals').json()['records'][0]
 response=client.put(base+'/signals/'+signal['id']+'/note',json={'version':signal['version'],'request_id':uuid4().hex,'note':'人工说明必须保留'});assert response.status_code==200
 with Session(engine()) as db:
  observation=db.get(m.Observation,row['id']);old=copy.deepcopy(observation.data);observation.data={**old,'extracted':{**old['extracted'],'attributes':{'tampered':1}}};db.commit()
 broken=client.get(base+'/signals').json()['records'][0];assert broken['basis'] is None and broken['integrity_issue'] and broken['note']=='人工说明必须保留'
 assert client.get(base+'/signals/'+signal['id']+'/export').status_code==409
 with Session(engine()) as db:db.get(m.Observation,row['id']).data=old;db.commit()
 assert client.get(base+'/signals/'+signal['id']+'/export').status_code==200
 current=client.get(base+'/local-observations/'+row['id']).json()
 rejected=client.post('/api/observations/'+row['id']+'/verify',json={'expected_version':current['version'],'decision':'rejected','reason':'候选不支持业务异常'});assert rejected.status_code==200
 assert client.get(base+'/signals').json()['records'][0]['source_review']=='rejected'

def test_rule_scope_confirmation_and_invalid_conditions(client,candidate_runtime):
 release,obj,base,body,row=setup(client)
 valid={'request_id':uuid4().hex,'name':'公开规则','entity_id':obj['id'],'profile':'local:'+body['extractor_id'],'operator':'gte','threshold':1,'confirm_candidate_only':True}
 for change in [{'confirm_candidate_only':False},{'threshold':True},{'threshold':0},{'threshold':1.2},{'name':' '},{'profile':'unknown'}]:
  assert client.post(base+'/candidate-watches',json={**valid,**change}).status_code in {409,422}
 other=object_row(client,release,1);w,_=watch(client,base,other,'local:'+body['extractor_id']);finish(client,base,row);assert not jobs(client,base,w)

from tests.test_native_receipts import setup_geo,bridge,scenes,pairs,pytestmark

def test_native_difference_notification_preserves_original_pair_and_uncertainty(client,pairs):
 a,b,release,body,base,task=setup_geo(client,pairs)
 with Session(engine()) as db:obj=db.get(m.Entity,body['entity_ids'][0]);object_id=obj.id
 w,_=watch(client,base,{'id':object_id},'native:GeoPair',threshold=5)
 _,listing=bridge(client,base)
 with Session(engine()) as db:ids=db.scalars(select(m.Job.id).where(m.Job.kind=='check_candidate_watch')).all();assert len(ids)==1
 for identity in ids:assert process_once(identity,deliver=False)
 deliver_events();signals=client.get(base+'/signals').json()['records'];assert len(signals)==1
 signal=signals[0];assert signal['basis']['extracted']['anomalies'][0]['value']==6 and signal['basis']['reported_at'] is None
 assert signal['basis']['original_model_result']['source_key'].startswith('GeoPair:') and len(signal['basis']['evidence_ids'])==2
 assert client.get(base+'/signals/'+signal['id']+'/export').status_code==200
 # Same derived original, old check and repeated delivery cannot create another signal.
 jobs(client,base,w);assert len(client.get(base+'/signals').json()['records'])==1


def test_selected_detection_class_and_score_do_not_create_false_positive(client,candidate_runtime):
 release,obj,base,body,row=setup(client);finish(client,base,row)
 for filters in [{'label':'car'},{'min_score':.9}]:
  w,_=watch(client,base,obj,'local:'+body['extractor_id'],**filters);jobs(client,base,w)
 assert not client.get(base+'/signals').json()['records']
 w,_=watch(client,base,obj,'local:'+body['extractor_id'],label='person',min_score=.7);jobs(client,base,w)
 signal=client.get(base+'/signals').json()['records'][0];assert signal['basis']['extracted']['anomalies'][0]['value']==1


def test_native_asr_literal_text_watch_preserves_machine_text_and_no_second_inference(client):
 from tests.test_native_media_receipts import audio,sync,finish_job
 source,cap,release,attached,body=audio(client);base='/api/workspace/instances/'+release['instance_id']
 response=client.post(base+'/tasks',json=body);assert response.status_code==201
 original=finish_job(client,response.json())
 w,_=watch(client,base,{'id':body['entity_ids'][0]},'native:AudioRun',operator='contains',needle='9点')
 sync(client,base)
 with Session(engine()) as db:before=len(db.scalars(select(m.Invocation)).all())
 jobs(client,base,w);signal=client.get(base+'/signals').json()['records'][0]
 assert '9点' in signal['basis']['extracted']['anomalies'][0]['value'] and signal['basis']['reported_at'] is None
 assert signal['basis']['original_model_result']['source_key'].startswith('AudioRun:')
 with Session(engine()) as db:assert len(db.scalars(select(m.Invocation)).all())==before and not db.scalars(select(m.CloudCall)).all()


def test_revoked_creator_and_foreign_instance_cannot_evaluate_or_publish(client,candidate_runtime,account):
 release,obj,base,body,row=setup(client);w,_=watch(client,base,obj,'local:'+body['extractor_id']);finish(client,base,row)
 queued=client.post(base+'/candidate-watches/'+w['id']+'/check-existing',json={'version':w['version']}).json()['job_ids']
 from tests.test_instance_workspace import prepared,enable
 from tests.test_instance_workspace import configuration
 from generic_agents.instance_compiler import signature_value
 import json
 config=configuration();config['instanceProfile']['name']='另一独立验证实例'
 for flow in config['flows'].values():flow['confirmedSignature']=json.dumps(signature_value(config,flow),ensure_ascii=False)
 other,_,_=prepared(client,config);enable(client,other)
 foreign='/api/workspace/instances/'+other['instance_id']
 assert client.get(foreign+'/candidate-watches/'+w['id']+'/checks').status_code==404
 with Session(engine()) as db:member=db.scalar(select(m.Membership).where(m.Membership.workspace_id==account[2]));member.role='viewer';db.commit()
 assert client.post(base+'/candidate-watches',json={'request_id':uuid4().hex,'name':'不允许','entity_id':obj['id'],'profile':'local:'+body['extractor_id'],'operator':'gte','threshold':1,'confirm_candidate_only':True}).status_code==403
 for j in queued:assert process_once(j,deliver=False)
 deliver_events();assert not client.get(base+'/signals').json()['records']
 with Session(engine()) as db:assert all(db.get(m.Job,j).status=='failed' for j in queued)


def test_copied_signed_native_processing_cannot_borrow_original_receipts_or_create_second_notification(client,pairs):
 from generic_agents.native_receipts import packet,checked
 from generic_agents.contracts import digest
 from generic_agents.worker import actor
 a,b,r,body,base,t=setup_geo(client,pairs);bridge(client,base)
 with Session(engine()) as db:
  row=db.scalar(select(m.Observation).where(m.Observation.data['native_instance_id'].as_string()==r['instance_id']))
  p=actor(db,row.workspace_id,row.data['submitted_by']);record=checked(db,p,row);v=packet(db,p,record,row.entity_id,version='native-observation-v1')
  legacy=m.Observation(workspace_id=row.workspace_id,source_id=row.source_id,source_record_id=v['source_key']+':'+v['entity_id'],processing_version='native-v1',entity_id=row.entity_id,observed_at=None,business_day=v['business_day'],status='processed',
   data={**row.data,'native_packet':v,'native_packet_hash':digest(v),'extracted':{'attributes':v['attributes']},'evidence_ids':v['evidence_ids']})
  # This is not an old unsigned observation: the copied data contains the
  # modern producer's independent receipt markers. A new row cannot borrow
  # that row's Event or Audit, even with a plausible normalizer-v1 packet.
  db.add(legacy);db.commit();original_id=row.id;copied_id=legacy.id
 w,_=watch(client,base,{'id':body['entity_ids'][0]},'native:GeoPair',threshold=5)
 ids=jobs(client,base,w);assert len(ids)==2
 signals=client.get(base+'/signals').json()['records'];assert len(signals)==1
 with Session(engine()) as db:
  records=[db.get(m.Job,identity) for identity in ids]
  original=next(j for j in records if j.data['observation_id']==original_id)
  copied=next(j for j in records if j.data['observation_id']==copied_id)
  assert original.status=='succeeded' and original.data['result']['matched']
  assert copied.status=='failed' and copied.data.get('error') and not copied.data.get('result')
  event_id=original.data['result']['event_id']
  assert db.get(m.PerceptionSignal,signals[0]['id']).event_id==event_id
 checks=client.get(base+'/candidate-watches/'+w['id']+'/checks').json()['records']
 good_checks=[j for j in checks if j['result'] is not None]
 blocked_checks=[j for j in checks if j['result'] is None]
 assert len(good_checks)==len(blocked_checks)==1
 assert good_checks[0]['result']['event_id']==event_id
 assert blocked_checks[0]['status']=='failed' and blocked_checks[0]['error']
