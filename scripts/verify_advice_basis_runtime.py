"""One existing public QA advice; no new account, media, device or authority."""
import json,sys
from pathlib import Path
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents import models as m
from generic_agents.db import engine
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_signal_advice_runtime import state
OUT=ROOT/'docs/evidence/advice-basis-v06';OUT.mkdir(parents=True,exist_ok=True)
def read(n):return json.loads((OUT/(n+'.json')).read_text())
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def prepare(a,c):
    if (OUT/'prepared.json').exists():raise RuntimeError('已有检查点，不重复准备')
    p=json.loads((ROOT/'docs/evidence/registered-advice-v06/prepared.json').read_text())
    before=state(a['workspace_id']);save('before',before)
    with Session(engine()) as db:
        p['old_advice_ids']=list(db.scalars(select(m.SignalAdvice.id).where(m.SignalAdvice.workspace_id==a['workspace_id'])))
        p['old_cloud_call_ids']=list(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id==a['workspace_id'])))
    r=c.post('/signal-advice/preview',json={k:p[k] for k in ['instance_id','signal_id','model_profile_id']});r.raise_for_status();preview=r.json();audit=preview['cloud_input']['basis_audit']
    assert audit['original_task']['completed'] and audit['saved_business_review']['decision']=='不属于业务异常'
    assert any(v['relation']=='same_originals' for v in audit['comparisons'])
    assert any(v['relation']=='different_originals' for v in audit['comparisons'])
    assert all(not v['direct_comparison_supported'] for v in audit['comparisons'])
    save('prepared',p);save('preview',preview)
    print('Existing public QA and local basis checked; no cloud inference requested')
def verify(a,c,phase):
    p=read('prepared');r=c.get('/signal-advice',params={'instance_id':p['instance_id'],'signal_id':p['signal_id']});r.raise_for_status()
    fresh=[v for v in r.json() if v['id'] not in p['old_advice_ids']];assert len(fresh)==1
    advice=fresh[0];assert advice['status'] in {'needs_confirmation','cancelled','applied'},advice['status']
    assert advice['request']['rule_version']=='signal-business-advice-v4'
    if advice['status']=='applied':assert advice['dispatch']['route']=='original_task' and advice['dispatch']['task_id']==p['task_id']
    else:assert not advice.get('dispatch')
    audit=advice['request']['cloud_input']['basis_audit'];assert audit==read('preview')['cloud_input']['basis_audit']
    r=c.get('/signal-advice/'+advice['id']+'/export');r.raise_for_status();assert r.json()==advice
    before=read('before');after=state(a['workspace_id'])
    for k in before:assert all(after[k].get(i)==h for i,h in before[k].items()),'old record changed '+k
    assert after['own_originals']==before['own_originals'] and after['other_originals']==before['other_originals']
    processing=('Task:','GeoRun:','GeoPair:','AudioRun:','SpeechActivityRun:','SegmentedAudioRun:','VideoRun:')
    assert not any(k.startswith(processing) for k in set(after['own'])-set(before['own']))
    paid=read('failed-paid-response')
    assert advice['local_revalidation']['status']=='passed' and advice['local_revalidation']['cloud_calls']==0
    assert advice['local_revalidation']['previous_error']==paid['error'] and advice['model_receipt']==paid['model_receipt']
    with Session(engine()) as db:
        stored=db.get(m.SignalAdvice,advice['id']);job=db.get(m.Job,paid['job_id'])
        assert stored.data['raw_response']==paid['raw_response'] and job.status=='failed'
        calls=list(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id==a['workspace_id'])))
        assert len(set(calls)-set(p['old_cloud_call_ids']))==1
    if phase=='restart':assert after==read('before-restart')
    save(phase+'-advice',advice);save(phase+'-verification',{'passed':True,'old_own_unchanged':len(before['own']),
        'own_records':len(after['own']),'other_records_unchanged':len(after['other']),
        'own_originals_unchanged':len(after['own_originals']),'other_originals_unchanged':len(after['other_originals']),
        'new_cloud_calls':1,'new_specialist_inference':0,'device_actions':0,'entity_state_updated':False,
        'dispatch':advice.get('dispatch'),'original_failed_job_preserved':True,'original_paid_response_preserved':True,
        'free_local_revalidation':True,'new_tasks':len([k for k in after['own'] if k.startswith('Task:') and k not in before['own']]),'model_receipt':advice['model_receipt'],'representative_domain_quality_validated':False,'production_ready':False})
    if phase=='before-restart':save('before-restart',after)
    print(json.dumps(read(phase+'-verification'),ensure_ascii=False))
def main():
    a=json.loads((ROOT/'.runtime/wms-access.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=35) as c:
        r=c.post('/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']})
        if sys.argv[1]=='prepare':prepare(a,c)
        else:verify(a,c,sys.argv[1])
if __name__=='__main__':main()
