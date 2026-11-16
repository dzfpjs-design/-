"""Isolated public sample; UI owns package installation, registration and review."""
import os,json,sys,time,hashlib
from pathlib import Path
from uuid import uuid4
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User,Workspace,Membership,CloudCall,SpecialistInstallation,AudioRun,Memory,Job,Capability,Evidence
from generic_agents.security import password_hash
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
OUT=ROOT/'docs/evidence/audio-package-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=ROOT/'.runtime/audio-package-validation-access.json'
def save(name,data):(OUT/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
phase=sys.argv[1]
if phase=='prepare':
 if not ACCESS.exists():
  a={'username':'audio_package_'+uuid4().hex[:8],'password':uuid4().hex}
  with Session(engine()) as db:
   u=User(username=a['username'],password_hash=password_hash(a['password']));w=Workspace(name='公开语音专业包安装核查');db.add_all([u,w]);db.flush();db.add(Membership(user_id=u.id,workspace_id=w.id,role='admin'));db.commit();a['workspace_id']=w.id;save('baseline',{'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all())})
  fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:json.dump(a,f)
 print('隔离公开安装工作区已准备，等待实际界面安装、登记、上传与核对。');sys.exit()
a=json.loads(ACCESS.read_text())
with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
 for attempt in range(40):
  try:r=c.post('/login',json={k:a[k] for k in ('username','password')});break
  except httpx.ConnectError:
   if attempt==39:raise
   time.sleep(.25)
 r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
 def get(path):r=c.get(path);r.raise_for_status();return r.json()
 installs=get('/specialists/installations');assert len(installs)==1;install=installs[0]
 assert install['pack_id']=='sensevoice-small-v1' and install['status']=='ready' and install['capability_id'];assert install['smoke']['inference_performed'] and install['smoke']['cloud_calls']==0 and install['smoke']['probe_kind']=='synthetic_non_speech_engineering_only'
 rows=get('/audio');assert len(rows)==1;row=rows[0];assert row['status']=='succeeded' and row['review'] and '工程验证' in row['review']['text'];assert row['installation']['installation_id']==install['id'] and row['installation']['generation']==install['generation']
 assert row['configuration']['language']=='zh' and row['result']['transcription']['inference_performed'];assert row['result']['cloud_calls']==0
 original=c.get('/audio/'+row['id']+'/original');original.raise_for_status();assert hashlib.sha256(original.content).hexdigest()==row['input_sha256'];exported=get('/audio/'+row['id']+'/export');assert exported['record']['installation']==row['installation']
 hashes={'original':hashlib.sha256(original.content).hexdigest(),'result':hashlib.sha256(json.dumps(row['result'],sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'export':hashlib.sha256(json.dumps(exported,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
 with Session(engine()) as db:
  def records(cls):return [{'id':r.id,'version':getattr(r,'version',None),'status':getattr(r,'status',None),'data':r.data} for r in db.scalars(select(cls).where(cls.workspace_id==a['workspace_id']).order_by(cls.id))]
  saved={cls.__name__:records(cls) for cls in (SpecialistInstallation,AudioRun,Memory,Job,Capability,Evidence)};m=db.get(Memory,row['review']['candidate_receipt_id']);assert m.status=='candidate' and not m.data['entity_state_updated'];cap=db.get(Capability,install['capability_id']);assert cap.status=='published' and cap.data['execution']['adapter']=='local_audio'
  clouds=sorted(db.scalars(select(CloudCall.id)).all());assert clouds==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
 record={'rows':rows,'installations':installs,'records':saved,'hashes':hashes,'catalog':get('/audio/catalog'),'cloud_ids':clouds}
 assert record['catalog']['workspace_prepared'] and record['catalog']['node_prepared']
 if phase=='snapshot':save('before-restart',record)
 elif phase=='restart-check':
  assert record==json.loads((OUT/'before-restart.json').read_text());save('after-restart',record);save('runtime',{'records_identical':True,'hashes_checked':3,'actual_local_package_probe':True,'registered_audio_capability':True,'upload_installation_provenance_saved':True,'actual_local_asr':True,'human_review_saved':True,'new_cloud_calls':0,'domain_quality_verified':False,'production_verified':False,'multiple_model_versions_verified':False,'database_restarted':False})
 else:raise ValueError('unknown phase')
 print('专业包实际安装检查、语音能力、原音频及人工核对已读回，无新增云调用。')
