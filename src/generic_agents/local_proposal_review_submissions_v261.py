"""Durable local review copies and terminal rejections; never dispatch a model."""
import copy
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select,text
from .contracts import digest
from .models import SetupAnalysis,Audit
from .services import scoped,audit
from . import local_proposal_review_drafts_v261 as drafts
from .local_proposal_review_models_v261 import LocalProposalReviewDraftV261 as Draft
from .local_proposal_review_submission_models_v261 import LocalReviewSubmissionV261 as Submission
from .local_proposal_review_submission_contracts_v261 import (
    SubmitInput,SCHEMA,LOOKUP_SCHEMA,RECEIPT_SCHEMA,REJECTION_SCHEMA,RECEIPT_KEYS,REJECTION_KEYS,
    ACTION,REJECTED_ACTION,submission_id,attempt_id,analysis_id)
from .local_proposal_review_compile_v261 import compile_saved_review
from .local_proposal_review_source_v261 import source_for_draft
from .local_proposal_review_policy_v261 import apply_local_review,LocalReviewInvalid,POLICY

RECORD='local-proposal-review-submission-record-v261'
POLICY_HASH=digest({'policy':POLICY,'operations':['discard_item','replace_citation'],
    'order':['saved_user_edits','exact_agent_alias','same_block_citation_expansion','complete_original_validator'],
    'contracts':[4,5],'cloud_calls':0})
ERROR='本地核对建议的原出处或独立保存依据无法核对；原记录保留'

def need(ok,message=ERROR,code=409):
    if not ok:raise HTTPException(code,message)

def _request_scope(workspace,owner,nonce):
    return digest({'workspace_id':workspace,'owner_id':owner,'request_id':nonce})

def _meta(row):
    r=row.data['receipt']
    return {'submission_id':row.id,'draft_id':row.draft_id,'draft_version':row.draft_version,
        'request_scope_hash':_request_scope(row.workspace_id,row.owner_id,row.request_id),
        'body_hash':row.data['body_hash'],'derived_analysis_id':row.derived_analysis_id,
        'result_hash':r.get('derived_result_hash'),'receipt_hash':r['receipt_hash'],
        'record_hash':row.data['record_hash'],'status':row.status}

def _provenance(row):
    return {'policy':POLICY,'submission_id':row.id,'receipt_hash':row.data['receipt']['receipt_hash'],
            'source_id':row.source_id}

def _check_record_data(db,row,workspace):
    need(row.workspace_id==workspace and row.status in {'saved','rejected'})
    data=row.data
    need(type(data) is dict and set(data)=={'schema','body','body_hash','scope','snapshot','derivation','receipt','record_hash'}
         and data['schema']==RECORD)
    body=SubmitInput.model_validate(data['body'])
    need(body.model_dump(mode='json')==data['body'] and digest(data['body'])==data['body_hash'])
    need(row.id==attempt_id(row.workspace_id,row.owner_id,row.request_id)
         and body.request_id==row.request_id and body.version==row.draft_version)
    scope=data['scope']
    need(type(scope) is dict and set(scope)=={'workspace_id','owner_id','author_role','draft_id','draft_version','source_id'}
        and scope=={'workspace_id':row.workspace_id,'owner_id':row.owner_id,'author_role':scope.get('author_role'),
             'draft_id':row.draft_id,'draft_version':row.draft_version,'source_id':row.source_id}
        and scope['author_role'] in {'admin','operator'})
    need(digest({k:v for k,v in data.items() if k!='record_hash'})==data['record_hash'])
    receipt=data['receipt'];keys=RECEIPT_KEYS if row.status=='saved' else REJECTION_KEYS
    need(type(receipt) is dict and set(receipt)==keys
        and receipt['schema']==(RECEIPT_SCHEMA if row.status=='saved' else REJECTION_SCHEMA)
        and all(receipt[k]==v for k,v in {'submission_id':row.id,'workspace_id':row.workspace_id,
          'owner_id':row.owner_id,'draft_id':row.draft_id,'draft_version':row.draft_version,
          'request_id':row.request_id,'body_hash':data['body_hash']}.items())
        and digest({k:v for k,v in receipt.items() if k!='receipt_hash'})==receipt['receipt_hash'])
    proofs=list(db.scalars(select(Audit).where(Audit.workspace_id==workspace,Audit.object_id==row.id).limit(2)))
    need(len(proofs)==1 and proofs[0].actor_id==row.owner_id
         and proofs[0].action==(ACTION if row.status=='saved' else REJECTED_ACTION) and proofs[0].data==_meta(row))
    if row.status=='saved':
        need(row.saved_key==submission_id(row.workspace_id,row.owner_id,row.draft_id,row.draft_version)
             and row.derived_analysis_id==analysis_id(row.saved_key) and receipt['derived_analysis_id']==row.derived_analysis_id
             and receipt['source_id']==row.source_id and receipt['value_hash']==body.value_hash
             and type(data['snapshot']) is dict and type(data['derivation']) is dict)
        child=db.get(SetupAnalysis,row.derived_analysis_id)
        need(child is not None and child.workspace_id==workspace
             and type(child.data) is dict and child.data.get('result_hash')==receipt['derived_result_hash']
             and digest(child.data.get('result'))==receipt['derived_result_hash']
             and child.data.get('local_review_provenance')==_provenance(row))
    else:
        need(row.saved_key is None and row.derived_analysis_id is None and data['snapshot'] is None and data['derivation'] is None)
    return row

def _check_record(db,row,workspace):
    try:return _check_record_data(db,row,workspace)
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError):
        raise HTTPException(409,ERROR) from None

def _saved_reply(db,row,workspace):
    _check_record(db,row,workspace)
    need(row.status=='saved')
    from .setup_understanding import view
    return {'schema':SCHEMA,'receipt':copy.deepcopy(row.data['receipt']),
        'analysis':view(db.get(SetupAnalysis,row.derived_analysis_id)),
        'verification_state':'saved_not_live_revalidated'}

def _outcome(db,row,workspace):
    _check_record(db,row,workspace)
    if row.status=='saved':return {'saved':_saved_reply(db,row,workspace),'rejection':None}
    receipt=copy.deepcopy(row.data['receipt']);related=None
    if receipt['saved_submission_id'] is not None:
        old=db.get(Submission,receipt['saved_submission_id'])
        need(old is not None and old.owner_id==row.owner_id and old.draft_id==row.draft_id
             and old.draft_version==row.draft_version)
        related=_saved_reply(db,old,workspace)
    return {'saved':related,'rejection':receipt}

def _find_nonce(db,p,nonce):
    identity=attempt_id(p.workspace_id,p.user_id,nonce)
    row=db.scalar(select(Submission).where(Submission.workspace_id==p.workspace_id,
        Submission.owner_id==p.user_id,Submission.request_id==nonce))
    if row is None:
        residue=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,Audit.object_id==identity).limit(1))
        need(residue is None,'原另存申请仍有保存痕迹，但回执记录缺失；未重新生成建议')
    else:need(row.id==identity)
    return row

def _write(db,p,draft,body,status,*,snapshot=None,derivation=None,receipt,child=None):
    payload=body.model_dump(mode='json')
    row=Submission(id=attempt_id(p.workspace_id,p.user_id,body.request_id),workspace_id=p.workspace_id,
        owner_id=p.user_id,draft_id=draft.id,draft_version=body.version,source_id=draft.source_id,
        request_id=body.request_id,status=status,
        saved_key=submission_id(p.workspace_id,p.user_id,draft.id,body.version) if status=='saved' else None,
        derived_analysis_id=child.id if child else None)
    data={'schema':RECORD,'body':payload,'body_hash':digest(payload),
        'scope':{'workspace_id':p.workspace_id,'owner_id':p.user_id,'author_role':p.role,
            'draft_id':draft.id,'draft_version':body.version,'source_id':draft.source_id},
        'snapshot':snapshot,'derivation':derivation,'receipt':receipt}
    data['record_hash']=digest(data);row.data=data
    if child is not None:
        child.data={**child.data,'local_review_provenance':_provenance(row)};db.add(child);db.flush()
    db.add(row);db.flush()
    audit(db,p,ACTION if status=='saved' else REJECTED_ACTION,row.id,_meta(row));db.flush()
    return _outcome(db,row,p.workspace_id)

def _reject(db,p,draft,body,code,message,saved=None):
    receipt={'schema':REJECTION_SCHEMA,'submission_id':attempt_id(p.workspace_id,p.user_id,body.request_id),
        'workspace_id':p.workspace_id,'owner_id':p.user_id,'draft_id':draft.id,'draft_version':body.version,
        'request_id':body.request_id,'body_hash':digest(body.model_dump(mode='json')),'code':code,
        'message':str(message)[:1000],'saved_submission_id':saved.id if saved else None}
    receipt['receipt_hash']=digest(receipt)
    return _write(db,p,draft,body,'rejected',receipt=receipt)

def _derive(raw,request,value,source_view,configuration):
    operations=compile_saved_review(value,source_view)
    blocks=[b for chunk in request['chunks'] for b in chunk]
    candidate,trace=apply_local_review(raw['value'],operations,blocks,contract_version=request['contract_version'])
    from .setup_agent_aliases_v261 import normalize_v5_agents
    candidate,aliases=normalize_v5_agents(candidate,contract_version=request['contract_version'])
    from .citation_expansion import expand_citations
    candidate,expanded=expand_citations(candidate,blocks)
    from .setup_understanding import validate_result,request_contract
    result=validate_result(candidate,blocks,configuration,request_contract(request),request=request)
    return result,{'operations':operations,'policy_trace':trace,'agent_aliases':aliases,'citation_normalizations':expanded}

class _InputRejection(ValueError):
    def __init__(self,code,message):self.code=code;super().__init__(message)

def submit(db,p,draft_id,body):
    body=drafts._body(SubmitInput,body);drafts._actor(db,p,write=True)
    prior=_find_nonce(db,p,body.request_id)
    if prior is not None:
        need(prior.draft_id==draft_id and prior.data['body_hash']==digest(body.model_dump(mode='json')),
             '同一另存原号不能更换稿件、版本或正文')
        return _outcome(db,prior,p.workspace_id)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(_request_scope(p.workspace_id,p.user_id,body.request_id)[:15],16)})
    prior=_find_nonce(db,p,body.request_id)
    if prior is not None:
        need(prior.draft_id==draft_id and prior.data['body_hash']==digest(body.model_dump(mode='json')),
             '同一另存原号不能更换稿件、版本或正文')
        return _outcome(db,prior,p.workspace_id)
    draft=drafts._checked(db,p,drafts._own(db,p,draft_id,lock=True))
    saved_key=submission_id(p.workspace_id,p.user_id,draft.id,body.version)
    existing=db.scalar(select(Submission).where(Submission.workspace_id==p.workspace_id,Submission.saved_key==saved_key))
    if existing is not None:
        _check_record(db,existing,p.workspace_id)
        return _reject(db,p,draft,body,'already_saved','这一版人稿已另存为待核对建议，请打开原记录',existing)
    # A lost saved row cannot silently make the stable outcome slot available.
    orphan=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,
        Audit.data['derived_analysis_id'].as_string()==analysis_id(saved_key)).limit(1))
    need(orphan is None,'这一版人稿仍有另存痕迹，但原回执缺失；未重新生成建议')
    if draft.status!='editing' or draft.version!=body.version or draft.data['value_hash']!=body.value_hash:
        return _reject(db,p,draft,body,'stale_draft','人稿已更新；原输入保留，请先核对保存版本')
    try:
        source,request,raw,request_hash=drafts._raw_source(db,p,draft.source_id,lock=True)
        need(raw['hash']==draft.raw_hash and request_hash==draft.data['source_request_hash'],'原回答或请求身份已变化')
        from .setup_understanding import current
        _,configuration,_=current(db,p,source)
        if configuration.version!=body.revision:
            raise _InputRejection('stale_configuration','当前配置已更新，人稿保留；请核对后再另存')
        display=source_for_draft(db,p,draft.id)
        value=copy.deepcopy(draft.data['value']);config=copy.deepcopy(configuration.data)
        result,derivation=_derive(raw,request,value,display,config)
        if derivation['operations']!=body.operations:
            raise _InputRejection('invalid_operations','页面修改与已保存人稿不一致，未另存建议')
    except _InputRejection as error:
        return _reject(db,p,draft,body,error.code,str(error))
    except (HTTPException,LocalReviewInvalid,ValueError) as error:
        message=error.detail if isinstance(error,HTTPException) else str(error)
        return _reject(db,p,draft,body,'invalid_proposal',message)
    identity=attempt_id(p.workspace_id,p.user_id,body.request_id)
    child=SetupAnalysis(id=analysis_id(saved_key),workspace_id=p.workspace_id,status='review',version=1,
        data={'request':copy.deepcopy(request),'consent':copy.deepcopy(source.data['consent']),
            'result':result,'result_hash':digest(result),'completed':list(range(len(request['chunks']))),
            'receipts':[],'error':None,'original_cloud_source':source.id,
            'local_reuse_guard':{'revision':configuration.version,'configuration_hash':digest(config)},
            'review_selection':{'names':[],'object_names':[],'accept_domain':False},
            'citation_normalizations':copy.deepcopy(derivation['citation_normalizations']),
            'agent_alias_normalizations':copy.deepcopy(derivation['agent_aliases'])})
    receipt={'schema':RECEIPT_SCHEMA,'submission_id':identity,'workspace_id':p.workspace_id,'owner_id':p.user_id,
        'draft_id':draft.id,'draft_version':body.version,'request_id':body.request_id,
        'body_hash':digest(body.model_dump(mode='json')),'value_hash':body.value_hash,'source_id':source.id,
        'source_raw_hash':raw['hash'],'source_request_hash':request_hash,
        'configuration_revision':configuration.version,'configuration_hash':digest(config),
        'operations_hash':digest(derivation['operations']),'policy_hash':POLICY_HASH,
        'derived_analysis_id':child.id,'derived_result_hash':digest(result)}
    receipt['receipt_hash']=digest(receipt)
    snapshot={'value':value,'configuration':config,'source_request':copy.deepcopy(request),
              'source_display':display}
    return _write(db,p,draft,body,'saved',snapshot=snapshot,derivation=derivation,receipt=receipt,child=child)

def lookup(db,p,draft_id,request_id,expected_body_hash):
    drafts._actor(db,p)
    need(drafts._uuid(draft_id) and drafts._uuid(request_id) and drafts._sha(expected_body_hash),'查询原申请格式不符',422)
    row=_find_nonce(db,p,request_id)
    if row is not None:
        need(row.draft_id==draft_id and row.data['body_hash']==expected_body_hash,'原另存申请的稿件或正文不匹配')
        result=_outcome(db,row,p.workspace_id)
    else:
        drafts._own(db,p,draft_id)
        result={'saved':None,'rejection':None}
    return {'schema':LOOKUP_SCHEMA,'found':row is not None,'submission':result['saved'],
            'rejection':result['rejection'],'absence_is_non_execution':False}

def _verify_origin(db,p,child,*,depth=0):
    """Public proposal use checks private provenance without returning private notes."""
    need(depth<=16,'本地核对建议的复用链超过本次核对上限')
    rows=list(db.scalars(select(Submission).where(Submission.workspace_id==p.workspace_id,
        Submission.derived_analysis_id==child.id).limit(2)))
    marker=child.data.get('local_review_provenance')
    if rows:
        need(len(rows)==1);row=_check_record(db,rows[0],p.workspace_id)
        need(marker==_provenance(row))
        snapshot=row.data['snapshot'];receipt=row.data['receipt']
        source=scoped(db,SetupAnalysis,row.source_id,p)
        raw=source.data.get('raw_response');request=source.data.get('request')
        need(type(raw) is dict and digest({k:v for k,v in raw.items() if k!='hash'})==raw.get('hash')==receipt['source_raw_hash']
             and digest(request)==receipt['source_request_hash']==digest(snapshot['source_request'])
             and child.data['request']==request and digest(snapshot['value'])==receipt['value_hash']
             and digest(snapshot['configuration'])==receipt['configuration_hash'] and receipt['policy_hash']==POLICY_HASH)
        # Reconstruction uses the frozen input and context, not the later editable draft.
        result,derivation=_derive(raw,request,snapshot['value'],snapshot['source_display'],snapshot['configuration'])
        need(derivation==row.data['derivation'] and digest(derivation['operations'])==receipt['operations_hash']
             and result==child.data['result'] and digest(result)==receipt['derived_result_hash']
             and child.data.get('agent_alias_normalizations')==derivation['agent_aliases']
             and child.data.get('citation_normalizations')==derivation['citation_normalizations'])
        return _provenance(row)
    residue=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,
        Audit.data['derived_analysis_id'].as_string()==child.id).limit(1))
    need(residue is None)
    parent_id=child.data.get('reused_from')
    reuse=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.object_id==child.id,Audit.action=='setup.understanding.reuse').limit(2)))
    if parent_id is not None or reuse:
        need(type(parent_id) is str and parent_id!=child.id and len(reuse)==1,
             '建议的复用依据不完整，原记录保留')
        need(reuse[0].data=={'source_id':parent_id,'revision':child.data.get('local_reuse_guard',{}).get('revision'),'no_cloud_call':True},
             '建议的复用依据不完整，原记录保留')
        parent=db.scalar(select(SetupAnalysis).where(SetupAnalysis.workspace_id==p.workspace_id,SetupAnalysis.id==parent_id))
        need(parent is not None,'建议的复用依据不完整，原记录保留')
        proof=verify_origin(db,p,parent,depth=depth+1)
        if proof is not None:
            need(marker==proof and _family(child.id) and child.data['request']==parent.data['request']
                 and child.data.get('result_hash')==parent.data.get('result_hash')
                 and digest(child.data.get('result'))==child.data.get('result_hash')
                 and child.data.get('agent_alias_normalizations')==parent.data.get('agent_alias_normalizations')
                 and child.data.get('citation_normalizations')==parent.data.get('citation_normalizations'))
            return proof
    need(marker is None and not _family(child.id))
    return None

def _family(value):
    try:return UUID(value).version==5
    except (ValueError,TypeError,AttributeError):return False

def verify_origin(db,p,child,*,depth=0):
    try:return _verify_origin(db,p,child,depth=depth)
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError):
        raise HTTPException(409,ERROR) from None
