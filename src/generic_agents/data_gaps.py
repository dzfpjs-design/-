"""Data emits durable needs; perception alone selects actions under approved policy."""
import copy
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from fastapi import HTTPException
from datetime import timedelta
from .models import DataGap, GapCheck, DataRun, Event, Job, MonitorPolicy, Acquisition, now
from .services import scoped, enqueue_event, audit
from .contracts import digest

TERMINAL = {'resolved', 'cancelled'}


def record(db, p, gap, operation, **details):
    db.add(GapCheck(workspace_id=p.workspace_id, gap_id=gap.id, actor_id=p.user_id,
        data={'operation':operation, 'status':gap.status, 'at':now().isoformat(), **details}))
    audit(db,p,'data.gap_'+operation,gap.id,details)


def queue(db, workspace_id, kind, key, data):
    db.execute(insert(Job).values(workspace_id=workspace_id,kind=kind,idempotency_key=key,data=data)
        .on_conflict_do_nothing(index_elements=['workspace_id','idempotency_key']))


def publish(db,p,query_id,state,include_records=False):
    if not query_id or state.get('status') not in {'insufficient','conflict'}:return []
    run=scoped(db,DataRun,query_id,p)
    result=state.get('result',{});groups={}
    for item in result.get('gaps',[]):
        if item.get('route') in ({'perception','records'} if include_records else {'perception'}) and item.get('entity_id'):
            groups.setdefault(item['entity_id'],[]).append(item)
    # A real same-time conflict has usable records, so it need not also appear
    # in missing-field gaps. Preserve that distinction instead of losing the
    # durable coordination record or calling the reported field absent.
    conflicts={}
    scope=set(run.data['request']['entity_ids']);required=set(run.data['request']['fields'])
    evidence={(item.get('entity_id'),item.get('observation_id')) for item in result.get('observation_evidence',[])}
    for item in result.get('conflicts',[]):
        if not isinstance(item,dict) or item.get('entity_id') not in scope or item.get('field') not in required:continue
        ids=item.get('observation_ids')
        if not isinstance(ids,list) or len(ids)<2 or len(ids)!=len(set(ids)):continue
        if any((item['entity_id'],identifier) not in evidence for identifier in ids):continue
        ref={key:copy.deepcopy(item[key]) for key in ('field','observation_ids','observed_at','reason') if key in item}
        conflicts.setdefault(item['entity_id'],[]).append(ref)
        groups.setdefault(item['entity_id'],[])
    identifiers=[]
    for entity_id,items in groups.items():
        # Relation traversal must remain within the original authorized scope.
        if entity_id not in run.data['request']['entity_ids']:continue
        existing=db.scalar(select(DataGap).where(DataGap.workspace_id==p.workspace_id,
            DataGap.query_id==query_id,DataGap.entity_id==entity_id))
        if existing:identifiers.append(existing.id);continue
        need=state['need']
        request={**run.data['request'],'mode':'structured','model_profile_id':None,
            'fields':need['fields'],'relation_query':need.get('relation_query') or run.data['request'].get('relation_query')}
        gap=DataGap(workspace_id=p.workspace_id,query_id=query_id,entity_id=entity_id,data={
            'actor_id':run.owner_id,'request':request,'missing_fields':(list(dict.fromkeys(
                f for item in items for f in item.get('missing_fields',[]) )) or need['fields']) if items else [],
            'reason':'；'.join(dict.fromkeys([i['reason'] for i in items if i.get('reason')]+(
                ['已有同一声明时刻的结果不一致，需核查原来源；字段已存在，未自动新增设备动作'] if conflicts.get(entity_id) else []))),
            **({'kind':'missing_and_conflict' if items else 'conflict',
                'conflict_fields':sorted({item['field'] for item in conflicts[entity_id]}),
                'conflict_refs':conflicts[entity_id]} if conflicts.get(entity_id) else {}),
            'original_status':state['status'],'evidence_refs':[
                {k:item[k] for k in ['entity_id','observation_id','version','observed_at'] if k in item}
                for item in result.get('evidence',[])+result.get('observation_evidence',[])
                if item['entity_id']==entity_id], 'detected_at':now().isoformat()})
        if run.data.get('native_goal_task_id'):
            from .models import Task
            parent=scoped(db,Task,run.data['native_goal_task_id'],p)
            if run.data.get('native_goal_context_hash')!=digest(parent.data.get('native_goal_context')):raise HTTPException(409,'原目标缺口接收上下文无法核实')
            gap.data={**gap.data,'business_task_id':parent.id,'business_received_at':now().isoformat()}
        db.add(gap);db.flush();identifiers.append(gap.id)
        event=enqueue_event(db,p,'data.gap','data-gap:'+gap.id,{'gap_id':gap.id,'query_id':query_id,
            'entity_id':entity_id,'actor_id':run.owner_id,'causation':'data_query','gap_version':gap.version})
        gap.data={**gap.data,'event_id':event.id}
        record(db,p,gap,'detected',reason=gap.data['reason'])
    return identifiers


def receive(db,event):
    gap=db.scalar(select(DataGap).where(DataGap.id==event.data['gap_id'],
        DataGap.workspace_id==event.workspace_id).with_for_update())
    if not gap:return False
    if gap.status=='pending_delivery':
        from .security import Principal
        p=Principal(gap.data['actor_id'],gap.workspace_id,'viewer','data-gap-receiver')
        gap.status='received';gap.version+=1
        gap.data={**gap.data,'received_at':now().isoformat(),'receiver':'perception'}
        record(db,p,gap,'received',reason='感知已接收原查询缺口；尚未决定设备动作')
        origin=scoped(db,DataRun,gap.query_id,p)
        if origin.data.get('native_goal_task_id'):
            from .native_goals import queue as goal_queue
            from .models import Task
            goal_queue(db,p,scoped(db,Task,origin.data['native_goal_task_id'],p),'received:'+gap.id)
        elif gap.data.get('workspace_existing_check') or 'workspace_need_context' in origin.data:
            from .workspace_needs import queue as need_queue
            need_queue(db,p,origin,'received:'+gap.id)
        else:
            queue(db,gap.workspace_id,'route_data_gap','gap-route:'+gap.id,
                {'gap_id':gap.id,'actor_id':gap.data['actor_id']})
    return True


def changed(db,p,entity_id,trigger):
    from .original_acquisition_reception_v127 import trigger_hold
    if trigger_hold(db,p,trigger,entity_id=entity_id):return
    ids=db.scalars(select(DataGap.id).where(DataGap.workspace_id==p.workspace_id,
        DataGap.entity_id==entity_id,DataGap.status.not_in(TERMINAL))).all()
    for identifier in ids:
        gap=db.get(DataGap,identifier)
        from .contracts import digest
        run=scoped(db,DataRun,gap.query_id,p)
        if run.data.get('native_goal_task_id'):
            continue # Native goal callback below coalesces a single parent-scope check.
        if gap.data.get('workspace_existing_check') or 'workspace_need_context' in run.data:
            from .workspace_needs import queue as need_queue
            from .security import Principal
            owner=Principal(gap.data['actor_id'],p.workspace_id,p.role,'workspace-existing-check')
            need_queue(db,owner,run,'changed:'+digest(trigger)[:24])
        else:
            queue(db,p.workspace_id,'recheck_data_gap','gap-check:'+identifier+':'+digest(trigger)[:24],
                {'gap_id':identifier,'actor_id':gap.data['actor_id'],'trigger':trigger})

    from .native_goals import source_changed
    source_changed(db,p,entity_id,trigger)


def workspace_memory_kwargs(db,p,gap):
    """A saved need can shrink Memory access, never promote another package."""
    if 'workspace_existing_check' in gap.data:
        from .workspace_needs import guard as need_guard,scoped_gap,memory_kwargs
        from .models import InstanceRelease
        original=scoped(db,DataRun,gap.query_id,p)
        c,_,entity=need_guard(db,p,original);scoped_gap(db,p,original,gap.id)
        release=scoped(db,InstanceRelease,c['release_id'],p)
        return memory_kwargs(entity,release)
    return {}

def evaluate(db,p,gap,query_id,existing_only=False):
    from .agents import data_agent
    # Fixed original requirement/window, latest alone advances as defined by the query contract.
    return data_agent(db,p,gap.data['request'],query_id,cached_only=existing_only,emit_gaps=False,learning=False,**workspace_memory_kwargs(db,p,gap))


def receipts_ready(db,p,state):
    for item in state.get('result',{}).get('observation_evidence',[]):
        if not db.scalar(select(Event.id).where(Event.workspace_id==p.workspace_id,
            Event.kind=='observation.data',Event.idempotency_key=='data:'+item['observation_id'],Event.status=='received')):
            return False
    return True


def locked_gap(db,p,identifier):
    """Native task → gap ordering matches native stop/check; legacy ordering stays intact."""
    gap=scoped(db,DataGap,identifier,p);run=scoped(db,DataRun,gap.query_id,p)
    if run.data.get('native_goal_task_id'):
        from .models import Task
        parent=scoped(db,Task,run.data['native_goal_task_id'],p,True)
        if parent.data.get('native_goal_query_id')!=run.id:raise HTTPException(409,'原业务资料缺口与任务关联改变')
    return scoped(db,DataGap,identifier,p,True)


def recheck(db,p,gap_id,trigger='manual',existing_only=False):
    gap=locked_gap(db,p,gap_id)
    if gap.status in TERMINAL:return gap
    original=scoped(db,DataRun,gap.query_id,p)
    if gap.data.get('workspace_existing_check') or 'workspace_need_context' in original.data:
        from .workspace_needs import guard as need_guard,scoped_gap
        need_guard(db,p,original);scoped_gap(db,p,original,gap.id);existing_only=True
    from .goal_queries import guard,completed
    try:guard(db,p,original)
    except HTTPException as error:
        gap.status='cancelled';gap.version+=1;gap.data={**gap.data,'decision_reason':str(error.detail)}
        record(db,p,gap,'cancelled',reason=str(error.detail));return gap
    reuse=None
    if original.data.get('goal_delegation'):
        from .models import Task
        from .goals import query_goal
        task=scoped(db,Task,original.data['goal_delegation']['task_id'],p)
        identifier=task.data.get('data_query_run_id')
        if identifier and identifier!=original.id and not gap.data.get('pending_recheck_query_id'):
            candidate=scoped(db,DataRun,identifier,p)
            current=query_goal(db,p,task,learning=False)
            if candidate.data['request']==gap.data['request'] and current['status'] in {'sufficient','needs_review'}:
                reuse=candidate
    if reuse:child=reuse
    elif gap.data.get('pending_recheck_query_id'):
        child=scoped(db,DataRun,gap.data['pending_recheck_query_id'],p,True)
    else:
        child=DataRun(workspace_id=p.workspace_id,owner_id=p.user_id,question=original.question,
            status='queued',data={'request':gap.data['request'],'parent_query_id':original.id,
                **({'goal_delegation':original.data['goal_delegation']} if original.data.get('goal_delegation') else {}),
                'gap_recheck':{'gap_id':gap.id,'trigger':trigger,'source_version':gap.version}})
        db.add(child);db.flush();gap.data={**gap.data,'pending_recheck_query_id':child.id}
        from .leases import checkpoint
        checkpoint(db)
    state={'status':child.status,'result':child.data['result']} if reuse else evaluate(db,p,gap,child.id,existing_only=existing_only)
    if not reuse:
        child=scoped(db,DataRun,child.id,p,True);child.status=state['status'];child.data={**child.data,**state}
    gap=locked_gap(db,p,gap.id)
    if gap.status in TERMINAL:return gap
    satisfied=state['status']=='sufficient' and receipts_ready(db,p,state)
    if state['status']!='sufficient' or satisfied:completed(db,p,child)
    gap.data={**gap.data,'latest_recheck_query_id':child.id,'last_checked_at':now().isoformat(),
        'latest_result_status':state['status'],'pending_recheck_query_id':None}
    if satisfied:
        gap.status='resolved';gap.data={**gap.data,'resolved_at':now().isoformat(),
            'resolution_reason':'原需求证据充分、无冲突，所用观测的数据归集回执已确认'}
    elif state['status']=='sufficient':gap.status='awaiting_collection'
    elif state['status']=='conflict':gap.status='needs_review'
    elif gap.status=='verifying':
        previous=gap.data.get('recheck_resume_status')
        gap.status=previous if previous and previous not in TERMINAL|{'verifying'} else 'unresolved'
    elif gap.status=='received':gap.status='unresolved'
    requirement=gap.data['request'].get('evidence_requirement')
    if not satisfied and state['status']=='insufficient' and requirement and requirement['source']!='archive' and (
        requirement['minimum_verification']!='unverified_extraction' and requirement['time']['mode'] in {'archive','latest'}):
        from .services import query_evidence
        reviewable=query_evidence(db,p,[gap.entity_id],gap.data['missing_fields'],learning=False,
            requirement={**requirement,'source':'observations','minimum_verification':'unverified_extraction'},
            fixed_window=gap.data['request'].get('resolved_time'),**workspace_memory_kwargs(db,p,gap))
        if reviewable['status']=='sufficient':
            gap.status='needs_review';gap.data={**gap.data,'decision_reason':
                '已有足够适时观测但确认等级不足；先核查现有证据，不新增设备动作'}
    gap.version+=1
    record(db,p,gap,'verified' if satisfied else 'verification_failed',query_id=child.id,
        result_status=state['status'],reason=gap.data.get('resolution_reason') if satisfied else
        '原需求仍不足、冲突或未取得所用观测的归集回执；补采成功不能代替证据充分')
    return gap


def route(db,p,gap_id):
    gap=locked_gap(db,p,gap_id)
    if gap.status in TERMINAL:return 'cancelled'
    original=scoped(db,DataRun,gap.query_id,p)
    if gap.data.get('workspace_existing_check') or 'workspace_need_context' in original.data:
        from .workspace_needs import process,context
        from .contracts import digest
        job=Job(data={'query_id':original.id,'context_hash':digest(context(original)),'existing_records_only':True})
        process(db,p,job);return 'succeeded'
    gap=recheck(db,p,gap.id,'before_perception_decision')
    if gap.status=='cancelled':return 'cancelled'
    if gap.status in {'resolved','awaiting_collection','needs_review'}:
        if gap.status=='needs_review':
            enqueue_event(db,p,'data.gap_coordination','gap-coordination:'+gap.id,
                {'gap_id':gap.id,'entity_id':gap.entity_id,'query_id':gap.query_id,'actor_id':gap.data['actor_id']})
        return 'succeeded'
    requirement=gap.data['request'].get('evidence_requirement')
    original=scoped(db,DataRun,gap.query_id,p)
    delegation=original.data.get('goal_delegation')
    parent=None
    if delegation:
        from .models import Task
        parent=scoped(db,Task,delegation['task_id'],p)
    if parent and parent.data['goal_spec']['observation_mode']=='existing_only':
        status,reason='needs_existing_evidence','原业务目标只允许既有证据；缺口送业务核查，监测权限不授予本目标新设备动作'
    elif not requirement or requirement['source']=='archive':
        status,reason='needs_record','需要档案责任人补充有效记录；新设备观测不能代替指定档案'
    elif requirement['time']['mode'] not in {'archive','latest'}:
        status,reason='needs_historical_evidence','原需求指定历史或固定日期窗；新观测不能证明过去状态，请核查历史来源'
    else:
        from .perception import propose_acquisitions, object_state, authorized_monitor
        policies=db.scalars(select(MonitorPolicy).where(MonitorPolicy.workspace_id==p.workspace_id,
            MonitorPolicy.status=='active').order_by(MonitorPolicy.created_at,MonitorPolicy.id)).all()
        suitable=[]
        for policy in policies:
            if gap.entity_id not in policy.data['entity_ids'] or not set(gap.data['missing_fields']).issubset(policy.data['fields']):continue
            try:authorized_monitor(db,p,policy)
            except (ValueError,HTTPException):continue
            suitable.append(policy)
        if not suitable:status,reason='needs_authorization','没有仍有效且覆盖此对象与字段的管理员监测授权；未下发设备'
        else:
            policy=suitable[0]
            state=object_state(db,p,gap.entity_id,gap.data['missing_fields'],
                requirement['time'].get('max_age_seconds') or policy.data['max_age_seconds'])
            if not state['gaps'] and not policy.data.get('selection_requirement'):
                status,reason='needs_review','已有适时观测，但原需求的确认等级、样本量或来源约束仍未满足；先核查现有证据'
            else:
                from .observation_requests import pin
                origin=pin(db,p,gap) if policy.data.get('selection_requirement') else None
                ids=propose_acquisitions(db,p,policy.id,entity_ids=[gap.entity_id],observation_request=origin)
                active=db.scalars(select(Acquisition).where(Acquisition.workspace_id==p.workspace_id,
                    Acquisition.entity_id==gap.entity_id,Acquisition.data['policy_id'].as_string()==policy.id,
                    *([Acquisition.data['data_gap_id'].as_string()==gap.id] if origin else []),
                    ((Acquisition.status.in_(['authorized','dispatching','unknown','awaiting_observation'])) |
                     (Acquisition.status.in_(['blocked_connector','blocked_resource']) &
                      (Acquisition.created_at>now()-timedelta(seconds=policy.data['cooldown_seconds'])))))).all()
                ids=list(dict.fromkeys(ids+[a.id for a in active]))
                rows=[scoped(db,Acquisition,x,p) for x in ids]
                status='awaiting_observation' if any(a.status in {'authorized','dispatching','awaiting_observation'} for a in rows) else (
                    'unknown_action' if any(a.status=='unknown' for a in rows) else 'blocked_connector' if rows else 'needs_coordination')
                reason={'awaiting_observation':'感知按已批准策略创建或复用补采，等待实际观测及原需求复核',
                    'unknown_action':'已有动作结果未知，禁止重发；先核对设备回执',
                    'blocked_connector':'授权来源没有已接入控制接口，不能自动补采',
                    'needs_coordination':'策略冷却、次数或未满足的感知条件阻止新补采，需要协调'}[status]
                gap.data={**gap.data,'policy_id':policy.id,'policy_version':policy.version,'acquisition_ids':ids}
                if policy.data.get('selection_requirement'):
                    from .models import ObservationPlan
                    plan=db.scalar(select(ObservationPlan).where(ObservationPlan.workspace_id==p.workspace_id,
                        ObservationPlan.policy_id==policy.id,ObservationPlan.entity_id==gap.entity_id,
                        ObservationPlan.data['observation_request']['gap_id'].as_string()==gap.id).order_by(ObservationPlan.created_at.desc()).limit(1))
                    if plan:
                        gap.data={**gap.data,'observation_plan_id':plan.id,'selection_status':plan.status}
                        if plan.status=='escalated':
                            status='unknown_action' if plan.data.get('escalation_code')=='unknown_action' else 'needs_coordination'
                            reason=plan.data.get('reason',reason)
    gap.status=status;gap.version+=1;gap.data={**gap.data,'decision_reason':reason,'decided_at':now().isoformat()}
    record(db,p,gap,'perception_decision',reason=reason,acquisition_ids=gap.data.get('acquisition_ids',[]))
    if status not in {'awaiting_observation'}:
        enqueue_event(db,p,'data.gap_coordination','gap-coordination:'+gap.id,
            {'gap_id':gap.id,'entity_id':gap.entity_id,'query_id':gap.query_id,'actor_id':gap.data['actor_id']})
    return 'succeeded'


def coordinate(db,event):
    """One durable business investigation, no new action authorization or goal completion."""
    gap=db.scalar(select(DataGap).where(DataGap.id==event.data['gap_id'],DataGap.workspace_id==event.workspace_id))
    if not gap:return False
    from .original_acquisition_coordination_v127 import prepare
    original=prepare(db,event)
    if original.receipt is not None:return original.receipt
    return _coordinate(db,event,original.gap)


def _coordinate(db,event,gap):
    """Only callers holding the actual original coordination locks enter here."""
    from .models import Entity,Task
    from .security import Principal
    if gap.status in TERMINAL:return True
    if not gap.data.get('business_task_id'):
        entity=db.get(Entity,gap.entity_id);original=db.get(DataRun,gap.query_id)
        delegation=original.data.get('goal_delegation')
        if delegation:
            task=db.get(Task,delegation['task_id'])
            if task and task.workspace_id==gap.workspace_id:
                gap.version+=1;gap.data={**gap.data,'business_task_id':task.id,'business_received_at':now().isoformat()}
                from .security import Principal
                record(db,Principal(gap.data['actor_id'],gap.workspace_id,'viewer','business-gap-receiver'),gap,'coordinated',
                    reason='补证协调回执送达原业务中枢，未授予新目标或设备动作')
                return True
        task=Task(workspace_id=gap.workspace_id,title='协调补证：'+str(entity.data.get('name') or entity.data.get('serial') or entity.external_id),
            package_id=entity.schema_capability_id,owner_id=gap.data['actor_id'],status='draft',data={
                'trigger':'data.gap_coordination','event_id':event.id,'data_gap_id':gap.id,
                'entity_ids':[gap.entity_id],'fields':gap.data['missing_fields'],
                'parent_query_id':gap.query_id,'original_question':original.question,
                'coordination_reason':gap.data.get('decision_reason'),'evidence_requirement':gap.data['request'].get('evidence_requirement')})
        db.add(task);db.flush();gap.version+=1;gap.data={**gap.data,'business_task_id':task.id,'business_received_at':now().isoformat()}
        p=Principal(gap.data['actor_id'],gap.workspace_id,'viewer','business-gap-receiver')
        record(db,p,gap,'coordinated',reason='已送业务中枢协调，未授予新设备动作或确认业务目标完成')
    return True
