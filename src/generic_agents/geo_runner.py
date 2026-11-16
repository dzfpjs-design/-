"""Local GTiff window/geometry codec; executed only in the locked geo environment."""
import argparse,json,math,os
from pathlib import Path
from importlib.metadata import version
os.environ.update(PROJ_NETWORK='OFF',GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',GDAL_PAM_ENABLED='NO')


def finite(value):
    return value if value is None or math.isfinite(value) else None


def open_scene(path):
    import rasterio
    # Reject VRT/GCP/RPC fallback and remote drivers before any dataset read.
    if not Path(path).is_file():raise ValueError('影像原件不是本地文件')
    return rasterio.open(path,driver='GTiff',sharing=False)


def spatial(src):
    from affine import Affine
    from pyproj import CRS
    transform=src.transform
    affine_ok=all(math.isfinite(v) for v in list(transform)[:6]) and abs(transform.a*transform.e-transform.b*transform.d)>1e-20
    gcps,gcp_crs=src.gcps
    unsupported=bool(gcps) or bool(src.rpcs)
    status='unsupported_gcps_or_rpc' if unsupported else 'missing_crs' if src.crs is None else 'missing_transform' if not affine_ok or transform==Affine.identity() else 'affine_available'
    return {'crs':src.crs.to_string() if src.crs else None,'crs_wkt':src.crs.to_wkt() if src.crs else None,
        'transform':list(transform)[:6],'axis_units':[a.unit_name for a in CRS.from_wkt(src.crs.to_wkt()).axis_info] if src.crs else None,'georeference_status':status,'ground_accuracy':'not_verified',
        'pixel_scale_native':[math.hypot(transform.a,transform.d),math.hypot(transform.b,transform.e)],
        'footprint_native':{'type':'Polygon','coordinates':[[list(transform*(x,y)) for x,y in [(0,0),(src.width,0),(src.width,src.height),(0,src.height),(0,0)]]]} if status=='affine_available' else None}


def inspect(path,recipe):
    import rasterio,numpy as np
    with rasterio.Env(GDAL_SKIP='VRT',GDAL_CACHEMAX=64*1024*1024):
        with open_scene(path) as src:
            if src.driver!='GTiff' or not 0<src.width*src.height<=recipe['max_pixels'] or not 1<=src.count<=recipe['max_bands']:raise ValueError('GeoTIFF尺寸或波段数量超过本地配额')
            if any(np.dtype(t).kind=='c' for t in src.dtypes):raise ValueError('复数波段尚未支持RGB处理，原件保留')
            if max(h*w for h,w in src.block_shapes)*sum(np.dtype(t).itemsize for t in src.dtypes)>recipe['max_block_bytes']:raise ValueError('影像压缩块解码量超过本地配额，请提供合理分块的GeoTIFF；原件保留')
            colors=[x.name for x in src.colorinterp];rgb=[colors.index(c)+1 for c in ['red','green','blue']] if all(c in colors for c in ['red','green','blue']) else None
            return {'schema':'local-raster-v1','width':src.width,'height':src.height,'bands':src.count,'dtypes':list(src.dtypes),'color_interpretation':colors,
                'suggested_rgb_bands':rgb,'nodata':[finite(x) for x in src.nodatavals],
                'nodata_is_nan':[x is not None and not math.isfinite(x) for x in src.nodatavals],
                'block_shapes':[list(x) for x in src.block_shapes],'metadata_time_text':src.tags().get('TIFFTAG_DATETIME'),
                'observed_at':None,'cloud_calls':0,**spatial(src)}


def render(path,request,out,recipe):
    import numpy as np,rasterio
    from rasterio.windows import Window
    from rasterio.enums import Resampling
    from PIL import Image
    bands=request['rgb_bands'];ranges=request.get('ranges');box=request.get('window')
    with rasterio.Env(GDAL_SKIP='VRT',GDAL_CACHEMAX=64*1024*1024):
        with open_scene(path) as src:
            if len(bands)!=3 or len(set(bands))!=3 or any(not isinstance(v,int) or isinstance(v,bool) or not 1<=v<=src.count for v in bands):raise ValueError('请明确选择三个不同的RGB波段')
            if ranges is None and any(src.dtypes[b-1]!='uint8' for b in bands):raise ValueError('高位深或浮点影像需明确各RGB波段的数值范围，不能静默拉伸')
            if ranges is not None and (len(ranges)!=3 or any(len(r)!=2 or not all(math.isfinite(v) for v in r) or not r[0]<r[1] for r in ranges)):raise ValueError('RGB量化范围无效')
            box=box or [0,0,src.width,src.height]
            if len(box)!=4 or any(not isinstance(v,int) or isinstance(v,bool) for v in box) or not 0<=box[0]<box[2]<=src.width or not 0<=box[1]<box[3]<=src.height:raise ValueError('影像窗口超出原件范围')
            w,h=box[2]-box[0],box[3]-box[1]
            analysis=request.get('analysis',False)
            if analysis and request.get('preview',False):raise ValueError('推理输入不能同时声明为缩放预览')
            if analysis and w*h>recipe['max_inference_pixels']:raise ValueError('选定分析区域超过本地像素配额，请明确缩小范围；未自动缩放或截取')
            edge=max(w,h) if analysis else recipe['preview_edge'] if request.get('preview',False) else recipe['window_edge']
            if not analysis and not request.get('preview',False) and max(w,h)>edge:raise ValueError('本地窗口超过固定配方尺寸，请分窗读取')
            scale=min(1.,edge/max(w,h));ow,oh=max(1,round(w*scale)),max(1,round(h*scale))
            win=Window(box[0],box[1],w,h)
            values=src.read(bands,window=win,out_shape=(3,oh,ow),resampling=Resampling.nearest)
            valid=np.all(src.read_masks(bands,window=win,out_shape=(3,oh,ow),resampling=Resampling.nearest)>0,axis=0)
            valid &= src.dataset_mask(window=win,out_shape=(oh,ow),resampling=Resampling.nearest)>0
            valid &= np.all(np.isfinite(values),axis=0)
            rgb=np.zeros((oh,ow,3),dtype=np.uint8)
            for i in range(3):
                a,b=ranges[i] if ranges else [0,255];safe=np.where(valid,values[i],a)
                rgb[:,:,i]=np.rint(np.clip((safe.astype('float64')-a)/(b-a),0,1)*255).astype('uint8')
            rgb[~valid]=0
            Image.fromarray(rgb).save(Path(out)/'image.png',format='PNG');Image.fromarray(valid.astype('uint8')*255).save(Path(out)/'valid.png',format='PNG')
            return {'schema':'local-raster-window-v1','window':box,'width':ow,'height':oh,'source_width':w,'source_height':h,
                'preview':bool(request.get('preview',False)),'resampling':'nearest','rgb_bands':bands,'ranges':ranges,
                'valid_pixels':int(valid.sum()),'sampled_pixels':ow*oh,'statistics_scope':'preview_samples' if request.get('preview') else 'window_pixels',
                'window_transform':list(src.window_transform(win))[:6],'cloud_calls':0}


def geometry(metadata,mask_path,bbox,recipe):
    import numpy as np
    from PIL import Image
    from affine import Affine
    import rasterio.features
    from pyproj import CRS,Transformer,network
    network.set_network_enabled(False)
    if metadata['georeference_status']!='affine_available' or not metadata.get('map_image_confirmed'):
        return {'geometry_native':None,'area_m2':None,'area_method':None,'position_status':'not_confirmed_map_reference'}
    with Image.open(mask_path) as im:
        if im.mode!='RGBA' or im.size!=(bbox[2]-bbox[0],bbox[3]-bbox[1]):raise ValueError('掩膜范围与原图坐标不一致')
        bitmap=np.asarray(im.getchannel('A'))>0
    transform=Affine(*metadata['transform'])*Affine.translation(bbox[0],bbox[1])
    crs=CRS.from_wkt(metadata['crs_wkt']);geodetic=crs.geodetic_crs
    if geodetic is None:raise ValueError('坐标系缺少可用椭球参考，不能计算平方米')
    translator=Transformer.from_crs(crs,geodetic,always_xy=True,allow_ballpark=False,only_best=True)
    geod=crs.get_geod();polygons=[];area=0.;vertices=0
    for shape,value in rasterio.features.shapes(bitmap.astype('uint8'),mask=bitmap,transform=transform,connectivity=4):
        rings=shape['coordinates'];vertices+=sum(len(r) for r in rings)
        if vertices>recipe['geometry_vertex_limit']:raise ValueError('区域边界超过本地几何额度，请缩小处理范围；不会静默简化原边界')
        ring_areas=[]
        for ring in rings:
            x,y=zip(*ring);lon,lat=translator.transform(x,y,errcheck=True)
            if not all(math.isfinite(v) for v in [*lon,*lat]) or any(abs(v)>90 for v in lat):raise ValueError('区域变换未通过坐标检查')
            if max(lon)-min(lon)>=180:raise ValueError('区域跨度过大，不能作为本地影像面积计算')
            dense=[]
            for index in range(len(ring)-1):
                _,_,distance=geod.inv(lon[index],lat[index],lon[index+1],lat[index+1])
                steps=max(1,math.ceil(distance/1000))
                if len(dense)+steps>100000:raise ValueError('面积计算边界超过本地额度')
                left,right=ring[index],ring[index+1]
                dense.extend([[left[0]+(right[0]-left[0])*i/steps,left[1]+(right[1]-left[1])*i/steps] for i in range(steps)])
            dense.append(ring[-1]);dx,dy=zip(*dense);dlon,dlat=translator.transform(dx,dy,errcheck=True)
            signed,_=geod.polygon_area_perimeter(dlon,dlat);ring_areas.append(abs(signed))
        measured=ring_areas[0]-sum(ring_areas[1:])
        if measured<0 or not math.isfinite(measured):raise ValueError('区域面积无法可靠计算')
        area+=measured;polygons.append(rings)
    return {'geometry_native':{'type':'MultiPolygon','coordinates':polygons},'area_m2':area,
        'area_method':'ellipsoidal_geodesic','area_crs':geodetic.to_string(),'position_status':'declared_map_affine',
        'ground_accuracy':'not_verified','area_edge_densification_max_m':1000,'vertices':vertices,'pixel_area':int(bitmap.sum())}


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['inspect','render','geometry']);p.add_argument('--path');p.add_argument('--request');p.add_argument('--out');args=p.parse_args()
    recipe=json.loads((Path(__file__).resolve().parents[2]/'runtime-packs/geo-rgb-v1.json').read_text())
    if any(version(name)!=v for name,v in recipe['environment'].items()):raise ValueError('本地地理依赖与锁定环境不一致')
    request=json.loads(Path(args.request).read_text()) if args.request else {}
    if args.mode=='inspect':result=inspect(args.path,recipe)
    elif args.mode=='render':result=render(args.path,request,args.out,recipe)
    else:result=geometry(request['metadata'],args.path,request['bbox'],recipe)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
if __name__=='__main__':main()
