"""Durable checkpoints for synthetic RTSP faults; no device or cloud actions."""
import argparse,json,time,os,signal,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job,CloudCall
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/local-continuous-v06'
def save(name,value):
    path=OUT/(name+'.json');temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));temp.replace(path)
def run(phase):
    access=json.loads((ROOT/'.runtime/understanding-validation-access.json').read_text())
    selection=json.loads((OUT/'selection.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as c:
        for _ in range(100):
            try:c.get('/health').raise_for_status();break
            except httpx.HTTPError:time.sleep(.1)
        else:raise AssertionError('API health timeout')
        r=c.post('/api/login',json={k:access[k] for k in ['username','password']});r.raise_for_status()
        c.headers.update({'X-Workspace-ID':access['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']})
        def call(method,path,body=None):
            r=c.request(method,'/api'+path,json=body);r.raise_for_status();return r.json()
        def current(identity):return next(x for x in call('GET','/media/continuous') if x['id']==identity)
        def clips(identity):return call('GET','/media/continuous/'+identity+'/sessions')
        def wait(identity,condition,seconds=100):
            until=time.monotonic()+seconds;old=None
            while time.monotonic()<until:
                row=current(identity);parts=clips(identity);state=(row['status'],row['attempts'],row['saved_segments'],row['analysis_completed'])
                if state!=old:print(json.dumps({'phase':phase,'progress':state}),flush=True);old=state
                if condition(row,parts):return row,parts
                time.sleep(.3)
            raise AssertionError('verification timeout; saved checkpoint retained')
        def start(name,limit=5,seconds=6):
            path=OUT/(name+'-start.json')
            if path.exists():return json.loads(path.read_text())
            ch=next(x for x in call('GET','/media/channels') if x['id']==selection['channel_id'])
            row=call('POST','/media/channels/'+ch['id']+'/continuous',{'channel_version':ch['version'],
                'installation_id':selection['installation_id'],'seconds':seconds,'max_segments':limit,'runtime_minutes':10,
                'configuration':{'sample_hz':1,'threshold':.5,'presence_rule':{'label_id':15,'seconds':1}},'allow_continuous_reads':True})
            save(name+'-start',row);return row
        def media(action):
            subprocess.run([sys.executable,str(ROOT/'scripts/media_runtime.py'),action],check=True,cwd=ROOT,capture_output=True)
        if phase=='pause-start':
            row=start('pause',10,8);wait(row['id'],lambda m,s:m['saved_segments']==1 and m['attempts']==2)
            print('第二个窗口正在接收，可从界面暂停');return
        if phase=='pause-check':
            row=json.loads((OUT/'pause-start.json').read_text());row=current(row['id']);parts=clips(row['id'])
            assert row['status']=='paused' and 1<=row['saved_segments']<10 and row['attempts']==row['saved_segments']+1
            assert parts[0]['evidence_id'] and parts[-1]['status']=='cancelled' and parts[-1]['evidence_id'] is None
            save('pause-before-resume',{'stream':row,'sessions':parts});print('暂停保存与未完成窗口已核对');return
        if phase=='pause-finish':
            initial=json.loads((OUT/'pause-before-resume.json').read_text());row,parts=wait(initial['stream']['id'],
                lambda m,s:m['status']=='exhausted' and m['analysis_pending']==0)
            assert row['attempts']==10 and row['saved_segments']==9 and row['analysis_completed']==9
            assert row['deadline']==initial['stream']['deadline'] and parts[0]['evidence_id']==initial['sessions'][0]['evidence_id']
            assert parts[len(initial['sessions'])]['connection_id']!=parts[0]['connection_id']
            save('pause-after-resume',{'stream':row,'sessions':parts});print('恢复沿用原预算；9原件、1缺失窗口保留');return
        if phase in ['offline','offline-check']:
            row=start('offline',6,6)
            if phase=='offline-check':
                row=current(row['id']);parts=clips(row['id'])
            else:
                row,parts=wait(row['id'],lambda m,s:m['saved_segments']==1 and m['attempts']==2)
                save('offline-before',{'stream':row,'sessions':parts})
                try:
                    media('publisher-stop')
                    row,parts=wait(row['id'],lambda m,s:m['status']=='reconnecting',30)
                    save('offline-interrupted',{'stream':row,'sessions':parts})
                finally:media('publisher-start')
            row,parts=wait(row['id'],lambda m,s:m['status']=='exhausted' and m['analysis_pending']==0,120)
            assert 2<=row['connections']<=4 and row['saved_segments']>=4
            assert any(x['status'] in ['partial','cancelled'] for x in parts)
            details=[call('GET','/videos/'+x['video_run_id']) for x in parts if x['video_run_id']]
            new=next(x for x in details if x['source_context']['connection_id']!=parts[0]['connection_id'])
            assert new['source_context']['continuous_with_previous'] is False
            save('offline-after',{'stream':row,'sessions':parts,'first_reconnected_video':new});print('实际断流、保存原件、有限重连与跨连接不连续已验证');return
        if phase=='crash':
            row=start('crash',4,8);row,parts=wait(row['id'],lambda m,s:m['saved_segments']==1 and m['attempts']==2)
            save('crash-before',{'stream':row,'sessions':parts})
            pid=int((ROOT/'.runtime/media-worker.pid').read_text())
            command=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout
            assert str(ROOT) in command and 'generic_agents.cli media-worker' in command
            os.kill(pid,signal.SIGKILL)
            subprocess.run([sys.executable,str(ROOT/'scripts/local_runtime.py'),'start','--mode','setup'],check=True,cwd=ROOT,capture_output=True)
            with Session(engine()) as db:
                job=db.get(Job,row['job_id']);save('crash-wait',{'stream_id':row['id'],'job_id':job.id,'lease_until':job.lease_until.isoformat(),
                    'lease_recoveries':job.data.get('lease_recoveries',0),'killed_at':time.time()})
            print('只中断项目录制处理器并重启；等待原180秒租约自然过期，无改短或强制释放');return
        if phase=='crash-check':
            before=json.loads((OUT/'crash-before.json').read_text())
            row,parts=wait(before['stream']['id'],lambda m,s:m['status']=='exhausted' and m['analysis_pending']==0,250)
            assert row['attempts']==4 and row['saved_segments']==3 and row['interruptions']==1
            assert parts[0]['evidence_id']==before['sessions'][0]['evidence_id']
            assert parts[1]['status']=='failed' and parts[1]['evidence_id'] is None
            with Session(engine()) as db:
                waited=json.loads((OUT/'crash-wait.json').read_text());job=db.get(Job,waited['job_id']);assert job.data['lease_recoveries']>=1
                assert datetime.fromisoformat(job.data['finished_at'])>=datetime.fromisoformat(waited['lease_until'])
                save('crash-after',{'stream':row,'sessions':parts,'job':{'id':job.id,'status':job.status,'lease_recoveries':job.data['lease_recoveries']}})
            print('原租约自然恢复：3原件完成本地分析、1中断窗口如实保留');return
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['pause-start','pause-check','pause-finish','offline','offline-check','crash','crash-check']);run(p.parse_args().phase)
