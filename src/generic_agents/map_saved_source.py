"""Version-bound publication of one already-saved WMTS/ArcGIS rendering.

This source never performs another network read and makes no claim about
capture time, raw satellite bands or field events.
"""
import base64
import hashlib
from fastapi import APIRouter, HTTPException
from sqlalchemy import select, text
from pydantic import Field
from .contracts import Contract, Manifest, digest
from .models import Job, Capability
from .services import scoped, published, register, validate_capability, publish, require, audit
from .setup_api import P, DB

router = APIRouter(prefix='/api/map-source-publications')
PUBLICATION = 'map_saved_source_publication'

class Publication(Contract):
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{12,100}$')
    preview_id: str = Field(min_length=1,max_length=100)

def _load(db, p, preview_id):
    preview = scoped(db, Job, preview_id, p)
    if preview.kind != 'map_service_preview' or preview.status != 'internal' or not isinstance(preview.data, dict):
        raise HTTPException(409, '已保存地图记录无法核对')
    data = preview.data
    connection = scoped(db, Job, data.get('connection_id'), p)
    if connection.kind != 'map_service_connection' or connection.status != 'internal' or not isinstance(connection.data, dict):
        raise HTTPException(409, '地图服务连接无法核对')
    conf = connection.data
    protocol = conf.get('protocol')
    if protocol not in ('wmts', 'arcgis', 'arcgis_image'):
        raise HTTPException(409, '地图服务协议无法核对')
    from .map_services_api import _url
    from .wms import authorize
    _url(conf.get('endpoint',''), protocol)
    authorize({'workspace_id':p.workspace_id,'endpoint':conf['endpoint'],'secret_ref':conf.get('secret_ref','')},p.workspace_id,db)
    try:
        raw=base64.b64decode(data['png'], validate=True)
        if (not raw or len(raw)>8*1024*1024 or hashlib.sha256(raw).hexdigest()!=data['sha256']
                or not isinstance(data['selection'],dict) or not isinstance(data['catalog_sha256'],str)):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise HTTPException(409, '保存地图或来源摘要不一致，请重新核对') from None
    return preview, connection

def _manifest(preview, connection):
    d, c = preview.data, connection.data
    proof={'preview_id':preview.id,'connection_id':connection.id,'protocol':c['protocol'],
           'sha256':d['sha256'],'catalog_sha256':d['catalog_sha256'],'selection':d['selection']}
    return Manifest(kind='source',namespace='saved_gateway_map',name='preview_'+preview.id.replace('-',''),
        version='1.0.0', owner='已核对的地图服务',
        description=str(c['name'])[:180]+'；仅读取已保存的地图，不重新访问服务，不代表原始卫星影像或业务事实。',
        allowed_agent_roles=['perception','data'],input_schema={'type':'object','additionalProperties':False},
        output_schema={'type':'object','additionalProperties':False,'required':['maps','has_more','not_authoritative','network_reads'],
          'properties':{'maps':{'type':'array','maxItems':1},'has_more':{'type':'boolean'},'not_authoritative':{'const':True},'network_reads':{'const':0}}},
        execution={'adapter':'saved_gateway_map','proof':proof,'side_effect':False},
        data_policy={'cloud_allowed':False,'external_allowed':False,'deployment':'local'},
        specification={'timezone':'Asia/Shanghai','raw_satellite_bands':False,'automatic_domain_use':False,'source_capture_time_verified':False})

def _publication(db,p,preview_id):
    return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==PUBLICATION,
        Job.status=='internal',Job.idempotency_key=='map-source-publication:'+preview_id))

def validate_manifest(db,p,value):
    try:
        proof=value.execution['proof']; preview,connection=_load(db,p,proof['preview_id'])
        row=_publication(db,p,preview.id)
        if not row or row.data.get('manifest_hash')!=digest(value.model_dump(mode='json')) or value.model_dump(mode='json')!=_manifest(preview,connection).model_dump(mode='json'):
            raise HTTPException(409,'地图来源登记与原始地图不一致')
    except (KeyError,TypeError,ValueError):
        raise HTTPException(409,'地图来源登记结构无效') from None

def current_source(db,p,capability_id):
    cap=published(db,p,capability_id,'source')
    value=Manifest.model_validate(cap.data)
    if value.execution.get('adapter')!='saved_gateway_map' or digest(cap.data)!=cap.content_hash:
        raise HTTPException(409,'地图来源发布契约无效')
    validate_manifest(db,p,value)
    preview,connection=_load(db,p,value.execution['proof']['preview_id'])
    receipt=_publication(db,p,preview.id)
    if receipt.data.get('capability_id')!=cap.id or receipt.data.get('manifest_hash')!=cap.content_hash:
        raise HTTPException(409,'地图来源发布记录不一致')
    return cap,preview,connection

def resource_proof(db,p,resource):
    details=resource.get('details',{})
    cap,preview,connection=current_source(db,p,details.get('capability_id'))
    expected_protocol={'wmts':'WMTS','arcgis':'ArcGIS MapServer','arcgis_image':'ArcGIS ImageServer'}[connection.data['protocol']]
    if (resource.get('protocol')!=expected_protocol or details.get('map_connection_id')!=connection.id
        or details.get('map_preview_id')!=preview.id or details.get('manifest_hash')!=cap.content_hash):
        raise HTTPException(409,'业务地图与已登记来源不一致')
    return {'capability_id':cap.id,'manifest_hash':cap.content_hash,'preview_id':preview.id,
      'connection_id':connection.id,'protocol':expected_protocol,'sha256':preview.data['sha256'],
      'selection':preview.data['selection'],'catalog_sha256':preview.data['catalog_sha256'],
      'source_kind':'saved_gateway_map','raw_satellite_bands':False,'domain_quality_verified':False}

def registered_read(db,p,cap):
    _,preview,connection=current_source(db,p,cap.id)
    return {'maps':[{'scene_id':preview.id,'name':connection.data['name']+' · 已保存地图',
       'not_authoritative':True}], 'has_more':False,'not_authoritative':True,'network_reads':0}

def _lock(db,p,key):
    lock=int.from_bytes(hashlib.sha256((p.workspace_id+':'+key).encode()).digest()[:8],'big',signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':lock})

def _attempt(db,p,body):
    return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
        Job.idempotency_key=='map-source-attempt:'+body.request_id))

def _attempt_result(db,p,body,row):
    if (row.kind!='map_source_attempt' or row.status!='internal' or row.data.get('actor_id')!=p.user_id
            or row.data.get('preview_id')!=body.preview_id):
        raise HTTPException(409,'原发布请求与账号、工作区或地图不一致')
    if row.data.get('state')=='cancelled':return {'state':'cancelled','request_id':body.request_id,'preview_id':body.preview_id}
    if row.data.get('state')!='created':raise HTTPException(409,'原发布请求回执无法核对')
    # A submission receipt is evidence of what was committed, not authority
    # to use the source today. Recover it even after authorization is revoked.
    cap=scoped(db,Capability,row.data.get('capability_id'),p)
    publication=_publication(db,p,body.preview_id)
    record=row.data.get('record')
    try:
        proof=cap.data['execution']['proof']
        if record is None and row.data.get('record_hash') is None:
            # Compatibility for receipts created before their snapshot was
            # persisted: reconstruct only from the immutable manifest and
            # its bound connection, without reauthorizing network access.
            connection=scoped(db,Job,proof['connection_id'],p)
            name=connection.data['name']
            suffix='；仅读取已保存的地图，不重新访问服务，不代表原始卫星影像或业务事实。'
            if (connection.kind!='map_service_connection' or connection.status!='internal'
                or connection.data.get('protocol')!=proof['protocol']
                or not isinstance(name,str) or cap.data['description']!=name[:180]+suffix):
                raise ValueError()
            record={'capability_id':cap.id,'manifest_hash':cap.content_hash,'preview_id':proof['preview_id'],
                    'connection_id':proof['connection_id'],'protocol':proof['protocol'],
                    'name':name,'selection':proof['selection']}
        elif not isinstance(record,dict) or digest(record)!=row.data.get('record_hash'):
            raise ValueError()
        if (not publication or publication.data.get('capability_id')!=cap.id
            or publication.data.get('manifest_hash')!=cap.content_hash or digest(cap.data)!=cap.content_hash
            or cap.data['execution']['adapter']!='saved_gateway_map' or record['capability_id']!=cap.id
            or record['manifest_hash']!=cap.content_hash or record['preview_id']!=body.preview_id
            or proof['preview_id']!=body.preview_id or record['connection_id']!=proof['connection_id']
            or record['protocol']!=proof['protocol'] or record['selection']!=proof['selection']):
            raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError):
        raise HTTPException(409,'原发布回执或来源版本无法核对') from None
    try:
        current_source(db,p,cap.id)
        valid=True
    except (HTTPException,ValueError,KeyError,TypeError,AttributeError):
        valid=False
    return {'state':'created','request_id':body.request_id,**record,'source_valid':valid,
            **({'source_message':'原地图已发布，但当前授权或来源状态已失效；请回网关核对。'} if not valid else {})}

@router.post('/resolve')
def resolve(body:Publication,p:P,db:DB):
    require(p,'admin');_lock(db,p,'map-source-attempt:'+body.request_id)
    old=_attempt(db,p,body)
    if old:return _attempt_result(db,p,body,old)
    # A revoked network grant must not prevent cancelling an uncompleted
    # request; the stop marker cannot read or publish any image.
    preview=scoped(db,Job,body.preview_id,p)
    if preview.kind!='map_service_preview' or preview.status!='internal':
        raise HTTPException(404,'没有找到原地图记录')
    db.add(Job(workspace_id=p.workspace_id,kind='map_source_attempt',status='internal',
       idempotency_key='map-source-attempt:'+body.request_id,
       data={'actor_id':p.user_id,'preview_id':body.preview_id,'state':'cancelled'}))
    audit(db,p,'map_source.attempt_cancelled',body.preview_id,{});db.commit()
    return {'state':'cancelled','request_id':body.request_id,'preview_id':body.preview_id}

@router.post('')
def create(body:Publication,p:P,db:DB):
    require(p,'admin');_lock(db,p,'map-source-attempt:'+body.request_id)
    attempt=_attempt(db,p,body)
    if attempt:return _attempt_result(db,p,body,attempt)
    _lock(db,p,'map-source:'+body.preview_id)
    preview,connection=_load(db,p,body.preview_id)
    old=_publication(db,p,preview.id)
    if old:
        cap,preview,connection=current_source(db,p,old.data.get('capability_id'))
    else:
        manifest=_manifest(preview,connection);cap=register(db,p,manifest)
        db.add(Job(workspace_id=p.workspace_id,kind=PUBLICATION,status='internal',
          idempotency_key='map-source-publication:'+preview.id,
          data={'preview_id':preview.id,'capability_id':cap.id,'manifest_hash':cap.content_hash}))
        db.flush();validate_capability(db,p,cap.id,cap.version)
        publish(db,p,cap.id,cap.version,'确认此固定地图供业务实例只读查看；不认定实时状态或原始卫星波段')
        audit(db,p,'map_source.published',preview.id,{'capability_id':cap.id})
    record={'capability_id':cap.id,'manifest_hash':cap.content_hash,'preview_id':preview.id,
        'connection_id':connection.id,'protocol':connection.data['protocol'],
        'name':connection.data['name'],'selection':preview.data['selection']}
    attempt=Job(workspace_id=p.workspace_id,kind='map_source_attempt',status='internal',
       idempotency_key='map-source-attempt:'+body.request_id,
       data={'actor_id':p.user_id,'preview_id':preview.id,'state':'created','capability_id':cap.id,
             'record':record,'record_hash':digest(record)})
    db.add(attempt);db.flush();db.commit()
    return _attempt_result(db,p,body,attempt)
