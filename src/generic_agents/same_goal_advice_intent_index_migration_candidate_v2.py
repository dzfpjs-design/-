"""Disposable-only rehearsal of one action-specific audit uniqueness migration.

Never called by routes/startup. It performs real DDL only after verifying the
explicit dedicated test-cluster identity. No historical rows or existing
indexes are ever deleted or rewritten. Not a production migration.
"""
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from .db import engine

ACTION='same_goal_advice.file_intent_candidate_v2'
NAMES=('ga_test_advice_file_intent_unique_v2a','ga_test_advice_file_intent_unique_v2b')
LOCK=736421381046


class _GuardConnection:
    def __init__(self,conn):self.conn=conn
    def get_bind(self):return self.conn.engine
    def execute(self,*args,**kwargs):return self.conn.execute(*args,**kwargs)


def _definition(name):
    # The implementation is intentionally pinned to the verified PG17
    # deparser. A different version or representation fails closed.
    return (f'CREATE UNIQUE INDEX {name} ON public.audit_events USING btree '
        "(workspace_id, object_id) WHERE ((action)::text = '"+ACTION+"'::text)")


def _catalog(conn,name):
    row=conn.execute(text("SELECT c.oid,c.relkind,i.indisvalid,i.indisready,i.indisunique,i.indislive, "
        "CASE WHEN i.indexrelid IS NOT NULL THEN pg_get_indexdef(c.oid) END AS definition, "
        "CASE WHEN i.indexrelid IS NOT NULL THEN i.indrelid END AS target "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_index i ON i.indexrelid=c.oid "
        "WHERE n.nspname='public' AND c.relname=:name"),{'name':name}).mappings().one_or_none()
    if row is None:return {'state':'missing'}
    target=conn.scalar(text("SELECT to_regclass('public.audit_events')::oid"))
    if (row['relkind']!='i' or row['target']!=target or row['definition']!=_definition(name)
            or row['indisunique'] is not True or row['indislive'] is not True):
        return {'state':'foreign_or_unknown'}
    if row['indisvalid'] is True and row['indisready'] is True:
        return {'state':'valid','oid':int(row['oid'])}
    if row['indisvalid'] is False:
        return {'state':'invalid','oid':int(row['oid']),'ready':bool(row['indisready'])}
    return {'state':'foreign_or_unknown'}


def _duplicates(conn):
    return bool(conn.scalar(text("SELECT EXISTS (SELECT 1 FROM public.audit_events "
        "WHERE action=:action GROUP BY workspace_id,object_id HAVING count(*)>1 LIMIT 1)"),{'action':ACTION}))


def _building(conn):
    return bool(conn.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_stat_progress_create_index "
        "WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) "
        "AND relid=to_regclass('public.audit_events')::oid)")))


def _build(conn,name):
    # name only originates from the two constants above; never from input.
    conn.execute(text(f"CREATE UNIQUE INDEX CONCURRENTLY {name} ON public.audit_events "
        "(workspace_id, object_id) WHERE action = 'same_goal_advice.file_intent_candidate_v2'"))


def rehearse():
    """Install or safely resume in the disposable cluster; returns status only.

    Duplicate history is retained. Failed/invalid/foreign indexes are never
    dropped. When the first exact invalid index is inactive and history is
    clear, the second predetermined name permits recovery while explicitly
    reporting the retained invalid object. No production use is authorized.
    """
    with engine().connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        guard.require_disposable_database(_GuardConnection(conn))
        version=int(conn.scalar(text("SELECT current_setting('server_version_num')")))
        if not 170000<=version<180000:return {'status':'unsupported_database'}
        table=conn.execute(text("SELECT c.relkind, "
            "(SELECT bool_and(a.attnotnull) FROM pg_attribute a WHERE a.attrelid=c.oid "
            "AND a.attname IN ('workspace_id','object_id')) AS notnull, "
            "(SELECT count(*) FROM pg_attribute a WHERE a.attrelid=c.oid "
            "AND a.attname IN ('workspace_id','object_id') AND a.attnum>0 AND NOT a.attisdropped) AS n "
            "FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace "
            "WHERE ns.nspname='public' AND c.relname='audit_events'" )).mappings().one_or_none()
        if not table or table['relkind']!='r' or table['notnull'] is not True or table['n']!=2:
            return {'status':'unsupported_table'}
        previous=conn.execute(text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")).one()
        if not conn.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':LOCK}):
            return {'status':'migration_busy'}
        try:
            # A non-cooperating builder may be invisible to the progress view.
            # Bound both lock waits and individual scans/DDL. Restore session
            # settings before returning this connection to the pool.
            conn.execute(text("SELECT set_config('lock_timeout','2s',false)"))
            conn.execute(text("SELECT set_config('statement_timeout','15s',false)"))
            states=[_catalog(conn,name) for name in NAMES]
            if any(s['state']=='foreign_or_unknown' for s in states):return {'status':'name_or_definition_conflict'}
            valid=[NAMES[i] for i,s in enumerate(states) if s['state']=='valid']
            invalid=[NAMES[i] for i,s in enumerate(states) if s['state']=='invalid']
            # Do not describe an in-progress index as a settled invalid
            # leftover, even if another valid index is already available.
            if _building(conn):return {'status':'build_in_progress','invalid_retained':invalid}
            if valid:return {'status':'already_installed','valid_indexes':valid,'invalid_retained':invalid}
            if _duplicates(conn):return {'status':'duplicate_history','invalid_retained':invalid}
            names=[NAMES[i] for i,s in enumerate(states) if s['state']=='missing']
            if not names:return {'status':'no_safe_name','invalid_retained':invalid}
            chosen=names[0]
            try:
                _build(conn,chosen)
            except SQLAlchemyError:
                # Never expose SQL/provider data. Capture state after failure;
                # a concurrently failed build may leave an invalid index.
                state=_catalog(conn,chosen)
                return {'status':'build_failed_review','index':chosen,
                    'invalid_retained':invalid+([chosen] if state['state']=='invalid' else []),
                    'observed_state':state['state']}
            final=_catalog(conn,chosen)
            if final['state']!='valid':return {'status':'verification_failed','index':chosen,'observed_state':final['state']}
            return {'status':'installed','index':chosen,'invalid_retained':invalid}
        finally:
            try:
                conn.execute(text("SELECT set_config('lock_timeout',:value,false)"),{'value':previous[0]})
                conn.execute(text("SELECT set_config('statement_timeout',:value,false)"),{'value':previous[1]})
            finally:
                conn.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':LOCK})
