"""Bounded, non-authoritative pure-input reuse for one review GET.

Directories, complete V181 input selections and an explicitly enabled pure Map
preparation check use separate private entries.
The first value is checked by the unchanged complete function. Every hit
re-encodes ALL actual inputs, not just their self-hashes or object IDs.
No IAM, row, file, receipt, model, source grant or answer result is cached here.
Copied contexts cannot share a result with another thread/task or outlive the
scope. Nested scopes are independent, even when their principal is identical.
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
# Bound stored keys (raw catalogs, losslessly compressed selection parameters),
# fingerprints and the canonical spelling-size of their private JSON results.
# Python object overhead is additionally bounded by entry count and the already
# bounded original inputs; this is not a heap-size claim.
MAX_RETAINED_BYTES = 16 * 1024 * 1024
_CURRENT = ContextVar('evidence_directory_review_request_v191', default=None)
_FINGERPRINT_ERRORS = (ValueError, TypeError, UnicodeError, OverflowError, RecursionError)
_MAP_FINGERPRINT_ERRORS = (AttributeError,) + _FINGERPRINT_ERRORS


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return current_thread(), task


def _binding(principal, attempt_id):
    values = tuple(getattr(principal, key, None) for key in
        ('user_id', 'workspace_id', 'session_id', 'role')) + (attempt_id,)
    # An incomplete synthetic/service identity retains the full original path.
    # This optimization never admits, rejects or repairs an identity itself.
    return values if all(type(value) is str and value for value in values) else None


@dataclass(frozen=True)
class _Entry:
    kind: str
    validator: object
    fingerprint: bytes
    content: bytes
    result: object
    original_length: int | None = None


@dataclass
class _Scope:
    principal: object
    attempt_id: str
    binding: tuple
    owner: tuple
    map_preparation_enabled: bool = False
    closed: bool = False
    entries: list = field(default_factory=list)
    retained_bytes: int = 0

    def current(self):
        thread, task = _owner()
        return (not self.closed and thread is self.owner[0] and task is self.owner[1]
            and _binding(self.principal, self.attempt_id) == self.binding)

    def close(self):
        self.closed = True
        self.entries.clear()
        self.retained_bytes = 0


@contextmanager
def catalog_read_scope(principal, attempt_id, *, map_preparation=False):
    """Start an independent lifetime; no result or authority handle is exposed."""
    binding = _binding(principal, attempt_id)
    state = (_Scope(principal, attempt_id, binding, _owner(),
        map_preparation_enabled=map_preparation is True) if binding is not None else None)
    token = _CURRENT.set(state)
    try:
        yield
    finally:
        if state is not None:
            state.close()
        _CURRENT.reset(token)


def _content(value):
    try:
        # Exact JSON types and all nested values are visited before encoding;
        # bool/int coercions or non-JSON keys cannot alias a valid directory.
        return canonical_json(value).encode('utf-8', errors='strict')
    except _FINGERPRINT_ERRORS:
        return None


def _check_catalog(catalog, validator, *, detach=True):
    """Internal adapter, never a caller-supplied ``verified`` shortcut.

    Validator identity is part of each entry: an unrelated permissive callback
    cannot prime the complete public validator's cache. Errors are not cached.
    Outside a valid owner scope even return-object identity is unchanged.
    """
    state = _CURRENT.get()
    if state is None or not state.current():
        checked = validator(catalog)
        return checked if detach else None
    content = _content(catalog)
    if content is None:
        checked = validator(catalog)
        return checked if detach else None
    fingerprint = hashlib.sha256(content).digest()
    for entry in state.entries:
        if (entry.kind == 'catalog' and entry.validator is validator and entry.fingerprint == fingerprint
                and entry.content == content):
            return copy.deepcopy(entry.result) if detach else None

    # Always run the complete original validator on a miss. A detached result
    # cannot expose or mutate the private snapshot used by another hit.
    checked = validator(catalog)
    result = copy.deepcopy(checked)
    after = _content(result)
    size = len(content) * 2 + len(fingerprint)
    if (after == content and state.current() and len(state.entries) < MAX_ENTRIES
            and size <= MAX_RETAINED_BYTES - state.retained_bytes):
        state.entries.append(_Entry('catalog', validator, fingerprint, content, result))
        state.retained_bytes += size
        return copy.deepcopy(result) if detach else None
    # Capacity, a changed input, or an invalidated lifetime only disables
    # reuse. It never introduces a new business rejection or evicts authority.
    return result if detach else None


def _selected_inputs(source_context, scope_plan, scope_id, selector):
    """Reuse only the complete V181 pure selection, never an admission result.

    All three actual parameters participate. The tuple's JSON representation
    is used only to measure retained bytes; the returned tuple and all nested
    values retain their original types. Only the full key is compressed; every
    hit checks its bounded decompression against ALL freshly encoded bytes.
    Misses/errors/capacity never replace the real selector, and a different
    callback cannot prime its entries.
    """
    state = _CURRENT.get()
    if state is None or not state.current():
        return selector(source_context, scope_plan, scope_id)
    content = _content([source_context, scope_plan, scope_id])
    if content is None:
        return selector(source_context, scope_plan, scope_id)
    fingerprint = hashlib.sha256(content).digest()
    for entry in state.entries:
        if (entry.kind == 'selected_inputs' and entry.validator is selector
                and _selected_key_matches(entry, content, fingerprint)):
            return copy.deepcopy(entry.result)

    checked = selector(source_context, scope_plan, scope_id)
    after = _content([source_context, scope_plan, scope_id])
    result = copy.deepcopy(checked)
    result_content = _content(list(result)) if type(result) is tuple else None
    if (result_content is not None and after == content and state.current()
            and len(state.entries) < MAX_ENTRIES):
        try:
            compressed = zlib.compress(content)
        except (zlib.error, OverflowError, MemoryError):
            return result
        size = len(compressed) + len(result_content) + len(fingerprint)
        if size <= MAX_RETAINED_BYTES - state.retained_bytes:
            state.entries.append(_Entry('selected_inputs', selector, fingerprint, compressed,
                result, original_length=len(content)))
            state.retained_bytes += size
            return copy.deepcopy(result)
    return result


def _map_preparation(value, catalog, cap, validator):
    """Opt-in reuse of only the complete V172 PURE preparation validator.

    The original validator always executes on a miss. Full actual plan,
    directory and cap.data participate, including execution and data_policy.
    No capability identity/current publication, files, receipts or DB results
    enter this cache. Keys are losslessly compressed and every hit compares
    against newly encoded FULL bytes, not caller-provided hashes or object IDs.
    """
    state = _CURRENT.get()
    if state is None or not state.current() or not state.map_preparation_enabled:
        return validator(value, catalog, cap)
    try:
        content = _content([value, catalog, cap.data])
    except _MAP_FINGERPRINT_ERRORS:
        content = None
    if content is None:
        return validator(value, catalog, cap)
    fingerprint = hashlib.sha256(content).digest()
    for entry in state.entries:
        if (entry.kind == 'map_preparation' and entry.validator is validator
                and state.current() and _selected_key_matches(entry, content, fingerprint)):
            return copy.deepcopy(entry.result)

    checked = validator(value, catalog, cap)
    try:
        after = _content([value, catalog, cap.data])
    except _MAP_FINGERPRINT_ERRORS:
        after = None
    result = copy.deepcopy(checked)
    result_content = _content(result)
    if (result_content is not None and after == content and state.current()
            and len(state.entries) < MAX_ENTRIES):
        try:
            compressed = zlib.compress(content)
        except (zlib.error, OverflowError, MemoryError):
            return checked
        size = len(compressed) + len(result_content) + len(fingerprint)
        if size <= MAX_RETAINED_BYTES - state.retained_bytes:
            state.entries.append(_Entry('map_preparation', validator, fingerprint,
                compressed, result, original_length=len(content)))
            state.retained_bytes += size
            return copy.deepcopy(result)
    # Capacity/serialization/lifetime merely disables reuse. It introduces no
    # extra business rejection or eviction of previously checked evidence.
    return checked


def _selected_key_matches(entry, actual, fingerprint):
    """A damaged private key is a miss, never a source or business rejection.

    Check the advertised size against the already materialized actual key
    BEFORE allocating decompression output. max_length bounds output to that
    exact original length plus one; no unbounded flush/decompress is used.
    EOF, both tail forms, length and full bytes are independently checked.
    """
    if (type(entry.original_length) is not int or entry.original_length != len(actual)
            or type(entry.content) is not bytes or type(entry.fingerprint) is not bytes
            or len(entry.fingerprint) != 32 or entry.fingerprint != fingerprint):
        return False
    try:
        decoder = zlib.decompressobj()
        restored = decoder.decompress(entry.content, entry.original_length + 1)
        return (decoder.eof is True and not decoder.unused_data and not decoder.unconsumed_tail
            and len(restored) == entry.original_length and restored == actual)
    except (zlib.error, ValueError, TypeError, OverflowError, MemoryError):
        return False
