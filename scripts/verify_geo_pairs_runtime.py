"""Compare actual API, immutable artifacts and existing records across an owned restart."""
import os,sys,json,hashlib,time
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.models import GeoScene,GeoRun,WorkflowScene,GeoPair,Job,Invocation,Memory,CloudCall
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
OUT=ROOT/'docs/evidence/geo-pair-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=json.loads((ROOT/'.runtime/geo-workflow-access.json').read_text())
OLD=json.loads((ROOT/'docs/evidence/geo-input-v06/before-restart.json').read_text())
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
 for attempt in range(40):
  try:r=c.post('/login',json={k:ACCESS[k] for k in ('username','password')});break
  except httpx.ConnectError:
   if attempt==39:raise
   time.sleep(.25)
 r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':ACCESS['workspace_id']})
 def get(path):r=c.get(path);r.raise_for_status();return r.json()
 def checked(key,path,sha=None):
  r=c.get(path);r.raise_for_status();actual=hashlib.sha256(r.content).hexdigest()
  if sha:assert actual==sha,key
  artifacts[key]=actual
 pairs=get('/geo/pairs');assert len(pairs)==1
 row=pairs[0];assert row['status']=='succeeded' and row['review']['decision']=='uncertain'
 assert '不作为遥感业务变化结论' in row['review']['note']
 assert row['result']['learned_model'] is False and row['result']['cloud_calls']==0
 assert row['result']['total_pixels']==76800 and row['result']['common_valid_pixels']==73774 and row['result']['unknown_pixels']==3026 and row['result']['candidate_pixels']==1993
 recovered=get('/geo/pairs/requests/'+row['request_id']+'?expected_hash='+row['request_hash']);assert recovered==row
 assert c.get('/geo/pairs/requests/'+row['request_id']+'?expected_hash=wrong').status_code==409
 scenes=get('/geo/scenes');current={s['id']:s for s in scenes};assert all(current[s['id']]==s for s in OLD['scenes'])
 instance=get('/instances')[0];iid=instance['id'];inputs=get('/workspace/instances/'+iid+'/input-scenes');objects=get('/instances/'+iid+'/objects')
 tasks=[get('/workspace/tasks/'+t['id']) for t in get('/workspace/instances/'+iid+'/tasks')]
 assert instance==OLD['instance'] and inputs==OLD['inputs'] and objects==OLD['objects'] and tasks==OLD['tasks']
 artifacts={}
 for key,sha in OLD['artifact_hashes'].items():
  if key.endswith('/export'):
   task=next(t for t in tasks if t['id']==key.split('/')[0]);receipt=next(r for r in task['workflow_runtime']['receipts'] if r.get('output',{}).get('spatial_segmentation'))
   checked(key,'/workspace/tasks/'+task['id']+'/spatial/'+receipt['output']['geo_run_id']+'/native-geojson',sha);continue
  path=settings().storage_dir/ACCESS['workspace_id']/(sha+'.bin')
  assert path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest()==sha,key
  artifacts[key]=sha
 for s in scenes:
  checked('scene/'+s['id']+'/original','/geo/scenes/'+s['id']+'/original',s['input_sha256'])
  if s['preview']:checked('scene/'+s['id']+'/preview','/geo/scenes/'+s['id']+'/preview',s['preview']['sha256'])
 for kind,ev in row['result']['artifacts'].items():checked('pair/'+kind,'/geo/pairs/'+row['id']+'/artifacts/'+kind,ev['sha256'])
 checked('pair/export','/geo/pairs/'+row['id']+'/export')
 with Session(engine()) as db:
  def records(cls):return [{'id':r.id,'version':r.version,'status':getattr(r,'status',None),'data':r.data} for r in db.scalars(select(cls).where(cls.workspace_id==ACCESS['workspace_id']).order_by(cls.id))]
  models={cls.__name__:records(cls) for cls in (GeoScene,GeoRun,WorkflowScene,GeoPair,Job,Invocation,Memory)}
  for name,old in OLD['models'].items():
   current={r['id']:r for r in models[name]};assert all(current[r['id']]==r for r in old),name
  assert models['Invocation']==OLD['invocations'] and models['Memory']==OLD['memories']
  clouds=sorted(db.scalars(select(CloudCall.id)).all());assert clouds==OLD['cloud_ids']
 record={'pairs':pairs,'scenes':scenes,'instance':instance,'inputs':inputs,'objects':objects,'tasks':tasks,'models':models,'artifact_hashes':artifacts,'cloud_ids':clouds}
 if sys.argv[1]=='snapshot':save('before-restart',record)
 elif sys.argv[1]=='restart-check':
  assert record==json.loads((OUT/'before-restart.json').read_text()),'服务重启后保存数据变化'
  save('after-restart',record);save('runtime',{'records_identical':True,'prior_50_artifacts_unchanged':True,'hashes_checked':len(artifacts),'pairs':1,'common_valid_pixels':73774,'unknown_pixels':3026,'candidate_pixels':1993,'learned_change_detector_verified':False,'cloud_calls_created':0,'entity_state_updated':False,'database_restarted':False,'domain_quality_verified':False,'production_verified':False})
 else:raise ValueError('unknown phase')
 print('双影像、五份比较证据、核查说明和全部旧记录已读回；没有新增云调用。')
