"""Pinned real ONNX, reference parity, durable scoped reviews and recovery."""
import copy,hashlib,io,json,random,runpy,subprocess,wave
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from generic_agents.speech_activity import speech_segments
from generic_agents.local_vad import call,validate,RECIPE_HASH,RECIPE
from generic_agents.models import SpeechActivityRun,Memory,Evidence,Membership
from generic_agents.db import engine
from generic_agents.worker import process_once
from generic_agents.config import settings
PACK='silero-vad-onnx-v1'
reference=runpy.run_path('tests/fixtures/silero_vad_reference.py')['get_speech_timestamps_from_probs']
def silence(channels=1,seconds=.5):
 b=io.BytesIO()
 with wave.open(b,'wb') as f:f.setnchannels(channels);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0'*(int(16000*seconds)*2*channels))
 return b.getvalue()
def install(client):
 r=client.post('/api/specialists/installations',json={'pack_id':PACK});assert r.status_code==201,r.text
 row=r.json();assert process_once(row['job_id'],deliver=False)
 row=next(x for x in client.get('/api/specialists/installations').json() if x['id']==row['id']);assert row['status']=='ready',row.get('error');return row
def upload(client,identity=None,content=None,configuration=None):
 return client.post('/api/audio-activity',files={'file':('公开普通话.mp3',content or Path('.runtime/audio-bootstrap/public-zh.mp3').read_bytes(),'audio/mpeg')},data={'request_id':identity or str(uuid4()),'configuration':json.dumps(configuration or {'channel':'mono','threshold':.5})})
def done(client):
 install(client);r=upload(client);assert r.status_code==201,r.text;row=r.json();assert process_once(row['job_id'],deliver=False)
 path='/api/audio-activity/'+row['id'];r=client.get(path);assert r.status_code==200,r.text;row=r.json();assert row['status']=='succeeded',row.get('error');return path,row

def test_official_fixed_default_aggregation_parity():
 cases=[[0.]*176,[1.]*176,[0.]*4+[.8]*8+[0.]*10,[.8]*7+[0.]*5+[.8]*12+[0.]*6,[.8]*8+[.4]*4+[.8]*8+[0.]*6]
 rng=random.Random(937)
 for _ in range(120):cases.append([rng.choice([0.,.1,.34,.35,.4,.5,.7,1.]) for i in range(rng.randint(1,1875))])
 for threshold in [.1,.5,.9]:
  for scores in cases:
   length=len(scores)*512-rng.randrange(0,512)
   assert speech_segments(scores,length,threshold)==reference(scores,threshold=threshold,audio_length_samples=length)

def test_actual_onnx_public_clip_and_silence_preserve_boundaries():
 content=Path('.runtime/audio-bootstrap/public-zh.mp3').read_bytes();r=call(content,{'channel':'mono','threshold':.5});a=r['activity']
 assert r['deployment']=='local' and r['cloud_calls']==0 and r['providers']==['CPUExecutionProvider']
 assert len(a['intervals'])==1 and a['intervals'][0]['start_sample']==11808 and a['intervals'][0]['end_sample']==82912
 assert a['inference_performed'] and not a['transcription_performed'] and not a['environment_classifier_used']
 quiet=call(silence(),{'channel':'mono','threshold':.5});assert quiet['activity']['inference_performed'] and not quiet['activity']['intervals'] and quiet['activity']['environment_audio_preserved']
 for mutate in [lambda x:x['activity']['intervals'][0].update(word_timestamps=True),lambda x:x['activity'].update(transcription_performed=True),lambda x:x.update(cloud_calls=1),lambda x:x['activity']['probabilities'].__setitem__(0,float('nan'))]:
  bad=copy.deepcopy(r);mutate(bad)
  with pytest.raises(ValueError):validate(bad,content,{'channel':'mono','threshold':.5})

def test_native_subprocess_network_access_is_denied():
 code="import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); assert r==1, r; print('native_os_network_denied')"
 r=subprocess.run(['/usr/bin/sandbox-exec','-p','(version 1)(allow default)(deny network*)',str(settings().vad_python.absolute()),'-c',code],capture_output=True,text=True,timeout=5)
 assert r.returncode==0 and r.stdout.strip()=='native_os_network_denied',r.stderr[-500:]

def test_actual_install_register_upload_review_draft_and_integrity(client):
 assert client.get('/api/audio-activity/catalog').json()['workspace_prepared'] is False
 assert upload(client).status_code==409
 row=install(client);assert row['recipe_hash']==RECIPE_HASH and row['smoke']['inference_performed'] and row['smoke']['cloud_calls']==0
 reg=client.post('/api/specialists/installations/'+row['id']+'/register',json={'version':row['version']});assert reg.status_code==200,reg.text
 assert reg.json()['capability_id'] and client.post('/api/specialists/installations/'+row['id']+'/samples',files={'file':('x.png',b'no','image/png')}).status_code==422
 identity=str(uuid4());r=upload(client,identity);assert r.status_code==201,r.text;row=r.json();assert upload(client,identity).json()['id']==row['id'];assert process_once(row['job_id'],deliver=False)
 path='/api/audio-activity/'+row['id'];row=client.get(path).json();assert row['status']=='succeeded',row.get('error')
 assert client.get('/api/audio-activity/requests/'+identity,params={'expected_hash':row['request_hash']}).json()['id']==row['id']
 draft={'version':row['version'],'decisions':['speech'],'note':'先听原件，再核对'};r=client.put(path+'/review-draft',json=draft);assert r.status_code==200,r.text;saved=r.json();assert client.get(path).json()['review_draft']['note']==draft['note']
 assert client.put(path+'/review',json={**draft,'confirmed':True}).status_code==409
 r=client.put(path+'/review',json={**draft,'version':saved['version'],'confirmed':True});assert r.status_code==200,r.text;final=r.json()
 assert final['review_draft'] is None and final['result']==row['result'] and client.get(path+'/export').json()['record']['review']['note']==draft['note']
 content=Path('.runtime/audio-bootstrap/public-zh.mp3').read_bytes();assert client.get(path+'/original').content==content
 with Session(engine()) as db:
  m=db.get(Memory,final['review']['candidate_receipt_id']);assert m.status=='candidate' and not m.data['entity_state_updated']
  ev=db.get(Evidence,row['evidence_id']);f=settings().storage_dir/ev.storage_key;f.write_bytes(b'broken')
 assert client.get(path+'/export').status_code==409
 f.write_bytes(content)

def test_cancel_retry_same_original_and_stale_version(client):
 install(client);identity=str(uuid4());r=upload(client,identity);row=r.json();path='/api/audio-activity/'+row['id']
 assert upload(client,identity,content=silence()).status_code==409
 cancelled=client.post(path+'/cancel',json={'version':row['version']});assert cancelled.status_code==200
 assert process_once(row['job_id'],deliver=False);assert client.get(path).json()['status']=='cancelled'
 assert client.post(path+'/retry',json={'version':row['version']}).status_code==409
 retry=client.post(path+'/retry',json={'version':cancelled.json()['version']});assert retry.status_code==200,retry.text;assert retry.json()['generation']==2 and retry.json()['evidence_id']==row['evidence_id']
 assert process_once(retry.json()['job_id'],deliver=False);assert client.get(path).json()['status']=='succeeded'
 assert client.get('/api/audio-activity/requests/'+identity,params={'expected_hash':'bad'}).status_code==409

def test_no_implicit_channels_unbounded_audio_cloud_or_unverified_backend(client):
 for body in [{'pack_id':PACK,'source':'mirror'},{'pack_id':PACK,'backend':'mps'}]:assert client.post('/api/specialists/installations',json=body).status_code==422
 with pytest.raises(ValueError,match='多个通道'):call(silence(channels=2),{'channel':'mono','threshold':.5})
 with pytest.raises(ValueError,match='60秒'):call(silence(seconds=61),{'channel':'mono','threshold':.5})
 for cfg in [{'channel':'mono','threshold':float('nan')},{'channel':'auto','threshold':.5},{'channel':'mono','threshold':.95}]:assert upload(client,configuration=cfg).status_code==422
 assert upload(client,content=b'invalid').status_code==415

def test_review_permissions_and_cross_workspace(client,account):
 path,row=done(client)
 from generic_agents.models import Workspace
 with Session(engine()) as db:
  m=db.query(Membership).filter_by(workspace_id=account[2]).one();m.role='viewer';other=Workspace(name='other');db.add(other);db.commit();other_id=other.id
 assert client.put(path+'/review',json={'version':row['version'],'decisions':['speech'],'note':'x','confirmed':True}).status_code==403
 client.headers['X-Workspace-ID']=other_id;assert client.get(path).status_code==403

def test_registered_gateway_runs_real_model_and_blocks_installation_drift(client,account):
 installation=install(client);r=client.post('/api/specialists/installations/'+installation['id']+'/register',json={'version':installation['version']});assert r.status_code==200,r.text;cap_id=r.json()['capability_id']
 r=upload(client);assert r.status_code==201,r.text;row=r.json()
 from generic_agents.gateway import invoke
 from generic_agents.security import Principal
 from generic_agents.models import SpecialistInstallation
 with Session(engine()) as db:
  member=db.query(Membership).filter_by(workspace_id=account[2]).one();p=Principal(member.user_id,member.workspace_id,'admin','test')
  result=invoke(db,p,cap_id,'perception',{'evidence_ids':[row['evidence_id']],'channel':'mono','threshold':.5},'vad-gateway-actual')
  assert result.status=='succeeded' and result.data['output']['speech_activity']['activity']['inference_performed'] and result.data['output']['anomalies']==[]
  assert result.data['output']['attributes']['speech_candidate_count']==1
  current=db.get(SpecialistInstallation,installation['id']);current.data={**current.data,'generation':2};db.commit()
  result=invoke(db,p,cap_id,'perception',{'evidence_ids':[row['evidence_id']]},'vad-gateway-drift');assert result.status=='blocked_evidence'
