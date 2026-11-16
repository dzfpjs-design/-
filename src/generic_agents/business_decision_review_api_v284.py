"""Original V284 decision context, read in one authenticated RR/RO snapshot.

This projection neither executes a graph node nor reconciles a paid response.
The actual reader keeps current IAM; historical actor, dispatch, SDK, capture,
Frame and receiver evidence remain those of the exact original producer.
"""
import copy
import json
from functools import wraps
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from . import business_decision_runtime_v284 as runtime
from . import business_decision_sources_v276 as sources
from . import workflow_graph as graph
from . import workflow_graph_scope as scope
from .business_flow_review_store import _bytes, _same
from .contracts import digest
from .models import Audit, Capability, Invocation, Job
from .security import Principal, require
from .signal_dual_read_session import read_session
from .snapshot_authentication_v141 import snapshot_authenticated as authenticated
from .workflow_decision_tool_planner_v285 import _native_audits, _success_audit

SCHEMA = 'business-decision-review-context-v284'
ERROR = '原决策成功记录、首答或冻结资料暂不能完整核对；未调用模型、补采或改写原记录'
MAX_BYTES = runtime.MAX_STATE_BYTES
router = APIRouter(prefix='/api/workspace/tasks')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(read_session)]
PENDING = '业务分析后台任务仍在收尾，暂不能读取已确认结果；请稍候核对本任务进度。'


def need(ok):
    if not ok:
        raise HTTPException(409, ERROR)


def checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, IndexError,
                StopIteration, UnicodeError, RecursionError, OverflowError):
            need(False)
    return wrapped


def _protocol(adapter):
    """Only an actual frozen node selects a fixed server implementation."""
    if adapter == 'business_decision_v284':
        return runtime, sources
    if adapter == 'business_goal_ready_v288':
        from . import business_goal_ready_runtime_v288, business_goal_ready_sources_v288
        return business_goal_ready_runtime_v288, business_goal_ready_sources_v288
    need(False)


def _successful_protocol_call(db, p, protocol, call, job, activation, result):
    if protocol is runtime:
        return _success_audit(db, p, call, job, activation, result)
    need(protocol is _protocol('business_goal_ready_v288')[0])
    from .business_reasoning_protocols_v276 import recovery_record
    marker, action = recovery_record(protocol.ADAPTER)
    original = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'capability.invoke', Audit.object_id == call.id).limit(2)))
    recovery = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == call.id).limit(2)))
    saved = call.data.get(marker)
    if saved is None:
        need(not recovery and len(original) == 1 and original[0].actor_id == job.data['actor_id']
            and _same(original[0].data, {'status': 'succeeded'}))
        return
    need(type(saved) is dict and set(saved) == {'previous_status', 'output_hash', 'recovered_at'}
        and type(saved['previous_status']) is str and saved['previous_status']
        and saved['previous_status'] != 'succeeded' and saved['output_hash'] == digest(result)
        and type(saved['recovered_at']) is str and saved['recovered_at']
        and len(original) <= 1 and len(recovery) == 1
        and all(event.actor_id == job.data['actor_id']
            and _same(event.data, {'status': saved['previous_status']}) for event in original)
        and recovery[0].actor_id == job.data['actor_id']
        and _same(recovery[0].data, {'job_id': job.id, 'activation_id': activation['id'],
            'previous_status': saved['previous_status'], 'output_hash': digest(result)}))


def _simulation(protocol, frame, inputs):
    data = frame['sources']['data']; packet = data['packet']
    need(type(packet['simulation_only']) is bool)
    if protocol is not runtime:
        source_module = _protocol('business_goal_ready_v288')[1]
        from .workflow_goal_evaluation_contracts_v288 import RECORD
        record = data['evaluation_record']
        need(frame['sources']['schema'] == source_module.SCHEMA and record['schema'] == RECORD
            and packet == inputs['data_packet'] and record['evaluation_id'] == packet['id']
            and record['evidence_ref']['evidence_id'] == packet['evidence_id']
            and record['evidence_ref']['sha256'] == packet['sha256']
            and record['simulation_only'] is packet['simulation_only'])
    return packet['simulation_only']


def _display_fields(frame):
    original = frame['capability_pins']['fields']
    need(type(original) is list and len(original) <= 900)
    result = []; seen = set()
    for field in original:
        key = (field['entity_id'], field['field'])
        schema = field['schema']; title = schema.get('title')
        need(type(schema) is dict and key not in seen
            and field['value_type'] in {'number', 'integer', 'string', 'boolean'}
            and schema.get('type') == field['value_type']
            and (field['unit'] is None or type(field['unit']) is str))
        seen.add(key)
        if title is not None:
            need(type(title) is str and len(title) <= 400)
        # A missing title is not permission to consult today's mutable package.
        label = title if title is not None and title.strip() else field['field']
        need(type(label) is str and bool(label.strip()) and len(label) <= 400)
        result.append({'entity_id': field['entity_id'], 'field': field['field'],
            'label': label, 'value_type': field['value_type'], 'unit': field['unit']})
    for requirement in frame['decision_input']['requirements']:
        matches = [field for field in result if field['entity_id'] == requirement['entity_id']
            and field['field'] == requirement['field']]
        need(len(matches) == 1 and matches[0]['value_type'] == requirement['value_type']
            and matches[0]['unit'] == requirement['unit'])
    return result


def _fact_values(decision_input):
    facts = decision_input['facts']; need(type(facts) is list and len(facts) <= 128)
    result = []; seen = set()
    for fact in facts:
        need(fact['fact_ref'] not in seen and type(fact['value']) in {str, int, float, bool, type(None)})
        seen.add(fact['fact_ref'])
        # Python's original parsed JSON value is rendered on the server. In
        # particular an int above JS's safe range never travels through Number.
        text = json.dumps(fact['value'], ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        result.append({'fact_ref': fact['fact_ref'], 'text': text})
    return result


@checked
def context(db, p, task_id, activation_id):
    """Read the exact successful reasoning activation; never choose by equality."""
    require(p, 'admin', 'operator', 'viewer')
    need(type(task_id) is str and type(activation_id) is str and 0 < len(activation_id) <= 200)
    with db.no_autoflush:
        sources._reader_iam(db, p)
        task, release, _business, _graph, state, activation, node = scope._frozen(
            db, p, task_id, activation_id)
        protocol, source_module = _protocol(node['execution']['adapter'])
        need(task.id == task_id and task.workspace_id == p.workspace_id
            and activation['id'] == activation_id and activation['status'] == 'completed'
            and node['kind'] == 'ability' and node['actor_id']
            and node['execution']['adapter'] == protocol.ADAPTER
            and node['execution']['agent_role'] == 'business'
            and release.id == task.data['instance_release_id'] and release.package_id == task.package_id)
        transport = task.data['graph_jobs'][activation_id]
        job = source_module.fresh(db, p, Job, transport['job_id'])
        call = source_module.fresh(db, p, Invocation, transport['invocation_id'])
        cap = source_module.fresh(db, p, Capability, node['execution']['capability_id'])
        original = scope._job_matches(task, job, state, activation, node)
        need(_same(original, transport) and call.status == 'succeeded'
            and (job.status == 'succeeded' or protocol is not runtime and job.status == 'running')
            and job.workspace_id == call.workspace_id == cap.workspace_id == p.workspace_id
            and job.data['actor_id'] == node['actor_id']
            and cap.id == call.capability_id and cap.kind == 'tool'
            and cap.content_hash == cap.validated_hash == digest(cap.data) == node['execution']['capability_hash']
            and cap.release_version == cap.data['version'] == node['execution']['release_version']
            and cap.data['execution']['adapter'] == protocol.ADAPTER
            and _same(cap.data['input_schema'], node['execution']['input_schema'])
            and _same(cap.data['output_schema'], node['execution']['output_schema']))
        inputs = job.data['inputs']; owner = transport.get('dispatch_ownership')
        authorization = transport.get('dispatch_authorization')
        need(type(owner) is dict and owner.get('job_id') == job.id
            and type(owner.get('lease_token')) is str and bool(owner['lease_token'])
            and type(authorization) is dict and authorization.get('job_id') == job.id
            and authorization.get('lease_token') == owner['lease_token']
            and authorization.get('actor_id') == job.data['actor_id']
            and authorization.get('epoch') == activation['epoch']
            and authorization.get('input_hash') == digest(inputs)
            and authorization.get('capability_hash') == cap.content_hash
            and transport.get('phase') == 'succeeded' and transport.get('dispatch_committed') is True)
        graph.check_invocation(call, p, cap, node, inputs, job, owner)
        protocol._manifest_shape(cap.data)
        if job.status == 'running':
            raise HTTPException(409, PENDING)
        # Each actual protocol's saved_result authenticates its original SDK,
        # captured bytes, frozen source family and its own checked_frame. No
        # fresh build_frame/resolve or source-family substitution happens here.
        result = protocol.saved_result(db, p, task, job, call, cap, inputs)
        need(_same(result, call.data['output']) and _same(result, activation['output'])
            and transport.get('output_hash') == digest(result))
        _native_audits(db, p, task, job, activation, call, inputs)
        _successful_protocol_call(db, p, protocol, call, job, activation, result)
        saved = protocol._checked_state_for_actor(db, p, cap, inputs, call.idempotency_key,
            call, actor_id=job.data['actor_id'])
        need(saved['capture'] is not None and saved['call_id'] is not None
            and protocol._cloud_call(db, p, saved['call_id']).status == 'succeeded')
        frozen = saved['request']; frame = frozen['frame']; decision_input = frame['decision_input']
        need(frozen['frame_hash'] == digest(frame)
            and result['schema'] == protocol.RESULT and result['input_hash'] == protocol.decision.input_hash(decision_input)
            and result['scope']['task_id'] == decision_input['scope']['task_id'] == task_id
            and result['scope']['activation_id'] == decision_input['scope']['activation_id'] == activation_id
            and _same(result['scope'], decision_input['scope']))
        need(_same(protocol.decision.check_result(decision_input, result), result))
        simulation = _simulation(protocol, frame, inputs)
        # The complete Frame is used for authentication above and stays local.
        response = {'schema': SCHEMA, 'task_id': task.id, 'activation_id': activation_id,
            'invocation_id': call.id, 'input_hash': result['input_hash'], 'result': copy.deepcopy(result),
            'decision_input': copy.deepcopy(decision_input), 'fields': _display_fields(frame),
            'fact_values': _fact_values(decision_input),
            'simulation_only': simulation}
        _bytes(response, MAX_BYTES)
        return response


@router.get('/{task_id}/decision-review-context-v284')
def get_context(task_id: UUID, p: P, db: DB,
                activation_id: str = Query(min_length=1, max_length=200)):
    return context(db, p, str(task_id), activation_id)
