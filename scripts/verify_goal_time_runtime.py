"""Real wall-clock goal expiry around normal API inputs and browser-created goal."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_signal_advice_runtime import state
OUT=ROOT/'docs/evidence/native-goal-time-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=ROOT/'.runtime/wms-access.json'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def read(name):return json.loads((OUT/(name+'.json')).read_text())
def find(c,p):
    rows=c.get('/workspace/instances/'+p['instance_id']+'/tasks').raise_for_status().json();rows=[t for t in rows if t['title']==p['title']];assert len(rows)==1
    task=c.get('/workspace/tasks/'+rows[0]['id']).raise_for_status().json()
    goal=c.get('/workspace/tasks/'+task['id']+'/goal').raise_for_status().json();return task,goal

def prepare(a,c):
    assert not (OUT/'prepared.json').exists(),'Continue existing checkpoint; no duplicate object or task'
    old=json.loads((ROOT/'docs/evidence/native-business-goal-v06/prepared.json').read_text())
    instance=next(i for i in c.get('/instances').raise_for_status().json() if i['id']==old['instance_id']);assert instance['status']=='partially_active'
    release=next(r for r in c.get('/instances/'+instance['id']+'/releases').raise_for_status().json() if r['id']==instance['active_release_id']);assert release['id']==old['release_id']
    original=next(o for o in c.get('/instances/'+instance['id']+'/objects').raise_for_status().json() if o['id']==old['entity_id'])
    save('baseline',state(a['workspace_id']))
    name='公开资料时效场景（回溯工程登记，非现场）'
    body={'release_id':release['id'],'object_type':original['entity_type'],'attributes':{old['field']:name},'reference':'公开工程回溯登记时效核验，非设备或现场测量','request_id':str(uuid4())}
    save('pending-object',body);obj=c.post('/instances/'+instance['id']+'/objects',json=body).raise_for_status().json()
    deadline=(datetime.now(timezone.utc)+timedelta(minutes=4)).replace(second=0,microsecond=0)
    save('prepared',{**{key:old[key] for key in ['instance_id','release_id','business_id','business_name','field','field_label','source_id','extractor_id']},
        'entity_id':obj['id'],'object_name':name,'deadline_ui':(deadline+timedelta(hours=8)).strftime('%Y-%m-%dT%H:%M'),'deadline':deadline.isoformat(),
        'title':'核对公开回溯登记资料有效期与截止（非现场验证）','purpose':'按一小时有效期核对公开人工登记资料，保留原依据和处理说明；到截止时间结束旧协调（非现场验证）'})
    print('Prepared one normal public engineering object and fixed existing source; no model/device/cloud changes')

def intake(a,c):
    assert not (OUT/'pending-intake.json').exists(),'Original input already fixed; do not repeat intake'
    p=read('prepared');task,goal=find(c,p);assert goal['goal_confirmed'] and goal['goal']['evidence']['time']['max_age_seconds']==3600
    assert datetime.fromisoformat(goal['goal']['deadline'])==datetime.fromisoformat(p['deadline'])
    assert goal['check']['evidence']['status']=='insufficient' and goal['check']['gaps'][0]['business_task_id']==task['id']
    observed=datetime.now(timezone.utc)-timedelta(seconds=3550)
    body={'source_id':p['source_id'],'extractor_id':p['extractor_id'],'entity_id':p['entity_id'],'source_record_id':'goal-time-public-'+task['id'],
        'observed_at':observed.isoformat(),'business_day':str((observed+timedelta(hours=8)).date()),'startup_mode':'manual',
        'attributes':{'公开登记值':27.5,p['field']:p['object_name'],'记录说明':'回溯人工登记时间专用于工程时效核验，未证明现场或采集时间真实性'}}
    save('pending-intake',{'task_id':task['id'],'body':body});response=c.post('/workspace/instances/'+p['instance_id']+'/reported-observations',json=body).raise_for_status().json()
    save('intake',{'task_id':task['id'],'observation_id':response['id'],'body':body})
    print('Normal retrospective engineering input saved; its one-hour validity ends in about 50 seconds')

def progress(a,c,phase):
    p=read('prepared');task,goal=find(c,p);with_input=read('intake')
    with Session(engine()) as db:
        saved=db.get(m.Task,task['id']);obs=db.get(m.Observation,with_input['observation_id']);assert obs.data['original_request']=={**with_input['body'],'processing_version':'1.0.0','acquisition_id':None,'sensor_asset_id':None,'sensor_modality':None} and obs.status=='processed'
        assert obs.data['extracted']['attributes']==with_input['body']['attributes']
        query=db.get(m.DataRun,saved.data['native_goal_query_id']);assert query.data['native_goal_request_hash']==digest(query.data['request'])
        job=db.get(m.Job,saved.data['native_goal_timer']['job_id']) if saved.data.get('native_goal_timer') else None
        if phase=='fresh-history':
            rows=list(db.scalars(select(m.Job).where(m.Job.kind=='check_native_business_goal',m.Job.data['task_id'].as_string()==task['id'])))
            expiry=[j for j in rows if j.data.get('time_trigger',{}).get('reason')=='原目标资料有效期已到'];assert len(expiry)==1
            original=expiry[0];receipt=db.get(m.Memory,original.data['time_trigger']['generation']);report=receipt.data['report'];assert digest(report)==receipt.data['report_hash'] and report['evidence']['status']=='sufficient'
            assert original.data['time_trigger_hash']==digest(original.data['time_trigger'])
            assert datetime.fromisoformat(original.data['time_trigger']['due_at'])==obs.observed_at+timedelta(seconds=3600,microseconds=1)
            assert datetime.fromisoformat(report['checked_at'])<original.available_at
            assert any(j.data.get('goal_check_id')==receipt.id and j.status=='succeeded' for j in rows)
            save('fresh',{'task_id':task['id'],'timer_job_id':original.id,'due_at':original.available_at.isoformat(),'request_hash':query.data['native_goal_request_hash'],'sufficient_receipt_id':receipt.id,'note':'Recovered from immutable sufficient receipt and original timer after live verifier omitted API default fields; not a replayed input or new live observation'})
        if phase=='fresh':
            assert goal['check']['evidence']['status']=='sufficient' and query.data['result']['status']=='sufficient' and job.status=='queued'
            assert datetime.fromisoformat(job.data['time_trigger']['due_at'])==obs.observed_at+timedelta(seconds=3600,microseconds=1)
            save('fresh',{'task_id':task['id'],'timer_job_id':job.id,'due_at':job.available_at.isoformat(),'request_hash':query.data['native_goal_request_hash'],'note':'actual queued original-goal timer; no browser timer needed'})
        elif phase=='expired':
            assert goal['check']['evidence']['status']=='insufficient' and query.data['result']['status']=='insufficient' and not goal['timed_out']
            assert any(g['business_task_id']==task['id'] and g['status']=='needs_existing_evidence' for g in goal['check']['gaps'])
            old=db.get(m.Job,read('fresh')['timer_job_id']);assert old.status=='succeeded' and old.data.get('goal_check_id')
            assert query.data['native_goal_request_hash']==read('fresh')['request_hash'] and job.status=='queued' and job.data['time_trigger']['reason']=='目标截止时间已到'
            save('expired',{'task_id':task['id'],'automatic_expiry_job_succeeded':True,'old_original_and_request_preserved':True,'gap_on_original_task':True,'deadline_job_id':job.id,'deadline_due_at':job.available_at.isoformat()})
        elif phase=='deadline':
            assert goal['timed_out'] and goal['scope_error'] and goal['next_time_check'] is None and not goal['acceptance']
            job=db.get(m.Job,read('expired')['deadline_job_id']);assert job.status=='succeeded' and job.data.get('goal_deadline_recorded')
            assert saved.data['workflow_runtime']['drafts'];save('deadline',{'task_id':task['id'],'automatic_deadline_job_succeeded':True,'human_notes_preserved':True,'formal_goal_accepted':False})
        elif phase=='poll':
            print(json.dumps({'task_id':task['id'],'goal_status':'timed_out' if goal['timed_out'] else goal['check']['evidence']['status'],
                'timer_job_id':job.id if job else None,'timer_status':job.status if job else None,'due_at':job.available_at.isoformat() if job else None},ensure_ascii=False));return
    save(phase+'-goal',goal);print('Verified real original-goal wall-clock phase:',phase)

def db_retained_stopped(identifier):
    with Session(engine()) as db:
        row=db.get(m.Task,identifier);return bool(row.data.get('native_goal_cancelled')) and row.data['goal_spec']['deadline'] is None

def verify(a,c,phase):
    p=read('prepared');task,goal=find(c,p);intake=read('intake');assert goal['timed_out'] and goal['cancelled'] and not goal['acceptance'] and goal['next_time_check'] is None
    assert task['status']=='workflow_waiting' and not task['workflow_runtime']['receipts']
    assert any('截止' in draft['note'] for draft in task['workflow_runtime']['drafts'].values())
    with Session(engine()) as db:
        obs=db.get(m.Observation,intake['observation_id']);assert obs.data['original_request']=={**intake['body'],'processing_version':'1.0.0','acquisition_id':None,'sensor_asset_id':None,'sensor_modality':None} and obs.data['extracted']['attributes']==intake['body']['attributes']
        call=db.get(m.Invocation,obs.data['invocation_id']);assert call.status=='succeeded' and call.capability_id==p['extractor_id']
        gaps=list(db.scalars(select(m.DataGap).where(m.DataGap.query_id==db.get(m.Task,task['id']).data['native_goal_query_id'])));assert len(gaps)==1 and gaps[0].data['business_task_id']==task['id']
        assert not list(db.scalars(select(m.Job).where(m.Job.kind=='check_native_business_goal',m.Job.status=='queued',m.Job.data['task_id'].as_string()==task['id'])))
    before=read('baseline');after=state(a['workspace_id']);assert after['other']==before['other'] and after['other_originals']==before['other_originals']
    assert all(after['own'].get(k)==v for k,v in before['own'].items()) and after['own_originals']==before['own_originals']
    new=set(after['own'])-set(before['own']);retained=read('first-submission-retained');assert set(k for k in new if k.startswith('Task:'))=={'Task:'+task['id'],'Task:'+retained['task_id']};assert db_retained_stopped(retained['task_id'])
    assert [k for k in new if k.startswith('Entity:')]==['Entity:'+p['entity_id']]
    assert [k for k in new if k.startswith('Observation:')]==['Observation:'+obs.id] and [k for k in new if k.startswith('Invocation:')]==['Invocation:'+call.id]
    assert not any(k.split(':')[0] in {'CloudCall','Acquisition','SpecialistRun'} for k in new)
    proof={'task_id':task['id'],'entity_id':p['entity_id'],'observation_id':obs.id,'real_wall_clock_automatic_expiry':True,'real_wall_clock_automatic_deadline':True,
        'original_request_preserved':True,'original_task_gap':True,'human_note_saved_after_deadline':True,'goal_stopped_after_deadline':True,'workflow_completed':False,'formal_goal_accepted':False,
        'retained_first_submission':read('first-submission-retained')['task_id'],'local_rule_calls':1,'new_cloud_calls':0,'new_device_actions':0,'new_specialist_inferences':0,'own_records':len(after['own']),'old_own_records_unchanged':len(before['own']),
        'other_records_unchanged':len(after['other']),'own_originals_unchanged':len(after['own_originals']),'other_originals_unchanged':len(after['other_originals']),'physical_state_verified':False,'production_ready':False}
    export=c.get('/workspace/tasks/'+task['id']+'/goal/export').raise_for_status().json();save(phase+'-export',export);save(phase+'-verification',proof)
    if phase=='before-restart':save('before-restart',after)
    else:assert after==read('before-restart')
    print('Verified original goal expiry/deadline/note/stop and isolation:',phase)

def main():
    a=json.loads(ACCESS.read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=35) as c:
        response=c.post('/login',json={k:a[k] for k in ['username','password']});response.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':response.json()['csrf_token']})
        phase=sys.argv[1]
        if phase=='prepare':prepare(a,c)
        elif phase=='intake':intake(a,c)
        elif phase in {'fresh','fresh-history','expired','deadline','poll'}:progress(a,c,phase)
        else:verify(a,c,phase)
if __name__=='__main__':main()
