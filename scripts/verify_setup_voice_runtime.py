"""Public-only local voice setup UI proof; no microphone or cloud calls."""
import os,json,sys,hashlib,time
from pathlib import Path
from uuid import uuid4
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User,Workspace,Membership,CloudCall,SetupDraft,SetupRevision,AudioRun,Memory,Evidence,Job,SpecialistInstallation
from generic_agents.security import password_hash
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
OUT=ROOT/'docs/evidence/setup-voice-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=ROOT/'.runtime/setup-voice-validation-access.json'
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
phase=sys.argv[1]
if phase=='prepare':
 if not ACCESS.exists():
  a={'username':'setup_voice_'+uuid4().hex[:8],'password':uuid4().hex}
  with Session(engine()) as db:
   u=User(username=a['username'],password_hash=password_hash(a['password']));w=Workspace(name='公开音频初始化验证');db.add_all([u,w]);db.flush();db.add(Membership(user_id=u.id,workspace_id=w.id,role='admin'));db.commit();a['workspace_id']=w.id;save('baseline',{'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all())})
  fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:json.dump(a,f)
 print('独立公开语音初始化工作区已准备。');sys.exit()
a=json.loads(ACCESS.read_text())
with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
 for attempt in range(40):
  try:r=c.post('/login',json={k:a[k] for k in ('username','password')});break
  except httpx.ConnectError:
   if attempt==39:raise
   time.sleep(.25)
 r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
 def get(path):r=c.get(path);r.raise_for_status();return r.json()
 draft=get('/setup/draft');rows=get('/audio');assert len(rows)==1;audio=rows[0];assert audio['status']=='succeeded';assert audio['installation']['backend']=='cpu';assert audio['result']['cloud_calls']==0
 if phase=='draft-check':
  assert audio['review_draft'] and '公开语音工程验证' in audio['review_draft']['text'];save('draft-check',{'audio_id':audio['id'],'text':audio['review_draft']['text'],'server_version':audio['version'],'configuration_unchanged':not draft['configuration'].get('voiceInputs'),'cloud_calls':0});print('实际界面核对草稿已在服务器保存，尚未应用。');sys.exit()
 assert audio['review'] and '公开语音工程验证' in audio['review']['text'] and audio['review_draft'] is None
 assert draft['configuration']['instanceProfile']['goal']=='保留原目标\n'+audio['review']['text'];refs=draft['configuration']['voiceInputs'];assert len(refs)==1 and refs[0]['audio_run_id']==audio['id'];assert refs[0]==next(iter(audio['setup_applications'].values()))['reference'];assert refs[0]['review_hash']
 original=c.get('/audio/'+audio['id']+'/original');original.raise_for_status();export=get('/audio/'+audio['id']+'/export');assert export['record']['review']==audio['review']
 sha=lambda b:hashlib.sha256(b).hexdigest()
 hashes={'original':sha(original.content),'result':sha(json.dumps(audio['result'],sort_keys=True,ensure_ascii=False).encode()),'export':sha(json.dumps(export,sort_keys=True,ensure_ascii=False).encode()),'configuration':sha(json.dumps(draft,sort_keys=True,ensure_ascii=False).encode())}
 with Session(engine()) as db:
  def rec(cls):return [{'id':r.id,'version':getattr(r,'version',None),'status':getattr(r,'status',None),'data':r.data} for r in db.scalars(select(cls).where(cls.workspace_id==a['workspace_id']).order_by(cls.id))]
  records={cls.__name__:rec(cls) for cls in (SetupDraft,SetupRevision,AudioRun,Memory,Evidence,Job,SpecialistInstallation)}
  assert sorted(db.scalars(select(CloudCall.id)).all())==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
 value={'hashes':hashes,'records':records,'cloud_calls':0,'microphone_tested':False,'domain_quality_verified':False,'browser_owns_application':True}
 if phase=='snapshot':save('before-restart',value);print('语音依据、人工核对及新配置版本已保存，4份摘要就绪。')
 elif phase=='restart-check':
  assert value==json.loads((OUT/'before-restart.json').read_text());save('after-restart',value);print('重启后4份摘要及全部语音/配置/核对/任务/安装记录一致。')
 else:raise ValueError('未知验证阶段')
