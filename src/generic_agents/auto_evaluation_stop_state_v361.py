"""Cold completion classification while Task -> producer locks remain held.

Conservative: incomplete, conflicting, stale-identity or unverifiable records
reject. No writes and no external dispatch. Staging only; not deployed.
"""
from sqlalchemy import select, or_
from fastapi import HTTPException
from .models import Job, Audit
from .contracts import digest
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation
from . import goal_evaluation_maturity_v299 as maturity
from . import goal_evaluation_provider_v288 as provider
from .workflow_goal_evaluation_append_v299 import checked_record

def _need(value):
    if not value: raise HTTPException(409,'评价进度无法完整核对，未保存停止请求')

def classify(db,p,task,producer):
    try:
        return _classify(db,p,task,producer)
    except HTTPException:
        raise
    except (KeyError,TypeError,ValueError,AttributeError,IndexError):
        raise HTTPException(409,'评价进度无法完整核对，未保存停止请求') from None

def _classify(db,p,task,producer):
    """Return fence or terminal only after validating the independent chain.

    Caller holds Task -> producer FOR UPDATE through commit. At most one
    append Evaluation/candidate is accepted; ambiguous history is refused.
    """
    _need(producer.workspace_id==p.workspace_id==task.workspace_id
          and producer.kind=='append_goal_evaluation_v297' and producer.data.get('task_id')==task.id)
    # This verifies the selected head and all selected-head chain links, even
    # when the producer's result is pending or an older historical result.
    current=provider._head(db,p,task)
    rows=list(db.scalars(select(Evaluation).where(Evaluation.workspace_id==p.workspace_id,
        Evaluation.task_id==task.id,Evaluation.job_id==producer.id).limit(2)))
    queue=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==maturity.QUEUED,Audit.data['task_id'].as_string()==task.id,
        Audit.data['producer_job_id'].as_string()==producer.id).limit(2)))
    candidates=list(db.scalars(select(Job).where(Job.workspace_id==p.workspace_id,
        Job.data[provider.JOB_PRODUCER].as_string()==producer.id).limit(2)))
    finals=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.object_id==task.id,Audit.action.in_([maturity.MATURED,maturity.SUPERSEDED]),
        Audit.data['producer_job_id'].as_string()==producer.id).limit(2)))
    _need(len(rows)<=1 and len(queue)<=1 and len(candidates)<=1 and len(finals)<=1)
    if not rows:
        _need(not queue and not candidates and not finals and producer.status in ('queued','running'))
        return 'fenced_effect_unknown'
    # A materialized evaluation while original Job is still running cannot be
    # authenticated as a completed producer here; do not guess its fate.
    if producer.status=='running':
        raise HTTPException(409,'评价结果已保存，后台仍在完成原任务；暂不能停止，请稍后重新核对')
    _need(producer.status=='succeeded' and len(queue)==len(candidates)==1)
    row=rows[0];candidate_job=candidates[0]
    checked=checked_record(db,p,task,row,producer_complete=True)
    _need(checked['row'].id==row.id)
    candidate,actual=maturity._candidate(db,p,task,candidate_job)
    _need(actual.id==row.id and queue[0].object_id==candidate_job.id
          and digest(queue[0].data)==digest(candidate))
    if finals:
        final=finals[0];data=final.data
        _need(type(data) is dict and data.get('producer_job_id')==producer.id
              and data.get('goal_check_job_id')==candidate_job.id and type(data.get('after_head')) is dict)
        if final.action==maturity.MATURED:
            proof=maturity.read_mature_history(db,p,task,head_hash=data['after_head']['head_hash'],
                                                evaluation_ref=candidate['evaluation_ref'])
            _need(proof['row'].job_id==producer.id and proof['head']==data['after_head'])
        else:
            _need(candidate_job.status=='succeeded' and candidate_job.lease_until is None)
            c,r,prepared,prepared_event,_memory=maturity._prepared(db,p,task,candidate_job)
            outboxes=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
                Audit.action==maturity.OUTBOX,Audit.object_id==candidate_job.id).limit(2)))
            _need(len(outboxes)==1 and type(outboxes[0].data) is dict)
            promoter=provider._fresh(db,p,Job,outboxes[0].data['promoter_job_id'])
            outbox,_=maturity._outbox(db,p,task,candidate_job,promoter)
            _need(promoter.status=='succeeded' and promoter.lease_until is None and current is not None)
            prior=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
                Audit.object_id==task.id,Audit.action==provider.SELECTED,
                Audit.data['head_hash'].as_string()==data.get('before_head_hash')).limit(2)))
            base_hash=c['base_head']['head_hash'] if c['base_head'] else None
            _need(len(prior)==1 and prior[0].data.get('head_hash')!=base_hash)
            expected=maturity._mature_value(task,candidate_job,promoter,c,prepared,prepared_event,
                                             outbox,data['before_head_hash'])
            _need(final.actor_id==c['actor_id'] and digest(data)==digest(expected))
        return 'terminal_record_only'
    _need(candidate_job.status in ('queued','running','succeeded'))
    prepared_rows=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==maturity.PREPARED,Audit.object_id==candidate_job.id).limit(2)))
    outboxes=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==maturity.OUTBOX,Audit.object_id==candidate_job.id).limit(2)))
    _need(len(prepared_rows)<=1 and len(outboxes)<=1)
    # A succeeded candidate must have a complete prepared/outbox chain; a
    # successful promoter without its terminal audit is corrupted/incomplete.
    if candidate_job.status=='succeeded':
        _c,_r,_prepared_value,_event,_memory=maturity._prepared(db,p,task,candidate_job)
        _need(len(prepared_rows)==len(outboxes)==1 and type(outboxes[0].data) is dict)
        promoter=provider._fresh(db,p,Job,outboxes[0].data['promoter_job_id'])
        maturity._outbox(db,p,task,candidate_job,promoter)
        _need(promoter.status in ('queued','running'))
    else:
        _need(not outboxes)
        if prepared_rows or maturity.PREPARED_KEY in candidate_job.data:
            _need(candidate_job.status=='running' and len(prepared_rows)==1
                  and maturity.PREPARED_KEY in candidate_job.data)
            maturity._prepared(db,p,task,candidate_job)
    return 'fenced_effect_unknown'
