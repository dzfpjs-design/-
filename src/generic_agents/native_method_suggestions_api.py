"""受控云建议的预览、授权、持久编辑与人工导入接口。"""
from typing import Annotated
from fastapi import APIRouter,Depends,Query
from sqlalchemy import select
from .db import session
from .security import Principal,authenticated
from .models import Capability
from .workspace_api import saved_execution
from . import native_method_suggestions as suggestions

router=APIRouter(prefix='/api/workspace/tasks')
DB=Annotated[object,Depends(session)]
P=Annotated[Principal,Depends(authenticated)]
ROOT='/{identity}/experience/suggestions'

@router.get(ROOT+'/models')
def models(identity:str,p:P,db:DB):
    from .cloud_provider_policy import provider_enabled
    saved_execution(db,p,identity)
    rows=db.scalars(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.kind=='foundation_model',Capability.status=='published')).all()
    result=[]
    for row in rows:
        if not provider_enabled(row.data.get('execution',{}).get('provider')):continue
        try:suggestions.model(db,p,row.id)
        except Exception:continue
        result.append({'id':row.id,'name':row.name,'provider':row.data['execution']['provider'],'model':row.data['execution']['model'],'version':row.version})
    return result

@router.post(ROOT+'/preview')
def preview(identity:str,body:suggestions.Preview,p:P,db:DB):
    task,_,_=saved_execution(db,p,identity)
    request,_=suggestions.preview(db,p,task,body.model_profile_id)
    return suggestions.preview_view(task,request)

@router.post(ROOT+'/local/preview')
def local_preview(identity:str,body:suggestions.LocalPreview,p:P,db:DB):
    task,_,_=saved_execution(db,p,identity)
    return suggestions.local_preview_view(task,suggestions.local_preview(db,p,task))

@router.get(ROOT)
def listing(identity:str,p:P,db:DB,limit:int=Query(default=20,ge=1,le=50),after:str|None=None):
    task,_,_=saved_execution(db,p,identity)
    return suggestions.listing(db,p,task,limit,after)

def change(identity,body,operation,p,db,target=None):
    task,_,_=saved_execution(db,p,identity,True)
    result=suggestions.execute(db,p,task,body,operation,target)
    db.commit();return result

@router.post(ROOT+'/start')
def start(identity:str,body:suggestions.Start,p:P,db:DB):return change(identity,body,'start',p,db)

@router.post(ROOT+'/local/start')
def local_start(identity:str,body:suggestions.LocalStart,p:P,db:DB):return change(identity,body,'local_start',p,db)

@router.get(ROOT+'/policy')
def policy(identity:str,p:P,db:DB):
    task,_,_=saved_execution(db,p,identity)
    return {'version':task.version,'policy':suggestions.policy_view(db,p,task)}

@router.post(ROOT+'/policy')
def set_policy(identity:str,body:suggestions.PolicyChange,p:P,db:DB):return change(identity,body,'policy',p,db)

@router.post(ROOT+'/requests/recover')
def recover(identity:str,body:suggestions.Recovery,p:P,db:DB):
    task,_,_=saved_execution(db,p,identity,True)
    result=suggestions.recover(db,p,task,body);db.commit();return result

@router.get(ROOT+'/{suggestion_id}')
def detail(identity:str,suggestion_id:str,p:P,db:DB):
    task,_,_=saved_execution(db,p,identity)
    return suggestions.row_view(db,p,task,suggestions.checked(db,p,suggestion_id,task))

@router.post(ROOT+'/{suggestion_id}/save')
def save(identity:str,suggestion_id:str,body:suggestions.Save,p:P,db:DB):return change(identity,body,'save',p,db,suggestion_id)
@router.post(ROOT+'/{suggestion_id}/decline')
def decline(identity:str,suggestion_id:str,body:suggestions.Change,p:P,db:DB):return change(identity,body,'decline',p,db,suggestion_id)
@router.post(ROOT+'/{suggestion_id}/cancel')
def cancel(identity:str,suggestion_id:str,body:suggestions.Change,p:P,db:DB):return change(identity,body,'cancel',p,db,suggestion_id)
@router.post(ROOT+'/{suggestion_id}/retry')
def retry(identity:str,suggestion_id:str,body:suggestions.Retry,p:P,db:DB):return change(identity,body,'retry',p,db,suggestion_id)
@router.post(ROOT+'/{suggestion_id}/import')
def import_draft(identity:str,suggestion_id:str,body:suggestions.Import,p:P,db:DB):return change(identity,body,'import',p,db,suggestion_id)
