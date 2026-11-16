from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.models import Acquisition, SourceReservation
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.perception import dispatch_acquisition
from .test_vertical_slice import publish, manifest, entity


def policy(client, obj, source):
    value = {"title": "温度不足自动补采", "entity_ids": [obj["id"]], "source_ids": [source["id"]],
         "fields": ["temperature"], "max_age_seconds": 3600, "cooldown_seconds": 3600,
         "max_acquisitions_per_day": 2, "max_duration_seconds": 100,
         "valid_until": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}
    response = client.post("/api/monitor-policies", json=value)
    assert response.status_code == 201, response.text
    row = response.json()
    response = client.post(f'/api/monitor-policies/{row["id"]}/approve', json={"expected_version": 1, "reason": "批准限定范围和次数"})
    assert response.status_code == 200, response.text
    return response.json()


def test_missing_perception_creates_bounded_acquisition_without_fake_control(client):
    package = publish(client, manifest()); obj = entity(client, package)
    source = publish(client, manifest("source", name="camera", execution={"adapter": "upload"}, specification={"timezone": "UTC"}))
    response = client.get(f'/api/entities/{obj["id"]}/perception-state')
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient"
    monitor = policy(client, obj, source)
    first = client.post(f'/api/monitor-policies/{monitor["id"]}/scan').json()
    assert len(first["acquisition_ids"]) == 1
    acq = client.get("/api/overview").json()["acquisitions"][0]
    assert acq["status"] == "blocked_connector"
    assert client.post(f'/api/monitor-policies/{monitor["id"]}/scan').json()["acquisition_ids"] == []
    assert client.post(f'/api/monitor-policies/{monitor["id"]}/revoke', json={"expected_version": monitor["version"],"reason":"撤销监测"}).status_code == 200
    assert client.post(f'/api/monitor-policies/{monitor["id"]}/scan').status_code == 409


def test_unknown_dispatch_cannot_replay(client, monkeypatch):
    package = publish(client, manifest()); obj = entity(client, package)
    source = publish(client, manifest("source", name="camera", execution={"adapter":"upload"}, specification={"timezone":"UTC"}))
    monitor = policy(client, obj, source)
    client.post(f'/api/monitor-policies/{monitor["id"]}/scan')
    overview = client.get("/api/overview").json(); acq_id = overview["acquisitions"][0]["id"]
    with Session(engine()) as db:
        acq = db.get(Acquisition, acq_id); acq.status = "unknown"; db.commit()
        p = Principal(monitor["approved_by"], monitor["workspace_id"], "admin", "test")
        monkeypatch.setattr("generic_agents.perception.validate_endpoint", lambda *a: (_ for _ in ()).throw(AssertionError("未知下发不可再次调用")))
        dispatch_acquisition(db, p, acq_id)
        assert db.get(Acquisition, acq_id).status == "unknown"


def test_monitor_hash_cannot_be_modified_after_approval(client):
    from generic_agents.models import MonitorPolicy
    package = publish(client, manifest()); obj = entity(client, package)
    source = publish(client, manifest("source", name="camera", execution={"adapter":"upload"}, specification={"timezone":"UTC"}))
    monitor = policy(client,obj,source)
    with Session(engine()) as db:
        row = db.get(MonitorPolicy, monitor["id"]); row.data = {**row.data,"max_acquisitions_per_day":1000};db.commit()
    assert client.post(f'/api/monitor-policies/{monitor["id"]}/scan').status_code == 409
