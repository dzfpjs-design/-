"""PG/API/worker quality acceptance with true local raster processing, synthetic sources."""
import subprocess
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import CloudCall,GeoScene,Job
from generic_agents.worker import process_once,deliver_events

pytestmark=pytest.mark.skipif(not settings().geo_python.is_file(),reason='local raster interpreter unavailable')

@pytest.fixture
def source_files(tmp_path):
    script=tmp_path/'generate.py';script.write_text('''import sys,numpy as np,rasterio
from pathlib import Path
from rasterio.transform import from_origin
root=Path(sys.argv[1])
for side in ('before','after'):
 with rasterio.open(root/(side+'.tif'),'w',driver='GTiff',height=64,width=64,count=3,dtype='uint8',crs='EPSG:32650',transform=from_origin(500000,2700000,10,10)) as d:d.write(np.full((3,64,64),120 if side=='before' else 135,dtype='uint8'))
 q=np.full((32,32),4,dtype='uint8')
 if side=='after':q[16,16]=9
 with rasterio.open(root/(side+'-quality.tif'),'w',driver='GTiff',height=32,width=32,count=1,dtype='uint8',crs='EPSG:32650',transform=from_origin(500000,2700000,20,20)) as d:d.write(q,1)
''')
    subprocess.run([str(settings().geo_python),str(script),str(tmp_path)],check=True,capture_output=True);return tmp_path


def scene(client,path,*,image):
    request=str(uuid4());response=client.post('/api/geo/scenes',data={'request_id':request},files={'file':(path.name,path.read_bytes(),'image/tiff')})
    assert response.status_code==201,response.text;row=response.json();assert process_once(row['job_id'],deliver=False)
    current=client.get('/api/geo/scenes/'+row['id']).json();assert current['status']=='parsed',current
    if image:
        result=client.put('/api/geo/scenes/'+row['id']+'/configuration',json={'version':current['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True})
        assert result.status_code==200,result.text;assert process_once(result.json()['job_id'],deliver=False)
        current=client.get('/api/geo/scenes/'+row['id']).json();assert current['status']=='ready',current
    return current


def prepared(client,source_files):
    selected={'before_image':scene(client,source_files/'before.tif',image=True),'after_image':scene(client,source_files/'after.tif',image=True),
        'before_quality':scene(client,source_files/'before-quality.tif',image=False),'after_quality':scene(client,source_files/'after-quality.tif',image=False)}
    adapter=client.post('/api/geo/quality/adapters/sentinel2-scl/register');assert adapter.status_code==200,adapter.text
    body={'request_id':'quality_'+uuid4().hex,'adapter_id':adapter.json()['id'],'association_confirmed':True}
    for name,row in selected.items():body[name+'_id']=row['id'];body[name+'_version']=row['version']
    return body,selected


def test_real_quality_job_creates_persisted_masked_scenes_and_exact_request_recovery(client,source_files,account):
    body,selected=prepared(client,source_files);route='/api/geo/quality/assessments'
    assert client.post(route,json={**body,'association_confirmed':False}).status_code==422
    saved=client.post(route,json=body);assert saved.status_code==201,saved.text;job=saved.json();assert job['status']=='queued'
    assert client.post(route,json=body).json()['id']==job['id']
    assert client.post(route,json={**body,'after_quality_id':body['before_quality_id']}).status_code in (409,422)
    assert client.get('/api/geo/quality/requests/'+body['request_id']).json()['id']==job['id']
    assert process_once(job['id'],deliver=False)
    response=client.get(route+'/'+job['id']);assert response.status_code==200,response.text;done=response.json();assert done['status']=='succeeded',done
    result=done['result'];assert result['source_association']=='user_declared_unverified' and result['classification_accuracy']=='not_verified'
    assert result['native']['total_pixels']==4096 and 0<result['native']['common_valid_pixels']<4096 and len(result['derived_scenes'])==2
    assert not result['object_state_updated'] and result['cloud_calls']==0
    for artifact in result['artifacts']:
        response=client.get(route+'/'+job['id']+'/artifacts/'+artifact['filename']);assert response.status_code==200 and response.headers['x-content-sha256']==artifact['sha256']
    for derived in result['derived_scenes']:
        original=client.get('/api/geo/scenes/'+derived['scene_id']).json();assert original['status']=='queued' and original['quality_origin_v351']['quality_job_id']==job['id']
        assert process_once(original['job_id'],deliver=False)
        ready=client.get('/api/geo/scenes/'+derived['scene_id']).json();assert ready['status']=='ready',ready
        assert ready['preview']['valid_pixels'] < ready['preview']['sampled_pixels']
    with Session(engine()) as db:
        assert not db.scalars(select(CloudCall)).all();record=db.get(Job,job['id']);assert record.status=='succeeded'
        for name,row in selected.items():assert db.get(GeoScene,row['id']).data['input_sha256']==row['input_sha256']
    assert client.get(route+'/'+job['id']).json()['result']==result
    assert done['data_reception']['state']=='pending'
    deliver_events(event_kind='data.geo_quality_report_v352')
    assert client.get(route+'/'+job['id']).json()['data_reception']['state']=='received'
    derived={x['side']:client.get('/api/geo/scenes/'+x['scene_id']).json() for x in result['derived_scenes']}
    body_pair={'request_id':'quality_pair_'+uuid4().hex,'before_scene_id':derived['before']['id'],'after_scene_id':derived['after']['id'],
        'before_version':derived['before']['version'],'after_version':derived['after']['version'],'roi':[0,0,64,64],
        'alignment':'same_grid','threshold':10,'rgb_comparability_confirmed':True}
    rejected=client.post('/api/geo/pairs',json={**body_pair,'after_scene_id':selected['after_image']['id'],'after_version':selected['after_image']['version']})
    assert rejected.status_code==409,rejected.text
    pair=client.post('/api/geo/pairs',json=body_pair);assert pair.status_code==201,pair.text
    assert all(x['quality_origin_v351']['quality_job_id']==job['id'] for x in pair.json()['sources'])
    assert process_once(pair.json()['job_id'],deliver=False)
    comparison=client.get('/api/geo/pairs/'+pair.json()['id']).json();assert comparison['status']=='succeeded',comparison
    assert comparison['result']['common_valid_pixels']==result['native']['common_valid_pixels']
    assert comparison['result']['unknown_pixels']==result['native']['unknown_or_excluded_pixels']
    assert comparison['result']['cloud_shadow_screening']=='not_applied'
    from generic_agents import geo_quality_v351 as quality,geo_pairs
    from generic_agents.security import Principal
    from generic_agents.models import Membership,GeoPair
    from fastapi import HTTPException
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));p=Principal(membership.user_id,account[2],membership.role,'test')
        derived_scene=db.get(GeoScene,derived['before']['id']);derived_scene.data={k:v for k,v in derived_scene.data.items() if k!='quality_origin_v351'};db.flush()
        with pytest.raises(HTTPException):quality.checked_scene(db,p,derived_scene)
        with pytest.raises(HTTPException):geo_pairs.inputs(db,p,db.get(GeoPair,comparison['id']))
        db.rollback()


def test_source_version_change_blocks_processing_without_creating_derived_scenes(client,source_files):
    body,_=prepared(client,source_files);saved=client.post('/api/geo/quality/assessments',json=body);assert saved.status_code==201;job=saved.json()
    update=client.put('/api/geo/scenes/'+body['before_image_id']+'/configuration',json={'version':body['before_image_version'],'rgb_bands':[1,2,3],'map_image_confirmed':True});assert update.status_code==200
    assert process_once(job['id'],deliver=False);failed=client.get('/api/geo/quality/assessments/'+job['id']);assert failed.status_code==200
    assert failed.json()['status']=='failed' and failed.json()['result'] is None
    assert not any(x.get('quality_origin_v351',{}).get('quality_job_id')==job['id'] for x in client.get('/api/geo/scenes').json())


def test_all_cloud_quality_keeps_original_and_produces_no_comparison_scene(client,source_files):
    code="""import sys,numpy as np,rasterio
with rasterio.open(sys.argv[1],'r+') as ds: ds.write(np.full((ds.height,ds.width),9,dtype='uint8'),1)
"""
    subprocess.run([str(settings().geo_python),'-c',code,str(source_files/'after-quality.tif')],check=True,capture_output=True)
    body,originals=prepared(client,source_files)
    response=client.post('/api/geo/quality/assessments',json=body);assert response.status_code==201,response.text
    identity=response.json()['id'];assert process_once(identity,deliver=False)
    report=client.get('/api/geo/quality/assessments/'+identity);assert report.status_code==200,report.text
    result=report.json()['result'];assert result['native']['common_valid_pixels']==0
    assert result['native']['unknown_or_excluded_pixels']==result['native']['total_pixels'] and result['derived_scenes']==[]
    assert client.get('/api/geo/quality/assessments/'+identity+'/artifacts/common-valid.png').status_code==200
    assert all(client.get('/api/geo/scenes/'+scene['id']).status_code==200 for scene in originals.values())
