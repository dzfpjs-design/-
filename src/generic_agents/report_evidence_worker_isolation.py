"""Versioned report jobs invisible to older generic worker claim queries.

Legacy 44c07f2 workers require available_at <= database time for every claim,
including unfiltered global workers. New delegated jobs carry a far-future
sentinel through queueing, execution, failure and explicit handoff. Only the
new explicitly job-id-scoped claim path may ignore the ordinary due-time rule.
The invariant assumes ordinary application writes preserve the sentinel; it
is not a substitute for a DB trigger against arbitrary external DB writers.
"""
from datetime import datetime, timezone

KIND = 'read_report_scoped_v1'
NOT_AVAILABLE = datetime(9999, 1, 1, tzinfo=timezone.utc)


def isolated(job):
    return bool(job is not None and job.kind == KIND and job.available_at == NOT_AVAILABLE)


def kind_for(row):
    from .report_reading_drafts import KIND as LEGACY_KIND
    return KIND if isinstance(row.request_id,str) and row.request_id.startswith('fl_') else LEGACY_KIND


def first_claim_eligible(db, job):
    """Fail closed unless this isolated Job has never consumed a model call.

    Caller holds the Job row lock. Handoff/epoch requeue is intentionally not
    admitted here until a separate claim protocol proves and authorizes it.
    """
    from copy import deepcopy
    from sqlalchemy import and_, func, inspect, or_, select
    from .contracts import digest
    from .models import Audit, Capability, CloudCall, Job, Membership, SetupReportDraft, User
    from . import cloud_report_plan_allowance as plans
    from . import cloud_scoped_extra_reservation as scoped

    if (not isolated(job) or job.status!='queued' or type(job.attempts) is not int or job.attempts!=0
            or job.lease_token is not None or job.lease_until is not None or type(job.data) is not dict
            or type(job.data.get('actor_id')) is not str or type(job.data.get('report_draft_id')) is not str
            or job.data.get('recovery_only',False) is not False
            or type(job.data.get('lease_recoveries',0)) is not int or job.data.get('lease_recoveries',0)!=0
            or job.data.get('delegated_epoch_hash') is not None or job.data.get('delegated_epoch_no',0)!=0):
        return False
    row=db.scalar(select(SetupReportDraft).where(SetupReportDraft.workspace_id==job.workspace_id,
        SetupReportDraft.id==job.data['report_draft_id']).with_for_update(nowait=True).execution_options(populate_existing=True))
    if (not row or row.actor_id!=job.data['actor_id'] or not row.request_id.startswith('fl_')
            or row.status!='queued' or type(row.data) is not dict
            or row.data.get('schema')!='report-evidence-draft-state-v1' or row.data.get('job_id')!=job.id):
        return False
    # Neither orphaned usage nor a prior handoff can be converted into a first
    # attempt merely by resetting the mutable attempt counter or stage state.
    if db.scalar(select(CloudCall.id).where(CloudCall.workspace_id==job.workspace_id,
            CloudCall.data['scoped_extra']['job_id'].as_string()==job.id).limit(1)):
        return False
    if db.scalar(select(Job.id).where(Job.workspace_id==job.workspace_id,Job.kind=='cloud_extra_dispatch_v1',
            Job.data['job_id'].as_string()==job.id).limit(1)):
        return False
    if db.scalar(select(Job.id).where(Job.workspace_id==job.workspace_id,Job.kind=='report_evidence_epoch_v1',
            or_(Job.data['payload']['job_id'].as_string()==job.id,
                Job.idempotency_key.startswith('report-evidence-epoch:'+job.id+':'))).limit(1)):
        return False
    if db.scalar(select(Job.id).where(Job.workspace_id==job.workspace_id,or_(
            Job.idempotency_key=='fl-initial-claim-v1:'+job.id,
            and_(Job.data['job_id'].as_string()==job.id,or_(
                Job.data['schema'].as_string()=='fl-initial-claim-v1',
                Job.data['draft_id'].as_string()==row.id)))).limit(1)):
        return False
    expected_claim={'schema':'fl-initial-claim-v1','job_id':job.id,
                    'draft_id':row.id,'actor_id':row.actor_id}
    # Older first-claim audits only contain payload_hash. Newer audits also
    # carry job_id. Either surviving, independently persisted witness blocks
    # a second claim even when the Job marker's index key was damaged.
    if db.scalar(select(Audit.id).where(Audit.workspace_id==job.workspace_id,or_(
            Audit.data['payload_hash'].as_string()==digest(expected_claim),
            and_(Audit.action=='cloud.report_scope.initial_claim_v1',
                 Audit.data['job_id'].as_string()==job.id))).limit(1)):
        return False
    phase=row.data.get('phase')
    reduce_objects=[]
    if phase=='map':
        if row.data.get('resume_history') or row.data.get('recovery_history'):
            return False
        stages=row.data.get('maps')
        if (type(stages) is not list or not stages or any(type(s) is not dict
                or s.get('status')!='pending' or s.get('inflight') is not False
                or s.get('reserved_call_id') is not None or s.get('journal') is not None for s in stages)):
            return False
        grant_key=plans.plan_key(job.id)
        grant_kind='cloud_report_plan_allowance_v1'
        expected_stages=[s.get('chunk_id') for s in stages]
    elif phase=='reduce':
        stage=row.data.get('reduction')
        if (type(stage) is not dict or stage.get('status')!='queued' or stage.get('inflight') is not False
                or stage.get('reserved_call_id') is not None or stage.get('journal') is not None):
            return False
        grant_key='evidence-reduce-allowance-v1:'+digest({'job_id':job.id})
        grant_kind='cloud_report_reduce_allowance_v1'
        expected_stages=['reduce']
    else:
        return False
    if any(type(s) is not str or not s for s in expected_stages) or len(set(expected_stages))!=len(expected_stages):
        return False
    grant=db.scalar(select(Job).where(Job.workspace_id==job.workspace_id,Job.idempotency_key==grant_key)
        .with_for_update(nowait=True).execution_options(populate_existing=True))
    if (not grant or grant.kind!=grant_kind or grant.status!='internal' or type(grant.data) is not dict
            or type(grant.data.get('manifest')) is not dict
            or grant.data.get('manifest_hash')!=digest(grant.data['manifest'])):
        return False
    manifest=grant.data['manifest']
    if (manifest.get('workspace_id')!=job.workspace_id or manifest.get('actor_id')!=row.actor_id
            or manifest.get('job_id')!=job.id or manifest.get('draft_id')!=row.id):
        return False
    clock=db.scalar(select(func.clock_timestamp()))
    if not (scoped._instant(manifest.get('not_before')) <= clock < scoped._instant(manifest.get('expires_at'))):
        return False
    user=db.scalar(select(User).where(User.id==row.actor_id).with_for_update(nowait=True)
                   .execution_options(populate_existing=True))
    member=db.scalar(select(Membership).where(Membership.workspace_id==job.workspace_id,
        Membership.user_id==row.actor_id).with_for_update(nowait=True).execution_options(populate_existing=True))
    if not user or not user.active or not member or member.role!='admin':
        return False
    cap=db.scalar(select(Capability).where(Capability.workspace_id==job.workspace_id,
        Capability.id==manifest.get('model_id')).with_for_update(nowait=True).execution_options(populate_existing=True))
    if (not cap or cap.kind!='foundation_model' or cap.status!='published'
            or cap.content_hash!=manifest.get('model_hash') or cap.validated_hash!=cap.content_hash
            or digest(cap.data)!=cap.content_hash or not isinstance(cap.data,dict)
            or cap.data.get('execution',{}).get('provider')!='deepseek'
            or cap.data.get('data_policy',{}).get('cloud_allowed') is not True):
        return False
    from .cloud_provider_policy import provider_enabled
    if not provider_enabled('deepseek'):
        return False
    if phase=='map':
        if (grant.data.get('consumed')!=[] or type(manifest.get('stage_plan')) is not list
                or any(type(x) is not dict for x in manifest['stage_plan'])
                or [x.get('stage_id') for x in manifest['stage_plan']]!=expected_stages
                or manifest.get('issuer_id')!=row.actor_id
                or manifest['stage_plan']!=plans.plan_from_preview(row.data['preview'])
                or manifest.get('source_hash')!=row.data['preview']['source_hash']
                or manifest.get('preview_hash')!=row.data['preview']['preview_hash']
                or manifest.get('max_calls')!=len(expected_stages)
                or manifest.get('max_tokens')!=sum(x['token_bound'] for x in manifest['stage_plan'])):
            return False
        grant_action='cloud.report_plan_allowance.issued_v1'
        grant_audit_data={'schema':'delegated-evidence-plan-issue-v1','grant_id':grant.id,
            'manifest_hash':grant.data['manifest_hash'],'allowance_preview_hash':manifest['allowance_preview_hash']}
    elif grant.data.get('used_call_id') is not None:
        return False
    else:
        from .security import Principal
        from .report_evidence_delegated_reduce_start import scope_hash as reduce_scope_hash
        pf=manifest.get('preflight')
        prepared=stage.get('preview')
        reduction=prepared.get('reduction_preparation') if isinstance(prepared,dict) else None
        contract=reduction.get('generation_contract') if isinstance(reduction,dict) else None
        if (not isinstance(pf,dict) or pf.get('preflight_hash')!=digest({k:v for k,v in pf.items() if k!='preflight_hash'})
                or manifest.get('preflight_hash')!=pf.get('preflight_hash')
                or pf.get('workspace_id')!=job.workspace_id or pf.get('actor_id')!=row.actor_id
                or pf.get('draft_id')!=row.id or pf.get('source_hash')!=row.data['preview']['source_hash']
                or manifest.get('source_hash')!=pf.get('source_hash')
                or manifest.get('original_job_id')!=pf.get('original_job_id')
                or manifest.get('map_results_hash')!=pf.get('map_results_hash')
                or pf.get('map_results_hash')!=digest([(s['chunk_id'],s['result_hash'],s['reserved_call_id'])
                                                       for s in row.data['maps']])
                or not isinstance(reduction,dict) or not isinstance(prepared,dict)
                or manifest.get('reduction_preview_hash')!=prepared.get('preview_hash')
                or manifest.get('reduction_preparation_hash')!=reduction.get('preparation_hash')
                or reduction.get('preparation_hash')!=digest({k:v for k,v in reduction.items() if k!='preparation_hash'})
                or not isinstance(contract,dict)
                or manifest.get('request_hash')!=reduction.get('messages_hash')
                or manifest.get('options_hash')!=digest(contract.get('dispatch_options'))
                or manifest.get('policy_hash')!=digest(contract.get('input_policy'))
                or manifest.get('token_bound')!=pf.get('token_bound')
                or type(manifest.get('max_calls')) is not int or manifest['max_calls']!=1
                or manifest.get('user_confirmed_business') is not False):
            return False
        principal=Principal(row.actor_id,job.workspace_id,member.role,'worker')
        if manifest.get('scope_hash')!=reduce_scope_hash(principal,row,job.id,pf):
            return False
        old_job=db.scalar(select(Job).where(Job.workspace_id==job.workspace_id,
            Job.id==manifest['original_job_id']).with_for_update(nowait=True).execution_options(populate_existing=True))
        old_grant=db.scalar(select(Job).where(Job.workspace_id==job.workspace_id,
            Job.idempotency_key==plans.plan_key(manifest['original_job_id'])).with_for_update(nowait=True)
            .execution_options(populate_existing=True))
        if (not isolated(old_job) or old_job.status!='succeeded'
                or old_job.lease_until is not None and old_job.lease_until>clock or not old_grant
                or old_grant.kind!='cloud_report_plan_allowance_v1' or old_grant.status!='internal'
                or type(old_grant.data) is not dict or type(old_grant.data.get('manifest')) is not dict
                or old_grant.data.get('manifest_hash')!=digest(old_grant.data['manifest'])
                or old_grant.data['manifest'].get('job_id')!=old_job.id
                or old_grant.data['manifest'].get('workspace_id')!=job.workspace_id
                or old_grant.data['manifest'].get('actor_id')!=row.actor_id
                or old_grant.data['manifest'].get('draft_id')!=row.id
                or old_grant.data['manifest_hash']!=pf.get('map_plan_hash')):
            return False
        reduce_objects=[old_job,old_grant]
        grant_action='cloud.report_reduce_allowance.issued_v1'
        grant_audit_data={'grant_id':grant.id,'manifest_hash':grant.data['manifest_hash']}
    grant_audits=db.scalars(select(Audit).where(Audit.workspace_id==job.workspace_id,
        Audit.object_id==grant.id,Audit.action==grant_action).limit(2).with_for_update(nowait=True)
        .execution_options(populate_existing=True)).all()
    if (len(grant_audits)!=1 or grant_audits[0].id!=grant.data.get('audit_id')
            or grant_audits[0].actor_id!=row.actor_id or grant_audits[0].data!=grant_audit_data):
        return False
    keys=[scoped.permit_key(job.id,stage) for stage in expected_stages]
    permits=db.scalars(select(Job).where(Job.workspace_id==job.workspace_id,Job.idempotency_key.in_(keys))
        .order_by(Job.id).with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    if len(permits)!=len(keys):
        return False
    permit_audits=db.scalars(select(Audit).where(Audit.workspace_id==job.workspace_id,
        Audit.object_id.in_([p.id for p in permits]),Audit.action=='cloud.extra_allowance.issued_v1')
        .order_by(Audit.id).with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    if len(permit_audits)!=len(permits):
        return False
    audits_by_object={a.object_id:a for a in permit_audits}
    expiries=[scoped._instant(manifest['expires_at'])]
    for permit in permits:
        data=permit.data
        if (permit.kind!='cloud_extra_allowance_v1' or permit.status!='internal' or type(data) is not dict
                or set(data)!=scoped._PERMIT_KEYS or type(data.get('manifest')) is not dict
                or frozenset(data['manifest'])!=scoped._MANIFEST_KEYS
                or data.get('schema')!='cloud-extra-allowance-record-v1'
                or data.get('manifest_hash')!=digest(data['manifest'])
                or data.get('used_call_id') is not None or data['manifest'].get('job_id')!=job.id
                or data['manifest'].get('stage_id') not in expected_stages
                or permit.idempotency_key!=scoped.permit_key(job.id,data['manifest']['stage_id'])):
            return False
        pm=data['manifest'];stage_id=pm['stage_id']
        pa=audits_by_object.get(permit.id)
        if (not pa or pa.id!=data.get('audit_id') or pa.actor_id!=row.actor_id
                or pa.data!={'schema':'cloud-extra-allowance-issue-v1','permit_id':permit.id,
                             'manifest_hash':data['manifest_hash']}
                or pm.get('schema')!='cloud-extra-allowance-manifest-v1'
                or pm.get('workspace_id')!=job.workspace_id or pm.get('actor_id')!=row.actor_id
                or pm.get('issuer_id')!=row.actor_id or pm.get('job_kind')!=job.kind
                or pm.get('permit_id')!=permit.id or pm.get('capability_id')!=cap.id
                or pm.get('capability_hash')!=cap.content_hash or type(pm.get('max_calls')) is not int
                or pm.get('max_calls')!=1 or type(pm.get('token_bound')) is not int
                or type(pm.get('max_tokens')) is not int or pm['token_bound']<1
                or pm['max_tokens']!=pm['token_bound']
                or not (scoped._instant(pm.get('not_before')) <= clock < scoped._instant(pm.get('expires_at')))):
            return False
        if phase=='map':
            entry=next(x for x in manifest['stage_plan'] if x['stage_id']==stage_id)
            expected_scope=plans.scope_hash(job.workspace_id,row.actor_id,row.id,job.id,
                manifest['source_hash'],manifest['preview_hash'],manifest['origin_hash'],stage_id)
            if (pm.get('scope_kind')!=plans.SCOPE or pm.get('scope_hash')!=expected_scope
                    or pm.get('request_hash')!=entry['request_hash'] or pm.get('options_hash')!=entry['options_hash']
                    or pm.get('token_bound')!=entry['token_bound']):
                return False
        else:
            from .report_evidence_delegated_reduce_start import SCOPE as REDUCE_SCOPE
            if (pm.get('scope_kind')!=REDUCE_SCOPE or pm.get('scope_hash')!=manifest.get('scope_hash')
                    or pm.get('request_hash')!=manifest.get('request_hash')
                    or pm.get('options_hash')!=manifest.get('options_hash')
                    or pm.get('token_bound')!=manifest.get('token_bound')):
                return False
        expiries.append(scoped._instant(pm['expires_at']))
    from .report_evidence_delegated_dispatch_gate import ready
    activation_records=[]
    if not ready():
        from .security import Principal
        from . import report_evidence_scoped_activation as activation
        map_job_id=job.id if phase=='map' else manifest.get('original_job_id')
        if type(map_job_id) is not str:
            return False
        active,active_audit,active_expiry=activation.for_job(
            db,Principal(row.actor_id,job.workspace_id,member.role,'worker'),row,map_job_id=map_job_id,lock=True)
        activation_records=[active,active_audit];expiries.append(active_expiry)
    objects=[row,grant,*grant_audits,cap,user,member,*permits,*permit_audits,*activation_records,*reduce_objects]
    snapshots=[]
    for obj in objects:
        state=inspect(obj)
        if (not state.persistent or state.session is not db or state.deleted or state.detached
                or state.expired_attributes or state.unloaded):
            return False
        snapshots.append((obj,{col.key:deepcopy(getattr(obj,col.key)) for col in state.mapper.column_attrs}))
    return {'snapshots':snapshots,'expires_at':min(expiries)}
