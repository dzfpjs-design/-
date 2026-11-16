"""Pure audio/VAD server path selection; no model, network, database or child."""
import ast
from dataclasses import FrozenInstanceError
import importlib.util
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys

import pytest
from fastapi import HTTPException

from generic_agents import config, local_audio_paths as paths, runtime_paths


@pytest.fixture(autouse=True)
def private_server_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ('GA_AUDIO_PYTHON', 'GA_AUDIO_WEIGHTS', 'GA_VAD_PYTHON', 'GA_VAD_WEIGHTS',
                 'AUDIO_PYTHON', 'AUDIO_WEIGHTS', 'VAD_PYTHON', 'VAD_WEIGHTS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('GA_LOCAL_RUNTIME_ROOT', str(tmp_path / 'runtimes'))
    monkeypatch.setenv('GA_STORAGE_DIR', str(tmp_path / 'storage'))
    monkeypatch.setenv('GA_DATABASE_URL', 'postgresql+psycopg://unused:unused@127.0.0.1:1/unused')
    monkeypatch.setenv('GA_REDIS_URL', 'redis://127.0.0.1:1/0')
    monkeypatch.setenv('GA_CLOUD_DAILY_CALL_LIMIT', '0')
    monkeypatch.setenv('GA_CLOUD_DAILY_TOKEN_LIMIT', '0')
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    monkeypatch.setenv('DEEPSEEK_API_KEY', '')
    def settings(): return config.Settings(_env_file=None)
    for module in (config, runtime_paths, paths): monkeypatch.setattr(module, 'settings', settings)
    def forbidden(*args, **kwargs):
        pytest.fail('pure audio path tests forbid processes and service connections')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)


def select(kind): return paths.audio_paths() if kind == 'audio' else paths.vad_paths()


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(b'pure fixture, never executable code'); path.chmod(0o755)
    return path


def blocked(kind):
    with pytest.raises(HTTPException) as caught: select(kind)
    assert caught.value.status_code == 409
    assert caught.value.detail in {paths._INVALID, runtime_paths._INVALID}
    assert caught.value.__suppress_context__
    return caught.value


def test_import_is_lazy(monkeypatch):
    monkeypatch.setattr(config, 'settings', lambda: pytest.fail('must not read settings on import'))
    monkeypatch.setattr(runtime_paths, 'local_runtime_root', lambda: pytest.fail('must not select root on import'))
    name = 'generic_agents._local_audio_lazy_test'
    spec = importlib.util.spec_from_file_location(name, paths.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module); spec.loader.exec_module(module)
    assert callable(module.audio_paths) and callable(module.vad_paths)


@pytest.mark.parametrize('kind', ['audio', 'vad'])
def test_defaults_are_fixed_under_selected_root_and_do_not_create_directories(tmp_path, kind):
    before = set(tmp_path.rglob('*')); selected = select(kind)
    assert selected.root == tmp_path / 'runtimes'
    assert selected.python == selected.root / 'venvs' / kind / 'bin/python'
    assert selected.weights == selected.root / (kind + '-bootstrap') / 'weights'
    assert not selected.python.exists() and not selected.weights.exists()
    assert set(tmp_path.rglob('*')) == before
    with pytest.raises(FrozenInstanceError): selected.python = tmp_path / 'replacement'


def test_settings_default_overrides_are_none_and_unprefixed_env_is_ignored(monkeypatch, tmp_path):
    for field in ('audio_python', 'audio_weights', 'vad_python', 'vad_weights'):
        assert config.Settings.model_fields[field].default is None
        monkeypatch.setenv(field.upper(), str(tmp_path / 'unprefixed'))
    current = config.Settings(_env_file=None)
    assert all(getattr(current, field) is None for field in ('audio_python', 'audio_weights', 'vad_python', 'vad_weights'))


@pytest.mark.parametrize('kind', ['audio', 'vad'])
@pytest.mark.parametrize('field', ['python', 'weights'])
def test_each_explicit_server_override_is_independent(monkeypatch, tmp_path, kind, field):
    override = tmp_path / 'original-project/.runtime' / ('venv/bin/python' if field == 'python' else 'weights')
    monkeypatch.setenv('GA_' + kind.upper() + '_' + field.upper(), str(override))
    selected = select(kind)
    assert getattr(selected, field) == override
    other = 'weights' if field == 'python' else 'python'
    expected = selected.root / (kind + '-bootstrap/weights' if other == 'weights' else 'venvs/' + kind + '/bin/python')
    assert getattr(selected, other) == expected
    assert selected.root == tmp_path / 'runtimes' and not override.exists()


@pytest.mark.parametrize('kind', ['audio', 'vad'])
def test_one_server_root_snapshot_is_used_for_both_default_fields(monkeypatch, tmp_path, kind):
    calls = []
    def root():
        calls.append(1); return tmp_path / ('first' if len(calls) == 1 else 'second')
    monkeypatch.setattr(paths, 'local_runtime_root', root)
    selected = select(kind)
    assert len(calls) == 1 and selected.root == tmp_path / 'first'
    assert selected.python.is_relative_to(selected.root) and selected.weights.is_relative_to(selected.root)


@pytest.mark.parametrize('kind', ['audio', 'vad'])
@pytest.mark.parametrize('field', ['python', 'weights'])
@pytest.mark.parametrize('invalid', ['relative', 'dotdot', 'control'])
def test_invalid_override_cannot_fall_back_to_an_old_default(monkeypatch, tmp_path, kind, field, invalid):
    values = {'relative': '.runtime/legacy', 'dotdot': str(tmp_path / 'one/../other'),
              'control': str(tmp_path / 'secret') + '\ncredential'}
    monkeypatch.setenv('GA_' + kind.upper() + '_' + field.upper(), values[invalid])
    error = blocked(kind)
    assert 'credential' not in error.detail and str(tmp_path) not in error.detail


@pytest.mark.parametrize('kind', ['audio', 'vad'])
def test_standard_venv_final_python_symlink_is_preserved(monkeypatch, tmp_path, kind):
    target = executable(tmp_path / 'base/bin/python3')
    literal = tmp_path / 'venv/bin/python'; literal.parent.mkdir(parents=True, mode=0o700)
    literal.symlink_to(target)
    monkeypatch.setenv('GA_' + kind.upper() + '_PYTHON', str(literal))
    selected = select(kind)
    assert selected.python == literal and selected.python != target
    assert selected.python.is_symlink() and selected.python.resolve() == target


@pytest.mark.parametrize('field', ['python', 'weights'])
def test_intermediate_directory_alias_is_rejected(monkeypatch, tmp_path, field):
    real = tmp_path / 'real'; real.mkdir(mode=0o700)
    alias = tmp_path / 'alias'; alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv('GA_AUDIO_' + field.upper(), str(alias / ('bin/python' if field == 'python' else 'weights')))
    blocked('audio')


def test_weights_terminal_directory_alias_is_not_a_venv_exception(monkeypatch, tmp_path):
    real = tmp_path / 'weights'; real.mkdir(mode=0o700)
    alias = tmp_path / 'alias'; alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv('GA_AUDIO_WEIGHTS', str(alias)); blocked('audio')


@pytest.mark.parametrize('target_kind', ['writable', 'not_executable', 'directory', 'missing', 'loop'])
def test_final_python_symlink_requires_a_trusted_executable_target(monkeypatch, tmp_path, target_kind):
    literal = tmp_path / 'venv/bin/python'; literal.parent.mkdir(parents=True, mode=0o700)
    target = tmp_path / 'target'
    if target_kind in {'writable', 'not_executable'}:
        executable(target); target.chmod(0o777 if target_kind == 'writable' else 0o644)
    elif target_kind == 'directory': target.mkdir(mode=0o700)
    if target_kind == 'loop': target = literal
    literal.symlink_to(target)
    monkeypatch.setenv('GA_AUDIO_PYTHON', str(literal)); blocked('audio')


@pytest.mark.parametrize('field', ['python', 'weights'])
@pytest.mark.parametrize('location', ['selected', 'ancestor'])
def test_writable_directories_are_rejected_for_missing_and_existing_paths(monkeypatch, tmp_path, field, location):
    parent = tmp_path / 'untrusted'; parent.mkdir(mode=0o700)
    child = parent / 'private'; child.mkdir(mode=0o700)
    selected = child / 'bin/python' if field == 'python' else child
    unsafe = child if location == 'selected' else parent; unsafe.chmod(0o777)
    try:
        monkeypatch.setenv('GA_AUDIO_' + field.upper(), str(selected)); blocked('audio')
    finally: unsafe.chmod(0o700)


@pytest.mark.parametrize('field', ['python', 'weights'])
@pytest.mark.parametrize('marker', ['source-manifest.json', 'release-manifest.json'])
def test_override_cannot_point_into_any_immutable_program_release(monkeypatch, tmp_path, field, marker):
    release = tmp_path / 'another-release'; release.mkdir(mode=0o700)
    (release / marker).write_text('{}')
    selected = release / ('venv/bin/python' if field == 'python' else 'weights')
    monkeypatch.setenv('GA_AUDIO_' + field.upper(), str(selected)); blocked('audio')


@pytest.mark.parametrize('mode', [0o777, 0o644])
def test_literal_python_file_must_also_be_safe(monkeypatch, tmp_path, mode):
    python = executable(tmp_path / 'bin/python'); python.chmod(mode)
    monkeypatch.setenv('GA_AUDIO_PYTHON', str(python)); blocked('audio')


def test_direct_system_owned_directory_is_not_a_private_venv_override(monkeypatch, tmp_path):
    python = executable(tmp_path / 'root-owned/bin/python'); target = python.parent
    native_stat = Path.stat
    def fake_stat(path, *args, **kwargs):
        result = native_stat(path, *args, **kwargs)
        if path == target:
            values = list(result); values[4] = 0; return os.stat_result(values)
        return result
    monkeypatch.setattr(Path, 'stat', fake_stat)
    monkeypatch.setenv('GA_AUDIO_PYTHON', str(python))
    if os.getuid() != 0: blocked('audio')


def test_foreign_or_writable_symlink_target_is_not_authorized(monkeypatch, tmp_path):
    target = executable(tmp_path / 'base/python')
    literal = tmp_path / 'venv/bin/python'; literal.parent.mkdir(parents=True, mode=0o700); literal.symlink_to(target)
    native_stat = Path.stat
    def fake_stat(path, *args, **kwargs):
        result = native_stat(path, *args, **kwargs)
        if path == target:
            values = list(result); values[4] = os.getuid() + 100000; return os.stat_result(values)
        return result
    monkeypatch.setattr(Path, 'stat', fake_stat)
    monkeypatch.setenv('GA_AUDIO_PYTHON', str(literal)); blocked('audio')


def test_symlink_target_cannot_be_replaced_through_a_writable_parent(monkeypatch, tmp_path):
    target = executable(tmp_path / 'base/python')
    literal = tmp_path / 'venv/bin/python'; literal.parent.mkdir(parents=True, mode=0o700); literal.symlink_to(target)
    target.parent.chmod(0o777)
    try:
        monkeypatch.setenv('GA_AUDIO_PYTHON', str(literal)); blocked('audio')
    finally: target.parent.chmod(0o700)


def test_filesystem_error_is_generic_not_a_raw_path_or_secret(monkeypatch, tmp_path):
    python = executable(tmp_path / 'bin/python')
    monkeypatch.setenv('GA_AUDIO_PYTHON', str(python)); native_stat = Path.stat
    def sensitive_error(path, *args, **kwargs):
        if path == python: raise PermissionError('private-credential-and-path')
        return native_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', sensitive_error)
    error = blocked('audio'); assert 'credential' not in error.detail and error.__cause__ is None


def catalog_function():
    # Execute only the shipping catalog function, avoiding app/DB module imports.
    source = Path(__file__).resolve().parents[1] / 'src/generic_agents/specialist_api.py'
    node = next(x for x in ast.parse(source.read_bytes()).body if isinstance(x, ast.FunctionDef) and x.name == 'catalog')
    node.decorator_list = []
    namespace = {'__package__': 'generic_agents', 'P': object,
                 'RECIPES': {'visible': {'pack_id': 'visible'}, 'hidden': {'hidden_in_setup_until_paired_workflow': True}}}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['catalog']


def test_specialist_health_uses_selected_audio_vad_paths_without_installing(tmp_path):
    result = catalog_function()(None)
    assert result['audio_runtime_installed'] is result['vad_runtime_installed'] is False
    assert result['cloud_fallback'] is False and len(result['packs']) == 1
    executable(tmp_path / 'runtimes/venvs/audio/bin/python')
    result = catalog_function()(None)
    assert result['audio_runtime_installed'] is True and result['vad_runtime_installed'] is False
    assert not (tmp_path / 'runtimes/audio-bootstrap').exists()


def test_specialist_health_does_not_hide_invalid_server_override(monkeypatch):
    monkeypatch.setenv('GA_VAD_WEIGHTS', 'relative/old-weights')
    with pytest.raises(HTTPException) as error: catalog_function()(None)
    assert error.value.status_code == 409
