"""Explicitly registered, read-only public observation examples.

These are configurable source capabilities, not real forest devices or events.
Network destinations are fixed by server-owned adapters, never by a request URL.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from urllib.error import URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select
from .contracts import Contract, Manifest, digest
from .instance_api import active
from .models import Capability
from .services import audit, check_version, publish, published, register, require, scoped, validate_capability
from .setup_api import DB, P

router = APIRouter(prefix='/api/public-observations')
NAMESPACE = 'public_observation'
SOURCES = {
    'nps_redwood': {'name':'红杉公园公开摄像头', 'kind':'snapshot', 'provider':'美国国家公园管理局',
        'location':'美国加利福尼亚州 Jedediah Smith 红杉公园',
        'source_page':'https://www.nps.gov/media/webcam/view.htm?id=BB7B050A-BE3B-E7FE-BFED58CB2E874B0F',
        'limitations':'约每分钟更新的静态帧，不是连续视频，不属于漳州或示例事件；使用应遵守来源方条件。'},
    'usgs_kilauea': {'name':'基拉韦厄火山公开直播', 'kind':'embedded_live', 'provider':'美国地质调查局',
        'location':'美国夏威夷基拉韦厄火山',
        'source_page':'https://www.usgs.gov/volcanoes/kilauea/summit-webcams',
        'limitations':'第三方公开视频，是否正在直播以官方播放器为准；不属于漳州或示例事件。'},
    'nasa_modis': {'name':'MODIS/Terra 真彩色遥感', 'kind':'satellite', 'provider':'NASA GIBS/ESDIS',
        'location':'全球，支持漳州区域', 'source_page':'https://nasa-gibs.github.io/gibs-api-docs/',
        'limitations':'UTC 日合成影像，约250米分辨率，可能有云遮；非实时、非高分辨率，不可单凭一张影像判断变化。'},
}

class Registration(Contract):
    provider_id: Literal['nps_redwood','usgs_kilauea','nasa_modis']
    confirm_read_only: Literal[True]

class Publication(Contract):
    version: int = Field(ge=1)
    confirm_read_only: Literal[True]

def manifest(provider_id):
    source = SOURCES[provider_id]
    return Manifest(kind='source', namespace=NAMESPACE, name=provider_id, version='1.0.0',
        owner=source['provider'], description=source['name']+'；只读公开资料，不作为现场事件证据。',
        allowed_agent_roles=['perception','data'], execution={'adapter':'public_observation_v1','provider_id':provider_id,'side_effect':False},
        data_policy={'cloud_allowed':False,'external_allowed':False,'deployment':'local'},
        specification={'provider_id':provider_id,'kind':source['kind'],'manual_binding_required':True,'raw_satellite_bands':False})

def verified_cap(db,p,identity):
    cap = published(db,p,identity,'source')
    provider_id = cap.data.get('execution',{}).get('provider_id')
    if provider_id not in SOURCES or cap.namespace != NAMESPACE or cap.data != manifest(provider_id).model_dump(mode='json') or cap.content_hash != digest(cap.data):
        raise HTTPException(409,'公开来源登记与发布内容不一致')
    return cap, provider_id

def resource_proof(db,p,resource):
    details=resource.get('details',{})
    cap,provider_id=verified_cap(db,p,details.get('public_source_id'))
    if details.get('manifest_hash')!=cap.content_hash: raise HTTPException(409,'公开来源已变更，请重新核对配置')
    return {'source_id':cap.id,'manifest_hash':cap.content_hash,'provider_id':provider_id,'kind':SOURCES[provider_id]['kind'],
        'real_device':False,'observation_currentness':'unknown_until_read'}

def configured_cap(db,p,instance_id,source_id):
    instance, release = active(db,p,instance_id)
    cap, provider_id = verified_cap(db,p,source_id)
    resources = release.data.get('snapshot',{}).get('resources',[])
    matched = [r for r in resources if isinstance(r,dict) and r.get('protocol') == 'PUBLIC_OBSERVATION'
        and isinstance(r.get('details'),dict) and r['details'].get('public_source_id') == cap.id
        and r['details'].get('manifest_hash') == cap.content_hash]
    if len(matched) != 1: raise HTTPException(404,'该已启用实例未配置此来源')
    from .map_access import can_read, _row
    if p.role!='admin' and _row(db,p):
        snapshot=release.data.get('snapshot',{})
        enabled=set(instance.data.get('enabled_business_ids',[]))
        names={b.get('name') for b in snapshot.get('businesses',[]) if isinstance(b,dict) and b.get('available') is True and b.get('id') in enabled and isinstance(b.get('name'),str)}
        selected=matched[0].get('businesses',[])
        if not isinstance(selected,list) or not can_read(db,p,[n for n in selected if isinstance(n,str) and n in names]):
            raise HTTPException(404,'该实例未授权查看此来源')
    return cap, provider_id

@router.get('/templates')
def templates(p:P):
    return [{'provider_id':k, **v} for k,v in SOURCES.items()]

@router.get('/registrations')
def registrations(p:P,db:DB):
    from .map_access import require_config
    require_config(db,p)
    rows=db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id, Capability.namespace==NAMESPACE).order_by(Capability.created_at)).all()
    return [{'source_id':r.id,'provider_id':r.name,'status':r.status,'version':r.version,'manifest_hash':r.content_hash} for r in rows]

@router.post('/registrations',status_code=201)
def create_registration(body:Registration,p:P,db:DB):
    require(p,'admin'); expected=manifest(body.provider_id)
    row=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.namespace==NAMESPACE,
        Capability.name==body.provider_id,Capability.release_version=='1.0.0'))
    if row:
        if row.data != expected.model_dump(mode='json') or row.content_hash != digest(row.data): raise HTTPException(409,'原来源登记不一致')
    else:
        row=register(db,p,expected); audit(db,p,'public_observation.registered',row.id,{'provider_id':body.provider_id,'read_only':True}); db.commit()
    return {'source_id':row.id,'provider_id':body.provider_id,'status':row.status,'version':row.version,'manifest_hash':row.content_hash}

@router.post('/{source_id}/publish')
def publish_source(source_id:str,body:Publication,p:P,db:DB):
    require(p,'admin'); row=scoped(db,Capability,source_id,p,True); check_version(row,body.version)
    provider_id=row.data.get('execution',{}).get('provider_id')
    if provider_id not in SOURCES or row.namespace!=NAMESPACE or row.data!=manifest(provider_id).model_dump(mode='json') or row.content_hash!=digest(row.data):
        raise HTTPException(409,'来源登记不一致')
    if row.status=='published': return {'source_id':row.id,'provider_id':provider_id,'status':row.status,'version':row.version,'manifest_hash':row.content_hash}
    if row.status=='draft':
        validate_capability(db,p,row.id,row.version)
    elif row.status!='validated':
        raise HTTPException(409,'来源状态不允许发布')
    publish(db,p,row.id,row.version,'已确认公开资料只读配置；不代表持续在线或现场设备接入')
    audit(db,p,'public_observation.published',row.id,{'provider_id':provider_id,'real_device':False}); db.commit()
    return {'source_id':row.id,'provider_id':provider_id,'status':row.status,'version':row.version,'manifest_hash':row.content_hash}

@router.get('')
def list_sources(instance_id:str,p:P,db:DB):
    _, release=active(db,p,instance_id); results=[]; seen=set()
    for resource in release.data.get('snapshot',{}).get('resources',[]):
        if not isinstance(resource,dict) or resource.get('protocol')!='PUBLIC_OBSERVATION': continue
        details=resource.get('details') if isinstance(resource.get('details'),dict) else {}
        identity=details.get('public_source_id')
        if not isinstance(identity,str) or identity in seen: continue
        try:cap, provider_id=configured_cap(db,p,instance_id,identity)
        except HTTPException as exc:
            if exc.status_code==404:continue
            raise
        seen.add(identity)
        row={'source_id':cap.id,'instance_id':instance_id,'status':'published',**SOURCES[provider_id]}
        if provider_id=='nps_redwood':
            row['media_url']=f'/api/public-observations/{cap.id}/snapshot?instance_id={instance_id}'
            row['refresh_interval_s']=60
        elif provider_id=='usgs_kilauea': row['media_url']='https://www.youtube.com/embed/f9-oSpYpubg'
        else:
            row.update(tile_url_template=f'/api/public-observations/{cap.id}/tiles/{{date}}/{{z}}/{{x}}/{{y}}?instance_id={instance_id}',
                observation_date=(datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat(), time_basis='UTC 日合成',
                max_native_zoom=9, resolution_m=250, center=[24.51,117.65])
        results.append(row)
    return {'instance_id':instance_id,'sources':results}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None

def remote_image(url,expected):
    try:
        with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'ForestryObservation/1.0','Accept':'image/jpeg'}),timeout=12) as result:
            content=result.read(4*1024*1024+1); kind=result.headers.get_content_type(); modified=result.headers.get('Last-Modified','')
            if kind!='image/jpeg' or len(content)>4*1024*1024 or not content.startswith(b'\xff\xd8\xff'): raise ValueError()
            actual=result.headers.get('layer-time-actual','') if expected=='satellite' else ''
    except (OSError, URLError, ValueError): raise HTTPException(502,'公开来源暂时无法读取，请稍后重试') from None
    headers={'Cache-Control':'private, no-store','X-Source-Last-Modified':modified[:100]}
    if actual: headers['X-Image-Date-UTC']=actual[:32]
    return Response(content=content,media_type='image/jpeg',headers=headers)

@router.get('/{source_id}/snapshot')
def snapshot(source_id:str,instance_id:str,p:P,db:DB):
    _, provider_id=configured_cap(db,p,instance_id,source_id)
    if provider_id!='nps_redwood': raise HTTPException(404,'此来源没有静态摄像头画面')
    response=remote_image('https://www.nps.gov/webcams-redw/Jed_Smith.jpg','snapshot')
    db.expire_all()
    from .instance_observation_catalog import ensure_current_principal
    ensure_current_principal(db,p);configured_cap(db,p,instance_id,source_id)
    return response

@router.get('/{source_id}/tiles/{image_date}/{z}/{x}/{y}')
def tile(source_id:str,image_date:str,z:int,x:int,y:int,instance_id:str,p:P,db:DB):
    _, provider_id=configured_cap(db,p,instance_id,source_id)
    if provider_id!='nasa_modis': raise HTTPException(404,'此来源没有卫星影像')
    try:
        day=date.fromisoformat(image_date)
        if day.isoformat()!=image_date or not date(2000,1,1)<=day<=datetime.now(timezone.utc).date() or not 0<=z<=9 or not 0<=x<2**z or not 0<=y<2**z: raise ValueError()
    except ValueError: raise HTTPException(422,'影像日期或范围无效') from None
    url=f'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/{image_date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg'
    response=remote_image(url,'satellite')
    db.expire_all()
    from .instance_observation_catalog import ensure_current_principal
    ensure_current_principal(db,p);configured_cap(db,p,instance_id,source_id)
    return response
