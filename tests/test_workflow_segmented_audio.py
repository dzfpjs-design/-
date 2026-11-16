"""Real task-owned local VAD/ASR and durable workflow review receipts."""
import json,copy,os,time
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import SegmentedAudioRun,SpeechActivityRun,Memory,CloudCall,Membership
from generic_agents.db import engine
from generic_agents.worker import process_once
from generic_agents.instance_compiler import signature_value
from tests.test_audio import upload
from tests.test_segmented_audio import source
from tests.test_instance_workspace import configuration,prepared,object_row,complete
from tests.test_workflow_abilities import enable,finish_job,run

def configured(client,content=None):
 s=source(client,'.runtime/vad-bootstrap/two-public-zh.wav')
 body={'source_run_id':s['source']['source_run_id'],'source_snapshot_hash':s['source_snapshot_hash']}
 r=client.post('/api/audio-segments/register-business',json=body);assert r.status_code==200,r.text;cap=r.json()
 assert client.post('/api/audio-segments/register-business',json=body).json()==cap
 original=upload(client,content if content is not None else Path('.runtime/vad-bootstrap/two-public-zh.wav').read_bytes()).json();assert process_once(original['job_id'],deliver=False)
 original=client.get('/api/audio/'+original['id']).json();assert original['status']=='succeeded',original.get('error')
 c=configuration();f=c['flows']['资料核验'];f['steps'][0].update(name='语音检测与分段转写',agent='多模感知智能体',role='',execution={'capability_id':cap['capability_id'],'automation_action':'observe','arguments':{},'bindings':{}});c['automation']['policies']['资料核验']['rules']['observe']={'mode':'自动运行'};f['confirmedSignature']=json.dumps(signature_value(c,f),ensure_ascii=False)
 release,_,check=prepared(client,c);assert release['available_business_ids'],check;enable(client,release);base='/api/workspace/instances/'+release['instance_id'];candidate=client.get(base+'/audio-candidates').json()[0]
 r=client.post(base+'/input-audio',json={'source_run_id':original['id'],'source_snapshot_hash':candidate['snapshot_hash'],'request_id':str(uuid4())});assert r.status_code==201,r.text
 obj=object_row(client,release);body={'release_id':release['id'],'business_id':release['available_business_ids'][0],'entity_ids':[obj['id']],'title':'公开双区间业务核查','audio_ids':[r.json()['id']],'request_id':str(uuid4())}
 assert client.post(base+'/tasks',json={**body,'audio_ids':[]}).status_code==422
 r=client.post(base+'/tasks',json=body);assert r.status_code==201,r.text;return r.json(),original,cap

def test_actual_business_vad_segments_review_receipts_and_forbidden_bypass(client):
 task,original,cap=configured(client);task=finish_job(client,task);assert task['status']=='workflow_waiting',task['workflow_runtime'].get('automatic')
 out=task['workflow_runtime']['receipts'][0]['output'];result=out['segmented_audio'];identity=out['segmented_run_id'];path='/api/workspace/tasks/'+task['id']+'/segmented-audio/'+identity
 assert task['workflow_runtime']['receipts'][0]['candidate_only'] and result['vad_used'] and len(result['segments'])==2
 assert all('9点' in x['text'] for x in result['segments']) and result['source_time']=='unknown' and result['cloud_calls']==0
 values=client.get('/api/workspace/tasks/'+task['id']+'/segmented-audio').json();assert values['runs'][0]['receipt']['model_load_count']==1
 assert client.get(path+'/original').content==Path('.runtime/vad-bootstrap/two-public-zh.wav').read_bytes()
 row=client.get('/api/audio-segments/'+identity).json();v=client.get('/api/audio-activity/'+out['speech_activity_run_id']).json()
 for suffix in ['cancel','retry']:
  assert client.post('/api/audio-segments/'+identity+'/'+suffix,json={'version':row['version']}).status_code==409
  assert client.post('/api/audio-activity/'+v['id']+'/'+suffix,json={'version':v['version']}).status_code==409
 assert client.put('/api/audio-segments/'+identity+'/review-draft',json={'version':row['version'],'texts':['绕过任务','绕过任务'],'note':''}).status_code==409
 assert client.post('/api/audio-segments',json={'request_id':str(uuid4()),'source_run_id':v['id'],'source_snapshot_hash':row['source_snapshot_hash'],'configuration':row['configuration']}).status_code==409
 r=complete(client,task,note='已听原音并核对两段公开文字；不做业务异常推断');assert r.status_code==200,r.text;task=r.json()
 data=client.get(path+'/export').json();assert not data['partial'] and data['review_receipts'][-1]['note']=='已听原音并核对两段公开文字；不做业务异常推断'
 with Session(engine()) as db:
  m=db.get(Memory,task['workflow_runtime']['data_receipt_id']);assert m.status=='candidate' and m.data['receipts'][0]['output']==out and not m.data['entity_state_updated']
  assert not db.scalars(select(CloudCall)).all()
 # A forged receipt cannot pass original result verification or advance review.
 with Session(engine()) as db:
  row=db.get(SegmentedAudioRun,identity);d=copy.deepcopy(row.data);d['installations']['asr']['generation']+=1;row.data=d;db.commit()
 assert client.get(path+'/export').status_code==409

def test_same_business_node_retry_keeps_actual_vad_and_first_asr_checkpoint(client,monkeypatch):
 task,_,_=configured(client)
 from generic_agents import local_segmented
 native=local_segmented.call
 def interrupted(*args,**kwargs):
  args=list(args);save=args[6]
  def first(value):save(value);raise ValueError('test stopped after native first segment checkpoint')
  args[6]=first;return native(*args,**kwargs)
 with monkeypatch.context() as patch:
  patch.setattr(local_segmented,'call',interrupted);task=finish_job(client,task)
 assert task['status']=='workflow_failed',task['workflow_runtime'].get('automatic')
 url='/api/workspace/tasks/'+task['id']+'/segmented-audio';saved=client.get(url).json();row=saved['runs'][0];assert len(row['segments'])==1 and row['status']=='failed'
 before=copy.deepcopy(row);v=client.get('/api/audio-activity/'+row['source_run_id']).json()
 r=run(client,task,retry=True);assert r.status_code==200,r.text;task=finish_job(client,r.json());assert task['status']=='workflow_waiting',task['workflow_runtime'].get('automatic')
 after=client.get(url).json()['runs'];assert len(after)==1 and after[0]['id']==before['id'] and after[0]['segments'][0]==before['segments'][0]
 assert after[0]['receipt']['indexes']==[1] and after[0]['generation']==2 and after[0]['source_snapshot_hash']==before['source_snapshot_hash']
 assert client.get('/api/audio-activity/'+v['id']).json()==v

def test_task_cancel_reaps_real_segment_process_and_keeps_task_original(client):
 task,_,_=configured(client);path='/api/workspace/tasks/'+task['id'];job=task['workflow_runtime']['automatic']['job_id']
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(process_once,job,deliver=False);deadline=time.monotonic()+60
  while time.monotonic()<deadline:
   data=client.get(path+'/segmented-audio').json();rows=data.get('runs',[])
   if rows and rows[0]['status']=='running' and rows[0].get('pid'):break
   assert not future.done(),client.get(path).json()['workflow_runtime'].get('automatic');time.sleep(.05)
  else:raise AssertionError('native segmented ASR not observed')
  pid=rows[0]['pid'];t=client.get(path).json();r=client.post(path+'/cancel',json={'version':t['version'],'reason':'取消公开分段测试','retained_note':'保留原音频'});assert r.status_code==200,r.text;assert future.result(timeout=15)
 with pytest.raises(ProcessLookupError):os.kill(pid,0)
 rows=client.get(path+'/segmented-audio').json()['runs'];assert rows[0]['status']=='cancelled' and rows[0]['source_snapshot_hash']==data['runs'][0]['source_snapshot_hash']
 assert client.get(path+'/segmented-audio/'+rows[0]['id']+'/original').status_code==200

def test_registered_task_pipeline_requires_owned_lease_and_fixed_bindings(client,account):
 task,_,cap=configured(client)
 from generic_agents.gateway import invoke
 from generic_agents.security import Principal
 with Session(engine()) as db:
  m=db.query(Membership).filter_by(workspace_id=account[2]).one();p=Principal(m.user_id,m.workspace_id,'admin','test')
  saved=task['audio_snapshots'][0];out=invoke(db,p,cap['capability_id'],'perception',{'task_id':task['id'],'audio_ref':{'input_audio_id':saved['id'],'snapshot_hash':saved['snapshot_hash']}},'forbidden-no-lease')
  assert out.status=='blocked_evidence' and not db.scalars(select(SegmentedAudioRun)).all()
  from generic_agents.workflow_segmented_audio import manifest,validate_manifest
  from generic_agents.contracts import Manifest
  from fastapi import HTTPException
  value=manifest(db,p).model_dump()
  value['execution']['input_bindings']['audio_ref']='object_attributes'
  with pytest.raises(HTTPException) as error:validate_manifest(db,p,Manifest.model_validate(value))
  assert error.value.status_code==422

def test_task_silent_original_runs_real_vad_without_inventing_asr_text(client):
 from tests.test_audio import wav
 task,_,_=configured(client,content=wav());task=finish_job(client,task);assert task['status']=='workflow_waiting',task['workflow_runtime'].get('automatic')
 out=task['workflow_runtime']['receipts'][0]['output'];result=out['segmented_audio'];row=client.get('/api/audio-segments/'+out['segmented_run_id']).json()
 assert result['segments']==[] and result['vad_used'] and not result['asr_inference_performed'] and result['skip_reason']=='no_vad_candidate_intervals'
 assert row['receipt']['model_load_count']==0 and row['receipt']['indexes']==[]
 v=client.get('/api/audio-activity/'+out['speech_activity_run_id']).json();assert v['result']['activity']['inference_performed'] and v['result']['activity']['intervals']==[]
