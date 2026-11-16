"""Pure path/integrity tests: no database, OCR, model or real child process."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import material_ocr_runtime as runtime


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('no actual subprocess is allowed in pure OCR path tests')
    monkeypatch.setattr(runtime.subprocess, 'run', forbidden)
    monkeypatch.setattr(runtime.subprocess, 'Popen', forbidden)


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    """Synthetic files exercise production checks, never a runnable interpreter."""
    root = tmp_path / 'runtime root 中文 "quoted"'
    monkeypatch.setattr(runtime, 'local_runtime_root', lambda: root)
    paths = runtime._operation_paths()
    def put(base, name, content):
        path = base / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {'path': name, 'sha256': runtime._sha(content)}
    py = put(paths.python.parent.parent, 'bin/python', b'not an executable')
    cfg = put(paths.python.parent.parent, 'pyvenv.cfg', b'synthetic private interpreter')
    base = put(paths.base_runtime, 'lib/bootstrap.py', b'private base')
    code = put(paths.site, 'package.py', b'private code')
    data = put(paths.site, 'private.data', b'private data')
    manifest = {
        'id': runtime._ENGINE_ID,
        'interpreter': {'sha256': py['sha256'], 'venv_config_sha256': cfg['sha256'],
                        'private_base': 'base-python', 'private_base_required': True},
        'base_runtime_files': [base], 'private_site_files': [code, data], 'runtime_files': [code],
        'runner': put(paths.base, 'runner.py', b'fixed runner'),
        'config': put(paths.base, 'config.json', b'{}'),
        'dictionary': put(paths.base, 'models/dictionary.json', b'[]'),
        'models': [put(paths.base, 'models/model.bin', b'fixed model')],
        'notices': [put(paths.base, 'NOTICE', b'fixed notice')],
        'contract': 1, 'name': 'synthetic test package', 'python': '3.12', 'packages': [],
        'limits': {}, 'data_policy': {'cloud_allowed': False},
    }
    (paths.base / 'manifest.json').write_bytes(runtime._canonical(manifest))
    # Synthetic manifest authorization is confined to this fixture; a separate
    # test retains the real production epoch and manifest pin.
    monkeypatch.setattr(runtime, '_MANIFEST_HASH', runtime._sha(runtime._canonical(manifest)))
    monkeypatch.setattr(runtime.sys, 'platform', 'darwin')
    monkeypatch.setattr(runtime, '_SANDBOX', tmp_path / 'sandbox-exec')
    runtime._SANDBOX.write_bytes(b'not executable')
    return paths, manifest


def test_import_does_not_resolve_server_paths(monkeypatch):
    from generic_agents import runtime_paths
    monkeypatch.setattr(runtime_paths, 'local_runtime_root', lambda: pytest.fail('import must be lazy'))
    name = 'generic_agents._ocr_lazy_path_test'
    spec = importlib.util.spec_from_file_location(name, runtime.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert module._RUNTIME_EPOCH == 'm49'


def test_fixed_production_manifest_and_epoch_remain_unchanged():
    assert runtime._RUNTIME_EPOCH == 'm49'
    expected = '55c902bbe51f2cdc9841e8571e9729f4c1a24c628601361a77019fbc7b1040e2'
    assert runtime._MANIFEST_HASH == expected
    original = Path(__file__).resolve().parents[1] / '.runtime/ocr-m49/manifest.json'
    assert runtime._sha(runtime._canonical(json.loads(original.read_bytes()))) == expected


def test_paths_and_readonly_profile_use_only_selected_root(prepared):
    paths, _ = prepared
    assert paths.base == paths.root / 'ocr-m49'
    assert paths.python == paths.root / 'venvs/ocr-m49/bin/python'
    assert paths.site == paths.root / 'venvs/ocr-m49/lib/python3.12/site-packages'
    assert paths.base_runtime == paths.root / 'venvs/ocr-m49/base-python'
    assert paths.profile.startswith('(version 1)(allow default)(deny network*)')
    for protected in [paths.base, paths.python.parent.parent]:
        assert '(deny file-write* (subpath ' + json.dumps(str(protected), ensure_ascii=False) + '))' in paths.profile


def test_invalid_root_is_readable_health_failure_without_fallback(monkeypatch):
    def invalid():
        raise HTTPException(409, 'selected server runtime is invalid')
    monkeypatch.setattr(runtime, 'local_runtime_root', invalid)
    result = runtime.engine_status()
    assert result['ready'] is False and result['manifest'] is None
    assert result['reason'] == 'selected server runtime is invalid'
    with pytest.raises(HTTPException, match='selected server runtime'):
        runtime.pdf_info(b'pdf')


def test_installation_and_probe_share_one_resolver_snapshot(prepared, monkeypatch):
    paths, manifest = prepared; calls = []; probes = []
    def selected():
        calls.append(1)
        return paths.root if len(calls) == 1 else paths.root / 'wrong-second-root'
    monkeypatch.setattr(runtime, 'local_runtime_root', selected)
    monkeypatch.setattr(runtime, '_sandbox_probe', lambda *, _paths: probes.append(_paths))
    actual, hashed = runtime._installation()
    assert actual == manifest and hashed == runtime._MANIFEST_HASH
    assert len(calls) == 1 and len(probes) == 1 and probes[0] == paths


def test_partial_health_uses_same_snapshot_and_stdlib_only_probe(prepared, monkeypatch):
    paths, _ = prepared; calls = []; commands = []
    monkeypatch.setattr(runtime, 'local_runtime_root', lambda: calls.append(1) or paths.root)
    def run(command, **kwargs):
        commands.append(command)
        assert command[2] == paths.profile and command[3] == str(paths.python)
        assert command[4:7] == ['-I', '-B', '-S']
        assert set(kwargs['env']) == {'PATH', 'TMPDIR'}
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess, 'run', run)
    assert runtime.engine_status()['ready'] is True
    assert len(calls) == len(commands) == 1


def test_execute_checks_probes_and_launches_the_same_selected_package(prepared, monkeypatch):
    paths, manifest = prepared; calls = []; checked = []; commands = []
    monkeypatch.setattr(runtime, 'local_runtime_root', lambda: calls.append(1) or
                        (paths.root if len(calls) == 1 else paths.root / 'wrong-second-root'))
    monkeypatch.setattr(runtime, '_sandbox_probe', lambda *, _paths: checked.append(_paths))
    class Completed:
        pid = 123456; returncode = 0
        def poll(self): return 0
    def popen(command, **kwargs):
        commands.append(command)
        assert command[2] == paths.profile and command[3] == str(paths.python)
        assert command[4:6] == ['-I', '-B'] and command[6] == str(paths.base / 'runner.py')
        assert kwargs['start_new_session'] is True
        result = {'page_count': 1, 'receipt': {'manifest_hash': runtime._MANIFEST_HASH,
                                             'input_sha256': runtime._sha(b'pdf')}}
        Path(command[-1]).write_text(json.dumps({'ok': True, 'result': result}))
        return Completed()
    monkeypatch.setattr(runtime.subprocess, 'Popen', popen)
    result = runtime.pdf_info(b'pdf')
    assert result['page_count'] == 1 and result['receipt']['manifest']['id'] == manifest['id']
    assert len(calls) == len(checked) == len(commands) == 1 and checked[0] == paths


def test_multi_page_call_keeps_one_path_snapshot(prepared, monkeypatch):
    paths, _ = prepared; resolutions = []; calls = []
    monkeypatch.setattr(runtime, 'local_runtime_root', lambda: resolutions.append(1) or paths.root)
    def execute(content, action, **kwargs):
        calls.append((action, kwargs['_paths']))
        return {'page_count': 2} if action == 'pdf_info' else {'page': kwargs['page']}
    monkeypatch.setattr(runtime, '_execute', execute)
    assert runtime.render_pdf(b'pdf') == [{'page': 1}, {'page': 2}]
    assert len(resolutions) == 1 and [action for action, _ in calls] == ['pdf_info', 'render', 'render']
    assert all(selected is calls[0][1] for _, selected in calls)


@pytest.mark.parametrize('root_name', ['base', 'site', 'base_runtime'])
@pytest.mark.parametrize('kind', ['file_link', 'directory_link', 'dangling_link', 'fifo'])
@pytest.mark.parametrize('full', [False, True])
def test_unlisted_links_and_special_entries_block_before_any_probe(prepared, monkeypatch, root_name, kind, full):
    paths, _ = prepared; extra = getattr(paths, root_name) / 'unlisted'
    if kind == 'fifo':
        import os
        os.mkfifo(extra)
    else:
        target = paths.base / 'runner.py' if kind == 'file_link' else paths.root
        if kind == 'dangling_link': target = paths.root / 'missing'
        extra.symlink_to(target, target_is_directory=kind == 'directory_link')
    monkeypatch.setattr(runtime, '_sandbox_probe', lambda **kwargs: pytest.fail('must block before probe'))
    with pytest.raises(HTTPException): runtime._installation(full=full)


@pytest.mark.parametrize('relative', ['base-python/lib/bootstrap.py', 'lib/python3.12/site-packages/package.py',
                                    'lib/python3.12/site-packages/private.data', 'bin/python', 'pyvenv.cfg'])
def test_changed_private_file_is_blocked_before_probe(prepared, monkeypatch, relative):
    paths, _ = prepared
    (paths.python.parent.parent / relative).write_bytes(b'changed')
    monkeypatch.setattr(runtime, '_sandbox_probe', lambda **kwargs: pytest.fail('must block before probe'))
    with pytest.raises(HTTPException): runtime._installation()


@pytest.mark.parametrize('which', ['base', 'site'])
def test_unrecorded_private_files_are_not_ignored(prepared, monkeypatch, which):
    paths, _ = prepared
    target = paths.base_runtime if which == 'base' else paths.site
    (target / 'added.data').write_bytes(b'extra')
    monkeypatch.setattr(runtime, '_sandbox_probe', lambda **kwargs: pytest.fail('must block before probe'))
    with pytest.raises(HTTPException): runtime._installation()
