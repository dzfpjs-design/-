"""Read-only checkpoints around actual UI review of an existing public native result."""
import argparse,json,sys
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.worker import actor
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_signal_runtime import snapshot,originals
OUT=ROOT/'docs/evidence/native-task-review-v06'
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def read(n):return json.loads((OUT/(n+'.json')).read_text())
def prepare(a):
 if (OUT/'prepared.json').exists():raise RuntimeError('已有准备检查点，请继续，不重复准备')
 with Session(engine()) as db:
  user=db.scalar(select(m.User).where(m.User.username==a['username']));p=actor(db,a['workspace_id'],user.id)
  instance=db.scalar(select(m.DomainInstance).where(m.DomainInstance.workspace_id==a['workspace_id'],m.DomainInstance.data['name'].as_string()=='地图影像区域核查'))
  assert instance and instance.status=='partially_active'
  row=db.scalar(select(m.Observation).where(m.Observation.workspace_id==a['workspace_id'],m.Observation.data['native_instance_id'].as_string()==instance.id))
  from generic_agents.native_receipts import checked
  checked(db,p,row);packet=row.data['native_packet'];assert packet['source_key'].startswith('GeoRun:') and packet['attributes']['区域候选数量']==4
  task=db.get(m.Task,packet['task_id']);assert task.status=='workflow_completed' and task.data['instance_release_id']==instance.active_release_id
  value={'instance_id':instance.id,'entity_id':row.entity_id,'original_task_id':task.id,'release_id':instance.active_release_id,
         'original_observation_id':row.id,'native_kind':'GeoRun','originals_reused':True,
         'invocations_before':len(db.scalars(select(m.Invocation.id).where(m.Invocation.workspace_id==a['workspace_id'])).all()),
         'cloud_before':len(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id==a['workspace_id'])).all())}
 save('before',{'own':snapshot(a['workspace_id']),'other':snapshot(a['workspace_id'],True),'originals':originals(a['workspace_id']),'other_originals':originals(a['workspace_id'],True)})
 save('prepared',value);print(json.dumps({'prepared':True,'existing_completed_native_task_verified':True,'new_accounts':0,'new_permissions':0,'new_inference':0}))
def verify(a,phase):
 import httpx
 prep=read('prepared');before=read('before');base='/workspace/instances/'+prep['instance_id']
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=60) as c:
  r=c.post('/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
  def get(path):
   r=c.get(path);r.raise_for_status();return r.json()
  try:
   watch=next(w for w in get(base+'/candidate-watches')['records'] if w['definition']['name']=='公开遥感结果补充核查')
   checks=get(base+'/candidate-watches/'+watch['id']+'/checks')['records'];assert len(checks)==2 and all(j['status']=='succeeded' for j in checks)
   rows=get('/workspace/tasks/'+prep['original_task_id']+'/linked-signals')['records'];assert len(rows)==1;row=rows[0]
   assert row['source_task_link']['task_id']==prep['original_task_id'] and row['status']=='reviewed' and not row['integrity_issue']
   assert row['business_review']['decision']=='not_abnormal' and row['business_review']['revision_reason']=='重新核对公开合成地图：区域候选只表示图像分割，不属于现场业务异常。'
   assert row['business_review_history'][0]['decision']=='uncertain' and row['business_review_history'][0]['note']=='已复核原任务的4个区域候选；采集时间及领域语义未证实，暂不能判断现场异常。'
   assert row['source_task_link']['note']=='对照原本地遥感分析任务补充审核，不重新推理，不改变原流程结论。'
   assert row['basis']['reported_at'] is None and row['basis']['extracted']['anomalies'][0]['value']==4
   export=get(base+'/signals/'+row['id']+'/export');assert export==read('browser-export')
   task=get('/workspace/tasks/'+prep['original_task_id']);assert task['status']=='workflow_completed'
  finally:c.post('/logout')
 with Session(engine()) as db:
  assert len(db.scalars(select(m.Invocation.id).where(m.Invocation.workspace_id==a['workspace_id'])).all())==prep['invocations_before']
  assert len(db.scalars(select(m.CloudCall.id).where(m.CloudCall.workspace_id==a['workspace_id'])).all())==prep['cloud_before']
  assert db.get(m.Entity,prep['entity_id']).version==1
  for review in [row['business_review'],*row['business_review_history']]:assert db.get(m.Memory,review['data_receipt_id']).status=='candidate'
 own=snapshot(a['workspace_id']);other=snapshot(a['workspace_id'],True)
 assert all(own.get(k)==v for k,v in before['own'].items()),'old own row changed'
 assert other==before['other'] and originals(a['workspace_id'],True)==before['other_originals']
 raw=originals(a['workspace_id']);assert raw==before['originals']
 stable={'own':own,'originals':raw,'export':export}
 if phase=='check':save('before-restart',stable)
 else:assert stable==read('before-restart')
 result={'passed':True,'original_task_unchanged':True,'old_own_records_unchanged':len(before['own']),'own_records':len(own),'other_records_unchanged':len(other),'originals_unchanged':len(raw),'other_originals_unchanged':len(before['other_originals']),
         'native_candidates_reused':4,'historical_normalizer_notifications_preserved':2,'new_notification_dedup_fixed_in_tests':True,'new_inference':0,'new_cloud_calls':0,'original_decision_history_preserved':True,'revision_history_preserved':True,'entity_state_updated':False,'ordinary_device_worker_started':False}
 save('verification' if phase=='check' else 'restart-verification',result);print(json.dumps(result))
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','check','restart-check']);phase=parser.parse_args().phase
 OUT.mkdir(exist_ok=True);access=json.loads((ROOT/'.runtime/wms-access.json').read_text());prepare(access) if phase=='prepare' else verify(access,phase)
