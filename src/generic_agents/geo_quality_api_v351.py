"""Saved source selections and quality results; no implicit model execution."""
from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from .setup_api import P,DB
from .models import Capability,Job,GeoScene
from .services import scoped
from .geo_storage import path_for
from . import geo_quality_v351 as service

router=APIRouter(prefix='/api/geo/quality')

@router.get('/adapters')
def adapters(p:P,db:DB):
    value=service.manifest();rows=db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id,
        Capability.namespace==value.namespace,Capability.name==value.name).order_by(Capability.created_at.desc())).all()
    result=[]
    for row in rows:
        ready=False
        try:service.adapter(db,p,row.id);ready=True
        except HTTPException:pass
        result.append({'id':row.id,'version':row.version,'status':row.status,'ready':ready,'name':'Sentinel-2 SCL（本地）'})
    return {'schema':'geo-quality-adapter-catalog-v351','adapters':result,'local':True,'source_association':'user_declared_unverified'}

@router.post('/adapters/sentinel2-scl/register')
def register_scl(p:P,db:DB):
    row=service.register_adapter(db,p);db.commit()
    return {'id':row.id,'status':row.status,'version':row.version,'ready':True,'name':'Sentinel-2 SCL（本地）'}

@router.post('/assessments',status_code=201)
def create(body:service.Create,p:P,db:DB):
    row=service.create(db,p,body);db.commit();return service.view(db,p,row)

@router.get('/assessments')
def listing(p:P,db:DB):
    rows=db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,Job.kind.in_([service.KIND,service.KIND_V352])).order_by(Job.created_at.desc()).limit(50))
    result=[]
    for row in rows:
        try:result.append(service.view(db,p,row))
        except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
            result.append({'schema':'geo-quality-assessment-v351','id':row.id,'status':'integrity_error',
                'request_id':None,'sources':{},'result':None,'error':'此报告的保存依据无法核对；原记录保留',
                'data_reception':{'state':'integrity_error'}})
    return result

@router.get('/requests/{request_id}')
def recover(request_id:str,p:P,db:DB):
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==service._key(request_id)))
    if not row or row.data.get('actor_id')!=p.user_id:raise HTTPException(404,'没有保存的原质量处理申请')
    return service.view(db,p,row)

@router.get('/assessments/{identity}')
def detail(identity:str,p:P,db:DB):
    return service.view(db,p,scoped(db,Job,identity,p))

@router.post('/assessments/{identity}/retry-reception')
def retry_reception(identity:str,p:P,db:DB):
    from .geo_quality_reception_v352 import retry
    job=scoped(db,Job,identity,p);retry(db,p,job);db.commit()
    return service.view(db,p,job)

@router.get('/assessments/{identity}/artifacts/{filename}')
def artifact(identity:str,filename:str,p:P,db:DB):
    job=scoped(db,Job,identity,p);service.checked(db,p,job)
    service.need(job.status=='succeeded' and job.data.get('result') is not None,'质量处理尚未完成')
    values=[x for x in job.data['result']['artifacts'] if x['filename']==filename]
    service.need(len(values)==1,'没有此质量原件或掩膜',404)
    entry=values[0];ev,path=path_for(db,p,entry['evidence_id']);service.need(ev.sha256==entry['sha256'])
    return FileResponse(path,media_type=entry['media_type'],filename=filename,headers={'X-Content-SHA256':ev.sha256,'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
