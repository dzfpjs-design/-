"""New wire domain and manual-clock behavior, no SQL or model execution."""
import pytest
from fastapi import HTTPException
from generic_agents.threshold_policy_protocol_v152 import parse_command,PolicyInput
from generic_agents.threshold_policy_intake_v152 import registration_clock

def body(value):
    return {'schema':'threshold-policy-input-v152','request_id':'15300000-0000-4000-8000-000000000001',
        'expected_version':1,'expected_choice':'candidate','source_record_id':'new','processing_version':'1.0.0',
        'observed_at':None,'business_day':'2026-10-21','attributes':{'count':value}}

@pytest.mark.parametrize('value',[10**400,-10**400,2**53,-2**53])
def test_unrepresentable_integer_is_closed_422_not_overflow_or_rounded(value):
    with pytest.raises(HTTPException) as error:parse_command(PolicyInput,body(value))
    assert error.value.status_code==422

@pytest.mark.parametrize('value',[2**53-1,-(2**53-1)])
def test_exact_browser_integer_boundary_is_preserved(value):
    saved=parse_command(PolicyInput,body(value));assert type(saved.attributes['count']) is int and saved.attributes['count']==value

def test_default_china_day_has_no_timezone_setting_and_no_utc_reinterpretation():
    actual=registration_clock('2026-10-21T20:00:00Z','2026-10-22',{})
    assert actual.isoformat()=='2026-10-21T20:00:00+00:00'
    with pytest.raises(HTTPException):registration_clock('2026-10-21T20:00:00Z','2026-10-21',{})

def test_explicit_source_timezone_has_priority():
    assert registration_clock('2026-10-21T20:00:00Z','2026-10-21',{'timezone':'UTC'}) is not None

@pytest.mark.parametrize('value',['',None,False,'Invalid/Zone'])
def test_invalid_source_timezone_cannot_silently_default(value):
    with pytest.raises(HTTPException) as error:registration_clock(None,'2026-10-21',{'timezone':value})
    assert error.value.status_code==409

def test_unknown_clock_stays_unknown_instead_of_inventing_now():
    assert registration_clock(None,'2026-10-21',{}) is None
