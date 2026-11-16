"""Legacy blank bootstrap must adopt normal grounded results without client-created boilerplate."""
import copy
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from tests.test_setup_understanding import prepared,start,process
from tests.test_setup_understanding_v4 import wire


def test_first_grounded_adoption_initializes_only_structure_and_keeps_real_materials(client,monkeypatch):
    body,material=prepared(client)
    # Reproduce the actual pre-V261 initial persisted state, BEFORE preview or
    # provider response. No result, source, receipt or success state is forged.
    with Session(engine()) as db:
        row=db.scalar(select(m.SetupDraft));row.data={};db.commit();body['revision']=row.version
    body.update(contract_version=4,request_id=str(uuid4()))
    sent,_=wire(monkeypatch);source,_=start(client,body);process(source)
    result=client.get('/api/setup/understanding/'+source['id']).json();assert result['status']=='review'
    original_result=copy.deepcopy(result['result'])
    apply={'version':result['version'],'revision':body['revision'],'names':[result['result']['businesses'][0]['name']],
        'object_names':[],'accept_domain':True,'request_id':str(uuid4()),'result_hash':result['result_hash'],'configuration_hash':digest({})}
    response=client.post('/api/setup/understanding/'+source['id']+'/apply',json=apply)
    assert response.status_code==200,response.text
    saved=response.json();configuration=saved['configuration']
    assert configuration['version']==1 and configuration['connections']==[] and configuration['resources']==[]
    assert [x['id'] for x in configuration['materials']]==[material['id']]
    assert configuration['businesses']==apply['names'] and len(configuration['flows'][apply['names'][0]]['steps'])==1
    replay=client.get('/api/setup/understanding/'+source['id']+'/apply-receipts/'+apply['request_id'])
    assert replay.status_code==200 and replay.json()['receipt']==saved['receipt']
    with Session(engine()) as db:
        row=db.get(m.SetupAnalysis,source['id']);assert row.data['before_configuration']=={} and row.data['result']==original_result
    undo=client.post('/api/setup/understanding/'+source['id']+'/undo',json={
        'version':saved['analysis']['version'],'revision':saved['revision'],'names':apply['names']})
    assert undo.status_code==200,undo.text
    assert undo.json()['configuration']['businesses']==[] and undo.json()['configuration']['resources']==[]
    reused=client.post('/api/setup/understanding/'+source['id']+'/reuse',json={
        'version':undo.json()['analysis']['version'],'revision':undo.json()['revision']})
    assert reused.status_code==201,reused.text
    assert reused.json()['result']==original_result and len(sent)==1


def test_structural_initializer_does_not_silently_repair_nonempty_malformed_configuration():
    from fastapi import HTTPException
    from generic_agents.setup_understanding import apply_configuration
    from generic_agents.setup_api import initial_configuration
    proposal={'domain':'test','objects':[],'businesses':[],'questions':[]}
    assert apply_configuration({},proposal,[])['version']==1
    bad={'version':99,'resources':[],'connections':[]}
    with pytest.raises(HTTPException):apply_configuration(bad,proposal,[])
    assert bad['version']==99 and initial_configuration()['businesses']==[]
