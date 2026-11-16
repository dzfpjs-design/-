"""Explicit, source-bound dataset capture and schema-checked object import.

Lock order for commands/writes: existing Job (worker/retry/resume) -> current User -> Membership -> Instance (when
bound) -> Source -> Binding -> Import -> row/link -> Entity. No lock survives
HTTP or file parsing. Each object mutation and its lineage event commit together.
"""
import copy
import hashlib
import math
import os
from contextlib import contextmanager
from functools import wraps
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import func, select, text

from . import models as m
from .business_data_models import (BusinessDataSource as Source, BusinessDataRead as Read,
    BusinessDataBinding as Binding, BusinessDataImport as Import, BusinessDataImportRow as ImportRow,
    BusinessDataRecordLink as Link)
from .contracts import EntityCreate, Manifest, digest
from .evidence import evidence_bytes, save_immutable
from .services import audit, check_version, create_entity, scoped, update_entity
from .security import require

NS = 'platform_business_data'
ADAPTER = 'business_data_source'
COMMAND = 'business_data.command_v204'
SOURCE_AUDIT = 'business_data.source_v204'
RAW_AUDIT = 'business_data.raw_v204'
PARSED_AUDIT = 'business_data.parsed_v204'
READ_ATTEMPT = 'business_data.read_attempt_v204'
READ_ORIGIN = 'business_data.read_origin_v204'
IMPORT_ORIGIN = 'business_data.import_origin_v204'
JOB_AUTH = 'business_data.job_authorized_v204'
BINDING_AUDIT = 'business_data.binding_v204'
PREVIEW_AUDIT = 'business_data.preview_v204'
ROW_AUDIT = 'business_data.row_preview_v204'
IMPORTED_AUDIT = 'business_data.row_imported_v204'
KINDS = {'http_json', 'csv', 'json', 'xlsx'}
MEDIA = {'csv': 'text/csv', 'json': 'application/json', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
MAX_BYTES = 20 * 1024 * 1024


def need(ok, message='原业务数据、映射、对象版本或独立来源依据无法核对'):
    if not ok: raise HTTPException(409, message)


def current_actor(db, p, *, lock=True):
    query = select(m.User).where(m.User.id == p.user_id, m.User.active.is_(True))
    if lock: query = query.with_for_update()
    need(db.scalar(query.execution_options(populate_existing=True)) is not None, '当前账户已停用')
    query = select(m.Membership).where(m.Membership.user_id == p.user_id,
        m.Membership.workspace_id == p.workspace_id, m.Membership.role.in_(['admin', 'operator']))
    if lock: query = query.with_for_update()
    need(db.scalar(query.execution_options(populate_existing=True)) is not None, '当前工作区执行权限已撤销')


def anchored(db, p, action, identity, value):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.object_id == identity, m.Audit.action == action)))
    need(len(rows) == 1 and same(rows[0].data, value))
    return rows[0]


def request_prior(db, p, body, scope=None, *, lock=True):
    db.info['business_data_command_scope'] = scope
    nonce = str(UUID(body['request_id']))
    if lock:
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
            {'key': int(digest({'business_data_command': p.workspace_id, 'request_id': nonce})[:15], 16)})
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == COMMAND, m.Audit.data['request_id'].as_string() == nonce)))
    need(len(rows) <= 1)
    if not rows: return None
    value = rows[0].data
    need(rows[0].actor_id == p.user_id and value['body'] == body and value['request_hash'] == digest(body)
        and value.get('scope') == scope,
        '原请求不能更换内容或发起人；请核对原提交')
    return copy.deepcopy(value['result'])


def command_saved(db, p, body, result):
    audit(db, p, COMMAND, body['request_id'], {'request_id': body['request_id'],
        'request_hash': digest(body), 'body': copy.deepcopy(body), 'result': copy.deepcopy(result),
        'scope': db.info.get('business_data_command_scope')})
    return result


def recover_request(db, p, request_id, expected_hash=None):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == COMMAND, m.Audit.data['request_id'].as_string() == request_id)))
    need(len(rows) <= 1)
    if not rows: return {'schema': 'business-data-command-v204', 'found': False, 'body': None, 'result': None}
    row = rows[0]
    need(row.actor_id == p.user_id and row.data['request_hash'] == digest(row.data['body'])
        and (expected_hash is None or expected_hash == row.data['request_hash']), '原请求内容或发起人不符')
    return {'schema': 'business-data-command-v204', 'found': True,
        'body': copy.deepcopy(row.data['body']), 'result': copy.deepcopy(row.data['result'])}


def source_manifest(source_id, revision, config):
    execution = {'adapter': ADAPTER, 'side_effect': False}
    return Manifest(kind='source', namespace=NS, name='dataset_' + source_id.replace('-', ''),
        version=f'{revision}.0.0', owner='业务数据连接', description=config['name'],
        allowed_agent_roles=['data'], execution=execution,
        data_policy={'cloud_allowed': False, 'external_allowed': config['kind'] == 'http_json', 'deployment': 'local'},
        specification={'schema': 'business-data-source-v204', 'source_id': source_id,
            'revision': revision, 'configuration': copy.deepcopy(config)}).model_dump(mode='json')


def check_config(config, workspace_id=None):
    need(config['kind'] in KINDS and type(config['name']) is str and bool(config['name'].strip()))
    reference = config.get('secret_ref')
    need(reference is None or type(reference) is str and reference.startswith('GA_CONNECTOR_SECRET_')
        and reference.removeprefix('GA_CONNECTOR_SECRET_').replace('_', '').isalnum(), '只允许服务端凭证引用名称')
    if config['kind'] == 'http_json':
        from .business_data_credentials import require_grant
        require_grant(workspace_id, config.get('endpoint'), reference)
        from .gateway import validate_endpoint
        validate_endpoint({'endpoint': config.get('endpoint')})
    else: need(config.get('endpoint') is None and reference is None, '上传连接不接收接口或凭证')


def checked_source(db, p, source, *, active=True, capability_id=None):
    need(source.workspace_id == p.workspace_id and (not active or source.status == 'active'), '业务数据连接已暂停')
    cap = scoped(db, m.Capability, capability_id or source.capability_id, p)
    config = cap.data.get('specification', {}).get('configuration')
    revision = cap.data.get('specification', {}).get('revision')
    need(type(revision) is int and revision >= 1 and type(config) is dict
        and same(cap.data, source_manifest(source.id, revision, config))
        and cap.namespace == NS and cap.name == 'dataset_' + source.id.replace('-', '')
        and cap.kind == 'source' and cap.status == 'published'
        and cap.release_version == f'{revision}.0.0'
        and cap.content_hash == cap.validated_hash == digest(cap.data))
    anchored(db, p, SOURCE_AUDIT, cap.id, {'source_id': source.id, 'capability_id': cap.id,
        'manifest_hash': cap.content_hash, 'revision': revision})
    if capability_id is None:
        need(source.data['revision'] == revision and same(source.data['configuration'], config)
            and source.name == config['name'] and source.kind == config['kind'])
    return cap, config


def save_source(db, p, body, identity=None):
    require(p, 'admin', 'operator')
    scope = 'save_source:' + (identity or 'new')
    prior = request_prior(db, p, body, scope, lock=False)
    if prior is not None: return prior
    config = {key: body[key] for key in ('name', 'kind', 'endpoint', 'secret_ref')}
    # validate_endpoint performs DNS. It runs before every advisory/row lock;
    # existing command recovery above never requires a new DNS lookup.
    check_config(config, p.workspace_id)
    prior = request_prior(db, p, body, scope)
    if prior is not None: return prior
    current_actor(db, p)
    if identity:
        row = scoped(db, Source, identity, p, True); check_version(row, body['expected_version'])
        need(row.kind == body['kind'], '修改数据类型请另建连接；旧读取保留')
        checked_source(db, p, row, active=False)
        revision = row.data['revision'] + 1
    else:
        row = Source(id=m.uid(), workspace_id=p.workspace_id, name=body['name'], kind=body['kind'],
            status='active', data={}); revision = 1
    manifest = source_manifest(row.id, revision, config)
    cap = m.Capability(workspace_id=p.workspace_id, namespace=NS, name=manifest['name'], kind='source',
        release_version=manifest['version'], status='published', data=manifest,
        content_hash=digest(manifest), validated_hash=digest(manifest))
    db.add(cap); db.flush()
    audit(db, p, SOURCE_AUDIT, cap.id, {'source_id': row.id, 'capability_id': cap.id,
        'manifest_hash': cap.content_hash, 'revision': revision})
    row.name = body['name']; row.capability_id = cap.id
    row.data = {**row.data, 'revision': revision, 'configuration': config}
    if identity: row.version += 1
    db.add(row); db.flush()
    return command_saved(db, p, body, source_view(db, p, row))


def source_view(db, p, row):
    _, config = checked_source(db, p, row, active=False)
    last = db.scalar(select(Read.id).where(Read.workspace_id == p.workspace_id, Read.source_id == row.id)
        .order_by(Read.created_at.desc(), Read.id.desc()).limit(1))
    return {'id': row.id, 'version': row.version, 'name': row.name, 'kind': row.kind, 'status': row.status,
        'endpoint': config.get('endpoint'), 'secret_ref': config.get('secret_ref'),
        'credential_configured': bool(config.get('secret_ref') and os.getenv(config['secret_ref'])),
        'last_read_id': last, 'revision': row.data['revision']}


def source_status(db, p, identity, body, status):
    prior = request_prior(db, p, body, 'source_status:' + identity + ':' + status)
    if prior is not None: return prior
    current_actor(db, p); row = scoped(db, Source, identity, p, True)
    check_version(row, body['expected_version']); checked_source(db, p, row, active=False)
    row.status = status; row.version += 1
    audit(db, p, 'business_data.source_' + status, row.id, {'reason': body['reason']})
    return command_saved(db, p, body, source_view(db, p, row))


def queue(db, p, kind, identity, key, **extra):
    old = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.idempotency_key == key))
    if old: return old
    row = m.Job(workspace_id=p.workspace_id, kind=kind, idempotency_key=key,
        data={'actor_id': p.user_id, 'business_data_id': identity, **extra})
    db.add(row); db.flush(); return row


def _raw_value(read):
    return {key: copy.deepcopy(read.data[key]) for key in ('source_hash', 'source_version', 'name', 'raw_sha256', 'raw_size',
        'raw_media_type', 'received_at', 'headers', 'request_hash')} | {'read_id': read.id,
        'source_id': read.source_id, 'capability_id': read.capability_id, 'evidence_id': read.evidence_id}


def save_raw(db, p, read, content, media_type, metadata):
    need(read.evidence_id is None, '原抓取已经保存，不能用新响应替换')
    need(type(content) is bytes and 0 < len(content) <= MAX_BYTES, '原件为空或超过20MiB，未替换已有原件')
    ev = save_immutable(db, p, content, media_type, {'modality': 'business_data', 'business_data_read_id': read.id})
    read.evidence_id = ev.id
    read.data = {**read.data, 'raw_sha256': ev.sha256, 'raw_size': ev.size_bytes, 'raw_media_type': media_type,
        'received_at': metadata.get('received_at', m.now().isoformat()), 'headers': copy.deepcopy(metadata.get('headers', {}))}
    audit(db, p, RAW_AUDIT, read.id, _raw_value(read)); db.flush()


def start_read(db, p, identity, body, content=None):
    # A saved command cannot make different uploaded bytes the original file.
    # This local check also applies when the old source is no longer executable.
    if content is not None:
        need(type(content) is bytes and 0 < len(content) <= MAX_BYTES
            and len(content) == body['file_size'] and hashlib.sha256(content).hexdigest() == body['file_sha256'],
            '上传原件与原请求大小或摘要不一致')
    prior = request_prior(db, p, body, 'start_read:' + identity)
    if prior is not None: return prior
    current_actor(db, p); source = scoped(db, Source, identity, p, True)
    check_version(source, body['expected_version']); cap, config = checked_source(db, p, source)
    need((source.kind == 'http_json') == (content is None), '此连接的读取方式不符')
    read = Read(id=m.uid(), workspace_id=p.workspace_id, source_id=source.id, capability_id=cap.id,
        actor_id=p.user_id, data={'source_version': source.version, 'source_hash': cap.content_hash,
            'kind': source.kind, 'name': body.get('file_name') or source.name,
            'request_hash': digest(body), 'request': copy.deepcopy(body), 'error': None})
    db.add(read); db.flush()
    if content is not None:
        save_raw(db, p, read, content, MEDIA[source.kind], {})
    job = queue(db, p, 'read_business_data', read.id, 'business-data-read:' + read.id)
    read.data = {**read.data, 'job_id': job.id}
    audit(db, p, READ_ORIGIN, read.id, read_origin(read))
    return command_saved(db, p, body, read_view(read))


def _read_authority(db, p, read, attempt=None):
    need(anchored(db, p, READ_ORIGIN, read.id, read_origin(read)).actor_id == read.actor_id)
    current_actor(db, p)
    source = scoped(db, Source, read.source_id, p, True)
    cap, config = checked_source(db, p, source)
    if config['kind'] == 'http_json':
        from .business_data_credentials import require_grant
        require_grant(p.workspace_id, config.get('endpoint'), config.get('secret_ref'))
    need(cap.id == read.capability_id
        and cap.content_hash == read.data['source_hash'], '原读取的连接版本已改变，请重新核对连接')
    if attempt is not None:
        rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.object_id == read.id, m.Audit.action == READ_ATTEMPT,
            m.Audit.data['lease_token'].as_string() == attempt['lease_token'])))
        need(source.version == attempt['source_version'] and len(rows) == 1
            and same(rows[0].data, attempt) and rows[0].actor_id == p.user_id,
            '本次读取期间连接或执行批准已变化；原读取保留，须明确恢复')
    return source, config


@contextmanager
def verification_scope(db):
    """Reuse immutable Read verification only within one read-only projection.

    The first check hashes the full frozen Read and independently checks its
    audits/raw bytes. The cache is discarded at the outer projection boundary,
    and every commit/rollback creates a different transaction key. Callers must
    not mutate Read JSON inside a projection; execution authority is never cached.
    """
    old = db.info.get('business_data_verification')
    if old is not None:
        yield old
        return
    value = {}
    db.info['business_data_verification'] = value
    try: yield value
    finally: db.info.pop('business_data_verification', None)


def projection(function):
    @wraps(function)
    def wrapped(db, *args, **kwargs):
        with verification_scope(db): return function(db, *args, **kwargs)
    return wrapped


def checked_read(db, p, read, *, parsed=True):
    cache = db.info.get('business_data_verification')
    key = (db.get_transaction(), p.workspace_id, read.id, read.version, read.evidence_id,
        read.source_id, read.capability_id, read.actor_id, parsed)
    cached = cache.get(key) if cache is not None else None
    if cached is not None and cached['data'] is read.data: return cached['raw']
    need(read.workspace_id == p.workspace_id and read.evidence_id is not None)
    need(anchored(db, p, READ_ORIGIN, read.id, read_origin(read)).actor_id == read.actor_id)
    source = scoped(db, Source, read.source_id, p)
    cap, _ = checked_source(db, p, source, active=False, capability_id=read.capability_id)
    need(cap.content_hash == read.data['source_hash'] and digest(read.data['request']) == read.data['request_hash'])
    ev, raw = evidence_bytes(db, p, read.evidence_id)
    need(ev.sha256 == read.data['raw_sha256'] and ev.size_bytes == read.data['raw_size']
        and ev.media_type == read.data['raw_media_type'] and ev.data.get('business_data_read_id') == read.id)
    anchored(db, p, RAW_AUDIT, read.id, _raw_value(read))
    if parsed:
        need(type(read.data.get('parsed')) is dict)
        anchored(db, p, PARSED_AUDIT, read.id, {'raw': _raw_value(read), 'parsed_hash': digest(read.data['parsed'])})
    if cache is not None:
        # Include the full frozen hash, not just an ID/raw-byte digest. It is
        # valid solely for this unchanged ORM JSON object and transaction.
        key = (db.get_transaction(), *key[1:])
        # SQLAlchemy's identity map is weak. Keep the verified row alive for
        # this projection; otherwise each receipt can reload/decode the same
        # Read into a different JSON object and unnecessarily rehash its file.
        cache[key] = {'read': read, 'data': read.data, 'frozen_hash': digest(read.data), 'raw': raw}
    return raw


def process_read(db, p, job):
    worker_job(db, p, job, 'read_business_data')
    read = scoped(db, Read, job.data['business_data_id'], p)
    need(read.data['job_id'] == job.id)
    worker_actor(db, p, job, read.actor_id)
    if read.status == 'ready': checked_read(db, p, read); return {'status': 'ready'}
    from .business_data_reader import parse_dataset, fetch_json
    try:
        source, config = _read_authority(db, p, read)
        attempt = {'read_id': read.id, 'job_id': job.id, 'attempt': job.attempts, 'lease_token': job.lease_token,
            'actor_id': p.user_id, 'source_version': source.version,
            'capability_id': read.capability_id, 'source_hash': read.data['source_hash']}
        prior = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.object_id == read.id, m.Audit.action == READ_ATTEMPT,
            m.Audit.data['lease_token'].as_string() == job.lease_token)))
        if prior: need(len(prior) == 1 and same(prior[0].data, attempt))
        else: audit(db, p, READ_ATTEMPT, read.id, attempt)
        read.status = 'reading'; db.commit()
        if read.evidence_id is None:
            need(read.data['kind'] == 'http_json')
            def before(): _read_authority(db, p, read, attempt); db.commit()
            def received(content, metadata):
                db.refresh(read)
                _read_authority(db, p, read, attempt)
                save_raw(db, p, read, content, metadata['content_type'], {'headers': metadata})
                db.commit()
            fetched = fetch_json({key: config[key] for key in ('endpoint', 'secret_ref') if config.get(key)},
                before_request=before, on_response=received)
            if read.evidence_id is None:
                _read_authority(db, p, read, attempt)
                save_raw(db, p, read, fetched.content, fetched.media_type,
                    {'headers': fetched.headers, 'received_at': fetched.received_at}); db.commit()
        raw = checked_read(db, p, read, parsed=False); db.commit()
        result = parse_dataset(raw, read.data['name'], 'json' if read.data['kind'] == 'http_json' else read.data['kind'])
        _read_authority(db, p, read, attempt)
        need('parsed' not in read.data, '原解析修订已经保存，不能覆盖')
        read.data = {**read.data, 'parsed': result, 'error': None}; read.status = 'ready'; read.version += 1
        audit(db, p, PARSED_AUDIT, read.id, {'raw': _raw_value(read), 'parsed_hash': digest(result)})
        return {'status': 'ready'}
    except Exception as error:
        from .leases import LeaseLost, ExecutionUnauthorized
        if isinstance(error, (LeaseLost, ExecutionUnauthorized)): raise
        db.rollback(); read = scoped(db, Read, read.id, p, True)
        read.status = 'failed'; read.version += 1
        message = str(error.detail) if isinstance(error, HTTPException) else '原件已保留，读取或解析失败；请核对格式、来源与连接后恢复'
        from .business_data_reader import ReaderError
        if isinstance(error, ReaderError): message = str(error)
        read.data = {**read.data, 'error': message}
        audit(db, p, 'business_data.read_failed', read.id, {'error': message, 'raw_saved': bool(read.evidence_id)})
        return {'status': 'failed', 'error': message}


def read_view(read):
    sets = []
    for group in read.data.get('parsed', {}).get('record_sets', []):
        samples = []
        for record in group['records'][:5]:
            cells = []
            for field in group['fields'][:30]:
                value = pointer(record['value'], field['path'], missing=None)
                cells.append({'path': field['path'], 'label': field['label'], 'value_text': display(value), 'type': value_type(value)})
            samples.append({'index': record['index'], 'location': record['location'], 'cells': cells})
        sets.append({key: group[key] for key in ('key', 'label', 'count', 'fields')} | {'samples': samples})
    return {'id': read.id, 'version': read.version, 'source_id': read.source_id, 'status': read.status,
        'name': read.data['name'], 'error': read.data.get('error'), 'warnings': read.data.get('parsed', {}).get('warnings', []),
        'record_sets': sets, 'raw_sha256': read.data.get('raw_sha256'), 'can_retry': read.status == 'failed',
        'raw_saved': bool(read.evidence_id), 'created_at': read.created_at.isoformat()}


MISSING = object()


def same(left, right):
    if left is MISSING or right is MISSING: return left is right
    return digest(left) == digest(right)


def merge_three_way(baseline, current, incoming):
    conflicts = []
    after = copy.deepcopy(current)
    for field, fresh in incoming.items():
        prior = baseline.get(field, MISSING); actual = current.get(field, MISSING)
        if not same(actual, prior) and not same(fresh, prior) and not same(fresh, actual):
            conflicts.append(field)
        elif same(actual, prior) or not same(fresh, prior):
            after[field] = copy.deepcopy(fresh)
    return after, conflicts


def pointer(value, path, *, missing=MISSING):
    need(type(path) is str and (path == '' or path.startswith('/')), '来源字段选择器无效')
    try:
        for part in path.split('/')[1:] if path else []:
            part = part.replace('~1', '/').replace('~0', '~')
            value = value[part] if type(value) is dict else value[int(part)]
        return copy.deepcopy(value)
    except (KeyError, IndexError, TypeError, ValueError): return missing


def value_type(value):
    if value is None: return 'null'
    if type(value) is bool: return 'boolean'
    if type(value) is int: return 'integer'
    if type(value) is float: return 'number'
    return {str: 'string', dict: 'object', list: 'array'}.get(type(value), 'unknown')


def display(value):
    if value is MISSING: return '未提供'
    if value is None: return '空值'
    if type(value) is bool: return '是' if value else '否'
    if type(value) in (dict, list): return '结构化内容'
    return str(value)[:500]


def convert(value, conversion):
    need(value is not MISSING, '来源字段缺失')
    if conversion == 'identity': return value
    if value is None: return None
    if conversion == 'string':
        need(type(value) in (str, int, float, bool))
        return str(value) if type(value) is not bool else 'true' if value else 'false'
    if conversion in {'integer', 'number'}:
        need(type(value) in (str, int, float) and type(value) is not bool, '数值不能由布尔、公式或复合内容代替')
        try:
            if conversion == 'integer':
                if type(value) is str:
                    need(value.strip() == value and value not in {'', '+', '-'} and value.lstrip('-').isdigit(), '整数必须精确，不能截断小数')
                result = int(value); need(type(value) is not float or value == result, '整数不能截断小数')
            else: result = float(value)
            need(type(result) is int or math.isfinite(result), '数值必须有限')
            return result
        except (ValueError, OverflowError): raise HTTPException(422, '数值转换失败，未补造零') from None
    if conversion == 'boolean':
        if type(value) is bool: return value
        need(type(value) is str and value in {'true', 'false'}, '布尔只接受原布尔或明确true/false')
        return value == 'true'
    if conversion == 'date':
        need(type(value) is str and date.fromisoformat(value).isoformat() == value, '日期只接受原ISO日期，不推断时区')
        return value
    raise HTTPException(422, '未批准的字段转换')


def options(db, p, identity, *, lock=False):
    from .instance_api import active
    instance, release = active(db, p, identity, lock)
    snapshot = release.data['snapshot']; catalog = {item['key']: item for item in snapshot.get('object_types', [])}
    types = []
    for key, schema in snapshot['entity_schemas'].items():
        title = catalog.get(key, {})
        labels = {item['key']: item for item in title.get('fields', [])}
        fields = [{'key': name, 'label': labels.get(name, {}).get('label') or spec.get('title') or name,
            'type': spec.get('type', 'unknown'), 'format': spec.get('format'), 'unit': spec.get('unit'),
            'required': name in schema.get('required', [])}
            for name, spec in schema.get('properties', {}).items()]
        types.append({'key': key, 'label': title.get('name', key), 'schema_hash': digest(schema), 'fields': fields})
    return {'instance_id': instance.id, 'release_id': release.id, 'object_types': types}, instance, release


def checked_binding(db, p, binding, *, current=False):
    value = binding.data
    need(anchored(db, p, BINDING_AUDIT, binding.id, {'binding_id': binding.id, 'hash': digest(value)}).actor_id == value['created_by'])
    need(value['source_id'] == binding.source_id and value['instance_id'] == binding.instance_id
        and value['release_id'] == binding.release_id and value['object_type'] == binding.object_type)
    if current:
        _, instance, release = options(db, p, binding.instance_id, lock=True)
        source = scoped(db, Source, binding.source_id, p, True); cap, _ = checked_source(db, p, source)
        need(release.id == binding.release_id and digest(release.data['snapshot']['entity_schemas'][binding.object_type]) == value['schema_hash']
            and cap.content_hash == value['source_hash'],
            '原实例版本、映射或连接已变化；请重新预览，不迁移旧批准')
        return instance, release, source


def bind(db, p, body):
    prior = request_prior(db, p, body, 'bind')
    if prior is not None: return prior
    current_actor(db, p); choices, instance, release = options(db, p, body['instance_id'], lock=True)
    need(release.id == body['release_id'], '实例版本已变化，请核对目标字段')
    source = scoped(db, Source, body['source_id'], p, True)
    check_version(source, body['source_expected_version']); cap, _ = checked_source(db, p, source)
    read = scoped(db, Read, body['read_id'], p); checked_read(db, p, read)
    need(read.source_id == source.id and read.capability_id == cap.id and read.status == 'ready')
    schema = release.data['snapshot']['entity_schemas'].get(body['object_type'])
    need(schema is not None and digest(schema) == body['schema_hash'])
    selected = next((item for item in read.data['parsed']['record_sets'] if item['key'] == body['record_set']), None)
    need(selected is not None, '请选择原读取的一组记录')
    paths = {field['path'] for field in selected['fields']}
    need(body['external_id_path'] in paths and bool(body['fields']), '必须明确稳定业务编号及字段映射')
    targets = [field['target'] for field in body['fields']]
    need(len(targets) == len(set(targets)) and all(field['source'] in paths
        and field['target'] in schema.get('properties', {})
        and field['conversion'] in {'identity', 'string', 'integer', 'number', 'boolean', 'date'} for field in body['fields']),
        '来源字段、目标字段或转换不在本次明确范围内')
    row = Binding(workspace_id=p.workspace_id, source_id=source.id, instance_id=instance.id,
        release_id=release.id, object_type=body['object_type'], data={**copy.deepcopy(body),
            'source_hash': cap.content_hash, 'schema': copy.deepcopy(schema), 'configuration_hash': release.configuration_hash,
            'created_by': p.user_id})
    db.add(row); db.flush(); audit(db, p, BINDING_AUDIT, row.id, {'binding_id': row.id, 'hash': digest(row.data)})
    return command_saved(db, p, body, binding_view(row))


def binding_view(row):
    return {'id': row.id, 'version': row.version, **{key: copy.deepcopy(row.data[key]) for key in (
        'source_id', 'read_id', 'instance_id', 'release_id', 'object_type', 'schema_hash', 'record_set', 'external_id_path', 'fields')}}


def external_key(record, path):
    value = pointer(record, path)
    need(type(value) in (str, int) and type(value) is not bool, '业务编号必须是非空文字或精确整数，不能用行号替代')
    result = str(value)
    need(result.strip() == result and bool(result) and len(result) <= 200, '业务编号为空、含多余空白或超过200字')
    return result


def mapped(record, binding):
    return {field['target']: convert(pointer(record, field['source']), field['conversion']) for field in binding.data['fields']}


def validate_target(schema, attributes):
    need(not list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(attributes)),
        '字段缺失、类型、日期或格式不符合当前对象定义；未填补默认值')


def read_order(read): return [read.created_at.astimezone(timezone.utc).isoformat(), read.id]


def preview_row(db, p, batch, binding, read, record, instance, release, duplicates):
    value = {'record_index': record['index'], 'location': record['location'], 'raw_value_hash': digest(record['value']),
        'external_key': '', 'mapped': {}, 'entity_id': None, 'target_version': None, 'before': None,
        'after': None, 'link_id': None, 'link_version': None, 'reasons': [], 'differences': [],
        'duplicate_count': duplicates}
    try:
        key = external_key(record['value'], binding.data['external_id_path']); value['external_key'] = key
        incoming = mapped(record['value'], binding); value['mapped'] = incoming
        link = db.scalar(select(Link).where(Link.workspace_id == p.workspace_id, Link.source_id == batch.source_id,
            Link.instance_id == binding.instance_id, Link.object_type == binding.object_type, Link.external_key == key))
        target = scoped(db, m.Entity, link.entity_id, p) if link else db.scalar(select(m.Entity).where(
            m.Entity.workspace_id == p.workspace_id, m.Entity.namespace == instance.namespace,
            m.Entity.entity_type == binding.object_type, m.Entity.external_id == key))
        if target and link is None:
            value.update(status='conflict', entity_id=target.id, reasons=['此业务编号已由人工或另一来源建立，不能自动接管'])
            return value
        if target:
            from .business_data_receipts import verify_link_snapshot
            verify_link_snapshot(db, p, link)
            from .entity_release_compat import require_entity
            require_entity(db, p, instance, release, target)
            value.update(entity_id=target.id, target_version=target.version, before=copy.deepcopy(target.data),
                link_id=link.id, link_version=link.version)
            last = link.data['mapped']
            after, conflicts = merge_three_way(last, target.data, incoming)
            if conflicts:
                value.update(status='conflict', reasons=['人工与来源同时修改同一字段：' + '、'.join(conflicts)])
                return value
            if read_order(read) < link.data['read_order'] and not same(incoming, last):
                value.update(status='conflict', reasons=['此原读取早于已导入版本，不能回滚较新业务资料'])
                return value
            validate_target(binding.data['schema'], after)
            value['after'] = after; value['status'] = 'unchanged' if same(after, target.data) else 'updated'
        else:
            validate_target(binding.data['schema'], incoming)
            value.update(status='new', after=incoming)
        for field, after in value['after'].items():
            before = (value['before'] or {}).get(field, MISSING)
            if not same(before, after): value['differences'].append({'label': binding.data['schema']['properties'][field].get('title') or field,
                'before_text': display(before), 'after_text': display(after)})
    except (HTTPException, ValueError, TypeError, KeyError, OverflowError) as error:
        value['status'] = 'invalid'; value['reasons'] = [str(error.detail) if isinstance(error, HTTPException) else '此行值或转换无法核对']
    return value


def start_preview(db, p, identity, body):
    prior = request_prior(db, p, body, 'preview:' + identity)
    if prior is not None: return prior
    current_actor(db, p); binding = scoped(db, Binding, identity, p)
    checked_binding(db, p, binding, current=True)
    read = scoped(db, Read, body['read_id'], p); checked_read(db, p, read)
    need(read.status == 'ready' and read.source_id == binding.source_id
        and read.data['source_hash'] == binding.data['source_hash'])
    row = Import(workspace_id=p.workspace_id, source_id=binding.source_id, binding_id=binding.id,
        read_id=read.id, actor_id=p.user_id, data={'error': None, 'request': copy.deepcopy(body), 'confirmed': False})
    db.add(row); db.flush()
    job = queue(db, p, 'preview_business_data', row.id, 'business-data-preview:' + row.id)
    row.data = {**row.data, 'job_id': job.id}
    audit(db, p, IMPORT_ORIGIN, row.id, import_origin(row))
    return command_saved(db, p, body, import_view(db, p, row))


def process_preview(db, p, job):
    worker_job(db, p, job, 'preview_business_data')
    batch = scoped(db, Import, job.data['business_data_id'], p)
    need(batch.data['job_id'] == job.id)
    worker_actor(db, p, job, batch.actor_id)
    need(anchored(db, p, IMPORT_ORIGIN, batch.id, import_origin(batch)).actor_id == batch.actor_id)
    if batch.status != 'queued': return {'status': batch.status}
    current_actor(db, p); binding = scoped(db, Binding, batch.binding_id, p)
    instance, release, _ = checked_binding(db, p, binding, current=True)
    batch = scoped(db, Import, batch.id, p, True)
    if batch.status != 'queued': return {'status': batch.status}
    read = scoped(db, Read, batch.read_id, p); checked_read(db, p, read)
    selected = next(item for item in read.data['parsed']['record_sets'] if item['key'] == binding.data['record_set'])
    grouped = {}; invalid = []
    for record in selected['records']:
        try: key = external_key(record['value'], binding.data['external_id_path'])
        except (HTTPException, ValueError, TypeError): invalid.append(record); continue
        grouped.setdefault(key, []).append(record)
    records = [(items[0], len(items), len({digest(item['value']) for item in items}) > 1) for items in grouped.values()]
    records += [(record, 1, False) for record in invalid]
    pins = []
    for ordinal, (record, count, collision) in enumerate(sorted(records, key=lambda item: item[0]['index'])):
        value = preview_row(db, p, batch, binding, read, record, instance, release, count)
        value['duplicate_locations'] = [item['location'] for item in grouped.get(value['external_key'], [record])]
        if collision: value.update(status='conflict', reasons=['不同内容使用同一业务编号，未采用最后一行覆盖'])
        row = ImportRow(workspace_id=p.workspace_id, import_id=batch.id, ordinal=ordinal,
            status=value['status'], preview=value, data={})
        db.add(row); db.flush()
        pin = {'row_id': row.id, 'ordinal': ordinal, 'preview_hash': digest(value)}
        audit(db, p, ROW_AUDIT, row.id, pin); pins.append(pin)
    frozen = {'binding_hash': digest(binding.data), 'read_id': read.id, 'raw_sha256': read.data['raw_sha256'],
        'parsed_hash': digest(read.data['parsed']), 'release_id': release.id, 'configuration_hash': release.configuration_hash,
        'source_hash': binding.data['source_hash'], 'rows': pins}
    batch.data = {**batch.data, 'preview': frozen, 'confirm_hash': digest(frozen), 'error': None}
    batch.status = 'previewed'; batch.version += 1
    audit(db, p, PREVIEW_AUDIT, batch.id, {'preview': frozen, 'confirm_hash': digest(frozen)})
    return {'status': 'previewed'}


def checked_preview(db, p, batch, binding, read):
    need(anchored(db, p, IMPORT_ORIGIN, batch.id, import_origin(batch)).actor_id == batch.actor_id)
    frozen = batch.data['preview']
    anchored(db, p, PREVIEW_AUDIT, batch.id, {'preview': frozen, 'confirm_hash': digest(frozen)})
    need(batch.data['confirm_hash'] == digest(frozen) and frozen['binding_hash'] == digest(binding.data)
        and frozen['read_id'] == read.id and frozen['raw_sha256'] == read.data['raw_sha256']
        and frozen['parsed_hash'] == digest(read.data['parsed']))
    return frozen


def checked_row(db, p, row):
    pin = {'row_id': row.id, 'ordinal': row.ordinal, 'preview_hash': digest(row.preview)}
    anchored(db, p, ROW_AUDIT, row.id, pin)
    return pin


@projection
def counts(db, p, batch):
    rows = list(db.scalars(select(ImportRow).where(ImportRow.workspace_id == p.workspace_id, ImportRow.import_id == batch.id)))
    result = {key: 0 for key in ('new', 'updated', 'unchanged', 'conflict', 'invalid', 'imported', 'data_received', 'failed', 'cancelled')}
    for row in rows:
        try:
            checked_row(db, p, row)
            if row.status == 'imported':
                from .business_data_receipts import verify_import_event
                verify_import_event(db, p, scoped(db, m.Event, row.data['event_id'], p))
        except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError, StopIteration):
            result['failed'] += 1
            continue
        if row.preview['status'] in result: result[row.preview['status']] += 1
        if row.status == 'imported': result['imported'] += 1
        if row.status in {'failed', 'cancelled'}: result[row.status] += 1
        if row.data.get('event_id'):
            event = scoped(db, m.Event, row.data['event_id'], p)
            from .business_data_receipts import delivery_view
            if delivery_view(db, p, event)['verified']: result['data_received'] += 1
    return result


@projection
def row_view(db, p, row):
    integrity_error = False
    try:
        checked_row(db, p, row)
        if row.status == 'imported':
            from .business_data_receipts import verify_import_event
            verify_import_event(db, p, scoped(db, m.Event, row.data['event_id'], p))
    except (HTTPException, KeyError, ValueError, TypeError, AttributeError, OSError, StopIteration):
        integrity_error = True
    delivery = None
    if row.data.get('event_id'):
        event = scoped(db, m.Event, row.data['event_id'], p)
        from .business_data_receipts import delivery_view
        delivery = delivery_view(db, p, event)
    batch = scoped(db, Import, row.import_id, p); binding = scoped(db, Binding, batch.binding_id, p)
    values = row.preview.get('after') or row.preview.get('mapped') or {}
    names = [values.get(key) for key in binding.data['schema'].get('required', [])
        if binding.data['schema'].get('properties', {}).get(key, {}).get('type') == 'string']
    name = next((value for value in names if type(value) is str and value.strip()), row.preview['external_key'] or '待核对记录')
    return {'id': row.id, 'external_key': row.preview['external_key'], 'name': name[:200],
        'status': 'failed' if integrity_error else row.status, 'integrity_error': integrity_error,
        'differences': [] if integrity_error else row.preview['differences'],
        'reasons': ['原预览、对象写入或独立来源回执无法核对；未重新导入'] if integrity_error else row.data.get('reasons', row.preview['reasons']), 'location': row.preview['location'],
        'duplicate_count': row.preview['duplicate_count'], 'entity_id': row.data.get('entity_id', row.preview['entity_id']),
        'data_received': bool(not integrity_error and delivery and delivery['verified']), 'data_delivery': delivery}


@projection
def import_view(db, p, batch):
    binding = scoped(db, Binding, batch.binding_id, p)
    rows = list(db.scalars(select(ImportRow).where(ImportRow.workspace_id == p.workspace_id,
        ImportRow.import_id == batch.id).order_by(ImportRow.ordinal).limit(20)))
    totals = counts(db, p, batch)
    contradictory = batch.status == 'completed' and any(row.status != 'imported' for row in db.scalars(
        select(ImportRow).where(ImportRow.workspace_id == p.workspace_id, ImportRow.import_id == batch.id)))
    effective_status = 'failed' if batch.status == 'completed' and (totals['failed'] or contradictory) else batch.status
    return {'id': batch.id, 'version': batch.version, 'source_id': batch.source_id, 'read_id': batch.read_id,
        'binding_id': batch.binding_id, 'instance_id': binding.instance_id, 'release_id': binding.release_id,
        'status': effective_status, 'name': '业务数据导入', 'error': batch.data.get('error') or ('原导入回执无法核对' if effective_status != batch.status else None),
        'confirm_hash': batch.data.get('confirm_hash'), 'counts': totals,
        'can_confirm': batch.status == 'previewed' and not totals['failed'],
        'can_resume': batch.status in {'failed', 'partial', 'cancelled'} and (not batch.data.get('confirmed') or bool(totals['failed'] or totals['cancelled'])),
        'rows': [row_view(db, p, row) for row in rows], 'created_at': batch.created_at.isoformat()}


def is_managed_manifest(value):
    return type(value) is dict and (value.get('namespace') == NS
        or type(value.get('execution')) is dict and value['execution'].get('adapter') == ADAPTER
        or type(value.get('specification')) is dict and value['specification'].get('schema') == 'business-data-source-v204')


def validate_managed_capability(db, p, cap):
    identity = cap.data.get('specification', {}).get('source_id')
    source = scoped(db, Source, identity, p)
    actual, _ = checked_source(db, p, source, active=False, capability_id=cap.id)
    return actual


def confirm_import(db, p, identity, body):
    prior = request_prior(db, p, body, 'confirm:' + identity)
    if prior is not None: return prior
    current_actor(db, p); batch = scoped(db, Import, identity, p)
    binding = scoped(db, Binding, batch.binding_id, p); checked_binding(db, p, binding, current=True)
    batch = scoped(db, Import, identity, p, True); check_version(batch, body['expected_version'])
    read = scoped(db, Read, batch.read_id, p); checked_read(db, p, read)
    checked_preview(db, p, batch, binding, read)
    need(batch.status == 'previewed' and body['confirm_hash'] == batch.data['confirm_hash'])
    all_counts = counts(db, p, batch)
    need(body['accept_partial'] or not (all_counts['conflict'] or all_counts['invalid']),
        '此批仍有冲突或无效行；须明确选择部分导入，原行不会丢弃')
    job = queue(db, p, 'import_business_data', batch.id, 'business-data-import:' + batch.id)
    confirmation = {'body': copy.deepcopy(body), 'actor_id': p.user_id,
        'preview_hash': batch.data['confirm_hash'], 'job_id': job.id}
    audit(db, p, 'business_data.confirmed_v204', batch.id, confirmation)
    batch.data = {**batch.data, 'confirmed': True, 'confirmation': confirmation, 'error': None}
    batch.status = 'queued_import'; batch.version += 1
    batch.data = {**batch.data, 'import_job_id': job.id}
    return command_saved(db, p, body, import_view(db, p, batch))


def _entity_event(db, p, entity):
    rows = list(db.scalars(select(m.EntityEvent).where(m.EntityEvent.workspace_id == p.workspace_id,
        m.EntityEvent.entity_id == entity.id, m.EntityEvent.data['entity_version'].as_integer() == entity.version)))
    need(len(rows) == 1 and same(rows[0].data.get('after'), entity.data), '对象写入事件与实际版本不一致')
    return rows[0]


def _write_row(db, p, batch, binding, read, row, instance, release):
    checked_row(db, p, row)
    frozen_pin = next((pin for pin in batch.data['preview']['rows'] if pin['row_id'] == row.id), None)
    need(frozen_pin == {'row_id': row.id, 'ordinal': row.ordinal, 'preview_hash': digest(row.preview)})
    if row.status == 'imported':
        from .business_data_receipts import verify_import_event
        verify_import_event(db, p, scoped(db, m.Event, row.data['event_id'], p))
        return
    if row.status in {'conflict', 'invalid'}: return
    value = row.preview
    need(value['status'] in {'new', 'updated', 'unchanged'})
    key = int(digest({'business_data_target': p.workspace_id, 'instance': instance.id,
        'type': binding.object_type, 'external_key': value['external_key']})[:15], 16)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
    if value['link_id']:
        link = scoped(db, Link, value['link_id'], p, True)
        need(link.version == value['link_version'], '此记录已被另一批更新，须重新预览')
        entity = scoped(db, m.Entity, value['entity_id'], p, True)
        need(entity.version == value['target_version'] and same(entity.data, value['before']),
            '预览后的对象已被修改；原人工值保留，须重新预览')
        from .entity_release_compat import require_entity
        require_entity(db, p, instance, release, entity)
        if value['status'] == 'updated':
            entity = update_entity(db, p, entity.id, copy.deepcopy(value['after']), value['target_version'], '明确确认的业务数据导入')
    else:
        need(db.scalar(select(m.Entity.id).where(m.Entity.workspace_id == p.workspace_id,
            m.Entity.namespace == instance.namespace, m.Entity.entity_type == binding.object_type,
            m.Entity.external_id == value['external_key'])) is None, '此业务编号已建立对象，不能自动接管')
        entity = create_entity(db, p, EntityCreate(package_id=release.package_id, entity_type=binding.object_type,
            external_id=value['external_key'], attributes=copy.deepcopy(value['after'])))
        link = Link(workspace_id=p.workspace_id, source_id=batch.source_id, instance_id=instance.id,
            object_type=binding.object_type, external_key=value['external_key'], entity_id=entity.id, data={})
        db.add(link); db.flush()
    event = _entity_event(db, p, entity)
    link.data = {'mapped': copy.deepcopy(value['mapped']), 'read_order': max(read_order(read), link.data.get('read_order', read_order(read))),
        'read_id': read.id, 'import_row_id': row.id, 'entity_version': entity.version,
        'entity_hash': digest(entity.data), 'entity_event_id': event.id}
    link.version += 1
    receipt = {'schema': 'business-data-row-receipt-v204', 'row_id': row.id, 'import_id': batch.id,
        'import_actor_id': p.user_id, 'entity_schema_capability_id': entity.schema_capability_id,
        'binding_id': binding.id, 'read_id': read.id, 'source_id': batch.source_id,
        'instance_id': instance.id, 'release_id': release.id, 'object_type': binding.object_type,
        'configuration_hash': release.configuration_hash, 'schema_hash': binding.data['schema_hash'],
        'external_key': value['external_key'], 'raw_sha256': read.data['raw_sha256'],
        'binding_hash': digest(binding.data), 'preview_hash': digest(value), 'confirm_hash': batch.data['confirm_hash'],
        'entity_id': entity.id, 'entity_version': entity.version, 'attributes': copy.deepcopy(entity.data),
        'entity_event_id': event.id, 'entity_event': copy.deepcopy(event.data), 'entity_event_actor': event.actor_id,
        'link_id': link.id, 'link_version': link.version, 'link_snapshot': copy.deepcopy(link.data),
        'operation': value['status'], 'provider_time_verified': False, 'physical_state_verified': False}
    message = m.Event(workspace_id=p.workspace_id, kind='business_data.imported',
        idempotency_key='business-data-imported:' + row.id,
        data={'schema': 'business-data-import-event-v204', 'receipt': receipt})
    db.add(message); db.flush()
    audit(db, p, IMPORTED_AUDIT, row.id, {'event_id': message.id, 'receipt': receipt})
    row.data = {**row.data, 'entity_id': entity.id, 'event_id': message.id, 'receipt': receipt}
    row.status = 'imported'; row.version += 1


def process_import(db, p, job):
    worker_job(db, p, job, 'import_business_data')
    batch = scoped(db, Import, job.data['business_data_id'], p)
    need(batch.data.get('confirmed') is True)
    anchored(db, p, 'business_data.confirmed_v204', batch.id, batch.data['confirmation'])
    need(batch.data['import_job_id'] == job.id == batch.data['confirmation']['job_id'])
    worker_actor(db, p, job, batch.data['confirmation']['actor_id'])
    verify_committed_rows(db, p, batch)
    if batch.status in {'completed', 'cancelled'}: return {'status': batch.status}
    while True:
        current_actor(db, p)
        binding = scoped(db, Binding, batch.binding_id, p)
        instance, release, _ = checked_binding(db, p, binding, current=True)
        batch = scoped(db, Import, batch.id, p, True)
        if batch.status == 'cancelled': return {'status': 'cancelled'}
        read = scoped(db, Read, batch.read_id, p); checked_read(db, p, read)
        checked_preview(db, p, batch, binding, read)
        verify_committed_rows(db, p, batch)
        rows = list(db.scalars(select(ImportRow).where(ImportRow.workspace_id == p.workspace_id,
            ImportRow.import_id == batch.id, ImportRow.status.in_(['new', 'updated', 'unchanged']))
            .order_by(ImportRow.ordinal).with_for_update().limit(25)))
        if not rows:
            totals = counts(db, p, batch)
            batch.status = 'partial' if totals['failed'] or totals['conflict'] or totals['invalid'] or totals['cancelled'] else 'completed'
            batch.version += 1
            return {'status': batch.status}
        batch.status = 'importing'
        for row in rows:
            try:
                with db.begin_nested(): _write_row(db, p, batch, binding, read, row, instance, release)
            except (HTTPException, ValueError, TypeError, KeyError) as error:
                row.status = 'failed'; row.version += 1
                row.data = {**row.data, 'reasons': [str(error.detail) if isinstance(error, HTTPException) else '此行原件、目标或写入依据无法核对']}
                audit(db, p, 'business_data.row_failed', row.id, {'preview_hash': digest(row.preview), 'reasons': row.data['reasons']})
        db.commit()  # row Entity + link + Event + independent Audit become visible together
        db.refresh(batch)


def retry_read(db, p, identity, body):
    prior = request_prior(db, p, body, 'retry_read:' + identity)
    if prior is not None: return prior
    read = scoped(db, Read, identity, p)
    job = scoped(db, m.Job, read.data['job_id'], p, True)
    need(job.kind == 'read_business_data' and job.data['business_data_id'] == read.id)
    current_actor(db, p)
    _read_authority(db, p, read); read = scoped(db, Read, identity, p, True)
    check_version(read, body['expected_version']); need(read.status == 'failed')
    need(read.data['job_id'] == job.id)
    need(job.status == 'failed', '原任务仍在运行，不能重复读取')
    read.status = 'queued'; read.version += 1; read.data = {**read.data, 'error': None}
    job.status = 'queued'; job.available_at = m.now(); job.lease_token = None; job.lease_until = None
    job.data = {**job.data, 'actor_id': p.user_id}; job.attempts = 0
    authorize_job(db, p, job, body)
    return command_saved(db, p, body, read_view(read))


def control_import(db, p, identity, body, action):
    prior = request_prior(db, p, body, action + ':' + identity)
    if prior is not None: return prior
    batch = scoped(db, Import, identity, p)
    original_job = None
    if action == 'resume':
        original_job = scoped(db, m.Job, batch.data['import_job_id'] if batch.data.get('confirmed') else batch.data['job_id'], p, True)
        need(original_job.data['business_data_id'] == batch.id
            and original_job.kind == ('import_business_data' if batch.data.get('confirmed') else 'preview_business_data'))
    current_actor(db, p)
    binding = scoped(db, Binding, batch.binding_id, p)
    if action == 'resume': checked_binding(db, p, binding, current=True)
    batch = scoped(db, Import, identity, p, True); check_version(batch, body['expected_version'])
    need(batch.status != 'completed', '已完成批次不能改为取消或重新导入')
    rows = list(db.scalars(select(ImportRow).where(ImportRow.workspace_id == p.workspace_id,
        ImportRow.import_id == batch.id).order_by(ImportRow.ordinal).with_for_update()))
    if action == 'cancel':
        for row in rows:
            if row.status in {'new', 'updated', 'unchanged', 'failed'}:
                row.status = 'cancelled'; row.version += 1
        batch.status = 'cancelled'
    else:
        need(batch.status in {'failed', 'partial', 'cancelled'})
        for row in rows:
            if row.status in {'failed', 'cancelled'}:
                checked_row(db, p, row)
                row.status = row.preview['status']; row.version += 1
        confirmed = batch.data.get('confirmed') is True
        job = original_job
        need(job.id == (batch.data['import_job_id'] if confirmed else batch.data['job_id']))
        need(job.status not in {'running', 'queued'}, '原任务仍在运行，请查回原批次')
        job.status = 'queued'; job.attempts = 0; job.available_at = m.now(); job.lease_token = None; job.lease_until = None
        job.data = {**job.data, 'actor_id': p.user_id}
        authorize_job(db, p, job, body)
        batch.status = 'queued_import' if confirmed else 'previewed' if batch.data.get('preview') else 'queued'
        if not confirmed and batch.data.get('preview'): job.status = 'succeeded'
    batch.version += 1
    audit(db, p, 'business_data.' + action, batch.id, {'reason': body['reason'], 'version': batch.version})
    return command_saved(db, p, body, import_view(db, p, batch))


def recover_job(db, p, job, reason):
    cls = Read if job.kind == 'read_business_data' else Import
    row = scoped(db, cls, job.data['business_data_id'], p, True)
    if row.status not in {'ready', 'completed', 'cancelled'}:
        row.status = 'failed'; row.version += 1; row.data = {**row.data, 'error': reason}
        audit(db, p, 'business_data.worker_interrupted', row.id, {'job_id': job.id, 'reason': reason})


def worker_job(db, p, job, kind):
    from .leases import ownership
    lease = ownership(db)
    need(isinstance(job, m.Job) and job.workspace_id == p.workspace_id and job.kind == kind
        and job.status == 'running' and job.data['actor_id'] == p.user_id
        and lease.get('job_id') == job.id and lease.get('lease_token') == job.lease_token,
        '后台任务原租约无法核对，未执行读取或写入')


def authorize_job(db, p, job, body):
    value = {'job_id': job.id, 'kind': job.kind, 'business_data_id': job.data['business_data_id'],
        'actor_id': p.user_id, 'body': copy.deepcopy(body), 'request_hash': digest(body)}
    row = m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=JOB_AUTH, object_id=job.id, data=value)
    db.add(row); db.flush()
    job.data = {**job.data, 'business_data_authorization_id': row.id}


def worker_actor(db, p, job, original_actor):
    identity = job.data.get('business_data_authorization_id')
    if identity is None:
        need(p.user_id == original_actor, '执行人变化但没有本次明确恢复批准')
        return
    authorization = scoped(db, m.Audit, identity, p)
    value = authorization.data
    need(authorization.action == JOB_AUTH and authorization.object_id == job.id and authorization.actor_id == p.user_id
        and value['actor_id'] == p.user_id and value['job_id'] == job.id and value['kind'] == job.kind
        and value['business_data_id'] == job.data['business_data_id'] and digest(value['body']) == value['request_hash'])
    command = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == COMMAND,
        m.Audit.data['request_id'].as_string() == value['body']['request_id'])))
    need(len(command) == 1 and command[0].actor_id == p.user_id and same(command[0].data['body'], value['body'])
        and command[0].data['request_hash'] == value['request_hash'])


@projection
def verify_committed_rows(db, p, batch):
    from .business_data_receipts import verify_import_event
    if batch.status == 'completed':
        need(db.scalar(select(ImportRow.id).where(ImportRow.workspace_id == p.workspace_id,
            ImportRow.import_id == batch.id, ImportRow.status != 'imported').limit(1)) is None,
            '原完成状态与逐行导入回执不一致；未重新写入对象')
    after = -1
    while True:
        rows = list(db.scalars(select(ImportRow).where(ImportRow.workspace_id == p.workspace_id,
            ImportRow.import_id == batch.id, ImportRow.status == 'imported', ImportRow.ordinal > after)
            .order_by(ImportRow.ordinal).limit(25)))
        if not rows: break
        for row in rows: verify_import_event(db, p, scoped(db, m.Event, row.data['event_id'], p))
        after = rows[-1].ordinal


def read_origin(read):
    return {'read_id': read.id, 'source_id': read.source_id, 'capability_id': read.capability_id,
        'initial_actor_id': read.actor_id, 'source_hash': read.data['source_hash'],
        'source_version': read.data['source_version'], 'request_hash': read.data['request_hash'],
        'name': read.data['name'], 'kind': read.data['kind'], 'job_id': read.data['job_id']}


def import_origin(batch):
    return {'import_id': batch.id, 'source_id': batch.source_id, 'binding_id': batch.binding_id,
        'read_id': batch.read_id, 'initial_actor_id': batch.actor_id,
        'request_hash': digest(batch.data['request']), 'job_id': batch.data['job_id']}
