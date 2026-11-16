"""Assignment receipts and durable human reports, never physical completion.

Only an installed V195 approval variant may dispatch. Personal work-order
commands never call a tool, write an Entity, change a Goal or complete a graph.
Lock order is original parent Task -> family workspace mutex. Read/recovery
uses only that mutex and never takes a parent lock or reads report originals.
All three row inventories and independent Audit inventories are checked;
database exceptions propagate. The caller owns the transaction and commit.
"""
import copy
import hashlib
import json
import re
import sys
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select

from . import models as m
from . import workflow_service_v195 as shared
from . import business_flow_review_store as safe
from .contracts import digest
from .services import audit, scoped, validate
from .workflow_task_dispatch_models_v198 import (
    WorkflowTaskDispatchBatchV198 as Batch,
    WorkflowTaskWorkOrderV198 as Item,
    WorkflowTaskWorkOrderCommandV198 as Command,
)


OP = 'dispatch_tasks'
RELEASE_VERSION = '1.0.0'
SCOPE = 'human_work_orders'
PREFIX = '/api/workspace/work-orders-v198'
BATCH = 'workflow-task-dispatch-batch-v198'
ITEM = 'workflow-work-order-state-v198'
COMMAND = 'workflow-work-order-command-v198'
VIEW = 'workflow-work-order-view-v198'
RESULT = 'workflow-work-order-result-v198'
REQUEST = 'workflow-work-order-original-result-v198'
RECEIPT = 'workflow-task-dispatch-receipt-v198'
CALL_KEY = 'workflow_task_dispatch_v198'
CREATED = 'workflow_task_dispatch.created_v198'
CHANGED = 'workflow_work_order.state_v198'
COMMANDED = 'workflow_work_order.command_v198'
FLAGS = {'execution_completed': False, 'independent_acceptance': False, 'world_state_verified': False}
MAX_INPUT_BYTES = 512 * 1024
MAX_COMMAND_BYTES = 128 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_HISTORY = 1000
OPERATIONS = {'progress', 'submit', 'return', 'cancel'}
STATUSES = {'assigned', 'in_progress', 'completion_reported', 'returned', 'cancelled'}
ERROR = '原工作单、任务派发、原请求或独立审计无法核对；未重派任务或确认实际完成'
STATE_KEYS = {'schema', 'id', 'workspace_id', 'batch_id', 'parent_task_id', 'spec', 'version',
    'status', 'note', 'updated_at', 'command_ids', 'record_hash'}
SPEC_KEYS = {'id', 'ordinal', 'title', 'description', 'expected_result', 'assignee_id',
    'assignee_name', 'due_at', 'dispatcher_id', 'dispatcher_name', 'parent_owner_id', 'simulation_only'}
BATCH_KEYS = {'schema', 'id', 'workspace_id', 'parent_task_id', 'invocation_id', 'job_id', 'actor_id',
    'capability_id', 'request_key', 'inputs', 'input_hash', 'approval', 'approval_hash', 'source',
    'created_at', 'initial_states', 'record_hash'}
SOURCE_KEYS = {'instance_id', 'release_id', 'package_id', 'business_id', 'entity_ids', 'goal_context_hash',
    'goal_snapshot', 'binding_hash', 'qualification_id', 'qualification_hash', 'source_hash',
    'configuration_hash', 'simulation_only', 'simulation_basis_hash'}


def need(ok, message=ERROR, status=409):
    if not ok: raise HTTPException(status, message)


def _same(a, b): return safe._same(a, b)
def _seal(value): return {**copy.deepcopy(value), 'record_hash': digest(value)}
def _uuid(value): return type(value) is str and re.fullmatch(shared.UUID_PATTERN, value) is not None
def _sha(value): return type(value) is str and re.fullmatch(shared.HASH_PATTERN, value) is not None
def _text(value, maximum): return type(value) is str and 0 < len(value) <= maximum and bool(value.strip()) and '\x00' not in value


def _time(value):
    need(type(value) is str and len(value) <= 80 and 'T' in value, status=422)
    try: result = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    except (ValueError, OverflowError): raise HTTPException(422, '期限必须是带时区的完整时间，原输入未改写') from None
    need(result.tzinfo is not None and result.utcoffset() is not None, status=422)
    return result


def input_schema(operation=OP):
    need(operation == OP, status=422)
    item = shared._object({'title': {'type': 'string', 'minLength': 1, 'maxLength': 200, 'pattern': r'\S'},
        'description': {'type': 'string', 'minLength': 1, 'maxLength': 4000, 'pattern': r'\S'},
        'expected_result': {'type': 'string', 'minLength': 1, 'maxLength': 2000, 'pattern': r'\S'},
        'assignee_id': {'type': 'string', 'pattern': shared.UUID_PATTERN},
        'due_at': {'type': 'string', 'format': 'date-time', 'maxLength': 80}})
    return shared._object({'scope': {'const': SCOPE},
        'items': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': item}})


def output_schema(operation=OP):
    need(operation == OP, status=422)
    identity = {'type': 'string', 'pattern': shared.UUID_PATTERN}
    receipt = shared._object({'schema': {'const': RECEIPT}, 'operation': {'const': OP},
        'scope': {'const': SCOPE}, 'batch_id': identity, 'parent_task_id': copy.deepcopy(identity),
        'invocation_id': copy.deepcopy(identity), 'status': {'const': 'assigned'},
        'items': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': shared._object({
            'item_id': copy.deepcopy(identity), 'assignee_id': copy.deepcopy(identity), 'status': {'const': 'assigned'}})},
        'simulation_only': {'type': 'boolean'}, 'batch_hash': {'type': 'string', 'pattern': shared.HASH_PATTERN},
        'receipt_hash': {'type': 'string', 'pattern': shared.HASH_PATTERN},
        'execution_completed': {'const': False}, 'independent_acceptance': {'const': False}})
    return shared._object({'result': {'type': 'string', 'minLength': 1, 'maxLength': 1000}, 'service_receipt': receipt})


def validate_manifest(manifest):
    need(type(manifest) is dict and manifest.get('kind') == 'tool'
        and manifest.get('version') == RELEASE_VERSION and manifest.get('allowed_agent_roles') == ['business'], status=422)
    safe._bytes(manifest, MAX_INPUT_BYTES)
    need(_same(manifest.get('execution'), {'adapter': 'workflow_service', 'service_actor': 'task_center',
        'operation': OP, 'side_effect': True}) and manifest['execution']['side_effect'] is True,
        '人工工作单派发需要独立任务中心操作及真实有副作用登记', 422)
    need(_same(manifest.get('input_schema'), input_schema()) and _same(manifest.get('output_schema'), output_schema()),
        '人工工作单的登记输入输出必须与实际处理器完全相符', 422)
    return copy.deepcopy(manifest)


def _inputs(value):
    need(type(value) is dict and set(value) == {'scope', 'items'} and value['scope'] == SCOPE
        and type(value['items']) is list and 1 <= len(value['items']) <= 20, status=422)
    safe._bytes(value, MAX_INPUT_BYTES); validate(input_schema(), value)
    for item in value['items']:
        need(type(item) is dict and set(item) == {'title', 'description', 'expected_result', 'assignee_id', 'due_at'}
            and _text(item['title'], 200) and _text(item['description'], 4000)
            and _text(item['expected_result'], 2000) and _uuid(item['assignee_id']), status=422)
        _time(item['due_at'])
    return copy.deepcopy(value)


make_approval = shared.make_approval


def validate_call_approval(db, p, cap, role, inputs, key, approval):
    return shared.validate_call_approval(db, p, cap, role, inputs, key, approval, _variant=sys.modules[__name__])


def _iam(db, p, *, write=False):
    user = db.scalar(select(m.User).where(m.User.id == p.user_id, m.User.active.is_(True)))
    member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == p.workspace_id,
        m.Membership.user_id == p.user_id))
    need(user is not None and member is not None and member.role == p.role,
        '当前账号或工作区权限已变化，请正常重新登录后核对', 403)
    if write: need(member.role in {'admin', 'operator'}, '当前账号没有工作单办理权限', 403)
    return user


def _lock(db, p, *, write=False):
    safe._lock(db, {'workflow_work_orders_v198': p.workspace_id}, shared=not write)


def _rows(db, model, p, maximum, *conditions):
    rows = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id, *conditions)
        .order_by(model.id).limit(maximum + 1).execution_options(populate_existing=True)))
    need(len(rows) <= maximum, '工作单历史超过本次完整核对容量；未截断或隐藏原记录')
    return rows


def _anchor(state, command_id):
    return {'schema': ITEM, 'item_id': state['id'], 'version': state['version'],
        'record_hash': state['record_hash'], 'command_id': command_id}


def _receipt(batch):
    value = {'schema': RECEIPT, 'operation': OP, 'scope': SCOPE, 'batch_id': batch['id'],
        'parent_task_id': batch['parent_task_id'], 'invocation_id': batch['invocation_id'], 'status': 'assigned',
        'items': [{'item_id': state['id'], 'assignee_id': state['spec']['assignee_id'], 'status': 'assigned'}
            for state in batch['initial_states']], 'simulation_only': batch['source']['simulation_only'],
        'batch_hash': batch['record_hash'], 'execution_completed': False, 'independent_acceptance': False}
    value['receipt_hash'] = digest(value)
    return {'result': '已保存并分派人工工作单；尚未执行，也未完成独立验收', 'service_receipt': value}


def _marker(batch):
    return {'schema': BATCH, 'batch_id': batch['id'], 'batch_hash': batch['record_hash'],
        'receipt_hash': _receipt(batch)['service_receipt']['receipt_hash']}


def _state(value):
    need(type(value) is dict and set(value) == STATE_KEYS and value['schema'] == ITEM
        and type(value['spec']) is dict and set(value['spec']) == SPEC_KEYS
        and value['id'] == value['spec']['id'] and type(value['version']) is int and value['version'] >= 1
        and value['status'] in STATUSES and type(value['note']) is str and len(value['note']) <= 12000
        and type(value['command_ids']) is list and len(value['command_ids']) < MAX_HISTORY
        and len(set(value['command_ids'])) == len(value['command_ids'])
        and value['record_hash'] == digest({k: v for k, v in value.items() if k != 'record_hash'}))
    spec = value['spec']
    need(type(spec['ordinal']) is int and 0 <= spec['ordinal'] < 20
        and all(_uuid(spec[key]) for key in ('id', 'assignee_id', 'dispatcher_id', 'parent_owner_id'))
        and _text(spec['assignee_name'], 100) and _text(spec['dispatcher_name'], 100) and type(spec['simulation_only']) is bool
        and _text(spec['title'], 200) and _text(spec['description'], 4000) and _text(spec['expected_result'], 2000)
        and all(_uuid(value[key]) for key in ('id', 'workspace_id', 'batch_id', 'parent_task_id')))
    _time(spec['due_at']); _time(value['updated_at']); safe._bytes(value, MAX_RECORD_BYTES)
    return value


def _history(state, actor_id, actor_name, operation, request_id):
    return {'version': state['version'], 'status': state['status'], 'operation': operation,
        'note': state['note'], 'actor_id': actor_id, 'actor_name': actor_name,
        'at': state['updated_at'], 'request_id': request_id}


def _view(state, history):
    spec = state['spec']
    return {'schema': VIEW, 'id': state['id'], 'batch_id': state['batch_id'], 'parent_task_id': state['parent_task_id'],
        **{key: copy.deepcopy(spec[key]) for key in ('title', 'description', 'expected_result', 'assignee_id',
            'assignee_name', 'due_at', 'simulation_only')}, 'scope': SCOPE, 'status': state['status'],
        'version': state['version'], 'note': state['note'], 'updated_at': state['updated_at'],
        'history': copy.deepcopy(history), **FLAGS}


def _allowed(state, actor_id, operation):
    status, spec = state['status'], state['spec']
    if operation in {'progress', 'submit'}:
        return actor_id == spec['assignee_id'] and status in {'assigned', 'in_progress', 'returned'}
    manager = actor_id in {spec['dispatcher_id'], spec['parent_owner_id']}
    return manager and (status == 'completion_reported' if operation == 'return' else status != 'cancelled')


def _transition(state, body, actor_id, operation, command_id, at):
    need(_allowed(state, actor_id, operation), '此状态或责任人不能进行该工作单操作', 403)
    status = {'progress': 'in_progress', 'submit': 'completion_reported', 'return': 'returned', 'cancel': 'cancelled'}[operation]
    value = {k: copy.deepcopy(v) for k, v in state.items() if k != 'record_hash'}
    value.update(version=state['version'] + 1, status=status, note=body['note'], updated_at=at,
        command_ids=[*state['command_ids'], command_id])
    return _seal(value)


def parse_wire(raw):
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_COMMAND_BYTES: raise ValueError(ERROR)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result: raise ValueError(ERROR)
                result[key] = value
            return result
        def constant(_): raise ValueError(ERROR)
        body = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=constant)
        if (type(body) is not dict or set(body) != {'request_id', 'expected_version', 'note'}
                or not _uuid(body['request_id']) or type(body['expected_version']) is not int
                or body['expected_version'] < 1 or not _text(body['note'], 12000)): raise ValueError(ERROR)
        safe._bytes(body, MAX_COMMAND_BYTES)
        return body
    except (ValueError, UnicodeError, TypeError, KeyError, RecursionError, OverflowError):
        raise ValueError('工作单命令需要原请求编号、严格版本和完整说明；未补默认值或截断输入') from None


def command_path(item_id, operation):
    need(_uuid(item_id) and operation in OPERATIONS, status=422)
    return PREFIX + '/' + item_id + '/' + operation


def _command_anchor(row):
    return {'schema': COMMAND, 'command_id': row.id, 'item_id': row.item_id, 'request_id': row.request_id,
        'body_sha256': row.body_sha256, 'record_hash': row.record_hash}


def _inventory(db, p):
    try: return _checked_inventory(db, p)
    except HTTPException: raise HTTPException(409, ERROR) from None
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration):
        raise HTTPException(409, ERROR) from None


def _checked_inventory(db, p):
    """Complete family rows/Audits plus reverse Invocation markers; no authority cache."""
    batches = _rows(db, Batch, p, 512); items = _rows(db, Item, p, 10240)
    commands = _rows(db, Command, p, 65536)
    events = _rows(db, m.Audit, p, 150000, m.Audit.action.in_([CREATED, CHANGED, COMMANDED]))
    # JSON null, malformed and empty markers are evidence too, never absence.
    calls = _rows(db, m.Invocation, p, 512, m.Invocation.data.has_key(CALL_KEY))
    used_events = set(); used_items = set(); used_commands = set(); used_calls = set(); views = {}
    def event(action, object_id, actor_id, data):
        matching = [row for row in events if row.action == action and row.object_id == object_id and _same(row.data, data)]
        need(len(matching) == 1 and matching[0].actor_id == actor_id and matching[0].id not in used_events)
        used_events.add(matching[0].id)
    for batch in batches:
        value = batch.data
        need(batch.version == 1 and type(value) is dict and set(value) == BATCH_KEYS and value['schema'] == BATCH
            and all(getattr(batch, key) == value[key] for key in ('id', 'workspace_id', 'parent_task_id',
                'invocation_id', 'job_id', 'actor_id', 'capability_id', 'record_hash'))
            and value['record_hash'] == digest({k: v for k, v in value.items() if k != 'record_hash'})
            and type(value['initial_states']) is list and 1 <= len(value['initial_states']) <= 20
            and type(value['source']) is dict and set(value['source']) == SOURCE_KEYS
            and type(value['approval']) is dict and set(value['approval']) == shared.APPROVAL_KEYS
            and value['approval']['schema'] == shared.APPROVAL
            and value['approval']['task_id'] == batch.parent_task_id
            and value['approval']['job_id'] == batch.job_id
            and value['approval']['capability_id'] == batch.capability_id
            and value['input_hash'] == digest(value['inputs']) and value['approval_hash'] == digest(value['approval']))
        safe._bytes(value, MAX_RECORD_BYTES); _inputs(value['inputs'])
        need(len(value['initial_states']) == len(value['inputs']['items']))
        event(CREATED, batch.id, batch.actor_id, {'schema': BATCH, 'batch_id': batch.id, 'record_hash': batch.record_hash})
        original = [row for row in calls if row.id == batch.invocation_id]
        need(len(original) == 1); call = original[0]
        need(call.id not in used_calls and call.capability_id == batch.capability_id
            and call.idempotency_key == value['request_key'] and _same(call.data.get(CALL_KEY), _marker(value))
            and call.data.get('job_id') == batch.job_id and call.data.get('input_hash') == value['input_hash']
            and call.data.get('capability_hash') == value['approval']['capability_hash']
            and call.data.get('side_effect') is True and call.data.get('role') == 'business'
            and _uuid(call.data.get('lease_token'))
            and _same(call.data.get('inputs'), value['inputs']) and _same(call.data.get('approval'), value['approval']))
        if call.data.get('output') is not None: need(_same(call.data['output'], _receipt(value)))
        used_calls.add(call.id)
        parent = db.scalar(select(m.Task).where(m.Task.workspace_id == p.workspace_id, m.Task.id == batch.parent_task_id))
        job = db.scalar(select(m.Job).where(m.Job.workspace_id == p.workspace_id, m.Job.id == batch.job_id))
        need(parent is not None and job is not None and job.kind == 'execute_full_semantic_graph_node'
            and job.data.get('task_id') == parent.id and job.data.get('actor_id') == batch.actor_id
            and job.data.get('invocation_key') == value['request_key']
            and all(job.data.get(key) == value['approval'][key] for key in ('activation_id', 'graph_hash', 'cancel_epoch'))
            and job.data.get('input_hash') == value['input_hash'] and _same(job.data.get('inputs'), value['inputs']))
        for ordinal, initial in enumerate(value['initial_states']):
            _state(initial)
            need(initial['version'] == 1 and initial['status'] == 'assigned' and initial['note'] == ''
                and initial['command_ids'] == [] and initial['batch_id'] == batch.id
                and initial['workspace_id'] == p.workspace_id and initial['parent_task_id'] == parent.id
                and initial['spec']['ordinal'] == ordinal
                and initial['spec']['dispatcher_id'] == batch.actor_id
                and _same({key: initial['spec'][key] for key in value['inputs']['items'][ordinal]}, value['inputs']['items'][ordinal])
                and initial['spec']['simulation_only'] is value['source']['simulation_only'])
            matches = [row for row in items if row.id == initial['id']]
            need(len(matches) == 1); item = matches[0]
            need(item.id not in used_items and item.batch_id == batch.id and item.parent_task_id == parent.id
                and item.assignee_id == initial['spec']['assignee_id'] and item.ordinal == ordinal)
            used_items.add(item.id)
            event(CHANGED, item.id, batch.actor_id, _anchor(initial, None))
            history = [_history(initial, batch.actor_id, initial['spec']['dispatcher_name'], 'assigned', None)]
            state = initial
            own_commands = sorted([row for row in commands if row.item_id == item.id], key=lambda row: row.item_version)
            need(len(own_commands) < MAX_HISTORY)
            for row in own_commands:
                need(row.id not in used_commands and row.version == 1 and row.parent_task_id == parent.id
                    and row.item_version == state['version'] + 1 and row.operation in OPERATIONS
                    and row.method == 'POST' and row.path == command_path(item.id, row.operation)
                    and row.body_sha256 == hashlib.sha256(row.exact_body.encode('utf-8')).hexdigest())
                body = parse_wire(row.exact_body.encode('utf-8')); data = row.data
                need(body['request_id'] == row.request_id and body['expected_version'] == state['version']
                    and type(data) is dict and set(data) == {'schema', 'actor_name', 'previous_hash', 'next_state', 'result', 'record_hash'}
                    and data['schema'] == COMMAND and data['previous_hash'] == state['record_hash']
                    and row.record_hash == data['record_hash'] == digest({
                        'id': row.id, 'workspace_id': row.workspace_id, 'item_id': row.item_id,
                        'parent_task_id': row.parent_task_id, 'actor_id': row.actor_id, 'request_id': row.request_id,
                        'item_version': row.item_version, 'operation': row.operation, 'method': row.method,
                        'path': row.path, 'exact_body': row.exact_body, 'body_sha256': row.body_sha256,
                        **{k: v for k, v in data.items() if k != 'record_hash'}}))
                next_state = _state(data['next_state'])
                need(_same(next_state, _transition(state, body, row.actor_id, row.operation, row.id, next_state['updated_at'])))
                history.append(_history(next_state, row.actor_id, data['actor_name'], row.operation, row.request_id))
                expected = {'schema': RESULT, 'request_id': row.request_id, 'item': _view(next_state, history), **FLAGS}
                need(_same(data['result'], expected))
                event(COMMANDED, row.request_id, row.actor_id, _command_anchor(row))
                event(CHANGED, item.id, row.actor_id, _anchor(next_state, row.id))
                used_commands.add(row.id); state = next_state
            need(_same(item.data, state) and item.version == state['version'] and item.status == state['status']
                and item.record_hash == state['record_hash'])
            views[item.id] = _view(state, history)
    need(used_items == {row.id for row in items} and used_commands == {row.id for row in commands}
        and used_events == {row.id for row in events} and used_calls == {row.id for row in calls})
    return batches, items, commands, views


def _source(db, p, current):
    task, release, binding = current['task'], current['release'], current['binding']
    from .goals import snapshot
    context = task.data.get('native_goal_context')
    need(type(context) is dict and task.data.get('native_goal_context_hash') == digest(context)
        and context.get('goal_snapshot') == snapshot(task) and type(binding['simulation_only']) is bool)
    simulation = binding['simulation_only']; simulation_basis = None
    frozen = task.data.get('perception_signal')
    if frozen is not None:
        need(type(frozen) is dict and _uuid(frozen.get('signal_id')))
        signal = scoped(db, m.PerceptionSignal, frozen['signal_id'], p)
        from .perception_signals import checked
        basis = checked(db, p, signal)
        need(signal.data['basis_hash'] == frozen['basis_hash'])
        if basis.get('simulation_origin') is not None:
            need(basis.get('simulation') is True and basis.get('field_verified') is False)
            simulation = True; simulation_basis = digest(basis['simulation_origin'])
    # A task SIM marker cannot silently turn into production when its actual
    # original observation signal is missing or cannot be independently checked.
    if task.data.get('simulation_origin') is not None: need(simulation_basis is not None)
    need(task.data.get('instance_release_id') == release.id and task.package_id == release.package_id
        and context.get('instance_id') == task.data.get('instance_id')
        and context.get('release_id') == release.id and context.get('business_id') == task.data.get('business_id')
        and _same(context.get('entity_ids'), task.data.get('entity_ids')))
    return {'instance_id': task.data['instance_id'], 'release_id': release.id, 'package_id': task.package_id,
        'business_id': task.data['business_id'], 'entity_ids': copy.deepcopy(task.data['entity_ids']),
        'goal_context_hash': digest(context), 'goal_snapshot': snapshot(task), 'binding_hash': binding['binding_hash'],
        'qualification_id': binding['qualification_id'], 'qualification_hash': binding['qualification_hash'],
        'source_hash': digest(binding['source']), 'configuration_hash': release.configuration_hash,
        'simulation_only': simulation, 'simulation_basis_hash': simulation_basis}


def execute(db, p, cap, inputs, key, approval):
    inputs = _inputs(inputs)
    current = validate_call_approval(db, p, cap, 'business', inputs, key, approval)
    call = shared._call(db, p, current, key, inputs, approval)
    actor = _iam(db, p, write=True)
    _lock(db, p, write=True)
    batches, items, commands, views = _inventory(db, p)
    existing = [row for row in batches if row.invocation_id == call.id]
    if existing: return _receipt(existing[0].data)
    need(CALL_KEY not in call.data and len(batches) < 512)
    from .layered_approval_store_v175 import real_member
    now = db.scalar(select(func.clock_timestamp())); at = now.isoformat()
    source = _source(db, p, current); parent = current['task']
    batch_id = m.uid(); states = []
    deadline = source['goal_snapshot']['spec'].get('deadline')
    for ordinal, item in enumerate(inputs['items']):
        due = _time(item['due_at'])
        need(due > now and (deadline is None or due <= _time(deadline)),
            '工作单期限须在当前时间之后，且不能超过原目标的明确截止时间', 422)
        assignee = real_member(db, p, item['assignee_id'])
        spec = {'id': m.uid(), 'ordinal': ordinal, **copy.deepcopy(item), 'assignee_name': assignee.username,
            'dispatcher_id': p.user_id, 'dispatcher_name': actor.username,
            'parent_owner_id': parent.owner_id, 'simulation_only': source['simulation_only']}
        state = _seal({'schema': ITEM, 'id': spec['id'], 'workspace_id': p.workspace_id, 'batch_id': batch_id,
            'parent_task_id': parent.id, 'spec': spec, 'version': 1, 'status': 'assigned', 'note': '',
            'updated_at': at, 'command_ids': []})
        _state(state); states.append(state)
    value = _seal({'schema': BATCH, 'id': batch_id, 'workspace_id': p.workspace_id,
        'parent_task_id': parent.id, 'invocation_id': call.id, 'job_id': current['job'].id,
        'actor_id': p.user_id, 'capability_id': cap.id, 'request_key': key, 'inputs': inputs,
        'input_hash': digest(inputs), 'approval': copy.deepcopy(approval), 'approval_hash': digest(approval),
        'source': source, 'created_at': at, 'initial_states': states})
    safe._bytes(value, MAX_RECORD_BYTES)
    batch = Batch(id=batch_id, workspace_id=p.workspace_id, version=1, parent_task_id=parent.id,
        invocation_id=call.id, job_id=current['job'].id, actor_id=p.user_id, capability_id=cap.id,
        record_hash=value['record_hash'], data=value)
    db.add(batch); db.flush()
    for state in states:
        db.add(Item(id=state['id'], workspace_id=p.workspace_id, batch_id=batch.id, parent_task_id=parent.id,
            assignee_id=state['spec']['assignee_id'], ordinal=state['spec']['ordinal'], status='assigned', version=1,
            record_hash=state['record_hash'], data=state))
        audit(db, p, CHANGED, state['id'], _anchor(state, None))
    call.data = {**call.data, CALL_KEY: _marker(value)}
    audit(db, p, CREATED, batch.id, {'schema': BATCH, 'batch_id': batch.id, 'record_hash': batch.record_hash})
    db.flush()
    _inventory(db, p)
    result = _receipt(value); validate(output_schema(), result)
    return result


def reconcile(db, p, cap, inputs, key, approval, call):
    current = validate_call_approval(db, p, cap, 'business', inputs, key, approval)
    actual = shared._call(db, p, current, key, inputs, approval, supplied=call, allow_unknown=True)
    _lock(db, p, write=True)
    batches, _, _, _ = _inventory(db, p)
    matched = [row for row in batches if row.invocation_id == actual.id]
    need(len(matched) == 1, '原派发批次未完整保存；仅核对原号，不补建或重复分派')
    return _receipt(matched[0].data)


def saved_receipt(db,p,cap,inputs,key,approval,call):
    """Cold-check existing assignment inventory; never grant or redispatch."""
    _iam(db,p); _lock(db,p)
    batches,_,_,_ = _inventory(db,p)
    matched = [row for row in batches if row.invocation_id == call.id]
    need(len(matched) == 1,'原派发库存无法核对；原调用保留，未重新派发')
    batch = matched[0]; value = batch.data
    need(call.status == 'succeeded' and batch.workspace_id == p.workspace_id
        and batch.actor_id == p.user_id and batch.capability_id == cap.id
        and call.idempotency_key == key == value['request_key']
        and _same(value['inputs'],_inputs(inputs)) and _same(value['approval'],approval))
    return _receipt(value)


def _readable(p, state):
    return p.user_id in {state['spec']['assignee_id'], state['spec']['dispatcher_id'], state['spec']['parent_owner_id']}


def _permissions(p, state):
    return {operation: p.role in {'admin', 'operator'} and _allowed(state, p.user_id, operation) for operation in sorted(OPERATIONS)}


def list_orders(db, p, *, mode='assigned', parent_task_id=None):
    _iam(db, p); need(mode in {'assigned', 'dispatched'}, status=422)
    if parent_task_id is not None: need(_uuid(parent_task_id), status=422)
    _lock(db, p); _, items, _, views = _inventory(db, p)
    result = []
    for row in items:
        spec = row.data['spec']
        selected = spec['assignee_id'] == p.user_id if mode == 'assigned' else p.user_id in {spec['dispatcher_id'], spec['parent_owner_id']}
        if selected and (parent_task_id is None or row.parent_task_id == parent_task_id):
            result.append({**views[row.id], 'permissions': _permissions(p, row.data)})
    return {'schema': 'workflow-work-order-list-v198', 'scope': SCOPE, 'mode': mode,
        'items': sorted(result, key=lambda item: (_time(item['due_at']), item['id'])), **FLAGS}


def get_order(db, p, item_id):
    need(_uuid(item_id), status=422); _iam(db, p); _lock(db, p)
    _, items, _, views = _inventory(db, p)
    matches = [row for row in items if row.id == item_id]
    need(len(matches) == 1, '工作单不存在或原库存无法核对', 404)
    need(_readable(p, matches[0].data), '请由原工作单负责人、派发人或原任务负责人读取', 403)
    return {**views[item_id], 'permissions': _permissions(p, matches[0].data)}


def perform(db, p, item_id, operation, raw, *, method, path):
    try: body = parse_wire(raw)
    except ValueError: raise HTTPException(422, '工作单原命令无法解析，输入保留') from None
    need(method == 'POST' and path == command_path(item_id, operation), status=422)
    actor = _iam(db, p, write=True)
    preliminary = db.scalar(select(Item).where(Item.workspace_id == p.workspace_id, Item.id == item_id))
    need(preliminary is not None)
    parent = db.scalar(select(m.Task).where(m.Task.workspace_id == p.workspace_id,
        m.Task.id == preliminary.parent_task_id).with_for_update().execution_options(populate_existing=True))
    need(parent is not None); _lock(db, p, write=True)
    _, items, commands, views = _inventory(db, p)
    sha = hashlib.sha256(raw).hexdigest()
    prior = [row for row in commands if row.request_id == body['request_id']]
    if prior:
        row = prior[0]
        need(row.actor_id == p.user_id and row.item_id == item_id and row.operation == operation
            and row.method == method and row.path == path and row.exact_body == raw.decode('utf-8')
            and row.body_sha256 == sha, '同一原请求不能更换工作单、账号、操作或正文')
        return copy.deepcopy(row.data['result'])
    item = next(row for row in items if row.id == item_id)
    if body['expected_version'] != item.version:
        raise HTTPException(409, {'code': 'workflow_work_order_version_conflict', 'not_applied': True,
            'current_version': item.version,'request_id':body['request_id'],'body_sha256':sha,
            'item_id':item_id,'operation':operation})
    need(item.version < MAX_HISTORY, '工作单历史已达容量，原记录保留，未截断')
    command_id = m.uid(); at = db.scalar(select(func.clock_timestamp())).isoformat()
    changed = _transition(item.data, body, p.user_id, operation, command_id, at)
    history = [*views[item.id]['history'], _history(changed, p.user_id, actor.username, operation, body['request_id'])]
    result = {'schema': RESULT, 'request_id': body['request_id'], 'item': _view(changed, history), **FLAGS}
    data = {'schema': COMMAND, 'actor_name': actor.username, 'previous_hash': item.record_hash,
        'next_state': changed, 'result': result}
    columns = {'id': command_id, 'workspace_id': p.workspace_id, 'item_id': item.id, 'parent_task_id': item.parent_task_id,
        'actor_id': p.user_id, 'request_id': body['request_id'], 'item_version': changed['version'],
        'operation': operation, 'method': method, 'path': path, 'exact_body': raw.decode('utf-8'), 'body_sha256': sha}
    record_hash = digest({**columns, **data})
    command = Command(**columns, version=1, record_hash=record_hash, data={**data, 'record_hash': record_hash})
    safe._bytes(command.data, MAX_RECORD_BYTES)
    item.data = changed; item.version = changed['version']; item.status = changed['status']; item.record_hash = changed['record_hash']
    db.add(command)
    audit(db, p, COMMANDED, command.request_id, _command_anchor(command))
    audit(db, p, CHANGED, item.id, _anchor(changed, command.id))
    db.flush(); _inventory(db, p)
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    return copy.deepcopy(result)


def recover(db, p, request_id, expected_sha256):
    need(_uuid(request_id) and _sha(expected_sha256), status=422)
    _iam(db, p); _lock(db, p)
    _, _, commands, _ = _inventory(db, p)
    matching = [row for row in commands if row.request_id == request_id]
    result = None
    if matching:
        row = matching[0]
        need(row.actor_id == p.user_id and row.body_sha256 == expected_sha256,
            '原工作单请求仅允许原账号与相同原正文核对', 403)
        result = copy.deepcopy(row.data['result'])
    return {'schema': REQUEST, 'request_id': request_id, 'found': result is not None,
        'body_sha256': expected_sha256, 'result': result}
