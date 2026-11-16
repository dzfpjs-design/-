"""Verify and supervise an explicitly configured candidate; never manage M44.

No migrations, database recovery, HTTP readiness probes or installation downloads.
The foreground owner alone may signal the process groups it directly creates.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from uuid import UUID

ROOT = Path(__file__).absolute().parents[1]
BLOCKED_PORTS = {4187, 5432, 8765, 18100, 18101, 18102, 18103, 18104, 18105, 18106, 18107, 18108, 18109, 18110, 18111, 18112, 18113, 18114, 18115, 18116, 18117, 18118, 18119, 18120, 18121, 18122, 18123, 18124, 18125, 18126, 18127, 18128, 18129, 18130, 18131, 18132, 18133, 18134, 18135, 18136, 18137, 18138, 18139, 18140, 18141, 18142, 18143, 18144, 18145, 18146, 18147, 18148, 50228, 50328, 50428, 50528, 50628, 50728, 50828, 50928, 51028, 51128, 51228, 51328, 51428, 51528, 51628, 51728, 51828, 51928, 52028, 52128, 52228, 52328, 52428, 52528, 52628, 52728, 52828, 52928, 53028, 53128, 53228, 53328, 53428, 53528, 53628, 53728, 53828, 53928, 54028, 54128, 54228, 54328, 54428, 54528, 54628, 54728, 54828, 54928, 55028, 55128, 55228, 55328, 55428, 55432, 55528, 55628, 55728, 55828, 55928, 56028, 56128, 56228, 56328, 56428, 56528, 56628, 56728, 56828, 56928, 57028, 57128, 57228, 57328, 57428, 57528, 57628, 57728, 57828, 57928, 58028, 58128, 58228, 58328, 58428, 58528, 58628, 58728, 58828, 58928, 59028, 59128, 59228, 59328, 59428, 59528, 59628, 59728, 59828, 59928, 60028, 60128, 60228, 60328, 60428, 60528, 60628, 60728, 60828, 60928, 61028, 61128, 61228, 61328, 18149, 18150, 18151, 18152, 18153, 18154, 18155, 18156, 18157, 18158, 63077}
BLOCKED_PORTS.update({61428, 61528, 61628, 61728, 61828, 61928, 62028, 62128, 62228, 18159, 18160, 18161, 18162, 18163, 18164})
BLOCKED_PORTS.update({62328, 62428, 62528, 18165, 18166, 18167})
BLOCKED_PORTS.update({62628, 62728, 62828, 18168, 18169})
BLOCKED_PORTS.update({62928, 18170})
BLOCKED_PORTS.update({63028, 18171})
BLOCKED_PORTS.update({63128, 18172})
BLOCKED_PORTS.update({63228, 18173})
BLOCKED_PORTS.update({63328, 18174})
BLOCKED_PORTS.update({63428})
BLOCKED_PORTS.update({63528, 18175})
BLOCKED_PORTS.update({63628, 18176})
BLOCKED_PORTS.update({63728})
BLOCKED_PORTS.update({64028})
BLOCKED_PORTS.update({63828, 18177})
BLOCKED_PORTS.update({64128, 64228, 18179})
BLOCKED_PORTS.update({64528, 18181})
BLOCKED_PORTS.update({64628, 18182})
BLOCKED_PORTS.update({64828, 65028})
BLOCKED_PORTS.update({65128, 65228, 65328, 65428})
BLOCKED_PORTS.update({65528})
BLOCKED_PORTS.update({63131})
BLOCKED_PORTS.update({63132, 18192})
BLOCKED_PORTS.update({63129, 18190, 63130, 18191})
BLOCKED_PORTS.update({64728, 18183, 64928, 18184})
BLOCKED_PORTS.update({64428, 18180})
BLOCKED_PORTS.update({63928, 18178})
FORBIDDEN_NAMES = {'.env', '__pycache__', '.pytest_cache', 'node_modules', '.git', '.runtime', '.DS_Store'}
ALLOWED_ENV = {'GA_DATABASE_URL', 'GA_REDIS_URL', 'GA_COOKIE_SECURE', 'GA_ALLOWED_ORIGINS', 'GA_SESSION_HOURS',
    'GA_ALLOWED_CONNECTOR_HOSTS', 'GA_DEVELOPMENT_CONNECTOR_URLS', 'GA_WMS_ALLOWED_ENDPOINTS', 'GA_WMS_ACCESS_GRANTS',
    'GA_CLOUD_DAILY_TOKEN_LIMIT', 'GA_CLOUD_DAILY_CALL_LIMIT', 'DASHSCOPE_API_KEY', 'DEEPSEEK_API_KEY',
    'GA_MAX_UPLOAD_BYTES', 'GA_LOCAL_RUNTIME_ROOT', 'GA_SPECIALIST_DIR', 'GA_SPECIALIST_PYTHON', 'GA_GEO_PYTHON', 'GA_AUDIO_PYTHON',
    'GA_AUDIO_WEIGHTS', 'GA_VAD_PYTHON', 'GA_VAD_WEIGHTS', 'GA_MEDIA_ALLOWED_ORIGINS', 'GA_MEDIA_API_SECRET_REF',
    'GA_MEDIA_RTSP_SECRET_REF', 'GA_MQTT_ALLOWED_BROKERS', 'GA_MQTT_RECEIVER_CONCURRENCY', 'GA_MQTT_RECEIVER_POLL_SECONDS',
    'GA_MQTT_RECEIVER_LEASE_SECONDS', 'GA_MQTT_RECEIVER_CONNECT_TIMEOUT_SECONDS'}


class Rejected(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise Rejected(code)


def canonical(path):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and path.resolve(strict=True) == path, 'noncanonical_path')
    return path


def file_record(path, *, content=False, private=False):
    path = canonical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1
            and not before.st_mode & 0o022, 'unsafe_file')
        if private:
            need(stat.S_IMODE(before.st_mode) == 0o600, 'private_configuration_required')
        digest = hashlib.sha256(); chunks = []
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
            if content:
                chunks.append(chunk); need(sum(map(len, chunks)) <= 2 * 1024 * 1024, 'metadata_too_large')
        after = os.fstat(fd); current = path.stat(follow_symlinks=False)
        need(all(getattr(before, k) == getattr(after, k) == getattr(current, k) for k in
            ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')), 'file_changed')
        result = {'bytes': before.st_size, 'sha256': digest.hexdigest()}
        return (b''.join(chunks), result) if content else result
    finally:
        os.close(fd)


def unique(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'duplicate_json_key'); result[key] = value
    return result


def read_json(path, *, private=False):
    raw, record = file_record(path, content=True, private=private)
    return json.loads(raw, object_pairs_hook=unique), record


def relative(name):
    need(isinstance(name, str) and '\\' not in name and not any(c in name for c in '\x00\r\n'), 'invalid_relative_path')
    path = PurePosixPath(name)
    need(path.parts and not path.is_absolute() and '..' not in path.parts and str(path) == name, 'invalid_relative_path')
    need(not any(p in FORBIDDEN_NAMES or p.startswith('.env') for p in path.parts)
        and path.suffix not in {'.pyc', '.pyo'}, 'cache_or_environment_in_release')
    return name


def load_release(manifest_path, expected_sha, *, root=ROOT):
    path = canonical(manifest_path); manifest, record = read_json(path)
    need(record['sha256'] == expected_sha, 'manifest_hash_changed')
    need(set(manifest) == {'format', 'release_id', 'backend_src', 'static_dist', 'setup_worker_scope', 'files', 'schema_model_sha256'}, 'manifest_fields_rejected')
    rid = manifest['release_id']
    need(isinstance(rid, str) and re.fullmatch('[a-z0-9][a-z0-9._-]{0,63}', rid) and rid not in {'.', '..'}, 'release_id_rejected')
    release = Path(root) / 'release-candidates' / rid
    need(path.parent == release and canonical(release) == release, 'not_a_candidate_release')
    need(manifest['format'] == 'generic-agents-candidate-v1' and manifest['backend_src'] == 'src'
        and manifest['static_dist'] == 'static/setup' and manifest['setup_worker_scope'] == 'workspace', 'release_contract_rejected')
    need(type(manifest['files']) is list and manifest['files'], 'empty_release')
    expected = {}
    for entry in manifest['files']:
        need(type(entry) is dict and set(entry) == {'path', 'bytes', 'sha256'}, 'file_entry_rejected')
        name = relative(entry['path'])
        need(name not in expected and type(entry['bytes']) is int and entry['bytes'] >= 0
            and isinstance(entry['sha256'], str) and re.fullmatch('[0-9a-f]{64}', entry['sha256']), 'file_entry_rejected')
        need(name != path.name, 'manifest_self_reference')
        expected[name] = {k: entry[k] for k in ('bytes', 'sha256')}
    actual = set()
    for directory, dirs, files in os.walk(release, followlinks=False):
        for name in dirs:
            child = Path(directory) / name; relative(str(child.relative_to(release)))
            need(not child.is_symlink() and not child.stat().st_mode & 0o022, 'unsafe_release_directory')
        for name in files:
            child = Path(directory) / name
            if child == path:
                continue
            name = relative(str(child.relative_to(release))); actual.add(name)
            need(name in expected and file_record(child) == expected[name], 'release_file_changed')
    need(actual == set(expected), 'release_file_set_changed')
    for name in ('src/generic_agents/__init__.py', 'src/generic_agents/cli.py', 'src/generic_agents/api.py',
        'src/generic_agents/models.py', 'static/setup/index.html'):
        need(name in expected, 'required_release_file_missing')
    need(manifest['schema_model_sha256'] == expected['src/generic_agents/models.py']['sha256'], 'model_hash_mismatch')
    class Assets(HTMLParser):
        def __init__(self):
            super().__init__(); self.refs = []
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'script' and values.get('src'):
                self.refs.append(values['src'])
            if tag == 'link' and values.get('rel') in {'stylesheet', 'modulepreload'}:
                self.refs.append(values.get('href', ''))
    assets = Assets(); assets.feed(file_record(release / 'static/setup/index.html', content=True)[0].decode('utf-8'))
    need(assets.refs, 'setup_assets_missing')
    for ref in assets.refs:
        url = urlsplit(ref)
        need(not url.scheme and not url.netloc and '%' not in url.path, 'external_or_aliased_asset')
        name = url.path.removeprefix('/setup/') if url.path.startswith('/setup/') else url.path
        need(not name.startswith('/') and 'static/setup/' + relative(name) in expected, 'setup_asset_missing')
    return {'root': release, 'manifest': manifest, 'record': record}


def private_directory(path):
    path = canonical(path); value = path.stat()
    need(path.is_dir() and value.st_uid == os.getuid() and stat.S_IMODE(value.st_mode) == 0o700, 'private_directory_required')
    return path


def validate_local_runtime_root(value, *, release_root):
    """Independent preflight before importing any candidate product module."""
    try:
        return _validate_local_runtime_root(value, release_root=release_root)
    except Rejected:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise Rejected('local_runtime_path_rejected') from None


def _validate_local_runtime_root(value, *, release_root):
    path = Path(value)
    need(path.is_absolute() and '..' not in path.parts and str(path) == value
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
        and path.resolve(strict=False) == path, 'local_runtime_path_rejected')
    need(not path.is_relative_to(release_root) and not release_root.is_relative_to(path), 'runtime_overlaps_release')
    need(not any((parent / name).is_file() for parent in (path, *path.parents)
        for name in ('source-manifest.json', 'release-manifest.json')), 'runtime_inside_frozen_release')
    nearest = path
    while not os.path.lexists(nearest):
        nearest = nearest.parent
    info = nearest.stat(follow_symlinks=False)
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022, 'unsafe_runtime_directory')
    for parent in nearest.parents:
        info = parent.stat(follow_symlinks=False)
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.getuid()}
            and (not info.st_mode & 0o022 or sticky_root), 'unsafe_runtime_ancestor')
    return path


def load_instance(path, *, root=ROOT):
    path = canonical(path); c, _ = read_json(path, private=True)
    need(c['format'] == 'generic-agents-instance-v1', 'instance_format_rejected')
    need(not set(c) - {'format', 'release_manifest', 'release_manifest_sha256', 'workspace_id', 'python',
        'host', 'port', 'cwd', 'state_dir', 'log_dir', 'storage_dir', 'environment', 'shutdown_grace_seconds', 'owned_database'}, 'instance_fields_rejected')
    home = private_directory(path.parent)
    protected = [Path(root) / p for p in ('.runtime', 'src', 'design', 'scripts', 'tests', 'deploy', 'release-candidates')]
    need(not Path(root).is_relative_to(home), 'instance_root_rejected')
    need(not any(home.is_relative_to(p) or p.is_relative_to(home) for p in protected), 'instance_overlaps_project_data')
    need(str(UUID(c['workspace_id'])) == c['workspace_id'], 'workspace_uuid_required')
    c['host'] = c.get('host', '127.0.0.1')
    need(c['host'] in {'127.0.0.1', '::1'} and type(c['port']) is int and 1024 <= c['port'] <= 65535, 'loopback_port_required')
    env = c['environment']; need(type(env) is dict and all(type(k) is str and type(v) is str for k, v in env.items()), 'environment_rejected')
    need(set(env) <= ALLOWED_ENV, 'environment_key_rejected')
    need({'GA_DATABASE_URL', 'GA_REDIS_URL'} <= set(env), 'explicit_service_urls_required')
    need(not any(ord(ch) < 32 or ord(ch) == 127 for ch in env['GA_DATABASE_URL']), 'database_url_control_character')
    db = urlsplit(env['GA_DATABASE_URL'])
    need(db.scheme == 'postgresql+psycopg' and db.hostname == '127.0.0.1' and db.port is not None
        and 1024 <= db.port <= 65535 and db.port not in BLOCKED_PORTS and not db.query and not db.fragment
        and db.password, 'candidate_database_endpoint_required')
    need(db.netloc.rsplit('@', 1)[-1] == '127.0.0.1:' + str(db.port), 'candidate_database_authority_required')
    need(re.fullmatch('/ga_candidate_[0-9a-f]{32}', db.path), 'candidate_database_name_required')
    database_id = db.path.removeprefix('/ga_candidate_')
    need(db.username == 'ga_candidate_app_' + database_id[:12], 'candidate_database_role_required')
    identity = c['owned_database']
    need(type(identity) is dict and set(identity) == {'system_identifier', 'oid', 'marker'}
        and isinstance(identity['system_identifier'], str) and re.fullmatch('[1-9][0-9]{0,19}', identity['system_identifier'])
        and type(identity['oid']) is int and 0 < identity['oid'] < 2**32
        and identity['marker'] == 'generic-agents-candidate:' + database_id, 'candidate_database_identity_required')
    need(c['port'] not in BLOCKED_PORTS | {db.port or 5432}, 'protected_or_database_port')
    paths = [private_directory(c[key]) for key in ('cwd', 'state_dir', 'log_dir', 'storage_dir')]
    need(len(set(paths)) == 4 and all(p.parent == home for p in paths), 'instance_directories_must_be_distinct_children')
    need(not any(os.path.lexists(p / '.env') for p in [home, *paths]), 'implicit_dotenv_forbidden')
    python = Path(c['python'])
    need(python.is_absolute() and '..' not in python.parts and python.is_file()
        and python.stat().st_mode & 0o111 and not python.stat().st_mode & 0o022, 'python_interpreter_rejected')
    need(type(c.get('shutdown_grace_seconds', 30)) in {int, float} and 1 <= c.get('shutdown_grace_seconds', 30) <= 300, 'shutdown_grace_rejected')
    release = load_release(Path(c['release_manifest']), c['release_manifest_sha256'], root=root)
    validate_local_runtime_root(env.get('GA_LOCAL_RUNTIME_ROOT', str(home / 'runtimes')), release_root=release['root'])
    return c, release


def service_commands(c, *, media=False, mqtt=False, legacy=False):
    base = [c['python'], '-B', '-m', 'generic_agents.cli']
    result = [('api', base + ['serve', '--host', c['host'], '--port', str(c['port'])]),
        ('setup', base + ['setup-worker', '--workspace-id', c['workspace_id']])]
    result += [(name, base + [command]) for name, command, enabled in
        [('media', 'media-worker', media), ('mqtt', 'mqtt-worker', mqtt), ('legacy', 'worker', legacy)] if enabled]
    return result


def child_environment(c, release):
    origin_host = '[' + c['host'] + ']' if c['host'] == '::1' else c['host']
    result = {'PATH': '/usr/bin:/bin', 'HOME': c['cwd'], 'TMPDIR': c['cwd'], 'LANG': 'C', 'LC_ALL': 'C',
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'PYTHONSAFEPATH': '1',
        'GA_CLOUD_DAILY_CALL_LIMIT': '0', 'GA_CLOUD_DAILY_TOKEN_LIMIT': '0', 'DASHSCOPE_API_KEY': '', 'DEEPSEEK_API_KEY': '',
        'GA_ALLOWED_CONNECTOR_HOSTS': '', 'GA_DEVELOPMENT_CONNECTOR_URLS': '', 'GA_MEDIA_ALLOWED_ORIGINS': '',
        'GA_MQTT_ALLOWED_BROKERS': '', 'GA_WMS_ALLOWED_ENDPOINTS': '', 'GA_WMS_ACCESS_GRANTS': '{}'}
    result.update(GA_COOKIE_SECURE='false', GA_ALLOWED_ORIGINS='http://' + origin_host + ':' + str(c['port']))
    result.update(c['environment']); result.update(PYTHONPATH=str(release['root'] / 'src'),
        GA_SETUP_BUILD_DIR=str(release['root'] / 'static/setup'), GA_STORAGE_DIR=c['storage_dir'])
    result['GA_LOCAL_RUNTIME_ROOT'] = str(validate_local_runtime_root(
        c['environment'].get('GA_LOCAL_RUNTIME_ROOT', str(Path(c['storage_dir']).parent / 'runtimes')),
        release_root=release['root']))
    return result


def process_snapshot():
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,uid=,lstart=,command='],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=3)
    rows = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 9)
        if len(fields) == 10:
            pid, ppid, pgid, uid = map(int, fields[:4])
            rows[pid] = {'pid': pid, 'ppid': ppid, 'pgid': pgid, 'uid': uid, 'started': ' '.join(fields[4:9]), 'command': fields[9]}
    return rows


def verify_database(c, *, engine_factory=None):
    """Run-only read-only identity fence. No migration, grant or fallback URL."""
    from sqlalchemy import create_engine, text
    need(not any(key.upper().startswith('PG') and value for key, value in os.environ.items()), 'inherited_libpq_environment_forbidden')
    url = c['environment']['GA_DATABASE_URL']; endpoint = urlsplit(url); identity = c['owned_database']
    factory = create_engine if engine_factory is None else engine_factory
    engine = factory(url, isolation_level='READ COMMITTED', hide_parameters=True,
        connect_args={'hostaddr': '127.0.0.1', 'port': endpoint.port, 'sslmode': 'disable', 'passfile': '/dev/null', 'connect_timeout': 2,
            'options': '-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
    try:
        with engine.connect() as db:
            try:
                db.exec_driver_sql('SET TRANSACTION READ ONLY')
                row = db.execute(text("SELECT current_database(),host(inet_server_addr()),inet_server_port(),d.oid,"
                    "shobj_description(d.oid,'pg_database'),s.system_identifier::text,current_user,"
                    "r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls,r.rolinherit,"
                    "EXISTS(SELECT 1 FROM pg_auth_members a WHERE a.member=r.oid),current_setting('transaction_read_only') "
                    "FROM pg_database d CROSS JOIN pg_control_system() s JOIN pg_roles r ON r.rolname=current_user "
                    "WHERE d.datname=current_database()")).one()
                need(tuple(row) == (endpoint.path[1:], '127.0.0.1', endpoint.port, identity['oid'], identity['marker'],
                    identity['system_identifier'], endpoint.username, False, False, False, False, False, False, False, 'on'),
                    'candidate_database_identity_or_privilege_mismatch')
            finally:
                db.rollback()
    except Exception:
        raise Rejected('candidate_database_readonly_preflight_failed') from None
    finally:
        engine.dispose()
    return {'verified': True, 'system_identifier': identity['system_identifier'], 'oid': identity['oid'],
        'database': endpoint.path[1:], 'port': endpoint.port, 'role': endpoint.username}


def write_state(directory, value):
    fd, name = tempfile.mkstemp(prefix='.candidate-state-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, sort_keys=True); output.write('\n'); output.flush(); os.fsync(output.fileno())
        os.replace(name, Path(directory) / 'candidate-state.json')
    finally:
        if os.path.exists(name): os.unlink(name)


IDENTITY_FIELDS = ('pid', 'pgid', 'uid', 'started', 'command')


def same_identity(current, original):
    return current is not None and all(current.get(k) == original.get(k) for k in IDENTITY_FIELDS)


def remember_members(rows, known):
    """Expand only from a currently matching in-memory identity; never rebind a PID."""
    selected = {pid for pid, original in known.items() if same_identity(rows.get(pid), original)}
    while True:
        found = {pid for pid, row in rows.items() if pid not in known and row['ppid'] in selected
            and row['uid'] == os.getuid() and pid > 1 and row['pgid'] > 1}
        if found <= selected: break
        for pid in found: known[pid] = dict(rows[pid])
        selected |= found
    return selected


def remaining_members(snapshot, group, expected):
    current = {pid: row for pid, row in snapshot().items() if row['pgid'] == group}
    need(group > 1 and all(pid in expected and row['uid'] == os.getuid()
        and same_identity(row, expected[pid]) for pid, row in current.items()), 'owned_group_identity_changed')
    return current


def signal_known_group(snapshot, killpg, group, signum, known):
    members = remaining_members(snapshot, group, known)
    if members:
        try: killpg(group, signum)
        except ProcessLookupError: pass
    return bool(members)


def supervise(c, release, *, media=False, mqtt=False, legacy=False, popen=subprocess.Popen,
        snapshot=process_snapshot, killpg=os.killpg, clock=time.monotonic, sleep=time.sleep, should_stop=lambda: False,
        readonly_preflight=verify_database):
    children = []; known = {}; errors = []; failure = None; phase = 'starting'; started = clock()
    state = {'release_id': release['manifest']['release_id'], 'schema_compatibility': 'unverified',
        'readiness': 'unverified', 'database_identity': 'unverified', 'owner': None, 'services': []}
    try:
        database = readonly_preflight(c)
        need(database.get('verified') is True, 'database_preflight_not_verified')
        state['database_identity'] = database
        state['owner'] = snapshot().get(os.getpid())
        need(state['owner'] is not None, 'supervisor_identity_unavailable')
        for name, command in service_commands(c, media=media, mqtt=mqtt, legacy=legacy):
            need(not os.path.lexists(Path(c['cwd']) / '.env'), 'implicit_dotenv_forbidden')
            fd = os.open(Path(c['log_dir']) / (name + '-' + str(time.time_ns()) + '.log'),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as log:
                child = popen(command, cwd=c['cwd'], env=child_environment(c, release), stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
                children.append((name, child)); need(child.poll() is None, 'service_exited_during_startup')
                identity = snapshot().get(child.pid)
                need(identity is not None and identity['pgid'] == child.pid and identity['uid'] == os.getuid()
                    and child.poll() is None, 'child_identity_unavailable')
                known[child.pid] = dict(identity)
                remember_members(snapshot(), known)
                state['services'].append({'name': name, 'identity': dict(identity)})
        while not should_stop():
            remember_members(snapshot(), known)
            need(all(child.poll() is None for _, child in children), 'supervised_service_exited')
            phase = 'supervised' if clock() - started >= 2 else 'starting'
            write_state(c['state_dir'], dict(state, phase=phase)); sleep(.2)
    except BaseException as exc:
        failure = type(exc).__name__
        if isinstance(exc, Rejected): failure = str(exc)
    finally:
        try:
            if children: remember_members(snapshot(), known)
        except Exception as exc: errors.append('members_' + type(exc).__name__)
        if any(child.pid not in known for _, child in children): errors.append('child_identity_unproven')
        # Only live matching members authorize signals. A held but reaped handle is insufficient.
        groups = {row['pgid'] for row in known.values()}
        for group in groups:
            try: signal_known_group(snapshot, killpg, group, signal.SIGTERM, known)
            except Exception as exc: errors.append('term_' + type(exc).__name__)
        until = clock() + c.get('shutdown_grace_seconds', 30)
        while known and clock() < until:
            pending = [child.poll() is None for _, child in children]
            try:
                rows = snapshot(); remember_members(rows, known)
                # Loss of a matching identity never proves disappearance. An
                # exiting child can briefly appear as '(python)' before poll
                # reaps it; changed/reused PIDs must also remain fail-closed.
                if (not any(pending) and not any(pid in rows for pid in known)
                        and not any(row['pgid'] in groups for row in rows.values())):
                    break
            except Exception as exc:
                errors.append('members_' + type(exc).__name__); break
            sleep(.05)
        try:
            # Reap a direct child that exited at the end of the grace period
            # before the fresh identity scan authorizes any KILL signal.
            for _, child in children: child.poll()
            remember_members(snapshot(), known)
        except Exception as exc: errors.append('members_' + type(exc).__name__)
        groups |= {row['pgid'] for row in known.values()}
        for group in groups:
            try: signal_known_group(snapshot, killpg, group, signal.SIGKILL, known)
            except Exception as exc: errors.append('kill_' + type(exc).__name__)
        for _, child in children:
            try:
                child.wait(timeout=5)
            except Exception as exc: errors.append('stop_' + type(exc).__name__)
        try:
            rows = snapshot()
            need(not any(pid in rows for pid in known), 'known_pid_present_or_reused_after_cleanup')
            need(not any(row['pgid'] in groups for row in rows.values()), 'owned_group_present_after_cleanup')
        except Exception as exc: errors.append('final_identity_' + type(exc).__name__)
        state.update(phase='failed' if failure or errors else 'stopped', error=failure, cleanup_errors=errors)
        try: write_state(c['state_dir'], state)
        except Exception as exc: errors.append('state_' + type(exc).__name__)
    return {'passed': not failure and not errors, 'error': failure, 'cleanup_errors': errors, 'readiness': 'unverified',
        'database_identity': state['database_identity']}


def status(c, *, snapshot=process_snapshot):
    path = Path(c['state_dir']) / 'candidate-state.json'
    if not path.exists(): return {'state': 'no_record', 'readiness': 'unverified', 'database_identity': 'unverified'}
    value, _ = read_json(path, private=True); current = snapshot(); owner = value.get('owner')
    matched = owner is not None and current.get(owner['pid']) == owner
    services = [{'name': item['name'], 'identity_matches': current.get(item['identity']['pid']) == item['identity']}
        for item in value.get('services', [])]
    observed = matched and bool(services) and all(item['identity_matches'] for item in services)
    return {'state': 'processes_present' if observed else 'not_verified', 'reported_phase': value['phase'],
        'supervisor_identity_matches': matched, 'services': services,
        'readiness': 'unverified', 'schema_compatibility': 'unverified', 'database_identity': 'unverified'}


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('action', choices=('check', 'run', 'status'))
    parser.add_argument('--instance', required=True, type=Path)
    for name in ('media', 'mqtt', 'legacy'): parser.add_argument('--' + name, action='store_true')
    parser.add_argument('--compare-to', type=Path); args = parser.parse_args()
    if args.action != 'run' and (args.media or args.mqtt or args.legacy): parser.error('service opt-ins require run')
    if args.compare_to and args.action != 'check': parser.error('--compare-to requires check')
    c, release = load_instance(args.instance)
    if args.action == 'check':
        result = {'verified_files': len(release['manifest']['files']), 'release_id': release['manifest']['release_id'],
            'schema_compatibility': 'unverified', 'database_identity': 'unverified'}
        if args.compare_to:
            old, _ = read_json(canonical(args.compare_to))
            result['model_definition_equal'] = old['schema_model_sha256'] == release['manifest']['schema_model_sha256']
    elif args.action == 'status': result = status(c)
    else:
        stop = False
        def cancel(*unused):
            nonlocal stop
            stop = True
        for signum in (signal.SIGTERM, signal.SIGINT): signal.signal(signum, cancel)
        lock = os.open(Path(c['state_dir']) / 'supervisor.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = supervise(c, release, media=args.media, mqtt=args.mqtt, legacy=args.legacy, should_stop=lambda: stop)
        finally: os.close(lock)
    print(json.dumps(result, sort_keys=True))
    if result.get('passed') is False: raise SystemExit(1)


if __name__ == '__main__':
    try: main()
    except (Rejected, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc) if isinstance(exc, Rejected) else type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
