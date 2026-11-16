"""Actual worker/local-copy/CPU installation and restart durability, own QA only."""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.geo_codec import file_digest
from generic_agents import models
from generic_agents.security import password_hash

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/changer-install-v06'
ACCESS = ROOT / '.runtime/changer-install-access.json'
CLASSES = [getattr(models, name) for name in ('SpecialistInstallation', 'SpecialistRun', 'GeoScene', 'GeoRun',
           'GeoPair', 'GeoDaily', 'Evidence', 'Capability', 'Task', 'Memory', 'Entity', 'Observation')]


def save(name, value):
    path = OUT / (name + '.json'); path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def existing(workspace_id):
    values, originals = {}, {}
    with Session(engine()) as db:
        for cls in CLASSES:
            for row in db.scalars(select(cls).where(cls.workspace_id != workspace_id)):
                value = {col.name: getattr(row, col.name).isoformat() if isinstance(getattr(row, col.name), datetime)
                         else getattr(row, col.name) for col in cls.__table__.columns}
                values[cls.__name__ + ':' + row.id] = digest(value)
                if cls is models.Evidence:
                    path = settings().storage_dir / row.storage_key
                    actual = file_digest(path)
                    if actual != row.sha256:
                        raise ValueError('existing original evidence failed SHA verification; no repair attempted')
                    originals[row.id] = actual
        cloud = sorted(db.scalars(select(models.CloudCall.id)).all())
    return {'records': values, 'originals': originals, 'cloud_ids': cloud}


def main(phase):
    OUT.mkdir(exist_ok=True)
    if phase == 'baseline':
        if ACCESS.exists():
            raise ValueError('preserve existing QA access and baseline; do not recreate')
        access = {'username': 'changer_install_' + uuid4().hex[:8], 'password': uuid4().hex}
        with Session(engine()) as db:
            user = models.User(username=access['username'], password_hash=password_hash(access['password']))
            workspace = models.Workspace(name='本地变化模型安装工程核查')
            db.add_all([user, workspace]); db.flush()
            db.add(models.Membership(user_id=user.id, workspace_id=workspace.id, role='admin')); db.commit()
            access['workspace_id'] = workspace.id
        with os.fdopen(os.open(ACCESS, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as target:
            json.dump(access, target)
        baseline = existing(access['workspace_id']); save('baseline', baseline)
        print(json.dumps({'baseline_saved': True, 'old_records': len(baseline['records']), 'old_originals': len(baseline['originals'])}))
        return
    access = json.loads(ACCESS.read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api', trust_env=False, timeout=30) as client:
        response = client.post('/login', json={key: access[key] for key in ('username', 'password')}); response.raise_for_status()
        client.headers.update({'X-CSRF-Token': response.json()['csrf_token'], 'X-Workspace-ID': access['workspace_id']})
        try:
            if phase == 'install':
                response = client.post('/specialists/installations', json={'pack_id': 'changer-r18-binary-v1', 'source': 'prepared_local', 'backend': 'cpu'})
                response.raise_for_status(); row = response.json(); save('requested', row)
                print(json.dumps({'queued': True, 'installation_id': row['id'], 'job_id': row['job_id']}))
                return
            identity = json.loads((OUT / 'requested.json').read_text())['id']
            deadline = time.monotonic() + 50
            while True:
                response = client.get('/specialists/installations'); response.raise_for_status()
                row = next(r for r in response.json() if r['id'] == identity)
                if row['status'] in {'ready', 'failed', 'cancelled'}:
                    break
                if time.monotonic() >= deadline:
                    save('latest-progress', {'id': identity, 'status': row['status'], 'stage': row.get('stage')})
                    raise TimeoutError('live installation not terminal; retain request and poll same job')
                time.sleep(.5)
            if row['status'] != 'ready' or not row['smoke']['inference_performed'] or row['smoke']['cloud_calls'] != 0:
                save('terminal-failure', row); raise ValueError('actual local install did not reach ready')
            from generic_agents.local_changer_install import installed, model_path, trace
            from generic_agents.security import Principal
            with Session(engine()) as db:
                user = db.scalar(select(models.User).where(models.User.username == access['username']))
                own = installed(db, Principal(user.id, access['workspace_id'], 'admin', ''), identity)
                installed_trace = trace(own)
                job = db.get(models.Job, row['job_id'])
                if job.status != 'succeeded':
                    raise ValueError('installation row lacks a succeeded durable job')
            snapshot = {'installation': row, 'trace': installed_trace, 'installed_weights_sha256': file_digest(model_path()),
                        'prepared_weights_sha256': file_digest(ROOT / '.runtime/changer-candidate/changer-r18.safetensors'),
                        'existing': existing(access['workspace_id'])}
            baseline = json.loads((OUT / 'baseline.json').read_text())
            if snapshot['existing'] != baseline:
                save('unexpected-existing-change', snapshot['existing']); raise ValueError('existing records, original SHA or cloud call set changed')
            if snapshot['installed_weights_sha256'] != snapshot['prepared_weights_sha256']:
                raise ValueError('installed model differs from fixed prepared artifact')
            if phase == 'check':
                save('before-restart', snapshot)
            else:
                before = json.loads((OUT / 'before-restart.json').read_text())
                if snapshot != before:
                    save('unexpected-restart-change', snapshot); raise ValueError('saved installation changed after normal restart')
                save('after-restart', snapshot)
                save('verification', {'passed': True, 'real_cpu_probe': True, 'persisted_installation_after_restart': True,
                    'installed_tensor_only_weights_sha256': snapshot['installed_weights_sha256'],
                    'old_records_unchanged': len(baseline['records']), 'old_originals_unchanged': len(baseline['originals']),
                    'new_cloud_calls': 0, 'business_registration_enabled': False, 'browser_flow_verified': False,
                    'domain_quality_verified': False})
            print(json.dumps({'passed': True, 'phase': phase, 'real_cpu_probe': True,
                              'old_records_unchanged': len(baseline['records']), 'new_cloud_calls': 0}))
        finally:
            result = client.post('/logout')
            if result.status_code not in (200, 204):
                raise ValueError('own QA session logout failed; no bulk session cleanup attempted')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('phase', choices=['baseline', 'install', 'check', 'restart-check'])
    main(parser.parse_args().phase)
