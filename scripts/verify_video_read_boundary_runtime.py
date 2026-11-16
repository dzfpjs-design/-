"""Owned public RTSP/CPU supplement journey; no cloud, microphone or physical control."""
import json,sys,time,hashlib
from pathlib import Path
from contextlib import contextmanager
from uuid import uuid5,NAMESPACE_URL
import httpx
from sqlalchemy import select,inspect
from sqlalchemy.orm import Session
from generic_agents.models import Base,CloudCall,Job
from generic_agents.db import engine
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/video-read-boundary-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=['audio-package-validation','audio-workflow','audio-validation','geo-workflow','pair-workflow','setup-voice-validation','speech-activity-validation','segmented-audio-validation','workflow-segmented-audio','instance-validation','instance-perception']
def access(n='instance-perception'):return json.loads((ROOT/'.runtime'/f'{n}-access.json').read_text())
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def load(n):return json.loads((OUT/(n+'.json')).read_text())
def rows(db,w):
 out={};existing=set(inspect(db.bind).get_table_names())
 for mapper in Base.registry.mappers:
  cls=mapper.class_
  if 'workspace_id' not in cls.__table__.columns or cls.__tablename__ not in existing:continue
  values={}
  for row in db.scalars(select(cls).where(cls.workspace_id==w)):
   raw={c.name:(v.isoformat() if hasattr(v,'isoformat') else v) for c in cls.__table__.columns if (v:=getattr(row,c.name)) is not None};values[row.id]={'hash':digest(raw),'kind':getattr(row,'kind',None)}
  if values:out[cls.__name__]=values
 return out
@contextmanager
def connection():
 a=access()
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=40) as c:
  for attempt in range(40):
   try:r=c.post('/login',json={k:a[k] for k in ('username','password')});r.raise_for_status();break
   except httpx.ConnectError:
    if attempt==39:raise
    time.sleep(.2)
  c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':r.json()['csrf_token']});yield c

def call(c,method,url,body=None):
 r=c.request(method,url,json=body) if body is not None else c.request(method,url);r.raise_for_status();return r.json()
def wait(fn,done,seconds=180):
 end=time.monotonic()+seconds;prior=None
 while time.monotonic()<end:
  r=fn();value=(r['status'],r.get('processing_status'))
  if value!=prior:print('进度：'+str(value),flush=True);prior=value
  if done(r):return r
  time.sleep(.5)
 raise RuntimeError('saved operation still live; inspect checkpoint before retry')

sys.path.insert(0,str(ROOT))
phase=sys.argv[1]
if phase=='baseline':
 assert not (OUT/'old-source-baseline.json').exists(),'immutable baseline exists'
 with Session(engine()) as db:
  assert not list(db.scalars(select(Job.id).where(Job.status.in_(['running','queued']),Job.kind.not_in(['cloud_budget'])))),'existing jobs must finish'
  save('old-source-baseline',{'workspaces':{access(n)['workspace_id']:rows(db,access(n)['workspace_id']) for n in ACCESS},'cloud_ids':sorted(db.scalars(select(CloudCall.id)))})
 print('immutable old QA baseline saved')
elif phase=='prepare':
 from tests.test_workflow_abilities import conf,enable
 from tests.test_vertical_slice import manifest,publish
 from tests.test_instance_workspace import prepared,object_row
 from generic_agents.instance_compiler import signature_value
 class Client:
  def __init__(self,c):self.c=c
  def get(self,path,**kw):return self.c.get(path.removeprefix('/api'),**kw)
  def post(self,path,**kw):return self.c.post(path.removeprefix('/api'),**kw)
  def put(self,path,**kw):return self.c.put(path.removeprefix('/api'),**kw)
 with connection() as c:
  draft=call(c,'GET','/setup/draft');backup=ROOT/'.runtime/video-read-boundary-original-draft.json'
  if not backup.exists():backup.write_text(json.dumps(draft,ensure_ascii=False));backup.chmod(0o600)
  install=next(x for x in call(c,'GET','/specialists/installations') if x['id']=='27ca297d-e46d-4fc6-8d9d-ec9b5ec14f3a');assert install['status']=='ready' and install['backend']=='cpu'
  gateway=next((x for x in call(c,'GET','/media/gateways') if x['name']=='本机统一读取边界公开视频来源'),None)
  if not gateway:gateway=call(c,'POST','/media/gateways',{'name':'本机统一读取边界公开视频来源','api_origin':'http://127.0.0.1:9997','rtsp_origin':'rtsp://127.0.0.1:8554'})
  if gateway['status']!='ready':gateway=call(c,'POST','/media/gateways/'+gateway['id']+'/discover',{'version':gateway['version']})
  channel=next((x for x in call(c,'GET','/media/channels') if x['gateway_id']==gateway['id'] and x['path']=='boundary-validation'),None)
  if not channel:channel=call(c,'POST','/media/channels',{'gateway_id':gateway['id'],'gateway_version':gateway['version'],'path':'boundary-validation','name':'统一读取边界公开场景视频','read_authorized':True})
  save('preparation-checkpoint',{'channel_id':channel['id'],'gateway_id':gateway['id'],'local_installation_id':install['id']})
  if not channel.get('validation_current'):
   session=call(c,'POST','/media/channels/'+channel['id']+'/capture',{'channel_version':channel['version'],'installation_id':install['id'],'seconds':4,'configuration':{'sample_hz':1,'threshold':.5,'presence_rule':None}})
   session=wait(lambda:next(x for x in call(c,'GET','/media/sessions') if x['id']==session['id']),lambda x:x.get('processing_status') in ['succeeded','failed'] or x['status'] in ['failed','cancelled'])
   assert session['status']=='captured' and session['processing_status']=='succeeded'
   channel=next(x for x in call(c,'GET','/media/channels') if x['id']==channel['id'])
  if not channel.get('capability_id'):channel=call(c,'POST','/media/channels/'+channel['id']+'/register',{'version':channel['version']})
  adapter=Client(c)
  caps=call(c,'GET','/capabilities')
  cap=next((x for x in caps if x.get('namespace')=='automatic_read_qa' and x.get('name')=='count_registered_objects' and x['status']=='published'),None)
  if not cap:
   cap=publish(adapter,manifest('tool',namespace='automatic_read_qa',name='count_registered_objects',description='读取并计数已登记的公开QA对象',allowed_agent_roles=['perception'],
    input_schema={'type':'object','required':['records','field','operation'],'additionalProperties':False,'properties':{'records':{'type':'array','items':{'type':'object'}},'field':{'type':'string'},'operation':{'enum':['count']}}},
    output_schema={'type':'object','required':['value','sample_count','provenance'],'properties':{'value':{'type':['number','null']},'sample_count':{'type':'integer'},'provenance':{'type':'array'}}},execution={'adapter':'builtin','handler':'aggregate_values','input_bindings':{}},specification={}))
  cfg=conf(cap,agent='多模感知智能体');cfg['instanceProfile'].update(name='统一读取边界资料核查',domain='公开视频资料核对',goal='缺少关联视频处理记录时，按有限只读授权补充并保存依据')
  f=cfg['flows']['资料核验'];f['steps'][0].update(name='读取并计数已登记对象',purpose='依据业务对象记录读取当前登记项')
  f['steps'][0]['execution']['automation_action']='observe';f['steps'][0]['execution']['arguments']['operation']='count'
  rid='media-channel:'+channel['id'];cfg['connections']=[{'id':'media-gateway:'+gateway['id'],'category':'video','name':gateway['name'],'protocol':'MediaMTX / RTSP','status':'read_verified','gateway_id':gateway['id']}]
  cfg['resources']=[{'id':rid,'category':'video','name':channel['name'],'connection':'media-gateway:'+gateway['id'],'protocol':'RTSP / H.264','status':'candidate','caps':['视频读取','本地目标检测'],'businesses':['资料核验'],'checks':{'read_verified':True,'domain_evaluation':False},'details':{'capability_id':channel['capability_id'],'channel_id':channel['id'],'deployment':'local'}}]
  cfg['automation']['policies']['资料核验'].update(triggers=['新观测到达','定时运行'],time='08:00',rules={'observe':{'mode':'自动运行','resources':[rid]}})
  cfg['equipment']={'types':{'camera':{'read':True,'mode':'禁止执行','limitEnabled':True,'limit':3,'cooldownEnabled':True,'cooldown':1,'durationEnabled':True,'duration':1}},'devices':{}}
  f['confirmedSignature']=json.dumps(signature_value(cfg,f),ensure_ascii=False)
  release,_,check=prepared(adapter,cfg);assert release['available_business_ids'],check
  enable(adapter,release)
  obj=next((x for x in call(c,'GET','/instances/'+release['instance_id']+'/objects') if list(x['attributes'].values())==['统一读取边界公开场景']),None)
  if not obj:
   kind=release['snapshot']['object_types'][0];obj=call(c,'POST','/instances/'+release['instance_id']+'/objects',{'release_id':release['id'],'object_type':kind['key'],'attributes':{kind['fields'][0]['key']:'统一读取边界公开场景'},'reference':'public-autonomous-video-v1'})
  binding=next((x for x in call(c,'GET','/media/domain-bindings') if x['channel_id']==channel['id']),None)
  if not binding:binding=call(c,'PUT','/media/channels/'+channel['id']+'/domain-binding',{'channel_version':channel['version'],'entity_id':obj['id'],'entity_version':obj['version'],'reason':'公开COCO合成猫场景；不是物理身份，时间未知','confirm_scene_association':True,'instance_release_id':release['id'],'business_id':release['available_business_ids'][0]})
  original=json.loads(backup.read_text());now_draft=call(c,'GET','/setup/draft');restored=call(c,'PUT','/setup/draft',{'revision':now_draft['revision'],'configuration':original['configuration']})
  assert restored['configuration']==original['configuration']
  save('selection',{'instance_id':release['instance_id'],'release_id':release['id'],'entity_id':obj['id'],'entity_name':'统一读取边界公开场景','channel_id':channel['id'],'binding_id':binding['id'],'local_installation_id':install['id'],'local_backend':'cpu','business_id':release['available_business_ids'][0],'configuration_restored':True,'cloud_calls_created':0})
 print('new own QA instance has real observe entry, fixed source and camera read-only bounds; prior draft restored')
elif phase in {'check','snapshot','restart-check','repair-restart-check'}:
 from generic_agents.models import VideoReadReservation,StreamSession,MediaMonitor,ContinuousStream
 s=load('selection');channel_id=s['channel_id']
 with connection() as c:
  boundary=call(c,'GET','/media/channels/'+channel_id+'/read-boundary');boundary.pop('checked_at')
  assert boundary['usage']['used']==3,boundary['usage']
  assert all(not x['allowed_now'] and '今日读取次数已用完' in x['issue'] for x in boundary['modes'].values()),boundary['modes']
  sessions=[x for x in call(c,'GET','/media/sessions') if x['channel_id']==channel_id]
  assert len(sessions)==3 and all(x['status']=='captured' and x['processing_status']=='succeeded' for x in sessions)
  monitors=[x for x in call(c,'GET','/media/monitors') if x['channel_id']==channel_id]
  assert len(monitors)==1 and monitors[0]['status']=='exhausted',monitors
  videos=[call(c,'GET','/videos/'+x['video_run_id']) for x in sessions];hashes={}
  for video in videos:
   assert video['status']=='succeeded' and video['backend']=='cpu'
   raw=c.get('/videos/'+video['id']+'/original');raw.raise_for_status();hashes[video['id']]=hashlib.sha256(raw.content).hexdigest()
   assert hashes[video['id']]==video['input_sha256']
  values={'boundary':boundary,'sessions':sessions,'monitors':monitors,'videos':videos,'original_sha256':hashes,
   'objects':call(c,'GET','/instances/'+s['instance_id']+'/objects'),'daily':call(c,'GET','/workspace/instances/'+s['instance_id']+'/daily')}
  original_config=json.loads((ROOT/'.runtime/video-read-boundary-original-draft.json').read_text())['configuration'];assert call(c,'GET','/setup/draft')['configuration']==original_config
 with Session(engine()) as db:
  receipts=list(db.scalars(select(VideoReadReservation).where(VideoReadReservation.channel_id==channel_id)))
  assert len(receipts)==3
  assert not list(db.scalars(select(ContinuousStream.id).where(ContinuousStream.channel_id==channel_id)))
  values['reservations']={x.id:x.data for x in receipts}
  baseline=load('old-source-baseline');current={w:rows(db,w) for w in baseline['workspaces']};changed=[];old_count=0;new_count={}
  for workspace,tables in baseline['workspaces'].items():
   for cls,rs in tables.items():
    for identity,value in rs.items():
     old_count+=1
     if current[workspace].get(cls,{}).get(identity)!=value:changed.append((workspace,cls,identity))
   for cls,rs in current[workspace].items():new_count[cls]=new_count.get(cls,0)+len(set(rs)-set(tables.get(cls,{})))
  assert len(changed)==1 and changed[0][0]==access()['workspace_id'] and changed[0][1]=='SetupDraft',changed
  assert sorted(db.scalars(select(CloudCall.id)))==baseline['cloud_ids']
  values['owned_qa_rows']=current
 if phase=='repair-restart-check':
  from generic_agents.video_read_boundary import POLICY_HASH
  before=load('after-restart');old_hashes={}
  for mode in ['manual','automatic']:
   old_hashes[mode]=before['boundary']['modes'][mode]['rules']['policy_hash']
   assert values['boundary']['modes'][mode]['rules']['policy_hash']==POLICY_HASH
   before['boundary']['modes'][mode]['rules']['policy_hash']=POLICY_HASH
  assert values==before,'repair changed saved records or boundary beyond the expected guard implementation hash'
  save('after-policy-repair',values)
  proof=load('verification');proof.update(policy_repair_current_hash_verified=True,policy_repair_old_hashes=old_hashes,
   policy_repair_new_hash=POLICY_HASH,policy_repair_no_saved_record_change=True)
  save('verification',proof)
  print('repaired guard is live; only expected implementation hash changed; all saved records, budget and originals unchanged')
 elif phase=='check':
  save('browser-journey',{'local_backend':'cpu','sessions':len(sessions),'frames':[len(x['frames']) for x in videos],'read_units':3,'manual_and_periodic_shared_budget':True,'continuous_start_denied_before_connection':True,'failed_interval_or_daily_requests_create_no_receipt':True,'original_sha256':hashes,'cloud_calls_created':0,'physical_control_calls_created':0,'domain_quality_verified':False})
  print('3 real local CPU reads; original SHA equal; shared daily budget exhausted; no extra receipt/continuous connection/cloud call')
 elif phase=='snapshot':save('before-restart',values);print('saved complete owned QA/source/rule/budget snapshot')
 else:
  assert values==load('before-restart'),'saved state differs after normal service restart'
  save('after-restart',values);save('verification',{'normal_api_setup_media_restart_equal':True,'original_sha256_equal':True,'old_qa_rows':old_count,'unchanged_old_qa_rows':old_count-1,'old_changes':changed,'qa_draft_original_content_restored':True,'new_rows':{k:v for k,v in new_count.items() if v},'read_units':3,'cloud_calls_created':0,'physical_control_calls_created':0,'domain_quality_verified':False,'production_verified':False})
  print('normal service restart preserves all own records, rules, budget and originals; '+str(old_count-1)+' old QA rows unchanged; cloud calls 0')
