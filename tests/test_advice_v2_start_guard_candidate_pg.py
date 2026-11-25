"""Current v2 preview recheck with disposable PG and synthetic evidence.

This tests the local prerequisite, not Start, transport, real approvals or
any model call. Goal frame/evaluation/source-selection remain test substitutes.
"""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Evidence, Plan, Task
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as subject
from generic_agents import same_goal_advice_unlimited_store_v2 as custody
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def saved(db,s,created):
    p=s['principal'];task=db.get(Task,s['task_id']);plan=db.get(Plan,created['plan_id'])
    prep,_=custody.read_prepared(db,p,task,plan)
    event=db.scalar(select(Audit).where(Audit.action==subject.ACTION))
    return task,plan,prep,event


def test_current_recheck_keeps_preview_unsent(prepared_preview):
    s,created,request,frame=prepared_preview;p=s['principal']
    with Session(engine()) as db:subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    with Session(engine()) as db:
        task,plan,prep,event=saved(db,s,created)
        assert subject.verify_current_for_start(db,p,task,plan,prep,event)==frame
        assert not list(db.scalars(select(CloudCall)))


def test_revoked_current_grant_blocks_start_guard(prepared_preview):
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    with Session(engine()) as db:
        _,_,_,event=saved(db,s,created);identity=event.data['preview_record']['evidence_pins'][0]['evidence_id']
        item=db.get(Evidence,identity);item.data={**item.data,'cloud_model_ids':[]};item.version+=1;db.commit()
    with Session(engine()) as db:
        task,plan,prep,event=saved(db,s,created)
        with pytest.raises(HTTPException):subject.verify_current_for_start(db,p,task,plan,prep,event)
        assert not list(db.scalars(select(CloudCall)))


def test_old_preview_not_valid_for_current_start(prepared_preview):
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:first=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    with Session(engine()) as db:
        subject.prepare(db,p,s['task_id'],created['plan_id'],{**request,'request_id':str(uuid4()),'expected_plan_version':3});db.commit()
    with Session(engine()) as db:
        task=db.get(Task,s['task_id']);plan=db.get(Plan,created['plan_id']);prep,_=custody.read_prepared(db,p,task,plan)
        old=next(e for e in db.scalars(select(Audit).where(Audit.action==subject.ACTION)) if e.data['request']['request_id']==request['request_id'])
        with pytest.raises(HTTPException):subject.verify_current_for_start(db,p,task,plan,prep,old)


def test_start_persists_one_parked_job_and_exact_replay(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents import same_goal_advice_common_inventory_v2 as common
    from generic_agents.models import Job
    from generic_agents.worker import claim
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成开始测试'}
    with Session(engine()) as db:first=start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    assert claim(first['job_id'],kinds=[start.KIND],workspace_id=p.workspace_id) is None
    engine().dispose()
    with Session(engine()) as db:
        assert start.begin(db,p,s['task_id'],created['plan_id'],body)==first
        job=db.get(Job,first['job_id']);assert job.kind==start.KIND and job.attempts==0
        assert job.available_at==custody.UNAVAILABLE_TO_GENERAL_WORKERS
        assert db.get(Plan,created['plan_id']).status=='awaiting_dispatch'
        assert common.read(db,p,db.get(Task,s['task_id']))['queued_v2']==1
        assert not list(db.scalars(select(CloudCall)))
        with pytest.raises(HTTPException):start.begin(db,p,s['task_id'],created['plan_id'],{**body,'reason':'不同申请'})


def test_start_with_revoked_grant_creates_no_job(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.models import Job
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成开始测试'}
    with Session(engine()) as db:
        _,_,_,event=saved(db,s,created);identity=event.data['preview_record']['evidence_pins'][0]['evidence_id']
        item=db.get(Evidence,identity);item.data={**item.data,'cloud_model_ids':[]};item.version+=1;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):start.begin(db,p,s['task_id'],created['plan_id'],body)
        assert not list(db.scalars(select(Job).where(Job.kind==start.KIND)))
        assert not list(db.scalars(select(CloudCall)))


def test_tampered_start_job_cannot_replay(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.models import Job
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成开始测试'}
    with Session(engine()) as db:first=start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    with Session(engine()) as db:
        job=db.get(Job,first['job_id']);job.attempts=1;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):start.begin(db,p,s['task_id'],created['plan_id'],body)
        assert not list(db.scalars(select(CloudCall)))


def test_concurrent_same_start_nonce_creates_one_job(prepared_preview):
    from queue import Queue
    from threading import Thread
    from time import monotonic, sleep
    from sqlalchemy import func, text
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.models import Job
    s,created,request,_=prepared_preview;p=s['principal'];second_pid=Queue();results=[];errors=[]
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成并发测试'}
    def contender():
        try:
            with Session(engine()) as db:
                second_pid.put(db.scalar(select(func.pg_backend_pid())))
                results.append(start.begin(db,p,s['task_id'],created['plan_id'],body));db.commit()
        except Exception as e:errors.append(e)
    with Session(engine()) as first:
        first_pid=first.scalar(select(func.pg_backend_pid()))
        original=start.begin(first,p,s['task_id'],created['plan_id'],body)
        thread=Thread(target=contender,daemon=True);thread.start();pid=second_pid.get(timeout=5)
        try:
            deadline=monotonic()+5;blocked=False
            while monotonic()<deadline:
                with engine().connect() as observer:
                    blocked=first_pid in observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid})
                if blocked:break
                sleep(.05)
            assert blocked
        finally:first.commit()
        thread.join(timeout=10);assert not thread.is_alive() and not errors
    assert results==[original]
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Job).where(Job.kind==start.KIND))))==1
        assert len(list(db.scalars(select(Audit).where(Audit.action==start.ACTION))))==1
        assert not list(db.scalars(select(CloudCall)))


def test_old_receipt_still_replays_after_v2_start(prepared_preview,client,monkeypatch):
    from copy import deepcopy
    from generic_agents.contracts import digest
    from generic_agents.models import Capability, Job, Invocation, uid
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from tests.test_same_goal_advice_v1_history_compat_pg import add_cancelled
    from tests.test_same_goal_advice_v1_replay_compat_pg import use_synthetic_task
    from tests.test_same_goal_advice_contracts_v302 import scene
    from tests import test_same_goal_advice_common_inventory_v2_pg as factory
    s,created,request,_=prepared_preview;p=s['principal'];use_synthetic_task(monkeypatch)
    sample=scene();cap_id=uid();sample.preparation['advice_capability_ref']['id']=cap_id
    with Session(engine()) as db:
        db.add(Capability(id=cap_id,workspace_id=p.workspace_id,namespace='historical_start_fixture',
            name='historical_advice',release_version='1.0.0',kind='tool',status='published',
            content_hash=digest(sample.manifest),validated_hash=digest(sample.manifest),data=sample.manifest));db.flush()
        with monkeypatch.context() as m:
            m.setattr(factory,'contract_scene',lambda:sample);old_id=add_cancelled(db,s)
        old=db.get(Plan,old_id);old_prepare=deepcopy(old.data['operations'][0]['body']);db.commit()
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成开始测试'}
    with Session(engine()) as db:start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    base='/api/workspace/tasks/'+s['task_id']+'/business-advice/preparations'
    response=client.post(base,json=old_prepare);assert response.status_code==201,response.text
    assert client.post(base,json={**old_prepare,'request_id':uid()}).status_code==409
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Job).where(Job.kind==start.KIND))))==1
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))


def test_start_serializes_with_evidence_revocation(prepared_preview):
    from queue import Queue
    from threading import Thread
    from time import monotonic, sleep
    from sqlalchemy import func, text
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    s,created,request,_=prepared_preview;p=s['principal'];pidq=Queue();errors=[]
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成并发撤权'}
    with Session(engine()) as db:
        event=db.scalar(select(Audit).where(Audit.action==subject.ACTION));identity=event.data['preview_record']['evidence_pins'][0]['evidence_id']
    def revoke():
        try:
            with Session(engine()) as db:
                pidq.put(db.scalar(select(func.pg_backend_pid())))
                item=db.get(Evidence,identity);item.data={**item.data,'cloud_model_ids':[]};item.version+=1;db.commit()
        except Exception as e:errors.append(e)
    with Session(engine()) as first:
        first_pid=first.scalar(select(func.pg_backend_pid()));start.begin(first,p,s['task_id'],created['plan_id'],body)
        thread=Thread(target=revoke,daemon=True);thread.start();pid=pidq.get(timeout=5)
        try:
            deadline=monotonic()+5;blocked=False
            while monotonic()<deadline:
                with engine().connect() as observer:blocked=first_pid in observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid})
                if blocked:break
                sleep(.05)
            assert blocked
        finally:first.commit()
        thread.join(timeout=10);assert not thread.is_alive() and not errors
    with Session(engine()) as db:
        assert db.get(Evidence,identity).data['cloud_model_ids']==[]
        assert not list(db.scalars(select(CloudCall)))


def test_uncommitted_revocation_refuses_start_without_job(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.models import Job
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成并发撤权'}
    with Session(engine()) as revoke:
        event=revoke.scalar(select(Audit).where(Audit.action==subject.ACTION));identity=event.data['preview_record']['evidence_pins'][0]['evidence_id']
        item=revoke.get(Evidence,identity);item.data={**item.data,'cloud_model_ids':[]};item.version+=1;revoke.flush()
        with Session(engine()) as first:
            with pytest.raises(HTTPException):start.begin(first,p,s['task_id'],created['plan_id'],body)
            first.rollback()
        revoke.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):start.begin(db,p,s['task_id'],created['plan_id'],body)
        db.rollback()
        assert not list(db.scalars(select(Audit).where(Audit.action==start.ACTION)))
        assert not list(db.scalars(select(Job).where(Job.kind==start.KIND))) and not list(db.scalars(select(CloudCall)))


@pytest.mark.parametrize('prepared_preview',[8],indirect=True)
def test_repro_goal_deadline_expires_before_start_commit(prepared_preview):
    import time
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.temporal import instant
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成目标到期重现'}
    with Session(engine()) as db:
        start.begin(db,p,s['task_id'],created['plan_id'],body)
        until=instant(db.get(Task,s['task_id']).data['goal_spec']['deadline'])
        delay=(until-db.scalar(select(__import__('sqlalchemy').func.clock_timestamp()))).total_seconds()
        time.sleep(max(0,delay)+.2)
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    from generic_agents.models import Job
    with Session(engine()) as db:
        assert not list(db.scalars(select(Audit).where(Audit.action==start.ACTION)))
        assert not list(db.scalars(select(Job).where(Job.kind==start.KIND))) and not list(db.scalars(select(CloudCall)))


@pytest.mark.parametrize('which',['model','source'])
def test_uncommitted_capability_change_refuses_start(prepared_preview,which):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.models import Capability, Job
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成能力并发变更'}
    cap_id=s['prep']['model_pin' if which=='model' else 'source_business_capability_ref']['id']
    with Session(engine()) as editor:
        cap=editor.get(Capability,cap_id);cap.status='retired';editor.flush()
        with Session(engine()) as first:
            with pytest.raises(HTTPException):start.begin(first,p,s['task_id'],created['plan_id'],body)
            first.rollback()
        editor.commit()
    with Session(engine()) as db:
        assert not list(db.scalars(select(Audit).where(Audit.action==start.ACTION)))
        assert not list(db.scalars(select(Job).where(Job.kind==start.KIND))) and not list(db.scalars(select(CloudCall)))


def test_altered_evidence_bytes_before_commit_roll_back_start(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.config import settings
    from generic_agents.models import Job
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成文件并发变更'}
    with Session(engine()) as db:
        start.begin(db,p,s['task_id'],created['plan_id'],body)
        event=db.scalar(select(Audit).where(Audit.action==subject.ACTION))
        identity=event.data['preview_record']['evidence_pins'][0]['evidence_id']
        path=settings().storage_dir / db.get(Evidence,identity).storage_key
        path.write_bytes(b'changed synthetic bytes')
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert not list(db.scalars(select(Audit).where(Audit.action==start.ACTION)))
        assert not list(db.scalars(select(Job).where(Job.kind==start.KIND))) and not list(db.scalars(select(CloudCall)))


@pytest.mark.parametrize('field',['status','active_release_id'])
def test_uncommitted_instance_pause_or_release_change_refuses_start(prepared_preview,field):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.models import DomainInstance, Job
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成实例并发变更'}
    with Session(engine()) as editor:
        row=editor.get(DomainInstance,s['prep']['scope']['instance_id'])
        setattr(row,field,'paused' if field=='status' else None);editor.flush()
        with Session(engine()) as first:
            with pytest.raises(HTTPException):start.begin(first,p,s['task_id'],created['plan_id'],body)
            first.rollback()
        editor.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):start.begin(db,p,s['task_id'],created['plan_id'],body)
        db.rollback()
        assert not list(db.scalars(select(Audit).where(Audit.action==start.ACTION)))
        assert not list(db.scalars(select(Job).where(Job.kind==start.KIND))) and not list(db.scalars(select(CloudCall)))


def test_instance_pause_waits_for_started_transaction(prepared_preview):
    from queue import Queue
    from threading import Thread
    from time import monotonic, sleep
    from sqlalchemy import func,text
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.models import DomainInstance
    s,created,request,_=prepared_preview;p=s['principal'];pidq=Queue();errors=[]
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成实例暂停'}
    def pause():
        try:
            with Session(engine()) as db:
                pidq.put(db.scalar(select(func.pg_backend_pid())))
                db.get(DomainInstance,s['prep']['scope']['instance_id']).status='paused';db.commit()
        except Exception as e:errors.append(e)
    with Session(engine()) as first:
        owner=first.scalar(select(func.pg_backend_pid()));start.begin(first,p,s['task_id'],created['plan_id'],body)
        thread=Thread(target=pause,daemon=True);thread.start();pid=pidq.get(timeout=5)
        try:
            until=monotonic()+5;blocked=False
            while monotonic()<until:
                with engine().connect() as obs:blocked=owner in obs.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid})
                if blocked:break
                sleep(.05)
            assert blocked
        finally:first.commit()
        thread.join(timeout=10);assert not thread.is_alive() and not errors


@pytest.mark.parametrize('finish',['commit','rollback','savepoint_failure'])
def test_commit_guards_do_not_leak_into_unrelated_session_work(prepared_preview,monkeypatch,finish):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.config import settings
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成事务复用'}
    with Session(engine()) as db:
        if finish=='savepoint_failure':
            with monkeypatch.context() as m:
                m.setattr(custody,'read_started',lambda *_: (_ for _ in ()).throw(HTTPException(409,'synthetic')))
                with pytest.raises(HTTPException):start.begin(db,p,s['task_id'],created['plan_id'],body)
        else:
            start.begin(db,p,s['task_id'],created['plan_id'],body)
            db.commit() if finish=='commit' else db.rollback()
        event=db.scalar(select(Audit).where(Audit.action==subject.ACTION))
        identity=event.data['preview_record']['evidence_pins'][0]['evidence_id']
        (settings().storage_dir/db.get(Evidence,identity).storage_key).write_bytes(b'changed after original transaction')
        other=Task(id=str(uuid4()),workspace_id=p.workspace_id,owner_id=p.user_id,
            package_id=s['prep']['scope']['package_id'],status='workflow_active',title='unrelated synthetic task',data={})
        db.add(other);db.flush()
        db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action='synthetic.unrelated',object_id=other.id,data={}))
        db.commit()
        assert db.info.get('v2_start_goal_commit_guard') is None
        assert db.info.get('v2_file_intent_commit_guard') is None


@pytest.mark.parametrize('finish',['rollback_savepoint','release_savepoint'])
def test_outer_savepoint_guard_lifetime(prepared_preview,finish):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents.config import settings
    from generic_agents.models import Job
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成外层保存点'}
    with Session(engine()) as db:
        outer=db.begin_nested();start.begin(db,p,s['task_id'],created['plan_id'],body)
        if finish=='rollback_savepoint':outer.rollback()
        else:outer.commit()
        event=db.scalar(select(Audit).where(Audit.action==subject.ACTION))
        identity=event.data['preview_record']['evidence_pins'][0]['evidence_id']
        (settings().storage_dir/db.get(Evidence,identity).storage_key).write_bytes(b'changed after savepoint')
        db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action='synthetic.unrelated',object_id=s['task_id'],data={}))
        if finish=='rollback_savepoint':
            db.commit()
        else:
            with pytest.raises(HTTPException):db.commit()
            db.rollback()
    with Session(engine()) as db:
        assert not list(db.scalars(select(Audit).where(Audit.action==start.ACTION)))
        assert not list(db.scalars(select(Job).where(Job.kind==start.KIND))) and not list(db.scalars(select(CloudCall)))


def test_start_restores_caller_lock_timeout(prepared_preview):
    from sqlalchemy import text
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成锁超时设置'}
    with Session(engine()) as db:
        db.execute(text("SET LOCAL lock_timeout = '7s'"))
        start.begin(db,p,s['task_id'],created['plan_id'],body)
        assert db.scalar(text("SELECT current_setting('lock_timeout')"))=='7s'
        db.commit()


def test_started_document_cold_read_requires_explicit_versioned_state(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成开始测试'}
    with Session(engine()) as db:start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    with Session(engine()) as db:
        task=db.get(Task,s['task_id']);plan=db.get(Plan,created['plan_id'])
        with pytest.raises(HTTPException):document.cold_read(db,p,task,plan)
        assert document.cold_read(db,p,task,plan,started=True)['document']['evidence_ref']['evidence_id']
        assert not list(db.scalars(select(CloudCall)))


def test_lease_original_is_unique_and_old_worker_never_claims(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as lease
    from generic_agents.models import Job
    from generic_agents.worker import claim
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成开始测试'}
    with Session(engine()) as db:first=start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    with Session(engine()) as db:original=lease.acquire(db,p,s['task_id'],created['plan_id']);db.commit()
    engine().dispose()
    with Session(engine()) as db:
        assert lease.acquire(db,p,s['task_id'],created['plan_id'])==original
        job=db.get(Job,first['job_id']);assert job.status=='running' and job.attempts==1
        assert not list(db.scalars(select(CloudCall)))
    assert claim(first['job_id'],kinds=[start.KIND],workspace_id=p.workspace_id) is None


def test_expired_lease_is_preserved_never_replaced(prepared_preview,monkeypatch):
    import time
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as lease
    from generic_agents.models import Job
    from generic_agents.worker import claim
    monkeypatch.setattr(lease,'SECONDS',2)
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成租约到期'}
    with Session(engine()) as db:first=start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    with Session(engine()) as db:original=lease.acquire(db,p,s['task_id'],created['plan_id']);db.commit()
    time.sleep(2.2)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):lease.acquire(db,p,s['task_id'],created['plan_id'])
        assert len(list(db.scalars(select(Audit).where(Audit.action==lease.ACTION))))==1
        job=db.get(Job,first['job_id']);assert job.lease_token==original['lease_token'] and job.attempts==1
        assert not list(db.scalars(select(CloudCall)))
    assert claim(first['job_id'],kinds=[start.KIND],workspace_id=p.workspace_id) is None


def test_concurrent_lease_is_the_same_original(prepared_preview):
    from queue import Queue
    from threading import Thread
    from time import monotonic,sleep
    from sqlalchemy import func,text
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as lease
    s,created,request,_=prepared_preview;p=s['principal'];pidq=Queue();results=[];errors=[]
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成并发租约'}
    with Session(engine()) as db:start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    def contender():
        try:
            with Session(engine()) as db:
                pidq.put(db.scalar(select(func.pg_backend_pid())))
                results.append(lease.acquire(db,p,s['task_id'],created['plan_id']));db.commit()
        except Exception as e:errors.append(e)
    with Session(engine()) as first:
        owner=first.scalar(select(func.pg_backend_pid()));original=lease.acquire(first,p,s['task_id'],created['plan_id'])
        thread=Thread(target=contender,daemon=True);thread.start();pid=pidq.get(timeout=5)
        try:
            until=monotonic()+5;blocked=False
            while monotonic()<until:
                with engine().connect() as obs:blocked=owner in obs.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid})
                if blocked:break
                sleep(.05)
            assert blocked
        finally:first.commit()
        thread.join(timeout=10);assert not thread.is_alive() and not errors
    assert results==[original]
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Audit).where(Audit.action==lease.ACTION))))==1
        assert not list(db.scalars(select(CloudCall)))


def test_started_original_must_still_pass_current_preview_check(prepared_preview):
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as lease
    s,created,request,frame=prepared_preview;p=s['principal']
    with Session(engine()) as db:shown=subject.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成发送前重验'}
    with Session(engine()) as db:start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    with Session(engine()) as db:lease.acquire(db,p,s['task_id'],created['plan_id']);db.commit()
    with Session(engine()) as db:
        task=db.get(Task,s['task_id']);plan=db.get(Plan,created['plan_id'])
        prep,_=custody.read_started(db,p,task,plan)
        event=db.scalar(select(Audit).where(Audit.action==subject.ACTION,Audit.object_id==plan.id))
        assert subject.verify_current_for_start(db,p,task,plan,prep,event,started=True)==frame
        assert not list(db.scalars(select(CloudCall)))
