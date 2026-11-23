"""Optional private backup of the *isolated synthetic test* Start checkpoint.

Never reads configuration or evidence from the user's workspace. The disposable
test cluster and storage directory have already been checked by conftest; the
additional assertions here deliberately reject normal application paths.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from sqlalchemy import func, inspect, select, text

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import CloudCall, Invocation, Job, Plan, Task

def require(condition, message='synthetic checkpoint isolation check failed'):
    if not condition:
        raise RuntimeError(message)


def pg_program(name):
    require(name in {'pg_dump', 'pg_restore'})
    program = shutil.which(name)
    if program is None:
        config = shutil.which('pg_config')
        require(config is not None, 'isolated checkpoint requires PostgreSQL client tools')
        bindir = subprocess.check_output([config, '--bindir'], text=True, timeout=10).strip()
        candidate = Path(bindir) / name
        require(candidate.is_file() and os.access(candidate, os.X_OK))
        program = str(candidate)
    return program


def save_if_requested(task_id, plan_id, synthetic_gateway_key):
    root_name = os.environ.get('GA_SYNTHETIC_ADVICE_CHECKPOINT')
    if not root_name:
        return
    root = Path(root_name).resolve(); temp_root = Path(tempfile.gettempdir()).resolve()
    require(root.parent == temp_root and root.name.startswith('advice-synthetic-checkpoint-'))
    require(root.is_dir() and not any(root.iterdir()))
    url = engine().url
    require(url.host == '127.0.0.1' and url.port and url.database and url.database.startswith('ga_test_'))
    require(url.username == 'advice_test' and url.password is None)
    data = Path(os.environ['GA_TEST_PG_DATA_DIR']).resolve()
    require(data.name == 'data' and data.parent.name.startswith('advice-race-pg-'))
    require(not os.environ.get('DEEPSEEK_API_KEY') and not os.environ.get('DASHSCOPE_API_KEY'))
    require(not settings().cloud_allowed_providers)
    evidence = Path(settings().storage_dir).resolve()
    require(evidence.parent == temp_root and evidence.name.startswith('ga-test-evidence-') and evidence.is_dir())
    synthetic_gateway_key = Path(synthetic_gateway_key).resolve()
    require(synthetic_gateway_key.name == 'master.key' and synthetic_gateway_key.parent.name == 'gateway-keys')
    require(temp_root in synthetic_gateway_key.parents and synthetic_gateway_key.is_file())
    os.chmod(root, 0o700)
    dump = root / 'synthetic-start.pgcustom'
    # Dedicated fixture only: it owns the test database and evidence directory;
    # external file writers are outside a PostgreSQL lock's protection.
    with engine().begin() as conn:
        actual_dir, system_id = conn.execute(text("SELECT current_setting('data_directory'), "
            "(SELECT system_identifier::text FROM pg_control_system())")).one()
        require(Path(actual_dir).resolve() == data and system_id == os.environ['GA_TEST_PG_SYSTEM_IDENTIFIER'])
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        tables = inspect(conn).get_table_names(schema='public')
        require(bool(tables))
        quote = conn.dialect.identifier_preparer.quote_identifier
        conn.execute(text('LOCK TABLE ' + ', '.join(quote('public') + '.' + quote(name) for name in tables)
            + ' IN SHARE MODE'))
        require(conn.scalar(select(func.count()).select_from(Job).where(Job.status == 'running')) == 0)
        require(conn.scalar(select(func.count()).select_from(CloudCall)) == 0)
        task = conn.execute(select(Task.id, Task.workspace_id).where(Task.id == task_id)).one()
        plan = conn.execute(select(Plan.task_id, Plan.data).where(Plan.id == plan_id)).one()
        require(plan.task_id == task.id and plan.data['schema'] == 'same-goal-advice-execution-v302')
        job_id = plan.data['advice_execution_v308']['job_ref']['id']
        job = conn.execute(select(Job.status, Job.kind).where(Job.id == job_id)).one()
        require(job.status == 'queued' and job.kind == 'generate_same_goal_advice_v302')
        require(conn.scalar(select(func.count()).select_from(Invocation)
            .where(Invocation.data['job_id'].as_string() == job_id)) == 0)
        entries = list(evidence.rglob('*'))
        require(not any(item.is_symlink() for item in entries))
        require(sum(item.stat().st_size for item in entries if item.is_file()) <= 100 * 1024 * 1024)
        subprocess.run([pg_program('pg_dump'), '-h', '127.0.0.1', '-p', str(url.port), '-U', url.username,
            '--format=custom', '--no-owner', '--no-privileges', '--file', str(dump), url.database],
            check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        shutil.copytree(evidence, root / 'evidence', symlinks=False)
        shutil.copyfile(synthetic_gateway_key, root / 'synthetic-test-gateway.key')
    os.chmod(dump, 0o600)
    os.chmod(root / 'synthetic-test-gateway.key', 0o600)
    manifest = {'schema': 'isolated-synthetic-advice-start-checkpoint-v1',
        'dump_sha256': hashlib.sha256(dump.read_bytes()).hexdigest(),
        'synthetic_gateway_key_sha256': hashlib.sha256((root / 'synthetic-test-gateway.key').read_bytes()).hexdigest(),
        'files': {str(item.relative_to(root / 'evidence')): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted((root / 'evidence').rglob('*')) if item.is_file()},
        'origin': 'dedicated_synthetic_test_fixture_declared'}
    record = root / 'manifest.json'; record.write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
    os.chmod(record, 0o600)
    print('ISOLATED_SYNTHETIC_START_CHECKPOINT_SAVED', flush=True)
