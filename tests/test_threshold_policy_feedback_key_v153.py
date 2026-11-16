"""Actual historical-key helper with an explicit resolve boundary double.

The double is not Policy/Invocation provenance or real feedback acceptance.
Root separately owns the genuine successful Policy calls and feedback HTTP
journey. This file verifies exact dispatch to the completed-history helper and
its ordinary-record compatibility without SQL, current authority or new IO.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import db as database
from generic_agents import threshold_policy_execution_v153 as execution
from generic_agents.threshold_policy_store_v153 import PolicyInventoryError


WORKSPACE='153fed00-0000-4000-8000-000000000001'
POLICY='153fed00-0000-4000-8000-000000000002'
SELECTION='153fed00-0000-4000-8000-000000000003'
OBSERVATION='153fed00-0000-4000-8000-000000000004'
JOB='153fed00-0000-4000-8000-000000000005'
OTHER='153fed00-0000-4000-8000-000000000006'
KEY='threshold-policy-call:'+POLICY+':'+SELECTION


class NoDatabase:
    def __getattr__(self,name):
        raise AssertionError('Only the explicit resolve double may read persistence in this helper test: '+name)


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    import socket
    import subprocess
    import sqlalchemy
    def deny(*args,**kwargs):raise AssertionError('Historical key unit tests forbid external effects')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)
    monkeypatch.setattr(subprocess,'Popen',deny);monkeypatch.setattr(sqlalchemy,'create_engine',deny)
    monkeypatch.setattr(database,'engine',deny)


def objects(*,choice='candidate',status='completed',grant_status='active'):
    observation=SimpleNamespace(id=OBSERVATION,workspace_id=WORKSPACE,data={'extractor_id':'synthetic-chosen-cap'})
    job=SimpleNamespace(id=JOB,workspace_id=WORKSPACE,lease_token='later-lease',data={'attempt':8})
    row=SimpleNamespace(id=SELECTION,policy_id=POLICY,workspace_id=WORKSPACE,choice=choice,
        status=status,observation_id=OBSERVATION,job_id=JOB,data={'call_key':KEY})
    grant=SimpleNamespace(id=POLICY,status=grant_status)
    principal=SimpleNamespace(workspace_id=WORKSPACE,user_id=OTHER,role='viewer')
    return observation,job,row,grant,principal


def install_resolver(monkeypatch,db,p,observation,job,result=None,error=None):
    calls=[]
    def resolve(actual,actor,*,observation=None,job=None):
        assert actual is db and actor is p
        calls.append((observation,job))
        if error is not None:raise error
        return result
    monkeypatch.setattr(execution,'resolve',resolve)
    return calls


def test_ordinary_record_returns_none_after_resolving_both_actual_ids(monkeypatch):
    observation,job,_,_,p=objects();db=NoDatabase()
    calls=install_resolver(monkeypatch,db,p,observation,job)
    assert execution.historical_call_key(db,p,observation,job) is None
    assert calls==[(observation,job)],'Marker absence cannot skip the independent resolve inventory'


@pytest.mark.parametrize('choice',['candidate','baseline'])
@pytest.mark.parametrize('grant_status',['active','paused','rolled_back'])
def test_completed_candidate_and_baseline_return_original_fixed_key_without_current_authorization(
        monkeypatch,choice,grant_status):
    observation,job,row,grant,p=objects(choice=choice,grant_status=grant_status);db=NoDatabase()
    before=deepcopy((observation.__dict__,job.__dict__,row.__dict__,grant.__dict__))
    calls=install_resolver(monkeypatch,db,p,observation,job,(row,grant))
    assert execution.historical_call_key(db,p,observation,job)==KEY
    assert calls==[(observation,job)]
    assert before==(observation.__dict__,job.__dict__,row.__dict__,grant.__dict__)
    assert ':attempt:' not in KEY and job.lease_token=='later-lease'


@pytest.mark.parametrize('status',['queued','reserved','failed','stopped'])
def test_noncompleted_policy_never_falls_back_to_legacy_attempt_key(monkeypatch,status):
    observation,job,row,grant,p=objects(status=status);db=NoDatabase()
    calls=install_resolver(monkeypatch,db,p,observation,job,(row,grant))
    with pytest.raises(PolicyInventoryError) as failure:
        execution.historical_call_key(db,p,observation,job)
    assert failure.value.status_code==409 and calls==[(observation,job)]
    assert row.status==status and row.data['call_key']==KEY


@pytest.mark.parametrize('mismatch',['observation','job','both'])
def test_even_completed_selection_rejects_mismatched_actual_observation_or_job(monkeypatch,mismatch):
    observation,job,row,grant,p=objects();db=NoDatabase()
    if mismatch in {'observation','both'}:row.observation_id=OTHER
    if mismatch in {'job','both'}:row.job_id=OTHER
    calls=install_resolver(monkeypatch,db,p,observation,job,(row,grant))
    with pytest.raises(PolicyInventoryError) as failure:
        execution.historical_call_key(db,p,observation,job)
    assert failure.value.status_code==409 and calls==[(observation,job)]


@pytest.mark.parametrize('error',[PolicyInventoryError('Synthetic persisted inventory damaged'),
    HTTPException(409,'Synthetic independent binding conflict')],ids=['inventory','binding'])
def test_resolver_integrity_error_propagates_unchanged_instead_of_becoming_ordinary(monkeypatch,error):
    observation,job,_,_,p=objects();db=NoDatabase()
    calls=install_resolver(monkeypatch,db,p,observation,job,error=error)
    with pytest.raises(HTTPException) as failure:
        execution.historical_call_key(db,p,observation,job)
    assert failure.value is error and calls==[(observation,job)]
