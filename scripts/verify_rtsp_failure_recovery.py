"""Actual read cancellation, offline failure, explicit reconnect and restart proof."""
import hashlib
import argparse
import json
import time
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
import media_runtime as node
import local_runtime as runtime
from verify_rtsp_runtime import ACCESS,BASELINE,OUT,ROOT,call
from generic_agents.db import engine
from generic_agents.models import CloudCall
from generic_agents.security import Principal
from generic_agents.gateway import invoke

def login(c):
    for _ in range(100):
        try:c.get('/health').raise_for_status();break
        except httpx.HTTPError:time.sleep(.1)
    else:raise AssertionError('API unavailable')
    r=c.post('/api/login',json={k:ACCESS[k] for k in ['username','password']});r.raise_for_status()
    c.headers.update({'X-Workspace-ID':ACCESS['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']})
    return r.json()

def selected(c):
    g=next(x for x in call(c,'GET','/media/gateways') if x['name']=='浏览器视频网关验收')
    ch=next(x for x in call(c,'GET','/media/channels') if x['gateway_id']==g['id'])
    installation=next(x for x in call(c,'GET','/specialists/installations') if x['status']=='ready')
    return g,ch,installation

def read(c,ch,installation,seconds):
    return call(c,'POST','/media/channels/'+ch['id']+'/capture',{'channel_version':ch['version'],'installation_id':installation['id'],'seconds':seconds,
        'configuration':{'sample_hz':1,'threshold':.5,'presence_rule':{'label_id':15,'seconds':1}}})

def saved(c,identity):return next(x for x in call(c,'GET','/media/sessions') if x['id']==identity)

def wait(c,identity,predicate,timeout=90):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        row=saved(c,identity)
        if predicate(row):return row
        time.sleep(.2)
    raise AssertionError('actual media status deadline exceeded')

def snapshot(c):
    _,ch,_=selected(c);sessions=[s for s in call(c,'GET','/media/sessions') if s['channel_id']==ch['id']]
    details=[call(c,'GET','/videos/'+s['video_run_id']) for s in sessions if s['video_run_id']]
    for d in details:
        r=c.get('/api/videos/'+d['id']+'/original');r.raise_for_status();assert hashlib.sha256(r.content).hexdigest()==d['input_sha256']
    return {'channel':ch,'sessions':sessions,'videos':details,'draft':call(c,'GET','/setup/draft')}

def run(finish_existing=False):
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as c:
        auth=login(c);g,ch,installation=selected(c)
        if finish_existing:
            records=[x for x in call(c,'GET','/media/sessions') if x['channel_id']==ch['id']]
            fresh=records[0];missing=records[1];old=records[2]
            assert fresh['status']=='captured' and fresh['processing_status']=='succeeded' and missing['status']=='failed' and old['status']=='cancelled'
            assert old['progress']['seconds']>0 and old['evidence_id'] is None and old['video_run_id'] is None
            assert fresh['capture_hash']==old['capture_hash']==missing['capture_hash']
            progress={'version':old['version']-1};old={**old,'version':1}
        else:
            return perform_and_finish(c,g,ch,installation)
        complete(c,g,ch,old,progress,missing,fresh,True)


def perform_and_finish(c,g,ch,installation):
    old=read(c,ch,installation,12);progress=wait(c,old['id'],lambda x:x['status']=='capturing' and x.get('progress',{}).get('seconds',0)>0,30)
    stopped=call(c,'POST','/media/sessions/'+old['id']+'/cancel',{'version':old['version']})
    assert progress['version']>old['version'] and stopped['status']=='cancelled'
    time.sleep(.6);stopped=saved(c,old['id']);assert stopped['evidence_id'] is None and stopped['video_run_id'] is None
    print('actual stale-progress cancellation saved',flush=True)
    node.stop('publisher','cats-synthetic.mp4')
    try:
        offline=call(c,'POST','/media/gateways/'+g['id']+'/discover',{'version':g['version']})
        assert offline['status']=='ready' and not offline['inventory']['channels'][0]['ready']
        ch=selected(c)[1];missing=read(c,ch,installation,6);missing=wait(c,missing['id'],lambda x:x['status']=='failed',30)
        assert missing['evidence_id'] is None and missing['video_run_id'] is None
        print('actual offline source failed without fake video',flush=True)
    finally:node.publisher()
    for _ in range(100):
        r=c.get('http://127.0.0.1:9997/v3/paths/list');r.raise_for_status()
        if r.json()['items'][0]['ready']:break
        time.sleep(.1)
    else:raise AssertionError('publisher reconnect failed')
    g,ch,installation=selected(c);g=call(c,'POST','/media/gateways/'+g['id']+'/discover',{'version':g['version']})
    fresh=read(c,ch,installation,6);fresh=wait(c,fresh['id'],lambda x:x['processing_status'] in {'succeeded','failed'},90)
    assert fresh['status']=='captured' and fresh['processing_status']=='succeeded',fresh.get('receipt')
    complete(c,g,ch,old,progress,missing,fresh,False)


def complete(c,g,ch,old,progress,missing,fresh,repair):
    ch=selected(c)[1];ch=call(c,'POST','/media/channels/'+ch['id']+'/register',{'version':ch['version']})
    with Session(engine()) as db:
        from generic_agents.models import Membership
        member=db.scalar(select(Membership).where(Membership.workspace_id==ACCESS['workspace_id']))
        p=Principal(member.user_id,ACCESS['workspace_id'],'admin','actual-protocol-proof')
        invocation=invoke(db,p,ch['capability_id'],'perception',{},'rtsp-archive-proof:'+fresh['id'])
        assert invocation.status=='succeeded',invocation.data.get('error')
        result=invocation.data['output'];assert result['not_authoritative'] and any(s['session_id']==fresh['id'] for s in result['sessions'])
        db.commit()
    before=snapshot(c)
    runtime.stop('setup-worker','setup-worker');runtime.stop('api','serve');runtime.start('api','serve');runtime.start('setup-worker','setup-worker')
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as after_client:
        login(after_client);after=snapshot(after_client)
    assert before==after
    with Session(engine()) as db:
        cloud=db.scalars(select(CloudCall).where(CloudCall.workspace_id==ACCESS['workspace_id'])).all()
        assert sorted(x.id for x in cloud)==sorted(BASELINE['cloud_ids'])
    report={'gateway_id':g['id'],'channel_id':ch['id'],'cancelled_session_id':old['id'],'cancel_version_sent':old['version'],'progress_version_before_stop':progress['version'],
        'offline_session_id':missing['id'],'offline_error':missing['error'],'recovered_session_id':fresh['id'],'video_run_id':fresh['video_run_id'],
        'capability_id':ch['capability_id'],'captured_duration_seconds':fresh['receipt']['duration_seconds'],'source_clock_utc':'unknown',
        'registered_gateway_invocation_verified':True,'normal_restart_equal':True,'sessions_preserved':len(after['sessions']),'new_cloud_calls':0,
        'capture_hash':fresh['capture_hash'],'verified_at_unix':time.time(),'completed_existing_failure_exercise':repair,
        'progress_version_reconstructed_from_persisted_stop':repair,'source_kind':'actual RTSP synthetic publisher; no field-device or model quality proof'}
    (OUT/'failure-recovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (OUT/'browser-selection.json').write_text(json.dumps({'gateway_id':g['id'],'channel_id':ch['id'],'session_id':fresh['id'],'video_run_id':fresh['video_run_id']}))
    print(json.dumps({k:report[k] for k in ['registered_gateway_invocation_verified','normal_restart_equal','sessions_preserved','new_cloud_calls']},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--finish-existing',action='store_true');run(parser.parse_args().finish_existing)
