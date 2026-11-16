"""Read the original protected command independently of result availability.

A lost response never becomes permission to create a replacement request.
The receipt proves this exact body was saved. It neither repairs a result nor
turns an unavailable source, price, role or provider into sending authority.
"""
import copy

from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy import or_

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_preview_v307 as previews
from . import same_goal_advice_execution_v308 as execution
from . import same_goal_advice_review_v308 as reviews
from . import same_goal_advice_options_v310 as options
from .contracts import digest
from .authored_goal_binding_parse_scope_v300 import authored_goal_binding_parse_scope as binding_structure_scope
from .models import Audit,Plan
from .gateway import ModelBlocked

SCHEMA='same-goal-advice-command-receipt-v310'
_ACTIONS={store.ACCEPTED,store.CANCELLED,previews.ACTION,execution.STARTED,reviews.ACTION}|options.ACTIONS


def _candidate(plan,operation,body,actor,audit_id,read_result):
    return {'plan':plan,'operation':operation,'body':copy.deepcopy(body),'actor':actor,
        'audit_id':audit_id,'read_result':read_result}


def _originals(db,p,task,plan,prep,job,request_id):
    found=[]
    for operation in plan.data['operations']:
        if operation['request_id']==request_id:
            found.append(_candidate(plan,operation['operation'],operation['body'],operation['actor_id'],
                operation['audit_id'],lambda:store._view(db,p,task,plan,prep,job)))
    chain=previews._chain(db,p,task,plan,prep)
    for event,value in chain:
        if value['request']['request_id']==request_id:
            def preview_result(event=event):
                return previews._history(db,p,task,plan,prep,event,latest=event.id==chain[-1][0].id)
            found.append(_candidate(plan,'preview',value['request'],event.actor_id,event.id,preview_result))
    started=execution.custody(db,p,task,plan,prep,job)
    if started is not None and started['start'].data['request']['request_id']==request_id:
        event=started['start']
        found.append(_candidate(plan,'start',event.data['request'],event.actor_id,event.id,
            lambda:execution._view(plan,execution.checked(db,p,task,plan,prep,job))))
    saved_reviews=reviews.checked(db,p,task,plan,prep)
    for value in saved_reviews:
        if value['request_id']==request_id:
            event=store._fresh(db,p,Audit,value['review_ref']['id'])
            def review_result(value=value):
                from .same_goal_advice_runtime_v308 import cold_result
                cold_result(db,p,task,plan)
                return copy.deepcopy(value)
            found.append(_candidate(plan,'review',event.data['request'],event.actor_id,event.id,review_result))
    return found


@binding_structure_scope()
@store.checked
def read(db,p,task_id,request_id,*,plan_id=None,operation=None):
    store._reader(db,p)
    request_id=TypeAdapter(c.ID).validate_python(request_id)
    task=store.existing._task(db,p,task_id)
    with db.no_autoflush:
        plans=store._rows(db,p,Plan,or_(
            (Plan.task_id==task.id)&(Plan.data['schema'].as_string()==c.PLAN),
            Plan.data['preparation_snapshot']['scope']['task_id'].as_string()==task.id))
        identities=[plan.id for plan in plans]
        events=store._rows(db,p,Audit,Audit.action.in_(_ACTIONS),or_(Audit.object_id.in_(identities),
            Audit.data['task_id'].as_string()==task.id),or_(
            Audit.data['request_id'].as_string()==request_id,
            Audit.data['request']['request_id'].as_string()==request_id,
            Audit.data['body']['request_id'].as_string()==request_id))
        matches=options.read_receipts(db,p,task,request_id)
        for plan in plans:
            prep,job=store._saved_base(db,p,task,plan)
            matches.extend(_originals(db,p,task,plan,prep,job,request_id))
        store.need(len(matches)<=1 and {event.id for event in events}=={value['audit_id'] for value in matches},
            '原请求编号存在矛盾或缺失记录，请保留原申请核查')
        if not matches:raise HTTPException(404,'尚未查到此原请求；请保留原编号和原内容核对')
        found=matches[0]
        if plan_id is not None:
            store.need(found['plan'] is not None and found['plan'].id==store.existing._id(plan_id),
                '原请求不属于当前分析申请')
        if operation is not None:
            store.need(found['operation']==operation,'原请求属于另一类操作，请保留原内容核查')
        result_status='available'
        try:result=found['read_result']()
        except ModelBlocked:
            # Command custody has already proved the saved body/actor. A
            # damaged financial result is not permission to submit it again.
            result=None;result_status='needs_check'
        except HTTPException as error:
            if error.status_code not in {404,409,410}:raise
            result=None;result_status='needs_check'
        return {'schema':SCHEMA,'found':True,'operation':found['operation'],'task_id':task.id,
            'plan_id':found['plan'].id if found['plan'] is not None else None,
            'request_id':request_id,'actor_id':found['actor'],
            'body_hash':digest(found['body']),'result':result,'result_status':result_status}
