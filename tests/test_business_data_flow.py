"""Owned-PG integration: confirmed work -> automatic plan -> real local data -> query.

Synthetic measurements exercise durable product plumbing, not field accuracy.
"""
import copy
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents.models import Capability, CloudCall, Event, InstanceRelease, Invocation, Job, Memory, Observation
from generic_agents.worker import deliver_events, process_once
from tests.test_instance_workspace import configuration, object_row, prepared
from tests.test_workflow_abilities import enable
from tests.test_vertical_slice import manifest, publish


def setup_flow(client,hold_delivery=None):
    cap=publish(client,manifest('tool',name='declared_measurement_rule',allowed_agent_roles=['perception'],
        input_schema={'type':'object','required':['attributes'],'additionalProperties':False,
            'properties':{'attributes':{'type':'object'}}},
        output_schema={'type':'object','required':['attributes','anomalies','method'],
            'properties':{'attributes':{'type':'object','title':'提取字段'},
                'anomalies':{'type':'array','items':{'type':'object'},'title':'异常线索'},
                'method':{'const':'registered_numeric_rules','title':'处理方法'}}},
        execution={'adapter':'builtin','handler':'numeric_rules'},
        specification={'rules':[{'id':'high','field':'公开登记值','operator':'gt','threshold':50}]},
        data_policy={'cloud_allowed':False}))
    c=configuration();f=c['flows']['资料核验'];step=f['steps'][0]
    step.update(agent='多模感知智能体',role='',name='识别登记异常',condition='',
        execution={'capability_id':cap['id'],'automation_action':'observe',
            'bindings':{'attributes':{'source':'object_attributes'}}})
    c['automation']['policies']['资料核验']['rules']['observe']={'mode':'自动运行'}
    f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    release,body,check=prepared(client,c);enable(client,release);obj=object_row(client,release)
    source=publish(client,manifest('source',name='declared_manual_source',allowed_agent_roles=['perception'],
        execution={'adapter':'upload'},specification={'timezone':'Asia/Shanghai'},data_policy={'cloud_allowed':False}))
    clock=datetime.now(timezone.utc)-timedelta(seconds=10)
    request={'source_id':source['id'],'source_record_id':str(uuid4()),'entity_id':obj['id'],'extractor_id':cap['id'],
        'attributes':{'公开登记值':65},'observed_at':clock.isoformat(),
        'business_day':str((clock+timedelta(hours=8)).date()),'startup_mode':'manual'}
    response=client.post('/api/workspace/instances/'+release['instance_id']+'/reported-observations',json=request)
    assert response.status_code==201,response.text
    obs=response.json()
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.data['observation_id'].as_string()==obs['id']))
        job_id=job.id
    if hold_delivery is not None:
        with hold_delivery.context() as patch:
            patch.setattr('generic_agents.worker.deliver_events',lambda **kwargs:None)
            assert process_once(job_id,kinds=['process_reported_observation'],deliver=False)
    else:assert process_once(job_id,kinds=['process_reported_observation'],deliver=False)
    return release,body,check,obj,obs


def query(client,obj,requirement=False):
    body={'question':'核对已登记的数值与来源','entity_ids':[obj['id']],'fields':['公开登记值'],'mode':'structured'}
    if requirement:
        body['evidence_requirement']={'source':'observations','minimum_verification':'unverified_extraction',
            'time':{'mode':'latest','timezone':'Asia/Shanghai','max_age_seconds':3600}}
    return client.post('/api/data/queries',json=body)


def test_actual_plan_reception_query_reopen_and_idempotence(client):
    release,_,check,obj,obs=setup_flow(client)
    plan=check['data_requirements'];output=plan['businesses'][0]['outputs'][0]
    assert plan['origin']=='system_design' and output['step_name']=='识别登记异常'
    assert output['input_bindings']=={'attributes':{'source':'object_attributes'}}
    assert {f['name'] for f in output['fields']}=={'attributes','anomalies','method'}
    reopened=client.get('/api/instances/'+release['instance_id']+'/releases').json()[0]
    assert reopened['snapshot']['data_requirements']==plan
    with Session(engine()) as db:
        event=db.scalar(select(Event).where(Event.kind=='observation.data',Event.data['observation_id'].as_string()==obs['id']))
        assert event.status=='received',event.data
        memories=db.scalars(select(Memory).where(Memory.data['event_id'].as_string()==event.id)).all()
        assert len(memories)==1 and memories[0].status=='candidate' and memories[0].data['not_authoritative']
        receipt=memories[0].data['data_reception']
        assert receipt['requirements_hash']==plan['requirements_hash']
        assert receipt['release_id']==release['id']
        before=copy.deepcopy(receipt)
    deliver_events(observation_id=obs['id'])
    for with_requirement in (False,True):
        response=query(client,obj,with_requirement);assert response.status_code==200,response.text
        rows=response.json()['data']['result']['observation_evidence'];assert len(rows)==1
        assert rows[0]['attributes']=={'公开登记值':65}
        assert rows[0]['verification']=='unverified_extraction' and rows[0]['data_reception']==before
    with Session(engine()) as db:
        assert len(db.scalars(select(Memory).where(Memory.data['event_id'].as_string()==event.id)).all())==1
        assert not db.scalars(select(CloudCall)).all()


@pytest.mark.parametrize('target',['snapshot_contract','package_marker','observation_output','observation_receipt','memory_receipt','event_payload'])
def test_tampered_lineage_is_blocked_in_both_query_paths(client,target):
    release,_,_,obj,obs=setup_flow(client)
    with Session(engine()) as db:
        row=db.get(Observation,obs['id']);data=copy.deepcopy(row.data)
        event=db.scalar(select(Event).where(Event.kind=='observation.data',Event.data['observation_id'].as_string()==obs['id']))
        if target=='snapshot_contract':
            r=db.get(InstanceRelease,release['id']);d=copy.deepcopy(r.data);del d['snapshot']['data_requirements'];r.data=d
        elif target=='package_marker':
            p=db.get(Capability,release['package_id']);d=copy.deepcopy(p.data);del d['specification']['data_requirements_hash'];p.data=d
        elif target=='observation_output':
            data['extracted']['attributes']['公开登记值']=1;row.data=data
        elif target=='observation_receipt':
            del data['data_reception'];row.data=data
        elif target=='memory_receipt':
            memory=db.scalar(select(Memory).where(Memory.data['event_id'].as_string()==event.id));d=copy.deepcopy(memory.data);del d['data_reception'];memory.data=d
        else:
            d=copy.deepcopy(event.data);d['extracted']['attributes']['公开登记值']=1;event.data=d
        db.commit()
    for required in (False,True):
        response=query(client,obj,required);assert response.status_code==409,response.text


def test_changed_configuration_requires_new_check_without_rebinding_saved_plan(client):
    release,body,check,_,_=setup_flow(client)
    draft=client.get('/api/setup/draft').json();config=draft['configuration']
    config['flows']['资料核验']['steps'][0]['purpose']='新的业务要求，尚未确认'
    saved=client.put('/api/setup/draft',json={'revision':draft['revision'],'configuration':config})
    assert saved.status_code==200,saved.text
    next_check=client.post('/api/instances/check',json={**body,'revision':saved.json()['revision']})
    assert next_check.status_code==200,next_check.text
    assert next_check.json()['data_requirements']['businesses'][0]['status']=='blocked'
    assert next_check.json()['configuration_hash']!=check['configuration_hash']
    old=client.get('/api/instances/'+release['instance_id']+'/releases').json()[0]
    assert old['snapshot']['data_requirements']==release['snapshot']['data_requirements']


def test_blocked_delivery_preserves_output_and_recovers_same_event_without_extraction(client,monkeypatch):
    release,_,_,obj,obs=setup_flow(client,hold_delivery=monkeypatch)
    with Session(engine()) as db:
        row=db.get(Observation,obs['id']);original=copy.deepcopy(row.data['extracted'])
        event=db.scalar(select(Event).where(Event.kind=='observation.data',Event.data['observation_id'].as_string()==obs['id']))
        event_id=event.id;d=copy.deepcopy(event.data);d['extracted']['attributes']['公开登记值']=1;event.data=d;db.commit()
    deliver_events(observation_id=obs['id'],event_kind='observation.data')
    url='/api/workspace/instances/'+release['instance_id']+'/reported-observations'
    record=client.get(url).json()['records'][0]
    assert not record['data_received'] and record['data_delivery']['status']=='blocked'
    assert not record['can_verify'] and record['data_delivery']['retry_available']
    retry=url+'/'+obs['id']+'/retry-data-delivery'
    body={'expected_version':record['version'],'reason':'恢复原保存回执后核对接收'}
    assert client.post(retry,json=body).status_code==409
    with Session(engine()) as db:
        event=db.get(Event,event_id);assert event.status=='blocked_delivery'
        assert not db.scalar(select(Memory).where(Memory.data['event_id'].as_string()==event_id))
        d=copy.deepcopy(event.data);d['extracted']=original;event.data=d;db.commit()
    for _ in range(2):
        response=client.post(retry,json=body);assert response.status_code==200,response.text
        assert response.json()['data_received']
    assert query(client,obj).status_code==200
    with Session(engine()) as db:
        assert db.get(Event,event_id).status=='received'
        assert len(db.scalars(select(Memory).where(Memory.data['event_id'].as_string()==event_id)).all())==1
        assert len(db.scalars(select(Invocation)).all())==1
        assert db.get(Observation,obs['id']).data['extracted']==original
        assert not db.scalars(select(CloudCall)).all()
