"""Existing public QA, one confirmed local workflow; no new credentials or devices."""
import json,sys
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
import httpx
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_signal_advice_runtime import state
OUT=ROOT/'docs/evidence/registered-advice-v06';OUT.mkdir(parents=True,exist_ok=True)
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def read(n):return json.loads((OUT/(n+'.json')).read_text())
def prepare(a,c):
    if (OUT/'prepared.json').exists():raise RuntimeError('已有检查点，不重复准备')
    prior=json.loads((ROOT/'docs/evidence/signal-advice-v06/prepared.json').read_text())
    snapshot=state(a['workspace_id']);save('before',snapshot)
    response=c.post('/signal-advice/preview',json={k:prior[k] for k in ['instance_id','signal_id','model_profile_id']});response.raise_for_status();preview=response.json()
    assert preview['cloud_input']['workflow_choices'] and any(r['id']=='original-task' and 'workflow_completed' in r['text'] for r in preview['cloud_input']['references'])
    with Session(engine()) as db:
        original=db.get(m.Task,prior['task_id']);signal=db.get(m.PerceptionSignal,prior['signal_id'])
        prior.update({'original_task_hash':digest(original.data),'original_signal_hash':digest(signal.data),
            'old_advice_ids':list(db.scalars(select(m.SignalAdvice.id).where(m.SignalAdvice.workspace_id==a['workspace_id']))),
            'old_geo_run_ids':list(db.scalars(select(m.GeoRun.id).where(m.GeoRun.workspace_id==a['workspace_id']))),
            'old_cloud_call_ids':list(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id==a['workspace_id'])))})
    save('prepared',prior);print('Existing workflow and completed original progress checked; no inference/cloud call made')
def verify(a,c,phase):
    prepared=read('prepared');response=c.get('/signal-advice',params={'instance_id':prepared['instance_id'],'signal_id':prepared['signal_id']});response.raise_for_status()
    fresh=[r for r in response.json() if r['id'] not in prepared['old_advice_ids']];assert len(fresh)==1
    advice=fresh[0];assert advice['status']=='applied' and advice['dispatch']['route']=='business_workflow'
    response=c.get('/workspace/tasks/'+advice['dispatch']['task_id']);response.raise_for_status();task=response.json()
    assert task['status']=='workflow_completed' and task['business_advice']['advice_id']==advice['id']
    assert task['workflow_runtime']['receipts'][-1]['note']=='已核对新的本地候选与固定原图；仅为公开合成地图工程结果，不改变原分析结论或现场状态。'
    with Session(engine()) as db:
        assert digest(db.get(m.Task,prepared['task_id']).data)==prepared['original_task_hash']
        assert digest(db.get(m.PerceptionSignal,prepared['signal_id']).data)==prepared['original_signal_hash']
        runs=list(db.scalars(select(m.GeoRun).where(m.GeoRun.workspace_id==a['workspace_id'])))
        new=[r for r in runs if r.id not in prepared['old_geo_run_ids']];assert len(new)==1 and new[0].status=='succeeded'
        calls=list(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id==a['workspace_id'])));assert len(set(calls)-set(prepared['old_cloud_call_ids']))==1
    response=c.get('/signal-advice/'+advice['id']+'/export');response.raise_for_status();assert response.json()==advice
    before=read('before');after=state(a['workspace_id'])
    changed=[i for i,h in before['own'].items() if after['own'].get(i)!=h]
    assert len(changed)==1 and changed[0].startswith('GeoDaily:'),changed
    # Daily is a live aggregate. Its immutable historical candidate memories
    # must survive, and every prior source record must remain byte-equivalent.
    with Session(engine()) as db:
        daily=db.get(m.GeoDaily,changed[0].split(':',1)[1])
        from generic_agents.geo_daily import checked_daily
        from types import SimpleNamespace
        checked_daily(db,SimpleNamespace(workspace_id=a['workspace_id']),daily.id)
        historical=[v for v in db.scalars(select(m.Memory).where(m.Memory.workspace_id==a['workspace_id']))
                    if v.data.get('daily_id')==daily.id and 'Memory:'+v.id in before['own']]
        assert historical
        latest=max(historical,key=lambda v:v.created_at)
        prior={v['key']:v for v in latest.data['records']};now={v['key']:v for v in daily.data['records']}
        assert all(now.get(k)==v for k,v in prior.items()),'old daily sources changed'
        assert set(now)-set(prior)=={'segmentation:'+new[0].id}
        daily_proof={'id':daily.id,'old_sources_preserved':len(prior),'new_sources':1,
                     'immutable_old_memories_preserved':len(historical),'current_sources':len(now),
                     'unique_originals':daily.data['unique_originals'],'description':'每日聚合新增独立处理回执；原来源及历史候选经验保留'}
    for k in ['other','own_originals','other_originals']:assert all(after[k].get(i)==h for i,h in before[k].items()),'old record changed '+k
    if phase=='restart':assert after==read('before-restart')
    save(phase+'-advice',advice);save(phase+'-task',task)
    result={'passed':True,'old_own_unchanged':len(before['own'])-len(changed),'daily_update':daily_proof,'own_records':len(after['own']),'other_records_unchanged':len(after['other']),
        'old_originals_unchanged':True,'original_task_unchanged':True,'original_signal_review_unchanged':True,
        'new_local_geo_runs':1,'new_cloud_calls':1,'model_receipt':advice['model_receipt'],'entity_state_updated':False,
        'device_actions':0,'production_ready':False,'representative_domain_quality_validated':False}
    save(phase+'-verification',result)
    if phase=='before-restart':save('before-restart',after)
    print(json.dumps(result,ensure_ascii=False))
def main():
    a=json.loads((ROOT/'.runtime/wms-access.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=40) as c:
        response=c.post('/login',json={k:a[k] for k in ['username','password']});response.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':response.json()['csrf_token']})
        if sys.argv[1]=='prepare':prepare(a,c)
        else:verify(a,c,sys.argv[1])
if __name__=='__main__':main()
