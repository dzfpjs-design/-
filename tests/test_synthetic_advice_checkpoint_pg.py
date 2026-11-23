"""Disposable-PG backup utility check, not an advice lifecycle test."""
import os
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Event, Thread
from time import monotonic, sleep

import pytest
from sqlalchemy import inspect, select, text, update
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Job, Plan, Task, uid
from tests.synthetic_advice_checkpoint import save_if_requested
from tests.synthetic_advice_checkpoint_restore import restore_to_disposable_test_db
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.mark.parametrize('mode', ['save_bad_path', 'save_wrong_db', 'restore_bad_path', 'restore_wrong_db'])
def test_optimized_python_keeps_isolation_guards(mode):
    root = Path(tempfile.mkdtemp(prefix='advice-synthetic-checkpoint-'))
    try:
        dump = root / 'synthetic-start.pgcustom'; dump.write_bytes(b'synthetic-test-dump')
        key = root / 'synthetic-test-gateway.key'; key.write_bytes(b'synthetic-test-key')
        (root / 'evidence').mkdir()
        (root / 'manifest.json').write_text(json.dumps({'schema': 'isolated-synthetic-advice-start-checkpoint-v1',
            'dump_sha256': hashlib.sha256(dump.read_bytes()).hexdigest(),
            'synthetic_gateway_key_sha256': hashlib.sha256(key.read_bytes()).hexdigest(), 'files': {}}))
        script = '''
import os, sys
from types import SimpleNamespace
from tests import synthetic_advice_checkpoint as save
from tests import synthetic_advice_checkpoint_restore as restore
mode, root = sys.argv[1:]
if mode.startswith('save'):
    os.environ['GA_SYNTHETIC_ADVICE_CHECKPOINT'] = '/' if mode.endswith('bad_path') else root
    if mode.endswith('wrong_db'):
        # A valid-looking empty test destination; a non-test DB must fail
        # before any connection or pg_dump, even with Python optimizations.
        import shutil
        shutil.rmtree(root)
        os.mkdir(root, 0o700)
    save.engine = lambda: SimpleNamespace(url=SimpleNamespace(host='127.0.0.1', port=1,
        database='production', username='advice_test', password=None))
    try: save.save_if_requested('synthetic-task', 'synthetic-plan', '/not-used')
    except RuntimeError: print('rejected')
    else: raise SystemExit('unsafe destination was accepted')
else:
    restore.engine = lambda: SimpleNamespace(url=SimpleNamespace(host='127.0.0.1', port=1,
        database='production', username='advice_test', password=None))
    try: restore.restore_to_disposable_test_db('/' if mode.endswith('bad_path') else root)
    except RuntimeError: print('rejected')
    else: raise SystemExit('unsafe destination was accepted')
'''
        completed = subprocess.run([sys.executable, '-O', '-c', script, mode, str(root)],
            capture_output=True, text=True, timeout=30)
        assert completed.returncode == 0 and completed.stdout.strip() == 'rejected', completed.stderr[-500:]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_save_rejects_wrong_real_pg_system_identifier(synthetic_custody, monkeypatch, tmp_path):
    data_dir = Path(os.environ['GA_TEST_PG_DATA_DIR']).resolve()
    if engine().url.username != 'advice_test' or data_dir.name != 'data' or not data_dir.parent.name.startswith('advice-race-pg-'):
        pytest.skip('requires the dedicated synthetic advice checkpoint PostgreSQL harness')
    root = Path(tempfile.mkdtemp(prefix='advice-synthetic-checkpoint-'))
    key_dir = tmp_path / 'gateway-keys'; key_dir.mkdir(); key = key_dir / 'master.key'; key.write_bytes(b'synthetic-test-only')
    try:
        monkeypatch.setenv('GA_SYNTHETIC_ADVICE_CHECKPOINT', str(root))
        monkeypatch.setenv('GA_TEST_PG_SYSTEM_IDENTIFIER', '0')
        monkeypatch.setenv('DEEPSEEK_API_KEY', ''); monkeypatch.setenv('DASHSCOPE_API_KEY', '')
        monkeypatch.setattr(settings(), 'cloud_allowed_providers', '')
        with pytest.raises(RuntimeError):
            save_if_requested(synthetic_custody['task_id'], synthetic_custody['plan_id'], key)
        assert not list(root.iterdir())
    finally:
        shutil.rmtree(root)


def test_private_checkpoint_restores_only_disposable_pg_and_synthetic_files(synthetic_custody, monkeypatch, tmp_path):
    data_dir = Path(os.environ['GA_TEST_PG_DATA_DIR']).resolve()
    if engine().url.username != 'advice_test' or data_dir.name != 'data' or not data_dir.parent.name.startswith('advice-race-pg-'):
        pytest.skip('requires the dedicated synthetic advice checkpoint PostgreSQL harness')
    monkeypatch.setenv('DEEPSEEK_API_KEY', '')
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    monkeypatch.setattr(settings(), 'cloud_allowed_providers', '')
    s = synthetic_custody; root = Path(tempfile.mkdtemp(prefix='advice-synthetic-checkpoint-'))
    monkeypatch.setattr(settings(), 'device_gateway_key_file', settings().device_gateway_key_file)
    key_dir = tmp_path / 'gateway-keys'; key_dir.mkdir(); key = key_dir / 'master.key'; key.write_bytes(b'synthetic-test-only')
    send_id = uid(); entered = Event(); finished = Event(); worker_pid = []; writer_errors = []; writer = None
    try:
        with Session(engine()) as db:
            plan = db.get(Plan, s['plan_id']); data = dict(plan.data)
            data['schema'] = 'same-goal-advice-execution-v302'
            data['advice_execution_v308'] = {'job_ref': {'id': send_id}}; plan.data = data
            db.add(Job(id=send_id, workspace_id=s['principal'].workspace_id,
                kind='generate_same_goal_advice_v302', status='queued', attempts=0,
                idempotency_key='checkpoint-synthetic-' + uid(), data={}))
            db.commit()
        monkeypatch.setenv('GA_SYNTHETIC_ADVICE_CHECKPOINT', str(root))
        actual_run = subprocess.run
        def coordinate_dump(command, **kwargs):
            nonlocal writer
            if Path(command[0]).name == 'pg_dump':
                def change_job():
                    try:
                        with Session(engine()) as db:
                            db.execute(text("SET LOCAL lock_timeout = '10s'"))
                            worker_pid.append(db.scalar(text('SELECT pg_backend_pid()'))); entered.set()
                            db.execute(update(Job).where(Job.id == send_id).values(status='failed'))
                            db.commit()
                    except BaseException as exc:
                        writer_errors.append(type(exc).__name__)
                    finally: finished.set()
                writer = Thread(target=change_job, daemon=True); writer.start()
                assert entered.wait(3)
                deadline = monotonic() + 5; blockers = []
                while monotonic() < deadline:
                    with Session(engine()) as observer:
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': worker_pid[0]})
                    if blockers: break
                    assert not finished.is_set(), 'concurrent database writer escaped the backup lock'
                    sleep(.02)
                assert blockers and not finished.is_set()
            return actual_run(command, **kwargs)
        monkeypatch.setattr(subprocess, 'run', coordinate_dump)
        save_if_requested(s['task_id'], s['plan_id'], key)
        assert writer is not None and finished.wait(10)
        writer.join(timeout=1)
        assert not writer_errors
        assert (root / 'manifest.json').is_file()
        # Even a correctly named test database is not an empty restore target
        # while its original records are present; no pg_restore may run yet.
        with pytest.raises(RuntimeError):
            restore_to_disposable_test_db(str(root))
        with Session(engine()) as db:
            assert db.get(Task, s['task_id']) is not None
        # Reset only conftest's own randomized temporary database and storage.
        assert engine().url.database.startswith('ga_test_')
        with engine().begin() as conn:
            tables = ', '.join(conn.dialect.identifier_preparer.quote_identifier(name)
                for name in inspect(conn).get_table_names())
            if tables: conn.execute(text('TRUNCATE ' + tables + ' RESTART IDENTITY CASCADE'))
        target = Path(settings().storage_dir)
        for item in target.iterdir():
            if item.is_dir(): shutil.rmtree(item)
            else: item.unlink()
        restore_to_disposable_test_db(str(root))
        with Session(engine()) as db:
            assert db.get(Task, s['task_id']) is not None
            assert db.get(Job, send_id).status == 'queued'
            assert db.get(Plan, s['plan_id']).data['advice_execution_v308']['job_ref']['id'] == send_id
    finally:
        if writer is not None:
            writer.join(timeout=10)
        shutil.rmtree(root)
