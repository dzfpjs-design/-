"""One persisted, revocable activation for one frozen report plan.

No global switch, model call, or budget is created by activation.  A transport
intent must hold the activation row until commit; revocation takes the same
row lock.  Intents committed first are already in flight and cannot be recalled.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .contracts import digest
from .models import Audit, AuthSession, Job, Membership, SetupDraft, User
from .security import Principal
from .services import require
from . import report_reading_drafts as legacy

router = APIRouter()
KIND = 'fl_scoped_activation_v1'
SCHEMA = 'report-scoped-activation-v1'
ISSUE_ACTION = 'cloud.report_scope.activated_v1'
REVOKE_ACTION = 'cloud.report_scope.revoked_v1'


class ActivationCommand(legacy.SafeContract):
    preview_id: UUID
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def exact_consent(cls, value):
        if value is not True:
            raise ValueError('必须明确授权这次报告计划')
        return value


def _deny():
    from .report_evidence_delegated_origin import _fail
    _fail('report_scope_denied', '本次报告的定向启用不存在、已撤销、已过期或无法核对；没有授权发送')


def _instant(value):
    if type(value) is not str:
        _deny()
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            _deny()
        return result
    except (ValueError, TypeError):
        _deny()


def _key(draft_id):
    return 'fl-activation-v1:' + digest({'draft_id': draft_id})


def _fetch(db, query, *, lock):
    try:
        if lock:
            query = query.with_for_update(nowait=True).execution_options(populate_existing=True)
        return db.scalar(query)
    except OperationalError as error:
        if getattr(error.orig, 'sqlstate', None) == '55P03':
            from .report_evidence_delegated_origin import _fail
            _fail('report_scope_busy', '该报告正在完成发送或撤销，请稍后重新查看状态', 409)
        raise


def _checked(db, principal, record, *, preview_id=None, draft_id=None, map_job_id=None, require_live=True):
    if (not isinstance(db, Session) or type(principal) is not Principal or principal.role != 'admin'
            or not record or record.workspace_id != principal.workspace_id or record.kind != KIND
            or record.status not in {'internal', 'revoked'} or type(record.data) is not dict
            or set(record.data) != {'schema', 'manifest', 'manifest_hash', 'issue_audit_id'}):
        _deny()
    data = record.data
    manifest = data.get('manifest')
    if (data.get('schema') != SCHEMA or type(manifest) is not dict
            or set(manifest) != {'schema', 'workspace_id', 'actor_id', 'preview_id', 'draft_id',
                                  'map_job_id', 'allowance_preview_hash', 'issued_at', 'expires_at'}
            or manifest.get('schema') != SCHEMA or data.get('manifest_hash') != digest(manifest)
            or manifest.get('workspace_id') != principal.workspace_id or manifest.get('actor_id') != principal.user_id
            or any(type(manifest.get(k)) is not str or not manifest[k] for k in
                   ('preview_id', 'draft_id', 'map_job_id', 'allowance_preview_hash'))
            or record.idempotency_key != _key(manifest['draft_id'])
            or preview_id is not None and manifest['preview_id'] != preview_id
            or draft_id is not None and manifest['draft_id'] != draft_id
            or map_job_id is not None and manifest['map_job_id'] != map_job_id):
        _deny()
    audits = db.scalars(select(Audit).where(Audit.workspace_id == principal.workspace_id,
        Audit.object_id == record.id, Audit.action == ISSUE_ACTION).limit(2)).all()
    if (len(audits) != 1 or audits[0].id != data.get('issue_audit_id')
            or audits[0].actor_id != principal.user_id
            or audits[0].data != {'schema': SCHEMA, 'activation_id': record.id,
                                  'manifest_hash': data['manifest_hash']}):
        _deny()
    revocations=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
        Audit.object_id==record.id,Audit.action==REVOKE_ACTION).limit(2)).all()
    if record.status=='internal' and revocations:
        _deny()
    if record.status=='revoked' and (len(revocations)!=1 or revocations[0].actor_id!=principal.user_id
            or revocations[0].data!={'schema':SCHEMA,'activation_id':record.id,
                                     'manifest_hash':data['manifest_hash']}):
        _deny()
    issued, expires = _instant(manifest['issued_at']), _instant(manifest['expires_at'])
    now = db.scalar(select(func.clock_timestamp()))
    if expires <= issued or (require_live and (record.status != 'internal' or not issued <= now < expires)):
        _deny()
    return record, audits[0], expires


def for_preview(db, principal, preview, *, lock=True):
    allowance = preview.data.get('allowance') if isinstance(preview.data, dict) else None
    if (not isinstance(allowance, dict) or any(type(allowance.get(k)) is not str
            for k in ('draft_id', 'job_id', 'allowance_preview_hash'))):
        _deny()
    q = select(Job).where(Job.workspace_id == principal.workspace_id,
                          Job.idempotency_key == _key(allowance['draft_id']))
    record = _fetch(db,q,lock=lock)
    checked = _checked(db, principal, record, preview_id=preview.id, draft_id=allowance['draft_id'],
                       map_job_id=allowance['job_id'])
    if checked[0].data['manifest']['allowance_preview_hash'] != allowance['allowance_preview_hash']:
        _deny()
    return checked


def for_job(db, principal, row, *, map_job_id, lock=True):
    if not row or row.workspace_id != principal.workspace_id or row.actor_id != principal.user_id:
        _deny()
    q = select(Job).where(Job.workspace_id == principal.workspace_id,
                          Job.idempotency_key == _key(row.id))
    checked = _checked(db, principal, _fetch(db,q,lock=lock), draft_id=row.id, map_job_id=map_job_id)
    # The allowance hash is checked again against the actual map grant, so a
    # record for another frozen plan cannot authorize this job.
    from . import cloud_report_plan_allowance as plans
    grant = db.scalar(select(Job).where(Job.workspace_id == principal.workspace_id,
                                        Job.idempotency_key == plans.plan_key(map_job_id)))
    if (not grant or not isinstance(grant.data, dict) or not isinstance(grant.data.get('manifest'), dict)
            or grant.data['manifest'].get('allowance_preview_hash') != checked[0].data['manifest']['allowance_preview_hash']):
        _deny()
    return checked


def for_worker(db, principal, job, *, lock=False):
    """Resolve a map or reduce worker through its original linked report."""
    from . import report_evidence_delegated_origin as origin
    from . import report_evidence_pipeline as evidence
    if (not job or job.workspace_id != principal.workspace_id or not isinstance(job.data, dict)
            or job.data.get('actor_id') != principal.user_id
            or not isinstance(job.data.get('report_draft_id'), str)):
        _deny()
    row = evidence.owned(db, principal, job.data['report_draft_id'])
    if not row.request_id.startswith('fl_') or row.data.get('job_id') != job.id:
        _deny()
    link = origin.verify_link(db, principal, row, lock=False, require_current=True)
    map_job_id = link['payload']['new_job_id']
    if job.id != map_job_id and row.data.get('phase') != 'reduce':
        _deny()
    return for_job(db, principal, row, map_job_id=map_job_id, lock=lock)


def _guard_write(db, principal, objects, *, expires=None):
    """Keep authenticated authority and scope immutable through commit."""
    from copy import deepcopy
    from sqlalchemy import inspect
    from . import cloud_scoped_extra_reservation as scoped
    auth = db.get(AuthSession, principal.session_id)
    user = db.get(User, principal.user_id)
    member = db.scalar(select(Membership).where(Membership.workspace_id == principal.workspace_id,
                                               Membership.user_id == principal.user_id))
    if not auth or not user or not member:
        _deny()
    objects = [*objects, auth, user, member]
    snapshots = [(obj, {c.key: deepcopy(getattr(obj, c.key)) for c in inspect(obj).mapper.column_attrs})
                 for obj in objects]
    tx = db.get_transaction(); checks = scoped._session_checks(db)
    previous = checks.get(tx)
    def check(session):
        if previous is not None:
            previous(session)
        now = session.scalar(select(func.clock_timestamp()))
        if (session.get_transaction() is not tx or expires is not None and now >= expires
                or auth.revoked or auth.expires_at <= now or not user.active or member.role != 'admin'):
            _deny()
        for obj, columns in snapshots:
            state = inspect(obj)
            if (obj in session.deleted or state.deleted or state.detached or not state.persistent
                    or state.session is not session or state.expired_attributes
                    or any(getattr(obj, k) != v for k, v in columns.items())):
                _deny()
    checks[tx] = check


def issue_tx(db, principal, request, preview_id):
    from . import report_evidence_delegated_budget_start as budget
    from .report_full_provisional import _lock_authority
    require(principal, 'admin')
    legacy.workspace_draft(db, principal)
    _lock_authority(db, principal, request)
    preview, command = budget._saved_preview(db, principal, preview_id, lock=True)
    if preview.data.get('used_grant_id') is not None:
        _deny()
    allowance = preview.data['allowance']
    summary = budget._summary(db, principal, preview, command)
    if summary['status'] != 'saved':
        _deny()
    now = db.scalar(select(func.clock_timestamp()))
    expiry = _instant(allowance['expires_at'])
    if now >= expiry:
        _deny()
    key = _key(allowance['draft_id'])
    existing = _fetch(db,select(Job).where(Job.workspace_id == principal.workspace_id,
                                           Job.idempotency_key == key),lock=True)
    if existing is not None:
        record, _, _ = _checked(db, principal, existing, preview_id=preview.id,
                                draft_id=allowance['draft_id'], map_job_id=allowance['job_id'])
        if record.data['manifest']['allowance_preview_hash'] != allowance['allowance_preview_hash']:
            _deny()
        return record, True
    identity = str(uuid4())
    manifest = {'schema': SCHEMA, 'workspace_id': principal.workspace_id, 'actor_id': principal.user_id,
                'preview_id': preview.id, 'draft_id': allowance['draft_id'], 'map_job_id': allowance['job_id'],
                'allowance_preview_hash': allowance['allowance_preview_hash'], 'issued_at': now.isoformat(),
                'expires_at': expiry.isoformat()}
    record = Job(id=identity, workspace_id=principal.workspace_id, kind=KIND, status='internal',
                 idempotency_key=key, data={})
    audit = Audit(workspace_id=principal.workspace_id, actor_id=principal.user_id, object_id=identity,
                  action=ISSUE_ACTION, data={'schema': SCHEMA, 'activation_id': identity,
                                            'manifest_hash': digest(manifest)})
    db.add_all([record, audit]); db.flush()
    record.data = {'schema': SCHEMA, 'manifest': manifest, 'manifest_hash': digest(manifest),
                   'issue_audit_id': audit.id}
    db.flush()
    _checked(db, principal, record, preview_id=preview.id, draft_id=allowance['draft_id'],
             map_job_id=allowance['job_id'])
    _guard_write(db,principal,[record,audit,preview],expires=expiry)
    return record, False


def revoke_tx(db, principal, request, activation_id):
    from .report_full_provisional import _lock_authority
    require(principal, 'admin')
    legacy.workspace_draft(db, principal)
    _lock_authority(db, principal, request)
    record = _fetch(db,select(Job).where(Job.workspace_id == principal.workspace_id,
                                         Job.id == activation_id),lock=True)
    record, _, _ = _checked(db, principal, record, require_live=False)
    if record.status == 'revoked':
        return record, True
    record.status = 'revoked'
    audit=Audit(workspace_id=principal.workspace_id, actor_id=principal.user_id, object_id=record.id,
                action=REVOKE_ACTION, data={'schema': SCHEMA, 'activation_id': record.id,
                                            'manifest_hash': record.data['manifest_hash']})
    db.add(audit); db.flush()
    _guard_write(db,principal,[record,audit])
    return record, False


def _response(record, replayed):
    return {'schema': SCHEMA, 'activation_id': record.id, 'preview_id': record.data['manifest']['preview_id'],
            'draft_id': record.data['manifest']['draft_id'], 'status': record.status,
            'expires_at': record.data['manifest']['expires_at'], 'replayed': replayed,
            'this_action_calls_model': False, 'user_confirmed_business': False,
            'in_flight_may_continue': True}


class ActivationStatusRequest(legacy.SafeContract):
    preview_id: UUID


def status_tx(db, principal, request, preview_id):
    """Read a saved plan and its activation under the same locks as mutations.

    A missing record means only that no matching record was found.  It says
    nothing about provider-side requests, charges, or deleted historical data.
    Invalid bindings and audits fail closed rather than becoming ``not_found``.
    """
    from . import report_evidence_delegated_budget_start as budget
    from .report_full_provisional import _lock_authority
    require(principal, 'admin')
    workspace = _fetch(db, select(SetupDraft).where(SetupDraft.workspace_id == principal.workspace_id), lock=True)
    if workspace is None:
        legacy.fail('configuration_required', '请先保存工作区业务说明，再核对草案发送范围')
    _lock_authority(db, principal, request)
    preview, command = budget._saved_preview(db, principal, preview_id, lock=True)
    summary = budget._summary(db, principal, preview, command)
    allowance = preview.data['allowance']
    # Check both canonical key and embedded references.  A malformed duplicate
    # or a record whose key was altered must not be presented as absent.
    issue_exists = select(Audit.id).where(Audit.workspace_id == principal.workspace_id,
        Audit.object_id == Job.id, Audit.action == ISSUE_ACTION).exists()
    query = select(Job).where(Job.workspace_id == principal.workspace_id, or_(
        Job.idempotency_key == _key(allowance['draft_id']),
        # Match a surviving preview reference even if kind/key were damaged.
        Job.data['manifest']['preview_id'].as_string() == preview.id,
        and_(Job.data['manifest']['draft_id'].as_string() == allowance['draft_id'],
             or_(Job.kind == KIND, issue_exists)),
    )).order_by(Job.id).limit(2).with_for_update(nowait=True).execution_options(populate_existing=True)
    try:
        rows = db.scalars(query).all()
    except OperationalError as error:
        if getattr(error.orig, 'sqlstate', None) == '55P03':
            from .report_evidence_delegated_origin import _fail
            _fail('report_scope_busy', '该报告正在完成发送或撤销，请稍后重新查看状态', 409)
        raise
    if len(rows) > 1:
        _deny()
    matches = []
    for row in rows:
        manifest = row.data.get('manifest') if isinstance(row.data, dict) else None
        if (row.idempotency_key == _key(allowance['draft_id'])
                or isinstance(manifest, dict) and
                (manifest.get('preview_id') == preview.id or manifest.get('draft_id') == allowance['draft_id'])):
            matches.append(row)
    if len(matches) > 1:
        _deny()
    record = matches[0] if matches else None
    if record is None:
        status, identity, expires_at = 'not_found', None, None
    else:
        record, _, expires = _checked(db, principal, record, preview_id=preview.id,
            draft_id=allowance['draft_id'], map_job_id=allowance['job_id'], require_live=False)
        manifest = record.data['manifest']
        if manifest['allowance_preview_hash'] != allowance['allowance_preview_hash']:
            _deny()
        now = db.scalar(select(func.clock_timestamp()))
        issued = _instant(manifest['issued_at'])
        if issued > now or expires > _instant(allowance['expires_at']):
            _deny()
        identity, expires_at = record.id, manifest['expires_at']
        status = 'revoked' if record.status == 'revoked' else ('expired' if now >= expires else 'active')
    value = {'schema': 'report-scoped-activation-status-v1', 'workspace_id': principal.workspace_id,
             'actor_id': principal.user_id, 'preview_id': preview.id, 'activation_id': identity,
             'status': status, 'plan_status': summary['status'], 'expires_at': expires_at,
             'execution_history': 'unknown', 'this_action_calls_model': False,
             'user_confirmed_business': False, 'in_flight_may_continue': True}
    return {**value, 'status_hash': digest(value)}


@router.post('/report-evidence-delegated/activation/status')
def activation_status(body: ActivationStatusRequest, request: Request, response: Response,
                      p: legacy.P, db: legacy.DB):
    result = status_tx(db, p, request, str(body.preview_id))
    db.rollback()
    response.headers['Cache-Control'] = 'private, no-store'
    return result


@router.post('/report-evidence-delegated/activation', status_code=201)
def activate(body: ActivationCommand, request: Request, response: Response, p: legacy.P, db: legacy.DB):
    record, replayed = issue_tx(db, p, request, str(body.preview_id))
    result = _response(record, replayed); db.commit()
    response.headers['Cache-Control'] = 'private, no-store'
    return result


@router.post('/report-evidence-delegated/activation/{identity}/revoke')
def revoke(identity: UUID, request: Request, response: Response, p: legacy.P, db: legacy.DB):
    record, replayed = revoke_tx(db, p, request, str(identity))
    result = _response(record, replayed); db.commit()
    response.headers['Cache-Control'] = 'private, no-store'
    return result
