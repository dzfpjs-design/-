"""Cloud API 2 configuration isolation, not an official platform test."""
import json
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Job, Membership, Workspace
from generic_agents import dji_cloud2_connections as cloud2
from tests.test_managed_mqtt_grants import vault

if not any(getattr(r, 'path', None) == '/api/dji/cloud2/connections' for r in app.routes):
    app.include_router(cloud2.router)


@pytest.fixture
def allowed(vault, monkeypatch):
    monkeypatch.setenv('GA_DJI_CLOUD2_ENDPOINTS', '192.168.0.10:443')


def payload():
    return {'request_id': str(uuid4()), 'name':'研发底座', 'host':'192.168.0.10', 'port':443,
            'http_header_name':'X-Work-Auth', 'http_header_value':'synthetic-http-secret',
            'ws_header_name':'X-WS-Auth', 'ws_header_value':'synthetic-ws-secret', 'confirm':True}


def test_credentials_persist_retry_scope_revocation_and_fences(client, account, allowed):
    body = payload(); created = client.post('/api/dji/cloud2/connections', json=body)
    assert created.status_code == 200, created.text
    receipt = created.json(); con = receipt['connection']
    assert con['status'] == 'not_verified' and con['enabled'] is True
    assert 'synthetic-http-secret' not in created.text and 'synthetic-ws-secret' not in created.text
    assert client.post('/api/dji/cloud2/connections', json=body).json() == receipt
    assert client.post('/api/dji/cloud2/connections', json={**body, 'http_header_value':'different'}).status_code == 409
    assert client.get('/api/dji/cloud2/operations/'+body['request_id']).json() == receipt
    with Session(engine()) as db:
        row = db.get(Job, con['id'])
        assert 'synthetic-http-secret' not in json.dumps(row.data)
        assert 'synthetic-ws-secret' not in json.dumps(row.data)
        other = Workspace(name='隔离工作区'); db.add(other); db.commit(); other_id=other.id
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as err: cloud2.acquire_snapshot(db, other_id, con['id'])
        assert err.value.status_code == 404
    with Session(engine()) as db:
        snapshot = cloud2.acquire_snapshot(db, account[2], con['id']); db.commit()
        assert snapshot['http_headers'][0]['value'] == 'synthetic-http-secret'
        assert snapshot['ws_headers'][0]['value'] == 'synthetic-ws-secret'
    with Session(engine()) as db:
        assert cloud2.publish_state(db, account[2], con['id'], con['version'], 'connected', 2)
        db.commit()
    assert client.get('/api/dji/cloud2/connections').json()['connections'][0]['device_count'] == 2
    req={'request_id':str(uuid4()), 'version':con['version'], 'confirm':True}
    with Session(engine()) as db:
        assert cloud2.is_current(db, account[2], con['id'], con['version'])
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, '/api/dji/cloud2/connections/'+con['id']+'/revoke', json=req)
            sleep(.2); assert not pending.done()
            db.commit(); revoked=pending.result(timeout=5)
    assert revoked.status_code == 200
    assert revoked.json()['connection']['status'] == 'revoked'
    assert client.post('/api/dji/cloud2/connections/'+con['id']+'/revoke', json=req).json() == revoked.json()
    with Session(engine()) as db:
        assert db.get(Job, con['id']).data['cipher'] == ''
        assert not cloud2.is_current(db, account[2], con['id'], con['version'])
        db.rollback()
        assert not cloud2.publish_state(db, account[2], con['id'], con['version'], 'connected', 999)
        with pytest.raises(HTTPException): cloud2.acquire_snapshot(db, account[2], con['id'])
    assert client.get('/api/dji/cloud2/connections').json()['connections'][0]['device_count'] is None


def test_authority_and_endpoint_changes_stop_existing_connection(client, account, allowed, monkeypatch):
    con = client.post('/api/dji/cloud2/connections', json=payload()).json()['connection']
    with Session(engine()) as db:
        m = db.scalar(select(Membership).where(Membership.workspace_id==account[2])); m.role='viewer'; db.commit()
    with Session(engine()) as db:
        assert not cloud2.is_current(db, account[2], con['id'], con['version']); db.rollback()
        assert not cloud2.publish_state(db, account[2], con['id'], con['version'], 'connected', 1)
        with pytest.raises(HTTPException): cloud2.acquire_snapshot(db, account[2], con['id'])
    with Session(engine()) as db:
        m = db.scalar(select(Membership).where(Membership.workspace_id==account[2])); m.role='admin'; db.commit()
    monkeypatch.setenv('GA_DJI_CLOUD2_ENDPOINTS','192.168.0.10:444')
    with Session(engine()) as db:
        assert not cloud2.is_current(db, account[2], con['id'], con['version'])


def test_validation_and_cancelled_request_never_creates_connection(client, allowed):
    body=payload()
    for update in ({'port':444}, {'host':'127.0.0.1'}, {'host':'example.com'},
                   {'http_header_name':'Host'}, {'http_header_name':'auth_keys'}, {'ws_header_name':'Sec-WebSocket-Protocol'}, {'ws_header_value':'bad;value'}, {'http_header_value':'bad\nvalue'}):
        assert client.post('/api/dji/cloud2/connections', json={**body, **update}).status_code == 422
    stop=client.post('/api/dji/cloud2/operations/'+body['request_id']+'/cancel')
    assert stop.json()['state']=='cancelled'
    assert client.post('/api/dji/cloud2/connections',json=body).json()['state']=='cancelled'
    assert client.get('/api/dji/cloud2/connections').json()['connections']==[]


def test_active_limit_and_revoke_frees_slot(client, allowed):
    records=[]
    for _ in range(8):
        response=client.post('/api/dji/cloud2/connections',json=payload())
        assert response.status_code==200, response.text
        records.append(response.json()['connection'])
    assert client.post('/api/dji/cloud2/connections',json=payload()).status_code==409
    item=records[0]
    response=client.post('/api/dji/cloud2/connections/'+item['id']+'/revoke',
                         json={'request_id':str(uuid4()),'version':item['version'],'confirm':True})
    assert response.status_code==200
    assert client.post('/api/dji/cloud2/connections',json=payload()).status_code==200


def test_stale_success_and_permission_loss_are_not_shown_as_connected(client, account, allowed):
    from datetime import timedelta
    from generic_agents.models import now
    item=client.post('/api/dji/cloud2/connections',json=payload()).json()['connection']
    with Session(engine()) as db:
        row=db.get(Job,item['id'])
        row.data={**row.data,'runtime_state':'connected','device_count':3,
                  'last_checked_at':(now()-timedelta(seconds=91)).isoformat()};db.commit()
    value=client.get('/api/dji/cloud2/connections').json()['connections'][0]
    assert value['status']=='disconnected' and value['device_count'] is None
    with Session(engine()) as db:
        row=db.get(Job,item['id']);row.data={**row.data,'last_checked_at':now().isoformat()}
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='viewer';db.commit()
    value=client.get('/api/dji/cloud2/connections').json()['connections'][0]
    assert value['status']=='authorization_lost' and value['device_count'] is None


def test_missing_key_and_cipher_tampering_stop_inflight_results(client, account, allowed, vault):
    from cryptography.fernet import Fernet
    item=client.post('/api/dji/cloud2/connections',json=payload()).json()['connection']
    original=vault.read_bytes();vault.write_bytes(Fernet.generate_key());vault.chmod(0o600)
    with Session(engine()) as db:
        assert not cloud2.is_current(db,account[2],item['id'],item['version']);db.rollback()
        assert not cloud2.publish_state(db,account[2],item['id'],item['version'],'connected',1)
    vault.write_bytes(original);vault.chmod(0o600)
    with Session(engine()) as db:
        row=db.get(Job,item['id']);row.data={**row.data,'cipher':'tampered'};db.commit()
    with Session(engine()) as db:
        assert not cloud2.is_current(db,account[2],item['id'],item['version']);db.rollback()
        assert not cloud2.publish_state(db,account[2],item['id'],item['version'],'connected',1)


def test_official_underscore_auth_header_is_supported(client, allowed):
    body=payload();body['http_header_name']='user_id';body['ws_header_name']='user_id'
    response=client.post('/api/dji/cloud2/connections',json=body)
    assert response.status_code==200,response.text
    assert response.json()['connection']['http_header_name']=='user_id'


def test_multiple_independent_auth_headers_encrypted_and_isolated(client, account, allowed):
    body=payload()
    for key in ('http_header_name','http_header_value','ws_header_name','ws_header_value'):body.pop(key)
    body.update(http_headers=[{'name':'user_id','value':'synthetic-http-user'}, {'name':'Authorization','value':'synthetic-http-token'}],
                ws_headers=[{'name':'user_id','value':'synthetic-ws-user'}, {'name':'X-WS-Token','value':'synthetic-ws-token'}])
    response=client.post('/api/dji/cloud2/connections',json=body)
    assert response.status_code==200,response.text
    item=response.json()['connection']
    assert item['http_header_names']==['user_id','Authorization']
    assert item['ws_header_names']==['user_id','X-WS-Token']
    assert 'synthetic-http-token' not in response.text and 'synthetic-ws-token' not in response.text
    with Session(engine()) as db:
        snapshot=cloud2.acquire_snapshot(db,account[2],item['id'])
        assert snapshot['http_headers']==body['http_headers'] and snapshot['ws_headers']==body['ws_headers']
        saved=json.dumps(db.get(Job,item['id']).data)
        assert all(secret not in saved for secret in ('synthetic-http-user','synthetic-http-token','synthetic-ws-user','synthetic-ws-token'))
    assert client.post('/api/dji/cloud2/connections',json=body).json()==response.json()
    for patch in ({'http_headers':[]}, {'ws_headers':[]},
                  {'http_headers':[{'name':'X-Auth','value':'a'},{'name':'x-auth','value':'b'}]},
                  {'ws_headers':[{'name':'x'+str(i),'value':'v'} for i in range(9)]}):
        assert client.post('/api/dji/cloud2/connections',json={**body,'request_id':str(uuid4()),**patch}).status_code==422


def test_oversize_auth_set_rejected_before_persisting(client, allowed):
    body=payload()
    for key in ('http_header_name','http_header_value','ws_header_name','ws_header_value'):body.pop(key)
    body.update(http_headers=[{'name':'x-http-'+str(i),'value':'a'*2048} for i in range(8)],
                ws_headers=[{'name':'x-ws-'+str(i),'value':'b'*2048} for i in range(8)])
    response=client.post('/api/dji/cloud2/connections',json=body)
    assert response.status_code==422
    assert '总长度' in response.json()['detail']
    assert client.get('/api/dji/cloud2/connections').json()['connections']==[]
