"""Only synthetic files/MockTransport; never run installers or load models."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import venv
import zipfile

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.engine import Engine

from generic_agents import config

ROOT = Path(__file__).absolute().parents[1]
REAL_CLIENT = httpx.Client
REAL_SEND = httpx.Client.send


def load(name):
    spec = importlib.util.spec_from_file_location('_test_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audio = load('prepare_local_audio')
vad = load('prepare_local_vad')


@pytest.fixture(autouse=True)
def pure_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure scripts tests forbid settings/database/network/process execution')
    for target, attr in ((Engine, 'connect'), (Engine, 'begin'), (socket.socket, 'connect'),
                         (socket.socket, 'connect_ex'), (subprocess, 'Popen'), (subprocess, 'run'),
                         (venv.EnvBuilder, 'create'), (httpx.Client, 'send'), (config, 'Settings')):
        monkeypatch.setattr(target, attr, forbidden)


def inventory(root):
    return {str(path.relative_to(root)): (path.stat().st_ino, path.stat().st_size,
            path.stat().st_mtime_ns, path.stat().st_ctime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest())
            for path in root.rglob('*') if path.is_file()}


def no_writes(monkeypatch):
    original_open = os.open
    def readonly_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            pytest.fail('matching or rejected cache must be strictly readonly')
        return original_open(path, flags, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail('matching or rejected cache must not write/create/connect')
    monkeypatch.setattr(os, 'open', readonly_open)
    monkeypatch.setattr(os, 'replace', forbidden)
    for attr in ('mkdir', 'write_text', 'write_bytes', 'unlink'):
        monkeypatch.setattr(Path, attr, forbidden)
    monkeypatch.setattr(httpx, 'Client', forbidden)


@pytest.mark.parametrize('name', ['prepare_local_audio', 'prepare_local_vad'])
def test_import_does_not_create_files_or_start_work(monkeypatch, name):
    no_writes(monkeypatch)
    assert callable(load(name).main)


@pytest.mark.parametrize('module', [audio, vad])
def test_main_requires_explicit_runtime_root(module):
    with pytest.raises(SystemExit) as error:
        module.main([])
    assert error.value.code == 2


@pytest.mark.parametrize('kind', ['relative', 'alias', 'immutable'])
def test_invalid_root_is_rejected_before_any_write(monkeypatch, tmp_path, kind):
    if kind == 'relative':
        root = Path('relative-runtime')
    elif kind == 'alias':
        real = tmp_path / 'real'; real.mkdir()
        root = tmp_path / 'alias'; root.symlink_to(real, target_is_directory=True)
    else:
        (tmp_path / 'release-manifest.json').write_text('{}')
        root = tmp_path / 'runtimes'
    no_writes(monkeypatch)
    with pytest.raises(HTTPException):
        audio.main(['--runtime-root', str(root)])


def recipe(content=b'pinned-public-fixture'):
    return {'repository': 'synthetic/public', 'files': [{'name': 'model.bin', 'bytes': len(content),
            'sha256': hashlib.sha256(content).hexdigest(), 'revision': 'fixed-public-revision'}]}


def test_audio_matching_cache_is_readonly_and_never_creates_venv(monkeypatch, tmp_path):
    weights = tmp_path / 'audio-bootstrap/weights'; weights.mkdir(parents=True)
    (weights / 'model.bin').write_bytes(b'pinned-public-fixture')
    before = inventory(tmp_path)
    no_writes(monkeypatch)
    result = audio.prepare_weights(tmp_path, recipe())
    assert result == {'state': 'complete', 'files': 1, 'cache_verified': True,
                      'weights_loaded': False, 'venv_created': False}
    assert inventory(tmp_path) == before
    assert not (tmp_path / 'venvs').exists()


def transport(monkeypatch, handler):
    monkeypatch.setattr(REAL_CLIENT, 'send', REAL_SEND)
    def client(**kwargs):
        assert kwargs['trust_env'] is False
        return REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(httpx, 'Client', client)


def test_audio_resumes_only_exact_range_and_checks_fixed_digest(monkeypatch, tmp_path):
    content = b'abcdef'
    weights = tmp_path / 'audio-bootstrap/weights'; weights.mkdir(parents=True)
    (weights / 'model.bin.part').write_bytes(content[:2])
    requests = []
    def reply(request):
        requests.append(request)
        assert request.headers['range'] == 'bytes=2-'
        return httpx.Response(206, headers={'content-range': 'bytes 2-5/6'}, content=content[2:])
    transport(monkeypatch, reply)
    result = audio.prepare_weights(tmp_path, recipe(content))
    assert len(requests) == 1 and result['weights_loaded'] is False
    assert (weights / 'model.bin').read_bytes() == content
    assert not (weights / 'model.bin.part').exists()
    assert not (tmp_path / 'venvs').exists()


@pytest.mark.parametrize('failure', ['wrong_digest', 'wrong_range', 'insecure_redirect'])
def test_audio_rejects_corrupt_or_unbound_download(monkeypatch, tmp_path, failure):
    weights = tmp_path / 'audio-bootstrap/weights'; weights.mkdir(parents=True)
    if failure == 'wrong_range':
        (weights / 'model.bin.part').write_bytes(b'ab')
    def reply(request):
        if failure == 'insecure_redirect':
            return httpx.Response(302, headers={'location': 'http://untrusted.invalid/file'})
        if failure == 'wrong_range':
            return httpx.Response(206, headers={'content-range': 'bytes 1-5/6'}, content=b'cdef')
        return httpx.Response(200, content=b'xxxxxx')
    transport(monkeypatch, reply)
    with pytest.raises(ValueError):
        audio.prepare_weights(tmp_path, recipe(b'abcdef'))
    assert not (weights / 'model.bin').exists()


def proven_runtime(root):
    environment = root / 'venvs/vad'; (environment / 'bin').mkdir(parents=True)
    python = environment / 'bin/python'; python.write_bytes(b'synthetic-never-executed'); python.chmod(0o700)
    base = root / 'base'; base.mkdir()
    executable = base / 'python'; executable.write_bytes(b'synthetic-base'); executable.chmod(0o700)
    (environment / 'pyvenv.cfg').write_text(f'home = {base}\ninclude-system-site-packages = false\nexecutable = {executable}\n')
    output = root / 'vad-bootstrap'; wheels = output / 'wheels'; wheels.mkdir(parents=True)
    pins = []
    for requirement in vad.REQ:
        name, version = requirement.split('==')
        content = f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n'.encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr(f'{name}-{version}.dist-info/METADATA', content)
        wheel = wheels / f'{name}-{version}-py3-none-any.whl'; wheel.write_bytes(buffer.getvalue())
        info = environment / f'lib/python3.12/site-packages/{name}-{version}.dist-info'
        info.mkdir(parents=True); (info / 'METADATA').write_bytes(content)
        pins.append({'name': name, 'version': version, 'filename': wheel.name,
                     'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(), 'size_bytes': wheel.stat().st_size,
                     'url': 'https://files.pythonhosted.org/packages/' + wheel.name})
    (output / 'runtime-wheels.json').write_text(json.dumps(pins))
    (output / 'requirements.lock').write_text(vad.lock_text(pins))
    return environment, output


def test_vad_proven_existing_environment_has_zero_writes_network_or_pip(monkeypatch, tmp_path):
    proven_runtime(tmp_path)
    before = inventory(tmp_path)
    no_writes(monkeypatch)
    result = vad.prepare_runtime(tmp_path, audio)
    assert result == {'state': 'runtime_ready', 'cache_verified': True, 'dependency_reinstall': False,
                      'model_loaded': False, 'runtime_native_import_verified': False}
    assert inventory(tmp_path) == before


@pytest.mark.parametrize('damage', ['wheel', 'version', 'lock', 'cfg', 'alias', 'unproven'])
def test_vad_bad_existing_environment_fails_closed_without_repair(monkeypatch, tmp_path, damage):
    environment, output = proven_runtime(tmp_path)
    if damage == 'wheel':
        next((output / 'wheels').glob('*.whl')).write_bytes(b'corrupted')
    elif damage == 'version':
        next(environment.glob('lib/python*/site-packages/*.dist-info/METADATA')).write_text('Name: onnxruntime\nVersion: wrong\n')
    elif damage == 'lock':
        (output / 'requirements.lock').write_text('unverified dependency')
    elif damage == 'cfg':
        (environment / 'pyvenv.cfg').write_text('include-system-site-packages = true\n')
    elif damage == 'alias':
        path = output / 'requirements.lock'; path.rename(output / 'actual-lock'); path.symlink_to(output / 'actual-lock')
    else:
        (output / 'runtime-wheels.json').unlink()
    before = inventory(tmp_path)
    no_writes(monkeypatch)
    with pytest.raises(ValueError, match='不自动重装'):
        vad.prepare_runtime(tmp_path, audio)
    assert inventory(tmp_path) == before
