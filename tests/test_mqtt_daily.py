"""Real originals, fixed snapshots and durable read-index recovery in disposable PG."""
from copy import deepcopy
from uuid import uuid4
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func,event,delete
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.models import (MqttDailyLedger,MqttDailyEntry,MqttDailyChange,MqttDailyCheckpoint,
    MqttReceipt,MqttReceiptDelivery,MqttBindingRevision,Memory,Event,Job,Evidence,Membership)
from generic_agents.mqtt_daily import summary,records,index_delivery,schedule_once
from generic_agents.mqtt_data import deliver_pending,summary_report
from generic_agents.mqtt_inbox import capture
from generic_agents.worker import process_once,deliver_events
from generic_agents.services import enqueue_event
from generic_agents.contracts import digest
from tests.test_mqtt_inbox import gateway,save,packet
from tests.test_mqtt_observations import prepared_mqtt,save as bound_save
from tests.test_mqtt_binding_routes import create,process_all,managed_pair


def current(gateway):
    with Session(engine()) as db:
        row=db.scalar(select(MqttDailyLedger).where(MqttDailyLedger.gateway_id==gateway[1],MqttDailyLedger.day_key=='2026-10-11'))
        return summary(db,gateway[0],row.id)


def queued_index():
    with Session(engine()) as db:
        return db.scalar(select(Job.id).where(Job.kind=='index_mqtt_daily',Job.status=='queued').order_by(Job.created_at))


def drain_index():
    for _ in range(30):
        identity=queued_index()
        if not identity:return
        assert process_once(identity,deliver=False)
    raise AssertionError('durable daily indexing did not finish')


def test_1005_actual_capture_full_pages_fixed_revision_late_and_constant_capture_work(gateway,client):
    sql_counts=[]
    def count(conn,cursor,statement,parameters,context,executemany):sql_counts.append(statement)
    event.listen(engine(),'before_cursor_execute',count)
    try:
        with Session(engine()) as db:
            for index in range(1005):
                before=len(sql_counts)
                capture(db,gateway[0],gateway[1],gateway[2],'sensors/one',packet(record_id='actual-'+str(index)),1)
                if index in (10,1004):
                    if index==10:early=len(sql_counts)-before
                    else:late=len(sql_counts)-before
            db.commit()
    finally:event.remove(engine(),'before_cursor_execute',count)
    assert late<=early+2
    frozen=current(gateway);assert frozen['counts']['raw_total']==1005
    assert frozen['counts']['data_pending']==1005 and frozen['cost']['status']=='unknown'
    url='/api/mqtt/daily/'+frozen['id']+'/records'
    first=client.get(url,params={'revision':frozen['revision'],'limit':100}).json()
    assert first['total']==1005 and len(first['records'])==100
    save(gateway,packet(record_id='arrived-later'))
    after=first['next_cursor'];collected=[row['receipt_id'] for row in first['records']]
    while after:
        response=client.get(url,params={'revision':frozen['revision'],'limit':100,'after':after})
        assert response.status_code==200,response.text
        page=response.json();assert page['total']==1005
        collected.extend(row['receipt_id'] for row in page['records']);after=page['next_cursor']
    assert len(collected)==len(set(collected))==1005
    latest=current(gateway);assert latest['counts']['raw_total']==1006
    assert client.get(url,params={'revision':latest['revision'],'after':first['next_cursor']}).status_code==409
    assert save(gateway,packet(record_id='actual-0'),duplicate=True).id==collected[0]
    assert current(gateway)['counts']['raw_total']==1006
    for _ in range(11):deliver_pending()
    final=current(gateway);assert final['counts']['data_received']==1006 and final['counts']['data_pending']==0
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(MqttReceipt))==1006
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.data['type'].as_string()=='sensor_original'))==1006
        events=db.scalars(select(Event).where(Event.kind=='perception.daily')).all()
        assert all('unique_original_ids' not in row.data and 'processed_ids' not in row.data for row in events)


def test_bad_unknown_and_scoped_original_use_frozen_revision(gateway,client):
    good=save(gateway,packet(record_id='good'));bad=save(gateway,b'\xff');unknown=save(gateway,packet(record_id='unknown',observed_at=None))
    deliver_pending()
    response=client.get('/api/mqtt/gateways/'+gateway[1]+'/days',params={'business_day':'unknown'})
    assert response.status_code==200
    value=response.json()['records'][0];assert value['business_day'] is None and value['counts']['raw_total']==2
    page=client.get('/api/mqtt/daily/'+value['id']+'/records',params={'revision':value['revision']}).json()
    assert {row['receipt_id'] for row in page['records']}=={bad.id,unknown.id}
    assert client.get('/api/mqtt/daily/'+value['id']+'/original/'+good.id,params={'revision':value['revision']}).status_code==404
    downloaded=client.get('/api/mqtt/daily/'+value['id']+'/original/'+bad.id,params={'revision':value['revision']})
    assert downloaded.content==b'\xff' and downloaded.headers['x-content-sha256']==bad.input_sha256
    assert client.get('/api/mqtt/gateways/'+gateway[1]+'/days',params={'business_day':'wrong'}).status_code==422


def test_read_committed_reloads_old_identity_map_and_concurrent_cross_day_capture(gateway):
    from generic_agents.mqtt_daily import days
    save(gateway,packet(record_id='seed'))
    with Session(engine(),expire_on_commit=False) as db:
        stale=db.scalar(select(MqttDailyLedger));old_revision=stale.revision
        save(gateway,packet(record_id='new-before-summary'))
        value=summary(db,gateway[0],stale.id)
        assert value['revision']>old_revision and value['counts']['raw_total']==2
    barrier=Barrier(2)
    def writer():
        barrier.wait()
        for index in range(24):
            save(gateway,packet(record_id='concurrent-'+str(index),
                observed_at='2026-10-10T23:00:00Z' if index%2 else '2026-10-09T23:00:00Z'))
    def reader():
        barrier.wait()
        for _ in range(24):
            with Session(engine()) as db:
                values=days(db,gateway[0],gateway_id=gateway[1])['records']
                for value in values:
                    page=records(db,gateway[0],value['id'],value['revision'])
                    assert page['total']==value['counts']['raw_total']
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks=[pool.submit(writer),pool.submit(reader)]
        for task in tasks:task.result(timeout=40)
    with Session(engine()) as db:
        assert sum(row['counts']['raw_total'] for row in days(db,gateway[0],gateway_id=gateway[1])['records'])==26


def test_delivery_indexed_before_observation_and_actual_success_updates_same_item(client,prepared_mqtt):
    create(client,prepared_mqtt)
    original,_=bound_save(prepared_mqtt)
    with Session(engine()) as db:
        ledger=db.scalar(select(MqttDailyLedger));value=summary(db,prepared_mqtt[3],ledger.id)
        assert value['counts']['processing_total']==1 and value['counts']['processing_pending']==1
        pending=records(db,prepared_mqtt[3],ledger.id,value['revision'],kind='processing')['records'][0]
        assert pending['delivery_id'] and pending['observation_id'] is None
    assert len(process_all(original.id))==1
    with Session(engine()) as db:
        final=summary(db,prepared_mqtt[3],ledger.id)
        assert final['counts']['raw_total']==1 and final['counts']['processing_succeeded']==1
        page=records(db,prepared_mqtt[3],ledger.id,final['revision'],kind='processing')
        assert page['records'][0]['item_id']==pending['item_id']
        assert page['records'][0]['invocation_id'] and page['records'][0]['deployment']=='local'
        old=records(db,prepared_mqtt[3],ledger.id,value['revision'],kind='processing')
        assert old['records'][0]['status']=='pending' and old['records'][0]['observation_id'] is None


def test_swapped_observation_cannot_be_indexed_as_another_target_success(client,prepared_mqtt):
    from tests.test_vertical_slice import entity
    create(client,prepared_mqtt)
    second=entity(client,{'id':prepared_mqtt[1]['schema_capability_id']});create(client,prepared_mqtt,obj=second)
    original,_=bound_save(prepared_mqtt);assert len(process_all(original.id))==2
    with Session(engine()) as db:
        deliveries=db.scalars(select(MqttReceiptDelivery).order_by(MqttReceiptDelivery.id)).all()
        one,two=deliveries;delivery_id=one.id;prior_observation=one.observation_id
        one.observation_id=two.observation_id;db.flush()
        value=index_delivery(db,prepared_mqtt[3],one);db.commit();ledger_id=value.id
    with Session(engine()) as db:
        latest=summary(db,prepared_mqtt[3],ledger_id)
        page=records(db,prepared_mqtt[3],ledger_id,latest['revision'],kind='processing')
        item=next(row for row in page['records'] if row['delivery_id']==delivery_id)
        assert item['status']=='blocked' and item['observation_id']==prior_observation


def test_two_instances_only_see_frozen_own_processing_and_shared_original(client,account,monkeypatch):
    fixture,destinations,routes=managed_pair(client,account,monkeypatch)
    original,_=bound_save(fixture)
    with Session(engine()) as db:
        ledger=db.scalar(select(MqttDailyLedger));value=summary(db,fixture[3],ledger.id)
    for destination in destinations:
        instance=destination[0]['instance_id'];params={'instance_id':instance,'revision':value['revision']}
        own=client.get('/api/mqtt/daily/'+ledger.id,params=params).json()
        assert own['counts']['raw_total']==1 and own['counts']['processing_total']==1
        page=client.get('/api/mqtt/daily/'+ledger.id+'/records',params={**params,'kind':'processing'}).json()
        assert len(page['records'])==1 and page['records'][0]['entity_id']==destination[2]['id']
        assert client.get('/api/mqtt/daily/'+ledger.id+'/original/'+original.id,params=params).status_code==200
        assert client.post('/api/mqtt/daily/'+ledger.id+'/inspection',params={'instance_id':instance},
            json={'request_id':str(uuid4()),'action':'start'}).status_code==422
    unrelated,_=bound_save(fixture,record='later-only')
    assert client.get('/api/mqtt/daily/'+ledger.id+'/original/'+unrelated.id,params=params).status_code==404


def test_first_corrupt_binding_cannot_create_cross_instance_membership(client,account,monkeypatch):
    import generic_agents.mqtt_daily as daily
    fixture,destinations,routes=managed_pair(client,account,monkeypatch)
    assert client.post('/api/mqtt/routes/'+routes[1]['id']+'/status',json={
        'expected_version':routes[1]['version'],'request_id':str(uuid4()),'status':'paused'}).status_code==200
    real_index=daily.index_delivery;monkeypatch.setattr(daily,'index_delivery',lambda *a,**k:None)
    original,_=bound_save(fixture);monkeypatch.setattr(daily,'index_delivery',real_index)
    with Session(engine()) as db:
        delivery=db.scalar(select(MqttReceiptDelivery));delivery_id=delivery.id;binding=db.get(MqttBindingRevision,delivery.binding_id)
        binding.data={**binding.data,'instance':{**binding.data['instance'],'id':destinations[1][0]['instance_id']}}
        db.commit()
    with Session(engine()) as db:
        real_index(db,fixture[3],db.get(MqttReceiptDelivery,delivery_id));db.commit()
    with Session(engine()) as db:
        ledger=db.scalar(select(MqttDailyLedger));value=summary(db,fixture[3],ledger.id)
        page=records(db,fixture[3],ledger.id,value['revision'],kind='processing')['records'][0]
        assert page['status']=='blocked' and page['instance_id'] is None
    for destination in destinations:
        instance=destination[0]['instance_id']
        assert client.get('/api/mqtt/instances/'+instance+'/days').json()['records']==[]
        assert client.get('/api/mqtt/daily/'+ledger.id+'/original/'+original.id,
            params={'revision':value['revision'],'instance_id':instance}).status_code==404


def test_inspection_damage_new_revision_preserves_old_candidate_and_resume_progress(gateway,client,monkeypatch):
    import generic_agents.mqtt_daily as daily
    errors=[];actual=daily.process_checkpoint
    def checked(*args,**kwargs):
        try:return actual(*args,**kwargs)
        except Exception as failure:errors.append(repr(failure));raise
    monkeypatch.setattr(daily,'process_checkpoint',checked)
    originals=[save(gateway,packet(record_id='inspect-'+str(index))) for index in range(3)]
    deliver_pending();before=current(gateway)
    with Session(engine()) as db:
        ev=db.get(Evidence,originals[1].evidence_id);path=settings().storage_dir/ev.storage_key;path.write_bytes(b'broken')
    url='/api/mqtt/daily/'+before['id']+'/inspection';body={'request_id':str(uuid4()),'action':'start'}
    response=client.post(url,json=body);assert response.status_code==200,response.text
    assert response.json()['revision']==before['revision']
    assert client.post(url,json=body).json()['id']==response.json()['id']
    drain_index();assert not errors,errors;after=current(gateway)
    assert after['counts']['data_received']==2 and after['counts']['data_blocked']==1
    with Session(engine()) as db:
        old=summary(db,gateway[0],before['id'],before['revision'])
        assert old['counts']['data_received']==3
    state=client.get(url).json();assert state['status']=='completed' and state['processed']==3
    assert state['revision']==before['revision'] and state['revision_hash']==before['revision_hash']
    path.write_bytes(packet(record_id='inspect-1'))
    assert client.post(url,json={'request_id':str(uuid4()),'action':'start'}).status_code==200
    drain_index();assert current(gateway)['counts']['data_received']==3


def test_backfill_failure_checkpoint_retry_uses_saved_watermark_and_position(gateway,client,monkeypatch):
    import generic_agents.mqtt_daily as daily
    errors=[];actual=daily.process_checkpoint
    def checked(*args,**kwargs):
        try:return actual(*args,**kwargs)
        except Exception as failure:errors.append(repr(failure));raise
    monkeypatch.setattr(daily,'process_checkpoint',checked)
    real=daily.index_original;monkeypatch.setattr(daily,'index_original',lambda *a,**k:None)
    for index in range(105):save(gateway,packet(record_id='legacy-backfill-'+str(index)))
    monkeypatch.setattr(daily,'index_original',real)
    schedule_once();assert process_once(queued_index(),deliver=False);assert not errors,errors
    first=client.get('/api/mqtt/gateways/'+gateway[1]+'/daily-index').json()
    assert first['processed']==100 and first['status']=='running'
    def interrupt(*args,**kwargs):raise RuntimeError('controlled checkpoint crash')
    monkeypatch.setattr(daily,'index_original',interrupt)
    assert process_once(queued_index(),deliver=False)
    failed=client.get('/api/mqtt/gateways/'+gateway[1]+'/daily-index').json()
    assert failed['status']=='failed' and failed['processed']==100
    monkeypatch.setattr(daily,'index_original',real)
    url='/api/mqtt/gateways/'+gateway[1]+'/daily-index'
    body={'request_id':str(uuid4()),'action':'retry','expected_version':failed['version']}
    restored=client.post(url,json=body);assert restored.status_code==200,restored.text
    assert client.post(url,json=body).json()['id']==restored.json()['id']
    assert restored.json()['processed']==100 and restored.json()['checked_through']==failed['checked_through']
    drain_index();assert current(gateway)['counts']['raw_total']==105
    final=client.get(url).json();assert final['status']=='completed' and final['processed']==105
    with Session(engine()) as db:
        assert db.get(MqttDailyCheckpoint,failed['id']).status=='failed'
        assert db.scalar(select(func.count()).select_from(MqttDailyEntry))==105


def test_committed_checkpoint_batch_replay_after_worker_death_is_safe_noop(gateway,monkeypatch):
    import generic_agents.mqtt_daily as daily
    from generic_agents.worker import claim
    from generic_agents.leases import attach
    from generic_agents.models import now
    from datetime import timedelta
    actual=daily.index_original;monkeypatch.setattr(daily,'index_original',lambda *a,**k:None)
    for index in range(103):save(gateway,packet(record_id='crash-window-'+str(index)))
    monkeypatch.setattr(daily,'index_original',actual);schedule_once()
    identity=queued_index();job_id,token=claim(identity)
    with Session(engine()) as db:
        attach(db,job_id,token)
        job=db.get(Job,job_id);daily.process_checkpoint(db,gateway[0],job);db.commit()
    # This is the real transaction window: index progress + successor persisted,
    # but original job still running because worker died before final Job status.
    with Session(engine()) as db:
        job=db.get(Job,job_id);assert job.status=='running' and job.data['checkpoint_committed']
        job.lease_until=now()-timedelta(seconds=1);db.commit()
    before=current(gateway);assert before['counts']['raw_total']==100
    assert process_once(job_id,deliver=False)
    assert current(gateway)['revision']==before['revision']
    with Session(engine()) as db:
        assert db.get(Job,job_id).status=='succeeded'
        checkpoint=db.scalar(select(MqttDailyCheckpoint));assert checkpoint.status=='running' and checkpoint.data['processed']==100
    drain_index();assert current(gateway)['counts']['raw_total']==103


@pytest.mark.parametrize('state,field',[('queued','cursor'),('queued','processed'),('queued','status'),
    ('completed','processed'),('completed','completed_at'),('completed','status')])
def test_checkpoint_get_cannot_display_forged_progress_or_completion(gateway,client,state,field):
    save(gateway,packet());value=current(gateway)
    url='/api/mqtt/daily/'+value['id']+'/inspection'
    started=client.post(url,json={'request_id':str(uuid4()),'action':'start'})
    assert started.status_code==200,started.text
    if state=='completed':drain_index()
    assert client.get(url).json()['status']==state
    with Session(engine()) as db:
        checkpoint=db.get(MqttDailyCheckpoint,started.json()['id'])
        if field=='status':checkpoint.status='completed' if state=='queued' else 'running'
        else:
            bad={'cursor':['2099-01-01T00:00:00+00:00','made-up'],'processed':999,
                 'completed_at':'2099-01-01T00:00:00+00:00'}[field]
            checkpoint.data={**checkpoint.data,field:bad}
        db.commit()
    assert client.get(url).status_code==409
    assert client.get('/api/mqtt/daily/'+value['id']).status_code==409


def test_checkpoint_get_remains_coherent_during_real_batch_commits(gateway,client,monkeypatch):
    import generic_agents.mqtt_daily as daily
    actual=daily.index_original;monkeypatch.setattr(daily,'index_original',lambda *a,**k:None)
    for index in range(103):save(gateway,packet(record_id='read-progress-'+str(index)))
    monkeypatch.setattr(daily,'index_original',actual);schedule_once()
    barrier=Barrier(2)
    def writer():barrier.wait();drain_index()
    def reader():
        barrier.wait();states=[]
        for _ in range(55):
            response=client.get('/api/mqtt/gateways/'+gateway[1]+'/daily-index')
            assert response.status_code==200,response.text
            states.append(response.json()['status'])
        return states
    with ThreadPoolExecutor(max_workers=2) as pool:
        write=pool.submit(writer);read=pool.submit(reader)
        write.result(timeout=30);assert set(read.result(timeout=30))<={'queued','running','completed'}
    final=client.get('/api/mqtt/gateways/'+gateway[1]+'/daily-index').json()
    assert final['status']=='completed' and final['processed']==103


def test_deleted_instance_member_cannot_make_partial_export_look_complete(client,account,monkeypatch):
    fixture,destinations,_=managed_pair(client,account,monkeypatch)
    bound_save(fixture,record='first-members');bound_save(fixture,record='second-members')
    with Session(engine()) as db:
        ledger=db.scalar(select(MqttDailyLedger));value=summary(db,fixture[3],ledger.id)
        member=db.scalar(select(MqttDailyChange).where(MqttDailyChange.kind=='processing',
            MqttDailyChange.instance_id==destinations[0][0]['instance_id']).order_by(MqttDailyChange.revision))
        db.delete(member);db.commit()
    params={'instance_id':destinations[0][0]['instance_id'],'revision':value['revision']}
    assert client.get('/api/mqtt/daily/'+value['id'],params=params).status_code==409
    assert client.get('/api/mqtt/daily/'+value['id']+'/records',params={**params,'kind':'processing'}).status_code==409


@pytest.mark.parametrize('field',['cursor','phase','processed','watermarks'])
def test_backfill_checkpoint_tamper_does_not_skip_history_or_claim_done(gateway,monkeypatch,field):
    import generic_agents.mqtt_daily as daily
    actual=daily.index_original;monkeypatch.setattr(daily,'index_original',lambda *a,**k:None)
    save(gateway,packet());monkeypatch.setattr(daily,'index_original',actual);schedule_once()
    identity=queued_index()
    with Session(engine()) as db:
        checkpoint=db.scalar(select(MqttDailyCheckpoint));state=deepcopy(checkpoint.data)
        state[field]={'cursor':['2099-01-01T00:00:00+00:00','zz'],'phase':'done','processed':99,'watermarks':{}}[field]
        checkpoint.data=state;db.commit()
    assert process_once(identity,deliver=False)
    with Session(engine()) as db:
        assert db.get(Job,identity).status=='failed'
        assert db.scalar(select(func.count()).select_from(MqttDailyEntry))==0
        assert db.scalar(select(MqttDailyCheckpoint)).status!='completed'


@pytest.mark.parametrize('target',['head','change','delete_earlier'])
def test_modified_current_or_missing_frozen_member_cannot_claim_full_count(gateway,client,target):
    save(gateway,packet(record_id='one'));save(gateway,packet(record_id='two'))
    value=current(gateway)
    with Session(engine()) as db:
        ledger=db.get(MqttDailyLedger,value['id'])
        if target=='head':ledger.data={**ledger.data,'counts':{**ledger.data['counts'],'raw_total':1}}
        else:
            change=db.scalar(select(MqttDailyChange).where(MqttDailyChange.ledger_id==ledger.id).order_by(MqttDailyChange.revision))
            if target=='change':change.data={**change.data,'item':{**change.data['item'],'status':'received'}}
            else:db.delete(change)
        db.commit()
    # Payload-only damage is found when its frozen item is read; deletion is found
    # by exact total/counters comparison, rather than a trusted mutable head.
    endpoint='/api/mqtt/daily/'+value['id']+('/records' if target=='change' else '')
    assert client.get(endpoint,params={'revision':value['revision']}).status_code==409


def test_real_v1_frozen_history_still_validates_and_remains_unchanged(gateway):
    save(gateway,packet(record_id='before-v1'));deliver_pending()
    with Session(engine()) as db:
        old=summary_report(db,gateway[0],gateway[1],'2026-10-11')
        event=enqueue_event(db,gateway[0],'perception.daily','actual-legacy-v1',old)
        event.data={**old,'summary_hash':digest(old)};identity=event.id;db.commit()
    save(gateway,packet(record_id='after-v1'))
    deliver_events(event_kind='perception.daily',event_schema='mqtt-inbox-daily-v1')
    with Session(engine()) as db:
        event=db.get(Event,identity);assert event.status=='received' and event.data['unique_original_ids']==old['unique_original_ids']
        memory=db.scalar(select(Memory).where(Memory.data['event_id'].as_string()==identity))
        assert memory and memory.data['schema']=='mqtt-inbox-daily-v1' and memory.data['truncated'] is False


def test_viewer_can_read_but_cannot_start_or_retry_durable_checks(gateway,client):
    save(gateway,packet());value=current(gateway)
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.user_id==gateway[0].user_id));member.role='viewer';db.commit()
    assert client.get('/api/mqtt/daily/'+value['id']).status_code==200
    for endpoint in ('/api/mqtt/daily/'+value['id']+'/inspection','/api/mqtt/gateways/'+gateway[1]+'/daily-index'):
        assert client.post(endpoint,json={'request_id':str(uuid4()),'action':'start'}).status_code==403
