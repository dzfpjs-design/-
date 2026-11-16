"""Real original MQTT observations must not be merged across unknown units."""
import copy
from datetime import datetime,timedelta,timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import mqtt_unit_evidence_v342 as units
from generic_agents.db import engine
from generic_agents.models import Audit,Observation
from generic_agents.perception import object_state
from tests.test_mqtt_observations import prepared_mqtt,bind,save,process_receipt


@pytest.mark.parametrize('case',['same_unit','different_unit','one_legacy_unknown','both_legacy'])
def test_same_time_numeric_state_checks_units_without_inventing_a_conversion(client,prepared_mqtt,case):
    first_gateway,obj,rule,p=prepared_mqtt;bind(client,prepared_mqtt)
    unit='F' if case=='different_unit' else 'C'
    configuration=copy.deepcopy(first_gateway['configuration']);configuration['mapping']['t']['unit']=unit
    r=client.post('/api/mqtt/gateways',json={'name':'第二个独立模拟来源','configuration':configuration})
    assert r.status_code==201,r.text;second=r.json()
    r=client.post('/api/mqtt/gateways/'+second['id']+'/status',json={'expected_version':second['version'],'status':'active'})
    assert r.status_code==200,r.text;other=(r.json(),obj,rule,p);bind(client,other)
    at=(datetime.now(timezone.utc)-timedelta(seconds=3)).isoformat()
    a,_=save(prepared_mqtt,record='unit-first',value=25,capture_time=at)
    b,_=save(other,record='unit-second',value=25,capture_time=at,units={'t':unit})
    first_id=process_receipt(a);second_id=process_receipt(b)
    if case in {'one_legacy_unknown','both_legacy'}:
        with Session(engine()) as db:
            for identity in ([first_id,second_id] if case=='both_legacy' else [first_id]):
                obs=db.get(Observation,identity);data=copy.deepcopy(obs.data);del data[units.KEY];obs.data=data
                db.delete(db.scalar(select(Audit).where(Audit.action==units.ACTION,Audit.object_id==identity)))
            db.commit()
    with Session(engine()) as db:
        state=object_state(db,p,obj['id'],['temperature'],3600);db.commit()
        field=state['fields']['temperature'];assert set(field['observation_ids'])=={first_id,second_id}
        if case=='same_unit':
            assert not field['conflicted'] and field['value']==25
            assert field['measurement_unit']=={'unit':'C','basis':'message'}
        elif case=='both_legacy':
            assert not field['conflicted'] and field['value']==25
            assert 'measurement_unit' not in field and 'unit_conflicted' not in field
        else:
            assert field['conflicted'] and field['unit_conflicted'] and field['value'] is None
            assert state['status']=='conflict' and '单位' in state['conflicts'][0]['reason']
