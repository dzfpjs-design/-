"""Instance-bound original documents, retained parse attempts and literal search.

Existing Job -> User -> Membership -> Instance -> Document -> Version is the
mutation lock order. Native parsing runs after commit, without held row locks.
Historical proof checks never borrow current execution authority.
"""
import copy
import hashlib
from contextlib import contextmanager
from pathlib import PurePath
from urllib.parse import urlencode
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import models as m
from .contracts import digest
from .evidence import evidence_bytes, save_immutable
from .security import Principal
from .services import audit, scoped, check_version
from .runtime_document_models import RuntimeDocument as Document, RuntimeDocumentVersion as Version
from .runtime_document_models import RuntimeDocumentParseAttempt as Attempt, RuntimeDocumentSearch as Search

COMMAND = 'runtime_document.command_v205'
UPLOAD = 'runtime_document.uploaded_v205'
START = 'runtime_document.attempt_started_v205'
RESULT = 'runtime_document.attempt_result_v205'
AUTH = 'runtime_document.job_authorized_v205'
SEARCH_ORIGIN = 'runtime_document.search_created_v205'
SEARCH_RESULT = 'runtime_document.search_result_v205'
MAX_BYTES = 20 * 1024 * 1024
DOCUMENT_LIMIT = HIT_LIMIT = 100
SCAN_BLOCK_LIMIT = 2000
SCAN_CHARACTER_LIMIT = 2000000
HIT_TEXT_LIMIT = 1200
MEDIA = {'.txt': 'text/plain', '.md': 'text/markdown', '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
TERMINAL = {'parsed', 'needs_input', 'failed', 'cancelled'}


class ParseCancelled(Exception):
    """Server-owned cancellation, never a parser/file-format error."""


def parse_guard(p, job_id, token, version_id, release_id, configuration_hash):
    """Short read-only session; no worker lease attachment or held row locks."""
    from .db import engine
    from .leases import LeaseLost, ExecutionUnauthorized
    with Session(engine()) as check:
        check.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        valid = check.scalar(select(m.Job.id).where(m.Job.id == job_id, m.Job.workspace_id == p.workspace_id,
            m.Job.status == 'running', m.Job.lease_token == token, m.Job.lease_until > func.clock_timestamp(),
            m.Job.kind == 'parse_runtime_document', m.Job.data['runtime_document_id'].as_string() == version_id,
            m.Job.data['actor_id'].as_string() == p.user_id))
        if not valid: raise LeaseLost('原文档处理租约已变化，旧执行结果未写入')
        version = scoped(check, Version, version_id, p)
        if version.status == 'cancelled': raise ParseCancelled('此原文档解析已取消')
        user = check.get(m.User, p.user_id)
        member = check.scalar(select(m.Membership).where(m.Membership.user_id == p.user_id,
            m.Membership.workspace_id == p.workspace_id))
        instance = scoped(check, m.DomainInstance, version.instance_id, p)
        release = scoped(check, m.InstanceRelease, release_id, p)
        package = scoped(check, m.Capability, release.package_id, p)
        if (not user or not user.active or not member or member.role not in {'admin', 'operator'} or member.role != p.role
                or instance.status != 'partially_active' or instance.active_release_id != release_id
                or version.release_id != release_id or release.instance_id != instance.id
                or release.configuration_hash != configuration_hash or digest(release.data['snapshot']) != configuration_hash
                or package.status != 'published' or package.kind != 'domain_package'
                or package.content_hash != package.validated_hash or digest(package.data) != package.content_hash):
            raise ExecutionUnauthorized('当前执行批准、实例或原发布版本已变化，原文档保留且未迁移批准')


def need(value, message='原运行文档、固定引用或独立来源依据无法核对'):
    if not value: raise HTTPException(409, message)


def same(a, b): return digest(a) == digest(b)


def anchored(db, p, action, identity, value):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity)))
    need(len(rows) == 1 and same(rows[0].data, value)); return rows[0]


def current_actor(db, p):
    user = db.scalar(select(m.User).where(m.User.id == p.user_id).with_for_update().execution_options(populate_existing=True))
    member = db.scalar(select(m.Membership).where(m.Membership.user_id == p.user_id,
        m.Membership.workspace_id == p.workspace_id).with_for_update().execution_options(populate_existing=True))
    need(user is not None and user.active and member is not None and member.role in {'admin', 'operator'}, '当前账户或工作区执行批准已撤销')


def active(db, p, instance_id, release_id):
    from .instance_api import active as current
    instance, release = current(db, p, instance_id, True)
    need(release.id == release_id, '实例版本已变化，请核对当前实例后重新提交')
    return instance, release


def context(db, p, identity):
    instance = scoped(db, m.DomainInstance, identity, p)
    allowed, reason = False, '此实例未启用或当前账户没有上传与查找批准'
    try:
        from .instance_api import active as current
        current(db, p, identity)
        user = db.get(m.User, p.user_id)
        member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == p.workspace_id, m.Membership.user_id == p.user_id))
        allowed = bool(user and user.active and member and member.role in {'admin', 'operator'})
        if allowed: reason = None
    except HTTPException: pass
    return {'instance_id': instance.id, 'release_id': instance.active_release_id, 'can_upload': allowed, 'reason': reason}


def prior(db, p, body, scope):
    nonce = str(UUID(body['request_id']))
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(digest({
        'runtime_document_command': p.workspace_id, 'nonce': nonce})[:15], 16)})
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == COMMAND, m.Audit.object_id == nonce)))
    need(len(rows) <= 1)
    if not rows: return None
    row = rows[0]
    need(row.actor_id == p.user_id and row.data['scope'] == scope and same(row.data['body'], body)
        and row.data['request_hash'] == digest(body), '原请求不能更换内容、路径或发起人')
    return copy.deepcopy(row.data['result'])


def saved(db, p, body, scope, result):
    audit(db, p, COMMAND, body['request_id'], {'scope': scope, 'body': copy.deepcopy(body),
        'request_hash': digest(body), 'result': copy.deepcopy(result)})
    return result


def recover(db, p, nonce, expected_hash=None):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == COMMAND, m.Audit.object_id == nonce)))
    need(len(rows) <= 1)
    if not rows: return {'schema': 'runtime-document-command-v205', 'found': False, 'body': None, 'result': None}
    row = rows[0]; need(row.actor_id == p.user_id and digest(row.data['body']) == row.data['request_hash'])
    need(expected_hash is None or expected_hash == row.data['request_hash'], '原请求摘要不一致，不能恢复其他提交')
    return {'schema': 'runtime-document-command-v205', 'found': True,
        'body': copy.deepcopy(row.data['body']), 'result': copy.deepcopy(row.data['result'])}


def validate_upload(body, content):
    name = body['file_name']; suffix = PurePath(name).suffix.lower()
    need(type(content) is bytes and 0 < len(content) <= MAX_BYTES and len(content) == body['file_size']
        and hashlib.sha256(content).hexdigest() == body['file_sha256'], '上传字节与原请求大小或摘要不符')
    need(suffix in MEDIA and '/' not in name and '\\' not in name and not any(ord(c) < 32 for c in name), '仅接收PDF、DOCX、TXT或MD原文件')
    return MEDIA[suffix]


def queue(db, p, kind, identity, key):
    job = m.Job(workspace_id=p.workspace_id, kind=kind, idempotency_key=key,
        data={'actor_id': p.user_id, 'runtime_document_id': identity})
    db.add(job); db.flush(); return job


def upload(db, p, instance_id, body, content, document_id=None):
    media = validate_upload(body, content)  # Including exact-original nonce replay.
    scope = 'upload:' + instance_id if document_id is None else 'version:' + document_id
    old = prior(db, p, body, scope)
    if old is not None: return old
    current_actor(db, p); instance, release = active(db, p, instance_id, body['release_id'])
    if document_id:
        document = scoped(db, Document, document_id, p, True)
        need(document.instance_id == instance.id); check_version(document, body['expected_version'])
        need(document.latest_version_id == body['prior_version_id'], '原文档已有新版本，请核对后更新')
        previous = scoped(db, Version, document.latest_version_id, p)
        sequence, prior_id = previous.sequence + 1, previous.id
    else:
        document = Document(id=m.uid(), workspace_id=p.workspace_id, instance_id=instance.id,
            title=body['title'], data={})
        db.add(document); db.flush(); sequence, prior_id = 1, None
    identity = m.uid()
    evidence = save_immutable(db, p, content, media, {'modality': 'runtime_document', 'runtime_document_version_id': identity})
    verified, stored = evidence_bytes(db, p, evidence.id)
    need(stored == content and verified.sha256 == body['file_sha256'] and verified.size_bytes == len(content)
        and verified.media_type == media and verified.data.get('runtime_document_version_id') == identity,
        '本次原件存储无法核对，未确认上传成功；已有原件未覆盖')
    version = Version(id=identity, workspace_id=p.workspace_id, document_id=document.id, instance_id=instance.id,
        release_id=release.id, actor_id=p.user_id, sequence=sequence, name=body['file_name'], evidence_id=evidence.id, data={})
    db.add(version); db.flush()
    job = queue(db, p, 'parse_runtime_document', version.id, 'runtime-document-parse:' + version.id)
    origin = {'version_id': version.id, 'document_id': document.id, 'instance_id': instance.id,
        'release_id': release.id, 'configuration_hash': release.configuration_hash, 'actor_id': p.user_id,
        'sequence': sequence, 'prior_version_id': prior_id, 'title': body['title'], 'name': version.name,
        'raw_sha256': evidence.sha256, 'raw_size': evidence.size_bytes, 'media_type': media,
        'evidence_id': evidence.id, 'job_id': job.id, 'request_hash': digest(body)}
    version.data = {'origin': origin, 'latest_attempt_id': None, 'error': None}
    audit(db, p, UPLOAD, version.id, origin)
    document.latest_version_id = version.id; document.title = body['title']
    if document_id: document.version += 1
    return saved(db, p, body, scope, document_view(db, p, document))


@contextmanager
def verification_scope(db):
    if 'runtime_document_checked' in db.info:
        yield; return
    db.info['runtime_document_checked'] = {}
    try: yield
    finally: db.info.pop('runtime_document_checked', None)


def checked_version(db, p, version):
    origin = version.data['origin']
    need(version.workspace_id == p.workspace_id and origin['version_id'] == version.id
        and origin['document_id'] == version.document_id and origin['instance_id'] == version.instance_id
        and origin['release_id'] == version.release_id and origin['actor_id'] == version.actor_id
        and origin['sequence'] == version.sequence and origin['name'] == version.name and origin['evidence_id'] == version.evidence_id)
    need(anchored(db, p, UPLOAD, version.id, origin).actor_id == version.actor_id)
    document = scoped(db, Document, version.document_id, p); need(document.instance_id == version.instance_id)
    release = scoped(db, m.InstanceRelease, version.release_id, p)
    need(release.instance_id == version.instance_id and release.configuration_hash == origin['configuration_hash'])
    cache = db.info.get('runtime_document_checked')
    key = (db.get_transaction(), version.id, digest(origin))
    if cache is not None and key in cache: return cache[key]['raw']
    evidence, raw = evidence_bytes(db, p, version.evidence_id)
    need(evidence.sha256 == origin['raw_sha256'] and evidence.size_bytes == origin['raw_size'] == len(raw)
        and evidence.media_type == origin['media_type'] and evidence.data.get('runtime_document_version_id') == version.id)
    if cache is not None: cache[key] = {'version': version, 'raw': raw}
    return raw


def attempt_origin(attempt): return {'attempt_id': attempt.id, 'version_id': attempt.document_version_id,
    'job_id': attempt.job_id, 'actor_id': attempt.actor_id, 'ordinal': attempt.ordinal, **attempt.data['pin']}


def checked_attempt(db, p, version, attempt):
    cache = db.info.get('runtime_document_checked')
    key = ('attempt', db.get_transaction(), version.id, attempt.id, attempt.version)
    old = cache.get(key) if cache is not None else None
    if old is not None and old['data'] is attempt.data: return old['result']
    checked_version(db, p, version)
    need(attempt.document_version_id == version.id and attempt.job_id == version.data['origin']['job_id'])
    need(anchored(db, p, START, attempt.id, attempt_origin(attempt)).actor_id == attempt.actor_id)
    need(attempt.status in TERMINAL and attempt.data['result']['status'] == attempt.status)
    value = {'attempt': attempt_origin(attempt), 'result': attempt.data['result'], 'event_id': attempt.data['event_id']}
    need(anchored(db, p, RESULT, attempt.id, value).actor_id == attempt.actor_id)
    need(attempt.data['parse_hash'] == digest(attempt.data['result']))
    if cache is not None:
        key = ('attempt', db.get_transaction(), version.id, attempt.id, attempt.version)
        cache[key] = {'attempt': attempt, 'version': version, 'data': attempt.data, 'result': attempt.data['result']}
    return attempt.data['result']


def execution(db, p, version):
    current_actor(db, p); _, release = active(db, p, version.instance_id, version.release_id)
    need(release.configuration_hash == version.data['origin']['configuration_hash'])


def worker_job(db, p, job, kind, identity, original_actor):
    from .leases import ownership
    lease = ownership(db)
    need(isinstance(job, m.Job) and job.workspace_id == p.workspace_id and job.kind == kind
        and job.status == 'running' and job.data.get('runtime_document_id') == identity
        and job.data.get('actor_id') == p.user_id and lease.get('job_id') == job.id
        and lease.get('lease_token') == job.lease_token, '原后台任务或租约无法核对')
    authorization(db, p, job, original_actor)


def authorization(db, p, job, original_actor):
    auth_id = job.data.get('runtime_document_authorization_id')
    if auth_id is None: need(job.data['actor_id'] == original_actor); return
    auth = scoped(db, m.Audit, auth_id, p); value = auth.data
    need(auth.action == AUTH and auth.object_id == job.id and auth.actor_id == job.data['actor_id']
        and value['job_id'] == job.id and value['subject_id'] == job.data['runtime_document_id']
        and value['actor_id'] == job.data['actor_id'] and value['body_hash'] == digest(value['body']))
    command = anchored(db, p, COMMAND, value['body']['request_id'], {'scope': 'retry:' + value['subject_id'],
        'body': value['body'], 'request_hash': value['body_hash'], 'result': value['command_result']})
    need(command.actor_id == auth.actor_id)


def finish(db, p, version, attempt, result):
    need(attempt.status == 'parsing' and result['status'] in TERMINAL)
    attempt.status = result['status']; attempt.version += 1
    attempt.data = {**attempt.data, 'result': copy.deepcopy(result), 'parse_hash': digest(result), 'finished_at': m.now().isoformat()}
    receipt = {'schema': 'runtime-document-receipt-v205', 'version_id': version.id, 'document_id': version.document_id,
        'instance_id': version.instance_id, 'attempt_id': attempt.id, 'actor_id': attempt.actor_id,
        'raw_sha256': version.data['origin']['raw_sha256'], 'origin_hash': digest(version.data['origin']),
        'attempt_hash': digest(attempt_origin(attempt)), 'parse_hash': attempt.data['parse_hash'],
        'status': result['status'], 'blocks_hash': digest(result['blocks']), 'searchable': result['status'] == 'parsed'}
    event = m.Event(workspace_id=p.workspace_id, kind='runtime_document.parsed',
        idempotency_key='runtime-document-result:' + attempt.id, data={'receipt': receipt})
    db.add(event); db.flush()
    attempt.data = {**attempt.data, 'event_id': event.id}
    audit(db, Principal(attempt.actor_id, p.workspace_id, 'viewer', 'runtime-document-result'), RESULT, attempt.id,
        {'attempt': attempt_origin(attempt), 'result': result, 'event_id': event.id})
    version.status = result['status']; version.version += 1
    version.data = {**version.data, 'latest_attempt_id': attempt.id, 'error': result.get('error')}


def process_parse(db, p, job):
    version = scoped(db, Version, job.data['runtime_document_id'], p)
    need(version.data['origin']['job_id'] == job.id)
    worker_job(db, p, job, 'parse_runtime_document', version.id, version.actor_id)
    checked_version(db, p, version)
    if version.status in TERMINAL:
        if version.data.get('latest_attempt_id'):
            checked_attempt(db, p, version, scoped(db, Attempt, version.data['latest_attempt_id'], p))
        return {'status': version.status, 'error': version.data.get('error')}
    execution(db, p, version)
    version = scoped(db, Version, version.id, p, True)
    old = scoped(db, Attempt, version.data['latest_attempt_id'], p) if version.data.get('latest_attempt_id') else None
    if old and old.status == 'parsing':
        finish(db, p, version, old, {'status': 'failed', 'blocks': [], 'engine': 'native-interrupted-v205',
            'warnings': [], 'error': '上次后台未保存解析结果，已保留中断记录并沿原任务继续'})
    ordinal = (db.scalar(select(func.max(Attempt.ordinal)).where(Attempt.document_version_id == version.id)) or 0) + 1
    attempt = Attempt(workspace_id=p.workspace_id, document_version_id=version.id, job_id=job.id, actor_id=p.user_id,
        ordinal=ordinal, data={'pin': {'lease_token': job.lease_token, 'job_attempt': job.attempts,
            'origin_hash': digest(version.data['origin'])}})
    db.add(attempt); db.flush(); audit(db, p, START, attempt.id, attempt_origin(attempt))
    version.status = 'parsing'; version.version += 1
    version.data = {**version.data, 'latest_attempt_id': attempt.id, 'error': None}
    raw = checked_version(db, p, version)
    parse_name, version_id, release_id = version.name, version.id, version.release_id
    configuration_hash, token, job_id = version.data['origin']['configuration_hash'], job.lease_token, job.id
    db.commit()
    from .document_parsers import ParseProblem
    from .runtime_document_parser import parse_runtime_document
    try:
        result = parse_runtime_document(raw, parse_name, guard=lambda: parse_guard(
            p, job_id, token, version_id, release_id, configuration_hash))
    except ParseCancelled:
        return {'status': 'cancelled'}
    except Exception as error:
        from .leases import LeaseLost, ExecutionUnauthorized
        if isinstance(error, (LeaseLost, ExecutionUnauthorized)): raise
        result = {'status': 'failed', 'blocks': [], 'engine': 'native-failed-v205', 'warnings': [],
            'error': str(error)[:1000] if isinstance(error, ParseProblem) else '原件解析失败，请核对文件后恢复'}
    execution(db, p, version)
    version = scoped(db, Version, version.id, p, True); attempt = scoped(db, Attempt, attempt.id, p, True)
    if version.status == 'cancelled':
        if attempt.status == 'parsing':
            finish(db, p, version, attempt, {'status': 'cancelled', 'blocks': [], 'engine': 'native-cancelled-v205', 'warnings': [], 'error': None})
        return {'status': 'cancelled'}
    need(version.data['latest_attempt_id'] == attempt.id and attempt.data['pin']['lease_token'] == job.lease_token)
    finish(db, p, version, attempt, result)
    return {'status': version.status, 'error': version.data.get('error')}


def selected_attempt(db, p, version, identity=None, expected_hash=None):
    identity = identity or version.data.get('latest_attempt_id')
    need(identity is not None, '此版本尚无已保存的解析结果')
    attempt = scoped(db, Attempt, identity, p)
    result = checked_attempt(db, p, version, attempt)
    need(expected_hash is None or expected_hash == attempt.data['parse_hash'], '引用的原解析结果摘要不一致')
    return attempt, result


def citation(db, p, version, block_id, attempt_id=None, expected_hash=None):
    from .runtime_document_receipts import delivery_view
    attempt, result = selected_attempt(db, p, version, attempt_id, expected_hash)
    block = next((block for block in result['blocks'] if block['id'] == block_id), None)
    if block is None: raise HTTPException(404, '原解析结果中没有此段正文')
    origin = version.data['origin']
    query = urlencode({'attempt_id': attempt.id, 'expected_parse_hash': attempt.data['parse_hash']})
    return {'version_id': version.id, 'document_id': version.document_id, 'instance_id': version.instance_id,
        'attempt_id': attempt.id, 'title': origin['title'], 'name': version.name, 'sequence': version.sequence,
        'block_id': block_id, 'text': block['text'], 'kind': block['kind'], 'location': copy.deepcopy(block['location']),
        'block_hash': digest(block),
        'raw_sha256': origin['raw_sha256'], 'parse_hash': attempt.data['parse_hash'],
        'parse_engine': result['engine'], 'delivery': delivery_view(db, p, attempt),
        'citation_url': '/api/runtime-documents/versions/' + version.id + '/citations/' + block_id + '?' + query}


def attempt_view(db, p, attempt):
    from .runtime_document_receipts import delivery_view
    result = attempt.data.get('result', {})
    return {'id': attempt.id, 'version': attempt.version, 'status': attempt.status, 'created_at': attempt.created_at.isoformat(),
        'finished_at': attempt.data.get('finished_at'), 'parse_hash': attempt.data.get('parse_hash'),
        'parse_engine': result.get('engine'), 'blocks_count': len(result.get('blocks', [])),
        'warnings': result.get('warnings', []), 'error': result.get('error'),
        'delivery': delivery_view(db, p, attempt) if attempt.status in TERMINAL else None}


def version_view(db, p, version):
    latest = scoped(db, Attempt, version.data['latest_attempt_id'], p) if version.data.get('latest_attempt_id') else None
    result = latest.data.get('result', {}) if latest else {}
    eligibility = context(db, p, version.instance_id)
    allowed = eligibility['can_upload'] and eligibility['release_id'] == version.release_id
    # A cancelled Version does not settle its original queued/running Job.
    # Match control.retry's real gate, using a current read without row locks;
    # this function is also used by read-only historical API sessions.
    job = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        m.Job.id == version.data['origin']['job_id']).execution_options(populate_existing=True))
    need(job is not None and job.kind == 'parse_runtime_document'
        and job.idempotency_key == 'runtime-document-parse:' + version.id
        and job.data.get('runtime_document_id') == version.id,
        '原文档后台任务不存在或关联不一致，不能据此提供恢复操作')
    job_pending = job.status in {'queued', 'running'}
    from .runtime_document_receipts import delivery_view
    return {'id': version.id, 'version': version.version, 'document_id': version.document_id,
        'instance_id': version.instance_id, 'sequence': version.sequence, 'name': version.name,
        'status': version.status, 'raw_saved': True, 'raw_sha256': version.data['origin']['raw_sha256'],
        'parse_hash': latest.data.get('parse_hash') if latest else None, 'parse_engine': result.get('engine'),
        'blocks_count': len(result.get('blocks', [])), 'warnings': result.get('warnings', []), 'error': version.data.get('error'),
        'can_retry': bool(allowed and version.status in {'failed', 'needs_input', 'cancelled'} and not job_pending),
        'can_cancel': bool(allowed and version.status in {'queued', 'parsing'}),
        'stopping': bool(version.status == 'cancelled' and job_pending),
        'latest_attempt_id': latest.id if latest else None,
        'delivery': delivery_view(db, p, latest) if latest and latest.status in TERMINAL else None}


def document_view(db, p, document):
    return {'id': document.id, 'version': document.version, 'instance_id': document.instance_id,
        'title': document.title, 'latest_version_id': document.latest_version_id,
        'latest': version_view(db, p, scoped(db, Version, document.latest_version_id, p)), 'created_at': document.created_at.isoformat()}


def control(db, p, identity, body, action):
    old = prior(db, p, body, action + ':' + identity)
    if old is not None: return old
    version = scoped(db, Version, identity, p)
    job = scoped(db, m.Job, version.data['origin']['job_id'], p, True)  # Same order as lease attachment.
    need(job.kind == 'parse_runtime_document' and job.data.get('runtime_document_id') == version.id)
    execution(db, p, version)
    version = scoped(db, Version, identity, p, True); check_version(version, body['expected_version'])
    checked_version(db, p, version)
    if action == 'cancel':
        need(version.status in {'queued', 'parsing'}, '已保存的解析结果不能被取消覆盖')
        attempt = scoped(db, Attempt, version.data['latest_attempt_id'], p, True) if version.data.get('latest_attempt_id') else None
        if attempt and attempt.status == 'parsing':
            finish(db, p, version, attempt, {'status': 'cancelled', 'blocks': [], 'engine': 'native-cancelled-v205',
                'warnings': [], 'error': None})
        else: version.status = 'cancelled'; version.version += 1
    else:
        need(version.status in {'failed', 'needs_input', 'cancelled'} and job.status not in {'queued', 'running'},
            '原处理仍在进行或已有成功结果，不能重复解析')
        if version.data.get('latest_attempt_id'):
            checked_attempt(db, p, version, scoped(db, Attempt, version.data['latest_attempt_id'], p))
        version.status = 'queued'; version.version += 1; version.data = {**version.data, 'error': None}
        job.status = 'queued'; job.attempts = 0; job.available_at = m.now(); job.lease_token = None; job.lease_until = None
        job.data = {**job.data, 'actor_id': p.user_id}
    audit(db, p, 'runtime_document.' + action, version.id, {'reason': body['reason'], 'version': version.version})
    result = version_view(db, p, version)
    if action == 'retry':
        auth = m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=AUTH, object_id=job.id,
            data={'job_id': job.id, 'subject_id': version.id, 'actor_id': p.user_id, 'body': copy.deepcopy(body),
                'body_hash': digest(body), 'command_result': copy.deepcopy(result)})
        db.add(auth); db.flush(); job.data = {**job.data, 'runtime_document_authorization_id': auth.id}
    return saved(db, p, body, action + ':' + identity, result)


def search_origin(search):
    return {'search_id': search.id, 'instance_id': search.instance_id, 'release_id': search.release_id,
        'actor_id': search.actor_id, 'keyword': search.keyword, 'inventory': search.data['inventory'],
        'coverage': search.data['coverage'], 'request_hash': search.data['request_hash'], 'job_id': search.data['job_id']}


def search_hit(full, keyword):
    """Bounded excerpt with positions in the original, not casefolded, text."""
    text = full['text']; folded_start = text.casefold().find(keyword.casefold())
    need(folded_start >= 0)
    folded_end = folded_start + len(keyword.casefold()); cursor = 0; start = end = None
    for index, char in enumerate(text):
        following = cursor + len(char.casefold())
        if start is None and following > folded_start: start = index
        if following >= folded_end: end = index + 1; break
        cursor = following
    need(start is not None and end is not None)
    left = max(0, start - (HIT_TEXT_LIMIT - (end - start)) // 2)
    right = min(len(text), left + HIT_TEXT_LIMIT); left = max(0, right - HIT_TEXT_LIMIT)
    return {**full, 'text': text[left:right], 'text_is_excerpt': left != 0 or right != len(text),
        'char_span': {'start': left, 'end': right}}


def start_search(db, p, instance_id, body):
    old = prior(db, p, body, 'search:' + instance_id)
    if old is not None: return old
    current_actor(db, p); active(db, p, instance_id, body['release_id'])
    need(type(body['keyword']) is str and 0 < len(body['keyword'].strip()) <= 200, '请输入要查找的原文字词')
    documents = list(db.scalars(select(Document).where(Document.workspace_id == p.workspace_id,
        Document.instance_id == instance_id).order_by(Document.created_at.desc(), Document.id.desc()).limit(DOCUMENT_LIMIT + 1)))
    inventory = []; limited = len(documents) > DOCUMENT_LIMIT
    from .runtime_document_receipts import delivery_view
    with verification_scope(db):
        for document in documents[:DOCUMENT_LIMIT]:
            version = scoped(db, Version, document.latest_version_id, p)
            item = {'document_id': document.id, 'version_id': version.id, 'attempt_id': None, 'parse_hash': None,
                'eligible': False, 'reason': '当前最新版本尚无完整且已核对接收的正文'}
            if version.status == 'parsed' and version.data.get('latest_attempt_id'):
                attempt, result = selected_attempt(db, p, version)
                if result['status'] == 'parsed' and delivery_view(db, p, attempt)['verified']:
                    item.update(attempt_id=attempt.id, parse_hash=attempt.data['parse_hash'], eligible=True, reason=None)
            inventory.append(item)
    coverage = {'selected': len(inventory), 'eligible': sum(item['eligible'] for item in inventory),
        'unavailable': sum(not item['eligible'] for item in inventory), 'limited': limited,
        'document_limit': DOCUMENT_LIMIT, 'hit_limit': HIT_LIMIT,
        'notice': '仅固定本次选择的前100份最新版本；未包括其余资料。' if limited else None}
    search = Search(workspace_id=p.workspace_id, instance_id=instance_id, release_id=body['release_id'],
        actor_id=p.user_id, keyword=body['keyword'], data={'inventory': inventory, 'coverage': coverage,
            'request_hash': digest(body), 'error': None})
    db.add(search); db.flush()
    job = queue(db, p, 'search_runtime_documents', search.id, 'runtime-document-search:' + search.id)
    search.data = {**search.data, 'job_id': job.id}; audit(db, p, SEARCH_ORIGIN, search.id, search_origin(search))
    return saved(db, p, body, 'search:' + instance_id, search_view(db, p, search))


def start_selected_search(db, p, instance_id, body, *, selected_version_id,
                          selected_attempt_id, expected_parse_hash, expected_raw_sha256):
    """Trusted single-version creation; the parent owns durable child recovery.

    This helper never adopts a pre-existing ordinary Search merely because
    its request body matches. Parent LINK/WAIT and this normal Search/Job must
    commit in one caller-owned transaction. It never commits or selects latest.
    """
    def argument(value, message):
        if not value: raise HTTPException(422, message)

    try:
        argument(type(body) is dict and set(body) == {'request_id', 'release_id', 'keyword'},
                 '固定正文搜索的请求字段不完整')
        argument(all(type(value) is str and str(UUID(value)) == value for value in
                 (instance_id, body['request_id'], body['release_id'], selected_version_id, selected_attempt_id)),
                 '固定正文搜索的版本或原请求编号无效')
        argument(all(type(value) is str and len(value) == 64 and not set(value)-set('0123456789abcdef')
                 for value in (expected_parse_hash, expected_raw_sha256)),
                 '固定正文搜索的原件或解析摘要无效')
        argument(type(body['keyword']) is str and 0 < len(body['keyword'].strip()) <= len(body['keyword']) <= 200,
                 '请输入明确的原文字词')
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(422, '固定正文搜索的请求或原版本参数无效') from None
    # prior serializes the existing normal document-command nonce. Its return
    # is deliberately a conflict, not a claim that this parent's child exists.
    old = prior(db, p, body, 'search:' + instance_id)
    need(old is None, '正文搜索原请求号已被使用，请从原父问题恢复；不能收养已有搜索')
    signature = digest(body)
    existing = db.scalar(select(Search.id).where(Search.workspace_id == p.workspace_id,
        Search.data['request_hash'].as_string() == signature).limit(1))
    witness = db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == SEARCH_ORIGIN, m.Audit.data['request_hash'].as_string() == signature).limit(1))
    need(existing is None and witness is None,
         '正文搜索原记录或独立来源证明已存在，未另建子任务')
    current_actor(db, p)
    active(db, p, instance_id, body['release_id'])
    version = scoped(db, Version, selected_version_id, p)
    origin = version.data.get('origin')
    need(version.instance_id == instance_id and version.status == 'parsed'
         and type(origin) is dict and origin.get('raw_sha256') == expected_raw_sha256,
         '指定文档版本或原件摘要已变化，未采用最新版本替代')
    from .runtime_document_receipts import delivery_view
    with verification_scope(db):
        attempt, result = selected_attempt(db, p, version, selected_attempt_id, expected_parse_hash)
        job = scoped(db, m.Job, attempt.job_id, p)
        need(job.kind == 'parse_runtime_document' and job.status == 'succeeded'
             and job.data.get('runtime_document_id') == version.id
             and job.data.get('actor_id') == attempt.actor_id
             and attempt.status == result['status'] == 'parsed',
             '指定正文解析的实际后台任务尚未成功结算，未创建搜索')
        authorization(db, p, job, version.actor_id)
        need(delivery_view(db, p, attempt)['verified'],
             '指定正文尚无可核对的独立收档回执，未创建搜索')
    inventory = [{'document_id': version.document_id, 'version_id': version.id,
        'attempt_id': attempt.id, 'parse_hash': attempt.data['parse_hash'], 'eligible': True, 'reason': None}]
    coverage = {'selected': 1, 'eligible': 1, 'unavailable': 0, 'limited': False,
        'document_limit': 1, 'hit_limit': HIT_LIMIT,
        'notice': '仅本次明确选择的一份固定文档版本；未包括其他版本或资料。'}
    search = Search(workspace_id=p.workspace_id, instance_id=instance_id, release_id=body['release_id'],
        actor_id=p.user_id, keyword=body['keyword'], data={'inventory': inventory, 'coverage': coverage,
            'request_hash': signature, 'error': None})
    db.add(search)
    db.flush()
    job = queue(db, p, 'search_runtime_documents', search.id, 'runtime-document-search:' + search.id)
    search.data = {**search.data, 'job_id': job.id}
    audit(db, p, SEARCH_ORIGIN, search.id, search_origin(search))
    return saved(db, p, body, 'search:' + instance_id, search_view(db, p, search))


def process_search(db, p, job):
    search = scoped(db, Search, job.data['runtime_document_id'], p)
    need(search.data['job_id'] == job.id and anchored(db, p, SEARCH_ORIGIN, search.id, search_origin(search)).actor_id == search.actor_id)
    worker_job(db, p, job, 'search_runtime_documents', search.id, search.actor_id)
    if search.status == 'completed': checked_search(db, p, search); return {'status': 'completed'}
    current_actor(db, p); active(db, p, search.instance_id, search.release_id)
    search.status = 'searching'; db.commit()
    hits = []; limited = search.data['coverage']['limited']; scanned_blocks = scanned_characters = 0; exhausted = False
    with verification_scope(db):
        for item in search.data['inventory']:
            if not item['eligible']: continue
            version = scoped(db, Version, item['version_id'], p)
            need(version.instance_id == search.instance_id and version.document_id == item['document_id'])
            attempt, result = selected_attempt(db, p, version, item['attempt_id'], item['parse_hash'])
            from .runtime_document_receipts import delivery_view
            need(result['status'] == 'parsed' and delivery_view(db, p, attempt)['verified'], '原搜索选定正文或接收依据已无法核对')
            for block in result['blocks']:
                if scanned_blocks >= SCAN_BLOCK_LIMIT or scanned_characters + len(block['text']) > SCAN_CHARACTER_LIMIT:
                    limited = exhausted = True; break
                scanned_blocks += 1; scanned_characters += len(block['text'])
                if search.keyword.casefold() not in block['text'].casefold(): continue
                hits.append(search_hit(citation(db, p, version, block['id'], attempt.id, item['parse_hash']), search.keyword))
                if len(hits) >= HIT_LIMIT: limited = exhausted = True; break
            if exhausted: break
    current_actor(db, p); active(db, p, search.instance_id, search.release_id)
    search = scoped(db, Search, search.id, p, True)
    coverage = {**search.data['coverage'], 'limited': limited, 'scanned_blocks': scanned_blocks,
        'scanned_characters': scanned_characters, 'scan_block_limit': SCAN_BLOCK_LIMIT,
        'scan_character_limit': SCAN_CHARACTER_LIMIT, 'hit_text_limit': HIT_TEXT_LIMIT,
        'excerpted_hits': sum(hit['text_is_excerpt'] for hit in hits)}
    if limited: coverage['notice'] = '本次仅展示已选资料与前100处匹配；未表示已完整覆盖全部资料。'
    result = {'inventory_hash': digest(search.data['inventory']), 'hits': hits, 'coverage': coverage}
    search.data = {**search.data, 'result': result, 'error': None}; search.status = 'completed'; search.version += 1
    audit(db, p, SEARCH_RESULT, search.id, result)
    return {'status': 'completed'}


def checked_search(db, p, search):
    need(anchored(db, p, SEARCH_ORIGIN, search.id, search_origin(search)).actor_id == search.actor_id)
    result = search.data['result']; anchored(db, p, SEARCH_RESULT, search.id, result)
    need(result['inventory_hash'] == digest(search.data['inventory']))
    with verification_scope(db):
        for hit in result['hits']:
            version = scoped(db, Version, hit['version_id'], p)
            need(version.instance_id == search.instance_id and any(item['eligible']
                and item['document_id'] == version.document_id and item['version_id'] == version.id
                and item['attempt_id'] == hit['attempt_id'] and item['parse_hash'] == hit['parse_hash']
                for item in search.data['inventory']))
            actual = citation(db, p, version, hit['block_id'], hit['attempt_id'], hit['parse_hash'])
            expected = search_hit(actual, search.keyword)
            need(same({k: v for k, v in expected.items() if k != 'delivery'}, {k: v for k, v in hit.items() if k != 'delivery'})
                and actual['delivery']['verified'])
    return result


def search_view(db, p, search):
    error, status, hits = search.data.get('error'), search.status, []
    coverage = search.data.get('coverage', {})
    if status == 'completed':
        try:
            result = checked_search(db, p, search); hits = result['hits']; coverage = result['coverage']
        except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError):
            status, error = 'failed', '原搜索结果、固定引用或接收依据无法核对；未用当前正文替换'
    return {'id': search.id, 'version': search.version, 'instance_id': search.instance_id,
        'keyword': search.keyword, 'status': status, 'error': error, 'coverage': coverage,
        'hits': copy.deepcopy(hits), 'created_at': search.created_at.isoformat()}


def recover_job(db, p, job, reason):
    # Refusal is a normal recovery outcome. Never let a forged/missing target
    # turn the original Job failure into a second exception escaping the loop.
    try:
        need(isinstance(job, m.Job) and job.workspace_id == p.workspace_id
            and job.kind in {'search_runtime_documents', 'parse_runtime_document'}
            and job.data.get('actor_id') == p.user_id)
        identity = job.data.get('runtime_document_id')
        need(type(identity) is str and str(UUID(identity)) == identity)
        if job.kind == 'search_runtime_documents':
            search = scoped(db, Search, identity, p, True)
            need(search.data['job_id'] == job.id
                and job.idempotency_key == 'runtime-document-search:' + search.id
                and anchored(db, p, SEARCH_ORIGIN, search.id, search_origin(search)).actor_id == search.actor_id)
            authorization(db, p, job, search.actor_id)
        else:
            version = scoped(db, Version, identity, p, True)
            origin = version.data['origin']
            need(type(origin) is dict and origin['job_id'] == job.id and origin['version_id'] == version.id
                and origin['document_id'] == version.document_id and origin['instance_id'] == version.instance_id
                and origin['release_id'] == version.release_id and origin['actor_id'] == version.actor_id
                and origin['evidence_id'] == version.evidence_id and origin['sequence'] == version.sequence
                and origin['name'] == version.name and job.idempotency_key == 'runtime-document-parse:' + version.id
                and anchored(db, p, UPLOAD, version.id, origin).actor_id == version.actor_id)
            authorization(db, p, job, version.actor_id)
            attempt = None
            if version.status not in TERMINAL and version.data.get('latest_attempt_id'):
                attempt_id = version.data['latest_attempt_id']
                need(type(attempt_id) is str and str(UUID(attempt_id)) == attempt_id)
                attempt = scoped(db, Attempt, attempt_id, p, True)
                need(attempt.document_version_id == version.id and attempt.job_id == job.id
                    and attempt.status == 'parsing' and attempt.data['pin']['origin_hash'] == digest(origin)
                    and anchored(db, p, START, attempt.id, attempt_origin(attempt)).actor_id == attempt.actor_id)
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError):
        # Only the original local Job receives this fixed diagnostic. No
        # submitted target ID, parser error, credential or foreign state leaks.
        if isinstance(job, m.Job) and job.workspace_id == p.workspace_id:
            message = '原后台任务与目标文档或搜索来源无法核对；目标记录未变更'
            if db.get(m.User, p.user_id) is not None:
                audit(db, p, 'runtime_document.recovery_refused_v205', job.id, {'reason': message})
            else:
                # An invented actor cannot satisfy Audit's User FK. Keep the
                # fixed refusal on the actual Job instead of guessing a user.
                job.data = {**job.data, 'runtime_document_recovery_error': message}
        return {'recovered': False, 'refused': True}
    if job.kind == 'search_runtime_documents':
        if search.status != 'completed':
            search.status = 'failed'; search.version += 1
            search.data = {**search.data, 'error': '查找未完成；原搜索范围和请求已保存，可查看处理状态'}
    else:
        if version.status in TERMINAL: return
        message = '解析未完成或执行批准已变化；原件和处理记录保留，可核对后明确重试'
        if attempt and attempt.status == 'parsing':
            finish(db, p, version, attempt, {'status': 'failed', 'blocks': [], 'engine': 'native-interrupted-v205', 'warnings': [], 'error': message})
        else:
            version.status = 'failed'; version.version += 1; version.data = {**version.data, 'error': message}
        audit(db, p, 'runtime_document.worker_interrupted', version.id, {'error': message})
