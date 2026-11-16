"""Actual local Goal jobs and authority fences in disposable PostgreSQL."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job,Task,CloudCall,Acquisition
from generic_agents.native_goal_scoped_worker_v355 import scope,tick
from tests.test_native_goals import setup,define


def new_check(client,base):
    goal=client.get(base).json();body={'request_id':str(uuid4()),'expected_version':goal['version'],'reason':'验证原任务本地自动核查'}
    response=client.post(base+'/check',json=body);assert response.status_code==200,response.text
    return response.json()['check_worker']['job_id']


def test_worker_processes_current_original_goal_without_claiming_old_or_other_task(client,account):
    release,_,task,base,body=setup(client);define(client,base,body)
    with Session(engine()) as db:old_id=db.get(Task,task['id']).data['native_goal_check_job_pin']['job_id']
    current=new_check(client,base)
    other_release,_,other_task,other_base,other_body=setup(client);define(client,other_base,other_body)
    with Session(engine()) as db:other_id=db.get(Task,other_task['id']).data['native_goal_check_job_pin']['job_id']
    result=tick(account[2],release['instance_id'],task['id']);assert result['processed']==1,result
    with Session(engine()) as db:
        done,old,other=db.get(Job,current),db.get(Job,old_id),db.get(Job,other_id)
        assert done.status=='succeeded' and done.data.get('goal_check_id')
        assert old.status=='queued' and old.attempts==0 and other.status=='queued' and other.attempts==0
        assert not db.query(CloudCall).count() and not db.query(Acquisition).count()
    goal=client.get(base).json();assert goal['check'] and goal['check']['evaluation']['completed'] is False


def test_changed_pin_between_selection_and_task_lock_never_executes_old_request(client,account,monkeypatch):
    from generic_agents import worker
    release,_,task,base,body=setup(client);define(client,base,body);first=new_check(client,base)
    original=worker.process_once;replacement=[]
    def race(*args,**kwargs):
        if not replacement:replacement.append(new_check(client,base))
        return original(*args,**kwargs)
    monkeypatch.setattr(worker,'process_once',race)
    result=tick(account[2],release['instance_id'],task['id']);assert result['processed']==1
    with Session(engine()) as db:
        old=db.get(Job,first);new=db.get(Job,replacement[0]);assert old.status=='failed' and not old.data.get('goal_check_id')
        assert new.status=='queued' and new.attempts==0
    monkeypatch.setattr(worker,'process_once',original)
    assert tick(account[2],release['instance_id'],task['id'])['processed']==1
    with Session(engine()) as db:assert db.get(Job,replacement[0]).status=='succeeded'


def test_superseded_expired_running_job_is_settled_without_executing_it(client,account):
    release,_,task,base,body=setup(client);define(client,base,body);old=new_check(client,base);new=new_check(client,base)
    with Session(engine()) as db:
        row=db.get(Job,old);row.status='running';row.attempts=1;row.lease_token='expired-test';row.lease_until=datetime.now(timezone.utc)-timedelta(seconds=5);db.commit()
    result=tick(account[2],release['instance_id'],task['id']);assert result['processed']==2,result
    with Session(engine()) as db:
        previous,current=db.get(Job,old),db.get(Job,new)
        assert previous.status=='failed' and not previous.data.get('goal_check_id') and previous.data['lease_recoveries']==1
        assert current.status=='succeeded' and current.data.get('goal_check_id')


def test_scope_rejects_incomplete_or_noncanonical_ids():
    valid=str(uuid4())
    for values in ((None,valid,valid),(valid,'all',valid),(valid,valid,'{'+valid+'}')):
        with pytest.raises(ValueError):scope(*values)


def test_new_observation_reception_triggers_original_goal_without_promoting_candidate_to_fact(client,account):
    from datetime import datetime,timedelta,timezone
    from sqlalchemy import select
    from generic_agents.models import Event,Memory,Entity
    from generic_agents.worker import process_once,deliver_events
    from tests.test_vertical_slice import manifest,publish
    release,obj,task,base,body=setup(client,source='observations');define(client,base,body)
    initial=tick(account[2],release['instance_id'],task['id']);assert initial['processed']==1
    field=body['goal_spec']['required_fields'][0]
    source=publish(client,manifest('source',name='scoped_goal_source',allowed_agent_roles=['perception'],
        execution={'adapter':'upload'},specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
    rule=publish(client,manifest('tool',name='scoped_goal_rule',allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},
        specification={'rules':[{'id':'high','field':'测试数值','operator':'gt','threshold':50}]},data_policy={'cloud_allowed':False}))
    clock=datetime.now(timezone.utc)-timedelta(seconds=1)
    request={'source_id':source['id'],'source_record_id':str(uuid4()),'entity_id':obj['id'],'extractor_id':rule['id'],
        'observed_at':clock.isoformat(),'business_day':str((clock+timedelta(hours=8)).date()),'startup_mode':'manual',
        'attributes':{'测试数值':1,field:obj['attributes'][field]}}
    response=client.post('/api/workspace/instances/'+release['instance_id']+'/reported-observations',json=request)
    assert response.status_code==201,response.text;observation_id=response.json()['id']
    with Session(engine()) as db:
        extraction=db.scalar(select(Job.id).where(Job.idempotency_key=='extract:'+observation_id));before_entity=db.get(Entity,obj['id']).version
    assert process_once(extraction,deliver=False)
    deliver_events(workspace_id=account[2],observation_id=observation_id,event_kind='observation.data')
    with Session(engine()) as db:
        event=db.scalar(select(Event).where(Event.workspace_id==account[2],Event.kind=='observation.data',Event.data['observation_id'].as_string()==observation_id))
        assert event and event.status=='received'
        memories=list(db.scalars(select(Memory).where(Memory.workspace_id==account[2],Memory.data['event_id'].as_string()==event.id,Memory.data['type'].as_string()=='observation_evidence')))
        assert len(memories)==1 and memories[0].data['not_authoritative'] is True
        assert isinstance(memories[0].data.get('data_reception'),dict)
        assert memories[0].data['data_reception']['can_execute'] is False
        assert memories[0].data['data_reception']['can_delete'] is False
        pending=db.get(Task,task['id']).data['native_goal_check_job_pin']['job_id']
        assert db.get(Job,pending).status=='queued'
    result=tick(account[2],release['instance_id'],task['id']);assert result['processed']==1,result
    with Session(engine()) as db:
        job=db.get(Job,pending);assert job.status=='succeeded' and job.data.get('goal_check_id')
        assert db.get(Entity,obj['id']).version==before_entity
        assert not db.query(CloudCall).count() and not db.query(Acquisition).count()
    goal=client.get(base).json();assert goal['check']['evaluation']['completed'] is False


def test_current_deadline_timer_is_processed_only_when_due(client,account):
    import time
    release,_,task,base,body=setup(client)
    deadline=datetime.now(timezone.utc)+timedelta(seconds=4)
    body['goal_spec']['deadline']=deadline.isoformat();define(client,base,body)
    assert tick(account[2],release['instance_id'],task['id'])['processed']==1
    with Session(engine()) as db:
        task_row=db.get(Task,task['id']);timer=task_row.data['native_goal_timer'];timer_id=timer['job_id']
        assert db.get(Job,timer_id).status=='queued'
    assert tick(account[2],release['instance_id'],task['id'])['processed']==0
    remaining=(deadline-datetime.now(timezone.utc)).total_seconds()
    if remaining>0:time.sleep(remaining+0.1)
    result=tick(account[2],release['instance_id'],task['id']);assert result['processed']==1,result
    with Session(engine()) as db:
        job=db.get(Job,timer_id);task_row=db.get(Task,task['id'])
        assert job.status=='succeeded' and job.data.get('goal_deadline_recorded') is True
        assert task_row.data.get('native_goal_timed_out')


def test_revoked_actor_cannot_claim_new_goal_but_expired_original_is_settled(client,account):
    from sqlalchemy import select
    from generic_agents.models import Membership,User
    release,_,task,base,body=setup(client);define(client,base,body);identity=new_check(client,base)
    with Session(engine()) as db:
        user_id=db.scalar(select(Membership.user_id).where(Membership.workspace_id==account[2]));db.get(User,user_id).active=False;db.commit()
    result=tick(account[2],release['instance_id'],task['id']);assert result['processed']==0 and result['blocked']>=1
    with Session(engine()) as db:
        job=db.get(Job,identity);assert job.status=='queued' and job.attempts==0
        job.status='running';job.attempts=1;job.lease_token='expired-revoked';job.lease_until=datetime.now(timezone.utc)-timedelta(seconds=5);db.commit()
    result=tick(account[2],release['instance_id'],task['id']);assert result['processed']==1,result
    with Session(engine()) as db:
        job=db.get(Job,identity);assert job.status=='failed' and not job.data.get('goal_check_id')
        assert not db.query(CloudCall).count() and not db.query(Acquisition).count()


def test_bounded_pages_do_not_starve_new_current_goal_behind_invalid_candidates(client,account):
    release,_,task,base,body=setup(client);define(client,base,body)
    with Session(engine()) as db:
        target=db.get(Task,task['id']);context=target.data['native_goal_context_hash'];actor=target.data['native_goal_context']['actor_id']
        for i in range(55):
            db.add(Job(workspace_id=account[2],kind='check_native_business_goal',
                idempotency_key='native-goal:'+task['id']+':'+context+':candidate-v299:'+str(uuid4()),
                available_at=datetime.now(timezone.utc)-timedelta(minutes=2),
                data={'task_id':task['id'],'actor_id':actor,'context_hash':context}))
        db.commit()
    identity=new_check(client,base)
    first=tick(account[2],release['instance_id'],task['id']);assert first['blocked']==50 and first['processed']==0
    second=tick(account[2],release['instance_id'],task['id'],after=first['after'])
    assert second['blocked']==5 and second['processed']==1,second
    with Session(engine()) as db:
        assert db.get(Job,identity).status=='succeeded'
        assert db.query(Job).filter(Job.kind=='check_native_business_goal',Job.idempotency_key.like('%candidate-v299:%'),Job.attempts!=0).count()==0


def test_forged_candidate_and_promoter_hints_never_authorize_claim(client,account):
    from generic_agents.models import Audit
    from generic_agents import goal_evaluation_maturity_v299 as maturity
    release,_,task,base,body=setup(client);define(client,base,body);tick(account[2],release['instance_id'],task['id'])
    with Session(engine()) as db:
        current=db.get(Task,task['id']);parent=db.get(Job,current.data['native_goal_check_job_pin']['job_id']);assert parent.status=='succeeded'
        actor=current.data['native_goal_context']['actor_id'];context=current.data['native_goal_context_hash']
        candidate=Job(workspace_id=account[2],kind='check_native_business_goal',idempotency_key='native-goal:'+task['id']+':'+context+':candidate-v299:'+str(uuid4()),data={'task_id':task['id'],'actor_id':actor,'context_hash':context})
        promoter=Job(workspace_id=account[2],kind=maturity.KIND,idempotency_key='forged-promoter-'+uuid4().hex,data={'task_id':task['id'],'actor_id':actor,'goal_check_job_id':parent.id})
        db.add_all([candidate,promoter]);db.flush()
        db.add(Audit(workspace_id=account[2],actor_id=actor,action=maturity.QUEUED,object_id=candidate.id,data={'task_id':task['id']}))
        db.add(Audit(workspace_id=account[2],actor_id=actor,action=maturity.OUTBOX,object_id=parent.id,data={'task_id':task['id'],'promoter_job_id':promoter.id}))
        ids=[candidate.id,promoter.id];db.commit()
    result=tick(account[2],release['instance_id'],task['id']);assert result['blocked']>=2 and result['processed']==0,result
    with Session(engine()) as db:
        assert all(db.get(Job,x).status=='queued' and db.get(Job,x).attempts==0 for x in ids)


def test_foreign_workspace_and_wrong_instance_cannot_claim_original_job(client,account):
    from fastapi import HTTPException
    from generic_agents.models import Workspace
    release,_,task,base,body=setup(client);define(client,base,body)
    with Session(engine()) as db:
        identity=db.get(Task,task['id']).data['native_goal_check_job_pin']['job_id']
        other=Workspace(name='Separate scope');db.add(other);db.flush();foreign=other.id;db.commit()
    with pytest.raises(HTTPException):tick(foreign,release['instance_id'],task['id'])
    with pytest.raises(HTTPException):tick(account[2],str(uuid4()),task['id'])
    with Session(engine()) as db:assert db.get(Job,identity).status=='queued' and db.get(Job,identity).attempts==0


def test_real_scoped_background_process_completes_original_job_and_stops(client,account,tmp_path):
    import os,signal,subprocess,sys,time
    from generic_agents.config import settings
    release,_,task,base,body=setup(client);define(client,base,body)
    with Session(engine()) as db:old_id=db.get(Task,task['id']).data['native_goal_check_job_pin']['job_id']
    identity=new_check(client,base)
    env=os.environ.copy();env['GA_STORAGE_DIR']=str(settings().storage_dir)
    cmd=[sys.executable,'-m','generic_agents.native_goal_scoped_worker_v355','--workspace-id',account[2],
        '--instance-id',release['instance_id'],'--task-id',task['id']]
    log=tmp_path/'worker.log'
    with log.open('w+') as output:
        child=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=output,stderr=output,env=env,start_new_session=True)
        try:
            deadline=time.monotonic()+30;status=None
            while time.monotonic()<deadline:
                with Session(engine()) as db:status=db.get(Job,identity).status
                if status in {'succeeded','failed','cancelled'}:break
                assert child.poll() is None,'scoped worker exited before settling its original job'
                time.sleep(.1)
            assert status=='succeeded',status
        finally:
            if child.poll() is None:child.send_signal(signal.SIGTERM)
            child.wait(timeout=15)
        assert child.returncode==0
    with Session(engine()) as db:
        assert db.get(Job,identity).data.get('goal_check_id')
        assert db.get(Job,old_id).status=='queued' and db.get(Job,old_id).attempts==0
        assert not db.query(CloudCall).count() and not db.query(Acquisition).count()
