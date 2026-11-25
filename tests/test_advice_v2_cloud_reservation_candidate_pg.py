"""Isolated PG only. Source/Goal/frame are substitutes; no transport."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import func,select,text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.models import Audit,CloudCall,Job,Membership
from generic_agents import same_goal_advice_cloud_claim_test_v2 as claim
from generic_agents import same_goal_advice_cloud_reservation_candidate_v2 as subject
from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as lease
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def started(prepared_preview):
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:claim.install(db);db.commit()
    with Session(engine()) as db:shown=preview.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
        'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
        'cloud_authorized':True,'reason':'合成预留测试'}
    with Session(engine()) as db:started=start.begin(db,p,s['task_id'],created['plan_id'],body);db.commit()
    with Session(engine()) as db:owned=lease.acquire(db,p,s['task_id'],created['plan_id']);db.commit()
    assert owned['job_id']==started['job_id']
    return s,created,owned


def _reserve(db,case):
    s,created,owned=case
    return subject.reserve(db,s['principal'],s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'])


def test_atomic_reservation_keeps_usage_unlimited_and_no_send(started,monkeypatch):
    from datetime import datetime
    from generic_agents import business_reasoning_protocols_v276 as protocols
    from generic_agents.worker import claim as ordinary_claim
    monkeypatch.setattr(settings(),'cloud_daily_token_limit',0);monkeypatch.setattr(settings(),'cloud_daily_call_limit',0)
    with Session(engine()) as db:result=_reserve(db,started);db.commit()
    assert result['transport_authorized'] is False and result['send_outcome']=='unknown'
    with Session(engine()) as db:
        row=db.get(CloudCall,result['cloud_call_id']);audit=db.get(Audit,result['reservation_audit_id'])
        daily=db.scalar(select(Job).where(Job.workspace_id==row.workspace_id,Job.kind=='cloud_budget'))
        assert row.status=='reserved' and row.reserved_tokens==row.charged_tokens>0
        assert row.data['budget_policy_v1']=='deepseek_unlimited' and daily.data['calls']==1
        assert daily.data['tokens']==row.reserved_tokens and row.data[subject.KEY]==audit.data
        record=audit.data
        assert record['transport_authorized'] is False and record['send_outcome']=='unknown'
        assert (datetime.fromisoformat(record['source_recheck_started_at']) <=
            datetime.fromisoformat(record['source_recheck_completed_at']) <= datetime.fromisoformat(record['reserved_at']))
        assert db.scalar(text('select count(*) from public.ga_test_advice_v2_dispatch_claims'))==1
        with pytest.raises(HTTPException):_reserve(db,started)
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==1
    assert protocols.applies('same_goal_advice_unlimited_v2') is False
    assert ordinary_claim(started[2]['job_id'],kinds=[start.KIND],workspace_id=started[0]['principal'].workspace_id) is None


def test_reservation_rollback_removes_only_new_candidate(started):
    with Session(engine()) as db:_reserve(db,started);db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==claim.ACTION))==0
        assert db.scalar(text('select count(*) from public.ga_test_advice_v2_dispatch_claims'))==0


def test_concurrent_reservation_cannot_create_second_claim(started):
    barrier=Barrier(2)
    def work(_):
        with Session(engine()) as db:
            barrier.wait(timeout=5)
            try:_reserve(db,started);db.commit();return 'reserved'
            except HTTPException:db.rollback();return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,[1,2]))
    assert sorted(results)==['refused','reserved']
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==1
        assert db.scalar(text('select count(*) from public.ga_test_advice_v2_dispatch_claims'))==1


def test_revocation_before_reserve_is_rejected(started):
    s,_,_=started;p=s['principal']
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id))
        member.role='viewer';db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):_reserve(db,started)
        db.rollback()
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_changed_lease_before_commit_rolls_back_reservation(started):
    with Session(engine()) as db:
        _reserve(db,started);db.get(Job,started[2]['job_id']).lease_token=str(uuid4())
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(CloudCall))==0


@pytest.mark.parametrize('change',['reserved_tokens','charged_tokens','capability_id','cloud_created',
    'audit_action','audit_object','audit_actor','audit_created','daily_key','job_available','job_kind','job_key',
    'job_version','plan_version','preview_action','task_title','task_payload','claim_job','orphan_audit'])
def test_same_transaction_raw_tampering_rolls_back(started,change):
    from generic_agents.models import Capability
    with Session(engine()) as db:
        result=_reserve(db,started);p=started[0]['principal'];plan=started[1]['plan_id'];job=started[2]['job_id']
        if change in {'reserved_tokens','charged_tokens'}:
            db.execute(text(f'UPDATE cloud_model_calls SET {change}={change}+1 WHERE id=:id'),{'id':result['cloud_call_id']})
        elif change=='capability_id':
            another=db.scalar(select(Capability.id).where(Capability.workspace_id==p.workspace_id,
                Capability.id!=db.get(CloudCall,result['cloud_call_id']).capability_id).limit(1))
            assert another is not None
            db.execute(text('UPDATE cloud_model_calls SET capability_id=:cap WHERE id=:id'),{'cap':another,'id':result['cloud_call_id']})
        elif change=='cloud_created':
            db.execute(text("UPDATE cloud_model_calls SET created_at=created_at+interval '1 second' WHERE id=:id"),
                {'id':result['cloud_call_id']})
        elif change in {'audit_action','audit_object'}:
            column='action' if change=='audit_action' else 'object_id'
            db.execute(text(f'UPDATE audit_events SET {column}=:value WHERE id=:id'),
                {'value':'synthetic-tamper','id':result['reservation_audit_id']})
        elif change=='audit_actor':
            from generic_agents.models import User
            other=User(username='synthetic_'+uuid4().hex,password_hash='synthetic');db.add(other);db.flush()
            db.execute(text('UPDATE audit_events SET actor_id=:actor WHERE id=:id'),
                {'actor':other.id,'id':result['reservation_audit_id']})
        elif change=='audit_created':
            db.execute(text("UPDATE audit_events SET created_at=created_at+interval '1 second' WHERE id=:id"),
                {'id':result['reservation_audit_id']})
        elif change=='daily_key':
            daily=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            db.execute(text('UPDATE jobs SET idempotency_key=:value WHERE id=:id'),{'value':'synthetic-tamper','id':daily.id})
        elif change=='job_available':
            db.execute(text('UPDATE jobs SET available_at=clock_timestamp() WHERE id=:id'),{'id':job})
        elif change in {'job_kind','job_key'}:
            column='kind' if change=='job_kind' else 'idempotency_key'
            db.execute(text(f'UPDATE jobs SET {column}=:value WHERE id=:id'),{'value':'synthetic-tamper','id':job})
        elif change=='job_version':
            db.execute(text('UPDATE jobs SET version=version+1 WHERE id=:id'),{'id':job})
        elif change=='plan_version':
            db.execute(text('UPDATE plan_versions SET version=version+1 WHERE id=:id'),{'id':plan})
        elif change=='preview_action':
            identity=db.scalar(select(Audit.id).where(Audit.action==preview.ACTION,Audit.object_id==plan))
            db.execute(text("UPDATE audit_events SET action='synthetic-tamper' WHERE id=:id"),{'id':identity})
        elif change=='task_payload':
            from generic_agents.models import Task
            task=db.get(Task,started[0]['task_id'])
            task.data={**task.data,'synthetic_post_reserve':True};db.flush()
        elif change=='task_title':
            db.execute(text("UPDATE tasks SET title='synthetic-tamper' WHERE id=:id"),{'id':started[0]['task_id']})
        elif change=='claim_job':
            daily=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            db.execute(text('UPDATE ga_test_advice_v2_dispatch_claims SET job_id=:new WHERE job_id=:old'),
                {'new':daily.id,'old':job})
        else:
            db.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=claim.ACTION,object_id=plan,data={}))
            db.flush()
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
        assert db.scalar(text('select count(*) from public.ga_test_advice_v2_dispatch_claims'))==0
    if change in {'job_available','job_kind','job_key','job_version'}:
        from generic_agents.worker import claim as ordinary_claim
        assert ordinary_claim(job,kinds=[start.KIND],workspace_id=p.workspace_id) is None


def test_fences_block_concurrent_audit_and_cloudcall_inserts(started,monkeypatch):
    from time import monotonic
    from sqlalchemy.exc import DBAPIError
    from generic_agents import same_goal_advice_start_safety_candidate_v2 as safety
    times={};real_lock=claim.lock_and_check;real_files=safety.check_evidence_bytes
    def watched_lock(db):
        times['start']=monotonic();return real_lock(db)
    def watched_files(*args):
        if 'start' in times:times['file_reads']=times.get('file_reads',0)+1
        return real_files(*args)
    monkeypatch.setattr(claim,'lock_and_check',watched_lock)
    monkeypatch.setattr(safety,'check_evidence_bytes',watched_files)
    with Session(engine()) as db:
        result=_reserve(db,started);p=started[0]['principal'];model=db.get(CloudCall,result['cloud_call_id']).capability_id
        for kind in ('audit','cloud'):
            with Session(engine()) as other:
                other.execute(text("SET LOCAL lock_timeout='100ms'"))
                if kind=='audit':other.add(Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=claim.ACTION,
                    object_id=started[1]['plan_id'],data={}))
                else:other.add(CloudCall(workspace_id=p.workspace_id,capability_id=model,reserved_tokens=1,charged_tokens=1,
                    data={'gateway_dispatch':{'identity':{'job_id':started[2]['job_id']}}}))
                with pytest.raises(DBAPIError) as error:other.flush()
                assert getattr(error.value.orig,'sqlstate',None)=='55P03'
                other.rollback()
        db.commit();times['end']=monotonic()
    assert times.get('file_reads',0)>0
    print('SYNTHETIC_CLAIM_FENCE_SECONDS',round(times['end']-times['start'],3),
        'FILE_CHECKS_AFTER_LOCK',times['file_reads'],flush=True)


@pytest.mark.parametrize('prepared_preview',[12],indirect=True)
def test_known_blocker_goal_can_expire_during_final_commit_guard(started,monkeypatch):
    """Reproduce a blocker; success is NOT acceptable for a real sender."""
    from time import sleep
    from generic_agents.models import Task
    from generic_agents.temporal import instant
    real=claim._no_orphans;calls=[]
    def slow_last(db):
        calls.append(1)
        if len(calls)==2:
            until=instant(db.get(Task,started[0]['task_id']).data['goal_spec']['deadline'])
            delay=(until-db.scalar(select(func.clock_timestamp()))).total_seconds()
            sleep(max(0,delay)+.2)
        return real(db)
    monkeypatch.setattr(claim,'_no_orphans',slow_last)
    with Session(engine()) as db:
        _reserve(db,started)
        # The pre-existing guards have run before this delayed final check.
        # Demonstrate the unsafe present outcome, never treat it as a pass gate.
        db.commit()
    with Session(engine()) as db:
        assert len(calls)==2
        assert db.scalar(select(func.count()).select_from(CloudCall))==1
        event=db.scalar(select(Audit).where(Audit.action==claim.ACTION))
        assert event.data['transport_authorized'] is False and event.data['send_outcome']=='unknown'
        with pytest.raises(HTTPException):_reserve(db,started)
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==1


def test_strict_reserved_custody_survives_reconnect_without_send(started):
    from generic_agents.models import Task,Plan
    from generic_agents import same_goal_advice_unlimited_store_v2 as custody
    s,created,_=started;p=s['principal']
    with Session(engine()) as db:result=_reserve(db,started);db.commit()
    engine().dispose()
    with Session(engine()) as db:
        task,plan=db.get(Task,s['task_id']),db.get(Plan,created['plan_id'])
        with pytest.raises(HTTPException):custody.read_started(db,p,task,plan)
        db.rollback()
    with Session(engine()) as db:
        task,plan=db.get(Task,s['task_id']),db.get(Plan,created['plan_id'])
        _prep,_job,record=custody.read_reserved_test_only(db,p,task,plan)
        assert record['cloud_call_id']==result['cloud_call_id']
        assert record['reservation_audit_id']==result['reservation_audit_id']
        assert record['transport_authorized'] is False and record['send_outcome']=='unknown'
        db.rollback()


@pytest.mark.parametrize('change',['charged_tokens','audit_object','claim_job'])
def test_saved_reservation_tamper_is_rejected(started,change):
    from generic_agents.models import Task,Plan
    from generic_agents import same_goal_advice_unlimited_store_v2 as custody
    s,created,_=started;p=s['principal']
    with Session(engine()) as db:result=_reserve(db,started);db.commit()
    with Session(engine()) as db:
        if change=='charged_tokens':
            db.execute(text('UPDATE cloud_model_calls SET charged_tokens=charged_tokens+1 WHERE id=:id'),{'id':result['cloud_call_id']})
        elif change=='audit_object':
            db.execute(text("UPDATE audit_events SET object_id='synthetic-tamper' WHERE id=:id"),{'id':result['reservation_audit_id']})
        else:
            daily=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            db.execute(text('UPDATE ga_test_advice_v2_dispatch_claims SET job_id=:new WHERE job_id=:old'),
                {'new':daily.id,'old':started[2]['job_id']})
        db.commit()
    with Session(engine()) as db:
        task,plan=db.get(Task,s['task_id']),db.get(Plan,created['plan_id'])
        with pytest.raises(HTTPException):custody.read_reserved_test_only(db,p,task,plan)
        db.rollback()


@pytest.mark.parametrize('boundary',['while_locking_day','before_call'])
def test_synthetic_database_clock_crossing_utc_day_rolls_back(started,monkeypatch,boundary):
    from datetime import datetime,timedelta,timezone
    base=datetime(2030,1,1,23,59,59,tzinfo=timezone.utc)
    sequence=([base,base+timedelta(seconds=2)] if boundary=='while_locking_day'
        else [base,base+timedelta(milliseconds=500),base+timedelta(seconds=2)])
    readings=iter(sequence)
    monkeypatch.setattr(subject,'_accounting_clock',lambda _db:next(readings))
    with Session(engine()) as db:
        with pytest.raises(HTTPException):_reserve(db,started)
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='cloud_budget'))==0
        assert db.scalar(text('select count(*) from public.ga_test_advice_v2_dispatch_claims'))==0


@pytest.mark.parametrize('fault',['negative_daily','orphan_unlimited'])
def test_bad_existing_day_ledger_is_not_repaired_or_ignored(started,fault):
    from generic_agents.gateway import ModelBlocked
    s,_,_=started;p=s['principal']
    with Session(engine()) as db:
        day=subject._utc_day(db.scalar(select(func.clock_timestamp())))
        if fault=='negative_daily':
            db.add(Job(workspace_id=p.workspace_id,kind='cloud_budget',status='internal',
                idempotency_key='cloud-budget:'+day,data={'day':day,'tokens':-1,'calls':0}))
        else:
            from generic_agents.models import Capability
            cap=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,
                Capability.kind=='foundation_model',Capability.status=='published'))
            db.add(CloudCall(workspace_id=p.workspace_id,capability_id=cap.id,reserved_tokens=1,charged_tokens=1,
                data={'day':day,'budget_policy_v1':'deepseek_unlimited'}))
        db.commit()
    with Session(engine()) as db:
        with pytest.raises((HTTPException,ModelBlocked)):_reserve(db,started)
        db.rollback()
    with Session(engine()) as db:
        assert db.scalar(text('select count(*) from public.ga_test_advice_v2_dispatch_claims'))==0
        if fault=='negative_daily':
            assert db.scalar(select(Job).where(Job.kind=='cloud_budget')).data['tokens']==-1
        else:assert db.scalar(select(func.count()).select_from(CloudCall))==1


def test_synthetic_next_day_settlement_uses_original_ledger_and_is_idempotent(started,monkeypatch):
    from datetime import timedelta
    from generic_agents import cloud_budget
    from generic_agents.models import Capability
    with Session(engine()) as db:result=_reserve(db,started);db.commit()
    with Session(engine()) as db:
        call=db.get(CloudCall,result['cloud_call_id']);model=db.get(Capability,call.capability_id)
        day=call.data['day'];bound=call.reserved_tokens
        from datetime import date
        next_day=(date.fromisoformat(day)+timedelta(days=1)).isoformat()
        extra=Job(workspace_id=call.workspace_id,kind='cloud_budget',status='internal',
            idempotency_key='cloud-budget:'+next_day,data={'day':next_day,'tokens':13,'calls':2})
        db.add(extra);db.commit()
        expected={'workspace_id':call.workspace_id,'capability_id':call.capability_id,
            'actor_id':call.data['actor_id'],'request_hash':call.data['request_hash'],
            'dispatch_options_hash':call.data['dispatch_options_hash'],
            'provider':'deepseek','model':model.data['execution']['model']}
        receipt={'call_id':call.id,'provider':'deepseek','model':expected['model'],
            'request_id':'synthetic','usage':{'total_tokens':7}}
        future=call.created_at+timedelta(days=1)
    monkeypatch.setattr(cloud_budget,'now',lambda:future)  # Synthetic settlement clock only; no system clock change.
    with Session(engine()) as db:
        cloud_budget.settle_paid_response(db,result['cloud_call_id'],receipt,expected,'succeeded');db.commit()
    with Session(engine()) as db:
        cloud_budget.settle_paid_response(db,result['cloud_call_id'],receipt,expected,'succeeded');db.commit()
    with Session(engine()) as db:
        original=db.scalar(select(Job).where(Job.idempotency_key=='cloud-budget:'+day))
        later=db.scalar(select(Job).where(Job.idempotency_key=='cloud-budget:'+next_day))
        call=db.get(CloudCall,result['cloud_call_id'])
        assert bound>0 and original.data['tokens']==7 and original.data['calls']==1
        assert later.data['tokens']==13 and later.data['calls']==2 and call.charged_tokens==7
