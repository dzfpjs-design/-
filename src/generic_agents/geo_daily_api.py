"""Scoped human-readable imagery collections; no model execution or state writes."""
from typing import Annotated
from fastapi import APIRouter,Query,HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from .setup_api import P,DB
from .models import GeoDaily,DomainInstance
from .services import require,scoped
from .geo_daily import checked_daily,reconcile,detail,summary
from .geo_storage import path_for
router=APIRouter(prefix='/api/imagery-daily')

def view(row,instance=None):
 value={'id':row.id,'version':row.version,**row.data,'scope':'workspace'}
 if instance:
  records=[r for r in row.data['records'] if r['instance_id']==instance];value.update(records=records,**summary(records),scope='instance_part_of_workspace_receipt')
 return value

@router.get('')
def rows(p:P,db:DB,instance_id:str|None=None,before_day:Annotated[str|None,Query(pattern=r'^\d{4}-\d{2}-\d{2}$')]=None):
 if instance_id:scoped(db,DomainInstance,instance_id,p)
 q=select(GeoDaily).where(GeoDaily.workspace_id==p.workspace_id)
 if instance_id:q=q.where(GeoDaily.data['records'].contains([{'instance_id':instance_id}]))
 if before_day:q=q.where(GeoDaily.received_day<before_day)
 found=db.scalars(q.order_by(GeoDaily.received_day.desc()).limit(31)).all()
 return {'rows':[view(checked_daily(db,p,r.id),instance_id) for r in found[:30]],'next_before_day':found[29].received_day if len(found)>30 else None}

@router.post('/reconcile')
def refresh(p:P,db:DB,instance_id:str|None=None):
 require(p,'admin','operator')
 if instance_id:scoped(db,DomainInstance,instance_id,p)
 reconcile(db,p.workspace_id,force=True);db.commit();return rows(p,db,instance_id)

@router.get('/{identity}/export')
def export(identity:str,p:P,db:DB,instance_id:str|None=None):
 if instance_id:scoped(db,DomainInstance,instance_id,p)
 row=checked_daily(db,p,identity)
 if instance_id and not any(r['instance_id']==instance_id for r in row.data['records']):raise HTTPException(404,'此归集没有当前实例的影像处理')
 return {'schema':'imagery-daily-collection-v1','collection':view(row,instance_id),'entity_state_updated':False,'source_time_verified':False,'domain_quality_verified':False,'cloud_calls':0}

@router.post('/{identity}/reconcile')
def refresh_day(identity:str,p:P,db:DB):
 require(p,'admin','operator');row=scoped(db,GeoDaily,identity,p)
 reconcile(db,p.workspace_id,force=True,target_day=row.received_day);db.commit();return view(checked_daily(db,p,identity))

@router.get('/{identity}/records/{key}')
def record_detail(identity:str,key:str,p:P,db:DB):return detail(db,p,checked_daily(db,p,identity),key)

@router.get('/{identity}/records/{key}/original/{index}')
def original(identity:str,key:str,index:int,p:P,db:DB):
 value=detail(db,p,checked_daily(db,p,identity),key)
 if not value['integrity_verified']:raise HTTPException(409,value['current']['integrity_issue'])
 if not 0<=index<len(value['record']['sources']):raise HTTPException(404,'没有此份原影像')
 source=value['record']['sources'][index];ev,path=path_for(db,p,source['evidence_id'])
 if ev.sha256!=source['sha256']:raise HTTPException(409,'原件与归集引用不一致')
 # Stream only after a fresh full checksum; do not materialize a 512MB TIFF in RAM.
 from fastapi.responses import FileResponse
 return FileResponse(path,media_type='image/tiff',headers={'X-Content-SHA256':ev.sha256,'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})
