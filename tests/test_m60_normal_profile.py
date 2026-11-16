"""Canonical normal profile boundaries; no real application/PG connection."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location('m60_normal_profile_tests',
    Path(__file__).parents[1] / 'scripts/m60_local_release_check.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
SECRET = 'm60-private-fixture-secret'
URL = 'postgresql+psycopg://fixture:' + SECRET + '@127.0.0.1:55432/generic_agents'


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / '.runtime/evidence').mkdir(parents=True)
    (root / '.env').write_text('GA_DATABASE_URL=' + URL + '\nGA_STORAGE_DIR=.runtime/evidence\n')
    for key in list(os.environ):
        if key.startswith('PG') or key in {'GA_DATABASE_URL', 'GA_STORAGE_DIR'}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(m, 'create_engine', lambda *a, **kw: pytest.fail('real DB forbidden'))
    return root


def refuses(code, fn):
    with pytest.raises(m.CheckError) as error:
        fn()
    assert error.value.code is code
    assert SECRET not in str(error.value) and SECRET not in repr(error.value)


def test_profile_reads_canonical_bytes_without_connection_and_hides_secrets(project):
    profile = m.load_normal_profile(project, {})
    assert profile.database_url == URL
    assert profile.storage == project / '.runtime/evidence'
    assert profile.project_root == project
    assert SECRET not in repr(profile) and URL not in repr(profile)
    assert len(profile.config_sha256) == 64


@pytest.mark.parametrize('url', [
    URL.replace('127.0.0.1', 'localhost'), URL.replace('127.0.0.1', '203.0.113.10'),
    URL.replace('55432', '5432'), URL.replace('generic_agents', 'postgres'),
    URL.replace('generic_agents', 'ga_test_other'), URL + '?hostaddr=203.0.113.10',
    URL.replace('fixture:' + SECRET, 'fixture'), '${UNTRUSTED_DATABASE}',
])
def test_bad_or_interpolated_profile_is_rejected_before_connection(project, monkeypatch, url):
    monkeypatch.setenv('UNTRUSTED_DATABASE', URL)
    (project / '.env').write_text('GA_DATABASE_URL=' + url + '\n')
    refuses(m.Code.CONTRACT, lambda: m.load_normal_profile(project, {}))


@pytest.mark.parametrize('env', [
    {'GA_DATABASE_URL': URL.replace('generic_agents', 'ga_test_other')},
    {'GA_STORAGE_DIR': '.runtime/different'}, {'PGHOSTADDR': '203.0.113.10'},
    {'PGHOST': '203.0.113.10'}, {'PGPORT': '5432'}, {'PGDATABASE': 'postgres'},
    {'PGSERVICE': SECRET}, {'PGSERVICEFILE': '/private/fixture'},
    {'PGOPTIONS': '-c search_path=other'}, {'PGPASSWORD': SECRET},
    {'PGPASSFILE': '/private/fixture'},
])
def test_inherited_overrides_cannot_choose_normal_target(project, env):
    refuses(m.Code.CONTRACT, lambda: m.load_normal_profile(project, env))


def test_equivalent_canonical_overrides_and_empty_pg_values_are_allowed(project):
    profile = m.load_normal_profile(project, {'GA_DATABASE_URL': URL,
        'GA_STORAGE_DIR': str(project / '.runtime/evidence'), 'PGHOSTADDR': ''})
    assert profile.database_url == URL


@pytest.mark.parametrize('mutation', ['env_symlink', 'storage_symlink', 'storage_outside', 'storage_parent'])
def test_escaping_or_symlink_configuration_is_rejected(project, mutation):
    if mutation == 'env_symlink':
        (project / '.env').rename(project / 'config.saved')
        (project / '.env').symlink_to('config.saved')
    elif mutation == 'storage_symlink':
        (project / '.runtime/evidence').rename(project / '.runtime/other')
        (project / '.runtime/evidence').symlink_to('other', target_is_directory=True)
    else:
        value = str(project.parent) if mutation == 'storage_outside' else '../evidence'
        (project / '.env').write_text('GA_DATABASE_URL=' + URL + '\nGA_STORAGE_DIR=' + value + '\n')
    refuses(m.Code.CONTRACT, lambda: m.load_normal_profile(project, {}))


@pytest.mark.parametrize('oid', [True, '17', 0, -1, None])
def test_missing_or_invalid_preobserved_oid_cannot_open_database(project, oid):
    profile = m.load_normal_profile(project, {})
    refuses(m.Code.CONTRACT, lambda: m.snapshot_normal(profile, expected_oid=oid))


def test_arbitrary_url_and_mutated_profile_cannot_open_database(project):
    refuses(m.Code.CONTRACT, lambda: m.snapshot_normal(URL, expected_oid=17))
    profile = m.load_normal_profile(project, {})
    (project / '.env').write_text('GA_DATABASE_URL=' + URL.replace(SECRET, 'changed') + '\n')
    refuses(m.Code.CONTRACT, lambda: m.snapshot_normal(profile, expected_oid=17))


def test_pg_global_changed_after_profile_load_is_rejected_before_connection(project, monkeypatch):
    profile = m.load_normal_profile(project, {})
    monkeypatch.setenv('PGSERVICE', SECRET)
    refuses(m.Code.CONTRACT, lambda: m.snapshot_normal(profile, expected_oid=17))


def fake_database(monkeypatch, *, observed=None, after_collect=None, error=None):
    events = []
    class Result:
        def __init__(self, value): self.value = value
        def mappings(self): return self
        def one(self): return self.value
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        @contextmanager
        def begin(self): events.append('transaction'); yield
        def execute(self, query):
            if error: raise error
            sql = str(query)
            if sql == 'SET TRANSACTION READ ONLY':
                events.append('readonly'); return Result(None)
            assert 'FROM pg_catalog.pg_database' in sql
            events.append('identity')
            return Result(observed or {'database_name': 'generic_agents', 'oid': 17,
                                      'transaction_read_only': 'on'})
    class Engine:
        def connect(self): events.append('connect'); return Connection()
        def dispose(self): events.append('disposed')
    def create(url, **kwargs):
        assert url == URL
        assert kwargs['isolation_level'] == 'REPEATABLE READ'
        assert kwargs['connect_args'] == {'hostaddr': '127.0.0.1',
            'options': '-c default_transaction_read_only=on', 'connect_timeout': 5}
        events.append('engine'); return Engine()
    def collect(connection, files):
        events.append('collect')
        if after_collect: after_collect()
        return m.make_inventory({'records': [{'id': 1}, {'id': 1}]}, {'records': {}}, files)
    monkeypatch.setattr(m, 'create_engine', create)
    monkeypatch.setattr(m, '_collect_inventory', collect)
    return events


def test_normal_snapshot_is_readonly_identity_first_and_preserves_repeated_rows(project, monkeypatch):
    profile = m.load_normal_profile(project, {})
    events = fake_database(monkeypatch)
    result = m.snapshot_normal(profile, expected_oid=17)
    assert events == ['engine', 'connect', 'transaction', 'readonly', 'identity', 'collect', 'disposed']
    hashes = result['tables']['records']['row_hashes']
    assert len(hashes) == 2 and hashes[0] == hashes[1]


@pytest.mark.parametrize('change', [{'oid': 18}, {'database_name': 'other'}, {'transaction_read_only': 'off'}])
def test_wrong_normal_identity_cannot_read_catalog_or_rows(project, monkeypatch, change):
    profile = m.load_normal_profile(project, {})
    observed = {'database_name': 'generic_agents', 'oid': 17, 'transaction_read_only': 'on', **change}
    events = fake_database(monkeypatch, observed=observed)
    refuses(m.Code.IDENTITY, lambda: m.snapshot_normal(profile, expected_oid=17))
    assert 'collect' not in events and events[-1] == 'disposed'


@pytest.mark.parametrize('mutation', ['config', 'files'])
def test_changes_during_snapshot_cannot_be_reported_as_stable(project, monkeypatch, mutation):
    profile = m.load_normal_profile(project, {})
    def change():
        if mutation == 'config': (project / '.env').write_text('GA_DATABASE_URL=' + URL.replace(SECRET, 'changed') + '\n')
        else: (profile.storage / 'changed.json').write_text('changed')
    events = fake_database(monkeypatch, after_collect=change)
    refuses(m.Code.CONTRACT if mutation == 'config' else m.Code.FILES,
            lambda: m.snapshot_normal(profile, expected_oid=17))
    assert events[-1] == 'disposed'


def test_normal_database_exception_cannot_leak_url_or_password(project, monkeypatch):
    profile = m.load_normal_profile(project, {})
    events = fake_database(monkeypatch, error=RuntimeError(URL))
    refuses(m.Code.DATABASE, lambda: m.snapshot_normal(profile, expected_oid=17))
    assert events[-1] == 'disposed'
