"""Private temporary files and fake SQL adapters only: no database or services."""
from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('prepare_instance_under_test', Path(__file__).parents[1] / 'scripts/prepare_instance.py')
install = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(install)
runtime = install.runner_module()
ENCODED = '$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNo'


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw); path.chmod(0o600)


@pytest.fixture
def example(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in tuple(os.environ):
        if key.upper().startswith('PG'):
            monkeypatch.delenv(key)
    root = tmp_path / 'project'; release = root / 'release-candidates/r1'
    files = {'src/generic_agents/' + x + '.py': b'# verified ' + x.encode() for x in ('__init__', 'cli', 'api', 'models', 'bootstrap')}
    files.update({'static/setup/index.html': b'<script src="/setup/assets/app.js"></script>', 'static/setup/assets/app.js': b'//asset'})
    for name, data in files.items(): put(release / name, data)
    manifest = {'format': 'generic-agents-candidate-v1', 'release_id': 'r1', 'backend_src': 'src', 'static_dist': 'static/setup',
        'setup_worker_scope': 'workspace', 'files': [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in files.items()],
        'schema_model_sha256': hashlib.sha256(files['src/generic_agents/models.py']).hexdigest()}
    path = release / 'source-manifest.json'; put(path, json.dumps(manifest).encode())
    runner = SimpleNamespace(**{key: getattr(runtime, key) for key in ('BLOCKED_PORTS', 'file_record', 'private_directory', 'read_json', 'validate_local_runtime_root')})
    runner.__file__ = runtime.__file__
    runner.load_release = lambda path, sha, **kw: runtime.load_release(path, sha, root=root)
    runner.load_instance = lambda path: runtime.load_instance(path, root=root)
    c = {'manifest': str(path), 'manifest_sha256': runtime.file_record(path)['sha256'], 'instance_dir': str(tmp_path / 'instance'),
        'python': sys.executable, 'pg_port': 65127, 'pg_admin_user': 'postgres', 'api_port': 18081,
        'redis_url': 'redis://127.0.0.1:6379/0', 'username': 'operator', 'workspace_name': 'My workspace'}
    # Keep actual connections/processes forbidden even if a test adapter is wired incorrectly.
    import psycopg
    import sqlalchemy
    monkeypatch.setattr(psycopg, 'connect', lambda *a, **k: pytest.fail('real SQL forbidden'))
    monkeypatch.setattr(sqlalchemy, 'create_engine', lambda *a, **k: pytest.fail('real engine forbidden'))
    return c, runner, root


class Resources:
    def __init__(self):
        self.parts = {}; self.writes = []; self.sysid = '765432123456'; self.fail = None
        self.schema_hash = '1' * 64; self.verified_configs = []

    def factory(self, c, admin_password, product, runner, *, checkpoint=None):
        assert admin_password == 'private-admin-password'
        resource = self
        class Gateway:
            def cluster(self, s=None):
                if s: install.need(s['system_identifier'] == resource.sysid, 'cluster_system_identifier_changed')
                return resource.sysid

            def apply(self, step, s, allow_create):
                if step not in resource.parts:
                    install.need(allow_create, 'owned_resource_missing')
                    resource.parts[step] = s['database_id']; resource.writes.append(step)
                    if step == 'database' and checkpoint: checkpoint('database_created', s)
                install.need(resource.parts[step] == s['database_id'], 'foreign_resource')
                if resource.fail == step: raise RuntimeError('SQL password=never-print-this')
                if step == 'database': s['oid'] = 16385
                if step == 'schema':
                    install.need(s['schema_sha256'] in {None, resource.schema_hash}, 'schema_snapshot_changed')
                    s['schema_sha256'] = resource.schema_hash
                if step == 'verified': resource.verified_configs.append(install.instance_configuration(s))
        for step in install.STEPS:
            setattr(Gateway, 'ensure_' + step, lambda self, s, *, allow_create, step=step: self.apply(step, s, allow_create))
        return Gateway()


def run(example, resources, **kwargs):
    c, runner, root = example
    return install.prepare(c, admin_password='private-admin-password', platform_password='private-platform-password',
        runner=runner, product_loader=lambda *a: {'security': SimpleNamespace(password_hash=lambda value: ENCODED)},
        postgres_factory=resources.factory, **kwargs)


def read_state(example):
    return json.loads((Path(example[0]['instance_dir']) / 'prepare-state.json').read_bytes())


def test_prepare_publishes_compatible_config_only_after_all_steps(example):
    resources = Resources(); path = run(example, resources); c, runner, _ = example
    state = read_state(example); config, _ = runner.load_instance(path)
    assert state['completed'] == list(install.STEPS) and state['phase'] == 'ready'
    assert resources.writes == list(install.STEPS) and len(resources.verified_configs) == 1
    assert config['workspace_id'] == state['workspace_id']
    assert config['owned_database'] == {'system_identifier': resources.sysid, 'oid': 16385, 'marker': state['marker']}
    assert config['environment']['GA_DATABASE_URL'].startswith('postgresql+psycopg://' + state['app'] + ':')
    assert 'private-admin-password' not in json.dumps(state) + path.read_text()
    assert 'private-platform-password' not in json.dumps(state) + path.read_text()
    for p in path.parent.iterdir():
        assert not p.is_symlink() and p.stat().st_nlink == 1 if p.is_file() else p.is_dir()
        assert stat.S_IMODE(p.stat().st_mode) == (0o700 if p.is_dir() else 0o600)


def test_unknown_database_commit_resumes_without_recreating_or_changing_secrets(example):
    resources = Resources()
    def interrupt(label, state):
        assert label == 'database_created' and state['phase'] == 'database_pending'
        raise RuntimeError('lost response with secret text')
    with pytest.raises(install.Rejected, match='installation_step_failed'): run(example, resources, checkpoint=interrupt)
    state = read_state(example); home = Path(example[0]['instance_dir'])
    assert state['completed'] == ['roles'] and state['phase'] == 'database_pending' and state['oid'] is None
    assert not (home / 'instance.json').exists()
    run(example, resources, resume=True)
    final = read_state(example)
    for field in ('database_id', 'app_password', 'password_hash', 'user_id', 'workspace_id'):
        assert final[field] == state[field]
    assert resources.writes.count('database') == 1


@pytest.mark.parametrize('step', install.STEPS)
def test_failure_never_publishes_and_resume_checks_live_resources(example, step):
    resources = Resources(); resources.fail = step
    with pytest.raises(install.Rejected, match='installation_step_failed'): run(example, resources)
    home = Path(example[0]['instance_dir']); before = read_state(example)
    assert not (home / 'instance.json').exists() and before['phase'] == step + '_pending'
    assert before['error_code'] == 'installation_step_failed'
    assert 'never-print-this' not in (home / 'prepare-state.json').read_text()
    resources.fail = None; run(example, resources, resume=True)
    assert resources.writes == list(install.STEPS)


def test_ready_resume_is_readonly_and_does_not_replace_configuration(example):
    resources = Resources(); path = run(example, resources); before = runtime.file_record(path); inode = path.stat().st_ino
    run(example, resources, resume=True)
    assert runtime.file_record(path) == before and path.stat().st_ino == inode
    assert resources.writes == list(install.STEPS)


def test_failed_configuration_validation_never_publishes_instance(example):
    c, runner, _ = example
    runner.load_instance = lambda path: install.need(False, 'final_configuration_rejected')
    with pytest.raises(install.Rejected, match='final_configuration_rejected'): run(example, Resources())
    assert not (Path(c['instance_dir']) / 'instance.json').exists()
    assert read_state(example)['phase'] == 'publish_pending'


@pytest.mark.parametrize('mutation', ['sysid', 'role', 'schema', 'source', 'inputs', 'config'])
def test_resume_rejects_drift_without_repair(example, mutation):
    resources = Resources(); path = run(example, resources); count = len(resources.writes)
    if mutation == 'sysid': resources.sysid = '9999'
    elif mutation == 'role': resources.parts['roles'] = 'f' * 32
    elif mutation == 'schema': resources.schema_hash = '3' * 64
    elif mutation == 'source':
        state = read_state(example); state['source_pins']['bootstrap']['sha256'] = '0' * 64
        put(path.parent / 'prepare-state.json', json.dumps(state).encode())
    elif mutation == 'inputs': example[0]['api_port'] = 19000
    else:
        config = json.loads(path.read_bytes()); config['workspace_id'] = str(install.uuid4()); put(path, json.dumps(config).encode())
    with pytest.raises(install.Rejected): run(example, resources, resume=True)
    assert len(resources.writes) == count


@pytest.mark.parametrize('field,value', [('pg_port', 55432), ('pg_port', 50228), ('api_port', 8765), ('api_port', 63077),
    ('pg_port', True), ('pg_admin_user', 'admin;DROP ROLE x'), ('python', '/usr/bin/python3'),
    ('redis_url', 'redis://secret:password@127.0.0.1:6379/0'), ('redis_url', 'redis://localhost:6379/0'),
    ('username', ' operator'), ('workspace_name', '')])
def test_invalid_inputs_reject_before_any_sql_or_private_directory(example, field, value):
    c, runner, root = example; c[field] = value
    with pytest.raises((install.Rejected, runtime.Rejected)):
        install.validate_inputs(c, runner, root=root)
    assert not Path(c['instance_dir']).exists()


def test_protected_directory_dotenv_and_inherited_libpq_rejected(example, monkeypatch):
    c, runner, root = example; original = c['instance_dir']
    c['instance_dir'] = str(root / '.runtime/new')
    (root / '.runtime').mkdir()
    with pytest.raises(install.Rejected, match='protected'): install.validate_inputs(c, runner, root=root)
    c['instance_dir'] = original
    monkeypatch.setenv('PGPASSWORD', 'not-read')
    with pytest.raises(install.Rejected, match='libpq'): install.validate_inputs(c, runner, root=root)
    monkeypatch.delenv('PGPASSWORD'); put(Path.cwd() / '.env', b'not read')
    with pytest.raises(install.Rejected, match='dotenv'): install.validate_inputs(c, runner, root=root)


def test_lock_and_private_state_reject_alias_or_concurrent_installer(example):
    c, runner, _ = example; home = Path(c['instance_dir'])
    with install.Journal(home, runner, resume=False):
        with pytest.raises(install.Rejected, match='lock'):
            with install.Journal(home, runner, resume=True): pass
    (home / 'prepare-state.json').symlink_to(home / 'prepare.lock')
    with pytest.raises(runtime.Rejected): runner.read_json(home / 'prepare-state.json', private=True)


class Result:
    def __init__(self, value): self.value = value
    def fetchone(self): return self.value
    def fetchall(self): return self.value


def fake_pg(c, runner):
    pg = object.__new__(install.Postgres); pg.c = c; pg.runner = runner
    pg._admin_password = 'must-remain-in-memory'
    return pg


def test_connection_is_fixed_libpq_readonly_and_rolls_back_on_failure(example):
    c, runner, _ = example; pg = fake_pg(c, runner); calls = []; actions = []
    class Connection:
        def execute(self, sql): actions.append(sql)
        def rollback(self): actions.append('rollback')
        def close(self): actions.append('close')
    pg.connect = lambda **kw: (calls.append(kw) or Connection())
    with pytest.raises(RuntimeError):
        with pg.connection(): raise RuntimeError('failure')
    assert actions == ['SET TRANSACTION READ ONLY', 'rollback', 'close']
    assert calls[0]['dbname'] == 'postgres' and calls[0]['hostaddr'] == '127.0.0.1'
    assert calls[0]['passfile'] == '/dev/null' and calls[0]['connect_timeout'] == 2 and calls[0]['sslmode'] == 'disable'
    assert calls[0]['password'] == 'must-remain-in-memory' and '5000' in calls[0]['options']


def test_connection_closes_even_if_rollback_fails(example):
    c, runner, _ = example; pg = fake_pg(c, runner); calls = []
    def rollback(): calls.append('rollback'); raise OSError('connection already lost')
    pg.connect = lambda **kw: SimpleNamespace(execute=lambda *a: None, rollback=rollback, close=lambda: calls.append('close'))
    with pytest.raises(OSError):
        with pg.connection(): pass
    assert calls == ['rollback', 'close']


@pytest.mark.parametrize('change', ['endpoint', 'version', 'superuser', 'other_database', 'sysid'])
def test_cluster_preflight_actual_metadata_rejections(example, change):
    c, runner, _ = example; pg = fake_pg(c, runner)
    row = ['postgres', '127.0.0.1', c['pg_port'], 170005, c['pg_admin_user'], True, '12345']
    names = [('postgres',), ('template0',), ('template1',)]
    if change == 'endpoint': row[1] = '127.0.0.2'
    elif change == 'version': row[3] = 180001
    elif change == 'superuser': row[5] = False
    elif change == 'other_database': names.append(('old_app',))
    @contextmanager
    def connect(*a, **k):
        yield SimpleNamespace(execute=lambda sql, *args: Result(names if 'datname FROM' in sql else tuple(row)))
    pg.connection = connect
    state = {'database': 'ga_candidate_' + 'a' * 32, 'system_identifier': '9999' if change == 'sysid' else '12345'}
    with pytest.raises(install.Rejected): pg.cluster(state if change == 'sysid' else None)


@pytest.mark.parametrize('change', ['member', 'superuser', 'login', 'comment', 'missing_one'])
def test_owned_roles_are_not_adopted_or_repaired(example, change):
    c, runner, _ = example; pg = fake_pg(c, runner)
    s = install.new_state(c, '12345', ENCODED, {})
    rows = [[s['owner'], False, False, False, False, False, False, False, install.role_comment(s, 'owner'), False],
        [s['app'], True, False, False, False, False, False, False, install.role_comment(s, 'app'), False]]
    if change == 'missing_one': rows.pop()
    else: rows[1][{'member': 9, 'superuser': 3, 'login': 1, 'comment': 8}[change]] = {'member': True, 'superuser': True, 'login': False, 'comment': 'foreign'}[change]
    pg.cluster = lambda s: None
    @contextmanager
    def connect(*a, **k): yield SimpleNamespace(execute=lambda *a: Result(rows))
    pg.connection = connect
    with pytest.raises(install.Rejected, match='owned_role'): pg.ensure_roles(s, allow_create=True)


def db_adapter(example, monkeypatch, *, marker=None):
    c, runner, _ = example; pg = fake_pg(c, runner); s = install.new_state(c, '12345', ENCODED, {})
    s['phase'] = 'database_pending'; s['completed'] = ['roles']; events = []
    row = [16384, s['owner'], marker, True]
    pg.cluster = lambda s: events.append('cluster_verified')
    pg.database_row = lambda s: tuple(row)
    pg.ensure_roles = lambda s, **k: events.append('roles_verified')
    pg.empty_unmarked_database = lambda s: events.append('empty_exclusive_verified')
    pg.checkpoint = lambda *a: None
    class Cursor:
        def __init__(self, db): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, query, params):
            events.append('marker_written'); assert query.as_string().startswith('COMMENT ON DATABASE "ga_candidate_')
            row[2] = params[0]
    import psycopg
    monkeypatch.setattr(psycopg, 'ClientCursor', Cursor)
    @contextmanager
    def connect(*a, **k): yield object()
    pg.connection = connect
    return pg, s, row, events


def test_database_pending_marker_gap_has_narrow_proof_before_comment(example, monkeypatch):
    pg, s, row, events = db_adapter(example, monkeypatch)
    pg.ensure_database(s, allow_create=True)
    assert events == ['cluster_verified', 'roles_verified', 'empty_exclusive_verified', 'marker_written']
    assert s['oid'] == 16384 and row[2] == s['marker']


@pytest.mark.parametrize('change', ['phase', 'no_intent', 'owner', 'oid', 'foreign_marker', 'not_empty'])
def test_unmarked_or_foreign_database_failclosed_without_comment(example, monkeypatch, change):
    pg, s, row, events = db_adapter(example, monkeypatch)
    if change == 'phase': s['phase'] = 'schema_pending'
    elif change == 'owner': row[1] = 'other_owner'
    elif change == 'oid': s['oid'] = 123
    elif change == 'foreign_marker': row[2] = 'another-run'
    elif change == 'not_empty': pg.empty_unmarked_database = lambda s: install.need(False, 'unmarked_database_not_empty_or_exclusive')
    with pytest.raises(install.Rejected): pg.ensure_database(s, allow_create=change != 'no_intent')
    assert 'marker_written' not in events


def schema_fixture():
    from sqlalchemy import Column, ForeignKey, Index, Integer, MetaData, String, Table, UniqueConstraint
    from sqlalchemy.dialects.postgresql import dialect
    metadata = MetaData()
    Table('base', metadata, Column('id', String(36), primary_key=True), Column('value', Integer, nullable=False))
    Table('linked', metadata, Column('id', String(36), primary_key=True), Column('base_id', String(36), ForeignKey('base.id')),
        UniqueConstraint('base_id'), Index('ix_linked_base', 'base_id'))
    for i in range(97): Table('extra_' + str(i), metadata, Column('id', String(36), primary_key=True))
    actual = {}
    from sqlalchemy import UniqueConstraint
    for name, table in metadata.tables.items():
        actual[name] = {'columns': [{'name': c.name, 'type': str(c.type.compile(dialect=dialect())), 'nullable': c.nullable,
            'default': None, 'identity': None, 'computed': None} for c in table.columns],
            'pk': {'name': name + '_pkey', 'constrained_columns': [c.name for c in table.primary_key.columns]},
            'fk': [{'name': name + '_fkey', 'constrained_columns': [c.name for c in f.columns], 'referred_schema': 'public',
                'referred_table': f.referred_table.name, 'referred_columns': [e.column.name for e in f.elements], 'options': {}}
                for f in table.foreign_key_constraints],
            'unique': [{'name': name + '_key', 'column_names': [c.name for c in u.columns]} for u in table.constraints if isinstance(u, UniqueConstraint)],
            'indexes': [{'name': x.name, 'column_names': [c.name for c in x.columns], 'unique': x.unique} for x in table.indexes], 'checks': []}
    return actual, metadata, dialect()


def test_full_99_table_schema_contract_passes_without_sql():
    actual, metadata, dialect = schema_fixture(); install.validate_schema(actual, metadata, dialect)


def test_schema_contract_follows_selected_metadata_instead_of_fixed_table_count():
    actual, metadata, dialect = schema_fixture()
    del actual['extra_96']; metadata.remove(metadata.tables['extra_96'])
    install.validate_schema(actual, metadata, dialect)


def test_schema_ddl_is_one_owner_transaction_not_shared_application_engine(example):
    c, runner, _ = example; pg = fake_pg(c, runner); s = install.new_state(c, '12345', ENCODED, {})
    actions = []; snapshots = iter([{}, {'tables': {'a': {}}, 'constraints': [], 'indexes': []}])
    pg.ensure_database = lambda *a, **kw: None
    pg.schema = lambda *a: next(snapshots)
    pg.statement = lambda db, statement, *ids: actions.append((statement, ids))
    pg.product = {'models': SimpleNamespace(Base=SimpleNamespace(metadata=SimpleNamespace(create_all=lambda db: actions.append('create_all_same_connection'))))}
    @contextmanager
    def begin():
        actions.append('begin')
        yield SimpleNamespace(exec_driver_sql=lambda sql: actions.append(sql))
        actions.append('commit')
    @contextmanager
    def engine(*a): yield SimpleNamespace(begin=begin)
    pg.engine = engine; pg.ensure_schema(s, allow_create=True)
    assert actions == ['begin', ('ALTER SCHEMA public OWNER TO {}', (s['owner'],)), ('SET ROLE {}', (s['owner'],)),
        'SET LOCAL search_path=public,pg_catalog', 'create_all_same_connection', 'ALTER TABLE observations ALTER COLUMN observed_at DROP NOT NULL',
        'ALTER TABLE observations ALTER COLUMN business_day DROP NOT NULL', 'RESET ROLE', 'commit']
    assert s['schema_sha256'] is not None


@pytest.mark.parametrize('change', ['extra_table', 'missing_table', 'column', 'nullable', 'type', 'default', 'pk', 'fk', 'unique', 'index', 'check'])
def test_schema_drift_rejected_instead_of_create_all_repair(change):
    actual, metadata, dialect = schema_fixture()
    if change == 'extra_table': actual['foreign'] = copy.deepcopy(actual['base'])
    elif change == 'missing_table': del actual['base']
    elif change == 'column': actual['base']['columns'].pop()
    elif change in {'nullable', 'type', 'default'}: actual['base']['columns'][1][change] = {'nullable': True, 'type': 'TEXT', 'default': '42'}[change]
    elif change == 'pk': actual['base']['pk']['constrained_columns'] = []
    elif change == 'fk': actual['linked']['fk'][0]['referred_table'] = 'extra_1'
    elif change == 'unique': actual['linked']['unique'] = []
    elif change == 'index': actual['linked']['indexes'][0]['unique'] = True
    else: actual['base']['checks'] = [{'sqltext': 'value>0'}]
    with pytest.raises(install.Rejected): install.validate_schema(actual, metadata, dialect)


@pytest.mark.parametrize('change', ['password', 'membership', 'active', 'duplicate'])
def test_lost_bootstrap_commit_requires_exact_id_hash_and_relationship(example, change):
    c, runner, _ = example; pg = fake_pg(c, runner); s = install.new_state(c, '12345', ENCODED, {})
    users = [(s['user_id'], c['username'], ENCODED, True)]; spaces = [(s['workspace_id'], c['workspace_name'])]
    memberships = [(s['user_id'], s['workspace_id'], 'admin')]
    pg.check_bootstrap(s, (users, spaces, memberships), exclusive=True)
    if change == 'password': users[0] = (*users[0][:2], 'other-hash', True)
    elif change == 'active': users[0] = (*users[0][:3], False)
    elif change == 'membership': memberships[0] = (*memberships[0][:2], 'viewer')
    else: memberships *= 2
    with pytest.raises(install.Rejected): pg.check_bootstrap(s, (users, spaces, memberships), exclusive=True)


def test_cli_never_echoes_driver_or_prompted_password(example, monkeypatch, capsys):
    c, runner, _ = example
    monkeypatch.setattr(install, 'runner_module', lambda: runner)
    monkeypatch.setattr(install, 'validate_inputs', lambda *a: None)
    monkeypatch.setattr(install.getpass, 'getpass', lambda *a: 'very-private-password')
    def fail(*a, **kw): raise RuntimeError('SQL postgres://secret:very-private-password')
    monkeypatch.setattr(install, 'prepare', fail)
    argv = [part for k, value in c.items() for part in ('--' + k.replace('_', '-'), str(value))]
    assert install.main(argv) == 1
    output = capsys.readouterr()
    assert not output.out and 'very-private' not in output.err and 'postgres://' not in output.err
    assert '未启动服务' in output.err and '私有进度/配置保留' in output.err


def test_foreign_loaded_product_is_rejected_before_import(example, monkeypatch):
    c, runner, _ = example; release = runner.load_release(Path(c['manifest']), c['manifest_sha256'])
    monkeypatch.setitem(sys.modules, 'generic_agents', SimpleNamespace(__file__='/foreign/generic_agents/__init__.py'))
    with pytest.raises(install.Rejected, match='foreign_product_module'): install.load_product(release, runner)


def test_optional_local_root_is_journalled_published_and_resume_bound(example):
    c, runner, _ = example
    components = Path(c['instance_dir']).parent / 'components'; components.mkdir(mode=0o700)
    c['local_runtime_root'] = str(components)
    resources = Resources(); path = run(example, resources)
    state = read_state(example); published, release = runner.load_instance(path)
    assert state['inputs']['local_runtime_root'] == str(components)
    assert published['environment']['GA_LOCAL_RUNTIME_ROOT'] == str(components)
    run(example, resources, resume=True)
    assert resources.writes == list(install.STEPS) and not tuple(components.iterdir())
    c['local_runtime_root'] = str(components / 'another')
    with pytest.raises(install.Rejected, match='resume_inputs_changed'): run(example, resources, resume=True)


@pytest.mark.parametrize('selected', ['relative', 'release', 'alias', 'writable'])
def test_invalid_local_root_stops_prepare_before_journal_or_sql(example, selected):
    c, runner, root = example
    components = Path(c['instance_dir']).parent / 'components'; components.mkdir(mode=0o700)
    if selected == 'relative': value = 'components'
    elif selected == 'release': value = str(Path(c['manifest']).parent / 'runtimes')
    elif selected == 'alias':
        alias = components.with_name('components-alias'); alias.symlink_to(components, target_is_directory=True); value = str(alias)
    else: components.chmod(0o777); value = str(components)
    c['local_runtime_root'] = value; resources = Resources()
    with pytest.raises(install.Rejected): run(example, resources)
    assert resources.writes == [] and not Path(c['instance_dir']).exists()


def test_cli_carries_optional_local_root_without_shell_execution(example, monkeypatch, capsys):
    c, runner, _ = example
    components = Path(c['instance_dir']).parent / '本地 components'; components.mkdir(mode=0o700)
    c['local_runtime_root'] = str(components); captured = {}
    monkeypatch.setattr(install, 'runner_module', lambda: runner)
    monkeypatch.setattr(install.getpass, 'getpass', lambda *a: 'private-password-long-enough')
    def prepared(supplied, **kwargs):
        captured.update(supplied); assert kwargs['platform_password'] == 'private-password-long-enough'
        return Path(supplied['instance_dir']) / 'instance.json'
    monkeypatch.setattr(install, 'prepare', prepared)
    argv = [part for key, value in c.items() for part in ('--' + key.replace('_', '-'), str(value))]
    assert install.main(argv) == 0 and captured == c
    output = capsys.readouterr()
    assert 'candidate_runtime.py' in output.out and 'private-password' not in output.out and not output.err


@pytest.mark.parametrize('code', sorted(install.DIAGNOSTICS))
def test_prepare_preserves_allowlisted_runner_rejection_code(monkeypatch, code):
    def reject(*args, **kwargs): raise runtime.Rejected(code)
    monkeypatch.setattr(install, '_prepare', reject)
    with pytest.raises(install.Rejected) as caught:
        install.prepare({}, admin_password='never-used')
    assert caught.value.args == (code,) and caught.value.__suppress_context__


@pytest.mark.parametrize('code', sorted(install.DIAGNOSTICS))
def test_cli_reports_only_fixed_operator_diagnostic_and_never_run_command(example, monkeypatch, capsys, code):
    c, runner, _ = example
    monkeypatch.setattr(install, 'runner_module', lambda: runner)
    def reject(*args, **kwargs): raise runtime.Rejected(code)
    monkeypatch.setattr(install, 'validate_inputs', reject)
    monkeypatch.setattr(install.getpass, 'getpass', lambda *a: pytest.fail('invalid directory must reject before TTY'))
    argv = [part for key, value in c.items() for part in ('--' + key.replace('_', '-'), str(value))]
    assert install.main(argv) == 1
    output = capsys.readouterr()
    assert output.out == ''
    assert output.err == install.DIAGNOSTICS[code] + '\n' + install.FAILURE_FOLLOWUP + '\n'
    assert c['instance_dir'] not in output.err and 'candidate_runtime.py' not in output.err
    assert '--resume' in output.err and '私有进度/配置保留' in output.err


@pytest.mark.parametrize('kind', ['driver', 'foreign_rejected_name', 'runner_unknown', 'unsafe_str'])
def test_prepare_unknown_failures_stay_generic_without_stringifying(monkeypatch, kind):
    if kind == 'driver': error = RuntimeError('SQL postgresql://secret:private-password@private-path')
    elif kind == 'foreign_rejected_name':
        foreign = type('Rejected', (RuntimeError,), {})
        error = foreign('runtime_overlaps_release SQL private-password')
    elif kind == 'runner_unknown': error = runtime.Rejected('unknown_private_password')
    else:
        class UnsafeString(RuntimeError):
            def __str__(self): raise AssertionError('must not stringify unknown exception')
        error = UnsafeString('unknown code')
    def reject(*args, **kwargs): raise error
    monkeypatch.setattr(install, '_prepare', reject)
    with pytest.raises(install.Rejected) as caught:
        install.prepare({}, admin_password='private-password')
    assert caught.value.args == ('installation_preflight_failed',)
    assert caught.value.__suppress_context__


def test_allowlisted_text_from_foreign_exception_can_only_select_fixed_safe_message(example, monkeypatch, capsys):
    c, runner, _ = example
    monkeypatch.setattr(install, 'runner_module', lambda: runner)
    def reject(*args, **kwargs): raise RuntimeError('runtime_overlaps_release')
    monkeypatch.setattr(install, 'validate_inputs', reject)
    argv = [part for key, value in c.items() for part in ('--' + key.replace('_', '-'), str(value))]
    assert install.main(argv) == 1
    output = capsys.readouterr()
    assert output.out == '' and output.err == install.DIAGNOSTICS['runtime_overlaps_release'] + '\n' + install.FAILURE_FOLLOWUP + '\n'


def test_cli_unknown_runner_failure_keeps_secret_text_and_success_command_hidden(example, monkeypatch, capsys):
    c, runner, _ = example
    monkeypatch.setattr(install, 'runner_module', lambda: runner)
    def reject(*args, **kwargs): raise runtime.Rejected('SQL private-password /private-sensitive-path')
    monkeypatch.setattr(install, 'validate_inputs', reject)
    argv = [part for key, value in c.items() for part in ('--' + key.replace('_', '-'), str(value))]
    assert install.main(argv) == 1
    output = capsys.readouterr()
    assert not output.out and output.err == '本次准备未完成或未通过核对。\n' + install.FAILURE_FOLLOWUP + '\n'
    assert all(secret not in output.err for secret in ('SQL', 'private-password', 'private-sensitive-path', 'candidate_runtime.py'))


def test_publish_runner_path_rejection_is_journalled_as_safe_code(example):
    _, runner, _ = example
    def reject(*args, **kwargs): raise runtime.Rejected('runtime_overlaps_release')
    runner.load_instance = reject
    with pytest.raises(install.Rejected, match='^runtime_overlaps_release$'):
        run(example, Resources())
    state = read_state(example)
    assert state['error_code'] == 'runtime_overlaps_release'
    assert not (Path(state['inputs']['instance_dir']) / 'instance.json').exists()
