"""Durable model planning, separate from human approval and action execution."""
import copy
from fastapi import HTTPException
from sqlalchemy import select,func
from .models import Plan,Task,Job,now
from .services import scoped,check_version,published,audit
from .security import require
from .contracts import digest
from .goals import snapshot,query_goal
from .business_tools import evidence_hash,scope_hash
from .leases import checkpoint
from .temporal import instant


class PlanningAbandoned(Exception):
    """A cancelled or superseded request must never activate a late plan."""


def request(db,p,task,expected,reason,model_profile_id,observation_mode=None):
    require(p,'admin','operator');check_version(task,expected)
    if task.status not in {'draft','blocked_model','review','waiting_evidence','waiting_tool','planning_failed'}:
        raise HTTPException(409,'当前任务已有活动规划或执行；请等待、取消或核对未知动作')
    goal=snapshot(task)
    if goal['spec']['deadline'] and now()>=instant(goal['spec']['deadline']):
        raise HTTPException(409,'目标截止时间已到，请先确认新目标或截止时间')
    if any(r.get('result',{}).get('status') in {'unknown','running'} and r.get('step',{}).get('side_effect')
        for r in task.data.get('step_receipts',{}).values()):
        raise HTTPException(409,'存在结果未知的外部动作，核对服务回执前不能重新规划')
    plans=select(Plan.id).where(Plan.workspace_id==p.workspace_id,Plan.task_id==task.id)
    if db.scalar(select(Job.id).where(Job.workspace_id==p.workspace_id,Job.status=='running',
        Job.lease_until>func.clock_timestamp(),Job.data['plan_id'].as_string().in_(plans)).limit(1)):
        raise HTTPException(409,'先前后台任务仍有有效租约，请等待结束后重新规划')
    if model_profile_id:published(db,p,model_profile_id,'foundation_model')
    if observation_mode is not None:
        if goal['spec']['observation_mode']=='existing_only' and observation_mode!='existing_only':
            raise HTTPException(409,'已确认目标只允许既有观测；扩大采集范围需确认新目标版本')
        task.data={**task.data,'observation_mode':observation_mode}
    previous_id=task.data.get('current_plan_id')
    if previous_id:
        previous=scoped(db,Plan,previous_id,p,True)
        if previous.status in {'review','blocked_model','failed','executed','waiting_tool','blocked_evidence'}:
            previous.status='superseded';previous.version+=1
    row=Plan(workspace_id=p.workspace_id,task_id=task.id,status='queued',data={
        'request':{'model_profile_id':model_profile_id,'scope_hash':scope_hash(task),'goal_snapshot':goal,
            'requested_at':now().isoformat(),'actor_id':p.user_id,'previous_plan_id':previous_id},
        'goal_snapshot':goal,'progress':{'stage':'queued','saved_at':now().isoformat()}})
    db.add(row);db.flush()
    from .sensor_agent_context import claim_for_plan
    claim_for_plan(db,p,task,row.id,model_profile_id)
    job=Job(workspace_id=p.workspace_id,kind='generate_plan',idempotency_key='planning:'+row.id,
        data={'plan_id':row.id,'actor_id':p.user_id})
    db.add(job);db.flush();row.data={**row.data,'planning_job_id':job.id}
    history=list(task.data.get('execution_failure_history',[]))
    if task.data.get('execution_error'):
        history.append({'plan_id':previous_id,'reason':task.data['execution_error'],'saved_at':now().isoformat()})
    task.status='planning';task.version+=1
    task.data={**task.data,'current_plan_id':row.id,'execution_error':None,'execution_failure_history':history}
    audit(db,p,'business.planning_requested',row.id,{'reason':reason,'goal_version':goal['version']})
    return row


def current(db,p,plan_id):
    plan=scoped(db,Plan,plan_id,p);task=scoped(db,Task,plan.task_id,p,True)
    plan=scoped(db,Plan,plan_id,p,True)
    if task.status=='cancelled' or plan.status=='cancelled':
        if plan.status!='cancelled':plan.status='cancelled';plan.version+=1
        raise PlanningAbandoned('任务已取消，不激活迟到规划')
    if task.data.get('current_plan_id')!=plan.id:
        if plan.status!='superseded':plan.status='superseded';plan.version+=1
        raise PlanningAbandoned('请求已被替代，不覆盖新任务状态')
    if snapshot(task)!=plan.data['request']['goal_snapshot'] or scope_hash(task)!=plan.data['request']['scope_hash']:
        raise HTTPException(409,'目标或对象范围已变化，请核对后重新规划')
    if task.data['goal_spec']['deadline'] and now()>=instant(task.data['goal_spec']['deadline']):
        raise HTTPException(409,'目标截止时间已到，保留规划请求与模型回执等待核查')
    return plan,task


def validate_inputs(db,p,task,cached):
    if digest({k:v for k,v in cached.items() if k!='content_hash'})!=cached.get('content_hash'):
        raise HTTPException(409,'规划结果检查点完整性校验失败，请重新规划')
    fresh=query_goal(db,p,task,learning=False)
    if evidence_hash(fresh)!=evidence_hash(cached['evidence']) or fresh['status']!=cached['evidence']['status']:
        raise HTTPException(409,'模型规划依据已变化，保留原响应并等待重新核查')
    from .learning import eligible
    eligible_cases={m.id:m for m in eligible(db,p,task.package_id)}
    for case in cached['evidence'].get('trusted_cases',[]):
        memory=eligible_cases.get(case['id'])
        if not memory or memory.text!=case['text'] or digest(memory.data)!=digest(case['provenance']):
            raise HTTPException(409,'规划使用的可信经验已撤回或变化，请重新核查')


def run(db,p,plan_id,*,cached_only=False):
    plan=scoped(db,Plan,plan_id,p)
    if plan.status in {'review','blocked_model','blocked_evidence','cancelled','superseded','approved','executed','waiting_data'}:
        return plan.status
    try:
        plan,task=current(db,p,plan_id)
        cached=plan.data.get('planning_checkpoint')
        resumed=bool(cached)
        if cached:validate_inputs(db,p,task,cached)
        elif plan.data.get('planning_input',{}).get('evidence',{}).get('sensor_agent_context'):
            # The checkpoint is written immediately before dispatch. A crash
            # afterward leaves the send outcome unknown; never spend a second
            # one-use disclosure authorization on an automatic retry.
            raise HTTPException(409,'原次云发送结果未知，请核对后重新授权，不自动重发')
        elif cached_only:raise HTTPException(409,'恢复次数已耗尽且没有已保存模型响应，需人工核查后重新规划')
        from .goal_queries import ensure
        if not ensure(db,p,task,plan):return plan.status
        plan.status='running';plan.version+=1
        plan.data={**plan.data,'progress':{'stage':'validating_saved_result' if cached else 'reading_evidence','saved_at':now().isoformat()}}
        task.status='planning';task.version+=1
        def before_model(signature,evidence):
            row,latest=current(db,p,plan_id)
            row.data={**row.data,'planning_input':{'signature':signature,'evidence':copy.deepcopy(evidence)},
                'progress':{'stage':'waiting_model','saved_at':now().isoformat()}}
            checkpoint(db)  # Release Job/Task locks while the model is on the network.
        def save_result(value,receipt,evidence,signature):
            row=scoped(db,Plan,plan_id,p,True)
            value={'value':copy.deepcopy(value),'receipt':copy.deepcopy(receipt),'evidence':copy.deepcopy(evidence),'signature':signature}
            value['content_hash']=digest(value)
            row.data={**row.data,'planning_checkpoint':value,'progress':{'stage':'model_result_saved','saved_at':now().isoformat()}}
            row.version+=1
            checkpoint(db)  # Paid response survives a crash before normalization/task activation.
            current(db,p,plan_id)
        from .agents import business_agent
        state=business_agent(db,p,task,plan.data['request']['model_profile_id'],
            before_model=before_model,checkpoint_result=save_result,cached=cached,request_key='task:'+task.id+':planning:'+plan_id)
        plan,task=current(db,p,plan_id)
        cached=plan.data.get('planning_checkpoint')
        if cached:validate_inputs(db,p,task,cached)
        plan.status=state['status'];plan.version+=1
        plan.data={**plan.data,**state,'progress':{'stage':'ready_for_review' if state['status']=='review' else 'blocked_model','saved_at':now().isoformat()}}
        task.status=state['status'];task.version+=1
        audit(db,p,'business.plan',plan.id,{'status':plan.status,'resumed_saved_response':resumed})
        return plan.status
    except PlanningAbandoned:
        return scoped(db,Plan,plan_id,p).status
    except HTTPException as failure:
        plan=scoped(db,Plan,plan_id,p);task=scoped(db,Task,plan.task_id,p,True)
        plan=scoped(db,Plan,plan_id,p,True)
        plan.status='blocked_evidence';plan.version+=1
        plan.data={**plan.data,'error':str(failure.detail),'progress':{'stage':'blocked_evidence','saved_at':now().isoformat()}}
        if task.data.get('current_plan_id')==plan.id and task.status not in {'cancelled','completed'}:
            task.status='waiting_evidence';task.version+=1
            task.data={**task.data,'execution_error':str(failure.detail)}
        audit(db,p,'business.planning_blocked',plan.id,{'reason':str(failure.detail)})
        return plan.status
