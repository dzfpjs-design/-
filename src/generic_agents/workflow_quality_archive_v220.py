"""Generate and save INTERNAL technical artifacts, not a formal deliverable.

side_effect=False follows local-model RESULT artifact persistence: this tool
does create local Evidence, while it never changes originals, objects, device
state, an external system, a publication, or independent data reception.
"""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from . import workflow_data_quality_v219 as quality
from . import workflow_graph_scope as scope
from .contracts import Manifest, digest
from .config import settings
from .models import Audit, Evidence, Invocation, Job, Task
from .services import audit, scoped, validate

HANDLER = 'workflow_task_quality_archive_v220'
RESULT = 'workflow-quality-archive-result-v220'
MACHINE = 'workflow-quality-archive-machine-v220'
KEY = 'workflow_quality_archive_v220'
SAVED = 'workflow.quality_archive.saved_v220'
PRODUCED = 'workflow.quality_archive.produced_v220'
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
_PENDING = 'workflow_quality_archive_result_transaction_v220'
FLAGS = {'internal_technical_summary': True, 'formal_deliverable': False,
    'physical_state_verified': False, 'species_identification_verified': False,
    'semantic_completeness_verified': False, 'independent_data_ack': False,
    'originals_deleted': False, 'originals_modified': False, 'cloud_authorized': False}


def need(ok, message='内部技术汇总、原回执或成对文件无法核对；未重建或替换原记录', code=409):
    if not ok: raise HTTPException(code, message)


def input_schema():
    return quality.obj({'quality_report': quality.report_schema(), 'dedup_report': quality.report_schema()})


def artifact_schema(media):
    return quality.obj({'evidence_id': quality.ident(), 'sha256': quality.hashed(),
        'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ARTIFACT_BYTES}, 'media_type': {'const': media}})


def output_schema():
    return quality.obj({'schema': {'const': RESULT}, 'technical_status': {'enum': ['pass','needs_review','blocked']},
        'source_report_hashes': quality.obj({'quality': quality.hashed(), 'dedup': quality.hashed()}),
        'artifacts': quality.obj({'markdown': artifact_schema('text/markdown'), 'machine_json': artifact_schema('application/json')}),
        'archive_hash': quality.hashed(), **{key: {'const': value} for key,value in FLAGS.items()}})


def manifest():
    return Manifest(kind='tool', namespace='task_quality_archive_v220', name=HANDLER, version='1.0.0',
        owner='平台内部技术汇总', description='生成并保存内部技术汇总（中文Markdown及机器JSON）；保留全部原件，不构成正式归档、生态结论或独立接收',
        allowed_agent_roles=['data'], input_schema=input_schema(), output_schema=output_schema(),
        execution={'adapter':'builtin','handler':HANDLER,'side_effect':False},
        data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False},
        budget={'max_attempts':1,'max_concurrency':1}, dependencies=[],
        specification={'schema':'workflow-quality-archive-contract-v220','internal_result_artifact':True,
            'creates_local_evidence':True,'no_formal_archive':True})


def validate_manifest(value):
    data=value.model_dump(mode='json') if hasattr(value,'model_dump') else copy.deepcopy(value)
    expected=manifest().model_dump(mode='json')
    for key in ('kind','description','allowed_agent_roles','input_schema','output_schema','execution','data_policy','budget','dependencies','specification'):
        need(quality.same(data.get(key),expected[key]), '内部技术汇总能力须固定本地数据角色、完整输入输出及内部Artifact边界',422)
    return data


def _sources(db,p,task,inputs):
    validate(input_schema(),inputs)
    from .workflow_task_context_v196 import ProducerPending
    try: quality.checked_quality_report(db,p,task,inputs['quality_report'])
    except ProducerPending:
        # A successful dedup tool could not have consumed a still-running
        # quality producer. Do not wait on that anomaly using the dedup pin.
        need(False,'前置质检链尚未终结或已变化，未当作可归档的真实去重来源')
    producer=inputs['dedup_report']['producer']
    need(producer['workspace_id']==p.workspace_id and producer['task_id']==task.id)
    original_job=scoped(db,Job,producer['job_id'],p)
    need(quality.same(original_job.data.get('inputs',{}).get('quality_report'),inputs['quality_report']),
        '去重原调用没有消费所给质检报告，未拼接不同来源')
    quality.checked_dedup_report(db,p,task,inputs['dedup_report'])
    need(inputs['quality_report']['source_inventory_hash']==inputs['dedup_report']['source_inventory_hash']
        and inputs['quality_report']['entity_context_hash']==inputs['dedup_report']['entity_context_hash'])
    return copy.deepcopy(producer)


def _bound(db,p,task,job,inputs):
    validate(input_schema(),inputs)
    actual,_,_,graph,state,activation,node=scope._frozen(db,p,task.id,job.data['activation_id'])
    scope._job_matches(actual,job,state,activation,node)
    need(task.workspace_id==p.workspace_id==job.workspace_id and node['actor_id']==p.user_id
        and node['execution']['agent_role']=='data' and quality.same(job.data['inputs'],inputs))
    for field in ('quality_report','dedup_report'):
        ref=graph['references'].get(node['execution']['input_bindings'].get(field))
        need(type(ref) is dict and ref.get('kind')=='object' and ref.get('source')=='output'
            and ref.get('field')=='report' and ref.get('nullable') is False
            and ref.get('node_id')==inputs[field]['producer']['node_id']
            and field not in node['execution']['arguments'],
            '技术汇总须消费本图两个真实上游报告输出，不能用客户输入或常量替代')


def producer_wait_pin(db,p,task,job,inputs):
    """Pre-Invocation wait hook; only the dedup producer may be finishing."""
    _bound(db,p,task,job,inputs)
    return _sources(db,p,task,inputs)


def _safe_report(report):
    """Opaque technical projection; deliberately omits even original metadata labels."""
    validate(quality.report_schema(),report)
    need(report['report_hash']==digest({k:v for k,v in report.items() if k!='report_hash'}))
    issues=[]
    for row in report['issues']:
        try:quality.issue(row['code']);code=row['code']
        except KeyError:code='unrecognized_technical_issue'
        identity=row['evidence_id']
        if not isinstance(identity,str) or quality.UUID.fullmatch(identity) is None:identity=None
        issues.append({'code':code,'severity':row['severity'],'evidence_id':identity})
    return {key:copy.deepcopy(report[key]) for key in ('report_hash','operation','producer',
        'source_inventory_hash','entity_context_hash','quality_status','attributes','duplicate_groups','digest_groups')} | {
        'files':[{key:row[key] for key in ('evidence_id','expected_sha256','actual_sha256','size_bytes',
            'hash_verified','format_verified')} for row in report['files']],
        'issues':issues}


def render_artifacts(inputs,*,task_id,invocation_id,created_at):
    """Pure rendering. This does not authenticate reports or grant permission."""
    validate(input_schema(),inputs)
    from datetime import datetime
    need(quality.UUID.fullmatch(task_id) is not None and quality.UUID.fullmatch(invocation_id) is not None
        and type(created_at) is str and len(created_at)<=80 and datetime.fromisoformat(created_at).tzinfo is not None)
    need(inputs['quality_report']['operation']=='quality' and inputs['dedup_report']['operation']=='deduplicate'
        and inputs['quality_report']['producer']['task_id']==inputs['dedup_report']['producer']['task_id']==task_id)
    source={key:_safe_report(inputs[key]) for key in ('quality_report','dedup_report')}
    statuses={row['quality_status'] for row in source.values()}
    status='blocked' if 'blocked' in statuses else 'needs_review' if 'needs_review' in statuses else 'pass'
    machine={'schema':MACHINE,'task_id':task_id,'invocation_id':invocation_id,'created_at':created_at,
        'technical_status':status,'source_reports':source,**FLAGS}
    raw=json.dumps(machine,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    labels={'pass':'技术核验通过','needs_review':'仍需技术复核','blocked':'存在技术阻断'}
    lines=['# 任务内部技术汇总','','本文件记录原件技术核验与同SHA字节分组，属于内部工作材料。',
        '不构成物种识别、生态评估、业务字段语义齐全、正式对外成果或数据管理独立接收。','',
        '## 核验结果','', '质检：'+labels[source['quality_report']['quality_status']],
        '去重检查：'+labels[source['dedup_report']['quality_status']],
        '本汇总技术状态：'+labels[status],'', '## 原件技术明细','',
        '| 原件 | 字节核验 | 格式核验 | 字节数 |','| --- | --- | --- | --- |']
    for i,row in enumerate(source['dedup_report']['files'],1):
        lines.append('| 原件'+str(i)+' | '+('一致' if row['hash_verified'] else '未确认')+' | '
            +('已核验' if row['format_verified'] else '待核验')+' | '+str(row['size_bytes'] if row['size_bytes'] is not None else '未知')+' |')
    if not source['dedup_report']['files']:lines.append('| 未有可核实原件 | 未确认 | 待核验 | 未知 |')
    lines+=['','## 待处理技术问题','']
    problems=source['quality_report']['issues']+source['dedup_report']['issues']
    if not problems:lines.append('两份原技术报告未列出技术问题；这不证明业务语义或现场状态正确。')
    else:
        for row in problems:
            # Reject unknown codes rather than echoing arbitrary source text.
            try:message=quality.issue(row['code'])['message']
            except KeyError:message='原报告含未识别技术问题代码，需要人工核对原回执。'
            lines.append('- '+message)
    lines+=['','## 同SHA字节分组','',
        '发现'+str(len(source['dedup_report']['duplicate_groups']))+'组完全相同字节的证据引用；未删除或覆盖任何原件。',
        '同SHA只表示文件字节一致，不表示同一物种、同一个体或同一现场事件。','',
        '## 证据与报告索引','', '质检报告摘要：`'+source['quality_report']['report_hash']+'`',
        '去重报告摘要：`'+source['dedup_report']['report_hash']+'`']
    for i,row in enumerate(source['dedup_report']['files'],1):
        lines.append('- 原件'+str(i)+'：证据引用 `'+row['evidence_id']+'`；实际SHA `'+str(row['actual_sha256'] or '未知')+'`。')
    lines+=['','## 使用边界','',
        '本步骤只生成并保存技术汇总及引用，不修改、删除原件或原报告，不发布经验，不更新管理对象状态。',
        '原件当前可用性以技术问题为准；候选结果仍需相应业务核验。没有新增独立接收确认。','']
    markdown='\n'.join(lines).encode('utf-8')
    need(0<len(markdown)<=MAX_ARTIFACT_BYTES and 0<len(raw)<=MAX_ARTIFACT_BYTES,
        '完整技术汇总超过单文件安全容量，未截取内容后当作完整成果',413)
    return {'markdown':markdown,'machine_json':raw},status


@contextmanager
def workspace_directory(root,workspace_id,*,create=True):
    need(type(workspace_id) is str and quality.UUID.fullmatch(workspace_id) is not None)
    root=Path(root)
    if not root.is_absolute():root=Path.cwd()/root
    handles=[]
    try:
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY);handles.append(fd)
        for part in (*root.parts[1:],workspace_id):
            need(part not in {'','.', '..'})
            try:next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            except FileNotFoundError:
                if not create:raise
                try:os.mkdir(part,mode=0o700,dir_fd=fd)
                except FileExistsError:pass
                next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            fd=next_fd;handles.append(fd)
        yield fd
    finally:
        for fd in reversed(handles):os.close(fd)


def _identity(fd):
    item=os.fstat(fd);need(stat.S_ISDIR(item.st_mode));return item.st_dev,item.st_ino


def _directory_unchanged(root,workspace_id,fd):
    with workspace_directory(root,workspace_id,create=False) as current:
        need(_identity(fd)==_identity(current),'内部技术文件目录已变化，未登记为原保存结果')


def _put(fd,content):
    need(type(content) is bytes and 0<len(content)<=MAX_ARTIFACT_BYTES)
    hashed=hashlib.sha256(content).hexdigest();name=hashed+'.bin'
    temporary='.quality-artifact-'+uuid4().hex+'.tmp'
    opened=None
    try:
        opened=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        offset=0
        while offset<len(content):
            written=os.write(opened,content[offset:offset+65536]);need(written>0);offset+=written
        os.fsync(opened);os.close(opened);opened=None
        try:os.link(temporary,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
        except FileExistsError:pass
        os.fsync(fd)
    finally:
        if opened is not None:os.close(opened)
        try:os.unlink(temporary,dir_fd=fd)
        except FileNotFoundError:pass
    return hashed


def _time(call):
    from datetime import UTC, datetime
    value=call.created_at
    need(isinstance(value,datetime) and value.utcoffset() is not None,
        '内部技术汇总的已保存时间必须明确带时区，未猜测本机时区',422)
    return value.astimezone(UTC).isoformat()


def _seal(value,key):return {**value,key:digest(value)}


def _output(status,inputs,artifacts):
    return _seal({'schema':RESULT,'technical_status':status,'source_report_hashes':{
        'quality':inputs['quality_report']['report_hash'],'dedup':inputs['dedup_report']['report_hash']},
        'artifacts':artifacts,**FLAGS},'archive_hash')


def checked_output(value):
    validate(output_schema(),value)
    need(value['archive_hash']==digest({k:v for k,v in value.items() if k!='archive_hash'}))
    need(len({row['evidence_id'] for row in value['artifacts'].values()})==2)
    return copy.deepcopy(value)


def _pin(call,job,node,inputs,output):
    return _seal({'schema':'workflow-quality-archive-binding-v220','invocation_id':call.id,'job_id':job.id,
        'task_id':job.data['task_id'],'actor_id':job.data['actor_id'],'activation_id':job.data['activation_id'],
        'node_id':node['id'],'capability_id':call.capability_id,'capability_hash':call.data['capability_hash'],
        'input_hash':digest(inputs),'created_at':_time(call),'source_report_hashes':output['source_report_hashes'],
        'artifacts':copy.deepcopy(output['artifacts']),'output_hash':digest(output)},'binding_hash')


def _evidence_marker(pin,kind):
    return {'schema':'workflow-quality-archive-evidence-v220','invocation_id':pin['invocation_id'],
        'task_id':pin['task_id'],'job_id':pin['job_id'],'kind':kind,'binding_hash':pin['binding_hash']}


def _audit_rows(db,p,action,identity):
    return list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==action,Audit.object_id==identity)))


def _negative(db,p,call):
    traces=list(db.scalars(select(Evidence.id).where(Evidence.workspace_id==p.workspace_id,
        Evidence.data[KEY]['invocation_id'].as_string()==call.id).limit(1)))
    completed=list(db.scalars(select(Audit.id).where(Audit.workspace_id==p.workspace_id,
        Audit.action=='workflow.graph.node_completed',Audit.data['invocation_id'].as_string()==call.id).limit(1)))
    need(KEY not in call.data and not traces and not completed and not _audit_rows(db,p,SAVED,call.id)
        and not _audit_rows(db,p,PRODUCED,call.id),
        '原汇总已有独立保存痕迹或部分回执，未补建、覆盖或重新执行')


def execute(db,p,cap,inputs,key):
    validate_manifest(cap.data);validate(input_schema(),inputs)
    task,job,node,_=scope.graph_context(db,p,key,cap)
    _bound(db,p,task,job,inputs);_sources(db,p,task,inputs)
    rows,problems,_,source_hash,entity_hash=quality.inventory(db,p,task)
    need(source_hash==inputs['dedup_report']['source_inventory_hash']
        and entity_hash==inputs['dedup_report']['entity_context_hash'],
        '技术检查后任务原件库存或对象版本已变化，未将旧报告当作当前汇总依据')
    current_files,_=quality.inspect_files(rows,root=settings().storage_dir,workspace_id=p.workspace_id,source_issues=problems)
    need(all(row['hash_verified'] for row in current_files),
        '原件当前字节完整性无法核对，未将旧技术状态写成当前汇总')
    call=db.scalar(select(Invocation).where(Invocation.workspace_id==p.workspace_id,Invocation.idempotency_key==key))
    need(call is not None and call.status=='running' and call.capability_id==cap.id
        and call.data.get('job_id')==job.id and call.data.get('input_hash')==digest(inputs))
    _negative(db,p,call)
    content,status=render_artifacts(inputs,task_id=task.id,invocation_id=call.id,created_at=_time(call))
    from .evidence import save_immutable
    # Pair metadata, source marker and SAVED are one SAVEPOINT inside the
    # original Gateway result transaction. Content-addressed orphan FILES after
    # an interruption are never treated as successful Evidence or repaired.
    need(_PENDING not in db.info)
    transaction=db.begin_nested()
    try:
        saved={};evidence_rows={}
        with workspace_directory(settings().storage_dir,p.workspace_id) as fd:
            for kind,media in (('markdown','text/markdown'),('machine_json','application/json')):
                hashed=_put(fd,content[kind]);_directory_unchanged(settings().storage_dir,p.workspace_id,fd)
                evidence=save_immutable(db,p,content[kind],media,{'modality':'internal_technical_artifact',
                    'purpose':KEY,'archive_kind':kind},existing_directory_fd=fd)
                need(evidence.sha256==hashed)
                saved[kind]={'evidence_id':evidence.id,'sha256':hashed,'size_bytes':len(content[kind]),'media_type':media}
                evidence_rows[kind]=evidence
            _directory_unchanged(settings().storage_dir,p.workspace_id,fd)
            output=checked_output(_output(status,inputs,saved));pin=_pin(call,job,node,inputs,output)
            call.data={**call.data,KEY:pin}
            for kind,evidence in evidence_rows.items():evidence.data={**evidence.data,KEY:_evidence_marker(pin,kind)}
            audit(db,p,SAVED,call.id,{'pin':pin,'output':output})
            db.flush()
            _directory_unchanged(settings().storage_dir,p.workspace_id,fd)
            for kind,evidence in evidence_rows.items():
                raw=quality.read_original(settings().storage_dir,p.workspace_id,evidence,remaining=MAX_ARTIFACT_BYTES)
                need(raw==content[kind])
        db.info[_PENDING]=(call.id,transaction)
        return output
    except BaseException:
        transaction.rollback()
        raise


def _saved(db,p,call,output):
    """Historical proof, without writes, new authorization, or current inventory."""
    output=checked_output(output)
    job=scoped(db,Job,call.data.get('job_id'),p);task=scoped(db,Task,job.data.get('task_id'),p)
    inputs=job.data.get('inputs');validate(input_schema(),inputs)
    need(call.workspace_id==p.workspace_id==job.workspace_id==task.workspace_id
        and job.kind==scope.KIND and job.data['invocation_key']==call.idempotency_key
        and call.data.get('input_hash')==digest(inputs)==job.data.get('input_hash')
        and call.data.get('role')=='data')
    # Immutable original inputs/producers remain independently verifiable;
    # this is NOT a fresh archive/FS permission or a source change operation.
    _sources(db,p,task,inputs)
    _,_,_,graph,state,activation,node=scope._frozen(db,p,task.id,job.data['activation_id'])
    scope._job_matches(task,job,state,activation,node)
    need(node['kind']=='ability' and node['execution']['agent_role']=='data'
        and node['execution']['capability_id']==call.capability_id
        and node['execution']['capability_hash']==call.data.get('capability_hash'))
    pin=_pin(call,job,node,inputs,output)
    need(quality.same(call.data.get(KEY),pin))
    anchors=_audit_rows(db,p,SAVED,call.id)
    need(len(anchors)==1 and anchors[0].actor_id==job.data['actor_id']
        and quality.same(anchors[0].data,{'pin':pin,'output':output}))
    expected,status=render_artifacts(inputs,task_id=task.id,invocation_id=call.id,created_at=_time(call))
    need(status==output['technical_status'])
    all_rows=list(db.scalars(select(Evidence).where(Evidence.workspace_id==p.workspace_id,
        Evidence.data[KEY]['invocation_id'].as_string()==call.id)))
    need({row.id for row in all_rows}=={item['evidence_id'] for item in output['artifacts'].values()} and len(all_rows)==2)
    for kind,descriptor in output['artifacts'].items():
        evidence=scoped(db,Evidence,descriptor['evidence_id'],p)
        need(evidence.sha256==descriptor['sha256']==hashlib.sha256(expected[kind]).hexdigest()
            and evidence.media_type==descriptor['media_type'] and evidence.size_bytes==descriptor['size_bytes']==len(expected[kind])
            and evidence.data.get('purpose')==KEY and evidence.data.get('archive_kind')==kind
            and evidence.data.get('cloud_model_ids')==[] and evidence.data.get('uploaded_by')==job.data['actor_id']
            and quality.same(evidence.data.get(KEY),_evidence_marker(pin,kind)))
        upload=_audit_rows(db,p,'evidence.upload',evidence.id)
        need(len(upload)==1 and upload[0].actor_id==job.data['actor_id'] and quality.same(upload[0].data,{
            'media_type':descriptor['media_type'],'sha256':descriptor['sha256'],'cloud_model_id':None,'consent':False}))
        need(quality.read_original(settings().storage_dir,p.workspace_id,evidence,remaining=MAX_ARTIFACT_BYTES)==expected[kind])
    return pin,output


def mark_produced(db,p,call,job,output):
    pending=db.info.get(_PENDING)
    need(pending is not None and pending[0]==call.id)
    try:
        need(call.status=='succeeded' and call.data.get('job_id')==job.id
            and call.data.get('output') is not None and quality.same(call.data['output'],output))
        pin,output=_saved(db,p,call,output)
        need(p.user_id==job.data['actor_id'] and not _audit_rows(db,p,PRODUCED,call.id))
        audit(db,p,PRODUCED,call.id,{'schema':'workflow-quality-archive-produced-v220',
            'binding_hash':pin['binding_hash'],'output_hash':digest(output),
            'task_id':pin['task_id'],'job_id':job.id,'invocation_id':call.id})
        pending[1].commit()
    except BaseException:
        if pending[1].is_active:pending[1].rollback()
        raise
    finally:
        db.info.pop(_PENDING,None)


def rollback_failed_outcome(db,call):
    """Only the new handler's failed result path, before setting failure fields."""
    pending=db.info.get(_PENDING)
    if pending is None:return
    need(pending[0]==call.id)
    db.info.pop(_PENDING,None)
    pending[1].rollback()


def checked_saved_result(db,p,call):
    need(call.status=='succeeded','原执行没有完整成功回执，未将孤儿文件或部分保存晋级成功')
    pin,output=_saved(db,p,call,call.data.get('output'))
    anchors=_audit_rows(db,p,PRODUCED,call.id)
    need(len(anchors)==1 and anchors[0].actor_id==pin['actor_id'] and quality.same(anchors[0].data,{
        'schema':'workflow-quality-archive-produced-v220','binding_hash':pin['binding_hash'],
        'output_hash':digest(output),'task_id':pin['task_id'],'job_id':pin['job_id'],'invocation_id':call.id}))
    return output
