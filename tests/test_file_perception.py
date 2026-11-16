from .planning_helpers import post_plan
import io
import json
from datetime import datetime, timezone
from uuid import uuid4
import httpx
from PIL import Image
from pydantic import SecretStr
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User, Workspace, Membership, Evidence
from generic_agents.config import settings
from generic_agents.api import app
from generic_agents.security import password_hash
from generic_agents.worker import process_once
from .test_vertical_slice import manifest, publish, entity, explicit_goal


OUTPUT = {"type": "object", "required": ["attributes", "anomalies"], "additionalProperties": False,
    "properties": {"attributes": {"type": "object", "properties": {"temperature": {"type": "number"}},
        "additionalProperties": False}, "anomalies": {"type": "array", "items": {"type": "object",
        "required": ["field", "value", "severity"], "properties": {"field": {"const": "temperature"},
        "value": {"type": "number"}, "severity": {"enum": ["warning", "critical"]}}}}}}


def setup(client):
    package = publish(client, manifest()); obj = entity(client, package)
    source = publish(client, manifest("source", name="camera_files", execution={"adapter": "upload"},
                                      specification={"timezone": "UTC"}))
    model = publish(client, manifest("foundation_model", name="vision", execution={"provider": "qwen",
        "model": "qwen-vl-plus", "supports_images": True, "secret_ref": "DASHSCOPE_API_KEY", "max_tokens": 512},
        data_policy={"cloud_allowed": True}))
    extractor = publish(client, manifest("specialist_model", name="file_extractor",
        allowed_agent_roles=["perception"], execution={"adapter": "cloud_extractor", "model_profile_id": model["id"]},
        dependencies=[model["id"]], data_policy={"cloud_allowed": True}, output_schema=OUTPUT,
        specification={"extraction_instruction": "提取可见温度，超过50度时报告异常，缺失信息不补造"}))
    return obj, source, model, extractor


def png():
    buffer = io.BytesIO(); Image.new("RGB", (80, 40), "white").save(buffer, "PNG")
    return buffer.getvalue()


def observation(client, obj, source, extractor, evidence_id):
    stamp = datetime.now(timezone.utc)
    return client.post("/api/observations", json={"source_id": source["id"], "source_record_id": uuid4().hex,
        "entity_id": obj["id"], "observed_at": stamp.isoformat(), "business_day": str(stamp.date()),
        "extractor_id": extractor["id"], "startup_mode": "manual", "evidence_ids": [evidence_id]})


def test_file_bytes_persist_and_workspace_isolation(client):
    content = png()
    uploaded = client.post("/api/evidence", content=content, headers={"Content-Type": "image/png"})
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.headers['Cache-Control'] == 'private, no-store'
    row = uploaded.json()
    assert row["data"]["width"] == 80 and row["data"]["cloud_model_ids"] == []
    assert "storage_key" not in row
    token = client.post("/api/tokens").json()["token"]
    with TestClient(app) as reopened:
        reopened.headers["Authorization"] = "Bearer " + token
        response = reopened.get(f'/api/evidence/{row["id"]}/content')
        assert response.content == content and response.headers['Cache-Control'] == 'private, no-store'
    username, password = uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password)); workspace = Workspace(name="隔离验证")
        db.add_all([user, workspace]); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin")); db.commit()
    with TestClient(app) as other:
        other.post("/api/login", json={"username": username, "password": password})
        assert other.get(f'/api/evidence/{row["id"]}/content').status_code == 404


def test_bad_format_and_size_are_rejected_without_records(client, monkeypatch):
    assert client.post("/api/evidence", content=b"not a png", headers={"Content-Type": "image/png"}).status_code == 415
    assert client.post("/api/evidence", content=b'<svg></svg>', headers={"Content-Type": "image/svg+xml"}).status_code == 415
    monkeypatch.setattr(settings(), "max_upload_bytes", 20)
    assert client.post("/api/evidence", content=b"x"*21, headers={"Content-Type": "text/plain"}).status_code == 413
    assert client.get("/api/evidence").json() == []


def test_missing_consent_blocks_cloud_and_reports_failure_to_data(client, monkeypatch):
    obj, source, model, extractor = setup(client)
    file = client.post("/api/evidence", content=b"temperature=70", headers={"Content-Type": "text/plain"}).json()
    monkeypatch.setattr("generic_agents.gateway.cloud_json", lambda *a: (_ for _ in ()).throw(AssertionError("未经授权不可出域")))
    assert observation(client, obj, source, extractor, file["id"]).status_code == 201
    assert process_once()
    overview = client.get("/api/overview").json()
    assert overview["observations"][0]["status"] == "failed"
    assert "没有授权" in overview["observations"][0]["data"]["processing_error"]
    assert overview["events"][0]["kind"] == "observation.failed" and overview["events"][0]["status"] == "received"
    assert overview["memories"][0]["data"]["type"] == "observation_failure"
    assert not overview["tasks"]


def test_image_is_actually_sent_and_output_reaches_both_agents(client, monkeypatch):
    obj, source, model, extractor = setup(client); content = png()
    file = client.post('/api/evidence?cloud_model_id='+model["id"], content=content,
                       headers={"Content-Type": "image/png"}).json()
    captured = []; base = httpx.Client
    def handler(request):
        captured.append(json.loads(request.content))
        output = {"attributes": {"temperature": 70}, "anomalies": [{"field": "temperature", "value": 70, "severity": "warning"}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}],
                                          "usage": {"total_tokens": 500}})
    monkeypatch.setattr("generic_agents.gateway.httpx.Client", lambda **kw: base(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings(), "dashscope_api_key", SecretStr("test-only-nonlive-key"))
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    assert observation(client, obj, source, extractor, file["id"]).status_code == 201
    assert process_once()
    parts = captured[0]["messages"][1]["content"]
    assert any(part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/jpeg;base64,") for part in parts)
    assert client.get(f'/api/evidence/{file["id"]}/content').content == content
    overview = client.get('/api/overview').json()
    assert overview["observations"][0]["status"] == "processed"
    assert {event["kind"] for event in overview["events"]} == {"observation.data", "perception.anomaly"}
    assert overview["tasks"][0]["data"]["evidence"]["evidence_ids"] == [file["id"]]
    assert client.get('/api/entities').json()[0]["data"]["temperature"] == 24
    from generic_agents.services import query_evidence
    from generic_agents.security import Principal
    with Session(engine()) as db:
        result = query_evidence(db, Principal(obj['workspace_id'], obj['workspace_id'], 'admin', 'test'), [obj['id']], ['temperature'])
        assert result['evidence'][0]['attributes']['temperature'] == 24
        assert result['observation_evidence'][0]['attributes']['temperature'] == 70
        assert result['observation_evidence'][0]['verification'] == 'unverified_extraction'
        assert result['observation_evidence'][0]['evidence_ids'] == [file['id']]
    assert overview["cloud_calls"][0]["charged_tokens"] == 500


def test_corrupt_file_is_detected_before_any_cloud_call(client):
    file = client.post('/api/evidence', content=png(), headers={"Content-Type": "image/png"}).json()
    with Session(engine()) as db:
        row = db.get(Evidence, file["id"])
        (settings().storage_dir / row.storage_key).write_bytes(b"corruption")
    response = client.get(f'/api/evidence/{file["id"]}/content')
    assert response.status_code == 409 and "校验失败" in response.json()["detail"]


def test_numeric_processor_cannot_silently_ignore_image(client):
    package = publish(client, manifest()); obj = entity(client, package)
    source = publish(client, manifest('source', name='source', execution={'adapter':'upload'}))
    processor = publish(client, manifest('specialist_model', name='numeric', allowed_agent_roles=['perception'],
                    execution={'adapter':'builtin','handler':'numeric_rules'}, specification={'rules':[]}))
    file = client.post('/api/evidence',content=png(),headers={'Content-Type':'image/png'}).json()
    assert observation(client,obj,source,processor,file['id']).status_code == 409


def test_authorize_and_retry_reuses_saved_input_without_duplicate_receivers(client, monkeypatch):
    obj, source, model, extractor = setup(client)
    file = client.post('/api/evidence', content=png(), headers={'Content-Type':'image/png'}).json()
    row = observation(client, obj, source, extractor, file['id']).json()
    assert process_once()
    failed = client.get('/api/overview').json()['observations'][0]
    assert failed['status'] == 'failed'
    permission = {'model_profile_id':model['id'], 'expected_version':file['version'], 'reason':'核实为非敏感合成图'}
    assert client.post('/api/evidence/'+file['id']+'/authorize', json=permission).status_code == 200
    assert client.post('/api/evidence/'+file['id']+'/authorize', json=permission).status_code == 409
    calls = []
    def cloud(*args):
        calls.append(args)
        return {'attributes':{'temperature':70}, 'anomalies':[{'field':'temperature','value':70,'severity':'warning'}]}, {'model':'test-only'}
    monkeypatch.setattr('generic_agents.gateway.cloud_json', cloud)
    retry = {'expected_version':failed['version'], 'reason':'文件授权已修复，使用原记录重试'}
    queued = client.post('/api/observations/'+row['id']+'/retry', json=retry)
    assert queued.status_code == 200
    assert queued.json()['data']['processing_error'] is None
    assert '没有授权' in queued.json()['data']['previous_failure']
    assert client.post('/api/observations/'+row['id']+'/retry', json=retry).status_code == 409
    assert process_once()
    overview = client.get('/api/overview').json()
    saved = overview['observations'][0]
    assert saved['id'] == row['id'] and saved['status'] == 'processed'
    assert saved['data']['evidence_ids'] == [file['id']] and saved['data']['processing_error'] is None
    assert len(calls) == 1 and client.get('/api/evidence/'+file['id']+'/content').content == png()
    assert {event['kind'] for event in overview['events']} == {'observation.failed','observation.data','perception.anomaly'}
    assert all(event['status'] == 'received' for event in overview['events'])
    assert len(overview['tasks']) == 1
    assert not process_once()
    assert len(client.get('/api/overview').json()['tasks']) == 1


def test_acquisition_verification_failure_cannot_erase_observation_or_delay_anomaly(client, monkeypatch):
    obj, source, model, extractor = setup(client)
    file = client.post('/api/evidence?cloud_model_id='+model['id'], content=png(), headers={'Content-Type':'image/png'}).json()
    monkeypatch.setattr('generic_agents.gateway.cloud_json', lambda *args: (
        {'attributes':{'temperature':70}, 'anomalies':[{'field':'temperature','value':70,'severity':'warning'}]}, {}))
    checked = []
    def reject_receipt(*args):
        # Delivery must already be committed before any device verification happens.
        overview = client.get('/api/overview').json()
        assert overview['observations'][0]['status'] == 'processed'
        assert len(overview['tasks']) == 1
        assert all(event['status'] == 'received' for event in overview['events'])
        checked.append(True)
        raise ValueError('receipt mismatch')
    monkeypatch.setattr('generic_agents.perception.receive_acquisition_observation', reject_receipt)
    assert observation(client, obj, source, extractor, file['id']).status_code == 201
    assert process_once()
    overview = client.get('/api/overview').json()
    assert overview['observations'][0]['status'] == 'processed'
    assert checked == [True]
    assert overview['jobs'][0]['status'] == 'succeeded'
    assert len(overview['tasks']) == 1


def test_business_inspection_is_read_only_and_corrupt_evidence_blocks_acceptance(client, monkeypatch):
    from generic_agents.models import Task
    obj, source, model, extractor = setup(client)
    file = client.post('/api/evidence?cloud_model_id='+model['id'], content=png(), headers={'Content-Type':'image/png'}).json()
    monkeypatch.setattr('generic_agents.gateway.cloud_json', lambda *args: ({'attributes':{'temperature':70},'anomalies':[]}, {}))
    observation(client, obj, source, extractor, file['id']); assert process_once()
    monkeypatch.setattr('generic_agents.agents.cloud_json', lambda *args: ({'summary':'只核查已存文件','steps':[
        {'title':'核查原始证据完整性','agent':'perception','action':'inspect_observation'},
        {'title':'返回有来源的报告','agent':'business','action':'report'}]}, {'provider':'test_fixture'}))
    task = client.post('/api/tasks',json={'title':'核查存量文件','package_id':obj['schema_capability_id'],
        'entity_ids':[obj['id']],'fields':['temperature'],'goal_spec':explicit_goal(obj)}).json()
    plan = post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':task['version'], 'reason':'受控测试','model_profile_id':model['id']}).json()
    assert client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'不补采'}).status_code == 200
    assert process_once()
    overview = client.get('/api/overview').json(); saved = next(t for t in overview['tasks'] if t['id']==task['id'])
    assert saved['status'] == 'awaiting_acceptance' and overview['acquisitions'] == []
    inspected = saved['data']['step_receipts'][plan['id']+':0']['result']
    assert inspected['file_integrity'][0]['status'] == 'verified'
    assert inspected['observation_evidence'][0]['attributes']['temperature'] == 70
    with Session(engine()) as db:
        evidence = db.get(Evidence,file['id']); (settings().storage_dir / evidence.storage_key).write_bytes(b'corruption')
    assert client.post('/api/tasks/'+task['id']+'/accept',json={'expected_version':saved['version'],'reason':'尝试验收'}).status_code == 409
    with Session(engine()) as db:
        assert db.get(Task,task['id']).status == 'awaiting_acceptance'
