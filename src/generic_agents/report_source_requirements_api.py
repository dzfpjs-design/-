"""Private source-to-workflow checks, with append-only versions and receipts.

No cloud calls, generation, adoption or device authority are granted here.
This sidecar never modifies the report draft or its accepted candidate.
"""
import copy
import hashlib
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import func, select, text

from .contracts import Contract, digest
from .models import AuthSession, Membership, SetupReportDraft, User, now
from .services import audit
from . import report_evidence_pipeline as evidence
from .report_reading_drafts import DB, P, fail
from .report_source_requirement_models import SetupRequirementCheck
from .report_source_requirements import (
    build_source_units, check_requirement_record, requirement_record_status,
)

router = APIRouter()
RECEIPT = 'report-source-requirement-receipt-v1'
STORED = 'report-source-requirement-stored-v1'


class SaveRequirementCheck(Contract):
    schema: Literal['report-source-requirement-save-v1']
    request_id: UUID
    check_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    draft_revision: int = Field(ge=1, strict=True)
    value: dict


def _owned_parent(db, p, identity, lock=False):
    query = select(SetupReportDraft).where(SetupReportDraft.id == str(identity),
        SetupReportDraft.workspace_id == p.workspace_id, SetupReportDraft.actor_id == p.user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        fail('not_found', '未找到本人资料草稿', 404)
    return row


def _write_actor(db, p):
    permitted = db.execute(select(User, Membership, AuthSession)
        .join(Membership, Membership.user_id == User.id)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(User.id == p.user_id, User.active.is_(True),
            Membership.workspace_id == p.workspace_id, Membership.role.in_(['admin', 'operator']),
            AuthSession.id == p.session_id, AuthSession.revoked.is_(False), AuthSession.expires_at > now())
        .with_for_update(read=True, of=[User, Membership, AuthSession])
        .execution_options(populate_existing=True)).first()
    if not permitted:
        fail('permission_changed', '当前账号没有保存业务要求核对的权限', 403)


def _context(db, p, identity, lock=False):
    row = _owned_parent(db, p, identity, lock)
    if not evidence.is_evidence(row):
        fail('source_review_unsupported', '这份旧草稿暂不支持逐项核对，请保留原稿', 409)
    evidence.check_row(row, p)
    catalog = evidence._catalog(row.data['preview'])
    view = evidence.draft_view(row, p)
    if view['review_value'] is not None:
        kind, value = 'manual', view['review_value']
        candidate = value['result']
    elif view['model_result'] is not None:
        kind, value = 'model', view['model_result']
        candidate = value
    else:
        kind, value, candidate = 'not_generated', None, None
    binding = {'draft_revision': row.version,
        'source_hash': catalog['descriptor']['source_hash'],
        'catalog_hash': catalog['descriptor']['catalog_hash'],
        'candidate_kind': kind, 'candidate_hash': None if candidate is None else digest(candidate),
        'effective_value_hash': None if value is None else digest(value)}
    return row, catalog, candidate, binding


def _scope(p, identity):
    return (SetupRequirementCheck.workspace_id == p.workspace_id,
        SetupRequirementCheck.actor_id == p.user_id, SetupRequirementCheck.draft_id == str(identity))


def _verified_entry(row):
    data = row.data
    try:
        if set(data) != {'schema', 'checked', 'binding', 'receipt'} or data['schema'] != STORED:
            raise ValueError()
        receipt = data['receipt']
        expected = {'schema': RECEIPT, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
            'draft_id': row.draft_id, 'check_id': row.check_id, 'item_version': row.item_version,
            'request_id': row.request_id, 'command_hash': row.command_hash,
            'checked_hash': digest(data['checked']), 'binding_hash': digest(data['binding']),
            'saved_at': receipt['saved_at']}
        if receipt != {**expected, 'receipt_hash': digest(expected)}:
            raise ValueError()
        if type(row.item_version) is not int or row.item_version < 1:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        fail('requirement_history_integrity', '已保存的核对记录无法核对；原记录保留')
    return data


def _entry_view(row, *, catalog=None, candidate=None, binding=None):
    data = _verified_entry(row)
    state = {'status': 'history', 'reasons': []}
    if binding is not None:
        try:
            state = requirement_record_status(data['checked'], catalog=catalog,
                candidate_result=candidate, candidate_hash=binding['candidate_hash'])
        except (KeyError, TypeError, ValueError):
            fail('requirement_history_integrity', '已保存的核对内容无法核对；不会替换原记录')
        if data['binding'] != binding:
            state = {'status': 'stale', 'reasons': ['draft_or_effective_value_changed']}
    return {'check_id': row.check_id, 'version': row.item_version,
        'checked': copy.deepcopy(data['checked']), 'binding': copy.deepcopy(data['binding']),
        'receipt': copy.deepcopy(data['receipt']), 'review_status': state,
        'can_apply': False, 'can_publish': False, 'semantic_completeness_verified': False}


def _latest_query(p, identity):
    latest = select(SetupRequirementCheck.check_id,
        func.max(SetupRequirementCheck.item_version).label('latest_version'))
    latest = latest.where(*_scope(p, identity)).group_by(SetupRequirementCheck.check_id).subquery()
    return select(SetupRequirementCheck).join(latest,
        (SetupRequirementCheck.check_id == latest.c.check_id)
        & (SetupRequirementCheck.item_version == latest.c.latest_version)).where(*_scope(p, identity))


def _source_label(ref):
    return ('文档第 ' + ref[3:] + ' 页') if ref.startswith('o:p') else ('原文位置 ' + ref.split(':', 1)[-1].lstrip('p'))


def _target_options(candidate):
    if candidate is None:
        return []
    options = []
    def add(path, label, value, kind):
        summary = value if isinstance(value, str) else value.get('purpose', value.get('statement', value.get('responsibility', ''))) if isinstance(value, dict) else ''
        options.append({'pointer': path, 'label': str(label), 'kind': kind,
            'summary': str(summary)[:300], 'value_hash': digest(value)})
    if 'domain' in candidate:
        add('/domain', '领域与目标', candidate['domain'], 'domain')
    for i, business in enumerate(candidate.get('businesses', [])):
        name = business.get('name', '业务 ' + str(i + 1))
        add(f'/businesses/{i}', name, business, 'business')
        for collection, title in [('steps', '步骤'), ('participants', '参与职责'), ('conditions', '条件'),
                ('interactions', '信息传递'), ('coordination', '协同安排')]:
            for j, item in enumerate(business.get(collection, [])):
                label = item.get('name') or item.get('agent') or item.get('statement') or item.get('purpose') or item.get('start_rule') or title + str(j + 1)
                add(f'/businesses/{i}/{collection}/{j}', name + ' · ' + title + ' · ' + label, item, collection)
    for collection, title in [('objects', '数据对象'), ('relationships', '数据关系'),
            ('global_claims', '资料要求'), ('questions', '待核问题')]:
        for i, value in enumerate(candidate.get(collection, [])):
            label = value if isinstance(value, str) else value.get('name') or value.get('statement') or title + str(i + 1)
            add(f'/{collection}/{i}', title + ' · ' + str(label), value, collection)
            if collection == 'objects':
                for j, field in enumerate(value.get('fields', [])):
                    add(f'/objects/{i}/fields/{j}', '数据字段 · ' + str(label) + ' · ' + str(field.get('name', field.get('label', j + 1))), field, 'fields')
    return options


@router.get('/report-evidence-readings/{identity}/requirement-review')
def get_review(identity: UUID, p: P, db: DB,
        offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
        source_unit_id: str | None = Query(None, max_length=100),
        check_offset: int = Query(0, ge=0), check_limit: int = Query(20, ge=1, le=100),
        target_search: str = Query('', max_length=100),
        target_offset: int = Query(0, ge=0), target_limit: int = Query(100, ge=1, le=200)):
    row, catalog, candidate, binding = _context(db, p, identity)
    bundle = build_source_units(catalog); units = bundle['units']
    selected = next((unit for unit in units if unit['source_unit_id'] == source_unit_id), None) if source_unit_id else next(iter(units), None)
    if source_unit_id and selected is None:
        fail('source_unit_not_found', '未找到这处原文位置', 404)
    summaries = []
    for unit in units[offset:offset + limit]:
        ref = unit['source_unit_id']
        original = ''.join(catalog['index'][span]['text'] for span in catalog['index'] if catalog['index'][span]['source_ref'] == ref)
        summaries.append({'source_unit_id': ref, 'label': _source_label(ref),
            'preview': original[:100], 'empty': not original, 'unit_hash': unit['unit_hash']})
    selected_view = None
    if selected:
        ref = selected['source_unit_id']
        original = ''.join(value['text'] for value in catalog['index'].values() if value['source_ref'] == ref)
        selected_view = {**selected, 'label': _source_label(ref), 'text': original}
    check_query = _latest_query(p, identity)
    if selected:
        check_query = check_query.where(SetupRequirementCheck.data['checked']['record']['source_unit_ids'].contains([selected['source_unit_id']]))
    else:
        check_query = check_query.where(False)
    check_total = db.scalar(select(func.count()).select_from(check_query.subquery()))
    matching = db.scalars(check_query.order_by(SetupRequirementCheck.created_at.desc())
        .offset(check_offset).limit(check_limit)).all()
    options = _target_options(candidate)
    if target_search:
        options = [option for option in options if target_search.casefold() in (option['label'] + option['summary']).casefold()]
    return {'schema': 'report-source-requirement-review-view-v1', 'draft_id': row.id,
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'binding': binding,
        'source_units': summaries, 'source_total': len(units), 'offset': offset, 'limit': limit,
        'selected_unit': selected_view,
        'checks': [_entry_view(entry, catalog=catalog, candidate=candidate, binding=binding) for entry in matching],
        'check_total': check_total, 'check_offset': check_offset, 'check_limit': check_limit,
        'target_options': options[target_offset:target_offset + target_limit], 'target_total': len(options),
        'target_offset': target_offset, 'candidate_available': candidate is not None,
        'semantic_completeness_verified': False, 'all_source_units_reviewed': False,
        'can_apply': False, 'can_publish': False}


def _command_hash(p, identity, body):
    return digest({'kind': 'source_requirement_check', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'draft_id': str(identity), 'body': body.model_dump(mode='json')})


@router.put('/report-evidence-readings/{identity}/requirement-checks/{check_id}')
def save_review(identity: UUID, check_id: UUID, body: SaveRequirementCheck, p: P, db: DB):
    if check_id != body.check_id:
        fail('requirement_identity_conflict', '核对记录与当前页面不一致', 422)
    _owned_parent(db, p, identity)
    command_hash = _command_hash(p, identity, body)
    lock_key = int.from_bytes(hashlib.sha256((p.workspace_id + ':' + p.user_id + ':' + str(body.request_id)).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_key})
    saved = db.scalar(select(SetupRequirementCheck).where(
        SetupRequirementCheck.workspace_id == p.workspace_id, SetupRequirementCheck.actor_id == p.user_id,
        SetupRequirementCheck.request_id == str(body.request_id)))
    if saved is not None:
        if saved.command_hash != command_hash or saved.draft_id != str(identity):
            fail('nonce_conflict', '这个保存编号已经用于另一份内容；原核对记录保留')
        return {'saved': _entry_view(saved), 'replayed': True}
    row, catalog, candidate, binding = _context(db, p, identity, True)
    _write_actor(db, p)
    if row.version != body.draft_revision:
        fail('draft_revision_conflict', '资料或流程已更新，请重新核对后保存', current_revision=row.version)
    previous = db.scalar(select(SetupRequirementCheck).where(*_scope(p, identity),
        SetupRequirementCheck.check_id == str(check_id)).order_by(SetupRequirementCheck.item_version.desc()).limit(1))
    actual = 0 if previous is None else previous.item_version
    if actual != body.expected_version:
        fail('requirement_version_conflict', '这条要求已在另一处修改；请先恢复已保存内容', current_version=actual)
    try:
        checked = check_requirement_record(body.value, catalog=catalog,
            candidate_result=candidate, candidate_hash=binding['candidate_hash'])
    except (KeyError, TypeError, ValueError):
        fail('requirement_check_invalid', '原文、流程位置或核对内容不匹配；请重新核对', 422)
    version = actual + 1
    receipt = {'schema': RECEIPT, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'draft_id': str(identity), 'check_id': str(check_id), 'item_version': version,
        'request_id': str(body.request_id), 'command_hash': command_hash,
        'checked_hash': digest(checked), 'binding_hash': digest(binding), 'saved_at': now().isoformat()}
    receipt = {**receipt, 'receipt_hash': digest(receipt)}
    entry = SetupRequirementCheck(workspace_id=p.workspace_id, actor_id=p.user_id, draft_id=str(identity),
        check_id=str(check_id), item_version=version, request_id=str(body.request_id), command_hash=command_hash,
        data={'schema': STORED, 'checked': checked, 'binding': binding, 'receipt': receipt})
    db.add(entry)
    audit(db, p, 'setup.source_requirement.saved', str(check_id),
        {'draft_id': str(identity), 'request_id': str(body.request_id), 'item_version': version})
    db.flush()
    result = _entry_view(entry, catalog=catalog, candidate=candidate, binding=binding)
    db.commit()
    return {'saved': result, 'replayed': False}


@router.get('/report-evidence-readings/{identity}/requirement-receipts/{request_id}')
def get_receipt(identity: UUID, request_id: UUID, p: P, db: DB):
    _owned_parent(db, p, identity)
    saved = db.scalar(select(SetupRequirementCheck).where(*_scope(p, identity),
        SetupRequirementCheck.request_id == str(request_id)))
    if saved is None:
        fail('receipt_not_found', '暂未查到这次保存；可以用同一保存编号重试', 404)
    return {'saved': _entry_view(saved), 'replayed': True}


@router.get('/report-evidence-readings/{identity}/requirement-checks/{check_id}/history')
def get_history(identity: UUID, check_id: UUID, p: P, db: DB,
        offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    _owned_parent(db, p, identity)
    entries = db.scalars(select(SetupRequirementCheck).where(*_scope(p, identity),
        SetupRequirementCheck.check_id == str(check_id)).order_by(SetupRequirementCheck.item_version.desc())
        .offset(offset).limit(limit)).all()
    return {'entries': [_entry_view(entry) for entry in entries], 'offset': offset, 'limit': limit}
