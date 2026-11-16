"""Durable unfinished whole-plan configuration, separate from execution.

Fresh saves check the actual immutable qualification inventory and current IAM,
never a report/provider/material or the claimed scope-binding hash. History and
original replies need only this store's full State/Command/two-Audit inventory.
Lock order: family nonce -> actor -> sorted qualification scopes -> sorted rows
-> qualification/review inventory. No reverse acquisition occurs in this store.
SQL errors propagate. Only pre-insert CAS refusal carries not_applied.
"""
import copy
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import or_, select

from . import models as m
from . import business_flow_review_store as reviews
from . import business_flow_qualification_store as qualifications
from . import business_flow_nested_review_v195 as nested
from .business_flow_nested_configuration_models import BusinessFlowNestedConfigurationDraft as Draft
from .business_flow_nested_configuration_models import BusinessFlowNestedConfigurationCommand as Command
from .contracts import digest
from .security import require
from .services import audit


PREFIX = '/api/setup/business-flow-executions'
STATE = 'business-flow-nested-configuration-state-v1'
COMMAND = 'business-flow-nested-configuration-command-record-v1'
COMMAND_SCHEMA = 'business-flow-nested-configuration-save-v1'
VIEW = 'business-flow-nested-configuration-view-v1'
RESULT = 'business-flow-nested-configuration-save-result-v1'
REQUEST = 'business-flow-nested-configuration-original-result-v1'
COMMAND_AUDIT = 'business_flow_nested_configuration.command_v1'
STATE_AUDIT = 'business_flow_nested_configuration.state_v1'
FLAGS = dict(reviews.FLAGS)
BODY_KEYS = {'schema', 'request_id', 'qualification_id', 'qualification_hash', 'expected_version',
    'source_plan_hash', 'scope_binding_hash', 'value'}
VALUE_KEYS = {'selected_flow_index', 'causal_bindings', 'flows', 'buffers', 'recovery'}
FLOW_KEYS = {'flow_index', 'business_name', 'execution_bindings', 'role_bindings',
    'node_actions', 'external_node_ids', 'entry_mode', 'buffers'}
FLOW_WITH_EXTRACTORS = FLOW_KEYS | {'observation_extractors'}
MAX_BYTES = 2 * 1024 * 1024
MAX_NODES = 100000
MAX_DEPTH = 40
MAX_DRAFTS = 200
MAX_HISTORY = 1000
ERROR = '递归执行草稿、原请求或独立审计无法核对；未覆盖输入、核实来源或授予执行资格'


class InventoryError(HTTPException):
    def __init__(self): super().__init__(409, ERROR)


def need(ok, message=ERROR, status=409):
    if not ok: raise HTTPException(status, message)


def _hash(value):
    if type(value) is not str or reviews.SHA.fullmatch(value) is None: raise ValueError(ERROR)
    return value


def _bounded(value, *, depth=MAX_DEPTH):
    pending = [(value, 0)]; count = 0
    while pending:
        item, level = pending.pop(); count += 1
        if count > MAX_NODES or level > depth: raise ValueError(ERROR)
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str: raise ValueError(ERROR)
                pending.append((key, level + 1)); pending.append((child, level + 1))
        elif type(item) is list:
            pending.extend((child, level + 1) for child in item)
    reviews._bytes(value, MAX_BYTES)
    return value


def _index(value, count=16):
    return type(value) is int and 0 <= value < count


def _value(value, *, flow_count=16):
    need(type(value) is dict and set(value) == VALUE_KEYS, status=422)
    need((value['selected_flow_index'] is None or _index(value['selected_flow_index'], flow_count))
        and (value['causal_bindings'] is None or type(value['causal_bindings']) is dict)
        and type(value['flows']) is list and len(value['flows']) <= 256
        and type(value['buffers']) is dict and type(value['recovery']) is list, status=422)
    for flow in value['flows']:
        need(type(flow) is dict and set(flow) in (FLOW_KEYS, FLOW_WITH_EXTRACTORS)
            and _index(flow['flow_index'], flow_count)
            and type(flow['business_name']) is str
            and (flow['execution_bindings'] is None or type(flow['execution_bindings']) is dict)
            and all(type(flow[key]) is dict for key in ('role_bindings', 'node_actions', 'buffers'))
            and type(flow['external_node_ids']) is list
            and (flow['entry_mode'] is None or type(flow['entry_mode']) is str), status=422)
        if 'observation_extractors' in flow:
            from .business_flow_nested_adoption_v195 import checked_extractor_selection
            checked_extractor_selection(flow['observation_extractors'], draft=True)
    _bounded(value)
    return copy.deepcopy(value)


def parse(raw):
    """Strict closed JSON; typed numbers, duplicates in arrays and text survive."""
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES: raise ValueError(ERROR)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result: raise ValueError(ERROR)
                result[key] = value
            return result
        def constant(_): raise ValueError(ERROR)
        body = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=constant)
        if type(body) is not dict or set(body) != BODY_KEYS or body['schema'] != COMMAND_SCHEMA: raise ValueError(ERROR)
        reviews.identity(body['request_id']); reviews.identity(body['qualification_id'])
        for key in ('qualification_hash', 'source_plan_hash', 'scope_binding_hash'): _hash(body[key])
        if type(body['expected_version']) is not int or body['expected_version'] < 0: raise ValueError(ERROR)
        _value(body['value']); _bounded(body, depth=MAX_DEPTH + 2)
        return body
    except (HTTPException, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError('递归草稿需要八个完整命令字段和有界原JSON；未补默认值、合并或截断') from None


def _nonce(db, p, request_id, *, shared=False):
    reviews._lock(db, {'business_flow_nested_configuration_nonce': reviews.identity(request_id),
        'workspace_id': p.workspace_id}, shared=shared)


def _ref(command):
    return {'id': command.id, 'request_id': command.request_id, 'version': command.draft_version,
        'body_sha256': command.body_sha256}


def _snapshot(row, version, value_hash, refs, scope_binding_hash):
    return {'schema': STATE, **{key: getattr(row, key) for key in ('id', 'workspace_id', 'actor_id',
        'qualification_id', 'qualification_hash', 'source_plan_hash', 'review_mode', 'simulation_only',
        'value_schema', 'flow_count', 'request_id')}, 'scope_binding_hash': scope_binding_hash,
        'version': version, 'value_hash': value_hash, 'command_refs': copy.deepcopy(refs)}


def _history_item(command, value_hash):
    return {'version': command.draft_version, 'request_id': command.request_id,
        'body_sha256': command.body_sha256, 'value_hash': value_hash, 'saved_at': reviews._utc(command.created_at)}


def _view(row, history, *, value=None, version=None, scope_binding_hash=None):
    return {'schema': VIEW, **{key: getattr(row, key) for key in ('id', 'actor_id', 'workspace_id',
        'qualification_id', 'qualification_hash', 'source_plan_hash', 'review_mode', 'simulation_only', 'value_schema')},
        'scope_binding_hash': row.scope_binding_hash if scope_binding_hash is None else scope_binding_hash,
        'saved': True, 'version': row.version if version is None else version,
        'value': copy.deepcopy(row.data['value'] if value is None else value),
        'value_hash': history[-1]['value_hash'], 'saved_at': history[-1]['saved_at'], 'history': copy.deepcopy(history),
        'current_source_checked': False, 'scope_binding_checked': False, **FLAGS}


def _result(command, view):
    return {'schema': RESULT, 'request_id': command.request_id, 'body_sha256': command.body_sha256,
        'draft': copy.deepcopy(view), **FLAGS}


def _command_pin(command):
    return {**{key: copy.deepcopy(getattr(command, key)) for key in ('id', 'workspace_id', 'actor_id',
        'draft_id', 'qualification_id', 'qualification_hash', 'source_plan_hash', 'scope_binding_hash',
        'draft_version', 'request_id', 'version', 'operation', 'exact_body', 'body_sha256', 'body_hash', 'data')},
        'saved_at': reviews._utc(command.created_at)}


def _command_audit(command):
    return {'schema': COMMAND, 'draft_id': command.draft_id, 'actor_id': command.actor_id,
        'qualification_id': command.qualification_id, 'qualification_hash': command.qualification_hash,
        'request_id': command.request_id, 'body_sha256': command.body_sha256, 'command_hash': command.command_hash}


def _state_audit(row, command):
    return {'schema': STATE, 'draft_id': row.id, 'actor_id': row.actor_id, 'qualification_id': row.qualification_id,
        'request_id': command.request_id, 'version': command.draft_version, 'command_id': command.id,
        'previous_hash': command.data['previous_hash'], 'state_hash': digest(command.data['record_snapshot'])}


def _mode(row):
    fields = nested.mode_fields(row.review_mode)
    need(row.simulation_only is fields['simulation_only'] and row.value_schema == fields['value_schema']
        and type(row.flow_count) is int and 1 <= row.flow_count <= 16)


def _history(row, commands, events, p):
    need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id and type(row.version) is int
        and 1 <= row.version <= MAX_HISTORY and type(row.data) is dict
        and set(row.data) == {'schema', 'value', 'command_refs'} and row.data['schema'] == STATE)
    for key in ('id', 'workspace_id', 'actor_id', 'qualification_id', 'request_id'): reviews.identity(getattr(row, key))
    for key in ('qualification_hash', 'source_plan_hash', 'scope_binding_hash', 'value_hash', 'record_hash'): _hash(getattr(row, key))
    _mode(row); _value(row.data['value'], flow_count=row.flow_count)
    refs = row.data['command_refs']; ordered = sorted(commands, key=lambda item: item.draft_version)
    need(type(refs) is list and len(refs) == row.version == len(ordered) and len(events) == row.version * 2
        and [item.draft_version for item in ordered] == list(range(1, row.version + 1)))
    history = []; previous = None; used = set()
    for version, command in enumerate(ordered, 1):
        for key in ('id', 'workspace_id', 'actor_id', 'draft_id', 'qualification_id', 'request_id'): reviews.identity(getattr(command, key))
        need(command.workspace_id == p.workspace_id and command.actor_id == p.user_id and command.draft_id == row.id
            and command.qualification_id == row.qualification_id and command.qualification_hash == row.qualification_hash
            and command.source_plan_hash == row.source_plan_hash and type(command.version) is int and command.version == 1
            and type(command.draft_version) is int and command.draft_version == version and command.operation == 'save'
            and type(command.exact_body) is str and type(command.data) is dict
            and set(command.data) == {'schema', 'previous_hash', 'record_snapshot', 'result'} and command.data['schema'] == COMMAND)
        raw = command.exact_body.encode('utf-8', errors='strict'); body = parse(raw)
        _value(body['value'], flow_count=row.flow_count); _hash(command.scope_binding_hash)
        value_hash = digest(body['value'])
        need(command.body_sha256 == hashlib.sha256(raw).hexdigest() and command.body_hash == digest(body)
            and command.command_hash == digest(_command_pin(command))
            and body['request_id'] == command.request_id and body['qualification_id'] == row.qualification_id
            and body['qualification_hash'] == row.qualification_hash and body['source_plan_hash'] == row.source_plan_hash
            and body['scope_binding_hash'] == command.scope_binding_hash and body['expected_version'] == version - 1
            and (version != 1 or row.request_id == command.request_id)
            and command.data['previous_hash'] == previous and reviews._same(refs[version - 1], _ref(command)))
        history.append(_history_item(command, value_hash))
        snapshot = _snapshot(row, version, value_hash, refs[:version], command.scope_binding_hash)
        need(reviews._same(command.data['record_snapshot'], snapshot) and reviews._same(command.data['result'],
            _result(command, _view(row, history, value=body['value'], version=version, scope_binding_hash=command.scope_binding_hash))))
        for action, object_id, expected in ((COMMAND_AUDIT, command.id, _command_audit(command)),
            (STATE_AUDIT, row.id, _state_audit(row, command))):
            candidates = [event for event in events if event.action == action and event.object_id == object_id
                and (action != STATE_AUDIT or event.data.get('version') == version)]
            need(len(candidates) == 1 and candidates[0].workspace_id == p.workspace_id
                and candidates[0].actor_id == p.user_id and reviews._same(candidates[0].data, expected))
            used.add(candidates[0].id)
        previous = digest(snapshot)
    need(used == {event.id for event in events} and row.record_hash == previous
        and row.value_hash == digest(row.data['value']) == history[-1]['value_hash']
        and row.scope_binding_hash == ordered[-1].scope_binding_hash)
    reviews._bytes(row.data)
    return history


def _inventory(db, p, *, write=False, target=None):
    try:
        reviews._lock(db, {'business_flow_nested_configuration_actor': p.user_id, 'workspace_id': p.workspace_id}, shared=not write)
        states = list(db.scalars(select(Draft).where(Draft.workspace_id == p.workspace_id,
            Draft.actor_id == p.user_id).execution_options(populate_existing=True)))
        commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
            Command.actor_id == p.user_id).execution_options(populate_existing=True)))
        events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action.in_([COMMAND_AUDIT, STATE_AUDIT]),
            or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
        need(len(states) <= MAX_DRAFTS and len(commands) <= MAX_DRAFTS * MAX_HISTORY
            and len(events) <= MAX_DRAFTS * MAX_HISTORY * 2)
        ids = {reviews.identity(row.id) for row in states} | {reviews.identity(row.draft_id) for row in commands}
        scopes = {reviews.identity(row.qualification_id) for row in states + commands}
        for event in events:
            need(type(event.data) is dict); ids.add(reviews.identity(event.data['draft_id']))
            scopes.add(reviews.identity(event.data['qualification_id']))
        if target is not None: scopes.add(reviews.identity(target))
        for qid in sorted(scopes):
            reviews._lock(db, {'business_flow_nested_configuration_scope': qid,
                'workspace_id': p.workspace_id, 'actor_id': p.user_id}, shared=not write)
        for identity in sorted(ids):
            reviews._lock(db, {'business_flow_nested_configuration_row': identity, 'workspace_id': p.workspace_id}, shared=not write)
        by_id = {row.id: row for row in states}; by_command = {row.id: row for row in commands}
        need(len(by_id) == len(states) and len(by_command) == len(commands) and set(by_id) == ids
            and len({row.request_id for row in commands}) == len(commands)
            and len({row.qualification_id for row in states}) == len(states))
        histories = {row.id: _history(row, [command for command in commands if command.draft_id == row.id],
            [event for event in events if event.data['draft_id'] == row.id], p) for row in states}
        need(all(command.draft_id in by_id for command in commands))
        return by_id, by_command, histories
    except InventoryError: raise
    except HTTPException as error: raise InventoryError() from error
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise InventoryError() from None


def _nonce_owner(db, p, request_id):
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.request_id == request_id).execution_options(populate_existing=True)))
    states = list(db.scalars(select(Draft).where(Draft.workspace_id == p.workspace_id,
        or_(Draft.request_id == request_id, Draft.data['command_refs'].contains([{'request_id': request_id}])))))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([COMMAND_AUDIT, STATE_AUDIT]), m.Audit.data['request_id'].as_string() == request_id)))
    need(len(commands) <= 1 and len(states) <= 1 and all(row.actor_id == p.user_id for row in commands + states)
        and all(event.actor_id == p.user_id and type(event.data) is dict and event.data.get('actor_id') == p.user_id for event in events),
        '原递归草稿编号不属于当前账号或库存冲突；未建立替代记录')
    need(not (states or events) or commands, '原草稿仍有状态或审计但缺少命令；未按新请求处理')
    if states and commands: need(states[0].id == commands[0].draft_id)


def _original(commands, body, raw):
    matches = [command for command in commands.values() if command.request_id == body['request_id']]
    need(len(matches) <= 1)
    if not matches: return None
    command = matches[0]
    need(command.exact_body == raw.decode('utf-8', errors='strict')
        and command.body_sha256 == hashlib.sha256(raw).hexdigest() and command.body_hash == digest(body)
        and command.qualification_id == body['qualification_id'] and command.qualification_hash == body['qualification_hash']
        and command.source_plan_hash == body['source_plan_hash'] and command.scope_binding_hash == body['scope_binding_hash'],
        '同原号的正文或来源范围不同；未替换已存请求')
    return copy.deepcopy(command.data['result'])


def _qualification(db, p, identity):
    value = qualifications.get(db, p, identity)
    need(value.get('schema') == nested.RESULT and value.get('id') == identity and value.get('reviewed_by') == p.user_id
        and all(value.get(key) is False for key in FLAGS))
    fields = nested.mode_fields(value['review_mode'])
    need(all(type(value.get(key)) is type(item) and value[key] == item for key, item in fields.items())
        and value['status'] == nested.status(value['review_mode']))
    checked = nested.check_value(value['value'], value['source_binding'], mode=value['review_mode'])
    _hash(value['qualification_hash'])
    return value, len(checked['business']['flows'])


def get(db, p, qualification_id):
    try: reviews.identity(qualification_id)
    except (ValueError, TypeError, AttributeError): raise HTTPException(422, '核对记录编号格式不完整；未创建草稿') from None
    reviews._iam(db, p)
    rows, _, histories = _inventory(db, p, target=qualification_id)
    qualification, flow_count = _qualification(db, p, qualification_id)
    found = [row for row in rows.values() if row.qualification_id == qualification_id]
    need(len(found) <= 1)
    if found:
        row = found[0]
        need(row.qualification_hash == qualification['qualification_hash'] and row.source_plan_hash == digest(qualification['value'])
            and row.review_mode == qualification['review_mode'] and row.simulation_only is qualification['simulation_only']
            and row.flow_count == flow_count)
        return _view(row, histories[row.id])
    return {'schema': VIEW, 'id': None, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'qualification_id': qualification_id, 'qualification_hash': qualification['qualification_hash'],
        'source_plan_hash': digest(qualification['value']), 'scope_binding_hash': None,
        'saved': False, 'version': 0, 'value': None, 'value_hash': None, 'saved_at': None, 'history': [],
        'review_mode': qualification['review_mode'], 'simulation_only': qualification['simulation_only'],
        'value_schema': qualification['value_schema'], 'current_source_checked': False, 'scope_binding_checked': False, **FLAGS}


def recover(db, p, request_id, expected_sha256):
    try: reviews.identity(request_id); _hash(expected_sha256)
    except (ValueError, TypeError, AttributeError): raise HTTPException(422, '原编号或SHA格式不完整；未猜测请求') from None
    reviews._iam(db, p); _nonce(db, p, request_id, shared=True)
    _, commands, _ = _inventory(db, p); _nonce_owner(db, p, request_id)
    found = [command for command in commands.values() if command.request_id == request_id]
    need(len(found) <= 1)
    if found: need(found[0].body_sha256 == expected_sha256, '原请求摘要不同；原稿保留')
    return {'schema': REQUEST, 'request_id': request_id, 'found': bool(found), 'body_sha256': expected_sha256,
        'result': copy.deepcopy(found[0].data['result']) if found else None}


def save(db, p, raw):
    try: body = parse(raw)
    except ValueError: raise HTTPException(422, '递归草稿原JSON或字段无效；原输入保留') from None
    reviews._iam(db, p); _nonce(db, p, body['request_id'])
    rows, commands, histories = _inventory(db, p, write=True, target=body['qualification_id'])
    _nonce_owner(db, p, body['request_id'])
    original = _original(commands, body, raw)
    if original is not None: return original
    require(p, 'admin', 'operator'); reviews._iam(db, p, write=True)
    qualification, flow_count = _qualification(db, p, body['qualification_id'])
    need(qualification['qualification_hash'] == body['qualification_hash']
        and digest(qualification['value']) == body['source_plan_hash'], '明确资格或源计划不同；未移源覆盖原槽位')
    value = _value(body['value'], flow_count=flow_count)
    found = [row for row in rows.values() if row.qualification_id == body['qualification_id']]
    need(len(found) <= 1); row = found[0] if found else None
    if row is not None:
        need(row.qualification_hash == body['qualification_hash'] and row.source_plan_hash == body['source_plan_hash']
            and row.review_mode == qualification['review_mode'] and row.simulation_only is qualification['simulation_only']
            and row.flow_count == flow_count)
    current = row.version if row is not None else 0; sha = hashlib.sha256(raw).hexdigest()
    if body['expected_version'] != current:
        raise HTTPException(409, {'code': 'business_flow_nested_configuration_version_conflict', 'not_applied': True,
            'request_id': body['request_id'], 'body_sha256': sha, 'current_version': current})
    need(current < MAX_HISTORY and (row is not None or len(rows) < MAX_DRAFTS),
        '已达完整历史保留容量；原草稿和原编号保留，未截断或覆盖', 413)
    if row is None:
        row = Draft(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
            qualification_id=body['qualification_id'], qualification_hash=body['qualification_hash'],
            source_plan_hash=body['source_plan_hash'], scope_binding_hash=body['scope_binding_hash'],
            review_mode=qualification['review_mode'], simulation_only=qualification['simulation_only'],
            value_schema=qualification['value_schema'], flow_count=flow_count, request_id=body['request_id'],
            version=1, created_at=m.now(), value_hash='', record_hash='', data={'schema': STATE, 'value': None, 'command_refs': []})
    version = current + 1
    command = Command(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id, draft_id=row.id,
        qualification_id=row.qualification_id, qualification_hash=row.qualification_hash,
        source_plan_hash=row.source_plan_hash, scope_binding_hash=body['scope_binding_hash'], draft_version=version,
        request_id=body['request_id'], version=1, operation='save', created_at=m.now(),
        exact_body=raw.decode('utf-8', errors='strict'), body_sha256=sha, body_hash=digest(body))
    refs = copy.deepcopy(row.data['command_refs']) + [_ref(command)]; value_hash = digest(value)
    snapshot = _snapshot(row, version, value_hash, refs, command.scope_binding_hash)
    history = copy.deepcopy(histories[row.id]) if row.id in histories else []
    history.append(_history_item(command, value_hash))
    result = _result(command, _view(row, history, value=value, version=version, scope_binding_hash=command.scope_binding_hash))
    command.data = {'schema': COMMAND, 'previous_hash': row.record_hash if current else None,
        'record_snapshot': snapshot, 'result': result}
    command.command_hash = digest(_command_pin(command)); reviews._bytes(command.data)
    row.version = version; row.value_hash = value_hash; row.scope_binding_hash = command.scope_binding_hash
    row.record_hash = digest(snapshot); row.data = {'schema': STATE, 'value': value, 'command_refs': refs}
    reviews._bytes(row.data)
    db.add(row); db.flush(); db.add(command); db.flush()
    audit(db, p, COMMAND_AUDIT, command.id, _command_audit(command))
    audit(db, p, STATE_AUDIT, row.id, _state_audit(row, command)); db.flush()
    _inventory(db, p, write=True, target=row.qualification_id)
    db.commit()
    return copy.deepcopy(result)
