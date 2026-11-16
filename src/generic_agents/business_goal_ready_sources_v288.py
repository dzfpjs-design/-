"""Cold V288 evaluations and V207 documents; never a V199 source alias.

Only the original saved evaluation/query/ACK/bytes and original successful
producers are used. Live qualification also checks current rolling freshness;
historical inspection never rolls the cut or renews acquisition permissions.
"""
import copy
from functools import wraps

from fastapi import HTTPException
from sqlalchemy import func, select

from . import business_decision_sources_v276 as original
from . import workflow_goal_evaluation_packet_v288 as packet
from . import workflow_goal_evaluation_store_v288 as store
from . import workflow_graph as workflow, workflow_graph_scope as scope
from . import goal_evaluation_query_v288 as queries
from .business_flow_review_store import _bytes
from .contracts import digest
from .models import Capability, Entity, Invocation, Job, Observation, Task
from .services import validate
from .temporal import instant, matches, resolve as resolve_time
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288

SCHEMA = 'business-goal-ready-sources-v288'
ERROR = '独立资料评估、原文档或成功生产记录无法核对；未刷新查询、采集或发送模型'
MAX_BYTES = original.MAX_BYTES
INPUT_KEYS = original.INPUT_KEYS
fresh, _reader_iam, _audits, same = original.fresh, original._reader_iam, original._audits, original.same


def need(value):
    if not value:
        raise HTTPException(409, ERROR)


def safe(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError,
                StopIteration, UnicodeError, OverflowError, RecursionError, OSError):
            raise HTTPException(409, ERROR) from None
    return checked


def data_packet_schema(): return packet.packet_schema()
def document_packet_schema(): return original.document_packet_schema()


def input_schema():
    value = original.input_schema()
    value['properties']['data_packet'] = data_packet_schema()
    return value


def _inputs(value):
    need(type(value) is dict and {'data_packet', 'document_packet'} <= set(value))
    selected = {key: copy.deepcopy(value[key]) for key in INPUT_KEYS if key in value}
    if 'evidence_ids' not in selected:
        selected['evidence_ids'] = [selected['data_packet']['evidence_id'], selected['document_packet']['evidence_id']]
    validate(input_schema(), selected); _bytes(selected, MAX_BYTES)
    identities = [selected['data_packet']['evidence_id'], selected['document_packet']['evidence_id']]
    need(len(set(identities)) == 2 and set(selected['evidence_ids']) == set(identities))
    return selected


def _producer(db, p, task, row, call, job, cap, *, allow_pending=False):
    if row.data.get('schema') == 'workflow-goal-evaluation-record-v299':
        need(job.kind == 'append_goal_evaluation_v297' and row.data.get('producer_family') == 'append_command_v297')
        if allow_pending and job.status == 'running':
            from .workflow_task_context_v196 import ProducerPending
            raise ProducerPending(job.id,call.id)
        from .workflow_goal_evaluation_append_v299 import saved_result as saved_append
        output=saved_append(db,p,task,job,call,cap,job.data['inputs'])
        from .goal_evaluation_provider_v288 import binding
        actual=fresh(db,p,Task,task.id);selected=binding(db,p,actual)
        need(selected is not None and selected['release'].id==row.release_id)
        return actual,selected['release'],call.data['approval'],output
    from .workflow_service_v195 import make_approval
    actual, release, business, graph, state, activation, node = scope._frozen(db, p, task.id, job.data['activation_id'])
    transport = scope._job_matches(actual, job, state, activation, node)
    binding = workflow.business_binding(release, business)
    from .authored_goal_data_workflow_store_v288 import BINDING, check_binding
    packet.validate_manifest(cap.data)
    inputs = job.data['inputs']; output = packet.checked_result(call.data['output'])
    need(binding['schema'] == BINDING and check_binding(binding) == binding
        and binding['workspace_id'] == p.workspace_id and binding['graph'] == graph
        and row.workspace_id == call.workspace_id == job.workspace_id == p.workspace_id
        and row.task_id == actual.id == task.id and row.id == row.invocation_id == call.id
        and row.job_id == job.id and row.actor_id == node['actor_id'] == job.data['actor_id']
        and release.id == row.release_id == task.data['instance_release_id']
        and release.instance_id == row.instance_id == task.data['instance_id']
        and release.package_id == row.package_id == task.package_id
        and node['id'] == 'evaluation_packet' and node['execution']['adapter'] == 'workflow_service'
        and node['execution']['agent_role'] == 'data' and cap.id == call.capability_id == node['execution']['capability_id']
        and cap.content_hash == cap.validated_hash == digest(cap.data) == node['execution']['capability_hash']
        and cap.release_version == node['execution']['release_version']
        and node['execution']['input_schema'] == cap.data['input_schema']
        and node['execution']['output_schema'] == cap.data['output_schema']
        and call.status == 'succeeded' and job.kind == scope.KIND
        and job.status in ({'running', 'succeeded'} if allow_pending else {'succeeded'})
        and activation['status'] == 'completed' and same(activation['output'], output)
        and transport.get('phase') == 'succeeded' and transport.get('dispatch_committed') is True
        and transport.get('invocation_id') == call.id and transport.get('output_hash') == digest(output)
        and transport.get('dispatch_input_hash') == digest(inputs))
    owner = transport['dispatch_ownership']; authorized = transport['dispatch_authorization']
    need(type(owner) is dict and set(owner) == {'job_id', 'lease_token'} and owner['job_id'] == job.id
        and type(owner['lease_token']) is str and bool(owner['lease_token'])
        and type(authorized) is dict and authorized.get('job_id') == job.id
        and authorized.get('lease_token') == owner['lease_token']
        and authorized.get('actor_id') == row.actor_id and authorized.get('epoch') == activation['epoch']
        and authorized.get('input_hash') == digest(inputs) and authorized.get('capability_hash') == cap.content_hash)
    approval = make_approval(actual, job, node, inputs)
    workflow.check_invocation(call, p, cap, node, inputs, job, owner, approval=approval)
    store.local._native_audits(db, p, actual, job, activation, call, inputs)
    store.local._success(db, p, actual, job, activation, call)
    return actual, release, approval, output


def _origins(db, p, task, record):
    """Real provenance for each admitted Qe record, never excluded candidates."""
    from .observation_integrity import checked_report
    from .simulation_observation_origin_v177 import guard as origin_guard
    from .instance_data_receipts import observation_receipt
    from .http_observation_exchange_v177 import checked_store, snapshot as exchange_snapshot
    from . import goal_ack_origin_mqtt_v297 as mqtt_origin
    report = record['evaluation_result']['result']; fields = record['evaluation_request']['fields']; result = []
    for row in report['evidence']:
        entity = fresh(db, p, Entity, row['entity_id'])
        need(entity.id in task.data['entity_ids'] and row['source'] == 'resource_entity'
            and entity.version == row['version'] and row['attributes'] == {
                key: value for key, value in entity.data.items() if key in fields}
            and row['observed_at'] == (entity.observed_at.isoformat() if entity.observed_at else None)
            and row['valid_until'] == (entity.valid_until.isoformat() if entity.valid_until else None))
        result.append({'kind': 'archive', 'record': copy.deepcopy(row), 'entity': {
            'id': entity.id, 'version': entity.version, 'entity_type': entity.entity_type,
            'package_id': entity.schema_capability_id, 'data': copy.deepcopy(entity.data)}})
    for row in report['observation_evidence']:
        obs = fresh(db, p, Observation, row['observation_id']); checked = checked_report(db, p, obs)
        reception = observation_receipt(db, p, obs)
        if mqtt_origin.handles(db, p, obs):
            origin = mqtt_origin.checked_origin(db, p, obs, task=task, cut=record['cut_at'])
            references = [ack for ack in record['ack_refs'] if ack['observation_id'] == obs.id]
            need(len(references) == 1 and references[0].get('origin_kind') == mqtt_origin.KIND
                and same(references[0]['mqtt_origin'], mqtt_origin.immutable_pins(origin)))
        else:
            origin = origin_guard(db, p, obs, execution=False, result=checked['extracted'])
        need(obs.entity_id == row['entity_id'] in task.data['entity_ids'] and obs.version == row['version']
            and obs.source_id == row['source_id'] and row['attributes'] == {
                key: value for key, value in checked['attributes'].items() if key in fields}
            and row['observed_at'] == obs.observed_at.isoformat() and row['data_reception'] == reception['data_reception']
            and checked['review'] != 'rejected')
        private = {'kind': 'observation', 'record': copy.deepcopy(row),
            'observation': {'id': obs.id, 'version': obs.version, 'data': copy.deepcopy(obs.data),
                'source_id': obs.source_id, 'entity_id': obs.entity_id},
            'report': checked, 'origin': origin, 'reception': reception}
        if type(origin) is dict and origin.get('exchange_id'):
            private['http_exchange'] = exchange_snapshot(checked_store(db, p, origin['exchange_id'], lock=False))
        result.append(private)
    return result


def _freshness(db, record):
    policy = record['preparation_policy']
    if policy['time_basis'] != 'rolling_latest': return
    at = db.scalar(select(func.clock_timestamp()))
    window = resolve_time(record['goal_snapshot']['spec']['evidence']['time'], reference=at)
    report = record['evaluation_result']['result']; admitted = report['evidence'] + report['observation_evidence']
    need(bool(admitted) and at >= instant(record['cut_at']) and all(
        item.get('observed_at') is not None and matches(instant(item['observed_at']), window, evaluated_at=at)
        for item in admitted))
    # Archive validity can expire after the saved cut without a row update.
    # Only live dispatch checks today's clock; historical verification keeps
    # the original query/GoalEvaluation semantics and never rolls that cut.
    need(all(item.get('valid_until') is None or instant(item['valid_until']) >= at
        for item in report['evidence']))


def _data(db, p, task, reference, *, allow_pending=False, live=True):
    row = fresh(db, p, GoalEvaluationV288, reference['id'])
    call = fresh(db, p, Invocation, row.invocation_id); job = fresh(db, p, Job, row.job_id)
    cap = fresh(db, p, Capability, call.capability_id)
    actual, release, approval, output = _producer(db, p, task, row, call, job, cap, allow_pending=allow_pending)
    if row.data.get('schema') == 'workflow-goal-evaluation-record-v299':
        result=output  # The fixed append producer has just cold-verified this original result.
    elif allow_pending and job.status == 'running':
        # Read the original committed journal without claiming Job completion.
        result = store._read(db, p, actual, job, call, cap, job.data['inputs'], approval)
    else:
        result = packet.saved_result(db, p, actual, job, call, cap, job.data['inputs'])
    need(result == output and reference == output['packet'] and output['route'] == 'data_ready'
        and output['evaluation_ref'] == {'id': row.id, 'record_hash': row.record_hash}
        and reference['evidence_id'] == row.evidence_id and reference['sha256'] == row.data['evidence_ref']['sha256'])
    record = copy.deepcopy(row.data)
    if live: _freshness(db, record)
    from .evidence import evidence_bytes
    evidence, content = evidence_bytes(db, p, row.evidence_id)
    need(evidence.sha256 == reference['sha256'] and evidence.media_type == 'text/plain'
        and content == store._document(actual, record))
    package = fresh(db, p, Capability, task.package_id)
    value = {'packet_id': row.id, 'packet': copy.deepcopy(reference), 'evidence_id': evidence.id,
        'sha256': evidence.sha256, 'producer_job_id': job.id, 'invocation_id': call.id,
        'evaluation_record': record, 'fact_origins': _origins(db, p, actual, record), 'text': content.decode('utf-8')}
    return release, package.content_hash, value


def _resolve(db, p, task, inputs, *, allow_pending=False, live=True):
    with db.no_autoflush:
        _reader_iam(db, p); need(isinstance(task, Task))
        actual = fresh(db, p, Task, task.id); selected = _inputs(inputs)
        release, package_hash, data = _data(db, p, actual, selected['data_packet'], allow_pending=allow_pending, live=live)
        documents_release, documents = original._documents(db, p, actual, selected['document_packet'], allow_pending=allow_pending)
        package = fresh(db, p, Capability, actual.package_id)
        need(release.id == documents_release.id == actual.data['instance_release_id']
            and package.kind == 'domain_package' and package.content_hash == package.validated_hash == digest(package.data) == package_hash)
        value = {'schema': SCHEMA, 'task_id': actual.id, 'release_id': release.id,
            'package_id': package.id, 'package_hash': package_hash, 'data': data, 'documents': documents,
            'evidence_ids': selected['evidence_ids']}
        value['sources_hash'] = digest(value); _bytes(value, MAX_BYTES)
        return value


@safe
def resolve(db, p, task, inputs): return _resolve(db, p, task, inputs)


@safe
def wait_snapshot(db, p, task, inputs):
    value = _resolve(db, p, task, inputs, allow_pending=True)
    pending = [value[key]['producer_job_id'] for key in ('data', 'documents')
        if fresh(db, p, Job, value[key]['producer_job_id']).status == 'running']
    return value, pending


@safe
def checked_frozen(db, p, task, frozen):
    _bytes(frozen, MAX_BYTES)
    need(type(frozen) is dict and set(frozen) == {'schema', 'task_id', 'release_id', 'package_id',
        'package_hash', 'data', 'documents', 'evidence_ids', 'sources_hash'} and frozen['schema'] == SCHEMA
        and frozen['sources_hash'] == digest({key: value for key, value in frozen.items() if key != 'sources_hash'}))
    value = _resolve(db, p, task, {'data_packet': frozen['data']['packet'],
        'document_packet': frozen['documents']['packet'], 'evidence_ids': frozen['evidence_ids']}, live=False)
    need(value == frozen)
    return copy.deepcopy(value)
