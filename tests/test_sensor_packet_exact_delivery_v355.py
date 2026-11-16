import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Event, Memory
from generic_agents.worker import deliver_events
from tests.test_mqtt_inbox import gateway, save, packet


def test_one_original_receipt_delivered_without_other_packet(gateway):
    first=save(gateway,packet(record_id='exact-1'))
    second=save(gateway,packet(record_id='exact-2'))
    ws=gateway[0].workspace_id
    with Session(engine()) as db:
        events=db.scalars(select(Event).where(Event.kind=='perception.sensor_packet')).all()
        event_ids={e.data['receipt_id']:e.id for e in events}
    deliver_events(workspace_id=ws,event_kind='perception.sensor_packet',packet_receipt_id=second.id)
    with Session(engine()) as db:
        assert db.get(Event,event_ids[second.id]).status=='received'
        assert db.get(Event,event_ids[first.id]).status=='pending_delivery'
        originals=db.scalars(select(Memory).where(Memory.data['type'].as_string()=='sensor_original')).all()
        assert len(originals)==1 and originals[0].data['receipt_id']==second.id
    deliver_events(workspace_id=ws,event_kind='perception.sensor_packet',packet_receipt_id=second.id)
    with Session(engine()) as db:
        assert db.get(Event,event_ids[first.id]).status=='pending_delivery'
        assert len(db.scalars(select(Memory).where(Memory.data['type'].as_string()=='sensor_original')).all())==1


@pytest.mark.parametrize('kwargs',[
    {'event_kind':'perception.sensor_packet','packet_receipt_id':'x'},
    {'workspace_id':'x','packet_receipt_id':'x'},
    {'workspace_id':'x','event_kind':'observation.data','packet_receipt_id':'x'},
    {'workspace_id':'x','event_kind':'perception.sensor_packet','packet_receipt_id':''},
])
def test_receipt_filter_rejects_ambiguous_scope(kwargs):
    with pytest.raises(ValueError): deliver_events(**kwargs)
