"""Atomic ordinary registry preparation, not downloads, model work or device IO."""
import copy
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import select,text,or_,and_
from . import models as m, services
from .contracts import digest
from .services import audit,require,scoped,published
from .authored_workflow_store_v208 import need
from .authored_image_workflow_contracts_v243 import ToolsCommand,parse,OPERATIONS

KIND='prepare_authored_image_tools_v243'
ACTION='authored_image.tools_prepared_v243'


def manifests():
    from . import workflow_data_quality_v219 as quality,workflow_quality_archive_v220 as report
    return {'quality':quality.manifest(quality.QUALITY),'dedup':quality.manifest(quality.DEDUP),'report':report.manifest()}


def validate(cap,operation):
    from . import workflow_data_quality_v219 as quality,workflow_quality_archive_v220 as report
    need(operation in OPERATIONS and cap.kind=='tool' and cap.status=='published'
        and cap.content_hash==cap.validated_hash==digest(cap.data)
        and cap.namespace==cap.data.get('namespace') and cap.name==cap.data.get('name')
        and cap.release_version==cap.data.get('version'),
        '工具登记名称、版本或完整合同无法核对，未按损坏元数据采用')
    (report.validate_manifest if operation=='report' else quality.validate_manifest)(cap.data)
    expected=report.HANDLER if operation=='report' else quality.QUALITY if operation=='quality' else quality.DEDUP
    need(cap.data['execution']['handler']==expected)
    return cap


def item(cap,operation):
    return {'operation':operation,'id':cap.id,'name':cap.name,'hash':cap.content_hash,'release_version':cap.release_version}


def _lock(db,value):db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest(value)[:15],16)})


def _recover(db,p,identity,expected_hash):
    key='authored-image-tools:'+identity
    _lock(db,['authored-image-tools-command-v243',p.workspace_id,identity])
    rows=list(db.scalars(select(m.Job).where(m.Job.workspace_id==p.workspace_id,m.Job.idempotency_key==key).limit(2)))
    events=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==ACTION,
        m.Audit.data['request_id'].as_string()==identity).limit(2)))
    need(len(rows)<=1 and len(events)==len(rows))
    if not rows:return {'schema':'authored-image-tools-recovery-v243','found':False,'body':None,'result':None}
    row=rows[0];value=row.data
    body,exact,hashed=parse(value['exact_body'].encode('utf8'),ToolsCommand)
    need(row.kind==KIND and row.status=='succeeded' and row.attempts==0 and row.lease_token is None and row.lease_until is None
        and value['schema']=='authored-image-tools-command-record-v243' and value['operation']=='local_registry_only'
        and value['actor_id']==events[0].actor_id==p.user_id and body.request_id==identity
        and value['body_sha256']==hashed==expected_hash
        and value['record_hash']==digest({k:v for k,v in value.items() if k!='record_hash'})
        and events[0].object_id==row.id and events[0].data==value)
    result=value['result']
    need(result['schema']=='authored-image-tools-result-v243' and result['request_id']==identity
        and result['body_sha256']==hashed and result['can_execute'] is False and result['cloud_authorized'] is False
        and [entry['operation'] for entry in result['capabilities']]==list(OPERATIONS))
    return {'schema':'authored-image-tools-recovery-v243','found':True,'body':body.model_dump(mode='json'),'result':copy.deepcopy(result)}


def recover(db,p,identity,expected_hash):
    try:return _recover(db,p,identity,expected_hash)
    except HTTPException as error:
        if error.status_code in {403,409}:raise
        need(False,'原工具准备正文不符合保存合同，未重建成功')
    except (KeyError,TypeError,ValueError,AttributeError,UnicodeError):need(False,'原工具准备命令或独立审计无法核对，未重建成功')


def prepare(db,p,raw):
    body,exact,hashed=parse(raw,ToolsCommand)
    original=recover(db,p,body.request_id,hashed)
    if original['found']:return original['result']
    require(p,'admin')
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    _lock(db,['authored-image-tools-workspace-v243',p.workspace_id])
    expected_manifests=manifests()
    keys=[and_(m.Capability.namespace==value.namespace,m.Capability.name==value.name,
        m.Capability.release_version==value.version) for value in expected_manifests.values()]
    # Match the image-adoption registry lock order; unknown/draft records are
    # authenticated under the same locks, never overwritten by preparation.
    existing=list(db.scalars(select(m.Capability).where(m.Capability.workspace_id==p.workspace_id,
        or_(*keys)).order_by(m.Capability.id).with_for_update().limit(4)))
    need(len(existing)<=3)
    selected=[]
    for operation,manifest in expected_manifests.items():
        expected=manifest.model_dump(mode='json')
        rows=[cap for cap in existing if (cap.namespace,cap.name,cap.release_version)==(manifest.namespace,manifest.name,manifest.version)]
        need(len(rows)<=1)
        if rows:
            cap=rows[0];validate(cap,operation)
            need(cap.data==expected,'此内置名称已有不同或未发布的登记，原记录保留；未覆盖')
        else:
            cap=services.register(db,p,manifest)
            services.validate_capability(db,p,cap.id,cap.version)
            services.publish(db,p,cap.id,cap.version,'明确确认图像原件技术复核所需的本地固定工具')
            validate(cap,operation)
        selected.append(item(cap,operation))
    result={'schema':'authored-image-tools-result-v243','request_id':body.request_id,'body_sha256':hashed,
        'capabilities':selected,'can_execute':False,'cloud_authorized':False}
    value={'schema':'authored-image-tools-command-record-v243','actor_id':p.user_id,'request_id':body.request_id,
        'exact_body':exact,'body_sha256':hashed,'operation':'local_registry_only','result':result}
    value={**value,'record_hash':digest(value)}
    job=m.Job(workspace_id=p.workspace_id,kind=KIND,idempotency_key='authored-image-tools:'+body.request_id,
        status='succeeded',attempts=0,data=value)
    db.add(job);db.flush();audit(db,p,ACTION,job.id,value);db.flush()
    recover(db,p,body.request_id,hashed)
    return result
