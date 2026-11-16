"""API contract tests with an isolated ledger and stubbed authentication/DB."""
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from generic_agents.db import session
from generic_agents.device_simulator_v175 import DeviceLedger
from generic_agents.security import Principal, authenticated
from generic_agents.uav_simulated_missions import SimulatedMissionService
from generic_agents import uav_simulated_missions_api as api


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    workspace = str(uuid4())
    other = str(uuid4())
    services = {}
    def fake_auth(request: Request):
        role = request.headers.get('x-test-role')
        if not role:
            raise HTTPException(401, 'unauthenticated')
        return Principal('user-1', other if role == 'other' else workspace, role, 'test-session')
    def fake_member(db, p, *, admin=False):
        if admin and p.role != 'admin':
            raise HTTPException(403, 'forbidden')
        return p
    def fake_service():
        # The dependency override need not duplicate auth: workspace scoping
        # is enforced by the coordinator and asserted explicitly below.
        return services.setdefault('single', SimulatedMissionService(tmp_path/'missions.sqlite', DeviceLedger(tmp_path/'devices.sqlite')))
    app.dependency_overrides[authenticated] = fake_auth
    app.dependency_overrides[session] = lambda: object()
    app.dependency_overrides[api.service] = fake_service
    monkeypatch.setattr(api, 'member', fake_member)
    with TestClient(app) as c:
        yield c


def test_authenticated_authorized_mission_and_persisted_retrieval(client):
    path = '/api/uav-simulation/missions'
    body = {'request_id':str(uuid4()), 'event_id':'模拟烟雾', 'start':[117.6,24.5], 'destination':[117.601,24.501]}
    assert client.get(path).status_code == 401
    assert client.post(path, json=body, headers={'x-test-role':'viewer'}).status_code == 403
    created = client.post(path, json=body, headers={'x-test-role':'admin'})
    assert created.status_code == 200, created.text
    plan = created.json()
    assert plan['simulated'] and plan['status'] == 'awaiting_approval'
    assert client.post(path, json=body, headers={'x-test-role':'admin'}).json()['id'] == plan['id']
    assert client.get(f"{path}/{plan['id']}", headers={'x-test-role':'other'}).status_code == 404
    assert client.post(f"{path}/{plan['id']}/start", json={'plan_hash':plan['plan_hash'],'confirm':True}, headers={'x-test-role':'viewer'}).status_code == 403
    assert client.post(f"{path}/{plan['id']}/start", json={'plan_hash':plan['plan_hash'],'confirm':False}, headers={'x-test-role':'admin'}).status_code == 422
    started=client.post(f"{path}/{plan['id']}/start", json={'plan_hash':plan['plan_hash'],'confirm':True}, headers={'x-test-role':'admin'})
    assert started.status_code == 200 and started.json()['status']=='flying'
    record=client.get(path, headers={'x-test-role':'viewer'}).json()['missions'][0]
    assert record['id']==plan['id'] and record['approval']['manager']=='user-1'


def test_real_cookie_csrf_membership_and_storage_survive_service_recreation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from generic_agents import security
    from generic_agents.models import User, Workspace, Membership, AuthSession

    engine=create_engine('sqlite:///'+str(tmp_path/'accounts.sqlite'), connect_args={'check_same_thread':False})
    for cls in (User, Workspace, Membership, AuthSession):
        cls.__table__.create(engine)
    with Session(engine) as db:
        user=User(username='tester',password_hash='unused',active=True)
        workspace=Workspace(name='测试工作区');db.add_all([user,workspace]);db.flush()
        db.add(Membership(user_id=user.id,workspace_id=workspace.id,role='admin'))
        uid,wid=user.id,workspace.id
        db.commit()
    config=SimpleNamespace(storage_dir=tmp_path/'storage',session_cookie_name='ga_session',allowed_origins='http://testserver',session_hours=12)
    monkeypatch.setattr(api,'settings',lambda:config)
    monkeypatch.setattr(security,'settings',lambda:config)
    with Session(engine) as db:
        row,token,csrf=security.new_session(db,db.get(User,uid));db.commit()
    app=FastAPI();app.include_router(api.router)
    def db_session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[session]=db_session
    path='/api/uav-simulation/missions'
    body={'request_id':str(uuid4()),'event_id':'模拟事件','start':[117.6,24.5],'destination':[117.601,24.501]}
    with TestClient(app, cookies={'ga_session':token}) as c:
        assert c.post(path,json=body).status_code==403
        headers={'x-csrf-token':csrf,'x-workspace-id':wid,'origin':'http://testserver'}
        created=c.post(path,json=body,headers=headers)
        assert created.status_code==200, created.text
        mid=created.json()['id']
        assert c.post(path+'/'+mid+'/start',json={'plan_hash':created.json()['plan_hash'],'confirm':True},headers=headers).json()['status']=='flying'
        assert c.get(path+'/'+mid,headers=headers).json()['approval']['manager']==uid
        with Session(engine) as db:
            membership=db.scalar(select(Membership).where(Membership.user_id==uid));membership.role='viewer';db.commit()
        assert c.post(path,json={**body,'request_id':str(uuid4())},headers=headers).status_code==403
        assert c.get(path+'/'+mid,headers=headers).status_code==200
    # A new application instance and new service objects read the same durable ledger.
    app2=FastAPI();app2.include_router(api.router);app2.dependency_overrides[session]=db_session
    with TestClient(app2,cookies={'ga_session':token}) as c:
        assert c.get(path+'/'+mid,headers={'x-workspace-id':wid}).json()['status']=='flying'
