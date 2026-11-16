"""Temporary candidate files and fake process handles only; no live services."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location('candidate_runtime_under_test', Path(__file__).parents[1] / 'scripts/candidate_runtime.py')
runtime = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(runtime)


def put(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data); path.chmod(mode)


def json_file(path, value):
    put(path, (json.dumps(value, sort_keys=True) + '\n').encode())


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / 'project'; release = root / 'release-candidates/r1'; home = tmp_path / 'instance'
    home.mkdir(mode=0o700)
    files = {'src/generic_agents/__init__.py': b'', 'src/generic_agents/api.py': b'# api\n',
        'src/generic_agents/cli.py': b'# cli\n', 'src/generic_agents/models.py': b'# models\n',
        'static/setup/index.html': b'<script type="module" src="/setup/assets/app.js"></script>',
        'static/setup/assets/app.js': b'console.log("candidate");'}
    for name, data in files.items(): put(release / name, data)
    manifest = {'format': 'generic-agents-candidate-v1', 'release_id': 'r1', 'backend_src': 'src',
        'static_dist': 'static/setup', 'setup_worker_scope': 'workspace',
        'files': [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in files.items()],
        'schema_model_sha256': hashlib.sha256(files['src/generic_agents/models.py']).hexdigest()}
    manifest_path = release / 'manifest.json'; json_file(manifest_path, manifest)
    put(home / 'python', b'fake interpreter, never execute', 0o700)
    c = {'format': 'generic-agents-instance-v1', 'release_manifest': str(manifest_path),
        'release_manifest_sha256': runtime.file_record(manifest_path)['sha256'],
        'workspace_id': '12345678-1234-4234-8234-123456789abc', 'python': str(home / 'python'), 'port': 18081,
        'environment': {'GA_DATABASE_URL': 'postgresql+psycopg://ga_candidate_app_fedcba987654:fake@127.0.0.1:50999/ga_candidate_fedcba987654432189ab123456789abc',
            'GA_REDIS_URL': 'redis://127.0.0.1:1/0'}, 'shutdown_grace_seconds': 1,
        'owned_database': {'system_identifier': '123456789', 'oid': 16384, 'marker': 'generic-agents-candidate:fedcba987654432189ab123456789abc'}}
    for key in ('cwd', 'state_dir', 'log_dir', 'storage_dir'):
        p = home / key; p.mkdir(mode=0o700); c[key] = str(p)
    config = home / 'instance.json'; json_file(config, c)
    return root, release, manifest_path, manifest, config, c


def test_full_candidate_and_scoped_commands(candidate):
    root, release, _, _, config, _ = candidate
    c, bundle = runtime.load_instance(config, root=root)
    assert c['host'] == '127.0.0.1'
    commands = dict(runtime.service_commands(c))
    assert set(commands) == {'api', 'setup'}
    assert commands['setup'][-3:] == ['setup-worker', '--workspace-id', c['workspace_id']]
    assert commands['api'][-4:] == ['--host', '127.0.0.1', '--port', '18081']
    assert set(dict(runtime.service_commands(c, mqtt=True))) == {'api', 'setup', 'mqtt'}
    env = runtime.child_environment(c, bundle)
    assert env['PYTHONPATH'] == str(release / 'src') and env['GA_SETUP_BUILD_DIR'] == str(release / 'static/setup')
    assert env['GA_ALLOWED_ORIGINS'] == 'http://127.0.0.1:18081' and env['GA_COOKIE_SECURE'] == 'false'
    assert env['GA_CLOUD_DAILY_CALL_LIMIT'] == '0' and env['DASHSCOPE_API_KEY'] == ''
    assert env['GA_LOCAL_RUNTIME_ROOT'] == str(config.parent / 'runtimes')
    assert not Path(env['GA_LOCAL_RUNTIME_ROOT']).exists()


def test_explicit_local_component_root_survives_service_environment(candidate):
    root, release, _, _, config, c = candidate
    components = config.parent.parent / 'local-components'; components.mkdir(mode=0o700)
    c['environment']['GA_LOCAL_RUNTIME_ROOT'] = str(components); json_file(config, c)
    loaded, bundle = runtime.load_instance(config, root=root)
    assert runtime.child_environment(loaded, bundle)['GA_LOCAL_RUNTIME_ROOT'] == str(components)
    assert not tuple(components.iterdir())


@pytest.mark.parametrize('mutation', ['relative', 'dotdot', 'alias', 'writable', 'file', 'release', 'old-release', 'unsafe-parent'])
def test_bad_local_component_root_is_rejected_before_service_start(candidate, mutation):
    root, release, _, _, config, c = candidate
    components = config.parent.parent / 'local-components'; components.mkdir(mode=0o700)
    selected = str(components)
    if mutation == 'relative': selected = 'local-components'
    elif mutation == 'dotdot': selected += '/../local-components'
    elif mutation == 'alias':
        alias = components.with_name('alias'); alias.symlink_to(components, target_is_directory=True); selected = str(alias)
    elif mutation == 'writable': components.chmod(0o777)
    elif mutation == 'file': components.rmdir(); components.write_text('not a directory')
    elif mutation == 'release': selected = str(release / 'runtimes')
    elif mutation == 'old-release':
        put(components / 'release-manifest.json', b'{}'); selected = str(components / 'runtimes')
    elif mutation == 'unsafe-parent': components.chmod(0o777); selected = str(components / 'missing/runtimes')
    c['environment']['GA_LOCAL_RUNTIME_ROOT'] = selected; json_file(config, c)
    with pytest.raises(runtime.Rejected): runtime.load_instance(config, root=root)


def test_local_root_filesystem_error_has_no_sensitive_exception_context(candidate, monkeypatch):
    root, release, _, _, config, c = candidate
    def fail(*a, **k): raise PermissionError('private-error-with-password')
    monkeypatch.setattr(Path, 'resolve', fail)
    with pytest.raises(runtime.Rejected) as error:
        runtime.validate_local_runtime_root(str(config.parent / 'components'), release_root=release)
    assert str(error.value) == 'local_runtime_path_rejected' and error.value.__suppress_context__


@pytest.mark.parametrize('mutation', ['extra', 'missing', 'changed', 'symlink', 'cache'])
def test_manifest_rejects_changed_file_set(candidate, mutation):
    root, release, manifest, _, _, c = candidate
    target = release / 'static/setup/assets/app.js'
    if mutation == 'extra': put(release / 'extra.txt', b'unknown')
    elif mutation == 'missing': target.unlink()
    elif mutation == 'changed': put(target, b'changed')
    elif mutation == 'symlink': target.unlink(); target.symlink_to(release / 'src/generic_agents/cli.py')
    else: put(release / 'src/generic_agents/__pycache__/bad.pyc', b'cache')
    with pytest.raises(runtime.Rejected): runtime.load_release(manifest, c['release_manifest_sha256'], root=root)


def test_model_hash_is_actual_file_hash_not_compatibility_claim(candidate):
    root, _, path, manifest, _, _ = candidate
    manifest['schema_model_sha256'] = '0' * 64; json_file(path, manifest)
    with pytest.raises(runtime.Rejected, match='model_hash_mismatch'):
        runtime.load_release(path, runtime.file_record(path)['sha256'], root=root)


@pytest.mark.parametrize('port', [8765, 4187, 63077, 55432, 50999, 51028, 60928, 61028, 61128, 61228, 61328, 61428, 61528, 61628, 61728, 61828, 61928, 62028, 62128, 62228, 62328, 62428, 62528, 62628, 62728, 62828, 62928, 63028, 63128, 63228, 63328, 63428, 63528, 63628, 63728, 63828, 64028, 64128, 18157, 18158, 18159, 18160, 18161, 18162, 18163, 18164, 18165, 18166, 18167, 18168, 18169, 18170, 18171, 18172, 18173, 18174, 18175, 18176, 18177])
def test_forbidden_api_ports(candidate, port):
    root, _, _, _, config, c = candidate; c['port'] = port; json_file(config, c)
    with pytest.raises(runtime.Rejected, match='protected_or_database_port'): runtime.load_instance(config, root=root)


@pytest.mark.parametrize('port', [64228, 18179, 64528, 18181, 64628, 18182, 64428, 18180, 63928, 18178])
def test_pre_api_failed_browser_attempt_is_spent(candidate, port):
    test_forbidden_api_ports(candidate, port)


@pytest.mark.parametrize('mutation', ['unscoped', 'env', 'unknown_ga_env', 'cwd_env', 'instance_env', 'directory_overlap'])
def test_explicit_private_instance(candidate, mutation):
    root, _, _, _, config, c = candidate
    if mutation == 'unscoped': c['workspace_id'] = ''
    elif mutation == 'env': c['environment']['PYTHONPATH'] = '/untrusted'
    elif mutation == 'unknown_ga_env': c['environment']['GA_UNRECOGNIZED_SWITCH'] = 'true'
    elif mutation == 'cwd_env': put(Path(c['cwd']) / '.env', b'fake=not_read')
    elif mutation == 'instance_env': put(config.parent / '.env', b'fake=not_read')
    else: c['state_dir'] = c['log_dir']
    json_file(config, c)
    with pytest.raises((runtime.Rejected, ValueError)): runtime.load_instance(config, root=root)


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, value): self.now += value


class Processes:
    def __init__(self, *, fail_at=None, leader_exit=False):
        self.items = []; self.signals = []; self.fail_at = fail_at; self.leader_exit = leader_exit
    def popen(self, command, **kwargs):
        if len(self.items) == self.fail_at: raise OSError('fake spawn failure')
        parent = self
        class Child:
            pid = 2000 + len(parent.items)
            alive = True
            group = True
            def poll(self): return None if self.alive else 1
            def wait(self, timeout):
                if self.alive: raise subprocess.TimeoutExpired('fake', timeout)
                return 1
        child = Child(); self.items.append((child, command, kwargs)); return child
    def snapshot(self):
        def identity(pid, ppid, command):
            return {'pid': pid, 'ppid': ppid, 'pgid': pid, 'uid': os.getuid(), 'started': 'fixed-start', 'command': command}
        rows = {os.getpid(): identity(os.getpid(), 1, 'fake supervisor')}
        rows.update({p.pid: identity(p.pid, os.getpid(), ' '.join(command)) for p, command, _ in self.items if p.alive})
        if self.leader_exit:
            rows.update({p.pid + 1000: identity(p.pid + 1000, p.pid, 'known old group member') | {'pgid': p.pid}
                for p, _, _ in self.items if p.group})
        return rows
    def killpg(self, pid, signum):
        child = next(p for p, _, _ in self.items if p.pid == pid)
        if not child.group: raise ProcessLookupError()
        self.signals.append((pid, signum))
        if signum == signal.SIGTERM:
            child.alive = False; child.group = self.leader_exit
        elif signum == signal.SIGKILL: child.alive = False; child.group = False


def run_fake(candidate, monkeypatch, *, processes=None, state_error=False):
    root, _, _, _, config, _ = candidate; c, bundle = runtime.load_instance(config, root=root)
    clock = Clock(); ps = processes or Processes()
    if state_error:
        def failed(*args): raise OSError('fake state write')
        monkeypatch.setattr(runtime, 'write_state', failed)
    result = runtime.supervise(c, bundle, popen=ps.popen, snapshot=ps.snapshot, killpg=ps.killpg,
        clock=clock, sleep=clock.sleep, should_stop=lambda: clock.now >= .4,
        readonly_preflight=lambda c: {'verified': True})
    return result, ps, c


def test_foreground_owner_stops_direct_groups(candidate, monkeypatch):
    result, ps, _ = run_fake(candidate, monkeypatch)
    assert result == {'passed': True, 'error': None, 'cleanup_errors': [], 'readiness': 'unverified', 'database_identity': {'verified': True}}
    assert all(not p.alive and not p.group for p, _, _ in ps.items)
    assert all(k['start_new_session'] and k['close_fds'] for _, _, k in ps.items)


def test_exiting_placeholder_waits_for_held_child_reap_before_kill_phase(candidate, monkeypatch):
    class Exiting(Processes):
        def popen(self, command, **kwargs):
            child = super().popen(command, **kwargs)
            child.exiting = child.placeholder_seen = child.reaped = False
            def poll():
                if child.exiting and child.placeholder_seen:
                    child.alive = child.group = False; child.reaped = True
                    return 0
                return None if child.alive else 0
            def wait(timeout):
                if poll() is None: raise subprocess.TimeoutExpired('fake held child', timeout)
                return 0
            child.poll = poll; child.wait = wait
            return child
        def killpg(self, pid, signum):
            self.signals.append((pid, signum))
            child = next(p for p, _, _ in self.items if p.pid == pid)
            assert signum == signal.SIGTERM, 'normal exit must be reaped without KILL'
            child.exiting = True
        def snapshot(self):
            rows = super().snapshot()
            for child, _, _ in self.items:
                if child.exiting and not child.reaped:
                    rows[child.pid]['command'] = '(python)'
                    child.placeholder_seen = True
            return rows
    ps = Exiting(); result, _, c = run_fake(candidate, monkeypatch, processes=ps)
    assert result['passed'] and result['cleanup_errors'] == []
    assert all(p.reaped for p, _, _ in ps.items)
    assert len(ps.signals) == 2 and all(sig == signal.SIGTERM for _, sig in ps.signals)
    state = json.loads((Path(c['state_dir']) / 'candidate-state.json').read_bytes())
    assert state['phase'] == 'stopped' and state['cleanup_errors'] == []


def test_absent_ps_row_does_not_replace_polling_the_held_child(candidate, monkeypatch):
    class DelayedReap(Processes):
        def popen(self, command, **kwargs):
            child = super().popen(command, **kwargs); child.polls_left = None; child.reaped = False
            def poll():
                if child.polls_left is not None:
                    child.polls_left -= 1
                    if child.polls_left <= 0:
                        child.alive = child.group = False; child.reaped = True; return 0
                return None if child.alive else 0
            def wait(timeout):
                if not child.reaped: raise subprocess.TimeoutExpired('fake held child', timeout)
                return 0
            child.poll = poll; child.wait = wait
            return child
        def killpg(self, pid, signum):
            self.signals.append((pid, signum))
            child = next(p for p, _, _ in self.items if p.pid == pid)
            assert signum == signal.SIGTERM
            child.polls_left = 3
        def snapshot(self):
            rows = super().snapshot()
            for child, _, _ in self.items:
                if child.polls_left is not None: rows.pop(child.pid, None)
            return rows
    ps = DelayedReap(); result, _, _ = run_fake(candidate, monkeypatch, processes=ps)
    assert result['passed'] and result['cleanup_errors'] == []
    assert all(p.reaped for p, _, _ in ps.items)


def test_spawn_failure_still_stops_prior_handle(candidate, monkeypatch):
    result, ps, _ = run_fake(candidate, monkeypatch, processes=Processes(fail_at=1))
    assert not result['passed'] and all(not p.group for p, _, _ in ps.items)


def test_state_write_failure_cannot_skip_cleanup(candidate, monkeypatch):
    result, ps, _ = run_fake(candidate, monkeypatch, state_error=True)
    assert not result['passed'] and all(not p.group for p, _, _ in ps.items)


def test_leader_exit_does_not_hide_live_group(candidate, monkeypatch):
    result, ps, _ = run_fake(candidate, monkeypatch, processes=Processes(leader_exit=True))
    assert result['passed'] and all(not p.group for p, _, _ in ps.items)
    assert sum(sig == signal.SIGKILL for _, sig in ps.signals) == 2


def test_status_rejects_reused_pid_and_never_claims_ready(candidate):
    root, _, _, _, config, _ = candidate; c, _ = runtime.load_instance(config, root=root)
    owner = {'pid': 123, 'uid': os.getuid(), 'started': 'before', 'command': 'candidate', 'pgid': 123, 'ppid': 1}
    runtime.write_state(c['state_dir'], {'phase': 'supervised', 'owner': owner, 'services': []})
    out = runtime.status(c, snapshot=lambda: {123: dict(owner, started='after')})
    assert out['supervisor_identity_matches'] is False and out['readiness'] == 'unverified'
    assert out['state'] == 'not_verified' and out['reported_phase'] == 'supervised'


def test_release_must_be_in_candidate_tree(candidate):
    root, _, path, _, _, c = candidate
    with pytest.raises(runtime.Rejected, match='not_a_candidate_release'):
        runtime.load_release(path, c['release_manifest_sha256'], root=root / 'another-root')


def test_exited_startup_is_failure(candidate, monkeypatch):
    class Exits(Processes):
        def popen(self, *args, **kwargs):
            child = super().popen(*args, **kwargs); child.alive = False; return child
    result, ps, _ = run_fake(candidate, monkeypatch, processes=Exits())
    assert not result['passed'] and result['error'] == 'service_exited_during_startup'
    assert 'child_identity_unproven' in result['cleanup_errors'] and ps.signals == []


def test_detached_helper_cleanup_is_bound_to_owned_descendants(candidate, monkeypatch):
    class Helpers(Processes):
        helper_alive = True
        def snapshot(self):
            rows = super().snapshot()
            if self.items and self.helper_alive:
                rows[3000] = {'pid': 3000, 'ppid': self.items[0][0].pid, 'pgid': 3000,
                    'uid': os.getuid(), 'started': 'helper-start', 'command': 'fake specialist'}
            return rows
        def killpg(self, pid, signum):
            if pid == 3000:
                self.signals.append((pid, signum)); self.helper_alive = False
            else: super().killpg(pid, signum)
    ps = Helpers(); result, ps, _ = run_fake(candidate, monkeypatch, processes=ps)
    assert result['passed'] and not ps.helper_alive and (3000, signal.SIGTERM) in ps.signals


def test_foreign_or_reused_descendant_identity_rejected():
    original = {'pid': 3, 'ppid': 2, 'pgid': 3, 'uid': os.getuid(), 'started': 'old', 'command': 'helper'}
    with pytest.raises(runtime.Rejected, match='owned_group_identity_changed'):
        runtime.remaining_members(lambda: {3: dict(original, started='new')}, 3, {3: original})
    known = {3: original}
    runtime.remember_members({3: dict(original, started='new'), 4: dict(original, pid=4, ppid=3)}, known)
    assert set(known) == {3}


@pytest.mark.parametrize('old_members', [False, True])
def test_reaped_leader_pid_reuse_never_signals_foreign_tree(candidate, monkeypatch, old_members):
    class Reused(Processes):
        calls = 0
        foreign_signals = []
        def snapshot(self):
            self.calls += 1; rows = super().snapshot()
            if self.calls >= 7 and self.items:
                child = self.items[0][0]; child.alive = False
                rows[child.pid] = {'pid': child.pid, 'ppid': 1, 'pgid': child.pid, 'uid': os.getuid(),
                    'started': 'new foreign start', 'command': 'foreign process'}
                rows[4000] = dict(rows[child.pid], pid=4000, ppid=child.pid, pgid=4000)
                if old_members:
                    rows[3000] = {'pid': 3000, 'ppid': child.pid, 'pgid': child.pid, 'uid': os.getuid(),
                        'started': 'old member', 'command': 'old helper'}
            elif old_members and self.items:
                rows[3000] = {'pid': 3000, 'ppid': self.items[0][0].pid, 'pgid': self.items[0][0].pid,
                    'uid': os.getuid(), 'started': 'old member', 'command': 'old helper'}
            return rows
        def killpg(self, pid, sig):
            if pid in {2000, 4000}: self.foreign_signals.append((pid, sig))
            else: super().killpg(pid, sig)
    ps = Reused(); result, ps, _ = run_fake(candidate, monkeypatch, processes=ps)
    assert not result['passed'] and ps.foreign_signals == [] and result['cleanup_errors']


def test_grandchild_group_drift_does_not_authorize_new_group():
    parent = {'pid': 2, 'ppid': 1, 'pgid': 2, 'uid': os.getuid(), 'started': 'parent', 'command': 'worker'}
    child = dict(parent, pid=3, ppid=2, pgid=3, started='child', command='helper')
    known = {2: parent}; runtime.remember_members({2: parent, 3: child}, known)
    drifted = dict(child, pgid=4); new_grandchild = dict(child, pid=5, ppid=3, pgid=5, started='unknown')
    runtime.remember_members({2: parent, 3: drifted, 5: new_grandchild}, known)
    assert 5 not in known and known[3]['pgid'] == 3
    signals = []
    with pytest.raises(runtime.Rejected):
        runtime.signal_known_group(lambda: {3: drifted}, lambda *v: signals.append(v), 4, signal.SIGTERM, known)
    assert signals == []


@pytest.mark.parametrize('change', ['localhost', 'ipv6', 'oldport', 'normaldb', 'role', 'query', 'fragment', 'leading_port'])
def test_database_target_must_be_new_exact_candidate(candidate, change):
    root, _, _, _, config, c = candidate; url = c['environment']['GA_DATABASE_URL']
    replacements = {'localhost': ('127.0.0.1', 'localhost'), 'ipv6': ('127.0.0.1', '[::1]'),
        'oldport': (':50999/', ':50728/'), 'normaldb': ('/ga_candidate_fedcba987654432189ab123456789abc', '/generic_agents'),
        'role': ('ga_candidate_app_fedcba987654:', 'other_app:'), 'leading_port': (':50999/', ':050999/')}
    if change == 'query': url += '?service=other'
    elif change == 'fragment': url += '#other'
    else: url = url.replace(*replacements[change])
    c['environment']['GA_DATABASE_URL'] = url; json_file(config, c)
    with pytest.raises(runtime.Rejected): runtime.load_instance(config, root=root)


@pytest.mark.parametrize('field,value', [('system_identifier', 'bad'), ('oid', True), ('marker', 'other')])
def test_declared_identity_has_strict_shape(candidate, field, value):
    root, _, _, _, config, c = candidate; c['owned_database'][field] = value; json_file(config, c)
    with pytest.raises(runtime.Rejected): runtime.load_instance(config, root=root)


def fake_identity_engine(c, row):
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *unused): return False
        def exec_driver_sql(self, sql): calls.append(sql)
        def execute(self, sql): calls.append(str(sql)); return type('Result', (), {'one': lambda self: row})()
        def rollback(self): calls.append('rollback')
    class Engine:
        def connect(self): return Connection()
        def dispose(self): calls.append('dispose')
    def factory(url, **kwargs):
        assert url == c['environment']['GA_DATABASE_URL']
        assert kwargs['isolation_level'] == 'READ COMMITTED' and kwargs['connect_args']['hostaddr'] == '127.0.0.1'
        assert kwargs['connect_args']['passfile'] == '/dev/null' and kwargs['connect_args']['sslmode'] == 'disable'
        assert kwargs['connect_args']['connect_timeout'] == 2
        assert kwargs['connect_args']['options'] == '-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'
        return Engine()
    return factory, calls


@pytest.mark.parametrize('index', [None, 0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
def test_readonly_live_fence_uses_real_observed_identity_and_privileges(candidate, monkeypatch, index):
    _, _, _, _, _, c = candidate
    for key in list(os.environ):
        if key.upper().startswith('PG'): monkeypatch.delenv(key)
    row = ['ga_candidate_fedcba987654432189ab123456789abc', '127.0.0.1', 50999, 16384,
        c['owned_database']['marker'], '123456789', 'ga_candidate_app_fedcba987654',
        False, False, False, False, False, False, False, 'on']
    if index is not None: row[index] = True if 7 <= index <= 13 else 'wrong'
    factory, calls = fake_identity_engine(c, row)
    if index is None: assert runtime.verify_database(c, engine_factory=factory)['verified'] is True
    else:
        with pytest.raises(runtime.Rejected): runtime.verify_database(c, engine_factory=factory)
    assert calls[0] == 'SET TRANSACTION READ ONLY' and calls[-2:] == ['rollback', 'dispose']
    assert not any(x.startswith(('CREATE ', 'ALTER ', 'INSERT ', 'UPDATE ', 'DELETE ')) for x in calls)


def test_database_failure_fences_every_spawn(candidate):
    root, _, _, _, config, _ = candidate; c, bundle = runtime.load_instance(config, root=root)
    def forbidden(*a, **k): raise AssertionError('must not spawn')
    result = runtime.supervise(c, bundle, popen=forbidden, snapshot=lambda: {}, killpg=forbidden,
        readonly_preflight=lambda c: {'verified': False})
    assert not result['passed'] and result['error'] == 'database_preflight_not_verified'
