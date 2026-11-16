"""Independent unsupported-object and path-swap boundaries; pure fake DB only.

No DB/service/provider access. PostgreSQL classification itself needs the
separate owned-restore probe; these tests enforce ordering and refusal behavior.
"""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SPEC = importlib.util.spec_from_file_location('m60_independent_catalog_review',
    Path(__file__).parents[1] / 'scripts/m60_local_release_check.py')
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)
RUN = 'c' * 32
DATABASE = 'ga_test_m60_m59_restore_' + RUN
MARKER = 'm60-m59-release-owned:' + RUN
URL = 'postgresql+psycopg://fixture:private-fixture@127.0.0.1:55432/' + DATABASE
TAG = '/* m60_untracked_catalog_guard */'


@pytest.fixture(autouse=True)
def deny_real_database(monkeypatch):
    monkeypatch.setattr(m, 'create_engine', lambda *a, **kw: pytest.fail('real DB forbidden'))


def fake_snapshot(monkeypatch, *, unsupported=False, identity_ok=True, scalar_error=None):
    """Explicit scalar contract; raw table SELECT is observable and independent."""
    events = []
    class Result:
        def __init__(self, value): self.value = value
        def mappings(self): return self
        def one(self): return self.value
        def __iter__(self): return iter(self.value)
        def scalar_one(self):
            events.append('scalar_one')
            if scalar_error: raise scalar_error
            return self.value
    class Preparer:
        def quote(self, name): return '"' + name + '"'
    class Dialect: identifier_preparer = Preparer()
    class Connection:
        dialect = Dialect()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        @contextmanager
        def begin(self): events.append('begin'); yield
        def execute(self, query, params=None):
            sql = str(query)
            if sql == 'SET TRANSACTION READ ONLY':
                events.append('readonly'); return Result(None)
            if 'FROM pg_catalog.pg_database' in sql:
                events.append('identity')
                return Result({'database_name': DATABASE, 'oid': 17 if identity_ok else 18,
                    'marker': MARKER, 'transaction_read_only': 'on'})
            if TAG in sql:
                events.append('catalog'); events.append(('catalog_sql', sql))
                return Result(unsupported)
            if sql.startswith('/* m60_relation_properties */'):
                assert params == {'table_name': 'records'}
                return Result({'properties': {'owner': 'fixture', 'persistence': 'p',
                    'replica_identity': 'd', 'tablespace': 'pg_default', 'options': [],
                    'privileges': [], 'columns': []}})
            assert sql == 'SELECT * FROM public."records"', sql
            events.append('table_rows')
            return Result([{'id': 1}, {'id': 1}])
    class Engine:
        def connect(self): return Connection()
        def dispose(self): events.append('disposed')
    class Inspector:
        def get_schema_names(self): return ['public']
        def get_view_names(self, **kw): return []
        get_materialized_view_names = get_view_names
        get_sequence_names = get_view_names
        def get_table_names(self, **kw): events.append('table_names'); return ['records']
        def get_columns(self, *args, **kw):
            return [{'name': 'id', 'type': 'INTEGER', 'nullable': False}]
        def get_pk_constraint(self, *args, **kw): return {'constrained_columns': []}
        def get_foreign_keys(self, *args, **kw): return []
        get_unique_constraints = get_foreign_keys
        get_check_constraints = get_foreign_keys
        get_indexes = get_foreign_keys
    def inspect(connection):
        events.append('inspect'); return Inspector()
    monkeypatch.setattr(m, 'create_engine', lambda *a, **kw: Engine())
    monkeypatch.setattr(m, 'inspect', inspect)
    monkeypatch.setattr(m, 'file_inventory', lambda *a: {'original.bin': {'sha256': 'b' * 64, 'bytes': 2}})
    fixed = m.OwnedRestore(URL, RUN, 17, MARKER)
    return fixed, events


def test_plain_supported_tables_are_read_after_identity_and_one_false_catalog_guard(monkeypatch):
    fixed, events = fake_snapshot(monkeypatch)
    value = m.snapshot_owned_restore(fixed, 'unused-fake-storage')
    assert len(value['tables']['records']['row_hashes']) == 2
    assert value['tables']['records']['row_hashes'][0] == value['tables']['records']['row_hashes'][1]
    assert events.count('catalog') == events.count('scalar_one') == 1
    assert events.index('identity') < events.index('catalog') < events.index('inspect') < events.index('table_rows')
    assert events[-1] == 'disposed'
    assert m.public_summary(m.compare_inventories(value, value))['verified'] is True


# One true scalar covers the common refusal boundary. Actual object-family
# classification is intentionally delegated to the separately owned PG probe;
# duplicating this same boolean under many labels would not prove that SQL.
def test_untracked_catalog_object_refuses_before_inspector_or_rows(monkeypatch):
    fixed, events = fake_snapshot(monkeypatch, unsupported=True)
    with pytest.raises(m.CheckError) as caught:
        m.snapshot_owned_restore(fixed, 'unused-fake-storage')
    assert caught.value.code == m.Code.UNSUPPORTED
    assert events.count('catalog') == events.count('scalar_one') == 1
    assert 'inspect' not in events and 'table_names' not in events and 'table_rows' not in events
    assert events.index('identity') < events.index('catalog')
    assert events[-1] == 'disposed'


@pytest.mark.parametrize('value', [None, 0, 1, 'false', 'true', [], {}])
def test_catalog_probe_must_return_an_actual_bool_not_a_coerced_value(monkeypatch, value):
    fixed, events = fake_snapshot(monkeypatch, unsupported=value)
    with pytest.raises(m.CheckError) as caught:
        m.snapshot_owned_restore(fixed, 'unused-fake-storage')
    assert caught.value.code == m.Code.DATABASE
    assert 'inspect' not in events and 'table_rows' not in events
    assert events[-1] == 'disposed'


def test_unverified_identity_never_reads_catalog_or_table_metadata(monkeypatch):
    fixed, events = fake_snapshot(monkeypatch, unsupported=True, identity_ok=False)
    with pytest.raises(m.CheckError) as caught:
        m.snapshot_owned_restore(fixed, 'unused-fake-storage')
    assert caught.value.code == m.Code.IDENTITY
    assert 'catalog' not in events and 'inspect' not in events and 'table_rows' not in events
    assert events[-1] == 'disposed'


def test_catalog_error_is_redacted_and_fails_before_table_read(monkeypatch):
    secret = 'private-catalog-fixture-error'
    fixed, events = fake_snapshot(monkeypatch, scalar_error=RuntimeError(secret))
    with pytest.raises(m.CheckError) as caught:
        m.snapshot_owned_restore(fixed, 'unused-fake-storage')
    assert caught.value.code == m.Code.DATABASE
    assert secret not in str(caught.value) and secret not in repr(caught.value)
    assert 'inspect' not in events and 'table_rows' not in events and events[-1] == 'disposed'


def test_catalog_query_consults_the_omitted_object_families(monkeypatch):
    fixed, events = fake_snapshot(monkeypatch)
    m.snapshot_owned_restore(fixed, 'unused-fake-storage')
    queries = [event[1].lower() for event in events if isinstance(event, tuple) and event[0] == 'catalog_sql']
    assert len(queries) == 1
    sql = queries[0]
    for name in ['pg_proc', 'pg_trigger', 'tgisinternal', 'pg_policy', 'relrowsecurity',
                 'relforcerowsecurity', 'pg_inherits', 'pg_rewrite', 'pg_type', 'pg_event_trigger', 'pg_namespace']:
        assert name in sql, name
    assert 'pg_class' in sql or 'pg_foreign_table' in sql
    assert 'pg_class' in sql or 'pg_partitioned_table' in sql
    # Text references alone do not prove SQL classification; root separately
    # verifies real catalog examples in an explicitly owned disposable database.


def test_replacing_candidate_directory_with_same_manifest_cannot_validate_changed_current_code(tmp_path, monkeypatch):
    root = tmp_path.resolve(); candidate = root / 'candidate'; package = candidate / 'generic_agents'
    package.mkdir(parents=True)
    expected_code = b'value = 1\n'; changed_code = b'value = 9\n'
    (package / '__init__.py').write_bytes(expected_code)
    raw = json.dumps({'files': [{'path': 'generic_agents/__init__.py',
        'sha256': hashlib.sha256(expected_code).hexdigest(), 'bytes': len(expected_code)}]}).encode()
    (candidate / 'source-manifest.json').write_bytes(raw)
    fixed = m.ManifestContract('candidate', 'candidate/source-manifest.json', hashlib.sha256(raw).hexdigest())
    real_open = m._open_at; swapped = []
    def replace_after_directory_open(project_root, relative, *, directory=False):
        fd = real_open(project_root, relative, directory=directory)
        if directory and relative == 'candidate' and not swapped:
            candidate.rename(root / 'old-candidate')
            (candidate / 'generic_agents').mkdir(parents=True)
            (candidate / 'generic_agents/__init__.py').write_bytes(changed_code)
            (candidate / 'source-manifest.json').write_bytes(raw)
            swapped.append(True)
        return fd
    monkeypatch.setattr(m, '_open_at', replace_after_directory_open)
    with pytest.raises(m.CheckError) as caught:
        m.verify_manifest(root, fixed)
    assert caught.value.code == m.Code.SOURCE
    assert swapped and (candidate / 'generic_agents/__init__.py').read_bytes() == changed_code
