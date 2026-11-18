"""Narrow, opt-in installation; never changes existing tables or functions."""
from sqlalchemy import inspect, text
from .report_full_provisional_models import FullProvisionalCandidate
from .typed_payload_migration_v131 import _TRIGGER_FUNCTION


def install(engine):
    """Install only the new table and its guards atomically, or fail closed.

    Existing tables are not adopted without separate provenance verification.
    The shared trigger function must already be the exact expected version.
    """
    table = FullProvisionalCandidate.__table__
    expected = _TRIGGER_FUNCTION.split('$guard$')[1]
    with engine.begin() as connection:
        connection.execute(text('SET LOCAL search_path = public, pg_catalog'))
        connection.execute(text('SELECT pg_advisory_xact_lock(734106131)'))
        actual = connection.execute(text("""
            SELECT p.prosrc, l.lanname, p.prosecdef, p.proconfig
              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
              JOIN pg_language l ON l.oid=p.prolang
             WHERE n.nspname='public' AND p.proname='ga_typed_payload_no_downgrade_v131'
               AND p.pronargs=0 AND p.prorettype='pg_catalog.trigger'::regtype
        """)).all()
        if len(actual) != 1 or actual[0] != (expected, 'plpgsql', False, None):
            raise RuntimeError('existing typed payload guard is absent or differs; installation stopped')
        if inspect(connection).has_table(table.name, schema='public'):
            raise RuntimeError('candidate table already exists; verify before adopting it')
        table.create(connection, checkfirst=False)
        qualified = connection.dialect.identifier_preparer.format_table(table)
        connection.execute(text(f'CREATE TRIGGER tr_ga_typed_payload_v131 BEFORE INSERT OR UPDATE ON {qualified} '
            'FOR EACH ROW EXECUTE FUNCTION public.ga_typed_payload_no_downgrade_v131()'))
        connection.execute(text(f"CREATE TRIGGER tr_ga_typed_payload_data_v131 BEFORE UPDATE OF data ON {qualified} "
            "FOR EACH ROW EXECUTE FUNCTION public.ga_typed_payload_no_downgrade_v131('data-write')"))
