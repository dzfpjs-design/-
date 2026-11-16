"""Real PG commands/clock ledger/source ACK/normal Task and exact worker leases.

Only continuous graph compilation eligibility is an explicit seam here: the
existing normally published legacy human flow is not silently upgraded. The
source, nonce ledger, physical immutable input, normal Task helper, occurrence
worker, actor locks and restart reads are real. Root must additionally verify
a newly compiled eligible graph and real external/model IO in the runtime.
"""
import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents import models as m, video_daily
from generic_agents import business_automation_service as service, business_automation_consumer as consumer
from generic_agents import business_automation_admission as admission, media_daily_delivery as delivery
from generic_agents.business_automation_models import BusinessAutomationSubscription as Subscription
from generic_agents.business_automation_contracts import JOB_KIND
from generic_agents.contracts import digest
from tests.test_business_automation_sources_pg_v242 import source_scene, daily_camera_source, media_runtime, mocked_runtime
from tests.test_media import capture, finish


def raw(value): return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()


@pytest.fixture
def case(source_scene,monkeypatch):
    s=source_scene
    declaration={'business_id':s.selection['business_id'],'business_name':'真实普通任务的资格夹具',
        **{key:digest(['explicit-eligibility-only-fixture',key]) for key in
           ('binding_hash','graph_hash','flow_hash','policy_hash','capabilities_hash','roles_hash')},
        'allowed_triggers':['new_data','daily_time'],'time':'08:00','provider_ids':['received_camera_frame_v1']}
    declaration['admission_hash']=digest(declaration)
    monkeypatch.setattr(service,'_declaration',lambda *args:copy.deepcopy(declaration))
    monkeypatch.setattr(admission,'policy',lambda *args:copy.deepcopy(declaration))
    return NS(s=s,p=s.source.p,bound=s.source.bound,client=s.client,
        path='/api/v2/instances/'+s.instance_id+'/business-automations',declaration=declaration)


def preview(c,trigger='new_data'):
    selected={**c.s.selection,'trigger_id':trigger}
    response=c.client.get(c.path+'/options',params={'release_id':selected['release_id'],'business_id':selected['business_id']})
    assert response.status_code==200,response.text[:300]
    body={'schema':'business-automation-preview-request-v1','selection':selected,'expected_options_hash':response.json()['options_hash']}
    response=c.client.post(c.path+'/preview',content=raw(body))
    assert response.status_code==200,response.text[:300]
    assert response.json()['can_enable'] is True,response.json()['blockers']
    return selected,response.json()


def arm(c,trigger='new_data'):
    selected,value=preview(c,trigger)
    body={'schema':'business-automation-enable-v1','request_id':str(uuid4()),'selection':selected,
        'expected_preview_hash':value['preview_hash'],'confirm_continuous':True}
    exact=raw(body);response=c.client.post(c.path,content=exact)
    assert response.status_code==201,response.text[:350]
    return body,exact,response.json()


def receive_new(c):
    with Session(c.bound) as db:
        camera=db.get(m.MediaChannel,c.s.source.camera_id)
        channel={'id':camera.id,'version':camera.version}
        installation={'id':db.get(m.StreamSession,c.s.source.session_id).data['installation_id']}
    queued=capture(c.client,channel,installation)
    assert queued.status_code==201,queued.text[:300]
    captured,run=finish(c.client,queued.json())
    assert captured['status']=='captured' and run['status']=='succeeded'
    with Session(c.bound) as db:video_daily.reconcile(db,c.p.workspace_id);db.commit()
    assert delivery.consume_once(c.p.workspace_id)['received']>=1
    return run['id']


def get(c,identity):
    response=c.client.get(c.path+'/'+identity)
    assert response.status_code==200,response.text[:350]
    return response.json()


def test_preview_does_not_arm_and_nonce_recovers_across_sessions_without_worker(case):
    c=case
    with Session(c.bound) as db:
        before={model.__name__:db.scalar(select(func.count()).select_from(model)) for model in (Subscription,m.Task,m.Job,m.CloudCall)}
    _,value=preview(c)
    assert value['source_state']=='ready'  # A recent old source is displayable but cannot trigger new_data.
    with Session(c.bound) as db:
        assert before=={model.__name__:db.scalar(select(func.count()).select_from(model)) for model in (Subscription,m.Task,m.Job,m.CloudCall)}
    body,exact,saved=arm(c);identity=saved['subscription']['id']
    again=c.client.post(c.path,content=exact)
    assert again.status_code==200 and again.json()['subscription']['id']==identity
    recovered=c.client.get(c.path+'/requests/'+body['request_id'],params={'expected_sha256':hashlib.sha256(exact).hexdigest()})
    assert recovered.status_code==200 and recovered.json()['found'] is True
    assert get(c,identity)['service']['state']=='unconfirmed'
    consumer.step(c.p.workspace_id)
    current=get(c,identity)
    assert current['service']['state']=='confirmed_recently' and current['occurrences']==[]
    assert current['version']==saved['subscription']['version']


def test_real_new_ack_product_consumer_creates_one_normal_task_and_repeated_daily_revision_cannot_duplicate(case):
    c=case;_,_,saved=arm(c);identity=saved['subscription']['id']
    run=receive_new(c)
    result=consumer.step(c.p.workspace_id,subscription_id=identity)
    assert result['processed'] is True
    current=get(c,identity);assert len(current['occurrences'])==1
    occurrence=current['occurrences'][0]
    assert occurrence['status']=='task_created' and occurrence['task_id'] and occurrence['task_path'].startswith('/api/workspace/tasks/')
    with Session(c.bound) as db:
        task=db.get(m.Task,occurrence['task_id']);witness=service.checked_task_witness(db,c.p,task)
        assert witness['unit']['run_id']==run and task.owner_id==c.p.user_id
        assert task.data['creation_request_id'].startswith('auto_') and task.data['image_snapshots']
        assert db.get(m.Job,occurrence['id']).attempts==1
        assert admission.verify_task(db,c.p,task).id==identity
        tasks=db.scalar(select(func.count()).select_from(m.Task))
        video_daily.reconcile(db,c.p.workspace_id);db.commit()
    delivery.consume_once(c.p.workspace_id)
    for _ in range(3):consumer.step(c.p.workspace_id,subscription_id=identity)
    assert get(c,identity)['occurrences']==current['occurrences']
    with Session(c.bound) as db:assert db.scalar(select(func.count()).select_from(m.Task))==tasks


def test_pause_stops_future_starts_and_fresh_resume_creates_new_authority_without_backfilling(case):
    c=case;_,_,saved=arm(c);old=saved['subscription']
    pause={'schema':'business-automation-pause-v1','request_id':str(uuid4()),'version':old['version'],'reason':'按业务停用未来启动'}
    response=c.client.post(c.path+'/'+old['id']+'/pause',content=raw(pause))
    assert response.status_code==200,response.text[:300]
    receive_new(c);assert consumer.step(c.p.workspace_id)['processed'] is False
    assert get(c,old['id'])['occurrences']==[]
    selected,value=preview(c)
    resume={'schema':'business-automation-resume-v1','request_id':str(uuid4()),'version':response.json()['subscription']['version'],
        'selection':selected,'expected_preview_hash':value['preview_hash'],'confirm_continuous':True}
    response=c.client.post(c.path+'/'+old['id']+'/resume',content=raw(resume))
    assert response.status_code==201,response.text[:300]
    new=response.json()['subscription']
    assert new['id']!=old['id'] and new['supersedes_id']==old['id']
    assert get(c,old['id'])['status']=='superseded'
    consumer.step(c.p.workspace_id);assert get(c,new['id'])['occurrences']==[]


def test_cas_unknown_nonce_different_raw_and_deleted_witness_are_conflicts(case):
    c=case;body,exact,saved=arm(c);identity=saved['subscription']['id']
    changed=copy.deepcopy(body);changed['selection']['limits']['min_interval_seconds']=2
    assert c.client.post(c.path,content=raw(changed)).status_code==409
    wrong={'schema':'business-automation-pause-v1','request_id':str(uuid4()),'version':99,'reason':'陈旧版本'}
    assert c.client.post(c.path+'/'+identity+'/pause',content=raw(wrong)).status_code==409
    with Session(c.bound) as db:
        event=db.scalar(select(m.Audit).where(m.Audit.action==service.CREATED,m.Audit.object_id==identity))
        db.delete(event);db.commit()
    assert c.client.get(c.path+'/'+identity).status_code==409
    assert c.client.get(c.path+'/requests/'+body['request_id'],params={'expected_sha256':hashlib.sha256(exact).hexdigest()}).status_code==409


def test_two_consumers_use_same_occurrence_and_normal_exclusive_job_lease(case,monkeypatch):
    from generic_agents import worker
    c=case;_,_,saved=arm(c);identity=saved['subscription']['id'];receive_new(c)
    entered=threading.Event();release=threading.Event();real=worker.claim
    def claim(*args,**kwargs):
        value=real(*args,**kwargs)
        if value:entered.set();assert release.wait(8)
        return value
    monkeypatch.setattr(worker,'claim',claim)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(consumer.step,c.p.workspace_id,subscription_id=identity)
        try:
            assert entered.wait(8)
            second=pool.submit(consumer.step,c.p.workspace_id,subscription_id=identity)
            other=second.result(timeout=8)
        finally:release.set()
        initial=first.result(timeout=15)
    assert initial['processed'] is True and other['processed'] is False
    assert len(get(c,identity)['occurrences'])==1
    with Session(c.bound) as db:
        job=db.get(m.Job,get(c,identity)['occurrences'][0]['id'])
        assert job.attempts==1 and job.status=='succeeded'


def test_foreign_old_and_cloud_jobs_are_never_claimed_even_when_rule_running(case):
    c=case;arm(c)
    with Session(c.bound) as db:
        foreign=m.Workspace(name='独立工作区');db.add(foreign);db.flush()
        jobs=[]
        for kind,workspace in [('execute_plan',c.p.workspace_id),('data_query',c.p.workspace_id),
                               (JOB_KIND,foreign.id),('execute_full_semantic_graph_node',c.p.workspace_id)]:
            job=m.Job(workspace_id=workspace,kind=kind,idempotency_key='negative-sentinel-'+str(uuid4()),data={'actor_id':c.p.user_id})
            db.add(job);db.flush();jobs.append(job.id)
        db.commit()
    for _ in range(3):assert consumer.step(c.p.workspace_id)['processed'] is False
    with Session(c.bound) as db:
        assert all(db.get(m.Job,identity).attempts==0 and db.get(m.Job,identity).status=='queued' for identity in jobs)


def test_runtime_due_column_is_not_an_unsigned_authorization_to_skip_or_invent_a_day(case):
    c=case;_,_,saved=arm(c,'daily_time');identity=saved['subscription']['id']
    with Session(c.bound) as db:
        row=db.get(Subscription,identity);row.next_due_at+=timedelta(days=1);db.commit()
    diagnostics=[]
    assert consumer.step(c.p.workspace_id,diagnostic=lambda message,identity:diagnostics.append(identity))['processed'] is False
    assert diagnostics==[identity] and get(c,identity)['occurrences']==[]


def test_second_scope_sql_candidates_still_cannot_claim_fabricated_task_association(case):
    """Negative graph/link sentinels test SQL routing, never a fake success."""
    c=case;_,_,saved=arm(c);receive_new(c)
    assert consumer.step(c.p.workspace_id,subscription_id=saved['subscription']['id'])['processed'] is True
    task_id=get(c,saved['subscription']['id'])['occurrences'][0]['task_id']
    other=str(uuid4())
    with Session(c.bound) as db:
        sentinel=m.Job(workspace_id=c.p.workspace_id,kind='execute_full_semantic_graph_node',
            idempotency_key='negative-scope-graph-'+str(uuid4()),data={'actor_id':c.p.user_id,'task_id':task_id,'activation_id':'not-a-real-activation'})
        link=m.Job(workspace_id=c.p.workspace_id,kind=JOB_KIND,idempotency_key='negative-scope-link-'+str(uuid4()),
            data={'actor_id':c.p.user_id,'subscription_id':other,'state':{'task_id':task_id,'status':'linked_existing_task'}})
        db.add_all([sentinel,link]);db.flush();sentinel_id=sentinel.id;db.commit()
        assert sentinel_id in [item[0] for item in consumer._page(db,c.p.workspace_id,None,20,other)]
    errors=[]
    result=consumer.step(c.p.workspace_id,subscription_id=other,diagnostic=lambda message,identity:errors.append(identity))
    assert result['processed'] is False and sentinel_id in errors
    with Session(c.bound) as db:
        assert db.get(m.Job,sentinel_id).attempts==0 and db.get(m.Job,sentinel_id).status=='queued'


def test_daily_before_due_waits_then_original_empty_occurrence_expires_without_task(case,monkeypatch):
    c=case;_,_,saved=arm(c,'daily_time');identity=saved['subscription']['id']
    with Session(c.bound) as db:
        row=db.get(Subscription,identity);due=row.next_due_at;created=row.authorized_at
        assert due>created
    # Boundary unit test with a named clock seam; Root owns actual DB-time tick acceptance.
    monkeypatch.setattr(service,'clock',lambda db:due-timedelta(microseconds=1))
    consumer.step(c.p.workspace_id);assert get(c,identity)['occurrences']==[]
    # The genuine old input becomes stale; cannot manufacture an empty Task.
    monkeypatch.setattr(service,'clock',lambda db:due+timedelta(days=1,hours=1))
    consumer.step(c.p.workspace_id)
    current=get(c,identity)
    assert any(row['status']=='skipped' for row in current['occurrences'])
    assert all(row['task_id'] is None for row in current['occurrences'])


def test_real_automatic_task_waits_actor_before_instance_while_v241_report_holds_user_exclusive(case,monkeypatch):
    """Actual PG/User EX and actual two worker handlers, with bounded barrier."""
    from generic_agents import runtime_documents as documents, runtime_document_receipts as receipts, worker
    from generic_agents.runtime_document_models import RuntimeDocumentVersion as Version
    from tests.test_runtime_documents_pg_v205 import uploaded, run
    from tests.test_document_question_api_v241 import body as question_body, ROOT as QUESTIONS
    c=case;s=NS(client=c.client,workspace=c.p.workspace_id,p=c.p)
    upload,_=uploaded(s,c.s.release,'摄像资料必须逐项核对接收记录。'.encode())
    assert upload.status_code==201,upload.text[:300]
    version_id=upload.json()['latest_version_id']
    assert run(s,version_id)=='succeeded';receipts.consume_once(c.p.workspace_id)
    report=NS(s=s,release=c.s.release,version_id=version_id)
    original=question_body(report)
    response=c.client.post(QUESTIONS,content=raw(original))
    assert response.status_code==201,response.text[:300]
    parent_id=response.json()['view']['query']['job']['id']
    _,_,saved=arm(c);identity=saved['subscription']['id'];receive_new(c)
    with Session(c.bound) as db:
        service.poll_subscription(db,c.p,identity);db.commit()
        job=db.scalar(select(m.Job).where(m.Job.kind==JOB_KIND,m.Job.data['subscription_id'].as_string()==identity))
        occurrence_id=job.id
    actor_held=threading.Event();release=threading.Event();actual=documents.current_actor
    def held_actor(db,p):
        actual(db,p)
        if not actor_held.is_set():actor_held.set();assert release.wait(8)
    monkeypatch.setattr(documents,'current_actor',held_actor)
    with ThreadPoolExecutor(max_workers=2) as pool:
        report_future=pool.submit(worker.process_once,parent_id,kinds=['data_query'],deliver=False,workspace_id=c.p.workspace_id)
        try:
            assert actor_held.wait(8)
            automatic=pool.submit(worker.process_once,occurrence_id,kinds=[JOB_KIND],deliver=False,workspace_id=c.p.workspace_id)
            # A live Instance lock proves automation has not acquired the
            # reverse lock before waiting for the report's User EX.
            with Session(c.bound) as probe:
                from sqlalchemy import text
                probe.execute(text("SET LOCAL lock_timeout='2s'"))
                probe.get(m.DomainInstance,c.s.instance_id,with_for_update=True)
                assert not automatic.done()
        finally:release.set()
        assert report_future.result(timeout=20) is True
        assert automatic.result(timeout=20) is True
    current=get(c,identity)
    assert current['occurrences'][0]['status']=='task_created'
    report_view=c.client.get(QUESTIONS+'/'+response.json()['view']['query']['id'])
    assert report_view.status_code==200 and report_view.json()['stage'] in {'waiting_document_search','waiting_search_settlement','answering'}
