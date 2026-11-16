"""Explicit one-display Selection inventory; never execution or current authority.

READ COMMITTED remains unchanged. These are checked model references in one
display assembly, not a SQL cache, transaction snapshot or fresh-source cache.
"""
from datetime import date, datetime, time

from sqlalchemy import event
from sqlalchemy.orm import Session

from .typed_payload_codec_v131 import canonical_json


def _need(value):
    if not value:
        from .threshold_policy_store_v153 import PolicyInventoryError
        raise PolicyInventoryError()


def _model_pin(row):
    """Pin every public mapped value, preserving SQL-time versus JSON types."""
    try:
        values = {}
        for column in row.__table__.columns:
            if column.info.get('typed_payload_internal'):
                continue
            value = getattr(row, column.key)
            values[column.name] = ({'sql_time_type': type(value).__name__, 'value': value.isoformat()}
                if isinstance(value, (datetime, date, time)) else {'json': value})
        return type(row), canonical_json(values)
    except (ValueError, TypeError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _need(False)


class PolicyReadInventory:
    def __init__(self, db, p):
        self.db = db
        self.identity = self._identity(p)
        self.transaction = db.get_transaction()
        self.closed = False
        self.loading = False
        self.failed = False
        self._rows = None
        self._chains = None
        self._bindings = {}
        self._objects = {}
        self._staged = {}
        self._staged_bindings = {}
        self._staged_refs = {}
        self._watching = False
        self._transaction_listener = self._started
        # A fresh real Session may autobegin on the initial Grant SELECT. Watch
        # its first actual root transaction, including a commit before any SQL.
        if self.transaction is None:
            _need(isinstance(db, Session))
            event.listen(db, 'after_transaction_create', self._transaction_listener)
            self._watching = True

    @staticmethod
    def _identity(p):
        return p.user_id, p.workspace_id, p.role, p.session_id

    def _started(self, session, transaction):
        if self.transaction is None and transaction.parent is None:
            self.transaction = transaction

    def _detach(self):
        if self._watching:
            event.remove(self.db, 'after_transaction_create', self._transaction_listener)
            self._watching = False

    def __enter__(self):
        return self

    def __exit__(self, *ignored):
        self.close()

    def close(self):
        self._detach()
        self.closed = True

    def check_scope(self, db, p):
        """Check ownership only; this never authorizes reuse of a model."""
        _need(not self.closed and not self.failed and db is self.db
            and self._identity(p) == self.identity and self.transaction is not None
            and self.transaction.is_active and db.get_transaction() is self.transaction)
        self._detach()
        return self

    def check(self, db, p, row=None):
        self.check_scope(db, p)
        if row is None:
            entries = self._objects.values()
        else:
            saved = self._bindings.get(row.id)
            _need(saved is not None and saved[0] is row)
            entries = (self._objects[key] for key in saved[3])
        for actual, pin in entries:
            _need(actual.workspace_id == p.workspace_id and _model_pin(actual) == pin)
        return self

    def _remember(self, target, rows, p):
        for row in rows:
            if row is None:
                continue
            _need(row.workspace_id == p.workspace_id)
            key, pin = id(row), _model_pin(row)
            for previous in (self._objects.get(key), self._staged.get(key)):
                if previous is not None:
                    _need(previous[0] is row and previous[1] == pin)
            target[key] = (row, pin)

    def pin_rows(self, db, p, rows):
        self.check(db, p)
        self._remember(self._objects, rows, p)

    def collecting(self, db, p):
        # These facts remain private until the entire original cold inventory
        # succeeds and every old and staged model pin is checked below.
        self.check_scope(db, p)
        _need(self.loading and self._rows is None)
        return self

    def stage_rows(self, db, p, rows):
        self.collecting(db, p)
        self._remember(self._staged, rows, p)

    def stage_lineage(self, db, p, row, rows):
        rows = tuple(rows)
        self.stage_rows(db, p, rows)
        self._staged_refs.setdefault(row.id, set()).update(id(item) for item in rows if item is not None)

    def stage_binding(self, db, p, row, grant, binding, states):
        self.collecting(db, p)
        _need(row.id not in self._staged_bindings and row.policy_id == grant.id)
        related = (row, grant, *binding, *states)
        self.stage_lineage(db, p, row, related)
        self._staged_bindings[row.id] = (row, grant, tuple(binding))

    def selection_data(self, db, p):
        if self._rows is not None:
            self.check(db, p)
        else:
            self.check_scope(db, p)
            _need(not self.loading)
            self.loading = True
            try:
                from . import threshold_policy_execution_v153 as execution
                rows, chains = execution._selection_records(db, p, _read_collector=self)
                _need(set(self._staged_bindings) == {row.id for row in rows})
                saved_rows = tuple(rows)
                saved_chains = {key: tuple(items) for key, items in chains.items()}
                saved_bindings = {key: (*saved, frozenset(self._staged_refs[key]))
                    for key, saved in self._staged_bindings.items()}
                # Nothing staged is available to a projection until every
                # invariant succeeds, including prior pins changed during IO.
                self.check(db, p)
                for actual, pin in self._staged.values():
                    _need(actual.workspace_id == p.workspace_id and _model_pin(actual) == pin)
                self._objects.update(self._staged)
                self._rows = saved_rows
                self._chains = saved_chains
                self._bindings = saved_bindings
            except Exception:
                self.failed = True
                raise
            finally:
                self.loading = False
                self._staged.clear()
                self._staged_bindings.clear()
                self._staged_refs.clear()
        return self._rows, dict(self._chains)

    def rows_for(self, db, p, grant):
        rows, _ = self.selection_data(db, p)
        selected = [row for row in rows if row.policy_id == grant.id]
        for row in selected:
            _need(self._bindings[row.id][1] is grant)
        return selected

    def binding(self, db, p, grant, row):
        self.check(db, p, row)
        saved = self._bindings[row.id]
        _need(saved[1] is grant and row.policy_id == grant.id)
        return saved[2]


def checked_read_inventory(value, db, p, *, row=None):
    _need(type(value) is PolicyReadInventory)
    return value.check(db, p, row)


def scoped_read_inventory(value, db, p):
    """Private call-site guard before staging or an immediate full check."""
    _need(type(value) is PolicyReadInventory)
    return value.check_scope(db, p)
