"""Bounded full-source DTO reuse inside one attested Task detail snapshot GET.

No source, IAM, adoption, role or execution decision survives this synchronous
operation. A real READ ONLY/REPEATABLE READ dependency issues a private permit;
ordinary Sessions, writers and workers do not have one. Files are NOT covered
by PostgreSQL MVCC: every reused entry is re-read through the normal evidence
channel before the endpoint returns. The scope never stores evidence bytes.
"""
import asyncio
import copy
import hashlib
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import current_thread

from fastapi import HTTPException
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from .typed_payload_codec_v131 import canonical_json


POLICY = 'task-full-source-read-snapshot-v1'
MAX_ENTRIES = 2
MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 12000
_PERMIT_KEY = '_task_full_source_read_permit_v1'
_HASH = re.compile(r'^[0-9a-f]{64}$')
_CURRENT = ContextVar('task_full_source_read_snapshot', default=None)


def _fail():
    raise HTTPException(409, {'code': 'full_source_read_snapshot_integrity',
        'message': '本次只读来源快照或证据文件已变化，未返回陈旧依据；原记录与人工稿保留'})


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return current_thread(), task


def _modes(connection):
    # Actual server transaction settings, never an app-config or caller flag.
    value = connection.exec_driver_sql("SELECT current_setting('transaction_read_only'), "
        "current_setting('transaction_isolation')").one()
    if tuple(value) != ('on', 'repeatable read'):
        _fail()


@dataclass
class _Permit:
    db: object
    connection: object
    transaction: object
    session_transaction: object
    actor_id: str
    workspace_id: str
    closed: bool = False


@contextmanager
def _attested_task_read_session(db, principal, connection, transaction):
    """Only the dedicated server GET dependency issues this lifetime permit."""
    if (not isinstance(db, Session) or not isinstance(connection, Connection)
            or db.get_bind() is not connection or connection.closed
            or connection.get_transaction() is not transaction or not transaction.is_active
            or db.new or db.dirty or db.deleted or db.in_nested_transaction()
            or db.info.get(_PERMIT_KEY) is not None):
        _fail()
    _modes(connection)
    # Bind the virtual Session transaction before yielding the GET. It joins
    # the existing external READ ONLY transaction; it does not commit it.
    session_transaction = db.get_transaction()
    if session_transaction is None:
        session_transaction = db.begin()
    if not session_transaction.is_active:
        _fail()
    permit = _Permit(db, connection, transaction, session_transaction,
        principal.user_id, principal.workspace_id)
    db.info[_PERMIT_KEY] = permit
    try:
        yield
    finally:
        permit.closed = True
        if db.info.get(_PERMIT_KEY) is permit:
            db.info.pop(_PERMIT_KEY)


def _permit(db):
    info = getattr(db, 'info', None)
    return info.get(_PERMIT_KEY) if type(info) is dict else None


@dataclass
class _Entry:
    result: object
    files: tuple
    size: int


def _files(snapshot):
    expected = {}
    pairs = [(snapshot['original']['evidence_id'], snapshot['original']['evidence_sha256']),
        (snapshot['copy']['evidence_id'], snapshot['copy']['ref']['hash'])]
    pairs.extend((page['image']['evidence_id'], page['image']['sha256']) for page in snapshot['pages'])
    for identity, sha in pairs:
        if (type(identity) is not str or not 0 < len(identity) <= 128 or type(sha) is not str
                or _HASH.fullmatch(sha) is None or identity in expected and expected[identity] != sha):
            _fail()
        expected[identity] = sha
    return tuple(sorted(expected.items()))


@dataclass
class _Scope:
    permit: _Permit
    task_id: str
    owner: tuple
    closed: bool = False
    entries: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    size: int = 0

    def check(self, db, p):
        permit = self.permit
        if (self.closed or permit.closed or self.owner != _owner() or permit.db is not db
                or _permit(db) is not permit or permit.actor_id != p.user_id
                or permit.workspace_id != p.workspace_id or db.get_bind() is not permit.connection
                or permit.connection.closed or permit.connection.get_transaction() is not permit.transaction
                or not permit.transaction.is_active
                or db.get_transaction() is not permit.session_transaction
                or not permit.session_transaction.is_active or db.in_nested_transaction()
                or db.new or db.dirty or db.deleted):
            _fail()
        _modes(permit.connection)

    def load(self, db, p, selection, policy, compute):
        self.check(db, p)
        try:
            key = (POLICY, policy, canonical_json(selection))
            hash(key)
        except (TypeError, ValueError, RecursionError, OverflowError, UnicodeError):
            # Cache eligibility must never narrow the existing source contract.
            return self.fresh(db, p, compute)
        entry = self.entries.get(key)
        if entry is not None:
            return copy.deepcopy(entry.result)
        result = self.fresh(db, p, compute)
        if len(self.entries) >= MAX_ENTRIES:
            return result
        encoded = canonical_json(result).encode('utf-8', errors='strict')
        size = len(encoded) + len(key[2].encode('utf-8', errors='strict'))
        if size > MAX_BYTES - self.size:
            return result
        files = _files(result)
        additions = {identity: sha for identity, sha in files if identity not in self.files}
        if (len(self.files) + len(additions) > MAX_FILES
                or any(identity in self.files and self.files[identity] != sha for identity, sha in files)):
            # A resource budget is never a new source/business refusal. This
            # result was fully checked just now, but cannot be reused here.
            return result
        entry = _Entry(copy.deepcopy(result), files, size)
        self.files.update(additions)
        self.entries[key] = entry
        self.size += size
        return copy.deepcopy(entry.result)

    def fresh(self, db, p, compute):
        result = compute()
        self.check(db, p)
        return result

    def finish(self, db, p):
        self.check(db, p)
        from .evidence import evidence_bytes
        for identity, sha in sorted(self.files.items()):
            self.check(db, p)
            row, content = evidence_bytes(db, p, identity)
            if row.sha256 != sha or hashlib.sha256(content).hexdigest() != sha:
                _fail()
        self.check(db, p)


@contextmanager
def task_full_source_read_scope(db, p, task_id):
    """Exit successfully BEFORE the synchronous endpoint returns its response.

    No streaming endpoint may use this scope. A FastAPI yield-dependency cleanup
    is too late for final file verification and deliberately does not do it.
    Unsupported/ordinary Sessions retain the full original validation path.
    """
    permit = _permit(db)
    if permit is None:
        yield
        return
    if type(permit) is not _Permit:
        _fail()
    previous = _CURRENT.get()
    if previous is not None:
        previous.check(db, p)
        if previous.permit is not permit or previous.task_id != task_id:
            _fail()
        yield
        return
    scope = _Scope(permit, task_id, _owner())
    scope.check(db, p)
    token = _CURRENT.set(scope)
    try:
        yield
        scope.finish(db, p)
    finally:
        scope.closed = True
        scope.entries.clear()
        scope.files.clear()
        scope.size = 0
        _CURRENT.reset(token)


def read_full_source(db, p, selection, *, lock, compute, policy=None, policy_factory=None):
    """Called only after the public freeze's original selection validation."""
    if lock is not False:
        return compute()
    scope = _CURRENT.get()
    if scope is None:
        return compute()
    if policy_factory is not None:
        policy = policy_factory()
    return scope.load(db, p, selection, policy, compute)
