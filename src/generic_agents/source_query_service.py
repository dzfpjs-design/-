"""Source-aware DataRun lifecycle, immutable answers and exact-request recovery.

Uses the existing data_query worker/lease. No HTTP/core circular imports, new
tables, device actions, automatic document-search Jobs or cloud fallbacks.
"""
import copy
import hashlib
from uuid import uuid4
from sqlalchemy import or_, select, text
from fastapi import HTTPException
from .models import Audit, DataRun, Job, User, now
from .services import audit, scoped, require
from .contracts import digest
from .source_query_contracts import (MARKER, CREATE_AUDIT, RESULT_AUDIT, COMMAND_AUDIT,
    need, parse, sha)

MAX_HISTORY = 1000
PAGE_SIZE = 20
ERROR = '资料查询的原请求、答案或独立证明无法核对；原记录保留'


def reader(db, p):
    from .media_daily_task_adapter import current_actor
    current_actor(db, p)


def _events(db, p, action, identity):
    return list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).limit(101)))


def is_v2(db, row, job=None):
    from . import document_question_service as documents
    if documents.is_record(db,row,job=job):return True
    if MARKER in row.data or type(row.data.get('request')) is dict and row.data['request'].get('schema') == 'data-query-create-v2': return True
    if type(row.data.get('result')) is dict and type(row.data['result'].get('answer')) is dict and row.data['result']['answer'].get('schema') == 'material-answer-v2': return True
    if job is not None and MARKER in job.data: return True
    if db.scalar(select(Audit.id).where(Audit.workspace_id == row.workspace_id,
        Audit.object_id == row.id, Audit.action.in_([CREATE_AUDIT, RESULT_AUDIT, COMMAND_AUDIT])).limit(1)): return True
    return db.scalar(select(Job.id).where(Job.workspace_id == row.workspace_id, Job.kind == 'data_query',
        Job.data['query_id'].as_string() == row.id, Job.data[MARKER].is_not(None)).limit(1)) is not None


def checked(db, p, row, *, check_result=True):
    """Metadata only; source/device changes cannot erase a saved request."""
    try:
        need(row.workspace_id == p.workspace_id and type(row.data.get(MARKER)) is dict)
        events = _events(db, p, CREATE_AUDIT, row.id); need(len(events) == 1)
        event = events[0]; value = event.data
        need(event.actor_id == row.owner_id == value['actor_id'] and value['query_id'] == row.id
            and value['record_hash'] == digest({k:v for k,v in value.items() if k != 'record_hash'}))
        original = parse(value['exact_body'].encode('utf8'), 'create')
        need(hashlib.sha256(value['exact_body'].encode('utf8')).hexdigest() == value['body_sha256']
            and original == row.data['request'] and row.question == original['question']
            and value['request_hash'] == digest(original)
            and value['instance_id'] == original['source_scope']['instance_id']
            and value['release_id'] == original['source_scope']['release_id']
            and row.data[MARKER] == {'schema':'source-query-record-v2', 'actor_id':row.owner_id,
                'instance_id':value['instance_id'], 'release_id':value['release_id'],
                'request_id':original['request_id'], 'body_sha256':value['body_sha256'], 'job_id':value['job_id']})
        job = scoped(db, Job, value['job_id'], p)
        need(row.data['job_id'] == job.id and job.kind == 'data_query'
            and job.idempotency_key == 'query:'+row.id and job.data['query_id'] == row.id
            and job.data['actor_id'] == row.owner_id
            and job.data.get(MARKER) == {'query_id':row.id,'creation_hash':value['record_hash'],
                'instance_id':value['instance_id']})
        jobs = list(db.scalars(select(Job.id).where(Job.workspace_id == p.workspace_id,
            Job.kind == 'data_query',Job.data['query_id'].as_string() == row.id).limit(2)))
        need(jobs == [job.id])
        commands = _events(db, p, COMMAND_AUDIT, row.id)
        need(len(commands) <= 100 and commands)
        seen = set(); notes = []; creates = []; cancels = []
        for command in commands:
            command_value = command.data; operation = command_value['operation']
            body = parse(command_value['exact_body'].encode('utf8'), operation)
            need(command.actor_id == command_value['actor_id'] and command_value['query_id'] == row.id
                and command_value['request_id'] == body['request_id'] and body['request_id'] not in seen
                and command_value['body_sha256'] == hashlib.sha256(command_value['exact_body'].encode('utf8')).hexdigest()
                and command_value['record_hash'] == digest({k:v for k,v in command_value.items() if k != 'record_hash'}))
            need(type(command_value['written_version']) is int and 1 <= command_value['written_version'] <= row.version)
            seen.add(body['request_id'])
            if operation == 'create':
                need(body == original and command.actor_id == row.owner_id
                    and command_value['body_sha256'] == value['body_sha256']); creates.append(command)
            elif operation == 'note':
                note = command_value['note']
                need(note['text'] == body['text'] and note['actor_id'] == command.actor_id
                    and command_value['written_version'] == body['version']+1)
                notes.append(note)
            else:
                need(command_value['reason'] == body['reason']
                    and command_value['written_version'] == body['version']+1)
                cancels.append(command)
        need(len(creates) == 1 and len(cancels) <= 1)
        need(sorted(notes,key=lambda x:(x['created_at'],x['id'])) == sorted(row.data.get('notes',[]),key=lambda x:(x['created_at'],x['id'])))
        if cancels: need(row.status == 'cancelled' and row.data.get('cancel_reason') == cancels[0].data['reason'])
        if row.status == 'cancelled': need(len(cancels) == 1)
        from . import document_question_service as documents
        if documents.is_record(db,row,job=job):documents.check_metadata(db,p,row,job)
        if check_result: _checked_answer(db, p, row, job)
        return row, job, event
    except HTTPException: raise
    except (KeyError, ValueError, TypeError, AttributeError, IndexError): raise HTTPException(409, ERROR) from None


def result_proof(row):
    result = row.data['result']
    return {'schema':'source-query-result-proof-v2','query_id':row.id,'job_id':row.data['job_id'],
        'request_hash':digest(row.data['request']),'result_hash':row.data['result_hash'],
        'snapshot_hash':digest(result['source_snapshot']),'tool':copy.deepcopy(result['tool'])}


def _checked_answer(db, p, row, job):
    events = _events(db, p, RESULT_AUDIT, row.id)
    if 'result' not in row.data:
        need(not events and 'result_hash' not in row.data and row.status not in {'sufficient','insufficient','needs_clarification'}
            and not (job.status == 'succeeded' and row.status != 'cancelled'))
        return
    need(len(events) == 1 and events[0].actor_id == row.owner_id
        and row.data['result_hash'] == digest(row.data['result']) and events[0].data == result_proof(row))
    result = row.data['result']; answer = result['answer']
    expected_status=('needs_clarification' if answer['answer_status'] in {'needs_clarification','unsupported'} else
        'insufficient' if answer['answer_status']=='partial' else 'sufficient')
    from . import document_question_service as documents
    scope=documents.effective_scope(db,p,row) if documents.is_record(db,row,job=job) else row.data['request']['source_scope']
    need(result['source_scope'] == scope
        and answer['answer_status'] in {'answered','partial','needs_clarification','unsupported'}
        and row.status == expected_status
        and answer['schema'] == 'material-answer-v2' and answer['coverage']['expected_sources_verified'] is False
        and type(result['cloud_calls']) is int and result['cloud_calls'] == 0)


def _lock(db, p, nonce):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':int(digest(
        {'workspace':p.workspace_id,'protocol':'source-query-v2','request':nonce})[:16],16)-(1<<63)})


def _command(db, p, row, operation, raw, **extra):
    body = parse(raw,operation)
    value = {'query_id':row.id,'actor_id':p.user_id,'operation':operation,'request_id':body['request_id'],
        'exact_body':raw.decode('utf8'),'body_sha256':hashlib.sha256(raw).hexdigest(),
        'written_version':row.version,**copy.deepcopy(extra)}
    value['record_hash']=digest(value)
    audit(db,p,COMMAND_AUDIT,row.id,value);db.flush()


def inventory(db, p, instance_id=None):
    reader(db,p)
    query = select(DataRun).where(DataRun.workspace_id == p.workspace_id,or_(DataRun.data[MARKER].is_not(None),
        DataRun.data['request']['schema'].as_string() == 'data-query-create-v2'))
    audits = select(Audit).where(Audit.workspace_id == p.workspace_id,Audit.action == CREATE_AUDIT)
    jobs = select(Job).where(Job.workspace_id == p.workspace_id,Job.kind == 'data_query',Job.data[MARKER].is_not(None))
    if instance_id is not None:
        from .models import DomainInstance
        scoped(db,DomainInstance,instance_id,p)
        query=query.where(or_(DataRun.data[MARKER]['instance_id'].as_string() == instance_id,
            DataRun.data['request']['source_scope']['instance_id'].as_string() == instance_id))
        audits=audits.where(Audit.data['instance_id'].as_string() == instance_id)
        jobs=jobs.where(Job.data[MARKER]['instance_id'].as_string() == instance_id)
    rows=list(db.scalars(query.limit(MAX_HISTORY+1)));events=list(db.scalars(audits.limit(MAX_HISTORY+1)))
    need(len(rows)<=MAX_HISTORY and len(events)<=MAX_HISTORY)
    need({row.id for row in rows} == {event.object_id for event in events} and len(rows)==len(events))
    # For an instance view, Jobs are checked against the exact known create
    # witnesses; global inventory additionally detects orphan Job traces.
    job_rows=list(db.scalars(jobs.limit(MAX_HISTORY+1)))
    need(len(job_rows)<=MAX_HISTORY and {row.id for row in rows} == {job.data['query_id'] for job in job_rows}
        and len(job_rows)==len(rows))
    for row in rows: checked(db,p,row,check_result=False)
    return rows


def get(db,p,identity):
    reader(db,p);row=scoped(db,DataRun,identity,p)
    need(is_v2(db,row),'此记录不属于V2来源查询',404)
    checked(db,p,row,check_result=False)
    return row


def recover(db,p,nonce,expected):
    reader(db,p)
    try:sha(expected)
    except ValueError:raise HTTPException(422,'原请求摘要无效') from None
    events=list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == COMMAND_AUDIT,Audit.data['request_id'].as_string() == nonce).limit(2)))
    need(len(events)<=1)
    base={'schema':'data-query-recovery-v2','request_id':nonce,'body_sha256':expected,'found':False,'result':None}
    if not events:
        # Cross-check retained create rows/audits/Jobs before saying unknown.
        inventory(db,p)
        traces=db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,Audit.action == CREATE_AUDIT,
            Audit.data['request_id'].as_string() == nonce).limit(1))
        rows=db.scalar(select(DataRun.id).where(DataRun.workspace_id == p.workspace_id,
            DataRun.data[MARKER]['request_id'].as_string() == nonce).limit(1))
        need(traces is None and rows is None)
        return base
    event=events[0]
    need(event.actor_id == p.user_id,'请由原操作者恢复这份原请求',403)
    need(event.data['body_sha256']==expected)
    row=get(db,p,event.object_id)
    result=command_view(db,p,row,event.data['operation'],nonce,expected)
    return {**base,'found':True,'result':result}


def command_view(db,p,row,operation,nonce,body_hash):
    return {'schema':'data-query-command-v2','operation':operation,'request_id':nonce,
        'body_sha256':body_hash,'query':view(db,p,row)}


def catalog(db,p,instance_id,batch_day):
    from . import instance_material_sources as providers
    reader(db,p)
    return providers.catalog(db,p,instance_id,batch_day)


def create(db,p,raw):
    body=parse(raw,'create');nonce=body['request_id'];body_hash=hashlib.sha256(raw).hexdigest()
    _lock(db,p,nonce);prior=recover(db,p,nonce,body_hash)
    if prior['found']:
        need(prior['result']['operation']=='create')
        return prior['result'],False
    require(p,'admin','operator')
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    from .models import DomainInstance
    scoped(db,DomainInstance,body['source_scope']['instance_id'],p,True)
    choices=catalog(db,p,body['source_scope']['instance_id'],body['source_scope']['batch_day'])
    need(choices['catalog_hash']==body['expected_catalog_hash']
        and choices['release_id']==body['source_scope']['release_id'],'资料目录或发布范围已变化，请重新核对')
    need(choices['permissions']['can_create'],'当前范围不能创建资料查询',403)
    need(set(body['source_scope']['business_ids']).issubset({item['id'] for item in choices['businesses']})
        and set(body['source_scope']['entity_ids']).issubset({item['id'] for item in choices['entities']})
        and set(body['source_scope']['document_search_ids']).issubset({item['id'] for item in choices['document_searches']}))
    from . import instance_material_sources as providers
    providers.validate_scope(db,p,copy.deepcopy(body['source_scope']))
    if body['parent_query_id']:
        parent=get(db,p,body['parent_query_id'])
        need(parent.data[MARKER]['instance_id']==body['source_scope']['instance_id'],'父查询不属于本实例')
    owner=db.get(User,p.user_id)
    need(owner is not None)
    row=DataRun(workspace_id=p.workspace_id,owner_id=p.user_id,question=body['question'],status='queued',version=1,
        data={'request':copy.deepcopy(body),'notes':[],'progress':'问题和明确范围已保存，等待本地资料核对'})
    db.add(row);db.flush()
    job=Job(workspace_id=p.workspace_id,kind='data_query',idempotency_key='query:'+row.id,
        data={'query_id':row.id,'actor_id':p.user_id})
    db.add(job);db.flush()
    value={'schema':'source-query-creation-v2','query_id':row.id,'actor_id':p.user_id,'owner_label':owner.username,
        'request_id':nonce,'instance_id':body['source_scope']['instance_id'],'release_id':body['source_scope']['release_id'],
        'job_id':job.id,'exact_body':raw.decode('utf8'),'body_sha256':body_hash,'request_hash':digest(body)}
    value['record_hash']=digest(value)
    row.data={**row.data,'job_id':job.id,MARKER:{'schema':'source-query-record-v2','actor_id':p.user_id,
        'instance_id':value['instance_id'],'release_id':value['release_id'],'request_id':nonce,
        'body_sha256':body_hash,'job_id':job.id}}
    job.data={**job.data,MARKER:{'query_id':row.id,'creation_hash':value['record_hash'],'instance_id':value['instance_id']}}
    audit(db,p,CREATE_AUDIT,row.id,value);_command(db,p,row,'create',raw)
    return command_view(db,p,row,'create',nonce,body_hash),True


def view(db,p,row):
    reader(db,p);row,job,event=checked(db,p,row,check_result=False)
    status=row.status;error=row.data.get('error');answer=None;result_hash=None
    try:
        _checked_answer(db,p,row,job)
        if 'result' in row.data:
            answer=copy.deepcopy(row.data['result']['answer']);result_hash=row.data['result_hash']
    except (HTTPException,KeyError,TypeError,ValueError):
        status='blocked_integrity';error='已保存答案或独立结果证明无法核对，原请求保留'
    if status!='blocked_integrity' and (job.status.startswith(('failed','blocked')) or job.status=='lease_lost'):
        status='failed';error='后台资料查询未能成功结算；已保存记录保留，没有重新查询或执行'
        answer=result_hash=None
    elif status!='blocked_integrity' and (job.status=='cancelled' or row.status=='cancelled'):
        status='cancelled';answer=result_hash=None
    elif status!='blocked_integrity' and job.status in {'queued','running'}:
        status='running' if row.status!='queued' or job.status=='running' else 'queued'
        answer=result_hash=None
    notes=[{'id':item['id'],'text':item['text'],'author_label':item['author_label'],'created_at':item['created_at']}
        for item in row.data.get('notes',[])]
    from . import document_question_service as documents
    scope=documents.display_scope(db,p,row) if documents.is_record(db,row,job=job) else row.data['request']['source_scope']
    return {'schema':'data-query-view-v2','id':row.id,'version':row.version,
        'instance_id':row.data[MARKER]['instance_id'],'release_id':row.data[MARKER]['release_id'],
        'owner_label':event.data['owner_label'],'created_at':row.created_at.isoformat(),'question':row.question,
        'source_scope':copy.deepcopy(scope),'status':status,
        'progress':row.data.get('progress',''),'error':error,'job':{'id':job.id,'status':job.status},
        'answer':answer,'result_hash':result_hash,'notes':notes,'permissions':{'can_read':True,
            'can_cancel':status in {'queued','running'} and 'result' not in row.data
                and p.role in {'admin','operator'} and (row.owner_id==p.user_id or p.role=='admin'),
            'can_note':p.role in {'admin','operator'}}}


def safe_reference(db,p,row):
    """V1 can point at V2 without exposing its private source snapshot/raw."""
    current=view(db,p,row)
    return {'schema':'data-query-reference-v144','id':row.id,'version':row.version,
        'status':current['status'],'question':row.question,'created_at':row.created_at.isoformat(),
        'reference_only':True,'family':'source_query_v2','context':{
            'instance_id':row.data[MARKER]['instance_id'],'task_id':None,
            'detail_path':'/api/v2/data/queries/'+row.id},
        'notice':'这是有来源范围的V2资料查询，请打开原查询查看答案和原请求；通用V1入口不能改写。'}


def _write(db,p,identity,raw,operation):
    body=parse(raw,operation);nonce=body['request_id'];body_hash=hashlib.sha256(raw).hexdigest()
    _lock(db,p,nonce);prior=recover(db,p,nonce,body_hash)
    if prior['found']:
        need(prior['result']['operation']==operation and prior['result']['query']['id']==identity)
        return body,prior['result'],None
    require(p,'admin','operator')
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    row=scoped(db,DataRun,identity,p,True);get(db,p,identity)
    need(row.version==body['version'],'原查询已推进，请保留待提交请求并重新核对当前版本')
    return body,None,row


def cancel(db,p,identity,raw):
    body,prior,row=_write(db,p,identity,raw,'cancel')
    if prior:return prior
    need(row.owner_id==p.user_id or p.role=='admin','请由原发起人或管理员停止',403)
    job=scoped(db,Job,row.data['job_id'],p);_checked_answer(db,p,row,job)
    from . import document_question_service as documents
    auto_wait=job.status==documents.PHASE and documents.waiting_cancel_allowed(db,p,row,job)
    need(row.status in {'queued','running'} and (job.status in {'queued','running'} or auto_wait) and 'result' not in row.data,'查询已结束或答案已保存，不能覆盖原答')
    row.status='cancelled';row.version+=1
    row.data={**row.data,'cancel_reason':body['reason'],'progress':'已停止这份查询，原请求和已有记录保留'}
    if auto_wait:
        job.status='cancelled';job.data={**job.data,'finished_at':now().isoformat()}
    _command(db,p,row,'cancel',raw,reason=body['reason'])
    return command_view(db,p,row,'cancel',body['request_id'],hashlib.sha256(raw).hexdigest())


def note(db,p,identity,raw):
    body,prior,row=_write(db,p,identity,raw,'note')
    if prior:return prior
    user=db.get(User,p.user_id);need(user is not None)
    notes=row.data.get('notes',[]);need(len(notes)<50,'人工解释已达50项，原解释保留',422)
    item={'id':str(uuid4()),'text':body['text'],'actor_id':p.user_id,'author_label':user.username,'created_at':now().isoformat()}
    row.version+=1;row.data={**row.data,'notes':[*notes,item]}
    _command(db,p,row,'note',raw,note=item)
    return command_view(db,p,row,'note',body['request_id'],hashlib.sha256(raw).hexdigest())


def execute(db,p,identity,*,cached_only=False):
    """Only normal leased data_query calls collect; recovery never recreates work."""
    from .leases import ownership,checkpoint
    row=get(db,p,identity);row,job,_=checked(db,p,row)
    expected=ownership(db)
    need(expected.get('job_id')==job.id and expected.get('lease_token')==job.lease_token and job.status=='running'
        and job.data['actor_id']==row.owner_id==p.user_id,'此查询必须由原持租后台任务执行')
    require(p,'admin','operator')
    if row.status=='cancelled':return {'status':'cancelled'}
    if 'result' in row.data:
        return {'status':row.status,'result':copy.deepcopy(row.data['result'])}
    from . import document_question_service as documents
    automatic=documents.is_record(db,row,job=job)
    if automatic:
        if documents.prepare_execution(db,p,row,job,cached_only=bool(cached_only or job.data.get('recovery_only'))):
            return {'status':documents.PHASE}
    elif cached_only or job.data.get('recovery_only'):
        raise HTTPException(409,'原查询恢复没有已保存完整答案；未重新扫描来源、创建搜索或发出工具请求')
    row.status='running';row.version+=1
    row.data={**row.data,'progress':'正在按明确实例、批次和资料类型核对本地来源'};checkpoint(db)
    from . import instance_material_sources as providers
    from .source_query_answer import execute_registered,TOOL
    scope=documents.effective_scope(db,p,row) if automatic else row.data['request']['source_scope']
    bundle=providers.collect(db,p,copy.deepcopy(scope))
    need(bundle['scope']==scope)
    projection=execute_registered(TOOL,row.question,bundle,now().isoformat())
    if automatic:projection=documents.finish_projection(db,p,row,projection)
    # Cancellation and metadata changes win before the result commit.
    db.expire_all();row=scoped(db,DataRun,identity,p,True);checked(db,p,row);reader(db,p)
    if row.status=='cancelled':return {'status':'cancelled'}
    require(p,'admin','operator')
    if automatic:documents.before_result(db,p,row)
    result={'source_scope':copy.deepcopy(bundle['scope']),'providers':copy.deepcopy(bundle['providers']),
        'source_snapshot':copy.deepcopy(bundle['snapshot']),'answer':projection,'tool':copy.deepcopy(TOOL),'cloud_calls':0}
    row.status=('needs_clarification' if projection['answer_status'] in {'needs_clarification','unsupported'} else
        'insufficient' if projection['answer_status']=='partial' else 'sufficient')
    row.version+=1;row.data={**row.data,'result':result,'result_hash':digest(result),
        'progress':'资料答复与来源已保存；查询执行状态不表示资料收齐或现场状态已认证'}
    audit(db,p,RESULT_AUDIT,row.id,result_proof(row));checkpoint(db)
    return {'status':row.status,'result':copy.deepcopy(result)}


def fail(db,p,job,reason):
    """Existing worker recovery can mark its own V2 target, never dispatch."""
    row=scoped(db,DataRun,job.data['query_id'],p,True)
    need(is_v2(db,row,job=job));checked(db,p,row)
    if row.status in {'queued','running'}:
        row.status='failed';row.version+=1;row.data={**row.data,'error':reason,'progress':'后台资料核对中断，原请求保留'}


def freshness(db,p,identity):
    row=get(db,p,identity);need('result' in row.data,'查询尚无已保存答案',409)
    checked(db,p,row)
    valid=False;physical=False;error=None
    try:
        from . import instance_material_sources as providers
        current=providers.recheck(db,p,copy.deepcopy(row.data['result']['source_snapshot']))
        valid=current['snapshot']==row.data['result']['source_snapshot']
        refs=[ref for record in current['records'] for ref in record['references']]
        physical=bool(refs) and all(ref['physical_recheck']=='checked' for ref in refs)
        if not valid:error='旧采用的来源或固定证据已变化；原答案未改，当前资料需另建查询'
        elif not physical:error='旧采用的元数据证明已核对，但没有完成全部原件重读；不能声称原件均已复核'
    except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
        error='旧采用的来源、原件或独立证明已缺失/变化；原答案保留，未改查当前来源'
    return {'schema':'data-query-freshness-v2','query_id':row.id,'originals_rechecked':valid and physical,
        'unchanged':valid and physical,'current_scope_checked':False,'error':error}
