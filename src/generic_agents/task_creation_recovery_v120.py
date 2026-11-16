"""Durable retirement of an original Task nonce, serialized with creation.

A network error never proves a Task was not saved. The user can instead seal
the exact original request under the same instance lock used by create_task.
Once sealed it can never create a Task, including delayed transport retries.
"""
import copy
import json
import re
from fastapi import HTTPException
from sqlalchemy import ForeignKey, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, Row, Scoped, Payload, Task
from .contracts import digest


class TaskCreationRetirement(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_task_creation_retirements'
    __table_args__ = (UniqueConstraint('workspace_id', 'instance_id', 'request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    body_hash: Mapped[str] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64))


def find(db, p, identity, request_id):
    return db.scalar(select(TaskCreationRetirement).where(
        TaskCreationRetirement.workspace_id == p.workspace_id,
        TaskCreationRetirement.instance_id == identity,
        TaskCreationRetirement.request_id == request_id))


def checked_view(row, p, signature):
    if row.actor_id != p.user_id:
        raise HTTPException(403, '请由原发起账号核对这项请求')
    try:
        # This import is deliberately lazy: migrate registers the additive model
        # without importing the API router. Historical checks need no live graph,
        # current source, publication or original execution permission.
        from .workspace_api import NewTask, creation_body_hash
        if (row.workspace_id != p.workspace_id or type(row.version) is not int or row.version != 1
                or type(row.data) is not dict or set(row.data) != {'reason', 'original'}):
            raise ValueError('retirement_shape')
        reason, original = row.data['reason'], row.data['original']
        if (type(reason) is not str or not reason or reason != reason.strip()
                or len(reason) > 1000 or '\x00' in reason or type(original) is not dict):
            raise ValueError('retirement_data')
        # Never normalize or rewrite an old stored command into a new receipt.
        # JSON encoding also rejects nonfinite numbers and invalid UTF-8 text.
        json.dumps(row.data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        typed = NewTask.model_validate(original)
        frozen = typed.model_dump(mode='json', exclude_unset=True)
        if (digest(frozen) != digest(original) or typed.request_id != row.request_id
                or type(row.body_hash) is not str or re.fullmatch(r'[a-f0-9]{64}', row.body_hash) is None
                or row.body_hash != signature or creation_body_hash(typed) != row.body_hash
                or type(row.command_hash) is not str or re.fullmatch(r'[a-f0-9]{64}', row.command_hash) is None
                or row.command_hash != digest({'actor_id': row.actor_id, 'instance_id': row.instance_id,
                    'original': original, 'reason': reason})):
            raise ValueError('retirement_hash')
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OverflowError):
        raise HTTPException(409, '原发起请求的保留记录无法完整核对，原记录保留') from None
    return {'schema': 'workflow-task-request-retirement-v1', 'actor_id': row.actor_id,
        'instance_id': row.instance_id, 'request_id': row.request_id,
        'creation_hash': row.body_hash, 'reason': reason,
        'original': copy.deepcopy(original), 'command_hash': row.command_hash}


def retire(db, p, identity, original, signature, reason):
    # Caller holds the real DomainInstance row lock before entering here.
    if db.scalar(select(Task.id).where(Task.workspace_id == p.workspace_id,
        Task.data['instance_id'].as_string() == identity,
        Task.data['creation_request_id'].as_string() == original.request_id)):
        raise HTTPException(409, '原任务已经保存，请核对原发起请求，不能撤回后重复发起')
    reason = reason.strip()
    command_hash = digest({'actor_id': p.user_id, 'instance_id': identity,
        'original': original.model_dump(mode='json',exclude_unset=True), 'reason': reason})
    row = find(db, p, identity, original.request_id)
    if row:
        value = checked_view(row, p, signature)
        if row.command_hash != command_hash:
            raise HTTPException(409, '原请求保留记录不能更换内容或原因')
        return value
    row = TaskCreationRetirement(workspace_id=p.workspace_id, instance_id=identity,
        actor_id=p.user_id, request_id=original.request_id, body_hash=signature,
        command_hash=command_hash, data={'reason': reason, 'original': original.model_dump(mode='json',exclude_unset=True)})
    db.add(row); db.flush()
    return checked_view(row, p, signature)
