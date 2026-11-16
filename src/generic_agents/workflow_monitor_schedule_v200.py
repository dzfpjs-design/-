"""Approved-policy check cadence and priority, consumed by the actual worker.

No hardware sample rate, drone route or camera PTZ is changed. Installation is
pending until an actual due policy-check cycle is durably recorded. The original
MonitorPolicy and its full approval, budgets and acquisition receipts stay intact.
"""
import copy
import base64
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from . import business_reasoning_v197 as reasoning
from . import business_flow_review_store as safe
from . import full_semantic_graph_v1 as kernel
from . import workflow_service_v195 as shared
from .contracts import Manifest, digest
from .models import Audit, Capability, DomainInstance, Entity, Invocation, Job, MonitorPolicy, Task, uid
from .services import audit, published, scoped, validate
from .workflow_monitor_schedule_models_v200 import (
    WorkflowMonitorScheduleHeadV200 as Head,
    WorkflowMonitorScheduleInstallationV200 as Installation,
    WorkflowMonitorScheduleCycleV200 as Cycle,
    WorkflowMonitorScheduleStopCommandV200 as StopCommand)

OP = 'adjust_monitoring_schedule'
RELEASE_VERSION = '1.0.0'
HEAD = 'workflow-monitor-schedule-head-v200'
INSTALL = 'workflow-monitor-schedule-installation-v200'
CYCLE = 'workflow-monitor-schedule-cycle-v200'
RECEIPT = 'workflow-monitor-schedule-receipt-v200'
CALL_KEY = 'workflow_monitor_schedule_v200'
INSTALLED = 'workflow.monitor_schedule.installed_v200'
CONSUMED = 'workflow.monitor_schedule.consumed_v200'
STATE = 'workflow.monitor_schedule.state_v200'
STOPPED = 'workflow.monitor_schedule.stopped_v200'
STOP = 'workflow-monitor-schedule-stop-command-v200'
STOP_RESULT = 'workflow-monitor-schedule-stop-result-v200'
MAX_RECORDS = 512
MAX_BYTES = 2 * 1024 * 1024
ERROR = '原监测检查调度、风险研判来源、策略权限或独立审计无法核对；未重建、扩大授权或改设备频率'
_ANY_HEAD = object()
INSTALL_KEYS = {'schema', 'id', 'head_id', 'workspace_id', 'policy_id', 'parent_task_id', 'invocation_id',
    'job_id', 'actor_id', 'capability_id', 'request_key', 'input_hash', 'inputs', 'approval', 'producer',
    'selected_profile', 'created_at', 'policy_hash', 'source', 'capacity', 'record_hash'}
CYCLE_KEYS = {'schema', 'id', 'head_id', 'installation_id', 'workspace_id', 'policy_id', 'head_version',
    'due_at', 'checked_at', 'outcome', 'ordered_entity_ids', 'priority_entity_ids', 'acquisition_ids',
    'reason', 'previous_head_hash', 'record_hash'}


def need(value, message=ERROR, status=409):
    if not value: raise HTTPException(status, message)


def _same(a, b): return safe._same(a, b)
def _seal(value): return {**copy.deepcopy(value), 'record_hash': digest(value)}
def _time(value):
    need(type(value) is str and 'T' in value and len(value) <= 80, status=422)
    try: result = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    except (ValueError, OverflowError): raise HTTPException(422, '监测检查期限需要完整带时区时间') from None
    need(result.tzinfo is not None and result.utcoffset() is not None, status=422)
    return result


def input_schema(operation=OP):
    need(operation == OP, status=422)
    ident = {'type': 'string', 'pattern': shared.UUID_PATTERN}
    pin = shared._object({'policy_id': ident, 'approved_hash': {'type': 'string', 'pattern': shared.HASH_PATTERN}})
    profile = shared._object({'candidate_ref': {'type': 'string', 'minLength': 1, 'maxLength': 80},
        'interval_seconds': {'type': 'integer', 'minimum': 60, 'maximum': 86400},
        'priority_entity_ids': {'type': 'array', 'minItems': 0, 'maxItems': 100, 'uniqueItems': True, 'items': ident},
        'valid_until': {'type': 'string', 'format': 'date-time', 'maxLength': 80}})
    schedule_pin = shared._object({'installation_id': ident,
        'record_hash': {'type': 'string', 'pattern': shared.HASH_PATTERN}})
    return shared._object({'reasoning_result': reasoning.output_schema(), 'policy_pin': pin,
        'expected_schedule_pin': {**schedule_pin, 'type': ['object', 'null']},
        'profiles': {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': profile}})


def output_schema(operation=OP):
    need(operation == OP, status=422)
    ident = {'type': 'string', 'pattern': shared.UUID_PATTERN}
    receipt = shared._object({'schema': {'const': RECEIPT}, 'operation': {'const': OP},
        'installation_id': ident, 'policy_id': ident, 'cycle_id': ident,
        'candidate_ref': {'type': 'string', 'minLength': 1, 'maxLength': 80},
        'interval_seconds': {'type': 'integer', 'minimum': 60, 'maximum': 86400},
        'priority_entity_ids': {'type': 'array', 'items': ident},
        'checked_at': {'type': 'string', 'format': 'date-time'},
        'status': {'const': 'check_consumed'}, 'cycle_hash': {'type': 'string', 'pattern': shared.HASH_PATTERN},
        'simulation_only': {'type': 'boolean'}, 'hardware_sampling_rate_changed': {'const': False},
        'device_execution_completed': {'const': False}, 'independent_acceptance': {'const': False}})
    return shared._object({'result': {'type': 'string', 'minLength': 1, 'maxLength': 1000}, 'service_receipt': receipt})


def _inputs(value):
    safe._bytes(value, MAX_BYTES); validate(input_schema(), value)
    need(type(value) is dict and set(value) == {'reasoning_result', 'policy_pin', 'profiles', 'expected_schedule_pin'}, status=422)
    refs = []
    for profile in value['profiles']:
        need(type(profile['interval_seconds']) is int and 60 <= profile['interval_seconds'] <= 86400
            and type(profile['priority_entity_ids']) is list
            and len(set(profile['priority_entity_ids'])) == len(profile['priority_entity_ids']), status=422)
        for identity in profile['priority_entity_ids']: safe.identity(identity)
        _time(profile['valid_until']); refs.append(profile['candidate_ref'])
    safe.identity(value['policy_pin']['policy_id'])
    need(len(refs) == len(set(refs)), '候选方案必须唯一，不按名称合并或自动选择第一项', 422)
    return copy.deepcopy(value)


def schedule_pin(installation):
    """Stable configuration CAS; periodic check cycles never change this pin."""
    value = installation.data if isinstance(installation, Installation) else installation
    return {'installation_id': value['id'], 'record_hash': value['record_hash']}


def _reserve_cycles(value, at, used):
    """Reserve all possible immediate checks plus an explicit terminal cycle."""
    need(type(used) is int and 0 <= used < MAX_RECORDS)
    counts = []
    for profile in value['profiles']:
        delta = _time(profile['valid_until']) - at
        micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        need(micros > 0, '检查调度期限已到，未安装或延长')
        quantum = profile['interval_seconds'] * 1000000
        counts.append((micros + quantum - 1) // quantum + 1)
    needed = max(counts)
    need(used + needed <= MAX_RECORDS,
        '此调度的检查次数与终结回执超出完整历史容量，请缩短期限或增大间隔；未改原调度')
    return {'existing_cycles': used, 'maximum_new_cycles': needed, 'terminal_slot_reserved': True}


def validate_manifest(manifest):
    data = manifest.model_dump(mode='json') if isinstance(manifest, Manifest) else copy.deepcopy(manifest)
    Manifest.model_validate(data)
    need(data.get('kind') == 'tool' and data.get('version') == RELEASE_VERSION
        and data.get('allowed_agent_roles') == ['business']
        and _same(data.get('execution'), {'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': OP, 'side_effect': True}) and data['execution']['side_effect'] is True
        and _same(data.get('input_schema'), input_schema()) and _same(data.get('output_schema'), output_schema())
        and data.get('dependencies') == [] and data.get('data_policy', {}).get('cloud_allowed') is False
        and type(data.get('budget', {}).get('max_attempts')) is int and data['budget']['max_attempts'] == 1,
        '检查调度须以真实有副作用本地服务登记；不改变硬件采样频率', 422)
    return data


make_approval = shared.make_approval


def validate_call_approval(db, p, cap, role, inputs, key, approval):
    return shared.validate_call_approval(db, p, cap, role, inputs, key, approval, _variant=sys.modules[__name__])


def _lock(db, p, policy_id):
    safe._lock(db, {'workflow_monitor_schedule_v200': p.workspace_id, 'policy_id': policy_id})


def _rows(db, model, p, policy_id):
    rows = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id, model.policy_id == policy_id)
        .order_by(model.id).limit(MAX_RECORDS + 1).execution_options(populate_existing=True)))
    need(len(rows) <= MAX_RECORDS, '监测检查调度历史达到完整核对容量；未截断或忽略旧记录')
    return rows


def _anchor(kind, value):
    return {'schema': kind, 'policy_id': value['policy_id'], 'id': value['id'],
        'record_hash': value['record_hash'], 'record': copy.deepcopy(value),
        **({'request_id': value['request_id']} if kind == STOP else {})}


def _state_anchor(value, previous):
    return {'schema': HEAD, 'policy_id': value['policy_id'], 'id': value['id'], 'version': value['version'],
        'previous_hash': previous, 'record_hash': value['record_hash'], 'record': copy.deepcopy(value)}


def _stop_body(body, raw):
    need(type(body) is dict and set(body) == {'request_id', 'expected_schedule_pin', 'reason', 'restore_legacy'}, status=422)
    safe.identity(body['request_id'])
    pin = body['expected_schedule_pin']
    need(type(pin) is dict and set(pin) == {'installation_id', 'record_hash'}
        and type(pin['record_hash']) is str and safe.SHA.fullmatch(pin['record_hash']) is not None
        and type(body['restore_legacy']) is bool and type(body['reason']) is str
        and bool(body['reason'].strip()) and len(body['reason']) <= 2000, status=422)
    safe.identity(pin['installation_id'])
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, status=422)
    def pairs(items):
        result = {}
        for key, item in items:
            need(key not in result, '停止请求含重复字段，原请求未应用', 422); result[key] = item
        return result
    try: decoded = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError): raise HTTPException(422, '停止请求原字节不是完整安全JSON') from None
    need(_same(decoded, body), '停止请求原字节与实际命令不一致', 422)
    return copy.deepcopy(body), hashlib.sha256(raw).hexdigest()


def _checked_stop(row, p, head):
    value = row.data; safe._bytes(value, MAX_BYTES)
    need(type(value) is dict and set(value) == {'schema', 'id', 'workspace_id', 'policy_id', 'head_id',
        'actor_id', 'request_id', 'body_sha256', 'raw_base64', 'path', 'body', 'previous_head_hash',
        'head_version', 'result', 'record_hash'} and value['schema'] == STOP
        and value['record_hash'] == row.record_hash == digest({k: v for k, v in value.items() if k != 'record_hash'})
        and row.version == 1 and all(value[k] == getattr(row, k) for k in
            ('id', 'workspace_id', 'policy_id', 'head_id', 'actor_id', 'request_id', 'body_sha256'))
        and row.workspace_id == p.workspace_id and row.head_id == head.id
        and type(value['path']) is str and value['path'].startswith('/') and len(value['path']) <= 1024
        and type(value['head_version']) is int and value['head_version'] >= 2)
    try: raw = base64.b64decode(value['raw_base64'], validate=True)
    except (ValueError, TypeError): raise HTTPException(409, ERROR) from None
    body, sha = _stop_body(value['body'], raw)
    need(body['request_id'] == row.request_id and sha == row.body_sha256)
    result = value['result']
    need(type(result) is dict and set(result) == {'schema', 'request_id', 'policy_id', 'installation_id',
        'record_hash', 'version', 'status', 'restore_legacy', 'device_execution_cancelled'}
        and result == {'schema': STOP_RESULT, 'request_id': row.request_id, 'policy_id': row.policy_id,
            **body['expected_schedule_pin'], 'version': value['head_version'], 'status': 'cancelled',
            'restore_legacy': body['restore_legacy'], 'device_execution_cancelled': False})
    return value


def _checked(db, p, policy_id):
    heads = _rows(db, Head, p, policy_id); installs = _rows(db, Installation, p, policy_id); cycles = _rows(db, Cycle, p, policy_id)
    stops = _rows(db, StopCommand, p, policy_id)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_([INSTALLED, CONSUMED, STATE, STOPPED]), Audit.data['policy_id'].as_string() == policy_id)))
    need(len(heads) <= 1)
    if not heads:
        need(not installs and not cycles and not stops and not events, '原调度审计仍在但主记录缺失，不能降级为旧监测或重建')
        return None, [], []
    head = heads[0]; value = head.data; safe._bytes(value, MAX_BYTES)
    need(type(value) is dict and set(value) == {'schema', 'id', 'workspace_id', 'policy_id', 'version', 'status',
        'installation_ids', 'cycle_ids', 'stop_command_ids', 'restore_legacy', 'active_installation_id', 'next_due', 'record_hash'}
        and value['schema'] == HEAD and value['id'] == head.id and value['workspace_id'] == p.workspace_id
        and value['policy_id'] == policy_id and type(value['version']) is int and value['version'] == head.version
        and value['status'] == head.status and head.status in {'active', 'expired', 'blocked', 'cancelled'}
        and value['record_hash'] == head.record_hash == digest({k: v for k, v in value.items() if k != 'record_hash'})
        and value['next_due'] == (head.next_due.isoformat() if head.next_due else None)
        and set(value['installation_ids']) == {v.id for v in installs} and len(value['installation_ids']) == len(installs)
        and set(value['cycle_ids']) == {v.id for v in cycles} and len(value['cycle_ids']) == len(cycles)
        and set(value['stop_command_ids']) == {v.id for v in stops} and len(value['stop_command_ids']) == len(stops)
        and type(value['restore_legacy']) is bool
        and (value['restore_legacy'] is False or head.status == 'cancelled')
        and value['active_installation_id'] in value['installation_ids'])
    used = set()
    for model, rows, kind, action in ((Installation, installs, INSTALL, INSTALLED), (Cycle, cycles, CYCLE, CONSUMED)):
        for row in rows:
            data = row.data; safe._bytes(data, MAX_BYTES)
            need(type(data) is dict and data.get('schema') == kind and data.get('id') == row.id
                and data.get('workspace_id') == p.workspace_id and data.get('policy_id') == policy_id
                and data.get('head_id') == head.id == row.head_id and row.version == 1
                and data.get('record_hash') == row.record_hash == digest({k: v for k, v in data.items() if k != 'record_hash'}))
            if model is Installation:
                need(set(data) == INSTALL_KEYS and all(data.get(k) == getattr(row, k) for k in ('parent_task_id', 'invocation_id', 'job_id', 'actor_id', 'capability_id', 'request_key', 'input_hash'))
                    and digest(data['inputs']) == row.input_hash); _inputs(data['inputs'])
                need(_same(data['capacity'], _reserve_cycles(data['inputs'], _time(data['created_at']), data['capacity']['existing_cycles'])))
            else:
                need(set(data) == CYCLE_KEYS and data.get('installation_id') == row.installation_id and row.installation_id in value['installation_ids']
                    and data.get('head_version') == row.head_version and data.get('outcome') == row.outcome
                    and row.outcome in {'checked', 'expired', 'blocked', 'cancelled'})
            matches = [v for v in events if v.action == action and v.object_id == row.id]
            need(len(matches) == 1 and _same(matches[0].data, _anchor(kind, data))); used.add(matches[0].id)
    by_install = {row.id: row.data for row in installs}; by_stop = {}
    for row in stops:
        data = _checked_stop(row, p, head); by_stop[row.id] = data
        matches = [event for event in events if event.action == STOPPED and event.object_id == row.id]
        need(len(matches) == 1 and matches[0].actor_id == row.actor_id
            and _same(matches[0].data, _anchor(STOP, data))); used.add(matches[0].id)
    states = sorted([v for v in events if v.action == STATE and v.object_id == head.id], key=lambda v: v.data.get('version', 0))
    need(len(states) == head.version); previous = None; previous_state = None
    for number, event in enumerate(states, 1):
        state = event.data.get('record'); need(type(state) is dict and set(state) == set(value)
            and all(state.get(k) == value[k] for k in ('schema', 'id', 'workspace_id', 'policy_id'))
            and state.get('version') == number and state['active_installation_id'] == state['installation_ids'][-1]
            and state.get('record_hash') == digest({k: v for k, v in state.items() if k != 'record_hash'})
            and _same(event.data, _state_anchor(state, previous)))
        if previous_state is None:
            need(len(state['installation_ids']) == 1 and not state['cycle_ids'] and not state['stop_command_ids']
                and state['status'] == 'active' and state['restore_legacy'] is False
                and by_install[state['active_installation_id']]['inputs']['expected_schedule_pin'] is None
                and by_install[state['active_installation_id']]['capacity']['existing_cycles'] == 0)
        else:
            original_install = by_install[previous_state['active_installation_id']]
            replacement = by_install[state['active_installation_id']]
            installed = (state['installation_ids'][:-1] == previous_state['installation_ids']
                and len(state['installation_ids']) == len(previous_state['installation_ids']) + 1
                and state['cycle_ids'] == previous_state['cycle_ids'] and state['stop_command_ids'] == previous_state['stop_command_ids']
                and state['status'] == 'active' and state['restore_legacy'] is False
                and replacement['inputs']['expected_schedule_pin'] == schedule_pin(original_install)
                and replacement['capacity']['existing_cycles'] == len(previous_state['cycle_ids'])
                and any(row.installation_id == original_install['id'] and row.outcome == 'checked'
                    and row.id in previous_state['cycle_ids'] for row in cycles))
            consumed = (state['installation_ids'] == previous_state['installation_ids']
                and state['cycle_ids'][:-1] == previous_state['cycle_ids']
                and len(state['cycle_ids']) == len(previous_state['cycle_ids']) + 1
                and state['active_installation_id'] == previous_state['active_installation_id']
                and state['stop_command_ids'] == previous_state['stop_command_ids']
                and state['restore_legacy'] is False and previous_state['status'] == 'active')
            if consumed:
                cycle = next(row.data for row in cycles if row.id == state['cycle_ids'][-1])
                need(cycle['head_version'] == number and cycle['previous_head_hash'] == previous
                    and cycle['installation_id'] == original_install['id']
                    and cycle['due_at'] == previous_state['next_due']
                    and state['status'] == ('active' if cycle['outcome'] == 'checked' else cycle['outcome'])
                    and state['next_due'] == ((_time(cycle['checked_at']) + timedelta(
                        seconds=original_install['selected_profile']['interval_seconds'])).isoformat()
                        if cycle['outcome'] == 'checked' else None))
            stopped = (state['installation_ids'] == previous_state['installation_ids']
                and state['cycle_ids'] == previous_state['cycle_ids']
                and state['active_installation_id'] == previous_state['active_installation_id']
                and state['stop_command_ids'][:-1] == previous_state['stop_command_ids']
                and len(state['stop_command_ids']) == len(previous_state['stop_command_ids']) + 1
                and state['status'] == 'cancelled' and state['next_due'] is None)
            if stopped:
                command = by_stop[state['stop_command_ids'][-1]]
                need(command['previous_head_hash'] == previous and command['head_version'] == number
                    and command['body']['expected_schedule_pin'] == schedule_pin(original_install)
                    and command['body']['restore_legacy'] is state['restore_legacy'])
            need(installed or consumed or stopped)
        previous = state['record_hash']; previous_state = state; used.add(event.id)
    need(_same(previous_state, value) and used == {v.id for v in events} and len(used) == len(events))
    return head, installs, cycles


def _store_head(db, p, head, value, *, previous):
    sealed = _seal(value); head.version = sealed['version']; head.status = sealed['status']
    head.next_due = _time(sealed['next_due']) if sealed['next_due'] else None
    head.data = sealed; head.record_hash = sealed['record_hash']; audit(db, p, STATE, head.id, _state_anchor(sealed, previous))


def _producer(db, p, context, result):
    """Resolve the exact native input binding, never search for equal output."""
    from .workflow_graph_scope import _frozen, _job_matches
    task = context['task']; state = task.data['graph_runtime']; current = state['activations'][context['job'].data['activation_id']]
    binding = current['inputs_snapshot']['bindings'].get('reasoning_result')
    need(type(binding) is dict and binding.get('missing') is False and binding.get('producer_activation_id'))
    activation = state['activations'][binding['producer_activation_id']]
    need(activation['status'] == 'completed' and activation['epoch'] == current['epoch']
        and _same(activation['output'], result) and binding['value_hash'] == digest(result))
    transport = task.data['graph_jobs'][activation['id']]
    job = scoped(db, Job, transport['job_id'], p); call = scoped(db, Invocation, transport['invocation_id'], p)
    need(job.data.get('task_id') == task.id and call.status == 'succeeded'
        and call.data.get('job_id') == job.id and call.idempotency_key == job.data['invocation_key']
        and _same(call.data.get('output'), result))
    from .worker import actor as actual_actor
    actor = actual_actor(db, p.workspace_id, job.data['actor_id'])
    original_task, release, business, graph, original_state, original_activation, node = _frozen(db, actor, task.id, activation['id'])
    _job_matches(original_task, job, original_state, original_activation, node)
    cap = published(db, actor, call.capability_id)
    need(cap.data['execution'].get('adapter') == reasoning.ADAPTER and cap.content_hash == node['execution']['capability_hash']
        and call.data.get('capability_hash') == cap.content_hash and call.data.get('role') == 'business')
    frozen = reasoning._checked_state(db, actor, cap, job.data['inputs'], job.data['invocation_key'], call)
    need(frozen['capture'] is not None)
    import base64
    raw = base64.b64decode(frozen['capture']['raw_base64'], validate=True).decode('utf-8', errors='surrogateescape')
    model_value, _receipt = reasoning.transport.parse_response(raw, frozen['request']['model_value_schema'], frozen['capture']['receipt'])
    need(_same(reasoning.check_result(frozen['request'], model_value), result))
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'capability.invoke', Audit.object_id == call.id)))
    need(len(events) == 1 and events[0].actor_id == actor.user_id and _same(events[0].data, {'status': 'succeeded'}))
    if job.status == 'running':
        from .workflow_task_context_v196 import ProducerPending
        raise ProducerPending(job.id, call.id)
    need(job.status == 'succeeded')
    return {'job_id': job.id, 'invocation_id': call.id, 'activation_id': activation['id'],
        'actor_id': actor.user_id, 'request_key_hash': digest(call.idempotency_key),
        'capability_id': cap.id, 'capability_hash': cap.content_hash, 'input_hash': call.data['input_hash'],
        'output_hash': digest(result), 'capture_hash': frozen['capture']['raw_sha256'],
        'candidates': copy.deepcopy(frozen['request']['inputs']['candidates'])}


def producer_wait_pin(db, p, task, job, inputs):
    """Original producer proof for the pre-Invocation same-Job wait.

    The ready activation already has the kernel's checked input snapshot. This
    does not infer provenance from a marker carried inside the model result.
    ProducerPending is deliberately propagated, before any installation writes.
    """
    from .workflow_graph_scope import _frozen, _job_matches
    value = _inputs(inputs)
    actual, _release, _business, graph, state, activation, node = _frozen(
        db, p, task.id, job.data['activation_id'])
    _job_matches(actual, job, state, activation, node)
    need(actual.id == task.id and job.data['actor_id'] == p.user_id
        and _same(kernel.ability_inputs(graph, state, activation['id']), value)
        and _same(job.data['inputs'], value))
    return _producer(db, p, {'task': actual, 'job': job}, value['reasoning_result'])


def _approval_proof(db, p, task):
    """Historical approval chain plus its current explicit stop switch.

    Normal completion/independent acceptance is not revocation of an already
    installed bounded monitor schedule. The current MonitorPolicy authorizes
    each later check; no new Task execution is authorized by this proof.
    """
    from . import layered_approval_store_v175 as approvals
    row = approvals.managed(db, p, task)
    need(row is not None and row.status in {'executing', 'acceptance_returned', 'accepted'},
        '原安装任务缺少真实逐级批准和明确启动记录，或已撤回')
    value = approvals.checked(row)
    execution = value.get('execution')
    need(type(execution) is dict and execution.get('initialized') is True)
    approvals.policy_value(db, p, approvals.current_policy(db, p, row.policy_id))
    submission, _original, _spec = approvals.current_submission(db, p, row)
    need(execution['submission_hash'] == approvals.row_hash(db, p, row))
    return {'managed_id': row.id, 'submission_id': submission.id,
        'submission_hash': execution['submission_hash'], 'execution_hash': digest(execution)}


def _policy(db, p, context, inputs, at):
    from .perception import authorized_monitor
    policy = scoped(db, MonitorPolicy, inputs['policy_pin']['policy_id'], p, True)
    authorized_monitor(db, p, policy)
    task = context['task']; release = context['release']
    need(policy.data['approved_hash'] == inputs['policy_pin']['approved_hash']
        and set(policy.data['entity_ids']) <= set(task.data['entity_ids'])
        and task.data['instance_release_id'] == release.id and task.package_id == release.package_id)
    from .native_goals import guard
    goal = guard(db, p, task, context['job'].data['invocation_key'])
    need(goal['instance_id'] == task.data['instance_id'] and goal['release_id'] == release.id
        and set(policy.data['entity_ids']) <= set(goal['entity_ids']))
    recommended = inputs['reasoning_result']['recommended_context']
    if recommended is not None:
        from .workflow_task_context_v196 import checked_context
        checked_context(db, p, recommended, task=task, dispatch=False)
        need(recommended['policy']['policy_id'] == policy.id
            and recommended['policy']['approved_hash'] == policy.data['approved_hash'])
    instance = scoped(db, DomainInstance, release.instance_id, p)
    for identifier in policy.data['entity_ids']:
        entity = scoped(db, Entity, identifier, p)
        need(entity.namespace == instance.namespace)
    for profile in inputs['profiles']:
        need(set(profile['priority_entity_ids']) <= set(policy.data['entity_ids'])
            and profile['interval_seconds'] >= max(60, policy.data['cooldown_seconds'])
            and at < _time(profile['valid_until']) <= _time(policy.data['valid_until']),
            '检查间隔、重点对象或期限超出原批准策略；原策略未改写')
    return policy


def _receipt(install, cycle):
    profile = install['selected_profile']
    value = {'schema': RECEIPT, 'operation': OP, 'installation_id': install['id'], 'policy_id': install['policy_id'],
        'cycle_id': cycle['id'], 'candidate_ref': profile['candidate_ref'], 'interval_seconds': profile['interval_seconds'],
        'priority_entity_ids': profile['priority_entity_ids'], 'checked_at': cycle['checked_at'], 'status': 'check_consumed',
        'cycle_hash': cycle['record_hash'], 'simulation_only': install['source']['simulation_only'],
        'hardware_sampling_rate_changed': False, 'device_execution_completed': False, 'independent_acceptance': False}
    result = {'result': '已按重点顺序实际消费一次原批准策略检查；未改变设备硬件采样频率，未确认设备动作完成', 'service_receipt': value}
    validate(output_schema(), result); return copy.deepcopy(result)


def _state(db, p, context, call, inputs, key, approval):
    _lock(db, p, inputs['policy_pin']['policy_id']); head, rows, cycles = _checked(db, p, inputs['policy_pin']['policy_id'])
    found = [v for v in rows if v.invocation_id == call.id or v.request_key == key]
    need(len(found) == 1, '原调度安装不存在或库存缺失；只核对原号，不补建')
    row = found[0]; value = row.data
    need(row.invocation_id == call.id and row.request_key == key and row.parent_task_id == context['task'].id
        and row.job_id == context['job'].id and row.actor_id == p.user_id and row.capability_id == context['capability'].id
        and row.input_hash == digest(inputs) and _same(value['inputs'], inputs) and _same(value['approval'], approval)
        and _same(call.data.get(CALL_KEY), {'schema': INSTALL, 'installation_id': row.id, 'record_hash': row.record_hash}))
    return row, cycles


def execute(db, p, cap, inputs, key, approval):
    inputs = _inputs(inputs); context = validate_call_approval(db, p, cap, 'business', inputs, key, approval)
    call = shared._call(db, p, context, key, inputs, approval)
    policy_id = inputs['policy_pin']['policy_id']; _lock(db, p, policy_id)
    head, rows, cycles = _checked(db, p, policy_id)
    prior = [v for v in rows if v.invocation_id == call.id or v.request_key == key]
    if prior: return reconcile(db, p, cap, inputs, key, approval, call)
    need(CALL_KEY not in call.data, '原调用已有安装痕迹，不能换号补建')
    if head is None:
        need(inputs['expected_schedule_pin'] is None, '调度不存在，本次配置版本不匹配')
    else:
        original = next(row for row in rows if row.id == head.data['active_installation_id'])
        need(inputs['expected_schedule_pin'] == schedule_pin(original),
            '原调度配置已变化，请核对当前配置版本；定时检查不会改变此配置标识')
        need(any(row.installation_id == original.id and row.outcome == 'checked' for row in cycles),
            '原安装尚未实际消费首次检查，不能用新研判覆盖未决安装')
    need(len(rows) < MAX_RECORDS)
    producer = _producer(db, p, context, inputs['reasoning_result'])
    result = inputs['reasoning_result']; need(result['review_state'] == 'ready_for_human_review'
        and result['recommended_candidate_ref'] is not None, '原风险研判仍缺资料、冲突或无明确建议，未安装检查调度')
    need([v['candidate_ref'] for v in inputs['profiles']] == [v['candidate_ref'] for v in producer['candidates']],
        '封闭调度方案须完整对应原研判输入，不能只凭同名或临时JSON授权')
    selected = next(v for v in inputs['profiles'] if v['candidate_ref'] == result['recommended_candidate_ref'])
    at = db.scalar(select(func.clock_timestamp())); policy = _policy(db, p, context, inputs, at)
    capacity = _reserve_cycles(inputs, at, len(cycles))
    approval_pin = _approval_proof(db, p, context['task'])
    identifier = uid(); head_id = head.id if head else uid(); source = context['binding']
    value = _seal({'schema': INSTALL, 'id': identifier, 'head_id': head_id, 'workspace_id': p.workspace_id,
        'policy_id': policy.id, 'parent_task_id': context['task'].id, 'invocation_id': call.id,
        'job_id': context['job'].id, 'actor_id': p.user_id, 'capability_id': cap.id, 'request_key': key,
        'input_hash': digest(inputs), 'inputs': inputs, 'approval': copy.deepcopy(approval),
        'producer': producer, 'selected_profile': copy.deepcopy(selected), 'created_at': at.isoformat(),
        'capacity': capacity,
        'policy_hash': policy.data['approved_hash'], 'source': {'instance_id': context['task'].data['instance_id'],
            'release_id': context['release'].id, 'binding_hash': source['binding_hash'],
            'approval_pin': approval_pin,
            'simulation_only': source['simulation_only']}})
    if head is None:
        head = Head(id=head_id, workspace_id=p.workspace_id, policy_id=policy.id, version=1, status='active',
            record_hash='', data={}); db.add(head); db.flush()
        state = {'schema': HEAD, 'id': head_id, 'workspace_id': p.workspace_id, 'policy_id': policy.id,
            'version': 1, 'status': 'active', 'installation_ids': [identifier], 'cycle_ids': [],
            'stop_command_ids': [], 'restore_legacy': False,
            'active_installation_id': identifier, 'next_due': at.isoformat()}; previous = None
    else:
        previous = head.record_hash; state = {k: copy.deepcopy(v) for k, v in head.data.items() if k != 'record_hash'}
        state.update(version=head.version + 1, status='active', restore_legacy=False, active_installation_id=identifier,
            installation_ids=[*state['installation_ids'], identifier], next_due=at.isoformat())
    row = Installation(**{k: value[k] for k in ('id', 'head_id', 'workspace_id', 'policy_id', 'parent_task_id',
        'invocation_id', 'job_id', 'actor_id', 'capability_id', 'request_key', 'input_hash', 'record_hash')}, version=1, data=value)
    db.add(row); audit(db, p, INSTALLED, row.id, _anchor(INSTALL, value)); _store_head(db, p, head, state, previous=previous)
    call.data = {**call.data, CALL_KEY: {'schema': INSTALL, 'installation_id': row.id, 'record_hash': row.record_hash}}
    db.flush(); _checked(db, p, policy.id)
    raise shared.PendingService('检查调度已安装，等待正常worker实际消费首次策略检查；未改设备频率')


def _stop_nonce(db, p, request_id):
    safe._lock(db, {'workflow_monitor_schedule_stop_nonce_v200': p.workspace_id, 'request_id': request_id})


def _stop_original(db, p, policy_id, request_id, sha, path):
    row = db.scalar(select(StopCommand).where(StopCommand.workspace_id == p.workspace_id,
        StopCommand.request_id == request_id).execution_options(populate_existing=True))
    traces = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == STOPPED, Audit.data['request_id'].as_string() == request_id)))
    need(row is not None or not traces, '原停止审计存在但命令缺失，不能换策略或重建原请求')
    if row is not None:
        need(row.actor_id == p.user_id, '停止请求属于原办理人', 403)
        need(row.policy_id == policy_id and row.body_sha256 == sha and row.data.get('path') == path,
            '原请求号已绑定其它停止正文或路径，未再次应用')
    _head, _installs, _cycles = _checked(db, p, policy_id)
    return row


def original_stop_request(db, p, policy_id, request_id, expected_sha256, *, path):
    """Read a genuine immutable stop result, not an inference from current state."""
    from .relations import lock_current_actor
    from .security import require
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    safe.identity(policy_id); safe.identity(request_id)
    need(type(expected_sha256) is str and safe.SHA.fullmatch(expected_sha256) is not None, status=422)
    _stop_nonce(db, p, request_id); _lock(db, p, policy_id)
    row = _stop_original(db, p, policy_id, request_id, expected_sha256, path)
    return {'schema': STOP_RESULT + '/original', 'request_id': request_id, 'found': row is not None,
        'body_sha256': expected_sha256, 'result': copy.deepcopy(row.data['result']) if row is not None else None}


def stop_schedule(db, p, policy_id, body, raw_body, *, path):
    """Stop future checks by original bytes/CAS; caller commits once.

    restore_legacy explicitly resumes the original approved monitor. Neither
    option cancels an in-flight physical action, alters its receipt or grants a
    new acquisition authorization. A lost response is read by original nonce.
    """
    from .relations import lock_current_actor
    from .security import require
    from .perception import authorized_monitor
    require(p, 'admin', 'operator'); safe.identity(policy_id)
    body, sha = _stop_body(body, raw_body)
    need(type(path) is str and path.startswith('/') and len(path) <= 1024, status=422)
    _stop_nonce(db, p, body['request_id']); _lock(db, p, policy_id)
    lock_current_actor(db, p)
    original = _stop_original(db, p, policy_id, body['request_id'], sha, path)
    if original is not None: return copy.deepcopy(original.data['result'])
    head, installs, _cycles = _checked(db, p, policy_id)
    need(head is not None, '此策略没有已安装调度，未创建停止或取消设备')
    need(len(head.data['stop_command_ids']) < MAX_RECORDS)
    current = next(row for row in installs if row.id == head.data['active_installation_id'])
    need(body['expected_schedule_pin'] == schedule_pin(current), '调度配置已经变化，原停止请求未应用')
    policy = scoped(db, MonitorPolicy, policy_id, p, True)
    authorized_monitor(db, p, policy)
    need(policy.data['approved_hash'] == current.data['policy_hash'])
    identifier = uid(); previous = head.record_hash
    state = {key: copy.deepcopy(item) for key, item in head.data.items() if key != 'record_hash'}
    state.update(version=head.version + 1, status='cancelled', next_due=None,
        restore_legacy=body['restore_legacy'], stop_command_ids=[*state['stop_command_ids'], identifier])
    result = {'schema': STOP_RESULT, 'request_id': body['request_id'], 'policy_id': policy_id,
        **body['expected_schedule_pin'], 'version': state['version'], 'status': 'cancelled',
        'restore_legacy': body['restore_legacy'], 'device_execution_cancelled': False}
    value = _seal({'schema': STOP, 'id': identifier, 'workspace_id': p.workspace_id,
        'policy_id': policy_id, 'head_id': head.id, 'actor_id': p.user_id, 'request_id': body['request_id'],
        'body_sha256': sha, 'raw_base64': base64.b64encode(raw_body).decode('ascii'), 'path': path,
        'body': body, 'previous_head_hash': previous, 'head_version': state['version'], 'result': result})
    row = StopCommand(**{key: value[key] for key in ('id', 'workspace_id', 'policy_id', 'head_id',
        'actor_id', 'request_id', 'body_sha256', 'record_hash')}, version=1, data=value)
    db.add(row); audit(db, p, STOPPED, row.id, _anchor(STOP, value))
    _store_head(db, p, head, state, previous=previous)
    db.flush(); _checked(db, p, policy_id)
    return copy.deepcopy(result)


def reconcile(db, p, cap, inputs, key, approval, call):
    context = validate_call_approval(db, p, cap, 'business', inputs, key, approval)
    actual = shared._call(db, p, context, key, inputs, approval, supplied=call, allow_unknown=True)
    install, cycles = _state(db, p, context, actual, inputs, key, approval)
    checked = sorted([v for v in cycles if v.installation_id == install.id and v.outcome == 'checked'], key=lambda v: v.head_version)
    if checked: return _receipt(install.data, checked[0].data)
    head, _records, _cycles = _checked(db, p, install.policy_id)
    need(not (head.status == 'cancelled' and head.data['active_installation_id'] == install.id),
        '原检查调度已明确停止，首个检查尚未生效；未取消或重派在途设备动作')
    failures = [v for v in cycles if v.installation_id == install.id and v.outcome in {'blocked', 'cancelled', 'expired'}]
    need(not failures, '原检查调度已阻断、停止或到期；保留原记录，不改成已生效')
    raise shared.PendingService('原检查调度仍待实际worker消费，尚未产生检查回执')


def _tick_authority(db, p, policy, install):
    from .perception import authorized_monitor
    from .workflow_graph_scope import _frozen, _job_matches
    from .workflow_graph import business_binding
    from .worker import actor
    from .relations import lock_current_actor
    actual_actor = actor(db, p.workspace_id, install['actor_id']); lock_current_actor(db, actual_actor)
    task, release, business, graph, state, activation, node = _frozen(db, actual_actor, install['parent_task_id'], install['approval']['activation_id'])
    job = scoped(db, Job, install['job_id'], actual_actor); _job_matches(task, job, state, activation, node)
    need(task.status not in {'workflow_cancelled', 'workflow_stopped'} and state['status'] not in {'cancelled', 'stopped', 'failed'}
        and not task.data.get('native_goal_cancelled') and not task.data.get('native_goal_timed_out')
        and activation['epoch'] == state['cancel_epoch'] == install['approval']['cancel_epoch']
        and _same(kernel.ability_inputs(graph, state, activation['id']), install['inputs'])
        and business_binding(release, business)['binding_hash'] == install['source']['binding_hash'])
    need(_same(_approval_proof(db, actual_actor, task), install['source']['approval_pin']))
    cap = published(db, actual_actor, install['capability_id']); validate_manifest(cap.data)
    need(cap.content_hash == install['approval']['capability_hash'])
    # Recheck the actual original decision and frozen graph as history. Do not
    # call live()/Goal.guard(): their executing-only gate would incorrectly
    # turn normal Task completion or acceptance into schedule cancellation.
    from .native_goals import saved_context
    goal = saved_context(db, actual_actor, task)
    from .instance_api import active
    instance, current_release = active(db, actual_actor, install['source']['instance_id'])
    need(current_release.id == release.id == install['source']['release_id']
        and business['id'] in instance.data.get('enabled_business_ids', [])
        and goal['instance_id'] == instance.id and goal['release_id'] == release.id
        and set(policy.data['entity_ids']) <= set(goal['entity_ids'])
        and set(policy.data['entity_ids']) <= set(task.data['entity_ids']))
    published(db, actual_actor, release.package_id, 'domain_package')
    for identifier in policy.data['entity_ids']:
        need(scoped(db, Entity, identifier, actual_actor).namespace == instance.namespace)
    need(_same(_producer(db, actual_actor, {'task': task, 'job': job}, install['inputs']['reasoning_result']), install['producer']))
    authorized_monitor(db, p, policy); need(policy.data['approved_hash'] == install['policy_hash'])
    return actual_actor


def policy_tick_pin(head):
    """Queue identity only; a hash marker never replaces _checked inventory."""
    if head is None:
        return None
    due = head.next_due
    need(due is None or isinstance(due, datetime) and due.tzinfo is not None and due.utcoffset() is not None)
    return {'id': head.id, 'version': head.version, 'record_hash': head.record_hash,
        'status': head.status, 'next_due': due.astimezone(timezone.utc).isoformat() if due is not None else None}


def policy_tick_policy_pin(policy):
    return {'workspace_id': policy.workspace_id, 'policy_id': policy.id, 'version': policy.version,
        'approved_hash': policy.data.get('approved_hash'), 'approved_by': policy.approved_by, 'status': policy.status}


def consume_policy_tick(db, p, policy, *, expected_head_pin=_ANY_HEAD, expected_policy_pin=_ANY_HEAD, expected_control_pin=_ANY_HEAD):
    """True consumes/suppresses this policy tick; False is genuine legacy only.

    The caller commits. No network, model or hardware action runs here. Original
    propose_acquisitions owns all actual gap/budget/asset/unknown-state checks.
    """
    # Actual service writers lock Task before their schedule advisory lock.
    # Read identities only to acquire that same order, then cold-check all
    # inventory under the lock; a changed head never grants authority.
    preliminary = _rows(db, Head, p, policy.id)
    need(len(preliminary) <= 1)
    expected = None
    if preliminary:
        before = preliminary[0]
        rows = _rows(db, Installation, p, policy.id)
        matched = [row for row in rows if row.id == before.data.get('active_installation_id')]
        need(len(matched) == 1)
        expected = (before.id, before.version, matched[0].id)
        from .signal_dual_handoffs import prelock_task
        prelock_task(db, p, matched[0].parent_task_id)
        scoped(db, Task, matched[0].parent_task_id, p, True)
    _lock(db, p, policy.id); head, installs, cycles = _checked(db, p, policy.id)
    # Task -> schedule -> Policy remains the existing order. Even first-time
    # controls serialize against this Policy row, without fabricating a V200 Head.
    policy=scoped(db,MonitorPolicy,policy.id,p,True)
    from .monitor_scheduling_service_v253 import periodic_gate
    mode_gate=periodic_gate(db,p,policy,expected=None if expected_control_pin is _ANY_HEAD else expected_control_pin,
        check_expected=expected_control_pin is not _ANY_HEAD)
    if not mode_gate['allowed']:return True
    if expected_head_pin is not _ANY_HEAD:
        need(_same(policy_tick_pin(head), expected_head_pin),
            '原排队检查的调度版本或到期时间已变化；旧任务保留，未消费另一项调度')
    if expected_policy_pin is not _ANY_HEAD:
        # Preserve Task -> schedule -> Policy order, including genuine legacy
        # and audited restore. Keep the Policy lock through caller fallback so
        # an old queued Job cannot consume a newly approved policy scope.
        policy = scoped(db, MonitorPolicy, policy.id, p, True)
        need(_same(policy_tick_policy_pin(policy), expected_policy_pin),
            '原排队检查的策略批准已变化；旧任务保留，未改用新的批准范围')
    if head is None: return False
    need(expected == (head.id, head.version, head.data['active_installation_id']),
        '调度版本在锁定前已变化，保留原记录，等待下一次正常核对')
    if head.status == 'expired' or head.status == 'cancelled' and head.data['restore_legacy'] is True:
        return False  # Only audited expiry/explicit restore resumes the original monitor.
    if head.status != 'active': return True
    policy = scoped(db, MonitorPolicy, policy.id, p, True)
    at = db.scalar(select(func.clock_timestamp()))
    if at < head.next_due: return True
    need(len(cycles) < MAX_RECORDS)
    install = next(v.data for v in installs if v.id == head.data['active_installation_id'])
    selected = install['selected_profile']; priority = selected['priority_entity_ids']
    order = [*priority, *[v for v in policy.data['entity_ids'] if v not in priority]]
    outcome = 'checked'; created = []; reason = None
    if at >= _time(selected['valid_until']): outcome = 'expired'; reason = '有界检查调度到期，未延长原监测授权'
    else:
        try:
            with db.begin_nested():
                _tick_authority(db, p, policy, install)
                from .perception import propose_acquisitions
                created = propose_acquisitions(db, p, policy.id, entity_ids=order)
                need(type(created) is list and len(created) == len(set(created)))
                for identity in created: safe.identity(identity)
        except (HTTPException, ValueError, KeyError, TypeError, AttributeError, StopIteration):
            outcome = 'blocked'; reason = '原监测策略、任务、模型研判或执行权限已变化；未继续检查或扩大授权'
    identifier = uid(); previous = head.record_hash
    value = _seal({'schema': CYCLE, 'id': identifier, 'head_id': head.id, 'installation_id': install['id'],
        'workspace_id': p.workspace_id, 'policy_id': policy.id, 'head_version': head.version + 1,
        'due_at': head.next_due.isoformat(), 'checked_at': at.isoformat(), 'outcome': outcome,
        'ordered_entity_ids': order, 'priority_entity_ids': priority, 'acquisition_ids': created,
        'reason': reason, 'previous_head_hash': previous})
    row = Cycle(id=identifier, workspace_id=p.workspace_id, policy_id=policy.id, head_id=head.id,
        installation_id=install['id'], head_version=head.version + 1, outcome=outcome, version=1,
        record_hash=value['record_hash'], data=value); db.add(row)
    audit(db, p, CONSUMED, row.id, _anchor(CYCLE, value))
    state = {k: copy.deepcopy(v) for k, v in head.data.items() if k != 'record_hash'}
    state.update(version=head.version + 1, cycle_ids=[*state['cycle_ids'], identifier],
        status='active' if outcome == 'checked' else outcome,
        next_due=(at + timedelta(seconds=selected['interval_seconds'])).isoformat() if outcome == 'checked' else None)
    _store_head(db, p, head, state, previous=previous); db.flush(); _checked(db, p, policy.id)
    return True
