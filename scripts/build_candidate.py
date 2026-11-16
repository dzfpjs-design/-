"""Assemble a source/build-bound candidate without reading runtime or secrets."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import zipfile

ROOT = Path(__file__).absolute().parents[1]


def need(value, message):
    if not value:
        raise RuntimeError(message)


def record(path):
    need(path.resolve(strict=True) == path, 'source_alias_rejected')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
             and before.st_nlink == 1 and not before.st_mode & 0o022, 'unsafe_source_file')
        parts = []
        while part := os.read(fd, 1024 * 1024):
            parts.append(part)
        after = os.fstat(fd)
        need(all(getattr(before, key) == getattr(after, key) for key in
            ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')), 'source_changed')
        raw = b''.join(parts)
        return raw, {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    finally:
        os.close(fd)


def tree(folder):
    need(folder.resolve(strict=True) == folder and folder.is_dir(), 'tree_alias_rejected')
    values = {}
    for current, dirs, files in os.walk(folder, followlinks=False):
        # Caches do not enter a release; aliases/special entries never get followed.
        for name in list(dirs):
            path = Path(current) / name
            need(not path.is_symlink(), 'source_directory_alias_rejected')
            if name == '__pycache__' or name.endswith('.egg-info'):
                dirs.remove(name)
        for name in files:
            path = Path(current) / name
            if name.endswith(('.pyc', '.pyo')) or name == '.DS_Store':
                continue
            need(not name.startswith('.env'), 'environment_file_in_source')
            _, value = record(path)
            values[str(path.relative_to(folder))] = value
    return values


def inputs(root):
    values = {}
    for name in ('src/generic_agents', 'model-packs', 'runtime-packs',
                 'design/resource-studio/src', 'design/resource-studio/public'):
        values.update({name + '/' + path: value for path, value in tree(root / name).items()})
    for name in ('pyproject.toml', 'requirements.lock', 'scripts/convert_changer_weights.py',
                 'scripts/candidate_runtime.py', 'scripts/prepare_instance.py', 'scripts/build_candidate.py',
                 'scripts/prepare_local_audio.py', 'scripts/prepare_local_vad.py',
                 'design/resource-studio/index.html', 'design/resource-studio/package.json',
                 'design/resource-studio/pnpm-lock.yaml', 'design/resource-studio/vite.config.mjs'):
        _, values[name] = record(root / name)
    return values


def safe_directory(path, root, *, create=False):
    """Reject output aliases before the first write, including existing ancestors."""
    root = Path(root).absolute(); path = Path(path).absolute()
    need(root.resolve(strict=True) == root and path.is_relative_to(root), 'output_directory_alias_rejected')
    current = root
    for name in (None, *path.relative_to(root).parts):
        if name is not None:
            current = current / name
        if not os.path.lexists(current):
            need(create, 'output_directory_missing')
            current.mkdir(mode=0o755)
        info = current.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and not info.st_mode & 0o022 and current.resolve(strict=True) == current,
             'output_directory_alias_rejected')


def write_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw); output.flush(); os.fsync(output.fileno())


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def assemble(root, release_id, static_dir, receipt_path, receipt_sha):
    root = Path(root).absolute(); static_dir = Path(static_dir).absolute()
    need(re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', release_id) and release_id not in {'.', '..'}, 'invalid_release_id')
    need(static_dir.is_relative_to(root / '.runtime/candidate-delivery')
         and static_dir.resolve(strict=True) == static_dir, 'explicit_private_build_required')
    raw, receipt_record = record(Path(receipt_path).absolute())
    need(receipt_record['sha256'] == receipt_sha, 'build_receipt_changed')
    receipt = json.loads(raw)
    need(receipt.get('format') == 'candidate-build-receipt-v1'
         and receipt['inputs_before'] == receipt['inputs_after'] == inputs(root)
         and receipt['static_files'] == tree(static_dir)
         and receipt['build_exit_code'] == 0, 'build_source_or_assets_changed')
    target = root / 'release-candidates' / release_id
    safe_directory(target.parent, root, create=True)
    need(not os.path.lexists(target), 'candidate_already_exists')
    target.mkdir(parents=True, mode=0o755)
    files = {}
    def copy(source, name, expected=None):
        content, value = record(source)
        need(expected is None or value == expected, 'copy_source_changed')
        write_new(target / name, content); files[name] = value
    for name, expected in receipt['inputs_after'].items():
        if name.startswith(('src/generic_agents/', 'model-packs/', 'runtime-packs/')) or name in {
                'pyproject.toml', 'requirements.lock', 'scripts/convert_changer_weights.py'}:
            copy(root / name, name, expected)
        elif name in {'scripts/prepare_local_audio.py', 'scripts/prepare_local_vad.py'}:
            copy(root / name, name, expected)
    for name, expected in receipt['static_files'].items():
        copy(static_dir / name, 'static/setup/' + name, expected)
    write_new(target / 'build-provenance.json', raw); files['build-provenance.json'] = receipt_record
    mqtt = b'paho-mqtt==2.1.0\n'
    write_new(target / 'requirements-mqtt.txt', mqtt)
    files['requirements-mqtt.txt'] = {'bytes': len(mqtt), 'sha256': hashlib.sha256(mqtt).hexdigest()}
    manifest = {'format': 'generic-agents-candidate-v1', 'release_id': release_id,
        'backend_src': 'src', 'static_dist': 'static/setup', 'setup_worker_scope': 'workspace',
        'schema_model_sha256': files['src/generic_agents/models.py']['sha256'],
        'files': [{'path': name, **value} for name, value in sorted(files.items())]}
    manifest_path = target / 'release-manifest.json'; manifest_raw = encode(manifest)
    write_new(manifest_path, manifest_raw)
    spec = importlib.util.spec_from_file_location('candidate_builder_verifier', root / 'scripts/candidate_runtime.py')
    runtime = importlib.util.module_from_spec(spec); spec.loader.exec_module(runtime)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    runtime.load_release(manifest_path, manifest_sha, root=root)
    need(inputs(root) == receipt['inputs_after'], 'sources_changed_during_assembly')
    return {'release_id': release_id, 'manifest': str(manifest_path), 'manifest_sha256': manifest_sha,
            'files': len(files), 'schema_compatibility': 'unverified', 'runtime_started': False}


def archive(root, result, guide):
    root = Path(root).absolute(); target = root / 'release-candidates' / result['release_id']
    need(re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', result['release_id'])
         and result['release_id'] not in {'.', '..'}
         and Path(result['manifest']).absolute() == target / 'release-manifest.json', 'invalid_archive_release')
    safe_directory(target, root)
    output = root / 'deliverables' / (result['release_id'] + '.zip')
    safe_directory(output.parent, root, create=True)
    manifest_raw, manifest_record = record(Path(result['manifest']))
    need(manifest_record['sha256'] == result['manifest_sha256'], 'candidate_manifest_changed')
    pinned_manifest = json.loads(manifest_raw)
    pinned_provenance = [item for item in pinned_manifest['files'] if item['path'] == 'build-provenance.json']
    provenance_raw, provenance_record = record(target / 'build-provenance.json')
    need(len(pinned_provenance) == 1 and provenance_record ==
         {key: pinned_provenance[0][key] for key in ('bytes', 'sha256')}, 'candidate_provenance_changed')
    provenance = json.loads(provenance_raw)
    need(inputs(root) == provenance['inputs_after'], 'sources_changed_before_archive')
    spec = importlib.util.spec_from_file_location('candidate_archive_verifier', root / 'scripts/candidate_runtime.py')
    runtime = importlib.util.module_from_spec(spec); spec.loader.exec_module(runtime)
    verified = runtime.load_release(Path(result['manifest']), result['manifest_sha256'], root=root)
    expected = {item['path']: {key: item[key] for key in ('bytes', 'sha256')}
                for item in verified['manifest']['files']}
    expected['release-manifest.json'] = verified['record']
    need(not os.path.lexists(output), 'archive_already_exists')
    temporary = output.with_name('.' + output.name + '.building')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream, zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        runner, runner_record = record(root / 'scripts/candidate_runtime.py')
        need(runner_record == provenance['inputs_after']['scripts/candidate_runtime.py'], 'runner_changed_before_archive')
        entries = {'scripts/candidate_runtime.py': runner, 'README.md': guide.encode()}
        installer, installer_record = record(root / 'scripts/prepare_instance.py')
        need(installer_record == provenance['inputs_after']['scripts/prepare_instance.py'], 'installer_changed_before_archive')
        entries['scripts/prepare_instance.py'] = installer
        for name, expected_record in expected.items():
            content, actual_record = record(target / name)
            need(actual_record == expected_record, 'candidate_changed_during_archive')
            entries['release-candidates/' + result['release_id'] + '/' + name] = content
        for name, raw in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 15, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, raw)
    runtime.load_release(Path(result['manifest']), result['manifest_sha256'], root=root)
    need(inputs(root) == provenance['inputs_after'], 'sources_changed_during_archive')
    # Publish the complete archive without replacing an existing delivery.
    os.chmod(temporary, 0o644)
    os.link(temporary, output, follow_symlinks=False)
    temporary.unlink()
    _, output_record = record(output)
    return {'path': str(output), **output_record}


def main():
    parser = argparse.ArgumentParser(description='只组装候选文件，不启动服务或修改数据库')
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--static-dir', type=Path, required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--build-receipt-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(assemble(ROOT, args.release_id, args.static_dir,
        args.build_receipt, args.build_receipt_sha256), ensure_ascii=False))


if __name__ == '__main__':
    main()
