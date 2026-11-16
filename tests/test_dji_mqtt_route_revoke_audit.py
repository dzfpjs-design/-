"""Revoke must serialize with new MQTT object-relationship commits."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import sleep
from uuid import uuid4
import pytest
from tests.test_mqtt_observations import prepared_mqtt
from tests.test_managed_mqtt_grants import vault

@pytest.mark.parametrize("operation",["create","update"])
def test_revoke_waits_for_inflight_object_link(client,prepared_mqtt,vault,monkeypatch,operation):
    from generic_agents import mqtt_inbox
    original,obj,rule,_=prepared_mqtt
    result=client.post('/api/mqtt/authorizations',json={'request_id':str(uuid4()),'name':'managed','host':'192.168.0.10','port':8883,'username':'fixture','password':'fixture'})
    assert result.status_code==200;grant=result.json()['authorization']
    cfg={'broker':grant['broker'],'topics':{'sensor/one':'device-one'},'mapping':{'t':{'attribute':'temperature','unit':'C'}},'fields':{},'username_ref':grant['username_ref'],'password_ref':grant['password_ref']}
    updated=client.put('/api/mqtt/gateways/'+original['id'],json={'expected_version':original['version'],'name':'managed source','configuration':cfg});assert updated.status_code==200
    gateway=client.post('/api/mqtt/gateways/'+original['id']+'/status',json={'expected_version':updated.json()['version'],'status':'active'});assert gateway.status_code==200
    if operation=="update":
        from tests.test_mqtt_binding_routes import create as create_route
        existing,_=create_route(client,(gateway.json(),obj,rule,None))
    entered,release=Event(),Event();original_validate=mqtt_inbox.validate_configuration
    def slow_validate(*args,**kwargs):
        value=original_validate(*args,**kwargs);entered.set();assert release.wait(8);return value
    monkeypatch.setattr(mqtt_inbox,'validate_configuration',slow_validate)
    body={'expected_version':gateway.json()['version'],'request_id':str(uuid4()),'topic':'sensor/one','entity_id':obj['id'],'extractor_id':rule['id'],'include_existing':False}
    with ThreadPoolExecutor(max_workers=2) as pool:
        if operation=='create':
            pending=pool.submit(client.post,'/api/mqtt/gateways/'+original['id']+'/routes',json=body)
        else:
            body['expected_version']=existing['version']
            pending=pool.submit(client.put,'/api/mqtt/routes/'+existing['id'],json=body)
        assert entered.wait(6)
        revoke=pool.submit(client.post,'/api/mqtt/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':grant['version'],'enabled':False})
        try:
            sleep(.4);assert not revoke.done(), 'revoke committed before in-flight object association'
        finally:
            release.set()
        assert pending.result(timeout=8).status_code==(201 if operation=='create' else 200)
        assert revoke.result(timeout=8).status_code==200
