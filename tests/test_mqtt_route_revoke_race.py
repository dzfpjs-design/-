"""Association creation/revision must serialize with managed broker revoke."""
from concurrent.futures import ThreadPoolExecutor
import time
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import MqttBindingRoute,MqttBindingRevision
from generic_agents.mqtt_binding_routes import create_route,update_route
from tests.test_managed_mqtt_grants import vault
from tests.test_mqtt_observations import prepared_mqtt
from tests.test_mqtt_binding_routes import create as api_create

@pytest.mark.parametrize('operation',['create','update'])
def test_association_change_is_fenced_by_broker_revocation(client,prepared_mqtt,vault,operation):
    old,obj,rule,p=prepared_mqtt
    grant=client.post('/api/mqtt/authorizations',json={'request_id':str(uuid4()),'name':'synthetic','host':'192.168.0.10','port':8883,'username':'u','password':'p'}).json()['authorization']
    cfg={'broker':grant['broker'],'topics':{'sensor/one':'device-one'},'mapping':{'t':{'attribute':'temperature','unit':'C'}},'fields':{},'username_ref':grant['username_ref'],'password_ref':grant['password_ref']}
    response=client.put('/api/mqtt/gateways/'+old['id'],json={'expected_version':old['version'],'name':'scoped','configuration':cfg});assert response.status_code==200,response.text
    response=client.post('/api/mqtt/gateways/'+old['id']+'/status',json={'expected_version':response.json()['version'],'status':'active'});assert response.status_code==200,response.text
    gateway=response.json();fixture=(gateway,obj,rule,p);existing=None
    if operation=='update':existing,_=api_create(client,fixture)
    body={'expected_version':gateway['version'] if operation=='create' else existing['version'],'request_id':str(uuid4()),'topic':'sensor/one','entity_id':obj['id'],'extractor_id':rule['id'],'include_existing':False,'field_links':None}
    with Session(engine(),expire_on_commit=False) as db:
        route=create_route(db,p,gateway['id'],body) if operation=='create' else update_route(db,p,existing['id'],body)
        route_id=route.id;expected_revisions=1 if operation=='create' else 2
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(client.post,'/api/mqtt/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':grant['version'],'enabled':False})
            time.sleep(.25);assert not future.done(),'撤权不能先于在途关联提交'
            db.commit();revoked=future.result(timeout=5)
    assert revoked.status_code==200,revoked.text
    new_body={**body,'request_id':str(uuid4()),'expected_version':gateway['version'] if operation=='create' else route.version}
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as exc:
            create_route(db,p,gateway['id'],new_body) if operation=='create' else update_route(db,p,route_id,new_body)
        assert exc.value.status_code==403;db.rollback()
        assert db.scalar(select(func.count()).select_from(MqttBindingRevision).where(MqttBindingRevision.route_id==route_id))==expected_revisions
    restore=client.post('/api/mqtt/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':revoked.json()['authorization']['version'],'enabled':True});assert restore.status_code==200,restore.text
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(MqttBindingRevision).where(MqttBindingRevision.route_id==route_id))==expected_revisions
        assert db.scalar(select(func.count()).select_from(MqttBindingRoute).where(MqttBindingRoute.gateway_id==gateway['id']))==1
