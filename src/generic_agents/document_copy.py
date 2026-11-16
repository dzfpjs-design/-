"""Durable local Word-copy intent. Never changes originals or invokes cloud models."""
import copy
import hashlib
from pathlib import PurePath
from fastapi import HTTPException
from sqlalchemy import select, text
from .contracts import digest
from .models import Job, SetupMaterial, Membership, User, now, uid
from .services import scoped, audit, check_version
from .security import require
from .evidence import evidence_bytes, save_immutable

KIND = 'prepare_document_copy'
POLICY = 'accepted_final_v1'
STATES = {'queued', 'converting', 'succeeded', 'failed', 'cancelled', 'response_unknown'}
ACTIVE = {'queued', 'converting'}
START_FENCE = ACTIVE | {'response_unknown'}


def fail(reason='本地副本请求或来源完整性无法核对，原记录保留', status=409):
    raise HTTPException(status, reason)


def engine_status():
    try:
        from .document_copy_runtime import engine_status as status
        return status()
    except ImportError:
        return {'available': False, 'engine_id': 'libreoffice-docx-final-v1', 'manifest_hash': None,
                'revision_policy': POLICY, 'reason': '本地文档转换运行环境尚未安装', 'limitations': []}


def current_actor(db, p):
    require(p, 'admin', 'operator')
    actual = db.execute(select(User.active, Membership.role).join(Membership, Membership.user_id == User.id)
        .where(User.id == p.user_id, Membership.workspace_id == p.workspace_id)).first()
    if not actual or not actual.active or actual.role != p.role or actual.role not in {'admin', 'operator'}:
        fail('当前资料管理权限已撤回或变化，请重新登录核对', 403)


def key(material_id, request_id):
    return f'document-copy:{material_id}:{request_id}'


FROZEN = ('actor_id', 'actor_role', 'source_material_id', 'source_evidence_id', 'source_name', 'source_sha256', 'source_material_version', 'engine_id', 'engine_manifest_hash', 'request_body_hash', 'requested_at')


def scope_hash(data): return digest({k:data.get(k) for k in FROZEN})


def checked(job):
    data = job.data
    body = data.get('request_body')
    if (data.get('request_scope_hash') != scope_hash(data) or job.kind != KIND or not isinstance(body, dict) or data.get('request_body_hash') != digest(body)
        or job.idempotency_key != key(data.get('source_material_id'), body.get('request_id'))
        or data.get('copy_status') not in STATES or type(data.get('request_version')) is not int
        or data['request_version'] < 1 or body.get('revision_policy') != POLICY
        or data.get('source_sha256') != body.get('source_sha256')
        or data.get('source_material_version') != body.get('expected_material_version')
        or data.get('engine_manifest_hash') != body.get('engine_manifest_hash')):
        fail()
    return job


def find(db, p, material_id, request_id, *, lock=False, missing=True):
    query = select(Job).where(Job.workspace_id == p.workspace_id,
        Job.idempotency_key == key(material_id, request_id), Job.kind == KIND)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if not row and missing:
        fail('尚未查到这个固定请求；请保留原请求编号和内容后再核对', 404)
    return checked(row) if row else None


def view(job):
    checked(job); d = job.data
    return {'request_id': d['request_body']['request_id'], 'request_body': copy.deepcopy(d['request_body']),
        'request_body_hash': d['request_body_hash'], 'actor_id': d['actor_id'], 'job_id': job.id, 'status': d['copy_status'],
        'version': d['request_version'], 'source_material_id': d['source_material_id'],
        'source_sha256': d['source_sha256'], 'source_material_version': d['source_material_version'],
        'revision_policy': POLICY, 'engine_id': d['engine_id'], 'engine_manifest_hash': d['engine_manifest_hash'],
        'output_material_id': d.get('output_material_id'), 'progress': d.get('progress'),
        'error': d.get('copy_error'), 'receipt': copy.deepcopy(d.get('receipt')),
        'cancellation': copy.deepcopy(d.get('cancellation')), 'created_at': d['requested_at']}


def eligible(material):
    if material.archived:
        return False, '原资料已归档'
    if PurePath(material.name).suffix.lower() != '.docx':
        return False, '本地完整副本目前支持 Word（DOCX）资料'
    if material.status in {'queued', 'parsing', 'uploaded'}:
        return False, '请等待原资料解析结束后再生成副本'
    return True, None


def overview(db, p, material):
    rows = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == KIND,
        Job.data['source_material_id'].as_string() == material.id).order_by(Job.created_at.desc(), Job.id.desc()).limit(100)))
    allowed, reason = eligible(material)
    if p.role not in {'admin', 'operator'}:
        allowed, reason = False, '当前账号只可查看'
    return {'material_id': material.id, 'material_version': material.version, 'source_sha256': material.sha256,
        'eligible': allowed, 'reason': reason, 'engine': engine_status(), 'items': [view(row) for row in rows]}


def start(db, p, material_id, value):
    def reject(reason, code='document_copy_start_rejected'):
        raise HTTPException(409, {'code':code, 'request_id':value['request_id'],
            'request_body_hash':digest(value), 'applied':False, 'message':reason})
    current_actor(db, p)
    material = scoped(db, SetupMaterial, material_id, p, True)
    old = find(db, p, material.id, value['request_id'], lock=False, missing=False)
    if old:
        if old.data['actor_id'] != p.user_id or old.data['request_body_hash'] != digest(value):
            reject('此请求编号已用于其他操作人或内容，原请求保留')
        return view(old)
    if material.version != value['expected_material_version']:
        reject('原资料已更新，本次副本请求未提交；请核对最新资料', 'document_copy_start_version_conflict')
    allowed, reason = eligible(material)
    if not allowed: reject(reason)
    if value['revision_policy'] != POLICY or value['source_sha256'] != material.sha256:
        reject('原文件摘要或修订策略已变化，请重新核对')
    existing = db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id, Job.kind == KIND,
        Job.data['source_material_id'].as_string() == material.id,
        Job.data['copy_status'].as_string().in_(list(START_FENCE))))
    if existing: reject('此原资料已有正在进行的本地副本请求，请恢复该请求')
    runtime = engine_status()
    if (not runtime.get('available') or runtime.get('manifest_hash') != value['engine_manifest_hash']
        or runtime.get('revision_policy') != POLICY or runtime.get('engine_id') != 'libreoffice-docx-final-v1'):
        reject(runtime.get('reason') or '本地转换运行包版本变化，请刷新后重新核对')
    ev, content = evidence_bytes(db, p, material.evidence_id)
    if ev.sha256 != material.sha256 or hashlib.sha256(content).hexdigest() != material.sha256: fail()
    data = {'actor_id': p.user_id, 'actor_role': p.role, 'source_material_id': material.id,
        'source_evidence_id': material.evidence_id, 'source_name': material.name,
        'source_sha256': material.sha256, 'source_material_version': material.version,
        'engine_id': runtime['engine_id'], 'engine_manifest_hash': runtime['manifest_hash'],
        'request_body': copy.deepcopy(value), 'request_body_hash': digest(value),
        'copy_status': 'queued', 'request_version': 1, 'requested_at': now().isoformat(),
        'progress': '等待在本地生成 PDF 副本；原文件和已有核对结果保留', 'copy_error': None}
    data['request_scope_hash'] = scope_hash(data)
    job = Job(workspace_id=p.workspace_id, kind=KIND,
        idempotency_key=key(material.id, value['request_id']), data=data)
    db.add(job); db.flush()
    audit(db, p, 'setup.document_copy.requested', job.id, {'source_material_id': material.id,
        'source_sha256': material.sha256, 'revision_policy': POLICY, 'request_body_hash': digest(value)})
    return view(job)


def cancel(db, p, material_id, request_id, value):
    def reject(reason, code='document_copy_cancel_rejected'):
        raise HTTPException(409, {'code':code, 'request_id':request_id,
            'cancel_body_hash':digest(value), 'applied':False, 'message':reason})
    current_actor(db, p)
    scoped(db, SetupMaterial, material_id, p)
    job = find(db, p, material_id, request_id, lock=True)
    old = job.data.get('cancellation')
    if old:
        if old.get('body') != value or old.get('actor_id') != p.user_id:
            reject('此任务已有其他取消回执，本次取消内容未提交；原回执保留')
        return view(job)
    if value['expected_request_version'] != job.data['request_version']:
        reject('副本任务已更新，本次取消未提交；请查回原请求后重新核对', 'document_copy_cancel_version_conflict')
    if job.data['copy_status'] not in START_FENCE:
        reject('此副本任务已结束，本次取消未提交；原件、结果和回执保留')
    was_unknown = job.data['copy_status'] == 'response_unknown'
    job.data = {**job.data, 'copy_status': 'cancelled', 'request_version': job.data['request_version'] + 1,
        'progress': '已明确放弃这次未确定的转换；旧进程的任何未确定输出均不再发布，原文件和请求保留' if was_unknown else '已取消本地副本生成；原文件保留',
        'cancellation': {'body': copy.deepcopy(value), 'actor_id': p.user_id, 'applied_at': now().isoformat()}}
    if job.status == 'queued': job.status = 'cancelled'
    audit(db, p, 'setup.document_copy.cancelled', job.id)
    return view(job)


def _source(db, p, job, *, lock=False):
    checked(job)
    material = scoped(db, SetupMaterial, job.data['source_material_id'], p, lock)
    allowed, reason = eligible(material)
    if not allowed: fail(reason)
    if (material.version != job.data['source_material_version'] or material.sha256 != job.data['source_sha256']
        or material.evidence_id != job.data['source_evidence_id'] or material.name != job.data['source_name']):
        fail('转换期间原资料已修改或重新解析，未发布副本，请核对原请求')
    return material


def _transition(job, status, progress, error=None):
    job.data = {**job.data, 'copy_status': status, 'request_version': job.data['request_version'] + 1,
        'progress': progress, 'copy_error': error}


def fail_job(db, p, job, reason):
    checked(job)
    if job.data['copy_status'] in ACTIVE:
        state = 'response_unknown' if job.data.get('conversion_started_at') else 'failed'
        _transition(job, state, '本地副本未发布；原件和请求保留，请查回原请求后核对', reason)


def process(db, p, job):
    """One renderer dispatch per fixed intent; uncertain interruption is never replayed."""
    from .leases import checkpoint
    from .document_copy_runtime import convert
    job = scoped(db, Job, job.id, p, True); checked(job)
    if job.data['copy_status'] not in ACTIVE: return
    if job.data.get('conversion_started_at'):
        fail_job(db, p, job, '上次本地转换已开始但未保存结果，禁止自动重跑；请保留并核对该请求')
        return
    current_actor(db, p)
    if p.user_id != job.data['actor_id'] or p.role != job.data['actor_role']: fail('原操作人的授权已变化', 403)
    source = _source(db, p, job, lock=True)
    ev, content = evidence_bytes(db, p, source.evidence_id)
    if ev.sha256 != source.sha256 or hashlib.sha256(content).hexdigest() != source.sha256: fail()
    _transition(job, 'converting', '正在本地生成 PDF 副本；完成后仍需逐页核对')
    job.data = {**job.data, 'conversion_started_at': now().isoformat()}
    db.commit()
    def guard():
        current_actor(db, p)
        current = scoped(db, Job, job.id, p, True); checked(current)
        if current.data['copy_status'] != 'converting': fail('本地副本请求已取消或停止')
        _source(db, p, current, lock=True)
        checkpoint(db)
    guard()
    try:
        result = convert(content, revision_policy=POLICY,
            expected_manifest_hash=job.data['engine_manifest_hash'], guard=guard)
        guard()
        pdf = result['content']; receipt = result['receipt']; sha = hashlib.sha256(pdf).hexdigest()
        if (not pdf.startswith(b'%PDF-') or receipt.get('source_sha256') != job.data['source_sha256']
            or receipt.get('pdf_sha256') != sha or receipt.get('engine_id') != job.data['engine_id']
            or receipt.get('manifest_hash') != job.data['engine_manifest_hash'] or receipt.get('revision_policy') != POLICY): fail()
        from .config import settings
        if not pdf or len(pdf) > settings().max_upload_bytes: fail('PDF 副本超过本地资料大小上限，未发布')
        job = scoped(db, Job, job.id, p, True); checked(job)
        source = _source(db, p, job, lock=True)
        if job.data['copy_status'] != 'converting': return
        current_actor(db, p)
        lock = int.from_bytes(hashlib.sha256((p.workspace_id + sha).encode()).digest()[:8], 'big', signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
        if db.scalar(select(SetupMaterial.id).where(SetupMaterial.workspace_id == p.workspace_id, SetupMaterial.sha256 == sha)):
            fail('相同 PDF 内容已存在于资料库，未覆盖或改变既有资料；请核对已有文件与本次回执')
        from .document_parsers import parse_document
        parsed = parse_document(pdf, 'copy.pdf')
        if parsed['status'] == 'failed': fail('本地 PDF 副本无法解析，未发布')
        copy_id = uid()
        copy_name = PurePath(source.name).stem[:210] + '（本地最终修订副本）.pdf'
        ev = save_immutable(db, p, pdf, 'application/pdf', {'modality': 'document', 'document_copy_job_id': job.id})
        provenance = {'contract_version': 1, 'copy_material_id': copy_id, 'workspace_id': p.workspace_id,
            'copy_evidence_id': ev.id, 'copy_name': copy_name, 'source_material_id': source.id,
            'source_sha256': source.sha256, 'source_material_version': source.version, 'source_name': source.name,
            'source_evidence_id': source.evidence_id, 'request_id': job.data['request_body']['request_id'],
            'job_id': job.id, 'request_body_hash': job.data['request_body_hash'], 'revision_policy': POLICY,
            'engine_id': job.data['engine_id'], 'engine_manifest_hash': job.data['engine_manifest_hash'],
            'pdf_sha256': sha, 'receipt': copy.deepcopy(receipt)}
        provenance = {**provenance, 'integrity_hash': digest(provenance)}
        row = SetupMaterial(id=copy_id, workspace_id=p.workspace_id, name=copy_name, sha256=sha,
            evidence_id=ev.id, status='needs_input', data={**parsed, 'document_copy': provenance,
                'warnings': list(dict.fromkeys([*parsed.get('warnings', []), *receipt.get('warnings', []),
                    '此文件为本地呈现的最终修订 PDF 副本；须核对全部页面，不能据此断言完整保留 Word 的所有语义。']))})
        db.add(row); db.flush()
        from .material_ocr_coverage import coverage
        cov = coverage(pdf, row.name, profile={'kind': 'document_copy_full_pages_v1', 'provenance_hash': provenance['integrity_hash']})
        if cov['gaps'] or receipt.get('page_count') != cov['unit_count']: fail('PDF 副本页数或呈现覆盖未能核对，未发布')
        row.data = {**row.data, 'ocr_coverage': cov, 'status': 'needs_input'}
        _transition(job, 'succeeded', '本地 PDF 副本已保存，请打开副本并核对全部页面')
        job.data = {**job.data, 'output_material_id': row.id, 'receipt': copy.deepcopy(receipt),
            'provenance': copy.deepcopy(provenance), 'published_at': now().isoformat()}
        audit(db, p, 'setup.document_copy.published', job.id, {'source_material_id': source.id,
            'output_material_id': row.id, 'source_sha256': source.sha256, 'pdf_sha256': sha,
            'provenance_hash': provenance['integrity_hash'], 'page_count': cov['unit_count']})
    except Exception as exc:
        from .leases import LeaseLost, ExecutionUnauthorized
        if isinstance(exc, (LeaseLost, ExecutionUnauthorized)): raise
        db.rollback()
        current = scoped(db, Job, job.id, p, True); checked(current)
        if current.data['copy_status'] == 'cancelled': return
        _transition(current, 'failed', '本地副本未发布，原件和请求保留',
            exc.detail if isinstance(exc, HTTPException) else str(exc)[:1000] if isinstance(exc, ValueError) else '本地转换未完成，请核对运行环境与原文件')


def checked_provenance(db, material):
    """Require both independent server records, including when a flag was stripped."""
    rows = list(db.scalars(select(Job).where(Job.workspace_id == material.workspace_id, Job.kind == KIND,
        Job.data['output_material_id'].as_string() == material.id)))
    value = material.data.get('document_copy')
    if not rows and value is None: return None
    if len(rows) != 1 or not isinstance(value, dict): fail()
    job = checked(rows[0]); raw = {k:v for k,v in value.items() if k != 'integrity_hash'}
    if (value.get('integrity_hash') != digest(raw) or job.data.get('copy_status') != 'succeeded'
        or job.data.get('provenance') != value or job.data.get('receipt') != value.get('receipt')
        or value.get('copy_material_id') != material.id or value.get('workspace_id') != material.workspace_id
        or value.get('copy_evidence_id') != material.evidence_id or value.get('copy_name') != material.name
        or value.get('pdf_sha256') != material.sha256 or PurePath(material.name).suffix.lower() != '.pdf'):
        fail()
    return copy.deepcopy(value)


def material_coverage(db, material, content):
    from .material_ocr_coverage import coverage
    provenance = checked_provenance(db, material)
    profile = {'kind': 'document_copy_full_pages_v1', 'provenance_hash': provenance['integrity_hash']} if provenance else None
    return coverage(content, material.name, profile=profile)
