"""Closed command tests only; no application execution by the author."""
import pytest
from pydantic import ValidationError
from generic_agents.monitor_scheduling_contracts_v253 import ModeCommand

BODY={'schema':'monitor-scheduling-command-v253','request_id':'模式/原号?保留-123456','instance_id':'22222222-2222-4222-8222-222222222222',
    'expected_version':0,'expected_policy_version':1,'approved_hash':'a'*64,'mode':'goal_only','reason':'仅已确认业务目标可补采'}

def test_unicode_nonce_and_two_explicit_modes():
    assert ModeCommand.model_validate(BODY).request_id==BODY['request_id']
    assert ModeCommand.model_validate({**BODY,'mode':'periodic'}).mode=='periodic'

@pytest.mark.parametrize('change',[{'expected_version':True},{'expected_policy_version':False},{'expected_version':-1},
    {'mode':'legacy'},{'mode':'paused'},{'approved_hash':'x'*64},{'can_execute':True},{'request_id':'short'},
    {'instance_id':'not-an-instance'},{'reason':''}])
def test_no_public_bypass_or_invented_legacy_command(change):
    with pytest.raises(ValidationError):ModeCommand.model_validate({**BODY,**change})



def test_retirement_is_an_explicit_negative_request_without_head_cas():
    import json
    from generic_agents.contracts import digest
    from generic_agents.monitor_scheduling_contracts_v253 import RetireCommand
    import hashlib
    raw=json.dumps(BODY,ensure_ascii=False)
    value={'schema':'monitor-scheduling-retire-v253','request_id':BODY['request_id'],'instance_id':BODY['instance_id'],
        'original_raw':raw,'raw_sha256':hashlib.sha256(raw.encode()).hexdigest(),'body_hash':digest(BODY),'reason':'保留原文并结束这份申请'}
    assert RetireCommand.model_validate(value).request_id==BODY['request_id']
    with pytest.raises(ValidationError):RetireCommand.model_validate({**value,'expected_version':1})
    with pytest.raises(ValidationError):RetireCommand.model_validate({**value,'actor_id':'self-claimed'})
