"""Existing public QA correction, normal UI writes only, no device or cloud."""
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
OUT=ROOT/'docs/evidence/input-correction-v06';OUT.mkdir(parents=True,exist_ok=True)
def read(n):return json.loads((OUT/(n+'.json')).read_text())
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def prepare(a,c):
    if (OUT/'prepared.json').exists():raise RuntimeError('已有检查点，不重复准备或写入')
    p=json.loads((ROOT/'docs/evidence/input-failure-v06/prepared.json').read_text())
    oid=json.loads((ROOT/'docs/evidence/input-failure-v06/submitted.json').read_text())['id']
    save('before',state(a['workspace_id']))
    with Session(engine()) as db:
        original=db.get(m.Observation,oid);assert original.workspace_id==a['workspace_id'] and original.status=='failed' and not original.data.get('correction_requests')
        failures=list(db.scalars(select(m.Event).where(m.Event.workspace_id==a['workspace_id'],m.Event.kind=='observation.failed',m.Event.data['observation_id'].as_string()==oid)))
        assert len(failures)==2 and all(e.status=='received' for e in failures)
        future=[j.id for j in db.scalars(select(m.Job).where(m.Job.workspace_id==a['workspace_id'],m.Job.kind=='refresh_object_state',m.Job.status=='queued')) if j.data['state_request'].get('due_state_hash') and j.data['state_request']['entity_id']==p['entity_id']]
        save('prepared',{'instance_id':p['instance_id'],'entity_id':p['entity_id'],'original_observation_id':oid,
            'original_data_hash':digest(original.data),'original_data':original.data,'original_version':original.version,'previous_expiry_jobs':future,'new_accounts':0,'new_permissions':0,'device_worker':'off'})
    print('Existing public failed input checkpoint saved; no write or model call')
def verify(a,c,phase):
    p=read('prepared');before=read('before');after=state(a['workspace_id'])
    assert before['other']==after['other'] and before['own_originals']==after['own_originals'] and before['other_originals']==after['other_originals']
    with Session(engine()) as db:
        original=db.get(m.Observation,p['original_observation_id']);assert original.status=='failed'
        raw={k:v for k,v in original.data.items() if not k.startswith('correction_')}
        assert raw==p['original_data'] and original.version==p['original_version']+1
        entries=original.data['correction_requests'];assert len(entries)==1 and digest(entries)==original.data['correction_requests_hash']
        from generic_agents.observation_corrections import linked,resolved
        from generic_agents.security import Principal
        principal=Principal(entries[0]['actor_id'],a['workspace_id'],'admin','correction-verifier')
        child=linked(db,principal,original,entries[0]);assert child.status=='processed' and child.data['extracted']['attributes']=={'公开登记值':39.5}
        resolution=resolved(db,principal,original);assert resolution and resolution['data_received'] and not resolution['physical_state_verified']
        saved=db.scalar(select(m.ObjectState).where(m.ObjectState.workspace_id==a['workspace_id'],m.ObjectState.entity_id==p['entity_id']))
        assert saved.data['fields']['公开登记值']['value']==39.5 and saved.data['status']=='observed' and not saved.data['input_issues']
        assert len(saved.data['corrected_inputs'])==1 and saved.data['corrected_inputs'][0]['original_observation_id']==original.id
        assert not saved.data['current_state_verified']
        failures=list(db.scalars(select(m.Event).where(m.Event.workspace_id==a['workspace_id'],m.Event.kind=='observation.failed',m.Event.data['observation_id'].as_string()==original.id)))
        assert sorted(e.data['attempt'] for e in failures)==[1,2] and all(e.status=='received' for e in failures)
        call=db.get(m.Invocation,child.data['invocation_id']);cap=db.get(m.Capability,call.capability_id)
        assert call.status=='succeeded' and cap.data['execution']['adapter']=='builtin' and cap.data['execution']['handler']=='numeric_rules' and cap.data['data_policy']['cloud_allowed'] is False
        permitted={'Observation:'+original.id,'ObjectState:'+saved.id}|{'Job:'+j for j in p['previous_expiry_jobs']}
        changed={k for k,v in before['own'].items() if after['own'].get(k)!=v};assert changed<=permitted,changed
        assert len([k for k in after['own'] if k.startswith('Observation:') and k not in before['own']])==1
        assert not [k for k in after['own'] if k.startswith(('CloudCall:','Task:','Entity:')) and k not in before['own']]
        jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==a['workspace_id'])))
        assert all(j.kind in {'refresh_object_state','process_reported_observation'} for j in jobs if j.kind not in {'login_limit','cloud_budget'} and 'Job:'+j.id not in before['own'])
        proof={'passed':True,'original_observation_id':original.id,'correction_observation_id':child.id,'original_data_unchanged':True,'original_failures_preserved':2,
            'state_version':saved.version,'state_hash':saved.data['state_hash'],'new_value':39.5,'new_cloud_calls':0,'device_actions':0,'new_specialist_inferences':0,'entity_updated':False,
            'own_records':len(after['own']),'other_records_unchanged':len(after['other']),'old_own_records_unchanged':len(before['own'])-len(changed),'changed_old_records':sorted(changed),
            'own_originals_unchanged':len(after['own_originals']),'other_originals_unchanged':len(after['other_originals']),'physical_state_verified':False,'production_ready':False}
    response=c.get('/workspace/instances/'+p['instance_id']+'/objects/'+p['entity_id']+'/state');response.raise_for_status();view=response.json();assert view['state']['state_hash']==proof['state_hash'];save(phase+'-state',view)
    response=c.get('/workspace/instances/'+p['instance_id']+'/objects/'+p['entity_id']+'/state-inputs/'+original.id,params={'expected_state_hash':proof['state_hash']});response.raise_for_status();detail=response.json()
    assert detail['original_attributes']=={'公开登记值':None} and len(detail['failure_history'])==2 and detail['correction_result']['data_received'] and not detail['retry_available'];save(phase+'-original',detail)
    if phase=='before-restart':save('before-restart',after)
    if phase=='after-restart':assert after==read('before-restart')
    save(phase+'-verification',proof);print(json.dumps(proof,ensure_ascii=False))
def main():
    a=json.loads((ROOT/'.runtime/wms-access.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=35) as c:
        response=c.post('/login',json={k:a[k] for k in ['username','password']});response.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':response.json()['csrf_token']})
        if sys.argv[1]=='prepare':prepare(a,c)
        else:verify(a,c,sys.argv[1])
if __name__=='__main__':main()
