"""Independently verify a manual replacement without rewriting the original graph.

This proves only the recorded continuation. Goal acceptance still requires the
existing data, policy, role and independent-acceptance checks.
"""
from fastapi import HTTPException
from . import formal_continuation_contracts_v345 as contract
from . import formal_continuation_store_v345 as store
from . import goal_evaluation_provider_v288 as provider
from . import graph_reasoning_retirement_v337 as retirement
from . import same_goal_reasoning_bridge_v339 as bridge
from . import same_goal_advice_store_v302 as advice
from . import workflow_graph_scope as scope
from . import workflow_job_dependencies_v264 as controls
from . import full_semantic_graph_v1 as kernel
from .contracts import digest
from .models import Plan,Job,Invocation,Capability
from .services import scoped
from .report_full_semantic_runtime_adoption import executor

SCHEMA='formal-continuation-completion-proof-v346'
need=store.need


def _frontier(task,graph,retired_id):
    state=kernel.check_run(graph,task.data['graph_runtime']);acts=state['activations'];nodes={n['id']:n for n in graph['nodes']}
    need(state['status']=='running' and state['error'] is None and state['goal_receipts']==[]
         and task.status=='workflow_waiting' and state['ready_human_ids']==[]
         and state['eligible_ability_ids']==[retired_id] and retired_id in acts)
    target=acts[retired_id];need(target['id']==retired_id and target['status']=='ready' and target['node_id']=='reasoning')
    need(all(a['id']==identity and (identity==retired_id or a['status']=='completed') for identity,a in acts.items()))
    need(all(a['node_id'] in nodes for a in acts.values()))
    abilities={identity for identity,a in acts.items() if nodes[a['node_id']]['kind']=='ability'}
    need(retired_id in abilities and set(task.data.get('graph_jobs',{}))==abilities)
    return state,acts,nodes,abilities


def verify(db,p,task,*,lock_sources=False):
    """Return one current proof, or None if no completed continuation exists."""
    if contract.INDEX not in task.data and not task.data.get(retirement.KEY) and not task.data.get(bridge.KEY):
        return None
    rows=store._inventory(db,p,task)
    candidates=[x for x in rows.values() if x['state']['phase']=='awaiting_acceptance']
    if not candidates:return None
    need(len(candidates)==1 and not any(x['state']['phase'] in {'draft','in_progress'} for x in rows.values()))
    saved=candidates[0];plan=saved['plan'];source=plan.data['source'];definition=plan.data['definition'];state=saved['state']
    current,actual_definition=store._snapshot(db,p,task,source['advice_id'],source['execution_id'],lock_sources=lock_sources)
    need(current==source and actual_definition==definition,'续段依据或责任人已变化，请保留记录重新核对')
    steps=definition['steps'];need(state['next_step']==len(steps) and set(state['completed'])=={x['key'] for x in steps})
    for actor in sorted({x['actor_id'] for x in steps}):executor(db,p,actor,lock=lock_sources)
    original=scoped(db,Plan,source['advice_id'],p);prep,job=advice._saved(db,p,task,original)
    link=bridge.inspect(db,p,task,original,prep,job);need(link is not None and digest(link)==source['retirement_link_hash'])
    record=retirement.historical_record(db,p,task,link['retirement_request_id'],original_actor_only=False)
    need(record is not None and digest(record)==link['retirement_hash'])
    selected=provider.binding(db,p,task);need(selected is not None and selected['binding']['binding_hash']==source['binding_hash'])
    graph=selected['binding']['graph'];retired_id=record['activation_id']
    graph_state,activations,nodes,abilities=_frontier(task,graph,retired_id)
    need(graph_state['graph_hash']==source['graph_hash'] and graph_state['state_hash']==source['graph_state_hash'])
    control=controls.checked_control(db,p,task);need(control is not None and control['state_hash_at_capture']==graph_state['state_hash'])
    receipts=[]
    for identity in sorted(abilities-{retired_id}):
        _,_,_,saved_graph,checked_state,activation,node=scope._frozen(db,p,task.id,identity)
        need(saved_graph==graph and checked_state['state_hash']==graph_state['state_hash'] and activation['status']=='completed')
        transport=task.data['graph_jobs'][identity];item=scoped(db,Job,transport['job_id'],p)
        scope._job_matches(task,item,checked_state,activation,node)
        call=scoped(db,Invocation,transport['invocation_id'],p);cap=scoped(db,Capability,node['execution']['capability_id'],p)
        need(transport['phase']=='succeeded' and item.status=='succeeded' and call.status=='succeeded'
             and call.idempotency_key==transport['invocation_key'] and call.data.get('job_id')==item.id
             and call.data.get('input_hash')==transport['input_hash'] and call.data.get('output')==activation['output']
             and call.capability_id==cap.id and cap.content_hash==cap.validated_hash==digest(cap.data)==node['execution']['capability_hash'])
        receipts.append({'activation_id':identity,'job_id':item.id,'invocation_id':call.id,
                         'input_hash':transport['input_hash'],'output_hash':digest(activation['output'])})
    body={'schema':SCHEMA,'workspace_id':p.workspace_id,'task_id':task.id,'plan_id':plan.id,
      'plan_version':plan.version,'source_hash':plan.data['source_hash'],'definition_hash':plan.data['definition_hash'],
      'last_command_hash':plan.data['operations'][-1]['record_hash'],'steps_hash':digest(state['completed']),
      'goal_hash':source['goal_hash'],'current_head_hash':source['evaluation_head_hash'],
      'original_graph_hash':graph_state['graph_hash'],'original_state_hash':graph_state['state_hash'],
      'control_hash':control['record_hash'],'retirement_hash':digest(record),'retired_activation_id':retired_id,
      'upstream_receipts_hash':digest(receipts),'advice_generation':source['context']['advice_generation'],
      'data_generation':source['context']['current_generation'],
      'simulation_only':next(x['simulation_only'] for x in source['context']['results'] if x['execution_id']==source['execution_id']),
      'test_only':next(x['test_only'] for x in source['context']['results'] if x['execution_id']==source['execution_id']),
      'original_node_completed':False,'physical_state_verified':False,'goal_accepted':False}
    return {**body,'basis_hash':digest(body)}
