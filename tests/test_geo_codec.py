"""Actual isolated GDAL/PROJ runtime contracts; fixtures declare synthetic spatial tags."""
import json,subprocess,io
from pathlib import Path
import pytest
from PIL import Image
from generic_agents.geo_codec import call,RECIPE,file_digest
from generic_agents.config import settings

pytestmark=pytest.mark.skipif(not settings().geo_python.is_file(),reason='configured isolated geo runtime not installed; geospatial release remains blocked')

@pytest.fixture
def scenes(tmp_path):
    script=tmp_path/'create.py'
    script.write_text('''import sys,numpy as np,rasterio
from pathlib import Path
from affine import Affine
from rasterio.enums import ColorInterp
from rasterio.control import GroundControlPoint
root=Path(sys.argv[1]);data=np.full((3,8,10),100,dtype='uint8');data[:,:2,:2]=0
for name,dtype,crs in [('rgb','uint8','EPSG:32650'),('high','uint16','EPSG:32650'),('unknown','uint8',None)]:
 with rasterio.open(root/(name+'.tif'),'w',driver='GTiff',width=10,height=8,count=3,dtype=dtype,crs=crs,transform=Affine(2,.5,500000,.25,-3,2600000),nodata=0) as dst:
  dst.write(data.astype(dtype));dst.colorinterp=(ColorInterp.red,ColorInterp.green,ColorInterp.blue)
for name,crs,transform in [('feet','EPSG:2277',Affine(2,.5,3000000,.25,-3,10000000)),('degrees','EPSG:4326',Affine(.00001,.000003,117,.000002,-.00001,24))]:
 with rasterio.open(root/(name+'.tif'),'w',driver='GTiff',width=10,height=8,count=3,dtype='uint8',crs=crs,transform=transform) as dst:
  dst.write(data);dst.colorinterp=(ColorInterp.red,ColorInterp.green,ColorInterp.blue)
with rasterio.open(root/'gcp.tif','w',driver='GTiff',width=10,height=8,count=3,dtype='uint8') as dst:
 dst.write(data);dst.gcps=([GroundControlPoint(row=0,col=0,x=500000,y=2600000),GroundControlPoint(row=0,col=10,x=500020,y=2600000),GroundControlPoint(row=8,col=0,x=500000,y=2599976)],rasterio.crs.CRS.from_epsg(32650))
''')
    result=subprocess.run([str(settings().geo_python),str(script),str(tmp_path)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr[-1000:]
    return tmp_path


def test_real_metadata_preserves_rotation_nodata_and_unknown_time(scenes):
    meta=call('inspect',scenes/'rgb.tif')
    assert meta['crs']=='EPSG:32650' and meta['georeference_status']=='affine_available'
    assert meta['transform']==[2,.5,500000,.25,-3,2600000]
    assert meta['suggested_rgb_bands']==[1,2,3] and meta['nodata']==[0,0,0]
    assert meta['observed_at'] is None and meta['ground_accuracy']=='not_verified' and meta['cloud_calls']==0


def test_real_window_mask_and_explicit_radiometry(scenes,tmp_path):
    out=tmp_path/'render';out.mkdir()
    r=call('render',scenes/'rgb.tif',{'rgb_bands':[1,2,3],'window':[0,0,10,8]},out)
    assert r['valid_pixels']==76 and r['sampled_pixels']==80 and r['statistics_scope']=='window_pixels'
    with Image.open(out/'image.png') as im:assert im.size==(10,8) and im.getpixel((0,0))==(0,0,0) and im.getpixel((4,4))==(100,100,100)
    with pytest.raises(ValueError,match='高位深'):call('render',scenes/'high.tif',{'rgb_bands':[1,2,3]},out)
    r=call('render',scenes/'high.tif',{'rgb_bands':[1,2,3],'ranges':[[0,200]]*3},out)
    with Image.open(out/'image.png') as im:assert im.getpixel((4,4))==(128,128,128)
    with pytest.raises(ValueError,match='量化范围'):call('render',scenes/'high.tif',{'rgb_bands':[1,2,3],'ranges':[[1,1]]*3},out)


def test_holes_and_rotation_produce_geodesic_square_metres(scenes,tmp_path):
    meta=call('inspect',scenes/'rgb.tif');meta['map_image_confirmed']=True
    mask=Image.new('RGBA',(10,8),(39,104,240,160))
    for x in range(3,7):
        for y in range(2,6):mask.putpixel((x,y),(0,0,0,0))
    path=tmp_path/'mask.png';mask.save(path)
    r=call('geometry',path,{'metadata':meta,'bbox':[0,0,10,8]})
    assert r['pixel_area']==64 and r['area_method']=='ellipsoidal_geodesic' and abs(r['area_m2']-64*abs(2*(-3)-.5*.25))<4
    assert len(r['geometry_native']['coordinates'][0])==2
    assert r['position_status']=='declared_map_affine' and r['ground_accuracy']=='not_verified'


def test_missing_or_unconfirmed_reference_never_fabricates_area(scenes,tmp_path):
    path=tmp_path/'mask.png';Image.new('RGBA',(10,8),(39,104,240,160)).save(path)
    for name in ['unknown','gcp']:
        meta=call('inspect',scenes/(name+'.tif'));meta['map_image_confirmed']=True
        assert meta['georeference_status'] in {'missing_crs','unsupported_gcps_or_rpc'}
        r=call('geometry',path,{'metadata':meta,'bbox':[0,0,10,8]});assert r['area_m2'] is None and r['geometry_native'] is None
    meta=call('inspect',scenes/'rgb.tif')
    assert call('geometry',path,{'metadata':meta,'bbox':[0,0,10,8]})['area_m2'] is None


def test_vrt_and_invalid_window_are_rejected_without_referencing_external_dataset(scenes,tmp_path):
    vrt=tmp_path/'not-a-tiff.tif';vrt.write_text('<VRTDataset rasterXSize="10" rasterYSize="8"><VRTRasterBand dataType="Byte" band="1"><SimpleSource><SourceFilename>'+str(scenes/'rgb.tif')+'</SourceFilename></SimpleSource></VRTRasterBand></VRTDataset>')
    with pytest.raises(ValueError,match='解码'):call('inspect',vrt)
    out=tmp_path/'render';out.mkdir()
    with pytest.raises(ValueError,match='窗口'):call('render',scenes/'rgb.tif',{'rgb_bands':[1,2,3],'window':[0,0,12,8]},out)
    with pytest.raises(ValueError,match='三个不同'):call('render',scenes/'rgb.tif',{'rgb_bands':[1,1,2]},out)


def test_codec_cancel_reaps_child_and_preserves_source(scenes):
    source=scenes/'rgb.tif';before=file_digest(source)
    def cancelled():raise ValueError('本次处理已取消')
    with pytest.raises(ValueError,match='已取消'):call('inspect',source,guard=cancelled)
    assert file_digest(source)==before


def test_fixed_runner_hash_is_checked_before_execution(scenes,monkeypatch):
    import generic_agents.geo_codec as codec
    monkeypatch.setitem(codec.RECIPE,'runner_sha256','0'*64)
    with pytest.raises(ValueError,match='固定配方'):call('inspect',scenes/'rgb.tif')


def test_area_handles_native_feet_and_degrees_without_treating_native_units_as_metres(scenes,tmp_path):
    path=tmp_path/'area.png';Image.new('RGBA',(10,8),(39,104,240,160)).save(path)
    values={}
    for name in ['feet','degrees']:
        meta=call('inspect',scenes/(name+'.tif'));meta['map_image_confirmed']=True
        value=call('geometry',path,{'metadata':meta,'bbox':[0,0,10,8]});assert value['pixel_area']==80 and value['area_method']=='ellipsoidal_geodesic' and value['geometry_native']
        values[name]=value['area_m2']
    assert 40<values['feet']<50  # roughly 80 * 6.125 US-survey-ft², projection factor retained
    assert 80<values['degrees']<120  # 24N small angular pixels, never 80 * tiny native determinant


def test_nonzero_analysis_roi_maps_to_original_pixel_offset(scenes,tmp_path):
    meta=call('inspect',scenes/'rgb.tif');meta['map_image_confirmed']=True
    path=tmp_path/'offset.png';Image.new('RGBA',(6,4),(39,104,240,160)).save(path)
    value=call('geometry',path,{'metadata':meta,'bbox':[2,3,8,7]})
    assert value['pixel_area']==24 and abs(value['area_m2']-147)<2
    ring=value['geometry_native']['coordinates'][0][0]
    assert [500005.5,2599991.5] in ring and [500019.5,2599981.0] in ring
