"""Real PG/registered SDK/native pixel evidence; ONLY planning HTTP is synthetic.

CloudCall budgets/dispatch/first-answer callbacks and source/ACK are production.
No public network/real payment/LLM quality claim. Root owns actual paid journey.
"""
import copy,json
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.models import DataRun,Job,Invocation,CloudCall,Audit,Memory,Capability,DomainInstance
from generic_agents.worker import process_once
from generic_agents import pixel_question_service_v252 as service
from generic_agents import pixel_question_contracts_v252 as contracts
from tests.test_workflow_pixel_native_pg_v250 import flow_scene,native_scene,ack,good,pytestmark
from tests.test_vertical_slice import publish

ROOT=Path(__file__).resolve().parents[1]

def candidate(question='查看影像成果',operation='read',metric=None,reception='any',quote='查看'):
    return {'status':'candidate','ir':{'operation':operation,'metric':metric,'group_by':'none',
        'time':{'basis':'machine_result_registered','registration_days':None,'timezone':'Asia/Shanghai'},
        'filters':{'reception':reception,'human_completion':'any'},
        'grounding':{'operation_quote':quote,'metric_quote':'候选像素' if metric else None,'time_quote':None,
            'filter_quotes':['已接收'] if reception=='received' and '已接收' in question else []},'limitations':[]},'explanation':'仅按登记和接收记录核对影像候选成果。'}

@pytest.fixture
def question_scene(flow_scene,monkeypatch):
    s=flow_scene
    profile=json.loads((ROOT/'examples/qwen.json').read_text());profile.update(namespace='public_pixel_question',name='planner')
    model=publish(s.client,profile)
    f=NS(**vars(s),model=model,question_base=s.base+'/pixel-questions',posts=[],reply=candidate(),post_error=None)
    monkeypatch.setenv('DASHSCOPE_API_KEY','public-v252-test-noncredential')
    # This fixture owns an explicit synthetic budget. Production/runner zero
    # limits remain unchanged outside monkeypatch's bounded lifetime. The real
    # reservation, tracked ledger and charging guards are still exercised.
    from generic_agents.config import settings
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',4)
    monkeypatch.setattr(settings(),'cloud_daily_token_limit',131072)
    import generic_agents.cloud_business_reasoning_v197 as transport
    class HTTP:
        def __init__(self,**options):
            assert options['follow_redirects'] is False and options['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def post(self,url,*,headers,json):
            assert url==transport.ENDPOINTS['qwen'];f.posts.append(copy.deepcopy(json))
            if f.post_error is not None:raise f.post_error
            envelope={'choices':[{'finish_reason':'stop','message':{'content':__import__('json').dumps(f.reply,ensure_ascii=False)}}],
                'usage':{'prompt_tokens':100,'completion_tokens':100,'total_tokens':200}}
            return NS(status_code=200,headers={'x-request-id':'public-v252-'+str(len(f.posts))},content=__import__('json').dumps(envelope,ensure_ascii=False).encode())
    monkeypatch.setattr(transport.httpx,'Client',HTTP)
    return f

def ask(s,question='查看影像成果',*,request_id=None):
    opts=good(s.client.get(s.question_base+'/options'));assert opts['can_ask'],opts['reasons']
    value={'schema':'pixel-question-preview-input-v252','release_id':opts['release_id'],'task_id':s.task_id,
        'entity_id':s.entity['id'],'question':question,'model_profile_id':s.model['id'],'options_hash':opts['options_hash']}
    shown=good(s.client.post(s.question_base+'/preview',json=value))
    body={**value,'schema':'pixel-question-submit-v252','request_id':request_id or '提问/原号?252-'+uuid4().hex,
        'preview_hash':shown['preview_hash'],'confirm_cloud':True}
    created=good(s.client.post(s.question_base,json=body),201)
    return body,created

def query_job(s,query_id):
    with Session(engine()) as db:
        jobs=list(db.scalars(select(Job).where(Job.workspace_id==s.workspace_id,Job.kind=='data_query',Job.data['query_id'].as_string()==query_id)))
        return sorted(jobs,key=lambda j:j.created_at)[-1].id

def finish_query(s,query_id):
    assert process_once(job_id=query_job(s,query_id),deliver=False)
    return good(s.client.get(s.question_base+'/'+query_id))

def counts(s):
    with Session(engine()) as db:
        return {model.__tablename__:len(list(db.scalars(select(model).where(model.workspace_id==s.workspace_id))))
            for model in (DataRun,Job,Invocation,CloudCall,Audit)}

def test_actual_app_paths_and_real_generation_dependency_catalog(question_scene):
    s=question_scene;paths=s.client.app.openapi()['paths'];prefix='/api/workspace/instances/{instance_id}/pixel-questions'
    assert prefix+'/options' in paths and prefix+'/{query_id}/commands/requests' in paths
    assert not any('/api/workspace/api/workspace' in path for path in paths)
    with Session(engine()) as db:
        cap=db.get(Capability,s.release['package_id']);spec=cap.data['specification']
        assert len(spec['native_query_catalog']['tools'])==3
        facet=spec['pixel_question_catalog_v252'];assert len(facet['tools'])==1
        pin=facet['tools'][0];assert pin['id'] in cap.data['dependencies'] and pin['id'] in spec['data_tool_ids']
        from generic_agents.data_tools import choices
        assert pin['id'] not in {x.id for x in choices(db,s.p,[s.entity['id']])}
        from generic_agents.native_query_catalog import catalog
        assert len(catalog(db,s.p,cap))==3


def test_real_single_job_sdk_waiting_ack_and_frozen_old_answer(question_scene):
    s=question_scene;body,created=ask(s);waiting=finish_query(s,created['id'])
    assert waiting['status']=='succeeded',{'status':waiting['status'],'error':waiting['error'],'planning_state':waiting['planning']['state']}
    assert waiting['answer']['state_counts']['registered_waiting']==1
    assert waiting['answer']['state_counts']['received_results']==0
    assert waiting['answer']['candidate_only'] and not waiting['answer']['field_truth_verified']
    assert len(s.posts)==1
    transmitted=json.dumps(s.posts[0],ensure_ascii=False)
    assert s.raw_before.hex()[:80] not in transmitted and s.source_before['sha256'] not in transmitted
    assert 'candidate_pixels' in transmitted and 'sources' not in json.loads(s.posts[0]['messages'][-1]['content'])
    before=copy.deepcopy(waiting);ack(s)
    assert good(s.client.get(s.question_base+'/'+created['id']))==before
    question='已接收成果的候选像素总和';s.reply=candidate(question,'sum','candidate_pixels','received','总和')
    _,new=ask(s,question);received=finish_query(s,new['id'])
    assert received['status']=='succeeded',{'status':received['status'],'error':received['error'],'planning_state':received['planning']['state']}
    assert received['answer']['state_counts']['received_results']==1
    with Session(engine()) as db:
        from generic_agents.models import WorkflowPixelRun
        run=db.get(WorkflowPixelRun,s.run_id);expected=run.data['result']['candidate_pixels']
        query=db.get(DataRun,new['id']);state=query.data[service.KEY]
        call=db.get(Invocation,received['tool']['invocation_id'])
        assert call.status=='succeeded' and call.data['output']==received['answer']
        assert received['answer']['groups'][0]['value_text']==str(expected)
        assert state['source_snapshot']['records'][0]['reception']['memory']['id']
        ledger=db.get(CloudCall,received['planning']['cloud_call_id'])
        assert ledger.charged_tokens==200 and ledger.data['gateway_dispatch']['identity']['suggestion_id']==query.id
        assert len(list(db.scalars(select(WorkflowPixelRun).where(WorkflowPixelRun.workspace_id==s.workspace_id))))==1


def test_unknown_planning_call_cannot_redispatch_or_lose_original(question_scene):
    s=question_scene;s.post_error=httpx.ReadTimeout('synthetic unknown after transport entry')
    body,created=ask(s);failed=finish_query(s,created['id'])
    assert failed['status']=='unknown_model' and not failed['actions']['can_retry'] and len(s.posts)==1
    before=counts(s)
    recovered=good(s.client.get(s.question_base+'/requests',params={'request_id':body['request_id'],'expected_body_hash':digest(body)}))
    assert recovered['found'] and recovered['query']['id']==created['id']
    repeated=good(s.client.post(s.question_base,json=body),201);assert repeated['id']==created['id']
    command={'schema':'pixel-question-command-v252','request_id':'恢复/未知?252-'+uuid4().hex,'version':failed['version'],
        'reason':'原请求超时后核对','operation':'retry','expected_request_hash':digest(body)}
    assert s.client.post(s.question_base+'/'+created['id']+'/retry',json=command).status_code==409
    assert counts(s)==before and len(s.posts)==1


def test_history_summary_does_not_scan_sources_and_pause_allows_readonly_nonce(question_scene,monkeypatch):
    s=question_scene;body,created=ask(s);finished=finish_query(s,created['id']);before=counts(s)
    import generic_agents.pixel_question_sources_v252 as sources
    def forbidden(*a,**kw):raise AssertionError('history summary must not hash every original')
    with monkeypatch.context() as patch:
        patch.setattr(sources,'verify_saved_snapshot',forbidden)
        listing=good(s.client.get(s.question_base));assert listing['records'][0]['has_saved_answer']
        assert 'answer' not in listing['records'][0]
    with Session(engine()) as db:version=db.get(DomainInstance,s.instance_id).version
    good(s.client.post('/api/instances/'+s.instance_id+'/pause',json={'version':version}))
    paused_counts=counts(s);assert paused_counts['audit_events']==before['audit_events']+1
    assert good(s.client.get(s.question_base+'/requests',params={'request_id':body['request_id'],'expected_body_hash':digest(body)}))['found']
    assert good(s.client.post(s.question_base,json=body),201)['id']==created['id']
    assert counts(s)==paused_counts
    assert good(s.client.get(s.question_base+'/options'))['can_ask'] is False


def test_bad_received_memory_hides_saved_numbers_without_mutating_query(question_scene):
    s=question_scene;ack(s);body,created=ask(s);finished=finish_query(s,created['id']);assert finished['answer']
    with Session(engine()) as db:
        row=db.get(DataRun,created['id']);saved=(row.version,digest(row.data));memory_id=row.data[service.KEY]['source_snapshot']['records'][0]['reception']['memory']['id']
        memory=db.get(Memory,memory_id);memory.data={**memory.data,'explicit_test_corruption':True};db.commit()
    before=counts(s);blocked=good(s.client.get(s.question_base+'/'+created['id']))
    assert blocked['status']=='blocked_integrity' and blocked['answer'] is None and not blocked['actions']['can_retry']
    assert blocked['tool']['status']=='succeeded' and blocked['tool']['output_hash']==finished['tool']['output_hash']
    with Session(engine()) as db:
        row=db.get(DataRun,created['id']);assert (row.version,digest(row.data))==saved
    assert counts(s)==before


def test_cancel_and_original_command_recovery_never_query_or_cloud(question_scene):
    s=question_scene;body,created=ask(s);command={'schema':'pixel-question-command-v252','request_id':'取消/保留?252-'+uuid4().hex,
        'version':created['version'],'reason':'不再查询但保留原问题','operation':'cancel','expected_request_hash':digest(body)}
    path=s.question_base+'/'+created['id'];cancelled=good(s.client.post(path+'/cancel',json=command))
    assert cancelled['query']['status']=='cancelled' and not s.posts
    before=counts(s);lookup=good(s.client.get(path+'/commands/requests',params={'request_id':command['request_id'],'expected_body_hash':digest(command)}))
    assert lookup['found'] and lookup['result']==cancelled
    assert good(s.client.post(path+'/cancel',json=command))==cancelled
    changed={**command,'reason':'不能换原正文'};assert s.client.post(path+'/cancel',json=changed).status_code==409
    assert counts(s)==before and not s.posts

@pytest.mark.parametrize('kind',['creation','plan','source','marker'])
def test_independent_anchors_cannot_be_stripped_into_an_ordinary_query(question_scene,kind):
    s=question_scene;ack(s);body,created=ask(s);finished=finish_query(s,created['id'])
    with Session(engine()) as db:
        row=db.get(DataRun,created['id'])
        if kind=='marker':row.data={k:v for k,v in row.data.items() if k!=service.KEY}
        else:
            action={'creation':service.CREATED,'plan':service.PLANNED,'source':service.SOURCED}[kind]
            proof=db.scalar(select(Audit).where(Audit.workspace_id==s.workspace_id,Audit.object_id==row.id,Audit.action==action));db.delete(proof)
        db.commit()
    before=counts(s);assert s.client.get(s.question_base+'/'+created['id']).status_code==409
    assert counts(s)==before and len(s.posts)==1


def test_cancellation_commits_before_source_checkpoint_and_prevents_sdk_and_outcome(question_scene,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    s=question_scene;body,created=ask(s);entered=Event();release=Event();actual=service._capture_source
    def before_capture(db,p,row,job):
        entered.set();assert release.wait(15),'bounded test barrier was not released'
        return actual(db,p,row,job)
    monkeypatch.setattr(service,'_capture_source',before_capture)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(process_once,job_id=query_job(s,created['id']),deliver=False)
        try:
            if not entered.wait(15):pending.result(timeout=2)
            assert entered.is_set()
            current=good(s.client.get(s.question_base+'/'+created['id']))
            command={'schema':'pixel-question-command-v252','request_id':'并发/取消?252-'+uuid4().hex,
                'version':current['version'],'reason':'来源捕获之前明确停止','operation':'cancel','expected_request_hash':digest(body)}
            cancelled=good(s.client.post(s.question_base+'/'+created['id']+'/cancel',json=command))
            assert cancelled['query']['status']=='cancelled'
        finally:release.set()
        assert pending.result(timeout=20)
    final=good(s.client.get(s.question_base+'/'+created['id']));assert final['status']=='cancelled' and final['source'] is None and final['answer'] is None
    assert len(s.posts)==1 and final['planning']['response_sha256']
    with Session(engine()) as db:
        assert not list(db.scalars(select(Invocation).where(Invocation.workspace_id==s.workspace_id,
            Invocation.idempotency_key.startswith('pixel-question-tool-v252:'+created['id']+':'))))
        assert not list(db.scalars(select(Audit).where(Audit.workspace_id==s.workspace_id,Audit.object_id==created['id'],Audit.action.in_([service.SOURCED,service.OUTCOME]))))


def test_worker_outcome_row_lock_serializes_cancel_instead_of_overwriting_command(question_scene,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    s=question_scene;body,created=ask(s);entered=Event();release=Event();actual=service._audit
    def before_outcome(db,p,action,identity,data):
        if action==service.OUTCOME:
            entered.set();assert release.wait(15),'bounded test barrier was not released'
        return actual(db,p,action,identity,data)
    monkeypatch.setattr(service,'_audit',before_outcome)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker=pool.submit(process_once,job_id=query_job(s,created['id']),deliver=False)
        try:
            if not entered.wait(15):worker.result(timeout=2)
            assert entered.is_set()
            # Only MVCC reads while producer holds its real DataRun EX.
            current=good(s.client.get(s.question_base+'/'+created['id']))
            command={'schema':'pixel-question-command-v252','request_id':'并发/终结?252-'+uuid4().hex,
                'version':current['version'],'reason':'与真实终结并发的取消','operation':'cancel','expected_request_hash':digest(body)}
            cancel=pool.submit(s.client.post,s.question_base+'/'+created['id']+'/cancel',json=command)
            assert not cancel.done()
        finally:release.set()
        assert worker.result(timeout=20)
        assert cancel.result(timeout=20).status_code==409
    final=good(s.client.get(s.question_base+'/'+created['id']));assert final['status']=='succeeded'
    with Session(engine()) as db:
        assert len(list(db.scalars(select(Audit).where(Audit.workspace_id==s.workspace_id,Audit.object_id==created['id'],Audit.action==service.OUTCOME))))==1
        assert not list(db.scalars(select(Audit).where(Audit.workspace_id==s.workspace_id,Audit.object_id==created['id'],Audit.action==service.COMMAND)))


def test_request_lookup_is_not_limited_to_recent100_and_is_actually_readonly_when_paused(question_scene):
    s=question_scene;body,old=ask(s)
    for _ in range(101):
        another={**body,'request_id':'历史/原号?252-'+uuid4().hex}
        good(s.client.post(s.question_base,json=another),201)
    with Session(engine()) as db:version=db.get(DomainInstance,s.instance_id).version
    good(s.client.post('/api/instances/'+s.instance_id+'/pause',json={'version':version}))
    before=counts(s)
    listing=good(s.client.get(s.question_base));assert old['id'] not in {v['id'] for v in listing['records']}
    restored=good(s.client.get(s.question_base+'/requests',params={'request_id':body['request_id'],'expected_body_hash':digest(body)}))
    assert restored['found'] and restored['query']['id']==old['id'] and restored['query']['request']==body
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        readonly=service.lookup(db,s.p,s.instance_id,body['request_id'],digest(body));assert readonly==restored
    assert counts(s)==before and not s.posts
    unknown=good(s.client.get(s.question_base+'/requests',params={'request_id':'未见/原号?252-'+uuid4().hex,'expected_body_hash':digest(body)}))
    assert not unknown['found'] and unknown['state']=='unknown' and unknown['query'] is None


def test_direct_registered_invocation_without_original_owned_data_query_is_rejected(question_scene):
    s=question_scene;opts=good(s.client.get(s.question_base+'/options'));body,created=ask(s)
    from generic_agents.gateway import invoke
    with Session(engine()) as db:
        with pytest.raises(HTTPException):invoke(db,s.p,opts['catalog']['tool']['id'],'data',
            {'query_id':created['id'],'plan_hash':'a'*64},'untrusted-direct-call-'+uuid4().hex)
        db.rollback()
    assert not s.posts
    with Session(engine()) as db:
        assert not list(db.scalars(select(Invocation).where(Invocation.workspace_id==s.workspace_id,
            Invocation.capability_id==opts['catalog']['tool']['id'])))


def test_ordinary_query_api_is_reference_only_and_cannot_rewrite_pixel_family(question_scene):
    s=question_scene;body,created=ask(s);before=counts(s)
    ordinary=good(s.client.get('/api/data/queries/'+created['id']))
    assert ordinary['reference_only'] and ordinary['family']==service.KEY and 'data' not in ordinary
    assert ordinary['context']['detail_path']==s.question_base+'/'+created['id']
    assert s.client.put('/api/data/queries/'+created['id']+'/draft',json={
        'expected_version':created['version'],'reason':'不能通过通用字段查询改写','draft':{'question':'换成普通字段'}}).status_code==409
    assert s.client.post('/api/data/queries/'+created['id']+'/cancel',json={
        'expected_version':created['version'],'reason':'不能绕专属原号取消'}).status_code==409
    assert counts(s)==before and not s.posts
    with Session(engine()) as db:
        row=db.get(DataRun,created['id']);row.data={k:v for k,v in row.data.items() if k!=service.KEY};db.commit()
    assert s.client.get('/api/data/queries/'+created['id']).status_code==409


def test_explicit_retry_reuses_paid_capture_source_and_successful_sdk_after_parent_failure(question_scene,monkeypatch):
    s=question_scene;body,created=ask(s);actual=service._audit;failed_once=[]
    def fail_before_outcome(db,p,action,identity,data):
        if action==service.OUTCOME and not failed_once:
            failed_once.append(True);raise ValueError('明确测试故障：真实SDK已成功，原父查询尚未保存答案')
        return actual(db,p,action,identity,data)
    with monkeypatch.context() as patch:
        patch.setattr(service,'_audit',fail_before_outcome)
        failed=finish_query(s,created['id'])
    assert failed['status']=='blocked_integrity' and failed['tool']['status']=='succeeded' and failed['answer'] is None
    old_job=query_job(s,created['id']);old_tool=failed['tool']['invocation_id'];source=copy.deepcopy(failed['source'])
    command={'schema':'pixel-question-command-v252','request_id':'显式/恢复?252-'+uuid4().hex,'version':failed['version'],
        'reason':'恢复原已保存回答和本机查询，不重新发送模型','operation':'retry','expected_request_hash':digest(body)}
    resumed=good(s.client.post(s.question_base+'/'+created['id']+'/retry',json=command));assert resumed['query']['status']=='queued'
    new_job=query_job(s,created['id']);assert new_job!=old_job
    success=finish_query(s,created['id']);assert success['status']=='succeeded' and success['tool']['invocation_id']==old_tool
    assert success['source']==source and len(s.posts)==1
    with Session(engine()) as db:
        assert db.get(Job,old_job).status=='failed' and db.get(Job,new_job).status=='succeeded'
        retry_job=db.get(Job,new_job)
        assert retry_job.data['pixel_question_cached_only'] is True
        assert retry_job.data['recovery_only'] is False  # claim's dynamic exhaustion flag
        queued=db.scalar(select(Audit).where(Audit.workspace_id==s.workspace_id,Audit.object_id==created['id'],
            Audit.action==service.QUEUED,Audit.data['job_id'].as_string()==new_job))
        assert queued.data['recovery_only'] is True  # immutable original local-only intent
        assert len(list(db.scalars(select(CloudCall).where(CloudCall.workspace_id==s.workspace_id))))==1
        assert len(list(db.scalars(select(Audit).where(Audit.workspace_id==s.workspace_id,Audit.object_id==created['id'],Audit.action==service.SOURCED))))==1
        assert len(list(db.scalars(select(Invocation).where(Invocation.workspace_id==s.workspace_id,
            Invocation.idempotency_key.startswith('pixel-question-tool-v252:'+created['id']+':')))))==1


def test_preview_day_is_frozen_across_midnight_and_history_never_needs_current_model_or_secret(question_scene,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from generic_agents.config import settings
    s=question_scene;first=datetime(2026,10,30,12,0,tzinfo=timezone.utc)
    monkeypatch.setattr(service.m,'now',lambda:first)
    question='查看今天登记的影像成果';s.reply=candidate(question,quote='查看')
    s.reply['ir']['time']['registration_days']=['2026-10-30'];s.reply['ir']['grounding']['time_quote']='今天登记'
    body,created=ask(s,question)
    monkeypatch.setattr(service.m,'now',lambda:first+timedelta(days=1))
    final=finish_query(s,created['id']);assert final['status']=='succeeded'
    assert final['plan']['time']['registration_days']==['2026-10-30']
    assert final['answer']['time']['registration_days']==['2026-10-30']
    # Explicit negative fixture: current registry availability is revoked, not
    # a fake publication or changed historical model content/receipt.
    with Session(engine()) as db:
        model=db.get(Capability,s.model['id']);model.status='draft';db.commit()
    monkeypatch.delenv('DASHSCOPE_API_KEY',raising=False)
    monkeypatch.setattr(settings(),'dashscope_api_key',None)
    monkeypatch.setattr(settings(),'cloud_daily_call_limit',0)
    monkeypatch.setattr(settings(),'cloud_daily_token_limit',0)
    before=counts(s)
    assert good(s.client.get(s.question_base+'/'+created['id']))==final
    assert good(s.client.get(s.question_base+'/requests',params={'request_id':body['request_id'],'expected_body_hash':digest(body)}))['query']==final
    assert counts(s)==before and len(s.posts)==1


def test_database_source_failure_has_readable_saved_error_and_explicit_cached_retry(question_scene,monkeypatch):
    s=question_scene;body,created=ask(s)
    import generic_agents.pixel_question_sources_v252 as sources
    def actual_readonly_sql_error(db,p,instance_id,**kwargs):
        # Real PostgreSQL transaction error, no fabricated receipt or success.
        # It occurs after the paid first answer/validated plan were committed.
        db.execute(text('SELECT pixel_question_expected_missing_function_v252()'))
        raise AssertionError('the isolated database must not define this test function')
    with monkeypatch.context() as patch:
        patch.setattr(sources,'capture',actual_readonly_sql_error)
        failed=finish_query(s,created['id'])
    assert failed['status']=='failed' and failed['planning']['state']=='parsed'
    assert failed['error'] and '原问题' in failed['error'] and '模型回答' in failed['error']
    assert 'pixel_question_expected_missing_function' not in failed['error'] and 'SELECT' not in failed['error']
    assert failed['actions']['can_retry'] and failed['plan'] is not None and failed['source'] is None
    assert len(s.posts)==1 and failed['planning']['response_sha256']
    old_job=query_job(s,created['id'])
    with Session(engine()) as db:
        row=db.get(DataRun,created['id']);saved=(row.version,digest(row.data))
        assert row.data[service.KEY]['error'] is None and row.data.get('error')
        assert db.get(Job,old_job).status=='failed'
    before=counts(s)
    assert good(s.client.get(s.question_base+'/'+created['id']))==failed
    recovery=good(s.client.get(s.question_base+'/requests',params={'request_id':body['request_id'],'expected_body_hash':digest(body)}))
    assert recovery['found'] and recovery['query']==failed
    with Session(engine()) as db:
        row=db.get(DataRun,created['id']);assert (row.version,digest(row.data))==saved
    assert counts(s)==before
    command={'schema':'pixel-question-command-v252','request_id':'数据库/恢复?252-'+uuid4().hex,'version':failed['version'],
        'reason':'修复来源查询后恢复原已保存规划，不重新发送模型','operation':'retry','expected_request_hash':digest(body)}
    resumed=good(s.client.post(s.question_base+'/'+created['id']+'/retry',json=command))
    assert resumed['query']['status']=='queued'
    new_job=query_job(s,created['id']);assert new_job!=old_job
    completed=finish_query(s,created['id'])
    assert completed['status']=='succeeded',{'status':completed['status'],'error':completed['error']}
    assert completed['planning']['cloud_call_id']==failed['planning']['cloud_call_id']
    assert completed['planning']['response_sha256']==failed['planning']['response_sha256'] and len(s.posts)==1
    with Session(engine()) as db:
        assert db.get(Job,old_job).status=='failed' and db.get(Job,new_job).status=='succeeded'
        assert len(list(db.scalars(select(CloudCall).where(CloudCall.workspace_id==s.workspace_id))))==1
        assert len(list(db.scalars(select(Audit).where(Audit.workspace_id==s.workspace_id,
            Audit.object_id==created['id'],Audit.action==service.SOURCED))))==1


def test_prepared_model_messages_contain_semantics_without_platform_ids_or_human_notes(question_scene):
    s=question_scene;options=good(s.client.get(s.question_base+'/options'))
    human_note='只应留在本地的人工作业说明-V252隐私边界'
    task=good(s.client.get('/api/workspace/tasks/'+s.task_id))
    good(s.client.put('/api/workspace/tasks/'+s.task_id+'/note',json={
        'version':task['version'],'step_id':task['workflow_runtime']['current_step_id'],'note':human_note}))
    body={'schema':'pixel-question-preview-input-v252','release_id':options['release_id'],'task_id':s.task_id,
        'entity_id':s.entity['id'],'question':'查看影像成果','model_profile_id':s.model['id'],'options_hash':options['options_hash']}
    before=counts(s);preview=good(s.client.post(s.question_base+'/preview',json=body))
    messages=json.dumps(preview['messages'],ensure_ascii=False)
    for private in [s.workspace_id,s.instance_id,s.release['id'],s.task_id,s.entity['id'],s.p.user_id,s.model['id'],
        options['catalog']['tool']['id'],options['catalog']['tool']['hash'],s.source_before['sha256'],s.source_after['sha256'],human_note]:
        assert private not in messages
    rules=json.loads(preview['messages'][1]['content']);selected=json.loads(preview['messages'][-1]['content'])
    assert 'tool' not in rules['catalog']
    assert set(selected['selected_scope'])=={'instance','task_limited','entity_limited'}
    assert selected['selected_scope']['task_limited'] is True and selected['selected_scope']['entity_limited'] is True
    assert preview['input']==body and not s.posts and counts(s)==before
    # Stored/future reprepare is idempotent with the already sanitized catalog;
    # no fake IDs are needed from the model. Full scope remains local in body.
    reference=rules['reference_registration_day']
    again=service._messages({**body,'instance_id':s.instance_id},rules['catalog'],reference)
    from generic_agents.cloud_business_reasoning_v197 import prepare
    with Session(engine()) as db:model=db.get(Capability,s.model['id']);prepared=prepare(model,again,contracts.model_result_schema())
    assert prepared['messages']==preview['messages']
