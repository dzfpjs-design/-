"""Synthetic query authorization/provenance tests; no DB, network or provider."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import data_tools, data_requirements, native_query_catalog
from generic_agents.contracts import DataRequest, digest
from generic_agents.models import Task, Entity, DataRun


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('DB, network and subprocess are forbidden in query pure tests')
    import socket
    import subprocess
    import sqlalchemy
    from generic_agents import db
    monkeypatch.setattr(db, 'engine', forbidden)
    monkeypatch.setattr(sqlalchemy, 'create_engine', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def fixture_scope(monkeypatch, *, proof=True, source='source-package'):
    from generic_agents import entity_release_compat as compat
    principal=SimpleNamespace(workspace_id='workspace',user_id='operator',role='operator',session_id='session')
    entities=[Entity(id='original-'+str(i),workspace_id='workspace',namespace='same-instance',
        schema_capability_id=source,entity_type='sample',external_id=str(i),version=i+4,data={'amount':value})
        for i,value in enumerate([20,28])]
    task=Task(id='saved-task',workspace_id='workspace',owner_id='operator',package_id='target-package',
        data={'instance_id':'instance','instance_release_id':'target-release','business_id':'business',
            'entity_ids':[row.id for row in entities], 'workflow_runtime':{'current_step_id':'human'}})
    if proof:
        task.data.update(object_definition_bindings={'server':'proof'},object_definition_bindings_hash='proof-hash')
    checks=[]
    monkeypatch.setattr(compat,'verify_task_objects',lambda db,p,saved,release=None:checks.append(saved.id))
    def scoped(db,model,identity,p,lock=False):
        if model is Entity:return next(row for row in entities if row.id==identity)
        raise AssertionError('unexpected row '+model.__name__)
    monkeypatch.setattr(data_tools,'scoped',scoped)
    return principal,task,entities,checks


def capability(identity):
    manifest=next(item for item in native_query_catalog.manifests() if item.specification['method']=='aggregate')
    value=manifest.model_dump(mode='json')
    return SimpleNamespace(id=identity,data=value,content_hash=digest(value),release_version=manifest.version)


def request(entities,tool):
    return DataRequest(question='计算登记值均值',entity_ids=[row.id for row in entities],fields=['amount'],
        mode='structured',business_timezone='UTC',evidence_requirement={'source':'archive'},
        tool_steps=[{'id':'mean','capability_id':tool.id,'input_from':'evidence',
            'parameters':{'field':'amount','operation':'mean'}}]).model_dump()


def source_catalog(monkeypatch,entities,tool):
    source=SimpleNamespace(id=entities[0].schema_capability_id,
        data={'dependencies':[tool.id],'specification':{'data_tool_ids':[tool.id]}})
    monkeypatch.setattr(data_tools,'published',lambda db,p,identity,kind=None:source if identity==source.id else tool)


def test_managed_task_uses_target_catalog_without_granting_it_to_generic_query(monkeypatch):
    p,task,entities,checks=fixture_scope(monkeypatch)
    source_tool=capability('source-tool');target_tool=capability('target-tool')
    source_catalog(monkeypatch,entities,source_tool)
    monkeypatch.setattr(native_query_catalog,'catalog_for_task',lambda db,p,saved:[target_tool])
    assert [cap.id for cap in data_tools.choices(None,p,task.data['entity_ids'])]==['source-tool']
    assert [cap.id for cap in data_tools.choices(None,p,task.data['entity_ids'],task=task)]==['target-tool']
    data_tools.validate_steps(None,p,request(entities,target_tool),request(entities,target_tool)['tool_steps'],task=task)
    with pytest.raises(HTTPException) as blocked:
        data_tools.validate_steps(None,p,request(entities,target_tool),request(entities,target_tool)['tool_steps'])
    assert blocked.value.status_code==403
    with pytest.raises(HTTPException):
        data_tools.validate_steps(None,p,request(entities,source_tool),request(entities,source_tool)['tool_steps'],task=task)
    assert checks==['saved-task']*3


@pytest.mark.parametrize('ids',[['original-0'],['original-0','original-1','other'],['original-0','original-0']])
def test_managed_query_cannot_expand_shrink_or_duplicate_saved_scope(monkeypatch,ids):
    p,task,_,_=fixture_scope(monkeypatch)
    with pytest.raises(HTTPException) as blocked:data_tools.choices(None,p,ids,task=task)
    assert blocked.value.status_code==403


def test_temporary_goal_cannot_be_used_as_authority(monkeypatch):
    p,task,_,_=fixture_scope(monkeypatch)
    temporary=SimpleNamespace(id=task.id,workspace_id=p.workspace_id,data=task.data,package_id=task.package_id)
    with pytest.raises(HTTPException) as blocked:data_tools.choices(None,p,task.data['entity_ids'],task=temporary)
    assert blocked.value.status_code==409


def test_legacy_task_keeps_source_catalog_and_source_memory(monkeypatch):
    p,task,entities,_=fixture_scope(monkeypatch,proof=False,source='target-package')
    tool=capability('original-tool');source_catalog(monkeypatch,entities,tool)
    monkeypatch.setattr(native_query_catalog,'catalog_for_task',lambda *a:pytest.fail('legacy catalog changed'))
    assert data_tools.choices(None,p,task.data['entity_ids'],task=task)==[tool]
    assert data_tools.managed_include_trusted_cases(None,p,task) is True
    assert data_tools.managed_include_trusted_cases(None,p,None) is True


@pytest.mark.parametrize('source,allowed',[('source-package',False),('target-package',True)])
def test_compatibility_does_not_migrate_memory_permission(monkeypatch,source,allowed):
    p,task,_,checks=fixture_scope(monkeypatch,source=source)
    assert data_tools.managed_include_trusted_cases(None,p,task) is allowed
    assert checks==['saved-task']


def test_source_permission_revocation_prevents_read_and_tool_authorization(monkeypatch):
    from generic_agents import entity_release_compat as compat
    p,task,_,_=fixture_scope(monkeypatch)
    def revoked(*args,**kwargs):raise HTTPException(409,'source package unpublished')
    monkeypatch.setattr(compat,'verify_task_objects',revoked)
    monkeypatch.setattr(native_query_catalog,'catalog_for_task',lambda *a:pytest.fail('grant reached after revocation'))
    for action in [lambda:data_tools.managed_include_trusted_cases(None,p,task),
            lambda:data_tools.choices(None,p,task.data['entity_ids'],task=task)]:
        with pytest.raises(HTTPException,match='source package unpublished'):action()


def test_request_preparation_uses_real_task_authority_without_serializing_it(monkeypatch):
    p,task,entities,_=fixture_scope(monkeypatch)
    tool=capability('target-tool');value=request(entities,tool);calls=[]
    monkeypatch.setattr(data_tools,'validate_steps',lambda db,p,req,steps,*,task=None:calls.append(task) or steps)
    prepared=data_requirements.prepare_request(None,p,value,_confirmed_plan=True,task=task)
    assert calls==[task]
    assert prepared['entity_ids']==value['entity_ids']
    assert not any(key.startswith('object_definition') or key=='task' for key in prepared)


@pytest.mark.parametrize('legacy_task',[False,True])
def test_generic_and_legacy_preparation_keep_original_no_keyword_tool_seam(monkeypatch,legacy_task):
    p,_,entities,_=fixture_scope(monkeypatch,proof=False)
    tool=capability('source-tool');value=request(entities,tool);calls=[]
    def old_choices(db,principal,ids):calls.append(ids);return [tool]
    monkeypatch.setattr(data_tools,'choices',old_choices)
    task=SimpleNamespace(data={'workflow_runtime':{'current_step_id':'human'}}) if legacy_task else None
    prepared=data_requirements.prepare_request(None,p,value,_confirmed_plan=True,task=task)
    assert prepared['tool_steps']==value['tool_steps'] and calls==[value['entity_ids']]
    assert data_tools.managed_task_kwargs(task)=={}


def test_orphan_private_hash_cannot_silently_select_legacy_authorization(monkeypatch):
    from generic_agents import entity_release_compat as compat
    p,task,_,_=fixture_scope(monkeypatch)
    task.data.pop('object_definition_bindings')
    def refused(*args,**kwargs):raise HTTPException(409,'missing server proof')
    monkeypatch.setattr(compat,'verify_task_objects',refused)
    assert data_tools.managed_task_kwargs(task)=={'task':task}
    with pytest.raises(HTTPException,match='missing server proof'):
        data_tools.choices(None,p,task.data['entity_ids'],**data_tools.managed_task_kwargs(task))


def test_goal_temporary_proposal_explicitly_keeps_saved_task_authority(monkeypatch):
    from generic_agents import goal_queries
    p,task,_,_=fixture_scope(monkeypatch)
    spec={'purpose':'读取登记值','entity_ids':task.data['entity_ids'],'required_fields':['amount'],
        'evidence':{'source':'archive'},'data_query':{'tool_steps':[],'max_tool_seconds':60,'allow_external_tools':False},
        'max_tool_calls':6}
    temporary=SimpleNamespace(data={**task.data,'goal_spec':spec,'goal_window':{'reference_at':'2026-10-16T00:00:00+00:00'}})
    calls=[]
    monkeypatch.setattr(data_requirements,'prepare_request',lambda db,p,value,**kwargs:calls.append(kwargs['task']) or value)
    goal_queries.request_for(None,p,temporary,authorization_task=task)
    assert calls==[task]


@pytest.mark.parametrize('changed_after_call',[False,True])
def test_mean24_keeps_original_versions_and_blocks_changed_query_evidence(monkeypatch,changed_after_call):
    from generic_agents import gateway,goal_queries
    p,task,entities,_=fixture_scope(monkeypatch)
    tool=capability('target-tool');value=request(entities,tool)
    monkeypatch.setattr(native_query_catalog,'catalog_for_task',lambda db,p,saved:[tool])
    monkeypatch.setattr(data_tools,'published',lambda *a,**k:tool)
    before=[(row.id,row.version,row.schema_capability_id,copy.deepcopy(row.data)) for row in entities]
    base={'status':'sufficient','evidence':[{'entity_id':row.id,'version':row.version,'attributes':copy.deepcopy(row.data)} for row in entities],
        'observation_evidence':[],'trusted_cases':[]}
    fresh=copy.deepcopy(base)
    run=DataRun(id='saved-query',workspace_id=p.workspace_id,owner_id=p.user_id,status='running',question=value['question'],data={'request':value})
    original_scoped=data_tools.scoped
    monkeypatch.setattr(data_tools,'scoped',lambda db,model,identity,p,lock=False:run if model is DataRun else original_scoped(db,model,identity,p,lock))
    monkeypatch.setattr(data_tools,'checkpoint',lambda *a:None)
    monkeypatch.setattr(data_tools,'audit',lambda *a:None)
    monkeypatch.setattr(goal_queries,'guard',lambda *a:None)
    calls=[]
    def invoke(db,p,identity,role,inputs,key):
        calls.append(copy.deepcopy(inputs))
        if changed_after_call:fresh['evidence'][0]['version']+=1
        return SimpleNamespace(id='local-receipt',status='succeeded',data={
            'output':data_tools.builtin(tool.data['execution']['handler'],inputs),'capability_hash':tool.content_hash})
    monkeypatch.setattr(gateway,'invoke',invoke)
    db=SimpleNamespace(expire_all=lambda:None,refresh=lambda row:None)
    result=data_tools.execute(db,p,value,copy.deepcopy(base),value['tool_steps'],run.id,lambda:copy.deepcopy(fresh),task=task)
    if changed_after_call:
        assert result['status']=='blocked_evidence' and 'derived_records' not in result
        assert result['tool_trace'][0]['invocation_id']=='local-receipt'
        assert [(row.id,row.version,row.schema_capability_id,row.data) for row in entities]==before
        return
    record=result['derived_records'][0]
    assert record['values']['value']==24 and record['values']['sample_count']==2
    assert record['entity_ids']==[row.id for row in entities]
    assert record['evidence_refs']==['entity:'+row.id+':'+str(row.version) for row in entities]
    assert [(row.id,row.version,row.schema_capability_id,row.data) for row in entities]==before
    assert calls and not any(key.startswith('object_definition') or key=='task' for key in calls[0])


def test_cross_release_data_agent_disables_memory_before_query_and_keeps_task(monkeypatch):
    from generic_agents import agents
    p,task,entities,_=fixture_scope(monkeypatch)
    tool=capability('target-tool');value=request(entities,tool);queries=[];executions=[]
    base={'status':'sufficient','evidence':[],'observation_evidence':[],'trusted_cases':[]}
    def read(*args,**kwargs):queries.append(kwargs);return copy.deepcopy(base)
    monkeypatch.setattr(agents,'query_evidence',read)
    monkeypatch.setattr(data_tools,'execute',lambda *a,**k:executions.append(k['task']) or a[3])
    result=agents.data_agent(None,p,value,'saved-query',emit_gaps=False,learning=False,task=task)
    assert result['status']=='sufficient'
    assert queries[0]['include_trusted_cases'] is False and executions==[task]


def test_disabled_memory_branch_does_not_read_memory_store(monkeypatch):
    from generic_agents import learning
    def forbidden(*args,**kwargs):pytest.fail('Memory store read without permission')
    monkeypatch.setattr(learning,'eligible',forbidden)
    monkeypatch.setattr(learning,'select_cases',forbidden)
    value=data_requirements.query(None,SimpleNamespace(workspace_id='workspace'),[],[],{'source':'archive'},include_trusted_cases=False)
    assert value['trusted_cases']==[] and value['learning_decisions']==[]


def metadata_fixture(monkeypatch):
    from generic_agents import workspace_api
    p,task,entities,checks=fixture_scope(monkeypatch)
    snapshot={'object_types':[{'key':'sample','name':'登记对象','fields':[
        {'key':'amount','label':'登记值','kind':'数值','required':True,'unit':'m'}]}],
        'entity_schemas':{'sample':{'type':'object','title':'登记对象',
            'properties':{'amount':{'type':'number','title':'登记值','unit':'m'}},'required':['amount'],'additionalProperties':False}}}
    release=SimpleNamespace(id='target-release',instance_id='instance',package_id=task.package_id,
        configuration_hash=digest(snapshot),data={'snapshot':snapshot})
    package=SimpleNamespace(id=task.package_id,data={'specification':{'configuration_hash':release.configuration_hash,
        'object_catalog':snapshot['object_types'],'entity_schemas':snapshot['entity_schemas']}})
    monkeypatch.setattr(workspace_api,'saved_execution',lambda *a:(task,release,{'id':'business','available':True}))
    return p,task,entities,checks,release,package


def test_new_query_metadata_uses_verified_original_ids_without_exposing_attributes(monkeypatch):
    from generic_agents import native_query_continuity as continuity
    p,task,entities,checks,release,package=metadata_fixture(monkeypatch)
    tool=capability('target-tool')
    monkeypatch.setattr(continuity,'scoped',lambda db,model,identity,p:next(row for row in entities if row.id==identity))
    monkeypatch.setattr(continuity,'published',lambda *a:package)
    monkeypatch.setattr(native_query_catalog,'catalog',lambda *a:[tool])
    result=continuity.build_context_catalog(None,p,task)
    assert checks==['saved-task']
    assert [row['id'] for row in result['objects']]==task.data['entity_ids']
    assert result['fields']==[{'key':'amount','label':'登记值','type':'number','unit':'m','entity_types':['sample']}]
    assert result['query_tools'][0]['id']=='target-tool'
    assert not any('attributes' in row or 'object_definition_bindings' in row for row in result['objects'])


def test_output_frame_accepts_verified_original_schema_but_rejects_extra_object(monkeypatch):
    p,task,entities,checks,release,package=metadata_fixture(monkeypatch)
    tool=capability('target-tool');value=request(entities,tool)
    monkeypatch.setattr(native_query_catalog,'published',lambda *a:package)
    monkeypatch.setattr(native_query_catalog,'catalog_for_task',lambda *a:[tool])
    db=SimpleNamespace(get=lambda model,identity:next((row for row in entities if row.id==identity),None))
    specification={'entity_ids':task.data['entity_ids'],'required_fields':['amount'],'data_query':{'tool_steps':value['tool_steps']}}
    output=native_query_catalog.output_schema_for_steps(db,p,task,specification)
    assert 'value' in output and checks==['saved-task']
    outside=Entity(id='extra',workspace_id=p.workspace_id,schema_capability_id=task.package_id,entity_type='sample')
    db.get=lambda model,identity:outside
    with pytest.raises(HTTPException):
        native_query_catalog.output_schema_for_steps(db,p,task,{**specification,'entity_ids':['extra']})


@pytest.mark.parametrize('proof',[False,True])
def test_query_proposal_dispatch_preserves_frozen_legacy_builder(monkeypatch,proof):
    from generic_agents import native_query_proposals as proposals,native_query_continuity as continuity,instance_api,workspace_api
    p,task,_,_=fixture_scope(monkeypatch,proof=proof)
    release=SimpleNamespace(id='target-release',configuration_hash='configuration')
    business={'id':'business'};step={'id':'human','agent':'人工确认'};calls=[]
    monkeypatch.setattr(proposals,'_actor',lambda *a:None)
    monkeypatch.setattr(workspace_api,'saved_execution',lambda *a:(task,release,business))
    monkeypatch.setattr(workspace_api,'current_step',lambda *a:step)
    monkeypatch.setattr(workspace_api,'authorize_step',lambda *a:None)
    monkeypatch.setattr(instance_api,'active',lambda *a:(None,release))
    monkeypatch.setattr(proposals,'scoped',lambda *a:SimpleNamespace(data={'enabled_business_ids':['business']}))
    monkeypatch.setattr(proposals.contract,'build_context_catalog',lambda *a:calls.append('legacy') or {'legacy':True})
    monkeypatch.setattr(continuity,'build_context_catalog',lambda *a:calls.append('proof') or {'proof':True})
    context,_,_=proposals._current(None,p,task)
    assert calls==['proof' if proof else 'legacy']
    assert ('object_definition_bindings_hash' in context) is proof


def test_frozen_proposal_contract_is_byte_exact():
    root=Path(__file__).resolve().parents[1]
    base=root/'.agentic-engineering/history/2026-10-16-before-entity-continuity/query'
    name='src/generic_agents/native_query_proposal_contract.py'
    expected=json.loads((base/'manifest.json').read_text())[name]['sha256']
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected


@pytest.mark.parametrize('nested',[False,True])
def test_public_task_removes_only_private_fields_in_overview_and_flat_shapes(monkeypatch,nested):
    from generic_agents import native_query_proposals as proposals
    from generic_agents.services import as_dict
    _,task,_,_=fixture_scope(monkeypatch)
    business={'object_definition_bindings':'a real business field','object_definition_bindings_hash':'a real business value'}
    task.data['object_snapshots']=[{'id':'object','attributes':copy.deepcopy(business)}]
    task.data['attributes']=copy.deepcopy(business)
    value=as_dict(task) if nested else {'id':task.id,**task.data}
    # Both shapes can be present in shared serializers; neither may expose the server proof.
    if nested:value.update(object_definition_bindings='top-private',object_definition_bindings_hash='top-private-hash')
    original=copy.deepcopy(value);result=proposals.public_task(value)
    assert value==original
    assert 'object_definition_bindings' not in result and 'object_definition_bindings_hash' not in result
    data=result['data'] if nested else result
    assert 'object_definition_bindings' not in data and 'object_definition_bindings_hash' not in data
    assert data['attributes']==business and data['object_snapshots'][0]['attributes']==business


def test_scope_hash_preserves_legacy_bytes_and_pins_new_private_proof(monkeypatch):
    from generic_agents import business_tools,entity_release_compat as compat
    _,task,_,_=fixture_scope(monkeypatch,proof=False)
    old={'package_id':task.package_id,'entity_ids':task.data['entity_ids'],'fields':[],'observation_mode':'auto'}
    assert business_tools.scope_hash(task)==digest(old)
    calls=[]
    monkeypatch.setattr(compat,'verify_task_binding_snapshot',lambda saved:calls.append(saved.id))
    task.data.update(object_definition_bindings={'server':'proof'},object_definition_bindings_hash='new-proof')
    assert business_tools.scope_hash(task)==digest({**old,'object_definition_bindings_hash':'new-proof'})
    assert calls==[task.id]


@pytest.mark.parametrize('proof',[False,True])
@pytest.mark.parametrize('surface',['planning','execution'])
def test_plan_and_execution_context_conditionally_pin_private_proof(monkeypatch,proof,surface):
    from generic_agents import (native_step_planning as planning,native_step_execution as execution,native_goals,
        native_planning_grants,workspace_api,goals,goal_queries,native_goal_queries,entity_release_compat as compat)
    p,task,_,_=fixture_scope(monkeypatch,proof=proof)
    task.status='workflow_waiting'
    task.data.update(goal_spec={'max_steps':10,'max_tool_calls':10},native_goal_context_hash='goal-context')
    task.data['workflow_runtime']['receipts']=[]
    release=SimpleNamespace(id='target-release',configuration_hash='config')
    step={'id':'human','name':'人工处理','agent':'人工确认','planning_tools':[]}
    business={'id':'business','flow':{'steps':[step]}}
    package=SimpleNamespace(id=task.package_id,content_hash='package-hash',data={'dependencies':[],
        'specification':{'planning_grants':{}}})
    monkeypatch.setattr(compat,'verify_task_binding_snapshot',lambda *a:None)
    monkeypatch.setattr(native_goals,'guard',lambda *a:None)
    monkeypatch.setattr(native_goals,'saved_context',lambda *a:None)
    monkeypatch.setattr(native_goals,'verify_acceptance_inputs',lambda *a:None)
    monkeypatch.setattr(workspace_api,'saved_execution',lambda *a:(task,release,business))
    monkeypatch.setattr(native_planning_grants,'current',lambda *a:(release,business,step,[]))
    monkeypatch.setattr(native_planning_grants,'pin',lambda *a:[])
    monkeypatch.setattr(planning,'manage',lambda *a:None)
    monkeypatch.setattr(planning,'_observation_sources',lambda *a:[])
    monkeypatch.setattr(planning,'published',lambda *a:package)
    monkeypatch.setattr(execution,'published',lambda *a:package)
    monkeypatch.setattr(execution,'step_count',lambda *a:0)
    monkeypatch.setattr(native_goal_queries,'step_count',lambda *a:0)
    monkeypatch.setattr(goal_queries,'call_count',lambda *a:0)
    evidence={'status':'sufficient','evidence':[],'observation_evidence':[]}
    goal={'spec':task.data['goal_spec'],'time':{}}
    monkeypatch.setattr(planning,'query_goal',lambda *a,**k:copy.deepcopy(evidence))
    monkeypatch.setattr(planning,'snapshot',lambda *a:copy.deepcopy(goal))
    monkeypatch.setattr(goals,'query_goal',lambda *a,**k:copy.deepcopy(evidence))
    monkeypatch.setattr(goals,'snapshot',lambda *a:copy.deepcopy(goal))
    context,_,_=(planning._current(None,p,task) if surface=='planning' else execution._context(None,p,task))
    assert ('object_definition_bindings_hash' in context) is proof
