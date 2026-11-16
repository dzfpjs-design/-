"""Malformed frozen schemas are rejected through the pure public error contract."""
from uuid import UUID
import pytest
from generic_agents.contracts import digest
from generic_agents.local_rules_api import CreateRule, _assemble_manifest
from generic_agents.threshold_evolution_gate_v147 import compare_frozen_thresholds


def _manifest():
    identity='00000000-0000-4000-8000-000000000001'
    request=UUID('00000000-0000-4000-8000-000000000004')
    body=CreateRule.model_validate({'request_id':request,'title':'合成规则',
        'rationale':'只验证坏schema的错误边界','entity_id':identity,
        'rules':[{'id':'count','field':'quantity','unit':None,'operator':'gt','threshold':20}]})
    return _assemble_manifest(body,request,digest(body.model_dump(mode='json')),
        identity,identity,'record',identity,{'quantity':{'type':'number'}},
        {'quantity':None}).model_dump(mode='json')


@pytest.mark.parametrize(('key','value'),[('minimum',True),('multipleOf',0)])
def test_bad_schema_is_a_value_error_instead_of_an_internal_schema_exception(key,value):
    baseline=_manifest();candidate=_manifest()
    baseline['input_schema']['properties']['attributes']['properties']['quantity'][key]=value
    with pytest.raises((ValueError,TypeError)):
        compare_frozen_thresholds(baseline,candidate,[],{'max_additional_reviews':0})
