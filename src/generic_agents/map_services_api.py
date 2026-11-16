"""Workspace-isolated read-only WMTS / ArcGIS map connection.

Uses explicit operation identifiers and internal non-claimable receipts. It
never follows catalog links, redirects, or arbitrary browser-supplied paths.
"""
import base64, hashlib, ipaddress, struct, time
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from urllib.parse import urlsplit
import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import Field, StrictBool
from sqlalchemy import select, text, and_, or_
from .contracts import Contract, digest
from .models import Job
from .services import require, audit
from .setup_api import P, DB
from .wms import endpoint as authorised_endpoint, authorize
from .connectors import headers
from .map_services_protocol import wmts_catalog, arcgis_catalog, arcgis_image_catalog, wmts_params, arcgis_params, LIMIT

router=APIRouter(prefix='/api/map-services')
KIND='map_service_connection'; READ='map_service_preview'; CAT='map_service_catalog'; CANCEL='map_service_cancelled'

def _key(kind,rid): return f'{kind}:{rid}'
def _lock(db,p,key):
    lock=int.from_bytes(hashlib.sha256((p.workspace_id+':'+key).encode()).digest()[:8], 'big',signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':lock})
def _job(db,p,key): return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key))
def _connection(db,p,identity):
    row=db.scalar(select(Job).where(Job.id==identity,Job.workspace_id==p.workspace_id,Job.kind==KIND,Job.status=='internal'))
    if not row: raise HTTPException(404,'没有找到此地图服务连接')
    return row
def _view(row):
    d=row.data
    return {'id':row.id,'request_id':d['request_id'],'version':row.version,'name':d['name'],
            'protocol':d['protocol'],'endpoint':d['endpoint'],'secret_ref':d['secret_ref'],
            'catalog':d.get('catalog'),'catalog_sha256':d.get('catalog_sha256'),'selection':d.get('selection'), 'preview_id':d.get('preview_id'),
            'catalog_request_id':d.get('catalog_request_id'),'catalog_previous_version':d.get('catalog_previous_version')}
def _fetch(conf,params,limit,suffix='',db=None,deadline=None):
    authorize(conf,conf['workspace_id'],db); url=_url(conf['endpoint'],'wmts')
    auth=headers({'secret_ref':conf.get('secret_ref')}); started=time.monotonic()
    if deadline is not None and started>=deadline: raise HTTPException(504,'地图目录读取超时，请核对服务后重试')
    try:
        remaining=deadline-started if deadline is not None else 12
        with httpx.Client(timeout=httpx.Timeout(min(12,remaining),connect=min(3,remaining)),follow_redirects=False,trust_env=False) as client:
            with client.stream('GET',url+suffix,params=params,headers={**auth,'Accept':'application/json,application/xml,text/xml,image/png','Accept-Encoding':'identity'}) as r:
                if 300<=r.status_code<400: raise HTTPException(502,'服务要求跳转；为保护授权范围，未跟随跳转')
                r.raise_for_status(); data=bytearray()
                if r.headers.get('content-encoding','').strip().lower() not in ('','identity'):raise HTTPException(502,'服务返回不支持的压缩编码')
                for chunk in r.iter_raw(chunk_size=65536):
                    data.extend(chunk)
                    if len(data)>limit or time.monotonic()-started>25 or deadline is not None and time.monotonic()>deadline: raise HTTPException(502,'服务响应超出大小或时间限制')
                return bytes(data),r.headers.get('content-type','').split(';')[0].lower()
    except httpx.HTTPError: raise HTTPException(502,'地图服务读取失败，请核对地址、网络及访问授权后重试') from None

def _conf(row,p):
    d=row.data
    return {'workspace_id':p.workspace_id,'endpoint':d['endpoint'],'secret_ref':d['secret_ref']}

def _url(value,protocol):
    parsed=urlsplit(value)
    try: ipaddress.ip_address(parsed.hostname or '')
    except ValueError:raise HTTPException(422,'为保护访问凭据，当前仅允许固定 IP 地址；域名服务请通过经授权的本地受控代理接入') from None
    if parsed.query or parsed.fragment: raise HTTPException(422,'服务地址不允许包含查询参数或凭据')
    value=authorised_endpoint(value)
    if protocol=='arcgis' and not urlsplit(value).path.rstrip('/').endswith('/MapServer'):
        raise HTTPException(422,'ArcGIS 地址需指向具体 MapServer 服务')
    if protocol=='arcgis_image' and not urlsplit(value).path.rstrip('/').endswith('/ImageServer'):
        raise HTTPException(422,'ArcGIS 地址需指向具体 ImageServer 服务')
    return value.rstrip('/')

class Connect(Contract):
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{12,100}$')
    name:str=Field(min_length=1,max_length=200)
    protocol:str=Field(pattern='^(wmts|arcgis|arcgis_image)$')
    endpoint:str=Field(min_length=1,max_length=1800)
    secret_ref:str=Field(default='',pattern=r'^(GA_CONNECTOR_SECRET_[A-Z0-9_]+)?$')
class Catalog(Contract):
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{12,100}$')
    version:int=Field(ge=1)
class Capture(Catalog):
    selection:dict
    confirm_read_only:StrictBool
class Resolve(Contract):
    kind:str=Field(pattern='^(create|catalog|preview)$')
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{12,100}$')
    connection_id:str=Field(default='',max_length=36)

@router.get('/connections')
def connections(p:P,db:DB):
    from .map_access import require_config
    require_config(db,p)
    return [_view(x) for x in db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==KIND,Job.status=='internal').order_by(Job.created_at.desc()).limit(100))]
@router.get('/connections/requests/{request_id}')
def lookup(request_id:str,p:P,db:DB):
    from .map_access import require_config
    require_config(db,p)
    row=_job(db,p,_key(KIND,request_id))
    if not row or row.kind!=KIND or row.status!='internal': raise HTTPException(404,'尚未找到原连接登记请求')
    return _view(row)
@router.post('/connections')
def create(body:Connect,response:Response,p:P,db:DB):
    require(p,'admin'); key=_key(KIND,body.request_id); _lock(db,p,key)
    raw=body.model_dump();raw['name']=raw['name'].strip()
    if not raw['name']:raise HTTPException(422,'请填写服务名称')
    sh=digest(raw); old=_job(db,p,key)
    if old:
        if old.kind==CANCEL:raise HTTPException(409,'此请求已安全结束，不能再次执行')
        if old.kind!=KIND or old.status!='internal' or old.data.get('submission_hash')!=sh: raise HTTPException(409,'请求标识已用于不同内容，原记录保留')
        response.status_code=200;return _view(old)
    raw['endpoint']=_url(raw['endpoint'],body.protocol)
    authorize({'workspace_id':p.workspace_id,'endpoint':raw['endpoint'],'secret_ref':raw['secret_ref']},p.workspace_id,db)
    row=Job(workspace_id=p.workspace_id,kind=KIND,status='internal',idempotency_key=key,data={**raw,'submission_hash':sh,'catalog':None,'selection':None,'preview_id':None})
    db.add(row);db.flush();audit(db,p,'map_service.connection_saved',row.id,{'protocol':body.protocol});db.commit();response.status_code=201;return _view(row)

@router.post('/connections/{identity}/catalog')
def catalog(identity:str,body:Catalog,p:P,db:DB):
    require(p,'admin'); _lock(db,p,'map-connection:'+identity); row=_connection(db,p,identity)
    receipt_key=_key(CAT,identity+':'+body.request_id); receipt=_job(db,p,receipt_key)
    if receipt and receipt.kind==CANCEL:raise HTTPException(409,'此目录请求已安全结束')
    if receipt and receipt.kind!=CAT:raise HTTPException(409,'此目录回执无法核对')
    if row.data.get('catalog_request_id')==body.request_id:
        if row.data.get('catalog_previous_version')!=body.version:raise HTTPException(409,'原目录请求内容不一致')
        return _view(row)
    if row.version!=body.version:raise HTTPException(409,'连接已有新版本，请刷新核对')
    conf=_conf(row,p)
    raw,_=_fetch(conf,{'SERVICE':'WMTS','REQUEST':'GetCapabilities','VERSION':'1.0.0'} if row.data['protocol']=='wmts' else {'f':'json'},LIMIT,db=db)
    try: parsed=wmts_catalog(raw) if row.data['protocol']=='wmts' else (arcgis_image_catalog(raw) if row.data['protocol']=='arcgis_image' else arcgis_catalog(raw))
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    row.data={**row.data,'catalog':parsed,'catalog_sha256':hashlib.sha256(raw).hexdigest(),
              'catalog_request_id':body.request_id,'catalog_previous_version':body.version,'selection':None,'preview_id':None}
    row.version+=1;db.add(Job(workspace_id=p.workspace_id,kind=CAT,status='internal',idempotency_key=receipt_key,data={'connection_id':identity,'request_id':body.request_id,'version':body.version}));audit(db,p,'map_service.catalog_saved',row.id,{'protocol':row.data['protocol'],'layers':len(parsed['layers'])});db.commit();return _view(row)

@router.post('/connections/{identity}/previews')
def capture(identity:str,body:Capture,response:Response,p:P,db:DB):
    require(p,'admin','operator');key=_key(READ,body.request_id);_lock(db,p,key)
    sh=digest({'connection_id':identity,**body.model_dump()});old=_job(db,p,key)
    if old:
        if old.kind==CANCEL:raise HTTPException(409,'此读取请求已安全结束，不能再次执行')
        if old.kind!=READ or old.status!='internal' or old.data.get('submission_hash')!=sh:raise HTTPException(409,'读取请求标识已用于不同内容')
        response.status_code=200;return {'id':old.id,'connection_id':identity,'request_id':body.request_id,'sha256':old.data['sha256'],'submission':old.data['submission']}
    _lock(db,p,'map-connection:'+identity);row=_connection(db,p,identity)
    if row.version!=body.version:raise HTTPException(409,'连接或图层目录已更新，请刷新核对')
    if not body.confirm_read_only:raise HTTPException(422,'请先确认只读范围')
    cat=row.data.get('catalog')
    wanted={'layer','matrix_set','matrix','style','row','col'} if row.data['protocol']=='wmts' else {'layer','bbox'}
    if set(body.selection)!=wanted and not (row.data['protocol']=='wmts' and set(body.selection)==wanted|{'max_row','max_col'}):raise HTTPException(422,'地图选择参数不完整或包含不支持的字段')
    if not cat:raise HTTPException(409,'请先读取图层目录')
    try: params=wmts_params(cat,body.selection) if row.data['protocol']=='wmts' else arcgis_params(cat,body.selection)
    except (ValueError,TypeError,KeyError) as exc:raise HTTPException(422,str(exc)) from None
    if row.data['protocol']=='wmts' and 'max_row' in body.selection:
        selection=body.selection;end_row=selection['max_row'];end_col=selection['max_col'];start_row=selection['row'];start_col=selection['col']
        if any(type(v) is not int for v in (start_row,start_col,end_row,end_col)) or end_row<start_row or end_col<start_col or (end_row-start_row+1)*(end_col-start_col+1)>16:raise HTTPException(422,'每次最多读取 16 张相邻瓦片')
        try:wmts_params(cat,{**selection,'row':end_row,'col':end_col})
        except (ValueError,TypeError,KeyError) as exc:raise HTTPException(422,str(exc)) from None
        layer=next(x for x in cat['layers'] if x['id']==selection['layer']);matrix_set=next(x for x in layer['matrix_sets'] if x['id']==selection['matrix_set']);matrix=next(x for x in matrix_set['matrices'] if x['id']==selection['matrix'])
        tw,th=matrix['tile_width'],matrix['tile_height'];width=tw*(end_col-start_col+1);height=th*(end_row-start_row+1)
        if width>2048 or height>2048 or width*height>4194304:raise HTTPException(422,'拼接地图尺寸不能超过 2048×2048')
        canvas=Image.new('RGBA',(width,height));started=time.monotonic()
        for tile_row in range(start_row,end_row+1):
            for tile_col in range(start_col,end_col+1):
                if time.monotonic()-started>60:raise HTTPException(504,'瓦片读取超时，原请求保留，可重试')
                try: tile_params=wmts_params(cat,{**selection,'row':tile_row,'col':tile_col})
                except (ValueError,TypeError,KeyError) as exc:raise HTTPException(422,str(exc)) from None
                tile,content_type=_fetch(_conf(row,p),tile_params,8*1024*1024,db=db)
                try:
                    with Image.open(BytesIO(tile)) as im:
                        if content_type!='image/png' or im.format!='PNG' or im.size!=(tw,th):raise ValueError()
                        im.load();canvas.paste(im.convert('RGBA'),((tile_col-start_col)*tw,(tile_row-start_row)*th))
                except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombError):raise HTTPException(502,'瓦片损坏或尺寸与目录不一致') from None
        output=BytesIO();canvas.save(output,format='PNG');raw=output.getvalue();ctype='image/png'
        if len(raw)>8*1024*1024:raise HTTPException(422,'拼接地图超过保存大小限制')
    else:
        suffix='' if row.data['protocol']=='wmts' else ('/exportImage' if row.data['protocol']=='arcgis_image' else '/export')
        raw,ctype=_fetch(_conf(row,p),params,8*1024*1024,suffix,db=db)
    if ctype!='image/png' or len(raw)<33 or not raw.startswith(b'\x89PNG\r\n\x1a\n') or raw[12:16]!=b'IHDR':raise HTTPException(502,'服务未返回 PNG 地图，请核对服务与图层')
    width,height=struct.unpack('>II',raw[16:24])
    if not (1<=width<=2048 and 1<=height<=2048 and width*height<=4194304):raise HTTPException(502,'服务返回的地图尺寸超出限制')
    if row.data['protocol']=='wmts' and 'max_row' not in body.selection:
        layer=next(x for x in cat['layers'] if x['id']==body.selection['layer']);matrix_set=next(x for x in layer['matrix_sets'] if x['id']==body.selection['matrix_set']);matrix=next(x for x in matrix_set['matrices'] if x['id']==body.selection['matrix'])
        if (width,height)!=(matrix['tile_width'],matrix['tile_height']):raise HTTPException(502,'瓦片尺寸与服务目录不一致')
    try:
        with Image.open(BytesIO(raw)) as img:
            if img.format!='PNG' or img.size!=(width,height):raise ValueError()
            img.verify()
        with Image.open(BytesIO(raw)) as img:img.load()
    except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombError):raise HTTPException(502,'服务返回的 PNG 已损坏或无法完整解码') from None
    saved=Job(workspace_id=p.workspace_id,kind=READ,status='internal',idempotency_key=key,
              data={'request_id':body.request_id,'submission_hash':sh,'connection_id':identity,'selection':body.selection,
                    'catalog_sha256':row.data['catalog_sha256'],'submission':body.model_dump(),'sha256':hashlib.sha256(raw).hexdigest(),
                    'png':base64.b64encode(raw).decode('ascii')})
    db.add(saved);db.flush();row.data={**row.data,'selection':body.selection,'preview_id':saved.id};row.version+=1
    audit(db,p,'map_service.preview_saved',saved.id,{'connection_id':identity,'protocol':row.data['protocol']});db.commit();response.status_code=201
    return {'id':saved.id,'connection_id':identity,'request_id':body.request_id,'sha256':saved.data['sha256'],'submission':saved.data['submission']}

@router.post('/operations/resolve')
def resolve(body:Resolve,p:P,db:DB):
    # This endpoint is an atomic barrier against a late-arriving request. It
    # either observes its committed receipt or records a permanent stop marker.
    require(p,'admin' if body.kind in {'create','catalog'} else 'operator','admin')
    if body.kind in {'catalog','preview'}:
        if not body.connection_id:raise HTTPException(422,'缺少原连接标识')
        _connection(db,p,body.connection_id)
    if body.kind=='create':
        key=_key(KIND,body.request_id);expected=KIND; _lock(db,p,key)
    elif body.kind=='preview':
        key=_key(READ,body.request_id);expected=READ; _lock(db,p,key)
    else:
        key=_key(CAT,body.connection_id+':'+body.request_id);expected=CAT; _lock(db,p,'map-connection:'+body.connection_id)
    old=_job(db,p,key)
    if old:
        if old.status!='internal' or old.kind not in {expected,CANCEL}:raise HTTPException(409,'原请求回执无法核对')
        if body.kind!='create' and old.data.get('connection_id')!=body.connection_id:raise HTTPException(409,'原请求与连接不一致')
        result={'state':'cancelled' if old.kind==CANCEL else 'completed','request_id':body.request_id}
        if old.kind==KIND: result['record']=_view(old)
        elif old.kind==READ: result['submission']=old.data['submission']
        elif old.kind==CAT: result['version']=old.data['version']
        return result
    db.add(Job(workspace_id=p.workspace_id,kind=CANCEL,status='internal',idempotency_key=key,
               data={'request_id':body.request_id,'connection_id':body.connection_id,'operation_kind':body.kind}))
    audit(db,p,'map_service.operation_stopped',body.request_id,{'operation_kind':body.kind});db.commit()
    return {'state':'cancelled','request_id':body.request_id}

@router.get('/previews/requests/{request_id}')
def preview_lookup(request_id:str,p:P,db:DB):
    from .map_access import require_config
    require_config(db,p)
    row=_job(db,p,_key(READ,request_id))
    if not row or row.kind!=READ or row.status!='internal':raise HTTPException(404,'尚未找到此读取请求')
    return {'id':row.id,'connection_id':row.data['connection_id'],'request_id':request_id,'sha256':row.data['sha256'],'submission':row.data['submission']}

@router.get('/connections/{identity}/previews')
def previews(identity:str,p:P,db:DB,before:str=''):
    from .map_access import require_config
    require_config(db,p)
    _connection(db,p,identity)
    query=select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==READ,Job.status=='internal',Job.data['connection_id'].astext==identity)
    if before:
        cursor=db.scalar(select(Job).where(Job.id==before,Job.workspace_id==p.workspace_id,Job.kind==READ,Job.status=='internal',Job.data['connection_id'].astext==identity))
        if not cursor:raise HTTPException(404,'地图历史位置不存在或不在此连接中')
        query=query.where(or_(Job.created_at<cursor.created_at,and_(Job.created_at==cursor.created_at,Job.id<cursor.id)))
    rows=db.scalars(query.order_by(Job.created_at.desc(),Job.id.desc()).limit(50))
    return [{'id':row.id,'request_id':row.data['request_id'],'selection':row.data['selection'],
             'sha256':row.data['sha256'],'created_at':row.created_at.isoformat()} for row in rows]

@router.get('/previews/{identity}/image')
def image(identity:str,p:P,db:DB):
    from .map_access import require_config
    require_config(db,p)
    return image_internal(identity,p,db)

def image_internal(identity:str,p:P,db:DB):
    row=db.scalar(select(Job).where(Job.id==identity,Job.workspace_id==p.workspace_id,Job.kind==READ,Job.status=='internal'))
    if not row:raise HTTPException(404,'没有找到此地图读取记录')
    try:raw=base64.b64decode(row.data['png'],validate=True)
    except (ValueError,KeyError):raise HTTPException(409,'已保存地图损坏，请联系管理员核对') from None
    if hashlib.sha256(raw).hexdigest()!=row.data.get('sha256'):raise HTTPException(409,'地图原件摘要不一致')
    return Response(content=raw,media_type='image/png',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
