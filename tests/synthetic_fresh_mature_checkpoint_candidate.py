"""Private checkpoint of a fresh synthetic mature Goal; never application data."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import func, inspect, select, text

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job, Task
from tests.synthetic_advice_checkpoint import pg_program, require


def save(task_id, gateway_key):
    name = os.environ.get('GA_SYNTHETIC_FRESH_GOAL_CHECKPOINT')
    if not name: return
    root = Path(name).resolve(); temp = Path(tempfile.gettempdir()).resolve()
    require(root.parent == temp and root.name.startswith('advice-fresh-mature-checkpoint-'))
    require(root.is_dir() and root.stat().st_uid == os.getuid() and not any(root.iterdir()))
    require(root.stat().st_mode & 0o077 == 0)
    url = engine().url; data = Path(os.environ['GA_TEST_PG_DATA_DIR']).resolve()
    require(url.host == '127.0.0.1' and url.port and url.username == 'advice_test' and url.password is None
        and url.database and url.database.startswith('ga_test_') and data.name == 'data'
        and data.parent.name.startswith('advice-race-pg-'))
    require(not os.environ.get('DEEPSEEK_API_KEY') and not os.environ.get('DASHSCOPE_API_KEY')
        and not settings().cloud_allowed_providers)
    storage = Path(settings().storage_dir).resolve(); key = Path(gateway_key).resolve()
    require(storage.parent == temp and storage.name.startswith('ga-test-evidence-') and storage.is_dir())
    require(temp in key.parents and key.name == 'master.key' and key.parent.name == 'gateway-keys' and key.is_file())
    require(not any(item.is_symlink() for item in storage.rglob('*')))
    require(sum(item.stat().st_size for item in storage.rglob('*') if item.is_file()) <= 100 * 1024 * 1024)
    dump = root / 'synthetic-mature.pgcustom'
    with engine().begin() as db:
        actual, system_id = db.execute(text("SELECT current_setting('data_directory'), "
            "(SELECT system_identifier::text FROM pg_control_system())")).one()
        require(Path(actual).resolve() == data and system_id == os.environ['GA_TEST_PG_SYSTEM_IDENTIFIER'])
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        tables = inspect(db).get_table_names(schema='public'); require(bool(tables))
        quote = db.dialect.identifier_preparer.quote_identifier
        db.execute(text('LOCK TABLE ' + ', '.join(quote('public') + '.' + quote(name) for name in tables) + ' IN SHARE MODE'))
        require(db.scalar(select(Task.id).where(Task.id == task_id)) == task_id)
        require(db.scalar(select(func.count()).select_from(Job).where(Job.status == 'running')) == 0)
        require(db.scalar(select(func.count()).select_from(CloudCall)) == 0)
        subprocess.run([pg_program('pg_dump'), '-h', '127.0.0.1', '-p', str(url.port), '-U', url.username,
            '--format=custom', '--no-owner', '--no-privileges', '--file', str(dump), url.database],
            check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        shutil.copytree(storage, root / 'evidence', symlinks=False)
        shutil.copyfile(key, root / 'synthetic-test-gateway.key')
    os.chmod(dump, 0o600); os.chmod(root / 'synthetic-test-gateway.key', 0o600)
    manifest = {'schema': 'isolated-synthetic-fresh-mature-checkpoint-v1', 'task_id': task_id,
        'dump_sha256': hashlib.sha256(dump.read_bytes()).hexdigest(),
        'key_sha256': hashlib.sha256((root / 'synthetic-test-gateway.key').read_bytes()).hexdigest(),
        'files': {str(item.relative_to(root / 'evidence')): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted((root / 'evidence').rglob('*')) if item.is_file()}}
    (root / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
    os.chmod(root / 'manifest.json', 0o600)
    print('ISOLATED_FRESH_SYNTHETIC_MATURE_CHECKPOINT_SAVED', flush=True)
