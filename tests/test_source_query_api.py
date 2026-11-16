"""Same real DataRun/data_query worker, with genuine partial/doc provider IO.

Media/model transport and legacy graph admission seams are inherited explicitly
from the provider suite. No successful query Job, answer or receipt is inserted.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException,Response
from starlette.requests import Request
from sqlalchemy import func,select
from sqlalchemy.orm import Session
import pytest
from generic_agents import models as m
from generic_agents import source_query_service as service, source_query_api as api
from generic_agents.source_query_contracts import MARKER,CREATE_AUDIT,RESULT_AUDIT,COMMAND_AUDIT,MAX_BODY
from generic_agents.worker import run_setup_job
from tests.test_instance_material_sources_v239 import (partial_material,formal_document,
    frame_supplement_case,daily_frame_source,daily_scene,daily_camera_source,
    media_runtime,mocked_runtime,explicit_no_alert_fixture)

ROOT='/api/v2/data/queries'


def raw(body):return json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()


def body(case,source_scope=None,question='为什么本次8秒补采没有完成？'):
    scope=source_scope or case.scope
    response=case.client.get(ROOT+'/catalog',params={'instance_id':scope['instance_id'],'batch_day':scope['batch_day']})
    assert response.status_code==200,response.text[:300]
    return {'schema':'data-query-create-v2','request_id':str(uuid4()),'question':question,
        'expected_catalog_hash':response.json()['catalog_hash'],'source_scope':copy.deepcopy(scope),'parent_query_id':None}


def post(case,path,value):return case.client.post(path,content=raw(value),headers={'Content-Type':'application/json'})


def create(case,**kwargs):
    value=body(case,**kwargs);response=post(case,ROOT,value)
    assert response.status_code==201,response.text[:350]
    return value,response.json()['query']


def finish(case,query):
    assert query['status']=='queued' and query['job']['status']=='queued'
    assert run_setup_job(query['job']['id'])==0
    response=case.client.get(ROOT+'/'+query['id']);assert response.status_code==200,response.text[:400]
    return response.json()


def test_real_partial_answer_original_request_resume_note_and_v1_read_only_reference(partial_material):
    case=partial_material;value,query=create(case)
    assert post(case,ROOT,value).status_code==200
    recovered=case.client.get(ROOT+'/requests/'+value['request_id'],params={'expected_sha256':hashlib.sha256(raw(value)).hexdigest()})
    assert recovered.status_code==200 and recovered.json()['found'] is True
    query=finish(case,query)
    assert query['status']=='insufficient' and query['answer']['intent']=='incomplete_capture'
    text=' '.join(item['text'] for item in query['answer']['findings'])
    assert '2.4秒' in text and '8秒' in text and '独立接收' in text
    assert query['answer']['coverage']['expected_sources_verified'] is False
    before=copy.deepcopy(query['answer']);old_hash=query['result_hash']
    note={'schema':'data-query-note-v2','request_id':str(uuid4()),'version':query['version'],
        'text':'人工解释：此次仅为技术试运行，不能代表现场全天状态。'}
    saved=post(case,ROOT+'/'+query['id']+'/notes',note)
    assert saved.status_code==200 and saved.json()['query']['notes'][0]['text']==note['text']
    assert post(case,ROOT+'/'+query['id']+'/notes',note).status_code==200
    fresh=case.client.get(ROOT+'/'+query['id']).json()
    assert fresh['answer']==before and fresh['result_hash']==old_hash
    listed=case.client.get(ROOT,params={'instance_id':case.scope['instance_id']})
    assert listed.status_code==200 and [item['id'] for item in listed.json()['items']]==[query['id']]
    assert listed.json()['next_cursor'] is None
    assert case.client.get(ROOT,params={'instance_id':str(uuid4())}).status_code==404
    legacy=case.client.get('/api/data/queries/'+query['id'])
    assert legacy.status_code==200 and legacy.json()['reference_only'] is True
    assert legacy.json()['context']['detail_path']==ROOT+'/'+query['id']
    for suffix,payload in [('cancel',{'expected_version':fresh['version'],'reason':'V1不能停止'}),
        ('confirm',{'expected_version':fresh['version'],'reason':'V1不能改范围'}),
        ('review-derived',{'expected_version':fresh['version'],'reason':'不能换来源','decision':'confirmed'})]:
        assert case.client.post('/api/data/queries/'+query['id']+'/'+suffix,json=payload).status_code==409
    assert case.client.put('/api/data/queries/'+query['id']+'/draft',json={
        'expected_version':fresh['version'],'reason':'不能覆盖','draft':{}}).status_code==409
    with Session(case.bound) as db:
        row=db.get(m.DataRun,query['id']);assert row.data['request']==value
        assert db.scalar(select(func.count()).select_from(m.Job).where(m.Job.workspace_id==case.p.workspace_id,
            m.Job.kind=='data_query'))==1
        assert db.scalar(select(func.count()).select_from(m.DataGap).where(m.DataGap.workspace_id==case.p.workspace_id))==0


def test_normal_cancellation_is_persistent_and_named_worker_noop_does_not_answer(partial_material):
    case=partial_material;value,query=create(case)
    with Session(case.bound) as db:
        original_queries=set(db.scalars(select(m.DataRun.id).where(m.DataRun.workspace_id==case.p.workspace_id)))
        original_jobs=set(db.scalars(select(m.Job.id).where(m.Job.workspace_id==case.p.workspace_id,
            m.Job.kind=='data_query')))
    command={'schema':'data-query-cancel-v2','request_id':str(uuid4()),'version':query['version'],'reason':'先停止这份资料核对'}
    response=post(case,ROOT+'/'+query['id']+'/cancel',command)
    assert response.status_code==200 and response.json()['query']['status']=='cancelled'
    assert post(case,ROOT+'/'+query['id']+'/cancel',command).status_code==200
    # The normal named runner returns zero only for succeeded, not cancelled.
    assert run_setup_job(query['job']['id'])==1
    reopened=case.client.get(ROOT+'/'+query['id'])
    assert reopened.status_code==200 and reopened.json()['status']=='cancelled' and reopened.json()['answer'] is None
    assert reopened.json()['job']=={'id':query['job']['id'],'status':'cancelled'}
    recovered=case.client.get(ROOT+'/requests/'+command['request_id'],
        params={'expected_sha256':hashlib.sha256(raw(command)).hexdigest()})
    assert recovered.status_code==200 and recovered.json()['found'] is True
    assert recovered.json()['result']['operation']=='cancel'
    assert recovered.json()['result']['query']['id']==query['id']
    assert recovered.json()['result']['query']['status']=='cancelled'
    with Session(case.bound) as db:
        job=db.get(m.Job,query['job']['id'])
        assert job.status=='cancelled' and job.attempts==1
        row=db.get(m.DataRun,query['id'])
        assert row.status=='cancelled' and 'result' not in row.data and 'result_hash' not in row.data
        assert set(db.scalars(select(m.DataRun.id).where(m.DataRun.workspace_id==case.p.workspace_id)))==original_queries
        assert set(db.scalars(select(m.Job.id).where(m.Job.workspace_id==case.p.workspace_id,
            m.Job.kind=='data_query')))==original_jobs


def test_formal_document_answer_keeps_fixed_citation_and_no_search_job_is_implicitly_created(formal_document):
    case=formal_document
    with Session(case.bound) as db:before=db.scalar(select(func.count()).select_from(m.Job).where(m.Job.workspace_id==case.p.workspace_id,m.Job.kind=='search_runtime_documents'))
    _,query=create(case,source_scope=case.document_scope,question='报告正文对每日资料有什么要求？')
    query=finish(case,query)
    assert query['answer']['intent']=='document_conditions' and query['answer']['references']
    assert all(ref['kind']=='document_excerpt' and ref['source_path'].startswith('/api/') for ref in query['answer']['references'])
    assert '每日' in ' '.join(item['text'] for item in query['answer']['findings'])
    _,unmatched=create(case,source_scope=case.document_scope,question='报告中审批有什么要求？')
    unmatched=finish(case,unmatched)
    assert unmatched['status']=='needs_clarification' and unmatched['answer']['answer_status']=='needs_clarification'
    assert not unmatched['answer']['findings'] and not unmatched['answer']['references']
    assert '相应正文检索' in unmatched['answer']['summary']
    with Session(case.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.Job).where(m.Job.workspace_id==case.p.workspace_id,
            m.Job.kind=='search_runtime_documents'))==before


def test_result_loss_and_succeeded_job_reverse_witness_never_look_like_pending_or_empty_success(partial_material):
    case=partial_material;_,query=create(case);completed=finish(case,query)
    assert completed['answer'] is not None
    with Session(case.bound) as db:
        row=db.get(m.DataRun,query['id']);service.checked(db,case.p,row)
        for status,delete_audit in [('insufficient',False),('running',True),('queued',True)]:
            with db.begin_nested() as fault:
                data=copy.deepcopy(row.data);data.pop('result');data.pop('result_hash');row.data=data;row.status=status
                if delete_audit:
                    event=db.scalar(select(m.Audit).where(m.Audit.workspace_id==case.p.workspace_id,
                        m.Audit.action==RESULT_AUDIT,m.Audit.object_id==row.id));db.delete(event)
                db.flush()
                view=service.view(db,case.p,row)
                assert view['status']=='blocked_integrity' and view['answer'] is None and view['result_hash'] is None
                from generic_agents.public_query_visibility_v144 import reject_generic_write
                with pytest.raises(HTTPException) as error:reject_generic_write(db,case.p,row)
                assert error.value.status_code==409
                fault.rollback()
            db.refresh(row);service.checked(db,case.p,row)
        assert service.view(db,case.p,row)['answer']==completed['answer']


@pytest.mark.parametrize('fault',['create_audit','job_marker','result_audit','all_direct_markers'])
def test_direct_and_reverse_witnesses_prevent_v1_or_worker_downgrade(partial_material,fault):
    case=partial_material;_,query=create(case)
    completed=finish(case,query)
    with Session(case.bound) as db:
        row=db.get(m.DataRun,query['id']);service.checked(db,case.p,row)
        if fault in {'create_audit','result_audit'}:
            action=CREATE_AUDIT if fault=='create_audit' else RESULT_AUDIT
            event=db.scalar(select(m.Audit).where(m.Audit.workspace_id==case.p.workspace_id,m.Audit.action==action,m.Audit.object_id==row.id))
            db.delete(event)
        elif fault=='job_marker':
            job=db.get(m.Job,row.data['job_id']);data=copy.deepcopy(job.data);data.pop(MARKER);job.data=data
        else:
            data=copy.deepcopy(row.data);data.pop(MARKER);data['request']=dict(data['request']);data['request'].pop('schema');row.data=data
            job=db.get(m.Job,row.data['job_id']);data=copy.deepcopy(job.data);data.pop(MARKER);job.data=data
        db.commit()
    response=case.client.get(ROOT+'/'+query['id'])
    if fault=='result_audit':
        assert response.status_code==200 and response.json()['status']=='blocked_integrity' and response.json()['answer'] is None
    else:assert response.status_code==409
    assert case.client.post('/api/data/queries/'+query['id']+'/cancel',json={
        'expected_version':completed['version'],'reason':'不能退回旧协议'}).status_code==409


@pytest.mark.parametrize('status',['running','failed'])
def test_real_saved_answer_does_not_claim_success_before_or_after_failed_job_tail(partial_material,status):
    case=partial_material;_,query=create(case);completed=finish(case,query)
    with Session(case.bound) as db:
        job=db.get(m.Job,query['job']['id']);assert job.status=='succeeded'
        job.status=status;db.commit()  # corruption/state-projection negative, never fake success
    response=case.client.get(ROOT+'/'+query['id'])
    assert response.status_code==200 and response.json()['status']==status and response.json()['answer'] is None


def test_current_source_failure_cannot_erase_original_request_or_alter_saved_answer(partial_material,monkeypatch):
    case=partial_material;value,query=create(case);query=finish(case,query)
    from generic_agents import instance_material_sources as sources
    def changed(*args,**kwargs):raise HTTPException(409,'旧来源证明被明确故障注入')
    monkeypatch.setattr(sources,'recheck',changed)
    freshness=case.client.get(ROOT+'/'+query['id']+'/freshness')
    assert freshness.status_code==200 and freshness.json()['unchanged'] is False
    assert freshness.json()['originals_rechecked'] is False
    recovered=case.client.get(ROOT+'/requests/'+value['request_id'],params={'expected_sha256':hashlib.sha256(raw(value)).hexdigest()})
    assert recovered.status_code==200 and recovered.json()['result']['query']['answer']==query['answer']
    assert case.client.get(ROOT+'/'+query['id']).json()['result_hash']==query['result_hash']


def test_same_original_nonce_concurrent_sessions_create_one_query_and_one_job(partial_material):
    case=partial_material;value=body(case);encoded=raw(value)
    def create_one():
        with Session(case.bound) as db:
            result,created=service.create(db,case.p,encoded);db.commit();return result,created
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:create_one(),range(2)))
    assert {item[1] for item in results}=={True,False}
    assert len({item[0]['query']['id'] for item in results})==1


@pytest.mark.parametrize('operation',['create','cancel','note'])
def test_routes_keep_synchronous_transaction_off_event_loop_and_reject_large_stream(monkeypatch,operation):
    entered=threading.Event();release=threading.Event();calls=[]
    def transaction(*args):calls.append(True);entered.set();assert release.wait(5);return {'ok':True},False
    monkeypatch.setattr(api,'transaction',transaction)
    from tests.test_source_query_contracts import request as create_body
    value=create_body() if operation=='create' else {'schema':'data-query-'+('note' if operation=='note' else 'cancel')+'-v2',
        'request_id':str(uuid4()),'version':1,('text' if operation=='note' else 'reason'):'明确操作'}
    def request(encoded):
        async def receive():return {'type':'http.request','body':encoded,'more_body':False}
        return Request({'type':'http','method':'POST','path':'/','headers':[]},receive)
    async def run():
        fn=getattr(api,operation)
        args=(request(raw(value)),Response(),None,None) if operation=='create' else ('query',request(raw(value)),None,None)
        task=asyncio.create_task(fn(*args))
        try:
            for _ in range(1000):
                if entered.is_set():break
                await asyncio.sleep(.001)
            assert entered.is_set() and not task.done()
        finally:release.set()
        await task
        count=len(calls)
        args=(request(b'x'*(MAX_BODY+1)),Response(),None,None) if operation=='create' else ('q',request(b'x'*(MAX_BODY+1)),None,None)
        with pytest.raises(HTTPException) as error:await fn(*args)
        assert error.value.status_code==413 and len(calls)==count
    asyncio.run(run())


@pytest.mark.parametrize('first',['generic_agents.source_query_api','generic_agents.worker'])
def test_fresh_source_query_router_worker_imports_never_connect(tmp_path,first):
    from tests.test_daily_frame_imports_v235 import SCRIPT
    code=SCRIPT.replace('print(json.dumps(',"v2=importlib.import_module('generic_agents.source_query_api')\nassert any(route.path=='/api/v2/data/queries' for route in v2.router.routes)\nprint(json.dumps(")
    order=[first,'generic_agents.source_query_api','generic_agents.worker']
    env={key:os.environ[key] for key in ('PATH','SYSTEMROOT','LANG','LC_ALL') if key in os.environ}
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'),PYTHONDONTWRITEBYTECODE='1',
        GA_DATABASE_URL='postgresql+psycopg2://invalid/never_connect',GA_STORAGE_DIR=str(tmp_path/'unused'))
    result=subprocess.run([sys.executable,'-c',code,json.dumps(order)],cwd=tmp_path,env=env,
        text=True,capture_output=True,timeout=45)
    assert result.returncode==0,result.stdout[-1000:]+'\n'+result.stderr[-2200:]
