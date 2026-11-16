"""Bind an independently prepared analysis to a stopped original node.

The original graph stays untouched.  The link, plan, job and their audits are
committed by the caller in one transaction.  It does not grant a model send.
"""
import copy

from sqlalchemy import select

from .contracts import digest
from .models import Audit, Plan, uid
from . import graph_reasoning_retirement_v337 as retirement

KEY = 'graph_reasoning_advice_links_v339'
SCHEMA = 'graph-reasoning-advice-link-v339'
ACTION = 'workflow.graph.reasoning_advice_linked_v339'
FIELDS = {'schema','task_id','plan_id','prepare_job_id','actor_id','request_id','request_hash',
          'preparation_hash','retirement_request_id','retirement_hash','graph_hash','input_hash',
          'goal_hash','head_hash','audit_id','record_hash'}
need = retirement.need


def _rows(db, p, task):
    """Positive inventories; a missing pointer is never proof of no event."""
    raw = task.data.get(retirement.KEY, {})
    need(type(raw) is dict and len(raw) <= 64)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == retirement.ACTION, Audit.object_id == task.id).limit(1001)))
    need(len(events) <= 64 and {a.id for a in events} == {r.get('audit_id') for r in raw.values() if type(r) is dict}
         and len(events) == len(raw), '原步骤停用记录不完整，请核对原记录')
    retired = [retirement.historical_record(db,p,task,key,original_actor_only=False) for key in raw]
    links = task.data.get(KEY, {})
    need(type(links) is dict and len(links) <= 100)
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION, Audit.data['task_id'].as_string() == task.id).limit(1001)))
    need(len(proofs) <= 100 and {a.id for a in proofs} == {r.get('audit_id') for r in links.values() if type(r) is dict}
         and len(proofs) == len(links), '续接关联记录不完整，请核对原记录')
    return retired,links,{event.id:event for event in proofs},{event.id:event for event in events}


def _proof(db,p,task,plan,prep,job,record,retired,proofs):
    need(type(record) is dict and set(record) == FIELDS and record['schema'] == SCHEMA
         and record['record_hash'] == digest({k:v for k,v in record.items() if k != 'record_hash'}))
    old = next((r for r in retired if r['request_id'] == record['retirement_request_id']),None)
    need(old is not None and record['retirement_hash'] == digest(old)
         and record['task_id'] == task.id == plan.task_id and record['plan_id'] == plan.id
         and record['prepare_job_id'] == job.id and record['actor_id'] == prep['requested_by']
         and record['request_id'] == prep['request']['request_id']
         and record['request_hash'] == prep['request_hash'] == digest(prep['request'])
         and record['preparation_hash'] == prep['record_hash']
         and record['graph_hash'] == old['graph_hash'] and record['input_hash'] == old['input_hash']
         and record['goal_hash'] == prep['scope']['goal_hash']
         and record['head_hash'] == prep['mature_head']['head_hash'])
    event = proofs.get(record['audit_id'])
    need(event is not None and event.object_id == plan.id and event.actor_id == record['actor_id']
         and event.data == record, '续接审计与原准备不一致，请核对原记录')
    return old


def inspect(db,p,task,plan,prep,job,*,for_send=False):
    retired,links,proofs,events = _rows(db,p,task)
    record = links.get(plan.id)
    if record is None:
        # Plans created before a retirement remain readable, but cannot be
        # started as a new replacement after the original node was stopped.
        need(not retired or all(plan.created_at < events[r['audit_id']].created_at for r in retired),
             '原分析与停用记录缺少正式关联；请保留原记录核对')
        need(not (for_send and retired), '原分析未关联已停用的步骤，不能作为新续接发送')
        return None
    old = _proof(db,p,task,plan,prep,job,record,retired,proofs)
    if for_send:
        original = retirement._locked_job(db,p,old['job_id'])
        state = task.data['graph_runtime'];activation = state['activations'][old['activation_id']]
        transport = task.data['graph_jobs'][old['activation_id']]
        retirement._no_prior_dispatch(db,p,task,original,activation,transport,prep['model_pin']['id'],retired=True)
    return copy.deepcopy(record)


def create(db,p,task,plan,prep,job):
    retired,links,proofs,_ = _rows(db,p,task)
    if not retired:
        need(not links and not proofs)
        return None
    need(len(retired)==1 and plan.id not in links and len(links)<100,
         '原步骤停用记录无法唯一关联；未创建新分析')
    # The Task lock is held by the ordinary preparation transaction. Protect
    # against another active linked preparation even if a caller bypassed its
    # ordinary inventory guard; terminal originals remain historical records.
    for plan_id,prior in links.items():
        other=db.scalar(select(Plan).where(Plan.workspace_id==p.workspace_id,Plan.task_id==task.id,Plan.id==plan_id))
        need(other is not None, '原续接申请缺失，请核对原记录')
        from .same_goal_advice_store_v302 import _saved
        old_prep,old_job=_saved(db,p,task,other)
        _proof(db,p,task,other,old_prep,old_job,prior,retired,proofs)
        need(other.status in {'cancelled','reviewed'}, '已有未结清的后续分析；请先核对原申请')
    old=retired[0]
    original=retirement._locked_job(db,p,old['job_id'])
    state=task.data['graph_runtime'];activation=state['activations'][old['activation_id']]
    transport=task.data['graph_jobs'][old['activation_id']]
    retirement._no_prior_dispatch(db,p,task,original,activation,transport,prep['model_pin']['id'],retired=True)
    need(prep['scope']['task_id']==task.id and prep['scope']['goal_hash']==task.data['goal_hash']
         and prep['mature_head']['head_hash']==task.data['current_goal_evaluation_v288']['head_hash']
         and prep['requested_by']==p.user_id)
    record={'schema':SCHEMA,'task_id':task.id,'plan_id':plan.id,'prepare_job_id':job.id,
            'actor_id':p.user_id,'request_id':prep['request']['request_id'],'request_hash':prep['request_hash'],
            'preparation_hash':prep['record_hash'],'retirement_request_id':old['request_id'],
            'retirement_hash':digest(old),'graph_hash':old['graph_hash'],'input_hash':old['input_hash'],
            'goal_hash':prep['scope']['goal_hash'],'head_hash':prep['mature_head']['head_hash'],'audit_id':uid()}
    record['record_hash']=digest(record)
    links=copy.deepcopy(links);links[plan.id]=record
    task.data={**task.data,KEY:links};task.version+=1
    db.add(Audit(id=record['audit_id'],workspace_id=p.workspace_id,actor_id=p.user_id,
                 action=ACTION,object_id=plan.id,data=record))
    db.flush()
    return record
