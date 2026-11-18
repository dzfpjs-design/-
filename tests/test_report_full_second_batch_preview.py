"""Synthetic, isolated PG checks for the read-only second-batch preview."""
import copy
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Job, Membership, SetupDraft, SetupReportDraft
from generic_agents.report_full_second_batch_preview import preview
from generic_agents.security import Principal
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, read_db
from tests.test_report_full_reading_pipeline import reading_scene, narrow_extract_capacity
from tests.test_report_full_provisional import fixture


@pytest.fixture(autouse=True)
def require_owned_disposable_database(monkeypatch):
    import tests.test_report_full_reading_source as source_tests
    import tests.test_report_full_reading_pipeline as pipeline_tests
    def fenced():
        url=engine().url
        assert url.host=='127.0.0.1' and url.database.startswith('ga_test_')
    fenced()
    monkeypatch.setattr(source_tests,'fence',fenced)
    monkeypatch.setattr(pipeline_tests,'fence',fenced)
    if not inspect(engine()).has_table('report_full_provisional_candidates',schema='public'):
        from generic_agents.report_full_provisional_migration import install
        install(engine())


def scene_with_candidate(scene, monkeypatch):
    narrow_extract_capacity(scene,monkeypatch)
    provider,row,view,body,url=fixture(scene,monkeypatch)
    assert len(row['extractions'])==2
    saved=scene['client'].post(url,json=body)
    assert saved.status_code==200,saved.text
    return provider,row,saved.json()


def endpoint(row):
    return '/api/setup/report-full-readings/'+row['id']+'/stages/extract-2/resume-preview'


def get(scene,row):
    return scene['client'].post(endpoint(row),params={'revision':row['revision']})


def test_preview_is_private_scoped_and_has_no_effects(reading_scene,monkeypatch):
    scene=reading_scene;provider,row,saved=scene_with_candidate(scene,monkeypatch)
    with Session(engine()) as db:
        draft=db.get(SetupReportDraft,row['id']);before=(draft.version,draft.status,digest(draft.data))
        call=copy.deepcopy(db.get(CloudCall,provider.calls[0]).data)
        jobs=db.scalar(select(func.count()).select_from(Job));calls=db.scalar(select(func.count()).select_from(CloudCall))
    response=get(scene,row);assert response.status_code==200,response.text
    assert response.headers['cache-control']=='private, no-store'
    result=response.json();signed={k:v for k,v in result.items() if k!='preview_hash'}
    assert digest(signed)==result['preview_hash']
    assert result['eligible_for_future_resume'] is False
    assert {b['code'] for b in result['blockers']}=={'second_stage_not_sent_unproven'}
    assert result['first_candidate']['receipt_hash']==saved['receipt']['receipt_hash']
    assert result['first_candidate']['status']=='needs_review' and result['first_candidate']['confirmed'] is False
    assert result['scope']['stage']=='extract-2' and result['scope']['source_hash']==row['preview']['source_hash']
    assert all(result[k] is False for k in ('can_start','can_send','can_apply','can_publish','first_batch_confirmed'))
    assert result['requires_separate_authorization'] is True
    assert 'provider_wire_base64' not in json.dumps(result) and 'source_snapshot' not in result
    assert scene['client'].post(endpoint(row),params={'revision':row['revision']+1}).status_code==409
    with read_db() as db:
        p=scene['p']
        for other in (Principal(str(uuid4()),p.workspace_id,'admin',''),Principal(p.user_id,str(uuid4()),'admin','')):
            with pytest.raises(HTTPException) as denied:preview(db,other,row['id'],row['revision'])
            assert denied.value.status_code==404
    with Session(engine()) as db:
        draft=db.get(SetupReportDraft,row['id']);assert (draft.version,draft.status,digest(draft.data))==before
        assert db.get(CloudCall,provider.calls[0]).data==call
        assert db.scalar(select(func.count()).select_from(Job))==jobs
        assert db.scalar(select(func.count()).select_from(CloudCall))==calls
    assert len(provider.calls)==1


def test_budget_and_configuration_drift_are_explicit_blockers(reading_scene,monkeypatch):
    scene=reading_scene;provider,row,_=scene_with_candidate(scene,monkeypatch)
    monkeypatch.setattr(settings(),'cloud_daily_token_limit',1)
    with Session(engine()) as db:
        config=db.scalar(select(SetupDraft).where(SetupDraft.workspace_id==scene['p'].workspace_id))
        config.version+=1;db.commit()
    response=get(scene,row);assert response.status_code==200,response.text
    data=response.json();codes={b['code'] for b in data['blockers']}
    assert {'budget_insufficient','frozen_scope_changed'}<=codes
    assert data['eligible_for_future_resume'] is False and data['can_send'] is False
    assert data['budget']['available'] is False
    assert len(provider.calls)==1


def test_existing_related_call_and_live_original_job_block(reading_scene,monkeypatch):
    scene=reading_scene;provider,row,_=scene_with_candidate(scene,monkeypatch)
    with Session(engine()) as db:
        draft=db.get(SetupReportDraft,row['id']);job=db.get(Job,draft.data['job_id']);job.status='queued'
        first=db.get(CloudCall,provider.calls[0]);batch=draft.data['preview']['batches'][1]
        db.add(CloudCall(workspace_id=scene['p'].workspace_id,capability_id=first.capability_id,
            status='reserved',reserved_tokens=1,charged_tokens=1,
            data={'actor_id':scene['p'].user_id,'request_hash':batch['messages_hash']}));db.commit()
    response=get(scene,row);assert response.status_code==200,response.text
    data=response.json();codes={b['code'] for b in data['blockers']}
    assert {'ledger_ambiguous','original_job_unverified'}<=codes
    assert data['eligible_for_future_resume'] is False and len(provider.calls)==1


def test_missing_saved_candidate_blocks_without_pretending_first_batch_success(reading_scene,monkeypatch):
    from tests.test_report_full_failed_candidate_view import fail_after_paid_checkpoint
    scene=reading_scene;narrow_extract_capacity(scene,monkeypatch)
    provider,row=fail_after_paid_checkpoint(scene,monkeypatch)
    response=get(scene,row);assert response.status_code==200,response.text
    data=response.json();assert 'first_candidate_required' in {b['code'] for b in data['blockers']}
    assert data['first_candidate'] is None and data['first_batch_confirmed'] is False
    assert data['eligible_for_future_resume'] is False and len(provider.calls)==1


def test_preview_keeps_generic_map_policy_gate(reading_scene,monkeypatch):
    scene=reading_scene;provider,row,_=scene_with_candidate(scene,monkeypatch)
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==scene['p'].workspace_id,
            Membership.user_id==scene['p'].user_id));member.role='operator'
        db.add(Job(workspace_id=scene['p'].workspace_id,kind='map_business_access_policy',status='internal',
            idempotency_key='map-business-access-policy-v1',data={'businesses':{}}));db.commit()
    assert get(scene,row).status_code==403
    assert len(provider.calls)==1


def test_original_failure_history_and_existing_resume_block(reading_scene,monkeypatch):
    scene=reading_scene;provider,row,_=scene_with_candidate(scene,monkeypatch)
    with Session(engine()) as db:
        audit=db.scalar(select(Audit).where(Audit.object_id==row['id'],
            Audit.action=='setup.report_full_reading.failed'))
        audit.data={**audit.data,'stage':'extract-2'}
        db.add(Job(workspace_id=scene['p'].workspace_id,kind='synthetic_resume',status='failed',
            idempotency_key='report-full-resume:'+row['id']+':extract-2',data={}));db.commit()
    response=get(scene,row);assert response.status_code==200,response.text
    codes={b['code'] for b in response.json()['blockers']}
    assert {'original_history_unverified','resume_job_exists'}<=codes
    assert response.json()['eligible_for_future_resume'] is False
    assert len(provider.calls)==1


def test_other_members_call_details_do_not_leak_through_preview(reading_scene,monkeypatch):
    scene=reading_scene;provider,row,_=scene_with_candidate(scene,monkeypatch)
    other_user=str(uuid4());other_hash='f'*64
    with Session(engine()) as db:
        first=db.get(CloudCall,provider.calls[0])
        unrelated=CloudCall(workspace_id=scene['p'].workspace_id,capability_id=first.capability_id,
            status='reserved',reserved_tokens=1,charged_tokens=1,
            data={'actor_id':other_user,'request_hash':other_hash})
        db.add(unrelated);db.flush();other_call=unrelated.id;db.commit()
    response=get(scene,row);assert response.status_code==200,response.text
    result=response.json();serialized=json.dumps(result,ensure_ascii=False)
    assert result['ledger_clear'] is False
    assert 'ledger_ambiguous' in {b['code'] for b in result['blockers']}
    assert 'ledger_inventory_hash' not in result
    assert all(secret not in serialized for secret in (other_user,other_hash,other_call))
    assert result['can_send'] is False and len(provider.calls)==1
