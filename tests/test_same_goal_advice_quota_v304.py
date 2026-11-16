"""Quota control flow with explicit SQL/IAM/release/context IO adapters.

Real closed request/Budget/manifest checks, nonce custody, protected Audit
payloads, expiry and store._policy compatibility run. No PG, actual current
account, publication, Goal facts, token, price, money ledger or cloud proof.
"""
import copy
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import same_goal_advice_quota_v304 as q
from generic_agents import same_goal_advice_store_v302 as store
from generic_agents.contracts import Manifest, digest
from generic_agents.models import Audit, Capability, Task
from generic_agents.security import Principal
from tests.test_goal_evaluation_append_command_v297 import predicate
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene

AT = datetime(2026, 11, 2, 18, tzinfo=timezone.utc)
CONFIGURATION = q._configuration


def uid(): return str(uuid4())


@pytest.fixture
def scene(monkeypatch):
    sample = contract_scene()
    p = Principal(uid(), sample.preparation['scope']['workspace_id'], 'admin', 'worker')
    model_data = Manifest(kind='foundation_model', namespace='quota_fixture', name='cloud_model', version='1.0.0',
        owner='explicit registry IO fixture', description='not an actual connected model',
        input_schema={'type':'object'}, output_schema={'type':'object'}, allowed_agent_roles=['business'],
        execution={'provider':'deepseek','model':'explicit-IO-fixture','max_tokens':1024},
        data_policy={'cloud_allowed':True}, budget={'max_attempts':1,'max_concurrency':1},
        dependencies=[], specification={}).model_dump(mode='json')
    model = Capability(id=sample.preparation['model_pin']['id'], workspace_id=p.workspace_id,
        kind='foundation_model', status='published', release_version='1.0.0', data=model_data,
        content_hash=digest(model_data), validated_hash=digest(model_data))
    source_data = copy.deepcopy(sample.source)
    source_data['execution']['model_profile_hash'] = model.content_hash
    source = Capability(id=sample.original['id'], workspace_id=p.workspace_id, kind='tool',
        status='published', release_version='1.0.0', data=source_data,
        content_hash=digest(source_data), validated_hash=digest(source_data))
    advice_data = q.c.build_manifest(source_data, store._pin(source))
    advice = Capability(id=sample.preparation['advice_capability_ref']['id'], workspace_id=p.workspace_id,
        kind='tool', status='published', release_version='1.0.0', data=advice_data,
        content_hash=digest(advice_data), validated_hash=digest(advice_data))
    spec = {'purpose':'quota configuration only','max_tool_calls':12,'max_steps':12,'deadline':None}
    task = Task(id=sample.preparation['scope']['task_id'], workspace_id=p.workspace_id,
        owner_id=p.user_id, package_id=sample.preparation['scope']['package_id'], version=1,
        title='额度政策纯控制流验证', status='workflow_active', data={'goal_spec':spec,'goal_hash':digest(spec),
            'goal_version':1,'goal_confirmed_by':p.user_id,'goal_window':{'reference_at':AT.isoformat()},
            'instance_id':sample.preparation['scope']['instance_id'],
            'instance_release_id':sample.preparation['scope']['release_id'],
            'native_goal_context_hash':'4'*64})
    goal = q.snapshot(task)
    scope = {key:value for key,value in sample.preparation['scope'].items() if key!='analysis_plan_id'}
    scope.update(goal_hash=goal['hash'],goal_snapshot_hash=digest(goal))
    context = {'scope':scope,'source':source,'advice':advice,'model':model,'goal':goal,'document_required':True}
    body = {'request_id':uid(),'expected_goal_hash':goal['hash'],'expected_mature_head_hash':'6'*64,
        'limits':{'max_tokens':1024,'max_request_bytes':50000,'max_evidence_items':2,
            'max_cost_minor_units':100,'currency':'CNY'},
        'expires_at':(AT+timedelta(hours=1)).isoformat(),'reason':'仅配置一次建议的额度上限'}
    s = NS(p=p,task=task,context=context,body=body,time=AT,allowed=True,member=True,
        rows={Audit:[],Capability:[source,advice,model],Task:[task]},events=[],flushes=0,config_calls=0)
    class IO:
        @property
        def no_autoflush(self): return nullcontext()
        def scalar(self, statement):
            if 'clock_timestamp' in str(statement):return s.time
            model=statement.column_descriptions[0]['entity']
            assert p.workspace_id in statement.compile().params.values()
            items=[item for item in s.rows[model] if predicate(statement.whereclause,item)]
            assert len(items)<=1
            s.events.append(('fresh',model,statement._for_update_arg is not None))
            return items[0] if items else None
        def scalars(self, statement):
            model=statement.column_descriptions[0]['entity']
            assert p.workspace_id in statement.compile().params.values()
            assert store.MAX_ROWS+1 in statement.compile().params.values()
            return [item for item in s.rows[model] if predicate(statement.whereclause,item)]
        def add(self,item):
            assert type(item) is Audit, 'quota writer cannot create caps/SDK/Job/Plan or another Task'
            s.rows[Audit].append(item)
        def flush(self):s.flushes+=1
        def commit(self):pytest.fail('API caller owns transaction')
        @contextmanager
        def begin_nested(self):
            before=list(s.rows[Audit])
            try:yield
            except BaseException:
                s.rows[Audit][:]=before
                raise
    s.db=IO()
    def reader(db,principal,**kwargs):
        assert db is s.db
        if not s.allowed or principal.workspace_id!=p.workspace_id:
            raise HTTPException(403,'explicit current IAM denial')
    monkeypatch.setattr(store,'_reader',reader)
    def actual_actor(db,principal):
        s.events.append(('current-IAM-IO',principal.user_id))
        if not s.allowed:raise HTTPException(403,'explicit current IAM denial')
    monkeypatch.setattr(q,'lock_current_actor',actual_actor)
    def member(db,principal,identity,*,admin=False):
        s.events.append(('current-member-lock-IO',identity,admin))
        if not s.member:raise HTTPException(403,'explicit disabled/demoted member')
    monkeypatch.setattr(store,'_member',member)
    def configuration(db,principal,actual,request):
        s.config_calls+=1
        assert db is s.db and actual is task and principal is s.p
        store.need(request['expected_goal_hash']==goal['hash']
            and request['expected_mature_head_hash']==s.body['expected_mature_head_hash'])
        return s.context
    monkeypatch.setattr(q,'_configuration',configuration)
    monkeypatch.setattr(store,'_scope',lambda *a,**k:pytest.fail('policy cannot depend on itself'))
    def registry(db,principal,identity,kind):
        values=[item for item in s.rows[Capability] if item.id==identity]
        store.need(len(values)==1 and values[0].kind==kind and values[0].status=='published')
        return values[0]
    monkeypatch.setattr(q,'published',registry)
    return s


def authorize(s,body=None,p=None):return q.authorize(s.db,p or s.p,s.task.id,body or s.body)


def test_real_policy_shape_and_budget_ref_are_consumable_by_existing_reader(scene):
    s=scene;receipt=authorize(s)
    policies=[a for a in s.rows[Audit] if a.action==store.QUOTA_POLICY]
    grants=[a for a in s.rows[Audit] if a.action==q.AUTHORIZATION]
    assert len(policies)==len(grants)==1 and s.flushes==1
    policy,grant=policies[0],grants[0]
    assert set(policy.data)==q.POLICY_FIELDS and policy.object_id==s.task.id
    assert policy.created_at==grant.created_at==AT
    assert grant.data['body_hash']==digest(s.body) and grant.data['policy_ref']==receipt['quota_policy_ref']
    assert receipt['quota_policy_ref']=={'id':policy.id,'record_hash':digest(policy.data)}
    assert receipt['configuration_only'] is True
    assert receipt['pricing_verified'] is receipt['cloud_send_authorized'] is False
    assert not {'price','estimated_cost','token','sdk_id'} & receipt.keys()
    budget=store._policy(s.db,s.p,s.task,{**s.context['scope'],'analysis_plan_id':uid()},
        s.context['source'],s.context['advice'],s.context['model'])
    assert budget['quota_policy_ref']==receipt['quota_policy_ref']
    assert budget['max_actual_calls']==budget['reserved_calls']==budget['reserved_steps']==1
    assert budget['provider']=='deepseek' and budget['goal_max_steps']==12


def test_lost_response_reuses_original_audits_without_extending_expiry_or_revalidating_changed_head(scene):
    s=scene;first=authorize(s);before=copy.deepcopy([a.data for a in s.rows[Audit]])
    s.time=AT+timedelta(hours=2)  # Original receipt can be read after expiry.
    s.body={**s.body,'expected_mature_head_hash':'7'*64}
    original=copy.deepcopy(s.rows[Audit][1].data['body'])
    assert authorize(s,original)==first
    assert s.config_calls==1 and s.flushes==1 and [a.data for a in s.rows[Audit]]==before


@pytest.mark.parametrize('field,value',[('reason','changed nonce content'),('expires_at','2026-11-02T20:00:00+00:00')])
def test_same_nonce_cannot_change_original_content(scene,field,value):
    s=scene;authorize(s);body={**s.body,field:value}
    with pytest.raises(HTTPException):authorize(s,body)
    assert len(s.rows[Audit])==2


def test_different_nonce_cannot_stack_another_active_policy_for_same_scope(scene):
    s=scene;authorize(s)
    with pytest.raises(HTTPException):authorize(s,{**s.body,'request_id':uid()})
    assert len(s.rows[Audit])==2


def test_expired_policy_allows_new_original_configuration_without_mutating_old_audits(scene):
    s=scene;first=authorize(s);before=copy.deepcopy([a.data for a in s.rows[Audit]])
    s.time=AT+timedelta(hours=2)
    body={**s.body,'request_id':uid(),'expires_at':(s.time+timedelta(hours=1)).isoformat()}
    second=authorize(s,body)
    assert first['quota_policy_ref']!=second['quota_policy_ref'] and len(s.rows[Audit])==4
    assert [a.data for a in s.rows[Audit]][:2]==before


@pytest.mark.parametrize('fault',['operator','disabled','reader-revoked','foreign-admin','naive-clock',
    'expired','too-long','goal-deadline','too-many-tokens','one-evidence'])
def test_current_permissions_goal_time_and_configuration_limits_fail_without_audit_writes(scene,fault):
    s=scene;body=copy.deepcopy(s.body);principal=s.p
    if fault=='operator':principal=Principal(s.p.user_id,s.p.workspace_id,'operator','worker')
    elif fault=='disabled':s.member=False
    elif fault=='reader-revoked':s.allowed=False
    elif fault=='foreign-admin':principal=Principal(s.p.user_id,uid(),'admin','worker')
    elif fault=='naive-clock':s.time=AT.replace(tzinfo=None)
    elif fault=='expired':body['expires_at']=AT.isoformat()
    elif fault=='too-long':body['expires_at']=(AT+timedelta(days=2)).isoformat()
    elif fault=='goal-deadline':s.context['goal']['spec']['deadline']=(AT+timedelta(minutes=30)).isoformat()
    elif fault=='too-many-tokens':body['limits']['max_tokens']=2048
    else:body['limits']['max_evidence_items']=1
    with pytest.raises(HTTPException):authorize(s,body,principal)
    assert not s.rows[Audit]


@pytest.mark.parametrize('extra',['actor_id','model_id','capability_id','provider','module','python','pricing','price_per_token'])
def test_request_cannot_supply_identity_implementation_or_unverified_prices(scene,extra):
    with pytest.raises(ValidationError):q.Authorize.model_validate({**scene.body,extra:'client-supplied'})


@pytest.mark.parametrize('field',['max_tokens','max_request_bytes','max_evidence_items','max_cost_minor_units'])
def test_boolean_never_counts_as_an_integer_ceiling(scene,field):
    body=copy.deepcopy(scene.body);body['limits'][field]=True
    with pytest.raises(ValidationError):q.Authorize.model_validate(body)


@pytest.mark.parametrize('fault',['orphan-policy','policy-body-drift','rehashed-policy','grant-body-drift','other-issuer'])
def test_nonce_replay_requires_both_independent_original_audits(scene,fault):
    s=scene;authorize(s);policy,grant=s.rows[Audit]
    if fault=='orphan-policy':s.rows[Audit].remove(policy)
    elif fault=='policy-body-drift':policy.data['limits']['max_tokens']=512
    elif fault=='rehashed-policy':
        policy.data['limits']['max_tokens']=512;policy.data['record_hash']=q.c.record_hash(policy.data)
    elif fault=='grant-body-drift':grant.data['body_hash']='0'*64
    else:grant.actor_id=uid()
    with pytest.raises(HTTPException):authorize(s)
    assert s.flushes==1


def test_configuration_uses_real_fixed_cap_compiler_without_needing_a_policy(scene,monkeypatch):
    from generic_agents import workspace_api,native_goals,goal_queries
    s=scene;context=s.context
    release=NS(id=context['scope']['release_id'])
    business={'id':context['scope']['business_id'],'execution_mode':'confirmed_graph_workflow'}
    binding={'binding_hash':context['scope']['source_binding_hash'],'graph':{'nodes':[
        {'id':'reasoning','kind':'ability','actor_id':s.p.user_id,'execution':{
            'agent_role':'business','capability_id':context['source'].id,
            'capability_hash':context['source'].content_hash,'release_version':'1.0.0'}},
        {'id':'packet','kind':'ability','actor_id':s.p.user_id,'execution':{'agent_role':'data'}}]}}
    monkeypatch.setattr(workspace_api,'saved_execution',lambda *a:(s.task,release,business))
    monkeypatch.setattr(native_goals,'guard',lambda *a,**k:None)
    monkeypatch.setattr(native_goals,'saved_context',lambda *a:None)
    monkeypatch.setattr(goal_queries,'call_count',lambda *a:0)
    remaining=NS(used_steps=0)
    def total_steps(db,principal,task):
        # Historical workflow accounting is an explicit IO boundary; this
        # mock Task deliberately has no actual workflow/step receipt store.
        assert db is s.db and principal is s.p and task is s.task
        return remaining.used_steps
    monkeypatch.setattr(native_goals,'total_steps',total_steps)
    monkeypatch.setattr(store.provider,'binding',lambda *a:{'release':release,'binding':binding})
    monkeypatch.setattr(store.existing,'_mature_head',lambda *a:{'head':{'head_hash':s.body['expected_mature_head_hash']}})
    result=CONFIGURATION(s.db,s.p,s.task,s.body)
    assert result['source'] is context['source'] and result['advice'] is context['advice']
    assert result['model'] is context['model'] and result['scope']==context['scope']
    assert not s.rows[Audit]
    remaining.used_steps=s.context['goal']['spec']['max_steps']
    with pytest.raises(HTTPException,match='步骤额度'):CONFIGURATION(s.db,s.p,s.task,s.body)
    remaining.used_steps=0
    # A supplied/edited derivative is not accepted just because it remains
    # marked published in the explicit registry IO adapter.
    context['advice'].data={**context['advice'].data,'description':'unapproved derivative drift'}
    with pytest.raises(HTTPException):CONFIGURATION(s.db,s.p,s.task,s.body)

