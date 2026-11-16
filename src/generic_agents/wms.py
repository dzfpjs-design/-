"""Workspace-scoped WMS reads, immutable responses and local raster derivation."""
import copy,ipaddress,json,re,socket,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import HTTPException
import httpx
from .config import settings
from .contracts import digest
from .services import scoped,require,audit
from .models import WMSConnection,WMSRead,GeoScene,Job,now
from .evidence import save_immutable,evidence_bytes
from .geo_storage import path_for
from .geo_codec import file_digest,RECIPE_HASH as GEO_HASH
from .local_specialists import guard_record
from .connectors import headers
from .wms_protocol import normalize_endpoint,catalog,map_params,XML_LIMIT,MAP_LIMIT
from .wms_raster import convert,RECIPE_HASH


def endpoint(value):
    try:value=normalize_endpoint(value)
    except ValueError as error:raise HTTPException(422,str(error)) from None
    allowed={v.strip() for v in settings().wms_allowed_endpoints.split(',') if v.strip()}
    if value not in allowed:raise HTTPException(422,'此确切WMS服务地址未获运行环境授权，请由运维登记后再连接')
    parsed=urlsplit(value)
    try:literal=ipaddress.ip_address(parsed.hostname)
    except ValueError:literal=None
    if literal and (literal.is_loopback or literal.is_private) and not (literal.is_link_local or literal.is_multicast or literal.is_unspecified):
        return value
    if parsed.scheme!='https':raise HTTPException(422,'公网WMS需要HTTPS，内网服务需要确切IP及运行环境授权')
    try:addresses=socket.getaddrinfo(parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM)
    except OSError:raise HTTPException(422,'WMS主机无法解析') from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise HTTPException(422,'此WMS地址不能指向未授权的私有或保留网络')
    return value


def grants(workspace_id):
    try:
        value=json.loads(settings().wms_access_grants)
        rows=value.get(workspace_id,[])
        if not isinstance(rows,list) or any(not isinstance(r,dict) or set(r)!={'endpoint','secret_ref'}
            or not isinstance(r['endpoint'],str) or normalize_endpoint(r['endpoint'])!=r['endpoint']
            or not isinstance(r['secret_ref'],str) or not re.fullmatch(r'(GA_CONNECTOR_SECRET_[A-Z0-9_]+)?',r['secret_ref']) for r in rows):raise ValueError()
        return rows
    except (ValueError,TypeError,AttributeError):raise HTTPException(409,'运行环境的WMS工作区访问授权配置无效，请由运维核对') from None


def authorize(configuration,workspace_id,db=None):
    if configuration.get('workspace_id') != workspace_id:
        raise HTTPException(403,'当前工作区未获此地图服务的读取授权')
    pair={'endpoint':configuration['endpoint'],'secret_ref':configuration.get('secret_ref','')}
    from .map_workspace_grants import decision, eligible
    selected=decision(db,workspace_id,pair['endpoint'],pair['secret_ref'],hold=True) if db is not None else None
    legacy=pair in grants(workspace_id)
    if selected is False or selected is None and not legacy or pair['secret_ref'] and not legacy:
        raise HTTPException(403,'当前工作区未获此WMS服务及凭据引用的读取授权，请由运维登记；不借用其它工作区凭据')
    if selected is True and not legacy:
        eligible(pair['endpoint'],workspace_id)


def fetch(configuration,params,limit,guard,db=None):
    authorize(configuration,configuration['workspace_id'],db)
    url=endpoint(configuration['endpoint']);auth=headers({'secret_ref':configuration.get('secret_ref')})
    guard();started=time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(10,connect=3),follow_redirects=False,trust_env=False) as client:
        with client.stream('GET',url,params=params,headers={**auth,'Accept':'image/png,application/xml,text/xml','Accept-Encoding':'identity'}) as response:
            if 300<=response.status_code<400:raise ValueError('WMS跳转未获授权，请核对原服务地址；未跟随目录或响应链接')
            response.raise_for_status();body=bytearray()
            if response.headers.get('content-encoding','').strip().lower() not in ('','identity'):
                raise ValueError('WMS服务返回不支持的压缩编码，请调整服务后重试')
            for chunk in response.iter_raw(chunk_size=65536):
                guard();body.extend(chunk)
                if len(body)>limit or time.monotonic()-started>25:raise ValueError('WMS响应超过大小或时间上限，请缩小范围后新建请求')
            guard();return bytes(body),response.headers.get('content-type','').split(';')[0].lower()


def queue(db,p,row,kind):
    key='connection_id' if isinstance(row,WMSConnection) else 'read_id'
    job=Job(workspace_id=p.workspace_id,kind=kind,idempotency_key=f'{kind}:{row.id}:{row.data["generation"]}',
            data={key:row.id,'generation':row.data['generation'],'actor_id':p.user_id})
    db.add(job);db.flush();row.data={**row.data,'job_id':job.id};return job


def frozen(connection,scope):
    return {'configuration':copy.deepcopy(connection.data['configuration']),'catalog_evidence_id':connection.data['catalog_evidence_id'],
            'catalog_sha256':connection.data['catalog_sha256'],'catalog_hash':connection.data['catalog_hash'],
            'connection_generation':connection.data['generation'],'scope':copy.deepcopy(scope)}


def check_catalog(db,p,snapshot):
    ev,raw=evidence_bytes(db,p,snapshot['catalog_evidence_id'])
    if ev.sha256!=snapshot['catalog_sha256']:raise HTTPException(409,'固定WMS目录原件摘要不一致')
    content=catalog(raw,snapshot['configuration']['version'])
    if digest(content)!=snapshot['catalog_hash']:raise HTTPException(409,'固定WMS目录与保存图层不一致')
    return content


def guard_read(db,p,identity,generation):
    row=guard_record(db,p,WMSRead,identity,generation)
    from .leases import ownership
    job_id=ownership(db).get('job_id');job=db.get(Job,job_id) if job_id else None
    if not job or job.id!=row.data['job_id'] or job.kind!='capture_wms_map' or job.data.get('read_id')!=identity:
        raise HTTPException(409,'地图读取只能在此请求的真实后台租约中执行')
    return check_active_read(db,p,row)


def locked_read(db,p,identity,generation,job_id):
    db.expire_all();row=scoped(db,WMSRead,identity,p,True)
    if row.data['generation']!=generation or row.data['job_id']!=job_id:
        raise HTTPException(409,'原地图执行已被新代次替代，停止保存旧执行结果')
    return check_active_read(db,p,row,locked=True)


def check_active_read(db,p,row,locked=False):
    require(p,'admin','operator');connection=scoped(db,WMSConnection,row.connection_id,p)
    if locked:connection=scoped(db,WMSConnection,row.connection_id,p,True)
    if row.status not in {'queued','running'}:raise HTTPException(409,'地图请求已终止，原记录与响应保留')
    saved=row.data['connection_snapshot'];scope=connection.data['scopes'].get(saved['scope']['id'])
    authorize(saved['configuration'],p.workspace_id,db)
    if (connection.status!='active' or not scope or frozen(connection,scope)!=saved
        or digest(row.data['request'])!=row.data['request_hash'] or digest(saved)!=row.data['connection_snapshot_hash']
        or row.data['runtime_hash']!=RECIPE_HASH):raise HTTPException(409,'原WMS连接、读取授权或固定请求已变化，请核对后恢复；已有响应保留')
    if saved['configuration']['endpoint'] not in {v.strip() for v in settings().wms_allowed_endpoints.split(',') if v.strip()}:
        raise HTTPException(409,'原WMS服务运行授权已撤销，原响应保留')
    return row


def failed(db,p,job,cls,key,operation):
    try:return operation(db,p,job)
    except (ValueError,TypeError,KeyError,HTTPException,httpx.HTTPError,OSError) as error:
        db.rollback()
        row=scoped(db,cls,job.data[key],p,True)
        reason='WMS连接或读取失败，请核对服务或保存配置后重试；原请求与已有响应保留，不转云' if isinstance(error,(httpx.HTTPError,OSError,TypeError,KeyError)) else str(error.detail if isinstance(error,HTTPException) else error)[:600]
        if row.data['generation']==job.data['generation'] and row.status in {'queued','running'}:
            row.status='failed';row.version+=1;row.data={**row.data,'error':reason};audit(db,p,'wms.processing_failed',row.id,{'reason':reason,'cloud_calls':0});db.commit()
        raise


def _probe(db,p,job):
    require(p,'admin');row=scoped(db,WMSConnection,job.data['connection_id'],p,True);generation=job.data['generation']
    if row.data['generation']!=generation or row.status in {'cancelled','catalog_ready','active','paused'}:return
    if digest(row.data['configuration'])!=row.data['configuration_hash']:raise ValueError('WMS连接配置摘要不一致')
    authorize(row.data['configuration'],p.workspace_id,db)
    identity=row.id;row.status='running';row.version+=1;configuration=copy.deepcopy(row.data['configuration']);db.commit()
    def guard():
        from .leases import ownership
        current=guard_record(db,p,WMSConnection,identity,generation)
        job_id=ownership(db).get('job_id');owned=db.get(Job,job_id) if job_id else None
        if not owned or owned.id!=current.data['job_id'] or owned.kind!='read_wms_catalog' or owned.data.get('connection_id')!=identity:
            raise HTTPException(409,'目录读取需要当前请求的真实后台租约')
        if current.data['configuration']!=configuration or digest(configuration)!=current.data['configuration_hash']:
            raise HTTPException(409,'目录请求配置已变化，原请求保留')
        authorize(configuration,p.workspace_id,db)
        return current
    content,mime=fetch(configuration,{'SERVICE':'WMS','VERSION':configuration['version'],'REQUEST':'GetCapabilities'},XML_LIMIT,guard,db)
    ev=save_immutable(db,p,content,'application/xml',{'modality':'map_catalog','purpose':'wms_catalog','connection_id':identity});evidence_bytes(db,p,ev.id)
    row=scoped(db,WMSConnection,identity,p,True);row.data={**row.data,'catalog_evidence_id':ev.id,'catalog_sha256':ev.sha256};db.commit()
    parsed=catalog(content,configuration['version']);guard();db.expire_all();row=scoped(db,WMSConnection,identity,p,True)
    if row.status not in {'queued','running'} or row.data['generation']!=generation or row.data['job_id']!=job.id:
        raise HTTPException(409,'目录读取已取消或替代，原响应保留')
    row.status='catalog_ready';row.version+=1;row.data={**row.data,'catalog':parsed,'catalog_hash':digest(parsed),'scopes':{},'error':None}
    audit(db,p,'wms.catalog_saved',identity,{'sha256':ev.sha256,'layer_count':len(parsed['layers']),'cloud_calls':0});db.commit()


def probe(db,p,job):return failed(db,p,job,WMSConnection,'connection_id',_probe)


def _read(db,p,job):
    require(p,'admin','operator');row=scoped(db,WMSRead,job.data['read_id'],p,True);generation=job.data['generation']
    if row.data['generation']!=generation or row.status in {'succeeded','cancelled'}:return
    identity=row.id;guard=lambda:guard_read(db,p,identity,generation);guard()
    row=scoped(db,WMSRead,identity,p,True);row.status='running';row.version+=1;db.commit();row=guard()
    request=copy.deepcopy(row.data['request']);saved=copy.deepcopy(row.data['connection_snapshot'])
    check_catalog(db,p,saved)
    if not row.data.get('response'):
        params=map_params(saved['configuration']['version'],saved['scope'],request['bbox'],request['width'],request['height'])
        content,mime=fetch(saved['configuration'],params,MAP_LIMIT,guard,db)
        row=locked_read(db,p,identity,generation,job.id)
        ev=save_immutable(db,p,content,mime or 'application/octet-stream',{'modality':'map_render','purpose':'wms_response','read_id':identity,
                          'source_capture_time_known':False,'raw_satellite_bands':False})
        evidence_bytes(db,p,ev.id)
        if mime!='image/png' or not content.startswith(b'\x89PNG\r\n\x1a\n'):
            row.data={**row.data,'rejected_responses':row.data.get('rejected_responses',[])+[{'evidence_id':ev.id,'sha256':ev.sha256,'media_type':mime}]};db.commit()
            raise ValueError('WMS返回的不是PNG地图，拒绝作为影像输入；拒收响应保留')
        row.data={**row.data,'response':{'evidence_id':ev.id,'sha256':ev.sha256,'media_type':mime,'received_at':now().isoformat(),'request_params':params}}
        db.commit();row=guard()
    response=copy.deepcopy(row.data['response']);png_evidence,png=path_for(db,p,response['evidence_id'])
    if png_evidence.sha256!=response['sha256']:raise ValueError('已保存的地图响应损坏，不重新读取远端来替换')
    body={**request,'crs':saved['scope']['crs'],'png_sha256':response['sha256']}
    with tempfile.TemporaryDirectory(prefix='ga-wms-output-') as tmp:
        result=convert(png,body,tmp,guard);output=Path(tmp)/'rendered-map.tif'
        if file_digest(output)!=result['sha256']:raise ValueError('派生GeoTIFF摘要不一致')
        guard();row=locked_read(db,p,identity,generation,job.id);check_catalog(db,p,saved)
        ev=save_immutable(db,p,output.read_bytes(),'image/tiff',{'modality':'raster','purpose':'wms_rendered_geotiff','read_id':identity,
              'name':saved['scope']['title']+'（WMS渲染图）.tif','raw_satellite_bands':False,'source_capture_time_known':False})
        path_for(db,p,ev.id)
        provenance={'read_id':identity,'request_hash':row.data['request_hash'],'connection_snapshot_hash':row.data['connection_snapshot_hash'],
                    'png_sha256':response['sha256'],'derived_sha256':ev.sha256,'conversion_recipe_hash':RECIPE_HASH,
                    'bbox':request['bbox'],'crs':saved['scope']['crs'],'raw_satellite_bands':False,'source_capture_time_known':False}
        scene=GeoScene(workspace_id=p.workspace_id,evidence_id=ev.id,data={'name':ev.data['name'],'input_sha256':ev.sha256,'size_bytes':ev.size_bytes,
             'runtime_hash':GEO_HASH,'upload_request_id':'wms-'+identity,'generation':1,'configuration':None,'metadata':None,'preview':None,
             'error':None,'history':[],'wms_origin':provenance})
        db.add(scene);db.flush()
        from .geo_api import queue as queue_scene
        queue_scene(db,p,scene)
        row.status='succeeded';row.version+=1;row.data={**row.data,'scene_id':scene.id,'result':{**result,'evidence_id':ev.id},'result_hash':digest(result),'error':None}
        audit(db,p,'wms.map_saved',identity,{'scene_id':scene.id,'png_sha256':response['sha256'],'cloud_calls':0});db.commit()


def read(db,p,job):return failed(db,p,job,WMSRead,'read_id',_read)


def source_provenance(db,p,scene):
    try:return _source_provenance(db,p,scene)
    except (KeyError,TypeError,ValueError,AttributeError):
        raise HTTPException(409,"保存的WMS来源证据结构不完整，请核对原记录；不继续处理损坏来源") from None


def _source_provenance(db,p,scene):
    origin=scene.data.get('wms_origin')
    if not origin:return None
    row=scoped(db,WMSRead,origin['read_id'],p)
    if row.status!='succeeded' or row.data.get('scene_id')!=scene.id:raise HTTPException(409,'WMS读取与派生影像不一致')
    saved=row.data['connection_snapshot'];response=row.data['response'];result=row.data['result']
    check_catalog(db,p,saved)
    request=row.data['request'];expected_params=map_params(saved['configuration']['version'],saved['scope'],request['bbox'],request['width'],request['height'])
    if response['media_type']!='image/png' or response['request_params']!=expected_params or result['width']!=request['width'] or result['height']!=request['height']:
        raise HTTPException(409,'地图原响应与固定范围或分辨率不一致')
    if digest(saved)!=row.data['connection_snapshot_hash'] or digest(row.data['request'])!=row.data['request_hash'] or digest({k:v for k,v in result.items() if k!='evidence_id'})!=row.data['result_hash']:
        raise HTTPException(409,'固定地图读取证据摘要不一致')
    png,_=path_for(db,p,response['evidence_id'])
    if png.sha256!=response['sha256']:raise HTTPException(409,'原始地图响应损坏')
    ev,_=path_for(db,p,scene.evidence_id)
    expected={'read_id':row.id,'request_hash':row.data['request_hash'],'connection_snapshot_hash':row.data['connection_snapshot_hash'],
              'png_sha256':response['sha256'],'derived_sha256':ev.sha256,'conversion_recipe_hash':row.data['runtime_hash'],
              'bbox':row.data['request']['bbox'],'crs':saved['scope']['crs'],'raw_satellite_bands':False,'source_capture_time_known':False}
    if origin!=expected or result['sha256']!=ev.sha256 or result['evidence_id']!=scene.evidence_id:
        raise HTTPException(409,'地图来源与派生影像不一致')
    return {**copy.deepcopy(origin),'layer':saved['scope']['layer'],'title':saved['scope']['title'],'style':saved['scope']['style'],
            'version':saved['configuration']['version'],'endpoint':saved['configuration']['endpoint'],'received_at':response['received_at'],
            'read_requested_at':row.created_at.isoformat()}
