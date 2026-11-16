"""Read-only resolver tests, using only owned temporary paths and server settings."""
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from fastapi import HTTPException

from generic_agents import config, runtime_paths


@pytest.fixture(autouse=True)
def isolated_server_configuration(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('GA_LOCAL_RUNTIME_ROOT', raising=False)
    monkeypatch.delenv('LOCAL_RUNTIME_ROOT', raising=False)
    monkeypatch.setenv('GA_DATABASE_URL', 'postgresql+psycopg://unused:unused@127.0.0.1:1/unused')
    monkeypatch.setenv('GA_REDIS_URL', 'redis://127.0.0.1:1/0')
    monkeypatch.setenv('GA_CLOUD_DAILY_CALL_LIMIT', '0')
    monkeypatch.setenv('GA_CLOUD_DAILY_TOKEN_LIMIT', '0')
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    monkeypatch.setenv('DEEPSEEK_API_KEY', '')
    def forbidden(*args, **kwargs):
        pytest.fail('resolver tests must not start processes or connect to services')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    # Never read the real project .env or use the process-wide settings cache.
    monkeypatch.setattr(runtime_paths, 'settings', lambda: config.Settings(_env_file=None))


def rejected(value, **kwargs):
    with pytest.raises(HTTPException) as caught:
        runtime_paths.validate_local_runtime_root(value, **kwargs)
    assert caught.value.status_code == 409
    assert caught.value.detail == runtime_paths._INVALID
    assert caught.value.__suppress_context__ is True
    return caught.value


def test_import_is_lazy_and_does_not_read_settings(monkeypatch):
    monkeypatch.setattr(config, 'settings', lambda: pytest.fail('import must not resolve settings'))
    name = 'generic_agents._runtime_root_lazy_test'
    spec = importlib.util.spec_from_file_location(name, runtime_paths.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert callable(module.local_runtime_root)


def test_default_uses_storage_sibling_without_creating_it_or_legacy_fallback(monkeypatch, tmp_path):
    storage = tmp_path / 'instance/storage'
    storage.parent.mkdir(mode=0o700)
    monkeypatch.setenv('GA_STORAGE_DIR', str(storage))
    before = set(tmp_path.rglob('*'))
    selected = runtime_paths.local_runtime_root()
    assert selected == storage.parent / 'runtimes'
    assert selected != runtime_paths._SOURCE_ROOT / '.runtime'
    assert not selected.exists() and not storage.exists()
    assert set(tmp_path.rglob('*')) == before


def test_relative_storage_is_anchored_to_private_cwd_without_mkdir(monkeypatch, tmp_path):
    monkeypatch.setenv('GA_STORAGE_DIR', 'instance/storage')
    assert runtime_paths.local_runtime_root() == tmp_path / 'instance/runtimes'
    assert not (tmp_path / 'instance').exists()


def test_explicit_original_like_runtime_root_is_allowed_in_place(tmp_path):
    source = tmp_path / 'project'; source.mkdir(mode=0o700)
    original = source / '.runtime'; original.mkdir(mode=0o700)
    before = original.stat()
    assert runtime_paths.validate_local_runtime_root(original, source_root=source) == original
    after = original.stat()
    assert (before.st_dev, before.st_ino, before.st_mode) == (after.st_dev, after.st_ino, after.st_mode)
    assert list(original.iterdir()) == []


def test_explicit_server_env_wins_over_storage_default(monkeypatch, tmp_path):
    selected = tmp_path / 'node-runtime'
    monkeypatch.setenv('GA_LOCAL_RUNTIME_ROOT', str(selected))
    monkeypatch.setenv('GA_STORAGE_DIR', str(tmp_path / 'private/storage'))
    settings = config.Settings(_env_file=None)
    assert settings.local_runtime_root == selected
    assert runtime_paths.local_runtime_root() == selected
    assert not selected.exists()


def test_unprefixed_setting_is_not_an_environment_alias(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCAL_RUNTIME_ROOT', str(tmp_path / 'untrusted-unprefixed'))
    monkeypatch.setenv('GA_STORAGE_DIR', str(tmp_path / 'storage'))
    assert config.Settings(_env_file=None).local_runtime_root is None
    assert runtime_paths.local_runtime_root() == tmp_path / 'runtimes'


@pytest.mark.parametrize('value', ['', 'relative/root', Path('relative'), None, object()])
def test_non_absolute_or_non_path_configuration_is_rejected(value):
    rejected(value)


def test_dotdot_and_control_characters_are_not_normalized_away(tmp_path):
    for value in (tmp_path / 'one/../other', str(tmp_path / 'secret') + '\npassword',
                  str(tmp_path / 'secret') + '\x7f'):
        error = rejected(value)
        assert 'password' not in error.detail and str(tmp_path) not in error.detail


@pytest.mark.parametrize('nested', [False, True])
def test_symlink_alias_is_rejected_including_alias_parent(tmp_path, nested):
    real = tmp_path / 'real'; real.mkdir(mode=0o700)
    alias = tmp_path / 'alias'; alias.symlink_to(real, target_is_directory=True)
    rejected(alias / 'missing-child' if nested else alias)


@pytest.mark.parametrize('mode', [0o720, 0o702, 0o777])
def test_writable_selected_root_is_rejected(tmp_path, mode):
    root = tmp_path / 'runtime'; root.mkdir(mode=0o700); root.chmod(mode)
    try:
        rejected(root)
    finally:
        root.chmod(0o700)


@pytest.mark.parametrize('missing_root', [False, True])
def test_writable_ancestor_is_rejected_even_when_selected_root_is_private(tmp_path, missing_root):
    ancestor = tmp_path / 'writable'; ancestor.mkdir(mode=0o700)
    selected = ancestor / 'runtime'
    if not missing_root: selected.mkdir(mode=0o700)
    ancestor.chmod(0o777)
    try:
        rejected(selected)
    finally:
        ancestor.chmod(0o700)


def test_regular_file_cannot_be_a_root_or_nearest_existing_parent(tmp_path):
    file = tmp_path / 'not-a-directory'; file.write_bytes(b'owned test only')
    rejected(file)
    rejected(file / 'child')


@pytest.mark.parametrize('marker', ['source-manifest.json', 'release-manifest.json'])
@pytest.mark.parametrize('level', ['self', 'ancestor'])
def test_any_immutable_release_marker_blocks_selected_root(tmp_path, marker, level):
    # This is a different release, not the resolver module's current source.
    source = tmp_path / 'current-source'; source.mkdir(mode=0o700)
    release = tmp_path / 'other-old-release'; release.mkdir(mode=0o700)
    selected = release if level == 'self' else release / 'nested/runtimes'
    (release / marker).write_bytes(b'{}')
    rejected(selected, source_root=source)


@pytest.mark.parametrize('marker', ['source-manifest.json', 'release-manifest.json'])
def test_current_immutable_source_also_blocks_runtime_under_it(tmp_path, marker):
    release = tmp_path / 'release'; release.mkdir(mode=0o700)
    (release / marker).write_bytes(b'{}')
    rejected(release / '.runtime', source_root=release)


@pytest.mark.parametrize('name', ['src', 'static', 'scripts', 'tests'])
def test_source_code_subtrees_are_never_runtime_roots(tmp_path, name):
    source = tmp_path / 'project'; source.mkdir(mode=0o700)
    rejected(source / name / 'runtimes', source_root=source)


def test_runtime_root_cannot_contain_the_source_tree(tmp_path):
    source = tmp_path / 'source'; source.mkdir(mode=0o700)
    rejected(tmp_path, source_root=source)
    rejected(source, source_root=source)


@pytest.mark.parametrize('where', ['root', 'ancestor'])
def test_foreign_owner_is_rejected_without_chown(monkeypatch, tmp_path, where):
    ancestor = tmp_path / 'ancestor'; ancestor.mkdir(mode=0o700)
    root = ancestor / 'runtime'; root.mkdir(mode=0o700)
    target = root if where == 'root' else ancestor
    native_stat = Path.stat
    def stat(path, *args, **kwargs):
        result = native_stat(path, *args, **kwargs)
        if path == target:
            values = list(result); values[4] = os.getuid() + 100000
            return os.stat_result(values)
        return result
    monkeypatch.setattr(Path, 'stat', stat)
    rejected(root)


@pytest.mark.parametrize('kind', [OSError, PermissionError, RuntimeError])
def test_sensitive_filesystem_error_is_generic_and_has_no_chained_leak(monkeypatch, tmp_path, kind):
    root = tmp_path / 'private-token-and-path'
    native_resolve = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == root: raise kind('sensitive-server-path-and-secret')
        return native_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', resolve)
    error = rejected(root)
    assert 'sensitive' not in error.detail and 'token' not in error.detail
    assert error.__cause__ is None
