"""Independent exact-wire human revisions; history never renews source authority."""
import copy
import re
from datetime import timezone
from functools import wraps
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text

from . import models as m
from .contracts import digest
from .services import audit
from .report_object_revision_models_v166 import ReportObjectRevision as Revision
from .report_object_revision_models_v166 import ReportObjectRevisionCommand as Command

ACTION = 'report.object_revision_saved_v166'
VIEW_SCHEMA = 'report-object-revision-view-v166'
STATE_SCHEMA = 'report-object-revision-state-v166'
COMMAND_SCHEMA = 'report-object-revision-command-v166'
REQUEST_SCHEMA = 'report-object-revision-request-v166'
ERROR = '新结构稿、原请求或独立审计库存无法核对；原稿和原报告保留'
BASIS_KEYS = {'report_id', 'report_revision', 'model_result_hash', 'source_hash',
    'catalog_hash', 'context_hash', 'base_value_hash'}
SELECTED_BASIS_KEYS = BASIS_KEYS | {'source_kind', 'consumer_id', 'selection_id', 'selection_hash'}
_SHA = re.compile(r'^[a-f0-9]{64}$')
_NONCE = re.compile(r'^[A-Za-z0-9_-]{12,100}$')


def need(value):
    if not value:
        raise HTTPException(409, ERROR)


def same(left, right):
    return digest(left) == digest(right)


def _integrity(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
            need(False)
    return checked


def _identity(value):
    try:
        need(type(value) is str and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError):
        need(False)
    return value


def _utc(value):
    need(value is not None and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat()


def _basis(value, report_id):
    need(type(value) is dict and (set(value) == BASIS_KEYS or set(value) == SELECTED_BASIS_KEYS)
        and value['report_id'] == report_id
        and type(value['report_revision']) is int and value['report_revision'] >= 1)
    _identity(report_id)
    need(all(type(value[key]) is str and _SHA.fullmatch(value[key]) is not None
        for key in BASIS_KEYS - {'report_id', 'report_revision'}))
    if set(value) == SELECTED_BASIS_KEYS:
        need(type(value['source_kind']) is str and value['source_kind'] == 'saved-map-design-v178')
        _identity(value['consumer_id']); _identity(value['selection_id'])
        need(type(value['selection_hash']) is str and _SHA.fullmatch(value['selection_hash']) is not None)
    return copy.deepcopy(value)


def _value(value, *, confirmed=False):
    """Historical shape only; current source is checked separately before writes."""
    from .report_object_semantics_v166 import ManualValueV166, confirm_value
    from .typed_payload_codec_v131 import canonical_json
    try:
        canonical_json(value)
        result = ManualValueV166.model_validate(value).model_dump()
        need(same(result, value))
        if confirmed:
            need(same(confirm_value(result), value))
        return copy.deepcopy(result)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        need(False)


def _owner(db, p, report_id):
    _identity(report_id)
    # Historical human work needs current ownership, not a decode of possibly
    # damaged machine JSON from the original report.
    owner = db.scalar(select(m.SetupReportDraft.actor_id).where(
        m.SetupReportDraft.id == report_id, m.SetupReportDraft.workspace_id == p.workspace_id,
        m.SetupReportDraft.actor_id == p.user_id))
    if owner != p.user_id:
        raise HTTPException(404, '原报告不存在或不属于当前账号')
    return owner


def _lock(db, key, *, shared=False):
    number = int(digest(key)[:16], 16) - (1 << 63)
    function = 'pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
    db.execute(text('SELECT ' + function + '(:key)'), {'key': number})


def _nonce_lock(db, p, request_id, *, shared=False):
    need(type(request_id) is str and _NONCE.fullmatch(request_id) is not None)
    _lock(db, {'report_object_revision_nonce_v166': request_id,
        'workspace_id': p.workspace_id, 'actor_id': p.user_id}, shared=shared)


def _report_lock(db, p, report_id, *, shared=False):
    _lock(db, {'report_object_revision_report_v166': report_id,
        'workspace_id': p.workspace_id}, shared=shared)


def _actor_lock(db, p, *, shared=False):
    _lock(db, {'report_object_revision_inventory_v166': p.user_id,
        'workspace_id': p.workspace_id}, shared=shared)


def _snapshot(row):
    return {'id': row.id, 'workspace_id': row.workspace_id, 'report_id': row.report_id,
        'actor_id': row.actor_id, 'version': row.version, 'status': row.status,
        'basis_hash': row.basis_hash, 'data': copy.deepcopy(row.data)}


def _command_pin(row):
    return {**{key: getattr(row, key) for key in ('id', 'workspace_id', 'report_id',
        'actor_id', 'revision_id', 'record_version', 'request_id', 'operation',
        'method', 'path', 'wire_sha256', 'body_hash')},
        'saved_at': _utc(row.created_at), 'data': copy.deepcopy(row.data)}


def _result(row):
    snapshot = row.data['record_snapshot']
    return {'report_id': row.report_id, 'version': row.record_version,
        'operation': row.operation, 'status': snapshot['status'],
        'basis_hash': snapshot['basis_hash'], 'value_hash': digest(snapshot['data']['value'])}


def _audit_pin(row):
    return {'schema': 'report-object-revision-command-audit-v166',
        **{key: getattr(row, key) for key in ('id', 'report_id', 'actor_id', 'revision_id',
            'record_version', 'request_id', 'operation', 'method', 'path',
            'wire_sha256', 'body_hash', 'command_hash')},
        'record_hash': digest(row.data['record_snapshot'])}


def _checked_command(row, saved_audit):
    from .report_object_revision_api_v166 import command_path, parse_wire, wire_sha256
    try:
        need(row.operation in {'save', 'confirm'} and row.method == 'POST'
            and type(row.record_version) is int and row.record_version >= 1
            and row.path == command_path(row.report_id, row.operation)
            and type(row.data) is dict and set(row.data) == {
                'schema', 'wire', 'body', 'record_snapshot', 'result', 'previous_hash'}
            and row.data['schema'] == COMMAND_SCHEMA)
        raw = row.data['wire'].encode('utf-8', errors='strict')
        body = parse_wire(raw)
        need(body['request_id'] == row.request_id and wire_sha256(raw) == row.wire_sha256
            and digest(body) == row.body_hash and same(body, row.data['body'])
            and same(row.data['result'], _result(row))
            and row.command_hash == digest(_command_pin(row))
            and saved_audit.workspace_id == row.workspace_id
            and saved_audit.actor_id == row.actor_id and saved_audit.action == ACTION
            and saved_audit.object_id == row.id and same(saved_audit.data, _audit_pin(row)))
        return body
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        need(False)


@_integrity
def _inventory(db, p, report_id):
    """Both directions under one report read lock, independent of machine health."""
    _report_lock(db, p, report_id, shared=True)
    rows = list(db.scalars(select(Revision).where(Revision.workspace_id == p.workspace_id,
        Revision.report_id == report_id)))
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        or_(Command.report_id == report_id, Command.revision_id.in_([row.id for row in rows])))))
    audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION, or_(m.Audit.data['report_id'].as_string() == report_id,
            m.Audit.data['revision_id'].as_string().in_([row.id for row in rows]),
            m.Audit.object_id.in_([row.id for row in commands])))))
    need(len(rows) <= 1 and len(commands) == len(audits)
        and len({row.id for row in commands}) == len(commands)
        and {row.id for row in commands} == {row.object_id for row in audits})
    if not rows:
        need(not commands and not audits)
        return None, []
    row = rows[0]
    need(row.actor_id == p.user_id and row.report_id == report_id
        and type(row.version) is int and row.version >= 1)
    commands.sort(key=lambda item: item.record_version)
    need([item.record_version for item in commands] == list(range(1, row.version + 1)))
    audit_by_id = {item.object_id: item for item in audits}
    previous_hash, first_basis = None, None
    last = None
    for command in commands:
        need(command.actor_id == p.user_id and command.report_id == report_id
            and command.revision_id == row.id and command.data['previous_hash'] == previous_hash)
        body = _checked_command(command, audit_by_id[command.id])
        snapshot = command.data['record_snapshot']
        need(type(snapshot) is dict and set(snapshot) == {'id', 'workspace_id', 'report_id',
            'actor_id', 'version', 'status', 'basis_hash', 'data'})
        state = snapshot['data']
        need(type(state) is dict and set(state) == {'schema', 'basis', 'value'}
            and state['schema'] == STATE_SCHEMA)
        basis = _basis(state['basis'], report_id)
        checked = _value(body['value'], confirmed=command.operation == 'confirm')
        need(body['expected_version'] == command.record_version - 1
            and body['basis_hash'] == digest(basis)
            and checked['result']['source_hash'] == basis['source_hash']
            and checked['result']['catalog_hash'] == basis['catalog_hash']
            and (first_basis is None or same(first_basis, basis)))
        expected = {'id': row.id, 'workspace_id': p.workspace_id, 'report_id': report_id,
            'actor_id': p.user_id, 'version': command.record_version,
            'status': 'confirmed' if command.operation == 'confirm' else 'draft',
            'basis_hash': digest(basis), 'data': {'schema': STATE_SCHEMA, 'basis': basis, 'value': checked}}
        need(same(snapshot, expected))
        first_basis = basis
        previous_hash, last = digest(snapshot), snapshot
    need(last is not None and same(_snapshot(row), last) and row.record_hash == digest(last))
    return row, commands


@_integrity
def _lookup(db, p, request_id):
    _nonce_lock(db, p, request_id, shared=True)
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.actor_id == p.user_id, Command.request_id == request_id).limit(2)))
    audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION, m.Audit.data['request_id'].as_string() == request_id,
        or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id)).limit(2)))
    need(len(commands) == len(audits) <= 1)
    if not commands:
        return None
    need(audits[0].object_id == commands[0].id)
    _checked_command(commands[0], audits[0])
    return commands[0]


@_integrity
def _actor_inventory(db, p, target_report_id, *, shared):
    """An absent nonce cannot conceal a retained row in another own report.

    Actor locking precedes discovery; all report locks are acquired in sorted
    order before any inventory is read. No target-first lock or S-to-X upgrade
    is used. Known saved replays do not enter this discovery path.
    """
    _actor_lock(db, p, shared=shared)
    rows = list(db.scalars(select(Revision).where(Revision.workspace_id == p.workspace_id,
        Revision.actor_id == p.user_id)))
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.actor_id == p.user_id)))
    audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION,
        or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
    reports = {target_report_id}
    for row in rows:
        reports.update((row.report_id, row.data['basis']['report_id']))
    for command in commands:
        reports.update((command.report_id, command.data['record_snapshot']['report_id'],
            command.data['record_snapshot']['data']['basis']['report_id']))
    reports.update(item.data['report_id'] for item in audits)
    for report_id in reports:
        _identity(report_id)
    ordered = sorted(reports)
    for report_id in ordered:
        _report_lock(db, p, report_id, shared=shared)
    checked = {}
    for report_id in ordered:
        checked[report_id] = _inventory(db, p, report_id)
    return checked[target_report_id]


def _envelope(request_id, fingerprint, command=None):
    return {'schema': REQUEST_SCHEMA, 'request_id': request_id, 'wire_sha256': fingerprint,
        'outcome': 'unknown' if command is None else 'saved',
        'result': None if command is None else copy.deepcopy(command.data['result'])}


def request_result(db, p, report_id, request_id, expected_sha256):
    _owner(db, p, report_id)
    need(type(expected_sha256) is str and _SHA.fullmatch(expected_sha256) is not None)
    command = _lookup(db, p, request_id)
    if command is not None:
        need(command.report_id == report_id and command.wire_sha256 == expected_sha256)
        _inventory(db, p, report_id)
    else:
        _actor_inventory(db, p, report_id, shared=True)
    return _envelope(request_id, expected_sha256, command)


def reject_legacy_adoption(db, p, report_id):
    """New attempts cannot silently adopt an older confirmed report family.

    Call before acquiring the original report's exclusive SQL row lock. The
    report advisory read lock remains held through the caller transaction.
    Historical successful legacy receipts/replays do not call this gate.
    """
    _owner(db, p, report_id)
    _, commands = _inventory(db, p, report_id)
    if any(command.operation == 'confirm' for command in commands):
        raise HTTPException(409,
            '此报告已确认独立新结构稿，新版采用尚未接入，原稿与配置保留，请勿用旧采用覆盖')
    return None


def _source(db, p, report_id, *, lock=False):
    from .report_object_revision_source_v166 import load_source
    source = load_source(db, p, report_id, lock=lock)
    need(type(source) is dict and set(source) == {
        'basis', 'basis_hash', 'value', 'catalog', 'allowed_span_refs', 'context'})
    basis = _basis(source['basis'], report_id)
    need(type(source['basis_hash']) is str and source['basis_hash'] == digest(basis))
    return source


def _current_role(db, p):
    if p.role not in {'admin', 'operator'}:
        return False
    return db.scalar(select(m.User.id).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.User.id == p.user_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
        m.Membership.role == p.role, m.Membership.role.in_(['admin', 'operator']))) is not None


def detail(db, p, report_id):
    _owner(db, p, report_id)
    row, commands = _actor_inventory(db, p, report_id, shared=True)
    source, issue = None, None
    try:
        source = _source(db, p, report_id)
        if row is not None and not same(row.data['basis'], source['basis']):
            raise HTTPException(409, '原报告、原人工稿或来源已有变化；新结构稿仍保留，不能静默换基')
    except HTTPException as error:
        if row is None or error.status_code not in {403, 404, 409, 422}:
            raise
        source = None
        issue = '当前报告来源无法用于新的结构确认；已保存人工稿和原请求保留，请核对原版本'
    can_write = source is not None and _current_role(db, p)
    if source is not None and not can_write:
        issue = '当前账号只可读取已保存内容，不能保存或确认新结构稿'
    basis = copy.deepcopy(row.data['basis'] if row is not None else source['basis'])
    value = copy.deepcopy(row.data['value'] if row is not None else source['value'])
    history = [{'version': item.record_version, 'operation': item.operation,
        'value': copy.deepcopy(item.data['record_snapshot']['data']['value']),
        'saved_at': _utc(item.created_at)} for item in commands]
    return {'schema': VIEW_SCHEMA, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'report_id': report_id, 'version': row.version if row is not None else 0,
        'status': row.status if row is not None else 'empty', 'basis': basis,
        'basis_hash': digest(basis), 'value': value, 'draft_history': history,
        'can_save': can_write, 'can_confirm': can_write,
        'source_available': source is not None, 'source_issue': issue}


def mutate(db, p, report_id, operation, body, raw, method, path):
    from .report_object_revision_api_v166 import command_path, parse_wire, wire_sha256
    from .report_object_semantics_v166 import validate_value, confirm_value
    from .relations import lock_current_actor
    _owner(db, p, report_id)
    need(operation in {'save', 'confirm'} and method == 'POST'
        and path == command_path(report_id, operation) and same(parse_wire(raw), body))
    fingerprint = wire_sha256(raw)
    _nonce_lock(db, p, body['request_id'])
    original = _lookup(db, p, body['request_id'])
    if original is not None:
        need(original.report_id == report_id and original.operation == operation
            and original.method == method and original.path == path
            and original.wire_sha256 == fingerprint and original.data['wire'] == raw.decode('utf-8'))
        _inventory(db, p, report_id)
        return _envelope(body['request_id'], fingerprint, original)
    row, _ = _actor_inventory(db, p, report_id, shared=False)
    if body['expected_version'] != (row.version if row is not None else 0):
        raise HTTPException(409, '新结构稿已有保存版本；本机输入保留，请读回后明确比较')
    lock_current_actor(db, p)
    source = _source(db, p, report_id, lock=True)
    need(body['basis_hash'] == source['basis_hash']
        and (row is None or same(row.data['basis'], source['basis'])))
    try:
        checked = validate_value(body['value'], source['catalog'], source['allowed_span_refs'], source['context'])
        need(same(checked, body['value']))
        if operation == 'confirm':
            need(same(confirm_value(checked), checked))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, '字段、原文片段、身份说明或逐项确认无法核对；原稿保留') from None
    basis = copy.deepcopy(source['basis'])
    saved_at = db.scalar(select(func.clock_timestamp()))
    previous_hash = row.record_hash if row is not None else None
    status = 'confirmed' if operation == 'confirm' else 'draft'
    state = {'schema': STATE_SCHEMA, 'basis': basis, 'value': copy.deepcopy(checked)}
    if row is None:
        row = Revision(workspace_id=p.workspace_id, report_id=report_id, actor_id=p.user_id,
            status=status, basis_hash=source['basis_hash'], version=1, record_hash='', data=state)
        db.add(row)
    else:
        row.version += 1
        row.status = status
        row.data = state
    db.flush()
    snapshot = _snapshot(row)
    row.record_hash = digest(snapshot)
    command = Command(workspace_id=p.workspace_id, report_id=report_id, actor_id=p.user_id,
        revision_id=row.id, record_version=row.version, request_id=body['request_id'],
        operation=operation, method=method, path=path, wire_sha256=fingerprint,
        body_hash=digest(body), created_at=saved_at, command_hash='', data={})
    db.add(command)
    db.flush()
    command.data = {'schema': COMMAND_SCHEMA, 'wire': raw.decode('utf-8'),
        'body': copy.deepcopy(body), 'record_snapshot': snapshot, 'previous_hash': previous_hash}
    command.data['result'] = _result(command)
    command.command_hash = digest(_command_pin(command))
    audit(db, p, ACTION, command.id, _audit_pin(command))
    db.flush()
    _inventory(db, p, report_id)
    return _envelope(body['request_id'], fingerprint, command)
