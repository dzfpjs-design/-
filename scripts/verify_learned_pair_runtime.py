"""Real API/worker source upload, learned result, daily receipt and restart check."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import GeoDaily, Memory
from generic_agents.local_changer_install import RECIPE_HASH
from verify_changer_install_runtime import existing

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/learned-pair-v06'
ACCESS=ROOT/'.runtime/changer-install-access.json'

def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')

def main(phase):
 OUT.mkdir(exist_ok=True);access=json.loads(ACCESS.read_text())
 with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=30) as c:
  login=c.post('/login',json={k:access[k] for k in ('username','password')});login.raise_for_status()
  c.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':access['workspace_id']})
  def call(method,path,**kw):
   response=c.request(method,path,**kw);response.raise_for_status();return response.json()
  def wait(path,statuses):
   until=time.monotonic()+50
   while True:
    row=call('GET',path)
    if row['status'] in statuses:return row
    if time.monotonic()>until:raise TimeoutError('same saved job remains live; do not create another request')
    time.sleep(.5)
  try:
   if phase=='prepare':
    if (OUT/'selection.json').exists():raise ValueError('preserve saved source selection')
    if not (OUT/'baseline.json').exists():save('baseline',existing(access['workspace_id']))
    model=next(r for r in call('GET','/specialists/installations') if r['pack_id']=='changer-r18-binary-v1' and r['status']=='ready')
    scenes=[]
    intents=json.loads((OUT/'source-intents.json').read_text()) if (OUT/'source-intents.json').exists() else [{'request_id':str(uuid4())},{'request_id':str(uuid4())}]
    save('source-intents',intents)
    for index,(name,filename) in enumerate([('工程A（合成坐标，非遥感评测）','before.tif'),('工程B（合成坐标，非遥感评测）','after.tif')]):
     raw=(ROOT/'.runtime/changer-pair-validation'/filename).read_bytes()
     intent=intents[index]
     if intent.get('scene_id'):row=call('GET','/geo/scenes/'+intent['scene_id'])
     else:
      response=c.get('/geo/uploads/'+intent['request_id'])
      if response.status_code==404:row=call('POST','/geo/scenes',data={'request_id':intent['request_id']},files={'file':(name+'.tif',raw,'image/tiff')})
      else:response.raise_for_status();row=response.json()
      intent['scene_id']=row['id'];save('source-intents',intents)
     assert row['input_sha256']==hashlib.sha256(raw).hexdigest()
     row=wait('/geo/scenes/'+row['id'],{'parsed','ready','failed'});assert row['status'] in {'parsed','ready'},row.get('error')
     if row['status']=='parsed':row=call('PUT','/geo/scenes/'+row['id']+'/configuration',json={'version':row['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True})
     row=wait('/geo/scenes/'+row['id'],{'ready','failed'});assert row['status']=='ready',row.get('error')
     scenes.append(row)
     save('sources-progress',scenes)
    a,b=scenes;body={'request_id':str(uuid4()),'model_installation_id':model['id'],'model_generation':model['generation'],
     'model_recipe_hash':RECIPE_HASH,'before_scene_id':a['id'],'after_scene_id':b['id'],'before_version':a['version'],'after_version':b['version'],
     'roi':[30,10,600,530],'alignment':'same_grid','probability_threshold':.5,'rgb_comparability_confirmed':True}
    row=call('POST','/geo/learned-pairs',json=body)
    save('selection',{'workspace_id':access['workspace_id'],'body':body,'pair_id':row['id'],'job_id':row['job_id'],'source_sha256':[s['input_sha256'] for s in scenes]})
    print(json.dumps({'queued':True,'pair_id':row['id'],'job_id':row['job_id'],'sources_saved':2}));return
   selection=json.loads((OUT/'selection.json').read_text());path='/geo/pairs/'+selection['pair_id']
   row=wait(path,{'succeeded','failed','cancelled'});assert row['status']=='succeeded',row.get('error')
   artifacts={}
   for kind,saved in row['result']['artifacts'].items():
    response=c.get(path+'/artifacts/'+kind);response.raise_for_status();actual=hashlib.sha256(response.content).hexdigest()
    assert actual==saved['sha256'];artifacts[kind]=actual
   originals=[]
   for source in row['sources']:
    response=c.get('/geo/scenes/'+source['scene_id']+'/original');response.raise_for_status()
    actual=hashlib.sha256(response.content).hexdigest();assert actual==source['sha256'];originals.append(actual)
   export=call('GET',path+'/export');browser=None
   if (OUT/'browser-selection.json').exists():
    ui=json.loads((OUT/'browser-selection.json').read_text());ui_path='/geo/pairs/'+ui['pair_id']
    ui_row=wait(ui_path,{'succeeded','failed','cancelled'});assert ui_row['status']=='succeeded' and ui_row['review']
    ui_artifacts={}
    for kind,saved in ui_row['result']['artifacts'].items():
     result=c.get(ui_path+'/artifacts/'+kind);result.raise_for_status()
     actual=hashlib.sha256(result.content).hexdigest();assert actual==saved['sha256'];ui_artifacts[kind]=actual
    ui_export=call('GET',ui_path+'/export')
    downloads=json.loads((OUT/'browser-downloads.json').read_text());assert downloads['pair_id']==ui['pair_id']
    expected={downloads['files'][0]['name']:ui_artifacts['probability_raster'],downloads['files'][1]['name']:ui_artifacts['change_mask'],downloads['files'][3]['name']:ui_row['sources'][0]['sha256']}
    for item in downloads['files']:
     raw=Path(item['path']).read_bytes()
     if item['name'] in expected:assert hashlib.sha256(raw).hexdigest()==expected[item['name']]
     else:assert json.loads(raw)==ui_export
    browser={'pair':ui_row,'artifact_sha256':ui_artifacts,'export':ui_export,'download_count':len(downloads['files'])}
   daily_rows=call('POST','/imagery-daily/reconcile')['rows']
   daily=next(d for d in daily_rows if any(r['id']==row['id'] and r['kind']=='comparison' for r in d['records']))
   item=next(r for r in daily['records'] if r['id']==row['id'] and r['kind']=='comparison')
   assert item['integrity_verified'] and item['learned_change_model'] and item['not_authoritative'] and not item['entity_state_updated']
   detail=call('GET','/imagery-daily/'+daily['id']+'/records/'+item['key'])
   assert detail['integrity_verified'] and detail['machine']['learned_model'] and len(detail['machine']['artifacts'])==8
   with Session(engine()) as db:
    receipt=db.get(GeoDaily,daily['id']);memory=db.get(Memory,receipt.data['data_memory_id'])
    assert memory.workspace_id==access['workspace_id'] and memory.status=='candidate'
    assert memory.data['receipt_hash']==receipt.data['receipt_hash'] and memory.data['records']==receipt.data['records']
   daily_export=call('GET','/imagery-daily/'+daily['id']+'/export')
   if browser:
    browser_daily=next(r for r in daily['records'] if r['id']==browser['pair']['id'])
    assert browser_daily['integrity_verified'] and browser_daily['learned_change_model'] and browser_daily['review_state']=='confirmed'
   snapshot={'pair':row,'artifact_sha256':artifacts,'original_sha256':originals,'export':export,'daily_export':daily_export,'daily_detail':detail,'browser':browser}
   before=json.loads((OUT/'baseline.json').read_text());current=existing(access['workspace_id'])
   if before!=current:save('unexpected-existing-change',current);raise ValueError('old records or cloud calls changed')
   if phase=='snapshot':save('before-restart',snapshot)
   elif phase=='restart-check':
    assert snapshot==json.loads((OUT/'before-restart.json').read_text()),'saved pair or review changed after restart'
    save('after-restart',snapshot)
    save('verification',{'passed':True,'real_local_learned_job':True,'result_and_review_preserved':True,
      'artifact_sha_verified':len(artifacts),'originals_verified':2,'old_records_unchanged':len(before['records']),
      'old_originals_unchanged':len(before['originals']),'new_cloud_calls':0,'browser_flow_verified':bool(browser),
      'browser_artifact_sha_verified':len(browser['artifact_sha256']) if browser else 0,
      'daily_receipt_and_candidate_memory_verified':True,'business_registration_enabled':False,'domain_quality_verified':False})
   else:save('checked',snapshot)
   print(json.dumps({'passed':True,'phase':phase,'artifact_sha_verified':len(artifacts),'old_records_unchanged':len(before['records']),'new_cloud_calls':0}))
  finally:
   result=c.post('/logout');assert result.status_code in (200,204),'own QA normal logout failed'

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','check','snapshot','restart-check']);main(parser.parse_args().phase)
