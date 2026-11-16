"""Synthetic fixed-profile/RSS tests; no real process, runtime or model runs."""
import copy
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import material_ocr_runtime as r
from tests.test_material_ocr_low_memory_runtime import pair

V1_HASH = '3a11f677a07ca36d3a767379b00546aa41c1c47f2dc659b80344872e7c10d13f'
V2_HASH = '54e499dbd267250c20a8b6a0ccc1174fed792030283a3459b3d2c15084459eb8'
V2_ID = 'rapidocr-3.10.0-ppocrv6-small-cpu-m49-low-memory-v2'


@pytest.fixture(autouse=True)
def forbid_real_processes(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('a real process is forbidden in this synthetic test')
    monkeypatch.setattr(r.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(r.subprocess, 'run', forbidden)


@pytest.fixture
def triple(pair, monkeypatch):
    p = pair
    paths = r._operation_paths(engine_hash=r.LOW_MEMORY_V2_MANIFEST_HASH)
    shutil.copytree(p.low.base, paths.base)
    config = json.loads((paths.base / 'config.json').read_bytes())
    config['params'].update({'Det.limit_type': 'max', 'Det.limit_side_len': 1536})
    (paths.base / 'config.json').write_bytes(r._canonical(config))
    manifest = copy.deepcopy(p.new_manifest)
    manifest.update(id=V2_ID, name='explicit synthetic bounded detector')
    manifest['config']['sha256'] = r._sha((paths.base / 'config.json').read_bytes())
    (paths.base / 'manifest.json').write_bytes(r._canonical(manifest))
    hashed = r._sha(r._canonical(manifest))
    monkeypatch.setattr(r, 'LOW_MEMORY_V2_MANIFEST_HASH', hashed)
    monkeypatch.setattr(r, '_LOW_MEMORY_V2_PROFILE', replace(r._LOW_MEMORY_V2_PROFILE, manifest_hash=hashed))
    bounded = r._operation_paths(engine_hash=hashed)
    return SimpleNamespace(pair=p, bounded=bounded, bounded_hash=hashed, bounded_manifest=manifest,
        entries={'m49': (p.legacy, p.old_hash, p.old_manifest),
                 'v1': (p.low, p.new_hash, p.new_manifest),
                 'v2': (bounded, hashed, manifest)})


def completed_child(monkeypatch, triple, peak, *, omit_peak=False, result_list=False):
    calls = []
    class Completed:
        pid = 123; returncode = 0
        def poll(self): return 0
    def popen(command, **kwargs):
        base = Path(command[6]).parent
        paths, hashed, manifest = next(v for v in triple.entries.values() if v[0].base == base)
        calls.append(hashed)
        assert command[2] == paths.profile and command[3] == str(paths.python)
        request = json.loads(Path(command[-2]).read_bytes())
        content = Path(request['input']).read_bytes()
        regions = [{'id': 'r1', 'text': '显式合成文字', 'box': [0, 0, 1, 1], 'score': 0.5}]
        value = {'regions': regions, 'elapsed_ms': 1, 'receipt': {
            'manifest_hash': hashed, 'input_sha256': r._sha(content),
            'result_sha256': r._sha(r._canonical(regions))}}
        if not omit_peak: value['peak_rss_bytes'] = peak
        if result_list: value = []
        Path(command[-1]).write_bytes(r._canonical({'ok': True, 'result': value}))
        return Completed()
    monkeypatch.setattr(r.subprocess, 'Popen', popen)
    # A completed-between-polls child has no observable live RSS. Its frozen
    # runner's reported peak must still prevent acceptance of an oversized run.
    monkeypatch.setattr(r, '_resident_bytes', lambda process: 0)
    return calls


def test_source_owned_v2_keeps_original_constants_and_profile_catalog():
    assert r._RUNTIME_EPOCH == 'm49'
    assert r._MANIFEST_HASH == '55c902bbe51f2cdc9841e8571e9729f4c1a24c628601361a77019fbc7b1040e2'
    assert r.LOW_MEMORY_MANIFEST_HASH == V1_HASH
    assert r.LOW_MEMORY_V2_MANIFEST_HASH == V2_HASH and r.LOW_MEMORY_V2_ENGINE_ID == V2_ID
    assert r.recognition_profile_pairs() == ((r.LOW_MEMORY_ENGINE_ID, V1_HASH), (V2_ID, V2_HASH))
    selected = r._selected_profile(V2_HASH)
    assert selected.private_runtime and selected.base_name == 'ocr-m49-low-memory-v2'
    assert selected.venv_name == 'venvs/ocr-m49'
    assert r._MAX_RESIDENT_BYTES == 1073741824 and r._WALL_TIMEOUT_SECONDS == 90


def test_v2_full_installation_shares_only_verified_private_venv(triple):
    t = triple
    manifest, hashed = r._installation(_paths=t.bounded)
    assert (manifest, hashed) == (t.bounded_manifest, t.bounded_hash)
    assert t.bounded.engine_hash == t.bounded_hash
    assert t.bounded.python == t.pair.legacy.python == t.pair.low.python
    assert t.bounded.base not in {t.pair.legacy.base, t.pair.low.base}
    assert t.pair.probes == [t.bounded]
    for root in (t.bounded.base, t.bounded.python.parent.parent):
        assert '(deny file-write* (subpath ' + json.dumps(str(root), ensure_ascii=False) + '))' in t.bounded.profile


def test_v2_manifest_cannot_be_mixed_with_v1_paths(triple):
    with pytest.raises(HTTPException):
        r._execute(b'synthetic image', 'recognize', engine_hash=triple.bounded_hash, _paths=triple.pair.low)
    assert not triple.pair.probes


def test_v2_v1_default_interleave_without_mutating_any_default(triple, monkeypatch):
    t = triple; before = (r._RUNTIME_EPOCH, r._MANIFEST_HASH, r._ENGINE_ID, r.LOW_MEMORY_MANIFEST_HASH)
    calls = completed_child(monkeypatch, t, 123456)
    for name in ('v2', 'v1', 'm49', 'v2'):
        paths, hashed, manifest = t.entries[name]
        value = r.recognize(b'synthetic image', expected_manifest_hash=hashed)
        assert value['receipt']['manifest_hash'] == hashed
        assert value['receipt']['manifest']['id'] == manifest['id']
        assert value['peak_rss_bytes'] == 123456
    assert calls == [t.entries[n][1] for n in ('v2', 'v1', 'm49', 'v2')]
    assert before == (r._RUNTIME_EPOCH, r._MANIFEST_HASH, r._ENGINE_ID, r.LOW_MEMORY_MANIFEST_HASH)
    # Retain the established no-argument two-item catalog; overview provides the
    # separate resume_profiles list with per-profile permissions.
    assert len(r.profiles()) == 2


@pytest.mark.parametrize('name', ['m49', 'v1', 'v2'])
@pytest.mark.parametrize('peak', [None, True, False, 0, -1, 1.0, float('inf'), 1073741825])
def test_reported_peak_is_strict_even_if_the_live_poll_missed_it(triple, monkeypatch, name, peak):
    completed_child(monkeypatch, triple, peak)
    with pytest.raises(HTTPException) as failure:
        r.recognize(b'synthetic image', expected_manifest_hash=triple.entries[name][1])
    assert failure.value.status_code == 409 and '内存' in failure.value.detail


@pytest.mark.parametrize('omit_peak,result_list', [(True, False), (False, True)])
def test_absent_peak_or_bad_result_shape_is_controlled_rejection(triple, monkeypatch, omit_peak, result_list):
    completed_child(monkeypatch, triple, 1, omit_peak=omit_peak, result_list=result_list)
    with pytest.raises(HTTPException) as failure:
        r.recognize(b'synthetic image', expected_manifest_hash=triple.bounded_hash)
    assert failure.value.status_code == 409


@pytest.mark.parametrize('peak', [1, 1073741824])
def test_reported_peak_boundary_is_accepted_without_claiming_a_measurement(triple, monkeypatch, peak):
    completed_child(monkeypatch, triple, peak)
    assert r.recognize(b'synthetic image', expected_manifest_hash=triple.bounded_hash)['peak_rss_bytes'] == peak


def test_unknown_hash_fails_before_paths_installation_or_process(monkeypatch):
    monkeypatch.setattr(r, 'local_runtime_root', lambda: pytest.fail('unknown hash must not resolve a runtime root'))
    with pytest.raises(HTTPException): r.recognize(b'synthetic image', expected_manifest_hash='0' * 64)
