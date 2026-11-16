"""Durable install/lease boundaries; native CPU execution has separate evidence."""
import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import local_changer_install as local
from generic_agents.db import engine
from generic_agents.models import Capability, CloudCall, SpecialistInstallation, SpecialistRun
from generic_agents.worker import process_once


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    target = tmp_path / 'tensor-only.safetensors'
    monkeypatch.setattr(local, 'model_path', lambda: target)
    monkeypatch.setattr(local, 'verify', lambda path: Path(path))
    def prepare(progress, guard):
        guard(); progress('hash_check', source='prepared_local'); target.write_bytes(b'test-only')
        return target
    monkeypatch.setattr(local, 'prepare', prepare)
    monkeypatch.setattr(local, 'probe', lambda path, guard: {'inference_performed': True, 'cloud_calls': 0,
                        'weights_sha256': local.RECIPE['files'][0]['sha256'], 'domain_quality_verified': False})
    return target


def install(client, **extra):
    result = client.post('/api/specialists/installations', json={
        'pack_id': local.PACK, 'source': 'prepared_local', 'backend': 'cpu', **extra})
    assert result.status_code == 201, result.text
    return result.json()


def saved(client, identity):
    return next(r for r in client.get('/api/specialists/installations').json() if r['id'] == identity)


def test_ready_install_is_saved_but_does_not_publish_wrong_single_image_capability(client, prepared):
    row = install(client); assert process_once(row['job_id'], deliver=False)
    result = saved(client, row['id'])
    assert result['status'] == 'ready' and result['deployment'] == 'local'
    assert result['pipeline_hash'] == local.PIPELINE_HASH
    assert result['smoke']['inference_performed'] and not result['smoke']['domain_quality_verified']
    assert result['paired_workflow_registration'] == 'pending'
    assert client.post('/api/specialists/installations/' + row['id'] + '/register', json={'version': result['version']}).status_code == 409
    data = io.BytesIO(); Image.new('RGB', (20, 20)).save(data, format='PNG')
    assert client.post('/api/specialists/installations/' + row['id'] + '/samples', files={'file': ('a.png', data.getvalue(), 'image/png')}).status_code == 422
    with Session(engine()) as db:
        assert not list(db.scalars(select(Capability)))
        assert not list(db.scalars(select(SpecialistRun)))
        assert not list(db.scalars(select(CloudCall)))


def test_bad_source_backend_and_wrong_pack_prepared_source_are_rejected_before_job(client):
    for values in ({'pack_id': local.PACK, 'source': 'official'},
                   {'pack_id': local.PACK, 'source': 'mirror'},
                   {'pack_id': local.PACK, 'source': 'prepared_local', 'backend': 'mps'},
                   {'pack_id': 'rtdetr-v2-r18', 'source': 'prepared_local'}):
        assert client.post('/api/specialists/installations', json=values).status_code == 422
    assert client.get('/api/specialists/installations').json() == []
    assert local.PACK not in [p['pack_id'] for p in client.get('/api/specialists/catalog').json()['packs']]


def test_failed_probe_can_explicitly_retry_with_old_failure_preserved(client, prepared, monkeypatch):
    good = local.probe
    monkeypatch.setattr(local, 'probe', lambda *args: (_ for _ in ()).throw(ValueError('local CPU probe failed')))
    row = install(client); process_once(row['job_id'], deliver=False)
    old = saved(client, row['id']); assert old['status'] == 'failed' and 'probe failed' in old['error']
    monkeypatch.setattr(local, 'probe', good)
    retry = install(client, version=old['version']); assert retry['generation'] == 2
    assert retry['attempts'][0]['status'] == 'failed'
    assert process_once(retry['job_id'], deliver=False)
    assert saved(client, row['id'])['status'] == 'ready'


def test_cancelled_install_does_not_execute_or_reactivate(client, prepared):
    row = install(client)
    assert client.post('/api/specialists/installations/' + row['id'] + '/cancel', json={'version': row['version']}).status_code == 200
    process_once(row['job_id'], deliver=False)
    assert saved(client, row['id'])['status'] == 'cancelled'
    assert not prepared.exists()


def test_changed_pipeline_is_not_accepted_as_ready_for_old_installation(client, prepared):
    row = install(client); assert process_once(row['job_id'], deliver=False)
    with Session(engine()) as db:
        current = db.get(SpecialistInstallation, row['id'])
        current.data = {**current.data, 'pipeline_hash': 'older-pipeline'}; db.commit()
    result = saved(client, row['id'])
    assert client.post('/api/specialists/installations/' + row['id'] + '/register', json={'version': result['version']}).status_code == 409
    assert saved(client, row['id'])['status'] == 'ready'


def test_prepared_paths_reject_symlink_targets(tmp_path):
    target = tmp_path / 'link'; target.symlink_to(tmp_path / 'missing')
    with pytest.raises(ValueError, match='符号链接'):
        local.safe_path(target)
