"""Actual explicit-family consumer, not a fake successful worker dispatch."""
import copy
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m,source_query_consumer as consumer
from generic_agents import document_question_service as questions
from generic_agents.runtime_document_models import RuntimeDocumentSearch as Search
from tests.test_document_question_api_v241 import create,get,post,ROOT,fixed_document,scene


def finish_by_product_consumer(case,identity):
    cursor=None
    for _ in range(12):
        result=consumer.step(case.p.workspace_id,after=cursor);cursor=result['after']
        view=get(case,identity)
        if view['stage']=='completed':return view
        assert view['stage'] not in {'failed','blocked_integrity'},view['query']['error']
    raise AssertionError('产品限定消费者未完成原父子资料查询')


def test_product_consumer_finishes_child_and_same_parent_without_manual_job_or_frontend_writes(fixed_document):
    case=fixed_document;_,view=create(case)
    result=finish_by_product_consumer(case,view['query']['id'])
    assert result['query']['job']['id']==view['query']['job']['id'] and result['child']['job_status']=='succeeded'
    assert '摄像资料应逐项核对' in result['query']['answer']['findings'][0]['text']
    with Session(case.bound) as db:
        assert db.get(m.Job,view['query']['job']['id']).attempts==2
        assert len(list(db.scalars(select(Search.id).where(Search.workspace_id==case.p.workspace_id))))==1


def test_old_v1_cloud_unrelated_search_and_foreign_workspace_jobs_are_never_claimed(fixed_document):
    case=fixed_document
    # Negative queued sentinels are not accepted/successful workflows. If the
    # consumer mistakenly dispatches any of them its real claim mutates attempts.
    with Session(case.bound) as db:
        other=m.Workspace(name='其他独立工作区');db.add(other);db.flush()
        ids=[]
        for kind,workspace,request in [('data_query',case.p.workspace_id,{'mode':'structured'}),
            ('data_query',case.p.workspace_id,{'mode':'cloud','model_profile_id':str(uuid4())}),
            ('data_query',other.id,{'mode':'cloud'})]:
            row=m.DataRun(workspace_id=workspace,owner_id=case.p.user_id,question='旧查询不能被本族领取',
                status='queued',data={'request':{'question':'旧查询',**request}})
            db.add(row);db.flush()
            job=m.Job(workspace_id=workspace,kind=kind,idempotency_key='query:'+row.id,
                data={'query_id':row.id,'actor_id':case.p.user_id});db.add(job);db.flush();ids.append(job.id)
        ordinary=m.Job(workspace_id=case.p.workspace_id,kind='search_runtime_documents',
            idempotency_key='unrelated-public-search:'+str(uuid4()),
            data={'runtime_document_id':str(uuid4()),'actor_id':case.p.user_id})
        db.add(ordinary);db.flush();ids.append(ordinary.id);db.commit()
    _,view=create(case);finish_by_product_consumer(case,view['query']['id'])
    with Session(case.bound) as db:
        assert all(db.get(m.Job,identity).status=='queued' and db.get(m.Job,identity).attempts==0 for identity in ids)


def test_bad_independent_query_proof_is_diagnosed_without_starving_later_valid_query(fixed_document):
    case=fixed_document;_,bad=create(case);_,good=create(case)
    with Session(case.bound) as db:
        job=db.get(m.Job,bad['query']['job']['id']);data=copy.deepcopy(job.data)
        data.pop(questions.MARKER);job.data=data;db.commit()
    errors=[]
    result=consumer.step(case.p.workspace_id,limit=1,diagnostic=lambda message,identity:errors.append(identity))
    assert result['processed'] is False and errors==[bad['query']['job']['id']]
    second=consumer.step(case.p.workspace_id,after=result['after'],limit=1)
    assert second['processed'] is True
    finished=finish_by_product_consumer(case,good['query']['id'])
    assert finished['stage']=='completed'
    with Session(case.bound) as db:
        assert db.get(m.Job,bad['query']['job']['id']).attempts==0


def test_two_consumers_race_only_the_ordinary_lease_can_dispatch_one_parent_and_one_child(fixed_document,monkeypatch):
    from generic_agents import worker
    case=fixed_document;_,view=create(case);entered=threading.Event();release=threading.Event();real=worker.claim
    def claim(*args,**kwargs):
        lease=real(*args,**kwargs)
        if lease and lease[0]==view['query']['job']['id']:
            entered.set();assert release.wait(5)
        return lease
    monkeypatch.setattr(worker,'claim',claim)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(consumer.step,case.p.workspace_id)
        try:
            assert entered.wait(5)
            second=pool.submit(consumer.step,case.p.workspace_id)
            no_claim=second.result(timeout=5)
        finally:release.set()
        results=[first.result(timeout=15),no_claim]
    assert sum(result['processed'] for result in results)==1
    result=finish_by_product_consumer(case,view['query']['id'])
    with Session(case.bound) as db:
        assert db.get(m.Job,result['query']['job']['id']).attempts==2
        assert len(list(db.scalars(select(Search.id).where(Search.workspace_id==case.p.workspace_id))))==1


def test_cancelled_parent_does_not_let_scoped_consumer_start_queued_child(fixed_document):
    case=fixed_document;_,view=create(case)
    assert consumer.step(case.p.workspace_id)['processed'] is True
    waiting=get(case,view['query']['id']);child=waiting['child']['job_id']
    command={'schema':'data-query-cancel-v2','request_id':str(uuid4()),'version':waiting['query']['version'],'reason':'停止本次报告答复'}
    assert post(case,'/api/v2/data/queries/'+view['query']['id']+'/cancel',command).status_code==200
    assert consumer.step(case.p.workspace_id)['processed'] is False
    with Session(case.bound) as db:
        assert db.get(m.Job,child).status=='queued' and db.get(m.Job,child).attempts==0
    assert get(case,view['query']['id'])['query']['answer'] is None


def test_workspace_and_stop_gate_run_before_database_or_dispatch(monkeypatch):
    from generic_agents import db,cli
    def forbidden(*args,**kwargs):raise AssertionError('invalid or stopped scope touched the DB')
    monkeypatch.setattr(db,'engine',forbidden)
    for value in (None,True,'','AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'):
        with pytest.raises(ValueError):consumer.step(value)
    stop=threading.Event();stop.set()
    assert consumer.step(str(uuid4()),stop_event=stop)['processed'] is False
    monkeypatch.setattr(sys,'argv',['ga','source-query-worker'])
    with pytest.raises(SystemExit) as error:cli.main()
    assert error.value.code==2
