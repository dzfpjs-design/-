"""Workspace-scoped, read-only human history from existing stage audits.

This projection never revalidates current execution eligibility or reads raw
files. It does not equate an old failure with the current successful head.
"""
import base64
import binascii
import json
import re
import unicodedata
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from . import business_data as b, models as m
from .business_data_models import BusinessDataRead as Read, BusinessDataImport as Import
from .contracts import digest
from .security import Principal
from .services import scoped

SCHEMA = 'business-data-history-v204'
PAGE_SIZE = 20
READ_ACTIONS = {
    b.READ_ORIGIN: ('已提交读取', '原读取请求已保存，可沿原记录查阅后续处理。', 'request'),
    b.READ_ATTEMPT: ('开始处理读取', '后台按本次批准的连接处理原读取任务。', 'info'),
    b.RAW_AUDIT: ('原始资料已保存', '原抓取已独立保存，后续解析不会替换原件。', 'succeeded'),
    b.PARSED_AUDIT: ('资料解析完成', '已保存本次解析结果，尚未因此写入业务对象。', 'succeeded'),
    'business_data.read_failed': ('读取或解析失败', '本次未完成；已有原件与失败记录保留，核对后可明确恢复。', 'failed'),
    'business_data.worker_interrupted': ('后台处理未完成', '后台中断或处理失败；原记录保留，可核对后恢复。', 'failed'),
}
IMPORT_ACTIONS = {
    b.IMPORT_ORIGIN: ('已提交导入预览', '已保存原映射和目标选择，预览不会写入业务对象。', 'request'),
    b.PREVIEW_AUDIT: ('导入预览已保存', '已保存逐行差异、冲突和无效记录，等待明确确认。', 'succeeded'),
    'business_data.confirmed_v204': ('已明确确认导入', '已保存本次导入批准，按原预览逐行处理。', 'request'),
    'business_data.cancel': ('已取消剩余处理', '未处理记录已停止；已经写入的对象和接收记录仍保留。', 'request'),
    'business_data.resume': ('已请求恢复原批次', '沿原映射和已保存进度继续，已完成记录不会重复导入。', 'request'),
    'business_data.worker_interrupted': ('后台导入未完成', '后台中断或处理失败；已保存的逐行进度仍保留。', 'failed'),
}


def failure_detail(value, fallback):
    """Old safe user-facing errors stay useful without leaking transport data."""
    if type(value) is not str: return fallback
    text = ''.join(char for char in value if not unicodedata.category(char).startswith('C')).strip()
    if any(term in text for term in ('本次读取期间连接', '连接或执行批准已变化')):
        return '读取期间连接版本或执行批准发生变化，未采用本次返回；原读取保留，须明确恢复。'
    if '连接版本已改变' in text:
        return '原读取所用连接配置已改变，未继续处理；请重新核对连接。'
    if any(term in text for term in ('账户已停用', '权限已撤销', '授权已撤销', '执行人变化')):
        return '执行账户或工作区批准已变化，本次处理受阻；原记录保留。'
    sensitive = (r'(?i)(?:[a-z][a-z0-9+.-]*://|(?:secret|password|passwd|token|bearer|authorization|'
        r'nonce|lease)[\s_:=]|GA_CONNECTOR_SECRET_|[A-Z]:[\\/]|(?:^|\s)[/\\][^\s]+|'
        r'[a-f0-9]{64}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}|[{}])')
    if not text or re.search(sensitive, text):
        return '本次处理失败；原错误含连接或私有信息，已在此隐藏，请由管理员核对原受限记录。'
    return text[:1000]


def _stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None: raise ValueError()
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def _encode(workspace_id, subject_type, subject_id, audit):
    value = {'v': 1, 'workspace': workspace_id, 'type': subject_type, 'subject': subject_id,
        'at': _stamp(audit.created_at), 'id': audit.id}
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':'), sort_keys=True).encode()).decode().rstrip('=')


def _decode(cursor, workspace_id, subject_type, subject_id):
    try:
        if type(cursor) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,768}', cursor): raise ValueError()
        raw = base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True)
        if base64.urlsafe_b64encode(raw).decode().rstrip('=') != cursor: raise ValueError()
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value: raise ValueError()
                value[key] = item
            return value
        value = json.loads(raw, object_pairs_hook=pairs)
        if type(value) is not dict or set(value) != {'v', 'workspace', 'type', 'subject', 'at', 'id'}: raise ValueError()
        if (type(value['v']) is not int or value['v'] != 1 or value['workspace'] != workspace_id
                or value['type'] != subject_type or value['subject'] != subject_id): raise ValueError()
        if type(value['id']) is not str or str(UUID(value['id'])) != value['id']: raise ValueError()
        at = datetime.fromisoformat(value['at'])
        if _stamp(at) != value['at']: raise ValueError()
        return at, value['id']
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, OverflowError):
        raise HTTPException(422, '历史分页依据无效，请从最新记录重新查看') from None


def _job(db, p, identity, kind, subject, original_actor, key):
    job = scoped(db, m.Job, identity, p)
    b.need(job.kind == kind and job.data.get('business_data_id') == subject.id
        and job.idempotency_key == key, '原后台任务来源无法核对')
    actor = job.data.get('actor_id')
    b.need(type(actor) is str)
    b.worker_actor(db, Principal(actor, p.workspace_id, 'viewer', 'business-data-history'), job, original_actor)
    return job


def _jobs(db, p, subject_type, subject):
    """Only independently anchored original jobs may supply continuation data."""
    if subject_type == 'read':
        b.need(b.anchored(db, p, b.READ_ORIGIN, subject.id, b.read_origin(subject)).actor_id == subject.actor_id)
        return [_job(db, p, subject.data['job_id'], 'read_business_data', subject, subject.actor_id,
            'business-data-read:' + subject.id)]
    b.need(b.anchored(db, p, b.IMPORT_ORIGIN, subject.id, b.import_origin(subject)).actor_id == subject.actor_id)
    jobs = [_job(db, p, subject.data['job_id'], 'preview_business_data', subject, subject.actor_id,
        'business-data-preview:' + subject.id)]
    confirmations = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.object_id == subject.id, m.Audit.action == 'business_data.confirmed_v204')))
    if subject.data.get('confirmed') or confirmations:
        value = subject.data['confirmation']
        b.need(subject.data.get('confirmed') is True and len(confirmations) == 1
            and b.same(confirmations[0].data, value) and confirmations[0].actor_id == value['actor_id']
            and value['job_id'] == subject.data['import_job_id'] and value['preview_hash'] == subject.data['confirm_hash'])
        jobs.append(_job(db, p, value['job_id'], 'import_business_data', subject, value['actor_id'],
            'business-data-import:' + subject.id))
    return jobs


def _authorization(db, p, audit, subject_type, subject, job_ids):
    try:
        value = audit.data; body = value['body']
        expected_scope = ('retry_read:' if subject_type == 'read' else 'resume:') + subject.id
        b.need(audit.object_id in job_ids and value['job_id'] == audit.object_id
            and value['business_data_id'] == subject.id and value['actor_id'] == audit.actor_id
            and value['kind'] in ({'read_business_data'} if subject_type == 'read' else {'preview_business_data', 'import_business_data'})
            and digest(body) == value['request_hash'])
        commands = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == b.COMMAND, m.Audit.data['request_id'].as_string() == body['request_id'])))
        b.need(len(commands) == 1 and commands[0].actor_id == audit.actor_id
            and commands[0].data.get('scope') == expected_scope and b.same(commands[0].data['body'], body)
            and commands[0].data['request_hash'] == value['request_hash'])
        return True
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError): return False


def history(db, p, subject_type, identity, cursor=None):
    if subject_type not in {'read', 'import'}: raise HTTPException(404, '记录不存在')
    with db.no_autoflush:
        subject = scoped(db, Read if subject_type == 'read' else Import, identity, p)
        actions = READ_ACTIONS if subject_type == 'read' else IMPORT_ACTIONS
        try:
            jobs = _jobs(db, p, subject_type, subject)
            recoveries = [job.data.get('lease_recoveries') for job in jobs]
            valid = all(type(count) is int and 0 <= count <= 1000000 for count in recoveries)
            continuation = {'count': sum(recoveries) if valid else None,
                'notice': None if valid else '原任务尚无可核对的后台续接次数记录。'}
        except (HTTPException, KeyError, TypeError, ValueError, AttributeError):
            jobs = []
            continuation = {'count': None, 'notice': '原后台任务与独立来源依据无法完整核对；续接次数未知。'}
        job_ids = {job.id for job in jobs}
        visible = or_(and_(m.Audit.object_id == subject.id, m.Audit.action.in_(actions)),
            and_(m.Audit.object_id.in_(job_ids), m.Audit.action == b.JOB_AUTH))
        query = select(m.Audit).where(m.Audit.workspace_id == p.workspace_id, visible)
        if cursor is not None:
            at, audit_id = _decode(cursor, p.workspace_id, subject_type, subject.id)
            if db.scalar(query.where(m.Audit.id == audit_id, m.Audit.created_at == at)) is None:
                raise HTTPException(422, '历史分页依据不属于此记录，请从最新记录重新查看')
            query = query.where(or_(m.Audit.created_at < at, and_(m.Audit.created_at == at, m.Audit.id < audit_id)))
        rows = list(db.scalars(query.order_by(m.Audit.created_at.desc(), m.Audit.id.desc()).limit(PAGE_SIZE + 1)))
        entries = []
        for audit in rows[:PAGE_SIZE]:
            if audit.action == b.JOB_AUTH:
                valid = _authorization(db, p, audit, subject_type, subject, job_ids)
                label, detail, status = (('已明确重试原读取' if subject_type == 'read' else '已批准恢复原批次'),
                    '沿已保存的原记录继续处理，原失败记录仍保留。', 'request') if valid else (
                    '恢复请求依据待核对', '此历史恢复请求与独立批准无法核对，未据此确认执行。', 'failed')
            else: label, detail, status = actions[audit.action]
            if audit.action == 'business_data.read_failed': detail = failure_detail(audit.data.get('error'), detail)
            elif audit.action == 'business_data.worker_interrupted': detail = failure_detail(audit.data.get('reason'), detail)
            user = db.get(m.User, audit.actor_id)
            actor = ''.join(char for char in user.username if ord(char) >= 32)[:100] if user else '原记录操作人'
            entries.append({'id': audit.id, 'at': _stamp(audit.created_at), 'actor': actor,
                'label': label, 'detail': detail, 'status': status})
        return {'schema': SCHEMA, 'subject_type': subject_type, 'subject_id': subject.id, 'entries': entries,
            'next_cursor': _encode(p.workspace_id, subject_type, subject.id, rows[PAGE_SIZE - 1]) if len(rows) > PAGE_SIZE else None,
            'continuation': continuation}
