"""An independent local DataRun with an authenticated, immutable query cut.

No Q0/Goal pointer, SDK result, reception, gap or producer completion is written.
The opaque clock exists only inside the current fenced evaluation invocation;
it is never a DataRequest field or an authority retained by a saved JSON marker.
"""
import copy
import hashlib
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select

from . import workflow_goal_evaluation_contracts_v288 as contract
from .contracts import DataRequest, EvidenceRequirement, Manifest, digest
from .goals import snapshot
from .models import Audit, Capability, DataRun, EntityEvent, Event, Invocation, Job, Membership, Memory, User
from .services import audit, published
from .temporal import instant, resolve
from .typed_payload_codec_v131 import canonical_json

KEY = 'goal_evaluation_v288'
SCHEMA = 'goal-evaluation-query-provenance-v288'
SAVED = 'workflow.goal_evaluation.query_saved_v288'
OP = 'create_goal_evaluation_packet_v288'
ERROR = '独立目标核对的原查询、固定时点或生产归属无法核实；未改原查询、补采或调用模型'
CONTROLS = {'cached_only': True, 'emit_gaps': False, 'learning': False, 'include_trusted_cases': False}
_WITNESS = object()


def need(value, detail=ERROR):
    if not value:
        raise HTTPException(409, detail)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, StopIteration,
                UnicodeError, RecursionError, OverflowError):
            need(False)
    return wrapped


def hashed(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _uuid(value):
    need(type(value) is str and str(UUID(value)) == value)
    return value


def _fresh(db, p, model, identity):
    row = db.scalar(select(model).where(model.workspace_id == p.workspace_id, model.id == identity)
        .execution_options(populate_existing=True))
    need(row is not None)
    return row


def _reader(db, p):
    need(db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role == p.role, Membership.role.in_(['admin', 'operator', 'viewer']))) == p.user_id)


@checked
def require_supported_goal(task):
    """Root must also call this before creating the evaluation SDK Invocation."""
    frozen = snapshot(task); goal = frozen['spec']; unsupported = []
    if goal.get('relation_query') is not None:
        unsupported.append('relation_query')
    if goal.get('data_query') is not None:
        unsupported.append('data_query')
    if goal.get('tool_steps'):
        unsupported.append('tool_steps')
    need(not unsupported, {'schema': 'goal-evaluation-query-unsupported-v288',
        'reason': '此版本尚未实现原目标的关系或高级查询核对；完整目标保留，未缩减为字段查询',
        'unsupported_goal_parts': unsupported, 'goal_snapshot_hash': hashed(frozen), 'goal_unchanged': True})
    need(bool(goal['entity_ids']) and bool(goal['required_fields']))
    return copy.deepcopy(frozen)


@checked
def _check_creator_binding(db, p, task, release, business, graph, node, cap):
    """Only the actual saved V288 family can mint this transient query cut."""
    from . import authored_goal_data_workflow_store_v288 as family
    from . import workflow_goal_evaluation_packet_v288 as packet
    from .workflow_graph import business_binding, live
    from .layered_approval_store_v175 import guard
    from .workflow_graph_goal_guard_v133 import before_dispatch
    binding = business_binding(release, business)
    need(binding.get('schema') == family.BINDING
        and family.check_binding(binding) == binding and binding['workspace_id'] == p.workspace_id
        and binding['simulation_only'] is True and binding['graph'] == graph
        and release.instance_id == task.data['instance_id']
        and release.package_id == task.package_id and business['id'] == task.data['business_id']
        and binding['business_name'] == business['name']
        and node == next(item for item in graph['nodes'] if item['id'] == 'evaluation_packet')
        and binding['capability_pins']['evaluation_packet'] == node['execution']
        and binding['tool_bindings']['evaluation_packet'] == {
            'capability_id': cap.id, 'capability_hash': cap.content_hash, 'actor_id': p.user_id}
        and binding['service_delegations']['evaluation_packet'] == {
            'service_actor': 'reader_service', 'operation': packet.OP, 'agent_role': 'data'})
    packet.validate_manifest(cap.data)
    guard(db, p, task, record_actor=True)
    live(db, p, task, release, business)
    from .leases import ownership
    job = _fresh(db, p, Job, ownership(db)['job_id'])
    before_dispatch(db, p, task, job, node)


def _assert_creator(db, p, task, job, evaluation_id, preparation_policy):
    from . import workflow_graph as runtime, workflow_graph_scope as scope
    from . import native_goals
    from .leases import fenced, ownership
    from .relations import lock_current_actor
    from .workflow_service_v195 import make_approval
    need(fenced(db))
    lease = ownership(db)
    need(lease.get('job_id') == job.id and bool(lease.get('lease_token')))
    actual_job = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id, Job.id == job.id,
        Job.kind == scope.KIND, Job.status == 'running', Job.lease_token == lease['lease_token'],
        Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
    need(actual_job is not None and actual_job.data['actor_id'] == p.user_id)
    lock_current_actor(db, p)
    actual, release, business, graph, state, activation, node = scope._frozen(
        db, p, task.id, actual_job.data['activation_id'])
    scope._job_matches(actual, actual_job, state, activation, node)
    need(actual.id == task.id and node['kind'] == 'ability' and node['id'] == 'evaluation_packet'
        and node['actor_id'] == p.user_id and node['execution']['agent_role'] == 'data'
        and node['execution']['adapter'] == 'workflow_service')
    cap = _fresh(db, p, Capability, node['execution']['capability_id'])
    published(db, p, cap.id, 'tool'); Manifest.model_validate(cap.data)
    need(cap.content_hash == cap.validated_hash == digest(cap.data) == node['execution']['capability_hash']
        and cap.release_version == node['execution']['release_version']
        and cap.data['allowed_agent_roles'] == ['data']
        and cap.data['execution'] == {'adapter': 'workflow_service', 'service_actor': 'reader_service',
            'operation': OP, 'side_effect': True}
        and node['execution']['input_schema'] == cap.data['input_schema']
        and node['execution']['output_schema'] == cap.data['output_schema'])
    _check_creator_binding(db, p, actual, release, business, graph, node, cap)
    current_task, current_job, current_node, pin = scope.graph_context(
        db, p, actual_job.data['invocation_key'], cap)
    need(current_task.id == actual.id and current_job.id == actual_job.id and current_node == node)
    call = _fresh(db, p, Invocation, _uuid(evaluation_id))
    need(call.idempotency_key == actual_job.idempotency_key == actual_job.data['invocation_key']
        and call.status == 'running' and call.data['job_id'] == actual_job.id)
    approval = make_approval(actual, actual_job, node, actual_job.data['inputs'])
    runtime.check_invocation(call, p, cap, node, actual_job.data['inputs'], actual_job, lease, approval=approval)
    native_goals.guard(db, p, actual)
    policy = contract.check_preparation_policy(actual.data['goal_spec'], preparation_policy)
    binding = runtime.business_binding(release, business)
    need(binding.get('preparation_policy') == policy)
    scope_value = {'workspace_id': p.workspace_id, 'task_id': actual.id,
        'instance_id': actual.data['instance_id'], 'release_id': release.id, 'package_id': release.package_id,
        'business_id': business['id'], 'graph_hash': state['graph_hash'],
        'source_binding_hash': binding['binding_hash'], 'goal_snapshot_hash': hashed(snapshot(actual)),
        'native_goal_context_hash': actual.data['native_goal_context_hash']}
    creator = {'workspace_id': p.workspace_id, 'task_id': actual.id,
        'invocation_id': call.id, 'job_id': actual_job.id, 'actor_id': p.user_id,
        'lease_token': lease['lease_token'], 'activation_id': activation['id'], 'node_id': node['id'],
        'cancel_epoch': activation['epoch'],
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'input_hash': actual_job.data['input_hash'],
        'request_key': call.idempotency_key, 'request_key_hash': hashed(call.idempotency_key),
        'scope_hash': hashed(scope_value)}
    return actual, scope_value, creator, policy


@dataclass(frozen=True, slots=True)
class _Clock:
    witness: object
    db: object
    workspace_id: str
    actor_id: str
    job_id: str
    lease_token: str
    query_id: str
    task_id: str
    request_hash: str
    entity_ids: tuple
    fields: tuple
    requirement_hash: str
    window_json: str
    cut: datetime


def _query_clock(db, p, entity_ids, fields, requirement, fixed_window, route_key, value):
    """Private transient capability for exactly this local read, never JSON."""
    from .leases import fenced, ownership
    need(type(value) is _Clock and value.witness is _WITNESS and value.db is db
        and value.workspace_id == p.workspace_id and value.actor_id == p.user_id
        and tuple(entity_ids) == value.entity_ids and tuple(fields) == value.fields
        and hashed(EvidenceRequirement.model_validate(requirement).model_dump(mode='json')) == value.requirement_hash
        and canonical_json(fixed_window) == value.window_json and route_key == value.query_id
        and fenced(db) and ownership(db) == {'job_id': value.job_id, 'lease_token': value.lease_token})
    return value.cut, copy.deepcopy(fixed_window)


def _agent_clock(db, p, request, context_key, task, value, *, cached_only, emit_gaps, learning, include_trusted_cases):
    need(type(value) is _Clock and task is not None and task.id == value.task_id
        and hashed(request) == value.request_hash and request['mode'] == 'structured'
        and request.get('model_profile_id') is None and request.get('relation_query') is None
        and request.get('tool_steps') == [] and request.get('allow_external_tools') is False
        and cached_only is True and emit_gaps is False and learning is False and include_trusted_cases is False)
    _query_clock(db, p, request['entity_ids'], request['fields'], request['evidence_requirement'],
        request['resolved_time'], context_key, value)


def _archive_at_cut(db, p, entity, value):
    need(type(value) is _Clock and value.witness is _WITNESS and value.db is db)
    if entity.created_at is None or entity.created_at > value.cut:
        return False
    changed = db.scalar(select(EntityEvent.id).where(EntityEvent.workspace_id == p.workspace_id,
        EntityEvent.entity_id == entity.id, EntityEvent.created_at > value.cut).limit(1))
    return changed is None


def _observation_at_cut(db, p, observation, reception, value):
    """The ordinary cold receipt proof ran first; now prove its knowledge time."""
    need(type(value) is _Clock and value.witness is _WITNESS and value.db is db)
    receipt = reception.get('data_reception')
    if receipt is None:
        return None
    need(type(receipt) is dict and type(receipt.get('event_id')) is str)
    event = _fresh(db, p, Event, receipt['event_id'])
    rows = list(db.scalars(select(Memory).where(Memory.workspace_id == p.workspace_id,
        Memory.data['event_id'].as_string() == event.id,
        Memory.data['type'].as_string() == 'observation_evidence').limit(2)))
    need(event.kind == 'observation.data' and event.idempotency_key == 'data:' + observation.id
        and event.status == 'received' and event.data.get('data_reception') == receipt
        and len(rows) == 1 and rows[0].status == 'candidate'
        and rows[0].data.get('data_reception') == receipt)
    memory = rows[0]
    times = (observation.created_at, event.created_at, event.received_at, memory.created_at)
    need(all(isinstance(at, datetime) and at.tzinfo is not None and at.utcoffset() is not None for at in times))
    if (any(at > value.cut for at in times) or observation.observed_at is None
            or observation.observed_at > event.received_at or event.created_at > event.received_at):
        return None
    return {'cut_at': value.cut.isoformat(), 'event_id': event.id, 'received_at': event.received_at.isoformat(),
        'memory_id': memory.id, 'memory_created_at': memory.created_at.isoformat(),
        'observation_created_at': observation.created_at.isoformat(), 'data_reception_hash': receipt['receipt_hash']}


def _confirmation_at_cut(observation, verification, value):
    """Prove normal reviewed_at/history and independent review audits at cut.

    Legacy reviews without request IDs still need the same complete history,
    fingerprint, reviewed_at and audit evidence. An ``at`` alias alone never
    backdates a review. This read cannot renew a saved evaluation or mutate Q0.
    """
    need(type(value) is _Clock and value.witness is _WITNESS
        and observation.workspace_id == value.workspace_id)
    try:
        from .reported_form import verified_history
        history = verified_history(observation.data)
        fingerprint = digest({'extracted': observation.data['extracted'],
            'evidence_ids': observation.data.get('evidence_ids', []),
            'invocation_id': observation.data.get('invocation_id')})
        if (type(verification) is not dict or verification.get('decision') != 'confirmed'
                or verification != observation.data.get('human_verification')
                or not history or len(history) > 1000 or history[-1] != verification
                or digest(history) != observation.data.get('verification_history_hash')
                or verification.get('input_hash') != fingerprint
                or not isinstance(observation.created_at, datetime)
                or observation.created_at.tzinfo is None or observation.created_at.utcoffset() is None):
            return False
        if verification.get('request_id') and verification.get('body_hash') != digest({
                'expected_version': observation.version - 1, 'reason': verification['reason'],
                'decision': verification['decision'], 'request_id': verification['request_id'],
                'expected_input_hash': fingerprint}):
            return False
        expected = []
        for item in history:
            _uuid(item['reviewed_by'])
            at = instant(item['reviewed_at'])
            if (item.get('decision') not in {'confirmed', 'rejected'}
                    or type(item.get('reason')) is not str or item.get('input_hash') != fingerprint
                    or not observation.created_at <= at <= value.cut):
                return False
            if expected and at < expected[-1][0]:
                return False
            expected.append((at, (item['reviewed_by'], item['decision'], item['reason'])))
        proofs = list(value.db.scalars(select(Audit).where(Audit.workspace_id == value.workspace_id,
            Audit.object_id == observation.id, Audit.action == 'observation.verified').limit(1001)))
        if len(proofs) != len(history) or len(proofs) > 1000:
            return False
        actual, identities = [], set()
        for proof in proofs:
            _uuid(proof.id)
            _uuid(proof.actor_id)
            if (proof.id in identities or proof.workspace_id != value.workspace_id or proof.object_id != observation.id
                    or proof.action != 'observation.verified'
                    or type(proof.data) is not dict or set(proof.data) != {'decision', 'reason'}
                    or proof.data['decision'] not in {'confirmed', 'rejected'}
                    or type(proof.data['reason']) is not str
                    or not isinstance(proof.created_at, datetime) or proof.created_at.tzinfo is None
                    or proof.created_at.utcoffset() is None
                    or not observation.created_at <= proof.created_at <= value.cut):
                return False
            identities.add(proof.id)
            actual.append((proof.created_at, (proof.actor_id, proof.data['decision'], proof.data['reason'])))
        actual.sort(key=lambda item: item[0])
        for index, ((reviewed_at, meaning), (recorded_at, original_meaning)) in enumerate(zip(expected, actual)):
            # Normal review holds the original Observation row lock through
            # its Audit insert. The next review follows that prior insert.
            # Equal audit clocks cannot order different statements; UUIDs are
            # never a substitute for that missing temporal proof.
            if (meaning != original_meaning or index and recorded_at == actual[index - 1][0]
                    and original_meaning != actual[index - 1][1]):
                return False
            previous = actual[index - 1][0] if index else observation.created_at
            if not previous <= reviewed_at <= recorded_at:
                return False
        return True
    except (HTTPException, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        return False


def query_ref(row):
    return {'query_id': row.id, 'version': row.version, 'request_hash': hashed(row.data['request']),
        'result_hash': hashed(row.data['result']), 'data_hash': hashed(row.data)}


def _saved_audit(db, p, row):
    marker = row.data[KEY]
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == SAVED, Audit.object_id == row.id).limit(2)))
    need(len(rows) == 1 and rows[0].actor_id == marker['creator']['actor_id']
        and rows[0].data == {'schema': SCHEMA, 'evaluation_id': marker['evaluation_id'],
            'job_id': marker['creator']['job_id'], 'marker_hash': marker['marker_hash'], 'query_ref': query_ref(row)})


@checked
def checked_frozen(db, p, task, query_id, *, evaluation_id):
    """Original query snapshot only; creator completion belongs to the reader."""
    with db.no_autoflush:
        _reader(db, p); row = _fresh(db, p, DataRun, query_id); marker = row.data[KEY]
        if type(marker) is dict and marker.get('schema') == 'goal-evaluation-query-provenance-v299':
            from .goal_evaluation_append_query_v299 import checked_frozen as append_snapshot
            return append_snapshot(db, p, task, query_id, evaluation_id=evaluation_id)
        need(type(marker) is dict and marker['schema'] == SCHEMA
            and marker['evaluation_id'] == _uuid(evaluation_id)
            and marker['scope']['workspace_id'] == p.workspace_id == task.workspace_id
            and marker['scope']['task_id'] == task.id
            and marker['scope']['goal_snapshot_hash'] == hashed(snapshot(task))
            and row.owner_id == marker['creator']['actor_id']
            and marker['creator']['invocation_id'] == marker['evaluation_id']
            and marker['marker_hash'] == hashed({key: item for key, item in marker.items() if key != 'marker_hash'})
            and row.data['request'] == row.data['result']['request']
            and row.status == row.data['result']['status'] == row.data['result']['result']['status']
            and row.data['result']['result']['resolved_time'] == marker['derived_window']
            and row.data['result']['result']['evaluated_at'] == marker['cut_at'])
        _saved_audit(db, p, row)
        return {'query_ref': query_ref(row), 'request': copy.deepcopy(row.data['request']),
            'result': copy.deepcopy(row.data['result']), 'provenance': copy.deepcopy(marker)}


@checked
def create_frozen(db, p, task, job, *, evaluation_id, cut_at, parent_query_id, preparation_policy):
    """Caller owns the original transaction/fence; no SDK completion is minted."""
    original_goal = require_supported_goal(task)
    task, scope, creator, policy = _assert_creator(db, p, task, job, evaluation_id, preparation_policy)
    need(snapshot(task) == original_goal and parent_query_id == task.data['native_goal_query_id'])
    trusted = db.scalar(select(func.clock_timestamp()))
    need(isinstance(trusted, datetime) and trusted.tzinfo is not None)
    cut = instant(cut_at); cut_at = cut.isoformat()
    initial_reference = instant(original_goal['time']['reference_at'])
    need(initial_reference <= cut and original_goal['time'] == resolve(
        original_goal['spec']['evidence']['time'], reference=initial_reference))
    window = contract.derive_evaluation_window(original_goal['spec'], policy,
        cut_at=cut_at, trusted_now=trusted.isoformat(),
        **({'initial_window': original_goal['time']} if policy['time_basis'] == 'fixed_window' else {}))
    if original_goal['spec'].get('deadline'):
        need(cut < instant(original_goal['spec']['deadline']))
    existing = list(db.scalars(select(DataRun).where(DataRun.workspace_id == p.workspace_id,
        DataRun.data[KEY]['evaluation_id'].as_string() == evaluation_id).limit(2)))
    reverse = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == SAVED, Audit.data['evaluation_id'].as_string() == evaluation_id).limit(2)))
    need(len(existing) <= 1 and len(reverse) <= 1)
    if existing:
        frozen = checked_frozen(db, p, task, existing[0].id, evaluation_id=evaluation_id)
        need(frozen['provenance']['cut_at'] == cut_at and frozen['provenance']['preparation_policy'] == policy
            and frozen['provenance']['scope'] == scope and len(reverse) == 1)
        return existing[0]
    need(not reverse)
    parent = _fresh(db, p, DataRun, _uuid(parent_query_id))
    need(parent.data.get('native_goal_task_id') == task.id
        and parent.data.get('native_goal_request_hash') == digest(parent.data['request'])
        and parent.data.get('native_goal_context_hash') == task.data['native_goal_context_hash']
        and parent.data.get('goal_delegation') == {'task_id': task.id, 'goal_snapshot': original_goal}
        and parent.data.get('native_goal_result_hash') == digest(parent.data['result']))
    parent_request = parent.data['request']; goal = original_goal['spec']
    need(parent_request.get('mode') == 'structured' and parent_request.get('model_profile_id') is None
        and parent_request.get('tool_steps') == [] and parent_request.get('allow_external_tools') is False
        and parent_request.get('entity_ids') == goal['entity_ids'] and parent_request.get('fields') == goal['required_fields']
        and parent_request.get('evidence_requirement') == goal['evidence'] and parent_request.get('relation_query') is None)
    from .data_requirements import prepare_request
    request = prepare_request(db, p, DataRequest(question=goal['purpose'], entity_ids=goal['entity_ids'],
        fields=goal['required_fields'], mode='structured', model_profile_id=None,
        business_timezone=policy['timezone'], evidence_requirement=goal['evidence'],
        relation_query=None, tool_steps=[], allow_external_tools=False).model_dump(mode='json'),
        reference=cut, _confirmed_plan=True, task=task)
    need(request['clarifications'] == [])
    request['resolved_time'] = copy.deepcopy(window)
    marker = {'schema': SCHEMA, 'evaluation_id': _uuid(evaluation_id), 'request_id': creator['job_id'],
        'request_key': creator['request_key'], 'scope': scope, 'creator': creator,
        'goal_snapshot': original_goal, 'preparation_policy': policy, 'derived_window': window,
        'cut_at': cut_at, 'cut_checked_at': trusted.isoformat(), 'controls': copy.deepcopy(CONTROLS),
        'original_query_ref': query_ref(parent), 'original_query_snapshot': copy.deepcopy(parent.data)}
    marker['marker_hash'] = hashed(marker)
    row = DataRun(workspace_id=p.workspace_id, owner_id=p.user_id, question=goal['purpose'],
        status='queued', version=1, data={'request': copy.deepcopy(request), KEY: marker})
    db.add(row); db.flush()
    clock = _Clock(_WITNESS, db, p.workspace_id, p.user_id, creator['job_id'], creator['lease_token'],
        row.id, task.id, hashed(request), tuple(request['entity_ids']), tuple(request['fields']),
        hashed(request['evidence_requirement']), canonical_json(window), cut)
    from .agents import data_agent
    state = data_agent(db, p, request, row.id, **CONTROLS, task=task, _goal_evaluation_cut=clock)
    need(state['request'] == request and state['status'] == state['result']['status']
        and state['result']['resolved_time'] == window and state['result']['evaluated_at'] == cut_at
        and state['result']['evidence_requirement'] == goal['evidence'])
    row.status = state['status']; row.data = {**row.data, 'result': copy.deepcopy(state)}
    audit(db, p, SAVED, row.id, {'schema': SCHEMA, 'evaluation_id': evaluation_id,
        'job_id': creator['job_id'], 'marker_hash': marker['marker_hash'], 'query_ref': query_ref(row)})
    # The immediate cold reader uses no_autoflush + populate_existing. Persist
    # this result and its audit in the caller's transaction before that reload;
    # otherwise it can replace the unflushed result with the initial request.
    db.flush()
    return row
