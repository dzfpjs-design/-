"""Single-report preparation within the existing DataRun/data_query lifecycle.

Three explicitly different request identities are retained: the user's auto
command, its server-derived ordinary V2 parent Create, and the child Search.
No original request is rewritten to insert a later Search identifier. Reads
check independent metadata witnesses only; physical checks belong to actual
dispatch/consumption and the existing explicit freshness operation.
"""
import copy
import hashlib
import json
import re
from datetime import timedelta
from uuid import UUID, uuid5
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session
from fastapi import HTTPException
from . import models as m
from .contracts import digest
from .security import Principal
from .services import scoped, audit, require
from .source_query_contracts import need, sha, uuid
from .document_question_contracts import parse

MARKER='document_question_v1'
CREATED='data.document_question_created_v241'
PLANNED='data.document_question_planned_v241'
LINK='data.document_question_search_link_v241'
WAIT='data.document_question_wait_v241'
PREPARED='data.document_question_prepared_v241'
WAKE='data.document_question_woken_v241'
FAILED='data.document_question_failed_v241'
ACTIONS=(CREATED,PLANNED,LINK,WAIT,PREPARED,WAKE,FAILED)
PHASE='waiting_document_search'
PLANNER={'id':'literal-report-keyword','version':'1'}
LIMITATIONS=['仅一份固定报告版本的明确字词查找，不是整篇语义理解或现场结论。',
    '报告日期为版本登记日，不是文档形成日期或现场拍摄日期。',
    '停止本次答复不等于已经开始的本地正文查找全部停止；原过程和引用保留。']
ERROR='报告提问的原申请、固定选择或父子任务证明无法核对；原记录保留'


def _events(db,p,action,identity):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==action,m.Audit.object_id==identity).limit(3)))


def _one(db,p,action,identity,*,optional=False):
    rows=_events(db,p,action,identity)
    need(len(rows)==1 or optional and not rows,ERROR)
    if not rows:return None
    value=rows[0].data
    need(value.get('record_hash')==digest({key:item for key,item in value.items() if key!='record_hash'})
        and rows[0].actor_id==value['actor_id'] and value['workspace_id']==p.workspace_id
        and value['query_id']==identity,ERROR)
    return value


def _save(db,p,action,row,**fields):
    need(not _events(db,p,action,row.id),ERROR)
    value={'query_id':row.id,'workspace_id':p.workspace_id,'actor_id':row.owner_id,
        'instance_id':row.data['request']['source_scope']['instance_id'],**copy.deepcopy(fields)}
    value['record_hash']=digest(value);audit(db,p,action,row.id,value);db.flush();return value


def _derived(nonce,kind,value):return str(uuid5(UUID(nonce),'document-question-v1:'+kind+':'+digest(value)))


def _raw(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf8')


def is_record(db,row,job=None):
    if MARKER in row.data:return True
    if type(row.data.get('request')) is dict and row.data['request'].get('schema')=='document-question-create-v1':return True
    if job is not None and MARKER in job.data:return True
    if db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==row.workspace_id,
        m.Audit.object_id==row.id,m.Audit.action.in_(ACTIONS)).limit(1)):return True
    if db.scalar(select(m.Job.id).where(m.Job.workspace_id==row.workspace_id,
        m.Job.kind.in_(['data_query','search_runtime_documents']),
        m.Job.data[MARKER]['query_id'].as_string()==row.id).limit(1)) is not None:return True
    from .runtime_document_models import RuntimeDocumentSearch as Search
    return db.scalar(select(Search.id).where(Search.workspace_id==row.workspace_id,
        Search.data[MARKER]['query_id'].as_string()==row.id).limit(1)) is not None


def _selected(db,p,selection,*,physical):
    from .runtime_document_models import RuntimeDocumentVersion as Version,RuntimeDocumentParseAttempt as Attempt
    from . import runtime_documents as d, runtime_document_receipts as receipts
    from .instance_observation_day_v216 import TZ
    version=scoped(db,Version,selection['document_version_id'],p)
    attempt=scoped(db,Attempt,selection['parse_attempt_id'],p)
    need(version.instance_id==selection['instance_id'] and attempt.document_version_id==version.id,ERROR)
    origin=version.data['origin']
    d.anchored(db,p,d.UPLOAD,version.id,origin)
    need(origin['version_id']==version.id and origin['document_id']==version.document_id
        and origin['raw_sha256']==selection['raw_sha256'] and origin['instance_id']==version.instance_id
        and version.status=='parsed' and attempt.data.get('parse_hash')==selection['parse_hash'] and attempt.status=='parsed',ERROR)
    job=scoped(db,m.Job,attempt.job_id,p)
    need(job.kind=='parse_runtime_document' and job.status=='succeeded'
        and job.data.get('runtime_document_id')==version.id and job.data.get('actor_id')==attempt.actor_id,ERROR)
    if physical:
        with d.verification_scope(db):
            actual,result=d.selected_attempt(db,p,version,attempt.id,selection['parse_hash'])
            need(actual.id==attempt.id and result['status']=='parsed' and receipts.delivery_view(db,p,attempt)['verified'],ERROR)
    else:
        event=scoped(db,m.Event,attempt.data['event_id'],p)
        need(event.kind=='runtime_document.parsed' and event.status=='received',ERROR)
        receipts.checked_ack(db,p,event,event.data['receipt'])
    return {'document_id':version.document_id,'version_id':version.id,'origin_hash':digest(origin),
        'attempt_id':attempt.id,'attempt_origin_hash':digest(d.attempt_origin(attempt)),
        'parse_hash':selection['parse_hash'],'raw_sha256':selection['raw_sha256'],
        'registered_day':str(version.created_at.astimezone(TZ).date())}


def catalog(db,p,instance_id):
    from .source_query_service import reader
    from .instance_api import active
    from .runtime_document_models import RuntimeDocument as Document,RuntimeDocumentVersion as Version,RuntimeDocumentParseAttempt as Attempt
    from .instance_observation_day_v216 import TZ
    reader(db,p);instance,release=active(db,p,instance_id)
    businesses=[{'id':b['id'],'label':b['name']} for b in release.data['snapshot']['businesses']
        if b.get('available') is True and b['id'] in instance.data.get('enabled_business_ids',[])]
    rows=list(db.scalars(select(Document).where(Document.workspace_id==p.workspace_id,
        Document.instance_id==instance.id).order_by(Document.created_at.desc(),Document.id.desc()).limit(101)))
    reports=[]
    for document in rows[:100]:
        version=scoped(db,Version,document.latest_version_id,p)
        need(version.document_id==document.id and version.instance_id==instance.id,ERROR)
        identity=version.data.get('latest_attempt_id');attempt=scoped(db,Attempt,identity,p) if identity else None
        parse_hash=attempt.data.get('parse_hash') if attempt else None
        reason='此版本正文尚未解析并独立收档，请先核对资料处理状态'
        available=False
        if attempt and parse_hash and version.status=='parsed':
            selection={'instance_id':instance.id,'document_version_id':version.id,'parse_attempt_id':attempt.id,
                'raw_sha256':version.data['origin']['raw_sha256'],'parse_hash':parse_hash}
            try:_selected(db,p,selection,physical=False);available=True;reason=None
            except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
                reason='该版本的解析或独立收档证明暂时无法核对，请查看原资料记录'
        reports.append({'document_id':document.id,'version_id':version.id,'title':document.title,
            'file_name':version.name,'sequence':version.sequence,'registered_day':str(version.created_at.astimezone(TZ).date()),
            'raw_sha256':version.data['origin']['raw_sha256'],'parse_attempt_id':identity,'parse_hash':parse_hash,
            'can_ask':available and p.role in {'admin','operator'},'reason':reason})
    result={'schema':'document-question-catalog-v1','instance_id':instance.id,'release_id':release.id,
        'configuration_hash':release.configuration_hash,'businesses':businesses,'reports':reports,
        'permissions':{'can_create':p.role in {'admin','operator'} and bool(businesses)},
        'limited':len(rows)>100,'limitations':[*LIMITATIONS,'目录只展示实例前100份当前文档版本；提交再核原件，不表示全部报告。']}
    result['catalog_hash']=digest(result);return result


def _creation(db,p,row):
    value=_one(db,p,CREATED,row.id)
    need(value['query_id']==row.id and value['workspace_id']==row.workspace_id==p.workspace_id
        and value['actor_id']==row.owner_id and row.data.get(MARKER)==value['marker'],ERROR)
    original=parse(value['exact_body'].encode('utf8'))
    need(hashlib.sha256(value['exact_body'].encode('utf8')).hexdigest()==value['body_sha256']
        and original==value['original'] and original['selection']==value['selection']
        and digest(original['selection'])==value['selection_hash'] and original['question']==row.question,ERROR)
    selection=original['selection'];pin=value['selected_pin']
    need(value['instance_id']==selection['instance_id'] and pin['version_id']==selection['document_version_id'] and pin['attempt_id']==selection['parse_attempt_id']
        and pin['parse_hash']==selection['parse_hash'] and pin['raw_sha256']==selection['raw_sha256']
        and value['base_scope']=={'instance_id':selection['instance_id'],'release_id':selection['release_id'],
            'business_ids':selection['business_ids'],'entity_ids':[],'batch_day':pin['registered_day'],
            'source_kinds':['document_searches'],'document_search_ids':[]},ERROR)
    from .source_query_contracts import parse as parse_v2,CREATE_AUDIT
    derived=parse_v2(value['parent_body'].encode('utf8'),'create')
    need(derived==row.data['request'] and derived['request_id']==_derived(original['request_id'],'parent',original)
        and hashlib.sha256(value['parent_body'].encode('utf8')).hexdigest()==value['parent_body_sha256']
        and derived['question']==original['question'] and derived['source_scope']==value['base_scope']
        and derived['parent_query_id']==original['parent_query_id'],ERROR)
    old=_events(db,p,CREATE_AUDIT,row.id)
    need(len(old)==1 and old[0].data['record_hash']==value['parent_creation_hash']
        and old[0].data['exact_body']==value['parent_body'] and old[0].data['actor_id']==row.owner_id,ERROR)
    return value


def _plan(db,p,row,*,optional=False):
    value=_one(db,p,PLANNED,row.id,optional=optional)
    if value:
        original=_creation(db,p,row)
        need(value['creation_hash']==original['record_hash'] and value['planner']==planner(row.question),ERROR)
    return value


def planner(question):
    from .source_query_answer import _document_terms,intent
    terms=_document_terms(question)
    # A conjunction is not a single explicit topic merely because a literal
    # tokenizer produced one string. Never silently choose its first side.
    keyword=terms[0] if len(terms)==1 and len(terms[0])<=200 and not re.search(r'以及|或者|或是|和|与|、|及',terms[0]) else None
    if intent(question)!='document_conditions':keyword=None
    return {**PLANNER,'keyword':keyword}


def _link(db,p,row,*,optional=False):
    from .runtime_document_models import RuntimeDocumentSearch as Search
    from . import runtime_documents as d
    value=_one(db,p,LINK,row.id,optional=optional)
    if value is None:
        need(not _events(db,p,WAIT,row.id) and not _events(db,p,PREPARED,row.id)
            and not _events(db,p,WAKE,row.id),ERROR)
        return None
    original=_creation(db,p,row);planned=_plan(db,p,row)
    expected_nonce=_derived(original['original']['request_id'],'search',
        {'selection_hash':original['selection_hash'],'planner':planned['planner']})
    need(value['creation_hash']==original['record_hash'] and value['plan_hash']==planned['record_hash']
        and value['body']=={'request_id':expected_nonce,'release_id':original['selection']['release_id'],
            'keyword':planned['planner']['keyword']} and value['body_hash']==digest(value['body']),ERROR)
    search=scoped(db,Search,value['search_id'],p);job=scoped(db,m.Job,value['child_job_id'],p)
    children=list(db.scalars(select(Search.id).where(Search.workspace_id==p.workspace_id,
        or_(Search.data['request_hash'].as_string()==value['body_hash'],
            Search.data[MARKER]['query_id'].as_string()==row.id)).limit(2)))
    jobs=list(db.scalars(select(m.Job.id).where(m.Job.workspace_id==p.workspace_id,
        m.Job.kind=='search_runtime_documents',or_(m.Job.data['runtime_document_id'].as_string()==search.id,
            m.Job.data[MARKER]['query_id'].as_string()==row.id)).limit(2)))
    need(children==[search.id] and jobs==[job.id],ERROR)
    need(search.instance_id==original['selection']['instance_id'] and search.release_id==original['selection']['release_id']
        and search.actor_id==row.owner_id and search.keyword==value['body']['keyword']
        and search.data['request_hash']==value['body_hash'] and search.data['job_id']==job.id
        and job.kind=='search_runtime_documents' and job.data.get('runtime_document_id')==search.id
        and job.data.get('actor_id')==row.owner_id and job.idempotency_key=='runtime-document-search:'+search.id
        and search.data.get(MARKER)==job.data.get(MARKER)=={'query_id':row.id,'link_hash':value['record_hash'],
            'instance_id':original['selection']['instance_id'],'original_request_id':original['original']['request_id']},ERROR)
    origin=d.search_origin(search)
    d.anchored(db,p,d.SEARCH_ORIGIN,search.id,origin)
    need(digest(origin)==value['search_origin_hash'],ERROR)
    receipts=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==d.COMMAND,m.Audit.object_id==expected_nonce).limit(2)))
    need(len(receipts)==1 and receipts[0].actor_id==row.owner_id
        and receipts[0].data['body']==value['body'] and receipts[0].data['request_hash']==value['body_hash']
        and receipts[0].data['scope']=='search:'+search.instance_id
        and receipts[0].data['result']['id']==search.id,ERROR)
    selected=original['selection']
    need(search.data['inventory']==[{'document_id':original['selected_pin']['document_id'],
        'version_id':selected['document_version_id'],'attempt_id':selected['parse_attempt_id'],
        'parse_hash':selected['parse_hash'],'eligible':True,'reason':None}],ERROR)
    waited=_one(db,p,WAIT,row.id)
    need(waited['link_hash']==value['record_hash'] and waited['parent_job_id']==row.data['job_id']
        and waited['child_job_id']==job.id and type(waited['attempts_at_wait']) is int
        and waited['attempts_at_wait']>0,ERROR)
    uuid(waited['lease_at_wait'])
    # These are the historical issue attempt/lease. A legitimate expired
    # running owner can be reclaimed between LINK/WAIT commit and the final
    # PHASE commit; its new claim/token must not rewrite the old wait proof.
    prepared=_one(db,p,PREPARED,row.id,optional=True)
    woke=_one(db,p,WAKE,row.id,optional=True)
    need(bool(prepared)==bool(woke),ERROR)
    if prepared:
        need(prepared['link_hash']==value['record_hash'] and prepared['wait_hash']==waited['record_hash']
            and woke['prepared_hash']==prepared['record_hash'] and woke['parent_job_id']==row.data['job_id']
            and search.status=='completed' and job.status=='succeeded',ERROR)
        result=search.data['result'];d.anchored(db,p,d.SEARCH_RESULT,search.id,result)
        need(prepared['search_result_hash']==digest(result),ERROR)
    return value,search,job,prepared


def check_metadata(db,p,row,job):
    """Additional seal after the ordinary V2 creation/command checks."""
    try:
        value=_creation(db,p,row)
        need(value['job_id']==job.id and job.data.get(MARKER)=={'query_id':row.id,'creation_hash':value['record_hash'],
            'instance_id':value['selection']['instance_id'],'original_request_id':value['original']['request_id']},ERROR)
        _plan(db,p,row,optional=True);_link(db,p,row,optional=True)
        return value
    except HTTPException:raise
    except (KeyError,TypeError,ValueError,AttributeError,IndexError):raise HTTPException(409,ERROR) from None


def effective_scope(db,p,row):
    value=_creation(db,p,row);linked=_link(db,p,row,optional=True)
    scope=copy.deepcopy(value['base_scope'])
    if linked:
        need(linked[3] is not None,'原正文搜索尚未成功结算，不能提前采用来源')
        scope['document_search_ids']=[linked[1].id]
    else:
        planned=_plan(db,p,row,optional=True)
        need(planned is not None and planned['planner']['keyword'] is None,
            '报告问题尚未完成规划，不能按普通空来源查询提前作答')
    return scope


def recover(db,p,nonce,expected):
    from . import source_query_service as source
    source.reader(db,p)
    try:sha(expected)
    except ValueError:raise HTTPException(422,'原报告问题摘要无效') from None
    rows=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==CREATED,m.Audit.data['original']['request_id'].as_string()==nonce).limit(2)))
    need(len(rows)<=1,ERROR)
    base={'schema':'document-question-recovery-v1','request_id':nonce,'body_sha256':expected,'found':False,'result':None}
    if not rows:
        inventory(db,p)
        traces=db.scalar(select(m.DataRun.id).where(m.DataRun.workspace_id==p.workspace_id,
            m.DataRun.data[MARKER]['request_id'].as_string()==nonce).limit(1))
        need(traces is None,ERROR);return base
    event=rows[0];need(event.actor_id==p.user_id,'请由原发起人恢复报告问题',403)
    need(event.data['body_sha256']==expected,'原报告提问不能更换内容')
    row=source.get(db,p,event.object_id)
    return {**base,'found':True,'result':command(db,p,row,nonce,expected)}


def create(db,p,raw):
    from . import source_query_service as source
    from .source_query_contracts import COMMAND_AUDIT,CREATE_AUDIT,MARKER as SOURCE_MARKER
    body=parse(raw);body_hash=hashlib.sha256(raw).hexdigest()
    source._lock(db,p,body['request_id']);prior=recover(db,p,body['request_id'],body_hash)
    if prior['found']:return prior['result'],False
    require(p,'admin','operator')
    choices=catalog(db,p,body['selection']['instance_id'])
    need(choices['catalog_hash']==body['expected_catalog_hash'] and choices['release_id']==body['selection']['release_id'],
        '报告目录或发布范围已变化，请保留问题并重新核对')
    need(choices['permissions']['can_create'] and set(body['selection']['business_ids']) <= {b['id'] for b in choices['businesses']},ERROR,403)
    selected=body['selection'];pin=_selected(db,p,selected,physical=True)
    base_scope={'instance_id':selected['instance_id'],'release_id':selected['release_id'],
        'business_ids':copy.deepcopy(selected['business_ids']),'entity_ids':[],'batch_day':pin['registered_day'],
        'source_kinds':['document_searches'],'document_search_ids':[]}
    parent_nonce=_derived(body['request_id'],'parent',body)
    occupied=db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==COMMAND_AUDIT,m.Audit.data['request_id'].as_string()==parent_nonce).limit(1))
    retained=db.scalar(select(m.DataRun.id).where(m.DataRun.workspace_id==p.workspace_id,
        m.DataRun.data[SOURCE_MARKER]['request_id'].as_string()==parent_nonce).limit(1))
    need(occupied is None and retained is None,'派生父申请已被占用或缺少原自动提问证明，未收养既有查询')
    old_catalog=source.catalog(db,p,selected['instance_id'],pin['registered_day'])
    parent={'schema':'data-query-create-v2','request_id':parent_nonce,'question':body['question'],
        'source_scope':base_scope,'expected_catalog_hash':old_catalog['catalog_hash'],'parent_query_id':body['parent_query_id']}
    encoded=_raw(parent);created,new=source.create(db,p,encoded);need(new,ERROR)
    row=scoped(db,m.DataRun,created['query']['id'],p);job=scoped(db,m.Job,row.data['job_id'],p)
    original=_events(db,p,CREATE_AUDIT,row.id);need(len(original)==1,ERROR)
    marker={'schema':'document-question-record-v1','request_id':body['request_id'],'selection_hash':digest(selected)}
    value=_save(db,p,CREATED,row,original=body,exact_body=raw.decode('utf8'),body_sha256=body_hash,
        selection=selected,selection_hash=digest(selected),selected_pin=pin,base_scope=base_scope,
        parent_body=encoded.decode('utf8'),parent_body_sha256=hashlib.sha256(encoded).hexdigest(),
        parent_creation_hash=original[0].data['record_hash'],job_id=job.id,marker=marker)
    row.data={**row.data,MARKER:marker};job.data={**job.data,MARKER:{'query_id':row.id,'creation_hash':value['record_hash'],
        'instance_id':selected['instance_id'],'original_request_id':body['request_id']}}
    db.flush();return command(db,p,row,body['request_id'],body_hash),True


def command(db,p,row,nonce,body_hash):
    return {'schema':'document-question-command-v1','operation':'create','request_id':nonce,
        'body_sha256':body_hash,'view':view(db,p,row)}


def inventory(db,p,instance_id=None):
    from . import source_query_service as source
    from .runtime_document_models import RuntimeDocumentSearch as Search
    rows=source.inventory(db,p,instance_id)
    candidates={row.id:row for row in rows if is_record(db,row)}
    events=select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action.in_(ACTIONS))
    children=select(Search).where(Search.workspace_id==p.workspace_id,Search.data[MARKER].is_not(None))
    jobs=select(m.Job).where(m.Job.workspace_id==p.workspace_id,
        m.Job.kind.in_(['data_query','search_runtime_documents']),m.Job.data[MARKER].is_not(None))
    if instance_id:
        events=events.where(m.Audit.data['instance_id'].as_string()==instance_id)
        children=children.where(Search.instance_id==instance_id)
        jobs=jobs.where(m.Job.data[MARKER]['instance_id'].as_string()==instance_id)
    maximum=source.MAX_HISTORY*len(ACTIONS)
    witnesses=list(db.scalars(events.limit(maximum+1)))
    searches=list(db.scalars(children.limit(source.MAX_HISTORY+1)))
    marked_jobs=list(db.scalars(jobs.limit(source.MAX_HISTORY*2+1)))
    created=[event for event in witnesses if event.action==CREATED]
    reverse={event.object_id for event in witnesses}|{r.data[MARKER]['query_id'] for r in searches}|{
        r.data[MARKER]['query_id'] for r in marked_jobs}
    need(len(witnesses)<=maximum and len(searches)<=source.MAX_HISTORY and len(marked_jobs)<=source.MAX_HISTORY*2
        and set(candidates)==reverse=={e.object_id for e in created} and len(candidates)==len(created),ERROR)
    for child in searches:
        owner=candidates[child.data[MARKER]['query_id']]
        linked=_link(db,p,owner)
        need(linked[1].id==child.id,ERROR)
    for marked in marked_jobs:
        owner=candidates[marked.data[MARKER]['query_id']]
        if marked.kind=='data_query':need(owner.data['job_id']==marked.id,ERROR)
        else:need(_link(db,p,owner)[2].id==marked.id,ERROR)
    return list(candidates.values())


def get(db,p,identity):
    from . import source_query_service as source
    source.reader(db,p)
    row=db.scalar(select(m.DataRun).where(m.DataRun.workspace_id==p.workspace_id,m.DataRun.id==identity))
    if row is None:
        trace=db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,
            m.Audit.object_id==identity,m.Audit.action.in_(ACTIONS)).limit(1))
        job=db.scalar(select(m.Job.id).where(m.Job.workspace_id==p.workspace_id,
            m.Job.data[MARKER]['query_id'].as_string()==identity).limit(1))
        from .runtime_document_models import RuntimeDocumentSearch as Search
        child=db.scalar(select(Search.id).where(Search.workspace_id==p.workspace_id,
            Search.data[MARKER]['query_id'].as_string()==identity).limit(1))
        need(trace is None and job is None and child is None,ERROR)
        need(False,'此查询不是单报告自动提问',404)
    need(is_record(db,row),'此查询不是单报告自动提问',404)
    return source.get(db,p,identity)


def view(db,p,row):
    from . import source_query_service as source
    current=source.view(db,p,row);original=_creation(db,p,row);planned=_plan(db,p,row,optional=True)
    linked=_link(db,p,row,optional=True);child=None;stage='planning' if current['status']=='running' else 'queued'
    if linked:
        _,search,job,prepared=linked
        result=search.data.get('result')
        if result is not None:
            from . import runtime_documents as d
            d.anchored(db,p,d.SEARCH_RESULT,search.id,result)
        child={'search_id':search.id,'job_id':job.id,'status':search.status,'job_status':job.status,
            'coverage':copy.deepcopy(result.get('coverage') if result else search.data.get('coverage'))}
        stage=('answering' if prepared else 'waiting_search_settlement' if search.status=='completed' else
            'searching' if job.status=='running' else PHASE)
    elif planned:stage='planning'
    if current['status'] in {'failed','cancelled','blocked_integrity'}:stage=current['status']
    elif current['status']=='needs_clarification':stage='needs_clarification'
    elif current['answer'] is not None:stage='completed'
    return {'schema':'document-question-view-v1','query':current,'selection':copy.deepcopy(original['selection']),
        'selection_hash':original['selection_hash'],'planner':copy.deepcopy(planned['planner'] if planned else {**PLANNER,'keyword':None}),
        'stage':stage,'child':child,'limitations':list(LIMITATIONS)}


def display_scope(db,p,row):
    """Progress may expose the proven Search without rewriting the request."""
    value=_creation(db,p,row);linked=_link(db,p,row,optional=True)
    result=copy.deepcopy(value['base_scope'])
    if linked and linked[3]:result['document_search_ids']=[linked[1].id]
    return result


def finish_projection(db,p,row,projection):
    """Explain a nonexecuted plan; never manufacture a document finding."""
    planned=_plan(db,p,row)
    if planned['planner']['keyword'] is None:
        need(not projection['findings'] and not projection['references'],ERROR)
        projection=copy.deepcopy(projection)
        projection.update(answer_status='needs_clarification',summary=
            '原问题已保存。请明确报告中的一个查找事项或字词，例如摄像；当前未创建正文搜索。')
    elif planned['planner']['keyword'] is not None and not projection['findings']:
        linked=_link(db,p,row)
        need(linked[3] is not None,ERROR)
        if not linked[1].data['result']['hits']:
            projection=copy.deepcopy(projection)
            projection.update(answer_status='needs_clarification',summary='已在所选固定报告版本查找“'+
                planned['planner']['keyword']+'”，未找到字面匹配。请核对用词；这不表示报告没有相关业务规定。')
    return projection


def _live(db,p,original,*,physical):
    from .source_query_service import reader
    from .instance_api import active
    reader(db,p);require(p,'admin','operator')
    instance,release=active(db,p,original['selection']['instance_id'])
    enabled={b['id'] for b in release.data['snapshot']['businesses'] if b.get('available') is True
        and b['id'] in instance.data.get('enabled_business_ids',[])}
    need(release.id==original['selection']['release_id']
        and set(original['selection']['business_ids'])<=enabled,'报告提问的当前实例版本或业务批准已变化')
    need(_selected(db,p,original['selection'],physical=physical)==original['selected_pin'],ERROR)


def before_result(db,p,row):
    _live(db,p,_creation(db,p,row),physical=False)
    effective_scope(db,p,row)


def lock_actor_for_dispatch(db,p,row):
    """Runtime-document nonce -> actual User, before Run/Instance locks.

    Ordinary Search takes its nonce before User. Taking User first here could
    deadlock a concurrent ordinary Search that preoccupies the derived nonce.
    prior only verifies/locks an original command; it never creates/adopts it.
    The parent chain and creator still reject every unlinked preoccupation.
    """
    from . import runtime_documents as d
    original=_creation(db,p,row);planned=_plan(db,p,row,optional=True)
    value=planned['planner'] if planned else planner(row.question)
    if value['keyword'] is not None:
        nonce=_derived(original['original']['request_id'],'search',
            {'selection_hash':original['selection_hash'],'planner':value})
        body={'request_id':nonce,'release_id':original['selection']['release_id'],'keyword':value['keyword']}
        d.prior(db,p,body,'search:'+original['selection']['instance_id'])
    d.current_actor(db,p)


def prepare_execution(db,p,row,job,*,cached_only=False):
    """True means the caller must finish this same Job in its waiting phase.

    Called before ordinary execute mutates/flushed DataRun. Acquire the real
    User lock BEFORE the parent DataRun/Instance: normal document mutation
    uses User -> Membership -> Instance. Cancellation/notes use User -> Run.
    Never run a child worker or wait for it with this transaction alive.
    """
    from . import runtime_documents as d,source_query_service as source
    from .runtime_document_models import RuntimeDocumentSearch as Search
    from .leases import ownership
    lock_actor_for_dispatch(db,p,row)
    row=scoped(db,m.DataRun,row.id,p,True)
    original=check_metadata(db,p,row,job)
    lease=ownership(db)
    need(lease.get('job_id')==job.id and lease.get('lease_token')==job.lease_token
        and job.status=='running' and row.owner_id==p.user_id,ERROR)
    if row.status=='cancelled':return False
    linked=_link(db,p,row,optional=True)
    if linked:
        if linked[3] is None:
            # Recover the original wait, including the crash window after
            # LINK/WAIT commit but before Job.PHASE commit. No child/source
            # execution is repeated, even for a recovery_only claim.
            row.status='running'
            row.version+=1
            row.data={**row.data,'progress':'已恢复原正文查找的等待；沿原任务继续，没有另建搜索'}
            return True
        _live(db,p,original,physical=True);return False
    if cached_only:
        previous=_plan(db,p,row,optional=True)
        need(previous is not None and previous['planner']['keyword'] is None,
            '恢复没有已封存的等待或明确澄清结果，未重新规划或创建搜索')
        return False
    _live(db,p,original,physical=True)
    planned=_plan(db,p,row,optional=True)
    if not planned:planned=_save(db,p,PLANNED,row,creation_hash=original['record_hash'],planner=planner(row.question))
    if planned['planner']['keyword'] is None:
        row.data={**row.data,'progress':'原问题已保存，请明确一个报告查找事项；未创建正文搜索'}
        return False
    nonce=_derived(original['original']['request_id'],'search',
        {'selection_hash':original['selection_hash'],'planner':planned['planner']})
    body={'request_id':nonce,'release_id':original['selection']['release_id'],'keyword':planned['planner']['keyword']}
    # An original helper attempt and these parent links commit atomically.
    # Any prior standalone command/child trace is an orphan/conflict, never
    # authority to adopt a preoccupied ordinary Search into this parent.
    occupied=db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==d.COMMAND,m.Audit.object_id==nonce).limit(1))
    orphan=db.scalar(select(Search.id).where(Search.workspace_id==p.workspace_id,
        or_(Search.data['request_hash'].as_string()==digest(body),Search.data[MARKER]['query_id'].as_string()==row.id)).limit(1))
    need(occupied is None and orphan is None,'派生正文申请已被占用或缺少原父子证明，未补建搜索')
    selected=original['selection']
    result=d.start_selected_search(db,p,selected['instance_id'],body,
        selected_version_id=selected['document_version_id'],selected_attempt_id=selected['parse_attempt_id'],
        expected_parse_hash=selected['parse_hash'],expected_raw_sha256=selected['raw_sha256'])
    search=scoped(db,Search,result['id'],p);child=scoped(db,m.Job,search.data['job_id'],p)
    linked=_save(db,p,LINK,row,creation_hash=original['record_hash'],plan_hash=planned['record_hash'],
        body=body,body_hash=digest(body),search_id=search.id,child_job_id=child.id,
        search_origin_hash=digest(d.search_origin(search)))
    tag={'query_id':row.id,'link_hash':linked['record_hash'],'instance_id':original['selection']['instance_id'],
        'original_request_id':original['original']['request_id']}
    search.data={**search.data,MARKER:tag};child.data={**child.data,MARKER:tag}
    _save(db,p,WAIT,row,link_hash=linked['record_hash'],parent_job_id=job.id,child_job_id=child.id,
        attempts_at_wait=job.attempts,lease_at_wait=job.lease_token)
    row.status='running';row.version+=1
    row.data={**row.data,'progress':'已按“'+body['keyword']+'”准备所选报告的正式正文查找，等待后台任务完成'}
    return True


def waiting_cancel_allowed(db,p,row,job):
    if not is_record(db,row,job=job):return False
    check_metadata(db,p,row,job)
    linked=_link(db,p,row)
    return job.status==PHASE and linked[3] is None and row.status in {'queued','running'}


def _actual_actor(db,workspace_id,actor_id):
    user=db.get(m.User,actor_id)
    member=db.scalar(select(m.Membership).where(m.Membership.workspace_id==workspace_id,m.Membership.user_id==actor_id))
    need(user is not None,'原报告提问账号无法核对',403)
    # This is actual membership metadata, not a grant. Inactive/revoked
    # actors fail _live and can have the original wait ended truthfully.
    return Principal(actor_id,workspace_id,member.role if member else 'unavailable','document-question-reconcile')


def reconcile_one(db,*,workspace_id,job_id):
    """Only an existing exact parent may be requeued, never a new execution."""
    uuid(workspace_id);uuid(job_id)
    hint=db.scalar(select(m.Job).where(m.Job.workspace_id==workspace_id,m.Job.id==job_id,
        m.Job.kind=='data_query',m.Job.status==PHASE))
    if hint is None:return 0
    identity=hint.data.get('query_id');uuid(identity)
    # No worker can claim waiting status. Lock Run then this exact waiting
    # Job, recheck both, and retain the original identity across wakeup.
    row=db.scalar(select(m.DataRun).where(m.DataRun.workspace_id==workspace_id,m.DataRun.id==identity)
        .with_for_update().execution_options(populate_existing=True))
    job=db.scalar(select(m.Job).where(m.Job.workspace_id==workspace_id,m.Job.id==job_id,
        m.Job.kind=='data_query',m.Job.status==PHASE).with_for_update(skip_locked=True)
        .execution_options(populate_existing=True))
    if job is None:return 0
    need(row is not None,ERROR)
    # Actor attribution for a failed wait is not execution permission. No
    # role is invented: current membership is loaded before any new work.
    p=_actual_actor(db,workspace_id,job.data['actor_id'])
    from . import source_query_service as source,runtime_documents as d
    try:
        source.checked(db,p,row)
        if row.status=='cancelled':job.status='cancelled';return 0
        original=_creation(db,p,row);linked=_link(db,p,row)
        link,search,child,prepared=linked
        need(prepared is None,ERROR)
        if child.status in {'queued','running'}:
            job.available_at=m.now()+timedelta(seconds=2)
            return 0
        need(child.status=='succeeded' and search.status=='completed',
            '原正文查找未成功结算；已保存原过程，没有自动重试或另建搜索')
        _live(db,p,original,physical=True)
        # This existing checker still reads the fixed originals/citations.
        # The per-call document proof avoids an H-squared hit traversal.
        from .instance_material_sources import _document_proof
        scope=copy.deepcopy(original['base_scope']);scope['document_search_ids']=[search.id]
        proof=_document_proof(db,p,scope,search.id)
        need(proof['pin']['result_hash'] is not None and proof['result'] is not None,ERROR)
        waited=_one(db,p,WAIT,row.id)
        ready=_save(db,p,PREPARED,row,link_hash=link['record_hash'],wait_hash=waited['record_hash'],
            search_result_hash=digest(proof['result']))
        _save(db,p,WAKE,row,prepared_hash=ready['record_hash'],parent_job_id=job.id)
        job.status='queued';job.available_at=m.now();job.lease_until=None;job.lease_token=None
        row.data={**row.data,'progress':'所选报告正文查找已完成，等待本次资料答复继续'};row.version+=1
        return 1
    except HTTPException as error:
        # Waiting is server-owned, nonleased metadata. An invalid/failed
        # dependency can stop the original question, never create a retry.
        if error.status_code not in {403,404,409,422}:raise
        message='原正文搜索、来源或当前批准无法继续；原问题和处理记录保留'
        if not _events(db,p,FAILED,row.id):_save(db,p,FAILED,row,reason=message)
        row.status='failed';row.version+=1;row.data={**row.data,'error':message,'progress':message}
        job.status='failed';job.data={**job.data,'error':message,'finished_at':m.now().isoformat()}
        return 0


def poll_once(workspace_id=None,*,job_ids=None,limit=20):
    """Bounded wakeup of this typed wait only, not a generic worker/runner.

    Each candidate is passed with its actual workspace and exact Job ID;
    old/nonwaiting Jobs and other kinds are never claimed or executed here.
    A post-child callback is only an optimization: this scan repairs a lost
    callback/wakeup after restart without any GET having side effects.
    """
    from .db import engine
    need(type(limit) is int and 1<=limit<=50,'报告等待扫描上限无效',422)
    if workspace_id is not None:uuid(workspace_id)
    if job_ids is not None:
        need(type(job_ids) is list and 0<len(job_ids)<=50 and len(set(job_ids))==len(job_ids),ERROR)
        for identity in job_ids:uuid(identity)
    with Session(engine()) as db:
        query=select(m.Job.id,m.Job.workspace_id).where(m.Job.kind=='data_query',m.Job.status==PHASE,
            m.Job.data[MARKER].is_not(None)).order_by(m.Job.available_at,m.Job.id).limit(limit)
        if workspace_id is not None:query=query.where(m.Job.workspace_id==workspace_id)
        if job_ids is not None:query=query.where(m.Job.id.in_(job_ids))
        candidates=list(db.execute(query));db.rollback()
        count=0
        for identity,workspace in candidates:
            try:
                count+=reconcile_one(db,workspace_id=workspace,job_id=identity);db.commit()
            except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
                db.rollback()
        return count


def child_finished(workspace_id,child_job_id):
    """Post-commit hint only; durable typed polling remains the recovery path."""
    from .db import engine
    uuid(workspace_id);uuid(child_job_id)
    with Session(engine()) as db:
        links=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==workspace_id,
            m.Audit.action==LINK,m.Audit.data['child_job_id'].as_string()==child_job_id).limit(2)))
        if not links:return 0
        need(len(links)==1,ERROR)
        parents=list(db.scalars(select(m.Job.id).where(m.Job.workspace_id==workspace_id,m.Job.kind=='data_query',
            m.Job.status==PHASE,m.Job.data['query_id'].as_string()==links[0].object_id).limit(2)))
        need(len(parents)<=1,ERROR)
    return poll_once(workspace_id,job_ids=parents) if parents else 0
