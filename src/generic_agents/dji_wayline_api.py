"""Private immutable DJI route-file library, without flight dispatch capability."""
from __future__ import annotations
from datetime import timezone
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import UUID
from fastapi import APIRouter,File,Form,HTTPException,Response,UploadFile
from sqlalchemy import select
from .dji_wayline_archive import MAX_ARCHIVE_BYTES,inspect_kmz
from .evidence import evidence_bytes,save_immutable
from .ftps_gateway_api import _current_admin,_lock
from .models import Job
from .services import audit,require,scoped
from .setup_api import P,DB

router=APIRouter(prefix='/api/dji/waylines',tags=['大疆航线资料'])
WAYLINE='dji_wayline_file_v1';STOP='dji_wayline_file_stop_v1'


def _key(actor,rid):return 'dji-wayline:'+actor+':'+rid

def _request(db,p,rid):
    return db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==_key(p.user_id,rid)).execution_options(populate_existing=True))

def _id(raw):
    try:return str(UUID(str(raw)))
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,'请求编号无效') from None

def _checked(db,p,row):
    if row.kind!=WAYLINE or row.status!='internal' or not isinstance(row.data,dict):raise HTTPException(409,'航线记录无法核对')
    data=row.data
    try:
        evidence,content=evidence_bytes(db,p,data['evidence_id'])
        summary=inspect_kmz(content)
        if (evidence.sha256!=data['sha256'] or summary!=data['summary'] or summary['sha256']!=data['sha256']
            or evidence.media_type!='application/vnd.google-earth.kmz' or evidence.size_bytes!=len(content)):
            raise ValueError()
    except (KeyError,ValueError,TypeError,HTTPException):raise HTTPException(409,'航线原件或解析记录无法核对，已停止使用') from None
    return {'id':row.id,'name':data['name'],'created_at':row.created_at.astimezone(timezone.utc).isoformat(),'version':row.version,
            'evidence_id':evidence.id,'summary':summary,'status':'saved','can_dispatch':False,
            'dispatch_reason':'设备上云、官方模拟器及飞行安全链尚未完成验收；当前仅保存与核对航线。'}

def _receipt(db,p,row):
    if row.kind==STOP:return {'state':'cancelled'}
    return {'state':'saved','wayline':_checked(db,p,row)}

@router.post('')
async def upload(p:P,db:DB,request_id:str=Form(...),file:UploadFile=File(...)):
    require(p,'admin');rid=_id(request_id)
    name=PurePosixPath((file.filename or '').replace('\\','/')).name
    if not name or len(name)>200 or any(ord(c)<32 or ord(c)==127 for c in name) or not name.lower().endswith('.kmz'):
        await file.close();raise HTTPException(422,'请选择名称有效的 .kmz 航线文件')
    try:content=await file.read(MAX_ARCHIVE_BYTES+1)
    finally:await file.close()
    summary=inspect_kmz(content)
    _lock(db,p.workspace_id,_key(p.user_id,rid));_current_admin(db,p)
    old=_request(db,p,rid)
    if old:
        if old.kind==STOP:return _receipt(db,p,old)
        if old.kind!=WAYLINE or old.data.get('sha256')!=summary['sha256'] or old.data.get('name')!=name:raise HTTPException(409,'原请求已经绑定其他航线，请先核对')
        return _receipt(db,p,old)
    evidence=save_immutable(db,p,content,'application/vnd.google-earth.kmz',{'modality':'dji_wayline','name':name,'inspection':'structural_only'})
    row=Job(workspace_id=p.workspace_id,kind=WAYLINE,status='internal',idempotency_key=_key(p.user_id,rid),
            data={'actor_id':p.user_id,'request_id':rid,'name':name,'sha256':summary['sha256'],
                  'evidence_id':evidence.id,'summary':summary})
    db.add(row);db.flush();audit(db,p,'dji.wayline.saved',row.id,{'sha256':summary['sha256'],'point_count':summary['point_count']});db.commit()
    return _receipt(db,p,row)

@router.get('/operations/{request_id}')
def operation(request_id:str,p:P,db:DB):
    require(p,'admin','operator','viewer');rid=_id(request_id);row=_request(db,p,rid)
    if row is None:raise HTTPException(404,'未找到原航线请求')
    return _receipt(db,p,row)

@router.post('/operations/{request_id}/cancel')
def cancel(request_id:str,p:P,db:DB):
    require(p,'admin','operator','viewer');rid=_id(request_id)
    _lock(db,p.workspace_id,_key(p.user_id,rid));row=_request(db,p,rid)
    if row:return _receipt(db,p,row)
    row=Job(workspace_id=p.workspace_id,kind=STOP,status='internal',idempotency_key=_key(p.user_id,rid),data={'actor_id':p.user_id,'request_id':rid})
    db.add(row);db.commit();return {'state':'cancelled'}

@router.get('')
def list_waylines(p:P,db:DB,response:Response,page:int=1):
    require(p,'admin','operator','viewer');response.headers['Cache-Control']='no-store'
    if type(page) is not int or not 1<=page<=10000:raise HTTPException(422,'页码无效')
    rows=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind==WAYLINE,Job.status=='internal')
        .order_by(Job.created_at.desc(),Job.id.desc()).offset((page-1)*10).limit(11)))
    result=[];unavailable=0
    for row in rows[:10]:
        try:
            item=_checked(db,p,row)
            item['summary']={k:v for k,v in item['summary'].items() if k!='routes'}
            result.append(item)
        except HTTPException:
            unavailable+=1
    return {'waylines':result,'page':page,'has_more':len(rows)>10,'unavailable_count':unavailable}

@router.get('/{identity}')
def detail(identity:UUID,p:P,db:DB,response:Response):
    require(p,'admin','operator','viewer');response.headers['Cache-Control']='no-store'
    return _checked(db,p,scoped(db,Job,str(identity),p))

@router.get('/{identity}/download')
def download(identity:UUID,p:P,db:DB):
    require(p,'admin','operator','viewer');row=scoped(db,Job,str(identity),p);_checked(db,p,row)
    _,content=evidence_bytes(db,p,row.data['evidence_id'])
    return Response(content,media_type='application/vnd.google-earth.kmz',headers={'Content-Disposition':'attachment; filename="wayline.kmz"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
