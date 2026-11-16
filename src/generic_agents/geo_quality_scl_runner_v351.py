"""Offline Sentinel-2 SCL quality adapter for four local, frozen GeoTIFFs.

The caller verifies custody and explicit user association separately. Source
classification is uncertain and this adapter does not verify scene identity.
"""
import argparse
import json
import math
import os
from pathlib import Path

os.environ.update(PROJ_NETWORK='OFF', GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', GDAL_PAM_ENABLED='NO')
ALLOWED = (4, 5, 6)
BUFFER_PIXELS = 6
MAX_PIXELS = 16_000_000
MAX_BLOCK_BYTES = 128 * 1024 * 1024


def need(value, message):
    if not value:
        raise ValueError(message)


def block_safe(dataset, np):
    need(dataset.driver == 'GTiff' and 0 < dataset.width * dataset.height <= MAX_PIXELS,
         '原影像不是受支持的本地GeoTIFF，或超过本地处理范围')
    need(bool(dataset.crs) and not dataset.gcps[0] and not dataset.rpcs,
         '质量影像需要可核对的坐标系和仿射变换')
    need(max(h*w for h,w in dataset.block_shapes) * sum(np.dtype(d).itemsize for d in dataset.dtypes) <= MAX_BLOCK_BYTES,
         '影像压缩块超过本地解码上限')
    transform = dataset.transform
    need(all(math.isfinite(v) for v in list(transform)[:6]) and transform.a > 0 and transform.e < 0
         and abs(transform.b) < 1e-10 and abs(transform.d) < 1e-10,
         '当前质量适配器仅支持北向、无旋转的仿射网格')


def dilate_bad(bad, pixels, np):
    # Square buffer is deliberately conservative; it is not a calibrated
    # estimate of the neural model's receptive field or residual cloud risk.
    height, width = bad.shape
    padded = np.pad(bad, pixels, mode='constant', constant_values=True)
    horizontal = np.zeros((height + 2*pixels, width), dtype=bool)
    for dx in range(2*pixels + 1):
        horizontal |= padded[:, dx:dx+width]
    output = np.zeros((height, width), dtype=bool)
    for dy in range(2*pixels + 1):
        output |= horizontal[dy:dy+height, :]
    return output


def build(image_paths, quality_paths, output):
    import numpy as np
    import rasterio
    from rasterio.warp import reproject
    from rasterio.enums import Resampling
    from PIL import Image
    output = Path(output)
    need(output.is_dir() and not any(output.iterdir()), '质量输出目录必须为空')
    sources = []
    with rasterio.Env(GDAL_SKIP='VRT', GDAL_CACHEMAX=64*1024*1024, GDAL_TIFF_INTERNAL_MASK=True):
        for image_path, quality_path, side in zip(image_paths, quality_paths, ('before','after')):
            need(Path(image_path).is_file() and Path(quality_path).is_file(), '质量处理只接受保存的本地原件')
            with rasterio.open(image_path, driver='GTiff', sharing=False) as image, rasterio.open(quality_path, driver='GTiff', sharing=False) as quality:
                block_safe(image, np); block_safe(quality, np)
                need(image.count == 3 and image.dtypes == ('uint8','uint8','uint8'),
                     '当前适配器需要三波段8位地图参考影像；原件保留')
                need(quality.count == 1 and quality.dtypes == ('uint8',), 'SCL质量层必须为单波段8位GeoTIFF')
                need(image.crs == quality.crs and image.crs.is_projected, '影像与SCL需要相同的投影坐标系')
                epsg=image.crs.to_epsg();need(type(epsg) is int and (32601<=epsg<=32660 or 32701<=epsg<=32760),
                    '当前SCL适配器需要以米为单位的UTM投影，不能猜测单位')
                a,q=image.transform,quality.transform
                need(abs(a.a-10)<1e-8 and abs(a.e+10)<1e-8 and abs(q.a-20)<1e-8 and abs(q.e+20)<1e-8,
                     '当前SCL适配器需要10米影像与20米质量层')
                # Pixel edges, not just dimensions, must be on the same 10m grid.
                need(abs((q.c-a.c)/10-round((q.c-a.c)/10))<1e-8 and
                     abs((q.f-a.f)/10-round((q.f-a.f)/10))<1e-8,
                     '影像和SCL像素网格未对齐，不能猜测配准')
                image_values=image.read();image_valid=np.all(image.read_masks()>0,axis=0) & (image.dataset_mask()>0)
                classes=quality.read(1);quality_valid=(quality.read_masks(1)>0)&(quality.dataset_mask()>0)
                need(bool(np.isin(classes[quality_valid],np.arange(12,dtype='uint8')).all()),
                     'SCL质量层存在不支持的类别；原件保留')
                prepared=np.where(quality_valid,classes,255).astype('uint8')
                aligned=np.full((image.height,image.width),255,dtype='uint8')
                reproject(prepared,aligned,src_transform=q,src_crs=quality.crs,dst_transform=a,
                    dst_crs=image.crs,src_nodata=255,dst_nodata=255,resampling=Resampling.nearest,
                    init_dest_nodata=True,num_threads=1,warp_mem_limit=64)
                raw_eligible=image_valid & np.isin(aligned,ALLOWED)
                invalid=dilate_bad(~raw_eligible,BUFFER_PIXELS,np)
                valid=~invalid
                target=output/(side+'.tif')
                profile={'driver':'GTiff','width':image.width,'height':image.height,'count':3,'dtype':'uint8',
                    'crs':image.crs,'transform':a,'compress':'deflate','tiled':True,'blockxsize':256,'blockysize':256,'photometric':'RGB'}
                with rasterio.open(target,'w',**profile) as dst:
                    dst.write(image_values);dst.write_mask(valid.astype('uint8')*255)
                    dst.update_tags(QUALITY_DERIVATION='user-declared Sentinel-2 SCL; 20m classification nearest to 10m',
                        QUALITY_LIMIT='classification and association unverified; 6-pixel conservative buffer not calibrated')
                mask=output/(side+'-valid.png');Image.fromarray(valid.astype('uint8')*255).save(mask,format='PNG')
                counts={str(int(k)):int(v) for k,v in zip(*np.unique(aligned,return_counts=True))}
                sources.append({'side':side,'width':image.width,'height':image.height,'crs':image.crs.to_string(),
                    'transform':list(a)[:6], 'quality_transform':list(q)[:6],
                    'quality_native_resolution_m':20,'target_resolution_m':10,'resampling':'nearest',
                    'class_counts':counts,'image_invalid_pixels':int((~image_valid).sum()),
                    'raw_eligible_pixels':int(raw_eligible.sum()),'valid_pixels':int(valid.sum()),
                    'buffer_excluded_pixels':int((raw_eligible & invalid).sum()),
                    'unknown_or_excluded_pixels':int((~valid).sum())})
        need(sources[0]['width']==sources[1]['width'] and sources[0]['height']==sources[1]['height']
             and sources[0]['crs']==sources[1]['crs'] and sources[0]['transform']==sources[1]['transform'],
             '两期影像网格不同；请先明确配准，不能只凭尺寸相同比较')
        with Image.open(output/'before-valid.png') as a, Image.open(output/'after-valid.png') as b:
            common=(np.asarray(a)>0)&(np.asarray(b)>0)
        Image.fromarray(common.astype('uint8')*255).save(output/'common-valid.png',format='PNG')
        result={'schema':'sentinel-scl-quality-result-v351','sides':sources,
            'total_pixels':int(common.size),'common_valid_pixels':int(common.sum()),
            'unknown_or_excluded_pixels':int((~common).sum()),'buffer_pixels':BUFFER_PIXELS,
            'candidate_only':True,'source_association':'user_declared_unverified',
            'classification_accuracy':'not_verified','buffer_effectiveness':'not_verified',
            'cloud_calls':0,'object_state_updated':False,
            'files':['before.tif','after.tif','before-valid.png','after-valid.png','common-valid.png']}
        return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--before',required=True);parser.add_argument('--after',required=True)
    parser.add_argument('--before-quality',required=True);parser.add_argument('--after-quality',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();value=build([args.before,args.after],[args.before_quality,args.after_quality],args.out)
    print(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')))

if __name__=='__main__':main()
