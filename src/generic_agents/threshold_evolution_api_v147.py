"""Human feedback on cold-checked rule receipts; no evaluation or execution API."""
import copy
import hashlib
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, StrictStr, ValidationError, field_validator
from sqlalchemy import and_, func, or_, select

from .contracts import Contract
from .db import session
from .models import Audit, uid
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit, check_version, scoped
from .threshold_evolution_models_v147 import FeedbackRevision, ThresholdCommand
from .threshold_evolution_protocol_v147 import command_fingerprint, read_wire, wire_sha256
from .typed_payload_codec_v131 import canonical_json

router = APIRouter(prefix='/evolution')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
ROOT = '/api/local-rules/evolution'
ERROR = '原反馈、修订链或保存请求无法核对；原记录保留，请核对原处理依据'
COMMAND_AUDIT = 'threshold_evolution.command_saved_v147'
AUDIT_SCHEMA = 'threshold-evolution-command-audit-v147'
AUDIT_FIELDS = {'schema', 'command_id', 'command_hash', 'request_id', 'method', 'path',
    'operation', 'fingerprint', 'wire_sha256', 'actor_id', 'workspace_id', 'feedback_id',
    'root_id', 'revision', 'feedback_version', 'observation_id', 'baseline_capability_id', 'rule_id'}
LEGACY_ERROR = '早期反馈缺少完整审计库存，本版本不能恢复其权威；原记录保留，不会补签或迁移旧依据'
LABEL = Literal['should_alert', 'should_not_alert', 'uncertain']
BASIS_FIELDS = {'schema', 'basis_hash', 'observation_id', 'observation_version',
    'baseline_capability_id', 'baseline_capability_hash', 'rule_id', 'field', 'field_label',
    'unit', 'operator', 'threshold', 'actual_value', 'actual_hit', 'instance_id',
    'release_id', 'entity_id', 'source_id'}


def _need(value, message=ERROR):
    if not value: raise HTTPException(409, message)


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value: raise ValueError('请保留原小写UUID编号')
    return value


class Command(Contract):
    request_id: StrictStr

    @field_validator('request_id')
    @classmethod
    def request_uuid(cls, value): return _uuid(value)


class FeedbackFields(Command):
    label: LABEL | None
    reason: StrictStr = Field(max_length=2000)

    @field_validator('reason')
    @classmethod
    def safe_reason(cls, value):
        if '\x00' in value: raise ValueError('理由不能含无效字符')
        value.encode('utf-8')
        return value


class CreateFeedback(FeedbackFields):
    observation_id: StrictStr
    rule_id: StrictStr = Field(min_length=1, max_length=80)
    expected_observation_version: int = Field(strict=True, ge=1)

    @field_validator('observation_id')
    @classmethod
    def observation_uuid(cls, value): return _uuid(value)


class SaveFeedback(FeedbackFields):
    expected_version: int = Field(strict=True, ge=1)


class ReviseFeedback(FeedbackFields):
    expected_head_revision: int = Field(strict=True, ge=1)


class SubmitFeedback(Command):
    expected_version: int = Field(strict=True, ge=1)
    confirm: Literal[True]

    @field_validator('confirm', mode='before')
    @classmethod
    def explicit_confirm(cls, value):
        if value is not True: raise ValueError('请明确确认提交判断')
        return value


class CancelFeedback(Command):
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or '\x00' in value: raise ValueError('请说明取消草稿的理由')
        value.encode('utf-8')
        return value


async def _request_wire(request: Request):
    raw = await request.body()
    try:
        value = read_wire(raw)
        if type(value) is not dict: raise ValueError()
        return raw, value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422, '请保留有效、无重复字段的原始JSON请求') from None


Wire = Annotated[tuple, Depends(_request_wire)]


def _parse(model, value):
    try:
        body = model.model_validate(value)
        _need(_hash(body.model_dump()) == _hash(value), '请求字段不能被自动补齐、转换或更换')
        return body
    except ValidationError as error:
        raise HTTPException(422, [{'field': '.'.join(map(str, item['loc'])), 'message': item['msg']}
                                  for item in error.errors()]) from None


def _identify(method, path, value):
    tail = path.removeprefix(ROOT + '/')
    if path == ROOT + '/feedback' and method == 'POST':
        body = _parse(CreateFeedback, value)
        return 'feedback_create', {'observation_id': body.observation_id, 'rule_id': body.rule_id}, body
    pieces = tail.split('/')
    _need(len(pieces) in (2, 3) and pieces[0] == 'feedback')
    try: identity = _uuid(pieces[1])
    except (ValueError, TypeError): raise HTTPException(422, '反馈编号不符合原请求') from None
    if len(pieces) == 2 and method == 'PUT':
        return 'feedback_save', {'feedback_id': identity}, _parse(SaveFeedback, value)
    _need(method == 'POST' and len(pieces) == 3)
    operation = pieces[2]
    models = {'submit': SubmitFeedback, 'cancel': CancelFeedback, 'revisions': ReviseFeedback}
    _need(operation in models)
    return 'feedback_' + operation, {'feedback_id': identity}, _parse(models[operation], value)


def _basis(db, p, observation_id, rule_id, expected=None):
    from .threshold_evolution_basis_v147 import feedback_basis
    return feedback_basis(db, p, observation_id, rule_id, expected)


def _public_basis(value):
    _need(type(value) is dict and set(value) == BASIS_FIELDS | {'proof'})
    return {key: copy.deepcopy(value[key]) for key in BASIS_FIELDS}


def _snapshot(row):
    return {key: copy.deepcopy(getattr(row, key)) for key in (
        'id', 'workspace_id', 'actor_id', 'root_id', 'revision', 'previous_id',
        'observation_id', 'baseline_capability_id', 'rule_id', 'status', 'version', 'data')}


def _record_hash(row):
    value = _snapshot(row)
    value['data'].pop('record_hash', None)
    return _hash(value)


def _seal(row):
    row.data = {**row.data, 'record_hash': _record_hash(row)}


def _command_payload(row):
    return {'schema': 'threshold-evolution-command-pin-v147',
        **{key: getattr(row, key) for key in ('id', 'workspace_id', 'actor_id', 'request_id',
           'operation', 'method', 'path', 'wire_sha256', 'fingerprint', 'feedback_id')}, 'data': row.data}


def _legacy_audits(db, p):
    # An old hash-only Audit cannot safely identify its lost command or root.
    # Do not interpret that absence as permission to issue replacement records.
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
                                             Audit.action == COMMAND_AUDIT)))
    commands = set(); nonces = set()
    for row in rows:
        _need(type(row.data) is dict and set(row.data) == AUDIT_FIELDS, LEGACY_ERROR)
        value = _checked_audit(p, row)
        nonce = (value['actor_id'], value['request_id'])
        _need(value['command_id'] not in commands and nonce not in nonces)
        commands.add(value['command_id']); nonces.add(nonce)


def _audit_pin(command, snapshot):
    return {'schema': AUDIT_SCHEMA,
        **{key: getattr(command, key) for key in ('request_id', 'method', 'path', 'operation',
            'fingerprint', 'wire_sha256', 'actor_id', 'workspace_id', 'feedback_id', 'command_hash')},
        'command_id': command.id, 'root_id': snapshot['root_id'], 'revision': snapshot['revision'],
        'feedback_version': snapshot['version'], 'observation_id': snapshot['observation_id'],
        'baseline_capability_id': snapshot['baseline_capability_id'], 'rule_id': snapshot['rule_id']}


def _checked_audit(p, row):
    value = row.data
    _need(row.workspace_id == p.workspace_id and row.action == COMMAND_AUDIT
          and type(value) is dict and set(value) == AUDIT_FIELDS and value['schema'] == AUDIT_SCHEMA)
    try:
        for key in ('command_id', 'request_id', 'actor_id', 'workspace_id', 'feedback_id', 'root_id',
                    'observation_id', 'baseline_capability_id'):
            _uuid(value[key])
        for key in ('command_hash', 'fingerprint', 'wire_sha256'):
            _need(type(value[key]) is str and len(value[key]) == 64
                  and all(ch in '0123456789abcdef' for ch in value[key]))
        _need(value['workspace_id'] == p.workspace_id and row.actor_id == value['actor_id']
              and row.object_id == value['command_id']
              and type(value['revision']) is int and value['revision'] >= 1
              and type(value['feedback_version']) is int and value['feedback_version'] >= 1
              and type(value['rule_id']) is str and 1 <= len(value['rule_id']) <= 80
              and value['operation'] in {'feedback_create', 'feedback_save', 'feedback_submit',
                                          'feedback_cancel', 'feedback_revisions'})
        _need(value['fingerprint'] == command_fingerprint(value['method'], value['path'],
            value['workspace_id'], value['actor_id'], value['wire_sha256']))
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise HTTPException(409, ERROR) from None
    return value


def _nonce_audits(db, p, nonce):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == COMMAND_AUDIT, Audit.actor_id == p.user_id,
        Audit.data['request_id'].as_string() == nonce)))
    for row in rows: _checked_audit(p, row)
    return rows


def _feedback_row(db, p, identity, lock=False):
    query = select(FeedbackRevision).where(FeedbackRevision.workspace_id == p.workspace_id,
                                          FeedbackRevision.id == identity)
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        trace = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == COMMAND_AUDIT, or_(Audit.data['feedback_id'].as_string() == identity,
                                               Audit.data['root_id'].as_string() == identity)).limit(1))
        _need(trace is None)
        raise HTTPException(404, '反馈不存在或不在当前工作区')
    return row


def _checked_command(db, p, row):
    _need(row.workspace_id == p.workspace_id and row.version == 1 and row.feedback_id is not None)
    value = row.data
    _need(type(value) is dict and set(value) == {'schema', 'wire_text', 'owner_refs', 'result_snapshot'}
          and value['schema'] == 'threshold-evolution-command-record-v147'
          and type(value['wire_text']) is str and type(value['owner_refs']) is dict)
    try:
        raw = value['wire_text'].encode('utf-8')
        wire = read_wire(raw)
        operation, owners, body = _identify(row.method, row.path, wire)
        signature = command_fingerprint(row.method, row.path, row.workspace_id, row.actor_id, wire_sha256(raw))
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise HTTPException(409, ERROR) from None
    except HTTPException:
        raise HTTPException(409, ERROR) from None
    _need(operation == row.operation and owners == value['owner_refs'] and body.request_id == row.request_id
          and wire_sha256(raw) == row.wire_sha256 and signature == row.fingerprint
          and _hash(_command_payload(row)) == row.command_hash)
    snapshot = value['result_snapshot']
    _need(type(snapshot) is dict and set(snapshot) == {'id', 'workspace_id', 'actor_id', 'root_id',
        'revision', 'previous_id', 'observation_id', 'baseline_capability_id', 'rule_id', 'status', 'version', 'data'}
          and snapshot['id'] == row.feedback_id and snapshot['workspace_id'] == p.workspace_id
          and snapshot['actor_id'] == row.actor_id and type(snapshot['version']) is int)
    saved_data = snapshot['data']
    _need(type(saved_data) is dict)
    unsigned = copy.deepcopy(snapshot); unsigned['data'].pop('record_hash', None)
    _need(saved_data.get('record_hash') == _hash(unsigned))
    if operation in {'feedback_create', 'feedback_revisions', 'feedback_save'}:
        _need(saved_data.get('label') == body.label and saved_data.get('reason') == body.reason
              and snapshot['status'] == 'draft' and saved_data.get('cancel_reason') is None)
    if operation == 'feedback_create':
        _need(snapshot['observation_id'] == body.observation_id and snapshot['rule_id'] == body.rule_id
              and snapshot['root_id'] == snapshot['id'] and snapshot['revision'] == 1
              and snapshot['previous_id'] is None and snapshot['version'] == 1)
    elif operation == 'feedback_revisions':
        _need(snapshot['root_id'] == owners['feedback_id'] and snapshot['version'] == 1
              and snapshot['revision'] == body.expected_head_revision + 1)
    else:
        _need(snapshot['id'] == owners['feedback_id'] and snapshot['version'] == body.expected_version + 1)
        if operation == 'feedback_submit': _need(snapshot['status'] == 'submitted')
        if operation == 'feedback_cancel':
            _need(snapshot['status'] == 'cancelled' and saved_data.get('cancel_reason') == body.reason)
    audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == row.id, Audit.action == COMMAND_AUDIT).limit(2)))
    _need(len(audits) == 1 and _checked_audit(p, audits[0]) == _audit_pin(row, snapshot))
    return snapshot


def _inventory_chain(db, p, root_id, lock=False):
    from .threshold_feedback_history_v148 import inventory_chain
    return inventory_chain(db, p, root_id, lock=lock)


def _chain(db, p, root_id, *, lock=False):
    from .threshold_feedback_history_v148 import require_current_basis
    rows = _inventory_chain(db, p, root_id, lock=lock)
    require_current_basis(db, p, rows)
    return rows


def _entry(row):
    label = row.data['label']; hit = row.data['basis']['actual_hit']
    classification = ('TP' if hit else 'FN') if label == 'should_alert' else (
        ('FP' if hit else 'TN') if label == 'should_not_alert' else 'unknown')
    return {key: copy.deepcopy(getattr(row, key)) for key in (
        'id', 'version', 'root_id', 'revision', 'previous_id', 'status', 'actor_id')} | {
        'label': row.data['label'], 'reason': row.data['reason'], 'cancel_reason': row.data['cancel_reason'],
        'classification': classification}


def _view(db, p, identity):
    _legacy_audits(db, p)
    row = _feedback_row(db, p, identity)
    rows = _chain(db, p, row.root_id)
    row = next(item for item in rows if item.id == identity)
    submitted = [item for item in rows if item.status == 'submitted']
    editable = rows[-1].id == row.id and row.status == 'draft' and row.actor_id == p.user_id and p.role in {'admin', 'operator'}
    return {'schema': 'threshold-evolution-feedback-view-v147', **_entry(row),
        'basis': _public_basis(row.data['basis']), 'history': [_entry(item) for item in rows],
        'latest_submitted': _entry(submitted[-1]) if submitted else None,
        'can_edit': editable, 'can_cancel': editable,
        'can_submit': editable and row.data['label'] is not None and bool(row.data['reason'].strip()),
        'can_revise': rows[-1].status != 'draft' and p.role in {'admin', 'operator'}}


def _lock_nonce(db, p, nonce):
    key = int(_hash({'scope': 'threshold-evolution-command-v147', 'workspace_id': p.workspace_id,
                     'actor_id': p.user_id, 'request_id': nonce})[:15], 16)
    db.execute(select(func.pg_advisory_xact_lock(key)))


def _begin(db, p, request, wire):
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    _legacy_audits(db, p)
    raw, value = wire
    try:
        nonce = _uuid(value.get('request_id'))
    except (ValueError, TypeError):
        raise HTTPException(422, '请保留原小写UUID请求编号') from None
    _lock_nonce(db, p, nonce)
    from .threshold_request_recovery_api_v148 import check_original_nonce
    check_original_nonce(db, p, nonce)
    operation, owners, body = _identify(request.method, request.url.path, value)
    original = db.scalar(select(ThresholdCommand).where(ThresholdCommand.workspace_id == p.workspace_id,
        ThresholdCommand.actor_id == p.user_id, ThresholdCommand.request_id == body.request_id)
        .with_for_update().execution_options(populate_existing=True))
    issued = _nonce_audits(db, p, body.request_id)
    if original:
        _need(len(issued) == 1 and issued[0].object_id == original.id)
        _checked_command(db, p, original)
        _need(original.method == request.method and original.path == request.url.path
              and original.operation == operation and original.data['owner_refs'] == owners
              and original.wire_sha256 == wire_sha256(raw), '同一请求编号不能更换正文、操作或反馈对象')
        return body, operation, owners, original
    _need(not issued, '此原请求留有保存审计但原记录缺失，不能作为新请求重发；请恢复原依据')
    return body, operation, owners, None


def _save_command(db, p, request, raw, body, operation, owners, row):
    db.flush()
    command = ThresholdCommand(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=body.request_id, operation=operation, method=request.method, path=request.url.path,
        wire_sha256=wire_sha256(raw), fingerprint=command_fingerprint(request.method, request.url.path,
            p.workspace_id, p.user_id, wire_sha256(raw)), feedback_id=row.id,
        data={'schema': 'threshold-evolution-command-record-v147', 'wire_text': raw.decode('utf-8'),
              'owner_refs': owners, 'result_snapshot': _snapshot(row)})
    command.command_hash = _hash(_command_payload(command))
    db.add(command)
    audit(db, p, COMMAND_AUDIT, command.id, _audit_pin(command, command.data['result_snapshot']))
    db.flush()


def _root_for_basis(db, p, basis):
    _legacy_audits(db, p)
    rows = list(db.scalars(select(FeedbackRevision).where(FeedbackRevision.workspace_id == p.workspace_id,
        FeedbackRevision.observation_id == basis['observation_id'],
        FeedbackRevision.baseline_capability_id == basis['baseline_capability_id'], FeedbackRevision.rule_id == basis['rule_id'])))
    roots = {row.root_id for row in rows}
    audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == COMMAND_AUDIT,
        Audit.data['observation_id'].as_string() == basis['observation_id'],
        Audit.data['baseline_capability_id'].as_string() == basis['baseline_capability_id'],
        Audit.data['rule_id'].as_string() == basis['rule_id'])))
    pins = [_checked_audit(p, row) for row in audits]
    audit_roots = {value['root_id'] for value in pins}
    _need(len(roots) <= 1)
    _need(roots == audit_roots, '原反馈审计与修订库存不完整；不会重新建立反馈或补签原依据')
    return next(iter(roots)) if roots else None


@router.get('/observations/{identity}/feedback-context')
def feedback_context(identity: str, rule_id: str, p: P, db: DB):
    try:
        basis = _basis(db, p, identity, rule_id)
    except HTTPException as error:
        if error.status_code not in (409, 422): raise
        return {'schema': 'threshold-evolution-feedback-context-v147', 'supported': False,
                'issue': str(error.detail), 'basis': None, 'feedback': None}
    root = _root_for_basis(db, p, basis)
    feedback = _view(db, p, _chain(db, p, root)[-1].id) if root else None
    return {'schema': 'threshold-evolution-feedback-context-v147', 'supported': True,
            'issue': None, 'basis': _public_basis(basis), 'feedback': feedback}


@router.post('/feedback', status_code=201)
def create_feedback(request: Request, wire: Wire, p: P, db: DB):
    body, operation, owners, prior = _begin(db, p, request, wire)
    if prior: return _view(db, p, prior.feedback_id)
    from .models import Observation
    scoped(db, Observation, body.observation_id, p, lock=True)
    basis = _basis(db, p, body.observation_id, body.rule_id, body.expected_observation_version)
    business = {key: basis[key] for key in ('observation_id', 'baseline_capability_id', 'rule_id')}
    db.execute(select(func.pg_advisory_xact_lock(int(_hash({'workspace_id': p.workspace_id,
        'scope': 'threshold-evolution-feedback-root-v147', **business})[:15], 16))))
    _need(_root_for_basis(db, p, basis) is None, '此原记录已有反馈，请打开已有反馈；他人未提交稿不能被覆盖')
    identity = uid()
    row = FeedbackRevision(id=identity, workspace_id=p.workspace_id, root_id=identity, revision=1,
        previous_id=None, actor_id=p.user_id, observation_id=body.observation_id,
        baseline_capability_id=basis['baseline_capability_id'], rule_id=body.rule_id, status='draft', version=1,
        data={'schema': 'threshold-evolution-feedback-record-v147', 'basis': basis,
              'label': body.label, 'reason': body.reason, 'cancel_reason': None})
    _seal(row); db.add(row)
    _save_command(db, p, request, wire[0], body, operation, owners, row)
    result = _view(db, p, row.id); db.commit(); return result


def _change(identity, request, wire, p, db, action):
    body, operation, owners, prior = _begin(db, p, request, wire)
    if prior: return _view(db, p, prior.feedback_id)
    target = _feedback_row(db, p, identity)
    rows = _chain(db, p, target.root_id, lock=True)
    head = rows[-1]
    if action == 'revisions':
        _need(identity == head.root_id and body.expected_head_revision == head.revision,
              '修订历史已变化，请打开最新节点后新建修订')
        _need(head.status != 'draft', '已有未提交草稿，请先处理原稿；不会覆盖他人输入')
        basis = _basis(db, p, head.observation_id, head.rule_id)
        row = FeedbackRevision(id=uid(), workspace_id=p.workspace_id, root_id=head.root_id,
            revision=head.revision + 1, previous_id=head.id, actor_id=p.user_id,
            observation_id=head.observation_id, baseline_capability_id=head.baseline_capability_id,
            rule_id=head.rule_id, status='draft', version=1,
            data={'schema': 'threshold-evolution-feedback-record-v147', 'basis': basis,
                  'label': body.label, 'reason': body.reason, 'cancel_reason': None})
        _seal(row); db.add(row)
    else:
        _need(head.id == identity and head.status == 'draft', '只可修改最新未提交草稿，原提交与取消历史保留')
        _need(head.actor_id == p.user_id, '此草稿由另一位操作者保存，请先协调原稿，不能覆盖')
        check_version(head, body.expected_version)
        row = head; row.version += 1
        if action == 'save': row.data = {**row.data, 'label': body.label, 'reason': body.reason}
        elif action == 'submit':
            _need(row.data['label'] is not None and bool(row.data['reason'].strip()), '请填写明确判断和理由后再提交')
            row.status = 'submitted'
        elif action == 'cancel':
            row.status = 'cancelled'; row.data = {**row.data, 'cancel_reason': body.reason}
        else: _need(False)
        _seal(row)
    _save_command(db, p, request, wire[0], body, operation, owners, row)
    if row.status=='submitted':
        from .threshold_policy_monitor_v153 import enqueue_feedback
        enqueue_feedback(db,p,row)
    result = _view(db, p, row.id); db.commit(); return result


@router.put('/feedback/{identity}')
def save_feedback(identity: str, request: Request, wire: Wire, p: P, db: DB):
    return _change(identity, request, wire, p, db, 'save')


@router.post('/feedback/{identity}/submit')
def submit_feedback(identity: str, request: Request, wire: Wire, p: P, db: DB):
    return _change(identity, request, wire, p, db, 'submit')


@router.post('/feedback/{identity}/cancel')
def cancel_feedback(identity: str, request: Request, wire: Wire, p: P, db: DB):
    return _change(identity, request, wire, p, db, 'cancel')


@router.post('/feedback/{identity}/revisions', status_code=201)
def revise_feedback(identity: str, request: Request, wire: Wire, p: P, db: DB):
    return _change(identity, request, wire, p, db, 'revisions')


@router.get('/feedback/{identity}')
def get_feedback(identity: str, p: P, db: DB):
    return _view(db, p, identity)


@router.get('/feedback')
def list_feedback(p: P, db: DB, observation_id: str | None = None,
                  baseline_capability_id: str | None = None,
                  limit: int = Query(default=20, ge=1, le=50), after: str | None = None):
    _legacy_audits(db, p)
    inventory = select(Audit, FeedbackRevision.id, FeedbackRevision.root_id,
        FeedbackRevision.revision, FeedbackRevision.observation_id,
        FeedbackRevision.baseline_capability_id, FeedbackRevision.rule_id).outerjoin(
            FeedbackRevision, and_(FeedbackRevision.workspace_id == Audit.workspace_id,
                FeedbackRevision.id == Audit.data['root_id'].as_string())).where(
                    Audit.workspace_id == p.workspace_id, Audit.action == COMMAND_AUDIT)
    if observation_id is not None:
        inventory = inventory.where(Audit.data['observation_id'].as_string() == observation_id)
    if baseline_capability_id is not None:
        inventory = inventory.where(Audit.data['baseline_capability_id'].as_string() == baseline_capability_id)
    # One statement sees a consistent inventory even if another root commits.
    # Audit-only roots must not disappear as an empty or truncated list.
    for issued, identity, root, revision, obs, cap, rule in db.execute(inventory):
        pin = _checked_audit(p, issued)
        _need(identity == root == pin['root_id'] and revision == 1
              and obs == pin['observation_id'] and cap == pin['baseline_capability_id'] and rule == pin['rule_id'])
    query = select(FeedbackRevision).where(FeedbackRevision.workspace_id == p.workspace_id,
                                         FeedbackRevision.revision == 1)
    if observation_id is not None: query = query.where(FeedbackRevision.observation_id == observation_id)
    if baseline_capability_id is not None: query = query.where(FeedbackRevision.baseline_capability_id == baseline_capability_id)
    if after:
        anchor = scoped(db, FeedbackRevision, after, p)
        _need(anchor.revision == 1)
        query = query.where(or_(FeedbackRevision.created_at < anchor.created_at,
            and_(FeedbackRevision.created_at == anchor.created_at, FeedbackRevision.id < anchor.id)))
    rows = list(db.scalars(query.order_by(FeedbackRevision.created_at.desc(), FeedbackRevision.id.desc()).limit(limit + 1)))
    return {'schema': 'threshold-evolution-feedback-list-v147',
        'feedback': [_view(db, p, _chain(db, p, row.id)[-1].id) for row in rows[:limit]],
        'next_after': rows[limit - 1].id if len(rows) > limit else None}


@router.get('/requests/{request_id}')
def recover_request(request_id: str, p: P, db: DB, expected_sha256: str,
                    original_method: Literal['POST', 'PUT'], original_path: str):
    _legacy_audits(db, p)
    try:
        _uuid(request_id)
        expected_fingerprint = command_fingerprint(original_method, original_path,
            p.workspace_id, p.user_id, expected_sha256)
    except (ValueError, TypeError): raise HTTPException(422, '请保留原请求方法、路径和原文摘要') from None
    _lock_nonce(db, p, request_id)
    from .threshold_request_recovery_api_v148 import check_original_nonce
    check_original_nonce(db, p, request_id)
    row = db.scalar(select(ThresholdCommand).where(ThresholdCommand.workspace_id == p.workspace_id,
        ThresholdCommand.actor_id == p.user_id, ThresholdCommand.request_id == request_id))
    issued = _nonce_audits(db, p, request_id)
    if row is None:
        _need(not issued, '原请求已有保存审计但命令记录缺失，不能当作未找到；请恢复原依据')
        return {'schema': 'threshold-evolution-request-recovery-v147', 'found': False, 'feedback': None}
    _need(len(issued) == 1 and issued[0].object_id == row.id)
    _checked_command(db, p, row)
    _need(row.method == original_method and row.path == original_path
          and row.wire_sha256 == expected_sha256 and row.fingerprint == expected_fingerprint,
          '原请求路径、方法或原文摘要不一致；不能借用已保存请求')
    return {'schema': 'threshold-evolution-request-recovery-v147', 'found': True,
            'feedback': _view(db, p, row.feedback_id)}
