"""Read-only, task-scoped inspection of an original automatic evaluation."""
from uuid import UUID
from fastapi import APIRouter,HTTPException
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from .setup_api import P
from .db import engine
from .models import Task
from .goal_evaluation_append_store_v297 import _reader,_task
from .native_goals import manage
from .auto_evaluation_ack_witness_v356 import AutoEvaluationAckWitnessV356 as Witness
from .auto_evaluation_trigger_v356 import AutoEvaluationTriggerV356 as Trigger
from .auto_evaluation_investigation_v356 import investigate

router=APIRouter()

@router.get('/tasks/{identity}/auto-evaluation-policy/investigations/{reference}')
def inspect_original(identity:str,reference:str,p:P):
    try:
        if str(UUID(reference))!=reference:raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise HTTPException(422,'原接收记录编号无效') from None
    # Own session: caller/session dependencies must never set transaction mode
    # after they have read any rows. No writes, background work or retries.
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        _reader(db,p,write=True);task=_task(db,p,identity);manage(p,task)
        witness=db.scalar(select(Witness).where(Witness.id==reference,Witness.workspace_id==p.workspace_id,Witness.task_id==task.id))
        if witness is None:raise HTTPException(404,'原接收记录不存在或不属于当前任务')
        origins=list(db.scalars(select(Trigger).where(Trigger.workspace_id==p.workspace_id,Trigger.task_id==task.id,
            Trigger.event_id==witness.event_id,Trigger.policy_id==witness.policy_id,
            Trigger.policy_version==witness.policy_version).limit(2)))
        if len(origins)!=1:
            return {'status':'unverifiable','proof_level':'unverifiable','message':'原请求无法核对，原记录保留；未执行恢复或新调用。'}
        result=investigate(db,p,task.id,origins[0].job_id)
        result['proof_level']='record_only' if result['status']=='requires_review' else 'unverifiable'
        if result['status']=='requires_review':
            result['message']='仅显示记录所报状态，调用及评价效果尚未独立核验，不能据此认定完成或允许重试；未执行恢复或新调用。'
        return result
