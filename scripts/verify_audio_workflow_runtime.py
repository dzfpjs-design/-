"""Own public sample workspace. UI owns ability binding, association, task and review."""
import os,sys,json,time,hashlib,datetime
from pathlib import Path
from uuid import uuid4,uuid5,NAMESPACE_URL
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User,Workspace,Membership,CloudCall,AudioRun,WorkflowAudio,Task,Job,Invocation,Memory,Capability,Entity
from generic_agents.security import password_hash
from generic_agents.instance_compiler import signature_value
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
OUT=ROOT/'docs/evidence/audio-workflow-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=ROOT/'.runtime/audio-workflow-access.json'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
phase=sys.argv[1]
if phase=='prepare' and not ACCESS.exists():
 a={'username':'audio_workflow_'+uuid4().hex[:8],'password':uuid4().hex}
 with Session(engine()) as db:
  u=User(username=a['username'],password_hash=password_hash(a['password']));w=Workspace(name='公开音频业务流程核查');db.add_all([u,w]);db.flush();db.add(Membership(user_id=u.id,workspace_id=w.id,role='admin'));db.commit();a['workspace_id']=w.id
  save('baseline',{'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all())})
 fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:json.dump(a,f)
a=json.loads(ACCESS.read_text())
with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
 for attempt in range(40):
  try:r=c.post('/login',json={k:a[k] for k in ('username','password')});break
  except httpx.ConnectError:
   if attempt==39:raise
   time.sleep(.25)
 r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
 def call(method,path,body=None):
  r=c.request(method,path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
 def get(path):return call('GET',path)
 def wait(path,states):
  until=time.monotonic()+100
  while True:
   row=get(path)
   if row['status'] in states:return row
   if time.monotonic()>until:raise TimeoutError('处理仍在后台，原请求保留')
   time.sleep(.25)
 if phase=='prepare':
  sources=[x for x in get('/audio') if not x.get('workflow_task_id')]
  if not sources:
   p=ROOT/'.runtime/audio-bootstrap/public-zh.mp3';r=c.post('/audio',data={'request_id':str(uuid5(NAMESPACE_URL,a['workspace_id']+':public-source')),'configuration':json.dumps({'channel':'mono','language':'auto'})},files={'file':(p.name,p.read_bytes(),'audio/mpeg')});r.raise_for_status();source=wait('/audio/'+r.json()['id'],{'succeeded','failed','cancelled'});assert source['status']=='succeeded',source.get('error')
  if not get('/setup/materials'):
   raw='公开音频资料核查规则\n在本机对任务明确选择的原音频重新转写，固定语言、通道和模型版本；识别文字不是业务异常。\n音频核查负责人听原音频并保存核对说明，保留识别原文及任务依据。\n数据管理保存音频来源、本地执行与人工回执，不更新对象状态或自动发布经验。'.encode();r=c.post('/setup/materials',files={'file':('公开音频资料核查规则.txt',raw,'text/plain')});r.raise_for_status();material=wait('/setup/materials/'+r.json()['id'],{'parsed','failed'});assert material['status']=='parsed'
  else:material=get('/setup/materials')[0]
  draft=get('/setup/draft')
  if not draft['configuration'].get('businesses'):
   steps=[]
   for sid,name,agent,role,index in [('transcribe','本地音频转写','多模感知智能体','',1),('review','保存业务音频核查说明','人工确认','音频核查负责人',2)]:
    quote=material['blocks'][index]['text'];steps.append({'id':sid,'name':name,'purpose':quote,'agent':agent,'role':role,'output':'识别文字与原音频依据' if sid=='transcribe' else '业务音频核查说明','sourceId':material['id'],'citation':quote,'citations':[{'material_id':material['id'],'block_id':material['blocks'][index]['id'],'quote':quote}],'nextId':'review' if sid=='transcribe' else 'end','condition':'','elseId':'end'})
   config={'version':1,'connections':[],'resources':[],'materials':[material],'businesses':['音频资料核查'],'instanceProfile':{'name':'公开音频资料核查','domain':'音频资料管理','goal':'本地转写、听原音频核查并保存业务依据','objectCandidates':['音频记录']},'flows':{'音频资料核查':{'trigger':'人工发起','output':'业务核查说明','steps':steps}},'automation':{'version':1,'policies':{'音频资料核查':{'triggers':['人工发起'],'rules':{key:{'mode':'自动运行' if key=='observe' else '仅给出建议'} for key in ('observe','state','data','supplement','write','learn')}}}}}
   flow=config['flows']['音频资料核查'];flow['confirmedSignature']=json.dumps(signature_value(config,flow),ensure_ascii=False);flow['confirmedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat();call('PUT','/setup/draft',{'revision':draft['revision'],'configuration':config})
  print('独立公开音频原件、规则和待绑定流程已保存，等待实际界面登记与选择。')
 elif phase=='compile':
  draft=get('/setup/draft');cap=next(x for x in get('/workspace/ability-options') if x['adapter']=='local_audio');s=draft['configuration']['flows']['音频资料核查']['steps'][0];assert s['execution']['capability_id']==cap['id'],'必须先在实际界面选择能力并确认流程'
  members=get('/instances/members');body={'revision':draft['revision'],'objects':[{'name':'音频记录','fields':[{'label':'名称','kind':'文本','required':True}]}],'role_bindings':{'音频核查负责人':members[0]['id']}}
  instances=get('/instances')
  if not instances:
   check=call('POST','/instances/check',body);assert not check['general_issues'] and any(b['available'] for b in check['businesses']),{'general':check['general_issues'],'business':[b['issues'] for b in check['businesses']]}
   release=call('POST','/instances',{**body,'confirm_configuration':True,'confirmed_configuration_hash':check['configuration_hash']});instance=get('/instances')[0];instance=call('POST','/instances/'+instance['id']+'/activate',{'version':instance['version'],'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True})
  else:instance=instances[0];release=get('/instances/'+instance['id']+'/releases')[0]
  if not get('/instances/'+instance['id']+'/objects'):
   kind=release['snapshot']['object_types'][0];call('POST','/instances/'+instance['id']+'/objects',{'release_id':release['id'],'object_type':kind['key'],'attributes':{kind['fields'][0]['key']:'公开音频记录（工程核查）'}})
  save('prepared',{'instance_id':instance['id'],'release_id':release['id'],'capability_id':cap['id']});print('实际界面选择的本地能力已编译到公开实例，等待界面关联音频、发起与核查任务。')
 elif phase in {'snapshot','restart-check'}:
  prepared=json.loads((OUT/'prepared.json').read_text());tasks=get('/workspace/instances/'+prepared['instance_id']+'/tasks');assert len(tasks)==1;task=get('/workspace/tasks/'+tasks[0]['id']);assert task['status']=='workflow_completed'
  receipt=next(r for r in task['workflow_runtime']['receipts'] if r.get('output',{}).get('audio_run_id'));run=get('/audio/'+receipt['output']['audio_run_id']);assert run['workflow_task_id']==task['id'] and run['result']==receipt['output']['audio_transcription'] and run['result']['transcription']['inference_performed'];assert '9点' in run['result']['transcription']['text'] and any(v in run['result']['transcription']['text'] for v in ('5点','五点'))
  source=task['audio_snapshots'][0]['snapshot'];assert source['source_run_id']!=run['id'];human=next(r for r in task['workflow_runtime']['receipts'] if r['step_id']=='review');assert '工程验证' in human['note'] and human['audio_snapshots']==task['audio_snapshots']
  original=c.get('/workspace/tasks/'+task['id']+'/audio/'+run['id']+'/original');original.raise_for_status();assert hashlib.sha256(original.content).hexdigest()==source['input_sha256'];exported=get('/workspace/tasks/'+task['id']+'/audio/'+run['id']+'/export');assert any('工程验证' in r.get('note','') for r in exported['review_receipts'])
  hashes={'original':hashlib.sha256(original.content).hexdigest(),'result':hashlib.sha256(json.dumps(run['result'],sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'export':hashlib.sha256(json.dumps(exported,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
  with Session(engine()) as db:
   def records(cls):return [{'id':r.id,'version':getattr(r,'version',None),'status':getattr(r,'status',None),'data':getattr(r,'data',None)} for r in db.scalars(select(cls).where(cls.workspace_id==a['workspace_id']).order_by(cls.id))]
   saved={cls.__name__:records(cls) for cls in (AudioRun,WorkflowAudio,Task,Job,Invocation,Memory,Capability)};assert len(saved['Invocation'])==1 and len(saved['AudioRun'])==2 and len(saved['WorkflowAudio'])==1
   m=db.get(Memory,task['workflow_runtime']['data_receipt_id']);assert m.status=='candidate' and m.data['audio_snapshots']==task['audio_snapshots'] and not m.data['entity_state_updated'];assert all(x.version==1 for x in db.scalars(select(Entity).where(Entity.workspace_id==a['workspace_id'])))
   clouds=sorted(db.scalars(select(CloudCall.id)).all());assert clouds==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
  record={'task':task,'records':saved,'hashes':hashes,'cloud_ids':clouds}
  if phase=='snapshot':save('before-restart',record)
  else:
   assert record==json.loads((OUT/'before-restart.json').read_text());save('after-restart',record);save('runtime',{'records_identical':True,'hashes_checked':3,'registered_ability':True,'actual_local_asr':True,'owned_workflow_lease':True,'business_review_saved':True,'new_cloud_calls':0,'entity_state_updated':False,'domain_quality_verified':False,'production_verified':False,'database_restarted':False})
  print('任务原音频、本地实际转写与人工核查读回一致，无新增云调用或对象修改。')
 else:raise ValueError('unknown phase')
