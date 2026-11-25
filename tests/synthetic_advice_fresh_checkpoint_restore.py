"""Restore fresh *synthetic* mature Goal fixtures into a disposable PG.

Only a dedicated, exclusively owned test runner may use this helper. Neither
its checks nor its process-local lock can prevent an uncooperative external
writer connecting between the exclusivity check and pg_restore; it is not an
application backup/restore tool. No existing approval, deadline or source is
rewritten, and cloud/physical device connections must remain disabled.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from uuid import UUID

from sqlalchemy import inspect, text
from generic_agents.config import settings
from generic_agents.db import engine
from tests.synthetic_advice_checkpoint import pg_program, require


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_private_tree(source, target, expected):
    """Copy only authenticated regular files into a private test directory."""
    os.chmod(target, 0o700)
    for item in sorted(source.rglob('*')):
        relative = item.relative_to(source)
        destination = target / relative
        require(not item.is_symlink())
        if item.is_dir():
            destination.mkdir(mode=0o700)
            os.chmod(destination, 0o700)
            continue
        require(item.is_file() and item.stat().st_nlink == 1)
        source_fd = os.open(item, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            source_stat = os.fstat(source_fd)
            require(stat.S_ISREG(source_stat.st_mode) and source_stat.st_uid == os.getuid() and source_stat.st_nlink == 1)
            target_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(target_fd, 0o600)
                with os.fdopen(source_fd, 'rb', closefd=False) as inp, os.fdopen(target_fd, 'wb', closefd=False) as out:
                    shutil.copyfileobj(inp, out)
            finally:
                os.close(target_fd)
        finally:
            os.close(source_fd)
        require(_sha(destination) == expected[str(relative)])


def restore_to_disposable_test_db(root_name):
    temp = Path(tempfile.gettempdir()).resolve()
    root_path = Path(root_name)
    require(root_path.is_absolute() and not root_path.is_symlink())
    root = root_path.resolve()
    require(root.parent == temp and root.name.startswith('advice-fresh-mature-checkpoint-'))
    require(root.is_dir() and root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0)
    entries = list(root.rglob('*'))
    require(all(not item.is_symlink() and (item.is_dir() or item.is_file())
        and item.stat().st_uid == os.getuid() and (not item.is_file() or item.stat().st_nlink == 1) for item in entries))
    require(sum(item.stat().st_size for item in entries if item.is_file()) <= 200 * 1024 * 1024)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    require(type(manifest) is dict and set(manifest) == {'schema','task_id','dump_sha256','key_sha256','files'})
    require(manifest['schema'] == 'isolated-synthetic-fresh-mature-checkpoint-v1')
    require(type(manifest['task_id']) is str and str(UUID(manifest['task_id'])) == manifest['task_id'])
    dump = root / 'synthetic-mature.pgcustom'; key = root / 'synthetic-test-gateway.key'; source = root / 'evidence'
    require(dump.is_file() and key.is_file() and source.is_dir())
    require(key.stat().st_uid == os.getuid() and key.stat().st_mode & 0o077 == 0)
    require(_sha(dump) == manifest['dump_sha256'] and _sha(key) == manifest['key_sha256'])
    actual = {str(item.relative_to(source)): _sha(item) for item in sorted(source.rglob('*')) if item.is_file()}
    require(type(manifest['files']) is dict and actual == manifest['files'])
    require(not os.environ.get('DEEPSEEK_API_KEY') and not os.environ.get('DASHSCOPE_API_KEY'))
    require(not settings().cloud_allowed_providers)
    url = engine().url
    require(url.host == '127.0.0.1' and url.port and url.database and url.database.startswith('ga_test_'))
    require(url.username == 'advice_test' and url.password is None)
    data = Path(os.environ['GA_TEST_PG_DATA_DIR']).resolve()
    require(data.name == 'data' and data.parent.name.startswith('advice-race-pg-'))
    target = Path(settings().storage_dir).resolve()
    require(target.parent == temp and target.name.startswith('ga-test-evidence-')
        and target.is_dir() and target.stat().st_uid == os.getuid() and not any(target.iterdir()))
    # This lock protects only cooperating instances of this dedicated runner.
    lock_path = data.parent / 'synthetic-fresh-restore.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        engine().dispose()
        with engine().connect() as conn:
            actual_dir, system_id = conn.execute(text("SELECT current_setting('data_directory'), "
                "(SELECT system_identifier::text FROM pg_control_system())")).one()
            require(Path(actual_dir).resolve() == data and system_id == os.environ['GA_TEST_PG_SYSTEM_IDENTIFIER'])
            tables = inspect(conn).get_table_names(schema='public'); require(bool(tables))
            quote = conn.dialect.identifier_preparer.quote_identifier
            for name in tables:
                require(not conn.scalar(text('SELECT EXISTS (SELECT 1 FROM ' + quote('public') + '.' + quote(name) + ' LIMIT 1)')))
            others = conn.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND backend_type = 'client backend'"))
            require(others == 0)
        engine().dispose()
        subprocess.run([pg_program('pg_restore'), '-h', '127.0.0.1', '-p', str(url.port), '-U', url.username,
            '--dbname', url.database, '--single-transaction', '--clean', '--if-exists', '--exit-on-error',
            '--no-owner', '--no-privileges', str(dump)], check=True, timeout=180,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _copy_private_tree(source, target, manifest['files'])
        settings().device_gateway_key_file = key
    return manifest['task_id']
