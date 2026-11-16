"""Real PostgreSQL controls and records; mocked codec is not model/runtime proof."""
from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import continuous_media as continuous,local_video
from generic_agents.db import engine
from generic_agents.models import ContinuousStream,StreamSession,Job,CloudCall,SpecialistInstallation
from generic_agents.worker import process_once
from tests.test_media import media_runtime,receipt,capture,session
from tests.test_media_monitor import prepared
from tests.test_local_specialists import mocked_runtime
from tests.test_local_video import RAW,CFG

def start(client,ch,install,**kw):
    return client.post('/api/media/channels/'+ch['id']+'/continuous',json={
        'channel_version':ch['version'],'installation_id':install['id'],'seconds':4,
        'max_segments':3,'runtime_minutes':5,'configuration':CFG,'allow_continuous_reads':True,**kw})

def current(client,identity):return next(x for x in client.get('/api/media/continuous').json() if x['id']==identity)

def codec(monkeypatch,after=None,discontinuity=False):
    def run(script,request,temp,guard,receive,timeout_seconds=300):
        assert script=='continuous_capture_runner.py' and timeout_seconds>0
        for index in range(request['segments']):
            guard();receive({'kind':'segment.open','index':index},temp)
            first=index*360000+(9000 if discontinuity and index else 0)
            value={**receipt(4),'kind':'segment.complete','index':index,'file':f'segment-{index:04d}.mp4',
                'first_source_pts':first,'last_source_pts':first+360000,'boundary_frame_overlap':index>0}
            (temp/value['file']).write_bytes(RAW);receive(value,temp)
            if after:after(index)
        receive({'kind':'stream.complete','segments':request['segments']},temp)
    monkeypatch.setattr(local_video,'child',run)

def execute(row):assert process_once(row['job_id'],deliver=False)

def test_one_connection_three_originals_analysis_independent(client,media_runtime,monkeypatch):
    ch,install=prepared(client);r=start(client,ch,install);assert r.status_code==201,r.text;row=r.json();seen=[]
    def after(index):
        m=current(client,row['id']);assert m['status']=='recording';assert m['saved_segments']==index+1
        with Session(engine()) as db:
            s=db.get(StreamSession,m['session_ids'][-1]);job=db.scalar(select(Job).where(Job.kind=='process_video',Job.data['run_id'].as_string()==s.video_run_id))
            # Jobs have a separate durable queue while this recording job remains active.
            assert s.video_run_id and job and job.status=='queued';seen.append(s.video_run_id)
    codec(monkeypatch,after);execute(row);final=current(client,row['id'])
    assert final['status']=='exhausted' and final['attempts']==3 and final['saved_segments']==3 and final['connections']==1
    clips=[session(client,x) for x in final['session_ids']]
    assert len({s['connection_id'] for s in clips})==1
    assert all(s['receipt']['source_clock_utc']=='unknown' for s in clips)
    for a,b in zip(clips,clips[1:]):assert a['receipt']['last_source_pts']==b['receipt']['first_source_pts']
    with Session(engine()) as db:
        assert not db.scalars(select(CloudCall)).all()
        jobs=db.scalars(select(Job).where(Job.kind=='process_video',Job.status=='queued')).all();ids=[x.id for x in jobs]
    for identity in ids:assert process_once(identity,deliver=False)
    assert all(session(client,x)['processing_status']=='succeeded' for x in final['session_ids'])

def test_pause_during_next_window_preserves_original_then_resume_budget(client,media_runtime,monkeypatch):
    ch,install=prepared(client);row=start(client,ch,install).json()
    def after(index):
        if index==0:
            r=client.post('/api/media/continuous/'+row['id']+'/pause',json={'version':1});assert r.status_code==200,r.text
    codec(monkeypatch,after);execute(row);m=current(client,row['id'])
    assert m['status']=='paused' and m['saved_segments']==1 and m['attempts']==1
    saved=session(client,m['session_ids'][0]);assert saved['video_run_id']
    resumed=client.post('/api/media/continuous/'+row['id']+'/resume',json={'version':m['version']});assert resumed.status_code==200,resumed.text
    assert resumed.json()['deadline']==m['deadline'] and resumed.json()['attempts']==1
    assert not process_once(row['job_id'],deliver=False) # completed old generation cannot run again
    codec(monkeypatch);execute(resumed.json());final=current(client,row['id']);assert final['saved_segments']==3 and final['attempts']==3
    assert session(client,final['session_ids'][1])['connection_id']!=saved['connection_id']

def test_authorization_and_model_change_do_not_auto_reconnect(client,media_runtime,monkeypatch):
    ch,install=prepared(client);row=start(client,ch,install).json();codec(monkeypatch)
    with Session(engine()) as db:
        x=db.get(SpecialistInstallation,install['id']);x.data={**x.data,'generation':x.data['generation']+1};db.commit()
    execute(row);m=current(client,row['id']);assert m['status']=='blocked' and m['attempts']==0 and m['connections']==1
    r=client.post('/api/media/continuous/'+row['id']+'/resume',json={'version':m['version']});assert r.status_code==409
    assert current(client,row['id'])['status']=='blocked'

def test_no_manual_or_periodic_duplicate_source(client,media_runtime):
    ch,install=prepared(client);row=start(client,ch,install).json()
    assert capture(client,ch,install).status_code==409
    from tests.test_media_monitor import start as monitor
    assert monitor(client,ch,install).status_code==409
    assert start(client,ch,install).status_code==409
    m=client.post('/api/media/continuous/'+row['id']+'/stop',json={'version':1}).json();assert m['status']=='stopped'
    assert capture(client,ch,install).status_code==201

def test_network_error_bounded_reconnect_and_budget(client,media_runtime,monkeypatch):
    ch,install=prepared(client);row=start(client,ch,install,max_segments=10).json()
    def offline(script,request,temp,guard,receive,timeout_seconds=300):
        guard();receive({'kind':'segment.open','index':0},temp);receive({'kind':'stream.failed'},temp)
    monkeypatch.setattr(local_video,'child',offline)
    for index in range(4):
        execute(row);m=current(client,row['id']);assert m['attempts']==index+1 and m['saved_segments']==0
        if index<3:
            assert m['status']=='reconnecting'
            with Session(engine()) as db:
                x=db.get(ContinuousStream,row['id']);x.data={**x.data,'reconnect_at':(continuous.clock(db)-timedelta(seconds=1)).isoformat()};db.commit()
            continuous.schedule_once();row=current(client,row['id'])
        else:assert m['status']=='blocked' and m['connections']==4

def test_bad_segment_boundary_never_claims_continuity(client,media_runtime,monkeypatch):
    ch,install=prepared(client);row=start(client,ch,install).json();codec(monkeypatch,discontinuity=True);execute(row)
    m=current(client,row['id']);assert m['saved_segments']==1 and m['attempts']==2
    assert session(client,m['session_ids'][1])['evidence_id'] is None

def test_cancel_current_window_blocks_continuous_not_saved_analysis(client,media_runtime,monkeypatch):
    ch,install=prepared(client);row=start(client,ch,install).json()
    def stop(script,request,temp,guard,receive,timeout_seconds=300):
        receive({'kind':'segment.open','index':0},temp);m=current(client,row['id']);s=session(client,m['current_session_id'])
        r=client.post('/api/media/sessions/'+s['id']+'/cancel',json={'version':s['version']});assert r.status_code==200
        guard()
    monkeypatch.setattr(local_video,'child',stop);execute(row)
    m=current(client,row['id']);assert m['status']=='blocked' and m['attempts']==1 and m['saved_segments']==0

def test_recovered_unfinished_window_consumes_budget_not_replayed(client,media_runtime,monkeypatch):
    ch,install=prepared(client);row=start(client,ch,install).json()
    with Session(engine()) as db:
        x=db.get(ContinuousStream,row['id']);from generic_agents.models import MediaChannel,MediaGateway
        c=db.get(MediaChannel,ch['id']);g=db.get(MediaGateway,c.gateway_id);i=db.get(SpecialistInstallation,install['id'])
        from generic_agents.worker import actor
        p=actor(db,x.workspace_id,x.actor_id);x.status='recording';db.flush()
        from generic_agents.video_read_boundary import reserve
        reservation=reserve(db,p,c,g,'automatic',300,'continuous:'+x.id+':fixture-interruption','continuous',x.id,x.data['generation'])
        s=continuous.queue_capture(db,p,c,g,i,4,CFG,create_job=False,reservation_id=reservation.id);s.status='capturing'
        x.data={**x.data,'attempts':1,'current_session_id':s.id,'session_ids':[s.id],
            'read_reservation_id':reservation.id,'read_reservation_ids':[reservation.id]};old=s.id;db.commit()
    codec(monkeypatch);execute(row);m=current(client,row['id'])
    assert m['interruptions']==1 and m['attempts']==3 and m['saved_segments']==2
    assert session(client,old)['status']=='failed' and session(client,old)['evidence_id'] is None

def test_original_model_job_and_controller_receipt_commit_atomically(client,media_runtime,monkeypatch):
    from generic_agents import media
    from generic_agents.leases import LeaseLost
    ch,install=prepared(client);row=start(client,ch,install).json();codec(monkeypatch)
    original=media.save_capture
    def saved_then_crash(*args,**kwargs):original(*args,**kwargs);raise LeaseLost('模拟原件提交后进程丢失')
    monkeypatch.setattr(media,'save_capture',saved_then_crash);execute(row)
    m=current(client,row['id']);assert m['attempts']==1 and m['saved_segments']==1 and m['current_session_id'] is None
    assert session(client,m['session_ids'][0])['video_run_id']
    with Session(engine()) as db:
        job=db.get(Job,row['job_id']);job.lease_until=continuous.clock(db)-timedelta(seconds=1);db.commit()
    monkeypatch.setattr(media,'save_capture',original);execute(row);m=current(client,row['id'])
    assert m['saved_segments']==3 and m['attempts']==3 and len(set(m['session_ids']))==3

def test_pause_resume_after_original_commit_cannot_restore_old_generation(client,media_runtime,monkeypatch):
    from generic_agents import media
    ch,install=prepared(client);row=start(client,ch,install).json();codec(monkeypatch)
    original=media.save_capture;resumed=[]
    def save_and_resume(*args,**kwargs):
        original(*args,**kwargs)
        if not resumed:
            m=current(client,row['id']);r=client.post('/api/media/continuous/'+m['id']+'/pause',json={'version':m['version']});assert r.status_code==200
            m=r.json();r=client.post('/api/media/continuous/'+m['id']+'/resume',json={'version':m['version']});assert r.status_code==200,r.text
            resumed.append(r.json())
    monkeypatch.setattr(media,'save_capture',save_and_resume);execute(row)
    m=current(client,row['id']);assert m['generation']==2 and m['saved_segments']==1 and m['status']=='queued'
    execute(resumed[0]);m=current(client,row['id']);assert m['generation']==2 and m['saved_segments']==3

def test_revoked_actor_and_expired_deadline_no_reads(client,media_runtime,monkeypatch):
    from generic_agents.models import Membership
    ch,install=prepared(client);row=start(client,ch,install).json();codec(monkeypatch)
    with Session(engine()) as db:
        m=db.scalar(select(Membership));m.role='viewer';db.commit()
    execute(row)
    with Session(engine()) as db:
        x=db.get(ContinuousStream,row['id']);assert x.status=='blocked' and x.data['attempts']==0
        db.scalar(select(Membership)).role='admin';x.status='paused'
        x.data={**x.data,'deadline':(continuous.clock(db)-timedelta(seconds=1)).isoformat()};db.commit()
    m=current(client,row['id']);r=client.post('/api/media/continuous/'+row['id']+'/resume',json={'version':m['version']})
    assert r.status_code==409 and current(client,row['id'])['attempts']==0

def test_history_and_counts_survive_global_recent_clip_limit(client,media_runtime,monkeypatch):
    ch,install=prepared(client);row=start(client,ch,install).json();codec(monkeypatch);execute(row)
    with Session(engine()) as db:
        target=db.get(ContinuousStream,row['id'])
        db.add_all([StreamSession(workspace_id=target.workspace_id,channel_id=ch['id'],status='cancelled',data={'seconds':4}) for _ in range(55)])
        jobs=db.scalars(select(Job).where(Job.kind=='process_video',Job.status=='queued')).all();ids=[x.id for x in jobs];db.commit()
    for identity in ids:process_once(identity,deliver=False)
    m=current(client,row['id']);assert m['analysis_completed']==3 and m['analysis_pending']==0
    assert not set(m['session_ids']) & {x['id'] for x in client.get('/api/media/sessions').json()}
    r=client.get('/api/media/continuous/'+m['id']+'/sessions');assert r.status_code==200,r.text
    assert [x['id'] for x in r.json()]==m['session_ids'] and all(x['processing_status']=='succeeded' for x in r.json())
