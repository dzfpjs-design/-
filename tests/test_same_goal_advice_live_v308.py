"""Fixed live authority boundary, with explicit SQL/custody IO spies.

These checks do not establish real PostgreSQL lease or provider execution.
"""
import copy
from contextlib import nullcontext

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_live_v308 as s
from generic_agents import same_goal_advice_runtime_v308 as runtime
from tests.test_same_goal_advice_runtime_v308 import scene as runtime_scene


@pytest.fixture
def scene(monkeypatch):
    value=runtime_scene.__wrapped__(monkeypatch)
    # The inherited fixture explicitly spies original preparation custody.
    # Cover the new fixed raw reader at that same declared IO boundary;
    # execution.custody and the absence of financial IO remain actual code.
    monkeypatch.setattr(s.store,'_saved_base',s.store._saved)
    return value


def _owner(t):
    return s._owner(t.db,t.p,t.advice,t.inputs,t.key)


def test_actual_owned_context_and_lock_order(scene):
    t=scene;t.locks.clear()
    value=_owner(t)
    assert value['invocation'].id==t.call.id
    assert [kind for kind,locked in t.locks if locked]==[s.Task,s.Plan,s.Job,s.Invocation]


@pytest.mark.parametrize('change',['no_fence','wrong_token','expired','not_running','wrong_actor','wrong_key'])
def test_claims_do_not_supply_owner_authority(scene,change):
    t=scene
    if change=='no_fence':t.fenced=False
    elif change=='wrong_token':t.owner['lease_token']='other-owner'
    elif change=='expired':t.send_job.lease_until=t.clock
    elif change=='not_running':t.send_job.status='queued'
    elif change=='wrong_actor':t.send_job.data={**t.send_job.data,'actor_id':t.prep['data_actor_id']}
    elif change=='wrong_key':t.key+=':different'
    with pytest.raises(HTTPException):_owner(t)


def test_historical_paid_custody_ignores_revoked_current_reader_and_expiry(scene,monkeypatch):
    t=scene;t.allow_reader=False;t.fenced=False;t.send_job.lease_until=t.clock
    monkeypatch.setattr(s,'_live',lambda *args:pytest.fail('current authority is not paid custody'))
    result=s.historical(t.db,t.p,t.advice,t.inputs,t.key,t.call)
    assert result['start'].id==t.execution['start'].id
    assert result['snapshot']==t.execution['snapshot'] and not t.readers


def test_historical_checks_actual_sdk_inventory(scene):
    t=scene;alien=copy.copy(t.call);alien.id='foreign-original'
    with pytest.raises(HTTPException):s.historical(t.db,t.p,t.advice,t.inputs,t.key,alien)


def test_raw_custody_does_not_depend_on_financial_sibling_integrity(scene,monkeypatch):
    t=scene
    def money(*args,**kwargs):pytest.fail('money proof belongs after raw custody')
    monkeypatch.setattr(s.execution,'_paid_inventory',money)
    result=s.historical(t.db,t.p,t.advice,t.inputs,t.key,t.call)
    assert result['calls'][0].id==t.call.id


def test_existing_original_gateway_does_not_recheck_new_send_source(scene,monkeypatch):
    t=scene
    monkeypatch.setattr(s,'_live',lambda *args:pytest.fail('existing SDK must recover original only'))
    approval=s.execution.approval_for(t.plan,t.execution['snapshot'],t.send_job)
    s.authorize_gateway(t.db,t.p,t.advice,'business',t.inputs,t.key,approval)


@pytest.mark.parametrize('change',['role','approval','public_session'])
def test_gateway_json_cannot_grant_worker_authority(scene,change):
    t=scene;role='business';approval=s.execution.approval_for(t.plan,t.execution['snapshot'],t.send_job)
    if change=='role':role='data'
    if change=='approval':approval={**approval,'job_id':'unrelated'}
    if change=='public_session':t.fenced=False
    with pytest.raises(HTTPException):s.authorize_gateway(t.db,t.p,t.advice,role,t.inputs,t.key,approval)


def test_current_never_returns_context_without_fresh_live_checks(scene,monkeypatch):
    t=scene;seen=[]
    def live(db,p,cap,context):
        seen.append(context['job'].id)
        raise HTTPException(409,'current source IO refused')
    monkeypatch.setattr(s,'_live',live)
    with pytest.raises(HTTPException):s.current(t.db,t.p,t.advice,t.inputs,t.key)
    assert seen==[t.send_job.id]


def test_budget_scope_cannot_be_inferred_from_current_database_job(scene):
    t=scene
    with pytest.raises(HTTPException):s.spending_context(t.db,t.p,t.model,100,'0'*64)
