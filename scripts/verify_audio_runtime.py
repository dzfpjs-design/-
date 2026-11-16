"""Own public ASR workspace; browser owns upload/review, verifier only reads evidence."""
import os,json,sys,hashlib,time
from pathlib import Path
from uuid import uuid4
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User,Workspace,Membership,AudioRun,Memory,Evidence,CloudCall,Job
from generic_agents.security import password_hash
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT);OUT=ROOT/'docs/evidence/audio-v06';OUT.mkdir(parents=True,exist_ok=True);ACCESS=ROOT/'.runtime/audio-validation-access.json'
def save(name,x):(OUT/(name+'.json')).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
phase=sys.argv[1]
if phase=='prepare':
 if not ACCESS.exists():
  a={'username':'audio_validation_'+uuid4().hex[:8],'password':uuid4().hex}
  with Session(engine()) as db:
   u=User(username=a['username'],password_hash=password_hash(a['password']));w=Workspace(name='公开中文音频本地核查');db.add_all([u,w]);db.flush();db.add(Membership(user_id=u.id,workspace_id=w.id,role='admin'));db.commit();a['workspace_id']=w.id
   save('baseline',{'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all())})
  fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:json.dump(a,f)
 print('隔离公开音频工作区已准备；等待实际界面上传和核对。');sys.exit()
a=json.loads(ACCESS.read_text())
with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
 for attempt in range(40):
  try:r=c.post('/login',json={k:a[k] for k in ('username','password')});break
  except httpx.ConnectError:
   if attempt==39:raise
   time.sleep(.25)
 r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
 def get(path):r=c.get(path);r.raise_for_status();return r.json()
 rows=get('/audio');assert len(rows)==1;row=rows[0];assert row['status']=='succeeded' and row['review'] and '工程验证' in row['review']['text']
 assert row['result']['transcription']['inference_performed'] and '9点' in row['result']['transcription']['text'] and '5点' in row['result']['transcription']['text']
 assert not row['result']['transcription']['word_timestamps'] and not row['result']['transcription']['environment_classifier_used'] and row['result']['cloud_calls']==0
 assert get('/audio/requests/'+row['request_id']+'?expected_hash='+row['request_hash'])==row
 original=c.get('/audio/'+row['id']+'/original');original.raise_for_status();original_sha=hashlib.sha256(original.content).hexdigest();assert original_sha==row['input_sha256']=='0e64de19e4ff9a02e682955c9112f32d2317cfdbb5bc2f3504664044c993f195'
 exported=get('/audio/'+row['id']+'/export');assert exported['record']['review']==row['review']
 hashes={'original':original_sha,'result':hashlib.sha256(json.dumps(row['result'],sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'export':hashlib.sha256(json.dumps(exported,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
 with Session(engine()) as db:
  def records(cls):return [{'id':r.id,'version':getattr(r,'version',None),'status':getattr(r,'status',None),'data':r.data} for r in db.scalars(select(cls).where(cls.workspace_id==a['workspace_id']).order_by(cls.id))]
  records_saved={cls.__name__:records(cls) for cls in [AudioRun,Memory,Evidence,Job]};m=db.get(Memory,row['review']['candidate_receipt_id']);assert m.status=='candidate' and not m.data['entity_state_updated']
  clouds=sorted(db.scalars(select(CloudCall.id)).all());assert clouds==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
 record={'rows':rows,'records':records_saved,'hashes':hashes,'catalog':get('/audio/catalog'),'cloud_ids':clouds}
 if phase=='snapshot':save('before-restart',record)
 elif phase=='restart-check':
  previous=json.loads((OUT/'before-restart.json').read_text())
  # Additive catalog fields do not change previously saved audio records or node guarantees.
  comparable={**record,'catalog':{k:record['catalog'][k] for k in previous['catalog']}}
  assert comparable==previous;save('after-restart',record);save('runtime',{'records_identical':True,'hashes_checked':3,'actual_local_asr':True,'human_review_saved':True,'candidate_receipt_saved':True,'new_cloud_calls':0,'entity_state_updated':False,'domain_quality_verified':False,'production_verified':False,'database_restarted':False})
 else:raise ValueError('unknown phase')
 print('真实转写、人工核对、原件与候选回执读回一致；无新增云调用。')
