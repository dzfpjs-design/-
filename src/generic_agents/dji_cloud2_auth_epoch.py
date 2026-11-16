"""Cloud API 2 permission epochs, installed by an explicit PostgreSQL migration.

The two identity tables remain mapped exactly as before for the rest of the
product. These columns and triggers are consumed only by Cloud API 2. A missing,
disabled, changed or unverifiable trigger blocks Cloud API 2 authorization.
Database administrators who can disable triggers or restore old snapshots are
outside this fence; a restore must invalidate existing Cloud API 2 grants.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text

_USER_BODY = """
BEGIN
  IF TG_OP = 'INSERT' THEN
    NEW._ga_cloud2_auth_nonce := pg_catalog.gen_random_uuid();
    NEW._ga_cloud2_auth_generation := 1;
  ELSE
    IF NEW._ga_cloud2_auth_nonce IS DISTINCT FROM OLD._ga_cloud2_auth_nonce
       OR NEW._ga_cloud2_auth_generation IS DISTINCT FROM OLD._ga_cloud2_auth_generation THEN
      RAISE EXCEPTION 'Cloud API 2 authorization identity is managed by the database' USING ERRCODE = '23514';
    END IF;
    IF NEW.active IS DISTINCT FROM OLD.active THEN
      NEW._ga_cloud2_auth_generation := OLD._ga_cloud2_auth_generation + 1;
    END IF;
  END IF;
  RETURN NEW;
END;
"""

_MEMBERSHIP_BODY = """
BEGIN
  IF TG_OP = 'INSERT' THEN
    NEW._ga_cloud2_auth_nonce := pg_catalog.gen_random_uuid();
    NEW._ga_cloud2_auth_generation := 1;
  ELSE
    IF NEW._ga_cloud2_auth_nonce IS DISTINCT FROM OLD._ga_cloud2_auth_nonce
       OR NEW._ga_cloud2_auth_generation IS DISTINCT FROM OLD._ga_cloud2_auth_generation THEN
      RAISE EXCEPTION 'Cloud API 2 authorization identity is managed by the database' USING ERRCODE = '23514';
    END IF;
    IF NEW.role IS DISTINCT FROM OLD.role OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
      NEW._ga_cloud2_auth_generation := OLD._ga_cloud2_auth_generation + 1;
    END IF;
  END IF;
  RETURN NEW;
END;
"""

_TABLES = (
    ('users', 'ga_cloud2_user_epoch_v1', 'tr_ga_cloud2_user_epoch_v1', _USER_BODY),
    ('memberships', 'ga_cloud2_membership_epoch_v1', 'tr_ga_cloud2_membership_epoch_v1', _MEMBERSHIP_BODY),
)
_MARKER = 'public.ga_cloud2_auth_epoch_schema_v1'


def _integrity(connection):
    """Check actual PostgreSQL catalogs, not just a migration version marker."""
    if connection.dialect.name != 'postgresql':
        return False
    # A single catalog query keeps repeated runtime permission checks bounded
    # even when several workspace connections poll simultaneously.
    rows = connection.execute(text("""
        SELECT e.table_name, pg_catalog.to_regclass(:marker) IS NOT NULL,
               pg_catalog.current_setting('session_replication_role') = 'origin',
               (SELECT count(*) = 2 FROM pg_catalog.pg_attribute a
                WHERE a.attrelid=pg_catalog.to_regclass('public.' || e.table_name)
                  AND a.attnum>0 AND NOT a.attisdropped AND a.attnotnull
                  AND ((a.attname='_ga_cloud2_auth_nonce'
                        AND pg_catalog.format_type(a.atttypid,a.atttypmod)='uuid')
                       OR (a.attname='_ga_cloud2_auth_generation'
                        AND pg_catalog.format_type(a.atttypid,a.atttypmod)='bigint'))),
               t.tgtype, t.tgenabled, t.tgnargs, t.tgqual IS NULL, t.tgattr::text = '',
               p.prosrc, p.prosecdef, p.proconfig, l.lanname,
               n.nspname, p.proname, p.pronargs
        FROM (VALUES ('users','tr_ga_cloud2_user_epoch_v1'),
                     ('memberships','tr_ga_cloud2_membership_epoch_v1')) AS e(table_name,trigger_name)
        LEFT JOIN pg_catalog.pg_trigger t ON t.tgrelid=pg_catalog.to_regclass('public.' || e.table_name)
             AND t.tgname=e.trigger_name AND NOT t.tgisinternal
        LEFT JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
        LEFT JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        LEFT JOIN pg_catalog.pg_language l ON l.oid=p.prolang
    """), {'marker': _MARKER}).all()
    expected = {table: (table, True, True, True, 23, 'O', 0, True, True, body,
                        False, ['search_path=pg_catalog'], 'plpgsql', 'public', function, 0)
                for table, function, _, body in _TABLES}
    return len(rows) == 2 and all(tuple(row) == expected.get(row[0]) for row in rows)


def migrate(engine):
    """Idempotent additive migration; never silently repairs an unsafe guard.

    Must run with normal writers quiesced and after a recoverable database
    backup. Existing Cloud API 2 rows do not get invented authorizations.
    """
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Cloud API 2 authorization epoch requires PostgreSQL')
    with engine.begin() as connection:
        # Do not hang a shared service indefinitely if an old identity writer
        # or another migration is still running. Any failure rolls back the
        # entire additive migration; operators can quiesce writers and retry.
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(text("SET LOCAL statement_timeout = '5min'"))
        connection.execute(text('SELECT pg_advisory_xact_lock(734106422)'))
        # Blocks all ordinary identity writers until columns and triggers are
        # installed together, including a membership change in another process.
        connection.execute(text('LOCK TABLE public.users, public.memberships IN ACCESS EXCLUSIVE MODE'))
        installed = connection.scalar(text('SELECT to_regclass(:name)'), {'name': _MARKER}) is not None
        if installed:
            if not _integrity(connection):
                raise RuntimeError('Cloud API 2 authorization guard is missing or changed; manual recovery required')
            return
        for table, _, _, _ in _TABLES:
            existing = connection.scalar(text("""
                SELECT count(*) FROM pg_catalog.pg_attribute
                WHERE attrelid=pg_catalog.to_regclass(:relation)
                AND attname IN ('_ga_cloud2_auth_nonce','_ga_cloud2_auth_generation')
                AND attnum>0 AND NOT attisdropped
            """), {'relation': 'public.'+table})
            if existing:
                raise RuntimeError('Cloud API 2 authorization migration state is ambiguous; manual recovery required')
        for table, function, trigger, body in _TABLES:
            connection.execute(text(f'ALTER TABLE public.{table} ADD COLUMN _ga_cloud2_auth_nonce uuid NOT NULL DEFAULT pg_catalog.gen_random_uuid()'))
            connection.execute(text(f'ALTER TABLE public.{table} ADD COLUMN _ga_cloud2_auth_generation bigint NOT NULL DEFAULT 1'))
            connection.exec_driver_sql(f'CREATE FUNCTION public.{function}() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $cloud2${body}$cloud2$')
            connection.execute(text(f'CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{function}()'))
        connection.execute(text('CREATE TABLE public.ga_cloud2_auth_epoch_schema_v1 (version integer PRIMARY KEY CHECK (version=1))'))
        connection.execute(text('INSERT INTO public.ga_cloud2_auth_epoch_schema_v1 (version) VALUES (1)'))
        if not _integrity(connection):
            raise RuntimeError('Cloud API 2 authorization guard installation could not be verified')


def actor_identity(db, workspace, actor):
    """Lock live actor rows and return the unforgeable-by-ordinary-writer epochs."""
    connection = db.connection()
    if not _integrity(connection):
        raise HTTPException(503, 'Cloud API 2 权限保护未就绪，请联系系统管理员')
    row = db.execute(text("""
        SELECT u.id AS user_id, u._ga_cloud2_auth_nonce::text AS user_nonce,
               u._ga_cloud2_auth_generation AS user_generation,
               m.id AS membership_id, m._ga_cloud2_auth_nonce::text AS membership_nonce,
               m._ga_cloud2_auth_generation AS membership_generation
        FROM public.users u JOIN public.memberships m ON m.user_id=u.id
        WHERE u.id=:actor AND u.active IS TRUE AND m.workspace_id=:workspace AND m.role='admin'
        FOR SHARE OF u,m
    """), {'workspace': workspace, 'actor': actor}).mappings().first()
    # Both tables are now transaction-locked against DDL; verify again to
    # close the window between initial catalog inspection and row locking.
    if not _integrity(connection):
        raise HTTPException(503, 'Cloud API 2 权限保护未就绪，请联系系统管理员')
    if not row:
        raise HTTPException(403, '授权人账号或工作区管理员权限已失效')
    identity = dict(row)
    if (not all(isinstance(identity[k], str) and identity[k] for k in ('user_id', 'membership_id', 'user_nonce', 'membership_nonce'))
            or not all(type(identity[k]) is int and identity[k] >= 1 for k in ('user_generation','membership_generation'))):
        raise HTTPException(503, 'Cloud API 2 权限保护无法核对')
    return identity
