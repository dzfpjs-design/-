"""Read-only facts for a delegated report whose execution may be interrupted.

No fact returned by this module authorizes a retry, a call, or a state change.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select

from .contracts import digest
from .models import Audit, Job
from . import cloud_report_plan_allowance as plans
from . import report_evidence_pipeline as pipeline
from .report_evidence_worker_isolation import isolated

router = APIRouter()


def _allowance(db, principal, row, job, now):
    """Report time bounds only when the bound and its issue audit agree."""
    is_reduce = row.data['reduction'].get('start_receipt') is not None
    key = ('evidence-reduce-allowance-v1:' + digest({'job_id': job.id})
           if is_reduce else plans.plan_key(job.id))
    kind = 'cloud_report_reduce_allowance_v1' if is_reduce else 'cloud_report_plan_allowance_v1'
    action = 'cloud.report_reduce_allowance.issued_v1' if is_reduce else 'cloud.report_plan_allowance.issued_v1'
    grant = db.scalar(select(Job).where(Job.workspace_id == principal.workspace_id, Job.idempotency_key == key))
    if not grant or grant.kind != kind or grant.status != 'internal' or type(grant.data) is not dict:
        return 'unverified'
    manifest = grant.data.get('manifest')
    if (type(manifest) is not dict or grant.data.get('manifest_hash') != digest(manifest)
            or manifest.get('workspace_id') != principal.workspace_id
            or manifest.get('actor_id') != principal.user_id or manifest.get('draft_id') != row.id
            or manifest.get('job_id') != job.id or manifest.get('source_hash') != row.data['preview']['source_hash']):
        return 'unverified'
    issues = db.scalars(select(Audit).where(Audit.workspace_id == principal.workspace_id,
        Audit.object_id == grant.id, Audit.action == action).limit(2)).all()
    if (len(issues) != 1 or issues[0].id != grant.data.get('audit_id')
            or issues[0].actor_id != principal.user_id or type(issues[0].data) is not dict
            or issues[0].data.get('grant_id') != grant.id
            or issues[0].data.get('manifest_hash') != grant.data['manifest_hash']):
        return 'unverified'
    try:
        start = datetime.fromisoformat(manifest['not_before'])
        end = datetime.fromisoformat(manifest['expires_at'])
        if start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None or end <= start:
            return 'unverified'
        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'unverified'
    return 'not_yet_valid' if now < start else 'expired' if now >= end else 'within_time_window'


def diagnostic(db, principal, identity, revision):
    row = pipeline.owned(db, principal, identity)
    if row.version != revision:
        pipeline.fail('delegated_diagnostic_revision_changed', '读取记录已变化，请刷新后重试')
    if not row.request_id.startswith('fl_'):
        pipeline.fail('delegated_diagnostic_not_found', '未找到本人的受控报告记录', 404)
    job = db.scalar(select(Job).where(Job.id == row.data['job_id'], Job.workspace_id == principal.workspace_id))
    if (not job or (job.kind != 'read_report_draft' and not isolated(job)) or type(job.data) is not dict
            or job.data.get('actor_id') != principal.user_id or job.data.get('report_draft_id') != row.id
            or job.status not in {'queued', 'running', 'failed', 'succeeded', 'cancelled'}):
        pipeline.fail('delegated_diagnostic_job_unverified', '任务记录暂时无法核对，原记录保留')
    now = db.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime) or now.tzinfo is None:
        pipeline.fail('delegated_diagnostic_clock_unverified', '服务器时间暂时无法核对')
    now = now.astimezone(timezone.utc)
    lease = 'not_running'
    if job.status == 'running':
        if not isinstance(job.lease_until, datetime) or job.lease_until.tzinfo is None or job.lease_token is None:
            lease = 'unverified'
        elif job.lease_until.astimezone(timezone.utc) <= now:
            lease = 'expired'
        else:
            lease = 'within_time_window'
    from .report_evidence_delegated_capture_view import capture_view
    if row.data['reduction'].get('start_receipt') is not None:
        unfinished = [('reduce', row.data['reduction'])]
    else:
        unfinished = [(s['chunk_id'], s) for s in row.data['maps'] if s['status'] != 'succeeded']
    recorded = [(name, s) for name, s in unfinished if s.get('reserved_call_id') is not None]
    response = 'all_completed' if not unfinished else 'not_determined'
    recovery = 'not_evaluated'
    if len(recorded) == 1:
        view = capture_view(db, principal, identity, recorded[0][0], revision)
        response = ('complete_unverified' if view['outcome'] == 'normal_end_unverified'
                    else 'not_recorded' if view['outcome'] == 'not_recorded'
                    else 'incomplete_or_unusable')
        recovery = view['recovery']['status']
    value = {'schema':'report-delegated-recovery-diagnostic-v1', 'actor_id':principal.user_id,
             'workspace_id':principal.workspace_id, 'draft_id':row.id, 'revision':row.version,
             'draft_status':row.status, 'job_status':job.status, 'lease':lease,
             'allowance_time':_allowance(db, principal, row, job, now),
             'response':response, 'recovery':recovery,
             'can_resend':False, 'can_resume':False, 'can_apply':False}
    return {**value, 'diagnostic_hash':digest(value)}


@router.get('/report-evidence-readings/{identity}/delegated-recovery-diagnostic')
def diagnostic_route(identity: UUID, response: Response, p: pipeline.ReadP, db: pipeline.DB,
                     revision: int = Query(..., ge=1)):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return diagnostic(db, p, str(identity), revision)
