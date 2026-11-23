"""Isolate custody refreshes from a caller's ORM identity map.

This is a read helper, not authority, a route or a writer. The second Session
uses the caller's existing connection/transaction, so row locks remain owned
by that transaction; it never commits or rolls back the caller's transaction.
Pending ORM writes are rejected rather than silently ignored by the reader.
"""
from contextlib import contextmanager
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .typed_payload_codec_v131 import canonical_json

MAX_LOADED_ROWS = 1000


def _check_loaded_state(caller, reader):
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
        names = tuple(prop.key for prop in state.mapper.column_attrs if prop.key in state.dict)
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
def isolated(db):
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, '当前事务存在未保存的修改，请先保存或撤销；原修改未被覆盖')
    with db.no_autoflush:
        connection = db.connection()
        with Session(bind=connection, autoflush=False, expire_on_commit=False,
                     join_transaction_mode='rollback_only') as reader:
            _check_loaded_state(db, reader)
            yield reader
            # Recheck after the custody reader acquired its Task/actor/Plan/Job
            # locks, so a concurrent change between the first comparison and
            # those locks cannot silently mix a caller's old JSON with the
            # newly read database snapshot.
            _check_loaded_state(db, reader)
