"""Release assembly and archive tests: no build process, service or database."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).absolute().parents[1]
SPEC = importlib.util.spec_from_file_location('candidate_build_test_module', ROOT / 'scripts/build_candidate.py')
builder = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(builder)


def fixture(root):
    files = {f'src/generic_agents/{name}.py': b'# code\n' for name in ('__init__', 'cli', 'api', 'models')}
    files.update({'model-packs/example.json': b'{}', 'runtime-packs/example.json': b'{}',
        'pyproject.toml': b'[project]\nname="example"\n', 'requirements.lock': b'',
        'scripts/convert_changer_weights.py': b'# converter\n', 'scripts/build_candidate.py': b'# builder\n',
        'scripts/prepare_instance.py': b'# installer\n',
        'scripts/prepare_local_audio.py': b'# explicit audio preparation\n',
        'scripts/prepare_local_vad.py': b'# explicit vad preparation\n',
        'scripts/candidate_runtime.py': (ROOT / 'scripts/candidate_runtime.py').read_bytes(),
        'design/resource-studio/src/main.jsx': b'// source\n',
        'design/resource-studio/public/icon.svg': b'<svg/>',
        **{'design/resource-studio/' + name: b'{}' for name in
           ('index.html', 'package.json', 'pnpm-lock.yaml', 'vite.config.mjs')}})
    static = root / '.runtime/candidate-delivery/runtime-build'
    files.update({str((static / 'index.html').relative_to(root)): b'<script src="/setup/assets/app.js"></script>',
                  str((static / 'assets/app.js').relative_to(root)): b'// compiled\n',
                  'src/generic_agents/__pycache__/ignored.pyc': b'not shipping',
                  '.env': b'synthetic-secret-not-part-of-delivery'})
    for name, data in files.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    source = builder.inputs(root)
    receipt = {'format': 'candidate-build-receipt-v1', 'inputs_before': source,
               'inputs_after': source, 'static_files': builder.tree(static), 'build_exit_code': 0}
    path = root / '.runtime/candidate-delivery/receipt.json'
    path.write_bytes(builder.encode(receipt))
    return static, path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_assembled_archive_binds_code_ui_recipes_and_omits_runtime_cache_and_secrets(tmp_path):
    static, receipt, digest = fixture(tmp_path)
    result = builder.assemble(tmp_path, 'candidate-test', static, receipt, digest)
    manifest = json.loads(Path(result['manifest']).read_text())
    names = {row['path'] for row in manifest['files']}
    assert {'src/generic_agents/api.py', 'static/setup/index.html', 'model-packs/example.json',
            'runtime-packs/example.json', 'requirements.lock',
            'scripts/prepare_local_audio.py', 'scripts/prepare_local_vad.py'} <= names
    assert not any('.env' in name or '__pycache__' in name for name in names)
    output = builder.archive(tmp_path, result, 'Candidate installation; real runtime unverified.')
    with zipfile.ZipFile(output['path']) as archive:
        assert 'scripts/candidate_runtime.py' in archive.namelist()
        assert archive.read('scripts/prepare_instance.py') == b'# installer\n'
        assert archive.read('release-candidates/candidate-test/scripts/prepare_local_audio.py') == b'# explicit audio preparation\n'
        assert archive.read('release-candidates/candidate-test/scripts/prepare_local_vad.py') == b'# explicit vad preparation\n'
        assert 'release-candidates/candidate-test/release-manifest.json' in archive.namelist()
        assert not any(name.startswith('/') or '..' in Path(name).parts for name in archive.namelist())
    assert (tmp_path / '.env').read_bytes() == b'synthetic-secret-not-part-of-delivery'


@pytest.mark.parametrize('change', ['source', 'assets', 'receipt_hash', 'normal_build'])
def test_changed_or_unproven_build_never_becomes_candidate(tmp_path, change):
    static, receipt, digest = fixture(tmp_path)
    if change == 'source': (tmp_path / 'src/generic_agents/api.py').write_text('# changed')
    if change == 'assets': (static / 'assets/app.js').write_text('// changed')
    if change == 'receipt_hash': digest = '0' * 64
    if change == 'normal_build':
        static = tmp_path / 'design/resource-studio/dist/runtime'; static.mkdir(parents=True)
    with pytest.raises(RuntimeError):
        builder.assemble(tmp_path, 'candidate-test', static, receipt, digest)
    assert not (tmp_path / 'release-candidates/candidate-test').exists()


def test_immutable_candidate_cannot_be_overwritten_or_archived_with_changed_runner(tmp_path):
    static, receipt, digest = fixture(tmp_path)
    result = builder.assemble(tmp_path, 'candidate-test', static, receipt, digest)
    with pytest.raises(RuntimeError, match='candidate_already_exists'):
        builder.assemble(tmp_path, 'candidate-test', static, receipt, digest)
    (tmp_path / 'scripts/candidate_runtime.py').write_text('# changed runner')
    with pytest.raises(RuntimeError, match='sources_changed_before_archive'):
        builder.archive(tmp_path, result, 'guide')


def test_source_directory_alias_is_rejected(tmp_path):
    fixture(tmp_path)
    alias = tmp_path / 'model-packs/alias'; alias.symlink_to(tmp_path / 'src', target_is_directory=True)
    with pytest.raises(RuntimeError, match='source_directory_alias_rejected'):
        builder.inputs(tmp_path)


@pytest.mark.parametrize('change', ['modified', 'extra'])
def test_changed_candidate_files_never_enter_published_archive(tmp_path, change):
    static, receipt, digest = fixture(tmp_path)
    result = builder.assemble(tmp_path, 'candidate-test', static, receipt, digest)
    target = Path(result['manifest']).parent
    if change == 'modified': (target / 'static/setup/assets/app.js').write_text('// changed')
    else: (target / 'private-not-an-env-file.txt').write_text('must not ship')
    with pytest.raises(RuntimeError): builder.archive(tmp_path, result, 'guide')
    assert not (tmp_path / 'deliverables/candidate-test.zip').exists()


@pytest.mark.parametrize('operation', ['assemble', 'archive'])
def test_output_parent_alias_rejected_before_external_write(tmp_path, operation):
    root = tmp_path / 'project'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    static, receipt, digest = fixture(root)
    if operation == 'assemble':
        (root / 'release-candidates').symlink_to(outside, target_is_directory=True)
        with pytest.raises(RuntimeError, match='output_directory_alias_rejected'):
            builder.assemble(root, 'candidate-test', static, receipt, digest)
    else:
        result = builder.assemble(root, 'candidate-test', static, receipt, digest)
        (root / 'deliverables').symlink_to(outside, target_is_directory=True)
        with pytest.raises(RuntimeError, match='output_directory_alias_rejected'):
            builder.archive(root, result, 'guide')
    assert list(outside.iterdir()) == []


def test_modified_provenance_and_runner_cannot_authorize_import(tmp_path):
    static, receipt, digest = fixture(tmp_path)
    result = builder.assemble(tmp_path, 'candidate-test', static, receipt, digest)
    (tmp_path / 'scripts/candidate_runtime.py').write_text('raise AssertionError("unbound_runner_executed")')
    provenance = Path(result['manifest']).parent / 'build-provenance.json'
    value = json.loads(provenance.read_text())
    value['inputs_after'] = builder.inputs(tmp_path)
    provenance.write_bytes(builder.encode(value))
    with pytest.raises(RuntimeError, match='candidate_provenance_changed'):
        builder.archive(tmp_path, result, 'guide')
    assert not (tmp_path / 'deliverables/candidate-test.zip').exists()
