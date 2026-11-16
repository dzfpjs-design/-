"""Verify actual local inference and registry receipts, then restart setup services.

Only the isolated validation workspace is used. Never sends data to cloud models.
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
from generic_agents.models import CloudCall, User, Capability, Evidence, SpecialistRun, Invocation
from generic_agents.gateway import invoke
from generic_agents.security import Principal
from generic_agents.local_specialists import RECIPE, RECIPE_HASH, verify_files, weights_dir

ROOT = Path(__file__).resolve().parents[1]; os.chdir(ROOT)
access = json.loads((ROOT / '.runtime/understanding-validation-access.json').read_text())
baseline = json.loads((ROOT / '.runtime/setup-recovery-baseline.json').read_text())
OUT = ROOT / 'docs/evidence/local-specialist-v06'; OUT.mkdir(parents=True, exist_ok=True)


@contextmanager
def connection():
    client = httpx.Client(base_url='http://127.0.0.1:8765', trust_env=False, timeout=30)
    try:
        response = client.post('/api/login', json={k: access[k] for k in ['username', 'password']})
        response.raise_for_status()
        client.headers.update({'X-CSRF-Token': response.json()['csrf_token'], 'X-Workspace-ID': access['workspace_id']})
        yield client
    finally:
        client.close()


def get(client, path):
    response = client.get(path); response.raise_for_status(); return response.json()


def snapshot(client):
    value = {'draft': get(client, '/api/setup/draft'), 'installations': get(client, '/api/specialists/installations'),
             'runs': get(client, '/api/specialists/runs'), 'capabilities': get(client, '/api/capabilities')}
    value['originals'] = []
    for run in value['runs']:
        response = client.get('/api/specialists/runs/' + run['id'] + '/original',
                              params={'workspace_id': access['workspace_id']}); response.raise_for_status()
        sha = hashlib.sha256(response.content).hexdigest(); assert sha == run['input_sha256']
        assert response.headers['x-content-sha256'] == sha
        assert 'attachment; filename*=' in response.headers['content-disposition']
        value['originals'].append({'sha256': sha, 'bytes': len(response.content)})
    return value


verify_files(weights_dir())
with connection() as client:
    before = snapshot(client)
    installation = before['installations'][0]
    assert installation['status'] == 'ready' and installation['source'] == 'mirror'
    assert installation['recipe_hash'] == RECIPE_HASH and installation['domain_evaluation'] == 'pending'
    assert installation['attempts'][0]['status'] == 'failed' and installation['attempts'][0]['source'] == 'official'
    run = next(row for row in before['runs'] if row['status'] == 'succeeded')
    result = run['result']; assert digest(result) == run['result_hash']
    assert result['cloud_calls'] == 0 and result['backend'] == 'cpu'
    assert sum(d['label'] == 'cat' for d in result['detections']) == 2
    assert sum(d['label'] == 'remote' for d in result['detections']) == 2
    bound = next(r for r in before['draft']['configuration']['resources']
                 if r.get('details', {}).get('capability_id') == installation['capability_id'])
    assert bound['status'] == 'candidate' and bound['checks']['domain_evaluation'] is False
    flow = before['draft']['configuration']['flows']['设备异常复核']
    assert flow['confirmedSignature'] and flow['steps'][2]['role'] == '业务负责人'

with Session(engine(), expire_on_commit=False) as db:
    ids = sorted(db.scalars(select(CloudCall.id).where(CloudCall.workspace_id == access['workspace_id'])).all())
    assert ids == sorted(baseline['cloud_ids'])
    user = db.scalar(select(User).where(User.username == access['username']))
    cap = db.get(Capability, installation['capability_id'])
    assert cap.status == 'published' and cap.data['data_policy']['cloud_allowed'] is False
    assert cap.data['specification']['automatic_domain_use'] is False
    assert db.get(Evidence, run['evidence_id']).data['cloud_model_ids'] == []
    p = Principal(user.id, access['workspace_id'], 'admin', 'local-runtime-validation')
    # Idempotent receipt key; repeated verifier runs reuse the existing successful result.
    call = invoke(db, p, cap.id, 'perception', {'evidence_ids': [run['evidence_id']], 'attributes': {}},
                  'local-runtime:' + run['id'])
    assert call.status == 'succeeded', call.data.get('error')
    assert call.data['output']['attributes']['detected_candidate_counts']['cat'] == 2
    assert call.data['output']['anomalies'] == []
    invocation_id = call.id; invocation_before = {'status': call.status, 'data': call.data}

runtime.stop('setup-worker', 'setup-worker'); runtime.stop('api', 'serve')
runtime.start('api', 'serve'); runtime.start('setup-worker', 'setup-worker')
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
    assert before == snapshot(client)
with Session(engine()) as db:
    call = db.get(Invocation, invocation_id)
    assert {'status': call.status, 'data': call.data} == invocation_before
    assert sorted(db.scalars(select(CloudCall.id).where(CloudCall.workspace_id == access['workspace_id'])).all()) == ids

mps = json.loads((ROOT / '.runtime/rtdetr-cats-mps.json').read_text())
assert mps['backend'] == 'mps' and sum(d['label'] == 'cat' for d in mps['detections']) == 2
downloaded = Path.home() / 'Downloads/coco-cats.jpg'
assert downloaded.is_file() and hashlib.sha256(downloaded.read_bytes()).hexdigest() == run['input_sha256']
report = {'date': '2026-10-08', 'recipe': RECIPE, 'recipe_sha256': RECIPE_HASH,
          'actual_browser_install_register_upload': True, 'official_download_failed_explicit_mirror_retry': True,
          'original_sha256': run['input_sha256'], 'image_pixels': [result['width'], result['height']],
          'result': result, 'registered_capability_actual_inference': invocation_before,
          'actual_mps_sample': mps, 'normal_setup_service_restart_exact': True,
          'snapshot_sha256': digest(before), 'new_cloud_calls': 0,
          'browser_original_attachment_download_verified': True, 'browser_download_sha256': run['input_sha256'],
          'sample_scope': 'one public COCO sample; two cats and two remotes; no domain benchmark',
          'domain_evaluation': 'pending', 'real_stream_tracking': False, 'postgres_restart': False,
          'crash_injection': False, 'install_upgrade_rollback': False, 'full_PACK01_VID01_complete': False,
          'production_ready': False}
(OUT / 'runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'local_installation': True, 'registered_call': call.status, 'normal_restart_exact': True,
                  'new_cloud_calls': 0, 'domain_quality_verified': False}, ensure_ascii=False))
