"""Routing and original-producer waiting; authority IO seams remain explicit.

These checks do not establish an adopted Task or a real model response. The
service's producer checker is separately tested against its actual parser.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import workflow_monitor_schedule_v200 as monitor
from generic_agents import workflow_service_catalog_v197 as catalog
from generic_agents import workflow_provenance_wait_v197 as waits
from generic_agents.business_flow_nested_execution_v195 import service_delegation
from generic_agents.contracts import Manifest, digest
from tests.test_workflow_provenance_wait_v197 import scene as wait_scene, producer, identity


def manifest():
    return Manifest(kind='tool', namespace='monitor_wiring', name='cadence', version=monitor.RELEASE_VERSION,
        owner='Synthetic test', description='Local monitor-check cadence; no hardware control',
        allowed_agent_roles=['business'], input_schema=monitor.input_schema(), output_schema=monitor.output_schema(),
        execution={'adapter':'workflow_service','service_actor':'execution_service','operation':monitor.OP,'side_effect':True},
        data_policy={'cloud_allowed':False}, budget={'max_attempts':1}).model_dump(mode='json')


def retained_call():
    data=manifest(); cap_id=identity('monitor-wiring-cap'); cap_hash=digest(data)
    approval={'schema':'workflow-service-approval-v195','capability_id':cap_id,
        'capability_hash':cap_hash,'input_hash':'b'*64}
    return NS(status='unknown',capability_id=cap_id,data={'capability_snapshot':data,
        'capability_hash':cap_hash,'input_hash':'b'*64,'role':'business','side_effect':True,'approval':approval,
        monitor.CALL_KEY:{'schema':monitor.INSTALL,'installation_id':identity('original-installation'),'record_hash':'a'*64}})


def test_new_service_selects_actual_installed_module_without_changing_old_service_family():
    data=manifest(); assert catalog.family(data) is monitor
    monitor.validate_manifest(data)
    assert waits._family(NS(data=data))=='monitor_schedule'
    assert service_delegation('execution_service',monitor.OP)=={
        'service_actor':'execution_service','operation':monitor.OP,'agent_role':'business','side_effect':True}
    for operation in ('in_app_notice','resense_with_receipt'):
        assert catalog.family({'execution':{'adapter':'workflow_service','operation':operation}}).__name__.endswith('workflow_service_v195')


@pytest.mark.parametrize('fault',['missing_marker','bad_marker','bad_schema','bad_hash','noncanonical_id',
    'changed_snapshot','changed_capability','changed_input','other_role','not_side_effect','wrong_approval'])
def test_unknown_call_cannot_be_routed_by_a_damaged_or_foreign_monitor_marker(fault):
    call=retained_call(); assert catalog.monitoring_receipt_retained(call)
    if fault=='missing_marker':del call.data[monitor.CALL_KEY]
    elif fault=='bad_marker':call.data[monitor.CALL_KEY]=None
    elif fault=='bad_schema':call.data[monitor.CALL_KEY]['schema']='workflow-service-state-v195'
    elif fault=='bad_hash':call.data[monitor.CALL_KEY]['record_hash']='z'*64
    elif fault=='noncanonical_id':call.data[monitor.CALL_KEY]['installation_id']='original'
    elif fault=='changed_snapshot':call.data['capability_snapshot']['description']='changed'
    elif fault=='changed_capability':call.capability_id=identity('another-monitor-cap')
    elif fault=='changed_input':call.data['input_hash']='c'*64
    elif fault=='other_role':call.data['role']='data'
    elif fault=='not_side_effect':call.data['side_effect']=False
    else:call.data['approval']['schema']='unapproved'
    assert catalog.monitoring_receipt_retained(call) is False


def test_reasoning_wait_keeps_exact_original_job_call_and_output_and_rechecks_after_finish(monkeypatch):
    s=wait_scene.__wrapped__(monkeypatch)
    original,call,_context=producer(s)
    s.job.data['inputs']={'reasoning_result':copy.deepcopy(call.data['output'])}
    pin={'job_id':original.id,'invocation_id':call.id,'actor_id':original.data['actor_id'],
        'request_key_hash':digest(original.data['invocation_key'])}
    def checker(db,p,task,job,inputs):
        assert (db,task,job)==(s.db,s.task,s.job)
        assert inputs==s.job.data['inputs']
        if original.status=='running':raise waits.reader.ProducerPending(original.id,call.id)
        return {**pin,'capture_hash':'d'*64}
    monkeypatch.setattr(monitor,'producer_wait_pin',checker)
    from generic_agents import worker
    monkeypatch.setattr(worker,'actor',lambda db,workspace,actor_id:s.p)
    before=(s.job.id,s.job.idempotency_key,s.job.attempts,copy.deepcopy(s.task.data))
    dependencies=waits._monitor_dependencies(s.db,s.p,s.task,s.job,s.job.data['inputs'])
    assert len(dependencies)==1 and dependencies[0]['kind']=='reasoning_producer'
    dependency=dependencies[0]
    assert dependency['pin']==pin and dependency['source_path']=='/reasoning_result'
    assert waits._dependency_terminal(s.db,s.task,s.job,dependency) is False
    original.status='succeeded'
    assert waits._dependency_terminal(s.db,s.task,s.job,dependency) is True
    assert waits._monitor_dependencies(s.db,s.p,s.task,s.job,s.job.data['inputs'])==[]
    assert before==(s.job.id,s.job.idempotency_key,s.job.attempts,s.task.data)
    assert s.db.additions==[] and s.db.present_call is None
    s.job.data['inputs']['reasoning_result']['actual_saved_output']='changed'
    with pytest.raises(HTTPException):waits._dependency_terminal(s.db,s.task,s.job,dependency)
