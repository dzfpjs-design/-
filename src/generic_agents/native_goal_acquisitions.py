"""Original native-goal acquisition evidence; receipt reads never dispatch commands."""
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .models import DataRun,DataGap,ObservationPlan,Acquisition,SensorAsset,Capability,SensorReservation,DeviceReservation,Observation,Event,now
from .services import scoped,check_version,audit
from .goals import snapshot
from .temporal import instant

LIMIT=100

def original(db,p,task,plan):
    c=task.data.get('native_goal_context');query_id=task.data.get('native_goal_query_id')
    if not c or digest(c)!=task.data.get('native_goal_context_hash') or c.get('goal_snapshot')!=snapshot(task):raise HTTPException(409,'原目标补采上下文无法核对')
    origin=plan.data.get('observation_request')
    if not origin or origin.get('query_id')!=query_id:raise HTTPException(404,'补采记录不属于当前原目标')
    run=scoped(db,DataRun,query_id,p);gap=scoped(db,DataGap,origin['gap_id'],p)
    expected={'task_id':task.id,'goal_snapshot':snapshot(task)}
    if run.data.get('native_goal_task_id')!=task.id or run.data.get('native_goal_context_hash')!=digest(c) or run.data.get('goal_delegation')!=expected or origin.get('goal_delegation')!=expected:raise HTTPException(409,'原补采业务委托关联已改变')
    if digest(run.data['request'])!=run.data.get('native_goal_request_hash') or origin.get('query_request_hash')!=digest(run.data['request']) or origin.get('request')!=gap.data['request'] or gap.query_id!=run.id:raise HTTPException(409,'原补采资料要求摘要或缺口关联已改变')
    if plan.entity_id!=gap.entity_id or origin.get('entity_id')!=gap.entity_id or gap.entity_id not in task.data['entity_ids'] or not origin.get('fields') or not set(origin['fields']).issubset(task.data['goal_spec']['required_fields']):raise HTTPException(409,'原补采对象或字段无法核对')
    return origin

def acquisition(db,p,task,identifier):
    row=scoped(db,Acquisition,identifier,p)
    if not row.data.get('observation_plan_id'):raise HTTPException(404,'此采集没有当前原目标的计划关联')
    plan=scoped(db,ObservationPlan,row.data['observation_plan_id'],p);origin=original(db,p,task,plan)
    if row.entity_id!=plan.entity_id or row.data.get('policy_id')!=plan.policy_id or not row.data.get('selected_fields') or not set(row.data['selected_fields']).issubset(origin['fields']):raise HTTPException(409,'原采集对象、监测策略或字段关联改变')
    return row,plan

def ended(task):
    spec=task.data.get('goal_spec') or {}
    return bool(task.data.get('native_goal_cancelled') or task.data.get('native_goal_timed_out') or task.data.get('native_goal_acceptance') or task.status=='workflow_cancelled' or spec.get('deadline') and now()>=instant(spec['deadline']))

def records(db,p,task,acq):
    from .observation_integrity import checked_report,CHECK_ERRORS
    rows=list(db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,Observation.data['acquisition_id'].as_string()==acq.id).order_by(Observation.created_at.desc()).limit(11)))
    values=[]
    for obs in rows[:10]:
        item={'id':obs.id,'version':obs.version,'status':obs.status,'observed_at':obs.observed_at.isoformat() if obs.observed_at else None,'can_verify':False}
        try:
            if obs.entity_id!=acq.entity_id or obs.source_id!=acq.capability_id:raise ValueError('原采集与资料的对象/来源不一致')
            report=checked_report(db,p,obs)
            if obs.data.get('verification_history_hash') and digest(obs.data.get('verification_history',[]))!=obs.data['verification_history_hash']:raise ValueError('原核查历史摘要不一致')
            fields=acq.data['selected_fields'];original=obs.data.get('original_request',{}).get('attributes')
            item.update(review=report['review'],original_values={k:original.get(k) for k in fields} if original is not None else None,extracted_values={k:report['attributes'].get(k) for k in fields},
                input_hash=digest({'extracted':obs.data['extracted'],'evidence_ids':obs.data.get('evidence_ids',[]),'invocation_id':obs.data['invocation_id']}),
                saved_review=obs.data.get('human_verification') if report['review'] in {'confirmed','rejected'} else None,
                can_verify=bool((p.role=='admin' or p.role=='operator' and task.owner_id==p.user_id) and original is not None and not obs.data.get('native_packet')))
        except CHECK_ERRORS:item['problem']='原处理尚未完成或原件/核查摘要不能核实；原记录保留，不能直接确认'
        values.append(item)
    return {'items':values,'truncated':len(rows)>10}

def summary(db,p,task,row,plan):
    asset=scoped(db,SensorAsset,row.data['sensor_asset_id'],p);source=scoped(db,Capability,row.capability_id,p)
    reservations=db.scalars(select(SensorReservation).where(SensorReservation.workspace_id==p.workspace_id,SensorReservation.asset_id==asset.id)).all()
    device_rows=db.scalars(select(DeviceReservation).where(DeviceReservation.workspace_id==p.workspace_id,DeviceReservation.acquisition_id==row.id)).all()
    held=any(r.acquisition_id==row.id for r in reservations) or bool(device_rows)
    receipt=row.data.get('reconciled_receipt') or row.data.get('receipt') or {}
    proof={k:receipt[k] for k in ['receipt_id','status','terminal','no_future_device_activity','no_future_observation'] if k in receipt}
    can_manage=p.role=='admin' or p.role=='operator' and task.owner_id==p.user_id
    return {'id':row.id,'version':row.version,'plan_id':plan.id,'entity_id':row.entity_id,'status':row.status,'plan_status':plan.status,
        'asset_title':asset.title,'source_name':source.data.get('description') or source.name,'required_fields':row.data.get('selected_fields',[]),
        'action':row.data.get('selected_action'),'created_at':row.created_at.isoformat(),'dispatched_at':row.data.get('dispatch_started_at'),
        'deadline':plan.data['deadline'],'device_action_terminal':row.data.get('device_action_terminal') is True,'reservation_held':held,
        'receipt':proof,'receipt_checked_at':row.data.get('reconciled_at'),'closing_observation_id':row.data.get('closing_observation_id'),
        'reason':('原采集已核查结案，设备占用已释放。' if row.status=='succeeded' and row.data.get('closing_observation_id') and not held
            else str(row.data.get('reason') or plan.data.get('reason') or '')[:1000]),
        'observations':records(db,p,task,row),'can_reconcile':bool(can_manage and row.status in {'unknown','dispatching','awaiting_observation'}),
        'parent_ended':ended(task),'physical_state_verified':False,'parent_goal_completed':bool(task.data.get('native_goal_acceptance'))}

def view(db,p,task):
    query_id=task.data.get('native_goal_query_id')
    if not query_id:return {'items':[],'truncated':False,'parent_ended':ended(task)}
    plan_ids=select(ObservationPlan.id).where(ObservationPlan.workspace_id==p.workspace_id,ObservationPlan.data['observation_request']['query_id'].as_string()==query_id)
    rows=list(db.scalars(select(Acquisition).where(Acquisition.workspace_id==p.workspace_id,Acquisition.data['observation_plan_id'].as_string().in_(plan_ids)).order_by(Acquisition.created_at,Acquisition.id).limit(LIMIT+1)))
    values=[]
    for row in rows[:LIMIT]:
        row,plan=acquisition(db,p,task,row.id);values.append(summary(db,p,task,row,plan))
    return {'items':values,'truncated':len(rows)>LIMIT,'parent_ended':ended(task)}

def stop_coordination(db,p,task,reason):
    """End original data-gap coordination, retaining every in-flight request and hold."""
    identifier=task.data.get('native_goal_query_id')
    if not identifier:return
    run=scoped(db,DataRun,identifier,p)
    if run.data.get('native_goal_task_id')!=task.id or run.data.get('native_goal_context_hash')!=digest(task.data['native_goal_context']) or digest(run.data['request'])!=run.data.get('native_goal_request_hash'):raise HTTPException(409,'原目标资料委托无法核对，未改动采集请求')
    from .data_gaps import record
    rows=db.scalars(select(DataGap).where(DataGap.workspace_id==p.workspace_id,DataGap.query_id==run.id).with_for_update()).all()
    for gap in rows:
        if gap.entity_id not in task.data['entity_ids']:raise HTTPException(409,'原缺口不属于目标对象')
        if gap.status in {'resolved','cancelled'}:continue
        gap.status='cancelled';gap.version+=1;gap.data={**gap.data,'decision_reason':reason+'；只保留原采集回执和迟到资料，不重发或取消真实设备动作'}
        record(db,p,gap,'cancelled',reason=gap.data['decision_reason'],original_task_id=task.id)

def reconcile(db,p,task,identifier,body):
    from .native_goals import manage
    manage(p,task);row,plan=acquisition(db,p,task,identifier)
    # A completed result is read without another supplier query; CAS protects live requests.
    if row.status not in {'unknown','dispatching','awaiting_observation'}:return summary(db,p,task,row,plan)
    check_version(row,body.expected_version)
    if row.status=='awaiting_observation':
        local=_received_data_settlement(db,p,task,row,plan,body)
        if local is not None:return local
    from .perception import reconcile_acquisition
    row=reconcile_acquisition(db,p,row.id)
    audit(db,p,'native_goal.receipt_read',row.id,{'task_id':task.id,'reason':body.reason,'new_device_command':False})
    return summary(db,p,task,row,plan)


def _received_data_settlement(db,p,task,row,plan,body):
    """Recover this original ACK only; never replay intake or supplier HTTP.

    Old deployed receivers may have committed a truthful ACK without the new
    local queue. The actual current requester keeps their own identity; the
    saved acquisition actor remains the ordinary background execution owner.
    """
    from .relations import lock_current_actor
    from . import instance_data_receipts as receipts
    from . import acquisition_ack_settlement_v245 as settlement
    rows=list(db.execute(select(Observation,Event).join(Event,
        (Event.workspace_id==Observation.workspace_id)&(Event.kind=='observation.data')&
        (Event.idempotency_key=='data:'+Observation.id)).where(
            Observation.workspace_id==p.workspace_id,Observation.data['acquisition_id'].as_string()==row.id,
            Observation.status=='processed',Event.status=='received')
        .order_by(Observation.observed_at.desc(),Observation.id).limit(21)))
    settlement.need(len(rows)<=20)
    for observation,event in rows:
        # A received flag without the original Memory/receipt is not permission
        # to fall back to HTTP or repair history. The existing verifier fails.
        proof=receipts.observation_receipt(db,p,observation).get('data_reception')
        if proof is None or proof.get('schema')!=receipts.SCHEMA:continue
        # V127's independently recorded reducing-only reception still permits
        # reading the original supplier's terminal receipt. It cannot use the
        # ordinary ACK queue and must not be mistaken for a new execution grant.
        from .original_acquisition_reception_v127 import context,hold,MARKER,HOLD_SCHEMA
        derivation=event.data.get('derivation_receipt')
        if MARKER in observation.data or type(derivation) is dict and any(
                key in derivation for key in ('original_reception_restriction_hash','original_reception_proof')):
            original=context(db,p,observation);restriction=hold(db,p,observation)
            settlement.need(original is not None and restriction is not None
                and original.task.id==task.id and original.acquisition.id==row.id and original.plan.id==plan.id
                and original.entity.id==observation.entity_id==row.entity_id
                and restriction['schema']==HOLD_SCHEMA and digest(restriction['proof'])==digest(original.proof)
                and type(derivation) is dict and derivation.get('schema')=='observation-derivation-skipped-v1'
                and derivation.get('event_id')==event.id and derivation.get('observation_id')==observation.id
                and derivation.get('original_reception_restriction_hash')==restriction['restriction_hash']
                and digest(derivation.get('original_reception_proof'))==digest(original.proof))
            continue
        # No added row lock reaches the existing HTTP fallback when no ACK is
        # available. Only this queue-only branch takes Actor -> Acquisition.
        lock_current_actor(db,p)
        row=scoped(db,Acquisition,row.id,p,True);check_version(row,body.expected_version)
        if row.status!='awaiting_observation':return summary(db,p,task,row,plan)
        job=settlement.enqueue(db,p,event,observation)
        audit(db,p,'native_goal.data_ack_settlement_requested_v245',row.id,{
            'task_id':task.id,'observation_id':observation.id,'event_id':event.id,
            'job_id':job.id if job else None,'request':body.model_dump(),'request_hash':digest(body.model_dump()),
            'device_commands':0,'replayed_data_event':False})
        value=summary(db,p,task,row,plan)
        value['reason']=('原资料已独立收件；原补采本地核验任务状态：'+job.status+'，未重新采集。' if job else
            '原资料已独立收件，但当前保持原接收限制；未自动恢复补采或发送设备请求。')
        return value
    return None
