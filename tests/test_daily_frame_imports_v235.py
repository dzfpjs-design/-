"""Fresh interpreter import orders; no app warm-up, database, worker or IO.

Run with --noconftest for the independent import slice. The child working
directory is empty, application/PG/key environment variables are not inherited,
and both socket and SQLAlchemy connection paths fail if import tries IO.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = r'''
import importlib
import json
import socket
import sys
from sqlalchemy.engine import Engine

attempted = []
def forbidden(*args, **kwargs):
    attempted.append(True)
    raise AssertionError('cold import attempted external IO or a DB connection')

socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
Engine.connect = forbidden
Engine.raw_connection = forbidden

order = json.loads(sys.argv[1])
for module in order:
    importlib.import_module(module)
assessment = importlib.import_module('generic_agents.evidence_assessment')
api = importlib.import_module('generic_agents.daily_frame_assessment_api_v233')
assert callable(assessment.collect) and callable(assessment.process) and callable(assessment.checked)
assert callable(api.checked_association) and callable(api.current_freshness)
paths = {route.path for route in api.router.routes}
assert '/api/workspace/tasks/{task_id}/daily-frame' in paths
assert '/api/workspace/tasks/{task_id}/daily-frame/assessments' in paths
assert not attempted
print(json.dumps({'ok': True, 'order': order, 'attempted_connections': len(attempted)}))
'''


@pytest.mark.parametrize('order', [
    ['generic_agents.evidence_assessment', 'generic_agents.worker', 'generic_agents.daily_frame_assessment_api_v233'],
    ['generic_agents.daily_frame_assessment_api_v233', 'generic_agents.evidence_assessment', 'generic_agents.worker'],
], ids=['assessment-worker-first', 'new-api-first'])
def test_fresh_import_order_without_app_warmup_or_external_io(tmp_path, order):
    source = Path(__file__).resolve().parents[1]/'src'
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'LANG', 'LC_ALL') if key in os.environ}
    env.update(PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE='1',
        GA_DATABASE_URL='postgresql+psycopg2://invalid/never_connect',
        GA_STORAGE_DIR=str(tmp_path/'unused-storage'))
    result = subprocess.run([sys.executable, '-c', SCRIPT, json.dumps(order)], cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, (result.stdout[-1000:], result.stderr[-2200:])
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines
    report = json.loads(lines[-1])
    assert report == {'ok': True, 'order': order, 'attempted_connections': 0}
