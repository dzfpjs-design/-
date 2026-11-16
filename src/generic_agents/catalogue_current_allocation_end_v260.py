"""Explicit V260 end adapter; reuse the original lease/finance/capture checks."""
import sys

from . import catalogue_current_allocation_v260 as current
from . import report_business_catalogue_allocation_end_v213 as original

PROFILE = current.PROFILE
OPERATION = 'end-current-interrupted'
DATA_KEY = 'current_interrupted_end'
END_SCHEMA = 'report-business-catalogue-current-allocation-ended-v260'
END_AUDIT = 'report.catalogue_current_allocation_end_v260'
CONTEXT = 'report-business-catalogue-current-allocation-end-context-v260'
EndInterruptedBody = original.EndInterruptedBody


def _self():
    return sys.modules[__name__]


def marker_pins(row):
    current.need(current._consumer()._family(row.data['preview']['preparation']).id == PROFILE)
    prep = row.data['preview']['preparation']; current.native_preparation(prep)
    return {'binding_id': prep['admission_basis']['binding_id'],
        'binding_record_hash': prep['admission_basis']['binding_record_hash'],
        'current_scope_hash': prep['current_scope']['current_scope_hash']}


def _family(row, p):
    current.check_preview(row.data['preview'], p)
    return original._family(row, p, _adapter=_self())


def _job(db, p, row, *, lock=False):
    return original._job(db, p, row, lock=lock, _adapter=_self())


def _checked_marker(marker, row, body):
    return original._checked_marker(marker, row, body, _adapter=_self())


def _audit_data(p, row, command, marker):
    return original._audit_data(p, row, command, marker, _adapter=_self())


def check_parent_command(db, p, row, command, *, ref):
    return original.check_parent_command(db, p, row, command, ref=ref, _adapter=_self())


def check_inventory(db, p, by_consumer, by_command, events):
    return original.check_inventory(db, p, by_consumer, by_command, events, _adapter=_self())


def _preflight(db, p, consumer_id):
    # Catalogue domain always precedes Consumer. No fresh config/source/grant is
    # required to retain and end the original financial waiting state.
    current.source.lock_before_consumer(db, p)
    c = current._consumer(); records, _, _ = c._inventory(db, p)
    row = c._owned(records, p, consumer_id)
    _family(row, p); current.verify_history(db, p, row, physical=False)
    return row


def context(db, p, consumer_id):
    row = _preflight(db, p, consumer_id)
    value = original.context(db, p, consumer_id, _adapter=_self())
    return {'call_status': None, 'reserved_tokens': None, 'capture_state': None,
        **value, **marker_pins(row)}


def end(db, p, consumer_id, body, exact, sha):
    # Writers never enter the shared Consumer inventory used by GET. Two
    # writers must queue at the nonce/actor exclusive mutex, not both retain a
    # shared actor lock and then attempt to upgrade it.
    current.source.lock_before_consumer(db, p)
    c = current._consumer()
    c._nonce(db, p, body.request_id); c._any_nonce(db, p, body.request_id)
    records, commands, _ = c._inventory(db, p, exclusive=True)
    row = c._owned(records, p, consumer_id)
    path = c.PREFIX + '/' + consumer_id + '/' + OPERATION
    prior = c._replay(commands, p, body.request_id, OPERATION, path, exact, sha)
    if prior is not None:
        current.need(prior['id'] == consumer_id)
        return prior
    # Only a first operation needs the remaining frozen source/Binding witness.
    # Current admin authority is checked by original.end after its replay gate.
    _family(row, p); current.verify_history(db, p, row, physical=False)
    return original.end(db, p, consumer_id, body, exact, sha, _adapter=_self())
