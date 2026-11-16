"""Actual learned A/B provenance; historical checks never renew model authority.

Reuse small V137 row/anchor helpers and existing SELECT-only completed-call
checks. RGB validation stays closed to RGB. No weights are opened by this cold
reader, and no field is added to an existing Pair's machine-hashed payload.
"""
import copy

from sqlalchemy import select, or_, and_

from . import models as m
from .contracts import digest, Manifest
from .services import audit, validate
from . import native_geo_pair_provenance_v137 as base

need, same, row, _audits = base.need, base.same, base.row, base._audits
INPUT_ACTION = 'learned_geo_pair.workflow_input_frozen_v138'
RESULT_ACTION = 'learned_geo_pair.workflow_result_frozen_v138'
ISSUED = 'native_learned_geo_pair_receipt_v138'
ISSUED_ACTION = 'native_learned_geo_pair.receipt_issued_v138'
RECEPTION_SCHEMA = 'instance-native-learned-geo-pair-data-reception-v1'


def input_audits(db, p, identity):
    return _audits(db, p, INPUT_ACTION, identity)


def issued_audits(db, p, identity):
    return _audits(db, p, ISSUED_ACTION, identity)


def input_anchor(pair, task, job, arguments, capability, call):
    return {**base.input_anchor(pair, task, job, arguments, capability, call),
        'schema': 'native-learned-geo-pair-workflow-input-v138',
        'model_trace': copy.deepcopy(pair.data['model_trace']), 'model_trace_hash': pair.data['model_trace_hash']}


def _inputs(db, p, pair, *, before=False):
    values = input_audits(db, p, pair.id); generation = pair.data['generation']
    need(type(generation) is int and generation >= 1
        and len(values) == generation - (1 if before else 0))
    for value in values:
        data = value.data
        need(type(data) is dict and set(data) == base._INPUT_KEYS | {'model_trace', 'model_trace_hash'}
            and data['schema'] == 'native-learned-geo-pair-workflow-input-v138'
            and data['pair_id'] == pair.id and type(data['generation']) is int
            and data['generation'] >= 1 and value.actor_id == data['actor_id'])
    need(sorted(value.data['generation'] for value in values) == list(range(1, len(values) + 1)))
    return {value.data['generation']: value for value in values}


def record_input(db, p, pair, task, job, arguments, capability, *, created=False):
    values = input_audits(db, p, pair.id)
    if not created and not values:
        need(not _audits(db, p, RESULT_ACTION, pair.id))
        return False  # Never back-sign an old Run during retry or collection.
    from .leases import ownership
    lease = ownership(db)
    call = db.scalar(select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id,
        m.Invocation.idempotency_key == job.data['invocation_key']).execution_options(populate_existing=True))
    need(pair.data.get('request_kind') == 'learned_binary_change' and pair.status == 'queued'
        and pair.data['workflow_task_id'] == task.id and pair.data['workflow_job_id'] == pair.data['job_id'] == job.id
        and capability.data['execution']['adapter'] == 'local_learned_geo_pair'
        and p.user_id == job.data['actor_id'] and call is not None and call.status == job.status == 'running'
        and lease.get('job_id') == job.id and type(lease.get('lease_token')) is str and bool(lease['lease_token'])
        and job.lease_token == call.data['lease_token'] == lease['lease_token']
        and call.data['job_id'] == job.id and call.capability_id == capability.id
        and call.data['capability_hash'] == capability.content_hash and call.data['input_hash'] == digest(arguments))
    if created:
        need(pair.data['generation'] == 1 and not values and not _audits(db, p, RESULT_ACTION, pair.id))
    _inputs(db, p, pair, before=True)
    audit(db, p, INPUT_ACTION, pair.id, input_anchor(pair, task, job, arguments, capability, call))
    if created:
        db.flush()
        from .graph_event_native_learned_geo_eligibility_v141 import record_eligibility
        record_eligibility(db, p, pair, task, job, capability)
    return True


def result_anchor(pair, task, job, frozen, completed):
    return {**base.result_anchor(pair, task, job, frozen, completed),
        'schema': 'native-learned-geo-pair-workflow-result-v138', 'model_trace_hash': pair.data['model_trace_hash']}


def record_result(db, p, pair, completed):
    if not input_audits(db, p, pair.id):
        need(not _audits(db, p, RESULT_ACTION, pair.id))
        return False
    need(pair.data.get('request_kind') == 'learned_binary_change' and pair.status == 'succeeded')
    frozen = _inputs(db, p, pair)[pair.data['generation']]
    task, job = row(db, m.Task, pair.data['workflow_task_id'], p), row(db, m.Job, pair.data['workflow_job_id'], p)
    capability, call = row(db, m.Capability, frozen.data['capability_id'], p), row(db, m.Invocation, frozen.data['invocation_id'], p)
    arguments = job.data['inputs'] if job.kind == 'execute_full_semantic_graph_node' else job.data['scope']['inputs']
    need(p.user_id == job.data['actor_id'] == frozen.actor_id
        and same(frozen.data, input_anchor(pair, task, job, arguments, capability, call)))
    existing = _audits(db, p, RESULT_ACTION, pair.id)
    need(all(type(item.data) is dict and type(item.data.get('generation')) is int
        and item.data['generation'] != pair.data['generation'] for item in existing))
    audit(db, p, RESULT_ACTION, pair.id, result_anchor(pair, task, job, frozen, completed))
    return True


def has_issued(db, p, observation):
    if ISSUED in observation.data or issued_audits(db, p, observation.id):
        return True
    if type(observation.data.get('data_reception')) is dict and observation.data['data_reception'].get('schema') == RECEPTION_SCHEMA:
        return True
    packet = observation.data.get('native_packet')
    keys = [packet.get('source_key')] if type(packet) is dict else []
    keys += [item.data.get('source_key') for item in _audits(db, p, 'observation.native_receipt_saved', observation.id)
        if type(item.data) is dict]
    return any(type(key) is str and key.startswith('GeoPair:') and
        (input_audits(db, p, key.split(':')[1]) or _audits(db, p, RESULT_ACTION, key.split(':')[1])) for key in keys)


def has_pair_trace(db, p, identity):
    """Writer-side family selection: incomplete new proof cannot become old.

    An issued Observation/Audit remains an independent trace when both input
    and result Audits have vanished. A genuine old Pair with no V138 trace
    still selects its original protocol; discovery never creates a new anchor.
    """
    if input_audits(db, p, identity) or _audits(db, p, RESULT_ACTION, identity):
        return True
    issued = select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ISSUED_ACTION, m.Audit.data['pair_id'].as_string() == identity)
    if db.scalar(issued.limit(1)) is not None:
        return True
    key = 'GeoPair:' + identity
    original_handoffs = select(m.Audit.object_id).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == 'observation.native_receipt_saved', m.Audit.data['source_key'].as_string() == key)
    observations = db.scalars(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
        or_(m.Observation.data['native_packet']['source_key'].as_string() == key,
            m.Observation.data[ISSUED]['pair_id'].as_string() == identity,
            and_(m.Observation.data['data_reception']['schema'].as_string() == RECEPTION_SCHEMA,
                m.Observation.data['data_reception']['run_id'].as_string() == identity),
            and_(m.Observation.data['native_instance_id'].as_string().is_not(None),
                m.Observation.source_record_id.startswith(key + ':')),
            m.Observation.id.in_(original_handoffs))).execution_options(populate_existing=True))
    pair = None
    for observation in observations:
        if has_issued(db, p, observation):
            return True
        if 'data_reception' in observation.data:
            if pair is None:
                pair = row(db, m.GeoPair, identity, p)
            if pair.data.get('request_kind') == 'learned_binary_change':
                # An old learned native packet never had a modern receipt.
                # Corrupting that receipt's schema cannot erase its presence.
                return True
    return False


def historical_model(db, p, trace):
    from .local_changer_install import PACK, RECIPE_HASH, PIPELINE_HASH, RECIPE
    need(type(trace) is dict and set(trace) == {'installation_id', 'pack_id', 'generation', 'recipe_hash', 'pipeline_hash', 'weights_sha256', 'backend'}
        and trace['pack_id'] == PACK and type(trace['generation']) is int and trace['generation'] >= 1
        and trace['recipe_hash'] == RECIPE_HASH and trace['pipeline_hash'] == PIPELINE_HASH
        and trace['weights_sha256'] == RECIPE['files'][0]['sha256'] and trace['backend'] == 'cpu')
    installation = row(db, m.SpecialistInstallation, trace['installation_id'], p)
    need(installation.pack_id == PACK)
    ready = [item for item in _audits(db, p, 'changer.install.ready', installation.id)
        if type(item.data) is dict and type(item.data.get('generation')) is int and item.data['generation'] == trace['generation']]
    need(len(ready) == 1 and same(ready[0].data,
        {'recipe_hash': RECIPE_HASH, 'generation': trace['generation'], 'engineering_probe_only': True}))
    jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
        m.Job.idempotency_key == 'specialist-install:' + installation.id + ':' + str(trace['generation']))))
    need(len(jobs) == 1 and jobs[0].kind == 'install_specialist' and jobs[0].status == 'succeeded'
        and jobs[0].data['installation_id'] == installation.id and jobs[0].data['generation'] == trace['generation']
        and jobs[0].data['actor_id'] == ready[0].actor_id)
    return ready[0]


def _call(db, p, task, release, business, pair):
    candidates = [(index, item) for index, item in enumerate(task.data['workflow_runtime']['receipts'])
        if type(item) is dict and type(item.get('output')) is dict and item['output'].get('geo_pair_id') == pair.id]
    need(len(candidates) == 1)
    index, receipt = candidates[0]
    job, call = row(db, m.Job, pair.data['workflow_job_id'], p), row(db, m.Invocation, receipt['invocation_id'], p)
    capability = row(db, m.Capability, pair.data['workflow_capability_id'], p)
    need(job.status == call.status == 'succeeded' and job.id == receipt['request_id'] == pair.data['job_id']
        and receipt.get('candidate_only') is True and receipt.get('entity_state_updated') is False)
    if 'graph_runtime' in task.data or 'workflow_graph_scope' in pair.data or job.kind == 'execute_full_semantic_graph_node':
        from . import workflow_graph as runtime, full_semantic_graph_v1 as pure, workflow_graph_scope as scope
        from .workflow_graph_goal_tools_v126 import _actual_result
        graph = runtime.graph_for(release, business); state = pure.check_run(graph, task.data['graph_runtime'])
        nodes = {item['id']: item for item in graph['nodes']}
        spec = scope.record_node(db, p, task, pair)['execution']
        _, reaped = _actual_result(db, p, task, graph, state, nodes, receipt, {'capability': capability})
        need(reaped)
        logical = {'scope': pair.data['workflow_graph_scope']}
    else:
        from .accepted_tool_history_v129 import _legacy
        _legacy(db, p, task, business, {capability.id})
        spec = next(item['execution'] for item in business['flow']['steps'] if item['id'] == receipt['step_id'])
        need(pair.data['workflow_step_id'] == receipt['step_id'] and job.data['scope']['visit'] == index)
        logical = {'task': task.id, 'step': job.data['scope']['step_id'], 'visit': index}
    need(capability.kind == 'specialist_model' and spec['adapter'] == capability.data['execution']['adapter'] == 'local_learned_geo_pair'
        and capability.content_hash == digest(capability.data) == capability.validated_hash == pair.data['workflow_capability_hash']
        == spec['capability_hash'] == receipt['capability_hash']
        and capability.release_version == spec['release_version'] == receipt['release_version']
        and same(pair.data['workflow_capability_snapshot'], capability.data)
        and same(receipt['output']['spatial_comparison'], pair.data['result']) and same(receipt['output']['anomalies'], []))
    validate(capability.data['input_schema'], receipt['input_snapshot'])
    need(pair.data['workflow_scope'] == digest({**logical, 'capability_hash': capability.content_hash, 'inputs': receipt['input_snapshot']}))
    return job, call, receipt, capability


def _checked_pair(db, p, identity):
    from .workspace_api import saved_execution
    from .entity_release_compat import verify_task_binding_snapshot
    from .workflow_geo_pairs import normalize, verify_inputs
    from .workflow_scenes import checked_input, view
    from .workflow_learned_geo_pairs import request_value, validate_manifest
    from .geo_pairs import checked, artifact_bytes
    from .geo_daily import sources
    from .learned_geo_pairs import ARTIFACTS
    from .changer_pair_codec import RECIPE, RECIPE_HASH
    pair = row(db, m.GeoPair, identity, p)
    need(pair.status == 'succeeded' and pair.data.get('request_kind') == 'learned_binary_change')
    task, release, business = saved_execution(db, p, pair.data['workflow_task_id'])
    verify_task_binding_snapshot(task, release); checked(db, p, pair); verify_inputs(db, p, task)
    job, call, receipt, capability = _call(db, p, task, release, business, pair)
    validate_manifest(db, p, Manifest.model_validate(capability.data), live=False)
    ids, snapshots = task.data['entity_ids'], task.data['comparison_scene_snapshots']
    need(ids and len(ids) == len(set(ids)) and [item['id'] for item in task.data['object_snapshots']] == ids
        and len(snapshots) == 2 and task.data['comparison_confirmed'] is True)
    for identity in ids:
        need(row(db, m.Entity, identity, p).namespace == row(db, m.DomainInstance, release.instance_id, p).namespace)
    attached = [checked_input(db, p, item['id']) for item in snapshots]
    need(all(item.instance_id == release.instance_id and item.scene_id == snapshot['snapshot']['scene_id']
        and same(view(item), snapshot) for item, snapshot in zip(attached, snapshots)))
    execution, result = capability.data['execution'], pair.data['result']
    ready = historical_model(db, p, pair.data['model_trace'])
    need(pair.before_scene_id == snapshots[0]['snapshot']['scene_id'] and pair.after_scene_id == snapshots[1]['snapshot']['scene_id']
        and same(pair.data['sources'], [normalize(item) for item in snapshots])
        and pair.data['sources_hash'] == digest(pair.data['sources'])
        and same(pair.data['request'], request_value(task, execution, pair.data['request_id']))
        and pair.data['request_hash'] == digest(pair.data['request'])
        and same(pair.data['after_allowed_window'], snapshots[1]['snapshot']['roi'])
        and same(pair.data['model_trace'], execution['model_trace']) and digest(pair.data['model_trace']) == pair.data['model_trace_hash']
        and pair.data['runtime_hash'] == execution['runtime_hash'] == RECIPE_HASH
        and result['weights_sha256'] == pair.data['model_trace']['weights_sha256']
        and result['graph_sha256'] == RECIPE['files']['local_changer_model.py']
        and result['source_sha256'] == [item['sha256'] for item in pair.data['sources']]
        and result['candidate_only'] is True and result['backend'] == 'cpu'
        and result['semantics'] == 'learned_binary_change_candidates_only'
        and result['object_state_updated'] is False and result['domain_quality'] == 'not_verified'
        and result['probability_calibration'] == 'not_verified' and set(result['artifacts']) == set(ARTIFACTS))
    artifacts = {kind: artifact_bytes(db, p, pair, kind)[0] for kind in ARTIFACTS}
    inputs = _inputs(db, p, pair); frozen = inputs[pair.data['generation']]
    need(frozen.actor_id == job.data['actor_id']
        and same(frozen.data, input_anchor(pair, task, job, receipt['input_snapshot'], capability, call))
        and pair.data['request_id'] == digest({'scope': pair.data['workflow_scope'], 'first_invocation': inputs[1].data['invocation_key']}))
    results = _audits(db, p, RESULT_ACTION, pair.id); by_generation = {}
    for item in results:
        data = item.data
        need(type(data) is dict and type(data.get('generation')) is int
            and data['generation'] in inputs and data['generation'] not in by_generation)
        original = inputs[data['generation']]
        need(data['schema'] == 'native-learned-geo-pair-workflow-result-v138'
            and data['pair_id'] == pair.id and data['task_id'] == task.id and data['job_id'] == original.data['job_id']
            and data['input_audit_id'] == original.id and data['input_anchor_hash'] == digest(original.data) and item.actor_id == original.actor_id)
        by_generation[data['generation']] = item
    need(pair.data['generation'] in by_generation)
    frozen_result = by_generation[pair.data['generation']]
    completed = row(db, m.Audit, frozen_result.data['completion_audit_id'], p)
    need(completed.action == 'geo.pair_saved' and completed.object_id == pair.id and completed.actor_id == job.data['actor_id']
        and same(completed.data, {'learned_model': True, 'cloud_calls': 0, 'candidate_pixels': result['candidate_pixels']})
        and same(frozen_result.data, result_anchor(pair, task, job, frozen, completed)))
    return {'pair': pair, 'task': task, 'release': release, 'business': business, 'job': job, 'call': call,
        'receipt': receipt, 'capability': capability, 'input_audit': frozen, 'result_audit': frozen_result,
        'completion_audit': completed, 'model_ready_audit': ready, 'artifacts': artifacts, 'sources': sources(db, p, 'comparison', pair)}


record_for_pair = base.record_for_pair


def packet_for_pair(db, p, context, entity_id, *, version='native-observation-v2'):
    from .native_receipts import packet
    need(entity_id in context['task'].data['entity_ids'])
    return packet(db, p, record_for_pair(context), entity_id, version=version)


def issued_anchor(context, observation, event):
    return {**base.issued_anchor(context, observation, event), 'schema': 'native-learned-geo-pair-issued-v138',
        'model_trace_hash': context['pair'].data['model_trace_hash'], 'model_ready_audit_id': context['model_ready_audit'].id}


def _checked_observation(db, p, observation, event=None):
    observation = row(db, m.Observation, observation.id, p); packet = observation.data.get('native_packet')
    need(type(packet) is dict and packet.get('schema') in {'native-observation-v1', 'native-observation-v2'}
        and type(packet.get('source_key')) is str and packet['source_key'].startswith('GeoPair:'))
    context = checked_pair(db, p, packet['source_key'].split(':', 1)[1]); source = row(db, m.Capability, observation.source_id, p)
    need(source.kind == 'source' and source.namespace == 'platform_native_receipts'
        and source.content_hash == digest(source.data) == source.validated_hash == observation.data['source_hash']
        and source.data['specification']['managed_instance'] == context['release'].instance_id == observation.data['native_instance_id']
        and observation.status == 'processed' and observation.observed_at is None and observation.data['candidate_only'] is True
        and observation.data['native_packet_hash'] == digest(packet)
        and observation.source_record_id == packet['source_key'] + ':' + observation.entity_id
        and observation.processing_version in {'native-v1', digest(packet)[:40]}
        and observation.business_day == packet['business_day']
        and same(packet, packet_for_pair(db, p, context, observation.entity_id, version=packet['schema']))
        and same(observation.data['extracted'], {'attributes': packet['attributes']}) and observation.data['evidence_ids'] == packet['evidence_ids']
        and 'extractor_id' not in observation.data and 'invocation_id' not in observation.data)
    events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.idempotency_key == 'data:' + observation.id).limit(2)))
    need(len(events) == 1); original = events[0]
    value = {'observation_id': observation.id, 'entity_id': observation.entity_id, 'source_id': source.id,
        'evidence_ids': packet['evidence_ids'], 'native_receipt': packet, 'observed_at': None,
        'candidate_only': True, 'extracted': observation.data['extracted'], 'invocation_id': None}
    need((event is None or event.id == original.id) and original.kind == 'observation.data'
        and all(key in original.data and same(original.data[key], item) for key, item in value.items()))
    receipts, issued = _audits(db, p, 'observation.native_receipt_saved', observation.id), issued_audits(db, p, observation.id)
    need(len(receipts) == len(issued) == 1 and receipts[0].actor_id == issued[0].actor_id == observation.data['submitted_by']
        and same(receipts[0].data, {'source_key': packet['source_key'], 'instance_id': context['release'].instance_id})
        and same(issued[0].data, issued_anchor(context, observation, original)) and same(observation.data.get(ISSUED), issued[0].data))
    return {**context, 'observation': observation, 'event': original, 'source': source, 'packet': copy.deepcopy(packet),
        'base': value, 'reception_audit': receipts[0], 'issued_audit': issued[0]}


def checked_pair(db, p, identity):
    return base._checked(_checked_pair, db, p, identity)


def checked_observation(db, p, observation, event=None):
    return base._checked(_checked_observation, db, p, observation, event)
