"""Synthetic files and fake leases only; never install, connect or load models."""
from contextlib import nullcontext
import hashlib
import io
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
import zipfile

import pytest
from sqlalchemy.engine import Engine

from generic_agents import local_audio_install as audio, local_vad_install as vad
from generic_agents.local_audio_paths import LocalAudioPaths


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure installation tests forbid database/network/child processes')
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(audio.httpx.Client, 'send', forbidden)


def metadata(path):
    value = path.stat()
    return value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('kind', ['audio', 'vad'])
def test_process_freezes_one_selection_through_download_extract_and_call(monkeypatch, tmp_path, kind):
    module = audio if kind == 'audio' else vad
    python = tmp_path / 'literal-python'; python.write_bytes(b'not executable code')
    weights = tmp_path / 'selected-weights'; weights.mkdir()
    selected = LocalAudioPaths(tmp_path, python, weights)
    selections = []; downloads = []; calls = []; extracts = []
    def choose():
        selections.append(selected)
        assert len(selections) == 1
        return selected
    monkeypatch.setattr(module, kind + '_paths', choose)
    row = SimpleNamespace(id='installation', status='queued', version=1, pack_id=module.RECIPE['pack_id'],
        data={'generation': 1, 'recipe_hash': module.RECIPE_HASH, 'source': 'official', 'backend': 'cpu'})
    db = SimpleNamespace(commit=lambda: None)
    monkeypatch.setattr(module, 'scoped', lambda *args: row)
    monkeypatch.setattr(module, 'guard_record', lambda *args: row)
    monkeypatch.setattr(module, 'require', lambda *args: None)
    monkeypatch.setattr(module, 'node_lock', nullcontext)
    monkeypatch.setattr(module, 'audit', lambda *args: None)
    monkeypatch.setattr(module, 'probe', lambda: b'synthetic-probe')
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(module.httpx, 'Client', Client)
    def download(client, item, root, progress, guard):
        assert root is selected.weights
        guard(); downloads.append(item['name'])
    monkeypatch.setattr(module, 'download_file', download)
    def call(content, configuration, guard=None, on_started=None, *, _paths=None):
        assert _paths is selected and content == b'synthetic-probe'
        guard(); on_started(1234); calls.append(configuration)
        return {'transcription': {'inference_performed': True}, 'audio': {'duration_seconds': .5},
                'elapsed_seconds': .1, 'activity': {'intervals': []}, 'input_sha256': 'synthetic-sha'}
    monkeypatch.setattr(module, 'call', call)
    if kind == 'vad':
        def extract(root, guard):
            assert root is selected.weights
            guard(); extracts.append(root)
        monkeypatch.setattr(module, 'extract', extract)
    module.process(db, SimpleNamespace(), SimpleNamespace(data={'installation_id': row.id, 'generation': 1}))
    assert row.status == 'ready' and row.data['smoke']['inference_performed'] is True
    assert len(selections) == len(calls) == 1
    assert downloads == ([module.RECIPE['wheel']['name']] if kind == 'vad' else [x['name'] for x in module.RECIPE['files']])
    assert len(extracts) == (1 if kind == 'vad' else 0)


@pytest.mark.parametrize('module', [audio, vad])
def test_installed_keeps_no_argument_ready_contract(monkeypatch, module):
    row = SimpleNamespace(pack_id=module.RECIPE['pack_id'], status='ready',
        data={'recipe_hash': module.RECIPE_HASH, 'smoke': {'inference_performed': True}})
    monkeypatch.setattr(module, 'scoped', lambda *args: row)
    seen = []
    monkeypatch.setattr(module, 'ready', lambda *a, **k: seen.append((a, k)))
    assert module.installed(None, None, identity='installation') is row
    assert seen == [((), {})]


@pytest.fixture
def wheel(monkeypatch, tmp_path):
    values = {'model.onnx': b'synthetic-onnx', 'LICENSE': b'synthetic-license'}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, value in values.items(): archive.writestr('fixed/' + name, value)
    raw = output.getvalue(); (tmp_path / 'fixed.whl').write_bytes(raw)
    recipe = {'wheel': {'name': 'fixed.whl', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()},
        'files': [{'name': n, 'archive_name': 'fixed/' + n, 'bytes': len(v),
                   'sha256': hashlib.sha256(v).hexdigest()} for n, v in values.items()]}
    monkeypatch.setattr(vad, 'RECIPE', recipe)
    return tmp_path, values


def test_matching_vad_model_and_license_are_strictly_readonly(monkeypatch, wheel):
    root, values = wheel
    for name, value in values.items(): (root / name).write_bytes(value)
    before = {p.name: metadata(p) for p in root.iterdir()}
    def forbidden(*args, **kwargs): pytest.fail('matched cache must not open a writer or replace a file')
    monkeypatch.setattr(vad.os, 'open', forbidden)
    monkeypatch.setattr(vad.os, 'replace', forbidden)
    vad.extract(root, lambda: None)
    assert {p.name: metadata(p) for p in root.iterdir()} == before


def test_missing_vad_files_still_require_verified_fixed_wheel(wheel):
    root, values = wheel
    vad.extract(root, lambda: None)
    assert {n: (root / n).read_bytes() for n in values} == values
    assert not tuple(root.glob('*.verified-part'))


def test_bad_wheel_cannot_be_hidden_by_matching_model_cache(wheel):
    root, values = wheel
    for name, value in values.items(): (root / name).write_bytes(value)
    (root / 'fixed.whl').write_bytes(b'bad wheel')
    with pytest.raises(ValueError, match='摘要'): vad.extract(root, lambda: None)


def test_vad_cached_target_alias_is_rejected(wheel):
    root, values = wheel
    outside = root / 'outside'; outside.write_bytes(values['model.onnx'])
    (root / 'model.onnx').symlink_to(outside)
    with pytest.raises(ValueError, match='符号链接'): vad.extract(root, lambda: None)


def test_asr_download_cache_match_has_no_network_or_file_change(tmp_path):
    content = b'synthetic-fixed-cache'; target = tmp_path / 'model.pt'; target.write_bytes(content)
    item = {'name': target.name, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
    before = metadata(target); seen = []
    class Client:
        def stream(self, *args, **kwargs): pytest.fail('matched ASR cache must not download')
    audio.download_file(Client(), item, tmp_path, lambda *a, **k: seen.append(k), lambda: None)
    assert metadata(target) == before and seen[-1]['cache_verified'] is True
