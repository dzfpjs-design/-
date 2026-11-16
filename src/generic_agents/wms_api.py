"""Human WMS registration and explicit read-only layer/extent permissions."""
import copy,hashlib
from typing import Literal
from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse
from pydantic import Field,StrictInt,StrictFloat,StrictBool
from sqlalchemy import select,text
from .setup_api import P,DB
from .contracts import Contract,digest
from .services import require,scoped,check_version,audit
from .models import WMSConnection,WMSRead,GeoScene
from .geo_storage import path_for
from .geo_pairs import workspace_principal
from .wms_protocol import normalize_endpoint,bbox,within
from .wms_raster import RECIPE_HASH
from .wms import endpoint,queue,frozen,check_catalog,source_provenance,authorize,grants

router=APIRouter(prefix='/api/wms')


def view(row):return {'id':row.id,'request_id':row.request_id,'version':row.version,'status':row.status,'created_at':row.created_at.isoformat(),
                      **({'connection_id':row.connection_id} if isinstance(row,WMSRead) else {}),**row.data}
def lock_request(db,p,key):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int.from_bytes(hashlib.sha256((p.workspace_id+key).encode()).digest()[:8],'big',signed=True)})


class Connection(Contract):
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{12,100}$')
    name:str=Field(min_length=1,max_length=200)
    endpoint:str=Field(min_length=1,max_length=1800)
    version:Literal['1.1.1','1.3.0']='1.3.0'
    secret_ref:str=Field(default='',pattern=r'^(GA_CONNECTOR_SECRET_[A-Z0-9_]+)?$')


class Version(Contract):version:StrictInt=Field(ge=1)


class Scope(Version):
    layer:str=Field(min_length=1,max_length=200)
    crs:Literal['EPSG:4326','CRS:84','EPSG:3857']
    bbox:tuple[StrictFloat|StrictInt,StrictFloat|StrictInt,StrictFloat|StrictInt,StrictFloat|StrictInt]
    style:str=Field(default='',max_length=200)
    confirm_read_only:StrictBool=False


class Read(Contract):
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{12,100}$')
    scope_id:str=Field(min_length=1,max_length=100)
    bbox:tuple[StrictFloat|StrictInt,StrictFloat|StrictInt,StrictFloat|StrictInt,StrictFloat|StrictInt]
    width:StrictInt=Field(ge=32,le=2048)
    height:StrictInt=Field(ge=32,le=2048)


class Publication(Version):scope_id:str=Field(min_length=1,max_length=100)


@router.get('/connections/{identity}/source-registrations/{scope_id}')
def source_registration(identity:str,scope_id:str,p:P,db:DB):
    from .wms_source import current_source
    row=scoped(db,WMSConnection,identity,p);publication=row.data.get('publications',{}).get(scope_id)
    if not publication:raise HTTPException(404,'此地图范围尚未登记为业务来源')
    current_source(db,p,publication['capability_id']);return view(row)


@router.post('/connections/{identity}/register-source')
def register_source(identity:str,body:Publication,p:P,db:DB):
    from .wms_source import checked_scope,manifest,saved_maps
    from .services import register,validate_capability,publish
    from .models import Capability,now
    require(p,'admin');row=scoped(db,WMSConnection,identity,p,True)
    row,scope=checked_scope(db,p,row.id,body.scope_id);entries=copy.deepcopy(row.data.get('publications',{}));old=entries.get(scope['id'])
    if old:
        cap=scoped(db,Capability,old['capability_id'],p)
        expected=manifest(row,scope,old['generation'])
        if cap.status=='published' and cap.data==expected.model_dump(mode='json') and cap.content_hash==old['manifest_hash']:
            from .wms_source import current_source
            current_source(db,p,cap.id);return view(row)
    check_version(row,body.version);maps,_=saved_maps(db,p,row,scope,1)
    if not maps:raise HTTPException(409,'请先实际读取此范围并保存RGB预览，再登记为业务地图来源')
    generation=old['generation']+1 if old else 1;fixed=manifest(row,scope,generation);cap=register(db,p,fixed)
    entries[scope['id']]={'capability_id':cap.id,'generation':generation,'manifest_hash':cap.content_hash,
        'catalog_sha256':row.data['catalog_sha256'],'scope_hash':digest(scope),'registered_at':now().isoformat(),
        'sample_read_id':maps[0]['read_id'],'sample_scene_id':maps[0]['scene_id']}
    row.data={**row.data,'publications':entries}
    validate_capability(db,p,cap.id,cap.version);publish(db,p,cap.id,cap.version,'此范围实际WMS响应及本地RGB保存已核对；只查询保存结果，不认定业务状态')
    row.version+=1;audit(db,p,'wms.source_registered',row.id,{'scope_id':scope['id'],'capability_id':cap.id,'network_reads':0});db.commit();return view(row)


@router.get('/catalog')
def supported(p:P):
    from .config import settings
    return {'protocol_versions':['1.1.1','1.3.0'],'formats':['image/png'],'runtime_installed':settings().geo_python.is_file(),
            'runtime_hash':RECIPE_HASH,'cloud_fallback':False,'raw_satellite_bands':False,'max_dimension':2048,
            'secret_references':sorted({g['secret_ref'] for g in grants(p.workspace_id) if g['secret_ref']})}


@router.get('/connections')
def connections(p:P,db:DB):return [view(r) for r in db.scalars(select(WMSConnection).where(WMSConnection.workspace_id==p.workspace_id).order_by(WMSConnection.created_at.desc()))]


@router.post('/connections',status_code=201)
def create(body:Connection,p:P,db:DB):
    require(p,'admin')
    value=body.model_dump();value['endpoint']=endpoint(value['endpoint']);value['name']=value['name'].strip()
    if not value['name']:raise HTTPException(422,'请填写服务名称')
    key=value.pop('request_id');lock_request(db,p,key);sha=digest(value)
    old=db.scalar(select(WMSConnection).where(WMSConnection.workspace_id==p.workspace_id,WMSConnection.request_id==key))
    if old:
        if old.data['registration_hash']!=sha:raise HTTPException(409,'同一登记请求不能替换服务或凭据引用，原记录保留')
        return view(old)
    configuration={k:value[k] for k in ('endpoint','version','secret_ref')}
    configuration['workspace_id']=p.workspace_id;authorize(configuration,p.workspace_id,db)
    row=WMSConnection(workspace_id=p.workspace_id,request_id=key,data={'name':value['name'],'registration_hash':sha,
        'configuration':configuration,'configuration_hash':digest(configuration),'generation':1,'catalog':None,'catalog_evidence_id':None,
        'catalog_sha256':None,'scopes':{},'error':None,'history':[]})
    db.add(row);db.flush();queue(db,p,row,'read_wms_catalog');audit(db,p,'wms.connection_registered',row.id,{'cloud_calls':0});db.commit();return view(row)


@router.get('/connections/requests/{request_id}')
def connection_request(request_id:str,p:P,db:DB):
    row=db.scalar(select(WMSConnection).where(WMSConnection.workspace_id==p.workspace_id,WMSConnection.request_id==request_id))
    if not row:raise HTTPException(404,'此登记请求尚未保存，请使用原请求核对或重试')
    return view(row)


@router.post('/connections/{identity}/approve')
def approve(identity:str,body:Scope,p:P,db:DB):
    require(p,'admin');row=scoped(db,WMSConnection,identity,p,True);check_version(row,body.version)
    authorize(row.data['configuration'],p.workspace_id,db)
    if row.status not in {'catalog_ready','active'} or not body.confirm_read_only:raise HTTPException(409,'请先读取目录，并明确确认只读图层与范围')
    snap={'configuration':row.data['configuration'],'catalog_evidence_id':row.data['catalog_evidence_id'],
          'catalog_sha256':row.data['catalog_sha256'],'catalog_hash':row.data['catalog_hash']}
    fixed_catalog=check_catalog(db,p,snap);layer=next((l for l in fixed_catalog['layers'] if l['name']==body.layer),None)
    try:window=bbox(body.bbox,body.crs)
    except ValueError as error:raise HTTPException(422,str(error)) from None
    if not layer or body.crs not in layer['bounds'] or not within(window,layer['bounds'][body.crs]) or body.style and body.style not in layer['styles']:
        raise HTTPException(422,'图层、样式或范围未在固定服务目录中声明；不会自动调用其它地址')
    scope={'layer':body.layer,'title':layer['title'],'crs':body.crs,'bbox':window,'style':body.style}
    scope['id']=digest({'catalog_hash':row.data['catalog_hash'],'scope':scope})[:24]
    row.data={**row.data,'scopes':{**row.data['scopes'],scope['id']:scope}};row.status='active';row.version+=1
    audit(db,p,'wms.read_scope_confirmed',row.id,{'scope_id':scope['id'],'read_only':True});db.commit();return view(row)


@router.post('/connections/{identity}/pause')
def pause(identity:str,body:Version,p:P,db:DB):
    require(p,'admin');row=scoped(db,WMSConnection,identity,p,True);check_version(row,body.version)
    if row.status!='active':raise HTTPException(409,'此连接当前未启用')
    row.status='paused';row.version+=1;audit(db,p,'wms.connection_paused',row.id);db.commit();return view(row)


@router.post('/connections/{identity}/resume')
def resume(identity:str,body:Version,p:P,db:DB):
    require(p,'admin');row=scoped(db,WMSConnection,identity,p,True);check_version(row,body.version)
    if row.status!='paused' or not row.data['scopes']:raise HTTPException(409,'没有可恢复的已确认读取范围')
    endpoint(row.data['configuration']['endpoint']);authorize(row.data['configuration'],p.workspace_id,db)
    row.status='active';row.version+=1;audit(db,p,'wms.connection_resumed',row.id);db.commit();return view(row)


@router.post('/connections/{identity}/retry')
def retry_catalog(identity:str,body:Version,p:P,db:DB):
    require(p,'admin');row=scoped(db,WMSConnection,identity,p,True);check_version(row,body.version)
    if row.status not in {'failed','cancelled'}:raise HTTPException(409,'只有失败或取消的目录读取可按原连接重试')
    endpoint(row.data['configuration']['endpoint']);authorize(row.data['configuration'],p.workspace_id,db)
    row.data={**row.data,'generation':row.data['generation']+1,'error':None,
        'history':row.data['history']+[{'generation':row.data['generation'],'error':row.data.get('error'),'catalog_evidence_id':row.data.get('catalog_evidence_id')}]}
    row.status='queued';row.version+=1;queue(db,p,row,'read_wms_catalog');db.commit();return view(row)


@router.post('/connections/{identity}/cancel')
def cancel_catalog(identity:str,body:Version,p:P,db:DB):
    require(p,'admin');row=scoped(db,WMSConnection,identity,p,True);check_version(row,body.version)
    if row.status not in {'queued','running'}:raise HTTPException(409,'目录读取已结束，记录保留')
    row.status='cancelled';row.version+=1;db.commit();return view(row)


@router.post('/connections/{identity}/reads',status_code=201)
def create_read(identity:str,body:Read,p:P,db:DB):
    require(p,'admin','operator');connection=scoped(db,WMSConnection,identity,p,True)
    request=body.model_dump();request['bbox']=list(body.bbox);request_id=request.pop('request_id');lock_request(db,p,request_id)
    sha=digest({'connection_id':identity,'request':request});old=db.scalar(select(WMSRead).where(WMSRead.workspace_id==p.workspace_id,WMSRead.request_id==request_id))
    if old:
        if old.data['submission_hash']!=sha:raise HTTPException(409,'同一地图读取请求不能替换范围或分辨率，请先核对原请求')
        return view(old)
    if connection.status!='active':raise HTTPException(409,'WMS连接尚未确认读取范围或已暂停')
    authorize(connection.data['configuration'],p.workspace_id,db)
    endpoint(connection.data['configuration']['endpoint']);scope=connection.data['scopes'].get(body.scope_id)
    if not scope:raise HTTPException(422,'请选择已确认的只读图层范围')
    try:request['bbox']=bbox(body.bbox,scope['crs'])
    except ValueError as error:raise HTTPException(422,str(error)) from None
    if not within(request['bbox'],scope['bbox']):raise HTTPException(422,'地图读取超过管理员已确认范围')
    if not settings_runtime():raise HTTPException(409,'本地地图转换环境尚未安装；不会转云')
    snap=frozen(connection,scope);check_catalog(db,p,snap)
    row=WMSRead(workspace_id=p.workspace_id,connection_id=identity,request_id=request_id,data={'submission_hash':sha,'request':request,'request_hash':digest(request),
        'connection_snapshot':snap,'connection_snapshot_hash':digest(snap),'runtime_hash':RECIPE_HASH,'generation':1,
        'response':None,'result':None,'result_hash':None,'scene_id':None,'error':None,'history':[],'rejected_responses':[]})
    db.add(row);db.flush();queue(db,p,row,'capture_wms_map');audit(db,p,'wms.map_requested',row.id,{'cloud_calls':0});db.commit();return view(row)


def settings_runtime():
    from .config import settings
    return settings().geo_python.is_file()


@router.get('/reads')
def reads(p:P,db:DB):return [view(r) for r in db.scalars(select(WMSRead).where(WMSRead.workspace_id==p.workspace_id).order_by(WMSRead.created_at.desc()).limit(100))]


@router.get('/reads/requests/{request_id}')
def read_request(request_id:str,p:P,db:DB):
    row=db.scalar(select(WMSRead).where(WMSRead.workspace_id==p.workspace_id,WMSRead.request_id==request_id))
    if not row:raise HTTPException(404,'此读取请求尚未保存，请用原请求核对或重试')
    return view(row)


@router.get('/reads/{identity}')
def detail(identity:str,p:P,db:DB):return view(scoped(db,WMSRead,identity,p))


@router.post('/reads/{identity}/cancel')
def cancel(identity:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,WMSRead,identity,p,True);check_version(row,body.version)
    if row.status not in {'queued','running'}:raise HTTPException(409,'地图读取已结束，原件和记录保留')
    row.status='cancelled';row.version+=1;db.commit();return view(row)


@router.post('/reads/{identity}/retry')
def retry(identity:str,body:Version,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,WMSRead,identity,p,True);check_version(row,body.version)
    if row.status not in {'failed','cancelled'}:raise HTTPException(409,'只有失败或取消的请求可按原范围重试')
    c=scoped(db,WMSConnection,row.connection_id,p)
    authorize(c.data['configuration'],p.workspace_id,db)
    if c.status!='active' or frozen(c,c.data['scopes'].get(row.data['request']['scope_id'],{}))!=row.data['connection_snapshot']:
        raise HTTPException(409,'原连接或授权已变化，请先恢复；不能自动更换图层或范围')
    history=row.data['history']+[{'generation':row.data['generation'],'error':row.data.get('error'),'status':row.status,'response':copy.deepcopy(row.data.get('response'))}]
    row.status='queued';row.version+=1;row.data={**row.data,'generation':row.data['generation']+1,'error':None,'history':history};queue(db,p,row,'capture_wms_map');db.commit();return view(row)


@router.get('/reads/{identity}/response')
def response(identity:str,p:P,db:DB,workspace_id:str|None=None):
    p=workspace_principal(db,p,workspace_id)
    from .map_access import require_config
    require_config(db,p)
    row=scoped(db,WMSRead,identity,p)
    if not row.data.get('response'):raise HTTPException(409,'尚无已保存的PNG响应')
    saved=row.data['response'];ev,path=path_for(db,p,saved['evidence_id'])
    if ev.sha256!=saved['sha256']:raise HTTPException(409,'WMS响应摘要不一致')
    return FileResponse(path,media_type='image/png',filename='wms-original-response.png',headers={'X-Content-SHA256':saved['sha256'],'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.get('/reads/{identity}/export')
def export(identity:str,p:P,db:DB,workspace_id:str|None=None):
    p=workspace_principal(db,p,workspace_id)
    from .map_access import require_config
    require_config(db,p)
    row=scoped(db,WMSRead,identity,p)
    if row.status!='succeeded':raise HTTPException(409,'地图读取尚未完成，进度和已有响应保留')
    origin=source_provenance(db,p,scoped(db,GeoScene,row.data['scene_id'],p))
    return {'schema':'wms-read-receipt-v1','read_id':row.id,'status':row.status,'provenance':origin,'request':row.data['request'],
            'result':row.data['result'],'cloud_calls':0,'raw_satellite_bands':False,'source_capture_time_known':False}
