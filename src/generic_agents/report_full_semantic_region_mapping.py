"""Explicit full-design citation positions; no private-note sharing or execution.

Private sessions freeze an expressly selected copy/page version. Each citation
occurrence is saved independently. A public proof requires a separate preview,
consent, all occurrences and the genuine whole-document confirmation gate.
"""
import copy
from urllib.parse import urlencode
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from sqlalchemy import select, text

from . import report_full_semantic_source_review as review
from . import report_full_semantic_region_source as source
from . import report_full_semantic_region_mapping_v1 as pure
from .report_full_semantic_region_models import FullSemanticRegionSession as Session, FullSemanticRegionEntry as Entry, FullSemanticRegionProof as Proof
from .report_reading_drafts import SafeContract
from .report_full_reading_source import project_full_source
from .evidence_privacy_trace_v1 import build_privacy_trace
from .contracts import digest
from .services import require, audit

router = APIRouter()
PREFIX = '/full-semantic-region-mappings/{identity}'
HASH = review.full.HASH
MAX_ENTRIES = 65536


def fail(code, message, status=409):
    review.fail('full_region_' + code, message, status)


class Start(SafeContract):
    schema: Literal['report-full-semantic-region-session-command-v1']
    request_id: UUID
    manifest_hash: str = Field(strict=True, pattern=HASH)
    selection: source.Selection


class Save(SafeContract):
    schema: Literal['report-full-semantic-region-entry-command-v1']
    request_id: UUID
    ref_hash: str = Field(strict=True, pattern=HASH)
    expected_version: int = Field(strict=True, ge=0, lt=100)
    mapping: dict
    image_acknowledgements: list[UUID] = Field(min_length=1, max_length=500)
    privacy_acknowledged: bool = Field(strict=True)


class Preview(SafeContract):
    schema: Literal['report-full-semantic-region-proof-preview-command-v1']
    session_id: UUID
    manifest_hash: str = Field(strict=True, pattern=HASH)
    configuration_revision: int = Field(strict=True, ge=1)
    configuration_hash: str = Field(strict=True, pattern=HASH)


class Publish(SafeContract):
    schema: Literal['report-full-semantic-region-proof-command-v1']
    request_id: UUID
    session_id: UUID
    manifest_hash: str = Field(strict=True, pattern=HASH)
    configuration_revision: int = Field(strict=True, ge=1)
    configuration_hash: str = Field(strict=True, pattern=HASH)
    preview_hash: str = Field(strict=True, pattern=HASH)
    consent: bool = Field(strict=True)


MODELS = {'session': (Session, Start), 'entry': (Entry, Save), 'proof': (Proof, Publish)}


def command_hash(p, identity, body):
    return digest({'schema': 'report-full-semantic-region-command-hash-v1',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'handoff_id': str(identity), 'body': body.model_dump(mode='json')})


def seal(value, key):
    review.bounded(value)
    return {**value, key: digest(value)}


def receipt(row, kind, body):
    p = type('Owner', (), {'workspace_id': row.workspace_id, 'user_id': row.actor_id})()
    return seal({'schema': 'report-full-semantic-region-receipt-v1', 'kind': kind,
        'request_id': row.request_id, 'resource_id': row.id, 'version': row.version,
        'session_id': row.id if kind == 'session' else row.session_id,
        'actor_id': row.actor_id, 'workspace_id': row.workspace_id, 'handoff_id': row.handoff_id,
        'command_hash': command_hash(p, row.handoff_id, body)}, 'receipt_hash')


def checked(row, kind, p, identity):
    try:
        if (row is None or row.workspace_id != p.workspace_id or row.handoff_id != str(identity)
                or kind != 'proof' and row.actor_id != p.user_id):
            fail('not_found', '未找到此人的引用定位记录', 404)
        expected_keys = {'schema', 'command', 'receipt', 'ledger_hash'} | ({'proof'} if kind == 'proof' else set())
        data = row.data
        if (set(data) != expected_keys or data['schema'] != 'report-full-semantic-region-' + kind + '-ledger-v1'
                or data['ledger_hash'] != digest({k: v for k, v in data.items() if k != 'ledger_hash'})):
            raise ValueError('ledger')
        body = MODELS[kind][1].model_validate(data['command'])
        if str(body.request_id) != row.request_id or data['receipt'] != receipt(row, kind, body):
            raise ValueError('receipt')
        if kind == 'entry' and (body.ref_hash != row.ref_hash or body.expected_version + 1 != row.version):
            raise ValueError('entry index')
        if kind == 'proof' and str(body.session_id) != row.session_id:
            raise ValueError('proof session')
        return body
    except (ValueError, TypeError, KeyError, AttributeError):
        fail('integrity', '定位记录或保存回执无法核对；原记录保留')


def write_lock(db, p):
    require(p, 'admin', 'operator')
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
        {'key': 'full-region-v1:' + p.workspace_id + ':' + p.user_id})


def prior(db, p, identity, body, kind):
    for name, (model, _) in MODELS.items():
        row = db.scalar(select(model).where(model.workspace_id == p.workspace_id,
            model.actor_id == p.user_id, model.request_id == str(body.request_id)))
        if row is not None:
            old = checked(row, name, p, row.handoff_id)
            if name != kind or row.handoff_id != str(identity) or command_hash(p, identity, old) != command_hash(p, identity, body):
                fail('nonce_conflict', '原请求编号已用于其他内容；原请求与输入保留')
            return row
    return None


def session_row(db, p, identity, sid):
    row = db.get(Session, str(sid)); checked(row, 'session', p, identity)
    return row


def ref_at(manifest, ref_hash):
    ref = next((item for item in manifest['refs'] if item['ref_hash'] == ref_hash), None)
    if ref is None:
        fail('invalid_value', '引用不属于本次完整设计', 422)
    return ref


def raw_source(snapshot, ref):
    return project_full_source(snapshot)[1][ref['source_ref']]


def entries(db, p, identity, session, manifest):
    rows = list(db.scalars(select(Entry).where(Entry.workspace_id == p.workspace_id,
        Entry.actor_id == p.user_id, Entry.session_id == session.id).order_by(Entry.ref_hash, Entry.version).limit(MAX_ENTRIES + 1)))
    if len(rows) > MAX_ENTRIES:
        fail('capacity', '定位历史超过完整读取上限；已保存记录保留', 413)
    latest = {}; history = []
    for row in rows:
        body = checked(row, 'entry', p, identity); ref_at(manifest, row.ref_hash)
        if row.version != latest.get(row.ref_hash, {'version': 0})['version'] + 1:
            fail('integrity', '定位历史不连续；原记录保留')
        value = {'id': row.id, 'ref_hash': row.ref_hash, 'version': row.version,
            'mapping': copy.deepcopy(body.mapping),
            'image_acknowledgements': [str(item) for item in body.image_acknowledgements],
            'privacy_acknowledged': body.privacy_acknowledged, 'receipt': copy.deepcopy(row.data['receipt'])}
        latest[row.ref_hash] = value; history.append(value)
    return latest, history


def check_mapping(body, ref, snapshot, pages, material_id):
    try:
        mapping = body.mapping
        segments = [{k: seg[k] for k in (*pure._META, 'start', 'end')} for seg in mapping['segments']]
        pure.check_region_mapping(mapping, ref, raw_source(snapshot, ref), pages, segments, copy_material_id=material_id)
        units = [seg['unit_id'] for seg in mapping['segments']]
        if ([str(item) for item in body.image_acknowledgements] != units
                or mapping['privacy_relation'] == 'privacy_projected' and not body.privacy_acknowledged):
            fail('invalid_value', '请查看每张原页图；遮盖范围须单独确认，不以占位文字代替原文', 422)
        return mapping
    except (ValueError, TypeError, KeyError):
        fail('invalid_value', '所选位置不能与冻结原文逐段核对；原输入保留', 422)


def current_source(db, p, snapshot, selection, formal=False, lock=False):
    try:
        return source.source_chain(db, p, snapshot, selection, current=True, formal=formal, lock=lock)
    except HTTPException as error:
        # This gate precedes any insertion/commit. A source-version refusal is
        # a definite rejection, so the original outbox may be explicitly archived.
        # Never relabel transport uncertainty or final authorization failures.
        if lock and error.status_code == 409:
            fail('stale', reason(error))
        raise


def readiness(db, p, snapshot, selection):
    try:
        current_source(db, p, snapshot, selection)
    except HTTPException as error:
        return False, False, reason(error)
    try:
        current_source(db, p, snapshot, selection, formal=True)
        return True, True, None
    except HTTPException as error:
        return True, False, reason(error)


def reason(error):
    return error.detail.get('message', '来源尚未通过完整核对') if isinstance(error.detail, dict) else str(error.detail)


def summary(row):
    return {'id': row.id, 'selection_hash': row.data['command']['selection']['selection_hash'],
        'copy_ref': copy.deepcopy(row.data['command']['selection']['copy_ref']),
        'receipt': copy.deepcopy(row.data['receipt'])}


def state(db, p, identity):
    ctx = review.context(db, p, identity); configuration, _, heads, manifest, _, _ = ctx
    rows = list(db.scalars(select(Session).where(Session.workspace_id == p.workspace_id,
        Session.actor_id == p.user_id, Session.handoff_id == str(identity)).order_by(Session.created_at, Session.id).limit(21)))
    if len(rows) > 20:
        fail('capacity', '已保存的副本定位会话超过上限；全部原记录保留', 413)
    for row in rows:
        checked(row, 'session', p, identity)
    proofs = list(db.scalars(select(Proof).where(Proof.workspace_id == p.workspace_id,
        Proof.handoff_id == str(identity)).order_by(Proof.created_at, Proof.id).limit(101)))
    if len(proofs) > 100:
        fail('capacity', '公开位置证明超过完整读取上限；全部记录保留', 413)
    for row in proofs:
        checked(row, 'proof', p, identity)
    return review.bounded({'schema': 'report-full-semantic-region-state-v1', 'handoff_id': str(identity),
        'active': str(identity) in heads, 'manifest': manifest,
        'configuration': {'revision': configuration.version, 'hash': digest(configuration.data)},
        'sessions': [summary(row) for row in rows], 'proofs': [{'id': row.id,
            'proof_hash': row.data['proof']['proof_hash'], 'mapped_ref_count': len(row.data['proof']['mappings'])} for row in proofs],
        'can_confirm': False, 'can_publish': False})


def session_view(db, p, identity, sid):
    ctx = review.context(db, p, identity); row = session_row(db, p, identity, sid)
    body = checked(row, 'session', p, identity)
    if body.manifest_hash != ctx[3]['manifest_hash']:
        fail('integrity', '定位会话与原完整设计不一致')
    latest, _ = entries(db, p, identity, row, ctx[3])
    current, formal, why = readiness(db, p, ctx[5], body.selection.model_dump(mode='json'))
    if current:
        _, material, _, pages, _ = current_source(db, p, ctx[5], body.selection.model_dump(mode='json'))
        for entry in latest.values():
            saved = db.get(Entry, entry['id'])
            check_mapping(checked(saved, 'entry', p, identity), ref_at(ctx[3], entry['ref_hash']), ctx[5], pages, material.id)
    # A stale source never erases saved work; the returned ledger is not renewed authority.
    return review.bounded({'schema': 'report-full-semantic-region-session-v1', **summary(row),
        'handoff_id': str(identity), 'manifest_hash': body.manifest_hash,
        'selection': body.selection.model_dump(mode='json'), 'entries': list(latest.values()),
        'current': current and str(identity) in ctx[2], 'formal_source_ready': formal,
        'reason': why, 'can_confirm': False, 'can_publish': False})


def persist(db, p, row, kind, body, extra=None):
    # Recheck and lock current User/Membership/AuthSession immediately before mutation.
    review.full.old().setup().automation_review_actor(db, p)
    row.request_id = str(body.request_id)
    db.add(row); db.flush()
    accepted = receipt(row, kind, body)
    row.data = seal({'schema': 'report-full-semantic-region-' + kind + '-ledger-v1',
        'command': body.model_dump(mode='json'), 'receipt': accepted, **(extra or {})}, 'ledger_hash')
    db.flush()
    audit(db, p, 'setup.full_semantic_region.' + kind, row.id,
        {'handoff_id': row.handoff_id, 'receipt_hash': accepted['receipt_hash'], 'version': row.version})
    db.commit()
    return {'schema': 'report-full-semantic-region-save-result-v1', 'receipt': accepted,
        'session_id': row.id if kind == 'session' else row.session_id}


@router.get(PREFIX)
def get_state(identity: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    return state(db, p, identity)


@router.post(PREFIX + '/sessions')
def start(identity: UUID, body: Start, p: review.pipeline.P, db: review.pipeline.DB):
    write_lock(db, p); old = prior(db, p, identity, body, 'session')
    if old:
        return {'schema': 'report-full-semantic-region-save-result-v1', 'receipt': old.data['receipt'], 'session_id': old.id}
    ctx = review.context(db, p, identity, lock=True)
    if str(identity) not in ctx[2] or body.manifest_hash != ctx[3]['manifest_hash']:
        fail('stale', '设计已有新版本，原定位工作保留')
    count = list(db.scalars(select(Session.id).where(Session.workspace_id == p.workspace_id,
        Session.actor_id == p.user_id, Session.handoff_id == str(identity)).limit(20)))
    if len(count) >= 20:
        fail('capacity', '此设计的副本选择已达上限；原记录保留', 413)
    current_source(db, p, ctx[5], body.selection.model_dump(mode='json'), lock=True)
    row = Session(workspace_id=p.workspace_id, actor_id=p.user_id, handoff_id=str(identity), version=1)
    return persist(db, p, row, 'session', body)


@router.get(PREFIX + '/requests/{request_id}')
def recover(identity: UUID, request_id: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    ctx = review.context(db, p, identity)
    for kind, (model, _) in MODELS.items():
        row = db.scalar(select(model).where(model.workspace_id == p.workspace_id,
            model.actor_id == p.user_id, model.request_id == str(request_id), model.handoff_id == str(identity)))
        if row is not None:
            checked(row, kind, p, identity)
            return {'schema': 'report-full-semantic-region-recovery-v1', 'found': True,
                'receipt': copy.deepcopy(row.data['receipt']),
                'session_id': row.id if kind == 'session' else row.session_id,
                'active': str(identity) in ctx[2]}
    return {'schema': 'report-full-semantic-region-recovery-v1', 'found': False}


@router.get(PREFIX + '/sessions/{sid}')
def get_session(identity: UUID, sid: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    return session_view(db, p, identity, sid)


@router.get(PREFIX + '/sessions/{sid}/history')
def history(identity: UUID, sid: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB,
            ref_hash: str = Query(pattern=HASH)):
    ctx = review.context(db, p, identity); ref_at(ctx[3], ref_hash)
    row = session_row(db, p, identity, sid)
    return review.bounded({'schema': 'report-full-semantic-region-history-v1', 'session_id': str(sid),
        'ref_hash': ref_hash, 'entries': [item for item in entries(db, p, identity, row, ctx[3])[1] if item['ref_hash'] == ref_hash]})


@router.get(PREFIX + '/sessions/{sid}/candidates')
def candidates(identity: UUID, sid: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB,
               ref_hash: str = Query(pattern=HASH)):
    ctx = review.context(db, p, identity); row = session_row(db, p, identity, sid)
    body = checked(row, 'session', p, identity); ref = ref_at(ctx[3], ref_hash)
    _, material, run, pages, _ = current_source(db, p, ctx[5], body.selection.model_dump(mode='json'))
    raw = raw_source(ctx[5], ref)
    try:
        found = pure.find_region_candidates(ref, raw, pages, copy_material_id=material.id)
    except pure.RegionMappingCapacityExceeded:
        fail('capacity', '精确匹配超过100处，不能截断或自动选择；请先核对来源文字', 413)
    except (ValueError, KeyError, TypeError):
        fail('integrity', '原文与副本区域无法冷核，已保存进度保留')
    touched = {segment['unit_id'] for value in found for segment in value['segments']}
    shown = []
    for page in pages:
        if page['unit_id'] not in touched:
            continue
        unit = source.ocr.checked_unit(db, p, material, run, page['unit_id'], image=True)
        shown.append({k: page[k] for k in pure._META} | {'width': unit.data['width'], 'height': unit.data['height'],
            'image_url': '/api/setup/materials/' + material.id + '/ocr/units/' + unit.id + '/image?' + urlencode({
                'run_id': run.id, 'image_sha256': page['image_sha256']})})
    trace = build_privacy_trace(raw['text'])
    return review.bounded({'schema': 'report-full-semantic-region-candidates-v1', 'session_id': str(sid),
        'manifest_hash': ctx[3]['manifest_hash'], 'ref': ref,
        'source_quote': trace['text'][ref['visible_start']:ref['visible_end']],
        'candidates': found, 'pages': shown})


@router.post(PREFIX + '/sessions/{sid}/entries')
def save(identity: UUID, sid: UUID, body: Save, p: review.pipeline.P, db: review.pipeline.DB):
    write_lock(db, p); old = prior(db, p, identity, body, 'entry')
    if old:
        if old.session_id != str(sid):
            fail('nonce_conflict', '原请求属于另一个副本选择；原输入保留')
        return {'schema': 'report-full-semantic-region-save-result-v1', 'receipt': old.data['receipt'], 'session_id': old.session_id}
    ctx = review.context(db, p, identity, lock=True); session = session_row(db, p, identity, sid)
    if str(identity) not in ctx[2]:
        fail('stale', '设计已有新版本；原定位记录保留')
    selection = session.data['command']['selection']
    _, material, _, pages, _ = current_source(db, p, ctx[5], selection, lock=True)
    ref = ref_at(ctx[3], body.ref_hash); check_mapping(body, ref, ctx[5], pages, material.id)
    latest, all_entries = entries(db, p, identity, session, ctx[3])
    expected = latest.get(body.ref_hash, {}).get('version', 0)
    if body.expected_version != expected:
        fail('conflict', '另一窗口已保存此引用；双方版本保留，请先查看历史')
    if len(all_entries) >= MAX_ENTRIES:
        fail('capacity', '定位历史已达上限；原输入保留', 413)
    row = Entry(workspace_id=p.workspace_id, actor_id=p.user_id, handoff_id=str(identity),
        session_id=str(sid), ref_hash=body.ref_hash, version=expected + 1)
    return persist(db, p, row, 'entry', body)


def proof_schema(manifest):
    schema = manifest.get('schema')
    if schema == 'report-object-source-manifest-v170':
        return 'report-object-region-proof-v170'
    if schema == 'report-full-semantic-source-manifest-v1':
        return 'report-full-semantic-region-proof-v1'
    fail('integrity', '未知的完整来源合同，不能签发引用位置证明')


def proof_preview_hash(proof):
    prefixes = {'report-full-semantic-region-proof-v1': 'report-full-semantic-region-proof-preview-hash-v1',
        'report-object-region-proof-v170': 'report-object-region-proof-preview-hash-v170'}
    prefix = prefixes.get(proof.get('schema'))
    if prefix is None:
        fail('integrity', '未知的引用位置证明合同')
    return digest({'schema': prefix, 'proof': proof})


def proof_value(db, p, identity, body, *, lock=False):
    ctx = review.context(db, p, identity, lock=lock); config, _, heads, manifest, _, snapshot = ctx
    if (str(identity) not in heads or body.manifest_hash != manifest['manifest_hash']
            or body.configuration_revision != config.version or body.configuration_hash != digest(config.data)):
        fail('stale', '完整设计或业务配置已有新版本，请保留进度后重新预览')
    session = session_row(db, p, identity, body.session_id)
    selection = session.data['command']['selection']
    _, material, _, pages, confirmation = current_source(db, p, snapshot, selection, formal=True, lock=lock)
    latest, _ = entries(db, p, identity, session, manifest)
    if not manifest['refs'] or set(latest) != {r['ref_hash'] for r in manifest['refs']}:
        fail('incomplete', '请逐处保存全部引用位置；未完成的进度已保留')
    mappings = []
    for ref in manifest['refs']:
        entry = latest[ref['ref_hash']]
        row = db.get(Entry, entry['id']); check_mapping(checked(row, 'entry', p, identity), ref, snapshot, pages, material.id)
        mappings.append({'ref_hash': ref['ref_hash'], 'entry_id': row.id, 'entry_version': row.version,
            'mapping': copy.deepcopy(entry['mapping'])})
    # Position verification cannot approve the meaning of even an explicit claim.
    pending = [owner['path'] for owner in manifest['owners']]
    return seal({'schema': proof_schema(manifest), 'handoff_id': str(identity),
        'manifest_hash': manifest['manifest_hash'], 'configuration': {'revision': config.version, 'hash': digest(config.data)},
        'session_id': session.id, 'selection': copy.deepcopy(selection),
        'confirmation_id': confirmation.id, 'mappings': mappings, 'pending_semantic_owner_paths': pending,
        'source_status': 'citation_positions_confirmed_semantics_pending', 'can_confirm': False, 'can_publish': False}, 'proof_hash')


@router.post(PREFIX + '/proof-preview')
def preview(identity: UUID, body: Preview, p: review.pipeline.ReadP, db: review.pipeline.DB):
    proof = proof_value(db, p, identity, body)
    schema = ('report-object-region-proof-preview-v170' if proof['schema'] == 'report-object-region-proof-v170'
        else 'report-full-semantic-region-proof-preview-v1')
    return {'schema': schema, 'proof': proof, 'preview_hash': proof_preview_hash(proof)}


@router.post(PREFIX + '/proofs')
def publish(identity: UUID, body: Publish, p: review.pipeline.P, db: review.pipeline.DB):
    write_lock(db, p); old = prior(db, p, identity, body, 'proof')
    if old:
        return {'schema': 'report-full-semantic-region-save-result-v1', 'receipt': old.data['receipt'], 'session_id': old.session_id}
    if body.consent is not True:
        fail('invalid_value', '请先核对预览并明确同意共享引用位置；私人说明不共享', 422)
    proof = proof_value(db, p, identity, body, lock=True)
    if body.preview_hash != proof_preview_hash(proof):
        fail('stale', '引用位置或确认版本已有变化，请重新预览；原进度保留')
    count = list(db.scalars(select(Proof.id).where(Proof.workspace_id == p.workspace_id,
        Proof.handoff_id == str(identity)).limit(100)))
    if len(count) >= 100:
        fail('capacity', '公开位置证明已达上限；原记录保留', 413)
    row = Proof(workspace_id=p.workspace_id, actor_id=p.user_id, handoff_id=str(identity),
        session_id=str(body.session_id), version=1)
    return persist(db, p, row, 'proof', body, {'proof': proof})


@router.get(PREFIX + '/proofs/{proof_id}')
def get_proof(identity: UUID, proof_id: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    ctx = review.context(db, p, identity); row = db.get(Proof, str(proof_id)); command = checked(row, 'proof', p, identity)
    proof = copy.deepcopy(row.data['proof'])
    owner = type('Owner', (), {'workspace_id': p.workspace_id, 'user_id': row.actor_id})()
    session = session_row(db, owner, identity, row.session_id)
    expected_keys = {'schema', 'handoff_id', 'manifest_hash', 'configuration', 'session_id', 'selection',
        'confirmation_id', 'mappings', 'pending_semantic_owner_paths', 'source_status', 'can_confirm', 'can_publish', 'proof_hash'}
    if (set(proof) != expected_keys or proof.get('proof_hash') != digest({k: v for k, v in proof.items() if k != 'proof_hash'})
            or proof['schema'] != proof_schema(ctx[3]) or proof['handoff_id'] != str(identity)
            or proof['manifest_hash'] != ctx[3]['manifest_hash'] or command.manifest_hash != proof['manifest_hash']
            or proof['session_id'] != session.id or proof['selection'] != session.data['command']['selection']
            or proof['configuration'] != {'revision': command.configuration_revision, 'hash': command.configuration_hash}
            or proof['pending_semantic_owner_paths'] != [item['path'] for item in ctx[3]['owners']]
            or proof['source_status'] != 'citation_positions_confirmed_semantics_pending'
            or proof['can_confirm'] is not False or proof['can_publish'] is not False
            or command.consent is not True
            or command.preview_hash != proof_preview_hash(proof)
            or not isinstance(proof['mappings'], list)
            or [m.get('ref_hash') for m in proof['mappings']] != [r['ref_hash'] for r in ctx[3]['refs']]):
        fail('integrity', '公开位置证明与冻结设计无法核对')
    current, formal, why = readiness(db, p, ctx[5], proof['selection'])
    pages = material = None
    if current and formal:
        _, material, _, pages, confirmation = current_source(db, p, ctx[5], proof['selection'], formal=True)
        if proof['confirmation_id'] != confirmation.id:
            fail('integrity', '公开位置证明与整篇确认记录不一致')
    for item, ref in zip(proof['mappings'], ctx[3]['refs']):
        if set(item) != {'ref_hash', 'entry_id', 'entry_version', 'mapping'}:
            fail('integrity', '公开位置证明含未声明字段')
        entry = db.get(Entry, item['entry_id']); body = checked(entry, 'entry', owner, identity)
        if entry.session_id != row.session_id or entry.version != item['entry_version'] or body.mapping != item['mapping']:
            fail('integrity', '公开位置证明与原保存记录不一致')
        if pages is not None:
            check_mapping(body, ref, ctx[5], pages, material.id)
    return review.bounded({'schema': 'report-full-semantic-region-public-proof-v1', 'id': row.id,
        'proof': proof, 'current_source_verified': current and formal and str(identity) in ctx[2],
        'reason': why, 'receipt': copy.deepcopy(row.data['receipt'])})
