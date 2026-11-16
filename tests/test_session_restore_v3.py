"""Cookie-only session attach: disposable PostgreSQL, real authentication, no replay."""
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,event,func
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import AuthSession,Membership,User,Workspace,now
from generic_agents.security import token_hash,new_session,password_hash
from tests.test_setup import config

PATH='/api/session/restore'
PREFIX='ga_csrf_restore_v1.'


def restore(client,**headers):
    return client.get(PATH,headers={'X-Session-Restore':'1',**headers})


def copy_tab(original):
    tab=TestClient(app);tab.cookies.update(original.cookies);tab.headers.update(dict(original.headers))
    tab.headers.pop('X-CSRF-Token',None)
    return tab


def write(client,note):
    draft=client.get('/api/setup/draft').json();value=draft['configuration'] or config()
    value['setupNotes']={'manual':note}
    return client.put('/api/setup/draft',json={'revision':draft['revision'],'configuration':value})


def test_restore_attaches_same_cookie_without_database_write_and_both_tabs_keep_writing(client,account):
    original_cookie=token_hash(client.cookies.get('ga_session'));original_csrf=client.headers['X-CSRF-Token']
    with Session(engine()) as db:
        before=db.scalar(select(AuthSession).where(AuthSession.token_hash==original_cookie))
        before_state=(before.id,before.token_hash,before.csrf_hash,before.expires_at,before.revoked,before.kind)
        count=db.scalar(select(func.count()).select_from(AuthSession))
        user_id=before.user_id
    tab=copy_tab(client);assert tab.get('/api/me').status_code==200
    statements=[]
    def record(connection,cursor,statement,parameters,context,executemany):
        statements.append(statement.lstrip().split(None,1)[0].upper())
    event.listen(engine(),'before_cursor_execute',record)
    try:response=restore(tab)
    finally:event.remove(engine(),'before_cursor_execute',record)
    assert response.status_code==200,response.text
    data=response.json();assert set(data)=={'csrf_token','username','user_id','workspace_id','role'}
    assert data['csrf_token'].startswith(PREFIX) and data['csrf_token']!=original_csrf
    assert data['user_id']==user_id and data['workspace_id']==account[2] and data['username']==account[0] and data['role']=='admin'
    assert tab.get('/api/me').json()['user_id']==data['user_id']
    assert 'no-store' in response.headers['Cache-Control'] and 'Set-Cookie' not in response.headers
    assert statements and set(statements)=={'SELECT'}
    assert token_hash(tab.cookies.get('ga_session'))==original_cookie
    with Session(engine()) as db:
        after=db.get(AuthSession,before_state[0])
        assert (after.id,after.token_hash,after.csrf_hash,after.expires_at,after.revoked,after.kind)==before_state
        assert db.scalar(select(func.count()).select_from(AuthSession))==count
    assert restore(tab).json()==data
    tab.headers['X-CSRF-Token']=data['csrf_token']
    assert write(client,'A 原随机令牌保存').status_code==200
    assert write(tab,'B 恢复令牌保存').status_code==200
    assert write(client,'A 随后仍可保存').status_code==200
    assert client.headers['X-CSRF-Token']==original_csrf
    assert client.get('/api/setup/draft').json()['configuration']['setupNotes']['manual']=='A 随后仍可保存'
    tab.close()


@pytest.mark.parametrize('headers',[
    {'X-Session-Restore':''},{'X-Session-Restore':'0'},{'X-Session-Restore':'1,1'},
    {'Origin':'http://attacker.invalid'},{'Origin':'null'},{'Origin':''},{'Origin':'http://localhost:8765.attacker.invalid'}])
def test_restore_rejects_nonexplicit_or_untrusted_source_without_cookie_changes(client,monkeypatch,headers):
    monkeypatch.setattr(settings(),'allowed_origins','http://localhost:8765,http://127.0.0.1:8765')
    original=token_hash(client.cookies.get('ga_session'));response=restore(client,**headers)
    assert response.status_code==403 and 'csrf_token' not in response.json()
    assert token_hash(client.cookies.get('ga_session'))==original and 'Set-Cookie' not in response.headers


def test_restore_missing_custom_header_and_cookie_are_rejected(client):
    assert client.get(PATH).status_code==403
    with TestClient(app) as anonymous:
        assert restore(anonymous).status_code==401


def test_restore_valid_origin_does_not_relax_write_origin_or_csrf(client,monkeypatch):
    monkeypatch.setattr(settings(),'allowed_origins','http://localhost:8765')
    response=restore(client,Origin='http://localhost:8765');assert response.status_code==200
    client.headers['X-CSRF-Token']=response.json()['csrf_token'];client.headers['Origin']='http://attacker.invalid'
    assert write(client,'来源不得放宽').status_code==403
    client.headers.pop('Origin');client.headers['X-CSRF-Token']=response.json()['csrf_token']+'x'
    assert write(client,'伪造恢复令牌不得通过').status_code==403


def test_restore_never_accepts_opaque_or_wildcard_origin_even_if_misconfigured(client,monkeypatch):
    monkeypatch.setattr(settings(),'allowed_origins','*,null')
    assert restore(client,Origin='*').status_code==403 and restore(client,Origin='null').status_code==403


@pytest.mark.parametrize('token',[b'garb\xe2ge',b'x'*20000])
def test_nonascii_or_long_csrf_header_is_rejected_without_server_error(client,token):
    draft=client.get('/api/setup/draft').json()
    response=client.put('/api/setup/draft',json={'revision':draft['revision'],'configuration':config()},
        headers={b'X-CSRF-Token':token})
    assert response.status_code==403 and response.json()['detail']=='CSRF validation failed'


@pytest.mark.parametrize('state',['expired','revoked','disabled','membership_removed'])
def test_restore_and_restored_write_recheck_current_identity_permissions(client,account,state):
    response=restore(client);assert response.status_code==200
    client.headers['X-CSRF-Token']=response.json()['csrf_token']
    with Session(engine()) as db:
        session=db.scalar(select(AuthSession).where(AuthSession.token_hash==token_hash(client.cookies.get('ga_session'))))
        if state=='expired':session.expires_at=now()-timedelta(seconds=1)
        elif state=='revoked':session.revoked=True
        elif state=='disabled':db.get(User,session.user_id).active=False
        else:db.delete(db.scalar(select(Membership).where(Membership.user_id==session.user_id,Membership.workspace_id==account[2])))
        db.commit()
    expected=403 if state=='membership_removed' else 401
    assert restore(client).status_code==expected
    assert client.post('/api/logout').status_code==expected


@pytest.mark.parametrize('different_actor',[False,True])
def test_restored_token_cannot_move_between_cookie_sessions_or_accounts(client,account,different_actor):
    first=restore(client);assert first.status_code==200
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]))
        if different_actor:
            user=User(username='other_'+uuid4().hex,password_hash=password_hash(uuid4().hex));db.add(user);db.flush()
            db.add(Membership(user_id=user.id,workspace_id=account[2],role='admin'))
        _,cookie,_=new_session(db,user);db.commit()
    with TestClient(app) as other:
        other.cookies.set('ga_session',cookie);other.headers['X-Workspace-ID']=account[2]
        second=restore(other);assert second.status_code==200
        assert second.json()['csrf_token']!=first.json()['csrf_token']
        other.headers['X-CSRF-Token']=first.json()['csrf_token'];assert write(other,'跨会话令牌拒绝').status_code==403
        other.headers['X-CSRF-Token']=second.json()['csrf_token'];assert write(other,'本会话有效').status_code==200


def test_restore_denies_bearer_and_api_cookie_even_when_a_valid_cookie_is_also_present(client,account):
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));_,bearer,_=new_session(db,user,kind='api');db.commit()
    assert restore(client,Authorization='Bearer '+bearer).status_code==401
    assert restore(client,Authorization='Basic invalid').status_code==401
    assert restore(client,Authorization='').status_code==401
    with TestClient(app) as api_cookie:
        api_cookie.cookies.set('ga_session',bearer);api_cookie.headers['X-Workspace-ID']=account[2]
        assert restore(api_cookie).status_code==401


def test_restore_is_workspace_bound_and_does_not_grant_viewer_write_access(client,account):
    with Session(engine()) as db:
        session=db.scalar(select(AuthSession).where(AuthSession.token_hash==token_hash(client.cookies.get('ga_session'))))
        own=Workspace(name='可读工作区');foreign=Workspace(name='非成员工作区');db.add_all([own,foreign]);db.flush()
        db.add(Membership(user_id=session.user_id,workspace_id=own.id,role='viewer'));db.commit();own_id,foreign_id=own.id,foreign.id
    denied=restore(client,**{'X-Workspace-ID':foreign_id});assert denied.status_code==403 and 'csrf_token' not in denied.json()
    viewer=restore(client,**{'X-Workspace-ID':own_id});assert viewer.status_code==200
    assert viewer.json()['workspace_id']==own_id and viewer.json()['role']=='viewer'
    client.headers.update({'X-Workspace-ID':own_id,'X-CSRF-Token':viewer.json()['csrf_token']})
    assert write(client,'只读角色不可写').status_code==403
