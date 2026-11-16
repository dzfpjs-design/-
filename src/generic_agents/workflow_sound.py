"""Registered environmental classification of a Task's own immutable sound.

ASR records are never used as sound-classification results. Model completion,
parent completion, independent data reception and human review stay separate.
"""
import copy
from fastapi import HTTPException
from sqlalchemy import select, text, func
from .contracts import Manifest, digest
from .models import Audit, Capability, DomainInstance, Invocation, Job, Task, now
from .services import scoped, require, published, validate, register, validate_capability, publish
from .relations import lock_current_actor
from .leases import ownership
from .sound_models import EnvironmentSoundRun
from . import sound_inputs

ADAPTER = 'local_environment_sound'
INPUT_ACTION = 'sound.workflow_input_frozen_v1'
RESULT_ACTION = 'sound.workflow_result_frozen_v1'
FAILED_ACTION = 'sound.workflow_failed_v1'
REVIEW_ACTION = 'sound.review_saved_v1'


def need(value, message='原环境声音的任务、原音、模型或处理凭据无法核对', code=409):
    if not value:
        raise HTTPException(code, message)


def manifest(model_trace=None):
    from .local_sound import RECIPE, RECIPE_HASH, PACK
    suffix = int(digest({'recipe_hash': RECIPE_HASH, 'model_trace': model_trace})[:12], 16)
    execution = {'adapter': ADAPTER, 'pack_id': PACK, 'recipe_hash': RECIPE_HASH, 'backend': 'cpu',
        'side_effect': False, 'input_bindings': {'sound_ref': 'task_sound_ref', 'task_id': 'task_identifier'}}
    if model_trace is not None:
        execution['model_trace'] = copy.deepcopy(model_trace)
    return Manifest(kind='specialist_model', namespace='platform_local', name='yamnet_task_sound', version='1.0.' + str(suffix),
        owner='本地环境声音服务', description='完整原音的环境声音候选窗口与原分数；不依赖语音活动或转写，不确认现场异常、物种、独立声源数量或精确事件边界。',
        allowed_agent_roles=['perception', 'data'],
        input_schema={'type': 'object', 'additionalProperties': False, 'required': ['sound_ref', 'task_id'],
            'properties': {'task_id': {'type': 'string'}, 'sound_ref': {'type': 'object', 'additionalProperties': False,
                'required': ['sound_input_id', 'snapshot_hash'], 'properties': {'sound_input_id': {'type': 'string'},
                    'snapshot_hash': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}}}}},
        output_schema={'type': 'object', 'additionalProperties': False,
            'required': ['attributes', 'anomalies', 'sound_classification', 'sound_run_id'],
            'properties': {'attributes': {'type': 'object', 'maxProperties': 0},
                'anomalies': {'type': 'array', 'maxItems': 0}, 'sound_classification': {'type': 'object'}, 'sound_run_id': {'type': 'string'}}},
        execution=execution, data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False},
        specification={'domain_evaluation': 'pending', 'automatic_domain_use': False, 'candidate_only': True,
            'asr_used': False, 'vad_used': False, 'score_calibration': 'not_verified',
            'license': RECIPE['license'], 'license_evidence': RECIPE['license_evidence']})


def validate_manifest(value):
    from .local_sound import ready, RECIPE_HASH, PACK
    ready()
    trace = value.execution.get('model_trace')
    need(type(trace) is dict and set(trace) == {'installation_id', 'pack_id', 'generation', 'recipe_hash', 'backend', 'model_sha256', 'labels_sha256'}
        and trace['pack_id'] == PACK and trace['recipe_hash'] == RECIPE_HASH and trace['backend'] == 'cpu',
        '请先完成真实本地声音安装，再登记固定版本能力', 422)
    need(digest(value.model_dump(mode='json')) == digest(manifest(trace).model_dump(mode='json')),
        '声音能力必须固定原音、真实模型及配方，不得扩大外发或状态写入权限', 422)


def publish_sound_capability(db, p):
    from .local_sound import installed, trace, checked_trace
    require(p, 'admin'); lock_current_actor(db, p)
    model = installed(db, p); value = manifest(trace(model)); validate_manifest(value)
    checked_trace(db, p, trace(model), live=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(digest({'sound_cap': p.workspace_id, 'version': value.version})[:15], 16)})
    cap = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.namespace == value.namespace, Capability.name == value.name, Capability.release_version == value.version))
    if cap is not None:
        need(cap.status == 'published' and digest(cap.data) == digest(value.model_dump(mode='json')))
    else:
        cap = register(db, p, value); validate_capability(db, p, cap.id, cap.version)
        publish(db, p, cap.id, cap.version, '真实本地固定模型；声音仅为候选，不授异常或现场事实权限')
    return cap


def snapshots(db, p, instance, ids):
    need(type(ids) is list and len(ids) <= 1 and len(ids) == len(set(ids)), '声音节点只接受一项明确选择的原音', 422)
    values = []
    for identity in ids:
        row = sound_inputs.checked(db, p, identity)
        need(row.instance_id == instance, '原音属于其他业务实例')
        values.append(sound_inputs.view(row))
    return values


def verify(db, p, task):
    if 'sound_snapshots' not in task.data:
        row = db.scalar(select(EnvironmentSoundRun.id).where(EnvironmentSoundRun.workspace_id == p.workspace_id,
            EnvironmentSoundRun.task_id == task.id).limit(1))
        anchor = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == INPUT_ACTION, Audit.data['task_id'].as_string() == task.id).limit(1))
        need(row is None and anchor is None, '原声音输入标记缺失，不能退回无声音任务')
        return
    saved = task.data['sound_snapshots']
    need(digest(snapshots(db, p, task.data['instance_id'], [s['id'] for s in saved])) == digest(saved))


def ref(task):
    saved = task.data.get('sound_snapshots', [])
    need(len(saved) == 1, '声音能力需要本任务明确选择的一项原音')
    return {'sound_input_id': saved[0]['id'], 'snapshot_hash': saved[0]['snapshot_hash']}


def local_inputs(db, p, task, step, inputs):
    if step['execution'].get('adapter') != ADAPTER:
        return
    verify(db, p, task)
    need(set(inputs) == {'task_id', 'sound_ref'} and inputs['task_id'] == task.id and digest(inputs['sound_ref']) == digest(ref(task)))


def _proof(db, p, action, run, data, identity=None):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == action,
        Audit.object_id == run.id).limit(2)))
    need(len(rows) == 1 and rows[0].actor_id == run.owner_id and (identity is None or rows[0].id == identity)
        and digest(rows[0].data) == digest(data))
    return rows[0]


def input_anchor(run):
    return {'schema': 'sound-workflow-input-v1', 'run_id': run.id, 'task_id': run.task_id, 'job_id': run.job_id,
        'invocation_id': run.invocation_id, 'actor_id': run.owner_id, 'capability_id': run.capability_id,
        'request_hash': run.data['request_hash'], 'sound_snapshot_hash': digest(run.data['sound_snapshot']),
        'installation_proof_hash': digest(run.data['installation_proof'])}


def result_anchor(run):
    return {'schema': 'sound-workflow-result-v1', 'run_id': run.id, 'task_id': run.task_id,
        'job_id': run.job_id, 'invocation_id': run.invocation_id, 'actor_id': run.owner_id,
        'request_hash': run.data['request_hash'], 'input_audit_id': run.data['input_audit_id'],
        'result_hash': run.data['result_hash'], 'output_hash': digest(run.data['output'])}


def checked_saved_run(db, p, identity):
    run = scoped(db, EnvironmentSoundRun, identity, p)
    try:
        from .local_sound import validate as validate_sound, checked_trace
        from .workspace_api import saved_execution
        task, release, business = saved_execution(db, p, run.task_id)
        verify(db, p, task); d = run.data; request = d['request']
        source = sound_inputs.checked(db, p, run.sound_input_id); _, content = sound_inputs.input_bytes(db, p, source)
        cap = scoped(db, Capability, run.capability_id, p); job = scoped(db, Job, run.job_id, p)
        call = scoped(db, Invocation, run.invocation_id, p)
        installation_proof = checked_trace(db, p, d['installation'], live=False)['proof']
        need(digest(installation_proof) == digest(d['installation_proof']))
        need(run.status in {'running', 'succeeded', 'failed'}
            and d['schema'] == 'environment-sound-run-v1' and digest(request) == d['request_hash']
            and task.data['instance_id'] == d['instance_id'] == source.instance_id and release.id == d['release_id']
            and business['id'] == d['business_id'] and digest(d['sound_snapshot']) == digest(task.data['sound_snapshots'][0])
            and run.sound_input_id == task.data['sound_snapshots'][0]['id']
            and digest(cap.data) == cap.content_hash == d['capability_hash'] and digest(d['capability_snapshot']) == cap.content_hash
            and cap.data['execution']['adapter'] == ADAPTER and digest(cap.data['execution']['model_trace']) == digest(d['installation'])
            and request['task_id'] == task.id and request['job_id'] == job.id and request['invocation_id'] == call.id
            and request['capability_id'] == cap.id and request['capability_hash'] == cap.content_hash
            and digest(request['inputs']) == call.data['input_hash']
            and call.capability_id == cap.id and call.data['capability_hash'] == cap.content_hash
            and call.data['job_id'] == job.id and call.data['lease_token'] == request['lease_token']
            and job.data.get('actor_id') == run.owner_id and job.data.get('task_id') == task.id
            and job.data.get('invocation_key') == call.idempotency_key
            and job.kind in {'execute_workflow_step', 'execute_full_semantic_graph_node'})
        local_inputs(db, p, task, {'execution': cap.data['execution']}, request['inputs'])
        scope = d['execution_scope']
        if job.kind == 'execute_workflow_step':
            step = next(s for s in business['flow']['steps'] if s['id'] == d['step_id'])
            need(step['execution']['adapter'] == ADAPTER and step['execution']['capability_id'] == cap.id
                and step['execution']['capability_hash'] == cap.content_hash
                and digest(scope) == digest(job.data['scope'])
                and scope['step_id'] == step['id'] and digest(scope['inputs']) == digest(request['inputs']))
        else:
            from .workflow_graph_scope import record_node
            proxy = type('SoundGraphRecord', (), {'data': {'workflow_graph_scope': scope,
                'workflow_task_id': task.id, 'workflow_job_id': job.id, 'workflow_capability_id': cap.id,
                'workflow_capability_hash': cap.content_hash, 'workflow_step_id': d['step_id'],
                'workflow_capability_snapshot': d['capability_snapshot']}})()
            node = record_node(db, p, task, proxy)
            need(node['execution']['adapter'] == ADAPTER and digest(job.data['inputs']) == digest(request['inputs']))
        expected_request = {'schema': 'environment-sound-request-v1', 'task_id': task.id,
            'inputs': {'task_id': task.id, 'sound_ref': ref(task)}, 'job_id': job.id,
            'invocation_id': call.id, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
            'execution_scope': scope, 'lease_token': call.data['lease_token'], 'installation': d['installation']}
        if 'approval_scope' in request:
            from .sound_result_settlement import verify_saved_scope
            verify_saved_scope(db, p, task, request['approval_scope'])
            expected_request['approval_scope'] = copy.deepcopy(request['approval_scope'])
        need(digest(request) == digest(expected_request)
            and digest(d['capability_snapshot']) == digest(manifest(d['installation']).model_dump(mode='json')))
        _proof(db, p, INPUT_ACTION, run, input_anchor(run), d['input_audit_id'])
        result_audits = list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == RESULT_ACTION, Audit.object_id == run.id).limit(2)))
        failure_audits = list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == FAILED_ACTION, Audit.object_id == run.id).limit(2)))
        need((len(result_audits), len(failure_audits)) == {'running': (0, 0), 'succeeded': (1, 0), 'failed': (0, 1)}[run.status])
        if run.status == 'succeeded':
            need(digest(d['result']) == d['result_hash'])
            validate_sound(d['result'], content, d['sound_snapshot']['snapshot']['configuration'])
            expected = {'attributes': {}, 'anomalies': [], 'sound_classification': d['result'], 'sound_run_id': run.id}
            need(digest(d['output']) == digest(expected)); _proof(db, p, RESULT_ACTION, run, result_anchor(run), d['result_audit_id'])
        else:
            need(d['result'] is None and d['result_hash'] is None and d['output'] is None and d['result_audit_id'] is None)
            if run.status == 'failed':
                _proof(db, p, FAILED_ACTION, run, {'request_hash': d['request_hash'], 'job_id': job.id, 'invocation_id': call.id})
        _checked_reviews(db, p, run)
        need(run.version == (1 if run.status == 'running' else 2) + len(d['reviews']))
        return {'run': run, 'task': task, 'release': release, 'business': business, 'job': job,
            'invocation': call, 'capability': cap, 'input': source, 'content': content}
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(409, '声音成果引用的原件或任务凭据缺失') from None
        raise
    except (KeyError, ValueError, TypeError, AttributeError, StopIteration):
        raise HTTPException(409, '声音成果、原件或独立处理凭据不一致') from None


def checked_run(db, p, identity):
    context = checked_saved_run(db, p, identity)
    if context['run'].status == 'succeeded':
        need(context['invocation'].status == 'succeeded'
            and digest(context['invocation'].data.get('output')) == digest(context['run'].data['output']))
    return context


def _live(db, p, task_id, cap, inputs, key):
    from .local_sound import installed, trace
    from .workflow_graph_scope import owned_graph_job, graph_context
    from .workspace_api import execution, saved_execution
    lock_current_actor(db, p); lease = ownership(db)
    need(lease.get('job_id') and lease.get('lease_token'))
    located = scoped(db, Task, task_id, p)
    instance = db.scalar(select(DomainInstance).where(DomainInstance.workspace_id == p.workspace_id,
        DomainInstance.id == located.data['instance_id']).with_for_update(read=True).execution_options(populate_existing=True))
    need(instance is not None)
    job = scoped(db, Job, lease['job_id'], p)
    valid_lease = db.scalar(select(Job.id).where(Job.workspace_id == p.workspace_id, Job.id == job.id,
        Job.lease_token == lease['lease_token'], Job.lease_until > func.clock_timestamp()))
    call = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id, Invocation.idempotency_key == key))
    need(valid_lease is not None and job.status == 'running' and job.lease_token == lease['lease_token'] and job.data.get('actor_id') == p.user_id
        and job.data.get('invocation_key') == key and call is not None and call.status == 'running'
        and call.capability_id == cap.id and call.data.get('job_id') == job.id and call.data.get('lease_token') == lease['lease_token']
        and call.data.get('input_hash') == digest(inputs) and call.data.get('capability_hash') == cap.content_hash)
    if owned_graph_job(db, p):
        task, job, node, scope = graph_context(db, p, key, cap)
        _, release, business = saved_execution(db, p, task.id)
        step_id = node.get('source_step_id') or node['id']
    else:
        task, release, business = execution(db, p, task_id, True)
        auto = task.data['workflow_runtime'].get('automatic', {}); scope = copy.deepcopy(job.data['scope'])
        need(job.kind == 'execute_workflow_step' and job.data.get('task_id') == task.id and task.status == 'workflow_running'
            and auto.get('job_id') == job.id and auto.get('phase') == 'running'
            and digest(scope['inputs']) == digest(inputs) and len(task.data['workflow_runtime']['receipts']) == scope['visit'])
        step_id = scope['step_id']; step = next(s for s in business['flow']['steps'] if s['id'] == step_id)
        need(step['execution']['adapter'] == ADAPTER and step['execution']['capability_id'] == cap.id
            and step['execution']['capability_hash'] == cap.content_hash)
    need(task.id == task_id and instance.id == task.data['instance_id'])
    need(instance.status == 'partially_active' and business['id'] in instance.data.get('enabled_business_ids', []), '业务已暂停，原音保留，未接受新分析结果')
    current = published(db, p, cap.id); need(current.content_hash == cap.content_hash == digest(current.data))
    local_inputs(db, p, task, {'execution': cap.data['execution']}, inputs)
    installed_row = installed(db, p, cap.data['execution']['model_trace']['installation_id'])
    need(digest(trace(installed_row)) == digest(cap.data['execution']['model_trace']), '任务原模型安装代次已变化，不能替换后继续处理')
    return task, release, business, job, call, scope, step_id


def execute(db, p, cap, inputs, key):
    from .local_sound import call as classify, checked_trace
    require(p, 'admin', 'operator'); validate_manifest(Manifest.model_validate(cap.data))
    task, release, business, job, invocation, scope, step_id = _live(db, p, inputs['task_id'], cap, inputs, key)
    old = db.scalar(select(EnvironmentSoundRun).where(EnvironmentSoundRun.workspace_id == p.workspace_id,
        EnvironmentSoundRun.invocation_id == invocation.id))
    if old is not None:
        context = checked_saved_run(db, p, old.id)
        need(old.status == 'succeeded' and context['job'].id == job.id, '原调用没有完整成功成果，请从业务流程明确重试，不能重跑同一调用')
        return copy.deepcopy(old.data['output'])
    saved = copy.deepcopy(task.data['sound_snapshots'][0]); source = sound_inputs.checked(db, p, saved['id'])
    _, content = sound_inputs.input_bytes(db, p, source)
    request = {'schema': 'environment-sound-request-v1', 'task_id': task.id, 'inputs': copy.deepcopy(inputs),
        'job_id': job.id, 'invocation_id': invocation.id, 'capability_id': cap.id, 'capability_hash': cap.content_hash,
        'execution_scope': scope, 'lease_token': ownership(db)['lease_token'], 'installation': cap.data['execution']['model_trace']}
    from .sound_result_settlement import freeze_scope
    request['approval_scope'] = freeze_scope(db, p, task)
    run = EnvironmentSoundRun(workspace_id=p.workspace_id, task_id=task.id, job_id=job.id, invocation_id=invocation.id,
        capability_id=cap.id, owner_id=p.user_id, sound_input_id=source.id, status='running', data={
            'schema': 'environment-sound-run-v1', 'instance_id': release.instance_id, 'release_id': release.id,
            'business_id': business['id'], 'step_id': step_id, 'execution_scope': scope,
            'request': request, 'request_hash': digest(request), 'sound_snapshot': saved,
            'capability_snapshot': copy.deepcopy(cap.data), 'capability_hash': cap.content_hash,
            'installation': copy.deepcopy(cap.data['execution']['model_trace']), 'input_audit_id': None, 'result_audit_id': None,
            'installation_proof': checked_trace(db, p, cap.data['execution']['model_trace'], live=True)['proof'],
            'result': None, 'result_hash': None, 'output': None, 'error': None, 'reviews': [], 'review_commands': {}})
    db.add(run); db.flush(); identity = run.id
    proof = Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=INPUT_ACTION, object_id=run.id, data=input_anchor(run))
    db.add(proof); db.flush(); run.data = {**run.data, 'input_audit_id': proof.id}; db.commit()
    def guard():
        db.expire_all(); current = scoped(db, EnvironmentSoundRun, identity, p)
        need(current.status == 'running'); _live(db, p, task.id, cap, inputs, key)
        checked_saved_run(db, p, identity); db.commit()
    try:
        result = classify(content, saved['snapshot']['configuration'], guard)
        _live(db, p, task.id, cap, inputs, key); run = scoped(db, EnvironmentSoundRun, identity, p, True)
        need(run.status == 'running')
        output = {'attributes': {}, 'anomalies': [], 'sound_classification': result, 'sound_run_id': run.id}
        validate(cap.data['output_schema'], output)
        run.status = 'succeeded'; run.version += 1
        run.data = {**run.data, 'result': result, 'result_hash': digest(result), 'output': output}
        proof = Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=RESULT_ACTION, object_id=run.id, data=result_anchor(run))
        db.add(proof); db.flush(); run.data = {**run.data, 'result_audit_id': proof.id}
        return copy.deepcopy(output)  # Gateway commits its real Invocation, not a data ACK.
    except Exception as error:
        from .leases import LeaseLost
        if isinstance(error, LeaseLost):
            raise
        db.rollback(); run = scoped(db, EnvironmentSoundRun, identity, p)
        if run.status == 'running':
            run.status = 'failed'; run.version += 1
            run.data = {**run.data, 'error': '原音分析未完成；请从原业务明确重试，保留本次失败和原音'}
            db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=FAILED_ACTION, object_id=run.id,
                data={'request_hash': run.data['request_hash'], 'job_id': run.job_id, 'invocation_id': run.invocation_id}))
        raise


def local_result(db, p, task, step, inputs, output):
    if step['execution'].get('adapter') != ADAPTER:
        return
    context = checked_run(db, p, output.get('sound_run_id')); run = context['run']
    need(run.task_id == task.id and run.capability_id == step['execution']['capability_id']
        and digest(inputs) == digest(run.data['request']['inputs']) and digest(output) == digest(run.data['output']))


def runs_for_task(db, p, task):
    rows = list(db.scalars(select(EnvironmentSoundRun).where(EnvironmentSoundRun.workspace_id == p.workspace_id,
        EnvironmentSoundRun.task_id == task.id).order_by(EnvironmentSoundRun.created_at, EnvironmentSoundRun.id)))
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == INPUT_ACTION, Audit.data['task_id'].as_string() == task.id)))
    need(len(rows) == len(proofs) and {r.id for r in rows} == {a.object_id for a in proofs})
    return rows


def completed_context(db, p, run):
    context = checked_run(db, p, run.id); task = context['task']; call = context['invocation']; job = context['job']
    need(run.status == 'succeeded')
    if job.kind == 'execute_workflow_step':
        receipts = [r for r in task.data['workflow_runtime']['receipts'] if r.get('invocation_id') == call.id]
        proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'workflow.ability_completed', Audit.object_id == task.id,
            Audit.data['invocation'].as_string() == call.id).limit(2)))
        need(len(receipts) == len(proofs) == 1 and digest(receipts[0]['output']) == digest(run.data['output'])
            and receipts[0]['step_id'] == run.data['step_id'] and receipts[0]['actor'] == run.owner_id
            and proofs[0].data.get('step') == run.data['step_id'])
    else:
        scope = run.data['execution_scope']; activation_id = scope['activation_id']
        activation = task.data['graph_runtime']['activations'][activation_id]
        transport = task.data['graph_jobs'][activation_id]
        proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == 'workflow.graph.node_completed', Audit.object_id == task.id,
            Audit.data['activation_id'].as_string() == activation_id,
            Audit.data['invocation_id'].as_string() == call.id).limit(2)))
        need(activation['status'] == 'completed' and digest(activation['output']) == digest(run.data['output'])
            and transport.get('phase') == 'succeeded' and transport.get('invocation_id') == call.id
            and transport.get('output_hash') == digest(run.data['output']) and len(proofs) == 1
            and digest(proofs[0].data) == digest({'activation_id': activation_id, 'invocation_id': call.id}))
    need(proofs[0].actor_id == run.owner_id)
    return {**context, 'completion_audit': proofs[0]}


def consumed_runs(db, p, task):
    rows = runs_for_task(db, p, task)
    identities = {r.get('output', {}).get('sound_run_id') for r in task.data.get('workflow_runtime', {}).get('receipts', [])}
    for item in task.data.get('graph_jobs', {}).values():
        if item.get('phase') == 'succeeded' and item.get('invocation_id'):
            call = scoped(db, Invocation, item['invocation_id'], p)
            identities.add(call.data.get('output', {}).get('sound_run_id'))
    identities.discard(None); need(identities <= {r.id for r in rows})
    for run in rows:
        job = scoped(db, Job, run.job_id, p)
        action, field = (('workflow.ability_completed', 'invocation') if job.kind == 'execute_workflow_step'
            else ('workflow.graph.node_completed', 'invocation_id'))
        completed = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == action, Audit.object_id == task.id, Audit.data[field].as_string() == run.invocation_id).limit(1))
        if completed is not None:
            need(run.id in identities, '原声音成果的步骤引用缺失，不能跳过独立数据收件')
        if run.id in identities:
            completed_context(db, p, run)
    return [r for r in rows if r.id in identities]


def verify_receipts(db, p, task):
    verify(db, p, task)
    for run in consumed_runs(db, p, task):
        matches = [r for r in task.data['workflow_runtime']['receipts'] if r.get('output', {}).get('sound_run_id') == run.id]
        need(matches and all(digest(r['output']) == digest(run.data['output']) for r in matches))


def _reception(db, p, run):
    from .native_sound_receipts import receipt_projection
    return receipt_projection(db, p, run)


def require_task_received(db, p, task):
    from .native_sound_receipts import require_task_received as require_received
    return require_received(db, p, task)


def _checked_reviews(db, p, run):
    commands = run.data.get('review_commands', {}); records = run.data.get('reviews', [])
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == REVIEW_ACTION, Audit.object_id == run.id)))
    need(len(records) == len(commands) == len(proofs))
    for receipt in records:
        command = commands[receipt['request_id']]; expected = {k: v for k, v in receipt.items() if k != 'receipt_hash'}
        original_body = {'schema': 'sound-review-v1', 'request_id': receipt['request_id'],
            'version': receipt['expected_version'], 'verdict': receipt['verdict'], 'note': receipt['note']}
        need(receipt['receipt_hash'] == digest(expected) and receipt['run_id'] == run.id and receipt['task_id'] == run.task_id
            and receipt['result_hash'] == run.data['result_hash'] and receipt['input_snapshot_hash'] == run.data['sound_snapshot']['snapshot_hash']
            and command['actor_id'] == receipt['actor_id'] and command['receipt_hash'] == receipt['receipt_hash']
            and command['body_hash'] == digest(original_body))
        found = [a for a in proofs if a.id == command['audit_id']]
        need(len(found) == 1 and found[0].actor_id == receipt['actor_id'] and digest(found[0].data) == digest({
            'schema': 'sound-review-audit-v1', 'run_id': run.id, 'request_id': receipt['request_id'],
            'body_hash': command['body_hash'], 'receipt': receipt}))


def recover_review(db, p, identity, request_id, body_hash, expected_version):
    run = checked_run(db, p, identity)['run']
    command = run.data['review_commands'].get(request_id)
    value = {'schema': 'sound-review-recovery-v1', 'run_id': run.id, 'task_id': run.task_id,
        'request_id': request_id, 'body_hash': body_hash, 'expected_version': expected_version,
        'current_version': run.version, 'found': False, 'receipt': None, 'superseded': False, 'reason': None}
    if command is None:
        if run.version > expected_version:
            return {**value, 'superseded': True,
                'reason': '原核查未保存且成果版本已前进；原版本申请不能再写入。请明确保留原稿，核对当前版本后另拟申请。'}
        return value
    need(command['actor_id'] == p.user_id, '无法核对此账号的原核查申请', 404)
    need(command['body_hash'] == body_hash, '核查申请原文摘要不一致，未换号或重新保存')
    receipt = next(r for r in run.data['reviews'] if r['request_id'] == request_id)
    need(receipt['expected_version'] == expected_version, '核查申请的原版本不一致')
    return {**value, 'found': True, 'receipt': copy.deepcopy(receipt)}


def run_view(run):
    return {'schema': 'environment-sound-run-view-v1', 'id': run.id, 'version': run.version, 'status': run.status,
        'task_id': run.task_id, 'job_id': run.job_id, 'invocation_id': run.invocation_id, 'owner_id': run.owner_id,
        'sound_input_id': run.sound_input_id, 'sound_snapshot': copy.deepcopy(run.data['sound_snapshot']),
        'installation': copy.deepcopy(run.data['installation']), 'result': copy.deepcopy(run.data['result']),
        'result_hash': run.data['result_hash'], 'error': run.data['error'], 'reviews': copy.deepcopy(run.data['reviews']),
        'content_path': '/api/sounds/runs/' + run.id + '/content', 'candidate_only': True, 'physical_state_verified': False}


def task_view(db, p, identity):
    from .workspace_api import saved_execution
    task, release, business = saved_execution(db, p, identity); verify(db, p, task)
    rows = runs_for_task(db, p, task)
    values = []
    for row in rows:
        context = checked_run(db, p, row.id)
        values.append({'run': run_view(context['run']), 'reception': _reception(db, p, row)})
    return {'schema': 'task-sound-results-v1', 'task_id': task.id, 'instance_id': release.instance_id,
        'release_id': release.id, 'business_id': business['id'], 'sound_snapshots': copy.deepcopy(task.data.get('sound_snapshots', [])),
        'review_access': review_access(db, p, task, release, business), 'records': values}


def _review_authority(db, p, task, release, business, *, record_actor=False):
    require(p, 'admin', 'operator')
    from . import layered_approval_store_v175 as approval
    approval.guard(db, p, task, record_actor=record_actor, lock_sources=record_actor)
    if not record_actor:
        managed = approval.managed(db, p, task)
        if managed is not None:
            _, _, spec = approval.current_submission(db, p, managed)
            approval.role_allowed(db, p, spec['executor'], lock_sources=False)
    if task.data.get('execution_mode') == 'confirmed_graph_workflow':
        from .workflow_graph import graph_for, node_for
        state = task.data['graph_runtime']; graph = graph_for(release, business)
        need(state['status'] not in {'succeeded', 'cancelled', 'stopped', 'failed'})
        allowed = [node_for(graph, a) for a in state['activations'].values()
            if a['status'] == 'ready' and node_for(graph, a)['kind'] == 'human' and node_for(graph, a)['actor_id'] == p.user_id]
        need(bool(allowed), '请由本任务当前指定的人工核查人员填写声音反馈', 403)
        source = next((s for s in business['flow']['steps'] if s['id'] == allowed[0]['source_step_id']), None)
        return source['name'] if source else allowed[0]['role']
    from .workspace_api import current_step, authorize_step
    step = current_step(task, business); authorize_step(p, release, step, '')
    return step['name']


def review_access(db, p, task, release, business):
    try:
        name = _review_authority(db, p, task, release, business)
        return {'allowed': True, 'reason': None, 'current_step_name': name}
    except HTTPException as error:
        return {'allowed': False, 'reason': str(error.detail), 'current_step_name': None}


def review_run(db, p, identity, body):
    run = scoped(db, EnvironmentSoundRun, identity, p)
    context = checked_run(db, p, identity); old = run.data['review_commands'].get(body['request_id']); body_hash = digest(body)
    if old:
        need(old['actor_id'] == p.user_id and old['body_hash'] == body_hash, '原核查请求不能更换账号或原文')
        return next(r for r in run.data['reviews'] if r['request_id'] == body['request_id']), False
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    from .workspace_api import saved_execution
    task, release, business = saved_execution(db, p, run.task_id, True)
    _review_authority(db, p, task, release, business, record_actor=True)
    run = scoped(db, EnvironmentSoundRun, identity, p, True); checked_run(db, p, identity)
    old = run.data['review_commands'].get(body['request_id'])
    if old:
        need(old['actor_id'] == p.user_id and old['body_hash'] == body_hash)
        return next(r for r in run.data['reviews'] if r['request_id'] == body['request_id']), False
    need(run.status == 'succeeded' and run.version == body['version'], '声音核查版本已变化，原说明保留，请重新核对')
    value = {'schema': 'sound-review-receipt-v1', 'run_id': run.id, 'task_id': run.task_id, 'request_id': body['request_id'],
        'actor_id': p.user_id, 'expected_version': body['version'], 'version': run.version + 1,
        'verdict': body['verdict'], 'note': body['note'], 'result_hash': run.data['result_hash'],
        'input_snapshot_hash': run.data['sound_snapshot']['snapshot_hash'], 'saved_at': now().isoformat()}
    receipt = {**value, 'receipt_hash': digest(value)}
    proof = Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=REVIEW_ACTION, object_id=run.id,
        data={'schema': 'sound-review-audit-v1', 'run_id': run.id, 'request_id': body['request_id'], 'body_hash': body_hash, 'receipt': receipt})
    db.add(proof); db.flush(); run.version += 1
    run.data = {**run.data, 'reviews': [*run.data['reviews'], receipt], 'review_commands': {**run.data['review_commands'],
        body['request_id']: {'actor_id': p.user_id, 'body_hash': body_hash, 'receipt_hash': receipt['receipt_hash'], 'audit_id': proof.id}}}
    _checked_reviews(db, p, run)
    from .sound_daily import refresh_review
    refresh_review(db,p,run)
    return receipt, True
