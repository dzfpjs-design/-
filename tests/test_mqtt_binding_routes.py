"""Disposable PostgreSQL proves one raw packet can serve independent frozen routes."""
import copy
import json
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import time
from threading import Event as ThreadEvent
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import (MqttBinding, MqttBindingRoute, MqttBindingRevision, MqttReceiptDelivery,
    MqttReceipt, Observation, Job, Evidence, CloudCall, User, DomainInstance)
from generic_agents.contracts import digest
from generic_agents.config import settings
from generic_agents.security import Principal
from generic_agents.observation_integrity import checked_report
from generic_agents.perception import object_state
from generic_agents.worker import process_once
from generic_agents.mqtt_observations import guard, promote_delivery
from tests.test_mqtt_observations import prepared_mqtt, save, bind as legacy_bind, process_receipt
from tests.test_vertical_slice import manifest, publish, entity


def create(client, fixture, obj=None, rule=None, **changes):
    gateway, first, default, p = fixture
    body={'expected_version':gateway['version'],'request_id':str(uuid4()),'topic':'sensor/one',
          'entity_id':(obj or first)['id'],'extractor_id':(rule or default)['id'],'include_existing':False}
    body.update(changes)
    response=client.post('/api/mqtt/gateways/'+gateway['id']+'/routes',json=body)
    assert response.status_code==201,response.text
    return response.json(),body


def update(client, route, **changes):
    body={key:route[key] for key in ('topic','entity_id','extractor_id','field_links')}
    body.update(expected_version=route['version'],request_id=str(uuid4()),include_existing=False)
    body.update(changes)
    return client.put('/api/mqtt/routes/'+route['id'],json=body),body


def pending_jobs(receipt_id):
    with Session(engine()) as db:
        return db.scalars(select(Job.id).where(Job.kind=='submit_mqtt_receipt',
            Job.data['receipt_id'].as_string()==receipt_id).order_by(Job.id)).all()


def process_all(receipt_id):
    for identity in pending_jobs(receipt_id):
        with Session(engine()) as db:ready=db.get(Job,identity).status=='queued'
        if ready:assert process_once(identity,deliver=False)
    with Session(engine()) as db:
        deliveries=db.scalars(select(MqttReceiptDelivery).where(MqttReceiptDelivery.receipt_id==receipt_id)).all()
        observation_ids=[row.observation_id for row in deliveries if row.observation_id]
        jobs=db.scalars(select(Job.id).where(Job.kind=='process_mqtt_observation',
            Job.status=='queued',
            Job.data['observation_id'].as_string().in_(observation_ids))).all()
    for identity in jobs: assert process_once(identity,deliver=False)
    return observation_ids


def other_rule(client,threshold=60,name='higher_rule'):
    return publish(client,manifest('tool',name=name,allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},data_policy={'cloud_allowed':False},
        specification={'rules':[{'id':'hot','field':'temperature','operator':'gt','threshold':threshold}]}))


def test_one_original_two_objects_independent_local_results_and_raw_data(client,prepared_mqtt):
    first,body=create(client,prepared_mqtt)
    second_object=entity(client,{'id':prepared_mqtt[1]['schema_capability_id']})
    second,_=create(client,prepared_mqtt,obj=second_object,rule=other_rule(client))
    receipt,raw=save(prepared_mqtt)
    observations=process_all(receipt.id)
    assert len(observations)==2 and first['source_id']!=second['source_id']
    with Session(engine()) as db:
        rows=db.scalars(select(Observation).order_by(Observation.entity_id)).all()
        assert {row.entity_id for row in rows}=={first['entity_id'],second['entity_id']}
        for row in rows:
            assert row.status=='processed',row.data
            assert checked_report(db,prepared_mqtt[3],row)['attributes']=={'temperature':39.5}
        by_source={row.source_id:row for row in rows}
        assert len(by_source[first['source_id']].data['extracted']['anomalies'])==1
        assert by_source[second['source_id']].data['extracted']['anomalies']==[]
        assert db.scalar(select(func.count()).select_from(MqttReceipt))==1
        assert db.scalar(select(func.count()).select_from(Evidence))==1
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
        assert 'observation_id' not in db.get(MqttReceipt,receipt.id).data
    records=client.get('/api/mqtt/gateways/'+first['gateway_id']+'/receipts').json()
    assert len(records[0]['associations'])==2 and records[0]['deliveries']==1
    assert all(row['processing']['status']=='processed' for row in records[0]['associations'])
    assert client.get('/api/mqtt/receipts/'+receipt.id+'/original').content==raw


def test_default_does_not_backfill_and_explicit_confirmation_is_durable(client,prepared_mqtt):
    before,_=save(prepared_mqtt)
    route,_=create(client,prepared_mqtt)
    assert pending_jobs(before.id)==[]
    response,request=update(client,route,include_existing=True)
    assert response.status_code==200,response.text
    assert len(pending_jobs(before.id))==1
    assert len(process_all(before.id))==1
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(MqttBindingRevision))==2
    # Replaying a lost HTTP response cannot reprocess or create another version.
    again=client.put('/api/mqtt/routes/'+route['id'],json=request)
    assert again.status_code==200 and again.json()['revision']==2
    assert len(pending_jobs(before.id))==1


def test_one_route_revision_keeps_other_rule_and_history_and_fences_old_jobs(client,prepared_mqtt):
    first,_=create(client,prepared_mqtt)
    second_object=entity(client,{'id':prepared_mqtt[1]['schema_capability_id']})
    second,_=create(client,prepared_mqtt,obj=second_object)
    old,_=save(prepared_mqtt)
    old_ids=process_all(old.id)
    waiting,_=save(prepared_mqtt,record='waiting')
    response,_=update(client,first,extractor_id=other_rule(client)['id'])
    assert response.status_code==200,response.text
    assert response.json()['revision']==2
    process_all(waiting.id)
    with Session(engine()) as db:
        jobs=db.scalars(select(Job).where(Job.kind=='submit_mqtt_receipt',
            Job.data['receipt_id'].as_string()==waiting.id)).all()
        assert sorted(row.status for row in jobs)==['failed','succeeded']
        old_rows=[db.get(Observation,identity) for identity in old_ids]
        assert all(checked_report(db,prepared_mqtt[3],row)['attributes']=={'temperature':39.5} for row in old_rows)
        first_old=next(row for row in old_rows if row.entity_id==first['entity_id'])
        with pytest.raises(HTTPException):guard(db,prepared_mqtt[3],first_old,execution=True)
    newest,_=save(prepared_mqtt,record='new')
    new_ids=process_all(newest.id)
    with Session(engine()) as db:
        rows=[db.get(Observation,identity) for identity in new_ids]
        assert {row.source_id for row in rows}=={response.json()['source_id'],second['source_id']}
        assert len(next(row for row in rows if row.entity_id==first['entity_id']).data['extracted']['anomalies'])==0
        assert len(next(row for row in rows if row.entity_id==second['entity_id']).data['extracted']['anomalies'])==1
    history=client.get('/api/mqtt/routes/'+first['id']+'/revisions').json()
    assert [row['revision'] for row in history]==[2,1] and history[1]['binding_id']==first['binding_id']


def test_independent_pause_does_not_reprocess_old_and_cas_or_uuid_cannot_overwrite(client,prepared_mqtt):
    first,original=create(client,prepared_mqtt)
    second_object=entity(client,{'id':prepared_mqtt[1]['schema_capability_id']})
    second,_=create(client,prepared_mqtt,obj=second_object)
    pause={'expected_version':1,'request_id':str(uuid4()),'status':'paused'}
    url='/api/mqtt/routes/'+first['id']+'/status'
    response=client.post(url,json=pause);assert response.status_code==200,response.text
    assert client.post(url,json=pause).json()['version']==2
    collision=client.post(url,json={**pause,'status':'active'});assert collision.status_code==409
    incoming,_=save(prepared_mqtt)
    assert len(process_all(incoming.id))==1
    resume=client.post(url,json={'expected_version':2,'request_id':str(uuid4()),'status':'active'})
    assert resume.status_code==200,resume.text
    assert len(pending_jobs(incoming.id))==1
    response,_=update(client,first,extractor_id=other_rule(client)['id'])
    assert response.status_code==409
    replay=client.post('/api/mqtt/gateways/'+first['gateway_id']+'/routes',json=original)
    assert replay.status_code==201 and replay.json()['id']==first['id']
    later,_=save(prepared_mqtt,record='after-resume')
    assert len(process_all(later.id))==2


def test_legacy_adoption_preserves_original_binding_and_observation_without_double_count(client,prepared_mqtt):
    old_binding=legacy_bind(client,prepared_mqtt)
    receipt,_=save(prepared_mqtt);observation_id=process_receipt(receipt)
    with Session(engine()) as db:
        before=copy.deepcopy(db.get(MqttBinding,old_binding['id']).data)
    routes=client.get('/api/mqtt/gateways/'+old_binding['gateway_id']+'/routes').json()
    assert len(routes)==1 and routes[0]['legacy']
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(MqttBindingRoute))==0
    changed,_=update(client,routes[0],extractor_id=other_rule(client)['id'])
    assert changed.status_code==200,changed.text
    assert changed.json()['id']==old_binding['id'] and changed.json()['revision']==2
    routes=client.get('/api/mqtt/gateways/'+old_binding['gateway_id']+'/routes').json()
    assert len(routes)==1
    with Session(engine()) as db:
        assert db.get(MqttBinding,old_binding['id']).data==before
        assert checked_report(db,prepared_mqtt[3],db.get(Observation,observation_id))['attributes']=={'temperature':39.5}
    incoming,_=save(prepared_mqtt,record='new-legacy')
    assert len(process_all(incoming.id))==1


def test_duplicate_packet_and_conflict_cover_every_destination(client,prepared_mqtt):
    first,_=create(client,prepared_mqtt)
    second_object=entity(client,{'id':prepared_mqtt[1]['schema_capability_id']})
    create(client,prepared_mqtt,obj=second_object)
    clock=(datetime.now(timezone.utc)-timedelta(seconds=4)).isoformat()
    receipt,raw=save(prepared_mqtt,capture_time=clock)
    ids=process_all(receipt.id)
    save(prepared_mqtt,capture_time=clock)
    assert len(pending_jobs(receipt.id))==2
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(Observation))==2
    conflict,_=save(prepared_mqtt,capture_time=clock,value=55)
    assert conflict.status=='quarantined' and pending_jobs(conflict.id)==[]
    with Session(engine()) as db:
        assert all(db.get(Observation,identity).status=='failed' for identity in ids)
        assert {row.status for row in db.scalars(select(MqttReceiptDelivery)).all()}=={'invalidated'}
        for identity in ids:
            row=db.get(Observation,identity)
            assert 'temperature' not in object_state(db,prepared_mqtt[3],row.entity_id,['temperature'],3600)['fields']
    assert client.get('/api/mqtt/receipts/'+receipt.id+'/original').content==raw


def test_tampered_revision_or_delivery_never_changes_other_result(client,prepared_mqtt):
    first,_=create(client,prepared_mqtt)
    second_object=entity(client,{'id':prepared_mqtt[1]['schema_capability_id']})
    second,_=create(client,prepared_mqtt,obj=second_object)
    with Session(engine()) as db:
        row=db.get(MqttBindingRevision,first['binding_id']);row.data={**row.data,'extractor_hash':'forged'};db.commit()
    receipt,_=save(prepared_mqtt)
    assert len(process_all(receipt.id))==1
    with Session(engine()) as db:
        observation=db.scalar(select(Observation));assert observation.source_id==second['source_id']
    # Broken association cannot be represented as a valid current declaration.
    response=client.get('/api/mqtt/gateways/'+first['gateway_id']+'/routes')
    assert response.status_code==409


def test_viewer_cannot_mutate_and_config_change_marks_review_without_rebinding(client,prepared_mqtt):
    route,body=create(client,prepared_mqtt)
    gateway=prepared_mqtt[0];config=copy.deepcopy(gateway['configuration']);config['mapping']['t']['unit']='F'
    response=client.put('/api/mqtt/gateways/'+gateway['id'],json={'expected_version':gateway['version'],
        'name':gateway['name'],'configuration':config});assert response.status_code==200
    got=client.get('/api/mqtt/gateways/'+gateway['id']+'/routes').json()[0]
    assert got['needs_review'] and got['source_id']==route['source_id']
    response=client.post('/api/mqtt/routes/'+route['id']+'/status',json={
        'expected_version':1,'status':'active','request_id':str(uuid4())})
    assert response.status_code==409
    from generic_agents.models import Membership
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.id==prepared_mqtt[3].user_id))
        member=db.scalar(select(Membership).where(Membership.user_id==user.id));member.role='viewer';db.commit()
    assert client.post('/api/mqtt/gateways/'+gateway['id']+'/routes',json={**body,'request_id':str(uuid4())}).status_code==403
    assert client.put('/api/mqtt/routes/'+route['id'],json={**body,'request_id':str(uuid4())}).status_code==403
    assert client.get('/api/mqtt/gateways/'+gateway['id']+'/routes').status_code==200


@pytest.mark.parametrize('field',['include_existing','processing_after','status','current_revision','current_revision_id','requests'])
def test_tampered_execution_scope_never_expands_history_or_blocks_other_route(client,prepared_mqtt,field):
    previous,_=save(prepared_mqtt,record='past')
    first,_=create(client,prepared_mqtt)
    second_object=entity(client,{'id':prepared_mqtt[1]['schema_capability_id']})
    second,_=create(client,prepared_mqtt,obj=second_object)
    with Session(engine()) as db:
        route=db.get(MqttBindingRoute,first['id'])
        if field=='status':route.status='paused'
        elif field=='current_revision':route.current_revision=88
        else:
            value={'include_existing':True,'processing_after':'2000-01-01T00:00:00+00:00',
                   'current_revision_id':second['binding_id'],'requests':{}}[field]
            route.data={**route.data,field:value}
        db.commit()
    from generic_agents.mqtt_observations import schedule_receipts
    with Session(engine()) as db:
        schedule_receipts(db,prepared_mqtt[3],db.get(__import__('generic_agents.models',fromlist=['MqttGateway']).MqttGateway,first['gateway_id']))
        db.commit()
    assert pending_jobs(previous.id)==[]
    current,_=save(prepared_mqtt,record='current')
    ids=process_all(current.id);assert len(ids)==1
    with Session(engine()) as db:assert db.get(Observation,ids[0]).source_id==second['source_id']
    assert client.get('/api/mqtt/gateways/'+first['gateway_id']+'/routes').status_code==409


def test_resume_without_history_permission_fences_existing_queued_delivery(client,prepared_mqtt):
    route,_=create(client,prepared_mqtt)
    receipt,_=save(prepared_mqtt)
    paused=client.post('/api/mqtt/routes/'+route['id']+'/status',json={
        'expected_version':1,'request_id':str(uuid4()),'status':'paused'});assert paused.status_code==200
    resumed=client.post('/api/mqtt/routes/'+route['id']+'/status',json={
        'expected_version':2,'request_id':str(uuid4()),'status':'active'});assert resumed.status_code==200
    assert process_all(receipt.id)==[]
    with Session(engine()) as db:
        job=db.get(Job,pending_jobs(receipt.id)[0]);assert job.status=='failed'
        delivery=db.scalar(select(MqttReceiptDelivery));assert not delivery.observation_id
    assert client.post('/api/mqtt/deliveries/'+delivery.id+'/submit',json={'expected_version':delivery.version}).status_code==409
    record=client.get('/api/mqtt/gateways/'+route['gateway_id']+'/receipts').json()[0]
    assert not record['associations'][0]['can_submit']
    fresh,_=save(prepared_mqtt,record='fresh');assert len(process_all(fresh.id))==1


def test_explicit_existing_scope_keeps_completed_and_creates_independent_unfinished_revision(client,prepared_mqtt):
    route,_=create(client,prepared_mqtt)
    completed,_=save(prepared_mqtt,record='completed');ids=process_all(completed.id)
    queued,_=save(prepared_mqtt,record='queued')
    changed,_=update(client,route,include_existing=True,extractor_id=other_rule(client)['id'])
    assert changed.status_code==200,changed.text
    assert len(pending_jobs(completed.id))==1 and len(pending_jobs(queued.id))==2
    new_ids=process_all(queued.id);assert len(new_ids)==1
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Observation))==2
        assert checked_report(db,prepared_mqtt[3],db.get(Observation,ids[0]))['attributes']=={'temperature':39.5}
        assert db.get(Observation,new_ids[0]).source_id==changed.json()['source_id']
    # A different object can explicitly consume an already stored original for its first association.
    second_object=entity(client,{'id':prepared_mqtt[1]['schema_capability_id']})
    second,_=create(client,prepared_mqtt,obj=second_object,include_existing=True)
    assert len(process_all(completed.id))==2


def test_job_delivery_swap_cannot_execute_another_target(client,prepared_mqtt):
    create(client,prepared_mqtt)
    second_object=entity(client,{'id':prepared_mqtt[1]['schema_capability_id']})
    create(client,prepared_mqtt,obj=second_object)
    receipt,_=save(prepared_mqtt)
    with Session(engine()) as db:
        jobs=db.scalars(select(Job).where(Job.kind=='submit_mqtt_receipt').order_by(Job.id)).all()
        jobs[0].data={**jobs[0].data,'delivery_id':jobs[1].data['delivery_id']};identity=jobs[0].id;db.commit()
    assert process_once(identity,deliver=False)
    with Session(engine()) as db:
        assert db.get(Job,identity).status=='failed'
        assert db.scalar(select(func.count()).select_from(Observation))==0


@pytest.mark.parametrize('field',['route_id','revision','binding_hash','input_sha256','original_target','authorization_hash'])
def test_historical_result_rejects_partial_delivery_chain_tampering(client,prepared_mqtt,field):
    create(client,prepared_mqtt);receipt,_=save(prepared_mqtt);ids=process_all(receipt.id)
    with Session(engine()) as db:
        delivery=db.scalar(select(MqttReceiptDelivery))
        if field=='route_id':delivery.route_id=str(uuid4())
        elif field=='revision':delivery.revision=90
        else:delivery.data={**delivery.data,field:'changed'}
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):checked_report(db,prepared_mqtt[3],db.get(Observation,ids[0]))


def test_failed_extraction_retry_requeues_actual_original_job_once(client,prepared_mqtt):
    route,_=create(client,prepared_mqtt);receipt,_=save(prepared_mqtt);ids=process_all(receipt.id)
    with Session(engine()) as db:
        observation=db.get(Observation,ids[0]);observation.status='failed'
        observation.data={**observation.data,'processing_error':'临时处理失败'}
        extraction=db.scalar(select(Job).where(Job.idempotency_key=='extract:'+observation.id))
        extraction.status='failed';extraction.attempts=1
        delivery=db.scalar(select(MqttReceiptDelivery));version=delivery.version;identity=delivery.id;extract_id=extraction.id
        db.commit()
    request={'expected_version':version}
    response=client.post('/api/mqtt/deliveries/'+identity+'/submit',json=request)
    assert response.status_code==200,response.text
    assert response.json()['status']=='received'
    assert client.post('/api/mqtt/deliveries/'+identity+'/submit',json=request).status_code==200
    with Session(engine()) as db:
        assert db.get(Job,extract_id).status=='queued'
        assert db.get(MqttReceiptDelivery,identity).version==version+1
    assert process_once(extract_id,deliver=False)
    with Session(engine()) as db:assert db.get(Observation,ids[0]).status=='processed'


def managed_pair(client,account,monkeypatch):
    from tests import test_instance_workspace as workspace
    from generic_agents.instance_compiler import signature_value
    definitions=copy.deepcopy(workspace.OBJECTS)
    definitions[0]['fields'].append({'label':'温度','kind':'数值','required':False})
    monkeypatch.setattr(workspace,'OBJECTS',definitions)
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','mqtt://127.0.0.1:1883')
    destinations=[]
    for index in (1,2):
        config=workspace.configuration();config['instanceProfile']['name']='真实业务实例'+str(index)
        flow=config['flows']['资料核验'];flow['confirmedSignature']=json.dumps(signature_value(config,flow),ensure_ascii=False)
        release,_,_=workspace.prepared(client,config);instance=workspace.enable(client,release)
        obj=workspace.object_row(client,release)
        key=next(field['key'] for field in release['snapshot']['object_types'][0]['fields'] if field['label']=='温度')
        rule=publish(client,manifest('tool',name='managed_multi_rule_'+str(index),allowed_agent_roles=['perception'],
            execution={'adapter':'builtin','handler':'numeric_rules'},data_policy={'cloud_allowed':False},
            specification={'rules':[{'id':'hot','field':key,'operator':'gt','threshold':38 if index==1 else 60}]}))
        destinations.append((release,instance,obj,key,rule))
    config={'broker':'mqtt://127.0.0.1:1883','topics':{'sensor/one':'device-one'},
        'mapping':{'t':{'attribute':'temperature','unit':'C'}},'fields':{},'username_ref':'','password_ref':''}
    gateway=client.post('/api/mqtt/gateways',json={'name':'多实例共享设备','configuration':config}).json()
    gateway=client.post('/api/mqtt/gateways/'+gateway['id']+'/status',json={'expected_version':1,'status':'active'}).json()
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','multi-managed-test')
    fixture=(gateway,destinations[0][2],destinations[0][4],p)
    routes=[]
    for release,instance,obj,key,rule in destinations:
        route,_=create(client,fixture,obj=obj,rule=rule,field_links={'temperature':key});routes.append(route)
    return fixture,destinations,routes


def test_two_actual_active_instances_share_original_but_receive_independent_signals_and_state(client,account,monkeypatch):
    from generic_agents.models import PerceptionSignal,ObjectState
    fixture,destinations,routes=managed_pair(client,account,monkeypatch)
    receipt,_=save(fixture);ids=process_all(receipt.id);assert len(ids)==2
    with Session(engine()) as db:
        refreshes=db.scalars(select(Job.id).where(Job.kind=='refresh_object_state',Job.status=='queued')).all()
    for identity in refreshes:assert process_once(identity,deliver=False)
    with Session(engine()) as db:
        for release,instance,obj,key,rule in destinations:
            row=next(db.get(Observation,identity) for identity in ids if db.get(Observation,identity).entity_id==obj['id'])
            assert row.status=='processed',row.data
            assert row.data['extracted']['attributes']=={key:39.5}
            state=db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id']))
            assert state and state.data['fields'][key]['value']==39.5
        signals=db.scalars(select(PerceptionSignal)).all()
        assert len(signals)==1 and signals[0].entity_id==destinations[0][2]['id']
        assert db.scalar(select(func.count()).select_from(MqttReceipt))==1
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
    first=client.get('/api/workspace/instances/'+destinations[0][0]['instance_id']+'/signals').json()
    second=client.get('/api/workspace/instances/'+destinations[1][0]['instance_id']+'/signals').json()
    assert len(first['records'])==1 and second['records']==[]
    assert [row['instance_name'] for row in routes]==['真实业务实例1','真实业务实例2']
    assert all(row['entity_type_label']=='观察场景' and row['entity_label']=='公开资料场景' for row in routes)


def test_final_model_guard_holds_instance_authorization_until_true_commit(client,account,monkeypatch):
    fixture,destinations,routes=managed_pair(client,account,monkeypatch)
    receipt,_=save(fixture)
    with Session(engine()) as db:
        submission=db.scalar(select(Job).where(Job.kind=='submit_mqtt_receipt',
            Job.data['binding_id'].as_string()==routes[0]['binding_id']))
        submit_id=submission.id
    assert process_once(submit_id,deliver=False)
    with Session(engine()) as db:
        delivery=db.scalar(select(MqttReceiptDelivery).where(MqttReceiptDelivery.binding_id==routes[0]['binding_id']))
        identity=delivery.observation_id
        extraction=db.scalar(select(Job).where(Job.idempotency_key=='extract:'+identity));extract_id=extraction.id
    from generic_agents import mqtt_observations
    original=mqtt_observations.guard;ready=ThreadEvent();release_guard=ThreadEvent();paused_started=ThreadEvent();pids=[]
    def gated(db,p,observation,*,execution,result=None):
        output=original(db,p,observation,execution=execution,result=result)
        if execution and result is not None and observation.id==identity:
            pids.append(db.scalar(text('SELECT pg_backend_pid()')));ready.set()
            assert release_guard.wait(10),'并发验证未及时释放最终授权锁'
        return output
    monkeypatch.setattr(mqtt_observations,'guard',gated)
    def pause():
        paused_started.set()
        return client.post('/api/instances/'+destinations[0][0]['instance_id']+'/pause',
            json={'version':destinations[0][1]['version']})
    with ThreadPoolExecutor(max_workers=2) as pool:
        working=pool.submit(process_once,extract_id,deliver=False)
        assert ready.wait(10)
        pausing=pool.submit(pause);assert paused_started.wait(2)
        blocked=False
        for _ in range(80):
            with Session(engine()) as db:
                blockers=db.execute(text('SELECT pg_blocking_pids(pid) FROM pg_stat_activity WHERE datname=current_database()')).scalars().all()
            if any(pids[0] in item for item in blockers):blocked=True;break
            time.sleep(.02)
        try:
            assert blocked,'实例暂停必须真实阻塞于最终授权事务，而非在提交前撤销'
            assert not pausing.done()
        finally:release_guard.set()
        assert working.result(10)
        response=pausing.result(10);assert response.status_code==200,response.text
    with Session(engine()) as db:
        row=db.get(Observation,identity);assert row.status=='processed',row.data
        assert checked_report(db,fixture[3],row)['attributes']
        with pytest.raises(HTTPException):original(db,fixture[3],row,execution=True)
    later,_=save(fixture,record='after-instance-pause')
    observations=process_all(later.id)
    assert len(observations)==1  # the other active business continues independently


@pytest.mark.parametrize('same_request',[False,True])
def test_concurrent_confirmations_have_one_new_revision_and_truthful_cas_recovery(client,prepared_mqtt,same_request):
    route,_=create(client,prepared_mqtt)
    body={'expected_version':1,'topic':route['topic'],'entity_id':route['entity_id'],
        'extractor_id':route['extractor_id'],'field_links':route['field_links'],'include_existing':False,
        'request_id':str(uuid4())}
    from generic_agents.mqtt_binding_routes import update_route
    def submit(request):
        with Session(engine()) as db:
            try:
                result=update_route(db,prepared_mqtt[3],route['id'],request)
                result_version=result.version;db.commit();return 200,result_version
            except HTTPException as error:
                db.rollback();return error.status_code,error.detail
    second=body if same_request else {**body,'request_id':str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        one=pool.submit(submit,body);two=pool.submit(submit,second)
        results=[one.result(10),two.result(10)]
    assert sorted(result[0] for result in results)==([200,200] if same_request else [200,409])
    if not same_request:
        assert next(result[1] for result in results if result[0]==409)=='关联版本已更新，本次请求未提交。请对照当前版本重新确认。'
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(MqttBindingRevision))==2
        assert db.get(MqttBindingRoute,route['id']).version==2


def test_configuration_scope_is_frozen_and_independent_of_reception_or_object_updates(client,prepared_mqtt):
    route,_=create(client,prepared_mqtt)
    from generic_agents.mqtt_binding_routes import association_snapshots
    with Session(engine()) as db:before=association_snapshots(db,prepared_mqtt[3],route['gateway_id'])
    receipt,_=save(prepared_mqtt);process_all(receipt.id)
    with Session(engine()) as db:
        from generic_agents.models import Entity
        obj=db.get(Entity,route['entity_id']);obj.data={**obj.data,'name':'正常业务资料更新'};db.commit()
        after=association_snapshots(db,prepared_mqtt[3],route['gateway_id'])
    assert before==after
    assert before[0]['processing_scope']['include_existing'] is False
    assert all(key not in json.dumps(before) for key in ['username_ref','password_ref','entity_label','received_count'])
    paused=client.post('/api/mqtt/routes/'+route['id']+'/status',json={
        'expected_version':1,'request_id':str(uuid4()),'status':'paused'})
    assert paused.status_code==200
    with Session(engine()) as db:changed=association_snapshots(db,prepared_mqtt[3],route['gateway_id'])
    assert changed!=before and changed[0]['processing_scope']['status']=='paused'


def test_status_cannot_silently_accept_existing_permission_and_request_uuid_cannot_be_reused_for_new_route(client,prepared_mqtt):
    route,_=create(client,prepared_mqtt)
    response=client.post('/api/mqtt/routes/'+route['id']+'/status',json={
        'expected_version':1,'request_id':str(uuid4()),'status':'active','include_existing':True})
    assert response.status_code==422 and '保存新关联版本' in response.json()['detail']
    changed,request=update(client,route);assert changed.status_code==200
    body={key:request[key] for key in ('topic','entity_id','extractor_id','field_links','request_id','include_existing')}
    body['expected_version']=prepared_mqtt[0]['version']
    response=client.post('/api/mqtt/gateways/'+route['gateway_id']+'/routes',json=body)
    assert response.status_code==409
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(MqttBindingRoute))==1
