"""Normal Media state + normal options API; no options/target replacement.

The actual existing media fixture mocks external capture/model IO only. This
proves camera discovery metadata, not qualified plain-flow admission or model
quality. Gate predicates have separate tests; Root owns real-H acceptance.
"""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from generic_agents import media_api, models as m
from generic_agents import media_daily_task_adapter as a
from tests.test_media_daily_task_source_v226 import daily_camera_source, media_runtime, mocked_runtime, checked
from tests.test_instance_workspace import prepared
from tests.test_workflow_abilities import enable


@pytest.fixture
def camera_options(client, daily_camera_source):
    source = daily_camera_source
    release, _, _ = prepared(client); enable(client, release)
    path = '/api/workspace/instances/'+release['instance_id']+'/daily-task-intents/options'
    with Session(source.bound) as db:
        camera = db.get(m.MediaChannel, source.camera_id)
        # This is the genuine state reached by normal capture/processing, not
        # a successful approval inserted to accommodate V226.
        assert camera.status == 'validated'
        frame_pin, _, _ = checked(db, source)
        gateway_id, camera_version = camera.gateway_id, camera.version
    return NS(source=source, path=path, gateway_id=gateway_id, camera_version=camera_version,
        frame_pin=frame_pin, client=client)


def test_actual_validated_source_is_listed_by_the_normal_options_api(camera_options):
    s = camera_options
    response = s.client.get(s.path)
    assert response.status_code == 200, response.text[:300]
    rows = [row for row in response.json()['cameras'] if row['id'] == s.source.camera_id]
    assert len(rows) == 1 and rows[0]['version'] == s.camera_version
    with Session(s.source.bound) as db:
        camera = db.get(m.MediaChannel, s.source.camera_id)
        assert rows[0]['hash'] == a.camera_hash(camera)


def test_normal_failed_gateway_probe_keeps_received_validated_camera_selectable(camera_options, monkeypatch):
    s = camera_options
    def offline(_): raise HTTPException(502, '公开测试网关当前离线')
    monkeypatch.setattr(media_api, 'inventory', offline)
    with Session(s.source.bound) as db: version = db.get(m.MediaGateway, s.gateway_id).version
    failed = s.client.post('/api/media/gateways/'+s.gateway_id+'/discover', json={'version': version})
    assert failed.status_code == 200 and failed.json()['status'] == 'failed'
    response = s.client.get(s.path)
    assert response.status_code == 200
    assert s.source.camera_id in [row['id'] for row in response.json()['cameras']]
    with Session(s.source.bound) as db:
        camera = db.get(m.MediaChannel, s.source.camera_id)
        assert camera.status == 'validated' and camera.version == s.camera_version
        assert checked(db, s.source)[0] == s.frame_pin


@pytest.mark.parametrize('status', ['confirmed', 'draft', 'disabled', 'failed'])
def test_unvalidated_or_disabled_channel_is_not_offered(camera_options, status):
    s = camera_options
    if status in {'confirmed', 'disabled'}:
        action = 'reconfirm' if status == 'confirmed' else 'disable'
        response = s.client.post('/api/media/channels/'+s.source.camera_id+'/'+action,
            json={'version': s.camera_version})
        assert response.status_code == 200, response.text[:300]
        assert response.json()['status'] == status
    else:
        # Explicit negative/corruption states only. Never synthesize validated
        # approval or alter the original capture/Frame/ACK success evidence.
        with Session(s.source.bound) as db:
            camera = db.get(m.MediaChannel, s.source.camera_id); camera.status = status; db.commit()
    response = s.client.get(s.path)
    assert response.status_code == 200
    assert s.source.camera_id not in [row['id'] for row in response.json()['cameras']]
