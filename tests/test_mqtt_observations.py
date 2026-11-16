import copy
import json
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from fastapi import HTTPException
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import User, MqttReceipt, MqttGateway, MqttBinding, Observation, Job, Event, Memory, Task, CloudCall
from generic_agents.security import Principal
from generic_agents.mqtt_inbox import capture
from generic_agents.mqtt_observations import guard, reconcile
from generic_agents.worker import process_once, deliver_events
from generic_agents.observation_integrity import checked_report
from generic_agents.perception import object_state
from tests.test_vertical_slice import manifest, publish, entity


@pytest.fixture
def prepared_mqtt(client, account, monkeypatch):
    monkeypatch.setattr(settings(), 'mqtt_allowed_brokers', 'mqtt://127.0.0.1:1883')
    package = publish(client, manifest()); obj = entity(client, package)
    rule = publish(client, manifest('tool', name='mqtt_numeric_rule', allowed_agent_roles=['perception'],
        execution={'adapter': 'builtin', 'handler': 'numeric_rules'}, data_policy={'cloud_allowed': False},
        specification={'rules': [{'id': 'hot', 'field': 'temperature', 'operator': 'gt', 'threshold': 38}]}))
    configuration = {'broker': 'mqtt://127.0.0.1:1883', 'topics': {'sensor/one': 'device-one'},
        'mapping': {'t': {'attribute': 'temperature', 'unit': 'C'}}, 'fields': {}, 'username_ref': '', 'password_ref': ''}
    response = client.post('/api/mqtt/gateways', json={'name': '传感接入', 'configuration': configuration})
    assert response.status_code == 201, response.text
    gateway = response.json()
    gateway = client.post('/api/mqtt/gateways/' + gateway['id'] + '/status',
        json={'expected_version': gateway['version'], 'status': 'active'}).json()
    with Session(engine()) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'mqtt-pipeline-test')
    return gateway, obj, rule, p


def bind(client, fixture):
    gateway, obj, rule, p = fixture
    response = client.post('/api/mqtt/gateways/' + gateway['id'] + '/bindings', json={
        'expected_version': gateway['version'], 'topic': 'sensor/one', 'entity_id': obj['id'], 'extractor_id': rule['id']})
    assert response.status_code == 201, response.text
    return response.json()


def save(fixture, record='r1', value=39.5, capture_time=None, **changes):
    gateway, obj, rule, p = fixture
    content = {'record_id': record, 'device_id': 'device-one',
        'observed_at': capture_time or (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(),
        'values': {'t': value}, 'units': {'t': 'C'}}
    content.update(changes); raw = json.dumps(content, ensure_ascii=False).encode()
    with Session(engine(), expire_on_commit=False) as db:
        row = capture(db, p, gateway['id'], gateway['version'], 'sensor/one', raw, 1)
        db.commit()
        return row, raw


def process_receipt(receipt):
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.kind == 'submit_mqtt_receipt', Job.data['receipt_id'].as_string() == receipt.id))
        assert job, '绑定来源应形成持久处理任务'
        submit_id = job.id
    assert process_once(submit_id, deliver=False)
    with Session(engine()) as db:
        submit = db.get(Job, submit_id)
        assert submit.status == 'succeeded', submit.data
        row = db.get(MqttReceipt, receipt.id)
        identity = row.data['observation_id']
        job = db.scalar(select(Job).where(Job.kind == 'process_mqtt_observation',
            Job.data['observation_id'].as_string() == identity))
        assert job
        extract_id = job.id
    assert process_once(extract_id, deliver=False)
    return identity


def test_original_packet_to_common_state_anomaly_and_daily_is_durable(client, prepared_mqtt):
    binding = bind(client, prepared_mqtt)
    receipt, raw = save(prepared_mqtt)
    identity = process_receipt(receipt)
    p = prepared_mqtt[3]
    with Session(engine()) as db:
        row = db.get(Observation, identity)
        assert row.status == 'processed', row.data
        assert row.data['extracted']['attributes'] == {'temperature': 39.5}
        assert row.data['extracted']['anomalies'][0]['rule_id'] == 'hot'
        report = checked_report(db, p, row)
        assert not report['source_time_verified'] and not report['physical_state_verified']
        state = object_state(db, p, row.entity_id, ['temperature'], 3600)
        assert state['fields']['temperature']['value'] == 39.5
        events = db.scalars(select(Event).where(Event.data['observation_id'].as_string() == identity)).all()
        assert {event.kind for event in events} >= {'observation.data', 'perception.anomaly'}
        assert all(event.status == 'received' for event in events)
        task = db.scalar(select(Task).where(Task.data['trigger'].as_string() == 'perception.anomaly'))
        assert task and task.data['evidence']['observation_id'] == identity
        summaries = db.scalars(select(Memory).where(Memory.data['schema'].as_string() == 'mqtt-inbox-daily-v2')
            .order_by(Memory.created_at)).all()
        assert summaries[-1].data['counts']['processing_succeeded']==1
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
    records = client.get('/api/mqtt/gateways/' + binding['gateway_id'] + '/receipts').json()
    assert records[0]['observation_id'] == identity and records[0]['accepted_as_observation']
    assert client.get('/api/mqtt/receipts/' + receipt.id + '/original').content == raw
    # Explicit repeated submission returns the original record and no duplicate job/task.
    response = client.post('/api/mqtt/receipts/' + receipt.id + '/submit', json={'expected_version': receipt.version})
    assert response.status_code == 200 and response.json()['id'] == identity
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Observation)) == 1
        assert db.scalar(select(func.count()).select_from(Task)) == 1


def test_first_capture_then_binding_queues_original_without_requiring_a_new_packet(client, prepared_mqtt):
    receipt, raw = save(prepared_mqtt)
    with Session(engine()) as db: assert not db.scalars(select(Job).where(Job.kind == 'submit_mqtt_receipt')).all()
    bind(client, prepared_mqtt)
    identity = process_receipt(receipt)
    assert client.get('/api/mqtt/receipts/' + receipt.id + '/original').content == raw
    with Session(engine()) as db: assert db.get(Observation, identity).status == 'processed'


def test_public_observation_endpoint_cannot_forge_mqtt_attributes(client, prepared_mqtt):
    binding = bind(client, prepared_mqtt); receipt, _ = save(prepared_mqtt)
    with Session(engine()) as db:
        decoded = db.get(MqttReceipt, receipt.id).data['decoded']
    body = {'source_id': binding['source_id'], 'source_record_id': receipt.source_record_id,
        'entity_id': binding['entity_id'], 'observed_at': decoded['observed_at'], 'business_day': decoded['business_day'],
        'attributes': {'temperature': 1}, 'evidence_ids': [receipt.evidence_id],
        'extractor_id': binding['extractor_id'], 'startup_mode': 'automatic'}
    response = client.post('/api/observations', json=body)
    assert response.status_code == 409, response.text
    with Session(engine()) as db: assert not db.scalars(select(Observation)).all()


@pytest.mark.parametrize('changes', [{'observed_at': None}, {'units': {'t': 'F'}}, {'device_id': 'other'}])
def test_uncertain_raw_packet_never_queues_extraction(client, prepared_mqtt, changes):
    bind(client, prepared_mqtt); receipt, _ = save(prepared_mqtt, **changes)
    assert receipt.status == 'quarantined'
    with Session(engine()) as db:
        assert not db.scalars(select(Job).where(Job.kind == 'submit_mqtt_receipt')).all()
    assert client.post('/api/mqtt/receipts/' + receipt.id + '/submit', json={'expected_version': receipt.version}).status_code == 409


def test_processed_record_content_conflict_invalidates_state_keeps_original_output(client, prepared_mqtt):
    bind(client, prepared_mqtt)
    clock = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    receipt, raw = save(prepared_mqtt, capture_time=clock)
    identity = process_receipt(receipt)
    changed, _ = save(prepared_mqtt, capture_time=clock, value=40)
    assert changed.status == 'quarantined'
    deliver_events(workspace_id=prepared_mqtt[3].workspace_id)
    with Session(engine()) as db:
        row = db.get(Observation, identity)
        assert row.status == 'failed' and row.data['extracted']['attributes'] == {'temperature': 39.5}
        state = object_state(db, prepared_mqtt[3], row.entity_id, ['temperature'], 3600)
        assert 'temperature' not in state['fields']
        summaries = db.scalars(select(Memory).where(Memory.data['schema'].as_string() == 'mqtt-inbox-daily-v2')
            .order_by(Memory.created_at)).all()
        assert summaries[-1].data['counts']['processing_blocked']==1
        assert db.scalar(select(func.count()).select_from(Observation)) == 1
    assert client.get('/api/mqtt/receipts/' + receipt.id + '/original').content == raw


@pytest.mark.parametrize('target', ['attributes', 'binding', 'original'])
def test_tampered_proof_cannot_be_reused_as_state(client, prepared_mqtt, target):
    bind(client, prepared_mqtt); receipt, _ = save(prepared_mqtt)
    identity = process_receipt(receipt)
    with Session(engine()) as db:
        row = db.get(Observation, identity)
        if target == 'attributes': row.data = {**row.data, 'attributes': {'temperature': 1}}
        elif target == 'original':
            value = copy.deepcopy(row.data); value['original_request']['attributes']['temperature'] = 1
            row.data = value
        else:
            binding = db.get(MqttBinding, row.data['mqtt_binding_id'])
            binding.data = {**binding.data, 'extractor_hash': 'changed'}
        db.commit()
    with Session(engine()) as db:
        with pytest.raises((HTTPException, ValueError)): checked_report(db, prepared_mqtt[3], db.get(Observation, identity))


def test_pause_stops_new_processing_but_preserves_completed_history(client, prepared_mqtt):
    binding = bind(client, prepared_mqtt); receipt, raw = save(prepared_mqtt)
    identity = process_receipt(receipt)
    new, _ = save(prepared_mqtt, record='r2', value=32)
    gateway = prepared_mqtt[0]
    response = client.post('/api/mqtt/gateways/' + gateway['id'] + '/status',
        json={'expected_version': gateway['version'], 'status': 'paused'})
    assert response.status_code == 200
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.kind == 'submit_mqtt_receipt', Job.data['receipt_id'].as_string() == new.id))
        job_id = job.id
    assert process_once(job_id, deliver=False)
    with Session(engine()) as db:
        assert db.get(Job, job_id).status == 'failed'
        assert checked_report(db, prepared_mqtt[3], db.get(Observation, identity))['attributes'] == {'temperature': 39.5}
        assert db.scalar(select(func.count()).select_from(Observation)) == 1
    assert client.get('/api/mqtt/receipts/' + receipt.id + '/original').content == raw
    active=client.post('/api/mqtt/gateways/'+gateway['id']+'/status',json={
        'expected_version':response.json()['version'],'status':'active'})
    assert active.status_code==200
    recovered=client.post('/api/mqtt/receipts/'+new.id+'/submit',json={'expected_version':new.version})
    assert recovered.status_code==200,recovered.text
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Observation))==2
        assert db.get(Job,job_id).status=='failed'  # Keep the original failed attempt.


def test_managed_business_receives_signal_and_background_state_from_sensor(client, account, monkeypatch):
    from tests import test_instance_workspace as workspace
    from generic_agents.models import PerceptionSignal, ObjectState
    definitions=copy.deepcopy(workspace.OBJECTS)
    definitions[0]['fields'].append({'label':'温度','kind':'数值','required':False})
    monkeypatch.setattr(workspace,'OBJECTS',definitions)
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','mqtt://127.0.0.1:1883')
    release,_,_=workspace.prepared(client);workspace.enable(client,release);obj=workspace.object_row(client,release)
    key=next(field['key'] for field in release['snapshot']['object_types'][0]['fields'] if field['label']=='温度')
    rule=publish(client,manifest('tool',name='managed_mqtt_rule',allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},data_policy={'cloud_allowed':False},
        specification={'rules':[{'id':'temperature-high','field':key,'operator':'gt','threshold':38}]}))
    configuration={'broker':'mqtt://127.0.0.1:1883','topics':{'sensor/one':'device-one'},
        'mapping':{'t':{'attribute':key,'unit':'C'}},'fields':{},'username_ref':'','password_ref':''}
    gateway=client.post('/api/mqtt/gateways',json={'name':'业务传感器','configuration':configuration}).json()
    gateway=client.post('/api/mqtt/gateways/'+gateway['id']+'/status',json={'expected_version':gateway['version'],'status':'active'}).json()
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','managed-test')
    fixture=(gateway,obj,rule,p);bind(client,fixture);receipt,_=save(fixture)
    identity=process_receipt(receipt)
    with Session(engine()) as db:
        signal=db.scalar(select(PerceptionSignal).where(PerceptionSignal.entity_id==obj['id']))
        assert signal and signal.status=='unassigned' and signal.data['basis']['observation_id']==identity
        assert db.scalar(select(func.count()).select_from(Task))==0  # Receive in the existing business inbox.
        refresh=db.scalar(select(Job).where(Job.kind=='refresh_object_state').order_by(Job.created_at.desc()))
        assert refresh;refresh_id=refresh.id
    assert process_once(refresh_id,deliver=False)
    with Session(engine()) as db:
        state=db.scalar(select(ObjectState).where(ObjectState.entity_id==obj['id']))
        assert state.data['fields'][key]['value']==39.5
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
    response=client.get('/api/workspace/instances/'+release['instance_id']+'/signals')
    assert response.status_code==200 and response.json()['records'][0]['id']==signal.id


def test_late_packets_increment_original_china_day_without_duplicate_summary(client,prepared_mqtt):
    from zoneinfo import ZoneInfo
    bind(client,prepared_mqtt)
    clock=(datetime.now(timezone.utc)-timedelta(days=2)).replace(hour=23,minute=30).isoformat()
    day=str(datetime.fromisoformat(clock).astimezone(ZoneInfo('Asia/Shanghai')).date())
    first,_=save(prepared_mqtt,record='late-1',value=10,capture_time=clock)
    one=process_receipt(first)
    later=(datetime.fromisoformat(clock)+timedelta(seconds=30)).isoformat()
    second,_=save(prepared_mqtt,record='late-2',value=11,capture_time=later)
    two=process_receipt(second)
    p=prepared_mqtt[3]
    with Session(engine()) as db:
        source_id=db.get(Observation,one).source_id
        reconcile(db,p,source_id,day);reconcile(db,p,source_id,day);db.commit()
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.daily',event_schema='mqtt-inbox-daily-v2',business_day=day)
    with Session(engine()) as db:
        summaries=db.scalars(select(Memory).where(Memory.data['schema'].as_string()=='mqtt-inbox-daily-v2')
            .order_by(Memory.created_at)).all()
        assert len(summaries)>=2
        assert all(item.data['business_day']==day for item in summaries)
        assert summaries[-1].data['counts']['processing_succeeded']==2
        state=object_state(db,p,prepared_mqtt[1]['id'],['temperature'],3600)
        assert state['status']=='insufficient' and state['fields']['temperature']['value']==11
        assert {'field':'temperature','reason':'stale'} in state['gaps']


def test_binding_cannot_switch_object_or_use_a_rule_for_another_field(client,prepared_mqtt):
    binding=bind(client,prepared_mqtt)
    gateway,obj,rule,p=prepared_mqtt
    with Session(engine()) as db:
        from generic_agents.models import Entity
        package_id=db.get(Entity,obj['id']).schema_capability_id
    another=entity(client,{'id':package_id})
    response=client.post('/api/mqtt/gateways/'+gateway['id']+'/bindings',json={
        'expected_version':gateway['version'],'topic':'sensor/one','entity_id':another['id'],'extractor_id':rule['id']})
    assert response.status_code==409
    bad=publish(client,manifest('tool',name='wrong_field_rule',allowed_agent_roles=['perception'],
        execution={'adapter':'builtin','handler':'numeric_rules'},data_policy={'cloud_allowed':False},
        specification={'rules':[{'id':'not-mapped','field':'other','operator':'gt','threshold':1}]}))
    response=client.post('/api/mqtt/gateways/'+gateway['id']+'/bindings',json={
        'expected_version':gateway['version'],'topic':'sensor/one','entity_id':obj['id'],'extractor_id':bad['id']})
    assert response.status_code==422
    assert client.get('/api/mqtt/gateways/'+gateway['id']+'/bindings').json()==[binding]


def test_registered_mqtt_source_cannot_widen_permissions(client,prepared_mqtt):
    binding=bind(client,prepared_mqtt)
    with Session(engine()) as db:
        from generic_agents.models import Capability
        source=db.get(Capability,binding['source_id']);original=copy.deepcopy(source.data)
        assert original['execution']['adapter']=='mqtt_source'
    for index,update in enumerate(({'data_policy':{'cloud_allowed':True}},
        {'execution':{'adapter':'mqtt_source','binding_id':binding['id'],'publish_commands':True}})):
        manifest_value={**original,'name':'widened_mqtt_'+str(index),**update}
        created=client.post('/api/capabilities',json=manifest_value)
        assert created.status_code==201
        row=created.json()
        response=client.post('/api/capabilities/'+row['id']+'/validate',json={
            'expected_version':row['version'],'reason':'测试拒绝扩大权限'})
        assert response.status_code==422
