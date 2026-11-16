"""Isolated PG/real ASGI check: a live preview cannot block source revocation."""
from concurrent.futures import ThreadPoolExecutor,TimeoutError as WaitTimeout
from pathlib import Path
from threading import Event,Thread
from uuid import uuid4
import socket,sys,time
import requests
import uvicorn
from generic_agents.api import app
from generic_agents.config import settings
from test_dji_video_bindings_pg import seed

def test_dji_video_live_does_not_hold_gateway_authority(client,account,monkeypatch):
    from generic_agents import media,instance_live_video
    from generic_agents import dji_video_bindings
    import subprocess
    gateway,cid,version=seed(client,account,monkeypatch)
    body={'request_id':str(uuid4()),'connection_id':gateway['id'],'channel_id':cid,'expected_connection_version':gateway['version'],'expected_channel_version':version,'confirmed':True}
    response=client.post('/api/dji/video-bindings',json=body);assert response.status_code==200,response.text
    identity=response.json()['binding']['id']
    monkeypatch.setattr(settings(),'specialist_python',Path(sys.executable))
    monkeypatch.setattr(media,'source_state',lambda *_args,**_kwargs:{'source_id':'synthetic'})
    gate=Event();seen=Event();calls=[0]
    def frame(_proc,_deadline):
        calls[0]+=1
        if calls[0]>1:
            seen.set();gate.wait(8)
        return b'\xff\xd8synthetic\xff\xd9'
    monkeypatch.setattr(instance_live_video,'_read_frame',frame)
    class Stream:
        def write(self,_data):pass
        def close(self):pass
    class Proc:
        def __init__(self,*_a,**_kw):self.stdin=Stream();self.stdout=Stream();self._alive=True
        def poll(self):return None if self._alive else 0
        def terminate(self):self._alive=False
        def kill(self):self._alive=False
        def wait(self,timeout=None):return 0
    monkeypatch.setattr(subprocess,'Popen',Proc)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='critical',access_log=False))
    thread=Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    assert server.started
    session=requests.Session();base=f'http://127.0.0.1:{port}/api'
    stream=None;pool=ThreadPoolExecutor(max_workers=1)
    try:
        login=session.post(base+'/login',json={'username':account[0],'password':account[1]},timeout=5);assert login.status_code==200
        session.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':account[2]})
        stream=session.get(base+'/dji/video-bindings/'+identity+'/live',stream=True,timeout=10);assert stream.status_code==200
        frames=stream.iter_content(chunk_size=None)
        assert b'synthetic' in next(frames);assert seen.wait(3)
        payload={'request_id':str(uuid4()),'expected_version':gateway['version'],'status':'paused'}
        future=pool.submit(session.post,base+'/dji/cloud/connections/'+gateway['id']+'/status',json=payload,timeout=10)
        try:result=future.result(timeout=2)
        except WaitTimeout:raise AssertionError('直播期间网关暂停被请求事务锁阻塞') from None
        assert result.status_code==200,result.text
        gate.set()
        future=pool.submit(next,frames,None)
        try:after_revoke=future.result(timeout=3)
        except WaitTimeout:raise AssertionError('网关暂停后视频未及时结束') from None
        assert after_revoke is None, '网关暂停后仍发送了新画面'
    finally:
        gate.set()
        if stream is not None:stream.close()
        pool.shutdown(wait=True,cancel_futures=True)
        server.should_exit=True;thread.join(timeout=10)
