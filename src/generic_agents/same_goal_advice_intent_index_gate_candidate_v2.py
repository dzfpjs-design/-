"""Disposable-only write fence for the v2 file-intent audit constraint.

The table lock prevents concurrent index DDL until transaction end while
remaining compatible with ordinary audit INSERTs. It serializes writers that
also take this fence, so it is not a production throughput design.
"""
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_intent_index_migration_candidate_v2 as migration
from . import same_goal_advice_store_v302 as old


@old.checked
def require(db, workspace_id):
    """Hold the index DDL fence through this transaction and check structure.

    NOWAIT fails closed if maintenance or another fenced writer has a
    conflicting lock; no caller may interpret that as a missing receipt.
    This never creates, drops or repairs an index and is not a cloud grant.
    """
    guard.require_disposable_database(db)
    old.need(db.get_nested_transaction() is None, '资料索引核验不支持保存点')
    try:
        db.execute(text('LOCK TABLE public.audit_events IN SHARE UPDATE EXCLUSIVE MODE NOWAIT'))
    except DBAPIError as exc:
        if getattr(exc.orig,'sqlstate',None)=='55P03':
            raise HTTPException(409,'资料索引正在维护或被占用；未写入资料，请稍后重试') from None
        raise HTTPException(503,'无法核对资料索引；未写入资料') from None
    conn=db.connection()
    version=int(conn.scalar(text("SELECT current_setting('server_version_num')")))
    old.need(170000<=version<180000, '数据库版本无法核对资料索引；未写入资料')
    states=[migration._catalog(conn,name) for name in migration.NAMES]
    old.need(all(state['state']!='foreign_or_unknown' for state in states),
        '资料索引名称或定义无法核对；未写入资料')
    candidates={state['oid'] for state in states if state['state']=='valid'}
    old.need(candidates, '资料唯一索引尚未就绪；未写入资料')
    # A table SHARE UPDATE EXCLUSIVE lock alone does not block ALTER INDEX
    # RENAME. Execute an index-eligible lookup, then prove this transaction
    # actually holds a relation lock on at least one exact valid index.
    # Merely requesting a plan or reading pg_index is not sufficient.
    before=db.scalar(text("SELECT current_setting('enable_seqscan')"))
    try:
        db.execute(text("SELECT set_config('enable_seqscan','off',true)"))
        db.execute(text("SELECT 1 FROM public.audit_events WHERE workspace_id=:workspace "
            "AND object_id=:object_id AND action='same_goal_advice.file_intent_candidate_v2' LIMIT 1"),
            {'workspace':workspace_id,'object_id':'00000000-0000-0000-0000-000000000000'}).first()
    finally:
        db.execute(text("SELECT set_config('enable_seqscan',:value,true)"),{'value':before})
    locked=set(db.scalars(text("SELECT relation FROM pg_locks WHERE pid=pg_backend_pid() "
        "AND locktype='relation' AND mode='AccessShareLock' AND granted AND relation=ANY(:oids)"),
        {'oids':list(candidates)}))
    old.need(bool(locked), '未能固定资料唯一索引；未写入资料')
    # The index might have been renamed between the first catalog lookup and
    # acquisition of its relation lock. Re-read after the lock is held.
    after=[migration._catalog(conn,name) for name in migration.NAMES]
    old.need(all(state['state']!='foreign_or_unknown' for state in after)
        and any(state['state']=='valid' and state['oid'] in locked for state in after),
        '资料唯一索引在核验期间变化；未写入资料')
    return {'index_checked':True}
