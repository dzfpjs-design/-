"""Normal public QA intake and browser-owned original business goal workflow."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_signal_advice_runtime import state
OUT=ROOT/'docs/evidence/native-business-goal-v06';OUT.mkdir(parents=True,exist_ok=True)
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str)+'\n')
def read(n):return json.loads((OUT/(n+'.json')).read_text())
def prepare(a,c):
    if (OUT/'prepared.json').exists():raise RuntimeError('已有检查点，不重复创建原任务或新登记')
    original=json.loads((ROOT/'docs/evidence/signal-v06/prepared.json').read_text())
    identity='b8d1db11-a5a7-4a30-88cf-6ff69cefef64'
    assert original['instance_id']==identity
    with Session(engine()) as db:
        instance=db.get(m.DomainInstance,identity);assert instance.workspace_id==a['workspace_id'] and instance.status=='partially_active'
        release=db.get(m.InstanceRelease,instance.active_release_id)
        entity=db.get(m.Entity,original['entity_id']);assert entity.namespace==instance.namespace and entity.schema_capability_id==release.package_id
        kind=next(x for x in release.data['snapshot']['object_types'] if x['key']==entity.entity_type)
        field=next(f for f in kind['fields'] if f['required'] and f['kind']=='文本')
        business=next(b for b in release.data['snapshot']['businesses'] if b['id'] in instance.data['enabled_business_ids'] and b['available'] and all(s['agent']=='人工确认' for s in b['flow']['steps']))
        save('before',state(a['workspace_id']))
        save('prepared',{'instance_id':identity,'release_id':release.id,'entity_id':entity.id,'business_id':business['id'],'business_name':business['name'],
            'field':field['key'],'field_label':field['label'],'expected_value':entity.data[field['key']],
            'source_id':original['source_id'],'extractor_id':original['extractor_id'],'title':'核对公开工程名称登记并完成原任务资料核验（非现场验证）',
            'goal_purpose':'对照公开工程名称登记与业务档案，完成资料核验，保留原步骤与登记依据（非现场验证）',
            'new_accounts':0,'new_permissions':0,'device_worker':'off'})
    print('Existing authorized public instance/object/source checkpoint saved; no writes or model calls')
def intake(a,c):
    if (OUT/'intake.json').exists():raise RuntimeError('已登记，沿现有记录继续，不能重复输入')
    p=read('prepared');base='/workspace/instances/'+p['instance_id']+'/tasks'
    rows=c.get(base).json();rows=[t for t in rows if t['title']==p['title']];assert len(rows)==1
    task=rows[0];goal=c.get('/workspace/tasks/'+task['id']+'/goal').json();assert goal['goal_confirmed'] and goal['check']['gaps'][0]['business_task_id']==task['id']
    assert goal['check']['gaps'][0]['status']=='needs_existing_evidence'
    clock=datetime.now(timezone.utc)-timedelta(seconds=1)
    body={'source_id':p['source_id'],'extractor_id':p['extractor_id'],'source_record_id':'native-goal-public-'+task['id'],
        'entity_id':p['entity_id'],'observed_at':clock.isoformat(),'business_day':str((clock+timedelta(hours=8)).date()),
        'startup_mode':'manual','attributes':{'公开登记值':27.5,p['field']:p['expected_value'],'记录说明':'公开工程名称登记用于验证原任务资料闭环，非现场测量'}}
    save('pending-intake',{'task_id':task['id'],'body':body})
    response=c.post('/workspace/instances/'+p['instance_id']+'/reported-observations',json=body);response.raise_for_status()
    save('intake',{'task_id':task['id'],'observation_id':response.json()['id'],'body':body})
    print('One normal public manual input saved; registered local rule will process it')
def verify(a,c,phase):
    p=read('prepared');i=read('intake');before=read('before');after=state(a['workspace_id'])
    assert before['other']==after['other'] and before['own_originals']==after['own_originals'] and before['other_originals']==after['other_originals']
    with Session(engine()) as db:
        task=db.get(m.Task,i['task_id']);assert task.status=='workflow_completed' and task.data['native_goal_acceptance']['evaluation']['completed']
        assert len(task.data['workflow_runtime']['receipts'])==2 and not task.data.get('native_goal_cancelled')
        value=task.data['native_goal_acceptance'];assert digest(value)==task.data['native_goal_acceptance_hash'] and not value['physical_state_verified']
        obs=db.get(m.Observation,i['observation_id']);assert obs.status=='processed' and obs.data['original_request']['attributes']==i['body']['attributes']
        assert obs.data['extracted']['attributes']==i['body']['attributes'] and obs.data['human_verification']['decision']=='confirmed' and len(obs.data['verification_history'])==1
        event=db.scalar(select(m.Event).where(m.Event.workspace_id==a['workspace_id'],m.Event.kind=='observation.data',m.Event.data['observation_id'].as_string()==obs.id));assert event.status=='received'
        query=db.get(m.DataRun,task.data['native_goal_query_id']);assert query.data['goal_delegation']['task_id']==task.id
        gaps=list(db.scalars(select(m.DataGap).where(m.DataGap.workspace_id==a['workspace_id'],m.DataGap.query_id==query.id)))
        assert len(gaps)==1 and gaps[0].status=='resolved' and gaps[0].data['business_task_id']==task.id
        receipt=db.get(m.Memory,task.data['native_goal_data_receipt_id']);assert receipt.status=='candidate' and receipt.data['acceptance_hash']==task.data['native_goal_acceptance_hash'] and not receipt.data['auto_publish']
        call=db.get(m.Invocation,obs.data['invocation_id']);cap=db.get(m.Capability,call.capability_id);assert call.status=='succeeded' and cap.data['execution']['handler']=='numeric_rules'
        changed={k for k,v in before['own'].items() if after['own'].get(k)!=v}
        # New declared Name may refresh the pre-existing derived state/expiry for this object, never originals or tasks.
        permitted=set()
        saved=db.scalar(select(m.ObjectState).where(m.ObjectState.workspace_id==a['workspace_id'],m.ObjectState.entity_id==p['entity_id']))
        if saved:permitted.add('ObjectState:'+saved.id)
        for job in db.scalars(select(m.Job).where(m.Job.workspace_id==a['workspace_id'],m.Job.kind=='refresh_object_state')):
            if job.data.get('state_request',{}).get('entity_id')==p['entity_id'] and job.data['state_request'].get('due_state_hash'):permitted.add('Job:'+job.id)
        assert changed<=permitted,changed
        new=set(after['own'])-set(before['own']);assert [k for k in new if k.startswith('Task:')]==['Task:'+task.id]
        assert [k for k in new if k.startswith('Observation:')]==['Observation:'+obs.id]
        assert [k for k in new if k.startswith('Invocation:')]==['Invocation:'+call.id]
        assert not any(k.split(':')[0] in {'CloudCall','SpecialistRun','Acquisition','Entity'} for k in new)
        proof={'task_id':task.id,'observation_id':obs.id,'data_query_id':query.id,'gap_id':gaps[0].id,'status':'workflow_completed','formal_goal_accepted':True,
            'original_input_preserved':True,'original_steps':2,'parallel_coordinator_tasks':0,'data_management_receipt':'candidate','learning_auto_published':False,
            'new_local_rule_invocations':1,'new_cloud_calls':0,'new_specialist_inferences':0,'device_actions':0,'entity_updated':False,
            'own_records':len(after['own']),'other_records_unchanged':len(after['other']),'old_own_records_unchanged':len(before['own'])-len(changed),
            'changed_old_records':sorted(changed),'own_originals_unchanged':len(after['own_originals']),'other_originals_unchanged':len(after['other_originals']),
            'physical_state_verified':False,'production_ready':False}
    response=c.get('/workspace/tasks/'+task.id+'/goal/export');response.raise_for_status();assert response.json()['goal']['acceptance']['evaluation']['completed']
    save(phase+'-export',response.json());save(phase+'-verification',proof)
    if phase=='before-restart':save('before-restart',after)
    if phase=='after-restart':assert read('before-restart')==after
    print('Verified original task/data gap/review/criteria/acceptance/data receipt and isolation:',phase)
def main():
    a=json.loads((ROOT/'.runtime/wms-access.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=35) as c:
        response=c.post('/login',json={k:a[k] for k in ['username','password']});response.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':response.json()['csrf_token']})
        if sys.argv[1]=='prepare':prepare(a,c)
        elif sys.argv[1]=='intake':intake(a,c)
        else:verify(a,c,sys.argv[1])
if __name__=='__main__':main()
