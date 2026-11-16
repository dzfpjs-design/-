"""Actual local spatial model integrated into a confirmed business workflow."""
import json,hashlib,copy
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.models import SpecialistInstallation,Memory,Invocation,CloudCall,Evidence,WorkflowScene
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.config import settings
from generic_agents.local_specialists import SAM_RECIPE,weights_dir
from generic_agents.instance_compiler import signature_value
from generic_agents.worker import process_once
from tests.test_geo_scenes import upload,parsed
from tests.test_geo_codec import scenes,pytestmark
from tests.test_instance_workspace import prepared,configuration,complete,object_row
from tests.test_workflow_abilities import enable,finish_job


def scene_ready(client,path):
    row=upload(client,path,name='公开照片合成坐标（不是遥感实测）.tif').json();assert process_once(row['job_id'],deliver=False)
    row=client.get('/api/geo/scenes/'+row['id']).json();response=client.put('/api/geo/scenes/'+row['id']+'/configuration',json={'version':row['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True});assert response.status_code==200,response.text;assert process_once(response.json()['job_id'],deliver=False)
    return client.get('/api/geo/scenes/'+row['id']).json()


def attach(client,r,scene,**extra):
    return client.post('/api/workspace/instances/'+r['instance_id']+'/input-scenes',json={'scene_id':scene['id'],'scene_version':scene['version'],'request_id':str(uuid4()),**extra})


def spatial_config(client,account):
    with Session(engine()) as db:
        row=SpecialistInstallation(workspace_id=account[2],pack_id='sam2-1-tiny',status='ready',data={'recipe_hash':digest(SAM_RECIPE),'generation':1,'backend':'cpu'});db.add(row);db.commit();identity=row.id;version=row.version
    registered=client.post('/api/geo/installations/'+identity+'/register-spatial',json={'version':version});assert registered.status_code==200,registered.text
    replay=client.post('/api/geo/installations/'+identity+'/register-spatial',json={'version':version});assert replay.status_code==200 and replay.json()==registered.json()
    c=configuration();s=c['flows']['资料核验']['steps'][0];s.update(agent='多模感知智能体',role='',name='本地空间区域候选',execution={'capability_id':registered.json()['capability_id'],'automation_action':'observe','arguments':{},'bindings':{}})
    c['automation']['policies']['资料核验']['rules']['observe']={'mode':'自动运行'};f=c['flows']['资料核验'];f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
    r,_,check=prepared(client,c);assert r['available_business_ids'],check;enable(client,r);return r,registered.json(),c


@pytest.mark.skipif(not (weights_dir(SAM_RECIPE)/'model.safetensors').is_file() or not Path('.runtime/geo-fixture/public-photo-synthetic-map.tif').is_file(),reason='actual local SAM/public synthetic TIFF unavailable; spatial workflow release blocked')
def test_actual_spatial_model_task_freezes_settings_saves_masks_and_data_receipt(client,account):
    r,registered,c=spatial_config(client,account);obj=object_row(client,r);scene=scene_ready(client,Path('.runtime/geo-fixture/public-photo-synthetic-map.tif'))
    attached=attach(client,r,scene,roi=[100,80,740,560]);assert attached.status_code==201,attached.text;source=attached.json();assert hashlib.sha256(client.get('/api/workspace/instances/'+r['instance_id']+'/input-scenes/'+source['id']+'/preview').content).hexdigest()==source['snapshot']['preview']['sha256']
    # Later global RGB/source-time changes must not silently change an existing input.
    change=client.put('/api/geo/scenes/'+scene['id']+'/configuration',json={'version':scene['version'],'rgb_bands':[3,2,1],'map_image_confirmed':False,'observed_at':'2026-10-08T08:00:00+08:00'});assert change.status_code==200
    body={'release_id':r['id'],'business_id':r['available_business_ids'][0],'entity_ids':[obj['id']],'scene_ids':[source['id']],'title':'公开空间输入核查','request_id':str(uuid4())}
    response=client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json=body);assert response.status_code==201,response.text
    task=finish_job(client,response.json());assert task['status']=='workflow_waiting',task.get('workflow_runtime',{}).get('automatic')
    receipt=task['workflow_runtime']['receipts'][0];output=receipt['output'];result=output['spatial_segmentation'];assert result['masks'] and result['cloud_calls']==0 and receipt['candidate_only']
    assert result['source_raster']['rgb_bands']==[1,2,3] and result['source_raster']['observed_at'] is None and result['source_raster']['roi']==[100,80,740,560]
    assert task['scene_snapshots']==[source],{k:(source.get(k),task['scene_snapshots'][0].get(k)) for k in source if source.get(k)!=task['scene_snapshots'][0].get(k)};assert receipt['scene_snapshots']==[source]
    assert client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json=body).json()['id']==task['id']
    base='/api/workspace/tasks/'+task['id'];run=output['geo_run_id'];assert hashlib.sha256(client.get(base+'/scenes/'+source['id']+'/preview').content).hexdigest()==source['snapshot']['preview']['sha256'];rgb=client.get(base+'/spatial/'+run+'/rgb');assert hashlib.sha256(rgb.content).hexdigest()==result['image_sha256']
    assert client.get(base+'/scenes/'+source['id']+'/original').content==Path('.runtime/geo-fixture/public-photo-synthetic-map.tif').read_bytes()
    for mask in result['masks']:
        response=client.get(base+'/spatial/'+run+'/masks/'+mask['mask_evidence_id']);assert response.status_code==200
        assert hashlib.sha256(response.content).hexdigest()==mask['mask_sha256'] and mask['bbox_original'][0]>=100 and mask['bbox_original'][1]>=80
    export=client.get(base+'/spatial/'+run+'/native-geojson');assert export.status_code==200 and export.json()['business_review']['task_title']==body['title']
    last=result['masks'][-1]
    with Session(engine()) as db:
        ev=db.get(Evidence,last['mask_evidence_id']);path=settings().storage_dir/ev.storage_key;original=path.read_bytes();path.write_bytes(b'corrupt')
    assert complete(client,task).status_code==409 and client.get(base+'/spatial/'+run+'/native-geojson').status_code==409
    path.write_bytes(original)
    final=complete(client,task);assert final.status_code==200,final.text;task=final.json()
    with Session(engine()) as db:
        memory=db.get(Memory,task['workflow_runtime']['data_receipt_id']);assert memory.status=='candidate' and memory.data['scene_snapshots']==[source] and not memory.data['entity_state_updated']
        assert len(db.scalars(select(Invocation)).all())==1 and not db.scalars(select(CloudCall)).all()
    assert client.get('/api/instances/'+r['instance_id']+'/objects').json()[0]['version']==1


def test_instance_association_and_task_creation_refuse_corrupt_or_other_instance_input(client,scenes):
    r,_,_=prepared(client,configuration());enable(client,r);obj=object_row(client,r);scene=scene_ready(client,scenes/'rgb.tif')
    body={'scene_id':scene['id'],'scene_version':scene['version'],'request_id':str(uuid4())};url='/api/workspace/instances/'+r['instance_id']+'/input-scenes'
    response=client.post(url,json=body);assert response.status_code==201,response.text;source=response.json();assert client.post(url,json=body).json()['id']==source['id']
    recovery='/api/workspace/instances/'+r['instance_id']+'/scene-associations/'+body['request_id']
    assert client.get(recovery+'?expected_hash='+digest({**body,'roi':None})).json()==source and client.get(recovery+'?expected_hash='+'f'*64).status_code==409 and client.get('/api/workspace/instances/'+r['instance_id']+'/scene-associations/'+str(uuid4())).status_code==404
    assert client.post(url,json={**body,'roi':[1,1,5,5]}).status_code==409
    assert attach(client,r,scene,roi=[0,0,11,8]).status_code==422
    other_config=configuration();other_config['instanceProfile']['name']='另一个公开业务实例'
    other,_,_=prepared(client,other_config);enable(client,other);other_object=object_row(client,other)
    other_task={'release_id':other['id'],'business_id':other['available_business_ids'][0],'entity_ids':[other_object['id']],'scene_ids':[source['id']],'title':'不能借用其他实例影像','request_id':str(uuid4())}
    assert client.post('/api/workspace/instances/'+other['instance_id']+'/tasks',json=other_task).status_code==409
    with Session(engine()) as db:
        row=db.get(WorkflowScene,source['id']);saved=copy.deepcopy(row.data);row.data={**row.data,'snapshot':{**row.data['snapshot'],'roi':[0,0,5,5]}};db.commit()
    task_body={'release_id':r['id'],'business_id':r['available_business_ids'][0],'entity_ids':[obj['id']],'scene_ids':[source['id']],'title':'固定影像核查','request_id':str(uuid4())}
    assert client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json=task_body).status_code==409 and client.get(recovery).status_code==409
    with Session(engine()) as db:
        row=db.get(WorkflowScene,source['id']);row.data=saved;db.commit();ev=db.get(Evidence,scene['evidence_id']);(settings().storage_dir/ev.storage_key).write_bytes(b'corrupt')
    assert client.post('/api/workspace/instances/'+r['instance_id']+'/tasks',json=task_body).status_code==409
