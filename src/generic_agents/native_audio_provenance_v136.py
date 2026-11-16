"""Actual native audio provenance. Historical integrity never grants new IO."""
import copy
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from . import models as m
from .contracts import digest
from .services import scoped, validate

ISSUED = 'native_audio_receipt_v136'
ISSUED_ACTION = 'native_audio.receipt_issued_v136'
INPUT_ACTION = 'audio.workflow_input_frozen_v136'
RESULT_ACTION = 'audio.workflow_result_frozen_v136'


def input_audits(db, p, run_id):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == INPUT_ACTION, m.Audit.object_id == run_id).limit(2)))


def input_anchor(run, task, job, inputs, capability_hash):
    return {'schema': 'native-audio-workflow-input-v136', 'run_id': run.id,
        'run_request_id': run.request_id, 'task_id': task.id,
        'instance_id': task.data['instance_id'], 'release_id': task.data['instance_release_id'],
        'business_id': task.data['business_id'], 'creation_hash': task.data['creation_hash'],
        'job_id': job.id, 'actor_id': job.data['actor_id'], 'invocation_key': job.data['invocation_key'],
        'capability_hash': capability_hash, 'inputs_hash': digest(inputs),
        'entity_ids': copy.deepcopy(task.data['entity_ids']),
        'object_snapshots_hash': digest(task.data['object_snapshots']),
        'audio_snapshots_hash': digest(task.data['audio_snapshots']),
        'graph_scope_hash': digest(run.data['workflow_graph_scope']) if 'workflow_graph_scope' in run.data else None}


def result_anchor(run, task, job, input_audit):
    return {'schema': 'native-audio-workflow-result-v136', 'run_id': run.id,
        'task_id': task.id, 'job_id': job.id, 'generation': run.data['generation'],
        'request_hash': run.data['request_hash'], 'result_hash': run.data['result_hash'],
        'recipe_hash': run.data['recipe_hash'], 'input_audit_id': input_audit.id,
        'input_anchor_hash': digest(input_audit.data)}


def issued_audits(db, p, observation_id):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ISSUED_ACTION, m.Audit.object_id == observation_id).limit(2)))


def has_issued(db, p, observation):
    return ISSUED in observation.data or bool(issued_audits(db, p, observation.id))


def need(value):
    if not value:
        raise HTTPException(409, '原生语音的原处理、原件、成员对象或回执无法核对；原记录保留')


def same(left, right):
    return digest(left) == digest(right)


def row(db, cls, identity, p, *, lock=False):
    query = select(cls).where(cls.id == identity, cls.workspace_id == p.workspace_id)
    if lock:
        query = query.with_for_update(read=True)
    result = db.scalar(query.execution_options(populate_existing=True))
    need(result is not None)
    return result


def actor(db, p, identity, *, lock=False):
    query = select(m.User, m.Membership).join(m.Membership,
        m.Membership.user_id == m.User.id).where(m.User.id == identity,
        m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
        m.Membership.role.in_(['admin', 'operator']))
    if lock:
        query = query.with_for_update(read=True)
    need(db.execute(query.execution_options(populate_existing=True)).first() is not None)
    return identity


def installation_pin(db, p, identity, *, live=False, lock=False):
    from .local_audio import RECIPE_HASH
    from .local_audio_install import PACK
    installation = row(db, m.SpecialistInstallation, identity, p, lock=lock)
    data = installation.data
    need(installation.pack_id == PACK and type(data) is dict
        and type(data.get('generation')) is int and data['generation'] >= 1
        and data.get('recipe_hash') == RECIPE_HASH and data.get('backend') == 'cpu')
    pin = {'installation_id': installation.id, 'pack_id': PACK,
        'generation': data['generation'], 'recipe_hash': RECIPE_HASH, 'backend': 'cpu'}
    if live:
        from .local_audio_install import installed, trace
        need(same(trace(installed(db, p, identity)), pin))
    return pin


def _audit(db, p, action, identity):
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity).limit(2)))
    need(len(rows) == 1)
    return rows[0]


def historical_installation(db, p, pin):
    from .local_audio import RECIPE_HASH
    from .local_audio_install import PACK
    need(type(pin) is dict and set(pin) == {'installation_id', 'pack_id', 'generation', 'recipe_hash', 'backend'}
        and pin['pack_id'] == PACK and pin['recipe_hash'] == RECIPE_HASH and pin['backend'] == 'cpu'
        and type(pin['generation']) is int and pin['generation'] >= 1)
    actual = row(db, m.SpecialistInstallation, pin['installation_id'], p)
    need(actual.pack_id == PACK)
    rows = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.object_id == actual.id, m.Audit.action == 'audio.install.ready',
        m.Audit.data['generation'].as_integer() == pin['generation']).limit(2)))
    need(len(rows) == 1 and rows[0].data == {'recipe_hash': RECIPE_HASH,
        'generation': pin['generation'], 'engineering_probe_only': True})
    return copy.deepcopy(pin)


def _call(db, p, task, business, run):
    receipts = task.data['workflow_runtime']['receipts']
    matches = [(index, receipt) for index, receipt in enumerate(receipts)
        if type(receipt) is dict and type(receipt.get('output')) is dict
        and receipt['output'].get('audio_run_id') == run.id]
    need(len(matches) == 1)
    index, receipt = matches[0]
    job = row(db, m.Job, run.data['workflow_job_id'], p)
    call = row(db, m.Invocation, receipt.get('invocation_id'), p)
    need(receipt['request_id'] == job.id == run.data['job_id']
        and receipt.get('candidate_only') is True and receipt.get('entity_state_updated') is False
        and job.status == 'succeeded' and call.status == 'succeeded'
        and type(call.data.get('lease_token')) is str and bool(call.data['lease_token'])
        and receipt['actor'] == job.data.get('actor_id'))
    if 'graph_runtime' in task.data or 'workflow_graph_scope' in run.data:
        from . import full_semantic_graph_v1 as pure
        from . import workflow_graph as graph_runtime
        from . import workflow_graph_scope as scope
        graph = graph_runtime.graph_for(row(db, m.InstanceRelease,
            task.data['instance_release_id'], p), business)
        state = pure.check_run(graph, task.data['graph_runtime'])
        activation = state['activations'][receipt['activation_id']]
        node = next(item for item in graph['nodes'] if item['id'] == activation['node_id'])
        need(receipt['execution_mode'] == 'registered_graph_ability'
            and activation['status'] == 'completed' and node['kind'] == 'ability'
            and receipt['actor'] == node['actor_id'])
        scope.record_node(db, p, task, run)
        transport = scope._job_matches(task, job, state, activation, node)
        attempt = transport['attempt']
        need(type(attempt) is int and 1 <= attempt <= node['execution']['max_attempts'])
        key = 'graph:' + task.id + ':' + activation['id'] + ':' + str(attempt)
        owner, authorization = transport['dispatch_ownership'], transport['dispatch_authorization']
        need(set(owner) == {'job_id', 'lease_token'} and owner['job_id'] == job.id
            and transport['phase'] == 'succeeded' and transport['dispatch_committed'] is True
            and transport['invocation_id'] == call.id and transport['output_hash'] == digest(receipt['output'])
            and set(authorization) == {'epoch', 'job_id', 'lease_token', 'input_hash',
                'capability_hash', 'actor_id', 'authorized_at'}
            and type(authorization['epoch']) is int and authorization['epoch'] == activation['epoch']
            and authorization['job_id'] == job.id and authorization['lease_token'] == owner['lease_token']
            and authorization['actor_id'] == node['actor_id']
            and authorization['input_hash'] == receipt['input_hash']
            and authorization['capability_hash'] == node['execution']['capability_hash']
            and transport['dispatch_input_hash'] == receipt['input_hash']
            and type(job.data['attempt']) is int and job.data['attempt'] == attempt
            and same(receipt['input_snapshot'], activation['inputs_snapshot']['arguments'])
            and same(receipt['output'], activation['output']))
        command = {'request_id': job.id, 'op': 'complete', 'actor_id': node['actor_id'],
            'body_hash': digest({'activation_id': activation['id'], 'output': receipt['output'],
                'actor_id': node['actor_id'], 'expected_version': activation['version'] - 1,
                'cancel_epoch': activation['epoch']})}
        need(sum(same(item, command) for item in state['commands']) == 1)
        spec = node['execution']
        cap = row(db, m.Capability, spec['capability_id'], p)
        graph_runtime.check_invocation(call, p, cap, node, receipt['input_snapshot'], job, owner)
    else:
        need('workflow_graph_scope' not in run.data and receipt['execution_mode'] == 'registered_ability')
        step = next(item for item in business['flow']['steps'] if item['id'] == receipt['step_id'])
        spec = step['execution']; original = job.data['scope']; attempt = original['attempt']
        need(type(attempt) is int and attempt >= 1)
        key = 'workflow:' + task.id + ':' + str(index) + ':' + str(attempt)
        need(job.kind == 'execute_workflow_step' and job.data['task_id'] == task.id
            and original['step_id'] == step['id'] and original['visit'] == index
            and same(original['inputs'], receipt['input_snapshot'])
            and original['input_hash'] == receipt['input_hash'])
    cap = row(db, m.Capability, spec['capability_id'], p)
    need(spec['adapter'] == 'local_audio' and cap.data['execution']['adapter'] == 'local_audio'
        and cap.content_hash == digest(cap.data) == spec['capability_hash'] == receipt['capability_hash']
        and cap.validated_hash == cap.content_hash and cap.release_version == spec['release_version']
        and receipt['release_version'] == cap.release_version and receipt['capability_id'] == cap.id
        and job.idempotency_key == key == job.data['invocation_key'] == call.idempotency_key
        and call.capability_id == cap.id and call.data['job_id'] == job.id
        and call.data['input_hash'] == receipt['input_hash'] == digest(receipt['input_snapshot'])
        and call.data['capability_hash'] == cap.content_hash and call.data['role'] == spec['agent_role']
        and call.data.get('approval') is None and call.data.get('side_effect') is False
        and same(call.data['output'], receipt['output'])
        and ('inputs' not in call.data or same(call.data['inputs'], receipt['input_snapshot']))
        and same(receipt['output']['audio_transcription'], run.data['result']))
    logical_scope = run.data['workflow_graph_scope'] if 'graph_runtime' in task.data else job.data['scope']
    need(run.request_id == digest({'scope': logical_scope, 'capability_hash': cap.content_hash,
        'inputs': receipt['input_snapshot']}))
    validate(spec['input_schema'] if 'input_schema' in spec else cap.data['input_schema'], receipt['input_snapshot'])
    validate(spec['output_schema'], receipt['output'])
    return job, call, receipt, cap


def checked_run(db, p, identity, *, live=False, lock=False):
    """One actual business AudioRun, no scan and no renewal of execution rights."""
    from .local_audio import checked, RECIPE_HASH
    from .workflow_audio import verify_result, checked_input
    from .workspace_api import saved_execution
    run = row(db, m.AudioRun, identity, p, lock=lock)
    need(run.status == 'succeeded' and type(run.data) is dict
        and type(run.data.get('generation')) is int and run.data['generation'] >= 1
        and run.data.get('deployment') == 'local' and run.data.get('recipe_hash') == RECIPE_HASH)
    task, release, business = saved_execution(db, p, run.data['workflow_task_id'])
    from .entity_release_compat import verify_task_binding_snapshot
    verify_task_binding_snapshot(task, release)
    ids = task.data['entity_ids']; objects = task.data['object_snapshots']
    need(type(ids) is list and ids and len(ids) == len(set(ids))
        and [item['id'] for item in objects] == ids)
    for identity in ids:
        entity = row(db, m.Entity, identity, p)
        need(entity.namespace == scoped(db, m.DomainInstance, release.instance_id, p).namespace)
    verify_result(db, p, task, run)
    attached = checked_input(db, p, run.data['input_audio_id'])
    root = row(db, m.AudioRun, attached.source_run_id, p)
    need('workflow_task_id' not in root.data and attached.instance_id == release.instance_id
        and run.data['input_snapshot_hash'] == attached.data['snapshot_hash'])
    evidence, content = checked(db, p, run)
    root_evidence, _ = checked(db, p, root)
    need(evidence.id == root_evidence.id and run.evidence_id == attached.data['snapshot']['evidence_id']
        and run.data['input_sha256'] == evidence.sha256
        and root.data['request_hash'] == digest({'input_sha256': root.data['input_sha256'],
            'configuration': root.data['configuration']}))
    root_job = row(db, m.Job, root.data['job_id'], p)
    root_completed = _audit(db, p, 'audio.transcription.saved', root.id)
    need(root_job.kind == 'transcribe_audio' and root_job.status == 'succeeded'
        and root_job.data['run_id'] == root.id and root_job.data['generation'] == root.data['generation']
        and root_job.idempotency_key == 'audio:' + root.id + ':' + str(root.data['generation'])
        and root_completed.actor_id == root_job.data['actor_id']
        and root_completed.data == {'input_sha256': root.data['input_sha256'],
            'recipe_hash': RECIPE_HASH, 'cloud_calls': 0})
    job, call, receipt, cap = _call(db, p, task, business, run)
    inputs_frozen = input_audits(db, p, run.id)
    need(len(inputs_frozen) == 1 and inputs_frozen[0].actor_id == job.data['actor_id']
        and same(inputs_frozen[0].data, input_anchor(run, task, job, receipt['input_snapshot'], cap.content_hash)))
    result_frozen = _audit(db,p,RESULT_ACTION,run.id)
    need(result_frozen.actor_id == job.data['actor_id']
        and same(result_frozen.data,result_anchor(run,task,job,inputs_frozen[0])))
    need(run.data['request_hash'] == digest(receipt['input_snapshot']))
    saved_installation = run.data.get('installation')
    historical_installation(db, p, saved_installation)
    if live:
        need(same(saved_installation, installation_pin(db, p,
            saved_installation['installation_id'], live=True, lock=lock)))
    need(same(root.data.get('installation'), saved_installation))
    completed = _audit(db, p, 'audio.transcription.saved', run.id)
    need(completed.actor_id == job.data['actor_id']
        and completed.data == {'input_sha256': run.data['input_sha256'],
            'recipe_hash': RECIPE_HASH, 'cloud_calls': 0})
    if live:
        actor(db, p, job.data['actor_id'], lock=lock)
    return {'run': run, 'task': task, 'release': release, 'business': business,
        'job': job, 'call': call, 'receipt': receipt, 'capability': cap,
        'root': root, 'attached': attached, 'evidence': evidence,
        'installation': copy.deepcopy(saved_installation), 'completion_audit': completed,
        'input_audit': inputs_frozen[0], 'result_audit': result_frozen}


def record_for_run(context):
    run, task, root = context['run'], context['task'], context['root']
    from .audio_daily import task_review
    return {'key': 'AudioRun:' + run.id, 'integrity_verified': True, 'integrity_issue': None,
        'name': run.data['name'], 'status': run.status, 'task_title': task.title,
        'association_basis': 'task_context_not_verified_coverage', 'source_time_verified': False,
        'domain_quality_verified': False, 'review_state': task_review(task), 'quality_gaps': [],
        'entity_ids': list(task.data['entity_ids']), 'task_id': task.id,
        'kind': 'transcription', 'modality': 'audio',
        'metrics': {'saved_asr_segments': 0, 'vad_intervals': 0,
            'whole_asr_performed': bool(run.data['result']['transcription'].get('inference_performed'))},
        'source_times': [{'value': None, 'basis': 'unknown'}],
        'source_sha256': [root.data['input_sha256']]}


def packet_for_run(db, p, context, entity_id, *, version='native-observation-v2'):
    from .native_receipts import packet
    need(entity_id in context['task'].data['entity_ids'])
    return packet(db, p, record_for_run(context), entity_id, version=version)


def checked_observation(db, p, observation, event=None, *, live=False, lock=False):
    observation = row(db, m.Observation, observation.id, p, lock=lock)
    value = observation.data.get('native_packet')
    need(type(value) is dict and value.get('schema') in {'native-observation-v1', 'native-observation-v2'}
        and value.get('source_key', '').startswith('AudioRun:'))
    context = checked_run(db, p, value['source_key'].split(':', 1)[1], live=live, lock=lock)
    source = row(db, m.Capability, observation.source_id, p, lock=lock)
    need(source.kind == 'source' and source.namespace == 'platform_native_receipts'
        and source.content_hash == digest(source.data) == observation.data.get('source_hash')
        and source.validated_hash == source.content_hash
        and source.data['specification']['managed_instance'] == context['release'].instance_id
        and observation.data.get('native_instance_id') == context['release'].instance_id
        and observation.entity_id in context['task'].data['entity_ids']
        and observation.observed_at is None and observation.status == 'processed'
        and observation.data.get('candidate_only') is True
        and observation.data.get('native_packet_hash') == digest(value)
        and observation.source_record_id == value['source_key'] + ':' + observation.entity_id
        and observation.processing_version in {'native-v1', digest(value)[:40]}
        and observation.business_day == value['business_day']
        and same(value, packet_for_run(db, p, context, observation.entity_id, version=value['schema']))
        and same(observation.data['extracted'], {'attributes': value['attributes']})
        and observation.data['evidence_ids'] == value['evidence_ids'])
    events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.idempotency_key == 'data:' + observation.id).limit(2)))
    need(len(events) == 1)
    original = events[0]
    need(event is None or event.id == original.id)
    base = {'observation_id': observation.id, 'entity_id': observation.entity_id,
        'source_id': source.id, 'evidence_ids': value['evidence_ids'], 'native_receipt': value,
        'observed_at': None, 'candidate_only': True,
        'extracted': observation.data['extracted'], 'invocation_id': None}
    need(original.kind == 'observation.data' and type(original.data) is dict
        and all(key in original.data and same(original.data[key], item) for key, item in base.items()))
    received = _audit(db, p, 'observation.native_receipt_saved', observation.id)
    need(received.actor_id == observation.data['submitted_by']
        and received.data == {'source_key': value['source_key'], 'instance_id': context['release'].instance_id})
    issued = {'schema': 'native-audio-issued-v136', 'run_id': context['run'].id,
        'task_id': context['task'].id, 'job_id': context['job'].id,
        'activation_id': context['run'].data.get('workflow_graph_scope', {}).get('activation_id'),
        'event_id': original.id, 'native_packet_hash': digest(value), 'source_id': source.id,
        'entity_id': observation.entity_id, 'input_snapshot_hash': context['attached'].data['snapshot_hash'],
        'installation_hash': digest(context['installation']), 'receipt_actor_id': observation.data['submitted_by']}
    inventory = issued_audits(db, p, observation.id)
    need(len(inventory) == 1 and inventory[0].actor_id == observation.data['submitted_by']
        and same(inventory[0].data, issued) and same(observation.data.get(ISSUED), issued))
    if live:
        need(source.status == 'published')
        actor(db, p, observation.data['submitted_by'], lock=lock)
    return {**context, 'observation': observation, 'event': original, 'source': source,
        'base': base, 'reception_audit': received, 'packet': copy.deepcopy(value)}


# Bad saved JSON is an integrity failure, not a driver/DB failure or raw trace.
_run = checked_run
_observation = checked_observation


def checked_run(db, p, identity, *, live=False, lock=False):
    try:
        return _run(db, p, identity, live=live, lock=lock)
    except HTTPException as error:
        if error.status_code != 422:
            raise
        need(False)
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, RecursionError, OSError):
        need(False)


def checked_observation(db, p, observation, event=None, *, live=False, lock=False):
    try:
        return _observation(db, p, observation, event, live=live, lock=lock)
    except HTTPException as error:
        if error.status_code != 422:
            raise
        need(False)
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, RecursionError, OSError):
        need(False)
