"""Prepare a candidate on an explicitly supplied, otherwise empty local PG17.

No service startup, package installation, migration of an existing application, or
resource deletion. Password prompts are terminal-only. The private journal is
an intent record; every resumed operation must prove its live resource identity.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import getpass
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import sys
import tempfile
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

ROOT = Path(__file__).absolute().parents[1]
STEPS = ('roles', 'database', 'schema', 'grants', 'bootstrap', 'verified')
INITIAL_DATABASES = {'postgres', 'template0', 'template1'}
INSTANCE_DIRS = ('cwd', 'state', 'logs', 'storage')
FORMAT = 'generic-agents-prepare-v1'

# Only these static codes can select a more specific operator explanation.
# Neither an exception class/name nor arbitrary exception text is trusted.
DIAGNOSTICS = {
    'runtime_overlaps_release': '本地组件目录与不可变程序包不能互相包含，请将程序包与组件目录分开。',
    'runtime_inside_frozen_release': '本地组件目录位于已冻结的程序包中，请选择程序包之外的独立组件目录。',
    'local_runtime_path_rejected': '本地组件目录必须使用无别名、无上级跳转的绝对路径，请检查服务器目录配置。',
    'unsafe_runtime_directory': '本地组件目录或最近的现存目录须由本用户所有，并禁止其他用户写入。',
    'unsafe_runtime_ancestor': '本地组件目录的上级目录所有者或写入权限不可信，请管理员核对目录权限。',
    'implicit_dotenv_forbidden': '工作目录或实例目录包含 .env，请改用不含 .env 的专用目录，避免加载旧服务凭据。',
    'resume_inputs_changed': '恢复参数与原安装进度不一致，请使用原参数和原私有目录核对。',
    'resume_source_changed': '原程序包或安装工具已改变，请恢复安装时固定的版本后核对原进度。',
    'new_instance_directory_required': '实例目录已存在；首次准备须选择全新目录，已有进度须按原目录恢复。',
    'instance_path_rejected': '实例目录必须使用无别名、无上级跳转的绝对路径。',
    'instance_overlaps_protected_data': '实例目录与受保护的工程或运行数据重叠，请选择独立私有目录。',
    'instance_overlaps_project_data': '实例目录与受保护的工程或运行数据重叠，请选择独立私有目录。',
}
FAILURE_FOLLOWUP = '未启动服务；已有私有进度/配置保留。若已创建实例，请仅对原私有目录使用 --resume 核对，不要覆盖或删除原进度。'


def diagnostic_code(error):
    args = error.args
    return args[0] if type(args) is tuple and len(args) == 1 and type(args[0]) is str and args[0] in DIAGNOSTICS else None


class Rejected(RuntimeError):
    """Only constant, non-secret error codes may escape the installer."""


def need(condition, code):
    if not condition:
        raise Rejected(code)


def runner_module():
    sys.dont_write_bytecode = True
    name = '_candidate_prepare_frozen_runner'
    path = ROOT / 'scripts/candidate_runtime.py'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    module = sys.modules[name]
    need(Path(module.__file__) == path, 'foreign_runner_module')
    return module


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def validate_inputs(c, runner, *, root=ROOT, executable=None):
    required = {'manifest', 'manifest_sha256', 'instance_dir', 'python', 'pg_port', 'pg_admin_user',
        'api_port', 'redis_url', 'username', 'workspace_name'}
    need(required <= set(c) <= required | {'local_runtime_root'}, 'input_fields_rejected')
    need(all(type(c[k]) is str for k in set(c) - {'pg_port', 'api_port'}), 'input_types_rejected')
    for key in ('pg_port', 'api_port'):
        need(type(c[key]) is int and 1024 <= c[key] <= 65535 and c[key] not in runner.BLOCKED_PORTS, 'protected_or_invalid_port')
    need(c['pg_port'] != c['api_port'], 'service_port_collision')
    need(re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,62}', c['pg_admin_user']), 'admin_role_rejected')
    need(c['username'] == c['username'].strip() and 1 <= len(c['username']) <= 100
        and c['workspace_name'] == c['workspace_name'].strip() and 1 <= len(c['workspace_name']) <= 200
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in c['username'] + c['workspace_name']), 'bootstrap_names_rejected')
    python = Path(c['python'])
    need(python.is_absolute() and '..' not in python.parts and str(python) == c['python']
        and python.is_file() and python.stat().st_mode & 0o111 and not python.stat().st_mode & 0o022,
        'literal_python_required')
    need(str(Path(sys.executable if executable is None else executable).absolute()) == str(python), 'run_with_selected_python')
    home = Path(c['instance_dir'])
    need(home.is_absolute() and '..' not in home.parts and str(home) == c['instance_dir']
        and home.parent.resolve(strict=True) == home.parent and not home.is_symlink(), 'instance_path_rejected')
    protected = [Path(root) / p for p in ('.runtime', 'src', 'design', 'scripts', 'tests', 'deploy', 'release-candidates')]
    need(not Path(root).is_relative_to(home) and not any(home.is_relative_to(p) or p.is_relative_to(home) for p in protected),
        'instance_overlaps_protected_data')
    need(not os.path.lexists(home / '.env') and not os.path.lexists(Path.cwd() / '.env'), 'implicit_dotenv_forbidden')
    redis = urlsplit(c['redis_url'])
    need(redis.scheme == 'redis' and redis.hostname == '127.0.0.1' and redis.port is not None
        and redis.netloc == '127.0.0.1:' + str(redis.port) and 1 <= redis.port <= 65535
        and re.fullmatch('/[0-9]+', redis.path) and not redis.query and not redis.fragment
        and redis.port not in {c['pg_port'], c['api_port']}, 'explicit_local_redis_required')
    need(not any(key.upper().startswith('PG') for key in os.environ), 'inherited_libpq_environment_forbidden')
    release = runner.load_release(Path(c['manifest']), c['manifest_sha256'], root=root)
    if 'local_runtime_root' in c:
        runner.validate_local_runtime_root(c['local_runtime_root'], release_root=release['root'])
    return release


def load_product(release, runner):
    """Import only the verified candidate. Never call the cached application engine."""
    source = release['root'] / 'src'
    for name, module in tuple(sys.modules.items()):
        if name == 'generic_agents' or name.startswith('generic_agents.'):
            need(getattr(module, '__file__', None) and Path(module.__file__).is_relative_to(source), 'foreign_product_module')
    # Settings defines env_file='.env'; imports here must never inherit live keys
    # or a normal database URL, and no settings()/engine() is called here.
    for name in tuple(os.environ):
        if name.startswith('GA_') or name in {'DASHSCOPE_API_KEY', 'DEEPSEEK_API_KEY'}:
            del os.environ[name]
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    models = importlib.import_module('generic_agents.models')
    bootstrap = importlib.import_module('generic_agents.bootstrap')
    security = importlib.import_module('generic_agents.security')
    expected = {entry['path']: {k: entry[k] for k in ('bytes', 'sha256')} for entry in release['manifest']['files']}
    for name, module in tuple(sys.modules.items()):
        if name == 'generic_agents' or name.startswith('generic_agents.'):
            path = Path(getattr(module, '__file__', ''))
            need(path.is_absolute() and path.is_relative_to(source), 'foreign_product_module')
            relative = str(path.relative_to(release['root']))
            need(relative in expected and runner.file_record(path) == expected[relative], 'product_import_not_in_manifest')
    need(bool(models.Base.metadata.tables) and all(t.schema in {None, 'public'} for t in models.Base.metadata.tables.values()),
        'unsupported_model_contract')
    return {'models': models, 'bootstrap': bootstrap, 'security': security}


def source_pins(release, runner):
    entries = {x['path']: {k: x[k] for k in ('bytes', 'sha256')} for x in release['manifest']['files']}
    names = ('src/generic_agents/models.py', 'src/generic_agents/bootstrap.py')
    need(all(x in entries for x in names), 'installer_product_helpers_missing')
    return {'manifest': release['record'], 'models': entries[names[0]], 'bootstrap': entries[names[1]],
        'installer': runner.file_record(Path(__file__).absolute()), 'runner': runner.file_record(Path(runner.__file__).absolute())}


def new_state(c, system_identifier, encoded_password, pins):
    ident = uuid4().hex
    return {'format': FORMAT, 'inputs': dict(c), 'database_id': ident,
        'database': 'ga_candidate_' + ident, 'owner': 'ga_candidate_owner_' + ident[:12],
        'app': 'ga_candidate_app_' + ident[:12], 'marker': 'generic-agents-candidate:' + ident,
        'app_password': secrets.token_urlsafe(36), 'password_hash': encoded_password,
        'user_id': str(uuid4()), 'workspace_id': str(uuid4()), 'system_identifier': system_identifier,
        'oid': None, 'schema_sha256': None, 'source_pins': pins, 'completed': [], 'phase': 'prepared', 'error_code': None}


def validate_state(s):
    need(type(s) is dict and set(s) == {'format', 'inputs', 'database_id', 'database', 'owner', 'app', 'marker',
        'app_password', 'password_hash', 'user_id', 'workspace_id', 'system_identifier', 'oid', 'schema_sha256',
        'source_pins', 'completed', 'phase', 'error_code'} and s['format'] == FORMAT, 'state_shape_rejected')
    ident = s['database_id']
    need(type(ident) is str and re.fullmatch('[0-9a-f]{32}', ident) and s['database'] == 'ga_candidate_' + ident
        and s['owner'] == 'ga_candidate_owner_' + ident[:12] and s['app'] == 'ga_candidate_app_' + ident[:12]
        and s['marker'] == 'generic-agents-candidate:' + ident, 'state_identity_rejected')
    need(type(s['app_password']) is str and re.fullmatch('[A-Za-z0-9_-]{48}', s['app_password'])
        and type(s['password_hash']) is str and s['password_hash'].startswith('$argon2id$'), 'state_secret_contract_rejected')
    need(all(type(s[k]) is str and str(UUID(s[k])) == s[k] for k in ('user_id', 'workspace_id')),
        'state_bootstrap_identity_rejected')
    need(type(s['system_identifier']) is str and re.fullmatch('[1-9][0-9]{0,19}', s['system_identifier']), 'state_system_identifier_rejected')
    need(s['oid'] is None or type(s['oid']) is int and 0 < s['oid'] < 2**32, 'state_oid_rejected')
    need(s['schema_sha256'] is None or type(s['schema_sha256']) is str and re.fullmatch('[0-9a-f]{64}', s['schema_sha256']), 'state_schema_digest_rejected')
    need(type(s['completed']) is list and s['completed'] == list(STEPS[:len(s['completed'])]), 'state_order_rejected')
    expected_phase = STEPS[len(s['completed'])] + '_pending' if len(s['completed']) < len(STEPS) else 'publish_pending'
    need(s['phase'] in {expected_phase, 'prepared', 'ready'} and (s['phase'] != 'prepared' or not s['completed'])
        and (s['phase'] != 'ready' or len(s['completed']) == len(STEPS)), 'state_phase_rejected')
    pins = s['source_pins']
    need(type(pins) is dict and set(pins) == {'manifest', 'models', 'bootstrap', 'installer', 'runner'}
        and all(type(p) is dict and set(p) == {'bytes', 'sha256'} and type(p['bytes']) is int and p['bytes'] >= 0
            and type(p['sha256']) is str and re.fullmatch('[0-9a-f]{64}', p['sha256']) for p in pins.values()), 'state_source_pins_rejected')
    need(s['error_code'] is None or type(s['error_code']) is str and re.fullmatch('[a-z0-9_]{1,80}', s['error_code']), 'state_error_rejected')


class Journal:
    def __init__(self, home, runner, *, resume):
        self.home = Path(home); self.runner = runner; self.resume = resume; self.lock = None

    def __enter__(self):
        try:
            return self.enter_locked()
        except BaseException:
            if self.lock is not None:
                os.close(self.lock); self.lock = None
            raise

    def enter_locked(self):
        if not self.resume:
            self.home.mkdir(mode=0o700)
        self.runner.private_directory(self.home)
        need(not os.path.lexists(self.home / '.env'), 'implicit_dotenv_forbidden')
        path = self.home / 'prepare.lock'
        self.lock = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        st = os.fstat(self.lock)
        need(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and st.st_nlink == 1
            and stat.S_IMODE(st.st_mode) == 0o600, 'unsafe_lock')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.lock); self.lock = None
            raise Rejected('another_installer_holds_lock') from None
        if not self.resume:
            for name in INSTANCE_DIRS:
                (self.home / name).mkdir(mode=0o700)
        for name in INSTANCE_DIRS:
            self.runner.private_directory(self.home / name)
            need(not os.path.lexists(self.home / name / '.env'), 'implicit_dotenv_forbidden')
        return self

    def __exit__(self, *_):
        if self.lock is not None:
            os.close(self.lock); self.lock = None

    def write(self, name, value, *, exclusive=False):
        need(name in {'prepare-state.json', 'instance.json'}, 'journal_name_rejected')
        self.runner.private_directory(self.home)
        target = self.home / name
        if os.path.lexists(target):
            need(not exclusive, 'published_configuration_already_exists')
            self.runner.file_record(target, private=True)
        fd, temporary = tempfile.mkstemp(prefix='.prepare-atomic-', dir=self.home)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write((json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n').encode())
                output.flush(); os.fsync(output.fileno())
            if name == 'instance.json':
                # The runner validates this exact private payload and directory
                # layout before the final configuration name becomes visible.
                self.runner.load_instance(Path(temporary))
            if exclusive:
                os.link(temporary, target, follow_symlinks=False)
                os.unlink(temporary)
            else:
                os.replace(temporary, target)
            directory = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)

    def save(self, state):
        validate_state(state); self.write('prepare-state.json', state)


def role_comment(s, role):
    return s['marker'] + ':' + role


def instance_configuration(s):
    c = s['inputs']; home = Path(c['instance_dir'])
    url = 'postgresql+psycopg://' + s['app'] + ':' + quote(s['app_password'], safe='') + '@127.0.0.1:' + str(c['pg_port']) + '/' + s['database']
    environment = {'GA_DATABASE_URL': url, 'GA_REDIS_URL': c['redis_url']}
    if 'local_runtime_root' in c:
        environment['GA_LOCAL_RUNTIME_ROOT'] = c['local_runtime_root']
    return {'format': 'generic-agents-instance-v1', 'release_manifest': c['manifest'],
        'release_manifest_sha256': c['manifest_sha256'], 'workspace_id': s['workspace_id'], 'python': c['python'],
        'host': '127.0.0.1', 'port': c['api_port'], 'cwd': str(home / 'cwd'), 'state_dir': str(home / 'state'),
        'log_dir': str(home / 'logs'), 'storage_dir': str(home / 'storage'),
        'environment': environment,
        'owned_database': {'system_identifier': s['system_identifier'], 'oid': s['oid'], 'marker': s['marker']}}


def finish(s, journal, gateway, runner):
    """Intent before every potentially committing operation; read before retry."""
    for step in STEPS:
        already = step in s['completed']
        if not already:
            s['phase'] = step + '_pending'; s['error_code'] = None; journal.save(s)
        getattr(gateway, 'ensure_' + step)(s, allow_create=not already)
        if not already:
            s['completed'].append(step)
            s['phase'] = (STEPS[len(s['completed'])] + '_pending') if len(s['completed']) < len(STEPS) else 'publish_pending'
            journal.save(s)
    config = instance_configuration(s)
    # Recheck the entire frozen release immediately before publishing credentials.
    runner.load_release(Path(s['inputs']['manifest']), s['inputs']['manifest_sha256'])
    path = journal.home / 'instance.json'
    if os.path.lexists(path):
        current, _ = runner.read_json(path, private=True)
        need(current == config, 'published_configuration_changed')
    else:
        journal.write('instance.json', config, exclusive=True)
    s['phase'] = 'ready'; s['error_code'] = None; journal.save(s)
    return path


class Postgres:
    """Small, injectable SQL boundary. All identifiers use psycopg.sql.Identifier."""
    def __init__(self, c, admin_password, product, runner, *, connect=None, engine_factory=None, checkpoint=None):
        import psycopg
        from sqlalchemy import create_engine
        self.c = c; self._admin_password = admin_password; self.product = product; self.runner = runner
        self.connect = psycopg.connect if connect is None else connect
        self.engine_factory = create_engine if engine_factory is None else engine_factory
        self.checkpoint = checkpoint or (lambda label, state: None)

    @contextmanager
    def connection(self, database='postgres', *, readonly=True, autocommit=False):
        conn = self.connect(host='127.0.0.1', hostaddr='127.0.0.1', port=self.c['pg_port'], dbname=database,
            user=self.c['pg_admin_user'], password=self._admin_password, sslmode='disable', passfile='/dev/null',
            connect_timeout=2, autocommit=autocommit,
            options='-c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=10000 -c search_path=pg_catalog,public')
        try:
            if readonly:
                conn.execute('SET TRANSACTION READ ONLY')
            yield conn
            if not autocommit and not readonly:
                conn.commit()
        finally:
            try:
                if not autocommit:
                    conn.rollback()
            finally:
                conn.close()

    def cluster(self, s=None):
        with self.connection() as db:
            row = db.execute("SELECT current_database(),host(inet_server_addr()),inet_server_port(),"
                "current_setting('server_version_num')::int,current_user,r.rolsuper,s.system_identifier::text "
                "FROM pg_roles r CROSS JOIN pg_control_system() s WHERE r.rolname=current_user").fetchone()
            need(row is not None and tuple(row[:6]) == ('postgres', '127.0.0.1', self.c['pg_port'], row[3], self.c['pg_admin_user'], True)
                and 170000 <= row[3] < 180000, 'dedicated_pg17_superuser_required')
            need(re.fullmatch('[1-9][0-9]{0,19}', row[6]), 'invalid_system_identifier')
            names = {r[0] for r in db.execute('SELECT datname FROM pg_database').fetchall()}
            expected = INITIAL_DATABASES | ({s['database']} if s else set())
            need(names == INITIAL_DATABASES if s is None else INITIAL_DATABASES <= names <= expected, 'cluster_contains_other_databases')
            if s:
                need(row[6] == s['system_identifier'], 'cluster_system_identifier_changed')
            return row[6]

    def ensure_roles(self, s, *, allow_create):
        from psycopg import ClientCursor, sql
        self.cluster(s)
        with self.connection() as db:
            rows = db.execute("SELECT r.rolname,r.rolcanlogin,r.rolinherit,r.rolsuper,r.rolcreatedb,r.rolcreaterole,"
                "r.rolreplication,r.rolbypassrls,shobj_description(r.oid,'pg_authid'),"
                "EXISTS(SELECT 1 FROM pg_auth_members a WHERE a.member=r.oid OR a.roleid=r.oid) "
                "FROM pg_roles r WHERE r.rolname IN (%s,%s) ORDER BY r.rolname", (s['owner'], s['app'])).fetchall()
        if not rows:
            need(allow_create, 'owned_roles_missing')
            with self.connection(readonly=False) as db:
                # Utility statements require client-side safe parameter binding.
                # Do not emit server statement/error logs containing a password.
                db.execute("SET LOCAL log_statement='none'")
                db.execute('SET LOCAL log_min_duration_statement=-1')
                db.execute('SET LOCAL log_min_duration_sample=-1')
                db.execute('SET LOCAL log_transaction_sample_rate=0')
                db.execute("SET LOCAL log_min_error_statement='panic'")
                db.execute("SET LOCAL password_encryption='scram-sha-256'")
                verifier = db.pgconn.encrypt_password(s['app_password'].encode(), s['app'].encode(), b'scram-sha-256').decode('ascii')
                need(verifier.startswith('SCRAM-SHA-256$'), 'app_password_verifier_failed')
                for role, login in ((s['owner'], False), (s['app'], True)):
                    command = sql.SQL('CREATE ROLE {} {} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(
                        sql.Identifier(role), sql.SQL('LOGIN' if login else 'NOLOGIN'))
                    with ClientCursor(db) as cur:
                        cur.execute(command + (sql.SQL(' PASSWORD %s') if login else sql.SQL('')), (verifier,) if login else None)
                        cur.execute(sql.SQL('COMMENT ON ROLE {} IS %s').format(sql.Identifier(role)), (role_comment(s, 'app' if login else 'owner'),))
            return self.ensure_roles(s, allow_create=False)
        expected = [(s['owner'], False, False, False, False, False, False, False, role_comment(s, 'owner'), False),
            (s['app'], True, False, False, False, False, False, False, role_comment(s, 'app'), False)]
        need(sorted(map(tuple, rows)) == sorted(expected), 'owned_role_identity_or_privilege_changed')

    def database_row(self, s):
        with self.connection() as db:
            return db.execute("SELECT d.oid,pg_get_userbyid(d.datdba),shobj_description(d.oid,'pg_database'),d.datallowconn "
                "FROM pg_database d WHERE datname=%s", (s['database'],)).fetchone()

    def empty_unmarked_database(self, s):
        with self.connection(s['database']) as db:
            need(db.execute("SELECT NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname NOT IN ('public','pg_catalog','information_schema') "
                "AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE 'pg_temp_%') AND NOT EXISTS(SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public') AND NOT EXISTS(SELECT 1 FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid())").fetchone() == (True,), 'unmarked_database_not_empty_or_exclusive')

    def ensure_database(self, s, *, allow_create):
        from psycopg import ClientCursor, sql
        self.cluster(s); row = self.database_row(s)
        if row is None:
            need(allow_create and s['oid'] is None, 'owned_database_missing')
            with self.connection(readonly=False, autocommit=True) as db:
                db.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(sql.Identifier(s['database']), sql.Identifier(s['owner'])))
            self.checkpoint('database_created', s)
            row = self.database_row(s)
        need(row and row[1] == s['owner'] and row[3] is True and (s['oid'] is None or s['oid'] == row[0]), 'owned_database_identity_changed')
        if row[2] is None:
            need(allow_create and s['phase'] == 'database_pending' and s['oid'] is None, 'unmarked_database_rejected')
            self.ensure_roles(s, allow_create=False); self.empty_unmarked_database(s)
            with self.connection(readonly=False) as db, ClientCursor(db) as cur:
                cur.execute(sql.SQL('COMMENT ON DATABASE {} IS %s').format(sql.Identifier(s['database'])), (s['marker'],))
            row = self.database_row(s)
        need(row[2] == s['marker'], 'owned_database_marker_changed')
        s['oid'] = row[0]

    @contextmanager
    def engine(self, s):
        from sqlalchemy.engine import URL
        engine = self.engine_factory(URL.create('postgresql+psycopg', username=self.c['pg_admin_user'], password=self._admin_password,
            host='127.0.0.1', port=self.c['pg_port'], database=s['database']), hide_parameters=True,
            connect_args={'hostaddr': '127.0.0.1', 'port': self.c['pg_port'], 'sslmode': 'disable', 'passfile': '/dev/null', 'connect_timeout': 2,
                'options': '-c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=10000 -c search_path=pg_catalog,public'})
        try:
            yield engine
        finally:
            engine.dispose()

    def statement(self, conn, template, *identifiers):
        from psycopg import sql
        command = sql.SQL(template).format(*(sql.Identifier(x) for x in identifiers))
        return conn.exec_driver_sql(command.as_string(conn.connection.driver_connection))

    def schema(self, s):
        from sqlalchemy import inspect
        with self.engine(s) as engine, engine.connect() as db:
            try:
                db.exec_driver_sql('SET TRANSACTION READ ONLY')
                inspector = inspect(db)
                schemas = inspector.get_schema_names()
                need(set(schemas) <= {'public', 'information_schema', 'pg_catalog'} | {x for x in schemas if x.startswith('pg_toast')}, 'extra_schema_rejected')
                names = inspector.get_table_names(schema='public')
                need(not inspector.get_view_names(schema='public') and not inspector.get_materialized_view_names(schema='public')
                    and not inspector.get_sequence_names(schema='public'), 'extra_schema_objects_rejected')
                owners = db.exec_driver_sql("SELECT c.relname,pg_get_userbyid(c.relowner),c.relkind,c.relrowsecurity,c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname").all()
                need(all(r[1] == s['owner'] and r[2] in {'r', 'i'} and not r[3] and not r[4] for r in owners), 'schema_object_ownership_changed')
                need(db.exec_driver_sql("SELECT NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public') "
                    "AND NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND NOT t.tgisinternal)").scalar_one(), 'extra_function_or_trigger_rejected')
                constraints = db.exec_driver_sql("SELECT c.conname,c.contype,c.condeferrable,c.condeferred,c.convalidated,pg_get_constraintdef(c.oid) "
                    "FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' ORDER BY c.conrelid,c.conname").all()
                need(all(r[1] in {'p', 'u', 'f'} and not r[2] and not r[3] and r[4] for r in constraints), 'constraint_semantics_changed')
                indexes = db.exec_driver_sql("SELECT c.relname,a.amname,i.indisvalid,i.indisready,i.indislive,i.indnullsnotdistinct,"
                    "i.indpred IS NULL,i.indexprs IS NULL,pg_get_indexdef(c.oid) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_am a ON a.oid=c.relam WHERE n.nspname='public' ORDER BY c.relname").all()
                need(all(tuple(r[1:8]) == ('btree', True, True, True, False, True, True) for r in indexes), 'index_semantics_changed')
                result = {}
                for name in sorted(names):
                    columns = inspector.get_columns(name, schema='public')
                    result[name] = {'columns': [{'name': x['name'], 'type': str(x['type'].compile(dialect=engine.dialect)),
                        'nullable': x['nullable'], 'default': x['default'], 'identity': x.get('identity'), 'computed': x.get('computed')} for x in columns],
                        'pk': inspector.get_pk_constraint(name, schema='public'), 'fk': inspector.get_foreign_keys(name, schema='public'),
                        'unique': inspector.get_unique_constraints(name, schema='public'), 'indexes': inspector.get_indexes(name, schema='public'),
                        'checks': inspector.get_check_constraints(name, schema='public')}
                if names:
                    validate_schema(result, self.product['models'].Base.metadata, engine.dialect)
                return {'tables': result, 'constraints': list(map(tuple, constraints)), 'indexes': list(map(tuple, indexes))} if names else {}
            finally:
                db.rollback()

    def ensure_schema(self, s, *, allow_create):
        self.ensure_database(s, allow_create=False)
        actual = self.schema(s)
        if not actual:
            need(allow_create and s['schema_sha256'] is None, 'owned_schema_missing')
            with self.engine(s) as engine, engine.begin() as db:
                self.statement(db, 'ALTER SCHEMA public OWNER TO {}', s['owner'])
                self.statement(db, 'SET ROLE {}', s['owner'])
                db.exec_driver_sql('SET LOCAL search_path=public,pg_catalog')
                self.product['models'].Base.metadata.create_all(db)
                db.exec_driver_sql('ALTER TABLE observations ALTER COLUMN observed_at DROP NOT NULL')
                db.exec_driver_sql('ALTER TABLE observations ALTER COLUMN business_day DROP NOT NULL')
                db.exec_driver_sql('RESET ROLE')
            actual = self.schema(s)
        schema_hash = digest(actual)
        need(s['schema_sha256'] is None or schema_hash == s['schema_sha256'], 'schema_snapshot_changed')
        s['schema_sha256'] = schema_hash

    def ensure_grants(self, s, *, allow_create):
        if allow_create:
            with self.engine(s) as engine, engine.begin() as db:
                for statement, args in [
                    ('REVOKE CONNECT, TEMPORARY ON DATABASE postgres, template0, template1 FROM PUBLIC', ()),
                    ('REVOKE ALL ON DATABASE {} FROM PUBLIC', (s['database'],)),
                    ('GRANT CONNECT ON DATABASE {} TO {}', (s['database'], s['app'])),
                    ('REVOKE ALL ON SCHEMA public FROM PUBLIC', ()),
                    ('GRANT USAGE ON SCHEMA public TO {}', (s['app'],)),
                    ('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC', ()),
                    ('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}', (s['app'],)),
                    ('ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC', (s['owner'],)),
                    ('ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE USAGE ON TYPES FROM PUBLIC', (s['owner'],)),
                    ('REVOKE EXECUTE ON FUNCTION pg_catalog.pg_control_system() FROM PUBLIC', ()),
                    ('GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO {}', (s['app'],))]:
                    self.statement(db, statement, *args)
        self.verify_grants(s)

    def verify_grants(self, s):
        self.ensure_roles(s, allow_create=False)
        with self.connection(s['database']) as db:
            row = db.execute("SELECT has_database_privilege(%s,current_database(),'CONNECT'),has_database_privilege(%s,current_database(),'CREATE'),"
                "has_database_privilege(%s,current_database(),'TEMP'),has_schema_privilege(%s,'public','USAGE'),has_schema_privilege(%s,'public','CREATE'),"
                "pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public'", (s['app'],) * 5).fetchone()
            need(tuple(row) == (True, False, False, True, False, s['owner']), 'app_database_or_schema_privileges_changed')
            rows = db.execute("SELECT c.relname,a.grantee,a.grantor,a.privilege_type,a.is_grantable FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(c.relacl) a "
                "WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname,a.grantee,a.privilege_type").fetchall()
            roles = dict(db.execute('SELECT rolname,oid FROM pg_roles WHERE rolname IN (%s,%s)', (s['owner'], s['app'])).fetchall())
            expected = set()
            for table in self.product['models'].Base.metadata.tables:
                for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                    expected.add((table, roles[s['app']], roles[s['owner']], privilege, False))
                for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN'):
                    expected.add((table, roles[s['owner']], roles[s['owner']], privilege, False))
            need(set(map(tuple, rows)) == expected, 'table_acl_changed')
            defaults = db.execute("SELECT d.defaclrole,d.defaclnamespace,d.defaclobjtype,a.grantee,a.grantor,a.privilege_type,a.is_grantable "
                "FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a WHERE d.defaclrole IN (%s,%s)",
                (roles[s['owner']], roles[s['app']])).fetchall()
            need(set(map(tuple, defaults)) == {(roles[s['owner']], 0, 'f', roles[s['owner']], roles[s['owner']], 'EXECUTE', False),
                (roles[s['owner']], 0, 'T', roles[s['owner']], roles[s['owner']], 'USAGE', False)}, 'default_acl_changed')
            # PUBLIC database/schema ACLs and role/schema ownership are not inferred from app capabilities.
            need(db.execute("SELECT NOT EXISTS(SELECT 1 FROM pg_database d CROSS JOIN LATERAL aclexplode(d.datacl) a "
                "WHERE d.datname=current_database() AND a.grantee=0) AND NOT EXISTS(SELECT 1 FROM pg_namespace n "
                "CROSS JOIN LATERAL aclexplode(n.nspacl) a WHERE n.nspname='public' AND a.grantee=0)").fetchone() == (True,), 'public_acl_changed')
            acl = db.execute("SELECT 'database',a.grantee,a.grantor,a.privilege_type,a.is_grantable FROM pg_database d "
                "CROSS JOIN LATERAL aclexplode(d.datacl) a WHERE d.datname=current_database() UNION ALL "
                "SELECT 'schema',a.grantee,a.grantor,a.privilege_type,a.is_grantable FROM pg_namespace n "
                "CROSS JOIN LATERAL aclexplode(n.nspacl) a WHERE n.nspname='public'").fetchall()
            expected_acl = {('database', roles[s['app']], roles[s['owner']], 'CONNECT', False),
                ('schema', roles[s['app']], roles[s['owner']], 'USAGE', False)}
            expected_acl |= {('database', roles[s['owner']], roles[s['owner']], p, False) for p in ('CREATE', 'CONNECT', 'TEMPORARY')}
            expected_acl |= {('schema', roles[s['owner']], roles[s['owner']], p, False) for p in ('CREATE', 'USAGE')}
            need(set(map(tuple, acl)) == expected_acl, 'database_or_schema_acl_changed')
            need(db.execute("SELECT bool_and(NOT has_database_privilege(%s,datname,'CONNECT') AND NOT has_database_privilege(%s,datname,'TEMP')) "
                "FROM pg_database WHERE datname IN ('postgres','template0','template1')", (s['app'], s['app'])).fetchone() == (True,), 'maintenance_database_access_changed')
            function = db.execute("SELECT p.proowner,a.grantee,a.grantor,a.privilege_type,a.is_grantable FROM pg_proc p "
                "CROSS JOIN LATERAL aclexplode(p.proacl) a WHERE p.oid='pg_catalog.pg_control_system()'::regprocedure").fetchall()
            need(len(function) == 2 and all(tuple(r[2:]) == (r[0], 'EXECUTE', False) for r in function)
                and {r[1] for r in function} == {function[0][0], roles[s['app']]}, 'control_function_acl_changed')

    def bootstrap_rows(self, s):
        with self.connection(s['database']) as db:
            users = db.execute('SELECT id,username,password_hash,active FROM users ORDER BY id').fetchall()
            spaces = db.execute('SELECT id,name FROM workspaces ORDER BY id').fetchall()
            memberships = db.execute('SELECT user_id,workspace_id,role FROM memberships ORDER BY id').fetchall()
            return users, spaces, memberships

    def check_bootstrap(self, s, rows, *, exclusive):
        users, spaces, memberships = rows
        expected_user = (s['user_id'], self.c['username'], s['password_hash'], True)
        expected_space = (s['workspace_id'], self.c['workspace_name'])
        expected_membership = (s['user_id'], s['workspace_id'], 'admin')
        need(expected_user in list(map(tuple, users)) and expected_space in list(map(tuple, spaces))
            and list(map(tuple, memberships)).count(expected_membership) == 1
            and not any((r[0] == s['user_id'] or r[1] == self.c['username']) and tuple(r) != expected_user for r in users)
            and not any((r[0] == s['user_id'] and r[1] == s['workspace_id']) and tuple(r) != expected_membership for r in memberships),
            'bootstrap_identity_or_hash_changed')
        if exclusive:
            need(len(users) == len(spaces) == len(memberships) == 1, 'unexpected_initial_bootstrap_rows')

    def ensure_bootstrap(self, s, *, allow_create):
        rows = self.bootstrap_rows(s)
        if not any(rows):
            from sqlalchemy.orm import Session
            need(allow_create, 'bootstrap_rows_missing')
            with self.engine(s) as engine, Session(engine) as db, db.begin():
                from sqlalchemy import text
                from psycopg import sql
                db.execute(text(sql.SQL('SET ROLE {}').format(sql.Identifier(s['owner'])).as_string()))
                result = self.product['bootstrap'].create_administrator(db, username=self.c['username'], workspace_name=self.c['workspace_name'],
                    encoded_password=s['password_hash'], user_id=s['user_id'], workspace_id=s['workspace_id'])
                need(result == {k: s[k] for k in ('user_id', 'workspace_id')}, 'bootstrap_helper_result_changed')
                db.execute(text('RESET ROLE'))
            rows = self.bootstrap_rows(s)
        self.check_bootstrap(s, rows, exclusive=allow_create)

    def ensure_verified(self, s, *, allow_create):
        self.cluster(s); self.ensure_database(s, allow_create=False); self.ensure_schema(s, allow_create=False)
        self.verify_grants(s); self.check_bootstrap(s, self.bootstrap_rows(s), exclusive=False)
        self.runner.verify_database(instance_configuration(s))


def validate_schema(actual, metadata, dialect):
    """Compare reflected semantics, then freeze the complete named catalog digest."""
    need(bool(actual) and set(actual) == set(metadata.tables), 'model_table_set_changed')
    for name, table in metadata.tables.items():
        row = actual[name]
        expected_columns = [{'name': c.name, 'type': str(c.type.compile(dialect=dialect)),
            'nullable': True if name == 'observations' and c.name in {'observed_at', 'business_day'} else c.nullable,
            'default': None, 'identity': None, 'computed': None} for c in table.columns]
        need(row['columns'] == expected_columns, 'model_columns_changed')
        need(row['pk']['constrained_columns'] == [c.name for c in table.primary_key.columns], 'model_primary_key_changed')
        foreign = sorted((tuple(x['constrained_columns']), x.get('referred_schema') or 'public', x['referred_table'], tuple(x['referred_columns']),
            tuple(sorted((x.get('options') or {}).items()))) for x in row['fk'])
        expected_foreign = sorted((tuple(c.name for c in f.columns), 'public', f.referred_table.name,
            tuple(e.column.name for e in f.elements), tuple(sorted((k, v) for k, v in {'ondelete': f.ondelete, 'onupdate': f.onupdate}.items() if v)))
            for f in table.foreign_key_constraints)
        need(foreign == expected_foreign, 'model_foreign_key_changed')
        from sqlalchemy import UniqueConstraint
        need(sorted(tuple(x['column_names']) for x in row['unique']) == sorted(tuple(c.name for c in u.columns)
            for u in table.constraints if isinstance(u, UniqueConstraint))
            and all(not any(v for v in x.get('dialect_options', {}).values()) for x in row['unique']), 'model_unique_constraint_changed')
        indexes = [x for x in row['indexes'] if not x.get('duplicates_constraint')]
        need(sorted((x['name'], tuple(x['column_names']), bool(x['unique'])) for x in indexes)
            == sorted((i.name, tuple(c.name for c in i.columns), i.unique) for i in table.indexes)
            and all(not x.get('expressions') and not any(v for v in x.get('dialect_options', {}).values()) for x in indexes), 'model_index_changed')
        need(not row['checks'], 'model_check_constraint_changed')


def _prepare(c, *, admin_password, platform_password=None, resume=False, checkpoint=None,
        runner=None, product_loader=load_product, postgres_factory=Postgres):
    """The same installer used by CLI; injectable boundaries are for offline tests.

    checkpoint('database_created', state) observes the real nontransactional gap;
    it may abort, but may not authorize adoption of other resources.
    """
    runner = runner_module() if runner is None else runner
    state = None
    if resume:
        state, _ = runner.read_json(Path(c['instance_dir']) / 'prepare-state.json', private=True)
        validate_state(state)
        need(state['inputs'] == c, 'resume_inputs_changed')
    else:
        need(not os.path.lexists(c['instance_dir']), 'new_instance_directory_required')
    release = validate_inputs(c, runner)
    product = product_loader(release, runner)
    pins = source_pins(release, runner)
    if state:
        need(state['source_pins'] == pins, 'resume_source_changed')
        from argon2 import extract_parameters
        extract_parameters(state['password_hash'])
    need(type(admin_password) is str and bool(admin_password), 'admin_password_required')
    gateway = postgres_factory(c, admin_password, product, runner, checkpoint=checkpoint)
    system_identifier = gateway.cluster(state)
    if state is None:
        need(type(platform_password) is str and len(platform_password) >= 12, 'platform_password_rejected')
        state = new_state(c, system_identifier, product['security'].password_hash(platform_password), pins)
    with Journal(c['instance_dir'], runner, resume=resume) as journal:
        if resume:
            reread, _ = runner.read_json(journal.home / 'prepare-state.json', private=True)
            need(reread == state, 'state_changed_before_lock')
        else:
            journal.save(state)
        try:
            return finish(state, journal, gateway, runner)
        except BaseException as error:
            code = diagnostic_code(error) or (str(error) if isinstance(error, Rejected) else '')
            state['error_code'] = code if re.fullmatch('[a-z0-9_]{1,80}', code) else 'installation_step_failed'
            journal.save(state)
            raise Rejected(state['error_code']) from None


def prepare(c, *, admin_password, platform_password=None, resume=False, checkpoint=None,
        runner=None, product_loader=load_product, postgres_factory=Postgres):
    """Public CLI-equivalent entry: driver exceptions never expose SQL/secrets."""
    try:
        return _prepare(c, admin_password=admin_password, platform_password=platform_password, resume=resume,
            checkpoint=checkpoint, runner=runner, product_loader=product_loader, postgres_factory=postgres_factory)
    except Rejected:
        raise
    except (Exception, KeyboardInterrupt) as error:
        raise Rejected(diagnostic_code(error) or 'installation_preflight_failed') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description='在已运行的专用本机 PostgreSQL 17 上准备新实例；不会启动服务。')
    parser.add_argument('--instance-dir', required=True)
    for name in ('manifest', 'manifest-sha256', 'python', 'redis-url', 'username', 'workspace-name', 'local-runtime-root'):
        parser.add_argument('--' + name)
    parser.add_argument('--pg-port', type=int); parser.add_argument('--api-port', type=int)
    parser.add_argument('--pg-admin-user'); parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    try:
        runner = runner_module()
        # No implicit settings or secret configuration lookup, even before imports.
        need(not any(key.upper().startswith('PG') for key in os.environ), 'inherited_libpq_environment_forbidden')
        supplied = {k: v for k, v in vars(args).items() if k != 'resume' and v is not None}
        if args.resume:
            state, _ = runner.read_json(Path(args.instance_dir) / 'prepare-state.json', private=True)
            validate_state(state); c = state['inputs']
            need(all(c.get(k) == v for k, v in supplied.items()), 'resume_inputs_changed')
        else:
            c = supplied
            required = {'instance_dir', 'manifest', 'manifest_sha256', 'python', 'redis_url', 'username', 'workspace_name',
                'pg_port', 'api_port', 'pg_admin_user'}
            need(required <= set(c) <= required | {'local_runtime_root'}, 'initial_arguments_required')
            need(not os.path.lexists(c['instance_dir']), 'new_instance_directory_required')
        validate_inputs(c, runner)
        admin_password = getpass.getpass('PostgreSQL 管理员密码：')
        need(bool(admin_password), 'admin_password_required')
        password = None
        if not args.resume:
            password = getpass.getpass('新平台管理员密码（至少12个字符）：')
            confirmation = getpass.getpass('再次输入平台管理员密码：')
            need(password == confirmation and len(password) >= 12, 'platform_password_rejected')
            del confirmation
        path = prepare(c, admin_password=admin_password, platform_password=password, resume=args.resume, runner=runner)
        del password, admin_password
        command = [c['python'], '-B', str(ROOT / 'scripts/candidate_runtime.py'), 'run', '--instance', str(path)]
        print(shlex.join(command))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        # Never stringify database/driver exceptions: they can contain SQL or passwords.
        reason = DIAGNOSTICS.get(diagnostic_code(error), '本次准备未完成或未通过核对。')
        print(reason + '\n' + FAILURE_FOLLOWUP, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
