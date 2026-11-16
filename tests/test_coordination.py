from .planning_helpers import post_plan
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Plan, Task, Entity, Issue, Job, Invocation
from generic_agents.worker import process_once
from .test_vertical_slice import manifest, publish, entity, explicit_goal


def planned_task(client, obj, package, monkeypatch):
    """Controlled test model response; never a production fallback or cloud-quality proof."""
    model = publish(client, manifest("foundation_model", name="qwen", execution={
        "provider": "qwen", "model": "qwen-plus", "secret_ref": "DASHSCOPE_API_KEY"},
        data_policy={"cloud_allowed": True}))
    monkeypatch.setattr("generic_agents.agents.cloud_json", lambda *args: ({"summary": "测试用受控计划", "steps": [
        {"title": "查询证据", "agent": "data", "action": "query_evidence"},
        {"title": "形成证据报告", "agent": "business", "action": "report"}]}, {"provider": "test_fixture"}))
    task = client.post("/api/tasks", json={"title": "核实对象状态", "package_id": package["id"],
                        "entity_ids": [obj["id"]], "fields": ["temperature"], 'goal_spec':explicit_goal(obj)}).json()
    response = post_plan(client,f'/api/tasks/{task["id"]}/plan', json={"expected_version": 1,
        "reason": "测试规划", "model_profile_id": model["id"]})
    assert response.status_code == 200, response.text
    return task, response.json()


def test_business_execution_feedback_trusted_case(client, monkeypatch):
    package = publish(client, manifest()); obj = entity(client, package)
    task, plan = planned_task(client, obj, package, monkeypatch)
    response = client.post(f'/api/plans/{plan["id"]}/approve', json={"expected_version": plan['version'],"reason": "证据和步骤已核实"})
    assert response.status_code == 200, response.text
    assert client.post(f'/api/plans/{plan["id"]}/approve', json={"expected_version": plan['version'],"reason": "重复批准"}).status_code == 409
    assert process_once()
    overview = client.get("/api/overview").json()
    task = next(t for t in overview["tasks"] if t["id"] == task["id"])
    assert task["status"] == "awaiting_acceptance"
    assert len(task["data"]["step_receipts"]) == 2
    response = client.post(f'/api/tasks/{task["id"]}/accept', json={"expected_version": task["version"],
                                                                 "reason": "经人工核查，证据报告符合需求"})
    assert response.status_code == 200, response.text
    memory = client.get("/api/overview").json()["memories"][0]
    assert memory["status"] == "candidate"
    response = client.post(f'/api/memories/{memory["id"]}/review', json={"expected_version": 1,
                               "reason": "确认案例可以复用", "decision": "trust"})
    assert response.status_code == 200, response.text
    run = client.post("/api/data/queries", json={"question": "复用已核实经验", "entity_ids": [obj["id"]], "mode": "structured"}).json()
    assert run["data"]["result"]["trusted_cases"][0]["id"] == memory["id"]


def test_approved_plan_tamper_is_blocked(client, monkeypatch):
    package = publish(client, manifest()); obj = entity(client, package)
    task, plan = planned_task(client, obj, package, monkeypatch)
    client.post(f'/api/plans/{plan["id"]}/approve', json={"expected_version": plan['version'], "reason": "批准测试"})
    with Session(engine()) as db:
        row = db.get(Plan, plan["id"]); row.data = {**row.data, "injected": "修改已批准内容"}; db.commit()
    process_once()
    overview = client.get("/api/overview").json()
    assert overview["jobs"][0]["status"] == "failed"
    assert not overview["tasks"][0]["data"].get("step_receipts")


def test_new_observation_after_report_requires_reverification_before_acceptance(client, monkeypatch):
    from generic_agents.models import Observation
    package = publish(client, manifest()); obj = entity(client, package)
    task, plan = planned_task(client, obj, package, monkeypatch)
    client.post('/api/plans/'+plan['id']+'/approve', json={'expected_version':plan['version'],'reason':'核对已有证据'})
    assert process_once()
    saved = client.get('/api/overview').json()['tasks'][0]
    assert saved['status'] == 'awaiting_acceptance'
    source = publish(client, manifest('source', name='new_camera', execution={'adapter':'upload'}))
    with Session(engine()) as db:
        db.add(Observation(workspace_id=obj['workspace_id'], source_id=source['id'], source_record_id='new-evidence', processing_version='1',
            entity_id=obj['id'], observed_at=datetime.now(timezone.utc), business_day='2026-10-05', status='processed',
            data={'attributes':{},'extracted':{'attributes':{'temperature':70},'anomalies':[]}}))
        db.commit()
    response = client.post('/api/tasks/'+saved['id']+'/accept', json={'expected_version':saved['version'], 'reason':'尝试验收旧报告'})
    assert response.status_code == 409
    assert not client.get('/api/overview').json()['memories']


def test_missing_observations_cannot_complete_an_inspection_plan(client, monkeypatch):
    package = publish(client, manifest()); obj = entity(client, package)
    task, plan = planned_task(client, obj, package, monkeypatch)
    with Session(engine()) as db:
        row = db.get(Plan, plan['id'])
        row.data = {**row.data, 'result':{**row.data['result'], 'plan':{'summary':'核查既有观测', 'steps':[
            {'title':'读取既有观测','agent':'perception','action':'inspect_observation'},
            {'title':'返回报告','agent':'business','action':'report'}]}}}
        db.commit()
    assert client.post('/api/plans/'+plan['id']+'/approve',json={'expected_version':plan['version'],'reason':'核查计划'}).status_code == 200
    assert process_once()
    assert client.get('/api/overview').json()['tasks'][0]['status'] == 'waiting_evidence'


def test_model_titles_cannot_claim_unimplemented_checks_or_duplicate_scoped_calls(client, monkeypatch):
    package = publish(client, manifest()); obj = entity(client, package)
    task, plan = planned_task(client,obj,package,monkeypatch)
    from generic_agents.models import Observation
    source=publish(client,manifest('source',name='existing_record',execution={'adapter':'upload'}))
    with Session(engine()) as db:
        stamp=datetime.now(timezone.utc)
        db.add(Observation(workspace_id=obj['workspace_id'],source_id=source['id'],source_record_id='stored',processing_version='1',
            entity_id=obj['id'],observed_at=stamp,business_day=str(stamp.date()),status='processed',
            data={'extracted':{'attributes':{'temperature':24},'anomalies':[]}}));db.commit()
    proposed = {'summary':'测试重复计划', 'steps':[
        {'title':'证明文件内容真实','agent':'perception','action':'inspect_observation'},
        {'title':'证明设备可靠','agent':'perception','action':'inspect_observation'},
        {'title':'保证现场正常','agent':'business','action':'report'}]}
    monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *args:(proposed,{'provider':'test_fixture'}))
    saved = client.get('/api/overview').json()['tasks'][0]
    model_id = next(c['id'] for c in client.get('/api/capabilities').json() if c['kind']=='foundation_model')
    response = post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':saved['version'],
        'reason':'核查能力约束','model_profile_id':model_id})
    assert response.status_code == 200, response.text
    result = response.json()['data']['result']
    assert result['model_proposal'] == proposed and result['capability_scoped']
    assert len(result['plan']['steps']) == 2
    assert result['plan']['steps'][0]['title'] == '核对既有观测和原始文件完整性（不发起采集）'


def test_existing_only_constraint_blocks_new_observations_and_unavailable_inspection(client,monkeypatch):
    package=publish(client,manifest());obj=entity(client,package)
    task,plan=planned_task(client,obj,package,monkeypatch)
    model=next(c for c in client.get('/api/capabilities').json() if c['kind']=='foundation_model')
    captured=[]
    def forbidden(*args):
        captured.append(args[2])
        return {'summary':'越过目标约束','steps':[{'title':'补采','agent':'perception','action':'request_observation'}]},{}
    monkeypatch.setattr('generic_agents.agents.cloud_json',forbidden)
    saved=client.get('/api/overview').json()['tasks'][0]
    result=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':saved['version'],'reason':'只允许既有证据',
        'model_profile_id':model['id'],'observation_mode':'existing_only'}).json()
    assert result['status']=='blocked_model'
    assert captured[0]['properties']['steps']['items']['properties']['action']['enum']==['query_evidence','report']
    assert client.get('/api/overview').json()['acquisitions']==[]


def test_governance_approval_and_verification(client):
    package = publish(client, manifest()); obj = entity(client, package)
    with Session(engine()) as db:
        row = db.get(Entity, obj["id"])
        row.data = {"name": "导入错误", "temperature": "损坏字段"}; db.commit()
    diagnose = client.post("/api/data/diagnose").json()
    issue_id = diagnose["created_issue_ids"][0]
    repair = client.post(f'/api/issues/{issue_id}/repair', json={"expected_entity_version": 1,
                "attributes": {"name": "已校正", "temperature": 25}, "reason": "根据原始设备记录纠正类型"})
    assert repair.status_code == 200, repair.text
    plan = repair.json()
    assert client.get("/api/entities").json()[0]["data"]["temperature"] == "损坏字段"
    approval = client.post(f'/api/governance/{plan["id"]}/approve', json={"expected_version": 1, "reason": "核实原始记录"})
    assert approval.status_code == 200, approval.text
    assert process_once()
    overview = client.get("/api/overview").json()
    assert overview["governance"][0]["status"] == "applied"
    assert overview["issues"][0]["status"] == "resolved"
    assert overview["issues"][0]["data"]["verification"]["matches_approved_change"]
    assert client.get("/api/entities").json()[0]["version"] == 2


def test_stale_cannot_be_repaired_by_overwriting_attributes(client):
    package = publish(client, manifest())
    obj = entity(client, package, (datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
    issue = client.post("/api/data/diagnose").json()["created_issue_ids"][0]
    response = client.post(f'/api/issues/{issue}/repair', json={"expected_entity_version": 1,
        "attributes": obj["data"], "reason": "尝试掩盖过期"})
    assert response.status_code == 409


def test_credentials_reference_cannot_exfiltrate_environment(client):
    value = manifest("foundation_model", name="bad_reference", execution={"provider": "qwen",
              "model": "qwen-plus", "secret_ref": "GA_DATABASE_URL"}, data_policy={"cloud_allowed": True})
    row = client.post("/api/capabilities", json=value).json()
    assert client.post(f'/api/capabilities/{row["id"]}/validate', json={"expected_version": 1, "reason": "检查引用"}).status_code == 422


def test_remote_schema_and_ssrf_rejected(client):
    value = manifest(input_schema={"$ref": "https://example.com/schema"})
    assert client.post("/api/capabilities", json=value).status_code == 422
    value = manifest("tool", name="private_network", execution={"adapter": "http", "endpoint": "http://127.0.0.1/admin"})
    row = client.post("/api/capabilities", json=value).json()
    assert client.post(f'/api/capabilities/{row["id"]}/validate', json={"expected_version": 1, "reason": "检查网络"}).status_code == 422
