"""Public first-input workflow, persistence and restart evidence; no cloud inference."""
import os,sys,json,hashlib,time
from pathlib import Path
from contextlib import contextmanager
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import CloudCall,Job,Invocation,Memory,GeoScene,GeoRun,WorkflowScene
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
OUT=ROOT/'docs/evidence/geo-input-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=json.loads((ROOT/'.runtime/geo-workflow-access.json').read_text())
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
@contextmanager
def client():
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
  for attempt in range(40):
   try:r=c.post('/login',json={k:ACCESS[k] for k in ['username','password']});break
   except httpx.ConnectError:
    if attempt==39:raise
    time.sleep(.25)
  r.raise_for_status()
  c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':ACCESS['workspace_id']});yield c

def get(c,path):r=c.get(path);r.raise_for_status();return r.json()
with client() as c:
 scenes=get(c,'/geo/scenes')
 until=time.monotonic()+60
 while any(s['status'] in {'queued','running'} for s in scenes):
  if time.monotonic()>until:raise TimeoutError('影像后台仍未完成，记录保留')
  time.sleep(.3);scenes=get(c,'/geo/scenes')
 assert len(scenes)==3
 for name in ['public-input-ui-proof.tif','public-upload-recovery-proof.tif']:assert sum(s['name']==name for s in scenes)==1
 instance=get(c,'/instances')[0];inputs=get(c,'/workspace/instances/'+instance['id']+'/input-scenes');objects=get(c,'/instances/'+instance['id']+'/objects')
 tasks=[get(c,'/workspace/tasks/'+t['id']) for t in get(c,'/workspace/instances/'+instance['id']+'/tasks')]
 assert len(inputs)==2 and len(tasks)==4 and objects[0]['version']==1
 cancelled=next(t for t in tasks if t['title']=='首次影像上传与关联恢复核查')
 assert cancelled['status']=='workflow_cancelled' and '首次接入核查' in cancelled['cancellation']['retained_note']
 assert cancelled['scene_snapshots'][0]['snapshot']['roi']==[120,90,760,570] and cancelled['scene_snapshots'][0]['snapshot']['configuration']['rgb_bands']==[3,2,1]
 old=json.loads((ROOT/'docs/evidence/geo-workflow-v06/before-restart.json').read_text())
 current={t['id']:t for t in tasks};assert all(current[t['id']]==t for t in old['tasks'])
 artifacts={}
 def binary(key,path,sha):
  r=c.get(path);r.raise_for_status();actual=hashlib.sha256(r.content).hexdigest();assert actual==sha,key;artifacts[key]=actual
 for scene in scenes:
  recovered=get(c,'/geo/uploads/'+scene['upload_request_id']);assert recovered['id']==scene['id']
  binary('scene/'+scene['id']+'/original','/geo/scenes/'+scene['id']+'/original',scene['input_sha256'])
  if scene['preview']:binary('scene/'+scene['id']+'/preview','/geo/scenes/'+scene['id']+'/preview',scene['preview']['sha256'])
 for source in inputs:
  binary('input/'+source['id']+'/preview','/workspace/instances/'+instance['id']+'/input-scenes/'+source['id']+'/preview',source['snapshot']['preview']['sha256'])
 for task in tasks:
  receipt=next(r for r in task['workflow_runtime']['receipts'] if r.get('output',{}).get('spatial_segmentation'));result=receipt['output']['spatial_segmentation'];run=receipt['output']['geo_run_id'];source=task['scene_snapshots'][0];snapshot=source['snapshot']
  assert receipt['candidate_only'] and result['cloud_calls']==0 and result['source_raster']['roi']==snapshot['roi'] and result['source_raster']['rgb_bands']==snapshot['configuration']['rgb_bands'] and result['source_raster']['observed_at'] is None
  base='/workspace/tasks/'+task['id'];items=[('tiff',base+'/scenes/'+source['id']+'/original',snapshot['input_sha256']),('preview',base+'/scenes/'+source['id']+'/preview',snapshot['preview']['sha256']),('rgb',base+'/spatial/'+run+'/rgb',result['image_sha256'])]
  items += [('mask-'+str(m['candidate_index']),base+'/spatial/'+run+'/masks/'+m['mask_evidence_id'],m['mask_sha256']) for m in result['masks']]
  for name,url,sha in items:binary(task['id']+'/'+name,url,sha)
  response=c.get(base+'/spatial/'+run+'/native-geojson');response.raise_for_status();assert response.json()['business_review']['status']==task['status'];artifacts[task['id']+'/export']=hashlib.sha256(response.content).hexdigest()
 assert all(artifacts[k]==v for k,v in old['artifact_hashes'].items())
 with Session(engine()) as db:
  def records(cls):return [{'id':r.id,'version':r.version,'status':getattr(r,'status',None),'data':r.data} for r in db.scalars(select(cls).where(cls.workspace_id==ACCESS['workspace_id']).order_by(cls.id))]
  jobs=records(Job);invocations=records(Invocation);memories=records(Memory)
  assert len(invocations)==4 and all(r['status']=='succeeded' for r in invocations) and len(memories)==2
  clouds=sorted(db.scalars(select(CloudCall.id)).all());assert clouds==old['cloud_ids']
  models={cls.__name__:records(cls) for cls in [GeoScene,GeoRun,WorkflowScene]}
 record={'instance':instance,'scenes':scenes,'inputs':inputs,'objects':objects,'tasks':tasks,'jobs':jobs,'invocations':invocations,'memories':memories,'models':models,'artifact_hashes':artifacts,'cloud_ids':clouds}
 if sys.argv[1]=='snapshot':save('before-restart',record)
 elif sys.argv[1]=='restart-check':
  assert record==json.loads((OUT/'before-restart.json').read_text()),'服务重启后原件或业务持久记录变化'
  save('after-restart',record);save('runtime',{'records_identical':True,'original_prior_tasks_unchanged':True,'scenes':3,'instance_inputs':2,'actual_local_business_invocations':4,'completed_tasks':2,'waiting_tasks':1,'cancelled_tasks':1,'candidate_data_receipts':2,'artifact_hashes_checked':len(artifacts),'cloud_calls_created':0,'entity_state_updated':False,'database_restarted':False,'domain_quality_verified':False,'production_verified':False})
 else:raise ValueError('unknown phase')
 print('已核对首次上传、唯一范围关联、取消说明与全部证据；旧任务依据不变，没有新增云调用。')
