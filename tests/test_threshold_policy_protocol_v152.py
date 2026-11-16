"""Real closed protocol functions; no calls, model inference or DB authority."""
from copy import deepcopy
import json
import pytest
from fastapi import HTTPException
from generic_agents import threshold_policy_protocol_v152 as protocol
from generic_agents.config import Settings

ID='15200000-0000-4000-8000-00000000000a'
REVIEW='15200000-0000-4000-8000-000000000002'

def create():
    return {'schema':'threshold-policy-create-v152','request_id':ID,'review_id':REVIEW,
        'expected_review_version':1,'title':'仅此原范围','reason':'管理员明确批准有限采用',
        'max_candidate_inputs':2,'expires_at':'2026-10-23T12:00:00+08:00','confirm_use':True}

def intake():
    return {'schema':'threshold-policy-input-v152','request_id':ID,'expected_version':1,
        'expected_choice':'candidate','source_record_id':'原件-01','processing_version':'1.0.0',
        'observed_at':None,'business_day':'2026-10-21','attributes':{'count':19.0}}

def parse(value,model):return protocol.parse_command(model,value)

def test_server_defaults_to_production_and_requests_cannot_choose_environment():
    assert Settings.model_fields['threshold_policy_environment'].default=='production'
    value=create();value['environment']='verification'
    with pytest.raises(HTTPException):parse(value,protocol.CreatePolicy)

@pytest.mark.parametrize('value',[True,False,1.0,0,-1,31,'2'])
def test_candidate_budget_is_actual_bounded_integer(value):
    original=create();original['max_candidate_inputs']=value
    with pytest.raises(HTTPException) as error:parse(original,protocol.CreatePolicy)
    assert error.value.status_code==422

@pytest.mark.parametrize('field',['expected_review_version','confirm_use'])
@pytest.mark.parametrize('value',[1.0,'1',False,None])
def test_version_and_consent_have_no_coercion(field,value):
    original=create();original[field]=value
    with pytest.raises(HTTPException):parse(original,protocol.CreatePolicy)

@pytest.mark.parametrize('time',['2026-10-23 12:00:00+08:00','20261023T120000+08:00',
    '2026-10-23T12:00:00+0800','2026-10-23T12:00:00','2026-02-30T12:00:00Z'])
def test_expiry_must_be_valid_unambiguous_wire_time(time):
    original=create();original['expires_at']=time
    with pytest.raises(HTTPException):parse(original,protocol.CreatePolicy)

def test_unknown_observation_time_remains_explicit_unknown_and_numeric_type_preserved():
    original=intake();body=parse(original,protocol.PolicyInput)
    assert body.observed_at is None and type(body.attributes['count']) is float
    assert body.model_dump(by_alias=True)==original

@pytest.mark.parametrize('value',[True,False,'19',None,float('nan'),float('inf')])
def test_actual_rule_input_never_accepts_boolean_text_or_nonfinite(value):
    original=intake();original['attributes']['count']=value
    with pytest.raises(HTTPException):parse(original,protocol.PolicyInput)

@pytest.mark.parametrize('change',[{'attributes':{}},{'attributes':{'a':1,'b':2}},
    {'business_day':'2026-02-30'},{'observed_at':'2026-10-21T12:00:00'},
    {'expected_version':True},{'expected_choice':'automatic'},{'source_record_id':' '}])
def test_new_input_does_not_guess_choices_dates_fields_or_identity(change):
    with pytest.raises(HTTPException):parse({**intake(),**change},protocol.PolicyInput)

@pytest.mark.parametrize('method,path',[('GET',protocol.ROOT),('POST',protocol.ROOT+'/'),
    ('POST',protocol.ROOT+'/'+ID+'/publish'),('POST',protocol.ROOT+'/'+ID.upper()+'/pause'),
    ('POST',protocol.ROOT+'/'+ID+'/inputs/extra')])
def test_no_alias_route_can_acquire_policy_authority(method,path):
    with pytest.raises(ValueError):protocol.identify(method,path)

def test_exact_original_wire_survives_read_and_different_bytes_stay_different():
    value=intake();raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
    assert protocol.read_command(raw)==value
    assert protocol.identify('POST',protocol.ROOT+'/'+ID+'/inputs')==(ID,'input',protocol.PolicyInput)
    duplicate=raw[:-1]+b',"request_id":"'+ID.encode()+b'"}'
    with pytest.raises(ValueError):protocol.read_command(duplicate)

@pytest.mark.parametrize('raw',[b'null',b'[]',b'1',b'"text"',b'true'])
def test_policy_wire_rejects_nonobject_with_protocol_error(raw):
    with pytest.raises(ValueError):protocol.read_command(raw)
