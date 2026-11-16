"""Local explicit alignment and RGB difference baseline, not a learned change detector."""
import argparse,json,math
from pathlib import Path
from importlib.metadata import version
from geo_runner import inspect,render,open_scene


def compare(before,after,request,out,base,recipe):
    import numpy as np,rasterio
    from affine import Affine
    from PIL import Image
    from rasterio.warp import reproject,transform_bounds
    from rasterio.enums import Resampling
    a_meta=inspect(before,base);b_meta=inspect(after,base)
    if any(m['georeference_status']!='affine_available' for m in (a_meta,b_meta)):
        raise ValueError('两幅影像都需有受支持的坐标系与仿射变换；不猜测地理位置')
    roi=request['roi'];threshold=request['threshold'];alignment=request['alignment']
    if len(roi)!=4 or any(type(v) is not int for v in roi) or not 0<=roi[0]<roi[2]<=a_meta['width'] or not 0<=roi[1]<roi[3]<=a_meta['height']:
        raise ValueError('比较范围超出A影像原始像素边界')
    w,h=roi[2]-roi[0],roi[3]-roi[1]
    if w*h>recipe['max_comparison_pixels']:raise ValueError('比较窗口超过1600万像素；请明确缩小范围')
    if type(threshold) is not int or not 1<=threshold<=255:raise ValueError('像素差异阈值应为1至255的整数')
    if alignment not in {'same_grid','reproject_after_to_before'}:raise ValueError('请明确选择影像对齐方式')
    out=Path(out);(out/'a').mkdir();(out/'b').mkdir()
    with rasterio.Env(GDAL_SKIP='VRT',GDAL_CACHEMAX=64*1024*1024):
        with open_scene(before) as a,open_scene(after) as b:
            target=a.transform*Affine.translation(roi[0],roi[1])
            same=a.crs==b.crs and a.transform==b.transform and a.width==b.width and a.height==b.height
            if alignment=='same_grid' and not same:raise ValueError('影像坐标系、完整网格或尺寸不同；请明确选择重投影，不能静默对齐')
            if same:b_roi=roi
            else:
                corners=[target*(x,y) for x,y in ((0,0),(w,0),(w,h),(0,h))]
                bounds=transform_bounds(a.crs,b.crs,min(x for x,y in corners),min(y for x,y in corners),max(x for x,y in corners),max(y for x,y in corners),densify_pts=21)
                if not all(math.isfinite(v) for v in bounds):raise ValueError('坐标转换范围无效，原件保留')
                inv=~b.transform;points=[inv*(x,y) for x in (bounds[0],bounds[2]) for y in (bounds[1],bounds[3])]
                b_roi=[max(0,math.floor(min(x for x,y in points))-1),max(0,math.floor(min(y for x,y in points))-1),min(b.width,math.ceil(max(x for x,y in points))+1),min(b.height,math.ceil(max(y for x,y in points))+1)]
            if b_roi[0]>=b_roi[2] or b_roi[1]>=b_roi[3]:raise ValueError('两幅影像在所选范围没有可比较的覆盖')
            if (b_roi[2]-b_roi[0])*(b_roi[3]-b_roi[1])>recipe['max_source_window_pixels']:
                raise ValueError('B影像所需读取窗口超过1600万像素；请缩小A比较范围，不自动降采样')
            render(before,{**request['before_configuration'],'window':roi,'analysis':True},out/'a',base)
            render(after,{**request['after_configuration'],'window':b_roi,'analysis':True},out/'b',base)
            with Image.open(out/'a'/'image.png') as im:a_rgb=np.array(im)
            with Image.open(out/'a'/'valid.png') as im:a_valid=np.array(im)>0
            with Image.open(out/'b'/'image.png') as im:b_rgb=np.array(im)
            with Image.open(out/'b'/'valid.png') as im:b_valid=np.array(im)>0
            if same:aligned=b_rgb;aligned_valid=b_valid
            else:
                source=b.transform*Affine.translation(b_roi[0],b_roi[1]);aligned=np.zeros((h,w,3),dtype='uint8');aligned_valid=np.zeros((h,w),dtype='uint8')
                for channel in range(3):
                    reproject(b_rgb[:,:,channel],aligned[:,:,channel],src_transform=source,src_crs=b.crs,dst_transform=target,dst_crs=a.crs,resampling=Resampling.nearest,num_threads=1,warp_mem_limit=64)
                reproject(b_valid.astype('uint8'),aligned_valid,src_transform=source,src_crs=b.crs,dst_transform=target,dst_crs=a.crs,resampling=Resampling.nearest,num_threads=1,warp_mem_limit=64)
                aligned_valid=aligned_valid>0
            common=a_valid&aligned_valid;count=int(common.sum())
            if not count:raise ValueError('两幅影像的共同有效像素为零；不能生成无变化结论')
            diff=np.max(np.abs(a_rgb.astype('int16')-aligned.astype('int16')),axis=2).astype('uint8');diff[~common]=0
            candidate=common&(diff>=threshold)
            Image.fromarray(a_rgb).save(out/'before.png');Image.fromarray(aligned).save(out/'after.png')
            Image.fromarray((common*255).astype('uint8')).save(out/'valid.png');Image.fromarray(diff).save(out/'difference.png')
            overlay=np.zeros((h,w,4),dtype='uint8');overlay[candidate]=[240,100,39,190];Image.fromarray(overlay).save(out/'candidate.png')
    return {'schema':'local-rgb-comparison-v1','algorithm':'rgb_max_channel_absolute_difference','learned_model':False,'cloud_calls':0,
        'threshold':threshold,'alignment':alignment,'resampling':'none' if same else 'nearest','registration_accuracy':'not_verified','radiometry':'user_declared_rgb_ranges',
        'width':w,'height':h,'roi':roi,'after_read_window':b_roi,'transform':list(target)[:6],'crs':a_meta['crs'],'crs_wkt':a_meta['crs_wkt'],
        'total_pixels':w*h,'common_valid_pixels':count,'unknown_pixels':w*h-count,'candidate_pixels':int(candidate.sum()),
        'semantics':'pixel_difference_candidates_only','domain_quality':'not_verified','cloud_shadow_screening':'not_applied','candidate_only':True}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--before',required=True);parser.add_argument('--after',required=True);parser.add_argument('--request',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2]
    base=json.loads((root/'runtime-packs/geo-rgb-v1.json').read_text());recipe=json.loads((root/'runtime-packs/geo-pair-v1.json').read_text())
    for name,pin in base['environment'].items():
        if version(name)!=pin:raise ValueError('本地地理处理依赖与固定配方不一致')
    print(json.dumps(compare(args.before,args.after,json.loads(Path(args.request).read_text()),args.out,base,recipe),allow_nan=False))


if __name__=='__main__':main()
