"""Recovery handoff with isolated PostgreSQL and locally captured fake HTTP."""
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import report_evidence_epoch_handoff as epoch
from generic_agents import report_evidence_pipeline as evidence
from generic_agents import report_evidence_prebound_transport as transport
from generic_agents import cloud_scoped_extra_reservation as scoped
from generic_agents.leases import attach
from generic_agents.cloud_report_plan_allowance import PlanAllowanceDenied
from generic_agents.db import engine
from generic_agents.models import AuthSession, CloudCall, Job, SetupReportDraft
from generic_agents.security import authenticated
from tests.test_cloud_report_plan_allowance_postgres import own_database, prepared_request
from tests.test_report_evidence_prebound_transport import prepared, _session
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_reading_source import full_source_scene


def recovered_fixture(prepared,reading_scene,monkeypatch,tmp_path):
    fixture,call_id,messages,options=prepared
    worker,rid,jid,gid,cap,first,lease=fixture
    monkeypatch.setattr(evidence,'_journal_root',lambda *args:tmp_path/'journals')
    with _session(fixture) as db:
        sink=transport.prepare_prebound_capture(db,worker,first['stage_id'],call_id,messages,options)
        try:
            witness=transport.commit_transport_intent(db,worker,first['stage_id'],call_id,messages,options,sink)
            witness.consume(worker,cap,first['stage_id'],call_id,messages,options,sink)
            payload={'choices':[{'message':{'content':json.dumps({'schema':'report-evidence-map-v3','claims':[]})},
                       'finish_reason':'stop'}], 'usage':{'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}}
            with sink:
                sink.mark_request_started();sink.begin_response(200,'','synthetic');sink.append(json.dumps(payload).encode());sink.finish(eof=True)
        finally:sink.close()
    with Session(engine()) as db:
        row=db.get(SetupReportDraft,rid);evidence._stop(db,worker,row,first['stage_id'],'test','synthetic interruption')
        job=db.get(Job,jid);job.status='failed';job.lease_until=None;revision=row.version;db.commit()
    response=reading_scene['client'].post('/api/setup/report-evidence-readings/'+rid+'/recover-saved',
        json={'revision':revision,'request_id':str(uuid4())})
    assert response.status_code==200,response.text
    request=prepared_request(reading_scene)
    with Session(engine()) as db:
        principal=authenticated(request,db);row=evidence.owned(db,principal,rid)
        cmd=evidence.ResumePreviewCommand(revision=row.version,request_id='fes_'+str(uuid4()))
        preview=evidence.resume_preview(row,principal,cmd,can_start=False)
        command=evidence.ResumeCommand(**cmd.model_dump(mode='json'),preview_hash=preview['preview_hash'],consent=True)
    return principal,request,command,fixture,call_id


def test_disabled_gate():
    with pytest.raises(PlanAllowanceDenied):epoch.handoff_tx(None,None,None,None,None)


def test_partial_recovery_requeues_same_job_without_changing_paid_records(prepared,reading_scene,monkeypatch,tmp_path):
    principal,request,command,fixture,call_id=recovered_fixture(prepared,reading_scene,monkeypatch,tmp_path)
    _,rid,jid,gid,_,_,old_lease=fixture
    monkeypatch.setattr(epoch,'_enabled',lambda:True)
    with Session(engine(),expire_on_commit=False) as db:
        grant_before=copy.deepcopy(db.get(Job,gid).data)
        out=epoch.handoff_tx(db,principal,request,rid,command);db.commit()
        assert out['job_id']==jid and out['new_cloud_calls']==0 and out['replayed'] is False
    # The handoff contract is persisted, but no separately authorized epoch
    # claim path is released yet. It must never masquerade as a first claim.
    from generic_agents.worker import claim
    from generic_agents.report_evidence_worker_isolation import KIND
    assert claim(job_id=jid,kinds=[KIND],workspace_id=principal.workspace_id) is None
    with Session(engine()) as db:
        replay=epoch.handoff_tx(db,principal,request,rid,command)
        assert replay['replayed'] is True and replay['epoch_id']==out['epoch_id']
    with Session(engine()) as db:
        job=db.get(Job,jid);row=evidence.owned(db,principal,rid)
        from generic_agents.report_evidence_worker_isolation import isolated
        assert isolated(job)
        assert job.status=='queued' and job.lease_token is None and job.lease_until is None
        assert row.status=='queued' and row.data['job_id']==jid
        assert db.get(Job,gid).data==grant_before
        from datetime import timedelta
        from generic_agents.models import now
        job.lease_token=old_lease
        with pytest.raises(PlanAllowanceDenied):epoch.assert_worker_epoch(db,principal,job,row)
        job.status='running';job.lease_token=str(uuid4());job.lease_until=now()+timedelta(minutes=5)
        assert job.lease_token!=old_lease
        assert len(epoch.assert_worker_epoch(db,principal,job,row))==2
        lease=job.lease_token;second=row.data['maps'][1]['chunk_id'];stage=evidence._stage(row,second)
        row.status='running';evidence._replace_stage(row,second,{**stage,'status':'running','inflight':True});db.commit()
    worker=fixture[0]
    with Session(engine(),expire_on_commit=False) as db:
        attach(db,jid,lease);db.info['worker_actor']=worker
        grant=db.get(Job,gid);entry=grant.data['manifest']['stage_plan'][1]
        permit=db.scalar(select(Job).where(Job.workspace_id==worker.workspace_id,
            Job.idempotency_key==scoped.permit_key(jid,entry['stage_id'])))
        second_call=scoped.reserve_scoped_tx(db,worker,permit.id,fixture[4],entry['token_bound'],
                                              entry['request_hash'],entry['options_hash']);db.commit()
    with Session(engine(),expire_on_commit=False) as db:
        attach(db,jid,lease);db.info['worker_actor']=worker
        row=db.get(SetupReportDraft,rid);messages=evidence._request(row,second)['messages']
        options=evidence._spec(row,second)['dispatch_options']
        sink=transport.prepare_prebound_capture(db,worker,second,second_call,messages,options)
        try:transport.commit_transport_intent(db,worker,second,second_call,messages,options,sink)
        finally:sink.close()


@pytest.mark.parametrize('mode',['active_lease','unknown','orphan_call','missing_capture','revoked','rollback','tamper_commit'])
def test_no_handoff_on_unverified_or_revoked_state(prepared,reading_scene,monkeypatch,tmp_path,mode):
    from datetime import timedelta
    from generic_agents.models import now
    from fastapi import HTTPException
    principal,request,command,fixture,call_id=recovered_fixture(prepared,reading_scene,monkeypatch,tmp_path)
    _,rid,jid,gid,_,_,_=fixture
    monkeypatch.setattr(epoch,'_enabled',lambda:True)
    if mode=='active_lease':
        with Session(engine()) as db:db.get(Job,jid).lease_until=now()+timedelta(minutes=5);db.commit()
    elif mode=='unknown':
        with Session(engine()) as db:db.get(CloudCall,call_id).status='reserved';db.commit()
    elif mode=='orphan_call':
        with Session(engine()) as db:
            db.add(CloudCall(workspace_id=principal.workspace_id,capability_id=fixture[4],status='reserved',
                reserved_tokens=1,charged_tokens=1,data={'scoped_extra':{'job_id':jid,'stage_id':'orphan'}}));db.commit()
    elif mode=='missing_capture':
        import shutil
        shutil.rmtree(tmp_path/'capture')
    elif mode=='revoked':
        with Session(engine()) as db:db.get(AuthSession,principal.session_id).revoked=True;db.commit()
    with Session(engine(),expire_on_commit=False) as db:
        before=copy.deepcopy(db.get(Job,gid).data)
        if mode in ('rollback','tamper_commit'):
            epoch.handoff_tx(db,principal,request,rid,command)
            if mode=='tamper_commit':
                job=db.get(Job,jid);job.data={**job.data,'delegated_epoch_hash':'0'*64}
                with pytest.raises(ValueError):db.commit()
        else:
            with pytest.raises((ValueError,OSError,HTTPException)):
                epoch.handoff_tx(db,principal,request,rid,command)
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Job,jid).status=='failed'
        assert db.get(Job,gid).data==before
        assert not db.scalars(select(Job).where(Job.kind=='report_evidence_epoch_v1')).all()


def test_concurrent_handoff_records_only_one_epoch(prepared,reading_scene,monkeypatch,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import HTTPException
    from sqlalchemy.exc import DBAPIError
    principal,request,command,fixture,_=recovered_fixture(prepared,reading_scene,monkeypatch,tmp_path)
    _,rid,jid,gid,_,_,_=fixture
    monkeypatch.setattr(epoch,'_enabled',lambda:True)
    def run():
        with Session(engine(),expire_on_commit=False) as db:
            try:
                result=epoch.handoff_tx(db,principal,request,rid,command);db.commit();return result
            except (ValueError,HTTPException,DBAPIError):
                db.rollback();return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:run(),range(2)))
    assert any(item is not None for item in results)
    assert len({item['epoch_id'] for item in results if item is not None})==1
    with Session(engine()) as db:
        assert len(db.scalars(select(Job).where(Job.kind=='report_evidence_epoch_v1')).all())==1
        assert db.get(Job,jid).status=='queued'
