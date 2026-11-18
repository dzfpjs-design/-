"""No network: task grants in a strictly fenced synthetic PostgreSQL."""
import copy
from uuid import uuid4

import pytest
from starlette.requests import Request
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from generic_agents import cloud_report_plan_allowance as plans
from generic_agents import report_evidence_pipeline as evidence
from generic_agents import report_evidence_delegated_origin as origin
from generic_agents import report_reading_drafts as legacy
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, Job, SetupReportDraft
from generic_agents.security import authenticated
from generic_agents.services import audit
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_delegated_origin import proposed


@pytest.fixture(autouse=True)
def own_database(monkeypatch):
    import tests.test_report_full_reading_source as source_tests
    import tests.test_report_full_reading_pipeline as pipeline_tests
    def check():
        url=engine().url
        assert url.host=='127.0.0.1' and url.database.startswith('ga_test_')
    check();monkeypatch.setattr(source_tests,'fence',check);monkeypatch.setattr(pipeline_tests,'fence',check)
    if not inspect(engine()).has_table('report_full_provisional_candidates',schema='public'):
        from generic_agents.report_full_provisional_migration import install
        install(engine())


def prepared_request(scene):
    wire=scene['client'].build_request('POST','/api/setup/report-evidence-delegated')
    return Request({'type':'http','http_version':'1.1','scheme':'http','method':'POST',
        'path':'/api/setup/report-evidence-delegated','raw_path':b'/api/setup/report-evidence-delegated',
        'query_string':b'','headers':[(k.lower(),v) for k,v in wire.headers.raw],'client':('127.0.0.1',1234),
        'server':('testserver',80)})


def prepare(scene, monkeypatch, db, *, deepseek=True):
    # This low-level contract fixture exercises ledger internals; production
    # activation is separately covered by scoped-activation integration tests.
    from generic_agents import report_evidence_delegated_dispatch_gate as gate
    monkeypatch.setattr(gate, 'ready', lambda: True)
    if deepseek:
        import json
        from pathlib import Path
        from tests.test_vertical_slice import publish
        from tests.test_report_full_second_batch_preview import scene_with_candidate
        _,old,_=scene_with_candidate(scene,monkeypatch)
        model=json.loads((Path(__file__).parents[1]/'examples/deepseek.json').read_text())
        model.update(name='synthetic_plan_'+uuid4().hex[:10],description='Synthetic no-network scope')
        model['execution'].update(model='deepseek-v4-pro',thinking_mode='disabled',max_tokens=8192)
        cap=publish(scene['client'],model)
        reading={**copy.deepcopy(scene['body']),'request_id':'fl_'+str(uuid4()),'model_id':cap['id']}
        body={'original_draft_id':old['id'],'original_revision':old['revision'],
              'operator_asserted_history_hash':'a'*64,'reading':reading}
        resp=scene['client'].post('/api/setup/report-evidence-delegated/preview',json=body)
        assert resp.status_code==200,resp.text
        proposal=resp.json()
    else:
        _,old,_,body,proposal=proposed(scene,monkeypatch)
    request=prepared_request(scene);p=authenticated(request,db)
    draft_id,job_id=str(uuid4()),str(uuid4())
    signed=plans.build_allowance_preview(p,proposal['reading_preview'],proposal['basis'],draft_id,job_id,
        db.scalar(select(func.clock_timestamp())))
    token=plans.arm_issuance(db,p,request,draft_id,job_id,
        approved_preview_hash=signed['allowance_preview_hash'],consent=True)
    start_body=origin.LinkedStartCommand(**body,linked_preview_hash=proposal['linked_preview_hash'],consent=True)
    reading=proposal['reading_preview'];start=evidence.StartCommand(**start_body.reading.model_dump(mode='json'),
        preview_hash=reading['preview_hash'],consent=True)
    row=SetupReportDraft(id=draft_id,workspace_id=p.workspace_id,actor_id=p.user_id,
        request_id=start_body.reading.request_id,status='queued')
    job=Job(id=job_id,workspace_id=p.workspace_id,kind=legacy.KIND,
        idempotency_key='report-evidence:'+row.request_id,
        data={'actor_id':p.user_id,'report_draft_id':row.id})
    db.add(job);db.flush();row.data=evidence._new_data(p,row.id,start,reading,job.id)
    db.add(row);db.flush()
    link={'schema':origin.ORIGIN,'basis':proposal['basis'],'new_draft_id':row.id,
        'new_job_id':job.id,'request_id':row.request_id,'command_hash':origin._signature(p,start_body)}
    oh=digest(link);db.add(Job(workspace_id=p.workspace_id,kind='report_evidence_origin_v1',status='internal',
        idempotency_key=origin._key(old['id']),data={'payload':link,'origin_hash':oh}))
    audit(db,p,'setup.report_evidence.authorize',row.id,{'request_id':row.request_id,
        'preview_hash':reading['preview_hash'],'map_calls':reading['input_summary']['map_call_count']})
    audit(db,p,origin.ACTION,row.id,{'origin_hash':oh,'request_id':row.request_id});db.flush()
    return p,request,row,job,signed,token


def test_issue_is_atomic_and_uses_only_server_plan_bounds(reading_scene,monkeypatch):
    with Session(engine(),expire_on_commit=False) as db:
        p,request,row,job,signed,token=prepare(reading_scene,monkeypatch,db)
        grant_id=plans.issue_plan_tx(db,p,request,row.id,job.id,signed,token)
        grant=db.get(Job,grant_id);stage_count=len(signed['stages'])
        assert grant.data['manifest']['max_calls']==stage_count
        assert grant.data['manifest']['max_tokens']==sum(s['token_bound'] for s in signed['stages'])
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='cloud_extra_allowance_v1'))==stage_count
        db.commit()
        assert grant.data['consumed']==[]
    with Session(engine()) as db:
        assert db.get(Job,grant_id).data['manifest_hash']==digest(db.get(Job,grant_id).data['manifest'])
        assert db.get(SetupReportDraft,row.id).status=='queued'


def test_rollback_leaves_no_report_or_permits(reading_scene,monkeypatch):
    with Session(engine(),expire_on_commit=False) as db:
        p,request,row,job,signed,token=prepare(reading_scene,monkeypatch,db)
        rid,jid=row.id,job.id
        plans.issue_plan_tx(db,p,request,rid,jid,signed,token);db.rollback()
    with Session(engine()) as db:
        assert db.get(SetupReportDraft,rid) is None and db.get(Job,jid) is None
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='cloud_report_plan_allowance_v1'))==0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='cloud_extra_allowance_v1'))==0


def test_unarmed_or_tampered_preview_cannot_issue(reading_scene,monkeypatch):
    with Session(engine(),expire_on_commit=False) as db:
        p,request,row,job,signed,token=prepare(reading_scene,monkeypatch,db)
        altered=copy.deepcopy(signed);altered['max_tokens']+=1
        with pytest.raises(plans.PlanAllowanceDenied):plans.issue_plan_tx(db,p,request,row.id,job.id,altered,token)
        db.rollback()


@pytest.mark.parametrize('case',['grant','permit','membership','job','capability'])
def test_commit_drift_rolls_back_entire_issuance(reading_scene,monkeypatch,case):
    from generic_agents.models import Capability, Membership
    with Session(engine(),expire_on_commit=False) as db:
        p,request,row,job,signed,token=prepare(reading_scene,monkeypatch,db)
        rid,jid=row.id,job.id
        gid=plans.issue_plan_tx(db,p,request,rid,jid,signed,token)
        if case=='grant':
            grant=db.get(Job,gid);value=copy.deepcopy(grant.data);value['manifest']['max_tokens']+=1;grant.data=value
        elif case=='permit':
            permit=db.scalar(select(Job).where(Job.kind=='cloud_extra_allowance_v1'))
            value=copy.deepcopy(permit.data);value['manifest']['max_tokens']+=1;permit.data=value
        elif case=='membership':
            db.scalar(select(Membership).where(Membership.user_id==p.user_id,Membership.workspace_id==p.workspace_id)).role='viewer'
        elif case=='job':job.status='failed'
        else:db.get(Capability,signed['model_id']).status='draft'
        with pytest.raises(plans.PlanAllowanceDenied):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(SetupReportDraft,rid) is None and db.get(Job,jid) is None
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='cloud_report_plan_allowance_v1'))==0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='cloud_extra_allowance_v1'))==0


def test_missing_or_replayed_arm_fails_without_writes(reading_scene,monkeypatch):
    with Session(engine(),expire_on_commit=False) as db:
        p,request,row,job,signed,token=prepare(reading_scene,monkeypatch,db)
        with pytest.raises(plans.PlanAllowanceDenied):plans.issue_plan_tx(db,p,request,row.id,job.id,signed,object())
        plans.issue_plan_tx(db,p,request,row.id,job.id,signed,token)
        with pytest.raises(plans.PlanAllowanceDenied):plans.issue_plan_tx(db,p,request,row.id,job.id,signed,token)
        db.rollback()


def test_scoped_stage_reservation_atomically_binds_real_report_job_and_budget(reading_scene,monkeypatch):
    from datetime import timedelta
    from generic_agents import cloud_scoped_extra_reservation as scoped
    from generic_agents.leases import attach
    from generic_agents.models import CloudCall, now
    from generic_agents.security import Principal
    with Session(engine(),expire_on_commit=False) as db:
        p,request,row,job,signed,token=prepare(reading_scene,monkeypatch,db)
        gid=plans.issue_plan_tx(db,p,request,row.id,job.id,signed,token)
        rid,jid,cap_id=row.id,job.id,signed['model_id'];first=signed['stages'][0];db.commit()
    lease=str(uuid4())
    with Session(engine()) as db:
        j=db.get(Job,jid);j.status='running';j.lease_token=lease;j.lease_until=now()+timedelta(minutes=5)
        r=db.get(SetupReportDraft,rid);r.status='running';st=evidence._stage(r,first['stage_id'])
        evidence._replace_stage(r,first['stage_id'],{**st,'status':'running','inflight':True});db.commit()
    worker_p=Principal(p.user_id,p.workspace_id,p.role,'worker')
    with Session(engine(),expire_on_commit=False) as db:
        attach(db,jid,lease);db.info['worker_actor']=worker_p
        permit=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
            Job.idempotency_key==scoped.permit_key(jid,first['stage_id'])))
        call_id=scoped.reserve_scoped_tx(db,worker_p,permit.id,cap_id,first['token_bound'],
            first['request_hash'],first['options_hash'])
        db.commit()
    with Session(engine()) as db:
        r=db.get(SetupReportDraft,rid);grant=db.get(Job,gid);call=db.get(CloudCall,call_id)
        stage=evidence._stage(r,first['stage_id'])
        assert stage['reserved_call_id']==call_id and stage['inflight'] is True
        assert grant.data['consumed']==[{'stage_id':first['stage_id'],'call_id':call_id,'token_bound':first['token_bound']}]
        assert call.data['input_policy_hash']==first['input_policy_hash']
        budget=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget'))
        assert budget.data['tokens']>=first['token_bound'] and budget.data['calls']>=1


def stage_fixture(scene,monkeypatch, *, deepseek=True):
    from datetime import timedelta
    from generic_agents.models import now
    from generic_agents.security import Principal
    with Session(engine(),expire_on_commit=False) as db:
        p,request,row,job,signed,token=prepare(scene,monkeypatch,db,deepseek=deepseek)
        gid=plans.issue_plan_tx(db,p,request,row.id,job.id,signed,token)
        rid,jid,cap_id=row.id,job.id,signed['model_id'];first=signed['stages'][0];db.commit()
    lease=str(uuid4())
    with Session(engine()) as db:
        j=db.get(Job,jid);j.status='running';j.lease_token=lease;j.lease_until=now()+timedelta(minutes=5)
        r=db.get(SetupReportDraft,rid);r.status='running';st=evidence._stage(r,first['stage_id'])
        evidence._replace_stage(r,first['stage_id'],{**st,'status':'running','inflight':True});db.commit()
    return Principal(p.user_id,p.workspace_id,p.role,'worker'),rid,jid,gid,cap_id,first,lease


def reserve_first(db,fixture):
    from generic_agents import cloud_scoped_extra_reservation as scoped
    p,rid,jid,gid,cap,first,lease=fixture
    permit=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
        Job.idempotency_key==scoped.permit_key(jid,first['stage_id'])))
    return scoped.reserve_scoped_tx(db,p,permit.id,cap,first['token_bound'],first['request_hash'],first['options_hash'])


def test_stage_reservation_rollback_retains_exact_report_and_budget(reading_scene,monkeypatch):
    from generic_agents.leases import attach
    from generic_agents.models import CloudCall
    p,rid,jid,gid,cap,first,lease=fixture=stage_fixture(reading_scene,monkeypatch)
    with Session(engine()) as db:
        before=digest(db.get(SetupReportDraft,rid).data)
        calls=db.scalar(select(func.count()).select_from(CloudCall))
        budget=copy.deepcopy(db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget')).data)
    with Session(engine(),expire_on_commit=False) as db:
        attach(db,jid,lease);db.info['worker_actor']=p
        reserve_first(db,fixture);db.rollback()
    with Session(engine()) as db:
        assert digest(db.get(SetupReportDraft,rid).data)==before and db.get(Job,gid).data['consumed']==[]
        assert db.scalar(select(func.count()).select_from(CloudCall))==calls
        assert db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget')).data==budget


def test_stage_or_total_change_after_reserve_cannot_commit(reading_scene,monkeypatch):
    from generic_agents.leases import attach
    from generic_agents import cloud_scoped_extra_reservation as scoped
    p,rid,jid,gid,cap,first,lease=fixture=stage_fixture(reading_scene,monkeypatch)
    with Session(engine(),expire_on_commit=False) as db:
        attach(db,jid,lease);db.info['worker_actor']=p
        reserve_first(db,fixture)
        grant=db.get(Job,gid);data=copy.deepcopy(grant.data);data['consumed']=[];grant.data=data
        with pytest.raises((plans.PlanAllowanceDenied,scoped.ScopedReservationDenied)):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Job,gid).data['consumed']==[]
        assert evidence._stage(db.get(SetupReportDraft,rid),first['stage_id'])['reserved_call_id'] is None



@pytest.mark.parametrize('mode',['normal','overspent','missing_usage','tamper_capability','tamper_version','tamper_created_at','delete_prior','expire_prior'])
def test_two_real_frozen_stages_require_settled_first_and_keep_total_cap(reading_scene,monkeypatch,tmp_path,mode):
    """Synthetic saved wire/journal, no real network or model invocation."""
    import json
    from generic_agents import report_evidence_delegated_wire_v1 as wire
    from generic_agents import report_evidence_http_capture_v1 as capture
    from generic_agents import report_evidence_native_bridge_v1 as bridge
    from generic_agents import report_evidence_journal as journal
    from generic_agents import cloud_scoped_extra_reservation as scoped
    from generic_agents.leases import attach
    from generic_agents.models import CloudCall, Capability
    monkeypatch.setattr(wire,'root',lambda:tmp_path/'synthetic-wire')
    monkeypatch.setattr(evidence,'_journal_root',lambda *args:tmp_path/'synthetic-journal')
    p,rid,jid,gid,cap,first,lease=fixture=stage_fixture(reading_scene,monkeypatch,deepseek=True)
    with Session(engine(),expire_on_commit=False) as db:
        attach(db,jid,lease);db.info['worker_actor']=p
        call_id=reserve_first(db,fixture);db.commit()
    with Session(engine(),expire_on_commit=False) as db:
        attach(db,jid,lease);db.info['worker_actor']=p
        row=evidence.owned(db,p,rid,True);ctx=wire.context(evidence,row,first['stage_id'])
        value={'schema':'report-evidence-map-v3','claims':[]}
        total=first['token_bound']+1 if mode=='overspent' else 20
        response={'choices':[{'message':{'content':json.dumps(value)},'finish_reason':'stop'}],
                  'usage':{} if mode=='missing_usage' else {'prompt_tokens':12,'completion_tokens':8,'total_tokens':total}}
        sink=capture.prepare(wire.root(),ctx)
        with sink:
            sink.mark_request_started();sink.begin_response(200,'','synthetic-request');sink.append(json.dumps(response).encode());sink.finish(eof=True)
        content,receipt,_=wire.response(evidence,row,first['stage_id'])
        binding=evidence.journal_binding(row,first['stage_id'],call_id)
        descriptor=bridge.write_missing_journal(journal,evidence._stage_journal_root(row,first['stage_id']),binding,content,receipt)
        st=evidence._stage(row,first['stage_id']);evidence._replace_stage(row,first['stage_id'],{**st,'journal':descriptor})
        evidence._settle(db,p,row,first['stage_id'],receipt,True)
        evidence._consume(db,p,row,first['stage_id'])
    with Session(engine(),expire_on_commit=False) as db:
        row=db.get(SetupReportDraft,rid);assert row.data['maps'][0]['status']=='succeeded'
        assert len(row.data['maps'])>=2
        second=row.data['maps'][1]['chunk_id'];st=evidence._stage(row,second)
        evidence._replace_stage(row,second,{**st,'status':'running','inflight':True});db.commit()
    other_cap_id=None
    if mode=='tamper_capability':
        with Session(engine()) as db:
            original=db.get(Capability,cap)
            other=Capability(workspace_id=p.workspace_id,namespace=original.namespace,name='synthetic-other-model',
                release_version=original.release_version,kind=original.kind,status=original.status,
                content_hash=original.content_hash,validated_hash=original.validated_hash,data=copy.deepcopy(original.data))
            db.add(other);db.flush();other_cap_id=other.id;db.commit()
    with Session(engine()) as db:
        before_grant=copy.deepcopy(db.get(Job,gid).data)
        before_budget=copy.deepcopy(db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget')).data)
        previous=db.get(CloudCall,call_id)
        before_first={column.key:copy.deepcopy(getattr(previous,column.key)) for column in inspect(previous).mapper.column_attrs}
    attempted_id=None
    with Session(engine(),expire_on_commit=False) as db:
        attach(db,jid,lease);db.info['worker_actor']=p
        grant=db.get(Job,gid);entry=grant.data['manifest']['stage_plan'][1]
        permit=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
            Job.idempotency_key==scoped.permit_key(jid,entry['stage_id'])))
        if mode in ('overspent','missing_usage'):
            with pytest.raises(plans.PlanAllowanceDenied):
                scoped.reserve_scoped_tx(db,p,permit.id,cap,entry['token_bound'],entry['request_hash'],entry['options_hash'])
            db.rollback();second_call=None
        else:
            second_call=scoped.reserve_scoped_tx(db,p,permit.id,cap,entry['token_bound'],entry['request_hash'],entry['options_hash'])
            attempted_id=second_call
            if mode=='normal':
                db.commit()
            else:
                previous=db.get(CloudCall,call_id)
                if mode=='tamper_capability':previous.capability_id=other_cap_id
                elif mode=='tamper_version':previous.version+=1
                elif mode=='tamper_created_at':
                    from datetime import timedelta
                    previous.created_at+=timedelta(seconds=1)
                elif mode=='delete_prior':db.delete(previous)
                elif mode=='expire_prior':db.expire(previous)
                with pytest.raises(plans.PlanAllowanceDenied):db.commit()
                db.rollback();second_call=None
    with Session(engine()) as db:
        grant=db.get(Job,gid)
        assert [x['stage_id'] for x in grant.data['consumed']]==([first['stage_id']] if mode!='normal' else [first['stage_id'],second])
        assert db.get(CloudCall,call_id).charged_tokens==({'overspent':first['token_bound']+1,'missing_usage':first['token_bound']}.get(mode,20))
        assert evidence._stage(db.get(SetupReportDraft,rid),second)['reserved_call_id']==second_call
        assert sum(x['token_bound'] for x in grant.data['consumed'])<=grant.data['manifest']['max_tokens']
        if mode!='normal':
            assert grant.data==before_grant
            assert db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind=='cloud_budget')).data==before_budget
            previous=db.get(CloudCall,call_id)
            assert {column.key:getattr(previous,column.key) for column in inspect(previous).mapper.column_attrs}==before_first
            if attempted_id is not None:assert db.get(CloudCall,attempted_id) is None
            permit=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
                Job.idempotency_key==scoped.permit_key(jid,second)))
            assert permit.data['used_call_id'] is None
            assert db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
                Job.idempotency_key==scoped.dispatch_key(jid,second))) is None


def test_same_stage_concurrent_reservations_consume_once(reading_scene,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi import HTTPException
    from sqlalchemy.exc import DBAPIError,IntegrityError
    from generic_agents.leases import attach,ExecutionUnauthorized,LeaseLost
    from generic_agents import cloud_scoped_extra_reservation as scoped
    from generic_agents.models import CloudCall
    fixture=stage_fixture(reading_scene,monkeypatch)
    p,rid,jid,gid,cap,first,lease=fixture;barrier=Barrier(2,timeout=10)
    def one():
        with Session(engine(),expire_on_commit=False) as db:
            attach(db,jid,lease);db.info['worker_actor']=p;barrier.wait()
            try:
                call=reserve_first(db,fixture);db.commit();return call
            except (plans.PlanAllowanceDenied,scoped.ScopedReservationDenied,HTTPException,
                    DBAPIError,IntegrityError,ExecutionUnauthorized,LeaseLost):
                db.rollback();return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:one(),range(2)))
    assert sum(x is not None for x in results)==1
    with Session(engine()) as db:
        grant=db.get(Job,gid);assert len(grant.data['consumed'])==1
        assert grant.data['consumed'][0]['call_id'] in results
        assert evidence._stage(db.get(SetupReportDraft,rid),first['stage_id'])['reserved_call_id']==grant.data['consumed'][0]['call_id']
        assert db.scalar(select(func.count()).select_from(CloudCall).where(
            CloudCall.workspace_id==p.workspace_id,CloudCall.data['scoped_extra']['job_id'].as_string()==jid))==1


def test_unsettled_first_stage_cannot_be_skipped(reading_scene,monkeypatch):
    from fastapi import HTTPException
    from generic_agents.leases import attach
    from generic_agents import cloud_scoped_extra_reservation as scoped
    from generic_agents.models import CloudCall
    p,rid,jid,gid,cap,first,lease=fixture=stage_fixture(reading_scene,monkeypatch)
    with Session(engine(),expire_on_commit=False) as db:
        attach(db,jid,lease);db.info['worker_actor']=p;first_call=reserve_first(db,fixture);db.commit()
    with Session(engine(),expire_on_commit=False) as db:
        attach(db,jid,lease);db.info['worker_actor']=p
        grant=db.get(Job,gid);assert len(grant.data['manifest']['stage_plan'])>=2
        second=grant.data['manifest']['stage_plan'][1]
        permit=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
            Job.idempotency_key==scoped.permit_key(jid,second['stage_id'])))
        with pytest.raises((plans.PlanAllowanceDenied,scoped.ScopedReservationDenied,HTTPException)):
            scoped.reserve_scoped_tx(db,p,permit.id,cap,second['token_bound'],second['request_hash'],second['options_hash'])
        db.rollback()
    with Session(engine()) as db:
        grant=db.get(Job,gid);assert len(grant.data['consumed'])==1 and grant.data['consumed'][0]['call_id']==first_call
        assert db.get(CloudCall,first_call).status=='reserved'
