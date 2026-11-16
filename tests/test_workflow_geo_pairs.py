"""Actual local A/B comparison in a pinned workflow, rather than reusing a sample result."""
import json
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import GeoPair,Memory,Invocation,CloudCall,Evidence
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.instance_compiler import signature_value
from generic_agents.worker import process_once
from generic_agents.geo_pair_codec import call
from tests.test_geo_codec import scenes,pytestmark
from tests.test_geo_pairs import pairs,ready,body,request
from tests.test_instance_workspace import configuration,prepared,object_row,complete
from tests.test_workflow_abilities import enable,finish_job
from tests.test_workflow_scenes import attach


def configured(client,pairs):
    a=ready(client,pairs/'rgb.tif','A工程合成.tif');b=ready(client,pairs/'after.tif','B工程合成.tif')
    r=client.post('/api/geo/pairs',json=body(a,b)).json();assert process_once(r['job_id'],deliver=False)
    r=client.get('/api/geo/pairs/'+r['id']).json();response=client.post('/api/geo/pairs/'+r['id']+'/register-business',json={'version':r['version']});assert response.status_code==200,response.text
    registered=response.json();assert client.post('/api/geo/pairs/'+r['id']+'/register-business',json={'version':r['version']}).json()==registered
    c=configuration();flow=c['flows']['资料核验'];flow['steps'][0].update(name='本地双影像像素对照',agent='多模感知智能体',role='',execution={'capability_id':registered['capability_id'],'automation_action':'observe','arguments':{},'bindings':{}})
    c['automation']['policies']['资料核验']['rules']['observe']={'mode':'自动运行'};flow['confirmedSignature']=json.dumps(signature_value(c,flow),ensure_ascii=False)
    release,_,check=prepared(client,c);assert release['available_business_ids'],check;enable(client,release)
    sources=[attach(client,release,a,roi=[0,0,10,8]).json(),attach(client,release,b,roi=[3,2,9,7]).json()]
    obj=object_row(client,release)
    value={'release_id':release['id'],'business_id':release['available_business_ids'][0],'entity_ids':[obj['id']],'title':'实际双影像业务核查','comparison_scene_ids':[s['id'] for s in sources],'comparison_confirmed':True,'request_id':str(uuid4())}
    return a,b,registered,release,sources,value


def test_v2_preserves_B_scope_and_marks_outside_unknown(pairs,tmp_path):
    out=tmp_path/'out';out.mkdir();r=call(pairs/'rgb.tif',pairs/'after.tif',request(after_allowed_window=[3,2,9,7]),out,recipe_id='geo-pair-v2')
    assert r['after_read_window']==[3,2,9,7] and r['common_valid_pixels']==26 and r['unknown_pixels']==54 and r['candidate_pixels']==6
    assert r['resampling']=='none' and r['learned_model'] is False
    # The shifted affine must warp only the saved B crop, including its nodata.
    from PIL import Image
    warped=tmp_path/'warped';warped.mkdir()
    r=call(pairs/'rgb.tif',pairs/'shifted.tif',request(alignment='reproject_after_to_before',after_allowed_window=[3,2,9,7]),warped,recipe_id='geo-pair-v2')
    assert r['after_read_window']==[3,2,9,7] and r['common_valid_pixels']==26 and r['unknown_pixels']==54 and r['candidate_pixels']==6
    assert r['resampling']=='nearest' and r['registration_accuracy']=='not_verified'
    with Image.open(warped/'valid.png') as image:
        assert image.getpixel((3,4))==0 and image.getpixel((4,4))==255 and image.getpixel((8,2))==0
    with Image.open(warped/'candidate.png') as image:
        assert image.getpixel((5,4))[3]>0 and image.getpixel((4,4))[3]==0


def test_real_pair_workflow_freezes_input_has_original_candidates_and_data_receipt(client,pairs):
    a,b,registered,release,sources,value=configured(client,pairs)
    assert client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json={**value,'comparison_confirmed':False}).status_code==422
    r=client.put('/api/geo/scenes/'+a['id']+'/configuration',json={'version':a['version'],'rgb_bands':[3,2,1],'map_image_confirmed':True});assert r.status_code==200
    assert process_once(r.json()['job_id'],deliver=False)
    response=client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json=value);assert response.status_code==201,response.text
    task=finish_job(client,response.json());assert task['status']=='workflow_waiting',task.get('workflow_runtime',{}).get('automatic')
    receipt=task['workflow_runtime']['receipts'][0];output=receipt['output'];result=output['spatial_comparison'];pair_id=output['geo_pair_id'];base='/api/workspace/tasks/'+task['id']+'/comparisons/'+pair_id
    assert task['comparison_scene_snapshots']==sources and receipt['comparison_scene_snapshots']==sources
    assert result['candidate_pixels']==6 and result['common_valid_pixels']==26 and result['unknown_pixels']==54 and receipt['candidate_only']
    assert client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json=value).json()['id']==task['id']
    for kind in result['artifacts']:assert client.get(base+'/artifacts/'+kind).status_code==200
    assert client.get(base+'/export').json()['business_review']['task_title']==value['title']
    with Session(engine()) as db:
        pair=db.get(GeoPair,pair_id);assert pair.data['sources'][0]['configuration']['rgb_bands']==[1,2,3]
        ev=db.get(Evidence,result['artifacts']['candidate']['evidence_id']);path=settings().storage_dir/ev.storage_key;raw=path.read_bytes();path.write_bytes(b'damaged')
    assert complete(client,task,note='不得在损坏候选时推进').status_code==409 and client.get(base+'/export').status_code==409
    path.write_bytes(raw)
    response=complete(client,task,note='已核对A/B及未知区域，仅保存候选；实际变化待现场复核');assert response.status_code==200,response.text;task=response.json()
    with Session(engine()) as db:
        memory=db.get(Memory,task['workflow_runtime']['data_receipt_id']);assert memory.status=='candidate' and memory.data['comparison_scene_snapshots']==sources and not memory.data['entity_state_updated']
        assert len(db.scalars(select(GeoPair)).all())==2 and len(db.scalars(select(Invocation)).all())==1 and not db.scalars(select(CloudCall)).all()
    assert client.get('/api/instances/'+release['instance_id']+'/objects').json()[0]['version']==1


def test_pair_task_refuses_missing_duplicate_and_other_instance_sources(client,pairs):
    a,b,registered,r,sources,value=configured(client,pairs);url='/api/workspace/instances/'+r['instance_id']+'/tasks'
    for ids in ([],[sources[0]['id']],[sources[0]['id'],sources[0]['id']]):assert client.post(url,json={**value,'comparison_scene_ids':ids}).status_code==422
    other,_,_=prepared(client,configuration());enable(client,other);obj=object_row(client,other)
    response=client.post('/api/workspace/instances/'+other['instance_id']+'/tasks',json={**value,'release_id':other['id'],'business_id':other['available_business_ids'][0],'entity_ids':[obj['id']]});assert response.status_code==409
    queued=client.post(url,json=value).json();response=client.post('/api/workspace/tasks/'+queued['id']+'/cancel',json={'version':queued['version'],'reason':'取消公开工程任务','retained_note':'保留对照来源，尚未执行'});assert response.status_code==200,response.text
    with Session(engine()) as db:assert len(db.scalars(select(GeoPair)).all())==1 and not db.scalars(select(Invocation)).all()


def test_pair_registered_contract_cannot_replace_task_bindings(client,pairs):
    a,b,registered,r,sources,value=configured(client,pairs)
    from generic_agents.workflow_geo_pairs import manifest
    m=manifest(40,'same_grid').model_dump(mode='json');m['name']='forged_pair';m['execution']['input_bindings']['scene_refs']='task_scene_refs'
    cap=client.post('/api/capabilities',json=m);assert cap.status_code==201,cap.text
    response=client.post('/api/capabilities/'+cap.json()['id']+'/validate',json={'version':cap.json()['version']});assert response.status_code==422
