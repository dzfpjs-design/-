"""Actual local Plan/Job/Audit custody over explicit source/IAM/SQL IO spies.

Not PG, published-capability, real permission, document, worker or product
durability evidence. Root runs the real target-runtime vertical separately.
"""
import copy
import sys
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from types import ModuleType,SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_store_v302 as s
from generic_agents.contracts import digest
from generic_agents.models import CloudCall
from tests.test_goal_evaluation_append_command_v297 import predicate
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene,ref

AT=datetime(2026,11,2,12,tzinfo=timezone.utc)
ACTUAL_SCOPE=s._scope


@pytest.fixture
def scene(monkeypatch):
    value=contract_scene();prep=value.preparation;scope=prep['scope']
    p=NS(workspace_id=scope['workspace_id'],user_id=prep['requested_by'],role='admin')
    task=s.Task(id=scope['task_id'],workspace_id=p.workspace_id,owner_id=p.user_id,package_id=scope['package_id'],
        status='workflow_active',version=1,data={'goal_hash':scope['goal_hash'],'goal_version':1,
            'goal_spec':{'max_tool_calls':12,'max_steps':12},'graph_runtime':{'retained_original':True}})
    cap=s.Capability(id=prep['advice_capability_ref']['id'],workspace_id=p.workspace_id,kind='tool',status='published',
        release_version='1.0.0',content_hash=digest(value.manifest),validated_hash=digest(value.manifest),data=value.manifest)
    state=NS(p=p,task=task,template=value,body=copy.deepcopy(prep['request']),clock=AT,flushes=0,source_reads=[],
        # Explicit empty paid-ledger IO inventory for this local-only fixture;
        # the actual scoped reverse-inventory SQL still runs.
        locks=[],nonce_calls=[],rows={s.Plan:[],s.Job:[],s.Audit:[],s.Invocation:[],CloudCall:[],s.Capability:[cap]})
    class DB:
        def scalar(self,statement):
            if 'clock_timestamp' in str(statement):return state.clock
            entity=statement.column_descriptions[0]['entity']
            if entity is s.Plan:
                return next((row.id for row in state.rows[s.Plan] if predicate(statement.whereclause,row)),None)
            assert entity is s.Task
            state.locks.append(statement._for_update_arg is not None)
            return task if predicate(statement.whereclause,task) else None
        def scalars(self,statement):
            model=statement.column_descriptions[0]['entity'];assert model in state.rows
            assert statement._limit_clause.value==s.MAX_ROWS+1
            return [row for row in state.rows[model] if predicate(statement.whereclause,row)]
        def add(self,row):
            assert type(row) in state.rows
            state.rows[type(row)].append(row)
        def flush(self):state.flushes+=1
        @contextmanager
        def begin_nested(self):
            before={model:list(rows) for model,rows in state.rows.items()}
            saved=[(row,row.status,row.version,copy.deepcopy(row.data)) for row in state.rows[s.Plan]]
            try:yield
            except BaseException:
                state.rows=before
                for row,status,version,data in saved:row.status=status;row.version=version;row.data=data
                raise
        def commit(self):pytest.fail('caller owns the transaction')
    state.db=DB()
    def nonce_io(db,principal,*,action,task_id,body,subject_id=None,advice_id=None):
        # Explicit PostgreSQL transaction-lock/foreign-positive inventory IO.
        # This fixture proves the original family guards below, not that IO;
        # the dedicated nonce utility pure/PG suite checks its actual SQL.
        assert db is state.db and principal is p and task_id==task.id and advice_id is None
        assert action in {s.ACCEPTED,s.CANCELLED} and type(body) is dict and 'request_id' in body
        assert subject_id is None if action==s.ACCEPTED else isinstance(subject_id,str)
        state.nonce_calls.append({'action':action,'task_id':task_id,'body':copy.deepcopy(body),
            'subject_id':subject_id,'advice_id':advice_id,'locks_before':len(state.locks)})
    monkeypatch.setattr(s.command_nonce,'acquire',nonce_io)
    def scope_io(db,principal,actual,body,identity):
        assert db is state.db and principal is p and actual is task
        state.source_reads.append(body['request_id'])
        context=copy.deepcopy(prep)
        for key in ('schema','request','request_hash','requested_by','record_hash'):context.pop(key)
        context['scope']['analysis_plan_id']=identity;context['manifest']=value.manifest
        return context
    monkeypatch.setattr(s,'_scope',scope_io)
    monkeypatch.setattr(s,'_reader',lambda *args,**kwargs:None)
    monkeypatch.setattr(s,'lock_current_actor',lambda db,principal:state.locks.append('actual_requester_IAM_IO'))
    state.prepare=lambda body=None:s.prepare(state.db,p,task.id,body or state.body)
    return state


def test_local_prepare_creates_sealed_snapshot_real_plan_job_audit_and_readers_have_no_source_or_sdk_effect(scene,monkeypatch):
    v=scene;before=copy.deepcopy(v.task.data);view=v.prepare();plan=v.rows[s.Plan][0];job=v.rows[s.Job][0]
    assert view['phase']=='queued' and view['cloud_send_available'] is False
    assert job.kind==s.c.PREPARE_KIND and plan.task_id==v.task.id and v.flushes==2
    assert set(plan.data)=={'schema','preparation_snapshot','jobs','operations','document_preparation','reviews'}
    assert plan.data['preparation_snapshot']['scope']['analysis_plan_id']==plan.id
    assert len(v.rows[s.Audit])==1 and v.rows[s.Audit][0].action==s.ACCEPTED
    assert v.nonce_calls==[{'action':s.ACCEPTED,'task_id':v.task.id,'body':v.body,
        'subject_id':None,'advice_id':None,'locks_before':0}]
    nonce_calls=copy.deepcopy(v.nonce_calls)
    def no_sources(*args,**kwargs):pytest.fail('readers do not gather sources or create new operations')
    monkeypatch.setattr(s,'_scope',no_sources)
    assert s.get(v.db,v.p,v.task.id,plan.id)==view
    assert s.get_operation(v.db,v.p,v.task.id,v.body['request_id'])==view
    assert s.listing(v.db,v.p,v.task.id)['records']==[view]
    assert not v.rows[s.Invocation] and v.task.data==before and len(v.rows[s.Job])==1
    assert v.nonce_calls==nonce_calls  # GET/readers never acquire a write nonce lock.


def test_prepare_lost_response_reuses_original_and_rejects_new_body_actor_or_nonce_while_pending(scene):
    v=scene;view=v.prepare();assert v.prepare()==view and len(v.source_reads)==1
    with pytest.raises(HTTPException):v.prepare({**v.body,'reason':'same nonce changed request'})
    with pytest.raises(HTTPException,match='尚未结清'):v.prepare({**v.body,'request_id':ref('another_nonce')['id']})
    v.p.user_id=ref('different_actor')['id']
    with pytest.raises(HTTPException):v.prepare()
    assert len(v.rows[s.Plan])==len(v.rows[s.Job])==len(v.rows[s.Audit])==1


@pytest.mark.parametrize('fault',['orphan_job','orphan_accepted','wrong_job_pin','sdk','duplicate_job'])
def test_reverse_inventory_rejects_orphan_or_conflicting_original_before_different_nonce(scene,fault):
    v=scene;v.prepare();plan=v.rows[s.Plan][0];job=v.rows[s.Job][0]
    if fault=='orphan_job':v.rows[s.Plan].clear();v.rows[s.Audit].clear()
    if fault=='orphan_accepted':v.rows[s.Plan].clear();v.rows[s.Job].clear()
    if fault=='wrong_job_pin':job.data={**job.data,'plan_id':ref('wrong_plan')['id']}
    if fault=='sdk':v.rows[s.Invocation].append(s.Invocation(id=ref('unexpected_sdk')['id'],workspace_id=v.p.workspace_id,
        idempotency_key='unexpected',data={'job_id':job.id}))
    if fault=='duplicate_job':v.rows[s.Job].append(s.Job(id=ref('duplicate')['id'],workspace_id=v.p.workspace_id,
        kind=s.c.PREPARE_KIND,idempotency_key=job.idempotency_key,data=copy.deepcopy(job.data)))
    with pytest.raises(HTTPException):v.prepare({**v.body,'request_id':ref('new_nonce')['id']})
    assert len(v.source_reads)==1


def test_foreign_workspace_orphans_do_not_poison_this_task(scene):
    v=scene;foreign=ref('foreign_workspace')['id']
    v.rows[s.Job].append(s.Job(id=ref('foreign_job')['id'],workspace_id=foreign,kind=s.c.PREPARE_KIND,
        idempotency_key='same-goal-advice-prepare:'+v.task.id+':foreign',data={'task_id':v.task.id}))
    assert v.prepare()['phase']=='queued'


def test_cancel_is_durable_idempotent_and_preserves_snapshot_and_job_without_refund_or_delete(scene):
    v=scene;view=v.prepare();plan=v.rows[s.Plan][0];job=v.rows[s.Job][0]
    before=copy.deepcopy(plan.data['preparation_snapshot']);original_job=copy.deepcopy(job.data)
    body={'request_id':ref('cancel')['id'],'expected_plan_version':1,'reason':'暂不准备新建议，保留原资料'}
    result=s.cancel(v.db,v.p,v.task.id,plan.id,body)
    assert result['phase']=='cancelled' and s.cancel(v.db,v.p,v.task.id,plan.id,body)==result
    assert s.get(v.db,v.p,v.task.id,plan.id)==result and plan.data['preparation_snapshot']==before
    assert job.data==original_job and job.status=='queued' and len(v.rows[s.Audit])==2
    assert not v.rows[s.Invocation] and not {'refund','deleted'} & set(result)
    assert v.prepare({**v.body,'request_id':ref('after_explicit_cancel')['id']})['phase']=='queued'


@pytest.mark.parametrize('fault',['version','cancel_nonce_collision','unknown_sdk','snapshot_rewrite','unsupported_stage'])
def test_cancel_does_not_bypass_original_phase_or_identity(scene,fault):
    v=scene;v.prepare();plan=v.rows[s.Plan][0];job=v.rows[s.Job][0]
    body={'request_id':ref('cancel')['id'],'expected_plan_version':1,'reason':'保留原号取消'}
    if fault=='version':body['expected_plan_version']=2
    if fault=='cancel_nonce_collision':body['request_id']=v.body['request_id']
    if fault=='unknown_sdk':v.rows[s.Invocation].append(s.Invocation(id=ref('sdk')['id'],workspace_id=v.p.workspace_id,
        idempotency_key='unknown',status='unknown',data={'job_id':job.id}))
    if fault=='snapshot_rewrite':plan.data['preparation_snapshot']['instruction']='changed without accepted authority'
    if fault=='unsupported_stage':plan.data={**plan.data,'execution_snapshot':{'unexpected':'send stage'}}
    with pytest.raises(HTTPException):s.cancel(v.db,v.p,v.task.id,plan.id,body)
    assert plan.status!='cancelled' and len(v.rows[s.Audit])==1


def test_missing_real_quota_policy_cannot_leave_an_accepted_plan_or_job(scene,monkeypatch):
    v=scene
    def scope(*args):raise HTTPException(409,'尚未配置可核对的服务器业务建议配额政策')
    monkeypatch.setattr(s,'_scope',scope)
    with pytest.raises(HTTPException,match='配额政策'):v.prepare()
    assert not v.rows[s.Plan] and not v.rows[s.Job] and not v.rows[s.Audit]


@pytest.fixture
def policy_scene(scene,monkeypatch):
    v=scene;prep=v.template.preparation;scope=prep['scope']
    source=NS(**{'id':prep['source_business_capability_ref']['id'],'content_hash':prep['source_business_capability_ref']['content_hash'],'release_version':'1.0.0'})
    advice=v.rows[s.Capability][0]
    model=NS(id=prep['model_pin']['id'],content_hash=prep['model_pin']['content_hash'],release_version='1.0.0',
        data={'execution':{'provider':'deepseek','max_tokens':1024}})
    limits={key:value for key,value in prep['budget'].items() if key!='quota_policy_ref'}
    body={'schema':s.POLICY_SCHEMA,'scope':{key:value for key,value in scope.items() if key!='analysis_plan_id'},
        'source_business_capability_ref':s._pin(source),'advice_capability_ref':s._pin(advice),'model_pin':s._pin(model),
        'issued_at':(AT-timedelta(seconds=1)).isoformat(),'expires_at':(AT+timedelta(hours=1)).isoformat(),'limits':limits}
    body['record_hash']=s.c.record_hash(body)
    audit=s.Audit(id=ref('real_policy_audit')['id'],workspace_id=v.p.workspace_id,actor_id=v.p.user_id,
        action=s.QUOTA_POLICY,object_id=v.task.id,created_at=AT,data=body)
    v.rows[s.Audit].append(audit);v.policy=audit;v.policy_scope=scope;v.policy_caps=(source,advice,model);v.policy_members=[]
    def member(db,p,identity,*,admin=False):
        assert admin and identity==v.policy.actor_id;v.policy_members.append(identity)
    monkeypatch.setattr(s,'_member',member)
    v.read_policy=lambda:s._policy(v.db,v.p,v.task,scope,source,advice,model)
    return v


def test_budget_ref_is_derived_from_actual_protected_admin_policy_audit(policy_scene):
    v=policy_scene;budget=v.read_policy()
    assert budget['quota_policy_ref']=={'id':v.policy.id,'record_hash':digest(v.policy.data)}
    assert budget['max_cost_minor_units']==v.policy.data['limits']['max_cost_minor_units']
    assert v.policy_members==[v.policy.actor_id] and not v.rows[s.Plan] and not v.rows[s.Job]


@pytest.mark.parametrize('fault',['missing','wrong_action','wrong_workspace','expired','duplicate','model','goal_budget','self_hash','future_audit'])
def test_quota_authority_needs_real_closed_audit_and_current_scope_not_just_a_plausible_ref(policy_scene,fault):
    v=policy_scene
    if fault=='missing':v.rows[s.Audit].clear()
    if fault=='wrong_action':v.policy.action='caller.created_policy'
    if fault=='wrong_workspace':v.policy.workspace_id=ref('other_workspace')['id']
    if fault=='expired':v.clock=AT+timedelta(hours=2)
    if fault=='duplicate':v.rows[s.Audit].append(s.Audit(id=ref('duplicate_policy')['id'],workspace_id=v.p.workspace_id,
        actor_id=v.p.user_id,action=s.QUOTA_POLICY,object_id=v.task.id,created_at=AT,data=copy.deepcopy(v.policy.data)))
    if fault=='model':v.policy.data['model_pin']['content_hash']='0'*64
    if fault=='goal_budget':v.policy.data['limits']['goal_max_steps']=99
    if fault in {'model','goal_budget'}:v.policy.data['record_hash']=s.c.record_hash(v.policy.data)
    if fault=='self_hash':v.policy.data['record_hash']='0'*64
    if fault=='future_audit':v.policy.created_at=AT+timedelta(seconds=1)
    with pytest.raises(HTTPException):v.read_policy()
    assert not v.rows[s.Plan] and not v.rows[s.Job]


@pytest.mark.parametrize('denied',[False,True])
@pytest.mark.parametrize('actor',['admin','business','data','unrelated'])
def test_actual_scope_rechecks_released_roles_manifests_and_approved_source_without_a_live_fact_gate(scene,monkeypatch,denied,actor):
    """Original/source/maturity/IAM results are explicit IO, compiler is real."""
    from generic_agents import workspace_api,native_goals
    v=scene;prep=v.template.preparation;scope=prep['scope'];checks=[]
    if actor!='admin':
        v.p.role='operator'
        v.p.user_id=prep[actor+'_actor_id'] if actor in {'business','data'} else ref('unrelated_operator')['id']
    v.task.data.update(instance_id=scope['instance_id'],instance_release_id=scope['release_id'],
        native_goal_context_hash=scope['native_goal_context_hash'])
    release=NS(id=scope['release_id']);business={'id':scope['business_id'],'execution_mode':'confirmed_graph_workflow'}
    model_data={'execution':{'provider':'deepseek','model':'actual_model','max_tokens':1024},
        'allowed_agent_roles':['business'],'data_policy':{'cloud_allowed':True}}
    model=NS(id=prep['model_pin']['id'],content_hash=digest(model_data),validated_hash=digest(model_data),
        release_version='1.0.0',data=model_data)
    source_data=copy.deepcopy(v.template.source);source_data['execution']['model_profile_hash']=model.content_hash
    source=NS(id=prep['source_business_capability_ref']['id'],data=source_data,content_hash=digest(source_data),
        validated_hash=digest(source_data),release_version='1.0.0')
    advice=v.rows[s.Capability][0];advice.data=s.c.build_manifest(source_data,s._pin(source))
    advice.content_hash=advice.validated_hash=digest(advice.data)
    nodes=[{'id':name,'kind':'ability','actor_id':actor,'execution':{'agent_role':role,
        'capability_id':source.id,'capability_hash':source.content_hash,'release_version':'1.0.0'}}
        for name,actor,role in [('reasoning',prep['business_actor_id'],'business'),('packet',prep['data_actor_id'],'data')]]
    selected={'release':release,'binding':{'graph':{'nodes':nodes},'binding_hash':scope['source_binding_hash']}}
    monkeypatch.setattr(native_goals,'guard',lambda *args,**kwargs:checks.append('normal_guard'))
    monkeypatch.setattr(native_goals,'saved_context',lambda *args:checks.append('original_context'))
    monkeypatch.setattr(workspace_api,'saved_execution',lambda *args:(v.task,release,business))
    monkeypatch.setattr(s.provider,'binding',lambda *args:selected)
    def member(db,p,identity,**kwargs):
        checks.append(identity)
        if denied:raise HTTPException(403,'actual role membership IO denied')
    monkeypatch.setattr(s,'_member',member)
    monkeypatch.setattr(s,'published',lambda db,p,identity,kind:source if identity==source.id else model)
    mature={'head':prep['mature_head'],'goal_check_job':NS(id=prep['mature_head']['goal_check_job_id']),
        'memory':NS(id=prep['mature_head']['memory_id']),'report_hash':prep['mature_head']['report_hash'],
        'record':{'original_query_ref':prep['original_query_ref'],'evaluation_query_ref':prep['evaluation_query_ref'],
            'cut_at':prep['evaluation_cut'],'stored_current_status':'stale'}}
    monkeypatch.setattr(s.existing,'_mature_head',lambda *args:mature)
    monkeypatch.setattr(s,'snapshot',lambda task:{'hash':scope['goal_hash'],'version':1})
    monkeypatch.setattr(s,'_policy',lambda *args:prep['budget'])
    monkeypatch.setattr(s.sources,'resolve_documents',lambda db,p,task,selection:checks.append(('approved_documents',selection)))
    monkeypatch.setattr(s.provider,'require_current',lambda *args,**kwargs:pytest.fail('local preparation does not grant current facts'))
    if denied:
        with pytest.raises(HTTPException) as error:ACTUAL_SCOPE(v.db,v.p,v.task,v.body,scope['analysis_plan_id'])
        assert error.value.status_code==403
    elif actor=='unrelated':
        with pytest.raises(HTTPException) as error:ACTUAL_SCOPE(v.db,v.p,v.task,v.body,scope['analysis_plan_id'])
        assert error.value.status_code==409
    else:
        current=ACTUAL_SCOPE(v.db,v.p,v.task,v.body,scope['analysis_plan_id'])
        assert current['evaluation_cut']==prep['evaluation_cut'] and current['model_pin']==s._pin(model)
        assert checks==['normal_guard','original_context',prep['business_actor_id'],prep['data_actor_id'],
            ('approved_documents',v.body['document_source'])]
        assert current['data_actor_id']==prep['data_actor_id']
        assert current['business_actor_id']==prep['business_actor_id']
        if actor=='business':
            monkeypatch.setattr(s,'_scope',ACTUAL_SCOPE)
            saved=s.prepare(v.db,v.p,v.task.id,v.body)
            actual=v.rows[s.Plan][0];job=v.rows[s.Job][0]
            assert saved['phase']=='queued' and job.data['actor_id']==prep['data_actor_id']
            assert actual.data['preparation_snapshot']['requested_by']==v.p.user_id
            assert actual.data['preparation_snapshot']['business_actor_id']==v.p.user_id


@pytest.mark.parametrize('terminal',[False,True])
def test_document_marker_is_not_ready_until_real_job_terminal_and_cold_reader_accepts_it(scene,monkeypatch,terminal):
    """Fixed V304 cold reader is an explicit IO boundary, not receipt proof."""
    v=scene;v.prepare();plan=v.rows[s.Plan][0];job=v.rows[s.Job][0]
    marker={'schema':s.DOCUMENT_PREPARATION,'receipt_ref':ref('actual_v304_receipt'),
        'evidence_ref':v.template.sources['document']['evidence_ref'],'prepare_job_id':job.id,
        'preparation_snapshot_hash':plan.data['preparation_snapshot']['record_hash']}
    plan.data={**plan.data,'document_preparation':marker};plan.status='awaiting_consent'
    job.status='succeeded' if terminal else 'running';calls=[]
    module=ModuleType('generic_agents.same_goal_advice_preparation_v304')
    def cold(db,p,task,actual):
        assert terminal and db is v.db and p is v.p and task is v.task and actual is plan
        calls.append(plan.id);return {'marker':copy.deepcopy(marker),'content':b'local private bytes','receipt':{'server_only':True}}
    module.cold_read=cold;monkeypatch.setitem(sys.modules,module.__name__,module)
    view=s.get(v.db,v.p,v.task.id,plan.id)
    assert view['phase']==('awaiting_consent' if terminal else 'preparing')
    assert view['document_preparation']==(marker if terminal else None)
    assert calls==([plan.id] if terminal else [])
    assert not {'content','receipt','server_only'} & set(view)
    if terminal:
        before=copy.deepcopy(plan.data['preparation_snapshot'])
        cancelled=s.cancel(v.db,v.p,v.task.id,plan.id,{'request_id':ref('cancel_prepared')['id'],
            'expected_plan_version':plan.version,'reason':'已保存资料保留，暂不授权发送'})
        assert cancelled['phase']=='cancelled' and cancelled['document_preparation']==marker
        assert plan.data['preparation_snapshot']==before and job.status=='succeeded'
