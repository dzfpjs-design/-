"""Read-only existing-account checkpoints; UI performs actual model and rule work."""
import json,sys,argparse
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.evidence import evidence_bytes
from generic_agents.worker import actor
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_signal_runtime import snapshot,originals
OUT=ROOT/'docs/evidence/candidate-signals-v06';ACCESS=ROOT/'.runtime/wms-access.json'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def read(name):return json.loads((OUT/(name+'.json')).read_text())
def prepare(a):
 if (OUT/'prepared.json').exists():raise RuntimeError('已有检查点，请续用，不重复准备')
 m.CandidateWatch.__table__.create(engine(),checkfirst=True)
 prior=json.loads((ROOT/'docs/evidence/signal-v06/prepared.json').read_text())
 with Session(engine()) as db:
  user=db.scalar(select(m.User).where(m.User.username==a['username']));p=actor(db,a['workspace_id'],user.id)
  instance=db.get(m.DomainInstance,prior['instance_id']);obj=db.get(m.Entity,prior['entity_id'])
  assert instance.workspace_id==a['workspace_id'] and instance.status=='partially_active' and obj.schema_capability_id==db.get(m.InstanceRelease,instance.active_release_id).package_id
  rows=db.scalars(select(m.Observation).where(m.Observation.workspace_id==a['workspace_id'],m.Observation.status=='processed',m.Observation.data['candidate_only'].as_boolean()==True,m.Observation.data['native_instance_id'].as_string().is_(None))).all()
  row=next(r for r in rows if r.data['extracted'].get('segmentation'))
  from generic_agents.observation_integrity import checked_report
  report=checked_report(db,p,row,allow_candidates=True);ev,content=evidence_bytes(db,p,row.data['evidence_ids'][0])
  (OUT/'source-image.png').write_bytes(content)
  value={'instance_id':instance.id,'entity_id':obj.id,'release_id':instance.active_release_id,'source_observation_id':row.id,
       'model_id':row.data['extractor_id'],'source_sha256':ev.sha256,'source_evidence_id':ev.id,'cloud_before':len(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id==a['workspace_id'])).all())}
 save('before',{'own':snapshot(a['workspace_id']),'other':snapshot(a['workspace_id'],True),'own_originals':originals(a['workspace_id']),'other_originals':originals(a['workspace_id'],True)})
 save('prepared',value);print(json.dumps({'prepared':True,'existing_account_and_instance':True,'local_model_and_original_verified':True,'new_accounts':0,'new_permissions':0}))
def verify(a,phase):
 import httpx
 prep=read('prepared');before=read('before');base='/workspace/instances/'+prep['instance_id']
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=60) as c:
  r=c.post('/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
  def get(path):
   r=c.get(path);r.raise_for_status();return r.json()
  try:
   watches=get(base+'/candidate-watches')['records'];assert len(watches)==1;watch=watches[0]
   assert watch['definition']['name']=='公开本地分割线索核查' and watch['definition']['threshold']==1 and watch['definition']['profile']['model_id']==prep['model_id']
   checks=get(base+'/candidate-watches/'+watch['id']+'/checks')['records'];assert len(checks)==1 and checks[0]['status']=='succeeded' and checks[0]['result']['delivery_status']=='received'
   signal=next(s for s in get(base+'/signals')['records'] if s.get('basis',{}).get('candidate_only'))
   assert signal['status']=='reviewed' and not signal['integrity_issue'] and signal['business_review']['decision']=='uncertain'
   assert signal['note']=='已对照本地原图及区域候选核查；这是公开工程图片线索，不能证明现场异常。'
   basis=signal['basis'];assert basis['reported_at'] is None and basis['extracted']['anomalies'][0]['value']>=1
   original=basis['original_model_result']['segmentation'];assert original['cloud_calls']==0 and original['image_sha256']==prep['source_sha256']
   assert original['backend']=='cpu' and original['inference_ms']>0 and basis['candidate_only']
   task=get('/workspace/tasks/'+signal['tasks'][0]['id']);assert task['status']=='workflow_completed' and not task['perception_review']['entity_state_updated']
   exported=get(base+'/signals/'+signal['id']+'/export');assert exported==read('browser-export')
  finally:c.post('/logout')
 with Session(engine()) as db:
  assert db.get(m.Entity,prep['entity_id']).version==1
  cloud=len(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id==a['workspace_id'])).all());assert cloud==prep['cloud_before']
  assert db.get(m.Memory,task['workflow_runtime']['data_receipt_id']).status=='candidate'
  event=db.get(m.Event,checks[0]['result']['event_id']);latency=(event.received_at-event.created_at).total_seconds()
 own=snapshot(a['workspace_id']);other=snapshot(a['workspace_id'],True)
 assert all(own.get(k)==v for k,v in before['own'].items()),'old own records changed'
 assert other==before['other'] and originals(a['workspace_id'],True)==before['other_originals'],'other workspace changed'
 raw=originals(a['workspace_id']);assert all(raw.get(k)==v for k,v in before['own_originals'].items())
 stable={'own':own,'own_originals':raw,'export':exported}
 if phase=='check':save('before-restart',stable)
 else:assert stable==read('before-restart'),'restart changed records or export'
 result={'passed':True,'own_records':len(own),'old_own_records_unchanged':len(before['own']),'other_records_unchanged':len(other),'own_originals':len(raw),'other_originals_unchanged':len(before['other_originals']),
    'model_backend':original['backend'],'inference_ms':original['inference_ms'],'candidate_count':len(original['masks']),'notification_delivery_seconds':latency,'new_cloud_calls':cloud-prep['cloud_before'],
    'entity_state_updated':False,'manual_decision':'uncertain','ordinary_device_worker_started':False}
 save('verification' if phase=='check' else 'restart-verification',result);print(json.dumps(result))
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','check','restart-check']);mode=parser.parse_args().phase
 OUT.mkdir(exist_ok=True);a=json.loads(ACCESS.read_text());prepare(a) if mode=='prepare' else verify(a,mode)
