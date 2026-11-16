"""Recover committed intent after a worker stops; external writes are never replayed."""
from sqlalchemy import select, or_
from .models import Invocation, Plan, Task, Observation, Acquisition, DataRun, Governance, EvaluationRun, now
from .services import scoped, audit, enqueue_event


def _recover_advice_calls(db,p,job):
    """Retain one typed paid SDK without interpreting it as a legacy step.

    This initial recovery transaction already holds the actual Job fence. It
    acquires SDK rows only, never a Task/Plan lock in the reverse direction.
    Full Start/source/paid-inventory proof belongs to the subsequent handler.
    """
    from fastapi import HTTPException
    from pydantic import TypeAdapter
    from . import same_goal_advice_contracts_v302 as c
    from . import same_goal_advice_execution_v308 as execution
    from .contracts import digest
    from .models import Capability
    def need(value):
        if not value:raise HTTPException(409,'原业务建议调用或固定发送任务无法核对；原号保留，未重发或恢复旧式步骤')
    value=job.data
    try:
        need(job.workspace_id==p.workspace_id and type(value) is dict
            and value.get('schema')==execution.JOB_SCHEMA and value.get('actor_id')==p.user_id)
        for name in ('plan_id','task_id','actor_id'):TypeAdapter(c.ID).validate_python(value[name])
        TypeAdapter(c.Hash).validate_python(value['execution_snapshot_hash'])
        key=execution.job_key(value['task_id'],value['plan_id'])
        need(job.idempotency_key==value['invocation_key']==key)
        query=select(Invocation).where(Invocation.workspace_id==p.workspace_id,or_(
            Invocation.data['job_id'].as_string()==job.id,Invocation.idempotency_key==key,
            Invocation.data['approval']['plan_ref']['id'].as_string()==value['plan_id'],
            Invocation.data['inputs']['plan_ref']['id'].as_string()==value['plan_id']))
        calls=list(db.scalars(query.order_by(Invocation.created_at.desc()).limit(2).with_for_update()).all())
        need(len(calls)<=1)
        for call in calls:
            data=call.data;inputs=c._parse(c.InvocationInput,data['inputs'])
            expected={'schema':c.APPROVAL,'plan_ref':inputs['plan_ref'],
                'preview_hash':inputs['preview_hash'],'job_id':job.id}
            need(call.idempotency_key==key and data.get('job_id')==job.id
                and data.get('role')=='business' and data.get('approval')==expected
                and inputs['plan_ref']=={'id':value['plan_id'],'record_hash':value['execution_snapshot_hash']}
                and data.get('input_hash')==digest(inputs)
                and call.status in {'created','running','unknown','failed','blocked_evidence',
                    'blocked_model','succeeded','not_executed'})
            TypeAdapter(c.ID).validate_python(data['lease_token'])
            cap=scoped(db,Capability,call.capability_id,p)
            need(cap.kind=='tool' and cap.content_hash==cap.validated_hash==digest(cap.data)
                and cap.data['execution'].get('adapter')==c.ADAPTER
                and cap.data['allowed_agent_roles']==['business']
                and data.get('capability_hash')==cap.content_hash
                and digest(data.get('capability_snapshot'))==digest(cap.data))
        for call in calls:
            if call.status=='running':
                # A cloud answer/cost may already exist even when the business
                # tool declares no device side effect. Preserve that unknown.
                call.status='unknown';call.version+=1
                call.data={**call.data,'error':'业务建议执行中断；原答与费用可能已产生，仅核对同一首答，不重新发送',
                    'recovery':{'recovered_at':now().isoformat(),'previous_lease_token':call.data['lease_token'],
                        'recovering_job_id':job.id,'reason':'expired_worker_lease'}}
                audit(db,p,'worker.invocation_recovered',call.id,{'status':'unknown','job_id':job.id})
        if calls:job.data={**job.data,'recovery_only':True}
        return len(calls)
    except HTTPException:raise
    except (ValueError,TypeError,KeyError,AttributeError,IndexError):need(False)


def recover_calls(db, p, job):
    if job.kind == 'execute_goal_continuation_read_v314':
        from .same_goal_continuation_execution_recovery_v314 import recover_calls as recover_continuation
        return recover_continuation(db, p, job)
    if job.kind=='generate_same_goal_advice_v302':
        return _recover_advice_calls(db,p,job)
    from .threshold_policy_execution_v153 import recover as recover_policy
    if recover_policy(db,p,job):return
    conditions = [Invocation.data['job_id'].as_string() == job.id]
    legacy = Invocation.data['job_id'].as_string().is_(None)
    graph_kinds = {'execute_full_semantic_graph_node','advance_full_semantic_graph','receive_full_semantic_graph_event'}
    if job.kind == 'execute_full_semantic_graph_node':
        if job.data.get('lease_recoveries', 0) or job.attempts > 1:
            # A recovered graph dispatch may adopt a successful Invocation only.
            # A missing/unknown call is never permission to send another one.
            job.data = {**job.data, 'recovery_only': True}
        if type(job.data.get('invocation_key')) is str and job.data['invocation_key']:
            conditions.append(Invocation.idempotency_key == job.data['invocation_key'])
    if job.kind == 'execute_plan':
        conditions.append(legacy & (Invocation.data['approval']['plan_id'].as_string() == job.data['plan_id']))
    if job.kind in {'process_observation','process_local_observation','process_reported_observation','process_mqtt_observation','process_simulation_observation_v176','process_http_mapped_observation_v177'}:
        conditions.append(legacy & Invocation.idempotency_key.startswith('observation:' + job.data['observation_id'] + ':attempt:'))
    calls = db.scalars(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.status.in_(['running', 'unknown', 'failed', 'blocked_evidence', 'blocked_model', 'succeeded', 'not_executed']),
        or_(*conditions)).order_by(Invocation.created_at.desc()).with_for_update()).all()
    if job.kind == 'prepare_same_goal_advice_v302':
        # A local preparation has no SDK at all. Unexpected calls are retained
        # as conflicting custody; never reinterpret them as legacy Plan steps.
        if calls:
            from fastapi import HTTPException
            raise HTTPException(409,'本地资料准备发现非本地调用记录；保留原号核查，禁止发送或重放')
        return 0
    for call in calls:
        if call.status == 'running':
            from .models import Capability
            cap = scoped(db, Capability, call.capability_id, p)
            side_effect = call.data.get('side_effect', call.data.get('capability_snapshot', cap.data)['execution'].get('side_effect', False))
            call.status = 'unknown' if side_effect else 'failed'
            call.version += 1
            call.data = {**call.data, 'error': '后台执行中断；外部动作可能已发送，禁止重放，请只读核对服务回执' if side_effect
                else '后台执行中断，未保存工具结果；请核查后明确重试',
                'recovery': {'recovered_at': now().isoformat(), 'previous_lease_token': call.data.get('lease_token'),
                             'recovering_job_id': job.id, 'reason': 'expired_worker_lease'}}
            audit(db, p, 'worker.invocation_recovered', call.id, {'status': call.status, 'job_id': job.id})
        if job.kind in graph_kinds:
            # These calls belong to durable graph activations, not legacy Plans.
            # workflow_graph.process verifies the exact frozen invocation key.
            continue
        if job.kind == 'append_goal_evaluation_v297':
            # This fixed command has its own immutable authority and recovery
            # receipts. Its typed approval is never a legacy Plan approval.
            # Keep any running -> unknown transition above; the normal append
            # handler must authenticate original effects before reconciliation.
            continue
        approval = call.data.get('approval')
        if approval and approval.get('native_step_execution') is True:
            from .native_step_execution import recover_invocation
            recover_invocation(db,p,job,call)
            continue
        if not approval:
            if job.kind in {'process_observation','process_local_observation','process_reported_observation','process_mqtt_observation','process_simulation_observation_v176','process_http_mapped_observation_v177'} and call.status == 'succeeded' and call.capability_id == job.data['extractor_id'] and not job.data.get('resume_invocation_key'):
                job.data = {**job.data, 'resume_invocation_key': call.idempotency_key}
            continue
        plan = scoped(db, Plan, approval['plan_id'], p, True)
        task = scoped(db, Task, plan.task_id, p, True)
        index = approval['step_index']; key = plan.id + ':' + str(index)
        step = plan.data['result']['plan']['steps'][index]
        attempt = task.data.get('tool_attempts', {}).get(key, 0)
        expected_key = step['request_key'] + (':retry:' + str(attempt) if attempt else '')
        if call.idempotency_key != expected_key:
            continue
        receipts = dict(task.data.get('step_receipts', {}))
        existing = receipts.get(key)
        if existing and existing['result'].get('status') == 'succeeded':
            continue
        from .execution import capability_receipt
        receipts[key] = {'step': step, 'result': capability_receipt(plan, step, call), 'finished_at': now().isoformat()}
        task.data = {**task.data, 'step_receipts': receipts}; task.version += 1
        if task.status == 'cancelled':
            plan.status = 'cancelled'
        elif task.data.get('current_plan_id') == plan.id and call.status != 'succeeded':
            task.status = 'waiting_tool'; plan.status = 'waiting_tool'
        plan.version += 1
    if job.kind == 'dispatch_acquisition':
        acq = scoped(db, Acquisition, job.data['acquisition_id'], p, True)
        if acq.status == 'dispatching':
            acq.status = 'unknown'; acq.version += 1
            acq.data = {**acq.data, 'reason': '派发进程中断，指令可能已发送；资源保持占用，禁止重新派发，请查询设备回执',
                        'recovered_at': now().isoformat()}
            audit(db, p, 'worker.acquisition_recovered', acq.id, {'job_id': job.id})
    return len(calls)


def fail_target(db, p, job, reason):
    if job.kind == 'generate_same_goal_advice_v302':
        from .same_goal_advice_runtime_v308 import fail_job
        return fail_job(db,p,job,reason)
    if job.kind == 'prepare_same_goal_advice_v302':
        from .same_goal_advice_preparation_v304 import fail_job
        return fail_job(db,p,job,reason)
    if job.kind in {'prepare_runtime_document_ocr', 'recognize_runtime_document_ocr_page', 'search_runtime_document_ocr'}:
        from .runtime_document_ocr import recover_job
        return recover_job(db, p, job, reason)
    if job.kind in {'parse_runtime_document', 'search_runtime_documents'}:
        from .runtime_documents import recover_job
        return recover_job(db, p, job, reason)
    if job.kind in {'read_business_data', 'preview_business_data', 'import_business_data'}:
        from .business_data import recover_job
        return recover_job(db, p, job, reason)
    if job.kind == 'read_scoped_design_v181':
        from .report_scoped_design_consumer_v181 import record_job_failure
        return record_job_failure(db, p, job, reason)
    if job.kind == 'read_saved_map_design_v176':
        from .report_saved_map_design_consumer_v176 import record_job_failure
        return record_job_failure(db, p, job, reason)
    if job.kind in {'sample_simulation_v176','recover_simulation_sample_v176'}:
        from .simulation_models_v176 import SimulationSampleV176
        sample = scoped(db,SimulationSampleV176,job.data['sample_id'],p,True)
        if sample.observation_id is None:
            sample.status = 'awaiting_receipt'
            sample.data = {**sample.data,'issue':'原取样中断；请只读核对原回执，不重新发送设备请求'}
        return
    from .threshold_policy_execution_v153 import fail as fail_policy
    if fail_policy(db,p,job,reason):return
    if job.kind == 'process_threshold_shadow_v151':
        from .threshold_shadow_execution_v151 import fail
        return fail(db,p,job,reason)
    if job.kind == 'evaluate_threshold_study_v149':
        from .threshold_study_store_v149 import fail_evaluation
        return fail_evaluation(db,p,job,reason)
    if job.kind in {'execute_full_semantic_graph_node','advance_full_semantic_graph','receive_full_semantic_graph_event','drain_full_semantic_graph_admissions'}:
        from .workflow_graph import fail_job
        return fail_job(db,p,job,reason)
    if job.kind == 'read_report_draft':
        from .report_reading_drafts import record_job_failure
        return record_job_failure(db, p, job, reason)
    if job.kind == 'generate_native_query_proposal':
        from .native_query_proposals import recover_job
        return recover_job(db,p,job,reason)
    if job.kind == 'execute_native_task_query':
        from .native_task_queries import recover_job
        return recover_job(db,p,job,reason)
    """Keep stopped work understandable and recoverable instead of orphaned as running."""
    if job.kind=='execute_native_goal_query':
        from .native_goal_queries import recover_job
        recover_job(db,p,job,reason)
    elif job.kind=='execute_native_step_plan':
        from .native_step_execution import recover_job
        recover_job(db,p,job,reason)
    elif job.kind=='generate_native_step_plan':
        from .native_step_planning import recover_job
        recover_job(db,p,job,reason)
    elif job.kind=='generate_native_method_suggestion':
        from .native_method_suggestions import fail_job
        fail_job(db,p,job,reason)
    elif job.kind=='advise_signal':
        from .models import SignalAdvice
        row=scoped(db,SignalAdvice,job.data['advice_id'],p,True)
        if row.status not in {'cancelled','applied','applying'}:
            row.status='failed';row.version+=1;row.data={**row.data,'error':reason,'progress':'处置建议未完成，原上下文与回执保留，不自动重发云请求'}
    elif job.kind=='interpret_evidence_question':
        from .models import EvidenceQuestion
        row=scoped(db,EvidenceQuestion,job.data['question_id'],p,True)
        if row.status not in {'cancelled','applied'}:
            row.status='failed';row.version+=1;row.data={**row.data,'error':reason,'progress':'理解未完成，原问题和回执保留，不自动重发云请求'}
    elif job.kind=='assess_evidence':
        from .models import EvidenceAssessment
        row=scoped(db,EvidenceAssessment,job.data['assessment_id'],p,True)
        if row.status in {'queued','running'}:
            row.status='failed';row.version+=1;row.data={**row.data,'error':reason,'progress':'核查未完成，原条件保留，可另建重试'}
    elif job.kind=='execute_workflow_step':
        from .workflow_abilities import fail
        fail(db,p,job,reason)
    elif job.kind=='record_continuous_media':
        from .models import ContinuousStream
        from .continuous_media import stop_current
        row=scoped(db,ContinuousStream,job.data['stream_id'],p,True)
        if row.data['generation']==job.data['generation'] and row.status in {'queued','recording'}:
            stop_current(db,row);row.status='blocked';row.version+=1;row.data={**row.data,'reason':reason,'last_error':reason}
    elif job.kind=='capture_media':
        from .models import StreamSession
        row=scoped(db,StreamSession,job.data['session_id'],p,True)
        if row.status not in {'captured','partial','cancelled','failed'}:
            row.status='failed';row.version+=1;row.data={**row.data,'error':reason}
    elif job.kind in {'install_specialist', 'run_specialist', 'process_video'}:
        from .models import SpecialistInstallation, SpecialistRun, VideoRun
        cls = {'install_specialist': SpecialistInstallation, 'run_specialist': SpecialistRun, 'process_video': VideoRun}[job.kind]
        key = 'installation_id' if job.kind == 'install_specialist' else 'run_id'
        row = scoped(db, cls, job.data[key], p, True)
        if row.data.get('generation') == job.data['generation'] and row.status not in {'ready', 'succeeded', 'cancelled', 'failed'}:
            row.status = 'failed'; row.version += 1
            row.data = {**row.data, 'error': reason}
    elif job.kind == 'parse_setup_material':
        from .models import SetupMaterial
        material = scoped(db, SetupMaterial, job.data['material_id'], p, True)
        if material.data.get('parse_generation') == job.data['generation'] and material.status in {'queued', 'parsing'}:
            material.status = 'failed'; material.version += 1
            material.data = {**material.data, 'error': reason, 'failed_at': now().isoformat()}
    elif job.kind == 'prepare_document_copy':
        from .document_copy import fail_job
        fail_job(db,p,job,reason)
    elif job.kind in {'prepare_material_ocr','recognize_material_ocr_unit'}:
        from .material_ocr import fail_job
        fail_job(db,p,job,reason)
    elif job.kind == 'understand_setup':
        from .models import SetupAnalysis
        row = scoped(db, SetupAnalysis, job.data['analysis_id'], p, True)
        if row.status in {'queued', 'running'}:
            row.status = 'failed'; row.version += 1
            row.data = {**row.data, 'error': reason}
    elif job.kind=='check_native_business_goal':
        from .contracts import digest
        row=scoped(db,Task,job.data['task_id'],p,True)
        if job.data.get('context_hash')==digest(row.data.get('native_goal_context')):
            row.data={**row.data,'native_goal_error':reason}
    elif job.kind=='check_workspace_data_need':
        from .workspace_needs import context
        from .contracts import digest
        row=scoped(db,DataRun,job.data['query_id'],p,True)
        if job.data.get('context_hash')==digest(context(row)):
            result={'error':reason,'checked_at':now().isoformat(),'existing_records_only':True,'parent_goal_completed':False}
            row.data={**row.data,'workspace_check':result,'workspace_check_hash':digest(result)}
    elif job.kind in {'route_data_gap','recheck_data_gap'}:
        from .data_gaps import locked_gap
        gap=locked_gap(db,p,job.data['gap_id'])
        if gap.status not in {'resolved','cancelled'}:
            gap.status='blocked_recovery';gap.version+=1
            gap.data={**gap.data,'decision_reason':reason,'last_error_at':now().isoformat()}
            from .data_gaps import record
            record(db,p,gap,'worker_failed',reason=reason)
    elif job.kind=='diagnose_data':
        from .models import QualityPolicy,User,Membership
        policy=scoped(db,QualityPolicy,job.data['policy_id'],p,True)
        if policy.version==job.data['policy_version'] and policy.status=='active':
            allowed=db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(
                User.id==policy.actor_id,User.active.is_(True),Membership.workspace_id==p.workspace_id,Membership.role=='admin'))
            if not allowed:
                policy.status='blocked_authorization';policy.version+=1
                policy.data={**policy.data,'authorization_error':'策略批准人的管理员授权已撤销，自动检查已停止'}
            policy.data={**policy.data,'last_error':reason,'last_error_at':now().isoformat()}
    elif job.kind=='generate_plan':
        plan=scoped(db,Plan,job.data['plan_id'],p)
        task=scoped(db,Task,plan.task_id,p,True)
        plan=scoped(db,Plan,plan.id,p,True)
        if plan.status in {'queued','running'}:
            plan.status='failed';plan.version+=1;plan.data={**plan.data,'error':reason,
                'progress':{'stage':'interrupted','saved_at':now().isoformat()}}
            if task.data.get('current_plan_id')==plan.id and task.status not in {'cancelled','completed'}:
                task.status='planning_failed';task.version+=1
                task.data={**task.data,'execution_error':reason}
    elif job.kind == 'execute_plan':
        plan = scoped(db, Plan, job.data['plan_id'], p, True)
        task = scoped(db, Task, plan.task_id, p, True)
        if task.status not in {'cancelled', 'completed', 'waiting_tool', 'awaiting_acceptance'}:
            task.status = 'waiting_evidence'; task.version += 1
            task.data = {**task.data, 'execution_error': reason}
            plan.status = 'failed'; plan.version += 1
    elif job.kind in {'process_observation','process_local_observation','process_reported_observation','process_mqtt_observation','process_simulation_observation_v176','process_http_mapped_observation_v177'}:
        row = scoped(db, Observation, job.data['observation_id'], p, True)
        if row.status != 'processed':
            from .observation_inputs import fail as fail_input
            fail_input(db,p,row,job.attempts,reason,'worker_interrupted')

    elif job.kind == 'data_query':
        row = scoped(db, DataRun, job.data['query_id'], p, True)
        from .goal_evaluation_query_guard_v288 import has_trace as goal_evaluation_trace
        if goal_evaluation_trace(db, p, row):
            return
        if row.status in {'queued', 'running'}:
            row.status = 'failed'; row.version += 1; row.data = {**row.data, 'error': reason}
            from .goal_queries import completed
            completed(db,p,row)
    elif job.kind == 'interpret_goal':
        row=scoped(db,Task,job.data['task_id'],p,True)
        if row.status=='interpreting_goal':
            row.status='needs_clarification';row.version+=1
            row.data={**row.data,'goal_error':reason,'goal_clarifications':['目标解析中断，原请求已保存，请核查模型配置后重试']}
    elif job.kind == 'apply_governance':
        row = scoped(db, Governance, job.data['plan_id'], p, True)
        if row.status != 'applied':
            row.status = 'failed'; row.version += 1; row.data = {**row.data, 'error': reason}
    elif job.kind == 'evaluate_learning':
        row = scoped(db, EvaluationRun, job.data['run_id'], p, True)
        if row.status not in {'passed', 'invalidated', 'failed'}:
            row.status = 'failed'; row.version += 1; row.data = {**row.data, 'error': reason}
            from .models import LearningCandidate
            candidate = scoped(db, LearningCandidate, row.candidate_id, p, True)
            candidate.status = 'failed'; candidate.version += 1
    elif job.kind == 'dispatch_acquisition':
        row = scoped(db, Acquisition, job.data['acquisition_id'], p, True)
        if row.status == 'authorized':
            row.status = 'blocked_connector'; row.version += 1; row.data = {**row.data, 'reason': reason}
    audit(db, p, 'worker.target_failed', job.id, {'kind': job.kind, 'reason': reason})
