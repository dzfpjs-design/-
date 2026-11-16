"""Durable human review of a proved paid answer; never action authority."""
import copy

from sqlalchemy import select

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as store
from .contracts import digest
from .models import Audit, Plan, uid
from .relations import lock_current_actor

ACTION='same_goal_advice.human_reviewed_v308'
SCHEMA='same-goal-advice-human-review-v308'
_FIELDS={'schema','workspace_id','task_id','plan_id','actor_id','actor_role','request','body_hash',
    'result_ref','ready_audit_ref','plan_version_before','plan_version_after','record_hash'}


def checked(db,p,task,plan,prep):
    """Cold receipt inventory, without recursion into result/Plan readers."""
    pointers=plan.data['reviews']
    rows=store._rows(db,p,Audit,Audit.action==ACTION,Audit.object_id==plan.id)
    store.need(type(pointers) is list and len(pointers)==len(rows)<=1)
    if not rows:
        store.need(plan.status!='reviewed')
        return []
    marker=plan.data.get(store.RESULT_KEY);store.need(marker is not None and plan.status=='reviewed')
    row=rows[0];value=row.data;pointer=pointers[0]
    store.need(type(value) is dict and set(value)==_FIELDS and value['schema']==SCHEMA
        and row.workspace_id==value['workspace_id']==p.workspace_id and row.object_id==plan.id==value['plan_id']
        and value['task_id']==task.id and value['actor_id']==row.actor_id
        and (value['actor_role']=='admin' or value['actor_role']=='operator' and row.actor_id==prep['business_actor_id'])
        and value['record_hash']==c.record_hash(value)
        and pointer=={'id':row.id,'record_hash':digest(value)})
    body=c.Review.model_validate(value['request']).model_dump(mode='json')
    store.need(value['body_hash']==digest(body) and body['result_hash']==marker['result_ref']['record_hash']
        and value['result_ref']==marker['result_ref'] and value['ready_audit_ref']==marker['ready_audit_ref']
        and type(value['plan_version_before']) is int
        and value['plan_version_before']==body['expected_plan_version']
        and value['plan_version_after']==value['plan_version_before']+1==plan.version)
    return [{'request_id':body['request_id'],'decision':body['decision'],'reason':body['reason'],
        'actor_id':row.actor_id,'result_hash':body['result_hash'],'reviewed_at':row.created_at.isoformat(),
        'review_ref':copy.deepcopy(pointer),'can_dispatch':False,'goal_completion_verified':False}]


@store.checked
def review(db,p,task_id,plan_id,body):
    body=c.Review.model_validate(body).model_dump(mode='json')
    store._reader(db,p,write=True)
    store.command_nonce.acquire(db,p,action=ACTION,task_id=task_id,body=body,subject_id=plan_id)
    task=store.existing._task(db,p,task_id,lock=True)
    plan=db.scalar(select(Plan).where(Plan.workspace_id==p.workspace_id,Plan.task_id==task.id,
        Plan.id==store.existing._id(plan_id)).with_for_update().execution_options(populate_existing=True))
    store.need(plan is not None)
    lock_current_actor(db,p)
    prep,_job=store._saved(db,p,task,plan)
    store.need(p.role=='admin' or p.user_id==prep['business_actor_id'])
    originals=checked(db,p,task,plan,prep)
    if originals:
        row=originals[0]
        event=store._fresh(db,p,Audit,row['review_ref']['id'])
        store.need(event.actor_id==p.user_id and event.data['request']==body,
            '本回答已有审核结论，请保留原记录，未覆盖他人的审核')
        return row
    store.need(plan.status=='ready_for_review' and plan.version==body['expected_plan_version'])
    from .same_goal_advice_runtime_v308 import cold_result
    result=cold_result(db,p,task,plan)
    store.need(digest(result)==body['result_hash'],'回答版本已变化，请重新核对原回答')
    marker=plan.data[store.RESULT_KEY]
    value={'schema':SCHEMA,'workspace_id':p.workspace_id,'task_id':task.id,'plan_id':plan.id,
        'actor_id':p.user_id,'actor_role':p.role,'request':copy.deepcopy(body),'body_hash':digest(body),
        'result_ref':copy.deepcopy(marker['result_ref']),'ready_audit_ref':copy.deepcopy(marker['ready_audit_ref']),
        'plan_version_before':plan.version,'plan_version_after':plan.version+1}
    value['record_hash']=c.record_hash(value)
    event=Audit(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,action=ACTION,object_id=plan.id,data=value)
    db.add(event)
    plan.data={**plan.data,'reviews':[{'id':event.id,'record_hash':digest(value)}]}
    plan.status='reviewed';plan.version+=1
    db.flush()
    return checked(db,p,task,plan,prep)[0]
