from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import pytest

SPEC = importlib.util.spec_from_file_location('runtime_source_under_test', Path(__file__).parents[1] / 'scripts/runtime_source.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def bundle(root):
    source = root / '.runtime/releases/test/backend'
    package = source / 'generic_agents'
    package.mkdir(parents=True)
    code = package / '__init__.py'
    code.write_text('value = 1\n')
    manifest = source / 'source-manifest.json'
    manifest.write_text(json.dumps({'files': [{'path': 'generic_agents/__init__.py', 'sha256': sha256(code.read_bytes()).hexdigest()}]}))
    pointer = root / '.runtime/active-backend-source.json'
    pointer.write_text(json.dumps({'format': 1, 'source_dir': str(source.relative_to(root)), 'manifest_sha256': sha256(manifest.read_bytes()).hexdigest()}))
    return source, code, pointer


def test_no_pointer_keeps_legacy_environment(tmp_path):
    original = {'PATH': '/bin', 'PYTHONPATH': '/existing'}
    assert MODULE.service_environment(tmp_path, original) == original


def test_verified_bundle_overrides_working_source_without_mutating_environment(tmp_path):
    source, _, _ = bundle(tmp_path)
    inherited = {'PATH': '/bin', 'PYTHONPATH': '/working', 'TEST_VALUE': 'retained'}
    result = MODULE.service_environment(tmp_path, inherited)
    assert result['PYTHONPATH'] == str(source)
    assert result['PYTHONSAFEPATH'] == result['PYTHONDONTWRITEBYTECODE'] == '1'
    assert result['TEST_VALUE'] == 'retained' and inherited['PYTHONPATH'] == '/working'
    assert result['GA_SETUP_BUILD_DIR'] == str(tmp_path / 'design/resource-studio/dist/runtime')


@pytest.mark.parametrize('change', ['code', 'extra', 'symlink', 'pointer_outside', 'manifest'])
def test_changed_or_unowned_runtime_cannot_start(tmp_path, change):
    source, code, pointer = bundle(tmp_path)
    if change == 'code': code.write_text('value = 2\n')
    elif change == 'extra': (code.parent / 'unrecorded.py').write_text('value = 3\n')
    elif change == 'symlink':
        outside = tmp_path / 'outside.py'; outside.write_text(code.read_text()); code.unlink(); code.symlink_to(outside)
    elif change == 'pointer_outside':
        value = json.loads(pointer.read_text()); value['source_dir'] = '../outside'; pointer.write_text(json.dumps(value))
    else: (source / 'source-manifest.json').write_text('{}')
    with pytest.raises((RuntimeError, OSError)):
        MODULE.service_environment(tmp_path, {})
