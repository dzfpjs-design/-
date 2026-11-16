"""Media authorization, provenance and recovery contracts; actual RTSP proof is separate."""
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import media,media_api,local_video
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Capability,CloudCall,MediaChannel,MediaGateway,Membership,StreamSession,SpecialistInstallation
from generic_agents.worker import process_once
from tests.test_local_specialists import mocked_runtime,ready
from tests.test_local_video import RAW,CFG,frame

DIRECTORY={'channels':[{'path':'camera/front','ready':True,'tracks':['H264'],'source_id':'session-A','source_type':'rtspSession','ready_at':None}],'checked_at':'2026-10-08T12:00:00+00:00','truncated':False}

def receipt(seconds=4,partial=False):
    received_end=time.time()
    return {'kind':'capture.complete','schema':'rtsp-capture-v1','file':'stream.mp4','requested_seconds':seconds,'duration_seconds':seconds,
        'partial':partial,'error':None,'packets':25,'first_source_pts':-90000,'source_time_base':'1/90000',
        'received_start_unix':received_end-seconds,'received_end_unix':received_end,'source_clock_utc':'unknown','codec':'h264',
        'audio_captured':False,'decoder_version':'16.1.0','probe_buffer_replayed':False,'cloud_calls':0}

@pytest.fixture
def media_runtime(mocked_runtime,monkeypatch):
    monkeypatch.setattr(media,'inventory',lambda gateway:copy.deepcopy(DIRECTORY))
    monkeypatch.setattr(media_api,'inventory',lambda gateway:copy.deepcopy(DIRECTORY))
    def capture(script,request,temp,guard,receive):
        guard();(temp/'stream.mp4').write_bytes(RAW)
        receive({'kind':'capture.progress','seconds':1.,'packets':7},temp);guard();receive(receipt(request['seconds']),temp)
    monkeypatch.setattr(media,'child',capture)
    def video(content,backend,configuration,checkpoints,guard,receive,lock_fd=None):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory);receive({'kind':'decoded','samples':9},temp)
            for i in range(9):
                guard();raw,value=frame(i);(temp/value['file']).write_bytes(raw);receive(value,temp)
            receive({'kind':'complete','schema':'local-video-v1','backend':backend,'cloud_calls':0,'sample_count':9,
                'decoded_frames':9,'duration_seconds':4.,'width':20,'height':20,'tracks':[],'gap_intervals':[],'audio_processed':False},temp)
    monkeypatch.setattr(local_video,'execute_video',video)

def gateway(client):
    r=client.post('/api/media/gateways',json={'name':'视频网关','api_origin':'http://127.0.0.1:9997','rtsp_origin':'rtsp://127.0.0.1:8554'});assert r.status_code==201,r.text
    row=r.json();r=client.post('/api/media/gateways/'+row['id']+'/discover',json={'version':row['version']});assert r.status_code==200,r.text
    return r.json()

def channel(client,g=None):
    g=g or gateway(client)
    r=client.post('/api/media/channels',json={'gateway_id':g['id'],'gateway_version':g['version'],'path':'camera/front','name':'前门','read_authorized':True});assert r.status_code==201,r.text
    return r.json()

def capture(client,ch,installation,**extra):
    r=client.post('/api/media/channels/'+ch['id']+'/capture',json={'channel_version':ch['version'],'installation_id':installation['id'],'seconds':4,'configuration':CFG,**extra});return r

def session(client,identity):return next(x for x in client.get('/api/media/sessions').json() if x['id']==identity)

def finish(client,initial):
    assert process_once(initial['job_id'],deliver=False)
    s=session(client,initial['id']);assert s['status']=='captured',s
    run=client.get('/api/videos/'+s['video_run_id']).json();assert process_once(run['job_id'],deliver=False)
    return session(client,s['id']),client.get('/api/videos/'+run['id']).json()

def test_capture_processing_delivery_register_and_exact_scope(client,media_runtime,account):
    installation=ready(client);ch=channel(client);r=capture(client,ch,installation);assert r.status_code==201
    s,d=finish(client,r.json());assert d['status']=='succeeded',d['error']
    assert d['delivery']=={'data_received':9,'business_received':1,'pending':0}
    assert all(f['source_context']['channel_id']==ch['id'] and f['utc'] is None for f in d['frames'])
    assert client.get('/api/videos/'+d['id']+'/original').content==RAW
    ch=client.get('/api/media/channels').json()[0];assert ch['validation_current']
    r=client.post('/api/media/channels/'+ch['id']+'/register',json={'version':ch['version']});assert r.status_code==200,r.text
    ch=r.json()
    with Session(engine()) as db:
        from generic_agents.security import Principal
        membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));p=Principal(membership.user_id,account[2],'admin','test')
        cap=db.get(Capability,ch['capability_id']);assert cap.status=='published' and cap.data['data_policy']['cloud_allowed'] is False
        result=media.registered_read(db,p,cap.data['execution']);assert len(result['sessions'])==1 and result['not_authoritative']
        widened={**cap.data['execution'],'side_effect':True}
        with pytest.raises(HTTPException):media.registered_read(db,p,widened)
        assert not db.scalars(select(CloudCall)).all()

@pytest.mark.parametrize('value',['http://example.com:9997','http://169.254.169.254:80','http://127.0.0.1:9997/a','http://u:secret@127.0.0.1:9997','http://127.0.0.1:9997?url=x','http://127.0.0.1:9998'])
def test_origins_reject_ssrf_redirect_paths_and_credentials(value):
    with pytest.raises(HTTPException):media.origin(value,'api')

def test_directory_contract_rejects_duplicate_and_invalid_tracks(monkeypatch):
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def iter_bytes(self):yield json.dumps({'pageCount':1,'items':[{'name':'camera','tracks':'H264','source':{}}]}).encode()
    class Client:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def stream(self,*args,**kwargs):return Response()
    monkeypatch.setattr(media.httpx,'Client',Client)
    g=SimpleNamespace(data={'api_origin':'http://127.0.0.1:9997','rtsp_origin':'rtsp://127.0.0.1:8554'})
    with pytest.raises(HTTPException):media.inventory(g)

def test_explicit_authorization_directory_and_cas(client,media_runtime):
    g=gateway(client);body={'gateway_id':g['id'],'gateway_version':g['version'],'path':'camera/front','name':'通道'}
    assert client.post('/api/media/channels',json=body).status_code==422
    assert client.post('/api/media/channels',json={**body,'read_authorized':True,'path':'../secret'}).status_code==422
    assert client.post('/api/media/channels',json={**body,'read_authorized':True,'path':'missing'}).status_code==409
    assert client.post('/api/media/channels',json={**body,'read_authorized':True,'gateway_version':g['version']-1}).status_code==409
    assert client.post('/api/media/channels',json={**body,'read_authorized':True,'name':'   '}).status_code==422
    first=channel(client,g);second=channel(client,g);assert first['id']==second['id']

def test_cancel_and_new_request_keep_old_session(client,media_runtime):
    install=ready(client);ch=channel(client);s=capture(client,ch,install).json()
    assert capture(client,ch,install).json()['id']==s['id']
    assert capture(client,ch,install,seconds=6).status_code==409
    assert client.post('/api/media/sessions/'+s['id']+'/cancel',json={'version':s['version']}).status_code==200
    process_once(s['job_id'],deliver=False);old=session(client,s['id']);assert old['status']=='cancelled' and old['evidence_id'] is None
    new=capture(client,ch,install).json();assert new['id']!=s['id'];finish(client,new)
    assert session(client,s['id'])['status']=='cancelled'

def test_stop_intent_survives_progress_version_and_is_idempotent(client,media_runtime):
    install=ready(client);ch=channel(client);s=capture(client,ch,install).json()
    with Session(engine()) as db:
        row=db.get(StreamSession,s['id']);row.status='capturing';row.version+=3;row.data={**row.data,'progress':{'seconds':1.5}};db.commit()
    assert client.post('/api/media/sessions/'+s['id']+'/cancel',json={'version':s['version']+10}).status_code==409
    first=client.post('/api/media/sessions/'+s['id']+'/cancel',json={'version':s['version']});assert first.status_code==200 and first.json()['status']=='cancelled'
    second=client.post('/api/media/sessions/'+s['id']+'/cancel',json={'version':s['version']});assert second.status_code==200 and second.json()['version']==first.json()['version']
    process_once(s['job_id'],deliver=False);assert session(client,s['id'])['evidence_id'] is None

def test_gateway_edit_invalidates_grants_and_old_capability(client,media_runtime):
    install=ready(client);g=gateway(client);ch=channel(client,g);s,d=finish(client,capture(client,ch,install).json());assert d['status']=='succeeded'
    ch=client.get('/api/media/channels').json()[0]
    assert client.post('/api/media/channels/'+ch['id']+'/register',json={'version':ch['version']}).status_code==200
    r=client.put('/api/media/gateways/'+g['id'],json={k:g[k] for k in ['name','api_origin','rtsp_origin','version']});assert r.status_code==200
    ch=client.get('/api/media/channels').json()[0];assert ch['status']=='disabled' and not ch['read_authorized'] and not ch['validation_current']
    assert capture(client,ch,install).status_code==409
    assert client.post('/api/media/channels/'+ch['id']+'/register',json={'version':ch['version']}).status_code==409
    assert client.get('/api/videos/'+d['id']+'/original').content==RAW

def test_mid_capture_revoke_blocks_late_results(client,media_runtime,monkeypatch):
    install=ready(client);ch=channel(client);s=capture(client,ch,install).json()
    real=media.child
    def revoke(script,request,temp,guard,receive):
        assert client.post('/api/media/channels/'+ch['id']+'/disable',json={'version':ch['version']}).status_code==200
        real(script,request,temp,guard,receive)
    monkeypatch.setattr(media,'child',revoke);process_once(s['job_id'],deliver=False)
    d=session(client,s['id']);assert d['status']=='failed' and d['evidence_id'] is None and d['video_run_id'] is None

def test_video_processing_rechecks_channel_grant(client,media_runtime):
    install=ready(client);ch=channel(client);s=capture(client,ch,install).json();process_once(s['job_id'],deliver=False)
    captured=session(client,s['id']);run=client.get('/api/videos/'+captured['video_run_id']).json()
    assert client.post('/api/media/channels/'+ch['id']+'/disable',json={'version':ch['version']}).status_code==200
    process_once(run['job_id'],deliver=False);d=client.get('/api/videos/'+run['id']).json();assert d['status']=='failed' and not d['frames']
    assert client.get('/api/videos/'+run['id']+'/original').content==RAW

def test_source_change_preserves_partial_original_but_cannot_validate(client,media_runtime,monkeypatch):
    install=ready(client);ch=channel(client);s=capture(client,ch,install).json();original=media.inventory;calls=[]
    def changed(g):
        result=original(g);calls.append(1)
        if len(calls)>1:result['channels'][0]['source_id']='different-source'
        return result
    monkeypatch.setattr(media,'inventory',changed);process_once(s['job_id'],deliver=False);s=session(client,s['id']);assert s['status']=='partial' and s['evidence_id']
    d=client.get('/api/videos/'+s['video_run_id']).json();process_once(d['job_id'],deliver=False)
    ch=client.get('/api/media/channels').json()[0];assert not ch['validation_current']
    assert client.post('/api/media/channels/'+ch['id']+'/register',json={'version':ch['version']}).status_code==409

def test_interrupted_window_is_not_reported_as_continuous(client,media_runtime):
    install=ready(client);ch=channel(client);s=capture(client,ch,install).json()
    with Session(engine()) as db:
        row=db.get(StreamSession,s['id']);row.status='capturing';row.data={**row.data,'started_at':'prior-attempt','progress':{'seconds':2.}};db.commit()
    after,d=finish(client,s);assert after['attempt_history'][0]['state']=='interrupted_unsaved_window' and d['status']=='succeeded'

def test_truncated_mux_cannot_pass_channel_validation(client,media_runtime,monkeypatch):
    install=ready(client);ch=channel(client);s=capture(client,ch,install).json();process_once(s['job_id'],deliver=False)
    saved=session(client,s['id']);run=client.get('/api/videos/'+saved['video_run_id']).json();real=local_video.execute_video
    def shortened(content,backend,configuration,checkpoints,guard,receive,lock_fd=None):
        def recv(value,temp):receive({**value,'duration_seconds':1.5} if value['kind']=='complete' else value,temp)
        real(content,backend,configuration,checkpoints,guard,recv,lock_fd)
    monkeypatch.setattr(local_video,'execute_video',shortened);process_once(run['job_id'],deliver=False)
    d=client.get('/api/videos/'+run['id']).json();assert d['status']=='failed' and '时长' in d['error'] and d['frames']
    assert not client.get('/api/media/channels').json()[0]['validation_current']
    assert client.get('/api/videos/'+run['id']+'/original').content==RAW

def test_model_changed_during_capture_still_keeps_original(client,media_runtime,monkeypatch):
    install=ready(client);ch=channel(client);s=capture(client,ch,install).json();real=media.child
    def changed(script,request,temp,guard,receive):
        with Session(engine()) as db:
            row=db.get(SpecialistInstallation,install['id']);row.data={**row.data,'generation':row.data['generation']+1};db.commit()
        real(script,request,temp,guard,receive)
    monkeypatch.setattr(media,'child',changed);process_once(s['job_id'],deliver=False)
    s=session(client,s['id']);assert s['status']=='captured' and s['evidence_id']
    run=client.get('/api/videos/'+s['video_run_id']).json();process_once(run['job_id'],deliver=False)
    d=client.get('/api/videos/'+run['id']).json();assert d['status']=='failed' and not d['frames']
    assert client.get('/api/videos/'+d['id']+'/original').content==RAW

def test_failed_reader_saves_diagnostic_without_creating_fake_clip(client,media_runtime,monkeypatch):
    install=ready(client);ch=channel(client);s=capture(client,ch,install).json()
    monkeypatch.setattr(media,'child',lambda script,request,temp,guard,receive:receive({'kind':'capture.failed','reason':'未读取到可保存的视频','error_code':'rtsp_timeout'},temp))
    process_once(s['job_id'],deliver=False);s=session(client,s['id'])
    assert s['status']=='failed' and s['failure_code']=='rtsp_timeout' and s['evidence_id'] is None and s['video_run_id'] is None

def test_operator_cannot_add_gateway_and_foreign_workspace_denied(client,media_runtime,account):
    g=gateway(client)
    assert client.get('/api/media/gateways',headers={'X-Workspace-ID':'foreign'}).status_code==403
    with Session(engine()) as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));member.role='operator';db.commit()
    assert client.post('/api/media/gateways/'+g['id']+'/discover',json={'version':g['version']}).status_code==403

@pytest.mark.parametrize('bad',[{'partial':False,'duration_seconds':1},{'duration_seconds':float('nan')},{'source_clock_utc':'invented'},{'codec':'hevc'},{'file':'../secret'},{'packets':True}])
def test_invalid_capture_receipts_are_rejected(bad):
    with pytest.raises(ValueError):media.validate_completion({**receipt(),**bad},4)
