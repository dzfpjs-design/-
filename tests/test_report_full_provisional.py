"""Synthetic private PG/API coverage; never accesses an actual report or model."""
import copy
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect, select, func
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Membership, SetupDraft, SetupReportDraft, User
from generic_agents.report_full_provisional import ALGORITHM, SaveCommand, discover, save
from generic_agents.report_full_provisional_models import FullProvisionalCandidate
from generic_agents.security import Principal
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_failed_candidate_view import fail_after_paid_checkpoint, route


@pytest.fixture(autouse=True)
def require_owned_disposable_database(monkeypatch):
    import tests.test_report_full_reading_source as source_tests
    import tests.test_report_full_reading_pipeline as pipeline_tests
    def fenced():
        url = engine().url
        assert url.host == '127.0.0.1' and url.database.startswith('ga_test_')
    fenced()
    monkeypatch.setattr(source_tests, 'fence', fenced)
    monkeypatch.setattr(pipeline_tests, 'fence', fenced)
    if not inspect(engine()).has_table('report_full_provisional_candidates', schema='public'):
        from generic_agents.report_full_provisional_migration import install
        install(engine())


def fixture(scene, monkeypatch):
    provider,row=fail_after_paid_checkpoint(scene,monkeypatch)
    preview=scene['client'].get(route(row),params={'revision':row['revision']})
    assert preview.status_code == 200, preview.text
    preview=preview.json()
    body={'revision':row['revision'],'request_id':str(uuid4()), 'raw_hash':preview['scope']['raw_hash'],
          'source_hash':row['preview']['source_hash'], 'view_hash':preview['view_hash'], 'algorithm_version':preview['algorithm_version']}
    url='/api/setup/report-full-readings/'+row['id']+'/stages/extract-1/provisional'
    return provider,row,preview,body,url


def test_save_replay_and_new_process_read_are_durable(reading_scene, account, monkeypatch):
    scene=reading_scene;provider,row,preview,body,url=fixture(scene,monkeypatch)
    with Session(engine()) as db:
        draft=db.get(SetupReportDraft,row['id']); original=(draft.version,draft.status,digest(draft.data))
        call_before=copy.deepcopy(db.get(CloudCall,provider.calls[0]).data)
    response=scene['client'].post(url,json=body)
    assert response.status_code==200, response.text
    assert response.headers['cache-control']=='private, no-store'
    saved=response.json();assert saved['candidate']==preview['candidate'] and saved['quarantine']==preview['quarantine']
    assert saved['confirmed'] is saved['can_apply'] is saved['can_publish'] is False
    assert scene['client'].post(url,json=body).json()==saved
    changed={**body,'view_hash':'0'*64}
    assert scene['client'].post(url,json=changed).status_code==409
    receipt='/api/setup/report-full-readings/'+row['id']+'/provisional-receipts/'+body['request_id']
    assert scene['client'].get(receipt).json()==saved
    found=scene['client'].get(url)
    assert found.status_code==200 and found.headers['cache-control']=='private, no-store'
    assert [item['receipt']['request_id'] for item in found.json()['items']]==[body['request_id']]
    assert 'candidate' not in found.json()['items'][0]
    with Session(engine()) as db:
        p=scene['p']
        for other in (Principal(str(uuid4()),p.workspace_id,'admin',''), Principal(p.user_id,str(uuid4()),'admin','')):
            with pytest.raises(HTTPException) as denied:discover(db,other,row['id'],'extract-1')
            assert denied.value.status_code==404
    with Session(engine()) as db:
        draft=db.get(SetupReportDraft,row['id']);assert (draft.version,draft.status,digest(draft.data))==original
        assert db.get(CloudCall,provider.calls[0]).data==call_before
        assert db.scalar(select(func.count()).select_from(FullProvisionalCandidate))==1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action=='setup.report_full_reading.provisional_saved'))==1
    # New Python process and fresh connection, without running any migration.
    env=os.environ.copy();env['GA_DATABASE_URL']=engine().url.render_as_string(hide_password=False)
    code='''import json,sys\nfrom fastapi.testclient import TestClient\nfrom generic_agents.api import app\na=json.load(sys.stdin)\nwith TestClient(app) as c:\n r=c.post('/api/login',json={'username':a['user'],'password':a['password']}); assert r.status_code==200\n c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace']})\n r=c.get(a['list_path']); assert r.status_code==200\n items=r.json()['items']; assert len(items)==1\n r=c.get(a['receipt_prefix']+items[0]['receipt']['request_id']); assert r.status_code==200\n assert r.json()['receipt']['receipt_hash']==a['receipt']\n'''
    payload={'user':account[0],'password':account[1],'workspace':account[2], 'list_path':url,
             'receipt_prefix':'/api/setup/report-full-readings/'+row['id']+'/provisional-receipts/',
             'receipt':saved['receipt']['receipt_hash']}
    result=subprocess.run([os.sys.executable,'-c',code],input=json.dumps(payload),text=True,capture_output=True,env=env,timeout=60)
    assert result.returncode==0, result.stderr[-2000:]
    assert len(provider.calls)==1


def test_scope_cas_and_invalid_checkpoint_fail_closed(reading_scene, monkeypatch):
    scene=reading_scene;provider,row,preview,body,url=fixture(scene,monkeypatch)
    for key in ('revision','raw_hash','source_hash','view_hash'):
        changed={**body,key:body[key]+1 if key=='revision' else '0'*64}
        assert scene['client'].post(url,json=changed).status_code==409
    with Session(engine()) as db:
        p=scene['p']
        for other in (Principal(str(uuid4()),p.workspace_id,'admin',''), Principal(p.user_id,str(uuid4()),'admin','')):
            with pytest.raises(HTTPException): save(db,other,row['id'],'extract-1',SaveCommand.model_validate(body))
            db.rollback()
    with Session(engine()) as db:
        draft=db.get(SetupReportDraft,row['id']);data=copy.deepcopy(draft.data)
        data['extractions'][0]['raw_response']=None;draft.data=data;db.commit()
    assert scene['client'].post(url,json=body).status_code==409
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(FullProvisionalCandidate))==0
    assert len(provider.calls)==1


def test_concurrent_save_single_record_and_audit(reading_scene, monkeypatch):
    scene=reading_scene;provider,row,preview,body,url=fixture(scene,monkeypatch)
    barrier=threading.Barrier(2)
    def worker(n):
        command={**body,'request_id':str(uuid4())}
        barrier.wait(timeout=10)
        response=scene['client'].post(url,json=command)
        if response.status_code==200:return ('saved',response.json()['receipt']['receipt_hash'])
        assert response.status_code==409
        return ('conflict',response.json()['detail']['code'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker,range(2)))
    assert sorted(r[0] for r in results)==['conflict','saved']
    assert next(r[1] for r in results if r[0]=='conflict')=='full_provisional_already_saved'
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(FullProvisionalCandidate))==1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action=='setup.report_full_reading.provisional_saved'))==1
    assert len(provider.calls)==1


def test_saved_record_and_original_are_checked_on_reopen(reading_scene, monkeypatch):
    scene=reading_scene;provider,row,preview,body,url=fixture(scene,monkeypatch)
    assert scene['client'].post(url,json=body).status_code==200
    receipt='/api/setup/report-full-readings/'+row['id']+'/provisional-receipts/'+body['request_id']
    with Session(engine()) as db:
        record=db.scalar(select(FullProvisionalCandidate));data=copy.deepcopy(record.data)
        data['candidate']['domain']='synthetic tampering';record.data=data;db.commit()
    assert scene['client'].get(receipt).status_code==409
    assert len(provider.calls)==1


def test_rehashed_candidate_cannot_forge_original_preview(reading_scene, monkeypatch):
    scene=reading_scene;provider,row,preview,body,url=fixture(scene,monkeypatch)
    assert scene['client'].post(url,json=body).status_code==200
    receipt='/api/setup/report-full-readings/'+row['id']+'/provisional-receipts/'+body['request_id']
    with Session(engine()) as db:
        record=db.scalar(select(FullProvisionalCandidate));data=copy.deepcopy(record.data)
        data['candidate']['domain']='synthetic forgery'
        data['receipt']['candidate_hash']=digest(data['candidate'])
        data['receipt']['receipt_hash']=digest({k:v for k,v in data['receipt'].items() if k!='receipt_hash'})
        record.data=data;db.commit()
    assert scene['client'].get(receipt).status_code==409
    assert scene['client'].get(url).status_code==409
    assert len(provider.calls)==1


def test_saved_candidate_cannot_be_read_after_ledger_tampering(reading_scene, monkeypatch):
    scene=reading_scene;provider,row,preview,body,url=fixture(scene,monkeypatch)
    assert scene['client'].post(url,json=body).status_code==200
    receipt='/api/setup/report-full-readings/'+row['id']+'/provisional-receipts/'+body['request_id']
    with Session(engine()) as db:
        call=db.get(CloudCall,provider.calls[0]);call.data={**call.data,'actor_id':str(uuid4())};db.commit()
    assert scene['client'].get(receipt).status_code==409
    assert len(provider.calls)==1


def test_historical_algorithm_remains_readable_after_new_writer_version(reading_scene, monkeypatch):
    from generic_agents import report_full_provisional as provisional
    scene=reading_scene;provider,row,preview,body,url=fixture(scene,monkeypatch)
    saved=scene['client'].post(url,json=body);assert saved.status_code==200
    original=saved.json()
    monkeypatch.setattr(provisional,'ALGORITHM','future-writer-test-v2')
    path='/api/setup/report-full-readings/'+row['id']+'/provisional-receipts/'+body['request_id']
    assert scene['client'].get(path).json()==original
    assert scene['client'].post(url,json=body).json()==original
    assert scene['client'].get(url).json()['items'][0]['receipt']==original['receipt']
    new_request={**body,'request_id':str(uuid4())}
    rejected=scene['client'].post(url,json=new_request)
    assert rejected.status_code==409 and rejected.json()['detail']['code']=='full_provisional_algorithm_changed'
    assert len(provider.calls)==1


def test_unknown_saved_algorithm_fails_closed(reading_scene, monkeypatch):
    scene=reading_scene;provider,row,preview,body,url=fixture(scene,monkeypatch)
    assert scene['client'].post(url,json=body).status_code==200
    with Session(engine()) as db:
        record=db.scalar(select(FullProvisionalCandidate));data=copy.deepcopy(record.data)
        record.algorithm_version='unknown-future-algorithm'
        receipt=data['receipt'];receipt['algorithm_version']=record.algorithm_version
        receipt['receipt_hash']=digest({k:v for k,v in receipt.items() if k!='receipt_hash'})
        record.data=data;db.commit()
    path='/api/setup/report-full-readings/'+row['id']+'/provisional-receipts/'+body['request_id']
    assert scene['client'].get(path).status_code==409
    assert scene['client'].get(url).status_code==409
    assert len(provider.calls)==1


@pytest.mark.parametrize('revocation', ['session','user','membership'])
def test_revocation_while_waiting_for_workspace_prevents_save(reading_scene, monkeypatch, revocation):
    from generic_agents import report_reading_drafts as legacy
    scene=reading_scene;provider,row,preview,body,url=fixture(scene,monkeypatch)
    entered=threading.Event(); original=legacy.workspace_draft
    def observed(db,p,lock=True):
        entered.set()
        return original(db,p,lock=lock)
    monkeypatch.setattr(legacy,'workspace_draft',observed)
    with Session(engine()) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.scalar(select(SetupDraft).where(SetupDraft.workspace_id==scene['p'].workspace_id).with_for_update())
        pending=pool.submit(scene['client'].post,url,json=body)
        assert entered.wait(10)
        with Session(engine()) as db:
            if revocation=='session':
                row_session=db.scalar(select(AuthSession).where(AuthSession.user_id==scene['p'].user_id,
                    AuthSession.revoked.is_(False)).order_by(AuthSession.created_at.desc()))
                row_session.revoked=True
            elif revocation=='user':db.get(User,scene['p'].user_id).active=False
            else:
                member=db.scalar(select(Membership).where(Membership.user_id==scene['p'].user_id,
                    Membership.workspace_id==scene['p'].workspace_id));member.role='viewer'
            db.commit()
        blocker.commit()
        response=pending.result(timeout=20)
    assert response.status_code in (401,403)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(FullProvisionalCandidate))==0
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action=='setup.report_full_reading.provisional_saved'))==0
    assert len(provider.calls)==1
