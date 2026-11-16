"""Read-only proof around a browser-created original goal and one real local CPU model call."""
import json,sys
from datetime import datetime
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.verify_signal_advice_runtime import state
OUT=ROOT/'docs/evidence/goal-before-local-model-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=ROOT/'.runtime/model-workflow-access.json'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def read(name):return json.loads((OUT/(name+'.json')).read_text())
def prepare(a,c):
    assert not (OUT/'prepared.json').exists(),'Continue existing checkpoint; never duplicate preparation'
    instances=c.get('/instances').raise_for_status().json();assert len(instances)==1
    i=instances[0];assert i['status']=='partially_active'
    releases=c.get('/instances/'+i['id']+'/releases').raise_for_status().json();r=next(x for x in releases if x['id']==i['active_release_id'])
    b=next(x for x in r['snapshot']['businesses'] if x['id'] in i['enabled_business_ids'] and x['flow']['steps'][0].get('execution',{}).get('adapter')=='local_specialist')
    objects=c.get('/instances/'+i['id']+'/objects').raise_for_status().json();assert len(objects)==1
    obj=objects[0];catalog=next(x for x in r['snapshot']['object_types'] if x['key']==obj['entity_type'])
    field=next(f for f in catalog['fields'] if f['required'] and obj['attributes'].get(f['key']))
    selection=json.loads((ROOT/'docs/evidence/model-workflow-v06/selection.json').read_text())
    images=c.get('/workspace/instances/'+i['id']+'/input-images').raise_for_status().json();image=next(x for x in images if x['sha256']==selection['image_sha256'])
    cap_id=b['flow']['steps'][0]['execution']['capability_id']
    installation=next(x for x in c.get('/specialists/installations').raise_for_status().json() if x.get('capability_id')==cap_id)
    assert installation['status']=='ready' and installation['backend']=='cpu'
    save('baseline',state(a['workspace_id']))
    save('prepared',{'instance_id':i['id'],'release_id':r['id'],'business_id':b['id'],'business_name':b['name'],'entity_id':obj['id'],
        'object_name':obj['attributes'][field['key']],'field_id':field['key'],'field_label':field['label'],'image_id':image['id'],'image_name':image['name'],'image_sha256':image['sha256'],
        'capability_id':cap_id,'installation_id':installation['id'],'title':'按原业务目标核对公开图片本地模型候选（非现场验证）',
        'purpose':'根据公开任务原图提取本地候选，核对场景名称与候选依据，保留人工结论并验收原任务目标（非现场验证）'})
    print('Prepared existing approved CPU model, public image and original business; no new records or permissions')
def verify(a,c,phase):
    p=read('prepared');tasks=c.get('/workspace/instances/'+p['instance_id']+'/tasks').raise_for_status().json();matches=[x for x in tasks if x['title']==p['title']];assert len(matches)==1
    task=c.get('/workspace/tasks/'+matches[0]['id']).raise_for_status().json();assert task['status']=='workflow_completed'
    g=c.get('/workspace/tasks/'+task['id']+'/goal').raise_for_status().json();assert g['acceptance']['evaluation']['completed'] and g['data_received']
    assert g['goal']['purpose']==p['purpose'] and g['goal']['entity_ids']==[p['entity_id']] and g['goal']['required_fields']==[p['field_id']]
    receipt=task['workflow_runtime']['receipts'][0];result=receipt['output']['detection'];assert receipt['candidate_only'] and not receipt['entity_state_updated']
    assert result['image_sha256']==p['image_sha256'] and result['backend']=='cpu' and result['cloud_calls']==0 and any(x['label']=='cat' for x in result['detections'])
    assert task['image_snapshots'][0]['id']==p['image_id'] and len(task['workflow_runtime']['receipts'])==2
    with Session(engine()) as db:
        original=db.get(m.Task,task['id']);call=db.get(m.Invocation,receipt['invocation_id']);job=db.scalar(select(m.Job).where(m.Job.kind=='execute_workflow_step',m.Job.data['task_id'].as_string()==task['id']))
        confirmed=datetime.fromisoformat(original.data['goal_confirmed_at'])
        assert confirmed<=job.created_at and confirmed<=call.created_at
        assert call.status=='succeeded' and call.capability_id==p['capability_id'] and call.data['input_hash']==receipt['input_hash']==digest(receipt['input_snapshot'])
        assert call.data['output']==receipt['output'] and call.data['capability_hash']==receipt['capability_hash']
        candidate=db.get(m.Memory,original.data['native_goal_data_receipt_id']);assert candidate.status=='candidate' and not candidate.data['auto_publish']
        cap=db.get(m.Capability,p['capability_id']);execution=cap.data['execution'];installation=db.get(m.SpecialistInstallation,p['installation_id'])
        assert execution['adapter']=='local_specialist' and execution['installation_id']==installation.id
        assert execution['generation']==installation.data['generation'] and execution['recipe_hash']==installation.data['recipe_hash'] and installation.data['backend']=='cpu'
        assert result['inference_ms']>0 and result['torch_version'] and result['transformers_version']
        # Registered workflow inference is durably owned by Invocation, not the standalone run endpoint.
        base=read('baseline');runs=list(db.scalars(select(m.SpecialistRun).where(m.SpecialistRun.workspace_id==a['workspace_id'])))
        assert not [run for run in runs if 'SpecialistRun:'+run.id not in base['own']]
    current=state(a['workspace_id']);base=read('baseline');assert current['other']==base['other'] and current['other_originals']==base['other_originals']
    assert all(current['own'].get(key)==value for key,value in base['own'].items()),'Old own records changed'
    assert all(current['own_originals'].get(key)==value for key,value in base['own_originals'].items()),'Old own originals changed'
    new=set(current['own'])-set(base['own']);assert [key for key in new if key.startswith('Task:')]==['Task:'+task['id']]
    assert [key for key in new if key.startswith('Invocation:')]==['Invocation:'+receipt['invocation_id']]
    assert not any(key.split(':')[0] in {'CloudCall','Acquisition','Entity','Observation'} for key in new)
    export=c.get('/workspace/tasks/'+task['id']+'/goal/export').raise_for_status().json();assert export['goal']['acceptance']==g['acceptance']
    save(phase+'-export',export)
    proof={'task_id':task['id'],'goal_saved_before_first_model':True,'local_model_invocations':1,'standalone_model_runs_created':0,'cpu_public_image_cat_candidate_found':True,
        'formal_goal_accepted':True,'workflow_steps':2,'data_receipt_candidate':True,'learning_auto_published':False,'cloud_calls':0,'device_actions':0,
        'own_records':len(current['own']),'old_own_records_unchanged':len(base['own']),'other_records_unchanged':len(current['other']),
        'old_own_originals_unchanged':len(base['own_originals']),'other_originals_unchanged':len(current['other_originals']),
        'new_evidence_files':len(current['own_originals'])-len(base['own_originals']),'domain_quality_verified':False,'production_ready':False}
    save(phase+'-verification',proof)
    if phase=='before-restart':save('before-restart',current)
    if phase=='after-restart':assert current==read('before-restart')
    print('Verified real local model, goal-before-first-call, original input, final acceptance and persistence:',phase)
def main():
    a=json.loads(ACCESS.read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=35) as c:
        r=c.post('/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']})
        if sys.argv[1]=='prepare':prepare(a,c)
        else:verify(a,c,sys.argv[1])
if __name__=='__main__':main()
