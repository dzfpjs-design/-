"""Real native products and archive inventories at explicit SQL/IAM IO seams.

No PostgreSQL, actual adopted Task, human quality, device or production archive
is proved by this public fixture. Root owns those independent validations.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from generic_agents import workflow_task_archive_v198 as subject
from generic_agents import workflow_task_archive_api_v198 as api
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, Invocation, Job, Task
from generic_agents.workflow_task_archive_models_v198 import (
    WorkflowTaskArchive as Archive, WorkflowTaskArchiveVersion as Version,
    WorkflowTaskArchiveReference as Reference, WorkflowTaskArchiveCommand as Command)
from tests.test_business_reasoning_v197 import ident, manifest, scene as reasoning_scene
from tests.test_full_semantic_graph_v1 import base, control, edge, guards, object_schema
from tests.test_report_business_flow_wire_v184 import no_external

AT=datetime(2026,1,1,tzinfo=timezone.utc)


class IO:
    """Public row selection spy, not a DB/locking/rollback implementation."""
    def __init__(self): self.rows={};self.added=[];self.commits=0;self.info={};self.selects=[]
    def add(self,row):
        assert type(row) in {Archive,Version,Reference,Command,Audit}
        self.added.append(row);self.rows.setdefault(type(row),[]).append(row)
    def flush(self):
        for i,row in enumerate(self.added):
            if row.id is None: row.id=ident(1000+i)
            if row.created_at is None: row.created_at=AT
            if row.version is None: row.version=1
    def commit(self): self.flush();self.commits+=1
    def _find(self,statement):
        entity=statement.column_descriptions[0]['entity'];params=statement.compile().params
        self.selects.append((entity,dict(params)))
        rows=list(self.rows.get(entity,[]))
        for field in ('workspace_id','id','task_id','source_node_id','archive_id','archive_version_id','kind','status','lease_token',
                'request_key','idempotency_key','action','object_id','record_type'):
            key=field+'_1'
            if key in params: rows=[row for row in rows if getattr(row,field)==params[key]]
        if entity is Version: rows.sort(key=lambda row:row.version)
        if entity is Reference: rows.sort(key=lambda row:row.ordinal)
        return rows
    def scalar(self,statement):
        rows=self._find(statement);assert len(rows)<=1
        return rows[0] if rows else None
    def scalars(self,statement): return self._find(statement)


def scene(monkeypatch,*,analysis_issue=False):
    s=NS(db=IO());r=reasoning_scene();s.p=r.p
    for candidate in r.inputs['candidates']: candidate['context']=None
    if analysis_issue:
        r.output['candidate_assessments'][0].update(assessment='conflicting',missing_information=['需核对来源冲突'])
        r.output['recommendation']['candidate_ref']=None
    data={**manifest(),'kind':'tool','name':'public_task_archive','allowed_agent_roles':['data'],
        'input_schema':subject.input_schema(),'output_schema':subject.output_schema(),
        'execution':{'adapter':subject.ADAPTER,'side_effect':True},'data_policy':{'cloud_allowed':False},
        'dependencies':[],'specification':{'schema':'workflow-task-archive-contract-v198',
            'operation':subject.OP,'candidate_only':True}}
    s.cap=NS(id=ident(701),workspace_id=s.p.workspace_id,data=data,content_hash=digest(data),
        validated_hash=digest(data),release_version='1.0.0',kind='tool',status='published')
    s.inputs={'record_type':'处置记录','title':'本次处理资料归集','summary':'待人工核对的处置说明，不等于事实确认'}
    human={'id':'review','kind':'human','source_step_id':'source_review','guards':guards(),
        'actor_id':s.p.user_id,'role':'核对人员','output_schema':object_schema(excluded={'type':'boolean'})}
    source={'id':'analysis','kind':'ability','source_step_id':'source_analysis','guards':guards(),
        'actor_id':s.p.user_id,'execution':{'capability_id':r.cap.id,'capability_hash':r.cap.content_hash,
            'agent_role':'business','input_bindings':{},'arguments':copy.deepcopy(r.inputs),
            'input_schema':r.cap.data['input_schema'],'output_schema':r.cap.data['output_schema'],
            'adapter':'business_reasoning','release_version':'1.0.0','max_attempts':1}}
    archive={'id':'archive','kind':'ability','source_step_id':'source_archive','guards':guards(),
        'actor_id':s.p.user_id,'execution':{'capability_id':s.cap.id,'capability_hash':s.cap.content_hash,
            'agent_role':'data','input_bindings':{},'arguments':copy.deepcopy(s.inputs),
            'input_schema':subject.input_schema(),'output_schema':subject.output_schema(),
            'adapter':subject.ADAPTER,'release_version':'1.0.0','max_attempts':1}}
    s.graph=kernel.validate_graph(base([human,source,archive,control('end','end')],
        [edge('review','analysis'),edge('analysis','archive'),edge('archive','end')],
        'review',['review','analysis','archive']))
    state=kernel.new_run(s.graph,{},10);first=state['ready_human_ids'][0]
    state=kernel.complete(s.graph,state,first,{'excluded':False},ident(702),s.p.user_id,0,0,11)
    second=state['eligible_ability_ids'][0]
    from generic_agents.business_reasoning_v197 import check_result
    s.analysis_output=check_result({'inputs':r.inputs},r.output)
    state=kernel.complete(s.graph,state,second,s.analysis_output,ident(703),s.p.user_id,0,0,12)
    identity=state['eligible_ability_ids'][0];s.node=next(row for row in s.graph['nodes'] if row['id']=='archive')
    s.task=Task(id=ident(704),workspace_id=s.p.workspace_id,owner_id=s.p.user_id,package_id=ident(705),
        title='公开的跨领域处理任务',status='workflow_waiting',version=3,data={'instance_id':ident(706),
            'instance_release_id':ident(707),'business_id':'public_generic_business','entity_ids':[],
            'object_snapshots':[],'graph_runtime':state,'workflow_runtime':{'receipts':[]}})
    s.key='graph:'+s.task.id+':'+identity+':1'
    s.job=Job(id=ident(708),workspace_id=s.p.workspace_id,kind='execute_full_semantic_graph_node',status='running',
        lease_token=ident(709),data={'task_id':s.task.id,'actor_id':s.p.user_id,'activation_id':identity,
            'graph_hash':state['graph_hash'],'cancel_epoch':0,'activation_version':0,'attempt':1,
            'invocation_key':s.key,'inputs':copy.deepcopy(s.inputs),'input_hash':digest(s.inputs)})
    s.approval=subject.make_approval(s.task,s.job,s.node,s.inputs)
    s.call=Invocation(id=ident(710),workspace_id=s.p.workspace_id,capability_id=s.cap.id,
        idempotency_key=s.key,status='running',data={'job_id':s.job.id,'role':'data','side_effect':True,
            'capability_hash':s.cap.content_hash,'input_hash':digest(s.inputs),'approval':s.approval})
    source_key='graph:'+s.task.id+':'+second+':1'
    s.producer_job=Job(id=ident(703),workspace_id=s.p.workspace_id,kind=s.job.kind,status='succeeded',
        data={'task_id':s.task.id,'activation_id':second,'actor_id':s.p.user_id,'invocation_key':source_key,
            'input_hash':digest(r.inputs)})
    s.producer=Invocation(id=ident(711),workspace_id=s.p.workspace_id,capability_id=r.cap.id,
        idempotency_key=source_key,status='succeeded',data={'job_id':s.producer_job.id,'role':'business',
            'capability_hash':r.cap.content_hash,'input_hash':digest(r.inputs),'output':copy.deepcopy(s.analysis_output)})
    human_receipt={'request_id':ident(702),'activation_id':first,'step_id':'source_review','actor':s.p.user_id,
        'execution_mode':'human_graph_result','output':{'excluded':False},'entity_state_updated':False}
    source_receipt={'request_id':s.producer_job.id,'activation_id':second,'actor':s.p.user_id,
        'execution_mode':'registered_graph_ability','invocation_id':s.producer.id,
        'input_snapshot':copy.deepcopy(r.inputs),'output':copy.deepcopy(s.analysis_output)}
    s.task.data={**s.task.data,'workflow_runtime':{'receipts':[human_receipt,source_receipt]},
        'graph_jobs':{second:{'job_id':s.producer_job.id,'invocation_id':s.producer.id,'invocation_key':source_key}}}
    binding={'schema':'business-flow-nested-execution-binding-v195','review_mode':'engineering_simulation',
        'simulation_only':True,'graph':s.graph};binding['binding_hash']=digest(binding)
    s.context={'task':s.task,'job':s.job,'node':s.node,'scope':{'activation_id':identity},
        'release':NS(id=ident(707)),'business':{'id':'public_generic_business','name':'跨领域处理'},
        'binding':binding,'graph':s.graph,'state':state,'capability':s.cap}
    s.db.rows={Task:[s.task],Capability:[s.cap],Invocation:[s.call,s.producer],Job:[s.job,s.producer_job]}
    s.db.add(Audit(workspace_id=s.p.workspace_id,actor_id=s.p.user_id,action='workflow.graph.human_completed',
        object_id=s.task.id,data={'activation_id':first}))
    s.db.add(Audit(workspace_id=s.p.workspace_id,actor_id=s.p.user_id,action='workflow.graph.node_completed',
        object_id=s.task.id,data={'activation_id':second,'invocation_id':s.producer.id}))
    s.db.flush()
    def scoped(db,cls,identity,p,lock=False):
        assert db is s.db
        rows=[row for row in db.rows.get(cls,[]) if row.id==identity and row.workspace_id==p.workspace_id]
        if len(rows)!=1: raise HTTPException(404,'synthetic scoped row missing')
        return rows[0]
    monkeypatch.setattr(subject,'scoped',scoped)
    def context(db,p,cap,role,inputs,key,approval):
        assert (db,p,cap,role,key)==(s.db,s.p,s.cap,'data',s.key)
        assert inputs==s.inputs and approval==s.approval
        return s.context
    s.actual_approval=subject.validate_call_approval
    monkeypatch.setattr(subject,'validate_call_approval',context)
    return s


def revision(s,view,*,request_id=None,title='人工补充的候选档案'):
    return json.dumps({'schema':subject.REVISION,'request_id':request_id or ident(720),
        'expected_version':view['version'],'source_version_hash':view['version_hash'],
        'record_type':'复核记录','title':title,'summary':'补充说明仍待正式人工采纳'},ensure_ascii=False).encode()


def test_real_saved_products_become_queryable_record_version_reference_and_original_command(monkeypatch):
    s=scene(monkeypatch);result=subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval)
    assert s.db.commits==0 and result['status']=='candidate' and result['simulation_only'] is True
    assert all(result[key] is value for key,value in subject.FLAGS.items())
    assert len(s.db.rows[Archive])==len(s.db.rows[Version])==len(s.db.rows[Command])==1
    assert len(s.db.rows[Reference])==2
    command=s.db.rows[Command][0]
    assert command.request_key==s.key and command.invocation_id==s.call.id
    assert json.loads(command.exact_body)['inputs']==s.inputs
    assert json.loads(command.exact_body)['approval']==s.approval
    assert command.body_sha256==hashlib.sha256(command.exact_body.encode()).hexdigest()
    view=subject.get(s.db,s.p,result['archive_id']);value=view['value']
    assert value['summary_verification']=='unconfirmed_proposal'
    assert value['basis']['task_id']==s.task.id and value['basis']['source_binding_hash']==s.context['binding']['binding_hash']
    assert {row['kind'] for row in value['sources']}=={'human_product','invocation'}
    assert all(row['id']!=s.call.id for row in value['sources'])
    assert value['sources'][1]['snapshot']['output']==s.analysis_output
    assert subject.list_archives(s.db,s.p,s.task.id)['items'][0]['id']==result['archive_id']
    before=len(s.db.added)
    assert subject.reconcile(s.db,s.p,s.cap,s.inputs,s.key,s.approval,s.call)==result
    assert subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval)==result
    assert len(s.db.added)==before and s.db.commits==0


def test_cas_revision_retains_original_sources_simulation_and_full_version_history(monkeypatch):
    s=scene(monkeypatch);first=subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval)
    old=subject.get(s.db,s.p,first['archive_id']);raw=revision(s,old)
    second=subject.revise(s.db,s.p,first['archive_id'],raw)
    assert second['version']==2 and s.db.commits==1
    current=subject.get(s.db,s.p,first['archive_id']);previous=subject.get(s.db,s.p,first['archive_id'],1)
    assert previous['value']==old['value'] and current['value']['title']=='人工补充的候选档案'
    assert current['value']['sources']==old['value']['sources'] and current['value']['simulation_only'] is True
    assert all(current['value'][key] is value for key,value in subject.FLAGS.items())
    assert len(current['history'])==2 and len(s.db.rows[Reference])==4
    rows=len(s.db.added)
    assert subject.revise(s.db,s.p,first['archive_id'],raw)==second
    assert len(s.db.added)==rows and s.db.commits==1
    assert subject.recover(s.db,s.p,ident(720),hashlib.sha256(raw).hexdigest())['result']==second
    tool=s.db.rows[Command][0]
    assert subject.recover(s.db,s.p,s.key,tool.body_sha256)['result']==first
    stale=revision(s,old,request_id=ident(721))
    with pytest.raises(HTTPException): subject.revise(s.db,s.p,first['archive_id'],stale)
    assert len(s.db.rows[Version])==2


def test_source_conflict_and_missing_information_are_preserved_as_unconfirmed_archive_issues(monkeypatch):
    s=scene(monkeypatch,analysis_issue=True)
    result=subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval)
    value=subject.get(s.db,s.p,result['archive_id'])['value']
    assert value['issues'][0]['code']=='analysis_pending_review'
    assessment=value['issues'][0]['assessment']
    assert assessment['assessment']=='conflicting' and assessment['missing_information']==['需核对来源冲突']
    assert value['sources'][1]['snapshot']['output']==s.analysis_output
    assert value['formal_archive'] is False and value['human_confirmed'] is False


def test_same_revision_nonce_committed_while_waiting_is_recovered_before_stale_cas(monkeypatch):
    s=scene(monkeypatch);first=subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval)
    raw=revision(s,subject.get(s.db,s.p,first['archive_id']))
    second=subject.revise(s.db,s.p,first['archive_id'],raw)
    original=subject._command;seen=[]
    def first_read_before_peer_commit(db,p,key):
        if key==ident(720):
            seen.append(key)
            if len(seen)==1: return None
        return original(db,p,key)
    monkeypatch.setattr(subject,'_command',first_read_before_peer_commit)
    count=len(s.db.added);commits=s.db.commits
    assert subject.revise(s.db,s.p,first['archive_id'],raw)==second
    assert len(seen)>=2 and len(s.db.added)==count and s.db.commits==commits


@pytest.mark.parametrize('fault',['bool_version','extra_confirmation','duplicate_field','nonfinite','invalid_utf8'])
def test_revision_wire_cannot_smuggle_confirmation_or_ambiguous_fields(monkeypatch,fault):
    s=scene(monkeypatch);first=subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval)
    raw=revision(s,subject.get(s.db,s.p,first['archive_id']));body=json.loads(raw)
    if fault=='bool_version': body['expected_version']=True
    elif fault=='extra_confirmation': body['formal_archive']=True
    elif fault=='nonfinite': body['expected_version']=float('inf')
    raw=json.dumps(body).encode()
    if fault=='duplicate_field': raw=raw[:-1]+b',"title":"other"}'
    elif fault=='invalid_utf8': raw=b'\xff'
    count=len(s.db.added)
    with pytest.raises((HTTPException,ValueError)): subject.revise(s.db,s.p,first['archive_id'],raw)
    assert len(s.db.added)==count and len(s.db.rows[Version])==1 and s.db.commits==0


def test_real_approval_gate_supports_pre_invocation_phase_and_rejects_cancel_or_wrong_role(monkeypatch):
    from generic_agents import workflow_graph_scope as scope, workflow_graph, layered_approval_store_v175 as layered
    from generic_agents import relations, workflow_graph_goal_guard_v133 as goals
    s=scene(monkeypatch);checks=[]
    identity=s.context['scope']['activation_id'];activation=s.context['state']['activations'][identity]
    s.db.info['worker_lease']={'job_id':s.job.id,'lease_token':s.job.lease_token}
    s.task.data={**s.task.data,'graph_jobs':{**s.task.data['graph_jobs'],identity:{'job_id':s.job.id,
        'invocation_key':s.key,'cancel_epoch':0,'input_hash':digest(s.inputs),'phase':'running'}}}
    # As in gateway.validate_call_approval, no current Invocation exists yet.
    s.db.rows[Invocation]=[s.producer]
    monkeypatch.setattr(subject,'published',lambda *_args:s.cap)
    monkeypatch.setattr(scope,'_frozen',lambda *_args:(s.task,s.context['release'],s.context['business'],
        s.graph,s.context['state'],activation,s.node))
    monkeypatch.setattr(workflow_graph,'business_binding',lambda *_args:s.context['binding'])
    monkeypatch.setattr(layered,'guard',lambda *_args,**_kwargs:checks.append('current_175'))
    monkeypatch.setattr(workflow_graph,'live',lambda *_args:checks.append('current_source'))
    monkeypatch.setattr(goals,'before_dispatch',lambda *_args:checks.append('current_goal'))
    monkeypatch.setattr(relations,'lock_current_actor',lambda *_args:checks.append('current_actor'))
    context=s.actual_approval(s.db,s.p,s.cap,'data',s.inputs,s.key,s.approval)
    assert context['job'] is s.job and context['node'] is s.node
    assert checks==['current_175','current_source','current_goal','current_actor']
    assert not s.db.rows.get(Archive) and s.db.commits==0
    with pytest.raises(HTTPException): s.actual_approval(s.db,s.p,s.cap,'business',s.inputs,s.key,s.approval)
    s.context['state']['status']='cancelled'
    with pytest.raises(HTTPException): s.actual_approval(s.db,s.p,s.cap,'data',s.inputs,s.key,s.approval)
    assert not s.db.rows.get(Command) and s.db.commits==0


@pytest.mark.parametrize('fault',['other_task','source_output','human_receipt','human_command','missing_audit'])
def test_forged_or_unbound_upstream_products_cannot_be_saved_as_archive_sources(monkeypatch,fault):
    s=scene(monkeypatch)
    if fault=='other_task': s.producer_job.data['task_id']=ident(800)
    elif fault=='source_output': s.producer.data['output']={**s.analysis_output,'review_state':'needs_information'}
    elif fault=='human_receipt': s.task.data['workflow_runtime']['receipts'][0]['actor']=ident(801)
    elif fault=='human_command': s.context['state']['commands'][0]['body_hash']='0'*64
    else: s.db.rows[Audit]=[row for row in s.db.rows[Audit] if row.action!='workflow.graph.node_completed']
    with pytest.raises((HTTPException,ValueError)): subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval)
    assert not s.db.rows.get(Archive) and not s.db.rows.get(Command) and s.db.commits==0


@pytest.mark.parametrize('fault',['reference','version','audit','head_node','command_bytes'])
def test_cold_inventory_tampering_is_blocked_not_silently_repaired(monkeypatch,fault):
    s=scene(monkeypatch);result=subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval)
    if fault=='reference': s.db.rows[Reference][0].source_hash='0'*64
    elif fault=='version': s.db.rows[Version][0].data['value']['summary']='tampered'
    elif fault=='audit': s.db.rows[Audit]=[row for row in s.db.rows[Audit] if row.action!=subject.SAVED]
    elif fault=='head_node': s.db.rows[Archive][0].source_node_id='another_node'
    else: s.db.rows[Command][0].exact_body+=' '
    count=len(s.db.added)
    with pytest.raises(HTTPException): subject.get(s.db,s.p,result['archive_id'])
    assert len(s.db.added)==count and s.db.commits==0


def test_missing_command_unknown_never_reinserts_and_actor_or_nonce_changes_are_rejected(monkeypatch):
    s=scene(monkeypatch)
    with pytest.raises(HTTPException): subject.reconcile(s.db,s.p,s.cap,s.inputs,s.key,s.approval,s.call)
    assert not s.db.rows.get(Archive)
    result=subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval);command=s.db.rows[Command][0]
    stranger=NS(**{**vars(s.p),'user_id':ident(810)})
    with pytest.raises(HTTPException) as error: subject.recover(s.db,stranger,s.key,command.body_sha256)
    assert error.value.status_code==403
    with pytest.raises(HTTPException): subject.recover(s.db,s.p,s.key,'0'*64)
    assert subject.recover(s.db,s.p,'not-a-saved-key','0'*64)['found'] is False
    viewer=NS(**{**vars(s.p),'role':'viewer'})
    assert subject.get(s.db,viewer,result['archive_id'])['value']['simulation_only'] is True
    with pytest.raises(HTTPException): subject.revise(s.db,viewer,result['archive_id'],revision(s,subject.get(s.db,s.p,result['archive_id'])))


def test_manifest_is_explicit_local_data_side_effect_not_formal_confirmation(monkeypatch):
    s=scene(monkeypatch);subject.validate_manifest(s.cap.data)
    for field,value in [('side_effect',False),('adapter','builtin')]:
        data=copy.deepcopy(s.cap.data);data['execution'][field]=value
        with pytest.raises(HTTPException): subject.validate_manifest(data)
    data=copy.deepcopy(s.cap.data);data['data_policy']['cloud_allowed']=True
    with pytest.raises(HTTPException): subject.validate_manifest(data)


def test_actual_archive_http_routes_read_versions_original_and_reject_duplicate_query(monkeypatch):
    s=scene(monkeypatch);result=subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval)
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[api.authenticated]=lambda:s.p
    app.dependency_overrides[api.session]=lambda:s.db
    with TestClient(app) as client:
        listed=client.get('/api/task-archives',params={'task_id':s.task.id})
        assert listed.status_code==200 and listed.json()['items'][0]['id']==result['archive_id']
        detail=client.get('/api/task-archives/'+result['archive_id'])
        assert detail.status_code==200 and detail.json()['value']['formal_archive'] is False
        command=s.db.rows[Command][0]
        original=client.get('/api/task-archives/requests/original',params={'request_key':s.key,'expected_sha256':command.body_sha256})
        assert original.status_code==200 and original.json()['result']==result
        assert client.get('/api/task-archives?task_id='+s.task.id+'&task_id='+s.task.id).status_code==422
        raw=revision(s,detail.json())
        saved=client.post('/api/task-archives/'+result['archive_id']+'/revisions',content=raw,
            headers={'Content-Type':'application/json'})
        assert saved.status_code==200 and saved.json()['version']==2
        old=client.get('/api/task-archives/'+result['archive_id']+'/versions/1')
        assert old.status_code==200 and old.json()['value']['title']==s.inputs['title']
