"""Cold, local sources for a decision frame; never a model payload or authority.

Both packet families must retain their original successful producers. The data
source is the V199 packet's saved query, never the current DataRun. The document
source is the V207 packet's independently checked, explicitly selected excerpts.
This module does not query fresh evidence, reconcile, receive or impersonate.
"""
import copy
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import select

from .business_flow_review_store import _bytes
from .contracts import digest
from .models import Audit, Capability, Invocation, Job, Membership, Task, User
from .services import validate
from . import workflow_graph as workflow
from . import workflow_graph_scope as scope
from . import workflow_task_packet_v199 as data_family
from . import workflow_document_packet_v207 as document_family

SCHEMA = 'business-decision-sources-v276'
ERROR = '原任务的两份来源包、成功生产记录或冻结资料无法完整核对；未查询新资料或生成事实'
MAX_BYTES = 16 * 1024 * 1024
INPUT_KEYS = {'data_packet', 'document_packet', 'evidence_ids'}


def need(value):
    if not value:
        raise HTTPException(409, ERROR)


def same(left, right):
    return digest(left) == digest(right)


def safe(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (HTTPException, ValueError, KeyError, TypeError, AttributeError,
                StopIteration, UnicodeError, OverflowError, RecursionError, OSError):
            raise HTTPException(409, ERROR) from None
    return checked


def data_packet_schema():
    return copy.deepcopy(data_family.output_schema()['properties']['packet'])


def document_packet_schema():
    return copy.deepcopy(document_family.output_schema()['properties']['packet'])


def input_schema():
    """Source ports only; another installed contract may add its own inputs."""
    ident = copy.deepcopy(data_packet_schema()['properties']['evidence_id'])
    return {'type': 'object', 'additionalProperties': False, 'required': sorted(INPUT_KEYS),
        'properties': {'data_packet': data_packet_schema(), 'document_packet': document_packet_schema(),
            'evidence_ids': {'type': 'array', 'minItems': 2, 'maxItems': 2,
                'uniqueItems': True, 'items': ident}}}


def _inputs(value):
    need(type(value) is dict and {'data_packet', 'document_packet'} <= set(value))
    selected = {key: copy.deepcopy(value[key]) for key in INPUT_KEYS if key in value}
    if 'evidence_ids' not in selected:
        selected['evidence_ids'] = [selected['data_packet']['evidence_id'], selected['document_packet']['evidence_id']]
    validate(input_schema(), selected); _bytes(selected, MAX_BYTES)
    identities = [selected['data_packet']['evidence_id'], selected['document_packet']['evidence_id']]
    need(len(set(identities)) == 2 and set(selected['evidence_ids']) == set(identities))
    return selected


def fresh(db, p, model, identity):
    rows = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id,
        model.id == identity).execution_options(populate_existing=True)))
    need(len(rows) == 1)
    return rows[0]


def _reader_iam(db, p):
    need(db.scalar(select(User.id).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role == p.role, Membership.role.in_(['admin', 'operator', 'viewer']))) == p.user_id)


def _audits(db, p, action, identity):
    return list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).execution_options(populate_existing=True)))


def _producer(db, p, task, packet, call, job, family, output, *, allow_pending=False):
    """Historical identity, original dispatch and completion; no live lease gate."""
    call = fresh(db, p, Invocation, call.id); job = fresh(db, p, Job, job.id)
    cap = fresh(db, p, Capability, call.capability_id)
    actual, release, business, graph, state, activation, node = scope._frozen(
        db, p, task.id, job.data['activation_id'])
    transport = scope._job_matches(actual, job, state, activation, node)
    binding = workflow.business_binding(release, business)
    family.validate_manifest(cap.data)
    inputs = job.data['inputs']; spec = node['execution']
    need(actual.id == task.id == packet.task_id and task.workspace_id == p.workspace_id
        and release.id == task.data['instance_release_id'] and release.instance_id == task.data['instance_id']
        and release.package_id == task.package_id and same(binding['graph'], graph)
        and packet.workspace_id == call.workspace_id == job.workspace_id == p.workspace_id
        and packet.invocation_id == call.id and packet.job_id == job.id
        and packet.actor_id == node['actor_id'] == job.data['actor_id']
        and job.kind == scope.KIND and call.status == 'succeeded'
        and job.status in ({'running', 'succeeded'} if allow_pending else {'succeeded'})
        and activation['status'] == 'completed' and same(activation['output'], output)
        and same(call.data['output'], output) and same(call.data['inputs'], inputs)
        and spec['adapter'] == family.ADAPTER and spec['agent_role'] == 'data'
        and spec['capability_id'] == cap.id and spec['capability_hash'] == cap.content_hash
        and cap.content_hash == cap.validated_hash == digest(cap.data)
        and spec['release_version'] == cap.release_version
        and same(spec['input_schema'], cap.data['input_schema'])
        and same(spec['output_schema'], cap.data['output_schema']))
    approval = family.make_approval(actual, job, node, inputs)
    owner = transport.get('dispatch_ownership'); authorization = transport.get('dispatch_authorization')
    need(type(owner) is dict and set(owner) == {'job_id', 'lease_token'}
        and owner['job_id'] == job.id and type(owner['lease_token']) is str and bool(owner['lease_token'])
        and type(authorization) is dict and set(authorization) == {'epoch', 'job_id', 'lease_token',
            'input_hash', 'capability_hash', 'actor_id', 'authorized_at'}
        and authorization['job_id'] == job.id and authorization['lease_token'] == owner['lease_token']
        and authorization['epoch'] == activation['epoch']
        and authorization['actor_id'] == packet.actor_id
        and authorization['input_hash'] == digest(inputs) and authorization['capability_hash'] == cap.content_hash
        and type(authorization['authorized_at']) is str and bool(authorization['authorized_at'])
        and transport.get('phase') == 'succeeded' and transport.get('dispatch_committed') is True
        and transport.get('invocation_id') == call.id and transport.get('output_hash') == digest(output)
        and transport.get('dispatch_input_hash') == digest(inputs))
    workflow.check_invocation(call, p, cap, node, inputs, job, owner, approval=approval)
    completed = [event for event in _audits(db, p, 'workflow.graph.node_completed', task.id)
        if event.data.get('activation_id') == activation['id']]
    authorized = [event for event in _audits(db, p, 'workflow.graph.dispatch_authorized', task.id)
        if event.data.get('job_id') == job.id]
    need(len(completed) == len(authorized) == 1 and completed[0].actor_id == authorized[0].actor_id == packet.actor_id
        and completed[0].data == {'activation_id': activation['id'], 'invocation_id': call.id}
        and authorized[0].data == {'activation_id': activation['id'], 'job_id': job.id,
            'epoch': activation['epoch'], 'input_hash': digest(inputs)})
    return release, binding, scope._scope(actual, job, node, activation)


def _data(db, p, task, reference, *, allow_pending=False):
    packet = fresh(db, p, data_family.Packet, reference['id'])
    command, call, job, evidence, _content, output = data_family._checked(db, p, packet)
    need(same(reference, output['packet']) and output['evidence_ids'] == [reference['evidence_id']]
        and evidence.id == packet.evidence_id == reference['evidence_id'] and evidence.sha256 == reference['sha256'])
    release, binding, producer_scope = _producer(db, p, task, packet, call, job, data_family, output,
        allow_pending=allow_pending)
    provenance = packet.data['provenance']; result = output['context_result']; package = result['evidence_package']
    saved_query = provenance['query']; saved = saved_query['data']; query = package['query']
    need(provenance['schema'] == 'workflow-task-evidence-packet-provenance-v199'
        and same(packet.data['context_result'], result) and same(provenance['scope'], producer_scope)
        and provenance['binding_hash'] == binding['binding_hash'] and provenance['release_id'] == release.id
        and package['scope']['task_id'] == task.id and package['scope']['release_id'] == release.id
        and package['scope']['package_id'] == task.package_id
        and package['scope']['source_binding_hash'] == binding['binding_hash']
        and package['scope']['native_goal_context_hash'] == digest(provenance['goal'])
        and query['query_id'] == saved_query['id'] and query['version_at_read'] == saved_query['version']
        and query['request_hash'] == digest(saved['request']) == saved['native_goal_request_hash']
        and query['goal_delegation_hash'] == digest(saved['goal_delegation'])
        and saved['goal_delegation']['task_id'] == saved['native_goal_task_id'] == task.id
        and saved['native_goal_context_hash'] == digest(provenance['goal'])
        and query['result_hash_at_read'] == digest(saved['result']) == saved['native_goal_result_hash'])
    return release, provenance['package_hash'], {'packet_id': packet.id, 'packet': copy.deepcopy(reference),
        'evidence_id': evidence.id, 'sha256': evidence.sha256, 'producer_job_id': job.id, 'invocation_id': call.id,
        'provenance': copy.deepcopy(provenance), 'context_result': copy.deepcopy(result), 'text': _content.decode('utf-8')}


def _documents(db, p, task, reference, *, allow_pending=False):
    packet = fresh(db, p, document_family.Packet, reference['id'])
    proof = document_family.checked_packet(db, p, packet, allow_pending=allow_pending)
    actual, command, call, job, actual_task, source_set, _activation, _transport = document_family._inventory(db, p, packet.invocation_id)
    output = command.data['output']
    need(actual.id == packet.id and actual_task.id == task.id and same(reference, output['packet'])
        and output['evidence_ids'] == [reference['evidence_id']]
        and proof['packet_id'] == packet.id and proof['producer_job_id'] == job.id and proof['invocation_id'] == call.id
        and proof['evidence_id'] == packet.evidence_id == reference['evidence_id']
        and proof['sha256'] == packet.sha256 == reference['sha256'])
    release, _binding, _producer_scope = _producer(db, p, task, packet, call, job, document_family, output,
        allow_pending=allow_pending)
    need(source_set.task_id == task.id and source_set.release_id == release.id
        and source_set.instance_id == task.data['instance_id']
        and source_set.id == reference['source_set_id'] and source_set.source_set_hash == reference['source_set_hash'])
    text = document_family.sources.render(source_set.data['entries']).decode('utf-8')
    return release, {'packet_id': packet.id, 'packet': copy.deepcopy(reference), 'evidence_id': packet.evidence_id,
        'sha256': packet.sha256, 'producer_job_id': job.id, 'invocation_id': call.id,
        'source_set_ref': {'source_set_id': source_set.id, 'source_set_hash': source_set.source_set_hash},
        'entries': copy.deepcopy(source_set.data['entries']), 'text': text}


def _resolve(db, p, task, inputs, *, allow_pending=False):
    with db.no_autoflush:
        _reader_iam(db, p); need(isinstance(task, Task))
        task = fresh(db, p, Task, task.id); selected = _inputs(inputs)
        data_release, package_hash, data = _data(db, p, task, selected['data_packet'], allow_pending=allow_pending)
        document_release, documents = _documents(db, p, task, selected['document_packet'], allow_pending=allow_pending)
        package = fresh(db, p, Capability, task.package_id)
        need(data_release.id == document_release.id == task.data['instance_release_id']
            and package.kind == 'domain_package' and package.workspace_id == p.workspace_id
            and package.content_hash == package.validated_hash == digest(package.data) == package_hash)
        value = {'schema': SCHEMA, 'task_id': task.id, 'release_id': data_release.id,
            'package_id': package.id, 'package_hash': package_hash, 'data': data, 'documents': documents,
            'evidence_ids': copy.deepcopy(selected['evidence_ids'])}
        value = {**value, 'sources_hash': digest(value)}
        _bytes(value, MAX_BYTES)
        return value


@safe
def resolve(db, p, task, inputs):
    """Freeze only terminal producers; callers must project before cloud IO."""
    return _resolve(db, p, task, inputs)


@safe
def wait_snapshot(db, p, task, inputs):
    """Read committed complete receipts while their original worker is closing.

    A running original Job is only a wait condition. This result is never a
    decision frame or a paid-call qualification; resolve still requires success.
    """
    with db.no_autoflush:
        value = _resolve(db, p, task, inputs, allow_pending=True)
        pending = [value[key]['producer_job_id'] for key in ('data', 'documents')
            if fresh(db, p, Job, value[key]['producer_job_id']).status == 'running']
        return value, pending


@safe
def checked_frozen(db, p, task, frozen):
    """Recheck the same immutable packet references; never choose a new query."""
    _bytes(frozen, MAX_BYTES)
    need(type(frozen) is dict and set(frozen) == {'schema', 'task_id', 'release_id',
        'package_id', 'package_hash', 'data', 'documents', 'evidence_ids', 'sources_hash'}
        and frozen['schema'] == SCHEMA
        and frozen['sources_hash'] == digest({key: value for key, value in frozen.items() if key != 'sources_hash'}))
    original = resolve(db, p, task, {'data_packet': frozen['data']['packet'],
        'document_packet': frozen['documents']['packet'], 'evidence_ids': frozen['evidence_ids']})
    need(same(original, frozen))
    return copy.deepcopy(original)
