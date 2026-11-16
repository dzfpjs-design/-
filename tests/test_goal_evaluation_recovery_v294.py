"""Original-Qe recovery orchestration with explicit source/lease/SQL IO seams.

Actual store materialization, immutable record/typed output and terminal hash
checks run. Frozen source, repair, IAM and lease ownership are supplied IO,
not PG, live permission or real worker evidence. Root runs the actual journey.
"""
import copy
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import workflow_goal_evaluation_recovery_v294 as subject
from generic_agents import workflow_goal_evaluation_store_v288 as store
from generic_agents import workflow_goal_evaluation_query_repair_v296 as repair
from generic_agents import evidence as evidence_module
from generic_agents.contracts import digest
from tests.test_workflow_goal_evaluation_store_v288 import runtime, presentation, CUT, uid


def recovery_scene(s, monkeypatch):
    s.call.status='unknown';s.call.workspace_id=s.p.workspace_id;s.call.capability_id=s.cap.id
    s.call.data['capability_snapshot']={'execution':{'adapter':'workflow_service',
        'service_actor':'reader_service','operation':subject.ports.OP,'side_effect':True}}
    s.cap.workspace_id=s.p.workspace_id;s.job.workspace_id=s.p.workspace_id
    node={'id':'evaluation_packet','actor_id':s.p.user_id}
    initial=NS(id=uid(91),actor_id=s.p.user_id,data={'status':'unknown'})
    binding={'binding_hash':s.record['scope']['source_binding_hash']}
    context={'task':s.task,'job':s.job,'call':s.call,'cap':s.cap,'node':node,
        'inputs':s.inputs,'approval':s.approval,'initial':initial,'binding':binding}
    frozen=store.queries.checked_frozen(s.db,s.p,s.task,uid(90),evaluation_id=s.call.id)
    retained={'audit':NS(data={'query_ref':copy.deepcopy(frozen['query_ref'])})}
    checks=[]
    def base(*args):checks.append('actual_original_scope_IO');return context
    monkeypatch.setattr(subject,'_base',base)
    monkeypatch.setattr(subject,'_fence',lambda *args:checks.append('actual_current_lease_IO'))
    monkeypatch.setattr(subject,'_retained',lambda *args:(retained,{'explicit_upstream_IO':True}))
    def restore(*args):checks.append('repair_same_original_query_IO');return copy.deepcopy(frozen)
    monkeypatch.setattr(repair,'restore_result',restore)
    @contextmanager
    def nested():
        before=copy.deepcopy((s.created,s.events,s.queued,s.call.data))
        checks.append('savepoint_enter')
        try:yield
        except BaseException:
            s.created[:],s.events[:],s.queued[:],s.call.data=before
            checks.append('savepoint_rollback');raise
        else:checks.append('savepoint_release')
    monkeypatch.setattr(type(s.db),'begin_nested',lambda actual:nested(),raising=False)
    return NS(s=s,context=context,frozen=frozen,checks=checks,node=node,binding=binding,initial=initial)


def resume(c):
    s=c.s
    return subject.resume(s.db,s.p,{'task':s.task,'job':s.job},s.call,s.cap,s.inputs,s.approval)


def terminal(c,monkeypatch):
    s=c.s;output=resume(c)
    s.call.status='succeeded';s.call.data.update(output=copy.deepcopy(output),service_previous_status='unknown')
    events=[];original_add=s.db.add
    def add(row):
        if type(row) is subject.Audit:events.append(row)
        else:original_add(row)
    monkeypatch.setattr(s.db,'add',add)
    monkeypatch.setattr(store.queries,'_fresh',lambda db,p,cls,identity:s.created[0])
    monkeypatch.setattr(evidence_module,'evidence_bytes',lambda *args:(s.evidence,s.content))
    def audit(db,p,action,identity,actor,expected=None):
        if action==store.CREATED:row=s.events[0]
        elif action=='evidence.upload':
            row=NS(actor_id=s.p.user_id,data={'media_type':'text/plain',
                'sha256':s.evidence.sha256,'cloud_model_id':None,'consent':False})
        elif action=='workflow.service.receipt_verified':
            row=NS(actor_id=s.p.user_id,data={'job_id':s.job.id,'activation_id':s.job.data['activation_id']})
        else:
            assert action==subject.TERMINAL and len(events)==1;row=events[0]
        assert row.actor_id==actor
        if expected is not None:subject.need(digest(row.data)==digest(expected))
        return row
    monkeypatch.setattr(subject,'_audit',audit)
    monkeypatch.setattr(subject,'_rows',lambda *args:[])
    def flush():c.checks.append('flush_pending_normal_success')
    monkeypatch.setattr(s.db,'flush',flush)
    assert subject.record_terminal_parent(s.db,s.p,s.task,s.job,s.call,s.cap,c.node,c.binding,s.inputs,s.approval)
    return output,events


def test_positive_original_query_finishes_same_sdk_and_real_materializer_without_new_query(runtime,monkeypatch):
    c=recovery_scene(runtime,monkeypatch);s=c.s;original=s.call.id
    result=resume(c)
    assert result['evaluation_ref']['id']==original==s.created[0].id
    assert s.created[0].data['cut_at']==CUT and s.queries==0
    assert len(s.created)==len(s.events)==len(s.queued)==1
    assert s.call.status=='unknown' and c.initial.data=={'status':'unknown'}
    assert c.checks.index('savepoint_enter')<c.checks.index('repair_same_original_query_IO')


def test_materialization_failure_rolls_back_partial_database_phase(runtime,monkeypatch):
    c=recovery_scene(runtime,monkeypatch);actual=store.materialize_frozen_v294
    def fail(*args):actual(*args);raise HTTPException(409,'explicit local save failure IO')
    monkeypatch.setattr(store,'materialize_frozen_v294',fail)
    with pytest.raises(HTTPException):resume(c)
    assert not runtime.created and not runtime.events and not runtime.queued
    assert store.KEY not in runtime.call.data and runtime.call.status=='unknown'
    assert 'savepoint_rollback' in c.checks and runtime.queries==0


@pytest.mark.parametrize('fault',[None,'content_drift'])
def test_terminal_original_unknown_proof_survives_later_consent_and_job_reclaim(runtime,monkeypatch,fault):
    c=recovery_scene(runtime,monkeypatch);output,events=terminal(c,monkeypatch);s=c.s
    assert c.checks.index('flush_pending_normal_success')<len(c.checks)-1
    before=copy.deepcopy(events[0].data)
    s.evidence.data['cloud_model_ids']=[uid(92)]  # Later real consent is independently authorized.
    s.job.lease_token=uid(93)
    if fault:s.content+=b'changed'
    arguments=(s.db,s.p,s.task,s.job,s.call,s.cap,{'output':output},NS(),NS(),c.node)
    if fault:
        with pytest.raises(HTTPException):subject.verify_terminal_parent(*arguments)
    else:
        subject.verify_terminal_parent(*arguments)
        assert events[0].data==before and c.initial.data=={'status':'unknown'}


@pytest.mark.parametrize('call',[None,NS(status='unknown',data={}),NS(status='succeeded',data={})])
def test_no_independent_retained_source_never_reexecutes_unknown(call):
    with pytest.raises(HTTPException,match='不重跑查询'):
        subject.resume(NS(),NS(),{},call,NS(),{}, {})


@pytest.mark.parametrize('conflict',[None,'evaluation','evidence','created_audit','goal_job','gap','extra_sdk'])
def test_complete_effect_inventory_never_uses_eval_zero_alone(monkeypatch,conflict):
    from generic_agents.temporal import instant
    p=NS(workspace_id=uid(1));call=NS(id=uid(2),idempotency_key='same-original',data={})
    context={'task':NS(id=uid(3)),'job':NS(id=uid(4)),'call':call,
        'initial':NS(created_at=instant(CUT))}
    retained={'row':NS(id=uid(5)),'marker':{'cut_checked_at':CUT}}
    seen=[]
    def rows(db,principal,model,*conditions):
        assert principal is p and conditions;seen.append(model)
        if model is subject.Invocation:return [call,NS(id=uid(6))] if conflict=='extra_sdk' else [call]
        selected={'evaluation':subject.GoalEvaluationV288,'evidence':subject.Evidence,
            'goal_job':subject.Job,'gap':subject.DataGap}.get(conflict)
        if model is selected:return [NS(id=uid(7))]
        if conflict=='created_audit' and model is subject.Audit and seen.count(subject.Audit)==1:return [NS(id=uid(8))]
        return []
    monkeypatch.setattr(subject,'_rows',rows)
    if conflict:
        with pytest.raises(HTTPException):subject._effects(NS(),p,context,retained)
    else:subject._effects(NS(),p,context,retained)
    assert subject.GoalEvaluationV288 in seen and subject.Evidence in seen


def test_inventory_queries_bound_workspace_and_overflow_is_not_absence():
    p=NS(workspace_id=uid(1));seen=[]
    class DB:
        def scalars(self,statement):
            values=statement.compile().params
            assert p.workspace_id in values.values() and subject.MAX_ROWS+1 in values.values()
            seen.append(statement);return [NS() for _ in range(subject.MAX_ROWS+1)]
    with pytest.raises(HTTPException):subject._rows(DB(),p,subject.Evidence)
    assert len(seen)==1
