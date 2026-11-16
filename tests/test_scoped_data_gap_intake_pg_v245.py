"""Actual scoped setup loop: saved need -> independent ACK -> field recheck.

No global worker, invented processed Observation or fake Event/Memory is used
for success. Only unrelated timer roots/signals are substituted to bound the
test loop; normal dispatch, numeric extraction and reception stay production.
"""
import copy
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import func,select
from sqlalchemy.orm import Session
from generic_agents import models as m,worker,workspace_observation_intake as intake
from generic_agents.db import engine
from generic_agents.instance_data_receipts import observation_receipt
from generic_agents.security import Principal
from tests.test_instance_workspace import prepared,enable,object_row
from tests.test_vertical_slice import manifest,publish
from tests.test_workspace_observation_intake_pg import run_default,foreign_rows,inventory


def good(response,status=200):
    assert response.status_code==status,response.text[:600]
    return response.json()


@pytest.fixture
def scene(client):
    release,_,_=prepared(client);enable(client,release);obj=object_row(client,release,1)
    field=next(value['key'] for value in release['snapshot']['object_types'][1]['fields'] if value['kind']=='整数')
    source=publish(client,manifest('source',name='scoped_numeric_source_'+uuid4().hex[:8],execution={'adapter':'upload'},
        specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False,'external_allowed':False}))
    tool=publish(client,manifest('tool',name='scoped_numeric_rule_'+uuid4().hex[:8],allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules','side_effect':False},specification={'rules':[]},
        data_policy={'cloud_allowed':False,'external_allowed':False}))
    me=good(client.get('/api/me'));p=Principal(me['user_id'],me['workspace_id'],'admin','scoped-data-intake-test')
    return NS(client=client,bound=engine(),workspace_id=p.workspace_id,actor_id=p.user_id,p=p,release=release,
        package={'id':release['package_id']},entity=obj,field=field,source=source,tool=tool)


def query(s):
    # This is direct field retrieval, not an aggregate/count request. The
    # public query contract intentionally asks for clarification on "数量".
    return good(s.client.post('/api/data/queries',json={'question':'读取所选对象指定字段的观测记录',
        'entity_ids':[s.entity['id']],'fields':[s.field],'mode':'structured','business_timezone':'Asia/Shanghai',
        'evidence_requirement':{'source':'observations','minimum_verification':'unverified_extraction',
            'time':{'mode':'latest','timezone':'Asia/Shanghai','max_age_seconds':3600}}}))


def ingest(s):
    stamp=datetime.now(timezone.utc)-timedelta(seconds=1)
    from zoneinfo import ZoneInfo
    value=good(s.client.post('/api/observations',json={'source_id':s.source['id'],'source_record_id':str(uuid4()),
        'entity_id':s.entity['id'],'observed_at':stamp.isoformat(),
        'business_day':str(stamp.astimezone(ZoneInfo('Asia/Shanghai')).date()),'attributes':{s.field:27},
        'extractor_id':s.tool['id'],'startup_mode':'manual'}),201)
    return value


def event(db,s,identity):
    return db.scalar(select(m.Event).where(m.Event.workspace_id==s.workspace_id,m.Event.kind=='observation.data',
        m.Event.idempotency_key=='data:'+identity))


def gap(db,run):return db.scalar(select(m.DataGap).where(m.DataGap.query_id==run['id']))


def test_actual_scoped_loop_delivers_need_then_observation_ack_and_rechecks_original_gap(scene,monkeypatch):
    s=scene;run=query(s);assert run['status']=='insufficient',run.get('data',{}).get('error')
    observation=ingest(s);foreign,before=foreign_rows(s)
    with Session(s.bound) as db:
        unrelated=m.Event(workspace_id=s.workspace_id,kind='not_selected_v245',idempotency_key='negative-topic-'+uuid4().hex,data={'negative_topic':True})
        unrelated_job=m.Job(workspace_id=s.workspace_id,kind='execute_plan',idempotency_key='negative-kind-'+uuid4().hex,data={'actor_id':s.actor_id})
        db.add_all([unrelated,unrelated_job]);db.flush();untouched=(unrelated.id,unrelated_job.id);db.commit()
    def complete(db):
        received=event(db,s,observation['id']);needed=gap(db,run)
        refresh=db.scalar(select(m.Job.id).where(m.Job.workspace_id==s.workspace_id,m.Job.kind=='refresh_object_state',m.Job.status=='succeeded'))
        return received is not None and received.status=='received' and needed.status=='resolved' and bool(refresh)
    run_default(s,monkeypatch,complete)
    with Session(s.bound) as db:
        original=db.get(m.Observation,observation['id']);received=event(db,s,original.id);needed=gap(db,run)
        receipt=observation_receipt(db,s.p,original)
        assert receipt['data_reception'] and received.data['data_reception']==receipt['data_reception']
        memories=list(db.scalars(select(m.Memory).where(m.Memory.workspace_id==s.workspace_id,
            m.Memory.data['event_id'].as_string()==received.id,m.Memory.data['type'].as_string()=='observation_evidence')))
        assert len(memories)==1 and memories[0].data['data_reception']==receipt['data_reception']
        assert needed.status=='resolved' and needed.data['latest_recheck_query_id']!=run['id']
        child=db.get(m.DataRun,needed.data['latest_recheck_query_id'])
        assert child.status=='sufficient' and child.data['parent_query_id']==run['id']
        assert db.get(m.DataRun,run['id']).status=='insufficient'
        assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id==s.workspace_id)))
        assert {model:inventory(db,model,foreign) for model in before}==before
        assert db.get(m.Event,untouched[0]).status=='pending_delivery' and db.get(m.Event,untouched[0]).attempts==0
        assert db.get(m.Job,untouched[1]).status=='queued' and db.get(m.Job,untouched[1]).attempts==0
        saved={model:inventory(db,model,s.workspace_id) for model in (m.Event,m.Memory,m.Invocation)}
    intake.deliver_pending(s.workspace_id);intake.deliver_pending(s.workspace_id)
    with Session(s.bound) as db:
        assert {model:inventory(db,model,s.workspace_id) for model in saved}==saved


def test_registered_quality_policy_activation_job_is_processed_by_scoped_loop(scene,monkeypatch):
    s=scene
    policy=good(s.client.post('/api/quality/policies',json={'name':'当前已发布业务包的本地检查',
        'package_ids':[s.package['id']],'interval_seconds':3600,'reason':'明确批准当前包本地检查'}),201)
    def complete(db):return bool(db.scalar(select(m.Job.id).where(m.Job.workspace_id==s.workspace_id,
        m.Job.kind=='diagnose_data',m.Job.status=='succeeded',m.Job.data['policy_id'].as_string()==policy['id'])))
    run_default(s,monkeypatch,complete)
    with Session(s.bound) as db:
        job=db.scalar(select(m.Job).where(m.Job.kind=='diagnose_data',m.Job.data['policy_id'].as_string()==policy['id']))
        assert job.attempts==1 and job.status=='succeeded'
        assert not list(db.scalars(select(m.CloudCall)))


def test_received_event_missing_memory_is_not_repaired_or_reextracted(scene,monkeypatch):
    s=scene;observation=ingest(s)
    def complete(db):
        received=event(db,s,observation['id'])
        return received is not None and received.status=='received'
    run_default(s,monkeypatch,complete)
    with Session(s.bound) as db:
        received=event(db,s,observation['id']);memory=db.scalar(select(m.Memory).where(m.Memory.workspace_id==s.workspace_id,
            m.Memory.data['event_id'].as_string()==received.id,m.Memory.data['type'].as_string()=='observation_evidence'))
        assert memory is not None;db.delete(memory);db.commit()
        original_job=db.scalar(select(m.Job).where(m.Job.idempotency_key=='extract:'+observation['id']))
        attempts=original_job.attempts;calls=db.scalar(select(func.count()).select_from(m.Invocation))
    intake.deliver_pending(s.workspace_id)
    with Session(s.bound) as db:
        with pytest.raises(HTTPException) as failure:observation_receipt(db,s.p,db.get(m.Observation,observation['id']))
        assert failure.value.status_code==409
        assert event(db,s,observation['id']).status=='received'
        assert db.scalar(select(m.Job).where(m.Job.idempotency_key=='extract:'+observation['id'])).attempts==attempts
        assert db.scalar(select(func.count()).select_from(m.Invocation))==calls


def test_original_reception_rejects_corrupt_payload_without_memory_or_state_value(scene,monkeypatch):
    s=scene;observation=ingest(s)
    with Session(s.bound) as db:job=db.scalar(select(m.Job).where(m.Job.idempotency_key=='extract:'+observation['id']));identity=job.id
    assert worker.process_once(job_id=identity,kinds=['process_observation'],deliver=False,workspace_id=s.workspace_id)
    with Session(s.bound) as db:
        received=event(db,s,observation['id']);value=copy.deepcopy(received.data)
        value['extracted']['attributes'][s.field]=999;received.data=value;db.commit()
    intake.deliver_pending(s.workspace_id)
    with Session(s.bound) as db:
        received=event(db,s,observation['id']);assert received.status=='blocked_delivery'
        assert not list(db.scalars(select(m.Memory).where(m.Memory.data['event_id'].as_string()==received.id)))
        assert db.get(m.Job,identity).attempts==1
    view=good(s.client.get('/api/workspace/instances/'+s.release['instance_id']+'/objects/'+s.entity['id']+'/state'))
    assert s.field not in view['state']['fields']  # Peer owns the actual ACK gate.
