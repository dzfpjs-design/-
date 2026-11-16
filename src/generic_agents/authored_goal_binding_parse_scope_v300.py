"""Bounded reuse of pure binding reconstruction within one synchronous call.

No DB row, permission, lease, clock or historical proof is cached. The complete
typed canonical input and actual implementation identities select a success.
An outer exit expires copied contexts; inherited threads/tasks own new scopes.
"""
import asyncio
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import current_thread

from .typed_payload_codec_v131 import canonical_json

MAX_ENTRIES = 2
MAX_CANONICAL_BYTES = 8 * 1024 * 1024


def _owner():
    try: task = asyncio.current_task()
    except RuntimeError: task = None
    return current_thread(), task


@dataclass
class _Lifetime:
    owner: tuple
    closed: bool = False
    retained_entries: int = 0
    canonical_bytes: int = 0


@dataclass(frozen=True)
class _Entry:
    canonical: str
    parse: object
    implementation: tuple
    result: object


@dataclass(frozen=True)
class _State:
    lifetime: _Lifetime
    entries: tuple = ()


_CURRENT = ContextVar('authored_goal_binding_parse_scope_v300', default=None)


def _active():
    state = _CURRENT.get()
    if (type(state) is not _State or state.lifetime.closed or state.lifetime.owner != _owner()):
        return None
    return state


@contextmanager
def authored_goal_binding_parse_scope():
    """Nested synchronous reads share one operation; exit closes all copies."""
    if _active() is not None:
        yield
        return
    lifetime = _Lifetime(_owner())
    token = _CURRENT.set(_State(lifetime))
    try:
        yield
    finally:
        lifetime.closed = True
        _CURRENT.reset(token)


def _canonical(value):
    if type(value) is not dict: return None
    try:
        text = canonical_json(value)
        return text, len(text.encode('utf-8', errors='strict'))
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        return None


def _same(entry, text, parse, implementation):
    return (entry.canonical == text and entry.parse is parse
        and len(entry.implementation) == len(implementation)
        and all(left is right for left, right in zip(entry.implementation, implementation)))


def read_binding_cached(value, *, parse, implementation=()):
    """Reuse a detached success, otherwise preserve the ordinary parser path.

    Capacity includes both retained input and output canonical UTF-8 bytes.
    The shared lifetime quota also bounds same-owner copied-context branches;
    their immutable entry inventories cannot overwrite the parent's inventory.
    Errors, unsupported inputs/outputs and over-capacity reads are never stored.
    """
    state = _active()
    if (state is None or type(implementation) is not tuple or len(implementation) > 8
            or not all(callable(item) for item in implementation)):
        return parse(value)
    encoded = _canonical(value)
    if encoded is None: return parse(value)
    text, size = encoded
    lifetime = state.lifetime
    if size > MAX_CANONICAL_BYTES or lifetime.canonical_bytes > MAX_CANONICAL_BYTES:
        return parse(value)
    for entry in state.entries:
        if _same(entry, text, parse, implementation):
            try: return copy.deepcopy(entry.result)
            except (TypeError, ValueError, OverflowError, RecursionError): return parse(value)
    if lifetime.retained_entries >= MAX_ENTRIES or lifetime.canonical_bytes + size > MAX_CANONICAL_BYTES:
        return parse(value)
    result = parse(value)  # Never catch/retain an original parser failure.
    output = _canonical(result)
    # Do not bind a non-pure implementation's changed input to its old key.
    if output is None or _canonical(value) != encoded: return result
    total = size + output[1]
    latest = _active()
    if (latest is None or latest.lifetime is not lifetime
            or lifetime.retained_entries >= MAX_ENTRIES
            or lifetime.canonical_bytes + total > MAX_CANONICAL_BYTES
            or any(_same(entry, text, parse, implementation) for entry in latest.entries)):
        return result
    try:
        retained = copy.deepcopy(result)
        detached = copy.deepcopy(retained)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return result
    entry = _Entry(text, parse, implementation, retained)
    lifetime.retained_entries += 1
    lifetime.canonical_bytes += total
    _CURRENT.set(_State(lifetime, (*latest.entries, entry)))
    return detached
