"""Real native VAD→ASR, partial checkpoints, human persistence and isolation."""
import json,copy
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.worker import process_once
from generic_agents.models import SegmentedAudioRun,Memory,Membership
from generic_agents.contracts import digest
from generic_agents.local_segmented import call,validate_segment

def source(client,path=".runtime/audio-bootstrap/public-zh.mp3"):
 path=Path(path)
 for pack in ['silero-vad-onnx-v1','sensevoice-small-v1']:
  r=client.post('/api/specialists/installations',json={'pack_id':pack});assert r.status_code==201,r.text;assert process_once(r.json()['job_id'],deliver=False)
  row=next(x for x in client.get('/api/specialists/installations').json() if x['id']==r.json()['id']);assert row['status']=='ready',row.get('error')
 r=client.post('/api/audio-activity',files={'file':('public.mp3',path.read_bytes(),'audio/wav' if path.suffix=='.wav' else 'audio/mpeg')},data={'configuration':json.dumps({'channel':'mono','threshold':.5}),'request_id':str(uuid4())});assert r.status_code==201,r.text;assert process_once(r.json()['job_id'],deliver=False)
 value=client.get('/api/audio-segments/source/'+r.json()['id']);assert value.status_code==200,value.text;return value.json()
def create(client,s,identity=None):return client.post('/api/audio-segments',json={'request_id':identity or str(uuid4()),'source_run_id':s['source']['source_run_id'],'source_snapshot_hash':s['source_snapshot_hash'],'configuration':{'indexes':[0],'language':'zh'}})

def test_actual_segmented_request_review_original_provenance_and_durability(client):
 s=source(client);identity=str(uuid4());r=create(client,s,identity);assert r.status_code==201,r.text;row=r.json();assert create(client,s,identity).json()['id']==row['id']
 assert process_once(row['job_id'],deliver=False);path='/api/audio-segments/'+row['id'];row=client.get(path).json();assert row['status']=='succeeded',row.get('error');segment=row['segments'][0]
 assert segment['text']=='开放时间早上9点至下午5点。' and segment['clip_start_seconds']==.538 and segment['clip_end_seconds']==5.382 and not segment['word_timestamps']
 assert row['result']['vad_used'] and row['receipt']['model_load_count']==1 and row['result']['cloud_calls']==0
 draft={'version':row['version'],'texts':['已核对：'+segment['text']],'note':'公开分段工程验证'};r=client.put(path+'/review-draft',json=draft);assert r.status_code==200,r.text;saved=r.json();assert client.get(path).json()['review_draft']['texts']==draft['texts']
 assert client.put(path+'/review',json={**draft,'confirmed':True}).status_code==409
 r=client.put(path+'/review',json={**draft,'version':saved['version'],'confirmed':True});assert r.status_code==200,r.text;reviewed=r.json();assert reviewed['segments']==row['segments'] and reviewed['review_draft'] is None
 assert client.get(path+'/export').json()['record']['review']['texts']==draft['texts']
 assert client.get('/api/audio-segments/requests/'+identity,params={'expected_hash':row['request_hash']}).json()['id']==row['id']
 with Session(engine()) as db:
  m=db.get(Memory,reviewed['review']['candidate_receipt_id']);assert m.status=='candidate' and not m.data['entity_state_updated']
 bad=copy.deepcopy(segment);bad['clip_start_sample']+=1
 with pytest.raises(ValueError):validate_segment(bad,s['source'],row['configuration'])

def test_actual_saved_checkpoint_resume_does_not_recompute_completed_segment(client,monkeypatch):
 s=source(client);r=create(client,s);assert r.status_code==201;r=r.json()
 from generic_agents import local_segmented
 native=local_segmented.call
 def interrupt(*args,**kwargs):
  args=list(args);save=args[6]
  def stop(value):save(value);raise ValueError('test interruption after durable checkpoint')
  args[6]=stop;return native(*args,**kwargs)
 with monkeypatch.context() as patch:
  patch.setattr(local_segmented,'call',interrupt);assert process_once(r['job_id'],deliver=False)
 path='/api/audio-segments/'+r['id'];saved=client.get(path).json();assert saved['status']=='failed' and len(saved['segments'])==1;segments=saved['segments']
 r=client.post(path+'/retry',json={'version':saved['version']});assert r.status_code==200,r.text;assert process_once(r.json()['job_id'],deliver=False)
 row=client.get(path).json();assert row['status']=='succeeded' and row['segments']==segments and row['receipt']['indexes']==[] and row['receipt']['model_load_count']==0

def test_cancel_invalid_snapshot_indexes_and_cross_role(client,account):
 s=source(client);r=create(client,s);row=r.json();path='/api/audio-segments/'+row['id']
 assert create(client,{**s,'source_snapshot_hash':'0'*64}).status_code==409
 for indexes in [[0,0],[1],[True],[]]:
  bad={'request_id':str(uuid4()),'source_run_id':s['source']['source_run_id'],'source_snapshot_hash':s['source_snapshot_hash'],'configuration':{'indexes':indexes,'language':'zh'}};assert client.post('/api/audio-segments',json=bad).status_code==422
 cancelled=client.post(path+'/cancel',json={'version':row['version']});assert cancelled.status_code==200;assert process_once(row['job_id'],deliver=False);assert client.get(path).json()['status']=='cancelled';assert client.get(path+'/export').json()['partial']
 with Session(engine()) as db:m=db.query(Membership).filter_by(workspace_id=account[2]).one();m.role='viewer';db.commit()
 assert client.post(path+'/retry',json={'version':cancelled.json()['version']}).status_code==403

def test_registered_pipeline_invokes_real_asr(client,account):
 s=source(client);r=client.post('/api/audio-segments/register',json={'source_run_id':s['source']['source_run_id'],'source_snapshot_hash':s['source_snapshot_hash']});assert r.status_code==200,r.text
 from generic_agents.gateway import invoke
 from generic_agents.security import Principal
 with Session(engine()) as db:
  m=db.query(Membership).filter_by(workspace_id=account[2]).one();p=Principal(m.user_id,m.workspace_id,'admin','test')
  out=invoke(db,p,r.json()['capability_id'],'perception',{'source_run_id':s['source']['source_run_id'],'source_snapshot_hash':s['source_snapshot_hash'],'indexes':[0],'language':'zh'},'real-segmented-native')
  assert out.status=='succeeded',out.data
  result=out.data['output'];assert result['anomalies']==[] and result['segmented_audio']['segments'][0]['text']=='开放时间早上9点至下午5点。' and result['segmented_audio']['cloud_calls']==0


def test_actual_two_intervals_share_one_asr_load_and_keep_source_clock(client):
 s=source(client,'.runtime/vad-bootstrap/two-public-zh.wav');assert len(s['source']['intervals'])==2
 r=client.post('/api/audio-segments',json={'request_id':str(uuid4()),'source_run_id':s['source']['source_run_id'],'source_snapshot_hash':s['source_snapshot_hash'],'configuration':{'indexes':[0,1],'language':'zh'}});assert r.status_code==201,r.text;assert process_once(r.json()['job_id'],deliver=False)
 row=client.get('/api/audio-segments/'+r.json()['id']).json();assert row['status']=='succeeded',row.get('error');assert row['receipt']['model_load_count']==1 and [v['index'] for v in row['segments']]==[0,1]
 assert all('9点' in v['text'] and '5点' in v['text'] for v in row['segments']);assert row['segments'][1]['clip_start_seconds']>7 and row['result']['source_time']=='unknown'

def test_running_native_cancel_reaps_owned_child_and_retries_original(client):
 import os,time
 from concurrent.futures import ThreadPoolExecutor
 s=source(client);r=create(client,s);assert r.status_code==201;r=r.json();path='/api/audio-segments/'+r['id']
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(process_once,r['job_id'],deliver=False);until=time.monotonic()+40
  while time.monotonic()<until:
   row=client.get(path).json()
   if row['status']=='running' and row.get('pid'):break
   assert not future.done(),row.get('error');time.sleep(.05)
  else:raise AssertionError('native process not observed')
  pid=row['pid'];cancel=client.post(path+'/cancel',json={'version':row['version']});assert cancel.status_code==200,cancel.text;assert future.result(timeout=10)
 with pytest.raises(ProcessLookupError):os.kill(pid,0)
 row=client.get(path).json();assert row['status']=='cancelled' and row['source_snapshot_hash']==s['source_snapshot_hash']
 retry=client.post(path+'/retry',json={'version':row['version']});assert retry.status_code==200,retry.text;assert process_once(retry.json()['job_id'],deliver=False);row=client.get(path).json();assert row['status']=='succeeded' and row['source_snapshot_hash']==s['source_snapshot_hash']
