"""Existing public QA only; durable local refresh, no devices or cloud requests."""
import json,sys
from pathlib import Path
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_signal_advice_runtime import state
OUT=ROOT/'docs/evidence/automatic-state-v06';OUT.mkdir(parents=True,exist_ok=True)
def read(n):return json.loads((OUT/(n+'.json')).read_text())
def save(n,value):(OUT/(n+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def prepare(a,c):
    if (OUT/'prepared.json').exists():raise RuntimeError('已有准备检查点，不重复准备')
    p=json.loads((ROOT/'docs/evidence/advice-basis-v06/prepared.json').read_text())
    before=state(a['workspace_id']);save('before',before)
    response=c.get('/workspace/instances/'+p['instance_id']+'/reported-observation-options');response.raise_for_status();options=response.json()
    save('prepared',{'instance_id':p['instance_id'],'entity_id':p['entity_id'],'options':options,'old_cloud_calls':[i for i in before['own'] if i.startswith('CloudCall:')],
        'old_observation_ids':[i.split(':')[1] for i in before['own'] if i.startswith('Observation:')],'ordinary_device_worker':'off','new_accounts':0,'new_permissions':0})
    print('Prepared existing public QA; sources',len(options['sources']),'rules',len(options['rules']))
def verify(a,c,phase):
    p=read('prepared');before=read('before');after=state(a['workspace_id'])
    assert before['other']==after['other'];assert before['own_originals']==after['own_originals'] and before['other_originals']==after['other_originals']
    with Session(engine()) as db:
        fresh=list(db.scalars(select(m.Observation).where(m.Observation.workspace_id==a['workspace_id'],m.Observation.id.not_in(p['old_observation_ids']))));assert len(fresh)==1
        row=fresh[0];assert row.entity_id==p['entity_id'] and row.status=='processed';assert row.data['extracted']['attributes']=={'公开登记值':38.5}
        jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==a['workspace_id'],m.Job.kind=='refresh_object_state',m.Job.data['state_request']['trigger_observation_id'].as_string()==row.id)))
        assert len(jobs)==1 and jobs[0].status=='succeeded'
        saved=db.scalar(select(m.ObjectState).where(m.ObjectState.workspace_id==a['workspace_id'],m.ObjectState.entity_id==p['entity_id']))
        assert saved.data['fields']['公开登记值']['value']==38.5 and not saved.data['current_state_verified']
        assert jobs[0].data['state_result']['state_hash']==saved.data['state_hash']
        entity=db.get(m.Entity,p['entity_id']);assert after['own']['Entity:'+entity.id]==before['own']['Entity:'+entity.id]
        permitted={'ObjectState:'+saved.id}
        changed={k for k,h in before['own'].items() if after['own'].get(k)!=h}
        assert changed<=permitted,changed
        call=db.get(m.Invocation,row.data['invocation_id']);cap=db.get(m.Capability,call.capability_id)
        assert call.status=='succeeded' and cap.data['execution']['adapter']=='builtin' and cap.data['execution']['handler']=='numeric_rules'
        assert not row.data.get('model_receipt')
        fresh_jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==a['workspace_id'])))
        assert all(j.kind in {'process_reported_observation','refresh_object_state'} for j in fresh_jobs if j.kind not in {'login_limit','cloud_budget'} and 'Job:'+j.id not in before['own'])
        assert not [i for i in after['own'] if i.startswith('Task:') and i not in before['own']]
        assert set(p['old_cloud_calls'])=={i for i in after['own'] if i.startswith('CloudCall:')}
        proof={'passed':True,'observation_id':row.id,'background_job_id':jobs[0].id,'state_hash':saved.data['state_hash'],'state_version':saved.version,
            'automatically_saved_before_state_page':True,'new_observations':1,'new_cloud_calls':0,'new_model_inferences':0,'device_actions':0,'entity_updated':False,
            'old_own_records_unchanged':len(before['own'])-len(changed),'changed_old_records':sorted(changed),'other_records_unchanged':len(after['other']),
            'own_originals_unchanged':len(after['own_originals']),'other_originals_unchanged':len(after['other_originals']),'physical_state_verified':False,'production_ready':False}
    r=c.get('/workspace/instances/'+p['instance_id']+'/objects/'+p['entity_id']+'/state');r.raise_for_status();view=r.json()
    assert view['state']['state_hash']==proof['state_hash'];save(phase+'-state-view',view);save(phase+'-verification',proof)
    if phase=='before-restart':save('before-restart',after)
    else:assert after==read('before-restart')
    print(json.dumps(proof,ensure_ascii=False))
def main():
    a=json.loads((ROOT/'.runtime/wms-access.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=35) as c:
        r=c.post('/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']})
        phase=sys.argv[1]
        if phase=='prepare':prepare(a,c)
        else:verify(a,c,phase)
if __name__=='__main__':main()
