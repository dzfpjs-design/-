"""Frozen approval history and raw receipt transport; no real Task claim."""
import copy
from types import SimpleNamespace as NS
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from generic_agents.contracts import Manifest,digest
from generic_agents import workflow_graph_goal_guard_v133 as guard
from generic_agents import workflow_task_packet_v199 as packet
from generic_agents import workflow_task_archive_v198 as archive
from generic_agents import workflow_service_v195 as service
from generic_agents import connectors

def scene(kind):
    module={'packet':packet,'archive':archive,'service':service}[kind]
    if kind=='service':
        execution={'adapter':'workflow_service','service_actor':'task_center','operation':'in_app_notice','side_effect':True}
        ins=service.input_schema('in_app_notice');outs=service.output_schema('in_app_notice');role='business';spec={}
    else:
        execution={'adapter':module.ADAPTER,'side_effect':True};ins=module.input_schema();outs=module.output_schema();role='data'
        contract='workflow-task-evidence-packet-contract-v199' if kind=='packet' else 'workflow-task-archive-contract-v198'
        spec={'schema':contract,'operation':module.OP,'candidate_only':True}
    value=Manifest(kind='tool',namespace='goal_history_test',name='frozen_write',version='1.0.0',owner='Synthetic',description='Synthetic frozen call',allowed_agent_roles=[role],execution=execution,input_schema=ins,output_schema=outs,data_policy={'cloud_allowed':False},budget={'max_attempts':1},specification=spec).model_dump(mode='json')
    task=NS(id=str(uuid4()));job=NS(id=str(uuid4()),data={'activation_id':'a','graph_hash':'a'*64,'cancel_epoch':0,'inputs':{'synthetic':'input'}})
    node={'execution':{'adapter':execution['adapter'],'capability_id':str(uuid4()),'capability_hash':digest(value),'input_schema':ins,'output_schema':outs,'agent_role':role}}
    call=NS(data={'capability_snapshot':value,'side_effect':True,'inputs':copy.deepcopy(job.data['inputs']),'approval':module.make_approval(task,job,node,job.data['inputs'])})
    return task,job,node,call

@pytest.mark.parametrize('kind',['packet','archive','service'])
def test_real_installed_write_contract_is_accepted_as_frozen_history(kind):
    guard._node_call_history(*scene(kind))

@pytest.mark.parametrize('kind',['packet','archive','service'])
@pytest.mark.parametrize('fault',['approval','input','snapshot','numeric_effect','other_adapter'])
def test_write_history_cannot_be_rebound_or_relabelled(kind,fault):
    task,job,node,call=scene(kind)
    if fault=='approval':call.data['approval']['task_id']=str(uuid4())
    elif fault=='input':call.data['inputs']={'changed':True}
    elif fault=='snapshot':call.data['capability_snapshot']['description']='changed'
    elif fault=='numeric_effect':call.data['side_effect']=1
    else:node['execution']['adapter']='builtin'
    with pytest.raises(HTTPException):guard._node_call_history(task,job,node,call)

def test_raw_transport_callback_precedes_parse_and_keeps_exact_wire_bytes():
    raw=b'{"status": "pending", "same": 1, "same": 2}'
    seen=[]
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'application/json'},content=raw))) as client:
        value,_=connectors.bounded_response(client,'GET','https://example.invalid/receipt',on_response=lambda body,meta:seen.append((body,meta)))
    assert seen==[(raw,{'status_code':200,'content_type':'application/json'})]
    assert value['same']==2 # Strict registered-tool parsing independently rejects duplicates.
    bad=b'{"incomplete":'
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'application/json'},content=bad))) as client:
        with pytest.raises(ValueError):connectors.bounded_response(client,'GET','https://example.invalid/receipt',on_response=lambda body,meta:seen.append((body,meta)))
    assert seen[-1][0]==bad
