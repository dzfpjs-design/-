"""Isolated PG, synthetic Modbus, actual business LangGraph; cloud is a spy."""
import json
from uuid import uuid4
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Entity,Job
from generic_agents import sensor_automation
from generic_agents.sensor_agent_context_api import router
from tests.test_sensor_automation_pg import device,modbus,policy
from tests.test_vertical_slice import manifest,publish,explicit_goal
from tests.planning_helpers import post_plan

if not any(getattr(r,'path','')=='/api/sensor-agent-context/preview' for r in app.routes):app.include_router(router)

def setup_flow(client,account,device,monkeypatch):
    connection=modbus(client,account,device,monkeypatch);policy(client,'modbus',connection,{'operator':'above','value':2.0});assert sensor_automation.tick(account[2])==1
    with Session(engine()) as db:
        link=db.scalar(select(Job).where(Job.workspace_id==account[2],Job.kind=='modbus_binding_v1'));obj=db.get(Entity,link.data['entity_id']);entity_id=obj.id;package=obj.schema_capability_id
    model=publish(client,manifest('foundation_model',namespace='sensor_test',name='deepseek_test',allowed_agent_roles=['business'],execution={'provider':'deepseek','model':'deepseek-chat','secret_ref':'DEEPSEEK_API_KEY'},data_policy={'cloud_allowed':True}))
    payload={'title':'核查采集数据','package_id':package,'entity_ids':[entity_id],'fields':['temperature'],'observation_mode':'existing_only','goal_spec':explicit_goal({'id':entity_id},mode='existing_only',source='either')}
    response=client.post('/api/tasks',json=payload);assert response.status_code==201,response.text
    return response.json(),model

def test_authorized_evidence_reaches_real_business_graph_and_denial_is_closed(client,account,device,monkeypatch):
    task,model=setup_flow(client,account,device,monkeypatch)
    candidates=client.get('/api/sensor-agent-context/tasks/'+task['id']+'/candidates');assert candidates.status_code==200,candidates.text
    records=candidates.json()['records'];assert {r['kind'] for r in records}=={'data','alert'}
    selection=[{'kind':r['kind'],'id':r['id']} for r in records]
    body={'task_id':task['id'],'model_id':model['id'],'selection':selection}
    preview=client.post('/api/sensor-agent-context/preview',json=body);assert preview.status_code==200,preview.text
    assert client.post('/api/sensor-agent-context/authorize',json={**body,'preview_hash':preview.json()['preview_hash'],'consent':False}).status_code==422
    response=client.post('/api/sensor-agent-context/authorize',json={**body,'preview_hash':preview.json()['preview_hash'],'consent':True});assert response.status_code==200,response.text
    captured=[]
    def cloud(cap,messages,schema,p):
        captured.append(json.loads(messages[0]['content']))
        return {'summary':'核对待查线索','steps':[{'title':'核对证据','agent':'data','action':'query_evidence'},{'title':'报告','agent':'business','action':'report'}]},{'provider':'synthetic_fixture'}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud)
    response=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':task['version'],'reason':'验证','model_profile_id':model['id']})
    assert response.status_code==200,response.text
    assert response.json()['status']=='review',response.text
    data=captured[0]['evidence']['sensor_agent_context'];assert len(data['records'])==2 and all('待核实' in r['status'] for r in data['records']);assert data['records'][0]['source_timestamp'] is None
    assert '不能当成现场结论' in captured[0]['instruction']
    assert 'register_values' not in json.dumps(captured[0])
    current=next(item for item in client.get('/api/overview').json()['tasks'] if item['id']==task['id'])
    repeated=client.post('/api/tasks/'+task['id']+'/plan',json={'expected_version':current['version'],'reason':'重复规划','model_profile_id':model['id']})
    assert repeated.status_code==409 and len(captured)==1
    assert client.post('/api/sensor-agent-context/tasks/'+task['id']+'/revoke').status_code==200
    assert client.get('/api/sensor-agent-context/tasks/'+task['id']+'/candidates').json()['authorized'] is False

def test_cross_object_and_changed_preview_rejected(client,account,device,monkeypatch):
    task,model=setup_flow(client,account,device,monkeypatch)
    body={'task_id':task['id'],'model_id':model['id'],'selection':[{'kind':'data','id':str(uuid4())}]}
    assert client.post('/api/sensor-agent-context/preview',json=body).status_code==404
    entries=client.get('/api/sensor-agent-context/tasks/'+task['id']+'/candidates').json()['records'];body['selection']=[{'kind':entries[0]['kind'],'id':entries[0]['id']}]
    assert client.post('/api/sensor-agent-context/authorize',json={**body,'preview_hash':'0'*64,'consent':True}).status_code==409

def test_unapproved_sensor_data_does_not_reach_graph(client,account,device,monkeypatch):
    task,model=setup_flow(client,account,device,monkeypatch);captured=[]
    def cloud(cap,messages,schema,p):
        captured.append(json.loads(messages[0]['content']))
        return {'summary':'待核对','steps':[{'title':'报告','agent':'business','action':'report'}]},{'provider':'synthetic_fixture'}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud)
    response=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':task['version'],'reason':'验证','model_profile_id':model['id']})
    assert response.status_code==200 and response.json()['status']=='review',response.text
    assert len(captured)==1 and 'sensor_agent_context' not in captured[0]['evidence']
    assert not any(r.get('attributes',{}).get('temperature')==2.5 for r in captured[0]['evidence'].get('observation_evidence',[]))

def test_wrong_model_stops_before_cloud(client,account,device,monkeypatch):
    task,model=setup_flow(client,account,device,monkeypatch)
    other=publish(client,manifest('foundation_model',namespace='sensor_test',name='other_deepseek',allowed_agent_roles=['business'],execution={'provider':'deepseek','model':'deepseek-chat','secret_ref':'DEEPSEEK_API_KEY'},data_policy={'cloud_allowed':True}))
    records=client.get('/api/sensor-agent-context/tasks/'+task['id']+'/candidates').json()['records'];body={'task_id':task['id'],'model_id':model['id'],'selection':[{'kind':records[0]['kind'],'id':records[0]['id']}]}
    preview=client.post('/api/sensor-agent-context/preview',json=body).json();assert client.post('/api/sensor-agent-context/authorize',json={**body,'preview_hash':preview['preview_hash'],'consent':True}).status_code==200
    captured=[];monkeypatch.setattr('generic_agents.agents.cloud_json',lambda *args:captured.append(args))
    response=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':task['version'],'reason':'验证','model_profile_id':other['id']})
    assert response.status_code==409,response.text
    assert not captured

def test_task_cancellation_serializes_with_authorized_cloud_call(client,account,device,monkeypatch):
    import threading,time
    from generic_agents.models import Task,User
    from generic_agents.security import Principal
    from generic_agents.agents import business_agent
    task,model=setup_flow(client,account,device,monkeypatch)
    records=client.get('/api/sensor-agent-context/tasks/'+task['id']+'/candidates').json()['records'];body={'task_id':task['id'],'model_id':model['id'],'selection':[{'kind':records[0]['kind'],'id':records[0]['id']}]}
    preview=client.post('/api/sensor-agent-context/preview',json=body).json();assert client.post('/api/sensor-agent-context/authorize',json={**body,'preview_hash':preview['preview_hash'],'consent':True}).status_code==200
    entered=threading.Event();release=threading.Event();cancelled=threading.Event();errors=[]
    def cloud(cap,messages,schema,p):
        entered.set();assert release.wait(8)
        return {'summary':'待核对','steps':[{'title':'报告','agent':'business','action':'report'}]},{'provider':'synthetic_fixture'}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud)
    def run_graph():
        try:
            with Session(engine(),expire_on_commit=False) as db:
                user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','synthetic')
                row=db.get(Task,task['id']);row.status='planning'
                grant=db.scalar(select(Job).where(Job.workspace_id==account[2],Job.kind=='sensor_agent_authorization_v1'))
                plan_id=str(uuid4());row.data={**row.data,'current_plan_id':plan_id};grant.data={**grant.data,'plan_id':plan_id};db.commit()
                business_agent(db,p,row,model['id'],before_model=lambda signature,evidence:db.commit())
                db.commit()
        except Exception as e:errors.append(repr(e))
    def cancel():
        try:
            with Session(engine()) as db:
                row=db.scalar(select(Task).where(Task.id==task['id']).with_for_update());row.status='cancelled';db.commit();cancelled.set()
        except Exception as e:errors.append(repr(e))
    thread=threading.Thread(target=run_graph);thread.start();assert entered.wait(8),errors
    other=threading.Thread(target=cancel);other.start();time.sleep(.25);assert not cancelled.is_set()
    release.set();thread.join(8);other.join(8);assert not thread.is_alive() and not other.is_alive() and cancelled.is_set() and not errors

def test_model_without_business_role_is_rejected(client,account,device,monkeypatch):
    task,_=setup_flow(client,account,device,monkeypatch)
    model=publish(client,manifest('foundation_model',namespace='sensor_test',name='perception_only',allowed_agent_roles=['perception'],execution={'provider':'deepseek','model':'deepseek-chat','secret_ref':'DEEPSEEK_API_KEY'},data_policy={'cloud_allowed':True}))
    records=client.get('/api/sensor-agent-context/tasks/'+task['id']+'/candidates').json()['records'];body={'task_id':task['id'],'model_id':model['id'],'selection':[{'kind':records[0]['kind'],'id':records[0]['id']}]}
    response=client.post('/api/sensor-agent-context/preview',json=body);assert response.status_code==409,response.text

def test_unknown_first_answer_never_redispatches(client,account,device,monkeypatch):
    from generic_agents.models import Task,Plan,User
    from generic_agents.security import Principal
    from generic_agents.planning import run
    task,model=setup_flow(client,account,device,monkeypatch)
    records=client.get('/api/sensor-agent-context/tasks/'+task['id']+'/candidates').json()['records'];body={'task_id':task['id'],'model_id':model['id'],'selection':[{'kind':records[0]['kind'],'id':records[0]['id']}]}
    preview=client.post('/api/sensor-agent-context/preview',json=body).json();assert client.post('/api/sensor-agent-context/authorize',json={**body,'preview_hash':preview['preview_hash'],'consent':True}).status_code==200
    calls=[]
    def cloud(*args):
        calls.append(1);return {'summary':'待核对','steps':[{'title':'报告','agent':'business','action':'report'}]},{'provider':'synthetic_fixture'}
    monkeypatch.setattr('generic_agents.agents.cloud_json',cloud)
    response=post_plan(client,'/api/tasks/'+task['id']+'/plan',json={'expected_version':task['version'],'reason':'验证','model_profile_id':model['id']});assert response.json()['status']=='review'
    plan_id=response.json()['id']
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','synthetic')
        plan=db.get(Plan,plan_id);target=db.get(Task,task['id']);data=dict(plan.data);data.pop('planning_checkpoint');plan.data=data;plan.status='running';target.status='planning';db.commit()
        assert run(db,p,plan_id)=='blocked_evidence';db.commit()
    assert len(calls)==1
