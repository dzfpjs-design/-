"""Read-only feasibility snapshot; it never authorizes or dispatches a call."""
from sqlalchemy import or_, select, text
from fastapi import HTTPException

from .cloud_provider_policy import provider_enabled
from .config import settings
from .contracts import digest
from .models import Audit, CloudCall, Job, now
from .report_full_raw_view import raw_view


def _block(blockers, code, message):
    blockers.append({'code':code,'message':message})


def preview(db, p, identity, revision):
    from . import report_full_reading_pipeline as full
    from . import report_reading_drafts as legacy
    from .report_full_provisional import _stored
    from .report_full_provisional_models import FullProvisionalCandidate

    row=full.owned(db,p,identity)
    if row.version != revision:
        full.fail('full_resume_revision_changed','原草案版本已变化，请刷新后核对')
    original=row.data['preview']; stages=row.data['extractions']; batches=original['batches']
    blockers=[]
    if (p.role not in ('admin','operator')):
        _block(blockers,'role_not_authorized','当前角色不能准备云模型调用。')
    if (len(stages)!=2 or len(batches)!=2 or [s.get('batch_id') for s in stages]!=['extract-1','extract-2']):
        full.fail('full_resume_stage_unavailable','此原任务不是可核对的两批抽取',409)
    first,second=stages;batch=batches[1]
    if (row.status!='failed' or row.data['phase']!='failed' or row.data.get('error_code')!='full_candidate_invalid'
        or first.get('status')!='failed' or first.get('error_code')!='full_candidate_invalid'):
        _block(blockers,'first_stage_not_repairable','原任务并非第一批候选校验失败的可核对状态。')
    if second != {'batch_id':'extract-2','status':'pending'}:
        _block(blockers,'second_stage_not_pristine','第二批已有执行或保留记录，不能推断未发送。')
    # Historical full-report receipts contain no independently verifiable
    # second-stage not-sent proof or frozen worker implementation identity.
    # A pending flag and absence of matching ledger rows are not that proof.
    _block(blockers,'second_stage_not_sent_unproven',
        '原任务没有保存第二批未发送的独立证明；仅凭待处理状态或未找到调用，无法安全继续。')
    first_raw=first.get('raw_response')
    try:
        original_view=raw_view(db,p,row.id,'extract-1',revision)
        if (original_view['status']!='available' or original_view['finish_reason']!='stop'
                or not full._complete_response(first_raw)):
            raise ValueError('incomplete')
        first_hash=original_view['scope']['raw_hash']
    except (HTTPException,ValueError,TypeError,KeyError):
        first_hash=None;_block(blockers,'first_raw_unverified','第一批原回答或费用账本无法完整核对。')

    job=db.scalar(select(Job).where(Job.id==row.data['job_id'],Job.workspace_id==p.workspace_id))
    job_ok=(job is not None and job.kind==legacy.KIND and job.status=='failed' and job.lease_until is None
        and job.attempts==1 and isinstance(job.data,dict) and job.data.get('lease_recoveries',0)==0
        and job.idempotency_key=='report-full-reading:'+row.request_id
        and job.data.get('actor_id')==p.user_id and job.data.get('report_draft_id')==row.id
        and isinstance(job.data.get('finished_at'),str))
    if not job_ok:
        _block(blockers,'original_job_unverified','原后台任务尚未以可核对的单次失败状态结束。')
    job_scope=({'id':job.id,'status':job.status,'attempts':job.attempts,
        'lease_recoveries':job.data.get('lease_recoveries',0),'data_hash':digest(job.data)} if job_ok else None)

    auth_audits=db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.actor_id==p.user_id,
        Audit.object_id==row.id,Audit.action=='setup.report_full_reading.authorize').limit(2)).all()
    failed_audits=db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.actor_id==p.user_id,
        Audit.object_id==row.id,Audit.action=='setup.report_full_reading.failed').limit(2)).all()
    audit_ok=(len(auth_audits)==1 and auth_audits[0].data=={'request_id':row.request_id,'preview_hash':original['preview_hash']}
        and len(failed_audits)==1 and failed_audits[0].data=={'stage':'extract-1','response_unknown':False,'raw_response_saved':True})
    if not audit_ok:
        _block(blockers,'original_history_unverified','原授权或第一批失败审计无法完整核对。')

    candidate=None
    if not db.scalar(text("SELECT to_regclass('public.report_full_provisional_candidates') IS NOT NULL")):
        _block(blockers,'candidate_store_unavailable','机器待核稿的保存功能尚不可用。')
    else:
        records=db.scalars(select(FullProvisionalCandidate).where(
            FullProvisionalCandidate.workspace_id==p.workspace_id,FullProvisionalCandidate.actor_id==p.user_id,
            FullProvisionalCandidate.draft_id==row.id,FullProvisionalCandidate.stage=='extract-1').limit(2)).all()
        if len(records)!=1:
            _block(blockers,'first_candidate_required','请先保存并核对第一批机器待核稿。')
        else:
            try:
                saved=_stored(db,p,records[0],row);receipt=saved['receipt']
                if receipt['raw_hash']!=first_hash or receipt['source_hash']!=original['source_hash']:
                    raise ValueError('provenance')
                candidate={'receipt_hash':receipt['receipt_hash'],'algorithm_version':receipt['algorithm_version'],
                    'quarantine_count':len(saved['quarantine']),'confirmed':False,'status':'needs_review'}
            except (HTTPException,ValueError,TypeError,KeyError):
                _block(blockers,'first_candidate_unverified','第一批保存稿与原回答或审计无法核对。')

    related=db.scalars(select(CloudCall).where(CloudCall.workspace_id==p.workspace_id,
        CloudCall.id != (first.get('reserved_call_id') or ''),or_(
            CloudCall.data['request_hash'].as_string()==batch['messages_hash'],
            CloudCall.status.in_(['reserved','unknown']))).order_by(CloudCall.id).limit(101)).all()
    if related:
        _block(blockers,'ledger_ambiguous','存在相同消息或未结清云调用，无法证明第二批从未发送。')
    if len(related)>100:
        _block(blockers,'ledger_capacity','云调用待核记录过多，无法安全判定。')
    resume_key='report-full-resume:'+row.id+':extract-2'
    existing=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==resume_key))
    if existing:
        _block(blockers,'resume_job_exists','此批次已有恢复任务或记录，请先核对原记录。')

    try:
        config=legacy.workspace_draft(db,p,lock=False)
        body=full.PreviewCommand.model_validate(original['body'])
        if config.version!=body.revision:raise ValueError('configuration')
        cap=legacy.old().model(db,p,str(body.model_id))
        if not provider_enabled(original['provider']):raise ValueError('provider')
        fresh=full.freeze_full_source(db,p,body.selection,lock=False)
        rebuilt=full.build_preview(p,body,config.data,cap,fresh,can_start=original['can_start'],
            messages_version=original['generation_contract']['messages_version'])
        if rebuilt!=original:raise ValueError('source')
        source_current=True
    except (HTTPException,ValueError,TypeError,KeyError,OSError):
        source_current=False;_block(blockers,'frozen_scope_changed','原资料、识别版本、配置或模型无法按原授权重核。')

    observed_at=now();day=str(observed_at.date());bound=batch['input_summary']['token_bound']
    budget_row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.idempotency_key=='cloud-budget:'+day))
    used=budget_row.data if budget_row else {'tokens':0,'calls':0}
    token_limit=settings().cloud_daily_token_limit;call_limit=settings().cloud_daily_call_limit
    budget_valid=(isinstance(used,dict) and (not budget_row or (budget_row.kind=='cloud_budget' and budget_row.status=='internal'
        and used.get('day')==day)) and type(used.get('tokens')) is int and used['tokens']>=0
        and type(used.get('calls')) is int and used['calls']>=0 and type(token_limit) is int and token_limit>=0
        and type(call_limit) is int and call_limit>=0 and type(bound) is int and bound>=0)
    if budget_valid:
        available=used['tokens']+bound<=token_limit and used['calls']+1<=call_limit
        budget={'day_utc':day,'observed_at_utc':observed_at.isoformat(),'used_tokens':used['tokens'],'token_limit':token_limit,
            'used_calls':used['calls'],'call_limit':call_limit,'required_tokens':bound,'available':available}
        if not available:_block(blockers,'budget_insufficient','当前工作区当日模型预算不足；没有预留费用。')
    else:
        budget=None;_block(blockers,'budget_unverified','当前预算账本无法核对，未预留费用。')

    result={'schema':'report-full-second-batch-resume-preview-v1',
        'scope':{'workspace_id':p.workspace_id,'actor_id':p.user_id,'draft_id':row.id,'revision':row.version,
            'stage':'extract-2','source_hash':original['source_hash'],'original_preview_hash':original['preview_hash'],
            'original_start_receipt_hash':row.data['start_receipt']['receipt_hash'],'batch_hash':batch['batch_hash'],
            'messages_hash':batch['messages_hash'],'generation_contract_hash':original['generation_contract_hash']},
        'original_job':job_scope,'original_history_verified':audit_ok,'first_candidate':candidate,
        'original_authorization':{'request_id':row.request_id,'frozen_batch_count':2,
            'original_consent_verified':True,'new_send_authorized':False},
        'ledger_clear':not bool(related),'frozen_scope_current':source_current,'budget':budget,
        'batch':{'source_count':len(batch['source_refs']),'input_summary':batch['input_summary'],
            'provider':original['provider'],'model':original['model']},
        'blockers':blockers,'eligible_for_future_resume':not blockers,
        'can_start':False,'can_send':False,'can_apply':False,'can_publish':False,
        'requires_separate_authorization':True,'first_batch_confirmed':False,
        'unverified':['resume_worker_not_implemented','credentials_and_network_not_checked',
            'budget_and_authority_must_be_rechecked_before_any_send']}
    result['preview_hash']=digest(result)
    return result
