"""Durable, separate manual continuation of a retired graph reasoning step.

Original graph activation and Job are never completed, and this module cannot
accept a Goal. Each change is serialized and anchored by an independent Audit.
"""
import copy
from sqlalchemy import or_, select
from fastapi import HTTPException
from . import formal_continuation_contracts_v345 as c
from . import formal_continuation_context_v344 as previous
from . import goal_evaluation_append_store_v297 as authority
from . import goal_evaluation_provider_v288 as provider
from . import same_goal_continuation_execution_goal_v314 as execution_gate
from . import same_goal_continuation_execution_store_v314 as executions
from . import same_goal_reasoning_bridge_v339 as bridge
from . import same_goal_advice_store_v302 as advice
from . import workspace_command_nonce_lock_v312 as nonce
from .contracts import digest
from .models import Audit,Plan,Task,now,uid
from .native_goals import manage
from .relations import lock_current_actor
from .services import scoped
from .authored_goal_binding_parse_scope_v300 import authored_goal_binding_parse_scope

need=authority.need
ERROR='续段原申请、顺序或独立审计无法核对；原记录保留'
MAX_PLANS=100
MAX_COMMANDS=500
ACTIVE={'draft','in_progress','awaiting_acceptance'}

def _hash(value):return digest({k:v for k,v in value.items() if k!='record_hash'})
def _task(db,p,identity,lock=False):return authority._task(db,p,identity,lock=lock)

def _snapshot(db,p,task,advice_id,execution_id,*,lock_sources=False):
    """Reconstruct and verify all live sources; never accept a client snapshot."""
    if lock_sources:provider.require_current(db,p,task,lock_sources=True)
    context=previous.context(db,p,task,advice_id)
    need(context['available'] and context['all_results_reviewed'],'原建议或后续办理尚不能用于续段')
    choices=[x for x in context['results'] if x['execution_id']==execution_id and x['reviewed'] and x['current_data']]
    need(len(choices)==1,'请选择已复核且依据当前资料的办理结果')
    selected=provider.binding(db,p,task);binding=selected['binding'];steps=previous._steps(binding)
    role_review=steps['review'];mapping=binding['step_mapping']
    definition={'steps':[{'key':'review_decision','name':'核对业务建议','role':role_review['role'],
                         'output':'业务建议核对意见','actor_id':role_review['actor_id']},
        *[{'key':m['node_id'],**step} for m,step in zip(mapping,steps['steps'])]]}
    old=scoped(db,Plan,advice_id,p);prep,job=advice._saved(db,p,task,old)
    link=bridge.inspect(db,p,task,old,prep,job);need(link is not None)
    ledger=execution_gate.goal_gate(db,p,task)
    rows=[r for r in executions._inventory(db,p,task) if r['plan'].id==execution_id]
    need(len(rows)==1 and rows[0]['plan'].status=='adopted')
    row=rows[0];need(row['proposal']['advice_plan_id']==advice_id)
    run=[r for r in ledger['runs'] if r['plan_id']==execution_id and r['phase']=='adopted' and r['settled']]
    need(len(run)==1)
    source={'schema':'formal-continuation-source-v345','workspace_id':p.workspace_id,'task_id':task.id,
        'advice_id':advice_id,'execution_id':execution_id,'context':context,'context_hash':digest(context),
        'binding_hash':binding['binding_hash'],'graph_hash':task.data['graph_runtime']['graph_hash'],
        'graph_state_hash':task.data['graph_runtime']['state_hash'],
        'retirement_link_hash':digest(link),'original_advice_hash':prep['record_hash'],
        'evaluation_head_hash':task.data[provider.KEY]['head_hash'],'goal_hash':task.data['goal_hash'],
        'execution_proposal_hash':row['proposal']['record_hash'],'execution_gate_hash':ledger['basis_hash']}
    return source,definition

def _expected_preview(task_id,advice_id,body,source,definition):
    preview={'schema':'formal-continuation-preview-v345','task_id':task_id,'advice_id':advice_id,
        'request':body,'source_hash':digest(source),'definition_hash':digest(definition),
        'cross_version':source['context']['different_generation']}
    return {**preview,'preview_hash':digest(preview)}

@authority.checked
@authored_goal_binding_parse_scope()
def preview(db,p,task_id,advice_id,body):
    authority._reader(db,p,write=True);task=_task(db,p,task_id);manage(p,task)
    need(_closed(db,p,task) is None,'目标已验收，原续段只可回看')
    need(task.version==body['expected_task_version'],'任务已变化，请重新核对')
    source,definition=_snapshot(db,p,task,advice_id,body['execution_id'])
    need(body['context_hash']==source['context_hash'],'资料或步骤已变化，请重新核对')
    need(not source['context']['different_generation'] or body['acknowledge_cross_version'],
        '请明确确认旧建议与当前资料分别来自不同版本')
    return _expected_preview(task_id,advice_id,body,source,definition)

def _event(db,p,plan,operation,body,before,after,previous,state):
    value={'schema':c.COMMAND,'task_id':plan.task_id,'advice_plan_id':plan.data['source']['advice_id'],
      'plan_id':plan.id,'operation':operation,'actor_id':p.user_id,'actor_role':p.role,
      'request_id':body['request_id'],'request':copy.deepcopy(body),'body_hash':digest(body),
      'source_hash':plan.data['source_hash'],'definition_hash':plan.data['definition_hash'],
      'version_before':before,'version_after':after,'previous_hash':previous,
      'state_hash':digest(state),'at':now().isoformat()}
    value['record_hash']=_hash(value)
    event=Audit(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,action=c.ACTIONS[operation],object_id=plan.id,data=value)
    db.add(event);db.flush()
    return {'id':event.id,'request_id':body['request_id'],'record_hash':value['record_hash']}

def _replay(plan,events,task):
    data=plan.data;need(type(data) is dict and set(data)=={'schema','source','source_hash','definition','definition_hash','creator_id','operations'}
      and data['schema']==c.SCHEMA and data['source_hash']==digest(data['source'])
      and data['definition_hash']==digest(data['definition']) and data['source']['task_id']==task.id
      and data['source']['workspace_id']==task.workspace_id and len(data['operations'])<=MAX_COMMANDS)
    indexed={e.id:e for e in events};need(len(indexed)==len(events)==len(data['operations']) and len(events)>0)
    state=c.initial();version=0;previous=None;receipts={}
    for pointer in data['operations']:
        event=indexed.get(pointer['id']);need(event is not None and event.object_id==plan.id)
        v=event.data;op=v.get('operation');body=v.get('request');need(op in c.ACTIONS and event.action==c.ACTIONS[op]
           and v.get('schema')==c.COMMAND and v.get('task_id')==task.id and v.get('advice_plan_id')==data['source']['advice_id']
           and v.get('plan_id')==plan.id and v.get('actor_id')==event.actor_id and v.get('actor_role') in {'admin','operator'}
           and v.get('source_hash')==data['source_hash'] and v.get('definition_hash')==data['definition_hash']
           and v.get('request_id')==body.get('request_id')
           and v.get('body_hash')==digest(body) and v.get('record_hash')==_hash(v)
           and pointer=={'id':event.id,'request_id':v['request_id'],'record_hash':v['record_hash']}
           and v['previous_hash']==previous and v['version_before']==version and v['version_after']==version+1
           and v['request_id'] not in receipts)
        model={'create':c.Create,'activate':c.Action,'complete_step':c.CompleteStep,'return_step':c.ReturnStep,'withdraw':c.Action}[op]
        need(model.model_validate(body).model_dump(mode='json')==body)
        if version==0:
            need(op=='create' and event.actor_id==data['creator_id'] and (event.actor_id==task.owner_id or v['actor_role']=='admin')
                 and body['context_hash']==data['source']['context_hash'] and body['execution_id']==data['source']['execution_id']
                 and (not data['source']['context']['different_generation'] or body['acknowledge_cross_version'])
                 and body['preview_hash']==_expected_preview(task.id,data['source']['advice_id'],_create_request(body),data['source'],data['definition'])['preview_hash'])
        else:
            need(op!='create' and body['expected_version']==version)
            try:state=c.apply(state,op,body,event.actor_id,data['definition'],manager=event.actor_id==task.owner_id or v['actor_role']=='admin')
            except c.TransitionError:need(False)
        need(v['state_hash']==digest(state));version+=1;previous=v['record_hash']
        receipts[v['request_id']]={'request_id':v['request_id'],'plan_id':plan.id,'operation':op,
           'version':version,'state':copy.deepcopy(state),'record_hash':previous}
    need(plan.version==version and plan.status==state['phase'])
    return state,receipts

def _inventory(db,p,task):
    index=task.data.get(c.INDEX,[]);need(type(index) is list and len(index)<=MAX_PLANS)
    ids=[item['plan_id'] for item in index];need(len(ids)==len(set(ids)))
    plans=list(db.scalars(select(Plan).where(Plan.workspace_id==p.workspace_id,or_(
        (Plan.task_id==task.id)&(Plan.data['schema'].as_string()==c.SCHEMA),Plan.id.in_(ids))).limit(MAX_PLANS+1)))
    need(len(plans)==len(index)<=MAX_PLANS and {x.id for x in plans}==set(ids) and all(x.task_id==task.id for x in plans))
    events=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action.in_(list(c.ACTIONS.values())),
        or_(Audit.data['task_id'].as_string()==task.id,Audit.object_id.in_(ids))).limit(MAX_PLANS*MAX_COMMANDS+1)))
    need(len(events)<=MAX_PLANS*MAX_COMMANDS and all(e.object_id in ids for e in events))
    result={}
    for plan in plans:
        state,receipts=_replay(plan,[e for e in events if e.object_id==plan.id],task)
        entry=next(x for x in index if x['plan_id']==plan.id)
        need(entry=={'plan_id':plan.id,'source_hash':plan.data['source_hash'],'creation_audit_id':plan.data['operations'][0]['id']})
        result[plan.id]={'plan':plan,'state':state,'receipts':receipts}
    need(sum(x['state']['phase'] in ACTIVE for x in result.values())<=1)
    return result

def _receipt(inventory,request_id,p,body):
    for saved in inventory.values():
        receipt=saved['receipts'].get(request_id)
        if receipt is None:continue
        pointer=next(x for x in saved['plan'].data['operations'] if x['request_id']==request_id)
        return saved,receipt,pointer
    return None

def _closed(db,p,task,*,lock=False):
    native=task.data.get('native_goal_acceptance')
    if native is not None:
        need(type(native) is dict and digest(native)==task.data.get('native_goal_acceptance_hash'))
        return 'native_goal'
    from .layered_approval_store_v175 import managed,checked,acceptance_hash
    row=managed(db,p,task,lock=lock)
    if row is None or row.status!='accepted':return None
    value=checked(row).get('acceptance');need(type(value) is dict and value.get('decision')=='accept')
    basis=value.get('basis');need(type(basis) is dict and basis.get('task_id')==task.id
        and basis.get('basis_hash')==acceptance_hash({k:v for k,v in basis.items() if k!='basis_hash'}))
    return 'layered_approval'

def _create_request(body):return {k:v for k,v in body.items() if k!='preview_hash'}

@authority.checked
@authored_goal_binding_parse_scope()
def create(db,p,task_id,advice_id,body):
    authority._reader(db,p,write=True)
    nonce.acquire(db,p,action=c.ACTIONS['create'],task_id=task_id,advice_id=advice_id,body=body)
    task=_task(db,p,task_id,lock=True);lock_current_actor(db,p);manage(p,task)
    inventory=_inventory(db,p,task);existing=_receipt(inventory,body['request_id'],p,body)
    if existing:return existing[1]
    need(_closed(db,p,task,lock=True) is None,'目标已验收，原续段只可回看')
    need(len(inventory)<MAX_PLANS,'续段历史已达上限，请保留原记录')
    need(not any(x['state']['phase'] in ACTIVE for x in inventory.values()),'已有尚未结束的续段，请先查看原记录')
    need(task.version==body['expected_task_version'],'任务已变化，请重新核对')
    source,definition=_snapshot(db,p,task,advice_id,body['execution_id'],lock_sources=True)
    base=_create_request(body);need(body['context_hash']==source['context_hash'],'资料或步骤已变化，请重新核对')
    need(not source['context']['different_generation'] or body['acknowledge_cross_version'],'请明确确认跨版本依据')
    expected=_expected_preview(task_id,advice_id,base,source,definition)
    need(body['preview_hash']==expected['preview_hash'],'预览依据已变化，请重新核对')
    plan=Plan(id=uid(),workspace_id=p.workspace_id,task_id=task.id,status='draft',version=1,data={
       'schema':c.SCHEMA,'source':source,'source_hash':digest(source),'definition':definition,
       'definition_hash':digest(definition),'creator_id':p.user_id,'operations':[]})
    db.add(plan);db.flush();state=c.initial()
    pointer=_event(db,p,plan,'create',body,0,1,None,state);plan.data={**plan.data,'operations':[pointer]}
    task.data={**task.data,c.INDEX:[*task.data.get(c.INDEX,[]),{'plan_id':plan.id,'source_hash':plan.data['source_hash'],'creation_audit_id':pointer['id']}]};task.version+=1
    db.flush();return {'request_id':body['request_id'],'plan_id':plan.id,'operation':'create','version':1,'state':state,'record_hash':pointer['record_hash']}

@authority.checked
@authored_goal_binding_parse_scope()
def action(db,p,task_id,advice_id,plan_id,operation,body):
    need(operation in {'activate','complete_step','return_step','withdraw'})
    authority._reader(db,p,write=True)
    nonce.acquire(db,p,action=c.ACTIONS[operation],task_id=task_id,advice_id=advice_id,subject_id=plan_id,body=body)
    task=_task(db,p,task_id,lock=True);lock_current_actor(db,p);inventory=_inventory(db,p,task)
    saved=inventory.get(plan_id);need(saved is not None and saved['plan'].data['source']['advice_id']==advice_id)
    existing=_receipt(inventory,body['request_id'],p,body)
    if existing:return existing[1]
    need(_closed(db,p,task,lock=True) is None,'目标已验收，原续段只可回看')
    plan=saved['plan'];state=saved['state'];need(plan.version==body['expected_version'],'续段已变化，请重新读取')
    manager=p.role=='admin' or p.user_id==task.owner_id
    if operation!='withdraw':
        source,definition=_snapshot(db,p,task,advice_id,plan.data['source']['execution_id'],lock_sources=True)
        need(source==plan.data['source'] and definition==plan.data['definition'],'原资料或责任人已变化，请保留记录并重新核对')
        if operation=='activate':
            need(source['context']['review']['available'] and all(x['available'] for x in source['context']['steps']),
                 '有责任人当前不可办理，请先核对工作区权限')
    try:new_state=c.apply(state,operation,body,p.user_id,plan.data['definition'],manager=manager)
    except c.TransitionError as e:raise HTTPException(409,str(e)) from None
    need(len(plan.data['operations'])<MAX_COMMANDS,'续段历史已达上限，请保留原记录')
    pointer=_event(db,p,plan,operation,body,plan.version,plan.version+1,plan.data['operations'][-1]['record_hash'],new_state)
    plan.data={**plan.data,'operations':[*plan.data['operations'],pointer]};plan.version+=1;plan.status=new_state['phase'];db.flush()
    return {'request_id':body['request_id'],'plan_id':plan.id,'operation':operation,'version':plan.version,
        'state':new_state,'record_hash':pointer['record_hash']}

@authority.checked
@authored_goal_binding_parse_scope()
def listing(db,p,task_id,advice_id):
    authority._reader(db,p);task=_task(db,p,task_id);values=_inventory(db,p,task)
    return {'plans':[{'plan_id':row['plan'].id,'phase':row['state']['phase'],'version':row['plan'].version}
        for row in values.values() if row['plan'].data['source']['advice_id']==advice_id],
        'task_completed':bool(_closed(db,p,task)),'device_actions':0,'cloud_calls':0}

@authority.checked
@authored_goal_binding_parse_scope()
def read(db,p,task_id,advice_id,plan_id):
    authority._reader(db,p);task=_task(db,p,task_id);saved=_inventory(db,p,task).get(plan_id)
    need(saved is not None and saved['plan'].data['source']['advice_id']==advice_id,'续段不存在或不属于原建议')
    plan=saved['plan'];state=saved['state'];source=plan.data['source'];definition=plan.data['definition']
    current=False;unavailable=None
    if state['phase'] in ACTIVE:
        try:
            live,actual_definition=_snapshot(db,p,task,advice_id,source['execution_id'])
            current=live==source and actual_definition==definition
            if not current:unavailable='资料、角色或原依据已变化，请保留原记录并重新核对。'
        except (HTTPException, KeyError, TypeError, ValueError, AttributeError, IndexError):
            unavailable='当前资料或权限尚不能核对；已保存记录保留。'
    closed=_closed(db,p,task)
    manager=p.role=='admin' or task.owner_id==p.user_id
    index=state['next_step'];assigned=index<len(definition['steps']) and definition['steps'][index]['actor_id']==p.user_id
    last_assigned=bool(definition['steps']) and definition['steps'][-1]['actor_id']==p.user_id
    context=source['context'];people=[context['review'],*context['steps']]
    steps=[]
    for i,(item,person) in enumerate(zip(definition['steps'],people)):
        completed=state['completed'].get(item['key'])
        steps.append({'key':item['key'],'name':item['name'],'role':item['role'],'output':item['output'],
            'person':person['person'],'available':person['available'],'result':completed['result'] if completed else None,
            'status':'completed' if completed else 'ready' if state['phase']=='in_progress' and i==index else 'waiting'})
    return {'schema':'formal-continuation-view-v345','plan_id':plan.id,'version':plan.version,
        'phase':state['phase'],'current_sources_verified':current,'source_notice':unavailable,
        'original_advice_generation':context['advice_generation'],'current_data_generation':context['current_generation'],
        'simulation_only':next(x['simulation_only'] for x in context['results'] if x['execution_id']==source['execution_id']),
        'test_only':next(x['test_only'] for x in context['results'] if x['execution_id']==source['execution_id']),
        'steps':steps,'permissions':{'can_activate':not closed and current and state['phase']=='draft' and manager and context['review']['available'] and all(x['available'] for x in context['steps']),
           'can_complete_step':not closed and current and state['phase']=='in_progress' and assigned,
           'can_return_step':not closed and current and ((state['phase']=='in_progress' and assigned) or (state['phase']=='awaiting_acceptance' and last_assigned)),
           'can_withdraw':not closed and state['phase'] in ACTIVE and manager},
        'goal_completed':bool(closed),'acceptance_available':False,'device_actions':0,'cloud_calls':0}

def _abandonment(db,p,task,advice_id,request_id):
    events=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==c.ABANDON,
        Audit.data['request_id'].as_string()==request_id).limit(2)))
    need(len(events)<=1)
    if not events:return None
    event=events[0];v=event.data
    need(event.object_id==task.id and event.actor_id==p.user_id and v.get('schema')=='formal-continuation-abandonment-v345'
        and v.get('workspace_id')==p.workspace_id and v.get('actor_role') in {'admin','operator'}
        and v.get('task_id')==task.id and v.get('advice_plan_id')==advice_id and v.get('actor_id')==p.user_id
        and v.get('request_id')==request_id and v.get('record_hash')==_hash(v) and v.get('body_hash')==digest(v.get('request')))
    body=c.Abandon.model_validate(v['request']).model_dump(mode='json')
    need(body==v['request'] and body['request_id']==request_id and v['original_body_hash']==digest(body['original_body']))
    return {'found':True,'abandoned':True,'request_id':request_id,'operation':body['operation'],
        'plan_id':body['plan_id'],'body_hash':v['original_body_hash'],'record_hash':v['record_hash'],
        'not_found_proves_unexecuted':False}

@authority.checked
def original(db,p,task_id,advice_id,request_id):
    authority._reader(db,p);task=_task(db,p,task_id);saved=_receipt(_inventory(db,p,task),request_id,p,None)
    negative=_abandonment(db,p,task,advice_id,request_id)
    need(not (saved is not None and negative is not None),'原申请与终止凭证冲突，请保留记录核对')
    if negative is not None:return negative
    if saved is None:return {'found':False,'not_found_proves_unexecuted':False}
    row,receipt,pointer=saved;need(row['plan'].data['source']['advice_id']==advice_id)
    event=scoped(db,Audit,pointer['id'],p);need(event.actor_id==p.user_id,'请由原申请账号核对')
    return {'found':True,'receipt':receipt,'body_hash':event.data['body_hash'],'not_found_proves_unexecuted':False}

@authority.checked
@authored_goal_binding_parse_scope()
def abandon(db,p,task_id,advice_id,request_id,body):
    """Atomically reserve an unexecuted original nonce; no business effects."""
    authority._reader(db,p,write=True);need(body['request_id']==request_id)
    nonce.acquire(db,p,action=c.ABANDON,task_id=task_id,advice_id=advice_id,body=body)
    task=_task(db,p,task_id,lock=True);lock_current_actor(db,p)
    inventory=_inventory(db,p,task)
    prior=_abandonment(db,p,task,advice_id,request_id)
    if prior is not None:return prior
    need(_receipt(inventory,request_id,p,None) is None,'原申请已有结果，请先读取原回执')
    if body['plan_id'] is not None:
        item=inventory.get(body['plan_id']);need(item is not None and item['plan'].data['source']['advice_id']==advice_id)
    value={'schema':'formal-continuation-abandonment-v345','workspace_id':p.workspace_id,'task_id':task.id,
        'advice_plan_id':advice_id,'actor_id':p.user_id,'actor_role':p.role,'request_id':request_id,
        'request':copy.deepcopy(body),'body_hash':digest(body),'original_body_hash':digest(body['original_body'])}
    value['record_hash']=_hash(value)
    db.add(Audit(id=uid(),workspace_id=p.workspace_id,actor_id=p.user_id,action=c.ABANDON,object_id=task.id,data=value));db.flush()
    return {'found':True,'abandoned':True,'request_id':request_id,'operation':body['operation'],'plan_id':body['plan_id'],
        'body_hash':value['original_body_hash'],'record_hash':value['record_hash'],'not_found_proves_unexecuted':False}

@authority.checked
def goal_guard(db,p,task,*,lock_sources=False):
    """An unclosed manual continuation can never silently become Goal acceptance.

    A completed continuation is independently verified before it can settle.
    Ordinary historic tasks without any relevant graph markers retain their
    original assessment shape and need no additional database read.
    """
    if c.INDEX not in task.data and not task.data.get(retirement_marker()) and not task.data.get(bridge.KEY):
        return None
    saved=_inventory(db,p,task)
    if not saved:return None
    phases=[x['state']['phase'] for x in saved.values()]
    history=[{'plan_id':key,'version':v['plan'].version,'phase':v['state']['phase'],
            'source_hash':v['plan'].data['source_hash'],'last_command_hash':v['plan'].data['operations'][-1]['record_hash']}
           for key,v in sorted(saved.items())]
    completion=None
    if 'awaiting_acceptance' in phases:
        from .formal_continuation_proof_v346 import verify
        completion=verify(db,p,task,lock_sources=lock_sources)
    unsettled=sum(x in {'draft','in_progress'} or x=='awaiting_acceptance' and completion is None for x in phases)
    proof_hash=digest(history) if completion is None else digest({'history':history,'completion_hash':completion['basis_hash']})
    value={'schema':'formal-continuation-goal-guard-v345','all_settled':unsettled==0,
        'open_count':unsettled,'continuation_count':len(phases),'basis_hash':proof_hash,
        'formal_completion_verified':completion is not None}
    if completion is not None:
        value.update(completion_proof_hash=completion['basis_hash'],advice_generation=completion['advice_generation'],data_generation=completion['data_generation'],
                     simulation_only=completion['simulation_only'],test_only=completion['test_only'])
    return value

def retirement_marker():
    from .graph_reasoning_retirement_v337 import KEY
    return KEY
