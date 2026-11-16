"""Normal workspace commands for durable local business-advice preparation."""
from fastapi import APIRouter

from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_quota_v304 as quota
from . import same_goal_advice_price_v307 as prices
from . import same_goal_advice_preview_v307 as previews
from . import same_goal_advice_execution_v308 as execution
from . import same_goal_advice_review_v308 as reviews
from . import same_goal_advice_context_v310 as context
from . import same_goal_advice_commands_v310 as commands
from . import same_goal_advice_options_v310 as options
from .setup_api import P, DB

router=APIRouter()
BASE='/tasks/{identity}/business-advice'


@router.post(BASE+'/price-options',status_code=201)
def configure_task_price(identity:str,body:options.PriceRequest,p:P,db:DB):
    result=options.price(db,p,identity,body.model_dump(mode='json'))
    db.commit()
    return result


@router.post(BASE+'/quota-options',status_code=201)
def configure_task_quota(identity:str,body:options.ConfigureQuota,p:P,db:DB):
    result=options.configure_quota(db,p,identity,body.model_dump(mode='json'))
    db.commit()
    return result


@router.get(BASE+'/context')
def read_context(identity:str,p:P,db:DB):
    return context.context(db,p,identity)


@router.get(BASE+'/operations/{request_id}')
def read_original_command(identity:str,request_id:str,p:P,db:DB):
    return commands.read(db,p,identity,request_id)


@router.get(BASE+'/preparations/{plan_id}/documents')
def read_original_documents(identity:str,plan_id:str,p:P,db:DB):
    return context.documents(db,p,identity,plan_id)


@router.get(BASE+'/preparations/{plan_id}/starts/operations/{request_id}')
def read_original_start(identity:str,plan_id:str,request_id:str,p:P,db:DB):
    return commands.read(db,p,identity,request_id,plan_id=plan_id,operation='start')


@router.get(BASE+'/preparations/{plan_id}/reviews/operations/{request_id}')
def read_original_review(identity:str,plan_id:str,request_id:str,p:P,db:DB):
    return commands.read(db,p,identity,request_id,plan_id=plan_id,operation='review')


@router.post('/business-advice/price-bases',status_code=201)
def register_price(body:prices.Authorize,p:P,db:DB):
    result=prices.authorize(db,p,body.model_dump(mode='json'))
    db.commit()
    return result


@router.post(BASE+'/quota-policy',status_code=201)
def authorize_quota(identity:str,body:quota.Authorize,p:P,db:DB):
    result=quota.authorize(db,p,identity,body.model_dump(mode='json'))
    db.commit()
    return result


@router.post(BASE+'/preparations',status_code=201)
def prepare(identity:str,body:contracts.Prepare,p:P,db:DB):
    result=store.prepare(db,p,identity,body.model_dump(mode='json'))
    db.commit()
    return result


@router.get(BASE+'/preparations')
def listing(identity:str,p:P,db:DB):
    return store.listing(db,p,identity)


@router.get(BASE+'/preparations/operations/{request_id}')
def read_operation(identity:str,request_id:str,p:P,db:DB):
    return store.get_operation(db,p,identity,request_id)


@router.get(BASE+'/preparations/{plan_id}')
def read_preparation(identity:str,plan_id:str,p:P,db:DB):
    return store.get(db,p,identity,plan_id)


@router.post(BASE+'/preparations/{plan_id}/cancel')
def cancel(identity:str,plan_id:str,body:contracts.Cancel,p:P,db:DB):
    result=store.cancel(db,p,identity,plan_id,body.model_dump(mode='json'))
    db.commit()
    return result


@router.post(BASE+'/preparations/{plan_id}/preview',status_code=201)
def prepare_preview(identity:str,plan_id:str,body:previews.PreviewRequest,p:P,db:DB):
    result=previews.prepare(db,p,identity,plan_id,body.model_dump(mode='json'))
    db.commit()
    return result


@router.get(BASE+'/preparations/{plan_id}/preview')
def read_preview(identity:str,plan_id:str,p:P,db:DB):
    return previews.get(db,p,identity,plan_id)


@router.get(BASE+'/preparations/{plan_id}/previews/operations/{request_id}')
def read_preview_operation(identity:str,plan_id:str,request_id:str,p:P,db:DB):
    return previews.get(db,p,identity,plan_id,request_id=request_id)


@router.post(BASE+'/preparations/{plan_id}/start',status_code=201)
def start_analysis(identity:str,plan_id:str,body:contracts.Start,p:P,db:DB):
    result=execution.start(db,p,identity,plan_id,body.model_dump(mode='json'))
    db.commit()
    return result


@router.post(BASE+'/preparations/{plan_id}/review')
def review_analysis(identity:str,plan_id:str,body:contracts.Review,p:P,db:DB):
    result=reviews.review(db,p,identity,plan_id,body.model_dump(mode='json'))
    db.commit()
    return result
