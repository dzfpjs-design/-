"""Opt-in isolated Mosquitto MQTT 5/TLS security test; never field credentials."""
import json, os, queue, ssl, threading, time
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
import paho.mqtt.client as mqtt

from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents.dji_onboarding_api import router
from generic_agents.dji_onboarding_runtime import start_workspace_onboarding
from tests.test_dji_onboarding import config, packet
from tests.test_managed_mqtt_grants import vault

if not any(getattr(r,'path',None)=='/api/dji/onboarding/configurations' for r in app.routes):app.include_router(router)


@pytest.mark.parametrize('allow_reply',[False,True])
def test_real_mosquitto_retained_and_puback(client, account, vault, monkeypatch, allow_reply):
    from generic_agents import dji_onboarding_runtime as runtime
    observed=[]; original=runtime.reply_once
    def observed_reply(*args,**kwargs):
        observed.append('started')
        try:
            value=original(*args,**kwargs);observed.append('completed');return value
        except Exception as error:
            observed.append(type(error).__name__);raise
    monkeypatch.setattr(runtime,'reply_once',observed_reply)
    prefix='GA_DJI_MQTT_ALLOW_' if allow_reply else 'GA_DJI_MQTT_DENY_'
    keys=['HOST','PORT','USER','PASSWORD','CA']
    values={k:os.getenv(prefix+k,'') for k in keys}
    if not all(values.values()):pytest.skip('需要独立 Mosquitto MQTT5 TLS/ACL 夹具')
    monkeypatch.setenv('GA_DJI_ONBOARDING_EXPERIMENTAL','1');monkeypatch.setenv('SSL_CERT_FILE',values['CA'])
    response=client.post('/api/mqtt/authorizations',json={'request_id':str(uuid4()),'name':'隔离真实 broker',
        'host':values['HOST'],'port':int(values['PORT']),'username':values['USER'],'password':values['PASSWORD']})
    assert response.status_code==200,response.text;grant=response.json()['authorization']
    created=client.post('/api/dji/onboarding/configurations',json={'request_id':str(uuid4()),
        'broker_authorization_id':grant['id'],'config':config(),'confirm':True,'broker_acl_confirmed':True})
    assert created.status_code==200,created.text;item=created.json()['configuration']
    reply_topic='thing/product/SYNTHDOCK/requests_reply';request_topic='thing/product/SYNTHDOCK/requests'
    seen=queue.Queue();ready=threading.Event();subscribed=threading.Event();done=threading.Event();publish_results={}
    device=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,protocol=mqtt.MQTTv5,client_id='audit-device-'+uuid4().hex[:12])
    device.username_pw_set(values['USER'],values['PASSWORD']);device.tls_set_context(ssl.create_default_context())
    device.on_connect=lambda c,u,f,r,p:(c.subscribe(reply_topic,qos=1),ready.set()) if r==0 else None
    device.on_subscribe=lambda c,u,m,r,p:subscribed.set()
    device.on_message=lambda c,u,m:seen.put(bytes(m.payload))
    device.on_publish=lambda c,u,mid,reason,p:publish_results.__setitem__(mid,not reason.is_failure)
    device.connect(values['HOST'],int(values['PORT']));device.loop_start()
    assert ready.wait(5) and subscribed.wait(5)
    retained=packet('config',{'config_type':'json','config_scope':'product'})
    def publish_request(payload,retain):
        published=device.publish(request_topic,payload,qos=1,retain=retain)
        deadline=time.monotonic()+5
        while published.mid not in publish_results and time.monotonic()<deadline:time.sleep(.02)
        assert publish_results.pop(published.mid,None) is True, '模拟设备请求被 broker 拒绝'
    publish_request(retained,True)
    worker=None
    try:
        active=client.post('/api/dji/onboarding/configurations/'+item['id']+'/status',json={
            'request_id':str(uuid4()),'version':item['version'],'enabled':True,'confirm':True})
        assert active.status_code==200
        worker=start_workspace_onboarding(account[2],done)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            with Session(engine()) as db:row=db.scalar(select(Job).where(Job.id==item['id']));state=row.data.get('receiver_state')
            if state=='receiving':break
            time.sleep(.1)
        assert state=='receiving'
        time.sleep(.5);assert seen.empty(), '订阅前的保留请求产生了回复'
        after=packet('config',{'config_type':'json','config_scope':'product'})
        publish_request(after,True)
        time.sleep(.8);assert seen.empty(), '订阅后的保留请求产生了回复'
        normal=packet('config',{'config_type':'json','config_scope':'product'})
        publish_request(normal,False)
        if allow_reply:
            try: reply=json.loads(seen.get(timeout=8))
            except queue.Empty:
                with Session(engine()) as db:row=db.scalar(select(Job).where(Job.id==item['id']));state=row.data.get('receiver_state')
                pytest.fail('常规请求未获回复；接收状态='+str(state)+'；处理阶段='+str(observed))
            assert reply['method']=='config' and reply['tid']==json.loads(normal)['tid'] and reply['data']['app_key']=='synthetic-app-key'
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                with Session(engine()) as db:events=db.scalars(select(Job).where(Job.kind=='dji_onboarding_reply_v1')).all()
                if events:break
                time.sleep(.1)
            assert len(events)==1 and events[0].data['method']=='config'
        else:
            time.sleep(2);assert seen.empty()
            with Session(engine()) as db:
                assert db.scalar(select(Job.id).where(Job.kind=='dji_onboarding_reply_v1')) is None
                row=db.scalar(select(Job).where(Job.id==item['id']));assert row.data['last_stage'] is None
    finally:
        # Clear the synthetic retained request from this isolated broker.
        publish_request(b'',True)
        done.set()
        if worker:worker.join(timeout=20)
        device.disconnect();device.loop_stop()
