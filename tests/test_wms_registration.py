"""Real HTTP/native rasters, registered source invocation and configuration proof."""
import copy,json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Capability,User,CloudCall,WorkflowScene,WMSRead,Membership,Workspace
from generic_agents.security import Principal
from generic_agents.gateway import invoke
from generic_agents.contracts import digest
from generic_agents.config import settings
from generic_agents.instance_compiler import signature_value
from generic_agents.worker import process_once
from tests.test_wms_source import service,connect,request_read,finish
from tests.test_instance_workspace import configuration,prepared,OBJECTS,save
from tests.test_workflow_abilities import enable


def ready_map(client,connection):
    row,_=request_read(client,connection);row=finish(client,row)
    scene=client.get('/api/geo/scenes/'+row['scene_id']).json();assert process_once(scene['job_id'],deliver=False)
    scene=client.get('/api/geo/scenes/'+scene['id']).json()
    response=client.put('/api/geo/scenes/'+scene['id']+'/configuration',json={'version':scene['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True});assert response.status_code==200,response.text
    assert process_once(response.json()['job_id'],deliver=False)
    return row,client.get('/api/geo/scenes/'+scene['id']).json()


def publication(client,connection):
    body={'version':connection['version'],'scope_id':next(iter(connection['scopes']))}
    result=client.post('/api/wms/connections/'+connection['id']+'/register-source',json=body)
    assert result.status_code==200,result.text
    assert client.post('/api/wms/connections/'+connection['id']+'/register-source',json=body).json()==result.json()
    row=result.json();return row,next(iter(row['publications'].values()))


def source_resource(connection,pub):
    return {'id':'wms:'+connection['id'],'category':'maps','protocol':'WMS','name':'公开地图来源','businesses':['资料核验'],
        'checks':{'verified':True},'details':{'wms_connection_id':connection['id'],'scope_id':next(iter(connection['scopes'])),
            'capability_id':pub['capability_id'],'manifest_hash':pub['manifest_hash']}}


def check(client,c):
    revision=save(client,c,review_automation=True)
    member=client.get('/api/instances/members').json()[0]
    response=client.post('/api/instances/check',json={'revision':revision,'objects':OBJECTS,'role_bindings':{'核验负责人':member['id']}})
    assert response.status_code==200,response.text;return response.json()


def test_catalog_only_cannot_publish_saved_map_source_and_real_registry_query_never_gets_map(client,service,account):
    connection=connect(client,service)
    assert client.post('/api/wms/connections/'+connection['id']+'/register-source',json={'version':connection['version'],'scope_id':next(iter(connection['scopes']))}).status_code==409
    row,scene=ready_map(client,connection);connection,pub=publication(client,connection)
    with Session(engine(),expire_on_commit=False) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','test')
        call=invoke(db,p,pub['capability_id'],'data',{},'wms-saved-source-'+str(uuid4()))
        assert call.status=='succeeded' and call.data['output']['network_reads']==0
        maps=call.data['output']['maps'];assert maps[0]['scene_id']==scene['id'] and maps[0]['provenance']['read_id']==row['id']
        assert maps[0]['provenance']['source_capture_time_known'] is False and not call.data['output']['has_more']
        assert not db.scalar(select(func.count()).select_from(CloudCall))
        assert db.scalar(select(func.count()).select_from(Capability).where(Capability.kind=='source'))==1
    assert service['maps']==1


def test_published_source_pause_grant_revocation_and_widened_manifest_fail_closed(client,service,account):
    connection=connect(client,service);ready_map(client,connection);connection,pub=publication(client,connection)
    with Session(engine()) as db:
        cap=db.get(Capability,pub['capability_id']);value=copy.deepcopy(cap.data)
    value['name']='widened_wms';value['data_policy']['cloud_allowed']=True
    created=client.post('/api/capabilities',json=value);assert created.status_code==201
    validated=client.post('/api/capabilities/'+created.json()['id']+'/validate',json={'expected_version':created.json()['version']})
    assert validated.status_code==422
    c=configuration();c['resources']=[source_resource(connection,pub)];assert check(client,c)['businesses'][0]['available']
    paused=client.post('/api/wms/connections/'+connection['id']+'/pause',json={'version':connection['version']});assert paused.status_code==200
    assert not check(client,c)['businesses'][0]['available']
    resumed=client.post('/api/wms/connections/'+connection['id']+'/resume',json={'version':paused.json()['version']});assert resumed.status_code==200
    original=settings().wms_allowed_endpoints;settings().wms_allowed_endpoints='';assert not check(client,c)['businesses'][0]['available'];settings().wms_allowed_endpoints=original
    settings().wms_access_grants='{}';assert not check(client,c)['businesses'][0]['available']
    assert service['maps']==1


def test_compiler_freezes_server_source_proof_and_only_blocks_associated_business(client,service):
    connection=connect(client,service);ready_map(client,connection);connection,pub=publication(client,connection)
    c=configuration();c['resources']=[source_resource(connection,pub)]
    c['businesses'].append('独立人工记录');c['flows']['独立人工记录']=copy.deepcopy(c['flows']['资料核验'])
    c['automation']['policies']['独立人工记录']=copy.deepcopy(c['automation']['policies']['资料核验'])
    checked=check(client,c);assert all(b['available'] for b in checked['businesses'])
    proof=checked['resources'][0]['backend_source_proof'];assert proof['manifest_hash']==pub['manifest_hash'] and not proof['raw_satellite_bands']
    c['resources'][0]['details']['manifest_hash']='0'*64;c['resources'][0]['backend_source_proof']=proof
    checked=check(client,c);assert [b['available'] for b in checked['businesses']]==[False,True]
    assert 'backend_source_proof' not in checked['resources'][0] and not checked['general_issues']


def test_corrupt_wms_provenance_is_rejected_before_saving_instance_association(client,service):
    connection=connect(client,service);row,scene=ready_map(client,connection)
    release,_,_=prepared(client,configuration());enable(client,release)
    with Session(engine()) as db:
        saved=db.get(WMSRead,row['id']);saved.data={**saved.data,'response':{**saved.data['response'],'sha256':'0'*64}};db.commit()
    response=client.post('/api/workspace/instances/'+release['instance_id']+'/input-scenes',json={'request_id':str(uuid4()),'scene_id':scene['id'],'scene_version':scene['version']})
    assert response.status_code==409
    with Session(engine()) as db:assert not db.scalar(select(func.count()).select_from(WorkflowScene))


def test_operator_cannot_publish_and_foreign_workspace_cannot_borrow_source(client,service,account):
    connection=connect(client,service);ready_map(client,connection);connection,pub=publication(client,connection)
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));membership=db.scalar(select(Membership).where(Membership.user_id==user.id))
        membership.role='operator';foreign=Workspace(name='其他核查工作区');db.add(foreign);db.flush();db.add(Membership(user_id=user.id,workspace_id=foreign.id,role='admin'));db.commit();foreign_id=foreign.id;uid=user.id
    assert client.post('/api/wms/connections/'+connection['id']+'/register-source',json={'version':connection['version'],'scope_id':next(iter(connection['scopes']))}).status_code==403
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as denied:invoke(db,Principal(uid,foreign_id,'admin','test'),pub['capability_id'],'data',{},'foreign-wms')
        assert denied.value.status_code==404
