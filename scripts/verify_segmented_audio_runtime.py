"""Actual public-only multi-interval UI proof, restart and original evidence integrity."""
import os,json,sys,time,hashlib
from pathlib import Path
from uuid import uuid4
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User,Workspace,Membership,CloudCall,SpeechActivityRun,SegmentedAudioRun,SpecialistInstallation,Capability,Memory,Evidence,Job,SetupDraft,SetupRevision
from generic_agents.security import password_hash
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT);OUT=ROOT/'docs/evidence/segmented-audio-v06';OUT.mkdir(parents=True,exist_ok=True);ACCESS=ROOT/'.runtime/segmented-audio-validation-access.json'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
phase=sys.argv[1]
if phase=='prepare':
 if not ACCESS.exists():
  a={'username':'segmented_audio_'+uuid4().hex[:8],'password':uuid4().hex}
  with Session(engine()) as db:
   u=User(username=a['username'],password_hash=password_hash(a['password']));w=Workspace(name='公开双区间语音核查');db.add_all([u,w]);db.flush();db.add(Membership(user_id=u.id,workspace_id=w.id,role='admin'));db.commit();a['workspace_id']=w.id;save('baseline',{'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all())})
  fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:json.dump(a,f)
 print('公开双区间独立工作区准备完毕，界面负责安装、处理和核查。');sys.exit()
a=json.loads(ACCESS.read_text())
with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=45) as c:
 for attempt in range(40):
  try:r=c.post('/login',json={k:a[k] for k in ('username','password')});break
  except httpx.ConnectError:
   if attempt==39:raise
   time.sleep(.25)
 r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
 def get(path):r=c.get(path);r.raise_for_status();return r.json()
 sources=get('/audio-activity');rows=get('/audio-segments');assert len(sources)==1 and len(rows)==1;source=sources[0];row=rows[0];assert source['status']=='succeeded' and row['status']=='succeeded';assert len(row['segments'])==2 and row['receipt']['model_load_count']==1 and row['result']['vad_used'] and row['result']['cloud_calls']==0
 assert all('9点' in s['text'] and '5点' in s['text'] for s in row['segments'])
 if phase=='draft-check':
  assert row['review_draft'] and len(row['review_draft']['texts'])==2 and '公开双区间工程核查' in row['review_draft']['note'] and not row['review'];save('draft-check',{'server_draft_saved':True,'texts':row['review_draft']['texts'],'note':row['review_draft']['note'],'confirmed':False});print('真实页面的逐段核对草稿已保存到服务器。');sys.exit()
 assert row['review'] and len(row['review']['texts'])==2 and '公开双区间工程核查' in row['review']['note'] and row['review_draft'] is None
 original=c.get('/audio-activity/'+source['id']+'/original');original.raise_for_status();export=get('/audio-segments/'+row['id']+'/export');assert export['record']['review']==row['review'] and not export['partial'];sha=lambda b:hashlib.sha256(b).hexdigest();assert sha(original.content)==sha((ROOT/'.runtime/vad-bootstrap/two-public-zh.wav').read_bytes())==source['input_sha256']
 hashes={'original':sha(original.content),'vad_result':sha(json.dumps(source['result'],sort_keys=True,ensure_ascii=False).encode()),'segmented_result':sha(json.dumps(row['result'],sort_keys=True,ensure_ascii=False).encode()),'export':sha(json.dumps(export,sort_keys=True,ensure_ascii=False).encode())}
 with Session(engine()) as db:
  def records(cls):return [{'id':r.id,'version':getattr(r,'version',None),'status':getattr(r,'status',None),'data':r.data} for r in db.scalars(select(cls).where(cls.workspace_id==a['workspace_id']).order_by(cls.id))]
  values={cls.__name__:records(cls) for cls in (SpeechActivityRun,SegmentedAudioRun,SpecialistInstallation,Capability,Memory,Evidence,Job,SetupDraft,SetupRevision)};m=db.get(Memory,row['review']['candidate_receipt_id']);assert m.status=='candidate' and not m.data['entity_state_updated'];caps=list(db.scalars(select(Capability).where(Capability.workspace_id==a['workspace_id'],Capability.status=='published')));assert any(cap.data['execution']['adapter']=='local_segmented_audio' for cap in caps)
  assert sorted(db.scalars(select(CloudCall.id)).all())==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
 value={'records':values,'hashes':hashes,'cloud_calls':0,'catalog':get('/audio-segments/catalog'),'domain_quality_verified':False,'production_verified':False,'microphone_tested':False,'business_task_binding_verified':False,'source_kind':'public_clip_repeated_with_explicit_synthetic_silence'}
 if phase=='snapshot':save('before-restart',value);print('两段转写及核对、登记、任务、配置记录已读回，4份摘要就绪。')
 elif phase=='restart-check':
  assert value==json.loads((OUT/'before-restart.json').read_text());save('after-restart',value);print('重启后4份摘要及全部逐段机器/人工/原件/能力记录完全一致。')
 else:raise ValueError('unknown phase')
