"""Existing authorized QA account; public numeric entry is performed in browser.

Only creates an independent business instance and read-only rule registrations.
Never creates users, workspaces, memberships or changes access rights.
"""
import argparse,json,sys
from pathlib import Path
from datetime import datetime
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models
from generic_agents.contracts import digest
from generic_agents.geo_codec import file_digest
from generic_agents.config import settings
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));OUT=ROOT/'docs/evidence/signal-v06';ACCESS=ROOT/'.runtime/wms-access.json'
BACKUP=ROOT/'.runtime/signal-original-draft.json'

def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def read(name):return json.loads((OUT/(name+'.json')).read_text())
def snapshot(workspace,other=False):
 result={}
 with Session(engine()) as db:
  for mapper in models.Base.registry.mappers:
   cls=mapper.class_
   if not hasattr(cls,'workspace_id'):continue
   condition=cls.workspace_id!=workspace if other else cls.workspace_id==workspace
   for row in db.scalars(select(cls).where(condition)):
    if cls is models.Job and row.kind in {'login_limit','cloud_budget'}:continue
    key=cls.__name__+':'+':'.join(str(getattr(row,c.name)) for c in mapper.primary_key)
    result[key]=digest({c.name:getattr(row,c.name).isoformat() if isinstance(getattr(row,c.name),datetime)
       else getattr(row,c.name) for c in cls.__table__.columns})
 return result

def originals(workspace,other=False):
 result={}
 with Session(engine()) as db:
  for row in db.scalars(select(models.Evidence).where(models.Evidence.workspace_id!=workspace if other else models.Evidence.workspace_id==workspace)):
   sha=file_digest(settings().storage_dir/row.storage_key);assert sha==row.sha256
   result[row.id]=sha
 return result

def publish(c,manifest):
 response=c.post('/capabilities',json=manifest);response.raise_for_status();row=response.json()
 for step in ['validate','publish']:
  response=c.post('/capabilities/'+row['id']+'/'+step,json={'expected_version':row['version'],'reason':'公开工程验证的本地只读规则契约，不读取设备或外发数据'})
  response.raise_for_status();row=response.json()
 return row

def prepare(c,a):
 if (OUT/'prepared.json').exists():raise RuntimeError('验证实例已有检查点，请续用，不重复初始化')
 models.PerceptionSignal.__table__.create(engine(),checkfirst=True)
 old=c.get('/setup/draft');old.raise_for_status();old=old.json()
 if not BACKUP.exists():BACKUP.write_text(json.dumps(old));BACKUP.chmod(0o600)
 with Session(engine()) as db:
  cloud=len(db.scalars(select(models.CloudCall.id).where(models.CloudCall.workspace_id==a['workspace_id'])).all())
 if not (OUT/'before.json').exists():save('before',{'other':snapshot(a['workspace_id'],True),'own':snapshot(a['workspace_id']),
    'other_originals':originals(a['workspace_id'],True),'own_originals':originals(a['workspace_id']),
    'cloud':cloud,'draft_id':old['id'],'draft_revision':old['revision'],'configuration_hash':digest(old['configuration'])})
 try:
  from tests.test_instance_workspace import configuration,prepared,enable,object_row
  from generic_agents.instance_compiler import signature_value
  config=configuration();config['instanceProfile'].update({'name':'公开登记异常交接','domain':'工程数据核查（非现场）','goal':'核对工程规则候选并保存人工判断，不更新现场状态'})
  for flow in config['flows'].values():flow['confirmedSignature']=json.dumps(signature_value(config,flow),ensure_ascii=False)
  candidates=[x for x in c.get('/instances').json() if x['name']=='公开登记异常交接' and x['status']=='draft']
  assert len(candidates)<=1,'ambiguous validation instance'
  class ClientPaths:
   def __getattr__(self,name):
    method=getattr(c,name)
    return lambda path,**kw:method(path[4:] if path.startswith('/api/') else path,**kw)
  client=ClientPaths()
  release,_,_=prepared(client,config,instance_id=candidates[0]['id'] if candidates else None);enable(client,release);obj=object_row(client,release)
 finally:
  current=c.get('/setup/draft');current.raise_for_status();current=current.json()
  restored=c.put('/setup/draft',json={'revision':current['revision'],'configuration':old['configuration']});restored.raise_for_status()
  assert restored.json()['configuration']==old['configuration'],'original configuration restore differs'
 capbase={'namespace':'signal_validation','version':'1.0.0','owner':a['username'],'allowed_agent_roles':['perception'],
  'input_schema':{'type':'object'},'output_schema':{'type':'object'},'data_policy':{'cloud_allowed':False}}
 source=publish(c,{**capbase,'kind':'source','name':'public_manual_source','description':'公开人工登记来源（工程验证，非现场设备）','execution':{'adapter':'upload'},'specification':{'timezone':'Asia/Shanghai'}})
 rule=publish(c,{**capbase,'kind':'tool','name':'public_numeric_rule','description':'本地登记阈值核查（公开工程规则）','execution':{'adapter':'builtin','handler':'numeric_rules'},
  'specification':{'rules':[{'id':'reported_high','field':'公开登记值','operator':'gt','threshold':50,'severity':'warning'}]}})
 save('prepared',{'instance_id':release['instance_id'],'release_id':release['id'],'entity_id':obj['id'],'business_id':release['available_business_ids'][0],
  'source_id':source['id'],'extractor_id':rule['id'],'input':65,'threshold':50,'public_engineering_data':True,
  'physical_device':False,'cloud_calls':0,'existing_authorized_account':True,'original_draft_restored':True})
 save('own-prepared',snapshot(a['workspace_id']))
 print(json.dumps({'prepared':True,'existing_account':True,'original_configuration_restored':True,'local_numeric_rule':True,'cloud_calls':0}))

def verify(c,a,phase):
 p=read('prepared');base='/workspace/instances/'+p['instance_id']
 def get(path):
  r=c.get(path);r.raise_for_status();return r.json()
 records=get(base+'/signals')['records'];assert len(records)==1
 signal=records[0];assert not signal['integrity_issue'] and signal['status']=='reviewed'
 assert signal['note']=='已核对公开人工登记65与已批准阈值50；这是工程规则候选，不能证明现场异常。'
 assert signal['basis']['extracted']['anomalies'][0]['value']==65 and signal['basis']['trigger_rules'][0]['threshold']==50
 assert not signal['basis']['source_time_verified'] and signal['business_review']['decision']=='uncertain'
 task=get('/workspace/tasks/'+signal['tasks'][0]['id']);assert task['status']=='workflow_completed' and not task['perception_review']['entity_state_updated']
 observations=get(base+'/reported-observations')['records'];assert len(observations)==1 and observations[0]['status']=='processed' and observations[0]['data_received']
 exported=get(base+'/signals/'+signal['id']+'/export');assert exported==read('browser-export')
 with Session(engine()) as db:
  cloud=len(db.scalars(select(models.CloudCall.id).where(models.CloudCall.workspace_id==a['workspace_id'])).all())
  memory=db.get(models.Memory,task['workflow_runtime']['data_receipt_id']);assert memory.status=='candidate' and not memory.data['auto_publish']
  assert db.get(models.Entity,p['entity_id']).version==1
 before=read('before');other=snapshot(a['workspace_id'],True);raw=originals(a['workspace_id'],True)
 assert other==before['other'] and raw==before['other_originals'],'other workspace records/originals changed'
 own=snapshot(a['workspace_id']);ownraw=originals(a['workspace_id'])
 allowed='SetupDraft:'+before['draft_id'];changed=[k for k,v in before['own'].items() if own.get(k)!=v]
 assert changed==[allowed] or not changed,'old own data changed beyond versioned draft restoration'
 draft=get('/setup/draft');assert digest(draft['configuration'])==before['configuration_hash']
 assert ownraw==before['own_originals'] and cloud==before['cloud']
 for key,value in read('own-prepared').items():assert own.get(key)==value,'prepared configuration/object/registration changed'
 stable={'own':own,'own_originals':ownraw,'export':exported}
 if phase=='check':save('before-restart',stable)
 else:assert stable==read('before-restart'),'restart changed own records/export'
 value={'passed':True,'own_records':len(own),'old_own_unchanged_except_draft_version':len(before['own'])-len(changed),
   'original_configuration_restored':True,'draft_revision_before':before['draft_revision'],'draft_revision_after':draft['revision'],
   'other_records_unchanged':len(other),'other_originals_unchanged':len(raw),'own_originals_unchanged':len(ownraw),
   'signals':1,'local_processing':1,'business_tasks':1,'decision':'uncertain','new_cloud_calls':cloud-before['cloud'],'entity_state_updated':False}
 save('verification' if phase=='check' else 'restart-verification',value);print(json.dumps(value))

def main(mode):
 OUT.mkdir(exist_ok=True);a=json.loads(ACCESS.read_text())
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=60) as c:
  r=c.post('/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']})
  try:prepare(c,a) if mode=='prepare' else verify(c,a,mode)
  finally:c.post('/logout')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','check','restart-check']);main(p.parse_args().phase)
