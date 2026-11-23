"""Restore an explicitly synthetic checkpoint to a fresh disposable PG."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from sqlalchemy import inspect, text
from generic_agents.config import settings
from generic_agents.db import engine
from tests.synthetic_advice_checkpoint import pg_program, require

def restore_to_disposable_test_db(root_name):
    """Restore only into conftest's already-created random disposable DB.

    No saved Audit, session, deadline or source pin is rewritten. Real guards
    continue to reject expired authorization or unavailable dependencies.
    """
    root = Path(root_name).resolve(); temp_root = Path(tempfile.gettempdir()).resolve()
    require(root.parent == temp_root and root.name.startswith('advice-synthetic-checkpoint-'))
    require(root.is_dir() and root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0)
    require(not any(item.is_symlink() for item in root.rglob('*')))
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    require(manifest['schema'] == 'isolated-synthetic-advice-start-checkpoint-v1')
    dump = root / 'synthetic-start.pgcustom'; key = root / 'synthetic-test-gateway.key'
    require(hashlib.sha256(dump.read_bytes()).hexdigest() == manifest['dump_sha256'])
    require(hashlib.sha256(key.read_bytes()).hexdigest() == manifest['synthetic_gateway_key_sha256'])
    source = root / 'evidence'
    actual_files = {str(item.relative_to(source)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(source.rglob('*')) if item.is_file()}
    require(actual_files == manifest['files'])
    url = engine().url
    require(url.host == '127.0.0.1' and url.port and url.database and url.database.startswith('ga_test_'))
    require(url.username == 'advice_test' and url.password is None)
    data = Path(os.environ['GA_TEST_PG_DATA_DIR']).resolve()
    require(data.name == 'data' and data.parent.name.startswith('advice-race-pg-'))
    engine().dispose()  # Close this test process's idle pool before exclusivity check.
    with engine().connect() as conn:
        actual_dir, system_id = conn.execute(text("SELECT current_setting('data_directory'), "
            "(SELECT system_identifier::text FROM pg_control_system())")).one()
        require(Path(actual_dir).resolve() == data and system_id == os.environ['GA_TEST_PG_SYSTEM_IDENTIFIER'])
        # This helper is for a dedicated test process and a newly created,
        # empty test database. A missing Task alone is not evidence of that.
        tables = inspect(conn).get_table_names(schema='public')
        require(bool(tables))
        quote = conn.dialect.identifier_preparer.quote_identifier
        for name in tables:
            require(not conn.scalar(text('SELECT EXISTS (SELECT 1 FROM ' + quote('public') + '.' + quote(name) + ' LIMIT 1)')))
        others = conn.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND pid <> pg_backend_pid() AND backend_type = 'client backend'"))
        require(others == 0)
    require(not os.environ.get('DEEPSEEK_API_KEY') and not os.environ.get('DASHSCOPE_API_KEY'))
    require(not settings().cloud_allowed_providers)
    target = Path(settings().storage_dir).resolve()
    require(target.parent == temp_root and target.name.startswith('ga-test-evidence-') and not any(target.iterdir()))
    engine().dispose()
    subprocess.run([pg_program('pg_restore'), '-h', '127.0.0.1', '-p', str(url.port), '-U', url.username,
        '--dbname', url.database, '--clean', '--if-exists', '--exit-on-error', '--no-owner', '--no-privileges', str(dump)],
        check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir(): shutil.copytree(item, destination, symlinks=False)
        else: shutil.copyfile(item, destination)
    settings().device_gateway_key_file = key
    print('ISOLATED_SYNTHETIC_START_CHECKPOINT_RESTORED', flush=True)
