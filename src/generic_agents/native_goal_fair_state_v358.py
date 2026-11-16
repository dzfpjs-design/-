"""Durable hints for an optional task-scoped scheduler; never authority.

This table is installed only by the explicit application migration. The
worker never creates it, and cannot operate if the schema cannot be verified.
"""
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, inspect, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator
from .models import Base, Row, Scoped, Payload


class NativeGoalFairStateV358(Row, Scoped, Payload, Base):
    __tablename__ = 'native_goal_fair_states_v358'
    __table_args__ = (UniqueConstraint('workspace_id', 'task_id', name='uq_goal_fair_workspace_task_v358'),)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'))
    instance_id: Mapped[str] = mapped_column(String(36))
    next_due_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


def require_schema(connection):
    """Reject missing or mismatched state storage, without executing DDL."""
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('公平调度需要已迁移的 PostgreSQL 状态表')
    if connection.scalar(text('SELECT current_schema()')) != 'public':
        raise RuntimeError('公平调度需要受信 public schema')
    inspector = inspect(connection)
    table = NativeGoalFairStateV358.__table__
    if not inspector.has_table(table.name, schema='public'):
        raise RuntimeError('公平调度状态表尚未迁移')
    actual = {col['name']: col for col in inspector.get_columns(table.name, schema='public')}
    expected = {col.name: col for col in table.columns}
    if set(actual) != set(expected):
        raise RuntimeError('公平调度状态表结构不匹配')
    for name, col in expected.items():
        found = actual[name]
        target = col.type.impl if isinstance(col.type, TypeDecorator) else col.type
        if found['nullable'] != col.nullable or not isinstance(found['type'], type(target)):
            raise RuntimeError('公平调度状态表字段不匹配')
        if getattr(target, 'length', None) != getattr(found['type'], 'length', None):
            raise RuntimeError('公平调度状态表字段长度不匹配')
        if isinstance(target, DateTime) and target.timezone != found['type'].timezone:
            raise RuntimeError('公平调度状态表时间字段不匹配')
    uniques = {tuple(item['column_names']) for item in inspector.get_unique_constraints(table.name, schema='public')}
    if ('workspace_id', 'task_id') not in uniques:
        raise RuntimeError('公平调度状态表缺少范围唯一约束')
    primary = inspector.get_pk_constraint(table.name, schema='public').get('constrained_columns')
    if primary != ['id']:
        raise RuntimeError('公平调度状态表主键不匹配')
    foreign = {(tuple(item['constrained_columns']), item['referred_table'], tuple(item['referred_columns']))
               for item in inspector.get_foreign_keys(table.name, schema='public')}
    if not {(('workspace_id',), 'workspaces', ('id',)), (('task_id',), 'tasks', ('id',))} <= foreign:
        raise RuntimeError('公平调度状态表范围约束不匹配')
    # Names alone cannot authenticate a target: another schema can hold a
    # same-named table. Require verified constraints pointing to trusted OIDs.
    fk_sql = text('''SELECT EXISTS (
      SELECT 1 FROM pg_catalog.pg_constraint c
      WHERE c.conrelid=to_regclass('public.native_goal_fair_states_v358')
        AND c.contype='f' AND c.convalidated
        AND c.confrelid=to_regclass(:target)
        AND c.conkey=ARRAY[(SELECT attnum FROM pg_catalog.pg_attribute
          WHERE attrelid=c.conrelid AND attname=:source AND NOT attisdropped)]::smallint[]
        AND c.confkey=ARRAY[(SELECT attnum FROM pg_catalog.pg_attribute
          WHERE attrelid=c.confrelid AND attname='id' AND NOT attisdropped)]::smallint[])''')
    for source, target in (('workspace_id','public.workspaces'),('task_id','public.tasks')):
        if not connection.scalar(fk_sql, {'source':source,'target':target}):
            raise RuntimeError('公平调度状态表外键目标或验证状态不匹配')
    valid_unique = connection.scalar(text('''SELECT EXISTS (
      SELECT 1 FROM pg_catalog.pg_constraint c
      JOIN pg_catalog.pg_index i ON i.indexrelid=c.conindid
      WHERE c.conrelid=to_regclass('public.native_goal_fair_states_v358')
        AND c.contype='u' AND c.convalidated AND i.indisvalid AND i.indisready AND i.indisunique
        AND c.conkey=ARRAY[
          (SELECT attnum FROM pg_catalog.pg_attribute WHERE attrelid=c.conrelid AND attname='workspace_id' AND NOT attisdropped),
          (SELECT attnum FROM pg_catalog.pg_attribute WHERE attrelid=c.conrelid AND attname='task_id' AND NOT attisdropped)
        ]::smallint[])'''))
    if not valid_unique:
        raise RuntimeError('公平调度状态表唯一索引无效')
