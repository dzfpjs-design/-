"""Bounded reuse of pure DOCX parsing within one synchronous operation.

No database row, evidence read, authority decision or OCR result is cached here.
The exact immutable source bytes and parser settings identify a result.  Leaving
the outer scope invalidates even copies of its ContextVar context.
"""
import asyncio
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import current_thread

MAX_ENTRIES = 2
MAX_SOURCE_BYTES = 64 * 1024 * 1024


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return current_thread(), task


@dataclass
class _Lifetime:
    owner: tuple
    closed: bool = False


@dataclass(frozen=True)
class _Entry:
    content: bytes
    policy: str
    max_blocks: int
    max_characters: int
    parse: object
    result: object


@dataclass(frozen=True)
class _State:
    lifetime: _Lifetime
    entries: tuple = ()
    source_bytes: int = 0


_CURRENT = ContextVar('report_text_parse_scope', default=None)


def _active():
    state = _CURRENT.get()
    if state is None or state.lifetime.closed or state.lifetime.owner != _owner():
        return None
    return state


@contextmanager
def report_text_parse_scope():
    """Share nested pure reads; always expire the outer operation on exit.

    Also usable as ``@report_text_parse_scope()``.  A copied context keeps only
    an expired lifetime after the original operation exits.  Separate tasks and
    threads start their own scopes, including when they inherit an outer context.
    """
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


def read_docx_cached(content, *, policy, max_blocks, max_characters, parse):
    """Consult only this operation's pure parser cache, or call ``parse``.

    Parser failures are never stored.  Full capacity causes ordinary parsing,
    without eviction, truncation or changes to the parser's validation gates.
    The callable itself is pinned so a changed implementation cannot reuse an
    earlier result.  Both returned hits and retained misses are detached copies.
    """
    state = _active()
    eligible = (state is not None and type(content) is bytes and type(policy) is str
        and type(max_blocks) is int and type(max_characters) is int
        and len(content) <= MAX_SOURCE_BYTES and state.source_bytes <= MAX_SOURCE_BYTES)
    if not eligible:
        return parse(content)
    for entry in state.entries:
        if (entry.parse is parse and entry.policy == policy
                and entry.max_blocks == max_blocks and entry.max_characters == max_characters
                and entry.content == content):
            return copy.deepcopy(entry.result)
    result = parse(content)
    # A reentrant pure caller may have populated the same operation while it
    # parsed.  Use the latest immutable state rather than overwriting its slots.
    latest = _active()
    if (latest is not None and latest.lifetime is state.lifetime
            and len(latest.entries) < MAX_ENTRIES
            and latest.source_bytes + len(content) <= MAX_SOURCE_BYTES):
        entry = _Entry(content, policy, max_blocks, max_characters, parse, copy.deepcopy(result))
        _CURRENT.set(_State(latest.lifetime, (*latest.entries, entry),
            latest.source_bytes + len(content)))
    return result
