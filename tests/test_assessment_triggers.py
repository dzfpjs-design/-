"""Configured autonomous local checks. Synthetic inference; separate runtime proof required."""
from datetime import timedelta,datetime,timezone
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import EvidenceAssessment,Job,VideoRun,SupplementWatch,Task
from generic_agents.supplement_watch import schedule_once
from generic_agents.worker import process_once
from tests.test_supplement_watch import configured,body,get,plans,complete
from tests.test_media import media_runtime,capture,finish
from tests.test_local_specialists import mocked_runtime
from tests.test_supplement import explicit_no_alert_fixture

def authorization(client,p,**kw):
 req=body(p,check_business_triggers=True,**kw);res=client.post('/api/supplement-watches',json=req);assert res.status_code==201,res.text;return res.json(),req

def process_check(client,row,index=-1):
 item=get(client,row)['automatic_checks'][index];r=client.get('/api/evidence-assessments/'+item['assessment_id']).json();assert process_once(r['job_id'],deliver=False);return client.get('/api/evidence-assessments/'+r['id']).json()

def tick_after(monkeypatch,seconds):
 from generic_agents import supplement_watch
 actual=supplement_watch.clock;monkeypatch.setattr(supplement_watch,'clock',lambda db:actual(db)+timedelta(seconds=seconds))

def test_business_event_autonomously_checks_then_reads_once_then_new_data_checks_without_reread(client,media_runtime,monkeypatch):
 r,obj,p,_,_=configured(client,triggers=['新观测到达']);row,_=authorization(client,p)
 schedule_once();row=get(client,row);assert len(row['automatic_checks'])==1 and not plans(client,row)
 schedule_once();assert len(get(client,row)['automatic_checks'])==1
 initial=process_check(client,row);assert initial['result']['objects'][0]['processing_records']==0
 schedule_once();queued=plans(client,row)[0];assert queued['assessment_id']==initial['id']
 complete(client,queued);tick_after(monkeypatch,16);schedule_once();row=get(client,row)
 assert len(row['automatic_checks'])==2 and row['automatic_checks'][-1]['reasons']==['对象关联资料或核查结果已变化']
 second=process_check(client,row);assert second['result']['objects'][0]['processing_records']==1
 tick_after(monkeypatch,120);schedule_once();schedule_once();row=get(client,row)
 assert len(plans(client,row))==1 and len(row['automatic_checks'])==2
 assert len(row['history'])==2 and not row['history'][-1].get('plan_id')


def test_old_authorization_and_manual_only_business_do_not_silently_generate_checks(client,media_runtime):
 _,_,p,_,_=configured(client)
 preview=client.post('/api/supplement-watches/check',json={'plan_id':p['id'],'action_key':p['proposal']['actions'][0]['key']}).json()
 assert preview['business_check_triggers'] is None and preview['business_trigger_issue']
 assert client.post('/api/supplement-watches',json=body(p,check_business_triggers=True)).status_code==409
 req=body(p);row=client.post('/api/supplement-watches',json=req).json();schedule_once();assert not get(client,row).get('automatic_checks')
 assert client.post('/api/supplement-watches',json=req).json()['id']==row['id']


def test_daily_china_schedule_once_per_day_and_no_missed_day_backfill(client,media_runtime,monkeypatch):
 r,obj,p,ch,install=configured(client,triggers=['定时运行'],time='08:00')
 finish(client,capture(client,ch,install,configuration={'sample_hz':1,'threshold':.5,'presence_rule':None}).json())
 from generic_agents import supplement_watch
 # The simulated schedule must follow the real fixture's saved read. An old
 # calendar constant moves backwards relative to its read-budget timestamp.
 fixed=datetime.now(timezone.utc).replace(hour=0,minute=30,second=0,microsecond=0)+timedelta(days=1)
 monkeypatch.setattr(supplement_watch,'clock',lambda db:fixed)
 from generic_agents import supplement_watch_api
 monkeypatch.setattr(supplement_watch_api,'clock',lambda db:fixed)
 row,_=authorization(client,p,minutes=1440);schedule_once();process_check(client,row);schedule_once()
 assert get(client,row)['automatic_checks'][0]['scheduled_day']==fixed.astimezone(supplement_watch.CHINA).date().isoformat()
 assert len(get(client,row)['history'])==1 and not get(client,row)['history'][0].get('plan_id')
 monkeypatch.setattr(supplement_watch,'clock',lambda db:fixed+timedelta(hours=23));schedule_once();assert len(get(client,row)['automatic_checks'])==1
 monkeypatch.setattr(supplement_watch,'clock',lambda db:fixed+timedelta(hours=23,minutes=31));schedule_once();row=get(client,row)
 assert len(row['automatic_checks'])==2 and row['automatic_checks'][1]['scheduled_day']==(fixed+timedelta(days=1)).astimezone(supplement_watch.CHINA).date().isoformat()
 assert not plans(client,row)

@pytest.mark.parametrize('triggers,time',[(['人工发起'],None),(['异常事件'],None),(['定时运行'],'25:61'),(['定时运行'],None)])
def test_unsupported_or_invalid_business_trigger_cannot_be_enabled(client,media_runtime,triggers,time):
 if triggers==['定时运行'] and time in {'25:61',None}:
  from tests.test_instance_workspace import configuration,save,OBJECTS
  c=configuration();p=c['automation']['policies']['资料核验'];p['triggers']=triggers
  if time is not None:p['time']=time
  revision=save(client,c);current=client.get('/api/setup/draft').json();scope=current['automation_review']['businesses'][0]
  refused=client.post('/api/setup/automation/review',json={'revision':revision,'business_id':scope['business_id'],'request_id':str(uuid4())})
  assert refused.status_code==422 and refused.json()['detail']=='按计划运行时间无效，未确认自动化范围'
  assert client.get('/api/setup/draft').json()['automation_review']['businesses'][0]['status']=='needs_review'
  member=client.get('/api/instances/members').json()[0]
  checked=client.post('/api/instances/check',json={'revision':revision,'objects':OBJECTS,'role_bindings':{'核验负责人':member['id']}})
  assert checked.status_code==200 and not checked.json()['businesses'][0]['available']
  with Session(engine()) as db:
   assert not db.scalar(select(SupplementWatch.id)) and not db.scalar(select(Task.id))
   assert not db.scalar(select(Job.id).where(Job.kind!='login_limit'))
  return
 _,_,p,_,_=configured(client,triggers=triggers,time=time)
 assert client.post('/api/supplement-watches',json=body(p,check_business_triggers=True)).status_code==409


def test_stopping_or_expiry_cancels_owned_pending_local_check(client,media_runtime,monkeypatch):
 _,_,p,_,_=configured(client,triggers=['新观测到达']);row,_=authorization(client,p);schedule_once()
 check=get(client,row)['automatic_checks'][0];res=client.post('/api/supplement-watches/'+row['id']+'/stop',json={'version':row['version']});assert res.status_code==200
 r=client.get('/api/evidence-assessments/'+check['assessment_id']).json();assert r['status']=='cancelled';process_once(r['job_id'],deliver=False)
 assert client.get('/api/evidence-assessments/'+r['id']).json()['status']=='cancelled' and not plans(client,row)
 row,_=authorization(client,p,minutes=1);schedule_once();check=get(client,row)['automatic_checks'][0];tick_after(monkeypatch,120);schedule_once()
 assert get(client,row)['status']=='expired' and client.get('/api/evidence-assessments/'+check['assessment_id']).json()['status']=='cancelled'


def test_failed_local_check_is_preserved_and_not_automatically_recreated(client,media_runtime):
 _,_,p,_,_=configured(client,triggers=['新观测到达']);row,_=authorization(client,p);schedule_once();check=get(client,row)['automatic_checks'][0]
 with Session(engine()) as db:r=db.get(EvidenceAssessment,check['assessment_id']);r.status='failed';r.data={**r.data,'error':'isolated invalid original'};db.commit()
 schedule_once();schedule_once();saved=get(client,row);assert saved['status']=='blocked' and len(saved['automatic_checks'])==1
 assert not plans(client,row)


def test_creation_and_watch_receipt_rollback_together_without_orphan(client,media_runtime,monkeypatch):
 _,_,p,_,_=configured(client,triggers=['新观测到达']);row,_=authorization(client,p)
 from generic_agents import evidence_assessment_api
 actual=evidence_assessment_api.create_saved
 def failure(*args,**kw):
  result=actual(*args,**kw);raise ValueError('isolated after saved row before watch receipt')
 monkeypatch.setattr(evidence_assessment_api,'create_saved',failure);schedule_once();assert get(client,row)['status']=='blocked' and not get(client,row).get('automatic_checks')
 with Session(engine()) as db:
  assert not list(db.scalars(select(EvidenceAssessment).where(EvidenceAssessment.request_id.like('auto-check-'+row['id']+'%'))))


def test_cancel_unknown_auto_check_confirmation_prevents_late_activation(client,media_runtime):
 _,_,p,_,_=configured(client,triggers=['新观测到达']);req=body(p,check_business_triggers=True)
 assert client.post('/api/supplement-watches/stop-confirmation',json=req).json()['status']=='stopped'
 row=client.post('/api/supplement-watches',json=req).json();schedule_once();assert row['status']=='stopped' and row['authorization'] is None


def test_other_source_metadata_does_not_trigger_selected_object_check(client,media_runtime,monkeypatch):
 _,_,p,_,_=configured(client,triggers=['新观测到达']);row,_=authorization(client,p);schedule_once();process_check(client,row)
 # Keep the matching check unconsumed: native read is not part of this metadata isolation assertion.
 from generic_agents import assessment_triggers
 with Session(engine()) as db:
  from generic_agents.models import SupplementWatch
  from generic_agents.security import Principal
  w=db.get(SupplementWatch,row['id']);principal=Principal(w.owner_id,w.workspace_id,'admin','test');before=assessment_triggers.signature(db,principal,w)
  other=db.scalar(select(VideoRun).where(VideoRun.data['source_context']['domain_binding']['entity_id'].as_string().is_(None)));other.version+=1;db.commit()
  assert assessment_triggers.signature(db,principal,w)==before

def test_human_review_drafts_do_not_count_as_new_verified_data(client,media_runtime):
 r,obj,p,ch,install=configured(client,triggers=['新观测到达']);_,video=finish(client,capture(client,ch,install,configuration={'sample_hz':1,'threshold':.5,'presence_rule':None}).json())
 row,_=authorization(client,p)
 from generic_agents import assessment_triggers
 from generic_agents.models import SupplementWatch,Task
 from generic_agents.security import Principal
 with Session(engine()) as db:
  w=db.get(SupplementWatch,row['id']);actor=Principal(w.owner_id,w.workspace_id,'admin','test');before=assessment_triggers.signature(db,actor,w)
  task=Task(workspace_id=w.workspace_id,owner_id=w.owner_id,package_id=r['package_id'],title='fixture review draft',status='workflow_pending',data={'instance_id':r['instance_id'],'entity_ids':[obj['id']],'run_id':video['id'],'workflow_runtime':{'drafts':{'check':'pending'}}});db.add(task);db.commit()
  assert assessment_triggers.signature(db,actor,w)==before
  task.data={**task.data,'workflow_runtime':{'drafts':{'check':'changed draft'}}};task.version+=1;db.commit();assert assessment_triggers.signature(db,actor,w)==before
  task.status='workflow_completed';task.data={**task.data,'workflow_runtime':{'receipts':[{'execution_mode':'human'}]},'perception_review':{'decision':'not_abnormal'}};task.version+=1;db.commit()
  assert assessment_triggers.signature(db,actor,w)!=before

def test_new_saved_source_supersedes_old_absence_check_without_extra_capture(client,media_runtime,monkeypatch):
 _,_,p,ch,install=configured(client,triggers=['新观测到达']);row,_=authorization(client,p);schedule_once();old=process_check(client,row)
 assert old['result']['objects'][0]['processing_records']==0
 finish(client,capture(client,ch,install,configuration={'sample_hz':1,'threshold':.5,'presence_rule':None}).json())
 # Before the next event poll, the old zero-source result cannot authorize another read.
 schedule_once();saved=get(client,row);assert not plans(client,row) and '已有变化' in saved['history'][0]['decision']
 tick_after(monkeypatch,16);schedule_once();new=process_check(client,row);assert new['result']['objects'][0]['processing_records']==1
 schedule_once();assert not plans(client,row)


def test_exhausted_source_daily_budget_does_not_hide_no_read_decision(client,media_runtime,monkeypatch):
 _,_,p,ch,install=configured(client,triggers=['新观测到达']);row,_=authorization(client,p)
 finish(client,capture(client,ch,install,configuration={'sample_hz':1,'threshold':.5,'presence_rule':None}).json())
 schedule_once();check=process_check(client,row);assert check['result']['objects'][0]['processing_records']==1
 # Another authorization/source may have used the physical read budget. No-read
 # decisions must still be visible without waiting until tomorrow or rereading.
 from generic_agents import supplement_watch
 monkeypatch.setattr(supplement_watch,'history',lambda db,row:[{'plan_id':'spent','at':supplement_watch.clock(db).isoformat()}]*10000)
 schedule_once();saved=get(client,row)
 assert len(saved['history'])==1 and not saved['history'][0].get('plan_id')
 assert '不读取' in saved['history'][0]['decision'] and not plans(client,row)
