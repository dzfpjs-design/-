"""Runtime boundaries and independent numerical check; no database/provider IO."""
import copy
from types import SimpleNamespace as NS
from datetime import datetime, timezone, timedelta
from contextlib import nullcontext

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_continuation_execution_runtime_v314 as r
from generic_agents import same_goal_continuation_execution_budget_v314 as b
from generic_agents.contracts import digest


@pytest.fixture
def output_scene(monkeypatch):
    at = datetime(2026, 11, 2, tzinfo=timezone.utc)
    inputs = {'field': 'temperature', 'operation': 'mean', 'records': [
        {'entity_id': 'one', 'version': 2, 'attributes': {'temperature': 12}},
        {'entity_id': 'two', 'version': 3, 'attributes': {}},
        {'entity_id': 'three', 'version': 1, 'attributes': {'temperature': 18}}]}
    basis = {'inputs': inputs, 'input_hash': digest(inputs), 'capability_pin': {'content_hash': 'cap'}}
    job = NS(id='job')
    saved = {'plan': NS(id='plan'), 'job': job, 'start': NS(actor_id='executor'),
        'proposal': {'task_id': 'task', 'record_hash': 'submission', 'grant_ref': {'id': 'grant'},
            'grant': {'basis': basis}}}
    call = NS(id='sdk', status='succeeded', data={'lease_token': 'original-lease',
        'capability_snapshot': {'output_schema': {}}, 'output': {'field': 'temperature',
            'operation': 'mean', 'value': 15, 'sample_count': 2, 'missing_count': 1,
            'provenance': [{'entity_id': v['entity_id'], 'version': v['version']} for v in inputs['records']]}})
    dispatched = {'schema': r.DISPATCH_SCHEMA, 'executor_id': 'executor', 'task_id': 'task',
        'plan_id': 'plan', 'job_id': 'job', 'invocation_id': 'sdk', 'submission_hash': 'submission',
        'input_hash': basis['input_hash'], 'capability_hash': 'cap',
        'approval_hash': digest(r.approval(saved)), 'lease_token': 'original-lease',
        'dispatched_at': at.isoformat(), 'lease_until': (at + timedelta(minutes=5)).isoformat()}
    dispatched['record_hash'] = r.c.record_hash(dispatched)
    audit = NS(actor_id='executor', data=dispatched)
    outcome = NS(actor_id='executor', data={'status': 'succeeded'})
    monkeypatch.setattr(r, '_call_identity', lambda *args: basis)
    monkeypatch.setattr(r.store, '_rows', lambda *args: [audit] if any(
        getattr(getattr(clause,'right',None),'value',None)==r.DISPATCHED for clause in args[3:]) else [outcome])
    return NS(saved=saved, call=call, job=job, audit=audit, outcome=outcome,
        task=NS(id='task'), p=NS(workspace_id='workspace'), basis=basis)


def test_success_is_independently_recomputed_and_copied(output_scene):
    t=output_scene
    output=r.checked_output(None,t.p,t.task,t.saved,t.job,t.call)
    assert output['value']==15 and output['missing_count']==1
    output['value']=0
    assert t.call.data['output']['value']==15


@pytest.mark.parametrize('mutation', ['value','sample_count','missing_count','provenance','wrong_actor','wrong_lease','expired_dispatch','unknown'])
def test_success_status_does_not_replace_custody_and_correct_math(output_scene,mutation):
    t=output_scene
    if mutation=='wrong_actor':t.outcome.actor_id='another'
    elif mutation=='wrong_lease':t.call.data['lease_token']='recovery-lease'
    elif mutation=='expired_dispatch':
        t.audit.data['dispatched_at']=t.audit.data['lease_until'];t.audit.data['record_hash']=r.c.record_hash(t.audit.data)
    elif mutation=='unknown':t.call.status='unknown'
    else:t.call.data['output'][mutation]={'value':0,'sample_count':3,'missing_count':0,'provenance':[]}[mutation]
    with pytest.raises(HTTPException) as e:r.checked_output(None,t.p,t.task,t.saved,t.job,t.call)
    assert e.value.status_code==409


def test_no_lease_refuses_before_any_task_or_sdk_io(monkeypatch):
    monkeypatch.setattr(r.leases,'ownership',lambda db:None)
    monkeypatch.setattr(r.leases,'fenced',lambda db:False)
    monkeypatch.setattr(r,'_load',lambda *args,**kw:pytest.fail('unowned caller reached task'))
    with pytest.raises(HTTPException):r.authorize_gateway(None,None,None,'data',{},'key',{'schema':r.c.APPROVAL})


@pytest.mark.parametrize('remnant', ['dispatch','event','memory','output_pointer','receipt_pointer'])
def test_missing_sdk_with_original_effects_never_creates_replacement(monkeypatch,remnant):
    saved={'plan':NS(id='plan',data={'output_ref':None,'receipt_ref':None}),'job':NS(id='job')}
    if remnant.endswith('_pointer'):saved['plan'].data[remnant.replace('_pointer','_ref')]={'id':'original'}
    models={'dispatch':r.Audit,'event':r.Event,'memory':r.Memory}
    monkeypatch.setattr(r.store,'_rows',lambda db,p,model,*args:[NS(id='original')] if model is models.get(remnant) else [])
    with pytest.raises(HTTPException):r.no_original_effects(None,None,saved)


@pytest.mark.parametrize('calls,steps,own,allowed',[(10,10,None,True),(12,10,None,False),(10,12,None,False),(12,12,'plan',True)])
def test_cumulative_budget_counts_reservation_once_without_reset(monkeypatch,calls,steps,own,allowed):
    from generic_agents import native_goals,goal_queries
    task=NS(data={'goal_spec':{'max_tool_calls':12,'max_steps':12}})
    original=copy.deepcopy(task.data)
    monkeypatch.setattr(b,'_records',lambda *args:[{'id':'plan','charged':True}] if own else [])
    monkeypatch.setattr(native_goals,'total_steps',lambda *args:steps)
    monkeypatch.setattr(goal_queries,'call_count',lambda *args:calls)
    if allowed:
        result=b.check_new_action(None,None,task,own)
        assert result['used_calls']==calls and result['used_steps']==steps and not result['extra_goal_budget']
    else:
        with pytest.raises(HTTPException):b.check_new_action(None,None,task,own)
    assert task.data==original


def test_empty_operational_counter_does_not_query_or_change_legacy_task():
    task=NS(data={'goal_spec':{'max_tool_calls':12,'max_steps':12}})
    db=NS(no_autoflush=nullcontext())
    assert b.step_count(db,None,task)==b.additional_budget_calls(db,None,task)==0
