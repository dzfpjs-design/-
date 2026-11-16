import hashlib,json
from pathlib import Path
from uuid import uuid4
import httpx,pytest
from generic_agents.worker import process_once
from generic_agents.local_audio_install import download_file,probe
from generic_agents.local_audio import RECIPE_HASH

PACK='sensevoice-small-v1'
def queued(client):
    r=client.post('/api/specialists/installations',json={'pack_id':PACK});assert r.status_code==201,r.text;return r.json()
def direct_upload(client):
    return client.post('/api/audio',files={'file':('公开.mp3',Path('.runtime/audio-bootstrap/public-zh.mp3').read_bytes(),'audio/mpeg')},data={'configuration':json.dumps({'language':'zh','channel':'mono'}),'request_id':str(uuid4())})

def test_actual_audio_package_probe_register_and_new_upload_provenance(client):
    assert client.get('/api/audio/catalog').json()['workspace_prepared'] is False
    assert direct_upload(client).status_code==409
    row=queued(client);assert client.post('/api/specialists/installations',json={'pack_id':PACK}).status_code==409
    assert process_once(row['job_id'],deliver=False)
    row=next(r for r in client.get('/api/specialists/installations').json() if r['id']==row['id']);assert row['status']=='ready',row.get('error')
    assert row['smoke']['inference_performed'] and row['smoke']['cloud_calls']==0 and row['smoke']['probe_sha256']==hashlib.sha256(probe()).hexdigest();assert row['recipe_hash']==RECIPE_HASH
    r=client.post('/api/specialists/installations/'+row['id']+'/register',json={'version':row['version']});assert r.status_code==200,r.text;registered=r.json();assert registered['capability_id']
    assert client.post('/api/specialists/installations/'+row['id']+'/register',json={'version':registered['version']}).json()==registered
    r=direct_upload(client);assert r.status_code==201,r.text;source=r.json();assert source['installation']['installation_id']==row['id'];assert process_once(source['job_id'],deliver=False)
    source=client.get('/api/audio/'+source['id']).json();assert source['status']=='succeeded'
    cap=client.post('/api/audio/'+source['id']+'/register-business',json={'version':source['version']});assert cap.status_code==200,cap.text;assert cap.json()['capability_id']==registered['capability_id']
    image=client.post('/api/specialists/installations/'+row['id']+'/samples',files={'file':('x.png',b'notimage','image/png')});assert image.status_code==422,image.text

def test_audio_installation_cancel_keeps_generation_and_actual_retry(client):
    row=queued(client);r=client.post('/api/specialists/installations/'+row['id']+'/cancel',json={'version':row['version']});assert r.status_code==200;r=r.json();assert process_once(row['job_id'],deliver=False)
    retry=client.post('/api/specialists/installations',json={'pack_id':PACK,'version':r['version']});assert retry.status_code==201,retry.text;retry=retry.json();assert retry['id']==row['id'] and retry['generation']==2 and retry['attempts'][0]['status']=='cancelled'
    assert process_once(retry['job_id'],deliver=False);saved=next(x for x in client.get('/api/specialists/installations').json() if x['id']==row['id']);assert saved['status']=='ready',saved.get('error')

def test_audio_pack_rejects_unverified_source_backend_and_unknown_pack(client):
    for body in [{'pack_id':PACK,'source':'mirror'},{'pack_id':PACK,'backend':'mps'},{'pack_id':'sensevoice-invented-version'}]:assert client.post('/api/specialists/installations',json=body).status_code==422
    assert client.get('/api/specialists/installations').json()==[]

def test_audio_installation_os_failure_is_saved_and_retryable_without_path_leak(client,monkeypatch):
    import errno
    from generic_agents import local_audio_install
    def inaccessible(*args,**kwargs):raise PermissionError(errno.EACCES,'private-internal-path')
    monkeypatch.setattr(local_audio_install,'download_file',inaccessible)
    row=queued(client);assert process_once(row['job_id'],deliver=False)
    saved=next(x for x in client.get('/api/specialists/installations').json() if x['id']==row['id']);assert saved['status']=='failed' and '权限不足' in saved['error'] and 'private-internal-path' not in saved['error']
    retry=client.post('/api/specialists/installations',json={'pack_id':PACK,'version':saved['version']});assert retry.status_code==201,retry.text;assert retry.json()['id']==row['id'] and retry.json()['generation']==2

def test_running_audio_installation_cancel_reaps_actual_owned_child(client):
    import os,time
    from concurrent.futures import ThreadPoolExecutor
    row=queued(client)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(process_once,row['job_id'],deliver=False)
        until=time.monotonic()+30
        while time.monotonic()<until:
            saved=next(x for x in client.get('/api/specialists/installations').json() if x['id']==row['id'])
            if saved['status']=='installing' and saved.get('stage')=='model_check':break
            assert not future.done(),saved.get('error');time.sleep(.05)
        else:raise AssertionError('实际本地子进程没有进入检查阶段')
        pid=saved['progress']['pid'];os.kill(pid,0)
        r=client.post('/api/specialists/installations/'+row['id']+'/cancel',json={'version':saved['version']});assert r.status_code==200,r.text
        assert future.result(timeout=15)
    with pytest.raises(ProcessLookupError):os.kill(pid,0)
    saved=next(x for x in client.get('/api/specialists/installations').json() if x['id']==row['id']);assert saved['status']=='cancelled' and not saved.get('smoke')
    assert direct_upload(client).status_code==409

def test_pinned_audio_download_resumes_checks_hash_and_rejects_replacement(tmp_path):
    raw=b'fixed-public-test-asset';item={'name':'fixed.bin','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'revision':'fixed-revision'};part=tmp_path/'fixed.bin.part';part.write_bytes(raw[:5]);seen=[]
    def handler(request):
        assert request.url.scheme=='https' and request.url.host=='modelscope.cn';seen.append(request.headers.get('range'));return httpx.Response(206,headers={'Content-Range':f'bytes 5-{len(raw)-1}/{len(raw)}'},content=raw[5:])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:download_file(c,item,tmp_path,lambda *a,**k:None,lambda:None)
    assert (tmp_path/'fixed.bin').read_bytes()==raw and seen==['bytes=5-'] and not part.exists()
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*len(raw)))) as c:
        (tmp_path/'fixed.bin').unlink()
        with pytest.raises(ValueError,match='摘要'):download_file(c,item,tmp_path,lambda *a,**k:None,lambda:None)
    assert not (tmp_path/'fixed.bin').exists() and part.exists()

def test_audio_download_does_not_follow_symlink_or_publish_after_cancel(tmp_path):
    raw=b'public';item={'name':'fixed.bin','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'revision':'fixed'};outside=tmp_path/'outside';outside.write_bytes(b'untouched');root=tmp_path/'cache';root.mkdir();(root/'fixed.bin.part').symlink_to(outside)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=raw))) as c:
        with pytest.raises(ValueError,match='符号链接'):download_file(c,item,root,lambda *a,**k:None,lambda:None)
        assert outside.read_bytes()==b'untouched';(root/'fixed.bin.part').unlink();(root/'fixed.bin.part').write_bytes(raw)
        def cancelled():raise ValueError('cancelled')
        with pytest.raises(ValueError,match='cancelled'):download_file(c,item,root,lambda *a,**k:None,cancelled)
        assert not (root/'fixed.bin').exists() and (root/'fixed.bin.part').read_bytes()==raw
