"""Actual parent/child Jobs, exact fixed document and ordinary DataAgent.

The reused fixture uses normal upload/native parsing/independent receipt; no
Search result, successful Job or answer is fabricated. State changes in the
negative cases are explicit fault/window projections after genuine success.
Root alone supplies and executes the disposable PostgreSQL environment.
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
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from types import SimpleNamespace as NS
from fastapi import HTTPException,Response
from sqlalchemy import event,func,select,text
from sqlalchemy.orm import Session
from starlette.requests import Request
import pytest
from generic_agents import models as m,worker,runtime_documents as d
from generic_agents import document_question_service as q,document_question_api as api
from generic_agents import source_query_service as source
from generic_agents.contracts import digest
from generic_agents.runtime_document_models import RuntimeDocumentSearch as Search
from tests.test_runtime_document_selected_search_v241 import fixed_document,scene

ROOT='/api/v2/data/document-questions'
V2='/api/v2/data/queries'


def encoded(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()


def body(case,question='报告对摄像有哪些要求？'):
    reply=case.s.client.get(ROOT+'/catalog',params={'instance_id':case.release['instance_id']})
    assert reply.status_code==200,reply.text[:350]
    catalog=reply.json();report=next(x for x in catalog['reports'] if x['version_id']==case.version_id)
    assert report['can_ask'] is True and catalog['permissions']['can_create'] is True
    return {'schema':'document-question-create-v1','request_id':str(uuid4()),'question':question,
        'expected_catalog_hash':catalog['catalog_hash'],'selection':{'instance_id':catalog['instance_id'],
            'release_id':catalog['release_id'],'business_ids':[x['id'] for x in catalog['businesses']],
            'document_version_id':report['version_id'],'parse_attempt_id':report['parse_attempt_id'],
            'raw_sha256':report['raw_sha256'],'parse_hash':report['parse_hash']},'parent_query_id':None}


def post(case,path,value):return case.s.client.post(path,content=encoded(value),headers={'Content-Type':'application/json'})


def create(case,question='报告对摄像有哪些要求？'):
    value=body(case,question);reply=post(case,ROOT,value)
    assert reply.status_code==201,reply.text[:400]
    return value,reply.json()['view']


def get(case,identity):
    reply=case.s.client.get(ROOT+'/'+identity);assert reply.status_code==200,reply.text[:400]
    return reply.json()


def waiting(case,view):
    identity=view['query']['id'];job_id=view['query']['job']['id']
    assert worker.run_setup_job(job_id)==1
    actual=get(case,identity)
    assert actual['stage']==q.PHASE and actual['query']['job']['status']==q.PHASE
    assert actual['child']['status']=='queued' and actual['planner']['keyword']=='摄像'
    with Session(case.bound) as db:
        assert db.get(m.Job,job_id).attempts==1
        assert db.get(Search,actual['child']['search_id']).data['inventory'][0]['version_id']==case.version_id
    return actual


def completed(case,view):
    view=waiting(case,view)
    assert worker.run_setup_job(view['child']['job_id'])==0
    ready=get(case,view['query']['id'])
    assert ready['stage']=='answering' and ready['query']['job']['id']==view['query']['job']['id']
    assert ready['query']['job']['status']=='queued'
    assert worker.run_setup_job(ready['query']['job']['id'])==0
    result=get(case,view['query']['id'])
    assert result['stage']=='completed' and result['query']['status']=='sufficient'
    return result


def test_normal_report_question_wait_child_original_parent_answer_note_and_reopen(fixed_document):
    case=fixed_document;original,view=create(case);result=completed(case,view)
    texts=' '.join(x['text'] for x in result['query']['answer']['findings'])
    assert '摄像资料应逐项核对' in texts and result['selection']==original['selection']
    assert result['query']['source_scope']['entity_ids']==[]
    assert result['query']['source_scope']['document_search_ids']==[result['child']['search_id']]
    reference=result['query']['answer']['references'][0]
    assert case.s.client.get(reference['source_path']).status_code==200
    note={'schema':'data-query-note-v2','request_id':str(uuid4()),'version':result['query']['version'],'text':'  人工解释单独保存。\n'}
    saved=post(case,V2+'/'+result['query']['id']+'/notes',note)
    assert saved.status_code==200 and saved.json()['query']['notes'][-1]['text']==note['text']
    reopened=get(case,result['query']['id'])
    assert reopened['query']['answer']==result['query']['answer'] and reopened['query']['result_hash']==result['query']['result_hash']
    recovery=case.s.client.get(ROOT+'/requests/'+original['request_id'],params={'expected_sha256':hashlib.sha256(encoded(original)).hexdigest()})
    assert recovery.status_code==200 and recovery.json()['found'] is True
    assert recovery.json()['result']['view']==reopened
    assert post(case,ROOT,original).status_code==200
    with Session(case.bound) as db:
        assert db.get(m.Job,view['query']['job']['id']).attempts==2
        row=db.get(m.DataRun,view['query']['id']);origin=q._creation(db,case.p,row)
        assert origin['exact_body'].encode()==encoded(original)
        assert row.data['request']['source_scope']['document_search_ids']==[]
        assert len({original['request_id'],row.data['request']['request_id'],q._link(db,case.p,row)[0]['body']['request_id']})==3


@pytest.mark.parametrize('question',['报告有哪些要求？','资料有哪些？','请随便说点什么','报告对摄像和审批有哪些要求？'])
def test_no_literal_topic_is_saved_for_clarification_without_child_search(fixed_document,question):
    case=fixed_document;_,view=create(case,question)
    assert worker.run_setup_job(view['query']['job']['id'])==0
    result=get(case,view['query']['id'])
    assert result['stage']=='needs_clarification' and result['child'] is None
    assert result['planner']['keyword'] is None and '一个查找事项' in result['query']['answer']['summary']
    with Session(case.bound) as db:
        assert not list(db.scalars(select(Search).where(Search.workspace_id==case.p.workspace_id)))


def test_unmatched_word_keeps_actual_successful_search_without_claiming_report_has_no_rules(fixed_document):
    case=fixed_document;_,view=create(case,'报告对审批有哪些要求？')
    assert worker.run_setup_job(view['query']['job']['id'])==1
    view=get(case,view['query']['id']);assert view['planner']['keyword']=='审批'
    assert worker.run_setup_job(view['child']['job_id'])==0
    assert worker.run_setup_job(view['query']['job']['id'])==0
    result=get(case,view['query']['id'])
    assert result['stage']=='needs_clarification' and result['child']['job_status']=='succeeded'
    assert not result['query']['answer']['findings'] and not result['query']['answer']['references']
    assert '未找到字面匹配' in result['query']['answer']['summary']
    assert '不表示报告没有相关业务规定' in result['query']['answer']['summary']


def test_lost_child_completion_hint_reconciles_once_and_job_running_tail_keeps_waiting(fixed_document,monkeypatch):
    case=fixed_document;_,view=create(case);view=waiting(case,view)
    monkeypatch.setattr(q,'child_finished',lambda *args:0)
    assert worker.run_setup_job(view['child']['job_id'])==0
    with Session(case.bound) as db:
        child=db.get(m.Job,view['child']['job_id']);assert child.status=='succeeded'
        with db.begin_nested() as fault:
            child.status='running';db.flush()
            assert q.reconcile_one(db,workspace_id=case.p.workspace_id,job_id=view['query']['job']['id'])==0
            assert q.view(db,case.p,db.get(m.DataRun,view['query']['id']))['stage']=='waiting_search_settlement'
            assert db.get(m.Job,view['query']['job']['id']).attempts==1
            fault.rollback()
        assert q.reconcile_one(db,workspace_id=case.p.workspace_id,job_id=view['query']['job']['id'])==1
        db.rollback()  # crash before wake commit must not strand the question
    assert get(case,view['query']['id'])['query']['job']['status']==q.PHASE
    assert q.poll_once(case.p.workspace_id,job_ids=[view['query']['job']['id']])==1
    assert q.poll_once(case.p.workspace_id,job_ids=[view['query']['job']['id']])==0
    assert worker.run_setup_job(view['query']['job']['id'])==0
    assert get(case,view['query']['id'])['stage']=='completed'


def test_crash_after_child_wait_commit_before_parent_phase_commit_recovers_same_child(fixed_document,monkeypatch):
    from generic_agents import leases
    case=fixed_document;original,view=create(case);identity=view['query']['id'];job_id=view['query']['job']['id']
    monkeypatch.setattr(leases,'LEASE_SECONDS',5)
    class ProcessLost(BaseException):pass
    reached=[]
    def after_commit(db):
        if db.info.get('worker_lease',{}).get('job_id')!=job_id:return
        with Session(case.bound) as probe:
            issued=probe.scalar(select(m.Audit).where(m.Audit.workspace_id==case.p.workspace_id,
                m.Audit.action==q.WAIT,m.Audit.object_id==identity))
            parent=probe.get(m.Job,job_id)
            if issued and parent.status=='running':
                reached.append(copy.deepcopy(issued.data));raise ProcessLost()
    event.listen(Session,'after_commit',after_commit)
    try:
        with pytest.raises(ProcessLost):worker.process_once(job_id,kinds=['data_query'],deliver=False,workspace_id=case.p.workspace_id)
    finally:event.remove(Session,'after_commit',after_commit)
    assert len(reached)==1
    with Session(case.bound) as db:
        parent=db.get(m.Job,job_id);assert parent.status=='running' and parent.attempts==1
        child_ids=list(db.scalars(select(Search.id).where(Search.workspace_id==case.p.workspace_id)))
        assert len(child_ids)==1
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        with Session(case.bound) as db:
            expired=db.scalar(select(m.Job.id).where(m.Job.id==job_id,m.Job.lease_until<func.clock_timestamp()))
        if expired:break
        time.sleep(.05)
    assert expired  # actual DB clock/lease expiry, no fake token or status reset
    assert worker.run_setup_job(job_id)==1
    restored=get(case,identity)
    assert restored['query']['job']['status']==q.PHASE and restored['child']['search_id']==child_ids[0]
    with Session(case.bound) as db:
        assert db.get(m.Job,job_id).attempts==2
        assert q._one(db,case.p,q.WAIT,identity)==reached[0]
        assert list(db.scalars(select(Search.id).where(Search.workspace_id==case.p.workspace_id)))==child_ids
    assert worker.run_setup_job(restored['child']['job_id'])==0
    assert worker.run_setup_job(job_id)==0
    assert get(case,identity)['stage']=='completed'
    recovered=case.s.client.get(ROOT+'/requests/'+original['request_id'],params={'expected_sha256':hashlib.sha256(encoded(original)).hexdigest()})
    assert recovered.status_code==200 and recovered.json()['result']['view']['query']['id']==identity


def test_wait_cancel_is_real_no_adoption_and_child_state_remains_honest(fixed_document):
    case=fixed_document;value,view=create(case);view=waiting(case,view)
    request={'schema':'data-query-cancel-v2','request_id':str(uuid4()),'version':view['query']['version'],'reason':'停止本次报告答复'}
    reply=post(case,V2+'/'+view['query']['id']+'/cancel',request)
    assert reply.status_code==200 and reply.json()['query']['status']=='cancelled'
    assert worker.run_setup_job(view['child']['job_id'])==0  # local child may really finish
    result=get(case,view['query']['id'])
    assert result['stage']=='cancelled' and result['query']['answer'] is None
    assert result['child']['job_status']=='succeeded'
    assert q.poll_once(case.p.workspace_id,job_ids=[view['query']['job']['id']])==0
    with Session(case.bound) as db:assert db.get(m.Job,view['query']['job']['id']).attempts==1
    assert post(case,ROOT,value).json()['view']['stage']=='cancelled'


@pytest.mark.parametrize('fault',['row_marker','job_marker','wait','link','all_direct_markers'])
def test_independent_parent_child_witnesses_prevent_ordinary_v2_or_v1_downgrade(fixed_document,fault):
    case=fixed_document;_,view=create(case);view=waiting(case,view)
    with Session(case.bound) as db:
        row=db.get(m.DataRun,view['query']['id']);q.check_metadata(db,case.p,row,db.get(m.Job,row.data['job_id']))
        if fault in {'row_marker','all_direct_markers'}:
            data=copy.deepcopy(row.data);data.pop(q.MARKER);row.data=data
        if fault in {'job_marker','all_direct_markers'}:
            for identity in [row.data['job_id'],view['child']['job_id']]:
                job=db.get(m.Job,identity);data=copy.deepcopy(job.data);data.pop(q.MARKER);job.data=data
        if fault in {'wait','link'}:
            event=db.scalar(select(m.Audit).where(m.Audit.workspace_id==case.p.workspace_id,
                m.Audit.action==(q.WAIT if fault=='wait' else q.LINK),m.Audit.object_id==row.id));db.delete(event)
        db.commit()
    assert case.s.client.get(ROOT+'/'+view['query']['id']).status_code==409
    assert case.s.client.get(V2+'/'+view['query']['id']).status_code==409
    reply=case.s.client.post('/api/data/queries/'+view['query']['id']+'/cancel',json={'expected_version':view['query']['version'],'reason':'不能降级'})
    assert reply.status_code==409


def test_preoccupied_child_nonce_is_rejected_without_adopting_or_issuing_another_search(fixed_document):
    case=fixed_document;value,view=create(case)
    nonce=q._derived(value['request_id'],'search',{'selection_hash':digest(value['selection']),'planner':q.planner(value['question'])})
    child_body={'request_id':nonce,'release_id':case.release['id'],'keyword':'摄像'}
    with Session(case.bound) as db:
        old=d.start_search(db,case.p,case.release['instance_id'],child_body);db.commit()
    assert worker.run_setup_job(view['query']['job']['id'])==1
    result=get(case,view['query']['id']);assert result['stage']=='failed' and result['child'] is None
    with Session(case.bound) as db:
        assert [row.id for row in db.scalars(select(Search).where(Search.workspace_id==case.p.workspace_id))]==[old['id']]


def test_concurrent_normal_search_holding_child_nonce_before_actor_cannot_deadlock_auto_parent(fixed_document,monkeypatch):
    case=fixed_document;value,view=create(case)
    nonce=q._derived(value['request_id'],'search',{'selection_hash':digest(value['selection']),'planner':q.planner(value['question'])})
    body={'request_id':nonce,'release_id':case.release['id'],'keyword':'摄像'}
    ordinary_at_actor=threading.Event();parent_at_nonce=threading.Event();release=threading.Event();local=threading.local()
    actor,prior=d.current_actor,d.prior
    def actor_barrier(db,p):
        if getattr(local,'ordinary',False):ordinary_at_actor.set();assert release.wait(5)
        return actor(db,p)
    def nonce_barrier(db,p,value,scope):
        if not getattr(local,'ordinary',False) and value.get('request_id')==nonce:parent_at_nonce.set()
        return prior(db,p,value,scope)
    monkeypatch.setattr(d,'current_actor',actor_barrier);monkeypatch.setattr(d,'prior',nonce_barrier)
    def ordinary():
        local.ordinary=True
        with Session(case.bound) as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            result=d.start_search(db,case.p,case.release['instance_id'],body);db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        search=pool.submit(ordinary)
        try:
            assert ordinary_at_actor.wait(5)
            parent=pool.submit(worker.run_setup_job,view['query']['job']['id'])
            assert parent_at_nonce.wait(5)
        finally:release.set()
        saved=search.result(timeout=15)
        assert parent.result(timeout=15)==1
    result=get(case,view['query']['id'])
    assert result['stage']=='failed' and result['child'] is None
    with Session(case.bound) as db:
        assert list(db.scalars(select(Search.id).where(Search.workspace_id==case.p.workspace_id)))==[saved['id']]
        assert not list(db.scalars(select(m.Audit.id).where(m.Audit.workspace_id==case.p.workspace_id,
            m.Audit.action==q.LINK,m.Audit.object_id==view['query']['id'])))


def test_child_reverse_inventory_never_turns_deleted_parent_into_unknown_original_nonce(fixed_document):
    from generic_agents.source_query_contracts import CREATE_AUDIT,COMMAND_AUDIT
    case=fixed_document;value,view=create(case);view=waiting(case,view);identity=view['query']['id']
    with Session(case.bound) as db:
        row=db.get(m.DataRun,identity);job=db.get(m.Job,row.data['job_id'])
        db.delete(job);db.delete(row)
        for proof in list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==case.p.workspace_id,
                m.Audit.object_id==identity,m.Audit.action.in_([*q.ACTIONS,CREATE_AUDIT,COMMAND_AUDIT])))):
            db.delete(proof)
        db.commit()
        original_children=list(db.scalars(select(Search.id).where(Search.workspace_id==case.p.workspace_id)))
    assert case.s.client.get(ROOT+'/'+identity).status_code==409
    reply=case.s.client.get(ROOT+'/requests/'+value['request_id'],params={'expected_sha256':hashlib.sha256(encoded(value)).hexdigest()})
    assert reply.status_code==409
    assert post(case,ROOT,value).status_code==409
    with Session(case.bound) as db:
        assert list(db.scalars(select(Search.id).where(Search.workspace_id==case.p.workspace_id)))==original_children
        assert not list(db.scalars(select(m.DataRun.id).where(m.DataRun.workspace_id==case.p.workspace_id)))


def test_original_answer_and_nonce_are_metadata_reads_when_adopted_original_bytes_are_missing(fixed_document):
    from generic_agents.config import settings
    case=fixed_document;original,view=create(case);result=completed(case,view)
    with Session(case.bound) as db:path=settings().storage_dir/db.get(m.Evidence,case.evidence_id).storage_key
    saved=path.read_bytes()
    try:
        path.write_bytes(b'controlled original corruption after genuine answer')
        assert get(case,view['query']['id'])==result
        assert post(case,ROOT,original).status_code==200
        recovery=case.s.client.get(ROOT+'/requests/'+original['request_id'],params={'expected_sha256':hashlib.sha256(encoded(original)).hexdigest()})
        assert recovery.status_code==200 and recovery.json()['result']['view']==result
        freshness=case.s.client.get(V2+'/'+view['query']['id']+'/freshness')
        assert freshness.status_code==200 and freshness.json()['originals_rechecked'] is False
        assert freshness.json()['unchanged'] is False
    finally:path.write_bytes(saved)


def test_same_original_nonce_two_transactions_create_exactly_one_parent(fixed_document):
    case=fixed_document;value=body(case);raw=encoded(value)
    def one():
        with Session(case.bound) as db:
            db.execute(text("SET LOCAL lock_timeout='5s'"))
            result,created=q.create(db,case.p,raw);db.commit();return result,created
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:one(),range(2)))
    assert sorted(x[1] for x in results)==[False,True]
    assert len({x[0]['view']['query']['id'] for x in results})==1


def test_original_auto_nonce_cannot_change_question_or_selected_fingerprint(fixed_document):
    case=fixed_document;value,view=create(case)
    changed=copy.deepcopy(value);changed['question']='报告对审批有哪些要求？'
    assert post(case,ROOT,changed).status_code==409
    changed=copy.deepcopy(value);changed['selection']['raw_sha256']='0'*64
    assert post(case,ROOT,changed).status_code==409
    recovered=case.s.client.get(ROOT+'/requests/'+value['request_id'],params={'expected_sha256':hashlib.sha256(encoded(value)).hexdigest()})
    assert recovered.status_code==200 and recovered.json()['result']['view']['query']['id']==view['query']['id']
    assert recovered.json()['result']['view']['selection']==value['selection']


@pytest.mark.parametrize('operation',['notes','cancel'])
def test_actual_user_first_note_or_cancel_and_first_worker_prepare_do_not_deadlock(fixed_document,monkeypatch,operation):
    case=fixed_document;_,view=create(case);entered=threading.Event();release=threading.Event()
    real=d.start_selected_search
    def pause(*args,**kwargs):
        entered.set();assert release.wait(5);return real(*args,**kwargs)
    monkeypatch.setattr(d,'start_selected_search',pause)
    command={'schema':'data-query-'+('note' if operation=='notes' else 'cancel')+'-v2','request_id':str(uuid4()),
        'version':view['query']['version'],('text' if operation=='notes' else 'reason'):'并发原输入必须明确处理'}
    def write():
        with Session(case.bound) as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            try:
                fn=source.note if operation=='notes' else source.cancel
                result=fn(db,case.p,view['query']['id'],encoded(command));db.commit();return result
            except HTTPException as error:db.rollback();return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        run=pool.submit(worker.run_setup_job,view['query']['job']['id'])
        try:
            assert entered.wait(5);attempt=pool.submit(write)
            # Release promptly; PostgreSQL lock_timeout prevents a deadlock
            # from hanging the harness even if either order regresses.
        finally:release.set()
        assert run.result(timeout=15)==1
        assert attempt.result(timeout=15)==409  # worker advanced version, no silent input loss
    current=get(case,view['query']['id'])
    command.update(request_id=str(uuid4()),version=current['query']['version'])
    saved=post(case,V2+'/'+view['query']['id']+'/'+operation,command)
    assert saved.status_code==200
    if operation=='notes':assert saved.json()['query']['notes'][-1]['text']==command['text']
    else:assert saved.json()['query']['status']=='cancelled'


def test_ordinary_v2_create_and_normal_search_same_actor_use_compatible_real_locks(fixed_document,monkeypatch):
    from generic_agents import relations
    case=fixed_document;value=body(case)
    with Session(case.bound) as db:
        pin=q._selected(db,case.p,value['selection'],physical=False)
        catalog=source.catalog(db,case.p,case.release['instance_id'],pin['registered_day'])
    ordinary={'schema':'data-query-create-v2','request_id':str(uuid4()),'question':'报告有哪些摄像要求？',
        'expected_catalog_hash':catalog['catalog_hash'],'parent_query_id':None,'source_scope':{
            'instance_id':case.release['instance_id'],'release_id':case.release['id'],'business_ids':value['selection']['business_ids'],
            'entity_ids':[],'batch_day':pin['registered_day'],'source_kinds':['document_searches'],'document_search_ids':[]}}
    entered=threading.Event();release=threading.Event();original=relations.lock_current_actor
    def pause(db,p):original(db,p);entered.set();assert release.wait(5)
    monkeypatch.setattr(relations,'lock_current_actor',pause)
    def create_parent():
        with Session(case.bound) as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"));result,new=source.create(db,case.p,encoded(ordinary));db.commit();return new
    def create_search():
        with Session(case.bound) as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"));result=d.start_search(db,case.p,case.release['instance_id'],{
                'request_id':str(uuid4()),'release_id':case.release['id'],'keyword':'摄像'});db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        parent=pool.submit(create_parent)
        try:assert entered.wait(5);child=pool.submit(create_search)
        finally:release.set()
        assert parent.result(timeout=15) is True
        assert child.result(timeout=15)['status']=='queued'


def test_new_post_keeps_transaction_off_event_loop_and_rejects_oversized_stream(monkeypatch):
    from tests.test_document_question_contracts_v241 import request as valid
    entered=threading.Event();release=threading.Event();calls=[]
    def transaction(*args):calls.append(True);entered.set();assert release.wait(5);return {'ok':True},True
    monkeypatch.setattr(api,'transaction',transaction)
    def request(raw):
        async def receive():return {'type':'http.request','body':raw,'more_body':False}
        return Request({'type':'http','method':'POST','path':'/','headers':[]},receive)
    async def run():
        task=asyncio.create_task(api.create(request(encoded(valid())),Response(),None,None))
        try:
            for _ in range(1000):
                if entered.is_set():break
                await asyncio.sleep(.001)
            assert entered.is_set() and not task.done()
        finally:release.set()
        await task
        with pytest.raises(HTTPException) as error:await api.create(request(b'x'*(24*1024+1)),Response(),None,None)
        assert error.value.status_code==413 and len(calls)==1
    asyncio.run(run())


@pytest.mark.parametrize('first',['generic_agents.document_question_api','generic_agents.worker'])
def test_fresh_report_router_worker_import_orders_never_connect(tmp_path,first):
    from tests.test_daily_frame_imports_v235 import SCRIPT
    code=SCRIPT.replace('print(json.dumps(',"v2=importlib.import_module('generic_agents.document_question_api')\nassert any(route.path=='/api/v2/data/document-questions' for route in v2.router.routes)\nprint(json.dumps(")
    order=[first,'generic_agents.document_question_api','generic_agents.worker']
    env={key:os.environ[key] for key in ('PATH','SYSTEMROOT','LANG','LC_ALL') if key in os.environ}
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'),PYTHONDONTWRITEBYTECODE='1',
        GA_DATABASE_URL='postgresql+psycopg2://invalid/never_connect',GA_STORAGE_DIR=str(tmp_path/'unused'))
    reply=subprocess.run([sys.executable,'-c',code,json.dumps(order)],cwd=tmp_path,env=env,text=True,capture_output=True,timeout=45)
    assert reply.returncode==0,reply.stdout[-1000:]+'\n'+reply.stderr[-2000:]
