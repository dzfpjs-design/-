import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import local_specialists as local
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Job, SpecialistInstallation, SpecialistRun, CloudCall, Membership, Capability
from generic_agents.worker import process_once


def output(backend='cpu', threshold=.5, content=None):
    return {'schema': 'local-detection-v1', 'backend': backend, 'threshold': threshold,
            'image_sha256': hashlib.sha256(content).hexdigest() if content is not None else None,
            'width': 20, 'height': 20, 'exif_orientation': 1, 'detections': [], 'cloud_calls': 0,
            'model_load_ms': 10, 'inference_ms': 5}


@pytest.fixture
def mocked_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), 'specialist_dir', tmp_path / 'models')
    monkeypatch.setattr(local, 'verify_files', lambda path: None)
    monkeypatch.setattr(local, 'download_pack', lambda source, progress: local.weights_dir())
    def run(path, backend, threshold=.5, content=None, guard=lambda: None):
        guard()
        return output(backend, threshold, content)
    monkeypatch.setattr(local, 'execute', run)


def install(client, source='official', **extra):
    r = client.post('/api/specialists/installations', json={'pack_id': 'rtdetr-v2-r18', 'source': source, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def ready(client):
    row = install(client)
    assert process_once(row['job_id'], deliver=False)
    result = client.get('/api/specialists/installations').json()[0]
    assert result['status'] == 'ready'
    return result


def sample(client, row):
    data = io.BytesIO(); Image.new('RGB', (20, 20)).save(data, format='PNG')
    r = client.post('/api/specialists/installations/' + row['id'] + '/samples',
                    files={'file': ('图.png', data.getvalue(), 'image/png')})
    assert r.status_code == 201, r.text
    return r.json(), data.getvalue()


def test_install_sample_registration_and_no_cloud(client, mocked_runtime):
    catalog = client.get('/api/specialists/catalog').json()
    assert catalog['default_deployment'] == 'local' and not catalog['cloud_fallback']
    row = ready(client)
    r = client.post('/api/specialists/installations/' + row['id'] + '/register', json={'version': row['version']})
    assert r.status_code == 200, r.text
    with Session(engine()) as db:
        cap = db.get(Capability, r.json()['capability_id'])
        assert cap.status == 'published' and cap.data['data_policy']['cloud_allowed'] is False
        assert cap.data['specification']['automatic_domain_use'] is False
    run, raw = sample(client, row)
    assert process_once(run['job_id'], deliver=False)
    saved = client.get('/api/specialists/runs').json()[0]
    assert saved['status'] == 'succeeded' and saved['result']['image_sha256'] == hashlib.sha256(raw).hexdigest()
    assert client.get('/api/evidence/' + run['evidence_id'] + '/content').content == raw
    download = client.get('/api/specialists/runs/' + run['id'] + '/original')
    assert download.content == raw and download.headers['x-content-sha256'] == run['input_sha256']
    assert 'attachment; filename*=' in download.headers['content-disposition']
    assert client.get('/api/specialists/runs/' + run['id'] + '/original?workspace_id=not-owned').status_code == 403
    with Session(engine()) as db:
        assert not db.scalars(select(CloudCall)).all()


def test_cancelled_install_does_not_reactivate_and_retry_keeps_failure_history(client, mocked_runtime):
    row = install(client)
    r = client.post('/api/specialists/installations/' + row['id'] + '/cancel', json={'version': row['version']})
    assert r.status_code == 200
    assert process_once(row['job_id'], deliver=False)
    saved = client.get('/api/specialists/installations').json()[0]
    assert saved['status'] == 'cancelled'
    retry = install(client, source='mirror', version=saved['version'])
    assert retry['generation'] == 2 and retry['attempts'][0]['status'] == 'cancelled'
    assert process_once(retry['job_id'], deliver=False)
    assert client.get('/api/specialists/installations').json()[0]['status'] == 'ready'


def test_failed_install_explicit_retry_not_silent_mirror(client, mocked_runtime, monkeypatch):
    def fail(source, progress):
        assert source == 'official'
        raise ValueError('公开来源连接失败，下载文件保留')
    monkeypatch.setattr(local, 'download_pack', fail)
    row = install(client); assert process_once(row['job_id'], deliver=False)
    saved = client.get('/api/specialists/installations').json()[0]
    assert saved['status'] == 'failed' and saved['source'] == 'official' and '连接失败' in saved['error']
    assert client.post('/api/specialists/installations', json={'pack_id': 'rtdetr-v2-r18', 'version': 1}).status_code == 409


def test_sample_cancel_and_retry_preserve_original_and_job(client, mocked_runtime):
    row = ready(client); run, raw = sample(client, row)
    r = client.post('/api/specialists/runs/' + run['id'] + '/cancel', json={'version': run['version']})
    assert r.status_code == 200
    process_once(run['job_id'], deliver=False)
    old = client.get('/api/specialists/runs').json()[0]
    r = client.post('/api/specialists/runs/' + old['id'] + '/retry', json={'version': old['version']})
    assert r.status_code == 201
    new = r.json(); assert new['retry_of'] == old['id'] and new['evidence_id'] == old['evidence_id']
    assert process_once(new['job_id'], deliver=False)
    assert {r['status'] for r in client.get('/api/specialists/runs').json()} == {'cancelled', 'succeeded'}


def test_operator_cannot_install_and_workspace_cannot_use_foreign_install(client, account, mocked_runtime):
    row = ready(client)
    with Session(engine()) as db:
        membership = db.scalar(select(Membership).where(Membership.workspace_id == account[2]))
        membership.role = 'operator'; db.commit()
    assert client.post('/api/specialists/installations', json={'pack_id': 'rtdetr-v2-r18'}).status_code == 403
    assert client.post('/api/specialists/installations/' + row['id'] + '/register', json={'version': row['version']}).status_code == 403
    assert client.post('/api/specialists/installations/not-owned/samples', files={'file': ('x.png', b'x', 'image/png')}).status_code == 404


def test_image_integrity_and_model_generation_changes_block_saved_job(client, mocked_runtime):
    row = ready(client); run, raw = sample(client, row)
    with Session(engine()) as db:
        installation = db.get(SpecialistInstallation, row['id'])
        installation.data = {**installation.data, 'backend': 'mps'}; db.commit()
    process_once(run['job_id'], deliver=False)
    saved = client.get('/api/specialists/runs').json()[0]
    assert saved['status'] == 'failed' and '后端变化' in saved['error']


def test_output_rejects_wrong_input_network_claims_nan_and_out_of_bounds():
    result = output(content=b'input'); local.validate_output(result, 'cpu', .5, b'input')
    for change in [{'cloud_calls': 1}, {'image_sha256': 'bad'}, {'detections': [{'score': float('nan'), 'bbox': [0, 0, 10, 10]}]},
                   {'detections': [{'score': .8, 'bbox': [-1, 0, 10, 10]}]}]:
        with pytest.raises(ValueError):
            local.validate_output({**result, **change}, 'cpu', .5, b'input')


def test_corrupt_download_artifact_rejected(tmp_path):
    with pytest.raises(ValueError, match='缺失'):
        local.verify_files(tmp_path)
    (tmp_path / 'config.json').write_bytes(b'x' * 5437)
    with pytest.raises(ValueError, match='摘要'):
        local.verify_files(tmp_path)


def test_local_node_lock_does_not_allow_parallel_resource_overcommit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), 'specialist_dir', tmp_path / 'models')
    with local.node_lock():
        with pytest.raises(ValueError, match='其他任务'):
            with local.node_lock():
                pass


def test_invalid_sample_and_unregistered_source_blocked(client, mocked_runtime):
    assert client.post('/api/specialists/installations', json={'pack_id': 'rtdetr-v2-r18', 'source': 'arbitrary'}).status_code == 422
    row = ready(client)
    assert client.post('/api/specialists/installations/' + row['id'] + '/samples?threshold=nan',
        files={'file': ('x.png', b'x', 'image/png')}).status_code == 422
    assert client.post('/api/specialists/installations/' + row['id'] + '/samples',
        files={'file': ('x.png', b'x', 'image/png')}).status_code == 415


def test_cancellation_while_model_runs_blocks_late_result(client, mocked_runtime, monkeypatch):
    row = ready(client); run, raw = sample(client, row)
    def stop(path, backend, threshold=.5, content=None, guard=lambda: None):
        current = client.get('/api/specialists/runs').json()[0]
        assert client.post('/api/specialists/runs/' + current['id'] + '/cancel',
                           json={'version': current['version']}).status_code == 200
        guard()
        raise AssertionError('Cancelled result must not be accepted')
    monkeypatch.setattr(local, 'execute', stop)
    process_once(run['job_id'], deliver=False)
    saved = client.get('/api/specialists/runs').json()[0]
    assert saved['status'] == 'cancelled' and 'result' not in saved


def test_registered_gateway_invokes_local_pack_and_rejects_widened_policy(client, account, mocked_runtime):
    from generic_agents.models import User
    from generic_agents.security import Principal
    from generic_agents.gateway import invoke
    row = ready(client)
    r = client.post('/api/specialists/installations/' + row['id'] + '/register', json={'version': row['version']})
    assert r.status_code == 200
    cap_id = r.json()['capability_id']; run, raw = sample(client, row)
    with Session(engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'test')
        call = invoke(db, p, cap_id, 'perception', {'evidence_ids': [run['evidence_id']], 'attributes': {}}, 'test-local-call')
        assert call.status == 'succeeded' and call.data['output']['anomalies'] == []
        cap = db.get(Capability, cap_id)
        manifest = {**cap.data, 'name': 'widened', 'data_policy': {'cloud_allowed': True}}
    register_response = client.post('/api/capabilities', json=manifest)
    assert register_response.status_code == 201
    bad = register_response.json()
    assert client.post('/api/capabilities/' + bad['id'] + '/validate',
                       json={'expected_version': bad['version'], 'reason': 'test'}).status_code == 422
