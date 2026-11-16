"""Durable incomplete private edits; no source, review or execution authority.

Root's authenticated API must cold-check the actual attempt/native capture and
provide source_binding, its original decoded baseline_value and diagnostics.
This store NEVER loads a provider/report, decides source authority, repairs an
answer or calls a semantic validator. History/replay need only saved storage
and current IAM. Safe incomplete values (including {} or null) are intentional.
All SQL errors propagate; the caller owns the transaction on failure.
"""
import copy
import hashlib
import json
import re
from datetime import timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select, text

from . import models as m
from .business_flow_review_models import BusinessFlowReviewCommand as Command
from .business_flow_review_models import BusinessFlowReviewDraft as Draft
from .contracts import digest
from .relations import lock_current_actor
from .report_full_reading_semantics import _safe
from .report_scoped_design_models_v181 import ReportScopedDesignAttemptV181 as Attempt
from .security import require
from .services import audit
from .typed_payload_codec_v131 import canonical_json

PREFIX = '/api/setup/business-flow-reviews'
STATE = 'business-flow-review-state-v1'
COMMAND = 'business-flow-review-command-v1'
VIEW = 'business-flow-review-view-v1'
RESULT = 'business-flow-review-save-result-v1'
REQUEST = 'business-flow-review-original-result-v1'
COMMAND_AUDIT = 'business_flow_review.command_v1'
STATE_AUDIT = 'business_flow_review.state_v1'
FLAGS = {'semantic_quality_verified': False, 'can_apply': False, 'can_publish': False, 'can_execute': False}
MAX_WIRE_BYTES = 2 * 1024 * 1024
MAX_VALUE_BYTES = 1024 * 1024
MAX_DIAGNOSTICS_BYTES = 8 * 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024
# A checked scope has at most 1920 original claims, each with 16 refs.
MAX_SOURCE_EVIDENCE = 1920 * 16
MAX_DRAFTS = 200
MAX_HISTORY = 1000
SHA = re.compile(r'^[a-f0-9]{64}$')
ERROR = '未完成人工稿、原请求或独立审计无法核对；原回答和已存人稿保留'
SOURCE_KEYS = set(('schema attempt_id producer_id revision model_id request_hash reserved_call_id '
    'capture_hash response_body_sha256 arguments_sha256 source_snapshot_hash generation_profile').split())
DIAGNOSTIC_KEYS = {'schema', 'available', 'value', 'issues', 'source_evidence', 'structural_valid', 'source_valid', *FLAGS}


class InventoryError(HTTPException):
    def __init__(self): super().__init__(409, ERROR)


def need(ok, message=ERROR, code=409):
    if not ok: raise HTTPException(code, message)


def identity(value):
    if type(value) is not str or str(UUID(value)) != value: raise ValueError(ERROR)
    return value


def _bytes(value, maximum=MAX_RECORD_BYTES):
    _safe(value)
    result = canonical_json(value).encode('utf-8', errors='strict')
    if len(result) > maximum: raise ValueError(ERROR)
    return result


def _same(left, right): return _bytes(left) == _bytes(right)


def _source(value, attempt_id):
    need(type(value) is dict)
    component = value.get('schema') == 'business-flow-component-review-source-v186'
    nested_component = value.get('schema') == 'business-flow-nested-component-review-source-v194'
    nested = value.get('schema') == 'business-flow-nested-review-source-v193' or nested_component
    added = ({'component_path','component_value_hash','component_validation_hash','original_protocol_valid'} if component else
        {'value_schema','component_path','component_value_hash','component_validation_hash','original_protocol_valid'} if nested_component else
        {'value_schema'} if nested else set())
    need(set(value) == SOURCE_KEYS | added
        and (component or nested or value['schema'] == 'business-flow-review-source-v1'))
    if nested:
        need(value['generation_profile'] == 'flow-v193'
            and value['value_schema'] == 'report-business-flow-nested-plan-v192')
        if nested_component:
            need(value['component_path'] == '/plan' and value['original_protocol_valid'] is False
                and all(type(value[k]) is str and SHA.fullmatch(value[k]) is not None
                    for k in ('component_value_hash','component_validation_hash')))
    else:
        need(value['generation_profile'] != 'flow-v193')
    if component:
        need(value['generation_profile'] == 'flow-v185' and value['component_path'] == '/revised_plan'
            and value['original_protocol_valid'] is False
            and all(type(value[k]) is str and SHA.fullmatch(value[k]) is not None
                for k in ('component_value_hash','component_validation_hash')))
    _bytes(value, 8192)
    for key in ('attempt_id', 'producer_id', 'model_id', 'reserved_call_id'): identity(value[key])
    for key in ('request_hash', 'capture_hash', 'response_body_sha256', 'arguments_sha256', 'source_snapshot_hash'):
        need(type(value[key]) is str and SHA.fullmatch(value[key]) is not None)
    need(value['attempt_id'] == attempt_id and type(value['revision']) is int and value['revision'] >= 1
        and type(value['generation_profile']) is str and value['generation_profile'] in {'flow-v182', 'flow-v183', 'flow-v184', 'flow-v185', 'flow-v187', 'flow-v190', 'flow-v193'})
    return copy.deepcopy(value)


def _diagnostics(value, draft_value):
    need(type(value) is dict and set(value) == DIAGNOSTIC_KEYS
        and value['schema'] == 'business-flow-review-diagnostics-v1')
    _bytes(value, MAX_DIAGNOSTICS_BYTES)
    need(all(value[key] is False for key in FLAGS)
        and all(type(value[key]) is bool for key in ('available', 'structural_valid', 'source_valid'))
        and _same(value['value'], draft_value)
        and type(value['issues']) is list and len(value['issues']) <= 4096
        and type(value['source_evidence']) is list and len(value['source_evidence']) <= MAX_SOURCE_EVIDENCE)
    for item in value['issues']:
        need(type(item) is dict and set(item) == {'code', 'path', 'message', 'span_ref'}
            and all(type(item[key]) is str for key in ('code', 'path', 'message'))
            and (item['span_ref'] is None or type(item['span_ref']) is str))
    for item in value['source_evidence']:
        need(type(item) is dict and set(item) == {'span_ref', 'source_text', 'text_origin'}
            and type(item['span_ref']) is str and type(item['source_text']) is str
            and item['text_origin'] == 'platform_resolved_source')
    return copy.deepcopy(value)


def save_path(attempt_id): return PREFIX + '/attempts/' + identity(attempt_id) + '/save'


def parse_wire(raw):
    """Only original exact UTF-8 JSON; no defaults/coercion/duplicate keys."""
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_WIRE_BYTES: raise ValueError(ERROR)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result: raise ValueError(ERROR)
                result[key] = value
            return result
        def constant(_): raise ValueError(ERROR)
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=constant)
        if type(value) is not dict or set(value) != {'request_id', 'expected_version', 'value', 'note'}:
            raise ValueError(ERROR)
        identity(value['request_id'])
        if type(value['expected_version']) is not int or not 0 <= value['expected_version'] < MAX_HISTORY:
            raise ValueError(ERROR)
        if type(value['note']) is not str or len(value['note']) > 12000: raise ValueError(ERROR)
        _bytes(value['value'], MAX_VALUE_BYTES); _bytes(value)
        return value
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError('人工稿须为安全有界JSON、原请求编号和严格版本；未改写或覆盖原稿') from None


def _lock(db, key, *, shared=False):
    name = 'pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
    db.execute(text('SELECT ' + name + '(:k)'), {'k': int(digest(key)[:15], 16)})


def _nonce(db, p, request_id, *, shared=False):
    _lock(db, {'business_flow_review_nonce': identity(request_id), 'workspace_id': p.workspace_id}, shared=shared)


def _iam(db, p, *, write=False):
    require(p, 'admin', 'operator', 'viewer')
    if write:
        require(p, 'admin', 'operator'); lock_current_actor(db, p); return
    query = select(m.User, m.Membership).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.User.id == p.user_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
        m.Membership.role == p.role)
    try: session_id = identity(getattr(p, 'session_id', None))
    except (ValueError, TypeError, AttributeError): session_id = None
    if session_id is not None:
        query = query.add_columns(m.AuthSession).join(m.AuthSession, m.AuthSession.user_id == m.User.id).where(
            m.AuthSession.id == session_id, m.AuthSession.revoked.is_(False), m.AuthSession.expires_at > m.now())
    need(db.execute(query).first() is not None, '当前账号、工作区角色或会话已变化；已存人稿保留', 403)


def _utc(value):
    need(value is not None and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat()


def _command_pin(row):
    return {**{key: copy.deepcopy(getattr(row, key)) for key in ('id', 'workspace_id', 'actor_id',
        'draft_id', 'attempt_id', 'draft_version', 'request_id', 'operation', 'method', 'path',
        'exact_body', 'body_sha256', 'body_hash', 'data')}, 'saved_at': _utc(row.created_at)}


def _snapshot(row, value, note, diagnostics, refs, version):
    return {'schema': STATE, 'id': row.id, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
        'attempt_id': row.attempt_id, 'request_id': row.request_id, 'version': version, 'status': 'incomplete',
        'source_binding_hash': row.source_binding_hash, 'baseline_hash': row.baseline_hash,
        'value_hash': digest(value), 'note_hash': digest(note), 'diagnostics_hash': digest(diagnostics),
        'command_refs': copy.deepcopy(refs)}


def _result(row, request_id, value, note, diagnostics, version):
    return {'schema': RESULT, 'id': row.id, 'attempt_id': row.attempt_id,
        'request_id': request_id, 'version': version, 'status': 'incomplete',
        'source_binding': copy.deepcopy(row.data['source_binding']),
        'baseline_value': copy.deepcopy(row.data['baseline_value']),
        'value': copy.deepcopy(value), 'note': note, 'diagnostics': copy.deepcopy(diagnostics),
        'source_binding_hash': row.source_binding_hash, 'baseline_hash': row.baseline_hash, **FLAGS}


def _command_audit(command):
    return {'schema': COMMAND, 'draft_id': command.draft_id, 'attempt_id': command.attempt_id,
        'actor_id': command.actor_id, 'request_id': command.request_id, 'command_hash': command.command_hash}


def _state_audit(row, command):
    return {'schema': STATE, 'draft_id': row.id, 'attempt_id': row.attempt_id, 'actor_id': row.actor_id,
        'request_id': command.request_id, 'version': command.draft_version, 'command_id': command.id,
        'previous_hash': command.data['previous_hash'], 'state_hash': digest(command.data['record_snapshot'])}


def _history(row, commands, events, p):
    need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id
        and type(row.version) is int and 1 <= row.version <= MAX_HISTORY and row.status == 'incomplete'
        and type(row.data) is dict and set(row.data) == {'schema', 'source_binding', 'baseline_value',
            'value', 'note', 'diagnostics', 'command_refs'} and row.data['schema'] == STATE)
    for field in ('id', 'workspace_id', 'actor_id', 'attempt_id', 'request_id'): identity(getattr(row, field))
    _source(row.data['source_binding'], row.attempt_id); _bytes(row.data['baseline_value'], MAX_VALUE_BYTES)
    need(row.source_binding_hash == digest(row.data['source_binding']) and row.baseline_hash == digest(row.data['baseline_value']))
    refs = row.data['command_refs']; ordered = sorted(commands, key=lambda item: item.draft_version)
    need(type(refs) is list and len(refs) == row.version == len(ordered) and len(events) == row.version * 2)
    need([item.draft_version for item in ordered] == list(range(1, row.version + 1)))
    previous = None; used = set(); history = []
    for version, command in enumerate(ordered, 1):
        for field in ('id', 'draft_id', 'attempt_id', 'request_id'): identity(getattr(command, field))
        need(command.workspace_id == p.workspace_id and command.actor_id == p.user_id
            and type(command.draft_version) is int
            and command.draft_id == row.id and command.attempt_id == row.attempt_id
            and command.operation == 'save' and command.method == 'POST' and command.path == save_path(row.attempt_id)
            and type(command.exact_body) is str and type(command.data) is dict
            and set(command.data) == {'schema', 'previous_hash', 'source_binding_hash', 'baseline_hash',
                'record_snapshot', 'result'} and command.data['schema'] == COMMAND)
        raw = command.exact_body.encode('utf-8', errors='strict'); body = parse_wire(raw)
        data = command.data; saved = data['result']
        need(type(saved) is dict)
        diag = _diagnostics(saved['diagnostics'], saved['value'])
        need(command.body_sha256 == hashlib.sha256(raw).hexdigest() and command.body_hash == digest(body)
            and command.command_hash == digest(_command_pin(command))
            and body['request_id'] == command.request_id and body['expected_version'] == version - 1
            and (version != 1 or command.request_id == row.request_id)
            and _same(body['value'], saved['value']) and _same(body['note'], saved['note'])
            and data['source_binding_hash'] == row.source_binding_hash and data['baseline_hash'] == row.baseline_hash
            and data['previous_hash'] == previous
            and _same(refs[version-1], {'id': command.id, 'request_id': command.request_id,
                'version': version, 'body_sha256': command.body_sha256}))
        snapshot = _snapshot(row, saved['value'], saved['note'], diag, refs[:version], version)
        need(_same(data['record_snapshot'], snapshot)
            and _same(saved, _result(row, command.request_id, saved['value'], saved['note'], diag, version)))
        for action, object_id, expected in ((COMMAND_AUDIT, command.id, _command_audit(command)),
                (STATE_AUDIT, row.id, _state_audit(row, command))):
            candidates = [event for event in events if event.action == action and event.object_id == object_id
                and (action != STATE_AUDIT or event.data.get('version') == version)]
            need(len(candidates) == 1 and candidates[0].workspace_id == p.workspace_id
                and candidates[0].actor_id == p.user_id and _same(candidates[0].data, expected))
            used.add(candidates[0].id)
        previous = digest(snapshot)
        history.append({'version': version, 'request_id': command.request_id, 'saved_at': _utc(command.created_at),
            'value': copy.deepcopy(saved['value']), 'note': saved['note'], 'diagnostics': copy.deepcopy(diag)})
    need(used == {event.id for event in events} and row.record_hash == previous
        and _same(row.data['value'], ordered[-1].data['result']['value'])
        and _same(row.data['note'], ordered[-1].data['result']['note'])
        and _same(row.data['diagnostics'], ordered[-1].data['result']['diagnostics']))
    _bytes(row.data); return history


def _inventory(db, p, *, write=False, target_attempt=None):
    """All writers share nonce -> actor -> sorted attempt -> sorted draft locks."""
    try:
        _lock(db, {'business_flow_review_actor': p.user_id, 'workspace_id': p.workspace_id}, shared=not write)
        rows = list(db.scalars(select(Draft).where(Draft.workspace_id == p.workspace_id,
            Draft.actor_id == p.user_id).execution_options(populate_existing=True)))
        commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
            Command.actor_id == p.user_id).execution_options(populate_existing=True)))
        events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action.in_([COMMAND_AUDIT, STATE_AUDIT]),
            or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
        need(len(rows) <= MAX_DRAFTS and len(commands) <= MAX_DRAFTS * MAX_HISTORY
            and len(events) <= MAX_DRAFTS * MAX_HISTORY * 2)
        ids = {identity(row.id) for row in rows} | {identity(item.draft_id) for item in commands}
        attempts = {identity(row.attempt_id) for row in rows} | {identity(item.attempt_id) for item in commands}
        for event in events:
            need(type(event.data) is dict)
            ids.add(identity(event.data['draft_id'])); attempts.add(identity(event.data['attempt_id']))
        if target_attempt is not None: attempts.add(identity(target_attempt))
        for value in sorted(attempts): _lock(db, {'business_flow_review_attempt': value,
            'workspace_id': p.workspace_id}, shared=not write)
        for value in sorted(ids): _lock(db, {'business_flow_review_draft': value,
            'workspace_id': p.workspace_id}, shared=not write)
        by_id = {row.id: row for row in rows}; by_command = {row.id: row for row in commands}
        need(len(by_id) == len(rows) and set(by_id) == ids and len(by_command) == len(commands)
            and len({row.attempt_id for row in rows}) == len(rows)
            and len({item.request_id for item in commands}) == len(commands))
        histories = {row.id: _history(row, [item for item in commands if item.draft_id == row.id],
            [event for event in events if event.data['draft_id'] == row.id], p) for row in rows}
        need(all(item.draft_id in by_id for item in commands))
        return by_id, by_command, histories
    except InventoryError: raise
    except HTTPException as error: raise InventoryError() from error
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise InventoryError() from None


def _nonce_owner(db, p, request_id):
    # Workspace-wide evidence prevents another actor, or a lost Command row,
    # from turning a previously used nonce into fresh write authority.
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.request_id == request_id).execution_options(populate_existing=True)))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([COMMAND_AUDIT, STATE_AUDIT]), m.Audit.data['request_id'].as_string() == request_id)))
    drafts = list(db.scalars(select(Draft).where(Draft.workspace_id == p.workspace_id,
        or_(Draft.request_id == request_id,
            Draft.data['command_refs'].contains([{'request_id': request_id}])))))
    need(len(commands) <= 1 and len(drafts) <= 1
        and all(item.actor_id == p.user_id for item in commands)
        and all(row.actor_id == p.user_id for row in drafts)
        and all(event.actor_id == p.user_id and type(event.data) is dict
            and event.data.get('actor_id') == p.user_id for event in events),
        '原请求编号已使用或不属于当前账号；未另建或覆盖')
    need(not (events or drafts) or commands, '原请求有人工稿或独立审计但缺少原命令；未作为新请求重建')
    if drafts and commands:
        need(drafts[0].id == commands[0].draft_id and drafts[0].attempt_id == commands[0].attempt_id)


def _read_command(commands, attempt_id, body, raw, method, path):
    matching = [item for item in commands.values() if item.request_id == body['request_id']]
    need(len(matching) <= 1)
    if not matching: return None
    command = matching[0]
    need(command.attempt_id == attempt_id and command.method == method and command.path == path
        and command.exact_body == raw.decode('utf-8', errors='strict')
        and command.body_sha256 == hashlib.sha256(raw).hexdigest() and command.body_hash == digest(body),
        '同一原请求编号的目标、路径或原文不同；原稿保留')
    return copy.deepcopy(command.data['result'])


def replay(db, p, attempt_id, raw, *, method, path):
    """Same original save only; None means no saved command, never write permission."""
    identity(attempt_id); body = parse_wire(raw); _iam(db, p)
    need(method == 'POST' and path == save_path(attempt_id), '人工稿保存路径或方法不符', 422)
    # This is the front door of a possible POST write. Hold exclusive locks
    # from the first probe; two replay-then-save callers must never upgrade
    # independent shared actor locks and deadlock each other.
    _nonce(db, p, body['request_id']); _nonce_owner(db, p, body['request_id'])
    _, commands, _ = _inventory(db, p, write=True, target_attempt=attempt_id)
    return _read_command(commands, attempt_id, body, raw, method, path)


def _view(row, history):
    return {'schema': VIEW, 'id': row.id, 'attempt_id': row.attempt_id, 'version': row.version,
        'status': row.status, **copy.deepcopy({key: row.data[key] for key in
            ('source_binding', 'baseline_value', 'value', 'note', 'diagnostics')}),
        'source_binding_hash': row.source_binding_hash, 'baseline_hash': row.baseline_hash,
        'history': copy.deepcopy(history), **FLAGS}


def get(db, p, attempt_id):
    identity(attempt_id); _iam(db, p)
    rows, _, histories = _inventory(db, p, target_attempt=attempt_id)
    matches = [row for row in rows.values() if row.attempt_id == attempt_id]
    need(len(matches) <= 1)
    return _view(matches[0], histories[matches[0].id]) if matches else None


def list_saved(db, p):
    """Complete owned inventory before callers acquire consumer-source locks."""
    _iam(db, p); rows, _, histories = _inventory(db, p)
    return [_view(row, histories[row.id]) for row in sorted(rows.values(), key=lambda row: row.id)]


def original_request(db, p, request_id, expected_sha256):
    identity(request_id); _iam(db, p)
    need(type(expected_sha256) is str and SHA.fullmatch(expected_sha256) is not None, '原请求SHA无法核对', 422)
    _nonce(db, p, request_id, shared=True); _nonce_owner(db, p, request_id)
    _, commands, _ = _inventory(db, p)
    found = [item for item in commands.values() if item.request_id == request_id]
    need(len(found) <= 1)
    if found: need(found[0].body_sha256 == expected_sha256, '原请求正文摘要不同；原稿保留')
    return {'schema': REQUEST, 'request_id': request_id, 'found': bool(found), 'body_sha256': expected_sha256,
        'result': copy.deepcopy(found[0].data['result']) if found else None}


def save(db, p, attempt_id, raw, *, method, path, source_binding, baseline_value, diagnostics):
    """One transaction; Root owns cold provenance and rolls back any exception."""
    identity(attempt_id); body = parse_wire(raw); _iam(db, p)
    need(method == 'POST' and path == save_path(attempt_id), '人工稿保存路径或方法不符', 422)
    _nonce(db, p, body['request_id']); _nonce_owner(db, p, body['request_id'])
    rows, commands, _ = _inventory(db, p, write=True, target_attempt=attempt_id)
    prior = _read_command(commands, attempt_id, body, raw, method, path)
    if prior is not None: return prior
    _iam(db, p, write=True)
    parent = db.scalar(select(Attempt).where(Attempt.id == attempt_id, Attempt.workspace_id == p.workspace_id,
        Attempt.actor_id == p.user_id).execution_options(populate_existing=True))
    need(parent is not None, '原方案不存在或不属于当前账号', 404)
    source_binding = _source(source_binding, attempt_id); _bytes(baseline_value, MAX_VALUE_BYTES)
    need(source_binding['producer_id'] == parent.producer_id and source_binding['model_id'] == parent.model_id
        and source_binding['revision'] == parent.revision and source_binding['reserved_call_id'] == parent.reserved_call_id)
    diag = _diagnostics(diagnostics, body['value'])
    matching = [row for row in rows.values() if row.attempt_id == attempt_id]; need(len(matching) <= 1)
    row = matching[0] if matching else None
    current_version = row.version if row is not None else 0
    if body['expected_version'] != current_version:
        raise HTTPException(409, {'code': 'business_flow_review_version_conflict',
            'not_applied': True, 'current_version': current_version})
    if row is None:
        need(len(rows) < MAX_DRAFTS)
        row = Draft(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id, attempt_id=attempt_id,
            request_id=body['request_id'], version=0, status='incomplete', created_at=m.now(),
            source_binding_hash=digest(source_binding), baseline_hash=digest(baseline_value), record_hash='',
            data={'schema': STATE, 'source_binding': source_binding, 'baseline_value': copy.deepcopy(baseline_value),
                'value': None, 'note': '', 'diagnostics': None, 'command_refs': []})
    else:
        need(_same(row.data['source_binding'], source_binding) and _same(row.data['baseline_value'], baseline_value),
            '原模型来源或只读基线不同；未覆盖已存人工劳动')
    version = row.version + 1; previous = row.record_hash if row.version else None
    command_id = m.uid(); sha = hashlib.sha256(raw).hexdigest()
    refs = row.data['command_refs'] + [{'id': command_id, 'request_id': body['request_id'],
        'version': version, 'body_sha256': sha}]
    data = {**row.data, 'value': copy.deepcopy(body['value']), 'note': body['note'],
        'diagnostics': diag, 'command_refs': refs}
    _bytes(data)
    snapshot = _snapshot(row, body['value'], body['note'], diag, refs, version)
    result = _result(row, body['request_id'], body['value'], body['note'], diag, version)
    command = Command(id=command_id, workspace_id=p.workspace_id, actor_id=p.user_id, draft_id=row.id,
        attempt_id=attempt_id, draft_version=version, request_id=body['request_id'], operation='save',
        method=method, path=path, exact_body=raw.decode('utf-8', errors='strict'), body_sha256=sha,
        body_hash=digest(body), created_at=m.now(), version=1,
        data={'schema': COMMAND, 'previous_hash': previous, 'source_binding_hash': row.source_binding_hash,
            'baseline_hash': row.baseline_hash, 'record_snapshot': snapshot, 'result': result})
    _bytes(command.data); command.command_hash = digest(_command_pin(command))
    row.version = version; row.data = data; row.record_hash = digest(snapshot)
    db.add(row); db.flush()  # The real FK parent must exist before its command.
    db.add(command); db.flush()
    audit(db, p, COMMAND_AUDIT, command.id, _command_audit(command))
    audit(db, p, STATE_AUDIT, row.id, _state_audit(row, command))
    db.flush(); _inventory(db, p, write=True, target_attempt=attempt_id)
    db.commit(); return copy.deepcopy(result)
