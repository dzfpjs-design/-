"""Read-only external receipt recovery. Never resends an unknown business action."""
import copy
from fastapi import HTTPException
from .models import Invocation,Task,Plan,Job,now
from .services import scoped,check_version,validate,audit
from .contracts import digest
from .security import require
from sqlalchemy import select, func

RECEIPT_SCHEMA={'type':'object','additionalProperties':False,
    'required':['idempotency_key','input_hash','status'],
    'properties':{'idempotency_key':{'type':'string'},'input_hash':{'type':'string'},
        'status':{'enum':['succeeded','pending','not_executed']},'output':{'type':'object'},'terminal':{'type':'boolean'}},
    'allOf':[{'if':{'properties':{'status':{'const':'succeeded'}}},'then':{'required':['output']}},
        {'if':{'properties':{'status':{'const':'not_executed'}}},
         'then':{'required':['terminal'],'properties':{'terminal':{'const':True}}}}]}
# terminal=True means the provider permanently closes this request key and rejects late submissions.
# A missing receipt, temporary not-found or cancellable pending job never proves non-execution.


def reconcile(db,p,identifier,expected,reason):
    require(p,'admin')
    invocation=scoped(db,Invocation,identifier,p,True);check_version(invocation,expected)
    if invocation.status not in {'running','unknown'} or not invocation.data.get('approval'):
        raise HTTPException(409,'此调用没有待核对的批准外部动作')
    owner_id = invocation.data.get('job_id')
    owner_query = select(Job.id).where(Job.workspace_id == p.workspace_id, Job.status == 'running',
        Job.lease_until > func.clock_timestamp())
    owner_query = owner_query.where(Job.id == owner_id) if owner_id else owner_query.where(
        Job.kind == 'execute_plan', Job.data['plan_id'].as_string() == invocation.data['approval']['plan_id'])
    if db.scalar(owner_query):
        raise HTTPException(409,'原执行进程仍持有有效租约，请等待执行完成或中断恢复后再核对回执')
    snapshot=invocation.data.get('capability_snapshot')
    if not snapshot or digest(snapshot)!=invocation.data['capability_hash'] or not snapshot['execution'].get('side_effect'):
        raise HTTPException(409,'缺少可信的外部动作契约快照')
    from .connectors import query_receipt
    # Release row locks during network reads; the action is never re-posted.
    call_version=invocation.version;db.commit()
    try:
        response=query_receipt(snapshot['execution'],invocation.idempotency_key)
        validate(RECEIPT_SCHEMA,response)
        if response['idempotency_key']!=invocation.idempotency_key or response['input_hash']!=invocation.data['input_hash']:
            raise HTTPException(409,'服务回执与已发送动作编号或输入哈希不一致')
        if response['status']=='succeeded':validate(snapshot['output_schema'],response['output'])
        error=None
    except Exception:
        response=None;error='服务回执查询失败或不匹配，动作仍未知，禁止重放'
    invocation=scoped(db,Invocation,identifier,p,True)
    if invocation.version!=call_version or invocation.status not in {'running','unknown'}:
        raise HTTPException(409,'调用已由另一核查处理，请刷新')
    check={'checked_at':now().isoformat(),'checked_by':p.user_id,'reason':reason,
        'status':response['status'] if response else 'unverified','error':error}
    invocation.data={**invocation.data,'receipt_checks':[*invocation.data.get('receipt_checks',[]),check]}
    invocation.version+=1
    if response:
        invocation.data={**invocation.data,'external_receipt':response}
        if response['status'] in {'succeeded','not_executed'}:
            invocation.status=response['status']
            if response['status']=='succeeded':invocation.data={**invocation.data,'output':response['output']}
    approval=invocation.data['approval'];plan=scoped(db,Plan,approval['plan_id'],p,True)
    task=scoped(db,Task,plan.task_id,p,True);key=plan.id+':'+str(approval['step_index'])
    receipts=copy.deepcopy(task.data.get('step_receipts',{}));record=receipts.get(key)
    if record and record['result'].get('invocation_id')==invocation.id:
        record['result']={**record['result'],'receipt_check':check}
        if response and response['status']=='succeeded':
            record['result']={**record['result'],'status':'succeeded','output':response['output'],
                'reason':'只读查询核对服务回执成功；原动作未重放'}
        elif response and response['status']=='not_executed':
            record['result']={**record['result'],'status':'not_executed','reason':'服务确认原请求已终止且未执行；重新规划与批准后才能再次执行'}
        task.data={**task.data,'step_receipts':receipts};task.version+=1
        if response and response['status']=='succeeded' and task.status=='waiting_tool' and task.data.get('current_plan_id')==plan.id:
            if plan.approved_hash!=digest(plan.data):raise HTTPException(409,'原计划批准内容已变化，不能恢复')
            from .business_tools import validate_step
            try:validate_step(db,p,task,record['step'])
            except HTTPException:
                record['result']['reason']='服务已确认动作完成，但证据/能力变化，不能继续旧计划，请重新核查目标'
                task.data={**task.data,'step_receipts':receipts}
            else:
                task.status='queued';plan.status='approved';plan.version+=1
                db.add(Job(workspace_id=p.workspace_id,kind='execute_plan',idempotency_key='plan:'+plan.id+':receipt:'+invocation.id,
                    data={'plan_id':plan.id,'actor_id':p.user_id}))
    audit(db,p,'business.tool_receipt',invocation.id,check);return invocation
