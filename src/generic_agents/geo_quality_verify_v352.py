"""Independent, offline verification of the quality mask and retained pixel data.

Runs only on pinned local evidence supplied by the receiver. Association and
classification accuracy remain unverified even after this integrity check.
"""
import argparse,json,sys
import numpy as np
import rasterio
from rasterio.warp import reproject,Resampling
from PIL import Image


def need(ok,message):
    if not ok:raise ValueError(message)


def verify_side(image_path,quality_path,derived_path,mask_path):
    with rasterio.open(image_path) as image,rasterio.open(quality_path) as quality,rasterio.open(derived_path) as derived:
        need(image.driver==quality.driver==derived.driver=='GTiff','不支持的影像格式')
        need(image.count==3 and quality.count==1 and derived.count==3 and all(x=='uint8' for x in image.dtypes+quality.dtypes+derived.dtypes),'影像波段或数据类型不一致')
        need(0<image.width*image.height<=16000000 and image.crs==quality.crs==derived.crs and image.crs is not None,'坐标系或影像尺寸不一致')
        a=image.transform;q=quality.transform
        need(a.b==a.d==q.b==q.d==0 and a.a==10 and a.e==-10 and q.a==20 and q.e==-20,'像元尺度不一致')
        need(abs((q.c-a.c)/10-round((q.c-a.c)/10))<1e-8 and abs((q.f-a.f)/10-round((q.f-a.f)/10))<1e-8,'质量层未对齐')
        need(derived.width==image.width and derived.height==image.height and derived.transform==a,'派生影像网格不一致')
        source=image.read();image_valid=np.all(image.read_masks()>0,axis=0)&(image.dataset_mask()>0)
        quality_pixels=quality.read(1);qvalid=(quality.read_masks(1)>0)&(quality.dataset_mask()>0)
        need(bool(np.isin(quality_pixels[qvalid],np.arange(12,dtype='uint8')).all()),'质量分类不受支持')
        quality_pixels=np.where(qvalid,quality_pixels,255).astype('uint8');aligned=np.full((image.height,image.width),255,dtype='uint8')
        reproject(quality_pixels,aligned,src_transform=q,src_crs=quality.crs,dst_transform=a,dst_crs=image.crs,src_nodata=255,dst_nodata=255,resampling=Resampling.nearest,init_dest_nodata=True,num_threads=1,warp_mem_limit=64)
        eligible=image_valid & np.isin(aligned,[4,5,6]);height,width=eligible.shape
        # Any excluded neighbor within 6 pixels, including beyond image edges,
        # invalidates the output. This verifies the stated policy, not its adequacy.
        padded=np.pad(~eligible,6,constant_values=True)
        horizontal=np.zeros((height+12,width),dtype=bool)
        for dx in range(13):horizontal|=padded[:,dx:dx+width]
        bad=np.zeros_like(eligible)
        for dy in range(13):bad|=horizontal[dy:dy+height,:]
        valid=~bad
        need(np.array_equal(derived.read(),source),'派生影像像元内容不一致')
        need(np.array_equal(derived.dataset_mask()>0,valid),'派生影像有效区不一致')
        with Image.open(mask_path) as im:
            need(im.format=='PNG' and im.mode=='L' and im.size==(width,height),'有效区掩膜格式不一致')
            mask=np.asarray(im);need(np.isin(mask,[0,255]).all() and np.array_equal(mask>0,valid),'有效区掩膜内容不一致')
        classes,counts=np.unique(aligned,return_counts=True)
        stats={'class_counts':{str(int(k)):int(v) for k,v in zip(classes,counts)},
            'image_invalid_pixels':int((~image_valid).sum()),'raw_eligible_pixels':int(eligible.sum()),
            'valid_pixels':int(valid.sum()),'buffer_excluded_pixels':int((eligible & bad).sum()),
            'unknown_or_excluded_pixels':int((~valid).sum())}
        return valid,{'width':width,'height':height,'transform':list(a)[:6],'crs':image.crs.to_string()},stats


def main():
    p=argparse.ArgumentParser()
    for name in ('before','after','before-quality','after-quality','before-derived','after-derived','before-mask','after-mask','common-mask'):p.add_argument('--'+name,required=True)
    a=p.parse_args();before,ga,sa=verify_side(a.before,a.before_quality,a.before_derived,a.before_mask);after,gb,sb=verify_side(a.after,a.after_quality,a.after_derived,a.after_mask)
    need(ga==gb,'两期影像网格不一致');common=before&after
    with Image.open(a.common_mask) as im:
        need(im.format=='PNG' and im.mode=='L' and im.size==(ga['width'],ga['height']),'共同有效区格式不一致')
        values=np.asarray(im);need(np.isin(values,[0,255]).all() and np.array_equal(values>0,common),'共同有效区与两期掩膜不一致')
    print(json.dumps({'schema':'quality-independent-check-v352','total_pixels':int(common.size),'common_valid_pixels':int(common.sum()),'unknown_or_excluded_pixels':int((~common).sum()),'sides':[sa,sb],'classification_accuracy':'not_verified','source_association':'user_declared_unverified','buffer_effectiveness':'not_verified','cloud_calls':0},separators=(',',':')))

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,rasterio.errors.RasterioError) as exc:
        print('ValueError: '+str(exc),file=sys.stderr);sys.exit(1)
