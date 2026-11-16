"""Recoverable incomplete bindings, separate from preview/adoption/execution.

Public functions: parse(raw), get(db,p,qualification_id,flow_id), save(db,p,raw),
recover(db,p,request_id,expected_sha256).  The exact seven-key command and
six-key value use Root's v1 contract; safe incomplete JSON is intentional.
No converter, defaults, provider, Job, material access or execution grant.

Lock order is nonce (save/recover) -> this actor -> sorted (qualification,flow)
-> sorted draft IDs -> immutable qualification inventory.  Readers acquire
shared locks and never upgrade.  Complete own State/Command/two-Audit inventory
is checked even for a missing nonce/scope.  Only a fresh save calls the real
qualifications.get and locks the current HTTP account/session for writing.
Original replies/recovery use stored inventory plus current read IAM, never
fresh materials or a renewed qualification.  SQL exceptions propagate; caller
owns rollback on failure.  No ``not_applied`` is issued after any row is added.
"""
import copy
import hashlib
import json
import re

from fastapi import HTTPException
from sqlalchemy import or_, select

from . import models as m
from . import business_flow_review_store as reviews
from . import business_flow_qualification_store as qualifications
from .business_flow_execution_draft_models import BusinessFlowExecutionDraft as Draft
from .business_flow_execution_draft_models import BusinessFlowExecutionDraftCommand as Command
from .contracts import digest
from .security import require
from .services import audit


STATE = 'business-flow-execution-draft-state-v1'
COMMAND = 'business-flow-execution-draft-command-record-v1'
COMMAND_SCHEMA = 'business-flow-execution-draft-command-v1'
VIEW = 'business-flow-execution-draft-view-v1'
RESULT = 'business-flow-execution-draft-save-result-v1'
REQUEST = 'business-flow-execution-draft-original-result-v1'
COMMAND_AUDIT = 'business_flow_execution_draft.command_v1'
STATE_AUDIT = 'business_flow_execution_draft.state_v1'
FLAGS = dict(reviews.FLAGS)
BODY_KEYS = {'schema', 'request_id', 'qualification_id', 'qualification_hash', 'flow_id', 'expected_version', 'value'}
VALUE_KEYS = {'business_name', 'execution_bindings', 'buffers', 'role_bindings', 'node_actions', 'external_node_ids'}
MAX_BYTES = 2 * 1024 * 1024
MAX_DRAFTS = 200
MAX_HISTORY = 1000
FLOW = re.compile(r'^[a-z][a-z0-9_-]{0,47}$')
ERROR = '未完成执行配置、原保存请求或独立审计无法核对；原输入和历史保留'


class InventoryError(HTTPException):
    def __init__(self): super().__init__(409, ERROR)


def need(ok, message=ERROR, code=409):
    if not ok: raise HTTPException(code, message)


def _identity(value): return reviews.identity(value)


def _flow(value):
    if type(value) is not str or FLOW.fullmatch(value) is None: raise ValueError(ERROR)
    return value


def _hash(value):
    if type(value) is not str or reviews.SHA.fullmatch(value) is None: raise ValueError(ERROR)
    return value


def _value(value):
    need(type(value) is dict and set(value) == VALUE_KEYS, code=422)
    need(type(value['business_name']) is str
        and all(type(value[key]) is dict for key in ('execution_bindings', 'buffers', 'role_bindings', 'node_actions'))
        and type(value['external_node_ids']) is list, code=422)
    reviews._bytes(value, MAX_BYTES)
    return copy.deepcopy(value)


def parse(raw):
    """Exact UTF-8 and closed keys, without Pydantic coercion or editor repair."""
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
        _identity(body['request_id']); _identity(body['qualification_id'])
        _hash(body['qualification_hash']); _flow(body['flow_id'])
        if type(body['expected_version']) is not int or body['expected_version'] < 0:
            raise ValueError(ERROR)
        _value(body['value']); reviews._bytes(body, MAX_BYTES)
        return body
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError, HTTPException):
        raise ValueError('执行草稿须为完整原JSON、严格版本及七个命令字段；不补默认值或截断输入') from None


def _nonce(db, p, request_id, *, shared=False):
    reviews._lock(db, {'business_flow_execution_draft_nonce': _identity(request_id),
        'workspace_id': p.workspace_id}, shared=shared)


def _scope(row):
    return (_identity(row.qualification_id), _flow(row.flow_id))


def _ref(command):
    return {'id': command.id, 'request_id': command.request_id,
        'version': command.draft_version, 'body_sha256': command.body_sha256}


def _snapshot(row, version, value_hash, refs):
    return {'schema': STATE, **{key: getattr(row, key) for key in ('id', 'workspace_id', 'actor_id',
        'qualification_id', 'qualification_hash', 'flow_id', 'request_id')},
        'version': version, 'value_hash': value_hash, 'command_refs': copy.deepcopy(refs)}


def _history_item(command, value_hash):
    return {'version': command.draft_version, 'request_id': command.request_id,
        'saved_at': reviews._utc(command.created_at), 'value_hash': value_hash}


def _view(row, history, *, value=None, version=None):
    return {'schema': VIEW, 'id': row.id, 'actor_id': row.actor_id, 'workspace_id': row.workspace_id,
        'qualification_id': row.qualification_id, 'qualification_hash': row.qualification_hash,
        'flow_id': row.flow_id, 'saved': True, 'version': row.version if version is None else version,
        'value': copy.deepcopy(row.data['value'] if value is None else value),
        'value_hash': history[-1]['value_hash'], 'saved_at': history[-1]['saved_at'],
        'history': copy.deepcopy(history), **FLAGS}


def _result(command, view):
    return {'schema': RESULT, 'request_id': command.request_id, 'body_sha256': command.body_sha256,
        'draft': copy.deepcopy(view), **FLAGS}


def _command_pin(row):
    return {**{key: copy.deepcopy(getattr(row, key)) for key in ('id', 'workspace_id', 'actor_id',
        'draft_id', 'qualification_id', 'qualification_hash', 'flow_id', 'draft_version', 'request_id',
        'version', 'operation', 'exact_body', 'body_sha256', 'body_hash', 'data')},
        'saved_at': reviews._utc(row.created_at)}


def _command_audit(command):
    return {'schema': COMMAND, 'draft_id': command.draft_id, 'actor_id': command.actor_id,
        'qualification_id': command.qualification_id, 'qualification_hash': command.qualification_hash,
        'flow_id': command.flow_id, 'request_id': command.request_id,
        'command_hash': command.command_hash, 'body_sha256': command.body_sha256}


def _state_audit(row, command):
    return {'schema': STATE, 'draft_id': row.id, 'actor_id': row.actor_id,
        'qualification_id': row.qualification_id, 'qualification_hash': row.qualification_hash,
        'flow_id': row.flow_id, 'request_id': command.request_id, 'version': command.draft_version,
        'command_id': command.id, 'previous_hash': command.data['previous_hash'],
        'state_hash': digest(command.data['record_snapshot'])}


def _history(row, commands, events, p):
    need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id
        and type(row.version) is int and 1 <= row.version <= MAX_HISTORY
        and type(row.data) is dict and set(row.data) == {'schema', 'value', 'command_refs'}
        and row.data['schema'] == STATE)
    for key in ('id', 'actor_id', 'workspace_id', 'qualification_id', 'request_id'): _identity(getattr(row, key))
    _flow(row.flow_id); _hash(row.qualification_hash); _hash(row.value_hash); _hash(row.record_hash)
    _value(row.data['value']); refs = row.data['command_refs']
    ordered = sorted(commands, key=lambda item: item.draft_version)
    need(type(refs) is list and len(refs) == row.version == len(ordered)
        and len(events) == row.version * 2 and [item.draft_version for item in ordered] == list(range(1, row.version + 1)))
    history, previous, used = [], None, set()
    for version, command in enumerate(ordered, 1):
        for key in ('id', 'draft_id', 'qualification_id', 'request_id'): _identity(getattr(command, key))
        need(command.workspace_id == p.workspace_id and command.actor_id == p.user_id
            and command.draft_id == row.id and command.qualification_id == row.qualification_id
            and command.qualification_hash == row.qualification_hash and command.flow_id == row.flow_id
            and type(command.version) is int and command.version == 1
            and type(command.draft_version) is int and command.draft_version == version
            and command.operation == 'save' and type(command.exact_body) is str
            and type(command.data) is dict and set(command.data) == {'schema', 'previous_hash', 'record_snapshot', 'result'}
            and command.data['schema'] == COMMAND)
        raw = command.exact_body.encode('utf-8', errors='strict'); body = parse(raw)
        value_hash = digest(body['value'])
        need(command.body_sha256 == hashlib.sha256(raw).hexdigest() and command.body_hash == digest(body)
            and command.command_hash == digest(_command_pin(command))
            and body['request_id'] == command.request_id and body['qualification_id'] == row.qualification_id
            and body['qualification_hash'] == row.qualification_hash and body['flow_id'] == row.flow_id
            and body['expected_version'] == version - 1 and (version != 1 or row.request_id == command.request_id)
            and command.data['previous_hash'] == previous and reviews._same(refs[version - 1], _ref(command)))
        history.append(_history_item(command, value_hash))
        snapshot = _snapshot(row, version, value_hash, refs[:version])
        need(reviews._same(command.data['record_snapshot'], snapshot)
            and reviews._same(command.data['result'], _result(command,
                _view(row, history, value=body['value'], version=version))))
        for action, object_id, expected in ((COMMAND_AUDIT, command.id, _command_audit(command)),
                (STATE_AUDIT, row.id, _state_audit(row, command))):
            candidates = [event for event in events if event.action == action and event.object_id == object_id
                and (action != STATE_AUDIT or event.data.get('version') == version)]
            need(len(candidates) == 1 and candidates[0].workspace_id == p.workspace_id
                and candidates[0].actor_id == p.user_id and reviews._same(candidates[0].data, expected))
            used.add(candidates[0].id)
        previous = digest(snapshot)
    need(used == {event.id for event in events} and row.record_hash == previous
        and row.value_hash == digest(row.data['value']) == history[-1]['value_hash'])
    reviews._bytes(row.data); return history


def _inventory(db, p, *, write=False, target_scope=None):
    """Discovery is three-sided and actor-serialized, including unknown reads."""
    try:
        reviews._lock(db, {'business_flow_execution_draft_actor': p.user_id,
            'workspace_id': p.workspace_id}, shared=not write)
        states = list(db.scalars(select(Draft).where(Draft.workspace_id == p.workspace_id,
            Draft.actor_id == p.user_id).execution_options(populate_existing=True)))
        commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
            Command.actor_id == p.user_id).execution_options(populate_existing=True)))
        events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action.in_([COMMAND_AUDIT, STATE_AUDIT]),
            or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
        need(len(states) <= MAX_DRAFTS and len(commands) <= MAX_DRAFTS * MAX_HISTORY
            and len(events) <= MAX_DRAFTS * MAX_HISTORY * 2)
        ids = {_identity(row.id) for row in states} | {_identity(row.draft_id) for row in commands}
        scopes = {_scope(row) for row in states} | {_scope(row) for row in commands}
        for event in events:
            need(type(event.data) is dict)
            ids.add(_identity(event.data['draft_id'])); scopes.add((_identity(event.data['qualification_id']), _flow(event.data['flow_id'])))
        if target_scope is not None: scopes.add((_identity(target_scope[0]), _flow(target_scope[1])))
        for qid, flow_id in sorted(scopes):
            reviews._lock(db, {'business_flow_execution_draft_scope': [qid, flow_id],
                'workspace_id': p.workspace_id, 'actor_id': p.user_id}, shared=not write)
        for draft_id in sorted(ids):
            reviews._lock(db, {'business_flow_execution_draft_row': draft_id,
                'workspace_id': p.workspace_id}, shared=not write)
        by_id, by_command = {row.id: row for row in states}, {row.id: row for row in commands}
        need(len(by_id) == len(states) and len(by_command) == len(commands) and set(by_id) == ids
            and len({row.request_id for row in commands}) == len(commands)
            and len({_scope(row) for row in states}) == len(states))
        histories = {row.id: _history(row, [item for item in commands if item.draft_id == row.id],
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
    need(len(commands) <= 1 and len(states) <= 1
        and all(row.actor_id == p.user_id for row in commands + states)
        and all(event.actor_id == p.user_id and type(event.data) is dict
            and event.data.get('actor_id') == p.user_id for event in events),
        '原草稿请求编号已使用或不属于当前账号；未建立替代记录')
    need(not (states or events) or commands, '原草稿请求仍有状态或审计但缺少命令；未作为新请求')
    if states and commands: need(states[0].id == commands[0].draft_id)


def _original(commands, body, raw):
    matches = [row for row in commands.values() if row.request_id == body['request_id']]
    need(len(matches) <= 1)
    if not matches: return None
    row = matches[0]
    need(row.exact_body == raw.decode('utf-8', errors='strict')
        and row.body_sha256 == hashlib.sha256(raw).hexdigest() and row.body_hash == digest(body)
        and row.qualification_id == body['qualification_id'] and row.qualification_hash == body['qualification_hash']
        and row.flow_id == body['flow_id'], '同一原编号的范围或字节不同；原命令未替换')
    return copy.deepcopy(row.data['result'])


def _qualification(db, p, qualification_id, flow_id):
    """Immutable author/source admission only; deliberately no fresh materials."""
    value = qualifications.get(db, p, qualification_id)
    need(value['id'] == qualification_id and value['reviewed_by'] == p.user_id
        and value['status'] == 'qualified' and value['human_review'] == 'author_self_reviewed'
        and all(value[key] is False for key in FLAGS))
    _hash(value['qualification_hash'])
    need(any(flow['flow_id'] == flow_id for flow in value['value']['business']['flows']),
        '这一作者核对版本不含所选流程；未换源或创建草稿', 404)
    return value


def get(db, p, qualification_id, flow_id):
    try: _identity(qualification_id); _flow(flow_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '作者核对编号或流程范围格式不完整；未创建草稿') from None
    reviews._iam(db, p)
    rows, _, histories = _inventory(db, p, target_scope=(qualification_id, flow_id))
    qualification = _qualification(db, p, qualification_id, flow_id)
    matches = [row for row in rows.values() if _scope(row) == (qualification_id, flow_id)]
    need(len(matches) <= 1)
    if matches:
        row = matches[0]; need(row.qualification_hash == qualification['qualification_hash'])
        return _view(row, histories[row.id])
    return {'schema': VIEW, 'id': None, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'qualification_id': qualification_id, 'qualification_hash': qualification['qualification_hash'],
        'flow_id': flow_id, 'saved': False, 'version': 0, 'value': None, 'value_hash': None,
        'saved_at': None, 'history': [], **FLAGS}


def recover(db, p, request_id, expected_sha256):
    try: _identity(request_id); _hash(expected_sha256)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '原草稿请求编号或SHA格式不完整；未猜测原请求') from None
    reviews._iam(db, p)
    _nonce(db, p, request_id, shared=True)
    _, commands, _ = _inventory(db, p); _nonce_owner(db, p, request_id)
    matches = [row for row in commands.values() if row.request_id == request_id]
    need(len(matches) <= 1)
    if matches: need(matches[0].body_sha256 == expected_sha256, '原请求摘要不同；未替换原草稿')
    return {'schema': REQUEST, 'request_id': request_id, 'found': bool(matches),
        'body_sha256': expected_sha256, 'result': copy.deepcopy(matches[0].data['result']) if matches else None}


def save(db, p, raw):
    try: body = parse(raw)
    except ValueError:
        raise HTTPException(422, '执行草稿原JSON、严格版本或完整字段无效；原输入保留') from None
    reviews._iam(db, p)
    _nonce(db, p, body['request_id'])
    rows, commands, histories = _inventory(db, p, write=True,
        target_scope=(body['qualification_id'], body['flow_id']))
    _nonce_owner(db, p, body['request_id'])
    previous_result = _original(commands, body, raw)
    if previous_result is not None: return previous_result
    require(p, 'admin', 'operator'); reviews._iam(db, p, write=True)
    qualification = _qualification(db, p, body['qualification_id'], body['flow_id'])
    need(qualification['qualification_hash'] == body['qualification_hash'], '作者核对来源不同；原输入保留')
    matches = [row for row in rows.values() if _scope(row) == (body['qualification_id'], body['flow_id'])]
    need(len(matches) <= 1)
    row = matches[0] if matches else None
    if row is not None: need(row.qualification_hash == body['qualification_hash'])
    current_version = row.version if row is not None else 0
    sha = hashlib.sha256(raw).hexdigest()
    if body['expected_version'] != current_version:
        raise HTTPException(409, {'code': 'business_flow_execution_draft_version_conflict',
            'not_applied': True, 'request_id': body['request_id'], 'body_sha256': sha,
            'current_version': current_version})
    need(current_version < MAX_HISTORY, '此草稿已达完整历史保留容量；原稿和所有原请求保留，未截断或新增保存', 413)
    need(row is not None or len(rows) < MAX_DRAFTS, '执行草稿超过完整保留容量；未覆盖既有输入', 413)
    # No add/flush occurred above.  CAS refusal is the only not_applied proof.
    if row is None:
        row = Draft(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
            qualification_id=body['qualification_id'], qualification_hash=body['qualification_hash'],
            flow_id=body['flow_id'], request_id=body['request_id'], version=1, created_at=m.now(),
            value_hash='', record_hash='', data={'schema': STATE, 'value': None, 'command_refs': []})
    version = current_version + 1
    command = Command(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id, draft_id=row.id,
        qualification_id=row.qualification_id, qualification_hash=row.qualification_hash, flow_id=row.flow_id,
        draft_version=version, request_id=body['request_id'], version=1, operation='save', created_at=m.now(),
        exact_body=raw.decode('utf-8', errors='strict'), body_sha256=sha, body_hash=digest(body))
    refs = copy.deepcopy(row.data['command_refs']) + [_ref(command)]
    value = _value(body['value']); value_hash = digest(value)
    snapshot = _snapshot(row, version, value_hash, refs)
    history = copy.deepcopy(histories[row.id]) if row.id in histories else []
    history.append(_history_item(command, value_hash))
    result = _result(command, _view(row, history, value=value, version=version))
    command.data = {'schema': COMMAND, 'previous_hash': row.record_hash if current_version else None,
        'record_snapshot': snapshot, 'result': result}
    command.command_hash = digest(_command_pin(command)); reviews._bytes(command.data)
    row.version = version; row.value_hash = value_hash; row.record_hash = digest(snapshot)
    row.data = {'schema': STATE, 'value': value, 'command_refs': refs}; reviews._bytes(row.data)
    # Explicit FK order within the SAME transaction; no committed orphan stage.
    db.add(row); db.flush(); db.add(command); db.flush()
    audit(db, p, COMMAND_AUDIT, command.id, _command_audit(command))
    audit(db, p, STATE_AUDIT, row.id, _state_audit(row, command)); db.flush()
    _inventory(db, p, write=True, target_scope=(row.qualification_id, row.flow_id))
    db.commit(); return copy.deepcopy(result)
