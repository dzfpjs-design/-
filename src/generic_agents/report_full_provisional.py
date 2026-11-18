"""Persist a bounded machine preview privately, without changing the report job."""
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import func, select

from .contracts import digest
from .models import Audit, AuthSession, CloudCall, Membership, User
from .report_full_provisional_models import FullProvisionalCandidate
from .report_full_failed_candidate_view import failed_candidate_view
from .report_full_raw_view import raw_view
from .report_full_reading_review_candidate import CANDIDATE_ALGORITHM_VERSION
from .services import audit, require
from .security import authenticated
from . import report_reading_drafts as legacy

ALGORITHM = CANDIDATE_ALGORITHM_VERSION
# Append-only registry: new writers may select a new ALGORITHM, while saved
# historical records keep their original algorithm and remain readable.
READABLE_ALGORITHMS = frozenset({'report-failed-candidate-quarantine-v1'})


class SaveCommand(legacy.SafeContract):
    revision: int = Field(ge=1, strict=True)
    request_id: UUID
    source_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    raw_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    view_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    algorithm_version: str = Field(min_length=1, max_length=80, strict=True)

    @field_validator('algorithm_version')
    @classmethod
    def known_algorithm(cls, value):
        if value not in READABLE_ALGORITHMS:
            raise ValueError('未知的候选算法版本')
        return value


def _fail(code, message, status=409):
    raise HTTPException(status, {'code': code, 'message': message},
                        headers={'Cache-Control': 'private, no-store'})


def _signature(p, draft_id, stage, body):
    return digest({'kind':'report_full_provisional_save', 'workspace_id':p.workspace_id,
                   'actor_id':p.user_id, 'draft_id':draft_id, 'stage':stage,
                   'body':body.model_dump(mode='json')})


def _stored(db, p, record, draft):
    """Validate stored output and its original paid checkpoint before reading."""
    data = record.data
    if not isinstance(data, dict) or set(data) != {'candidate','quarantine','receipt'}:
        _fail('full_provisional_integrity', '保存的机器候选无法核对；原记录保留')
    receipt = data.get('receipt')
    if not isinstance(receipt,dict):
        _fail('full_provisional_integrity', '保存的机器候选无法核对；原记录保留')
    fields = {'schema','workspace_id','actor_id','draft_id','stage','request_id','draft_revision',
              'source_hash','raw_hash','preview_hash','view_hash','algorithm_version','candidate_hash',
              'quarantine_hash','command_hash','receipt_hash'}
    if (set(receipt) != fields or receipt.get('schema') != 'report-full-provisional-receipt-v1'
        or receipt.get('receipt_hash') != digest({k:v for k,v in receipt.items() if k != 'receipt_hash'})
        or receipt.get('workspace_id') != p.workspace_id or receipt.get('actor_id') != p.user_id
        or receipt.get('draft_id') != draft.id or record.draft_id != draft.id
        or receipt.get('stage') != record.stage or receipt.get('request_id') != record.request_id
        or receipt.get('draft_revision') != record.draft_revision or draft.version < record.draft_revision
        or receipt.get('raw_hash') != record.raw_hash or receipt.get('algorithm_version') != record.algorithm_version
        or record.algorithm_version not in READABLE_ALGORITHMS or receipt.get('source_hash') != draft.data['preview']['source_hash']
        or receipt.get('preview_hash') != draft.data['preview']['preview_hash']
        or receipt.get('candidate_hash') != digest(data['candidate'])
        or receipt.get('quarantine_hash') != digest(data['quarantine'])):
        _fail('full_provisional_integrity', '保存的机器候选无法核对；原记录保留')
    original_scope = {'workspace_id':p.workspace_id, 'actor_id':p.user_id, 'draft_id':draft.id,
        'revision':record.draft_revision, 'stage':record.stage,
        'preview_hash':receipt['preview_hash'], 'raw_hash':record.raw_hash}
    original_preview = {'schema':'report-full-failed-candidate-preview-v1', 'scope':original_scope,
        'candidate':data['candidate'], 'quarantine':data['quarantine'], 'algorithm_version':record.algorithm_version,
        'status':'needs_review', 'confirmed':False, 'can_apply':False, 'can_publish':False}
    try:
        original_command = SaveCommand.model_validate({'revision':record.draft_revision,
            'request_id':record.request_id, 'source_hash':receipt['source_hash'], 'raw_hash':record.raw_hash,
            'view_hash':receipt['view_hash'], 'algorithm_version':record.algorithm_version})
        if (digest(original_preview) != receipt['view_hash']
                or _signature(p, draft.id, record.stage, original_command) != receipt['command_hash']):
            raise ValueError('provenance')
    except (ValueError, TypeError, KeyError):
        _fail('full_provisional_integrity', '保存的候选与原预览无法核对；原记录保留')
    audits = db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.actor_id == p.user_id,
        Audit.object_id == draft.id, Audit.action == 'setup.report_full_reading.provisional_saved',
        Audit.data['request_id'].as_string() == record.request_id).limit(2)).all()
    if (len(audits) != 1 or audits[0].data != {'stage':record.stage, 'request_id':record.request_id,
            'receipt_hash':receipt['receipt_hash']}):
        _fail('full_provisional_integrity', '保存回执与审计无法核对；原记录保留')
    raw = raw_view(db, p, draft.id, record.stage, draft.version)
    if (raw['scope']['raw_hash'] != record.raw_hash or raw['status'] != 'available'
            or raw['finish_reason'] != 'stop' or raw['response_body_status'] != 'complete'):
        _fail('full_provisional_integrity', '原付费回答或回执无法核对；保存稿保留')
    return {'schema':'report-full-provisional-v1', 'id':record.id,
            'candidate':data['candidate'], 'quarantine':data['quarantine'], 'receipt':receipt,
            'status':'needs_review', 'confirmed':False, 'can_apply':False, 'can_publish':False}


def _lock_authority(db, p, request):
    # Existing logout/revocation and membership changes update these same rows.
    # Hold locks through commit and refresh potentially stale identity-map rows.
    session = db.scalar(select(AuthSession).where(AuthSession.id == p.session_id)
        .with_for_update().execution_options(populate_existing=True))
    user = db.scalar(select(User).where(User.id == p.user_id)
        .with_for_update().execution_options(populate_existing=True))
    member = db.scalar(select(Membership).where(Membership.user_id == p.user_id,
        Membership.workspace_id == p.workspace_id).with_for_update().execution_options(populate_existing=True))
    if (request is None or not session or session.user_id != p.user_id or session.revoked
            or not user or not user.active or not member or member.role != p.role
            or member.role not in ('admin','operator')):
        _fail('full_provisional_authority_changed', '当前登录或工作区权限已变化，未保存候选', 403)
    fresh = authenticated(request, db)
    if fresh != p:
        _fail('full_provisional_authority_changed', '当前登录或工作区权限已变化，未保存候选', 403)


def save(db, p, identity, stage, body, *, request=None):
    from . import report_full_reading_pipeline as full
    require(p, 'admin', 'operator')
    # The workspace lock serializes nonce and first-save checks even across drafts.
    legacy.workspace_draft(db, p)
    _lock_authority(db, p, request)
    draft = full.owned(db, p, identity, True)
    old = db.scalar(select(FullProvisionalCandidate).where(
        FullProvisionalCandidate.workspace_id == p.workspace_id,
        FullProvisionalCandidate.actor_id == p.user_id,
        FullProvisionalCandidate.request_id == str(body.request_id)))
    signature = _signature(p, draft.id, stage, body)
    if old:
        old_receipt = old.data.get('receipt') if isinstance(old.data, dict) else None
        if not isinstance(old_receipt, dict):
            _fail('full_provisional_integrity', '保存的机器候选无法核对；原记录保留')
        if old.draft_id != draft.id or old.stage != stage or old_receipt.get('command_hash') != signature:
            _fail('full_provisional_nonce_conflict', '原保存编号已用于不同内容；原记录保留')
        return _stored(db, p, old, draft)
    if body.algorithm_version != ALGORITHM:
        _fail('full_provisional_algorithm_changed', '候选生成版本已更新，请重新读取并核对')
    if draft.version != body.revision:
        _fail('full_provisional_revision_changed', '草案版本已变化，请刷新后重新核对')
    if draft.data['preview']['source_hash'] != body.source_hash:
        _fail('full_provisional_source_changed', '冻结来源与预览不一致；原记录保留')
    stages = [s for s in draft.data['extractions'] if s.get('batch_id') == stage]
    if len(stages) != 1:
        _fail('full_provisional_stage_invalid', '未找到此批次', 404)
    call_id = stages[0].get('reserved_call_id')
    if not call_id:
        _fail('full_provisional_raw_missing', '原回答不存在，未保存候选')
    db.scalar(select(CloudCall).where(CloudCall.id == call_id, CloudCall.workspace_id == p.workspace_id).with_for_update())
    preview = failed_candidate_view(db, p, draft.id, stage, body.revision)
    if (preview['scope']['raw_hash'] != body.raw_hash or preview['view_hash'] != body.view_hash
            or preview['algorithm_version'] != body.algorithm_version):
        _fail('full_provisional_preview_changed', '原回答或候选预览已变化，请重新核对')
    duplicate = db.scalar(select(FullProvisionalCandidate.id).where(
        FullProvisionalCandidate.workspace_id == p.workspace_id, FullProvisionalCandidate.actor_id == p.user_id,
        FullProvisionalCandidate.draft_id == draft.id, FullProvisionalCandidate.stage == stage,
        FullProvisionalCandidate.raw_hash == body.raw_hash, FullProvisionalCandidate.algorithm_version == ALGORITHM))
    if duplicate:
        _fail('full_provisional_already_saved', '此批次机器候选已有保存记录，请读取原回执')
    signed = {'schema':'report-full-provisional-receipt-v1', 'workspace_id':p.workspace_id,
        'actor_id':p.user_id, 'draft_id':draft.id, 'stage':stage, 'request_id':str(body.request_id),
        'draft_revision':body.revision, 'source_hash':body.source_hash, 'raw_hash':body.raw_hash,
        'preview_hash':preview['scope']['preview_hash'], 'view_hash':body.view_hash, 'algorithm_version':ALGORITHM,
        'candidate_hash':digest(preview['candidate']), 'quarantine_hash':digest(preview['quarantine']),
        'command_hash':signature}
    receipt = {**signed, 'receipt_hash':digest(signed)}
    record = FullProvisionalCandidate(workspace_id=p.workspace_id, actor_id=p.user_id, draft_id=draft.id,
        stage=stage, request_id=str(body.request_id), draft_revision=body.revision, raw_hash=body.raw_hash,
        algorithm_version=ALGORITHM, data={'candidate':preview['candidate'], 'quarantine':preview['quarantine'], 'receipt':receipt})
    db.add(record); db.flush()
    audit(db,p,'setup.report_full_reading.provisional_saved',draft.id,
          {'stage':stage, 'request_id':str(body.request_id), 'receipt_hash':receipt['receipt_hash']})
    if not db.scalar(select(AuthSession.id).where(AuthSession.id == p.session_id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())):
        _fail('full_provisional_authority_changed', '登录已过期，未保存候选', 403)
    db.commit()
    return _stored(db,p,record,draft)


def read(db, p, identity, request_id):
    from . import report_full_reading_pipeline as full
    draft = full.owned(db, p, identity)
    record = db.scalar(select(FullProvisionalCandidate).where(
        FullProvisionalCandidate.workspace_id == p.workspace_id, FullProvisionalCandidate.actor_id == p.user_id,
        FullProvisionalCandidate.draft_id == draft.id, FullProvisionalCandidate.request_id == str(request_id)))
    if record is None:
        _fail('full_provisional_not_found', '未找到本人的保存回执', 404)
    return _stored(db,p,record,draft)


def discover(db, p, identity, stage):
    """Find the owner's saved receipts even after local browser state is lost."""
    from . import report_full_reading_pipeline as full
    draft = full.owned(db, p, identity)
    if stage not in ('extract-1','extract-2') or not any(
            entry.get('batch_id') == stage for entry in draft.data['extractions']):
        _fail('full_provisional_stage_invalid', '未找到此批次', 404)
    records = db.scalars(select(FullProvisionalCandidate).where(
        FullProvisionalCandidate.workspace_id == p.workspace_id, FullProvisionalCandidate.actor_id == p.user_id,
        FullProvisionalCandidate.draft_id == draft.id, FullProvisionalCandidate.stage == stage)
        .order_by(FullProvisionalCandidate.created_at, FullProvisionalCandidate.id).limit(11)).all()
    if len(records) > 10:
        _fail('full_provisional_list_capacity', '保存记录过多，原记录保留，请联系管理员核对', 409)
    items = []
    for record in records:
        checked = _stored(db, p, record, draft)
        items.append({'id':checked['id'], 'receipt':checked['receipt'], 'status':'needs_review', 'confirmed':False})
    return {'schema':'report-full-provisional-list-v1','workspace_id':p.workspace_id,
            'actor_id':p.user_id,'draft_id':draft.id,'stage':stage,'items':items}
