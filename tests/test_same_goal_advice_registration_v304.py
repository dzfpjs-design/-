"""Real fixed-contract checks; publication/model reads are explicit IO spies."""
import copy
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from generic_agents import same_goal_advice_registration_v304 as registration
from generic_agents import business_goal_ready_runtime_v288 as original
from generic_agents import gateway
from generic_agents.contracts import digest
from tests.test_same_goal_advice_contracts_v302 import scene


@pytest.mark.parametrize('fault',[None,'source_hash','validated_hash','release_version','instruction','model_denied'])
def test_fixed_derivative_still_reads_current_original_and_model_authority(monkeypatch,fault):
    s=scene();source=s.source;pin=s.original;manifest=copy.deepcopy(s.manifest)
    cap=NS(id=pin['id'],content_hash=pin['content_hash'],validated_hash=pin['content_hash'],
        release_version=pin['release_version'],data=source)
    calls=[];db=object();p=object();model=object()
    def published(actual,principal,identity,kind):
        assert actual is db and principal is p and identity==pin['id'] and kind=='tool'
        calls.append('published');return cap
    def check(actual,principal,data):
        assert actual is db and principal is p and data is source
        calls.append('model')
        if fault=='model_denied':raise HTTPException(403,'current model revoked')
        return model
    monkeypatch.setattr(registration,'published',published)
    monkeypatch.setattr(original,'validate_manifest',check)
    if fault=='source_hash':cap.content_hash='0'*64
    if fault=='validated_hash':cap.validated_hash='0'*64
    if fault=='release_version':cap.release_version='9.0.0'
    if fault=='instruction':manifest['specification']['instruction']='Changed without original source'
    if fault is None:
        assert registration.validate_manifest(db,p,manifest) is model and calls==['published','model']
    else:
        with pytest.raises(HTTPException):registration.validate_manifest(db,p,manifest)
        assert calls==(['published','model'] if fault=='model_denied' else ['published'])


def test_ordinary_gateway_without_installed_worker_fence_cannot_create_an_sdk(monkeypatch):
    from generic_agents import same_goal_advice_execution_v308 as execution
    from generic_agents import leases
    from generic_agents.models import Invocation,Job
    from generic_agents.security import Principal
    from tests.test_goal_evaluation_append_command_v297 import predicate
    from tests.test_same_goal_advice_execution_v308 import scene as start_scene
    # Real closed Start/Plan/Job checks run over declared original-source and
    # SQL IO fixtures. No authorize_gateway/_owner/fenced function is replaced.
    # The actual Principal has no installed transaction fence on this Session.
    value=start_scene.__wrapped__(monkeypatch);value.start();saved=value.checked()
    assert type(value.p) is Principal and not value.rows[Invocation]
    class ForbiddenSDK:
        info={}
        @property
        def no_autoflush(self):return value.db.no_autoflush
        def scalar(self,statement):
            model=statement.column_descriptions[0]['entity']
            if model is Job:
                rows=[row for row in value.rows[Job] if predicate(statement.whereclause,row)]
                assert len(rows)<=1
                return rows[0] if rows else None
            return value.db.scalar(statement)
        def scalars(self,statement):return value.db.scalars(statement)
        def add(self,row):pytest.fail('ordinary caller cannot create any SDK or execution artifact')
        def flush(self):pytest.fail('ordinary caller cannot flush a new SDK')
        def commit(self):pytest.fail('ordinary caller cannot commit an SDK')
    db=ForbiddenSDK();assert not leases.fenced(db) and leases.ownership(db)=={}
    monkeypatch.setattr(gateway,'published',lambda actual,principal,identity:value.advice)
    inputs=execution.input_for(value.plan,saved['snapshot'])
    approval=execution.approval_for(value.plan,saved['snapshot'],saved['job'])
    with pytest.raises(HTTPException) as error:
        gateway.invoke(db,value.p,value.advice.id,'business',inputs,saved['job'].idempotency_key,approval=approval)
    assert error.value.status_code==409 and not value.rows[Invocation]
