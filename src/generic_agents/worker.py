"""PostgreSQL durable worker. Claim leases and outbox receipts survive restarts."""
import time
import signal
from importlib import import_module
from threading import Event as _StopEvent
from fastapi import HTTPException
from datetime import timedelta
from uuid import UUID
from zoneinfo import ZoneInfo
from sqlalchemy import select, or_, update, func, literal
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert, JSONB
from sqlalchemy.exc import DBAPIError
from .db import engine
from .models import Job, Observation, Event, Memory, Task, Entity, Capability, Membership, User, Workspace, MonitorPolicy, DataRun, Plan, Acquisition, now, uid
from .security import Principal
from .services import scoped, enqueue_event, audit
from .agents import perception_agent
from .contracts import digest


MQTT_UNIT_QUALITY_KIND = "diagnose_mqtt_units_v357"

SETUP_KINDS = (
    "record_audio_stream_v255", "refresh_audio_stream_delivery_v255",
    "prepare_runtime_document_ocr", "recognize_runtime_document_ocr_page", "search_runtime_document_ocr",
    "parse_runtime_document", "search_runtime_documents",
    "read_business_data", "preview_business_data", "import_business_data",
    "process_observation", "diagnose_data", "route_data_gap", "recheck_data_gap", "settle_received_acquisition_v245",
    "create_signal_approval_draft_v247", "compare_pixel_change_v249", "settle_sound_result_v1",
    "consume_workspace_monitor_tick", "dispatch_acquisition",
    "sample_simulation_v176", "process_simulation_observation_v176", "recover_simulation_sample_v176",
    "read_saved_map_design_v176",
    "read_scoped_design_v181",
    "process_http_mapped_observation_v177", "read_http_observation_receipt_v177",
    "data_query", "execute_native_task_query", "generate_native_query_proposal", "execute_native_goal_query",
    "execute_native_step_plan", "generate_native_step_plan", "generate_native_method_suggestion",
    "index_mqtt_daily", "check_native_business_goal", "append_goal_evaluation_v297", "promote_goal_evaluation_v299",
    "prepare_same_goal_advice_v302", "generate_same_goal_advice_v302", "execute_goal_continuation_read_v314",
    "check_workspace_data_need", "refresh_object_state",
    "advise_signal", "check_candidate_watch", "process_reported_observation", "process_mqtt_observation",
    "submit_mqtt_receipt", "sync_native_receipts", "sync_native_audio_run_v136", "process_local_observation", "read_wms_catalog",
    "capture_wms_map", "interpret_evidence_question", "assess_evidence", "parse_setup_material",
    "prepare_document_copy", "prepare_material_ocr", "recognize_material_ocr_unit", "parse_geo_scene", "prepare_geo_quality_v351", "prepare_geo_quality_v352",
    "analyze_geo_scene", "compare_geo_pair", "transcribe_audio", "transcribe_speech_segments",
    "detect_speech_activity", "understand_setup", "install_specialist", "run_specialist", "process_video",
    "capture_media", "execute_workflow_step", "read_report_draft",
    "execute_full_semantic_graph_node", "advance_full_semantic_graph", "receive_full_semantic_graph_event",
    "admit_graph_source_event", "drain_full_semantic_graph_admissions",
    "evaluate_threshold_study_v149",
    "process_threshold_shadow_v151", "check_threshold_policy_v153",
)


SETUP_POLL_ROOTS = (
    ("geo_quality_reception_v352", "deliver_pending"),
    ("audio_streams", "poll_once"),
    ("audio_stream_delivery", "deliver_pending"),
    ("runtime_document_ocr_receipts", "consume_once"),
    ("runtime_document_receipts", "consume_once"),
    ("business_data_receipts", "consume_once"),
    ("workspace_observation_intake", "deliver_pending"),
    ("signal_approval_routing_v247", "poll_once"),
    ("media_daily_delivery", "consume_once"),
    ("media_daily_task_adapter", "consume_once"),
    ("document_question_service", "poll_once"),
    ("workflow_evidence_consent_wait_v199", "poll_once"),
    ("workflow_document_packet_wait_v207", "poll_once"),
    ("workflow_provenance_wait_v197", "poll_once"),
    ("workflow_service_poll_v195", "schedule_once"),
    ("simulation_loop_v176", "schedule_once"), ("simulation_loop_v176", "deliver_pending"),
    ("mqtt_data", "deliver_pending"), ("perception_signals", "deliver_pending"),
    ("candidate_watches", "deliver_pending"), ("supplement_watch", "schedule_once"),
    ("media_monitor", "schedule_once"), ("supplement", "schedule_once"),
)
SETUP_DAILY_ROOTS = (
    ("business_data_receipts", "schedule_once"),
    ("workspace_observation_intake", "reconcile_daily"),
    ("workspace_monitoring", "schedule_once"),
    ("mqtt_observations", "schedule_pending"), ("mqtt_daily", "schedule_once"),
    ("video_daily", "schedule_once"), ("audio_daily", "schedule_once"),
    ("geo_daily", "schedule_once"), ("local_observations", "schedule"),
    ("native_receipts", "schedule"),
)


def _workspace_uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("工作区范围必须提供标准小写 UUID") from None
    return value


class ActorRevoked(ValueError):
    """The current account or workspace execution membership is unavailable."""


class MqttDiagnosticScopeMismatch(ValueError):
    """Do not run generic recovery against a changed diagnostic job."""


def actor(db, workspace_id, user_id):
    user = db.get(User, user_id)
    membership = db.scalar(select(Membership).where(Membership.workspace_id == workspace_id,
                                                     Membership.user_id == user_id))
    if not user or not user.active or not membership or membership.role not in {"admin", "operator"}:
        raise ActorRevoked("任务执行授权已撤销")
    return Principal(user_id, workspace_id, membership.role, "worker")


def claim(job_id=None, kinds=None, *, workspace_id=None, _stop_event=None, _mqtt_gateway_id=None):
    from .report_evidence_worker_isolation import KIND as SCOPED_REPORT_KIND, NOT_AVAILABLE
    scoped_report = bool(job_id is not None and tuple(kinds or ()) == (SCOPED_REPORT_KIND,))
    if SCOPED_REPORT_KIND in (kinds or ()) and not scoped_report:
        raise ValueError('受控报告处理须指定唯一任务编号及唯一任务类型')
    if scoped_report:
        try:
            if not isinstance(job_id,str) or str(UUID(job_id)) != job_id:
                raise ValueError()
        except (ValueError,TypeError,AttributeError):
            raise ValueError('受控报告须指定标准小写 UUID 任务编号') from None
    if workspace_id is not None:
        workspace_id = _workspace_uuid(workspace_id)
    if _mqtt_gateway_id is not None:
        _workspace_uuid(_mqtt_gateway_id)
        if workspace_id is None or job_id is None or tuple(kinds or ()) != (MQTT_UNIT_QUALITY_KIND,):
            raise ValueError('单位质量诊断须指定工作区、原网关、原任务及唯一任务类型')
        _workspace_uuid(job_id)
    if _stop_event is not None and _stop_event.is_set():
        return None
    with Session(engine(), expire_on_commit=False) as db:
        from .workflow_job_dependencies_v264 import pending_predicate
        from .workflow_goal_evaluation_barrier_v288 import pending_predicate as pending_goal_evaluation
        from .goal_approval_claim_barrier_v354 import pending_predicate as pending_goal_approval
        from .auto_evaluation_recovery_v360 import Control, rollout_enabled
        due = (Job.available_at == NOT_AVAILABLE if scoped_report else
               (Job.available_at <= func.clock_timestamp()) & (Job.kind != SCOPED_REPORT_KIND))
        query = (select(Job).where(due, Job.kind != "login_limit",
            ~pending_predicate(),
            ~pending_goal_evaluation(),
            ~pending_goal_approval(),
            or_(Job.status == "queued", (Job.status == "running") &
                or_(Job.lease_until.is_(None), Job.lease_until < func.clock_timestamp())))
            .order_by(Job.available_at).with_for_update(skip_locked=True).limit(1))
        if rollout_enabled():
            fenced_producer = select(Control.id).where(Control.workspace_id==Job.workspace_id,
                Control.producer_job_id==Job.id,Control.outcome=='fenced_effect_unknown').exists()
            query=query.where(~fenced_producer)
        if job_id: query = query.where(Job.id == job_id)
        if kinds: query = query.where(Job.kind.in_(kinds))
        if _mqtt_gateway_id is None:
            from .models import Audit as MqttQueueAudit
            original_mqtt_request=select(MqttQueueAudit.id).where(
                MqttQueueAudit.workspace_id==Job.workspace_id,MqttQueueAudit.object_id==Job.id,
                MqttQueueAudit.action=='quality.mqtt_unit_diagnostic_queued_v357').exists()
            # Keep a damaged diagnostic out of every general worker even if
            # its mutable kind was changed after the original enqueue.
            query=query.where(Job.kind != MQTT_UNIT_QUALITY_KIND,
                ~Job.idempotency_key.startswith('mqtt-unit-diagnostic-v357:'),
                func.coalesce(Job.data['unit_request']['schema'].as_string(),'')!='mqtt-unit-diagnostic-request-v357',
                ~original_mqtt_request)
        else:
            query=query.where(Job.kind == MQTT_UNIT_QUALITY_KIND,
                Job.data['gateway_id'].as_string() == _mqtt_gateway_id)
        if workspace_id is None and job_id is None:
            query=query.where(Job.kind.not_in(['discover_device_gateway_v268', 'create_signal_approval_draft_v247', 'compare_pixel_change_v249', 'settle_sound_result_v1',
                'record_audio_stream_v255','refresh_audio_stream_delivery_v255']))
            from .models import Audit
            audio_job_audit=select(Audit.id).where(Audit.workspace_id==Job.workspace_id,Audit.object_id==Job.id,
                Audit.action.in_(['audio.stream_record_queued_v255','audio.stream_observation_queued_v255',
                                 'audio.stream_delivery_queued_v255'])).exists()
            query=query.where(func.coalesce(Job.data['schema'].as_string(),'').not_in(
                ['audio-stream-record-job-v255','audio-stream-extract-job-v255','audio-stream-delivery-job-v255']),
                ~Job.idempotency_key.startswith('audio-stream-record:'),
                ~Job.idempotency_key.startswith('audio-stream-delivery:'),~audio_job_audit)
            from .sound_candidate_watches import JOB_SCHEMA,ENQUEUED
            sound_job_audit=select(Audit.id).where(Audit.workspace_id==Job.workspace_id,
                Audit.action==ENQUEUED,Audit.object_id==Job.id).exists()
            query=query.where(func.coalesce(Job.data['schema'].as_string(),'')!=JOB_SCHEMA,
                ~Job.idempotency_key.startswith('sound-candidate-check:'),~sound_job_audit)
            from .multimodal_question_service_v262 import JOB_SCHEMA as MM_QUERY_SCHEMA, QUEUED as MM_QUERY_QUEUED
            mm_query_audit=select(Audit.id).where(Audit.workspace_id==Job.workspace_id,Audit.action==MM_QUERY_QUEUED,
                Audit.data['job_id'].as_string()==Job.id).exists()
            mm_query_row=select(DataRun.id).where(DataRun.workspace_id==Job.workspace_id,
                DataRun.id==Job.data['query_id'].as_string(),DataRun.id.like('________-____-5___-%')).exists()
            query=query.where(func.coalesce(Job.data['schema'].as_string(),'')!=MM_QUERY_SCHEMA,
                func.coalesce(Job.data['family'].as_string(),'')!='multimodal_query_v262',
                ~Job.idempotency_key.startswith('multimodal-question-v262:'),~mm_query_audit,~mm_query_row)
        if workspace_id is not None: query = query.where(Job.workspace_id == workspace_id)
        row = db.scalar(query)
        if not row or (_stop_event is not None and _stop_event.is_set()):
            return None
        if rollout_enabled() and row.kind == 'append_goal_evaluation_v297':
            # claim has already locked the producer row; retain that lock
            # until commit so stop cannot race between this test and claim.
            if db.scalar(select(Control.id).where(Control.workspace_id == row.workspace_id,
                         Control.producer_job_id == row.id,Control.outcome=='fenced_effect_unknown')) is not None:
                db.rollback()
                return None
        from . import leases
        expired = row.status == "running"
        exhausted = row.attempts >= 3
        continuation_recovery = row.kind == 'execute_goal_continuation_read_v314' and row.data.get('recovery_only') is True
        row.data = {**row.data, "recovery_only": exhausted or continuation_recovery,
            "lease_recoveries": row.data.get("lease_recoveries", 0) + int(expired)}
        row.status = "running"; row.attempts += int(not exhausted); row.lease_token = uid()
        # Keep elapsed-time arithmetic in PostgreSQL. Python ZoneInfo addition
        # resets fold=1 during a DST rollback and can put a fresh lease an hour
        # into the past, even though same-zone subtraction appears positive.
        row.lease_until = db.scalar(select(func.clock_timestamp() + timedelta(seconds=leases.LEASE_SECONDS)))
        if _stop_event is not None and _stop_event.is_set():
            db.rollback()
            return None
        db.commit()
        return row.id, row.lease_token


def _catalogue_allocation_fresh_phase_job(db, job):
    """Lightweight phase eligibility, NOT source, model or payment admission.

    Run only inside the original exclusively fenced recovery transaction. A
    genuine existing Consumer must still be unissued. The handler rechecks its
    entire original family/body/history/lease after activation.
    """
    profile = job.data.get('generation_profile') if type(job.data) is dict else None
    schemas = {
        'catalogue-allocation-v213': 'report-business-catalogue-allocation-preparation-v213',
        'catalogue-current-allocation-v260': 'report-business-catalogue-current-allocation-preparation-v260',
    }
    if (job.kind != 'read_saved_map_design_v176' or type(job.data) is not dict
            or type(profile) is not str or profile not in schemas
            or ('recovery_only' in job.data and job.data['recovery_only'] is not False)
            or type(job.data.get('consumer_id')) is not str
            or type(job.data.get('actor_id')) is not str):
        return False
    from .report_saved_map_design_models_v176 import ReportSavedMapDesignV176 as Consumer
    query = select(Consumer.id).where(Consumer.id == job.data['consumer_id'],
        Consumer.job_id == job.id, Consumer.actor_id == job.data['actor_id'],
        Consumer.workspace_id == job.workspace_id, Consumer.status.in_(['queued', 'running']),
        Consumer.reserved_call_id.is_(None),
        func.jsonb_typeof(Consumer.data['dispatch_started']) == 'boolean',
        Consumer.data['dispatch_started'] == literal(False, type_=JSONB),
        Consumer.data['preview']['generation_profile'].as_string() == profile,
        Consumer.data['preview']['body']['generation_profile'].as_string() == profile,
        Consumer.data['preview']['preparation']['schema'].as_string()
            == schemas[profile])
    if profile == 'catalogue-current-allocation-v260':
        query = query.where(Consumer.data['preview']['preparation']['native_preparation']['schema'].as_string()
            == 'report-business-catalogue-allocation-preparation-v213')
    found = db.scalar(query.limit(1))
    return found == job.data['consumer_id']


def _check_append_terminal_fence(db, job, status):
    """Avoid reading fence-only payload fields when the rollout is disabled."""
    if status != 'succeeded' or job.kind != 'append_goal_evaluation_v297':return
    from .auto_evaluation_recovery_v360 import rollout_enabled,require_unfenced,Refused
    if not rollout_enabled():return
    from .leases import LeaseLost
    try:
        require_unfenced(db,workspace_id=job.workspace_id,task_id=job.data['task_id'],job_id=job.id)
    except Refused:
        raise LeaseLost() from None


def process_once(job_id=None, kinds=None, deliver=True, *, workspace_id=None, _stop_event=None, _mqtt_gateway_id=None):
    requested_job_id=job_id
    if _mqtt_gateway_id is not None and deliver is not False:
        raise ValueError('网关限定诊断不得投递其他任务')
    if workspace_id is not None:
        workspace_id = _workspace_uuid(workspace_id)
    from .leases import (attach, heartbeat, LeaseLost, local_goal_phase, graph_handler_lease_phase,
        catalogue_allocation_handler_lease_phase)
    from .job_recovery import recover_calls, fail_target
    def dispatch():
        if deliver and not (_stop_event is not None and _stop_event.is_set()):
            if workspace_id is None: deliver_events()
            else: deliver_events(workspace_id=workspace_id)
    claim_options = {}
    if workspace_id is not None: claim_options['workspace_id'] = workspace_id
    if _stop_event is not None: claim_options['_stop_event'] = _stop_event
    if _mqtt_gateway_id is not None: claim_options['_mqtt_gateway_id'] = _mqtt_gateway_id
    if _stop_event is not None and _stop_event.is_set(): return False
    lease = claim(job_id, kinds, **claim_options)
    if not lease:
        dispatch()
        if _stop_event is not None and _stop_event.is_set(): return False
        lease=claim(job_id, kinds, **claim_options)
        if not lease:return False
    job_id, token = lease
    status, error = "succeeded", None
    failure_diagnostic = None
    local_workspace=None
    finished_kind=None
    finished_workspace=None
    promotion_committed=False
    try:
        with heartbeat(engine(), job_id, token):
            with (Session(engine(), expire_on_commit=False) as db,
                    local_goal_phase(db, job_id, token) as goal_phase,
                    graph_handler_lease_phase(db, job_id, token) as graph_phase,
                    catalogue_allocation_handler_lease_phase(db, job_id, token) as allocation_phase):
                attach(db, job_id, token)
                job = db.get(Job, job_id)
                if (job.kind==MQTT_UNIT_QUALITY_KIND or _mqtt_gateway_id is not None) and (
                    _mqtt_gateway_id is None or job.kind!=MQTT_UNIT_QUALITY_KIND
                    or requested_job_id!=job.id or workspace_id!=job.workspace_id
                    or job.data.get('gateway_id')!=_mqtt_gateway_id
                    or tuple(kinds or ())!=(MQTT_UNIT_QUALITY_KIND,)):
                    raise MqttDiagnosticScopeMismatch('单位质量诊断的网关租约范围不一致')
                recovery_p = Principal(job.data["actor_id"], job.workspace_id, "viewer", "worker-recovery")
                recover_calls(db, recovery_p, job)
                allocation_fresh = _catalogue_allocation_fresh_phase_job(db, job)
                db.commit()
                if (job.kind in {'check_native_business_goal','append_goal_evaluation_v297','promote_goal_evaluation_v299','prepare_same_goal_advice_v302','generate_same_goal_advice_v302','execute_goal_continuation_read_v314'} and 'time_trigger' not in job.data
                        and 'time_trigger_hash' not in job.data):
                    goal_phase.activate()
                elif job.kind == 'execute_full_semantic_graph_node':
                    graph_phase.activate()
                elif allocation_fresh:
                    allocation_phase.activate(workspace_id=job.workspace_id, actor_id=job.data['actor_id'])
                if job.data.get("recovery_only") and job.kind not in {'execute_native_task_query','generate_native_query_proposal','execute_native_goal_query','execute_native_step_plan','generate_native_step_plan','generate_plan','data_query','interpret_evidence_question','advise_signal','generate_native_method_suggestion','prepare_material_ocr','recognize_material_ocr_unit','execute_full_semantic_graph_node','advance_full_semantic_graph','receive_full_semantic_graph_event','admit_graph_source_event','drain_full_semantic_graph_admissions','append_goal_evaluation_v297','promote_goal_evaluation_v299','prepare_same_goal_advice_v302','generate_same_goal_advice_v302','execute_goal_continuation_read_v314'}:
                    raise ValueError("已达到后台自动恢复次数上限")
                p = (Principal(job.data['actor_id'],job.workspace_id,'viewer','policy-stop-monitor')
                     if job.kind=='check_threshold_policy_v153' and job.data.get('internal_scope')=='threshold-policy-stop-v153'
                     else Principal(job.data['actor_id'],job.workspace_id,'viewer','mqtt-daily-index')
                     if job.kind=='index_mqtt_daily' and job.data.get('internal_scope')=='mqtt-daily-index'
                     else actor(db, job.workspace_id, job.data["actor_id"]))
                if job.kind in {'index_mqtt_daily','check_threshold_policy_v153'}:
                    # This lease fences a local read/index transaction, not device
                    # execution. Its frozen source checks remain in mqtt_daily;
                    # current operator revocation must not erase old raw history.
                    db.info.pop('worker_actor',None)
                else:db.info["worker_actor"] = p
                if job.kind in {'prepare_runtime_document_ocr', 'recognize_runtime_document_ocr_page', 'search_runtime_document_ocr'}:
                    from . import runtime_document_ocr
                    handler = {'prepare_runtime_document_ocr': runtime_document_ocr.process_prepare,
                               'recognize_runtime_document_ocr_page': runtime_document_ocr.process_page,
                               'search_runtime_document_ocr': runtime_document_ocr.process_search}[job.kind]
                    result = handler(db, p, job)
                    if result['status'] in {'failed', 'partial'}:
                        status, error = 'failed', result.get('error') or '扫描页文字未全部处理完成，请核对原页和处理记录'
                    elif result['status'] == 'cancelled':
                        status, error = 'cancelled', result.get('error')
                elif job.kind in {'parse_runtime_document', 'search_runtime_documents'}:
                    from . import runtime_documents
                    handler = (runtime_documents.process_parse if job.kind == 'parse_runtime_document'
                               else runtime_documents.process_search)
                    result = handler(db, p, job)
                    if result['status'] in {'failed', 'needs_input'}:
                        status, error = 'failed', result.get('error') or '资料未处理完整，请核对原件、解析说明和原记录'
                    elif result['status'] == 'cancelled':
                        status, error = 'cancelled', result.get('error')
                elif job.kind in {'read_business_data', 'preview_business_data', 'import_business_data'}:
                    from . import business_data
                    handler = {'read_business_data': business_data.process_read,
                               'preview_business_data': business_data.process_preview,
                               'import_business_data': business_data.process_import}[job.kind]
                    result = handler(db, p, job)
                    if result['status'] in {'failed', 'partial'}:
                        status, error = 'failed', result.get('error') or '业务数据处理未全部完成，请核对原批次记录'
                    elif result['status'] == 'cancelled':
                        status, error = 'cancelled', result.get('error')
                elif job.kind == 'check_threshold_policy_v153':
                    from .threshold_policy_monitor_v153 import process as process_policy_monitor
                    process_policy_monitor(db,p,job)
                elif job.kind == 'consume_workspace_monitor_tick':
                    from .workspace_monitoring import process as process_monitor_tick
                    result = process_monitor_tick(db, p, job)
                    if result['state'] == 'blocked':
                        status, error = 'failed', result['reason']
                    elif result['state'] == 'cancelled':
                        status, error = 'cancelled', result['reason']
                elif job.kind == 'index_mqtt_daily':
                    from .mqtt_daily import process_checkpoint
                    process_checkpoint(db,p,job)
                elif job.kind == 'execute_native_task_query':
                    from .native_task_queries import process as process_supporting_query
                    result=process_supporting_query(db,p,job)
                    if result in {'failed','blocked_evidence'}:
                        status='failed';error=scoped(db,DataRun,job.data['query_id'],p).data.get('error')
                    elif result=='cancelled':status='cancelled'
                elif job.kind == 'generate_native_query_proposal':
                    from .native_query_proposals import process as process_query_proposal
                    result=process_query_proposal(db,p,job)
                    if result in {'failed','response_unknown'}:
                        status='failed';error=scoped(db,Plan,job.data['plan_id'],p).data.get('error')
                    elif result=='cancelled':status='cancelled'
                elif job.kind == 'execute_native_goal_query':
                    from .native_goal_queries import process as process_native_query
                    result=process_native_query(db,p,job)
                    if result in {'failed','blocked_evidence'}:
                        status='failed';error=scoped(db,DataRun,job.data['query_id'],p).data.get('error')
                    elif result=='cancelled':status='cancelled'
                elif job.kind == 'execute_goal_continuation_read_v314':
                    from .same_goal_continuation_execution_runtime_v314 import process as process_continuation
                    result=process_continuation(db,p,job)
                    if result=='cancelled':status='cancelled'
                    elif result!='succeeded':status='failed';error='办理结果尚未核对完成，原请求保留'
                elif job.kind == 'execute_native_step_plan':
                    from . import native_step_execution as native_execution
                    result=native_execution.process(db,p,job)
                    if result in {'failed','blocked'}:
                        status='failed';error=native_execution._run(scoped(db,Task,job.data['task_id'],p),job.data['plan_id']).get('error')
                    elif result=='cancelled':status='cancelled'
                elif job.kind == 'generate_native_step_plan':
                    from .native_step_planning import process as process_native_plan
                    result=process_native_plan(db,p,job)
                    if result in {'failed','response_unknown'}:
                        status='failed';error=scoped(db,Plan,job.data['plan_id'],p).data.get('error')
                    elif result=='cancelled':status='cancelled'
                elif job.kind == 'generate_native_method_suggestion':
                    from .native_method_suggestions import process,checked
                    process(db,p,job)
                    suggestion=checked(db,p,job.data['suggestion_id'])
                    if suggestion.status in {'failed','response_unknown'}:status,error='failed',suggestion.data.get('error')
                elif job.kind == 'check_native_business_goal':
                    from .native_goals import process
                    process(db,p,job)
                elif job.kind == 'append_goal_evaluation_v297':
                    from .workflow_goal_evaluation_append_v299 import run as append_evaluation
                    result=append_evaluation(db,p,job)
                    if result != 'succeeded':
                        status,error='failed','原追加评价尚未取得完整成功回执，请核对原操作；未另建查询或重发调用'
                elif job.kind == 'promote_goal_evaluation_v299':
                    from .goal_evaluation_maturity_v299 import process_promotion
                    result=process_promotion(db,p,job)
                    if result != 'succeeded':
                        status,error='failed','原评价尚未完成成熟核对，请继续原操作；未切换当前结果'
                elif job.kind == 'prepare_same_goal_advice_v302':
                    from .same_goal_advice_preparation_v304 import run as prepare_advice
                    result=prepare_advice(db,p,job)
                    if result == 'cancelled':status='cancelled'
                    elif result != 'succeeded':
                        status,error='failed','原业务资料准备尚未取得完整回执；请核对原操作，未发送模型请求'
                elif job.kind == 'generate_same_goal_advice_v302':
                    from .same_goal_advice_runtime_v308 import process as generate_advice
                    result=generate_advice(db,p,job)
                    if result == 'cancelled':status='cancelled'
                    elif result != 'succeeded':
                        status,error='failed','原业务建议尚未取得完整首答回执；保留原调用和费用，不重新发送'
                elif job.kind == 'check_workspace_data_need':
                    from .workspace_needs import process
                    process(db,p,job)
                elif job.kind == 'create_signal_approval_draft_v247':
                    from .signal_approval_routing_v247 import process as approval_draft
                    approval_draft(db,p,job)
                elif job.kind == 'refresh_object_state':
                    from .state_refresh import process as refresh_state
                    refresh_state(db,p,job)
                elif job.kind == 'settle_received_acquisition_v245':
                    from .acquisition_ack_settlement_v245 import process as settle_acquisition
                    settle_acquisition(db,p,job)
                elif job.kind == 'advise_signal':
                    from .signal_advice import process
                    process(db,p,job)
                elif job.kind == 'interpret_evidence_question':
                    from .evidence_question import process
                    process(db,p,job)
                elif job.kind == 'assess_evidence':
                    from .evidence_assessment import process
                    process(db,p,job)
                elif job.kind == 'business_automation_start':
                    from .business_automation_service import process as process_automation
                    result = process_automation(db,p,job)
                    if result in {'waiting_source','waiting_limit'}: status = result
                    elif result in {'blocked','skipped','cancelled'}: status = 'cancelled' if result != 'blocked' else 'failed'
                elif job.kind == 'execute_workflow_step':
                    from .workflow_abilities import process
                    process(db,p,job)
                elif job.kind == 'admit_graph_source_event':
                    from .graph_event_dispatch import process as admit_graph_source
                    result = admit_graph_source(db, p, job)
                    from .workflow_graph_waits import wake_admission
                    wake_admission(db, p, result)
                elif job.kind in {'execute_full_semantic_graph_node','advance_full_semantic_graph','receive_full_semantic_graph_event','drain_full_semantic_graph_admissions'}:
                    from . import workflow_graph
                    result = (workflow_graph.process(db,p,job) if job.kind == 'execute_full_semantic_graph_node'
                        else workflow_graph.process_wait(db,p,job))
                    if result in {'cancelled','blocked','waiting_service','waiting_provenance','waiting_evidence_consent','waiting_document_packet_v207'}:
                        status = result
                    if result == 'blocked':
                        saved = scoped(db,Task,job.data['task_id'],p)
                        error = saved.data.get(workflow_graph.TRANSPORT, {}).get(job.data.get('activation_id'), {}).get(
                            'reason') or '完整业务图执行已暂停，请核对已保存的节点回执后明确恢复'
                elif job.kind == 'check_candidate_watch':
                    from .candidate_watches import process
                    process(db,p,job)
                elif job.kind == 'settle_sound_result_v1':
                    from .sound_result_settlement import process as settle_sound
                    if settle_sound(db,p,job)!='succeeded':raise ValueError('原声音成果交接未完成')
                elif job.kind == 'record_audio_stream_v255':
                    from .audio_streams import process as record_audio
                    result=record_audio(db,p,job)
                    if result not in {'succeeded','waiting','cancelled','failed'}:raise ValueError('音轨采集返回未登记状态')
                    if result=='failed':status='failed';error='音轨采集未完成；已保存原件保留，请在原采集记录核对原因'
                    try:
                        with db.begin_nested():
                            from .audio_stream_models import AudioStream
                            from .audio_streams import stream_observations
                            from .audio_stream_delivery import enqueue_snapshot
                            controller=scoped(db,AudioStream,job.data['stream_id'],p)
                            if controller.status in {'completed','stopped','failed'}:
                                recorded=stream_observations(db,p,controller)
                                if recorded:enqueue_snapshot(db,p,controller,recorded[-1].id)
                    except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                        audit(db,p,'audio.stream_snapshot_enqueue_blocked_v255',job.data['stream_id'],
                            {'job_id':job.id,'reason':'原采集进度保留；完整日清单修订尚待核对'})
                elif job.kind == 'refresh_audio_stream_delivery_v255':
                    from .audio_stream_delivery import process as deliver_audio
                    deliver_audio(db,p,job)
                elif job.kind == 'sync_native_receipts':
                    from .native_receipts import sync_queued_video
                    sync_queued_video(db,p,job)
                elif job.kind == 'sync_native_audio_run_v136':
                    from .worker_native_audio_v136 import process as sync_native_audio
                    sync_native_audio(db,p,job)
                elif job.kind == 'parse_setup_material':
                    from .setup_api import process_material
                    process_material(db, p, job)
                elif job.kind == 'prepare_document_copy':
                    from .document_copy import process as process_copy, checked
                    process_copy(db,p,job)
                    copy_job=checked(scoped(db,Job,job.id,p))
                    copy_status=copy_job.data['copy_status']
                    if copy_status in {'failed','response_unknown'}: status,error='failed',copy_job.data.get('copy_error')
                    elif copy_status=='cancelled': status='cancelled'
                elif job.kind == 'prepare_material_ocr':
                    from .material_ocr import process_prepare
                    process_prepare(db, p, job)
                elif job.kind == 'recognize_material_ocr_unit':
                    from .material_ocr import process_unit
                    process_unit(db, p, job)
                elif job.kind == 'parse_geo_scene':
                    from .geo_process import process_scene
                    process_scene(db,p,job)
                elif job.kind in {'prepare_geo_quality_v351','prepare_geo_quality_v352'}:
                    from .geo_quality_v351 import process as process_geo_quality
                    process_geo_quality(db,p,job)
                elif job.kind == 'read_wms_catalog':
                    from .wms import probe
                    probe(db,p,job)
                elif job.kind == 'capture_wms_map':
                    from .wms import read
                    read(db,p,job)
                elif job.kind == 'analyze_geo_scene':
                    from .geo_inference import process_run
                    process_run(db,p,job)
                elif job.kind == 'detect_speech_activity':
                    from .local_vad import process as process_activity
                    process_activity(db,p,job)
                elif job.kind == 'transcribe_speech_segments':
                    from .local_segmented import process as process_segments
                    process_segments(db,p,job)
                elif job.kind == 'transcribe_audio':
                    from .local_audio import process as process_audio
                    process_audio(db, p, job)
                elif job.kind == 'compare_geo_pair':
                    from .geo_pairs import process
                    process(db,p,job)
                elif job.kind == 'compare_pixel_change_v249':
                    from .pixel_change_runs_v249 import process as process_pixel_change
                    result = process_pixel_change(db, p, job)
                    status, error = result['status'], result['error']
                elif job.kind == 'install_specialist':
                    from .local_specialists import process_install
                    process_install(db, p, job)
                elif job.kind == 'run_specialist':
                    from .local_specialists import process_run
                    process_run(db, p, job)
                elif job.kind == 'process_video':
                    from .native_video_provenance_v164 import before_result,freeze_result
                    started = before_result(db,p,job)
                    from .local_video import process_video
                    process_video(db, p, job)
                    freeze_result(db,p,job,started=started)
                elif job.kind == 'capture_media':
                    from .native_video_provenance_v164 import freeze_capture
                    frozen = freeze_capture(db,p,job)
                    if frozen:
                        # Commit the pre-read anchor under the existing lease before
                        # the first external source operation. attach() remains in
                        # force and revalidates every subsequent transaction.
                        db.commit()
                    from .media import process_capture
                    process_capture(db,p,job)
                elif job.kind == 'record_continuous_media':
                    from .continuous_media import process_record
                    process_record(db,p,job)
                    from .models import ContinuousStream
                    stream=scoped(db,ContinuousStream,job.data['stream_id'],p)
                    if stream.status in {'blocked','reconnecting'}:status,error='failed',stream.data.get('last_error')
                    elif stream.status in {'paused','stopped'}:status='cancelled'
                elif job.kind == 'read_scoped_design_v181':
                    from .report_scoped_design_consumer_v181 import run as read_scoped_design
                    result = read_scoped_design(db, p, job)
                elif job.kind == 'read_saved_map_design_v176':
                    from .report_saved_map_design_consumer_v176 import run as read_saved_map_design
                    result = read_saved_map_design(db, p, job)
                    if result['status'] in {'failed','response_unknown'}:
                        status, error = 'failed', '新方案未完成；原调用与回答保留，请核对原记录'
                elif job.kind in {'read_report_draft','read_report_scoped_v1'}:
                    if job.kind == 'read_report_scoped_v1':
                        from .report_evidence_worker_isolation import isolated
                        if not isolated(job):
                            raise ValueError('受控报告任务的隔离标记已变化')
                        from .models import SetupReportDraft
                        report=db.scalar(select(SetupReportDraft).where(
                            SetupReportDraft.id==job.data.get('report_draft_id'),
                            SetupReportDraft.workspace_id==job.workspace_id))
                        if (not report or report.actor_id!=job.data.get('actor_id')
                                or not report.request_id.startswith('fl_') or not isinstance(report.data,dict)
                                or report.data.get('schema')!='report-evidence-draft-state-v1'
                                or report.data.get('job_id')!=job.id):
                            raise ValueError('受控任务与报告草稿的归属不一致')
                    from .report_reading_drafts import run as read_report_draft
                    draft_status = read_report_draft(db, p, job.data['report_draft_id'])
                    if draft_status in {'failed', 'response_unknown'}:
                        status = 'failed'; error = '报告待核草案未完成；原调用记录保留，不自动重付'
                elif job.kind == 'understand_setup':
                    from .setup_understanding import run as understand_setup
                    understand_setup(db, p, job.data['analysis_id'])
                    from .models import SetupAnalysis
                    analysis = scoped(db, SetupAnalysis, job.data['analysis_id'], p)
                    if analysis.status == 'failed':
                        status = 'failed'; error = analysis.data.get('error')
                elif job.kind=='submit_mqtt_receipt':
                    from .mqtt_observations import process_submission
                    row=process_submission(db,p,job)
                    job.data={**job.data,'observation_id':row.id}
                elif job.kind == 'sample_simulation_v176':
                    from .simulation_loop_v176 import run_sample
                    run_sample(db,p,job)
                elif job.kind == 'recover_simulation_sample_v176':
                    from .simulation_sample_recovery_v176 import run as recover_simulation_sample
                    recover_simulation_sample(db,p,job)
                elif job.kind == 'read_http_observation_receipt_v177':
                    from .http_observation_worker_v177 import run_read
                    from .observation_requests import lock_acquisition_origin
                    from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177
                    exchange=scoped(db,HTTPObservationExchangeV177,job.data['exchange_id'],p)
                    lock_acquisition_origin(db,p,scoped(db,Acquisition,exchange.acquisition_id,p))
                    run_read(db,p,job)
                elif job.kind in {"process_observation","process_local_observation","process_reported_observation","process_mqtt_observation","process_simulation_observation_v176","process_http_mapped_observation_v177"}:
                    from .threshold_policy_execution_v153 import prepare_job as prepare_policy
                    policy_key=prepare_policy(db,p,job)
                    if job.kind in {'process_local_observation','process_reported_observation','process_mqtt_observation'}:local_workspace=job.workspace_id
                    if job.kind=='process_reported_observation':
                        from .perception_signals import guard_rule
                        guard_rule(db,p,scoped(db,Observation,job.data['observation_id'],p))
                    observation = scoped(db, Observation, job.data["observation_id"], p)
                    if observation.status != "processed":
                        if workspace_id is not None and job.kind == 'process_observation':
                            from .workspace_observation_intake import guard_execution
                            guard_execution(db, p, job, observation, first=True)
                        state = perception_agent(db, p, observation, job.data["extractor_id"], job.attempts,
                            invocation_key=policy_key or job.data.get("resume_invocation_key"))
                        if workspace_id is not None and job.kind == 'process_observation':
                            guard_execution(db, p, job, observation)
                        if state["status"] != "processed":
                            status = "failed"; error = state.get("error", "观测提取失败")
                            observation.status = "failed"
                    audit(db, p, "perception.process", observation.id, {"status": observation.status})
                    if observation.status == "processed":
                        from .threshold_policy_execution_v153 import completed as complete_policy
                        complete_policy(db,p,job)
                        db.commit(); dispatch()
                        from .perception import receive_acquisition_observation
                        try:
                            with db.begin_nested():
                                # dispatch() committed reception in another
                                # Session; this worker keeps objects after commit.
                                # Reload the whole original under its lease fence.
                                db.refresh(observation)
                                receive_acquisition_observation(db, p, observation)
                        except LeaseLost:
                            raise
                        except Exception:
                            acq_id = observation.data.get("acquisition_id")
                            if acq_id:
                                acquisition = scoped(db, Acquisition, acq_id, p, True)
                                acquisition.data = {**acquisition.data, "reason": "观测已送达，但补采核验未通过；需要核对时间和设备回执"}
                                audit(db, p, "acquisition.verification_failed", acquisition.id)
                elif job.kind == "execute_plan":
                    from .execution import execute_plan
                    plan = scoped(db, Plan, job.data['plan_id'], p)
                    if plan.status not in {'waiting_tool', 'executed'}:
                        execute_plan(db, p, plan.id)
                    plan = scoped(db, Plan, plan.id, p)
                    if plan.status == 'waiting_tool':
                        status, error = 'failed', '注册工具执行受阻，请在任务页面核查回执'
                    elif plan.status == 'cancelled':
                        status = 'cancelled'
                    elif plan.status == 'failed':
                        status, error = 'failed', '计划依据已变化，请核查已保存结果后重新规划'
                elif job.kind == "apply_governance":
                    from .execution import apply_repair
                    apply_repair(db, p, job.data["plan_id"])
                elif job.kind=='diagnose_mqtt_units_v357':
                    from .mqtt_unit_quality_dispatch_v357 import run as run_mqtt_unit_quality
                    run_mqtt_unit_quality(db,p,job)
                elif job.kind=='diagnose_data':
                    from .quality import run
                    status=run(db,p,job)
                elif job.kind=='route_data_gap':
                    from .data_gaps import route
                    status=route(db,p,job.data['gap_id'])
                elif job.kind=='recheck_data_gap':
                    from .data_gaps import recheck
                    recheck(db,p,job.data['gap_id'],job.data.get('trigger','manual'))
                elif job.kind == "dispatch_acquisition":
                    from .perception import dispatch_acquisition
                    dispatch_acquisition(db, p, job.data["acquisition_id"])
                    acquisition = scoped(db, Acquisition, job.data['acquisition_id'], p)
                    if acquisition.status=='cancelled':status='cancelled'
                    elif acquisition.status in {'unknown', 'blocked_connector', 'blocked_resource', 'rejected'}:
                        status, error = 'failed', acquisition.data.get('reason', '补采未完成，需要核查设备回执或接入配置')
                elif job.kind == "data_query":
                    from .agents import data_agent
                    # Classify before taking a Run lock. Automatic report
                    # preparation must acquire User before Run/Instance;
                    # ordinary V1 retains its original locked branch below.
                    row = scoped(db, DataRun, job.data["query_id"], p)
                    from .goal_evaluation_query_guard_v288 import reject_generic_write as reject_evaluation_write
                    reject_evaluation_write(db, p, row)
                    from . import source_query_service as source_queries
                    from . import pixel_question_service_v252 as pixel_questions
                    from . import multimodal_question_service_v262 as multimodal_questions
                    if multimodal_questions.has_trace(db,p,row.id):
                        result=multimodal_questions.process(db,p,job)
                        if result=='cancelled':status='cancelled'
                        elif result not in {'succeeded','needs_clarification'}:
                            status,error='failed',multimodal_questions.checked(db,p,scoped(db,DataRun,row.id,p)).get('error')
                    elif pixel_questions.has_trace(db,p,row.id):
                        result=pixel_questions.process(db,p,job)
                        if result=='cancelled':status='cancelled'
                        elif result not in {'succeeded','needs_clarification'}:
                            status,error='failed',pixel_questions.checked(db,p,scoped(db,DataRun,row.id,p)).get('error')
                    elif source_queries.is_v2(db,row,job=job):
                        from . import document_question_service as document_questions
                        if document_questions.is_record(db,row,job=job):
                            document_questions.lock_actor_for_dispatch(db,p,row)
                        else:
                            from .media_daily_task_adapter import current_actor as query_actor
                            query_actor(db,p,write=True)
                        row=scoped(db,DataRun,row.id,p,True)
                        source_queries.checked(db,p,row)
                        state=data_agent(db,p,row.data['request'],row.id,
                            cached_only=bool(job.data.get('recovery_only')),emit_gaps=False,learning=False)
                        if state.get('status')=='waiting_document_search':status='waiting_document_search'
                        row=scoped(db,DataRun,row.id,p,True)
                        if row.status=='cancelled':status='cancelled'
                        elif row.status=='failed':status,error='failed',row.data.get('error')
                    else:
                        row = scoped(db, DataRun, row.id, p, True)
                        if row.status=='cancelled':status='cancelled'
                        if row.status in {"queued", "running"}:
                            if job.data.get('recovery_only') and row.data['request'].get('mode')!='cloud' and not (
                                row.data['request'].get('tool_steps') and row.data.get('tool_context')):
                                raise ValueError('结构化查询已达到后台自动恢复次数上限')
                            state = data_agent(db, p, row.data["request"], row.id,learning=not bool(row.data.get('goal_delegation')),
                                **({'cached_only':True} if job.data.get('recovery_only') else {}))
                            row = scoped(db, DataRun, row.id, p, True)
                            if row.status=='cancelled':
                                status='cancelled'
                            else:
                                row.status = state["status"]; row.data = {**row.data,**state,
                                    'progress':{'stage':state['status'],'saved_at':now().isoformat()}}; row.version += 1
                            audit(db, p, "data.query_complete", row.id, {"status": row.status})
                        from .goal_queries import completed
                        completed(db,p,row)
                elif job.kind == 'generate_plan':
                    from .planning import run
                    result=run(db,p,job.data['plan_id'],cached_only=bool(job.data.get('recovery_only')))
                    if result in {'cancelled','superseded'}:status=result
                    elif result in {'blocked_model','blocked_evidence'}:
                        status='failed';error=scoped(db,Plan,job.data['plan_id'],p).data.get('error','规划受阻，请核查条件')
                elif job.kind == 'interpret_goal':
                    from .goals import interpret
                    interpret(db,p,job.data['task_id'],job.data['request'])
                elif job.kind == 'process_threshold_shadow_v151':
                    from .threshold_shadow_execution_v151 import process as process_threshold_shadow
                    result = process_threshold_shadow(db,p,job)
                    if result == 'stopped': status = 'cancelled'
                    elif result != 'completed':
                        status, error = 'failed', '影子观察未完成；正式处理与原占用回执保留，不自动重发'
                elif job.kind == 'evaluate_threshold_study_v149':
                    from .threshold_study_runner_v149 import process as process_threshold_study
                    result = process_threshold_study(db,p,job)
                    if result == 'cancelled': status = 'cancelled'
                    elif result != 'completed':
                        status, error = 'failed', '阈值比较已停止，原进度和回执保留，请查看评估说明'
                elif job.kind == "evaluate_learning":
                    from .learning import evaluate
                    try:
                        with db.begin_nested():
                            evaluate(db, p, job.data["run_id"])
                    except LeaseLost:
                        raise
                    except Exception:
                        from .models import EvaluationRun, LearningCandidate
                        row = scoped(db, EvaluationRun, job.data["run_id"], p, True)
                        row.status = "failed"; row.data = {**row.data, "error": "评测输入或依据失效，修复后请重新评测"}
                        candidate = scoped(db, LearningCandidate, row.candidate_id, p, True)
                        candidate.status = "failed"; candidate.version += 1
                        status, error = "failed", row.data["error"]
                elif job.kind == "propose_learning":
                    from .learning import propose_candidate
                    memory = scoped(db, Memory, job.data["memory_id"], p)
                    if memory.status == "trusted":
                        propose_candidate(db, p, memory.data["package_id"], [memory.id],
                                          "经验选择候选："+memory.data.get("goal", "已验收案例")[:140])
                else:
                    raise ValueError("不支持的任务类型")
                atomic_promotion=(job.kind=='promote_goal_evaluation_v299' and status=='succeeded')
                if atomic_promotion:
                    # Publish the pointer, its audits and the actual promoter
                    # terminal in this one existing fenced transaction.
                    from .worker_goal_promotion_atomic_v301 import stage_success
                    stage_success(db,p,job)
                db.commit()
                promotion_committed=atomic_promotion
    except MqttDiagnosticScopeMismatch:
        # The row's type/scope cannot be trusted. Do not run generic failure
        # handlers or recover_calls; leave the fenced lease for later review.
        return True
    except LeaseLost:
        dispatch(); return True
    except Exception as failure:
        from .worker_failure_diagnostics_v289 import capture_failure
        failure_diagnostic = capture_failure(failure)
        status, error = "failed", "后台执行已停止，请核查数据、能力及授权后恢复"
        from .workflow_abilities import WorkflowBlocked
        if isinstance(failure,WorkflowBlocked):error=failure.reason
        try:
            with (Session(engine(), expire_on_commit=False) as db,
                    local_goal_phase(db, job_id, token) as failure_phase):
                attach(db, job_id, token)
                job = db.get(Job, job_id)
                if isinstance(failure, HTTPException) and job.kind == 'capture_media':
                    error = '实例、视频来源或读取授权未通过核对，未继续读取；请核查后重新发起'
                elif isinstance(failure, HTTPException) and job.kind == 'process_video':
                    error = '视频处理的原来源、模型或业务授权未通过核对；原视频保留'
                recovery_p = Principal(job.data["actor_id"], job.workspace_id, "viewer", "worker-recovery")
                recover_calls(db, recovery_p, job)
                if job.kind == 'generate_same_goal_advice_v302':
                    # Release the recovery transaction's initial Job/SDK locks
                    # before fail_job takes Task -> Plan -> Job. Every later
                    # BEGIN and the final COMMIT retain the real lease fence.
                    db.commit();failure_phase.activate()
                from . import source_query_service as source_queries
                from . import multimodal_question_service_v262 as multimodal_questions
                if job.kind == 'business_automation_start':
                    from .business_automation_service import fail as fail_automation
                    fail_automation(db,recovery_p,job,error)
                elif job.kind == 'compare_pixel_change_v249':
                    from .pixel_change_runs_v249 import fail as fail_pixel_change
                    fail_pixel_change(db, recovery_p, job, error)
                elif job.kind=='data_query' and multimodal_questions.has_trace(db,recovery_p,job.data['query_id']):
                    multimodal_questions.fail(db,recovery_p,job,error)
                elif job.kind=='data_query' and source_queries.is_v2(db,scoped(db,DataRun,job.data['query_id'],recovery_p),job=job):
                    source_queries.fail(db,recovery_p,job,error)
                else:
                    fail_target(db, recovery_p, job, error)
                if job.kind in {'execute_workflow_step','execute_full_semantic_graph_node'}:
                    from .workflow_pixel_runs_v250 import record_parent_failure as fail_pixel_parent
                    fail_pixel_parent(db,recovery_p,job,error)
                if job.kind in {'generate_native_query_proposal','execute_native_task_query'}:
                    restored=(scoped(db,Plan,job.data['plan_id'],recovery_p)
                        if job.kind=='generate_native_query_proposal'
                        else scoped(db,DataRun,job.data['query_id'],recovery_p))
                    job.data={**job.data,'query_recovery':{'prior_error':error,
                        'target_status':restored.status,'recovered_at':now().isoformat()}}
                    if restored.status in {'needs_review','sufficient'}:status,error='succeeded',None
                    elif restored.status=='cancelled':status,error='cancelled',None
                    else:error=restored.data.get('error') or error
                if job.kind=='execute_native_goal_query':
                    restored=scoped(db,DataRun,job.data['query_id'],recovery_p)
                    job.data={**job.data,'native_query_recovery':{'prior_error':error,'query_status':restored.status,'recovered_at':now().isoformat()}}
                    if restored.status=='sufficient':status,error='succeeded',None
                    elif restored.status=='cancelled':status,error='cancelled',None
                    else:error=restored.data.get('error') or error
                if job.kind=='execute_native_step_plan':
                    from . import native_step_execution as native_execution
                    recovered=native_execution._run(scoped(db,Task,job.data['task_id'],recovery_p),job.data['plan_id'])
                    job.data={**job.data,'native_execution_recovery':{'prior_error':error,
                        'execution_status':recovered['status'],'recovered_at':now().isoformat()}}
                    if recovered['status'] in {'needs_review','accepted'}:status,error='succeeded',None
                    elif recovered['status']=='cancelled':status,error='cancelled',None
                    else:error=recovered.get('error') or error
                if job.kind=='generate_native_step_plan':
                    restored=scoped(db,Plan,job.data['plan_id'],recovery_p)
                    job.data={**job.data,'native_plan_recovery':{'prior_error':error,
                        'plan_status':restored.status,'recovered_at':now().isoformat()}}
                    if restored.status=='needs_review':status,error='succeeded',None
                    elif restored.status=='cancelled':status,error='cancelled',None
                    else:error=restored.data.get('error') or error
                if job.kind=='index_mqtt_daily':
                    from .mqtt_daily import fail_checkpoint
                    fail_checkpoint(db,job)
                db.commit()
        except LeaseLost:
            dispatch(); return True
    if promotion_committed:
        # This trusted local control flag is set only after commit succeeded.
        # The normal final session cannot re-attach a terminal running lease.
        dispatch()
        return True
    try:
        with Session(engine()) as db:
            attach(db, job_id, token)
            job = db.get(Job, job_id)
            _check_append_terminal_fence(db,job,status)
            from .auto_evaluation_recovery_v360 import rollout_enabled
            if rollout_enabled() and status == 'succeeded' and job.kind == 'check_native_business_goal':
                # Candidate source is revalidated by _candidate before using
                # its producer; ordinary goal checks have no candidate marker.
                from .goal_evaluation_maturity_v299 import KEY as CANDIDATE, _candidate
                if CANDIDATE in job.data:
                    from .models import Task
                    from .auto_evaluation_recovery_v360 import require_unfenced, Refused
                    principal=actor(db,job.workspace_id,job.data['actor_id'])
                    task=db.get(Task,job.data['task_id'])
                    candidate,_row=_candidate(db,principal,task,job)
                    try:
                        require_unfenced(db,workspace_id=job.workspace_id,
                            task_id=task.id,job_id=candidate['producer_job_id'])
                    except Refused:
                        raise LeaseLost() from None
            if status=='succeeded' and job.kind in {'prepare_geo_quality_v351','prepare_geo_quality_v352'}:
                from .geo_quality_v351 import finalize as finalize_geo_quality
                try:finalize_geo_quality(db,actor(db,job.workspace_id,job.data['actor_id']),job)
                except LeaseLost:raise
                except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
                    db.rollback();job=db.get(Job,job_id)
                    status='failed';error='质量结果或输出准备无法核对；原影像和原申请保留'
            if status=='succeeded' and job.kind=='settle_sound_result_v1':
                from .sound_result_settlement import finalize as finalize_sound_settlement
                try:finalize_sound_settlement(db,actor(db,job.workspace_id,job.data['actor_id']),job)
                except LeaseLost:raise
                except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
                    db.rollback();job=db.get(Job,job_id)
                    status='failed';error='原声音成果交接未完成；模型没有重新运行，请核对原交接申请'
            if status == 'succeeded' and job.kind in {'execute_workflow_step','execute_full_semantic_graph_node'}:
                # Child availability and original success become visible together.
                # A separate worker cannot observe an unfinalized original Job.
                from .worker_native_audio_v136 import enqueue_completed
                receipt_actor = Principal(job.data['actor_id'],job.workspace_id,'operator','native-receipt-outbox')
                from .models import WorkflowPixelRun
                pixel_owned=db.scalar(select(WorkflowPixelRun.id).where(WorkflowPixelRun.workspace_id==job.workspace_id,
                    WorkflowPixelRun.job_id==job.id).limit(1)) is not None
                from .sound_models import EnvironmentSoundRun
                sound_owned=db.scalar(select(EnvironmentSoundRun.id).where(EnvironmentSoundRun.workspace_id==job.workspace_id,
                    EnvironmentSoundRun.job_id==job.id).limit(1)) is not None
                if sound_owned:
                    from .native_sound_receipts import emit_completed as emit_sound_completed
                    try:emit_sound_completed(db,receipt_actor,job)
                    except LeaseLost:raise
                    except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
                        db.rollback();job=db.get(Job,job_id)
                        status='failed';error='声音模型结果已保留，但业务资料交接未完成，请从原任务核对原结果'
                        from .sound_result_settlement import record_parent_failure
                        try:
                            with db.begin_nested():record_parent_failure(db,receipt_actor,job,error)
                        except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
                            pass  # Missing independent failure proof stays blocked; never invent it later.

                elif pixel_owned:
                    from .native_pixel_receipts_v250 import emit_completed as emit_pixel_completed
                    try:
                        emit_pixel_completed(db,receipt_actor,job)
                    except LeaseLost:
                        raise
                    except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
                        # The model and original step may already be durable.
                        # Preserve them; fail this exact producer Job under its
                        # original lease rather than inventing an ACK or leaving
                        # a stranded running row. Explicit settlement is separate.
                        db.rollback()
                        job=db.get(Job,job_id)
                        status='failed';error='本地分析结果已保留，但业务成果交接未完成，请从原任务核对并恢复交接'
                elif not enqueue_completed(db,receipt_actor,job) and job.kind == 'execute_workflow_step':
                    from .native_receipts import enqueue_task
                    enqueue_task(db,receipt_actor,job)
            if status == 'succeeded' and job.kind == 'process_video':
                # Native reception must not race an unfinalized processing Job.
                from .native_receipts import enqueue_video
                enqueue_video(db,Principal(job.data['actor_id'],job.workspace_id,'operator','native-video-outbox'),job)
            if status == 'succeeded' and job.kind == 'process_reported_observation':
                # The original success and its shadow admission are one outbox
                # transaction. A bad shadow authority cannot undo formal work.
                from .threshold_shadow_execution_v151 import enqueue_completed
                try:
                    with db.begin_nested():
                        enqueue_completed(db,actor(db,job.workspace_id,job.data['actor_id']),job)
                except LeaseLost:
                    raise
                except (HTTPException, ValueError, KeyError, TypeError, AttributeError):
                    shadow_actor=Principal(job.data['actor_id'],job.workspace_id,'viewer','shadow-outbox')
                    audit(db,shadow_actor,'threshold_shadow.admission_blocked_v151',job.data['observation_id'],
                        {'parent_job_id':job.id,'reason':'候选影子授权、范围或额度不可用，正式处理保留，未安排候选'})
            # Lease fencing must see the original running row until before_commit;
            # enqueue queries must not autoflush this final status prematurely.
            if status == 'succeeded' and job.kind in {'sample_simulation_v176','recover_simulation_sample_v176'}:
                from .simulation_loop_v176 import finalize_sample
                finalize_sample(db,job)
            if status == 'succeeded' and job.kind in {'dispatch_acquisition','read_http_observation_receipt_v177'}:
                from .http_observation_worker_v177 import finalize_parent
                db.info['worker_actor']=actor(db,job.workspace_id,job.data['actor_id'])
                finalize_parent(db,job)
            if status=='failed' and job.kind=='create_signal_approval_draft_v247':
                from .signal_approval_routing_v247 import failed_attempt
                try:
                    with db.begin_nested():failed_attempt(db,job)
                except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
                    pass  # The original failed Job remains, never repair its proof.
            if status=='failed' and job.kind=='settle_sound_result_v1':
                from .sound_result_settlement import record_failure as record_sound_settlement_failure
                try:
                    with db.begin_nested():record_sound_settlement_failure(db,actor(db,job.workspace_id,job.data['actor_id']),job,error)
                except (HTTPException,ValueError,KeyError,TypeError,AttributeError,OSError):
                    pass  # An unproven failed attempt is not automatically retried or treated as settled.
            if status == 'failed' and failure_diagnostic is not None:
                # The original attempt, independent diagnostic Audit and final
                # failure become durable together under the original attach()
                # fence. This records no successful call and permits no retry.
                from .models import Audit
                from .worker_failure_diagnostics_v289 import attempt_record, KEY, ACTION
                diagnostic = attempt_record(job.id, job.workspace_id, job.attempts, failure_diagnostic)
                diagnostic_audit_id = uid()
                db.add(Audit(id=diagnostic_audit_id, workspace_id=job.workspace_id,
                    actor_id=job.data['actor_id'], action=ACTION, object_id=job.id, data=diagnostic))
                job.data = {**job.data, KEY: {**diagnostic, 'audit_id': diagnostic_audit_id}}
            job.status = status; job.lease_until = None
            job.data = {**job.data, "finished_at": now().isoformat(), **({"error": error} if error else {})}
            if status == 'succeeded' and job.kind == 'check_native_business_goal':
                from .goal_evaluation_maturity_v299 import enqueue_promotion_after_success
                with db.no_autoflush:
                    enqueue_promotion_after_success(db,actor(db,job.workspace_id,job.data['actor_id']),job)
            finished_kind=job.kind
            finished_workspace=job.workspace_id
            db.commit()
    except LeaseLost:
        pass
    if finished_kind=='search_runtime_documents':
        # The child must be durably terminal before a parent may be woken.
        # Failure of this optional hint never rewrites the original Search;
        # bounded typed polling repairs a lost hint after restart.
        from .document_question_service import child_finished
        try:child_finished(finished_workspace,job_id)
        except (HTTPException,DBAPIError,ValueError,KeyError,TypeError,AttributeError):pass
    dispatch()
    if finished_kind=='submit_mqtt_receipt':
        with Session(engine()) as db:
            finished=db.get(Job,job_id)
            p=Principal(finished.data['actor_id'],finished.workspace_id,'viewer','mqtt-daily-index')
            from .mqtt_daily import index_delivery,index_legacy,publish_ledger
            from .mqtt_binding_routes import find_binding
            from .models import MqttReceiptDelivery,MqttReceipt
            if finished.data.get('delivery_id'):
                original_delivery=db.scalar(select(MqttReceiptDelivery).where(MqttReceiptDelivery.workspace_id==p.workspace_id,
                    MqttReceiptDelivery.receipt_id==finished.data['receipt_id'],
                    MqttReceiptDelivery.binding_id==finished.data['binding_id']))
                if original_delivery:ledger=index_delivery(db,p,original_delivery)
                else:ledger=None
            else:
                ledger=index_legacy(db,p,scoped(db,MqttReceipt,finished.data['receipt_id'],p),
                    find_binding(db,p,finished.data['binding_id']))
            if ledger:publish_ledger(db,ledger)
            db.commit()
    if local_workspace:
        with Session(engine()) as db:
            job=db.get(Job,job_id);observation=db.get(Observation,job.data['observation_id'])
            p=Principal(job.data['actor_id'],local_workspace,'viewer','local-data-receiver')
            is_mqtt=job.kind=='process_mqtt_observation'
            daily_source=observation.source_id;daily_day=observation.business_day
            if is_mqtt:
                from .mqtt_observations import reconcile
            else:
                from .local_observations import reconcile
            delivery_identity=job.data['observation_id'] if job.kind in {'process_reported_observation','process_mqtt_observation'} else None
            if is_mqtt:reconcile(db,p,observation.source_id,observation.business_day,observation_id=observation.id)
            else:reconcile(db,p,observation.source_id,observation.business_day)
            db.commit()
        deliver_events(workspace_id=local_workspace,observation_id=delivery_identity)
        if is_mqtt:deliver_events(workspace_id=local_workspace,event_kind='perception.daily',event_schema='mqtt-inbox-daily-v2')
    return True



def observation_derivation_hold(db,event,original):
    """A paused generated instance can receive facts, but start no new work."""
    if original.entity_id is None:return None  # Preserve the separate legacy path.
    p=Principal(original.data['submitted_by'],event.workspace_id,'viewer','observation-receiver')
    from .observation_candidate_dispatch import is_audio,verify_candidate
    if is_audio(db,p,original):
        verify_candidate(db,p,original,original.data['extractor_id'],execution=False)
        value={'schema':'observation-derivation-skipped-v1','event_id':event.id,'observation_id':original.id,
            'code':'gateway_audio_candidate_only','reason':'设备音轨只形成待核查的声音候选，原始采集时间仍未知。',
            'skipped':['data_gaps','state_refresh','goal_queries','graph_sources','anomalies','candidate_watches','quality'],
            'recorded_at':now().isoformat(),'cloud_calls':0,'device_actions':False,'current_state_updated':False}
        value['receipt_hash']=digest(value);audit(db,p,'observation.derivation_skipped',event.id,value)
        return value
    from .native_sound_receipts import handles as sound_receipt
    if sound_receipt(db,p,original):
        from .native_sound_provenance import checked_observation as check_sound
        check_sound(db,p,original,event)
        value={'schema':'observation-derivation-skipped-v1','event_id':event.id,'observation_id':original.id,
            'code':'environment_sound_candidate_only','reason':'环境声音只形成候选，未确认现场状态或业务异常；请在原任务核查原音。',
            'skipped':['data_gaps','state_refresh','goal_queries','anomalies','quality'],
            'recorded_at':now().isoformat(),'cloud_calls':0,'device_actions':False,'current_state_updated':False}
        value['receipt_hash']=digest(value);audit(db,p,'observation.derivation_skipped',event.id,value)
        return value
    from .original_acquisition_reception_v127 import hold as original_reception_hold
    restriction=original_reception_hold(db,p,original,remember=True)
    if restriction:
        proof=restriction['proof']
        receipt={'schema':'observation-derivation-skipped-v1','event_id':event.id,'observation_id':original.id,
            'instance_id':proof['instance_id'],'source_release_id':proof['release_id'],
            'code':restriction['code'],'reason':restriction['reason'],
            'original_reception_restriction_hash':restriction['restriction_hash'],
            'original_reception_proof':proof,
            'skipped':['data_gaps','state_refresh','goal_queries','graph_sources','anomalies','candidate_watches','quality'],
            'recorded_at':now().isoformat(),'cloud_calls':0,'device_actions':False,'current_state_updated':False}
        receipt['receipt_hash']=digest(receipt)
        audit(db,p,'observation.derivation_skipped',event.id,receipt)
        return receipt
    entity=scoped(db,Entity,original.entity_id,p)
    from .instance_domain import generated_release
    source=generated_release(db,p,entity)  # Ambiguity/integrity errors stay explicit.
    if source is None:return None
    from .models import DomainInstance
    instance=scoped(db,DomainInstance,source.instance_id,p,True)
    if instance.status=='partially_active' and instance.active_release_id:return None
    receipt={'schema':'observation-derivation-skipped-v1','event_id':event.id,'observation_id':original.id,
        'instance_id':instance.id,'source_release_id':source.id,'active_release_id':instance.active_release_id,
        'instance_status':instance.status,'code':'instance_not_active',
        'reason':'原登记或处理失败已接收；实例尚未启用或已暂停，未启动缺口核查、状态刷新或目标推进。',
        'skipped':['data_gaps','state_refresh','goal_queries'],'recorded_at':now().isoformat(),
        'cloud_calls':0,'device_actions':False,'current_state_updated':False}
    receipt['receipt_hash']=digest(receipt)
    audit(db,p,'observation.derivation_skipped',event.id,receipt)
    return receipt


def enqueue_graph_event(db, event):
    """Independent graph fan-out; preserve the original receiver and event body."""
    try:
        with db.begin_nested():
            from .workflow_graph import enqueue_event as graph_event
            return graph_event(db,event)
    except Exception as failure:
        # Do not append diagnostics to Event.data: that is the exact payload
        # already frozen by graph recipients. Keep one independent work item.
        prior = db.scalar(select(Memory.id).where(Memory.workspace_id == event.workspace_id,
            Memory.data['type'].as_string() == 'graph_event_delivery_blocked',
            Memory.data['event_id'].as_string() == event.id))
        if prior is None:
            db.add(Memory(workspace_id=event.workspace_id, status='candidate',
                text='业务等待事件未能排入接收任务；原事件及现有接收结果保留，请核查后恢复',
                data={'type':'graph_event_delivery_blocked','event_id':event.id,
                    'event_kind':event.kind,'error_kind':type(failure).__name__,
                    'not_authoritative':True}))
        return 0


def deliver_events(workspace_id=None,observation_id=None,*,event_kind=None,source_id=None,business_day=None,event_schema=None,packet_receipt_id=None):
    if packet_receipt_id is not None and (not workspace_id or event_kind != "perception.sensor_packet" or not isinstance(packet_receipt_id,str) or not packet_receipt_id):
        raise ValueError("A packet receipt requires a workspace and sensor packet event kind")
    # Both receiver action and acknowledgement share one transaction.
    auto_ack_budget={'remaining':2.0,'deferred_overflows':[]}
    from .sound_candidate_watches import EVENT_SCHEMA,ISSUED,deliver_pending as deliver_sound
    if (packet_receipt_id is None and workspace_id is not None and observation_id is None and event_kind in {None,'perception.candidate'}
        and event_schema in {None,EVENT_SCHEMA} and source_id is None and business_day is None):
        deliver_sound(_workspace_uuid(workspace_id))
    with Session(engine()) as db:
        from .models import Audit
        sound_event_audit=select(Audit.id).where(Audit.workspace_id==Event.workspace_id,
            Audit.action==ISSUED,Audit.object_id==Event.id).exists()
        query=select(Event).where(Event.status == "pending_delivery",
            func.coalesce(Event.data['schema'].as_string(),'')!=EVENT_SCHEMA,
            ~Event.idempotency_key.startswith('candidate-sound:'),~sound_event_audit)
        if workspace_id is None:
            audio_event_audit=select(Audit.id).where(Audit.workspace_id==Event.workspace_id,Audit.object_id==Event.id,
                Audit.action.in_(['audio.stream_daily_frozen_v255','audio.stream_candidate_issued_v255'])).exists()
            audio_origin_audit=select(Audit.id).where(Audit.workspace_id==Event.workspace_id,
                Audit.action=='audio.stream_capture_saved_v255',
                Audit.object_id==Event.data['observation_id'].as_string()).exists()
            query=query.where(Event.kind!='perception.audio_candidate',
                func.coalesce(Event.data['schema'].as_string(),'').not_in(['gateway-audio-daily-v255','gateway-audio-candidate-v255']),
                ~Event.idempotency_key.startswith('audio-stream-daily:'),
                ~Event.idempotency_key.startswith('audio-stream-candidate:'),~audio_event_audit,~audio_origin_audit)
        if workspace_id is not None:query=query.where(Event.workspace_id==workspace_id)
        if observation_id is not None:query=query.where(Event.data["observation_id"].as_string()==observation_id)
        if packet_receipt_id is not None:query=query.where(Event.data['receipt_id'].as_string()==packet_receipt_id)
        if event_kind is not None:query=query.where(Event.kind==event_kind)
        if source_id is not None:query=query.where(Event.data['source_id'].as_string()==source_id)
        if business_day is not None:query=query.where(Event.data['business_day'].as_string()==business_day)
        if event_schema is not None:query=query.where(Event.data['schema'].as_string()==event_schema)
        events = db.scalars(query.order_by(Event.created_at).with_for_update(skip_locked=True).limit(100)).all()
        for event in events:
            derivation_hold=None
            acknowledged=False
            from .audio_stream_delivery import handles_event as audio_candidate_event,handles_daily as audio_daily_event,event_delivery_state
            audio_receiver=Principal(event.data.get('producer_actor_id','audio-receiver'),event.workspace_id,'viewer','audio-event-receiver')
            audio_notification=audio_candidate_event(db,audio_receiver,event)
            audio_daily=audio_daily_event(db,audio_receiver,event)
            if audio_notification or audio_daily:
                try:
                    if event_delivery_state(db,audio_receiver,event)=='pending_producer':continue
                    if ((audio_notification and event.kind!='perception.audio_candidate')
                            or (audio_daily and event.kind!='perception.daily')):raise ValueError('音轨消息类型与独立签发依据不符')
                    derivation_hold={'code':'gateway_audio_notification_only','graph_sources':False,'device_actions':False}
                except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                    from .audio_stream_delivery import block_delivery
                    block_delivery(db,event,'原音轨消息的签发依据未通过核对，原记录保留')
                    continue
            if event.kind=='data.geo_quality_report_v352':
                from .geo_quality_reception_v352 import receive as receive_geo_quality
                try:
                    with db.begin_nested():receive_geo_quality(db,event)
                except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                    event.status='blocked_delivery';event.attempts+=1
                    continue
                derivation_hold={'code':'quality_report_only','graph_sources':False,'device_actions':False}
            elif event.kind=='perception.sensor_packet':
                from .mqtt_data import receive_packet
                try:
                    with db.begin_nested():receive_packet(db,event)
                except (HTTPException,KeyError,ValueError,TypeError,OSError):
                    event.status='blocked_delivery';event.attempts+=1
                    event.data={**event.data,'delivery_error':'原件或接收依据核对未通过，请恢复原件后重试'}
                    from .mqtt_daily import index_original,internal
                    from .models import MqttReceipt
                    index_original(db,internal(event.workspace_id),scoped(db,MqttReceipt,event.data['receipt_id'],internal(event.workspace_id)),blocked=True)
                    continue
            elif event.kind=='data.gap':
                from .data_gaps import receive
                if not receive(db,event):continue
            elif event.kind=='perception.acquisition_escalation':
                from .sensing_selection import receive_escalation
                try:
                    with db.begin_nested():outcome=receive_escalation(db,event)
                except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                    event.status='blocked_delivery';event.attempts+=1
                    event.data={**event.data,'delivery_error':'原补采协调与业务依据无法核对，原消息保留，请核查后重试'}
                    continue
                if not outcome:continue
                if type(outcome) is dict:derivation_hold=outcome
            elif event.kind=='data.gap_coordination':
                from .data_gaps import coordinate
                try:
                    with db.begin_nested():outcome=coordinate(db,event)
                except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                    event.status='blocked_delivery';event.attempts+=1
                    event.data={**event.data,'delivery_error':'原资料缺口协调与业务依据无法核对，原消息保留，请核查后重试'}
                    continue
                if not outcome:continue
                if type(outcome) is dict:derivation_hold=outcome
            elif event.kind=='business.continuation_read_result_v314':
                from .same_goal_continuation_result_receiver_v314 import receive as receive_continuation
                try:
                    with db.begin_nested():received=receive_continuation(db,event)
                except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                    event.status='blocked_delivery';event.attempts+=1
                    continue
                if not received:continue
                acknowledged=True
            elif event.kind in {"observation.data", "observation.failed"}:
                original = db.get(Observation,event.data.get('observation_id')) if event.data.get('observation_id') else None
                if not original or original.workspace_id!=event.workspace_id or original.entity_id!=event.data.get('entity_id'):
                    event.status='blocked_delivery';event.attempts+=1;continue
                from .graph_event_native_learned_geo_capture_v141 import handles as learned_receipt, receive as receive_learned
                try: learned = event.kind == 'observation.data' and learned_receipt(db,event,original)
                except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                    event.status='blocked_delivery';event.attempts+=1;continue
                if learned:
                    try:
                        with db.begin_nested():
                            derivation_hold=observation_derivation_hold(db,event,original)
                            receive_learned(db,event,original,derivation_hold=derivation_hold)
                        acknowledged=True
                    except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                        event.status='blocked_delivery';event.attempts+=1
                        event.data={**event.data,'delivery_error':'原变化候选的数据接收或来源授权未通过核对；原模型结果保留，请恢复依据后核查'}
                        continue
                elif event.kind=='observation.data':
                    from .instance_data_receipts import receive_observation
                    receiver=Principal(original.data['submitted_by'],event.workspace_id,'viewer','data-plan-receiver')
                    try:
                        with db.begin_nested():
                            reception=receive_observation(db,receiver,event,original)
                    except (HTTPException,KeyError,ValueError,TypeError,OSError):
                        event.status='blocked_delivery';event.attempts+=1
                        event.data={**event.data,'delivery_error':'自动数据方案或原观测回执无法核对，原件与处理记录保留，请核查后重试'}
                        continue
                    if reception is not None:event.data={**event.data,'data_reception':reception}
                if not learned:
                    derivation_hold=observation_derivation_hold(db,event,original)
                    if derivation_hold:event.data={**event.data,'derivation_receipt':derivation_hold}
                    db.add(Memory(workspace_id=event.workspace_id, status="candidate",
                        text="观测无法提取，需要核查" if event.kind == "observation.failed" else "已接收观测提取结果，等待事实核实",
                        data={**event.data, "event_id": event.id,
                        "type": "observation_failure" if event.kind == "observation.failed" else "observation_evidence",
                        "not_authoritative": True}))
            elif event.kind=='perception.audio_candidate':
                from .audio_stream_delivery import receive as receive_audio_candidate
                try:
                    with db.begin_nested():received=receive_audio_candidate(db,event)
                    if not received:continue
                except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                    from .audio_stream_delivery import block_delivery
                    block_delivery(db,event,'音轨候选的原音、模型或独立收件依据未通过核对，请保留记录核查')
                    continue
            elif event.kind in {"perception.anomaly","perception.candidate"}:
                entity = db.get(Entity, event.data["entity_id"])
                observation = db.get(Observation, event.data["observation_id"])
                if not entity or not observation or entity.workspace_id!=event.workspace_id or observation.workspace_id!=event.workspace_id or observation.entity_id!=entity.id:
                    event.status='blocked_delivery';event.attempts+=1;continue
                derivation_hold=observation_derivation_hold(db,event,observation)
                if derivation_hold:
                    # Scope can close after extraction queued this anomaly.
                    # Retain the original Event; its Observation/data receipt
                    # keeps the result while no new business work is started.
                    event.data={**event.data,'derivation_receipt':derivation_hold}
                else:
                    from .perception_signals import receive
                    if not receive(db,event):
                        db.add(Task(workspace_id=event.workspace_id, title="核查观测异常：" + entity.external_id,
                            package_id=entity.schema_capability_id, owner_id=observation.data["submitted_by"],
                            status="draft", data={"trigger": "perception.anomaly", "event_id": event.id,
                            "entity_ids": [entity.id], "evidence": event.data, "fields": []}))
            elif event.kind == "perception.daily":
                if audio_daily:
                    from .audio_stream_delivery import validate_daily as validate_audio_daily
                    try:validate_audio_daily(db,event)
                    except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                        from .audio_stream_delivery import block_delivery
                        block_delivery(db,event,'音轨每日整理的原冻结版本未通过核对，请保留记录核查')
                        continue
                if event.data.get('schema') == 'workspace-observation-daily-v204':
                    from .workspace_observation_intake import validate_daily
                    try:validate_daily(db,event)
                    except (HTTPException,KeyError,ValueError,TypeError,OSError):
                        event.status='blocked_delivery';event.attempts+=1
                        event.data={**event.data,'delivery_error':'每日登记整理的冻结依据无法核对；原记录保留，请核查'}
                        continue
                if event.data.get('schema') in {'mqtt-inbox-daily-v2','mqtt-daily-v2'}:
                    from .mqtt_daily import validate_v2
                    try:validate_v2(db,event)
                    except (HTTPException,KeyError,ValueError,TypeError,OSError):
                        event.status='blocked_delivery';event.attempts+=1
                        event.data={**event.data,'delivery_error':'每日整理的冻结修订未通过核对，请保留原件核查'}
                        continue
                if event.data.get('schema')=='mqtt-inbox-daily-v1':
                    from .mqtt_data import validate_summary
                    try:validate_summary(db,event)
                    except (HTTPException,KeyError,ValueError,TypeError,OSError):
                        event.status='blocked_delivery';event.attempts+=1
                        event.data={**event.data,'delivery_error':'传感原件整理清单未通过核对，请核查原件'}
                        continue
                daily_text="本地资料每日整理已保存（采集时间待核）" if event.data.get("schema") in {"local-file-daily-v1","native-multimodal-daily-v1"} else "每日观测归集完成"
                if event.data.get('schema') == 'workspace-observation-daily-v204':
                    daily_text=('每日登记资料已保存（含失败、待处理或日期待核项）' if event.data['status']=='incomplete'
                        else '每日登记资料已整理（保存记录候选，非现场核验）')
                if event.data.get('schema')=='mqtt-daily-v1':
                    daily_text='传感数据每日整理已更新（设备采集时间声明，仍须核查）' if event.data.get('status')=='collected_with_limits' else '传感数据每日整理含失败或待处理数据，请核查'
                if event.data.get('schema')=='mqtt-inbox-daily-v1':
                    daily_text='采集时间未知的传感原件已列入待核对清单' if event.data['business_day'] is None else '传感原件每日清单已保存（含未绑定和待核查数据）'
                if event.data.get('schema') in {'mqtt-inbox-daily-v2','mqtt-daily-v2'}:
                    daily_text='传感每日整理修订已保存（原件与独立处理分别计数；非现场事实核验）'
                if audio_daily:daily_text='设备音轨每日整理已保存（原音、处理与独立收件分别计数；声音分类为待核查候选）'
                db.add(Memory(workspace_id=event.workspace_id, status="candidate",
                    text=daily_text, data={**event.data, "event_id": event.id, "type": "daily_summary"}))
            else:
                # Graph-only event kinds have their own durable recipients;
                # unmatched events keep the previous pending-delivery behavior.
                if enqueue_graph_event(db,event):
                    event.status = 'received'; event.received_at = now(); event.attempts += 1
                continue
            if not acknowledged:
                if event.kind=='observation.data':
                    from .auto_evaluation_ack_witness_v356 import reception,capture_enabled_for_ack
                    with reception(db,event):
                        event.status = "received"; event.received_at = now(); event.attempts += 1
                        # The original receipt and Memory must be durable within
                        # this same transaction before checking any policy.
                        db.flush()
                        capture_enabled_for_ack(db,event,original,restricted=derivation_hold is not None,budget=auto_ack_budget)
                else:
                    event.status = "received"; event.received_at = now(); event.attempts += 1
            if event.kind=='observation.data':
                db.flush()
                from .audio_stream_delivery import enqueue_received as queue_audio_delivery
                audio_actor=Principal(original.data['submitted_by'],event.workspace_id,'viewer','audio-data-ack')
                try:
                    with db.begin_nested():queue_audio_delivery(db,audio_actor,original)
                except (HTTPException,KeyError,TypeError,ValueError,AttributeError,OSError):
                    audit(db,audio_actor,'audio.stream_delivery_enqueue_blocked_v255',original.id,
                        {'event_id':event.id,'reason':'原数据接收已保存；音轨每日整理或业务通知尚未排队，请核对原音轨记录'})
                from .sound_daily import refresh_received as refresh_sound_daily
                refresh_sound_daily(db,Principal(original.data['submitted_by'],event.workspace_id,'viewer','sound-data-ack'),original)
                from .sound_candidate_watches import enqueue_received as queue_sound_watches
                sound_actor=Principal(original.data['submitted_by'],event.workspace_id,'viewer','sound-data-ack')
                try:
                    with db.begin_nested():queue_sound_watches(db,sound_actor,original)
                except (HTTPException,KeyError,TypeError,ValueError,AttributeError,OSError):
                    audit(db,sound_actor,'sound.candidate_enqueue_blocked_v1',original.id,
                        {'event_id':event.id,'reason':'已保存原数据接收；声音关注检查待核对，未生成通知'})
            if event.kind=='observation.failed':
                from .observation_candidate_dispatch import is_audio
                failed_audio=Principal(original.data['submitted_by'],event.workspace_id,'viewer','audio-failure-receiver')
                if is_audio(db,failed_audio,original):
                    try:
                        with db.begin_nested():
                            from .audio_streams import observation_context
                            from .audio_stream_delivery import enqueue_snapshot
                            captured=observation_context(db,failed_audio,original,execution=False)
                            enqueue_snapshot(db,failed_audio,captured['stream'],original.id)
                    except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
                        audit(db,failed_audio,'audio.stream_snapshot_enqueue_blocked_v255',original.id,
                            {'event_id':event.id,'reason':'原处理失败记录保留；完整日清单修订尚待核对'})
            if event.kind=='observation.failed' and derivation_hold is None:
                from .state_refresh import source_changed
                failed_input=db.get(Observation,event.data['observation_id'])
                failed_actor=Principal(failed_input.data['submitted_by'],event.workspace_id,'viewer','failed-input-receiver')
                source_changed(db,failed_actor,failed_input,'新登记处理失败，需要核对原输入')
            if event.kind=='observation.data' and derivation_hold is None:
                from .native_pixel_daily_v250 import refresh_received as refresh_pixel_daily
                refresh_pixel_daily(db,Principal(original.data['submitted_by'],event.workspace_id,'viewer','pixel-data-ack'),original)
                from .acquisition_ack_settlement_v245 import enqueue as queue_acquisition_settlement
                try:
                    with db.begin_nested():
                        queue_acquisition_settlement(db,
                            Principal(original.data['submitted_by'],event.workspace_id,'viewer','data-ack-settlement'),event,original)
                except (HTTPException,KeyError,TypeError,ValueError,AttributeError,OSError):
                    # The independent data receipt remains truthful even when
                    # original action settlement needs operator repair.
                    audit(db,Principal(original.data['submitted_by'],event.workspace_id,'viewer','data-ack-settlement'),
                        'acquisition.data_ack_settlement_blocked_v245',original.id,{'event_id':event.id})
                from .data_gaps import changed
                p=Principal(event.data.get('actor_id') or db.get(Observation,event.data['observation_id']).data['submitted_by'],
                    event.workspace_id,'viewer','data-gap-receiver')
                changed(db,p,event.data['entity_id'],'observation:'+event.data['observation_id'])
                from .signal_approval_routing_v247 import wake_for_observation
                wake_for_observation(db,event.workspace_id,event.data['observation_id'])
                from .state_refresh import source_changed
                source_changed(db,p,db.get(Observation,event.data['observation_id']))
                from .goal_queries import source_received
                source_received(db,p,event.data['observation_id'],event.data['entity_id'])
            # Legacy receivers may append reception/derivation metadata above.
            # Freeze the actual final payload, in a separate savepoint, after
            # those changes so graph event_hash cannot be stale on insertion.
            if derivation_hold is None:
                enqueue_graph_event(db,event)
        if auto_ack_budget['deferred_overflows']:
            db.flush()
            from .auto_evaluation_ack_witness_v356 import flush_deferred_overflows
            flush_deferred_overflows(db,auto_ack_budget)
        db.commit()


def daily_reconcile():
    """Reconcile recent business days; late observations create a new receipt revision."""
    with Session(engine()) as db:
        sources = db.scalars(select(Capability).where(Capability.kind == "source", Capability.status == "published")).all()
        for source in sources:
            if source.namespace=='platform_mqtt' or source.data.get('specification',{}).get('mqtt_binding_id'):continue
            from .simulation_loop_v176 import simulated_source
            if source.data.get('specification',{}).get('simulation_environment_id') or simulated_source(db,source.id):continue
            tz = ZoneInfo(source.data.get("specification", {}).get("timezone", "UTC"))
            yesterday = (now().astimezone(tz) - timedelta(days=1)).date()
            for offset in range(7):
                day = str(yesterday - timedelta(days=offset))
                observations = db.scalars(select(Observation).where(Observation.workspace_id == source.workspace_id,
                    Observation.source_id == source.id, Observation.business_day == day,
                    Observation.data['candidate_only'].as_boolean().is_distinct_from(True))).all()
                if not observations:
                    continue
                received = sorted(o.id for o in observations if o.status == "processed")
                failed = sorted(o.id for o in observations if o.status == "failed")
                pending = sorted(o.id for o in observations if o.status == "received")
                value = {"source_id": source.id, "business_day": day, "processed_ids": received,
                         "failed_ids": failed, "pending_ids": pending,
                         "status": "incomplete" if failed or pending else "complete"}
                key = "daily:"+source.id+":"+day+":"+digest(value)
                db.execute(insert(Event).values(workspace_id=source.workspace_id, kind="perception.daily",
                    idempotency_key=key, data=value).on_conflict_do_nothing(
                    index_elements=["workspace_id", "idempotency_key"]))
        db.commit()


def run():
    last_reconcile = 0
    last_database_notice = float('-inf')
    print("持久任务处理器已启动；退出后任务仍保存在数据库", flush=True)
    while True:
        try:
            if time.monotonic() - last_reconcile > 60:
                from .quality import schedule_once
                from .audio_daily import schedule_once as schedule_audio_daily
                from .geo_daily import schedule_once as schedule_geo_daily
                daily_reconcile(); schedule_audio_daily(); schedule_geo_daily(); monitor_once(); schedule_once(); last_reconcile = time.monotonic()
                from .mqtt_daily import schedule_once as schedule_mqtt_daily
                schedule_mqtt_daily()
                from .simulation_loop_v176 import schedule_once as schedule_simulation, deliver_pending as deliver_simulation
                schedule_simulation(); deliver_simulation()
            if not process_once():
                time.sleep(1)
        except DBAPIError:
            if time.monotonic() - last_database_notice > 30:
                print('数据库连接暂时不可用；保留持久任务并等待恢复', flush=True)
                last_database_notice = time.monotonic()
            time.sleep(2)


def monitor_once():
    from .perception import propose_acquisitions
    from .workflow_monitor_schedule_v200 import consume_policy_tick
    with Session(engine()) as db:
        ids = db.scalars(select(MonitorPolicy.id).where(MonitorPolicy.status == "active")).all()
    for policy_id in ids:
        with Session(engine()) as db:
            policy = db.get(MonitorPolicy, policy_id)
            try:
                p = actor(db, policy.workspace_id, policy.approved_by)
                if not consume_policy_tick(db, p, policy):
                    propose_acquisitions(db, p, policy.id)
                db.commit()
            except Exception:
                db.rollback()


def run_setup_job(job_id):
    """Bounded operator attempt using the ordinary setup claim/execution path.

    Exit 0 confirms the selected Job is persisted as succeeded; 3 means no claim.
    All other outcomes return 1. No task is requeued and no scheduler is started.
    """
    # Defend direct callers as well: a missing ID must never turn into a global
    # claim through process_once's backwards-compatible default argument.
    try:
        if not isinstance(job_id, str) or str(UUID(job_id)) != job_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("单任务执行必须提供标准小写 UUID") from None
    print("配置资料处理器仅尝试指定任务一次；不运行全局调度", flush=True)
    try:
        from .report_evidence_worker_isolation import KIND as SCOPED_REPORT_KIND
        worked=process_once(job_id=job_id,kinds=[SCOPED_REPORT_KIND],deliver=False)
        if not worked:
            worked=process_once(job_id=job_id,kinds=SETUP_KINDS,deliver=False)
        if not worked:
            print("未领取指定任务：不存在、已终结、未到执行时间、租约有效或类型不支持；未领取其他任务", flush=True)
            return 3
        # process_once returning True means an attempt, including failed or lost
        # leases. Read durable state before describing this attempt as successful.
        with Session(engine()) as db:
            job = db.get(Job, job_id)
            status = job.status if job else None
    except DBAPIError:
        print("指定任务处理或状态回查遇到数据库错误；无法确认成功，未自动重试", flush=True)
        return 1
    except Exception:
        print("指定任务处理或状态回查异常；无法确认成功，未自动重试", flush=True)
        return 1
    if status == "succeeded":
        print("指定任务已处理；持久状态为 succeeded", flush=True)
        return 0
    print("指定任务已尝试处理；持久状态为 " + (status or "无法读取") + "，未确认成功", flush=True)
    return 1


def run_setup(workspace_id):
    """Run all setup kinds for one explicit workspace until SIGTERM/SIGINT.

    Stop requests prevent the next root/claim. The current bounded root or Job
    may finish normally; a forced exit relies on the existing durable lease and
    conservative invocation recovery, never a new automatic replay policy.
    """
    workspace_id = _workspace_uuid(workspace_id)
    stop = _StopEvent()
    previous_handlers = {}
    last_database_notice = float('-inf')
    database_degraded = False
    current_phase = '工作区启动检查'
    uav_monitor_thread = None
    sensor_collector_thread = None
    sensor_analyzer_thread = None
    mqtt_receiver_thread = None
    dji_onboarding_thread = None
    dji_cloud2_thread = None

    def request_stop(signum, frame):
        stop.set()

    def database_unavailable():
        nonlocal last_database_notice, database_degraded
        database_degraded = True
        if time.monotonic() - last_database_notice >= 30:
            print('数据库连接暂时不可用；保留工作区持久任务并等待恢复', flush=True)
            last_database_notice = time.monotonic()
        stop.wait(2)

    def database_ready():
        nonlocal database_degraded
        if database_degraded:
            print('数据库连接已恢复；继续处理明确工作区的持久任务', flush=True)
            database_degraded = False

    def roots(items):
        nonlocal current_phase
        for name, method in items:
            if stop.is_set(): return False
            current_phase = '工作区定时协调：' + name + '.' + method
            module = import_module('.' + name, __package__)
            if stop.is_set(): return False
            if (name, method) == ('workspace_monitoring', 'schedule_once') or name == 'workspace_observation_intake':
                getattr(module, method)(workspace_id=workspace_id, _stop_event=stop)
            else:
                getattr(module, method)(workspace_id=workspace_id)
        return not stop.is_set()

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        while not stop.is_set():
            try:
                with Session(engine()) as db:
                    exists = db.get(Workspace, workspace_id) is not None
            except DBAPIError:
                database_unavailable()
                continue
            if not exists:
                print('工作区不存在；未启动定时、投递或领取任务', flush=True)
                return 1
            database_ready()
            break
        if stop.is_set(): return 0
        print('配置资料处理器已启动；仅处理工作区 ' + workspace_id + f' 的全部{len(SETUP_KINDS)}类配置任务', flush=True)
        try:
            from .uav_simulated_setup_monitor import start_workspace_monitor
            uav_monitor_thread = start_workspace_monitor(workspace_id, stop)
        except Exception as error:
            print('模拟无人机状态监测未启动：错误类别 ' + type(error).__name__ + '；主处理器继续运行', flush=True)
        try:
            from .sensor_automation import start_workspace_collector
            sensor_collector_thread = start_workspace_collector(workspace_id, stop)
        except Exception as error:
            print('传感器自动采集未启动：错误类别 ' + type(error).__name__ + '；主处理器继续运行', flush=True)
        try:
            from .sensor_native_analysis import start_workspace_analyzer
            sensor_analyzer_thread = start_workspace_analyzer(workspace_id, stop)
        except Exception as error:
            print('传感器证据分析未启动：错误类别 ' + type(error).__name__ + '；主处理器继续运行', flush=True)
        try:
            from .mqtt_workspace_supervisor import start_workspace_mqtt
            mqtt_receiver_thread = start_workspace_mqtt(workspace_id, stop)
        except Exception as error:
            print('MQTT 接收处理器未启动：错误类别 ' + type(error).__name__ + '；主处理器继续运行', flush=True)
        try:
            from .dji_onboarding_runtime import start_workspace_onboarding
            dji_onboarding_thread = start_workspace_onboarding(workspace_id, stop)
        except Exception as error:
            print('大疆上云实验处理器未启动：错误类别 ' + type(error).__name__ + '；主处理器继续运行', flush=True)
        try:
            from .dji_cloud2_runtime import start_workspace_cloud2
            dji_cloud2_thread = start_workspace_cloud2(workspace_id, stop)
        except Exception as error:
            print('大疆 Cloud API 2 工作区处理器未启动：错误类别 ' + type(error).__name__, flush=True)
        last_daily = None
        while not stop.is_set():
            try:
                if not roots(SETUP_POLL_ROOTS): break
                if last_daily is None or time.monotonic() - last_daily >= 60:
                    if not roots(SETUP_DAILY_ROOTS): break
                    last_daily = time.monotonic()
                if stop.is_set(): break
                current_phase = '工作区持久任务处理'
                worked = process_once(kinds=SETUP_KINDS, deliver=False,
                    workspace_id=workspace_id, _stop_event=stop)
                database_ready()
                if not worked and not stop.is_set(): stop.wait(1)
            except DBAPIError:
                database_unavailable()
        return 0
    except Exception as error:
        print('配置资料处理器已停止：阶段=' + current_phase + '；错误类别=' + type(error).__name__
              + '；请核查工作区和已保存任务，未确认成功', flush=True)
        return 1
    finally:
        stop.set()
        if uav_monitor_thread is not None:
            uav_monitor_thread.join(timeout=1)
        if sensor_collector_thread is not None:
            sensor_collector_thread.join(timeout=1)
        if sensor_analyzer_thread is not None:
            sensor_analyzer_thread.join(timeout=300)
            if sensor_analyzer_thread.is_alive():
                print('传感器分析仍在处理，原请求和结果状态需重新核对', flush=True)
        if mqtt_receiver_thread is not None:
            mqtt_receiver_thread.join(timeout=180)
            if mqtt_receiver_thread.is_alive():
                print('MQTT 接收处理器尚未完全退出，请核对连接状态', flush=True)
        if dji_cloud2_thread is not None:
            dji_cloud2_thread.join(timeout=30)
            if dji_cloud2_thread.is_alive():
                print('大疆 Cloud API 2 处理器尚未完全退出，请核对连接状态', flush=True)
        if dji_onboarding_thread is not None:
            dji_onboarding_thread.join(timeout=70)
            if dji_onboarding_thread.is_alive():
                print('大疆上云实验处理器尚未完全退出，请核对接收状态', flush=True)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        print('配置资料处理器已退出；持久任务保留，未继续领取', flush=True)


def run_media():
    """Dedicated recorder; no model jobs, old monitors or device-control policies."""
    print('持续视频录制处理器已启动；模型分析独立运行',flush=True)
    while True:
        try:
            from .continuous_media import schedule_once
            schedule_once()
            if not process_once(kinds=['record_continuous_media'],deliver=False):time.sleep(1)
        except DBAPIError:time.sleep(2)
