import copy
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.models import Event,Memory,Observation,Task,CloudCall,Evidence,MqttReceipt,Job
from generic_agents.mqtt_data import deliver_pending,summary_report
from generic_agents.mqtt_observations import schedule_pending
from generic_agents.worker import deliver_events
from tests.test_mqtt_inbox import gateway,save,packet
from tests.test_mqtt_observations import prepared_mqtt,bind


@pytest.mark.parametrize('changes,undated', [({},False),({'observed_at':None},True),
    ({'units':{'t':'F'}},False),({'device_id':'unknown'},False)])
def test_unbound_uncertain_originals_reach_data_without_fabricated_object_or_clock(gateway,client,changes,undated):
    receipt,raw=save(gateway,packet(**changes)),packet(**changes)
    deliver_pending();deliver_pending()
    with Session(engine()) as db:
        data=db.scalar(select(Memory).where(Memory.data['type'].as_string()=='sensor_original'))
        assert data and data.data['receipt_id']==receipt.id and data.data['input_sha256']==receipt.input_sha256
        assert data.data['capture_time_unknown']==undated and not data.data['physical_state_verified']
        summary=db.scalar(select(Memory).where(Memory.data['schema'].as_string()=='mqtt-inbox-daily-v2'))
        assert summary.data['counts']['raw_total']==1 and summary.data['counts']['data_received']==1
        assert 'unique_original_ids' not in summary.data
        if undated:
            assert summary.data['business_day'] is None
            assert summary.data['date_basis']=='capture_time_unknown_no_day_assigned'
        else:assert summary.data['business_day']=='2026-10-11'
        assert db.scalar(select(func.count()).select_from(Observation))==0
        assert db.scalar(select(func.count()).select_from(Task))==0
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
    view=client.get('/api/mqtt/gateways/'+gateway[1]+'/receipts').json()[0]
    assert view['data_received'] and not view['accepted_as_observation']
    assert client.get('/api/mqtt/receipts/'+receipt.id+'/original').content==raw


def test_exact_redelivery_does_not_duplicate_data_receipt_or_daily_summary(gateway):
    receipt=save(gateway,packet());deliver_pending()
    assert save(gateway,packet(),duplicate=True).id==receipt.id
    deliver_pending()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.data['type'].as_string()=='sensor_original'))==1
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.data['schema'].as_string()=='mqtt-inbox-daily-v2'))==1


def test_unknown_clock_invalid_utf8_and_empty_payload_keep_originals_in_undated_ledger(gateway):
    receipts=[save(gateway,raw) for raw in (b'\xff',b'',packet(observed_at='wrong'))]
    deliver_pending()
    with Session(engine()) as db:
        summaries=db.scalars(select(Memory).where(Memory.data['schema'].as_string()=='mqtt-inbox-daily-v2')).all()
        assert len(summaries)==1 and summaries[0].data['counts']['raw_total']==len(receipts)
        assert summaries[0].data['business_day'] is None
        assert summaries[0].data['counts']['raw_needs_review']==len(receipts)


@pytest.mark.parametrize('target',['event','blob','parsed_time'])
def test_corrupt_original_or_delivery_proof_is_blocked_not_reported_received(gateway,client,target):
    receipt=save(gateway,packet())
    with Session(engine()) as db:
        if target=='blob':
            ev=db.get(Evidence,receipt.evidence_id);(settings().storage_dir/ev.storage_key).write_bytes(b'broken')
        elif target=='event':
            event=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet'))
            value=copy.deepcopy(event.data);value['report']['input_sha256']='bad';event.data=value
        else:
            row=db.get(MqttReceipt,receipt.id);value=copy.deepcopy(row.data)
            value['decoded']['business_day']='2026-10-12';row.data=value
        db.commit()
    deliver_pending()
    with Session(engine()) as db:
        event=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet'))
        assert event.status=='blocked_delivery' and event.data['delivery_error']
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.data['type'].as_string()=='sensor_original'))==0
    view=client.get('/api/mqtt/gateways/'+gateway[1]+'/receipts').json()[0]
    assert not view['data_received'] and view['data_delivery']['status']=='blocked_delivery'


def test_data_pending_dispatch_does_not_deliver_unrelated_legacy_events(gateway):
    from generic_agents.services import enqueue_event
    save(gateway,packet())
    with Session(engine()) as db:
        unrelated=enqueue_event(db,gateway[0],'perception.daily','legacy-daily',{'source_id':'other','business_day':'2026-10-10'})
        identity=unrelated.id;db.commit()
    deliver_pending()
    with Session(engine()) as db:assert db.get(Event,identity).status=='pending_delivery'


def test_over_two_hundred_unbound_inputs_continue_in_new_scheduler_pass(client,prepared_mqtt):
    from generic_agents.mqtt_inbox import capture
    gateway,obj,rule,p=prepared_mqtt
    with Session(engine()) as db:
        for index in range(203):
            raw=packet(record_id='backlog-'+str(index),device_id='device-one')
            capture(db,p,gateway['id'],gateway['version'],'sensor/one',raw,1)
        db.commit()
    bind(client,prepared_mqtt)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='submit_mqtt_receipt'))==200
    schedule_pending();schedule_pending()
    with Session(engine()) as db:
        jobs=db.scalars(select(Job).where(Job.kind=='submit_mqtt_receipt')).all()
        assert len(jobs)==203 and len({job.data['receipt_id'] for job in jobs})==203
        assert db.scalar(select(func.count()).select_from(Observation))==0
    collected=[];cursor=''
    while True:
        response=client.get('/api/mqtt/gateways/'+gateway['id']+'/receipts',params={'limit':100,**({'after':cursor} if cursor else {})})
        assert response.status_code==200 and response.headers['x-total-count']=='203'
        collected.extend(row['id'] for row in response.json())
        cursor=response.headers['x-next-cursor']
        if not cursor:break
    assert len(collected)==203 and len(set(collected))==203


def test_new_late_original_creates_original_day_revision_instead_of_reception_day(gateway):
    first=save(gateway,packet(record_id='late-1'));deliver_pending()
    second=save(gateway,packet(record_id='late-2'));deliver_pending()
    with Session(engine()) as db:
        summaries=db.scalars(select(Memory).where(Memory.data['schema'].as_string()=='mqtt-inbox-daily-v2').order_by(Memory.created_at)).all()
        assert len(summaries)==2 and all(s.data['business_day']=='2026-10-11' for s in summaries)
        assert summaries[-1].data['counts']['raw_total']==2


def test_daily_summary_cannot_substitute_another_original(gateway):
    receipt=save(gateway,packet())
    deliver_events(event_kind='perception.sensor_packet')
    from generic_agents.mqtt_daily import publish_pending
    publish_pending()
    with Session(engine()) as db:
        event=db.scalar(select(Event).where(Event.kind=='perception.daily'))
        value=copy.deepcopy(event.data);value['counts']['raw_total']=10;event.data=value;db.commit()
    deliver_events(event_kind='perception.daily',event_schema='mqtt-inbox-daily-v2')
    with Session(engine()) as db:
        event=db.scalar(select(Event).where(Event.kind=='perception.daily'))
        assert event.status=='blocked_delivery'
        assert not db.scalars(select(Memory).where(Memory.data['schema'].as_string()=='mqtt-inbox-daily-v2')).all()


def test_one_damaged_old_original_does_not_block_next_valid_original(gateway):
    old=save(gateway,packet(record_id='old'))
    with Session(engine()) as db:
        original=db.get(Evidence,old.evidence_id);(settings().storage_dir/original.storage_key).write_bytes(b'broken')
    new=save(gateway,packet(record_id='new'));deliver_pending()
    with Session(engine()) as db:
        memories=db.scalars(select(Memory).where(Memory.data['type'].as_string()=='sensor_original')).all()
        assert len(memories)==1 and memories[0].data['receipt_id']==new.id
        summary=db.scalar(select(Memory).where(Memory.data['schema'].as_string()=='mqtt-inbox-daily-v2'))
        assert summary.data['counts']['data_blocked']==1 and summary.data['counts']['raw_total']==2


def test_restore_original_then_explicit_retry_keeps_one_data_receipt(gateway,client):
    raw=packet();receipt=save(gateway,raw)
    with Session(engine()) as db:
        original=db.get(Evidence,receipt.evidence_id);path=settings().storage_dir/original.storage_key
        path.write_bytes(b'broken')
    deliver_pending()
    url='/api/mqtt/receipts/'+receipt.id+'/retry-data';body={'expected_version':receipt.version}
    assert client.post(url,json=body).status_code==409
    path.write_bytes(raw)
    response=client.post(url,json=body)
    assert response.status_code==200 and response.json()['status']=='pending_delivery'
    deliver_pending()
    assert client.post(url,json=body).json()['status']=='received'
    deliver_pending()
    with Session(engine()) as db:
        data=db.scalars(select(Memory).where(Memory.data['type'].as_string()=='sensor_original')).all()
        assert len(data)==1 and data[0].data['receipt_id']==receipt.id
        event=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet'))
        assert event.attempts==2 and event.data['previous_delivery_error']
