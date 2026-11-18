"""Closed, task-bound budget contracts for delegated evidence reads.

No endpoint, gateway or worker is registered by importing this module.  The
caller must establish authentication, the report-origin link, and the lease
fence in its own transaction.  No function here performs network IO or commits.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from .contracts import digest
from .leases import fenced, ownership
from .models import Audit, AuthSession, Capability, CloudCall, Job, Membership, SetupReportDraft, User
from .security import Principal

SCHEMA = 'delegated-evidence-plan-allowance-v1'
RECORD = 'delegated-evidence-plan-allowance-record-v1'
SCOPE = 'delegated-evidence-map-v1'
MAX_STAGES = 24
MAX_TOKENS = 2_000_000
MAX_TTL = timedelta(hours=24)
_HASH = re.compile(r'^[a-f0-9]{64}$')


class PlanAllowanceDenied(ValueError):
    pass


def _deny():
    raise PlanAllowanceDenied('本次分段范围、额度或后台任务无法安全核对；未授予调用权限')


def plan_key(job_id):
    return 'evidence-plan-allowance-v1:' + digest({'job_id': job_id})


def scope_hash(workspace_id, actor_id, draft_id, job_id, source_hash, preview_hash, origin_hash, stage_id):
    return digest({'schema': SCOPE, 'workspace_id': workspace_id, 'actor_id': actor_id,
        'draft_id': draft_id, 'job_id': job_id, 'source_hash': source_hash,
        'preview_hash': preview_hash, 'origin_hash': origin_hash, 'stage_id': stage_id})


def plan_from_preview(preview):
    """Extract every frozen map stage without accepting caller-supplied bounds."""
    try:
        prep = preview['map_preparation']
        contract = prep['generation_contract']
        chunks = prep['plan']['chunks']
        if (type(chunks) is not list or not 1 <= len(chunks) <= MAX_STAGES
                or contract['schema'] != 'report-evidence-map-generation-v3'):
            _deny()
        options_hash = digest(contract['dispatch_options'])
        policy_hash = digest(contract['input_policy'])
        stages = []
        for chunk in chunks:
            sid, rh = chunk['chunk_id'], chunk['messages_hash']
            bound = chunk['input_summary']['token_bound']
            if (type(sid) is not str or not sid or len(sid) > 120 or type(rh) is not str
                    or not _HASH.fullmatch(rh) or type(bound) is not int or bound < 1):
                _deny()
            stages.append({'stage_id': sid, 'request_hash': rh, 'options_hash': options_hash,
                           'input_policy_hash': policy_hash, 'token_bound': bound})
        if (len({x['stage_id'] for x in stages}) != len(stages)
                or len({x['request_hash'] for x in stages}) != len(stages)
                or sum(x['token_bound'] for x in stages) > MAX_TOKENS):
            _deny()
        return stages
    except (KeyError, TypeError, AttributeError, OverflowError):
        _deny()


def validate_progress(stages, consumed, calls, *, workspace_id, actor_id, capability_id, job_id):
    """Bound a plan by immutable reservation totals and settled prior calls.

    ``calls`` must be database rows locked *before* the daily budget row.  A
    missing, reserved, failed, unknown, mismatched or overspent prior call
    blocks further reservations.  Actual provider charges are not money caps.
    """
    if (type(stages) is not list or type(consumed) is not list or type(calls) is not dict
            or any(type(v) is not str or not v for v in (workspace_id,actor_id,capability_id,job_id))):
        _deny()
    if len(consumed) > len(stages):
        _deny()
    total = 0
    for index, item in enumerate(consumed):
        if (type(item) is not dict or set(item) != {'stage_id','call_id','token_bound'}
                or item['stage_id'] != stages[index]['stage_id']
                or item['token_bound'] != stages[index]['token_bound']
                or type(item['call_id']) is not str or not item['call_id']):
            _deny()
        call = calls.get(item['call_id'])
        receipt = call.data.get('receipt') if call and isinstance(call.data,dict) else None
        usage = receipt.get('usage') if isinstance(receipt,dict) else None
        actual = usage.get('total_tokens') if isinstance(usage,dict) else None
        if (not call or call.status != 'succeeded' or call.reserved_tokens != item['token_bound']
                or type(call.charged_tokens) is not int or not 0 <= call.charged_tokens <= item['token_bound']
                or call.workspace_id != workspace_id or call.capability_id != capability_id
                or not isinstance(call.data, dict) or call.data.get('actor_id') != actor_id
                or not isinstance(call.data.get('scoped_extra'),dict)
                or call.data['scoped_extra'].get('job_id') != job_id
                or call.data['scoped_extra'].get('stage_id') != item['stage_id']
                or call.data.get('request_hash') != stages[index]['request_hash']
                or call.data.get('dispatch_options_hash') != stages[index]['options_hash']
                or call.data.get('input_policy_hash') != stages[index]['input_policy_hash']
                or not isinstance(receipt,dict) or receipt.get('call_id') != item['call_id']
                or type(actual) is not int or actual < 0 or call.charged_tokens != actual):
            _deny()
        total += item['token_bound']
    if len(calls) != len(consumed) or total > sum(x['token_bound'] for x in stages):
        _deny()
    return total



_ARM_KEY = 'delegated_report_plan_issue_arm_v1'
_ARM_SEAL = object()


def build_allowance_preview(principal, reading_preview, basis, draft_id, job_id, issued_at):
    """Create an exact plan summary from an already server-rebuilt preview.

    The caller must first authenticate and run the evidence and origin
    preview builders. Identifiers and source/basis hashes are bound; no client
    value can select or enlarge a token bound, call count, or validity period.
    """
    from uuid import UUID
    from . import report_evidence_pipeline as evidence
    try:
        if type(principal) is not Principal or principal.role != 'admin':
            _deny()
        for value in (draft_id, job_id):
            if type(value) is not str or str(UUID(value)) != value:
                _deny()
        if (not isinstance(issued_at, datetime) or issued_at.tzinfo is None
                or issued_at.utcoffset() is None or not isinstance(basis, dict)):
            _deny()
        checked = evidence.check_preview(deepcopy(reading_preview), principal)
        if (not checked['body']['request_id'].startswith('fl_')
                or basis.get('workspace_id') != principal.workspace_id
                or basis.get('actor_id') != principal.user_id
                or basis.get('new_preview_hash') != checked['preview_hash']
                or basis.get('original_source_hash') != checked['source_hash']
                or basis.get('user_confirmed_business') is not False
                or basis.get('user_reviewed_history') is not False
                or basis.get('historical_second_send') != 'not_independently_proven'
                or basis.get('duplicate_cost_risk') is not True):
            _deny()
        stages = plan_from_preview(checked)
        if basis.get('new_messages_hashes') != [s['request_hash'] for s in stages]:
            _deny()
        start = issued_at.astimezone(timezone.utc)
        value = {'schema':'delegated-evidence-plan-preview-v1',
            'workspace_id':principal.workspace_id, 'actor_id':principal.user_id,
            'draft_id':draft_id, 'job_id':job_id, 'request_id':checked['body']['request_id'],
            'source_hash':checked['source_hash'], 'reading_preview_hash':checked['preview_hash'],
            'basis_hash':digest(basis), 'model_id':checked['body']['model_id'],
            'model_hash':checked['published_model_hash'], 'stages':stages,
            'max_tokens':sum(item['token_bound'] for item in stages), 'max_calls':len(stages),
            'not_before':start.isoformat(), 'expires_at':(start + MAX_TTL).isoformat(),
            'old_second_send':'not_independently_proven', 'duplicate_cost_risk':True,
            'user_confirmed_business':False, 'can_send':False}
        return {**value, 'allowance_preview_hash':digest(value)}
    except (KeyError, TypeError, AttributeError, OverflowError):
        _deny()


def arm_issuance(db, principal, request, draft_id, job_id, *, approved_preview_hash, consent):
    """Bind future *new* IDs to this authenticated, still-open transaction.

    Call before inserting either row; the same transaction must later create
    the report, job, origin, audits and grant. Losing the session or rolling
    back invalidates the arm. This is an internal API, not an HTTP endpoint.
    """
    from uuid import UUID
    from . import report_reading_drafts as legacy
    from .report_full_provisional import _lock_authority
    if (not isinstance(db, Session) or type(principal) is not Principal or principal.role != 'admin'
            or request is None or consent is not True or type(approved_preview_hash) is not str
            or not _HASH.fullmatch(approved_preview_hash)):
        _deny()
    try:
        if any(type(v) is not str or str(UUID(v)) != v for v in (draft_id,job_id)):
            _deny()
    except (ValueError,TypeError):
        _deny()
    legacy.workspace_draft(db, principal)
    _lock_authority(db, principal, request)
    tx = db.get_transaction()
    prior = db.info.get(_ARM_KEY)
    if prior is not None and not isinstance(prior,dict):
        _deny()
    if prior is not None and prior.get('tx') is not tx:
        db.info.pop(_ARM_KEY,None)
    if tx is None or _ARM_KEY in db.info or db.scalar(select(SetupReportDraft.id).where(SetupReportDraft.id==draft_id)) is not None or db.scalar(select(Job.id).where(Job.id==job_id)) is not None:
        _deny()
    token = object()
    db.info[_ARM_KEY] = {'seal':_ARM_SEAL, 'token':token, 'tx':tx, 'principal':principal,
                         'draft_id':draft_id, 'job_id':job_id, 'approved_preview_hash':approved_preview_hash}
    return token


def issue_plan_tx(db, principal, request, draft_id, job_id, allowance_preview, token):
    """Issue a server-calculated plan and per-stage permits in the armed tx.

    Does not commit or send. The caller must commit the linked report, job,
    origin, audit and all grants together. No production endpoint calls this
    function yet; the authenticated request must approve this derived plan.
    """
    from . import report_evidence_pipeline as evidence
    from . import report_evidence_delegated_origin as origin
    from . import report_reading_drafts as legacy
    from . import cloud_scoped_extra_reservation as scoped
    from .report_full_provisional import _lock_authority
    if not isinstance(db, Session) or type(principal) is not Principal or principal.role != 'admin' or request is None:
        _deny()
    armed = db.info.get(_ARM_KEY)
    if (not isinstance(armed,dict) or armed.get('seal') is not _ARM_SEAL or armed.get('token') is not token
            or armed.get('tx') is not db.get_transaction() or armed.get('principal') != principal
            or armed.get('draft_id') != draft_id or armed.get('job_id') != job_id
            or not isinstance(allowance_preview,dict)
            or armed.get('approved_preview_hash') != allowance_preview.get('allowance_preview_hash')):
        _deny()
    legacy.workspace_draft(db, principal)
    _lock_authority(db, principal, request)
    db.flush()
    row = evidence.owned(db, principal, draft_id, True)
    if (not row.request_id.startswith('fl_') or row.status != 'queued' or row.data.get('phase') != 'map'
            or row.data['reduction'].get('preview') is not None):
        _deny()
    link = origin.verify_link(db, principal, row, require_current=True)
    preview = row.data['preview']
    if not isinstance(allowance_preview,dict):
        _deny()
    try:
        issued_at = datetime.fromisoformat(allowance_preview['not_before'])
        rebuilt = build_allowance_preview(principal, preview, link['payload']['basis'], draft_id, job_id, issued_at)
        clock = db.scalar(select(func.clock_timestamp())).astimezone(timezone.utc)
        if (rebuilt != allowance_preview or issued_at.astimezone(timezone.utc)>clock
                or clock >= datetime.fromisoformat(rebuilt['expires_at']).astimezone(timezone.utc)):
            _deny()
    except (KeyError,TypeError,ValueError,OverflowError):
        _deny()
    stages = rebuilt['stages']
    if (len(stages) != len(row.data['maps']) or any(
            stage != evidence._empty_stage(chunk_id=plan['stage_id'])
            for stage, plan in zip(row.data['maps'], stages))):
        _deny()
    job = db.scalar(select(Job).where(Job.workspace_id == principal.workspace_id,
        Job.id == job_id).with_for_update().execution_options(populate_existing=True))
    from .report_evidence_worker_isolation import isolated
    if (not isolated(job) or job.status != 'queued' or job.lease_token is not None
            or job.lease_until is not None or not isinstance(job.data,dict)
            or job.data.get('actor_id') != principal.user_id or job.data.get('report_draft_id') != row.id
            or row.data['job_id'] != job.id or link['payload']['new_job_id'] != job.id):
        _deny()
    cap = db.scalar(select(Capability).where(Capability.workspace_id == principal.workspace_id,
        Capability.id == preview['body']['model_id']).with_for_update().execution_options(populate_existing=True))
    if (not cap or cap.kind != 'foundation_model' or cap.status != 'published'
            or not isinstance(cap.data,dict) or not isinstance(cap.data.get('execution'),dict)
            or preview.get('provider') != 'deepseek' or cap.data['execution'].get('provider') != 'deepseek'
            or cap.content_hash != preview['published_model_hash'] or cap.validated_hash != cap.content_hash
            or digest(cap.data) != cap.content_hash or cap.data.get('data_policy',{}).get('cloud_allowed') is not True):
        _deny()
    if db.scalar(select(Job.id).where(Job.workspace_id == principal.workspace_id,
            Job.idempotency_key == plan_key(job.id))):
        _deny()
    manifest = {'schema':SCHEMA, 'workspace_id':principal.workspace_id, 'actor_id':principal.user_id,
        'issuer_id':principal.user_id, 'draft_id':row.id, 'job_id':job.id,
        'origin_hash':link['origin_hash'], 'source_hash':preview['source_hash'],
        'preview_hash':preview['preview_hash'], 'allowance_preview_hash':rebuilt['allowance_preview_hash'],
        'model_id':cap.id, 'model_hash':cap.content_hash, 'stage_plan':stages,
        'max_tokens':rebuilt['max_tokens'], 'max_calls':rebuilt['max_calls'],
        'not_before':rebuilt['not_before'], 'expires_at':rebuilt['expires_at']}
    grant = Job(workspace_id=principal.workspace_id, kind='cloud_report_plan_allowance_v1', status='internal',
        idempotency_key=plan_key(job.id), data={})
    db.add(grant); db.flush()
    audit = Audit(workspace_id=principal.workspace_id, actor_id=principal.user_id, object_id=grant.id,
        action='cloud.report_plan_allowance.issued_v1',
        data={'schema':'delegated-evidence-plan-issue-v1', 'grant_id':grant.id, 'manifest_hash':digest(manifest),
              'allowance_preview_hash':rebuilt['allowance_preview_hash']})
    db.add(audit); db.flush()
    grant.data = {'schema':RECORD, 'manifest':manifest, 'manifest_hash':digest(manifest),
                  'audit_id':audit.id, 'consumed':[]}
    issued_permits = []
    for entry in stages:
        permit = Job(workspace_id=principal.workspace_id, kind='cloud_extra_allowance_v1', status='internal',
            idempotency_key=scoped.permit_key(job.id,entry['stage_id']), data={})
        db.add(permit); db.flush()
        stage_scope = scope_hash(principal.workspace_id, principal.user_id, row.id, job.id,
            preview['source_hash'], preview['preview_hash'], link['origin_hash'], entry['stage_id'])
        item = {'schema':'cloud-extra-allowance-manifest-v1','workspace_id':principal.workspace_id,
            'actor_id':principal.user_id,'issuer_id':principal.user_id,'job_id':job.id,'job_kind':job.kind,
            'permit_id':permit.id,'capability_id':cap.id,'capability_hash':cap.content_hash,
            'scope_kind':SCOPE,'stage_id':entry['stage_id'],'scope_hash':stage_scope,
            'request_hash':entry['request_hash'],'options_hash':entry['options_hash'],
            'token_bound':entry['token_bound'],'max_tokens':entry['token_bound'],'max_calls':1,
            'not_before':rebuilt['not_before'],'expires_at':rebuilt['expires_at']}
        permit_audit = Audit(workspace_id=principal.workspace_id, actor_id=principal.user_id, object_id=permit.id,
            action='cloud.extra_allowance.issued_v1',data={'schema':'cloud-extra-allowance-issue-v1',
                'permit_id':permit.id,'manifest_hash':digest(item)})
        db.add(permit_audit); db.flush()
        permit.data={'schema':'cloud-extra-allowance-record-v1','manifest':item,'manifest_hash':digest(item),
                     'audit_id':permit_audit.id,'used_call_id':None}
        issued_permits.append((permit, permit_audit, deepcopy(permit.data), deepcopy(permit_audit.data),
                               permit.id, permit.idempotency_key, permit_audit.id))
    db.flush()
    # Keep every issuance component, active authorization and the source task
    # bound until the very transaction commit. Rollback clears this closure.
    actor = db.scalar(select(User).where(User.id == principal.user_id).with_for_update().execution_options(populate_existing=True))
    member = db.scalar(select(Membership).where(Membership.user_id == principal.user_id,
        Membership.workspace_id == principal.workspace_id).with_for_update().execution_options(populate_existing=True))
    session_row = db.scalar(select(AuthSession).where(AuthSession.id == principal.session_id)
        .with_for_update().execution_options(populate_existing=True))
    expected_row, expected_job, expected_grant, expected_audit = (deepcopy(row.data),deepcopy(job.data),
        deepcopy(grant.data),deepcopy(audit.data))
    expected_version = row.version
    grant_id, audit_id, actor_id, member_id, session_id = grant.id, audit.id, actor.id, member.id, session_row.id
    expected_request_id, job_key = row.request_id, job.idempotency_key
    transaction = db.get_transaction()
    checks = scoped._session_checks(db)
    if transaction is None or transaction in checks or not actor or not member or not session_row:
        _deny()
    def check_at_commit(session):
        current = session.scalar(select(func.clock_timestamp())).astimezone(timezone.utc)
        objects = [row,job,grant,audit,cap,actor,member,session_row]
        objects.extend(x for pair in issued_permits for x in pair[:2])
        if (session.get_transaction() is not transaction or any(x in session.deleted or inspect(x).deleted or inspect(x).detached for x in objects)
                or current >= datetime.fromisoformat(rebuilt['expires_at']).astimezone(timezone.utc)
                or actor.id != actor_id or actor.id != principal.user_id or not actor.active
                or member.id != member_id or member.role != 'admin' or member.user_id != principal.user_id
                or member.workspace_id != principal.workspace_id or session_row.id != session_id
                or session_row.id != principal.session_id or session_row.user_id != principal.user_id
                or session_row.revoked or session_row.expires_at <= current
                or row.id != draft_id or row.workspace_id != principal.workspace_id or row.actor_id != principal.user_id
                or row.status != 'queued' or row.request_id != expected_request_id
                or row.version != expected_version or row.data != expected_row
                or job.id != job_id or job.workspace_id != principal.workspace_id or job.status != 'queued'
                or not isolated(job) or job.idempotency_key != job_key
                or job.lease_until is not None or job.lease_token is not None or job.data != expected_job
                or grant.id != grant_id or audit.id != audit_id or grant.data != expected_grant or grant.kind != 'cloud_report_plan_allowance_v1'
                or grant.status != 'internal' or grant.workspace_id != principal.workspace_id
                or grant.idempotency_key != plan_key(job_id) or audit.data != expected_audit
                or audit.actor_id != principal.user_id or audit.workspace_id != principal.workspace_id
                or audit.object_id != grant.id or audit.action != 'cloud.report_plan_allowance.issued_v1'
                or cap.id != manifest['model_id'] or cap.workspace_id != principal.workspace_id
                or cap.kind != 'foundation_model' or cap.status != 'published' or cap.content_hash != manifest['model_hash']
                or cap.validated_hash != cap.content_hash or digest(cap.data) != cap.content_hash):
            _deny()
        for permit, permit_audit, expected_permit, expected_permit_audit, permit_id, permit_key, permit_audit_id in issued_permits:
            if (permit.id != permit_id or permit.idempotency_key != permit_key or permit_audit.id != permit_audit_id
                    or permit.data != expected_permit or permit.kind != 'cloud_extra_allowance_v1'
                    or permit.status != 'internal' or permit.workspace_id != principal.workspace_id
                    or permit_audit.data != expected_permit_audit or permit_audit.actor_id != principal.user_id
                    or permit_audit.workspace_id != principal.workspace_id or permit_audit.object_id != permit.id
                    or permit_audit.action != 'cloud.extra_allowance.issued_v1'):
                _deny()
    checks[transaction] = check_at_commit
    db.info.pop(_ARM_KEY,None)
    return grant.id


class _MapBinding:
    """Locked server-owned stage/grant, valid only within one transaction."""
    def __init__(self, db, row, grant, audit, previous, entry, principal, epoch_records=()):
        self.tx=db.get_transaction();self.row=row;self.grant=grant;self.entry=deepcopy(entry);self.principal=principal
        self.row_before=deepcopy(row.data);self.grant_before=deepcopy(grant.data)
        self.row_id=row.id;self.grant_id=grant.id;self.grant_key=grant.idempotency_key
        self.audit=audit;self.audit_id=audit.id;self.audit_data=deepcopy(audit.data)
        # Snapshot every mapped column, including capability, workspace and
        # audit timestamps/version; selective checks miss ORM-side changes.
        self.previous=[(call,{column.key:deepcopy(getattr(call,column.key))
                            for column in inspect(call).mapper.column_attrs}) for call in previous]
        from .report_evidence_prebound_transport import _call_snapshot
        for item in epoch_records:
            if inspect(item).unloaded:db.refresh(item)
        self.epoch_snapshots=[_call_snapshot(db,item) for item in epoch_records]
        self.scope_expiries=[datetime.fromisoformat(item.data['manifest']['expires_at'])
            for item in epoch_records if isinstance(item,Job) and item.kind=='fl_scoped_activation_v1']
        self.after_row=None;self.after_grant=None;self.version=None

    def bind(self, db, call):
        from . import report_evidence_pipeline as evidence
        if db.get_transaction() is not self.tx or self.row.data != self.row_before or self.grant.data != self.grant_before:
            _deny()
        entry=self.entry;st=evidence._stage(self.row,entry['stage_id'])
        if st['status']!='running' or st['inflight'] is not True or st['reserved_call_id'] is not None or st['journal'] is not None:
            _deny()
        call.data={**call.data,'input_policy_hash':entry['input_policy_hash']}
        evidence._replace_stage(self.row,entry['stage_id'],{**st,'reserved_call_id':call.id})
        used=[*self.grant.data['consumed'],{'stage_id':entry['stage_id'],'call_id':call.id,'token_bound':entry['token_bound']}]
        self.grant.data={**self.grant.data,'consumed':used}
        self.after_row=deepcopy(self.row.data);self.after_grant=deepcopy(self.grant.data);self.version=self.row.version

    def check(self, db):
        from .report_evidence_prebound_transport import _check_call_snapshot
        for snapshot in self.epoch_snapshots:_check_call_snapshot(db,snapshot)
        if self.scope_expiries and db.scalar(select(func.clock_timestamp())) >= min(self.scope_expiries):
            _deny()
        if (db.get_transaction() is not self.tx or self.after_row is None or self.after_grant is None
                or self.row in db.deleted or self.grant in db.deleted or inspect(self.row).deleted
                or inspect(self.grant).deleted or inspect(self.row).detached or inspect(self.grant).detached
                or self.row.data != self.after_row or self.row.version != self.version
                or self.row.id != self.row_id or self.row.status != 'running'
                or self.grant.id != self.grant_id or self.grant.idempotency_key != self.grant_key
                or self.audit.id != self.audit_id or self.audit.data != self.audit_data
                or self.audit.actor_id != self.principal.user_id
                or self.audit.workspace_id != self.principal.workspace_id
                or self.audit.object_id != self.grant_id
                or self.audit.action != 'cloud.report_plan_allowance.issued_v1'
                or self.audit in db.deleted or inspect(self.audit).deleted or inspect(self.audit).detached
                or self.grant.data != self.after_grant or self.grant.kind != 'cloud_report_plan_allowance_v1'
                or self.grant.status != 'internal' or self.row.workspace_id != self.principal.workspace_id
                or self.grant.workspace_id != self.principal.workspace_id or self.row.actor_id != self.principal.user_id):
            _deny()
        for call,snapshot in self.previous:
            state=inspect(call)
            if (call in db.deleted or state.deleted or state.detached or not state.persistent
                    or state.session is not db or state.expired_attributes
                    or any(getattr(call,key)!=value for key,value in snapshot.items())):
                _deny()
        return True


def prepare_map_stage(db, principal, job, capability, permit_manifest):
    """Rebuild a delegated fl_ stage under the real worker lease; no dispatch.

    Called only by a server-owned adapter after the generic permit, issuer and
    worker checks. Prior calls are locked before any daily-budget row lock.
    A later gateway integration must commit before any HTTP and protect *all*
    applicable exits; merely calling this function does not authorize a POST.
    """
    from . import report_evidence_pipeline as evidence
    from . import report_evidence_delegated_origin as origin
    from .gateway import prepare_cloud_json
    from .report_evidence_worker_isolation import isolated
    if not isolated(job) or not fenced(db) or db.info.get('worker_actor') != principal or ownership(db).get('job_id') != job.id:
        _deny()
    rid=job.data.get('report_draft_id') if isinstance(job.data,dict) else None
    if type(rid) is not str or not rid:
        _deny()
    row=evidence.owned(db,principal,rid,True)
    if (not row.request_id.startswith('fl_') or row.data['job_id']!=job.id
            or row.status!='running' or row.data['phase']!='map'):
        _deny()
    link=origin.verify_link(db,principal,row,require_current=True)
    from .report_evidence_epoch_handoff import assert_worker_epoch
    epoch_records=assert_worker_epoch(db,principal,job,row)
    from .report_evidence_delegated_dispatch_gate import ready
    if not ready():
        from . import report_evidence_scoped_activation as activation
        activation_row,activation_audit,_=activation.for_job(db,principal,row,map_job_id=job.id,lock=True)
        epoch_records=[*epoch_records,activation_row,activation_audit]
    current_cap=evidence._fresh_cap(db,principal,row,map_source=True)
    if current_cap.id!=capability.id or current_cap.content_hash!=capability.content_hash:
        _deny()
    preview=row.data['preview'];stages=plan_from_preview(preview);key=permit_manifest.get('stage_id')
    grant=db.scalar(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.idempotency_key==plan_key(job.id)).with_for_update().execution_options(populate_existing=True))
    if (not grant or grant.kind!='cloud_report_plan_allowance_v1' or grant.status!='internal'
            or not isinstance(grant.data,dict) or set(grant.data)!={'schema','manifest','manifest_hash','audit_id','consumed'}
            or grant.data.get('schema')!=RECORD or grant.data.get('manifest_hash')!=digest(grant.data.get('manifest'))):
        _deny()
    gm=grant.data['manifest'];used=grant.data['consumed']
    manifest_keys={'schema','workspace_id','actor_id','issuer_id','draft_id','job_id','origin_hash',
        'source_hash','preview_hash','allowance_preview_hash','model_id','model_hash','stage_plan',
        'max_tokens','max_calls','not_before','expires_at'}
    if (not isinstance(gm,dict) or set(gm)!=manifest_keys or gm.get('schema')!=SCHEMA or gm.get('workspace_id')!=principal.workspace_id
            or gm.get('actor_id')!=principal.user_id or gm.get('issuer_id')!=principal.user_id
            or gm.get('draft_id')!=row.id or gm.get('job_id')!=job.id or gm.get('origin_hash')!=link['origin_hash']
            or gm.get('source_hash')!=preview['source_hash'] or gm.get('preview_hash')!=preview['preview_hash']
            or gm.get('model_id')!=capability.id or gm.get('model_hash')!=capability.content_hash
            or gm.get('stage_plan')!=stages or gm.get('max_tokens')!=sum(x['token_bound'] for x in stages)
            or gm.get('max_calls')!=len(stages) or type(used) is not list or len(used)>=len(stages)
            or key!=stages[len(used)]['stage_id'] or permit_manifest.get('scope_kind')!=SCOPE
            or permit_manifest.get('scope_hash')!=scope_hash(principal.workspace_id,principal.user_id,row.id,job.id,
                preview['source_hash'],preview['preview_hash'],link['origin_hash'],key)):
        _deny()
    clock=db.scalar(select(func.clock_timestamp())).astimezone(timezone.utc)
    try:
        start=datetime.fromisoformat(gm['not_before']);end=datetime.fromisoformat(gm['expires_at'])
        if start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None:
            _deny()
        start=start.astimezone(timezone.utc);end=end.astimezone(timezone.utc)
    except (ValueError,TypeError,KeyError):
        _deny()
    if not start<=clock<end or end-start>MAX_TTL:
        _deny()
    audits=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
        Audit.object_id==grant.id,Audit.action=='cloud.report_plan_allowance.issued_v1').limit(2)
        .with_for_update().execution_options(populate_existing=True)).all()
    if (len(audits)!=1 or audits[0].id!=grant.data['audit_id'] or audits[0].actor_id!=principal.user_id
            or audits[0].data!={'schema':'delegated-evidence-plan-issue-v1','grant_id':grant.id,
                'manifest_hash':grant.data['manifest_hash'],'allowance_preview_hash':gm['allowance_preview_hash']}):
        _deny()
    if [x['chunk_id'] for x in row.data['maps']]!=[x['stage_id'] for x in stages]:
        _deny()
    idx=len(used)
    if (any(x['status']!='succeeded' for x in row.data['maps'][:idx])
            or any(x['status']!='pending' or x['inflight'] or x['reserved_call_id'] is not None
                   for x in row.data['maps'][idx+1:])):
        _deny()
    if any(not isinstance(item,dict) or row.data['maps'][i].get('reserved_call_id')!=item.get('call_id')
            for i,item in enumerate(used)):
        _deny()
    prior_ids=[x.get('call_id') for x in used if isinstance(x,dict)]
    previous=db.scalars(select(CloudCall).where(CloudCall.id.in_(prior_ids),
        CloudCall.workspace_id==principal.workspace_id).order_by(CloudCall.id).with_for_update(nowait=True)
        .execution_options(populate_existing=True)).all() if prior_ids else []
    total=validate_progress(stages,used,{c.id:c for c in previous},workspace_id=principal.workspace_id,
        actor_id=principal.user_id,capability_id=capability.id,job_id=job.id)
    entry=stages[idx]
    if total+entry['token_bound']>gm['max_tokens'] or idx+1>gm['max_calls']:
        _deny()
    actual=evidence._request(row,key);schema=evidence.legacy.canonical_schema(evidence._spec(row,key)['output_schema'])
    messages,_,images=prepare_cloud_json(capability,actual['messages'][1:],schema)
    if images or messages!=actual['messages'] or digest(messages)!=entry['request_hash']:
        _deny()
    if (permit_manifest.get('request_hash')!=entry['request_hash']
            or permit_manifest.get('options_hash')!=entry['options_hash']
            or permit_manifest.get('token_bound')!=entry['token_bound']):
        _deny()
    return _MapBinding(db,row,grant,audits[0],previous,entry,principal,epoch_records)
