"""Test-only scoped cache of byte-identical successful pure decodes.

Not used by the application. It never caches queries, authorization, locks or
ORM rows. A synchronous handler must explicitly open and close its own scope.
"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
import sys
import threading

from generic_agents import typed_payload_codec_v131 as codec

_CURRENT = ContextVar('synthetic_decode_scope', default=None)


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class SyntheticBudget:
    def __init__(self, limit=32 * 1024 * 1024, per_scope=4 * 1024 * 1024):
        if type(limit) is not int or type(per_scope) is not int or min(limit, per_scope) < 0:
            raise ValueError('invalid synthetic cache budget')
        self.limit, self.per_scope = limit, per_scope
        self.used = 0
        self.lock = threading.Lock()

    @contextmanager
    def bind(self, actor, workspace):
        if not isinstance(actor, str) or not isinstance(workspace, str):
            raise ValueError('synthetic scope identity required')
        scope = _Scope(self, actor, workspace)
        token = _CURRENT.set(scope)
        try:
            yield scope
        finally:
            scope.close()
            _CURRENT.reset(token)


class _Scope:
    def __init__(self, budget, actor, workspace):
        self.budget, self.actor, self.workspace = budget, actor, workspace
        self.thread, self.task = threading.get_ident(), _task()
        self.closed, self.used = False, 0
        self.entries = {}
        self.hits = self.misses = self.bypassed = 0

    def active(self):
        return not self.closed and self.thread == threading.get_ident() and self.task is _task()

    def close(self):
        with self.budget.lock:
            if not self.closed:
                self.closed = True
                self.budget.used -= self.used
                self.used = 0
                self.entries.clear()


def _size(key, result=''):
    # Conservative per-entry allowance for dict growth and allocator overhead.
    text, marker, owner, projection = key
    return (1024 + sys.getsizeof(key) + sys.getsizeof(owner) + sys.getsizeof(result)
            + sum(sys.getsizeof(item) for item in (text, marker, projection, *owner)))


def install(monkeypatch, budget):
    """Install only in a disposable test process; preserve original failures."""
    from generic_agents import typed_payload_orm_v131 as orm
    original = codec.decode_payload
    # Avoid the existing process-global LRU so tests can prove scope isolation.
    monkeypatch.setattr(codec, '_verified_decode_json', codec._verified_decode_json.__wrapped__)

    def decode(text, marker, owner, projection_text):
        scope = _CURRENT.get()
        if scope is None or not scope.active():
            return original(text, marker, owner, projection_text)
        try:
            checked_owner = codec.validate_owner(owner)
            checked_marker = codec.validate_marker(marker, allow_legacy=True)
            if (checked_marker == codec.LEGACY_MARKER or type(text) is not str
                    or type(projection_text) is not str
                    or checked_owner['workspace_id'] != scope.workspace):
                scope.bypassed += 1
                return original(text, marker, owner, projection_text)
            owner_values = tuple(checked_owner[key] for key in ('table', 'field', 'owner_id', 'workspace_id'))
            key = (text, checked_marker, owner_values, projection_text)
        except (TypeError, ValueError, UnicodeError, codec.TypedPayloadError):
            return original(text, marker, owner, projection_text)
        with budget.lock:
            saved = scope.entries.get(key) if scope.active() else None
        if saved is not None:
            scope.hits += 1
            return json.loads(saved)
        scope.misses += 1
        value = original(text, marker, owner, projection_text)
        if _size(key) > budget.per_scope:
            scope.bypassed += 1
            return value
        try:
            saved = codec._dump_validated(value)
            size = _size(key, saved)
            with budget.lock:
                if (scope.active() and key not in scope.entries
                        and scope.used + size <= budget.per_scope and budget.used + size <= budget.limit):
                    scope.entries[key] = saved
                    scope.used += size
                    budget.used += size
                else:
                    scope.bypassed += 1
        except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
            scope.bypassed += 1
        return value

    monkeypatch.setattr(codec, 'decode_payload', decode)
    monkeypatch.setattr(orm, 'decode_payload', decode)
    return decode
