"""Actual audio processing, source-cohort receipts, recovery and privacy."""
import copy
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import AudioDaily,AudioRun,SegmentedAudioRun,Memory,Membership,Workspace,CloudCall,now
from generic_agents.worker import process_once
from generic_agents.config import settings
from generic_agents.models import Evidence
from tests.test_audio import upload
from tests.test_segmented_audio import source,create
from tests.test_workflow_segmented_audio import configured
from tests.test_workflow_abilities import finish_job
from tests.test_instance_workspace import complete

def collect(client):
 r=client.post('/api/audio-daily/reconcile');assert r.status_code==200,r.text;return r.json()['rows']

def test_actual_native_results_deduplicate_original_and_persist_review_revisions(client):
 assert collect(client)==[]
 s=source(client);seg=create(client,s).json();assert process_once(seg['job_id'],deliver=False)
 original=upload(client).json();assert process_once(original['job_id'],deliver=False)
 rows=collect(client);assert len(rows)==1;r=rows[0]
 assert r['unique_originals']==1 and r['verified_originals']==1 and r['upload_requests']==2 and r['processing_records']==3 and r['saved_asr_segments']==1
 assert r['whole_transcriptions']==1 and r['vad_intervals']==1 and r['source_time']=='unknown' and not r['source_time_verified'] and r['cloud_calls']==0
 assert collect(client)==rows
 record=next(x for x in r['records'] if x['kind']=='segments');base='/api/audio-daily/'+r['id']+'/records/'+record['key'];d=client.get(base).json()
 assert '9点' in d['machine']['segments'][0]['text'] and d['integrity_verified'] and not d['changed_since_collection']
 assert client.get(base+'/original').content==Path('.runtime/audio-bootstrap/public-zh.mp3').read_bytes()
 seg=client.get('/api/audio-segments/'+seg['id']).json();review={'version':seg['version'],'texts':['归集核对：开放时间早上9点至下午5点。'],'note':'公开工程核查','confirmed':True};assert client.put('/api/audio-segments/'+seg['id']+'/review',json=review).status_code==200
 assert client.get(base).json()['changed_since_collection']
 revised=collect(client)[0];assert revised['id']==r['id'] and revised['version']==r['version']+1
 assert client.get(base).json()['review']['texts']==review['texts'] and not client.get(base).json()['changed_since_collection']
 exported=client.get('/api/audio-daily/'+r['id']+'/export').json();assert exported['collection']['receipt_hash']==revised['receipt_hash']
 with Session(engine()) as db:
  memories=db.scalars(select(Memory).where(Memory.data['type'].astext=='audio_daily_summary')).all();assert len(memories)==2 and all(m.status=='candidate' for m in memories)
  daily=db.get(AudioDaily,r['id']);assert db.get(Memory,daily.data['data_memory_id']).data['receipt_hash']==daily.data['receipt_hash']
  assert not db.scalars(select(CloudCall)).all()

def test_pending_cancelled_actual_decode_failure_and_role_workspace_scope(client,account):
 pending=upload(client).json();cancelled=upload(client).json();r=client.post('/api/audio/'+cancelled['id']+'/cancel',json={'version':cancelled['version']});assert r.status_code==200
 bad=upload(client,content=b'ID3'+b'\x00'*20).json();assert process_once(bad['job_id'],deliver=False);assert client.get('/api/audio/'+bad['id']).json()['status']=='failed'
 rows=collect(client);r=rows[0];assert len(r['pending_ids'])==1 and len(r['failed_ids'])==1 and len(r['cancelled_ids'])==1 and r['whole_transcriptions']==0 and r['status']=='requires_attention'
 assert r['processing_records']==3 and r['unique_originals']==2
 with Session(engine()) as db:
  w=Workspace(name='other private audio scope');db.add(w);db.flush();foreign=AudioDaily(workspace_id=w.id,received_day='2026-01-01',data={'records':[]});db.add(foreign);db.flush();foreign_id=foreign.id
  member=db.query(Membership).filter_by(workspace_id=account[2]).one();member.role='viewer';db.commit()
 assert client.get('/api/audio-daily').status_code==200 and client.post('/api/audio-daily/reconcile').status_code==403
 assert client.get('/api/audio-daily/'+foreign_id+'/export').status_code==404
 assert client.get('/api/audio-daily/'+r['id']+'/records/transcription:'+bad['id']).json()['current_status']=='failed'

def test_old_pending_and_later_human_review_update_original_day(client,monkeypatch):
 row=upload(client).json();first=collect(client)[0]
 from generic_agents import audio_daily
 clock=now()+timedelta(days=13);monkeypatch.setattr(audio_daily,'now',lambda:clock)
 old=collect(client)[0];assert old['id']==first['id'] and old['received_day']==first['received_day'] and len(old['pending_ids'])==1
 assert process_once(row['job_id'],deliver=False);done=collect(client)[0];assert not done['pending_ids'] and done['received_day']==first['received_day'] and done['whole_transcriptions']==1
 r=client.get('/api/audio/'+row['id']).json();assert client.put('/api/audio/'+row['id']+'/review',json={'version':r['version'],'text':'旧原件后续人工核对','confirmed':True}).status_code==200
 revised=collect(client)[0];assert revised['version']==done['version']+1 and revised['received_day']==first['received_day'] and revised['records'][0]['review_state']=='confirmed'

def test_real_partial_checkpoint_and_resume_do_not_double_count_segments(client,monkeypatch):
 s=source(client,'.runtime/vad-bootstrap/two-public-zh.wav');body={'request_id':__import__('uuid').uuid4().hex,'source_run_id':s['source']['source_run_id'],'source_snapshot_hash':s['source_snapshot_hash'],'configuration':{'indexes':[0,1],'language':'zh'}}
 r=client.post('/api/audio-segments',json=body);assert r.status_code==201;r=r.json()
 from generic_agents import local_segmented
 native=local_segmented.call
 def interrupted(*args,**kwargs):
  args=list(args);save=args[6]
  def first(value):save(value);raise ValueError('test interruption after real first segment')
  args[6]=first;return native(*args,**kwargs)
 with monkeypatch.context() as patch:
  patch.setattr(local_segmented,'call',interrupted);assert process_once(r['job_id'],deliver=False)
 first=collect(client)[0];assert first['saved_asr_segments']==1 and len(first['failed_ids'])==1
 row=client.get('/api/audio-segments/'+r['id']).json();retry=client.post('/api/audio-segments/'+r['id']+'/retry',json={'version':row['version']});assert retry.status_code==200;assert process_once(retry.json()['job_id'],deliver=False)
 second=collect(client)[0];assert second['id']==first['id'] and second['saved_asr_segments']==2 and second['processing_records']==2 and not second['failed_ids']
 assert collect(client)==[second]

def test_actual_business_late_cohort_and_human_task_receipt_are_drillable(client):
 task,original,cap=configured(client)
 # Controlled historical upload date in the disposable database; processing is real.
 with Session(engine()) as db:row=db.get(AudioRun,original['id']);row.created_at-=timedelta(days=1);db.commit()
 task=finish_job(client,task);assert task['status']=='workflow_waiting'
 rows=collect(client);task_day=next(r for r in rows if r['business_tasks']);assert task_day['unique_originals']==1 and task_day['business_tasks_awaiting_review']==1 and task_day['late_processing_records']>=2
 filtered=client.get('/api/audio-daily',params={'instance_id':task['instance_id']});assert filtered.status_code==200;scoped=filtered.json()['rows'][0]
 assert scoped['scope']=='instance_part_of_workspace_receipt' and len(scoped['records'])==2 and all(r['task_id']==task['id'] for r in scoped['records'])
 r=complete(client,task,note='公开分段业务结果已核查');assert r.status_code==200,r.text
 revised=collect(client);after=next(r for r in revised if r['id']==task_day['id']);assert after['business_tasks_awaiting_review']==0 and after['version']>task_day['version']
 rec=next(r for r in after['records'] if r['kind']=='segments');value=client.get('/api/audio-daily/'+after['id']+'/records/'+rec['key']).json();assert value['business_reviews'][-1]['note']=='公开分段业务结果已核查' and task['id'] in value['task_url']

def test_corrupt_original_and_receipt_cannot_be_presented_as_verified(client,monkeypatch):
 row=upload(client).json();assert process_once(row['job_id'],deliver=False);first=collect(client)[0]
 with Session(engine()) as db:
  evidence=db.get(Evidence,row['evidence_id']);path=settings().storage_dir/evidence.storage_key;raw=path.read_bytes();path.write_bytes(b'broken')
 broken=collect(client)[0];assert broken['verified_originals']==0 and len(broken['integrity_issue_ids'])==1
 base='/api/audio-daily/'+broken['id']+'/records/'+broken['records'][0]['key'];value=client.get(base).json();assert not value['integrity_verified'] and value['machine'] is None and client.get(base+'/original').status_code==409
 path.write_bytes(raw);restored=collect(client)[0];assert restored['verified_originals']==1 and not restored['integrity_issue_ids'] and restored['receipt_hash']==first['receipt_hash']
 # A closed old receipt still needs an operator recovery path.
 from generic_agents import audio_daily
 clock=now()+timedelta(days=13);monkeypatch.setattr(audio_daily,'now',lambda:clock)
 closed=collect(client)[0];assert not closed['provisional']
 with Session(engine()) as db:
  row=db.get(AudioDaily,first['id']);m=db.get(Memory,row.data['data_memory_id']);data=copy.deepcopy(m.data);data['unique_originals']=999;m.data=data;db.commit()
 assert client.get('/api/audio-daily').status_code==409 and client.get('/api/audio-daily/'+first['id']+'/export').status_code==409
 recovered=collect(client)[0];assert recovered['version']==closed['version']+1 and len(recovered['recovery_history'])==1 and recovered['receipt_hash']==closed['receipt_hash']
 with Session(engine()) as db:
  old=db.get(Memory,closed['data_memory_id']);assert old.status=='invalidated' and old.data['unique_originals']==999
  current=db.get(Memory,recovered['data_memory_id']);assert current.status=='candidate' and current.data['unique_originals']==1
 assert collect(client)==[recovered]
