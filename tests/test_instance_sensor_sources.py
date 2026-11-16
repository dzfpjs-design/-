"""Disposable PG: source inventory is frozen configuration, never live intake state."""
import copy
import json
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import MqttGateway,MqttReceipt,Workspace,User,InstanceRelease,Capability,Job,CloudCall
from generic_agents.security import Principal
from generic_agents.mqtt_inbox import capture
from tests.test_mqtt_api import configuration as sensor_configuration
from tests.test_instance_workspace import OBJECTS,configuration,save


@pytest.fixture(autouse=True)
def local_broker_permission(monkeypatch):
    # Permission to save a fixture; no broker, subscriber, real device or cloud call.
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','mqtt://127.0.0.1:1883,mqtt://localhost:1884')


def source(client,**changes):
    config=sensor_configuration();config.update(changes)
    response=client.post('/api/mqtt/gateways',json={'name':'温度测量来源','configuration':config})
    assert response.status_code==201,response.text
    return response.json()


def checked(client):
    revision=save(client,configuration(),review_automation=True)
    member=client.get('/api/instances/members').json()[0]
    body={'revision':revision,'objects':OBJECTS,'role_bindings':{'核验负责人':member['id']}}
    response=client.post('/api/instances/check',json=body)
    assert response.status_code==200,response.text
    return body,response.json()


def generate(client,body,preview,instance_id=None):
    return client.post('/api/instances',json={**body,'confirmed_configuration_hash':preview['configuration_hash'],
        'confirm_configuration':True,'instance_id':instance_id})


def test_workspace_inventory_and_generation_never_register_sensor_capability_or_start_intake(client,account):
    gateway=source(client,username_ref='GA_MQTT_TEST_USERNAME',password_ref='GA_MQTT_TEST_PASSWORD')
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]))
        foreign=Workspace(name='另一工作区');db.add(foreign);db.flush()
        other=MqttGateway(workspace_id=foreign.id,owner_id=user.id,name='不可泄漏的来源',
            data=sensor_configuration(),status='active');db.add(other);db.commit();foreign_id=other.id
    body,preview=checked(client)
    assert len(preview['sensor_sources'])==1
    summary=preview['sensor_sources'][0]
    assert summary['id']==gateway['id'] and summary['name']==gateway['name']
    assert summary['version']==1 and summary['status']=='paused'
    assert summary['configuration_hash']==digest(gateway['configuration'])
    assert summary['topics']=={'sensor/one':'one'} and summary['devices']==['one']
    assert summary['mapping']=={'t':{'attribute':'temperature','unit':'C'}}
    assert summary['fields']['time_key']=='observed_at' and summary['fields']['record_key']=='record_id'
    assert summary['binding_status']=='unbound' and summary['bound_topics']==[]
    assert summary['connection_status']=='unknown' and summary['access_mode']=='read_only'
    assert not summary['device_commands_supported'] and not preview['sensor_source_issues']
    assert not preview['general_issues'] and preview['businesses'][0]['available']
    serialized=json.dumps(preview,ensure_ascii=False)
    for private in ['username_ref','password_ref','GA_MQTT_TEST_USERNAME','GA_MQTT_TEST_PASSWORD',foreign_id,'不可泄漏的来源']:
        assert private not in serialized
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Capability))==0
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind!='login_limit'))==0
    result=generate(client,body,preview);assert result.status_code==201,result.text
    assert result.json()['status']=='prepared'
    assert client.get('/api/mqtt/gateways').json()[0]['status']=='paused'
    with Session(engine()) as db:
        caps=db.scalars(select(Capability)).all()
        packages=[cap for cap in caps if cap.kind=='domain_package']
        queries=[cap for cap in caps if cap.namespace=='platform_native_queries']
        assert len(caps)==4 and len(packages)==1 and len(queries)==3
        assert {cap.data['specification']['method'] for cap in queries}=={'keyword','aggregate','fulltext'}
        assert all(cap.kind=='tool' and cap.data['execution']['adapter']=='builtin'
            and cap.data['execution']['side_effect'] is False for cap in queries)
        assert set(packages[0].data['dependencies'])=={cap.id for cap in queries}
        assert set(packages[0].data['specification']['data_tool_ids'])=={cap.id for cap in queries}
        assert not any(cap.kind=='data_source' for cap in caps)
        assert db.scalar(select(func.count()).select_from(MqttReceipt))==0
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind!='login_limit'))==0


@pytest.mark.parametrize('change',['name','topic','device','unit','selector','broker','credential_reference','status'])
def test_saved_source_changes_invalidate_old_generation_confirmation(client,change):
    gateway=source(client);body,before=checked(client)
    if change=='status':
        response=client.post('/api/mqtt/gateways/'+gateway['id']+'/status',
            json={'expected_version':gateway['version'],'status':'active'})
    else:
        config=copy.deepcopy(gateway['configuration']);name=gateway['name']
        if change=='name':name='已改名的温度来源'
        elif change=='topic':config['topics']={'sensor/two':'one'}
        elif change=='device':config['topics']['sensor/one']='new-device'
        elif change=='unit':config['mapping']['t']['unit']='F'
        elif change=='selector':config['fields']={'time_key':'captured_at'}
        elif change=='broker':config['broker']='mqtt://localhost:1884'
        elif change=='credential_reference':config.update(username_ref='GA_MQTT_OTHER_USER',password_ref='GA_MQTT_OTHER_PASSWORD')
        response=client.put('/api/mqtt/gateways/'+gateway['id'],json={
            'expected_version':gateway['version'],'name':name,'configuration':config})
    assert response.status_code==200,response.text
    rejected=generate(client,body,before);assert rejected.status_code==409,rejected.text
    response=client.post('/api/instances/check',json=body);assert response.status_code==200,response.text
    after=response.json();assert after['configuration_hash']!=before['configuration_hash']
    assert after['sensor_sources'][0]['version']==2
    assert after['sensor_sources'][0]['connection_status']=='unknown'
    assert generate(client,body,after).status_code==201


def test_receipts_duplicate_deliveries_and_last_message_do_not_change_configuration_signature(client,account):
    gateway=source(client)
    active=client.post('/api/mqtt/gateways/'+gateway['id']+'/status',
        json={'expected_version':gateway['version'],'status':'active'})
    assert active.status_code==200,active.text
    version=active.json()['version'];body,before=checked(client)
    raw=b'{"device_id":"one","record_id":"r1","observed_at":"2026-10-10T08:00:00+08:00","values":{"t":12}}'
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','source-config-test')
        receipt=capture(db,p,gateway['id'],version,'sensor/one',raw,1);db.commit()
        capture(db,p,gateway['id'],version,'sensor/one',raw,1,duplicate=True);db.commit()
        assert db.scalar(select(func.count()).select_from(MqttReceipt))==1
        saved=db.get(MqttReceipt,receipt.id)
        assert saved.data['deliveries']==2 and saved.data['last_delivery_at']
    response=client.post('/api/instances/check',json=body);assert response.status_code==200,response.text
    after=response.json()
    assert before['configuration_hash']==after['configuration_hash']
    assert before['sensor_sources']==after['sensor_sources']
    assert generate(client,body,before).status_code==201


def test_generated_source_summary_is_durable_and_historical_not_rebound(client):
    gateway=source(client);body,before=checked(client)
    result=generate(client,body,before);assert result.status_code==201,result.text
    original=result.json();frozen=copy.deepcopy(original['snapshot']['sensor_sources'])
    config=copy.deepcopy(gateway['configuration']);config['mapping']['t']['unit']='F'
    response=client.put('/api/mqtt/gateways/'+gateway['id'],json={
        'expected_version':1,'name':'新名称','configuration':config})
    assert response.status_code==200,response.text
    response=client.post('/api/instances/check',json=body);assert response.status_code==200,response.text
    updated=generate(client,body,response.json(),original['instance_id'])
    assert updated.status_code==201,updated.text
    assert updated.json()['release_number']==2 and updated.json()['snapshot']['sensor_sources']!=frozen
    with Session(engine()) as db:
        old=db.get(InstanceRelease,original['id'])
        assert old.data['snapshot']['sensor_sources']==frozen
        assert old.configuration_hash==before['configuration_hash']
    history=client.get('/api/instances/'+original['instance_id']+'/releases').json()
    assert len(history)==2 and history[1]['snapshot']['sensor_sources']==frozen
    assert history[1]['snapshot']['sensor_sources'][0]['mapping']['t']['unit']=='C'
    # Old history remains readable without retroactively inventing a source inventory.
    with Session(engine()) as db:
        old=db.get(InstanceRelease,original['id']);data=copy.deepcopy(old.data)
        data['snapshot'].pop('sensor_sources');data['snapshot'].pop('sensor_source_issues');old.data=data;db.commit()
    old_view=client.get('/api/instances/'+original['instance_id']+'/releases').json()[1]
    assert 'sensor_sources' not in old_view['snapshot']


def test_abnormal_or_oversized_source_is_explicitly_flagged_without_echoing_credentials(client):
    gateway=source(client)
    with Session(engine()) as db:
        row=db.get(MqttGateway,gateway['id']);bad=copy.deepcopy(row.data)
        bad['broker']='mqtt://literal-user:DO_NOT_EXPOSE@127.0.0.1:1883'
        bad['mapping']['t']['unexpected_secret']='DO_NOT_EXPOSE'
        row.data=bad;db.commit()
    body,preview=checked(client);summary=preview['sensor_sources'][0]
    assert summary['summary_status']=='needs_review' and summary['binding_status']=='unknown'
    assert 'topics' not in summary and 'mapping' not in summary and 'broker' not in summary
    assert preview['sensor_source_issues'] and 'DO_NOT_EXPOSE' not in json.dumps(preview)
    assert not preview['general_issues'] and generate(client,body,preview).json()['status']=='prepared'
    with Session(engine()) as db:
        row=db.get(MqttGateway,gateway['id']);bad=sensor_configuration()
        bad['topics']={'sensor/'+str(i):'device-'+str(i) for i in range(33)};row.data=bad;db.commit()
    response=client.post('/api/instances/check',json=body);assert response.status_code==200,response.text
    assert response.json()['sensor_sources'][0]['summary_status']=='needs_review'
    assert response.json()['sensor_source_issues']


def test_review_limit_warns_and_retains_all_sources_in_frozen_generation(client,account):
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]))
        db.add_all(MqttGateway(workspace_id=account[2],owner_id=user.id,name='来源 '+str(i),
            status='paused',data=sensor_configuration()) for i in range(101));db.commit()
    body,preview=checked(client)
    assert len(preview['sensor_sources'])==101 and len({row['id'] for row in preview['sensor_sources']})==101
    assert any('101' in issue and '全部来源摘要已保留' in issue for issue in preview['sensor_source_issues'])
    assert not preview['general_issues']
    response=generate(client,body,preview);assert response.status_code==201,response.text
    assert len(response.json()['snapshot']['sensor_sources'])==101
