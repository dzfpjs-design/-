"""Disposable PG and independent analyzer processes; provider is synthetic."""
import os
from pathlib import Path
import subprocess
import socket
import threading
import sys
import time
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job
from tests.test_sensor_native_analysis_pg import setup_analysis
from tests.test_same_goal_advice_initial_evaluation_pg import reject_non_loopback_network

@pytest.fixture
def device():
    # A documentation-only private address; the transport below never connects.
    return '10.255.255.253', 15020, []


WORKER = r'''
import json, socket, sys, threading, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
import httpx
original_connect = socket.socket.connect
def local_only(self, address):
    if not isinstance(address,tuple) or address[0] != '127.0.0.1':
        raise RuntimeError('non-loopback connection forbidden')
    return original_connect(self,address)
socket.socket.connect = local_only
from generic_agents import sensor_native_analysis as svc
from generic_agents import cloud_business_reasoning_v197 as transport
mode, workspace, audit_path = sys.argv[1:]
if mode == 'crash':
    # Simulate an already expired five-minute lease without a five-minute wait.
    svc.now = lambda: datetime.now(timezone.utc) - timedelta(minutes=6)
if mode == 'save': svc._recover = lambda *args: False
class SyntheticClient:
    def __init__(self,*args,**kwargs): pass
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def post(self,url,headers,json):
        with open(audit_path,'a') as f:
            f.write('request\n'); f.flush()
        if mode == 'crash': time.sleep(60)
        value={'summary':'合成信息','observations':[{'evidence_refs':['R1'],'assessment':'待核实','limitations':'时间未知'}],'suggested_checks':['核查']}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':__import__('json').dumps(value,ensure_ascii=False)}}],
            'usage':{'prompt_tokens':30,'completion_tokens':20,'total_tokens':50}},headers={'x-request-id':'synthetic-only'})
transport.httpx.Client = SyntheticClient
stop=threading.Event()
svc.start_workspace_analyzer(workspace,stop)
while True: time.sleep(1)
'''


def wait_until(condition, timeout=30):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=condition()
        if value:return value
        time.sleep(.1)
    raise AssertionError('isolated worker did not reach expected state')


def start(tmp_path, workspace, mode):
    env=os.environ.copy();env.update(GA_CLOUD_ALLOWED_PROVIDERS='deepseek',DEEPSEEK_API_KEY='synthetic-only',
        DASHSCOPE_API_KEY='',GA_STORAGE_DIR=str(settings().storage_dir))
    output=open(tmp_path/(mode+'.log'),'w')
    process=subprocess.Popen([sys.executable,'-c',WORKER,mode,workspace,str(tmp_path/'calls')],
        env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
    output.close()
    return process


def stop(process, *, kill=False):
    if process.poll() is None:
        process.kill() if kill else process.terminate()
        process.wait(timeout=10)


def state(identity):
    with Session(engine()) as db:
        return db.get(Job,identity).data['state']


def queue(client, account, device, monkeypatch):
    monkeypatch.setattr('generic_agents.modbus_readonly_transport.read_registers', lambda *args: [25])
    task,base,packet,model=setup_analysis(client,account,device,monkeypatch)
    preview=client.post(base+'/analyses/preview',json={'request_id':str(uuid4()),'packet_id':packet['id'],
        'model_id':model,'max_output_tokens':256}).json()
    request={'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    response=client.post(base+'/analyses/'+preview['preview_id']+'/send',json=request)
    assert response.status_code==200,response.text
    return base,packet,model,request,response.json()['analysis']['id']


def test_worker_restart_recovers_saved_answer_without_resending(client,account,device,monkeypatch,tmp_path):
    base,packet,model,request,identity=queue(client,account,device,monkeypatch)
    first=start(tmp_path,account[2],'save')
    try:
        wait_until(lambda: state(identity)=='response_saved' or (first.poll() is not None and pytest.fail('first worker exited')))
    finally:stop(first)
    assert (tmp_path/'calls').read_text().splitlines()==['request']
    engine().dispose()
    second=start(tmp_path,account[2],'recover')
    try:
        wait_until(lambda: state(identity)=='completed' or (second.poll() is not None and pytest.fail('second worker exited')))
    finally:stop(second)
    result=client.get(base+'/analyses/requests/'+request['request_id']).json()
    assert result['state']=='completed' and result['analysis']['receipt']['usage']['total_tokens']==50
    assert (tmp_path/'calls').read_text().splitlines()==['request']
    with Session(engine()) as db: assert len(list(db.scalars(select(CloudCall))))==1


def test_killed_worker_unknown_is_not_automatically_resent(client,account,device,monkeypatch,tmp_path):
    base,packet,model,request,identity=queue(client,account,device,monkeypatch)
    first=start(tmp_path,account[2],'crash')
    try:wait_until(lambda: (tmp_path/'calls').exists() or (first.poll() is not None and pytest.fail('first worker exited')))
    finally:stop(first,kill=True)
    assert state(identity)=='dispatching'
    engine().dispose()
    second=start(tmp_path,account[2],'recover')
    try:
        time.sleep(7)
        assert second.poll() is None
        result=client.get(base+'/analyses/requests/'+request['request_id']).json()
        assert result['state']=='response_unknown'
        assert (tmp_path/'calls').read_text().splitlines()==['request']
        assert client.get(base+'/analyses/history').json()['analyses'][0]['state']=='response_unknown'
        with Session(engine()) as db: assert len(list(db.scalars(select(CloudCall))))==1
    finally:stop(second)
