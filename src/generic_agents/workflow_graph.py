"""Durable graph activations backed by the real Job/Invocation/lease pipeline.

Pure graph transitions never call tools. This module freezes transport intent
before IO and re-locks the task/epoch before adopting a returned result. A human
wait is independent of every other ready activation. Unknown calls retain their
original key and never become a new automatic attempt.
"""
import copy
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .models import Job, Invocation, Memory, DomainInstance, now
from .services import scoped, published, require, audit, validate
from . import full_semantic_graph_v1 as graph_api
from .report_text_parse_scope import report_text_parse_scope

TRANSPORT = 'graph_jobs'
KIND = 'execute_full_semantic_graph_node'
SYSTEM_INPUTS = {'object_records', 'archive_records', 'entity_ids', 'task_identifier', 'task_id',
    'task_evidence', 'task_context', 'task_image_ids', 'task_scene_refs', 'task_comparison_refs', 'task_pixel_pair_refs', 'task_audio_ref', 'task_sound_ref'}


def clock(): return now().timestamp()


def invoke_pure(operation, *args, **kwargs):
    try: return operation(*args, **kwargs)
    except ValueError as error:
        raise HTTPException(409, '执行状态需要核对，原记录保留：' + str(error)) from None


def marked(task): return 'graph_runtime' in task.data


def business_binding(release, business):
    from .instance_semantic_runtime_dispatch import modern
    return modern(release.data['snapshot']).business_binding(release, business)


def graph_for(release, business): return business_binding(release, business)['graph']


def node_for(graph, activation):
    return next(node for node in graph['nodes'] if node['id'] == activation['node_id'])


def initialization_inputs(task, supplied):
    from .workflow_abilities import attributes
    supplied = copy.deepcopy(supplied)
    if set(supplied) & SYSTEM_INPUTS:
        raise HTTPException(422, '任务对象、原件和关联输入由服务器固定，不能手工替换')
    records = [{'entity_id': obj['id'], 'type': obj['type_name'], 'version': obj['version'],
        'attributes': attributes(obj)} for obj in task.data['object_snapshots']]
    from .workflow_audio import ref as audio_ref
    from .workflow_sound import ref as sound_ref
    from .workflow_scenes import refs as scene_refs
    from .workflow_geo_pairs import refs as pair_refs
    return {**supplied, 'object_records': records, 'archive_records': records,
        'entity_ids': list(task.data['entity_ids']), 'task_identifier': task.id, 'task_id': task.id,
        'task_evidence': {'task_id': task.id, 'material_snapshots': task.data.get('material_snapshots', [])},
        'task_context': {'task_id': task.id, 'material_snapshots': task.data.get('material_snapshots', [])},
        'task_image_ids': [x['id'] for x in task.data.get('image_snapshots', [])],
        'task_scene_refs': scene_refs(task),
        'task_comparison_refs': pair_refs(task),
        'task_pixel_pair_refs': copy.deepcopy(task.data.get('pixel_pair_snapshot')),
        'task_audio_ref': audio_ref(task) if task.data.get('audio_snapshots') else None,
        'task_sound_ref': sound_ref(task) if task.data.get('sound_snapshots') else None}


def initialize(db, p, task, release, business, supplied):
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task)
    binding = business_binding(release, business); graph = binding['graph']
    from .runtime_task_documents import validate_inputs as validate_document_sources
    validate_document_sources(db, p, task, graph, supplied)
    values = initialization_inputs(task, supplied)
    properties = graph['input_schema'].get('properties', {})
    # Server fields are materialized only when explicitly declared by the graph.
    values = {key: value for key, value in values.items() if key in properties or key in supplied}
    from . import workflow_graph_waits as waits
    from .workflow_job_dependencies_v264 import trace_for, observer_options, save_control
    observer = trace_for(db, p, task, initialize=True)
    state = (waits.initialize(db, p, task, binding, values, **observer_options(observer)) if waits.enabled(binding)
        else invoke_pure(graph_api.new_run, graph, values, clock(), **observer_options(observer)))
    task.data = {**task.data, 'graph_runtime': state, TRANSPORT: {},
        'workflow_runtime': {'schema': 'workflow-graph-projection-v1', 'current_step_id': None,
            'receipts': [], 'drafts': {}, 'max_steps': 1000}}
    save_control(db, p, task, state, observer)
    settle(db, p, task, release, business, state)


def live(db, p, task, release, business, *, lock_sources=True):
    """Check current execution context; advisory reads do not acquire grants.

    The default retains every dispatch lock. An explicit read-only projection
    checks saved provenance and current readable authority without holding
    action locks; it cannot grant execution.
    """
    if type(lock_sources) is not bool:
        raise HTTPException(409, '来源核对方式无法确认')
    from .layered_approval_store_v175 import guard as approval_guard
    if lock_sources: approval_guard(db,p,task)
    else: approval_guard(db,p,task,lock_sources=False)
    from .signal_graph_routing_v134 import guard_current as graph_route_guard
    if lock_sources: graph_route_guard(db,p,task)
    else: graph_route_guard(db,p,task,lock=False)
    from .instance_semantic_runtime_dispatch import modern
    selected = modern(release.data['snapshot'])
    if lock_sources: selected.verify_release(db, p, release,business_name=business['name'])
    else: selected.verify_release(db, p, release,business_name=business['name'],live=False)
    instance = scoped(db, DomainInstance, task.data['instance_id'], p)
    if instance.status != 'partially_active' or business['id'] not in instance.data.get('enabled_business_ids', []):
        raise HTTPException(409, '此实例或业务已暂停，原处理记录保留')
    published(db, p, release.package_id, 'domain_package')
    from .entity_release_compat import verify_task_objects
    if lock_sources: verify_task_objects(db, p, task, release)
    else: verify_task_objects(db, p, task, release,lock_sources=False)
    from .instance_compiler import verify_material_snapshots
    verify_material_snapshots(db, p, task.data.get('material_snapshots', []), allow_archived=True)
    from .workspace_api import verify_perception_evidence
    if lock_sources: verify_perception_evidence(db, p, task)
    else: verify_perception_evidence(db, p, task,lock_sources=False)
    for name in ('workflow_images', 'workflow_scenes'):
        module = __import__('generic_agents.' + name, fromlist=['verify_masks'])
        module.verify_masks(db, p, task)
    from .workflow_images import verify
    verify(db, p, task)
    from .workflow_audio import verify_receipts
    verify_receipts(db, p, task)
    from .workflow_geo_pairs import verify_receipts as verify_pairs
    verify_pairs(db,p,task)


def settle(db, p, task, release, business, state):
    """Persist all frontier intents in one transaction; never run IO inline."""
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task)
    graph = graph_for(release, business)
    invoke_pure(graph_api.check_run, graph, state)
    from .workflow_job_dependencies_v264 import CONTROL, bind_graph
    if CONTROL in task.data:
        task.data = {**task.data, 'graph_runtime': state}
    transports = copy.deepcopy(task.data.get(TRANSPORT, {}))
    if state['status'] not in {'cancelled', 'succeeded', 'stopped', 'failed'}:
        for identity in state['eligible_ability_ids']:
            from .graph_reasoning_retirement_v337 import is_retired
            if is_retired(task, identity):
                if identity not in transports or transports[identity].get('phase') != 'retired_for_new_evaluation':
                    raise HTTPException(409, '原步骤停用依据与执行记录不一致；未重新派发')
                continue
            if identity in transports: continue
            activation = state['activations'][identity]; node = node_for(graph, activation)
            inputs = invoke_pure(graph_api.ability_inputs, graph, state, identity)
            key = 'graph:' + task.id + ':' + identity + ':1'
            job = Job(workspace_id=p.workspace_id, kind=KIND, status='queued', idempotency_key=key,
                data={'actor_id': node['actor_id'], 'task_id': task.id, 'activation_id': identity,
                    'graph_hash': state['graph_hash'], 'cancel_epoch': state['cancel_epoch'],
                    'activation_version': activation['version'], 'invocation_key': key,
                    'inputs': inputs, 'input_hash': digest(inputs), 'attempt': 1})
            db.add(job); db.flush()
            bind_graph(db, p, task, job)
            transports[identity] = {'job_id': job.id, 'invocation_key': key,
                'input_hash': digest(inputs), 'phase': 'queued', 'cancel_epoch': state['cancel_epoch'],
                'attempt': 1}
    task.version += 1
    task.data = {**task.data, 'graph_runtime': state, TRANSPORT: transports}
    timers = copy.deepcopy(task.data.get('graph_timers', {}))
    if state['status'] not in {'cancelled', 'succeeded', 'stopped', 'failed'}:
        for identity, activation in state['activations'].items():
            if activation['status'] != 'waiting_event' or activation['deadline'] is None: continue
            key = identity + ':' + str(state['cancel_epoch'])
            if key in timers: continue
            job = Job(workspace_id=p.workspace_id, kind='advance_full_semantic_graph', status='queued',
                idempotency_key='graph-timer:' + task.id + ':' + key,
                available_at=datetime.fromtimestamp(activation['deadline'], timezone.utc),
                data={'actor_id': task.owner_id, 'task_id': task.id, 'cancel_epoch': state['cancel_epoch'],
                    'graph_hash': state['graph_hash'], 'activation_id': identity, 'deadline': activation['deadline']})
            db.add(job); db.flush(); timers[key] = job.id
    task.data = {**task.data, 'graph_timers': timers}
    task.status = {'succeeded': 'workflow_completed', 'cancelled': 'workflow_cancelled',
        'failed': 'workflow_failed', 'stopped': 'workflow_stopped'}.get(state['status'], 'workflow_waiting')
    if state['status'] == 'succeeded' and not task.data['workflow_runtime'].get('data_receipt_id'):
        receipt = Memory(workspace_id=p.workspace_id, status='candidate', text='完整协同流程已保存执行结果，业务事实仍以核查结论为准',
            data={'type': 'workflow_graph_result', 'task_id': task.id,
                'instance_release_id': release.id, 'graph_hash': state['graph_hash'],
                'state_hash': state['state_hash'], 'activations': copy.deepcopy(state['activations']),
                'goal_receipts': copy.deepcopy(state['goal_receipts']),
                'object_snapshots': task.data['object_snapshots'],
                'entity_state_updated': False, 'auto_publish': False})
        db.add(receipt); db.flush()
        task.data = {**task.data, 'workflow_runtime': {**task.data['workflow_runtime'], 'data_receipt_id': receipt.id}}
    from . import workflow_graph_waits as waits
    binding = business_binding(release, business)
    if waits.enabled(binding): waits.schedule(db, p, task, binding, state)


def legacy_step(node, graph):
    """Adapt only modality provenance helpers, never old sequential scheduling."""
    execution = copy.deepcopy(node['execution'])
    execution['bindings'] = {field: {'source': graph['references'][ref]['field']}
        for field, ref in execution['input_bindings'].items()
        if graph['references'][ref]['source'] == 'input'}
    return {'id': node['source_step_id'] or node['id'], 'execution': execution}


def modality_inputs(db, p, task, node, graph, inputs):
    step = legacy_step(node, graph)
    for name in ('workflow_images', 'workflow_audio', 'workflow_sound', 'workflow_segmented_audio',
            'workflow_scenes', 'workflow_geo_pairs', 'workflow_learned_geo_pairs', 'workflow_pixel_sources_v250'):
        module = __import__('generic_agents.' + name, fromlist=['local_inputs'])
        module.local_inputs(db, p, task, step, inputs)


def modality_result(db, p, task, node, graph, inputs, output):
    step = legacy_step(node, graph)
    from .workflow_images import local_result
    local_result(db, p, step, inputs, output)
    for name in ('workflow_audio', 'workflow_sound', 'workflow_segmented_audio', 'workflow_scenes',
            'workflow_geo_pairs', 'workflow_learned_geo_pairs', 'workflow_pixel_runs_v250'):
        module = __import__('generic_agents.' + name, fromlist=['local_result'])
        module.local_result(db, p, task, step, inputs, output)


@report_text_parse_scope()
def process(db, p, job):
    from .workspace_api import saved_execution
    from .gateway import invoke
    task, release, business = saved_execution(db, p, job.data['task_id'], True)
    from .layered_approval_store_v175 import guard as approval_guard
    approval_guard(db,p,task)
    graph = graph_for(release, business); state = task.data['graph_runtime']
    identity = job.data['activation_id']; activation = state['activations'].get(identity)
    transport = task.data[TRANSPORT].get(identity)
    if (not transport or transport['job_id'] != job.id or job.data['graph_hash'] != state['graph_hash']
            or job.data['input_hash'] != digest(job.data['inputs'])):
        raise HTTPException(409, '图节点的持久执行意图与任务不一致')
    from .graph_reasoning_retirement_v337 import is_retired
    if is_retired(task, identity) or transport.get('phase') == 'retired_for_new_evaluation':
        return 'cancelled'
    if state['cancel_epoch'] != job.data['cancel_epoch'] or state['status'] in {'cancelled', 'stopped', 'failed'}:
        return 'cancelled'
    from .workflow_job_dependencies_v264 import require_ready
    require_ready(db, p, task, job)
    if activation['status'] == 'completed': return 'succeeded'
    node = node_for(graph, activation)
    if node['actor_id'] != p.user_id: raise HTTPException(403, '图节点执行账号与实际后台账号不一致')
    live(db, p, task, release, business)
    expected = invoke_pure(graph_api.ability_inputs, graph, state, identity)
    if expected != job.data['inputs']: raise HTTPException(409, '图节点冻结输入与实际来源不一致')
    cap = published(db, p, node['execution']['capability_id'])
    package = published(db, p, release.package_id, 'domain_package')
    if cap.id not in package.data['dependencies']: raise HTTPException(409, '此能力未列入冻结业务版本依赖')
    service = node['execution']['adapter'] == 'workflow_service'
    archive = node['execution']['adapter'] == 'task_archive'
    packet = node['execution']['adapter'] == 'task_evidence_packet'
    document_packet = node['execution']['adapter'] == 'runtime_document_packet'
    if service:
        from .workflow_service_v195 import make_approval
        service_approval = make_approval(task, job, node, expected)
    elif archive:
        from .workflow_task_archive_v198 import make_approval
        service_approval = make_approval(task, job, node, expected)
    elif packet:
        from .workflow_task_packet_v199 import make_approval
        service_approval = make_approval(task, job, node, expected)
    elif document_packet:
        from .workflow_document_packet_v207 import make_approval
        service_approval = make_approval(task, job, node, expected)
    else:
        service_approval = None
    prior = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == job.data['invocation_key']))
    if prior is None:
        from .workflow_document_packet_wait_v207 import preflight as document_preflight
        document_wait = document_preflight(db,p,task,job,node,cap,expected)
        if document_wait:
            return 'waiting_document_packet_v207'
        if document_wait is None:
            from .workflow_provenance_wait_v197 import preflight
            if preflight(db,p,task,job,node,cap,expected):
                return 'waiting_provenance'
            from .workflow_evidence_consent_wait_v199 import preflight as consent_preflight
            if consent_preflight(db,p,task,job,node,cap,expected):
                return 'waiting_evidence_consent'
    from .business_reasoning_protocols_v276 import applies as reasoning_adapter, family as reasoning_protocol, retained_state, recovery_record
    reasoning_state = retained_state(node['execution']['adapter'], prior)
    if (reasoning_adapter(node['execution']['adapter']) and prior is not None
            and prior.status != 'succeeded' and type(reasoning_state) is dict
            and type(reasoning_state.get('capture')) is dict):
        # Reparse the captured first answer of this exact Invocation. This does
        # not dispatch again or replace a terminal provider ledger/paid answer.
        from .gateway import ModelBlocked
        try:
            recovered = reasoning_protocol(node['execution']['adapter']).recover(
                db,p,cap,expected,job.data['invocation_key'],invocation=prior)
            validate(cap.data['output_schema'], recovered)
        except (HTTPException, ModelBlocked):
            return record_failure(db,p,task,identity,
                '原业务分析回答未能恢复；原回答和费用已保留，未重新调用模型',prior)
        previous_status = prior.status
        marker, recovery_action = recovery_record(node['execution']['adapter'])
        prior.status = 'succeeded'; prior.data = {**prior.data, 'output': recovered,
            marker: {'previous_status': previous_status,
                'output_hash': digest(recovered), 'recovered_at': now().isoformat()}}
        audit(db,p,recovery_action,prior.id,
            {'job_id':job.id,'activation_id':identity,'previous_status':previous_status,
                'output_hash':digest(recovered)})
    from .workflow_service_catalog_v197 import monitoring_receipt_retained
    receipt_pending = service and prior is not None and (prior.status == 'waiting_receipt'
        or prior.status == 'unknown' and type(prior.data.get('service_state')) is dict
        and prior.data['service_state'].get('schema') == 'workflow-service-state-v195'
        or monitoring_receipt_retained(prior, db=db, p=p, task=task, job=job))
    if prior is not None and prior.status != 'succeeded' and not receipt_pending:
        # No new nonce, adapter dispatch, or inference from a missing receipt.
        return record_failure(db, p, task, identity, '原调用尚无可采用的成功回执，请核查原请求；未自动重发', prior)
    if job.data.get('recovery_only') and prior is None:
        from .workflow_provenance_wait_v197 import can_start_after_wait
        from .workflow_evidence_consent_wait_v199 import can_start_after_wait as after_consent
        provenance = type(job.data.get('workflow_provenance_wait_v197')) is dict and can_start_after_wait(db,task,job)
        consent = type(job.data.get('workflow_evidence_consent_wait_v199')) is dict and after_consent(db,task,job)
        from .workflow_document_packet_wait_v207 import can_start_after_wait as after_document_packet
        documents = type(job.data.get('workflow_document_packet_wait_v207')) is dict and after_document_packet(db,task,job)
        reviewed = False
        if type(job.data.get('simulation_review_v202')) is str:
            from .workflow_simulation_review_v202 import can_start_after_review
            reviewed = can_start_after_review(db, p, task, job)
        from .workflow_local_pre_dispatch_recovery_v287 import KEY as local_recovery_key, can_start_after_pre_dispatch_recovery
        local_first_dispatch = (local_recovery_key in job.data
            and can_start_after_pre_dispatch_recovery(db, p, task, job))
        from .workflow_resense_pre_dispatch_recovery_v290 import (KEY as resense_recovery_key,
            can_start_after_pre_dispatch_recovery as can_start_resense_recovery)
        resense_first_dispatch = (resense_recovery_key in job.data
            and can_start_resense_recovery(db, p, task, job))
        if not (provenance or consent or documents or reviewed or local_first_dispatch or resense_first_dispatch):
            return record_failure(db, p, task, identity, '恢复次数已达上限且未找到成功回执，不能自动重新执行')
    from .workflow_graph_goal_guard_v133 import reserve as reserve_goal, result_step as goal_result_step
    if prior is None:
        # The Task lock serializes graph/native/query reservations. This must
        # precede modality preparation, which may itself read/process media.
        reserve_goal(db, p, task, job, node)
    else:
        try:
            goal_result_step(db, p, task, job, node)
        except HTTPException:
            # The original success remains in Invocation. Do not even prepare
            # media again merely to recover an ended goal's historical result.
            return record_failure(db, p, task, identity, '原成功调用已保留；原目标受限，未重新准备输入或推进流程')
    inputs = copy.deepcopy(expected); modality_inputs(db, p, task, node, graph, inputs)
    validate(cap.data['input_schema'], inputs)
    from .leases import ownership
    dispatch_owner=transport.get('dispatch_ownership') if prior is not None else ownership(db)
    if type(dispatch_owner) is not dict or dispatch_owner.get('job_id')!=job.id or not dispatch_owner.get('lease_token'):
        raise HTTPException(409,'原调用的实际派发租约缺失，不能凭自洽结果替代真实调用')
    if prior is not None:check_invocation(prior,p,cap,node,inputs,job,dispatch_owner,approval=service_approval)
    next_transport = {**transport, 'phase': 'running',
        'dispatch_committed':True,'dispatch_ownership':dispatch_owner,'dispatch_input_hash':digest(inputs)}
    from .workflow_resense_pre_dispatch_recovery_v290 import KEY as resense_recovery_key
    recovered_resense_intent = resense_recovery_key in job.data and prior is None
    if recovered_resense_intent:
        from .workflow_resense_pre_dispatch_recovery_v290 import record_dispatch_intent
        record_dispatch_intent(db,p,task,job,next_transport)
    transports = copy.deepcopy(task.data[TRANSPORT]); transports[identity] = next_transport
    task.data = {**task.data, TRANSPORT: transports}; task.version += 1
    if not recovered_resense_intent:
        audit(db, p, 'workflow.graph.dispatch', task.id, {'activation_id': identity, 'job_id': job.id})
    db.commit()  # durable intent/lease before real IO
    if receipt_pending:
        from .workflow_service_v195 import PendingService
        from .workflow_service_catalog_v197 import family
        try:
            output = family(cap).reconcile(db,p,cap,inputs,job.data['invocation_key'],service_approval,prior)
        except PendingService:
            call = prior
        else:
            validate(cap.data['output_schema'], output)
            previous_status = prior.status
            prior.status = 'succeeded'; prior.data = {**prior.data, 'output': output,
                'service_previous_status': previous_status,
                'service_receipt_verified_at': now().isoformat()}
            audit(db,p,'workflow.service.receipt_verified',prior.id,{'job_id':job.id,'activation_id':identity})
            if previous_status == 'waiting_receipt':
                from .workflow_job_dependencies_v264 import record_terminal_service_parent
                record_terminal_service_parent(db,p,task,job,prior,cap,node,
                    business_binding(release,business),inputs,service_approval)
            elif previous_status == 'unknown':
                from .workflow_goal_evaluation_recovery_v294 import applies as retained_evaluation, record_terminal_parent
                if retained_evaluation(prior):
                    record_terminal_parent(db,p,task,job,prior,cap,node,
                        business_binding(release,business),inputs,service_approval)
            call = prior
    else:
        call = prior or (invoke(db, p, cap.id, node['execution']['agent_role'], inputs,
            job.data['invocation_key'], approval=service_approval) if service or archive or packet or document_packet else
            invoke(db, p, cap.id, node['execution']['agent_role'], inputs, job.data['invocation_key']))
    check_invocation(call,p,cap,node,inputs,job,dispatch_owner,approval=service_approval)
    # gateway may commit independently; always acquire fresh task state again.
    task, release, business = saved_execution(db, p, task.id, True)
    state = copy.deepcopy(task.data['graph_runtime']); transport = copy.deepcopy(task.data[TRANSPORT][identity])
    if state['cancel_epoch'] != job.data['cancel_epoch'] or state['status'] in {'cancelled', 'stopped', 'failed'}:
        transports = copy.deepcopy(task.data[TRANSPORT]); transports[identity] = {**transport,
            'phase': 'late_result_preserved', 'invocation_id': call.id, 'invocation_status': call.status}
        task.data = {**task.data, TRANSPORT: transports}; task.version += 1
        audit(db, p, 'workflow.graph.late_result', task.id, {'activation_id': identity, 'invocation_id': call.id})
        return 'cancelled'
    if service and (call.status == 'waiting_receipt' or receipt_pending and call.status == 'unknown'):
        transports = copy.deepcopy(task.data[TRANSPORT]); transports[identity] = {**transport,
            'phase': 'waiting_service', 'invocation_id': call.id, 'invocation_status': call.status,
            'waiting_since': transport.get('waiting_since') or now().isoformat(),
            'reason': call.data.get('pending_reason') or '原补采已保存，等待观测处理与数据接收回执'}
        task.data = {**task.data, TRANSPORT: transports}; task.version += 1
        job.available_at = now() + timedelta(seconds=10)
        return 'waiting_service'
    if call.status != 'succeeded':
        return record_failure(db, p, task, identity, call.data.get('error') or '能力未返回成功回执', call)
    if archive or packet or document_packet or service and cap.data['execution'].get('operation') in {'dispatch_tasks','execute_registered_tool_plan'}:
        # A success marker cannot replace the saved work orders or archive.
        # This path only reads original inventory, including after API/worker
        # restart. It never invokes an adapter or reconstructs missing rows.
        from .business_flow_review_store import _same
        try:
            if archive:
                from .workflow_task_archive_v198 import reconcile as read_original
            elif packet:
                from .workflow_task_packet_v199 import reconcile as read_original
            elif document_packet:
                from .workflow_document_packet_v207 import reconcile as read_original
            elif cap.data['execution'].get('operation') == 'execute_registered_tool_plan':
                from .workflow_registered_tool_plan_v201 import saved_receipt as read_original
            else:
                from .workflow_task_dispatch_v198 import saved_receipt as read_original
            actual_receipt = read_original(db,p,cap,inputs,job.data['invocation_key'],service_approval,call)
            if not _same(actual_receipt,call.data.get('output')):
                raise HTTPException(409,'原持久记录与调用回执不一致')
        except HTTPException:
            return record_failure(db,p,task,identity,'原工作单或档案库存无法核对；原调用保留，未重建或重复派发',call)
    try:
        goal_result_step(db, p, task, job, node)
    except HTTPException:
        # The actual Invocation has already been saved. A changed/stopped goal
        # can retain that history, never complete this activation or queue peers.
        transports = copy.deepcopy(task.data[TRANSPORT]); transports[identity] = {**transport,
            'phase': 'late_result_preserved', 'invocation_id': call.id, 'invocation_status': call.status,
            'reason': '原目标已停止、到期或额度受限；原调用结果已保留，未推进流程'}
        task.data = {**task.data, TRANSPORT: transports}; task.version += 1
        audit(db, p, 'workflow.graph.late_result', task.id, {'activation_id': identity, 'invocation_id': call.id})
        return 'cancelled'
    live(db, p, task, release, business)
    output = copy.deepcopy(call.data['output']); modality_result(db, p, task, node, graph, inputs, output)
    activation = state['activations'][identity]
    from . import workflow_graph_waits as waits
    binding = business_binding(release, business)
    from .workflow_job_dependencies_v264 import trace_for, observer_options, save_control
    observer = trace_for(db, p, task, producer_job=job, activation_id=identity)
    result = (waits.transition(db, p, task, binding, operation='complete', command={
        'activation_id': identity, 'output': output, 'request_id': job.id, 'actor_id': p.user_id,
        'expected_version': activation['version'], 'cancel_epoch': state['cancel_epoch']}, **observer_options(observer))
        if waits.enabled(binding) else invoke_pure(graph_api.complete, graph, state, identity, output,
            job.id, p.user_id, activation['version'], state['cancel_epoch'], clock(), **observer_options(observer)))
    transports = copy.deepcopy(task.data[TRANSPORT]); transports[identity] = {**transport,
        'phase': 'succeeded', 'invocation_id': call.id, 'output_hash': digest(output)}
    receipt = {'request_id': job.id, 'step_id': node['source_step_id'] or node['id'],
        'activation_id': identity, 'actor': p.user_id, 'at': now().isoformat(),
        'execution_mode': 'registered_graph_ability', 'invocation_id': call.id,
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'release_version': cap.release_version,
        'input_snapshot': inputs, 'input_hash': digest(inputs), 'output': output,
        'entity_state_updated': False, 'candidate_only': True}
    task.data = {**task.data, TRANSPORT: transports, 'workflow_runtime': {**task.data['workflow_runtime'],
        'receipts': [*task.data['workflow_runtime']['receipts'], receipt]}}
    audit(db, p, 'workflow.graph.node_completed', task.id, {'activation_id': identity, 'invocation_id': call.id})
    if observer is not None:
        task.data = {**task.data, 'graph_runtime': result}
        save_control(db, p, task, result, observer)
    settle(db, p, task, release, business, result)
    return 'succeeded'


def check_invocation(call,p,cap,node,inputs,job,owner,*,approval=None):
    if (call.workspace_id!=p.workspace_id or call.capability_id!=cap.id
            or call.idempotency_key!=job.data['invocation_key']
            or call.data.get('input_hash')!=digest(inputs)
            or call.data.get('role')!=node['execution']['agent_role']
            or call.data.get('capability_hash')!=node['execution']['capability_hash']
            or call.data.get('job_id')!=job.id or call.data.get('lease_token')!=owner.get('lease_token')
            or call.data.get('approval') != approval
            or call.data.get('side_effect') is not (approval is not None)):
        raise HTTPException(409,'实际调用的能力、输入、账号角色或原派发租约无法核对，原记录保留')


def record_failure(db, p, task, identity, reason, call=None):
    transports = copy.deepcopy(task.data[TRANSPORT]); item = transports[identity]
    blocked = {**item, 'phase': 'blocked', 'reason': str(reason)[:1000]}
    if call is not None:
        if item.get('invocation_id') is not None and item['invocation_id'] != call.id:
            raise HTTPException(409, '原调用引用与实际回执不一致，未覆盖原调用记录')
        blocked.update(invocation_id=call.id, invocation_status=call.status)
    else:
        # No returned call is not proof that an earlier call did not exist.
        # Preserve any actual original reference; omit only null placeholders.
        for field in ('invocation_id', 'invocation_status'):
            if blocked.get(field) is None:
                blocked.pop(field, None)
    transports[identity] = blocked
    task.data = {**task.data, TRANSPORT: transports}; task.version += 1
    audit(db, p, 'workflow.graph.node_blocked', task.id, {'activation_id': identity})
    return 'blocked'


def fail_job(db, p, job, reason):
    from .models import Task
    task = scoped(db, Task, job.data['task_id'], p, True)
    from . import workflow_graph_waits as waits
    if waits.AUTHORITY in task.data and job.kind in {waits.KIND, 'advance_full_semantic_graph'}:
        state = task.data['graph_runtime']; authority = task.data[waits.AUTHORITY]
        if (state['status'] in {'cancelled', 'succeeded', 'stopped', 'failed'}
                or job.data.get('cancel_epoch') != state['cancel_epoch']): return
        if job.kind == waits.KIND:
            if (task.data.get('graph_wait_poll', {}).get('job_id') != job.id
                    or job.data.get('authority_hash') != authority['authority_hash']): return
        else:
            # The durable poll is the canonical recovery target. A legacy
            # deadline job retains its own failure, without hiding a failed
            # poll behind a different Job/version that the user cannot retry.
            return
    item = task.data.get(TRANSPORT, {}).get(job.data.get('activation_id'))
    if item and item['job_id'] == job.id and item['phase'] not in {'succeeded', 'late_result_preserved', 'retired_for_new_evaluation'}:
        record_failure(db, p, task, job.data['activation_id'], reason)
    elif job.kind != KIND:
        task.data = {**task.data, 'graph_wait_error': {'job_id': job.id, 'reason': str(reason)[:1000]}}
        task.version += 1


def process_wait(db, p, job):
    from .workspace_api import saved_execution
    task, release, business = saved_execution(db, p, job.data['task_id'], True)
    from . import workflow_graph_waits as waits
    binding = business_binding(release, business)
    state = task.data['graph_runtime']
    if (state['cancel_epoch'] != job.data['cancel_epoch']
            or state['status'] in {'cancelled', 'stopped', 'failed', 'succeeded'}): return 'cancelled'
    from .workflow_graph_goal_guard_v133 import progress as goal_progress
    goal_progress(db, p, task)
    if job.kind == waits.KIND:
        if not waits.enabled(binding):
            raise HTTPException(409, '旧流程没有确认消息来源，不能开放等待执行')
        return waits.process(db, p, job, task, release, business, binding)
    if state['graph_hash'] != job.data['graph_hash']:
        raise HTTPException(409, '等待节点的冻结流程摘要不一致')
    if waits.enabled(binding):
        if job.kind == 'receive_full_semantic_graph_event':
            raise HTTPException(409, '此流程只接纳已核对的消息回执，旧事件不能替代')
        authority = waits.checked(task, binding)
        window = authority['windows'].get(job.data.get('activation_id'))
        expected_key = 'graph-timer:' + task.id + ':' + str(job.data.get('activation_id')) + ':' + str(state['cancel_epoch'])
        if (job.kind != 'advance_full_semantic_graph' or window is None
                or job.idempotency_key != expected_key or job.data.get('deadline') != window['deadline']):
            raise HTTPException(409, '原等待期限任务与实际窗口不一致，原记录保留')
        if window['status'] != 'open': return 'succeeded'
        live(db, p, task, release, business)
        result = waits.transition(db, p, task, binding)
        settle(db, p, task, release, business, result)
        return 'succeeded'
    live(db, p, task, release, business)
    if job.kind == 'receive_full_semantic_graph_event':
        from .models import Event
        from .workflow_graph_api import receive_event
        event = scoped(db, Event, job.data['event_id'], p)
        if digest(event.data) != job.data['event_hash']:
            raise HTTPException(409, '来源事件内容已变化，不能采用另一个回执')
        receive_event(db, p, task, release, business, event)
    else:
        # Drain already durable, timely source events before deciding a timeout.
        # Delivery queue latency does not rewrite their original arrival time.
        from .models import Event
        from .workflow_graph_api import receive_event
        waiting=[a for a in state['activations'].values() if a['status']=='waiting_event']
        if waiting:
            earliest=min(a['created_at'] for a in waiting)
            kinds={s['event_type'] for s in graph_for(release,business)['subscriptions']}
            events=db.scalars(select(Event).where(Event.workspace_id==p.workspace_id,Event.kind.in_(kinds),
                Event.created_at>=datetime.fromtimestamp(earliest,timezone.utc))
                .order_by(Event.created_at,Event.id)).all()
            for event in events:
                if 'correlation' not in event.data or 'output' not in event.data:continue
                receive_event(db,p,task,release,business,event)
                if task.data['graph_runtime']['status'] in {'cancelled','succeeded','stopped','failed'}:return 'succeeded'
            state=task.data['graph_runtime']
        from .workflow_job_dependencies_v264 import trace_for, observer_options, save_control
        observer = trace_for(db, p, task)
        result = invoke_pure(graph_api.advance, graph_for(release, business), state, clock(), **observer_options(observer))
        if observer is not None:
            task.data = {**task.data, 'graph_runtime': result}
            save_control(db, p, task, result, observer)
        settle(db, p, task, release, business, result)
    return 'succeeded'


def enqueue_event(db, event):
    """Queue independent graph recipients; never delay the original receiver."""
    from .models import Task, InstanceRelease
    tasks = db.scalars(select(Task).where(Task.workspace_id == event.workspace_id,
        Task.status == 'workflow_waiting', Task.data.has_key('graph_runtime')).order_by(Task.id)).all()
    matched = 0
    for task in tasks:
        release = db.get(InstanceRelease, task.data['instance_release_id'])
        business = next(b for b in release.data['snapshot']['businesses'] if b['id'] == task.data['business_id'])
        graph = graph_for(release, business)
        from .workflow_graph_waits import enabled
        if enabled(business_binding(release, business)): continue
        if event.kind not in {s['event_type'] for s in graph['subscriptions']}: continue
        matched += 1
        key = 'graph-event:' + task.id + ':' + event.id
        if db.scalar(select(Job.id).where(Job.workspace_id == event.workspace_id, Job.idempotency_key == key)): continue
        db.add(Job(workspace_id=event.workspace_id, kind='receive_full_semantic_graph_event',
            idempotency_key=key, status='queued', data={'actor_id': task.owner_id, 'task_id': task.id,
                'event_id': event.id, 'event_hash': digest(event.data), 'graph_hash': task.data['graph_runtime']['graph_hash'],
                'cancel_epoch': task.data['graph_runtime']['cancel_epoch']}))
    return matched


def public_view(task, release, business, p, db=None):
    from .instance_api import view
    binding = business_binding(release, business); graph = binding['graph']
    state = invoke_pure(graph_api.check_run, graph, task.data['graph_runtime'])
    humans = []
    nested = binding.get('schema') == 'business-flow-nested-execution-binding-v195'
    human_ids = list(state['ready_human_ids'])
    if nested:
        # Waiting inputs must remain visible/editable without changing native
        # readiness or granting completion authority to a blocked activation.
        human_ids.extend(identity for identity, item in state['activations'].items()
            if item['status'] == 'waiting_guard' and identity not in human_ids
            and node_for(graph, item)['kind'] == 'human')
    for identity in human_ids:
        activation = state['activations'][identity]; node = node_for(graph, activation)
        source=next((s for s in business['flow']['steps'] if s['id']==node['source_step_id']),{})
        from .business_flow_execution_inputs import describe as original_inputs
        required_inputs = original_inputs(binding, state, identity)
        humans.append({'activation_id': identity, 'node_id': node['id'], 'role': node['role'],
            'source_step_id':node['source_step_id'],'name':source.get('name','人工核对'),'purpose':source.get('purpose',''),
            'actor_id': node['actor_id'], 'version': activation['version'],
            'can_edit': node['actor_id'] == p.user_id and p.role in {'admin', 'operator'},
            'output_schema': node['output_schema'], 'draft': copy.deepcopy(activation.get('draft')),
            **({'activation_status': activation['status'], 'can_complete':
                activation['status'] == 'ready' and identity in state['ready_human_ids']
                and all(x['available'] for x in required_inputs)} if nested else {}),
            **({'required_inputs': required_inputs, 'inputs_available': all(x['available'] for x in required_inputs)}
                if binding.get('schema') in {'business-flow-execution-binding-v1', 'business-flow-nested-execution-binding-v195'} else {})})
    labels={}
    for identity,activation in state['activations'].items():
        node=node_for(graph,activation)
        source=next((s for s in business['flow']['steps'] if s['id']==node['source_step_id']),{})
        labels[identity]={'name':source.get('name') or {'wait':'等待外部回执','fork':'同时处理','join':'等待各项处理完成',
            'decision':'判断下一步','end':'流程结束'}.get(node['kind'],'协同处理'),
            'source_step_id':node['source_step_id'],'kind':node['kind'],
            'actor_id':node.get('actor_id'),
            'output_schema':copy.deepcopy(node.get('output_schema') if node['kind']=='human'
                else node['execution']['output_schema'] if node['kind']=='ability' else None),
            'can_retry':node['kind']=='ability' and node.get('actor_id')==p.user_id and p.role in {'admin','operator'}
                and task.data.get(TRANSPORT,{}).get(identity,{}).get('phase')=='blocked'
                and state['status'] not in {'cancelled','succeeded','stopped','failed'}}
    from .workflow_graph_waits import public as message_status, enabled
    from .workflow_graph_goal_view_v133 import project as goal_projection
    goal_gate = goal_projection(db, p, task)
    from .signal_graph_routing_v134 import task_state as route_projection
    route_gate = route_projection(db,p,task) if db is not None else (
        {'schema':'signal-graph-task-state-v134','status':'unavailable',
            'message':'原自动交接依据需要服务器核对；草稿和历史仍保留',
            'can_submit_new_result':False,'can_retry_ability':False,'delivery_id':None}
        if 'signal_graph_origin_v134' in task.data else None)
    notices = None
    if db is not None and 'workflow_service_notices_v195' in task.data:
        from .workflow_service_v195 import notices as service_notices
        notices = service_notices(db,p,task)
    simulation_review_ids = []
    if binding.get('simulation_only') is True:
        from .business_flow_nested_adoption_v195 import service_delegations
        review_delegations = (service_delegations(binding['translation'])
            if binding.get('schema') == 'business-flow-nested-execution-binding-v195' else {})
        for identity, activation in state['activations'].items():
            node = node_for(graph, activation)
            if (node['kind'] == 'ability' and activation['status'] == 'ready'
                    and review_delegations.get(node['id'], {}).get('operation') == 'execute_registered_tool_plan'
                    and task.data.get(TRANSPORT, {}).get(identity, {}).get('phase') == 'blocked'
                    and state['status'] not in {'cancelled', 'succeeded', 'stopped', 'failed'}):
                simulation_review_ids.append(identity)
    simulation_receipts = {}
    if db is not None and 'graph_simulation_reviews_v202' in task.data:
        from .workflow_simulation_review_v202 import public_receipts
        simulation_receipts = public_receipts(db, task)
    return {**view(task), **({'graph_goal_gate_v133': goal_gate} if goal_gate is not None else {}),
        **({'workflow_notices_v195': notices} if notices is not None else {}),
        **({'signal_graph_route_v134': route_gate} if route_gate is not None else {}),
        'business': business,'graph_node_labels':labels, 'roles': release.data['snapshot']['roles'],
        'graph_simulation_review_activation_ids': simulation_review_ids,
        'graph_simulation_review_receipts_v202': simulation_receipts,
        **({'workflow_review_mode': binding['review_mode'], 'workflow_simulation_only': binding['simulation_only']}
            if binding.get('schema') == 'business-flow-nested-execution-binding-v195' else {}),
        'profile': release.data['snapshot']['profile'], 'graph_pending_humans': humans,
        'graph_waits': [copy.deepcopy(a) for a in state['activations'].values() if a['status'].startswith('waiting')],
        'completed': state['status'] == 'succeeded',
        **({'graph_message_status': message_status(task, binding, p)} if enabled(binding) else {}),
        'permissions': {'can_cancel_task': p.role == 'admin' or task.owner_id == p.user_id,
            'can_complete_step': False, 'can_save_note': False, 'can_run_ability': False}}
