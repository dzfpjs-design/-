"""Real pure entry policy only; a bool cannot prove a real route or Delivery.

The production Task path calls this only after current binding verification
and prepare_handoff. Root must separately exercise actual PG/HTTP routing.
"""
import copy
import pytest
from fastapi import HTTPException
from generic_agents import business_flow_execution_runtime as runtime
from generic_agents import business_flow_execution_graph as graph


def envelope(kind='event',version=graph.BINDINGS_V2):
    return {'schema':runtime.NEW_BINDING,'flow':{'source_flow':{'trigger':{'kind':kind}}},
        'execution_bindings':{'schema':version,**({'trigger':{'mode':graph.EVENT_TRIGGER_MODE}} if version==graph.BINDINGS_V2 else {})}}


def test_event_fresh_task_requires_the_previously_verified_real_route_path():
    binding=envelope();before=copy.deepcopy(binding)
    with pytest.raises(HTTPException,match='') as error:
        runtime.require_task_start(binding,verified_anomaly_handoff=False)
    assert error.value.status_code==409 and '普通手动任务' in error.value.detail
    runtime.require_task_start(binding,verified_anomaly_handoff=True)
    assert binding==before


@pytest.mark.parametrize('via_route',[False,True])
def test_existing_manual_binding_retains_manual_and_separately_authorized_route_entry(via_route):
    runtime.require_task_start(envelope('manual',graph.BINDINGS),verified_anomaly_handoff=via_route)


@pytest.mark.parametrize('fault',['v1_event','v2_manual','v2_schedule','v2_arrival','missing_mode','extra_mode','wrong_mode','unknown_schema','no_flow','bad_flow','bad_bindings'])
def test_conflicting_or_incomplete_entry_policy_never_authorizes_new_start(fault):
    value=envelope()
    if fault=='v1_event':value=envelope('event',graph.BINDINGS)
    elif fault.startswith('v2_'):value['flow']['source_flow']['trigger']['kind']={'v2_manual':'manual','v2_schedule':'schedule','v2_arrival':'data_arrival'}[fault]
    elif fault=='missing_mode':value['execution_bindings']['trigger']={}
    elif fault=='extra_mode':value['execution_bindings']['trigger']['automatic']=True
    elif fault=='wrong_mode':value['execution_bindings']['trigger']['mode']='trust_description'
    elif fault=='unknown_schema':value['execution_bindings']['schema']='unknown'
    elif fault=='no_flow':value.pop('flow')
    elif fault=='bad_flow':value['flow']=[]
    elif fault=='bad_bindings':value['execution_bindings']=[]
    before=copy.deepcopy(value)
    with pytest.raises(HTTPException) as error:runtime.require_task_start(value,verified_anomaly_handoff=True)
    assert error.value.status_code==409 and value==before


@pytest.mark.parametrize('value',[1,0,None,'true',{},[]])
def test_entry_flag_is_strict_and_not_a_truthy_serialized_authority(value):
    with pytest.raises(HTTPException) as error:runtime.require_task_start(envelope(),verified_anomaly_handoff=value)
    assert error.value.status_code==409
