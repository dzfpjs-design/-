"""Actual MediaMTX protocol proof; synthetic public-image publisher, no cloud."""
import hashlib
import argparse
import json
import time
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import CloudCall

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/local-rtsp-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=json.loads((ROOT/'.runtime/understanding-validation-access.json').read_text())
BASELINE=json.loads((ROOT/'.runtime/setup-recovery-baseline.json').read_text())
STATE=OUT/'selection.json'

def call(c,method,path,body=None):
    response=c.request(method,'/api'+path,json=body);response.raise_for_status();return response.json()

def run(new_window=False):
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as c:
        for _ in range(100):
            try:c.get('/health').raise_for_status();break
            except httpx.HTTPError:time.sleep(.1)
        else:raise AssertionError('API did not become healthy')
        response=c.post('/api/login',json={k:ACCESS[k] for k in ['username','password']});response.raise_for_status()
        c.headers.update({'X-Workspace-ID':ACCESS['workspace_id'],'X-CSRF-Token':response.json()['csrf_token']})
        installation=next(x for x in call(c,'GET','/specialists/installations') if x['status']=='ready')
        if STATE.exists():
            state=json.loads(STATE.read_text())
            if new_window:
                old=next(x for x in call(c,'GET','/media/sessions') if x['id']==state['session_id'])
                (OUT/('prior-'+old['id']+'.json')).write_text(json.dumps(old,ensure_ascii=False,indent=2))
                channel=next(x for x in call(c,'GET','/media/channels') if x['id']==state['channel_id'])
                session=call(c,'POST','/media/channels/'+channel['id']+'/capture',{'channel_version':channel['version'],'installation_id':installation['id'],'seconds':6,'configuration':{'sample_hz':1,'threshold':.5,'presence_rule':{'label_id':15,'seconds':1}}})
                state['session_id']=session['id'];STATE.write_text(json.dumps(state))
        else:
            gateway=call(c,'POST','/media/gateways',{'name':'本机媒体网关验证','api_origin':'http://127.0.0.1:9997','rtsp_origin':'rtsp://127.0.0.1:8554'})
            gateway=call(c,'POST','/media/gateways/'+gateway['id']+'/discover',{'version':gateway['version']})
            assert gateway['status']=='ready' and gateway['inventory']['channels'][0]['ready']
            channel=call(c,'POST','/media/channels',{'gateway_id':gateway['id'],'gateway_version':gateway['version'],'path':'validation','name':'公开图片合成视频通道','read_authorized':True})
            session=call(c,'POST','/media/channels/'+channel['id']+'/capture',{'channel_version':channel['version'],'installation_id':installation['id'],'seconds':6,'configuration':{'sample_hz':1,'threshold':.5,'presence_rule':{'label_id':15,'seconds':1}}})
            state={'gateway_id':gateway['id'],'channel_id':channel['id'],'session_id':session['id']};STATE.write_text(json.dumps(state))
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            session=next(x for x in call(c,'GET','/media/sessions') if x['id']==state['session_id'])
            print(json.dumps({'capture':session['status'],'processing':session['processing_status'],'progress_seconds':session.get('progress',{}).get('seconds')},ensure_ascii=False),flush=True)
            if session['status'] in {'failed','cancelled'}:raise AssertionError(session.get('error'))
            if session['processing_status']=='failed':raise AssertionError(call(c,'GET','/videos/'+session['video_run_id'])['error'])
            if session['processing_status']=='succeeded':break
            time.sleep(3)
        else:raise AssertionError('bounded RTSP validation deadline exceeded')
        detail=call(c,'GET','/videos/'+session['video_run_id'])
        original=c.get('/api/videos/'+detail['id']+'/original');original.raise_for_status()
        assert hashlib.sha256(original.content).hexdigest()==detail['input_sha256']==session['input_sha256']
        assert detail['frames'] and detail['delivery']['data_received']==len(detail['frames']) and detail['delivery']['pending']==0
        assert all(x['source_context']['session_id']==session['id'] and x['utc'] is None for x in detail['frames'])
        channel=next(x for x in call(c,'GET','/media/channels') if x['id']==state['channel_id'])
        assert channel['status']=='validated' and not session['receipt']['partial']
        if not channel.get('capability_id'):channel=call(c,'POST','/media/channels/'+channel['id']+'/register',{'version':channel['version']})
    with Session(engine()) as db:
        calls=db.scalars(select(CloudCall).where(CloudCall.workspace_id==ACCESS['workspace_id'])).all()
        assert sorted(x.id for x in calls)==sorted(BASELINE['cloud_ids'])
    report={**state,'video_run_id':detail['id'],'capability_id':channel['capability_id'],'receipt':session['receipt'],
        'frames':len(detail['frames']),'delivery':detail['delivery'],'original_bytes':len(original.content),'original_sha256':detail['input_sha256'],
        'pipeline_hash':detail['pipeline_hash'],'capture_hash':session['capture_hash'],'source_kind':'actual RTSP/TCP synthetic publisher; no physical camera or quality claim',
        'new_cloud_calls':0,'cloud_call_count':len(calls),'verified_at_unix':time.time()}
    (OUT/'runtime.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:report[k] for k in ['frames','delivery','original_bytes','new_cloud_calls']},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--new-window',action='store_true');run(parser.parse_args().new_window)
