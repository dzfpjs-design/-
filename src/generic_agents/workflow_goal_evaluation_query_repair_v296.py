"""Recover a lost local result only against the original independent SAVED hash.

This is not a new evaluation or a new query cut. The original invocation stays
unknown until the ordinary fenced graph reconciler completes its original work.
"""
import copy
from types import SimpleNamespace

from sqlalchemy import func, select

from . import goal_evaluation_query_v288 as q
from . import workflow_goal_evaluation_contracts_v288 as contract
from .contracts import DataRequest, digest
from .models import Audit, DataRun, Job
from .services import audit
from .temporal import instant
from .typed_payload_codec_v131 import canonical_json

REPAIRED = 'workflow.goal_evaluation.query_result_restored_v296'
SCHEMA = 'goal-evaluation-query-result-restoration-v296'
need = q.need


def _request_shape(request, marker):
    derived = {'resolved_time', 'clarifications', 'reference_at'}
    need(type(request) is dict and set(request) == set(DataRequest.model_fields) | derived)
    base = {k:v for k,v in request.items() if k not in derived}
    need(digest(DataRequest.model_validate(base).model_dump(mode='json')) == digest(base)
        and request['clarifications'] == [] and request['reference_at'] == marker['cut_at']
        and request['resolved_time'] == marker['derived_window'])


def _reference(row, state):
    data = {**row.data, 'result': copy.deepcopy(state)}
    candidate = SimpleNamespace(id=row.id, version=row.version, data=data)
    return q.query_ref(candidate), data


def _candidate(row, state, saved):
    """A saved digest, never current plausibility, decides if restoration fits."""
    marker = row.data[q.KEY]
    need(type(state) is dict and state['request'] == row.data['request']
        and state['status'] == state['result']['status']
        and state['result']['resolved_time'] == marker['derived_window']
        and state['result']['evaluated_at'] == marker['cut_at']
        and state['result']['evidence_requirement'] == marker['goal_snapshot']['spec']['evidence'])
    reference, data = _reference(row, state)
    need(reference == saved.data['query_ref'],
        '重建结果与原查询审计哈希不一致；原查询、原时点及失败记录保留，未采用新结果')
    return data


@q.checked
def inspect_retained(db, p, task, job, call, cap, inputs, approval, upstream):
    """Cold source authentication; this function grants no execution lease."""
    q._reader(db, p)
    from . import workflow_goal_evaluation_packet_v288 as packet
    from . import workflow_service_v195 as service
    packet.validate_manifest(cap.data)
    need(task.workspace_id == job.workspace_id == call.workspace_id == cap.workspace_id == p.workspace_id
        and call.status == 'unknown' and call.capability_id == cap.id
        and call.idempotency_key == job.idempotency_key == job.data['invocation_key']
        and call.data['job_id'] == job.id and call.data['role'] == 'data' and call.data['side_effect'] is True
        and call.data['capability_snapshot'] == cap.data
        and call.data['capability_hash'] == cap.content_hash == cap.validated_hash == digest(cap.data)
        and inputs == call.data['inputs'] == job.data['inputs']
        and call.data['input_hash'] == job.data['input_hash'] == digest(inputs)
        and approval == call.data['approval'])
    state = task.data['graph_runtime']; activation = state['activations'][job.data['activation_id']]
    binding = upstream['binding']; node = next(item for item in binding['graph']['nodes']
        if item['id'] == activation['node_id'])
    need(node['id'] == 'evaluation_packet' and node['actor_id'] == job.data['actor_id'] == p.user_id
        and approval == service.make_approval(task, job, node, inputs))
    transport = task.data['graph_jobs'][activation['id']]
    owner = transport['dispatch_ownership']; authorized = transport['dispatch_authorization']
    need(transport['invocation_id'] == call.id and transport['dispatch_committed'] is True
        and owner == {'job_id': job.id, 'lease_token': call.data['lease_token']}
        and bool(call.data['lease_token']) and authorized['lease_token'] == call.data['lease_token']
        and authorized['job_id'] == job.id and authorized['input_hash'] == digest(inputs)
        and authorized['capability_hash'] == cap.content_hash and authorized['actor_id'] == p.user_id
        and authorized['epoch'] == approval['cancel_epoch'])
    initial = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'capability.invoke', Audit.object_id == call.id).limit(2)))
    need(len(initial) == 1 and initial[0].actor_id == p.user_id and initial[0].data == {'status': 'unknown'})
    rows = list(db.scalars(select(DataRun).where(DataRun.workspace_id == p.workspace_id,
        DataRun.data[q.KEY]['evaluation_id'].as_string() == call.id).limit(2)))
    reverse = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == q.SAVED, Audit.data['evaluation_id'].as_string() == call.id).limit(2)))
    need(len(rows) == len(reverse) == 1)
    row = rows[0]; saved = reverse[0]; marker = row.data[q.KEY]
    frozen_goal = q.require_supported_goal(task)
    policy = contract.check_preparation_policy(task.data['goal_spec'], binding['preparation_policy'])
    scope = {'workspace_id': p.workspace_id, 'task_id': task.id,
        'instance_id': task.data['instance_id'], 'release_id': task.data['instance_release_id'],
        'package_id': task.package_id, 'business_id': task.data['business_id'], 'graph_hash': state['graph_hash'],
        'source_binding_hash': binding['binding_hash'], 'goal_snapshot_hash': q.hashed(frozen_goal),
        'native_goal_context_hash': task.data['native_goal_context_hash']}
    creator = {'workspace_id': p.workspace_id, 'task_id': task.id, 'invocation_id': call.id,
        'job_id': job.id, 'actor_id': p.user_id, 'lease_token': call.data['lease_token'],
        'activation_id': activation['id'], 'node_id': node['id'], 'cancel_epoch': activation['epoch'],
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'input_hash': digest(inputs),
        'request_key': call.idempotency_key, 'request_key_hash': q.hashed(call.idempotency_key),
        'scope_hash': q.hashed(scope)}
    need(marker['schema'] == q.SCHEMA and marker['evaluation_id'] == call.id
        and marker['request_id'] == job.id and marker['request_key'] == call.idempotency_key
        and marker['creator'] == creator and marker['scope'] == scope
        and marker['goal_snapshot'] == frozen_goal and marker['preparation_policy'] == policy
        and marker['controls'] == q.CONTROLS and row.owner_id == p.user_id and row.version == 1
        and marker['marker_hash'] == q.hashed({k:v for k,v in marker.items() if k != 'marker_hash'}))
    parent = q._fresh(db, p, DataRun, task.data['native_goal_query_id'])
    need(marker['original_query_ref'] == q.query_ref(parent)
        and marker['original_query_snapshot'] == parent.data)
    goal = frozen_goal['spec']; request = row.data['request']
    _request_shape(request, marker)
    need(request['mode'] == 'structured' and request['model_profile_id'] is None
        and request['entity_ids'] == goal['entity_ids'] and request['fields'] == goal['required_fields']
        and request['question'] == goal['purpose'] and request['evidence_requirement'] == goal['evidence']
        and request['tool_steps'] == [] and request['allow_external_tools'] is False
        and request.get('relation_query') is None and request['clarifications'] == []
        and request['resolved_time'] == marker['derived_window'])
    expected_window = contract.derive_evaluation_window(goal, policy, cut_at=marker['cut_at'],
        trusted_now=marker['cut_checked_at'],
        **({'initial_window': frozen_goal['time']} if policy['time_basis'] == 'fixed_window' else {}))
    need(marker['derived_window'] == expected_window
        and instant(frozen_goal['time']['reference_at']) <= instant(marker['cut_at'])
        and (not goal.get('deadline') or instant(marker['cut_at']) < instant(goal['deadline'])))
    reference = saved.data['query_ref']
    need(saved.object_id == row.id and saved.actor_id == p.user_id
        and saved.data == {'schema': q.SCHEMA, 'evaluation_id': call.id, 'job_id': job.id,
            'marker_hash': marker['marker_hash'], 'query_ref': reference}
        and set(reference) == {'query_id', 'version', 'request_hash', 'result_hash', 'data_hash'}
        and reference['query_id'] == row.id and reference['version'] == row.version
        and reference['request_hash'] == q.hashed(request)
        and all(type(reference[k]) is str and len(reference[k]) == 64
            and all(c in '0123456789abcdef' for c in reference[k])
            for k in ('request_hash', 'result_hash', 'data_hash')))
    repaired = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == REPAIRED, Audit.object_id == row.id).limit(2)))
    if 'result' in row.data:
        need(set(row.data) == {q.KEY, 'request', 'result'} and len(repaired) <= 1)
        frozen = q.checked_frozen(db, p, task, row.id, evaluation_id=call.id)
        if repaired:
            need(repaired[0].actor_id == p.user_id and repaired[0].data == {
                'schema': SCHEMA, 'evaluation_id': call.id, 'job_id': job.id,
                'saved_audit_id': saved.id, 'query_ref': reference, 'cut_at': marker['cut_at'],
                'input_hash': digest(inputs), 'new_query_created': False,
                'cloud_called': False, 'device_called': False})
    else:
        need(set(row.data) == {q.KEY, 'request'} and row.status == 'queued' and not repaired
            and row.created_at <= saved.created_at and call.created_at <= saved.created_at
            and saved.created_at <= initial[0].created_at)
        frozen = None
    return {'row': row, 'frozen': frozen, 'audit': saved, 'marker': copy.deepcopy(marker),
        'needs_result_repair': frozen is None}


@q.checked
def restore_result(db, p, task, job, call, cap, inputs, approval, upstream):
    """A real original-Job lease permits hash-checked local reconstruction only."""
    from .leases import fenced, ownership
    lease = ownership(db)
    need(fenced(db) and lease.get('job_id') == job.id and lease.get('lease_token') == job.lease_token
        and bool(job.lease_token) and job.status == 'running'
        and job.lease_until > db.scalar(select(func.clock_timestamp())))
    retained = inspect_retained(db, p, task, job, call, cap, inputs, approval, upstream)
    if retained['frozen'] is not None:
        return retained['frozen']
    row, saved, marker = retained['row'], retained['audit'], retained['marker']
    request = copy.deepcopy(row.data['request'])
    clock = q._Clock(q._WITNESS, db, p.workspace_id, p.user_id, job.id, lease['lease_token'],
        row.id, task.id, q.hashed(request), tuple(request['entity_ids']), tuple(request['fields']),
        q.hashed(request['evidence_requirement']), canonical_json(marker['derived_window']), instant(marker['cut_at']))
    from .agents import data_agent
    state = data_agent(db, p, request, row.id, **q.CONTROLS, task=task, _goal_evaluation_cut=clock)
    data = _candidate(row, state, saved)
    row.data = data; row.status = state['status']
    audit(db, p, REPAIRED, row.id, {'schema': SCHEMA, 'evaluation_id': call.id, 'job_id': job.id,
        'saved_audit_id': saved.id, 'query_ref': copy.deepcopy(saved.data['query_ref']),
        'cut_at': marker['cut_at'], 'input_hash': digest(inputs), 'new_query_created': False,
        'cloud_called': False, 'device_called': False})
    db.flush()
    return q.checked_frozen(db, p, task, row.id, evaluation_id=call.id)
