"""Real offline SAM + synthetic tagged public RGB, not a remote-sensing accuracy claim."""
import base64,hashlib,io,json,subprocess
from pathlib import Path
from uuid import uuid4
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import SpecialistInstallation,CloudCall
from generic_agents.contracts import digest
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.local_specialists import SAM_RECIPE,weights_dir
from generic_agents.geo_inference import clip_invalid
from generic_agents.worker import process_once
from tests.test_geo_scenes import upload
from tests.test_geo_codec import scenes,pytestmark


def test_nodata_cut_removes_empty_candidates_and_preserves_original_roi_and_merge_provenance():
    im=Image.new('RGBA',(4,4),(39,104,240,160));buf=io.BytesIO();im.save(buf,format='PNG')
    mask={'candidate_index':1,'bbox':[0,0,4,4],'area_pixels':16,'png_base64':base64.b64encode(buf.getvalue()).decode(),'merge_evidence':[{'iou':.8}],'source_tiles':[0]}
    valid=Image.new('L',(4,4),255)
    for y in range(4):valid.putpixel((0,y),0)
    v=io.BytesIO();valid.save(v,format='PNG');r=clip_invalid({'width':4,'height':4,'masks':[mask]},v.getvalue(),[10,20,14,24]);m=r['masks'][0]
    assert m['bbox']==[1,0,4,4] and m['bbox_original']==[11,20,14,24] and m['area_pixels']==12 and m['nodata_removed_pixels']==4
    assert m['merge_evidence']==[] and m['rgb_merge_evidence']==[{'iou':.8}]
    valid=Image.new('L',(4,4),0);v=io.BytesIO();valid.save(v,format='PNG');r=clip_invalid({'width':4,'height':4,'masks':[mask]},v.getvalue(),[10,20,14,24]);assert not r['masks'] and r['nodata_removed_candidates']==1


def test_unavailable_map_reference_cannot_be_confirmed_and_bool_band_is_rejected(client,scenes):
    row=upload(client,scenes/'unknown.tif').json();assert process_once(row['job_id'],deliver=False)
    row=client.get('/api/geo/scenes/'+row['id']).json();url='/api/geo/scenes/'+row['id']+'/configuration'
    assert client.put(url,json={'version':row['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True}).status_code==422
    assert client.put(url,json={'version':row['version'],'rgb_bands':[True,2,3]}).status_code==422


@pytest.mark.skipif(not (weights_dir(SAM_RECIPE)/'model.safetensors').is_file() or not Path('.runtime/rtdetr-bootstrap/coco-cats.jpg').is_file(),reason='actual SAM weights/public fixture unavailable; local geo model release blocked')
def test_real_local_sam_native_geometry_review_export_and_source_integrity(client,account,tmp_path,monkeypatch):
    source=Path('.runtime/rtdetr-bootstrap/coco-cats.jpg').resolve();scene_path=tmp_path/'public-synthetic.tif'
    script=tmp_path/'fixture.py';script.write_text('''import sys,numpy as np,rasterio
from PIL import Image
from affine import Affine
from rasterio.enums import ColorInterp
im=np.asarray(Image.open(sys.argv[1]).convert('RGB'));valid=np.full(im.shape[:2],255,dtype='uint8');valid[:20,:20]=0
with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
 with rasterio.open(sys.argv[2],'w',driver='GTiff',width=im.shape[1],height=im.shape[0],count=3,dtype='uint8',crs='EPSG:32650',transform=Affine(2,.5,500000,.25,-3,2600000)) as dst:
  dst.write(im.transpose(2,0,1));dst.colorinterp=(ColorInterp.red,ColorInterp.green,ColorInterp.blue);dst.write_mask(valid)
''')
    r=subprocess.run([str(settings().geo_python),str(script),str(source),str(scene_path)],capture_output=True,text=True,timeout=30);assert r.returncode==0,r.stderr[-500:]
    row=upload(client,scene_path,name='公开照片合成坐标（不是遥感实测）.tif').json();assert process_once(row['job_id'],deliver=False);row=client.get('/api/geo/scenes/'+row['id']).json();url='/api/geo/scenes/'+row['id']
    r=client.put(url+'/configuration',json={'version':row['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True});assert r.status_code==200,r.text;assert process_once(r.json()['job_id'],deliver=False);row=client.get(url).json()
    with Session(engine()) as db:
        installation=SpecialistInstallation(workspace_id=account[2],pack_id='sam2-1-tiny',status='ready',data={'recipe_hash':digest(SAM_RECIPE),'generation':1,'backend':'cpu'});db.add(installation);db.commit();identity=installation.id
    body={'version':row['version'],'installation_id':identity,'request_id':str(uuid4()),'roi':[0,0,640,480]}
    response=client.post(url+'/runs',json=body);assert response.status_code==201,response.text;run=response.json()
    # Cancel after actual SAM masks are checkpointed; retry must reuse saved inference.
    import generic_agents.geo_inference as inference
    native=inference.call;cancelled=[]
    def stop_after_masks(mode,*args,**kwargs):
        if mode=='geometry' and not cancelled:
            current=client.get('/api/geo/runs/'+run['id']).json();assert current['mask_checkpoint']['masks']
            response=client.post('/api/geo/runs/'+run['id']+'/cancel',json={'version':current['version']});assert response.status_code==200;cancelled.append(True)
        return native(mode,*args,**kwargs)
    monkeypatch.setattr(inference,'call',stop_after_masks);assert process_once(run['job_id'],deliver=False)
    stopped=client.get('/api/geo/runs/'+run['id']).json();assert stopped['status']=='cancelled' and stopped['mask_checkpoint']['masks']
    resumed=client.post('/api/geo/runs/'+run['id']+'/retry',json={'version':stopped['version']});assert resumed.status_code==200,resumed.text
    monkeypatch.setattr(inference,'execute',lambda *a,**k: (_ for _ in ()).throw(AssertionError('saved model checkpoint must be reused')))
    assert process_once(resumed.json()['job_id'],deliver=False)
    saved=client.get('/api/geo/runs/'+run['id']).json();assert saved['status']=='succeeded',saved['error'];result=saved['result']
    assert result['masks'] and result['cloud_calls']==0 and result['geometry_failures']==0 and result['candidate_only'] and not result['entity_state_updated']
    assert result['source_raster']['sha256']==hashlib.sha256(scene_path.read_bytes()).hexdigest()
    assert result['width']==640 and result['height']==480 and saved['rgb_checkpoint']['valid_pixels']==640*480-400
    assert client.post(url+'/runs',json=body).json()['id']==run['id']
    assert client.post(url+'/runs',json={**body,'roi':[1,1,639,479]}).status_code==409
    path='/api/geo/runs/'+run['id'];rgb=client.get(path+'/rgb');assert hashlib.sha256(rgb.content).hexdigest()==result['image_sha256']
    for mask in result['masks']:
        raw=client.get(path+'/masks/'+mask['mask_evidence_id']);assert raw.status_code==200
        assert hashlib.sha256(raw.content).hexdigest()==mask['mask_sha256']
        with Image.open(io.BytesIO(raw.content)) as im:
            assert sum(im.getchannel('A').histogram()[1:])==mask['area_pixels']
        assert mask['geometry_native']['type']=='MultiPolygon' and mask['area_method']=='ellipsoidal_geodesic' and mask['area_m2']>0
        assert abs(mask['area_m2']/mask['area_pixels']-6.125)<.1
    review={'version':saved['version'],'note':'公开工程样例核对；合成坐标只验证机制，不证明遥感地面精度。','decisions':{'1':'uncertain'}}
    r=client.put(path+'/review',json=review);assert r.status_code==200,r.text;assert r.json()['review']['note']==review['note'];assert client.put(path+'/review',json=review).status_code==409
    export=client.get(path+'/native-geojson');assert export.status_code==200 and export.json()['review']['decisions']=={'1':'uncertain'};assert 'NOT RFC7946' in export.json()['format_notice']
    with Session(engine()) as db:
        assert not db.scalars(select(CloudCall)).all()
        from generic_agents.models import Evidence
        mask_ev=db.get(Evidence,result['masks'][0]['mask_evidence_id']);mask_file=settings().storage_dir/mask_ev.storage_key;before=mask_file.read_bytes();mask_file.write_bytes(b'corrupt')
    assert client.get(path+'/native-geojson').status_code==409
    assert client.put(path+'/review',json={**review,'version':r.json()['version']}).status_code==409
    mask_file.write_bytes(before)
    with Session(engine()) as db:
        original=db.get(Evidence,row['evidence_id']);(settings().storage_dir/original.storage_key).write_bytes(b'corrupt')
    assert client.get(path+'/rgb').status_code==409 and client.get(path+'/native-geojson').status_code==409
