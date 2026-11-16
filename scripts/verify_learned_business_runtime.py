"""Own synthetic QA workflow; real UI selects the model and creates/reviews tasks."""
import argparse
import hashlib
import json
import time
from datetime import datetime,UTC
from pathlib import Path
from uuid import uuid5,NAMESPACE_URL
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,GeoPair,Memory,Capability,WorkflowScene,Invocation,Job,GeoDaily
from generic_agents.instance_compiler import signature_value
from verify_changer_install_runtime import existing

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/learned-business-v06'
ACCESS=ROOT/'.runtime/changer-install-access.json'
BUSINESS='影像变化候选核查'
ROLE='影像核查负责人'

def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def main(phase):
 OUT.mkdir(exist_ok=True);access=json.loads(ACCESS.read_text())
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
  login=c.post('/login',json={k:access[k] for k in ('username','password')});login.raise_for_status()
  c.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':access['workspace_id']})
  def call(method,path,body=None):
   response=c.request(method,path,**({'json':body} if body is not None else {}));response.raise_for_status();return response.json()
  def get(path):return call('GET',path)
  def wait(path,states):
   deadline=time.monotonic()+60
   while True:
    row=get(path)
    if row['status'] in states:return row
    if time.monotonic()>deadline:raise TimeoutError('same saved job remains live; preserve request and poll it again')
    time.sleep(.3)
  def nonce(label):return str(uuid5(NAMESPACE_URL,access['workspace_id']+':learned-business:'+label))
  try:
   if phase=='prepare':
    if not (OUT/'baseline.json').exists():save('baseline',existing(access['workspace_id']))
    source_selection=json.loads((ROOT/'docs/evidence/learned-pair-v06/selection.json').read_text())
    material=next((m for m in get('/setup/materials') if m['name']=='公开变化模型业务核查规则.txt'),None)
    if not material:
     raw=('公开变化模型业务核查规则\n'
      '本地模型读取任务明确选择的A/B影像及各自允许范围，保存共同有效区域、变化概率与候选，不认定业务异常。\n'
      '影像核查负责人对照两幅原件、未知区域和模型候选，保存人工说明与依据。\n'
      '数据管理保留固定来源、模型版本、分析证据和流程回执；不更新对象状态或自动发布经验。\n'
      '合成坐标工程影像只用于核对实现，不代表真实遥感或林业质量。').encode()
     response=c.post('/setup/materials',files={'file':('公开变化模型业务核查规则.txt',raw,'text/plain')});response.raise_for_status();material=response.json()
     save('material-progress',{'material_id':material['id'],'sha256':hashlib.sha256(raw).hexdigest()})
    material=wait('/setup/materials/'+material['id'],{'parsed','failed'});assert material['status']=='parsed',material.get('error')
    draft=get('/setup/draft');config=draft['configuration']
    if not config.get('businesses'):
     steps=[]
     for sid,name,agent,role,index in [('compare','本地模型识别变化候选','多模感知智能体','',1),('review','保存业务影像核查说明','人工确认',ROLE,2)]:
      block=material['blocks'][index];quote=block['text'];steps.append({'id':sid,'name':name,'purpose':quote,'agent':agent,'role':role,
       'output':'变化概率与候选' if sid=='compare' else '业务核查说明','sourceId':material['id'],'citation':quote,
       'citations':[{'material_id':material['id'],'block_id':block['id'],'quote':quote}],
       'nextId':'review' if sid=='compare' else 'end','condition':'','elseId':'end'})
     config={'version':1,'connections':[],'resources':[],'materials':[material],'businesses':[BUSINESS],
      'instanceProfile':{'name':'公开影像变化候选核查','domain':'影像资料管理','goal':'在本机核对固定A/B、未知区域和候选，保存人工与数据依据','objectCandidates':['影像资料']},
      'flows':{BUSINESS:{'trigger':'人工发起','output':'业务核查说明','steps':steps}},
      'automation':{'version':1,'policies':{BUSINESS:{'triggers':['人工发起'],'rules':{k:{'mode':'自动运行' if k=='observe' else '仅给出建议'} for k in ('observe','state','data','supplement','write','learn')}}}}}
     flow=config['flows'][BUSINESS];flow['confirmedSignature']=json.dumps(signature_value(config,flow),ensure_ascii=False)
     flow['confirmedAt']=datetime.now(UTC).isoformat();call('PUT','/setup/draft',{'revision':draft['revision'],'configuration':config})
    elif config.get('businesses')!=[BUSINESS]:raise ValueError('own QA draft contains other work; preserve it')
    save('sources',{'before_scene_id':source_selection['body']['before_scene_id'],'after_scene_id':source_selection['body']['after_scene_id'],
                    'source_sha256':source_selection['source_sha256']})
    save('prepared-draft',{'material_id':material['id'],'business':BUSINESS,'draft_revision':get('/setup/draft')['revision']})
    print(json.dumps({'public_material_and_draft_saved':True,'business_task_created':False}));return
   if phase=='compile':
    draft=get('/setup/draft');config=draft['configuration'];step=config['flows'][BUSINESS]['steps'][0]
    options=get('/workspace/ability-options');cap=next(x for x in options if x['id']==step.get('execution',{}).get('capability_id'))
    assert cap['adapter']=='local_learned_geo_pair','real UI must select the dedicated learned capability'
    member=next(x for x in get('/instances/members') if x['name']==access['username'])
    body={'revision':draft['revision'],'objects':[{'name':'影像资料','fields':[{'label':'名称','kind':'文本','required':True}]}],'role_bindings':{ROLE:member['id']}}
    instances=get('/instances')
    if not instances:
     check=call('POST','/instances/check',body)
     assert not check['general_issues'] and any(b['available'] for b in check['businesses']),{'general':check['general_issues'],'business':[b['issues'] for b in check['businesses']]}
     release=call('POST','/instances',{**body,'confirm_configuration':True,'confirmed_configuration_hash':check['configuration_hash']})
     instance=get('/instances')[0]
    else:
     assert len(instances)==1;instance=instances[0];release=get('/instances/'+instance['id']+'/releases')[0]
    if instance['status']!='partially_active':instance=call('POST','/instances/'+instance['id']+'/activate',{'version':instance['version'],
        'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True})
    iid=instance['id'];sources=json.loads((OUT/'sources.json').read_text())
    if not get('/instances/'+iid+'/objects'):
     kind=release['snapshot']['object_types'][0]
     call('POST','/instances/'+iid+'/objects',{'release_id':release['id'],'object_type':kind['key'],
          'attributes':{kind['fields'][0]['key']:'合成工程影像资料（不是实测地物身份）'}})
    for label,identity,roi in [('A',sources['before_scene_id'],[30,10,600,530]),('B',sources['after_scene_id'],[70,50,590,510])]:
     scene=get('/geo/scenes/'+identity)
     call('POST','/workspace/instances/'+iid+'/input-scenes',{'request_id':nonce('input-'+label),'scene_id':identity,'scene_version':scene['version'],'roi':roi})
    save('prepared',{'instance_id':iid,'release_id':release['id'],'capability_id':cap['id']})
    print(json.dumps({'real_ui_capability_compiled':True,'two_authorized_inputs_saved':True,'business_task_created':False}));return
   prepared=json.loads((OUT/'prepared.json').read_text());iid=prepared['instance_id']
   tasks=[get('/workspace/tasks/'+t['id']) for t in get('/workspace/instances/'+iid+'/tasks')]
   assert len(tasks)==1,'preserve task requests, do not create another task'
   task=wait('/workspace/tasks/'+tasks[0]['id'],{'workflow_waiting','workflow_completed','workflow_failed','workflow_blocked'})
   assert task['status']=='workflow_completed',task['workflow_runtime'].get('automatic')
   receipt=next(r for r in task['workflow_runtime']['receipts'] if r.get('output',{}).get('spatial_comparison'))
   result=receipt['output']['spatial_comparison'];pair_id=receipt['output']['geo_pair_id']
   assert receipt['candidate_only'] and result['learned_model'] and result['cloud_calls']==0
   assert result['after_read_window']==[70,50,590,510] and result['unknown_pixels']>0 and len(result['artifacts'])==8
   base='/workspace/tasks/'+task['id']+'/comparisons/'+pair_id;artifacts={}
   for kind,saved in result['artifacts'].items():
    response=c.get(base+'/artifacts/'+kind);response.raise_for_status()
    actual=hashlib.sha256(response.content).hexdigest();assert actual==saved['sha256'];artifacts[kind]=actual
   export=get(base+'/export');originals=[]
   for source in task['comparison_scene_snapshots']:
    response=c.get('/geo/scenes/'+source['scene_id']+'/original');response.raise_for_status()
    actual=hashlib.sha256(response.content).hexdigest();assert actual==source['snapshot']['input_sha256'];originals.append(actual)
   assert originals==json.loads((OUT/'sources.json').read_text())['source_sha256']
   browser_downloads=OUT/'browser-downloads.json'
   if browser_downloads.exists():
    downloads=json.loads(browser_downloads.read_text());assert len(downloads)==5
    expected=originals+[artifacts['probability_raster'],artifacts['change_mask']]
    for download,sha in zip(downloads[:4],expected):
     assert hashlib.sha256(Path(download['path']).read_bytes()).hexdigest()==sha,'browser download differs from saved evidence'
    assert json.loads(Path(downloads[4]['path']).read_text())==export,'browser export differs from actual task review'
   elif phase in {'snapshot','restart-check'}:raise ValueError('actual browser downloads required before durability claim')
   daily=call('POST','/imagery-daily/reconcile');collection=next(d for d in daily['rows'] if any(r['id']==pair_id for r in d['records']))
   item=next(r for r in collection['records'] if r['id']==pair_id)
   assert item['learned_change_model'] and item['integrity_verified'] and item['instance_id']==iid and item['review_state']=='confirmed'
   objects=get('/instances/'+iid+'/objects');assert len(objects)==1 and objects[0]['version']==1
   with Session(engine()) as db:
    memory=db.get(Memory,task['workflow_runtime']['data_receipt_id'])
    assert memory.status=='candidate' and memory.data['comparison_scene_snapshots']==task['comparison_scene_snapshots'] and not memory.data['entity_state_updated']
    rows={cls.__name__:[{'id':r.id,'version':r.version,'status':getattr(r,'status',None),'data':r.data}
          for r in db.scalars(select(cls).where(cls.workspace_id==access['workspace_id']).order_by(cls.id))]
          for cls in (Task,GeoPair,WorkflowScene,Invocation,Job,Memory,Capability,GeoDaily)}
   before=json.loads((OUT/'baseline.json').read_text());current=existing(access['workspace_id'])
   if before!=current:save('unexpected-existing-change',current);raise ValueError('old records, raw hashes or cloud call IDs changed')
   snapshot={'task':task,'export':export,'artifact_sha256':artifacts,'original_sha256':originals,'objects':objects,'records':rows,
             'draft':get('/setup/draft'),'daily_export':get('/imagery-daily/'+collection['id']+'/export')}
   if phase=='snapshot':save('before-restart',snapshot)
   elif phase=='restart-check':
    assert snapshot==json.loads((OUT/'before-restart.json').read_text()),'saved business flow or evidence changed after normal restart'
    save('after-restart',snapshot);save('verification',{'passed':True,'real_local_workflow':True,'candidate_only':True,
       'task_review_and_memory_preserved':True,'artifact_sha_verified':8,'originals_verified':2,'browser_downloads_verified':5,
       'old_records_unchanged':len(before['records']),'old_originals_unchanged':len(before['originals']),'new_cloud_calls':0,
       'entity_state_updated':False,'domain_quality_verified':False,'production_verified':False})
   else:save('checked',snapshot)
   print(json.dumps({'passed':True,'phase':phase,'task_completed':True,'artifact_sha_verified':8,'new_cloud_calls':0}))
  finally:
   response=c.post('/logout');assert response.status_code in (200,204),'own QA normal logout failed'

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','compile','check','snapshot','restart-check']);main(parser.parse_args().phase)
