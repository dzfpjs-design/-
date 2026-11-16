"""Read-only, provenance-checked preparation for a separate workflow continuation.

This never changes the original graph, creates a continuation, or authorizes work.
"""
import copy
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .models import Plan, User, Membership
from .services import scoped
from . import goal_evaluation_provider_v288 as provider
from . import graph_reasoning_retirement_v337 as retirement
from . import same_goal_reasoning_bridge_v339 as bridge
from . import same_goal_advice_store_v302 as advice
from . import same_goal_continuation_sources_v312 as source
from . import same_goal_continuation_execution_store_v314 as executions
from . import same_goal_continuation_execution_goal_v314 as gate
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation

SCHEMA='formal-continuation-context-v344'
ERROR='原流程、审核或办理依据无法完整核对；原记录保留，暂不能继续'

def need(value, message=ERROR):
    if not value: raise HTTPException(409,message)

def _steps(binding):
    """Derive ordered steps from the frozen confirmed graph, never client input."""
    flow=binding['flow']['steps'];mapping=binding['step_mapping'];graph=binding['graph']
    need(type(flow) is list and 0<len(flow)<=100 and type(mapping) is list and len(mapping)==len(flow))
    nodes=graph['nodes'];edges=graph['edges'];by_id={n['id']:n for n in nodes}
    need(len(by_id)==len(nodes) and 'reasoning' in by_id and 'review_decision' in by_id)
    ids=[];steps=[]
    for step,m in zip(flow,mapping):
        need(type(step) is dict and type(m) is dict and m['source_step_id']==step['id']
             and m['name']==step['name'] and m['role']==step['role'] and m['output']==step['output'])
        n=by_id.get(m['node_id']);need(n and n['kind']=='human' and n['source_step_id']==step['id']
            and n['role']==step['role'] and n['actor_id']==binding['role_bindings'][step['role']]['user_id'])
        need(m['node_id'] not in ids);ids.append(m['node_id'])
        steps.append({'name':step['name'],'role':step['role'],'output':step['output'],'actor_id':n['actor_id']})
    route=['reasoning','review_decision',*ids,'end']
    for a,b in zip(route,route[1:]):
        found=[e for e in edges if e['from']==a and e['to']==b and e['on']=='success'
               and e['branch_id'] is None and e['loop_id'] is None]
        need(len(found)==1)
    review=by_id['review_decision'];need(review['kind']=='human' and review['source_step_id'] is None)
    return {'review':{'role':review['role'],'actor_id':review['actor_id']},'steps':steps}

def _person(entry, users, memberships):
    person=users.get(entry['actor_id']);membership=memberships.get(entry['actor_id'])
    return {**{k:v for k,v in entry.items() if k!='actor_id'},'person':person.username if person else None,
            'available':bool(person and person.active and membership and membership.role in {'admin','operator'})}

def context(db,p,task,advice_id):
    """Inspect original sources and list reviewed results eligible for later planning."""
    selected=provider.binding(db,p,task);need(selected is not None)
    bound=selected['binding'];state=task.data['graph_runtime'];need(task.status=='workflow_waiting')
    old=scoped(db,Plan,advice_id,p);need(old.task_id==task.id)
    prep,job=advice._saved(db,p,task,old)
    link=bridge.inspect(db,p,task,old,prep,job)
    if link is None:
        return {'schema':SCHEMA,'available':False,'reason':'这份建议没有关联已停用的原分析步骤，不能作为原流程续段依据。',
                'can_start':False,'task_completed':False,'device_actions':0,'cloud_calls':0}
    _,_,review,original,_,_=source._source(db,p,task,old)
    need(review['decision']=='accepted','原建议尚未获得采纳意见，不能作为续段依据')
    records,_,_,_=bridge._rows(db,p,task)
    retired=[r for r in records if r['request_id']==link['retirement_request_id']]
    need(len(retired)==1);record=retired[0]
    original_job=scoped(db,__import__('generic_agents.models',fromlist=['Job']).Job,record['job_id'],p)
    act=state['activations'].get(record['activation_id']);transport=task.data['graph_jobs'].get(record['activation_id'])
    need(act and transport and act['node_id']=='reasoning' and act['status']=='ready')
    retirement._no_prior_dispatch(db,p,task,original_job,act,transport,prep['model_pin']['id'],retired=True)
    current=provider.require_current(db,p,task,lock_sources=False)
    head=task.data[provider.KEY];need(current is not None and head['evaluation_ref']==current['evaluation_ref'])
    ref=prep['mature_head']['evaluation_ref'];old_eval=scoped(db,Evaluation,ref['id'],p)
    need(old_eval.task_id==task.id and old_eval.record_hash==ref['record_hash'])
    definition=_steps(bound)
    actor_ids={definition['review']['actor_id'],*(s['actor_id'] for s in definition['steps'])}
    users={u.id:u for u in db.scalars(select(User).where(User.id.in_(actor_ids)))}
    memberships={m.user_id:m for m in db.scalars(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id.in_(actor_ids)))}
    goal=gate.goal_gate(db,p,task);rows=executions._inventory(db,p,task);by_id={x['plan'].id:x for x in rows}
    choices=[]
    for run in goal['runs']:
        row=by_id[run['plan_id']];proposal=row['proposal'];basis=proposal['grant']['basis']
        if run['phase']!='adopted' or proposal['advice_plan_id']!=old.id:continue
        same_head=basis['evaluation']['mature_head_hash']==head['head_hash']
        choices.append({'execution_id':run['plan_id'],'reviewed':True,'data_generation':basis['evaluation']['generation'],
          'data_as_of':basis['evaluation']['cut_at'],'current_data':same_head,
          'record_count':len(basis['records']),'field':copy.deepcopy(basis['field']),
          'operation':basis['operation'],'simulation_only':basis['simulation_only'],'test_only':basis['test_only']})
    return {'schema':SCHEMA,'available':True,'can_start':False,'task_completed':False,'device_actions':0,'cloud_calls':0,
      'advice_generation':old_eval.generation,'advice_data_as_of':original['data_as_of'],
      'current_generation':head['generation'],'different_generation':old_eval.generation!=head['generation'],
      'review':_person(definition['review'],users,memberships),'steps':[_person(s,users,memberships) for s in definition['steps']],
      'results':choices,'all_results_reviewed':goal['all_reviewed'],
      'reason':'当前仅能核对来源和待办，正式续段尚未启动；原业务目标仍未验收。'}
