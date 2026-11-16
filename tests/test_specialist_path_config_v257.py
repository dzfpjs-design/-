"""Settings path anchoring only; no model, database, installer or subprocess."""
from pathlib import Path

import pytest

from generic_agents.config import Settings
from generic_agents.local_changer_install import safe_path


@pytest.fixture(autouse=True)
def isolated_specialist_setting(monkeypatch):
    monkeypatch.delenv('GA_SPECIALIST_DIR', raising=False)


def configured(**values):
    # Never load .env or connect: these required URLs are synthetic init values.
    return Settings(_env_file=None, database_url='postgresql+psycopg://example.invalid/ga_path_config_test',
        redis_url='redis://example.invalid/0', **values)


def test_default_specialist_directory_is_anchored_when_settings_are_constructed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    value = configured()
    assert value.specialist_dir.is_absolute()
    assert value.specialist_dir == tmp_path / '.runtime' / 'specialist'


@pytest.mark.parametrize('relative', [Path('component-models'), Path('nested') / 'specialist',
    Path('models') / '..' / 'specialist'])
def test_explicit_relative_specialist_directory_preserves_original_components(tmp_path, monkeypatch, relative):
    monkeypatch.chdir(tmp_path)
    value = configured(specialist_dir=relative)
    assert value.specialist_dir == tmp_path / relative and value.specialist_dir.is_absolute()
    if '..' in relative.parts: assert '..' in value.specialist_dir.parts


def test_environment_relative_directory_uses_initial_cwd_without_resolving_dotdot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GA_SPECIALIST_DIR', 'models/../specialist')
    value = configured()
    assert value.specialist_dir == tmp_path / 'models' / '..' / 'specialist'
    assert '..' in value.specialist_dir.parts


def test_absolute_specialist_directory_is_not_rebased_or_resolved(tmp_path, monkeypatch):
    initial = tmp_path / 'initial'; initial.mkdir(); monkeypatch.chdir(initial)
    original = tmp_path / 'components' / '..' / 'model-store'
    value = configured(specialist_dir=original)
    assert value.specialist_dir == original and '..' in value.specialist_dir.parts


@pytest.mark.parametrize('explicit', [False, True])
def test_changing_to_native_temporary_cwd_does_not_move_an_existing_settings_path(tmp_path, monkeypatch, explicit):
    initial = tmp_path / 'service'; initial.mkdir()
    runner = tmp_path / 'native-temporary'; runner.mkdir()
    monkeypatch.chdir(initial)
    args = {'specialist_dir': Path('components/models')} if explicit else {}
    before = configured(**args); frozen = before.specialist_dir
    monkeypatch.chdir(runner)
    assert before.specialist_dir == frozen and before.specialist_dir.is_absolute()
    later = configured(**args)
    assert later.specialist_dir != before.specialist_dir
    assert later.specialist_dir == runner / ('components/models' if explicit else '.runtime/specialist')


@pytest.mark.parametrize('with_dotdot', [False, True])
def test_symbolic_parent_remains_visible_to_the_original_safe_path_guard(tmp_path, monkeypatch, with_dotdot):
    service = tmp_path / 'service'; service.mkdir()
    real = tmp_path / 'real-model-store'; real.mkdir()
    link = service / 'linked-model-store'; link.symlink_to(real, target_is_directory=True)
    monkeypatch.chdir(service)
    relative = Path('linked-model-store') / ('../weights' if with_dotdot else 'weights')
    value = configured(specialist_dir=relative)
    assert value.specialist_dir == service / relative
    assert link in value.specialist_dir.parents
    if with_dotdot: assert '..' in value.specialist_dir.parts
    with pytest.raises(ValueError, match='符号链接'):
        safe_path(value.specialist_dir / 'fixed-model.safetensors')
    assert link.is_symlink()  # Configuration and validation do not alter it.


def test_symbolic_leaf_is_not_canonicalized_away_before_the_original_guard(tmp_path, monkeypatch):
    real = tmp_path / 'actual-store'; real.mkdir()
    link = tmp_path / 'linked-store'; link.symlink_to(real, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    value = configured(specialist_dir=Path('linked-store'))
    assert value.specialist_dir == link and value.specialist_dir.is_symlink()
    with pytest.raises(ValueError, match='符号链接'): safe_path(value.specialist_dir)


def test_only_specialist_directory_gains_anchoring_other_path_settings_keep_existing_semantics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    storage = Path('.runtime/evidence'); python = Path('.runtime/venvs/rtdetr/bin/python')
    value = configured(specialist_dir=Path('models'), storage_dir=storage, specialist_python=python)
    assert value.specialist_dir == tmp_path / 'models'
    assert value.storage_dir == storage and not value.storage_dir.is_absolute()
    assert value.specialist_python == python and not value.specialist_python.is_absolute()
