"""Authenticated setup records and immutable materials, separate from runtime observations."""
import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse, Response
from pydantic import Field
from sqlalchemy import select,func
from sqlalchemy.dialects.postgresql import insert
from .contracts import Contract,digest
from .db import session
from .config import settings
from .models import SetupDraft, SetupRevision, SetupMaterial, Evidence, Job, uid, now
from .security import authenticated, Principal, require
from .services import scoped, audit, check_version
from .evidence import evidence_bytes

router = APIRouter(prefix='/api/setup')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
SUFFIXES = {'.pdf', '.docx', '.txt', '.md', '.csv', '.json', '.xlsx', '.tif', '.tiff', '.geojson', '.zip'}
MEDIA = {'.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.txt': 'text/plain',
         '.md': 'text/plain', '.csv': 'text/csv', '.json': 'application/json', '.geojson': 'application/geo+json',
         '.tif': 'image/tiff', '.tiff': 'image/tiff', '.zip': 'application/zip'}


def draft(db, p, lock=False):
    db.execute(insert(SetupDraft).values(id=uid(), workspace_id=p.workspace_id, data={}, version=1)
               .on_conflict_do_nothing(index_elements=['workspace_id']))
    query = select(SetupDraft).where(SetupDraft.workspace_id == p.workspace_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalar(query)


def draft_view(row):
    from .setup_automation import review_summary
    return {'id': row.id, 'revision': row.version, 'configuration': row.data,
            'understanding': {'status': 'available', 'reason': '选择已发布云模型并核对发送内容后生成待确认建议'},
            'automation_review':review_summary(row.data)}


def initial_configuration():
    """Structural blank state only; never invent a domain, connection or workflow."""
    return {'version':1,'resources':[],'connections':[],'materials':[],
            'instanceProfile':{},'businesses':[],'flows':{}}


def material_view(row, db, include_blocks=False):
    ev = db.get(Evidence, row.evidence_id)
    result = {'id': row.id, 'name': row.name, 'hash': row.sha256, 'size': ev.size_bytes,
              'type': ev.media_type, 'createdAt': row.created_at.isoformat(), 'archived': row.archived,
              'version': row.version, 'note': row.data.get('note', ''), 'kind': row.data.get('kind', '业务资料'),
              'status': row.status, 'warnings': row.data.get('warnings', []), 'error': row.data.get('error'),
              'engine': row.data.get('engine'), 'job_id': row.data.get('job_id'),
              'block_count': len(row.data.get('blocks', []))}
    from .material_ocr import material_summary
    result['ocr'] = material_summary(db, row)
    from .document_copy import checked_provenance
    result['document_copy'] = checked_provenance(db,row)
    if include_blocks:
        result['blocks'] = row.data.get('blocks', [])
    if row.data.get('resource_kind') == 'geospatial_raster':
        result['resource_kind'] = row.data['resource_kind']
        result['source_kind'] = row.data.get('source_kind')
        result['resource_coverage'] = row.data.get('resource_coverage')
        metadata = row.data.get('geospatial_metadata') or {}
        result['geospatial_summary'] = {key: metadata.get(key) for key in (
            'width', 'height', 'bands', 'crs', 'georeference_status',
            'observed_at', 'source_time_verified', 'analysis_performed')}
        if include_blocks:
            result['geospatial_metadata'] = metadata
    return result


def validate_configuration(value):
    if value.get('version') != 1 or not isinstance(value.get('resources'), list) or not isinstance(value.get('connections'), list):
        raise HTTPException(422, '配置格式不完整，原记录保留')
    if len(json.dumps(value, ensure_ascii=False).encode()) > 2 * 1024 * 1024:
        raise HTTPException(413, '配置超过2MB，请拆分或减少恢复历史')
    from .setup_business_candidates import validate_bundle, validate_unresolved
    if value.get('businessCandidates') is not None:
        validate_bundle(value['businessCandidates'])
    if not isinstance(value.get('instanceProfile', {}), dict) or not isinstance(value.get('flows', {}), dict):
        raise HTTPException(422, '领域或流程配置格式不完整')
    if any(not isinstance(value.get('instanceProfile',{}).get(k,''),str) for k in ['name','domain','goal']):
        raise HTTPException(422,'实例名称、领域和目标必须是文字')
    if any(not isinstance(row,dict) for row in [*value['connections'],*value['resources']]):
        raise HTTPException(422,'连接或资源记录格式不完整')
    businesses=value.get('businesses', [])
    if not isinstance(businesses, list) or any(not isinstance(b,str) and not (isinstance(b,dict) and isinstance(b.get('name'),str)) for b in businesses):
        raise HTTPException(422, '业务清单格式不完整')
    aliases=value.get('businessAliases',{})
    if not isinstance(aliases,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in aliases.items()):
        raise HTTPException(422,'业务名称关联格式不完整')
    instructions=value.get('automationInstructions',{})
    if not isinstance(instructions,dict) or any(not isinstance(k,str) or not isinstance(v,str) or len(v)>12000 for k,v in instructions.items()):
        raise HTTPException(422,'自动化修改要求格式不完整')
    for f in value.get('flows', {}).values():
        if not isinstance(f,dict) or not isinstance(f.get('steps',[]),list) or any(not isinstance(s,dict) for s in f.get('steps',[])):
            raise HTTPException(422, '流程步骤格式不完整')
        if any(not isinstance(f.get(k,''),str) for k in ['trigger','output']):raise HTTPException(422,'业务启动与产出必须是文字')
        if any(not isinstance(s.get(k,''),str) for s in f.get('steps',[]) for k in ['id','name','purpose','agent','role','output','sourceId','citation','condition','nextId','elseId']):
            raise HTTPException(422,'步骤名称、角色、产出和依据必须是文字')
        if not isinstance(f.get('inferenceReason',''),str):
            raise HTTPException(422,'业务依据说明必须是文字')
        for item in [f,*f.get('steps',[])]:
            validate_unresolved(item.get('candidateUnresolvedSources', []))
            if 'candidateKey' in item and (not isinstance(item['candidateKey'], str)
                    or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', item['candidateKey'])):
                raise HTTPException(422, '候选业务标识无法核对，原草稿保留')
            citations=item.get('citations',[])
            if not isinstance(citations,list) or len(citations)>50 or any(not isinstance(c,dict) or any(not isinstance(c.get(k),str) for k in ['material_id','block_id','quote']) for c in citations):
                raise HTTPException(422,'原文引用需包含资料、段落和引用文字')
    def visit(node):
        if isinstance(node, dict):
            for key, val in node.items():
                if key.lower() in {'secret', 'password', 'api_key', 'apikey', 'access_token', 'token'} and val:
                    raise HTTPException(422, '配置不可保存凭证，请使用凭证引用')
                visit(val)
        elif isinstance(node, list):
            for item in node:
                visit(item)
    visit(value)
    from .report_full_semantic_handoff import validate_configuration as validate_full_semantics
    try:
        validate_full_semantics(value)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise HTTPException(422, '完整业务设计或步骤映射无法核对，原配置保留') from None


@router.get('/draft')
def get_draft(p: P, db: DB):
    row = draft(db, p); db.commit()
    return draft_view(row)


class SaveDraft(Contract):
    revision: int = Field(ge=1)
    configuration: dict


@router.put('/draft')
def save_draft(body: SaveDraft, p: P, db: DB):
    require(p, 'admin', 'operator')
    submitted = copy.deepcopy(body.configuration)
    has_rename = '_businessRenameIntent' in submitted
    rename_intent = submitted.pop('_businessRenameIntent', None)
    if has_rename and rename_intent is None:
        raise HTTPException(409, '业务改名范围无法核对，原配置保留。')
    validate_configuration(submitted)
    row = draft(db, p, True); check_version(row, body.revision)
    from .setup_business_candidates import protect_transitions
    if submitted.get('businessCandidates') != row.data.get('businessCandidates'):
        raise HTTPException(409, '业务候选请使用专用操作更新，原草稿保留')
    protect_transitions(db, p, row.data, submitted)
    from .setup_automation import synchronize_reviews
    from .setup_automation_instruction_guard import protect_automation_instruction_transition
    protect_automation_instruction_transition(row.data,submitted,explicit_rename=rename_intent)
    proposed=synchronize_reviews(row.data,submitted)
    validate_voice_references(db,p,submitted)
    materials = proposed.get('materials', [])
    if not isinstance(materials, list):
        raise HTTPException(422, '资料清单格式不完整')
    changed_sources=set()
    for m in materials:
        if not isinstance(m, dict) or not m.get('id'):
            raise HTTPException(422, '资料记录不完整')
        material = scoped(db, SetupMaterial, m['id'], p, True)
        if m.get('hash') != material.sha256:
            raise HTTPException(409, '原件摘要不匹配，未保存配置')
        metadata = {'note': str(m.get('note', ''))[:5000], 'kind': str(m.get('kind', '业务资料'))[:100]}
        archived = bool(m.get('archived', False))
        if material.archived != archived or any(material.data.get(k, '' if k == 'note' else '业务资料') != v for k, v in metadata.items()):
            if type(m.get('version')) is not int or m['version'] != material.version:
                raise HTTPException(409, '资料信息已被修改，请重新读取后保存；原草稿保留')
            audit(db, p, 'setup.material.metadata', material.id, {'archived': archived})
            material.archived = archived; material.data = {**material.data, **metadata}; material.version += 1
            changed_sources.add(material.id)
        # Persist the version produced by this very save. Keeping the previous
        # value would make a later history preview report an already-reviewed
        # metadata edit as another unseen material change.
        m['version']=material.version
    from .flow_confirmation import synchronize_flow_confirmations
    proposed=synchronize_flow_confirmations(row.data,proposed,changed_sources=changed_sources)
    from .report_configuration_handoff import protect_configuration_sources
    proposed=protect_configuration_sources(db,p,row.data,proposed)
    validate_configuration(proposed)
    from .typed_payload_codec_v131 import canonical_json
    if canonical_json(row.data) == canonical_json(proposed):
        db.commit(); return draft_view(row)
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id, draft_id=row.id,
               version=row.version, data=row.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    row.data = proposed; row.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=row.id, version=row.version, data=row.data))
    audit(db, p, 'setup.save', row.id, {'revision': row.version})
    db.commit()
    return draft_view(row)


class AutomationReview(Contract):
    revision:int=Field(strict=True,ge=1)
    business_id:str=Field(pattern=r'^business_[a-f0-9]{16}$')
    request_id:UUID


def automation_review_record(db,p,identity,*,workspace_bound=True):
    from .models import Audit
    query=select(Audit).where(Audit.action=='setup.automation.review',Audit.data['request_id'].as_string()==identity)
    if workspace_bound:query=query.where(Audit.workspace_id==p.workspace_id)
    records=list(db.scalars(query.limit(2)))
    if len(records)>1:raise HTTPException(409,'原自动化确认收据异常，请保留记录核对')
    if not records:return None
    record=records[0]
    if digest({k:v for k,v in record.data.items() if k!='record_hash'})!=record.data.get('record_hash'):
        raise HTTPException(409,'原自动化确认收据无法核对，请保留记录')
    return record


def automation_review_reply(row,record,replayed):
    return {**draft_view(row),'review_receipt':{'request_id':record.data['request_id'],
        'business_id':record.data['body']['business_id'],'saved_revision':record.data['saved_revision'],'replayed':replayed}}


def automation_review_actor(db,p):
    # 当前确认人/成员/会话锁持至提交，撤权不能穿过最终确认提交窗口。
    from .models import User,Membership,AuthSession
    row=db.execute(select(User,Membership,AuthSession).join(Membership,Membership.user_id==User.id)
        .join(AuthSession,AuthSession.user_id==User.id).where(User.id==p.user_id,User.active.is_(True),
            Membership.workspace_id==p.workspace_id,Membership.role.in_(['admin','operator']),
            AuthSession.id==p.session_id,AuthSession.revoked.is_(False),AuthSession.expires_at>now())
        .with_for_update(read=True,of=[User,Membership,AuthSession]).execution_options(populate_existing=True)).first()
    if not row:raise HTTPException(403,'当前账号或工作区权限已变化，不能确认自动化范围')


@router.post('/automation/review')
def review_automation(body:AutomationReview,p:P,db:DB):
    require(p,'admin','operator')
    # 同一编号全局绑定当前工作区、身份和请求；事务锁防止跨工作区并发洗白。
    nonce_lock=int.from_bytes(hashlib.sha256(body.request_id.bytes).digest()[:8],'big',signed=True)
    db.execute(select(func.pg_advisory_xact_lock(nonce_lock)))
    row=draft(db,p,True);automation_review_actor(db,p)
    payload=body.model_dump(mode='json');signature=digest(payload)
    prior=automation_review_record(db,p,str(body.request_id),workspace_bound=False)
    if prior is not None:
        if prior.workspace_id!=p.workspace_id or prior.actor_id!=p.user_id or prior.object_id!=row.id or prior.data.get('body')!=payload or prior.data.get('body_hash')!=signature:
            raise HTTPException(409,'同一自动化确认编号不能更换身份、业务或请求内容')
        db.commit();return automation_review_reply(row,prior,True)
    if row.version!=body.revision:
        raise HTTPException(409,'配置版本已更新，本次自动化确认未提交。请读取最新配置后重新核对。')
    from .setup_automation import names,identifier,policy_for,metadata
    name=next((n for n in names(row.data) if identifier('business',n)==body.business_id),None)
    if name is None:raise HTTPException(404,'此业务不存在于当前配置，未确认自动化范围')
    policy=policy_for(row.data,name)
    if not isinstance(policy,dict) or not isinstance(policy.get('rules'),dict) or not policy['rules']:raise HTTPException(409,'请先配置此业务的自动化范围，空规则占位不能确认为执行授权')
    if any(not isinstance(r,dict) or r.get('mode') not in ['自动运行','执行前确认','仅给出建议'] for r in policy['rules'].values()):
        raise HTTPException(422,'自动化动作设置无效，原规则保留')
    triggers=policy.get('triggers',[])
    if not isinstance(triggers,list) or any(t not in ['人工发起','新观测到达','定时运行','异常事件'] for t in triggers):
        raise HTTPException(422,'启动条件无效，请回第4步核对')
    if '定时运行' in triggers and (not isinstance(policy.get('time'),str) or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',policy['time'])):
        raise HTTPException(422,'按计划运行时间无效，未确认自动化范围')
    value=copy.deepcopy(row.data);policy=copy.deepcopy(policy)
    policy['flow_review']=metadata(value,name,policy,'confirmed',p.user_id,now().isoformat())
    value['automation']['policies'][name]=policy
    db.execute(insert(SetupRevision).values(id=uid(),workspace_id=p.workspace_id,draft_id=row.id,
        version=row.version,data=row.data).on_conflict_do_nothing(index_elements=['draft_id','version']))
    row.data=value;row.version+=1
    db.add(SetupRevision(workspace_id=p.workspace_id,draft_id=row.id,version=row.version,data=value))
    details={'request_id':str(body.request_id),'body':payload,'body_hash':signature,'saved_revision':row.version,
        'flow_hash':policy['flow_review']['flow_hash'],'policy_hash':policy['flow_review']['policy_hash']}
    details['record_hash']=digest(details)
    from .models import Audit
    record=Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action='setup.automation.review',object_id=row.id,data=details)
    db.add(record);db.commit();return automation_review_reply(row,record,False)


@router.get('/automation/review/requests/{request_id}')
def probe_automation_review(request_id:UUID,p:P,db:DB,business_id:str=Query(pattern=r'^business_[a-f0-9]{16}$')):
    row=draft(db,p);record=automation_review_record(db,p,str(request_id))
    if record is None or record.actor_id!=p.user_id or record.object_id!=row.id or record.data.get('body',{}).get('business_id')!=business_id:
        raise HTTPException(404,'当前身份与业务范围未找到已提交的自动化确认')
    db.commit();return automation_review_reply(row,record,True)


def validate_voice_references(db,p,configuration):
    from .models import AudioRun
    from .local_audio import checked
    refs=configuration.get('voiceInputs',[])
    if not isinstance(refs,list) or len(refs)>100:raise HTTPException(422,'语音依据记录格式无效或超过100条')
    for ref in refs:
        if not isinstance(ref,dict) or not isinstance(ref.get('audio_run_id'),str) or not isinstance(ref.get('request_id'),str):raise HTTPException(422,'语音依据需要对应的音频和应用请求')
        audio=scoped(db,AudioRun,ref['audio_run_id'],p);checked(db,p,audio)
        receipt=audio.data.get('setup_applications',{}).get(ref['request_id'])
        if not receipt or receipt['reference']!=ref:raise HTTPException(409,'语音依据与已保存应用收据不一致，未修改配置')


class ApplyVoice(Contract):
    revision:int=Field(ge=1)
    audio_version:int=Field(ge=1)
    audio_run_id:UUID
    request_id:UUID
    text:str=Field(min_length=1,max_length=12000)
    target:Literal['goal','business_instruction','automation_instruction']='goal'
    business:str=Field(default='',max_length=250)
    mode:Literal['append','replace']='append'


@router.post('/voice/apply')
def apply_voice(body:ApplyVoice,p:P,db:DB):
    """Human approval saves the audio review and configuration in one CAS transaction."""
    from .contracts import digest
    from .models import AudioRun
    from .local_audio import checked
    from .audio_api import record_review
    from .setup_understanding import names
    require(p,'admin','operator');configuration=draft(db,p,True)
    audio=scoped(db,AudioRun,str(body.audio_run_id),p,True);checked(db,p,audio)
    applications=audio.data.get('setup_applications',{});request=str(body.request_id)
    signature=digest(body.model_dump(mode='json'))
    if request in applications:
        if applications[request]['request_hash']!=signature:raise HTTPException(409,'同一应用请求内容已变化，请先核对原收据')
        db.commit();return draft_view(configuration)
    check_version(configuration,body.revision);check_version(audio,body.audio_version)
    if audio.status!='succeeded' or audio.data.get('workflow_task_id'):raise HTTPException(409,'请选择真实转写完成的独立音频；业务核查结果不能代替配置输入')
    value=body.text.strip()
    if not value:raise HTTPException(422,'请先核对并填写要应用的文字')
    if body.target in {'business_instruction','automation_instruction'} and body.business not in names(configuration.data):raise HTTPException(422,'请选择当前配置中的业务，再保存修改要求')
    if len(applications)>=100 or len(configuration.data.get('voiceInputs',[]))>=100:raise HTTPException(409,'语音应用依据已达100条，请先整理当前配置')
    proposed=json.loads(json.dumps(configuration.data))
    target=proposed.setdefault('instanceProfile',{}) if body.target=='goal' else proposed.setdefault('automationInstructions' if body.target=='automation_instruction' else 'setupNotes',{})
    key='goal' if body.target=='goal' else body.business
    target[key]=('\n'.join([target.get(key,'').rstrip(),value]).strip() if body.mode=='append' else value)
    validate_configuration(proposed)
    record_review(db,p,audio,value)
    reference={'request_id':request,'audio_run_id':audio.id,'target':body.target,'business':body.business,'mode':body.mode,'text':value,'input_sha256':audio.data['input_sha256'],'result_hash':audio.data['result_hash'],'recipe_hash':audio.data['recipe_hash'],'review_hash':digest(audio.data['review']),'candidate_receipt_id':audio.data['review']['candidate_receipt_id'],'actor_id':p.user_id,'saved_at':audio.data['review']['saved_at'],'deployment':'local','cloud_calls':0}
    audio.data={**audio.data,'setup_applications':{**applications,request:{'request_hash':signature,'reference':reference}}}
    proposed['voiceInputs']=[*proposed.get('voiceInputs',[]),reference]
    audit(db,p,'setup.voice.apply',configuration.id,{'audio_run_id':audio.id,'target':body.target,'business':body.business,'cloud_calls':0,'confirmed_flow':False})
    return save_draft(SaveDraft(revision=body.revision,configuration=proposed),p,db)


@router.get('/voice/requests/{request_id}')
def voice_request(request_id:UUID,audio_run_id:UUID,p:P,db:DB):
    from .models import AudioRun
    from .local_audio import checked
    audio=scoped(db,AudioRun,str(audio_run_id),p);checked(db,p,audio)
    receipt=audio.data.get('setup_applications',{}).get(str(request_id))
    if not receipt:raise HTTPException(404,'该请求尚无已保存的语音应用收据；请核对当前配置后再决定是否重新应用')
    current=draft(db,p);db.commit()
    return {'receipt':receipt,'draft':draft_view(current)}


@router.get('/draft/history')
def history_draft(p: P, db: DB, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), before_revision: int | None = Query(None,ge=1)):
    query=select(SetupRevision).where(SetupRevision.workspace_id == p.workspace_id)
    if before_revision is not None:query=query.where(SetupRevision.version<before_revision)
    rows = db.scalars(query.order_by(SetupRevision.version.desc()).offset(offset).limit(limit)).all()
    return [{'revision': r.version, 'created_at': r.created_at.isoformat(), 'configuration': r.data} for r in rows]


@router.get('/materials')
def materials(p: P, db: DB, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    return [material_view(m, db) for m in db.scalars(select(SetupMaterial).where(
        SetupMaterial.workspace_id == p.workspace_id).order_by(SetupMaterial.created_at, SetupMaterial.id)
        .offset(offset).limit(limit))]


@router.post('/materials', status_code=201)
async def upload_material(p: P, db: DB, file: UploadFile = File()):
    require(p, 'admin', 'operator')
    name = (file.filename or '').replace('\\', '/').split('/')[-1]
    if not name or len(name) > 250 or any(ord(c) < 32 for c in name) or Path(name).suffix.lower() not in SUFFIXES:
        raise HTTPException(415, '文件名称或格式不支持')
    content = await file.read(settings().max_upload_bytes + 1)
    await file.close()
    if not content or len(content) > settings().max_upload_bytes:
        raise HTTPException(413, '文件为空或超过服务器大小上限')
    sha = hashlib.sha256(content).hexdigest()
    # Serialize duplicates per workspace without a database-wide lock.
    from sqlalchemy import text
    lock = int.from_bytes(hashlib.sha256((p.workspace_id + sha).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
    existing = db.scalar(select(SetupMaterial).where(SetupMaterial.workspace_id == p.workspace_id, SetupMaterial.sha256 == sha))
    if existing:
        return material_view(existing, db)
    key = p.workspace_id + '/' + sha + '.bin'
    path = settings().storage_dir / key
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            os.chmod(temporary, 0o600); out.write(content); out.flush(); os.fsync(out.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    ev = Evidence(workspace_id=p.workspace_id, sha256=sha, size_bytes=len(content), storage_key=key,
                  media_type=MEDIA[Path(name).suffix.lower()], data={'modality': 'document', 'cloud_model_ids': [], 'uploaded_by': p.user_id})
    db.add(ev); db.flush()
    row = SetupMaterial(workspace_id=p.workspace_id, name=name, sha256=sha, evidence_id=ev.id, data={})
    db.add(row); db.flush()
    queue_parse(db, p, row)
    audit(db, p, 'setup.material.upload', row.id, {'sha256': sha, 'bytes': len(content)})
    db.commit()
    return material_view(row, db)


def queue_parse(db, p, row):
    generation = row.data.get('parse_generation', 0) + 1
    from .material_ocr import supersede_for_native_parse
    supersede_for_native_parse(db,p,row,generation)
    job = Job(workspace_id=p.workspace_id, kind='parse_setup_material', idempotency_key=f'setup:{row.id}:{generation}',
              data={'actor_id': p.user_id, 'material_id': row.id, 'generation': generation})
    db.add(job); db.flush()
    row.status = 'queued'; row.version += 1
    pending = {'contract_version': 1, 'status': 'pending', 'unit_count': 0, 'gaps': [], 'reason': None}
    from .document_copy import checked_provenance
    provenance = checked_provenance(db,row)
    if provenance:
        pending = {**pending, 'contract_version': 2, 'profile': {'kind':'document_copy_full_pages_v1',
            'provenance_hash':provenance['integrity_hash']}, 'force_all_pages':True}
    row.data = {**row.data, 'job_id': job.id, 'parse_generation': generation, 'error': None,
                'ocr_coverage': pending}


@router.get('/materials/{object_id}')
def get_material(object_id: str, p: P, db: DB):
    return material_view(scoped(db, SetupMaterial, object_id, p), db, True)


@router.get('/materials/{object_id}/content')
def content_material(object_id: str, p: P, db: DB):
    from urllib.parse import quote
    row = scoped(db, SetupMaterial, object_id, p)
    ev, content = evidence_bytes(db, p, row.evidence_id)
    return Response(content, media_type=ev.media_type,
                    headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(row.name),
                             'X-Content-SHA256': row.sha256, 'X-Content-Type-Options': 'nosniff'})


@router.get('/materials/{object_id}/docx-structure')
def material_docx_structure(object_id: str, p: P, db: DB, include_candidates: bool = False):
    """Inspect the immutable original without changing prior parsing or review."""
    from .document_parsers import ParseProblem
    from .docx_structure_audit import audit_docx_structure
    row = scoped(db, SetupMaterial, object_id, p)
    if Path(row.name).suffix.lower() != '.docx':
        raise HTTPException(415, '此项结构核对仅支持 Word DOCX 原件')
    ev, content = evidence_bytes(db, p, row.evidence_id)
    if ev.sha256 != row.sha256:
        raise HTTPException(409, '资料原件身份不一致，请先恢复原文件')
    try:
        result = audit_docx_structure(content, include_candidates=include_candidates)
    except ParseProblem as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={'Cache-Control': 'private, no-store',
                                        'X-Content-Type-Options': 'nosniff'})


class MaterialAction(Contract):
    version: int = Field(ge=1)


@router.post('/materials/{object_id}/retry')
def retry_material(object_id: str, body: MaterialAction, p: P, db: DB):
    require(p, 'admin', 'operator')
    row = scoped(db, SetupMaterial, object_id, p, True); check_version(row, body.version)
    if row.status not in {'failed', 'needs_input', 'cancelled'}:
        raise HTTPException(409, '当前资料不需要重试')
    queue_parse(db, p, row); db.commit()
    return material_view(row, db)


@router.post('/materials/{object_id}/cancel')
def cancel_material(object_id: str, body: MaterialAction, p: P, db: DB):
    require(p, 'admin', 'operator')
    row = scoped(db, SetupMaterial, object_id, p, True); check_version(row, body.version)
    if row.status not in {'queued', 'parsing'}:
        raise HTTPException(409, '当前解析已结束')
    row.status = 'cancelled'; row.version += 1
    audit(db, p, 'setup.material.cancel', row.id)
    db.commit(); return material_view(row, db)


def process_material(db, p, job):
    from .document_parsers import parse_document, ParseProblem
    from .leases import LeaseLost, ExecutionUnauthorized
    row = scoped(db, SetupMaterial, job.data['material_id'], p, True)
    if row.data.get('parse_generation') != job.data['generation'] or row.status in {'parsed', 'resource_ready', 'cancelled'}:
        return
    row.status = 'parsing'; row.version += 1
    db.commit()
    geotiff = Path(row.name).suffix.lower() in {'.tif', '.tiff'}
    try:
        if geotiff:
            from .material_geotiff_v175 import parse_material
            result = parse_material(db, p, row, job)
        else:
            _, content = evidence_bytes(db, p, row.evidence_id)
            result = parse_document(content, row.name)
            from .document_copy import material_coverage
            result['ocr_coverage'] = material_coverage(db, row, content)
            if result['ocr_coverage']['status'] in {'needs_ocr', 'blocked'}:
                result['status'] = 'needs_input'
    except (LeaseLost, ExecutionUnauthorized):
        raise
    except Exception as exc:
        result = {'status': 'failed', 'blocks': [], 'engine': 'geotiff_metadata_v175' if geotiff else 'native-v1',
                  'error': str(exc) if isinstance(exc, ParseProblem) else '原件读取或解析失败，请核查后重试'}
    row = scoped(db, SetupMaterial, job.data['material_id'], p, True)
    if row.data.get('parse_generation') != job.data['generation'] or row.status == 'cancelled':
        return
    row.status = result['status']; row.version += 1
    row.data = {**row.data, **result, 'finished_at': now().isoformat()}
    audit(db, p, 'setup.material.parsed', row.id, {'status': row.status, 'blocks': len(result['blocks']), 'engine': result['engine']})


# OCR API uses the same setup/session scope; the module does not alter paid
# understanding schemas, cloud grants, or already saved analyses.
from .material_ocr_api import router as material_ocr_router
router.include_router(material_ocr_router)

from .document_copy_api import router as document_copy_router
router.include_router(document_copy_router)

# Private, unsent preparation is separate from shared business configuration.
from .understanding_preparation import router as understanding_preparation_router
router.include_router(understanding_preparation_router)

# Private per-question drafts and explicit follow-up snapshots.
from .setup_clarifications import router as setup_clarifications_router
router.include_router(setup_clarifications_router)

# Current-session revocation intentionally leaves browser cookies untouched.
from .session_revoke_api import router as session_revoke_router
router.include_router(session_revoke_router)

# Private report-body candidates never authorize configuration application.
from .report_reading_drafts import router as report_reading_drafts_router
router.include_router(report_reading_drafts_router)

# Full-source private candidates have independent requests and typed semantics.
from .report_full_reading_pipeline import router as report_full_reading_router
router.include_router(report_full_reading_router)

from .report_evidence_pipeline import router as report_evidence_reading_router
router.include_router(report_evidence_reading_router)
from .report_evidence_delegated_origin import router as report_evidence_delegated_origin_router
router.include_router(report_evidence_delegated_origin_router)
from .report_evidence_delegated_budget_start import router as report_evidence_delegated_budget_router
router.include_router(report_evidence_delegated_budget_router)
from .report_evidence_scoped_activation import router as report_evidence_scoped_activation_router
router.include_router(report_evidence_scoped_activation_router)
from .report_evidence_delegated_reduce_start import router as report_evidence_delegated_reduce_router
router.include_router(report_evidence_delegated_reduce_router)
from .report_evidence_delegated_capture_view import router as report_evidence_delegated_capture_router
router.include_router(report_evidence_delegated_capture_router)
from .report_evidence_recovery_diagnostics import router as report_evidence_recovery_diagnostics_router
router.include_router(report_evidence_recovery_diagnostics_router)

# Explicit local review preserves the paid original and never dispatches a model.
from .report_draft_partial_review import router as report_draft_partial_review_router
router.include_router(report_draft_partial_review_router)

from .report_configuration_handoff import router as report_configuration_handoff_router
router.include_router(report_configuration_handoff_router)
from .report_full_semantic_handoff import router as report_full_semantic_handoff_router
router.include_router(report_full_semantic_handoff_router)
from .report_full_semantic_replacement import router as report_full_semantic_replacement_router
router.include_router(report_full_semantic_replacement_router)
from .report_full_semantic_source_review import router as report_full_semantic_source_review_router
router.include_router(report_full_semantic_source_review_router)
from .report_full_semantic_region_source import router as report_full_semantic_region_source_router
router.include_router(report_full_semantic_region_source_router)
from .report_full_semantic_region_mapping import router as report_full_semantic_region_mapping_router
router.include_router(report_full_semantic_region_mapping_router)

# Private, recoverable quote selections grant no source approval.
from .report_source_region_binding import router as report_source_region_binding_router
router.include_router(report_source_region_binding_router)

from .report_source_requirements_api import router as source_requirement_review_router
router.include_router(source_requirement_review_router)

from .report_full_semantic_schema_adoption import router as report_full_semantic_schema_router
router.include_router(report_full_semantic_schema_router)
from .report_full_semantic_runtime_adoption import router as report_full_semantic_runtime_router
router.include_router(report_full_semantic_runtime_router)
from .report_full_semantic_runtime_revision import router as full_semantic_runtime_revision_router
router.include_router(full_semantic_runtime_revision_router)
from .report_full_semantic_graph_adoption import router as full_semantic_graph_router
router.include_router(full_semantic_graph_router)
from .report_full_semantic_graph_revision import router as full_semantic_graph_revision_router
router.include_router(full_semantic_graph_revision_router)
from .report_full_semantic_graph_editor import router as full_semantic_graph_editor_router
router.include_router(full_semantic_graph_editor_router)
from .report_full_semantic_graph_wire import router as full_semantic_graph_wire_router
router.include_router(full_semantic_graph_wire_router)
from .report_full_semantic_graph_adoption_v4 import router as full_semantic_graph_v4_router
router.include_router(full_semantic_graph_v4_router)
from .graph_event_api import router as graph_event_producer_router
router.include_router(graph_event_producer_router)
from .graph_event_catalog_v122 import router as graph_event_catalog_router
from .graph_event_revocation_v122 import router as graph_event_revocation_router
router.include_router(graph_event_catalog_router)
router.include_router(graph_event_revocation_router)
from .graph_event_request_recovery_v123 import router as graph_event_request_recovery_router
router.include_router(graph_event_request_recovery_router)
from .graph_event_delivery_recovery_v124 import router as graph_event_delivery_recovery_router
router.include_router(graph_event_delivery_recovery_router)
from .graph_event_delivery_retirement_v125 import router as graph_event_delivery_retirement_router
router.include_router(graph_event_delivery_retirement_router)

# Existing routes already carry /api/setup; the new router has its canonical
# absolute prefix. Aggregate once without duplicating either public path.
# These modules register on the original prefixed router. Register before
# exporting the composite so late imports cannot expose unprefixed paths.
from . import setup_understanding, setup_recovery
from .report_scoped_design_api_v181 import router as scoped_design_plan_router
router.include_router(scoped_design_plan_router)
setup_routes = router
router = APIRouter()
router.include_router(setup_routes)
from .report_saved_map_design_consumer_v176 import router as saved_map_design_router
router.include_router(saved_map_design_router)
from .report_saved_map_review_source_v178 import router as saved_map_review_source_router
router.include_router(saved_map_review_source_router)
from .report_scoped_design_consumer_v181 import router as scoped_design_attempt_router
router.include_router(scoped_design_attempt_router)
from .business_flow_review_api import router as business_flow_review_router
router.include_router(business_flow_review_router)
from .business_flow_qualification_api import router as business_flow_qualification_router
router.include_router(business_flow_qualification_router)
from .business_flow_execution_api import router as business_flow_execution_router
router.include_router(business_flow_execution_router)

from .authored_workflow_api_v208 import router as authored_workflow_router
router.include_router(authored_workflow_router)
from .report_business_catalogue_api_v211 import router as business_catalogue_router
router.include_router(business_catalogue_router)
from .report_business_catalogue_allocation_api_v213 import router as business_catalogue_allocation_router
router.include_router(business_catalogue_allocation_router)
from .report_business_catalogue_allocation_api_v213 import consumer_router as business_catalogue_allocation_consumer_router
router.include_router(business_catalogue_allocation_consumer_router)
from .report_business_catalogue_allocation_end_v213 import router as business_catalogue_allocation_end_router
router.include_router(business_catalogue_allocation_end_router)
from .instance_observation_day_v216 import router as instance_observation_day_router
router.include_router(instance_observation_day_router)
from .workflow_quality_artifact_api_v220 import router as workflow_quality_artifact_router
router.include_router(workflow_quality_artifact_router)
from .media_daily_task_api import router as media_daily_task_router
router.include_router(media_daily_task_router)
from .daily_frame_assessment_api_v233 import router as daily_frame_assessment_router
router.include_router(daily_frame_assessment_router)
from .frame_supplement_api_v236 import router as frame_supplement_router
router.include_router(frame_supplement_router)
from .source_query_api import router as source_query_router
router.include_router(source_query_router)
from .document_question_api import router as document_question_router
router.include_router(document_question_router)
from .business_automation_api import router as business_automation_router
router.include_router(business_automation_router)
from .authored_image_workflow_api_v243 import router as authored_image_workflow_router
router.include_router(authored_image_workflow_router)
from .authored_service_workflow_api_v270 import router as authored_service_workflow_router
router.include_router(authored_service_workflow_router)
from .authored_decision_workflow_api_v278 import router as authored_decision_workflow_router
router.include_router(authored_decision_workflow_router)
from .catalogue_current_context_api_v260 import router as catalogue_current_context_router
router.include_router(catalogue_current_context_router)

# The independent Goal-data family retains its own command and recovery path.
from .authored_goal_data_workflow_api_v288 import router as goal_data_workflow_router_v288
router.include_router(goal_data_workflow_router_v288)
