"""Read-only preflight for a separately authorised delegated reduction.

This module grants no allowance and creates no job, call or transport intent.
It is not an HTTP route and cannot enable the production dispatch gate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import digest
from .models import Audit, CloudCall, Job
from .security import Principal


class ReducePreflightDenied(ValueError):
    pass


def _receipt_binding(value, prepared, row, principal, command):
    """Bind receipt fields to the verified reduction, without returning source."""
    try:
        reduction = prepared['reduction_preparation']
        sha = lambda x: type(x) is str and re.fullmatch(r'[a-f0-9]{64}', x) is not None
        if (type(value) is not dict or type(prepared) is not dict or type(reduction) is not dict
                or value['preflight_hash'] != digest({k:v for k,v in value.items() if k != 'preflight_hash'})
                or value['draft_id'] != row.id or value['workspace_id'] != principal.workspace_id
                or value['actor_id'] != principal.user_id or value['revision'] != command.revision
                or value['reduction_preview_hash'] != prepared['preview_hash']
                or prepared['body'] != {'revision':command.revision, 'request_id':command.request_id}
                or value['reduction_preparation_hash'] != digest(reduction)
                or reduction['preparation_hash'] != digest({k:v for k,v in reduction.items() if k != 'preparation_hash'})
                or reduction['map_plan_hash'] != row.data['preview']['map_preparation']['plan']['plan_hash']
                or not all(sha(x) for x in (value['preflight_hash'], value['map_plan_hash'],
                    value['reduction_preview_hash'], reduction['preparation_hash'],
                    reduction['map_plan_hash'], reduction['map_inputs_hash']))):
            raise ReducePreflightDenied()
        # V1 preflight's map_plan_hash is the allowance manifest digest.  Keep
        # that historic meaning there; the v2 binding separately names both
        # the real map plan and the manifest to match the eventual receipt.
        binding = {'schema':'delegated-reduce-receipt-binding-v1',
            'workspace_id':principal.workspace_id, 'actor_id':principal.user_id, 'draft_id':row.id,
            'revision':command.revision, 'request_id':command.request_id,
            'preflight_hash':value['preflight_hash'], 'reduction_preview_hash':prepared['preview_hash'],
            'map_plan_hash':reduction['map_plan_hash'], 'map_inputs_hash':reduction['map_inputs_hash'],
            'reduction_preparation_hash':reduction['preparation_hash'],
            'map_grant_manifest_hash':value['map_plan_hash']}
        return {**binding, 'binding_hash':digest(binding)}
    except ReducePreflightDenied:
        raise
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ReducePreflightDenied('汇总回执范围无法核对；未签发任何额度') from None


def _valid_map_intent(actual, expected, manifest, permit_manifest):
    if type(actual) is not dict:
        return False
    extra=set(actual)-set(expected)
    if extra == {'issued_at','not_after','lease_until'}:
        def instant(value):
            parsed=datetime.fromisoformat(value)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError('timezone')
            return parsed.astimezone(timezone.utc)
        try:
            issued=instant(actual['issued_at']);deadline=instant(actual['not_after'])
            lease=instant(actual['lease_until'])
            if (deadline<=issued or deadline!=min(issued+timedelta(seconds=30),lease,
                    instant(manifest['expires_at']),instant(permit_manifest['expires_at']))
                    or issued<instant(manifest['not_before']) or issued<instant(permit_manifest['not_before'])):
                return False
        except (TypeError,ValueError,OverflowError,KeyError):
            return False
    elif extra:
        return False
    base={k:v for k,v in actual.items() if k not in extra}
    return base == expected and digest(base) == digest(expected)


def preview(db: Session, principal: Principal, row, command, *, _rechecking=False, _with_receipt_binding=False):
    """Verify the completed original map plan before describing extra cost.

    The caller must discard this result when any source or DB state changes.
    This result is not an authorization and must never be accepted as one.
    """
    from . import cloud_report_plan_allowance as plans
    from . import cloud_scoped_extra_reservation as scoped
    from . import report_evidence_delegated_origin as origin
    from . import report_evidence_delegated_wire_v1 as wire
    from . import report_evidence_pipeline as pipeline
    try:
        if (not isinstance(db, Session) or type(principal) is not Principal
                or principal.role not in ('admin', 'operator')
                or not row.request_id.startswith('fl_')
                or not (row.status == 'awaiting_merge' if not _rechecking else
                    row.status == 'running' and row.data.get('phase') == 'reduce')
                or row.workspace_id != principal.workspace_id or row.actor_id != principal.user_id
                or type(command) is not pipeline.ReducePreviewCommand):
            raise ReducePreflightDenied()
        link = origin.verify_link(db, principal, row, lock=False, require_current=True)
        original_job = link['payload']['new_job_id']
        cap = pipeline._fresh_cap(db, principal, row)
        stages = plans.plan_from_preview(row.data['preview'])
        grant = db.scalar(select(Job).where(Job.workspace_id == principal.workspace_id,
            Job.idempotency_key == plans.plan_key(original_job)))
        if (not grant or grant.kind != 'cloud_report_plan_allowance_v1' or grant.status != 'internal'
                or type(grant.data) is not dict or set(grant.data) != {'schema','manifest','manifest_hash','audit_id','consumed'}
                or grant.data['schema'] != plans.RECORD or grant.data['manifest_hash'] != digest(grant.data['manifest'])):
            raise ReducePreflightDenied()
        manifest, consumed = grant.data['manifest'], grant.data['consumed']
        if (manifest.get('workspace_id') != principal.workspace_id or manifest.get('actor_id') != principal.user_id
                or manifest.get('draft_id') != row.id or manifest.get('job_id') != original_job
                or manifest.get('origin_hash') != link['origin_hash']
                or manifest.get('source_hash') != row.data['preview']['source_hash']
                or manifest.get('preview_hash') != row.data['preview']['preview_hash']
                or manifest.get('model_id') != cap.id or manifest.get('model_hash') != cap.content_hash
                or manifest.get('stage_plan') != stages or manifest.get('max_calls') != len(stages)
                or manifest.get('max_tokens') != sum(s['token_bound'] for s in stages)
                or type(consumed) is not list or len(consumed) != len(stages)):
            raise ReducePreflightDenied()
        audits = db.scalars(select(Audit).where(Audit.workspace_id == principal.workspace_id,
            Audit.object_id == grant.id, Audit.action == 'cloud.report_plan_allowance.issued_v1').limit(2)).all()
        if (len(audits) != 1 or audits[0].id != grant.data['audit_id'] or audits[0].actor_id != principal.user_id
                or audits[0].data != {'schema':'delegated-evidence-plan-issue-v1','grant_id':grant.id,
                    'manifest_hash':grant.data['manifest_hash'],'allowance_preview_hash':manifest['allowance_preview_hash']}):
            raise ReducePreflightDenied()
        call_ids = [item['call_id'] for item in consumed]
        calls = db.scalars(select(CloudCall).where(CloudCall.workspace_id == principal.workspace_id,
            CloudCall.id.in_(call_ids))).all()
        plans.validate_progress(stages, consumed, {call.id:call for call in calls},
            workspace_id=principal.workspace_id, actor_id=principal.user_id, capability_id=cap.id, job_id=original_job)
        if ([s['chunk_id'] for s in row.data['maps']] != [s['stage_id'] for s in stages]
                or any(s['status'] != 'succeeded' or s['reserved_call_id'] != c['call_id']
                       for s,c in zip(row.data['maps'], consumed))):
            raise ReducePreflightDenied()
        for stage, call_id in zip(stages, call_ids):
            permit = db.scalar(select(Job).where(Job.workspace_id == principal.workspace_id,
                Job.idempotency_key == scoped.permit_key(original_job, stage['stage_id'])))
            dispatch = db.scalar(select(Job).where(Job.workspace_id == principal.workspace_id,
                Job.idempotency_key == scoped.dispatch_key(original_job, stage['stage_id'])))
            if (not permit or permit.kind != 'cloud_extra_allowance_v1' or permit.status != 'internal'
                    or type(permit.data) is not dict or permit.data.get('used_call_id') != call_id
                    or permit.data.get('manifest_hash') != digest(permit.data.get('manifest'))
                    or not dispatch or dispatch.kind != 'cloud_extra_dispatch_v1' or dispatch.status != 'internal'
                    or type(dispatch.data) is not dict or dispatch.data.get('state') != 'transport_intent'
                    or dispatch.data.get('call_id') != call_id or dispatch.data.get('permit_id') != permit.id
                    or dispatch.data.get('intent_hash') != digest(dispatch.data.get('intent'))):
                raise ReducePreflightDenied()
            pm, dd = permit.data['manifest'], dispatch.data
            scope = plans.scope_hash(principal.workspace_id, principal.user_id, row.id, original_job,
                manifest['source_hash'], manifest['preview_hash'], link['origin_hash'], stage['stage_id'])
            if (pm.get('workspace_id') != principal.workspace_id or pm.get('actor_id') != principal.user_id
                    or pm.get('issuer_id') != principal.user_id or pm.get('job_id') != original_job
                    or pm.get('permit_id') != permit.id or pm.get('stage_id') != stage['stage_id']
                    or pm.get('scope_kind') != plans.SCOPE or pm.get('scope_hash') != scope
                    or pm.get('capability_id') != cap.id or pm.get('capability_hash') != cap.content_hash
                    or pm.get('request_hash') != stage['request_hash'] or pm.get('options_hash') != stage['options_hash']
                    or pm.get('token_bound') != stage['token_bound'] or pm.get('max_tokens') != stage['token_bound']
                    or pm.get('max_calls') != 1 or dd.get('job_id') != original_job
                    or dd.get('stage_id') != stage['stage_id'] or dd.get('scope_hash') != scope
                    or dd.get('manifest_hash') != permit.data['manifest_hash']):
                raise ReducePreflightDenied()
            permit_audits = db.scalars(select(Audit).where(Audit.workspace_id == principal.workspace_id,
                Audit.object_id == permit.id, Audit.action == 'cloud.extra_allowance.issued_v1').limit(2)).all()
            if (len(permit_audits) != 1 or permit_audits[0].id != permit.data.get('audit_id')
                    or permit_audits[0].actor_id != principal.user_id
                    or permit_audits[0].data != {'schema':'cloud-extra-allowance-issue-v1',
                        'permit_id':permit.id,'manifest_hash':permit.data['manifest_hash']}):
                raise ReducePreflightDenied()
            expected_intent = {'schema':'delegated-evidence-transport-intent-v1',
                'workspace_id':principal.workspace_id,'actor_id':principal.user_id,'draft_id':row.id,
                'job_id':original_job,'stage_id':stage['stage_id'],'call_id':call_id,'permit_id':permit.id,
                'scope_hash':scope,'request_hash':stage['request_hash'],'options_hash':stage['options_hash'],
                'token_bound':stage['token_bound']}
            intent_audits = db.scalars(select(Audit).where(Audit.workspace_id == principal.workspace_id,
                Audit.object_id == dispatch.id, Audit.action == 'cloud.delegated_evidence.transport_intent_v1').limit(2)).all()
            if (not _valid_map_intent(dd.get('intent'),expected_intent,manifest,pm) or len(intent_audits) != 1
                    or intent_audits[0].actor_id != principal.user_id
                    or intent_audits[0].data != {'schema':expected_intent['schema'],
                        'intent_hash':dd['intent_hash']}):
                raise ReducePreflightDenied()
            parsed = pipeline._parsed(row, stage['stage_id'])
            wire.verify_saved_response(pipeline, row, stage['stage_id'], parsed,
                                       pipeline._descriptor(row, stage['stage_id']))
        if _rechecking:
            prepared = row.data['reduction']['preview']
            if prepared['body'] != command.model_dump(mode='json'):
                raise ReducePreflightDenied()
            catalog = pipeline._catalog(row.data['preview'])
            maps = pipeline._map_results(row,catalog)
            pipeline._check_reduce_preview(prepared,row,principal,catalog,maps)
        else:
            prepared = pipeline.reduce_preview(row, principal, command)
        reduction = prepared['reduction_preparation']
        summary = reduction['input_summary']
        token_bound = summary['token_bound']
        if type(token_bound) is not int or token_bound < 1:
            raise ReducePreflightDenied()
        value = {'schema':'delegated-reduce-readonly-preflight-v1', 'draft_id':row.id,
            'revision':command.revision, 'workspace_id':principal.workspace_id, 'actor_id':principal.user_id,
            'original_job_id':original_job, 'source_hash':manifest['source_hash'],
            'map_plan_hash':grant.data['manifest_hash'],
            'map_results_hash':digest([(s['chunk_id'],s['result_hash'],s['reserved_call_id']) for s in row.data['maps']]),
            'map_calls':len(stages), 'reduction_preview_hash':prepared['preview_hash'],
            'reduction_preparation_hash':digest(reduction), 'token_bound':token_bound,
            'additional_call_count':1, 'additional_cost':True, 'user_confirmed_business':False,
            'can_start':False, 'can_send':False}
        result = {**value, 'preflight_hash':digest(value)}
        if _with_receipt_binding:
            return result, _receipt_binding(result, prepared, row, principal, command)
        return result
    except ReducePreflightDenied:
        raise
    except Exception:
        raise ReducePreflightDenied('原分段、账本或汇总范围无法完整核对；未签发任何额度') from None
