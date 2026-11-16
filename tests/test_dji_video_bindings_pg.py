"""Disposable PG tests for user-confirmed DJI/media associations."""
from uuid import uuid4
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import MediaGateway,MediaChannel
from generic_agents.dji_video_bindings import router
if not any(getattr(r,'path',None)=='/api/dji/video-bindings' for r in app.routes):app.include_router(router)


def seed(client,account,monkeypatch):
    monkeypatch.setattr(settings(),'mqtt_allowed_brokers','mqtt://127.0.0.1:18833')
    monkeypatch.setattr(settings(),'media_allowed_origins','http://127.0.0.1:9997,rtsp://127.0.0.1:8554')
    b={'request_id':str(uuid4()),'name':'合成机场','broker':'mqtt://127.0.0.1:18833','device_sn':'SIMDOCK123','username_ref':'','password_ref':''}
    response=client.post('/api/dji/cloud/connections',json=b);assert response.status_code==200,response.text;g=response.json()['connection']
    response=client.post('/api/dji/cloud/connections/'+g['id']+'/status',json={'request_id':str(uuid4()),'expected_version':g['version'],'status':'active'});assert response.status_code==200,response.text;g=response.json()['connection']
    with Session(engine(),expire_on_commit=False) as db:
        mg=MediaGateway(workspace_id=account[2],status='ready',data={'name':'合成视频网关','api_origin':'http://127.0.0.1:9997','rtsp_origin':'rtsp://127.0.0.1:8554','generation':1,'inventory':{'channels':[]}});db.add(mg);db.flush()
        channel=MediaChannel(workspace_id=account[2],gateway_id=mg.id,path='simulated',status='confirmed',data={'name':'模拟画面','generation':1,'read_authorized':True});db.add(channel);db.commit();cid=channel.id;version=channel.version
    return g,cid,version


def test_binding_durable_recovery_and_revocation(client,account,monkeypatch):
    g,cid,version=seed(client,account,monkeypatch);base='/api/dji/video-bindings'
    b={'request_id':str(uuid4()),'connection_id':g['id'],'channel_id':cid,'expected_connection_version':g['version'],'expected_channel_version':version,'confirmed':True}
    assert client.post(base,json={**b,'confirmed':False}).status_code==422
    response=client.post(base,json=b);assert response.status_code==200,response.text;result=response.json();binding=result['binding']
    assert client.post(base,json=b).json()==result
    assert client.post(base,json={**b,'confirmed':False}).status_code in (409,422)
    assert client.get(base+'/operations/'+b['request_id']).json()==result
    listed=client.get(base);assert listed.status_code==200 and listed.json()['bindings'][0]['available'] is True
    stopped={'request_id':str(uuid4()),'expected_version':binding['version'],'enabled':False}
    paused=client.post(base+'/'+binding['id']+'/pause',json=stopped);assert paused.status_code==200,paused.text
    assert client.post(base+'/'+binding['id']+'/pause',json=stopped).json()==paused.json()
    assert client.get(base+'/'+binding['id']+'/live').status_code==409
    assert client.get(base).json()['bindings'][0]['available'] is False
    restored=client.post(base,json={**b,'request_id':str(uuid4())});assert restored.status_code==200,restored.text
    assert restored.json()['binding']['id']==binding['id']
    with Session(engine()) as db:
        channel=db.get(MediaChannel,cid);channel.status='disabled';channel.data={**channel.data,'read_authorized':False,'generation':channel.data['generation']+1};db.commit()
    assert client.get(base+'/'+binding['id']+'/live').status_code==409
    assert client.get(base).json()['bindings'][0]['available'] is False


def test_cancel_fence_and_actor_boundary(client,account,monkeypatch):
    g,cid,version=seed(client,account,monkeypatch);base='/api/dji/video-bindings';rid=str(uuid4())
    assert client.post(base+'/operations/'+rid+'/cancel').json()['state']=='cancelled'
    b={'request_id':rid,'connection_id':g['id'],'channel_id':cid,'expected_connection_version':g['version'],'expected_channel_version':version,'confirmed':True}
    assert client.post(base,json=b).json()['state']=='cancelled'
    assert not client.get(base).json()['bindings']
