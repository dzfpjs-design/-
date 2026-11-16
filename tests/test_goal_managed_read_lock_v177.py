"""Actual Goal/managed-scope propagation with explicit dependency IO spies.

These are pure dispatch tests, not a substitute for the authoritative object
validator or a real REPEATABLE READ READ ONLY request. Root runs both layers.
The validator spy records its lock contract and can raise; it never supplies
an alternative successful object/source authorization proof.
"""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import data_tools, entity_release_compat, goals, native_goals
from generic_agents.models import Task


class ReachedQuery(Exception):
    pass


@pytest.fixture
def scene(monkeypatch):
    principal=NS(workspace_id='workspace-public',user_id='actor-public')
    task=Task(id='task-public',workspace_id=principal.workspace_id,package_id='package-public',
        data={'entity_ids':['entity-public'],'fields':['temperature'],
            'object_definition_bindings':{'entity-public':{}},
            'goal_spec':{'evidence':{'source':'observations'}},
            'goal_window':{'start':'2026-10-23T00:00:00+00:00'}})
    calls=[];evidence_calls=[];db=object();result={'public_synthetic_evidence':[]}
    entity=NS(schema_capability_id=task.package_id)

    def authoritative_boundary(actual_db,p,actual_task,*,lock_sources=True):
        assert actual_db is db and p is principal and actual_task is task
        calls.append(lock_sources)

    def scoped_entity(actual_db,model,identity,p):
        assert actual_db is db and p is principal and identity=='entity-public'
        assert model is data_tools.Entity
        return entity

    def query_evidence(actual_db,p,entity_ids,fields,**kwargs):
        assert actual_db is db and p is principal
        assert entity_ids==task.data['entity_ids'] and fields==task.data['fields']
        evidence_calls.append(kwargs)
        return result

    monkeypatch.setattr(entity_release_compat,'verify_task_objects',authoritative_boundary)
    monkeypatch.setattr(data_tools,'scoped',scoped_entity)
    monkeypatch.setattr(goals,'query_evidence',query_evidence)
    return NS(p=principal,task=task,db=db,calls=calls,evidence_calls=evidence_calls,
        entity=entity,result=result)


@pytest.mark.parametrize('mode',[None,True,False])
@pytest.mark.parametrize('same_package',[True,False])
def test_actual_goal_query_preserves_scope_checks_and_consumes_lock_option(scene,mode,same_package):
    case=scene
    if not same_package:case.entity.schema_capability_id='other-package'
    kwargs={} if mode is None else {'lock_sources':mode}
    assert goals.query_goal(case.db,case.p,case.task,learning=False,**kwargs) is case.result
    assert case.calls==[mode if mode is not None else True]
    query=case.evidence_calls[0]
    assert 'lock_sources' not in query and query['learning'] is False
    assert query['requirement']==case.task.data['goal_spec']['evidence']
    assert query['fixed_window']==case.task.data['goal_window']
    assert ('include_trusted_cases' not in query if same_package else query['include_trusted_cases'] is False)


@pytest.mark.parametrize('mode',[None,True,False])
def test_authoritative_source_failure_still_propagates_before_evidence_read(scene,monkeypatch,mode):
    case=scene
    failure=HTTPException(409,'original object/source integrity failure')
    def refused(*args,**kwargs):raise failure
    monkeypatch.setattr(entity_release_compat,'verify_task_objects',refused)
    with pytest.raises(HTTPException) as caught:
        goals.query_goal(case.db,case.p,case.task,**({} if mode is None else {'lock_sources':mode}))
    assert caught.value is failure and case.evidence_calls==[]


@pytest.mark.parametrize('mode',[None,True,False])
def test_native_assessment_explicitly_propagates_display_or_accept_lock_mode(scene,monkeypatch,mode):
    case=scene;seen=[]
    def guard(actual_db,p,task,*,lock_sources=True):
        assert actual_db is case.db and p is case.p and task is case.task
        seen.append(('guard',lock_sources))
    def query(actual_db,p,task,**kwargs):
        assert actual_db is case.db and p is case.p and task is case.task
        seen.append(('query',kwargs))
        raise ReachedQuery
    monkeypatch.setattr(native_goals,'guard',guard)
    monkeypatch.setattr(native_goals,'query_goal',query)
    with pytest.raises(ReachedQuery):
        native_goals.assessment(case.db,case.p,case.task,accepted=True,
            **({} if mode is None else {'lock_sources':mode}))
    expected=True if mode is None else mode
    assert seen==[('guard',expected),('query',{'learning':False,'lock_sources':expected})]


def test_read_only_managed_scope_still_rejects_cross_workspace_task_before_verification(scene):
    case=scene;case.task.workspace_id='other-workspace'
    with pytest.raises(HTTPException) as caught:
        goals.query_goal(case.db,case.p,case.task,lock_sources=False)
    assert caught.value.status_code==409 and case.calls==[] and case.evidence_calls==[]


def test_unmanaged_legacy_goal_keeps_old_query_contract_without_new_verification(scene):
    case=scene;case.task.data={k:v for k,v in case.task.data.items() if k!='object_definition_bindings'}
    assert goals.query_goal(case.db,case.p,case.task,lock_sources=False) is case.result
    assert case.calls==[] and 'include_trusted_cases' not in case.evidence_calls[0]
