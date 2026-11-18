"""Gated API and atomic transaction for delegated plan issuance.

An authenticated admin may save a bounded preview. Task creation remains closed
in production, as does the separate dispatch gate. No helper calls a provider.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, field_validator

from sqlalchemy import and_, func, inspect, or_, select

from . import cloud_report_plan_allowance as plans
from . import report_evidence_delegated_origin as origin
from . import report_evidence_pipeline as evidence
from . import report_full_reading_pipeline as full
from . import report_reading_drafts as legacy
from .contracts import digest
from .models import Audit, AuthSession, CloudCall, Job, Membership, SetupReportDraft, User
from .report_full_provisional import _lock_authority
from .services import audit, require

router = APIRouter()
MAX_WORKSPACE_SAVED_PREVIEWS = 50000
_RENEWAL_AUTH = object()


class SavedPlanLookup(legacy.SafeContract):
    original_draft_id: UUID
    before_preview_id: UUID | None = None
    page_size: int = Field(default=50, ge=1, le=50, strict=True)


class SavedPlanStart(legacy.SafeContract):
    preview_id: UUID
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def exact_consent(cls, value):
        if value is not True:
            raise ValueError('必须授权这次计划的发送范围')
        return value


class SavedPlanRenew(legacy.SafeContract):
    preview_id: UUID
    request_id: str = Field(pattern=r'^fl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', strict=True)


def _enabled():
    # No environment or request switch. Tests can inject only in isolation.
    return False


def _authority(db, principal, request, *, preview_only=False):
    if not preview_only and not _enabled():
        origin._fail('delegated_budget_start_disabled', '受托读取仍未开放，未创建任务或调用模型')
    require(principal, 'admin')
    legacy.workspace_draft(db, principal)
    _lock_authority(db, principal, request)


def _preview_key(principal, request_id):
    """One stable server-side preview per actor and original request number."""
    return 'delegated-plan-preview-v2:' + digest({
        'actor_id': principal.user_id, 'request_id': request_id})


def _preview_command_hash(principal, command):
    return digest({'kind':'report_evidence_budget_preview_v2',
        'workspace_id':principal.workspace_id, 'actor_id':principal.user_id,
        'body':command.model_dump(mode='json')})


def _saved_preview(db, principal, preview_id, *, lock=False):
    """Return only a current actor's server-issued plan and checked command.

    This verifies the independent audit and persisted values, but intentionally
    does not claim an expired or stale plan can still be started. Actual start
    rebuilds the source, permissions, plan, expiry and original history.
    """
    query=select(Job).where(Job.id==str(preview_id), Job.workspace_id==principal.workspace_id)
    if lock:
        query=query.with_for_update().execution_options(populate_existing=True)
    record=db.scalar(query)
    data=record.data if record and isinstance(record.data,dict) else None
    old_keys={'actor_id','linked_preview_hash','allowance','used_grant_id','command','command_hash'}
    if (not record or record.kind!='cloud_report_plan_preview_v1' or record.status!='internal'
            or not isinstance(data,dict)
            or set(data) not in (old_keys,old_keys|{'source_summary','renewed_from'})
            or data.get('actor_id')!=principal.user_id or not isinstance(data.get('allowance'),dict)):
        origin._fail('delegated_allowance_not_found', '未找到本人保存的额度预览', 404)
    try:
        command=origin.LinkedPreviewCommand.model_validate(data['command'])
        allowance=data['allowance']
        command_hash=_preview_command_hash(principal,command)
        if (record.idempotency_key!=_preview_key(principal,command.reading.request_id)
                or data['command_hash']!=command_hash
                or allowance.get('workspace_id')!=principal.workspace_id
                or allowance.get('actor_id')!=principal.user_id
                or allowance.get('request_id')!=command.reading.request_id
                or allowance.get('allowance_preview_hash')!=digest({k:v for k,v in allowance.items() if k!='allowance_preview_hash'})):
            raise ValueError('preview binding')
    except (ValueError, TypeError, KeyError):
        origin._fail('delegated_allowance_invalid', '保存的额度预览无法核对')
    audits=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
        Audit.object_id==record.id, Audit.action=='cloud.report_plan_preview.created_v1').limit(2)).all()
    expected={'allowance_hash':digest(allowance),
        'linked_preview_hash':data['linked_preview_hash'],'command_hash':command_hash}
    if 'source_summary' in data:
        value=data['source_summary']; selection=command.reading.selection.model_dump(mode='json')
        selection_keys=('source_mode','original_ref','copy_ref','provenance_hash','run_id','generation','run_version')
        coverage_keys=('expected_pages','prepared_pages','selected_pages','word_blocks','ocr_regions',
            'word_characters','ocr_characters')
        coverage=value.get('coverage') if isinstance(value,dict) else None
        versions=coverage.get('versions_count') if isinstance(coverage,dict) else None
        if (not isinstance(value,dict) or set(value)!={'source_hash','selection_hash','selection','coverage'}
                or value.get('source_hash')!=allowance['source_hash']
                or value.get('selection_hash')!=digest(selection)
                or value.get('selection')!={k:selection[k] for k in selection_keys}
                or not isinstance(coverage,dict) or set(coverage)!=set(coverage_keys)|{'versions_count'}
                or any(type(coverage[k]) is not int or coverage[k]<0 for k in coverage_keys)
                or any(coverage[k]!=len(selection['pages']) for k in ('expected_pages','prepared_pages','selected_pages'))
                or not isinstance(versions,dict) or set(versions)!={'raw','edited','confirmed'}
                or any(type(v) is not int or v<0 for v in versions.values())
                or sum(versions.values())!=len(selection['pages'])):
            origin._fail('delegated_allowance_invalid','资料范围摘要无法核对')
        expected.update(source_summary_hash=digest(value),renewed_from=data['renewed_from'])
    if (len(audits)!=1 or audits[0].actor_id!=principal.user_id or audits[0].data!=expected):
        origin._fail('delegated_allowance_invalid', '额度预览审计无法核对')
    return record, command


def _summary(db, principal, record, command):
    """An operator-facing, private snapshot; it does not authorize sending."""
    allowance=record.data['allowance']
    used=record.data['used_grant_id']
    if used is not None:
        grant=db.scalar(select(Job).where(Job.id==used,Job.workspace_id==principal.workspace_id))
        data=grant.data if grant and isinstance(grant.data,dict) else None
        manifest=data.get('manifest') if data else None
        if (not grant or grant.kind!='cloud_report_plan_allowance_v1' or grant.status!='internal'
                or grant.idempotency_key!=plans.plan_key(allowance['job_id'])
                or not isinstance(manifest,dict) or data.get('manifest_hash')!=digest(manifest)
                or manifest.get('allowance_preview_hash')!=allowance['allowance_preview_hash']
                or manifest.get('workspace_id')!=principal.workspace_id
                or manifest.get('actor_id')!=principal.user_id
                or manifest.get('draft_id')!=allowance['draft_id']
                or manifest.get('job_id')!=allowance['job_id']):
            origin._fail('delegated_allowance_invalid', '已保存任务的额度记录无法核对')
        issued=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
            Audit.object_id==grant.id,Audit.action=='cloud.report_plan_allowance.issued_v1').limit(2)).all()
        if (len(issued)!=1 or issued[0].id!=data.get('audit_id') or issued[0].actor_id!=principal.user_id
                or issued[0].data!={'schema':'delegated-evidence-plan-issue-v1','grant_id':grant.id,
                    'manifest_hash':data['manifest_hash'],
                    'allowance_preview_hash':allowance['allowance_preview_hash']}):
            origin._fail('delegated_allowance_invalid', '已保存任务的额度审计无法核对')
        row=evidence.owned(db,principal,allowance['draft_id'])
        marker=origin.verify_link(db,principal,row,lock=False)
        if (row.request_id!=command.reading.request_id or marker['payload']['new_job_id']!=allowance['job_id']):
            origin._fail('delegated_allowance_invalid', '已保存任务与原预览无法核对')
    try:
        expires=datetime.fromisoformat(allowance['expires_at'])
        clock=db.scalar(select(func.clock_timestamp()))
        if expires.tzinfo is None or expires.utcoffset() is None:
            raise ValueError('timezone')
        status='started' if used is not None else ('expired' if clock>=expires else 'saved')
    except (KeyError,TypeError,ValueError,OverflowError):
        origin._fail('delegated_allowance_invalid', '额度预览有效期无法核对')
    # Availability is derived from the exact server-saved plan; absence or a
    # revoked/expired activation keeps the default closed. This preview alone
    # never grants a model call.
    activated=False
    if status=='saved':
        from . import report_evidence_scoped_activation as scope
        try:
            scope.for_preview(db,principal,record,lock=False)
            activated=True
        except HTTPException:
            pass
    source=record.data.get('source_summary')
    result={'schema':'report-evidence-budget-plan-v1', 'preview_id':record.id,
        'workspace_id':record.workspace_id, 'actor_id':record.data['actor_id'],
        'original_draft_id':str(command.original_draft_id), 'original_revision':command.original_revision,
        'request_id':command.reading.request_id, 'linked_preview_hash':record.data['linked_preview_hash'],
        'command_hash':record.data['command_hash'],
        'source_summary_verified':source is not None,
        'source_hash':source['source_hash'] if source else None,
        'selection_hash':source['selection_hash'] if source else None,
        'selection':source['selection'] if source else None,
        'coverage':source['coverage'] if source else None,
        'renewed_from':record.data.get('renewed_from'),
        'allowance_preview_hash':allowance['allowance_preview_hash'],
        'model_id':allowance['model_id'], 'model_hash':allowance['model_hash'],
        'max_tokens':allowance['max_tokens'], 'max_calls':allowance['max_calls'],
        'not_before':allowance['not_before'], 'expires_at':allowance['expires_at'],
        'draft_id':allowance['draft_id'] if used is not None else None,
        'status':status,
        'historical_second_send':'not_independently_proven',
        'source_relationship':'same_report_word_and_ocr_views_overlap',
        'not_original_batch_recovery':True, 'duplicate_cost_risk':True,
        'previous_records_preserved':True, 'price_limit_available':False,
        'refresh_preview_when_dispatch_changes':True,
        # An absent usage marker does not rule out orphaned tasks, receipts,
        # ledger entries or provider calls. Only a verified present marker
        # positively establishes issuance; execution and cost remain unknown.
        'user_confirmed_business':False, 'allowance_issued':True if used is not None else None,
        'usage_marker':'verified_issued' if used is not None else 'not_recorded',
        'budget_reserved':None, 'model_called':None, 'execution_history':'unknown',
        'preview_action_calls_model':False,
        'can_start':activated, 'can_send':activated,
        'start_blocker':(None if activated or used is not None else
                         '此计划尚未启用，或启用已撤销或过期；不会创建任务或调用模型。')}
    return {**result,'summary_hash':digest(result)}


def preview_tx(db, principal, request, command, *, renewed_from=None, renewal_auth=None):
    """Rebuild the complete plan from stored source and current authority."""
    _authority(db, principal, request, preview_only=True)
    if renewed_from is not None and renewal_auth is not _RENEWAL_AUTH:
        origin._fail('delegated_renewal_not_allowed','续期须先核对原预览')
    if type(command) is not origin.LinkedPreviewCommand:
        origin._fail('delegated_command_invalid', '受托读取请求无效')
    existing=db.scalar(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.idempotency_key==_preview_key(principal,command.reading.request_id)).with_for_update())
    if existing is not None:
        record, prior_command=_saved_preview(db,principal,existing.id,lock=True)
        if _preview_command_hash(principal,prior_command)!=_preview_command_hash(principal,command):
            origin._fail('nonce_conflict', '原预览编号已用于不同范围，未新增任务')
        if record.data.get('renewed_from')!=renewed_from:
            origin._fail('nonce_conflict','原预览编号已用于不同续期范围')
        return {'preview_id':record.id,'linked_preview_hash':record.data['linked_preview_hash'],
                'allowance':record.data['allowance'], 'can_send':False,
                'historical_second_send':'not_independently_proven'}
    # The workspace lock in _authority serializes these limits across requests.
    # Old records and their audit are retained; no cleanup touches source data.
    clock=db.scalar(select(func.clock_timestamp()))
    total=db.scalar(select(func.count()).select_from(Job).where(Job.workspace_id==principal.workspace_id,
        Job.kind=='cloud_report_plan_preview_v1'))
    if total>=MAX_WORKSPACE_SAVED_PREVIEWS:
        origin._fail('delegated_preview_storage_limit', '保存的预览已达容量上限，请联系管理员处理', 429)
    recent=select(func.count()).select_from(Job).where(Job.workspace_id==principal.workspace_id,
        Job.kind=='cloud_report_plan_preview_v1', Job.created_at>=clock-timedelta(hours=24))
    if (db.scalar(recent)>=100 or db.scalar(recent.where(Job.created_at>=clock-timedelta(hours=1)))>=20):
        origin._fail('delegated_preview_rate_limited', '预览次数已达上限，请稍后重试', 429)
    legacy.guard_existing_paid(db, principal)
    if legacy.by_nonce(db, principal, command.reading.request_id):
        origin._fail('nonce_exists', '此编号已有任务')
    if db.scalar(select(Job.id).where(Job.workspace_id == principal.workspace_id,
                                     Job.idempotency_key == origin._key(command.original_draft_id))):
        origin._fail('delegated_already_started', '此原任务已有独立读取记录')
    # One lineage per old draft. A new request number is only permitted through
    # the guarded renewal path; this prevents bypassing an uncertain outcome.
    unbound=db.scalar(select(Job.id).where(Job.workspace_id==principal.workspace_id,
        Job.kind=='cloud_report_plan_preview_v1',
        Job.data['command']['original_draft_id'].as_string().is_(None)).limit(1))
    if unbound is not None:
        origin._fail('delegated_preview_history_unknown','旧预览无法核对来源，请先恢复原记录')
    previous=db.scalar(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.kind=='cloud_report_plan_preview_v1', Job.status=='internal',
        Job.data['command']['original_draft_id'].as_string()==str(command.original_draft_id))
        .order_by(Job.created_at.desc(),Job.id.desc()).limit(1))
    if previous is not None and renewed_from is None:
        origin._fail('delegated_preview_exists','该报告已有预览，请找回原预览或按规则续期')
    if renewed_from is not None and (previous is None or previous.id!=renewed_from):
        origin._fail('delegated_renewal_conflict','已有更新的预览，请找回原记录')
    proposal=origin._proposal(db,principal,command)
    evidence._require_current_map(proposal['reading_preview'])
    issued_at=db.scalar(select(func.clock_timestamp()))
    allowance=plans.build_allowance_preview(principal,proposal['reading_preview'],proposal['basis'],
                                            str(uuid4()),str(uuid4()),issued_at)
    source=proposal['reading_preview']['source_snapshot']
    selection=command.reading.selection.model_dump(mode='json')
    coverage=source['coverage']
    source_summary={'source_hash':allowance['source_hash'],'selection_hash':digest(selection),
        'selection':{k:selection[k] for k in ('source_mode','original_ref','copy_ref',
            'provenance_hash','run_id','generation','run_version')},
        'coverage':{k:coverage[k] for k in ('expected_pages','prepared_pages','selected_pages',
            'word_blocks','ocr_regions','word_characters','ocr_characters','versions_count')}}
    preview_id=str(uuid4())
    command_hash=_preview_command_hash(principal,command)
    record=Job(id=preview_id,workspace_id=principal.workspace_id,kind='cloud_report_plan_preview_v1',
        status='internal',idempotency_key=_preview_key(principal,command.reading.request_id),
        data={'actor_id':principal.user_id,'linked_preview_hash':proposal['linked_preview_hash'],
              'allowance':allowance,'used_grant_id':None,
              'command':command.model_dump(mode='json'),'command_hash':command_hash,
              'source_summary':source_summary,'renewed_from':renewed_from})
    receipt=Audit(workspace_id=principal.workspace_id,actor_id=principal.user_id,
        action='cloud.report_plan_preview.created_v1',object_id=preview_id,
        data={'allowance_hash':digest(allowance),'linked_preview_hash':proposal['linked_preview_hash'],
              'command_hash':command_hash,'source_summary_hash':digest(source_summary),'renewed_from':renewed_from})
    db.add_all([record,receipt])
    db.flush()
    from copy import deepcopy
    from . import cloud_scoped_extra_reservation as scoped
    actor=db.get(User,principal.user_id);session_row=db.get(AuthSession,principal.session_id)
    member=db.scalar(select(Membership).where(Membership.user_id==principal.user_id,
        Membership.workspace_id==principal.workspace_id))
    objects=(record,receipt,actor,session_row,member)
    if any(obj is None for obj in objects):plans._deny()
    snapshots=[{col.key:deepcopy(getattr(obj,col.key)) for col in inspect(obj).mapper.column_attrs} for obj in objects]
    tx=db.get_transaction();checks=scoped._session_checks(db)
    if tx is None or tx in checks:plans._deny()
    def check_preview_commit(session):
        if session.get_transaction() is not tx:plans._deny()
        for obj,snapshot in zip(objects,snapshots):
            state=inspect(obj)
            if (obj in session.deleted or state.deleted or state.detached or not state.persistent
                    or state.session is not session or state.expired_attributes
                    or any(getattr(obj,k)!=v for k,v in snapshot.items())):plans._deny()
        if session_row.expires_at<=session.scalar(select(func.clock_timestamp())):plans._deny()
    checks[tx]=check_preview_commit
    return {'preview_id':preview_id,'linked_preview_hash':proposal['linked_preview_hash'],'allowance':allowance,
            'can_send':False,'historical_second_send':'not_independently_proven'}


def start_tx(db, principal, request, command, *, preview_id, allowance_preview):
    """Prepare one linked task and all permits; caller commits or rolls back.

    Every amount is rebuilt from persisted server inputs. Submitted preview
    only confirms that derived plan, never authorizes a client-chosen amount.
    """
    # A closed creation gate must not prevent recovery of a committed receipt.
    # The path below validates actor, workspace, origin, preview and grant first.
    _authority(db,principal,request,preview_only=True)
    if type(command) is not origin.LinkedStartCommand or type(allowance_preview) is not dict:
        origin._fail('delegated_command_invalid', '受托读取请求无效')
    try:
        draft_id=allowance_preview['draft_id'];job_id=allowance_preview['job_id']
        approved_hash=allowance_preview['allowance_preview_hash']
        issued_at=datetime.fromisoformat(allowance_preview['not_before'])
    except (KeyError,TypeError,ValueError):
        origin._fail('delegated_allowance_invalid', '额度预览无效')
    preview, saved_command = _saved_preview(db, principal, preview_id, lock=True)
    command_without_start=origin.LinkedPreviewCommand(
        **command.model_dump(mode='json',exclude={'linked_preview_hash','consent'}))
    if (preview.data['command_hash']!=_preview_command_hash(principal,command_without_start)
            or preview.data.get('linked_preview_hash')!=command.linked_preview_hash
            or preview.data.get('allowance')!=allowance_preview):
        origin._fail('delegated_allowance_invalid', '未找到已保存的服务端额度预览')
    audits=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,Audit.object_id==preview_id,
        Audit.action=='cloud.report_plan_preview.created_v1').limit(2).with_for_update()
        .execution_options(populate_existing=True)).all()
    expected_audit={'allowance_hash':digest(allowance_preview),
        'linked_preview_hash':command.linked_preview_hash,
        'command_hash':_preview_command_hash(principal,saved_command)}
    if 'source_summary' in preview.data:
        expected_audit.update(source_summary_hash=digest(preview.data['source_summary']),
            renewed_from=preview.data['renewed_from'])
    if (len(audits)!=1 or audits[0].actor_id!=principal.user_id or audits[0].data!=expected_audit):
        origin._fail('delegated_allowance_invalid', '额度预览审计无法核对')
    preview_audit=audits[0]
    prior=legacy.by_nonce(db,principal,command.reading.request_id)
    if prior is not None:
        marker=origin.verify_link(db,principal,prior)
        summary=_summary(db,principal,preview,saved_command)
        grant=db.scalar(select(Job).where(Job.workspace_id==principal.workspace_id,
            Job.idempotency_key==plans.plan_key(marker['payload']['new_job_id'])))
        if (marker['payload']['command_hash']!=origin._signature(principal,command)
                or prior.id!=draft_id or marker['payload']['new_job_id']!=job_id
                or summary['status']!='started' or summary['draft_id']!=prior.id
                or not grant or preview.data.get('used_grant_id')!=grant.id
                or grant.data.get('manifest',{}).get('allowance_preview_hash')!=approved_hash):
            origin._fail('nonce_conflict', '原编号已用于不同受托范围')
        start=evidence.StartCommand(**command.reading.model_dump(mode='json'),
                                    preview_hash=prior.data['preview']['preview_hash'],consent=True)
        return {'result':evidence.replay_start(prior,principal,start),'grant_id':grant.id,'replayed':True}
    if preview.data.get('used_grant_id') is not None:
        origin._fail('delegated_allowance_invalid', '额度预览已使用')
    latest=db.scalar(select(Job.id).where(Job.workspace_id==principal.workspace_id,
        Job.kind=='cloud_report_plan_preview_v1',Job.status=='internal',
        Job.data['command']['original_draft_id'].as_string()==str(command.original_draft_id))
        .order_by(Job.created_at.desc(),Job.id.desc()).limit(1))
    if latest!=preview.id:
        origin._fail('delegated_preview_superseded','已有更新的预览，不能启动旧预览')
    from .report_evidence_delegated_dispatch_gate import ready
    activation=None
    if not (_enabled() and ready()):
        # The production path is only the exact, persisted activation. The
        # row stays locked through the transaction that issues the plan.
        from . import report_evidence_scoped_activation as scope
        activation=scope.for_preview(db,principal,preview,lock=True)
    legacy.guard_existing_paid(db,principal)
    if db.scalar(select(Job.id).where(Job.workspace_id==principal.workspace_id,
                                     Job.idempotency_key==origin._key(command.original_draft_id))):
        origin._fail('delegated_already_started', '此原任务已有独立读取记录')
    proposal=origin._proposal(db,principal,origin.LinkedPreviewCommand(
        original_draft_id=command.original_draft_id,original_revision=command.original_revision,
        operator_asserted_history_hash=command.operator_asserted_history_hash,reading=command.reading))
    if proposal['linked_preview_hash']!=command.linked_preview_hash:
        origin._fail('delegated_preview_changed', '来源或范围已变化，未创建任务')
    value=proposal['reading_preview'];evidence._require_current_map(value)
    rebuilt=plans.build_allowance_preview(principal,value,proposal['basis'],draft_id,job_id,issued_at)
    if rebuilt!=allowance_preview:
        origin._fail('delegated_allowance_changed', '额度预览已变化，未创建任务')
    token=plans.arm_issuance(db,principal,request,draft_id,job_id,
                             approved_preview_hash=approved_hash,consent=command.consent)
    start=evidence.StartCommand(**command.reading.model_dump(mode='json'),
                                preview_hash=value['preview_hash'],consent=True)
    row=SetupReportDraft(id=draft_id,workspace_id=principal.workspace_id,actor_id=principal.user_id,
                         request_id=command.reading.request_id,status='queued')
    job=Job(id=job_id,workspace_id=principal.workspace_id,kind=legacy.KIND,
            idempotency_key='report-evidence:'+row.request_id,
            data={'actor_id':principal.user_id,'report_draft_id':row.id})
    db.add(job);db.flush();row.data=evidence._new_data(principal,row.id,start,value,job.id)
    evidence._state_size(row.data);db.add(row);db.flush()
    link={'schema':origin.ORIGIN,'basis':proposal['basis'],'new_draft_id':row.id,'new_job_id':job.id,
          'request_id':row.request_id,'command_hash':origin._signature(principal,command)}
    origin_hash=digest(link)
    db.add(Job(workspace_id=principal.workspace_id,kind='report_evidence_origin_v1',status='internal',
               idempotency_key=origin._key(command.original_draft_id),data={'payload':link,'origin_hash':origin_hash}))
    audit(db,principal,'setup.report_evidence.authorize',row.id,
          {'request_id':row.request_id,'preview_hash':value['preview_hash'],'map_calls':value['input_summary']['map_call_count']})
    audit(db,principal,origin.ACTION,row.id,{'origin_hash':origin_hash,'request_id':row.request_id})
    db.flush();origin.verify_link(db,principal,row,require_current=True)
    grant_id=plans.issue_plan_tx(db,principal,request,row.id,job.id,rebuilt,token)
    preview.data={**preview.data,'used_grant_id':grant_id}
    # Keep the trusted preview linked through commit, alongside issuance's
    # own commit-time invariants; never refresh away uncommitted tampering.
    from copy import deepcopy
    from . import cloud_scoped_extra_reservation as scoped
    expected=deepcopy(preview.data);tx=db.get_transaction();checks=scoped._session_checks(db)
    audit_snapshot={col.key:deepcopy(getattr(preview_audit,col.key)) for col in inspect(preview_audit).mapper.column_attrs}
    activation_snapshots=[] if activation is None else [
        (obj,{col.key:deepcopy(getattr(obj,col.key)) for col in inspect(obj).mapper.column_attrs})
        for obj in activation[:2]]
    issuance_check=checks[tx]
    def check_preview_at_commit(session):
        state=inspect(preview)
        audit_state=inspect(preview_audit)
        if (session.get_transaction() is not tx or preview in session.deleted or state.deleted
                or state.detached or not state.persistent or state.session is not session
                or state.expired_attributes or preview.id!=preview_id
                or preview.workspace_id!=principal.workspace_id or preview.kind!='cloud_report_plan_preview_v1'
                or preview.status!='internal'
                or preview.idempotency_key!=_preview_key(principal,command.reading.request_id)
                or preview.data!=expected or preview_audit in session.deleted or audit_state.deleted
                or audit_state.detached or not audit_state.persistent or audit_state.session is not session
                or audit_state.expired_attributes
                or any(getattr(preview_audit,key)!=value for key,value in audit_snapshot.items())):
            plans._deny()
        if activation is not None:
            if session.scalar(select(func.clock_timestamp())) >= activation[2]:
                plans._deny()
            for obj, columns in activation_snapshots:
                state=inspect(obj)
                if (obj in session.deleted or state.deleted or state.detached or not state.persistent
                        or state.session is not session or state.expired_attributes
                        or any(getattr(obj,key)!=value for key,value in columns.items())):
                    plans._deny()
        issuance_check(session)
    checks[tx]=check_preview_at_commit
    return {'result':evidence.replay_start(row,principal,start),'grant_id':grant_id,'replayed':False}


def renew_tx(db, principal, request, body):
    """Create a new preview only after verifying an expired plan never started."""
    _authority(db,principal,request,preview_only=True)
    old,command=_saved_preview(db,principal,body.preview_id,lock=True)
    data=command.model_dump(mode='json')
    data['reading']['request_id']=body.request_id
    new_command=origin.LinkedPreviewCommand.model_validate(data)
    existing=db.scalar(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.idempotency_key==_preview_key(principal,body.request_id)).with_for_update())
    if existing is not None:
        checked,prior=_saved_preview(db,principal,existing.id,lock=True)
        if (checked.data.get('renewed_from')!=old.id
                or _preview_command_hash(principal,prior)!=_preview_command_hash(principal,new_command)):
            origin._fail('nonce_conflict','续期编号已用于其他范围')
        return checked,prior,True
    summary=_summary(db,principal,old,command)
    allowance=old.data['allowance']
    if summary['status']!='expired' or old.data['used_grant_id'] is not None:
        origin._fail('delegated_renewal_not_allowed','仅未启动且已过期的预览可以续期')
    # All known execution and linkage footprints must be absent. Any partial
    # or inconsistent state is unknown and is never treated as an unsent task.
    if (legacy.by_nonce(db,principal,command.reading.request_id) is not None
            or db.scalar(select(SetupReportDraft.id).where(SetupReportDraft.workspace_id==principal.workspace_id,
                SetupReportDraft.id==allowance['draft_id'])) is not None
            or db.scalar(select(Job.id).where(Job.workspace_id==principal.workspace_id,
                or_(Job.id==allowance['job_id'],Job.idempotency_key==plans.plan_key(allowance['job_id']),
                    Job.idempotency_key=='report-evidence:'+command.reading.request_id,
                    Job.idempotency_key==origin._key(command.original_draft_id),
                    Job.data['manifest']['job_id'].as_string()==allowance['job_id']))) is not None
            or db.scalar(select(Audit.id).where(Audit.workspace_id==principal.workspace_id,
                Audit.action=='cloud.report_plan_allowance.issued_v1',
                Audit.data['allowance_preview_hash'].as_string()==allowance['allowance_preview_hash'])) is not None
            or db.scalar(select(CloudCall.id).where(CloudCall.workspace_id==principal.workspace_id,
                or_(CloudCall.data['scoped_extra']['job_id'].as_string()==allowance['job_id'],
                    CloudCall.data['request_hash'].as_string().in_([s['request_hash'] for s in allowance['stages']])))) is not None):
        origin._fail('delegated_renewal_outcome_unknown','任务状态无法证明未启动，请先核对原记录')
    result=preview_tx(db,principal,request,new_command,renewed_from=old.id,renewal_auth=_RENEWAL_AUTH)
    checked,prior=_saved_preview(db,principal,result['preview_id'])
    return checked,prior,False


@router.post('/report-evidence-delegated/budget-plan/preview')
def budget_preview_route(body: origin.LinkedPreviewCommand, request: Request, response: Response,
                         p: legacy.P, db: legacy.DB):
    """Persist one recoverable plan; preview never creates a task or calls a model."""
    value=preview_tx(db,p,request,body)
    record, command=_saved_preview(db,p,value['preview_id'])
    summary=_summary(db,p,record,command)
    db.commit()
    response.headers['Cache-Control']='private, no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return summary


@router.post('/report-evidence-delegated/budget-plan/find')
def budget_find_route(body: SavedPlanLookup, response: Response, p: full.ReadP, db: legacy.DB):
    """Find one's saved plans after refresh or a change of browser."""
    from .map_access import require_config
    require(p,'admin'); require_config(db,p)
    original=db.scalar(select(SetupReportDraft.id).where(
        SetupReportDraft.id==str(body.original_draft_id), SetupReportDraft.workspace_id==p.workspace_id,
        SetupReportDraft.actor_id==p.user_id))
    if original is None:
        origin._fail('delegated_original_not_found', '未找到本人的原报告记录', 404)
    query=select(Job).where(Job.workspace_id==p.workspace_id,
        Job.kind=='cloud_report_plan_preview_v1', Job.status=='internal',
        Job.data['actor_id'].as_string()==p.user_id,
        Job.data['command']['original_draft_id'].as_string()==str(body.original_draft_id))
    if body.before_preview_id is not None:
        cursor,cursor_command=_saved_preview(db,p,body.before_preview_id)
        if cursor_command.original_draft_id!=body.original_draft_id:
            origin._fail('delegated_allowance_not_found','未找到本人保存的额度预览',404)
        query=query.where(or_(Job.created_at<cursor.created_at,
            and_(Job.created_at==cursor.created_at,Job.id<cursor.id)))
    records=db.scalars(query.order_by(Job.created_at.desc(),Job.id.desc()).limit(body.page_size+1)).all()
    items=[]
    for item in records[:body.page_size]:
        checked,command=_saved_preview(db,p,item.id)
        items.append(_summary(db,p,checked,command))
    response.headers['Cache-Control']='private, no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return {'schema':'report-evidence-budget-plan-list-v1','workspace_id':p.workspace_id,
        'actor_id':p.user_id,'original_draft_id':str(body.original_draft_id),
        'truncated':len(records)>body.page_size,
        'next_before_preview_id':items[-1]['preview_id'] if len(records)>body.page_size else None,
        'items':items}


@router.post('/report-evidence-delegated/budget-plan/renew')
def budget_renew_route(body: SavedPlanRenew, request: Request, response: Response,
                       p: legacy.P, db: legacy.DB):
    record,command,replayed=renew_tx(db,p,request,body)
    summary=_summary(db,p,record,command)
    db.commit()
    response.headers['Cache-Control']='private, no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return {'schema':'report-evidence-budget-renew-v1','replayed':replayed,'plan':summary}


@router.post('/report-evidence-delegated/budget-plan/start', status_code=202)
def budget_start_route(body: SavedPlanStart, request: Request, response: Response,
                       p: legacy.P, db: legacy.DB):
    """Start only the exact server-saved plan, with dispatch still gated."""
    _authority(db,p,request,preview_only=True)
    record, command=_saved_preview(db,p,body.preview_id,lock=True)
    start=origin.LinkedStartCommand(**command.model_dump(mode='json'),
        linked_preview_hash=record.data['linked_preview_hash'],consent=body.consent)
    result=start_tx(db,p,request,start,preview_id=str(body.preview_id),
        allowance_preview=record.data['allowance'])
    row=result['result']['draft']
    db.commit()
    response.headers['Cache-Control']='private, no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return {'schema':'report-evidence-budget-start-v1','preview_id':str(body.preview_id),
        'draft_id':row['id'],'request_id':row['request_id'],'status':row['status'],
        'revision':row['revision'],'replayed':result['replayed'],
        'user_confirmed_business':False,'can_apply':False,'can_publish':False}
