"""Durable private checks against the exact, frozen complete source.

This is deliberately not a formal final-view projection. No current source is
silently substituted, no original/private manual note is returned, and no
configuration, source-confirmation, execution or paid state is changed.
"""
import copy
import json
from typing import Literal
from uuid import UUID
from urllib.parse import urlencode

from fastapi import APIRouter, Query
from pydantic import Field, model_validator
from sqlalchemy import select, text

from . import report_evidence_pipeline as pipeline
from . import report_full_semantic_handoff as full
from . import report_full_semantic_replacement as replacement
from .contracts import digest
from .models import SetupReportDraft
from .report_full_semantic_source_review_models import FullSemanticSourceReview as Review
from .report_reading_drafts import SafeContract
from .services import audit, require

router = APIRouter()
PREFIX = '/full-semantic-source-reviews/{identity}'
STATE = 'report-full-semantic-source-review-state-v1'
SAVE = 'report-full-semantic-source-review-save-v1'
RECEIPT = 'report-full-semantic-source-review-receipt-v1'
LEDGER = 'report-full-semantic-source-review-entry-v1'
MAX_OWNER_VERSIONS = 100
MAX_REPLY_BYTES = 24 * 1024 * 1024


def fail(code, message, status=409):
    pipeline.fail(code, message, status)


class Value(SafeContract):
    decision: Literal['pending', 'checked', 'needs_change']
    note: str = Field(strict=True, max_length=4000)
    checked_ref_hashes: list[str] = Field(max_length=65536)

    @model_validator(mode='after')
    def unique_refs(self):
        import re
        if (len(set(self.checked_ref_hashes)) != len(self.checked_ref_hashes)
                or any(not re.fullmatch(full.HASH, item) for item in self.checked_ref_hashes)):
            raise ValueError('引用选择必须是独立、完整的来源摘要')
        return self


class Save(SafeContract):
    schema: Literal['report-full-semantic-source-review-save-v1']
    request_id: UUID
    manifest_hash: str = Field(strict=True, pattern=full.HASH)
    owner_path: str = Field(strict=True, min_length=1, max_length=500, pattern=r'^/')
    owner_value_hash: str = Field(strict=True, pattern=full.HASH)
    expected_version: int = Field(strict=True, ge=0, le=MAX_OWNER_VERSIONS)
    value: Value


def command_hash(p, identity, body):
    return digest({'schema': 'report-full-semantic-source-review-command-hash-v1',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'handoff_id': str(identity), 'body': body.model_dump(mode='json')})


def context(db, p, identity, *, lock=False):
    """Cold-verify shared ancestry and the private reading as its actual owner."""
    writable = full.old().configuration_row(db, p, True) if lock else None
    records, heads, _, snapshot = replacement.record_set(db, p, include_configuration=True)
    if writable is not None and (writable.id != snapshot.id or writable.version != snapshot.version
            or digest(writable.data) != digest(snapshot.data)):
        fail('source_review_stale', '工作区配置已变化，请保留输入并重新核对')
    configuration = writable if writable is not None else snapshot
    record = records.get(str(identity))
    if record is None:
        fail('not_found', '未找到当前工作区的完整设计', 404)
    replacement.assert_active_configuration(configuration.data, heads)
    report = db.get(SetupReportDraft, record['report_id'])
    if report is None or report.workspace_id != p.workspace_id or report.actor_id != record['actor_id']:
        fail('source_review_integrity', '原读取记录无法核对，已保存的核对记录保留')
    owner = type('ReadingOwner', (), {'user_id': report.actor_id, 'workspace_id': p.workspace_id})()
    pipeline.check_row(report, owner)
    preview = report.data['preview']
    from .report_full_semantic_source_manifest_v1 import build_source_manifest
    try:
        catalog = pipeline._catalog(preview)
        manifest = build_source_manifest(record['design'], catalog, preview['source_snapshot'],
            privacy_projection_hash=preview['projection_hash'])
    except (ValueError, TypeError, KeyError):
        fail('source_review_integrity', '完整设计与冻结原文、脱敏目录或引用位置不一致，原记录保留')
    if record['design']['source_hash'] != preview['source_hash']:
        fail('source_review_integrity', '此设计的原始资料摘要无法核对')
    return configuration, record, heads, manifest, catalog, preview['source_snapshot']


def owner_at(manifest, path):
    owner = next((o for o in manifest['owners'] if o['path'] == path), None)
    if owner is None:
        fail('source_review_invalid_value', '此项不在完整设计的核对清单中，原输入保留', 422)
    return owner


def validate_value(body, owner):
    values = body.value.model_dump(mode='json')
    checked = values['checked_ref_hashes']
    refs = owner['ref_hashes']
    if (body.owner_value_hash != owner['value_hash']
            or checked != [ref for ref in refs if ref in checked]
            or any(ref not in refs for ref in checked)):
        fail('source_review_invalid_value', '核对项或引用选择不属于此冻结版本，原输入保留', 422)
    if values['decision'] == 'checked' and (not refs or checked != refs
            or owner['evidence_status'] in {'unknown', 'context'}):
        fail('source_review_invalid_value', '请逐处核对全部引用；缺少依据或尚未明确的内容请保存为待核对或需要修改', 422)
    return values


def receipt(p, identity, body):
    value = {'schema': RECEIPT, 'request_id': str(body.request_id),
        'owner_path': body.owner_path, 'version': body.expected_version + 1,
        'value_hash': digest(body.value.model_dump(mode='json')), 'manifest_hash': body.manifest_hash,
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'handoff_id': str(identity),
        'command_hash': command_hash(p, identity, body)}
    return {**value, 'receipt_hash': digest(value)}


def checked_entry(row, p, manifest=None):
    try:
        if (row.workspace_id != p.workspace_id or row.actor_id != p.user_id
                or set(row.data) != {'schema', 'command', 'receipt', 'entry_hash'}
                or row.data['schema'] != LEDGER
                or row.data['entry_hash'] != digest({k: v for k, v in row.data.items() if k != 'entry_hash'})):
            raise ValueError('entry shape')
        body = Save.model_validate(row.data['command'])
        if (str(body.request_id) != row.request_id or body.owner_path != row.owner_path
                or body.expected_version + 1 != row.version
                or row.data['receipt'] != receipt(p, row.handoff_id, body)):
            raise ValueError('entry receipt')
        if manifest is not None:
            if body.manifest_hash != manifest['manifest_hash']:
                raise ValueError('entry manifest')
            validate_value(body, owner_at(manifest, row.owner_path))
        return {'owner_path': row.owner_path, 'version': row.version,
            'value': body.value.model_dump(mode='json'), 'receipt': copy.deepcopy(row.data['receipt'])}
    except (ValueError, TypeError, KeyError, AttributeError):
        fail('source_review_integrity', '私人核对记录或保存回执无法核对；原记录保留')


def entries(db, p, identity, manifest, *, path=None):
    statement = select(Review).where(Review.workspace_id == p.workspace_id,
        Review.actor_id == p.user_id, Review.handoff_id == str(identity))
    if path is not None:
        statement = statement.where(Review.owner_path == path)
    rows = list(db.scalars(statement.order_by(Review.owner_path, Review.version)))
    versions = {}; result = []
    for row in rows:
        if row.version != versions.get(row.owner_path, 0) + 1 or row.version > MAX_OWNER_VERSIONS:
            fail('source_review_integrity', '私人核对历史不连续，原记录保留')
        versions[row.owner_path] = row.version
        result.append(checked_entry(row, p, manifest))
    return result


def bounded(value):
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_REPLY_BYTES:
        fail('source_review_capacity', '完整核对响应超过上限；服务器历史和原输入保留', 413)
    return value


def state(db, p, identity, ctx=None):
    configuration, _, heads, manifest, _, _ = ctx or context(db, p, identity)
    latest = {}
    for entry in entries(db, p, identity, manifest):
        latest[entry['owner_path']] = entry
    return bounded({'schema': STATE, 'handoff_id': str(identity), 'active': str(identity) in heads,
        'configuration': {'revision': configuration.version, 'hash': digest(configuration.data)},
        'manifest': manifest, 'reviews': list(latest.values()), 'can_confirm': False, 'can_publish': False})


def prior_request(db, p, request_id):
    return db.scalar(select(Review).where(Review.workspace_id == p.workspace_id,
        Review.actor_id == p.user_id, Review.request_id == str(request_id)))


@router.get(PREFIX)
def get_state(identity: UUID, p: pipeline.ReadP, db: pipeline.DB):
    return state(db, p, identity)


@router.get(PREFIX + '/requests/{request_id}')
def recover(identity: UUID, request_id: UUID, p: pipeline.ReadP, db: pipeline.DB):
    # Scope authorization still applies; never expose another person's nonce.
    ctx = context(db, p, identity)
    row = prior_request(db, p, request_id)
    value = {'schema': 'report-full-semantic-source-review-recovery-v1', 'found': row is not None and row.handoff_id == str(identity)}
    if value['found']:
        entry = checked_entry(row, p, ctx[3])
        history = entries(db, p, identity, ctx[3], path=row.owner_path)
        value.update(receipt=entry['receipt'], entry={k: entry[k] for k in ('owner_path', 'version', 'value')},
            current_matches=str(identity) in ctx[2] and history[-1]['version'] == row.version)
    return value


@router.get(PREFIX + '/history')
def history(identity: UUID, p: pipeline.ReadP, db: pipeline.DB,
            owner_path: str = Query(min_length=1, max_length=500)):
    ctx = context(db, p, identity); owner_at(ctx[3], owner_path)
    return bounded({'schema': 'report-full-semantic-source-review-history-v1',
        'handoff_id': str(identity), 'owner_path': owner_path,
        'items': entries(db, p, identity, ctx[3], path=owner_path)})


@router.get(PREFIX + '/evidence')
def evidence(identity: UUID, p: pipeline.ReadP, db: pipeline.DB,
             owner_path: str = Query(min_length=1, max_length=500)):
    _, record, _, manifest, catalog, snapshot = context(db, p, identity)
    owner = owner_at(manifest, owner_path)
    from .report_full_reading_source import project_full_source
    from .evidence_privacy_trace_v1 import build_privacy_trace
    _, index = project_full_source(snapshot)
    if record['design']['schema'] == 'report-object-semantic-design-v169':
        from .report_object_source_manifest_v170 import owner_value
        value = owner_value(record['design'], owner_path)
        if digest(value) != owner['value_hash']:
            fail('source_review_integrity', '核对项与完整身份来源无法核对，原记录保留')
    else:
        value = record['design']['result']
        for key in owner_path.strip('/').split('/'):
            value = value[int(key)] if isinstance(value, list) else value[key]
    references = []
    for ref in manifest['refs']:
        if ref['owner_path'] != owner_path:
            continue
        raw = index[ref['source_ref']]
        visible = build_privacy_trace(raw['text'])['text']
        start, end = ref['visible_start'], ref['visible_end']
        left, right = max(0, start - 240), min(len(visible), end + 240)
        image_url = image_sha = None
        if raw['kind'] == 'ocr_page':
            loc = raw['location']; image_sha = loc['image_sha256']
            image_url = '/api/setup/materials/' + raw['material_id'] + '/ocr/units/' + loc['unit_id'] + '/image?' + urlencode({
                'run_id': loc['run_id'], 'image_sha256': image_sha})
        references.append({**copy.deepcopy(ref), 'quote': visible[start:end],
            'context': visible[left:right], 'context_start': left,
            'location': copy.deepcopy(ref['source_anchor']['raw_location']),
            'image_url': image_url, 'image_sha256': image_sha})
    return bounded({'schema': 'report-full-semantic-source-review-evidence-v1',
        'manifest_hash': manifest['manifest_hash'], 'owner': {**copy.deepcopy(owner), 'value': copy.deepcopy(value)},
        'references': references, 'formal_source_status': 'pending_final_view_projection',
        'can_confirm': False, 'can_publish': False})


@router.post(PREFIX + '/entries')
def save(identity: UUID, body: Save, p: pipeline.P, db: pipeline.DB):
    require(p, 'admin', 'operator')
    # One actor-wide lock also serializes the same nonce across different designs.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
        {'key': 'full-source-review-v1:' + p.workspace_id + ':' + p.user_id})
    prior = prior_request(db, p, body.request_id)
    if prior is not None:
        if (prior.handoff_id != str(identity)
                or checked_entry(prior, p)['receipt']['command_hash'] != command_hash(p, identity, body)):
            fail('source_review_nonce_conflict', '原保存编号已用于其他内容；原请求与输入保留')
        result = state(db, p, identity)
        result['receipt'] = checked_entry(prior, p, result['manifest'])['receipt']
        return result
    ctx = context(db, p, identity, lock=True)
    manifest = ctx[3]
    if str(identity) not in ctx[2] or body.manifest_hash != manifest['manifest_hash']:
        fail('source_review_stale', '共享设计已有新版本，原核对输入与历史保留；请先核对版本')
    values = validate_value(body, owner_at(manifest, body.owner_path))
    history = entries(db, p, identity, manifest, path=body.owner_path)
    current = history[-1]['version'] if history else 0
    if body.expected_version != current:
        fail('source_review_conflict', '另一窗口已保存此项；本次输入和双方版本保留')
    if current >= MAX_OWNER_VERSIONS:
        fail('source_review_capacity', '此项核对历史已达上限；原输入与全部历史保留', 413)
    full.old().setup().automation_review_actor(db, p)
    accepted = receipt(p, identity, body)
    data = {'schema': LEDGER, 'command': body.model_dump(mode='json'), 'receipt': accepted}
    data['entry_hash'] = digest(data)
    row = Review(workspace_id=p.workspace_id, actor_id=p.user_id, handoff_id=str(identity),
        owner_path=body.owner_path, request_id=str(body.request_id), version=current + 1, data=data)
    db.add(row); db.flush()
    audit(db, p, 'setup.full_semantic_source_review.saved', row.id,
        {'handoff_id': str(identity), 'owner_path': body.owner_path, 'version': row.version,
         'value_hash': digest(values), 'receipt_hash': accepted['receipt_hash']})
    result = state(db, p, identity, ctx)
    result['receipt'] = accepted
    db.commit()
    return result
