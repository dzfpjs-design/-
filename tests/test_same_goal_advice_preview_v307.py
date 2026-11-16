"""Real projection/limited prepare/quote/seals with explicit source/DB IO spies.

No actual PG, consent authority, official-price HTTP, SDK, worker or product
durability is proved by these tests. Root verifies the real path separately.
"""
import copy
from contextlib import contextmanager,nullcontext
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_preview_v307 as s
from generic_agents.contracts import Manifest,digest
from generic_agents.models import Evidence
from generic_agents.security import Principal
from tests.test_goal_evaluation_append_command_v297 import predicate
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene,ref
from tests.test_same_goal_advice_projection_v307 import scene as projection_scene

AT=datetime(2026,11,2,12,tzinfo=timezone.utc)


@pytest.fixture
def scene(monkeypatch):
    original=contract_scene();fixture=projection_scene.__wrapped__(monkeypatch)
    frame=fixture['frame'];prep=fixture['preparation'];scope=prep['scope']
    model_data=Manifest(kind='foundation_model',namespace='preview_fixture',name='fixed_model',version='1.0.0',
        owner='explicit registry IO fixture',description='Synthetic published model IO boundary',
        input_schema={'type':'object'},output_schema={'type':'object'},allowed_agent_roles=['business'],
        execution={'provider':'deepseek','model':'deepseek-v4-pro','thinking_mode':'disabled',
            'max_tokens':4096,'secret_ref':'DEEPSEEK_API_KEY'},data_policy={'cloud_allowed':True},
        budget={'max_attempts':1,'max_concurrency':1},dependencies=[],specification={}).model_dump(mode='json')
    model=s.Capability(id=prep['model_pin']['id'],workspace_id=scope['workspace_id'],kind='foundation_model',
        status='published',content_hash=digest(model_data),validated_hash=digest(model_data),release_version='1.0.0',data=model_data)
    source_manifest=copy.deepcopy(original.source);source_manifest['execution']['model_profile_hash']=model.content_hash
    source_pin={**prep['source_business_capability_ref'],'content_hash':digest(source_manifest)}
    manifest=s.c.build_manifest(source_manifest,source_pin)
    prep.update(source_business_capability_ref=source_pin,model_pin=s.store._pin(model),
        advice_capability_ref={**prep['advice_capability_ref'],'content_hash':digest(manifest)})
    prep=s.c.seal_preparation({key:value for key,value in prep.items() if key!='record_hash'},manifest=manifest)
    frame['model_pin']=prep['model_pin'];frame['instruction']=prep['instruction']
    source=frame['source_record']
    source=s.c.seal_source({key:value for key,value in source.items() if key!='record_hash'},preparation=prep,manifest=manifest)
    frame['source_record']=source
    p=Principal(prep['business_actor_id'],scope['workspace_id'],'operator','preview_actor_IO')
    task=s.Task(id=scope['task_id'],workspace_id=p.workspace_id,package_id=scope['package_id'],owner_id=prep['requested_by'],data={})
    plan=s.Plan(id=scope['analysis_plan_id'],workspace_id=p.workspace_id,task_id=task.id,version=2,
        status='awaiting_consent',data={'schema':s.c.PLAN,'preparation_snapshot':prep,'jobs':{'prepare':{}},
            'operations':[],'document_preparation':{'explicit_real_receipt_IO':True},'reviews':[]})
    job=s.Job(id=ref('actual_local_prep_job')['id'],workspace_id=p.workspace_id,kind=s.c.PREPARE_KIND,
        status='succeeded',lease_until=None,data={'task_id':task.id})
    advice=s.Capability(id=prep['advice_capability_ref']['id'],workspace_id=p.workspace_id,kind='tool',
        content_hash=digest(manifest),validated_hash=digest(manifest),release_version='1.0.0',data=manifest)
    state=NS(prep=prep,frame=frame,task=task,plan=plan,p=p,job=job,model=model,advice=advice,
        events=[],clock=AT,locks=[],nonce_calls=[],flushes=0,builds=0,clouds=0,current_prices=0,old_prices=[],allowed=True,
        rows={s.Audit:[],s.Capability:[advice,model]},files={},parts=copy.deepcopy(fixture['evidence_parts']))
    pins=copy.deepcopy(fixture['evidence_pins']);grants=[]
    for index,(pin,ref_value) in enumerate(zip(pins,[source['data_evidence_ref'],source['document']['evidence_ref']])):
        pin.update(version=2,model_profile_id=model.id)
        raw=state.parts[index*2+1]['text'].encode('utf-8')
        file=Evidence(id=ref_value['evidence_id'],workspace_id=p.workspace_id,version=2,sha256=ref_value['sha256'],
            size_bytes=len(raw),media_type='text/plain',data={'cloud_model_ids':[model.id]})
        state.files[file.id]=(file,raw)
        audit=s.Audit(id=ref('consent_'+str(index))['id'],workspace_id=p.workspace_id,actor_id=p.user_id,
            action='evidence.cloud_authorize',object_id=file.id,created_at=AT-timedelta(seconds=1),
            data={'model_id':model.id,'reason':'actual consent IO boundary','sha256':file.sha256,'version':2})
        state.rows[s.Audit].append(audit)
        grants.append({'evidence_id':file.id,'model_profile_id':model.id,'audit_id':audit.id,'audit_hash':s._grant_hash(audit)})
    state.documents={'model_pin':prep['model_pin'],'evidence_pins':pins,'consent_pins':grants,'parts':state.parts}
    price_ref=ref('actual_price_audit')
    price=s.money.seal_price({'schema':s.money.PRICE,'basis_ref':price_ref,
        'official_original':{'url':s.money.OFFICIAL_URL,'sha256':'c'*64,'size_bytes':4096},
        'provider':'deepseek','model':'deepseek-v4-pro','currency':'CNY','rate_unit_tokens':1_000_000,
        'input_cache_miss_minor_per_million':900,'output_minor_per_million':2700,
        'cache_discount_assumed':False,'off_peak_discount_assumed':False,
        'checked_at':(AT-timedelta(hours=1)).isoformat(),'expires_at':(AT+timedelta(hours=1)).isoformat(),
        'database_price_authorization_verified':False,'provider_price_lock_guaranteed':False},now=AT)
    state.price={'price':price,'basis_ref':price_ref,'evidence_ref':{'evidence_id':ref('official_original')['id'],
        'sha256':'c'*64,'size_bytes':4096,'media_type':'text/plain'}}
    evaluation=s.Evaluation(id=source['mature_head']['evaluation_ref']['id'],workspace_id=p.workspace_id,
        task_id=task.id,record_hash=source['mature_head']['evaluation_ref']['record_hash'],generation=source['evaluation_generation'],
        job_id=ref('eval_job')['id'],invocation_id=ref('eval_sdk')['id'],data={'original_query_ref':source['original_query_ref'],
            'evaluation_query_ref':source['evaluation_query_ref'],'cut_at':source['evaluation_cut']})
    eval_job=s.Job(id=evaluation.job_id,workspace_id=p.workspace_id,data={'inputs':{'actual_frozen_eval_IO':True}})
    sdk=s.Invocation(id=evaluation.invocation_id,workspace_id=p.workspace_id,capability_id=ref('eval_cap')['id'],data={})
    eval_cap=s.Capability(id=sdk.capability_id,workspace_id=p.workspace_id,data={})
    objects={(s.Capability,advice.id):advice,(s.Capability,model.id):model,(s.Evaluation,evaluation.id):evaluation,
        (s.Job,eval_job.id):eval_job,(s.Invocation,sdk.id):sdk,(s.Capability,eval_cap.id):eval_cap}
    class DB:
        no_autoflush=nullcontext()
        def scalar(self,statement):
            if 'clock_timestamp' in str(statement):return state.clock
            cls=statement.column_descriptions[0]['entity'];assert cls in {s.Task,s.Plan}
            actual=task if cls is s.Task else plan;state.locks.append((cls,statement._for_update_arg is not None))
            return actual if predicate(statement.whereclause,actual) else None
        def scalars(self,statement):
            cls=statement.column_descriptions[0]['entity'];assert cls is s.Audit
            return [row for row in state.rows[s.Audit] if predicate(statement.whereclause,row)]
        def add(self,row):assert type(row) is s.Audit;state.rows[s.Audit].append(row)
        def flush(self):state.flushes+=1
        @contextmanager
        def begin_nested(self):
            before=list(state.rows[s.Audit]);version=plan.version
            try:yield
            except BaseException:state.rows[s.Audit]=before;plan.version=version;raise
        def commit(self):pytest.fail('caller owns commit')
    state.db=DB()
    def nonce_io(db,principal,*,action,task_id,body,subject_id=None,advice_id=None):
        # Shared base fixture for Preview -> Start -> runtime -> human review.
        # Actual PG lock/foreign-family original inventory is a declared IO
        # boundary; all original family hash/custody/lease guards stay real.
        assert db is state.db and task_id==state.task.id and subject_id==state.plan.id and advice_id is None
        assert action in {s.ACTION,'same_goal_advice.started_v308','same_goal_advice.human_reviewed_v308'}
        assert type(body) is dict and 'request_id' in body
        state.nonce_calls.append({'action':action,'task_id':task_id,'actor_id':principal.user_id,
            'body':copy.deepcopy(body),'subject_id':subject_id,'advice_id':advice_id,'locks_before':len(state.locks)})
    monkeypatch.setattr(s.store.command_nonce,'acquire',nonce_io)
    monkeypatch.setattr(s.store,'_reader',lambda *args,**kwargs:None)
    monkeypatch.setattr(s,'lock_current_actor',lambda *args:state.events.append('current_IAM_IO'))
    monkeypatch.setattr(s.store,'_saved',lambda db,p,actual,record:(prep,job))  # Original Plan/ACCEPTED/Job custody IO.
    monkeypatch.setattr(s.store,'_fresh',lambda db,p,cls,identity:objects[cls,identity])
    def build(*args):
        state.builds+=1
        if not state.allowed:raise HTTPException(409,'actual current source IO rejected')
        return copy.deepcopy(frame)
    monkeypatch.setattr(s.frames,'build_current',build)
    def cloud(*args):state.clouds+=1;return copy.deepcopy(state.documents)
    monkeypatch.setattr(s.sources,'cloud_documents',cloud)
    monkeypatch.setattr(s,'published',lambda *args:model)
    def current(*args):state.current_prices+=1;return copy.deepcopy(state.price)
    def historical(db,p,pin,*,basis_ref,priced_at):
        assert pin==prep['model_pin'] and basis_ref==price_ref and AT<=priced_at<=state.clock
        state.old_prices.append(priced_at);return copy.deepcopy(state.price)
    monkeypatch.setattr(s.prices,'current',current);monkeypatch.setattr(s.prices,'checked_original',historical)
    monkeypatch.setattr(s.evidence,'evidence_bytes',lambda db,p,identifier:state.files[identifier])
    monkeypatch.setattr(s.documents,'cold_read',lambda *args:{'document':source['document']})
    monkeypatch.setattr(s.packets,'saved_result',lambda *args:{'evaluation_ref':source['mature_head']['evaluation_ref'],
        'packet':{'evidence_id':source['data_evidence_ref']['evidence_id']}})
    state.body={'request_id':ref('preview_nonce')['id'],'expected_plan_version':plan.version,
        'expected_goal_hash':scope['goal_hash'],'expected_mature_head_hash':prep['mature_head']['head_hash'],
        'reason':'核对实际匿名全文与本次费用上限'}
    state.prepare=lambda body=None:s.prepare(state.db,state.p,task.id,plan.id,body or state.body)
    state.get=lambda request_id=None:s.get(state.db,state.p,task.id,plan.id,request_id)
    return state


def test_actual_anonymous_request_limited_options_quote_and_audit_are_saved_without_sdk_send_or_plan_data_mutation(scene):
    t=scene;before=copy.deepcopy(t.plan.data);view=t.prepare();events=[row for row in t.rows[s.Audit] if row.action==s.ACTION]
    assert len(events)==1 and t.plan.version==3 and t.plan.data==before and t.plan.status=='awaiting_consent'
    assert view['options']['max_tokens']==1024<t.model.data['execution']['max_tokens']
    assert view['can_start'] is False and view['cloud_send_authorized'] is False and view['preview_generated_cloud_calls']==0
    assert view['maximum_cost_minor_units']<=view['configured_ceiling_minor_units'] and t.flushes==1
    assert t.locks[:2]==[(s.Task,True),(s.Plan,True)]
    assert t.nonce_calls==[{'action':s.ACTION,'task_id':t.task.id,'actor_id':t.p.user_id,
        'body':t.body,'subject_id':t.plan.id,'advice_id':None,'locks_before':0}]
    rendered=s.c.base._json(view).lower()
    assert t.prep['scope']['analysis_plan_id'] not in s.c.base._json(view['messages']).lower()
    assert not {'aliases','projection','frame_snapshot','source_record','model_snapshot','secret_ref'} & set(view)
    assert events[0].data['record_hash']==s.c.record_hash(events[0].data)
    assert view==t.get() and t.current_prices==1


def test_lost_response_and_expired_price_read_original_without_live_frame_new_grants_or_price(scene):
    t=scene;view=t.prepare();t.clock=AT+timedelta(hours=3);t.allowed=False
    for file,_raw in t.files.values():file.data['cloud_model_ids']=[];file.version+=1
    assert t.prepare()==view and t.get()==view
    assert t.builds==1 and t.clouds==1 and t.current_prices==1 and len(t.old_prices)==2
    t.plan.status='cancelled';t.plan.version+=1
    assert t.get()['preview_hash']==view['preview_hash']


def test_explicit_next_preview_is_one_linear_latest_candidate_and_old_nonce_stays_original(scene):
    t=scene;first=t.prepare();t.clock+=timedelta(seconds=1)
    second=t.prepare({**t.body,'request_id':ref('next_preview')['id'],'expected_plan_version':3})
    assert second['preview_generation']==2 and second['latest_for_plan'] is True
    historical=t.get(t.body['request_id'])
    assert historical['preview_generation']==1 and historical['latest_for_plan'] is False
    assert historical['preview_hash']==first['preview_hash'] and historical['phase']=='historical_preview'
    assert t.plan.version==4 and len([row for row in t.rows[s.Audit] if row.action==s.ACTION])==2


@pytest.mark.parametrize('fault',['version','goal','head','cancelled','job_running','actor','source','missing_grant'])
def test_invalid_current_command_or_authority_never_creates_preview_or_advances_plan(scene,fault):
    t=scene;body=copy.deepcopy(t.body)
    if fault=='version':body['expected_plan_version']=1
    if fault=='goal':body['expected_goal_hash']='0'*64
    if fault=='head':body['expected_mature_head_hash']='0'*64
    if fault=='cancelled':t.plan.status='cancelled'
    if fault=='job_running':t.job.status='running'
    if fault=='actor':t.p=Principal(t.prep['data_actor_id'],t.p.workspace_id,'operator','actual_data_post_IO')
    if fault=='source':t.allowed=False
    if fault=='missing_grant':t.documents['consent_pins'].pop()
    with pytest.raises(HTTPException):t.prepare(body)
    assert t.plan.version==2 and not [row for row in t.rows[s.Audit] if row.action==s.ACTION]


@pytest.mark.parametrize('fault',['modified_body','broken_chain','wrong_task','missing_consent_audit','changed_original'])
def test_readback_is_original_and_failclosed_without_refilling_or_repricing(scene,fault):
    t=scene;t.prepare();event=next(row for row in t.rows[s.Audit] if row.action==s.ACTION)
    if fault=='modified_body':event.data['prepared']['options']['max_tokens']=8192
    if fault=='broken_chain':event.data['previous_preview_record_hash']='0'*64;event.data['record_hash']=s.c.record_hash(event.data)
    if fault=='wrong_task':event.data['task_id']=ref('other_task')['id'];event.data['record_hash']=s.c.record_hash(event.data)
    if fault=='missing_consent_audit':t.rows[s.Audit]=[row for row in t.rows[s.Audit] if row.action==s.ACTION]
    if fault=='changed_original':
        identifier=event.data['source_record']['data_evidence_ref']['evidence_id'];file,raw=t.files[identifier]
        t.files[identifier]=(file,raw+b'changed')
    with pytest.raises(HTTPException):t.get()
    assert t.builds==1 and t.clouds==1 and t.current_prices==1
