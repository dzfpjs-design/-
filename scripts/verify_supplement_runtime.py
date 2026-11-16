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
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/evidence/supplement-v06';OUT.mkdir(parents=True,exist_ok=True)
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

phase=sys.argv[1]
if phase=='baseline':
 assert not (OUT/'old-source-baseline.json').exists(),'immutable baseline exists'
 with Session(engine()) as db:
  assert not list(db.scalars(select(Job.id).where(Job.status.in_(['running','queued']),Job.kind.not_in(['cloud_budget'])))),'wait existing jobs before runtime restart'
  save('old-source-baseline',{'workspaces':{access(n)['workspace_id']:rows(db,access(n)['workspace_id']) for n in ACCESS},'cloud_ids':sorted(db.scalars(select(CloudCall.id)))})
 print('eleven owned QA workspace baseline saved; original sources unchanged')
elif phase=='prepare':
 with connection() as c:
  i=next(x for x in call(c,'GET','/instances') if x['id']=='69f4e984-953c-4918-a2ca-75910c072100');assert i['status']=='partially_active'
  release=next(x for x in call(c,'GET','/instances/'+i['id']+'/releases') if x['id']==i['active_release_id']);original=next(x for x in call(c,'GET','/instances/'+i['id']+'/objects') if x['id']=='0a7b1e54-9cdf-493e-ab00-f9ea1f066202')
  assert list(original['attributes'].values())==['公开猫观察区']
  name='公开补充观察场景';obj=next((x for x in call(c,'GET','/instances/'+i['id']+'/objects') if list(x['attributes'].values())==[name]),None)
  if not obj:obj=call(c,'POST','/instances/'+i['id']+'/objects',{'release_id':release['id'],'object_type':original['entity_type'],'attributes':{k:name for k in original['attributes']},'reference':'public-supplement-video-v1','request_id':str(uuid5(NAMESPACE_URL,'public-supplement-object-v1'))})
  installation=next(x for x in call(c,'GET','/specialists/installations') if x['id']=='27ca297d-e46d-4fc6-8d9d-ec9b5ec14f3a');assert installation['status']=='ready' and installation['backend']=='cpu'
  gateways=call(c,'GET','/media/gateways');old=next(x for x in gateways if x['id']=='68824629-1acd-4383-a442-4d5210057e94');assert old['api_origin']=='http://127.0.0.1:9997' and old['rtsp_origin']=='rtsp://127.0.0.1:8554'
  gateway=next((x for x in gateways if x['name']=='本机补充公开视频来源'),None)
  if not gateway:gateway=call(c,'POST','/media/gateways',{'name':'本机补充公开视频来源','api_origin':old['api_origin'],'rtsp_origin':old['rtsp_origin']})
  if gateway['status']!='ready':gateway=call(c,'POST','/media/gateways/'+gateway['id']+'/discover',{'version':gateway['version']})
  assert gateway['status']=='ready'
  channel=next((x for x in call(c,'GET','/media/channels') if x['gateway_id']==gateway['id'] and x['path']=='validation'),None)
  if not channel:channel=call(c,'POST','/media/channels',{'gateway_id':gateway['id'],'gateway_version':gateway['version'],'path':'validation','name':name+'视频','read_authorized':True})
  save('preparation-checkpoint',{'instance_id':i['id'],'object_id':obj['id'],'gateway_id':gateway['id'],'channel_id':channel['id'],'installation_id':installation['id'],'public_synthetic_source':True})
  if not channel.get('validation_current'):
   sessions=call(c,'GET','/media/sessions');session=next((x for x in sessions if x['channel_id']==channel['id'] and (x['status'] in ['queued','connecting','capturing'] or x.get('processing_status') in ['queued','running'])),None)
   if not session:session=call(c,'POST','/media/channels/'+channel['id']+'/capture',{'channel_version':channel['version'],'installation_id':installation['id'],'seconds':4,'configuration':{'sample_hz':1,'threshold':.5,'presence_rule':None}})
   session=wait(lambda:next(x for x in call(c,'GET','/media/sessions') if x['id']==session['id']),lambda x:x.get('processing_status') in ['succeeded','failed'] or x['status'] in ['failed','cancelled'])
   assert session['status']=='captured' and session['processing_status']=='succeeded',session.get('error')
   channel=next(x for x in call(c,'GET','/media/channels') if x['id']==channel['id'])
  if not channel.get('capability_id'):channel=call(c,'POST','/media/channels/'+channel['id']+'/register',{'version':channel['version']})
  binding=next((x for x in call(c,'GET','/media/domain-bindings') if x['channel_id']==channel['id']),None)
  if not binding:binding=call(c,'PUT','/media/channels/'+channel['id']+'/domain-binding',{'channel_version':channel['version'],'entity_id':obj['id'],'entity_version':obj['version'],'reason':'公开COCO合成猫视频的补充观察场景，不代表实物身份；拍摄时间未知','confirm_scene_association':True,'instance_release_id':release['id'],'business_id':release['available_business_ids'][0]})
  assert binding['entity_id']==obj['id']
  save('selection',{'instance_id':i['id'],'object_id':obj['id'],'object_name':name,'gateway_id':gateway['id'],'channel_id':channel['id'],'installation_id':installation['id'],'binding_id':binding['id'],'backend':'cpu','source':'owned public COCO cat synthetic RTSP via loopback MediaMTX','physical_device':False,'cloud_calls_created':0})
 print('new public QA object/source bound; existing object/config/source untouched; validation read occurred before binding')
elif phase=='browser-check':
 selection=load('selection')
 with connection() as c:
  plans=call(c,'GET','/supplement-plans?instance_id='+selection['instance_id']);r=next(x for x in plans if x['status']=='completed' and x['proposal']['objects'][0]['object']['id']==selection['object_id'])
  assert len(r['approved']['actions'])==len(r['sessions'])==1 and r['approved']['seconds']==6
  old=call(c,'GET','/evidence-assessments/'+r['assessment_id']);new=call(c,'GET','/evidence-assessments/'+r['followup_assessment_id']);assert new['request']['parent_id']==old['id']
  compare=r['comparison'][0];assert compare['before_processing_records']==0 and compare['after_processing_records']==1 and not compare['current_state_sufficient']
  assert {'source_time','coverage','domain_quality'}<={g['kind'] for g in compare['remaining_gaps']};assert not any(g['kind']=='missing_saved_processing' for g in compare['remaining_gaps'])
  source=r['source_receipts'][0];assert source['capture_status']=='captured' and source['processing_status']=='succeeded'
  video=call(c,'GET','/videos/'+source['video_run_id']);assert video['status']=='succeeded' and video['configuration']['presence_rule'] is None and video['backend']=='cpu'
  raw=c.get('/videos/'+video['id']+'/original');raw.raise_for_status();assert hashlib.sha256(raw.content).hexdigest()==video['input_sha256']
  body={'version':r['version'],'action_keys':[a['key'] for a in r['approved']['actions']],'seconds':6,'confirm_one_time_read':True,'request_id':r['approval_request_id']};assert call(c,'POST','/supplement-plans/'+r['id']+'/approve',body)['sessions']==r['sessions']
  assert load('browser-export')['plan']==r
  call(c,'POST','/media/daily/reconcile')
  save('browser-journey',{'plan_id':r['id'],'original_assessment_id':old['id'],'followup_assessment_id':new['id'],'video_run_id':video['id'],'saved_original_sha256':video['input_sha256'],'record_count':[0,1],'remaining_gaps':[g['kind'] for g in compare['remaining_gaps']],'admin_one_time_read':True,'approved_seconds':6,'local_backend':'cpu','raw_original_hash_verified':True,'same_approval_no_second_read':True,'browser_export_matches_saved_plan':True,'cloud_calls_created':0,'domain_quality_verified':False})
 print('actual browser saved approval, RTSP read/local model, 0→1 comparison, remaining gaps, original SHA and export verified')
elif phase in {'snapshot','restart-check'}:
 with connection() as c:
  selection=load('selection');journey=load('browser-journey');plan=call(c,'GET','/supplement-plans/'+journey['plan_id']);old=call(c,'GET','/evidence-assessments/'+journey['original_assessment_id']);new=call(c,'GET','/evidence-assessments/'+journey['followup_assessment_id']);video=call(c,'GET','/videos/'+journey['video_run_id']);daily=call(c,'GET','/workspace/instances/'+selection['instance_id']+'/daily');export=call(c,'GET','/supplement-plans/'+journey['plan_id']+'/export')
  raw=c.get('/videos/'+video['id']+'/original');raw.raise_for_status();assert hashlib.sha256(raw.content).hexdigest()==video['input_sha256'];assert new['result']['records'][0]['integrity_verified']
  visible={'plan':plan,'original_assessment':old,'followup_assessment':new,'video':video,'daily':daily,'export':export,'original_sha256':video['input_sha256']}
 with Session(engine()) as db:
  baseline=load('old-source-baseline');current={access(n)['workspace_id']:rows(db,access(n)['workspace_id']) for n in ACCESS};count=0
  for workspace,kinds in baseline['workspaces'].items():
   for kind,records in kinds.items():
    for identity,prior in records.items():assert current[workspace].get(kind,{}).get(identity)==prior,(kind,identity);count+=1
  assert sorted(db.scalars(select(CloudCall.id)))==baseline['cloud_ids'],'supplement must not call cloud'
  additions={}
  for w,kinds in current.items():
   for kind,records in kinds.items():
    amount=len(set(records)-set(baseline['workspaces'][w].get(kind,{})))
    if amount:additions[kind]=additions.get(kind,0)+amount
  state={'visible':visible,'workspaces':current,'old_records_identical':count,'new_rows':additions,'cloud_ids':baseline['cloud_ids'],'ordinary_services_restart_only':True,'physical_devices_verified':False,'domain_quality_verified':False,'postgres_restart':False}
 if phase=='snapshot':save('before-restart',state)
 else:assert state==load('before-restart'),'normal service reload changed saved receipts';save('after-restart',state)
 print({'old_records_identical':count,'new_rows':additions,'cloud_calls_created':0,'state':phase})
else:raise SystemExit('unknown phase')
