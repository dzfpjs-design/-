"""Quarantine one pinned public checkpoint; never load pickle or enable a model."""
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path

import httpx

ROOT=Path(__file__).resolve().parents[1]/'.runtime'/'changer-candidate'
ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
REVISION='6b88e874f8dea7430a2ab1ed84b356c6a67128a0'
SHA='da3f569306dadd1fac5b64abc2a3d484571cd0ecf6410e1f152275cfe49a3618'
SIZE=136880383
NAME='ChangerEx_r18-512x512_40k_levircd.pth'
URL='https://hf-mirror.com/likyoo/Open-CD_Model_Zoo/resolve/'+REVISION+'/LEVIR-CD/'+NAME

def status(stage,**values):
    value={'stage':stage,'repository':'likyoo/Open-CD_Model_Zoo','revision':REVISION,
           'filename':NAME,'expected_sha256':SHA,'expected_bytes':SIZE,
           'download_source':'registered_mirror','checkpoint_executed':False,
           'enabled':False,'default_deployment':'local',**values}
    temp=ROOT/'status.tmp';temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.chmod(0o600)
    os.replace(temp,ROOT/'status.json')

def valid(path):
    if not path.is_file() or path.stat().st_size!=SIZE:return False
    h=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()==SHA

with (ROOT/'download.lock').open('a') as lock:
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('candidate download already live; inspect status instead of restarting')
    file=ROOT/NAME;part=ROOT/(NAME+'.part')
    try:
        if valid(file):status('quarantined_hash_verified',downloaded_bytes=SIZE);print('pinned checkpoint already verified; not executed');raise SystemExit(0)
        offset=part.stat().st_size if part.exists() else 0
        if offset>=SIZE:offset=0
        status('connecting',downloaded_bytes=offset)
        started=time.monotonic()
        with httpx.Client(timeout=httpx.Timeout(40,connect=10),follow_redirects=True,trust_env=False) as client:
            with client.stream('GET',URL,headers={'Range':f'bytes={offset}-'} if offset else {}) as response:
                response.raise_for_status()
                if offset and response.status_code==206:
                    if response.headers.get('content-range','').split('/')[0]!=f'bytes {offset}-{SIZE-1}':raise ValueError('range mismatch')
                else:offset=0
                if 'text/html' in response.headers.get('content-type',''):raise ValueError('HTML is not a checkpoint')
                with part.open('ab' if offset else 'wb') as target:
                    part.chmod(0o600);count=reported=offset
                    for chunk in response.iter_bytes(256*1024):
                        count+=len(chunk)
                        if count>SIZE or time.monotonic()-started>600:raise ValueError('fixed size or duration exceeded')
                        target.write(chunk)
                        if count-reported>=1024*1024 or count==SIZE:
                            status('downloading',downloaded_bytes=count);reported=count
                    target.flush();os.fsync(target.fileno())
        if not valid(part):raise ValueError('checkpoint checksum mismatch')
        os.replace(part,file);status('quarantined_hash_verified',downloaded_bytes=SIZE)
        print('pinned checkpoint SHA256 verified in local quarantine; not executed or enabled')
    except Exception as error:
        status('failed',failure_type=type(error).__name__,downloaded_bytes=part.stat().st_size if part.exists() else 0)
        print('candidate preparation failed: '+type(error).__name__+'; checkpoint remains disabled')
        raise SystemExit(1)
