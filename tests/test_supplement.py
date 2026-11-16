"""Synthetic stream fixture contracts; actual local CPU/RTSP and browser proof are separate."""
import copy
import pytest
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import SupplementPlan,StreamSession,CloudCall,VideoDomainBinding,Entity,Membership,Job,User
from generic_agents.worker import process_once
from generic_agents.supplement import schedule_once
from tests.test_instance_perception import linked
from tests.test_media import finish,media_runtime
from tests.test_local_specialists import mocked_runtime
from tests.test_evidence_assessment import run,request

@pytest.fixture(autouse=True)
def explicit_no_alert_fixture(media_runtime,monkeypatch):
 from generic_agents import local_video
 original=local_video.execute_video
 def execute(content,backend,configuration,checkpoints,guard,receive,lock_fd=None):
  def report(value,temp):
   if value.get('kind')=='frame' and configuration.get('presence_rule') is None:value={**value,'events':[]}
   receive(value,temp)
  return original(content,backend,configuration,checkpoints,guard,report,lock_fd=lock_fd)
 monkeypatch.setattr(local_video,'execute_video',execute)

def proposal(client,r,obj,**kw):
 assessment=run(client,request(r,[obj['id']],required_modalities=['video'],**kw));body={'assessment_id':assessment['id'],'request_id':str(uuid4())}
 response=client.post('/api/supplement-plans',json=body);assert response.status_code==201,response.text
 return assessment,response.json(),body

def approve(client,row,**kw):
 body={'version':row['version'],'action_keys':[row['proposal']['actions'][0]['key']],'seconds':4,'confirm_one_time_read':True,'request_id':str(uuid4()),**kw}
 return client.post('/api/supplement-plans/'+row['id']+'/approve',json=body),body

def test_saved_missing_video_to_confirmed_read_local_recheck_and_idempotency(client,media_runtime):
 installation,ch,r,obj,_,_=linked(client);old,row,body=proposal(client,r,obj)
 assert old['result']['objects'][0]['processing_records']==0 and len(row['proposal']['actions'])==1
 assert client.post('/api/supplement-plans',json=body).json()['id']==row['id']
 response,action=approve(client,row);assert response.status_code==200,response.text;row=response.json()
 replay=client.post('/api/supplement-plans/'+row['id']+'/approve',json=action);assert replay.json()['sessions']==row['sessions']
 initial=client.get('/api/media/sessions').json();session=next(s for s in initial if s['id']==row['sessions'][0]['id']);assert session['domain_binding']['entity_id']==obj['id'] and session['configuration']['presence_rule'] is None
 _,video=finish(client,session);assert video['status']=='succeeded',video.get('error');schedule_once();row=client.get('/api/supplement-plans/'+row['id']).json();assert row['status']=='rechecking'
 assessment=client.get('/api/evidence-assessments/'+row['followup_assessment_id']).json();assert process_once(assessment['job_id'],deliver=False);schedule_once();schedule_once()
 row=client.get('/api/supplement-plans/'+row['id']).json();assert row['status']=='completed'
 compare=row['comparison'][0];assert compare['before_processing_records']==0 and compare['after_processing_records']==1 and not compare['current_state_sufficient']
 assert {'source_time','coverage','domain_quality'}<={g['kind'] for g in compare['remaining_gaps']}
 assert client.get('/api/evidence-assessments/'+old['id']).json()==old
 assert client.get('/api/supplement-plans/'+row['id']+'/export').json()['plan']==row
 with Session(engine()) as db:assert db.scalar(select(CloudCall.id)) is None


def test_unknown_modalities_do_not_silently_read_video(client,media_runtime):
 _,_,r,obj,_,_=linked(client);_,row,_=proposal(client,r,obj,goal='available_evidence')
 # Video is allowed for its own explicit gap; other missing types remain unimplemented.
 old=run(client,request(r,[obj['id']],required_modalities=['audio','imagery']))
 row=client.post('/api/supplement-plans',json={'assessment_id':old['id'],'request_id':str(uuid4())}).json();assert row['proposal']['actions']==[]
 needs=row['proposal']['objects'][0]['needs'];assert len([g for g in needs if g['kind']=='missing_saved_processing' and g.get('resource_issue')])==2
 response=client.post('/api/supplement-plans/'+row['id']+'/approve',json={'version':row['version'],'action_keys':['invented'],'seconds':4,'confirm_one_time_read':True,'request_id':str(uuid4())});assert response.status_code==409


def test_binding_change_and_object_change_block_saved_approval(client,media_runtime):
 _,ch,r,obj,_,binding=linked(client);_,row,_=proposal(client,r,obj)
 response=client.post('/api/media/domain-bindings/'+binding['id']+'/disable',json={'version':binding['version']});assert response.status_code==200
 response,_=approve(client,row);assert response.status_code==409
 with Session(engine()) as db:assert not list(db.scalars(select(StreamSession).where(StreamSession.data['supplement_plan_id'].as_string()==row['id'])))
 with Session(engine()) as db:entity=db.get(Entity,obj['id']);entity.version+=1;db.commit()
 assert client.post('/api/supplement-plans',json={'assessment_id':row['assessment_id'],'request_id':str(uuid4())}).status_code==409


def test_cancel_saved_read_preserves_original_and_no_followup(client,media_runtime):
 _,_,r,obj,_,_=linked(client);old,row,_=proposal(client,r,obj);response,_=approve(client,row);row=response.json()
 response=client.post('/api/supplement-plans/'+row['id']+'/cancel',json={'version':row['version']});assert response.status_code==200
 session_id=row['sessions'][0]['id'];session=next(s for s in client.get('/api/media/sessions').json() if s['id']==session_id);assert session['status']=='cancelled'
 assert process_once(session['job_id'],deliver=False);schedule_once();row=client.get('/api/supplement-plans/'+row['id']).json();assert row['status']=='cancelled' and 'followup_assessment_id' not in row
 assert client.get('/api/evidence-assessments/'+old['id']).json()==old


def test_busy_source_is_not_reused_and_admin_confirmation_required(client,media_runtime,account):
 installation,ch,r,obj,_,_=linked(client);_,row,_=proposal(client,r,obj)
 from tests.test_media import capture
 active=capture(client,ch,installation).json();response,_=approve(client,row);assert response.status_code==409
 assert client.post('/api/supplement-plans/'+row['id']+'/approve',json={'version':row['version'],'action_keys':[row['proposal']['actions'][0]['key']],'seconds':4,'confirm_one_time_read':False,'request_id':str(uuid4())}).status_code==422
 with Session(engine()) as db:membership=db.scalar(select(Membership).join(User,User.id==Membership.user_id).where(User.username==account[0]));membership.role='operator';db.commit()
 response,_=approve(client,row);assert response.status_code==403


def test_read_failure_still_rechecks_without_repeating_capture(client,media_runtime,monkeypatch):
 _,_,r,obj,_,_=linked(client);_,row,_=proposal(client,r,obj);response,_=approve(client,row);row=response.json();session=row['sessions'][0]
 def fail(*args,**kw):raise ValueError('fixture stream offline')
 monkeypatch.setattr('generic_agents.media.child',fail)
 assert process_once(session['job_id'],deliver=False);schedule_once();row=client.get('/api/supplement-plans/'+row['id']).json();assert row['status']=='rechecking' and row['has_failed_or_partial_reads']
 result=client.get('/api/evidence-assessments/'+row['followup_assessment_id']).json();assert process_once(result['job_id'],deliver=False);schedule_once();row=client.get('/api/supplement-plans/'+row['id']).json();assert row['status']=='completed' and row['comparison'][0]['after_processing_records']==0
 with Session(engine()) as db:assert len(list(db.scalars(select(StreamSession).where(StreamSession.data['supplement_plan_id'].as_string()==row['id']))))==1


def test_source_changed_after_approval_blocks_owned_read_and_keeps_original(client,media_runtime):
 _,ch,r,obj,_,_=linked(client);old,row,_=proposal(client,r,obj);response,_=approve(client,row);row=response.json()
 assert client.post('/api/media/channels/'+ch['id']+'/disable',json={'version':ch['version']}).status_code==200
 schedule_once();row=client.get('/api/supplement-plans/'+row['id']).json();assert row['status']=='blocked' and row.get('followup_assessment_id') is None
 session=next(s for s in client.get('/api/media/sessions').json() if s['id']==row['sessions'][0]['id']);assert session['status']=='cancelled'
 assert client.get('/api/evidence-assessments/'+old['id']).json()==old


def test_lost_admin_authority_stops_read_before_any_new_result(client,media_runtime,account):
 _,_,r,obj,_,_=linked(client);_,row,_=proposal(client,r,obj);response,_=approve(client,row);row=response.json()
 with Session(engine()) as db:
  m=db.scalar(select(Membership).join(User,User.id==Membership.user_id).where(User.username==account[0]));m.role='operator';db.commit()
 schedule_once();row=client.get('/api/supplement-plans/'+row['id']).json();assert row['status']=='blocked'
 session=next(s for s in client.get('/api/media/sessions').json() if s['id']==row['sessions'][0]['id']);assert session['status']=='cancelled'


def test_cancel_during_followup_commit_cannot_resurrect_plan(client,media_runtime,monkeypatch):
 _,_,r,obj,_,_=linked(client);_,row,_=proposal(client,r,obj);response,_=approve(client,row);row=response.json()
 session=next(s for s in client.get('/api/media/sessions').json() if s['id']==row['sessions'][0]['id']);finish(client,session)
 from generic_agents import evidence_assessment_api
 real=evidence_assessment_api.create
 def stop_after_save(body,p,db):
  result=real(body,p,db)
  current=client.get('/api/supplement-plans/'+row['id']).json()
  assert client.post('/api/supplement-plans/'+row['id']+'/cancel',json={'version':current['version']}).status_code==200
  return result
 monkeypatch.setattr(evidence_assessment_api,'create',stop_after_save);schedule_once()
 row=client.get('/api/supplement-plans/'+row['id']).json();assert row['status']=='cancelled'
 result=client.get('/api/evidence-assessments/'+row['followup_assessment_id']).json();assert result['status']=='cancelled'
 schedule_once();assert client.get('/api/supplement-plans/'+row['id']).json()==row


def test_cancel_followup_preserves_read_original_and_result_hash_guards(client,media_runtime):
 _,_,r,obj,_,_=linked(client);_,row,_=proposal(client,r,obj);response,_=approve(client,row);row=response.json()
 session=next(s for s in client.get('/api/media/sessions').json() if s['id']==row['sessions'][0]['id']);finish(client,session);schedule_once()
 row=client.get('/api/supplement-plans/'+row['id']).json();assert row['status']=='rechecking'
 assert client.post('/api/supplement-plans/'+row['id']+'/cancel',json={'version':row['version']}).status_code==200
 child=client.get('/api/evidence-assessments/'+row['followup_assessment_id']).json();assert child['status']=='cancelled'
 session=next(s for s in client.get('/api/media/sessions').json() if s['id']==session['id']);assert session['status']=='captured' and session['processing_status']=='succeeded'
 # Corruption is injected only in this disposable test database, never the running app.
 with Session(engine()) as db:
  saved=db.get(SupplementPlan,row['id']);saved.data={**saved.data,'source_receipts':[]};db.commit()
 assert client.get('/api/supplement-plans/'+row['id']+'/export').status_code==409


def test_saved_request_cannot_change_actions_and_failed_confirmation_creates_no_read(client,media_runtime):
 _,_,r,obj,_,_=linked(client);_,row,body=proposal(client,r,obj)
 changed={**body,'assessment_id':'unknown'};assert client.post('/api/supplement-plans',json=changed).status_code==409
 response,action=approve(client,row,action_keys=['unknown']);assert response.status_code==409
 response,action=approve(client,row);assert response.status_code==200
 assert client.post('/api/supplement-plans/'+row['id']+'/approve',json={**action,'seconds':5}).status_code==409
 with Session(engine()) as db:assert len(list(db.scalars(select(StreamSession).where(StreamSession.data['supplement_plan_id'].as_string()==row['id']))))==1
