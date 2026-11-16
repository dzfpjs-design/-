"""Private local review drafts. These routes neither call models nor apply configuration."""
from typing import Annotated
from fastapi import Query
from fastapi.responses import JSONResponse
from .setup_api import router,P,DB
from . import local_proposal_review_drafts_v261 as service
from .local_proposal_review_source_v261 import source_for_draft
from . import local_proposal_review_submissions_v261 as submissions
from .local_proposal_review_submission_contracts_v261 import SubmitInput

def _available(value):
    value['local_submission_available']=True
    return value


@router.post('/understanding/{source_id}/local-review-drafts',status_code=201)
def open_local_review(source_id:str,body:service.OpenInput,p:P,db:DB):
    value=service.open_draft(db,p,source_id,body);db.commit();return _available(value)


@router.get('/understanding/{source_id}/local-review-drafts')
def local_review_history(source_id:str,p:P,db:DB,limit:Annotated[int,Query(ge=1,le=20)]=20):
    return [_available(x) for x in service.list_drafts(db,p,source_id,limit)]


@router.get('/local-review-drafts/{draft_id}')
def local_review(draft_id:str,p:P,db:DB):
    return _available(service.get_draft(db,p,draft_id))


@router.get('/local-review-drafts/{draft_id}/source')
def local_review_source(draft_id:str,p:P,db:DB):
    return source_for_draft(db,p,draft_id)


@router.put('/local-review-drafts/{draft_id}')
def save_local_review(draft_id:str,body:service.SaveInput,p:P,db:DB):
    value=service.save_draft(db,p,draft_id,body);_available(value['current_draft']);db.commit();return value

@router.get('/understanding/{source_id}/local-review-drafts/by-request/{request_id}')
def lookup_local_open(source_id:str,request_id:str,p:P,db:DB,
        expected_body_hash:Annotated[str,Query(pattern=r'^[0-9a-f]{64}$')]):
    value=service.lookup_open(db,p,source_id,request_id,expected_body_hash)
    if value['draft'] is not None:_available(value['draft'])
    return value

@router.get('/local-review-drafts/{draft_id}/saves/by-request/{request_id}')
def lookup_local_save(draft_id:str,request_id:str,p:P,db:DB,
        expected_body_hash:Annotated[str,Query(pattern=r'^[0-9a-f]{64}$')],
        expected_version:Annotated[int,Query(ge=1)]):
    value=service.lookup_save(db,p,draft_id,request_id,expected_body_hash,expected_version)
    _available(value['current_draft'])
    if value['save_reply'] is not None:_available(value['save_reply']['current_draft'])
    return value

@router.post('/local-review-drafts/{draft_id}/submissions')
def submit_local_review(draft_id:str,body:SubmitInput,p:P,db:DB):
    outcome=submissions.submit(db,p,draft_id,body)
    db.commit()  # A terminal rejection is also a durable, recoverable outcome.
    if outcome['rejection'] is not None:
        rejected=outcome['rejection'];saved=outcome['saved']
        return JSONResponse(status_code=409,content={'detail':{'code':rejected['code'],
            'message':rejected['message'],'rejection':rejected,
            'receipt':saved['receipt'] if saved else None,'analysis':saved['analysis'] if saved else None,
            'verification_state':'rejected_local_review'}})
    return outcome['saved']

@router.get('/local-review-drafts/{draft_id}/submissions/by-request/{request_id}')
def lookup_local_submission(draft_id:str,request_id:str,p:P,db:DB,
        expected_body_hash:Annotated[str,Query(pattern=r'^[0-9a-f]{64}$')]):
    return submissions.lookup(db,p,draft_id,request_id,expected_body_hash)
