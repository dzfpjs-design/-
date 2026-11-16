"""Read-only investigation for an interrupted automatic append.

No scheduler/claim/recovery or capability call is performed. A caller must start
an independent REPEATABLE READ, READ ONLY transaction. The output cannot close
an operation or authorize a new one.
"""
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import func,or_,select,text
from sqlalchemy.exc import SQLAlchemyError
from .models import Job,Invocation
from .security import Principal
from .contracts import digest
from .goal_evaluation_append_store_v297 import _reader,_task,_saved,approval_for
from .auto_evaluation_trigger_v356 import AutoEvaluationTriggerV356,historical_origin
from .auto_evaluation_ack_witness_v356 import AutoEvaluationAckWitnessV356
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288
from .goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297
from .native_goals import manage

_UNVERIFIED={'status':'unverifiable','message':'原记录无法核对，原请求与可能的调用结果保留；未执行恢复或新调用。'}

def investigate(db,p,task_id,job_id):
    """Inspect only one original operation, never confer execution authority."""
    _reader(db,p,write=True)
    task=_task(db,p,task_id)
    manage(p,task)
    if db.new or db.dirty or db.deleted:raise HTTPException(409,'请先保存当前操作，再核对原请求')
    if db.scalar(text('SHOW transaction_read_only'))!='on' or db.scalar(text('SHOW transaction_isolation'))!='repeatable read':
        raise HTTPException(409,'原请求核对需要独立的只读一致性事务')
    try:
        if type(job_id) is not str or str(UUID(job_id))!=job_id:raise ValueError()
        origins=list(db.scalars(select(AutoEvaluationTriggerV356).where(
            AutoEvaluationTriggerV356.workspace_id==p.workspace_id,
            AutoEvaluationTriggerV356.task_id==task.id,AutoEvaluationTriggerV356.job_id==job_id).limit(2)))
        if len(origins)!=1:raise ValueError()
        origin=origins[0];actor=origin.data.get('actor_id')
        if type(actor) is not str or str(UUID(actor))!=actor:raise ValueError()
        witness=db.get(AutoEvaluationAckWitnessV356,origin.data.get('witness_id'))
        if witness is None:raise ValueError()
        # The synthetic principal is used solely inside the saved-proof reader;
        # current caller permission has already been checked, and no grant is made.
        historical=Principal(actor,p.workspace_id,'viewer','auto-evaluation-investigation')
        historical_origin(db,historical,task,origin,witness)
        command=db.get(GoalEvaluationAppendCommandV297,origin.command_id)
        if command is None:raise ValueError()
        record,job=_saved(db,historical,task,command)
        if job.id!=job_id or job.kind!='append_goal_evaluation_v297':raise ValueError()
        calls=list(db.scalars(select(Invocation).where(Invocation.workspace_id==p.workspace_id,
            or_(Invocation.idempotency_key==record['invocation_key'],Invocation.data['job_id'].as_string()==job.id)).limit(2)))
        if len(calls)>1:raise ValueError()
        call=calls[0] if calls else None
        if call is not None:
            value=call.data
            if not (call.capability_id==record['capability_id'] and call.idempotency_key==record['invocation_key']
                and value.get('job_id')==job.id and value.get('role')=='data' and value.get('side_effect') is True
                and value.get('capability_hash')==record['capability_hash']
                and digest(value.get('capability_snapshot'))==record['capability_hash']
                and digest(value.get('inputs'))==record['input_hash']==value.get('input_hash')
                and digest(value.get('approval'))==digest(approval_for(record))):raise ValueError()
        rows=list(db.scalars(select(GoalEvaluationV288).where(GoalEvaluationV288.workspace_id==p.workspace_id,
            or_(GoalEvaluationV288.job_id==job.id,GoalEvaluationV288.invocation_id==call.id if call else False)).limit(2)))
        if len(rows)>1:raise ValueError()
        now=db.scalar(select(func.clock_timestamp()))
        lease='not_running' if job.status!='running' else 'expired' if job.lease_until is None or job.lease_until<=now else 'active'
        state='no_record_found' if call is None else 'record_present'
        return {'status':'requires_review','job_state':job.status,'lease_state':lease,
            'invocation_record':state,'invocation_state':call.status if call else None,
            'evaluation_record_present':bool(rows),
            'message':'仅核对保存的记录；未运行恢复或新调用。无调用记录不能证明没有发生效果；评价记录存在也不代表已完成。'}
    except (HTTPException,SQLAlchemyError,ValueError,KeyError,TypeError,AttributeError):
        return dict(_UNVERIFIED)
