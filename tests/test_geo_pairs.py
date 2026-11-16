"""Actual locked Rasterio comparison and durable human review; disposable database only."""
import hashlib,io,json,subprocess
from uuid import uuid4
import pytest
from PIL import Image
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.config import settings
from generic_agents.geo_pair_codec import call
from generic_agents.models import GeoPair,Evidence,CloudCall,Membership,Workspace
from generic_agents.db import engine
from generic_agents.worker import process_once
from tests.test_geo_codec import scenes,pytestmark
from tests.test_geo_scenes import upload


@pytest.fixture
def pairs(scenes):
    script=scenes/'pair.py'
    script.write_text('''import sys,numpy as np,rasterio
from pathlib import Path
from affine import Affine
root=Path(sys.argv[1])
with rasterio.open(root/'rgb.tif') as src:profile=src.profile;data=src.read()
data[:,4:6,4:7]=180;data[:,2:4,7:9]=0
for name,transform in [('after',profile['transform']),('shifted',profile['transform']*Affine.translation(1,0))]:
 p={**profile,'transform':transform}
 with rasterio.open(root/(name+'.tif'),'w',**p) as dst:dst.write(data)
''')
    result=subprocess.run([str(settings().geo_python),str(script),str(scenes)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr[-500:]
    return scenes


def request(**updates):
    value={'roi':[0,0,10,8],'alignment':'same_grid','threshold':40,'before_configuration':{'rgb_bands':[1,2,3]},'after_configuration':{'rgb_bands':[1,2,3]}}
    return {**value,**updates}


def test_true_window_difference_never_calls_nodata_unchanged(pairs,tmp_path):
    out=tmp_path/'out';out.mkdir();r=call(pairs/'rgb.tif',pairs/'after.tif',request(),out)
    assert r['common_valid_pixels']==72 and r['unknown_pixels']==8 and r['candidate_pixels']==6
    assert r['learned_model'] is False and r['cloud_calls']==0 and r['registration_accuracy']=='not_verified'
    with Image.open(out/'valid.png') as im:assert im.getpixel((8,2))==0 and im.getpixel((4,4))==255
    with Image.open(out/'candidate.png') as im:assert im.getpixel((8,2))[3]==0 and im.getpixel((4,4))[3]>0
    out=tmp_path/'roi';out.mkdir();r=call(pairs/'rgb.tif',pairs/'after.tif',request(roi=[4,4,7,6]),out)
    assert r['candidate_pixels']==6 and r['total_pixels']==6 and r['unknown_pixels']==0


def test_alignment_requires_explicit_choice_and_reports_unverified_registration(pairs,tmp_path):
    out=tmp_path/'bad';out.mkdir()
    with pytest.raises(ValueError,match='静默'):call(pairs/'rgb.tif',pairs/'shifted.tif',request(),out)
    out=tmp_path/'warp';out.mkdir();r=call(pairs/'rgb.tif',pairs/'shifted.tif',request(alignment='reproject_after_to_before'),out)
    assert r['resampling']=='nearest' and r['common_valid_pixels']==64 and r['unknown_pixels']==16
    with Image.open(out/'candidate.png') as im:assert im.getpixel((5,4))[3]>0 and im.getpixel((4,4))[3]==0
    out=tmp_path/'unknown';out.mkdir()
    with pytest.raises(ValueError,match='不猜测'):call(pairs/'unknown.tif',pairs/'after.tif',request(),out)


def ready(client,path,name):
    r=upload(client,path,name=name).json();assert process_once(r['job_id'],deliver=False)
    r=client.get('/api/geo/scenes/'+r['id']).json()
    response=client.put('/api/geo/scenes/'+r['id']+'/configuration',json={'version':r['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True});assert response.status_code==200,response.text
    assert process_once(response.json()['job_id'],deliver=False)
    return client.get('/api/geo/scenes/'+r['id']).json()


def body(a,b,**changes):
    return {'request_id':str(uuid4()),'before_scene_id':a['id'],'after_scene_id':b['id'],'before_version':a['version'],'after_version':b['version'],'roi':[0,0,10,8],'alignment':'same_grid','threshold':40,'rgb_comparability_confirmed':True,**changes}


def test_pair_sources_frozen_original_request_recovered_review_export_durable(client,pairs):
    a=ready(client,pairs/'rgb.tif','合成A.tif');b=ready(client,pairs/'after.tif','合成B.tif');value=body(a,b)
    response=client.post('/api/geo/pairs',json=value);assert response.status_code==201,response.text;r=response.json();path='/api/geo/pairs/'+r['id']
    assert client.post('/api/geo/pairs',json=value).json()['id']==r['id']
    assert client.post('/api/geo/pairs',json={**value,'threshold':50}).status_code==409
    assert client.get('/api/geo/pairs/requests/'+value['request_id']+'?expected_hash='+r['request_hash']).json()['id']==r['id']
    assert client.get('/api/geo/pairs/requests/'+value['request_id']+'?expected_hash=wrong').status_code==409
    # Later display settings do not replace frozen source RGB or scope.
    res=client.put('/api/geo/scenes/'+a['id']+'/configuration',json={'version':a['version'],'rgb_bands':[3,2,1],'map_image_confirmed':True});assert res.status_code==200
    assert process_once(res.json()['job_id'],deliver=False)
    assert process_once(r['job_id'],deliver=False);r=client.get(path).json();assert r['status']=='succeeded',r.get('error')
    assert r['sources'][0]['configuration']['rgb_bands']==[1,2,3] and r['chronology']=='unknown_dates_A_B_comparison'
    assert r['result']['candidate_pixels']==6 and r['result']['geometry']['pixel_area']==6
    for kind,ev in r['result']['artifacts'].items():
        response=client.get(path+'/artifacts/'+kind);assert response.status_code==200
        assert hashlib.sha256(response.content).hexdigest()==ev['sha256']
    note={'version':r['version'],'note':'工程合成样本：排除无效像素，实物变化待核实','decision':'uncertain'}
    r=client.put(path+'/review',json=note).json();assert r['review']['note']==note['note']
    assert client.put(path+'/review',json=note).status_code==409
    assert client.get(path).json()['review']==r['review']
    response=client.get(path+'/export');assert response.status_code==200 and response.json()['review']==r['review']
    with Session(engine()) as db:assert not db.scalars(select(CloudCall)).all() and len(db.scalars(select(GeoPair)).all())==1


def test_pair_cancel_retry_scope_and_evidence_damage_block_acceptance(client,pairs):
    a=ready(client,pairs/'rgb.tif','A.tif');b=ready(client,pairs/'after.tif','B.tif')
    assert client.post('/api/geo/pairs',json=body(a,a)).status_code==422
    assert client.post('/api/geo/pairs',json=body(a,b,rgb_comparability_confirmed=False)).status_code==422
    r=client.post('/api/geo/pairs',json=body(a,b)).json();path='/api/geo/pairs/'+r['id']
    r=client.post(path+'/cancel',json={'version':r['version']}).json();assert process_once(r['job_id'],deliver=False)
    assert client.get(path).json()['status']=='cancelled'
    r=client.post(path+'/retry',json={'version':r['version']}).json();assert process_once(r['job_id'],deliver=False)
    r=client.get(path).json();assert r['status']=='succeeded'
    assert client.get(path+'/export?workspace_id=not-owned').status_code==403
    with Session(engine()) as db:
        m=db.scalar(select(Membership));w=Workspace(name='隔离工作区');db.add(w);db.flush();db.add(Membership(user_id=m.user_id,workspace_id=w.id,role='admin'));db.commit();other=w.id
    old=client.headers['X-Workspace-ID'];client.headers['X-Workspace-ID']=other
    assert client.get(path).status_code==404
    assert client.get('/api/geo/pairs/requests/'+r['request_id']).status_code==404
    assert client.get('/api/geo/pairs').json()==[]
    client.headers['X-Workspace-ID']=old
    with Session(engine()) as db:
        ev=db.get(Evidence,r['result']['artifacts']['candidate']['evidence_id']);(settings().storage_dir/ev.storage_key).write_bytes(b'damaged')
    assert client.get(path+'/artifacts/candidate').status_code==409
    assert client.get(path+'/export').status_code==409
    assert client.put(path+'/review',json={'version':r['version'],'note':'不应保存','decision':'keep'}).status_code==409


def test_pair_invalid_grid_and_no_common_valid_pixels_fail_without_success(client,pairs,tmp_path):
    a=ready(client,pairs/'rgb.tif','A.tif');b=ready(client,pairs/'shifted.tif','B.tif')
    response=client.post('/api/geo/pairs',json=body(a,b));assert response.status_code==422
    response=client.post('/api/geo/pairs',json=body(a,b,alignment='reproject_after_to_before',roi=[0,0,1,1]));assert response.status_code==201
    r=response.json();assert process_once(r['job_id'],deliver=False)
    r=client.get('/api/geo/pairs/'+r['id']).json();assert r['status']=='failed' and r['result'] is None and '共同有效' in r['error']
