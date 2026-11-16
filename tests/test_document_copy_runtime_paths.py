"""Temporary fake packs only. No renderer, database, HTTP or model calls."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from generic_agents import document_copy_runtime as runtime
from generic_agents import runtime_paths


def write(path, content, *, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(content); path.chmod(0o700 if executable else 0o600)


@pytest.fixture(autouse=True)
def no_real_execution(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs): pytest.fail('pure Word path tests must not perform external work')
    monkeypatch.setattr(runtime.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(runtime.subprocess, 'run', forbidden)
    monkeypatch.setattr(runtime.os, 'killpg', forbidden)
    import psycopg
    import sqlalchemy
    import httpx
    monkeypatch.setattr(psycopg, 'connect', forbidden)
    monkeypatch.setattr(sqlalchemy, 'create_engine', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    monkeypatch.setattr(runtime.sys, 'platform', 'darwin')
    sandbox = tmp_path.resolve() / 'not-a-real-sandbox-exec'
    write(sandbox, b'never executed', executable=True)
    monkeypatch.setattr(runtime, '_SANDBOX', sandbox)


@pytest.fixture
def pack(monkeypatch, tmp_path):
    root = tmp_path.resolve() / 'external-components'; root.mkdir(mode=0o700)
    base = root / 'document-copy-m51'
    files = {'LibreOfficeDev.app/Contents/MacOS/soffice': (b'not a renderer', True),
        'LibreOfficeDev.app/Contents/Resources/fixed-resource': (b'exact resource', False)}
    for name, (raw, executable) in files.items(): write(base / name, raw, executable=executable)
    manifest = {'engine_id': runtime._ENGINE_ID, 'revision_policy': runtime.POLICY, 'runtime_version': 'pure fake, not qualification',
        'entrypoint': 'LibreOfficeDev.app/Contents/MacOS/soffice',
        'files': [{'path': name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'executable': executable}
            for name, (raw, executable) in sorted(files.items())]}
    raw = json.dumps(manifest, sort_keys=True).encode(); write(base / 'manifest.json', raw)
    monkeypatch.setattr(runtime, '_MANIFEST_HASH', hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(runtime, 'local_runtime_root', lambda: root)
    return root, base, manifest


def test_historical_identity_and_digest_remain_fixed():
    assert runtime._MANIFEST_HASH == 'fc577b4df71bd9e15f4aa6d675abb9f2df737bd064b4ec95002695a4fd3c6666'
    assert runtime._ENGINE_ID == 'libreoffice-docx-final-v1'


def test_external_pack_selected_without_release_relative_path(pack):
    root, base, expected = pack
    manifest, native = runtime._installation()
    assert manifest == expected and native == base / expected['entrypoint']
    assert runtime.engine_status()['available'] is True
    assert runtime._operation_base() == root / 'document-copy-m51'


def test_default_root_is_instance_derived_without_legacy_fallback_or_mkdir(monkeypatch, tmp_path):
    instance = tmp_path.resolve() / 'private-instance'; instance.mkdir(mode=0o700)
    monkeypatch.setattr(runtime_paths, 'settings', lambda: SimpleNamespace(local_runtime_root=None, storage_dir=instance / 'storage'))
    assert runtime._operation_base() == instance / 'runtimes/document-copy-m51'
    assert runtime.engine_status()['available'] is False
    assert not (instance / 'runtimes').exists()


def test_invalid_root_does_not_launch_or_fallback(monkeypatch):
    def invalid(): raise HTTPException(409, '服务器组件根配置无效')
    monkeypatch.setattr(runtime, 'local_runtime_root', invalid)
    status = runtime.engine_status()
    assert status['available'] is False and status['reason'] == '服务器组件根配置无效'
    with pytest.raises(HTTPException, match='服务器组件根配置无效'):
        runtime.convert(b'unused', revision_policy=runtime.POLICY, expected_manifest_hash=runtime._MANIFEST_HASH)


@pytest.mark.parametrize('change', ['manifest', 'bytes', 'size', 'executable', 'extra', 'missing', 'file_symlink', 'directory_symlink'])
def test_external_selection_preserves_exact_pack_checks(pack, change):
    _, base, _ = pack
    resource = base / 'LibreOfficeDev.app/Contents/Resources/fixed-resource'
    if change == 'manifest': (base / 'manifest.json').write_bytes(b'{}')
    elif change == 'bytes': resource.write_bytes(b'other resource')
    elif change == 'size': resource.write_bytes(b'x')
    elif change == 'executable': resource.chmod(0o700)
    elif change == 'extra': write(base / 'extra', b'not declared')
    elif change == 'missing': resource.unlink()
    elif change == 'file_symlink': resource.unlink(); resource.symlink_to(base / 'manifest.json')
    else: (base / 'alias-dir').symlink_to(resource.parent, target_is_directory=True)
    with pytest.raises(HTTPException) as error: runtime._installation()
    assert error.value.status_code == 409


@pytest.mark.parametrize('where', ['root', 'base', 'loop'])
def test_alias_ancestors_or_pack_never_become_execution_paths(pack, monkeypatch, where):
    root, base, _ = pack
    if where == 'root':
        alias = root.parent / 'root-alias'; alias.symlink_to(root, target_is_directory=True)
        monkeypatch.setattr(runtime, 'local_runtime_root', lambda: alias)
    elif where == 'base':
        moved = root / 'other-location'; base.rename(moved); base.symlink_to(moved, target_is_directory=True)
    else:
        moved = root / 'other-location'; base.rename(moved); base.symlink_to(base)
    with pytest.raises(HTTPException): runtime._installation()


def test_sandbox_grants_only_selected_app_and_private_job(pack, tmp_path):
    root, base, manifest = pack; native = base / manifest['entrypoint']; bundle = native.parents[2]
    job = tmp_path.resolve() / 'job'
    profile = runtime._profile(bundle, job, native)
    assert '(deny default)' in profile and '(deny network*)' in profile
    assert '(subpath ' + json.dumps(str(bundle)) + ')' in profile
    assert '(subpath ' + json.dumps(str(root)) + ')' not in profile
    assert '(subpath ' + json.dumps(str(base)) + ')' not in profile
    assert '(allow file-write* (subpath ' + json.dumps(str(job)) + ')(literal "/dev/null"))' in profile
    assert '(allow process-exec (literal ' + json.dumps(str(native)) + ')(literal "/bin/bash"))' in profile


def fake_conversion(monkeypatch, pack, *, mutate=None):
    from pypdf import PdfWriter
    from generic_agents import document_copy_pdf
    stream = io.BytesIO(); pdf = PdfWriter(); pdf.add_blank_page(width=72, height=72); pdf.write(stream)
    output = stream.getvalue(); launches = []; checks = []; roots = []
    root, base, manifest = pack
    before = {str(p.relative_to(base)): p.read_bytes() for p in base.rglob('*') if p.is_file()}
    installation = runtime._installation
    def choose(): roots.append(root); return root
    def check(**kwargs): checks.append(kwargs['base']); return installation(**kwargs)
    monkeypatch.setattr(runtime, 'local_runtime_root', choose)
    monkeypatch.setattr(runtime, '_installation', check)
    monkeypatch.setattr(runtime, 'normalize_docx', lambda content, policy: (b'normalized fake document', {'source_chinese_unique': 0, 'warnings': []}))
    monkeypatch.setattr(document_copy_pdf, 'strip_internal_navigation', lambda content: (content, {'pure_fake': True}))
    monkeypatch.setattr(document_copy_pdf, 'chinese_text_health', lambda content, unique: {'pure_fake': True})
    monkeypatch.setattr(runtime, '_kill', lambda process: None)
    class Process:
        pid = 999999999; returncode = 0
        def poll(self): return 0
    def spawn(command, **kwargs):
        launches.append((command, kwargs)); job = Path(kwargs['cwd'])
        (job / 'out/input.pdf').write_bytes(output)
        if mutate: mutate()
        # A later settings change must not select a second root during this conversion.
        def reselected(): pytest.fail('same operation selected its runtime twice')
        monkeypatch.setattr(runtime, 'local_runtime_root', reselected)
        return Process()
    monkeypatch.setattr(runtime.subprocess, 'Popen', spawn)
    return output, launches, checks, roots, before


def test_conversion_binds_checks_spawn_and_postcheck_to_one_root(pack, monkeypatch):
    root, base, manifest = pack
    output, launches, checks, roots, before = fake_conversion(monkeypatch, pack)
    result = runtime.convert(b'source bytes', revision_policy=runtime.POLICY, expected_manifest_hash=runtime._MANIFEST_HASH)
    assert roots == [root] and checks == [base, base] and len(launches) == 1
    command, options = launches[0]; native = base / manifest['entrypoint']
    assert str(native) in command and '(subpath ' + json.dumps(str(native.parents[2])) + ')' in command[2]
    assert '(subpath ' + json.dumps(str(root)) + ')' not in command[2]
    assert options['start_new_session'] is True and options['env']['HOME'] == str(options['cwd'])
    assert set(options['env']) == {'HOME', 'TMPDIR', 'PATH', 'LANG', 'LC_ALL', 'SAL_USE_VCLPLUGIN', 'FONTCONFIG_FILE', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'FC_LANG'}
    assert result['content'] == output and result['receipt']['manifest_hash'] == runtime._MANIFEST_HASH
    assert result['receipt']['page_count'] == 1
    assert {str(p.relative_to(base)): p.read_bytes() for p in base.rglob('*') if p.is_file()} == before


def test_same_root_postcheck_rejects_tampering_without_accepting_pdf(pack, monkeypatch):
    _, base, _ = pack
    target = base / 'LibreOfficeDev.app/Contents/Resources/fixed-resource'
    _, launches, checks, roots, _ = fake_conversion(monkeypatch, pack, mutate=lambda: target.write_bytes(b'tampered'))
    with pytest.raises(HTTPException):
        runtime.convert(b'source bytes', revision_policy=runtime.POLICY, expected_manifest_hash=runtime._MANIFEST_HASH)
    assert len(launches) == 1 and checks == [base, base] and len(roots) == 1


def test_expected_hash_rejected_before_root_lookup_or_process(monkeypatch):
    monkeypatch.setattr(runtime, 'local_runtime_root', lambda: pytest.fail('must reject before selecting any root'))
    with pytest.raises(HTTPException):
        runtime.convert(b'unused', revision_policy=runtime.POLICY, expected_manifest_hash='0' * 64)
