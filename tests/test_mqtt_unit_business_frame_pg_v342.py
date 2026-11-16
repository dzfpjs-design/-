"""Real MQTT packet and receipt -> business fact unit; no model call."""
import copy,json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func,select
from sqlalchemy.orm import Session

from generic_agents import business_goal_ready_frame_v288 as frame
from generic_agents import goal_ack_origin_mqtt_v297 as mqtt_origin
from generic_agents import mqtt_unit_evidence_v342 as units
from generic_agents.db import engine
from generic_agents.instance_data_receipts import observation_receipt
from generic_agents.models import Audit,Observation
from generic_agents.observation_integrity import checked_report
from generic_agents.worker import deliver_events
from tests.test_goal_ack_origin_mqtt_pg_v297 import original
from tests.test_mqtt_binding_routes import managed_pair,process_all
from tests.test_mqtt_observations import save as save_packet


def frozen_and_field(db,s,*,unit=None):
    obs=db.get(Observation,s.identity);report=checked_report(db,s.p,obs)
    cut=db.scalar(select(func.clock_timestamp()))
    origin=mqtt_origin.checked_origin(db,s.p,obs,task=s.target,cut=cut)
    record={'entity_id':obs.entity_id,'source_id':obs.source_id,'observation_id':obs.id,
            'observed_at':obs.observed_at.isoformat(),'attributes':copy.deepcopy(report['attributes'])}
    item={'kind':'observation','record':copy.deepcopy(record),'observation':{'id':obs.id,'version':obs.version,
          'source_id':obs.source_id,'entity_id':obs.entity_id,'data':copy.deepcopy(obs.data)},
          'report':copy.deepcopy(report),'origin':copy.deepcopy(origin),'reception':observation_receipt(db,s.p,obs)}
    frozen={'data':{'evaluation_record':{'evaluation_result':{'result':{'evidence':[],
             'observation_evidence':[record],'conflicts':[]}}},'fact_origins':[item]}}
    key=next(iter(report['attributes']))
    field={'entity_id':obs.entity_id,'field':key,'value_type':'number','unit':unit,'schema':{'type':'number'}}
    return frozen,[field]


def test_verified_mqtt_packet_unit_and_basis_reach_business_fact(client,account,monkeypatch):
    s=original(client,account,monkeypatch,declare_test=True)
    with Session(engine()) as db:
        frozen,fields=frozen_and_field(db,s)
        facts,sources,conflicts=frame._facts(db,s.p,frozen,fields)
        assert len(facts)==len(sources)==1 and conflicts==[]
        assert facts[0]['value']==39.5 and facts[0]['unit']=='C'
        source=json.loads(sources[0]['text'])
        assert source['field_units']=={fields[0]['field']:'C'}
        assert source['field_unit_basis']=={fields[0]['field']:'原报文提供'}
        assert json.loads(facts[0]['source_refs'][0]['quote'])==source


def test_missing_wire_unit_is_attributed_to_confirmed_mapping_in_business_fact(client,account,monkeypatch):
    from tests import test_goal_ack_origin_mqtt_pg_v297 as fixture
    monkeypatch.setattr(fixture,'save',lambda source,**kwargs:save_packet(source,units={},**kwargs))
    s=original(client,account,monkeypatch,declare_test=True)
    with Session(engine()) as db:
        frozen,fields=frozen_and_field(db,s);facts,sources,_=frame._facts(db,s.p,frozen,fields)
        source=json.loads(sources[0]['text'])
        assert facts[0]['unit']=='C'
        assert source['field_unit_basis']=={fields[0]['field']:'已确认接入映射，报文未提供'}


def test_legacy_mqtt_fact_keeps_its_old_unknown_unit_and_text(client,account,monkeypatch):
    s=original(client,account,monkeypatch,declare_test=True)
    with Session(engine()) as db:
        obs=db.get(Observation,s.identity);data=copy.deepcopy(obs.data);del data[units.KEY];obs.data=data
        db.delete(db.scalar(select(Audit).where(Audit.action==units.ACTION,Audit.object_id==obs.id)));db.commit()
    with Session(engine()) as db:
        frozen,fields=frozen_and_field(db,s);facts,sources,_=frame._facts(db,s.p,frozen,fields)
        assert facts[0]['unit'] is None
        source=json.loads(sources[0]['text']);assert source['field_units'] is None and 'field_unit_basis' not in source


def test_declared_business_unit_conflicting_with_verified_mqtt_unit_is_rejected(client,account,monkeypatch):
    s=original(client,account,monkeypatch,declare_test=True)
    with Session(engine()) as db:
        frozen,fields=frozen_and_field(db,s,unit='F')
        with pytest.raises(HTTPException):frame._facts(db,s.p,frozen,fields)


def test_normal_data_need_detail_displays_verified_mqtt_unit(client,account,monkeypatch):
    fixture,destinations,_=managed_pair(client,account,monkeypatch)
    receipt,_=save_packet(fixture);identities=process_all(receipt.id)
    release,_instance,obj,field,_rule=destinations[0]
    with Session(engine()) as db:
        identity=next(i for i in identities if db.get(Observation,i).entity_id==obj['id'])
    deliver_events(workspace_id=fixture[3].workspace_id,observation_id=identity,event_kind='observation.data')
    base='/api/workspace/instances/'+release['instance_id']+'/data-needs'
    response=client.post(base,json={'request_id':str(uuid4()),'release_id':release['id'],
        'business_id':release['available_business_ids'][0],'entity_id':obj['id'],
        'purpose':'核对该对象已接收的温度观测','fields':[field],'source':'observations',
        'confirmation':'unverified_extraction','max_age_hours':1})
    assert response.status_code==201,response.text
    detail=client.get(base+'/'+response.json()['id']);assert detail.status_code==200,detail.text
    row=next(x for x in detail.json()['records'] if x['observation_id']==identity)
    assert row['extracted_values'][field]==39.5
    assert row['measurement_units'][field]=={'unit':'C','basis':'message'}
