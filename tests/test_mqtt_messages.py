import json
import pytest
from generic_agents.mqtt_messages import decode_sensor_message, SensorFields

MAPPING={'t':{'attribute':'temperature','unit':'C'}}


def payload(**updates):
    value={'record_id':'measurement-1','device_id':'device-1','observed_at':'2026-10-10T23:30:00Z',
           'values':{'t':12.3},'units':{'t':'C'}}
    value.update(updates)
    return json.dumps(value,ensure_ascii=False).encode()


def test_original_time_china_day_unit_identity_and_exact_input_preserved():
    raw=payload(); one=decode_sensor_message(raw,MAPPING); two=decode_sensor_message(raw,MAPPING)
    assert one==two and one['usable'] and one['business_day']=='2026-10-11'
    assert one['observed_at']=='2026-10-10T23:30:00+00:00' and not one['timezone_assumed_china']
    assert one['attributes']=={'temperature':12.3} and one['unit_origin']=={'temperature':'message'}
    assert decode_sensor_message(payload(device_id='device-2'),MAPPING)['source_record_id']!=one['source_record_id']
    changed=decode_sensor_message(payload(values={'t':99}),MAPPING)
    assert changed['source_record_id']==one['source_record_id'] and changed['input_sha256']!=one['input_sha256']


def test_missing_time_is_not_replaced_by_arrival_time():
    result=decode_sensor_message(payload(observed_at=None),MAPPING)
    assert not result['usable'] and result['observed_at'] is None and result['business_day'] is None


def test_naive_time_uses_explicit_default_china_and_marks_assumption():
    result=decode_sensor_message(payload(observed_at='2026-10-10T23:30:00',units={}),MAPPING)
    assert result['observed_at'].endswith('+08:00') and result['timezone_assumed_china']
    assert result['unit_origin']=={'temperature':'confirmed_source_mapping'}


@pytest.mark.parametrize('changes', [
    {'observed_at':'wrong date'}, {'observed_at':'2026-10-10'}, {'observed_at':'9999-12-31T23:59:59Z'},
    {'values':{'t':True}}, {'values':{'t':'12.3'}},
    {'values':{'t':None}}, {'values':{'t':1e20}}, {'units':{'t':'F'}},
])
def test_uncertain_fields_never_become_usable(changes):
    result=decode_sensor_message(payload(**changes),MAPPING)
    assert not result['usable'] and result['issues']


@pytest.mark.parametrize('raw',[b'{"record_id":"a","record_id":"b"}',b'[]',b'\xff',b' '*(65537),b'{"value":NaN}'])
def test_ambiguous_or_invalid_originals_are_rejected(raw):
    with pytest.raises(ValueError):decode_sensor_message(raw,MAPPING)


def test_mapping_cannot_overwrite_an_attribute():
    with pytest.raises(ValueError):decode_sensor_message(payload(),{'t':MAPPING['t'],'other':MAPPING['t']})
    with pytest.raises(ValueError):decode_sensor_message(payload(),{'t':{**MAPPING['t'],'scale':.1}})


def test_sender_field_names_are_configurable_not_forestry_constants():
    raw=json.dumps({'serial':'packet-1','meter':'meter-1','time':'2026-10-10T12:00:00+08:00','data':{'reading':42},'units':{'reading':'kWh'}}).encode()
    result=decode_sensor_message(raw,{'reading':{'attribute':'energy','unit':'kWh'}},SensorFields('serial','meter','time','data','units'))
    assert result['usable'] and result['attributes']=={'energy':42}


def test_invalid_mapping_and_ambiguous_sender_field_configuration_fail_explicitly():
    with pytest.raises(ValueError):decode_sensor_message(payload(),['not a mapping'])
    with pytest.raises(ValueError):decode_sensor_message(payload(),MAPPING,SensorFields(device_key='record_id'))


def test_source_can_explicitly_use_capture_time_as_its_stable_record_identifier():
    result=decode_sensor_message(payload(record_id=None),MAPPING,SensorFields(record_key='observed_at'))
    assert result['usable'] and result['reported_record_id']=='2026-10-10T23:30:00Z'
