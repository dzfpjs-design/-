"""Task/Invocation-owned pixel producers. Resource-panel runs remain independent."""
import copy
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import select, text
from .contracts import digest, Manifest
from .models import WorkflowPixelRun, Task, InstanceRelease, DomainInstance, Job, Invocation, Capability, Audit
from .services import scoped, published, validate
from .evidence import evidence_bytes, save_immutable
from .leases import ownership
from .relations import lock_current_actor
from .local_changer_install import trace, model_path
from .pixel_change_runs_v249 import _model
from . import pixel_change_codec_v249 as codec
from .workflow_pixel_manifest_v250 import ADAPTER, validate_manifest
from .workflow_pixel_sources_v250 import verify_task, local_inputs

INPUT_ACTION = 'workflow_pixel.input_frozen_v250'
RESULT_ACTION = 'workflow_pixel.result_frozen_v250'
FAILED_ACTION = 'workflow_pixel.run_failed_v250'
SETTLED_ACTION = 'workflow_pixel.result_settled_v250'
ERROR = '无法核对本次业务像素分析的原图、调用或保存结果，原记录保留'


def need(value, message=ERROR):
    if not value:
        raise HTTPException(409, message)


def _row(db, p, cls, identity):
    value = db.scalar(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
        .execution_options(populate_existing=True))
    need(value is not None)
    return value


def _audit(db, p, action, identity, value):
    proof = Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=action, object_id=identity, data=copy.deepcopy(value))
    db.add(proof); db.flush()
    return proof


def _proof(db, p, action, identity, proof_id, actor_id, value):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).limit(2)))
    need(len(rows) == 1 and rows[0].id == proof_id and rows[0].actor_id == actor_id
         and digest(rows[0].data) == digest(value))
    return rows[0]


def _input_anchor(run, lease_token):
    d = run.data
    return {'schema': 'workflow-pixel-input-v250', 'run_id': run.id, 'task_id': run.task_id,
        'job_id': run.job_id, 'invocation_id': run.invocation_id, 'actor_id': run.owner_id,
        'instance_id': d['instance_id'], 'release_id': d['release_id'], 'business_id': d['business_id'],
        'step_id': d['step_id'], 'execution_scope': d['execution_scope'], 'request_hash': d['request_hash'],
        'sources_hash': d['sources_hash'], 'model_trace_hash': d['model_trace_hash'],
        'runtime_hash': d['runtime_hash'], 'capability_id': run.capability_id, 'capability_hash': d['capability_hash'],
        'lease_token': lease_token}


def _result_anchor(run):
    return {'schema': 'workflow-pixel-result-proof-v250', 'run_id': run.id, 'task_id': run.task_id,
        'job_id': run.job_id, 'invocation_id': run.invocation_id, 'input_audit_id': run.data['input_audit_id'],
        'request_hash': run.data['request_hash'], 'result_hash': run.data['result_hash'],
        'output_hash': digest(run.data['output'])}


def _result(run):
    if run.data['result'] is None:
        return None
    return {**copy.deepcopy(run.data['result']), 'result_hash': run.data['result_hash']}


def _approval_scope(db, p, task):
    from .layered_approval_store_v175 import managed, checked
    row = managed(db, p, task)
    if row is None:
        return None
    data = checked(row)
    need(data.get('execution') is not None)
    return {'managed_id': row.id, 'context_hash': data['context_hash'],
        'submission_id': data['current_submission_id'], 'submission_hash': data['execution']['submission_hash']}


def artifact_bytes(db, p, run, kind):
    refs = [v for v in run.data['result']['artifacts'] if v['kind'] == kind]
    need(len(refs) == 1)
    ref = refs[0]; ev, raw = evidence_bytes(db, p, ref['evidence_id'])
    need(ev.sha256 == ref['sha256'] and ev.media_type == ref['media_type'] and ev.size_bytes == ref['size_bytes'] == len(raw)
         and digest(ev.data) == digest({'purpose': 'workflow_pixel_output_v250', 'run_id': run.id,
             'task_id': run.task_id, 'invocation_id': run.invocation_id, 'kind': kind,
             'request_hash': run.data['request_hash'], 'coordinate_space': 'pixel',
             'cloud_model_ids': [], 'uploaded_by': run.owner_id}))
    return ev, raw


def checked_saved_run(db, p, run):
    """Verify stored model evidence without declaring its producer completed."""
    try:
        d = run.data
        need(run.workspace_id == p.workspace_id and d['schema'] == 'workflow-pixel-run-v250'
             and run.status in {'running', 'succeeded', 'failed', 'cancelled'})
        if run.status != 'succeeded':
            need(d['result'] is None and d['result_hash'] is None and d['result_audit_id'] is None and d['output'] is None)
        from .workspace_api import saved_execution
        task, release, business = saved_execution(db, p, run.task_id)
        need(task.data['instance_id'] == d['instance_id'] and release.id == d['release_id']
             and business['id'] == d['business_id'] and run.owner_id != '')
        package = _row(db, p, Capability, release.package_id)
        instance = _row(db, p, DomainInstance, release.instance_id)
        need(package.id == task.package_id and package.namespace == instance.namespace
             and digest(package.data) == package.content_hash
             and digest(release.data['snapshot']) == release.configuration_hash
             and package.data.get('specification', {}).get('configuration_hash') == release.configuration_hash)
        pair = verify_task(db, p, task)
        need(pair is not None and digest(d['sources']) == d['sources_hash']
             and digest(d['sources']) == digest({'before': pair['before'], 'after': pair['after']})
             and digest(d['model_trace']) == d['model_trace_hash'] and digest(d['request']) == d['request_hash'])
        cap = _row(db, p, Capability, run.capability_id)
        frozen = Manifest.model_validate(d['capability_snapshot'])
        validate_manifest(db, p, frozen, live=False)
        need(digest(frozen.model_dump()) == d['capability_hash'] == cap.content_hash
             and digest(cap.data) == cap.content_hash and digest(frozen.execution['model_trace']) == d['model_trace_hash']
             and frozen.execution['runtime_hash'] == d['runtime_hash'])
        job = _row(db, p, Job, run.job_id); call = _row(db, p, Invocation, run.invocation_id)
        need(job.kind in {'execute_workflow_step', 'execute_full_semantic_graph_node'}
             and job.data.get('task_id') == task.id and job.data.get('actor_id') == run.owner_id
             and call.idempotency_key == job.data.get('invocation_key') and call.capability_id == cap.id
             and call.data.get('job_id') == job.id and call.data.get('capability_hash') == d['capability_hash']
             and call.data.get('input_hash') == digest(d['request']['inputs']))
        expected_inputs = {'task_id': task.id, 'pair_ref': pair}
        need(digest(d['request']['inputs']) == digest(expected_inputs))
        scope = d['execution_scope']
        if job.kind == 'execute_workflow_step':
            step = next((s for s in business['flow']['steps'] if s['id'] == d['step_id']), None)
            need(step is not None and step['execution']['adapter'] == ADAPTER
                 and step['execution']['capability_id'] == cap.id and step['execution']['capability_hash'] == cap.content_hash
                 and scope['schema'] == 'workflow-pixel-compact-scope-v250'
                 and digest(scope) == digest({'schema': 'workflow-pixel-compact-scope-v250', 'task_id': task.id,
                     'job_id': job.id, 'step_id': job.data['scope']['step_id'], 'visit': job.data['scope']['visit'],
                     'invocation_key': call.idempotency_key, 'input_hash': call.data['input_hash'],
                     'capability_id': cap.id, 'capability_hash': cap.content_hash})
                 and digest(job.data['scope']['inputs']) == digest(expected_inputs))
        else:
            from .workflow_graph_scope import record_node
            # The existing historical graph record verifier uses this frozen
            # graph scope; it never borrows the compact automatic-step state.
            proxy = type('PixelGraphRecord', (), {'data': {'workflow_graph_scope': scope,
                'workflow_task_id': task.id, 'workflow_job_id': job.id, 'workflow_capability_id': cap.id,
                'workflow_capability_hash': cap.content_hash, 'workflow_step_id': d['step_id'],
                'workflow_capability_snapshot': d['capability_snapshot']}})()
            node = record_node(db, p, task, proxy)
            need(node['execution']['adapter'] == ADAPTER and digest(job.data['inputs']) == digest(expected_inputs))
        request = {'schema': 'workflow-pixel-request-v250', 'task_id': task.id, 'inputs': expected_inputs,
            'job_id': job.id, 'invocation_id': call.id, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
            'execution_scope': scope, 'model_trace': d['model_trace'], 'threshold_text': frozen.execution['threshold_text'],
            'runtime_hash': d['runtime_hash'], 'approval_scope': d['request'].get('approval_scope')}
        approval = request['approval_scope']
        if approval is not None:
            need(type(approval) is dict and set(approval) == {'managed_id', 'context_hash', 'submission_id', 'submission_hash'}
                 and task.data.get('layered_approval_v175') == approval['managed_id'])
        need(digest(request) == d['request_hash'])
        input_audit = _proof(db, p, INPUT_ACTION, run.id, d['input_audit_id'], run.owner_id, _input_anchor(run, call.data.get('lease_token')))
        result_audit = None; artifacts = {}
        if run.status == 'succeeded':
            need(d['result'] is not None and digest(d['result']) == d['result_hash']
                 and type(d['result']['artifacts']) is list and len(d['result']['artifacts']) == 6
                 and {v['kind'] for v in d['result']['artifacts']} == {*codec.FILES, 'manifest'})
            output = {'pixel_run_id': run.id, 'attributes': {
                **{k: d['result'][k] for k in ('candidate_pixels', 'common_valid_pixels', 'unknown_pixels')},
                'not_authoritative': True}, 'anomalies': [], 'pixel_comparison': _result(run)}
            need(digest(output) == digest(d['output']))
            validate(frozen.output_schema, output)
            result_audit = _proof(db, p, RESULT_ACTION, run.id, d['result_audit_id'], run.owner_id, _result_anchor(run))
            for kind in {*codec.FILES, 'manifest'}:
                artifacts[kind] = artifact_bytes(db, p, run, kind)[0]
        return {'run': run, 'task': task, 'release': release, 'business': business, 'job': job, 'invocation': call,
            'capability_snapshot': frozen.model_dump(), 'input_audit': input_audit, 'result_audit': result_audit,
            'completion_audit': None, 'settlement_audit': None, 'sources': copy.deepcopy(d['sources']), 'artifacts': artifacts}
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(409, ERROR) from None
        raise
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration):
        raise HTTPException(409, ERROR) from None


def _completed(db, p, context):
    run, task, call, job = (context[k] for k in ('run', 'task', 'invocation', 'job'))
    need(run.status == 'succeeded' and call.status == 'succeeded'
         and digest(call.data.get('output')) == digest(run.data['output']))
    if job.kind == 'execute_workflow_step':
        receipt = [r for r in task.data['workflow_runtime']['receipts'] if r.get('invocation_id') == call.id]
        proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'workflow.ability_completed', Audit.object_id == task.id,
            Audit.data['invocation'].as_string() == call.id).limit(2)))
        need(len(receipt) == len(proofs) == 1 and digest(receipt[0].get('output')) == digest(run.data['output'])
             and proofs[0].actor_id == run.owner_id and proofs[0].data.get('step') == run.data['step_id'])
    else:
        scope = run.data['execution_scope']
        proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'workflow.graph.node_completed', Audit.object_id == task.id,
            Audit.data['activation_id'].as_string() == scope['activation_id'],
            Audit.data['invocation_id'].as_string() == call.id).limit(2)))
        need(len(proofs) == 1 and proofs[0].actor_id == run.owner_id)
    context['completion_audit'] = proofs[0]
    return context


def checked_completed_run(db, p, run, job):
    context = _completed(db, p, checked_saved_run(db, p, run))
    lease = ownership(db)
    need(context['job'].id == job.id and job.status == 'running' and lease.get('job_id') == job.id
         and lease.get('lease_token') == job.lease_token and job.data['actor_id'] == p.user_id)
    return context


def checked_run(db, p, run, *, historical=True, lock_sources=False):
    context = _completed(db, p, checked_saved_run(db, p, run))
    settled = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == SETTLED_ACTION, Audit.object_id == run.id).limit(2)))
    need(len(settled) <= 1)
    if settled:
        return checked_settled_run(db, p, run, settled[0])
    need(context['job'].status == 'succeeded')
    return context


def runs_for_task(db, p, task):
    verify_task(db, p, task)
    rows = list(db.scalars(select(WorkflowPixelRun).where(WorkflowPixelRun.workspace_id == p.workspace_id,
        WorkflowPixelRun.task_id == task.id).order_by(WorkflowPixelRun.created_at, WorkflowPixelRun.id)))
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == INPUT_ACTION, Audit.data['task_id'].as_string() == task.id)))
    need({v.object_id for v in proofs} == {r.id for r in rows} and len(proofs) == len(rows))
    return rows


def consumed_runs_for_task(db, p, task):
    rows = runs_for_task(db, p, task); wanted = set()
    for receipt in task.data.get('workflow_runtime', {}).get('receipts', []):
        value = receipt.get('output', {}).get('pixel_run_id')
        if value is not None:
            wanted.add(value)
    for item in task.data.get('graph_jobs', {}).values():
        if item.get('phase') == 'succeeded' and item.get('invocation_id'):
            call = _row(db, p, Invocation, item['invocation_id'])
            value = call.data.get('output', {}).get('pixel_run_id')
            if value is not None:
                wanted.add(value)
    by_id = {r.id: r for r in rows}; need(wanted <= set(by_id))
    from .native_pixel_provenance_v250 import ISSUED_ACTION
    for run in rows:
        action = 'workflow.ability_completed' if run.data['execution_scope'].get('schema') == 'workflow-pixel-compact-scope-v250' else 'workflow.graph.node_completed'
        field = 'invocation' if action == 'workflow.ability_completed' else 'invocation_id'
        completed = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == task.id, Audit.action == action, Audit.data[field].as_string() == run.invocation_id).limit(1))
        issued = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == ISSUED_ACTION, Audit.data['run_id'].as_string() == run.id).limit(1))
        if completed is not None or issued is not None:
            need(run.id in wanted, '原业务成果的步骤引用缺失，不能跳过数据接收或人工核查')
            _completed(db, p, checked_saved_run(db, p, run))
    return [r for r in rows if r.id in wanted]


def run_view(run, *, settlement_action=None):
    d = run.data
    return {'id': run.id, 'version': run.version, 'status': run.status, 'task_id': run.task_id,
        'job_id': run.job_id, 'invocation_id': run.invocation_id, 'capability_id': run.capability_id,
        'capability_hash': d['capability_hash'], 'execution_scope': copy.deepcopy(d['execution_scope']),
        'request_hash': d['request_hash'], 'sources': copy.deepcopy(d['sources']), 'model_trace': copy.deepcopy(d['model_trace']),
        'threshold_text': d['request']['threshold_text'], 'result': _result(run), 'error': d['error'],
        'settlement_action': settlement_action or {'can_settle': False, 'reason': '请从原任务核对交接恢复权限', 'task_version': None}}


def _live(db, p, task_id, cap, inputs, key, *, physical=False):
    lock_current_actor(db, p)
    lease = ownership(db)
    need(lease.get('job_id') and lease.get('lease_token'))
    job = _row(db, p, Job, lease['job_id'])
    need(job.status == 'running' and job.lease_token == lease['lease_token']
         and job.data.get('actor_id') == p.user_id and job.data.get('invocation_key') == key)
    call = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id, Invocation.idempotency_key == key))
    need(call is not None and call.status == 'running' and call.data.get('job_id') == job.id
         and call.data.get('lease_token') == lease['lease_token'] and call.capability_id == cap.id
         and call.data.get('input_hash') == digest(inputs) and call.data.get('capability_hash') == cap.content_hash)
    from .workflow_graph_scope import owned_graph_job, graph_context
    if owned_graph_job(db, p):
        task, job, node, scope = graph_context(db, p, key, cap)
        from .workspace_api import saved_execution
        _, release, business = saved_execution(db, p, task.id)
        step_id = node['source_step_id'] or node['id']
    else:
        from .workspace_api import execution
        task, release, business = execution(db, p, task_id, True)
        auto = task.data['workflow_runtime'].get('automatic', {})
        need(job.kind == 'execute_workflow_step' and job.data.get('task_id') == task.id
             and task.status == 'workflow_running' and auto.get('job_id') == job.id
             and auto.get('phase') == 'running' and digest(job.data['scope']['inputs']) == digest(inputs)
             and len(task.data['workflow_runtime']['receipts']) == job.data['scope']['visit'])
        step_id = job.data['scope']['step_id']
        step = next((s for s in business['flow']['steps'] if s['id'] == step_id), None)
        need(step is not None and step['execution']['adapter'] == ADAPTER
             and step['execution']['capability_id'] == cap.id and step['execution']['capability_hash'] == cap.content_hash)
        scope = {'schema': 'workflow-pixel-compact-scope-v250', 'task_id': task.id, 'job_id': job.id,
            'step_id': step_id, 'visit': job.data['scope']['visit'], 'invocation_key': key,
            'input_hash': digest(inputs), 'capability_id': cap.id, 'capability_hash': cap.content_hash}
    need(task.id == task_id)
    instance = _row(db, p, DomainInstance, task.data['instance_id'])
    need(instance.status == 'partially_active' and business['id'] in instance.data.get('enabled_business_ids', []),
         '业务已暂停或停止，本次分析不接受新的结果；原图和记录保留')
    actual = published(db, p, cap.id)
    need(actual.content_hash == cap.content_hash and digest(actual.data) == actual.content_hash)
    local_inputs(db, p, task, {'execution': {'adapter': ADAPTER}}, inputs)
    model = _model(db, p, cap.data['execution']['model_trace']['installation_id'], lock=True, physical=physical)
    need(digest(trace(model)) == digest(cap.data['execution']['model_trace'])
         and cap.data['execution']['runtime_hash'] == codec.RUNTIME_HASH,
         '原模型或处理版本已变化，请核对后登记新能力，不能替换原输入')
    return task, release, business, job, call, scope, step_id


def execute(db, p, cap, inputs, key):
    validate_manifest(db, p, Manifest.model_validate(cap.data))
    task, release, business, job, call, scope, step_id = _live(db, p, inputs['task_id'], cap, inputs, key, physical=True)
    existing = db.scalar(select(WorkflowPixelRun).where(WorkflowPixelRun.workspace_id == p.workspace_id,
        WorkflowPixelRun.invocation_id == call.id))
    if existing is not None:
        context = checked_saved_run(db, p, existing)
        need(existing.status == 'succeeded' and context['job'].id == job.id,
             '原调用尚无完整成功结果，请从原流程明确重试，不能重复运行同一调用')
        return copy.deepcopy(existing.data['output'])
    pair = inputs['pair_ref']; sources = {'before': pair['before'], 'after': pair['after']}
    ex = cap.data['execution']
    request = {'schema': 'workflow-pixel-request-v250', 'task_id': task.id, 'inputs': copy.deepcopy(inputs),
        'job_id': job.id, 'invocation_id': call.id, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'execution_scope': scope, 'model_trace': ex['model_trace'], 'threshold_text': ex['threshold_text'],
        'runtime_hash': ex['runtime_hash'], 'approval_scope': _approval_scope(db, p, task)}
    run = WorkflowPixelRun(workspace_id=p.workspace_id, task_id=task.id, job_id=job.id, invocation_id=call.id,
        capability_id=cap.id, owner_id=p.user_id, status='running', data={
            'schema': 'workflow-pixel-run-v250', 'instance_id': release.instance_id, 'release_id': release.id,
            'business_id': business['id'], 'step_id': step_id, 'execution_scope': scope,
            'request': request, 'request_hash': digest(request), 'sources': sources, 'sources_hash': digest(sources),
            'model_trace': copy.deepcopy(ex['model_trace']), 'model_trace_hash': digest(ex['model_trace']),
            'runtime_hash': ex['runtime_hash'], 'capability_snapshot': copy.deepcopy(cap.data),
            'capability_hash': cap.content_hash, 'input_audit_id': None, 'result_audit_id': None,
            'error': None, 'result': None, 'result_hash': None, 'output': None})
    db.add(run); db.flush()
    proof = _audit(db, p, INPUT_ACTION, run.id, _input_anchor(run, ownership(db)['lease_token']))
    run.data = {**run.data, 'input_audit_id': proof.id}; identity = run.id
    before, after = [evidence_bytes(db, p, sources[side]['id'])[1] for side in ('before', 'after')]
    db.commit()
    def guard():
        db.expire_all()
        current = _row(db, p, WorkflowPixelRun, identity)
        need(current.status == 'running')
        _live(db, p, inputs['task_id'], cap, inputs, key)
        checked_saved_run(db, p, current)
        db.commit()
    try:
        native, files = codec.call(before, after, ex['threshold_text'], weights=model_path(), guard=guard)
        task, release, business, job, call, _, _ = _live(db, p, inputs['task_id'], cap, inputs, key, physical=True)
        run = _row(db, p, WorkflowPixelRun, identity)
        need(run.status == 'running')
        artifacts = [_save_output(db, p, run, kind, raw, media) for kind, (raw, media) in files.items()]
        manifest = {'schema': 'workflow-pixel-manifest-v250', 'run_id': run.id, 'task_id': task.id,
            'job_id': job.id, 'invocation_id': call.id, 'request': request, 'request_hash': run.data['request_hash'],
            'sources': sources, 'model_trace': ex['model_trace'], 'coordinate_space': 'pixel', 'georeference_available': False,
            'candidate_only': True, 'object_state_updated': False, 'native_result': native, 'artifacts': artifacts}
        artifacts.append(_save_output(db, p, run, 'manifest',
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode(), 'application/json'))
        result = {'schema': 'workflow-pixel-result-v250', 'coordinate_space': 'pixel', 'georeference_available': False,
            'candidate_only': True, 'cloud_calls': 0, 'object_state_updated': False, 'cloud_shadow_screening': 'not_applied',
            **{k: native[k] for k in ('width', 'height', 'total_pixels', 'common_valid_pixels', 'unknown_pixels', 'candidate_pixels')},
            'threshold_text': ex['threshold_text'], 'inference_seconds_text': str(native['inference_seconds']), 'artifacts': artifacts}
        run.status = 'succeeded'; run.version += 1
        run.data = {**run.data, 'result': result, 'result_hash': digest(result)}
        output = {'pixel_run_id': run.id, 'attributes': {**{k: native[k] for k in ('candidate_pixels', 'common_valid_pixels', 'unknown_pixels')},
            'not_authoritative': True}, 'anomalies': [], 'pixel_comparison': _result(run)}
        validate(cap.data['output_schema'], output)
        run.data = {**run.data, 'output': output}
        result_proof = _audit(db, p, RESULT_ACTION, run.id, _result_anchor(run))
        run.data = {**run.data, 'result_audit_id': result_proof.id}
        # The gateway saves this exact output and Invocation status in the same
        # transaction. This is model evidence, not parent completion or ACK.
        return copy.deepcopy(output)
    except Exception:
        from .leases import LeaseLost
        import sys
        if isinstance(sys.exception(), LeaseLost):
            raise
        db.rollback()
        current = _row(db, p, WorkflowPixelRun, identity)
        if current.status == 'running':
            current.status = 'failed'; current.version += 1
            current.data = {**current.data, 'error': '本次本地分析未完成，请从原业务步骤核对后明确重试；原图保留'}
            _audit(db, p, FAILED_ACTION, current.id, {'task_id': current.task_id, 'job_id': current.job_id,
                'invocation_id': current.invocation_id, 'request_hash': current.data['request_hash'], 'status': 'failed'})
        raise


def _save_output(db, p, run, kind, raw, media):
    ev = save_immutable(db, p, raw, media, {'purpose': 'workflow_pixel_output_v250', 'run_id': run.id,
        'task_id': run.task_id, 'invocation_id': run.invocation_id, 'kind': kind,
        'request_hash': run.data['request_hash'], 'coordinate_space': 'pixel'})
    saved, content = evidence_bytes(db, p, ev.id)
    need(content == raw and saved.size_bytes == len(raw))
    return {'kind': kind, 'evidence_id': ev.id, 'sha256': ev.sha256, 'size_bytes': ev.size_bytes, 'media_type': ev.media_type}


def local_result(db, p, task, step, inputs, output):
    if step['execution'].get('adapter') != ADAPTER:
        return
    run = _row(db, p, WorkflowPixelRun, output.get('pixel_run_id'))
    context = checked_saved_run(db, p, run)
    need(run.task_id == task.id and context['invocation'].status == 'succeeded'
         and run.capability_id == step['execution']['capability_id']
         and digest(output) == digest(run.data['output']) and digest(inputs) == digest(run.data['request']['inputs']))


def checked_settled_run(db, p, run, settlement):
    context = _completed(db, p, checked_saved_run(db, p, run))
    proof = _row(db, p, Audit, settlement.id); value = proof.data
    expected = {'schema', 'audit_id', 'workspace_id', 'actor_id', 'task_id', 'run_id', 'job_id', 'job_status',
        'invocation_id', 'completion_audit_id', 'input_audit_id', 'result_audit_id', 'request_hash', 'result_hash',
        'output_hash', 'sources_hash', 'body', 'body_hash', 'settlement_hash'}
    from .workflow_pixel_contracts_v250 import Settlement
    try:
        body = Settlement.model_validate(value['body'])
        need(set(value) == expected and value['schema'] == 'workflow-pixel-settlement-proof-v250'
             and proof.action == SETTLED_ACTION and proof.object_id == run.id
             and value['audit_id'] == proof.id and proof.actor_id == value['actor_id']
             and value['workspace_id'] == p.workspace_id and value['task_id'] == run.task_id
             and value['run_id'] == run.id and value['job_id'] == run.job_id
             and value['job_status'] == context['job'].status == 'failed'
             and value['invocation_id'] == run.invocation_id
             and value['completion_audit_id'] == context['completion_audit'].id
             and value['input_audit_id'] == context['input_audit'].id
             and value['result_audit_id'] == context['result_audit'].id
             and value['request_hash'] == run.data['request_hash'] and value['result_hash'] == run.data['result_hash']
             and value['sources_hash'] == run.data['sources_hash'] and value['output_hash'] == digest(run.data['output'])
             and digest(body.model_dump()) == value['body_hash']
             and digest({k: v for k, v in value.items() if k != 'settlement_hash'}) == value['settlement_hash'])
    except (ValueError, KeyError, TypeError):
        need(False)
    originals = list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action == SETTLED_ACTION, Audit.object_id == run.id).limit(2)))
    need(originals == [proof.id])
    context['settlement_audit'] = proof
    return context


def settlement_action(db, p, run):
    value = {'can_settle': False, 'reason': '当前没有需要恢复的已完成结果交接', 'task_version': None}
    try:
        context = _completed(db, p, checked_saved_run(db, p, run)); task = context['task']
        value['task_version'] = task.version
        if context['job'].status != 'failed':
            return value
        from .workspace_api import execution
        execution(db, p, task.id, lock_sources=False)
        need(digest(_approval_scope(db, p, task)) == digest(run.data['request'].get('approval_scope')),
             '原审批执行范围已由新的范围取代，不能恢复旧结果交接')
        need(task.status in {'workflow_waiting', 'workflow_failed', 'workflow_blocked', 'workflow_completed'},
             '原任务已撤回、取消或执行范围变化，不能新发交接；历史仍保留')
        if 'graph_runtime' in task.data:
            need(task.data['graph_runtime']['cancel_epoch'] == run.data['execution_scope']['cancel_epoch'],
                 '原图节点范围已被新范围取代，不能恢复旧交接')
        need(p.role in {'admin', 'operator'} and (p.role == 'admin' or p.user_id in {task.owner_id, run.owner_id}),
             '请由任务发起人、原执行人或管理员核对交接恢复')
        from .native_pixel_provenance_v250 import observations, ISSUED_ACTION
        issued = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == ISSUED_ACTION, Audit.data['run_id'].as_string() == run.id).limit(1))
        settled = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == SETTLED_ACTION, Audit.object_id == run.id).limit(1))
        need(not observations(db, p, run.id) and issued is None and settled is None,
             '原交接已存在或需要检查，不能再次生成')
        value.update(can_settle=True, reason='模型成果和原步骤已完整保存，可以恢复交接；不会重新分析或改写旧失败任务')
    except HTTPException as error:
        value['reason'] = str(error.detail)
    return value


def settle(db, p, task_id, run_id, body):
    from .pixel_change_runs_v249 import _lock_nonce
    from .models import uid
    _lock_nonce(db, p, 'workflow-settlement:' + body.request_id)
    saved = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == SETTLED_ACTION, Audit.data['body']['request_id'].as_string() == body.request_id).limit(2)))
    need(len(saved) <= 1)
    if saved:
        proof = saved[0]
        need(proof.actor_id == p.user_id and proof.object_id == run_id and proof.data['task_id'] == task_id
             and proof.data['body_hash'] == digest(body.model_dump()), '原交接操作号不能更换任务、结果或正文')
        # Historical command recovery is the saved proof, not a fresh execution
        # or a new declaration that current source files are still intact.
        need(proof.data['settlement_hash'] == digest({k: v for k, v in proof.data.items() if k != 'settlement_hash'}))
        return settlement_result(proof)
    lock_current_actor(db, p)
    original_task = scoped(db, Task, task_id, p)
    db.scalar(select(DomainInstance).where(DomainInstance.workspace_id == p.workspace_id,
        DomainInstance.id == original_task.data['instance_id']).with_for_update(read=True)
        .execution_options(populate_existing=True))
    task = scoped(db, Task, task_id, p, True); run = scoped(db, WorkflowPixelRun, run_id, p, True)
    need(run.task_id == task.id)
    from .services import check_version
    check_version(task, body.version)
    from .workspace_api import execution
    execution(db, p, task.id, lock_sources=True)
    action = settlement_action(db, p, run); need(action['can_settle'], action['reason'])
    context = _completed(db, p, checked_saved_run(db, p, run))
    identity = uid()
    value = {'schema': 'workflow-pixel-settlement-proof-v250', 'audit_id': identity, 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'task_id': task.id, 'run_id': run.id, 'job_id': run.job_id, 'job_status': 'failed',
        'invocation_id': run.invocation_id, 'completion_audit_id': context['completion_audit'].id,
        'input_audit_id': context['input_audit'].id, 'result_audit_id': context['result_audit'].id,
        'request_hash': run.data['request_hash'], 'result_hash': run.data['result_hash'],
        'output_hash': digest(run.data['output']), 'sources_hash': run.data['sources_hash'],
        'body': body.model_dump(), 'body_hash': digest(body.model_dump())}
    proof = Audit(id=identity, workspace_id=p.workspace_id, actor_id=p.user_id, action=SETTLED_ACTION,
        object_id=run.id, data={**value, 'settlement_hash': digest(value)})
    db.add(proof); db.flush()
    from .native_pixel_receipts_v250 import emit_settled
    need(emit_settled(db, p, run, proof) == 1)
    return settlement_result(proof)


def settlement_result(proof):
    value = proof.data
    return {'schema': 'workflow-pixel-settlement-result-v250', 'request_id': value['body']['request_id'],
        'body_hash': value['body_hash'], 'task_id': value['task_id'], 'run_id': value['run_id'], 'settlement': copy.deepcopy(value)}


def recover_settlement(db, p, task_id, request_id, expected_body_hash):
    scoped(db, Task, task_id, p)
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == SETTLED_ACTION, Audit.data['body']['request_id'].as_string() == request_id).limit(2)))
    need(len(rows) <= 1)
    result = None
    if rows:
        proof = rows[0]
        if proof.actor_id != p.user_id or proof.data.get('task_id') != task_id:
            raise HTTPException(404, '原交接申请不属于当前账号或任务')
        need(proof.data['body_hash'] == expected_body_hash
             and proof.data['body_hash'] == digest(proof.data['body'])
             and proof.id == proof.data['audit_id']
             and proof.data['settlement_hash'] == digest({k: v for k, v in proof.data.items() if k != 'settlement_hash'}))
        result = settlement_result(proof)
    return {'schema': 'workflow-pixel-settlement-recovery-v250', 'found': result is not None,
        'state': 'found' if result is not None else 'unknown', 'request_id': request_id,
        'expected_body_hash': expected_body_hash, 'result': result}


def record_parent_failure(db, p, job, reason):
    """Only label unfinished records owned by this exact failed leased parent."""
    lease = ownership(db)
    need(lease.get('job_id') == job.id and lease.get('lease_token') == job.lease_token)
    rows = list(db.scalars(select(WorkflowPixelRun).where(WorkflowPixelRun.workspace_id == p.workspace_id,
        WorkflowPixelRun.job_id == job.id).with_for_update()))
    for run in rows:
        if run.status != 'running':
            continue  # A successful saved model result remains independent history.
        call = _row(db, p, Invocation, run.invocation_id)
        need(run.owner_id == p.user_id == job.data.get('actor_id') and run.task_id == job.data.get('task_id')
             and call.data.get('job_id') == job.id and run.data['result'] is None and run.data['result_audit_id'] is None)
        _proof(db, p, INPUT_ACTION, run.id, run.data['input_audit_id'], run.owner_id,
            _input_anchor(run, call.data.get('lease_token')))
        run.status = 'failed'; run.version += 1
        run.data = {**run.data, 'error': reason[:600]}
        _audit(db, p, FAILED_ACTION, run.id, {'task_id': run.task_id, 'job_id': job.id,
            'invocation_id': run.invocation_id, 'request_hash': run.data['request_hash'], 'status': 'failed'})
