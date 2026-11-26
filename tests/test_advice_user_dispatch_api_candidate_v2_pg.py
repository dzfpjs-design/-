"""Cookie, CSRF and persisted state checks for the isolated user send API."""
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job
from generic_agents import same_goal_advice_dispatch_control_candidate_v2 as control
from generic_agents import same_goal_advice_user_dispatch_api_candidate_v2 as api
from tests.test_advice_dispatch_delegation_candidate_v2_pg import pending
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings(),'v2_advice_setup_test_enabled',True)
    monkeypatch.setattr(settings(),'v2_user_send_test_enabled',True)
    app.include_router(api.router,prefix='/api/workspace')


def base(s,created):
    return '/api/workspace/tasks/'+s['task_id']+'/business-advice-v2/preparations/'+created['plan_id']+'/dispatch'


def test_http_csrf_confirmation_and_revoke_survive_relogin(enabled,pending,client,account):
    s,created,body=pending;url=base(s,created)
    csrf=client.headers.pop('X-CSRF-Token')
    assert client.post(url+'/start',json=body).status_code==403
    client.headers['X-CSRF-Token']=csrf
    assert client.post(url+'/start',json={**body,'confirmed':False}).status_code==422
    sent=client.post(url+'/start',json=body)
    assert sent.status_code==201 and sent.json()['state']=='waiting',sent.text[:150]
    assert client.post(url+'/start',json=body).json()==sent.json()
    assert client.get(url).json()['state']=='waiting'
    assert client.post('/api/logout').status_code==200
    with TestClient(app) as fresh:
        login=fresh.post('/api/login',json={'username':account[0],'password':account[1]});assert login.status_code==200
        fresh.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert fresh.get(url).json()['state']=='needs_attention'
        cancelled=fresh.post(url+'/cancel',json={'request_id':str(uuid4())})
        assert cancelled.status_code==200 and cancelled.json()['state']=='cancelled'
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_other_user_and_workspace_cannot_start_or_read(enabled,pending,client,account):
    s,created,body=pending;url=base(s,created);username='test_'+uuid4().hex;password=uuid4().hex
    assert client.post('/api/users',json={'username':username,'password':password,'role':'operator'}).status_code==201
    with TestClient(app) as other:
        login=other.post('/api/login',json={'username':username,'password':password});assert login.status_code==200
        other.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        assert other.post(url+'/start',json=body).status_code in (403,409)
        assert other.get(url).status_code in (403,404)
        other.headers['X-Workspace-ID']=str(uuid4())
        assert other.get(url).status_code in (403,404)
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(CloudCall))==0


def test_replayed_confirmation_after_claim_does_not_claim_waiting(enabled,pending,client):
    s,created,body=pending;url=base(s,created)
    assert client.post(url+'/start',json=body).json()['state']=='waiting'
    with Session(engine()) as db:
        row=db.scalar(select(Job).where(Job.kind==control.KIND))
        control.claim(db,row.id);db.commit()
    response=client.post(url+'/start',json=body)
    assert response.status_code==201 and response.json()['state']=='needs_attention'


def test_api_token_cannot_confirm_for_user(enabled,pending,client,account):
    s,created,body=pending;url=base(s,created)
    token=client.post('/api/tokens').json()['token']
    with TestClient(app) as automated:
        automated.headers.update({'Authorization':'Bearer '+token,'X-Workspace-ID':account[2]})
        assert automated.post(url+'/start',json=body).status_code in (403,409)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==control.KIND))==0
