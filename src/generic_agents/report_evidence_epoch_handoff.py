"""Disabled recovery epoch primitives for one original delegated map job.

The original grant, permits, calls, and audit are never migrated to another
job. A handoff requeues the same job after an expired lease and appends an
audited epoch. The new worker must acquire a different fenced lease.
No production entrypoint calls handoff_tx; this module never sends HTTP.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from .contracts import digest
from .models import Audit, AuthSession, CloudCall, Job, Membership, User
from . import report_evidence_pipeline as evidence
from . import report_evidence_delegated_origin as origin
from . import report_evidence_delegated_recovery_v1 as recovery
from . import report_reading_drafts as legacy
from . import cloud_report_plan_allowance as plans
from . import cloud_scoped_extra_reservation as scoped
from .report_full_provisional import _lock_authority
from .services import require


def _enabled():
    return False


def _deny():
    plans._deny()


def _locked(db, query):
    return db.scalar(query.with_for_update(nowait=True).execution_options(populate_existing=True))


def _records(db, principal, job_id):
    rows=db.scalars(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.kind=='report_evidence_epoch_v1',Job.data['payload']['job_id'].as_string()==job_id)
        .order_by(Job.created_at,Job.id).with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    rows.sort(key=lambda x:x.data.get('payload',{}).get('epoch',-1))
    previous=None;last_count=0
    for index,row in enumerate(rows,1):
        data=row.data;payload=data.get('payload') if isinstance(data,dict) else None
        if (row.status!='internal' or not isinstance(payload,dict) or set(data)!={'payload','hash','audit_id'}
                or set(payload)!={'schema','epoch','previous_hash','workspace_id','actor_id','draft_id','job_id',
                    'grant_id','plan_hash','origin_hash','consumed_count','old_lease_hash','resume_hash','previous_attempts'}
                or data['hash']!=digest(payload) or payload.get('schema')!='report-evidence-epoch-v1'
                or payload.get('epoch')!=index or payload.get('previous_hash')!=previous
                or payload.get('actor_id')!=principal.user_id or payload.get('workspace_id')!=principal.workspace_id
                or payload.get('job_id')!=job_id
                or type(payload.get('consumed_count')) is not int or payload['consumed_count']<last_count
                or row.idempotency_key!='report-evidence-epoch:'+job_id+':'+str(index)):
            _deny()
        audits=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
            Audit.object_id==row.id,Audit.action=='cloud.report_epoch.handoff_v1').limit(2)
            .with_for_update(nowait=True).execution_options(populate_existing=True)).all()
        if (len(audits)!=1 or audits[0].id!=data['audit_id'] or audits[0].actor_id!=principal.user_id
                or audits[0].data!={'epoch_hash':data['hash']}):_deny()
        previous=data['hash'];last_count=payload['consumed_count']
    return rows


def assert_worker_epoch(db,principal,job,row):
    """Lock and validate the epoch chain before reserve or transport.

    Caller already holds the fenced Job lease. Return locked records + audit
    for commit-time snapshots. A prior epoch's lease must never be reused.
    """
    rows=_records(db,principal,job.id)
    expected=rows[-1].data['hash'] if rows else None
    if (job.data.get('delegated_epoch_hash')!=expected
            or job.data.get('delegated_epoch_no',0)!=len(rows)):_deny()
    audits=[]
    for record in rows:
        payload=record.data['payload']
        if (payload.get('draft_id')!=row.id or payload.get('old_lease_hash')==digest(job.lease_token)
                or payload.get('plan_hash')!=digest(plans.plan_from_preview(row.data['preview']))
                or payload['consumed_count']>len(row.data['maps'])):_deny()
        audit=_locked(db,select(Audit).where(Audit.id==record.data['audit_id'],Audit.workspace_id==principal.workspace_id))
        if not audit:_deny()
        audits.append(audit)
    if rows:
        grant=_locked(db,select(Job).where(Job.workspace_id==principal.workspace_id,
                                           Job.idempotency_key==plans.plan_key(job.id)))
        if (not grant or grant.id!=rows[-1].data['payload']['grant_id']
                or not isinstance(grant.data,dict) or not isinstance(grant.data.get('consumed'),list)
                or rows[-1].data['payload']['consumed_count']>len(grant.data['consumed'])):_deny()
    return rows+audits


def handoff_tx(db,principal,request,draft_id,command):
    """Append one handoff and requeue original Job in this transaction only."""
    if not _enabled():_deny()
    require(principal,'admin');legacy.workspace_draft(db,principal);_lock_authority(db,principal,request)
    actor=db.get(User,principal.user_id);session_row=db.get(AuthSession,principal.session_id)
    member=db.scalar(select(Membership).where(Membership.workspace_id==principal.workspace_id,
                                               Membership.user_id==principal.user_id))
    row=evidence.owned(db,principal,draft_id,True)
    if not row.request_id.startswith('fl_') or type(command) is not evidence.ResumeCommand:_deny()
    prior=[x for x in row.data.get('resume_history',[]) if x['receipt']['request_id']==command.request_id]
    if prior:
        old_job=_locked(db,select(Job).where(Job.workspace_id==principal.workspace_id,Job.id==row.data['job_id']))
        records=_records(db,principal,old_job.id) if old_job else []
        matches=[r for r in records if r.data['payload']['resume_hash']==command.preview_hash]
        expected=evidence.command_hash(principal,command,kind='report_evidence_resume',draft_id=row.id)
        if (len(prior)!=1 or prior[0]['receipt']['command_hash']!=expected or len(matches)!=1
                or old_job.data.get('delegated_epoch_hash')!=(records[-1].data['hash'] if records else None)
                or old_job.data.get('delegated_epoch_no')!=len(records)):_deny()
        return {'epoch_id':matches[0].id,'epoch':matches[0].data['payload']['epoch'],
                'job_id':old_job.id,'new_cloud_calls':0,'replayed':True}
    if (row.status!='failed' or row.data.get('error_code')!='evidence_remaining_unstarted'
            or not row.data.get('recovery_history')):_deny()
    old_job=_locked(db,select(Job).where(Job.workspace_id==principal.workspace_id,Job.id==row.data['job_id']))
    clock=db.scalar(select(func.clock_timestamp()))
    if (not old_job or old_job.kind!=legacy.KIND or old_job.status!='failed'
            or (old_job.lease_until is not None and old_job.lease_until>clock)
            or old_job.data.get('actor_id')!=principal.user_id or old_job.data.get('report_draft_id')!=row.id):_deny()
    link=origin.verify_link(db,principal,row,require_current=True)
    if link['payload']['new_job_id']!=old_job.id:_deny()
    cap=evidence._fresh_cap(db,principal,row,map_source=True)
    grant=_locked(db,select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.idempotency_key==plans.plan_key(old_job.id)))
    if (not grant or grant.kind!='cloud_report_plan_allowance_v1' or grant.status!='internal'
            or grant.data.get('manifest_hash')!=digest(grant.data.get('manifest'))):_deny()
    gm=grant.data['manifest'];stages=plans.plan_from_preview(row.data['preview']);used=grant.data['consumed']
    if (gm.get('stage_plan')!=stages or gm.get('job_id')!=old_job.id or gm.get('draft_id')!=row.id
            or gm.get('actor_id')!=principal.user_id or gm.get('workspace_id')!=principal.workspace_id
            or gm.get('issuer_id')!=principal.user_id or gm.get('model_id')!=cap.id
            or gm.get('model_hash')!=cap.content_hash or gm.get('origin_hash')!=link['origin_hash']
            or gm.get('source_hash')!=row.data['preview']['source_hash']
            or gm.get('max_calls')!=len(stages) or gm.get('max_tokens')!=sum(s['token_bound'] for s in stages)
            or type(used) is not list or not 0<len(used)<len(stages)):_deny()
    try:
        start=datetime.fromisoformat(gm['not_before']);end=datetime.fromisoformat(gm['expires_at'])
        if start.tzinfo is None or end.tzinfo is None or not start<=clock<end:_deny()
    except (KeyError,ValueError,TypeError):_deny()
    grant_audit=_locked(db,select(Audit).where(Audit.id==grant.data.get('audit_id'),Audit.workspace_id==principal.workspace_id))
    if (not grant_audit or grant_audit.actor_id!=principal.user_id or grant_audit.object_id!=grant.id
            or grant_audit.action!='cloud.report_plan_allowance.issued_v1'
            or grant_audit.data!={'schema':'delegated-evidence-plan-issue-v1','grant_id':grant.id,
              'manifest_hash':grant.data['manifest_hash'],'allowance_preview_hash':gm['allowance_preview_hash']}):_deny()
    # A missing permit marker alone cannot establish non-send: reject orphan
    # ledger/dispatch rows for this job, including stages outside the plan.
    calls=db.scalars(select(CloudCall).where(CloudCall.workspace_id==principal.workspace_id,
        CloudCall.data['scoped_extra']['job_id'].as_string()==old_job.id).order_by(CloudCall.id)
        .with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    if {c.id for c in calls}!={x['call_id'] for x in used} or len(calls)!=len(used):_deny()
    plans.validate_progress(stages,used,{c.id:c for c in calls},workspace_id=principal.workspace_id,
        actor_id=principal.user_id,capability_id=cap.id,job_id=old_job.id)
    immutable=[grant,grant_audit,*calls];used_dispatches=[];receipt_fields=('call_id','provider','model','usage','request_id')
    for i,entry in enumerate(stages):
        permit=_locked(db,select(Job).where(Job.workspace_id==principal.workspace_id,
            Job.idempotency_key==scoped.permit_key(old_job.id,entry['stage_id'])))
        dispatch=_locked(db,select(Job).where(Job.workspace_id==principal.workspace_id,
            Job.idempotency_key==scoped.dispatch_key(old_job.id,entry['stage_id'])))
        if (not permit or permit.kind!='cloud_extra_allowance_v1' or permit.status!='internal'
                or permit.data.get('manifest_hash')!=digest(permit.data.get('manifest'))):_deny()
        pm=permit.data.get('manifest')
        if (not isinstance(pm,dict) or frozenset(pm)!=scoped._MANIFEST_KEYS
                or pm.get('schema')!='cloud-extra-allowance-manifest-v1'
                or pm.get('workspace_id')!=principal.workspace_id or pm.get('actor_id')!=principal.user_id
                or pm.get('issuer_id')!=principal.user_id or pm.get('job_id')!=old_job.id
                or pm.get('job_kind')!=old_job.kind or pm.get('permit_id')!=permit.id
                or pm.get('capability_id')!=cap.id or pm.get('capability_hash')!=cap.content_hash
                or pm.get('scope_kind')!=plans.SCOPE or pm.get('stage_id')!=entry['stage_id']
                or pm.get('scope_hash')!=plans.scope_hash(principal.workspace_id,principal.user_id,row.id,
                    old_job.id,gm['source_hash'],gm['preview_hash'],link['origin_hash'],entry['stage_id'])
                or pm.get('request_hash')!=entry['request_hash'] or pm.get('options_hash')!=entry['options_hash']
                or pm.get('token_bound')!=entry['token_bound'] or pm.get('max_tokens')!=entry['token_bound']
                or pm.get('max_calls')!=1 or pm.get('not_before')!=gm['not_before']
                or pm.get('expires_at')!=gm['expires_at']):_deny()
        pa=_locked(db,select(Audit).where(Audit.id==permit.data.get('audit_id'),Audit.workspace_id==principal.workspace_id))
        if (not pa or pa.actor_id!=principal.user_id or pa.object_id!=permit.id
                or pa.action!='cloud.extra_allowance.issued_v1' or pa.data!={'schema':'cloud-extra-allowance-issue-v1',
                'permit_id':permit.id,'manifest_hash':permit.data['manifest_hash']}):_deny()
        immutable.extend((permit,pa));stage=row.data['maps'][i]
        if i>=len(used):
            if (permit.data.get('used_call_id') is not None or dispatch is not None
                    or stage['reserved_call_id'] is not None or stage['inflight'] or stage['journal'] is not None):_deny()
            continue
        call=next(c for c in calls if c.id==used[i]['call_id'])
        if (stage['status']!='succeeded' or stage['reserved_call_id']!=call.id
                or permit.data.get('used_call_id')!=call.id or not dispatch
                or dispatch.kind!='cloud_extra_dispatch_v1' or dispatch.status!='internal'
                or dispatch.data.get('state')!='transport_intent' or dispatch.data.get('call_id')!=call.id
                or dispatch.data.get('permit_id')!=permit.id or dispatch.data.get('job_id')!=old_job.id
                or dispatch.data.get('stage_id')!=entry['stage_id'] or dispatch.data.get('scope_hash')!=pm['scope_hash']
                or dispatch.data.get('manifest_hash')!=permit.data['manifest_hash']
                or dispatch.data.get('intent_hash')!=digest(dispatch.data.get('intent'))):_deny()
        intent=dispatch.data['intent']
        expected_intent={'schema':'delegated-evidence-transport-intent-v1','workspace_id':principal.workspace_id,
                'actor_id':principal.user_id,'draft_id':row.id,'job_id':old_job.id,'stage_id':entry['stage_id'],
                'call_id':call.id,'permit_id':permit.id,'scope_hash':pm['scope_hash'],
                'request_hash':entry['request_hash'],'options_hash':entry['options_hash'],
                'token_bound':entry['token_bound']}
        if (set(intent)!=set(expected_intent)|{'issued_at','not_after','lease_until'}
                or any(intent.get(k)!=v for k,v in expected_intent.items())):_deny()
        try:
            issued=datetime.fromisoformat(intent['issued_at']);not_after=datetime.fromisoformat(intent['not_after'])
            lease_until=datetime.fromisoformat(intent['lease_until'])
            if (any(x.tzinfo is None or x.utcoffset() is None for x in (issued,not_after,lease_until))
                    or not_after<=issued or not_after>issued+timedelta(seconds=30)
                    or not_after>lease_until or not_after>end):_deny()
        except (TypeError,ValueError,KeyError):_deny()
        ia=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,Audit.object_id==dispatch.id,
            Audit.action=='cloud.delegated_evidence.transport_intent_v1').limit(2)
            .with_for_update(nowait=True).execution_options(populate_existing=True)).all()
        if (len(ia)!=1 or ia[0].actor_id!=principal.user_id or ia[0].data!={
            'schema':'delegated-evidence-transport-intent-v1','intent_hash':dispatch.data['intent_hash']}):_deny()
        _,receipt,state,descriptor=recovery._preflight(evidence,row,entry['stage_id'])
        if (state.get('ready') is not True or descriptor!=stage['journal']
                or any(call.data['receipt'].get(k)!=receipt.get(k) for k in receipt_fields)):_deny()
        immutable.extend((dispatch,ia[0]))
        used_dispatches.append(dispatch.id)
    actual_dispatches=db.scalars(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.kind=='cloud_extra_dispatch_v1',Job.data['job_id'].as_string()==old_job.id)
        .order_by(Job.id).with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    if {d.id for d in actual_dispatches}!=set(used_dispatches) or len(actual_dispatches)!=len(used_dispatches):_deny()
    history=row.data['recovery_history'][-1]
    if history.get('schema')!='report-evidence-recovery-receipt-v1':_deny()
    recovery_audits=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
        Audit.actor_id==principal.user_id,Audit.object_id==row.id,
        Audit.action=='setup.report_evidence.delegated_wire_recovered')
        .order_by(Audit.created_at.desc(),Audit.id.desc()).limit(1)
        .with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    if (len(recovery_audits)!=1 or recovery_audits[0].data.get('new_call') is not False
            or recovery_audits[0].data.get('remaining_count')!=len(stages)-len(used)):_deny()
    immutable.extend(recovery_audits)
    resume=evidence.resume_preview(row,principal,evidence.ResumePreviewCommand(revision=command.revision,
        request_id=command.request_id),can_start=False)
    if resume['preview_hash']!=command.preview_hash:_deny()
    records=_records(db,principal,old_job.id)
    if len(records)>=24:_deny()
    if (old_job.data.get('delegated_epoch_hash')!=(records[-1].data['hash'] if records else None)
            or old_job.data.get('delegated_epoch_no',0)!=len(records)):_deny()
    previous=records[-1].data['hash'] if records else None
    payload={'schema':'report-evidence-epoch-v1','epoch':len(records)+1,'previous_hash':previous,
        'workspace_id':principal.workspace_id,'actor_id':principal.user_id,'draft_id':row.id,'job_id':old_job.id,
        'grant_id':grant.id,'plan_hash':digest(stages),'origin_hash':link['origin_hash'],
        'consumed_count':len(used),'old_lease_hash':digest(old_job.lease_token),'resume_hash':resume['preview_hash'],
        'previous_attempts':old_job.attempts}
    marker=Job(id=str(uuid4()),workspace_id=principal.workspace_id,kind='report_evidence_epoch_v1',status='internal',
        idempotency_key='report-evidence-epoch:'+old_job.id+':'+str(payload['epoch']),data={})
    db.add(marker);db.flush()
    audit=Audit(workspace_id=principal.workspace_id,actor_id=principal.user_id,object_id=marker.id,
        action='cloud.report_epoch.handoff_v1',data={'epoch_hash':digest(payload)})
    db.add(audit);db.flush();marker.data={'payload':payload,'hash':digest(payload),'audit_id':audit.id}
    data=copy.deepcopy(row.data)
    for stage in data['maps'][len(used):]:stage.update(status='pending',error=None,error_code=None)
    receipt=evidence._resume_receipt(row,principal,command,resume)
    data.update(phase='map',error=None,error_code=None,
        resume_history=[*data['resume_history'],{'preview':resume,'receipt':receipt}])
    row.data=data;row.version=command.revision+1;row.status='queued';evidence._state_size(data)
    old_job.status='queued';old_job.lease_token=None;old_job.lease_until=None;old_job.available_at=clock
    # This is an explicit, verified recovery epoch, not an automatic retry.
    # Preserve the prior counter in the immutable epoch before resetting it;
    # the ordinary worker otherwise forces recovery-only after three attempts.
    old_job.attempts=0
    old_job.data={**old_job.data,'recovery_only':False,'delegated_epoch_hash':digest(payload),'delegated_epoch_no':payload['epoch']}
    db.flush()
    from .report_evidence_prebound_transport import _call_snapshot,_check_call_snapshot
    old_audits=[_locked(db,select(Audit).where(Audit.id==r.data['audit_id'],
        Audit.workspace_id==principal.workspace_id)) for r in records]
    if any(item is None for item in [actor,session_row,member,*old_audits]):_deny()
    objects=[row,old_job,marker,audit,actor,session_row,member,*immutable,*records,*old_audits]
    from sqlalchemy import inspect
    for item in objects:
        if inspect(item).unloaded:db.refresh(item)
    snapshots=[_call_snapshot(db,item) for item in objects]
    tx=db.get_transaction();checks=scoped._session_checks(db)
    if tx is None or tx in checks:_deny()
    def commit_check(session):
        if session.get_transaction() is not tx:_deny()
        for snapshot in snapshots:_check_call_snapshot(session,snapshot)
        at=session.scalar(select(func.clock_timestamp()))
        if at>=end or session_row.expires_at<=at or session_row.revoked or not actor.active or member.role!='admin':_deny()
        for i in range(len(used)):
            _,receipt_now,state,descriptor=recovery._preflight(evidence,row,stages[i]['stage_id'])
            call=next(c for c in calls if c.id==used[i]['call_id'])
            if (state.get('ready') is not True or descriptor!=row.data['maps'][i]['journal']
                    or any(call.data['receipt'].get(k)!=receipt_now.get(k) for k in receipt_fields)):_deny()
    checks[tx]=commit_check
    return {'epoch_id':marker.id,'epoch':payload['epoch'],'job_id':old_job.id,'new_cloud_calls':0,'replayed':False}
