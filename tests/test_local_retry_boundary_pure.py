"""Canonical HTTP retry dispatch, locked managed scope and saved nonce recovery."""
import copy
import importlib.util
import operator
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.routing import Match

from generic_agents import api, observation_retry_api as retry, instance_domain
from generic_agents.contracts import digest
from generic_agents.models import DomainInstance, Job, Observation

_spec=importlib.util.spec_from_file_location('_local_retry_image_fixture',Path(__file__).with_name('test_local_image_continuity_pure.py'))
_fixture=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_fixture)
managed_image=_fixture.managed_image
forbid_runtime=_fixture.forbid_runtime


@pytest.fixture
def attempt(managed_image,monkeypatch):
    w,original,source,cap,image=managed_image
    proof=instance_domain.current_context(w.db,w.p,w.entity)[2]
    data={**copy.deepcopy(original.data),'extractor_id':cap.id,'local_instance':{
        'id':w.instance.id,'release_id':w.r2.id,'entity_source_package_id':w.r1.package_id,
        'entity_definition_hash':digest(proof)}}
    row=Observation(id=original.id,workspace_id=w.p.workspace_id,source_id=original.source_id,
        source_record_id=original.source_record_id,processing_version=original.processing_version,
        entity_id=original.entity_id,business_day=original.business_day,observed_at=None,
        status='failed',version=2,data=data)
    job=NS(id='owned-extract-job',workspace_id=w.p.workspace_id,idempotency_key='extract:'+row.id,
        kind='process_local_observation',status='failed',attempts=1,data={'observation_id':row.id},
        lease_token='original-failed-lease')
    w.db.rows[Observation]={row.id:row};w.db.rows[Job]={job.id:job}
    w.db.locks=[]
    def scalar(query):
        model=query.column_descriptions[0]['entity']
        assert model in {Job,Observation}, 'pure retry adapter refuses unexpected SQL'
        found=list(w.db.rows[model].values())
        for term in query._where_criteria:
            assert term.operator is operator.eq
            found=[value for value in found if getattr(value,term.left.key)==term.right.value]
        assert query._for_update_arg is not None, 'retry must retain transactional row locks'
        w.db.locks.append(model)
        return found[0] if found else None
    w.db.scalar=scalar
    # The source, activation and actual definition verification stay real; only
    # model/file byte seams come from managed_image's isolated fixture.
    monkeypatch.setattr(retry,'scoped',w.db.scoped)
    w.row=row;w.job=job;w.cap=cap;w.legacy_calls=[]
    return w


def body(nonce='owned_retry_nonce_01',**changes):
    return retry.ScopedObservationRetry(expected_version=2,reason='原执行权限已恢复',request_id=nonce,**changes)


def stub_legacy(w,monkeypatch):
    def delegate(identity,payload,p,db):
        assert db is w.db and p is w.p and isinstance(payload,api.ObservationRetry)
        assert db.locks[:1]==[Job] and ('Observation',identity,True) in db.reads
        w.legacy_calls.append((identity,payload.model_dump(),copy.deepcopy(db.reads)))
        return {'delegated':True,'observation_id':identity}
    monkeypatch.setattr(api,'retry_observation',delegate)


def snapshot(w):
    return (copy.deepcopy(w.row.data),w.row.status,w.row.version,copy.deepcopy(vars(w.job)))


def test_real_app_first_matching_retry_route_is_guard_and_workspace_prefix_is_preserved():
    scope={'type':'http','path':'/api/observations/owned-id/retry','method':'POST','root_path':''}
    # This installed FastAPI preserves included routers as lazy branches. Use
    # their real effective contexts, in registration order, rather than assume
    # app.routes was flattened into APIRoute objects by include_router.
    expanded=[]
    for branch in api.app.routes:
        contexts=getattr(branch,'effective_route_contexts',None)
        expanded.extend(contexts() if contexts else [branch])
    matching=[route for route in expanded if route.matches(scope)[0] is Match.FULL]
    assert len(matching)==2 and matching[0].endpoint is retry.retry_observation_scoped
    assert matching[1].endpoint is api.retry_observation
    paths={(route.path,method) for route in expanded for method in getattr(route,'methods',())}
    assert ('/api/workspace/instances/{identity}/local-observations','POST') in paths
    assert ('/api/workspace/instances/{identity}/local-observation-requests/{request_id}','GET') in paths
    assert ('/api/workspace/tasks/{identity}','GET') in paths
    assert ('/instances/{identity}/local-observations','POST') not in paths


@pytest.mark.parametrize('nonce',[None,'new_retry_nonce_01'])
@pytest.mark.parametrize('failure',['paused','old_release'])
def test_new_retry_with_or_without_nonce_is_locked_and_rejected_before_delegation(attempt,monkeypatch,nonce,failure):
    w=attempt;stub_legacy(w,monkeypatch)
    if failure=='paused':w.instance.status='paused'
    else:w.row.data['local_instance']['release_id']=w.r1.id
    before=snapshot(w)
    with pytest.raises(HTTPException) as error:retry.retry_observation_scoped(w.row.id,body(nonce),w.p,w.db)
    assert error.value.status_code==409
    assert (DomainInstance.__name__,w.instance.id,True) in w.db.reads
    assert w.legacy_calls==[] and snapshot(w)==before and w.job.attempts==1


@pytest.mark.parametrize('nonce',[None,'new_retry_nonce_02'])
def test_valid_new_retry_holds_real_instance_lock_before_original_handler(attempt,monkeypatch,nonce):
    w=attempt;stub_legacy(w,monkeypatch);before=snapshot(w)
    assert retry.retry_observation_scoped(w.row.id,body(nonce),w.p,w.db)['delegated'] is True
    assert len(w.legacy_calls)==1
    assert ('DomainInstance',w.instance.id,True) in w.legacy_calls[0][2]
    assert snapshot(w)==before


@pytest.mark.parametrize('nonce',[None,'new_retry_nonce_03'])
def test_local_job_cannot_downgrade_into_generic_when_candidate_flag_is_missing(attempt,monkeypatch,nonce):
    w=attempt;stub_legacy(w,monkeypatch);w.row.data.pop('candidate_only');w.instance.status='paused'
    before=snapshot(w)
    with pytest.raises(HTTPException):retry.retry_observation_scoped(w.row.id,body(nonce),w.p,w.db)
    assert w.legacy_calls==[] and snapshot(w)==before


def test_retry_keeps_actual_iam_before_any_row_or_instance_read(attempt,monkeypatch):
    w=attempt;stub_legacy(w,monkeypatch);w.p.role='viewer';w.db.reads.clear()
    with pytest.raises(HTTPException) as error:retry.retry_observation_scoped(w.row.id,body(),w.p,w.db)
    assert error.value.status_code==403 and not w.db.reads and not w.db.locks and not w.legacy_calls


def test_generic_non_candidate_still_delegates_original_behavior(attempt,monkeypatch):
    w=attempt;stub_legacy(w,monkeypatch)
    w.row.data.pop('candidate_only');w.row.data.pop('local_instance');w.job.kind='process_observation'
    w.instance.status='paused';w.db.reads.clear()
    assert retry.retry_observation_scoped(w.row.id,body(None),w.p,w.db)['delegated'] is True
    assert not any(isinstance(value,tuple) and value[0]=='DomainInstance' for value in w.db.reads)


def save_nonce(w,payload):
    receipt={'request_id':payload.request_id,'request_hash':digest(payload.model_dump()),
        'actor_id':w.p.user_id,'original_version':2,'queued_version':3,'attempt':2}
    w.row.data.update(retry_requests=[receipt],retry_requests_hash=digest([receipt]))
    w.row.status='processed';w.row.version=4;w.job.status='succeeded';w.job.attempts=2


@pytest.mark.parametrize('change',[None,'actor','body'])
def test_saved_nonce_uses_frozen_original_handler_receipt_checks_without_new_scope(attempt,monkeypatch,change):
    w=attempt;payload=body();save_nonce(w,payload);w.instance.status='paused';w.db.reads.clear()
    legacy=api.retry_observation
    def delegate(identity,value,p,db):
        w.legacy_calls.append(value.model_dump())
        return legacy(identity,value,p,db)
    monkeypatch.setattr(api,'retry_observation',delegate)
    if change=='actor':w.p.user_id='different-operator'
    elif change=='body':payload=retry.ScopedObservationRetry(**{**payload.model_dump(),'reason':'更换了重试原因'})
    before=snapshot(w)
    if change:
        with pytest.raises(HTTPException) as error:retry.retry_observation_scoped(w.row.id,payload,w.p,w.db)
        assert error.value.status_code==409
    else:
        result=retry.retry_observation_scoped(w.row.id,payload,w.p,w.db)
        assert result['id']==w.row.id and result['status']=='processed' and result['version']==4
    assert len(w.legacy_calls)==1 and snapshot(w)==before
    assert not any(isinstance(value,tuple) and value[0]=='DomainInstance' for value in w.db.reads)


def test_tampered_saved_retry_journal_is_rejected_before_delegation(attempt,monkeypatch):
    w=attempt;payload=body();save_nonce(w,payload);stub_legacy(w,monkeypatch)
    w.row.data['retry_requests'][0]['queued_version']=99;before=snapshot(w)
    with pytest.raises(HTTPException) as error:retry.retry_observation_scoped(w.row.id,payload,w.p,w.db)
    assert error.value.status_code==409 and not w.legacy_calls and snapshot(w)==before


def test_retry_adapter_has_identical_frozen_wire_contract_and_rejects_rebinding_fields():
    payload={'expected_version':2,'reason':'原运行时已恢复','request_id':'owned_retry_nonce_04'}
    assert retry.ScopedObservationRetry(**payload).model_dump()==api.ObservationRetry(**payload).model_dump()
    assert retry.ScopedObservationRetry(**{**payload,'request_id':None}).model_dump()==api.ObservationRetry(**{**payload,'request_id':None}).model_dump()
    for model in (retry.ScopedObservationRetry,api.ObservationRetry):
        for key in ['entity_id','source_id','evidence_ids','extractor_id','release_id']:
            with pytest.raises(ValidationError):model(**payload,**{key:'replacement'})
