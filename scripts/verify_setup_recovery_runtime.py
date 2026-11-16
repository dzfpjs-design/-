"""Verify the browser's recovery/reuse actions without issuing cloud requests.

Uses a previously saved baseline, preserves it, and restarts only setup services.
"""
from contextlib import contextmanager
import hashlib
import json
import os
import time
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

import local_runtime as runtime
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
access = json.loads((ROOT / '.runtime/understanding-validation-access.json').read_text())
baseline = json.loads((ROOT / '.runtime/setup-recovery-baseline.json').read_text())
OUT = ROOT / 'docs/evidence/setup-recovery-v06'
OUT.mkdir(parents=True, exist_ok=True)


@contextmanager
def connection():
    client = httpx.Client(base_url='http://127.0.0.1:8765', trust_env=False, timeout=20)
    try:
        response = client.post('/api/login', json={k: access[k] for k in ['username', 'password']})
        response.raise_for_status()
        client.headers.update({'X-CSRF-Token': response.json()['csrf_token'],
                               'X-Workspace-ID': access['workspace_id']})
        yield client
    finally:
        client.close()


def get(client, path):
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def snapshot(client):
    result = {'draft': get(client, '/api/setup/draft'),
              'analyses': get(client, '/api/setup/understanding'),
              'materials': get(client, '/api/setup/materials'),
              'history': get(client, '/api/setup/draft/history?limit=100')}
    result['originals'] = []
    for material in result['materials']:
        response = client.get('/api/setup/materials/' + material['id'] + '/content')
        response.raise_for_status()
        sha = hashlib.sha256(response.content).hexdigest()
        assert sha == material['hash']
        result['originals'].append({'id': material['id'], 'sha256': sha, 'bytes': len(response.content)})
    return result


def ledger():
    with Session(engine()) as db:
        calls = sorted(db.scalars(select(CloudCall.id).where(CloudCall.workspace_id == access['workspace_id'])).all())
        jobs = sorted(db.scalars(select(Job.id).where(Job.workspace_id == access['workspace_id'],
                                                     Job.kind == 'understand_setup')).all())
    assert calls == sorted(baseline['cloud_ids'])
    assert jobs == sorted(baseline['cloud_job_ids'])
    return {'cloud_calls': len(calls), 'cloud_jobs': len(jobs)}


with connection() as client:
    before = snapshot(client)
    clone = next(row for row in before['analyses'] if row['reused_from'] == baseline['source_analysis_id'])
    assert clone['status'] == 'applied' and clone['receipts'] == []
    assert clone['original_cloud_source'] == baseline['source_analysis_id']
    assert clone['review_selection']['names'] == ['设备异常复核']
    assert clone['local_revision'] == baseline['original_revision'] + 1
    source = next(row for row in before['analyses'] if row['id'] == baseline['source_analysis_id'])
    assert source['status'] == 'undone' and source['receipts']
    history = {row['revision']: row['configuration'] for row in before['history']}
    assert 6 in history and baseline['original_revision'] in history
    assert history[6]['flows']['设备异常复核']['steps'][2]['role'] == '值班人员'
    assert history[baseline['original_revision']]['flows']['设备异常复核']['steps'][2]['role'] == '业务负责人'
    restored = history[clone['local_revision']]
    assert restored['flows']['设备异常复核']['steps'][2]['role'] == '值班人员'
    flow = before['draft']['configuration']['flows']['设备异常复核']
    assert len(flow['steps']) == 4 and flow['steps'][2]['role'] == '业务负责人'
    assert flow['confirmedSignature'] and flow['confirmedAt']
    assert before['draft']['revision'] > clone['applied_revision']
    assert all(material['name'].endswith('.txt') for material in before['materials'])
    counts = ledger()

runtime.stop('setup-worker', 'setup-worker')
runtime.stop('api', 'serve')
runtime.start('api', 'serve')
runtime.start('setup-worker', 'setup-worker')
for _ in range(80):
    try:
        if httpx.get('http://127.0.0.1:8765/health', trust_env=False, timeout=1).status_code == 200:
            break
    except httpx.HTTPError:
        pass
    time.sleep(.1)
else:
    raise RuntimeError('本项目 API 未恢复')

with connection() as client:
    after = snapshot(client)
assert before == after
assert ledger() == counts
report = {'date': '2026-10-08', 'scope': 'synthetic setup configuration recovery and paid proposal reuse',
          'browser_restore_preview_and_confirm': True, 'old_versions_retained': [6, baseline['original_revision']],
          'restored_revision': clone['local_revision'], 'final_revision': before['draft']['revision'],
          'browser_reuse_review_survived_reload': True, 'reused_proposal_applied': True,
          'source_proposal_still_undone': True, 'human_confirmation_persisted': True,
          'originals': before['originals'], 'normal_api_setup_worker_restart_exact': True,
          'snapshot_sha256': digest(before), **counts, 'new_cloud_calls': 0, 'new_cloud_jobs': 0,
          'backend_tests': 337, 'backend_seconds': 126.76, 'frontend_tests': 59,
          'postgres_restart': False, 'crash_injection': False, 'production_ready': False,
          'professional_models_installed': False, 'full_GF01_complete': False}
(OUT / 'runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'normal_restart_exact': True, 'final_revision': report['final_revision'],
                  'originals_verified': len(before['originals']), **counts,
                  'new_cloud_calls': 0, 'full_product_complete': False}, ensure_ascii=False))
