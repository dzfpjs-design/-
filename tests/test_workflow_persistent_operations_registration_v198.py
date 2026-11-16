"""Real authenticated registry and route checks on the owned disposable PG.

Registration is not proof of a leased Task, dispatch or successful archive.
"""
import copy
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import workflow_task_archive_v198 as archive
from generic_agents import workflow_task_dispatch_v198 as dispatch
from generic_agents import business_flow_execution_api as catalog
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.gateway import invoke
from generic_agents.models import Capability, Invocation, Acquisition, CloudCall
from generic_agents.workflow_abilities import eligible
from tests.test_workflow_service_registration_v195 import good, publish, authority


def manifest(kind):
    module = archive if kind == 'archive' else dispatch
    return Manifest(kind='tool',namespace='persistent_operations_v198',name=kind+'_'+uuid4().hex[:10],
        version='1.0.0',owner='工程验收账号',description='登记实际持久操作；登记本身不代表执行成功',
        allowed_agent_roles=['data' if kind == 'archive' else 'business'],
        input_schema=module.input_schema(),output_schema=module.output_schema(),
        execution=({'adapter':'task_archive','side_effect':True} if kind == 'archive' else
            {'adapter':'workflow_service','service_actor':'task_center','operation':dispatch.OP,'side_effect':True}),
        specification=({'schema':'workflow-task-archive-contract-v198','operation':archive.OP,'candidate_only':True}
            if kind == 'archive' else {}),data_policy={'cloud_allowed':False},
        budget={'max_attempts':1,'max_concurrency':1}).model_dump(mode='json')


@pytest.mark.parametrize('kind',['archive','dispatch'])
def test_real_registry_catalog_pin_and_no_unapproved_dispatch(client,account,kind):
    row=publish(client,manifest(kind))
    with Session(engine()) as db:
        cap=db.get(Capability,row['id']);p=authority(db,account)
        assert cap.content_hash == cap.validated_hash == digest(cap.data)
        _,choices=catalog._nested_catalog(db,p)
        choice=next(item for item in choices if item['id']==cap.id)
        assert choice['eligible_agent_roles']==['data' if kind=='archive' else 'business']
        assert choice['requires_approved_plan'] is True
        if kind=='archive':
            with pytest.raises(HTTPException):eligible(cap,'data')
            eligible(cap,'data',allow_task_archive=True)
            inputs={'record_type':'核验','title':'候选归档','summary':'尚未提交独立验收'}
            with pytest.raises(HTTPException) as error:invoke(db,p,cap.id,'data',inputs,'unapproved-'+uuid4().hex)
            assert error.value.status_code==409
        for model in (Invocation,Acquisition,CloudCall):
            assert not list(db.scalars(select(model).where(model.workspace_id==account[2])))


@pytest.mark.parametrize('fault',['side_effect','cloud','schema','role','operation'])
def test_archive_registration_rejects_contract_drift(client,account,fault):
    value=manifest('archive')
    if fault=='side_effect':value['execution']['side_effect']=False
    if fault=='cloud':value['data_policy']['cloud_allowed']=True
    if fault=='schema':value['input_schema']['additionalProperties']=True
    if fault=='role':value['allowed_agent_roles']=['business']
    if fault=='operation':value['specification']['operation']='confirm_archive'
    draft=good(client.post('/api/capabilities',json=value),201)
    result=client.post('/api/capabilities/'+draft['id']+'/validate',json={'expected_version':draft['version'],'reason':'拒绝不匹配的操作'})
    assert result.status_code in {409,422}


def test_normal_app_exports_exact_routes_without_duplicate_prefix(client,account):
    from generic_agents.api import app
    paths=app.openapi()['paths']
    assert '/api/task-archives' in paths
    assert '/api/workspace/work-orders-v198' in paths
    assert not any('/api/workspace/api/task-archives' in path for path in paths)
    assert client.get('/api/task-archives',params={'task_id':str(uuid4())}).status_code==404
    assert good(client.get('/api/workspace/work-orders-v198'))['items']==[]
