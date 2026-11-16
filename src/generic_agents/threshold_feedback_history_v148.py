"""Checked feedback inventory and a read-only human-history projection.

Inventory proves what was saved, not that its machine evidence is still usable.
The V147 actionable view retains its independent full-basis comparison. No
reader here writes records, repairs evidence or grants candidate eligibility.
"""
import copy
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_, select

from .db import session
from .models import Audit, Observation
from .security import Principal, authenticated
from .services import scoped
from .threshold_evolution_models_v147 import FeedbackRevision, ThresholdCommand
from .typed_payload_codec_v131 import canonical_json


router = APIRouter(prefix='/evolution')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
ERROR = '原反馈、修订链或保存请求无法核对；原记录保留，请核对原处理依据'
UNAVAILABLE = '原机器依据目前无法核对；人工理由和提交历史保留，不能用于候选或策略'
_ENTRY = ('id', 'version', 'root_id', 'revision', 'previous_id', 'status', 'actor_id')
_REFERENCE = ('observation_id', 'instance_id', 'release_id', 'baseline_capability_id', 'rule_id', 'basis_hash')
_PROOF = {'schema', 'workspace_id', 'original_request', 'original_input_hash',
    'reported_rule_contract', 'machine', 'source', 'extractor', 'authoring_audit',
    'ingest_audit', 'job', 'invocation', 'data_event', 'receipt', 'memory',
    'source_time_verified', 'physical_state_verified', 'entity_state_updated'}


def _legacy():
    # local_rules composes routers before its constants exist. Importing the
    # legacy/basis modules only when called avoids a router-initialization cycle.
    from . import threshold_evolution_api_v147
    return threshold_evolution_api_v147


def _need(condition):
    if not condition:
        raise HTTPException(409, ERROR)


def checked_saved_basis(value, row, p):
    """Check the original 19-field basis bound by real Command/Audit inventory.

    This performs no current source/Invocation reads. Its original hash includes
    the entire saved proof and excludes only basis_hash/observation_version,
    exactly as feedback_basis originally issued it.
    """
    f = _legacy()
    try:
        _need(row.workspace_id == p.workspace_id and type(value) is dict
              and set(value) == f.BASIS_FIELDS | {'proof'}
              and value['schema'] == 'threshold-evolution-feedback-basis-v147')
        for key in ('observation_id', 'baseline_capability_id', 'instance_id', 'release_id', 'entity_id', 'source_id'):
            f._uuid(value[key])
        _need(value['observation_id'] == row.observation_id
              and value['baseline_capability_id'] == row.baseline_capability_id
              and value['rule_id'] == row.rule_id
              and type(value['observation_version']) is int and value['observation_version'] >= 1)
        for key in ('basis_hash', 'baseline_capability_hash'):
            _need(type(value[key]) is str and len(value[key]) == 64
                  and all(char in '0123456789abcdef' for char in value[key]))
        _need(type(value['rule_id']) is str and 1 <= len(value['rule_id']) <= 80
              and type(value['field']) is str and 1 <= len(value['field']) <= 100
              and type(value['field_label']) is str and bool(value['field_label'].strip())
              and (value['unit'] is None or type(value['unit']) is str and 1 <= len(value['unit']) <= 80)
              and value['operator'] in {'gt', 'lt'})
        _need(type(value['threshold']) in {int, float} and math.isfinite(value['threshold'])
              and abs(value['threshold']) <= 10**15
              and type(value['actual_value']) in {int, float} and math.isfinite(value['actual_value'])
              and type(value['actual_hit']) is bool)
        matched = (value['actual_value'] > value['threshold'] if value['operator'] == 'gt'
                   else value['actual_value'] < value['threshold'])
        _need(value['actual_hit'] is matched)
        proof = value['proof']
        _need(type(proof) is dict and set(proof) == _PROOF
              and proof['schema'] == 'threshold-evolution-machine-proof-v147'
              and proof['workspace_id'] == p.workspace_id
              and all(proof[key] is False for key in (
                  'source_time_verified', 'physical_state_verified', 'entity_state_updated')))
        _need(proof['source']['id'] == value['source_id']
              and proof['extractor']['id'] == value['baseline_capability_id']
              and proof['extractor']['hash'] == value['baseline_capability_hash']
              and proof['job']['observation_id'] == value['observation_id']
              and proof['job']['extractor_id'] == value['baseline_capability_id']
              and proof['invocation']['capability_id'] == value['baseline_capability_id']
              and proof['invocation']['capability_hash'] == value['baseline_capability_hash'])
        original, contract, receipt = proof['original_request'], proof['reported_rule_contract'], proof['receipt']
        _need(original['source_id'] == value['source_id'] and original['entity_id'] == value['entity_id']
              and original['extractor_id'] == value['baseline_capability_id']
              and contract['instance_id'] == value['instance_id'] and contract['release_id'] == value['release_id']
              and contract['entity_id'] == value['entity_id']
              and all(receipt[key] == value[key] for key in ('observation_id', 'instance_id', 'release_id', 'entity_id', 'source_id'))
              and receipt['extractor_id'] == value['baseline_capability_id'])
        _need(value['basis_hash'] == f._hash({key: item for key, item in value.items()
            if key not in {'basis_hash', 'observation_version'}}))
        canonical_json(value)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        raise HTTPException(409, ERROR) from None
    return copy.deepcopy(value)


def inventory_chain(db, p, root_id, lock=False):
    """Return checked ORM rows, retaining every R1 inventory/transition gate.

    A mutating caller may request the original root/revision locks, but must
    separately apply its current-basis and action/version/author gates. Read
    callers and retirement do not acquire Observation or feedback write locks.
    """
    f = _legacy()
    f._legacy_audits(db, p)
    root = f._feedback_row(db, p, root_id, lock)
    _need(root.root_id == root.id and root.revision == 1)
    query = select(FeedbackRevision).where(FeedbackRevision.workspace_id == p.workspace_id,
        FeedbackRevision.root_id == root.id).order_by(FeedbackRevision.revision)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    rows = list(db.scalars(query))
    _need(bool(rows) and rows[0].id == root.id)
    ids = {row.id for row in rows}
    commands = list(db.scalars(select(ThresholdCommand).where(ThresholdCommand.workspace_id == p.workspace_id,
        or_(ThresholdCommand.feedback_id.in_(ids),
            ThresholdCommand.data['result_snapshot']['root_id'].as_string() == root.id))))
    command_ids = {command.id for command in commands}
    audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == f.COMMAND_AUDIT, or_(Audit.data['root_id'].as_string() == root.id,
            and_(Audit.data['observation_id'].as_string() == root.observation_id,
                 Audit.data['baseline_capability_id'].as_string() == root.baseline_capability_id,
                 Audit.data['rule_id'].as_string() == root.rule_id),
            Audit.data['feedback_id'].as_string().in_(ids), Audit.object_id.in_(command_ids)))))
    pins = [f._checked_audit(p, item) for item in audits]
    _need(len(pins) == len(commands) and {item['command_id'] for item in pins} == command_ids
          and {item['feedback_id'] for item in pins} == ids)
    for item in pins:
        _need(item['root_id'] == root.id and item['observation_id'] == root.observation_id
              and item['baseline_capability_id'] == root.baseline_capability_id and item['rule_id'] == root.rule_id)
    snapshots = {}
    for command in commands:
        saved = f._checked_command(db, p, command)
        key = (saved['id'], saved['version'])
        _need(key not in snapshots)
        snapshots[key] = (saved, command.operation)
    previous, first_basis = None, None
    for index, row in enumerate(rows, 1):
        _need(row.revision == index and row.previous_id == (previous.id if previous else None)
              and row.root_id == root.id and row.observation_id == root.observation_id
              and row.baseline_capability_id == root.baseline_capability_id and row.rule_id == root.rule_id
              and row.status in {'draft', 'submitted', 'cancelled'} and type(row.version) is int and row.version >= 1)
        value = row.data
        _need(type(value) is dict and set(value) == {'schema', 'basis', 'label', 'reason', 'cancel_reason', 'record_hash'}
              and value['schema'] == 'threshold-evolution-feedback-record-v147'
              and value['record_hash'] == f._record_hash(row)
              and value['label'] in (None, 'should_alert', 'should_not_alert', 'uncertain')
              and type(value['reason']) is str and len(value['reason']) <= 2000
              and (value['cancel_reason'] is None if row.status != 'cancelled'
                   else type(value.get('cancel_reason')) is str and bool(value['cancel_reason'].strip())))
        saved_basis = checked_saved_basis(value['basis'], row, p)
        comparable = f._hash({key: item for key, item in saved_basis.items() if key != 'observation_version'})
        if first_basis is None:
            first_basis = comparable
        _need(comparable == first_basis)
        _need(all((row.id, version) in snapshots for version in range(1, row.version + 1))
              and sum(key[0] == row.id for key in snapshots) == row.version
              and f._hash(snapshots[(row.id, row.version)][0]) == f._hash(f._snapshot(row)))
        first, operation = snapshots[(row.id, 1)]
        _need(operation == ('feedback_create' if index == 1 else 'feedback_revisions'))
        for version in range(2, row.version + 1):
            old, _ = snapshots[(row.id, version - 1)]
            new, operation = snapshots[(row.id, version)]
            _need(old['status'] == 'draft' and operation in {'feedback_save', 'feedback_submit', 'feedback_cancel'}
                  and f._hash({key: item for key, item in old.items() if key not in {'status', 'version', 'data'}})
                  == f._hash({key: item for key, item in new.items() if key not in {'status', 'version', 'data'}})
                  and f._hash(old['data']['basis']) == f._hash(new['data']['basis']))
            if operation in {'feedback_submit', 'feedback_cancel'}:
                _need(old['data']['label'] == new['data']['label'] and old['data']['reason'] == new['data']['reason'])
        if previous:
            _need(previous.status != 'draft')
        if row.status == 'submitted':
            _need(value['label'] is not None and bool(value['reason'].strip()))
        previous = row
    return rows


def _matches_current(rows, actual):
    f = _legacy()
    return all(row.data['basis']['basis_hash'] == actual['basis_hash']
        and f._hash({key: value for key, value in row.data['basis'].items() if key != 'observation_version'})
        == f._hash({key: value for key, value in actual.items() if key != 'observation_version'}) for row in rows)


def require_current_basis(db, p, rows):
    """Keep the V147 actionable chain's complete, independent machine check."""
    root = rows[0]
    actual = _legacy()._basis(db, p, root.observation_id, root.rule_id)
    _need(_matches_current(rows, actual))
    return actual


def _basis_available(db, p, rows):
    from .threshold_evolution_basis_v147 import ERROR as MACHINE_ERROR
    root = rows[0]
    # This subject check is authorization, not a substitute for machine proof.
    scoped(db, Observation, root.observation_id, p)
    try:
        actual = _legacy()._basis(db, p, root.observation_id, root.rule_id)
    except HTTPException as error:
        if error.detail != MACHINE_ERROR:
            raise
        if error.status_code in {409, 422}:
            return False
        if error.status_code == 404:
            # A missing/foreign subject remains 404; an absent dependency after
            # the same subject is still readable is only a machine issue.
            scoped(db, Observation, root.observation_id, p)
            return False
        raise
    return _matches_current(rows, actual)


def _entry(row):
    return {key: copy.deepcopy(getattr(row, key)) for key in _ENTRY} | {
        key: copy.deepcopy(row.data[key]) for key in ('label', 'reason', 'cancel_reason')}


def _history(db, p, rows, identity):
    row = next((item for item in rows if item.id == identity), None)
    _need(row is not None)
    available = _basis_available(db, p, rows)
    submitted = [item for item in rows if item.status == 'submitted']
    return {'schema': 'threshold-evolution-feedback-history-v148',
        'entry': _entry(row), 'history': [_entry(item) for item in rows],
        'latest_submitted': _entry(submitted[-1]) if submitted else None,
        'basis_reference': {key: copy.deepcopy(row.data['basis'][key]) for key in _REFERENCE},
        'basis_available': available, 'issue': None if available else UNAVAILABLE,
        'eligible_for_candidate': False, 'can_edit': False, 'can_cancel': False,
        'can_submit': False, 'can_revise': False}


@router.get('/feedback/{identity}/history')
def feedback_history(identity: str, p: P, db: DB):
    f = _legacy()
    f._legacy_audits(db, p)
    row = f._feedback_row(db, p, identity)
    rows = inventory_chain(db, p, row.root_id)
    return _history(db, p, rows, identity)


@router.get('/observations/{identity}/feedback-history')
def observation_feedback_history(identity: str, p: P, db: DB, rule_id: str | None = None):
    f = _legacy()
    scoped(db, Observation, identity, p)
    try:
        if rule_id is not None and not (type(rule_id) is str and 1 <= len(rule_id) <= 80 and rule_id == rule_id.strip()):
            raise ValueError()
        canonical_json(rule_id)
    except (ValueError, TypeError):
        raise HTTPException(422, '请保留原规则编号') from None
    f._legacy_audits(db, p)
    record_query = select(FeedbackRevision).where(FeedbackRevision.workspace_id == p.workspace_id,
        FeedbackRevision.observation_id == identity)
    audit_query = select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == f.COMMAND_AUDIT, Audit.data['observation_id'].as_string() == identity)
    command_query = select(ThresholdCommand).where(ThresholdCommand.workspace_id == p.workspace_id,
        ThresholdCommand.data['result_snapshot']['observation_id'].as_string() == identity)
    if rule_id is not None:
        record_query = record_query.where(FeedbackRevision.rule_id == rule_id)
        audit_query = audit_query.where(Audit.data['rule_id'].as_string() == rule_id)
        command_query = command_query.where(ThresholdCommand.data['result_snapshot']['rule_id'].as_string() == rule_id)
    records = list(db.scalars(record_query))
    audits = list(db.scalars(audit_query))
    commands = list(db.scalars(command_query))
    roots = {row.root_id for row in records}
    audit_roots = {f._checked_audit(p, row)['root_id'] for row in audits}
    command_roots = {f._checked_command(db, p, row)['root_id'] for row in commands}
    _need(len(roots) <= 1 and roots == audit_roots == command_roots)
    if not roots:
        return {'schema': 'threshold-feedback-history-lookup-v148', 'found': False, 'history': None}
    rows = inventory_chain(db, p, next(iter(roots)))
    return {'schema': 'threshold-feedback-history-lookup-v148', 'found': True,
            'history': _history(db, p, rows, rows[-1].id)}
