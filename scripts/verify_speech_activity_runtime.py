"""Public audio only. Actual browser owns installation/upload/review; never microphone."""
import os,json,sys,hashlib,time
from pathlib import Path
from uuid import uuid4
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User,Workspace,Membership,CloudCall,SpecialistInstallation,SpeechActivityRun,Memory,Job,Capability,Evidence,SetupDraft,SetupRevision
from generic_agents.security import password_hash
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
OUT=ROOT/'docs/evidence/speech-activity-v06';OUT.mkdir(parents=True,exist_ok=True);ACCESS=ROOT/'.runtime/speech-activity-validation-access.json'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
phase=sys.argv[1]
if phase=='prepare':
 if not ACCESS.exists():
  a={'username':'speech_activity_'+uuid4().hex[:8],'password':uuid4().hex}
  with Session(engine()) as db:
   u=User(username=a['username'],password_hash=password_hash(a['password']));w=Workspace(name='公开语音区间核查');db.add_all([u,w]);db.flush();db.add(Membership(user_id=u.id,workspace_id=w.id,role='admin'));db.commit();a['workspace_id']=w.id;save('baseline',{'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all())})
  fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:json.dump(a,f)
 print('公开语音区间独立工作区已准备，实际界面承担安装和听音核查。');sys.exit()
a=json.loads(ACCESS.read_text())
with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
 for attempt in range(40):
  try:r=c.post('/login',json={k:a[k] for k in ('username','password')});break
  except httpx.ConnectError:
   if attempt==39:raise
   time.sleep(.25)
 r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
 def get(p):r=c.get(p);r.raise_for_status();return r.json()
 rows=get('/audio-activity');assert len(rows)==1;row=rows[0];assert row['status']=='succeeded';assert row['result']['activity']['inference_performed'] and row['result']['cloud_calls']==0
 if phase=='draft-check':
  assert row['review_draft']['decisions']==['speech'] and '公开语音区间工程核查' in row['review_draft']['note'] and not row['review'];save('draft-check',{'run_id':row['id'],'server_draft_saved':True,'note':row['review_draft']['note'],'confirmed':False});print('实际听音核查草稿已在服务器保存。');sys.exit()
 assert row['review'] and '公开语音区间工程核查' in row['review']['note'] and row['review_draft'] is None
 installs=get('/specialists/installations');assert len(installs)==1;installed=installs[0];assert installed['status']=='ready' and installed['capability_id'] and installed['smoke']['inference_performed']
 original=c.get('/audio-activity/'+row['id']+'/original');original.raise_for_status();export=get('/audio-activity/'+row['id']+'/export');assert export['record']['review']==row['review'];assert not export['domain_quality_verified'] and not export['entity_state_updated']
 sha=lambda b:hashlib.sha256(b).hexdigest();assert sha(original.content)==row['input_sha256'];hashes={'original':sha(original.content),'result':sha(json.dumps(row['result'],sort_keys=True,ensure_ascii=False).encode()),'export':sha(json.dumps(export,sort_keys=True,ensure_ascii=False).encode())}
 with Session(engine()) as db:
  def rec(cls):return [{'id':r.id,'version':getattr(r,'version',None),'status':getattr(r,'status',None),'data':r.data} for r in db.scalars(select(cls).where(cls.workspace_id==a['workspace_id']).order_by(cls.id))]
  records={cls.__name__:rec(cls) for cls in (SpeechActivityRun,SpecialistInstallation,Memory,Job,Capability,Evidence,SetupDraft,SetupRevision)}
  m=db.get(Memory,row['review']['candidate_receipt_id']);assert m.status=='candidate' and not m.data['entity_state_updated'];cap=db.get(Capability,installed['capability_id']);assert cap.status=='published' and cap.data['execution']['adapter']=='local_speech_activity'
  assert sorted(db.scalars(select(CloudCall.id)).all())==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
 value={'hashes':hashes,'records':records,'catalog':get('/audio-activity/catalog'),'cloud_calls':0,'microphone_tested':False,'domain_quality_verified':False,'browser_owns_review':True}
 if phase=='snapshot':save('before-restart',value);print('原音频、区间、核查及登记记录已读回，3份摘要就绪。')
 elif phase=='restart-check':
  assert value==json.loads((OUT/'before-restart.json').read_text());save('after-restart',value);print('重启后3份摘要及全部原音频、区间、核查、安装和能力记录一致。')
 else:raise ValueError('未知核查阶段')
