"""Exact-request retirement; no model, worker, device or policy execution."""
import copy
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import and_, or_, select

from . import threshold_evolution_api_v147 as old
from .contracts import digest
from .models import Audit, Capability, Observation, uid
from .relations import lock_current_actor
from .security import require
from .services import audit, scoped
from .threshold_evolution_models_v147 import ThresholdCommand
from .threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from .threshold_request_recovery_models_v148 import ThresholdRequestRetirement as Decision
from .threshold_request_recovery_protocol_v148 import (
    RetirementCommand, read_retirement_wire, retirement_identity, retirement_receipt_hash,
)

router = APIRouter(prefix='/evolution')
ACTION = 'threshold_evolution.request_retired_v148'
ERROR = '原请求的保存或停止接收记录无法完整核对；原稿保留，请恢复原依据'
AUDIT_FIELDS = {'schema', 'decision_id', 'command_hash', 'request_id', 'original_request_id',
                'actor_id', 'workspace_id', 'original_method', 'original_path',
                'original_sha256', 'wire_sha256', 'outcome', 'owner_refs'}
DATA_FIELDS = {'schema', 'wire_text', 'identity', 'owner_pin', 'fingerprint', 'saved', 'retirement'}


def _need(value, message=ERROR):
    if not value:
        raise HTTPException(409, message)


def _payload(row):
    return {'schema': 'threshold-request-retirement-pin-v148',
        **{key: getattr(row, key) for key in ('id', 'workspace_id', 'actor_id', 'request_id',
            'original_request_id', 'original_method', 'original_path', 'original_sha256',
            'wire_sha256', 'outcome')}, 'data': row.data}


def _audit_pin(row):
    return {'schema': 'threshold-request-retirement-audit-v148', 'decision_id': row.id,
        **{key: getattr(row, key) for key in ('command_hash', 'request_id', 'original_request_id',
            'actor_id', 'workspace_id', 'original_method', 'original_path', 'original_sha256',
            'wire_sha256', 'outcome')}, 'owner_refs': copy.deepcopy(row.data['identity']['owner_refs'])}


def _parse(raw):
    try:
        value = read_retirement_wire(raw)
        body = RetirementCommand.model_validate(value)
        _need(old._hash(body.model_dump(by_alias=True)) == old._hash(value))
        return body
    except (ValueError, TypeError, UnicodeError, RecursionError, ValidationError):
        raise HTTPException(422, '请保留完整的原请求、停止接收理由和明确确认') from None


async def _wire(request: Request):
    raw = await request.body()
    return raw, _parse(raw)


RetirementWire = Annotated[tuple, Depends(_wire)]


def _saved(db, p, command):
    snapshot = old._checked_command(db, p, command)
    # A self-consistent command alone cannot conceal a missing revision or Audit.
    old._inventory_chain(db, p, snapshot['root_id'])
    return {'command_id': command.id, 'feedback_id': snapshot['id'], 'root_id': snapshot['root_id'],
            'revision': snapshot['revision'], 'version': snapshot['version'],
            'status': snapshot['status'], 'result_hash': old._hash(snapshot)}


def _checked(db, p, row, issued):
    _need(row.workspace_id == p.workspace_id and row.version == 1
          and row.outcome in {'saved', 'retired'} and type(row.data) is dict
          and set(row.data) == DATA_FIELDS and row.data['schema'] == 'threshold-request-retirement-record-v148')
    try:
        old._uuid(row.id); old._uuid(row.actor_id); old._uuid(row.workspace_id)
        raw = row.data['wire_text'].encode('utf-8')
        body = _parse(raw)
        identity = retirement_identity(row.original_request_id, body)
        fingerprint = command_fingerprint(row.original_method, row.original_path,
            row.workspace_id, row.actor_id, row.original_sha256)
        _need(body.request_id == row.request_id and body.original_method == row.original_method
            and body.original_path == row.original_path and body.original_sha256 == row.original_sha256
            and wire_sha256(raw) == row.wire_sha256 and identity == row.data['identity']
            and fingerprint == row.data['fingerprint']
            and old._hash(_payload(row)) == row.command_hash)
        _need(issued is not None and issued.workspace_id == p.workspace_id
            and issued.actor_id == row.actor_id and issued.action == ACTION and issued.object_id == row.id
            and type(issued.data) is dict and set(issued.data) == AUDIT_FIELDS
            and old._hash(issued.data) == old._hash(_audit_pin(row)))
        _owner(db, p, identity, actor_id=row.actor_id, retained=row.data['owner_pin'])
        command = db.scalar(select(ThresholdCommand).where(ThresholdCommand.workspace_id == p.workspace_id,
            ThresholdCommand.actor_id == row.actor_id, ThresholdCommand.request_id == row.original_request_id))
        traces = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == old.COMMAND_AUDIT, Audit.actor_id == row.actor_id,
            Audit.data['request_id'].as_string() == row.original_request_id)))
        if row.outcome == 'saved':
            _need(command is not None and len(traces) == 1 and traces[0].object_id == command.id
                  and row.data['retirement'] is None)
            _match(command, row.original_method, row.original_path, row.original_sha256, fingerprint)
            _need(old._hash(row.data['saved']) == old._hash(_saved(db, p, command)))
        else:
            _need(command is None and not traces and row.data['saved'] is None)
            receipt = row.data['retirement']
            _need(type(receipt) is dict and set(receipt) == {
                'schema', 'id', 'request_id', 'original_request_id', 'actor_id', 'workspace_id',
                'original_method', 'original_path', 'original_sha256', 'reason', 'outcome', 'receipt_hash'})
            unsigned = {key: value for key, value in receipt.items() if key != 'receipt_hash'}
            _need(receipt['receipt_hash'] == retirement_receipt_hash(unsigned)
                  and old._hash(unsigned) == old._hash(_unsigned(row, body)))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise HTTPException(409, ERROR) from None
    except HTTPException as error:
        if error.status_code == 403:
            raise
        raise HTTPException(409, ERROR) from None
    return row


def _inventory(db, p):
    old._legacy_audits(db, p)
    # One SQL snapshot includes orphan rows and orphan independent Audits.
    pairs = list(db.execute(select(Decision, Audit).join(Audit, and_(
        Audit.workspace_id == Decision.workspace_id, Audit.action == ACTION,
        Audit.object_id == Decision.id), full=True).where(or_(
            Decision.workspace_id == p.workspace_id,
            and_(Audit.workspace_id == p.workspace_id, Audit.action == ACTION)))))
    rows = []; ids = set(); commands = set(); originals = set()
    for row, issued in pairs:
        _need(row is not None and issued is not None and row.id not in ids)
        _checked(db, p, row, issued)
        command_key = (row.actor_id, row.request_id)
        original_key = (row.actor_id, row.original_request_id)
        _need(command_key not in commands and original_key not in originals)
        ids.add(row.id); commands.add(command_key); originals.add(original_key); rows.append(row)
    # A retirement command nonce cannot borrow any ordinary or original nonce.
    _need(not (commands & originals))
    for row in rows:
        _need(db.scalar(select(ThresholdCommand.id).where(ThresholdCommand.workspace_id == p.workspace_id,
            ThresholdCommand.actor_id == row.actor_id, ThresholdCommand.request_id == row.request_id)) is None)
        _need(db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == old.COMMAND_AUDIT, Audit.actor_id == row.actor_id,
            Audit.data['request_id'].as_string() == row.request_id)) is None)
    return rows


def check_original_nonce(db, p, nonce):
    """Called while the legacy writer holds its one original nonce lock."""
    rows = _inventory(db, p)
    _need(not any(row.actor_id == p.user_id and row.request_id == nonce for row in rows),
          '这个编号已用于停止接收命令，不能用于保存判断')
    _need(not any(row.actor_id == p.user_id and row.original_request_id == nonce
                  and row.outcome == 'retired' for row in rows),
          '这个原请求已永久停止接收，请核对结果后修改原稿并另行保存')


def _match(command, method, path, signature, fingerprint):
    _need(command.method == method and command.path == path and command.wire_sha256 == signature
          and command.fingerprint == fingerprint, '原请求的方法、路径或原文摘要不一致')


def _original(db, p, nonce, method, path, signature):
    try:
        old._uuid(nonce)
        fingerprint = command_fingerprint(method, path, p.workspace_id, p.user_id, signature)
        # The state endpoint accepts only the same five closed feedback routes.
        # Validate a closed route without inventing an original command body.
        import re
        prefix = old.ROOT + '/feedback'
        valid = method == 'POST' and path == prefix
        match = re.fullmatch(re.escape(prefix) + r'/([0-9a-f-]{36})(?:/(submit|cancel|revisions))?', path)
        if match:
            old._uuid(match[1])
            valid = (method == 'PUT' and match[2] is None) or (method == 'POST' and match[2] is not None)
        if not valid:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, '请保留原请求方法、完整路径和原文摘要') from None
    command = db.scalar(select(ThresholdCommand).where(ThresholdCommand.workspace_id == p.workspace_id,
        ThresholdCommand.actor_id == p.user_id, ThresholdCommand.request_id == nonce))
    issued = old._nonce_audits(db, p, nonce)
    if command is None:
        _need(not issued)
        return None
    _need(len(issued) == 1 and issued[0].object_id == command.id)
    _match(command, method, path, signature, fingerprint)
    return _saved(db, p, command)


def _state(nonce, method, path, signature, saved, row):
    return {'schema': 'threshold-request-state-v148', 'original_request_id': nonce,
        'original_method': method, 'original_path': path, 'original_sha256': signature,
        'outcome': 'saved' if saved is not None else ('retired' if row else 'unknown'),
        'saved': saved, 'retirement': copy.deepcopy(row.data['retirement']) if row and row.outcome == 'retired' else None}


def _owner(db, p, identity, *, actor_id=None, retained=None, saved=None):
    owners = identity['owner_refs']
    if identity['operation'] == 'feedback_create':
        observation = scoped(db, Observation, owners['observation_id'], p)
        data = observation.data
        _need(type(data) is dict and type(data.get('reported_rule_contract')) is dict)
        cap = scoped(db, Capability, data.get('extractor_id'), p)
        # Only establish the original rule identity; publication, source health,
        # current observation review and feedback write eligibility are irrelevant.
        contract = data['reported_rule_contract']
        if retained is not None:
            _need(type(retained) is dict and set(retained) == {'observation_id', 'rule_id',
                'baseline_capability_id', 'extractor_hash', 'rule'}
                and retained['observation_id'] == observation.id
                and retained['baseline_capability_id'] == cap.id
                and retained['extractor_hash'] == contract.get('extractor_hash')
                and retained['rule_id'] == owners['rule_id']
                and type(retained['rule']) is dict and retained['rule'].get('id') == owners['rule_id'])
            return copy.deepcopy(retained)
        # Already-saved operations retain their original full machine proof;
        # current model contents cannot revoke recovery of that saved operation.
        if saved is not None:
            feedback = old._feedback_row(db, p, saved['feedback_id'])
            old._inventory_chain(db, p, feedback.root_id)
            manifest = feedback.data['basis']['proof']['extractor']['manifest']
        else:
            manifest = cap.data
        _need(type(manifest) is dict and digest(manifest) == contract.get('extractor_hash'))
        rules = manifest.get('specification', {}).get('rules')
        _need(type(rules) is list and len(rules) == 1 and type(rules[0]) is dict
              and rules[0].get('id') == owners['rule_id'])
        return {'observation_id': observation.id, 'rule_id': owners['rule_id'],
                'baseline_capability_id': cap.id, 'extractor_hash': contract['extractor_hash'],
                'rule': copy.deepcopy(rules[0])}
    else:
        target = old._feedback_row(db, p, owners['feedback_id'])
        old._inventory_chain(db, p, target.root_id)
        if identity['operation'] == 'feedback_revisions':
            _need(target.id == target.root_id)
        else:
            _need(target.actor_id == (actor_id or p.user_id), '请由原稿作者停止接收这份请求')
        value = {'feedback_id': target.id, 'root_id': target.root_id, 'actor_id': target.actor_id}
        if retained is not None:
            _need(old._hash(retained) == old._hash(value))
        return value


def _unsigned(row, body):
    return {'schema': 'threshold-request-retirement-receipt-v148', 'id': row.id,
        **{key: getattr(row, key) for key in ('request_id', 'original_request_id', 'actor_id',
            'workspace_id', 'original_method', 'original_path', 'original_sha256')},
        'reason': body.reason, 'outcome': 'retired'}


@router.get('/requests/{original_request_id}/status')
def request_status(original_request_id: str, p: old.P, db: old.DB, expected_sha256: str,
                   original_method: Literal['POST', 'PUT'], original_path: str):
    old._lock_nonce(db, p, original_request_id)
    rows = _inventory(db, p)
    _need(not any(row.actor_id == p.user_id and row.request_id == original_request_id for row in rows))
    saved = _original(db, p, original_request_id, original_method, original_path, expected_sha256)
    row = next((row for row in rows if row.actor_id == p.user_id
                and row.original_request_id == original_request_id), None)
    if row:
        _need(row.original_method == original_method and row.original_path == original_path
              and row.original_sha256 == expected_sha256)
    return _state(original_request_id, original_method, original_path, expected_sha256, saved, row)


@router.post('/requests/{original_request_id}/retire')
def retire_request(original_request_id: str, wire: RetirementWire, p: old.P, db: old.DB):
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    raw, body = wire
    try:
        identity = retirement_identity(original_request_id, body)
    except (ValueError, TypeError):
        raise HTTPException(422, '原请求编号、原文或反馈路径无法确认') from None
    for nonce in sorted((original_request_id, body.request_id)):
        old._lock_nonce(db, p, nonce)
    rows = _inventory(db, p)
    previous = next((row for row in rows if row.actor_id == p.user_id and row.request_id == body.request_id), None)
    if previous:
        _need(previous.original_request_id == original_request_id and previous.wire_sha256 == wire_sha256(raw)
              and previous.data['wire_text'] == raw.decode('utf-8'), '同一停止接收编号不能更换正文或原请求')
        return _state(original_request_id, body.original_method, body.original_path, body.original_sha256,
                      previous.data['saved'], previous)
    _need(not any(row.actor_id == p.user_id and (
        row.original_request_id in {original_request_id, body.request_id} or row.request_id == original_request_id)
        for row in rows), '原编号已被使用，请核对原请求结果，不要更换编号重发')
    _need(db.scalar(select(ThresholdCommand.id).where(ThresholdCommand.workspace_id == p.workspace_id,
        ThresholdCommand.actor_id == p.user_id, ThresholdCommand.request_id == body.request_id)) is None)
    _need(not old._nonce_audits(db, p, body.request_id))
    saved = _original(db, p, original_request_id, body.original_method, body.original_path, body.original_sha256)
    owner_pin = _owner(db, p, identity, saved=saved)
    row = Decision(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id, request_id=body.request_id,
        original_request_id=original_request_id, original_method=body.original_method,
        original_path=body.original_path, original_sha256=body.original_sha256,
        wire_sha256=wire_sha256(raw), outcome='saved' if saved else 'retired', version=1)
    unsigned = _unsigned(row, body)
    receipt = None if saved else {**unsigned, 'receipt_hash': retirement_receipt_hash(unsigned)}
    row.data = {'schema': 'threshold-request-retirement-record-v148', 'wire_text': raw.decode('utf-8'),
        'identity': identity, 'owner_pin': owner_pin,
        'fingerprint': command_fingerprint(body.original_method, body.original_path,
            p.workspace_id, p.user_id, body.original_sha256), 'saved': saved, 'retirement': receipt}
    row.command_hash = old._hash(_payload(row)); db.add(row)
    audit(db, p, ACTION, row.id, _audit_pin(row)); db.flush()
    _inventory(db, p)
    result = _state(original_request_id, body.original_method, body.original_path, body.original_sha256, saved, row)
    db.commit()
    return result
