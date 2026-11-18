"""A separately authorized evidence task linked to an earlier failed report.

The link describes an operator-delegated *new* analysis of overlapping source,
not recovery of the original batch or proof of historical network inactivity.
No model request is sent here. Legacy ``fe_`` drafts keep their old contract.
"""
from __future__ import annotations

import copy
import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.exc import DBAPIError

from . import report_reading_drafts as legacy
from . import report_full_reading_pipeline as full
from . import report_evidence_pipeline as evidence
from .contracts import digest
from .models import Audit, AuthSession, CloudCall, Job, SetupReportDraft, uid
from .report_full_provisional import _lock_authority
from .report_full_provisional import _stored as check_provisional
from .report_full_provisional_models import FullProvisionalCandidate
from .report_full_raw_view import raw_view
from .report_full_second_batch_preview import preview as inspect_old
from .services import audit, require

router = APIRouter()
ORIGIN = 'report-evidence-delegated-origin-v1'
ACTION = 'setup.report_evidence.delegated_origin_v1'
KEY_PREFIX = 'report-evidence-delegated:'
_SHA = r'^[a-f0-9]{64}$'
_BASIS_KEYS = {'schema', 'workspace_id', 'actor_id', 'original_draft_id', 'original_revision',
    'original_job_id', 'original_start_receipt_hash', 'original_source_hash', 'original_projection_hash',
    'original_first_raw_hash', 'original_first_candidate_receipt_hash', 'original_messages_hashes',
    'new_messages_hashes', 'new_preview_hash', 'operator_asserted_history_hash',
    'history_digest_verification', 'history_assessment', 'historical_second_send', 'source_relationship', 'new_analysis',
    'not_original_batch_recovery', 'authorization_basis', 'user_reviewed_history', 'user_confirmed_business',
    'duplicate_cost_risk'}


class LinkedPreviewCommand(legacy.SafeContract):
    original_draft_id: UUID
    original_revision: int = Field(ge=1, strict=True)
    # The operator supplies the digest of a separately retained history
    # assessment. The API cannot independently authenticate that file.
    operator_asserted_history_hash: str | None = Field(default=None, pattern=_SHA, strict=True)
    reading: evidence.PreviewCommand

    @field_validator('reading')
    @classmethod
    def linked_nonce(cls, value):
        if not value.request_id.startswith('fl_'):
            raise ValueError('受托读取必须使用独立 fl_ 编号')
        return value


class LinkedStartCommand(LinkedPreviewCommand):
    linked_preview_hash: str = Field(pattern=_SHA, strict=True)
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def exact_consent(cls, value):
        if value is not True:
            raise ValueError('必须授权此新任务的范围与调用')
        return value


def _fail(code, message, status=409):
    evidence.fail(code, message, status)


def _key(original_id):
    return KEY_PREFIX + str(original_id)


def _old_job(db, workspace_id, job_id, *, lock):
    """Never wait for a worker Job while holding the workspace draft lock.

    A live worker takes the Job lease before acquiring the workspace lock.
    NOWAIT avoids the inverse lock cycle and is a pre-send denial, not a
    retry or evidence that an earlier request did not leave the machine.
    """
    query = select(Job).where(Job.id == job_id, Job.workspace_id == workspace_id)
    if lock:
        query = query.with_for_update(nowait=True).execution_options(populate_existing=True)
    try:
        return db.scalar(query)
    except DBAPIError as error:
        if getattr(error.orig, 'sqlstate', None) != '55P03':
            raise
        db.rollback()
        _fail('delegated_original_busy', '原任务正在处理或核对，本次未新增模型调用')


def _new_hashes(preview):
    chunks = preview['map_preparation']['plan']['chunks']
    hashes = [chunk['messages_hash'] for chunk in chunks]
    if not hashes or len(hashes) != len(set(hashes)) or any(not re.fullmatch(_SHA, h) for h in hashes):
        _fail('delegated_messages_invalid', '新分段消息无法唯一核对，未发送')
    return hashes


_HISTORY_ASSUMPTIONS = [
    'runtime_code_not_independently_attested',
    'database_and_audit_integrity_assumed',
    'no_unobserved_external_gateway_bypass_assumed',
]
_HISTORY_SCHEMA = 'report-evidence-history-assessment-v1'


def _history_assessment(assessment):
    """Seal only observations the server has just checked, never a send proof."""
    scope = assessment['scope']
    job = assessment['original_job']
    candidate = assessment['first_candidate']
    authorization = assessment['original_authorization']
    codes = {item['code'] for item in assessment['blockers']}
    if (assessment['original_history_verified'] is not True
            or assessment['ledger_clear'] is not True
            or assessment['frozen_scope_current'] is not True
            or not isinstance(job, dict) or job.get('status') != 'failed'
            or job.get('attempts') != 1 or job.get('lease_recoveries') != 0
            or not isinstance(job.get('data_hash'), str) or not re.fullmatch(_SHA, job['data_hash'])
            or not isinstance(candidate, dict) or candidate.get('confirmed') is not False
            or not isinstance(authorization, dict) or authorization.get('original_consent_verified') is not True
            or scope.get('stage') != 'extract-2'
            or 'second_stage_not_sent_unproven' not in codes
            or codes - {'second_stage_not_sent_unproven', 'budget_insufficient'}):
        _fail('delegated_history_unavailable', '原任务观察条件无法核对，未创建新任务')
    body = {
        'schema': _HISTORY_SCHEMA,
        'workspace_id': scope['workspace_id'], 'actor_id': scope['actor_id'],
        'original_draft_id': scope['draft_id'], 'original_revision': scope['revision'],
        'original_start_receipt_hash': scope['original_start_receipt_hash'],
        'original_source_hash': scope['source_hash'], 'second_messages_hash': scope['messages_hash'],
        'original_job_id': job['id'], 'original_job_data_hash': job['data_hash'],
        'original_job_failed_single_attempt_no_active_lease': True,
        'original_authorization_and_failure_audit_verified': True,
        'first_response_ledger_and_candidate_verified': True,
        'second_stage_pending_and_workspace_ledger_clear': True,
        'independent_transport_or_provider_proof': False,
        'conclusion': 'conditional_engineering_inference_not_provider_proof',
        'assumptions': list(_HISTORY_ASSUMPTIONS),
    }
    return {**body, 'assessment_hash': digest(body)}


def _check_history_assessment(value, basis):
    """Check the frozen observation without inventing later history."""
    keys = {'schema', 'workspace_id', 'actor_id', 'original_draft_id', 'original_revision',
        'original_start_receipt_hash', 'original_source_hash', 'second_messages_hash',
        'original_job_id', 'original_job_data_hash', 'original_job_failed_single_attempt_no_active_lease',
        'original_authorization_and_failure_audit_verified', 'first_response_ledger_and_candidate_verified',
        'second_stage_pending_and_workspace_ledger_clear', 'independent_transport_or_provider_proof',
        'conclusion', 'assumptions', 'assessment_hash'}
    if (not isinstance(value, dict) or set(value) != keys or value.get('schema') != _HISTORY_SCHEMA
            or value.get('assessment_hash') != digest({k: v for k, v in value.items() if k != 'assessment_hash'})
            or any(value.get(k) != basis.get(k) for k in ('workspace_id', 'actor_id', 'original_draft_id',
                'original_revision', 'original_start_receipt_hash', 'original_source_hash', 'original_job_id'))
            or not isinstance(basis.get('original_messages_hashes'), list) or len(basis['original_messages_hashes']) != 2
            or value.get('second_messages_hash') != basis['original_messages_hashes'][1]
            or not isinstance(value.get('original_job_data_hash'), str)
            or not re.fullmatch(_SHA, value['original_job_data_hash'])
            or any(value.get(k) is not True for k in ('original_job_failed_single_attempt_no_active_lease',
                'original_authorization_and_failure_audit_verified', 'first_response_ledger_and_candidate_verified',
                'second_stage_pending_and_workspace_ledger_clear'))
            or value.get('independent_transport_or_provider_proof') is not False
            or value.get('conclusion') != 'conditional_engineering_inference_not_provider_proof'
            or value.get('assumptions') != _HISTORY_ASSUMPTIONS):
        _fail('delegated_origin_invalid', '受托历史观察无法核对，未发送')
    return value


def _basis(db, p, old, new_preview, asserted_history_hash):
    # This existing diagnostic verifies original ownership, original paid raw,
    # provenance, audit, job, exact source/config/model and provisional copy.
    old_job = _old_job(db, p.workspace_id, old.data['job_id'], lock=True)
    first_call_id = old.data['extractions'][0].get('reserved_call_id')
    first_call = db.scalar(select(CloudCall).where(CloudCall.id == first_call_id,
        CloudCall.workspace_id == p.workspace_id).with_for_update()) if first_call_id else None
    if not old_job or not first_call:
        _fail('delegated_history_unavailable', '原任务或原账本无法核对，未创建新任务')
    assessment = inspect_old(db, p, old.id, old.version)
    allowed = {'second_stage_not_sent_unproven', 'budget_insufficient'}
    codes = {item['code'] for item in assessment['blockers']}
    if 'second_stage_not_sent_unproven' not in codes or codes - allowed:
        _fail('delegated_history_unavailable', '原任务或账本无法安全核对，未创建新任务')
    old_preview = old.data['preview']
    if (new_preview['source_hash'] != old_preview['source_hash']
            or new_preview['projection_hash'] != old_preview['projection_hash']):
        _fail('delegated_source_changed', '新读取范围与原冻结报告不同，未创建新任务')
    old_hashes = [batch['messages_hash'] for batch in old_preview['batches']]
    new_hashes = _new_hashes(new_preview)
    if len(old_hashes) != 2 or set(old_hashes) & set(new_hashes):
        _fail('delegated_messages_overlap', '新消息编号与旧调用相同，未创建新任务')
    candidate = assessment['first_candidate']
    if not candidate or candidate['confirmed'] is not False:
        _fail('delegated_candidate_missing', '原付费回答的待核稿未能核对，未创建新任务')
    history_assessment = _history_assessment(assessment)
    return {
        'schema': 'report-evidence-delegated-basis-v1',
        'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'original_draft_id': old.id, 'original_revision': old.version,
        'original_job_id': old.data['job_id'],
        'original_start_receipt_hash': old.data['start_receipt']['receipt_hash'],
        'original_source_hash': old_preview['source_hash'],
        'original_projection_hash': old_preview['projection_hash'],
        'original_first_raw_hash': old.data['extractions'][0]['raw_response']['hash'],
        'original_first_candidate_receipt_hash': candidate['receipt_hash'],
        'original_messages_hashes': old_hashes, 'new_messages_hashes': new_hashes,
        'new_preview_hash': new_preview['preview_hash'],
        'operator_asserted_history_hash': asserted_history_hash,
        'history_digest_verification': ('not_provided' if asserted_history_hash is None
                                        else 'operator_asserted_not_independently_verified'),
        'history_assessment': history_assessment,
        'historical_second_send': 'not_independently_proven',
        'source_relationship': 'same_report_word_and_ocr_views_overlap',
        'new_analysis': True, 'not_original_batch_recovery': True,
        # The authenticated principal submitted this request; neither the
        # client-provided history digest nor consent proves a chat instruction.
        'authorization_basis': 'authenticated_actor_submitted_request',
        'user_reviewed_history': False, 'user_confirmed_business': False,
        'duplicate_cost_risk': True,
    }


def _proposal(db, p, command):
    old = full.owned(db, p, command.original_draft_id, True)
    if old.version != command.original_revision:
        _fail('delegated_original_changed', '原任务版本已变化，请重新核对')
    value = evidence.make_preview(db, p, command.reading)
    basis = _basis(db, p, old, value, command.operator_asserted_history_hash)
    from .report_unresolved_overlap import guard_new_paid_sources
    selection = value['body']['selection']
    guard_new_paid_sources(db, p, [selection['original_ref'], selection['copy_ref']],
                           linked_original_id=old.id)
    from .report_evidence_delegated_dispatch_gate import ready
    dispatch_ready = ready()
    blocker = (value['start_blocker'] if dispatch_ready else
               '分段读取的定向额度及安全派发尚未接通；本次仅核对范围，没有创建任务或调用模型。')
    result = {'schema': 'report-evidence-delegated-preview-v1', 'basis': basis,
              'reading_preview': value, 'can_start': value['can_start'] and dispatch_ready,
              'start_blocker': blocker,
              'can_apply': False, 'can_publish': False}
    return {**result, 'linked_preview_hash': digest(result)}


def _signature(p, body):
    return digest({'kind': 'report_evidence_delegated_start_v1', 'workspace_id': p.workspace_id,
                   'actor_id': p.user_id, 'body': body.model_dump(mode='json')})


def verify_link(db, p, row, *, lock=True, require_current=False):
    """Verify historical provenance; optionally require authority to send now.

    Reading an already-saved response must not depend on the old task still
    being in its original failed state. A new dispatch has the stricter rule:
    the old task and paid-call situation must still match the admission view.
    """
    if not row.request_id.startswith('fl_'):
        return
    marker_query = select(Job).where(Job.workspace_id == p.workspace_id,
        Job.kind == 'report_evidence_origin_v1',
        Job.data['payload']['new_draft_id'].as_string() == row.id).limit(2)
    records = db.scalars(marker_query.with_for_update() if lock else marker_query).all()
    record = records[0] if len(records) == 1 else None
    data = record.data if record else None
    if (not record or record.kind != 'report_evidence_origin_v1' or record.status != 'internal'
            or not isinstance(data, dict) or set(data) != {'payload', 'origin_hash'}
            or data['origin_hash'] != digest(data['payload'])):
        _fail('delegated_origin_missing', '受托来源记录缺失或无法核对，未发送')
    link = data['payload']; basis = link.get('basis') if isinstance(link, dict) else None
    if (not isinstance(link, dict) or set(link) != {'schema', 'basis', 'new_draft_id', 'new_job_id',
            'request_id', 'command_hash'} or link['schema'] != ORIGIN or not isinstance(basis, dict)
            or set(basis) != _BASIS_KEYS or basis.get('schema') != 'report-evidence-delegated-basis-v1'
            or (basis.get('operator_asserted_history_hash') is not None
                and (not isinstance(basis['operator_asserted_history_hash'], str)
                     or not re.fullmatch(_SHA, basis['operator_asserted_history_hash'])))
            or basis.get('workspace_id') != p.workspace_id or basis.get('actor_id') != p.user_id
            or link['new_draft_id'] != row.id or link['request_id'] != row.request_id
            or basis.get('new_preview_hash') != row.data['preview']['preview_hash']
            or basis.get('new_messages_hashes') != _new_hashes(row.data['preview'])
            or basis.get('original_source_hash') != row.data['preview']['source_hash']
            or basis.get('original_projection_hash') != row.data['preview']['projection_hash']
            or basis.get('historical_second_send') != 'not_independently_proven'
            or basis.get('source_relationship') != 'same_report_word_and_ocr_views_overlap'
            or basis.get('history_digest_verification') != (
                'not_provided' if basis.get('operator_asserted_history_hash') is None
                else 'operator_asserted_not_independently_verified')
            or basis.get('new_analysis') is not True
            or basis.get('authorization_basis') != 'authenticated_actor_submitted_request'
            or basis.get('user_reviewed_history') is not False or basis.get('user_confirmed_business') is not False
            or basis.get('duplicate_cost_risk') is not True or basis.get('not_original_batch_recovery') is not True):
        _fail('delegated_origin_invalid', '受托来源与新任务无法核对，未发送')
    if record.idempotency_key != _key(basis.get('original_draft_id')):
        _fail('delegated_origin_invalid', '受托来源唯一编号无法核对，未发送')
    history_assessment = _check_history_assessment(basis['history_assessment'], basis)
    audit_query = select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.actor_id == p.user_id, Audit.object_id == row.id, Audit.action == ACTION).limit(2)
    audits = db.scalars(audit_query.with_for_update() if lock else audit_query).all()
    if len(audits) != 1 or audits[0].data != {'origin_hash': data['origin_hash'], 'request_id': row.request_id}:
        _fail('delegated_audit_invalid', '受托来源审计无法核对，未发送')
    initial_job = db.scalar(select(Job).where(Job.id == link['new_job_id'], Job.workspace_id == p.workspace_id))
    from .report_evidence_worker_isolation import isolated
    if (not initial_job or (initial_job.kind != legacy.KIND and not isolated(initial_job))
            or require_current and not isolated(initial_job)
            or initial_job.idempotency_key != 'report-evidence:' + row.request_id
            or initial_job.data.get('actor_id') != p.user_id or initial_job.data.get('report_draft_id') != row.id):
        _fail('delegated_job_invalid', '受托初始任务与草案不一致，未发送')
    old = full.owned(db, p, basis['original_draft_id'], lock)
    original = old.data['preview']; stages = old.data['extractions']
    if (type(basis['original_revision']) is not int or basis['original_revision'] < 1
            or old.version < basis['original_revision']
            or old.data['start_receipt']['receipt_hash'] != basis['original_start_receipt_hash']
            or original['source_hash'] != basis['original_source_hash']
            or original['projection_hash'] != basis['original_projection_hash']
            or [b['messages_hash'] for b in original['batches']] != basis['original_messages_hashes']
            or len(stages) != 2 or not stages[0].get('raw_response')
            or stages[0]['raw_response']['hash'] != basis['original_first_raw_hash']):
        _fail('delegated_original_changed', '原任务的历史来源或原答无法核对')
    if (require_current and (old.version != basis['original_revision'] or old.status != 'failed'
            or old.data['job_id'] != basis['original_job_id']
            or stages[1] != {'batch_id': 'extract-2', 'status': 'pending'})):
        _fail('delegated_original_changed', '原任务或其未处理批次已变化，未发送')
    old_job = _old_job(db, p.workspace_id, basis['original_job_id'], lock=lock)
    if (not old_job or old_job.kind != legacy.KIND
            or old_job.idempotency_key != 'report-full-reading:' + old.request_id
            or old_job.data.get('actor_id') != p.user_id or old_job.data.get('report_draft_id') != old.id):
        _fail('delegated_original_job_changed', '原后台任务的历史归属无法核对')
    if (require_current and (old_job.status != 'failed' or old_job.lease_until is not None
            or old_job.attempts != 1 or old_job.data.get('lease_recoveries', 0) != 0)):
        _fail('delegated_original_job_changed', '原任务可能仍在执行，未发送')
    if require_current and digest(old_job.data) != history_assessment['original_job_data_hash']:
        _fail('delegated_original_job_changed', '原后台任务记录已变化，未发送')
    call_query = select(CloudCall).where(CloudCall.workspace_id == p.workspace_id,
        CloudCall.id == stages[0].get('reserved_call_id'))
    if not db.scalar(call_query.with_for_update() if lock else call_query):
        _fail('delegated_original_ledger_changed', '原回答账本无法核对，未发送')
    raw = raw_view(db, p, old.id, 'extract-1', old.version)
    if raw['status'] != 'available' or raw['finish_reason'] != 'stop' or raw['scope']['raw_hash'] != basis['original_first_raw_hash']:
        _fail('delegated_original_ledger_changed', '原回答或费用回执无法核对，未发送')
    candidates_query = select(FullProvisionalCandidate).where(
        FullProvisionalCandidate.workspace_id == p.workspace_id,
        FullProvisionalCandidate.actor_id == p.user_id,
        FullProvisionalCandidate.draft_id == old.id,
        FullProvisionalCandidate.stage == 'extract-1').limit(2)
    candidates = db.scalars(candidates_query.with_for_update() if lock else candidates_query).all()
    if (len(candidates) != 1 or
            check_provisional(db, p, candidates[0], old)['receipt']['receipt_hash'] != basis['original_first_candidate_receipt_hash']):
        _fail('delegated_candidate_changed', '原待核候选无法核对，未发送')
    if require_current and db.scalar(select(CloudCall.id).where(CloudCall.workspace_id == p.workspace_id,
            CloudCall.data['request_hash'].as_string() == basis['original_messages_hashes'][1]).limit(1)):
        _fail('delegated_original_ledger_changed', '原批次账本出现调用记录，未发送')
    return copy.deepcopy(data)


@router.post('/report-evidence-delegated/preview')
def preview_route(body: LinkedPreviewCommand, request: Request, response: Response, p: legacy.P, db: legacy.DB):
    require(p, 'admin', 'operator'); legacy.workspace_draft(db, p); _lock_authority(db, p, request)
    legacy.guard_existing_paid(db, p)
    if legacy.by_nonce(db, p, body.reading.request_id):
        _fail('nonce_exists', '此编号已有原任务，请按原编号恢复')
    if db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key == _key(body.original_draft_id))):
        _fail('delegated_already_started', '此原任务已有独立读取记录，请按原编号找回')
    value = _proposal(db, p, body)
    # This legacy entry cannot issue a scoped plan even if an isolated test
    # enables dispatch. The separately governed budget-plan route is the only
    # candidate for creating a new delegated task.
    value = {**value, 'can_start':False, 'start_blocker':'分段读取的定向额度及安全派发尚未接通；旧入口不能签发额度，本次只预览。'}
    value['linked_preview_hash'] = digest({key: val for key,val in value.items() if key != 'linked_preview_hash'})
    response.headers['Cache-Control'] = 'private, no-store'
    db.rollback(); return value


@router.post('/report-evidence-delegated', status_code=202)
def start_route(body: LinkedStartCommand, request: Request, response: Response, p: legacy.P, db: legacy.DB):
    require(p, 'admin', 'operator'); legacy.workspace_draft(db, p); _lock_authority(db, p, request)
    response.headers['Cache-Control'] = 'private, no-store'
    prior = legacy.by_nonce(db, p, body.reading.request_id)
    if prior is not None:
        marker = verify_link(db, p, prior)
        if marker['payload']['command_hash'] != _signature(p, body):
            _fail('nonce_conflict', '原编号已用于不同受托范围，未新增任务')
        start = evidence.StartCommand(**body.reading.model_dump(mode='json'),
            preview_hash=prior.data['preview']['preview_hash'], consent=True)
        return evidence.replay_start(prior, p, start)
    _fail('delegated_dispatch_not_ready', '旧入口不能签发定向额度；没有创建任务或调用模型。')


@router.post('/report-evidence-delegated/{identity}/origin')
def origin_route(identity: UUID, response: Response, p: full.ReadP, db: legacy.DB):
    # Match the global generic GET/HEAD raw-data gate after opening the
    # read-only snapshot; POST itself does not bypass that authorization.
    from .map_access import require_config
    require_config(db, p)
    row = evidence.owned(db, p, identity)
    if not row.request_id.startswith('fl_'):
        _fail('delegated_origin_missing', '此任务没有受托来源记录', 404)
    result = verify_link(db, p, row, lock=False)
    response.headers['Cache-Control'] = 'private, no-store'
    return {'schema': ORIGIN, 'origin': result, 'can_apply': False, 'can_publish': False}
