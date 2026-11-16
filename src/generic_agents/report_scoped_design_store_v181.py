"""Durable private scope plans; never source, spending or execution authority.

History checks the stored four-key source and every original Command/Audit, not
current source availability. Fresh create/save/confirm separately call Root's
real runtime source loader. Drafts allow unassigned claims; confirmation does
not invent ownership and uses the actual complete planner180 gate.
"""
import copy
import hashlib
import importlib
import json
import re
from datetime import timezone
from functools import wraps
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select, text

from . import models as m
from . import report_indexed_inputs_v178 as codec
from . import report_scoped_design_plan_v180 as planner
from .contracts import digest
from .relations import lock_current_actor
from .report_full_reading_semantics import _safe
from .report_saved_map_design_preparation_v176 import _source as checked_source_context
from .report_scoped_design_models_v181 import ReportScopedDesignPlanV181 as Plan
from .report_scoped_design_models_v181 import ReportScopedDesignPlanCommandV181 as Command
from .security import require
from .services import audit
from .typed_payload_codec_v131 import canonical_json

PREFIX = '/api/setup/report-scoped-designs/v181'
VIEW = 'report-scoped-design-plan-view-v181'
LIST = 'report-scoped-design-plan-list-v181'
INPUTS = 'report-scoped-design-inputs-v181'
RESULT = 'report-scoped-design-plan-result-v181'
REQUEST = 'report-scoped-design-plan-request-v181'
STATE = 'report-scoped-design-plan-state-v181'
COMMAND = 'report-scoped-design-plan-command-v181'
COMMAND_AUDIT = 'report.scoped_design_plan_command_v181'
STATE_AUDIT = 'report.scoped_design_plan_state_v181'
MAX_WIRE_BYTES = 1024 * 1024
MAX_PLANS = 200
MAX_HISTORY = 1000
MAX_RECORD_BYTES = 20 * 1024 * 1024
ERROR = '业务范围计划、原请求或独立审计库存无法核对；原来源和人稿保留'
SHA = re.compile(r'^[a-f0-9]{64}$')


class InventoryError(HTTPException):
    def __init__(self, detail=ERROR): super().__init__(409, detail)


def need(value, detail=ERROR, code=409):
    if not value: raise HTTPException(code, detail)


def identity(value):
    if type(value) is not str or str(UUID(value)) != value: raise ValueError(ERROR)
    return value


def same(left, right): return canonical_json(left) == canonical_json(right)


def _safe_bytes(value):
    _safe(value)
    return canonical_json(value).encode('utf-8', errors='strict')


def _utc(value):
    need(value is not None and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat()


def command_path(plan_id, operation):
    if operation == 'create':
        need(plan_id is None)
        return PREFIX + '/plans'
    need(operation in {'save', 'confirm'})
    return PREFIX + '/plans/' + identity(plan_id) + '/' + operation


def parse_wire(raw, operation):
    """Exact closed body; do not fill defaults, normalize UUIDs or re-sign JSON."""
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_WIRE_BYTES:
            raise ValueError(ERROR)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result: raise ValueError(ERROR)
                result[key] = value
            return result
        def constant(_): raise ValueError(ERROR)
        body = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs,
            parse_constant=constant)
        _safe_bytes(body)
        fields = {'request_id', 'revision', 'source_snapshot_hash'}
        if operation == 'create': fields |= {'producer_id', 'ledger'}
        elif operation == 'save': fields |= {'expected_version', 'ledger'}
        elif operation == 'confirm': fields |= {'expected_version', 'confirm'}
        else: raise ValueError(ERROR)
        if type(body) is not dict or set(body) != fields: raise ValueError(ERROR)
        identity(body['request_id'])
        if operation == 'create': identity(body['producer_id'])
        if type(body['revision']) is not int or body['revision'] < 1: raise ValueError(ERROR)
        if type(body['source_snapshot_hash']) is not str or not SHA.fullmatch(body['source_snapshot_hash']):
            raise ValueError(ERROR)
        if operation != 'create' and (type(body['expected_version']) is not int or body['expected_version'] < 1):
            raise ValueError(ERROR)
        if operation == 'confirm':
            if body['confirm'] is not True: raise ValueError(ERROR)
        elif type(body['ledger']) is not dict or 'confirmation' in body['ledger']:
            raise ValueError(ERROR)
        return body
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise ValueError('范围计划请求字段、严格版本或原始JSON无法核对') from None


def _inputs(source_context):
    try:
        snapshot, claims, _ = checked_source_context(source_context)
        indexed = codec.build_indexed_inputs(claims, snapshot['snapshot_hash'])
        return snapshot, claims, indexed
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '完整原来源或原编号声明无法核对；未换基、保存或调用模型') from None


def _draft_boundary(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try: return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
            raise HTTPException(422, '草稿范围、位置、共享或背景引用不符合严格合同；原稿保留') from None
    return checked


@_draft_boundary
def validate_draft(ledger, source_context):
    """Validate partial ownership without fabricating a confirmation or gaps.

    Empty scopes and unassigned positions are valid human work. Only explicitly
    declared background positions may be used as scope context. No unassigned
    claim is silently classified as background and no original refs are added.
    """
    snapshot, _, indexed = _inputs(source_context)
    need(type(ledger) is dict and set(ledger) == planner._BASE_KEYS)
    _safe_bytes(ledger)
    count = indexed['input_count']
    need(ledger['schema'] == planner.LEDGER
        and ledger['source_snapshot_hash'] == snapshot['snapshot_hash']
        and ledger['indexed_inputs_hash'] == indexed['inputs_hash']
        and ledger['input_claims_hash'] == indexed['input_claims_hash']
        and type(ledger['input_count']) is int and ledger['input_count'] == count)
    scopes, shared, background = ledger['scopes'], ledger['shared'], ledger['background']
    need(type(scopes) is list and len(scopes) <= planner.MAX_SCOPES
        and type(shared) is list and len(shared) <= count
        and type(background) is list and len(background) <= count)
    names, by_id, owned = set(), {}, set()
    for scope in scopes:
        need(type(scope) is dict and set(scope) == {'scope_id', 'name', 'claim_positions', 'context_positions'})
        scope_id = planner._id(scope['scope_id']); name = scope['name']
        need(scope_id not in by_id and type(name) is str and 1 <= len(name) <= 200
            and bool(name.strip()) and name not in names)
        by_id[scope_id] = scope; names.add(name)
        planner._positions(scope['claim_positions'], count, 'scope_claim_positions')
        planner._positions(scope['context_positions'], count, 'scope_context_positions')
        for position in scope['claim_positions']:
            need(position not in owned, '同一原声明不能重复归属；原草稿保留')
            owned.add(position)
    previous = -1
    for item in shared:
        need(type(item) is dict and set(item) == {'input_position', 'scope_ids', 'reason'})
        position, recipients = item['input_position'], item['scope_ids']
        need(type(position) is int and previous < position < count and position >= 0
            and position not in owned and type(recipients) is list and 2 <= len(recipients) <= len(scopes)
            and all(type(scope_id) is str and scope_id in by_id for scope_id in recipients)
            and len(recipients) == len(set(recipients)))
        planner._reason(item['reason']); previous = position; owned.add(position)
    previous, background_positions = -1, set()
    for item in background:
        need(type(item) is dict and set(item) == {'input_position', 'reason'})
        position = item['input_position']
        need(type(position) is int and previous < position < count and position >= 0 and position not in owned)
        planner._reason(item['reason']); previous = position; owned.add(position); background_positions.add(position)
    need(all(position in background_positions for scope in scopes for position in scope['context_positions']),
        '范围背景引用须指向已明确说明的背景项；未自动归类原声明')
    return copy.deepcopy(ledger)


def _confirmed_plan(source_context, ledger, *, actor_id, request_id, saved_at):
    snapshot, claims, indexed = _inputs(source_context)
    confirmation = {'schema': planner.CONFIRMATION, 'status': 'confirmed',
        'request_id': identity(request_id), 'actor_id': identity(actor_id),
        'confirmed_at': saved_at, 'ledger_hash': codec.digest(ledger)}
    full = {**copy.deepcopy(ledger), 'confirmation': confirmation}
    return planner.build_plan(indexed, snapshot['snapshot_hash'], full, input_claims=claims)


def _lock(db, value, *, shared):
    number = int(digest(value)[:16], 16) - (1 << 63)
    name = 'pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
    db.execute(text('SELECT ' + name + '(:key)'), {'key': number})


def _nonce(db, p, request_id, *, shared):
    _lock(db, {'scoped_design_nonce_v181': identity(request_id),
        'workspace_id': p.workspace_id, 'actor_id': p.user_id}, shared=shared)


def _iam(db, p, *, write=False):
    need(p.role in {'admin', 'operator', 'viewer'}, '当前账号没有当前工作区读取权限', 403)
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
    need(db.execute(query).first() is not None, '当前账号、工作区角色或会话已变化，原稿保留', 403)


def _owner(db, p, producer_id):
    producer_id = identity(producer_id)
    actor = db.scalar(select(m.SetupReportDraft.actor_id).where(m.SetupReportDraft.id == producer_id,
        m.SetupReportDraft.workspace_id == p.workspace_id, m.SetupReportDraft.actor_id == p.user_id))
    need(actor == p.user_id, '原报告不存在或不属于当前账号', 404)


def _confirmation(plan):
    return copy.deepcopy(plan['scope_ledger']['confirmation']) if plan is not None else None


def _snapshot(row, *, version=None, status=None, ledger=None, plan=None, use_current=True):
    if use_current:
        ledger, plan = row.data['ledger'], row.data['plan']
    return {'schema': STATE, 'id': row.id, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
        'producer_id': row.producer_id, 'request_id': row.request_id, 'revision': row.revision,
        'source_snapshot_hash': row.source_snapshot_hash,
        'source_context_hash': digest(row.data['source_context']),
        'version': row.version if version is None else version,
        'status': row.status if status is None else status, 'ledger_hash': digest(ledger),
        'confirmation_hash': digest(_confirmation(plan)) if plan is not None else None,
        'plan_hash': plan['plan_hash'] if plan is not None else None}


def _result(row, operation, *, version=None, status=None, plan_hash=None):
    return {'schema': RESULT, 'id': row.id, 'version': row.version if version is None else version,
        'operation': operation, 'status': row.status if status is None else status,
        'plan_hash': plan_hash}


def _command_pin(command):
    return {**{key: copy.deepcopy(getattr(command, key)) for key in ('id', 'workspace_id', 'actor_id',
        'producer_id', 'plan_id', 'plan_version', 'request_id', 'operation', 'method', 'path',
        'exact_body', 'wire_sha256', 'body_hash', 'data')}, 'saved_at': _utc(command.created_at)}


def _command_audit(command):
    return {'schema': COMMAND, 'plan_id': command.plan_id, 'producer_id': command.producer_id,
        'actor_id': command.actor_id, 'request_id': command.request_id,
        'command_hash': command.command_hash, 'command_pin': _command_pin(command)}


def _state_audit(row, command, state_hash):
    return {'schema': STATE, 'plan_id': row.id, 'producer_id': row.producer_id,
        'actor_id': row.actor_id, 'version': command.plan_version, 'command_id': command.id,
        'previous_hash': command.data['previous_hash'], 'state_hash': state_hash}


def _checked_history(row, commands, events, p):
    need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id
        and type(row.version) is int and 1 <= row.version <= MAX_HISTORY
        and type(row.revision) is int and row.revision >= 1 and row.status in {'draft', 'confirmed'}
        and type(row.data) is dict and set(row.data) == {'source_context', 'ledger', 'plan'})
    for value in (row.id, row.workspace_id, row.actor_id, row.producer_id, row.request_id): identity(value)
    snapshot, _, _ = _inputs(row.data['source_context'])
    need(snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id
        and snapshot['producer']['id'] == row.producer_id and row.source_snapshot_hash == snapshot['snapshot_hash'])
    need(len(commands) == row.version and len(events) == row.version * 2)
    ordered = sorted(commands, key=lambda command: command.plan_version)
    need([item.plan_version for item in ordered] == list(range(1, row.version + 1)))
    previous, previous_ledger, confirmed, checked_events, history = None, None, False, set(), []
    for version, command in enumerate(ordered, 1):
        need(not confirmed and command.workspace_id == p.workspace_id and command.actor_id == p.user_id
            and command.plan_id == row.id and command.producer_id == row.producer_id
            and type(command.plan_version) is int and command.plan_version == version
            and command.operation in {'create', 'save', 'confirm'} and command.method == 'POST'
            and type(command.exact_body) is str and type(command.data) is dict
            and set(command.data) == {'schema', 'body', 'ledger', 'confirmation', 'plan_hash',
                'previous_hash', 'record_snapshot', 'result'} and command.data['schema'] == COMMAND)
        identity(command.id); identity(command.request_id)
        raw = command.exact_body.encode('utf-8', errors='strict'); body = parse_wire(raw, command.operation)
        need(command.path == command_path(None if command.operation == 'create' else row.id, command.operation)
            and command.wire_sha256 == hashlib.sha256(raw).hexdigest()
            and command.body_hash == digest(body) and same(command.data['body'], body)
            and body['request_id'] == command.request_id and body['revision'] == row.revision
            and body['source_snapshot_hash'] == row.source_snapshot_hash
            and command.data['previous_hash'] == previous and command.command_hash == digest(_command_pin(command)))
        need((version == 1 and command.operation == 'create' and command.request_id == row.request_id
                and body['producer_id'] == row.producer_id)
            or (version > 1 and command.operation in {'save', 'confirm'} and body['expected_version'] == version - 1))
        ledger = validate_draft(command.data['ledger'], row.data['source_context'])
        if command.operation == 'confirm':
            need(same(ledger, previous_ledger))
            plan = _confirmed_plan(row.data['source_context'], ledger, actor_id=p.user_id,
                request_id=command.request_id, saved_at=_utc(command.created_at))
            confirmed = True
        else:
            need(same(ledger, body['ledger'])); plan = None
        need(same(command.data['confirmation'], _confirmation(plan))
            and command.data['plan_hash'] == (plan['plan_hash'] if plan is not None else None))
        state = _snapshot(row, version=version, status='confirmed' if confirmed else 'draft',
            ledger=ledger, plan=plan, use_current=False)
        need(same(command.data['record_snapshot'], state)
            and same(command.data['result'], _result(row, command.operation, version=version,
                status=state['status'], plan_hash=state['plan_hash'])))
        state_hash = digest(state)
        command_events = [event for event in events if event.action == COMMAND_AUDIT and event.object_id == command.id]
        state_events = [event for event in events if event.action == STATE_AUDIT
            and event.object_id == row.id and event.data.get('version') == version]
        need(len(command_events) == len(state_events) == 1)
        for event in command_events + state_events:
            need(event.workspace_id == p.workspace_id and event.actor_id == p.user_id)
            checked_events.add(event.id)
        need(same(command_events[0].data, _command_audit(command))
            and same(state_events[0].data, _state_audit(row, command, state_hash)))
        history.append({'version': version, 'operation': command.operation, 'request_id': command.request_id,
            'ledger': ledger, 'confirmation': _confirmation(plan), 'plan_hash': state['plan_hash'],
            'saved_at': _utc(command.created_at)})
        previous, previous_ledger = state_hash, ledger
    need(checked_events == {event.id for event in events}
        and row.status == ('confirmed' if confirmed else 'draft') and row.record_hash == previous
        and same(row.data['ledger'], previous_ledger)
        and same(row.data['plan'], plan) and row.record_hash == digest(_snapshot(row)))
    need(len(_safe_bytes(row.data)) <= MAX_RECORD_BYTES)
    return history


def _inventory(db, p, *, lock=False, target_producer=None, target_plan=None):
    """Nonce callers precede actor -> sorted producer -> sorted plan locks.

    Every writer uses the actor Exclusive lock, so three-sided discovery cannot
    straddle another same-owner commit. Readers use only Shared advisory locks,
    never row locks, and never upgrade their lock mode within this function.
    """
    need(type(lock) is bool)
    try:
        _lock(db, {'scoped_design_actor_v181': p.user_id, 'workspace_id': p.workspace_id}, shared=not lock)
        rows = list(db.scalars(select(Plan).where(Plan.workspace_id == p.workspace_id,
            Plan.actor_id == p.user_id).execution_options(populate_existing=True)))
        commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
            Command.actor_id == p.user_id).execution_options(populate_existing=True)))
        events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action.in_([COMMAND_AUDIT, STATE_AUDIT]),
            or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
        need(len(rows) <= MAX_PLANS and len(commands) <= MAX_PLANS * MAX_HISTORY
            and len(events) <= MAX_PLANS * MAX_HISTORY * 2)
        producers = {identity(row.producer_id) for row in rows} | {identity(command.producer_id) for command in commands}
        ids = {identity(row.id) for row in rows} | {identity(command.plan_id) for command in commands}
        for event in events:
            need(type(event.data) is dict)
            producers.add(identity(event.data['producer_id'])); ids.add(identity(event.data['plan_id']))
        if target_producer is not None: producers.add(identity(target_producer))
        if target_plan is not None: ids.add(identity(target_plan))
        for value in sorted(producers):
            _lock(db, {'scoped_design_report_v181': value, 'workspace_id': p.workspace_id}, shared=not lock)
        for value in sorted(ids):
            _lock(db, {'scoped_design_plan_v181': value, 'workspace_id': p.workspace_id}, shared=not lock)
        by_id = {row.id: row for row in rows}; by_command = {command.id: command for command in commands}
        need(len(by_id) == len(rows) and len(by_command) == len(commands)
            and len({command.request_id for command in commands}) == len(commands))
        # The target may be genuinely absent. Any stored command/audit pointing
        # to a missing row is independent residual evidence, never unknown.
        need(all(command.plan_id in by_id for command in commands)
            and all(event.data['plan_id'] in by_id for event in events))
        histories = {}
        for row in rows:
            histories[row.id] = _checked_history(row,
                [command for command in commands if command.plan_id == row.id],
                [event for event in events if event.data['plan_id'] == row.id], p)
        return by_id, by_command, histories
    except InventoryError: raise
    except HTTPException as error:
        raise InventoryError() from error
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise InventoryError() from None


def _owned(rows, p, plan_id):
    row = rows.get(identity(plan_id))
    need(row is not None and row.workspace_id == p.workspace_id and row.actor_id == p.user_id,
        '范围计划不存在或不属于当前账号', 404)
    return row


def checked_plan(db, p, plan_id, *, lock=False, confirmed=False):
    """Return actual storage-checked ORM row, NOT current source or call rights."""
    need(type(lock) is bool and type(confirmed) is bool)
    _iam(db, p)
    rows, _, _ = _inventory(db, p, lock=lock, target_plan=plan_id)
    row = _owned(rows, p, plan_id)
    if confirmed: need(row.status == 'confirmed' and row.data['plan'] is not None,
        '请先保存并明确确认完整业务范围计划；没有建立生成调用')
    return row


def _view(row, history, p):
    _, _, indexed = _inputs(row.data['source_context'])
    plan = row.data['plan']
    return {'schema': VIEW, 'id': row.id, 'version': row.version, 'status': row.status,
        'workspace_id': row.workspace_id, 'actor_id': row.actor_id, 'producer_id': row.producer_id,
        'revision': row.revision, 'source_snapshot_hash': row.source_snapshot_hash,
        'source_summary': {'input_count': indexed['input_count'],
            'input_claims_hash': indexed['input_claims_hash'], 'indexed_inputs_hash': indexed['inputs_hash']},
        'ledger': copy.deepcopy(row.data['ledger']), 'confirmation': _confirmation(plan),
        'plan': copy.deepcopy(plan), 'plan_hash': plan['plan_hash'] if plan is not None else None,
        'history': copy.deepcopy(history), 'can_save': row.status == 'draft' and p.role in {'admin', 'operator'},
        'can_confirm': row.status == 'draft' and p.role in {'admin', 'operator'},
        'requires_current_source_check': True, 'can_apply': False, 'can_publish': False}


def list_plans(db, p, producer_id):
    _iam(db, p); _owner(db, p, producer_id)
    rows, _, histories = _inventory(db, p, target_producer=producer_id)
    return {'schema': LIST, 'producer_id': producer_id,
        'items': [_view(row, histories[row.id], p) for row in sorted(rows.values(), key=lambda row: row.id)
            if row.producer_id == producer_id]}


def proposal(db, p, producer_id, revision):
    """Cold inferred draft suggestion, with no storage or provider action."""
    _iam(db, p); _owner(db, p, producer_id)
    need(type(revision) is int and revision >= 1, '请核对当前配置版本', 422)
    source = runtime().load_source(db, p, producer_id, revision, lock=False)
    snapshot, claims, indexed = _inputs(source)
    need(snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id
        and snapshot['producer']['id'] == producer_id)
    from .report_scoped_design_proposal_v181 import build_proposal
    try:
        suggested = build_proposal(indexed, snapshot['snapshot_hash'], input_claims=claims,
            catalog=source['catalog'])
        validate_draft(suggested['ledger'], source)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '完整来源的业务范围建议无法核对，未保存、确认或调用模型') from None
    return {'schema': 'report-scoped-design-proposal-view-v181', 'producer_id': producer_id,
        'revision': revision, 'source_snapshot_hash': snapshot['snapshot_hash'],
        'source_summary': {'input_count': indexed['input_count'],
            'input_claims_hash': indexed['input_claims_hash'], 'indexed_inputs_hash': indexed['inputs_hash']},
        'proposal': copy.deepcopy(suggested), 'requires_current_source_check': True,
        'can_apply': False, 'can_publish': False}


def detail(db, p, plan_id):
    _iam(db, p)
    rows, _, histories = _inventory(db, p, target_plan=plan_id)
    row = _owned(rows, p, plan_id)
    return _view(row, histories[row.id], p)


MAX_INPUT_QUERY_CHARS = 200


def _matching_input_positions(snapshot, count, query):
    needle = query.casefold()
    positions = []
    for position in range(count):
        claim = snapshot['input_items'][snapshot['input_order'][position]]
        if type(claim['statement']) is not str:
            raise ValueError('invalid_saved_statement')
        if needle in claim['statement'].casefold():
            positions.append(position)
    return positions


def plan_inputs(db, p, plan_id, *, offset=0, limit=50, query=None):
    """Page saved original claims/text, never current source or write authority.

    Positions belong to the saved input_order, including duplicate claims. A
    resolved span proves only that this saved text/reference can be read; it
    does not establish that the model statement is supported or true.
    """
    need(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 100,
        '请使用非负整数起始位置和1至100条的整数分页数量', 422)
    if query is not None:
        need(type(query) is str and len(query) <= MAX_INPUT_QUERY_CHARS,
            '搜索词须为200字符以内的普通文本，不接受布尔或数值', 422)
        try:
            _safe_bytes(query)
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            raise HTTPException(422, '搜索词须为安全Unicode文本，不能含空字符') from None
    _iam(db, p)
    rows, _, _ = _inventory(db, p, target_plan=plan_id)
    row = _owned(rows, p, plan_id)
    source = row.data['source_context']
    snapshot, _, indexed = _inputs(source)
    from .report_evidence_plan import resolve_evidence
    try:
        count = indexed['input_count']
        positions = range(count) if query is None else _matching_input_positions(snapshot, count, query)
        matching_count = len(positions); end = min(offset + limit, matching_count); items = []
        for position in positions[offset:end]:
            input_id = snapshot['input_order'][position]
            claim = copy.deepcopy(snapshot['input_items'][input_id])
            resolved = resolve_evidence(source['catalog'], claim['span_refs'], source['allowed_span_refs'])
            items.append({'global_position': position, 'input_id': input_id, 'claim': claim,
                'spans': [{'span_ref': item['span_ref'], 'text': item['text']} for item in resolved],
                'anchor_status': 'resolved_saved_text' if resolved else 'unanchored'})
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise InventoryError('保存的声明位置或原文引用无法核对；未猜配、裁剪或换用当前来源') from None
    value = {'schema': INPUTS, 'plan_id': row.id, 'source_snapshot_hash': snapshot['snapshot_hash'],
        'indexed_inputs_hash': indexed['inputs_hash'], 'input_claims_hash': indexed['input_claims_hash'],
        'input_count': count, 'offset': offset, 'limit': limit,
        'next_offset': end if end < matching_count else None, 'items': items,
        'historical_source': True, 'requires_current_source_check': True,
        'can_apply': False, 'can_publish': False}
    if query is not None:
        value.update(query=query, matching_input_count=matching_count)
    return value


def request_result(db, p, request_id, expected_sha256):
    _iam(db, p); identity(request_id)
    need(type(expected_sha256) is str and SHA.fullmatch(expected_sha256), '原请求SHA无法核对', 422)
    _nonce(db, p, request_id, shared=True)
    _, commands, _ = _inventory(db, p)
    matches = [command for command in commands.values() if command.request_id == request_id]
    need(len(matches) <= 1)
    command = matches[0] if matches else None
    if command is not None: need(command.wire_sha256 == expected_sha256, '原请求正文摘要不同，原记录保留')
    return {'schema': REQUEST, 'request_id': request_id, 'wire_sha256': expected_sha256,
        'outcome': 'saved' if command is not None else 'unknown',
        'result': copy.deepcopy(command.data['result']) if command is not None else None}


def runtime():
    try:
        module = importlib.import_module('.report_scoped_design_runtime_v181', __package__)
    except ModuleNotFoundError as error:
        if error.name != __package__ + '.report_scoped_design_runtime_v181': raise
        raise HTTPException(503, '当前真实范围来源接线尚未启用，未保存或调用模型') from None
    need(callable(getattr(module, 'load_source', None)), '当前真实范围来源接线尚未启用，未保存或调用模型', 503)
    return module


def mutate(db, p, plan_id, operation, raw, *, actual_method, actual_path):
    body = parse_wire(raw, operation)
    need(actual_method == 'POST' and actual_path == command_path(plan_id, operation),
        '范围计划操作路径与原请求不一致', 422)
    _iam(db, p); _nonce(db, p, body['request_id'], shared=False)
    rows, commands, _ = _inventory(db, p, lock=True,
        target_producer=body.get('producer_id'), target_plan=plan_id)
    exact = raw.decode('utf-8', errors='strict'); sha = hashlib.sha256(raw).hexdigest()
    prior = next((command for command in commands.values() if command.request_id == body['request_id']), None)
    if prior is not None:
        need(prior.operation == operation and prior.path == actual_path and prior.method == actual_method
            and prior.exact_body == exact and prior.wire_sha256 == sha,
            '原编号已经用于另一正文或范围，未覆盖或建立新操作')
        return copy.deepcopy(prior.data['result'])
    _iam(db, p, write=True)
    if operation == 'create':
        producer_id = body['producer_id']; row = None
    else:
        row = _owned(rows, p, plan_id); producer_id = row.producer_id
        need(row.status == 'draft', '此计划已确认，不能原地修改；请另建计划，原稿保留')
        need(body['expected_version'] == row.version, '计划版本已变化，原输入保留，请核对原请求或刷新')
        need(body['revision'] == row.revision and body['source_snapshot_hash'] == row.source_snapshot_hash,
            '计划原来源和配置版本固定，不能静默换基')
    _owner(db, p, producer_id)
    source = runtime().load_source(db, p, producer_id, body['revision'], lock=True)
    snapshot, _, _ = _inputs(source)
    need(snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id
        and snapshot['producer']['id'] == producer_id and snapshot['snapshot_hash'] == body['source_snapshot_hash'],
        '原来源、账号或来源快照已变化，原计划保留')
    if row is not None:
        need(same(source, row.data['source_context']), '计划完整原来源已变化，不能换基或覆盖人稿')
    ledger = validate_draft(row.data['ledger'] if operation == 'confirm' else body['ledger'], source)
    when = m.now(); saved_at = _utc(when); plan = None
    if operation == 'confirm':
        try:
            plan = _confirmed_plan(source, ledger, actor_id=p.user_id,
                request_id=body['request_id'], saved_at=saved_at)
        except planner.ScopedDesignPlanError:
            raise HTTPException(409, '计划尚未完整分派全部原声明，或范围/共享/背景关系不完整；原草稿保留，未确认或调用模型') from None
    need(row is not None or len(rows) < MAX_PLANS, '完整计划库存已达当前容量，未截断或覆盖旧稿')
    if row is None:
        row = Plan(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id, producer_id=producer_id,
            request_id=body['request_id'], revision=body['revision'],
            source_snapshot_hash=body['source_snapshot_hash'], version=1,
            status='draft', record_hash='', created_at=when,
            data={'source_context': copy.deepcopy(source), 'ledger': ledger, 'plan': None})
        previous = None
    else:
        previous = row.record_hash
        need(row.version < MAX_HISTORY, '计划已达完整历史容量；未覆盖或截断旧稿')
        row.version += 1
    row.status = 'confirmed' if operation == 'confirm' else 'draft'
    row.data = {'source_context': copy.deepcopy(source), 'ledger': ledger, 'plan': plan}
    row.record_hash = digest(_snapshot(row))
    confirmation = _confirmation(plan); plan_hash = plan['plan_hash'] if plan is not None else None
    command = Command(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        producer_id=producer_id, plan_id=row.id, plan_version=row.version,
        request_id=body['request_id'], operation=operation, method=actual_method,
        path=actual_path, exact_body=exact, wire_sha256=sha, body_hash=digest(body),
        created_at=when, command_hash='', data={'schema': COMMAND, 'body': copy.deepcopy(body),
            'ledger': copy.deepcopy(ledger), 'confirmation': confirmation, 'plan_hash': plan_hash,
            'previous_hash': previous, 'record_snapshot': _snapshot(row),
            'result': _result(row, operation, plan_hash=plan_hash)})
    command.command_hash = digest(_command_pin(command))
    # Scalar foreign keys have no mapper relationship ordering. All rows still
    # commit or roll back together in the API transaction.
    db.add(row); db.flush(); db.add(command); db.flush()
    audit(db, p, COMMAND_AUDIT, command.id, _command_audit(command))
    audit(db, p, STATE_AUDIT, row.id, _state_audit(row, command, row.record_hash))
    db.flush()
    return copy.deepcopy(command.data['result'])
