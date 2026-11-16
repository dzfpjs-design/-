import hashlib
import io
import json
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import local_video as video
from generic_agents.models import VideoRun, VideoFrame, VideoAlert, Event, Memory, Job, CloudCall, Membership, User, now
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_local_specialists import mocked_runtime, ready

RAW=b'\x00\x00\x00\x18ftypmp42'+b'\0'*40
CFG={'sample_hz':2,'threshold':.5,'presence_rule':{'label_id':15,'seconds':1}}


def frame(index):
    f=io.BytesIO();Image.new('RGB',(20,20),(index*20,0,0)).save(f,format='JPEG')
    raw=f.getvalue();d={'bbox':[1.,1.,10.,10.],'score':.9,'label':'cat','label_id':15}
    return raw,{'kind':'frame','sample_index':index,'file':f'frame-{index:04d}.jpg',
        'frame_sha256':hashlib.sha256(raw).hexdigest(),'width':20,'height':20,'pts':index*500,'time_base':'1/1000',
        'seconds':index*.5,'segment':1,'gap_before':False,'detections':[d],
        'tracks':[{**d,'track_ref':'1:1'}],'events':[{'event_key':'presence:1:1','track_ref':'1:1','label':'cat',
            'from_seconds':.5,'to_seconds':1.5,'kind':'presence_candidate'}] if index==3 else [],'inference_ms':10}


def simulate(content,backend,configuration,checkpoints,guard,receive,lock_fd=None):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);receive({'kind':'decoded','samples':4},root)
        for i in range(4):
            guard();raw,value=frame(i);(root/value['file']).write_bytes(raw);receive(value,root)
        receive({'kind':'complete','schema':'local-video-v1','backend':backend,'cloud_calls':0,'sample_count':4,
            'decoded_frames':10,'duration_seconds':1.5,'width':20,'height':20,'tracks':[],
            'gap_intervals':[],'audio_processed':False},root)


@pytest.fixture
def video_runtime(mocked_runtime,monkeypatch):
    monkeypatch.setattr(video,'execute_video',simulate)


def upload(client,installation,configuration=CFG,raw=RAW):
    return client.post('/api/videos',data={'installation_id':installation['id'],'configuration':json.dumps(configuration)},
                       files={'file':('视频.mp4',raw,'video/mp4')})


def test_video_data_business_evidence_review_and_no_cloud(client,video_runtime):
    installation=ready(client);r=upload(client,installation);assert r.status_code==201,r.text
    run=r.json();assert process_once(run['job_id'],deliver=False)
    d=client.get('/api/videos/'+run['id']).json()
    assert d['status']=='succeeded' and len(d['frames'])==4
    assert d['delivery']=={'data_received':4,'business_received':1,'pending':0}
    assert all(f['utc'] is None and f['not_authoritative'] for f in d['frames'])
    original=client.get('/api/videos/'+run['id']+'/original');assert original.content==RAW
    assert original.headers['x-content-sha256']==hashlib.sha256(RAW).hexdigest()
    assert client.get('/api/videos/'+run['id']+'/original?workspace_id=foreign').status_code==403
    alert=d['alerts'][0]
    saved_note=client.post(f'/api/videos/{run["id"]}/alerts/{alert["id"]}/review',json={'version':alert['version'],'decision':'draft','note':'暂存核查说明，稍后继续复核'})
    assert saved_note.status_code==200 and saved_note.json()['status']=='pending'
    alert=saved_note.json();assert client.get('/api/videos/'+run['id']).json()['alerts'][0]['review_draft']=='暂存核查说明，稍后继续复核'
    r=client.post(f'/api/videos/{run["id"]}/alerts/{alert["id"]}/review',json={'version':alert['version'],'decision':'confirmed','note':'对照视频确认猫持续出现'})
    assert r.status_code==200 and r.json()['status']=='confirmed'
    assert client.get('/api/videos/'+run['id']).json()['alerts'][0]['review']['note']=='对照视频确认猫持续出现'
    assert client.post(f'/api/videos/{run["id"]}/alerts/{alert["id"]}/review',json={'version':alert['version'],'decision':'dismissed','note':'再次核对后排除'}).status_code==409
    with Session(engine()) as db:
        assert len(db.scalars(select(Memory)).all())==4
        assert not db.scalars(select(CloudCall)).all()
    assert client.post('/api/evidence/'+run['evidence_id']+'/authorize',json={'model_profile_id':'any','expected_version':1,'reason':'testing video consent blocking'}).status_code==409


def test_duplicate_upload_reuses_exact_processing_request(client,video_runtime):
    installation=ready(client);first=upload(client,installation).json();second=upload(client,installation).json()
    assert first['id']==second['id']
    with Session(engine()) as db:assert len(db.scalars(select(VideoRun)).all())==1


def test_cancel_and_retry_preserve_input_and_old_record(client,video_runtime):
    installation=ready(client);run=upload(client,installation).json()
    assert client.post('/api/videos/'+run['id']+'/cancel',json={'version':run['version']}).status_code==200
    process_once(run['job_id'],deliver=False)
    old=client.get('/api/videos/'+run['id']).json();assert old['status']=='cancelled' and not old['frames']
    new=client.post('/api/videos/'+run['id']+'/retry',json={'version':old['version']}).json()
    assert new['id']!=old['id'] and new['evidence_id']==old['evidence_id'] and new['retry_of']==old['id']
    process_once(new['job_id'],deliver=False);assert client.get('/api/videos/'+new['id']).json()['status']=='succeeded'

def test_video_stop_accepts_stale_progress_but_never_future_version(client,video_runtime):
    installation=ready(client);run=upload(client,installation).json()
    with Session(engine()) as db:
        row=db.get(VideoRun,run['id']);row.status='running';row.version+=4;db.commit()
    assert client.post('/api/videos/'+run['id']+'/cancel',json={'version':run['version']+20}).status_code==409
    first=client.post('/api/videos/'+run['id']+'/cancel',json={'version':run['version']});assert first.status_code==200
    second=client.post('/api/videos/'+run['id']+'/cancel',json={'version':run['version']});assert second.status_code==200 and first.json()==second.json()
    process_once(run['job_id'],deliver=False);assert client.get('/api/videos/'+run['id']).json()['status']=='cancelled'


def test_midrun_cancel_keeps_frames_and_blocks_late_results(client,video_runtime,monkeypatch):
    installation=ready(client);run=upload(client,installation).json()
    def cancelled(content,backend,configuration,checkpoints,guard,receive,lock_fd=None):
        def recv(value,temp):
            receive(value,temp)
            if value['kind']=='frame' and value['sample_index']==0:
                d=client.get('/api/videos/'+run['id']).json()
                assert client.post('/api/videos/'+run['id']+'/cancel',json={'version':d['version']}).status_code==200
        simulate(content,backend,configuration,checkpoints,guard,recv)
    monkeypatch.setattr(video,'execute_video',cancelled);process_once(run['job_id'],deliver=False)
    d=client.get('/api/videos/'+run['id']).json();assert d['status']=='cancelled' and len(d['frames'])==1 and not d['alerts']


def test_checkpoint_replay_keeps_identity_and_does_not_redeliver(client,video_runtime,monkeypatch,account):
    installation=ready(client);run=upload(client,installation).json()
    def interrupted(content,backend,configuration,checkpoints,guard,receive,lock_fd=None):
        def recv(value,temp):
            receive(value,temp)
            if value['kind']=='frame' and value['sample_index']==1:raise RuntimeError('injected worker interruption')
        simulate(content,backend,configuration,checkpoints,guard,recv)
    monkeypatch.setattr(video,'execute_video',interrupted)
    with Session(engine()) as db:
        user=db.scalar(select(User).where(User.username==account[0]));p=Principal(user.id,account[2],'admin','test')
        with pytest.raises(RuntimeError):video.process_video(db,p,db.get(Job,run['job_id']))
    def resumed(content,backend,configuration,checkpoints,guard,receive,lock_fd=None):
        assert len(checkpoints)==2;simulate(content,backend,configuration,checkpoints,guard,receive)
    monkeypatch.setattr(video,'execute_video',resumed);process_once(run['job_id'],deliver=False)
    d=client.get('/api/videos/'+run['id']).json()
    assert d['status']=='succeeded' and d['recovery_count']==1 and d['delivery']['data_received']==4
    with Session(engine()) as db:
        assert len(db.scalars(select(Memory)).all())==4 and len(db.scalars(select(VideoAlert)).all())==1


@pytest.mark.parametrize('configuration',[{'sample_hz':30},{'threshold':float('nan')},{'recorded_at':'2026-10-08T12:00:00'},{'presence_rule':{'label_id':99,'seconds':1}}])
def test_invalid_config_is_not_queued(client,video_runtime,configuration):
    installation=ready(client);assert upload(client,installation,configuration).status_code==422


def test_invalid_container_and_changed_pipeline_block_processing(client,video_runtime,monkeypatch):
    installation=ready(client);assert upload(client,installation,raw=b'not-mp4').status_code==415
    run=upload(client,installation).json();monkeypatch.setattr(video,'PIPELINE_HASH','changed')
    process_once(run['job_id'],deliver=False);d=client.get('/api/videos/'+run['id']).json()
    assert d['status']=='failed' and not d['frames'] and '版本' in d['error']


def test_frame_output_bounds_and_identity():
    raw,d=frame(0);video.validate_frame(d,raw,'cpu')
    bad={**d,'file':'../leak'}
    with pytest.raises(ValueError):video.validate_frame(bad,raw,'cpu')
    bad={**d,'tracks':[{**d['tracks'][0],'track_ref':'global-person'}]}
    with pytest.raises(ValueError):video.validate_frame(bad,raw,'cpu')
    bad={**d,'seconds':float('nan')}
    with pytest.raises(ValueError):video.validate_frame(bad,raw,'cpu')


def test_recorded_time_is_explicit_and_original_corruption_blocks(client,video_runtime):
    from generic_agents.config import settings
    from generic_agents.models import Evidence
    installation=ready(client);run=upload(client,installation,{**CFG,'recorded_at':'2026-10-08T10:00:00+08:00'}).json()
    process_once(run['job_id'],deliver=False);d=client.get('/api/videos/'+run['id']).json()
    assert d['frames'][0]['utc']=='2026-10-08T02:00:00+00:00'
    assert all(f['utc_basis']=='user_supplied_unverified' for f in d['frames'])
    other=upload(client,installation,{**CFG,'threshold':.6}).json()
    with Session(engine()) as db:
        ev=db.get(Evidence,other['evidence_id']);(settings().storage_dir/ev.storage_key).write_bytes(b'corrupt')
    process_once(other['job_id'],deliver=False)
    failed=client.get('/api/videos/'+other['id']).json()
    assert failed['status']=='failed' and not failed['frames'] and '校验失败' in failed['error']


def test_viewer_cannot_upload_cancel_or_review_and_foreign_run_is_hidden(client,video_runtime,account):
    installation=ready(client);run=upload(client,installation).json();process_once(run['job_id'],deliver=False)
    d=client.get('/api/videos/'+run['id']).json();alert=d['alerts'][0]
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2]));membership.role='viewer';db.commit()
    assert upload(client,installation).status_code==403
    assert client.post('/api/videos/'+run['id']+'/cancel',json={'version':d['version']}).status_code==403
    assert client.post(f'/api/videos/{run["id"]}/alerts/{alert["id"]}/review',json={'version':alert['version'],'decision':'confirmed','note':'不具备权限的判断'}).status_code==403
    assert client.get('/api/videos/not-in-workspace').status_code==404


def test_frame_path_traversal_is_rejected(client,video_runtime,monkeypatch):
    installation=ready(client);run=upload(client,installation).json()
    def wrong(content,backend,configuration,checkpoints,guard,receive,lock_fd=None):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);raw,value=frame(0);value['file']='../frame-0000.jpg';receive(value,root)
    monkeypatch.setattr(video,'execute_video',wrong);process_once(run['job_id'],deliver=False)
    d=client.get('/api/videos/'+run['id']).json();assert d['status']=='failed' and not d['frames']
