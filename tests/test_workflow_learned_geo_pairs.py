"""Real CPU learned candidates in task-bound workflows, using disposable records."""
import copy
import hashlib
import json
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Capability, GeoPair, Invocation, CloudCall, Memory, Task, SpecialistInstallation
from generic_agents.worker import process_once
from generic_agents.instance_compiler import signature_value
from tests.test_learned_geo_pairs import configured, rasters, create
from tests.test_instance_workspace import configuration, prepared, object_row, complete
from tests.test_workflow_scenes import attach
from tests.test_workflow_abilities import enable, finish_job, run


@pytest.fixture
def business(client, configured):
    sample=create(client,configured);assert process_once(sample['job_id'],deliver=False)
    sample=client.get('/api/geo/pairs/'+sample['id']).json();assert sample['status']=='succeeded',sample.get('error')
    url='/api/geo/pairs/'+sample['id']+'/register-business'
    response=client.post(url,json={'version':sample['version']});assert response.status_code==200,response.text
    registered=response.json();assert registered['learned_model'] and registered['kind']=='specialist_model'
    assert client.post(url,json={'version':sample['version']}).json()==registered
    c=configuration();flow=c['flows']['资料核验']
    flow['steps'][0].update(name='本地模型变化候选核查',agent='多模感知智能体',role='',execution={'capability_id':registered['capability_id'],'automation_action':'observe','arguments':{},'bindings':{}})
    c['automation']['policies']['资料核验']['rules']['observe']={'mode':'自动运行'}
    flow['confirmedSignature']=json.dumps(signature_value(c,flow),ensure_ascii=False)
    release,_,check=prepared(client,c);assert release['available_business_ids'],check;enable(client,release)
    a=client.get('/api/geo/scenes/'+configured['before_scene_id']).json();b=client.get('/api/geo/scenes/'+configured['after_scene_id']).json()
    sources=[attach(client,release,a,roi=[0,0,80,64]).json(),attach(client,release,b,roi=[10,8,72,56]).json()]
    obj=object_row(client,release)
    body={'release_id':release['id'],'business_id':release['available_business_ids'][0],'entity_ids':[obj['id']],
          'title':'实际学习型双影像业务核查','comparison_scene_ids':[s['id'] for s in sources],'comparison_confirmed':True,'request_id':str(uuid4())}
    return configured,sample,registered,release,sources,body


def start(client, values):
    release=values[3];response=client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json=values[5])
    assert response.status_code==201,response.text
    return response.json()


def test_real_learned_business_preserves_B_authorization_and_data_receipt(client,business):
    configured,sample,registered,release,sources,body=business
    task=finish_job(client,start(client,business));assert task['status']=='workflow_waiting',task
    receipt=task['workflow_runtime']['receipts'][0];output=receipt['output'];result=output['spatial_comparison']
    assert receipt['candidate_only'] and result['learned_model'] and result['cloud_calls']==0
    assert output['geo_pair_id']!=sample['id'] and result['after_read_window']==[10,8,72,56]
    assert result['common_valid_pixels']==2956 and result['unknown_pixels']==2164
    assert 'pixel_difference_candidate_pixels' not in output['attributes'] and output['anomalies']==[]
    assert receipt['comparison_scene_snapshots']==sources
    base='/api/workspace/tasks/'+task['id']+'/comparisons/'+output['geo_pair_id']
    assert client.get(base+'/artifacts/difference').status_code==404
    for kind,saved in result['artifacts'].items():
        r=client.get(base+'/artifacts/'+kind);assert r.status_code==200,r.text
        assert hashlib.sha256(r.content).hexdigest()==saved['sha256']
    assert client.get(base+'/export').json()['result']['learned_model'] is True
    assert client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json=body).json()['id']==task['id']
    response=complete(client,task,note='已核对本地模型与固定A/B范围；这里只保留工程候选，实际变化未核验')
    assert response.status_code==200,response.text
    with Session(engine()) as db:
        data=response.json()['workflow_runtime'];memory=db.get(Memory,data['data_receipt_id'])
        assert memory.status=='candidate' and not memory.data['entity_state_updated']
        assert memory.data['comparison_scene_snapshots']==sources
        assert len(list(db.scalars(select(Invocation))))==1 and len(list(db.scalars(select(GeoPair))))==2
        assert not list(db.scalars(select(CloudCall)))
    assert client.get('/api/instances/'+release['instance_id']+'/objects').json()[0]['version']==1


def test_business_input_and_contract_cannot_expand_source_or_cloud_access(client,business):
    _,_,registered,release,sources,body=business;url='/api/workspace/instances/'+release['instance_id']+'/tasks'
    for ids in ([],[sources[0]['id']],[sources[0]['id'],sources[0]['id']]):
        assert client.post(url,json={**body,'comparison_scene_ids':ids}).status_code==422
    assert client.post(url,json={**body,'comparison_confirmed':False}).status_code==422
    other,_,_=prepared(client,configuration());enable(client,other);obj=object_row(client,other)
    foreign=client.post('/api/workspace/instances/'+other['instance_id']+'/tasks',json={**body,'release_id':other['id'],
        'business_id':other['available_business_ids'][0],'entity_ids':[obj['id']]})
    assert foreign.status_code==409
    a=client.get('/api/geo/scenes/'+business[0]['before_scene_id']).json()
    small=attach(client,release,a,roi=[0,0,20,20]).json()
    assert client.post(url,json={**body,'comparison_scene_ids':[small['id'],sources[1]['id']]}).status_code==422
    with Session(engine()) as db:cap=copy.deepcopy(db.get(Capability,registered['capability_id']).data)
    for field,value in (('data_policy',{'deployment':'cloud','cloud_allowed':True,'external_allowed':True}),
                        ('execution',{**cap['execution'],'input_bindings':{'scene_refs':'task_scene_refs','task_id':'task_identifier'}})):
        forged=copy.deepcopy(cap)
        forged['name']='forged_'+uuid4().hex[:12];forged[field]=value
        r=client.post('/api/capabilities',json=forged);assert r.status_code==201,r.text
        assert client.post('/api/capabilities/'+r.json()['id']+'/validate',json={'version':r.json()['version']}).status_code==422


def test_model_generation_change_blocks_new_business_run_but_keeps_saved_sample(client,business):
    configured,sample,_,_,_,_=business;task=start(client,business)
    with Session(engine()) as db:
        model=db.get(SpecialistInstallation,configured['model_installation_id']);model.data={**model.data,'generation':2};db.commit()
    task=finish_job(client,task)
    assert task['status'] in {'workflow_failed','workflow_blocked'} and task['workflow_runtime']['receipts']==[]
    assert client.get('/api/geo/pairs/'+sample['id']+'/export').status_code==200
    with Session(engine()) as db:assert len(list(db.scalars(select(GeoPair))))==1


def test_pause_before_saving_native_result_rejects_late_result_and_can_retry(client,business,monkeypatch):
    import generic_agents.learned_geo_pairs as learned
    original=learned.call;task=start(client,business);instance_id=business[3]['instance_id']
    def pause_after_native(*args,**kwargs):
        result=original(*args,**kwargs)
        instance=next(r for r in client.get('/api/instances').json() if r['id']==instance_id)
        response=client.post('/api/instances/'+instance_id+'/pause',json={'version':instance['version']})
        assert response.status_code==200,response.text
        return result
    monkeypatch.setattr(learned,'call',pause_after_native)
    task=finish_job(client,task)
    assert task['status']=='workflow_blocked' and task['workflow_runtime']['receipts']==[]
    with Session(engine()) as db:
        partial=next(r for r in db.scalars(select(GeoPair)) if r.data.get('workflow_task_id')==task['id'])
        assert partial.status=='failed' and partial.data.get('result') is None
        partial_id=partial.id
    monkeypatch.setattr(learned,'call',original);enable(client,business[3])
    response=run(client,task,True);assert response.status_code==200,response.text
    saved=finish_job(client,response.json());assert saved['status']=='workflow_waiting',saved
    assert saved['workflow_runtime']['receipts'][0]['output']['geo_pair_id']==partial_id


def test_queued_cancel_does_not_create_model_candidates(client,business):
    task=start(client,business)
    response=client.post('/api/workspace/tasks/'+task['id']+'/cancel',json={'version':task['version'],'reason':'停止工程核查','retained_note':'原输入保留，模型尚未运行'})
    assert response.status_code==200,response.text
    assert process_once(task['workflow_runtime']['automatic']['job_id'],deliver=False)
    with Session(engine()) as db:
        assert len(list(db.scalars(select(GeoPair))))==1 and not list(db.scalars(select(Invocation)))
        assert db.get(Task,task['id']).status=='workflow_cancelled'


def test_direct_model_call_without_current_business_lease_cannot_read_candidates(client,business,account):
    from generic_agents.gateway import invoke
    from generic_agents.security import Principal
    from generic_agents.models import Membership
    from generic_agents.workflow_geo_pairs import refs
    task=start(client,business)
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]))
        principal=Principal(member.user_id,account[2],'admin','')
        row=db.get(Task,task['id']);call=invoke(db,principal,business[2]['capability_id'],'perception',{'scene_refs':refs(row),'task_id':row.id},str(uuid4()))
        assert call.status=='blocked_evidence' and '租约' in call.data['error']
        assert len(list(db.scalars(select(GeoPair))))==1 and not list(db.scalars(select(CloudCall)))


def test_canonical_contract_rejects_each_permission_change_with_name_unchanged(client,business,account):
    from fastapi import HTTPException
    from generic_agents.contracts import Manifest
    from generic_agents.security import Principal
    from generic_agents.workflow_learned_geo_pairs import validate_manifest
    with Session(engine()) as db:
        cap=copy.deepcopy(db.get(Capability,business[2]['capability_id']).data)
        p=Principal('',account[2],'admin','')
        validate_manifest(db,p,Manifest.model_validate(cap))
        changes=[('data_policy',{**cap['data_policy'],'cloud_allowed':True}),
                 ('allowed_agent_roles',['perception','data','business']),
                 ('execution',{**cap['execution'],'side_effect':True}),
                 ('execution',{**cap['execution'],'input_bindings':{'scene_refs':'task_scene_refs','task_id':'task_identifier'}})]
        for field,value in changes:
            forged={**copy.deepcopy(cap),field:value}
            with pytest.raises(HTTPException) as rejected:
                validate_manifest(db,p,Manifest.model_validate(forged))
            assert rejected.value.status_code==422


def test_corrupt_capability_hash_blocks_registration_and_job_before_model_read(client,business):
    _,sample,registered,_,_,_=business;task=start(client,business)
    with Session(engine()) as db:
        cap=db.get(Capability,registered['capability_id']);cap.content_hash='0'*64;db.commit()
    response=client.post('/api/geo/pairs/'+sample['id']+'/register-business',json={'version':sample['version']})
    assert response.status_code==409
    task=finish_job(client,task)
    assert task['status'] in {'workflow_failed','workflow_blocked'} and not task['workflow_runtime']['receipts']
    assert client.get('/api/geo/pairs/'+sample['id']+'/export').status_code==200
    with Session(engine()) as db:assert len(list(db.scalars(select(GeoPair))))==1


def test_saved_model_contract_corruption_returns_recoverable_conflict(client,business):
    task=finish_job(client,start(client,business));pair_id=task['workflow_runtime']['receipts'][0]['output']['geo_pair_id']
    with Session(engine()) as db:
        row=db.get(GeoPair,pair_id);data=copy.deepcopy(row.data)
        data['workflow_capability_snapshot']['execution']['model_trace']['generation']=0;row.data=data;db.commit()
    response=client.get('/api/workspace/tasks/'+task['id']+'/comparisons/'+pair_id+'/export')
    assert response.status_code==409 and '契约损坏' in response.text
