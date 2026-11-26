"""Isolated PG and one local fake HTTP request; no production credentials."""
import json
import os
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from generic_agents import same_goal_advice_loopback_dispatch_candidate_v2 as dispatch
from generic_agents import same_goal_advice_response_reconcile_candidate_v2 as old_reconcile
from generic_agents import same_goal_advice_terminal_candidate_v2 as terminal
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import setup, synthetic_source_expiry
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody

pytestmark=pytest.mark.skipif(not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'),reason='isolated fake provider only')


def _install():
    with Session(engine()) as db:
        terminal.install(db);db.commit()


def test_atomic_terminal_survives_restart_and_does_not_authorize_repeat(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            proof=terminal.settle_and_record(db,p.workspace_id,call_id,intent);db.commit()
            assert proof['charged_tokens']==5 and proof['status']=='succeeded'
            assert not proof['retry_authorized'] and not proof['new_request_authorized']
        engine().dispose()
        with Session(engine()) as db:
            assert terminal.verify(db,p.workspace_id,call_id,intent)==proof
            assert terminal.settle_and_record(db,p.workspace_id,call_id,intent)==proof
            ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            assert (ledger.data['calls'],ledger.data['tokens'])==(1,5)
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))==1
            db.rollback()
        with pytest.raises(HTTPException):dispatch.execute_once(*args)
        assert len(server.requests)==1


def test_unknown_usage_keeps_reservation_and_never_mints_terminal(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion)) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1]
        with Session(engine()) as db:
            with pytest.raises(HTTPException):terminal.settle_and_record(db,p.workspace_id,call_id,first['intent_audit_id'])
            db.rollback(); call=db.get(CloudCall,call_id)
            assert call.status=='reserved' and call.charged_tokens==call.reserved_tokens
            assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))
        assert len(server.requests)==1


def test_existing_settled_record_cannot_be_retroactively_certified(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}):
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        old_reconcile.reconcile(p.workspace_id,call_id,intent)
        with Session(engine()) as db:
            with pytest.raises(HTTPException):terminal.settle_and_record(db,p.workspace_id,call_id,intent)
            db.rollback()
            assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))


def test_tampered_terminal_is_rejected_and_original_is_not_resent(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            proof=terminal.settle_and_record(db,p.workspace_id,call_id,intent);db.commit()
            record=db.get(Audit,proof['terminal_audit_id']);record.data={**record.data,'charged_tokens':4};db.commit()
        with Session(engine()) as db:
            with pytest.raises(HTTPException):terminal.verify(db,p.workspace_id,call_id,intent)
        assert len(server.requests)==1


def test_rollback_and_commit_time_ledger_tamper_do_not_make_a_terminal(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            terminal.settle_and_record(db,p.workspace_id,call_id,intent)
            db.rollback()
        with Session(engine()) as db:
            call=db.get(CloudCall,call_id)
            assert call.status=='reserved' and call.charged_tokens==call.reserved_tokens
            assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))
            terminal.settle_and_record(db,p.workspace_id,call_id,intent)
            ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            ledger.data={**ledger.data,'tokens':999999};db.flush()
            with pytest.raises(HTTPException):db.commit()
            db.rollback()
        with Session(engine()) as db:
            call=db.get(CloudCall,call_id)
            assert call.status=='reserved' and call.charged_tokens==call.reserved_tokens
            assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))
        assert len(server.requests)==1


def test_two_concurrent_settlements_cannot_create_two_terminal_receipts(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        ready=Event();release=Event()
        def worker():
            with Session(engine()) as db:
                proof=terminal.settle_and_record(db,p.workspace_id,call_id,intent)
                ready.set();assert release.wait(20);db.commit();return proof
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(worker);assert ready.wait(20)
            try:
                with Session(engine()) as db:
                    with pytest.raises((HTTPException,SQLAlchemyError)):
                        terminal.settle_and_record(db,p.workspace_id,call_id,intent)
                    db.rollback()
            finally:release.set()
            proof=future.result(timeout=20)
        with Session(engine()) as db:
            assert terminal.verify(db,p.workspace_id,call_id,intent)==proof
            ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            assert (ledger.data['calls'],ledger.data['tokens'])==(1,5)
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))==1
        assert len(server.requests)==1


def test_cross_workspace_cannot_verify_or_settle_another_call(started):
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            with pytest.raises(HTTPException):terminal.settle_and_record(db,str(uuid4()),call_id,intent)
            db.rollback()
            assert db.get(CloudCall,call_id).status=='reserved'
            assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))
        assert len(server.requests)==1


def test_failed_provider_response_with_known_usage_is_recorded_as_failure(started):
    _,args,_=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,mode='http_error',usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            proof=terminal.settle_and_record(db,p.workspace_id,call_id,intent);db.commit()
            assert proof['status']=='failed' and proof['charged_tokens']==5
            assert not proof['new_request_authorized'] and not proof['retry_authorized']
        engine().dispose()
        with Session(engine()) as db:
            assert terminal.verify(db,p.workspace_id,call_id,intent)==proof
        assert len(server.requests)==1


def test_verified_terminal_is_closed_for_isolated_inventory_only(started,monkeypatch):
    from generic_agents import same_goal_advice_common_inventory_v2 as inventory
    from generic_agents.config import settings
    from generic_agents.models import Task
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            terminal.settle_and_record(db,p.workspace_id,call_id,intent);db.commit()
        with Session(engine()) as db:
            with pytest.raises(HTTPException):inventory.read(db,p,db.get(Task,args[1]))
            db.rollback()
        monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
        with Session(engine()) as db:
            basis=inventory.read(db,p,db.get(Task,args[1]))
            assert basis['queued_v2']==0
            db.rollback()
        with pytest.raises(HTTPException):dispatch.execute_once(*args)
        assert len(server.requests)==1


def test_existing_reconciler_uses_atomic_terminal_only_when_test_opted_in(started,monkeypatch):
    from generic_agents import same_goal_advice_common_inventory_v2 as inventory
    from generic_agents.config import settings
    from generic_agents.models import Task
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
        result=old_reconcile.reconcile(p.workspace_id,call_id,intent)
        assert result=={'state':'settled','answer_state':'succeeded','usage_unavailable':False,
            'retry_authorized':False,'business_result_verified':False}
        engine().dispose()
        with Session(engine()) as db:
            assert inventory.read(db,p,db.get(Task,args[1]))['queued_v2']==0
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))==1
        assert old_reconcile.reconcile(p.workspace_id,call_id,intent)==result
        with pytest.raises(HTTPException):dispatch.execute_once(*args)
        assert len(server.requests)==1


def test_damaged_daily_total_blocks_terminal_and_any_next_request(started,monkeypatch):
    from generic_agents import same_goal_advice_common_inventory_v2 as inventory
    from generic_agents.config import settings
    from generic_agents.models import Task
    _,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            terminal.settle_and_record(db,p.workspace_id,call_id,intent);db.commit()
            ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            ledger.data={**ledger.data,'tokens':0};db.commit()
        monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
        with Session(engine()) as db:
            with pytest.raises(HTTPException):terminal.verify(db,p.workspace_id,call_id,intent)
            db.rollback()
        with Session(engine()) as db:
            with pytest.raises(HTTPException):inventory.read(db,p,db.get(Task,args[1]))
            db.rollback()
        with pytest.raises(HTTPException):dispatch.execute_once(*args)
        assert len(server.requests)==1


def test_new_request_after_verified_terminal_and_original_request_cannot_resend(started,prepared_preview,client,monkeypatch):
    from generic_agents import same_goal_advice_common_inventory_v2 as inventory
    from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
    from generic_agents import same_goal_advice_file_intent_candidate_v2 as file_intent
    from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
    from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as lease
    from generic_agents import same_goal_advice_cloud_reservation_candidate_v2 as reserve
    from generic_agents import same_goal_advice_unlimited_contracts_v2 as v2contracts
    from generic_agents import evidence
    from generic_agents.config import settings
    from generic_agents.contracts import digest
    from generic_agents.models import Capability, Plan, Task
    s,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1];intent=first['intent_audit_id']
        with Session(engine()) as db:
            terminal.settle_and_record(db,p.workspace_id,call_id,intent);db.commit()
        monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
        monkeypatch.setattr(settings(),'v2_history_compat_test_enabled',True)
        path='/api/workspace/tasks/'+s['task_id']+'/business-advice/preparations'
        first_history=client.get(path)
        assert first_history.status_code==200,first_history.text
        prep=copy.deepcopy(s['prep']);original_request=copy.deepcopy(prep['request'])
        new_request=copy.deepcopy(original_request);new_request['request_id']=str(uuid4())
        new_request['reason']='合成的第二次独立建议申请'
        prep['scope']['analysis_plan_id']=str(uuid4());prep['request']=new_request
        prep['request_hash']=digest(new_request);prep.pop('record_hash')
        with Session(engine()) as db:
            manifest=copy.deepcopy(db.get(Capability,prep['advice_capability_ref']['id']).data)
        prep=v2contracts.seal_preparation(prep,manifest=manifest)
        monkeypatch.setattr(writer.candidate,'derive_candidate',lambda *_:{'preparation_candidate':copy.deepcopy(prep)})
        with Session(engine()) as db:
            created=writer.prepare(db,p,s['task_id'],new_request,prep['budget']['policy_ref']);db.commit()
            assert created['plan_id']!=args[2] and not created['replayed']
        engine().dispose()
        with Session(engine()) as db:
            basis=inventory.read(db,p,db.get(Task,s['task_id']))
            assert basis['queued_v2']==1 and db.get(Plan,created['plan_id']).status=='queued'
            replay=writer.prepare(db,p,s['task_id'],new_request,prep['budget']['policy_ref'])
            assert replay['replayed'] and replay['plan_id']==created['plan_id'];db.rollback()
        with Session(engine()) as db:
            with pytest.raises(HTTPException):writer.prepare(db,p,s['task_id'],original_request,prep['budget']['policy_ref'])
            db.rollback()
        with pytest.raises(HTTPException):dispatch.execute_once(*args)
        # Complete a genuinely distinct second application using synthetic
        # Goal/source substitutes and the real local PG and loopback sender.
        with Session(engine()) as db:
            file_intent.register(db,p,s['task_id'],created['job_id']);db.commit()
        with Session(engine()) as db:
            document.process(db,p,s['task_id'],created['job_id']);db.commit()
        frame=copy.deepcopy(prepared_preview[3])
        with Session(engine(),expire_on_commit=False) as db:
            doc=document.cold_read(db,p,db.get(Task,s['task_id']),db.get(Plan,created['plan_id']))
            doc_ref=doc['document']['evidence_ref']
            item=db.get(evidence.Evidence,doc_ref['evidence_id'])
            evidence.authorize_cloud(db,p,item.id,prep['model_pin']['id'],item.version,'合成第二次申请授权');db.commit()
        source=copy.deepcopy(frame['source_record']);source.pop('record_hash')
        source['plan_id']=created['plan_id'];source['document']=doc['document']
        frame['source_record']=v2contracts.seal_source(source,preparation=prep,manifest=manifest)
        frame['evidence_ids'][1]=doc_ref['evidence_id']
        frame['decision_input']['sources'][1].update(source_id=doc_ref['evidence_id'],content_hash=doc_ref['sha256'])
        monkeypatch.setattr(preview.frame_builder,'build_current',lambda *_,**__:copy.deepcopy(frame))
        request={'request_id':str(uuid4()),'expected_plan_version':2,'expected_goal_hash':prep['scope']['goal_hash'],
            'expected_mature_head_hash':prep['mature_head']['head_hash'],'reason':'合成第二次预览'}
        with Session(engine()) as db:
            shown=preview.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
        start_body={'request_id':str(uuid4()),'expected_plan_version':3,'expected_goal_hash':request['expected_goal_hash'],
            'expected_mature_head_hash':request['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
            'cloud_authorized':True,'reason':'合成第二次启动'}
        with Session(engine()) as db:
            start.begin(db,p,s['task_id'],created['plan_id'],start_body);db.commit()
        with Session(engine()) as db:
            owned=lease.acquire(db,p,s['task_id'],created['plan_id']);db.commit()
        with Session(engine()) as db:
            allocated=reserve.reserve(db,p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token']);db.commit()
        active_history=client.get(path)
        assert active_history.status_code==200,active_history.text
        second_args=(p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],allocated['cloud_call_id'])
        second=dispatch.execute_once(*second_args)
        with Session(engine()) as db:
            terminal.settle_and_record(db,p.workspace_id,allocated['cloud_call_id'],second['intent_audit_id']);db.commit()
        engine().dispose()
        with Session(engine()) as db:
            assert inventory.read(db,p,db.get(Task,s['task_id']))['queued_v2']==0
            ledger=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
            assert (ledger.data['calls'],ledger.data['tokens'])==(2,10)
            assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))==2
        final_history=client.get(path)
        assert final_history.status_code==200,final_history.text
        from generic_agents import same_goal_advice_terminal_paging_test_v2 as paging
        with Session(engine()) as db:
            summary=paging.scan(db,p,s['task_id'],page_size=1)
            assert summary=={'verified_terminals':2,'pages':2,'max_page_rows':1,
                'new_request_authorized':False,'retry_authorized':False}
            db.rollback()
        with pytest.raises(HTTPException):dispatch.execute_once(*second_args)
        assert len(server.requests)==2


def test_budget_http_reports_terminal_as_needing_snapshot_review(started,client,monkeypatch):
    from generic_agents.config import settings
    from tests.test_goal_budget_display_advice_versions_candidate_pg import setup_context
    s,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}):
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1]
        with Session(engine()) as db:
            terminal.settle_and_record(db,p.workspace_id,call_id,first['intent_audit_id']);db.commit()
        monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
        setup_context(monkeypatch,s)
        path='/api/workspace/tasks/'+s['task_id']+'/goal/budget-display'
        response=client.get(path)
        assert response.status_code==409
        assert '已结算业务建议' in response.json()['detail'] and '尚不可用' in response.json()['detail']


def test_concurrent_same_new_request_after_terminal_creates_one_plan(started,monkeypatch):
    from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
    from generic_agents import same_goal_advice_unlimited_contracts_v2 as v2contracts
    from generic_agents.config import settings
    from generic_agents.contracts import digest
    from generic_agents.models import Capability, Plan
    s,args,opinion=setup(started);_install();port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args);p=args[0];call_id=args[-1]
        with Session(engine()) as db:
            terminal.settle_and_record(db,p.workspace_id,call_id,first['intent_audit_id']);db.commit()
        monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
        prep=copy.deepcopy(s['prep']);request=copy.deepcopy(prep['request']);request['request_id']=str(uuid4())
        request['reason']='合成并发第二次申请';prep['scope']['analysis_plan_id']=str(uuid4())
        prep['request']=request;prep['request_hash']=digest(request);prep.pop('record_hash')
        with Session(engine()) as db:manifest=copy.deepcopy(db.get(Capability,prep['advice_capability_ref']['id']).data)
        prep=v2contracts.seal_preparation(prep,manifest=manifest)
        monkeypatch.setattr(writer.candidate,'derive_candidate',lambda *_:{'preparation_candidate':copy.deepcopy(prep)})
        barrier=Barrier(2)
        def run():
            with Session(engine()) as db:
                barrier.wait(timeout=10)
                result=writer.prepare(db,p,s['task_id'],request,prep['budget']['policy_ref']);db.commit();return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(run) for _ in range(2)]
            results=[future.result(timeout=30) for future in futures]
        assert {item['plan_id'] for item in results}=={prep['scope']['analysis_plan_id']}
        assert sorted(item['replayed'] for item in results)==[False,True]
        with Session(engine()) as db:
            assert db.scalar(select(func.count()).select_from(Plan).where(Plan.task_id==s['task_id']))==2
            assert db.scalar(select(func.count()).select_from(CloudCall))==1
        assert len(server.requests)==1


@pytest.mark.xfail(strict=True,raises=HTTPException,
    reason='已知阻碍：版本库存按单次查询限制 1000 条，需稳定分页和反向孤儿核验')
def test_inventory_reader_can_examine_more_than_one_thousand_records(account):
    """Acceptance gate, not proof that these synthetic events are valid history."""
    from generic_agents import same_goal_advice_store_v302 as old
    from generic_agents.models import User
    from generic_agents.security import Principal
    username,_,workspace=account
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==username));p=Principal(user.id,workspace,'admin','test')
        db.add_all([Audit(id=str(uuid4()),workspace_id=workspace,actor_id=user.id,
            action='synthetic.terminal_pagination',object_id=str(uuid4()),data={'sequence':i}) for i in range(1001)])
        db.commit()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.workspace_id==workspace,Audit.action=='synthetic.terminal_pagination'))==1001
        rows=old._rows(db,p,Audit,Audit.action=='synthetic.terminal_pagination')
        assert len(rows)==1001 and {row.data['sequence'] for row in rows}==set(range(1001))
