"""Integration of isolated terminal and v1-history/recovery experiments.

Goal/source/views use synthetic substitutes. No real provider or workspace.
"""
import json
import os
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Membership, User
from generic_agents import cloud_budget
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from generic_agents import same_goal_advice_loopback_dispatch_candidate_v2 as dispatch
from generic_agents import same_goal_advice_response_spool_test_v2 as spool
from generic_agents import same_goal_advice_terminal_candidate_v2 as terminal
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_advice_v2_recovery_console_candidate_pg import spool_root, _pending, _url
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import synthetic_source_expiry
from tests.test_same_goal_advice_runtime_v308 import _opinion
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody
from tests.test_v1_history_reserved_candidate_pg import history

pytestmark=pytest.mark.skipif(not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'),reason='isolated fake provider only')


def _enable_terminal(monkeypatch):
    with Session(engine()) as db:terminal.install(db);db.commit()
    monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
    monkeypatch.setattr(settings(),'v2_history_compat_test_enabled',True)


def test_v1_http_history_after_terminal_and_tampered_terminal_fails(history,client,monkeypatch):
    s,created,owned,reserved,old_id,data=history;p=s['principal']
    with Session(engine()) as db:
        if db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None:
            storage.install(db);db.commit()
        shown=db.scalar(select(Audit).where(Audit.action==preview.ACTION,Audit.object_id==created['plan_id']))
        opinion=_opinion(shown.data['preview_record']['cloud_request']['decision_input'])
    with Session(engine()) as db:terminal.install(db);db.commit()
    args=(p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'])
    with provider(int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']),content=json.dumps(opinion),
        usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args)
        with Session(engine()) as db:
            proof=terminal.settle_and_record(db,p.workspace_id,args[-1],first['intent_audit_id']);db.commit()
        monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
        monkeypatch.setattr(settings(),'v2_history_compat_test_enabled',True)
        base='/api/workspace/tasks/'+s['task_id']+'/business-advice/preparations'
        response=client.get(base)
        assert response.status_code==200,response.text
        assert response.json()['records']==[{'id':old_id,'status':'cancelled'}]
        assert client.post(base,json=data['operations'][0]['body']).json()['id']==old_id
        with Session(engine()) as db:
            record=db.get(Audit,proof['terminal_audit_id']);record.data={**record.data,'charged_tokens':4};db.commit()
        assert client.get(base).status_code==409
        assert len(server.requests)==1


def test_dual_flags_revocation_during_file_read_blocks_recovery(started,client,account,monkeypatch):
    args,outcome,permit=_pending(started,monkeypatch);_enable_terminal(monkeypatch)
    original=spool.load
    def revoke(*a,**kw):
        value=original(*a,**kw)
        with Session(engine()) as db:
            user=db.scalar(select(User).where(User.username==account[0]))
            member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==account[2]))
            member.role='viewer';db.commit()
        return value
    with monkeypatch.context() as patch:
        patch.setattr(spool,'load',revoke)
        assert client.post(_url(args[1])+'/'+permit+'/recover').status_code==409
    with Session(engine()) as db:
        assert storage.read(db,args[0].workspace_id,args[-1],outcome['intent_audit_id'])['state']=='unknown'
        assert db.get(CloudCall,args[-1]).status=='reserved'


def test_dual_flags_natural_session_expiry_blocks_terminal_commit(started,client,account,monkeypatch):
    args,outcome,permit=_pending(started,monkeypatch);_enable_terminal(monkeypatch)
    with Session(engine()) as db:
        session=db.get(AuthSession,args[0].session_id)
        session.expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=1);db.commit()
    original=cloud_budget.settle_paid_response
    def slow(*a,**kw):
        result=original(*a,**kw);time.sleep(1.5);return result
    with monkeypatch.context() as patch:
        patch.setattr(cloud_budget,'settle_paid_response',slow)
        assert client.post(_url(args[1])+'/'+permit+'/recover').status_code==409
    with Session(engine()) as db:
        assert isinstance(storage.read(db,args[0].workspace_id,args[-1],outcome['intent_audit_id']),tuple)
        assert db.get(CloudCall,args[-1]).status=='reserved'
        assert not db.scalar(select(func.count()).select_from(Audit).where(Audit.action==terminal.ACTION))
    with TestClient(app) as fresh:
        login=fresh.post('/api/login',json={'username':account[0],'password':account[1]})
        fresh.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        response=fresh.post(_url(args[1])+'/'+permit+'/recover')
        assert response.status_code==200,response.text


def test_v1_http_with_terminal_and_distinct_active_reservation(history,prepared_preview,client,monkeypatch):
    import copy
    from uuid import uuid4
    from generic_agents import evidence
    from generic_agents.contracts import digest
    from generic_agents.models import Capability, Evidence, Plan, Task
    from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
    from generic_agents import same_goal_advice_file_intent_candidate_v2 as file_intent
    from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
    from generic_agents import same_goal_advice_unlimited_contracts_v2 as contracts
    from generic_agents import same_goal_advice_unlimited_start_candidate_v2 as start
    from generic_agents import same_goal_advice_unlimited_lease_candidate_v2 as lease
    from generic_agents import same_goal_advice_cloud_reservation_candidate_v2 as reservation
    s,created,owned,reserved,old_id,_=history;p=s['principal']
    with Session(engine()) as db:
        if db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None:
            storage.install(db);db.commit()
        terminal.install(db);db.commit()
        shown=db.scalar(select(Audit).where(Audit.action==preview.ACTION,Audit.object_id==created['plan_id']))
        opinion=_opinion(shown.data['preview_record']['cloud_request']['decision_input'])
    args=(p,s['task_id'],created['plan_id'],owned['job_id'],owned['lease_token'],reserved['cloud_call_id'])
    with provider(int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']),content=json.dumps(opinion),
        usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        first=dispatch.execute_once(*args)
        with Session(engine()) as db:
            terminal.settle_and_record(db,p.workspace_id,args[-1],first['intent_audit_id']);db.commit()
        monkeypatch.setattr(settings(),'v2_terminal_history_test_enabled',True)
        monkeypatch.setattr(settings(),'v2_history_compat_test_enabled',True)
        prep=copy.deepcopy(s['prep']);request=copy.deepcopy(prep['request'])
        request['request_id']=str(uuid4());request['reason']='合成第二次申请'
        prep['scope']['analysis_plan_id']=str(uuid4());prep['request']=request
        prep['request_hash']=digest(request);prep.pop('record_hash')
        with Session(engine()) as db:manifest=copy.deepcopy(db.get(Capability,prep['advice_capability_ref']['id']).data)
        prep=contracts.seal_preparation(prep,manifest=manifest)
        monkeypatch.setattr(writer.candidate,'derive_candidate',lambda *_:{'preparation_candidate':copy.deepcopy(prep)})
        with Session(engine()) as db:
            created2=writer.prepare(db,p,s['task_id'],request,prep['budget']['policy_ref']);db.commit()
        with Session(engine()) as db:file_intent.register(db,p,s['task_id'],created2['job_id']);db.commit()
        with Session(engine()) as db:document.process(db,p,s['task_id'],created2['job_id']);db.commit()
        frame=copy.deepcopy(prepared_preview[3])
        with Session(engine(),expire_on_commit=False) as db:
            doc=document.cold_read(db,p,db.get(Task,s['task_id']),db.get(Plan,created2['plan_id']))
            ref=doc['document']['evidence_ref'];item=db.get(Evidence,ref['evidence_id'])
            evidence.authorize_cloud(db,p,item.id,prep['model_pin']['id'],item.version,'合成第二次授权');db.commit()
        source=copy.deepcopy(frame['source_record']);source.pop('record_hash')
        source['plan_id']=created2['plan_id'];source['document']=doc['document']
        frame['source_record']=contracts.seal_source(source,preparation=prep,manifest=manifest)
        frame['evidence_ids'][1]=ref['evidence_id']
        frame['decision_input']['sources'][1].update(source_id=ref['evidence_id'],content_hash=ref['sha256'])
        monkeypatch.setattr(preview.frame_builder,'build_current',lambda *_,**__:copy.deepcopy(frame))
        preview_body={'request_id':str(uuid4()),'expected_plan_version':2,
            'expected_goal_hash':prep['scope']['goal_hash'],'expected_mature_head_hash':prep['mature_head']['head_hash'],
            'reason':'合成第二次预览'}
        with Session(engine()) as db:shown=preview.prepare(db,p,s['task_id'],created2['plan_id'],preview_body);db.commit()
        body={'request_id':str(uuid4()),'expected_plan_version':3,
            'expected_goal_hash':preview_body['expected_goal_hash'],
            'expected_mature_head_hash':preview_body['expected_mature_head_hash'],'preview_hash':shown['preview_hash'],
            'cloud_authorized':True,'reason':'合成第二次启动'}
        with Session(engine()) as db:start.begin(db,p,s['task_id'],created2['plan_id'],body);db.commit()
        with Session(engine()) as db:owned2=lease.acquire(db,p,s['task_id'],created2['plan_id']);db.commit()
        with Session(engine()) as db:
            allocated=reservation.reserve(db,p,s['task_id'],created2['plan_id'],owned2['job_id'],owned2['lease_token']);db.commit()
        base='/api/workspace/tasks/'+s['task_id']+'/business-advice/preparations'
        result=client.get(base)
        assert result.status_code==200,result.text
        assert result.json()['records']==[{'id':old_id,'status':'cancelled'}]
        with Session(engine()) as db:
            row=db.get(CloudCall,allocated['cloud_call_id']);row.reserved_tokens+=1;db.commit()
        assert client.get(base).status_code==409
        assert len(server.requests)==1
