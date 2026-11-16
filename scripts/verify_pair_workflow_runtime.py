"""Public synthetic A/B workflow preparation and durable API verification; no cloud calls."""
import os,sys,json,time,hashlib,datetime
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User,Workspace,Membership,CloudCall,Job,GeoScene,GeoPair,WorkflowScene,Task,Invocation,Memory,Capability
from generic_agents.security import password_hash
from generic_agents.instance_compiler import signature_value
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
OUT=ROOT/'docs/evidence/pair-workflow-v06';OUT.mkdir(parents=True,exist_ok=True)
ACCESS=ROOT/'.runtime/pair-workflow-access.json'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
phase=sys.argv[1]
if phase=='prepare' and not ACCESS.exists():
 a={'username':'pair_workflow_'+uuid4().hex[:8],'password':uuid4().hex}
 with Session(engine()) as db:
  u=User(username=a['username'],password_hash=password_hash(a['password']));w=Workspace(name='公开双影像业务链核查');db.add_all([u,w]);db.flush();db.add(Membership(user_id=u.id,workspace_id=w.id,role='admin'));db.commit();a['workspace_id']=w.id
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
 def call(method,path,body=None):r=c.request(method,path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
 def get(path):return call('GET',path)
 def wait(path,states):
  until=time.monotonic()+90
  while True:
   row=get(path)
   if row['status'] in states:return row
   if time.monotonic()>until:raise TimeoutError('后台未结束，原件与记录保留')
   time.sleep(.25)
 if phase=='prepare':
  sources=[]
  for name in ('public-synthetic-A.tif','public-synthetic-B.tif'):
   row=next((s for s in get('/geo/scenes') if s['name']==name),None)
   if not row:
    p=ROOT/'.runtime/geo-pair-fixture'/name;r=c.post('/geo/scenes',data={'request_id':'pair-workflow-'+name[:-4]},files={'file':(name,p.read_bytes(),'image/tiff')});r.raise_for_status();row=r.json()
   row=wait('/geo/scenes/'+row['id'],{'parsed','ready','failed','cancelled'});assert row['status'] in {'parsed','ready'},row.get('error')
   if row['status']=='parsed':
    row=call('PUT','/geo/scenes/'+row['id']+'/configuration',{'version':row['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True});row=wait('/geo/scenes/'+row['id'],{'ready','failed','cancelled'});assert row['status']=='ready',row.get('error')
   sources.append(row)
  pairs=get('/geo/pairs')
  if not pairs:
   x,y=sources;row=call('POST','/geo/pairs',{'request_id':'pair-workflow-baseline-public','before_scene_id':x['id'],'after_scene_id':y['id'],'before_version':x['version'],'after_version':y['version'],'roi':[0,0,320,240],'threshold':30,'alignment':'same_grid','rgb_comparability_confirmed':True});row=wait('/geo/pairs/'+row['id'],{'succeeded','failed','cancelled'});assert row['status']=='succeeded',row.get('error')
  if not get('/setup/materials'):
   raw='公开双影像资料核查规则\n在本机比较任务明确选择的A/B影像，保留各自RGB与范围、共同有效区域和像素差异候选；不认定业务异常。\n影像核查负责人对照两幅原件、未知区域和候选，保存人工说明与依据。\n数据管理保留固定来源、比较证据和流程回执，不更新对象状态或发布经验。\n公开照片加合成坐标只用于工程核对，不代表遥感质量。'.encode();r=c.post('/setup/materials',files={'file':('公开双影像资料核查规则.txt',raw,'text/plain')});r.raise_for_status();material=wait('/setup/materials/'+r.json()['id'],{'parsed','failed'});assert material['status']=='parsed'
  else:material=get('/setup/materials')[0]
  draft=get('/setup/draft')
  if not draft['configuration'].get('businesses'):
   steps=[]
   for sid,name,agent,role,index in [('compare','本地双影像像素对照','多模感知智能体','',1),('review','保存业务对照核查说明','人工确认','影像核查负责人',2)]:
    quote=material['blocks'][index]['text'];steps.append({'id':sid,'name':name,'purpose':quote,'agent':agent,'role':role,'output':'双影像像素候选' if sid=='compare' else '业务核查说明','sourceId':material['id'],'citation':quote,'citations':[{'material_id':material['id'],'block_id':material['blocks'][index]['id'],'quote':quote}],'nextId':'review' if sid=='compare' else 'end','condition':'','elseId':'end'})
   config={'version':1,'connections':[],'resources':[],'materials':[material],'businesses':['双影像资料核查'],'instanceProfile':{'name':'公开双影像资料核查','domain':'影像资料管理','goal':'比较固定A/B、核对未知区域并保存人工与数据依据，不认定业务变化','objectCandidates':['影像记录']},'flows':{'双影像资料核查':{'trigger':'人工发起','output':'业务核查说明','steps':steps}},'automation':{'version':1,'policies':{'双影像资料核查':{'triggers':['人工发起'],'rules':{key:{'mode':'自动运行' if key=='observe' else '仅给出建议'} for key in ('observe','state','data','supplement','write','learn')}}}}}
   flow=config['flows']['双影像资料核查'];flow['confirmedSignature']=json.dumps(signature_value(config,flow),ensure_ascii=False);flow['confirmedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat();call('PUT','/setup/draft',{'revision':draft['revision'],'configuration':config})
  print('独立公开工作区的原件、比较样本、规则资料和待绑定业务已保存；未创建业务任务。')
 elif phase=='compile':
  draft=get('/setup/draft');caps=get('/workspace/ability-options');cap=next(x for x in caps if x['adapter']=='local_geo_pair');config=draft['configuration'];s=config['flows']['双影像资料核查']['steps'][0];assert s['execution']['capability_id']==cap['id'],'须先在实际界面选择执行能力'
  members=get('/instances/members');body={'revision':draft['revision'],'objects':[{'name':'影像记录','fields':[{'label':'名称','kind':'文本','required':True}]}],'role_bindings':{'影像核查负责人':members[0]['id']}}
  instances=get('/instances')
  if not instances:
   check=call('POST','/instances/check',body);assert not check['general_issues'] and any(b['available'] for b in check['businesses']),{'general':check['general_issues'],'business':[b['issues'] for b in check['businesses']]}
   release=call('POST','/instances',{**body,'confirm_configuration':True,'confirmed_configuration_hash':check['configuration_hash']});instance=get('/instances')[0];instance=call('POST','/instances/'+instance['id']+'/activate',{'version':instance['version'],'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True})
  else:instance=instances[0];release=get('/instances/'+instance['id']+'/releases')[0]
  if not get('/instances/'+instance['id']+'/objects'):
   kind=release['snapshot']['object_types'][0];call('POST','/instances/'+instance['id']+'/objects',{'release_id':release['id'],'object_type':kind['key'],'attributes':{kind['fields'][0]['key']:'公开合成影像记录（不是实测地物身份）'}})
  attached=get('/workspace/instances/'+instance['id']+'/input-scenes')
  if not attached:
   sources=get('/geo/scenes')
   for name,roi in [('public-synthetic-A.tif',[0,0,320,240]),('public-synthetic-B.tif',[70,50,190,130])]:
    source=next(s for s in sources if s['name']==name);call('POST','/workspace/instances/'+instance['id']+'/input-scenes',{'request_id':'public-business-pair-'+name[:-4],'scene_id':source['id'],'scene_version':source['version'],'roi':roi})
  save('prepared',{'instance_id':instance['id'],'release_id':release['id'],'capability_id':cap['id']});print('经实际界面选择的能力已编译启用到独立公开实例；两范围和对象已保存，等待界面发起任务。')
 elif phase in {'snapshot','restart-check'}:
  instance=get('/instances')[0];iid=instance['id'];release=get('/instances/'+iid+'/releases')[0];tasks=[get('/workspace/tasks/'+t['id']) for t in get('/workspace/instances/'+iid+'/tasks')]
  assert len(tasks)==1 and tasks[0]['status']=='workflow_completed'
  task=tasks[0];receipt=next(r for r in task['workflow_runtime']['receipts'] if r.get('output',{}).get('spatial_comparison'));result=receipt['output']['spatial_comparison'];pair_id=receipt['output']['geo_pair_id'];assert receipt['candidate_only'] and result['after_allowed_window']==[70,50,190,130] and result['unknown_pixels']>0 and result['learned_model'] is False
  assert '不作为业务变化结论' in task['workflow_runtime']['receipts'][-1]['note']
  artifacts={}
  def binary(key,path,sha=None):
   r=c.get(path);r.raise_for_status();actual=hashlib.sha256(r.content).hexdigest()
   if sha:assert actual==sha,key
   artifacts[key]=actual
  scenes=get('/geo/scenes')
  for scene in scenes:
   binary('scene/'+scene['id'],'/geo/scenes/'+scene['id']+'/original',scene['input_sha256']);binary('preview/'+scene['id'],'/geo/scenes/'+scene['id']+'/preview',scene['preview']['sha256'])
  for pair in get('/geo/pairs'):
   for kind,ev in pair['result']['artifacts'].items():binary(pair['id']+'/'+kind,'/geo/pairs/'+pair['id']+'/artifacts/'+kind,ev['sha256'])
  for kind,ev in result['artifacts'].items():binary('task/'+kind,'/workspace/tasks/'+task['id']+'/comparisons/'+pair_id+'/artifacts/'+kind,ev['sha256'])
  binary('task/export','/workspace/tasks/'+task['id']+'/comparisons/'+pair_id+'/export')
  with Session(engine()) as db:
   def records(cls):return [{'id':r.id,'version':r.version,'status':getattr(r,'status',None),'data':r.data} for r in db.scalars(select(cls).where(cls.workspace_id==a['workspace_id']).order_by(cls.id))]
   rows={cls.__name__:records(cls) for cls in (GeoScene,GeoPair,WorkflowScene,Task,Job,Invocation,Memory,Capability)};memory=db.get(Memory,task['workflow_runtime']['data_receipt_id']);assert memory.status=='candidate' and memory.data['comparison_scene_snapshots']==task['comparison_scene_snapshots'];clouds=sorted(db.scalars(select(CloudCall.id)).all());assert clouds==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
  record={'instance':instance,'release':release,'tasks':tasks,'sources':get('/workspace/instances/'+iid+'/input-scenes'),'objects':get('/instances/'+iid+'/objects'),'draft':get('/setup/draft'),'records':rows,'artifact_hashes':artifacts,'cloud_ids':clouds}
  assert record['objects'][0]['version']==1
  if phase=='snapshot':save('before-restart',record)
  else:
   assert record==json.loads((OUT/'before-restart.json').read_text());save('after-restart',record);save('runtime',{'records_identical':True,'hashes_checked':len(artifacts),'actual_local_invocations':len(rows['Invocation']),'candidate_memory':len(rows['Memory']),'new_cloud_calls':0,'entity_state_updated':False,'domain_quality_verified':False,'database_restarted':False,'production_verified':False})
  print('双影像实际业务调用、人工说明、数据候选和全部证据已核对；没有新增云调用。')
 else:raise ValueError('unknown phase')
