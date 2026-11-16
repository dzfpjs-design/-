"""Independent local evaluation query produced by an actual append command.

The old private clock is only a transient bounded-read capability. A command
producer has its own immutable authority and never impersonates a graph node.
"""
import copy
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select

from . import goal_evaluation_query_v288 as q
from . import workflow_goal_evaluation_contracts_v299 as contract
from .contracts import DataRequest, digest
from .models import Audit, Capability, DataRun, Invocation, Job
from .services import audit
from .temporal import instant, resolve
from .typed_payload_codec_v131 import canonical_json

KEY = q.KEY
SCHEMA = 'goal-evaluation-query-provenance-v299'
SAVED = 'workflow.goal_evaluation.query_saved_v299'
CONTROLS = q.CONTROLS
need = q.need


class Creator(contract.ProducerPin):
    request_key: q.contract.Text
    command_ref: contract.CommandRef


class Marker(q.contract.Closed):
    schema: Literal['goal-evaluation-query-provenance-v299']
    producer_family: Literal['append_command_v297']
    command_ref: contract.CommandRef
    evaluation_id: q.contract.ID
    request_id: q.contract.ID
    request_key: q.contract.Text
    scope: q.contract.EvaluationScope
    creator: Creator
    goal_snapshot: q.contract.GoalSnapshot
    preparation_policy: q.contract.PreparationPolicy
    derived_window: dict
    cut_at: q.contract.Timestamp
    cut_checked_at: q.contract.Timestamp
    controls: q.contract.QueryControls
    original_query_ref: q.contract.QueryRef
    original_query_snapshot: dict
    marker_hash: q.contract.Hash


def _same(left, right): return q.hashed(left) == q.hashed(right)


def _scope(current):
    task, selected = current['task'], current['selected']; release, binding = selected['release'], selected['binding']
    return {'workspace_id': task.workspace_id, 'task_id': task.id,
        'instance_id': release.instance_id, 'release_id': release.id, 'package_id': release.package_id,
        'business_id': task.data['business_id'], 'graph_hash': digest(binding['graph']),
        'source_binding_hash': binding['binding_hash'], 'goal_snapshot_hash': q.hashed(q.snapshot(task)),
        'native_goal_context_hash': task.data['native_goal_context_hash']}


def _authority(db, p, current, call, *, live):
    from . import workflow_goal_evaluation_append_v299 as producer
    actual_job = q._fresh(db, p, Job, current['job'].id)
    actual_call = q._fresh(db, p, Invocation, call.id)
    cap = q._fresh(db, p, Capability, actual_call.capability_id)
    checked = producer.runner_authority(db, p, actual_job, cap=cap, inputs=actual_call.data['inputs'],
        key=actual_call.idempotency_key, approval=actual_call.data['approval'], call=actual_call, require_lease=live)
    need(checked['task'].id == current['task'].id and checked['job'].id == current['job'].id
        and checked['command'].id == current['command'].id
        and _same(checked['record'], current['record']) and checked['cap'].id == cap.id)
    if live:
        validated = producer.validate_call_approval(db, p, cap, 'data', actual_call.data['inputs'],
            actual_call.idempotency_key, actual_call.data['approval'])
        need(validated['task'].id == checked['task'].id and validated['job'].id == actual_job.id
            and validated['command'].id == checked['command'].id and _same(validated['record'], checked['record'])
            and actual_call.status == 'running')
    else:
        need(actual_call.status in {'running', 'unknown', 'succeeded'})
    record = checked['record']; task = checked['task']; scope = _scope(checked)
    need(actual_job.workspace_id == actual_call.workspace_id == task.workspace_id == p.workspace_id
        and actual_call.data['job_id'] == actual_job.id and actual_call.data['role'] == 'data'
        and actual_call.data['side_effect'] is True
        and actual_call.data['capability_hash'] == cap.content_hash == cap.validated_hash == digest(cap.data)
        and _same(actual_call.data['capability_snapshot'], cap.data)
        and _same(actual_call.data['inputs'], record['inputs'])
        and actual_call.data['input_hash'] == actual_job.data['input_hash'] == record['input_hash']
        == digest(actual_call.data['inputs'])
        and actual_call.idempotency_key == actual_job.idempotency_key == record['invocation_key']
        and bool(actual_call.data['lease_token']))
    if live:
        from .leases import fenced, ownership
        need(fenced(db) and ownership(db) == {'job_id': actual_job.id, 'lease_token': actual_call.data['lease_token']}
            and actual_job.lease_token == actual_call.data['lease_token'] and actual_job.status == 'running')
    command_ref = {'id': checked['command'].id, 'record_hash': record['record_hash']}
    creator = {'workspace_id': p.workspace_id, 'task_id': task.id, 'scope_hash': q.hashed(scope),
        'invocation_id': actual_call.id, 'job_id': actual_job.id, 'actor_id': record['actor_id'],
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'input_hash': record['input_hash'],
        'request_key_hash': q.hashed(actual_call.idempotency_key), 'lease_token': actual_call.data['lease_token'],
        'request_key': actual_call.idempotency_key, 'command_ref': command_ref}
    Creator.model_validate(creator)
    return checked, actual_call, scope, creator


def _q0(db, p, current):
    task = current['task']; frozen = q.require_supported_goal(task)
    parent = q._fresh(db, p, DataRun, task.data['native_goal_query_id']); data = parent.data
    goal = frozen['spec']; request = data['request']
    need(data['native_goal_task_id'] == task.id
        and data['native_goal_request_hash'] == digest(request)
        and data['native_goal_result_hash'] == digest(data['result'])
        and data['native_goal_context_hash'] == task.data['native_goal_context_hash']
        and _same(data['goal_delegation'], {'task_id': task.id, 'goal_snapshot': frozen})
        and request.get('mode') == 'structured' and request.get('model_profile_id') is None
        and request['tool_steps'] == [] and request['allow_external_tools'] is False
        and request['entity_ids'] == goal['entity_ids'] and request['fields'] == goal['required_fields']
        and _same(request['evidence_requirement'], goal['evidence']) and request.get('relation_query') is None
        and _same(q.query_ref(parent), current['record']['original_query_ref']))
    return parent, frozen


def _events(db, p, *, query_id=None, evaluation_id=None):
    statement = select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_([q.SAVED, SAVED]))
    statement = statement.where(Audit.object_id == query_id) if query_id is not None else statement.where(
        Audit.data['evaluation_id'].as_string() == evaluation_id)
    return list(db.scalars(statement.limit(2).execution_options(populate_existing=True)))


def _request(request, marker):
    derived = {'resolved_time', 'reference_at', 'clarifications'}
    need(type(request) is dict and set(request) == set(DataRequest.model_fields) | derived)
    base = {key: value for key, value in request.items() if key not in derived}
    need(_same(DataRequest.model_validate(base).model_dump(mode='json'), base)
        and request['mode'] == 'structured' and request['model_profile_id'] is None
        and request['tool_steps'] == [] and request['allow_external_tools'] is False
        and request['relation_query'] is None and request['clarifications'] == []
        and request['reference_at'] == marker['cut_at'] and _same(request['resolved_time'], marker['derived_window']))
    goal = marker['goal_snapshot']['spec']; policy = marker['preparation_policy']
    need(request['question'] == goal['purpose'] and request['entity_ids'] == goal['entity_ids']
        and request['fields'] == goal['required_fields'] and _same(request['evidence_requirement'], goal['evidence'])
        and request['business_timezone'] == policy['timezone'])


@q.checked
def checked_frozen(db, p, task, query_id, *, evaluation_id):
    from .goal_evaluation_append_models_v297 import GoalEvaluationAppendCommandV297 as Command
    with db.no_autoflush:
        q._reader(db, p); row = q._fresh(db, p, DataRun, query_id)
        raw = row.data[KEY]; marker = q.contract._parse(Marker, raw)
        need(_same(marker, raw) and marker['controls']['cached_only'] is True
            and all(raw['controls'][key] is False for key in ('emit_gaps', 'learning', 'include_trusted_cases'))
            and raw['controls']['cached_only'] is True)
        call = q._fresh(db, p, Invocation, q._uuid(evaluation_id))
        job = q._fresh(db, p, Job, marker['creator']['job_id'])
        command = q._fresh(db, p, Command, marker['command_ref']['id'])
        current = {'task': task, 'job': job, 'command': command, 'record': command.data}
        checked, call, scope, creator = _authority(db, p, current, call, live=False)
        parent, frozen_goal = _q0(db, p, checked)
        policy = contract.check_preparation_policy(task.data['goal_spec'], checked['selected']['binding']['preparation_policy'])
        need(marker['evaluation_id'] == call.id and marker['request_id'] == job.id
            and marker['producer_family'] == contract.PRODUCER_FAMILY and marker['command_ref'] == creator['command_ref']
            and marker['request_key'] == creator['request_key'] and _same(marker['creator'], creator)
            and _same(marker['scope'], scope) and _same(marker['goal_snapshot'], frozen_goal)
            and _same(marker['preparation_policy'], policy) and _same(marker['original_query_ref'], q.query_ref(parent))
            and _same(marker['original_query_snapshot'], parent.data)
            and marker['marker_hash'] == q.hashed({key: value for key, value in raw.items() if key != 'marker_hash'})
            and row.owner_id == creator['actor_id'] and row.version == 1 and row.question == frozen_goal['spec']['purpose'])
        _request(row.data['request'], marker)
        initial = frozen_goal['time']; cut = instant(marker['cut_at'])
        window = contract.derive_evaluation_window(frozen_goal['spec'], policy, cut_at=marker['cut_at'],
            trusted_now=marker['cut_checked_at'], **({'initial_window': initial} if policy['time_basis'] == 'fixed_window' else {}))
        need(instant(initial['reference_at']) <= cut and _same(initial, resolve(frozen_goal['spec']['evidence']['time'],
            reference=instant(initial['reference_at']))) and _same(window, marker['derived_window'])
            and instant(checked['record']['accepted_at']) <= call.created_at <= cut
            and (not frozen_goal['spec'].get('deadline') or cut < instant(frozen_goal['spec']['deadline'])))
        state = row.data['result']
        need(_same(state['request'], row.data['request']) and row.status == state['status'] == state['result']['status']
            and _same(state['result']['resolved_time'], window) and state['result']['evaluated_at'] == marker['cut_at']
            and _same(state['result']['evidence_requirement'], frozen_goal['spec']['evidence']))
        events = _events(db, p, query_id=row.id)
        need(len(events) == 1 and events[0].action == SAVED and events[0].actor_id == creator['actor_id']
            and events[0].data == {'schema': SCHEMA, 'producer_family': contract.PRODUCER_FAMILY,
                'command_ref': marker['command_ref'], 'evaluation_id': call.id, 'job_id': job.id,
                'marker_hash': marker['marker_hash'], 'query_ref': q.query_ref(row)})
        return {'query_ref': q.query_ref(row), 'request': copy.deepcopy(row.data['request']),
            'result': copy.deepcopy(state), 'provenance': copy.deepcopy(marker)}


@q.checked
def create_frozen(db, p, current, call, *, cut_at):
    q._reader(db, p)
    current, call, scope, creator = _authority(db, p, current, call, live=True)
    parent, frozen_goal = _q0(db, p, current); task, job = current['task'], current['job']
    policy = contract.check_preparation_policy(task.data['goal_spec'], current['selected']['binding']['preparation_policy'])
    trusted = db.scalar(select(func.clock_timestamp()))
    need(isinstance(trusted, datetime) and trusted.tzinfo is not None)
    cut = cut_at if isinstance(cut_at, datetime) else instant(cut_at)
    need(cut.tzinfo is not None and instant(current['record']['accepted_at']) <= call.created_at <= cut <= trusted)
    cut_at = cut.isoformat(); initial = frozen_goal['time']
    need(instant(initial['reference_at']) <= cut)
    window = contract.derive_evaluation_window(frozen_goal['spec'], policy, cut_at=cut_at,
        trusted_now=trusted.isoformat(), **({'initial_window': initial} if policy['time_basis'] == 'fixed_window' else {}))
    if frozen_goal['spec'].get('deadline'): need(cut < instant(frozen_goal['spec']['deadline']))
    rows = list(db.scalars(select(DataRun).where(DataRun.workspace_id == p.workspace_id,
        DataRun.data[KEY]['evaluation_id'].as_string() == call.id).limit(2)))
    events = _events(db, p, evaluation_id=call.id)
    need(len(rows) <= 1 and len(events) <= 1)
    if rows:
        saved = checked_frozen(db, p, task, rows[0].id, evaluation_id=call.id)
        need(saved['provenance']['cut_at'] == cut_at and _same(saved['provenance']['creator'], creator)
            and len(events) == 1)
        return rows[0]
    need(not events)
    from .data_requirements import prepare_request
    goal = frozen_goal['spec']
    request = prepare_request(db, p, DataRequest(question=goal['purpose'], entity_ids=goal['entity_ids'],
        fields=goal['required_fields'], mode='structured', model_profile_id=None, business_timezone=policy['timezone'],
        evidence_requirement=goal['evidence'], relation_query=None, tool_steps=[], allow_external_tools=False
        ).model_dump(mode='json'), reference=cut, _confirmed_plan=True, task=task)
    need(request['clarifications'] == []); request['resolved_time'] = copy.deepcopy(window)
    marker = {'schema': SCHEMA, 'producer_family': contract.PRODUCER_FAMILY, 'command_ref': creator['command_ref'],
        'evaluation_id': call.id, 'request_id': job.id, 'request_key': creator['request_key'], 'scope': scope,
        'creator': creator, 'goal_snapshot': frozen_goal, 'preparation_policy': policy, 'derived_window': window,
        'cut_at': cut_at, 'cut_checked_at': trusted.isoformat(), 'controls': copy.deepcopy(CONTROLS),
        'original_query_ref': q.query_ref(parent), 'original_query_snapshot': copy.deepcopy(parent.data)}
    marker['marker_hash'] = q.hashed(marker); q.contract._parse(Marker, marker); _request(request, marker)
    row = DataRun(workspace_id=p.workspace_id, owner_id=creator['actor_id'], question=goal['purpose'],
        status='queued', version=1, data={'request': copy.deepcopy(request), KEY: marker})
    db.add(row); db.flush()
    clock = q._Clock(q._WITNESS, db, p.workspace_id, p.user_id, job.id, creator['lease_token'], row.id, task.id,
        q.hashed(request), tuple(request['entity_ids']), tuple(request['fields']),
        q.hashed(request['evidence_requirement']), canonical_json(window), cut)
    from .agents import data_agent
    state = data_agent(db, p, request, row.id, **CONTROLS, task=task, _goal_evaluation_cut=clock)
    need(_same(state['request'], request) and state['status'] == state['result']['status']
        and _same(state['result']['resolved_time'], window) and state['result']['evaluated_at'] == cut_at
        and _same(state['result']['evidence_requirement'], goal['evidence']))
    row.status = state['status']; row.data = {**row.data, 'result': copy.deepcopy(state)}
    audit(db, p, SAVED, row.id, {'schema': SCHEMA, 'producer_family': contract.PRODUCER_FAMILY,
        'command_ref': creator['command_ref'], 'evaluation_id': call.id, 'job_id': job.id,
        'marker_hash': marker['marker_hash'], 'query_ref': q.query_ref(row)})
    db.flush()  # Complete result + independent SAVED before no_autoflush reload.
    return row


@q.checked
def visible_reference(db, p, row, events, evaluations):
    """Restricted DTO only; never an append execution or completion grant."""
    from .workspace_api import saved_execution
    need(len(events) == 1 and len(evaluations) <= 1 and events[0].action == SAVED)
    anchor = events[0].data
    need(anchor['schema'] == SCHEMA and anchor['query_ref']['query_id'] == row.id)
    job = q._fresh(db, p, Job, anchor['job_id'])
    task, release, _business = saved_execution(db, p, job.data['task_id'])
    frozen = checked_frozen(db, p, task, row.id, evaluation_id=anchor['evaluation_id'])
    marker = frozen['provenance']; creator = marker['creator']
    need(events[0].actor_id == row.owner_id == creator['actor_id']
        and _same(anchor['query_ref'], frozen['query_ref']))
    if evaluations:
        evaluation = evaluations[0]
        need(evaluation.id == evaluation.invocation_id == marker['evaluation_id']
            and evaluation.evaluation_query_id == row.id and evaluation.task_id == task.id
            and evaluation.job_id == creator['job_id'] and evaluation.actor_id == creator['actor_id']
            and evaluation.workspace_id == p.workspace_id and evaluation.release_id == release.id
            and evaluation.instance_id == release.instance_id and evaluation.package_id == release.package_id
            and evaluation.data['schema'] == contract.RECORD
            and evaluation.data['producer_family'] == contract.PRODUCER_FAMILY
            and _same(evaluation.data['command_ref'], marker['command_ref'])
            and _same(evaluation.data['evaluation_query_ref'], frozen['query_ref'])
            and _same(evaluation.data['scope'], marker['scope']))
    return {'schema': 'data-query-reference-v144', 'id': row.id, 'version': row.version, 'status': row.status,
        'question': row.question, 'created_at': row.created_at.isoformat(), 'reference_only': True,
        'family': 'goal_evaluation_v299', 'context': {'instance_id': release.instance_id, 'task_id': task.id,
            'detail_path': '/api/workspace/tasks/' + task.id},
        'notice': '这是原任务追加的独立资料核对快照；此入口不表示生产已完成、收件完整或目标已达成，请回原任务查看。'}
