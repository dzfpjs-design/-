"""Server-pinned data/parent needs intersect independent monitor permission."""
import copy
from types import SimpleNamespace
from fastapi import HTTPException
from .contracts import digest
from .models import DataGap,DataRun,Task,ObservationPlan
from .services import scoped
from .temporal import instant


def pin(db,p,gap):
    run=scoped(db,DataRun,gap.query_id,p)
    value={'gap_id':gap.id,'query_id':run.id,'entity_id':gap.entity_id,
        'fields':sorted(set(gap.data['missing_fields'])),
        'request':copy.deepcopy(gap.data['request']),'query_request_hash':digest(run.data['request']),
        'goal_delegation':copy.deepcopy(run.data.get('goal_delegation'))}
    guard(db,p,value,dispatch=True)
    return value


def guard(db,p,value,*,dispatch=False):
    from .data_gaps import locked_gap
    gap=locked_gap(db,p,value['gap_id']);run=scoped(db,DataRun,value['query_id'],p)
    if gap.query_id!=run.id or gap.entity_id!=value['entity_id'] or value['entity_id'] not in value['request']['entity_ids']:
        raise HTTPException(409,'补证约束的原查询与对象不一致')
    if run.status=='cancelled' or gap.status=='cancelled' or dispatch and gap.status=='resolved':
        raise HTTPException(409,'原查询或补证已停止/满足，未继续设备动作')
    if digest(run.data['request'])!=value['query_request_hash'] or gap.data['request']!=value['request'] or run.data.get('goal_delegation')!=value['goal_delegation']:
        raise HTTPException(409,'原查询要求或业务委托已变化，未继续旧补证')
    if not value['fields'] or not set(value['fields']).issubset(gap.data['request']['fields']):
        raise HTTPException(409,'补采字段超出原查询明确要求')
    from .goal_queries import guard as guard_goal
    guard_goal(db,p,run)
    if value['goal_delegation']:
        task=scoped(db,Task,value['goal_delegation']['task_id'],p,True)
        spec=task.data['goal_spec']
        if value['entity_id'] not in spec['entity_ids'] or not set(value['fields']).issubset(spec['required_fields']):
            raise HTTPException(409,'补采对象或字段超出已确认原业务目标')
        if spec['observation_mode']=='existing_only':
            raise HTTPException(409,'原业务目标只允许既有证据；缺口送业务核查，监测权限不授予本目标新设备动作')
    from .goal_conflict_observation_service_v259 import verify_origin
    verify_origin(db,p,value)
    return gap,run


def effective(db,p,policy,value,*,dispatch=False):
    if not value:return policy
    guard(db,p,value,dispatch=dispatch)
    fields=value['fields'];requirement=copy.deepcopy(value['request']['evidence_requirement'])
    if not set(fields).issubset(policy.data['fields']) or value['entity_id'] not in policy.data['entity_ids']:
        raise HTTPException(409,'原补证不在监测批准范围内')
    if not requirement or requirement['source']=='archive' or requirement['time']['mode'] not in {'archive','latest'}:
        raise HTTPException(409,'指定档案或历史日期需核查已有来源，未触发新设备')
    requirement['source']='observations'
    age=min(policy.data['max_age_seconds'],requirement['time'].get('max_age_seconds') or policy.data['max_age_seconds'])
    requirement['time']={'mode':'latest','timezone':requirement['time'].get('timezone') or policy.data.get('timezone','UTC'),'max_age_seconds':age}
    verification='confirmed' if requirement['minimum_verification']!='unverified_extraction' or policy.data['selection_requirement']['minimum_verification']=='confirmed' else 'unverified_extraction'
    requirement['minimum_verification']=verification
    data={**policy.data,'fields':fields,'max_age_seconds':age,
        'selection_requirement':{**policy.data['selection_requirement'],'minimum_verification':verification},
        'observation_evidence_requirement':requirement}
    return SimpleNamespace(id=policy.id,workspace_id=policy.workspace_id,approved_by=policy.approved_by,data=data)


def deadline(value):
    if value and value.get('goal_delegation'):
        at=value['goal_delegation']['goal_snapshot']['spec'].get('deadline')
        if at:return instant(at)


def lock_acquisition_origin(db,p,acq):
    """Gap→parent→monitor ordering, held only until the dispatch checkpoint commit."""
    plan_id=acq.data.get('observation_plan_id')
    if not plan_id:return
    value=scoped(db,ObservationPlan,plan_id,p).data.get('observation_request')
    if value:
        from .data_gaps import locked_gap
        locked_gap(db,p,value['gap_id'])
        if value.get('goal_delegation'):scoped(db,Task,value['goal_delegation']['task_id'],p,True)


def frozen(policy,acq):
    value=acq.data.get('dispatch_observation_constraints')
    if not value:raise HTTPException(409,'已派发业务补证缺少原质量快照，保留占用并核查')
    return SimpleNamespace(id=policy.id,workspace_id=policy.workspace_id,data={**policy.data,**copy.deepcopy(value)})
