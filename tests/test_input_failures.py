"""Real local validation errors, not mocked model results or app DB fault injection."""
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import copy
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Observation,Job,Event,Memory,ObjectState,CloudCall,Entity
from generic_agents.worker import process_once,deliver_events
from generic_agents.state_refresh import KIND
from tests.test_mixed_evidence import setup


def bad(client):
    from tests.test_instance_workspace import prepared,enable,object_row
    from tests.test_vertical_slice import manifest,publish
    r,_,_=prepared(client);enable(client,r);obj=object_row(client,r)
    source=publish(client,manifest('source',name='invalid_manual_source',allowed_agent_roles=['perception'],execution={'adapter':'upload'},specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
    rule=publish(client,manifest('tool',name='invalid_local_threshold',allowed_agent_roles=['perception'],execution={'adapter':'builtin','handler':'numeric_rules'},specification={'rules':[{'id':'high','field':'公开登记值','operator':'gt','threshold':50}]},data_policy={'cloud_allowed':False}))
    clock=datetime.now(timezone.utc)-timedelta(seconds=1)
    body={'source_id':source['id'],'source_record_id':str(uuid4()),'entity_id':obj['id'],'extractor_id':rule['id'],
        'observed_at':clock.isoformat(),'business_day':str((clock+timedelta(hours=8)).date()),'startup_mode':'manual','attributes':{'公开登记值':None}}
    response=client.post('/api/workspace/instances/'+r['instance_id']+'/reported-observations',json=body)
    assert response.status_code==201,response.text
    obs=response.json()
    with Session(engine()) as db:job=db.scalar(select(Job).where(Job.idempotency_key=='extract:'+obs['id']));jid=job.id
    assert process_once(jid,deliver=False)
    deliver_events(observation_id=obs['id'])
    return r,obj,obs,body,jid


def refresh_jobs(obj):
    with Session(engine()) as db:ids=[j.id for j in db.scalars(select(Job).where(Job.kind==KIND,Job.status=='queued',
        Job.data['state_request']['entity_id'].as_string()==obj['id']))]
    for identity in ids:process_once(identity,deliver=False)


def test_invalid_rule_value_does_not_silently_pass_or_imply_no_anomaly(client):
    r,obj,obs,body,jid=bad(client);refresh_jobs(obj)
    with Session(engine()) as db:
        row=db.get(Observation,obs['id']);job=db.get(Job,jid)
        assert row.status=='failed' and job.status=='failed'
        assert all(row.data['original_request'][k]==v for k,v in body.items())
        event=db.scalar(select(Event).where(Event.kind=='observation.failed'))
        assert event.status=='received' and event.data['attempt']==1 and event.data['invocation_id']
        state=db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id']))
        assert state.data['status']=='insufficient' and state.data['input_issues'][0]['stage']=='failed'
        assert not state.data['fields'] and not state.data['current_state_verified']
        assert not list(db.scalars(select(CloudCall)))


def test_retry_request_is_idempotent_and_second_failure_is_independently_delivered(client):
    r,obj,obs,body,jid=bad(client)
    with Session(engine()) as db:version=db.get(Observation,obs['id']).version
    retry={'expected_version':version,'reason':'核对原输入，原值不修改','request_id':str(uuid4())}
    url='/api/observations/'+obs['id']+'/retry';response=client.post(url,json=retry);assert response.status_code==200,response.text
    assert client.post(url,json=retry).status_code==200
    assert process_once(jid,deliver=False);deliver_events(observation_id=obs['id']);refresh_jobs(obj)
    assert client.post(url,json=retry).status_code==200
    assert client.post(url,json={**retry,'reason':'不能替换原重试理由'}).status_code==409
    with Session(engine()) as db:
        events=list(db.scalars(select(Event).where(Event.kind=='observation.failed').order_by(Event.created_at)))
        assert [e.data['attempt'] for e in events]==[1,2] and all(e.status=='received' for e in events)
        assert len(list(db.scalars(select(Memory).where(Memory.data['type'].as_string()=='observation_failure'))))==2
        row=db.get(Observation,obs['id']);assert all(row.data['original_request'][k]==v for k,v in body.items()) and db.get(Job,jid).attempts==2
        assert len(row.data['retry_requests'])==1


def test_pending_input_keeps_old_value_but_never_looks_like_new_success(client):
    r,obj,obs,body=setup(client)
    pending=client.post('/api/observations',json={**body,'source_record_id':str(uuid4()),'attributes':{'公开登记值':39.5}})
    assert pending.status_code==201
    view=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state").json()['state']
    assert view['fields']['公开登记值']['value']==27.5 and view['status']=='insufficient'
    assert view['input_issues'][0]['stage']=='received'
    assert {'field':None,'reason':'incomplete_input','message':'有待处理或失败的新输入；已保存字段不是这些输入的处理结果'} in view['gaps']
    detail=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state-inputs/{pending.json()['id']}",params={'expected_state_hash':view['state_hash']})
    assert detail.status_code==200 and detail.json()['original_attributes']=={'公开登记值':39.5}
    assert not detail.json()['retry_available']


def test_changed_original_or_scope_never_displays_original_values_as_verified(client):
    r,obj,obs,body,jid=bad(client)
    with Session(engine()) as db:row=db.get(Observation,obs['id']);data=copy.deepcopy(row.data);data['attributes']['公开登记值']=99;row.data=data;db.commit()
    response=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state")
    view=response.json()['state'];issue=view['input_issues'][0]
    assert not issue['input_integrity_verified']
    detail=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state-inputs/{obs['id']}",params={'expected_state_hash':view['state_hash']}).json()
    assert detail['original_attributes'] is None and not detail['retry_available']
    assert client.post('/api/observations/'+obs['id']+'/retry',json={'expected_version':issue['observation_version'],'reason':'原输入不一致不得重试','request_id':str(uuid4())}).status_code==409


def test_retry_journal_integrity_and_state_scope_are_checked(client):
    r,obj,obs,body,jid=bad(client)
    retry={'expected_version':2,'reason':'核对原输入','request_id':str(uuid4())}
    with Session(engine()) as db:retry['expected_version']=db.get(Observation,obs['id']).version
    url='/api/observations/'+obs['id']+'/retry'
    assert client.post(url,json=retry).status_code==200
    assert process_once(jid,deliver=False);deliver_events(observation_id=obs['id'])
    with Session(engine()) as db:
        row=db.get(Observation,obs['id']);data=copy.deepcopy(row.data);data['retry_requests'][0]['reason']='changed';row.data=data;db.commit()
    state=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state").json()['state']
    detail_url=f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state-inputs/{obs['id']}"
    view=client.get(detail_url,params={'expected_state_hash':state['state_hash']}).json()
    assert not view['retry_available'] and not view['retry_receipts_verified'] and view['retry_requests']==[]
    assert client.post(url,json=retry).status_code==409
    assert client.get(detail_url,params={'expected_state_hash':'0'*64}).status_code==409
    from tests.test_instance_workspace import object_row
    other=object_row(client,r)
    assert client.get(detail_url.replace(obj['id'],other['id']),params={'expected_state_hash':state['state_hash']}).status_code==404


def test_unrelated_invocation_never_becomes_verified_failure_receipt(client):
    r,obj,obs,body,jid=bad(client)
    from generic_agents.models import Invocation
    with Session(engine()) as db:
        event=db.scalar(select(Event).where(Event.kind=='observation.failed'))
        real=db.get(Invocation,event.data['invocation_id']);call=Invocation(workspace_id=real.workspace_id,capability_id=real.capability_id,idempotency_key='unrelated-input',status=real.status,data=copy.deepcopy(real.data));db.add(call);db.flush();event.data={**event.data,'invocation_id':call.id};db.commit()
    state=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state").json()['state']
    detail=client.get(f"/api/workspace/instances/{r['instance_id']}/objects/{obj['id']}/state-inputs/{obs['id']}",params={'expected_state_hash':state['state_hash']}).json()
    assert not detail['failure_history'][0]['has_extraction_receipt']
