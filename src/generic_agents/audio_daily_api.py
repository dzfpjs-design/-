"""Readable persisted audio summaries and evidence drill-down without cloud inference."""
from typing import Annotated
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import Response
from sqlalchemy import select
from .setup_api import P,DB
from .models import AudioDaily,DomainInstance
from .services import require,scoped
from .audio_daily import checked_daily,reconcile,detail,checked_source,KINDS,summary
router=APIRouter(prefix='/api/audio-daily')

def view(row,instance=None):
 value={'id':row.id,'version':row.version,**row.data,'scope':'workspace'}
 if instance:
  records=[r for r in row.data['records'] if r['instance_id']==instance]
  value.update(records=records,**summary(records),scope='instance_part_of_workspace_receipt')
 return value

def scope(db,p,instance):
 if instance:scoped(db,DomainInstance,instance,p)

@router.get('')
def rows(p:P,db:DB,instance_id:str|None=None,before_day:Annotated[str|None,Query(pattern=r'^\d{4}-\d{2}-\d{2}$')]=None):
 scope(db,p,instance_id);q=select(AudioDaily).where(AudioDaily.workspace_id==p.workspace_id)
 if instance_id:q=q.where(AudioDaily.data['records'].contains([{'instance_id':instance_id}]))
 if before_day:q=q.where(AudioDaily.received_day<before_day)
 found=db.scalars(q.order_by(AudioDaily.received_day.desc()).limit(31)).all()
 result={'rows':[view(checked_daily(db,p,r.id),instance_id) for r in found[:30]],'next_before_day':found[29].received_day if len(found)>30 else None}
 from .sound_daily import pending as pending_sounds, collection_status as sound_collection_status
 for row_value,original_row in zip(result['rows'],found[:30]):
  state=sound_collection_status(db,p,original_row.received_day)
  if state['state']!='not_collected':row_value['sound_collection_status']=state
 sounds=pending_sounds(db,p)
 if instance_id:sounds=[v for v in sounds if v['instance_id']==instance_id]
 if sounds:result['sound_pending']=sounds
 return result

@router.post('/reconcile')
def refresh(p:P,db:DB,instance_id:str|None=None):
 require(p,'admin','operator');scope(db,p,instance_id);reconcile(db,p.workspace_id);db.commit();return rows(p,db,instance_id)

@router.get('/{identity}/export')
def export(identity:str,p:P,db:DB,instance_id:str|None=None):
 scope(db,p,instance_id);row=checked_daily(db,p,identity)
 if instance_id and not any(r['instance_id']==instance_id for r in row.data['records']):raise HTTPException(404,'此归集没有当前实例的音频处理记录')
 return {'schema':'audio-daily-collection-v1','collection':view(row,instance_id),'source_time_verified':False,'domain_quality_verified':False,'entity_state_updated':False,'cloud_calls':0}

@router.get('/{identity}/records/{key}')
def record_detail(identity:str,key:str,p:P,db:DB):return detail(db,p,checked_daily(db,p,identity),key)

@router.get('/{identity}/records/{key}/original')
def original(identity:str,key:str,p:P,db:DB):
 row=checked_daily(db,p,identity);value=detail(db,p,row,key)
 if not value['integrity_verified']:raise HTTPException(409,value['integrity_issue'])
 saved=value['record'];source=scoped(db,KINDS[saved['kind']],saved['id'],p);meta,content=checked_source(db,p,saved['kind'],source)
 media=meta.media_type if saved['kind']=='environment_sound' else source.data.get('source',source.data)['media_type']
 return Response(content,media_type=media,headers={'X-Content-SHA256':saved['input_sha256'],'X-Content-Type-Options':'nosniff'})
