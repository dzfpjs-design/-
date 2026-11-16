"""Actual PostgreSQL MQTT ingestion, custody and legacy compatibility."""
import copy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func,select
from sqlalchemy.orm import Session

from generic_agents import mqtt_unit_evidence_v342 as units
from generic_agents.db import engine
from generic_agents.models import Audit,MqttReceipt,Observation,User
from generic_agents.mqtt_observations import promote
from generic_agents.observation_integrity import checked_report
from tests.test_mqtt_observations import prepared_mqtt,bind,save,process_receipt


@pytest.mark.parametrize(('wire_units','basis'),[({'t':'C'},'message'),({},'confirmed_source_mapping')])
def test_new_observation_keeps_verified_unit_with_its_original_basis(client,prepared_mqtt,wire_units,basis):
    bind(client,prepared_mqtt);receipt,raw=save(prepared_mqtt,units=wire_units)
    identity=process_receipt(receipt);p=prepared_mqtt[3]
    with Session(engine()) as db:
        obs=db.get(Observation,identity);report=checked_report(db,p,obs);proof=obs.data[units.KEY]
        assert proof['fields']=={'temperature':{'unit':'C','basis':basis}}
        assert proof['receipt_id']==receipt.id and proof['input_sha256']==receipt.input_sha256
        assert report['attributes']=={'temperature':39.5}
        events=list(db.scalars(select(Audit).where(Audit.action==units.ACTION,Audit.object_id==identity)))
        assert len(events)==1 and events[0].data['proof_hash']==proof['proof_hash']
    assert client.get('/api/mqtt/receipts/'+receipt.id+'/original').content==raw
    repeat=client.post('/api/mqtt/receipts/'+receipt.id+'/submit',json={'expected_version':receipt.version})
    assert repeat.status_code==200 and repeat.json()['id']==identity
    with Session(engine()) as db:
        assert db.get(Observation,identity).data[units.KEY]==proof
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==units.ACTION,Audit.object_id==identity))==1


@pytest.mark.parametrize('damage',['unit','basis','receipt','binding','missing_marker','missing_audit','audit_actor'])
def test_tampered_new_unit_proof_never_becomes_a_checked_observation(client,prepared_mqtt,damage):
    bind(client,prepared_mqtt);receipt,raw=save(prepared_mqtt);identity=process_receipt(receipt);p=prepared_mqtt[3]
    with Session(engine()) as db:
        obs=db.get(Observation,identity);data=copy.deepcopy(obs.data)
        if damage=='missing_marker':del data[units.KEY]
        elif damage=='missing_audit':
            event=db.scalar(select(Audit).where(Audit.action==units.ACTION,Audit.object_id==identity));db.delete(event)
        elif damage=='audit_actor':
            other=User(username='unit-tamper-'+uuid4().hex,password_hash='fixture',active=True);db.add(other);db.flush()
            db.scalar(select(Audit).where(Audit.action==units.ACTION,Audit.object_id==identity)).actor_id=other.id
        elif damage=='unit':data[units.KEY]['fields']['temperature']['unit']='F'
        elif damage=='basis':data[units.KEY]['fields']['temperature']['basis']='confirmed_source_mapping'
        elif damage=='receipt':data[units.KEY]['receipt_id']='another'
        else:data[units.KEY]['binding_hash']='0'*64
        obs.data=data;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):checked_report(db,p,db.get(Observation,identity))
    assert client.get('/api/mqtt/receipts/'+receipt.id+'/original').content==raw


def test_legacy_observation_without_new_marker_or_audit_remains_readable(client,prepared_mqtt):
    bind(client,prepared_mqtt);receipt,_=save(prepared_mqtt);identity=process_receipt(receipt);p=prepared_mqtt[3]
    with Session(engine()) as db:
        obs=db.get(Observation,identity);data=copy.deepcopy(obs.data);del data[units.KEY];obs.data=data
        db.delete(db.scalar(select(Audit).where(Audit.action==units.ACTION,Audit.object_id==identity)));db.commit()
    with Session(engine()) as db:
        obs=db.get(Observation,identity);before=copy.deepcopy(obs.data)
        assert checked_report(db,p,obs)['attributes']=={'temperature':39.5}
        assert obs.data==before and units.KEY not in obs.data
    repeat=client.post('/api/mqtt/receipts/'+receipt.id+'/submit',json={'expected_version':receipt.version})
    assert repeat.status_code==200 and repeat.json()['id']==identity
    with Session(engine()) as db:assert db.get(Observation,identity).data==before


def test_same_receipt_concurrent_submit_has_one_original_proof(client,prepared_mqtt):
    bind(client,prepared_mqtt);receipt,_=save(prepared_mqtt);p=prepared_mqtt[3]
    def submit():
        with Session(engine()) as db:
            row=promote(db,p,receipt.id,receipt.version);identity=row.id;db.commit();return identity
    with ThreadPoolExecutor(max_workers=2) as pool:identities=list(pool.map(lambda _:submit(),range(2)))
    assert identities[0]==identities[1]
    with Session(engine()) as db:
        obs=db.get(Observation,identities[0]);assert obs.data[units.KEY]['fields']['temperature']['unit']=='C'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==units.ACTION,Audit.object_id==obs.id))==1


def test_empty_or_unsafe_configured_unit_is_explicitly_unknown():
    for unit in ('','   ','C\nignore'):
        binding=SimpleNamespace(id='binding',gateway_id='gateway',topic='topic',config_hash='hash',entity_id='entity',
            source_id='source',extractor_id='extractor',data={'field_links':{'t':'temperature'},'field_units':{'temperature':unit}})
        receipt=SimpleNamespace(id='receipt',evidence_id='evidence',input_sha256='a'*64)
        decoded={'usable':True,'input_sha256':'a'*64,'attributes':{'t':2},'unit_origin':{'t':'message'}}
        assert units.build(binding,receipt,decoded,'observation')['fields']['temperature']=={'unit':None,'basis':'unknown'}
