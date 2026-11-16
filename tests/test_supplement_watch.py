"""Finite configured automatic reads; inference is a fixture, not model-quality evidence."""
import copy,json
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import SupplementWatch,SupplementPlan,StreamSession,CloudCall,Membership,User
from generic_agents.supplement_watch import schedule_once
from generic_agents.instance_compiler import signature_value
from generic_agents.worker import process_once
from tests.test_media import media_runtime,channel,capture,finish
from tests.test_local_specialists import ready,mocked_runtime
from tests.test_instance_workspace import prepared,object_row
from tests.test_workflow_abilities import tool,conf,enable
from tests.test_evidence_assessment import run,request
from tests.test_supplement import explicit_no_alert_fixture

def configured(client,*,mode='自动运行',type_rule=None,custom=None,associated=True,triggers=None,time=None):
 installation=ready(client);ch=channel(client);finish(client,capture(client,ch,installation).json())
 ch=client.get('/api/media/channels').json()[0];ch=client.post('/api/media/channels/'+ch['id']+'/register',json={'version':ch['version']}).json()
 cap=tool(client,agent='perception');c=conf(cap,agent='多模感知智能体')
 step=c['flows']['资料核验']['steps'][0];step['execution']['automation_action']='observe'
 rid='media-channel:'+ch['id'];c['resources']=[{'id':rid,'category':'video','name':'公开测试视频','details':{'channel_id':ch['id'],'capability_id':ch['capability_id']},'businesses':['资料核验'] if associated else []}]
 c['automation']['policies']['资料核验']['rules']={'observe':{'mode':mode,'resources':[rid]}}
 if triggers is not None:c['automation']['policies']['资料核验']['triggers']=triggers
 if time is not None:c['automation']['policies']['资料核验']['time']=time
 c['equipment']={'types':{'camera':{'read':True,'mode':'禁止执行',**(type_rule or {})}},'devices':{}}
 if custom:c['equipment']['devices'][rid]={'type':'camera','custom':True,'rule':custom}
 f=c['flows']['资料核验'];f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
 r,_,check=prepared(client,c);assert r['available_business_ids'],check;enable(client,r);obj=object_row(client,r)
 response=client.put('/api/media/channels/'+ch['id']+'/domain-binding',json={'channel_version':ch['version'],'entity_id':obj['id'],'entity_version':obj['version'],'reason':'fixture public scene','confirm_scene_association':True,'instance_release_id':r['id'],'business_id':r['available_business_ids'][0]});assert response.status_code==200,response.text
 assessment=run(client,request(r,[obj['id']],required_modalities=['video']))
 response=client.post('/api/supplement-plans',json={'assessment_id':assessment['id'],'request_id':str(uuid4())});assert response.status_code==201,response.text
 return r,obj,client.get('/api/supplement-plans/'+response.json()['id']).json(),ch,installation

def body(plan,**kw):return {'plan_id':plan['id'],'action_key':plan['proposal']['actions'][0]['key'],'request_id':str(uuid4()),'seconds':4,'max_reads':2,'minutes':60,'cooldown_minutes':1,'confirm_automatic_read':True,**kw}
def watch(client,plan,**kw):
 req=body(plan,**kw);res=client.post('/api/supplement-watches',json=req);assert res.status_code==201,res.text;return res.json(),req

def new_assessment(client,r,obj,**kw):return run(client,request(r,[obj['id']],required_modalities=['video'],**kw))
def get(client,row):return client.get('/api/supplement-watches/'+row['id']).json()
def plans(client,row):return [p for p in client.get('/api/supplement-plans?instance_id='+row['instance_id']).json() if p.get('automatic_watch_id')==row['id']]
def complete(client,plan):
 session=next(x for x in client.get('/api/media/sessions').json() if x['id']==plan['sessions'][0]['id']);finish(client,session)
 from generic_agents.supplement import schedule_once as reconcile
 reconcile();plan=client.get('/api/supplement-plans/'+plan['id']).json();a=client.get('/api/evidence-assessments/'+plan['followup_assessment_id']).json();process_once(a['job_id'],deliver=False);reconcile();return client.get('/api/supplement-plans/'+plan['id']).json()

def test_confirm_then_new_gap_reads_once_and_follows_up_without_recursive_capture(client,media_runtime):
 r,obj,template,ch,install=configured(client);old=client.get('/api/evidence-assessments/'+template['assessment_id']).json();row,req=watch(client,template,max_reads=1)
 assert row['authorization']['permissions']['camera_time_window_required'] is False
 assert row['authorization']['control_authorized'] is False and not row['history'];schedule_once();assert not plans(client,row)
 a=new_assessment(client,r,obj);schedule_once();p=plans(client,row)[0];assert p['assessment_id']==a['id'];schedule_once();assert len(plans(client,row))==1
 assert client.post('/api/supplement-watches',json=req).json()['id']==row['id']
 assert client.post('/api/supplement-watches',json={**req,'seconds':5}).status_code==409
 p=complete(client,p);assert p['status']=='completed' and p['comparison'][0]['after_processing_records']==1
 schedule_once();assert get(client,row)['status']=='exhausted' and len(plans(client,row))==1
 assert client.get('/api/evidence-assessments/'+old['id']).json()==old
 with Session(engine()) as db:assert db.scalar(select(CloudCall.id)) is None

@pytest.mark.parametrize('kw',[{'mode':'执行前确认'},{'type_rule':{'read':False}},{'custom':{'read':False}},{'associated':False},{'type_rule':{'readScopeEnabled':True,'readScope':'somewhere'}}])
def test_configuration_permission_intersection_blocks_without_new_reads(client,media_runtime,kw):
 _,_,p,_,_=configured(client,**kw)
 before=len(client.get('/api/media/sessions').json())
 assert client.post('/api/supplement-watches/check',json={'plan_id':p['id'],'action_key':p['proposal']['actions'][0]['key']}).status_code==409
 assert client.post('/api/supplement-watches',json=body(p)).status_code==409
 assert len(client.get('/api/media/sessions').json())==before


def test_default_old_human_instance_cannot_bypass_suggest_rules(client,media_runtime):
 from tests.test_instance_perception import linked
 _,_,r,obj,_,_=linked(client);a=new_assessment(client,r,obj)
 p=client.post('/api/supplement-plans',json={'assessment_id':a['id'],'request_id':str(uuid4())}).json()
 assert client.post('/api/supplement-watches',json=body(p)).status_code==409


def test_stricter_daily_and_cooldown_limits_and_physical_duplicate(client,media_runtime):
 r,obj,p,ch,_=configured(client,type_rule={'limitEnabled':True,'limit':1,'cooldownEnabled':True,'cooldown':15},custom={'read':True,'limitEnabled':True,'limit':2,'cooldownEnabled':True,'cooldown':20})
 # Setup validation occurred yesterday; today has one business read available.
 with Session(engine()) as db:
  from generic_agents.models import VideoReadReservation
  for receipt in db.scalars(select(VideoReadReservation).where(VideoReadReservation.channel_id==ch['id'])):
   from generic_agents.contracts import digest
   receipt.created_at-=timedelta(days=1);saved={**receipt.data['receipt'],'reserved_at':receipt.created_at.isoformat()}
   receipt.data={'receipt':saved,'receipt_hash':digest(saved)}
  db.commit()
 row,_=watch(client,p,max_reads=5,cooldown_minutes=1);assert row['authorization']['max_reads']==1 and row['authorization']['cooldown_minutes']==20
 assert client.post('/api/supplement-watches',json=body(p)).status_code==409
 new_assessment(client,r,obj);schedule_once();assert len(plans(client,row))==1


def test_nonmatching_and_late_completed_checks_not_skipped(client,media_runtime):
 r,obj,p,_,_=configured(client);row,_=watch(client,p)
 new_assessment(client,r,obj,purpose='different confirmed use');schedule_once();assert not plans(client,row)
 # Newly queued matching check is invisible until completion, then remains eligible.
 req=request(r,[obj['id']],required_modalities=['video']);res=client.post('/api/evidence-assessments',json=req);assert res.status_code==201;assessment=res.json()
 schedule_once();assert not plans(client,row);assert process_once(assessment['job_id'],deliver=False)
 schedule_once();assert len(plans(client,row))==1


def test_stop_and_expiry_cancel_own_read_without_replaying_and_preserve_template(client,media_runtime,monkeypatch):
 r,obj,p,_,_=configured(client);row,_=watch(client,p);a=new_assessment(client,r,obj);schedule_once();queued=plans(client,row)[0]
 assert client.post('/api/supplement-watches/'+row['id']+'/stop',json={'version':row['version']}).status_code==200
 assert client.get('/api/supplement-plans/'+queued['id']).json()['status']=='blocked';assert get(client,row)['status']=='stopped'
 assert client.get('/api/supplement-plans/'+p['id']).json()==p


def test_expired_authorization_stops_queued_read(client,media_runtime,monkeypatch):
 r,obj,p,_,_=configured(client);row2,_=watch(client,p,minutes=1);new_assessment(client,r,obj);schedule_once();queued2=plans(client,row2)[0]
 from generic_agents import supplement_watch
 actual=supplement_watch.clock;monkeypatch.setattr(supplement_watch,'clock',lambda db:actual(db)+timedelta(minutes=2))
 schedule_once();assert get(client,row2)['status']=='expired';assert client.get('/api/supplement-plans/'+queued2['id']).json()['status']=='blocked'
 schedule_once();assert len(plans(client,row2))==1


def test_revoke_admin_stops_only_owned_plan(client,media_runtime,account):
 r,obj,p,_,_=configured(client);row,_=watch(client,p);new_assessment(client,r,obj);schedule_once();queued=plans(client,row)[0]
 with Session(engine()) as db:
  m=db.scalar(select(Membership).join(User,User.id==Membership.user_id).where(User.username==account[0]));m.role='operator';db.commit()
 schedule_once();assert get(client,row)['status']=='blocked';assert client.get('/api/supplement-plans/'+queued['id']).json()['status']=='blocked'
 assert client.post('/api/supplement-watches',json=body(p)).status_code==403


def test_read_failure_consumes_once_and_followup_not_a_new_trigger(client,media_runtime,monkeypatch):
 r,obj,p,_,_=configured(client);row,_=watch(client,p);new_assessment(client,r,obj);schedule_once();queued=plans(client,row)[0]
 monkeypatch.setattr('generic_agents.media.child',lambda *a,**kw:(_ for _ in ()).throw(ValueError('fixture offline')))
 s=next(x for x in client.get('/api/media/sessions').json() if x['id']==queued['sessions'][0]['id']);process_once(s['job_id'],deliver=False)
 from generic_agents.supplement import schedule_once as reconcile
 reconcile();queued=client.get('/api/supplement-plans/'+queued['id']).json();a=client.get('/api/evidence-assessments/'+queued['followup_assessment_id']).json();process_once(a['job_id'],deliver=False);reconcile();schedule_once()
 assert len(plans(client,row))==1 and len(get(client,row)['history'])==1


def test_source_busy_waits_without_consuming_budget(client,media_runtime):
 r,obj,p,ch,install=configured(client);row,_=watch(client,p);new_assessment(client,r,obj)
 extra=capture(client,ch,install).json();schedule_once();assert not plans(client,row) and get(client,row)['history']==[]
 assert '等待' in get(client,row)['progress']
 client.post('/api/media/sessions/'+extra['id']+'/cancel',json={'version':extra['version']})
 # Own fixture DB cancellation has no capture side effect.
 with Session(engine()) as db:
  s=db.get(StreamSession,extra['id']);s.status='cancelled';db.commit()
 schedule_once();assert len(plans(client,row))==1


def test_saved_video_remaining_quality_gaps_do_not_trigger_reread(client,media_runtime,monkeypatch):
 r,obj,p,_,_=configured(client);row,_=watch(client,p);new_assessment(client,r,obj);schedule_once();complete(client,plans(client,row)[0])
 new=new_assessment(client,r,obj);assert new['result']['objects'][0]['processing_records']==1
 from generic_agents import supplement_watch
 actual=supplement_watch.clock;monkeypatch.setattr(supplement_watch,'clock',lambda db:actual(db)+timedelta(minutes=2))
 schedule_once();assert len(plans(client,row))==1 and len(get(client,row)['history'])==2
 assert not get(client,row)['history'][-1].get('plan_id')


def test_new_authorization_does_not_reset_daily_source_budget(client,media_runtime,monkeypatch):
 r,obj,p,ch,_=configured(client,type_rule={'limitEnabled':True,'limit':1})
 # Setup validation is a prior-day request, not part of today's one-read budget.
 with Session(engine()) as db:
  from generic_agents.models import VideoReadReservation
  for receipt in db.scalars(select(VideoReadReservation).where(VideoReadReservation.channel_id==ch['id'])):
   from generic_agents.contracts import digest
   receipt.created_at-=timedelta(days=1);saved={**receipt.data['receipt'],'reserved_at':receipt.created_at.isoformat()}
   receipt.data={'receipt':saved,'receipt_hash':digest(saved)}
  db.commit()
 row,_=watch(client,p);new_assessment(client,r,obj);schedule_once()
 queued=plans(client,row)[0]
 monkeypatch.setattr('generic_agents.media.child',lambda *a,**kw:(_ for _ in ()).throw(ValueError('fixture offline')))
 s=next(x for x in client.get('/api/media/sessions').json() if x['id']==queued['sessions'][0]['id']);process_once(s['job_id'],deliver=False)
 from generic_agents.supplement import schedule_once as reconcile
 reconcile();a=client.get('/api/supplement-plans/'+queued['id']).json();child=client.get('/api/evidence-assessments/'+a['followup_assessment_id']).json();process_once(child['job_id'],deliver=False);reconcile();schedule_once();assert get(client,row)['status']=='exhausted'
 row2,_=watch(client,p);new_assessment(client,r,obj);schedule_once();assert not plans(client,row2) and '每日次数' in get(client,row2)['progress']


def test_stop_unknown_confirmation_prevents_late_create_and_reactivation(client,media_runtime):
 r,obj,p,_,_=configured(client);req=body(p)
 stop=client.post('/api/supplement-watches/stop-confirmation',json=req);assert stop.status_code==200,stop.text
 assert stop.json()['status']=='stopped' and stop.json()['authorization'] is None
 late=client.post('/api/supplement-watches',json=req);assert late.status_code==201 and late.json()['id']==stop.json()['id'] and late.json()['status']=='stopped'
 new_assessment(client,r,obj);schedule_once();assert not plans(client,late.json())
 assert client.post('/api/supplement-watches/stop-confirmation',json={**req,'seconds':5}).status_code==409


def test_stopping_saved_confirmation_cancels_only_its_read(client,media_runtime):
 r,obj,p,_,_=configured(client);row,req=watch(client,p);new_assessment(client,r,obj);schedule_once();plan=plans(client,row)[0]
 assert client.post('/api/supplement-watches/stop-confirmation',json=req).json()['status']=='stopped'
 assert client.get('/api/supplement-plans/'+plan['id']).json()['status']=='blocked'
 assert client.post('/api/supplement-watches',json=req).json()['status']=='stopped'


def test_supplement_authorization_reference_is_not_removable(client,media_runtime):
 r,obj,p,_,_=configured(client);row,_=watch(client,p);new_assessment(client,r,obj);schedule_once();plan=plans(client,row)[0]
 with Session(engine()) as db:
  saved=db.get(SupplementPlan,plan['id']);saved.data={k:v for k,v in saved.data.items() if k!='automatic_watch_id'};db.commit()
 from generic_agents.supplement import schedule_once as reconcile
 reconcile();assert client.get('/api/supplement-plans/'+plan['id']).json()['status']=='blocked'
