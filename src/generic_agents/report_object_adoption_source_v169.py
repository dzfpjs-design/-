"""Confirmed human sources for the independent V169 adoption family.

Fresh adoption retains the V166 actor/report locks before current authority
and source checks. Historical loading is an internal shared-ledger check: its
caller must already authenticate that ledger and its origin. It is NOT an API
for looking up another person's private revisions or for authorizing adoption.
Neither path returns the private note, original command body or wire.
"""
import copy
from functools import wraps
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select

from . import models as m
from .contracts import digest
from . import report_object_revision_store_v166 as revisions

ORIGIN_SCHEMA = 'report-object-confirmed-origin-v169'
ERROR = '已确认结构稿、原确认命令或独立审计不能核对；原稿和配置保留'


def _need(condition):
    if not condition:
        raise HTTPException(409, ERROR)


def _guard(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError,
                RecursionError, OverflowError):
            raise HTTPException(409, ERROR) from None
    return checked


def check_origin(origin):
    """Pure closed-shape check; independent inventory is still required."""
    from .report_object_configuration_v169 import check_origin as checked
    return checked(origin)


def _audit(db, p, command, *, lock=True):
    # Read the actual independent row, not an origin supplied by the caller or
    # a copy of the Command's self-reported audit pin. The report lock already
    # excludes an application writer; SHARE also holds this audit through commit.
    query = select(m.Audit).where(
        m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == revisions.ACTION,
        m.Audit.object_id == command.id).limit(2)
    if lock:
        query = query.with_for_update(read=True).execution_options(populate_existing=True)
    rows = list(db.scalars(query))
    _need(len(rows) == 1)
    row = rows[0]
    _need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id
        and row.action == revisions.ACTION and row.object_id == command.id
        and revisions.same(row.data, revisions._audit_pin(command)))
    # Recheck the actual original UTF8 wire/Command against this independent row.
    revisions._checked_command(command, row)
    return row


def _audit_value(row):
    return {key: copy.deepcopy(getattr(row, key)) for key in
        ('id', 'workspace_id', 'actor_id', 'action', 'object_id', 'data')}


def _confirmed(db, p, command, *, lock_audit=True):
    _need(command.operation == 'confirm')
    snapshot = command.data['record_snapshot']
    _need(snapshot['status'] == 'confirmed'
        and snapshot['version'] == command.record_version)
    # The complete inventory already checked the historical source/hash/shape
    # chain. This confirmation check uses the human snapshot, never a machine
    # initial value and never a later revision's note or acknowledgements.
    value = revisions._value(snapshot['data']['value'], confirmed=True)
    basis = revisions._basis(snapshot['data']['basis'], command.report_id)
    actual_audit = _audit(db, p, command, lock=lock_audit)
    origin = check_origin({'schema': ORIGIN_SCHEMA,
        'report_id': command.report_id, 'actor_id': command.actor_id,
        'workspace_id': command.workspace_id, 'revision_id': command.revision_id,
        'revision_version': command.record_version,
        'revision_record_hash': digest(snapshot), 'basis_hash': snapshot['basis_hash'],
        'confirmed_command_id': command.id, 'confirmed_command_hash': command.command_hash,
        'confirmed_wire_sha256': command.wire_sha256,
        'confirmed_value_hash': digest(value), 'confirmed_audit_id': actual_audit.id,
        'confirmed_audit_hash': digest(_audit_value(actual_audit))})
    return {'origin': origin, 'result': copy.deepcopy(value['result']),
        'object_identity_confirmations': copy.deepcopy(value['object_identity_confirmations']),
        'basis': copy.deepcopy(basis)}


@_guard
def check_saved_origin(db, p, origin):
    """Check an already authenticated shared ledger's historical confirmation.

    The outer shared-ledger reader owns access control. Only that ledger's
    exact origin is accepted here; no current actor/source/last-confirmed gate
    is renewed, and no private human fields are returned.
    """
    expected = check_origin(origin)
    _need(expected['workspace_id'] == p.workspace_id)
    owner = SimpleNamespace(user_id=expected['actor_id'], workspace_id=p.workspace_id)
    row, commands = revisions._inventory(db, owner, expected['report_id'])
    _need(row is not None)
    matches = [item for item in commands if item.id == expected['confirmed_command_id']]
    _need(len(matches) == 1)
    # _inventory holds the report advisory read lock. A historical read-only
    # projection must not request PostgreSQL row locks; immutable Audit bytes
    # and their independent command binding are still checked in full.
    result = _confirmed(db, owner, matches[0], lock_audit=False)
    _need(revisions.same(result['origin'], expected))
    return result


@_guard
def load_confirmed(db, p, report_id, *, lock=False):
    """Load the latest confirmed source for a NEW explicitly consented adoption.

    The caller acquires its adoption nonce before this function and its
    configuration CAS afterwards. Actor Shared precedes every sorted report
    Shared lock; this function never upgrades a report lock or writes a row.
    """
    _need(type(lock) is bool)
    revisions._owner(db, p, report_id)
    row, commands = revisions._actor_inventory(db, p, report_id, shared=True)
    _need(row is not None and row.status == 'confirmed' and commands
        and commands[-1].operation == 'confirm'
        and commands[-1].record_version == row.version)
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    current = revisions._source(db, p, report_id, lock=lock)
    _need(revisions.same(row.data['basis'], current['basis'])
        and row.basis_hash == current['basis_hash'])
    from .report_object_semantics_v166 import validate_value, confirm_value
    value = copy.deepcopy(row.data['value'])
    validated = validate_value(value, current['catalog'], current['allowed_span_refs'], current['context'])
    _need(revisions.same(validated, value) and revisions.same(confirm_value(validated), value))
    result = _confirmed(db, p, commands[-1])
    # _source has already cold-checked the original report. Obtain its actual
    # row for the existing material freezer instead of rebuilding material refs.
    query = select(m.SetupReportDraft).where(m.SetupReportDraft.id == report_id,
        m.SetupReportDraft.workspace_id == p.workspace_id, m.SetupReportDraft.actor_id == p.user_id)
    if lock:
        query = query.with_for_update(read=True).execution_options(populate_existing=True)
    report = db.scalar(query)
    _need(report is not None and report.id == report_id
        and report.workspace_id == p.workspace_id and report.actor_id == p.user_id)
    from .report_full_semantic_handoff import material_refs
    result.update(context=copy.deepcopy(current['context']),
        materials=copy.deepcopy(material_refs(db, p, report, lock=lock)))
    return result
