"""Isolate custody refreshes from a caller's ORM identity map.

This is a read helper, not authority, a route or a writer. The second Session
uses the caller's existing connection/transaction, so row locks remain owned
by that transaction; it never commits or rolls back the caller's transaction.
Changes to records that could affect this inventory are rejected; unrelated
edits stay untouched and do not affect this read-only counter.
"""
from contextlib import contextmanager
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Audit, AuthSession, Capability, CloudCall, Evidence, Invocation, Job, Membership, Plan, Task, User
from .typed_payload_codec_v131 import canonical_json

MAX_LOADED_ROWS = 1000
INVENTORY_MODELS = frozenset({Task, Plan, Job, Audit, Invocation, CloudCall, Evidence, Capability,
    User, Membership, AuthSession})


def _columns(state, task_id):
    model = state.mapper.class_
    if model not in INVENTORY_MODELS:
        return ()
    if model is Task:
        if state.identity is None or state.identity[0] != task_id:
            return ()
        # Title is presentation-only; it is not used by inventory or the
        # surrounding goal counters. Preserve an in-progress title edit.
        return tuple(prop.key for prop in state.mapper.column_attrs if prop.key != 'title')
    return tuple(prop.key for prop in state.mapper.column_attrs)


def _check_pending(caller, task_id):
    for obj in caller.new.union(caller.deleted):
        state = obj._sa_instance_state
        if state.mapper.class_ is Task:
            current_id = state.identity[0] if state.identity is not None else state.dict.get('id')
            if current_id != task_id:
                continue
        if state.mapper.class_ in INVENTORY_MODELS:
            raise HTTPException(409, '当前事务存在影响计数的未保存修改；原修改未被覆盖')
    for obj in caller.dirty:
        state = obj._sa_instance_state
        names = _columns(state, task_id)
        if any(state.attrs[name].history.has_changes() for name in names):
            raise HTTPException(409, '当前事务存在影响计数的未保存修改；原修改未被覆盖')


def _check_loaded_state(caller, reader, task_id):
    """Reject stale or untracked loaded state without refreshing the caller.

    SQLAlchemy's ordinary JSON column does not mark an in-place dict/list edit
    dirty. Compare loaded mapped columns, including status/lease/version,
    against the same transaction's stored values, in mapper/column batches.
    A caller whose in-memory state differs must explicitly save or discard it.
    """
    groups = defaultdict(list)
    for state in caller.identity_map.all_states():
        if state.obj() is None:
            continue
        names = tuple(name for name in _columns(state, task_id) if name in state.dict)
        if not names:
            continue
        if len(state.mapper.primary_key) != 1 or state.identity is None or len(state.identity) != 1:
            raise HTTPException(409, '当前事务中已加载的资料无法安全核对；未覆盖原修改')
        groups[(state.mapper, names)].append((state.identity[0], state))
    if sum(len(entries) for entries in groups.values()) > MAX_LOADED_ROWS:
        raise HTTPException(409, '当前事务中已加载的资料过多，未进行可能不完整的核对')
    for (mapper, names), entries in groups.items():
        primary = mapper.primary_key[0]
        attrs = [getattr(mapper.class_, name) for name in names]
        rows = reader.execute(select(primary, *attrs).where(primary.in_([key for key, _ in entries]))).all()
        values = {row[0]: row[1:] for row in rows}
        for key, state in entries:
            saved = values.get(key)
            try:
                def differs(left, right):
                    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
                        return canonical_json(left) != canonical_json(right)
                    return type(left) is not type(right) or left != right
                changed = saved is None or any(differs(state.dict[name], saved[index])
                    for index, name in enumerate(names))
            except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
                changed = True
            if changed:
                raise HTTPException(409, '当前事务存在未保存或已变化的资料；原修改未被覆盖')


@contextmanager
def isolated(db, task_id):
    _check_pending(db, task_id)
    with db.no_autoflush:
        connection = db.connection()
        with Session(bind=connection, autoflush=False, expire_on_commit=False,
                     join_transaction_mode='rollback_only') as reader:
            _check_loaded_state(db, reader, task_id)
            yield reader
            # Recheck after the custody reader acquired its Task/actor/Plan/Job
            # locks, so a concurrent change between the first comparison and
            # those locks cannot silently mix a caller's old JSON with the
            # newly read database snapshot.
            _check_pending(db, task_id)
            _check_loaded_state(db, reader, task_id)
