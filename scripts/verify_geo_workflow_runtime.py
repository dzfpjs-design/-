"""Confirmed business -> real local spatial model -> human/data archive; public engineering input."""
import os,sys,json,time,hashlib,datetime
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User,Workspace,Membership,CloudCall,Memory,Invocation,Job,AuthSession
from generic_agents.security import password_hash
from generic_agents.instance_compiler import signature_value
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
ACCESS=ROOT/'.runtime/geo-workflow-access.json';OUT=ROOT/'docs/evidence/geo-workflow-v06';OUT.mkdir(parents=True,exist_ok=True)
FIXTURE=ROOT/'.runtime/geo-fixture/public-photo-synthetic-map.tif'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def access():return json.loads(ACCESS.read_text())
@contextmanager
def connection():
 a=access()
 with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as c:
  for attempt in range(40):
   try:r=c.post('/api/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();break
   except httpx.ConnectError:
    if attempt==39:raise
    time.sleep(.25)
  c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']});yield c

def call(c,method,path,body=None):
 r=c.request(method,'/api'+path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
def wait(read,done):
 until=time.monotonic()+300
 while True:
  row=read()
  if done(row):return row
  if time.monotonic()>until:raise TimeoutError('真实业务处理仍未结束，所有记录保留')
  time.sleep(.4)
phase=sys.argv[1]
if phase=='prepare':
 if not ACCESS.exists():
  a={'username':'geo_business_'+uuid4().hex[:8],'password':uuid4().hex}
  with Session(engine()) as db:
   user=User(username=a['username'],password_hash=password_hash(a['password']));workspace=Workspace(name='公开空间业务链工程核查');db.add_all([user,workspace]);db.flush();db.add(Membership(user_id=user.id,workspace_id=workspace.id,role='admin'));db.commit();a['workspace_id']=workspace.id
   save('baseline',{'workspace_id':workspace.id,'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all())})
  fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:json.dump(a,f)
 with connection() as c:
  installs=call(c,'GET','/specialists/installations');installation=next((x for x in installs if x['pack_id']=='sam2-1-tiny'),None)
  if not installation:installation=call(c,'POST','/specialists/installations',{'pack_id':'sam2-1-tiny','source':'official','backend':'cpu'})
  installation=wait(lambda:next(x for x in call(c,'GET','/specialists/installations') if x['id']==installation['id']),lambda x:x['status'] in {'ready','failed','cancelled'});assert installation['status']=='ready',installation.get('error')
  cap=installation.get('geo_capability_id')
  if not cap:cap=call(c,'POST','/geo/installations/'+installation['id']+'/register-spatial',{'version':installation['version']})['capability_id']
  scenes=call(c,'GET','/geo/scenes')
  if not scenes:
   with FIXTURE.open('rb') as f:r=c.post('/api/geo/scenes',data={'request_id':str(uuid4())},files={'file':('公开照片合成坐标（不是遥感实测）.tif',f,'image/tiff')});r.raise_for_status();scene=r.json()
  else:scene=scenes[0]
  scene=wait(lambda:call(c,'GET','/geo/scenes/'+scene['id']),lambda x:x['status'] in {'parsed','ready','failed','cancelled'});assert scene['status'] in {'parsed','ready'},scene.get('error')
  if scene['status']=='parsed':
   scene=call(c,'PUT','/geo/scenes/'+scene['id']+'/configuration',{'version':scene['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True});scene=wait(lambda:call(c,'GET','/geo/scenes/'+scene['id']),lambda x:x['status'] in {'ready','failed','cancelled'});assert scene['status']=='ready',scene.get('error')
  instances=call(c,'GET','/instances')
  if not instances:
   materials=call(c,'GET','/setup/materials')
   if not materials:
    raw='公开空间候选核查规则\n本地模型读取任务选定的GeoTIFF及固定RGB和分析范围，提取通用区域候选，保留原件和原坐标依据。\n核查负责人对照固定原件、分析图和掩膜保存核查结论；数据管理保留流程证据，不更新对象状态。\n公开照片和合成坐标仅验证工程链，不证明遥感质量或地面精度。'.encode()
    r=c.post('/api/setup/materials',files={'file':('公开空间候选核查规则.txt',raw,'text/plain')});r.raise_for_status();material=r.json()
   else:material=materials[0]
   material=wait(lambda:call(c,'GET','/setup/materials/'+material['id']),lambda x:x['status'] in {'parsed','failed'});assert material['status']=='parsed'
   steps=[]
   for sid,name,agent,role,index in [('segment','提取空间区域候选','多模感知智能体','',1),('record','保存影像核查结论','人工确认','影像核查负责人',2)]:
    quote=material['blocks'][index]['text'];step={'id':sid,'name':name,'purpose':quote,'agent':agent,'role':role,'output':'空间区域候选' if sid=='segment' else '影像核查结论','sourceId':material['id'],'citation':quote,'citations':[{'material_id':material['id'],'block_id':material['blocks'][index]['id'],'quote':quote}],'nextId':'record' if sid=='segment' else 'end','condition':'','elseId':'end'}
    if sid=='segment':step['execution']={'capability_id':cap,'automation_action':'observe','arguments':{},'bindings':{}}
    steps.append(step)
   config={'version':1,'connections':[],'resources':[],'materials':[material],'businesses':['影像候选核查'],'instanceProfile':{'name':'公开空间影像核查实例','domain':'场景资料管理','goal':'通过本地模型与固定空间输入核查候选并保存数据依据','objectCandidates':['观察场景']},'flows':{'影像候选核查':{'trigger':'人工发起','output':'影像核查结论','steps':steps}},'automation':{'version':1,'policies':{'影像候选核查':{'triggers':['人工发起'],'rules':{a:{'mode':'自动运行' if a=='observe' else '仅给出建议'} for a in ['observe','state','data','supplement','write','learn']}}}}}
   flow=config['flows']['影像候选核查'];flow['confirmedSignature']=json.dumps(signature_value(config,flow),ensure_ascii=False);flow['confirmedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat()
   draft=call(c,'GET','/setup/draft');draft=call(c,'PUT','/setup/draft',{'revision':draft['revision'],'configuration':config});member=call(c,'GET','/instances/members')[0]
   compilation={'revision':draft['revision'],'objects':[{'name':'观察场景','fields':[{'label':'名称','kind':'文本','required':True}]}],'role_bindings':{'影像核查负责人':member['id']}}
   check=call(c,'POST','/instances/check',compilation);assert not check['general_issues'] and any(b['available'] for b in check['businesses']),{'general':check['general_issues'],'business_issues':[b['issues'] for b in check['businesses']]}
   release=call(c,'POST','/instances',{**compilation,'confirmed_configuration_hash':check['configuration_hash'],'confirm_configuration':True});instances=call(c,'GET','/instances')
  instance=instances[0];release=call(c,'GET','/instances/'+instance['id']+'/releases')[0]
  if not instance.get('enabled_business_ids',[]):instance=call(c,'POST','/instances/'+instance['id']+'/activate',{'version':instance['version'],'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True})
  objects=call(c,'GET','/instances/'+instance['id']+'/objects')
  if not objects:
   kind=release['snapshot']['object_types'][0];objects=[call(c,'POST','/instances/'+instance['id']+'/objects',{'release_id':release['id'],'object_type':kind['key'],'attributes':{kind['fields'][0]['key']:'公开照片场景（不代表实测地物身份）'}})]
  inputs=call(c,'GET','/workspace/instances/'+instance['id']+'/input-scenes')
  if not inputs:inputs=[call(c,'POST','/workspace/instances/'+instance['id']+'/input-scenes',{'scene_id':scene['id'],'scene_version':scene['version'],'roi':[100,80,740,560],'request_id':str(uuid4())})]
  tasks=call(c,'GET','/workspace/instances/'+instance['id']+'/tasks')
  while len(tasks)<2:
   n=len(tasks);tasks.append(call(c,'POST','/workspace/instances/'+instance['id']+'/tasks',{'release_id':release['id'],'business_id':release['available_business_ids'][0],'entity_ids':[objects[0]['id']],'scene_ids':[inputs[0]['id']],'title':'公开空间候选核查归档' if not n else '公开空间候选核查待续','request_id':str(uuid4())}))
  for brief in tasks:
   task=wait(lambda:call(c,'GET','/workspace/tasks/'+brief['id']),lambda x:x['status'] in {'workflow_waiting','workflow_completed','workflow_failed'});assert task['status'] in {'workflow_waiting','workflow_completed'},task.get('workflow_runtime',{}).get('automatic')
   if task['status']=='workflow_waiting' and not task['workflow_runtime']['drafts']:
    task=call(c,'PUT','/workspace/tasks/'+task['id']+'/note',{'version':task['version'],'step_id':'record','note':'公开照片合成坐标工程核查：候选边界需进一步确认，不认定地物身份或异常；原件和掩膜保留。'})
   if task['title']=='公开空间候选核查归档' and task['status']=='workflow_waiting':call(c,'POST','/workspace/tasks/'+task['id']+'/complete-step',{'version':task['version'],'step_id':'record','note':'核对公开工程候选与固定RGB输入，保留空间证据；不证明遥感准确率或定位精度。','request_id':str(uuid4())})
  save('selection',{'instance_id':instance['id'],'input_scene_id':inputs[0]['id'],'capability_id':cap,'original_sha256':scene['input_sha256'],'fixture':'public COCO resized and tagged with synthetic CRS; not real remote sensing','browser_verified':False,'domain_quality_verified':False})
 print('真实业务配置worker调用本地空间能力完成两任务；一项核查归档，一项说明待续，未使用云模型。')
elif phase in {'snapshot','restart-check'}:
 with connection() as c:
  instance=call(c,'GET','/instances')[0];release=call(c,'GET','/instances/'+instance['id']+'/releases')[0];inputs=call(c,'GET','/workspace/instances/'+instance['id']+'/input-scenes');objects=call(c,'GET','/instances/'+instance['id']+'/objects');tasks=[call(c,'GET','/workspace/tasks/'+t['id']) for t in call(c,'GET','/workspace/instances/'+instance['id']+'/tasks')]
  assert len(tasks)>=2 and any(t['status']=='workflow_completed' for t in tasks) and any(t['status']=='workflow_waiting' and t['workflow_runtime']['drafts'] for t in tasks);assert objects[0]['version']==1
  artifacts={}
  for task in tasks:
   receipt=task['workflow_runtime']['receipts'][0];r=receipt['output']['spatial_segmentation'];run=receipt['output']['geo_run_id'];source=task['scene_snapshots'][0]
   assert r['masks'] and r['cloud_calls']==0 and receipt['candidate_only'] and r['source_raster']['roi']==[100,80,740,560] and r['source_raster']['rgb_bands']==[1,2,3]
   for name,url,sha in [('tiff','/workspace/tasks/'+task['id']+'/scenes/'+source['id']+'/original',source['snapshot']['input_sha256']),('preview','/workspace/tasks/'+task['id']+'/scenes/'+source['id']+'/preview',source['snapshot']['preview']['sha256']),('rgb','/workspace/tasks/'+task['id']+'/spatial/'+run+'/rgb',r['image_sha256'])]+[('mask-'+str(m['candidate_index']),'/workspace/tasks/'+task['id']+'/spatial/'+run+'/masks/'+m['mask_evidence_id'],m['mask_sha256']) for m in r['masks']]:
    response=c.get('/api'+url);response.raise_for_status();assert hashlib.sha256(response.content).hexdigest()==sha;artifacts[task['id']+'/'+name]=sha
   response=c.get('/api/workspace/tasks/'+task['id']+'/spatial/'+run+'/native-geojson');response.raise_for_status();assert response.json()['business_review']['status']==task['status'];artifacts[task['id']+'/export']=hashlib.sha256(response.content).hexdigest()
  with Session(engine()) as db:
   clouds=sorted(db.scalars(select(CloudCall.id)).all());assert clouds==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
   def records(cls):return [{'id':x.id,'version':x.version,'status':x.status,'data':x.data} for x in db.scalars(select(cls).where(cls.workspace_id==access()['workspace_id']).order_by(cls.id))]
   jobs=records(Job);invocations=records(Invocation);memories=records(Memory);assert len(invocations)==len(tasks) and all(x['status']=='succeeded' for x in invocations);assert len(memories)==sum(t['status']=='workflow_completed' for t in tasks) and all(m['data']['scene_snapshots']==inputs for m in memories)
  record={'instance':instance,'release':release,'inputs':inputs,'objects':objects,'tasks':tasks,'jobs':jobs,'invocations':invocations,'memories':memories,'artifact_hashes':artifacts,'cloud_ids':clouds}
  if phase=='snapshot':save('before-restart',record)
  else:
   assert record==json.loads((OUT/'before-restart.json').read_text()),'服务重启后业务记录或空间依据变化';save('after-restart',record);save('runtime',{'records_identical':True,'actual_business_invocations':len(invocations),'completed_review_tasks':sum(t['status']=='workflow_completed' for t in tasks),'pending_review_tasks':sum(t['status']=='workflow_waiting' for t in tasks),'candidate_data_receipts':len(memories),'artifact_hashes_checked':len(artifacts),'cloud_calls_created':0,'entity_state_updated':False,'browser_verified':False,'domain_quality_verified':False,'database_restarted':False,'production_verified':False})
 print('全部任务的固定原件、预览、掩膜、导出、人工说明和候选数据归档均已核对；没有新增云调用。')
else:raise SystemExit('Unknown phase')
