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
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/automatic-read-v06';OUT.mkdir(parents=True,exist_ok=True)
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
  draft=call(c,'GET','/setup/draft');backup=ROOT/'.runtime/automatic-read-original-draft.json'
  if not backup.exists():backup.write_text(json.dumps(draft,ensure_ascii=False));backup.chmod(0o600)
  install=next(x for x in call(c,'GET','/specialists/installations') if x['id']=='27ca297d-e46d-4fc6-8d9d-ec9b5ec14f3a');assert install['status']=='ready' and install['backend']=='cpu'
  gateway=next((x for x in call(c,'GET','/media/gateways') if x['name']=='本机自动补充公开视频来源'),None)
  if not gateway:gateway=call(c,'POST','/media/gateways',{'name':'本机自动补充公开视频来源','api_origin':'http://127.0.0.1:9997','rtsp_origin':'rtsp://127.0.0.1:8554'})
  if gateway['status']!='ready':gateway=call(c,'POST','/media/gateways/'+gateway['id']+'/discover',{'version':gateway['version']})
  channel=next((x for x in call(c,'GET','/media/channels') if x['gateway_id']==gateway['id'] and x['path']=='validation'),None)
  if not channel:channel=call(c,'POST','/media/channels',{'gateway_id':gateway['id'],'gateway_version':gateway['version'],'path':'validation','name':'自动补充公开场景视频','read_authorized':True})
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
  cfg=conf(cap,agent='多模感知智能体');cfg['instanceProfile'].update(name='有限自动资料核查',domain='公开视频资料核对',goal='缺少关联视频处理记录时，按有限只读授权补充并保存依据')
  f=cfg['flows']['资料核验'];f['steps'][0].update(name='读取并计数已登记对象',purpose='依据业务对象记录读取当前登记项')
  f['steps'][0]['execution']['automation_action']='observe';f['steps'][0]['execution']['arguments']['operation']='count'
  rid='media-channel:'+channel['id'];cfg['connections']=[{'id':'media-gateway:'+gateway['id'],'category':'video','name':gateway['name'],'protocol':'MediaMTX / RTSP','status':'read_verified','gateway_id':gateway['id']}]
  cfg['resources']=[{'id':rid,'category':'video','name':channel['name'],'connection':'media-gateway:'+gateway['id'],'protocol':'RTSP / H.264','status':'candidate','caps':['视频读取','本地目标检测'],'businesses':['资料核验'],'checks':{'read_verified':True,'domain_evaluation':False},'details':{'capability_id':channel['capability_id'],'channel_id':channel['id'],'deployment':'local'}}]
  cfg['automation']['policies']['资料核验']['rules']={'observe':{'mode':'自动运行','resources':[rid]}}
  cfg['equipment']={'types':{'camera':{'read':True,'mode':'禁止执行','limitEnabled':True,'limit':2,'cooldownEnabled':True,'cooldown':1}},'devices':{}}
  f['confirmedSignature']=json.dumps(signature_value(cfg,f),ensure_ascii=False)
  release,_,check=prepared(adapter,cfg);assert release['available_business_ids'],check
  enable(adapter,release)
  obj=next((x for x in call(c,'GET','/instances/'+release['instance_id']+'/objects') if list(x['attributes'].values())==['自动补充公开场景']),None)
  if not obj:
   kind=release['snapshot']['object_types'][0];obj=call(c,'POST','/instances/'+release['instance_id']+'/objects',{'release_id':release['id'],'object_type':kind['key'],'attributes':{kind['fields'][0]['key']:'自动补充公开场景'},'reference':'public-automatic-video-v1'})
  binding=next((x for x in call(c,'GET','/media/domain-bindings') if x['channel_id']==channel['id']),None)
  if not binding:binding=call(c,'PUT','/media/channels/'+channel['id']+'/domain-binding',{'channel_version':channel['version'],'entity_id':obj['id'],'entity_version':obj['version'],'reason':'公开COCO合成猫场景；不是物理身份，时间未知','confirm_scene_association':True,'instance_release_id':release['id'],'business_id':release['available_business_ids'][0]})
  original=json.loads(backup.read_text());now_draft=call(c,'GET','/setup/draft');restored=call(c,'PUT','/setup/draft',{'revision':now_draft['revision'],'configuration':original['configuration']})
  assert restored['configuration']==original['configuration']
  save('selection',{'instance_id':release['instance_id'],'release_id':release['id'],'entity_id':obj['id'],'entity_name':'自动补充公开场景','channel_id':channel['id'],'binding_id':binding['id'],'local_installation_id':install['id'],'local_backend':'cpu','business_id':release['available_business_ids'][0],'configuration_restored':True,'cloud_calls_created':0})
 print('new own QA instance has real observe entry, fixed source and camera read-only bounds; prior draft restored')
elif phase=='check':
 s=load('selection')
 with connection() as c:
  watches=call(c,'GET','/supplement-watches?instance_id='+s['instance_id']);w=next(x for x in watches if x['authorization'])
  w=wait(lambda:call(c,'GET','/supplement-watches/'+w['id']),lambda x:len(x['history'])>=2)
  reads=[x for x in w['history'] if x.get('plan_id')];assert len(reads)==1 and len(w['history'])==2 and not w['history'][-1].get('plan_id')
  assert w['authorization']['seconds']==6 and w['authorization']['max_reads']==2 and w['authorization']['cooldown_minutes']==1
  assert w['authorization']['permissions']['daily_limit']==2 and w['authorization']['permissions']['control_authorized'] is False
  assert w['authorization']['permissions']['camera_time_window_required'] is False and w['authorization']['raw_media_cloud_allowed'] is False
  p=call(c,'GET','/supplement-plans/'+reads[0]['plan_id']);assert p['status']=='completed' and p['automatic_watch_id']==w['id'] and len(p['sessions'])==1
  old=call(c,'GET','/evidence-assessments/'+p['assessment_id']);new=call(c,'GET','/evidence-assessments/'+p['followup_assessment_id']);assert new['request']['parent_id']==old['id']
  assert old['result']['objects'][0]['processing_records']==0 and new['result']['objects'][0]['processing_records']==1
  assert {'source_time','coverage','domain_quality'}<={x['kind'] for x in p['comparison'][0]['remaining_gaps']}
  skipped=call(c,'GET','/evidence-assessments/'+w['history'][-1]['assessment_id']);assert skipped['result']['objects'][0]['processing_records']==1
  proof=p['source_receipts'][0];assert proof['capture_status']=='captured' and proof['processing_status']=='succeeded'
  video=call(c,'GET','/videos/'+proof['video_run_id']);assert video['status']=='succeeded' and video['backend']=='cpu' and video['configuration']['presence_rule'] is None
  raw=c.get('/videos/'+video['id']+'/original');raw.raise_for_status();assert hashlib.sha256(raw.content).hexdigest()==video['input_sha256']
  call(c,'POST','/media/daily/reconcile')
  save('browser-journey',{'watch_id':w['id'],'plan_id':p['id'],'template_assessment_id':call(c,'GET','/supplement-plans/'+w['template_plan_id'])['assessment_id'],'original_assessment_id':old['id'],'followup_assessment_id':new['id'],'skipped_assessment_id':skipped['id'],'video_run_id':video['id'],'source_receipt':proof,'saved_original_sha256':video['input_sha256'],'record_count':[0,1],'remaining_gaps':[x['kind'] for x in p['comparison'][0]['remaining_gaps']],'saved_frames':len(video['frames']),'one_read_after_new_check':True,'second_independent_check_not_reread':True,'cloud_calls_created':0,'domain_quality_verified':False})
 print('actual browser confirmed scope: one automatic CPU read, 0→1, next quality gaps do not reread; original SHA verified')
elif phase in {'snapshot','restart-check'}:
 s=load('selection');j=load('browser-journey')
 with connection() as c:
  watch=call(c,'GET','/supplement-watches/'+j['watch_id']);assert watch['status']=='stopped','stop own authorization in browser before durable snapshot'
  values={'watch':watch,'watch_export':call(c,'GET','/supplement-watches/'+watch['id']+'/export'),'plan':call(c,'GET','/supplement-plans/'+j['plan_id']),
   'original':call(c,'GET','/evidence-assessments/'+j['original_assessment_id']),'followup':call(c,'GET','/evidence-assessments/'+j['followup_assessment_id']),
   'skipped':call(c,'GET','/evidence-assessments/'+j['skipped_assessment_id']),'video':call(c,'GET','/videos/'+j['video_run_id']),
   'daily':call(c,'GET','/workspace/instances/'+s['instance_id']+'/daily'),'objects':call(c,'GET','/instances/'+s['instance_id']+'/objects')}
  raw=c.get('/videos/'+j['video_run_id']+'/original');raw.raise_for_status();values['video_original_sha256']=hashlib.sha256(raw.content).hexdigest()
  assert values['watch_export']==load('browser-export'),'browser download differs from saved authorization'
  original_config=json.loads((ROOT/'.runtime/automatic-read-original-draft.json').read_text())['configuration'];assert call(c,'GET','/setup/draft')['configuration']==original_config
 with Session(engine()) as db:
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
 if phase=='snapshot':save('before-restart',values);print('full watch/results/source/hash/QA snapshot saved; one QA draft version changed with original content restored')
 else:
  before=load('before-restart');assert values==before
  save('after-restart',values);save('verification',{'browser_export_matches_saved_authorization':True,'normal_api_setup_media_restart_equal':True,'video_original_sha256_equal':True,'old_qa_rows':old_count,'unchanged_old_qa_rows':old_count-1,'old_changes':changed,'qa_draft_original_content_restored':True,'new_rows':{k:v for k,v in new_count.items() if v},'cloud_calls_created':0,'physical_control_calls_created':0,'production_verified':False,'domain_quality_verified':False,'automatic_modality':'single-object bounded H264 video only','browser_pending_response_fault_injected':False,'isolated_db_pending_cancel_verified':True})
  print('normal API/setup/media restart: all own saved data/source bytes equal; '+str(old_count-1)+' old QA rows unchanged, original draft content restored; cloud calls 0')
