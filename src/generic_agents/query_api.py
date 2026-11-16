"""Recoverable operator clarification; confirmation creates a new immutable query result."""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field,ValidationError
from sqlalchemy import select
from .db import session
from .security import authenticated,Principal,require
from .models import DataRun,Job,now
from .contracts import Contract,VersionAction,DataRequest,digest
from .services import scoped,as_dict,check_version,audit
from .data_requirements import prepare_request
from .query_needs import catalog

router=APIRouter(prefix='/api/data/queries')
DB=Annotated[object,Depends(session)]
P=Annotated[Principal,Depends(authenticated)]
EDITABLE={'needs_clarification','needs_confirmation','blocked_model','failed'}

def reject_task_query(row,db=None,p=None):
    from .goal_evaluation_query_guard_v288 import managed
    if managed(row):
        raise HTTPException(409, "目标独立评估为原任务的封存资料；通用查询不能改写")
    # The original Task owns native source/derived queries as well as adopted
    # supporting queries. Workspace membership alone is not task management
    # authority. Reject every generic write entry, even a damaged/empty marker.
    if db is not None and p is not None:
        from .public_query_visibility_v144 import reject_generic_write
        return reject_generic_write(db,p,row)
    if any(key in row.data for key in ('native_task_query_contract', 'native_query_contract', 'native_goal_task_id')):
        raise HTTPException(409,'原任务查询请在原业务任务中管理；不能通过通用查询改写、派生、核查或取消')
    from .native_candidate_queries_v139 import MARKER,has_trace
    if MARKER in row.data or db is not None and p is not None and has_trace(db,p,row.id):
        raise HTTPException(409,'原候选查询请在原实例资料区核对；不能通过通用查询改写、派生、核查或取消')


class DraftAction(VersionAction):
    draft: dict = Field(default_factory=dict)


class DerivedReview(VersionAction):
    decision: str = Field(pattern='^(confirmed|rejected)$')


@router.post('/{identifier}/review-derived')
def review_derived(identifier:str,body:DerivedReview,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,DataRun,identifier,p,True);check_version(row,body.expected_version)
    reject_task_query(row,db,p)
    verification=row.data.get('result',{}).get('derivation_verification')
    withdraw=body.decision=='rejected' and row.status=='sufficient' and verification=='human_confirmed_extension'
    if not withdraw and (row.status!='needs_review' or verification!='unverified_registered_extension'):
        raise HTTPException(409,'当前没有待核查的外部推导结果')
    result=row.data['result'];request=row.data['request']
    if body.decision=='confirmed':
        from .data_tools import frame,validate_steps
        from .services import query_evidence,published
        validate_steps(db,p,request,result['query_tool_steps'])
        fresh=query_evidence(db,p,request['entity_ids'],request['fields'],learning=False,
            requirement=request.get('evidence_requirement'),fixed_window=request.get('resolved_time'),relation_query=request.get('relation_query'))
        from .business_tools import relation_bindings
        relation_frame=lambda value:relation_bindings(value) if value.get('relations') else None
        if fresh['status']!='sufficient' or frame(fresh)!=row.data['tool_context']['source_records'] or relation_frame(fresh)!=relation_frame(result):
            raise HTTPException(409,'原证据或关系依据改变，不能确认旧推导；请保留原结果并重新查询')
        for item in result['tool_trace']:
            if published(db,p,item['capability_id']).content_hash!=item['capability_hash']:
                raise HTTPException(409,'查询能力依据改变，不能确认旧推导')
    review={'decision':body.decision,'reason':body.reason,'actor_id':p.user_id,'at':now().isoformat(),
        'source_version':row.version,'derived_result_hash':digest(result['derived_records'])}
    row.status='sufficient' if body.decision=='confirmed' else 'rejected';row.version+=1
    row.data={**row.data,'result':{**result,'status':row.status,'derivation_verification':'human_confirmed_extension' if body.decision=='confirmed' else 'human_rejected_extension',
        'tool_blocker':None if body.decision=='confirmed' else '外部推导被拒绝，原输入和回执保留'},
        'derived_review':review,'derived_review_history':[*row.data.get('derived_review_history',[]),review]}
    from .goal_queries import completed
    completed(db,p,row)
    if row.data.get('goal_delegation'):
        from .data_gaps import changed
        for entity_id in row.data['request']['entity_ids']:changed(db,p,entity_id,'query-review:'+row.id+':'+str(row.version))
    audit(db,p,'data.derived_review',row.id,review);db.commit();return as_dict(row)


def editable(db,p,identifier,expected):
    require(p,'admin','operator')
    row=scoped(db,DataRun,identifier,p,True);check_version(row,expected)
    reject_task_query(row,db,p)
    if row.data.get('goal_delegation'):raise HTTPException(409,'业务委托方案由已确认目标控制；请在原业务目标中调整并重新规划')
    if row.data.get('resolved_query_id'):raise HTTPException(409,'此方案已创建新查询，请查看新结果；原记录继续保留')
    tool_recovery=row.status in {'blocked_evidence','insufficient','rejected','sufficient'} and bool(row.data.get('request',{}).get('tool_steps') or row.data.get('tool_trace'))
    if row.status not in EDITABLE and not tool_recovery:raise HTTPException(409,'当前查询不需要澄清或已经确认；原结果继续保留')
    return row


@router.get('')
def list_queries(p:P,db:DB):
    from .public_query_visibility_v144 import QueryVisibility
    rows=list(db.scalars(select(DataRun).where(DataRun.workspace_id==p.workspace_id)
        .order_by(DataRun.created_at.desc()).limit(100)))
    visibility=QueryVisibility(db,p,rows)
    result=[]
    for row in rows:
        if not visibility.family(row):
            result.append(as_dict(row));continue
        try:
            result.append(visibility.reference(row))
        except HTTPException as error:
            if error.status_code not in (403,404,409):raise
            # A restricted but unreadable/damaged query is never raw fallback.
    return result


@router.get('/{identifier}')
def get_query(identifier:str,p:P,db:DB):
    from .public_query_visibility_v144 import QueryVisibility
    row=scoped(db,DataRun,identifier,p)
    visibility=QueryVisibility(db,p,[row])
    return visibility.detail(row) if visibility.family(row) else as_dict(row)


@router.put('/{identifier}/draft')
def save_draft(identifier:str,body:DraftAction,p:P,db:DB):
    row=editable(db,p,identifier,body.expected_version)
    # Saving incomplete form state is permitted; it never authorizes execution.
    import json
    if len(json.dumps(body.draft,ensure_ascii=False).encode())>16000:
        raise HTTPException(422,'草稿过大，请仅保存查询条件')
    allowed={'question','fields','relation_query','evidence_requirement','business_timezone','form_reason',
        'tool_steps','max_tool_calls','max_tool_seconds','allow_external_tools'}
    if set(body.draft)-allowed:raise HTTPException(422,'草稿包含不支持的查询条件')
    row.data={**row.data,'resolution_draft':body.draft,'draft_saved_at':now().isoformat()};row.version+=1
    audit(db,p,'data.need_draft_saved',row.id,{'reason':body.reason});db.commit();return as_dict(row)


@router.post('/{identifier}/confirm')
def confirm(identifier:str,body:VersionAction,p:P,db:DB):
    row=editable(db,p,identifier,body.expected_version)
    draft=row.data.get('resolution_draft')
    if draft is None:raise HTTPException(409,'请先保存并核对查询条件草稿')
    original=row.data['request']
    try:
        request=DataRequest.model_validate({'question':draft.get('question',row.question),
            'entity_ids':original['entity_ids'],'fields':draft.get('fields',[]),'mode':'structured',
            'relation_query':draft.get('relation_query'),'evidence_requirement':draft.get('evidence_requirement'),
            'business_timezone':draft.get('business_timezone'),'tool_steps':draft.get('tool_steps',[]),
            'max_tool_calls':draft.get('max_tool_calls',6),'max_tool_seconds':draft.get('max_tool_seconds',60),
            'allow_external_tools':draft.get('allow_external_tools',False)}).model_dump()
    except ValidationError:
        raise HTTPException(422,'草稿条件尚不完整或格式不符合限制，请核对对象关系、字段和时间')
    if not request['fields']:raise HTTPException(422,'请明确需要核查的字段')
    description=catalog(db,p,original['entity_ids'])
    fields={k for pkg in description['packages'] for schema in pkg['fields'].values() for k in schema}
    if len(request['fields'])!=len(set(request['fields'])) or not set(request['fields']).issubset(fields):
        raise HTTPException(422,'字段必须属于授权对象的注册业务包，且不能重复')
    prepared=prepare_request(db,p,request)
    if prepared['clarifications']:raise HTTPException(409,'；'.join(prepared['clarifications']))
    from .agents import data_agent
    child=DataRun(workspace_id=p.workspace_id,question=request['question'],owner_id=p.user_id,
        status='queued',data={'request':prepared,'parent_query_id':row.id,
            'human_confirmation':{'actor_id':p.user_id,'reason':body.reason,'at':now().isoformat(),
                'source_version':row.version,'source_question':row.question}})
    db.add(child);db.flush()
    if prepared.get('tool_steps'):
        db.add(Job(workspace_id=p.workspace_id,kind='data_query',idempotency_key='query:'+child.id,
            data={'query_id':child.id,'actor_id':p.user_id}))
    else:
        state=data_agent(db,p,prepared,child.id)
        child.status=state['status'];child.data={**child.data,**state}
    recovered=bool(row.data.get('result'))
    if not recovered:row.status='resolved'
    row.version+=1
    row.data={**row.data,'resolved_query_id':child.id,'resolution_reason':body.reason,'resolved_at':now().isoformat()}
    audit(db,p,'data.need_confirmed',row.id,{'query_id':child.id,'reason':body.reason})
    db.commit();return {'source':as_dict(row),'query':as_dict(child)}


@router.post('/{identifier}/cancel')
def cancel(identifier:str,body:VersionAction,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,DataRun,identifier,p,True);check_version(row,body.expected_version)
    reject_task_query(row,db,p)
    if row.status not in {'queued','running'}:raise HTTPException(409,'仅可取消等待或正在解析的查询')
    row.status='cancelled';row.version+=1
    row.data={**row.data,'cancelled_at':now().isoformat(),'cancel_reason':body.reason,
        'error':'已停止后续查询；已发出的模型请求可能仍计费，回执到达后会保留'}
    from .goal_queries import completed
    completed(db,p,row)
    audit(db,p,'data.query_cancelled',row.id,{'reason':body.reason});db.commit();return as_dict(row)
