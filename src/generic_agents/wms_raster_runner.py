"""Local PNG-to-GeoTIFF derivation; never a remote GDAL source or raw band claim."""
import argparse,hashlib,json
from importlib.metadata import version
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from PIL import Image


def convert(source,request,out):
    for package in ('numpy','rasterio','pillow'):
        if version(package)!=request['environment'][package]:raise ValueError('本地地理依赖与固定版本不一致')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=request['png_sha256']:raise ValueError('地图响应摘要不一致')
    with Image.open(source) as image:
        if image.format!='PNG' or image.size!=(request['width'],request['height']):raise ValueError('地图响应格式或分辨率与请求不同')
        rgba=np.asarray(image.convert('RGBA'))
    valid=(rgba[:,:,3]>0).astype('uint8')*255
    crs='EPSG:4326' if request['crs']=='CRS:84' else request['crs']
    output=out/'rendered-map.tif'
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True,PROJ_NETWORK='OFF'):
        with rasterio.open(output,'w',driver='GTiff',width=request['width'],height=request['height'],count=3,dtype='uint8',
                           crs=crs,transform=from_bounds(*request['bbox'],request['width'],request['height']),compress='deflate') as dest:
            dest.write(np.moveaxis(rgba[:,:,:3],-1,0));dest.write_mask(valid)
            dest.update_tags(SOURCE_KIND='WMS_RENDERED_MAP',SOURCE_CAPTURE_TIME='UNKNOWN',RAW_SATELLITE_BANDS='NO',PNG_SHA256=request['png_sha256'])
        with rasterio.open(output) as check:
            if check.driver!='GTiff' or check.count!=3 or check.shape!=valid.shape or not np.array_equal(check.dataset_mask(),valid):
                raise ValueError('本地派生影像读回失败')
    return {'schema':'wms-rendered-raster-v1','width':request['width'],'height':request['height'],'bands':3,
            'valid_pixels':int((valid>0).sum()),'transparent_pixels':int((valid==0).sum()),
            'partial_alpha_pixels':int(((rgba[:,:,3]>0)&(rgba[:,:,3]<255)).sum()),
            'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'size_bytes':output.stat().st_size,
            'raw_satellite_bands':False,'source_capture_time_known':False,'cloud_calls':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--path',required=True);parser.add_argument('--request',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();value=json.loads(Path(args.request).read_text());print(json.dumps(convert(Path(args.path),value,Path(args.out)),allow_nan=False))
