"""Saved raster originals and explicit RGB/map declarations, before local inference."""
import hashlib,math
from datetime import datetime
from typing import Annotated
from fastapi import APIRouter,UploadFile,File,Form,HTTPException
from fastapi.responses import FileResponse,Response
from pydantic import Field,model_validator,StrictInt
from sqlalchemy import select,text
from .setup_api import P,DB
from .contracts import Contract,digest
from .services import require,scoped,check_version,audit
from .models import GeoScene,Job,Membership
from .security import Principal
from .geo_codec import RECIPE,RECIPE_HASH
from .geo_storage import upload,path_for
from .evidence import evidence_bytes
router=APIRouter(prefix='/api/geo')


def view(row):
    return {'id':row.id,'version':row.version,'status':row.status,'evidence_id':row.evidence_id,'created_at':row.created_at.isoformat(),**row.data}


def queue(db,p,row):
    generation=row.data['generation'];job=Job(workspace_id=p.workspace_id,kind='parse_geo_scene',
        idempotency_key=f'geo-scene:{row.id}:{generation}',data={'scene_id':row.id,'generation':generation,'actor_id':p.user_id})
    db.add(job);db.flush();row.data={**row.data,'job_id':job.id};return job


@router.get('/catalog')
def catalog(p:P):
    from .config import settings
    return {'runtime_installed':settings().geo_python.is_file(),'recipe_hash':RECIPE_HASH,'max_upload_bytes':RECIPE['max_upload_bytes'],
        'local':True,'cloud_fallback':False,'supported':'GTiff local upload; no VRT or remote URL','spatial_inference':'local RGB candidate segmentation; confirmed local raster task binding; domain quality unverified', 'max_inference_pixels':RECIPE['max_inference_pixels']}


@router.get('/scenes')
def scenes(p:P,db:DB):
    return [view(r) for r in db.scalars(select(GeoScene).where(GeoScene.workspace_id==p.workspace_id).order_by(GeoScene.created_at.desc()).limit(100))]


@router.post('/scenes',status_code=201)
async def create(p:P,db:DB,request_id:Annotated[str,Form(pattern=r'^[a-zA-Z0-9_-]{12,100}$')],file:UploadFile=File()):
    row=await upload(db,p,file);await file.close()
    lock=int.from_bytes(hashlib.sha256((p.workspace_id+request_id).encode()).digest()[:8],'big',signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':lock})
    old=db.scalar(select(GeoScene).where(GeoScene.workspace_id==p.workspace_id,GeoScene.data['upload_request_id'].as_string()==request_id))
    if old:
        if old.data['input_sha256']!=row.sha256 or old.data['name']!=row.data['name']:raise HTTPException(409,'同一上传请求不能更换原件或文件名，旧影像保留')
        result=view(old);db.rollback();return result
    scene=GeoScene(workspace_id=p.workspace_id,evidence_id=row.id,data={'name':row.data['name'],'input_sha256':row.sha256,'size_bytes':row.size_bytes,
        'upload_request_id':request_id,'runtime_hash':RECIPE_HASH,'generation':1,'configuration':None,'metadata':None,'preview':None,'error':None,'history':[]})
    db.add(scene);db.flush();queue(db,p,scene);audit(db,p,'geo.scene_queued',scene.id,{'cloud_calls':0});db.commit();return view(scene)


@router.get('/uploads/{request_id}')
def upload_request(request_id:str,p:P,db:DB):
    import re
    if not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}',request_id):raise HTTPException(422,'上传请求格式不正确')
    row=db.scalar(select(GeoScene).where(GeoScene.workspace_id==p.workspace_id,GeoScene.data['upload_request_id'].as_string()==request_id))
    if not row:raise HTTPException(404,'此上传请求尚未保存；请用原请求和原文件重试，不另建请求')
    ev,_=path_for(db,p,row.evidence_id)
    if ev.sha256!=row.data['input_sha256'] or ev.size_bytes!=row.data['size_bytes']:raise HTTPException(409,'保存记录与影像原件不一致，恢复请求保留')
    return view(row)


@router.get('/scenes/{identity}')
def detail(identity:str,p:P,db:DB):return view(scoped(db,GeoScene,identity,p))


@router.get('/scenes/{identity}/original')
def original(identity:str,p:P,db:DB,workspace_id:str|None=None):
    if workspace_id and workspace_id!=p.workspace_id:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==workspace_id,Membership.user_id==p.user_id))
        if not membership:raise HTTPException(403,'没有此工作区的访问权限')
        p=Principal(p.user_id,membership.workspace_id,membership.role,p.session_id)
    from .map_access import require_config
    require_config(db,p)
    row=scoped(db,GeoScene,identity,p);ev,path=path_for(db,p,row.evidence_id)
    if ev.sha256!=row.data['input_sha256']:raise HTTPException(409,'影像记录与原件摘要不一致')
    return FileResponse(path,media_type='image/tiff',filename=row.data['name'],headers={'X-Content-SHA256':ev.sha256,'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.get('/scenes/{identity}/preview')
def preview(identity:str,p:P,db:DB):
    from .map_access import require_config
    require_config(db,p)
    return preview_internal(identity,p,db)

def preview_internal(identity:str,p:P,db:DB):
    row=scoped(db,GeoScene,identity,p);saved=row.data.get('preview')
    if not saved:raise HTTPException(409,'尚未确认RGB设置或预览尚未保存，原件仍保留')
    ev,path=path_for(db,p,row.evidence_id);image,content=evidence_bytes(db,p,saved['evidence_id'])
    if ev.sha256!=row.data['input_sha256'] or image.sha256!=saved['sha256'] or image.data.get('scene_id')!=row.id or image.data.get('generation')!=row.data['generation']:
        raise HTTPException(409,'预览与影像版本不一致，请读取保存记录')
    return Response(content,media_type='image/png',headers={'X-Content-SHA256':image.sha256,'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


class Version(Contract):version:int=Field(ge=1)


class Configuration(Version):
    rgb_bands:tuple[StrictInt,StrictInt,StrictInt]
    ranges:tuple[tuple[float,float],tuple[float,float],tuple[float,float]]|None=None
    map_image_confirmed:bool=False
    observed_at:datetime|None=None
    @model_validator(mode='after')
    def checked(self):
        if len(set(self.rgb_bands))!=3 or any(b<1 for b in self.rgb_bands):raise ValueError('请确认三个不同的RGB波段')
        if self.ranges and any(not all(math.isfinite(v) for v in r) or r[0]>=r[1] for r in self.ranges):raise ValueError('每个波段需提供有限且递增的数值范围')
        if self.observed_at and self.observed_at.tzinfo is None:raise ValueError('采集时间需包含时区；未知时保持空白')
        return self


@router.put('/scenes/{identity}/configuration')
def configure(identity:str,body:Configuration,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,GeoScene,identity,p,True);check_version(row,body.version)
    if row.status not in {'parsed','ready','failed','cancelled'} or not row.data.get('metadata'):raise HTTPException(409,'请先完成空间读取，或取消正在处理的版本')
    meta=row.data['metadata']
    if body.map_image_confirmed and meta['georeference_status']!='affine_available':raise HTTPException(422,'原件没有已支持的地图参考，不能确认地理位置；可继续按像素处理')
    if any(b>meta['bands'] for b in body.rgb_bands):raise HTTPException(422,'选择的RGB波段不在当前影像中')
    if body.ranges is None and any(meta['dtypes'][b-1]!='uint8' for b in body.rgb_bands):raise HTTPException(422,'高位深影像需确认三个波段的数值范围')
    value=body.model_dump(mode='json',exclude={'version'});generation=row.data['generation']+1
    history=row.data['history']+[{'generation':row.data['generation'],'configuration':row.data['configuration'],'preview':row.data['preview'],'status':row.status,'error':row.data['error']}]
    row.status='queued';row.version+=1;row.data={**row.data,'generation':generation,'configuration':value,'history':history[-30:],'preview':None,'error':None,'runtime_hash':RECIPE_HASH}
    queue(db,p,row);audit(db,p,'geo.rgb_confirmed',row.id,{'generation':generation,'configuration_hash':digest(value),'observed_at_inferred':False});db.commit();return view(row)


@router.post('/scenes/{identity}/cancel')
def cancel(identity:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,GeoScene,identity,p,True);check_version(row,body.version)
    if row.status not in {'queued','running'}:raise HTTPException(409,'此影像处理已经结束，记录保留')
    row.status='cancelled';row.version+=1;audit(db,p,'geo.scene_cancelled',row.id);db.commit();return view(row)


@router.post('/scenes/{identity}/retry')
def retry(identity:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,GeoScene,identity,p,True);check_version(row,body.version)
    if row.status not in {'failed','cancelled'}:raise HTTPException(409,'只有失败或取消的处理可以明确重试')
    path_for(db,p,row.evidence_id)
    history=row.data['history']+[{'generation':row.data['generation'],'configuration':row.data['configuration'],'preview':row.data['preview'],'status':row.status,'error':row.data['error']}]
    row.status='queued';row.version+=1;row.data={**row.data,'generation':row.data['generation']+1,'runtime_hash':RECIPE_HASH,'preview':None,'history':history[-30:],'error':None}
    queue(db,p,row);db.commit();return view(row)


class Analysis(Version):
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    installation_id:str=Field(min_length=1,max_length=100)
    roi:tuple[StrictInt,StrictInt,StrictInt,StrictInt]|None=None


def run_view(row):return {'id':row.id,'version':row.version,'status':row.status,'scene_id':row.scene_id,'installation_id':row.installation_id,'created_at':row.created_at.isoformat(),**row.data}


@router.get('/scenes/{identity}/runs')
def runs(identity:str,p:P,db:DB):
    from .models import GeoRun
    scoped(db,GeoScene,identity,p)
    return [run_view(r) for r in db.scalars(select(GeoRun).where(GeoRun.workspace_id==p.workspace_id,GeoRun.scene_id==identity).order_by(GeoRun.created_at.desc()).limit(100))]


@router.post('/scenes/{identity}/runs',status_code=201)
def analyze(identity:str,body:Analysis,p:P,db:DB):
    from .models import GeoRun
    from .local_specialists import ready_install,SAM_RECIPE
    require(p,'admin','operator');scene=scoped(db,GeoScene,identity,p,True)
    # Same request recovers a saved response even when a worker has since advanced versions.
    old=db.scalar(select(GeoRun).where(GeoRun.workspace_id==p.workspace_id,GeoRun.scene_id==identity,GeoRun.data['request_id'].as_string()==body.request_id))
    body_hash=digest(body.model_dump(mode='json'))
    if old:
        if old.data['request_hash']!=body_hash:raise HTTPException(409,'同一分析请求不能更改影像版本、模型或范围')
        return run_view(old)
    check_version(scene,body.version)
    if scene.status!='ready':raise HTTPException(409,'请先保存RGB设置并完成预览，原件保留')
    installation=ready_install(db,p,body.installation_id)
    if installation.pack_id!='sam2-1-tiny':raise HTTPException(422,'空间候选分割需要已就绪的本地SAM模型')
    meta=scene.data['metadata'];roi=list(body.roi or (0,0,meta['width'],meta['height']))
    if not 0<=roi[0]<roi[2]<=meta['width'] or not 0<=roi[1]<roi[3]<=meta['height']:raise HTTPException(422,'分析范围超出影像原始像素边界')
    if (roi[2]-roi[0])*(roi[3]-roi[1])>RECIPE['max_inference_pixels']:raise HTTPException(422,'分析范围超过1600万像素，请明确选择较小区域；不会自动缩放或只取左上角')
    ev,_=path_for(db,p,scene.evidence_id)
    row=GeoRun(workspace_id=p.workspace_id,scene_id=scene.id,installation_id=installation.id,data={'request_id':body.request_id,'request_hash':body_hash,
        'scene_generation':scene.data['generation'],'original_evidence_id':ev.id,'input_sha256':ev.sha256,'metadata':meta,'configuration':scene.data['configuration'],
        'roi':roi,'backend':installation.data['backend'],'installation_generation':installation.data['generation'],'sam_recipe_hash':digest(SAM_RECIPE),
        'runtime_hash':RECIPE_HASH,'generation':1,'stage':'requested','result':None,'error':None,'review':None,'review_history':[]})
    db.add(row);db.flush();run_queue(db,p,row);audit(db,p,'geo.analysis_queued',row.id,{'roi':roi,'cloud_calls':0});db.commit();return run_view(row)


def run_queue(db,p,row):
    job=Job(workspace_id=p.workspace_id,kind='analyze_geo_scene',idempotency_key=f'geo-run:{row.id}:{row.data["generation"]}',data={'run_id':row.id,'generation':row.data['generation'],'actor_id':p.user_id})
    db.add(job);db.flush();row.data={**row.data,'job_id':job.id}


def checked_run(db,p,identity):
    from .models import GeoRun
    row=scoped(db,GeoRun,identity,p);ev,_=path_for(db,p,row.data['original_evidence_id'])
    if ev.sha256!=row.data['input_sha256']:raise HTTPException(409,'GeoTIFF原件与候选记录不一致')
    if row.status!='succeeded' or not row.data.get('result'):raise HTTPException(409,'本地候选尚未保存，原件保留')
    if digest(row.data['result'])!=row.data.get('result_hash'):raise HTTPException(409,'候选记录摘要不一致，未作为核查依据')
    return row


@router.get('/runs/{identity}')
def run_detail(identity:str,p:P,db:DB):
    from .models import GeoRun
    return run_view(scoped(db,GeoRun,identity,p))


@router.get('/runs/{identity}/rgb')
def run_rgb(identity:str,p:P,db:DB):
    from .map_access import require_config
    require_config(db,p)
    row=checked_run(db,p,identity);cp=row.data['rgb_checkpoint'];ev,raw=evidence_bytes(db,p,cp['rgb_evidence_id'])
    if ev.sha256!=cp['rgb_sha256'] or ev.data.get('run_id')!=row.id or ev.data.get('source_sha256')!=row.data['input_sha256']:raise HTTPException(409,'RGB分析图与保存的原件版本不一致')
    return Response(raw,media_type='image/png',headers={'Cache-Control':'private, no-store','X-Content-SHA256':ev.sha256})


@router.get('/runs/{identity}/masks/{mask_id}')
def run_mask(identity:str,mask_id:str,p:P,db:DB):
    from .map_access import require_config
    require_config(db,p)
    from .segmentation import mask_bytes
    row=checked_run(db,p,identity);ev,raw=mask_bytes(db,p,row.data['result'],mask_id,row.data['rgb_checkpoint']['rgb_evidence_id'])
    return Response(raw,media_type='image/png',headers={'Cache-Control':'private, no-store','X-Content-SHA256':ev.sha256})


@router.get('/runs/{identity}/native-geojson')
def native_export(identity:str,p:P,db:DB):
    from .map_access import require_config
    require_config(db,p)
    import json
    from .segmentation import mask_bytes
    row=checked_run(db,p,identity);result=row.data['result'];features=[]
    for mask in result['masks']:
        mask_bytes(db,p,result,mask['mask_evidence_id'],row.data['rgb_checkpoint']['rgb_evidence_id'])
        features.append({'type':'Feature','geometry':mask['geometry_native'],'properties':{k:v for k,v in mask.items() if k not in {'geometry_native','rgb_merge_evidence'}}})
    value={'type':'FeatureCollection','crs':{'type':'name','properties':{'name':row.data['metadata']['crs']}},'features':features,
        'format_notice':'Native source CRS coordinates; NOT RFC7946/WGS84 GeoJSON. Retain declared CRS.',
        'source_raster':result['source_raster'],'source_crs_wkt':row.data['metadata']['crs_wkt'],'result_hash':row.data['result_hash'],'review':row.data.get('review'),
        'candidate_only':True,'ground_accuracy':'not_verified','domain_quality':'not_verified'}
    return Response(json.dumps(value,ensure_ascii=False,allow_nan=False),media_type='application/geo+json',headers={'Content-Disposition':'attachment; filename="native-candidates.geojson"','Cache-Control':'private, no-store'})


class Review(Version):
    note:str=Field(min_length=1,max_length=4000)
    decisions:dict[str,str]=Field(default_factory=dict,max_length=80)


@router.put('/runs/{identity}/review')
def review(identity:str,body:Review,p:P,db:DB):
    from .models import GeoRun
    from .segmentation import mask_bytes
    require(p,'admin','operator');row=scoped(db,GeoRun,identity,p,True);check_version(row,body.version);checked_run(db,p,identity)
    result=row.data['result'];identities={str(m['candidate_index']) for m in result['masks']}
    if any(k not in identities or v not in {'keep','exclude','uncertain'} for k,v in body.decisions.items()):raise HTTPException(422,'候选核查选项不属于本次结果')
    for mask in result['masks']:mask_bytes(db,p,result,mask['mask_evidence_id'],row.data['rgb_checkpoint']['rgb_evidence_id'])
    if not body.note.strip():raise HTTPException(422,'请填写核查备注')
    history=row.data['review_history']+([row.data['review']] if row.data.get('review') else [])
    row.data={**row.data,'review':{'note':body.note.strip(),'decisions':body.decisions,'reviewer_id':p.user_id,'result_hash':row.data['result_hash'],'saved_at':datetime.now().astimezone().isoformat()},'review_history':history[-30:]};row.version+=1
    audit(db,p,'geo.review_saved',row.id,{'candidate_only':True,'entity_state_updated':False});db.commit();return run_view(row)


@router.post('/runs/{identity}/cancel')
def cancel_run(identity:str,body:Version,p:P,db:DB):
    from .models import GeoRun
    require(p,'admin','operator');row=scoped(db,GeoRun,identity,p,True);check_version(row,body.version)
    if row.status not in {'queued','running'}:raise HTTPException(409,'本次候选处理已经结束，结果保留')
    row.status='cancelled';row.version+=1;db.commit();return run_view(row)


@router.post('/runs/{identity}/retry')
def retry_run(identity:str,body:Version,p:P,db:DB):
    from .models import GeoRun
    from .local_specialists import SAM_RECIPE
    require(p,'admin','operator');row=scoped(db,GeoRun,identity,p,True);check_version(row,body.version)
    if row.status not in {'failed','cancelled'}:raise HTTPException(409,'只有失败或取消的分析可以重试')
    if row.data['runtime_hash']!=RECIPE_HASH or row.data['sam_recipe_hash']!=digest(SAM_RECIPE):raise HTTPException(409,'处理配方已经变化，请创建新分析请求；旧结果保留')
    path_for(db,p,row.data['original_evidence_id']);row.status='queued';row.version+=1;row.data={**row.data,'generation':row.data['generation']+1,'error':None};run_queue(db,p,row);db.commit();return run_view(row)


@router.post('/installations/{identity}/register-spatial')
def register_spatial(identity:str,body:Version,p:P,db:DB):
    from .local_specialists import ready_install
    from .workflow_scenes import spatial_manifest
    from .services import register,validate_capability,publish
    require(p,'admin');row=ready_install(db,p,identity);manifest=spatial_manifest(row)
    # A saved registration response may be lost. Reuse the exact published version,
    # even if an unrelated save has advanced the installation row's CAS version.
    if row.data.get('geo_capability_id') and row.data.get('geo_runtime_hash')==RECIPE_HASH:
        from .services import published
        cap=published(db,p,row.data['geo_capability_id'],'specialist_model')
        if cap.data==manifest.model_dump():return {'installation_id':row.id,'version':row.version,'capability_id':cap.id,'runtime_hash':RECIPE_HASH}
    check_version(row,body.version);cap=register(db,p,manifest)
    if cap.status!='published':
        validate_capability(db,p,cap.id,cap.version);publish(db,p,cap.id,cap.version,'仅登记本地通用空间候选；地面定位精度和领域识别评测尚未通过')
    row.data={**row.data,'geo_capability_id':cap.id,'geo_runtime_hash':RECIPE_HASH};row.version+=1
    audit(db,p,'geo.ability_registered',cap.id,{'deployment':'local','runtime_hash':RECIPE_HASH,'domain_evaluation':'pending'});db.commit()
    return {'installation_id':row.id,'version':row.version,'capability_id':cap.id,'runtime_hash':RECIPE_HASH}
