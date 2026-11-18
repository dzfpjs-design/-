"""Disabled, internal authorization contract for one delegated reduction.

The existing maps are paid evidence, not business approval. This module does
not send a request or make the production dispatch gate configurable.
"""
from __future__ import annotations

import copy
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from .contracts import digest
from .models import Audit, AuthSession, Capability, CloudCall, Job, Membership, User
from .security import Principal
from . import report_evidence_pipeline as evidence
from . import report_reading_drafts as legacy


SCOPE = 'delegated-evidence-reduce-v1'
router = APIRouter()


class ReduceStartRequest(evidence.ReducePreviewCommand):
    reduction_preview_hash: str = Field(pattern=r'^[0-9a-f]{64}$', strict=True)
    preflight_hash: str = Field(pattern=r'^[0-9a-f]{64}$', strict=True)
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def exact_consent(cls, value):
        if value is not True:
            raise ValueError('必须单独同意本次汇总')
        return value


class ReduceAuthorizationDenied(ValueError):
    pass


def _enabled():
    return False


def _deny():
    raise ReduceAuthorizationDenied('汇总授权范围或原记录无法核对；未新增调用')


def scope_hash(principal, row, job_id, preflight):
    return digest({'schema':SCOPE,'workspace_id':principal.workspace_id,'actor_id':principal.user_id,
        'draft_id':row.id,'job_id':job_id,'source_hash':preflight['source_hash'],
        'map_plan_hash':preflight['map_plan_hash'],'map_results_hash':preflight['map_results_hash'],
        'preflight_hash':preflight['preflight_hash']})


def preview_tx(db, principal, request, draft_id, command, *, with_receipt_binding=False):
    from . import report_evidence_delegated_reduce_preflight as preflight
    from . import report_evidence_pipeline as evidence
    from . import report_reading_drafts as legacy
    from .report_full_provisional import _lock_authority
    if not isinstance(db,Session) or type(principal) is not Principal or principal.role != 'admin':
        _deny()
    legacy.workspace_draft(db,principal)
    _lock_authority(db,principal,request)
    row=evidence.owned(db,principal,draft_id)
    result=preflight.preview(db,principal,row,command, _with_receipt_binding=with_receipt_binding)
    value=result[0] if with_receipt_binding else result
    if value['map_calls'] != 14:
        _deny()
    return result


def start_tx(db, principal, request, draft_id, command, approved_preflight_hash):
    """Issue a single job and permit atomically; caller owns commit/rollback."""
    from . import cloud_scoped_extra_reservation as scoped
    from . import report_evidence_delegated_origin as origin
    from . import report_evidence_delegated_reduce_preflight as preflight
    from . import report_evidence_pipeline as evidence
    from . import report_reading_drafts as legacy
    from .report_full_provisional import _lock_authority
    from .report_evidence_delegated_dispatch_gate import ready
    if (not isinstance(db,Session) or type(principal) is not Principal
            or principal.role != 'admin' or type(command) is not evidence.ReduceCommand
            or command.consent is not True or type(approved_preflight_hash) is not str):
        _deny()
    row=evidence.owned(db,principal,draft_id)
    prior=row.data['reduction']['start_receipt']
    if prior is not None:
        legacy.workspace_draft(db,principal)
        _lock_authority(db,principal,request)
        if (prior['request_id']!=command.request_id or prior['command_hash']!=evidence.command_hash(
                principal,command,kind='report_evidence_reduce',draft_id=row.id)):
            _deny()
        original_job_id=row.data['job_id']
        grant=db.scalar(select(Job).where(Job.workspace_id==principal.workspace_id,
            Job.idempotency_key=='evidence-reduce-allowance-v1:'+digest({'job_id':original_job_id})))
        permit=db.scalar(select(Job).where(Job.workspace_id==principal.workspace_id,
            Job.idempotency_key==scoped.permit_key(original_job_id,'reduce')))
        if (not grant or grant.kind!='cloud_report_reduce_allowance_v1' or not isinstance(grant.data,dict)
                or grant.data.get('manifest_hash')!=digest(grant.data.get('manifest'))
                or grant.data['manifest'].get('preflight_hash')!=approved_preflight_hash
                or grant.data['manifest'].get('draft_id')!=row.id
                or grant.data['manifest'].get('job_id')!=original_job_id
                or not permit or permit.kind!='cloud_extra_allowance_v1' or not isinstance(permit.data,dict)
                or permit.data.get('manifest_hash')!=digest(permit.data.get('manifest'))):
            _deny()
        pm=permit.data['manifest'];gm=grant.data['manifest']
        if (pm.get('workspace_id')!=principal.workspace_id or pm.get('actor_id')!=principal.user_id
                or pm.get('job_id')!=original_job_id or pm.get('permit_id')!=permit.id
                or pm.get('stage_id')!='reduce' or pm.get('scope_kind')!=SCOPE
                or pm.get('scope_hash')!=gm.get('scope_hash') or pm.get('request_hash')!=gm.get('request_hash')
                or pm.get('options_hash')!=gm.get('options_hash') or pm.get('token_bound')!=gm.get('token_bound')
                or permit.data.get('used_call_id')!=grant.data.get('used_call_id')):
            _deny()
        issue=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
            Audit.object_id==grant.id,Audit.action=='cloud.report_reduce_allowance.issued_v1').limit(2)).all()
        if (len(issue)!=1 or issue[0].id!=grant.data.get('audit_id') or issue[0].actor_id!=principal.user_id
                or issue[0].data!={'grant_id':grant.id,'manifest_hash':grant.data['manifest_hash']}):
            _deny()
        permit_audits=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
            Audit.object_id==permit.id,Audit.action=='cloud.extra_allowance.issued_v1').limit(2)).all()
        if (len(permit_audits)!=1 or permit_audits[0].id!=permit.data.get('audit_id')
                or permit_audits[0].actor_id!=principal.user_id
                or permit_audits[0].data!={'schema':'cloud-extra-allowance-issue-v1',
                    'permit_id':permit.id,'manifest_hash':permit.data['manifest_hash']}):
            _deny()
        return {'job_id':original_job_id,'grant_id':grant.id,'permit_id':permit.id,
            'receipt':copy.deepcopy(prior),'replayed':True}
    activation=None
    if not (_enabled() and ready()):
        # A separately consented reduce is permitted only under the same
        # report's live activation, locked through issuance and commit.
        legacy.workspace_draft(db,principal)
        _lock_authority(db,principal,request)
        link=origin.verify_link(db,principal,row,lock=False,require_current=True)
        from . import report_evidence_scoped_activation as scope_activation
        activation=scope_activation.for_job(db,principal,row,map_job_id=link['payload']['new_job_id'],lock=True)
    # A completed map worker must release its fence before a new job may be
    # prepared. NOWAIT avoids workspace -> worker lock inversions.
    link=origin.verify_link(db,principal,row,lock=False,require_current=True)
    old_job=db.scalar(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.id==link['payload']['new_job_id']).with_for_update(nowait=True).execution_options(populate_existing=True))
    clock=db.scalar(select(func.clock_timestamp()))
    if (not old_job or old_job.status != 'succeeded' or old_job.lease_until and old_job.lease_until > clock):
        _deny()
    legacy.workspace_draft(db,principal)
    _lock_authority(db,principal,request)
    row=evidence.owned(db,principal,draft_id,True)
    if row.status!='awaiting_merge' or row.data['reduction']['start_receipt'] is not None:
        _deny()
    # Lock every map ledger record before ever creating a new call/budget row.
    ids=[stage.get('reserved_call_id') for stage in row.data['maps']]
    if len(ids)!=14 or any(type(i) is not str or not i for i in ids):
        _deny()
    retained_calls=db.scalars(select(CloudCall).where(CloudCall.workspace_id==principal.workspace_id,
        CloudCall.id.in_(ids)).order_by(CloudCall.id).with_for_update(nowait=True)).all()
    map_records=db.scalars(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.idempotency_key.in_([key for s in row.data['maps'] for key in
            (scoped.permit_key(old_job.id,s['chunk_id']),scoped.dispatch_key(old_job.id,s['chunk_id']))]))
        .order_by(Job.id).with_for_update(nowait=True)).all()
    from . import cloud_report_plan_allowance as plans
    map_grant=db.scalar(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.idempotency_key==plans.plan_key(old_job.id)).with_for_update(nowait=True))
    if not map_grant or len(map_records)!=28:_deny()
    retained_audits=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
        Audit.object_id.in_([map_grant.id,*[item.id for item in map_records]]),
        Audit.action.in_(['cloud.report_plan_allowance.issued_v1','cloud.extra_allowance.issued_v1',
                          'cloud.delegated_evidence.transport_intent_v1']))
        .order_by(Audit.id).with_for_update(nowait=True)).all()
    check_command=evidence.ReducePreviewCommand(revision=command.revision,request_id=command.request_id)
    # SQLAlchemy's identity map holds weak references. Retain every record
    # loaded during source/receipt verification through final commit.
    retained=list(db.identity_map.values())
    def retain_loaded(_session, instance):
        retained.append(instance)
    event.listen(db,'loaded_as_persistent',retain_loaded)
    try:
        checked=preflight.preview(db,principal,row,check_command)
    finally:
        event.remove(db,'loaded_as_persistent',retain_loaded)
    if checked['map_calls']!=14 or checked['preflight_hash']!=approved_preflight_hash:
        _deny()
    reduction=evidence.reduce_preview(row,principal,check_command)
    if command.preview_hash!=reduction['preview_hash']:
        _deny()
    spec=reduction['reduction_preparation']['generation_contract']
    prepared=reduction['reduction_preparation']
    if spec.get('evidence_stage')!='reduce-v2' or prepared['call_count']!=1:
        _deny()
    cap=evidence._fresh_cap(db,principal,row)
    cap=db.scalar(select(Capability).where(Capability.workspace_id==principal.workspace_id,
        Capability.id==cap.id).with_for_update(nowait=True))
    user=db.scalar(select(User).where(User.id==principal.user_id).with_for_update(nowait=True))
    member=db.scalar(select(Membership).where(Membership.workspace_id==principal.workspace_id,
        Membership.user_id==principal.user_id).with_for_update(nowait=True))
    session=db.scalar(select(AuthSession).where(AuthSession.id==principal.session_id).with_for_update(nowait=True))
    if not user or not user.active or not member or member.role!='admin' or not session or session.revoked:
        _deny()
    from .report_evidence_worker_isolation import KIND as SCOPED_KIND, NOT_AVAILABLE
    job=Job(workspace_id=principal.workspace_id,kind=SCOPED_KIND,status='queued',available_at=NOT_AVAILABLE,
        idempotency_key='evidence-reduce:'+command.request_id,
        data={'actor_id':principal.user_id,'report_draft_id':row.id})
    db.add(job);db.flush()
    grant=Job(workspace_id=principal.workspace_id,kind='cloud_report_reduce_allowance_v1',status='internal',
        idempotency_key='evidence-reduce-allowance-v1:'+digest({'job_id':job.id}),data={})
    db.add(grant);db.flush()
    permit=Job(workspace_id=principal.workspace_id,kind='cloud_extra_allowance_v1',status='internal',
        idempotency_key=scoped.permit_key(job.id,'reduce'),data={})
    db.add(permit);db.flush()
    now=db.scalar(select(func.clock_timestamp()));end=now+timedelta(hours=24)
    if activation is not None:
        end=min(end,activation[2])
    if end<=now:
        _deny()
    scope=scope_hash(principal,row,job.id,checked)
    manifest={'schema':'delegated-reduce-allowance-v1','workspace_id':principal.workspace_id,
        'actor_id':principal.user_id,'draft_id':row.id,'job_id':job.id,'original_job_id':old_job.id,
        'preflight':checked,'preflight_hash':checked['preflight_hash'],'source_hash':checked['source_hash'],
        'map_results_hash':checked['map_results_hash'],'reduction_preview_hash':reduction['preview_hash'],
        'reduction_preparation_hash':prepared['preparation_hash'],'model_id':cap.id,'model_hash':cap.content_hash,
        'request_hash':prepared['messages_hash'],'options_hash':digest(spec['dispatch_options']),
        'policy_hash':digest(spec['input_policy']),'token_bound':checked['token_bound'],
        'max_calls':1,'not_before':now.isoformat(),'expires_at':end.isoformat(),
        'scope_hash':scope,'user_confirmed_business':False}
    ga=Audit(workspace_id=principal.workspace_id,actor_id=principal.user_id,object_id=grant.id,
        action='cloud.report_reduce_allowance.issued_v1',data={'grant_id':grant.id,'manifest_hash':digest(manifest)})
    db.add(ga);db.flush()
    grant.data={'schema':'delegated-reduce-allowance-record-v1','manifest':manifest,
        'manifest_hash':digest(manifest),'audit_id':ga.id,'used_call_id':None}
    pm={'schema':'cloud-extra-allowance-manifest-v1','workspace_id':principal.workspace_id,
        'actor_id':principal.user_id,'issuer_id':principal.user_id,'job_id':job.id,'job_kind':job.kind,
        'permit_id':permit.id,'capability_id':cap.id,'capability_hash':cap.content_hash,
        'scope_kind':SCOPE,'stage_id':'reduce','scope_hash':scope,'request_hash':prepared['messages_hash'],
        'options_hash':digest(spec['dispatch_options']),'token_bound':checked['token_bound'],
        'max_tokens':checked['token_bound'],'max_calls':1,'not_before':now.isoformat(),'expires_at':end.isoformat()}
    pa=Audit(workspace_id=principal.workspace_id,actor_id=principal.user_id,object_id=permit.id,
        action='cloud.extra_allowance.issued_v1',data={'schema':'cloud-extra-allowance-issue-v1',
            'permit_id':permit.id,'manifest_hash':digest(pm)})
    db.add(pa);db.flush()
    permit.data={'schema':'cloud-extra-allowance-record-v1','manifest':pm,'manifest_hash':digest(pm),
        'audit_id':pa.id,'used_call_id':None}
    receipt=evidence._reduce_receipt(row,principal,command,reduction)
    row.status='queued'
    evidence._set(row,{**row.data,'phase':'reduce','job_id':job.id,
        'reduction':evidence._empty_stage('queued',preview=reduction,start_receipt=receipt)})
    ra=Audit(workspace_id=principal.workspace_id,actor_id=principal.user_id,object_id=row.id,
        action='setup.report_evidence.reduce_authorize',data={'request_id':command.request_id,
            'preview_hash':reduction['preview_hash']})
    db.add(ra);db.flush()
    # A private commit witness detects caller-side mutation of every loaded
    # persistent column; files are re-read separately immediately before send.
    # Reuse the same all-column and same-connection DB checks as the reduce
    # transport; a late direct SQL listener must not rewrite issuance proof.
    from .report_evidence_reduce_transport import _snapshots, _check_snapshots, _check_database_snapshots
    retained.extend(retained_calls);retained.extend(retained_audits)
    # All changes were flushed above. Load only missing mapped columns; do not
    # refresh values already checked by preflight or replace pending writes.
    for obj in list(db.identity_map.values()):
        state=inspect(obj)
        if not state.persistent or state.deleted or state.detached or obj in db.deleted:
            _deny()
        missing=set(state.unloaded)
        columns={col.key for col in state.mapper.column_attrs}
        if missing - columns:
            _deny()
        if missing:
            db.refresh(obj,attribute_names=sorted(missing))
    snapshots=_snapshots(db)
    transaction=db.get_transaction();checks=scoped._session_checks(db)
    if transaction is None or transaction in checks:_deny()
    def check_at_commit(s):
        current=s.scalar(select(func.clock_timestamp()))
        if s.get_transaction() is not transaction or current>=end or session.expires_at<=current:
            _deny()
        try:
            _check_snapshots(s,snapshots)
        except Exception:
            _deny()
    checks[transaction]=check_at_commit
    connection=db.connection()
    def verify_database_at_commit(conn):
        try:
            _check_snapshots(db,snapshots)
            _check_database_snapshots(conn,snapshots)
        except Exception:
            _deny()
    def cleanup(_session, ended):
        if ended is transaction and event.contains(connection,'commit',verify_database_at_commit):
            event.remove(connection,'commit',verify_database_at_commit)
    event.listen(connection,'commit',verify_database_at_commit)
    event.listen(db,'after_transaction_end',cleanup)
    return {'job_id':job.id,'grant_id':grant.id,'permit_id':permit.id,'receipt':copy.deepcopy(receipt),'replayed':False}


def _preview_response(identity, body, request, response, p, db, *, with_receipt_binding=False):
    from .report_evidence_delegated_reduce_preflight import ReducePreflightDenied
    try:
        result=preview_tx(db,p,request,str(identity),body,with_receipt_binding=with_receipt_binding)
        value,binding = result if with_receipt_binding else (result,None)
    except (ReducePreflightDenied,ReduceAuthorizationDenied):
        raise HTTPException(409,detail={'code':'delegated_reduce_preflight_blocked',
            'message':'当前仅支持 14 个分段全部完成且依据、账本可核对的报告；未签发额度或调用模型。'}) from None
    active=False
    try:
        from . import report_evidence_scoped_activation as scope_activation
        row=evidence.owned(db,p,str(identity))
        scope_activation.for_job(db,p,row,map_job_id=value['original_job_id'],lock=False)
        active=True
    except HTTPException:
        pass
    db.rollback()
    response.headers['Cache-Control']='private, no-store'
    payload = {'schema':'delegated-reduce-preview-v2' if with_receipt_binding else 'delegated-reduce-preview-v1',
        'preflight':value,
        'supported_map_count':14,'maximum_call_count':1,'token_upper_bound':value['token_bound'],
        'price_limit_available':False,'price_upper_bound':None,
        'price_note':'未取得可核对的模型单价，无法计算或保证费用上限。',
        'additional_cost':True,'user_confirmed_business':False,
        'can_start':active,'can_send':active,
        'start_blocker':None if active else '该报告的定向启用未生效或已撤销；当前仅供核对。'}
    if with_receipt_binding:
        payload['receipt_binding']=binding
    return payload


@router.post('/report-evidence-readings/{identity}/delegated-reduce/preview')
def preview_route(identity: UUID, body: evidence.ReducePreviewCommand, request: Request, response: Response,
                  p: legacy.P, db: legacy.DB):
    return _preview_response(identity, body, request, response, p, db)


@router.post('/report-evidence-readings/{identity}/delegated-reduce/preview-v2')
def preview_v2_route(identity: UUID, body: evidence.ReducePreviewCommand, request: Request, response: Response,
                     p: legacy.P, db: legacy.DB):
    return _preview_response(identity, body, request, response, p, db, with_receipt_binding=True)


@router.post('/report-evidence-readings/{identity}/delegated-reduce/start', status_code=202)
def start_route(identity: UUID, body: ReduceStartRequest, request: Request, response: Response,
                p: legacy.P, db: legacy.DB):
    from .report_evidence_delegated_reduce_preflight import ReducePreflightDenied
    command=evidence.ReduceCommand(revision=body.revision,request_id=body.request_id,
        preview_hash=body.reduction_preview_hash,consent=body.consent)
    try:
        result=start_tx(db,p,request,str(identity),command,body.preflight_hash)
        db.commit()
    except (ReducePreflightDenied,ReduceAuthorizationDenied):
        db.rollback()
        raise HTTPException(409,detail={'code':'delegated_reduce_not_authorized',
            'message':'汇总范围、授权或原记录无法核对；未新增调用。'}) from None
    response.headers['Cache-Control']='private, no-store'
    return {'schema':'delegated-reduce-start-v1','job_id':result['job_id'],
        'receipt':result['receipt'],'replayed':result['replayed'],
        'user_confirmed_business':False,'can_apply':False,'can_publish':False}
