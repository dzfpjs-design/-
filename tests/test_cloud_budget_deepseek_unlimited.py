"""Real PostgreSQL accounting with synthetic model capabilities and no network."""
from concurrent.futures import ThreadPoolExecutor
import copy
import time
from threading import Event
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from generic_agents.cloud_budget import reserve, finish, BudgetDenied, settle_paid_response
from generic_agents.cloud_budget_policy import budget_status, MARKER_KEY, DEEPSEEK_UNLIMITED
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.gateway import ModelBlocked
from generic_agents.models import Capability, CloudCall, Job, User, now
from generic_agents.security import Principal


@pytest.fixture
def scene(account, monkeypatch):
    username, _, workspace = account
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 100)
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 2)
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == username))
        principal = Principal(user.id, workspace, 'admin', 'test-session')
        caps = []
        for provider in ('deepseek', 'qwen'):
            data={'execution': {'provider': provider, 'model': provider+'-test'}}
            cap = Capability(workspace_id=workspace, namespace='test', name=provider, release_version='1',
                kind='foundation_model', status='published', content_hash=digest(data), validated_hash=digest(data),
                data=data)
            db.add(cap); caps.append(cap)
        db.commit()
        for cap in caps: db.refresh(cap); db.expunge(cap)
    return principal, *caps


def test_new_deepseek_unlimited_but_qwen_uses_its_own_allowance_and_total_is_preserved(scene):
    p, ds, qwen = scene
    one=reserve(p,ds,900,'a'*64); two=reserve(p,ds,800,'b'*64)
    finish(one, {'usage':{'total_tokens':150}}, 'succeeded')
    with Session(engine()) as db:
        s=budget_status(db,p,ds,10000)
        assert s['available'] and s['unlimited'] and s['token_limit'] is None
        q=budget_status(db,p,qwen,60)
        assert q['available'] and (q['used_tokens'],q['used_calls']) == (0,0)
        ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
        assert (ledger.data['tokens'],ledger.data['calls'])==(950,2)
        assert db.get(CloudCall,one).data[MARKER_KEY] == DEEPSEEK_UNLIMITED
        assert db.get(CloudCall,two).status=='reserved'
    call=reserve(p,qwen,60,'c'*64)
    with pytest.raises(BudgetDenied): reserve(p,qwen,41,'d'*64)
    finish(call, {'usage':{'total_tokens':30}}, 'succeeded')
    assert reserve(p,qwen,70,'e'*64)
    with pytest.raises(BudgetDenied): reserve(p,qwen,1,'f'*64)


def test_deepseek_uses_ledger_when_other_provider_limits_are_invalid(scene, monkeypatch):
    p, ds, qwen = scene
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', -1)
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', -1)
    call = reserve(p, ds, 900, 'd'*64)
    finish(call, {'usage': {'total_tokens': 42}}, 'succeeded')
    with Session(engine()) as db:
        status = budget_status(db, p, ds, 10000)
        ledger = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == 'cloud_budget'))
        assert status['unlimited'] and status['available']
        assert (ledger.data['calls'], ledger.data['tokens']) == (1, 42)
        with pytest.raises(ModelBlocked): budget_status(db, p, qwen, 1)


def test_empty_ledger_preview_preserves_typed_payload_rules(scene):
    p,ds,qwen=scene
    with Session(engine()) as db:
        q=budget_status(db,p,qwen,100)
        d=budget_status(db,p,ds,10000)
        assert (q['used_tokens'],q['used_calls'],q['available'])==(0,0,True)
        assert d['unlimited'] and d['available']


def test_empty_preview_can_become_stale_but_cannot_authorize_over_limit(scene):
    p,_,qwen=scene
    observed, resume=Event(),Event()
    def preview():
        with Session(engine()) as db:
            execute=db.execute
            def intercepted(statement,*args,**kwargs):
                result=execute(statement,*args,**kwargs)
                sql=str(statement)
                if 'cloud_model_calls' in sql and 'OVER' in sql:
                    observed.set()
                    assert resume.wait(8)
                return result
            db.execute=intercepted
            return budget_status(db,p,qwen,1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(preview)
        try:
            assert observed.wait(8)
            reserve(p,qwen,100,'a'*64)
        finally:
            resume.set()
        snapshot=future.result(timeout=8)
    assert snapshot['available'] and snapshot['used_tokens']==0
    with pytest.raises(BudgetDenied): reserve(p,qwen,1,'b'*64)


def test_unattributed_old_calls_remain_in_shared_allowance_and_old_settlement_survives(scene):
    p, ds, qwen=scene
    day=str(now().date())
    with Session(engine()) as db:
        db.add(Job(workspace_id=p.workspace_id,kind='cloud_budget',status='internal',
            idempotency_key='cloud-budget:'+day,data={'day':day,'tokens':40,'calls':1}))
        old=CloudCall(workspace_id=p.workspace_id,capability_id=ds.id,reserved_tokens=40,
            charged_tokens=40,data={'day':day,'request_hash':'a'*64,'actor_id':p.user_id})
        db.add(old); db.commit(); oldid=old.id
    with Session(engine()) as db:
        assert budget_status(db,p,qwen,61)['available'] is False
    assert reserve(p,ds,200,'b'*64)
    finish(oldid,{'usage':{'total_tokens':10}},'succeeded')
    with Session(engine()) as db:
        q=budget_status(db,p,qwen,90)
        assert (q['used_tokens'],q['used_calls'],q['available'])==(10,1,True)


def test_provider_exemption_requires_current_published_database_capability(scene):
    p, ds, qwen=scene
    forged=copy.deepcopy(qwen); forged.data={'execution':{'provider':'deepseek','model':'qwen-test'}}
    with pytest.raises(ModelBlocked): reserve(p,forged,101,'a'*64)
    with Session(engine()) as db:
        current=db.get(Capability,ds.id); current.status='draft'; db.commit()
    with pytest.raises(ModelBlocked): reserve(p,ds,101,'b'*64)
    with Session(engine()) as db:
        assert db.scalar(select(CloudCall).where(CloudCall.workspace_id==p.workspace_id)) is None


def test_altered_persisted_data_or_unflushed_session_changes_cannot_mint_exemption(scene):
    p, ds, _ = scene
    with Session(engine()) as db:
        row=db.get(Capability,ds.id)
        row.data={'execution': {'provider':'deepseek','model':'changed-without-publishing'}}
        with pytest.raises(ModelBlocked): budget_status(db,p,row,1000)
        db.rollback()
        row=db.get(Capability,ds.id)
        row.data={'execution': {'provider':'deepseek','model':'tampered'}}
        db.commit()  # published hash deliberately remains unchanged
    with pytest.raises(ModelBlocked): reserve(p,ds,1000,'a'*64)


def test_concurrent_calls_use_one_locked_ledger_without_losing_usage(scene):
    p,ds,qwen=scene
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(lambda n:reserve(p,ds,1000,str(n)*64), range(4)))
    assert len(set(ids))==4
    with Session(engine()) as db:
        assert budget_status(db,p,qwen,100)['available']
        ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
        assert (ledger.data['tokens'],ledger.data['calls'])==(4000,4)
    assert reserve(p,qwen,100,'a'*64)


def test_capability_change_waits_then_is_rechecked_before_any_reservation(scene):
    p,ds,_=scene
    with Session(engine()) as editor, ThreadPoolExecutor(max_workers=1) as pool:
        cap=editor.scalar(select(Capability).where(Capability.id==ds.id).with_for_update())
        editor_pid=editor.scalar(text('select pg_backend_pid()'))
        pending=pool.submit(reserve,p,ds,1000,'a'*64)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            blocked=editor.scalar(text('select count(*) from pg_stat_activity '
                'where :pid = any(pg_blocking_pids(pid))'), {'pid':editor_pid})
            if blocked: break
            time.sleep(.02)
        assert blocked and not pending.done()
        cap.status='draft'
        editor.commit()
        with pytest.raises(ModelBlocked): pending.result(timeout=8)
    with Session(engine()) as db:
        assert db.scalar(select(CloudCall).where(CloudCall.workspace_id==p.workspace_id)) is None


def test_concurrent_finish_and_new_reservations_keep_total_and_provider_projection(scene):
    p,ds,qwen=scene
    calls=[reserve(p,ds,1000,str(i)*64) for i in range(3)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(finish,c,{'usage':{'total_tokens':10}},'succeeded') for c in calls]
        futures.append(pool.submit(reserve,p,qwen,50,'e'*64))
        for future in futures: future.result(timeout=8)
    with Session(engine()) as db:
        ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
        q=budget_status(db,p,qwen,50)
        assert (ledger.data['tokens'],ledger.data['calls'])==(80,4)
        assert (q['used_tokens'],q['used_calls'],q['available'])==(50,1,True)
