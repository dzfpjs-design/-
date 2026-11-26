"""Aggregate-only, disposable-PG migration research; never grants authority.

Disabled unless called explicitly. This candidate is not registered as an API,
does not read evidence files, and cannot be used against a real database.
Its counts are observations, not a completeness or migration-readiness proof.
"""
import os
import re
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.engine import make_url

from .same_goal_advice_prepare_writer_candidate_v2 import require_disposable_database

_UNKNOWN = ('writer_inventory', 'direct_write_paths', 'row_visibility',
            'call_binding_authority', 'shared_ledger_integrity', 'lock_order',
            'file_immutability', 'migration_readiness')


def _result(state, *, role_counts=None, records=None):
    return {'state': state, 'authorizes_migration': False, 'authorizes_send': False,
            'role_counts': role_counts, 'record_counts': records,
            'unproven': list(_UNKNOWN)}


def _dedicated_url(engine):
    """Reject non-test targets before opening any network connection."""
    try:
        actual = engine.url
        expected = make_url(os.environ.get('GA_TEST_DATABASE_URL', ''))
        path = Path(os.environ.get('GA_TEST_PG_DATA_DIR', '')).resolve()
        sid = os.environ.get('GA_TEST_PG_SYSTEM_IDENTIFIER', '')
        return (actual.drivername.startswith('postgresql') and actual.host == '127.0.0.1'
            and actual.port and actual.port != 55486 and actual.username in {'advice_test','counter_test'}
            and actual.password is None and bool(re.fullmatch(r'ga_test_[0-9a-f]{12}', actual.database or ''))
            and (expected.host, expected.port, expected.username, expected.password)
                == (actual.host, actual.port, actual.username, None)
            and path.name == 'data' and (path.parent.name.startswith('advice-race-pg-')
                or path.parent.name.startswith('v2-counter-isolated-')) and sid.isdecimal())
    except Exception:
        return False


def inspect_isolated(engine, role_names=(), *, enabled=False):
    """Run an explicit, read-only test-cluster snapshot; never return raw IDs.

    Unknown and failed checks never imply success. Callers may not use this as
    an authorization, task inventory, or substitute for privilege auditing.
    """
    if enabled is not True:
        return _result('disabled')
    if (not isinstance(role_names, (list, tuple)) or len(role_names) > 32
            or any(not isinstance(name, str) or not name or len(name) > 63 for name in role_names)
            or len(set(role_names)) != len(role_names)):
        return _result('unknown')
    if not _dedicated_url(engine):
        return _result('unknown')
    try:
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
            with conn.begin():
                conn.execute(text('SET TRANSACTION READ ONLY'))
                conn.execute(text("SET LOCAL statement_timeout = '10s'"))
                require_disposable_database(SimpleNamespace(get_bind=lambda:conn, execute=conn.execute))
                roles = conn.execute(text('''
                    WITH wanted AS (SELECT unnest(CAST(:names AS text[])) AS name),
                    targets AS (SELECT c.oid,c.relowner FROM pg_class c
                      JOIN pg_namespace n ON n.oid=c.relnamespace
                      WHERE n.nspname='public' AND c.relname=ANY(:tables)
                        AND c.relkind IN ('r','p')),
                    selected AS (SELECT w.name,r.oid,r.rolsuper,r.rolcreaterole,r.rolbypassrls
                      FROM wanted w LEFT JOIN pg_roles r ON r.rolname=w.name)
                    SELECT count(*), count(*) FILTER (WHERE oid IS NULL),
                      count(*) FILTER (WHERE oid IS NOT NULL AND EXISTS (SELECT 1 FROM pg_roles reachable
                        WHERE pg_has_role(selected.oid,reachable.oid,'MEMBER')
                          AND (reachable.rolsuper OR reachable.rolcreaterole OR reachable.rolbypassrls))),
                      count(*) FILTER (WHERE oid IS NOT NULL AND EXISTS (SELECT 1 FROM targets t
                        WHERE pg_has_role(selected.oid,t.relowner,'MEMBER'))),
                      count(*) FILTER (WHERE oid IS NOT NULL AND EXISTS (SELECT 1 FROM targets t CROSS JOIN pg_roles reachable
                        WHERE pg_has_role(selected.oid,reachable.oid,'MEMBER') AND
                          (has_table_privilege(reachable.oid,t.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')
                           OR has_any_column_privilege(reachable.oid,t.oid,'INSERT,UPDATE')))),
                      count(*) FILTER (WHERE oid IS NOT NULL AND EXISTS (SELECT 1 FROM pg_roles reachable
                        WHERE pg_has_role(selected.oid,reachable.oid,'MEMBER')
                          AND has_schema_privilege(reachable.oid,'public','CREATE'))),
                      (SELECT count(*) FROM targets)
                    FROM selected'''), {'names':list(role_names),
                        'tables':['tasks','plan_versions','jobs','audit_events','invocations',
                                  'cloud_model_calls','evidence_objects']}).one()
                if roles[6] != 7:
                    return _result('unknown')
                data = conn.execute(text('''
                    SELECT
                      (SELECT count(*) FROM plan_versions p JOIN tasks t ON t.id=p.task_id
                        WHERE p.workspace_id<>t.workspace_id),
                      (SELECT count(*) FROM audit_events a
                        LEFT JOIN tasks t ON t.id=a.data->>'task_id'
                        WHERE a.action LIKE 'same_goal_advice.%' AND a.data->>'task_id' IS NOT NULL
                          AND t.id IS NULL),
                      (SELECT count(*) FROM audit_events a JOIN tasks t ON t.id=a.data->>'task_id'
                        WHERE a.action LIKE 'same_goal_advice.%' AND a.workspace_id<>t.workspace_id),
                      (SELECT count(*) FROM cloud_model_calls c
                        WHERE c.data ? 'same_goal_advice_unlimited_v2'
                           OR c.data->'gateway_dispatch'->'identity'->>'family'='same_goal_advice_unlimited_v2'),
                      (SELECT count(*) FROM cloud_model_calls),
                      (SELECT count(*) FROM cloud_model_calls c
                        WHERE c.data ? 'same_goal_advice_unlimited_v2' AND (
                          NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id=c.data->'same_goal_advice_unlimited_v2'->>'task_id')
                          OR NOT EXISTS (SELECT 1 FROM plan_versions p WHERE p.id=c.data->'same_goal_advice_unlimited_v2'->>'plan_id')
                          OR NOT EXISTS (SELECT 1 FROM jobs j WHERE j.id=c.data->'same_goal_advice_unlimited_v2'->>'job_id')
                          OR NOT EXISTS (SELECT 1 FROM audit_events a WHERE a.id=c.data->'same_goal_advice_unlimited_v2'->>'audit_id'))),
                      (SELECT count(*) FROM cloud_model_calls c
                        WHERE c.data ? 'same_goal_advice_unlimited_v2' AND (
                          EXISTS (SELECT 1 FROM tasks t WHERE t.id=c.data->'same_goal_advice_unlimited_v2'->>'task_id' AND t.workspace_id<>c.workspace_id)
                          OR EXISTS (SELECT 1 FROM plan_versions p WHERE p.id=c.data->'same_goal_advice_unlimited_v2'->>'plan_id'
                            AND (p.workspace_id<>c.workspace_id OR p.task_id IS DISTINCT FROM c.data->'same_goal_advice_unlimited_v2'->>'task_id'))
                          OR EXISTS (SELECT 1 FROM jobs j WHERE j.id=c.data->'same_goal_advice_unlimited_v2'->>'job_id' AND j.workspace_id<>c.workspace_id)
                          OR EXISTS (SELECT 1 FROM audit_events a WHERE a.id=c.data->'same_goal_advice_unlimited_v2'->>'audit_id' AND a.workspace_id<>c.workspace_id)))
                    ''' )).one()
                # No authoritative production binding table exists yet. Both
                # recognisable and generic calls remain unproven, never labelled
                # bound/unbound solely because an experimental table is present.
                return _result('observed', role_counts={
                    'requested':roles[0], 'missing':roles[1], 'elevated':roles[2],
                    'owner_or_member':roles[3], 'table_or_column_write':roles[4],
                    'public_schema_create':roles[5]}, records={
                    'plan_cross_workspace_task':data[0], 'advice_audit_missing_task':data[1],
                    'advice_audit_cross_workspace_task':data[2],
                    'identifiable_v2_calls_without_authoritative_binding_proof':data[3],
                    'all_calls_without_authoritative_binding_proof':data[4],
                    'v2_calls_missing_declared_links':data[5],
                    'v2_calls_with_cross_workspace_declared_links':data[6]})
    except Exception:
        # Omit database errors, identifiers, URLs and values from the output.
        return _result('unknown')
