"""Lifecycle checks use only fake launchers, fake releases and loopback ports."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from urllib.request import urlopen

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'managed_test_api.py'
spec = importlib.util.spec_from_file_location('managed_test_api_test', SCRIPT)
managed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(managed)


def make_release(tmp_path):
    root = (tmp_path / 'release').resolve()
    (root / 'src' / 'generic_agents').mkdir(parents=True)
    (root / 'static' / 'setup').mkdir(parents=True)
    (root / 'model-packs').mkdir()
    (root / 'runtime-packs').mkdir()
    (root / 'src' / 'generic_agents' / '__init__.py').write_text('')
    (root / 'src' / 'generic_agents' / 'api.py').write_text('''
import json, os, sys
from pathlib import Path
async def app(scope, receive, send):
    body=json.dumps({'release':True,'setup':os.environ.get('GA_SETUP_BUILD_DIR'),
      'original':os.environ.get('TEST_ORIGINAL_SETTING'),
      'storage':os.environ.get('GA_STORAGE_DIR'),
      'model':os.environ.get('GA_SPECIALIST_PYTHON'),
      'pack':(Path(__file__).resolve().parents[2]/'model-packs'/'pack.json').read_text(),
      'no_bytecode':sys.dont_write_bytecode}).encode()
    await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]})
    await send({'type':'http.response.body','body':body})
''')
    (root / 'static' / 'setup' / 'index.html').write_text('new static')
    (root / 'model-packs' / 'pack.json').write_text('{}')
    (root / 'runtime-packs' / 'runtime.json').write_text('{}')
    return root


def make_legacy(tmp_path):
    path = (tmp_path / 'legacy.py').resolve()
    path.write_text('''
import os, sys, uvicorn
from pathlib import Path
root=Path('/old-root')
os.environ['TEST_ORIGINAL_SETTING']='preserved'
os.environ['GA_SETUP_BUILD_DIR']='/old-static'
os.environ['GA_STORAGE_DIR']='/existing-storage'
os.environ['GA_SPECIALIST_PYTHON']='/existing-model-runtime'
sys.path.insert(0, str(root/'src'))
uvicorn.run('generic_agents.api:app',host='127.0.0.1',port=8766,log_level='error',access_log=False)
''')
    path.chmod(0o600)
    return path


def freeze(root):
    for path in sorted(root.rglob('*'), key=lambda item: len(item.parts), reverse=True):
        if not path.is_symlink():
            path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


@pytest.fixture(autouse=True)
def release_permissions_for_cleanup(tmp_path):
    yield
    root = tmp_path / 'release'
    if root.exists():
        root.chmod(0o700)
        for path in sorted(root.rglob('*'), key=lambda item: len(item.parts)):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)


def _port():
    for _ in range(30):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        if port not in {8766, 55486}:
            return port
    raise RuntimeError('no test port')


def command(root, legacy, state, port):
    freeze(root)
    return [sys.executable, str(SCRIPT), 'serve', '--release', str(root),
            '--legacy-launcher', str(legacy), '--legacy-sha256', hashlib.sha256(legacy.read_bytes()).hexdigest(),
            '--artifact-sha256', managed._artifact_digest(root), '--state-dir', str(state), '--port', str(port)]


def run_control(state, action):
    return subprocess.run([sys.executable, str(SCRIPT), action, '--state-dir', str(state)],
                          text=True, capture_output=True, timeout=5)


@contextmanager
def short_state():
    with tempfile.TemporaryDirectory(prefix='ga-managed-', dir=str(Path('/tmp').resolve())) as value:
        state = Path(value).resolve()
        state.chmod(0o700)
        yield state


def test_real_loopback_lifecycle_uses_release_and_preserves_original_settings(tmp_path):
    root, legacy = make_release(tmp_path), make_legacy(tmp_path)
    with short_state() as state:
        port = _port()
        process = subprocess.Popen(command(root, legacy, state, port), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, env={'PATH': os.environ.get('PATH', '')})
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail('isolated server exited during startup: '+process.stderr.read().decode(errors='replace'))
                if (state / 'active.json').exists():
                    result = run_control(state, 'status')
                    if result.returncode == 0 and '已就绪' in result.stdout:
                        break
                time.sleep(.05)
            else:
                pytest.fail('isolated server did not become ready')
            with urlopen(f'http://127.0.0.1:{port}/health', timeout=3) as reply:
                body = json.load(reply)
            assert body == {'release': True, 'setup': str(root / 'static' / 'setup'),
                            'original': 'preserved', 'storage': '/existing-storage',
                            'model': '/existing-model-runtime', 'pack': '{}', 'no_bytecode': True}
            # A stale or unauthenticated request cannot stop this instance.
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.connect(str(state / 'control.sock'))
                peer.sendall(b'{"command":"stop","token":"wrong","run_id":"wrong"}\n')
                assert peer.recv(100) == b''
            assert run_control(state, 'status').returncode == 0
            assert run_control(state, 'stop').returncode == 0
            assert process.wait(timeout=10) == 0
            assert not (state / 'active.json').exists()
            assert not (state / 'control.sock').exists()
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('127.0.0.1', port))
        finally:
            if process.poll() is None:
                process.kill()  # this test owns the unreaped direct child
                process.wait(timeout=5)


def test_port_conflict_never_stops_other_listener_or_writes_state(tmp_path):
    root, legacy = make_release(tmp_path), make_legacy(tmp_path)
    with short_state() as state, socket.socket() as holder:
        holder.bind(('127.0.0.1', 0)); holder.listen()
        port = holder.getsockname()[1]
        assert port not in {8766, 55486}
        result = subprocess.run(command(root, legacy, state, port), text=True, capture_output=True, timeout=5)
        assert result.returncode != 0
        assert not (state / 'active.json').exists()
        with socket.create_connection(('127.0.0.1', port), timeout=2):
            pass


def test_stale_state_is_not_replaced_or_used_to_signal(tmp_path):
    root, legacy = make_release(tmp_path), make_legacy(tmp_path)
    with short_state() as state:
        stale = state / 'active.json'; stale.write_text('stale'); stale.chmod(0o600)
        result = subprocess.run(command(root, legacy, state, _port()), text=True, capture_output=True, timeout=5)
        assert result.returncode != 0 and stale.read_text() == 'stale'
        assert run_control(state, 'stop').returncode != 0


def test_artifact_digest_covers_pack_files_and_rejects_environment_files(tmp_path):
    root = make_release(tmp_path)
    freeze(root)
    before = managed._artifact_digest(root)
    pack = root / 'model-packs' / 'pack.json'
    pack.chmod(0o600); pack.write_text('{"v":2}'); pack.chmod(0o444)
    assert managed._artifact_digest(root) != before
    (root / 'src').chmod(0o700)
    (root / 'src' / '.env').write_text('test-only')
    (root / 'src' / '.env').chmod(0o444)
    (root / 'src').chmod(0o555)
    with pytest.raises(managed.Refused):
        managed._artifact_digest(root)


def test_runtime_link_must_match_explicit_existing_runtime(tmp_path):
    root = make_release(tmp_path)
    runtime = (tmp_path / 'runtime').resolve(); runtime.mkdir()
    (root / '.runtime').symlink_to(runtime, target_is_directory=True)
    with pytest.raises(managed.Refused):
        managed._runtime_link(root, None)
    managed._runtime_link(root, runtime)
    other = (tmp_path / 'other').resolve(); other.mkdir()
    with pytest.raises(managed.Refused):
        managed._runtime_link(root, other)


def test_changed_original_launcher_or_release_refuses_before_start(tmp_path):
    root, legacy = make_release(tmp_path), make_legacy(tmp_path)
    with short_state() as state:
        cmd = command(root, legacy, state, _port())
        legacy.write_text(legacy.read_text() + '\n# changed\n'); legacy.chmod(0o600)
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=5)
        assert result.returncode != 0 and not (state / 'active.json').exists()


def test_launcher_with_unexpected_worker_or_subprocess_is_rejected(tmp_path):
    root, legacy = make_release(tmp_path), make_legacy(tmp_path)
    legacy.write_text('import subprocess, uvicorn\nsubprocess.run(["true"])\n'
                      'uvicorn.run("generic_agents.api:app",host="127.0.0.1",port=8766)\n')
    legacy.chmod(0o600)
    with short_state() as state:
        result = subprocess.run(command(root, legacy, state, _port()),
                                text=True, capture_output=True, timeout=5)
        assert result.returncode != 0 and not (state / 'active.json').exists()


def test_launcher_extra_uvicorn_options_are_not_silently_dropped(tmp_path):
    root, legacy = make_release(tmp_path), make_legacy(tmp_path)
    legacy.write_text('import uvicorn\n'
                      'uvicorn.run("generic_agents.api:app",host="127.0.0.1",port=8766,'
                      'log_level="error",access_log=False,reload=True)\n')
    legacy.chmod(0o600)
    with short_state() as state:
        result = subprocess.run(command(root, legacy, state, _port()),
                                text=True, capture_output=True, timeout=5)
        assert result.returncode != 0 and not (state / 'active.json').exists()


def test_runtime_configuration_is_not_rendered_on_failure(tmp_path):
    root, legacy = make_release(tmp_path), make_legacy(tmp_path)
    legacy.write_text('import os, uvicorn\nraise RuntimeError("SYNTHETIC_PRIVATE_MARKER")\n'
                      'uvicorn.run("generic_agents.api:app",host="127.0.0.1",port=8766)\n')
    legacy.chmod(0o600)
    with short_state() as state:
        result = subprocess.run(command(root, legacy, state, _port()),
                                text=True, capture_output=True, timeout=5)
        assert result.returncode != 0
        assert 'SYNTHETIC_PRIVATE_MARKER' not in result.stdout + result.stderr


def test_changed_release_during_legacy_initialization_refuses_before_api_import(tmp_path):
    root, legacy = make_release(tmp_path), make_legacy(tmp_path)
    target = root / 'model-packs' / 'pack.json'
    legacy.write_text('from pathlib import Path\nimport uvicorn\n'
                      f'Path({str(target)!r}).chmod(0o600)\n'
                      f'Path({str(target)!r}).write_text("changed")\n'
                      f'Path({str(target)!r}).chmod(0o444)\n'
                      'uvicorn.run("generic_agents.api:app",host="127.0.0.1",port=8766)\n')
    legacy.chmod(0o600)
    with short_state() as state:
        result = subprocess.run(command(root, legacy, state, _port()),
                                text=True, capture_output=True, timeout=5)
        assert result.returncode != 0
        assert target.read_text() == 'changed'
        assert not (state / 'active.json').exists()
        assert not (state / 'control.sock').exists()
