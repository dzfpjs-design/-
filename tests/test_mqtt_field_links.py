from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Observation,MqttReceipt
from tests.test_mqtt_observations import prepared_mqtt,save,process_receipt


def test_source_measurement_names_can_bind_to_business_fields_without_editing_originals(client,prepared_mqtt):
    gateway,obj,rule,p=prepared_mqtt
    original_configuration={**gateway['configuration'],'mapping':{'t':{'attribute':'air_temperature','unit':'C'}}}
    updated=client.put('/api/mqtt/gateways/'+gateway['id'],json={
        'name':gateway['name'],'expected_version':gateway['version'],'configuration':original_configuration})
    assert updated.status_code==200
    gateway=client.post('/api/mqtt/gateways/'+gateway['id']+'/status',json={
        'expected_version':updated.json()['version'],'status':'active'}).json()
    fixture=(gateway,obj,rule,p);receipt,raw=save(fixture)
    response=client.post('/api/mqtt/gateways/'+gateway['id']+'/bindings',json={
        'expected_version':gateway['version'],'topic':'sensor/one','entity_id':obj['id'],'extractor_id':rule['id'],
        'field_links':{'air_temperature':'temperature'}})
    assert response.status_code==201,response.text
    identity=process_receipt(receipt)
    with Session(engine()) as db:
        assert db.get(MqttReceipt,receipt.id).data['decoded']['attributes']=={'air_temperature':39.5}
        observation=db.get(Observation,identity)
        assert observation.data['attributes']=={'temperature':39.5}
        assert observation.data['extracted']['attributes']=={'temperature':39.5}
    assert client.get('/api/mqtt/receipts/'+receipt.id+'/original').content==raw


def test_field_links_cannot_ignore_fields_duplicate_targets_or_apply_scale(client,prepared_mqtt):
    gateway,obj,rule,p=prepared_mqtt
    for mapping in ({'temperature':'other'}, {}, {'temperature':{'field':'temperature','scale':.1}}):
        response=client.post('/api/mqtt/gateways/'+gateway['id']+'/bindings',json={
            'expected_version':gateway['version'],'topic':'sensor/one','entity_id':obj['id'],'extractor_id':rule['id'],
            'field_links':mapping})
        assert response.status_code==422,response.text
