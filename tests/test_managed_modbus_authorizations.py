"""Workspace GUI authorization, real protocol read and safe recovery."""
import json, socket, threading, time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Membership, User


@pytest.fixture(autouse=True)
def policy(monkeypatch):
    monkeypatch.setenv('GA_MANAGED_MODBUS_ENABLED','1')
    monkeypatch.setenv('GA_MANAGED_GATEWAY_NETWORKS','192.168.0.0/16')
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS','[]')


@pytest.fixture
def device():
    host='192.168.0.100'; server=socket.socket()
    try:server.bind((host,0))
    except OSError:pytest.skip('No authorized private test interface')
    server.listen();server.settimeout(.1);running=threading.Event();running.set();calls=[]
    def serve():
        while running.is_set():
            try:conn,_=server.accept()
            except socket.timeout:continue
            except OSError:break
            with conn:
                req=conn.recv(12);calls.append(req)
                if len(req)==12:conn.sendall(req[:2]+b'\x00\x00\x00\x05'+req[6:7]+req[7:8]+b'\x02\x00\x2a')
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    yield host,server.getsockname()[1],calls
    running.clear();server.close();thread.join(timeout=2)


def payload(host,port):
    return {'request_id':str(uuid4()),'name':'隔离测试设备','host':host,'port':port,'unit':1,'start':0,'end':10,'functions':[3]}


def create(client,body):
    response=client.post('/api/modbus/authorizations',json=body);assert response.status_code==200,response.text
    return response.json()['result']


def test_gui_authorization_to_real_read_and_revoke(client,device):
    host,port,calls=device;body=payload(host,port)
    denied=client.post('/api/modbus/authorizations',json={**body,'host':'8.8.8.8'})
    assert denied.status_code==403
    grant=create(client,body);assert create(client,body)==grant
    assert client.get('/api/modbus/grants').json()['grants'][0]['id']==grant['id']
    body2={'request_id':str(uuid4()),'name':'测试连接','grant_id':grant['id'],'function':3,'address':1,'value_type':'uint16'}
    connection=client.post('/api/modbus/connections',json=body2).json()['connection']
    path='/api/modbus/connections/'+connection['id']+'/readings'
    result=client.post(path,json={'request_id':str(uuid4()),'version':connection['version']}).json()['reading']
    assert result['state']=='succeeded' and result['value']==42 and len(calls)==1
    change={'request_id':str(uuid4()),'version':grant['version'],'enabled':False}
    stopped=client.post('/api/modbus/authorizations/'+grant['id']+'/status',json=change)
    assert stopped.status_code==200 and not stopped.json()['result']['enabled']
    assert not client.get('/api/modbus/grants').json()['grants']
    assert client.post(path,json={'request_id':str(uuid4()),'version':connection['version']}).status_code==409
    assert len(calls)==1
    history=client.get(path).json()['readings'];assert history[0]['id']==result['id']


def test_recovery_fences_unknown_request_and_role_loss(client,account):
    request=payload('192.168.0.9',502)
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));member=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==account[2]));member.role='viewer';db.commit()
    body={'kind':'create','submission':request,'grant_id':None}
    response=client.post('/api/modbus/authorizations/operations/'+request['request_id']+'/resolve',json=body)
    assert response.status_code==200 and response.json()['state']=='cancelled', response.text
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='admin';db.commit()
    assert client.post('/api/modbus/authorizations',json=request).json()['state']=='cancelled'
    assert not client.get('/api/modbus/grants').json()['grants']


def test_revoke_waits_for_inflight_read(client,device,monkeypatch):
    import generic_agents.modbus_gateway_api as api
    host,port,calls=device;grant=create(client,payload(host,port));request={'request_id':str(uuid4()),'name':'测试连接','grant_id':grant['id'],'function':3,'address':0,'value_type':'uint16'}
    connection=client.post('/api/modbus/connections',json=request).json()['connection'];started=threading.Event();release=threading.Event();original=api.read_registers
    def wait_read(*args):started.set();assert release.wait(5);return original(*args)
    monkeypatch.setattr(api,'read_registers',wait_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        read=pool.submit(client.post,'/api/modbus/connections/'+connection['id']+'/readings',json={'request_id':str(uuid4()),'version':connection['version']})
        assert started.wait(5)
        revoke=pool.submit(client.post,'/api/modbus/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':grant['version'],'enabled':False})
        time.sleep(.2);assert not revoke.done();release.set()
        assert read.result(timeout=8).status_code==200 and revoke.result(timeout=8).status_code==200
    assert len(calls)==1 and not client.get('/api/modbus/grants').json()['grants']


def test_revoked_managed_grant_never_restores_identical_legacy_grant(client,account,monkeypatch):
    body=payload('192.168.0.9',502);grant=create(client,body)
    legacy={'workspace':account[2],'host':body['host'],'port':body['port'],'unit':body['unit'],'start':body['start'],'end':body['end'],'functions':body['functions']}
    monkeypatch.setenv('GA_MODBUS_READ_GRANTS',json.dumps([legacy]))
    assert client.post('/api/modbus/authorizations/'+grant['id']+'/status',json={'request_id':str(uuid4()),'version':grant['version'],'enabled':False}).status_code==200
    assert client.get('/api/modbus/grants').json()['grants']==[]
    monkeypatch.setenv('GA_MANAGED_MODBUS_ENABLED','0')
    assert client.get('/api/modbus/grants').json()['grants']==[]
