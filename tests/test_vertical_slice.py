from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi.testclient import TestClient
from generic_agents.api import app


def manifest(kind="domain_package", namespace="inspection", name="site", **overrides):
    return {"kind": kind, "namespace": namespace, "name": name, "version": "1.0.0", "owner": "测试操作者",
            "description": "验证包", "specification": {"entity_schemas": {"site": {"type": "object",
             "required": ["name", "temperature"], "additionalProperties": False, "properties": {
             "name": {"type": "string"}, "temperature": {"type": "number"}}}}}, **overrides}


def explicit_goal(obj,fields=None,mode='auto',source='either'):
    """Human-declared fixture contract, not a model fallback or business-value claim."""
    return {'purpose':'核对合成登记资料与待核查观测并保存证据报告','entity_ids':[obj['id']],
        'required_fields':fields or ['temperature'],'observation_mode':mode,
        'evidence':{'source':source,'minimum_verification':'unverified_extraction'},
        'subgoals':[{'id':'verify','description':'核对来源并由人验收','criterion_ids':['evidence','human']}],
        'criteria':[{'id':'evidence','description':'指定字段证据充分','kind':'evidence_sufficient'},
                    {'id':'human','description':'合成协议核对由人验收','kind':'human_confirmation'}]}


def publish(client, value):
    response = client.post("/api/capabilities", json=value)
    assert response.status_code == 201, response.text
    row = response.json()
    response = client.post(f'/api/capabilities/{row["id"]}/validate', json={
        "expected_version": row["version"], "reason": "验证契约"})
    assert response.status_code == 200, response.text
    row = response.json()
    response = client.post(f'/api/capabilities/{row["id"]}/publish', json={
        "expected_version": row["version"], "reason": "批准测试注册"})
    assert response.status_code == 200, response.text
    return response.json()


def entity(client, package, valid_until=None):
    response = client.post("/api/entities", json={"package_id": package["id"], "entity_type": "site",
        "external_id": uuid4().hex, "attributes": {"name": "真实API持久化验证对象", "temperature": 24},
        "valid_until": valid_until})
    assert response.status_code == 201, response.text
    return response.json()


def test_auth_csrf_revocation(client):
    assert client.get("/api/me").status_code == 200
    headers = dict(client.headers); del client.headers["X-CSRF-Token"]
    assert client.post("/api/tokens").status_code == 403
    client.headers.update(headers)
    token = client.post("/api/tokens").json()["token"]
    with TestClient(app) as sdk:
        sdk.headers["Authorization"] = "Bearer " + token
        assert sdk.get("/api/me").status_code == 200
        assert sdk.post("/api/logout").status_code == 200
        assert sdk.get("/api/me").status_code == 401
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_registration_schema_versions_and_persistence(client):
    package = publish(client, manifest())
    invalid = client.post("/api/entities", json={"package_id": package["id"], "entity_type": "site",
        "external_id": "invalid", "attributes": {"name": "缺少温度"}})
    assert invalid.status_code == 422
    obj = entity(client, package)
    response = client.patch(f'/api/entities/{obj["id"]}', json={"expected_version": 1,
        "reason": "核实更新", "attributes": {"name": "已核实", "temperature": 30}})
    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert client.patch(f'/api/entities/{obj["id"]}', json={"expected_version": 1,
        "reason": "旧版本覆盖", "attributes": obj["data"]}).status_code == 409
    history = client.get(f'/api/entities/{obj["id"]}/history').json()
    assert len(history) == 2 and history[0]["data"]["before"] == obj["data"]
    # New session/connection still sees saved server records.
    token = client.post("/api/tokens").json()["token"]
    with TestClient(app) as reopened:
        reopened.headers["Authorization"] = "Bearer " + token
        assert reopened.get("/api/entities").json()[0]["data"]["name"] == "已核实"


def test_query_sufficiency_stale_and_no_fake_cloud(client):
    package = publish(client, manifest())
    obj = entity(client, package, (datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
    body = {"question": "登记对象档案是否充分？", "entity_ids": [obj["id"]], "fields": ["temperature"], "mode": "structured"}
    run = client.post("/api/data/queries", json=body).json()
    assert run["status"] == "insufficient"
    assert run["data"]["result"]["gaps"][0]["route"] == "perception"
    assert run["data"]["result"]["evidence"][0]["version"] == 1
    cloud = client.post("/api/data/queries", json={**body, "mode": "cloud"}).json()
    assert cloud["status"] == "blocked_model" and "result" not in cloud["data"]
    assert len(client.get("/api/data/queries").json()) == 2
    first = client.post("/api/data/diagnose").json()
    second = client.post("/api/data/diagnose").json()
    assert len(first["created_issue_ids"]) == 1 and second["created_issue_ids"] == []


def test_workspace_and_viewer_authorization(client):
    package = publish(client, manifest()); obj = entity(client, package)
    new = client.post("/api/users", json={"username": uuid4().hex, "password": uuid4().hex, "role": "viewer"})
    assert new.status_code == 201
    client.headers["X-Workspace-ID"] = str(uuid4())
    assert client.get("/api/entities").status_code == 403
    del client.headers["X-Workspace-ID"]
    from sqlalchemy.orm import Session
    from generic_agents.db import engine
    from generic_agents.models import User, Workspace, Membership
    from generic_agents.security import password_hash
    username, password = uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password)); workspace = Workspace(name="第二工作区")
        db.add_all([user, workspace]); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="viewer")); db.commit()
    with TestClient(app) as outsider:
        login = outsider.post("/api/login", json={"username": username, "password": password}).json()
        outsider.headers["X-CSRF-Token"] = login["csrf_token"]
        assert outsider.post("/api/capabilities", json=manifest()).status_code == 403
        response = outsider.post("/api/data/queries", json={"question": "越权", "entity_ids": [obj["id"]], "mode": "structured"})
        assert response.status_code == 404


def test_perception_daily_idempotency_and_anomaly(client):
    from generic_agents.worker import process_once, daily_reconcile, deliver_events
    package = publish(client, manifest()); obj = entity(client, package)
    source = publish(client, manifest("source", name="sensor", execution={"adapter": "upload"},
         specification={"timezone": "UTC", "source_type": "camera"}))
    extractor = publish(client, manifest("specialist_model", name="numeric", allowed_agent_roles=["perception"],
        execution={"adapter": "builtin", "handler": "numeric_rules"}, specification={"rules": [
            {"id": "high_temp", "field": "temperature", "operator": "gt", "threshold": 50}]}))
    body = {"source_id": source["id"], "source_record_id": "camera_record_1", "entity_id": obj["id"],
        "observed_at": (datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),
        "business_day": str((datetime.now(timezone.utc)-timedelta(days=1)).date()),
        "attributes": {"temperature": 70}, "extractor_id": extractor["id"], "startup_mode": "manual"}
    response = client.post("/api/observations", json=body)
    assert response.status_code == 201, response.text
    assert client.post("/api/observations", json=body).json()["id"] == response.json()["id"]
    assert client.post("/api/observations", json={**body, "attributes": {"temperature": 71}}).status_code == 409
    assert process_once()
    daily_reconcile(); deliver_events(); daily_reconcile(); deliver_events()
    overview = client.get("/api/overview").json()
    assert overview["observations"][0]["status"] == "processed"
    assert len(overview["tasks"]) == 1
    assert overview["tasks"][0]["data"]["trigger"] == "perception.anomaly"
    assert len(overview["memories"]) == 2
    assert all(event["status"] == "received" for event in overview["events"])
    assert client.get("/api/entities").json()[0]["data"]["temperature"] == 24  # no unapproved overwrite


def test_generic_kernel_two_packages(client):
    first = publish(client, manifest())
    second = publish(client, manifest(namespace="maintenance", name="machine", specification={
        "entity_schemas": {"machine": {"type": "object", "required": ["serial", "hours"],
        "properties": {"serial": {"type": "string"}, "hours": {"type": "integer", "minimum": 0}}}}}))
    a = entity(client, first)
    response = client.post("/api/entities", json={"package_id": second["id"], "entity_type": "machine",
        "external_id": "pump01", "attributes": {"serial": "SN001", "hours": 123}})
    assert response.status_code == 201
    b = response.json()
    result = client.post("/api/data/queries", json={"question": "查询两个业务对象", "entity_ids": [a["id"], b["id"]],
                          "mode": "structured"}).json()
    assert result["status"] == "sufficient" and len(result["data"]["result"]["evidence"]) == 2
