"""Bounded pure evidence reuse during exactly one Consumer inventory check.

This is not a source, account, grant, lease, paid-call or database cache. Every
miss calls the original complete validator. Hits compare all actual JSON bytes
and return detached results; no caller-supplied hash or object ID is trusted.
The scope never leaves its owning thread/task or survives its finally block.
"""
import asyncio
import copy
import hashlib
import zlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import current_thread

from .typed_payload_codec_v131 import canonical_json


MAX_ENTRIES = 8
MAX_RETAINED_BYTES = 16 * 1024 * 1024
_CURRENT = ContextVar('catalogue_allocation_inventory_pure_v213', default=None)
_CACHE_ERRORS = (ValueError, TypeError, UnicodeError, OverflowError, RecursionError, MemoryError)
_COMPRESSION_ERRORS = (zlib.error,) + _CACHE_ERRORS


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return current_thread(), task


def _binding(principal):
    values = tuple(getattr(principal, key, None) for key in
        ('user_id', 'workspace_id', 'session_id', 'role'))
    return values if all(type(value) is str and value for value in values) else None


@dataclass(frozen=True)
class _Entry:
    validator: object
    fingerprint: bytes
    content: bytes
    result: object
    original_length: int


@dataclass
class _Scope:
    principal: object
    binding: tuple
    owner: tuple
    entries: list = field(default_factory=list)
    retained_bytes: int = 0
    closed: bool = False

    def current(self):
        thread, task = _owner()
        return (not self.closed and thread is self.owner[0] and task is self.owner[1]
            and _binding(self.principal) == self.binding)

    def close(self):
        self.closed = True
        self.entries.clear()
        self.retained_bytes = 0


@contextmanager
def allocation_read_scope(principal):
    """Independent opt-in lifetime, including for nested inventory checks."""
    binding = _binding(principal)
    state = _Scope(principal, binding, _owner()) if binding is not None else None
    token = _CURRENT.set(state)
    try:
        yield
    finally:
        if state is not None:
            state.close()
        _CURRENT.reset(token)


def _content(value):
    try:
        return canonical_json(value).encode('utf-8', errors='strict')
    except _CACHE_ERRORS:
        return None


def _key_matches(entry, actual, fingerprint):
    # Only private, internally generated keys are compressed. Bound output by
    # the already materialized ACTUAL key before decompressing anything.
    if (type(entry.original_length) is not int or entry.original_length != len(actual)
            or len(actual) > MAX_RETAINED_BYTES or type(entry.content) is not bytes
            or type(entry.fingerprint) is not bytes or len(entry.fingerprint) != 32
            or entry.fingerprint != fingerprint):
        return False
    try:
        decoder = zlib.decompressobj()
        restored = decoder.decompress(entry.content, len(actual) + 1)
        return (decoder.eof is True and not decoder.unused_data and not decoder.unconsumed_tail
            and len(restored) == len(actual) and restored == actual)
    except _COMPRESSION_ERRORS:
        return False


def _check(value, validator):
    """Internal pure adapter. The validator identity participates in every hit.

    Retained bytes count the lossless key, SHA and canonical result spelling, not
    Python heap overhead. Entry count and the original evidence bounds also
    apply. Capacity/serialization/copy failure only disables this optimization.
    Exceptions from the original validator are never caught or remembered.
    """
    state = _CURRENT.get()
    if state is None or not state.current():
        return validator(value)
    content = _content(value)
    if content is None or len(content) > MAX_RETAINED_BYTES:
        return validator(value)
    fingerprint = hashlib.sha256(content).digest()
    for entry in state.entries:
        if (entry.validator is validator and state.current()
                and _key_matches(entry, content, fingerprint)):
            try:
                return copy.deepcopy(entry.result)
            except _CACHE_ERRORS:
                return validator(value)

    checked = validator(value)
    if not state.current() or _content(value) != content:
        return checked
    try:
        result = copy.deepcopy(checked)
    except _CACHE_ERRORS:
        return checked
    result_content = _content(result)
    if result_content is None:
        return checked
    try:
        compressed = zlib.compress(content)
    except _COMPRESSION_ERRORS:
        return checked
    size = len(compressed) + len(fingerprint) + len(result_content)
    if (state.current() and len(state.entries) < MAX_ENTRIES
            and size <= MAX_RETAINED_BYTES - state.retained_bytes):
        state.entries.append(_Entry(validator, fingerprint, compressed, result, len(content)))
        state.retained_bytes += size
    return checked
