"""Existing public QA record, normal browser actions; no device/model calls."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models as m
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_signal_advice_runtime import state
OUT=ROOT/'docs/evidence/workspace-need-v06';OUT.mkdir(parents=True,exist_ok=True)
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str)+'\n')
def read(n):return json.loads((OUT/(n+'.json')).read_text())
def prepare(a,c):
    if (OUT/'prepared.json').exists():raise RuntimeError('已有准备检查点，不重复创建或核查')
    instance='e607bc9c-9bc6-430f-a108-82a3c4b4f355';entity='df6e5c59-0751-4feb-bc94-3b5bf325ddb8';obs='98fcfc16-ff9b-4925-bbbc-e2b995a7102b'
    with Session(engine()) as db:
        row=db.get(m.Observation,obs);assert row.workspace_id==a['workspace_id'] and row.status=='processed'
        assert row.data['extracted']['attributes']=={'公开登记值':39.5} and not row.data.get('human_verification')
        age=(datetime.now(timezone.utc)-row.observed_at).total_seconds()/3600
        assert age<720
        hours=24 if age<24 else min(720,int(age)+24)
        expiry=[j.id for j in db.scalars(select(m.Job).where(m.Job.workspace_id==a['workspace_id'],m.Job.kind=='refresh_object_state',m.Job.status=='queued')) if j.data['state_request'].get('due_state_hash') and j.data['state_request']['entity_id']==entity]
        caches=[v.id for v in db.scalars(select(m.VectorCache).where(m.VectorCache.workspace_id==a['workspace_id'],m.VectorCache.status=='ready',m.VectorCache.data['entity_ids'].contains([entity])))]
        before_data=row.data;before_version=row.version
    response=c.get('/workspace/instances/'+instance+'/data-needs/options');response.raise_for_status();options=response.json()
    assert entity in [x['id'] for x in options['objects']]
    save('before',state(a['workspace_id']));save('prepared',{'instance_id':instance,'entity_id':entity,'observation_id':obs,
        'purpose':'核对公开工程登记39.5是否具备人工核查依据（非现场验证）','max_age_hours':hours,'release_id':options['release_id'],
        'business_id':options['businesses'][0]['id'],'original_data':before_data,'original_version':before_version,'previous_expiry_jobs':expiry,'previous_caches':caches})
    print('Existing public record checkpoint saved; no new users or model calls')
def verify(a,c,phase):
    p=read('prepared');before=read('before');after=state(a['workspace_id'])
    assert before['other']==after['other'] and before['own_originals']==after['own_originals'] and before['other_originals']==after['other_originals']
    base='/workspace/instances/'+p['instance_id']+'/data-needs'
    rows=c.get(base).json()['items'];rows=[r for r in rows if r['purpose']==p['purpose']];assert len(rows)==1
    need=rows[0];detail=c.get(base+'/'+need['id']);detail.raise_for_status();detail=detail.json()
    assert detail['status']=='resolved' and detail['business_received'] and detail['scope_valid'] and not detail['parent_goal_completed']
    assert detail['records'][0]['original_values']==detail['records'][0]['extracted_values']=={'公开登记值':39.5} and detail['records'][0]['review']=='confirmed'
    with Session(engine()) as db:
        row=db.get(m.Observation,p['observation_id']);raw={k:v for k,v in row.data.items() if k not in {'human_verification','verification_history','verification_history_hash'}}
        assert raw==p['original_data'] and row.version==p['original_version']+1
        assert len(row.data['verification_history'])==1 and digest(row.data['verification_history'])==row.data['verification_history_hash']
        query=db.get(m.DataRun,need['id']);ctx=query.data['workspace_need_context'];assert digest(ctx)==query.data['workspace_need_context_hash']
        gap=db.get(m.DataGap,query.data['data_gap_ids'][0]);task=db.get(m.Task,gap.data['business_task_id'])
        assert task.status=='coordination_completed' and task.data['trigger']=='data.gap_coordination' and task.data['data_gap_id']==gap.id
        assert gap.status=='resolved' and gap.data['business_received_at']
        saved=db.scalar(select(m.ObjectState).where(m.ObjectState.workspace_id==a['workspace_id'],m.ObjectState.entity_id==p['entity_id']))
        assert saved.data['fields']['公开登记值']['value']==39.5 and not saved.data['current_state_verified']
        permitted={'Observation:'+row.id,'ObjectState:'+saved.id}|{'Job:'+j for j in p['previous_expiry_jobs']}|{'VectorCache:'+v for v in p['previous_caches']}
        changed={k for k,v in before['own'].items() if after['own'].get(k)!=v};assert changed<=permitted,changed
        new=set(after['own'])-set(before['own']);assert not any(k.split(':')[0] in {'CloudCall','Invocation','SpecialistRun','Acquisition','Entity','Observation'} for k in new)
        new_tasks=[k for k in new if k.startswith('Task:')];assert new_tasks==['Task:'+task.id]
        new_jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==a['workspace_id'])))
        assert not [j.id for j in new_jobs if 'Job:'+j.id in new and j.kind in {'route_data_gap','recheck_data_gap','execute_acquisition'}]
        proof={'query_id':query.id,'gap_id':gap.id,'coordination_task_id':task.id,'status':'resolved','review_history_entries':1,
            'original_input_unchanged':True,'new_cloud_calls':0,'device_actions':0,'new_specialist_inferences':0,'new_entity_writes':0,
            'own_records':len(after['own']),'other_records_unchanged':len(after['other']),'old_own_records_unchanged':len(before['own'])-len(changed),
            'changed_old_records':sorted(changed),'own_originals_unchanged':len(after['own_originals']),'other_originals_unchanged':len(after['other_originals']),
            'physical_state_verified':False,'parent_goal_completed':False,'production_ready':False}
    export=c.get(base+'/'+need['id']+'/export');export.raise_for_status();assert export.json()['detail']['status']=='resolved'
    save(phase+'-detail',detail);save(phase+'-export',export.json());save(phase+'-verification',proof)
    if phase=='before-restart':save('before-restart',after)
    if phase=='after-restart':assert after==read('before-restart')
    print('Verified saved need, original input, human review, coordination and isolation:',phase)
def main():
    a=json.loads((ROOT/'.runtime/wms-access.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=35) as c:
        response=c.post('/login',json={k:a[k] for k in ['username','password']});response.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':response.json()['csrf_token']})
        if sys.argv[1]=='prepare':prepare(a,c)
        else:verify(a,c,sys.argv[1])
if __name__=='__main__':main()
