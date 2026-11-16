"""Actual offline video proof, worker kill/lease recovery and normal restart.

Uses only the named synthetic validation workspace and public-image derived clips.
No cloud model call, no physical camera, no device control.
"""
import hashlib
import json
import os
import signal
import time
from contextlib import contextmanager
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
import local_runtime as runtime
from generic_agents.db import engine
from generic_agents.local_video import PIPELINE_HASH
from generic_agents.models import CloudCall, Job, Memory, Event

ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
OUT=ROOT/'docs/evidence/local-video-v06';OUT.mkdir(parents=True,exist_ok=True)
STATUS=ROOT/'.runtime/video-validation/status.json'
ACCESS=json.loads((ROOT/'.runtime/understanding-validation-access.json').read_text())
BASELINE=json.loads((ROOT/'.runtime/setup-recovery-baseline.json').read_text())


def status(stage,**data):
    STATUS.write_text(json.dumps({'stage':stage,'at':time.time(),**data}));print(stage,flush=True)


@contextmanager
def client():
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as c:
        for _ in range(100):
            try:
                health=c.get('/health');health.raise_for_status();break
            except httpx.HTTPError:time.sleep(.1)
        else:raise AssertionError('API startup did not become healthy')
        r=c.post('/api/login',json={k:ACCESS[k] for k in ['username','password']});r.raise_for_status()
        c.headers.update({'X-Workspace-ID':ACCESS['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']})
        yield c


def get(c,path):
    r=c.get(path);r.raise_for_status();return r.json()


def snapshot(c,run_id):
    d=get(c,'/api/videos/'+run_id)
    r=c.get('/api/videos/'+run_id+'/original');r.raise_for_status()
    assert hashlib.sha256(r.content).hexdigest()==d['input_sha256']
    for f in d['frames']:
        raw=c.get('/api/evidence/'+f['evidence_id']+'/content');raw.raise_for_status()
        assert hashlib.sha256(raw.content).hexdigest()==f['frame_sha256']
    return d


# Keep API and worker on the same pipeline code before starting the fault exercise.
runtime.stop('setup-worker','setup-worker');runtime.stop('api','serve')
runtime.start('api','serve');runtime.start('setup-worker','setup-worker')
with client() as c:
    assert get(c,'/api/videos/catalog')['pipeline_hash']==PIPELINE_HASH
    installation=next(r for r in get(c,'/api/specialists/installations') if r['status']=='ready')
    with Session(engine()) as db:
        assert not db.scalars(select(Job).where(Job.kind.in_(['parse_setup_material','understand_setup','install_specialist','run_specialist','process_video']),Job.status.in_(['queued','running']))).all()
    raw=(ROOT/'.runtime/video-validation/cats-synthetic.mp4').read_bytes()
    # Fixed distinct 1Hz configuration makes the harness rerun idempotently.
    cfg={'sample_hz':1,'threshold':.5,'presence_rule':{'label_id':15,'seconds':1}}
    r=c.post('/api/videos',data={'installation_id':installation['id'],'configuration':json.dumps(cfg)},files={'file':('视频恢复合成验收.mp4',raw,'video/mp4')});r.raise_for_status();run=r.json();run_id=run['id']
    killed=False;cut=[];deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        d=get(c,'/api/videos/'+run_id)
        if d['status']=='succeeded':break
        if d['status']=='failed':raise AssertionError(d['error'])
        if d['saved_samples']>=2:
            with Session(engine()) as db:
                job=db.get(Job,run['job_id']);assert job.status=='running'
            cut=[(f['id'],f['receipt_hash']) for f in d['frames']]
            pid=int((ROOT/'.runtime/setup-worker.pid').read_text());assert runtime.owned(pid,'setup-worker')
            (OUT/'interruption-checkpoints.json').write_text(json.dumps({'run_id':run_id,'checkpoints':cut,'pid':pid}))
            os.kill(pid,signal.SIGKILL);killed=True
            status('worker_killed',run_id=run_id,saved_samples=len(cut),pid=pid)
            runtime.start('setup-worker','setup-worker');break
        time.sleep(.05)
    if not killed and d['status']!='succeeded':raise AssertionError('worker did not enter measurable processing')
    deadline=time.monotonic()+260
    while time.monotonic()<deadline:
        d=get(c,'/api/videos/'+run_id)
        if d['status'] in {'failed','cancelled'}:raise AssertionError(d['error'])
        if d['status']=='succeeded':break
        with Session(engine()) as db:
            job=db.get(Job,run['job_id']);assert job.status in {'queued','running'}
            assert runtime.owned(int((ROOT/'.runtime/setup-worker.pid').read_text()),'setup-worker')
        status('waiting_live_lease_recovery',run_id=run_id,saved_samples=d['saved_samples'],attempts=job.attempts)
        time.sleep(10)
    else:raise AssertionError('video lease recovery deadline exceeded')
    before=snapshot(c,run_id)
    assert before['status']=='succeeded' and before['saved_samples']==6
    assert before['delivery']=={'data_received':6,'business_received':2,'pending':0}
    for identity,receipt in cut:assert any(f['id']==identity and f['receipt_hash']==receipt for f in before['frames'])
    assert sum(x['label']=='cat' for x in before['frames'][-1]['tracks'])==2
    if killed:assert before['recovery_count']>=1
    alert=before['alerts'][0]
    r=c.post(f'/api/videos/{run_id}/alerts/{alert["id"]}/review',json={'version':alert['version'],'decision':'draft','note':'合成视频核查说明已暂存；等待继续核查。'});r.raise_for_status()
    before=snapshot(c,run_id)
    with Session(engine()) as db:
        job=db.get(Job,run['job_id'])
        recovery={'kill_performed_this_verifier_run':killed,'checkpoint_ids_compared_this_run':len(cut),'attempts':job.attempts,'lease_recoveries':job.data.get('lease_recoveries',0),'checkpoint_rebuild_count':before.get('recovery_count',0),'status':job.status}
        assert len(db.scalars(select(Memory).where(Memory.workspace_id==ACCESS['workspace_id'],Memory.data['run_id'].as_string()==run_id)).all())==6
status('normal_restart',run_id=run_id)
runtime.stop('setup-worker','setup-worker');runtime.stop('api','serve');runtime.start('api','serve');runtime.start('setup-worker','setup-worker')
with client() as c:after=snapshot(c,run_id)
assert before==after
with Session(engine()) as db:
    calls=db.scalars(select(CloudCall).where(CloudCall.workspace_id==ACCESS['workspace_id'])).all()
    assert sorted(x.id for x in calls)==sorted(BASELINE['cloud_ids'])
report={'pipeline_hash':PIPELINE_HASH,'source':'synthetic MP4 from public COCO still; not field video or an accuracy benchmark',
        'decoder':'PyAV isolated from Supervision/OpenCV process','run_id':run_id,'deployment':'local','new_cloud_calls':0,
        'recovery':recovery,'normal_restart_equal':before==after,'frames':len(after['frames']),'delivery':after['delivery'],
        'draft_note_survived_restart':after['alerts'][0]['review_draft'],'cloud_call_count':len(calls)}
(OUT/'runtime.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
status('complete',**report)
