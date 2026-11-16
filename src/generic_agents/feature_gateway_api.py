"""Read-only ArcGIS FeatureServer, scoped to an authorised workspace.

This stores vector features, never renders or labels them as map imagery.
"""
import hashlib,json,math,re,time
from urllib.parse import urlsplit
from fastapi import APIRouter,HTTPException,Response
from pydantic import Field,StrictBool
from sqlalchemy import select
from .contracts import Contract,Manifest,digest
from .models import Job,Capability
from .security import require
from .setup_api import P,DB
from .services import audit,register,validate_capability,publish,published
from .wms import authorize
from .wms_protocol import bbox
from .map_services_api import _fetch,_url,_lock
from .map_access import require_config

router=APIRouter(prefix='/api/feature-services')
CON='feature_service_connection';READ='feature_service_read';STOP='feature_service_stopped';CAT='feature_service_catalog';PUB='feature_source_publication';ATTEMPT='feature_source_attempt'
def key(kind,rid):return kind+':'+rid
def job(db,p,k):return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==k))
def connection(db,p,identity):
 row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==identity,Job.kind==CON,Job.status=='internal'))
 if not row:raise HTTPException(404,'未找到此要素服务')
 return row
def view(row):
 d=row.data;return {'id':row.id,'version':row.version,'name':d['name'],'endpoint':d['endpoint'],'request_id':d['request_id'],'secret_ref':d['secret_ref'],'catalog':d.get('catalog'),'catalog_request_id':d.get('catalog_request_id'),'catalog_previous_version':d.get('catalog_previous_version')}
def conf(row,p):return {'workspace_id':p.workspace_id,'endpoint':row.data['endpoint'],'secret_ref':row.data['secret_ref']}
def parse(raw):
 try:
  val=json.loads(raw)
  if not isinstance(val,dict) or 'error' in val:raise ValueError()
  return val
 except (ValueError,TypeError):raise HTTPException(502,'要素服务返回无效数据') from None
def geometry_intersects(geometry,area):
 """Validate a bounded RFC 7946 geometry and test intersection with an envelope.

 Query semantics are intersection rather than containment: a line or polygon
 may legitimately extend outside the authorised query envelope.
 """
 if not isinstance(geometry,dict) or 'crs' in geometry or not isinstance(geometry.get('type'),str):raise ValueError('geometry')
 west,south,east,north=area;counter=[0]
 def point(value):
  counter[0]+=1
  if counter[0]>10000 or not isinstance(value,list) or len(value) not in (2,3) or any(type(x) not in (int,float) or not math.isfinite(x) for x in value):raise ValueError('coordinate')
  if not (-180<=value[0]<=180 and -90<=value[1]<=90):raise ValueError('coordinate range')
  return (float(value[0]),float(value[1]))
 def inside(p):return west<=p[0]<=east and south<=p[1]<=north
 def segment(a,b):
  # Liang–Barsky clipping handles endpoints and intersections on boundaries.
  dx,dy=b[0]-a[0],b[1]-a[1];lo,hi=0.0,1.0
  for divisor,offset in ((-dx,a[0]-west),(dx,east-a[0]),(-dy,a[1]-south),(dy,north-a[1])):
   if divisor==0:
    if offset<0:return False
   else:
    t=offset/divisor
    if divisor<0:lo=max(lo,t)
    else:hi=min(hi,t)
    if lo>hi:return False
  return True
 def line(values,ring=False):
  if not isinstance(values,list) or len(values)<(4 if ring else 2) or len(values)>(512 if ring else 10000):raise ValueError('line')
  if ring and values[0]!=values[-1]:raise ValueError('unclosed ring')
  pts=[point(v) for v in values]
  if any(abs(a[0]-b[0])>180 for a,b in zip(pts,pts[1:])):raise ValueError('antimeridian')
  if ring and abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(pts,pts[1:])))<1e-18:raise ValueError('degenerate ring')
  return pts
 def line_hit(pts):return any(inside(p) for p in pts) or any(segment(a,b) for a,b in zip(pts,pts[1:]))
 def in_ring(p,pts):
  x,y=p;yes=False
  for a,b in zip(pts,pts[1:]):
   if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:yes=not yes
  return yes
 def intersects_segments(a,b,c,d):
  def orient(x,y,z):return (y[0]-x[0])*(z[1]-x[1])-(y[1]-x[1])*(z[0]-x[0])
  def on(x,y,z):return min(x[0],y[0])<=z[0]<=max(x[0],y[0]) and min(x[1],y[1])<=z[1]<=max(x[1],y[1])
  p,q,r,s=orient(a,b,c),orient(a,b,d),orient(c,d,a),orient(c,d,b)
  if ((p>0 and q<0) or (p<0 and q>0)) and ((r>0 and s<0) or (r<0 and s>0)):return True
  return (p==0 and on(a,b,c)) or (q==0 and on(a,b,d)) or (r==0 and on(c,d,a)) or (s==0 and on(c,d,b))
 def polygon(values):
  if not isinstance(values,list) or not values or len(values)>20:raise ValueError('polygon')
  rings=[line(r,True) for r in values]
  if sum(len(r) for r in rings)>1000:raise ValueError('polygon complexity')
  for ring in rings:
   edges=list(zip(ring,ring[1:]));last=len(edges)-1
   for i,(a,b) in enumerate(edges):
    for j in range(i+1,len(edges)):
     if j==i+1 or (i==0 and j==last):continue
     if intersects_segments(a,b,*edges[j]):raise ValueError('self-intersecting ring')
  for i,ring in enumerate(rings[1:],1):
   if not in_ring(ring[0],rings[0]):raise ValueError('hole outside exterior')
   for other in rings[:i]:
    if any(intersects_segments(a,b,c,d) for a,b in zip(ring,ring[1:]) for c,d in zip(other,other[1:])):raise ValueError('intersecting rings')
   if any(in_ring(ring[0],other) or in_ring(other[0],ring) for other in rings[1:i]):raise ValueError('overlapping holes')
  if any(line_hit(r) for r in rings):return True
  center=((west+east)/2,(south+north)/2)
  return in_ring(center,rings[0]) and not any(in_ring(center,hole) for hole in rings[1:])
 kind=geometry['type'];coords=geometry.get('coordinates')
 if kind=='Point':return inside(point(coords))
 if kind=='MultiPoint':
  if not isinstance(coords,list) or not coords:raise ValueError('multipoint')
  pts=[point(p) for p in coords];return any(inside(p) for p in pts)
 if kind=='LineString':return line_hit(line(coords))
 if kind=='MultiLineString':
  if not isinstance(coords,list) or not coords:raise ValueError('multiline')
  lines=[line(x) for x in coords];return any(line_hit(x) for x in lines)
 if kind=='Polygon':return polygon(coords)
 if kind=='MultiPolygon':
  if not isinstance(coords,list) or not coords:raise ValueError('multipolygon')
  # Evaluate all parts, including those after an intersecting part, to avoid
  # accepting malformed or unbounded coordinates through short-circuiting.
  hits=[polygon(x) for x in coords];return any(hits)
 raise ValueError('unsupported geometry')
def field_name(value):return isinstance(value,str) and bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,127}',value))
class Connect(Contract):
 request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{12,100}$');name:str=Field(min_length=1,max_length=200);endpoint:str=Field(min_length=1,max_length=1800);secret_ref:str=Field(default='',pattern=r'^(GA_CONNECTOR_SECRET_[A-Z0-9_]+)?$')
class Version(Contract):request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{12,100}$');version:int=Field(ge=1)
class Read(Version):
 layer:int=Field(ge=0);fields:list[str]=Field(min_length=1,max_length=30);bounds:tuple[float,float,float,float];confirm_read_only:StrictBool
class Resolve(Contract):
 kind:str=Field(pattern='^(create|catalog|read)$');request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{12,100}$');connection_id:str=Field(default='',max_length=36)
class Publication(Contract):
 request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{12,100}$');read_id:str=Field(min_length=1,max_length=100)
@router.get('/connections')
def connections(p:P,db:DB):
 require_config(db,p);return [view(x) for x in db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==CON,Job.status=='internal').order_by(Job.created_at.desc()).limit(100))]
@router.post('/connections')
def create(body:Connect,response:Response,p:P,db:DB):
 require(p,'admin');k=key(CON,body.request_id);_lock(db,p,k);value=body.model_dump(mode='json');value['name']=value['name'].strip();sh=digest(value)
 if not value['name']:raise HTTPException(422,'请填写服务名称')
 old=job(db,p,k)
 if old:
  if old.kind==STOP:raise HTTPException(409,'原请求已结束')
  if old.kind!=CON or old.data.get('submission_hash')!=sh:raise HTTPException(409,'原请求内容不一致')
  return view(old)
 u=urlsplit(value['endpoint'])
 if u.query or u.fragment or not u.path.rstrip('/').endswith('/FeatureServer'):raise HTTPException(422,'请填写具体 FeatureServer 地址，不含凭据或查询参数')
 value['endpoint']=_url(value['endpoint'],'wmts');authorize({'workspace_id':p.workspace_id,'endpoint':value['endpoint'],'secret_ref':value['secret_ref']},p.workspace_id,db)
 row=Job(workspace_id=p.workspace_id,kind=CON,status='internal',idempotency_key=k,data={**value,'submission_hash':sh,'catalog':None});db.add(row);db.flush();audit(db,p,'feature_service.created',row.id,{});db.commit();response.status_code=201;return view(row)
@router.post('/connections/{identity}/catalog')
def catalog(identity:str,body:Version,p:P,db:DB):
 require(p,'admin');_lock(db,p,'feature-connection:'+identity);row=connection(db,p,identity);k=key(CAT,identity+':'+body.request_id);old=job(db,p,k)
 if old:
  if old.kind==STOP:raise HTTPException(409,'原请求已结束')
  if old.kind!=CAT or old.data.get('version')!=body.version:raise HTTPException(409,'目录请求不一致')
  if row.data.get('catalog_request_id')!=body.request_id:raise HTTPException(409,'目录已更新，请重新读取')
  return view(row)
 if row.version!=body.version:raise HTTPException(409,'服务配置已更新，请重新读取')
 deadline=time.monotonic()+30
 root=parse(_fetch(conf(row,p),{'f':'json'},1024*1024,db=db,deadline=deadline)[0]);layers=root.get('layers')
 if not isinstance(layers,list) or not layers or len(layers)>20:raise HTTPException(422,'当前支持包含 1 至 20 个图层的 FeatureServer')
 result=[];seen=set()
 for entry in layers:
  lid=entry.get('id') if isinstance(entry,dict) else None
  if type(lid)!=int or lid<0 or lid in seen:raise HTTPException(422,'图层编号无效或重复')
  seen.add(lid);meta=parse(_fetch(conf(row,p),{'f':'json'},1024*1024,'/'+str(lid),db=db,deadline=deadline)[0]);ext=meta.get('extent') or {};sr=ext.get('spatialReference') or {}
  if sr.get('latestWkid',sr.get('wkid'))!=4326:continue
  try:bounds=bbox([ext[k] for k in ('xmin','ymin','xmax','ymax')],'EPSG:4326')
  except (KeyError,ValueError,TypeError):continue
  fields=meta.get('fields');fields=fields if isinstance(fields,list) else [];clean=[]
  for f in fields[:100]:
   if isinstance(f,dict) and field_name(f.get('name')):clean.append({'name':f['name'],'alias':str(f.get('alias') or f['name'])[:150],'type':str(f.get('type') or '')[:80]})
  if not clean or len({x['name'] for x in clean})!=len(clean):continue
  result.append({'id':lid,'name':str(meta.get('name') or entry.get('name') or f'图层 {lid}')[:200],'fields':clean,'bounds':bounds})
 if not result:raise HTTPException(422,'没有可读取的 EPSG:4326 图层及字段')
 row.data={**row.data,'catalog':{'layers':result},'catalog_request_id':body.request_id,'catalog_previous_version':body.version};row.version+=1;db.add(Job(workspace_id=p.workspace_id,kind=CAT,status='internal',idempotency_key=k,data={'version':body.version,'connection_id':identity}));audit(db,p,'feature_service.catalog',row.id,{'layers':len(result)});db.commit();return view(row)
@router.post('/connections/{identity}/reads')
def read(identity:str,body:Read,response:Response,p:P,db:DB):
 require(p,'admin');k=key(READ,body.request_id);_lock(db,p,k);submission=body.model_dump(mode='json');sh=digest({'connection_id':identity,**submission});old=job(db,p,k)
 if old:
  if old.kind==STOP:raise HTTPException(409,'原请求已结束')
  if old.kind!=READ or old.data.get('submission_hash')!=sh:raise HTTPException(409,'要素读取请求不一致')
  return {'id':old.id,'request_id':body.request_id,'submission':old.data['submission']}
 _lock(db,p,'feature-connection:'+identity);row=connection(db,p,identity)
 if row.version!=body.version:raise HTTPException(409,'服务目录已更新，请重新选择')
 if not body.confirm_read_only:raise HTTPException(422,'请确认只读范围')
 layers=(row.data.get('catalog') or {}).get('layers',[]);layer=next((x for x in layers if x['id']==body.layer),None)
 if not layer or len(set(body.fields))!=len(body.fields) or not set(body.fields).issubset({x['name'] for x in layer['fields']}):raise HTTPException(422,'字段或图层不在目录中')
 try:area=bbox(body.bounds,'EPSG:4326')
 except (ValueError,TypeError):raise HTTPException(422,'经纬度范围无效') from None
 if any(area[i]<layer['bounds'][i] for i in (0,1)) or any(area[i]>layer['bounds'][i] for i in (2,3)):raise HTTPException(422,'范围超出服务声明范围')
 env=json.dumps({'xmin':area[0],'ymin':area[1],'xmax':area[2],'ymax':area[3],'spatialReference':{'wkid':4326}},separators=(',',':'))
 params={'f':'geojson','where':'1=1','geometryType':'esriGeometryEnvelope','geometry':env,'inSR':'4326','outSR':'4326','spatialRel':'esriSpatialRelIntersects','outFields':','.join(body.fields),'returnGeometry':'true','resultRecordCount':'200'}
 raw,_=_fetch(conf(row,p),params,2*1024*1024,'/'+str(body.layer)+'/query',db=db);value=parse(raw)
 if value.get('type')!='FeatureCollection' or 'crs' in value or not isinstance(value.get('features'),list) or len(value['features'])>200:raise HTTPException(502,'服务未返回符合限制的 WGS 84 GeoJSON')
 for f in value['features']:
  if not isinstance(f,dict) or 'crs' in f or f.get('type')!='Feature' or not isinstance(f.get('properties'),dict) or set(f['properties'])-set(body.fields):raise HTTPException(502,'要素字段不符合选择范围')
  if any(not (x is None or type(x) in (str,bool,int,float)) or type(x)==str and len(x)>10000 or type(x) in (int,float) and not math.isfinite(x) for x in f['properties'].values()):raise HTTPException(502,'要素字段值无效')
  try:
   if not geometry_intersects(f.get('geometry'),area):raise ValueError()
  except (ValueError,TypeError,OverflowError,RecursionError):raise HTTPException(502,'服务返回的要素坐标无效或不在所选范围内') from None
 rowdata={'request_id':body.request_id,'connection_id':identity,'submission_hash':sh,'submission':submission,'features':value,'sha256':hashlib.sha256(raw).hexdigest(),'data_hash':digest(value),'truncated':bool(value.get('exceededTransferLimit'))}
 out=Job(workspace_id=p.workspace_id,kind=READ,status='internal',idempotency_key=k,data=rowdata);db.add(out);db.flush();audit(db,p,'feature_service.read',out.id,{'count':len(value['features'])});db.commit();response.status_code=201;return {'id':out.id,'request_id':body.request_id,'submission':submission}
@router.get('/connections/{identity}/reads')
def history(identity:str,p:P,db:DB):
 require_config(db,p);connection(db,p,identity);rows=db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==READ,Job.status=='internal',Job.data['connection_id'].astext==identity).order_by(Job.created_at.desc()).limit(100))
 return [{'id':r.id,'request_id':r.data['request_id'],'count':len(r.data['features']['features']),'truncated':r.data['truncated'],'created_at':r.created_at.isoformat()} for r in rows if r.data.get('connection_id')==identity]
@router.get('/reads/{identity}')
def result(identity:str,p:P,db:DB):
 require_config(db,p);row=read_record(db,p,identity)
 return {'id':row.id,'submission':row.data['submission'],'features':row.data['features'],'truncated':row.data['truncated']}

def read_record(db,p,identity):
 row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==identity,Job.kind==READ,Job.status=='internal'))
 if not row:raise HTTPException(404,'未找到要素读取记录')
 if digest(row.data.get('features'))!=row.data.get('data_hash'):raise HTTPException(409,'保存的要素内容已损坏，请联系管理员核对')
 return row
def source_data(db,p,identity):
 row=read_record(db,p,identity);con=connection(db,p,row.data['connection_id']);c=conf(con,p)
 _url(c['endpoint'],'wmts');authorize(c,p.workspace_id,db)
 return row,con
def source_manifest(row,con):
 proof={'read_id':row.id,'connection_id':con.id,'data_hash':row.data['data_hash'],'selection':row.data['submission']}
 return Manifest(kind='source',namespace='saved_feature_data',name='read_'+row.id.replace('-',''),version='1.0.0',owner='已核对的要素服务',description=con.data['name']+'；已保存矢量要素，不代表实时状态或事实确认。',allowed_agent_roles=['perception','data'],input_schema={'type':'object','additionalProperties':False},output_schema={'type':'object','additionalProperties':False,'properties':{'features':{'type':'array'},'not_authoritative':{'const':True},'network_reads':{'const':0}},'required':['features','not_authoritative','network_reads']},execution={'adapter':'saved_feature_data','proof':proof,'side_effect':False},data_policy={'cloud_allowed':False,'external_allowed':False,'deployment':'local'},specification={'timezone':'Asia/Shanghai','domain_quality_verified':False})
def pubrow(db,p,identity):return job(db,p,key(PUB,identity))
def validate_manifest(db,p,value):
 try:
  row,con=source_data(db,p,value.execution['proof']['read_id']);proof=pubrow(db,p,row.id)
  if not proof or proof.data.get('manifest_hash')!=digest(value.model_dump(mode='json')) or source_manifest(row,con).model_dump(mode='json')!=value.model_dump(mode='json'):raise HTTPException(409,'要素来源与原记录不一致')
 except (ValueError,KeyError,TypeError):raise HTTPException(409,'要素来源结构无效') from None
def current_source(db,p,identity):
 cap=published(db,p,identity,'source');v=Manifest.model_validate(cap.data)
 if v.execution.get('adapter')!='saved_feature_data' or digest(cap.data)!=cap.content_hash:raise HTTPException(409,'要素来源契约无效')
 validate_manifest(db,p,v);row,con=source_data(db,p,v.execution['proof']['read_id']);receipt=pubrow(db,p,row.id)
 if receipt.data.get('capability_id')!=cap.id:raise HTTPException(409,'要素来源回执不一致')
 return cap,row,con
def resource_proof(db,p,resource):
 d=resource.get('details',{});cap,row,con=current_source(db,p,d.get('capability_id'))
 if resource.get('protocol')!='ArcGIS FeatureServer' or d.get('feature_connection_id')!=con.id or d.get('feature_read_id')!=row.id or d.get('manifest_hash')!=cap.content_hash:raise HTTPException(409,'业务要素与已发布来源不一致')
 return {'capability_id':cap.id,'manifest_hash':cap.content_hash,'read_id':row.id,'connection_id':con.id,'data_hash':row.data['data_hash'],'source_kind':'saved_feature_data','domain_quality_verified':False}
def registered_read(db,p,cap):
 _,row,_=current_source(db,p,cap.id);return {'features':row.data['features']['features'],'not_authoritative':True,'network_reads':0}
def pub_result(db,p,body,receipt):
 if receipt.kind!=ATTEMPT or receipt.data.get('actor_id')!=p.user_id or receipt.data.get('read_id')!=body.read_id:raise HTTPException(409,'原发布回执不一致')
 if receipt.data.get('state')=='cancelled':return {'state':'cancelled','request_id':body.request_id,'read_id':body.read_id}
 fixed=receipt.data.get('snapshot');valid=True
 if not isinstance(fixed,dict) or digest(fixed)!=receipt.data.get('snapshot_hash'):raise HTTPException(409,'原发布回执损坏')
 try:current_source(db,p,fixed['capability_id'])
 except (HTTPException,ValueError,KeyError,TypeError):valid=False
 return {'state':'created','request_id':body.request_id,**fixed,'source_valid':valid}
@router.post('/publications')
def publish_source(body:Publication,p:P,db:DB):
 require(p,'admin');_lock(db,p,key(ATTEMPT,body.request_id));attempt=job(db,p,key(ATTEMPT,body.request_id))
 if attempt:return pub_result(db,p,body,attempt)
 _lock(db,p,key(PUB,body.read_id));row,con=source_data(db,p,body.read_id);old=pubrow(db,p,row.id)
 if old:cap,_,_=current_source(db,p,old.data['capability_id'])
 else:
  manifest=source_manifest(row,con);cap=register(db,p,manifest);db.add(Job(workspace_id=p.workspace_id,kind=PUB,status='internal',idempotency_key=key(PUB,row.id),data={'capability_id':cap.id,'manifest_hash':cap.content_hash}));db.flush();validate_capability(db,p,cap.id,cap.version);publish(db,p,cap.id,cap.version,'确认已保存矢量要素供授权业务只读核对')
 fixed={'capability_id':cap.id,'manifest_hash':cap.content_hash,'read_id':row.id,'connection_id':con.id,'name':con.data['name'],'selection':row.data['submission']}
 attempt=Job(workspace_id=p.workspace_id,kind=ATTEMPT,status='internal',idempotency_key=key(ATTEMPT,body.request_id),data={'actor_id':p.user_id,'read_id':row.id,'state':'created','snapshot':fixed,'snapshot_hash':digest(fixed)});db.add(attempt);db.flush();db.commit();return pub_result(db,p,body,attempt)
@router.post('/publications/resolve')
def resolve_publication(body:Publication,p:P,db:DB):
 require(p,'admin');_lock(db,p,key(ATTEMPT,body.request_id));old=job(db,p,key(ATTEMPT,body.request_id))
 if old:return pub_result(db,p,body,old)
 read_record(db,p,body.read_id);row=Job(workspace_id=p.workspace_id,kind=ATTEMPT,status='internal',idempotency_key=key(ATTEMPT,body.request_id),data={'actor_id':p.user_id,'read_id':body.read_id,'state':'cancelled'});db.add(row);db.flush();db.commit();return pub_result(db,p,body,row)
@router.post('/resolve')
def resolve(body:Resolve,p:P,db:DB):
 require(p,'admin')
 if body.kind!='create':connection(db,p,body.connection_id)
 k=key(CON,body.request_id) if body.kind=='create' else key(READ,body.request_id) if body.kind=='read' else key(CAT,body.connection_id+':'+body.request_id)
 _lock(db,p,'feature-connection:'+body.connection_id if body.kind=='catalog' else k);old=job(db,p,k)
 if old:
  if old.kind==STOP:return {'state':'cancelled'}
  if body.kind=='create' and old.kind==CON:return {'state':'completed','record':view(old)}
  if body.kind=='read' and old.kind==READ and old.data.get('connection_id')==body.connection_id:return {'state':'completed','submission':old.data['submission'],'id':old.id}
  if body.kind=='catalog' and old.kind==CAT and old.data.get('connection_id')==body.connection_id:return {'state':'completed','version':old.data['version']}
  raise HTTPException(409,'原请求回执无法核对')
 db.add(Job(workspace_id=p.workspace_id,kind=STOP,status='internal',idempotency_key=k,data={'connection_id':body.connection_id}));db.commit();return {'state':'cancelled'}
