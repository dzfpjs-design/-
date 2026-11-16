"""Disposable-DB regression for revoking cloud sensor context during planning."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor,TimeoutError
from fastapi.testclient import TestClient
from generic_agents.api import app
from generic_agents.worker import process_once
from generic_agents.db import engine
from generic_agents.models import Task
from sqlalchemy.orm import Session
from tests.test_sensor_agent_context_pg import setup_flow,device
from tests.test_vertical_slice import manifest,publish


def test_cancel_and_revoke_fences_cloud_send(client,account,device,monkeypatch):
    task,_=setup_flow(client,account,device,monkeypatch)
    model=publish(client,manifest('foundation_model',namespace='sensor_test',name='business_only',
        allowed_agent_roles=['business'],execution={'provider':'deepseek','model':'deepseek-chat','secret_ref':'DEEPSEEK_API_KEY'},data_policy={'cloud_allowed':True}))
    records=client.get('/api/sensor-agent-context/tasks/'+task['id']+'/candidates').json()['records']
    body={'task_id':task['id'],'model_id':model['id'],'selection':[{'kind':records[0]['kind'],'id':records[0]['id']}]}
    preview=client.post('/api/sensor-agent-context/preview',json=body)
    assert preview.status_code==200
    assert client.post('/api/sensor-agent-context/authorize',json={**body,'preview_hash':preview.json()['preview_hash'],'consent':True}).status_code==200
    entered=threading.Event();release=threading.Event();sent=[]
    def cloud(cap,messages,schema,p):
        entered.set();assert release.wait(30)
        sent.append(json.loads(messages[0]['content']))
        return {'summary':'核对','steps':[{'title':'报告','agent':'business','action':'report'}]},{'provider':'synthetic_fixture'}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud)
    response=client.post('/api/tasks/'+task['id']+'/plan',json={'expected_version':task['version'],'reason':'测试','model_profile_id':model['id']})
    assert response.status_code==200 and response.json()['status']=='queued',response.text
    job=response.json()['data']['planning_job_id']
    other=TestClient(app)
    login=other.post('/api/login',json={'username':account[0],'password':account[1]})
    other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            worker=pool.submit(process_once,job)
            assert entered.wait(15)
            with Session(engine()) as db:
                current=db.get(Task,task['id']);version=current.version
                assert current.status=='planning'
            cancellation=pool.submit(other.post,'/api/tasks/'+task['id']+'/cancel',json={'expected_version':version,'reason':'撤回证据'})
            try:cancel=cancellation.result(timeout=3)
            except TimeoutError:
                # A locked in-flight call may finish before cancellation is acknowledged.
                assert not sent
                release.set();worker.result(timeout=20)
                assert cancellation.result(timeout=20).status_code in (200,409)
                assert len(sent)==1
                return
            assert cancel.status_code==200,cancel.text
            revoke=other.post('/api/sensor-agent-context/tasks/'+task['id']+'/revoke')
            assert revoke.status_code==200,revoke.text
            release.set();worker.result(timeout=20)
        assert not sent, '撤权成功后云调用仍获得了包含传感器证据的请求'
    finally:
        release.set();other.close()


def test_sensor_context_rejects_model_not_approved_for_business(client,account,device,monkeypatch):
    task,_=setup_flow(client,account,device,monkeypatch)
    model=publish(client,manifest('foundation_model',namespace='sensor_test',name='perception_only',
        allowed_agent_roles=['perception'],execution={'provider':'deepseek','model':'deepseek-chat','secret_ref':'DEEPSEEK_API_KEY'},data_policy={'cloud_allowed':True}))
    records=client.get('/api/sensor-agent-context/tasks/'+task['id']+'/candidates').json()['records']
    body={'task_id':task['id'],'model_id':model['id'],'selection':[{'kind':records[0]['kind'],'id':records[0]['id']}]}
    assert client.post('/api/sensor-agent-context/preview',json=body).status_code==409
