"""Pure response format tests; these do not establish a real HTTP response."""
import copy
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import same_goal_advice_first_response_candidate_v2 as capture
from generic_agents.gateway import ModelBlocked


def receipt(call):return {'provider':'deepseek','model':'synthetic','call_id':call,'http_status':200,
    'usage':{},'usage_unavailable':True,'usage_unavailable_reason':'unparsed_original_http'}


def test_preserves_exact_original_and_offline_recovers_usage():
    call,intent=str(uuid4()),str(uuid4())
    body={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'value':3})}}],
        'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}}
    raw=json.dumps(body,ensure_ascii=False).encode()
    saved=capture.seal(cloud_call_id=call,intent_audit_id=intent,raw=raw,receipt=receipt(call))
    assert capture.check(saved,cloud_call_id=call,intent_audit_id=intent)==raw
    result,actual=capture.parse_saved(saved,cloud_call_id=call,intent_audit_id=intent,
        schema={'type':'object','properties':{'value':{'type':'integer'}},'required':['value']})
    assert result=={'value':3} and actual['usage']['total_tokens']==5


def test_invalid_answer_remains_preserved_and_never_invents_usage():
    call,intent=str(uuid4()),str(uuid4());raw=b'not valid json\xff'
    saved=capture.seal(cloud_call_id=call,intent_audit_id=intent,raw=raw,receipt=receipt(call))
    assert capture.check(saved,cloud_call_id=call,intent_audit_id=intent)==raw
    with pytest.raises(ModelBlocked):capture.parse_saved(saved,cloud_call_id=call,intent_audit_id=intent,schema={'type':'object'})
    assert saved['receipt']['usage_unavailable'] is True and saved['receipt']['usage']=={}


def test_changed_bytes_or_identity_are_rejected():
    call,intent=str(uuid4()),str(uuid4())
    saved=capture.seal(cloud_call_id=call,intent_audit_id=intent,raw=b'response',receipt=receipt(call))
    altered=copy.deepcopy(saved);altered['raw_base64']='eA=='
    with pytest.raises((HTTPException,ValueError)):capture.check(altered,cloud_call_id=call,intent_audit_id=intent)
    with pytest.raises((HTTPException,ValueError)):capture.check(saved,cloud_call_id=str(uuid4()),intent_audit_id=intent)


def test_missing_usage_is_not_recorded_as_zero():
    call,intent=str(uuid4()),str(uuid4())
    raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}).encode()
    saved=capture.seal(cloud_call_id=call,intent_audit_id=intent,raw=raw,receipt=receipt(call))
    result,actual=capture.parse_saved(saved,cloud_call_id=call,intent_audit_id=intent,schema={'type':'object'})
    assert result=={} and actual['usage_unavailable'] is True and actual['usage']=={}


def test_http_error_and_oversized_metadata_are_not_silently_discarded():
    call,intent=str(uuid4()),str(uuid4());metadata={**receipt(call),'http_status':429}
    saved=capture.seal(cloud_call_id=call,intent_audit_id=intent,raw=b'rate limited',receipt=metadata)
    with pytest.raises(ModelBlocked):capture.parse_saved(saved,cloud_call_id=call,intent_audit_id=intent,schema={'type':'object'})
    assert capture.check(saved,cloud_call_id=call,intent_audit_id=intent)==b'rate limited'
    with pytest.raises((HTTPException,ValueError)):
        capture.seal(cloud_call_id=call,intent_audit_id=intent,raw=b'',receipt={**metadata,'message':'x'*70000})
