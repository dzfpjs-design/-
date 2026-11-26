"""Verify bounded output capacity without sending or changing a saved model."""
import copy
import pytest
from pydantic import ValidationError
from generic_agents import cloud_business_reasoning_v197 as transport
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents.gateway import ModelBlocked
from tests.test_business_reasoning_v197 import scene


def test_published_deepseek_profile_allows_8192_per_response():
    model = copy.deepcopy(scene().model)
    model.data['execution'] = {'provider': 'deepseek', 'model': 'deepseek-v4-pro',
        'secret_ref': 'DEEPSEEK_API_KEY', 'max_tokens': 8192, 'thinking_mode': 'disabled'}
    result = transport.prepare(model, [{'role':'user','content':'Synthetic content'}],
        {'type':'object','properties':{'value':{'type':'string'}}}, output_token_limit=8192)
    assert result['options']['max_tokens'] == 8192
    assert result['options']['thinking'] == {'type':'disabled'}
    assert policy.RequestedLimits(max_tokens=8192, max_request_bytes=50000, max_evidence_items=2).max_tokens == 8192
    with pytest.raises(ValidationError):
        policy.RequestedLimits(max_tokens=8193, max_request_bytes=50000, max_evidence_items=2)
    with pytest.raises(ModelBlocked):
        transport.prepare(model, [{'role':'user','content':'Synthetic content'}],
            {'type':'object'}, output_token_limit=8193)
