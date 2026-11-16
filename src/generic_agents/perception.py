"""Evidence-derived object states and bounded sensor acquisition."""
import os
from datetime import timedelta, datetime
from zoneinfo import ZoneInfo
import httpx
from fastapi import HTTPException
from sqlalchemy import select, or_
from sqlalchemy.dialects.postgresql import insert
from .models import (Entity, Observation, ObjectState, MonitorPolicy, Acquisition, SourceReservation,
                     Capability, Job, User, Membership, now)
from .services import scoped, published, audit, check_version
from .contracts import digest
from .security import require
from .gateway import validate_endpoint


def object_state(db, p, entity_id, required_fields=None, max_age_seconds=86400):
    from .observation_integrity import checked_report, CHECK_ERRORS
    from .state_receipt_guard_v245 import check as state_receipt
    from .mqtt_unit_evidence_v342 import KEY as MQTT_UNIT_KEY
    entity = scoped(db, Entity, entity_id, p)
    # Serialize concurrent first/read evaluations without replacing another reader's snapshot.
    db.execute(insert(ObjectState).values(workspace_id=p.workspace_id, entity_id=entity.id, data={})
        .on_conflict_do_nothing(index_elements=['workspace_id', 'entity_id']))
    snapshot = db.scalar(select(ObjectState).where(ObjectState.workspace_id == p.workspace_id,
        ObjectState.entity_id == entity.id).with_for_update())
    checked_at = now()
    observations = db.scalars(select(Observation).where(Observation.workspace_id == p.workspace_id,
        Observation.entity_id == entity.id, Observation.status == "processed", Observation.observed_at <= checked_at,
        Observation.data['candidate_only'].as_boolean().is_distinct_from(True))
        .order_by(Observation.observed_at.desc(), Observation.id).limit(201)).all()
    truncated = len(observations) > 200
    observations = observations[:200]
    unclocked = db.scalars(select(Observation).where(Observation.workspace_id == p.workspace_id,
        Observation.entity_id == entity.id, or_(Observation.status == 'extracted_unclocked',
            (Observation.status == 'processed') & Observation.observed_at.is_(None)))
        .order_by(Observation.created_at.desc()).limit(21)).all()
    fields, conflicts, evidence_ids, excluded = {}, [], [], []
    superseded_records=[]
    from .observation_corrections import superseded
    for observation in observations:
        replacement=superseded(db,p,observation)
        if replacement:
            superseded_records.append({'observation_id':observation.id,'correction_observation_id':replacement['correction_observation_id'],'message':'已由同一原输入的后续更正代替；原登记与核查保留'})
            continue
        try:
            report = checked_report(db, p, observation)
        except CHECK_ERRORS:
            excluded.append({'observation_id': observation.id, 'source_id': observation.source_id,
                'reason': 'integrity_failed', 'reported_at': observation.observed_at.isoformat(),
                'message': '原登记、提取调用或文件依据未通过核对，未采用其中的数值'})
            continue
        if report['review'] == 'rejected':
            excluded.append({'observation_id': observation.id, 'source_id': observation.source_id,
                'reason': 'human_rejected', 'reported_at': observation.observed_at.isoformat(),
                'message': '原提取结果已被人工拒绝，未采用其中的数值'})
            continue
        try:
            reception = state_receipt(db, p, observation)
        except CHECK_ERRORS:
            excluded.append({'observation_id': observation.id, 'source_id': observation.source_id,
                'reason': 'data_reception_integrity_failed', 'status': 'blocked_integrity',
                'reported_at': observation.observed_at.isoformat(),
                'message': '独立收件依据未通过核对，未采用其中的数值；原登记与处理记录保留'})
            continue
        if reception is not None and reception['status'] == 'waiting_data':
            excluded.append({'observation_id': observation.id, 'source_id': observation.source_id,
                'reason': 'awaiting_data_reception', 'status': 'pending_data_reception',
                'reported_at': observation.observed_at.isoformat(),
                'message': ('独立收件受阻，未采用其中的数值；请核对原接收记录'
                    if reception['delivery_status'] == 'blocked_delivery' else
                    '提取结果尚未完成独立收件，未采用其中的数值')})
            continue
        evidence_ids.append(observation.id)
        extracted = report['extracted']
        uncertainty = extracted.get('uncertainty', {})
        if not isinstance(uncertainty, dict): uncertainty = {}
        for field, value in report['attributes'].items():
            candidate = {'value': value, 'observation_id': observation.id, 'source_id': observation.source_id,
                         'source_name': report['source_name'], 'invocation_id': report['invocation_id']}
            unit_proof=observation.data.get(MQTT_UNIT_KEY) if observation.data.get('mqtt_receipt_id') else None
            unit=(unit_proof or {}).get('fields',{}).get(field)
            if unit is not None:candidate['measurement_unit']=dict(unit)
            if reception is not None:
                candidate.update(data_reception_verified=True,
                    data_reception_receipt_hash=reception['receipt_hash'])
            current = fields.get(field)
            if current is None:
                fields[field] = {'value': value, 'observed_at': observation.observed_at.isoformat(),
                    'observation_ids': [observation.id], 'source_ids': [observation.source_id],
                    'method': extracted.get('method', 'registered_extractor'),
                    'uncertainty': uncertainty.get(field, 'model_extraction_unverified'
                        if observation.data.get('model_receipt') else 'reported_measurement'),
                    'file_evidence_ids': observation.data.get('evidence_ids', []),
                    'integrity_verified': True, 'source_time_verified': False, 'physical_state_verified': False,
                    'basis': report['basis'], 'candidates': [candidate], 'conflicted': False}
                if unit is not None:fields[field]['measurement_unit']=dict(unit)
            elif current['observed_at'] == observation.observed_at.isoformat():
                current['observation_ids'].append(observation.id); current['source_ids'].append(observation.source_id)
                current['candidates'].append(candidate)
                unit_conflict=False
                if type(value) in {int,float} and any('measurement_unit' in c for c in current['candidates']):
                    declared=[(c.get('measurement_unit') or {}).get('unit') for c in current['candidates']]
                    # Missing or different units cannot prove equivalence,
                    # even when two raw numbers happen to be identical.
                    unit_conflict=any(u is None for u in declared) or len(set(declared))!=1
                    if unit_conflict:current['unit_conflicted']=True
                if unit_conflict or any(c['value'] != current['candidates'][0]['value'] for c in current['candidates']):
                    current['conflicted'] = True
                    current['value'] = None  # Never choose/average one of incompatible reports.
    for field, value in fields.items():
        if value['conflicted']:
            conflicts.append({'field': field, 'observation_ids': value['observation_ids'],
                              'reason': ('同一登记时刻的单位不同或未核对，不能直接比较，需核查原来源'
                                         if value.get('unit_conflicted') else '同一登记时刻的结果不一致，需核查原来源')})
    basis = 'caller_requested_fields' if required_fields is not None else 'available_report_fields'
    requested = list(dict.fromkeys(required_fields)) if required_fields is not None else list(fields)
    field_ages = {}
    if required_fields is None:
        policies = db.scalars(select(MonitorPolicy).where(MonitorPolicy.workspace_id == p.workspace_id,
            MonitorPolicy.status == 'active')).all()
        required = []
        for policy in policies:
            if entity.id not in policy.data.get('entity_ids', []): continue
            try: authorized_monitor(db, p, policy)
            except HTTPException: continue
            required.extend(policy.data['fields'])
            for field in policy.data['fields']:
                field_ages[field] = min(field_ages.get(field, policy.data['max_age_seconds']), policy.data['max_age_seconds'])
        if required:
            requested = list(dict.fromkeys(required)); basis = 'confirmed_monitor_policy'
    for field, value in fields.items():
        value['freshness_seconds'] = field_ages.get(field, max_age_seconds)
    gaps = []
    for field in requested:
        if field not in fields: reason = 'missing'
        elif fields[field]['conflicted']: reason = 'conflict'
        elif fields[field]['value'] is None: reason = 'missing_value'
        elif datetime.fromisoformat(fields[field]['observed_at']) < checked_at - timedelta(seconds=field_ages.get(field, max_age_seconds)): reason = 'stale'
        else: continue
        gaps.append({'field': field, 'reason': reason})
    if excluded and (not requested or any(field not in fields or any(
        e['reported_at'] >= fields[field]['observed_at'] for e in excluded) for field in requested)):
        gaps.append({'field': None, 'reason': 'excluded_evidence', 'message': '最新登记存在未采用的依据，不能据此关闭感知缺口'})
    if truncated:
        gaps.append({'field': None, 'reason': 'scan_limit', 'message': '已达到状态扫描上限，需按时间范围核查历史'})
    from .observation_inputs import incomplete
    inputs,corrected,inputs_truncated=incomplete(db,p,entity.id)
    if inputs:gaps.append({'field':None,'reason':'incomplete_input','message':'有待处理或失败的新输入；已保存字段不是这些输入的处理结果'})
    if inputs_truncated:gaps.append({'field':None,'reason':'input_scan_limit','message':'未完成输入超过显示上限，需要核对原处理清单'})
    value = {'corrected_inputs':corrected,'input_issues':inputs,'inputs_truncated':inputs_truncated,'entity_id': entity.id, 'fields': fields, 'gaps': gaps, 'conflicts': conflicts,
        'status': 'conflict' if conflicts else 'insufficient' if gaps or not fields else 'observed',
        'physical_state': 'unknown', 'current_state_verified': False,
        'requirements_basis': basis, 'required_fields': requested,
        'requirements_defined': basis != 'available_report_fields', 'field_freshness_seconds': field_ages,
        'excluded_records': excluded,
        'unclocked_candidates': [{'observation_id': o.id, 'source_id': o.source_id,
            'source_clock_utc': 'unknown', 'not_authoritative': True,
            'message': '采集时间未知，候选请在原处理记录核查；未用于对象状态'} for o in unclocked[:20]],
        'candidates_truncated': len(unclocked) > 20, 'truncated': truncated,
        'evidence_ids': evidence_ids, 'freshness_seconds': max_age_seconds,
        'authoritative_resource_version': entity.version, 'rule_version': 'reported-state-v6'}
    if superseded_records:value['superseded_records']=superseded_records
    state_hash = digest(value)
    damaged = bool(snapshot.data) and digest({k:v for k,v in snapshot.data.items() if k not in {'state_hash','evaluated_at','history','history_truncated'}}) != snapshot.data.get('state_hash')
    if damaged:
        audit(db,p,'object_state.cache_integrity_failed',entity.id,{'rejected_snapshot':snapshot.data,'snapshot_hash':digest(snapshot.data)})
    if not snapshot.data:
        snapshot.data = {**value, 'state_hash': state_hash, 'evaluated_at': checked_at.isoformat(), 'history': []}
    elif snapshot.data.get('state_hash') != state_hash or damaged:
        history = [*snapshot.data.get('history', []), {'version': snapshot.version,
            'state_hash': snapshot.data.get('state_hash') or digest(snapshot.data),
            'evaluated_at': snapshot.data.get('evaluated_at'), 'status': snapshot.data.get('status'),
            'fields': {} if damaged else snapshot.data.get('fields', {}), 'gaps': [] if damaged else snapshot.data.get('gaps', []),
            **({'integrity_failed':True,'status':'blocked_integrity','message':'原状态缓存未通过摘要核对；已重新核对原登记，未采用原缓存值'} if damaged else {})}]
        snapshot.version += 1
        snapshot.data = {**value, 'state_hash': state_hash, 'evaluated_at': checked_at.isoformat(),
            'history': history[-10:], 'history_truncated': len(history) > 10 or snapshot.data.get('history_truncated', False)}
    return {**value, 'state_hash':snapshot.data['state_hash'],'evaluated_at':snapshot.data['evaluated_at'],
        'history':snapshot.data.get('history',[]),'history_truncated':snapshot.data.get('history_truncated',False),
        'state_version': snapshot.version, 'checked_at': checked_at.isoformat()}


def approve_monitor(db, p, policy_id, expected, reason):
    require(p, "admin")
    policy = scoped(db, MonitorPolicy, policy_id, p, True); check_version(policy, expected)
    if policy.status != "review":
        raise HTTPException(409, "监测策略已处理")
    for entity_id in policy.data["entity_ids"]:
        scoped(db, Entity, entity_id, p)
    for source_id in policy.data["source_ids"]:
        published(db, p, source_id, "source")
    from .sensing_selection import validate_policy
    validate_policy(db,p,policy.data)
    policy.status = "active"; policy.approved_by = p.user_id; policy.version += 1
    policy.data = {**policy.data, "approved_hash": digest(policy.data), "approval_reason": reason}
    audit(db, p, "monitor.approve", policy.id); return policy


def authorized_monitor(db,p,policy):
    if policy.data.get("selection_requirement") and not policy.data.get("device_scope"):
        raise HTTPException(409,"旧选择策略缺少统一设备范围快照，请重新核查并批准新策略")
    original = {k:v for k,v in policy.data.items() if k not in {"approved_hash", "approval_reason"}}
    if policy.status != "active" or digest(original) != policy.data.get("approved_hash"):
        raise HTTPException(409, "监测授权已撤销或发生变化")
    if now() > datetime.fromisoformat(policy.data["valid_until"]):
        raise HTTPException(409, "监测授权已过期")
    allowed=db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(
        User.id==policy.approved_by,User.active.is_(True),Membership.workspace_id==p.workspace_id,
        Membership.role=='admin'))
    if not allowed:raise HTTPException(409,'监测批准人的管理员授权已撤销')


def propose_acquisitions(db, p, policy_id, *, entity_ids=None, observation_request=None):
    # Preserve original Task -> Gap -> Policy order for formal supplements.
    if observation_request:
        from .observation_requests import guard as request_guard
        request_guard(db,p,observation_request,dispatch=True)
    policy = scoped(db, MonitorPolicy, policy_id, p, True)
    authorized_monitor(db,p,policy)
    from .monitor_scheduling_service_v253 import require_proposal
    require_proposal(db,p,policy,observation_request)
    selected=policy.data['entity_ids'] if entity_ids is None else entity_ids
    if not set(selected).issubset(policy.data['entity_ids']):raise HTTPException(403,'补采对象不在策略范围')
    if policy.data.get("selection_requirement"):
        from .sensing_selection import propose,refresh
        if observation_request:
            from .goal_conflict_observation_service_v259 import adopted_plan
            once=adopted_plan(db,p,policy,observation_request)
            if once is not None:return refresh(db,p,policy,once)
        return propose(db,p,policy,selected,observation_request=observation_request)
    if len(policy.data["source_ids"])>1:raise HTTPException(409,"旧多来源策略缺少资产选择契约，请重新登记并批准")
    today = now().date()
    all_acquisitions = db.scalars(select(Acquisition).where(Acquisition.workspace_id == p.workspace_id)).all()
    counted = sum(1 for a in all_acquisitions if a.created_at.date() == today and a.data.get("policy_id") == policy.id)
    created = []
    for entity_id in selected:
        state = object_state(db, p, entity_id, policy.data["fields"], policy.data["max_age_seconds"])
        if not state["gaps"]:
            continue
        for source_id in policy.data["source_ids"]:
            if counted >= policy.data["max_acquisitions_per_day"]:
                break
            previous = [a for a in all_acquisitions if a.entity_id == entity_id and a.capability_id == source_id
                        and a.data.get("policy_id") == policy.id]
            if any(a.status in {"authorized", "dispatching", "unknown", "awaiting_observation"} for a in previous):
                continue
            if previous and max(a.created_at for a in previous) > now()-timedelta(seconds=policy.data["cooldown_seconds"]):
                continue
            source = published(db, p, source_id, "source")
            can_control = False  # No new legacy control without an explicit reviewed device identity.
            acq = Acquisition(workspace_id=p.workspace_id, entity_id=entity_id, capability_id=source_id,
                idempotency_key="monitor:"+policy.id+":"+entity_id+":"+source_id+":"+now().isoformat(),
                status="authorized" if can_control else "blocked_connector", data={"policy_id": policy.id,
                "gaps": state["gaps"], "actor_id": policy.approved_by, "policy_hash": policy.data["approved_hash"],
                "reason": "授权范围内发现感知不足" if can_control else "指定来源没有统一设备身份或已接入控制接口，请登记设备并迁移到选择策略"})
            db.add(acq); db.flush()
            from .monitor_scheduling_service_v253 import freeze_acquisition
            freeze_acquisition(db,p,policy,acq,observation_request)
            created.append(acq.id); counted += 1
            if can_control:
                db.add(Job(workspace_id=p.workspace_id, kind="dispatch_acquisition", idempotency_key="dispatch:"+acq.id,
                    data={"acquisition_id": acq.id, "actor_id": policy.approved_by}))
            audit(db, p, "perception.acquire_propose", acq.id, {"status": acq.status})
    return created


def _managed_source_failure_diagnostic(db, p, acquisition, exchange, failure):
    """Record code locations on the original ownership; never a send/retry proof."""
    from .leases import ownership
    from .worker_failure_diagnostics_v289 import capture_failure
    owner = ownership(db)
    diagnostic = capture_failure(failure)
    audit(db, p, 'acquisition.managed_source_failure_v291', acquisition.id, {
        'schema': 'acquisition-managed-source-failure-v291',
        'workspace_id': p.workspace_id, 'acquisition_id': acquisition.id,
        'exchange_id': exchange.id if exchange is not None else None,
        'job_id': owner.get('job_id'),
        'original_dispatch_job_id': exchange.dispatch_job_id if exchange is not None else None,
        'diagnostic': diagnostic, 'diagnostic_hash': digest(diagnostic)})


def dispatch_acquisition(db, p, acquisition_id):
    acq = scoped(db, Acquisition, acquisition_id, p)
    if acq.status in {"unknown", "dispatching", "awaiting_observation", "succeeded", "cancelled"}:
        return  # No replay after uncertain external side effects.
    if acq.status != "authorized":
        raise ValueError("补采请求尚未授权")
    from .goal_conflict_observation_service_v259 import lock_requester
    lock_requester(db,p,acq)
    from .observation_requests import lock_acquisition_origin
    lock_acquisition_origin(db,p,acq)
    policy = scoped(db, MonitorPolicy, acq.data["policy_id"], p, True)
    acq=scoped(db,Acquisition,acquisition_id,p,True)
    if acq.status!="authorized":return
    authorized_monitor(db,p,policy)
    from .monitor_scheduling_service_v253 import require_dispatch
    from .models import ObservationPlan
    origin=(scoped(db,ObservationPlan,acq.data['observation_plan_id'],p).data.get('observation_request')
        if acq.data.get('observation_plan_id') else None)
    try:require_dispatch(db,p,policy,acq,origin)
    except HTTPException as error:
        acq.status='blocked_connector';acq.version+=1;acq.data={**acq.data,'reason':str(error.detail)}
        audit(db,p,'monitor.scheduling_action_blocked_v253',acq.id,{'post_sent':False,'reason':str(error.detail)})
        return
    if policy.status != "active" or policy.data["approved_hash"] != acq.data["policy_hash"]:
        raise ValueError("监测策略已撤销")
    original = {k:v for k,v in policy.data.items() if k not in {"approved_hash", "approval_reason"}}
    if digest(original) != policy.data["approved_hash"] or now() > datetime.fromisoformat(policy.data["valid_until"]):
        raise ValueError("监测授权已变化或过期")
    source = published(db, p, acq.capability_id, "source")
    entity = scoped(db, Entity, acq.entity_id, p)
    execution = source.data["execution"]
    selected=bool(acq.data.get("observation_plan_id"));asset=None
    if selected:
        from .sensing_selection import pre_dispatch
        try:asset=pre_dispatch(db,p,policy,acq)
        except HTTPException as error:
            origin_stopped=False
            from .models import ObservationPlan
            origin=scoped(db,ObservationPlan,acq.data['observation_plan_id'],p).data.get('observation_request')
            if origin:
                from .observation_requests import guard
                try:guard(db,p,origin,dispatch=True)
                except HTTPException:origin_stopped=True
            acq.status="cancelled" if origin_stopped else "blocked_resource" if error.status_code==423 else "blocked_connector"
            acq.data={**acq.data,"reason":error.detail}
            from .sensing_selection import refresh_after_action
            refresh_after_action(db,p,acq);return
    if not selected:
        acq.status="blocked_connector";acq.data={**acq.data,"reason":"旧指定来源缺少统一设备身份，禁止新控制；请核查并迁移选择策略"};return
    if execution.get("adapter") != "http" or (not selected and not execution.get("side_effect")):
        raise ValueError("感知源没有控制接口")
    from .device_gateway_sources_v270 import managed as managed_source, validate_source
    managed_source_call = managed_source(db,p,source)
    key = None
    if managed_source_call:
        try:
            validate_source(db,p,source,purpose='prepare')
        except HTTPException:
            acq.status='blocked_connector';acq.version+=1
            acq.data={**acq.data,'reason':'设备来源当前不具备新采集资格，请恢复原连接或授权后重试原计划'}
            if not acq.data.get('dispatch_started_at'):
                from .sensing_selection import release as release_unstarted
                release_unstarted(db,p,acq)
            resumable_proof = False
            try:
                with db.begin_nested():
                    from .managed_source_unsent_v270 import mark_pre_exchange_unsent,mark_unsent
                    from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as HTTPExchange
                    original_exchange=db.scalar(select(HTTPExchange).where(HTTPExchange.workspace_id==p.workspace_id,
                        HTTPExchange.acquisition_id==acq.id))
                    if original_exchange is None:mark_pre_exchange_unsent(db,p,acq)
                    else:mark_unsent(db,p,acq,original_exchange)
                    resumable_proof = True
            except (HTTPException,ValueError,TypeError,KeyError,AttributeError):
                pass
            audit(db,p,'acquisition.source_preflight_blocked_v270',acq.id,{'post_sent':False,
                'unsent_proof_saved':resumable_proof})
            from .sensing_selection import refresh_after_action
            refresh_after_action(db,p,acq)
            return
    else:
        endpoint = validate_endpoint(execution)
        secret_ref = execution.get("secret_ref")
        if secret_ref and not secret_ref.startswith("GA_CONNECTOR_SECRET_"):
            raise ValueError("连接器密钥引用无效")
        key = os.getenv(secret_ref) if secret_ref else None
        if secret_ref and not key:
            acq.status = "blocked_connector"; acq.data = {**acq.data, "reason": "连接器密钥未配置"}; return
    if selected:
        from .sensing_selection import reservation as reserve_asset
        reservation=reserve_asset(db,p,acq,create=True)
    else:
        db.execute(insert(SourceReservation).values(workspace_id=p.workspace_id, source_id=source.id)
            .on_conflict_do_nothing(index_elements=["workspace_id", "source_id"]))
        reservation = db.scalar(select(SourceReservation).where(SourceReservation.workspace_id == p.workspace_id,
            SourceReservation.source_id == source.id).with_for_update())
    if reservation.acquisition_id and reservation.acquisition_id != acq.id:
        acq.status = "blocked_resource"; acq.data = {**acq.data, "reason": "设备资源已被占用，等待回执释放"}; return
    if selected:
        from .device_identity import reservation as reserve_device
        device_hold=reserve_device(db,p,acq,create=True)
        if device_hold.acquisition_id and device_hold.acquisition_id!=acq.id:
            acq.status="blocked_resource";acq.data={**acq.data,"reason":"统一设备仍被其他接口占用"};return
        device_hold.acquisition_id=acq.id
    reservation.acquisition_id = acq.id; acq.status = "dispatching"
    payload = {"request_key": acq.idempotency_key, "target_external_id": entity.external_id,
               "fields": [g["field"] for g in acq.data["gaps"]],
               "max_duration_seconds": policy.data["max_duration_seconds"]}
    if selected:acq.data={**acq.data,"dispatch_asset":asset.data,"dispatch_execution":execution}
    if selected:payload.update({"asset_external_id":asset.external_id,"action":acq.data["selected_action"],
        "max_cost_minor":acq.data["reserved_cost_minor"],"currency":acq.data["currency"]})
    mapped_exchange = None
    from .http_observation_transport_v177 import prepare_dispatch as prepare_http_mapping
    try:
        with db.begin_nested():
            prior_mapping = None
            if managed_source_call:
                from .http_observation_worker_v177 import find_exchange
                from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as HTTPExchange
                if (acq.data.get('http_observation_exchange_id_v177') or db.scalar(select(HTTPExchange.id).where(
                        HTTPExchange.workspace_id==p.workspace_id,HTTPExchange.acquisition_id==acq.id))):
                    prior_mapping = find_exchange(db,p,acq)
            prepared_mapping = ((prior_mapping,False) if prior_mapping is not None else
                prepare_http_mapping(db,p,acq,source,asset,payload))
            if managed_source_call and prepared_mapping is None:
                raise ValueError('设备资料来源缺少原字段映射，未下发')
            if prepared_mapping is not None:
                mapped_exchange, created = prepared_mapping
                if not created:
                    if managed_source_call:
                        from .managed_source_unsent_v270 import require_unsent_retry
                        if require_unsent_retry(db,p,acq,mapped_exchange,stage='preflight'):
                            payload=mapped_exchange.data['freeze']['wire_frozen']['request']
                            acq.data={**acq.data,'http_observation_exchange_id_v177':mapped_exchange.id}
                        else:
                            acq.status='unknown'
                            acq.data={**acq.data,'reason':'原设备交换缺少完整的未发送恢复依据，保留占用并核对原号，未重新派发'}
                            acq.version+=1
                            audit(db,p,'acquisition.mapping.original_retained_v177',acq.id,{'exchange_id':mapped_exchange.id,'post_replayed':False})
                            return
                    else:
                        # A durable original exchange is never permission to POST
                        # again, even when its HTTP answer has not been saved.
                        acq.status='unknown'
                        acq.data={**acq.data,'reason':'原字段映射请求已登记；请核对原号回执，未再次下发'}
                        acq.version+=1
                        audit(db,p,'acquisition.mapping.original_retained_v177',acq.id,{'exchange_id':mapped_exchange.id,'post_replayed':False})
                        return
                payload=mapped_exchange.data['freeze']['wire_frozen']['request']
                acq.data={**acq.data,'http_observation_exchange_id_v177':mapped_exchange.id}
    except (HTTPException,ValueError,TypeError,KeyError) as error:
        acq.status='blocked_connector'
        acq.data={**acq.data,'reason':str(getattr(error,'detail','原字段映射未通过核对，未发送设备请求'))[:1000]}
        from .sensing_selection import release as release_unstarted
        release_unstarted(db,p,acq)
        acq.version+=1
        audit(db,p,'acquisition.mapping.blocked_v177',acq.id,{'post_sent':False})
        return
    dispatched = False
    def mark_original_send():
        nonlocal dispatched
        send_proof = {}
        if managed_source_call:
            from .leases import ownership
            if ownership(db)['job_id'] != mapped_exchange.dispatch_job_id:
                from .managed_source_unsent_v270 import dispatch_audit_data
                send_proof = dispatch_audit_data(db,p,acq,mapped_exchange)
        acq.data = {**acq.data, "dispatch_started_at": now().isoformat(), "request_hash": digest(payload)}
        audit(db, p, "acquisition.dispatch", acq.id,send_proof); db.commit()
        dispatched = True
    if not managed_source_call:
        mark_original_send()
    from .leases import checkpoint, LeaseLost
    try:
        checkpoint(db)
        if managed_source_call:
            from .device_gateway_source_http_v270 import post_acquisition
            receipt = post_acquisition(db,p,source,acq,mapped_exchange,
                before_authorize=lambda: pre_dispatch(db,p,policy,acq),on_send=mark_original_send)
        else:
            headers = {"Idempotency-Key": acq.idempotency_key}
            if key:
                headers["Authorization"] = "Bearer " + key
            with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
                if mapped_exchange is not None:
                    # Supplier body_sha256 binds these exact frozen UTF-8 bytes.
                    headers['Content-Type']='application/json'
                    response = client.post(endpoint, content=mapped_exchange.data['freeze']['wire_frozen']['body'].encode('utf-8'), headers=headers)
                else:
                    response = client.post(endpoint, json=payload, headers=headers)
            # Even HTTP errors may follow an accepted device command: retain unknown state.
            response.raise_for_status(); receipt = response.json()
        acq = scoped(db, Acquisition, acq.id, p, True)
        if selected:
            if mapped_exchange is not None:
                from .http_observation_worker_v177 import settle_receipt
                settle_receipt(db,p,mapped_exchange.id,receipt)
            else:
                from .sensing_selection import after_receipt
                after_receipt(db,p,acq,receipt)
        else:
            if not isinstance(receipt.get("receipt_id"),str) or receipt.get("status") not in {"accepted","rejected"}:
                raise ValueError("回执格式无效")
            acq.status="awaiting_observation" if receipt["status"]=="accepted" else "rejected"
            if receipt["status"]=="rejected":
                scoped_reservation=db.scalar(select(SourceReservation).where(SourceReservation.source_id==source.id,
                    SourceReservation.workspace_id==p.workspace_id).with_for_update())
                if scoped_reservation and scoped_reservation.acquisition_id==acq.id:scoped_reservation.acquisition_id=None
        acq.data={**acq.data,"receipt":receipt,"received_at":now().isoformat()}
    except LeaseLost:
        raise
    except Exception as error:
        acq = scoped(db, Acquisition, acq.id, p, True)
        if managed_source_call and not dispatched:
            _managed_source_failure_diagnostic(db,p,acq,mapped_exchange,error)
            acq.status = 'blocked_connector'
            acq.data = {**acq.data, 'reason': '设备资料来源或凭据核对未通过，未发送请求'}
            from .sensing_selection import release as release_unstarted
            release_unstarted(db,p,acq)
            from .managed_source_unsent_v270 import mark_unsent
            try:
                with db.begin_nested():mark_unsent(db,p,acq,mapped_exchange)
            except (HTTPException,ValueError,TypeError,KeyError,AttributeError):
                audit(db,p,'acquisition.managed_source_unproven_v270',acq.id,{'post_sent':False,
                    'resume_allowed':False})
        else:
            acq.status = "unknown"
            acq.data = {**acq.data, "reason": "下发结果未知，保留设备占用；核对外部回执后恢复，不能重复起飞或控制"}
    acq.version+=1
    audit(db, p, "acquisition.receipt", acq.id, {"status": acq.status})
    if selected:
        from .sensing_selection import refresh_after_action
        refresh_after_action(db,p,acq)


def reconcile_acquisition(db, p, acquisition_id):
    require(p, "admin", "operator")
    acq = scoped(db, Acquisition, acquisition_id, p)
    from .observation_requests import lock_acquisition_origin
    lock_acquisition_origin(db,p,acq)
    scoped(db,MonitorPolicy,acq.data["policy_id"],p,True)
    acq=scoped(db,Acquisition,acquisition_id,p,True)
    selected=bool(acq.data.get("observation_plan_id"))
    from .http_observation_worker_v177 import find_exchange,queue_read
    mapped_exchange=find_exchange(db,p,acq)
    if mapped_exchange is not None:
        queue_read(db,p,acq)
        return acq
    if acq.status not in ({"unknown","dispatching","awaiting_observation"} if selected else {"unknown","dispatching"}):
        raise HTTPException(409, "此补采请求不需要核对下发回执")
    source = scoped(db,Capability,acq.capability_id,p) if selected else published(db, p, acq.capability_id, "source")
    execution = acq.data.get("dispatch_execution",source.data["execution"]) if selected else source.data["execution"]
    endpoint = validate_endpoint({**execution, "endpoint": execution.get("receipt_endpoint", "")})
    secret_ref = execution.get("secret_ref")
    if secret_ref and not secret_ref.startswith("GA_CONNECTOR_SECRET_"):
        raise HTTPException(409, "连接器密钥引用无效")
    key = os.getenv(secret_ref) if secret_ref else None
    if secret_ref and not key:
        raise HTTPException(409, "连接器凭证未配置")
    headers = {"Authorization": "Bearer " + key} if key else {}
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.get(endpoint, params={"request_key": acq.idempotency_key}, headers=headers)
        response.raise_for_status(); receipt = response.json()
        if selected:
            from .sensing_selection import after_receipt
            after_receipt(db,p,acq,receipt)
        elif receipt.get("request_key") != acq.idempotency_key or receipt.get("status") not in {"accepted", "completed", "rejected", "completed_no_data", "cancelled"}:
            raise ValueError("回执不匹配")
        if not selected and receipt.get("status")!="accepted":
            guarantee="no_future_device_activity" if receipt["status"]=="completed" else "no_future_observation"
            if receipt.get("terminal") is not True or receipt.get(guarantee) is not True:raise ValueError("旧动作缺少永久结束保证")
        if not isinstance(receipt.get("receipt_id"), str) or not receipt.get("receipt_id"):
            raise ValueError("回执缺少编号")
    except Exception:
        raise HTTPException(409, "尚不能确认外部回执，继续保留设备占用")
    if not selected:
        acq.status = "awaiting_observation" if receipt["status"] in {"accepted","completed"} else "rejected"
        acq.data={**acq.data,"device_action_terminal":receipt["status"]!="accepted"}
    acq.data = {**acq.data, "reconciled_receipt": receipt, "reconciled_at": now().isoformat()}
    if not selected and acq.status == "rejected":
        reservation = db.scalar(select(SourceReservation).where(SourceReservation.workspace_id == p.workspace_id,
                                                    SourceReservation.source_id == source.id).with_for_update())
        if reservation and reservation.acquisition_id == acq.id:
            reservation.acquisition_id = None
    acq.version+=1
    audit(db, p, "acquisition.reconcile", acq.id, {"status": acq.status})
    if selected:
        from .sensing_selection import refresh_after_action,close_available
        close_available(db,p,acq)
        refresh_after_action(db,p,acq)
    return acq


def receive_acquisition_observation(db, p, observation, *, settlement_only=False):
    acq_id = observation.data.get("acquisition_id")
    if not acq_id:
        return
    acq = scoped(db, Acquisition, acq_id, p)
    from .observation_requests import lock_acquisition_origin
    lock_acquisition_origin(db,p,acq)
    scoped(db,MonitorPolicy,acq.data["policy_id"],p,True)
    acq=scoped(db,Acquisition,acq_id,p,True)
    if acq.status == "succeeded":
        return
    if acq.status != "awaiting_observation" or acq.entity_id != observation.entity_id or acq.capability_id != observation.source_id:
        raise ValueError("观测与补采请求不匹配")
    if observation.observed_at < datetime.fromisoformat(acq.data["dispatch_started_at"]):
        raise ValueError("观测早于补采下发时间")
    if acq.data.get("observation_plan_id"):
        from .sensing_selection import close_observation
        return close_observation(db,p,observation,acq,refresh_plan=not settlement_only)
    if acq.data.get("device_action_terminal") is not True:return
    policy = scoped(db, MonitorPolicy, acq.data["policy_id"], p)
    state = object_state(db, p, acq.entity_id, policy.data["fields"], policy.data["max_age_seconds"])
    if not state["gaps"] and not state["conflicts"]:
        acq.status = "succeeded"; acq.version += 1
        acq.data = {**acq.data, "closing_observation_id": observation.id, "object_state": state,
                    "finished_at": now().isoformat()}
        reservation = db.scalar(select(SourceReservation).where(SourceReservation.workspace_id == p.workspace_id,
                    SourceReservation.source_id == acq.capability_id).with_for_update())
        if reservation and reservation.acquisition_id == acq.id:
            reservation.acquisition_id = None
        audit(db, p, "acquisition.close", acq.id, {"observation_id": observation.id})
