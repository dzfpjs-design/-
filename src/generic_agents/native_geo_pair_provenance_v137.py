"""Frozen provenance for real task-bound RGB comparisons, never model truth.

The three independent Audit families are append-only observations of new work.
They do not live in GeoPair.data: changing that payload would change the old
native packet's machine hash. Historical checks grant no current execution.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import select

from . import models as m
from .contracts import digest
from .services import audit, validate

INPUT_ACTION = 'geo_pair.workflow_input_frozen_v137'
RESULT_ACTION = 'geo_pair.workflow_result_frozen_v137'
ISSUED = 'native_geo_pair_receipt_v137'
ISSUED_ACTION = 'native_geo_pair.receipt_issued_v137'
RECEPTION_SCHEMA = 'instance-native-geo-pair-data-reception-v1'


def need(value):
    if not value:
        raise HTTPException(409, '原双影像工程处理、A/B范围、成员对象或接收依据无法核对；原记录保留')


def same(left, right):
    return digest(left) == digest(right)


def row(db, cls, identity, p):
    value = db.scalar(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
        .execution_options(populate_existing=True))
    need(value is not None)
    return value


def _audits(db, p, action, identity):
    # There is no lifetime generation cap on the original public workflow.
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity)))


def input_audits(db, p, pair_id):
    return _audits(db, p, INPUT_ACTION, pair_id)


def issued_audits(db, p, observation_id):
    return _audits(db, p, ISSUED_ACTION, observation_id)


def input_anchor(pair, task, job, inputs, capability, call):
    return {'schema': 'native-geo-pair-workflow-input-v137', 'pair_id': pair.id,
        'generation': pair.data['generation'], 'request_id': pair.data['request_id'],
        'task_id': task.id, 'instance_id': task.data['instance_id'],
        'release_id': task.data['instance_release_id'], 'business_id': task.data['business_id'],
        'creation_hash': task.data['creation_hash'], 'job_id': job.id,
        'actor_id': job.data['actor_id'], 'invocation_key': job.data['invocation_key'],
        'invocation_id': call.id, 'lease_token': call.data['lease_token'],
        'capability_id': capability.id, 'capability_hash': capability.content_hash,
        'release_version': capability.release_version, 'inputs_hash': digest(inputs),
        'workflow_scope': pair.data['workflow_scope'], 'request_hash': pair.data['request_hash'],
        'request': copy.deepcopy(pair.data['request']), 'runtime_hash': pair.data['runtime_hash'],
        'before_scene_id': pair.before_scene_id, 'after_scene_id': pair.after_scene_id,
        'sources_hash': pair.data['sources_hash'],
        'after_allowed_window': copy.deepcopy(pair.data['after_allowed_window']),
        'entity_ids': copy.deepcopy(task.data['entity_ids']),
        'object_snapshots_hash': digest(task.data['object_snapshots']),
        'comparison_snapshots_hash': digest(task.data['comparison_scene_snapshots']),
        'graph_scope_hash': digest(pair.data['workflow_graph_scope']) if 'workflow_graph_scope' in pair.data else None}


_INPUT_KEYS = {'schema', 'pair_id', 'generation', 'request_id', 'task_id', 'instance_id',
    'release_id', 'business_id', 'creation_hash', 'job_id', 'actor_id', 'invocation_key',
    'invocation_id', 'lease_token',
    'capability_id', 'capability_hash', 'release_version', 'inputs_hash', 'workflow_scope',
    'request_hash', 'request', 'runtime_hash', 'before_scene_id', 'after_scene_id',
    'sources_hash', 'after_allowed_window', 'entity_ids', 'object_snapshots_hash',
    'comparison_snapshots_hash', 'graph_scope_hash'}


def _input_inventory(db, p, pair, *, before_new=False):
    values = input_audits(db, p, pair.id)
    generation = pair.data['generation']
    need(type(generation) is int and generation >= 1)
    for value in values:
        data = value.data
        need(type(data) is dict and set(data) == _INPUT_KEYS
            and data['schema'] == 'native-geo-pair-workflow-input-v137'
            and data['pair_id'] == pair.id and type(data['generation']) is int
            and data['generation'] >= 1 and value.actor_id == data['actor_id'])
    expected = generation - 1 if before_new else generation
    need(len(values) == expected and sorted(value.data['generation'] for value in values)
        == list(range(1, len(values) + 1)))
    return {value.data['generation']: value for value in values}


def record_input(db, p, pair, task, job, inputs, capability, *, created=False):
    """Only a genuinely new execution may append this generation's input."""
    values = input_audits(db, p, pair.id)
    if not created and not values:
        # Old workflow rows remain old. A retry must not retroactively sign a
        # successful/failed pre-V137 input or create an invented generation 1.
        need(not _audits(db, p, RESULT_ACTION, pair.id))
        return False
    need(pair.data.get('request_kind') is None and pair.status == 'queued'
        and capability.data['execution']['adapter'] == 'local_geo_pair'
        and pair.data['workflow_task_id'] == task.id
        and pair.data['workflow_job_id'] == pair.data['job_id'] == job.id
        and p.user_id == job.data['actor_id'])
    if created:
        need(pair.data['generation'] == 1 and not values
            and not _audits(db, p, RESULT_ACTION, pair.id))
    _input_inventory(db, p, pair, before_new=True)
    # This is the actual adapter entry, after the gateway persisted the running
    # call. Freeze its original lease now; a later Job takeover is not that
    # lease and must never be used to reconstruct a historical dispatch pin.
    from .leases import ownership
    lease = ownership(db)
    call = db.scalar(select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id,
        m.Invocation.idempotency_key == job.data['invocation_key'])
        .execution_options(populate_existing=True))
    need(call is not None and job.status == call.status == 'running'
        and lease.get('job_id') == job.id and type(lease.get('lease_token')) is str
        and bool(lease['lease_token']) and job.lease_token == lease['lease_token']
        and call.capability_id == capability.id and call.data['job_id'] == job.id
        and call.data['lease_token'] == lease['lease_token']
        and call.data['capability_hash'] == capability.content_hash
        and call.data['input_hash'] == digest(inputs))
    audit(db, p, INPUT_ACTION, pair.id, input_anchor(pair, task, job, inputs, capability, call))
    return True


def result_anchor(pair, task, job, input_audit, completion_audit):
    return {'schema': 'native-geo-pair-workflow-result-v137', 'pair_id': pair.id,
        'task_id': task.id, 'job_id': job.id, 'generation': pair.data['generation'],
        'request_hash': pair.data['request_hash'], 'result_hash': pair.data['result_hash'],
        'runtime_hash': pair.data['runtime_hash'], 'sources_hash': pair.data['sources_hash'],
        'artifacts_hash': digest(pair.data['result']['artifacts']),
        'input_audit_id': input_audit.id, 'input_anchor_hash': digest(input_audit.data),
        'completion_audit_id': completion_audit.id}


def record_result(db, p, pair, completion_audit):
    """Called in the actual result/artifact transaction, never during sync."""
    values = input_audits(db, p, pair.id)
    if not values:
        need(not _audits(db, p, RESULT_ACTION, pair.id))
        return False
    need(pair.data.get('request_kind') is None and pair.status == 'succeeded')
    inputs = _input_inventory(db, p, pair)[pair.data['generation']]
    task = row(db, m.Task, pair.data['workflow_task_id'], p)
    job = row(db, m.Job, pair.data['workflow_job_id'], p)
    capability = row(db, m.Capability, inputs.data['capability_id'], p)
    call = row(db, m.Invocation, inputs.data['invocation_id'], p)
    arguments = job.data['inputs'] if job.kind == 'execute_full_semantic_graph_node' else job.data['scope']['inputs']
    need(p.user_id == job.data['actor_id'] == inputs.actor_id
        and same(inputs.data, input_anchor(pair, task, job, arguments, capability, call)))
    existing = _audits(db, p, RESULT_ACTION, pair.id)
    need(all(type(item.data) is dict and type(item.data.get('generation')) is int
        for item in existing))
    need(not any(item.data['generation'] == pair.data['generation'] for item in existing))
    audit(db, p, RESULT_ACTION, pair.id, result_anchor(pair, task, job, inputs, completion_audit))
    return True


def has_issued(db, p, observation):
    if ISSUED in observation.data or issued_audits(db, p, observation.id):
        return True
    if type(observation.data.get('data_reception')) is dict and observation.data['data_reception'].get('schema') == RECEPTION_SCHEMA:
        return True
    packet = observation.data.get('native_packet')
    keys = []
    if type(packet) is dict:
        keys.append(packet.get('source_key'))
    # Independent original handoff also survives stripping the packet/marker.
    originals = _audits(db, p, 'observation.native_receipt_saved', observation.id)
    keys += [item.data.get('source_key') for item in originals if type(item.data) is dict]
    for key in keys:
        if type(key) is str and key.startswith('GeoPair:'):
            identity = key.split(':')[1]
            if input_audits(db, p, identity) or _audits(db, p, RESULT_ACTION, identity):
                return True
    return False


def _call(db, p, task, release, business, pair):
    receipts = task.data['workflow_runtime']['receipts']
    matches = [(index, receipt) for index, receipt in enumerate(receipts)
        if type(receipt) is dict and type(receipt.get('output')) is dict
        and receipt['output'].get('geo_pair_id') == pair.id]
    need(len(matches) == 1)
    index, receipt = matches[0]
    job = row(db, m.Job, pair.data['workflow_job_id'], p)
    call = row(db, m.Invocation, receipt.get('invocation_id'), p)
    need(receipt['request_id'] == job.id == pair.data['job_id']
        and receipt.get('candidate_only') is True and receipt.get('entity_state_updated') is False
        and job.status == call.status == 'succeeded'
        and type(call.data.get('lease_token')) is str and bool(call.data['lease_token'])
        and receipt['actor'] == job.data['actor_id'])
    if 'graph_runtime' in task.data or 'workflow_graph_scope' in pair.data or job.kind == 'execute_full_semantic_graph_node':
        from . import full_semantic_graph_v1 as pure
        from . import workflow_graph as runtime
        from . import workflow_graph_scope as scope
        graph = runtime.graph_for(release, business)
        state = pure.check_run(graph, task.data['graph_runtime'])
        activation = state['activations'][receipt['activation_id']]
        node = next(item for item in graph['nodes'] if item['id'] == activation['node_id'])
        need(receipt['execution_mode'] == 'registered_graph_ability'
            and node['kind'] == 'ability' and activation['status'] == 'completed'
            and receipt['actor'] == node['actor_id'])
        scope.record_node(db, p, task, pair)
        transport = scope._job_matches(task, job, state, activation, node)
        attempt = transport['attempt']
        need(type(attempt) is int and 1 <= attempt <= node['execution']['max_attempts'])
        key = 'graph:' + task.id + ':' + activation['id'] + ':' + str(attempt)
        owner, authorization = transport['dispatch_ownership'], transport['dispatch_authorization']
        need(set(owner) == {'job_id', 'lease_token'} and owner['job_id'] == job.id
            and transport['phase'] == 'succeeded' and transport['dispatch_committed'] is True
            and transport['invocation_id'] == call.id and transport['output_hash'] == digest(receipt['output'])
            and set(authorization) == {'epoch', 'job_id', 'lease_token', 'input_hash', 'capability_hash', 'actor_id', 'authorized_at'}
            and type(authorization['epoch']) is int and authorization['epoch'] == activation['epoch']
            and authorization['job_id'] == job.id and authorization['lease_token'] == owner['lease_token']
            and authorization['actor_id'] == node['actor_id']
            and authorization['input_hash'] == transport['dispatch_input_hash'] == receipt['input_hash']
            and authorization['capability_hash'] == node['execution']['capability_hash']
            and job.data['attempt'] == attempt
            and same(receipt['input_snapshot'], activation['inputs_snapshot']['arguments'])
            and same(receipt['output'], activation['output']))
        command = {'request_id': job.id, 'op': 'complete', 'actor_id': node['actor_id'],
            'body_hash': digest({'activation_id': activation['id'], 'output': receipt['output'],
                'actor_id': node['actor_id'], 'expected_version': activation['version'] - 1,
                'cancel_epoch': activation['epoch']})}
        need(sum(same(item, command) for item in state['commands']) == 1)
        spec = node['execution']
        capability = row(db, m.Capability, spec['capability_id'], p)
        runtime.check_invocation(call, p, capability, node, receipt['input_snapshot'], job, owner)
        logical_scope = pair.data['workflow_graph_scope']
    else:
        need('workflow_graph_scope' not in pair.data and receipt['execution_mode'] == 'registered_ability'
            and job.kind == 'execute_workflow_step' and job.data['task_id'] == task.id)
        step = next(item for item in business['flow']['steps'] if item['id'] == receipt['step_id'])
        spec = step['execution']; logical_scope = job.data['scope']
        attempt = logical_scope['attempt']
        need(type(attempt) is int and attempt >= 1
            and logical_scope['step_id'] == step['id'] and logical_scope['visit'] == index
            and same(logical_scope['inputs'], receipt['input_snapshot'])
            and logical_scope['input_hash'] == receipt['input_hash'])
        key = 'workflow:' + task.id + ':' + str(index) + ':' + str(attempt)
    capability = row(db, m.Capability, spec['capability_id'], p)
    need(capability.kind == 'tool' and spec['adapter'] == capability.data['execution']['adapter'] == 'local_geo_pair'
        and capability.content_hash == digest(capability.data) == capability.validated_hash == spec['capability_hash'] == receipt['capability_hash']
        and capability.release_version == spec['release_version'] == receipt['release_version']
        and receipt['capability_id'] == call.capability_id == capability.id
        and job.idempotency_key == job.data['invocation_key'] == call.idempotency_key == key
        and call.data['job_id'] == job.id and call.data['capability_hash'] == capability.content_hash
        and call.data['input_hash'] == receipt['input_hash'] == digest(receipt['input_snapshot'])
        and call.data['role'] == spec['agent_role'] and call.data.get('approval') is None
        and call.data.get('side_effect') is False and same(call.data['output'], receipt['output'])
        and ('inputs' not in call.data or same(call.data['inputs'], receipt['input_snapshot']))
        and same(receipt['output']['spatial_comparison'], pair.data['result'])
        and same(receipt['output']['anomalies'], []))
    if 'graph_runtime' not in task.data:
        need(attempt <= min(5, int(capability.data['budget'].get('max_attempts', 2))))
    need(pair.data['workflow_scope'] == digest({'scope': logical_scope,
        'capability_hash': capability.content_hash, 'inputs': receipt['input_snapshot']})
        and pair.data['request_hash'] == digest(receipt['input_snapshot']))
    validate(spec.get('input_schema', capability.data['input_schema']), receipt['input_snapshot'])
    validate(spec['output_schema'], receipt['output'])
    return job, call, receipt, capability


def _checked_pair(db, p, identity):
    from .workspace_api import saved_execution
    from .workflow_geo_pairs import verify_pair_result, normalize
    from .workflow_scenes import checked_input, view
    from .geo_pairs import artifact_bytes, KINDS
    from .geo_pair_codec import V2
    from .geo_daily import sources
    from .entity_release_compat import verify_task_binding_snapshot
    pair = row(db, m.GeoPair, identity, p)
    need(pair.status == 'succeeded' and type(pair.data) is dict and pair.data.get('request_kind') is None)
    task, release, business = saved_execution(db, p, pair.data['workflow_task_id'])
    verify_task_binding_snapshot(task, release)
    ids, objects = task.data['entity_ids'], task.data['object_snapshots']
    need(type(ids) is list and ids and len(ids) == len(set(ids)) and [item['id'] for item in objects] == ids)
    instance = row(db, m.DomainInstance, release.instance_id, p)
    for identity in ids:
        need(row(db, m.Entity, identity, p).namespace == instance.namespace)
    verify_pair_result(db, p, task, pair)
    job, call, receipt, capability = _call(db, p, task, release, business, pair)
    saved = task.data['comparison_scene_snapshots']
    need(len(saved) == 2 and task.data['comparison_confirmed'] is True)
    attached = [checked_input(db, p, item['id']) for item in saved]
    need(all(item.instance_id == release.instance_id and same(view(item), snapshot)
        and item.scene_id == snapshot['snapshot']['scene_id'] for item, snapshot in zip(attached, saved)))
    execution = capability.data['execution']
    need(execution.get('side_effect') is False and capability.data['data_policy']['cloud_allowed'] is False
        and capability.data['data_policy']['external_allowed'] is False
        and capability.data['specification']['learned_model'] is False
        and type(execution['threshold']) is int and 1 <= execution['threshold'] <= 255
        and execution['alignment'] in {'same_grid', 'reproject_after_to_before'}
        and pair.before_scene_id == saved[0]['snapshot']['scene_id']
        and pair.after_scene_id == saved[1]['snapshot']['scene_id']
        and pair.data['sources_hash'] == digest(pair.data['sources'])
        and same(pair.data['sources'], [normalize(item) for item in saved])
        and same(pair.data['request'], {'roi': saved[0]['snapshot']['roi'],
            'threshold': execution['threshold'], 'alignment': execution['alignment']})
        and same(pair.data['after_allowed_window'], saved[1]['snapshot']['roi'])
        and pair.data['runtime_hash'] == execution['runtime_hash'] == digest(V2)
        and pair.data['result']['schema'] == 'local-rgb-comparison-v1'
        and pair.data['result']['learned_model'] is False and pair.data['result']['cloud_calls'] == 0
        and set(pair.data['result']['artifacts']) == set(KINDS))
    artifacts = {kind: artifact_bytes(db, p, pair, kind)[0] for kind in KINDS}
    inputs = _input_inventory(db, p, pair)
    frozen_input = inputs[pair.data['generation']]
    need(frozen_input.actor_id == job.data['actor_id']
        and same(frozen_input.data, input_anchor(pair, task, job, receipt['input_snapshot'], capability, call))
        and inputs[1].data['invocation_key'] == pair.data['request_id'])
    frozen_results = _audits(db, p, RESULT_ACTION, pair.id)
    by_generation = {}
    for item in frozen_results:
        data = item.data
        need(type(data) is dict and type(data.get('generation')) is int
            and data['generation'] in inputs and data['generation'] not in by_generation)
        original_input = inputs[data['generation']]
        need(data.get('schema') == 'native-geo-pair-workflow-result-v137'
            and data.get('pair_id') == pair.id and data.get('task_id') == task.id
            and data.get('job_id') == original_input.data['job_id']
            and data.get('input_audit_id') == original_input.id
            and data.get('input_anchor_hash') == digest(original_input.data)
            and item.actor_id == original_input.actor_id)
        by_generation[data['generation']] = item
    need(pair.data['generation'] in by_generation)
    frozen_result = by_generation[pair.data['generation']]
    completed = row(db, m.Audit, frozen_result.data['completion_audit_id'], p)
    need(completed.action == 'geo.pair_saved' and completed.object_id == pair.id
        and completed.actor_id == job.data['actor_id']
        and same(completed.data, {'cloud_calls': 0, 'learned_model': False,
            'candidate_pixels': pair.data['result']['candidate_pixels'],
            'unknown_pixels': pair.data['result']['unknown_pixels']})
        and same(frozen_result.data, result_anchor(pair, task, job, frozen_input, completed)))
    return {'pair': pair, 'task': task, 'release': release, 'business': business,
        'job': job, 'call': call, 'receipt': receipt, 'capability': capability,
        'input_audit': frozen_input, 'result_audit': frozen_result, 'completion_audit': completed,
        'sources': sources(db, p, 'comparison', pair), 'artifacts': artifacts}


def record_for_pair(context):
    from .audio_daily import task_review
    pair, task, sources = context['pair'], context['task'], context['sources']
    return {'key': 'GeoPair:' + pair.id, 'integrity_verified': True, 'integrity_issue': None,
        'name': task.title, 'status': pair.status, 'task_title': task.title,
        'association_basis': 'task_context_not_verified_coverage', 'source_time_verified': False,
        'domain_quality_verified': False, 'review_state': task_review(task), 'quality_gaps': [],
        'entity_ids': list(task.data['entity_ids']), 'task_id': task.id, 'kind': 'comparison', 'modality': 'imagery',
        'metrics': {'candidate_masks': 0, 'candidate_pixels': pair.data['result']['candidate_pixels'],
            'unknown_pixels': pair.data['result']['unknown_pixels']},
        'source_times': [{'value': item['observed_at'], 'basis': item['time_basis']} for item in sources],
        'source_sha256': [item['sha256'] for item in sources]}


def packet_for_pair(db, p, context, entity_id, *, version='native-observation-v2'):
    from .native_receipts import packet
    need(entity_id in context['task'].data['entity_ids'])
    return packet(db, p, record_for_pair(context), entity_id, version=version)


def issued_anchor(context, observation, event):
    return {'schema': 'native-geo-pair-issued-v137', 'pair_id': context['pair'].id,
        'generation': context['pair'].data['generation'], 'task_id': context['task'].id,
        'job_id': context['job'].id,
        'activation_id': context['pair'].data.get('workflow_graph_scope', {}).get('activation_id'),
        'event_id': event.id, 'native_packet_hash': observation.data['native_packet_hash'],
        'source_id': observation.source_id, 'entity_id': observation.entity_id,
        'input_audit_id': context['input_audit'].id, 'input_anchor_hash': digest(context['input_audit'].data),
        'result_audit_id': context['result_audit'].id, 'result_anchor_hash': digest(context['result_audit'].data),
        'receipt_actor_id': observation.data['submitted_by']}


def _checked_observation(db, p, observation, event=None):
    observation = row(db, m.Observation, observation.id, p)
    packet = observation.data.get('native_packet')
    need(type(packet) is dict and packet.get('schema') in {'native-observation-v1', 'native-observation-v2'}
        and type(packet.get('source_key')) is str and packet['source_key'].startswith('GeoPair:'))
    context = checked_pair(db, p, packet['source_key'].split(':', 1)[1])
    source = row(db, m.Capability, observation.source_id, p)
    need(source.kind == 'source' and source.namespace == 'platform_native_receipts'
        and source.content_hash == digest(source.data) == source.validated_hash == observation.data['source_hash']
        and source.data['specification']['managed_instance'] == context['release'].instance_id
        and observation.data['native_instance_id'] == context['release'].instance_id
        and observation.status == 'processed' and observation.observed_at is None
        and observation.data['candidate_only'] is True
        and observation.data['native_packet_hash'] == digest(packet)
        and observation.source_record_id == packet['source_key'] + ':' + observation.entity_id
        and observation.processing_version in {'native-v1', digest(packet)[:40]}
        and observation.business_day == packet['business_day']
        and same(packet, packet_for_pair(db, p, context, observation.entity_id, version=packet['schema']))
        and same(observation.data['extracted'], {'attributes': packet['attributes']})
        and observation.data['evidence_ids'] == packet['evidence_ids']
        and 'extractor_id' not in observation.data and 'invocation_id' not in observation.data)
    events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.idempotency_key == 'data:' + observation.id).limit(2)))
    need(len(events) == 1)
    original = events[0]
    base = {'observation_id': observation.id, 'entity_id': observation.entity_id,
        'source_id': source.id, 'evidence_ids': packet['evidence_ids'], 'native_receipt': packet,
        'observed_at': None, 'candidate_only': True, 'extracted': observation.data['extracted'], 'invocation_id': None}
    need((event is None or event.id == original.id) and original.kind == 'observation.data'
        and type(original.data) is dict
        and all(key in original.data and same(original.data[key], value) for key, value in base.items()))
    receipts = _audits(db, p, 'observation.native_receipt_saved', observation.id)
    need(len(receipts) == 1 and receipts[0].actor_id == observation.data['submitted_by']
        and same(receipts[0].data, {'source_key': packet['source_key'], 'instance_id': context['release'].instance_id}))
    issued = issued_anchor(context, observation, original)
    inventory = issued_audits(db, p, observation.id)
    need(len(inventory) == 1 and inventory[0].actor_id == observation.data['submitted_by']
        and same(inventory[0].data, issued) and same(observation.data.get(ISSUED), issued))
    return {**context, 'observation': observation, 'event': original, 'source': source,
        'base': base, 'packet': copy.deepcopy(packet), 'reception_audit': receipts[0], 'issued_audit': inventory[0]}


def _checked(function, *args):
    try:
        return function(*args)
    except HTTPException as error:
        if error.status_code != 422:
            raise
        need(False)
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, RecursionError, OSError):
        need(False)


def checked_pair(db, p, identity):
    return _checked(_checked_pair, db, p, identity)


def checked_observation(db, p, observation, event=None):
    return _checked(_checked_observation, db, p, observation, event)
