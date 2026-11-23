"""Unrouted display-only total Goal budget from one real PostgreSQL snapshot.

No result from this module grants execution, sending, or reservation authority.
Current v1 totals retain their original accounting; queued v2 preparation is a
separate observation, not an executed call or a new budget authorization.
"""
from collections import defaultdict
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from . import same_goal_advice_common_inventory_v2 as inventory
from . import same_goal_advice_execution_v308 as advice
from . import same_goal_advice_read_session_v2 as isolation
from . import same_goal_advice_store_v302 as old
from .models import Task
from .typed_payload_codec_v131 import canonical_json


ERROR = '当前事务中有可能影响目标预算的未保存资料，请先保存或撤销；原编辑未被覆盖'


def _outside_workspace(state, saved, workspace_id):
    # Only exclude a scoped row when both its current and stored workspace are
    # known and different. Its data can never satisfy a workspace-scoped query.
    if 'workspace_id' not in state.mapper.attrs or 'workspace_id' not in state.dict:
        return False
    current = state.dict['workspace_id']
    return current is not None and current != workspace_id and (saved is None or saved != workspace_id)


def _extra_state(caller, reader, workspace_id):
    """Guard every loaded model not already covered by the inventory reader.

    Conservatively include evaluation commands and related source records, not
    just DataRun. Untracked JSON changes are compared with the same snapshot.
    """
    for obj in caller.new.union(caller.deleted):
        state = obj._sa_instance_state
        if state.mapper.class_ in isolation.INVENTORY_MODELS:
            continue
        if obj in caller.deleted or not _outside_workspace(state, None, workspace_id):
            raise HTTPException(409, ERROR)
        # An apparently unrelated transient row may reuse the primary key of
        # a current-workspace original; do not treat that collision as safe.
        primary = state.mapper.primary_key
        if len(primary) != 1:
            raise HTTPException(409, ERROR)
        key = state.mapper.get_property_by_column(primary[0]).key
        identity = state.dict.get(key)
        try: key_type = primary[0].type.python_type
        except (AttributeError, NotImplementedError): raise HTTPException(409, ERROR)
        if type(identity) is not key_type:
            raise HTTPException(409, ERROR)
        if reader.scalar(select(primary[0]).where(primary[0] == identity)) is not None:
            raise HTTPException(409, ERROR)
    groups = defaultdict(list)
    for state in caller.identity_map.all_states():
        if state.obj() is None or state.mapper.class_ in isolation.INVENTORY_MODELS:
            continue
        names = tuple(prop.key for prop in state.mapper.column_attrs if prop.key in state.dict)
        if not names: continue
        if len(state.mapper.primary_key) != 1 or state.identity is None or len(state.identity) != 1:
            raise HTTPException(409, ERROR)
        groups[(state.mapper, names)].append((state.identity[0], state))
    if sum(map(len, groups.values())) > isolation.MAX_LOADED_ROWS:
        raise HTTPException(409, ERROR)
    for (mapper, names), entries in groups.items():
        primary = mapper.primary_key[0]
        attrs = [getattr(mapper.class_, name) for name in names]
        rows = reader.execute(select(primary, *attrs).where(primary.in_([key for key, _ in entries]))).all()
        found = {row[0]: row[1:] for row in rows}
        for key, state in entries:
            saved = found.get(key)
            if saved is None:
                raise HTTPException(409, ERROR)
            values = dict(zip(names, saved))
            if _outside_workspace(state, values.get('workspace_id'), workspace_id):
                continue
            try:
                for name in names:
                    left, right = state.dict[name], values[name]
                    different = (canonical_json(left) != canonical_json(right)
                        if isinstance(left, (dict, list)) or isinstance(right, (dict, list))
                        else type(left) is not type(right) or left != right)
                    if different: raise HTTPException(409, ERROR)
            except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
                raise HTTPException(409, ERROR)


@old.checked
def read_snapshot(db, principal, task):
    """Read persisted totals as of a snapshot, never issue authority.

    Changes that may influence the totals are rejected without flushing. The
    caller retains its transaction and its in-memory state in every case.
    """
    old.need(type(task) is Task)
    try: UUID(principal.session_id)
    except (ValueError, TypeError, AttributeError): old.need(False, '只读展示必须使用真实用户会话')
    state = task._sa_instance_state
    old.need(state.identity is not None and len(state.identity) == 1
        and object_session(task) is db and db.identity_map.get(state.key) is task,
        '目标任务不属于当前会话，未展示预算')
    task_id = state.identity[0]
    with isolation.isolated(db, task_id) as reader:
        old.need(reader.get_bind().dialect.name == 'postgresql')
        level, readonly = reader.execute(select(func.current_setting('transaction_isolation'),
            func.current_setting('transaction_read_only'))).one()
        old.need(readonly == 'on' and level in {'repeatable read', 'serializable'},
            '只读展示需要一致的数据库快照；不能用于发送授权')
        _extra_state(db, reader, principal.workspace_id)
        stored_task = reader.get(Task, task_id)
        old.need(stored_task is not None and stored_task.workspace_id == principal.workspace_id)
        basis = inventory._read(reader, principal, task_id, lock=False)
        from . import goal_queries, native_goals
        calls = goal_queries.call_count(reader, principal, stored_task)
        steps = native_goals.total_steps(reader, principal, stored_task)
        old.need(advice.budget_calls(reader, principal, stored_task) == basis['v1_calls'],
            '原业务建议计数与共同库存不一致，未展示目标预算')
        _extra_state(db, reader, principal.workspace_id)
        result = {'calls': calls, 'steps': steps, 'queued_v2': basis['queued_v2']}
    # The isolation context performs further SQL on exit. Check the real
    # session *after* that work, on the same connection/snapshot and without
    # triggering the caller's autoflush. Concurrent revocations are still
    # visible only to a later snapshot.
    with db.no_autoflush:
        with Session(bind=db.connection(), autoflush=False, expire_on_commit=False,
                     join_transaction_mode='rollback_only') as last_reader:
            inventory.v2._lock_current_reader(last_reader, principal, lock=False)
    return result
